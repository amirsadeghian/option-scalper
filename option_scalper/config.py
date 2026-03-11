"""
Configuration for the Option Scalping Bot.
Adjust parameters to match your trading style and risk tolerance.
"""

# ── IBKR Connection ──────────────────────────────────────────
TWS_HOST = "127.0.0.1"
TWS_PORT = 7497          # 7497 = TWS Paper, 4002 = Gateway Paper
CLIENT_ID = 1

# ── Instrument ───────────────────────────────────────────────
SYMBOL = "SPY"           # Underlying symbol
EXCHANGE = "SMART"
CURRENCY = "USD"
RIGHTS = ["C", "P"]      # Trade both calls AND puts (bidirectional)
NUM_STRIKES = 5          # Number of ATM strikes to monitor per side

# ── Signal Parameters ────────────────────────────────────────
SPREAD_THRESHOLD = 0.05      # Max bid-ask spread to enter ($)
MOMENTUM_WINDOW = 10         # Number of ticks for momentum calc
MOMENTUM_THRESHOLD = 0.03    # Min price move to trigger entry ($)
MIN_VOLUME = 50              # Minimum volume filter
MIN_GAMMA = 0.03             # Min gamma — ensures meaningful convexity at entry

# ── Execution ────────────────────────────────────────────────
ORDER_SIZE = 1               # Number of contracts per trade
PROFIT_TARGET = 0.10         # Profit target per contract ($)
STOP_LOSS = 0.08             # Stop loss per contract ($)
TRAIL_AMOUNT = 0.03          # Trailing stop amount ($)
USE_LIMIT_ORDERS = True      # Use limit orders instead of market
LIMIT_OFFSET = 0.01          # Offset from mid for limit orders ($)

# ── Risk Management ─────────────────────────────────────────
MAX_POSITIONS = 3            # Max concurrent open positions
MAX_DAILY_LOSS = 200.0       # Max daily loss before shutdown ($)
MAX_TRADES_PER_DAY = 50      # Max trades per day
COOLDOWN_SECONDS = 5         # Seconds between trades
MAX_POSITION_HOLD_SEC = 120  # Force-close after N seconds

# ── Logging ──────────────────────────────────────────────────
LOG_FILE = "scalper.log"
TRADE_LOG_FILE = "trades.csv"