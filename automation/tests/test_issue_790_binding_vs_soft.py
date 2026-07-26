"""Issue #790 (P1) — `near_miss_deltas` mischt aktive und deaktivierte Gates; das gemeldete
bindende Gate ist für 283 von 321 Studies eines, das gar keine Eligibility-Entscheidung mehr trifft.

Root-Cause: `near_miss_deltas` enthält 10 Gates (`oos_gate_deltas`), aber nur eine Teilmenge davon
ist tatsächlich in `eligible_requires_all`/`_any` — vier wurden per #650/#676/#677/#697 entfernt und
wirken seither nur noch als weiche Near-Miss-Distanz.

Fix: `report._split_near_miss_deltas` trennt anhand von `reward._active_gate_collinearity_keys`
(dieselbe Laufzeit-Quelle wie der #760-Kollinearitäts-Check, keine zweite gepflegte Liste) in
`{'binding': ..., 'soft': ...}`; `binding_gate` ist ausschliesslich aus `binding` abgeleitet.
"""
from automation.optimizer.report import _split_near_miss_deltas


def _cfg(requires_all, requires_any=None):
    return {"eligible_requires_all": requires_all, "eligible_requires_any": requires_any or []}


def test_config_without_min_expectancy_puts_it_in_soft_never_binding():
    cfg = _cfg(["min_trades", "max_drawdown", "oos_min_psr"], ["min_profit_factor", "min_win_rate"])
    raw = {
        "oos_min_trades": 0.5, "oos_max_drawdown": 0.1, "oos_min_psr": -0.05,
        "oos_min_expectancy": -0.30, "oos_min_profitable_folds_frac": -0.90,
    }
    binding, soft, binding_gate = _split_near_miss_deltas(raw, cfg)
    assert "oos_min_expectancy" in soft
    assert "oos_min_expectancy" not in binding
    assert "oos_min_profitable_folds_frac" in soft
    # Binding-Gate ist NIE aus soft, obwohl oos_min_profitable_folds_frac das negativste Delta hat.
    assert binding_gate != "oos_min_profitable_folds_frac"
    assert binding_gate == "oos_min_psr"


def test_binding_gate_is_always_a_key_from_requires_all_or_any():
    cfg = _cfg(["min_trades", "max_drawdown", "oos_min_psr", "oos_min_excess_return"],
               ["min_profit_factor", "min_win_rate"])
    raw = {
        "oos_min_trades": 0.3, "oos_max_drawdown": 0.1, "oos_min_psr": 0.2,
        "oos_min_excess_return": -0.01, "oos_min_expectancy": -0.99, "any_condition": 0.4,
    }
    binding, soft, binding_gate = _split_near_miss_deltas(raw, cfg)
    active_keys = {"oos_min_trades", "oos_max_drawdown", "oos_min_psr", "oos_min_excess_return",
                  "any_condition"}
    assert binding_gate in active_keys
    assert binding_gate == "oos_min_excess_return"


def test_empty_raw_deltas_yields_empty_binding_and_soft():
    cfg = _cfg(["min_trades"])
    binding, soft, binding_gate = _split_near_miss_deltas({}, cfg)
    assert binding == {}
    assert soft == {}
    assert binding_gate is None


def test_disabled_gate_key_never_appears_in_binding_even_if_most_negative():
    """Reproduziert die #742-Beobachtung: oos_min_profitable_folds_frac(-0.90) waere das
    negativste Delta ueber ALLE Keys, darf aber (deaktiviert seit #676) niemals binding_gate sein."""
    cfg = _cfg(["min_trades", "oos_min_psr"], ["min_profit_factor"])
    raw = {"oos_min_trades": 0.1, "oos_min_psr": 0.05,
          "oos_min_profitable_folds_frac": -0.90, "oos_min_expectancy": -0.85}
    binding, soft, binding_gate = _split_near_miss_deltas(raw, cfg)
    assert binding_gate in ("oos_min_trades", "oos_min_psr")
    assert "oos_min_profitable_folds_frac" not in binding
    assert "oos_min_expectancy" not in binding


def test_report_record_carries_split_structure(monkeypatch):
    from automation.optimizer.report import _study_record

    class _T:
        def __init__(self, value, deltas):
            self.value = value
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": True, "oos_gate_deltas": deltas}

    class _S:
        trials = [_T(1.0, {"oos_min_trades": 0.2, "oos_min_expectancy": -0.5})]
        best_value = 1.0
        user_attrs = {}

    tournament_cfg = _cfg(["min_trades"], ["min_profit_factor"])
    proposal = {"symbol": "X", "strategy": "Y"}
    record, _checks = _study_record(proposal, _S(), tournament_cfg)
    assert set(record["near_miss_deltas"].keys()) == {"binding", "soft"}
    assert "oos_min_expectancy" in record["near_miss_deltas"]["soft"]
    assert "oos_min_trades" in record["near_miss_deltas"]["binding"]
    assert record["binding_gate"] == "oos_min_trades"
