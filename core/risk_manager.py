"""
core/risk_manager.py - Dynamic Risk Management

Implements:
- Volatility-based leverage adjustment (ATR)
- Win rate monitoring and adjustment
- Drawdown protection
- Market regime detection (Trending vs Sideway)
- Dynamic margin mode selection (ISOLATED vs CROSS)
"""

import os
from decimal import Decimal
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

from rich.console import Console

console = Console()

# Load config from environment
ENABLE_DYNAMIC_RISK = os.getenv('ENABLE_DYNAMIC_RISK', 'false').lower() == 'true'
BASE_LEVERAGE = int(os.getenv('LEVERAGE', '10'))
MIN_LEVERAGE = int(os.getenv('MIN_LEVERAGE', '3'))
MAX_LEVERAGE = int(os.getenv('MAX_LEVERAGE', '20'))
HIGH_VOLATILITY_THRESHOLD = float(os.getenv('HIGH_VOLATILITY_THRESHOLD', '3.0'))
LOW_VOLATILITY_THRESHOLD = float(os.getenv('LOW_VOLATILITY_THRESHOLD', '1.0'))
GOOD_WIN_RATE = float(os.getenv('GOOD_WIN_RATE', '60.0'))
BAD_WIN_RATE = float(os.getenv('BAD_WIN_RATE', '40.0'))
MAX_DRAWDOWN_PERCENT = float(os.getenv('MAX_DRAWDOWN_PERCENT', '20.0'))
TREND_STRENGTH_THRESHOLD = float(os.getenv('TREND_STRENGTH_THRESHOLD', '0.7'))


@dataclass
class TradeHistory:
    """Single trade record."""
    symbol: str
    side: str
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal
    timestamp: datetime
    win: bool


@dataclass
class RiskMetrics:
    """Current risk metrics."""
    volatility: float  # ATR percentage
    win_rate: float  # Last N trades win rate
    drawdown: float  # Current drawdown percentage
    trend_strength: float  # 0-1, higher = stronger trend
    consecutive_losses: int
    total_trades: int
    winning_trades: int


class DynamicRiskManager:
    """
    Dynamic risk manager that adjusts leverage and margin mode
    based on market conditions and performance.
    """
    
    def __init__(
        self,
        base_leverage: int = BASE_LEVERAGE,
        min_leverage: int = MIN_LEVERAGE,
        max_leverage: int = MAX_LEVERAGE,
        enabled: bool = ENABLE_DYNAMIC_RISK
    ):
        """
        Initialize dynamic risk manager.
        
        Args:
            base_leverage: Base leverage to use
            min_leverage: Minimum leverage allowed
            max_leverage: Maximum leverage allowed
            enabled: Enable dynamic adjustment
        """
        self.base_leverage = base_leverage
        self.min_leverage = min_leverage
        self.max_leverage = max_leverage
        self.enabled = enabled
        
        self.trade_history: List[TradeHistory] = []
        self.peak_balance = Decimal("0")
        self.consecutive_losses = 0
        
        console.print(f"[cyan]Dynamic Risk Manager: {'ENABLED' if enabled else 'DISABLED'}[/cyan]")
        if enabled:
            console.print(f"[dim]  Leverage Range: {min_leverage}x - {max_leverage}x (Base: {base_leverage}x)[/dim]")
    
    def add_trade(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        exit_price: Decimal,
        pnl: Decimal
    ) -> None:
        """
        Record a completed trade.
        
        Args:
            symbol: Trading symbol
            side: 'LONG' or 'SHORT'
            entry_price: Entry price
            exit_price: Exit price
            pnl: Profit/Loss in USDT
        """
        win = pnl > 0
        
        trade = TradeHistory(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            timestamp=datetime.now(),
            win=win
        )
        
        self.trade_history.append(trade)
        
        # Track consecutive losses
        if win:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
        
        # Keep only last 100 trades
        if len(self.trade_history) > 100:
            self.trade_history.pop(0)
    
    def calculate_metrics(self, current_balance: Decimal, atr_percent: float = 2.0) -> RiskMetrics:
        """
        Calculate current risk metrics.
        
        Args:
            current_balance: Current account balance
            atr_percent: Current ATR as percentage of price
            
        Returns:
            RiskMetrics object
        """
        # Update peak balance
        if current_balance > self.peak_balance:
            self.peak_balance = current_balance
        
        # Calculate drawdown
        drawdown = 0.0
        if self.peak_balance > 0:
            drawdown = float((self.peak_balance - current_balance) / self.peak_balance * 100)
        
        # Calculate win rate from recent trades (last 20)
        recent_trades = self.trade_history[-20:] if len(self.trade_history) > 0 else []
        win_rate = 0.0
        if recent_trades:
            wins = sum(1 for t in recent_trades if t.win)
            win_rate = (wins / len(recent_trades)) * 100
        
        # Estimate trend strength (simplified - would need market data for real implementation)
        # For now, use win rate as proxy: high win rate = trending, low = choppy
        trend_strength = min(win_rate / 100.0, 1.0)
        
        return RiskMetrics(
            volatility=atr_percent,
            win_rate=win_rate,
            drawdown=drawdown,
            trend_strength=trend_strength,
            consecutive_losses=self.consecutive_losses,
            total_trades=len(self.trade_history),
            winning_trades=sum(1 for t in self.trade_history if t.win)
        )
    
    def calculate_optimal_leverage(self, metrics: RiskMetrics) -> int:
        """
        Calculate optimal leverage based on metrics.
        
        Logic:
        - High volatility → Reduce leverage
        - Low win rate → Reduce leverage
        - High drawdown → Reduce leverage drastically
        - Consecutive losses → Reduce leverage
        
        Args:
            metrics: Current risk metrics
            
        Returns:
            Optimal leverage
        """
        if not self.enabled:
            return self.base_leverage
        
        leverage = self.base_leverage
        
        # 1. Volatility adjustment
        if metrics.volatility > HIGH_VOLATILITY_THRESHOLD:
            leverage = max(leverage - 3, self.min_leverage)
            console.print(f"[yellow]⚠ High volatility ({metrics.volatility:.2f}%) - Reducing leverage[/yellow]")
        elif metrics.volatility < LOW_VOLATILITY_THRESHOLD:
            leverage = min(leverage + 2, self.max_leverage)
        
        # 2. Win rate adjustment
        if metrics.total_trades >= 10:  # Need minimum trades
            if metrics.win_rate < BAD_WIN_RATE:
                leverage = max(leverage - 2, self.min_leverage)
                console.print(f"[yellow]⚠ Low win rate ({metrics.win_rate:.1f}%) - Reducing leverage[/yellow]")
            elif metrics.win_rate > GOOD_WIN_RATE:
                leverage = min(leverage + 1, self.max_leverage)
        
        # 3. Drawdown protection (CRITICAL)
        if metrics.drawdown > MAX_DRAWDOWN_PERCENT:
            leverage = self.min_leverage
            console.print(f"[red]🚨 DRAWDOWN ALERT ({metrics.drawdown:.1f}%) - Minimum leverage enforced[/red]")
        elif metrics.drawdown > MAX_DRAWDOWN_PERCENT / 2:
            leverage = max(leverage - 2, self.min_leverage)
            console.print(f"[yellow]⚠ Drawdown warning ({metrics.drawdown:.1f}%) - Reducing leverage[/yellow]")
        
        # 4. Consecutive losses protection
        if metrics.consecutive_losses >= 3:
            leverage = max(leverage - 2, self.min_leverage)
            console.print(f"[yellow]⚠ {metrics.consecutive_losses} consecutive losses - Reducing leverage[/yellow]")
        
        return max(self.min_leverage, min(leverage, self.max_leverage))
    
    def determine_margin_mode(self, metrics: RiskMetrics) -> str:
        """
        Determine optimal margin mode based on market conditions.
        
        Logic:
        - Strong trend + Good win rate → CROSS (capture momentum)
        - Choppy/Sideway → ISOLATED (protect capital)
        - High volatility → ISOLATED (safety)
        
        Args:
            metrics: Current risk metrics
            
        Returns:
            'isolated' or 'cross'
        """
        if not self.enabled:
            return 'isolated'  # Default to safe mode
        
        # Safety first: Always ISOLATED in these conditions
        if metrics.drawdown > 10.0:
            return 'isolated'
        
        if metrics.volatility > HIGH_VOLATILITY_THRESHOLD:
            return 'isolated'
        
        if metrics.consecutive_losses >= 2:
            return 'isolated'
        
        # Strong trend + Good performance → Can use CROSS
        if (metrics.trend_strength > TREND_STRENGTH_THRESHOLD and 
            metrics.win_rate > GOOD_WIN_RATE and
            metrics.total_trades >= 10):
            console.print(f"[cyan]📊 Strong trend ({metrics.trend_strength:.2f}) + Good win rate → Using CROSS mode[/cyan]")
            return 'cross'
        
        # Default to safe mode
        return 'isolated'
    
    def should_stop_trading(self, metrics: RiskMetrics) -> Tuple[bool, str]:
        """
        Check if trading should be stopped.
        
        Args:
            metrics: Current risk metrics
            
        Returns:
            (should_stop, reason)
        """
        # Maximum drawdown breached
        if metrics.drawdown > MAX_DRAWDOWN_PERCENT:
            return True, f"Maximum drawdown exceeded: {metrics.drawdown:.1f}% > {MAX_DRAWDOWN_PERCENT}%"
        
        # Too many consecutive losses
        if metrics.consecutive_losses >= 5:
            return True, f"Too many consecutive losses: {metrics.consecutive_losses}"
        
        return False, ""
    
    def get_risk_summary(self, metrics: RiskMetrics, leverage: int, margin_mode: str) -> str:
        """
        Get formatted risk summary.
        
        Args:
            metrics: Current risk metrics
            leverage: Current leverage
            margin_mode: Current margin mode
            
        Returns:
            Formatted summary string
        """
        return (
            f"[cyan]Risk Metrics:[/cyan]\n"
            f"  Volatility: {metrics.volatility:.2f}%\n"
            f"  Win Rate: {metrics.win_rate:.1f}% ({metrics.winning_trades}/{metrics.total_trades})\n"
            f"  Drawdown: {metrics.drawdown:.1f}%\n"
            f"  Trend Strength: {metrics.trend_strength:.2f}\n"
            f"  Consecutive Losses: {metrics.consecutive_losses}\n"
            f"[cyan]Risk Settings:[/cyan]\n"
            f"  Leverage: {leverage}x\n"
            f"  Margin Mode: {margin_mode.upper()}"
        )
