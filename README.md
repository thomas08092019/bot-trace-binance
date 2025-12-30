# Gemini Immortal Trading Bot

A zero-trust, fail-safe Binance Futures trading bot with 11 layers of security.

## 🛡️ Safety Features

- **Single Instance Lock** - Prevents multiple bot instances
- **Floor Rounding** - Never rounds up quantities (avoids insufficient balance)
- **Stale Data Guard** - Rejects data older than 3 seconds
- **Atomic Execution** - Stop loss based on actual executed quantity
- **Ghost Synchronizer** - Auto-fixes stop loss mismatches
- **Panic Script** - Emergency kill switch
- **Spread Guard** - Aborts if spread > 0.1%

## 📁 Project Structure

```
├── main.py              # Main Orchestrator
├── panic.py             # Emergency Kill Switch
├── core/
│   ├── bootstrap.py     # PID Lock & Safety Checks
│   ├── calculator.py    # Math Floor Calculations
│   ├── exchange.py      # CCXT Async Wrapper
│   ├── execution.py     # Atomic Order Execution
│   └── safety.py        # Ghost Synchronizer
└── strategy/
    └── scanner.py       # Market Scanner
```

## 🚀 Quick Start

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure:
```bash
cp .env.example .env
# Edit .env with your API keys
```

3. Run:
```bash
python main.py
```

4. Emergency Stop:
```bash
python panic.py
```

## ⚠️ Warning

This bot trades real money. Use at your own risk. Always test on testnet first.

## 📜 License

MIT
