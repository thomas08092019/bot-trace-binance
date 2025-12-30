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

# CRITICAL: Load .env BEFORE importing other modules
# This ensures all modules get the correct config values
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.bootstrap import bootstrap_system, BootstrapError, SingleInstanceLock
from core.exchange import create_exchange, SafeExchange, StaleDataError, ExchangeError
from core.calculator import calculate_safe_quantity, parse_decimal
from core.execution import execute_atomic_entry, SpreadTooWideError
from core.safety import ghost_synchronizer, get_position_summary, display_position_summary
from core.risk_manager import DynamicRiskManager
from core.notifier import Notifier, set_notifier, get_notifier
from strategy.scanner import scan_market, get_default_symbols, fetch_top_symbols
from strategy.manager import PositionManager

console = Console()

# Global flag for graceful shutdown
shutdown_requested = False
lock: Optional[SingleInstanceLock] = None
exchange: Optional[SafeExchange] = None
notifier: Optional[Notifier] = None


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
        'margin_mode': os.getenv('MARGIN_MODE', 'isolated').lower(),
        'stoploss_percent': float(os.getenv('STOPLOSS_PERCENT', '2.0')),
        'max_position_percent': float(os.getenv('MAX_POSITION_PERCENT', '10.0')),
        'max_concurrent_positions': int(os.getenv('MAX_CONCURRENT_POSITIONS', '5')),
        'base_symbol_limit': int(os.getenv('BASE_SYMBOL_LIMIT', '15')),
        'max_symbol_limit': int(os.getenv('MAX_SYMBOL_LIMIT', '50')),
        'testnet': os.getenv('TESTNET', 'false').lower() == 'true',
        'symbol': os.getenv('SYMBOL', 'BTC/USDT'),
        'scan_interval': int(os.getenv('SCAN_INTERVAL', '60')),  # seconds
        'tp_timeout_seconds': int(os.getenv('TP_TIMEOUT_SECONDS', '30')),
        'enable_dynamic_risk': os.getenv('ENABLE_DYNAMIC_RISK', 'false').lower() == 'true',
        # Take Profit settings (0 = disabled)
        'takeprofit_percent': float(os.getenv('TAKEPROFIT_PERCENT', '0')),
        # Trailing Stop settings (0 = disabled)
        'trailing_activation_percent': float(os.getenv('TRAILING_ACTIVATION_PERCENT', '0')),
        'trailing_callback_percent': float(os.getenv('TRAILING_CALLBACK_PERCENT', '0.5')),
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
    Main trading loop with Trailing Stop support.
    
    Args:
        exchange: SafeExchange instance
        config: Configuration dictionary
    """
    global shutdown_requested
    
    risk_percent = Decimal(str(config['risk_percent']))
    leverage = config['leverage']
    margin_mode = config['margin_mode']
    stoploss_percent = Decimal(str(config['stoploss_percent']))
    max_position_percent = Decimal(str(config['max_position_percent']))
    max_concurrent_positions = config['max_concurrent_positions']
    base_symbol_limit = config['base_symbol_limit']
    max_symbol_limit = config['max_symbol_limit']
    takeprofit_percent = Decimal(str(config['takeprofit_percent']))
    trailing_activation = Decimal(str(config['trailing_activation_percent']))
    trailing_callback = Decimal(str(config['trailing_callback_percent']))
    scan_interval = config['scan_interval']
    
    # Calculate per-position margin limit
    per_position_percent = max_position_percent / Decimal(str(max_concurrent_positions))
    
    console.print(f"[cyan]Portfolio Settings:[/cyan]")
    console.print(f"  Max Concurrent Positions: {max_concurrent_positions}")
    console.print(f"  Total Margin Pool: {max_position_percent}%")
    console.print(f"  Per-Position Limit: {per_position_percent}%")
    console.print(f"  Margin Mode: {margin_mode.upper()}")
    console.print(f"  Leverage: {leverage}x")
    console.print(f"  TP Timeout: {config['tp_timeout_seconds']}s (force close if TP reached but not filled)")
    console.print(f"  Dynamic Risk: {'ENABLED' if config['enable_dynamic_risk'] else 'DISABLED'}")
    console.print(f"[cyan]Scanner Settings:[/cyan]")
    console.print(f"  Base Symbols: {base_symbol_limit}")
    console.print(f"  Max Symbols: {max_symbol_limit} (progressive)")
    
    # Initialize Position Manager for trailing stops and TP timeout
    position_manager = None
    if trailing_activation > 0:
        position_manager = PositionManager(
            exchange=exchange,
            trailing_activation_percent=trailing_activation,
            trailing_callback_percent=trailing_callback,
            stoploss_percent=stoploss_percent,
            tp_timeout_seconds=config['tp_timeout_seconds']
        )
        console.print(f"[green]✓ Trailing Stop enabled: Activation={trailing_activation}%, Callback={trailing_callback}%[/green]")
    
    # Initialize Dynamic Risk Manager
    risk_manager = DynamicRiskManager(
        base_leverage=leverage,
        min_leverage=int(os.getenv('MIN_LEVERAGE', '3')),
        max_leverage=int(os.getenv('MAX_LEVERAGE', '20')),
        enabled=config['enable_dynamic_risk']
    )
    
    # Initialize Notification System (Optional)
    global notifier
    notifier = Notifier()
    set_notifier(notifier)
    
    # Send startup notification
    if notifier.is_enabled():
        try:
            await notifier.send_startup(
                balance=float(balance),
                testnet=config.get('testnet', False)
            )
        except Exception as e:
            console.print(f"[yellow]⚠ Failed to send startup notification: {e}[/yellow]")
    
    iteration = 0
    symbols = []  # Will be fetched dynamically
    base_symbol_limit = 15  # Default scan size
    
    while not shutdown_requested:
        iteration += 1
        
        console.print(Panel(
            f"[bold cyan]TRADING LOOP - Iteration {iteration}[/bold cyan]",
            border_style="cyan"
        ))
        
        try:
            # ====== STEP 0: UPDATE SYMBOL WATCHLIST (Every iteration for real-time volume) ======
            console.print("[dim]Updating symbol watchlist...[/dim]")
            symbols = await fetch_top_symbols(exchange, limit=base_symbol_limit)
            if not symbols:
                console.print("[yellow]⚠ No symbols available - using fallback[/yellow]")
                symbols = get_default_symbols()
            
            # ====== STEP 1: GHOST SYNCHRONIZER (SAFETY FIRST) ======
            # Pass None for symbol to check ALL positions/orders
            sync_result = await ghost_synchronizer(exchange, stoploss_percent, None)
            
            if sync_result['errors'] > 0:
                console.print("[yellow]⚠ Safety issues detected - skipping this iteration[/yellow]")
                await asyncio.sleep(10)
                continue
            
            # ====== STEP 2: DISPLAY CURRENT POSITIONS ======
            summaries = await get_position_summary(exchange, None)
            display_position_summary(summaries)
            
            # Extract active symbols to avoid duplicates
            active_symbols = set()
            if summaries:
                for summary in summaries:
                    active_symbols.add(summary['symbol'])
            
            current_position_count = len(active_symbols)
            available_slots = max_concurrent_positions - current_position_count
            
            console.print(f"[cyan]Portfolio Status: {current_position_count}/{max_concurrent_positions} positions[/cyan]")
            
            # ====== STEP 3: PROCESS TRAILING STOPS IF POSITION EXISTS ======
            if summaries and position_manager:
                console.print("\n[bold]Processing Trailing Stops...[/bold]")
                
                # Get positions and orders for trailing stop processing
                positions = await exchange.fetch_positions()
                open_orders = await exchange.fetch_open_orders()
                
                # Process trailing stops
                trailing_result = await position_manager.process_trailing_stops(
                    positions=positions,
                    open_orders=open_orders
                )
                
                # Display tracker status
                position_manager.display_tracker_status()
            
            # ====== STEP 4: CHECK IF WE CAN OPEN NEW POSITIONS ======
            if available_slots <= 0:
                console.print("[dim]Portfolio full - waiting for positions to close...[/dim]")
                await asyncio.sleep(min(scan_interval, 10))
                continue
            
            # ====== STEP 5: SCAN AND ENTER POSITIONS IMMEDIATELY ======
            console.print(f"[cyan]Scanning for signals to fill {available_slots} slot(s)...[/cyan]")
            
            # Progressive scanning: base_symbol_limit → max_symbol_limit
            # BUT enter positions IMMEDIATELY when found (don't wait for full scan)
            step_size = 5
            scan_limits = list(range(base_symbol_limit, max_symbol_limit + 1, step_size))
            if scan_limits[-1] != max_symbol_limit:
                scan_limits.append(max_symbol_limit)
            
            positions_entered = 0
            
            for scan_limit in scan_limits:
                # Stop if we've filled all slots
                if positions_entered >= available_slots:
                    console.print(f"[green]✓ All {available_slots} slots filled - stopping scan[/green]")
                    break
                
                # Fetch symbols for this scan limit
                if len(symbols) < scan_limit:
                    console.print(f"[dim]Expanding scan to top {scan_limit} symbols...[/dim]")
                    symbols = await fetch_top_symbols(exchange, limit=scan_limit)
                
                # Scan for signals
                signals = await scan_market(
                    exchange=exchange,
                    symbols=symbols,
                    stoploss_percent=stoploss_percent,
                    max_signals=available_slots - positions_entered + 5  # Only need remaining slots
                )
                
                if not signals:
                    continue
                
                # Filter out symbols already in portfolio
                filtered_signals = [s for s in signals if s.symbol not in active_symbols]
                
                if not filtered_signals:
                    continue
                
                console.print(f"[green]Found {len(filtered_signals)} signal(s) at {scan_limit} symbols - entering positions NOW[/green]")
                
                # ====== STEP 6: ENTER POSITIONS IMMEDIATELY ======
                for signal in filtered_signals:
                    # Stop if we've filled all slots
                    if positions_entered >= available_slots:
                        break
                    
                    # Calculate take profit price if enabled
                    tp_price = None
                    if takeprofit_percent > 0:
                        if signal.direction == 'LONG':
                            tp_price = signal.entry_price * (Decimal("1") + takeprofit_percent / Decimal("100"))
                        else:
                            tp_price = signal.entry_price * (Decimal("1") - takeprofit_percent / Decimal("100"))
                    
                    tp_info = f"\nTake Profit: {tp_price}" if tp_price else ""
                    trailing_info = f"\nTrailing: Activation={trailing_activation}%, Callback={trailing_callback}%" if trailing_activation > 0 else ""
                    
                    console.print(Panel(
                        f"[bold green]SIGNAL #{positions_entered + 1}[/bold green]\n"
                        f"Symbol: {signal.symbol}\n"
                        f"Direction: {signal.direction}\n"
                        f"Entry: {signal.entry_price}\n"
                        f"Stop Loss: {signal.stoploss_price}{tp_info}{trailing_info}\n"
                        f"Reason: {signal.reason}",
                        title="📈 ENTERING POSITION NOW",
                        border_style="green"
                    ))
                    
                    # Validate entry price (skip if 0 or invalid)
                    if signal.entry_price <= 0:
                        console.print(f"[red]✗ Invalid entry price ({signal.entry_price}) for {signal.symbol} - skipping[/red]")
                        continue
                    
                    # Get balance
                    balance_info = await exchange.fetch_balance()
                    usdt_balance = Decimal(str(balance_info.get('USDT', {}).get('free', 0)))
                    
                    if usdt_balance <= 0:
                        console.print("[red]✗ Insufficient balance - stopping entry[/red]")
                        break
                    
                    # Calculate position size with per-position limit
                    market_info = exchange.get_market_info(signal.symbol)
                    
                    quantity = calculate_safe_quantity(
                        balance=usdt_balance,
                        risk_percent=risk_percent,
                        entry_price=signal.entry_price,
                        stoploss_price=signal.stoploss_price,
                        exchange_info=market_info,
                        symbol=signal.symbol,
                        leverage=leverage,
                        max_position_percent=per_position_percent  # Use divided amount
                    )
                    
                    if quantity == 0:
                        console.print(f"[yellow]⚠ Calculated quantity is 0 for {signal.symbol} - skipping[/yellow]")
                        continue
                    
                    # Execute atomic entry
                    side = 'buy' if signal.direction == 'LONG' else 'sell'
                    
                    try:
                        result = await execute_atomic_entry(
                            exchange=exchange,
                            symbol=signal.symbol,
                            side=side,
                            quantity=quantity,
                            stoploss_price=signal.stoploss_price,
                            takeprofit_price=tp_price,  # Optional TP
                            leverage=leverage,  # Pass leverage to set per-position
                            margin_mode=margin_mode  # Pass margin mode (isolated/cross)
                        )
                        
                        if result['success']:
                            tp_order_info = ""
                            if result.get('take_profit_order'):
                                tp_order_info = f"\nTake Profit: {result['take_profit_order']['id']}"
                            
                            console.print(Panel(
                                f"[bold green]TRADE EXECUTED[/bold green]\n"
                                f"Symbol: {signal.symbol}\n"
                                f"Entry: {result['entry_order']['id']}\n"
                                f"Stop Loss: {result['stop_loss_order']['id']}{tp_order_info}\n"
                                f"Executed: {result['executed_qty']} @ {result['average_price']}",
                                title="✅ SUCCESS",
                                border_style="green"
                            ))
                            
                            # Track successful entry
                            positions_entered += 1
                            active_symbols.add(signal.symbol)
                            
                    except SpreadTooWideError as e:
                        console.print(f"[yellow]⚠ Trade aborted for {signal.symbol}: {e}[/yellow]")
                    except StaleDataError as e:
                        console.print(f"[red]✗ Stale data for {signal.symbol}: {e}[/red]")
                    except ExchangeError as e:
                        console.print(f"[red]✗ Exchange error for {signal.symbol}: {e}[/red]")
                    
                    # Small delay between entries
                    await asyncio.sleep(1)
            
            # If we didn't enter any positions in this iteration, wait before next scan
            if positions_entered == 0:
                console.print("[yellow]⚠ No positions entered - will try again next scan[/yellow]")
            else:
                console.print(f"[green]✓ Entered {positions_entered}/{available_slots} positions this iteration[/green]")
        
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
        console.print(f"[dim]Take Profit: {config['takeprofit_percent']}% {'(enabled)' if config['takeprofit_percent'] > 0 else '(disabled)'}[/dim]")
        console.print(f"[dim]Trailing: Activation={config['trailing_activation_percent']}%, Callback={config['trailing_callback_percent']}% {'(enabled)' if config['trailing_activation_percent'] > 0 else '(disabled)'}[/dim]")
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
            symbol=config['symbol'],
            margin_mode=config['margin_mode']
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
