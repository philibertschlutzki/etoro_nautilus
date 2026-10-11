---
ebc_prompt_id: EBC-H3
titel: "Exits und Shorts: broker-nativer Trailing-Stop, archetyp-spezifischer Trailing-Take-Profit, regime- und kostengegateter Short-Layer"
version: 1.0.0
erstellt_am: 2026-10-11
quelle_repository: https://github.com/philibertschlutzki/etoro_nautilus
quelle_commit: 5f737ed495e70eab85b2dbbb7673eb7369c428ce
beantwortet_fragen: [F3, F4]
konto_modus: nur_demo
stufen: [S0_setup, SA_historisch, SB_forward_shadow, SC_demo_mechanik]
mindestdauer: {SA: "Rechenzeit, typisch < 1 Tag", SB: ">= 30 Handelstage", SC: ">= 30 Stop-Ereignisse und >= 10 Short-Nächte je Klasse"}
ergebnisordner: results/H3/
primaere_endpunkte: [P3.1, P3.2, P3.3]
seed: 20261011
abhaengig_von: "EBC-H1 §4–§6 (Universum, Kosten, Archetypen) — wiederverwenden, falls vorhanden"
---

# EBC-H3 — Was Short, Trailing-Take-Profit und Trailing-Stop-Loss im Repository optimieren

## 0. Auftrag

Du prüfst die vorregistrierte Hypothese H3 gegen reale eToro-Daten und die reale Ausführungsmechanik des eToro-Demo-Kontos. Ergebnis: Verdikt `BESTAETIGT | WIDERLEGT | UNENTSCHIEDEN` mit allen Messwerten in `results/H3/`.

Beantwortete Fragen:
- **F3** — Was lässt sich mit Short-Positionen, Trailing-Take-Profit und Trailing-Stop-Loss im Repository optimieren?
- **F4** (Teil) — Fehlende Features: broker-native Exits (`IsTslEnabled`, `TakeProfitRate`, Teilschliessung), Short-Layer mit Regime-, Leerverkaufbarkeits- und Finanzierungsprüfung, Zeitbox je Archetyp, Ereignisfilter.

## 1. Harte Regeln

1. **Nur Demo.** Orders ausschliesslich über Demo-Endpunkte (`/api/v1/trading/execution/demo/...`) oder das von `automation/account_guard.py` verifizierte Paper-Konto; vor jeder Order `GET https://public-api.etoro.com/api/v1/me`, Abweichung ⇒ Abbruch.
2. **Konto-Isolation** wie EBC-H1 Regel 2 (eigenes Demo-Konto, sonst disjunkte Instrumente und Verwaltung je `positionId`, sonst Stufe C `BLOCKIERT`).
3. **Produktion unberührt.** Code- und Config-Änderungen (Varianten, Patches aus Anhang B) nur im Experiment-Klon auf Branch `ebc/h3`; nie pushen.
4. **Vorregistrierung** in `results/H3/preregistration.json` vor jeder Auswertung (Parameter, Seeds, Datumsgrenzen, Faktorstufen, SHA-256 dieser Datei).
5. **Stufe C Grössen:** Mechanik-Tests (C1, C4, C5) ≤ 50 USD je Order; C2/C3 ≤ 100 USD je Order, ≤ 20 gleichzeitige H3-Positionen. Jede Testposition spätestens nach Ende des Tests schliessen.
6. Rate-Limits einhalten; Keys nie loggen.

## 2. Hypothese

### 2.1 Behauptung (H3)

Im Repository bestimmt das Exit- und Seiten-Management die Netto-Rendite stärker als das Entry-Signal, und es ist pauschal statt archetyp- und anlageklassenspezifisch. Optimierbar sind drei Hebel:

1. **Trailing-Stop-Loss:** Der Stop wird heute auf dem Stunden-Schlusskurs geprüft und erst danach als Market-Order ausgeführt; beim Broker liegt nur ein weiter Katastrophen-Stop. Ein broker-nativer Trailing-Stop (eToro `IsTslEnabled`) mit gleicher Distanz verkleinert die Verlust-Tails bei nicht schlechterer Expectancy.
2. **Trailing-Take-Profit:** Ein einheitlicher TTP (Aktivierung 0,5 ATR, Rücklauf 0,3 ATR) verbessert Mean-Reversion, kappt aber den rechten Rand von Trend- und Ausbruchsstrategien und senkt dort die Expectancy.
3. **Short:** Shorts sind heute zufällig über die Strategien verteilt und teils unbeabsichtigt. Bedingungslose Aktien-Shorts sind netto negativ; ein Short-Layer nur im Abwärtsregime, nur auf leerverkaufbaren Nicht-Krypto-Instrumenten und mit gemessener Finanzierung hat positive Netto-Expectancy oder senkt den Portfolio-Drawdown.

**H0:** Der Bar-Close-Stop ist dem nativen Trailing-Stop nicht unterlegen; der TTP wirkt in allen Archetypen gleichgerichtet; bedingungslose Shorts sind nicht schlechter als regime-gegatete.

### 2.2 Vorhersagen (vorregistriert)

| ID | Vorhersage | Messgrösse | Schwelle | Rolle |
|---|---|---|---|---|
| P3.1 | Broker-Trailing-Stop (S1) schlägt Bar-Close-Stop (S0) im Tail ohne Expectancy-Verlust | Eintrags-gepaarte Differenzen (§7.1): CVaR5 der Trade-Renditen, Netto-Expectancy | CVaR5(S1) um ≥ 20 % besser **und** untere 90-%-KI-Grenze von Expectancy(S1) − Expectancy(S0) > −2 bps | primär |
| P3.2 | TTP wirkt archetyp-abhängig | Δ = Expectancy(P1) − Expectancy(P0) je Archetyp | Δ_M > 0, Δ_T < 0, Δ_V < 0 **und** Interaktion Δ_M − ½(Δ_T + Δ_V) mit unterer 90-%-KI-Grenze > 0 | primär |
| P3.3 | Gegatete Shorts tragen, bedingungslose Aktien-Shorts nicht | Netto-Expectancy der Short-Seite (Aktien) unter Z1 und Z2; ΔSR und ΔMaxDD des Portfolios durch Z2 gegenüber Z0 | Expectancy_Z1,Aktien < 0 (Punkt) **und** [Expectancy_Z2 > 0 **oder** MaxDD-Reduktion ≥ 15 % bei ΔSR ≥ 0] | primär |
| P3.4 | Die Short-Leaks sind real und kosten | Anteil unbeabsichtigter Shorts und deren Netto-Beitrag (§7.4) auf realen Stundendaten | Anteil > 0 reproduziert; Patch verbessert Expectancy oder senkt die Varianz der Strategie | sekundär |
| P3.5 | Optimale Zeitbox hängt vom Archetyp ab | SR_netto über max. Haltedauer {1, 3, 5, 10, 20, 40} Tage | T: Optimum ≥ 10; M: Optimum ≤ 5 | sekundär |
| P3.6 | Nativer TSL funktioniert nur mit dem dokumentierten Feld | Demo-Test C1 | `IsTslEnabled` ⇒ Stop-Level folgt dem Kurs; `IsTrailingStop` (Feldname des Adapters) ⇒ kein Nachziehen | sekundär |
| P3.7 | Modellierte Finanzierung stimmt | gemessene Overnight-Kosten (bps/Tag) je Klasse und Seite vs. 0,79 | Abweichung > 50 % ⇒ Kostenmodell anpassen, P3.3 neu rechnen | sekundär |

### 2.3 Widerlegung

H3 ist **widerlegt**, wenn (a) S0 bei CVaR5 **und** Expectancy besser ist als S1 (untere KI-Grenzen der Differenzen S0 − S1 > 0) **oder** (b) die TTP-Interaktion umgekehrt ist (obere 90-%-KI-Grenze von Δ_M − ½(Δ_T + Δ_V) < 0) **oder** (c) bedingungslose Aktien-Shorts eine höhere Netto-Expectancy als gegatete haben (untere KI-Grenze von Z1 − Z2 > 0). Sind primäre Vorhersagen unerfüllt, ohne dass ein Widerlegungskriterium greift: `UNENTSCHIEDEN`.

### 2.4 Begründung aus dem Repository (Stand `5f737ed`)

- **D1 Stop-Mechanik.** `_check_exits_and_update` vergleicht den Trailing-Stop mit `bar.close` (`automation/strategies/hourly_strategy_base.py:1277–1289`) und setzt danach eine Market-Order ab; der Docstring begründet das mit Whipsaw-Schutz und Backtest-Parität (`:1077–1083`). Beim Broker hängt nur der Katastrophen-Stop `SL:<pct>` mit `pct = clamp(3 · Stopdistanz, 2 %, 15 %)` (`:1426–1441`, `strategy_defaults.json` `_disaster_stop`). Die Kataloge ab #1063 massen realisierte Stop-Verluste weitgehend unabhängig von der Stopdistanz — vor dem Ingest-Fix #1330, daher hier neu zu messen.
- **D2 Broker-Trailing-Stop doppelt inaktiv.** Der Adapter setzt `payload["IsTrailingStop"] = True` nur bei Tag `TSL:1` und `ETORO_ENABLE_TSL=1` (`automation/adapters/etoro_execution.py:841–862`; `automation/momentum_ls_run.py:53`, Default `"0"`). Keine Strategie erzeugt `TSL:1`. Die eToro-Agent-Skill-Dokumentation nennt das Feld `IsTslEnabled` (`https://builders.etoro.com/skills/etoro-apps/SKILL.md`), nicht `IsTrailingStop`. Ebenfalls ungenutzt: `TakeProfitRate` (Tag `TP:`, `etoro_execution.py:809–820`) und Teilschliessung über `UnitsToDeduct`.
- **D3 Trailing-Take-Profit pauschal.** Paper-Overlay und Demo-Bot setzen für alle Strategien `trailing_tp_activation_atr = 0,5`, `trailing_tp_trail_atr = 0,3` (`automation/papertrading.py:211–212`, `automation/momentum_ls_run.py:309–313`). Mindestsicherung `2 · Spread + Round-Trip-Gebühr` (`hourly_strategy_base.py:1360–1367`), Aktivierung erst ab doppelter Mindestsicherung (`:268–288`): bei Krypto 230 bps Sicherung, also Aktivierung erst ab 460 bps Gewinn — innerhalb der 7-Stunden-Zeitbox ein Ereignis von etwa 2–3,5 σ der 7-Stunden-Bewegung (σ_1h 50–90 bps angenommen), also selten.
- **D4 Short-Verhalten der 14 aktiven Strategien.** Bedingungslos beidseitig: SmaCrossover, MeanReversion, DynamicBreakout, VwapExhaustion, TrendPullback, AdxAtrMomentum, HourlyMeanReversion (7). Per `allow_short: true`: ComboTrendVwap (`strategies.json:66`). Long-only per `allow_short = False`: SqueezeBreakout, OpeningRangeBreakout, DonchianRegimeBreakout, Rsi2Reversion (4). Unbeabsichtigt: VolatilityBreakoutPump, FlashCrashReversal (2, D5). Einzige Leerverkaufsprüfung: Krypto-Shorts werden blockiert (`hourly_strategy_base.py:854–870`; eToro errorCode 747, Commit `31372e5`). Finanzierung im Kostenmodell: Short pauschal 0,79 bps/Tag für alle Klassen, Long 0 — auch für Long-CFDs auf FX, Rohstoffe und Indizes (`backtest.json` `overnight_financing_bps_per_day_by_asset_class`).
- **D5 Short-Leaks reproduziert** (synthetische 24/7-Stundendaten, NautilusTrader 1.231.0, Repo-Code unverändert, Harness in Anhang A):
  - VolatilityBreakoutPump („Pump Rider"): nach Trailing-Stop- oder Zeit-Exit bleibt `current_signal == "BUY"` (keine `on_position_closed`-Überschreibung); das Exit-Signal „MOMENTUM LOST" (`automation/strategies/volatility_breakout.py:53–56`) eröffnet bei flacher Position einen Short. Seeds 1–3: 79/79, 77/77, 77/77 Long-/Short-Einstiege — die Hälfte aller Positionen sind Shorts, kein einziger Signal-Exit.
  - FlashCrashReversal: `current_signal = "BUY"` wird vor der Order gesetzt (`flash_crash_reversal.py:63`); wird der Entry blockiert (Test: `max_daily_trades = 1`; live analog Spread-Gate, Aggregat- und Risiko-Deckel), eröffnet das Erholungssignal (`:66–72`) einen Short: 14 von 79 bzw. 17 von 93 Positionen (18 %); ohne Blockade 0 Shorts.
- **D6 Einheitliche Zeitbox** ≤ 7 Stunden-Bars für alle Strategien und Klassen (`automation/optimizer/_contracts.py:76–88`).
- **D7 Ereignisrisiko.** Kein Earnings-/Dividenden-Kalender; Aktienpositionen mit Einstieg nach etwa 10:30 ET halten wegen der 7-Bar-Zeitbox über Nacht.

## 3. Schritt 0 — Setup und Preflight

Wie EBC-H1 §3 (Klon auf `5f737ed`, Branch `ebc/h3`, venv, `.env` nur Demo, `python -m automation.demo_order_check`, `results/H3/preflight.json`). Zusätzlich:

```bash
mkdir -p experiments/ebc_h3 results/H3
# Anhang A nach experiments/ebc_h3/short_leak_harness.py kopieren, dann:
python experiments/ebc_h3/short_leak_harness.py --strategy vbp --seeds 1 2 3
python experiments/ebc_h3/short_leak_harness.py --strategy fcr --seeds 1 2 --max-daily-trades 1
```

Erwartung (Referenzwerte aus §2.4 D5): `vbp` je Seed gleich viele Long- und Short-Einstiege; `fcr` mit `--max-daily-trades 1` Short-Anteil > 0. Abweichende Zahlen bei unverändertem Code ⇒ NautilusTrader-Version prüfen und dokumentieren, dann fortfahren.

## 4. Universum und Daten

Wie EBC-H1 §4 (`U_breit`, Seed 20261011, ≥ 750 Tageskerzen; vorhandene `results/H1/universe.csv` unverändert übernehmen und SHA-256 vorregistrieren). Tageskerzen mit Open/High/Low/Close sind Pflicht (Intrabar-Stops). Für §7.4 zusätzlich Stundenkerzen der Instrumente mit ≥ 60 Handelstagen Stundenhistorie.

## 5. Kostenmodell

Wie EBC-H1 §5. Für Shorts zählt die in Stufe C gemessene Finanzierung je Klasse und Seite (§9 C3); bis dahin 0,79 bps/Tag. Für Aktien-Shorts zusätzlich: Leerverkaufbarkeit je Instrument aus §9 C4 (nicht leerverkaufbar ⇒ Short-Signal verworfen).

## 6. Faktoren und Stufen

Entry-Signale: Archetypen T, M, V exakt nach EBC-H1 §6.1 auf der Tagesachse. Grundeinstellung, wo nicht variiert: S0, P0, Seitenregel aus EBC-H1 (Aktien/ETF/Krypto long, FX/Rohstoffe/Indizes beidseitig), Zeitstopps T 60 / M 5 / V 20 Tage.

| Faktor | Stufe | Definition |
|---|---|---|
| **S** Stop | S0 Status quo | Trailing-Stop `k · ATR20` vom besten Schlusskurs, Prüfung auf dem Schlusskurs, Ausführung zum nächsten Eröffnungskurs; Katastrophen-Stop `clamp(3 · Stopdistanz, 2 %, 15 %)` intrabar |
| | S1 Broker-TSL | eToro-Semantik (in C1 zu bestätigen): Distanz D = k · ATR20 beim Einstieg in Preis fixiert; Long: Stop = höchstes Hoch seit Einstieg − D, Auslösung intrabar bei Low ≤ Stop, Fill = min(Open, Stop); Short: Stop = tiefstes Tief + D, Auslösung bei High ≥ Stop, Fill = max(Open, Stop); jeweils plus Slippage |
| | S2 Kombination | S0 plus Broker-TSL mit Distanz 1,5 · D als Schutz |
| **P** Gewinnmitnahme | P0 | keine |
| | P1 Status quo Paper | TTP Aktivierung 0,5 ATR, Rücklauf 0,3 ATR, Mindestsicherung 2 · Spread + Round-Trip-Gebühr (`compute_trailing_tp_lock`) |
| | P2 weit | TTP 1,5 ATR / 1,0 ATR, gleiche Mindestsicherung |
| | P3 Teilschliessung | 50 % bei +1 R (R = Stopdistanz beim Einstieg), Rest mit dem Stop des Faktors S |
| **Z** Seite | Z0 | long-only auf allen Klassen |
| | Z1 bedingungslos | Short-Spiegelregeln der Archetypen auf allen Klassen ausser Krypto |
| | Z2 regime-gegatet | Short nur, wenn `C_t < SMA200_t` und SMA200 über 20 Tage fallend, Instrument leerverkaufbar (C4), nicht Krypto; Finanzierung gemessen |
| | Z3 | wie Z2, aber Aktien/ETFs long-only (Shorts nur FX/Rohstoffe/Indizes) |
| **H** Zeitbox | H ∈ {1, 3, 5, 10, 20, 40} | maximale Haltedauer in Handelstagen |

## 7. Stufe A — Historische Experimente (Tagesachse, OOS ab Kerze 251)

### 7.1 Eintrags-gepaarte Bewertung der Exits (P3.1, P3.2)
Jedes Entry-Signal wird **unabhängig** unter jeder Exit-Variante bis zum Ausstieg verfolgt (Überlappungen erlaubt). So entsteht je Signal eine gepaarte Differenz der Trade-Renditen zwischen Varianten.
- **E1 Stop:** T, M, V × {S0, S1, S2}, P0. Kennzahlen je Archetyp und Klasse: Netto-Expectancy (bps auf Notional), CVaR5 der Trade-Renditen, Verlust je Stop-Exit, Stop-Gap = (Fill − Stop-Level) / Entry, Anteil Whipsaws (Stop ausgelöst, Kurs schliesst am selben Tag wieder jenseits des Stops), Haltedauer.
- **E2 Gewinnmitnahme:** T, M, V × {P0, P1, P2, P3}, S0. Δ je Archetyp und Interaktion nach P3.2.
- Stundenachse ergänzend: die 14 Repo-Strategien mit Status-quo-Parametern auf allen Instrumenten mit Stundenhistorie unter S0 vs. S1 (Intrabar aus den O/H/L/C-Ticks der Stundenkerzen) — nur berichten.

### 7.2 Short-Layer (P3.3)
T, M, V × {Z0, Z1, Z2, Z3}, S0, P0, als Portfolio nach EBC-H1 §7.3 (2 % Notional je Position, ≤ 30 Positionen). Kennzahlen: Netto-Expectancy der Short-Trades je Klasse, Anteil der Finanzierung an den Short-Kosten, SR und MaxDD des Portfolios, Korrelation der Short-Seite zur Long-Seite in den 10 % schlechtesten Portfolio-Tagen (Absicherungswirkung).

### 7.3 Zeitbox (P3.5)
T, M, V × H ∈ {1, 3, 5, 10, 20, 40}, S0, P0, Z0. SR_netto je Stufe; Optimum je Archetyp.

### 7.4 Short-Leaks auf realen Daten (P3.4)
VolatilityBreakoutPump und FlashCrashReversal auf der Stundenachse aller Instrumente mit Stundenhistorie, Status-quo-Parameter (`strategy_defaults.json` + `PAPER_TRAILING_TP`), je einmal original und gepatcht (Anhang B). Für FlashCrashReversal zusätzlich mit den Live-Deckeln des Demo-Bots (`max_aggregate_open_positions`, Spread-Gate) als Blockadequelle. Kennzahlen: Anteil Short-Einstiege, Netto-Ergebnis der Shorts, Expectancy und Varianz der Strategie original vs. gepatcht.

### 7.5 Szenario-Matrix
Alle Kennzahlen je Regime R1–R4 und Ereignisklasse wie EBC-H1 §7.6; zusätzlich für Aktien: Positionen über Earnings-Termine (Termine aus öffentlichen Kalendern, nur zur Auswertung) getrennt ausweisen.

## 8. Stufe B — Forward-Shadow (≥ 30 Handelstage)

Vorregistriert und eingefroren vor dem Start:
- **H3-Exit-Profil:** S1 für alle Archetypen, P1 nur für M (T/V: P0), Shorts nach Z2 (bzw. Z3, falls Stufe A Z3 > Z2 zeigt — Regel vorab festlegen), Zeitbox je Archetyp = Optimum aus §7.3.
- **Status-quo-Exit-Profil:** S0, P1 für alle, Z1, Zeitbox 1 Handelstag.

Beide auf denselben Entry-Signalen, täglich nach Kerzenschluss deterministisch neu gerechnet, Fill zum nächsten Eröffnungskurs mit gemessenen Kosten. Wöchentlicher Zwischenbericht ohne Regeländerung.

## 9. Stufe C — Demo-Mechanik (parallel zu B)

- **C1 Feldname des Trailing-Stops (P3.6).** Zwei Demo-Orders à ≤ 50 USD auf einem liquiden Instrument (z. B. EURUSD oder Large-Cap-Aktie in der Session) über `POST /api/v1/trading/execution/demo/market-open-orders/by-amount`: (A) mit `"IsTslEnabled": true` und `StopLossRate` = aktueller Ask − D (Long), (B) mit `"IsTrailingStop": true` und derselben `StopLossRate`. Alle 5 Minuten über einen Handelstag `GET /api/v1/trading/info/demo/pnl` lesen und das Stop-Level beider Positionen protokollieren (`stage_c/tsl_field_check.csv`). Ergebnis: welches Feld wirkt; ob eToro die Distanz fix in Preis hält. Danach beide Positionen schliessen.
- **C2 Ausführungsqualität TSL (P3.1).** ≥ 30 Stop-Ereignisse über alle Klassen mit dem in C1 bestätigten Feld und Distanz D wie in S1; reicht die Zahl der Ereignisse nach 15 Handelstagen nicht, darf D für weitere Testpositionen auf 1 · ATR20 sinken (Stop-Slippage hängt kaum von der Distanz ab — Änderung dokumentieren). Je Ereignis: Stop-Level beim Auslösen, Schlusskurs der Position, Zeitpunkt; Stop-Slippage seitenbereinigt in bps auf den Entry (positiv = ungünstig: Long `(Stop − Close-Kurs)/Entry`, Short `(Close-Kurs − Stop)/Entry`). Gleichzeitig die Shadow-Variante S0 derselben Entries mitrechnen.
- **C3 Finanzierung (P3.7).** ≥ 10 Positions-Nächte je Klasse und Seite (Aktien-Short, FX long/short, Rohstoffe long/short, Indizes long/short), davon ≥ 1 Wochenende; Gebührenfelder täglich aus `…/demo/pnl` → bps/Tag je Klasse und Seite (`stage_c/financing.csv`).
- **C4 Leerverkaufbarkeit.** 30 Zufallsaktien aus `U_breit`: Short-Order à ≤ 20 USD, Status über `GET /api/v1/trading/info/demo/orders/{orderId}` (eToro lehnt teils asynchron ab), angenommene Positionen sofort schliessen. Ergebnis: Quote leerverkaufbarer Aktien (`stage_c/shortability.csv`), Fehlercodes.
- **C5 Teilschliessung.** Eine Position à ≤ 50 USD öffnen und über `POST /api/v1/trading/execution/demo/market-close-orders/positions/{positionId}` mit `{"InstrumentId": <id>, "UnitsToDeduct": <halbe Stückzahl der Position>}` halbieren; Restposition und Gebühren dokumentieren (Voraussetzung für P3).
- **Kosten-Rekalibrierung:** Weichen gemessene Finanzierung, Slippage oder Stop-Gap um > 2 bps bzw. > 20 % vom Modell ab, rechnest du die betroffenen Experimente aus §7 mit den gemessenen Werten neu; das Verdikt stützt sich auf die rekalibrierte Rechnung.

## 10. Metriken (Definitionen)

- Trade-Rendite netto in bps auf das Entry-Notional, inkl. Spread, Gebühren, Slippage, Finanzierung.
- `CVaR5` = Mittelwert der schlechtesten 5 % der Trade-Renditen; Verbesserung in % = (CVaR5_S0 − CVaR5_S1) / |CVaR5_S0|.
- `R` = Stopdistanz beim Einstieg in bps; Expectancy in R ergänzend berichten.
- Portfolio: `SR = mean(r)/std(r) · √252`, MaxDD, CVaR5 der Tagesrenditen.

## 11. Statistik und Entscheidungsregel

- Trade-Ebene: Cluster-Bootstrap nach Kalendermonat der Einträge (5000 Replikate, Seed 20261011) für gepaarte Differenzen und die TTP-Interaktion; 90-%-Perzentil-KI.
- Portfolio-Ebene: gepaarter Stationary Bootstrap (`automation.optimizer.bootstrap.stationary_bootstrap_indices`, Blocklänge `optimal_block_length`).
- Stufe B und C dürfen nur abschwächen: zeigt C2 eine mittlere Stop-Slippage, die den S1-Vorteil aus P3.1 vollständig aufzehrt, oder C1, dass eToro den Stop nicht nachzieht, wird P3.1 als nicht erfüllt gewertet.

| Verdikt | Bedingung |
|---|---|
| `BESTAETIGT` | P3.1, P3.2 und P3.3 erfüllt, Stufen B/C widersprechen nicht |
| `WIDERLEGT` | Kriterium aus §2.3 erfüllt |
| `UNENTSCHIEDEN` | sonst; nenne die fehlende Evidenz |

## 12. Ausgaben

```
results/H3/
  preregistration.json   preflight.json   universe.csv   costs_calibration.json
  stage_a/ exits_paired.parquet   (signal_id, archetyp, klasse, variante, rendite_bps, exit_grund, haltedauer)
           stop_summary.csv   ttp_interaction.json   short_layer.csv   timebox.csv
           leaks_real_data.csv   regimes.csv
  stage_b/ forward_daily_returns.csv   weekly_<KW>.md
  stage_c/ tsl_field_check.csv   tsl_events.csv   financing.csv   shortability.csv
           partial_close.json   fills.csv   cost_recalibration.json
  metrics.json   verdict.json   report.md
```

`verdict.json` mit denselben Feldern wie EBC-H1 (`hypothesis: "H3"`), zusätzlich:

```json
{
  "tsl": {"field_effective": "IsTslEnabled|IsTrailingStop|keines", "distance_fixed_in_price": true, "stop_slippage_bps_p50": 0.0},
  "ttp_delta_bps": {"T": 0.0, "M": 0.0, "V": 0.0, "interaction_ci90": [0.0, 0.0]},
  "shorts": {"expectancy_bps": {"Z1_equity": 0.0, "Z2_equity": 0.0, "Z2_fx": 0.0}, "maxdd_change": 0.0, "shortable_share_equity": 0.0},
  "financing_bps_per_day": {"EQUITY": {"short": 0.0}, "FOREX": {"long": 0.0, "short": 0.0}, "COMMODITY": {}, "INDEX": {}},
  "leaks": {"vbp_short_share_original": 0.0, "vbp_short_share_patched": 0.0, "fcr_short_share_original": 0.0},
  "optimal_timebox_days": {"T": 0, "M": 0, "V": 0}
}
```

`report.md` (Deutsch, ≤ 3 Seiten): Verdikt, Antwort auf F3 je Hebel (Short, TTP, TSL) mit gemessenem Effekt, Ergebnis der Demo-Mechanik-Tests.

## 13. Folgen je Verdikt

- **BESTAETIGT:** Issues im Katalogformat des Repos: (1) Adapter auf `IsTslEnabled` umstellen, Strategien setzen `TSL:1` mit Distanz aus `k · ATR`, Backtest-Modell S1 als Option mit Paritätstest; (2) TTP nur für Mean-Reversion-Strategien, Aktivierung aus Kostenquote; Teilschliessung über `UnitsToDeduct`; (3) Short-Layer: `allow_short` für alle Strategien explizit (Default `False`), Regime-Gate, Leerverkaufbarkeit je Instrument, gemessene Finanzierung je Instrument und Seite im Kostenmodell; (4) Short-Leaks schliessen (Anhang B); (5) Zeitbox je Archetyp statt einheitlich 7 Bars; (6) Ereignisfilter für Overnight-Halte über Earnings.
- **WIDERLEGT:** Bar-Close-Logik beibehalten; prüfen, ob der Vorteil von S0 aus vermiedenen Whipsaws stammt (Anteil Whipsaws je Klasse), und die Distanz k je Klasse statt die Mechanik anpassen.
- **UNENTSCHIEDEN:** Stufe C bis ≥ 60 Stop-Ereignisse verlängern; Short-Experimente auf zusätzliche 150 Aktien ausdehnen.

## 14. Ablauf-Checkliste

- [ ] S0 Setup, Preflight, Isolation; Harness aus Anhang A reproduziert die Referenzwerte
- [ ] Universum, Daten (O/H/L/C), Kosten
- [ ] `preregistration.json`
- [ ] §7.1 Exits gepaart, §7.2 Short-Layer, §7.3 Zeitbox, §7.4 Leaks auf realen Daten, §7.5 Regime
- [ ] Stufe B gestartet; C1, C4, C5 erledigt; C2, C3 laufen bis zur Mindestzahl
- [ ] Kosten-Rekalibrierung geprüft
- [ ] `verdict.json`, `report.md`

---

## Anhang A — Short-Leak-Harness (`experiments/ebc_h3/short_leak_harness.py`)

```python
"""EBC-H3 Anhang A — Short-Leak-Reproduktion mit unverändertem Repo-Code.

Aufruf im Root des Experiment-Klons:
  python experiments/ebc_h3/short_leak_harness.py --strategy vbp --seeds 1 2 3
  python experiments/ebc_h3/short_leak_harness.py --strategy fcr --seeds 1 2 --max-daily-trades 1
"""
import argparse
import collections
import os
import sys

import numpy as np

sys.path.insert(0, os.getcwd())
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig  # noqa: E402
from nautilus_trader.config import LoggingConfig  # noqa: E402
from nautilus_trader.model.currencies import USD  # noqa: E402
from nautilus_trader.model.data import QuoteTick  # noqa: E402
from nautilus_trader.model.enums import AccountType, AssetClass, OmsType  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId, Venue  # noqa: E402
from nautilus_trader.model.instruments import Cfd  # noqa: E402
from nautilus_trader.model.objects import Money, Price, Quantity  # noqa: E402

HOUR_NS = 3_600_000_000_000


def synthetic_ticks(iid, seed: int, shock: float):
    """24/7-Stundenpfad: Random Walk, 25 Schocks über 3 Stunden (+ Pump / − Crash), danach Gegenbewegung."""
    rng = np.random.default_rng(seed)
    hours = 24 * 120
    r = rng.normal(0.0, 0.006, hours)
    for k in rng.choice(np.arange(50, hours - 30), size=25, replace=False):
        r[k:k + 3] += shock
        r[k + 3:k + 15] -= shock / 3.0
    mid = 100.0 * np.exp(np.cumsum(r))
    t0 = 1_700_000_000_000_000_000
    out = []
    for h in range(hours):
        for q in range(4):  # 4 Quotes je Stunde, wie die O/H/L/C-Ticks des Katalogs
            ts = t0 + h * HOUR_NS + q * (HOUR_NS // 4)
            m = mid[h] * (1.0 + rng.normal(0.0, 0.0008))
            out.append(QuoteTick(instrument_id=iid, bid_price=Price(round(m * 0.9998, 2), 2),
                                 ask_price=Price(round(m * 1.0002, 2), 2), bid_size=Quantity(1e6, 2),
                                 ask_size=Quantity(1e6, 2), ts_event=ts, ts_init=ts))
    return out


def run(strategy: str, seed: int, max_daily_trades: int) -> dict:
    iid = InstrumentId.from_str("SIMLEAK.ETORO")
    inst = Cfd(instrument_id=iid, raw_symbol=iid.symbol, asset_class=AssetClass.EQUITY, quote_currency=USD,
               price_precision=2, size_precision=2, price_increment=Price(0.01, 2),
               size_increment=Quantity(0.01, 2), ts_event=0, ts_init=0)
    common = dict(instrument_id=str(iid), bar_type=f"{iid}-1-HOUR-MID-INTERNAL", bb_period=10, bb_std_dev=2.0,
                  trade_amount_pct=15.0, disaster_stop_mode="simulated_order", session_window=None,
                  max_daily_trades=max_daily_trades)
    if strategy == "vbp":
        from automation.strategies.volatility_breakout import (VolatilityBreakoutConfig as Cfg,
                                                               VolatilityBreakoutPumpStrategy as Strat)
        cfg = Cfg(**common, atr_trailing_multiplier=1.5, max_bars_in_trade=7, cooldown_bars=0)
        shock = +0.012
    else:
        from automation.strategies.flash_crash_reversal import (FlashCrashReversalConfig as Cfg,
                                                                FlashCrashReversalStrategy as Strat)
        cfg = Cfg(**common, rsi_period=7, rsi_oversold=30.0, atr_trailing_multiplier=0.75, max_bars_in_trade=4)
        shock = -0.012
    eng = BacktestEngine(config=BacktestEngineConfig(trader_id="LEAK-001",
                                                     logging=LoggingConfig(log_level="ERROR")))
    eng.add_venue(venue=Venue("ETORO"), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                  base_currency=USD, starting_balances=[Money(10_000, USD)])
    eng.add_instrument(inst)
    eng.add_data(synthetic_ticks(iid, seed, shock))
    eng.add_strategy(Strat(config=cfg))
    eng.run()
    positions = eng.trader.generate_positions_report()
    fills = eng.trader.generate_order_fills_report()
    eng.dispose()
    sides = collections.Counter(positions["entry"].astype(str)) if len(positions) else collections.Counter()
    reasons = collections.Counter()
    for tag in (fills["tags"] if len(fills) and "tags" in fills else []):
        text = str(tag)
        for key in ("TRAILING_STOP", "TIME_BOX", "SIGNAL_REVERSAL", "DISASTER_STOP", "PROFIT_TARGET"):
            if f"EXIT_REASON:{key}" in text:
                reasons[key] += 1
    return {"strategy": strategy, "seed": seed, "max_daily_trades": max_daily_trades,
            "positions": int(len(positions)), "long_entries": sides.get("BUY", 0),
            "short_entries": sides.get("SELL", 0), "exit_reasons": dict(reasons)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=["vbp", "fcr"], required=True)
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--max-daily-trades", type=int, default=5)
    args = ap.parse_args()
    for s in args.seeds:
        print(run(args.strategy, s, args.max_daily_trades))
```

Referenzausgabe (NautilusTrader 1.231.0, Commit `5f737ed`): `vbp` Seeds 1/2/3 → 79/79, 77/77, 77/77 Long/Short, Exits nur `TRAILING_STOP` und `TIME_BOX`; `fcr` Seeds 1/2 ohne Blockade → 102/0, 126/0; mit `--max-daily-trades 1` → 65/14, 76/17.

## Anhang B — Patch der Short-Leaks (nur im Experiment-Klon, für §7.4)

```python
# automation/strategies/volatility_breakout.py
class VolatilityBreakoutConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    bb_period: int = 20
    bb_std_dev: float = 2.0
    allow_short: bool = False            # neu: Short-Entry nur explizit

class VolatilityBreakoutPumpStrategy(HourlyStrategyBase):
    ...
    def _on_sell_signal(self, bar):
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            ...                           # unverändert: Long schliessen
            return
        if not self.config.allow_short:   # neu: Exit-Signal eröffnet nie eine Gegenposition
            self.current_signal = None
            return
        ...                               # unverändert

    def on_position_closed(self, event) -> None:   # neu
        super().on_position_closed(event)
        self.current_signal = None

# automation/strategies/flash_crash_reversal.py — dieselbe allow_short-Sperre in _on_sell_signal;
# zusätzlich current_signal = "BUY" erst setzen, nachdem _on_buy_signal tatsächlich eine Order abgesetzt hat
# (z. B. _on_buy_signal gibt True zurück, on_bar setzt den Zustand nur dann).
```
