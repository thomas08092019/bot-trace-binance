"""
core/execution.py - Atomic Order Execution

Implements:
- Spread Guard (abort if spread > 0.1%)
- Atomic entry + stop loss sequence
- Optional Take Profit order placement
- Stop loss based on ACTUAL executedQty (not requested qty)
- Emergency market close on failure
"""

import os
import asyncio
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple

from rich.console import Console
from rich.panel import Panel

from .exchange import SafeExchange, StaleDataError, ExchangeError
from .calculator import (
    parse_decimal,
    floor_to_step,
    floor_price_to_tick,
    get_step_size,
    get_tick_size,
    validate_min_notional
)

console = Console()

# Load config from environment (with defaults)
MAX_SPREAD_RATIO = Decimal(os.getenv('MAX_SPREAD_RATIO', '0.001'))

# Maximum retries for stop loss placement
MAX_SL_RETRIES = 5

# Maximum retries for take profit placement
MAX_TP_RETRIES = 3


class ExecutionError(Exception):
    """Error during order execution."""
    pass


class SpreadTooWideError(ExecutionError):
    """Spread exceeds maximum allowed threshold."""
    pass


async def check_spread(
    exchange: SafeExchange,
    symbol: str
) -> Tuple[Decimal, Decimal, Decimal]:
    """
    Check if spread is within acceptable limits.
    
    Args:
        exchange: SafeExchange instance
        symbol: Trading symbol
        
    Returns:
        Tuple of (bid, ask, spread_ratio)
        
    Raises:
        SpreadTooWideError: If spread exceeds MAX_SPREAD_RATIO
        StaleDataError: If ticker data is stale
    """
    ticker = await exchange.fetch_ticker(symbol)
    
    bid_raw = ticker.get('bid')
    ask_raw = ticker.get('ask')
    last_raw = ticker.get('last')
    
    if bid_raw is None or ask_raw is None:
        # Testnet fallback: use last price when bid/ask unavailable
        if last_raw is None:
            raise ExecutionError("No price data available")
        console.print(f"[yellow]⚠ Bid/Ask unavailable (testnet), using last price[/yellow]")
        bid = parse_decimal(last_raw)
        ask = parse_decimal(last_raw)
        spread_ratio = Decimal("0")
        console.print(f"[green]✓ Spread check skipped (testnet mode)[/green]")
        return bid, ask, spread_ratio
    
    bid = parse_decimal(bid_raw)
    ask = parse_decimal(ask_raw)
    
    if bid <= 0 or ask <= 0:
        raise ExecutionError(f"Invalid bid/ask: {bid}/{ask}")
    
    spread_ratio = (ask - bid) / ask
    spread_percent = spread_ratio * Decimal("100")
    
    console.print(f"[dim]Spread check: bid={bid}, ask={ask}, spread={spread_percent:.4f}%[/dim]")
    
    if spread_ratio > MAX_SPREAD_RATIO:
        raise SpreadTooWideError(
            f"Spread {spread_percent:.4f}% exceeds maximum {MAX_SPREAD_RATIO * 100}%"
        )
    
    console.print(f"[green]✓ Spread OK: {spread_percent:.4f}%[/green]")
    return bid, ask, spread_ratio

async def emergency_close_position(
    exchange: SafeExchange,
    symbol: str,
    amount: Decimal,
    is_long: bool
) -> bool:
    """
    Emergency close a position immediately.
    
    CRITICAL: This is the fail-safe - it MUST succeed.
    
    Args:
        exchange: SafeExchange instance
        symbol: Trading symbol
        amount: Position amount
        is_long: True if long position
        
    Returns:
        True if closed successfully
    """
    console.print(Panel(
        f"[bold red]EMERGENCY CLOSE: {symbol}[/bold red]\n"
        f"Amount: {amount}, Side: {'LONG' if is_long else 'SHORT'}",
        title="🚨 EMERGENCY 🚨",
        border_style="red"
    ))
    
    close_side = 'sell' if is_long else 'buy'
    
    for attempt in range(MAX_SL_RETRIES):
        try:
            await exchange.close_position(symbol, amount, close_side)
            console.print(f"[green]✓ Emergency close successful[/green]")
            return True
        except Exception as e:
            console.print(f"[red]✗ Emergency close attempt {attempt + 1} failed: {e}[/red]")
            await asyncio.sleep(1)
    
    console.print(f"[bold red]✗ EMERGENCY CLOSE FAILED - MANUAL INTERVENTION REQUIRED[/bold red]")
    return False


async def place_stop_loss(
    exchange: SafeExchange,
    symbol: str,
    executed_qty: Decimal,
    stop_price: Decimal,
    is_long: bool,
    market_info: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Place stop loss order for a position.
    
    CRITICAL: This uses the ACTUAL executed quantity, not requested.
    
    Args:
        exchange: SafeExchange instance
        symbol: Trading symbol
        executed_qty: ACTUAL executed quantity from entry order
        stop_price: Stop loss trigger price
        is_long: True if long position
        market_info: Market information
        
    Returns:
        Stop loss order result, or None if failed
    """
    # Stop loss side is opposite of position
    sl_side = 'sell' if is_long else 'buy'
    
    # Floor the stop price to tick size
    tick_size = get_tick_size(market_info, symbol)
    floored_stop_price = floor_price_to_tick(stop_price, tick_size)
    
    console.print(f"[cyan]→ Placing stop loss: {sl_side} {executed_qty} @ {floored_stop_price}[/cyan]")
    
    for attempt in range(MAX_SL_RETRIES):
        try:
            sl_order = await exchange.create_stop_market_order(
                symbol=symbol,
                side=sl_side,
                amount=executed_qty,
                stop_price=floored_stop_price
            )
            console.print(f"[green]✓ Stop loss placed: {sl_order['id']}[/green]")
            return sl_order
        except Exception as e:
            console.print(f"[red]✗ Stop loss attempt {attempt + 1} failed: {e}[/red]")
            await asyncio.sleep(0.5)
    
    return None


async def place_take_profit(
    exchange: SafeExchange,
    symbol: str,
    executed_qty: Decimal,
    take_profit_price: Decimal,
    is_long: bool,
    market_info: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Place take profit limit order for a position.
    
    CRITICAL: This uses the ACTUAL executed quantity, not requested.
    
    Args:
        exchange: SafeExchange instance
        symbol: Trading symbol
        executed_qty: ACTUAL executed quantity from entry order
        take_profit_price: Take profit limit price
        is_long: True if long position
        market_info: Market information
        
    Returns:
        Take profit order result, or None if failed
    """
    # Take profit side is opposite of position (same as SL)
    tp_side = 'sell' if is_long else 'buy'
    
    # Floor the take profit price to tick size
    tick_size = get_tick_size(market_info, symbol)
    floored_tp_price = floor_price_to_tick(take_profit_price, tick_size)
    
    console.print(f"[cyan]→ Placing take profit: {tp_side} {executed_qty} @ {floored_tp_price}[/cyan]")
    
    for attempt in range(MAX_TP_RETRIES):
        try:
            # Use TAKE_PROFIT_MARKET order type for futures
            tp_order = await exchange.create_take_profit_order(
                symbol=symbol,
                side=tp_side,
                amount=executed_qty,
                stop_price=floored_tp_price
            )
            console.print(f"[green]✓ Take profit placed: {tp_order['id']}[/green]")
            return tp_order
        except Exception as e:
            console.print(f"[yellow]⚠ Take profit attempt {attempt + 1} failed: {e}[/yellow]")
            await asyncio.sleep(0.5)
    
    # Take profit failure is NOT critical - position still protected by SL
    console.print("[yellow]⚠ Could not place take profit - position still protected by stop loss[/yellow]")
    return None


async def execute_atomic_entry(
    exchange: SafeExchange,
    symbol: str,
    side: str,
    quantity: Decimal,
    stoploss_price: Decimal,
    takeprofit_price: Optional[Decimal] = None,
    leverage: int = 10
) -> Dict[str, Any]:
    """
    Execute atomic entry sequence: Market Order + Stop Loss + Optional Take Profit.
    
    ATOMIC SEQUENCE:
    1. Set leverage and margin mode (ISOLATED)
    2. Check spread (abort if > 0.1%)
    3. Place market entry order
    4. Verify executed quantity and average price
    5. Place stop loss based on ACTUAL executed qty
    6. If stop loss fails -> Emergency close position
    7. (Optional) Place take profit order
    
    Args:
        exchange: SafeExchange instance
        symbol: Trading symbol
        side: 'buy' (long) or 'sell' (short)
        quantity: Quantity to trade
        stoploss_price: Stop loss trigger price
        takeprofit_price: Optional take profit price (None to disable)
        leverage: Leverage to use (default 10)
        
    Returns:
        Dictionary with entry and stop loss order details
        
    Raises:
        ExecutionError: If atomic sequence fails
        SpreadTooWideError: If spread is too wide
        StaleDataError: If data is stale
    """
    result = {
        'symbol': symbol,
        'side': side,
        'entry_order': None,
        'stop_loss_order': None,
        'take_profit_order': None,
        'executed_qty': Decimal("0"),
        'average_price': Decimal("0"),
        'success': False
    }
    
    is_long = side.lower() == 'buy'
    market_info = exchange.get_market_info(symbol)
    
    tp_info = f"\nTake Profit: {takeprofit_price}" if takeprofit_price else ""
    
    console.print(Panel(
        f"[bold cyan]ATOMIC ENTRY SEQUENCE[/bold cyan]\n"
        f"Symbol: {symbol}\n"
        f"Side: {side.upper()}\n"
        f"Quantity: {quantity}\n"
        f"Stop Loss: {stoploss_price}{tp_info}",
        title="⚡ EXECUTION",
        border_style="cyan"
    ))
    
    # ====== STEP 0: SET LEVERAGE AND MARGIN MODE ======
    console.print("\n[bold]Step 0/5: Margin Configuration[/bold]")
    try:
        # Set margin mode to ISOLATED
        try:
            await exchange.set_margin_mode('isolated', symbol)
            console.print(f"[green]✓ Margin mode set to ISOLATED for {symbol}[/green]")
        except Exception as e:
            # Margin mode may already be set, which is fine
            console.print(f"[dim]⚠ Margin mode note: {e}[/dim]")
        
        # Set leverage
        await exchange.set_leverage(leverage, symbol)
        console.print(f"[green]✓ Leverage set to {leverage}x for {symbol}[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠ Could not configure margin/leverage: {e}[/yellow]")
        # Continue anyway - may already be configured
    
    # ====== STEP 1: CHECK SPREAD ======
    console.print("\n[bold]Step 1/5: Spread Check[/bold]")
    try:
        await check_spread(exchange, symbol)
    except SpreadTooWideError as e:
        console.print(f"[red]✗ ABORT: {e}[/red]")
        raise
    
    # ====== STEP 2: MARKET ENTRY ======
    console.print("\n[bold]Step 2/5: Market Entry[/bold]")
    try:
        entry_order = await exchange.create_market_order(
            symbol=symbol,
            side=side,
            amount=quantity
        )
        result['entry_order'] = entry_order
    except Exception as e:
        console.print(f"[red]✗ Entry order failed: {e}[/red]")
        raise ExecutionError(f"Entry order failed: {e}")
    
    # ====== STEP 3: VERIFY EXECUTION ======
    console.print("\n[bold]Step 3/5: Verify Execution[/bold]")
    
    # Get the actual executed quantity and average price
    executed_qty = parse_decimal(entry_order.get('filled', 0))
    average_price = parse_decimal(entry_order.get('average', 0))
    
    # If not in immediate response, fetch the order
    if executed_qty == 0:
        await asyncio.sleep(0.5)  # Brief delay for order to settle
        fetched_order = await exchange.fetch_order(entry_order['id'], symbol)
        executed_qty = parse_decimal(fetched_order.get('filled', 0))
        average_price = parse_decimal(fetched_order.get('average', 0))
    
    result['executed_qty'] = executed_qty
    result['average_price'] = average_price
    
    console.print(f"[dim]Executed Qty: {executed_qty}[/dim]")
    console.print(f"[dim]Average Price: {average_price}[/dim]")
    
    # If nothing was executed, we're done (no position to protect)
    if executed_qty == 0:
        console.print("[yellow]⚠ No quantity executed - no stop loss needed[/yellow]")
        result['success'] = True
        return result
    
    # ====== STEP 4: PLACE STOP LOSS (ATOMIC DEFENSE) ======
    console.print("\n[bold]Step 4/5: Atomic Defense (Stop Loss)[/bold]")
    
    # CRITICAL: Use ACTUAL executed quantity, not requested quantity
    sl_order = await place_stop_loss(
        exchange=exchange,
        symbol=symbol,
        executed_qty=executed_qty,  # <-- ACTUAL qty, not requested!
        stop_price=stoploss_price,
        is_long=is_long,
        market_info=market_info
    )
    
    if sl_order:
        result['stop_loss_order'] = sl_order
        result['success'] = True
        
        # ====== STEP 5 (OPTIONAL): PLACE TAKE PROFIT ======
        if takeprofit_price is not None and takeprofit_price > 0:
            console.print("\n[bold]Step 5/5: Take Profit (Optional)[/bold]")
            
            tp_order = await place_take_profit(
                exchange=exchange,
                symbol=symbol,
                executed_qty=executed_qty,
                take_profit_price=takeprofit_price,
                is_long=is_long,
                market_info=market_info
            )
            
            if tp_order:
                result['take_profit_order'] = tp_order
        
        tp_info = f"\nTake Profit: {result['take_profit_order']['id']}" if result['take_profit_order'] else ""
        
        console.print(Panel(
            f"[bold green]ATOMIC ENTRY COMPLETE[/bold green]\n"
            f"Entry: {entry_order['id']}\n"
            f"Stop Loss: {sl_order['id']}{tp_info}\n"
            f"Executed: {executed_qty} @ {average_price}",
            title="✅ SUCCESS",
            border_style="green"
        ))
    else:
        # CRITICAL: Stop loss failed - EMERGENCY CLOSE
        console.print("[bold red]✗ STOP LOSS FAILED - INITIATING EMERGENCY CLOSE[/bold red]")
        
        closed = await emergency_close_position(
            exchange=exchange,
            symbol=symbol,
            amount=executed_qty,
            is_long=is_long
        )
        
        if closed:
            console.print("[yellow]⚠ Position emergency closed (no stop loss)[/yellow]")
        else:
            console.print(Panel(
                "[bold red]CRITICAL: EMERGENCY CLOSE FAILED[/bold red]\n"
                f"Symbol: {symbol}\n"
                f"Qty: {executed_qty}\n"
                f"Side: {'LONG' if is_long else 'SHORT'}\n\n"
                "[bold]MANUAL INTERVENTION REQUIRED IMMEDIATELY![/bold]",
                title="🚨 CRITICAL FAILURE 🚨",
                border_style="red"
            ))
        
        raise ExecutionError("Stop loss placement failed - position emergency closed")
    
    return result


async def close_position_with_cancel(
    exchange: SafeExchange,
    symbol: str,
    position_qty: Decimal,
    is_long: bool
) -> bool:
    """
    Close a position, cancelling any existing orders first.
    
    Args:
        exchange: SafeExchange instance
        symbol: Trading symbol
        position_qty: Position quantity
        is_long: True if long position
        
    Returns:
        True if position closed successfully
    """
    console.print(f"[cyan]→ Closing position: {symbol}[/cyan]")
    
    try:
        # Cancel all orders for this symbol first
        await exchange.cancel_all_orders(symbol)
        
        # Close the position
        close_side = 'sell' if is_long else 'buy'
        await exchange.close_position(symbol, position_qty, close_side)
        
        console.print(f"[green]✓ Position closed: {symbol}[/green]")
        return True
    except Exception as e:
        console.print(f"[red]✗ Failed to close position: {e}[/red]")
        return False
