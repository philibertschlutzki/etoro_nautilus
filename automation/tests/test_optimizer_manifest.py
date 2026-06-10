import json, hashlib, datetime as dt
from pathlib import Path
import pytest
from automation.optimizer import manifest, resolve, trial_config

UTC = dt.timezone.utc

# --- resolve_params -------------------------------------------------------
def test_resolve_params_precedence(tmp_path):
    (tmp_path / "strategy_defaults.json").write_text(json.dumps(
        {"SmaCrossoverStrategy": {"sma_period": 5, "cooldown_bars": 12}}), "utf-8")
    (tmp_path / "strategies.json").write_text(json.dumps(
        {"strategies": [{"strategy_class": "SmaCrossoverStrategy", "params": {"cooldown_bars": 20}}]}), "utf-8")
    out = resolve.resolve_params("SmaCrossoverStrategy", {"sma_period": 8}, tmp_path)
    assert out["sma_period"] == 8       # sampled gewinnt
    assert out["cooldown_bars"] == 20   # strategies.json > defaults

# --- manifest helpers -----------------------------------------------------
def test_sha256_file_deterministic(tmp_path):
    f = tmp_path / "x.bin"; f.write_bytes(b"hello")
    assert manifest.sha256_file(f) == hashlib.sha256(b"hello").hexdigest()

def test_catalog_fingerprint_stable_and_sensitive(tmp_path):
    (tmp_path / "AAA").mkdir(); (tmp_path / "AAA" / "data.parquet").write_bytes(b"a")
    fp1 = manifest.catalog_fingerprint(tmp_path)
    assert fp1 == manifest.catalog_fingerprint(tmp_path)          # stabil
    (tmp_path / "BBB").mkdir(); (tmp_path / "BBB" / "data.parquet").write_bytes(b"b")
    assert manifest.catalog_fingerprint(tmp_path) != fp1          # reagiert auf Änderung

def test_catalog_fingerprint_missing_dir_no_crash(tmp_path):
    assert isinstance(manifest.catalog_fingerprint(tmp_path / "nope"), str)

# --- optimizer.json -------------------------------------------------------
def test_optimizer_json_parses_and_has_keys():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    for k in ("n_trials","n_startup_trials","seed","penalty_overfit_weight",
              "penalty_dd_weight","bonus_coverage_weight","penalty_unevaluable_oos","sortino_clip_abs"):
        assert k in cfg

def test_backtest_json_has_holdout():
    cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    assert isinstance(cfg["walk_forward"]["holdout_days"], int)

# --- build_trial: deterministisches end_time via injiziertem now ----------
def test_build_trial_end_time_weekday(tmp_path):
    now = dt.datetime(2026, 6, 10, 15, 0, tzinfo=UTC)   # Mittwoch
    _, mpath = trial_config.build_trial(
        "SmaCrossoverStrategy", {"sma_period": 8},
        study_name="s", trial_number=0, seed=42, now=now, holdout_days=45, n_folds=4)
    m = json.loads(Path(mpath).read_text("utf-8"))
    assert m["manifest_version"] == "1.0"
    assert m["global_settings"]["end_time"] == "2026-04-26T00:00:00Z"   # 2026-06-10 − 45 Tage
    assert m["strategies"][0]["params"]["sma_period"] == 8

def test_build_trial_sunday_rollback(tmp_path):
    now = dt.datetime(2026, 6, 7, 9, 0, tzinfo=UTC)     # Sonntag → −1 Tag (06.06.) − 45
    _, mpath = trial_config.build_trial(
        "SmaCrossoverStrategy", {}, study_name="s", trial_number=1, seed=42,
        now=now, holdout_days=45, n_folds=4)
    m = json.loads(Path(mpath).read_text("utf-8"))
    assert m["global_settings"]["end_time"] == "2026-04-22T00:00:00Z"   # 2026-06-06 − 45 Tage

# --- Standalone-Prinzip ---------------------------------------------------
def test_no_forbidden_imports():
    for p in Path("automation/optimizer").glob("*.py"):
        src = p.read_text("utf-8")
        assert "from archive" not in src and "import archive" not in src
        assert "from adapters" not in src and "import adapters" not in src
