"""
core/calculator.py - Safe Mathematical Calculations

Implements:
- Floor rounding relative to symbol's stepSize (NEVER standard rounding)
- Min notional validation (>= 6 USDT)
- All calculations use decimal.Decimal for precision
"""

import os
import math
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from typing import Optional, Dict, Any

from rich.console import Console

console = Console()

# Load config from environment (with defaults)
MIN_NOTIONAL_USDT = Decimal(os.getenv('MIN_NOTIONAL_USDT', '6.0'))


class CalculatorError(Exception):
    """Error during calculation - usually indicates invalid input."""
    pass


def parse_decimal(value: Any) -> Decimal:
    """
    Safely parse any value to Decimal.
    
    Args:
        value: Value to parse (str, int, float, Decimal)
        
    Returns:
        Decimal representation
        
    Raises:
        CalculatorError: If value cannot be parsed
    """
    try:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, float):
            # Convert float to string first to avoid precision issues
            return Decimal(str(value))
        return Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as e:
        raise CalculatorError(f"Cannot parse '{value}' as Decimal: {e}")


def get_step_size(exchange_info: Dict[str, Any], symbol: str) -> Decimal:
    """
    Extract stepSize (lot size) from exchange info.
    
    Args:
        exchange_info: CCXT market info dictionary
        symbol: Trading symbol
        
    Returns:
        Step size as Decimal
        
    Raises:
        CalculatorError: If stepSize cannot be found
    """
    try:
        # CCXT normalizes this under 'precision' and 'limits'
        if 'precision' in exchange_info:
            precision = exchange_info['precision']
            if 'amount' in precision:
                decimal_places = precision['amount']
                # CCXT có 2 kiểu trả về:
                # 1. Số decimal places (int): 3 -> step = 0.001
                # 2. Giá trị step trực tiếp (float): 0.001 -> step = 0.001
                if isinstance(decimal_places, int):
                    return Decimal(10) ** -decimal_places
                elif isinstance(decimal_places, float):
                    # Đây là step size trực tiếp
                    return parse_decimal(decimal_places)
        
        # Try to get from limits
        if 'limits' in exchange_info and 'amount' in exchange_info['limits']:
            limits = exchange_info['limits']['amount']
            if 'min' in limits and limits['min']:
                # Use min as a proxy for step size
                return parse_decimal(limits['min'])
        
        # Fallback: try to find in info dict (raw exchange data)
        if 'info' in exchange_info and 'filters' in exchange_info['info']:
            for f in exchange_info['info']['filters']:
                if f.get('filterType') == 'LOT_SIZE':
                    return parse_decimal(f['stepSize'])
        
        raise CalculatorError(f"Could not find stepSize for {symbol}")
        
    except (KeyError, TypeError) as e:
        raise CalculatorError(f"Error extracting stepSize for {symbol}: {e}")


def get_tick_size(exchange_info: Dict[str, Any], symbol: str) -> Decimal:
    """
    Extract tickSize (price precision) from exchange info.
    
    Args:
        exchange_info: CCXT market info dictionary
        symbol: Trading symbol
        
    Returns:
        Tick size as Decimal
        
    Raises:
        CalculatorError: If tickSize cannot be found
    """
    try:
        if 'precision' in exchange_info:
            precision = exchange_info['precision']
            if 'price' in precision:
                decimal_places = precision['price']
                # CCXT có 2 kiểu trả về:
                # 1. Số decimal places (int): 2 -> tick = 0.01
                # 2. Giá trị tick trực tiếp (float): 0.1 -> tick = 0.1
                if isinstance(decimal_places, int):
                    return Decimal(10) ** -decimal_places
                elif isinstance(decimal_places, float):
                    # Đây là tick size trực tiếp
                    return parse_decimal(decimal_places)
        
        # Fallback: try to find in info dict (raw exchange data)
        if 'info' in exchange_info and 'filters' in exchange_info['info']:
            for f in exchange_info['info']['filters']:
                if f.get('filterType') == 'PRICE_FILTER':
                    return parse_decimal(f['tickSize'])
        
        raise CalculatorError(f"Could not find tickSize for {symbol}")
        
    except (KeyError, TypeError) as e:
        raise CalculatorError(f"Error extracting tickSize for {symbol}: {e}")


def floor_to_step(value: Decimal, step_size: Decimal) -> Decimal:
    """
    Floor a value to the nearest step size.
    
    CRITICAL: This is the ONLY way to calculate quantities.
    NEVER use round() for trading quantities.
    
    Formula: floor(value / step_size) * step_size
    
    Args:
        value: Value to floor
        step_size: Step size to floor to
        
    Returns:
        Floored value
    """
    if step_size <= 0:
        raise CalculatorError(f"Invalid step_size: {step_size}")
    
    # Use math.floor for guaranteed floor behavior
    steps = math.floor(float(value / step_size))
    return Decimal(str(steps)) * step_size


def floor_price_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    """
    Floor a price to the nearest tick size.
    
    Args:
        price: Price to floor
        tick_size: Tick size to floor to
        
    Returns:
        Floored price
    """
    if tick_size <= 0:
        raise CalculatorError(f"Invalid tick_size: {tick_size}")
    
    steps = math.floor(float(price / tick_size))
    return Decimal(str(steps)) * tick_size


def calculate_position_size(
    balance: Decimal,
    risk_percent: Decimal,
    entry_price: Decimal,
    stoploss_price: Decimal,
    step_size: Decimal,
    leverage: int = 1,
    max_position_percent: Decimal = Decimal("10.0")
) -> Decimal:
    """
    Calculate safe position size using floor rounding.
    
    CRITICAL: Limits position size to prevent over-leveraging!
    
    Formula:
        Distance = |entry_price - stoploss_price|
        RawQty = (Balance * Risk% * Leverage) / Distance
        
        BUT limited by:
        MaxMargin = Balance * MaxPosition%
        MaxNotional = MaxMargin * Leverage
        MaxQty = MaxNotional / Entry_Price
        
        FinalQty = MIN(RawQty, MaxQty)
        SafeQty = floor(FinalQty / step_size) * step_size
    
    Args:
        balance: Available balance (USDT)
        risk_percent: Risk percentage (e.g., 1.0 for 1%)
        entry_price: Entry price
        stoploss_price: Stop loss price
        step_size: Symbol's step size
        leverage: Trading leverage
        max_position_percent: Max % of balance to use as margin (default 10%)
        
    Returns:
        Safe quantity (floored to step size), or Decimal("0") if invalid
    """
    try:
        # Validate inputs
        if balance <= 0:
            console.print("[red]✗ Balance must be positive[/red]")
            return Decimal("0")
        
        if entry_price <= 0 or stoploss_price <= 0:
            console.print("[red]✗ Prices must be positive[/red]")
            return Decimal("0")
        
        if risk_percent <= 0:
            console.print("[red]✗ Risk percent must be positive[/red]")
            return Decimal("0")
        
        # Calculate distance (absolute difference)
        distance = abs(entry_price - stoploss_price)
        
        if distance == 0:
            console.print("[red]✗ Entry and stoploss prices cannot be the same[/red]")
            return Decimal("0")
        
        # Calculate risk amount in USDT
        risk_amount = balance * (risk_percent / Decimal("100"))
        
        # Calculate raw quantity based on risk
        # With leverage, we can control more with the same margin
        raw_qty_risk = (risk_amount * Decimal(str(leverage))) / distance
        
        # CRITICAL: Apply position size limit to prevent over-leveraging
        max_margin = balance * (max_position_percent / Decimal("100"))
        max_notional = max_margin * Decimal(str(leverage))
        max_qty_position = max_notional / entry_price
        
        # Take the MINIMUM of risk-based qty and position limit
        if raw_qty_risk > max_qty_position:
            console.print(f"[yellow]⚠ Position size limited by MAX_POSITION_PERCENT ({max_position_percent}%)[/yellow]")
            console.print(f"[yellow]  Risk-based qty: {raw_qty_risk:.2f}, Limited to: {max_qty_position:.2f}[/yellow]")
            raw_qty = max_qty_position
        else:
            raw_qty = raw_qty_risk
        
        # CRITICAL: Floor to step size (NEVER round)
        safe_qty = floor_to_step(raw_qty, step_size)
        
        # Calculate actual margin and notional for logging
        actual_notional = safe_qty * entry_price
        actual_margin = actual_notional / Decimal(str(leverage))
        margin_percent = (actual_margin / balance) * Decimal("100")
        
        console.print(f"[dim]Calculator: balance={balance}, risk={risk_percent}%, "
                     f"distance={distance}, raw_qty={raw_qty}, safe_qty={safe_qty}[/dim]")
        console.print(f"[cyan]Position: notional={actual_notional:.2f} USDT, "
                     f"margin={actual_margin:.2f} USDT ({margin_percent:.1f}% of balance)[/cyan]")
        
        return safe_qty
        
    except Exception as e:
        console.print(f"[red]✗ Position size calculation error: {e}[/red]")
        return Decimal("0")


def validate_min_notional(
    quantity: Decimal,
    price: Decimal,
    min_notional: Decimal = MIN_NOTIONAL_USDT
) -> bool:
    """
    Validate that order meets minimum notional value.
    
    Args:
        quantity: Order quantity
        price: Order price
        min_notional: Minimum notional value (default: 6 USDT)
        
    Returns:
        True if order meets minimum notional, False otherwise
    """
    notional = quantity * price
    
    if notional < min_notional:
        console.print(f"[red]✗ Order notional ({notional} USDT) below minimum ({min_notional} USDT)[/red]")
        return False
    
    console.print(f"[green]✓ Notional check passed: {notional} USDT >= {min_notional} USDT[/green]")
    return True


def calculate_safe_quantity(
    balance: Decimal,
    risk_percent: Decimal,
    entry_price: Decimal,
    stoploss_price: Decimal,
    exchange_info: Dict[str, Any],
    symbol: str,
    leverage: int = 1,
    max_position_percent: Decimal = Decimal("10.0")
) -> Decimal:
    """
    Complete safe quantity calculation with all validations.
    
    This is the main entry point for position sizing.
    
    Args:
        balance: Available balance (USDT)
        risk_percent: Risk percentage
        entry_price: Entry price
        stoploss_price: Stop loss price
        exchange_info: CCXT market info
        symbol: Trading symbol
        leverage: Trading leverage
        max_position_percent: Max % of balance to use as margin
        
    Returns:
        Safe quantity, or Decimal("0") if any validation fails
    """
    try:
        # Get step size from exchange info
        step_size = get_step_size(exchange_info, symbol)
        console.print(f"[dim]Step size for {symbol}: {step_size}[/dim]")
        
        # Calculate position size
        safe_qty = calculate_position_size(
            balance=balance,
            risk_percent=risk_percent,
            entry_price=entry_price,
            stoploss_price=stoploss_price,
            step_size=step_size,
            leverage=leverage,
            max_position_percent=max_position_percent
        )
        
        if safe_qty == 0:
            return Decimal("0")
        
        # Validate minimum notional
        if not validate_min_notional(safe_qty, entry_price):
            return Decimal("0")
        
        return safe_qty
        
    except CalculatorError as e:
        console.print(f"[red]✗ Calculator error: {e}[/red]")
        return Decimal("0")


def calculate_stoploss_price(
    entry_price: Decimal,
    stoploss_percent: Decimal,
    is_long: bool,
    tick_size: Decimal
) -> Decimal:
    """
    Calculate stop loss price based on percentage.
    
    Args:
        entry_price: Entry price
        stoploss_percent: Stop loss percentage (e.g., 2.0 for 2%)
        is_long: True for long position, False for short
        tick_size: Symbol's tick size
        
    Returns:
        Stop loss price (floored to tick size)
    """
    multiplier = Decimal("1") - (stoploss_percent / Decimal("100"))
    
    if is_long:
        # Long position: SL is below entry
        sl_price = entry_price * multiplier
    else:
        # Short position: SL is above entry
        sl_price = entry_price * (Decimal("2") - multiplier)
    
    return floor_price_to_tick(sl_price, tick_size)


def calculate_pnl(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
    is_long: bool
) -> Decimal:
    """
    Calculate profit/loss for a position.
    
    Args:
        entry_price: Entry price
        exit_price: Exit price
        quantity: Position quantity
        is_long: True for long, False for short
        
    Returns:
        PnL in USDT
    """
    if is_long:
        return (exit_price - entry_price) * quantity
    else:
        return (entry_price - exit_price) * quantity
