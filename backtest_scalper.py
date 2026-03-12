#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest for option_scalper strategy
--------------------------------------
Uses IB TWS Paper connection to fetch 30 days of 5-min SPY bars.
Simulates the EXACT same signal logic as option_scalper/:
  - Bid-ask spread filter  (<= $0.05)
  - Delta filter           (0.20 <= |delta| <= 0.70)
  - 10-bar momentum filter (>= $0.03)
  - Profit target          (+$0.10 / contract)
  - Stop loss              (-$0.08 / contract)
  - Force-close            (after 24 bars = 120 min at 5-min bars)
  - Risk gates             (max 3 positions, $200 daily loss cap, 50 trades/day, 5s cooldown)

Option pricing uses Black-Scholes + rolling historical vol as IV proxy.
Spread estimated as max(0.01, 2% of mid) -- typical for near-ATM SPY options.
Volume filter skipped (SPY options are always sufficiently liquid).
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import warnings
from collections import deque
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import pytz
from scipy.stats import norm

from ib_async import IB, Stock, util

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
#  CONFIGURATION  (mirrors option_scalper/config.py exactly)
# ---------------------------------------------------------------------------

HOST      = "127.0.0.1"
PORT      = 7497
CLIENT_ID = 3          # distinct from bot (1) and backtest_ib (2)

# Signal params
SPREAD_THRESHOLD   = 0.05
MOMENTUM_WINDOW    = 10     # bars
MOMENTUM_THRESHOLD = 0.03
MIN_DELTA          = 0.20
MAX_DELTA          = 0.70
MIN_GAMMA          = 0.03   # minimum gamma for entry
MAX_ENTRY_PRICE    = 1.50   # skip options priced above this at entry ($)
TRADE_HOURS        = {9, 10, 14, 15}  # ET hours allowed for new entries (block 11-13 chop)

# Exit params
PROFIT_TARGET          = 0.10    # $ per share
STOP_LOSS              = 0.08    # $ per share
MAX_POSITION_HOLD_BARS = 24      # 24 x 5-min bars = 120 min
SLIPPAGE               = 0.01    # limit offset per side

# Risk params
STARTING_BALANCE   = 26_000.0   # Paper account starting balance ($)
MAX_DAILY_LOSS_PCT = 0.10        # Max daily loss as % of current balance
MAX_POSITIONS      = 3
MAX_TRADES_PER_DAY = 50
COOLDOWN_BARS      = 1           # 1 bar = 5-min cooldown (mirrors 5s in live bot)

RISK_FREE_RATE = 0.045
ET_TZ          = pytz.timezone("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-8s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ScalperBacktest")


# ---------------------------------------------------------------------------
#  BLACK-SCHOLES HELPERS
# ---------------------------------------------------------------------------

def bs_call_price(S, K, T, r, sigma):
    if T <= 1e-6 or sigma <= 1e-6:
        return max(S - K, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return float(S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2))


def bs_put_price(S, K, T, r, sigma):
    if T <= 1e-6 or sigma <= 1e-6:
        return max(K - S, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return float(K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1))


def bs_gamma(S, K, T, r, sigma):
    """Gamma is identical for calls and puts."""
    if T <= 1e-6 or sigma <= 1e-6:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return float(norm.pdf(d1) / (S * sigma * np.sqrt(T)))


def bs_delta(S, K, T, r, sigma, right: str = "C"):
    if T <= 1e-6 or sigma <= 1e-6:
        if right == "C":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return float(norm.cdf(d1) if right == "C" else norm.cdf(d1) - 1)


def time_to_expiry(ts: datetime) -> float:
    today      = ts.date()
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    expiry_date = today + timedelta(days=days_ahead)
    expiry_dt   = ET_TZ.localize(
        datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0)
    )
    secs = (expiry_dt - ts).total_seconds()
    return max(secs / (365.25 * 86400), 1 / 365.25)


def rolling_hist_vol(closes: pd.Series, window: int = 20) -> pd.Series:
    """Annualised historical vol from 5-min log returns (78 bars/day x 252 days)."""
    return np.log(closes / closes.shift(1)).rolling(window).std() * np.sqrt(252 * 78)


# ---------------------------------------------------------------------------
#  OPTION QUOTE SIMULATOR  (mirrors OptionQuote + DataFeed.update())
# ---------------------------------------------------------------------------

class SimQuote:
    """Simulated OptionQuote for one strike+right at one bar."""
    __slots__ = ("mid", "spread", "delta", "gamma", "price_history")

    def __init__(self):
        self.mid:           float = 0.0
        self.spread:        float = 0.0
        self.delta:         float = 0.0
        self.gamma:         float = 0.0
        self.price_history: deque = deque(maxlen=MOMENTUM_WINDOW)


# ---------------------------------------------------------------------------
#  SIGNAL ENGINE  (exact port of option_scalper/signals.py)
# ---------------------------------------------------------------------------

def momentum(q: SimQuote) -> float:
    h = list(q.price_history)
    if len(h) < MOMENTUM_WINDOW:
        return 0.0
    return h[-1] - h[0]


def evaluate_entry(q: SimQuote) -> Optional[float]:
    """Return signal strength (0-1) or None if no signal."""
    if q.mid <= 0 or q.spread > SPREAD_THRESHOLD:
        return None
    if not (MIN_DELTA <= abs(q.delta) <= MAX_DELTA):   # abs handles calls+puts
        return None
    if q.gamma < MIN_GAMMA:
        return None
    mom = momentum(q)
    if mom < MOMENTUM_THRESHOLD:
        return None
    spread_score   = max(0.0, 1 - q.spread / SPREAD_THRESHOLD)
    momentum_score = min(1.0, mom / (MOMENTUM_THRESHOLD * 3))
    gamma_score    = min(1.0, q.gamma / (MIN_GAMMA * 5))
    return (spread_score + momentum_score + gamma_score) / 3


def check_exit(entry_price: float, q: SimQuote) -> Optional[str]:
    if q.mid <= 0:
        return None
    pnl = q.mid - entry_price
    if pnl >= PROFIT_TARGET:
        return "profit_target"
    if pnl <= -STOP_LOSS:
        return "stop_loss"
    return None


# ---------------------------------------------------------------------------
#  BACKTEST ENGINE
# ---------------------------------------------------------------------------

class ScalperBacktest:

    def __init__(self):
        self.ib = IB()

    async def connect(self):
        while True:
            try:
                await self.ib.connectAsync(HOST, PORT, clientId=CLIENT_ID)
                log.info(f"Connected  |  {HOST}:{PORT}  clientId={CLIENT_ID}")
                break
            except Exception as exc:
                log.error(f"Connection failed: {exc}  -- retrying in 10 s ...")
                await asyncio.sleep(10)

    async def fetch_spy_bars(self, total_days: int = 365) -> pd.DataFrame:
        """Fetch up to `total_days` of 5-min SPY bars in 30-day chunks.

        IB limits 5-min bar requests to ~60 days per call.  We use 30-day
        chunks with a 12-second pacing delay between requests to stay safely
        within IB's 60-requests-per-10-minutes rule.
        """
        spy   = Stock("SPY", "SMART", "USD")
        [spy] = await self.ib.qualifyContractsAsync(spy)

        n_chunks = (total_days + 29) // 30          # ceiling division
        all_dfs: list[pd.DataFrame] = []
        end_dt  = ""                                 # empty = right now

        for i in range(n_chunks):
            log.info(f"Fetching chunk {i + 1}/{n_chunks}  "
                     f"(end={end_dt or 'now'}) ...")
            try:
                bars = await self.ib.reqHistoricalDataAsync(
                    spy, endDateTime=end_dt, durationStr="30 D",
                    barSizeSetting="5 mins", whatToShow="TRADES",
                    useRTH=True, formatDate=1,
                )
            except Exception as exc:
                log.warning(f"Chunk {i + 1} failed ({exc}) — stopping early.")
                break

            if not bars:
                log.warning(f"No bars returned for chunk {i + 1} — stopping early.")
                break

            df_chunk = util.df(bars)
            df_chunk.columns = [c.lower() for c in df_chunk.columns]
            df_chunk["date"] = pd.to_datetime(df_chunk["date"])
            if df_chunk["date"].dt.tz is None:
                df_chunk["date"] = df_chunk["date"].dt.tz_localize(ET_TZ)
            else:
                df_chunk["date"] = df_chunk["date"].dt.tz_convert(ET_TZ)

            all_dfs.append(df_chunk)
            earliest = df_chunk["date"].min()
            log.info(f"  {len(df_chunk)} bars  "
                     f"{earliest}  ->  {df_chunk['date'].max()}")

            # Move end pointer one minute before the earliest bar in this chunk
            end_dt = (earliest - timedelta(minutes=1)).strftime("%Y%m%d %H:%M:%S")

            if i < n_chunks - 1:
                log.info("  Pausing 12 s (IB pacing) ...")
                await asyncio.sleep(12)

        if not all_dfs:
            raise RuntimeError("IB returned no bars.")

        # Merge chunks (they arrive newest-first) → sort ascending
        df = pd.concat(reversed(all_dfs))
        df = df.drop_duplicates(subset=["date"]).sort_values("date")
        df.set_index("date", inplace=True)
        log.info(f"Total: {len(df)} bars  |  {df.index[0]}  ->  {df.index[-1]}")
        return df

    def simulate(self, df: pd.DataFrame) -> list[dict]:
        """
        Bar-by-bar simulation mirroring bot._main_loop().

        Evaluates 5 strikes x 2 rights (calls + puts) = 10 contracts per bar.
        Calls:  buy when call momentum is positive (underlying trending up).
        Puts:   buy when put  momentum is positive (underlying trending down).
        Gamma filter ensures only high-convexity options are entered.
        """
        df = df.copy()
        df["iv"]  = rolling_hist_vol(df["close"], 20).clip(0.05, 3.0).fillna(0.20)
        df.dropna(subset=["iv"], inplace=True)

        trades: list[dict] = []

        # Risk state (mirrors RiskManager)
        current_balance  = float(STARTING_BALANCE)
        daily_loss_limit = current_balance * MAX_DAILY_LOSS_PCT
        daily_pnl        = 0.0
        trade_count      = 0
        last_trade_bar   = -999
        halted           = False
        current_day: Optional[date] = None

        # Position state: {(strike, right) -> (entry_price, entry_bar, entry_ts, order_size)}
        positions: dict[tuple, tuple] = {}

        # Per-(strike, right) quote history
        quote_history: dict[tuple, SimQuote] = {}

        bars = list(df.iterrows())

        for bar_idx, (ts, row) in enumerate(bars):
            S   = float(row["close"])
            iv  = float(row["iv"])
            T   = time_to_expiry(ts)

            # Reset daily counters on new trading day
            today = ts.date()
            if today != current_day:
                daily_pnl        = 0.0
                trade_count      = 0
                halted           = False
                daily_loss_limit = current_balance * MAX_DAILY_LOSS_PCT
                current_day      = today

            # Determine the 5 near-ATM strikes x 2 rights = 10 contracts
            atm     = round(S)
            strikes = [float(atm + k) for k in (-2, -1, 0, 1, 2)]
            rights  = ["C", "P"]

            # Update SimQuote objects for each (strike, right)
            for K in strikes:
                for right in rights:
                    key = (K, right)
                    if key not in quote_history:
                        quote_history[key] = SimQuote()
                    q = quote_history[key]

                    if right == "C":
                        mid   = bs_call_price(S, K, T, RISK_FREE_RATE, iv)
                    else:
                        mid   = bs_put_price(S, K, T, RISK_FREE_RATE, iv)
                    delta  = bs_delta(S, K, T, RISK_FREE_RATE, iv, right)
                    gamma  = bs_gamma(S, K, T, RISK_FREE_RATE, iv)
                    spread = max(0.01, 0.02 * max(mid, 0.10))

                    q.mid    = mid
                    q.spread = spread
                    q.delta  = delta
                    q.gamma  = gamma
                    if mid > 0:
                        q.price_history.append(mid)

            # ---- Check exits on all open positions ----------------------
            for key in list(positions.keys()):
                entry_price, entry_bar, entry_ts, order_size = positions[key]
                q = quote_history.get(key)
                if not q:
                    continue

                exit_reason = check_exit(entry_price, q)
                held_bars   = bar_idx - entry_bar

                if exit_reason is None and held_bars >= MAX_POSITION_HOLD_BARS:
                    exit_reason = "max_hold_time"

                if exit_reason:
                    K, right    = key
                    exit_price  = q.mid
                    pnl_per_sh  = exit_price - entry_price - 2 * SLIPPAGE
                    pnl_dollar  = round(pnl_per_sh * 100 * order_size, 2)
                    daily_pnl      += pnl_dollar
                    current_balance += pnl_dollar
                    trade_count    += 1
                    last_trade_bar  = bar_idx

                    trades.append({
                        "entry_time":   entry_ts,
                        "exit_time":    ts,
                        "right":        right,
                        "strike":       K,
                        "entry_option": round(entry_price, 2),
                        "exit_option":  round(exit_price,  2),
                        "pnl_dollar":   pnl_dollar,
                        "pnl_pct":      round(pnl_per_sh / entry_price * 100, 1)
                                        if entry_price > 0 else 0.0,
                        "exit_reason":  exit_reason,
                        "underlying":   round(S, 2),
                        "order_size":   order_size,
                    })
                    del positions[key]

            # ---- EOD force-close: no overnight positions ----------------
            is_last_bar = (
                bar_idx + 1 >= len(bars)
                or bars[bar_idx + 1][0].date() != today
            )
            if is_last_bar and positions:
                for key in list(positions.keys()):
                    entry_price, entry_bar, entry_ts, order_size = positions[key]
                    q          = quote_history.get(key)
                    K, right   = key
                    exit_price = q.mid if (q and q.mid > 0) else entry_price
                    pnl_per_sh = exit_price - entry_price - 2 * SLIPPAGE
                    pnl_dollar = round(pnl_per_sh * 100 * order_size, 2)
                    daily_pnl       += pnl_dollar
                    current_balance += pnl_dollar
                    trade_count     += 1
                    last_trade_bar   = bar_idx
                    trades.append({
                        "entry_time":   entry_ts,
                        "exit_time":    ts,
                        "right":        right,
                        "strike":       K,
                        "entry_option": round(entry_price, 2),
                        "exit_option":  round(exit_price,  2),
                        "pnl_dollar":   pnl_dollar,
                        "pnl_pct":      round(pnl_per_sh / entry_price * 100, 1)
                                        if entry_price > 0 else 0.0,
                        "exit_reason":  "eod_close",
                        "underlying":   round(S, 2),
                        "order_size":   order_size,
                    })
                    del positions[key]

            # ---- Risk gates (mirrors RiskManager.can_trade()) -----------
            open_count  = len(positions)
            cooldown_ok = (bar_idx - last_trade_bar) >= COOLDOWN_BARS

            can_trade = (
                not halted
                and not is_last_bar
                and ts.hour in TRADE_HOURS
                and open_count < MAX_POSITIONS
                and daily_pnl > -daily_loss_limit
                and trade_count < MAX_TRADES_PER_DAY
                and cooldown_ok
            )
            if daily_pnl <= -daily_loss_limit or trade_count >= MAX_TRADES_PER_DAY:
                halted = True

            # ---- Scan entries (mirrors bot._check_entries()) ------------
            if can_trade:
                candidates = []
                for K in strikes:
                    for right in rights:
                        key = (K, right)
                        if key in positions:
                            continue
                        q = quote_history.get(key)
                        if not q:
                            continue
                        strength = evaluate_entry(q)
                        if strength is not None and q.mid <= MAX_ENTRY_PRICE:
                            candidates.append((strength, key, q.mid))

                if candidates:
                    _, best_key, best_mid = max(candidates, key=lambda x: x[0])
                    entry_price = best_mid + SLIPPAGE
                    opt_cost    = max(entry_price, 0.50) * 100
                    order_size  = max(1, int(current_balance / MAX_POSITIONS / opt_cost))
                    positions[best_key] = (entry_price, bar_idx, ts, order_size)
                    last_trade_bar = bar_idx

        return trades

    @staticmethod
    def print_results(trades: list[dict], df: pd.DataFrame) -> None:
        if not trades:
            log.info("No completed trades in the backtest window.")
            return

        t = pd.DataFrame(trades)

        total   = len(t)
        winners = t[t["pnl_dollar"] > 0]
        losers  = t[t["pnl_dollar"] <= 0]
        w_rate  = len(winners) / total * 100
        total_pnl = t["pnl_dollar"].sum()
        avg_win   = winners["pnl_dollar"].mean() if len(winners) else 0.0
        avg_loss  = losers["pnl_dollar"].mean()  if len(losers)  else 0.0
        pf        = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
        cum_pnl   = t["pnl_dollar"].cumsum()
        max_dd    = (cum_pnl.cummax() - cum_pnl).max()
        t["date"] = pd.to_datetime(t["entry_time"]).dt.date
        daily_pnl = t.groupby("date")["pnl_dollar"].sum()
        sharpe    = (
            daily_pnl.mean() / daily_pnl.std() * np.sqrt(252)
            if daily_pnl.std() > 0 else 0.0
        )
        exit_counts = t["exit_reason"].value_counts()

        SEP = "=" * 62
        print(f"\n{SEP}")
        print("  BACKTEST  -  option_scalper strategy  -  SPY ATM Calls+Puts")
        print(SEP)
        print(f"  Data source    : IB TWS Paper ({HOST}:{PORT})")
        print(f"  Data period    : {t['entry_time'].min().date()} "
              f"to {t['exit_time'].max().date()}")
        print(f"  IB bars        : {len(df)}")
        print(f"  Signal         : Spread<=${SPREAD_THRESHOLD}  "
              f"Delta [{MIN_DELTA},{MAX_DELTA}]  Gamma>={MIN_GAMMA}  "
              f"Momentum>=${MOMENTUM_THRESHOLD} over {MOMENTUM_WINDOW} bars  "
              f"EntryPrice<=${MAX_ENTRY_PRICE}  Hours={sorted(TRADE_HOURS)}")
        print(f"  Exit           : TP +${PROFIT_TARGET}  "
              f"/ SL -${STOP_LOSS}  / hold>{MAX_POSITION_HOLD_BARS} bars")
        print(f"  Slippage       : ${SLIPPAGE}/side")
        print(f"  Starting bal.  : ${STARTING_BALANCE:>10,.2f}")
        print(f"  Daily loss cap : {MAX_DAILY_LOSS_PCT*100:.0f}% of equity")
        print(SEP)
        ending_balance = STARTING_BALANCE + total_pnl
        print(f"  Total trades   : {total}")
        print(f"  Win rate       : {w_rate:.1f}%  "
              f"({len(winners)} wins  /  {len(losers)} losses)")
        print(f"  Total P&L      : ${total_pnl:>10,.2f}")
        print(f"  Ending balance : ${ending_balance:>10,.2f}  "
              f"({(ending_balance/STARTING_BALANCE - 1)*100:+.0f}%)")
        print(f"  Avg win        : ${avg_win:>8,.2f}")
        print(f"  Avg loss       : ${avg_loss:>8,.2f}")
        print(f"  Profit factor  : {pf:.2f}")
        print(f"  Max drawdown   : ${max_dd:>8,.2f}")
        print(f"  Sharpe (ann.)  : {sharpe:.2f}")
        print(SEP)

        print("\n-- Exit Reasons --------------------------------------------------")
        for reason, count in exit_counts.items():
            print(f"  {reason:<20} {count:>4} trades")

        print("\n-- Calls vs Puts -------------------------------------------------")
        for right in ["C", "P"]:
            sub = t[t["right"] == right]
            if len(sub):
                label = "Calls" if right == "C" else "Puts "
                wr    = len(sub[sub["pnl_dollar"] > 0]) / len(sub) * 100
                print(f"  {label}  trades={len(sub):>3}  "
                      f"win%={wr:4.1f}  pnl=${sub['pnl_dollar'].sum():>8,.2f}")

        print("\n-- All Trades ----------------------------------------------------")
        cols = ["entry_time", "exit_time", "right", "strike",
                "entry_option", "exit_option", "pnl_dollar", "pnl_pct", "exit_reason"]
        print(
            t[cols].to_string(
                index=False,
                formatters={
                    "entry_time":  lambda x: str(x)[:16],
                    "exit_time":   lambda x: str(x)[:16],
                    "pnl_dollar":  lambda x: f"${x:,.2f}",
                    "pnl_pct":     lambda x: f"{x:.1f}%",
                },
            )
        )

        print("\n-- Daily P&L -----------------------------------------------------")
        for d, pnl in daily_pnl.items():
            bar  = "#" * int(abs(pnl) // 10)
            sign = "+" if pnl >= 0 else "-"
            print(f"  {d}  {sign}${abs(pnl):>7,.2f}  {bar}")

        out = "backtest_scalper_results.csv"
        t.to_csv(out, index=False)
        log.info(f"Trade log saved -> {out}")

    async def run(self):
        try:
            await self.connect()
            df     = await self.fetch_spy_bars(total_days=365)
            trades = self.simulate(df)
            self.print_results(trades, df)
        finally:
            if self.ib.isConnected():
                self.ib.disconnect()
                log.info("Disconnected from IB.")


if __name__ == "__main__":
    asyncio.run(ScalperBacktest().run())
