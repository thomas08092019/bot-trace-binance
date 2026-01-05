# Trading Journal & PnL Reporting

## Overview

The Gemini Immortal Bot includes a comprehensive **Trading Journal** system that automatically tracks all your trades in a local SQLite database and provides detailed performance analytics across multiple timeframes.

Inspired by `binance-trade-bot`'s reporting features, enhanced with advanced PnL analytics.

## Features

✅ **Automatic Trade Recording** - Every entry, exit, stop loss, and take profit is logged  
✅ **Realized PnL Tracking** - Accurate profit/loss calculation for LONG and SHORT positions  
✅ **Multi-Timeframe Analysis** - Performance stats for yesterday, week, month, year, all-time  
✅ **Win Rate Calculation** - Track your success rate over time  
✅ **Beautiful Reports** - Rich terminal tables displayed on startup  
✅ **Zero Configuration** - Works out of the box, no setup required  
✅ **Local Storage** - All data stored in `trades.db` SQLite file  

## Database Schema

### Trade Model

| Field | Type | Description |
|-------|------|-------------|
| id | AutoField | Primary key |
| symbol | CharField | Trading pair (e.g., 'BTC/USDT') |
| side | CharField | 'LONG' or 'SHORT' |
| trade_type | CharField | 'ENTRY', 'STOP_LOSS', 'TAKE_PROFIT', 'MANUAL_CLOSE', 'TP_TIMEOUT' |
| order_id | CharField | Exchange order ID (optional) |
| quantity | DecimalField | Position size |
| price | DecimalField | Execution price |
| realized_pnl | DecimalField | Profit/Loss in USDT (null for ENTRY) |
| fee | DecimalField | Trading fees |
| leverage | IntegerField | Leverage used |
| entry_price | DecimalField | Entry price reference (stored in exits) |
| timestamp | DateTimeField | Trade execution time |

## Automatic Tracking

### What Gets Recorded

**1. Entry Trades**
- Recorded when: Position opened successfully
- PnL: null (no profit/loss yet)
- Includes: Symbol, side, quantity, entry price, leverage

**2. Exit Trades**
- Recorded when: Position closed (TP, SL, timeout, manual)
- PnL: Calculated automatically
- Includes: Exit price, entry price, realized PnL

**3. PnL Calculation**

```python
# For LONG positions
PnL = (Exit Price - Entry Price) × Quantity - Fees

# For SHORT positions
PnL = (Entry Price - Exit Price) × Quantity - Fees
```

**Example:**
```
Entry: LONG BTC/USDT @ $50,000, Qty: 0.1
Exit: TP @ $51,500, Fee: $2
PnL = ($51,500 - $50,000) × 0.1 - $2 = $148
```

## Startup Report

When the bot starts, you'll see a comprehensive performance report:

```
═══════════════════════════════════════════════════════
           📊 TRADING PERFORMANCE REPORT               
═══════════════════════════════════════════════════════

┏━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Timeframe     ┃ Trades ┃ Win Rate ┃     Total PnL ┃    Avg PnL ┃    Max Win ┃   Max Loss ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Yesterday     │   12   │  66.7%   │   +$1,245.50  │   +$103.79 │   +$350.00 │   -$120.00 │
│ Last 3 Days   │   35   │  62.9%   │   +$3,120.80  │    +$89.17 │   +$450.00 │   -$180.00 │
│ This Week     │   58   │  65.5%   │   +$5,890.25  │   +$101.56 │   +$520.00 │   -$200.00 │
│ This Month    │  187   │  63.1%   │  +$18,450.75  │    +$98.67 │   +$850.00 │   -$350.00 │
│ All Time      │  523   │  64.8%   │  +$52,340.20  │   +$100.08 │   +$980.00 │   -$450.00 │
└───────────────┴────────┴──────────┴───────────────┴────────────┴────────────┴────────────┘

╭─ 📈 Overall Performance ──────────────────────────────╮
│ Total Trades: 523                                     │
│ Overall Win Rate: 64.8%                               │
│ Total Fees Paid: $2,615.00                            │
│ Net Profit: +$52,340.20 💰                            │
╰───────────────────────────────────────────────────────╯
```

## Timeframes Analyzed

| Timeframe | Period |
|-----------|--------|
| Yesterday | Yesterday 00:00 - 23:59 |
| Last 3 Days | 3 days ago - now |
| This Week | Monday - today |
| Last Week | Last Monday - Sunday |
| This Month | 1st of month - today |
| Last Month | 1st to last day of last month |
| This Year | Jan 1 - today |
| Last Year | Last year Jan 1 - Dec 31 |
| All Time | All recorded trades |

## Statistics Provided

For each timeframe:

1. **Trades** - Total number of closed positions
2. **Win Rate** - Percentage of profitable trades
3. **Total PnL** - Sum of all realized profits/losses
4. **Avg PnL** - Average profit/loss per trade
5. **Max Win** - Largest winning trade
6. **Max Loss** - Largest losing trade

## API Reference

### Database Functions

```python
from core.database import (
    initialize_db,
    record_entry,
    record_exit,
    calculate_pnl,
    get_last_entry_price
)

# Initialize database (called automatically on startup)
initialize_db()

# Record entry trade
record_entry(
    symbol='BTC/USDT',
    side='LONG',
    quantity=Decimal('0.1'),
    price=Decimal('50000'),
    leverage=10,
    fee=Decimal('1.0'),
    order_id='123456'
)

# Record exit trade (PnL calculated automatically)
record_exit(
    symbol='BTC/USDT',
    side='LONG',
    trade_type='TAKE_PROFIT',
    quantity=Decimal('0.1'),
    exit_price=Decimal('51500'),
    entry_price=Decimal('50000'),
    leverage=10,
    fee=Decimal('2.0'),
    order_id='123457'
)

# Calculate PnL manually
pnl = calculate_pnl(
    entry_price=Decimal('50000'),
    exit_price=Decimal('51500'),
    quantity=Decimal('0.1'),
    side='LONG',
    fee=Decimal('2.0')
)  # Returns: Decimal('148.0')

# Get last entry price for a symbol
entry_price = get_last_entry_price('BTC/USDT', 'LONG')
```

### Reporter Functions

```python
from core.reporter import PnLReporter

reporter = PnLReporter()

# Print startup report (called automatically)
reporter.print_startup_report()

# Get stats for specific period
stats = reporter.get_period_stats('week')
# Returns:
# {
#     'total_trades': 58,
#     'winning_trades': 38,
#     'losing_trades': 20,
#     'win_rate': 65.5,
#     'total_pnl': Decimal('5890.25'),
#     'avg_pnl': Decimal('101.56'),
#     'max_win': Decimal('520.00'),
#     'max_loss': Decimal('-200.00'),
#     'total_fees': Decimal('116.00')
# }

# Print recent trades
reporter.print_recent_trades(limit=10)

# Get symbol-specific performance
btc_stats = reporter.get_symbol_performance('BTC/USDT')
```

## Configuration

### Database Path

```env
# In .env file
DATABASE_PATH=trades.db  # Default location

# Custom path
DATABASE_PATH=/path/to/my_trades.db
```

### Disable Journal (Not Recommended)

The journal runs automatically. To disable, you would need to comment out the database initialization in `main.py`, but this is **NOT recommended** as you'll lose valuable performance insights.

## Data Persistence

- **Database File**: `trades.db` (SQLite)
- **Location**: Same directory as bot
- **Backup**: Simply copy `trades.db` file
- **Migration**: Move `trades.db` to new location and update `DATABASE_PATH`

## Database Management

### View Trades Manually

```python
from core.database import Trade, db

# Connect to database
db.connect()

# Get all trades
for trade in Trade.select().order_by(Trade.timestamp.desc()).limit(10):
    print(f"{trade.timestamp}: {trade.symbol} {trade.trade_type} ${trade.price} PnL: {trade.realized_pnl}")

# Get profitable trades only
profitable = Trade.select().where(Trade.realized_pnl > 0)

# Get trades for specific symbol
btc_trades = Trade.select().where(Trade.symbol == 'BTC/USDT')

# Close connection
db.close()
```

### Export Trades to CSV

```python
import csv
from core.database import Trade, db

db.connect()

with open('trades_export.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Timestamp', 'Symbol', 'Side', 'Type', 'Quantity', 'Price', 'PnL', 'Fee'])
    
    for trade in Trade.select():
        writer.writerow([
            trade.timestamp,
            trade.symbol,
            trade.side,
            trade.trade_type,
            trade.quantity,
            trade.price,
            trade.realized_pnl or 0,
            trade.fee
        ])

db.close()
print("Trades exported to trades_export.csv")
```

### Reset Database

```bash
# Backup first
cp trades.db trades_backup.db

# Delete database
rm trades.db

# Will be recreated on next bot startup
python main.py
```

## Performance Considerations

- **Storage**: ~1KB per trade record
- **Query Speed**: Indexed by symbol and timestamp for fast analytics
- **Memory**: Minimal impact, database operations are non-blocking
- **Startup Time**: +0.1-0.5 seconds for report generation (depends on trade count)

## Advanced Usage

### Custom Analytics

```python
from datetime import datetime, timedelta
from core.database import Trade, db

db.connect()

# Get today's winning trades
today = datetime.now().replace(hour=0, minute=0, second=0)
wins_today = Trade.select().where(
    (Trade.timestamp >= today) &
    (Trade.realized_pnl > 0)
).count()

# Get average hold time (entry to exit)
# Would need to match entries with exits by symbol/side

# Get best performing day
from peewee import fn

best_day = (Trade
    .select(
        fn.DATE(Trade.timestamp).alias('date'),
        fn.SUM(Trade.realized_pnl).alias('daily_pnl')
    )
    .where(Trade.realized_pnl.is_null(False))
    .group_by(fn.DATE(Trade.timestamp))
    .order_by(fn.SUM(Trade.realized_pnl).desc())
    .first())

db.close()
```

## Troubleshooting

### "Database locked" error

SQLite doesn't handle concurrent writes well. The bot handles this automatically, but if you're accessing the database externally:

```python
from core.database import DatabaseContext

# Use context manager for safe access
with DatabaseContext():
    # Your database operations here
    pass
```

### Missing trades in report

- Check if database file exists: `ls -la trades.db`
- Verify trades were recorded: Check console for "📝 Trade recorded" messages
- Ensure bot has write permissions to directory

### Database corruption

```bash
# Validate database
sqlite3 trades.db "PRAGMA integrity_check;"

# If corrupted, restore from backup
cp trades_backup.db trades.db
```

## Best Practices

1. **Regular Backups** - Copy `trades.db` weekly
2. **Monitor Stats** - Review startup report daily for performance trends
3. **Export Data** - Periodically export to CSV for external analysis
4. **Track Patterns** - Use symbol performance stats to identify best pairs
5. **Win Rate Goals** - Aim for >55% win rate for profitable trading

## Future Enhancements

Potential additions (community contributions welcome):

- 📊 Web dashboard for trade visualization
- 📈 Chart generation (equity curve, drawdown graph)
- 📧 Email/Telegram daily performance reports
- 🔄 Trade correlation analysis
- 📉 Drawdown tracking and alerts
- 💹 Sharpe ratio and other advanced metrics

## Summary

The Trading Journal provides:

✅ **Complete Trade History** - Never lose track of a trade  
✅ **Performance Analytics** - Know your stats across all timeframes  
✅ **Win Rate Tracking** - Measure and improve success rate  
✅ **Beautiful Reports** - Easy-to-read terminal displays  
✅ **Zero Overhead** - Automatic, non-intrusive tracking  

**Start trading and watch your performance stats grow!** 📊
