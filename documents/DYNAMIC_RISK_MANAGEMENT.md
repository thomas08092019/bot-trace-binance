# Dynamic Risk Management System

## Overview

The Dynamic Risk Manager adjusts trading parameters based on real-time performance metrics and market conditions. This advanced feature helps optimize risk while protecting capital.

## Features

### 1. **TP Timeout Protection** ⏱
**Problem:** Price reaches TP level but order doesn't fill (price fluctuates around TP)  
**Solution:** If price stays at TP level for X seconds without fill, force close with market order

**Configuration:**
```env
TP_TIMEOUT_SECONDS=30  # Force close after 30s at TP level
```

**How it works:**
1. Monitor when current price reaches TP level
2. Start countdown timer when TP first reached
3. If timer exceeds `TP_TIMEOUT_SECONDS` and TP still not filled → Execute market close
4. Reset timer if price moves away from TP level

**Example:**
```
Position: LONG BTC/USDT @ $50,000
TP Order: $51,500 (3% profit)
Current Price: $51,510 (above TP)
Status: TP reached but order not filled
Action: After 30s → Force close at market price ($51,510)
```

---

### 2. **Dynamic Leverage Adjustment** 📊

Automatically adjusts leverage based on market conditions and performance.

**Configuration:**
```env
ENABLE_DYNAMIC_RISK=true
MIN_LEVERAGE=3
MAX_LEVERAGE=20
BASE_LEVERAGE=10  # From LEVERAGE setting
```

**Adjustment Logic:**

| Condition | Action | Example |
|-----------|--------|---------|
| High Volatility (ATR > 3%) | Reduce leverage by 3 | 10x → 7x |
| Low Volatility (ATR < 1%) | Increase leverage by 2 | 10x → 12x |
| Win Rate < 40% | Reduce leverage by 2 | 10x → 8x |
| Win Rate > 60% | Increase leverage by 1 | 10x → 11x |
| Drawdown > 20% | Set minimum leverage | Any → 3x |
| Drawdown > 10% | Reduce leverage by 2 | 10x → 8x |
| 3+ consecutive losses | Reduce leverage by 2 | 10x → 8x |

**Volatility Calculation:**
```python
# ATR (Average True Range) as % of price
volatility = (ATR / current_price) * 100

# Example:
# BTC @ $50,000, ATR = $1,500
# Volatility = (1500 / 50000) * 100 = 3%
```

---

### 3. **Dynamic Margin Mode Selection** 🎯

Chooses between ISOLATED and CROSS based on market conditions.

**Configuration:**
```env
MARGIN_MODE=isolated  # Base setting
TREND_STRENGTH_THRESHOLD=0.7
```

**Selection Logic:**

| Market Condition | Margin Mode | Reason |
|-----------------|-------------|--------|
| Drawdown > 10% | ISOLATED | Protect capital |
| High volatility (ATR > 3%) | ISOLATED | Reduce risk |
| 2+ consecutive losses | ISOLATED | Safety first |
| Strong trend + Win rate > 60% | CROSS | Capture momentum |
| Default | ISOLATED | Safety default |

**Trend Strength:**
- Calculated from recent win rate (simplified)
- Real implementation would use indicators (ADX, moving averages)
- 0.0 = No trend (choppy), 1.0 = Strong trend

---

### 4. **Trading Stop Protection** 🛑

Automatically stops trading when risk limits breached.

**Stop Conditions:**
- Drawdown > 20% of peak balance
- 5+ consecutive losing trades

**Example:**
```
Peak Balance: $5,000
Current Balance: $3,800
Drawdown: 24%
Action: STOP TRADING (exceeds 20% limit)
```

---

## Configuration Examples

### Conservative (Recommended for Beginners)
```env
ENABLE_DYNAMIC_RISK=false
LEVERAGE=5
MARGIN_MODE=isolated
TP_TIMEOUT_SECONDS=30
```

### Moderate (Recommended for Most Users)
```env
ENABLE_DYNAMIC_RISK=true
MIN_LEVERAGE=3
MAX_LEVERAGE=15
LEVERAGE=10  # Base leverage
MARGIN_MODE=isolated
TP_TIMEOUT_SECONDS=30
HIGH_VOLATILITY_THRESHOLD=3.0
LOW_VOLATILITY_THRESHOLD=1.0
GOOD_WIN_RATE=60.0
BAD_WIN_RATE=40.0
MAX_DRAWDOWN_PERCENT=20.0
```

### Aggressive (Advanced Traders Only)
```env
ENABLE_DYNAMIC_RISK=true
MIN_LEVERAGE=5
MAX_LEVERAGE=20
LEVERAGE=15
MARGIN_MODE=isolated  # Still use isolated for safety
TP_TIMEOUT_SECONDS=20  # Faster timeout
HIGH_VOLATILITY_THRESHOLD=4.0
LOW_VOLATILITY_THRESHOLD=0.5
GOOD_WIN_RATE=70.0
BAD_WIN_RATE=35.0
MAX_DRAWDOWN_PERCENT=25.0
TREND_STRENGTH_THRESHOLD=0.6  # More aggressive CROSS mode
```

---

## Risk Metrics Tracked

The system continuously monitors:

1. **Volatility (ATR%)**: Market volatility as percentage of price
2. **Win Rate**: Percentage of winning trades (last 20 trades)
3. **Drawdown**: Decline from peak balance
4. **Trend Strength**: Market trending vs choppy (0-1 scale)
5. **Consecutive Losses**: Number of losing trades in a row
6. **Total/Winning Trades**: Performance history

---

## How Dynamic Risk Manager Works

### Initialization
```python
# Created automatically in main.py
risk_manager = DynamicRiskManager(
    base_leverage=10,
    min_leverage=3,
    max_leverage=20,
    enabled=True  # From ENABLE_DYNAMIC_RISK
)
```

### Per-Trade Flow
```
1. Calculate current metrics
   ├── Get account balance
   ├── Calculate drawdown from peak
   ├── Calculate win rate (last 20 trades)
   └── Estimate volatility (ATR)

2. Determine optimal leverage
   ├── Start with base leverage (10x)
   ├── Adjust for volatility
   ├── Adjust for win rate
   ├── Adjust for drawdown
   └── Clamp to MIN_LEVERAGE - MAX_LEVERAGE

3. Determine margin mode
   ├── Check safety conditions (drawdown, volatility, losses)
   ├── If safe + strong trend → CROSS
   └── Otherwise → ISOLATED

4. Check if should stop trading
   ├── Drawdown > MAX_DRAWDOWN_PERCENT?
   ├── Too many consecutive losses?
   └── If yes → Stop trading

5. Apply settings to next position
```

### After-Trade Tracking
```python
# Record trade result
risk_manager.add_trade(
    symbol='BTC/USDT',
    side='LONG',
    entry_price=50000,
    exit_price=51500,
    pnl=50  # USDT
)

# Automatic updates:
# - Win rate recalculated
# - Consecutive loss counter updated
# - Trade history maintained (last 100)
```

---

## TP Timeout in Action

### Scenario 1: TP Filled Normally ✅
```
09:00:00 - Position opened: LONG BTC @ $50,000
09:01:30 - Price reaches $51,500 (TP level)
09:01:31 - TP order filled at $51,500
Result: Normal exit, 3% profit
```

### Scenario 2: TP Timeout Triggered ⏱
```
09:00:00 - Position opened: LONG BTC @ $50,000
09:01:30 - Price reaches $51,500 (TP level)
          → Timeout timer starts (30s)
09:01:45 - Price: $51,510 (still at TP)
09:02:00 - Price: $51,505 (still at TP)
09:02:00 - TIMEOUT! Force close at market: $51,505
Result: Market exit, 3.01% profit (saved from potential reversal)
```

### Scenario 3: TP Timeout Reset 🔄
```
09:00:00 - Position opened: LONG BTC @ $50,000
09:01:30 - Price reaches $51,500 (TP level)
          → Timeout timer starts (30s)
09:01:45 - Price drops to $51,400 (below TP)
          → Timeout timer RESET
09:02:00 - Price rises to $51,600 (above TP)
          → New timeout timer starts (30s)
09:02:15 - TP order filled at $51,500
Result: Normal exit, 3% profit
```

---

## Monitoring & Display

### Risk Metrics Summary
```
Risk Metrics:
  Volatility: 2.45%
  Win Rate: 65.0% (13/20)
  Drawdown: 5.2%
  Trend Strength: 0.78
  Consecutive Losses: 0
Risk Settings:
  Leverage: 11x (adjusted from 10x base)
  Margin Mode: CROSS
```

### TP Timeout Alerts
```
⏰ BTC/USDT: TP level $51,500 reached! Timeout started (30s)
⏰ BTC/USDT: TP timeout in 15.3s (price @ $51,510, TP @ $51,500)

╔═ TP TIMEOUT ══════════════════════════════╗
║ TP TIMEOUT - FORCE CLOSING                ║
║ Symbol: BTC/USDT                          ║
║ TP Level: $51,500                         ║
║ Current Price: $51,505                    ║
║ Waited: 30.2s (timeout: 30s)              ║
║ Reason: TP reached but order not filled   ║
╚═══════════════════════════════════════════╝
✓ Position force closed at market price
```

---

## Best Practices

### For Beginners
1. **Disable Dynamic Risk** initially (`ENABLE_DYNAMIC_RISK=false`)
2. Use **fixed leverage** (5-10x)
3. Always use **ISOLATED margin**
4. Keep **TP_TIMEOUT_SECONDS=30** (default)
5. Focus on learning strategy first

### For Intermediate Traders
1. **Enable Dynamic Risk** after 50+ trades
2. Start with **conservative limits** (MIN_LEVERAGE=3, MAX_LEVERAGE=15)
3. Monitor **risk metrics** regularly
4. Adjust thresholds based on your results
5. Keep TP timeout enabled

### For Advanced Traders
1. Fine-tune **all parameters** based on backtest results
2. Consider **lower TP timeout** (20-25s) for scalping
3. Use **wider leverage range** (3-20x)
4. Implement **custom volatility calculations** (ATR, Bollinger Bands)
5. Track and analyze **all metrics**

---

## Safety Warnings ⚠️

1. **Dynamic risk is NOT a guarantee** - Can still lose money
2. **Past performance ≠ future results** - Win rate history doesn't predict future
3. **Volatile markets can change quickly** - ATR might not react fast enough
4. **Always use stop losses** - Dynamic risk won't save you from no SL
5. **Start small** - Test with minimal capital first
6. **Monitor regularly** - Don't leave bot unattended with dynamic risk enabled

---

## Troubleshooting

### Dynamic risk not working
- Check `ENABLE_DYNAMIC_RISK=true` in .env
- Verify at least 10 trades in history (needed for meaningful metrics)
- Look for console messages about leverage adjustments

### TP timeout not triggering
- Verify `TP_TIMEOUT_SECONDS > 0` in .env
- Check if take profit orders exist
- Ensure Position Manager is initialized with tp_timeout parameter
- Look for "TP level reached" messages in console

### Leverage not changing
- Dynamic risk requires 10+ trades minimum
- Check if conditions for adjustment are met (volatility, win rate)
- Verify MIN_LEVERAGE < BASE_LEVERAGE < MAX_LEVERAGE
- Look at console for risk metric displays

### Bot stopped trading
- Check drawdown - might exceed MAX_DRAWDOWN_PERCENT
- Check consecutive losses - might be 5+
- Look for "STOP TRADING" messages in console
- Review risk summary to see current metrics

---

## Summary

The Dynamic Risk Management system provides:

✅ **TP Timeout Protection** - Never miss a profit target  
✅ **Automatic Leverage Adjustment** - Optimize risk/reward  
✅ **Smart Margin Mode Selection** - ISOLATED for safety, CROSS for trends  
✅ **Drawdown Protection** - Stop trading before major losses  
✅ **Performance Tracking** - Learn from every trade  

**Default Recommendation:** Start with TP timeout enabled, dynamic risk disabled. Enable dynamic risk after you have 50+ trades and understand the metrics.
