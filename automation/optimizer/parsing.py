import json
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
    is_max_trades: int
    # Issue #401: OOS-total_return als evaluable Reward-Fallback, wenn der Sortino
    # mathematisch undefiniert ist (Zero-Loss / Sub-Threshold). Default 0.0 haelt alle
    # bestehenden TournamentMetrics(**kw)-Konstruktionen rueckwaertskompatibel.
    oos_total_return: float = 0.0
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

    # is_sortino_median fallback logic
    is_sortino_median = agg.get("median_is_sortino")
    if is_sortino_median is None:
        is_sortino_median = agg.get("median_sortino", 0.0)

    oos_fold_sortinos = agg.get("oos_fold_sortinos") or []
    oos_metrics = agg.get("oos_metrics") or {}

    if oos_fold_sortinos and isinstance(oos_fold_sortinos, list) and len(oos_fold_sortinos) > 0:
        oos_sortino = statistics.median(oos_fold_sortinos)
    else:
        oos_sortino = oos_metrics.get("sortino_ratio")

    oos_max_drawdown = oos_metrics.get("max_drawdown") or 0.0
    oos_total_trades = oos_metrics.get("total_trades") or 0
    # Issue #401: evaluable Reward-Fallback fuer Zero-Loss/Sub-Threshold-Sortino.
    oos_total_return = oos_metrics.get("total_return")

    is_total_trades = 0
    is_max_trades = 0
    is_best_total_return = 0.0
    is_best_win_rate = 0.0
    full_results = data.get("full_results") or []
    if full_results and isinstance(full_results, list):
        trades_list = [r.get("metrics", {}).get("total_trades", 0) for r in full_results if isinstance(r, dict)]
        is_total_trades = sum(trades_list)
        is_max_trades = max(trades_list) if trades_list else 0
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

    return TournamentMetrics(
        oos_evaluated=bool(oos_evaluated),
        oos_eligible=bool(oos_eligible),
        is_sortino_median=float(is_sortino_median) if is_sortino_median is not None else 0.0,
        oos_sortino=float(oos_sortino) if oos_sortino is not None else None,
        oos_max_drawdown=float(oos_max_drawdown) if oos_max_drawdown is not None else 0.0,
        oos_total_trades=int(oos_total_trades) if oos_total_trades is not None else 0,
        win_count=int(win_count) if win_count is not None else 0,
        fully_eligible_pairs=int(fully_eligible_pairs) if fully_eligible_pairs is not None else 0,
        is_total_trades=int(is_total_trades),
        is_max_trades=int(is_max_trades),
        oos_total_return=float(oos_total_return) if oos_total_return is not None else 0.0,
        is_best_total_return=float(is_best_total_return),
        is_best_win_rate=float(is_best_win_rate),
        data_window_start=str(dw_start) if dw_start is not None else None,
        data_window_end=str(dw_end) if dw_end is not None else None,
        data_window_days=float(dw_days) if dw_days is not None else None,
        fill_ts_min=int(dw_fill_min) if dw_fill_min is not None else None,
        fill_ts_max=int(dw_fill_max) if dw_fill_max is not None else None,
    )
