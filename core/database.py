"""
core/database.py - Trading Journal Database

Implements:
- SQLite database for trade history
- Peewee ORM for safe data operations
- Trade model with Entry, Stop Loss, Take Profit tracking
- Realized PnL calculation and storage
- Safe transaction handling

Inspired by binance-trade-bot's database architecture.
"""

import os
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any

from peewee import (
    Model,
    SqliteDatabase,
    AutoField,
    CharField,
    DecimalField,
    DateTimeField,
    IntegerField
)
from rich.console import Console

console = Console()

# Database configuration
DB_PATH = os.getenv('DATABASE_PATH', 'trades.db')
db = SqliteDatabase(DB_PATH)


class BaseModel(Model):
    """Base model for all database models."""
    class Meta:
        database = db


class Trade(BaseModel):
    """
    Trade record model.
    
    Stores all executed trades including entries, exits (SL/TP), and manual closes.
    PnL is calculated and stored for exit trades.
    """
    
    id = AutoField(primary_key=True)
    
    # Trade identification
    symbol = CharField(max_length=20, index=True)
    side = CharField(max_length=10)  # 'LONG' or 'SHORT'
    trade_type = CharField(max_length=20, index=True)  # 'ENTRY', 'STOP_LOSS', 'TAKE_PROFIT', 'MANUAL_CLOSE', 'TP_TIMEOUT'
    
    # Order details
    order_id = CharField(max_length=50, null=True)
    quantity = DecimalField(max_digits=20, decimal_places=8)
    price = DecimalField(max_digits=20, decimal_places=8)
    
    # Financial metrics
    realized_pnl = DecimalField(max_digits=20, decimal_places=8, null=True)  # Null for ENTRY
    fee = DecimalField(max_digits=20, decimal_places=8, default=0)
    leverage = IntegerField(default=1)
    
    # Entry reference for PnL calculation
    entry_price = DecimalField(max_digits=20, decimal_places=8, null=True)  # Stored in exit trades
    
    # Timestamps
    timestamp = DateTimeField(default=datetime.now, index=True)
    
    class Meta:
        table_name = 'trades'
        indexes = (
            # Composite index for performance
            (('symbol', 'timestamp'), False),
            (('trade_type', 'timestamp'), False),
        )
    
    def __str__(self):
        pnl_str = f", PnL: ${self.realized_pnl}" if self.realized_pnl else ""
        return f"Trade({self.symbol}, {self.side}, {self.trade_type}, ${self.price}{pnl_str})"


def initialize_db() -> bool:
    """
    Initialize database and create tables.
    
    Returns:
        True if initialization successful
    """
    try:
        db.connect(reuse_if_open=True)
        db.create_tables([Trade], safe=True)
        
        # Get trade count
        trade_count = Trade.select().count()
        
        console.print(f"[green]✓ Database initialized: {DB_PATH}[/green]")
        console.print(f"[dim]  Total trades recorded: {trade_count}[/dim]")
        
        return True
        
    except Exception as e:
        console.print(f"[red]✗ Database initialization failed: {e}[/red]")
        return False


def record_trade(
    symbol: str,
    side: str,
    trade_type: str,
    quantity: Decimal,
    price: Decimal,
    realized_pnl: Optional[Decimal] = None,
    fee: Decimal = Decimal("0"),
    leverage: int = 1,
    entry_price: Optional[Decimal] = None,
    order_id: Optional[str] = None
) -> Optional[Trade]:
    """
    Record a trade in the database.
    
    Args:
        symbol: Trading symbol (e.g., 'BTC/USDT')
        side: 'LONG' or 'SHORT'
        trade_type: 'ENTRY', 'STOP_LOSS', 'TAKE_PROFIT', 'MANUAL_CLOSE', 'TP_TIMEOUT'
        quantity: Trade quantity
        price: Execution price
        realized_pnl: Realized profit/loss (for exits only)
        fee: Trading fee in USDT
        leverage: Leverage used
        entry_price: Entry price reference (for exit trades)
        order_id: Exchange order ID
        
    Returns:
        Trade instance if successful, None otherwise
    """
    try:
        trade = Trade.create(
            symbol=symbol,
            side=side,
            trade_type=trade_type,
            order_id=order_id,
            quantity=quantity,
            price=price,
            realized_pnl=realized_pnl,
            fee=fee,
            leverage=leverage,
            entry_price=entry_price,
            timestamp=datetime.now()
        )
        
        # Log to console
        pnl_info = f", PnL: ${realized_pnl:+.2f}" if realized_pnl else ""
        console.print(f"[dim]📝 Trade recorded: {symbol} {trade_type} @ ${price}{pnl_info}[/dim]")
        
        return trade
        
    except Exception as e:
        console.print(f"[yellow]⚠ Failed to record trade: {e}[/yellow]")
        return None


from .calculator import calculate_pnl  # Import instead of duplicate definition


def get_last_entry_price(symbol: str, side: str) -> Optional[Decimal]:
    """
    Get the most recent entry price for a symbol and side.
    
    Useful for calculating PnL when exit order doesn't have entry price reference.
    
    Args:
        symbol: Trading symbol
        side: 'LONG' or 'SHORT'
        
    Returns:
        Entry price if found, None otherwise
    """
    try:
        entry = (Trade
                 .select()
                 .where(
                     (Trade.symbol == symbol) &
                     (Trade.side == side) &
                     (Trade.trade_type == 'ENTRY')
                 )
                 .order_by(Trade.timestamp.desc())
                 .first())
        
        return entry.price if entry else None
        
    except Exception as e:
        console.print(f"[yellow]⚠ Failed to get last entry price: {e}[/yellow]")
        return None


def record_entry(
    symbol: str,
    side: str,
    quantity: Decimal,
    price: Decimal,
    leverage: int = 1,
    fee: Decimal = Decimal("0"),
    order_id: Optional[str] = None
) -> Optional[Trade]:
    """
    Convenience function to record an entry trade.
    
    Args:
        symbol: Trading symbol
        side: 'LONG' or 'SHORT'
        quantity: Position quantity
        price: Entry price
        leverage: Leverage used
        fee: Entry fee
        order_id: Exchange order ID
        
    Returns:
        Trade instance if successful
    """
    return record_trade(
        symbol=symbol,
        side=side,
        trade_type='ENTRY',
        quantity=quantity,
        price=price,
        realized_pnl=None,  # No PnL for entry
        fee=fee,
        leverage=leverage,
        order_id=order_id
    )


def record_exit(
    symbol: str,
    side: str,
    trade_type: str,
    quantity: Decimal,
    exit_price: Decimal,
    entry_price: Decimal,
    leverage: int = 1,
    fee: Decimal = Decimal("0"),
    order_id: Optional[str] = None
) -> Optional[Trade]:
    """
    Convenience function to record an exit trade with automatic PnL calculation.
    
    Args:
        symbol: Trading symbol
        side: 'LONG' or 'SHORT'
        trade_type: 'STOP_LOSS', 'TAKE_PROFIT', 'MANUAL_CLOSE', 'TP_TIMEOUT'
        quantity: Position quantity
        exit_price: Exit price
        entry_price: Entry price for PnL calculation
        leverage: Leverage used
        fee: Total fees (entry + exit combined)
        order_id: Exchange order ID
        
    Returns:
        Trade instance if successful
    """
    # Calculate realized PnL
    pnl = calculate_pnl(entry_price, exit_price, quantity, side, fee)
    
    return record_trade(
        symbol=symbol,
        side=side,
        trade_type=trade_type,
        quantity=quantity,
        price=exit_price,
        realized_pnl=pnl,
        fee=fee,
        leverage=leverage,
        entry_price=entry_price,
        order_id=order_id
    )


def close_db() -> None:
    """Close database connection."""
    if not db.is_closed():
        db.close()
        console.print("[dim]Database connection closed[/dim]")


# Context manager for safe database operations
class DatabaseContext:
    """Context manager for safe database operations."""
    
    def __enter__(self):
        db.connect(reuse_if_open=True)
        return db
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if not db.is_closed():
            db.close()
        return False
