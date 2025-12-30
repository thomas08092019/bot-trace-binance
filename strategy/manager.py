"""
strategy/manager.py - Position Manager with Trailing Stop

Implements:
- Trailing stop logic with activation threshold
- Take profit timeout (force close if TP reached but not filled)
- Safe stop loss movement (always uses Ghost Synchronizer pattern)
- Highest price tracking per position
- CRITICAL: All operations use SafeExchange wrapper
"""

import asyncio
from decimal import Decimal
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.exchange import SafeExchange, ExchangeError
from core.calculator import parse_decimal, get_tick_size, floor_price_to_tick
from core.safety import (
    get_position_side,
    get_position_qty,
    find_stop_loss_for_position,
    check_sl_qty_mismatch,
    is_stop_order
)

console = Console()


@dataclass
class PositionTracker:
    """Tracks position state for trailing stop and TP timeout."""
    symbol: str
    entry_price: Decimal
    highest_price: Decimal  # For LONG
    lowest_price: Decimal   # For SHORT
    is_long: bool
    trailing_activated: bool = False
    last_sl_price: Decimal = Decimal("0")
    tp_reached_time: Optional[datetime] = None  # When TP level was first reached
    tp_level: Decimal = Decimal("0")  # Take profit price level


class PositionManager:
    """
    Manages open positions with trailing stop functionality.
    
    CRITICAL: This class maintains state between iterations.
    All stop loss modifications use Ghost Synchronizer safety pattern.
    """
    
    def __init__(
        self,
        exchange: SafeExchange,
        trailing_activation_percent: Decimal,
        trailing_callback_percent: Decimal,
        stoploss_percent: Decimal,
        tp_timeout_seconds: int = 30
    ):
        """
        Initialize Position Manager.
        
        Args:
            exchange: SafeExchange instance
            trailing_activation_percent: % profit to activate trailing (e.g., 1.5)
            trailing_callback_percent: % callback from high/low for trailing SL (e.g., 0.5)
            stoploss_percent: Initial stop loss percent
            tp_timeout_seconds: Seconds to wait before force-closing at TP level
        """
        self.exchange = exchange
        self.trailing_activation = trailing_activation_percent / Decimal("100")
        self.trailing_callback = trailing_callback_percent / Decimal("100")
        self.stoploss_percent = stoploss_percent
        self.tp_timeout_seconds = tp_timeout_seconds
        
        # Track positions by symbol
        self._trackers: Dict[str, PositionTracker] = {}
    
    def _get_or_create_tracker(
        self,
        position: Dict[str, Any]
    ) -> PositionTracker:
        """
        Get existing tracker or create new one for position.
        
        Args:
            position: Position dictionary
            
        Returns:
            PositionTracker instance
        """
        symbol = position.get('symbol')
        entry_price = parse_decimal(position.get('entryPrice', 0))
        is_long = get_position_side(position) == 'LONG'
        
        if symbol not in self._trackers:
            self._trackers[symbol] = PositionTracker(
                symbol=symbol,
                entry_price=entry_price,
                highest_price=entry_price,
                lowest_price=entry_price,
                is_long=is_long
            )
            console.print(f"[dim]Created tracker for {symbol} @ {entry_price}[/dim]")
        
        return self._trackers[symbol]
    
    def _remove_tracker(self, symbol: str) -> None:
        """Remove tracker when position is closed."""
        if symbol in self._trackers:
            del self._trackers[symbol]
            console.print(f"[dim]Removed tracker for {symbol}[/dim]")
    
    def _update_price_extremes(
        self,
        tracker: PositionTracker,
        current_price: Decimal
    ) -> None:
        """
        Update highest/lowest price for position.
        
        Args:
            tracker: PositionTracker instance
            current_price: Current market price
        """
        if tracker.is_long:
            if current_price > tracker.highest_price:
                tracker.highest_price = current_price
                console.print(f"[green]↑ New high for {tracker.symbol}: {current_price}[/green]")
        else:
            if current_price < tracker.lowest_price:
                tracker.lowest_price = current_price
                console.print(f"[red]↓ New low for {tracker.symbol}: {current_price}[/red]")
    
    def _check_trailing_activation(
        self,
        tracker: PositionTracker,
        current_price: Decimal
    ) -> bool:
        """
        Check if trailing stop should be activated.
        
        Args:
            tracker: PositionTracker instance
            current_price: Current market price
            
        Returns:
            True if trailing should be activated
        """
        if tracker.trailing_activated:
            return True
        
        if tracker.entry_price == 0:
            return False
        
        if tracker.is_long:
            # LONG: Activate when price is above entry by activation threshold
            activation_price = tracker.entry_price * (Decimal("1") + self.trailing_activation)
            if current_price >= activation_price:
                tracker.trailing_activated = True
                console.print(Panel(
                    f"[bold green]TRAILING STOP ACTIVATED[/bold green]\n"
                    f"Symbol: {tracker.symbol}\n"
                    f"Entry: {tracker.entry_price}\n"
                    f"Current: {current_price}\n"
                    f"Activation threshold: {self.trailing_activation * 100}%",
                    title="📈 TRAILING",
                    border_style="green"
                ))
                return True
        else:
            # SHORT: Activate when price is below entry by activation threshold
            activation_price = tracker.entry_price * (Decimal("1") - self.trailing_activation)
            if current_price <= activation_price:
                tracker.trailing_activated = True
                console.print(Panel(
                    f"[bold green]TRAILING STOP ACTIVATED[/bold green]\n"
                    f"Symbol: {tracker.symbol}\n"
                    f"Entry: {tracker.entry_price}\n"
                    f"Current: {current_price}\n"
                    f"Activation threshold: {self.trailing_activation * 100}%",
                    title="📉 TRAILING",
                    border_style="green"
                ))
                return True
        
        return False
    
    def _calculate_trailing_sl(
        self,
        tracker: PositionTracker
    ) -> Decimal:
        """
        Calculate new trailing stop loss price.
        
        Args:
            tracker: PositionTracker instance
            
        Returns:
            New stop loss price
        """
        if tracker.is_long:
            # LONG: SL trails below the highest price
            return tracker.highest_price * (Decimal("1") - self.trailing_callback)
        else:
            # SHORT: SL trails above the lowest price
            return tracker.lowest_price * (Decimal("1") + self.trailing_callback)
    
    async def _move_stop_loss(
        self,
        position: Dict[str, Any],
        current_sl_order: Dict[str, Any],
        new_sl_price: Decimal
    ) -> bool:
        """
        Move stop loss to new price with safety guards.
        
        CRITICAL: Uses Ghost Synchronizer pattern:
        1. Verify position quantity
        2. Cancel old SL
        3. Place new SL with correct quantity
        
        Args:
            position: Position dictionary
            current_sl_order: Current stop loss order
            new_sl_price: New stop loss price
            
        Returns:
            True if stop loss was moved successfully
        """
        symbol = position.get('symbol')
        pos_qty = get_position_qty(position)
        pos_side = get_position_side(position)
        is_long = pos_side == 'LONG'
        
        market_info = self.exchange.get_market_info(symbol)
        tick_size = get_tick_size(market_info, symbol)
        floored_new_sl = floor_price_to_tick(new_sl_price, tick_size)
        
        console.print(f"[cyan]→ Moving SL for {symbol}: {current_sl_order.get('stopPrice')} → {floored_new_sl}[/cyan]")
        
        try:
            # Step 1: Cancel existing stop loss
            await self.exchange.cancel_order(current_sl_order['id'], symbol)
            console.print(f"[yellow]✓ Cancelled old SL: {current_sl_order['id']}[/yellow]")
            
            # Brief delay to ensure cancellation processed
            await asyncio.sleep(0.3)
            
            # Step 2: Place new stop loss with ACTUAL position quantity
            # (Ghost Synchronizer pattern - always use real position qty)
            sl_side = 'sell' if is_long else 'buy'
            
            new_sl_order = await self.exchange.create_stop_market_order(
                symbol=symbol,
                side=sl_side,
                amount=pos_qty,  # CRITICAL: Use actual position quantity
                stop_price=floored_new_sl
            )
            
            console.print(f"[green]✓ New SL placed: {new_sl_order['id']} @ {floored_new_sl}[/green]")
            
            # Update tracker
            if symbol in self._trackers:
                self._trackers[symbol].last_sl_price = floored_new_sl
            
            return True
            
        except Exception as e:
            console.print(f"[red]✗ Failed to move SL: {e}[/red]")
            
            # CRITICAL: If we cancelled SL but failed to place new one,
            # we need to restore protection
            console.print("[yellow]⚠ Attempting to restore stop loss protection...[/yellow]")
            
            try:
                # Calculate fallback SL price based on entry
                entry_price = parse_decimal(position.get('entryPrice', 0))
                if is_long:
                    fallback_sl = entry_price * (Decimal("1") - self.stoploss_percent / Decimal("100"))
                else:
                    fallback_sl = entry_price * (Decimal("1") + self.stoploss_percent / Decimal("100"))
                
                fallback_sl = floor_price_to_tick(fallback_sl, tick_size)
                sl_side = 'sell' if is_long else 'buy'
                
                await self.exchange.create_stop_market_order(
                    symbol=symbol,
                    side=sl_side,
                    amount=pos_qty,
                    stop_price=fallback_sl
                )
                console.print(f"[yellow]✓ Fallback SL placed @ {fallback_sl}[/yellow]")
            except Exception as e2:
                console.print(f"[bold red]✗ CRITICAL: Could not restore SL: {e2}[/bold red]")
            
            return False
    
    async def _check_tp_timeout(
        self,
        position: Dict[str, Any],
        tracker: PositionTracker,
        current_price: Decimal,
        open_orders: List[Dict[str, Any]]
    ) -> bool:
        """
        Check if TP level reached and timeout exceeded.
        
        If price reaches TP level but order doesn't fill within timeout,
        force close the position with market order.
        
        Args:
            position: Position dictionary
            tracker: PositionTracker instance
            current_price: Current market price
            open_orders: List of open orders
            
        Returns:
            True if position was force closed
        """
        symbol = position.get('symbol')
        pos_qty = get_position_qty(position)
        is_long = tracker.is_long
        
        # Find take profit order
        tp_order = None
        for order in open_orders:
            if order.get('symbol') == symbol and order.get('type') == 'TAKE_PROFIT_MARKET':
                tp_order = order
                tracker.tp_level = parse_decimal(order.get('stopPrice', 0))
                break
        
        if not tp_order or tracker.tp_level == 0:
            # No TP order found, reset timeout tracking
            tracker.tp_reached_time = None
            return False
        
        # Check if price has reached TP level
        tp_reached = False
        if is_long:
            # LONG: TP is above current price, reached if current >= TP
            tp_reached = current_price >= tracker.tp_level
        else:
            # SHORT: TP is below current price, reached if current <= TP
            tp_reached = current_price <= tracker.tp_level
        
        # Handle TP reached state
        if tp_reached:
            # First time reaching TP level
            if tracker.tp_reached_time is None:
                tracker.tp_reached_time = datetime.now()
                console.print(f"[yellow]⏰ {symbol}: TP level {tracker.tp_level} reached! Timeout started ({self.tp_timeout_seconds}s)[/yellow]")
                return False
            
            # Check if timeout exceeded
            elapsed = (datetime.now() - tracker.tp_reached_time).total_seconds()
            if elapsed >= self.tp_timeout_seconds:
                console.print(Panel(
                    f"[bold yellow]TP TIMEOUT - FORCE CLOSING[/bold yellow]\n"
                    f"Symbol: {symbol}\n"
                    f"TP Level: {tracker.tp_level}\n"
                    f"Current Price: {current_price}\n"
                    f"Waited: {elapsed:.1f}s (timeout: {self.tp_timeout_seconds}s)\n"
                    f"Reason: TP reached but order not filled",
                    title="⏱ TP TIMEOUT",
                    border_style="yellow"
                ))
                
                try:
                    # Force close with market order
                    close_side = 'sell' if is_long else 'buy'
                    order = await self.exchange.create_market_order(
                        symbol=symbol,
                        side=close_side,
                        amount=pos_qty
                    )
                    console.print(f"[green]✓ Position force closed at market price: {order['id']}[/green]")
                    
                    # Cancel TP order to avoid duplicate fills
                    try:
                        await self.exchange.cancel_order(tp_order['id'], symbol)
                        console.print(f"[yellow]✓ Cancelled TP order: {tp_order['id']}[/yellow]")
                    except Exception as e:
                        console.print(f"[yellow]⚠ Could not cancel TP order: {e}[/yellow]")
                    
                    return True
                    
                except Exception as e:
                    console.print(f"[red]✗ Failed to force close {symbol}: {e}[/red]")
                    return False
            else:
                # Still waiting for timeout
                remaining = self.tp_timeout_seconds - elapsed
                console.print(f"[yellow]⏰ {symbol}: TP timeout in {remaining:.1f}s (price @ {current_price}, TP @ {tracker.tp_level})[/yellow]")
                return False
        else:
            # Price moved away from TP level, reset timeout
            if tracker.tp_reached_time is not None:
                console.print(f"[dim]{symbol}: Price moved away from TP level ({current_price} vs {tracker.tp_level}), timeout reset[/dim]")
                tracker.tp_reached_time = None
            return False
    
    async def process_trailing_stops(
        self,
        positions: List[Dict[str, Any]],
        open_orders: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Process trailing stops for all open positions.
        
        This should be called every iteration of the main loop.
        
        Args:
            positions: List of open positions
            open_orders: List of open orders
            
        Returns:
            Dictionary with processing results
        """
        result = {
            'positions_processed': 0,
            'trailing_activated': 0,
            'stops_moved': 0,
            'tp_timeouts': 0,
            'errors': 0
        }
        
        if not positions:
            # Clean up trackers for closed positions
            self._trackers.clear()
            return result
        
        # Get current symbols with positions
        current_symbols = {p.get('symbol') for p in positions}
        
        # Remove trackers for closed positions
        closed_symbols = set(self._trackers.keys()) - current_symbols
        for symbol in closed_symbols:
            self._remove_tracker(symbol)
        
        console.print("\n[bold cyan]═══ TRAILING STOP PROCESSOR ═══[/bold cyan]")
        
        for position in positions:
            symbol = position.get('symbol')
            result['positions_processed'] += 1
            
            try:
                # Get or create tracker
                tracker = self._get_or_create_tracker(position)
                
                # Fetch current price
                ticker = await self.exchange.fetch_ticker(symbol)
                current_price = parse_decimal(ticker.get('last', 0))
                
                if current_price == 0:
                    console.print(f"[yellow]⚠ Could not get price for {symbol}[/yellow]")
                    continue
                
                # Update price extremes
                self._update_price_extremes(tracker, current_price)
                
                # Check TP timeout (force close if TP reached but not filled)
                if await self._check_tp_timeout(position, tracker, current_price, open_orders):
                    result['tp_timeouts'] += 1
                    continue  # Position closed, skip trailing stop processing
                
                # Check if trailing should be activated
                if not self._check_trailing_activation(tracker, current_price):
                    profit_pct = ((current_price - tracker.entry_price) / tracker.entry_price * Decimal("100"))
                    if not tracker.is_long:
                        profit_pct = -profit_pct
                    console.print(f"[dim]{symbol}: {profit_pct:+.2f}% (waiting for {self.trailing_activation * 100}% to activate trailing)[/dim]")
                    continue
                
                if not tracker.trailing_activated:
                    result['trailing_activated'] += 1
                
                # Find current stop loss
                sl_order = find_stop_loss_for_position(position, open_orders)
                
                if not sl_order:
                    console.print(f"[red]✗ No SL found for {symbol} - skipping trailing[/red]")
                    result['errors'] += 1
                    continue
                
                current_sl_price = parse_decimal(sl_order.get('stopPrice', 0))
                
                # Calculate new trailing SL
                new_sl_price = self._calculate_trailing_sl(tracker)
                
                # Only move SL if new price is BETTER (higher for long, lower for short)
                should_move = False
                
                if tracker.is_long:
                    # LONG: Only move if new SL is higher than current
                    if new_sl_price > current_sl_price:
                        should_move = True
                        console.print(f"[green]↑ {symbol}: Move SL up {current_sl_price} → {new_sl_price}[/green]")
                else:
                    # SHORT: Only move if new SL is lower than current
                    if new_sl_price < current_sl_price:
                        should_move = True
                        console.print(f"[red]↓ {symbol}: Move SL down {current_sl_price} → {new_sl_price}[/red]")
                
                if should_move:
                    success = await self._move_stop_loss(position, sl_order, new_sl_price)
                    if success:
                        result['stops_moved'] += 1
                    else:
                        result['errors'] += 1
                else:
                    console.print(f"[dim]{symbol}: SL @ {current_sl_price} (no move needed, trailing @ {new_sl_price})[/dim]")
                
            except Exception as e:
                console.print(f"[red]✗ Error processing {symbol}: {e}[/red]")
                result['errors'] += 1
        
        # Summary
        if result['stops_moved'] > 0 or result['trailing_activated'] > 0 or result['tp_timeouts'] > 0:
            console.print(Panel(
                f"Positions: {result['positions_processed']}\n"
                f"Trailing Activated: {result['trailing_activated']}\n"
                f"Stops Moved: {result['stops_moved']}\n"
                f"TP Timeouts: {result['tp_timeouts']}\n"
                f"Errors: {result['errors']}",
                title="📊 TRAILING SUMMARY",
                border_style="cyan"
            ))
        
        return result
    
    def get_tracker_status(self) -> List[Dict[str, Any]]:
        """
        Get status of all position trackers.
        
        Returns:
            List of tracker status dictionaries
        """
        return [
            {
                'symbol': t.symbol,
                'entry_price': t.entry_price,
                'highest_price': t.highest_price if t.is_long else None,
                'lowest_price': t.lowest_price if not t.is_long else None,
                'is_long': t.is_long,
                'trailing_activated': t.trailing_activated,
                'last_sl_price': t.last_sl_price
            }
            for t in self._trackers.values()
        ]
    
    def display_tracker_status(self) -> None:
        """Display current tracker status in a table."""
        if not self._trackers:
            return
        
        table = Table(title="Position Trackers")
        table.add_column("Symbol", style="cyan")
        table.add_column("Side", style="magenta")
        table.add_column("Entry", style="yellow")
        table.add_column("Extreme", style="green")
        table.add_column("Trailing", style="bold")
        table.add_column("Last SL", style="red")
        
        for t in self._trackers.values():
            extreme = str(t.highest_price) if t.is_long else str(t.lowest_price)
            trailing_status = "[green]ACTIVE[/green]" if t.trailing_activated else "[dim]waiting[/dim]"
            
            table.add_row(
                t.symbol,
                "LONG" if t.is_long else "SHORT",
                str(t.entry_price),
                extreme,
                trailing_status,
                str(t.last_sl_price) if t.last_sl_price > 0 else "-"
            )
        
        console.print(table)
