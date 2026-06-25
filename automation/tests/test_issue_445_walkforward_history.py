"""Issue #445 (P1) — Walk-Forward-Geometrie vs. dokumentierte Datenhistorie.

Stellt sicher, dass (a) `backtest.json.walk_forward` und `strategy_defaults.json._schema` dieselbe
Historie-Annahme tragen (Single Source of Truth), und (b) `build_trial` fail-loud abbricht, wenn die
Geometrie (`is_window + splits×oos + holdout`) die dokumentierte `data_history_days` übersteigt —
statt erst spät & still im Spätstarter-Filter ("Keine Instrumente") zu kollabieren.
"""
import datetime as dt
import json
from pathlib import Path

import pytest

from automation.optimizer.trial_config import build_trial, config_dir

UTC = dt.timezone.utc


def _wf():
    return json.loads((config_dir() / "backtest.json").read_text("utf-8"))["walk_forward"]


def test_config_geometry_within_documented_history():
    """Die aktive Geometrie MUSS in die deklarierte Datenhistorie passen (Konsistenz-Invariante)."""
    wf = _wf()
    required = wf["is_window_days"] + wf["splits"] * wf["oos_window_days"] + wf["holdout_days"]
    assert "data_history_days" in wf, "data_history_days fehlt (Issue #445 SSOT-Feld)"
    assert required <= wf["data_history_days"], (
        f"Geometrie {required} d > data_history_days {wf['data_history_days']} d — "
        f"die Konfiguration ist in sich inkonsistent (Issue #445)."
    )


def test_strategy_defaults_schema_matches_history():
    """strategy_defaults._schema referenziert dieselbe ~15-Monate/450-Tage-Annahme wie backtest.json."""
    schema = json.loads((config_dir() / "strategy_defaults.json").read_text("utf-8"))["_schema"]
    desc = schema["description"]
    wf = _wf()
    # Die deklarierte Tageszahl taucht in der Schema-Beschreibung auf (gemeinsame Annahme).
    assert str(wf["data_history_days"]) in desc or "15 Monate" in desc, (
        "strategy_defaults._schema und backtest.json kodieren NICHT dieselbe Historie-Annahme."
    )
    # Die früher widersprüchliche „12 Monate"-Aussage darf nicht mehr alleinstehen.
    assert "15 Monate" in desc


def test_build_trial_raises_when_geometry_exceeds_history():
    """Übersteigt die Geometrie data_history_days, bricht build_trial mit erklärender Meldung ab."""
    now = dt.datetime(2026, 6, 11, tzinfo=UTC)
    with pytest.raises(ValueError, match="Datenhistorie"):
        # n_folds=20 ⇒ 180 + 20×45 + 45 = 1125 d ≫ 450 d
        build_trial("ComboTrendVwapStrategy", {}, study_name="issue445_fail",
                    trial_number=0, seed=42, now=now, holdout_days=45, n_folds=20)


def test_build_trial_passes_with_default_geometry(tmp_path):
    """Die validierte Default-Geometrie (180/45/4/45 = 405 ≤ 450) passiert die Assertion."""
    now = dt.datetime(2026, 6, 11, tzinfo=UTC)
    trial_dir, manifest_path = build_trial(
        "ComboTrendVwapStrategy", {}, study_name="issue445_ok",
        trial_number=0, seed=42, now=now, holdout_days=45, n_folds=4,
    )
    assert Path(manifest_path).exists()
