"""Issue #844 (P0) — `sortino_numeric_guard_min_periods` fehlt weiterhin (5. Wiederkehr).

Katalog #823 hat diagnostiziert, dass `sortino_numeric_guard_min_periods` in `tournament.json`
fehlt und `_effective_sortino_numeric_guard` dadurch inert ist. Der #823-Fix dokumentierte den Key
im `_schema`, setzte den WERT aber nie. Der Fix hier: (1) den Wert setzen, (2) eine
Registry-Preflight (`_required_keys.json` + `invariants.check_required_config_keys` +
`sweep.assert_required_config_keys_valid`) einführen, die verhindert, dass ein registrierter Key
künftig erneut still fehlen kann.

Akzeptanzkriterien:
- AK-1: check_required_config_keys FAILt auf einer Config ohne den Key und PASSt, sobald er
  gesetzt ist.
- AK-2: sweep.main() (bzw. run_per_symbol_sweep im echten Pfad) bricht mit Exit-Code 2 ab, wenn
  ein required-Key fehlt — vor dem ersten Symbol.
- AK-3: _effective_sortino_numeric_guard(25.0, 36) == 3.75 (±1e-9);
  _effective_sortino_numeric_guard(25.0, 1600) == 25.0.
- AK-4: Im Re-Run trägt jede SORTINO_GUARD_TRIPPED-Meldung einen n_periods-abhängigen Guard-Wert.
"""
import json

import pytest

from automation.optimizer import invariants as inv


# ── AK-1: check_required_config_keys (reine Funktion) ───────────────────────────────────────────
_SPEC = {
    "schema_version": 1,
    "tournament.json": {
        "sortino_numeric_guard_min_periods": {"type": "number", "required": True},
    },
    "optimizer.json": {
        "floor_plateau_k_example": {"type": "integer", "required": True, "reject_values": [0]},
    },
}


def test_ak1_fails_on_missing_required_key():
    result = inv.check_required_config_keys({"tournament.json": {}}, _SPEC)
    assert result.passed is False
    assert "tournament.json::sortino_numeric_guard_min_periods" in (result.actual or {})
    assert result.severity == "blocking"


def test_ak1_passes_once_key_is_set():
    result = inv.check_required_config_keys(
        {"tournament.json": {"sortino_numeric_guard_min_periods": 1600},
         "optimizer.json": {"floor_plateau_k_example": 5}},
        _SPEC,
    )
    assert result.passed is True


def test_null_value_is_treated_as_missing():
    result = inv.check_required_config_keys(
        {"tournament.json": {"sortino_numeric_guard_min_periods": None}}, _SPEC)
    assert result.passed is False
    assert result.actual["tournament.json::sortino_numeric_guard_min_periods"] == "ist null"


def test_reject_values_are_rejected():
    result = inv.check_required_config_keys(
        {"tournament.json": {"sortino_numeric_guard_min_periods": 1600},
         "optimizer.json": {"floor_plateau_k_example": 0}},
        _SPEC,
    )
    assert result.passed is False
    assert "optimizer.json::floor_plateau_k_example" in result.actual


def test_non_required_keys_are_ignored():
    spec = {"tournament.json": {"optional_key": {"type": "number", "required": False}}}
    result = inv.check_required_config_keys({"tournament.json": {}}, spec)
    assert result.passed is True


def test_metadata_keys_in_spec_are_skipped():
    """schema_version/_comment im Spec-Root sind keine Datei-Sektionen und dürfen den Check nicht
    crashen lassen."""
    result = inv.check_required_config_keys({}, _SPEC)
    assert result.passed is False  # die echten required-Keys fehlen weiterhin


# ── AK-3: der reale tournament.json-Wert macht den T-bewussten Guard aktiv ───────────────────────
def test_ak3_real_config_activates_t_aware_guard():
    import automation.backtest_runner as br

    br._sortino_numeric_guard_min_periods_cache = None
    br._sortino_numeric_guard_min_periods_cached = False
    br._sortino_numeric_guard_reference_mode_cache = None
    try:
        assert br._effective_sortino_numeric_guard(25.0, 36)[0] == pytest.approx(3.75, abs=1e-9)
        assert br._effective_sortino_numeric_guard(25.0, 1600)[0] == pytest.approx(25.0, abs=1e-9)
    finally:
        br._sortino_numeric_guard_min_periods_cache = None
        br._sortino_numeric_guard_min_periods_cached = False
        br._sortino_numeric_guard_reference_mode_cache = None


def test_real_tournament_json_sets_the_key():
    data = json.loads(open("automation/config/tournament.json", encoding="utf-8").read())
    assert data.get("sortino_numeric_guard_min_periods") == 1600


def test_real_required_keys_json_is_valid_and_matches_real_configs():
    """Die reale _required_keys.json PASST gegen die realen tournament.json/optimizer.json —
    sonst würde der Preflight jeden echten Sweep-Start blockieren."""
    spec = json.loads(open("automation/config/_required_keys.json", encoding="utf-8").read())
    configs = {}
    for filename in spec:
        if filename in ("schema_version", "_comment"):
            continue
        configs[filename] = json.loads(open(f"automation/config/{filename}", encoding="utf-8").read())
    result = inv.check_required_config_keys(configs, spec)
    assert result.passed is True, result.detail


# ── AK-2: sweep.assert_required_config_keys_valid() ──────────────────────────────────────────────
def test_ak2_assert_exits_with_code_2_on_missing_key(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)
    (tmp_path / "_required_keys.json").write_text(json.dumps(_SPEC), "utf-8")
    (tmp_path / "tournament.json").write_text(json.dumps({}), "utf-8")
    (tmp_path / "optimizer.json").write_text(json.dumps({"floor_plateau_k_example": 5}), "utf-8")

    with pytest.raises(SystemExit) as exc_info:
        sweep.assert_required_config_keys_valid()
    assert exc_info.value.code == 2


def test_assert_is_a_noop_when_all_required_keys_present(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)
    (tmp_path / "_required_keys.json").write_text(json.dumps(_SPEC), "utf-8")
    (tmp_path / "tournament.json").write_text(
        json.dumps({"sortino_numeric_guard_min_periods": 1600}), "utf-8")
    (tmp_path / "optimizer.json").write_text(json.dumps({"floor_plateau_k_example": 5}), "utf-8")

    sweep.assert_required_config_keys_valid()  # darf NICHT raisen


def test_assert_is_a_noop_when_required_keys_json_is_absent(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)  # kein _required_keys.json hier
    sweep.assert_required_config_keys_valid()  # darf NICHT raisen (Registry ist additiv/opt-in)


def test_preflight_is_wired_into_run_per_symbol_sweep(monkeypatch, tmp_path):
    """Der Preflight läuft im ECHTEN Pfad (using_real_optimize=True), NICHT bei injizierten
    HI-7-Fakes (siehe test_issue_595_strategy_space_parity.py-Konvention)."""
    from automation.optimizer import sweep

    called = {"n": 0}

    def _fake_assert():
        called["n"] += 1
        raise SystemExit(2)

    monkeypatch.setattr(sweep, "assert_required_config_keys_valid", _fake_assert)
    monkeypatch.setattr(sweep, "assert_strategy_space_parity", lambda *a, **k: None)
    monkeypatch.setattr(sweep, "_assert_gate_reward_parity", lambda: None)
    monkeypatch.setattr(sweep, "assert_pandas_version_supported", lambda: None)
    monkeypatch.setattr(sweep, "assert_structural_min_modelled_trials_valid", lambda *a, **k: None)

    with pytest.raises(SystemExit):
        sweep.run_per_symbol_sweep(["S"], ["A.ETORO"], tier="all", n_jobs=1)
    assert called["n"] == 1
