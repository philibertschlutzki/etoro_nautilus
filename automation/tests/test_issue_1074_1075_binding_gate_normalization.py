"""Issue #1074/#1075 (Katalog #866-2, Kohorte D) — ``binding_gate``/``holdout_binding_gate`` waren
``argmin`` über ROHE, dimensionsbehaftete Deltas (Wiederkehr #631 in neuer Umgebung): ein
grosskaliges Gate (``oos_min_trades``, Skala 10²) gewinnt nie gegen ein kleinskaliges
(``oos_min_psr``, Skala 1), unabhängig davon, welches Gate ÖKONOMISCH tatsächlich bindet (Beweis
B-11 im #866-Katalog: ``oos_max_drawdown`` lag in 14/14 Studies im engen Band 0,2626–0,2984 gegen
ein 30-%-Gate — bindet NIRGENDS — wurde aber in 8/14 Studies trotzdem als "das bindende Gate"
ausgewiesen).

#1074 — ``reward.normalize_gate_deltas_for_binding`` + ``report._split_near_miss_deltas`` leiten
``binding_gate`` aus den NORMIERTEN Deltas ab; beide Formen bleiben im Report sichtbar.

#1075 — ``confirm._holdout_binding_gate`` normiert ebenso UND wird nur gesetzt, wenn die
Holdout-Stufe TATSÄCHLICH FAILt; ein bestandener Holdout trägt stattdessen
``holdout_tightest_margin`` (semantisch ehrlich: kein Gate ist gescheitert).
"""
from automation.optimizer import reward as _reward
from automation.optimizer.report import _split_near_miss_deltas
from automation.optimizer.confirm import (
    _holdout_binding_gate, _holdout_tightest_margin, _active_holdout_gate_deltas,
)


# ── reward.normalize_gate_deltas_for_binding ─────────────────────────────────────────────────────
def test_normalize_uses_configured_threshold_as_scale():
    cfg = {"max_drawdown": 0.30, "oos_min_psr": 0.75}
    raw = {"oos_max_drawdown": 0.0374, "oos_min_psr": -0.0653}
    normalized = _reward.normalize_gate_deltas_for_binding(raw, cfg)
    assert abs(normalized["oos_max_drawdown"] - 0.0374 / 0.30) < 1e-9
    assert abs(normalized["oos_min_psr"] - (-0.0653 / 0.75)) < 1e-9


def test_normalize_reproduces_the_866_catalog_reference_case():
    """AdxAtr-Referenzfall (Beweis B-11): oos_min_trades=442.0 (Skala 10^2), oos_max_drawdown=
    0.2626 (~immer im Band, bindet nirgends), oos_min_psr=0.0653 (die tatsaechlich knappe Groesse).
    Normiert muss oos_min_psr am naechsten an 0 (am knappsten) liegen, NICHT oos_max_drawdown."""
    cfg = {"min_trades": 100, "max_drawdown": 0.30, "oos_min_psr": 0.75}
    raw = {"oos_min_trades": 442.0, "oos_max_drawdown": 0.2626, "oos_min_psr": 0.0653}
    normalized = _reward.normalize_gate_deltas_for_binding(raw, cfg)
    tightest = min(normalized, key=lambda k: normalized[k])
    assert tightest == "oos_min_psr"
    assert normalized["oos_max_drawdown"] < 1.0  # weit von der 30%-Schwelle entfernt normiert


def test_normalize_falls_back_to_identity_without_a_resolvable_threshold():
    normalized = _reward.normalize_gate_deltas_for_binding({"oos_min_psr": -0.15}, {})
    assert normalized["oos_min_psr"] == -0.15


# ── report._split_near_miss_deltas: binding_gate derived from binding_normalized ────────────────
def test_binding_gate_no_longer_always_picks_the_large_scale_gate():
    cfg = {
        "eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr"],
        "min_trades": 100, "max_drawdown": 0.30, "oos_min_psr": 0.75,
    }
    raw = {"oos_min_trades": 442.0, "oos_max_drawdown": 0.2626, "oos_min_psr": 0.0653}
    binding, soft, binding_gate, binding_normalized = _split_near_miss_deltas(raw, cfg)
    assert binding_gate == "oos_min_psr"
    assert set(binding_normalized) == {"oos_min_trades", "oos_max_drawdown", "oos_min_psr"}


def test_binding_gate_max_drawdown_never_wins_when_deltas_stay_in_a_tight_band():
    """8/14 Studies im #866-Katalog wiesen oos_max_drawdown faelschlich als bindend aus, obwohl
    das Delta immer im Band 0,2626-0,2984 gegen ein 30%-Gate lag (bindet nirgends)."""
    cfg = {
        "eligible_requires_all": ["max_drawdown", "oos_min_psr"],
        "max_drawdown": 0.30, "oos_min_psr": 0.75,
    }
    for dd_delta in (0.2626, 0.2984, 0.28):
        raw = {"oos_max_drawdown": dd_delta, "oos_min_psr": 0.0653}
        _b, _s, binding_gate, _bn = _split_near_miss_deltas(raw, cfg)
        assert binding_gate == "oos_min_psr", f"failed for dd_delta={dd_delta}"


# ── confirm._holdout_binding_gate / _holdout_tightest_margin ────────────────────────────────────
def test_holdout_binding_gate_normalized_reproduces_the_866_catalog_case():
    cfg = {
        "eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr"],
        "min_trades": 100, "max_drawdown": 0.30, "oos_min_psr": 0.75,
    }
    deltas = {"oos_min_trades": 442.0, "oos_max_drawdown": 0.2626, "oos_min_psr": 0.0653}
    assert _holdout_binding_gate(deltas, cfg) == "oos_min_psr"


def test_active_holdout_gate_deltas_excludes_deactivated_gates():
    cfg = {"eligible_requires_all": ["min_trades", "oos_min_psr"]}
    deltas = {"oos_min_trades": 0.1, "oos_min_psr": -0.02, "oos_min_expectancy": -0.99}
    active = _active_holdout_gate_deltas(deltas, cfg)
    assert "oos_min_expectancy" not in active
    assert set(active) == {"oos_min_trades", "oos_min_psr"}


def test_holdout_tightest_margin_picks_the_smallest_positive_normalized_margin():
    cfg = {
        "eligible_requires_all": ["max_drawdown", "oos_min_psr"],
        "max_drawdown": 0.30, "oos_min_psr": 0.75,
    }
    # Beide Gates BESTANDEN (positive Deltas) — oos_min_psr liegt naeher an seiner Schwelle.
    deltas = {"oos_max_drawdown": 0.20, "oos_min_psr": 0.03}
    result = _holdout_tightest_margin(deltas, cfg)
    assert result["gate"] == "oos_min_psr"


def test_holdout_tightest_margin_none_when_nothing_passing():
    cfg = {"eligible_requires_all": ["oos_min_psr"]}
    deltas = {"oos_min_psr": -0.1}
    assert _holdout_tightest_margin(deltas, cfg) is None
