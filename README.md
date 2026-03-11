# 🔁 Option Scalper Bot (IBKR)

A medium-frequency option scalping bot for Interactive Brokers using `ib_insync`.

## Quick Start

### Prerequisites
- Python 3.9+
- TWS or IB Gateway running with API enabled (Settings → API → Enable)
- Paper trading account recommended for testing

### Install
```bash
cd option_scalper
pip install -r requirements.txt
```

### Configure
Edit `option_scalper/config.py` to set your:
- TWS host/port (default: `127.0.0.1:7497` for TWS Paper)
- Symbol, right (call/put), number of strikes
- Signal parameters (spread, momentum thresholds)
- Risk limits (max positions, daily loss cap)

### Run
```bash
cd option_scalper
python bot.py
```

### Output
- Console: real-time status and signals
- `scalper.log`: detailed debug log
- `trades.csv`: trade journal for post-analysis

## Architecture

```
bot.py (orchestrator)
├── connection.py    → IBKR connection with auto-reconnect
├── data_feed.py     → Real-time option quote streaming
├── signals.py       → Entry/exit signal detection
├── execution.py     → Order placement & position tracking
├── risk_manager.py  → Risk controls & daily limits
└── logger_setup.py  → Logging & trade journaling
```

## ⚠️ Important Disclaimers
- **Paper trade first** – never deploy untested code with real money.
- Options scalping is extremely risky – you can lose your entire investment.
- IBKR enforces API rate limits; this bot paces requests appropriately.
- Review IBKR's Pattern Day Trader rules and margin requirements.
- This is a starting framework – production bots need additional testing, monitoring, and hardening.
