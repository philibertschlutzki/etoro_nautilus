"""Issue #665 — per-Fold-Sortino: annualisiert mit per-Fold-VARIIERENDEM Faktor (über Folds
inkommensurabel) + Rauschen bei kleinem T.

``_get_annualization_factor`` leitet den Annualisierungsfaktor EMPIRISCH aus der Kalender-Span
JEDES Folds ab — unterschiedliche Kalenderabdeckung (Wochenenden/Feiertage/Lücken) erzeugt
fold-spezifische Faktoren. Der annualisierte per-Fold-Sortino (``oos_fold_sortinos``) ist damit
über Folds NICHT vergleichbar, obwohl PBO/CSCV und die Fold-Median-Telemetrie ihn bisher darüber
mittelten. Der Fix führt die annualisierungs-invariante Parallelgrösse ``oos_fold_sortino_periods``
(per-Perioden-Sortino je Fold) ein, die für jede fold-übergreifende Aggregation kanonisch ist.
"""
import math

from automation.backtest_runner import (
    apply_fold_aggregation,
    collect_oos_fold_sortino_periods,
    collect_oos_fold_sortinos,
    _effective_sortino_numeric_guard,
)


def test_collect_oos_fold_sortino_periods_skips_none_and_preserves_order():
    folds = [{"sortino_period": 0.05}, {"sortino_period": None}, {"sortino_period": 0.03}]
    assert collect_oos_fold_sortino_periods(folds) == [0.05, 0.03]


def test_collect_oos_fold_sortino_periods_empty():
    assert collect_oos_fold_sortino_periods([]) == []


def _fold(sortino_period: float, annualization_factor: float, total_return: float = 0.01) -> dict:
    """Simuliert einen ``_calculate_stats``-Fold-Output: derselbe per-Perioden-Sortino, aber mit
    einem fold-spezifischen (empirischen) Annualisierungsfaktor auf ``sortino_ratio`` skaliert —
    exakt das #665-Symptom (zwei Folds, gleiche Periodenqualität, unterschiedliche Kalender-Span)."""
    return {
        "sortino_period": sortino_period,
        "sortino_ratio": sortino_period * math.sqrt(annualization_factor),
        "total_return": total_return,
    }


def test_fold_median_invariant_to_fold_specific_annualization_factor():
    """Zwei Studien mit IDENTISCHER per-Fold-Perioden-Sortino-Sequenz, aber unterschiedlichen
    (fold-spezifischen) Kalender-Spans/Annualisierungsfaktoren je Fold, liefern IDENTISCHE
    ``oos_fold_sortino_periods``/``sortino_period_fold_median`` — invariant gegen die Wahl des
    Annualisierungsfaktors. Die annualisierte Legacy-Serie (``oos_fold_sortinos``) divergiert
    dagegen (bleibt nur forensisch)."""
    period_seq = [0.05, -0.03, 0.02, 0.08]

    # Lauf A: "kompakte" Kalenderabdeckung je Fold (z. B. RTH ohne Wochenenden-Lücken).
    factors_a = [1400.0, 1550.0, 1300.0, 1650.0]
    per_fold_oos_a = [_fold(p, f) for p, f in zip(period_seq, factors_a)]

    # Lauf B: DIESELBE Perioden-Performance, aber jeder Fold hat eine ANDERE (z. B. lückenhaftere)
    # Kalender-Span ⇒ ein völlig anderer, fold-spezifischer Annualisierungsfaktor.
    factors_b = [900.0, 4200.0, 2100.0, 500.0]
    per_fold_oos_b = [_fold(p, f) for p, f in zip(period_seq, factors_b)]

    metrics_a = {"sortino_ratio": 0.5, "total_return": 0.01}
    metrics_b = {"sortino_ratio": 0.5, "total_return": 0.01}
    apply_fold_aggregation(metrics_a, per_fold_oos_a)
    apply_fold_aggregation(metrics_b, per_fold_oos_b)

    # Kanonische, annualisierungs-invariante Grösse: bit-identisch über beide Läufe.
    assert metrics_a["oos_fold_sortino_periods"] == metrics_b["oos_fold_sortino_periods"]
    assert metrics_a["sortino_period_fold_median"] == metrics_b["sortino_period_fold_median"]
    # Der issue-benannte Telemetrie-Alias trägt dieselbe Serie.
    assert metrics_a["per_fold_oos_sortino_period"] == metrics_a["oos_fold_sortino_periods"]

    # Die annualisierte Legacy-Serie ist NICHT invariant (das ist exakt das #665-Symptom) —
    # bleibt aber unter ihrem eigenen (forensischen) Namen verfügbar.
    assert metrics_a["oos_fold_sortinos"] != metrics_b["oos_fold_sortinos"]
    assert metrics_a["oos_fold_sortinos"] == collect_oos_fold_sortinos(per_fold_oos_a)


def test_sortino_period_fold_median_matches_raw_median():
    per_fold_oos = [_fold(0.05, 1500.0), _fold(0.03, 1500.0), _fold(-0.01, 1500.0), _fold(0.07, 1500.0)]
    metrics = {"sortino_ratio": 0.5, "total_return": 0.01}
    apply_fold_aggregation(metrics, per_fold_oos)
    import statistics
    assert metrics["sortino_period_fold_median"] == statistics.median([0.05, 0.03, -0.01, 0.07])


def test_empty_per_fold_oos_list_yields_none_median_and_empty_periods():
    metrics = {"sortino_ratio": None, "total_return": 0.0}
    apply_fold_aggregation(metrics, [])
    assert metrics["oos_fold_sortino_periods"] == []
    assert metrics["sortino_period_fold_median"] is None


def test_effective_guard_is_bit_identical_when_key_absent(monkeypatch):
    """Fehlt ``sortino_numeric_guard_min_periods`` (Default) ⇒ der T-bewusste Guard ist ein
    No-Op — bit-identisch zum Pre-#665-Guard, unabhängig von n_periods."""
    import automation.backtest_runner as br
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cache", None)
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cached", False)
    monkeypatch.setattr(br, "config_dir", lambda: __import__("pathlib").Path("/nonexistent-cfg-dir"))
    assert _effective_sortino_numeric_guard(25.0, 10) == 25.0
    assert _effective_sortino_numeric_guard(25.0, 5000) == 25.0


def test_effective_guard_scales_down_for_small_t_when_configured(monkeypatch, tmp_path):
    """Ist ``sortino_numeric_guard_min_periods`` konfiguriert, schrumpft der effektive Guard
    proportional zu sqrt(T/min_periods) UNTERHALB der Referenz und bleibt EXAKT beim Legacy-Wert
    bei/über der Referenz (bit-identisch am und über dem Referenzpunkt)."""
    import json
    import automation.backtest_runner as br

    cfg_dir = tmp_path
    (cfg_dir / "tournament.json").write_text(
        json.dumps({"sortino_numeric_guard_min_periods": 500}), encoding="utf-8"
    )
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cache", None)
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cached", False)
    monkeypatch.setattr(br, "config_dir", lambda: cfg_dir)

    guard_at_reference = _effective_sortino_numeric_guard(25.0, 500)
    guard_above_reference = _effective_sortino_numeric_guard(25.0, 5000)
    guard_small_t = _effective_sortino_numeric_guard(25.0, 137)

    assert guard_at_reference == 25.0
    assert guard_above_reference == 25.0  # gecappt bei 1.0 ⇒ nie strenger als der Legacy-Wert
    assert guard_small_t < 25.0
    assert math.isclose(guard_small_t, 25.0 * math.sqrt(137 / 500), rel_tol=1e-9)
