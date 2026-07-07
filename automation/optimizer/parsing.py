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
    oos_expectancy: float = 0.0
    # Issue #452: OOS-Distanzmetriken fuer kontinuierliche Constraint-Penalties bei
    # evaluierten, aber nicht eligiblen Trials. Defaults halten bestehende Tests/Fixtures stabil.
    oos_win_rate: float = 0.0
    oos_profit_factor: float | None = None
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
    # Issue #565 — Per-Fold-OOS-Sortinos für die reward-seitige Fold-Dispersions-Strafe (pstdev).
    # Leeres Tuple, wenn keine Fold-Telemetrie vorliegt (immutable Default ⇒ kein mutable-default-
    # Footgun; rückwärtskompatibel). Belohnt konsistente statt glücklicher Per-Fold-Performance.
    oos_fold_sortinos: tuple = ()

def parse_tournament(path: Path) -> TournamentMetrics:
    """Liest aggregate_winner/oos_metrics typsicher (None-safe).
       oos_sortino = Median von aggregate_winner.oos_fold_sortinos, falls vorhanden,
       sonst oos_metrics.sortino_ratio. is_sortino_median = median_is_sortino bzw. median_sortino."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    fully_eligible_pairs = data.get("fully_eligible_pairs") or 0
    agg = data.get("aggregate_winner") or {}

    # Issue #405 — Per-Symbol-Evaluierbarkeit vom Gewinner-Status entkoppeln (Pitfall #75,
    # Defekt 1). Im Single-Symbol-Sweep bleibt `aggregate_winner` null, solange das Symbol das
    # volle Tournament-Gate (IS-eligible ∧ OOS-eligible) fuer KEINE Parametrisierung klaert —
    # die Per-Symbol-OOS-Resultate existieren aber. Fehlt der Aggregat-Gewinner, der
    # `single_symbol_oos`-Block (von write_tournament_json geschrieben) aber vorhanden, leite die
    # OOS-Metriken daraus ab. Rein additiv: bei vorhandenem aggregate_winner (Praezedenz) oder in
    # Multi-Symbol-Laeufen (kein Block) ist dieser Pfad inaktiv ⇒ bit-identisch.
    if not agg and data.get("single_symbol_oos"):
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

    oos_fold_sortinos = agg.get("oos_fold_sortinos") or []
    oos_metrics = agg.get("oos_metrics") or {}

    # Issue #549 — Gate/Reward-Sortino-Parität. Seit #549 setzt extract_metrics (apply_fold_aggregation)
    # oos_metrics["sortino_ratio"] bereits AN DER QUELLE auf den Fold-Median der oos_fold_sortinos.
    # Beide Zweige hier liefern damit denselben kanonischen Wert (median(oos_fold_sortinos) im WF-Pfad,
    # der gepoolte Fallback nur, wenn keine Fold-Sortinos existieren) — der Reward liest exakt den
    # Sortino, den das Gate (_evaluate_oos_eligibility) verwendet. Kein divergierender Gradient mehr
    # an der Gate-Grenze (siehe AGENTS.md Pitfall #110).
    if oos_fold_sortinos and isinstance(oos_fold_sortinos, list) and len(oos_fold_sortinos) > 0:
        oos_sortino = statistics.median(oos_fold_sortinos)
    else:
        oos_sortino = oos_metrics.get("sortino_ratio")

    oos_max_drawdown = oos_metrics.get("max_drawdown") or 0.0
    oos_total_trades = oos_metrics.get("total_trades") or 0
    # Issue #401: evaluable Reward-Fallback fuer Zero-Loss/Sub-Threshold-Sortino.
    oos_total_return = oos_metrics.get("total_return")
    oos_expectancy = oos_metrics.get("expectancy")
    # Issue #452: OOS-Win-Rate / Profit-Factor fuer die kontinuierliche Constraint-Distanz.
    oos_win_rate = oos_metrics.get("win_rate")
    oos_profit_factor = oos_metrics.get("profit_factor")

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
        oos_expectancy=float(oos_expectancy) if oos_expectancy is not None else 0.0,
        oos_win_rate=float(oos_win_rate) if oos_win_rate is not None else 0.0,
        oos_profit_factor=float(oos_profit_factor) if oos_profit_factor is not None else None,
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
        # Issue #565 — Per-Fold-OOS-Sortinos (None-safe ⇒ leeres Tuple) für die Fold-Dispersion.
        oos_fold_sortinos=tuple(oos_fold_sortinos) if oos_fold_sortinos else (),
    )

    # Pre-Return Invarianten-Check
    if data.get("universe_size") == 1:
        full_results = data.get("full_results", [])
        has_oos_trades = any((r.get("oos_metrics") or {}).get("total_trades", 0) > 0 for r in full_results)
        if has_oos_trades and metrics.oos_total_trades <= 0:
            raise ValueError("Invariante gebrochen: Worker-OOS-Metriken existent, aber parse_tournament liefert oos_total_trades <= 0 (Issue #487).")

    return metrics
