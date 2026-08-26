"""Issue #1294 (GH #1167, Katalog #1272-1297, P1) — Config-Werte widersprechen ihrer eigenen
dokumentierten Kalibrierung.

Symptom. ``oos_min_trades = 8`` in tournament.json, waehrend das ``_schema``-Feld dokumentiert, der
Wert sei in #617 "von 1 auf 20 gehoben" worden, weil "ein OOS-Sortino/Profit-Factor aus 1-9 Trades
keine Kennzahl" sei — 8 liegt im ausdruecklich verworfenen Bereich. ``sortino_numeric_guard = 500.0``,
waehrend #614 dokumentiert, der Wert sei "auf 25.0 gesenkt" worden. Keine bestehende Pruefung
verglich den Live-Wert mit seiner eigenen dokumentierten Kalibrierung.

Fix.
1. ``_schema.calibrations`` (strukturiert, additiv neben dem bestehenden ``_schema.fields``-
   Fliesstext): ``{config_key: {calibrated_value, calibrated_in_issue, calibration_basis}}``.
2. Neue Invariante ``check_config_matches_calibration`` (severity 'blocking'): weicht der Live-Wert
   ab, muss ein vollstaendiger Eintrag in ``config_override_accepted`` existieren.
3. Beide konkreten Faelle auf den kalibrierten Wert zurueckgesetzt (oos_min_trades=20,
   sortino_numeric_guard=25.0).
"""
import inspect
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv, reward, sweep

CFG_DIR = Path("automation/config")


def _load(name):
    return json.loads((CFG_DIR / name).read_text("utf-8"))


# ---------------------------------------------------------------------------------------------
# Production config: the two concrete cases from the issue are reset to their calibration
# ---------------------------------------------------------------------------------------------

def test_production_oos_min_trades_matches_its_own_calibration():
    cfg = _load("tournament.json")
    assert cfg["oos_min_trades"] == 20
    assert cfg["_schema"]["calibrations"]["oos_min_trades"]["calibrated_value"] == 20


def test_production_sortino_numeric_guard_matches_its_own_calibration():
    cfg = _load("tournament.json")
    assert cfg["sortino_numeric_guard"] == 25.0
    assert cfg["_schema"]["calibrations"]["sortino_numeric_guard"]["calibrated_value"] == 25.0


def test_production_config_passes_check_config_matches_calibration():
    tcfg = _load("tournament.json")
    ocfg = _load("optimizer.json")
    result = inv.check_config_matches_calibration({"tournament.json": tcfg, "optimizer.json": ocfg})
    assert result.passed is True
    assert result.severity == "blocking"


# ---------------------------------------------------------------------------------------------
# invariants.check_config_matches_calibration — pure logic
# ---------------------------------------------------------------------------------------------

def _doc(*, live_value, calibrated_value=20, override=None):
    doc = {
        "oos_min_trades": live_value,
        "_schema": {"calibrations": {
            "oos_min_trades": {"calibrated_value": calibrated_value, "calibrated_in_issue": "#617",
                               "calibration_basis": "..."},
        }},
    }
    if override is not None:
        doc["config_override_accepted"] = {"oos_min_trades": override}
    return doc


def test_matching_live_value_passes():
    result = inv.check_config_matches_calibration({"tournament.json": _doc(live_value=20)})
    assert result.passed is True


def test_reference_symptom_oos_min_trades_8_fails_blocking():
    result = inv.check_config_matches_calibration({"tournament.json": _doc(live_value=8)})
    assert result.passed is False
    assert result.severity == "blocking"
    offender = result.actual["tournament.json:oos_min_trades"]
    assert offender["live_value"] == 8
    assert offender["calibrated_value"] == 20
    assert offender["calibrated_in_issue"] == "#617"


def test_incomplete_override_still_fails():
    result = inv.check_config_matches_calibration({
        "tournament.json": _doc(live_value=8, override={"value": 8, "rationale": "..."})})  # no decided_in_issue
    assert result.passed is False


def test_override_with_wrong_value_still_fails():
    result = inv.check_config_matches_calibration({
        "tournament.json": _doc(live_value=8, override={
            "value": 5, "rationale": "...", "decided_in_issue": "#9999"})})
    assert result.passed is False


def test_complete_documented_override_passes():
    result = inv.check_config_matches_calibration({
        "tournament.json": _doc(live_value=8, override={
            "value": 8, "rationale": "Deliberate operator override for X.", "decided_in_issue": "#9999"})})
    assert result.passed is True


def test_multiple_sources_both_checked():
    result = inv.check_config_matches_calibration({
        "tournament.json": _doc(live_value=8),
        "optimizer.json": _doc(live_value=20),
    })
    assert result.passed is False
    assert "tournament.json:oos_min_trades" in result.actual
    assert "optimizer.json:oos_min_trades" not in result.actual


def test_no_calibrations_present_is_inconclusive():
    result = inv.check_config_matches_calibration({"tournament.json": {"foo": "bar"}})
    assert result.passed is True
    assert result.inconclusive is True


def test_empty_config_docs_is_inconclusive():
    result = inv.check_config_matches_calibration({})
    assert result.passed is True
    assert result.inconclusive is True


def test_non_string_schema_fields_do_not_crash():
    """_schema.fields bleibt String-only (bestehender Konsument); calibrations ist ein
    ZUSAETZLICHES, separates Feld -- diese Funktion darf nicht crashen, wenn fields fremdartige
    Werte traegt."""
    doc = _doc(live_value=20)
    doc["_schema"]["fields"] = {"oos_min_trades": 12345}  # untypisch, aber nicht diese Funktion's Sorge
    result = inv.check_config_matches_calibration({"tournament.json": doc})
    assert result.passed is True


# ---------------------------------------------------------------------------------------------
# reward.assert_config_matches_calibration — fail-loud preflight
# ---------------------------------------------------------------------------------------------

def test_assert_raises_on_mismatch():
    with pytest.raises(ValueError, match="config_override_accepted"):
        reward.assert_config_matches_calibration({"tournament.json": _doc(live_value=8)})


def test_assert_passes_silently_when_calibration_matches():
    reward.assert_config_matches_calibration({"tournament.json": _doc(live_value=20)})


def test_production_config_passes_the_preflight():
    tcfg = _load("tournament.json")
    ocfg = _load("optimizer.json")
    reward.assert_config_matches_calibration({"tournament.json": tcfg, "optimizer.json": ocfg})


def test_sweep_preflight_calls_the_new_guard():
    source = inspect.getsource(sweep._assert_gate_reward_parity)
    assert "assert_config_matches_calibration" in source


# ---------------------------------------------------------------------------------------------
# report.py wiring
# ---------------------------------------------------------------------------------------------

def test_wired_in_build_report():
    from automation.optimizer import report as rpt
    source = inspect.getsource(rpt._build_report)
    assert "check_config_matches_calibration" in source


def test_check_config_matches_calibration_appears_in_stream(tmp_path):
    from automation.optimizer import report as rpt
    report = rpt._build_report(
        [], run_id="run-1294-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_config_matches_calibration" in names
