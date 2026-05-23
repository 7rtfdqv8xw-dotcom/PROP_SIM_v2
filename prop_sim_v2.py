#!/usr/bin/env python3
"""
BOS + FVG / IFVG Trend Reentry – Prop Firm Simulator
Trend : 5m BOS + 15m BOS alignment (minimum swing size filter)
Entry : cleanest FVG (first touch) or IFVG, 5m primary / 15m secondary
Confirmation: FVG size ≥ threshold  +  stable trend  +  entry-candle direction
RR    : 1 : 0.75  (Stop 16 pts / Target 12 pts, 2 NQ mini contracts)
"""
import pandas as pd
import numpy as np
from collections import defaultdict
import warnings
warnings.filterwarnings("ignore")

# ════════════════════════════════════════════════════════════════
# PARAMETERS
# ════════════════════════════════════════════════════════════════
START_DATE    = "2021-05-19"
EUR_USD       = 1.08

ACCOUNT_COST  = 90
ACCOUNT_SIZE  = 50_000
MAX_ACCOUNTS  = 5
CAPITAL       = 7_000

EVAL_TARGET   = 3_000
EVAL_MAX_DD   = 2_000
EVAL_DAY_CAP  = 1_500
EVAL_MIN_DAYS = 2

EXP_FLOOR     = 48_000
EXP_WIN_MIN   = 150
EXP_WIN_DAYS  = 5
EXP_MAX_PAY   = 2_000
EXP_MIN_PAY   = 500 / EUR_USD

STOP_PTS  = 16.0
TGT_PTS   = 12.0
CONTRACTS = 4
PV        = 20.0
RISK      = STOP_PTS * PV * CONTRACTS   # $1,280
REWARD    = TGT_PTS  * PV * CONTRACTS   # $960
COMMISSION = 16.0   # $ per round-trip, 4 contracts (~$4/contract incl. fees)
PAYOUT_SPLIT = 0.90 # 90% to trader
CONSISTENCY_CAP = 0.50  # best single day ≤ 50% of total profit (informational only, not a blocker)
SLIP_PTS = 0.5      # stop-loss slippage in points (stop-market fills 0.5pt beyond stop)

# BOS
SWING_N5      = 3      # bars each side for 5m swing detection
SWING_N15     = 2      # bars each side for 15m swing detection
MIN_SWING5    = 15.0   # minimum swing range in pts (5m) – filters choppy structure
MIN_SWING15   = 20.0   # minimum swing range in pts (15m)
TREND_STABLE  = 6      # require aligned trend for this many consecutive bars before entry

# FVG
FVG_MIN5      = 5.0    # minimum FVG gap size pts (5m)
FVG_MIN15     = 8.0    # minimum FVG gap size pts (15m)
FVG_LB5       = 12     # max bars back for 5m FVG (60 min)
FVG_LB15      = 40     # max bars back for 15m FVG mapped to 5m bars

MAX_BARS      = 36     # max bars to hold trade on 5m = 3 h

# ════════════════════════════════════════════════════════════════
# 1. LOAD & RESAMPLE
# ════════════════════════════════════════════════════════════════
print("Loading 1m NQ data …")
df1 = pd.read_csv(
    str(__import__("pathlib").Path(__file__).parent / "nq-1m.csv"),
    sep=";", header=None,
    names=["date","time","open","high","low","close","volume"]
)
df1["dt"] = pd.to_datetime(df1["date"] + " " + df1["time"], format="%d/%m/%Y %H:%M:%S")
df1 = df1[df1["dt"] >= START_DATE].set_index("dt")
df1 = df1[["open","high","low","close","volume"]].astype(float)

def resamp(df, p):
    return df.resample(p).agg(
        open=("open","first"), high=("high","max"),
        low=("low","min"),   close=("close","last"),
        volume=("volume","sum")
    ).dropna(subset=["open"])

df5  = resamp(df1, "5min")
df15 = resamp(df1, "15min")
print(f"  5m: {len(df5):,} bars  |  15m: {len(df15):,} bars")

# ════════════════════════════════════════════════════════════════
# 2. BOS TREND  (significant-swing filter)
# ════════════════════════════════════════════════════════════════
def bos_trend(df, n, min_swing):
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    N = len(df)
    trend = np.zeros(N, dtype=np.int8)
    cur = 0
    sh, sl = [], []

    for i in range(2*n, N):
        ci = i - n
        window_h = h[ci-n:ci+n+1]
        window_l = l[ci-n:ci+n+1]
        swing_rng = window_h.max() - window_l.min()

        # Only register significant swings
        if swing_rng >= min_swing:
            if h[ci] == window_h.max():
                sh = (sh + [h[ci]])[-4:]
            if l[ci] == window_l.min():
                sl = (sl + [l[ci]])[-4:]

        if sh and c[i] > sh[-1]:
            cur = 1;  sh = [];  sl = []
        elif sl and c[i] < sl[-1]:
            cur = -1; sh = [];  sl = []
        trend[i] = cur

    return pd.Series(trend, index=df.index)

print("Computing BOS trends …")
t5  = bos_trend(df5,  SWING_N5,  MIN_SWING5)
t15 = bos_trend(df15, SWING_N15, MIN_SWING15)
df5["trend5"]  = t5
df5["trend15"] = t15.shift(1).reindex(df5.index, method="ffill").fillna(0).astype(int)
df5["aligned"] = np.where(
    (df5["trend5"] == df5["trend15"]) & (df5["trend5"] != 0),
    df5["trend5"], 0
)

# Stable-trend flag: aligned for at least TREND_STABLE consecutive bars
aligned_arr = df5["aligned"].values
stable = np.zeros(len(df5), dtype=bool)
cnt = 0
for i in range(len(df5)):
    if aligned_arr[i] != 0:
        cnt += 1
    else:
        cnt = 0
    stable[i] = (cnt >= TREND_STABLE)
df5["stable"] = stable

print(f"  Bull aligned+stable: {((df5['aligned']==1)&df5['stable']).sum():,} bars  "
      f"Bear: {((df5['aligned']==-1)&df5['stable']).sum():,} bars")

# ════════════════════════════════════════════════════════════════
# 3. FVG DETECTION
# ════════════════════════════════════════════════════════════════
def find_fvgs(df, min_size):
    h = df["high"].values
    l = df["low"].values
    idx = df.index
    out = []
    for i in range(2, len(df)):
        bs = l[i] - h[i-2]       # bullish gap: low[i] > high[i-2]
        if bs >= min_size:
            out.append([i, idx[i], 1, float(h[i-2]), float(l[i]), float(bs), False])
        bs2 = l[i-2] - h[i]      # bearish gap: high[i] < low[i-2]
        if bs2 >= min_size:
            out.append([i, idx[i], -1, float(h[i]), float(l[i-2]), float(bs2), False])
    return out

print("Detecting FVGs …")
fvg5  = find_fvgs(df5,  FVG_MIN5)
fvg15 = find_fvgs(df15, FVG_MIN15)

df5_ts = df5.index
def ts2bar5(ts):
    return min(df5_ts.searchsorted(ts), len(df5_ts)-1)

fvg15_mapped = []
for f in fvg15:
    fc = f[:]
    fc[0] = ts2bar5(f[1])
    fvg15_mapped.append(fc)

fvg5_at  = defaultdict(list)
fvg15_at = defaultdict(list)
for f in fvg5:
    fvg5_at[f[0]+1].append(f[:])   # +1: FVG erst ab dem Bar NACH seiner Entstehung verfügbar
for f in fvg15_mapped:
    fvg15_at[f[0]+1].append(f[:])

print(f"  5m FVGs: {len(fvg5):,}   15m FVGs: {len(fvg15):,}")

# ════════════════════════════════════════════════════════════════
# 4. SESSION FILTER  (CT: 08:35–10:30 and 12:30–14:00)
# ════════════════════════════════════════════════════════════════
def session(ts):
    t = ts.hour * 60 + ts.minute
    if 8*60+35 <= t < 10*60+30: return 1
    if 12*60+30 <= t < 14*60:   return 2
    return 0

# ════════════════════════════════════════════════════════════════
# 5. SIGNAL GENERATION  (max 1 per session)
# ════════════════════════════════════════════════════════════════
print("Generating signals …")

aligned_v = df5["aligned"].values
stable_v  = df5["stable"].values
bars5     = list(df5.itertuples())
N5        = len(bars5)

signals      = []
sig_sessions = set()
active5  = []
active15 = []

for i in range(max(FVG_LB5, FVG_LB15, SWING_N5*2+TREND_STABLE+5), N5):
    b  = bars5[i]
    ts = b.Index

    # ── Ingest new FVGs ──────────────────────────────────────
    for f in fvg5_at.get(i, []):
        active5.append(f[:])
    for f in fvg15_at.get(i, []):
        active15.append(f[:])

    # ── Prune stale FVGs ─────────────────────────────────────
    active5  = [f for f in active5  if i - f[0] <= FVG_LB5]
    active15 = [f for f in active15 if i - f[0] <= FVG_LB15]

    # ── Session / trend / stability check ────────────────────
    sess = session(ts)
    if sess == 0:
        # Still update touched flags outside session
        for fvg in active5 + active15:
            if not fvg[6] and b.low <= fvg[4] and b.high >= fvg[3]:
                fvg[6] = True
        continue

    tr = int(aligned_v[i])
    if tr == 0 or not stable_v[i]:
        for fvg in active5 + active15:
            if not fvg[6] and b.low <= fvg[4] and b.high >= fvg[3]:
                fvg[6] = True
        continue

    sess_key = (ts.date(), sess)
    if sess_key in sig_sessions:
        for fvg in active5 + active15:
            if not fvg[6] and b.low <= fvg[4] and b.high >= fvg[3]:
                fvg[6] = True
        continue

    # ── Find candidates BEFORE updating touched flags ────────
    candidates = []

    for fvg in active5:
        bot, top, size = fvg[3], fvg[4], fvg[5]
        was_touched = fvg[6]
        if b.low > top or b.high < bot:
            continue                              # not in zone this bar

        if was_touched and fvg[2] == -tr:
            if tr == 1 and b.close > (b.low + b.high) / 2:
                candidates.append((fvg, "5m-IFVG"))
            elif tr == -1 and b.close < (b.low + b.high) / 2:
                candidates.append((fvg, "5m-IFVG"))

    for fvg in active15:
        bot, top, size = fvg[3], fvg[4], fvg[5]
        was_touched = fvg[6]
        if b.low > top or b.high < bot:
            continue

        if was_touched and fvg[2] == -tr:
            if tr == 1 and b.close > (b.low + b.high) / 2:
                candidates.append((fvg, "15m-IFVG"))
            elif tr == -1 and b.close < (b.low + b.high) / 2:
                candidates.append((fvg, "15m-IFVG"))

    # ── NOW update touched flags ──────────────────────────────
    for fvg in active5 + active15:
        if not fvg[6] and b.low <= fvg[4] and b.high >= fvg[3]:
            fvg[6] = True

    if not candidates:
        continue

    # Take first valid IFVG (chronologically oldest) – no score ranking
    best, fvg_tf = candidates[0]

    fvg_mid = (best[3] + best[4]) / 2.0
    if tr == 1:
        entry  = fvg_mid
        stop   = entry - STOP_PTS
        target = entry + TGT_PTS
    else:
        entry  = fvg_mid
        stop   = entry + STOP_PTS
        target = entry - TGT_PTS

    best[6] = True  # mark as used

    signals.append({
        "ts"      : ts,
        "date"    : ts.date(),
        "bar_i"   : i,
        "sess"    : sess,
        "trend"   : tr,
        "fvg_tf"  : fvg_tf,
        "fvg_size": round(best[5], 2),
        "entry"   : round(entry, 2),
        "stop"    : round(stop, 2),
        "target"  : round(target, 2),
        "fvg_bot" : round(best[3], 2),
        "fvg_top" : round(best[4], 2),
    })
    sig_sessions.add(sess_key)

sigs = pd.DataFrame(signals)
if sigs.empty:
    print("No signals generated.")
    exit()
print(f"  Signals: {len(sigs):,}  "
      f"(long {(sigs['trend']==1).sum():,}  short {(sigs['trend']==-1).sum():,}  "
      f"FVG {sigs['fvg_tf'].str.contains('FVG').sum():,}  "
      f"IFVG {sigs['fvg_tf'].str.contains('IFVG').sum():,})")

# ════════════════════════════════════════════════════════════════
# 6. TRADE OUTCOMES  (5m bar resolution)
# ════════════════════════════════════════════════════════════════
print("Simulating outcomes …")
outcomes = []
for _, sig in sigs.iterrows():
    i0       = int(sig["bar_i"]) + 1
    i_end    = min(i0 + MAX_BARS, N5)
    entry    = sig["entry"]
    stop     = sig["stop"]
    target   = sig["target"]
    tr       = int(sig["trend"])
    sig_date = sig["date"]
    fvg_bot  = sig["fvg_bot"]
    fvg_top  = sig["fvg_top"]

    fill_price = None
    last_close = None
    exit_type  = None   # "win" | "loss" | "zone_fail" | None = timeout/unfilled

    for j in range(i0, i_end):
        fb = bars5[j]
        if fb.Index.date() != sig_date or session(fb.Index) == 0:
            break
        last_close = fb.close

        if fill_price is None:
            if tr == 1:
                if fb.open < fvg_bot:          # zone broken below → cancel
                    exit_type = "zone_fail"; break
                if fb.open <= entry:
                    fill_price = fb.open       # gap fill at open (within zone)
                elif fb.low <= entry:
                    fill_price = entry         # limit touched intrabar
            else:
                if fb.open > fvg_top:          # zone broken above → cancel
                    exit_type = "zone_fail"; break
                if fb.open >= entry:
                    fill_price = fb.open
                elif fb.high >= entry:
                    fill_price = entry
            if fill_price is None:
                continue

        if tr == 1:
            if fb.low  <= stop:   exit_type = "loss"; break
            if fb.high >= target: exit_type = "win";  break
        else:
            if fb.high >= stop:   exit_type = "loss"; break
            if fb.low  <= target: exit_type = "win";  break

    # zone broken or never filled → no trade
    if exit_type == "zone_fail" or fill_price is None:
        continue

    # timeout with open position → close at session end (realistic market-close exit)
    if exit_type is None:
        if last_close is None: continue
        exit_price = last_close
        result     = "exit_close"
    elif exit_type == "win":
        exit_price = target;  result = "win"
    else:
        exit_price = stop - SLIP_PTS * tr; result = "loss"

    if tr == 1:
        raw = (exit_price - fill_price) * PV * CONTRACTS
    else:
        raw = (fill_price - exit_price) * PV * CONTRACTS
    pnl = raw - COMMISSION
    outcomes.append({**sig.to_dict(), "result": result, "pnl": pnl})

trades = pd.DataFrame(outcomes)
if trades.empty:
    print("No resolved trades.")
    exit()

wins   = (trades["result"] == "win").sum()
losses = (trades["result"] == "loss").sum()
exits  = (trades["result"] == "exit_close").sum()
total  = len(trades)
wr     = wins / total
avg_pnl = trades["pnl"].mean()
pos_pnl = trades[trades["pnl"] > 0]["pnl"].sum()
neg_pnl = abs(trades[trades["pnl"] < 0]["pnl"].sum())
pf      = pos_pnl / neg_pnl if neg_pnl > 0 else float("inf")

# ════════════════════════════════════════════════════════════════
# 7. PROP FIRM SIMULATION
# ════════════════════════════════════════════════════════════════
trades_by_date = {d: g.to_dict("records") for d, g in trades.groupby("date")}
all_dates      = sorted(trades_by_date.keys())

capital_eur = CAPITAL
accounts    = []
next_id     = 0
st = dict(bought=0, evals_pass=0, evals_fail=0,
          exp_term=0, payouts=0, payout_usd=0.0)

def new_acc():
    global capital_eur, next_id
    if capital_eur < ACCOUNT_COST: return None
    capital_eur -= ACCOUNT_COST
    next_id += 1
    st["bought"] += 1
    return dict(id=next_id, phase="eval", balance=0.0,
                days=set(), day_pnl={}, alive=True,
                peak_eod=float(ACCOUNT_SIZE),          # for EOD trailing drawdown
                exp_win_days=0, exp_win_dates=set(), exp_base=0.0)

for _ in range(MAX_ACCOUNTS):
    a = new_acc()
    if a: accounts.append(a)

for trade_date in all_dates:
    day_trades = trades_by_date[trade_date]
    for acc in accounts:
        if not acc["alive"]: continue
        # Floor frozen at START of day from previous EOD peak (true EOD trailing DD)
        dd_floor = acc["peak_eod"] - EVAL_MAX_DD
        dpnl = acc["day_pnl"].get(trade_date, 0.0)
        for tr in day_trades:
            if acc["phase"] == "eval":
                if dpnl >= EVAL_DAY_CAP: break
                credit = min(tr["pnl"], EVAL_DAY_CAP - dpnl) if tr["result"] == "win" else tr["pnl"]
            else:
                credit = tr["pnl"]
            dpnl           += credit
            acc["balance"] += credit
            acc["days"].add(trade_date)
            # Eval pass can trigger intraday (hitting target passes immediately)
            if acc["phase"] == "eval":
                if acc["balance"] >= EVAL_TARGET and len(acc["days"]) >= EVAL_MIN_DAYS:
                    acc["phase"] = "express"; acc["exp_base"] = acc["balance"]
                    st["evals_pass"] += 1; break
        acc["day_pnl"][trade_date] = dpnl
        # EOD breach check – only end-of-day balance matters (true EOD trailing DD)
        eod_equity = ACCOUNT_SIZE + acc["balance"]
        if acc["phase"] == "eval" and eod_equity <= dd_floor:
            acc["alive"] = False; st["evals_fail"] += 1
        elif acc["phase"] == "express" and eod_equity < dd_floor:
            acc["alive"] = False; st["exp_term"] += 1
        # Update peak for NEXT day only if still alive
        if acc["alive"] and eod_equity > acc["peak_eod"]:
            acc["peak_eod"] = eod_equity
        if acc["phase"] == "express" and acc["alive"]:
            if dpnl >= EXP_WIN_MIN and trade_date not in acc["exp_win_dates"]:
                acc["exp_win_dates"].add(trade_date); acc["exp_win_days"] += 1
            if acc["exp_win_days"] >= EXP_WIN_DAYS:
                profit = acc["balance"] - acc["exp_base"]
                # Withdraw 50% of profit, capped at $2K, then apply split
                payout = max(0.0, min(profit * 0.5, EXP_MAX_PAY)) * PAYOUT_SPLIT
                # Require min $500 payout and min $1K balance – no consistency block
                min_bal_ok = acc["balance"] >= 1_000
                if payout >= EXP_MIN_PAY and min_bal_ok:
                    st["payouts"]   += 1; st["payout_usd"] += payout
                    capital_eur     += payout / EUR_USD
                    acc["exp_win_days"] = 0; acc["exp_win_dates"] = set()
                    acc["exp_base"] = acc["balance"]
                    # Payout resets trailing drawdown to starting balance + $100
                    acc["peak_eod"] = float(ACCOUNT_SIZE) + 100
    accounts = [a for a in accounts if a["alive"]]
    while len(accounts) < MAX_ACCOUNTS and capital_eur >= ACCOUNT_COST:
        a = new_acc()
        if a: accounts.append(a)

# ════════════════════════════════════════════════════════════════
# 8. FINAL REPORT
# ════════════════════════════════════════════════════════════════
alive_eval = sum(1 for a in accounts if a["phase"] == "eval")
alive_exp  = sum(1 for a in accounts if a["phase"] == "express")
total_spent    = st["bought"] * ACCOUNT_COST
total_recv_eur = st["payout_usd"] / EUR_USD
net_profit     = capital_eur - CAPITAL
roi_5y         = net_profit / CAPITAL * 100
roi_ann        = roi_5y / 5

eq     = trades["pnl"].cumsum()
max_dd = (eq - eq.cummax()).min()
breakeven_wr = 1 / (1 + TGT_PTS / STOP_PTS)

trades["month"] = pd.to_datetime(trades["date"]).dt.to_period("M")
monthly = trades.groupby("month").agg(
    n=("pnl","count"),
    wins=("result", lambda x: (x=="win").sum()),
    pnl=("pnl","sum")
)
monthly["wr"] = monthly["wins"] / monthly["n"]
best3  = monthly["pnl"].nlargest(3)
worst3 = monthly["pnl"].nsmallest(3)

trades["year"] = pd.to_datetime(trades["date"]).dt.year
yearly = trades.groupby("year").agg(
    n=("pnl","count"),
    wins=("result", lambda x: (x=="win").sum()),
    pnl=("pnl","sum")
)
yearly["wr"] = yearly["wins"] / yearly["n"]

W = 64

def row(label, val):
    return f"║  {label:<35}{str(val):<{W-37}}║"

def sep():
    return "╠" + "═"*W + "╣"

def hdr(t):
    return [f"║  \033[1m{t}\033[0m{'':<{W-4-len(t)}}║",
            f"║  {'─'*(W-4):<{W-4}}║"]

print()
print("╔" + "═"*W + "╗")
print(f"║{'  PROP FIRM SIMULATION':^{W}}║")
print(f"║{'  BOS + FVG / IFVG Trend Reentry  ·  NQ Nasdaq':^{W}}║")
print(sep())
print(row("Period", f"{all_dates[0]}  →  {all_dates[-1]}"))
print(row("Data range (5 years)", f"{len(all_dates):,} trading days"))
print(sep())

print(f"║  {'STRATEGY PERFORMANCE':<{W-2}}║")
print(f"║  {'─'*(W-4):<{W-4}}║")
print(row("Total trades resolved",       f"{total:,}"))
print(row("  Long entries (trend ↑)",    f"{(trades['trend']==1).sum():,}"))
print(row("  Short entries (trend ↓)",   f"{(trades['trend']==-1).sum():,}"))
print(row("  via 5m FVG",                f"{(trades['fvg_tf']=='5m-FVG').sum():,}"))
print(row("  via 15m FVG",               f"{(trades['fvg_tf']=='15m-FVG').sum():,}"))
print(row("  via 5m IFVG",               f"{(trades['fvg_tf']=='5m-IFVG').sum():,}"))
print(row("  via 15m IFVG",              f"{(trades['fvg_tf']=='15m-IFVG').sum():,}"))
print(row("Win rate",                    f"{wr:.1%}   (break-even: {breakeven_wr:.1%})"))
print(row("Loss rate",                   f"{1-wr:.1%}"))
print(row("Wins / Losses",              f"{wins} W  /  {losses} L"))
print(row("Avg P&L per trade",           f"${avg_pnl:+,.2f}"))
print(row("Profit factor",              f"{pf:.2f}"))
print(row("Avg trades per day",          f"{total/max(1,len(all_dates)):.2f}"))
print(row("Max drawdown (1 account)",    f"${max_dd:,.0f}"))
print(sep())

print(f"║  {'ACCOUNT ACTIVITY':<{W-2}}║")
print(f"║  {'─'*(W-4):<{W-4}}║")
print(row("Accounts purchased",          f"{st['bought']}  ×  €{ACCOUNT_COST}  =  €{total_spent:,}"))
print(row("  Evaluations passed  ✓",     f"{st['evals_pass']}  ({st['evals_pass']/max(1,st['bought']):.0%})"))
print(row("  Evaluations failed  ✗",     f"{st['evals_fail']}  ({st['evals_fail']/max(1,st['bought']):.0%})"))
print(row("Express (funded) accounts",   f"{st['evals_pass']}"))
print(row("  Express terminated",        f"{st['exp_term']}"))
print(row("  Still alive at end",        f"{len(accounts)}  (eval {alive_eval} / express {alive_exp})"))
print(sep())

print(f"║  {'PAYOUTS':<{W-2}}║")
print(f"║  {'─'*(W-4):<{W-4}}║")
avg_pay = st["payout_usd"] / max(1, st["payouts"])
print(row("Payout cycles completed",     f"{st['payouts']}"))
print(row("Avg USD per payout",          f"${avg_pay:,.0f}"))
print(row("Total payouts (USD)",         f"${st['payout_usd']:,.0f}"))
print(row("Total payouts (EUR)",         f"€{total_recv_eur:,.0f}"))
print(sep())

print(f"║  {'CAPITAL FLOW':<{W-2}}║")
print(f"║  {'─'*(W-4):<{W-4}}║")
print(row("Starting capital",            f"€{CAPITAL:,}"))
print(row("  – Total account costs",     f"€{total_spent:,}"))
print(row("  + Total payouts received",  f"€{total_recv_eur:,.0f}"))
print(row("Final capital",               f"€{capital_eur:,.0f}"))
print(row("Net profit / loss",           f"€{net_profit:+,.0f}"))
print(row("ROI over 5 years",            f"{roi_5y:+.1f} %"))
print(row("Annual ROI",                  f"{roi_ann:+.1f} %"))
print(sep())

print(f"║  {'BEST / WORST MONTHS  (raw P&L per account)':<{W-2}}║")
print(f"║  {'─'*(W-4):<{W-4}}║")
for m, v in best3.items():
    print(row(f"  ▲ {m}", f"${v:>+10,.0f}   WR {monthly.loc[m,'wr']:.0%}   n={monthly.loc[m,'n']:.0f}"))
for m, v in worst3.items():
    print(row(f"  ▼ {m}", f"${v:>+10,.0f}   WR {monthly.loc[m,'wr']:.0%}   n={monthly.loc[m,'n']:.0f}"))
print(sep())

print(f"║  {'YEAR-BY-YEAR  (raw P&L, single account)':<{W-2}}║")
print(f"║  {'─'*(W-4):<{W-4}}║")
for yr, row_d in yearly.iterrows():
    bar_chr = "█" * max(0, int(row_d["wr"]*20)) + "░" * max(0, 20-int(row_d["wr"]*20))
    sign = "+" if row_d["pnl"] >= 0 else ""
    print(row(f"  {yr}",
              f"{sign}${row_d['pnl']:>9,.0f}  n={row_d['n']:>3.0f}  WR={row_d['wr']:.0%}  {bar_chr}"))
print("╚" + "═"*W + "╝")
print(f"\n  Break-even win rate at 0.75 RR = {breakeven_wr:.1%}")
print(f"  Strategy win rate              = {wr:.1%}")
delta = wr - breakeven_wr
verdict = "PROFITABLE ✓" if delta > 0 else "NOT PROFITABLE ✗"
print(f"  Delta                          = {delta:+.1%}  →  {verdict}")
