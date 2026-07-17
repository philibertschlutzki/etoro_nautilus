"""Issue #664 — Profitable-Folds-Gate (≥0.5) bei nicht-stationärem Ziel: strukturell an
Regime-Heterogenität gekoppelt — DIAGNOSE + ENTSCHEIDUNG, kein blinder Fix.

Fix: (1) ein Diagnose-Artefakt (Vorzeichen-Autokorrelation + Fold-Sign-Flip-Anteil) trennt
"Symbol-Regime-Signal" von "Strategie-Schwäche"; (2) ein opt-in ``profitable_folds_weighting``-
Config-Key ('equal'|'recency'), Default 'equal' = BIT-IDENTISCH zum Status quo. 'recency' ist rein
deklarativ bereitgestellt — die produktive Aktivierung erfordert einen eigenen Kalibrierlauf
(explizit NICHT Teil dieses Fixes).
"""
import automation.backtest_runner as br
from automation.backtest_runner import (
    apply_fold_aggregation,
    _evaluate_oos_eligibility,
    _fold_recency_weights,
    _fold_regime_diagnostics,
)


def _reset_caches(monkeypatch, cfg_dir):
    monkeypatch.setattr(br, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(br, "_profitable_folds_weighting_cache", None)
    monkeypatch.setattr(br, "_recency_halflife_folds_cache", None)
    monkeypatch.setattr(br, "_recency_halflife_folds_cached", False)
    monkeypatch.setattr(br, "_fold_winsorize_cache", None)
    monkeypatch.setattr(br, "_fold_profit_epsilon_cache", None)


def _fold(total_return):
    return {"total_return": total_return, "sortino_ratio": None, "sortino_period": None}


# ── Diagnose-Artefakt (rein informativ) ──────────────────────────────────────────────────────────
def test_regime_diagnostics_flag_nonstationary_symbol_pattern():
    """TSLA-artiges Muster: Fold 1-3 uniform negativ, Fold 4 (jüngster) positiv ⇒ WENIGE Flips
    (robustes Primärsignal) — ein Symbol-Regime-Signal, keine Strategie-Rejection. Die
    Autokorrelation (sekundär, small-N-sensitiv bei 3:1-Klassenungleichgewicht) ist lediglich
    definiert, ihr Vorzeichen wird hier bewusst NICHT geprüft (siehe Docstring)."""
    diag = _fold_regime_diagnostics([-0.02, -0.015, -0.018, 0.03])
    assert diag["fold_sign_flip_frac"] == 1.0 / 3.0  # nur der letzte Übergang flippt das Vorzeichen
    assert diag["fold_sign_autocorr"] is not None


def test_regime_diagnostics_balanced_regime_split_has_positive_autocorr():
    """Ein balancierter Zwei-Regime-Split (2 negative, dann 2 positive Folds — genau EIN
    Vorzeichenwechsel) liefert eine klar POSITIVE Autokorrelation (Regime-Läufe, keine
    Klassen-Imbalance-Verzerrung)."""
    diag = _fold_regime_diagnostics([-0.02, -0.015, 0.02, 0.025])
    assert diag["fold_sign_flip_frac"] == 1.0 / 3.0
    assert diag["fold_sign_autocorr"] > 0.0


def test_regime_diagnostics_alternating_pattern_has_negative_autocorr():
    diag = _fold_regime_diagnostics([0.01, -0.01, 0.01, -0.01])
    assert diag["fold_sign_flip_frac"] == 1.0
    assert diag["fold_sign_autocorr"] < 0.0


def test_regime_diagnostics_none_for_degenerate_input():
    assert _fold_regime_diagnostics([]) == {"fold_sign_autocorr": None, "fold_sign_flip_frac": None}
    assert _fold_regime_diagnostics([0.01]) == {"fold_sign_autocorr": None, "fold_sign_flip_frac": None}


def test_apply_fold_aggregation_stamps_diagnostics_and_recency_fraction():
    per_fold = [_fold(-0.02), _fold(-0.015), _fold(-0.018), _fold(0.03)]
    metrics = {"sortino_ratio": None, "total_return": -0.005}
    apply_fold_aggregation(metrics, per_fold)
    assert "fold_sign_autocorr" in metrics
    assert "fold_sign_flip_frac" in metrics
    assert "oos_profitable_folds_frac_recency" in metrics
    # Equal-Fraktion bleibt unverändert bei 1/4 (nur Fold 4 profitabel).
    assert metrics["oos_profitable_folds_frac"] == 0.25
    # Recency-Fraktion gewichtet den (einzigen profitablen) jüngsten Fold stärker ⇒ höher als 1/4.
    assert metrics["oos_profitable_folds_frac_recency"] > metrics["oos_profitable_folds_frac"]


# ── Recency-Gewichte (rein) ───────────────────────────────────────────────────────────────────────
def test_fold_recency_weights_monotonically_increasing_toward_present():
    w = _fold_recency_weights(4, halflife=2.0)
    assert len(w) == 4
    assert w == sorted(w)  # ältester (Index 0) -> jüngster (Index 3) monoton steigend
    assert w[-1] == 1.0    # jüngster Fold trägt das volle (unnormierte) Gewicht


def test_fold_recency_weights_default_halflife_when_none_or_nonpositive():
    w_none = _fold_recency_weights(4, None)
    w_zero = _fold_recency_weights(4, 0.0)
    assert w_none == w_zero  # beide fallen auf halflife=n_folds zurück


def test_fold_recency_weights_empty():
    assert _fold_recency_weights(0, 2.0) == []


# ── Gate: 'equal' bit-identisch, 'recency' opt-in ────────────────────────────────────────────────
_BASE_TCFG = {
    "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
    "oos_min_win_rate": 0.0, "oos_min_profitable_folds_frac": 0.5, "max_drawdown": 0.3,
}


def _oos_metrics_for_gate(per_fold):
    m = {
        "total_trades": 40, "max_drawdown": 0.02, "win_rate": 0.4, "total_return": 0.01,
        "expectancy": 0.01, "sortino_ratio": 1.0, "profit_factor": 1.2,
        "median_position_notional": 1000.0,
    }
    apply_fold_aggregation(m, per_fold)
    return m


def test_default_equal_mode_matches_legacy_frac_and_gate_outcome():
    # 1 von 4 Folds profitabel (0.25 < 0.5) ⇒ Gate scheitert unter 'equal' (Default, kein Key gesetzt).
    per_fold = [_fold(-0.02), _fold(-0.015), _fold(-0.018), _fold(0.03)]
    m = _oos_metrics_for_gate(per_fold)
    ev = _evaluate_oos_eligibility(m, dict(_BASE_TCFG, eligible_requires_all=["oos_min_profitable_folds_frac"]))
    assert ev["oos_eligible"] is False
    assert any("oos_min_profitable_folds" in r for r in ev["oos_rejection_reasons"])
    assert not any("recency" in r for r in ev["oos_rejection_reasons"])


def test_recency_mode_can_flip_gate_outcome_when_explicitly_configured(monkeypatch, tmp_path):
    """Bewusst konfiguriertes 'recency' kann ein Symbol mit uniform negativen Alt-Folds + starkem
    jüngstem Fold eligible machen, wo 'equal' es ablehnt — NUR wenn der Key explizit gesetzt ist.

    ``apply_fold_aggregation`` liest die Recency-Halbwertszeit aus der (gecachten) Datei-Config —
    hier auf eine echte tmp tournament.json mit aggressiver Halbwertszeit (1.0) gesetzt, damit die
    RECENCY-FRAKTION selbst (nicht nur die Gate-Auswahl) den jüngsten Fold stark genug gewichtet."""
    import json
    (tmp_path / "tournament.json").write_text(
        json.dumps({"recency_halflife_folds": 1.0}), encoding="utf-8")
    _reset_caches(monkeypatch, tmp_path)

    per_fold = [_fold(-0.02), _fold(-0.015), _fold(-0.018), _fold(0.05)]
    m = _oos_metrics_for_gate(per_fold)
    tcfg_equal = dict(_BASE_TCFG, eligible_requires_all=["oos_min_profitable_folds_frac"])
    tcfg_recency = dict(tcfg_equal, profitable_folds_weighting="recency")

    ev_equal = _evaluate_oos_eligibility(m, tcfg_equal)
    ev_recency = _evaluate_oos_eligibility(m, tcfg_recency)

    assert ev_equal["oos_eligible"] is False
    assert ev_recency["oos_eligible"] is True


def test_recency_mode_absent_key_is_bit_identical_to_equal():
    """Fehlt der Key komplett (kein 'profitable_folds_weighting' in tournament_cfg) ⇒ identisches
    Ergebnis wie explizites 'equal'."""
    per_fold = [_fold(0.01), _fold(-0.02), _fold(0.015), _fold(-0.03)]
    m = _oos_metrics_for_gate(per_fold)
    tcfg_absent = dict(_BASE_TCFG, eligible_requires_all=["oos_min_profitable_folds_frac"])
    tcfg_explicit_equal = dict(tcfg_absent, profitable_folds_weighting="equal")

    ev_absent = _evaluate_oos_eligibility(m, tcfg_absent)
    ev_explicit = _evaluate_oos_eligibility(m, tcfg_explicit_equal)
    assert ev_absent["oos_eligible"] == ev_explicit["oos_eligible"]


def test_config_readers_default_to_equal_and_none(monkeypatch, tmp_path):
    cfg_dir = tmp_path
    _reset_caches(monkeypatch, cfg_dir)
    assert br._read_profitable_folds_weighting() == "equal"
    assert br._read_recency_halflife_folds() is None


def test_config_readers_pick_up_tournament_json(monkeypatch, tmp_path):
    import json
    cfg_dir = tmp_path
    (cfg_dir / "tournament.json").write_text(
        json.dumps({"profitable_folds_weighting": "recency", "recency_halflife_folds": 2.5}),
        encoding="utf-8",
    )
    _reset_caches(monkeypatch, cfg_dir)
    assert br._read_profitable_folds_weighting() == "recency"
    assert br._read_recency_halflife_folds() == 2.5
