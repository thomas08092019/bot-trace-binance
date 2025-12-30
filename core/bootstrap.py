"""
core/bootstrap.py - System Bootstrap & PID Locking

Implements:
- Single Instance enforcement via PID file lock
- Sanity checks for risk parameters
- Time synchronization with Binance
- Force cancel all open orders on startup
- Force margin type to ISOLATED
"""

import os
import sys
import atexit
import asyncio
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

console = Console()

# Lock file path (relative to project root)
LOCK_FILE = Path(__file__).parent.parent / "bot.lock"

# Safety limits (NON-NEGOTIABLE)
MAX_RISK_PERCENT = 5.0
MAX_LEVERAGE = 20


class BootstrapError(Exception):
    """Fatal bootstrap error - bot must not start."""
    pass


class SingleInstanceLock:
    """
    File-based PID lock to ensure only one bot instance runs at a time.
    
    CRITICAL: This is a ZERO-TOLERANCE safety mechanism.
    """
    
    def __init__(self, lock_path: Path = LOCK_FILE):
        self.lock_path = lock_path
        self.locked = False
    
    def _read_pid(self) -> Optional[int]:
        """Read PID from lock file if it exists."""
        try:
            if self.lock_path.exists():
                content = self.lock_path.read_text().strip()
                return int(content) if content else None
        except (ValueError, OSError):
            return None
        return None
    
    def _is_process_running(self, pid: int) -> bool:
        """Check if a process with given PID is running."""
        if sys.platform == "win32":
            # Windows: use ctypes to check process
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process:
                kernel32.CloseHandle(process)
                return True
            return False
        else:
            # Unix: send signal 0 to check
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
    
    def acquire(self) -> None:
        """
        Acquire the lock. CRASH if another instance is running.
        
        Raises:
            BootstrapError: If another instance is detected running.
        """
        existing_pid = self._read_pid()
        
        if existing_pid is not None:
            if self._is_process_running(existing_pid):
                console.print(Panel(
                    f"[bold red]FATAL: Another instance is running (PID: {existing_pid})[/bold red]\n"
                    f"[yellow]Lock file: {self.lock_path}[/yellow]\n\n"
                    f"If you're sure no other instance is running, delete the lock file manually.",
                    title="🚨 INSTANCE CONFLICT 🚨",
                    border_style="red"
                ))
                raise BootstrapError(f"Instance already running with PID {existing_pid}")
            else:
                # Stale lock file - previous instance crashed
                console.print(f"[yellow]⚠ Removing stale lock file (PID {existing_pid} not running)[/yellow]")
                self.lock_path.unlink(missing_ok=True)
        
        # Write our PID
        current_pid = os.getpid()
        self.lock_path.write_text(str(current_pid))
        self.locked = True
        
        # Register cleanup on exit
        atexit.register(self.release)
        
        console.print(f"[green]✓ Lock acquired (PID: {current_pid})[/green]")
    
    def release(self) -> None:
        """Release the lock by removing the lock file."""
        if self.locked:
            try:
                self.lock_path.unlink(missing_ok=True)
                self.locked = False
                console.print("[green]✓ Lock released[/green]")
            except OSError as e:
                console.print(f"[yellow]⚠ Could not release lock: {e}[/yellow]")


def validate_risk_parameters(risk_percent: float, leverage: int) -> None:
    """
    Validate risk parameters against safety limits.
    
    CRITICAL: These limits are NON-NEGOTIABLE.
    
    Raises:
        BootstrapError: If parameters exceed safety limits.
    """
    if risk_percent > MAX_RISK_PERCENT:
        raise BootstrapError(
            f"RISK_PERCENT ({risk_percent}%) exceeds maximum allowed ({MAX_RISK_PERCENT}%)"
        )
    
    if leverage > MAX_LEVERAGE:
        raise BootstrapError(
            f"LEVERAGE ({leverage}x) exceeds maximum allowed ({MAX_LEVERAGE}x)"
        )
    
    if risk_percent <= 0:
        raise BootstrapError("RISK_PERCENT must be positive")
    
    if leverage <= 0:
        raise BootstrapError("LEVERAGE must be positive")
    
    console.print(f"[green]✓ Risk parameters validated (Risk: {risk_percent}%, Leverage: {leverage}x)[/green]")


async def sync_time_with_exchange(exchange) -> None:
    """
    Synchronize local time with exchange server time.
    
    Args:
        exchange: CCXT exchange instance
        
    Raises:
        BootstrapError: If time difference is too large (>5 seconds)
    """
    import time
    
    server_time = await exchange.fetch_time()
    local_time = int(time.time() * 1000)
    
    diff_ms = abs(server_time - local_time)
    diff_seconds = diff_ms / 1000
    
    if diff_seconds > 5:
        raise BootstrapError(
            f"Time sync error: {diff_seconds:.2f}s difference with exchange. "
            "Please sync your system clock."
        )
    
    console.print(f"[green]✓ Time sync OK (diff: {diff_ms}ms)[/green]")


async def force_cancel_all_orders(exchange, symbol: Optional[str] = None) -> int:
    """
    Force cancel all open orders.
    
    Args:
        exchange: CCXT exchange instance
        symbol: Optional symbol to filter (None = all symbols)
        
    Returns:
        Number of orders cancelled
    """
    try:
        if symbol:
            orders = await exchange.fetch_open_orders(symbol)
        else:
            orders = await exchange.fetch_open_orders()
        
        cancelled = 0
        for order in orders:
            try:
                await exchange.cancel_order(order['id'], order['symbol'])
                cancelled += 1
                console.print(f"[yellow]⚠ Cancelled order: {order['id']} ({order['symbol']})[/yellow]")
            except Exception as e:
                console.print(f"[red]✗ Failed to cancel order {order['id']}: {e}[/red]")
        
        if cancelled > 0:
            console.print(f"[yellow]⚠ Force cancelled {cancelled} open order(s)[/yellow]")
        else:
            console.print("[green]✓ No open orders to cancel[/green]")
        
        return cancelled
        
    except Exception as e:
        console.print(f"[red]✗ Error fetching orders: {e}[/red]")
        return 0


async def force_isolated_margin(exchange, symbol: str, leverage: int) -> None:
    """
    Force margin type to ISOLATED and set leverage.
    
    Args:
        exchange: CCXT exchange instance
        symbol: Trading symbol
        leverage: Leverage to set
    """
    try:
        # Set margin type to ISOLATED
        try:
            await exchange.set_margin_mode('isolated', symbol)
            console.print(f"[green]✓ Margin mode set to ISOLATED for {symbol}[/green]")
        except Exception as e:
            # May fail if already set - this is OK
            if 'No need to change margin type' not in str(e):
                console.print(f"[yellow]⚠ Margin mode note: {e}[/yellow]")
        
        # Set leverage
        await exchange.set_leverage(leverage, symbol)
        console.print(f"[green]✓ Leverage set to {leverage}x for {symbol}[/green]")
        
    except Exception as e:
        console.print(f"[red]✗ Error setting margin/leverage: {e}[/red]")
        raise BootstrapError(f"Failed to configure margin/leverage: {e}")


async def bootstrap_system(exchange, risk_percent: float, leverage: int, symbol: str) -> SingleInstanceLock:
    """
    Complete system bootstrap sequence.
    
    This function performs ALL safety checks before the bot can start trading.
    
    Args:
        exchange: CCXT exchange instance
        risk_percent: Risk percentage per trade
        leverage: Trading leverage
        symbol: Trading symbol
        
    Returns:
        SingleInstanceLock: The acquired lock (must be held during operation)
        
    Raises:
        BootstrapError: If any safety check fails
    """
    console.print(Panel(
        "[bold cyan]GEMINI IMMORTAL TRADING BOT[/bold cyan]\n"
        "[dim]Initializing Safety Systems...[/dim]",
        title="🚀 BOOTSTRAP",
        border_style="cyan"
    ))
    
    # Step 1: Acquire instance lock
    console.print("\n[bold]Step 1/5: Instance Lock[/bold]")
    lock = SingleInstanceLock()
    lock.acquire()
    
    # Step 2: Validate risk parameters
    console.print("\n[bold]Step 2/5: Risk Validation[/bold]")
    validate_risk_parameters(risk_percent, leverage)
    
    # Step 3: Time synchronization
    console.print("\n[bold]Step 3/5: Time Sync[/bold]")
    await sync_time_with_exchange(exchange)
    
    # Step 4: Cancel all open orders for the trading symbol
    console.print("\n[bold]Step 4/5: Order Cleanup[/bold]")
    # CHỈ HỦY LỆNH TRÊN CẶP ĐANG TRADE ĐỂ TRÁNH LỖI RATE LIMIT
    await force_cancel_all_orders(exchange, symbol)
    
    # Step 5: Set margin mode and leverage
    console.print("\n[bold]Step 5/5: Margin Configuration[/bold]")
    await force_isolated_margin(exchange, symbol, leverage)
    
    console.print(Panel(
        "[bold green]ALL SYSTEMS GO[/bold green]\n"
        "[dim]Safety checks passed. Bot is ready to trade.[/dim]",
        title="✅ BOOTSTRAP COMPLETE",
        border_style="green"
    ))
    
    return lock


def get_lock_pid() -> Optional[int]:
    """
    Get the PID from the lock file.
    
    Used by panic.py to kill the main process.
    
    Returns:
        PID if lock file exists and is valid, None otherwise
    """
    lock = SingleInstanceLock()
    return lock._read_pid()
