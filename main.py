"""
main.py - Main Orchestrator

Implements:
- Graceful exit handler (SIGINT, SIGTERM)
- Main trading loop with safety-first approach
- Integration of all modules
"""

import asyncio
import os
import signal
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.live import Live
from rich.table import Table

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.bootstrap import bootstrap_system, BootstrapError, SingleInstanceLock
from core.exchange import create_exchange, SafeExchange, StaleDataError, ExchangeError
from core.calculator import calculate_safe_quantity, parse_decimal
from core.execution import execute_atomic_entry, SpreadTooWideError
from core.safety import ghost_synchronizer, get_position_summary, display_position_summary
from strategy.scanner import scan_market, get_default_symbols

console = Console()

# Global flag for graceful shutdown
shutdown_requested = False
lock: Optional[SingleInstanceLock] = None
exchange: Optional[SafeExchange] = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    
    sig_name = signal.Signals(signum).name
    console.print(f"\n[yellow]⚠ Received {sig_name} - Initiating graceful shutdown...[/yellow]")
    shutdown_requested = True


def setup_signal_handlers():
    """Set up signal handlers for graceful shutdown."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Windows doesn't have SIGHUP
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, signal_handler)


def load_config() -> dict:
    """
    Load configuration from environment variables.
    
    Returns:
        Configuration dictionary
        
    Raises:
        ValueError: If required config is missing
    """
    # Load .env file
    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)
    
    # Required variables
    api_key = os.getenv('API_KEY')
    secret_key = os.getenv('SECRET_KEY')
    
    if not api_key or not secret_key:
        raise ValueError("API_KEY and SECRET_KEY must be set in .env file")
    
    # Optional variables with defaults
    config = {
        'api_key': api_key,
        'secret_key': secret_key,
        'risk_percent': float(os.getenv('RISK_PERCENT', '1.0')),
        'leverage': int(os.getenv('LEVERAGE', '10')),
        'stoploss_percent': float(os.getenv('STOPLOSS_PERCENT', '2.0')),
        'testnet': os.getenv('TESTNET', 'false').lower() == 'true',
        'symbol': os.getenv('SYMBOL', 'BTC/USDT'),
        'scan_interval': int(os.getenv('SCAN_INTERVAL', '60')),  # seconds
    }
    
    return config


async def cleanup():
    """Clean up resources on shutdown."""
    global exchange, lock
    
    console.print("\n[cyan]Cleaning up...[/cyan]")
    
    if exchange:
        try:
            await exchange.disconnect()
        except Exception as e:
            console.print(f"[red]Error disconnecting exchange: {e}[/red]")
    
    if lock:
        lock.release()
    
    console.print("[green]✓ Cleanup complete[/green]")


async def trading_loop(
    exchange: SafeExchange,
    config: dict
):
    """
    Main trading loop.
    
    Args:
        exchange: SafeExchange instance
        config: Configuration dictionary
    """
    global shutdown_requested
    
    risk_percent = Decimal(str(config['risk_percent']))
    leverage = config['leverage']
    stoploss_percent = Decimal(str(config['stoploss_percent']))
    scan_interval = config['scan_interval']
    symbols = get_default_symbols()
    
    iteration = 0
    
    while not shutdown_requested:
        iteration += 1
        
        console.print(Panel(
            f"[bold cyan]TRADING LOOP - Iteration {iteration}[/bold cyan]",
            border_style="cyan"
        ))
        
        try:
            # ====== STEP 1: GHOST SYNCHRONIZER (SAFETY FIRST) ======
            sync_result = await ghost_synchronizer(exchange, stoploss_percent)
            
            if sync_result['errors'] > 0:
                console.print("[yellow]⚠ Safety issues detected - skipping this iteration[/yellow]")
                await asyncio.sleep(10)
                continue
            
            # ====== STEP 2: DISPLAY CURRENT POSITIONS ======
            summaries = await get_position_summary(exchange)
            display_position_summary(summaries)
            
            # ====== STEP 3: CHECK IF WE CAN OPEN NEW POSITIONS ======
            if summaries:
                console.print("[dim]Already have open position(s) - waiting for exit...[/dim]")
                await asyncio.sleep(scan_interval)
                continue
            
            # ====== STEP 4: SCAN FOR SIGNALS ======
            signals = await scan_market(
                exchange=exchange,
                symbols=symbols,
                stoploss_percent=stoploss_percent,
                max_signals=3
            )
            
            if not signals:
                console.print("[dim]No signals found - waiting for next scan...[/dim]")
                await asyncio.sleep(scan_interval)
                continue
            
            # ====== STEP 5: ATTEMPT ENTRY ON BEST SIGNAL ======
            best_signal = signals[0]
            
            console.print(Panel(
                f"[bold green]SIGNAL DETECTED[/bold green]\n"
                f"Symbol: {best_signal.symbol}\n"
                f"Direction: {best_signal.direction}\n"
                f"Entry: {best_signal.entry_price}\n"
                f"Stop Loss: {best_signal.stoploss_price}\n"
                f"Reason: {best_signal.reason}",
                title="📈 TRADE OPPORTUNITY",
                border_style="green"
            ))
            
            # Get balance
            balance_info = await exchange.fetch_balance()
            usdt_balance = Decimal(str(balance_info.get('USDT', {}).get('free', 0)))
            
            if usdt_balance <= 0:
                console.print("[red]✗ Insufficient balance[/red]")
                await asyncio.sleep(scan_interval)
                continue
            
            # Calculate position size
            market_info = exchange.get_market_info(best_signal.symbol)
            
            quantity = calculate_safe_quantity(
                balance=usdt_balance,
                risk_percent=risk_percent,
                entry_price=best_signal.entry_price,
                stoploss_price=best_signal.stoploss_price,
                exchange_info=market_info,
                symbol=best_signal.symbol,
                leverage=leverage
            )
            
            if quantity == 0:
                console.print("[yellow]⚠ Calculated quantity is 0 - skipping trade[/yellow]")
                await asyncio.sleep(scan_interval)
                continue
            
            # Execute atomic entry
            side = 'buy' if best_signal.direction == 'LONG' else 'sell'
            
            try:
                result = await execute_atomic_entry(
                    exchange=exchange,
                    symbol=best_signal.symbol,
                    side=side,
                    quantity=quantity,
                    stoploss_price=best_signal.stoploss_price
                )
                
                if result['success']:
                    console.print(Panel(
                        f"[bold green]TRADE EXECUTED[/bold green]\n"
                        f"Entry: {result['entry_order']['id']}\n"
                        f"Stop Loss: {result['stop_loss_order']['id']}\n"
                        f"Executed: {result['executed_qty']} @ {result['average_price']}",
                        title="✅ SUCCESS",
                        border_style="green"
                    ))
                    
            except SpreadTooWideError as e:
                console.print(f"[yellow]⚠ Trade aborted: {e}[/yellow]")
            except StaleDataError as e:
                console.print(f"[red]✗ Stale data detected: {e}[/red]")
            except ExchangeError as e:
                console.print(f"[red]✗ Exchange error: {e}[/red]")
            
        except Exception as e:
            console.print(f"[red]✗ Unexpected error in trading loop: {e}[/red]")
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
        
        # Wait before next iteration
        if not shutdown_requested:
            console.print(f"\n[dim]Next scan in {scan_interval} seconds...[/dim]")
            
            # Use shorter sleep intervals to respond to shutdown quickly
            for _ in range(scan_interval):
                if shutdown_requested:
                    break
                await asyncio.sleep(1)


async def main():
    """Main entry point."""
    global lock, exchange
    
    console.print(Panel(
        "[bold cyan]GEMINI IMMORTAL TRADING BOT[/bold cyan]\n"
        "[dim]Zero Trust & Fail-Safe Trading System[/dim]",
        title="🚀 STARTUP",
        border_style="cyan"
    ))
    
    try:
        # Load configuration
        console.print("\n[bold]Loading configuration...[/bold]")
        config = load_config()
        
        console.print(f"[dim]Risk: {config['risk_percent']}%[/dim]")
        console.print(f"[dim]Leverage: {config['leverage']}x[/dim]")
        console.print(f"[dim]Stop Loss: {config['stoploss_percent']}%[/dim]")
        console.print(f"[dim]Testnet: {config['testnet']}[/dim]")
        
        # Set up signal handlers
        setup_signal_handlers()
        
        # Create exchange connection
        console.print("\n[bold]Connecting to exchange...[/bold]")
        exchange = create_exchange(
            api_key=config['api_key'],
            secret_key=config['secret_key'],
            testnet=config['testnet']
        )
        await exchange.connect()
        
        # Bootstrap system (safety checks + PID lock)
        lock = await bootstrap_system(
            exchange=exchange.exchange,  # Pass the raw CCXT exchange
            risk_percent=config['risk_percent'],
            leverage=config['leverage'],
            symbol=config['symbol']
        )
        
        # Start trading loop
        await trading_loop(exchange, config)
        
    except BootstrapError as e:
        console.print(Panel(
            f"[bold red]BOOTSTRAP FAILED[/bold red]\n{e}",
            title="🚨 FATAL ERROR",
            border_style="red"
        ))
        sys.exit(1)
        
    except ValueError as e:
        console.print(Panel(
            f"[bold red]CONFIGURATION ERROR[/bold red]\n{e}",
            title="🚨 FATAL ERROR",
            border_style="red"
        ))
        sys.exit(1)
        
    except KeyboardInterrupt:
        console.print("\n[yellow]Keyboard interrupt received[/yellow]")
        
    except Exception as e:
        console.print(Panel(
            f"[bold red]UNEXPECTED ERROR[/bold red]\n{e}",
            title="🚨 FATAL ERROR",
            border_style="red"
        ))
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        
    finally:
        await cleanup()
        
        console.print(Panel(
            "[bold cyan]GEMINI IMMORTAL[/bold cyan]\n"
            "[dim]Shutdown complete. Stay safe.[/dim]",
            title="👋 GOODBYE",
            border_style="cyan"
        ))


if __name__ == '__main__':
    asyncio.run(main())
