"""A4.2 — build_trial writes global_settings.instruments (Tier 10).

instruments=None must omit the key entirely (bit-identical legacy manifest, HI-2).
"""
import json
import datetime as dt
from pathlib import Path

from automation.optimizer import trial_config

UTC = dt.timezone.utc


def test_build_trial_writes_instruments(tmp_path):
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    _, mp = trial_config.build_trial(
        "SmaCrossoverStrategy", {}, study_name="s_instr",
        trial_number=0, seed=42, now=now, holdout_days=45, n_folds=4,
        instruments=["TSLA.ETORO"])
    gs = json.loads(Path(mp).read_text("utf-8"))["global_settings"]
    assert gs["instruments"] == ["TSLA.ETORO"]


def test_build_trial_without_instruments_omits_key(tmp_path):
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    _, mp = trial_config.build_trial(
        "SmaCrossoverStrategy", {}, study_name="s_noinstr",
        trial_number=1, seed=42, now=now, holdout_days=45, n_folds=4)
    assert "instruments" not in json.loads(Path(mp).read_text("utf-8"))["global_settings"]
