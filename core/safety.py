"""
core/safety.py - Ghost Synchronizer & Position Safety

Implements:
- Ghost Scanner: Detect orphan positions without stop loss
- Logic Synchronizer: Verify SL quantity matches position quantity
- Auto-fix any mismatches (human intervention detection)
"""

import asyncio
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from .exchange import SafeExchange, ExchangeError
from .calculator import parse_decimal, get_tick_size, floor_price_to_tick

console = Console()

# Tolerance for quantity mismatch (to account for floating point)
QTY_MISMATCH_TOLERANCE = Decimal("0.00001")


class SafetyError(Exception):
    """Error in safety checks."""
    pass


def is_stop_order(order: Dict[str, Any]) -> bool:
    """
    Check if an order is a stop loss order.
    
    Args:
        order: Order dictionary
        
    Returns:
        True if order is a stop loss type
    """
    order_type = order.get('type', '').upper()
    return order_type in ('STOP_MARKET', 'STOP', 'STOP_LOSS', 'STOP_LOSS_LIMIT')


def get_position_side(position: Dict[str, Any]) -> str:
    """
    Determine if position is LONG or SHORT.
    
    Args:
        position: Position dictionary
        
    Returns:
        'LONG' or 'SHORT'
    """
    contracts = float(position.get('contracts', 0))
    side = position.get('side', '')
    
    if side.lower() == 'long' or contracts > 0:
        return 'LONG'
    return 'SHORT'


def get_position_qty(position: Dict[str, Any]) -> Decimal:
    """
    Get absolute position quantity.
    
    Args:
        position: Position dictionary
        
    Returns:
        Absolute position quantity as Decimal
    """
    contracts = position.get('contracts', 0)
    return abs(parse_decimal(contracts))


def find_stop_loss_for_position(
    position: Dict[str, Any],
    open_orders: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Find the stop loss order for a given position.
    
    Args:
        position: Position dictionary
        open_orders: List of open orders
        
    Returns:
        Stop loss order if found, None otherwise
    """
    symbol = position.get('symbol')
    pos_side = get_position_side(position)
    
    # For a LONG position, SL should be a SELL order
    # For a SHORT position, SL should be a BUY order
    expected_sl_side = 'sell' if pos_side == 'LONG' else 'buy'
    
    for order in open_orders:
        if order.get('symbol') != symbol:
            continue
        
        if not is_stop_order(order):
            continue
        
        order_side = order.get('side', '').lower()
        if order_side == expected_sl_side:
            return order
    
    return None


def check_sl_qty_mismatch(
    position_qty: Decimal,
    sl_qty: Decimal
) -> Tuple[bool, Decimal]:
    """
    Check if stop loss quantity matches position quantity.
    
    Args:
        position_qty: Position quantity
        sl_qty: Stop loss order quantity
        
    Returns:
        Tuple of (is_mismatch, difference)
    """
    diff = abs(position_qty - sl_qty)
    is_mismatch = diff > QTY_MISMATCH_TOLERANCE
    return is_mismatch, diff


async def fix_missing_stop_loss(
    exchange: SafeExchange,
    position: Dict[str, Any],
    stoploss_percent: Decimal
) -> bool:
    """
    Place a missing stop loss for a position.
    
    Args:
        exchange: SafeExchange instance
        position: Position dictionary
        stoploss_percent: Stop loss percentage
        
    Returns:
        True if stop loss was placed successfully
    """
    symbol = position.get('symbol')
    pos_qty = get_position_qty(position)
    pos_side = get_position_side(position)
    entry_price = parse_decimal(position.get('entryPrice', 0))
    
    if entry_price == 0:
        console.print(f"[red]✗ Cannot fix SL: Unknown entry price for {symbol}[/red]")
        return False
    
    # Calculate stop loss price
    multiplier = Decimal("1") - (stoploss_percent / Decimal("100"))
    
    if pos_side == 'LONG':
        sl_price = entry_price * multiplier
        sl_side = 'sell'
    else:
        sl_price = entry_price * (Decimal("2") - multiplier)
        sl_side = 'buy'
    
    # Floor to tick size
    market_info = exchange.get_market_info(symbol)
    tick_size = get_tick_size(market_info, symbol)
    sl_price = floor_price_to_tick(sl_price, tick_size)
    
    console.print(f"[cyan]→ Placing missing stop loss for {symbol}: {sl_side} {pos_qty} @ {sl_price}[/cyan]")
    
    try:
        order = await exchange.create_stop_market_order(
            symbol=symbol,
            side=sl_side,
            amount=pos_qty,
            stop_price=sl_price
        )
        order_id = order.get('id')
        console.print(f"[green]✓ Stop loss created: {order_id}[/green]")
        
        # CRITICAL: Immediate verification - check if order was accepted
        await asyncio.sleep(0.5)  # Brief delay for exchange processing
        
        try:
            fresh_order = await exchange.fetch_order(order_id, symbol)
            status = fresh_order.get('status', '').lower()
            
            if status in ('canceled', 'cancelled', 'expired', 'rejected'):
                console.print(Panel(
                    f"[bold red]STOP LOSS REJECTED BY EXCHANGE[/bold red]\n"
                    f"Order ID: {order_id}\n"
                    f"Status: {status.upper()}\n"
                    f"Symbol: {symbol}\n"
                    f"Side: {sl_side}\n"
                    f"Quantity: {pos_qty}\n"
                    f"Stop Price: {sl_price}\n\n"
                    f"[yellow]Raw Exchange Response:[/yellow]\n"
                    f"{fresh_order.get('info', 'N/A')}",
                    title="🚨 SL ORDER FAILED",
                    border_style="red"
                ))
                return False
            elif status == 'open':
                console.print(f"[green]✓ Stop loss verified: {order_id} (status: {status})[/green]")
                return True
            else:
                console.print(f"[yellow]⚠ Stop loss status uncertain: {status}[/yellow]")
                return True  # Assume OK if not explicitly failed
                
        except Exception as verify_error:
            console.print(f"[yellow]⚠ Could not verify SL order (may still be OK): {verify_error}[/yellow]")
            return True  # Order was placed, verification failed (not critical)
            
    except Exception as e:
        console.print(f"[red]✗ Failed to place stop loss: {e}[/red]")
        return False


async def fix_qty_mismatch(
    exchange: SafeExchange,
    position: Dict[str, Any],
    sl_order: Dict[str, Any],
    stoploss_percent: Decimal
) -> bool:
    """
    Fix stop loss quantity mismatch.
    
    Process:
    1. Cancel existing stop loss
    2. Place new stop loss with correct quantity
    
    Args:
        exchange: SafeExchange instance
        position: Position dictionary
        sl_order: Existing stop loss order
        stoploss_percent: Stop loss percentage
        
    Returns:
        True if mismatch was fixed
    """
    symbol = position.get('symbol')
    pos_qty = get_position_qty(position)
    pos_side = get_position_side(position)
    sl_qty = parse_decimal(sl_order.get('amount', 0))
    
    console.print(Panel(
        f"[bold yellow]QUANTITY MISMATCH DETECTED[/bold yellow]\n"
        f"Symbol: {symbol}\n"
        f"Position Qty: {pos_qty}\n"
        f"Stop Loss Qty: {sl_qty}\n"
        f"Difference: {abs(pos_qty - sl_qty)}",
        title="⚠ GHOST SYNC",
        border_style="yellow"
    ))
    
    try:
        # Step 1: Cancel existing stop loss
        console.print(f"[yellow]→ Cancelling incorrect stop loss: {sl_order['id']}[/yellow]")
        await exchange.cancel_order(sl_order['id'], symbol)
        
        # Brief delay to ensure cancellation is processed
        await asyncio.sleep(0.3)
        
        # Step 2: Place correct stop loss
        return await fix_missing_stop_loss(exchange, position, stoploss_percent)
        
    except Exception as e:
        console.print(f"[red]✗ Failed to fix mismatch: {e}[/red]")
        return False


async def ghost_synchronizer(
    exchange: SafeExchange,
    stoploss_percent: Decimal = Decimal("2.0"),
    symbol: Optional[str] = None
) -> Dict[str, Any]:
    """
    Ghost Synchronizer - Main safety routine.
    
    This routine:
    1. Fetches all open positions
    2. Fetches all open orders
    3. For each position:
       - Case 1: No stop loss -> Place one
       - Case 2: Stop loss qty != Position qty -> Fix it
       - Case 3: All good -> Log and continue
    
    CRITICAL: This must run at the START of each trading loop iteration.
    
    Args:
        exchange: SafeExchange instance
        stoploss_percent: Stop loss percentage for new SLs
        symbol: Optional symbol to filter orders (recommended to avoid rate limits)
        
    Returns:
        Dictionary with sync results
    """
    result = {
        'positions_checked': 0,
        'missing_sl_fixed': 0,
        'qty_mismatch_fixed': 0,
        'errors': 0,
        'all_synced': False
    }
    
    console.print("\n[bold cyan]═══ GHOST SYNCHRONIZER ═══[/bold cyan]")
    
    try:
        # Fetch all positions first
        positions = await exchange.fetch_positions()
        
        if not positions:
            console.print("[green]✓ No open positions - nothing to sync[/green]")
            result['all_synced'] = True
            return result
        
        # If symbol is specified, filter positions to that symbol only
        if symbol:
            positions = [p for p in positions if p.get('symbol') == symbol]
            if not positions:
                console.print(f"[green]✓ No positions for {symbol}[/green]")
                result['all_synced'] = True
                return result
        
        # Fetch open orders - use symbol filter if available to reduce rate limit impact
        open_orders = await exchange.fetch_open_orders(symbol)
        
        console.print(f"[dim]Found {len(positions)} position(s), {len(open_orders)} open order(s)[/dim]")
        
        # Create a summary table
        table = Table(title="Position Safety Status")
        table.add_column("Symbol", style="cyan")
        table.add_column("Side", style="magenta")
        table.add_column("Qty", style="green")
        table.add_column("SL Status", style="yellow")
        table.add_column("Action", style="red")
        
        all_synced = True
        
        for position in positions:
            symbol = position.get('symbol')
            pos_qty = get_position_qty(position)
            pos_side = get_position_side(position)
            
            result['positions_checked'] += 1
            
            # Find corresponding stop loss
            sl_order = find_stop_loss_for_position(position, open_orders)
            
            if sl_order is None:
                # CASE 1: Missing stop loss
                table.add_row(
                    symbol,
                    pos_side,
                    str(pos_qty),
                    "[red]MISSING[/red]",
                    "Creating SL"
                )
                
                success = await fix_missing_stop_loss(exchange, position, stoploss_percent)
                if success:
                    result['missing_sl_fixed'] += 1
                else:
                    result['errors'] += 1
                    all_synced = False
                    console.print(f"[yellow]⚠ SL placement failed for {symbol} - will retry next cycle[/yellow]")
                    
            else:
                # Check quantity match
                sl_qty = parse_decimal(sl_order.get('amount', 0))
                is_mismatch, diff = check_sl_qty_mismatch(pos_qty, sl_qty)
                
                if is_mismatch:
                    # CASE 2: Quantity mismatch
                    table.add_row(
                        symbol,
                        pos_side,
                        str(pos_qty),
                        f"[yellow]MISMATCH ({sl_qty})[/yellow]",
                        "Fixing Qty"
                    )
                    
                    success = await fix_qty_mismatch(
                        exchange, position, sl_order, stoploss_percent
                    )
                    if success:
                        result['qty_mismatch_fixed'] += 1
                    else:
                        result['errors'] += 1
                        all_synced = False
                else:
                    # CASE 3: All good
                    table.add_row(
                        symbol,
                        pos_side,
                        str(pos_qty),
                        "[green]OK[/green]",
                        "-"
                    )
        
        console.print(table)
        
        result['all_synced'] = all_synced and result['errors'] == 0
        
        if result['all_synced']:
            console.print("[green]✓ All positions synchronized[/green]")
        else:
            console.print(f"[yellow]⚠ Sync completed with {result['errors']} error(s)[/yellow]")
        
        return result
        
    except ExchangeError as e:
        console.print(f"[red]✗ Ghost sync error: {e}[/red]")
        result['errors'] += 1
        return result


async def verify_position_safety(exchange: SafeExchange, symbol: Optional[str] = None) -> bool:
    """
    Verify that all positions have stop losses.
    
    Args:
        exchange: SafeExchange instance
        symbol: Optional symbol to filter orders (recommended to avoid rate limits)
        
    Returns:
        True if all positions have stop losses
    """
    try:
        positions = await exchange.fetch_positions()
        
        if not positions:
            return True
        
        open_orders = await exchange.fetch_open_orders(symbol)
        
        for position in positions:
            sl_order = find_stop_loss_for_position(position, open_orders)
            if sl_order is None:
                console.print(f"[red]✗ Orphan position detected: {position.get('symbol')}[/red]")
                return False
        
        return True
        
    except Exception as e:
        console.print(f"[red]✗ Verification error: {e}[/red]")
        return False


async def get_position_summary(exchange: SafeExchange, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Get a summary of all positions with their protection status.
    
    Args:
        exchange: SafeExchange instance
        symbol: Optional symbol to filter orders (recommended to avoid rate limits)
        
    Returns:
        List of position summaries
    """
    summaries = []
    
    try:
        positions = await exchange.fetch_positions()
        open_orders = await exchange.fetch_open_orders(symbol)
        
        for position in positions:
            symbol = position.get('symbol')
            pos_qty = get_position_qty(position)
            pos_side = get_position_side(position)
            entry_price = parse_decimal(position.get('entryPrice', 0))
            unrealized_pnl = parse_decimal(position.get('unrealizedPnl', 0))
            
            sl_order = find_stop_loss_for_position(position, open_orders)
            
            summary = {
                'symbol': symbol,
                'side': pos_side,
                'quantity': pos_qty,
                'entry_price': entry_price,
                'unrealized_pnl': unrealized_pnl,
                'has_stop_loss': sl_order is not None,
                'stop_loss_price': parse_decimal(sl_order.get('stopPrice', 0)) if sl_order else None,
                'protected': sl_order is not None
            }
            
            if sl_order:
                sl_qty = parse_decimal(sl_order.get('amount', 0))
                is_mismatch, _ = check_sl_qty_mismatch(pos_qty, sl_qty)
                summary['sl_qty_match'] = not is_mismatch
                summary['protected'] = not is_mismatch
            
            summaries.append(summary)
        
        return summaries
        
    except Exception as e:
        console.print(f"[red]✗ Summary error: {e}[/red]")
        return []


def display_position_summary(summaries: List[Dict[str, Any]]) -> None:
    """
    Display position summaries in a formatted table.
    
    Args:
        summaries: List of position summaries
    """
    if not summaries:
        console.print("[dim]No open positions[/dim]")
        return
    
    table = Table(title="Position Summary")
    table.add_column("Symbol", style="cyan")
    table.add_column("Side", style="magenta")
    table.add_column("Qty", style="green")
    table.add_column("Entry", style="yellow")
    table.add_column("PnL", style="white")
    table.add_column("SL Price", style="red")
    table.add_column("Status", style="bold")
    
    for s in summaries:
        pnl_color = "green" if s['unrealized_pnl'] >= 0 else "red"
        pnl_str = f"[{pnl_color}]{s['unrealized_pnl']:.2f}[/{pnl_color}]"
        
        sl_str = str(s['stop_loss_price']) if s['stop_loss_price'] else "[red]NONE[/red]"
        
        if s['protected']:
            status = "[green]PROTECTED[/green]"
        elif s['has_stop_loss']:
            status = "[yellow]QTY MISMATCH[/yellow]"
        else:
            status = "[red]UNPROTECTED[/red]"
        
        table.add_row(
            s['symbol'],
            s['side'],
            str(s['quantity']),
            str(s['entry_price']),
            pnl_str,
            sl_str,
            status
        )
    
    console.print(table)
