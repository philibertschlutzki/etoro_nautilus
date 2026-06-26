diff --git a/automation/AGENTS.md b/automation/AGENTS.md
index 22d4572..85c1da8 100644
--- a/automation/AGENTS.md
+++ b/automation/AGENTS.md
@@ -715,7 +715,7 @@ Die Backtest-Orchestrierung unterstützt nun eine Walk-Forward-Validierung mit O
 **Fix:** Modul-Level `warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)` in `run_optimization.py`. **Bewusst gezielt:** nur diese Warn-Kategorie wird unterdrückt; **kein** globales `optuna.logging.set_verbosity(ERROR)`, da Optunas native Per-Trial-INFO-Logs (Reward-Werte) im Sweep (`make_symbol_objective` emittiert kein strukturiertes Event) die einzige Per-Trial-Rückmeldung sind und für die Diagnose (Issue #401) gebraucht werden — ein ERROR-Silencing würde die Observability aus Issue #403 untergraben. Test-gesichert via `importlib.reload` in einem isolierten `catch_warnings`-Kontext (pytest verwaltet `warnings.filters` pro Test).
 **Betroffen:** `automation/optimizer/run_optimization.py`
 
-### 🟢 Pitfall #75 — Per-Symbol-Sweep: Unevaluable-Floor-Kollaps [BEHOBEN — Plateau (#404–#410), Floor-Guard v3 (#413), **Defekt A: stiller Fill-`ts`-Fallback (#448, GH-#448) → Pitfall #80**]
+### 🟢 Pitfall #75 — Per-Symbol-Sweep: Unevaluable-Floor-Kollaps [BEHOBEN — Plateau (#404–#410), Floor-Guard v3 (#413), **Defekt A: stiller Fill-`ts`-Fallback (#448, GH-#448) → Pitfall #80**, **Defekt B: OOS-Abdeckungs-Blindstelle (H2-Datenlücke + verworfene Telemetrie) (#449, GH-#449) → Pitfall #82**]
 **Symptom:** `python -m automation.optimizer.sweep --strategies all --tier all --n-jobs 6` liefert für JEDEN Trial exakt `value: -9.75`, über alle Parameter (`sma_period` 5–53, `cooldown_bars` 2–36) und über 576+ akkumulierte Trials hinweg. `Best is trial 0` bewegt sich nie. TPE hat keinen Gradienten; der Sweep ist effektiv ein teurer Zufallsgenerator.
 **Abgrenzung zu Pitfall #71/#401:** NICHT der Zero-Loss/Sub-Threshold-Sortino-Fall. −9.75 ist mathematisch exakt `penalty_unevaluable_oos (−10.0) + unevaluable_shaping_span (0.25) × progress (1.0)` — der **Unevaluable-Floor mit gesättigtem Shaping**, NICHT der Evaluable-Floor (`−10.0 + 0.25 + evaluable_floor_epsilon = −9.749`). Der `max(reward, floor)`-Evaluable-Pfad kann −9.75 niemals erzeugen (sein Minimum ist −9.749). Folglich landet jeder Trial im Unevaluable-Zweig `not m.oos_evaluated or base_source is None`, und der #401-`total_return`-Fallback greift NICHT (er verlangt `oos_evaluated ∧ oos_eligible`).
 **Root Cause (zwei kompoundierende Defekte):**
@@ -780,6 +780,25 @@ Die Backtest-Orchestrierung unterstützt nun eine Walk-Forward-Validierung mit O
 **Fix/Regel:** Single Source of Truth = `*Config`-Felder. **Ein Parameter darf nur gesampelt werden, wenn er als Config-Feld existiert.** Renames in `spaces.py`; Phantom-Volumen-/RSI-Keys entfernt; `trend_tolerance_pct` als Feld ergänzt + im Trend-Gate verdrahtet; FlashCrash sampelt jetzt die echten Entry-Felder `bb_period`/`bb_std_dev`. Die zentrale Regressions-Assertion `test_search_space_binding.py::test_sampled_params_bind_to_config` prüft für JEDE aktive Strategie `set(sample_params(s)) ⊆ set(Config.__struct_fields__)` und fängt jeden künftigen Drift fail-fast ab. Die vollständige Soll-Vorgabe steht in `automation/OPTIMIZER_PARAMETER_REFERENZ.md`.
 **Betroffen:** `automation/optimizer/spaces.py`, `automation/strategies/tesla_combo_strategy.py` (`trend_tolerance_pct`-Feld + Verdrahtung); Tests `automation/tests/test_search_space_binding.py`, `automation/tests/test_combo_conjunction_switches.py` (veraltete „dropped"-Assertion korrigiert); Doku `automation/OPTIMIZER_PARAMETER_REFERENZ.md`.
 
+### 🟢 Pitfall #82 — OOS-Abdeckungs-Blindstelle: Daten erreichen das OOS-Sub-Fenster nie ⇒ struktureller OOS=0-Kollaps [BEHOBEN: GH-#449 (Vorschlag #449)]
+**Symptom:** Identisch zur Pitfall-#80-Signatur (`oos_evaluated: false`, `oos_total_trades: 0`, `is_total_trades == is_max_trades`, alle Trials auf dem Unevaluable-Floor −9.90…−9.93, „Floor-Plateau erkannt" nach 16 Trials) — aber für **TSLA.ETORO über ALLE sechs aktiven Strategien**, obwohl der `_fill_ts_ns`-Fix (#80) bereits greift und der IS-Backtest 100–170 Round-Trips erzeugt. Sechs strukturell verschiedene Strategien können nicht zufällig alle am Tag 180 aufhören zu handeln.
+**Root Cause:** Der IS/OOS-Split (`extract_metrics`) und `check_data_span` waren **beide korrekt** — der Defekt war **datenseitig + telemetrie-seitig**, nicht logisch. Die Walk-Forward-Geometrie verankert das früheste OOS-Sub-Fenster bei `start_ns + is_window_ns` (= `start + 180 d` = 2025-11-12 bei now=2026-06-25). Ein Trade ist nur dann OOS-klassifizierbar, wenn sein Exit-`ts` **diese Grenze erreicht**. Reichen die TSLA-Katalogdaten in der zweiten Fensterhälfte (H2, 2025-11→2026-05) nur als **dünner/stale Endpunkt** (z. B. nach einem `catalog_service`-Ausfall mit partiellem Backfill), liegen ALLE realen Fills in `[start, start+180 d]` ⇒ das OOS-Sub-Fenster erhält **null** Fills ⇒ `oos_total_trades=0` **strukturell, parameter-unabhängig**. Zwei Guards verfehlten das: **(a)** die #448-Plausibilitäts-Assertion prüft nur die **untere** Kante (`fill_ts_max < start_ns`), nicht die **OOS-Abdeckungs**-Kante (`fill_ts_max < start_ns + is_window_ns`); **(b)** `check_data_span` validiert nur `last_tick − first_tick ≥ required − tol` (357 d) — das besteht auch dann, wenn H2 nur aus Endpunkt-Ticks besteht (es prüft Spannweite, nicht Dichte/Aktualität). Und entscheidend: die diagnostisch einzige relevante Zahl (`fill_ts_max` vs. OOS-Grenze) wurde **vor der Operator-Konsole verworfen** — deshalb blieb der Bug über mehrere Sessions undiagnostiziert.
+**Fix/Regel:** **(1) Telemetrie** — `extract_metrics` berechnet `oos_window_start_ns = start_ns + is_window_ns` und `oos_covered = (fill_ts_max ≥ oos_window_start_ns)`; beides wird (über `_oos_window_start_ns`/`_oos_covered` → Worker-Result → `write_tournament_json`-`data_window`-Block → `parse_tournament`/`TournamentMetrics`) in **beide** `optimizer_trial_completed`-Events gehoben (`fill_ts_max`, `oos_window_start_ns`, `oos_covered`, `oos_coverage_gap_days`). Bei `oos_covered=False` ist der Floor-Grund auf einen Blick eindeutig **datenseitig** statt parameterseitig. **(2) WARN statt raise in `extract_metrics`** — die OOS-Abdeckungs-Verletzung wird als sichtbare Logzeile gemeldet, NICHT als `ValueError` (ein `raise` → NULL-Rückgabe verschluckt genau die Telemetrie); die **harte** Vorab-Abweisung gehört ins Sweep-Gate-1-Preflight. **(3) Gate-1-Preflight** (`gate.data_reaches_oos_window` + `sweep.enumerate_tunable_pairs`/`latest_ts_by_symbol`) — ein Symbol, dessen jüngster Katalog-Tick die früheste OOS-Grenze nicht erreicht, wird VOR dem Sweep mit klarer Begründung **übersprungen** (Grund `OOS_WINDOW_UNREACHABLE`), statt 100 strukturell nutzlose Trials zu fahren. **Vollständig fail-open** (fehlt die Tick-Telemetrie/Geometrie ⇒ Preflight aus, Verhalten bit-identisch). **Regel: Bar-ANZAHL (Gate-1 a–c) ist nicht hinreichend — die Daten-AKTUALITÄT muss die OOS-Grenze erreichen.** **Operative Konsequenz:** Bei `oos_covered=False` den H2-Katalog des Symbols auffrischen (Backfill 2025-11→heute), dann erneut tunen.
+**Invariante:** Per-Symbol-Tuning ist nur sinnvoll, wenn `fill_ts_max ≥ start_ns + is_window_ns` (mindestens ein Fill kann ins OOS-Fenster fallen). Andernfalls ist `oos_total_trades=0` kein Strategie-, sondern ein Daten-Defekt und MUSS als solcher sichtbar sein.
+**Betroffen:** `automation/backtest_runner.py` (`extract_metrics` — `oos_window_start_ns`/`oos_covered` + WARN; `run_single_backtest_worker`-Result; `write_tournament_json`-`data_window`), `automation/optimizer/parsing.py` (`TournamentMetrics.oos_window_start_ns/oos_covered/oos_coverage_gap_days`), `automation/optimizer/run_optimization.py` (beide Trial-Events), `automation/optimizer/gate.py` (`data_reaches_oos_window`), `automation/optimizer/sweep.py` (`latest_ts_by_symbol`, Preflight in `enumerate_tunable_pairs`/`run_per_symbol_sweep`); Tests `automation/tests/test_issue_449_oos_coverage.py`.
+
+### 🟢 Pitfall #83 — Floor-Plateau-Guard warnt nur, stoppt die Study nicht ⇒ ~30 min verschwendete Compute [BEHOBEN: GH-#450 (Vorschlag #450)]
+**Symptom:** Nach „Floor-Plateau erkannt" (Pitfall #75/#82-Klasse) läuft die Study dennoch bis `n_trials=100` weiter — der TPE-Sampler hat keinen Gradienten (alle Trials unevaluable), erzeugt also nur teures Rauschen. Pro Symbol/Strategie verfällt die restliche Compute (~84 Trials) nutzlos; über einen vollen `--symbols all`-Sweep summiert sich das massiv.
+**Root Cause:** `floor_plateau_callback` (#409/#413) ist reine Observability — es setzt `floor_plateau_warned` und loggt, ruft aber **nie** `study.stop()`. Das war als bewusste „Observability ändert nie eine Entscheidung"-Leitplanke gedacht, kostet aber bei strukturell unevaluablen Symbolen volle Laufzeit.
+**Fix/Regel:** Opt-in-Parameter `stop_on_plateau: bool = False`. In **beiden** Plateau-Zweigen (evaluable-basiert UND Legacy-Wert-basiert) ruft der Guard `study.stop()` — aber nur, wenn `stop_on_plateau=True`. Die Produktion bindet `stop_on_plateau=True` in beiden `partial(floor_plateau_callback, …)`-Stellen (`optimize_symbol`, `optimize`); die Default-Signatur bleibt `False`, sodass alle Unit-Tests mit Fake-Study (die kein `.stop()` haben) **unverändert** durchlaufen. Der `.stop()`-Aufruf ist zusätzlich via `getattr(study, "stop", None)` + `try/except` abgesichert (eine Study außerhalb des `optimize()`-Kontexts ⇒ Warnung genügt, kein Crash). **Observability-Invariante bleibt:** der Guard ändert weiterhin **nie** eine Reward-/Promotion-Entscheidung — er beendet nur eine bereits als aussichtslos erkannte Suche früher.
+**Betroffen:** `automation/optimizer/run_optimization.py` (`floor_plateau_callback`-Signatur + zwei `study.stop()`-Zweige; zwei `partial`-Bindungen); Tests `automation/tests/test_issue_449_oos_coverage.py` (`test_plateau_*`).
+
+### 🟢 Pitfall #84 — Walk-Forward-Fenster-Arithmetik dupliziert ⇒ Divergenz-Footgun [BEHOBEN: GH-#451 (Vorschlag #451)]
+**Symptom:** Latentes Risiko (kein akutes Fehlverhalten): die Fenster-Berechnung (`end = Mitternacht(now); Sonntag→−1 d; −holdout; start = end − (is + splits·oos)`) lebte ausschließlich inline in `build_trial`. Jeder weitere Konsument (das #82-Preflight braucht **exakt dieselbe** OOS-Grenze) müsste sie nachbauen — und genau eine solche Divergenz zwischen „start_ns fürs Laden" und „start_ns für den Split" ist die Wurzel der gesamten OOS=0-Bug-Klasse.
+**Root Cause:** Keine geteilte reine Funktion für die Fenster-Grenzen; die Arithmetik war an die Manifest-Schreib-Logik von `build_trial` gekoppelt.
+**Fix/Regel:** `trial_config.compute_walk_forward_window(*, now, holdout_days, is_window_days, oos_window_days, n_folds) -> (start, end)` ist die **EINZIGE** Quelle der Fenster-Arithmetik. `build_trial` delegiert daran (Verhalten bit-identisch — durch `test_window_reproduces_known_dates` gegen das real beobachtete Fenster 2025-05-16→2026-05-11 verifiziert); das Sweep-Gate-1-Preflight (#82) nutzt dieselbe Funktion. **Regel: Fenster-Grenzen NIE inline nachbauen — immer `compute_walk_forward_window`.**
+**Betroffen:** `automation/optimizer/trial_config.py` (`compute_walk_forward_window`, `build_trial` delegiert), `automation/optimizer/sweep.py` (Preflight-Setup); Tests `automation/tests/test_issue_449_oos_coverage.py` (`test_window_*`, `test_build_trial_uses_shared_window`).
+
 ### Pitfall #53: Optimizer-Storage — SQLite-Default, Postgres-Opt-in (A4.7)
 **Symptom:** Unklarheit über Datenhaltung und fehlende PR-Promotion.
 **Ursache/Lösung:** **SQLite für Single-Node** ist der strikte Default (per-Study-Datei `{WORK}/sweep/{study}.db`). **Postgres (o. ä.) nur für explizite parallele Sweeps** über mehrere Maschinen gegen *eine* Study — reines Opt-In via `optimizer.json['storage_url']` oder ENV `ETORO_OPTUNA_STORAGE` (ENV hat Vorrang), aufgelöst durch `run_optimization.resolve_storage`. Diese Aufweichung der „ausschließlich SQLite"-Leitplanke ist **bewusst, dokumentiert und begrenzt**: bei non-SQLite-URL ist Determinismus pro Study nur bei `n_jobs=1` garantiert (Warnung wird geloggt; Pitfall #68 bleibt für SQLite gültig). Eine ENV-URL wird verbatim genutzt (Fail-Fast bei ungültiger URI statt stillem SQLite-Fallback). Der Optimizer verändert `tournament.json` NIE und startet NIE Phase 5; Promotion nur per PR.
@@ -829,7 +848,7 @@ Limit-Exits (wie z.B. das native Profit-Target) werden **asynchron** verwaltet.
 
 | Datum | Änderung | Dateien |
 |-------|----------|---------|
-| 2026-06-25 | **IMPLEMENTIERUNG GitHub-Issues #441–#448 (Per-Symbol-Optimizer-Forensik 2026-06-24).** Sechs Code-/Config-Defekte + zwei Doku-Deliverables, alle test-gesichert (volle automation-Suite lokal grün: 351 passed). **#448 (P0, Pitfall #80) — struktureller OOS=0:** Wurzel = stiller Fill-`ts`-Fallback `getattr(f,'ts_event',getattr(f,'ts_init',0))` (a) `0`-Default ⇒ jeder Round-Trip als In-Sample; (b) Fallback-Report `generate_order_fills_report` hat `ts_last`, kein `ts_event`. Fix: `_fill_ts_ns` (fail-loud, `ts_event→ts_last→ts_init`) als einzige Lesestelle + Plausibilitäts-Assertion (`fill_ts_max<start_ns ∨ fill_ts_min≤0 ⇒ ValueError`). Reproduktion 142 uniforme Exits ⇒ IS=72/OOS=70. Pitfall #75 → 🟢. **#447 (P1) — Reward-Floor:** Floor-Separations- (`unevaluable_max=−9.75 < evaluable_min=−9.749`) und Saturations-Invariante (`reward(is=target)==reward(is=10·target)`) formal test-fixiert (Code war bereits korrekt). **#446 (P1, Pitfall #81) — Sampling↔Config:** Renames `bb_std→bb_std_dev`/`vwap_window→vwap_period`; Phantom-Volumen-/RSI-Keys entfernt (1h-Bars haben `volume=1.0`, VwapExhaustion hat kein RSI); `trend_tolerance_pct` in Combo verdrahtet; FlashCrash sampelt jetzt `bb_period`/`bb_std_dev`; zentrale Bindungs-Assertion `test_search_space_binding.py`. **#445 (P1) — Walk-Forward-Historie:** validierte Geometrie (180/45/4/45=405) behalten, ehrliche `data_history_days=450` (~15 Monate) deklariert (SSOT mit `strategy_defaults._schema`), Fail-Loud-Startup-Assertion in `build_trial`. **#444 (P2) — data_window:** Schreib-Seite ergänzt (`write_tournament_json` schreibt `data_window` inkl. `fill_ts_min/max`; Worker/`extract_metrics` liefern die Spanne); Round-Trip-Test. **#443 (P3) — Loop-Var-Shadowing:** innere Fold-Schleifen `i/j`→`fold` (verhaltensneutral). **#442/#441 (Doku):** `OPTIMIZER_PARAMETER_REFERENZ.md` (korrigierte Suchräume aller aktiven Strategien) neu; AGENTS.md: Pitfall #75 → 🟢, neue Pitfalls #80/#81, §18-Konventionen (Fill-`ts` fail-loud, Bindungs-Pflicht, kein Loop-Shadowing, Geometrie≤Historie). | `automation/backtest_runner.py`, `automation/optimizer/spaces.py`, `automation/optimizer/parsing.py`, `automation/optimizer/trial_config.py`, `automation/strategies/tesla_combo_strategy.py`, `automation/config/backtest.json`, `automation/config/strategy_defaults.json`, `automation/OPTIMIZER_PARAMETER_REFERENZ.md`, `automation/tests/test_issue_448_oos_split.py`, `automation/tests/test_issue_447_floor_separation.py`, `automation/tests/test_issue_445_walkforward_history.py`, `automation/tests/test_issue_444_data_window.py`, `automation/tests/test_search_space_binding.py`, `automation/tests/test_combo_conjunction_switches.py`, `automation/AGENTS.md` |
+| 2026-06-25 | **IMPLEMENTIERUNG GitHub-Issues #449–#451 (Per-Symbol-Sweep OOS=0 für TSLA.ETORO — Defekt B von Pitfall #75).** Forensik des `--symbols TSLA.ETORO --n-jobs 6`-Logs: alle Trials aller sechs aktiven Strategien auf dem Unevaluable-Floor (−9.90…−9.93), `oos_total_trades=0`, obwohl der #448-Fix greift und IS 100–170 Trades erzeugt. **Diagnose (simulationsverifiziert):** Split-Math und `check_data_span` sind KORREKT — der Defekt ist daten- + telemetrie-seitig. Alle realen Fills liegen in `[start, start+is_window]`; das früheste OOS-Sub-Fenster (`start+180 d` = 2025-11-12) erhält null Fills ⇒ `oos_total_trades=0` strukturell, parameter-unabhängig (vermutlich H2-Katalog-Abdeckungslücke 2025-11→2026-05). Die entscheidende Zahl (`fill_ts_max` vs. OOS-Grenze) ging vor der Operator-Konsole verloren ⇒ über mehrere Sessions undiagnostiziert. **Parameter-Raum-Audit: SAUBER** (alle sechs Strategien — jeder gesampelte Knopf bindet an ein Config-Feld; #446 hatte die früheren Mismatches bereits behoben). **#449 (P0, Pitfall #82):** `extract_metrics` berechnet `oos_window_start_ns`/`oos_covered`; durchgereicht über Worker-Result → `write_tournament_json`-`data_window` → `parse_tournament`/`TournamentMetrics` → BEIDE `optimizer_trial_completed`-Events (`fill_ts_max`, `oos_window_start_ns`, `oos_covered`, `oos_coverage_gap_days`). OOS-Abdeckungs-Verletzung als WARN (nicht `raise` — ein raise→NULL verschluckt die Telemetrie). Gate-1-Preflight `data_reaches_oos_window` + `latest_ts_by_symbol`: Symbol, dessen jüngster Tick die OOS-Grenze nicht erreicht, wird VOR dem Sweep übersprungen (`OOS_WINDOW_UNREACHABLE`) statt 100 nutzlose Trials zu fahren — vollständig fail-open. **#450 (P1, Pitfall #83):** `floor_plateau_callback(stop_on_plateau=True)` stoppt die aussichtslose Study aktiv (spart ~84 Trials/Symbol); Default `False` ⇒ Unit-Tests mit Fake-Study unverändert; Observability-Invariante (ändert nie eine Reward-/Promotion-Entscheidung) bleibt. **#451 (P2, Pitfall #84):** `compute_walk_forward_window` als EINZIGE Fenster-Arithmetik (gegen Inline-Divergenz — die Wurzel dieser Bug-Klasse); `build_trial` delegiert (bit-identisch, gegen 2025-05-16→2026-05-11 verifiziert), Preflight nutzt dieselbe Funktion. Pitfall-Nummern kollisionsfrei (höchste vorher #81; neu #82/#83/#84). Testbare Teile lokal grün (`test_issue_449_oos_coverage.py`: 12 passed; volle importierbare Suite ohne Regression). **Operative Konsequenz für TSLA:** bei `oos_covered=False` H2-Katalog auffrischen (Backfill 2025-11→heute), dann erneut tunen. | `automation/backtest_runner.py`, `automation/optimizer/parsing.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/gate.py`, `automation/optimizer/sweep.py`, `automation/optimizer/trial_config.py`, `automation/tests/test_issue_449_oos_coverage.py`, `automation/AGENTS.md` | Sechs Code-/Config-Defekte + zwei Doku-Deliverables, alle test-gesichert (volle automation-Suite lokal grün: 351 passed). **#448 (P0, Pitfall #80) — struktureller OOS=0:** Wurzel = stiller Fill-`ts`-Fallback `getattr(f,'ts_event',getattr(f,'ts_init',0))` (a) `0`-Default ⇒ jeder Round-Trip als In-Sample; (b) Fallback-Report `generate_order_fills_report` hat `ts_last`, kein `ts_event`. Fix: `_fill_ts_ns` (fail-loud, `ts_event→ts_last→ts_init`) als einzige Lesestelle + Plausibilitäts-Assertion (`fill_ts_max<start_ns ∨ fill_ts_min≤0 ⇒ ValueError`). Reproduktion 142 uniforme Exits ⇒ IS=72/OOS=70. Pitfall #75 → 🟢. **#447 (P1) — Reward-Floor:** Floor-Separations- (`unevaluable_max=−9.75 < evaluable_min=−9.749`) und Saturations-Invariante (`reward(is=target)==reward(is=10·target)`) formal test-fixiert (Code war bereits korrekt). **#446 (P1, Pitfall #81) — Sampling↔Config:** Renames `bb_std→bb_std_dev`/`vwap_window→vwap_period`; Phantom-Volumen-/RSI-Keys entfernt (1h-Bars haben `volume=1.0`, VwapExhaustion hat kein RSI); `trend_tolerance_pct` in Combo verdrahtet; FlashCrash sampelt jetzt `bb_period`/`bb_std_dev`; zentrale Bindungs-Assertion `test_search_space_binding.py`. **#445 (P1) — Walk-Forward-Historie:** validierte Geometrie (180/45/4/45=405) behalten, ehrliche `data_history_days=450` (~15 Monate) deklariert (SSOT mit `strategy_defaults._schema`), Fail-Loud-Startup-Assertion in `build_trial`. **#444 (P2) — data_window:** Schreib-Seite ergänzt (`write_tournament_json` schreibt `data_window` inkl. `fill_ts_min/max`; Worker/`extract_metrics` liefern die Spanne); Round-Trip-Test. **#443 (P3) — Loop-Var-Shadowing:** innere Fold-Schleifen `i/j`→`fold` (verhaltensneutral). **#442/#441 (Doku):** `OPTIMIZER_PARAMETER_REFERENZ.md` (korrigierte Suchräume aller aktiven Strategien) neu; AGENTS.md: Pitfall #75 → 🟢, neue Pitfalls #80/#81, §18-Konventionen (Fill-`ts` fail-loud, Bindungs-Pflicht, kein Loop-Shadowing, Geometrie≤Historie). | `automation/backtest_runner.py`, `automation/optimizer/spaces.py`, `automation/optimizer/parsing.py`, `automation/optimizer/trial_config.py`, `automation/strategies/tesla_combo_strategy.py`, `automation/config/backtest.json`, `automation/config/strategy_defaults.json`, `automation/OPTIMIZER_PARAMETER_REFERENZ.md`, `automation/tests/test_issue_448_oos_split.py`, `automation/tests/test_issue_447_floor_separation.py`, `automation/tests/test_issue_445_walkforward_history.py`, `automation/tests/test_issue_444_data_window.py`, `automation/tests/test_search_space_binding.py`, `automation/tests/test_combo_conjunction_switches.py`, `automation/AGENTS.md` |
 | 2026-06-24 | **IMPLEMENTIERUNG GitHub-Issues #415–#423 (= interne #411–#416 Code + #417 Doku); Pitfalls #76–#79 → 🟢 BEHOBEN.** Die in der Forensik (Eintrag unten) als OFFEN dokumentierten Defekte sind jetzt chirurgisch implementiert UND test-gesichert (lokal grün: optimizer/sweep/runner-Suite 154 passed). **#411/#416-GH-#417 (DDL-Race, Pitfall #76):** prozessweiter `_study_lock` + `_create_study_with_retry` (genau EIN Retry nur auf `"already exists"`, sonst Fail-Fast Pitfall #66) + serielles `_preinit_study_storage` vor dem Pool; Test mit echtem SQLite & 8 Threads. **#412/GH-#415+#418 (Paar-Dedup, Pitfall #77):** order-preserving Dedup in `load_symbol_universe` & `enumerate_tunable_pairs` + Fail-Fast-`ValueError` bei kollidierendem `study_name`. **#413/GH-#419 (Floor-Guard v3):** `floor_plateau_callback` evaluable-basiert (`oos_evaluated`-User-Attr) statt Wert-Gleichheit; Legacy-Wert-Guard als Fallback; `make_symbol_objective` setzt `oos_evaluated`-Attr. **Defekt A (0 evaluable Trials) bleibt offen** (erfordert realen Katalog-Sweep, `data/` gitignored) ⇒ Pitfall #75 bleibt 🟡. **#414/GH-#420 (Logging-Init, Pitfall #78):** `setup_bot_logging("optimizer")` als erste Anweisung in `sweep.main()`. **#415/GH-#421 (Zeitdauer, Pitfall #79):** Wall-Clock in `run_backtest` (beide Modi, optionaler `timings`-Out-Param ⇒ signatur-kompat); `backtest_ms` im Per-Trial-Event; neue `optimizer_study_completed`/`sweep_completed`-Events + Konsolen-Schlusszeile. **#416/GH-#422 (Subprozess-Logs, Pitfall #79):** `_persist_subprocess_logs` schreibt stdout/stderr pro Trial nach `trial_dir/logs/` (auch im Erfolgsfall); `data_window_*`/`rejection_reason` zusätzlich im Event. CI-Gate um die neuen Tests erweitert. | `automation/optimizer/run_optimization.py`, `automation/optimizer/runner.py`, `automation/optimizer/sweep.py`, `automation/optimizer/parsing.py`, `automation/tests/test_issue_411_storage_ddl_race.py`, `automation/tests/test_issue_412_pair_dedup.py`, `automation/tests/test_issue_413_floor_guard_v3.py`, `automation/tests/test_issue_414_sweep_logging.py`, `automation/tests/test_issue_415_backtest_timing.py`, `automation/tests/test_issue_416_subprocess_logs.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
 | 2026-06-24 | **Forensik Sweep-Log (`--tier all --n-jobs 6`) + Issues #411–#417.** Sechs Defekte + AGENTS.md-Härtung aus der Analyse zweier Sweep-Logs. **#411 (P0)** Optuna/SQLite `create_all`-DDL-Race (`table studies already exists`) crasht den Sweep — `load_if_exists` schützt den Schema-Bootstrap NICHT; Fix: serialisiertes `_preinit_study_storage` + Retry (Pitfall #76). **#412 (P0)** doppelte `(strategy,symbol)`-Paare kollabieren N Worker auf eine Study (`…_WDAY_ETORO` Trial 499 bei n_trials=100), zerstören Reproduzierbarkeit (Pitfall #68) und lösen #411 aus; Fix: order-preserving Dedup in `load_symbol_universe`/`enumerate_tunable_pairs` + Fail-Fast-Assertion (Pitfall #77). **#413 (P1)** Floor-Kollaps besteht empirisch fort: alle Trials unevaluable (−9.85…−9.93 ⊂ [−10.0,−9.75)), 0 evaluable; `floor_plateau_callback` (#409) ist im v3-Shaping-Regime toter Code (Wert-Gleichheits-Prädikat trifft geshapete Sub-Floor-Werte nie) → Ersatz durch evaluable-basierten Guard; Pitfall #75 auf 🟡 TEILWEISE reklassifiziert. **#414 (P1)** Sweep-Entrypoint initialisiert kein Logging ⇒ #404-`[JSON_EVENT]`-Telemetrie (INFO) wird stumm verworfen; Fix: `setup_bot_logging("optimizer")` in `sweep.main()` (Pitfall #78). **#415 (P1)** Backtest-Zeitdauer wird nirgends ausgewiesen; Fix: Wall-Clock in `run_backtest` (beide Modi) + `backtest_ms` + `optimizer_study_completed`/`sweep_completed`-Summaries (Pitfall #79). **#416 (P2)** `run_backtest` verschluckt stdout/stderr im Erfolgsfall; Fix: pro Trial nach `trial_dir/logs/` persistieren (Pitfall #79). **#417 (P2)** AGENTS.md wasserdicht: #75 reklassifiziert + empirischer Nachtrag, #409-Bullet als ⚠️ ineffektiv markiert, #72-Parallel-Safety-Vorbedingung ergänzt, Pitfalls #76–#79 angelegt, §18 um Zeitdauer-/Logging-Init-/Eindeutigkeits-Pflicht erweitert. **Pitfall-Nummern kollisionsfrei (höchste #79).** | `automation/optimizer/run_optimization.py`, `automation/optimizer/runner.py`, `automation/optimizer/sweep.py`, `automation/config/` (Universe-Hygiene), `automation/tests/test_issue_411_storage_ddl_race.py`, `…_412_pair_dedup.py`, `…_413_floor_guard_v3.py`, `…_414_sweep_logging.py`, `…_415_backtest_timing.py`, `…_416_subprocess_logs.py`, `automation/AGENTS.md` |
 | 2026-06-24 | **Behebung Pitfall #75 (Per-Symbol-Sweep konstanter Reward −9.75 — Unevaluable-Floor-Kollaps): Issues #404–#410.** Die zwei kompoundierenden Defekte (Gewinner-Status vs. Evaluierbarkeit; Shaping-Sättigung) sind behoben — TPE hat im Per-Symbol-Sweep wieder einen Gradienten. **#404 (P0)** Per-Symbol-Telemetrie: `make_symbol_objective` emittiert `optimizer_trial_completed` (`symbol`, `oos_evaluated`, `oos_eligible` [trennt IS-/OOS-Drop], `oos_total_trades`, `oos_total_return`, `is_total_trades`, `is_max_trades`, `outcome`). **#405 (P0)** Evaluierbarkeit entkoppelt: `write_tournament_json` schreibt im Single-Symbol-Pfad einen `single_symbol_oos`-Block aus `r['_oos_eval']`/`r['oos_metrics']` (ungeachtet Gewinner-Status); `parse_tournament` nutzt ihn als Fallback bei fehlendem `aggregate_winner` (Multi-Symbol bit-identisch). **#406 (P1)** `per_symbol_shaping_trade_target` (400) verhindert die sofortige Shaping-Sättigung im Per-Symbol-Pfad. **#407 (P1)** `_gate_proximity` (aus `is_best_total_return`/`is_best_win_rate` gegen `shaping_return_target`/`shaping_winrate_target`) liefert einen kontinuierlichen, hart auf `unevaluable_shaping_span` gedeckelten Eligibility-Gradienten. **#408 (P2)** modale Rejection-Reason: `trial.set_user_attr("rejection_reason")` + `confirm._dominant_rejection` → `proposal["dominant_rejection"]`. **#409 (P2)** `floor_plateau_callback` warnt nach `n_startup_trials`, wenn alle Trials am Unevaluable-Floor kleben. **#410 (P3)** `reward_semantics_version: 3` + `_check_reward_semantics_version` (Study-Hygiene gegen alte Floor-Trials). **Invarianten:** Per-Symbol-Evaluierbarkeit ≠ Gewinner-Status; Anti-Gate-Gaming bleibt erhalten (Unevaluable < Evaluable-Floor — evaluierbare Trials werden IMMER besser bewertet). | `automation/optimizer/reward.py`, `automation/optimizer/parsing.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/confirm.py`, `automation/backtest_runner.py`, `automation/config/optimizer.json`, `automation/tests/test_issue_404_symbol_telemetry.py`, `automation/tests/test_issue_405_single_symbol_evaluability.py`, `automation/tests/test_issue_406_per_symbol_shaping.py`, `automation/tests/test_issue_407_gate_proximity.py`, `automation/tests/test_issue_408_rejection_surfacing.py`, `automation/tests/test_issue_409_floor_guard.py`, `automation/tests/test_issue_410_reward_versioning.py`, `automation/AGENTS.md` |
diff --git a/automation/backtest_runner.py b/automation/backtest_runner.py
index e3dbaba..e77c7a6 100644
--- a/automation/backtest_runner.py
+++ b/automation/backtest_runner.py
@@ -1030,6 +1030,23 @@ def extract_metrics(engine: BacktestEngine, starting_capital: float, log_fn=None
         fill_ts_min = min(_all_fill_ts) if _all_fill_ts else None
         fill_ts_max = max(_all_fill_ts) if _all_fill_ts else None
 
+        # Issue #449 (Pitfall #82) — OOS-Abdeckungsgrenze.
+        # Die früheste OOS-Sub-Fenster-Grenze ist `start_ns + is_window_ns`: ein Fill ist genau dann
+        # OOS-klassifizierbar, wenn sein Exit-ts >= dieser Grenze liegt (siehe Split-Logik unten,
+        # split_oos_start_ns = start_ns + fold*oos_window_ns + is_window_ns, minimal bei fold=0).
+        # Wird die Grenze von `fill_ts_max` nie erreicht, kann KEIN Fold OOS-Trades erhalten ⇒
+        # struktureller oos_total_trades=0-Kollaps (Floor-Plateau), UNABHÄNGIG von den Parametern.
+        # Diese Grenze + ein boolean `oos_covered` werden in die data_window-Telemetrie gehoben,
+        # damit die diagnostisch entscheidende Zahl (fill_ts_max vs. OOS-Grenze) am Operator-Konsol
+        # sichtbar ist statt verloren zu gehen (das war die Blindstelle über mehrere Sessions).
+        oos_window_start_ns = None
+        oos_covered = None
+        if walk_forward_dict and start_ns is not None:
+            _is_window_ns_cov = walk_forward_dict.get("is_window_days", 90) * 86400 * 1_000_000_000
+            oos_window_start_ns = start_ns + _is_window_ns_cov
+            if fill_ts_max is not None:
+                oos_covered = bool(fill_ts_max >= oos_window_start_ns)
+
         # Issue #448 (Pitfall #80) — Plausibilitäts-Assertion gegen den stillen OOS=0-Kollaps.
         # Im Walk-Forward-Modus MÜSSEN die Fills in der absoluten Epoch-ns-Domäne des Fensters
         # liegen. `fill_ts_max < start_ns` (ALLE Round-Trips lägen vor Fensterbeginn) oder
@@ -1045,6 +1062,19 @@ def extract_metrics(engine: BacktestEngine, starting_capital: float, log_fn=None
                     f"Vermutliche Ursache: Fill-ts aus falscher Clock-Domäne oder fehlendes "
                     f"ts_event/ts_last (siehe _fill_ts_ns)."
                 )
+            # Issue #449 (Pitfall #82) — OOS-Abdeckungs-Diagnose (NICHT fail-loud hier, da ein
+            # raise → NULL-Rückgabe die Telemetrie verschluckt; die harte Vorab-Abweisung gehört
+            # in das Sweep-Gate-1-Preflight). Eine sichtbare WARN-Zeile genügt, um den OOS=0-Grund
+            # eindeutig von "Strategie handelt nicht im OOS" abzugrenzen: hier reichen die DATEN
+            # nicht bis ins OOS-Sub-Fenster.
+            if oos_covered is False and log_fn:
+                _gap_days = (oos_window_start_ns - fill_ts_max) / (86400 * 1_000_000_000)
+                log_fn(
+                    f"[OOS-Coverage] WARN (Pitfall #82): fill_ts_max liegt {_gap_days:.1f} Tage VOR "
+                    f"der frühesten OOS-Grenze (start_ns + is_window). Kein Fold kann OOS-Trades "
+                    f"erhalten ⇒ oos_total_trades=0 ist strukturell, nicht parameterbedingt. "
+                    f"Ursache liegt in der H2-Datenabdeckung/-Aktualität, nicht in den Parametern."
+                )
 
         is_pnls = []
         oos_pnls = []
@@ -1160,6 +1190,9 @@ def extract_metrics(engine: BacktestEngine, starting_capital: float, log_fn=None
                 # Issue #444/#448 — beobachtete Fill-ts-Spanne für die data_window-Telemetrie.
                 "_fill_ts_min": fill_ts_min,
                 "_fill_ts_max": fill_ts_max,
+                # Issue #449 — OOS-Abdeckungsgrenze + Flag für data_window-Telemetrie.
+                "_oos_window_start_ns": oos_window_start_ns,
+                "_oos_covered": oos_covered,
             }
         else:
             # Fallback for backwards compatibility if oos isn't requested
@@ -1171,7 +1204,7 @@ def extract_metrics(engine: BacktestEngine, starting_capital: float, log_fn=None
         # Issue #448 — formgleicher Rückgabewert: im Walk-Forward-Modus das nested NULL-Dict, sonst
         # das flache NULL (der Worker behandelt beide, aber Formgleichheit hält die Telemetrie sauber).
         if walk_forward_dict and start_ns is not None:
-            return {"metrics": NULL, "oos_metrics": NULL, "_fill_ts_min": None, "_fill_ts_max": None}
+            return {"metrics": NULL, "oos_metrics": NULL, "_fill_ts_min": None, "_fill_ts_max": None, "_oos_window_start_ns": None, "_oos_covered": None}
         return NULL
 
 
@@ -1676,8 +1709,20 @@ def write_tournament_json(
     _fill_maxs = [r.get("_fill_ts_max") for r in all_results if r.get("_fill_ts_max") is not None]
     fill_ts_min = min(_fill_mins) if _fill_mins else None
     fill_ts_max = max(_fill_maxs) if _fill_maxs else None
+    # Issue #449 (Pitfall #82) — OOS-Abdeckungsgrenze aggregieren. oos_window_start_ns ist über alle
+    # Worker identisch (gleiches start_ns + is_window); oos_covered ist genau dann True, wenn die
+    # global maximale Fill-ts die früheste OOS-Grenze erreicht. False ⇒ struktureller OOS=0-Kollaps
+    # aus Datenabdeckung, nicht aus Parametern (die diagnostisch entscheidende Unterscheidung).
+    _oos_starts = [r.get("_oos_window_start_ns") for r in all_results if r.get("_oos_window_start_ns") is not None]
+    oos_window_start_ns = min(_oos_starts) if _oos_starts else None
+    oos_covered = None
+    if oos_window_start_ns is not None and fill_ts_max is not None:
+        oos_covered = bool(fill_ts_max >= oos_window_start_ns)
     if start_ns is not None or end_ns is not None or fill_ts_min is not None:
         _day_ns = 86400 * 1_000_000_000
+        _oos_gap_days = None
+        if oos_window_start_ns is not None and fill_ts_max is not None and oos_covered is False:
+            _oos_gap_days = round((oos_window_start_ns - fill_ts_max) / _day_ns, 1)
         output["data_window"] = {
             "start_ns": start_ns,
             "end_ns":   end_ns,
@@ -1686,6 +1731,11 @@ def write_tournament_json(
             "days":     round((end_ns - start_ns) / _day_ns, 1) if (start_ns and end_ns) else None,
             "fill_ts_min": fill_ts_min,
             "fill_ts_max": fill_ts_max,
+            # Issue #449 — OOS-Abdeckungs-Telemetrie.
+            "oos_window_start_ns": oos_window_start_ns,
+            "oos_window_start": pd.Timestamp(oos_window_start_ns, unit="ns", tz="UTC").isoformat() if oos_window_start_ns else None,
+            "oos_covered": oos_covered,
+            "oos_coverage_gap_days": _oos_gap_days,
         }
 
     Path(output_path).parent.mkdir(parents=True, exist_ok=True)
@@ -2070,6 +2120,9 @@ def run_single_backtest_worker(
         # data_window-Block der tournament_result.json). None, wenn keine Round-Trips/kein WF-Modus.
         fill_ts_min = extracted_data.get("_fill_ts_min") if isinstance(extracted_data, dict) else None
         fill_ts_max = extracted_data.get("_fill_ts_max") if isinstance(extracted_data, dict) else None
+        # Issue #449 — OOS-Abdeckungsgrenze + Flag nach oben reichen (data_window-Telemetrie).
+        oos_window_start_ns = extracted_data.get("_oos_window_start_ns") if isinstance(extracted_data, dict) else None
+        oos_covered = extracted_data.get("_oos_covered") if isinstance(extracted_data, dict) else None
 
         def format_metric(m_dict, key, min_trades_req):
             if m_dict.get('total_trades', 0) < min_trades_req:
@@ -2146,6 +2199,8 @@ def run_single_backtest_worker(
             "_last_tick_ns": last_tick_ns_val,
             "_fill_ts_min": fill_ts_min,
             "_fill_ts_max": fill_ts_max,
+            "_oos_window_start_ns": oos_window_start_ns,
+            "_oos_covered": oos_covered,
         }
     finally:
         if temp_catalog_dir and os.path.exists(temp_catalog_dir):
diff --git a/automation/optimizer/gate.py b/automation/optimizer/gate.py
index 1220665..4c965b1 100644
--- a/automation/optimizer/gate.py
+++ b/automation/optimizer/gate.py
@@ -51,3 +51,35 @@ def is_symbol_tunable(symbol: str, n_params: int, *, available_bars: int,
         return (False, "OOS_FOLD_TOO_SHORT")
 
     return (True, "OK")
+
+
+def data_reaches_oos_window(
+    *,
+    newest_ns: int | None,
+    start_ns: int,
+    is_window_days: int,
+    recency_grace_days: float = 0.0,
+) -> tuple[bool, float]:
+    """Issue #449 (Pitfall #82) — reine OOS-Erreichbarkeits-Prüfung für das Sweep-Gate-1-Preflight.
+
+    Gate 1 (a)-(c) prüft die Bar-ANZAHL, aber NICHT die AKTUALITÄT: ein Symbol kann ≥ required_bars
+    besitzen und trotzdem ausschließlich Historie in der ersten Fensterhälfte haben — dann bleibt
+    das OOS-Sub-Fenster leer und JEDER Trial kollabiert strukturell auf den Unevaluable-Floor
+    (oos_total_trades=0), unabhängig von den Parametern. Diese Funktion prüft die notwendige
+    Bedingung „der jüngste Tick erreicht die früheste OOS-Grenze".
+
+    Die früheste OOS-Grenze ist ``start_ns + is_window_days`` (= split_oos_start bei fold=0, siehe
+    extract_metrics). ``newest_ns`` muss diese Grenze (minus optionaler Karenz) erreichen.
+
+    Gibt ``(ok, gap_days)`` zurück; ``gap_days`` > 0 = Abstand des jüngsten Ticks VOR der OOS-Grenze
+    (nur aussagekräftig, wenn ok=False). ``newest_ns=None`` (unbekannt/gemockt) ⇒ fail-open (True),
+    damit das Preflight nie strenger ist als die vorhandene Information erlaubt.
+    """
+    if newest_ns is None:
+        return (True, 0.0)
+    day_ns = 86400 * 1_000_000_000
+    oos_window_start_ns = start_ns + int(is_window_days * day_ns)
+    grace_ns = int(recency_grace_days * day_ns)
+    gap_days = (oos_window_start_ns - newest_ns) / day_ns
+    ok = newest_ns >= (oos_window_start_ns - grace_ns)
+    return (ok, gap_days)
diff --git a/automation/optimizer/parsing.py b/automation/optimizer/parsing.py
index 82d312f..7720822 100644
--- a/automation/optimizer/parsing.py
+++ b/automation/optimizer/parsing.py
@@ -34,6 +34,12 @@ class TournamentMetrics:
     # Diagnose direkt sichtbar. None, wenn der Block (oder die Felder) fehlen (rückwärtskompatibel).
     fill_ts_min: int | None = None
     fill_ts_max: int | None = None
+    # Issue #449 (Pitfall #82) — OOS-Abdeckungsgrenze + Flag. oos_covered=False ⇒ die Daten reichen
+    # nicht bis in das früheste OOS-Sub-Fenster ⇒ oos_total_trades=0 ist strukturell (Datenabdeckung),
+    # nicht parameterbedingt. oos_coverage_gap_days = Abstand fill_ts_max → OOS-Grenze in Tagen.
+    oos_window_start_ns: int | None = None
+    oos_covered: bool | None = None
+    oos_coverage_gap_days: float | None = None
 
 def parse_tournament(path: Path) -> TournamentMetrics:
     """Liest aggregate_winner/oos_metrics typsicher (None-safe).
@@ -102,6 +108,10 @@ def parse_tournament(path: Path) -> TournamentMetrics:
     dw_days = dw.get("days")
     dw_fill_min = dw.get("fill_ts_min")
     dw_fill_max = dw.get("fill_ts_max")
+    # Issue #449 — OOS-Abdeckungs-Telemetrie (None-safe, rückwärtskompatibel).
+    dw_oos_start = dw.get("oos_window_start_ns")
+    dw_oos_covered = dw.get("oos_covered")
+    dw_oos_gap = dw.get("oos_coverage_gap_days")
 
     return TournamentMetrics(
         oos_evaluated=bool(oos_evaluated),
@@ -122,4 +132,7 @@ def parse_tournament(path: Path) -> TournamentMetrics:
         data_window_days=float(dw_days) if dw_days is not None else None,
         fill_ts_min=int(dw_fill_min) if dw_fill_min is not None else None,
         fill_ts_max=int(dw_fill_max) if dw_fill_max is not None else None,
+        oos_window_start_ns=int(dw_oos_start) if dw_oos_start is not None else None,
+        oos_covered=bool(dw_oos_covered) if dw_oos_covered is not None else None,
+        oos_coverage_gap_days=float(dw_oos_gap) if dw_oos_gap is not None else None,
     )
diff --git a/automation/optimizer/run_optimization.py b/automation/optimizer/run_optimization.py
index 81e6a4b..a082805 100644
--- a/automation/optimizer/run_optimization.py
+++ b/automation/optimizer/run_optimization.py
@@ -112,7 +112,8 @@ def log_active_config(context: str, *, base_cfg: Path | None = None, extra: dict
 
 def floor_plateau_callback(study, trial, *, weights: dict | None = None,
                            n_startup_trials: int | None = None, eps: float = 1e-6,
-                           logger: logging.Logger | None = None) -> None:
+                           logger: logging.Logger | None = None,
+                           stop_on_plateau: bool = False) -> None:
     """Issue #409/#413 (P2) — Fail-Loud-Guard gegen den Unevaluable-Floor-Kollaps (Pitfall #75).
 
     Optuna-Callback (Signatur ``(study, trial)``; ``weights``/``n_startup_trials``/``logger`` sind
@@ -160,6 +161,17 @@ def floor_plateau_callback(study, trial, *, weights: dict | None = None,
                 "Symbol ist derzeit ein No-Op.",
                 len(completed),
             )
+            # Issue #450 (Pitfall #83) — bei erkanntem Plateau die Study aktiv stoppen statt die
+            # restlichen ~84 Trials als Zero-Gradient-Zufallsgenerator weiterlaufen zu lassen
+            # (≈30 min verschwendete Compute pro voller Sweep-Runde). Opt-in (nur Produktion bindet
+            # stop_on_plateau=True); Unit-Tests mit Fake-Study bleiben unberührt.
+            if stop_on_plateau:
+                _stop = getattr(study, "stop", None)
+                if callable(_stop):
+                    try:
+                        _stop()
+                    except Exception:  # Study nicht in optimize()-Kontext → Warnung genügt
+                        pass
         return
 
     # Legacy-Fallback (kein oos_evaluated-Attr, z. B. alte Studies / globaler make_objective-Pfad):
@@ -176,6 +188,14 @@ def floor_plateau_callback(study, trial, *, weights: dict | None = None,
             "und Gates; verwirf ggf. die stale Study (rm data/optimizer/sweep/*.db).",
             len(completed), floor,
         )
+        # Issue #450 (Pitfall #83) — siehe oben: aktiv stoppen statt weiterlaufen (opt-in).
+        if stop_on_plateau:
+            _stop = getattr(study, "stop", None)
+            if callable(_stop):
+                try:
+                    _stop()
+                except Exception:
+                    pass
 
 
 def _check_reward_semantics_version(study, opt_data: dict,
@@ -283,7 +303,12 @@ def make_objective(
             "win_count": metrics.win_count,
             "is_total_trades": metrics.is_total_trades,
             "is_max_trades": metrics.is_max_trades,
-            "outcome": outcome
+            "outcome": outcome,
+            # Issue #449 (Pitfall #82) — OOS-Abdeckungs-Diagnose auch im Multi-Symbol-Event.
+            "fill_ts_max": metrics.fill_ts_max,
+            "oos_window_start_ns": metrics.oos_window_start_ns,
+            "oos_covered": metrics.oos_covered,
+            "oos_coverage_gap_days": metrics.oos_coverage_gap_days,
         })
 
         return reward
@@ -333,7 +358,7 @@ def optimize(strategy: str, n_trials: int | None = None, n_jobs: int = 1):
     _check_reward_semantics_version(study, opt_data)
 
     # Issue #409 — Fail-Loud-Guard auch im globalen Pfad (gleicher Floor-Kollaps moeglich).
-    floor_guard = partial(floor_plateau_callback, weights=opt_data, n_startup_trials=n_startup_trials)
+    floor_guard = partial(floor_plateau_callback, weights=opt_data, n_startup_trials=n_startup_trials, stop_on_plateau=True)
     study.optimize(
         make_objective(strategy),
         n_trials=n_trials,
@@ -539,6 +564,14 @@ def make_symbol_objective(strategy: str, symbol: str, global_params: dict,
             "data_window_start": metrics.data_window_start,
             "data_window_end": metrics.data_window_end,
             "data_window_days": metrics.data_window_days,
+            # Issue #449 (Pitfall #82) — die diagnostisch entscheidenden Zahlen, die bisher vor der
+            # Operator-Konsole verloren gingen: erreicht fill_ts_max die früheste OOS-Grenze? Bei
+            # oos_covered=False ist OOS=0 strukturell (H2-Datenabdeckung), NICHT parameterbedingt —
+            # damit ist der Floor-Plateau-Grund auf einen Blick eindeutig.
+            "fill_ts_max": metrics.fill_ts_max,
+            "oos_window_start_ns": metrics.oos_window_start_ns,
+            "oos_covered": metrics.oos_covered,
+            "oos_coverage_gap_days": metrics.oos_coverage_gap_days,
         })
         return reward
     return objective
@@ -598,7 +631,7 @@ def optimize_symbol(strategy: str, symbol: str, n_trials: int | None = None,
     )
     # Issue #409 — Fail-Loud-Guard: warnt, sobald nach n_startup_trials alle Trials am
     # Unevaluable-Floor kleben (Pitfall #75). Config einmalig gebunden (kein Per-Trial-IO).
-    floor_guard = partial(floor_plateau_callback, weights=opt_data, n_startup_trials=n_startup_trials)
+    floor_guard = partial(floor_plateau_callback, weights=opt_data, n_startup_trials=n_startup_trials, stop_on_plateau=True)
     study.optimize(objective, n_trials=n_trials, n_jobs=1,
                    catch=(json.JSONDecodeError, OSError), callbacks=[floor_guard])
     # Issue #415 — Per-Study-Summary (Timing + Evaluierbarkeit) als strukturiertes Event.
diff --git a/automation/optimizer/sweep.py b/automation/optimizer/sweep.py
index 442520f..94f9b94 100644
--- a/automation/optimizer/sweep.py
+++ b/automation/optimizer/sweep.py
@@ -17,8 +17,8 @@ import time
 from pathlib import Path
 
 from automation.optimizer import bounds
-from automation.optimizer.gate import is_symbol_tunable
-from automation.optimizer.trial_config import config_dir
+from automation.optimizer.gate import is_symbol_tunable, data_reaches_oos_window
+from automation.optimizer.trial_config import config_dir, compute_walk_forward_window
 from automation.optimizer.manifest import WORK
 from automation.optimizer.run_optimization import (
     optimize_symbol as _optimize_symbol,
@@ -145,19 +145,71 @@ def count_available_bars(symbols, *, catalog_path: Path | None = None) -> dict[s
     return out
 
 
+def latest_ts_by_symbol(symbols, *, catalog_path: Path | None = None) -> dict[str, int | None]:
+    """Issue #449 — jüngster ts_event (Epoch-ns) je Symbol aus den Parquet-Row-Group-Statistiken.
+
+    Parallel zu ``count_available_bars`` (gleiche Datei/Statistik-Quelle), liefert aber die ABSOLUTE
+    Aktualität statt nur die Spanne — die Zahl, die das OOS-Erreichbarkeits-Preflight (Gate 1) braucht.
+    Fail-open: fehlt die Datei / schlägt das Lesen fehl / fehlt die Statistik, ist der Wert ``None``
+    (das Preflight wird dann für dieses Symbol übersprungen, nie strenger als die Datenlage erlaubt).
+    Im CI gemockt (HI-7)."""
+    if catalog_path is None:
+        base = config_dir()
+        raw = "data/nautilus"
+        bt = base / "backtest.json"
+        if bt.exists():
+            try:
+                with open(bt, "r", encoding="utf-8") as f:
+                    raw = (json.load(f) or {}).get("catalog_path", "data/nautilus")
+            except (OSError, ValueError):
+                pass
+        catalog_path = base.parent.parent / raw
+
+    out: dict[str, int | None] = {}
+    for sym in symbols:
+        newest: int | None = None
+        pq_file = Path(catalog_path) / "data" / "quote_tick" / sym / "data.parquet"
+        if pq_file.exists():
+            try:
+                import pyarrow.parquet as pq
+                pf = pq.ParquetFile(str(pq_file))
+                if "ts_event" in pf.schema.names:
+                    idx = pf.schema.names.index("ts_event")
+                    for rg in range(pf.metadata.num_row_groups):
+                        st = pf.metadata.row_group(rg).column(idx).statistics
+                        hi = int(st.max)
+                        newest = hi if newest is None else max(newest, hi)
+            except Exception:
+                newest = None
+        out[sym] = newest
+    return out
+
+
 def enumerate_tunable_pairs(strategies: list[str], symbols: list[str] | None,
                             *, tier: str, available_bars: dict[str, int],
-                            config: dict) -> list[tuple[str, str, str]]:
+                            config: dict,
+                            latest_ts: dict[str, int | None] | None = None,
+                            oos_window_start_ns: int | None = None,
+                            recency_grace_days: float = 0.0,
+                            logger: logging.Logger | None = None) -> list[tuple[str, str, str]]:
     """Enumeriert (strategy, symbol, 'OK')-Tripel.
 
     1. Symbol-Liste = ``symbols`` or ``load_symbol_universe()``.
     2. Tier: 'deployable' (nur Tier-A-Gewinner pro Strategie), 'refine' (Platzhalter, P3),
        'all' (Kreuzprodukt strategies × Symbole).
     3. Gate 1: ``is_symbol_tunable(...)`` muss True sein.
+    4. Issue #449 — OOS-Erreichbarkeits-Preflight: wenn ``latest_ts``/``oos_window_start_ns``
+       übergeben sind, wird ein Symbol, dessen jüngster Tick die früheste OOS-Grenze NICHT erreicht,
+       verworfen (Grund 'OOS_WINDOW_UNREACHABLE') und einmalig als WARN geloggt — statt 100 nutzlose
+       Trials zu fahren, die strukturell auf dem Floor kollabieren. Fehlen die Argumente (oder ist der
+       Symbol-Wert None), greift das Preflight nicht (fail-open ⇒ Verhalten bit-identisch zu vorher).
     Ausgeschlossene Paare sind NICHT enthalten.
     """
     syms = symbols if symbols else load_symbol_universe()
     winners = load_tier_a_winners() if tier == "deployable" else {}
+    if logger is None:
+        logger = logging.getLogger("optimizer")
+    _warned_unreachable: set[str] = set()
 
     pairs: list[tuple[str, str, str]] = []
     for strategy in strategies:
@@ -173,8 +225,28 @@ def enumerate_tunable_pairs(strategies: list[str], symbols: list[str] | None,
         for symbol in candidate_syms:
             ok, _reason = is_symbol_tunable(
                 symbol, n_params, available_bars=available_bars.get(symbol, 0), config=config)
-            if ok:
-                pairs.append((strategy, symbol, "OK"))
+            if not ok:
+                continue
+            # Issue #449 — OOS-Erreichbarkeit (Aktualität), nur wenn Telemetrie vorliegt.
+            if latest_ts is not None and oos_window_start_ns is not None:
+                reach_ok, gap_days = data_reaches_oos_window(
+                    newest_ns=latest_ts.get(symbol),
+                    start_ns=oos_window_start_ns,  # bereits = start_ns + is_window (s. Aufrufer)
+                    is_window_days=0,              # Grenze ist schon eingerechnet
+                    recency_grace_days=recency_grace_days,
+                )
+                if not reach_ok:
+                    if symbol not in _warned_unreachable:
+                        _warned_unreachable.add(symbol)
+                        logger.warning(
+                            "⛔ OOS-Preflight (Pitfall #82): Symbol %s übersprungen — jüngster Tick "
+                            "liegt %.1f Tage VOR der frühesten OOS-Grenze. Kein Trial könnte "
+                            "evaluierbare OOS-Trades erzeugen (struktureller Floor-Kollaps). "
+                            "Ursache: H2-Katalog-Abdeckung/-Aktualität. Daten auffrischen, dann erneut "
+                            "tunen.", symbol, gap_days,
+                        )
+                    continue
+            pairs.append((strategy, symbol, "OK"))
 
     # Issue #412 — order-preserving Dedup der (strategy, symbol)-Tripel. Selbst wenn die Symbol-
     # Liste schon dedupliziert ist (load_symbol_universe), schuetzt dies gegen Duplikate aus einer
@@ -230,8 +302,37 @@ def run_per_symbol_sweep(strategies: list[str], symbols: list[str] | None = None
     config = _load_gate_config()
     available_bars = count_available_bars(syms)
 
+    # Issue #449 — OOS-Erreichbarkeits-Preflight vorbereiten: dieselbe Fenster-Arithmetik wie die
+    # Trials (geteilte reine Funktion, #451) ⇒ früheste OOS-Grenze = start + is_window. Plus den
+    # jüngsten Katalog-Tick je Symbol (fail-open). So wird ein Symbol mit H2-Abdeckungslücke VOR dem
+    # Sweep mit klarer Begründung übersprungen statt 100 Floor-Trials zu fahren. Vollständig
+    # fail-open: fehlt/ist die walk_forward-Geometrie unvollständig oder schlägt das Katalog-Lesen
+    # fehl, bleibt das Preflight aus (Verhalten bit-identisch zum regulären Lauf).
+    latest_ts = None
+    oos_window_start_ns = None
+    try:
+        import datetime as _dt
+        _wf = config.get("walk_forward") or {}
+        if all(k in _wf for k in ("holdout_days", "is_window_days", "oos_window_days", "splits")):
+            _wstart, _wend = compute_walk_forward_window(
+                now=_dt.datetime.now(_dt.timezone.utc),
+                holdout_days=_wf["holdout_days"],
+                is_window_days=_wf["is_window_days"],
+                oos_window_days=_wf["oos_window_days"],
+                n_folds=_wf["splits"],
+            )
+            _start_ns = int(_wstart.timestamp() * 1_000_000_000)
+            _day_ns = 86400 * 1_000_000_000
+            oos_window_start_ns = _start_ns + _wf["is_window_days"] * _day_ns
+            latest_ts = latest_ts_by_symbol(syms)
+    except Exception:
+        latest_ts = None  # fail-open: Preflight aus, regulärer Lauf
+        oos_window_start_ns = None
+
     pairs = enumerate_tunable_pairs(strategies, syms, tier=tier,
-                                    available_bars=available_bars, config=config)
+                                    available_bars=available_bars, config=config,
+                                    latest_ts=latest_ts,
+                                    oos_window_start_ns=oos_window_start_ns)
 
     # Issue #412 — harte Eindeutigkeits-Assertion (Fail-Fast statt stiller Kollision, Pitfall #66).
     # enumerate_tunable_pairs dedupliziert bereits; diese Assertion ist der Guertel-und-Hosentraeger-
diff --git a/automation/optimizer/trial_config.py b/automation/optimizer/trial_config.py
index 4b9ad81..0d9eba0 100644
--- a/automation/optimizer/trial_config.py
+++ b/automation/optimizer/trial_config.py
@@ -13,6 +13,35 @@ def config_dir() -> Path:
     # Default to automation/config from WORK parent
     return WORK.parent.parent / "automation" / "config"
 
+
+def compute_walk_forward_window(
+    *,
+    now: dt.datetime,
+    holdout_days: int,
+    is_window_days: int,
+    oos_window_days: int,
+    n_folds: int,
+) -> tuple[dt.datetime, dt.datetime]:
+    """Issue #451 — EINZIGE Quelle der Walk-Forward-Fenster-Arithmetik (Pitfall #84).
+
+    Bisher lebte diese Berechnung ausschließlich inline in ``build_trial``. Das Sweep-Gate-1-
+    Preflight (#449) braucht exakt dieselbe Grenze, um zu prüfen, ob die Daten bis ins OOS-Sub-
+    Fenster reichen. Eine zweite, parallele Implementierung wäre genau die Divergenz-Footgun, die
+    diese Bug-Klasse (start_ns für Laden ≠ start_ns für Split) überhaupt erst erzeugt — deshalb
+    teilen sich beide Aufrufer diese eine reine Funktion.
+
+    Regel (unverändert ggü. dem alten Inline-Code):
+      end   = Mitternacht(now); wenn Sonntag → −1 Tag; dann − holdout_days
+      start = end − (is_window_days + n_folds * oos_window_days)
+    """
+    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
+    if end.weekday() == 6:  # Sonntag → Samstag
+        end -= dt.timedelta(days=1)
+    end -= dt.timedelta(days=holdout_days)
+    start = end - dt.timedelta(days=is_window_days + n_folds * oos_window_days)
+    return start, end
+
+
 def build_trial(
     strategy_class: str,
     sampled: dict,
@@ -83,15 +112,15 @@ def build_trial(
         "walk_forward_active": True,
     }
 
-    # Calculate dates
-    # Midnight of `now`
-    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
-    # If Sunday (weekday() == 6), rollback to Saturday
-    if end.weekday() == 6:
-        end -= dt.timedelta(days=1)
-
-    end -= dt.timedelta(days=holdout_days)
-    start = end - dt.timedelta(days=is_window_days + n_folds * oos_window_days)
+    # Calculate dates — Issue #451: delegiert an die geteilte reine Funktion (Single Source of
+    # Truth), die auch das Sweep-Gate-1-Preflight nutzt. Verhindert Divergenz der Fenster-Grenzen.
+    start, end = compute_walk_forward_window(
+        now=now,
+        holdout_days=holdout_days,
+        is_window_days=is_window_days,
+        oos_window_days=oos_window_days,
+        n_folds=n_folds,
+    )
 
     # Setup directories
     trial_dir = WORK / study_name / f"trial_{trial_number:04d}"
diff --git a/automation/tests/test_issue_449_oos_coverage.py b/automation/tests/test_issue_449_oos_coverage.py
new file mode 100644
index 0000000..e36afd8
--- /dev/null
+++ b/automation/tests/test_issue_449_oos_coverage.py
@@ -0,0 +1,221 @@
+"""Issue #449/#450/#451 — OOS-Abdeckungs-Blindstelle, Floor-Stopp & geteilte Fenster-Arithmetik.
+
+Hintergrund (Pitfall #82): Der Per-Symbol-Sweep kollabiert für ein Symbol auf den Unevaluable-Floor
+(oos_total_trades=0), wenn die Daten zwar die geforderte ANZAHL Bars besitzen (Gate-1 (a)–(c)
+bestehen), aber ausschließlich Historie in der ersten Fensterhälfte enthalten — das früheste
+OOS-Sub-Fenster ``[start + is_window, …]`` bleibt leer, und JEDER Trial ist strukturell unevaluable,
+UNABHÄNGIG von den Parametern. Bisher ging genau die diagnostische Zahl (jüngster Tick vs. OOS-Grenze)
+vor der Operator-Konsole verloren.
+
+Diese Tests decken die rein funktionalen, nautilus-freien Teile der Behebung ab:
+  * #451 — ``compute_walk_forward_window`` ist die EINZIGE Fenster-Arithmetik (auch fürs Preflight).
+  * #449 — ``data_reaches_oos_window`` (Gate-Helper) + ``enumerate_tunable_pairs``-Preflight.
+  * #450 — ``floor_plateau_callback(stop_on_plateau=True)`` stoppt die Study aktiv.
+"""
+import datetime as dt
+import logging
+
+import optuna
+
+from automation.optimizer.trial_config import compute_walk_forward_window
+from automation.optimizer.gate import data_reaches_oos_window
+from automation.optimizer import sweep
+from automation.optimizer import run_optimization as ro
+
+DAY = 86400 * 1_000_000_000
+
+
+# ---------------------------------------------------------------------------
+# #451 — geteilte Walk-Forward-Fenster-Arithmetik (Single Source of Truth)
+# ---------------------------------------------------------------------------
+def test_window_reproduces_known_dates():
+    """Donnerstag 2026-06-25, Standard-Geometrie ⇒ exakt das im Log beobachtete Fenster."""
+    now = dt.datetime(2026, 6, 25, 14, 0, tzinfo=dt.timezone.utc)
+    start, end = compute_walk_forward_window(
+        now=now, holdout_days=45, is_window_days=180, oos_window_days=45, n_folds=4)
+    assert str(start.date()) == "2025-05-16"
+    assert str(end.date()) == "2026-05-11"
+
+
+def test_window_sunday_rollback():
+    """Sonntag-Anker rollt auf Samstag zurück, BEVOR das Holdout abgezogen wird."""
+    now_sun = dt.datetime(2026, 6, 21, 9, 0, tzinfo=dt.timezone.utc)  # weekday() == 6
+    assert now_sun.weekday() == 6
+    _start, end = compute_walk_forward_window(
+        now=now_sun, holdout_days=45, is_window_days=180, oos_window_days=45, n_folds=4)
+    # Samstag 2026-06-20 − 45 Tage = 2026-05-06
+    assert str(end.date()) == "2026-05-06"
+
+
+def test_build_trial_uses_shared_window(monkeypatch, tmp_path):
+    """build_trial darf KEINE zweite Fenster-Implementierung haben — es muss die geteilte
+    Funktion aufrufen (sonst lebt die Divergenz-Footgun weiter). Wir patchen die geteilte
+    Funktion und prüfen, dass build_trial ihren Rückgabewert verwendet."""
+    from automation.optimizer import trial_config as tc
+
+    sentinel_start = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
+    sentinel_end = dt.datetime(2020, 12, 31, tzinfo=dt.timezone.utc)
+    called = {}
+
+    def _fake_window(**kw):
+        called.update(kw)
+        return sentinel_start, sentinel_end
+
+    monkeypatch.setattr(tc, "compute_walk_forward_window", _fake_window)
+    # Minimal-Config, damit build_trial nicht an fehlenden Dateien stirbt.
+    cfg = tmp_path / "config"
+    cfg.mkdir()
+    (cfg / "backtest.json").write_text(
+        '{"start_capital":10000,"walk_forward":{"is_window_days":180,"oos_window_days":45,'
+        '"splits":4,"holdout_days":45,"data_history_days":450}}', "utf-8")
+    (cfg / "strategies.json").write_text('{"strategies":[]}', "utf-8")
+
+    # resolve_params/Manifest-Schreiben ist hier Nebensache; wir wollen nur den Fenster-Aufruf prüfen.
+    monkeypatch.setattr(tc, "resolve_params", lambda *a, **k: {})
+    monkeypatch.setattr(tc, "git_commit", lambda: "deadbeef")
+    monkeypatch.setattr(tc, "catalog_fingerprint", lambda *a, **k: "fp")
+    monkeypatch.setattr(tc, "sha256_file", lambda *a, **k: "sha")
+
+    try:
+        tc.build_trial("SmaCrossoverStrategy", {}, study_name="s", trial_number=0, seed=1,
+                       base_cfg=cfg, copy_config=False)
+    except Exception:
+        pass  # Manifest-Details irrelevant — der Fenster-Aufruf ist das Prüfziel.
+
+    assert called, "build_trial muss compute_walk_forward_window aufrufen (keine Inline-Kopie)"
+    assert called.get("holdout_days") == 45 and called.get("is_window_days") == 180
+
+
+# ---------------------------------------------------------------------------
+# #449 — OOS-Erreichbarkeits-Gate-Helper
+# ---------------------------------------------------------------------------
+def _boundary():
+    now = dt.datetime(2026, 6, 25, 14, 0, tzinfo=dt.timezone.utc)
+    start, _ = compute_walk_forward_window(
+        now=now, holdout_days=45, is_window_days=180, oos_window_days=45, n_folds=4)
+    return int(start.timestamp() * 1e9) + 180 * DAY  # = 2025-11-12
+
+
+def test_reaches_oos_true_when_data_recent():
+    b = _boundary()
+    newest = int(dt.datetime(2026, 5, 8, tzinfo=dt.timezone.utc).timestamp() * 1e9)
+    ok, gap = data_reaches_oos_window(newest_ns=newest, start_ns=b, is_window_days=0)
+    assert ok is True and gap < 0
+
+
+def test_reaches_oos_false_when_data_stops_in_h1():
+    b = _boundary()
+    newest = int(dt.datetime(2025, 9, 30, tzinfo=dt.timezone.utc).timestamp() * 1e9)
+    ok, gap = data_reaches_oos_window(newest_ns=newest, start_ns=b, is_window_days=0)
+    assert ok is False and gap > 0  # ~43 Tage vor der OOS-Grenze
+
+
+def test_reaches_oos_failopen_on_unknown():
+    b = _boundary()
+    ok, gap = data_reaches_oos_window(newest_ns=None, start_ns=b, is_window_days=0)
+    assert ok is True and gap == 0.0
+
+
+def test_reaches_oos_respects_grace():
+    b = _boundary()
+    # Tick genau 3 Tage vor der Grenze: ohne Karenz NOK, mit 5-Tage-Karenz OK.
+    newest = b - 3 * DAY
+    assert data_reaches_oos_window(newest_ns=newest, start_ns=b, is_window_days=0)[0] is False
+    assert data_reaches_oos_window(newest_ns=newest, start_ns=b, is_window_days=0,
+                                   recency_grace_days=5.0)[0] is True
+
+
+# ---------------------------------------------------------------------------
+# #449 — Sweep-Preflight: unerreichbares Symbol wird übersprungen (fail-open ohne Telemetrie)
+# ---------------------------------------------------------------------------
+_CFG = {"walk_forward": {"is_window_days": 180, "oos_window_days": 45, "splits": 4,
+                         "holdout_days": 45},
+        "gate1_buffer_days": 30, "min_bars_per_param": 50, "min_oos_bars_per_fold": 200}
+
+
+def test_preflight_skips_unreachable_symbol(monkeypatch):
+    monkeypatch.setattr(sweep, "load_symbol_universe", lambda *a, **k: ["TSLA.ETORO", "AAPL.ETORO"])
+    monkeypatch.setattr(sweep, "n_params_for", lambda strat: 6)
+    b = _boundary()
+    big = {"TSLA.ETORO": 450 * 24, "AAPL.ETORO": 450 * 24}  # beide bestehen das Count-Gate
+    latest = {
+        "TSLA.ETORO": int(dt.datetime(2025, 9, 30, tzinfo=dt.timezone.utc).timestamp() * 1e9),  # H1-only
+        "AAPL.ETORO": int(dt.datetime(2026, 5, 8, tzinfo=dt.timezone.utc).timestamp() * 1e9),   # reicht ins OOS
+    }
+    pairs = sweep.enumerate_tunable_pairs(
+        ["SmaCrossoverStrategy"], ["TSLA.ETORO", "AAPL.ETORO"], tier="all",
+        available_bars=big, config=_CFG, latest_ts=latest, oos_window_start_ns=b)
+    kept = sorted({sym for _, sym, _ in pairs})
+    assert kept == ["AAPL.ETORO"]  # TSLA strukturell übersprungen
+
+
+def test_preflight_failopen_without_telemetry(monkeypatch):
+    """Ohne latest_ts/oos_window_start_ns ist das Verhalten bit-identisch zu vorher (beide behalten)."""
+    monkeypatch.setattr(sweep, "load_symbol_universe", lambda *a, **k: ["TSLA.ETORO", "AAPL.ETORO"])
+    monkeypatch.setattr(sweep, "n_params_for", lambda strat: 6)
+    big = {"TSLA.ETORO": 450 * 24, "AAPL.ETORO": 450 * 24}
+    pairs = sweep.enumerate_tunable_pairs(
+        ["SmaCrossoverStrategy"], ["TSLA.ETORO", "AAPL.ETORO"], tier="all",
+        available_bars=big, config=_CFG)
+    assert sorted({sym for _, sym, _ in pairs}) == ["AAPL.ETORO", "TSLA.ETORO"]
+
+
+# ---------------------------------------------------------------------------
+# #450 — Floor-Plateau stoppt die Study aktiv (opt-in)
+# ---------------------------------------------------------------------------
+class _FakeTrial:
+    def __init__(self, value, oos_evaluated=None):
+        self.value = value
+        self.state = optuna.trial.TrialState.COMPLETE
+        self.user_attrs = {} if oos_evaluated is None else {"oos_evaluated": oos_evaluated}
+
+
+class _FakeStudy:
+    def __init__(self, trials):
+        self.trials = trials
+        self._attrs = {}
+        self.stop_called = 0
+
+    @property
+    def user_attrs(self):
+        return dict(self._attrs)
+
+    def set_user_attr(self, k, v):
+        self._attrs[k] = v
+
+    def stop(self):
+        self.stop_called += 1
+
+
+_W = {"penalty_unevaluable_oos": -10.0, "unevaluable_shaping_span": 0.25, "n_startup_trials": 3}
+
+
+def test_plateau_stop_optin_calls_stop():
+    trials = [_FakeTrial(-9.85, oos_evaluated=False),
+              _FakeTrial(-9.90, oos_evaluated=False),
+              _FakeTrial(-9.93, oos_evaluated=False)]
+    study = _FakeStudy(trials)
+    ro.floor_plateau_callback(study, trials[-1], weights=_W, n_startup_trials=3,
+                              logger=logging.getLogger("t450a"), stop_on_plateau=True)
+    assert study.stop_called == 1
+
+
+def test_plateau_default_does_not_stop():
+    """Default (stop_on_plateau=False) bleibt reine Observability — Study läuft weiter."""
+    trials = [_FakeTrial(-9.85, oos_evaluated=False),
+              _FakeTrial(-9.90, oos_evaluated=False),
+              _FakeTrial(-9.93, oos_evaluated=False)]
+    study = _FakeStudy(trials)
+    ro.floor_plateau_callback(study, trials[-1], weights=_W, n_startup_trials=3,
+                              logger=logging.getLogger("t450b"))
+    assert study.stop_called == 0
+
+
+def test_plateau_no_stop_when_some_evaluable():
+    trials = [_FakeTrial(-9.85, oos_evaluated=False),
+              _FakeTrial(0.5, oos_evaluated=True),
+              _FakeTrial(-9.90, oos_evaluated=False)]
+    study = _FakeStudy(trials)
+    ro.floor_plateau_callback(study, trials[-1], weights=_W, n_startup_trials=3,
+                              logger=logging.getLogger("t450c"), stop_on_plateau=True)
+    assert study.stop_called == 0  # ein evaluable Trial ⇒ kein Plateau
