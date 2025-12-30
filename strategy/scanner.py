"""
strategy/scanner.py - Market Scanner & Signal Generator

Implements:
- Volume filter (minimum 24h volume)
- Technical analysis (RSI, EMA crossover)
- Signal generation for entry opportunities
"""

import os
import asyncio
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

console = Console()

# Load config from environment (with defaults)
MIN_VOLUME_USDT = Decimal(os.getenv('MIN_VOLUME_USDT', '10000000'))

# Technical indicator settings
RSI_PERIOD = int(os.getenv('RSI_PERIOD', '14'))
RSI_OVERSOLD = float(os.getenv('RSI_OVERSOLD', '45'))
RSI_OVERBOUGHT = float(os.getenv('RSI_OVERBOUGHT', '55'))

EMA_FAST_PERIOD = int(os.getenv('EMA_FAST_PERIOD', '9'))
EMA_SLOW_PERIOD = int(os.getenv('EMA_SLOW_PERIOD', '21'))


@dataclass
class Signal:
    """Trading signal with metadata."""
    symbol: str
    direction: str  # 'LONG' or 'SHORT'
    strength: float  # 0.0 to 1.0
    entry_price: Decimal
    stoploss_price: Decimal
    reason: str


def calculate_rsi(closes: List[float], period: int = RSI_PERIOD) -> float:
    """
    Calculate Relative Strength Index.
    
    Args:
        closes: List of closing prices
        period: RSI period
        
    Returns:
        RSI value (0-100)
    """
    if len(closes) < period + 1:
        return 50.0  # Neutral if not enough data
    
    # Calculate price changes
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    
    # Get last 'period' changes
    recent_changes = changes[-(period):]
    
    gains = [c for c in recent_changes if c > 0]
    losses = [-c for c in recent_changes if c < 0]
    
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def calculate_ema(prices: List[float], period: int) -> List[float]:
    """
    Calculate Exponential Moving Average.
    
    Args:
        prices: List of prices
        period: EMA period
        
    Returns:
        List of EMA values
    """
    if len(prices) < period:
        return []
    
    ema = []
    multiplier = 2 / (period + 1)
    
    # First EMA is SMA
    sma = sum(prices[:period]) / period
    ema.append(sma)
    
    # Calculate remaining EMAs
    for price in prices[period:]:
        ema_value = (price - ema[-1]) * multiplier + ema[-1]
        ema.append(ema_value)
    
    return ema


def detect_ema_crossover(
    fast_ema: List[float],
    slow_ema: List[float]
) -> Optional[str]:
    """
    Detect EMA crossover.
    
    Args:
        fast_ema: Fast EMA values
        slow_ema: Slow EMA values
        
    Returns:
        'BULLISH' for bullish cross, 'BEARISH' for bearish, None otherwise
    """
    if len(fast_ema) < 2 or len(slow_ema) < 2:
        return None
    
    # Current positions
    fast_above_now = fast_ema[-1] > slow_ema[-1]
    fast_above_prev = fast_ema[-2] > slow_ema[-2]
    
    if fast_above_now and not fast_above_prev:
        return 'BULLISH'  # Fast crossed above slow
    elif not fast_above_now and fast_above_prev:
        return 'BEARISH'  # Fast crossed below slow
    
    return None


async def analyze_symbol(
    exchange,
    symbol: str,
    stoploss_percent: Decimal
) -> Optional[Signal]:
    """
    Analyze a symbol for trading signals.
    
    Args:
        exchange: SafeExchange instance
        symbol: Trading symbol
        stoploss_percent: Stop loss percentage
        
    Returns:
        Signal if found, None otherwise
    """
    try:
        # Fetch OHLCV data
        ohlcv = await exchange.fetch_ohlcv(symbol, '1h', 100)
        
        if len(ohlcv) < 50:
            return None
        
        # Extract closes
        closes = [candle[4] for candle in ohlcv]
        
        # Calculate indicators
        rsi = calculate_rsi(closes)
        fast_ema = calculate_ema(closes, EMA_FAST_PERIOD)
        slow_ema = calculate_ema(closes, EMA_SLOW_PERIOD)
        
        crossover = detect_ema_crossover(fast_ema, slow_ema)
        
        current_price = Decimal(str(closes[-1]))
        
        # Get EMA position for logging
        ema_position = "BULLISH" if fast_ema[-1] > slow_ema[-1] else "BEARISH"
        ema_cross_str = f", {crossover} cross" if crossover else ""
        
        # Log analysis details
        coin_name = symbol.split('/')[0]
        console.print(
            f"  [cyan]{coin_name:8}[/cyan] │ "
            f"RSI: [yellow]{rsi:5.1f}[/yellow] │ "
            f"EMA: [{'green' if ema_position == 'BULLISH' else 'red'}]{ema_position}{ema_cross_str}[/{'green' if ema_position == 'BULLISH' else 'red'}]",
            end=""
        )
        
        signal = None
        
        # RELAXED TESTNET LOGIC: RSI + EMA position (no crossover required)
        # LONG signal: RSI oversold + EMA is bullish (uptrend)
        if rsi < RSI_OVERSOLD and ema_position == 'BULLISH':
            sl_price = current_price * (Decimal("1") - stoploss_percent / Decimal("100"))
            signal = Signal(
                symbol=symbol,
                direction='LONG',
                strength=min((RSI_OVERSOLD - rsi) / RSI_OVERSOLD, 1.0),
                entry_price=current_price,
                stoploss_price=sl_price,
                reason=f"RSI oversold ({rsi:.1f}) + Bullish EMA trend"
            )
            console.print(f" → [bold green]LONG SIGNAL![/bold green] (Strength: {signal.strength:.2f})")
        
        # SHORT signal: RSI overbought + EMA is bearish (downtrend)
        elif rsi > RSI_OVERBOUGHT and ema_position == 'BEARISH':
            sl_price = current_price * (Decimal("1") + stoploss_percent / Decimal("100"))
            signal = Signal(
                symbol=symbol,
                direction='SHORT',
                strength=min((rsi - RSI_OVERBOUGHT) / (100 - RSI_OVERBOUGHT), 1.0),
                entry_price=current_price,
                stoploss_price=sl_price,
                reason=f"RSI overbought ({rsi:.1f}) + Bearish EMA trend"
            )
            console.print(f" → [bold red]SHORT SIGNAL![/bold red] (Strength: {signal.strength:.2f})")
        
        else:
            # Log why no signal
            reasons = []
            if rsi >= RSI_OVERSOLD and rsi <= RSI_OVERBOUGHT:
                reasons.append(f"RSI neutral ({rsi:.1f})")
            elif rsi < RSI_OVERSOLD and ema_position == 'BEARISH':
                reasons.append(f"RSI oversold but EMA bearish")
            elif rsi > RSI_OVERBOUGHT and ema_position == 'BULLISH':
                reasons.append(f"RSI overbought but EMA bullish")
            
            reason_str = ", ".join(reasons) if reasons else "No conditions met"
            console.print(f" → [dim]{reason_str}[/dim]")
        
        return signal
        
    except Exception as e:
        console.print(f"[dim]Analysis error for {symbol}: {e}[/dim]")
        return None


async def filter_by_volume(
    exchange,
    symbols: List[str],
    min_volume: Decimal = MIN_VOLUME_USDT
) -> List[str]:
    """
    Filter symbols by 24h volume.
    
    Args:
        exchange: SafeExchange instance
        symbols: List of symbols to filter
        min_volume: Minimum 24h volume in USDT
        
    Returns:
        Filtered list of symbols meeting volume criteria
    """
    filtered = []
    
    for symbol in symbols:
        try:
            ticker = await exchange.fetch_ticker(symbol)
            volume_usdt = Decimal(str(ticker.get('quoteVolume', 0)))
            
            if volume_usdt >= min_volume:
                filtered.append(symbol)
        except Exception:
            continue
    
    return filtered


async def scan_market(
    exchange,
    symbols: List[str],
    stoploss_percent: Decimal,
    max_signals: int = 5
) -> List[Signal]:
    """
    Scan market for trading signals.
    
    Process:
    1. Filter by volume
    2. Analyze each symbol
    3. Return top signals by strength
    
    Args:
        exchange: SafeExchange instance
        symbols: List of symbols to scan
        stoploss_percent: Stop loss percentage
        max_signals: Maximum number of signals to return
        
    Returns:
        List of signals sorted by strength
    """
    console.print("\n[bold cyan]═══ MARKET SCANNER ═══[/bold cyan]")
    
    # Step 1: Volume filter
    console.print(f"[dim]Filtering {len(symbols)} symbols by volume...[/dim]")
    volume_filtered = await filter_by_volume(exchange, symbols)
    console.print(f"[dim]{len(volume_filtered)} symbols passed volume filter[/dim]")
    
    if not volume_filtered:
        console.print("[yellow]⚠ No symbols passed volume filter[/yellow]")
        return []
    
    # Step 2: Analyze symbols
    console.print(f"\n[bold]Analyzing {len(volume_filtered)} symbols:[/bold]")
    symbols_str = ', '.join([s.split('/')[0] for s in volume_filtered])
    console.print(f"[dim]{symbols_str}[/dim]\n")
    
    signals = []
    for symbol in volume_filtered:
        signal = await analyze_symbol(exchange, symbol, stoploss_percent)
        if signal:
            signals.append(signal)
    
    # Step 3: Sort by strength and return top signals
    signals.sort(key=lambda s: s.strength, reverse=True)
    top_signals = signals[:max_signals]
    
    # Display results
    if top_signals:
        table = Table(title="Trading Signals")
        table.add_column("Symbol", style="cyan")
        table.add_column("Direction", style="magenta")
        table.add_column("Strength", style="yellow")
        table.add_column("Entry", style="green")
        table.add_column("Stop Loss", style="red")
        table.add_column("Reason", style="dim")
        
        for sig in top_signals:
            dir_color = "green" if sig.direction == 'LONG' else "red"
            table.add_row(
                sig.symbol,
                f"[{dir_color}]{sig.direction}[/{dir_color}]",
                f"{sig.strength:.2f}",
                str(sig.entry_price),
                str(sig.stoploss_price),
                sig.reason
            )
        
        console.print(table)
    else:
        console.print("[yellow]⚠ No trading signals found[/yellow]")
    
    return top_signals


async def fetch_top_symbols(
    exchange,
    limit: int = 15
) -> List[str]:
    """
    Fetch top symbols by 24h volume dynamically.
    
    Args:
        exchange: SafeExchange instance
        limit: Number of top symbols to return
        
    Returns:
        List of top volume symbols (USDT pairs only)
    """
    try:
        # Fetch all tickers
        tickers = await exchange.fetch_tickers()
        
        # Filter for USDT pairs and extract volume data
        usdt_pairs = []
        for symbol, ticker in tickers.items():
            if symbol.endswith('/USDT:USDT'):  # Futures format
                volume = float(ticker.get('quoteVolume', 0))
                if volume > 0:
                    usdt_pairs.append({
                        'symbol': symbol,
                        'volume': volume
                    })
        
        # Sort by volume (descending)
        usdt_pairs.sort(key=lambda x: x['volume'], reverse=True)
        
        # Get top N symbols
        top_symbols = [pair['symbol'] for pair in usdt_pairs[:limit]]
        
        if top_symbols:
            # Display detailed table
            table = Table(title=f"Top {limit} Volume Pairs")
            table.add_column("Rank", style="dim")
            table.add_column("Symbol", style="cyan")
            table.add_column("24h Volume (USDT)", style="green", justify="right")
            
            for i, pair in enumerate(usdt_pairs[:limit], 1):
                volume_m = pair['volume'] / 1_000_000
                table.add_row(
                    str(i),
                    pair['symbol'].split('/')[0],
                    f"{volume_m:,.2f}M"
                )
            
            console.print(table)
        
        return top_symbols
        
    except Exception as e:
        console.print(f"[yellow]⚠ Error fetching top symbols: {e}[/yellow]")
        # Fallback to default symbols
        return get_default_symbols()


def get_default_symbols() -> List[str]:
    """
    Get default list of symbols to scan (fallback).
    
    DEPRECATED: Use fetch_top_symbols() instead for dynamic ranking.
    
    Returns:
        List of popular trading pairs
    """
    return [
        'BTC/USDT:USDT',
        'ETH/USDT:USDT',
        'BNB/USDT:USDT',
        'SOL/USDT:USDT',
        'XRP/USDT:USDT',
        'DOGE/USDT:USDT',
        'ADA/USDT:USDT',
        'AVAX/USDT:USDT',
        'DOT/USDT:USDT',
        'MATIC/USDT:USDT',
        'LINK/USDT:USDT',
        'UNI/USDT:USDT',
        'ATOM/USDT:USDT',
        'LTC/USDT:USDT',
        'ETC/USDT:USDT',
    ]
