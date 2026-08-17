import json
import logging
import statistics
from dataclasses import dataclass
from pathlib import Path

@dataclass
class TournamentMetrics:
    oos_evaluated: bool
    oos_eligible: bool
    is_sortino_median: float
    oos_sortino: float | None
    oos_max_drawdown: float
    oos_total_trades: int
    win_count: int
    fully_eligible_pairs: int
    is_total_trades: int
    hit_trade_cap: bool = False
    # Issue #401: OOS-total_return als evaluable Reward-Fallback, wenn der Sortino
    # mathematisch undefiniert ist (Zero-Loss / Sub-Threshold). Default 0.0 haelt alle
    # bestehenden TournamentMetrics(**kw)-Konstruktionen rueckwaertskompatibel.
    oos_total_return: float = 0.0
    # Issue #966 (Katalog A, P0, Pitfall #305 in AGENTS.md) — VORHER float=0.0: eine FEHLENDE
    # expectancy (kein Key in oos_metrics.json) kollabierte auf denselben Wert wie eine ECHT
    # BEOBACHTETE Expectancy von 0.0 — ein Sentinel, der die Signatur eines Messwerts trägt, wird
    # von jedem nachgelagerten Konsumenten (Gate, Constraint-Distanz, TPE-Sampler) als Messwert
    # behandelt (dieselbe #759-Fehlerklasse wie oos_win_rate/oos_profit_factor, hier nachgezogen).
    oos_expectancy: float | None = None
    # Issue #452: OOS-Distanzmetriken fuer kontinuierliche Constraint-Penalties bei
    # evaluierten, aber nicht eligiblen Trials.
    # Issue #759 — VORHER float=0.0: eine FEHLENDE win_rate (kein Trial je evaluiert/kein
    # win_rate-Key im Metrics-Dict) kollabierte auf denselben Wert wie eine ECHT BEOBACHTETE
    # win_rate von 0.0. Nachgelagerte Policies (reward.check_any_arm_reachability_live/
    # resolve_any_arm_policy) konnten die beiden Faelle nicht mehr trennen und rekalibrierten
    # Schwellen aus einer Verteilung, die teils/ausschliesslich aus Missing-Data-Sentinels bestand
    # (empirisch: 305/459 [#660]-Warnungen mit p99=0.0000, davon 255 aus Studies mit 0 evaluierten
    # Trials). float | None = None laesst „nicht messbar" von „gemessen null" unterscheidbar.
    oos_win_rate: float | None = None
    oos_profit_factor: float | None = None
    # Issue #1004 (Katalog #858) — ``oos_profit_factor`` bleibt weiterhin gecappt (Zero-Regression
    # auf Gate/Reward); diese beiden Felder machen die Zensur explizit sichtbar statt sie stumm zu
    # verlieren (Pitfall #342). Defaults sind rueckwaertskompatibel (Legacy-JSONs ohne die Keys).
    oos_profit_factor_censored: bool = False
    oos_profit_factor_raw: float | None = None
    # Issue #1031 (Katalog #866) — additiv zu ``oos_expectancy`` (die weiterhin unveraendert
    # gecappte/-definierte Zahl, Zero-Regression): eine gegen Nennerausreisser robuste
    # kapitalgewichtete Variante plus Winsorisierungs-/Ausreisser-Telemetrie (siehe
    # ``backtest_runner._calculate_stats``-Docstring). Defaults rueckwaertskompatibel (Legacy-JSONs
    # ohne die Keys).
    oos_expectancy_capital_weighted: float | None = None
    oos_expectancy_winsorized: float | None = None
    oos_expectancy_outlier_count: int = 0
    oos_expectancy_notional_degenerate_count: int = 0
    # Issue #946/#1112 (Katalog #960) — Dust-Round-Trips (Notional < 5% des Median-Notionals),
    # jetzt AN DER QUELLE verworfen (``backtest_runner._filter_dust_round_trips``, VOR jeder
    # IS/OOS-Aufteilung), statt nur an der Expectancy-Konsumstelle (die vormalige ``oos_expectancy_
    # notional_degenerate_count`` oben, seit diesem Fix strukturell 0, weil Dust die
    # Expectancy-Berechnung nie mehr erreicht). Default rueckwaertskompatibel (Legacy-JSONs).
    oos_dust_round_trips_filtered_count: int = 0
    # Issue #1042 (Katalog #866) E-1/E-3 — Kosten-Stressband (additive Telemetrie, siehe
    # ``backtest_runner._expectancy_cost_stress``-Docstring) und CVaR/ES-Tail-Risiko (additive
    # Telemetrie, siehe ``_calculate_stats``-Berechnungsblock). Defaults rueckwaertskompatibel.
    oos_expectancy_cost_stress_1_5x: float | None = None
    oos_expectancy_cost_stress_2x: float | None = None
    oos_cvar_95: float | None = None
    oos_es_99: float | None = None
    # Issue #407: beste IS-Performance ueber alle full_results als kontinuierliches Gate-Naehe-
    # Signal fuer unevaluable Trials (_gate_proximity). Defaults 0.0 ⇒ rueckwaertskompatibel.
    is_best_total_return: float = 0.0
    is_best_win_rate: float = 0.0
    # Issue #416: tatsaechliches Daten-Zeitfenster des Backtests (aus dem optionalen ``data_window``-
    # Block der tournament_result.json), gehoben ins optimizer_trial_completed-Event fuer die
    # Per-Trial-Fehleranalyse. None, wenn der Block fehlt (rueckwaertskompatibel).
    data_window_start: str | None = None
    data_window_end: str | None = None
    data_window_days: float | None = None
    # Issue #444/#448 — beobachtete Fill-ts-Spanne (Epoch-ns) aus dem data_window-Block. Macht
    # einen OOS-Domänen-Defekt (Fills außerhalb von [start_ns, end_ns], Pitfall #80) ohne Ad-hoc-
    # Diagnose direkt sichtbar. None, wenn der Block (oder die Felder) fehlen (rückwärtskompatibel).
    fill_ts_min: int | None = None
    fill_ts_max: int | None = None
    # Issue #455 (Pitfall #82) — OOS-Abdeckungs-Telemetrie. Die früheste OOS-Sub-Fenster-Grenze
    # (``start_ns + is_window_ns``) und ob die realen Fills sie erreichen. ``oos_covered=False``
    # macht einen strukturellen OOS=0-Kollaps auf einen Blick DATENseitig statt parameterseitig
    # erkennbar (dünner/staler H2-Katalog). None, wenn der Block (oder die Felder) fehlen.
    oos_window_start_ns: int | None = None
    oos_covered: bool | None = None
    oos_coverage_gap_days: float | None = None
    # Issue #453 — granulare OOS-Eligibility-Ablehnungsgründe (z. B. "oos_max_drawdown: 0.5 > 0.3").
    # Aus dem single_symbol_oos/aggregate_winner-Block; leeres Tuple, wenn keiner vorliegt
    # (immutable Default ⇒ kein mutable-default-Footgun). Macht den OOS-Gate-Drop pro Trial konkret.
    oos_rejection_reasons: tuple = ()
    # Issue #463: Telemetry property for OOS Anchor Divergence invariant check.
    oos_anchor_divergence: bool | None = None
    # Issue #554 — maschinenlesbare Gate-Deltas (metric → actual − threshold). Leeres Dict, wenn der
    # Block fehlt (rückwärtskompatibel zu Pre-#554-JSONs). Für die forensische Near-Miss-Analyse.
    oos_gate_deltas: dict | None = None
    # Issue #565 — Per-Fold-OOS-Sortinos (forensisch seit #589; die Fold-Dispersion läuft nun über
    # die Returns). Leeres Tuple, wenn keine Fold-Telemetrie vorliegt (immutable Default).
    # Issue #665 — DEPRECATED für fold-übergreifende Aggregation/Vergleiche (annualisiert,
    # fold-spezifisch inkommensurabel); siehe backtest_runner.collect_oos_fold_sortinos-Docstring.
    # Nur noch forensische Anzeige-Telemetrie.
    oos_fold_sortinos: tuple = ()
    # Issue #665 — die kanonische, annualisierungs-invariante Parallelgrösse (per-Perioden
    # OOS-Sortino je Fold). JEDE fold-übergreifende Aggregation (PBO, Fold-Median-Diagnose) MUSS
    # diese Serie konsumieren, nicht ``oos_fold_sortinos``.
    oos_fold_sortino_periods: tuple = ()
    # Issue #589/#590 — Per-Fold-OOS-Returns (gut konditionierte Größe der Fold-Dispersions-Strafe)
    # und die Gesamtzahl der Walk-Forward-Folds (Normierung fehlender/degenerierter Folds im Reward).
    oos_fold_returns: tuple = ()
    oos_folds_total: int = 0
    # Issue #613 — der GEPOOLTE IS-Sortino aus der IS-Equity-Kurve (derselbe _calculate_stats-mtm-Pfad
    # wie oos_sortino), die KOHÄRENTE Grösse für die Divergenz-Strafe. is_sortino_median (Fold-/Symbol-
    # Median) bleibt rein forensische Telemetrie. Default None ⇒ rückwärtskompatibel: reward.py fällt
    # dann auf is_sortino_median zurück (Legacy-JSONs/Fixtures ohne median_is_sortino_pooled).
    is_sortino_pooled: float | None = None
    # Issue #613 — Aggregationsebene des IS-/OOS-Sortino (Kohärenz-Telemetrie). "pooled_equity_curve"
    # wenn aus der mtm-Equity-Kurve, sonst "trade_sequential"/None. Für die fail-loud Kohärenz-Assertion.
    is_sortino_aggregation_basis: str | None = None
    oos_sortino_aggregation_basis: str | None = None
    # Issue #614 / #630 — PSR + psr_z (Probabilistic Sharpe/Sortino Ratio).
    # psr (CDF) dient als statistisches Gate, psr_z (Effektstärke) als Ranking-Base.
    # None ⇒ undefiniert (zu wenig Trades / Guard).
    oos_psr: float | None = None
    oos_psr_z: float | None = None
    oos_sortino_period: float | None = None
    oos_sortino_annualized: float | None = None
    # Issue #980/#1134 (Katalog #986) — woher der F-Faktor hinter oos_sortino_annualized kam (siehe
    # backtest_runner._get_annualization_factor_with_source-Docstring). None ⇒ Legacy-JSON/kein
    # Equity-Kurven-Pfad (rueckwaertskompatibel).
    oos_annualization_factor_source: str | None = None
    oos_n_periods: int = 0
    # Issue #845 — der Downside-Beobachtungs-Nenner (backtest_runner._calculate_stats
    # "downside_obs", #823 SORTINO_INSUFFICIENT_DOWNSIDE-Schwelle), durchgereicht als eigenes Feld
    # statt einer stillen Re-Interpretation von oos_n_periods: n_periods misst die volle
    # informative Serie, downside_obs nur die tatsaechlich downside-tragende Teilmenge — beide
    # koennen um Groessenordnungen auseinanderfallen (die #845-Motivation: Faktor 45 innerhalb
    # einer Familie). None ⇒ vor Erreichen der Berechnung ausgestiegen (rückwärtskompatibel zu
    # Pre-#845-JSONs).
    oos_downside_obs: int | None = None
    oos_ret_skew: float = 0.0
    oos_ret_kurtosis: float = 3.0
    # Issue #620 — die #589-Kohärenz-Invariante (sign(oos_sortino)==sign(oos_total_return)) feuert im
    # Backtest-SUBPROZESS; ihr Flag ``oos_coherence_violation`` erreichte bisher weder TournamentMetrics
    # noch das JSON_EVENT. Jetzt durchgereicht ⇒ beobachtbar (Study-Zähler coherence_violations).
    oos_coherence_violation: bool = False
    # Issue #619 — die per-Perioden-OOS-Returns (gecappt) für den Stationary-Bootstrap-CI im Holdout-Gate.
    oos_period_returns: tuple = ()
    # Issue #552/#635 — Benchmark-relative Alpha-Telemetrie (oos_buyhold_return/oos_excess_return aus
    # backtest_runner._calculate_stats, #632 per-Fold-kompoundiert). Vorher NICHT nach TournamentMetrics
    # durchgereicht ⇒ reward._normalized_gate_distances/#612-Sampler-Constraint konnten das
    # oos_min_excess_return-Gate nie normiert sehen (die #635-PSR/Excess-Return-Erweiterung wäre sonst
    # strukturell inert geblieben). None, wenn keine Benchmark-Serie vorlag (rückwärtskompatibel).
    oos_buyhold_return: float | None = None
    oos_excess_return: float | None = None
    # Issue #850 — Anteil der Fenster-Zeit mit offener Position (backtest_runner._calculate_stats
    # "exposure_fraction"), damit ein Excess-Return gegen einen fallenden Benchmark von echtem
    # Alpha unterscheidbar wird (ein hoher Excess bei exposure_fraction nahe 0 ist ueberwiegend
    # vermiedener Kursverlust, keine Handelsleistung — siehe summary_de.py Abschnitt 2.3). None,
    # wenn keine Fenster-Spanne auswertbar war (rückwärtskompatibel zu Pre-#850-JSONs).
    oos_exposure_fraction: float | None = None
    # Issue #986/#1140 (Katalog #986, Pitfall #412 in AGENTS.md) — ``oos_excess_return`` allein
    # bestätigte im fallenden Markt jede Strategie mit ``exposure_fraction`` < 100 % unabhängig von
    # jedem Edge (0/39 auf steigenden, 62/65 auf fallenden Symbolen). ``oos_alpha``/``oos_beta``
    # sind die OLS-Regressionskoeffizienten der Strategie-Perioden-(Log-)Returns gegen die
    # Benchmark-Perioden-(Log-)Returns (aus ``backtest_runner.extract_metrics``, identisch
    # gefensterte/alignte Serien wie ``oos_buyhold_return`` — Issue #772 Index-Gleichheit), NICHT
    # annualisiert (dieselbe Periodengranularität wie ``oos_period_returns``). ``oos_alpha_tstat``
    # ist der t-Wert von α (Standard-OLS-Formel); ein Kandidat mit β ≈ 0,2 und α ≈ 0 ist ein
    # Teilzeit-Long, kein Edge. None, wenn keine Benchmark-Serie/zu wenige Perioden vorlagen
    # (rückwärtskompatibel).
    oos_alpha: float | None = None
    oos_beta: float | None = None
    oos_alpha_tstat: float | None = None
    # Issue #710 — Haltedauer-Metrik (Bars, NICHT Sekunden — alle Strategien laufen auf 1h-Bars).
    # Median (robuste Zentraltendenz gegen schiefe per-Fold-Verteilungen) + p95 (Deadline-Nähe).
    # Optional[float]=None ⇒ migrationssicher (Legacy-JSONs/Fixtures ohne das Feld laufen unveraendert
    # grün). Reine Telemetrie in dieser Stufe — der Reward-Term folgt in #711.
    oos_median_bars_held: float | None = None
    oos_p95_bars_held: float | None = None
    # Issue #832 Fix Punkt 1 (Katalog #828-#835, GitHub-Issue #751) — Haltedauer in SEKUNDEN
    # (Rohmaterial fuer summary_de.py Abschnitt 4 "Trades mit der laengsten Haltedauer"), direkt
    # aus backtest_runner._calculate_stats. None ⇒ migrationssicher (Legacy-JSONs ohne das Feld).
    oos_max_holding_time_s: float | None = None
    oos_p95_holding_time_s: float | None = None
    # Issue #562/#774 — Round-Trip-Kosten (bps, spread + commission) derselben Single Source of
    # Truth, die bereits das kostenrelative Expectancy-Gate speist (backtest_runner.
    # _evaluate_oos_eligibility). #774 konsumiert denselben Wert für die Turnover-Reward-Strafe
    # (asset-class-aufgelöst statt einer TSLA-kalibrierten globalen Konstante). None, wenn Spread/
    # Kommission nicht auflösbar waren (rückwärtskompatibel).
    round_trip_cost_bps: float | None = None
    # Issue #798 — True, sobald backtest_runner die Perioden-Renditeserie auf period_returns_cap
    # gekappt hat (n_periods > cap). False ⇒ rückwärtskompatibel zu Pre-#798-JSONs.
    period_returns_truncated: bool = False
    # Issue #801 — True, sobald die OOS-Equity-Kurve waehrend des Fensters nicht-positiv wurde
    # (backtest_runner.assert_positive_equity/EQUITY_NONPOSITIVE). Der Trial gilt dann als
    # oekonomisch ruiniert (sortino/psr sind None, total_return bleibt erhalten). False ⇒
    # rückwärtskompatibel zu Pre-#801-JSONs.
    oos_equity_ruined: bool = False
    # Issue #804 — die strukturierten Inferenzpfad-Diagnosen aus backtest_runner._calculate_stats
    # (EQUITY_NONPOSITIVE/PERIOD_RETURNS_NOT_FINITE/RETURN_SERIES_IDENTITY_*/
    # NON_CONTIGUOUS_FOLD_SEGMENTS/SORTINO_GUARD_TRIPPED/COHERENCE_INVARIANT_VIOLATION), gehoben aus
    # ``oos_metrics['inference_diagnostics']`` — Tupel von ``{'code','detail','value'}``-Dicts.
    # Leeres Tuple ⇒ keine Verletzung ODER Pre-#804-JSON (rückwärtskompatibel).
    inference_diagnostics: tuple = ()
    # Issue #899 — Exit-Telemetrie (aus Order-Tags, nicht aus dem abgeschnittenen Subprozess-
    # Logger). ``oos_exit_reason_histogram`` summiert exakt zu ``oos_total_trades``.
    # ``oos_max_holding_bars`` ist die PRIMÄRE Bar-Messgrösse (#899 Fix 2, #902); leeres Dict/None
    # ⇒ migrationssicher (Legacy-JSONs ohne die Felder).
    oos_exit_reason_histogram: dict | None = None
    oos_max_holding_bars: float | None = None
    oos_gross_loss_mean_bps: float | None = None
    # Issue #1035 (Katalog #866) — dieselbe Groesse, aber NUR ueber nachweisliche TRAILING_STOP-
    # Exits (siehe backtest_runner._aggregate_exit_telemetry-Docstring).
    oos_gross_loss_mean_bps_trailing_stop: float | None = None
    # Issue #972/#1126 (Katalog #986) — robuste Gegenstuecke zum ungeschuetzten Mittel oben (Median
    # und 5/95-winsorisiertes Mittel derselben Grundgesamtheit, siehe backtest_runner._aggregate_
    # exit_telemetry-Docstring, Pitfall #405 in AGENTS.md).
    oos_gross_loss_median_bps_trailing_stop: float | None = None
    oos_gross_loss_winsorized_mean_bps_trailing_stop: float | None = None
    oos_n_trailing_stop_losses: int = 0
    # Issue #972/#1126 — wie viele der in _filter_dust_round_trips verworfenen Round-Trips ein
    # nachweislicher TRAILING_STOP-Verlust-Exit waren (Teilnenner von oos_n_trailing_stop_losses).
    oos_n_trailing_stop_losses_dust_filtered: int = 0
    # Issue #972/#1126 — p05/p50/p95 des Round-Trip-Notionals dieses Trials (macht den bps-Nenner
    # von gross_loss_*_bps_trailing_stop auditierbar).
    oos_rt_notional_p05: float | None = None
    oos_rt_notional_p50: float | None = None
    oos_rt_notional_p95: float | None = None
    oos_gross_win_mean_bps: float | None = None
    oos_atr_median_bps: float | None = None
    oos_atr_min_bps: float | None = None
    # Issue #975/#1129 — der ROHE (ungefloorte) ATR-Median, Gegenstueck zu oos_atr_median_bps (dem
    # EFFEKTIVEN, ratschen-gefloorten Wert).
    oos_atr_raw_median_bps: float | None = None
    # Issue #976/#1130 — Absetzen-zu-Fill-Latenz (Bars) und Slippage (bps), NUR ueber nachweisliche
    # TRAILING_STOP-Exits mit vollstaendiger Order-/Fill-Telemetrie (siehe backtest_runner.
    # _aggregate_exit_telemetry-Docstring).
    oos_stop_exit_fill_lag_bars_median: float | None = None
    oos_stop_exit_slippage_bps_median: float | None = None
    oos_n_trailing_stop_exits_with_fill_lag_telemetry: int = 0
    # Issue #1095 (Katalog #928) — Median der Bars zwischen Trailing-Stop-Signal und tatsaechlichem
    # Markt-Close-Fill (siehe backtest_runner._aggregate_exit_telemetry-Docstring); None ohne einen
    # einzigen getaggten Stop-Exit dieses Trials.
    oos_stop_exit_lag_bars_median: float | None = None
    # Issue #953/#1119 (Katalog #960) — Median der je-Position-Bar-Spannen-Mediane (bps, siehe
    # backtest_runner._aggregate_exit_telemetry-Docstring); Referenzgroesse fuer
    # invariants.check_stop_loss_vs_bar_range (Verlust = adverse Bewegung EINER Bar, nicht
    # Stopdistanz + Ueberschiessen). None ohne eine einzige Position mit Bar-Spannen-Telemetrie.
    oos_bar_range_median_bps: float | None = None
    # Issue #1097 (Katalog #930) — Stichprobengroesse HINTER oos_gross_loss_mean_bps (ALLE
    # Verlust-Trades dieses Trials, nicht nur Stop-Exits); Grundlage fuer den trade-gewichteten
    # (statt medianbasierten) Study-Pool-Mittelwert, siehe report._pooled_mean_of_trial_field.
    oos_n_losses: int = 0
    # Issue #903 — rohe Round-Trip-Haltedauern (Sekunden), Eingangsgrösse für die ROUND-TRIP-Ebene
    # von invariants.compute_trial_timebox_violations. Leeres Tuple ⇒ Pre-#899-JSON (rückwärts-
    # kompatibel; der Konsument fällt dann auf den Trial-Maximum-Punkt zurück).
    oos_holding_times_s: tuple = ()

def parse_tournament(path: Path) -> TournamentMetrics:
    """Liest aggregate_winner/oos_metrics typsicher (None-safe).
       Issue #948/#1114 (Katalog #960) — Docstring-Korrektur: oos_sortino ist seit #549/#589 der
       GEPOOLTE ``oos_metrics.sortino_ratio`` (konkatenierte OOS-Equity-Kurve, kohaerent mit
       total_return), NICHT der Median von ``oos_fold_sortinos`` (der fruehere Wortlaut hier war
       seit #589 stale — ``oos_fold_sortinos`` bleibt reine Anzeige-Telemetrie, siehe Feld-
       Docstring oben). is_sortino_median = median_is_sortino bzw. median_sortino (ein separates
       Feld, keine Quelle von oos_sortino)."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    fully_eligible_pairs = data.get("fully_eligible_pairs") or 0
    agg = data.get("aggregate_winner") or {}
    psw = data.get("per_symbol_winners") or {}

    if isinstance(psw, dict) and len(psw) == 1:
        single_win = list(psw.values())[0]
        if single_win.get("oos_eligible") or not agg.get("oos_eligible"):
            agg = single_win
    elif not agg and data.get("single_symbol_oos"):
        agg = data["single_symbol_oos"]

    oos_evaluated = agg.get("oos_evaluated") or False
    oos_eligible = agg.get("oos_eligible") or False
    win_count = agg.get("win_count") or 0
    # Issue #453 — konkrete OOS-Ablehnungsgründe (None-safe ⇒ leeres Tuple).
    oos_rejection_reasons = tuple(agg.get("oos_rejection_reasons") or ())
    # Issue #554 — maschinenlesbare Gate-Deltas (None-safe ⇒ leeres Dict).
    oos_gate_deltas = dict(agg.get("oos_gate_deltas") or {})

    # is_sortino_median fallback logic
    is_sortino_median = agg.get("median_is_sortino")
    if is_sortino_median is None:
        is_sortino_median = agg.get("median_sortino", 0.0)

    # Issue #613 — der EXPLIZIT gepoolte IS-Sortino aus der IS-Equity-Kurve (derselbe mtm-Pfad wie
    # oos_sortino), die kohärente Divergenz-Grösse. None ⇒ reward.py fällt auf is_sortino_median zurück
    # (Legacy-JSONs/Fixtures ohne das Feld). Der Fold-/Symbol-Median bleibt rein forensisch.
    is_sortino_pooled = agg.get("median_is_sortino_pooled")
    is_sortino_aggregation_basis = agg.get("is_sortino_aggregation_basis")

    oos_fold_sortinos = agg.get("oos_fold_sortinos") or []
    # Issue #665 — annualisierungs-invariante Parallelgrösse (fällt None-safe auf oos_metrics
    # zurück, falls nur dort gestempelt — z. B. direkte apply_fold_aggregation-Aufrufer/Fixtures).
    oos_metrics = agg.get("oos_metrics") or {}
    oos_fold_sortino_periods = agg.get("oos_fold_sortino_periods")
    if not oos_fold_sortino_periods:
        oos_fold_sortino_periods = oos_metrics.get("oos_fold_sortino_periods") or []
    # Issue #613 — Aggregationsebene des OOS-Sortino (Kohärenz-Telemetrie).
    oos_sortino_aggregation_basis = oos_metrics.get("sortino_aggregation_basis") or agg.get("aggregation_basis")
    # Issue #589/#590 — Per-Fold-OOS-Returns + Gesamt-Fold-Zahl für die reward-seitige Fold-
    # Dispersion (pstdev(returns)) und die Normierung fehlender Folds. None-safe.
    oos_fold_returns = agg.get("oos_fold_returns")
    if oos_fold_returns is None:
        oos_fold_returns = oos_metrics.get("oos_fold_returns") or []
    oos_folds_total = agg.get("oos_folds_total")
    if oos_folds_total is None:
        oos_folds_total = oos_metrics.get("oos_folds_total")

    # Issue #549/#589 — Gate/Reward-Sortino-Parität. Seit #589 ist ``oos_metrics["sortino_ratio"]``
    # der GEPOOLTE OOS-Sortino aus der konkatenierten OOS-Equity-Kurve (kohärent mit total_return) —
    # NICHT mehr der Fold-Median. Gate (_evaluate_oos_eligibility) UND Reward lesen damit exakt
    # denselben kanonischen, gepoolten Sortino (kein divergierender Gradient an der Gate-Grenze; kein
    # Median, der einen katastrophalen Fold maskiert). Siehe AGENTS.md Pitfall #110/#113.
    oos_sortino = oos_metrics.get("sortino_ratio")
    if oos_sortino is None:
        oos_sortino = oos_metrics.get("sortino_ratio_pooled")
    if oos_sortino is None and isinstance(agg.get("metrics"), dict):
        oos_sortino = agg["metrics"].get("sortino_ratio")
    # Issue #614 / #630 — PSR + psr_z + per-Perioden-/annualisierter Sortino + T + Momente (None-safe).
    oos_psr = oos_metrics.get("psr")
    if oos_psr is None and isinstance(agg.get("metrics"), dict):
        oos_psr = agg["metrics"].get("psr")
    oos_psr_z = oos_metrics.get("psr_z")
    oos_sortino_period = oos_metrics.get("sortino_period")
    oos_sortino_annualized = oos_metrics.get("sortino_annualized")
    # Issue #980/#1134 — siehe TournamentMetrics-Docstring.
    oos_annualization_factor_source = oos_metrics.get("annualization_factor_source")
    oos_n_periods = oos_metrics.get("n_periods")
    # Issue #845 — Downside-Beobachtungs-Nenner (None-safe ⇒ rückwärtskompatibel zu Pre-#845-JSONs).
    oos_downside_obs = oos_metrics.get("downside_obs")
    oos_ret_skew = oos_metrics.get("ret_skew")
    oos_ret_kurtosis = oos_metrics.get("ret_kurtosis")
    # Issue #620 — Kohärenz-Verletzungs-Flag aus dem Subprozess (None-safe ⇒ False).
    oos_coherence_violation = bool(oos_metrics.get("oos_coherence_violation") or False)
    # Issue #619 — per-Perioden-OOS-Returns (None-safe ⇒ leeres Tuple).
    oos_period_returns = tuple(oos_metrics.get("period_returns") or ())
    # Issue #552/#635 — Benchmark-relative Alpha-Telemetrie (None-safe; fehlt ohne Benchmark-Serie).
    oos_buyhold_return = oos_metrics.get("oos_buyhold_return")
    oos_excess_return = oos_metrics.get("oos_excess_return")
    # Issue #986/#1140 — α/β-Regressionskoeffizienten + t(α) (None-safe; fehlen ohne Benchmark-
    # Serie/zu wenige Perioden, siehe TournamentMetrics-Docstring).
    oos_alpha = oos_metrics.get("oos_alpha")
    oos_beta = oos_metrics.get("oos_beta")
    oos_alpha_tstat = oos_metrics.get("oos_alpha_tstat")
    # Issue #850 — Exposure-Telemetrie (None-safe ⇒ rückwärtskompatibel zu Pre-#850-JSONs).
    oos_exposure_fraction = oos_metrics.get("exposure_fraction")
    # Issue #710 — Haltedauer-Metrik (Bars, None-safe ⇒ rückwärtskompatibel zu Pre-#710-JSONs).
    oos_median_bars_held = oos_metrics.get("median_bars_held")
    oos_p95_bars_held = oos_metrics.get("p95_bars_held")
    # Issue #832 — Haltedauer in Sekunden (None-safe ⇒ rückwärtskompatibel zu Pre-#832-JSONs).
    oos_max_holding_time_s = oos_metrics.get("max_holding_time_s")
    oos_p95_holding_time_s = oos_metrics.get("p95_holding_time_s")

    # Issue #774 — dieselbe Round-Trip-Kosten-Telemetrie, die bereits das Expectancy-Gate speist
    # (backtest_runner._evaluate_oos_eligibility, #562/#684). None-safe.
    round_trip_cost_bps = oos_metrics.get("round_trip_cost_bps")
    # Issue #798 — Truncation-Telemetrie fuer die gekappte Perioden-Renditeserie (None-safe ⇒ False,
    # rückwärtskompatibel zu Pre-#798-JSONs, die das Feld noch nicht schreiben).
    period_returns_truncated = bool(oos_metrics.get("period_returns_truncated") or False)
    # Issue #801 — Ruin-Telemetrie (None-safe ⇒ False, rückwärtskompatibel zu Pre-#801-JSONs).
    oos_equity_ruined = bool(oos_metrics.get("equity_ruined") or False)
    # Issue #804 — strukturierte Inferenzpfad-Diagnosen (None-safe ⇒ leeres Tuple).
    inference_diagnostics = tuple(oos_metrics.get("inference_diagnostics") or ())
    # Issue #899 — Exit-Telemetrie (None-safe ⇒ rückwärtskompatibel zu Pre-#899-JSONs).
    oos_exit_reason_histogram = oos_metrics.get("exit_reason_histogram")
    oos_max_holding_bars = oos_metrics.get("max_holding_bars")
    oos_gross_loss_mean_bps = oos_metrics.get("gross_loss_mean_bps")
    # Issue #1035 (Katalog #866) — siehe TournamentMetrics-Docstring.
    oos_gross_loss_mean_bps_trailing_stop = oos_metrics.get("gross_loss_mean_bps_trailing_stop")
    # Issue #972/#1126 — siehe TournamentMetrics-Docstring.
    oos_gross_loss_median_bps_trailing_stop = oos_metrics.get("gross_loss_median_bps_trailing_stop")
    oos_gross_loss_winsorized_mean_bps_trailing_stop = oos_metrics.get(
        "gross_loss_winsorized_mean_bps_trailing_stop")
    oos_n_trailing_stop_losses = oos_metrics.get("n_trailing_stop_losses")
    oos_n_trailing_stop_losses_dust_filtered = oos_metrics.get("n_trailing_stop_losses_dust_filtered")
    oos_rt_notional_p05 = oos_metrics.get("rt_notional_p05")
    oos_rt_notional_p50 = oos_metrics.get("rt_notional_p50")
    oos_rt_notional_p95 = oos_metrics.get("rt_notional_p95")
    oos_gross_win_mean_bps = oos_metrics.get("gross_win_mean_bps")
    oos_atr_median_bps = oos_metrics.get("atr_median_bps")
    oos_atr_min_bps = oos_metrics.get("atr_min_bps")
    # Issue #975/#1129 — siehe TournamentMetrics-Docstring.
    oos_atr_raw_median_bps = oos_metrics.get("atr_raw_median_bps")
    # Issue #976/#1130 — siehe TournamentMetrics-Docstring.
    oos_stop_exit_fill_lag_bars_median = oos_metrics.get("stop_exit_fill_lag_bars_median")
    oos_stop_exit_slippage_bps_median = oos_metrics.get("stop_exit_slippage_bps_median")
    oos_n_trailing_stop_exits_with_fill_lag_telemetry = oos_metrics.get(
        "n_trailing_stop_exits_with_fill_lag_telemetry")
    # Issue #1095 (Katalog #928) — siehe TournamentMetrics-Docstring.
    oos_stop_exit_lag_bars_median = oos_metrics.get("stop_exit_lag_bars_median")
    # Issue #953/#1119 (Katalog #960) — siehe TournamentMetrics-Docstring.
    oos_bar_range_median_bps = oos_metrics.get("bar_range_median_bps")
    # Issue #1097 (Katalog #930) — siehe TournamentMetrics-Docstring.
    oos_n_losses = oos_metrics.get("n_losses")
    oos_holding_times_s = oos_metrics.get("holding_times_s")

    oos_max_drawdown = oos_metrics.get("max_drawdown") or 0.0
    oos_total_trades = oos_metrics.get("total_trades") or 0
    # Issue #401: evaluable Reward-Fallback fuer Zero-Loss/Sub-Threshold-Sortino.
    oos_total_return = oos_metrics.get("total_return")
    oos_expectancy = oos_metrics.get("expectancy")
    # Issue #1031 (Katalog #866) — siehe TournamentMetrics-Docstring.
    oos_expectancy_capital_weighted = oos_metrics.get("expectancy_capital_weighted")
    oos_expectancy_winsorized = oos_metrics.get("expectancy_winsorized")
    oos_expectancy_outlier_count = oos_metrics.get("expectancy_outlier_count")
    oos_expectancy_notional_degenerate_count = oos_metrics.get("expectancy_notional_degenerate_count")
    # Issue #946/#1112 (Katalog #960) — siehe TournamentMetrics-Docstring.
    oos_dust_round_trips_filtered_count = oos_metrics.get("dust_round_trips_filtered_count")
    # Issue #1042 (Katalog #866) E-1/E-3 — siehe TournamentMetrics-Docstring. Issue #1081 (Katalog
    # #866-2) — bevorzugt den umbenannten, kosten-VOLLSTAENDIGEN Schluessel
    # (expectancy_round_trip_cost_stress_*, backtest_runner._expectancy_cost_stress); der alte Name
    # bleibt als Fallback fuer Legacy-Reports, traegt seit #1081 aber denselben korrigierten Wert.
    oos_expectancy_cost_stress_1_5x = (
        oos_metrics.get("expectancy_round_trip_cost_stress_1_5x")
        if oos_metrics.get("expectancy_round_trip_cost_stress_1_5x") is not None
        else oos_metrics.get("expectancy_cost_stress_1_5x"))
    oos_expectancy_cost_stress_2x = (
        oos_metrics.get("expectancy_round_trip_cost_stress_2x")
        if oos_metrics.get("expectancy_round_trip_cost_stress_2x") is not None
        else oos_metrics.get("expectancy_cost_stress_2x"))
    oos_cvar_95 = oos_metrics.get("cvar_95")
    oos_es_99 = oos_metrics.get("es_99")
    # Issue #452: OOS-Win-Rate / Profit-Factor fuer die kontinuierliche Constraint-Distanz.
    oos_win_rate = oos_metrics.get("win_rate")
    oos_profit_factor = oos_metrics.get("profit_factor")
    # Issue #1004 — Zensur-Telemetrie neben dem (weiterhin gecappten) Punktschätzer.
    oos_profit_factor_censored = bool(oos_metrics.get("profit_factor_censored") or False)
    oos_profit_factor_raw = oos_metrics.get("profit_factor_raw")

    is_total_trades = 0
    hit_trade_cap = False
    is_best_total_return = 0.0
    is_best_win_rate = 0.0
    full_results = data.get("full_results") or []
    if full_results and isinstance(full_results, list):
        trades_list = [r.get("metrics", {}).get("total_trades", 0) for r in full_results if isinstance(r, dict)]
        is_total_trades = sum(trades_list)

        strat_params = full_results[0].get("strat_params", {}) if len(full_results) > 0 and isinstance(full_results[0], dict) else {}
        max_trades_cap = strat_params.get("max_trades_cap")

        hit_trade_cap = False
        if max_trades_cap is not None:
            # Check if any run hit the cap
            if any(t >= max_trades_cap for t in trades_list):
                hit_trade_cap = True
        # Issue #407: beste IS-Rendite/-Trefferquote als Gate-Naehe-Signal (None-safe; negative
        # Rendite traegt 0 bei, da der Reward sie spaeter ohnehin auf [0,1] clippt).
        returns_list = [(r.get("metrics") or {}).get("total_return") or 0.0
                        for r in full_results if isinstance(r, dict)]
        winrates_list = [(r.get("metrics") or {}).get("win_rate") or 0.0
                         for r in full_results if isinstance(r, dict)]
        is_best_total_return = max(returns_list) if returns_list else 0.0
        is_best_win_rate = max(winrates_list) if winrates_list else 0.0

    # Issue #416 — optionales Daten-Zeitfenster (None-safe; fehlt der Block ⇒ alle Felder None).
    dw = data.get("data_window") or {}
    dw_start = dw.get("start")
    dw_end = dw.get("end")
    dw_days = dw.get("days")
    dw_fill_min = dw.get("fill_ts_min")
    dw_fill_max = dw.get("fill_ts_max")
    # Issue #455 — OOS-Abdeckungs-Felder (None-safe, rückwärtskompatibel zu Pre-#455-JSONs).
    dw_oos_start = dw.get("oos_window_start_ns")
    dw_oos_covered = dw.get("oos_covered")
    dw_oos_gap = dw.get("oos_coverage_gap_days")

    # Issue #463: Invariant Assertion for OOS Anchor Divergence
    oos_anchor_divergence = False
    _oos_covered_bool = bool(dw_oos_covered) if dw_oos_covered is not None else False

    _oos_total_trades = oos_metrics.get("total_trades") or 0
    if _oos_covered_bool and dw_fill_max is not None and dw_oos_start is not None and dw_fill_max >= dw_oos_start and _oos_total_trades == 0:
        logging.getLogger("optimizer").warning("OOS Anchor Divergence detected: fill_ts_max in OOS union but 0 OOS trades.")
        oos_anchor_divergence = True

    metrics = TournamentMetrics(
        oos_evaluated=bool(oos_evaluated),
        oos_eligible=bool(oos_eligible),
        is_sortino_median=float(is_sortino_median) if is_sortino_median is not None else 0.0,
        oos_sortino=float(oos_sortino) if oos_sortino is not None else None,
        oos_max_drawdown=float(oos_max_drawdown) if oos_max_drawdown is not None else 0.0,
        oos_total_trades=int(oos_total_trades) if oos_total_trades is not None else 0,
        win_count=int(win_count) if win_count is not None else 0,
        fully_eligible_pairs=int(fully_eligible_pairs) if fully_eligible_pairs is not None else 0,
        is_total_trades=int(is_total_trades),
        hit_trade_cap=bool(hit_trade_cap),
        oos_total_return=float(oos_total_return) if oos_total_return is not None else 0.0,
        # Issue #966 (Katalog A, P0) — None durchreichen statt auf 0.0 zu kollabieren, analog #759
        # fuer oos_win_rate/oos_profit_factor (siehe Dataclass-Feld-Kommentar). Root-Cause: ein
        # fehlender ``expectancy``-Key in oos_metrics.json wurde bislang zu einer Zahl, die die
        # Signatur eines Messwerts trug ("0.0 Erwartungswert bei 122 Trades und PF 2.44" — bit-
        # genau, arithmetisch unmoeglich) und von JEDEM nachgelagerten Konsumenten als echte
        # Beobachtung behandelt wurde (Pitfall #305 in AGENTS.md).
        oos_expectancy=float(oos_expectancy) if oos_expectancy is not None else None,
        # Issue #1031 (Katalog #866) — siehe TournamentMetrics-Docstring.
        oos_expectancy_capital_weighted=(
            float(oos_expectancy_capital_weighted)
            if oos_expectancy_capital_weighted is not None else None),
        oos_expectancy_winsorized=(
            float(oos_expectancy_winsorized) if oos_expectancy_winsorized is not None else None),
        oos_expectancy_outlier_count=(
            int(oos_expectancy_outlier_count) if oos_expectancy_outlier_count is not None else 0),
        oos_expectancy_notional_degenerate_count=(
            int(oos_expectancy_notional_degenerate_count)
            if oos_expectancy_notional_degenerate_count is not None else 0),
        # Issue #946/#1112 (Katalog #960) — siehe TournamentMetrics-Docstring.
        oos_dust_round_trips_filtered_count=(
            int(oos_dust_round_trips_filtered_count)
            if oos_dust_round_trips_filtered_count is not None else 0),
        # Issue #1042 (Katalog #866) E-1/E-3 — siehe TournamentMetrics-Docstring.
        oos_expectancy_cost_stress_1_5x=(
            float(oos_expectancy_cost_stress_1_5x)
            if oos_expectancy_cost_stress_1_5x is not None else None),
        oos_expectancy_cost_stress_2x=(
            float(oos_expectancy_cost_stress_2x)
            if oos_expectancy_cost_stress_2x is not None else None),
        oos_cvar_95=float(oos_cvar_95) if oos_cvar_95 is not None else None,
        oos_es_99=float(oos_es_99) if oos_es_99 is not None else None,
        # Issue #759 — None durchreichen statt auf 0.0 zu kollabieren (siehe Dataclass-Feld-Kommentar).
        oos_win_rate=float(oos_win_rate) if oos_win_rate is not None else None,
        oos_profit_factor=float(oos_profit_factor) if oos_profit_factor is not None else None,
        oos_profit_factor_censored=oos_profit_factor_censored,
        oos_profit_factor_raw=(
            float(oos_profit_factor_raw) if oos_profit_factor_raw is not None else None),
        is_best_total_return=float(is_best_total_return),
        is_best_win_rate=float(is_best_win_rate),
        data_window_start=str(dw_start) if dw_start is not None else None,
        data_window_end=str(dw_end) if dw_end is not None else None,
        data_window_days=float(dw_days) if dw_days is not None else None,
        fill_ts_min=int(dw_fill_min) if dw_fill_min is not None else None,
        fill_ts_max=int(dw_fill_max) if dw_fill_max is not None else None,
        oos_window_start_ns=int(dw_oos_start) if dw_oos_start is not None else None,
        oos_covered=bool(dw_oos_covered) if dw_oos_covered is not None else None,
        oos_coverage_gap_days=float(dw_oos_gap) if dw_oos_gap is not None else None,
        oos_rejection_reasons=oos_rejection_reasons,
        oos_anchor_divergence=oos_anchor_divergence,
        oos_gate_deltas=oos_gate_deltas,
        # Issue #565 — Per-Fold-OOS-Sortinos (None-safe ⇒ leeres Tuple); forensisch seit #589,
        # DEPRECATED für Aggregation seit #665 (annualisiert, fold-spezifisch inkommensurabel).
        oos_fold_sortinos=tuple(oos_fold_sortinos) if oos_fold_sortinos else (),
        # Issue #665 — kanonische, annualisierungs-invariante Parallelgrösse für Aggregation.
        oos_fold_sortino_periods=tuple(oos_fold_sortino_periods) if oos_fold_sortino_periods else (),
        # Issue #589/#590 — Per-Fold-OOS-Returns + Gesamt-Fold-Zahl (None-safe).
        oos_fold_returns=tuple(oos_fold_returns) if oos_fold_returns else (),
        oos_folds_total=int(oos_folds_total) if oos_folds_total is not None else 0,
        # Issue #613 — gepoolter IS-Sortino + Aggregations-Basen (Kohärenz).
        is_sortino_pooled=float(is_sortino_pooled) if is_sortino_pooled is not None else None,
        is_sortino_aggregation_basis=str(is_sortino_aggregation_basis) if is_sortino_aggregation_basis is not None else None,
        oos_sortino_aggregation_basis=str(oos_sortino_aggregation_basis) if oos_sortino_aggregation_basis is not None else None,
        # Issue #614 / #630 — PSR + psr_z + Sortino-Zerlegung (per-Periode/annualisiert) + T + Momente.
        oos_psr=float(oos_psr) if oos_psr is not None else None,
        oos_psr_z=float(oos_psr_z) if oos_psr_z is not None else None,
        oos_sortino_period=float(oos_sortino_period) if oos_sortino_period is not None else None,
        oos_sortino_annualized=float(oos_sortino_annualized) if oos_sortino_annualized is not None else None,
        # Issue #980/#1134 — siehe TournamentMetrics-Docstring.
        oos_annualization_factor_source=(
            str(oos_annualization_factor_source)
            if oos_annualization_factor_source is not None else None),
        oos_n_periods=int(oos_n_periods) if oos_n_periods is not None else 0,
        oos_downside_obs=int(oos_downside_obs) if oos_downside_obs is not None else None,
        oos_ret_skew=float(oos_ret_skew) if oos_ret_skew is not None else 0.0,
        oos_ret_kurtosis=float(oos_ret_kurtosis) if oos_ret_kurtosis is not None else 3.0,
        oos_coherence_violation=oos_coherence_violation,
        oos_period_returns=oos_period_returns,
        # Issue #552/#635 — Benchmark-relative Alpha-Telemetrie (None-safe).
        oos_buyhold_return=float(oos_buyhold_return) if oos_buyhold_return is not None else None,
        oos_excess_return=float(oos_excess_return) if oos_excess_return is not None else None,
        # Issue #986/#1140 — α/β-Regressionskoeffizienten + t(α) (None-safe).
        oos_alpha=float(oos_alpha) if oos_alpha is not None else None,
        oos_beta=float(oos_beta) if oos_beta is not None else None,
        oos_alpha_tstat=float(oos_alpha_tstat) if oos_alpha_tstat is not None else None,
        oos_exposure_fraction=float(oos_exposure_fraction) if oos_exposure_fraction is not None else None,
        # Issue #710 — Haltedauer-Metrik (Bars, None-safe).
        oos_median_bars_held=float(oos_median_bars_held) if oos_median_bars_held is not None else None,
        oos_p95_bars_held=float(oos_p95_bars_held) if oos_p95_bars_held is not None else None,
        oos_max_holding_time_s=float(oos_max_holding_time_s) if oos_max_holding_time_s is not None else None,
        oos_p95_holding_time_s=float(oos_p95_holding_time_s) if oos_p95_holding_time_s is not None else None,
        # Issue #774 — Round-Trip-Kosten (bps), None-safe.
        round_trip_cost_bps=float(round_trip_cost_bps) if round_trip_cost_bps is not None else None,
        period_returns_truncated=period_returns_truncated,
        oos_equity_ruined=oos_equity_ruined,
        inference_diagnostics=inference_diagnostics,
        # Issue #899 — Exit-Telemetrie (None-safe).
        oos_exit_reason_histogram=dict(oos_exit_reason_histogram) if oos_exit_reason_histogram else None,
        oos_max_holding_bars=float(oos_max_holding_bars) if oos_max_holding_bars is not None else None,
        oos_gross_loss_mean_bps=float(oos_gross_loss_mean_bps) if oos_gross_loss_mean_bps is not None else None,
        # Issue #1035 (Katalog #866) — siehe TournamentMetrics-Docstring.
        oos_gross_loss_mean_bps_trailing_stop=(
            float(oos_gross_loss_mean_bps_trailing_stop)
            if oos_gross_loss_mean_bps_trailing_stop is not None else None),
        oos_n_trailing_stop_losses=(
            int(oos_n_trailing_stop_losses) if oos_n_trailing_stop_losses is not None else 0),
        # Issue #972/#1126 — siehe TournamentMetrics-Docstring.
        oos_gross_loss_median_bps_trailing_stop=(
            float(oos_gross_loss_median_bps_trailing_stop)
            if oos_gross_loss_median_bps_trailing_stop is not None else None),
        oos_gross_loss_winsorized_mean_bps_trailing_stop=(
            float(oos_gross_loss_winsorized_mean_bps_trailing_stop)
            if oos_gross_loss_winsorized_mean_bps_trailing_stop is not None else None),
        oos_n_trailing_stop_losses_dust_filtered=(
            int(oos_n_trailing_stop_losses_dust_filtered)
            if oos_n_trailing_stop_losses_dust_filtered is not None else 0),
        oos_rt_notional_p05=float(oos_rt_notional_p05) if oos_rt_notional_p05 is not None else None,
        oos_rt_notional_p50=float(oos_rt_notional_p50) if oos_rt_notional_p50 is not None else None,
        oos_rt_notional_p95=float(oos_rt_notional_p95) if oos_rt_notional_p95 is not None else None,
        oos_gross_win_mean_bps=float(oos_gross_win_mean_bps) if oos_gross_win_mean_bps is not None else None,
        oos_atr_median_bps=float(oos_atr_median_bps) if oos_atr_median_bps is not None else None,
        oos_atr_min_bps=float(oos_atr_min_bps) if oos_atr_min_bps is not None else None,
        # Issue #975/#1129 — siehe TournamentMetrics-Docstring.
        oos_atr_raw_median_bps=(
            float(oos_atr_raw_median_bps) if oos_atr_raw_median_bps is not None else None),
        # Issue #976/#1130 — siehe TournamentMetrics-Docstring.
        oos_stop_exit_fill_lag_bars_median=(
            float(oos_stop_exit_fill_lag_bars_median)
            if oos_stop_exit_fill_lag_bars_median is not None else None),
        oos_stop_exit_slippage_bps_median=(
            float(oos_stop_exit_slippage_bps_median)
            if oos_stop_exit_slippage_bps_median is not None else None),
        oos_n_trailing_stop_exits_with_fill_lag_telemetry=(
            int(oos_n_trailing_stop_exits_with_fill_lag_telemetry)
            if oos_n_trailing_stop_exits_with_fill_lag_telemetry is not None else 0),
        # Issue #1095 (Katalog #928) — siehe TournamentMetrics-Docstring.
        oos_stop_exit_lag_bars_median=(
            float(oos_stop_exit_lag_bars_median)
            if oos_stop_exit_lag_bars_median is not None else None),
        # Issue #953/#1119 (Katalog #960) — siehe TournamentMetrics-Docstring.
        oos_bar_range_median_bps=(
            float(oos_bar_range_median_bps) if oos_bar_range_median_bps is not None else None),
        # Issue #1097 (Katalog #930) — siehe TournamentMetrics-Docstring.
        oos_n_losses=int(oos_n_losses) if oos_n_losses is not None else 0,
        oos_holding_times_s=tuple(oos_holding_times_s) if oos_holding_times_s else (),
    )

    # Issue #798 — die period_returns-Serie wird von KEINEM Konsumenten mehr von der Platte gelesen,
    # nachdem sie hier in metrics.oos_period_returns gehoben wurde (confirm._study_pbo/CSCV und der
    # Holdout-Bootstrap-CI lesen aus trial.user_attrs, nicht aus tournament_result.json). Strippen +
    # Zurueckschreiben macht ein erhaltenes tournament_result.json (der aktuell beste Trial, #794)
    # schlanker, unabhaengig davon, ob dieser konkrete Trial am Ende erhalten bleibt (best-effort,
    # ein Schreibfehler darf das bereits vollstaendige Parsing-Ergebnis nicht ungueltig machen).
    if oos_metrics.pop("period_returns", None) is not None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            logging.getLogger("optimizer").debug(
                "[#798] Konnte gekürztes tournament_result.json %s nicht zurückschreiben "
                "(non-fatal, Parsing-Ergebnis bleibt gültig).", path,
            )

    # Pre-Return Invarianten-Check
    if data.get("universe_size") == 1:
        full_results = data.get("full_results", [])
        has_oos_trades = any((r.get("oos_metrics") or {}).get("total_trades", 0) > 0 for r in full_results)
        if has_oos_trades and metrics.oos_total_trades <= 0:
            raise ValueError("Invariante gebrochen: Worker-OOS-Metriken existent, aber parse_tournament liefert oos_total_trades <= 0 (Issue #487).")

    return metrics
