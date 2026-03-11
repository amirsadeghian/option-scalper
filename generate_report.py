"""
Generate a self-contained HTML backtest report from backtest_scalper_results.csv
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path

CSV            = Path(__file__).parent / "backtest_scalper_results.csv"
OUT            = Path(__file__).parent / "backtest_report.html"
STARTING_BAL   = 26_000.0

df = pd.read_csv(CSV)
df["date"] = df["entry_time"].str[:10]

# ── Summary stats ──────────────────────────────────────────────────────────────
total_trades   = len(df)
wins           = (df["pnl_dollar"] > 0).sum()
losses         = (df["pnl_dollar"] <= 0).sum()
win_rate       = wins / total_trades * 100
total_pnl      = df["pnl_dollar"].sum()
ending_balance = STARTING_BAL + total_pnl
pct_return     = (ending_balance / STARTING_BAL - 1) * 100
avg_win        = df.loc[df["pnl_dollar"] > 0,  "pnl_dollar"].mean()
avg_loss       = df.loc[df["pnl_dollar"] <= 0, "pnl_dollar"].mean()
gross_win      = df.loc[df["pnl_dollar"] > 0,  "pnl_dollar"].sum()
gross_loss     = abs(df.loc[df["pnl_dollar"] <= 0, "pnl_dollar"].sum())
profit_factor  = gross_win / gross_loss if gross_loss > 0 else float("inf")
cum_pnl        = df["pnl_dollar"].cumsum()
max_drawdown   = (cum_pnl.cummax() - cum_pnl).max()
best_trade     = df["pnl_dollar"].max()
worst_trade    = df["pnl_dollar"].min()

# ── Daily P&L ──────────────────────────────────────────────────────────────────
daily = df.groupby("date")["pnl_dollar"].sum().reset_index()
daily.columns = ["date", "pnl"]
daily = daily.sort_values("date").reset_index(drop=True)
daily["cum_pnl"] = daily["pnl"].cumsum() + STARTING_BAL

# ── Sharpe ─────────────────────────────────────────────────────────────────────
sharpe = (daily["pnl"].mean() / daily["pnl"].std() * np.sqrt(252)
          if daily["pnl"].std() > 0 else 0.0)

# ── Exit reasons ───────────────────────────────────────────────────────────────
exits = df["exit_reason"].value_counts().to_dict()

# ── Calls vs Puts ──────────────────────────────────────────────────────────────
side = df.groupby("right").agg(
    trades=("pnl_dollar", "count"),
    wins=("pnl_dollar", lambda x: (x > 0).sum()),
    pnl=("pnl_dollar", "sum"),
).reset_index()
side["win_pct"] = side["wins"] / side["trades"] * 100

# ── P&L histogram ──────────────────────────────────────────────────────────────
bin_min = int(df["pnl_dollar"].min()) - 1000
bin_max = int(df["pnl_dollar"].max()) + 2000
step    = max(500, (bin_max - bin_min) // 40)
bins    = list(range(bin_min, bin_max, step))
counts, edges = np.histogram(df["pnl_dollar"], bins=bins)
hist_labels = [f"{int(e):,}" for e in edges[:-1]]
hist_counts = counts.tolist()

# ── Running balance series ─────────────────────────────────────────────────────
balance_series = (STARTING_BAL + cum_pnl).tolist()

# ── Trade table rows ───────────────────────────────────────────────────────────
def row_html(r):
    pnl_cls    = "pos" if r["pnl_dollar"] > 0 else "neg"
    reason_cls = {"profit_target": "pt", "stop_loss": "sl",
                  "max_hold_time": "mht", "eod_close": "eod"}.get(r["exit_reason"], "")
    has_size   = "order_size" in r and not pd.isna(r.get("order_size", float("nan")))
    size_td    = f'<td class="num">{int(r["order_size"])}</td>' if has_size else "<td>—</td>"
    return (
        f'<tr>'
        f'<td>{str(r["entry_time"])[:16]}</td>'
        f'<td>{str(r["exit_time"])[:16]}</td>'
        f'<td class="center">{r["right"]}</td>'
        f'<td class="center">{r["strike"]:.0f}</td>'
        f'<td class="num">${r["entry_option"]:.2f}</td>'
        f'<td class="num">${r["exit_option"]:.2f}</td>'
        f'{size_td}'
        f'<td class="num {pnl_cls}">${r["pnl_dollar"]:+,.2f}</td>'
        f'<td class="num {pnl_cls}">{r["pnl_pct"]:+.1f}%</td>'
        f'<td class="center"><span class="badge {reason_cls}">{r["exit_reason"]}</span></td>'
        f'<td class="num">{r["underlying"]:.2f}</td>'
        f'</tr>'
    )

trade_rows = "\n".join(row_html(r) for _, r in df.iterrows())

# ── Chart data ─────────────────────────────────────────────────────────────────
daily_labels   = json.dumps(daily["date"].tolist())
daily_pnl_data = json.dumps([round(v, 2) for v in daily["pnl"].tolist()])
daily_bal_data = json.dumps([round(v, 2) for v in daily["cum_pnl"].tolist()])
trade_labels   = json.dumps(list(range(1, len(df) + 1)))
balance_data   = json.dumps([round(v, 2) for v in balance_series])
sides_labels   = json.dumps(side["right"].tolist())
sides_pnl      = json.dumps([round(v, 2) for v in side["pnl"].tolist()])
sides_winpct   = json.dumps([round(v, 1) for v in side["win_pct"].tolist()])

data_start = df["date"].min()
data_end   = df["date"].max()

best_day_row  = daily.loc[daily["pnl"].idxmax()]
worst_day_row = daily.loc[daily["pnl"].idxmin()]

best10_rows  = "".join(
    f'<tr><td>{r["date"]}</td><td class="pos">${r["pnl"]:+,.2f}</td></tr>'
    for _, r in daily.nlargest(10, "pnl").iterrows())
worst10_rows = "".join(
    f'<tr><td>{r["date"]}</td><td class="neg">${r["pnl"]:+,.2f}</td></tr>'
    for _, r in daily.nsmallest(10, "pnl").iterrows())
exit_rows = "".join(
    f'<tr><td>{k}</td><td>{v} &nbsp;<span style="color:#64748b">({v/total_trades*100:.1f}%)</span></td></tr>'
    for k, v in exits.items())
side_rows = "".join(
    f'<tr><td>{"Calls" if r["right"]=="C" else "Puts"}</td>'
    f'<td>{r["trades"]} trades · {r["win_pct"]:.1f}% win · '
    f'<span class="{"pos" if r["pnl"]>=0 else "neg"}">${r["pnl"]:+,.2f}</span></td></tr>'
    for _, r in side.iterrows())

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Backtest Report — SPY Option Scalper</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #0f1117; color: #e2e8f0; font-size: 14px; }}
h1   {{ font-size: 22px; font-weight: 700; }}
h2   {{ font-size: 12px; font-weight: 600; color: #94a3b8; text-transform: uppercase;
        letter-spacing: .07em; margin-bottom: 14px; }}
a    {{ color: #60a5fa; }}
.page {{ max-width: 1440px; margin: 0 auto; padding: 28px 20px; }}
.header   {{ margin-bottom: 26px; }}
.header p {{ color: #64748b; margin-top: 5px; font-size: 12.5px; line-height: 1.6; }}
.tag {{ display: inline-block; background: #1e3a5f; color: #60a5fa;
        padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-right: 4px; }}

/* KPI */
.kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr));
             gap: 12px; margin-bottom: 26px; }}
.kpi {{ background: #1a1f2e; border: 1px solid #252c40; border-radius: 10px; padding: 16px 18px; }}
.kpi .label {{ font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing:.05em; }}
.kpi .value {{ font-size: 22px; font-weight: 700; margin-top: 5px; line-height: 1.1; }}
.kpi .sub   {{ font-size: 11px; color: #64748b; margin-top: 3px; }}
.pos {{ color: #34d399; }}
.neg {{ color: #f87171; }}
.neu {{ color: #e2e8f0; }}
.hi  {{ color: #38bdf8; }}

/* Charts */
.chart-row  {{ display: grid; gap: 14px; margin-bottom: 14px; }}
.cols-1     {{ grid-template-columns: 1fr; }}
.cols-2     {{ grid-template-columns: 1fr 1fr; }}
.cols-3     {{ grid-template-columns: 2fr 1fr; }}
.card {{ background: #1a1f2e; border: 1px solid #252c40; border-radius: 10px; padding: 20px; }}
.card canvas {{ max-height: 300px; }}

/* Stat tables */
.stat-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }}
.stat-table {{ width: 100%; border-collapse: collapse; }}
.stat-table td {{ padding: 8px 10px; border-bottom: 1px solid #1e2535; font-size: 13px; }}
.stat-table td:last-child {{ text-align: right; font-weight: 600; }}
.stat-table tr:last-child td {{ border-bottom: none; }}

/* Trade table */
.table-wrap {{ overflow-x: auto; max-height: 600px; overflow-y: auto; }}
table.trades {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
table.trades thead {{ position: sticky; top: 0; z-index: 1; }}
table.trades thead tr {{ background: #141824; }}
table.trades th {{ padding: 9px 10px; text-align: left; color: #64748b;
                   font-weight: 600; text-transform: uppercase; font-size: 10.5px;
                   border-bottom: 1px solid #252c40; white-space: nowrap; }}
table.trades td {{ padding: 6px 10px; border-bottom: 1px solid #181e2d; white-space: nowrap; }}
table.trades tr:hover td {{ background: #1e2840; }}
.num    {{ text-align: right; font-variant-numeric: tabular-nums; }}
.center {{ text-align: center; }}
.badge {{ display: inline-block; padding: 2px 7px; border-radius: 4px;
          font-size: 10.5px; font-weight: 600; }}
.badge.pt  {{ background: #14532d; color: #4ade80; }}
.badge.sl  {{ background: #450a0a; color: #f87171; }}
.badge.mht {{ background: #1e3a5f; color: #60a5fa; }}
.badge.eod {{ background: #3b2a0a; color: #fbbf24; }}

@media (max-width: 900px) {{
  .cols-2, .cols-3, .stat-row {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="page">

<div class="header">
  <h1>SPY ATM Option Scalper &mdash; Backtest Report</h1>
  <p>
    <span class="tag">Period: {data_start} → {data_end}</span>
    <span class="tag">5-min bars · IB TWS Paper</span>
    <span class="tag">Signal: Spread≤$0.05 · Delta [0.2,0.7] · Gamma≥0.03 · Mom≥$0.03/10 bars</span>
    <span class="tag">Exit: TP +$0.10 / SL -$0.08 / hold>24 bars</span>
    <span class="tag">Start: $26,000 · Daily cap: 10% equity · EOD close</span>
  </p>
</div>

<!-- KPIs -->
<div class="kpi-grid">
  <div class="kpi">
    <div class="label">Starting Balance</div>
    <div class="value neu">${STARTING_BAL:,.0f}</div>
  </div>
  <div class="kpi">
    <div class="label">Ending Balance</div>
    <div class="value pos">${ending_balance:,.2f}</div>
    <div class="sub">{pct_return:+.0f}% return</div>
  </div>
  <div class="kpi">
    <div class="label">Total P&amp;L</div>
    <div class="value {'pos' if total_pnl >= 0 else 'neg'}">${total_pnl:+,.2f}</div>
  </div>
  <div class="kpi">
    <div class="label">Total Trades</div>
    <div class="value neu">{total_trades}</div>
  </div>
  <div class="kpi">
    <div class="label">Win Rate</div>
    <div class="value {'pos' if win_rate >= 50 else 'neu'}">{win_rate:.1f}%</div>
    <div class="sub">{wins}W / {losses}L</div>
  </div>
  <div class="kpi">
    <div class="label">Profit Factor</div>
    <div class="value {'pos' if profit_factor >= 1.5 else 'neu'}">{profit_factor:.2f}</div>
  </div>
  <div class="kpi">
    <div class="label">Sharpe (ann.)</div>
    <div class="value hi">{sharpe:.2f}</div>
  </div>
  <div class="kpi">
    <div class="label">Max Drawdown</div>
    <div class="value neg">-${max_drawdown:,.2f}</div>
  </div>
  <div class="kpi">
    <div class="label">Avg Win</div>
    <div class="value pos">${avg_win:,.2f}</div>
  </div>
  <div class="kpi">
    <div class="label">Avg Loss</div>
    <div class="value neg">-${abs(avg_loss):,.2f}</div>
  </div>
  <div class="kpi">
    <div class="label">Best Trade</div>
    <div class="value pos">${best_trade:+,.2f}</div>
  </div>
  <div class="kpi">
    <div class="label">Worst Trade</div>
    <div class="value neg">${worst_trade:,.2f}</div>
  </div>
</div>

<!-- Running balance (per trade) -->
<div class="chart-row cols-1" style="margin-bottom:14px;">
  <div class="card">
    <h2>Account Balance (per trade)</h2>
    <canvas id="balChart"></canvas>
  </div>
</div>

<!-- Daily charts -->
<div class="chart-row cols-2">
  <div class="card">
    <h2>Daily P&amp;L</h2>
    <canvas id="dailyChart"></canvas>
  </div>
  <div class="card">
    <h2>Account Balance (daily close)</h2>
    <canvas id="balDailyChart"></canvas>
  </div>
</div>

<!-- Histogram + calls/puts -->
<div class="chart-row cols-2">
  <div class="card">
    <h2>P&amp;L Distribution per Trade</h2>
    <canvas id="histChart"></canvas>
  </div>
  <div class="card">
    <h2>Calls vs Puts</h2>
    <canvas id="sidesChart"></canvas>
  </div>
</div>

<!-- stats row -->
<div class="stat-row">
  <div class="card">
    <h2>Exit Breakdown</h2>
    <table class="stat-table">{exit_rows}</table>
  </div>
  <div class="card">
    <h2>Calls vs Puts Detail</h2>
    <table class="stat-table">{side_rows}</table>
  </div>
</div>

<div class="stat-row">
  <div class="card">
    <h2>Best 10 Days</h2>
    <table class="stat-table">{best10_rows}</table>
  </div>
  <div class="card">
    <h2>Worst 10 Days</h2>
    <table class="stat-table">{worst10_rows}</table>
  </div>
</div>

<!-- Full trade log -->
<div class="card" style="margin-bottom:0;">
  <h2>All Trades ({total_trades} total)</h2>
  <div class="table-wrap">
    <table class="trades">
      <thead><tr>
        <th>Entry</th><th>Exit</th><th>Side</th><th>Strike</th>
        <th>Entry $</th><th>Exit $</th><th>Contracts</th>
        <th>P&amp;L $</th><th>P&amp;L %</th><th>Reason</th><th>Underlying</th>
      </tr></thead>
      <tbody>{trade_rows}</tbody>
    </table>
  </div>
</div>

</div><!-- /page -->
<script>
const G = {{ color: 'rgba(255,255,255,0.04)' }};
const F = {{ color: '#64748b', size: 11 }};
const fmt$ = v => '$' + v.toLocaleString('en-US', {{maximumFractionDigits:0}});

// Balance per trade
new Chart(document.getElementById('balChart'), {{
  type: 'line',
  data: {{
    labels: {trade_labels},
    datasets: [{{
      label: 'Account Balance ($)',
      data: {balance_data},
      borderColor: '#38bdf8',
      backgroundColor: 'rgba(56,189,248,0.06)',
      fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.05,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
    scales: {{
      x: {{ grid: G, ticks: {{ ...F, maxTicksLimit: 20 }} }},
      y: {{ grid: G, ticks: {{ ...F, callback: fmt$ }} }}
    }}
  }}
}});

// Daily P&L bar
const dp = {daily_pnl_data};
new Chart(document.getElementById('dailyChart'), {{
  type: 'bar',
  data: {{
    labels: {daily_labels},
    datasets: [{{
      label: 'Daily P&L ($)',
      data: dp,
      backgroundColor: dp.map(v => v >= 0 ? 'rgba(52,211,153,0.75)' : 'rgba(248,113,113,0.75)'),
      borderRadius: 3,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: G, ticks: F }},
      y: {{ grid: G, ticks: {{ ...F, callback: fmt$ }} }}
    }}
  }}
}});

// Daily balance
new Chart(document.getElementById('balDailyChart'), {{
  type: 'line',
  data: {{
    labels: {daily_labels},
    datasets: [{{
      label: 'Balance ($)',
      data: {daily_bal_data},
      borderColor: '#a78bfa',
      backgroundColor: 'rgba(167,139,250,0.07)',
      fill: true, borderWidth: 2, pointRadius: 4,
      pointBackgroundColor: '#a78bfa', tension: 0.35,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
    scales: {{
      x: {{ grid: G, ticks: F }},
      y: {{ grid: G, ticks: {{ ...F, callback: fmt$ }} }}
    }}
  }}
}});

// Histogram
const hc = {json.dumps(hist_counts)};
const hl = {json.dumps(hist_labels)};
new Chart(document.getElementById('histChart'), {{
  type: 'bar',
  data: {{
    labels: hl,
    datasets: [{{
      label: 'Trade count',
      data: hc,
      backgroundColor: hl.map(v => +v.replace(/,/g,'') >= 0
        ? 'rgba(52,211,153,0.7)' : 'rgba(248,113,113,0.7)'),
      borderRadius: 2,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: G, ticks: {{ ...F, maxTicksLimit: 16 }} }},
      y: {{ grid: G, ticks: F }}
    }}
  }}
}});

// Calls vs Puts
new Chart(document.getElementById('sidesChart'), {{
  type: 'bar',
  data: {{
    labels: {sides_labels},
    datasets: [
      {{
        label: 'Total P&L ($)',
        data: {sides_pnl},
        backgroundColor: ['rgba(56,189,248,0.7)', 'rgba(167,139,250,0.7)'],
        borderRadius: 5, yAxisID: 'y',
      }},
      {{
        label: 'Win Rate (%)',
        data: {sides_winpct},
        type: 'line',
        borderColor: '#f59e0b',
        backgroundColor: 'transparent',
        borderWidth: 2, pointRadius: 6,
        pointBackgroundColor: '#f59e0b', yAxisID: 'y2',
      }}
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
    scales: {{
      x: {{ grid: G, ticks: F }},
      y: {{ grid: G, ticks: {{ ...F, callback: fmt$ }} }},
      y2: {{ position: 'right', grid: {{ display: false }},
             ticks: {{ ...F, callback: v => v + '%' }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""

OUT.write_text(html, encoding="utf-8")
print(f"Report written to: {OUT}")
