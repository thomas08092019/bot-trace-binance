"""
core/reporter.py - Trading Journal Analytics & PnL Reporting

Implements:
- Comprehensive PnL analysis across multiple timeframes
- Win rate calculation
- Trade statistics (count, average, max, min)
- Beautiful startup reports using rich.table
- Historical performance tracking

Inspired by binance-trade-bot's reporting features.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Tuple, Optional

from peewee import fn
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from .database import Trade, db

console = Console()


class PnLReporter:
    """
    Trading Journal Analytics Engine.
    
    Provides comprehensive PnL analysis across multiple timeframes
    with win rate and trade statistics.
    """
    
    def __init__(self):
        """Initialize PnL Reporter."""
        self.console = Console()
    
    def _get_date_range(self, period: str) -> Tuple[datetime, datetime]:
        """
        Get start and end datetime for a period.
        
        Args:
            period: 'yesterday', 'last_3_days', 'week', 'last_week', 
                   'month', 'last_month', 'year', 'last_year', 'all_time'
                   
        Returns:
            Tuple of (start_date, end_date)
        """
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        if period == 'yesterday':
            start = today_start - timedelta(days=1)
            end = today_start
            
        elif period == 'last_3_days':
            start = today_start - timedelta(days=3)
            end = now
            
        elif period == 'week':
            # Current week (Monday to now)
            days_since_monday = now.weekday()
            start = today_start - timedelta(days=days_since_monday)
            end = now
            
        elif period == 'last_week':
            # Last week (Monday to Sunday)
            days_since_monday = now.weekday()
            last_monday = today_start - timedelta(days=days_since_monday + 7)
            last_sunday = last_monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
            start = last_monday
            end = last_sunday
            
        elif period == 'month':
            # Current month (1st to now)
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now
            
        elif period == 'last_month':
            # Last month (1st to last day)
            first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_day_of_last_month = first_of_this_month - timedelta(days=1)
            start = last_day_of_last_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = last_day_of_last_month.replace(hour=23, minute=59, second=59)
            
        elif period == 'year':
            # Current year (Jan 1 to now)
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now
            
        elif period == 'last_year':
            # Last year (Jan 1 to Dec 31)
            last_year = now.year - 1
            start = datetime(last_year, 1, 1, 0, 0, 0)
            end = datetime(last_year, 12, 31, 23, 59, 59)
            
        elif period == 'all_time':
            # All recorded trades
            start = datetime(2020, 1, 1)  # Arbitrary early date
            end = now
            
        else:
            raise ValueError(f"Unknown period: {period}")
        
        return start, end
    
    def get_period_stats(self, period: str) -> Dict[str, any]:
        """
        Get trading statistics for a specific period.
        
        Args:
            period: Time period identifier
            
        Returns:
            Dictionary with statistics:
                - total_trades: Total number of exit trades
                - winning_trades: Number of profitable trades
                - losing_trades: Number of losing trades
                - win_rate: Win rate percentage
                - total_pnl: Total realized PnL
                - avg_pnl: Average PnL per trade
                - max_win: Largest winning trade
                - max_loss: Largest losing trade
                - total_fees: Total fees paid
        """
        start_date, end_date = self._get_date_range(period)
        
        # Query exit trades (trades with realized PnL)
        exit_trades = (Trade
                      .select()
                      .where(
                          (Trade.timestamp >= start_date) &
                          (Trade.timestamp <= end_date) &
                          (Trade.realized_pnl.is_null(False)) &
                          (Trade.trade_type.in_(['STOP_LOSS', 'TAKE_PROFIT', 'MANUAL_CLOSE', 'TP_TIMEOUT']))
                      ))
        
        total_trades = exit_trades.count()
        
        if total_trades == 0:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': Decimal('0'),
                'avg_pnl': Decimal('0'),
                'max_win': Decimal('0'),
                'max_loss': Decimal('0'),
                'total_fees': Decimal('0')
            }
        
        # Calculate statistics
        total_pnl = Decimal('0')
        total_fees = Decimal('0')
        winning_trades = 0
        losing_trades = 0
        max_win = Decimal('0')
        max_loss = Decimal('0')
        
        for trade in exit_trades:
            pnl = trade.realized_pnl or Decimal('0')
            total_pnl += pnl
            total_fees += trade.fee or Decimal('0')
            
            if pnl > 0:
                winning_trades += 1
                max_win = max(max_win, pnl)
            elif pnl < 0:
                losing_trades += 1
                max_loss = min(max_loss, pnl)
        
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        avg_pnl = total_pnl / total_trades if total_trades > 0 else Decimal('0')
        
        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'max_win': max_win,
            'max_loss': max_loss,
            'total_fees': total_fees
        }
    
    def print_startup_report(self) -> None:
        """
        Print comprehensive trading performance report on startup.
        
        Shows PnL statistics across multiple timeframes with win rates.
        """
        console.print("\n[bold cyan]═══════════════════════════════════════════════════════[/bold cyan]")
        console.print("[bold cyan]           📊 TRADING PERFORMANCE REPORT               [/bold cyan]")
        console.print("[bold cyan]═══════════════════════════════════════════════════════[/bold cyan]\n")
        
        # Define timeframes to analyze
        timeframes = [
            ('Yesterday', 'yesterday'),
            ('Last 3 Days', 'last_3_days'),
            ('This Week', 'week'),
            ('Last Week', 'last_week'),
            ('This Month', 'month'),
            ('Last Month', 'last_month'),
            ('This Year', 'year'),
            ('All Time', 'all_time')
        ]
        
        # Create main statistics table
        table = Table(show_header=True, header_style="bold cyan", border_style="cyan")
        table.add_column("Timeframe", style="bold", width=15)
        table.add_column("Trades", justify="center", width=8)
        table.add_column("Win Rate", justify="center", width=10)
        table.add_column("Total PnL", justify="right", width=15)
        table.add_column("Avg PnL", justify="right", width=12)
        table.add_column("Max Win", justify="right", width=12)
        table.add_column("Max Loss", justify="right", width=12)
        
        total_all_time_pnl = Decimal('0')
        total_all_time_trades = 0
        
        for label, period in timeframes:
            stats = self.get_period_stats(period)
            
            # Track all-time totals
            if period == 'all_time':
                total_all_time_pnl = stats['total_pnl']
                total_all_time_trades = stats['total_trades']
            
            # Skip empty periods (except All Time)
            if stats['total_trades'] == 0 and period != 'all_time':
                continue
            
            # Format values
            trades_str = str(stats['total_trades'])
            win_rate_str = f"{stats['win_rate']:.1f}%"
            
            # Color-code PnL
            pnl = stats['total_pnl']
            if pnl > 0:
                pnl_str = f"[green]+${pnl:,.2f}[/green]"
                avg_str = f"[green]+${stats['avg_pnl']:,.2f}[/green]"
            elif pnl < 0:
                pnl_str = f"[red]${pnl:,.2f}[/red]"
                avg_str = f"[red]${stats['avg_pnl']:,.2f}[/red]"
            else:
                pnl_str = f"${pnl:,.2f}"
                avg_str = f"${stats['avg_pnl']:,.2f}"
            
            # Format max win/loss
            max_win_str = f"[green]+${stats['max_win']:,.2f}[/green]" if stats['max_win'] > 0 else "-"
            max_loss_str = f"[red]${stats['max_loss']:,.2f}[/red]" if stats['max_loss'] < 0 else "-"
            
            # Add row
            table.add_row(
                label,
                trades_str,
                win_rate_str,
                pnl_str,
                avg_str,
                max_win_str,
                max_loss_str
            )
        
        console.print(table)
        
        # Summary panel
        if total_all_time_trades > 0:
            all_time_stats = self.get_period_stats('all_time')
            
            summary_text = (
                f"[bold]Total Trades:[/bold] {total_all_time_trades}\n"
                f"[bold]Overall Win Rate:[/bold] {all_time_stats['win_rate']:.1f}%\n"
                f"[bold]Total Fees Paid:[/bold] ${all_time_stats['total_fees']:,.2f}\n"
            )
            
            if total_all_time_pnl > 0:
                summary_text += f"[bold]Net Profit:[/bold] [green]+${total_all_time_pnl:,.2f}[/green] 💰"
            elif total_all_time_pnl < 0:
                summary_text += f"[bold]Net Loss:[/bold] [red]${total_all_time_pnl:,.2f}[/red] 📉"
            else:
                summary_text += f"[bold]Net PnL:[/bold] ${total_all_time_pnl:,.2f} ⚖️"
            
            console.print(Panel(
                summary_text,
                title="📈 Overall Performance",
                border_style="green" if total_all_time_pnl > 0 else "red"
            ))
        else:
            console.print("[yellow]No trades recorded yet. Start trading to see statistics![/yellow]")
        
        console.print()
    
    def get_recent_trades(self, limit: int = 10) -> List[Trade]:
        """
        Get most recent trades.
        
        Args:
            limit: Maximum number of trades to return
            
        Returns:
            List of Trade instances
        """
        return (Trade
                .select()
                .order_by(Trade.timestamp.desc())
                .limit(limit))
    
    def print_recent_trades(self, limit: int = 10) -> None:
        """
        Print table of recent trades.
        
        Args:
            limit: Number of recent trades to display
        """
        trades = self.get_recent_trades(limit)
        
        if not trades:
            console.print("[yellow]No trades recorded yet.[/yellow]")
            return
        
        table = Table(title=f"Last {limit} Trades", show_header=True, header_style="bold cyan")
        table.add_column("Time", style="dim", width=19)
        table.add_column("Symbol", width=12)
        table.add_column("Side", width=6)
        table.add_column("Type", width=12)
        table.add_column("Price", justify="right", width=12)
        table.add_column("Qty", justify="right", width=10)
        table.add_column("PnL", justify="right", width=12)
        
        for trade in trades:
            time_str = trade.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            
            # Color-code side
            side_str = f"[green]{trade.side}[/green]" if trade.side == 'LONG' else f"[red]{trade.side}[/red]"
            
            # Format PnL
            if trade.realized_pnl is not None:
                pnl = trade.realized_pnl
                if pnl > 0:
                    pnl_str = f"[green]+${pnl:,.2f}[/green]"
                elif pnl < 0:
                    pnl_str = f"[red]${pnl:,.2f}[/red]"
                else:
                    pnl_str = "$0.00"
            else:
                pnl_str = "-"
            
            table.add_row(
                time_str,
                trade.symbol,
                side_str,
                trade.trade_type,
                f"${trade.price:,.2f}",
                f"{trade.quantity:.4f}",
                pnl_str
            )
        
        console.print(table)
    
    def get_symbol_performance(self, symbol: str) -> Dict[str, any]:
        """
        Get performance statistics for a specific symbol.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT')
            
        Returns:
            Dictionary with symbol-specific statistics
        """
        exit_trades = (Trade
                      .select()
                      .where(
                          (Trade.symbol == symbol) &
                          (Trade.realized_pnl.is_null(False))
                      ))
        
        total_trades = exit_trades.count()
        
        if total_trades == 0:
            return {
                'symbol': symbol,
                'total_trades': 0,
                'win_rate': 0.0,
                'total_pnl': Decimal('0')
            }
        
        total_pnl = Decimal('0')
        winning_trades = 0
        
        for trade in exit_trades:
            pnl = trade.realized_pnl or Decimal('0')
            total_pnl += pnl
            if pnl > 0:
                winning_trades += 1
        
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        
        return {
            'symbol': symbol,
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': total_pnl / total_trades
        }
