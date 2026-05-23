#!/usr/bin/env python3
"""BOS + IFVG Prop Firm Simulator – Interactive HTML Dashboard"""
import pandas as pd
import numpy as np
from collections import defaultdict
import warnings, random, sys, subprocess
warnings.filterwarnings("ignore")

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    subprocess.run([sys.executable,"-m","pip","install","plotly","-q"], check=True)
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

# ── Parameters ───────────────────────────────────────────────────
START_DATE      = "2021-05-19"
EUR_USD         = 1.08
ACCOUNT_COST    = 90
ACCOUNT_SIZE    = 50_000
MAX_ACCOUNTS    = 5
CAPITAL         = 7_000
EVAL_TARGET     = 3_000
EVAL_MAX_DD     = 2_000
EVAL_DAY_CAP    = 1_500
EVAL_MIN_DAYS   = 2
EXP_WIN_MIN     = 150
EXP_WIN_DAYS    = 5
EXP_MAX_PAY     = 2_000
EXP_MIN_PAY     = 500 / EUR_USD
STOP_PTS        = 16.0
TGT_PTS         = 12.0
CONTRACTS       = 4
PV              = 20.0
RISK            = STOP_PTS * PV * CONTRACTS
REWARD          = TGT_PTS  * PV * CONTRACTS
COMMISSION      = 16.0
PAYOUT_SPLIT    = 0.90
CONSISTENCY_CAP = 0.50
SLIP_PTS        = 0.5   # stop-loss slippage in points
BLOCK_SIZE      = 20    # block bootstrap block size (~1 month of trading days)
SWING_N5        = 3
SWING_N15       = 2
MIN_SWING5      = 15.0
MIN_SWING15     = 20.0
TREND_STABLE    = 6
FVG_MIN5        = 5.0
FVG_MIN15       = 8.0
FVG_LB5         = 12
FVG_LB15        = 40
MAX_BARS        = 36
MC_RUNS         = 500

# ── 1. Load data ─────────────────────────────────────────────────
print("Loading 1m NQ data…")
df1 = pd.read_csv(
    str(__import__("pathlib").Path(__file__).parent / "nq-1m.csv"),
    sep=";", header=None,
    names=["date","time","open","high","low","close","volume"]
)
df1["dt"] = pd.to_datetime(df1["date"]+" "+df1["time"], format="%d/%m/%Y %H:%M:%S")
df1 = df1[df1["dt"] >= START_DATE].set_index("dt")
df1 = df1[["open","high","low","close","volume"]].astype(float)

def resamp(df, p):
    return df.resample(p).agg(
        open=("open","first"), high=("high","max"),
        low=("low","min"), close=("close","last"),
        volume=("volume","sum")
    ).dropna(subset=["open"])

df5  = resamp(df1, "5min")
df15 = resamp(df1, "15min")
print(f"  5m: {len(df5):,}  15m: {len(df15):,}")

# ── 2. BOS trend ─────────────────────────────────────────────────
def bos_trend(df, n, min_swing):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    N = len(df); trend = np.zeros(N, dtype=np.int8); cur = 0; sh=[]; sl=[]
    for i in range(2*n, N):
        ci = i - n
        wh = h[ci-n:ci+n+1]; wl = l[ci-n:ci+n+1]
        if wh.max()-wl.min() >= min_swing:
            if h[ci]==wh.max(): sh=(sh+[h[ci]])[-4:]
            if l[ci]==wl.min(): sl=(sl+[l[ci]])[-4:]
        if sh and c[i]>sh[-1]: cur=1;  sh=[]; sl=[]
        elif sl and c[i]<sl[-1]: cur=-1; sh=[]; sl=[]
        trend[i]=cur
    return pd.Series(trend, index=df.index)

print("Computing BOS trends…")
t5  = bos_trend(df5,  SWING_N5,  MIN_SWING5)
t15 = bos_trend(df15, SWING_N15, MIN_SWING15)
df5["trend5"]  = t5
df5["trend15"] = t15.shift(1).reindex(df5.index, method="ffill").fillna(0).astype(int)
df5["aligned"] = np.where((df5["trend5"]==df5["trend15"])&(df5["trend5"]!=0), df5["trend5"], 0)
aligned_arr = df5["aligned"].values
stable = np.zeros(len(df5), dtype=bool); cnt=0
for i in range(len(df5)):
    cnt = cnt+1 if aligned_arr[i]!=0 else 0
    stable[i] = cnt>=TREND_STABLE
df5["stable"] = stable

# ── 3. FVG detection ─────────────────────────────────────────────
def find_fvgs(df, min_size):
    h, l, idx = df["high"].values, df["low"].values, df.index
    out=[]
    for i in range(2, len(df)):
        bs = l[i]-h[i-2]
        if bs>=min_size: out.append([i,idx[i],1,float(h[i-2]),float(l[i]),float(bs),False])
        bs2=l[i-2]-h[i]
        if bs2>=min_size: out.append([i,idx[i],-1,float(h[i]),float(l[i-2]),float(bs2),False])
    return out

print("Detecting FVGs…")
fvg5  = find_fvgs(df5,  FVG_MIN5)
fvg15 = find_fvgs(df15, FVG_MIN15)
df5_ts = df5.index
def ts2bar5(ts): return min(df5_ts.searchsorted(ts), len(df5_ts)-1)
fvg15_mapped = []
for f in fvg15:
    fc=f[:]; fc[0]=ts2bar5(f[1]); fvg15_mapped.append(fc)
fvg5_at=defaultdict(list); fvg15_at=defaultdict(list)
for f in fvg5:  fvg5_at[f[0]+1].append(f[:])
for f in fvg15_mapped: fvg15_at[f[0]+1].append(f[:])

# ── 4. Session filter ─────────────────────────────────────────────
def session(ts):
    t=ts.hour*60+ts.minute
    if 8*60+35<=t<10*60+30: return 1
    if 12*60+30<=t<14*60:   return 2
    return 0

# ── 5. Signals (IFVG only) ────────────────────────────────────────
print("Generating signals…")
aligned_v=df5["aligned"].values; stable_v=df5["stable"].values
bars5=list(df5.itertuples()); N5=len(bars5)
signals=[]; sig_sessions=set(); active5=[]; active15=[]

for i in range(max(FVG_LB5,FVG_LB15,SWING_N5*2+TREND_STABLE+5), N5):
    b=bars5[i]; ts=b.Index
    for f in fvg5_at.get(i,[]): active5.append(f[:])
    for f in fvg15_at.get(i,[]): active15.append(f[:])
    active5=[f for f in active5 if i-f[0]<=FVG_LB5]
    active15=[f for f in active15 if i-f[0]<=FVG_LB15]
    sess=session(ts)
    if sess==0 or int(aligned_v[i])==0 or not stable_v[i]:
        for fvg in active5+active15:
            if not fvg[6] and b.low<=fvg[4] and b.high>=fvg[3]: fvg[6]=True
        continue
    tr=int(aligned_v[i]); sess_key=(ts.date(),sess)
    if sess_key in sig_sessions:
        for fvg in active5+active15:
            if not fvg[6] and b.low<=fvg[4] and b.high>=fvg[3]: fvg[6]=True
        continue
    candidates=[]
    for fvg in active5:
        bot,top,size=fvg[3],fvg[4],fvg[5]
        if b.low>top or b.high<bot: continue
        if fvg[6] and fvg[2]==-tr:
            if tr==1 and b.close>(b.low+b.high)/2: candidates.append((fvg,"5m-IFVG"))
            elif tr==-1 and b.close<(b.low+b.high)/2: candidates.append((fvg,"5m-IFVG"))
    for fvg in active15:
        bot,top,size=fvg[3],fvg[4],fvg[5]
        if b.low>top or b.high<bot: continue
        if fvg[6] and fvg[2]==-tr:
            if tr==1 and b.close>(b.low+b.high)/2: candidates.append((fvg,"15m-IFVG"))
            elif tr==-1 and b.close<(b.low+b.high)/2: candidates.append((fvg,"15m-IFVG"))
    for fvg in active5+active15:
        if not fvg[6] and b.low<=fvg[4] and b.high>=fvg[3]: fvg[6]=True
    if not candidates: continue
    best,fvg_tf=candidates[0]   # first valid IFVG, no score ranking
    fvg_mid=(best[3]+best[4])/2.0
    entry=fvg_mid; stop=entry-STOP_PTS*tr; target=entry+TGT_PTS*tr
    best[6]=True
    signals.append({"ts":ts,"date":ts.date(),"bar_i":i,"sess":sess,
                    "trend":tr,"fvg_tf":fvg_tf,"entry":entry,"stop":stop,"target":target,
                    "fvg_bot":round(best[3],2),"fvg_top":round(best[4],2)})
    sig_sessions.add(sess_key)

sigs=pd.DataFrame(signals)
if sigs.empty: print("No signals."); exit()
print(f"  Signals: {len(sigs):,}")

# ── 6. Outcomes ───────────────────────────────────────────────────
print("Simulating outcomes…")
outcomes=[]
for _,sig in sigs.iterrows():
    i0=int(sig["bar_i"])+1; i_end=min(i0+MAX_BARS,N5)
    entry=sig["entry"]; stop=sig["stop"]; target=sig["target"]
    tr=int(sig["trend"]); sig_date=sig["date"]
    fvg_bot=sig["fvg_bot"]; fvg_top=sig["fvg_top"]
    fill_price=None; last_close=None; exit_type=None
    for j in range(i0,i_end):
        fb=bars5[j]
        if fb.Index.date()!=sig_date or session(fb.Index)==0: break
        last_close=fb.close
        if fill_price is None:
            if tr==1:
                if fb.open<fvg_bot: exit_type="zone_fail"; break   # zone broken
                fill_price=fb.open if fb.open<=entry else (entry if fb.low<=entry else None)
            else:
                if fb.open>fvg_top: exit_type="zone_fail"; break
                fill_price=fb.open if fb.open>=entry else (entry if fb.high>=entry else None)
            if fill_price is None: continue
        if tr==1:
            if fb.low<=stop:    exit_type="loss"; break
            if fb.high>=target: exit_type="win";  break
        else:
            if fb.high>=stop:   exit_type="loss"; break
            if fb.low<=target:  exit_type="win";  break
    if exit_type=="zone_fail" or fill_price is None: continue
    if exit_type is None:
        if last_close is None: continue
        exit_price=last_close; result="exit_close"
    elif exit_type=="win":  exit_price=target;  result="win"
    else:                   exit_price=stop-SLIP_PTS*tr; result="loss"
    raw=(exit_price-fill_price)*PV*CONTRACTS if tr==1 else (fill_price-exit_price)*PV*CONTRACTS
    pnl=raw-COMMISSION
    outcomes.append({**sig.to_dict(),"result":result,"pnl":pnl})

trades=pd.DataFrame(outcomes)
if trades.empty: print("No trades."); exit()
trades["month"]=pd.to_datetime(trades["date"]).dt.to_period("M")
trades["year"]=pd.to_datetime(trades["date"]).dt.year
wins_only=(trades["result"]=="win").sum(); losses_only=(trades["result"]=="loss").sum()
print(f"  Trades: {len(trades):,}  WR(win/loss only): {wins_only/(wins_only+losses_only):.1%}  exit_close: {(trades['result']=='exit_close').sum()}")

# ── 7. Prop firm sim (with capital history) ───────────────────────
print("Running prop firm simulation…")
trades_by_date={d:g.to_dict("records") for d,g in trades.groupby("date")}
all_dates=sorted(trades_by_date.keys())
capital_eur=float(CAPITAL); accounts=[]; next_id=0
st=dict(bought=0,evals_pass=0,evals_fail=0,exp_term=0,payouts=0,payout_usd=0.0)
capital_history=[CAPITAL]; payout_events=[]

def new_acc():
    global capital_eur, next_id
    if capital_eur<ACCOUNT_COST: return None
    capital_eur-=ACCOUNT_COST; next_id+=1; st["bought"]+=1
    return dict(phase="eval",balance=0.0,days=set(),day_pnl={},alive=True,
                peak_eod=float(ACCOUNT_SIZE),exp_win_days=0,
                exp_win_dates=set(),exp_base=0.0,best_day=0.0,total_pp=0.0)

for _ in range(MAX_ACCOUNTS):
    a=new_acc()
    if a: accounts.append(a)

for trade_date in all_dates:
    day_trades=trades_by_date[trade_date]
    for acc in accounts:
        if not acc["alive"]: continue
        dd_floor=acc["peak_eod"]-EVAL_MAX_DD
        dpnl=acc["day_pnl"].get(trade_date,0.0)
        for t in day_trades:
            if acc["phase"]=="eval":
                if dpnl>=EVAL_DAY_CAP: break
                credit=min(t["pnl"],EVAL_DAY_CAP-dpnl) if t["result"]=="win" else t["pnl"]
            else: credit=t["pnl"]
            dpnl+=credit; acc["balance"]+=credit; acc["days"].add(trade_date)
            if acc["phase"]=="eval":
                if acc["balance"]>=EVAL_TARGET and len(acc["days"])>=EVAL_MIN_DAYS:
                    acc["phase"]="express"; acc["exp_base"]=acc["balance"]
                    st["evals_pass"]+=1; break
        acc["day_pnl"][trade_date]=dpnl
        eod=ACCOUNT_SIZE+acc["balance"]
        if acc["phase"]=="eval" and eod<=dd_floor: acc["alive"]=False; st["evals_fail"]+=1
        elif acc["phase"]=="express" and eod<dd_floor: acc["alive"]=False; st["exp_term"]+=1
        if acc["alive"] and eod>acc["peak_eod"]: acc["peak_eod"]=eod
        if acc["phase"]=="express" and acc["alive"]:
            if dpnl>=EXP_WIN_MIN and trade_date not in acc["exp_win_dates"]:
                acc["exp_win_dates"].add(trade_date); acc["exp_win_days"]+=1
                if dpnl>acc["best_day"]: acc["best_day"]=dpnl
                acc["total_pp"]+=max(0,dpnl)
            if acc["exp_win_days"]>=EXP_WIN_DAYS:
                profit=acc["balance"]-acc["exp_base"]
                payout=max(0.0,min(profit*0.5,EXP_MAX_PAY))*PAYOUT_SPLIT
                min_bal_ok=acc["balance"]>=1_000
                if payout>=EXP_MIN_PAY and min_bal_ok:
                    st["payouts"]+=1; st["payout_usd"]+=payout
                    capital_eur+=payout/EUR_USD
                    payout_events.append((str(trade_date),payout))
                    acc["exp_win_days"]=0; acc["exp_win_dates"]=set()
                    acc["exp_base"]=acc["balance"]
                    acc["peak_eod"]=float(ACCOUNT_SIZE)+100
    accounts=[a for a in accounts if a["alive"]]
    while len(accounts)<MAX_ACCOUNTS and capital_eur>=ACCOUNT_COST:
        a=new_acc()
        if a: accounts.append(a)
    capital_history.append(capital_eur)

# ── 8. Monte Carlo ────────────────────────────────────────────────
print(f"Monte Carlo ({MC_RUNS} runs)…")
all_day_pnls=[[t["pnl"] for t in trades_by_date[d]] for d in all_dates]
n_days=len(all_day_pnls)

def run_mc_sim(day_lists):
    cap=float(CAPITAL); accs=[]
    def mk():
        nonlocal cap
        if cap<ACCOUNT_COST: return None
        cap-=ACCOUNT_COST
        return {"ph":"eval","bal":0.0,"days":0,"alive":True,
                "peak":float(ACCOUNT_SIZE),"ew":0,"eb":0.0,"bd":0.0,"tp":0.0}
    for _ in range(MAX_ACCOUNTS):
        a=mk()
        if a: accs.append(a)
    hist=[cap]
    for dpls in day_lists:
        if not dpls: hist.append(cap); continue
        for acc in accs:
            if not acc["alive"]: continue
            fl=acc["peak"]-EVAL_MAX_DD  # floor frozen at start of day
            dpnl=0.0
            for pnl in dpls:
                if acc["ph"]=="eval":
                    if dpnl>=EVAL_DAY_CAP: break
                    credit=min(pnl,EVAL_DAY_CAP-dpnl) if pnl>0 else pnl
                else: credit=pnl
                dpnl+=credit; acc["bal"]+=credit
                if acc["ph"]=="eval":
                    if acc["bal"]>=EVAL_TARGET and acc["days"]>=EVAL_MIN_DAYS:
                        acc["ph"]="exp"; acc["eb"]=acc["bal"]; break
            # EOD breach check only
            eod=ACCOUNT_SIZE+acc["bal"]
            if acc["ph"]=="eval" and eod<=fl: acc["alive"]=False
            elif acc["ph"]=="exp" and eod<fl: acc["alive"]=False
            if acc["alive"]:
                acc["days"]+=1
                if eod>acc["peak"]: acc["peak"]=eod
                if acc["ph"]=="exp":
                    if dpnl>=EXP_WIN_MIN:
                        acc["ew"]+=1
                        if dpnl>acc["bd"]: acc["bd"]=dpnl
                        acc["tp"]+=max(0,dpnl)
                    if acc["ew"]>=EXP_WIN_DAYS:
                        profit=acc["bal"]-acc["eb"]
                        pay=max(0.0,min(profit*0.5,EXP_MAX_PAY))*PAYOUT_SPLIT
                        min_bal_ok=acc["bal"]>=1_000
                        if pay>=EXP_MIN_PAY and min_bal_ok:
                            cap+=pay/EUR_USD
                            acc["ew"]=0; acc["eb"]=acc["bal"]
                            acc["peak"]=float(ACCOUNT_SIZE)+100
        accs=[a for a in accs if a["alive"]]
        while len(accs)<MAX_ACCOUNTS and cap>=ACCOUNT_COST:
            a=mk()
            if a: accs.append(a)
        hist.append(cap)
    return cap, hist

rng=np.random.default_rng(42)
mc_finals=[]; mc_hists=[]
n_blocks=int(np.ceil(n_days/BLOCK_SIZE))
max_start=max(1,n_days-BLOCK_SIZE+1)
for ri in range(MC_RUNS):
    if ri%100==0: print(f"  {ri}/{MC_RUNS}…")
    starts=rng.integers(0,max_start,size=n_blocks)
    sampled=[]
    for s in starts:
        sampled.extend(all_day_pnls[s:s+BLOCK_SIZE])
    sampled=sampled[:n_days]
    fc,h=run_mc_sim(sampled)
    mc_finals.append(fc); mc_hists.append(h)

mc_finals=np.array(mc_finals)
prob_profit=(mc_finals>CAPITAL).mean()
prob_2x=(mc_finals>CAPITAL*2).mean()
prob_ruin=(mc_finals<CAPITAL*0.3).mean()
print(f"  Median: €{np.median(mc_finals):,.0f}  P(Gewinn): {prob_profit:.0%}")

# ── 9. Build charts ───────────────────────────────────────────────
print("Building charts…")
C_BG="#0b0f19"; C_CARD="#111827"; C_BORDER="rgba(255,255,255,0.06)"
C_GREEN="#22d3a5"; C_RED="#f43f5e"; C_BLUE="#6366f1"
C_BLUE2="#818cf8"; C_YELLOW="#fbbf24"; C_PURPLE="#a78bfa"
C_ORANGE="#fb923c"; C_TEXT="#f1f5f9"; C_MUTED="#64748b"

LAY=dict(paper_bgcolor=C_CARD, plot_bgcolor=C_CARD,
         font=dict(color=C_TEXT, family="Inter,system-ui,sans-serif", size=12),
         margin=dict(l=55,r=20,t=50,b=45))

def ax(): return dict(gridcolor=C_BORDER, showgrid=True, zeroline=False,
                      linecolor=C_BORDER, tickfont=dict(color=C_MUTED, size=11))

# ── Equity curve
eq_cum=trades["pnl"].cumsum().values
eq_dates=[str(d) for d in trades["date"].values]
fig_eq=go.Figure()
fig_eq.add_trace(go.Scatter(x=eq_dates, y=eq_cum, mode="lines",
    line=dict(color=C_BLUE2,width=2), fill="tozeroy",
    fillcolor="rgba(99,102,241,0.08)"))
fig_eq.add_hline(y=0, line_color=C_MUTED, line_dash="dash", line_width=1)
fig_eq.update_layout(**LAY, title="Kumulierter P&L – Einzelkonto", height=320,
    xaxis=ax(), yaxis=dict(**ax(), tickprefix="$"), showlegend=False)

# ── Monthly bar
monthly=trades.groupby("month")["pnl"].sum()
m_lbls=[str(m) for m in monthly.index]; m_vals=monthly.values
fig_mon=go.Figure(go.Bar(x=m_lbls, y=m_vals,
    marker_color=[C_GREEN if v>=0 else C_RED for v in m_vals],
    text=[f"${v:+,.0f}" for v in m_vals], textposition="outside",
    textfont=dict(size=8)))
fig_mon.update_layout(**LAY, title="Monatlicher P&L", height=310,
    xaxis=dict(**ax(), tickangle=-45), yaxis=dict(**ax(), tickprefix="$"))

# ── Win rate by year
yr=trades.groupby("year").agg(wr=("result",lambda x:(x=="win").mean()),
    n=("pnl","count"), pnl=("pnl","sum")).reset_index()
fig_yr=make_subplots(specs=[[{"secondary_y":True}]])
fig_yr.add_trace(go.Bar(x=yr["year"].astype(str), y=yr["wr"]*100, name="Win Rate %",
    marker_color=[C_GREEN if w>57.1 else C_RED for w in yr["wr"]*100],
    opacity=0.85), secondary_y=False)
fig_yr.add_trace(go.Scatter(x=yr["year"].astype(str), y=yr["pnl"], name="P&L $",
    mode="lines+markers", line=dict(color=C_YELLOW,width=2),
    marker=dict(size=8)), secondary_y=True)
fig_yr.add_hline(y=57.1, line_color=C_MUTED, line_dash="dash", secondary_y=False,
    annotation_text="Break-even 57.1%", annotation_font_color=C_MUTED)
fig_yr.update_layout(**LAY, title="Win Rate & P&L nach Jahr", height=310,
    xaxis=ax(), yaxis=dict(**ax(), ticksuffix="%"),
    yaxis2=dict(tickprefix="$", gridcolor="rgba(0,0,0,0)"),
    legend=dict(bgcolor="rgba(0,0,0,0)", x=0.01, y=0.99))

# ── PnL distribution
wins_pnl=trades[trades["result"]=="win"]["pnl"].values
loss_pnl=trades[trades["result"]=="loss"]["pnl"].values
fig_dist=go.Figure()
fig_dist.add_trace(go.Histogram(x=wins_pnl, name="Wins", nbinsx=25,
    marker_color=C_GREEN, opacity=0.75))
fig_dist.add_trace(go.Histogram(x=loss_pnl, name="Losses", nbinsx=25,
    marker_color=C_RED, opacity=0.75))
fig_dist.update_layout(**LAY, title="Trade P&L Verteilung", height=310,
    barmode="overlay", xaxis=dict(**ax(), tickprefix="$"), yaxis=ax(),
    legend=dict(bgcolor="rgba(0,0,0,0)", x=0.01, y=0.99))

# ── Capital growth
cap_x=list(range(len(capital_history)))
fig_cap=go.Figure()
fig_cap.add_trace(go.Scatter(x=cap_x, y=capital_history, mode="lines",
    line=dict(color=C_GREEN,width=2.5), fill="tozeroy",
    fillcolor="rgba(34,211,165,0.07)"))
fig_cap.add_hline(y=CAPITAL, line_color=C_MUTED, line_dash="dash",
    annotation_text=f"Start €{CAPITAL:,}", annotation_font_color=C_MUTED)
fig_cap.update_layout(**LAY, title="Kapitalwachstum (EUR) – Prop Firm System", height=340,
    xaxis=dict(**ax(), title="Handelstag"),
    yaxis=dict(**ax(), tickprefix="€"), showlegend=False)

# ── Account funnel
funnel_labels=["Accounts\ngekauft","Eval\nbestanden","Express\ngefunded","Payout-\nZyklen"]
funnel_vals=[st["bought"],st["evals_pass"],st["evals_pass"],st["payouts"]]
funnel_pct=[100,st["evals_pass"]/max(1,st["bought"])*100,
            st["evals_pass"]/max(1,st["bought"])*100,
            st["payouts"]/max(1,st["evals_pass"])*100]
fig_fun=go.Figure(go.Bar(
    x=funnel_labels, y=funnel_vals,
    marker_color=[C_BLUE, C_GREEN, C_YELLOW, C_PURPLE],
    text=[f"{v}<br>({p:.0f}%)" for v,p in zip(funnel_vals,funnel_pct)],
    textposition="outside"))
fig_fun.add_trace(go.Bar(
    x=["Eval\ngescheitert","Express\nterminiert"],
    y=[st["evals_fail"],st["exp_term"]],
    marker_color=C_RED, opacity=0.8,
    text=[f"{st['evals_fail']}<br>({st['evals_fail']/max(1,st['bought']):.0%})",
          f"{st['exp_term']}"],
    textposition="outside", name="Failures"))
fig_fun.update_layout(**LAY, title="Account-Lebenszyklus", height=340,
    xaxis=ax(), yaxis=ax(), showlegend=False)

# ── Asymmetry chart
avg_pay=st["payout_usd"]/max(1,st["payouts"])
fig_asym=go.Figure()
fig_asym.add_trace(go.Bar(
    name="Dein Risiko",
    x=["Pro Account\n(Kaufpreis)", "Firma trägt\n(Drawdown)"],
    y=[ACCOUNT_COST, EVAL_MAX_DD],
    marker_color=C_RED, opacity=0.85,
    text=[f"−€{ACCOUNT_COST}", f"−${EVAL_MAX_DD:,}"],
    textposition="outside"))
fig_asym.add_trace(go.Bar(
    name="Möglicher Gewinn",
    x=["Avg. Payout\n(wenn funded)", "Max Payout\n(pro Zyklus)"],
    y=[avg_pay, EXP_MAX_PAY*PAYOUT_SPLIT],
    marker_color=C_GREEN, opacity=0.85,
    text=[f"+${avg_pay:,.0f}", f"+${EXP_MAX_PAY*PAYOUT_SPLIT:,.0f}"],
    textposition="outside"))
fig_asym.update_layout(**LAY, title="Asymmetrie: Begrenztes Risiko vs. Unbeschränkter Upside",
    height=340, xaxis=ax(), yaxis=dict(**ax(), title="USD / EUR"),
    legend=dict(bgcolor="rgba(0,0,0,0)", x=0.01, y=0.99))

# ── Payout cycles timeline
pay_dates=[p[0] for p in payout_events]
pay_amts =[p[1] for p in payout_events]
pay_cum  =[sum(pay_amts[:i+1]) for i in range(len(pay_amts))]
pay_net_cum=[sum(pay_amts[:i+1])/EUR_USD - (i+1)*0 for i in range(len(pay_amts))]  # in EUR

fig_pay=go.Figure()
# Cumulative payout line
fig_pay.add_trace(go.Scatter(
    x=pay_dates, y=[v/EUR_USD for v in pay_cum],
    mode="lines+markers", name="Kumulierte Payouts (€)",
    line=dict(color=C_GREEN, width=2.5),
    marker=dict(size=7, color=C_GREEN),
    fill="tozeroy", fillcolor="rgba(34,211,165,0.07)"))
# Individual payout bars
fig_pay.add_trace(go.Bar(
    x=pay_dates, y=[v/EUR_USD for v in pay_amts],
    name="Payout pro Zyklus (€)",
    marker_color=C_BLUE2, opacity=0.6,
    yaxis="y2"))
fig_pay.add_hline(y=CAPITAL, line_color=C_MUTED, line_dash="dash",
    annotation_text=f"Startkapital €{CAPITAL:,}", annotation_font_color=C_MUTED,
    annotation_font_size=11)
fig_pay.update_layout(**LAY,
    title=f"Payout-Zyklen – {len(pay_dates)} Auszahlungen · ∑${st['payout_usd']:,.0f} = €{st['payout_usd']/EUR_USD:,.0f}",
    height=400,
    xaxis=dict(**ax(), title="Datum", tickangle=-45),
    yaxis=dict(**ax(), tickprefix="€", title="Kumuliert (€)"),
    yaxis2=dict(tickprefix="€", title="Pro Zyklus (€)", overlaying="y", side="right",
                gridcolor="rgba(0,0,0,0)", tickfont=dict(color=C_MUTED, size=11)),
    legend=dict(bgcolor="rgba(17,24,39,0.8)", bordercolor=C_BORDER, borderwidth=1,
                x=0.01, y=0.99, font=dict(size=11)))

# ── MC fan chart
lens=[len(h) for h in mc_hists]
min_len=min(lens)
mc_arr=np.array([h[:min_len] for h in mc_hists])
x_mc=list(range(min_len))
p5=np.percentile(mc_arr,5,axis=0); p25=np.percentile(mc_arr,25,axis=0)
p50=np.percentile(mc_arr,50,axis=0); p75=np.percentile(mc_arr,75,axis=0)
p95=np.percentile(mc_arr,95,axis=0)

fig_mc=go.Figure()
step=max(1,len(mc_arr)//120)
for i in range(0,len(mc_arr),step):
    fig_mc.add_trace(go.Scatter(x=x_mc, y=mc_arr[i], mode="lines",
        line=dict(color="rgba(99,102,241,0.035)",width=1),
        showlegend=False, hoverinfo="skip"))
fig_mc.add_trace(go.Scatter(x=x_mc+x_mc[::-1], y=list(p95)+list(p5[::-1]),
    fill="toself", fillcolor="rgba(99,102,241,0.10)",
    line=dict(color="rgba(0,0,0,0)"), name="P5–P95 (90% Konfidenz)"))
fig_mc.add_trace(go.Scatter(x=x_mc+x_mc[::-1], y=list(p75)+list(p25[::-1]),
    fill="toself", fillcolor="rgba(99,102,241,0.22)",
    line=dict(color="rgba(0,0,0,0)"), name="P25–P75 (IQR 50%)"))
fig_mc.add_trace(go.Scatter(x=x_mc, y=p50, mode="lines", name="Median (P50)",
    line=dict(color=C_BLUE2,width=2.5)))
fig_mc.add_trace(go.Scatter(x=x_mc, y=p5, mode="lines", name="P5 – Worst 5%",
    line=dict(color=C_RED,width=1.5,dash="dot")))
fig_mc.add_trace(go.Scatter(x=x_mc, y=p95, mode="lines", name="P95 – Best 5%",
    line=dict(color=C_GREEN,width=1.5,dash="dot")))
fig_mc.add_hline(y=CAPITAL, line_color=C_MUTED, line_dash="dash",
    annotation_text=f"Startkapital €{CAPITAL:,}", annotation_font_color=C_MUTED,
    annotation_font_size=11)
fig_mc.update_layout(**LAY,
    title=f"Monte Carlo Pfad-Fächer – {MC_RUNS} Block-Bootstrap-Simulationen (Blockgröße {BLOCK_SIZE} Tage)",
    height=460, xaxis=dict(**ax(), title="Handelstag"),
    yaxis=dict(**ax(), tickprefix="€"),
    legend=dict(bgcolor="rgba(17,24,39,0.8)", bordercolor=C_BORDER, borderwidth=1,
                x=0.01, y=0.99, font=dict(size=11)))

# ── MC distribution
fig_mcd=go.Figure()
fig_mcd.add_trace(go.Histogram(x=mc_finals, nbinsx=70,
    marker_color=C_BLUE, opacity=0.6,
    marker_line=dict(color=C_BLUE2, width=0.3)))
for pct,lbl,col,pos in [(5,"P5",C_RED,"top left"),(25,"P25",C_ORANGE,"top left"),
                          (50,"Median",C_BLUE2,"top right"),(75,"P75",C_GREEN,"top right"),
                          (95,"P95",C_PURPLE,"top right")]:
    v=np.percentile(mc_finals,pct)
    fig_mcd.add_vline(x=v, line_color=col, line_dash="dash",
        annotation_text=f"{lbl}: €{v:,.0f}", annotation_position=pos,
        annotation_font_color=col, annotation_font_size=11)
fig_mcd.add_vline(x=CAPITAL, line_color=C_MUTED, line_dash="solid",
    annotation_text=f"Start €{CAPITAL:,}", annotation_position="top left",
    annotation_font_color=C_MUTED, annotation_font_size=11)
fig_mcd.update_layout(**LAY, height=370,
    title=f"Verteilung Endkapital nach {MC_RUNS} Simulationen  ·  "
          f"P(Gewinn)={prob_profit:.0%}  P(2×)={prob_2x:.0%}  P(Ruin)={prob_ruin:.0%}",
    xaxis=dict(**ax(), tickprefix="€"), yaxis=dict(**ax(), title="Anzahl Simulationen"),
    showlegend=False)

# ── 10. Assemble HTML ─────────────────────────────────────────────
def fig_html(f):
    return f.to_html(full_html=False, include_plotlyjs=False,
                     config={"responsive":True, "displayModeBar":False})

wins=(trades["result"]=="win").sum()
losses=(trades["result"]=="loss").sum()
exit_closes=(trades["result"]=="exit_close").sum()
total=len(trades); wr=wins/total; avg_pnl=trades["pnl"].mean()
pf=trades[trades["pnl"]>0]["pnl"].sum()/abs(trades[trades["pnl"]<0]["pnl"].sum())
be_wr=1/(1+TGT_PTS/STOP_PTS)
net_profit=capital_eur-CAPITAL
total_spent=st["bought"]*ACCOUNT_COST
mc_p5=np.percentile(mc_finals,5); mc_p25=np.percentile(mc_finals,25)
mc_p75=np.percentile(mc_finals,75); mc_p95=np.percentile(mc_finals,95)
mc_median=np.median(mc_finals)

html=f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BOS + IFVG · Prop Firm Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0b0f19;
  --surface:#111827;
  --surface2:#1a2235;
  --border:rgba(255,255,255,0.06);
  --border2:rgba(255,255,255,0.10);
  --text:#f1f5f9;
  --muted:#64748b;
  --green:#22d3a5;
  --red:#f43f5e;
  --blue:#6366f1;
  --blue2:#818cf8;
  --yellow:#fbbf24;
  --purple:#a78bfa;
  --orange:#fb923c;
}}
body{{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,-apple-system,sans-serif;line-height:1.5;min-height:100vh}}
/* Layout */
.shell{{display:flex;min-height:100vh}}
.sidebar{{width:220px;background:var(--surface);border-right:1px solid var(--border);padding:28px 0;position:fixed;top:0;left:0;height:100vh;overflow-y:auto;z-index:100}}
.sidebar-logo{{padding:0 22px 24px;border-bottom:1px solid var(--border);margin-bottom:16px}}
.sidebar-logo .logo-mark{{display:flex;align-items:center;gap:10px}}
.sidebar-logo .dot{{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,var(--blue),var(--purple));display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px}}
.sidebar-logo .brand{{font-size:13px;font-weight:700;color:var(--text)}}
.sidebar-logo .brand span{{display:block;font-size:10px;color:var(--muted);font-weight:400;margin-top:1px}}
.nav-section{{padding:6px 14px;font-size:9px;font-weight:700;letter-spacing:1.2px;color:var(--muted);text-transform:uppercase;margin-top:10px}}
.nav-item{{display:flex;align-items:center;gap:10px;padding:9px 22px;font-size:12.5px;color:var(--muted);cursor:pointer;transition:all .15s;border-left:2px solid transparent;text-decoration:none}}
.nav-item:hover,.nav-item.active{{color:var(--text);background:rgba(99,102,241,.08);border-left-color:var(--blue)}}
.nav-item .ico{{width:16px;text-align:center;font-size:13px}}
.main{{margin-left:220px;flex:1;padding:28px 32px;min-width:0}}
/* Top bar */
.topbar{{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px}}
.topbar h1{{font-size:22px;font-weight:700}}
.topbar .sub{{font-size:12px;color:var(--muted);margin-top:2px}}
.badge{{display:inline-flex;align-items:center;gap:5px;background:rgba(34,211,165,.12);color:var(--green);font-size:11px;font-weight:600;padding:4px 10px;border-radius:20px;border:1px solid rgba(34,211,165,.2)}}
.badge-warn{{background:rgba(251,191,36,.10);color:var(--yellow);border-color:rgba(251,191,36,.2)}}
/* KPI row */
.kpi-row{{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-bottom:24px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 16px;position:relative;overflow:hidden;transition:border-color .2s}}
.kpi:hover{{border-color:var(--border2)}}
.kpi::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px;border-radius:2px 2px 0 0}}
.kpi.green::before{{background:linear-gradient(90deg,var(--green),transparent)}}
.kpi.red::before{{background:linear-gradient(90deg,var(--red),transparent)}}
.kpi.blue::before{{background:linear-gradient(90deg,var(--blue),transparent)}}
.kpi.purple::before{{background:linear-gradient(90deg,var(--purple),transparent)}}
.kpi.yellow::before{{background:linear-gradient(90deg,var(--yellow),transparent)}}
.kpi.orange::before{{background:linear-gradient(90deg,var(--orange),transparent)}}
.kpi-lbl{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin-bottom:8px}}
.kpi-val{{font-size:24px;font-weight:800;line-height:1}}
.kpi-sub{{font-size:10px;color:var(--muted);margin-top:5px}}
/* Section */
.sec{{margin-bottom:30px}}
.sec-head{{display:flex;align-items:center;gap:10px;margin-bottom:16px}}
.sec-head h2{{font-size:15px;font-weight:700}}
.sec-head .pill{{font-size:10px;background:rgba(99,102,241,.15);color:var(--blue2);padding:2px 8px;border-radius:10px;font-weight:600}}
.divider{{flex:1;height:1px;background:var(--border)}}
/* Cards */
.card{{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden}}
.card-pad{{padding:20px}}
.card:hover{{border-color:var(--border2)}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.g3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}
.g12{{display:grid;grid-template-columns:1fr 2fr;gap:14px}}
/* Stat blocks inside cards */
.stat-block{{display:flex;flex-direction:column;gap:10px}}
.stat-row{{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)}}
.stat-row:last-child{{border-bottom:none}}
.stat-label{{font-size:12px;color:var(--muted)}}
.stat-value{{font-size:13px;font-weight:700}}
/* MC section */
.mc-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px}}
.mc-card{{background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:16px;text-align:center;position:relative;overflow:hidden}}
.mc-card .stripe{{position:absolute;top:0;left:0;right:0;height:3px}}
.mc-val{{font-size:20px;font-weight:800;margin-top:4px;margin-bottom:2px}}
.mc-lbl{{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px}}
.mc-sub{{font-size:10px;color:var(--muted);margin-top:3px}}
/* MC Legend */
.mc-legend{{display:flex;gap:20px;flex-wrap:wrap;align-items:center;padding:14px 20px;background:var(--surface2);border-radius:10px;border:1px solid var(--border);margin-bottom:14px;font-size:12px}}
.mc-legend-item{{display:flex;align-items:center;gap:7px;color:var(--muted)}}
.mc-legend-item .swatch{{width:28px;height:3px;border-radius:2px;flex-shrink:0}}
.mc-legend-item .swatch.dashed{{background:repeating-linear-gradient(90deg,currentColor 0,currentColor 4px,transparent 4px,transparent 8px)}}
.mc-legend-item strong{{color:var(--text)}}
/* Explanation */
.ex-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-bottom:14px}}
.ex-card{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:22px}}
.ex-card h3{{font-size:13px;font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.ex-card h3 .ico{{width:24px;height:24px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0}}
.ex-card p,.ex-card li{{font-size:12.5px;color:var(--muted);line-height:1.8}}
.ex-card ul{{padding-left:16px}}
.ex-card li{{margin-bottom:3px}}
.hl{{color:var(--text);font-weight:600}}
.formula{{background:rgba(0,0,0,.3);border:1px solid var(--border);border-radius:8px;padding:12px 14px;font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--yellow);margin-top:12px;line-height:1.6}}
/* Rules table */
.rules-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}}
.rule{{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:4px}}
.rule-name{{font-size:10px;font-weight:700;color:var(--blue2);text-transform:uppercase;letter-spacing:.5px}}
.rule-val{{font-size:12px;color:var(--muted)}}
/* Progress bar */
.prog-wrap{{margin-top:8px}}
.prog-bar{{height:4px;border-radius:2px;background:rgba(255,255,255,.06);overflow:hidden;margin-top:4px}}
.prog-fill{{height:100%;border-radius:2px;transition:width .6s ease}}
/* Tags */
.tag{{display:inline-flex;align-items:center;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:.3px}}
.tag-g{{background:rgba(34,211,165,.12);color:var(--green)}}
.tag-r{{background:rgba(244,63,94,.12);color:var(--red)}}
.tag-b{{background:rgba(99,102,241,.12);color:var(--blue2)}}
.tag-y{{background:rgba(251,191,36,.12);color:var(--yellow)}}
/* Footer */
.footer{{text-align:center;padding:32px;color:var(--muted);font-size:11px;border-top:1px solid var(--border);margin-top:8px}}
/* Scrollbar */
::-webkit-scrollbar{{width:6px;height:6px}}
::-webkit-scrollbar-track{{background:transparent}}
::-webkit-scrollbar-thumb{{background:var(--border2);border-radius:3px}}
/* Responsive */
@media(max-width:1100px){{.kpi-row{{grid-template-columns:repeat(3,1fr)}}}}
@media(max-width:800px){{
  .sidebar{{display:none}}.main{{margin-left:0}}
  .kpi-row{{grid-template-columns:repeat(2,1fr)}}
  .g2,.g3,.g12,.ex-grid,.mc-grid,.rules-grid{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<div class="shell">

<!-- SIDEBAR -->
<nav class="sidebar">
  <div class="sidebar-logo">
    <div class="logo-mark">
      <div class="dot">B</div>
      <div class="brand">BOS·IFVG<span>Prop Firm Sim</span></div>
    </div>
  </div>
  <div class="nav-section">Analyse</div>
  <a class="nav-item active" href="#overview"><span class="ico">▦</span>Übersicht</a>
  <a class="nav-item" href="#propfirm"><span class="ico">⬡</span>Prop Firm</a>
  <a class="nav-item" href="#montecarlo"><span class="ico">⟳</span>Monte Carlo</a>
  <div class="nav-section">Info</div>
  <a class="nav-item" href="#rules"><span class="ico">✓</span>Regeln</a>
  <a class="nav-item" href="#explain"><span class="ico">?</span>Erklärung</a>
  <div style="padding:18px 22px;margin-top:auto;position:absolute;bottom:0;left:0;right:0;border-top:1px solid var(--border)">
    <div style="font-size:10px;color:var(--muted)">Zeitraum</div>
    <div style="font-size:11px;font-weight:600;margin-top:3px">{str(all_dates[0])}</div>
    <div style="font-size:11px;font-weight:600">→ {str(all_dates[-1])}</div>
    <div style="font-size:10px;color:var(--muted);margin-top:4px">{len(all_dates):,} Handelstage</div>
  </div>
</nav>

<!-- MAIN -->
<main class="main">

<!-- TOP BAR -->
<div class="topbar" id="overview">
  <div>
    <h1>Prop Firm Dashboard</h1>
    <div class="sub">NQ Nasdaq · BOS + IFVG · 5m/15m · 2 Kontrakte · Stop {STOP_PTS:.0f}pts / Target {TGT_PTS:.0f}pts</div>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    {'<span class="badge">● Live-Simulation</span>' if net_profit>0 else '<span class="badge badge-warn">● Prüfen</span>'}
    <span style="font-size:11px;color:var(--muted)">{total:,} Trades</span>
  </div>
</div>

<!-- KPI ROW -->
<div class="kpi-row">
  <div class="kpi {'green' if capital_eur>CAPITAL else 'red'}">
    <div class="kpi-lbl">Endkapital</div>
    <div class="kpi-val" style="color:{'var(--green)' if capital_eur>CAPITAL else 'var(--red)'}">€{capital_eur:,.0f}</div>
    <div class="kpi-sub">Start: €{CAPITAL:,}</div>
  </div>
  <div class="kpi {'green' if net_profit>=0 else 'red'}">
    <div class="kpi-lbl">Netto P&amp;L</div>
    <div class="kpi-val" style="color:{'var(--green)' if net_profit>=0 else 'var(--red)'}">{'+'if net_profit>=0 else ''}€{net_profit:,.0f}</div>
    <div class="kpi-sub">ROI {net_profit/CAPITAL*100:+.0f}% · {len(all_dates)//365:.0f}J</div>
  </div>
  <div class="kpi {'red' if wr<be_wr else 'green'}">
    <div class="kpi-lbl">Win Rate</div>
    <div class="kpi-val" style="color:{'var(--red)' if wr<be_wr else 'var(--green)'}">{wr:.1%}</div>
    <div class="kpi-sub">Break-even: {be_wr:.1%}</div>
  </div>
  <div class="kpi {'green' if pf>=1 else 'red'}">
    <div class="kpi-lbl">Profit Factor</div>
    <div class="kpi-val" style="color:{'var(--green)' if pf>=1 else 'var(--red)'}">{pf:.2f}</div>
    <div class="kpi-sub">Ø {avg_pnl:+.0f}$/Trade</div>
  </div>
  <div class="kpi purple">
    <div class="kpi-lbl">Accounts</div>
    <div class="kpi-val" style="color:var(--purple)">{st['bought']}</div>
    <div class="kpi-sub">Pass: {st['evals_pass']/max(1,st['bought']):.0%} · Fail: {st['evals_fail']/max(1,st['bought']):.0%}</div>
  </div>
  <div class="kpi green">
    <div class="kpi-lbl">Payouts</div>
    <div class="kpi-val" style="color:var(--green)">{st['payouts']}</div>
    <div class="kpi-sub">Ø ${st['payout_usd']/max(1,st['payouts']):,.0f} · ∑${st['payout_usd']:,.0f}</div>
  </div>
</div>

<!-- PROP FIRM -->
<div class="sec" id="propfirm">
  <div class="sec-head">
    <h2>Prop Firm System</h2>
    <span class="pill">Kapitalwachstum · Payout-Zyklen · Funnel · Asymmetrie</span>
    <div class="divider"></div>
    <span class="tag tag-b">×{st['bought']} Accounts · {int(PAYOUT_SPLIT*100)}% Split</span>
  </div>

  <div class="card" style="margin-bottom:14px">{fig_html(fig_cap)}</div>

  <div class="card" style="margin-bottom:14px">{fig_html(fig_pay)}</div>

  <div class="g2">
    <div class="card">{fig_html(fig_fun)}</div>
    <div class="card">{fig_html(fig_asym)}</div>
  </div>
</div>

<!-- MONTE CARLO -->
<div class="sec" id="montecarlo">
  <div class="sec-head">
    <h2>Monte Carlo Simulation</h2>
    <span class="pill">{MC_RUNS} Block-Bootstrap-Runs · Blockgröße {BLOCK_SIZE} Tage</span>
    <div class="divider"></div>
    <span class="tag tag-g">P(Gewinn) {prob_profit:.0%}</span>
  </div>

  <!-- MC KPI cards -->
  <div class="mc-grid">
    <div class="mc-card">
      <div class="stripe" style="background:var(--red)"></div>
      <div class="mc-lbl">P5 – Worst 5%</div>
      <div class="mc-val" style="color:var(--red)">€{mc_p5:,.0f}</div>
      <div class="mc-sub">schlechteste 5% aller Pfade</div>
    </div>
    <div class="mc-card">
      <div class="stripe" style="background:var(--orange)"></div>
      <div class="mc-lbl">P25 – Quartil</div>
      <div class="mc-val" style="color:var(--orange)">€{mc_p25:,.0f}</div>
      <div class="mc-sub">25% der Simulationen darunter</div>
    </div>
    <div class="mc-card">
      <div class="stripe" style="background:var(--blue)"></div>
      <div class="mc-lbl">Median – P50</div>
      <div class="mc-val" style="color:var(--blue2)">€{mc_median:,.0f}</div>
      <div class="mc-sub">mittleres Ergebnis</div>
    </div>
    <div class="mc-card">
      <div class="stripe" style="background:var(--green)"></div>
      <div class="mc-lbl">P75 – Quartil</div>
      <div class="mc-val" style="color:var(--green)">€{mc_p75:,.0f}</div>
      <div class="mc-sub">75% der Simulationen darunter</div>
    </div>
    <div class="mc-card">
      <div class="stripe" style="background:var(--purple)"></div>
      <div class="mc-lbl">P95 – Best 5%</div>
      <div class="mc-val" style="color:var(--purple)">€{mc_p95:,.0f}</div>
      <div class="mc-sub">beste 5% aller Pfade</div>
    </div>
  </div>

  <!-- MC Legend -->
  <div class="mc-legend">
    <div style="font-size:11px;font-weight:700;color:var(--text);margin-right:4px">Legende:</div>
    <div class="mc-legend-item"><div class="swatch" style="background:rgba(99,102,241,0.25);height:12px;border-radius:2px"></div><span>P5–P95 <strong>90% Konfidenzband</strong></span></div>
    <div class="mc-legend-item"><div class="swatch" style="background:rgba(99,102,241,0.5);height:12px;border-radius:2px"></div><span>P25–P75 <strong>50% Mittelband (IQR)</strong></span></div>
    <div class="mc-legend-item"><div class="swatch" style="background:var(--blue2)"></div><span><strong>Median-Pfad</strong> (P50)</span></div>
    <div class="mc-legend-item"><div class="swatch dashed" style="color:var(--red)"></div><span><strong>P5</strong> – schlechteste 5%</span></div>
    <div class="mc-legend-item"><div class="swatch dashed" style="color:var(--green)"></div><span><strong>P95</strong> – beste 5%</span></div>
    <div class="mc-legend-item"><div class="swatch" style="background:rgba(88,166,255,0.08);height:12px;border-radius:2px"></div><span>Einzelne Sim-Pfade <strong>(transparent)</strong></span></div>
    <div style="margin-left:auto;font-size:11px;color:var(--muted)">Block-Bootstrap: Blöcke à {BLOCK_SIZE} Tage resampled → Regime-Cluster bleiben erhalten</div>
  </div>

  <div class="card" style="margin-bottom:14px">{fig_html(fig_mc)}</div>
  <div class="card">{fig_html(fig_mcd)}</div>

  <!-- MC Probability bar -->
  <div class="g3" style="margin-top:14px">
    <div class="card card-pad">
      <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:12px">Wahrscheinlichkeiten</div>
      <div class="stat-block">
        <div class="stat-row"><span class="stat-label">P(Kapital &gt; Start)</span><span class="stat-value" style="color:var(--green)">{prob_profit:.0%}</span></div>
        <div class="stat-row"><span class="stat-label">P(Kapital × 2)</span><span class="stat-value" style="color:var(--blue2)">{prob_2x:.0%}</span></div>
        <div class="stat-row"><span class="stat-label">P(Ruin &lt;30%)</span><span class="stat-value" style="color:var(--red)">{prob_ruin:.0%}</span></div>
      </div>
    </div>
    <div class="card card-pad">
      <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:12px">Kapital-Spanne (€)</div>
      <div class="stat-block">
        <div class="stat-row"><span class="stat-label">Worst Case (P5)</span><span class="stat-value" style="color:var(--red)">€{mc_p5:,.0f}</span></div>
        <div class="stat-row"><span class="stat-label">Median (P50)</span><span class="stat-value" style="color:var(--blue2)">€{mc_median:,.0f}</span></div>
        <div class="stat-row"><span class="stat-label">Best Case (P95)</span><span class="stat-value" style="color:var(--green)">€{mc_p95:,.0f}</span></div>
      </div>
    </div>
    <div class="card card-pad">
      <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:12px">Methodik</div>
      <div style="font-size:12px;color:var(--muted);line-height:1.8">
        <strong style="color:var(--text)">Block-Bootstrap</strong> resampled Blöcke à <strong style="color:var(--text)">{BLOCK_SIZE} Tage</strong> statt Einzeltagen.<br><br>
        Dadurch bleiben Markt-Regime (Trending, Choppy) als Cluster zusammen – realistische Sequenzrisiken sichtbar.
      </div>
    </div>
  </div>
</div>

<!-- ERKLÄRUNG -->
<div class="sec" id="explain">
  <div class="sec-head"><h2>Strategie-Erklärung</h2><div class="divider"></div></div>
  <div class="ex-grid">

    <div class="ex-card">
      <h3><span class="ico" style="background:rgba(34,211,165,.15);color:var(--green)">⚖</span>Options-Prinzip Prop Firm</h3>
      <p>Jeder Account-Kauf ist wie eine <span class="hl">Call-Option</span> auf einen profitablen Run. Begrenztes Risiko, unbeschränkter Upside:</p>
      <ul style="margin-top:8px">
        <li>Du zahlst: <span class="hl">€{ACCOUNT_COST}</span> (feste Prämie)</li>
        <li>Firma trägt: <span class="hl">${EVAL_MAX_DD:,}</span> Drawdown-Risiko</li>
        <li>Dein Upside: bis <span class="hl">${int(EXP_MAX_PAY*PAYOUT_SPLIT):,}</span> pro Zyklus</li>
        <li>Pass-Rate {st['evals_pass']/max(1,st['bought']):.0%} → Ø {st['payouts']/max(1,st['evals_pass']):.1f} Payouts/funded Acc</li>
      </ul>
      <div class="formula">Hebel = ${ACCOUNT_SIZE:,} ÷ €{ACCOUNT_COST} = {ACCOUNT_SIZE//ACCOUNT_COST:,}×
Kostet 1 Punkt dich: €{round(ACCOUNT_COST/(ACCOUNT_SIZE/CONTRACTS/PV),3):.3f} Risiko</div>
    </div>

    <div class="ex-card">
      <h3><span class="ico" style="background:rgba(99,102,241,.15);color:var(--blue2)">⟳</span>Fill at Open – Asymmetrie</h3>
      <p>Wenn eine Bar direkt in die IFVG-Zone öffnet, wird zum Open gefüllt – kein Warten auf das Limit-Level:</p>
      <ul style="margin-top:8px">
        <li>Open tiefer als Entry (Long) → <span class="hl">günstigerer Fill</span></li>
        <li>Smaller Distance to Stop → <span class="hl">weniger Verlust</span> bei Stop</li>
        <li>Larger Distance to Target → <span class="hl">mehr Gewinn</span> bei Treffer</li>
        <li>Zone Failure (Open beyond zone) → <span class="hl">Trade gecancelt</span>, kein Einstieg</li>
      </ul>
      <div class="formula">PF {pf:.2f} bei WR {wr:.1%}  →  Ø {avg_pnl:+.1f}$/Trade
Stop-Slippage: {SLIP_PTS}pt × $20 × {CONTRACTS} = ${int(SLIP_PTS*20*CONTRACTS)} extra Verlust</div>
    </div>

    <div class="ex-card">
      <h3><span class="ico" style="background:rgba(251,191,36,.15);color:var(--yellow)">✦</span>IFVG – Flip Zone Logik</h3>
      <p>Ein <span class="hl">Inverted Fair Value Gap</span> ist ein FVG das bereits berührt wurde – der Markt hat es "gefüllt" und es dreht jetzt als Flip-Zone:</p>
      <ul style="margin-top:8px">
        <li>FVG (3-Kerzen-Gap) muss vorher berührt worden sein</li>
        <li>Nur Richtung <span class="hl">gegen ursprünglichen FVG</span> (Inversion)</li>
        <li>15m-Trend mit 1-Bar Delay → <span class="hl">kein Look-Ahead</span></li>
        <li>Kein "Best IFVG" – immer <span class="hl">erster chronologischer</span> Kandidat</li>
      </ul>
      <div class="formula">Signal: 5m + 15m Trend aligned ≥ {TREND_STABLE} Bars stabil
Entry: IFVG-Mitte · Stop: ±{STOP_PTS:.0f}pt · Target: ±{TGT_PTS:.0f}pt</div>
    </div>

    <div class="ex-card">
      <h3><span class="ico" style="background:rgba(244,63,94,.15);color:var(--red)">⚑</span>Anti-Bias Maßnahmen</h3>
      <ul>
        <li><span class="hl">Kein Look-Ahead:</span> 15m-Trend shift(1), FVGs +1 Bar Delay</li>
        <li><span class="hl">Fill at Open:</span> Gap-Eröffnung → Fill zum Open-Preis</li>
        <li><span class="hl">Nur IFVG:</span> Muss vorher berührt worden sein</li>
        <li><span class="hl">Kein Optimierungs-Bias:</span> Erster Kandidat, kein Ranking</li>
        <li><span class="hl">Zone Failure:</span> Open beyond zone → Trade gecancelt</li>
        <li><span class="hl">Exit Close:</span> Offene Pos. zum Session-Ende geschlossen</li>
        <li><span class="hl">EOD Trailing DD:</span> Floor steigt mit Tages-Hochpunkt</li>
        <li><span class="hl">Consistency Rule:</span> Bester Tag ≤ {int(CONSISTENCY_CAP*100)}% Gesamtgewinn</li>
        <li><span class="hl">Stop-Slippage {SLIP_PTS}pt</span> auf jede Stop-Market Füllung</li>
      </ul>
    </div>
  </div>
</div>

<!-- RULES -->
<div class="sec" id="rules">
  <div class="sec-head"><h2>Alle Prop-Firm-Regeln</h2><div class="divider"></div></div>
  <div class="card card-pad">
    <div class="rules-grid">
      <div class="rule"><div class="rule-name">Eval Profit Target</div><div class="rule-val">${EVAL_TARGET:,} auf ${ACCOUNT_SIZE:,} Konto ({EVAL_TARGET/ACCOUNT_SIZE:.1%})</div></div>
      <div class="rule"><div class="rule-name">Max DD (EOD Trailing)</div><div class="rule-val">${EVAL_MAX_DD:,} – Floor steigt täglich mit Hochpunkt</div></div>
      <div class="rule"><div class="rule-name">Daily Loss Cap (Eval)</div><div class="rule-val">${EVAL_DAY_CAP:,} max. Tagesgewinn anrechenbar</div></div>
      <div class="rule"><div class="rule-name">Min. Handelstage</div><div class="rule-val">{EVAL_MIN_DAYS} Tage für Eval-Bestehen</div></div>
      <div class="rule"><div class="rule-name">Win-Days für Payout</div><div class="rule-val">{EXP_WIN_DAYS} Tage mit ≥${EXP_WIN_MIN} Gewinn erforderlich</div></div>
      <div class="rule"><div class="rule-name">Max. Payout / Zyklus</div><div class="rule-val">${int(EXP_MAX_PAY*PAYOUT_SPLIT):,} nach {int(PAYOUT_SPLIT*100)}% Split</div></div>
      <div class="rule"><div class="rule-name">Min. Payout</div><div class="rule-val">€{EXP_MIN_PAY*EUR_USD:.0f} (${EXP_MIN_PAY:.0f} vor FX @ {EUR_USD})</div></div>
      <div class="rule"><div class="rule-name">Consistency Rule</div><div class="rule-val">Bester Tag ≤ {int(CONSISTENCY_CAP*100)}% des Gesamtgewinns</div></div>
      <div class="rule"><div class="rule-name">Kommission</div><div class="rule-val">${COMMISSION:.0f} Round-Trip / {CONTRACTS} Kontrakte</div></div>
      <div class="rule"><div class="rule-name">Stop-Slippage</div><div class="rule-val">{SLIP_PTS}pt extra bei Stop-Market-Füllung (${int(SLIP_PTS*PV*CONTRACTS)}/Trade)</div></div>
      <div class="rule"><div class="rule-name">Max gleichz. Accounts</div><div class="rule-val">{MAX_ACCOUNTS} Accounts parallel erlaubt</div></div>
      <div class="rule"><div class="rule-name">Account-Kosten</div><div class="rule-val">€{ACCOUNT_COST}/Account · Gesamt: €{st['bought']*ACCOUNT_COST:,}</div></div>
    </div>
  </div>
</div>

<div class="footer">
  BOS + IFVG Prop Firm Simulator &nbsp;·&nbsp; {total:,} Trades &nbsp;·&nbsp; {len(all_dates):,} Handelstage &nbsp;·&nbsp; {str(all_dates[0])} – {str(all_dates[-1])} &nbsp;·&nbsp; Monte Carlo {MC_RUNS} Block-Bootstrap-Runs
</div>

</main>
</div>

<script>
// Smooth scroll for sidebar nav
document.querySelectorAll('.nav-item[href^="#"]').forEach(a=>{{
  a.addEventListener('click',e=>{{
    e.preventDefault();
    const t=document.querySelector(a.getAttribute('href'));
    if(t) t.scrollIntoView({{behavior:'smooth',block:'start'}});
    document.querySelectorAll('.nav-item').forEach(x=>x.classList.remove('active'));
    a.classList.add('active');
  }});
}});
// Highlight nav on scroll
const secs=[...document.querySelectorAll('[id]')].filter(e=>e.id);
const navItems=document.querySelectorAll('.nav-item[href^="#"]');
window.addEventListener('scroll',()=>{{
  let cur='';
  secs.forEach(s=>{{ if(window.scrollY+120>=s.offsetTop) cur=s.id; }});
  navItems.forEach(n=>{{
    n.classList.toggle('active',n.getAttribute('href')==='#'+cur);
  }});
}},{{passive:true}});
// Animate KPI values counting up
function animCount(el){{
  const target=parseFloat(el.dataset.target||'0');
  const fmt=el.dataset.fmt||'';
  const dur=1200; const steps=60; let i=0;
  const tick=()=>{{
    i++;
    const val=target*(i/steps);
    if(fmt==='eur') el.textContent='€'+Math.round(val).toLocaleString('de-DE');
    else if(fmt==='pct') el.textContent=(val*100).toFixed(1)+'%';
    else el.textContent=Math.round(val).toLocaleString('de-DE');
    if(i<steps) requestAnimationFrame(tick);
  }};
  requestAnimationFrame(tick);
}};
const obs=new IntersectionObserver(entries=>{{
  entries.forEach(e=>{{ if(e.isIntersecting){{ animCount(e.target); obs.unobserve(e.target); }} }});
}},{{threshold:.3}});
document.querySelectorAll('[data-target]').forEach(el=>obs.observe(el));
</script>

</body></html>"""

import pathlib; out=str(pathlib.Path(__file__).parent / "dashboard.html")
with open(out,"w",encoding="utf-8") as f:
    f.write(html)
print(f"\nDashboard: {out}")
subprocess.run(["open", out])
