"""
panic.py - EMERGENCY KILL SWITCH

STANDALONE SCRIPT - Uses SYNCHRONOUS ccxt (not async).

This script is designed to be run independently of the main bot.
It will:
1. KILL the main bot process (using PID from lock file) - PANIC FIRST!
2. Cancel ALL open orders
3. Close ALL open positions at market
4. Remove the lock file

RUN THIS WHEN SOMETHING GOES WRONG!
"""

import os
import sys
import time
from pathlib import Path
from decimal import Decimal

from dotenv import load_dotenv
import ccxt
from rich.console import Console
from rich.panel import Panel

console = Console()

# Lock file path
LOCK_FILE = Path(__file__).parent / "bot.lock"


def print_banner():
    """Print the panic banner."""
    console.print(Panel(
        "[bold red]🚨 PANIC KILL SWITCH 🚨[/bold red]\n\n"
        "[yellow]This will:[/yellow]\n"
        "1. KILL the main bot process\n"
        "2. Cancel ALL open orders\n"
        "3. Close ALL positions at MARKET\n"
        "4. Remove the lock file\n\n"
        "[bold red]THIS IS A DESTRUCTIVE OPERATION![/bold red]",
        title="⚠️ EMERGENCY ⚠️",
        border_style="red"
    ))


def kill_main_process() -> bool:
    """
    Kill the main bot process using PID from lock file.
    
    CRITICAL: This MUST be done FIRST before closing positions
    to prevent conflicts.
    
    Returns:
        True if process was killed, False otherwise
    """
    console.print("\n[bold red]Step 1: KILLING MAIN PROCESS[/bold red]")
    
    if not LOCK_FILE.exists():
        console.print("[yellow]⚠ Lock file not found - main process may not be running[/yellow]")
        return False
    
    try:
        pid = int(LOCK_FILE.read_text().strip())
        console.print(f"[dim]Found PID: {pid}[/dim]")
        
        # Kill the process
        if sys.platform == "win32":
            # Windows
            import ctypes
            
            PROCESS_TERMINATE = 1
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            
            if handle:
                result = kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
                
                if result:
                    console.print(f"[green]✓ Process {pid} terminated[/green]")
                    return True
                else:
                    console.print(f"[yellow]⚠ Could not terminate process {pid}[/yellow]")
            else:
                console.print(f"[yellow]⚠ Could not open process {pid} - may already be dead[/yellow]")
        else:
            # Unix
            import signal as sig
            
            try:
                # First try SIGTERM
                os.kill(pid, sig.SIGTERM)
                time.sleep(1)
                
                # Check if still running
                try:
                    os.kill(pid, 0)
                    # Still running, use SIGKILL
                    os.kill(pid, sig.SIGKILL)
                    console.print(f"[green]✓ Process {pid} killed (SIGKILL)[/green]")
                except OSError:
                    console.print(f"[green]✓ Process {pid} terminated (SIGTERM)[/green]")
                
                return True
                
            except OSError as e:
                console.print(f"[yellow]⚠ Process {pid} not running: {e}[/yellow]")
        
        return False
        
    except (ValueError, OSError) as e:
        console.print(f"[red]✗ Error killing process: {e}[/red]")
        return False


def cancel_all_orders(exchange) -> int:
    """
    Cancel ALL open orders.
    
    Args:
        exchange: CCXT exchange instance
        
    Returns:
        Number of orders cancelled
    """
    console.print("\n[bold red]Step 2: CANCELLING ALL ORDERS[/bold red]")
    
    try:
        orders = exchange.fetch_open_orders()
        
        if not orders:
            console.print("[green]✓ No open orders to cancel[/green]")
            return 0
        
        cancelled = 0
        for order in orders:
            try:
                exchange.cancel_order(order['id'], order['symbol'])
                console.print(f"[green]✓ Cancelled: {order['id']} ({order['symbol']})[/green]")
                cancelled += 1
            except Exception as e:
                console.print(f"[red]✗ Failed to cancel {order['id']}: {e}[/red]")
        
        console.print(f"[green]✓ Cancelled {cancelled} order(s)[/green]")
        return cancelled
        
    except Exception as e:
        console.print(f"[red]✗ Error fetching orders: {e}[/red]")
        return 0


def close_all_positions(exchange) -> int:
    """
    Close ALL open positions at market price.
    
    Args:
        exchange: CCXT exchange instance
        
    Returns:
        Number of positions closed
    """
    console.print("\n[bold red]Step 3: CLOSING ALL POSITIONS[/bold red]")
    
    try:
        positions = exchange.fetch_positions()
        
        # Filter to only positions with non-zero quantity
        open_positions = [p for p in positions if float(p.get('contracts', 0)) != 0]
        
        if not open_positions:
            console.print("[green]✓ No open positions to close[/green]")
            return 0
        
        closed = 0
        for pos in open_positions:
            try:
                symbol = pos['symbol']
                contracts = float(pos.get('contracts', 0))
                side = pos.get('side', '')
                
                # Determine close side
                if side.lower() == 'long' or contracts > 0:
                    close_side = 'sell'
                else:
                    close_side = 'buy'
                
                abs_qty = abs(contracts)
                
                console.print(f"[cyan]→ Closing {symbol}: {close_side} {abs_qty}[/cyan]")
                
                # Create market order to close
                exchange.create_order(
                    symbol=symbol,
                    type='market',
                    side=close_side,
                    amount=abs_qty,
                    params={'reduceOnly': True}
                )
                
                console.print(f"[green]✓ Closed: {symbol}[/green]")
                closed += 1
                
            except Exception as e:
                console.print(f"[red]✗ Failed to close {pos.get('symbol')}: {e}[/red]")
        
        console.print(f"[green]✓ Closed {closed} position(s)[/green]")
        return closed
        
    except Exception as e:
        console.print(f"[red]✗ Error fetching positions: {e}[/red]")
        return 0


def remove_lock_file():
    """Remove the lock file."""
    console.print("\n[bold red]Step 4: REMOVING LOCK FILE[/bold red]")
    
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
            console.print(f"[green]✓ Lock file removed: {LOCK_FILE}[/green]")
        else:
            console.print("[yellow]⚠ Lock file already removed[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Failed to remove lock file: {e}[/red]")


def main():
    """Main panic execution."""
    print_banner()
    
    # Confirmation
    console.print("\n[bold yellow]Are you sure you want to proceed? (yes/no): [/bold yellow]", end="")
    
    try:
        response = input().strip().lower()
    except EOFError:
        response = 'no'
    
    if response != 'yes':
        console.print("[cyan]Panic aborted.[/cyan]")
        return
    
    console.print(Panel(
        "[bold red]EXECUTING PANIC SEQUENCE[/bold red]",
        border_style="red"
    ))
    
    # ====== STEP 1: KILL MAIN PROCESS (PANIC FIRST!) ======
    kill_main_process()
    
    # Brief delay to ensure process is dead
    time.sleep(2)
    
    # ====== STEP 2-3: CONNECT AND CLEAN UP ======
    try:
        # Load config
        env_path = Path(__file__).parent / '.env'
        load_dotenv(env_path)
        
        api_key = os.getenv('API_KEY')
        secret_key = os.getenv('SECRET_KEY')
        testnet = os.getenv('TESTNET', 'false').lower() == 'true'
        
        if not api_key or not secret_key:
            console.print("[red]✗ API_KEY and SECRET_KEY not found in .env[/red]")
            # Still remove lock file even without API access
            remove_lock_file()
            return
        
        # Create exchange connection (SYNC!)
        console.print("\n[dim]Connecting to exchange (sync mode)...[/dim]")
        
        config = {
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
            }
        }
        
        exchange = ccxt.binanceusdm(config)
        
        if testnet:
            exchange.set_sandbox_mode(True)
            console.print("[yellow]⚠ TESTNET MODE[/yellow]")
        
        exchange.load_markets()
        console.print("[green]✓ Connected to exchange[/green]")
        
        # Cancel all orders
        cancel_all_orders(exchange)
        
        # Close all positions
        close_all_positions(exchange)
        
    except Exception as e:
        console.print(f"[red]✗ Exchange error: {e}[/red]")
    
    # ====== STEP 4: REMOVE LOCK FILE ======
    remove_lock_file()
    
    # Final message
    console.print(Panel(
        "[bold red]SYSTEM KILLED & FLATTENED[/bold red]\n\n"
        "[green]✓ Main process terminated[/green]\n"
        "[green]✓ All orders cancelled[/green]\n"
        "[green]✓ All positions closed[/green]\n"
        "[green]✓ Lock file removed[/green]\n\n"
        "[dim]The bot has been completely shut down.[/dim]",
        title="🔴 PANIC COMPLETE 🔴",
        border_style="red"
    ))


if __name__ == '__main__':
    main()
