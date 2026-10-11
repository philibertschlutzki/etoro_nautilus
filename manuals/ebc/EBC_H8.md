---
ebc_prompt_id: EBC-H8
titel: "Realer Durchsatz: B++ als täglicher REST-Executor im eToro-Paper-Konto — Trades, Kosten, Tracking und Ausführungszeitpunkt"
version: 1.0.0
erstellt_am: 2026-10-11
quelle_repository: https://github.com/philibertschlutzki/etoro_nautilus
quelle_commit: 40671219c3e007ea268c5d48ff5573cafaef88a7
beantwortet_fragen: [F5, F4]
ziele: {gewinn: "realisieren", verluste: "realisieren", trades: "realisieren"}
konto_modus: nur_demo
freigabe_erforderlich: true
stufen: [S0_setup, SC0_mechanik, SC_paper_ausfuehrung, SB_schatten_parallel]
mindestdauer: {SC0: "1 Handelstag", SC: ">= 40 Handelstage und >= 150 reale Round-Trips", SB: "parallel zu SC"}
ergebnisordner: results/H8/
primaere_endpunkte: [P8.1, P8.2, P8.3]
seed: 20261011
abhaengig_von: "Verdikte EBC-H4 bis EBC-H7 (Zusammensetzung von B++ nach §6.1); ohne bestätigte Hebel gilt B+ aus EBC-H1"
---

# EBC-H8 — Realer Durchsatz von B++ im Paper-Konto

## 0. Auftrag

Du führst B++ — B+ plus alle in EBC-H4 bis EBC-H7 bestätigten Hebel — mit einem täglichen REST-Executor im eToro-Paper-Konto aus und misst, ob die Modellgewinne, die Verlustbegrenzung und die Zahl der Trades real erreichbar sind. Parallel läuft derselbe Regelsatz als Schatten mit Modell-Fills. Ergebnis: Verdikt `BESTAETIGT | WIDERLEGT | UNENTSCHIEDEN` mit allen Messwerten in `results/H8/`.

Beantwortete Fragen:
- **F5** — Gewinne maximieren, Verluste reduzieren, Trades erhöhen: Hier wird geprüft, ob die in H1–H7 im Modell gemessenen Effekte im realen Paper-Handel ankommen.
- **F4** (Teil) — Fehlende Features: Ausführungspfad für Tagesachsen-Strategien, Kosten- und Stop-Messung im Betrieb, Budget-Trennung zwischen Bots auf einem Konto.

## 1. Harte Regeln

1. **Freigabe.** Orders erst nach ausdrücklicher schriftlicher Freigabe durch den Nutzer. Die Freigabe legt fest: (a) die Konto-Variante nach Regel 3, (b) das H8-Budget E₀ (Standard 10 000 USD Paper-Geld, höchstens das Guthaben des Kontos), (c) das Startdatum. Ohne Freigabe nur Schritt 0 und der Trockenlauf (`--dry-run`, keine Orders).
2. **Nur Demo/Paper.** Vor jedem Lauf und vor jeder Order `account_guard.verify_paper_account` (`GET https://public-api.etoro.com/api/v1/me` muss genau die angeheftete `realCid` liefern), Endpunkte aus `automation/adapters/etoro_execution.py` (`_REST_BASE[env]`, Info-Pfade `/api/v1/trading/info/<env>/…`). Abweichung ⇒ Lauf abbrechen, keine Order, Vorfall melden.
3. **Konto-Isolation.** Der Allocator des Inkubations-Bots rechnet seit Commit `09509cb` mit allen offenen Positionen des Kontos: keine Einstiege in Instrumente mit offener Position (`automation/momentum_ls_allocator.py:144`), Zahl und Notional aller Positionen (`:171–172`), Restbudget gegen den 60-%-Deckel (`:189`); `daily_orchestrator._open_position_symbols` (`automation/daily_orchestrator.py:1627`) hält Symbole mit offener Position fest. Beim Start übernimmt der Adapter zudem alle offenen eToro-Positionen auf Instrumenten seines Universums und unterstellt sie dem Bot-Stop, dem Trailing-Take-Profit und dem Exposure-Deckel (`automation/adapters/etoro_execution.py:243–283`, Commit `d198415`). H8-Positionen im selben Konto verbrauchen also das Budget des Inkubations-Bots, sperren dessen Instrumente und können nach einem Neustart von ihm geschlossen werden — und umgekehrt. Zulässig ist nur eine der Varianten: **(a)** eigenes Demo-Konto (eigener Key, eigene `ETORO_PAPER_ACCOUNT_CID`); **(b)** Inkubations-Bot für die Dauer von H8 pausiert; **(c)** H8 handelt nur Instrumente, die nicht in `automation/config/instrument_map.json` stehen (dort stehen alle Instrumente, die der Bot laden und übernehmen kann), mit einem vom Nutzer festgelegten Teilbudget; die Beeinflussung des Inkubations-Bots über den gemeinsamen Exposure-Deckel wird in `protocol_deviations` dokumentiert. H8 verwaltet Positionen ausschliesslich über ihre `positionId` und schliesst nie eine Position, die es nicht selbst eröffnet hat (Register `results/H8/stage_c/positions.jsonl`).
4. **Grössen.** Notional je Order nach der B++-Regel (§6.1) bezogen auf die H8-Equity (E₀ plus realisierte und unrealisierte P&L der H8-Positionen), höchstens 200 USD; Gesamt-Exposure ≤ 60 % der H8-Equity; `Leverage = 1`. Jede Einstiegs-Order trägt eine `StopLossRate` (Katastrophen-Stop nach B++); ohne Stop keine Order.
5. **Produktion unberührt.** Executor nur unter `experiments/ebc/`, eigener Lock (`flock`), eigenes Log unter `logs/ebc_h8/`; keine Änderung an `momentum_ls_run.py`, `daily_orchestrator.py` oder `automation/config/*.json`.
6. **Vorregistrierung** in `results/H8/preregistration.json` vor dem ersten Handelstag: B++-Zusammensetzung nach §6.1 mit den Verdikten und den SHA-256 der `verdict.json` von H4–H7, Executor-Commit, Randomisierungsregel, Zeitplan, Schwellen, SHA-256 dieser Datei.
7. **Rate-Limits** ≤ 1 Request/s, Backoff wie `experiments/ebc/api.py`; Keys nie loggen.
8. **Not-Aus.** Existiert `results/H8/STOP`, sendet der Executor keine Einstiege mehr und verwaltet nur bestehende Positionen. Keine neuen Einstiege bei Tagesverlust > 3 % oder Drawdown > 20 % der H8-Equity.
9. `manuals/ebc/EBC_H1.md`–`EBC_H7.md` und `results/H1`–`results/H7` nicht ändern.

## 2. Hypothese

### 2.1 Behauptung (H8)

Die Modellergebnisse von B++ sind real erzielbar. Ein täglicher REST-Executor ohne Nautilus-Live-Knoten setzt im eToro-Paper-Konto mindestens 90 % der Schatten-Trades um, mit realen Kosten innerhalb Modell + 2 bps je Seite, und die reale Tagesrendite folgt dem Schatten (Korrelation ≥ 0,9). Zusätzlich verbessert eine Ausführung kurz vor Handelsschluss auf einem Proxy-Schlusskurs (PC) gegenüber der Ausführung zum nächsten Open (NO) die reale Rendite je Trade, weil sie die Nacht zwischen Signal und Fill schliesst.

**H0:** Ablehnungen, Ausfälle, Mindestgrössen und fehlende Kurse begrenzen den Durchsatz; die realen Kosten übersteigen das Modell; die reale Rendite entkoppelt sich vom Schatten.

### 2.2 Vorhersagen (vorregistriert)

| ID | Vorhersage | Messgrösse | Schwelle | Rolle |
|---|---|---|---|---|
| P8.1 | Durchsatz | reale Round-Trips / Schatten-Round-Trips im selben Zeitraum; Anteil abgelehnter Orders (synchron und asynchron) | ≥ 0,90 **und** ≤ 2 % | primär |
| P8.2 | Kosten wie modelliert | reale Kosten je Seite (halber Spread beim Absenden + Slippage gegen die Quote beim Absenden + Gebühren) je Klasse gegen das Modell | Mittel ≤ Modell + 2 bps **und** obere 90-%-KI-Grenze ≤ Modell + 5 bps | primär |
| P8.3 | Tracking | Korrelation der realen und der Schatten-Tagesrenditen; mittlere tägliche Differenz real − Schatten | Korrelation ≥ 0,90 **und** untere 90-%-KI-Grenze der mittleren Differenz > −3 bps/Tag | primär |
| P8.4 | Ausführungszeitpunkt | mittlere Abweichung real − Schatten je Trade, PC-Gruppe minus NO-Gruppe (randomisiert, §6.3) | Punktschätzer ≥ +3 bps je Trade; je Archetyp berichten | sekundär |
| P8.5 | Proxy-Signale sind verlässlich | Anteil der PC-Entscheidungen, die der endgültige Schlusskurs nicht bestätigt | ≤ 5 % | sekundär |
| P8.6 | Betriebssicherheit | Anteil ausgeführter geplanter Läufe; H8-Positionen ohne Broker-Stop bei der stündlichen Prüfung; Läufe bis zur Wiederaufnahme nach einem Ausfall | ≥ 98 %; 0; ≤ 1 | sekundär |
| P8.7 | Promotion | wöchentlicher sequenzieller Test der realen Tagesrenditen | Woche der ersten `PROMOTE`-Entscheidung berichten | sekundär |

### 2.3 Widerlegung

H8 ist **widerlegt**, wenn nach ≥ 40 Handelstagen (a) reale / Schatten-Round-Trips < 0,75 **oder** (b) die untere 90-%-KI-Grenze der realen Kosten je Seite in K1 > Modell + 5 bps (die Modellkosten sind klar zu niedrig) **oder** (c) die Korrelation der Tagesrenditen < 0,70. Sonst, bei unerfüllten primären Vorhersagen: `UNENTSCHIEDEN`.

### 2.4 Begründung aus dem Repository (Stand `4067121`)

- **R1 Kein Realitätsabgleich.** H1–H3 liefen nur in Stufe A; Stufe C wurde nie gestartet (`results/H1/report.md`, `results/H3/report.md`). Kosten stammen aus Rückfallwerten, die Spread-Stichproben vom Wochenende sind für Nicht-Krypto-Instrumente unbrauchbar. Für die Archetypen gibt es keine einzige reale Messung von Slippage, Gebühren, Finanzierung oder Stop-Verhalten.
- **R2 Die Archetypen haben keinen Ausführungspfad.** `automation/config/ebc_archetypes.json` wird „von keinem Produktionspfad gelesen"; der Live-Bot ist auf die Stundenachse gesperrt (`automation/bar_axis.py:20`, `LIVE_AXIS = "OneHour"`). Die Archetypen treffen eine Entscheidung je Tag; dafür reicht ein täglicher REST-Executor.
- **R3 Betrieb.** Paper-Bericht 2026-10-11 17:00 UTC (`logs/paper_reports/paper_report_latest.md`): Inkubations-Bot läuft seit etwa 13:15 UTC nicht, 0 Evidenz-Ledger, 2 offene Positionen (BTC 199,56 USD, INJ 156,10 USD), freies Guthaben 9619,58 USD. `automation/ops_guard.py` erkennt Ausfälle, startet aber nicht neu.
- **R4 Konto-Interferenz** wie Regel 3 beschrieben (`automation/momentum_ls_allocator.py:144`, `:171–172`, `:189`).
- **R5 Ausführungslücke.** Die Simulation füllt zum Eröffnungskurs nach dem Signal-Schlusskurs. Ob die Nacht dazwischen den Archetypen Rendite kostet oder bringt, ist ungemessen; PC schliesst die Lücke, riskiert aber Fehlsignale, weil der Proxy nicht der Schlusskurs ist.
- **R6 Broker-Exits.** EBC-H3 blieb `UNENTSCHIEDEN`; der Adapter setzt `IsTrailingStop` (`automation/adapters/etoro_execution.py:852–853`), eToro dokumentiert `IsTslEnabled`, und `ETORO_ENABLE_TSL` ist standardmässig `"0"` (`automation/momentum_ls_run.py:54`). B++ nutzt darum keinen Broker-Trailing-Stop, nur den Katastrophen-Stop als `StopLossRate`; der Trailing-Stop wird vom Executor auf dem Schlusskurs geprüft.

## 3. Schritt 0 — Setup und Preflight

```bash
cd <Experiment-Arbeitsverzeichnis von EBC-H1–H7>
git fetch origin && git switch -c ebc/h8 origin/main
. venv/bin/activate
python experiments/ebc/preflight.py results/H8/preflight.json --expected-ancestor 40671219c3e007ea268c5d48ff5573cafaef88a7
mkdir -p results/H8/stage_b results/H8/stage_c logs/ebc_h8
```

Prüfungen (alle müssen bestehen): (1) `/me` liefert das angeheftete Paper-Konto; (2) HEAD enthält `4067121`; (3) `verdict.json` von H4–H7 vorhanden oder ausdrücklich als fehlend vermerkt, B++-Zusammensetzung nach §6.1 abgeleitet und in `results/H8/bplusplus.json` geschrieben; (4) Konto-Variante aus der Freigabe dokumentiert; (5) Trockenlauf über 3 Handelstage: Der Executor erzeugt mit `--dry-run` je Lauf die Order-Liste; sie muss mit den Einstiegen und Ausstiegen des Schattens desselben Tages übereinstimmen.

## 4. Universum und Daten

Universum von B++ nach §6.1 (`U_breit` bzw. `U_breit` ∪ `U_neu`, Kostenfilter q ≤ 0,10 mit der Kostentabelle aus EBC-H4). Bei Variante (c) ohne alle Instrumente aus `automation/config/instrument_map.json` (Regel 3) und ohne Instrumente mit fremden offenen Positionen. Tageskerzen täglich nach Kerzenschluss fortschreiben wie `stage_b_daily.py`; Proxy-Kurse für PC aus `GET /api/v1/market-data/instruments/rates`.

## 5. Kostenmodell

Modell = Kostentabelle von EBC-H4 (sonst Rückfallwerte aus `experiments/ebc/costs.py`). Real je Fill: halber Spread = (Ask − Bid) / Mitte / 2 beim Absenden; Slippage = seitenbereinigt (Fill − Quote beim Absenden) / Quote; Gebühren und Finanzierung aus `GET /api/v1/trading/info/<env>/pnl` je Position, in bps auf das Notional.

## 6. B++ und Arme

### 6.1 Zusammensetzung von B++ (vorregistrierte Regel)

| Baustein | übernommen, wenn | sonst |
|---|---|---|
| Kapazität 120 Plätze × 0,5 %; Universum `U_breit` ∪ `U_neu` (nur K1, falls P4.6 erfüllt) | EBC-H4 `BESTAETIGT` | 30 × 2 %, `U_breit` |
| Haltedauern nach `recommended_holding` aus EBC-H5 für die gewählte Kapazität; NP10, falls P5.4 erfüllt | EBC-H5 `BESTAETIGT` | T 60 / M 5 / V 20 |
| Katastrophen-Stop DS1 (Deckel 45 % bzw. grösste akzeptierte Distanz aus EBC-H6 §9), Risikobudget RB, Volatilitätssteuerung VT | EBC-H6 `BESTAETIGT` | DS0, gleiches Notional, ohne VT |
| zusätzliche Archetypen aus `archetypes_for_bplusplus` von EBC-H7 mit Quoten nach EBC-H7 §6.2 | EBC-H7 `BESTAETIGT` oder einzeln bestehend | nur T, M, V |
| Trailing-Stop auf Schlusskurs, kein TTP, kein Broker-Trailing-Stop | immer (EBC-H3 `UNENTSCHIEDEN`) | – |

Ist keine der Hypothesen H4–H7 bestätigt, ist B++ = B+ (EBC-H1). Die Zusammensetzung wird vor dem ersten Handelstag eingefroren; spätere Verdikte ändern sie nicht.

### 6.2 Arme

| Arm | Ausführung | Zweck |
|---|---|---|
| **B++ real** | Executor, reale Orders im Paper-Konto | Messobjekt |
| **B++ Schatten** | dieselben Regeln, Fill zum Eröffnungskurs nach dem Signal, Modellkosten (`stage_b_multi.py --hyp H8`) | Referenz für P8.1, P8.3 |
| **Q** | Inkubations-Bot des Repos, nur lesend (Tagesrenditen aus `/pnl` und Evidenz-Ledgern), falls er läuft | Kontext |

### 6.3 Ausführungszeitpunkt (randomisiert)

- Jeder neue Trade (Instrument, Archetyp, Signaltag) erhält PC oder NO über `int(sha256(f"{signaltag}|{instrument_id}|{archetyp}|20261011").hexdigest(), 16) % 2` (1 = PC, 0 = NO); alle Entscheidungen dieses Trades (Einstieg, Ausstiege) folgen demselben Zeitpunkt.
- **PC** nur für Instrumente mit US-Session (09:30–16:00 America/New_York): Entscheidung um 15:40 ET auf dem Proxy-Schlusskurs (Mitte aus `rates`), Orders bis 15:50 ET. Instrumente ohne US-Session laufen immer über NO und gehen nicht in P8.4 ein.
- **NO:** Entscheidung nach dem endgültigen Schlusskurs (Kerzen-Update nach 22:30 UTC); Ausführung zur nächsten Session-Eröffnung + 5 Minuten bzw. als vorgemerkte Order, falls C0.1 zeigt, dass eToro Market-Orders ausserhalb der Session bis zur Eröffnung vormerkt.
- **Proxy-Abgleich (P8.5):** Nach dem Schlusskurs wird jede PC-Entscheidung mit dem endgültigen Schlusskurs neu gerechnet; Abweichungen werden gezählt, die reale Position bleibt unverändert (keine Korrektur-Orders), der Schatten folgt dem endgültigen Schlusskurs.
- Zeitangaben in America/New_York mit `zoneinfo`; die Sommerzeit endet in den USA am 2026-11-01.

## 7. Stufe C0 — Mechanik (1 Handelstag, nach Freigabe)

- **C0.1 Order ausserhalb der Session:** Market-Order über 20 USD auf eine US-Aktie nach Handelsschluss: abgelehnt, vorgemerkt oder sofort ausgeführt? Danach richtet sich NO.
- **C0.2 Stop beim Einstieg:** Order mit `StopLossRate` im Abstand des Katastrophen-Stops von B++; gespeichertes Niveau aus `/pnl` lesen.
- **C0.3 Schliessen:** `POST {REST_BASE}/market-close-orders/positions/{positionId}` mit `{"InstrumentId": <id>, "UnitsToDeduct": null}` schliesst vollständig.
- **C0.4 Asynchrone Ablehnung:** Status jeder Order über `GET /api/v1/trading/info/<env>/orders/{orderId}` bis zum Endzustand abfragen (Zeitlimit 60 s); Ablehnungscodes protokollieren.
- **C0.5 Abgleich:** Positionen aus `/pnl` gegen das H8-Register; jede Differenz ist ein Vorfall.

Ausgabe `stage_c/c0_mechanics.json`. Alle C0-Positionen werden am selben Tag geschlossen.

## 8. Stufe C — Paper-Ausführung (≥ 40 Handelstage, ≥ 150 Round-Trips)

Executor `experiments/ebc/h8_executor.py` mit den Unterbefehlen `pc`, `eod`, `open`, `reconcile` und der Option `--dry-run`; Start per Cron oder systemd-User-Timer wie `automation/ops_guard.py` (`--timer-help` als Vorlage), jeweils mit `flock`.

| Zeitpunkt | Unterbefehl | Aufgabe |
|---|---|---|
| 15:40 ET | `pc` | Proxy-Kurse lesen, Entscheidungen der PC-Trades (Einstiege, Trailing-, Zeit- und Signal-Exits), Orders senden, Status prüfen |
| nach 22:30 UTC | `eod` | Kerzen fortschreiben, Schatten rechnen, Proxy-Abgleich, NO-Entscheidungen für die nächste Session in `stage_c/planned_orders.csv` |
| Session-Eröffnung + 5 min | `open` | geplante NO-Orders senden, Status prüfen |
| stündlich | `reconcile` | `/pnl` gegen Register; fehlt bei einer H8-Position der Broker-Stop, Position schliessen und als Vorfall zählen |

Je Order protokollieren (`stage_c/orders.csv`, `stage_c/fills.csv`): `order_id, position_id, instrument, klasse, archetyp, seite, timing (PC|NO), decision_ts, submit_ts, bid, ask, fill_px, units, amount_usd, stop_loss_rate, status, error_code, fees_usd`. Tägliche reale Equity der H8-Positionen (realisiert plus unrealisiert) nach `stage_c/daily_real.csv`; Vorfälle (Ausfall, Ablehnung, fehlender Stop, Abgleichsdifferenz) nach `stage_c/incidents.csv`.

**Kosten-Rekalibrierung:** Weichen die realen Kosten einer Klasse nach ≥ 50 Fills um mehr als 2 bps bzw. 20 % vom Modell ab, rechnest du den Schatten zusätzlich mit den gemessenen Kosten und berichtest beide Fassungen; P8.3 stützt sich auf die rekalibrierte Fassung.

## 9. Schatten (Stufe B, parallel)

`python experiments/ebc/stage_b_multi.py --hyp H8 --arms Bpp --since <Startdatum>` täglich nach dem `eod`-Lauf; Ausgabe `results/H8/stage_b/shadow_daily.csv` und `shadow_trades.parquet`. Der Schatten verwendet dieselben Signale und Daten wie der Executor, aber Modell-Fills und Modellkosten.

## 10. Metriken (Definitionen)

- Round-Trip: eröffnete und vollständig geschlossene Position; offene Positionen am Ende getrennt zählen.
- Kosten je Seite in bps nach §5; Mittel je Klasse und Archetyp.
- Reale Tagesrendite: Änderung der H8-Equity / H8-Equity des Vortags; Schatten-Tagesrendite aus `shadow_daily.csv`.
- Abweichung je Trade: d = reale Netto-Rendite − Schatten-Netto-Rendite desselben Trades (bps auf das Notional); P8.4 vergleicht mean(d | PC) − mean(d | NO).
- Betriebsquote: ausgeführte / geplante Läufe.

## 11. Statistik und Entscheidungsregel

- P8.2: Cluster-Bootstrap der Kosten je Seite nach Handelstag (5000, Seed 20261011, 90 %).
- P8.3: KI des Mittelwerts der täglichen Differenzreihe real − Schatten per Stationary Bootstrap (`portfolio.boot_indices`, Blocklänge `optimal_block_length` der Differenzreihe, 5000 Replikate, Seed 20261011, 90 %).
- P8.4: Zwei-Stichproben-Bootstrap der Abweichungen d, geclustert nach Kalenderwoche des Signals (5000, Seed 20261011).
- P8.7: wöchentlich `automation.optimizer.sequential.sequential_decision(returns, look_index=w, k_max=26, n_concurrent=1, confidence=0.95)` auf den realen Tagesrenditen.
- Entscheidung nach ≥ 40 Handelstagen und ≥ 150 Round-Trips; davor nur Zwischenberichte (`stage_c/weekly_<KW>.md`) ohne Regeländerung.

| Verdikt | Bedingung |
|---|---|
| `BESTAETIGT` | P8.1, P8.2 und P8.3 erfüllt |
| `WIDERLEGT` | Kriterium aus §2.3 erfüllt |
| `UNENTSCHIEDEN` | sonst; nenne die fehlende Evidenz |

## 12. Ausgaben

```
results/H8/
  preregistration.json   preflight.json   bplusplus.json
  stage_c/ c0_mechanics.json   planned_orders.csv   orders.csv   fills.csv   positions.jsonl
           daily_real.csv   incidents.csv   weekly_<KW>.md   cost_recalibration.json
  stage_b/ shadow_daily.csv   shadow_trades.parquet
  metrics.json   verdict.json   report.md
```

`verdict.json` mit denselben Feldern wie EBC-H1 (`hypothesis: "H8"`), zusätzlich:

```json
{
  "bplusplus": {"capacity": "C30|C120", "universe": "U_breit|U_breit+U_neu", "holding": {"T": 0, "M": 0, "V": 0},
                "np10": false, "loss_budget": {"ds_max": 0.15, "sizing": "EN|RB", "vol_targeting": false},
                "extra_archetypes": [], "source_verdicts": {"H4": "", "H5": "", "H6": "", "H7": ""}},
  "account_variant": "a|b|c",
  "throughput": {"real_round_trips": 0, "shadow_round_trips": 0, "ratio": 0.0, "rejected_share": 0.0},
  "costs_bps_per_side": {"K1": {"model": 0.0, "real_mean": 0.0, "ci90": [0.0, 0.0], "n_fills": 0}},
  "tracking": {"corr": 0.0, "mean_diff_bps_per_day": 0.0, "ci90": [0.0, 0.0]},
  "timing": {"pc_minus_no_bps_per_trade": 0.0, "ci90": [0.0, 0.0], "proxy_disagreement": 0.0},
  "operations": {"run_share": 0.0, "positions_without_stop": 0, "incidents": 0},
  "sequential": {"first_promote_week": null}
}
```

`report.md` (Deutsch, ≤ 3 Seiten): Verdikt, Zusammensetzung von B++, Vorhersagetabelle, Kosten real gegen Modell je Klasse, Tracking-Grafik als Tabelle (Woche × real/Schatten), Ergebnis PC gegen NO, Vorfälle, Antwort auf F5 in drei Sätzen.

## 13. Folgen je Verdikt

- **BESTAETIGT:** Issues: (1) Tagesachsen-Executor als eigener Ausführungspfad neben dem Stunden-Bot, mit gemeinsamer Kontosicht (`automation/account_view.py`) und getrennten Budgets je Bot; (2) Ausführungszeitpunkt nach P8.4; (3) Kostenmodell auf die gemessenen Werte umstellen (`experiments/ebc/costs.py`, `automation/config/backtest.json`); (4) die Entscheidung über Echtgeld bleibt beim Nutzer — H8 liefert ausschliesslich Paper-Evidenz.
- **WIDERLEGT:** (a) ⇒ Ursachen nach Ablehnungscodes und Ausfällen aufschlüsseln und beheben, dann wiederholen; (b) ⇒ H1–H7 mit den gemessenen Kosten neu rechnen und die Verdikte überprüfen; (c) ⇒ Tracking-Ursachen klären (Fill-Zeitpunkt, Kursquelle, Teilfills, Proxy-Fehler).
- **UNENTSCHIEDEN:** Stufe C bis 80 Handelstage bzw. 300 Round-Trips verlängern.

## 14. Ablauf-Checkliste

- [ ] Freigabe mit Konto-Variante, Budget E₀ und Startdatum liegt vor (sonst nur Schritt 0 und Trockenlauf)
- [ ] Preflight bestanden, B++-Zusammensetzung in `bplusplus.json` eingefroren
- [ ] `h8_executor.py` mit `--dry-run` über 3 Handelstage deckungsgleich mit dem Schatten
- [ ] `preregistration.json` geschrieben
- [ ] C0.1–C0.5 erledigt, Positionen geschlossen
- [ ] Stufe C läuft mit Timern; Schatten läuft; wöchentliche Zwischenberichte
- [ ] Kosten-Rekalibrierung geprüft
- [ ] Nach ≥ 40 Handelstagen und ≥ 150 Round-Trips: `verdict.json` und `report.md`
