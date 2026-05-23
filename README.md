# BOS + IFVG · Prop Firm Simulator

## Idee

Da „Prop Firms" (Proprietary Trading) in den letzten Jahren stark an Bedeutung gewonnen haben und es ermöglichen, mit wenig Eigenkapital am Futures-Markt zu partizipieren, habe ich systematisch getestet, ob und unter welchen Bedingungen man mit Prop Firms profitabel sein kann — auch mit einer Strategie, die isoliert betrachtet unter der Break-even-Schwelle liegt.

Als Grundlage dienten €7.000 Startkapital, mit dem die Simulation Evaluationsphasen kaufen konnte. Als Beispiel wurde die **Lucid Flex 50K** (€90 pro Account) verwendet — bis zu 5 Accounts gleichzeitig, synchron mit derselben Strategie betrieben.

---

## Regeln der Prop Firm

Jeder Account durchläuft zwei Phasen.

**Evaluation:**
Auf einem virtuellen $50.000-Konto muss ein Profit-Ziel von $3.000 erreicht werden, ohne den maximalen Tagesverlust von $2.000 (EOD Trailing Drawdown) zu überschreiten. Pro Tag werden maximal $1.500 angerechnet. Mindestens 2 Handelstage sind Pflicht.

**Express (Funded):**
Nach 5 Gewinntagen mit jeweils mindestens $150 wird ein Payout ausgelöst — maximal $2.000, davon 90% an den Trader. Nach jedem Payout setzt der Drawdown-Floor zurück. Scheitert ein Account, wird sofort ein neuer gekauft und alle Payout-Erlöse werden reinvestiert.

---

## Die Strategie

Gehandelt wird NQ Nasdaq Futures auf Basis von **BOS (Break of Structure)** und **IFVG (Inverted Fair Value Gap)**. Der Dual-Timeframe-Ansatz (5m + 15m) stellt sicher, dass nur in Richtung des übergeordneten Trends gehandelt wird. Ein Signal entsteht, wenn der Preis eine IFVG-Zone — eine zuvor berührte und damit invertierte FVG-Zone — in Trendrichtung schließt. Gehandelt wird ausschließlich in definierten Sessions (08:35–10:30 Uhr und 12:30–14:00 Uhr CT), maximal 1 Trade pro Session.

| Parameter | Wert |
|---|---|
| Stop-Loss | 16 Punkte |
| Take-Profit | 12 Punkte |
| RR-Ratio | 0.75 |
| Kontrakte | 4 NQ Mini |
| Commission | $16 Round-Trip |
| Stop-Slippage | 0.5 Punkte |

---

## Ziel & warum Low RR

Das bewusst gewählte RR von 0.75 ergibt eine Break-even-Schwelle von **57.1%**. Die tatsächliche Win Rate der Strategie liegt bei **51.9%** — sie ist damit messbar unprofitabel. Das war Absicht: Es sollte getestet werden, ob die Prop Firm Struktur alleine ausreicht, um aus einem negativen Erwartungswert einen positiven Gesamtoutput zu machen. Das niedrige RR erhöht zudem die Wahrscheinlichkeit, das Profit-Ziel der Evaluation zu erreichen, ohne den Drawdown zu reißen.

---

## Ergebnisse

| Kennzahl | Wert |
|---|---|
| Zeitraum | 5 Jahre (Mai 2021 – Mai 2026) |
| Trades gesamt | 565 |
| Win Rate | 51.9% (Break-even: 57.1%) |
| Profit Factor | 1.10 |
| Ø P&L / Trade | +$47.43 |
| Accounts gekauft | 310 × €90 = €27.900 |
| Eval-Erfolgsquote | 34% (200 gescheitert · 105 bestanden) |
| Payout-Zyklen | 75 |
| Gesamt Payouts (USD) | $100.919 |
| Gesamt Payouts (EUR) | €93.443 |
| Startkapital | €7.000 |
| Endkapital | €72.543 |
| Netto-Gewinn | +€65.543 |
| ROI (5 Jahre) | **+936%** |
| ROI (p.a.) | +187% |

**Jahr für Jahr (Raw P&L, 1 Account):**

| Jahr | P&L | Win Rate | Trades |
|---|---|---|---|
| 2021 | −$514 | 50% | 62 |
| 2022 | +$17.973 | 57% | 103 |
| 2023 | +$10.178 | 54% | 112 |
| 2024 | +$4.695 | 54% | 127 |
| 2025 | +$48 | 48% | 119 |
| 2026 | −$5.580 | 43% | 42 |

Trotz negativer Strategie-Erwartung entsteht ein massiver Gewinn — durch drei Mechanismen:

1. **Fill-at-Open-Asymmetrie** — öffnet der Markt günstig innerhalb der IFVG-Zone, entsteht ein kleinerer Verlust bei Stop und ein größerer Gewinn bei Target. Das erklärt den positiven Ø P&L von +$47/Trade trotz negativer Win Rate.
2. **Prop Firm Hebelasymmetrie** — jeder Account wirkt wie eine Call-Option: €90 Risiko, $50.000 Kaufkraft, bis zu $1.800 Netto-Payout pro Zyklus.
3. **Cascade-Reinvestment** — alle Payouts werden sofort in neue Accounts reinvestiert, bis zu 5 laufen parallel. Dieses Compounding ist der Haupttreiber des ROI.

---

## Kein Look-Ahead Bias · Realistische Fills

Besonderes Augenmerk lag auf der Vermeidung von Simulations-Fehlern:

- Der 15m-Trend wird um 1 Bar geshiftet, bevor er auf den 5m-Chart gemappt wird — der aktuelle, noch offene Bar ist nie sichtbar.
- FVG-Zonen werden erst ab dem Bar *nach* ihrer Entstehung als aktiv markiert.
- Swing-Hochs und -Tiefs gelten erst als bestätigt, wenn n Folgebars vergangen sind (kein zukünftiger Bar wird verwendet).
- Bei der Fill-Logik gilt: öffnet der nächste Bar außerhalb der Zone, wird der Trade gecancelt statt trotzdem gefüllt.
- Stop-Losses erhalten 0.5 Punkte Slippage; Stop hat immer Priorität über Target auf demselben Bar (konservative Annahme).
- Bei mehreren aktiven IFVGs gleichzeitig wird immer die chronologisch älteste Zone genommen — kein Ranking, kein Auswählen des „besten" Signals.

---

## Fazit

Prop Firms sind kein Ersatz für eine profitable Strategie — aber sie verändern das Risiko-Rendite-Profil fundamental. Der Trader riskiert pro Account €90, hat aber Zugriff auf $50.000 Kaufkraft mit asymmetrischem Upside. Selbst eine Strategie, die unter normalen Bedingungen Geld verlieren würde, kann durch diesen Hebel, Cascade-Reinvestment und parallelen Betrieb mehrerer Accounts einen dreistelligen prozentualen Return erzeugen.

Die größten Risiken:
- 65% aller Evaluations-Versuche scheitern
- Die Performance hängt stark von Marktregimes ab — ab 2024 wurde der NQ deutlich choppiger
- Das System hängt an der Prop Firm Struktur, nicht an der Strategie selbst
- Historische Performance garantiert keine zukünftigen Ergebnisse

---

## Voraussetzungen

```
pip install pandas numpy plotly
```

NQ 1-Minuten CSV-Datei im selben Ordner ablegen.

**CSV-Format (Semikolon-getrennt):**
```
dd/mm/yyyy HH:MM:SS;open;high;low;close;volume
```

---

## Ausführung

```bash
python3 prop_sim_v2.py   # Simulation + Konsolenausgabe
python3 dashboard.py     # Simulation + interaktives HTML-Dashboard
```

---

## Parameter-Übersicht

```python
STOP_PTS     = 16.0    # Stop-Loss in Punkten
TGT_PTS      = 12.0    # Take-Profit in Punkten
CONTRACTS    = 4       # NQ Mini Kontrakte
SLIP_PTS     = 0.5     # Stop-Slippage in Punkten
COMMISSION   = 16.0    # $ Round-Trip

SWING_N5     = 3       # Swing-Bestätigung 5m
SWING_N15    = 2       # Swing-Bestätigung 15m
MIN_SWING5   = 15.0    # Mindest-Swing 5m (Punkte)
MIN_SWING15  = 20.0    # Mindest-Swing 15m (Punkte)
TREND_STABLE = 6       # Mindest-Bars stabiler Trend
FVG_MIN5     = 5.0     # Mindest-FVG 5m (Punkte)
FVG_MIN15    = 8.0     # Mindest-FVG 15m (Punkte)
FVG_LB5      = 12      # FVG Lookback 5m (Bars)
FVG_LB15     = 40      # FVG Lookback 15m (Bars)

ACCOUNT_SIZE = 50000   # Virtuelles Konto ($)
ACCOUNT_COST = 90      # Einmalige Gebühr (€)
EVAL_TARGET  = 3000    # Profit-Ziel Eval ($)
EVAL_MAX_DD  = 2000    # Max EOD Trailing DD ($)
EVAL_DAY_CAP = 1500    # Max Tagesgewinn Eval ($)
EXP_WIN_DAYS = 5       # Gewinntage für Payout
EXP_WIN_MIN  = 150     # Mindestgewinn Gewinntag ($)
EXP_MAX_PAY  = 2000    # Max Payout pro Zyklus ($)
PAYOUT_SPLIT = 0.90    # Trader-Anteil
MAX_ACCOUNTS = 5       # Max parallele Accounts
```

---

*Dieser Code ist ein Backtesting-Tool zu Bildungszwecken. Historische Ergebnisse sind keine Garantie für zukünftige Performance. Prop Firm Trading beinhaltet das Risiko des vollständigen Verlusts des eingesetzten Kapitals.*
