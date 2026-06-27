import pytest
import datetime as dt
from automation.optimizer.trial_config import compute_walk_forward_window
from automation.optimizer.confirm import confirm_per_symbol_promotion

def test_compute_walk_forward_window_clamp():
    now = dt.datetime(2026, 6, 25, tzinfo=dt.timezone.utc)

    # Testfall 1: now ist neuer als Katalog => Window verschiebt sich nach hinten
    catalog_dt = dt.datetime(2026, 6, 10, 15, 30, tzinfo=dt.timezone.utc)
    catalog_newest_ns = int(catalog_dt.timestamp() * 1_000_000_000)

    start_clamp, end_clamp = compute_walk_forward_window(
        now=now,
        holdout_days=45,
        is_window_days=120,
        oos_window_days=30,
        n_folds=4,
        catalog_newest_ns=catalog_newest_ns,
    )

    start_no_clamp, end_no_clamp = compute_walk_forward_window(
        now=catalog_dt,  # Simulating that now was exactly the catalog time
        holdout_days=45,
        is_window_days=120,
        oos_window_days=30,
        n_folds=4,
    )

    assert end_clamp == end_no_clamp
    assert start_clamp == start_no_clamp
    assert end_clamp < now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=45)

    # Testfall 2: now ist älter als Katalog => Window bleibt bei now
    future_catalog_dt = dt.datetime(2026, 7, 10, tzinfo=dt.timezone.utc)
    future_catalog_newest_ns = int(future_catalog_dt.timestamp() * 1_000_000_000)

    start_no_clamp_now, end_no_clamp_now = compute_walk_forward_window(
        now=now,
        holdout_days=45,
        is_window_days=120,
        oos_window_days=30,
        n_folds=4,
    )

    start_future_clamp, end_future_clamp = compute_walk_forward_window(
        now=now,
        holdout_days=45,
        is_window_days=120,
        oos_window_days=30,
        n_folds=4,
        catalog_newest_ns=future_catalog_newest_ns,
    )

    assert end_future_clamp == end_no_clamp_now
    assert start_future_clamp == start_no_clamp_now

class DummyStudy:
    class DummyTrial:
        @property
        def user_attrs(self):
            return {"sampled_params": {"param1": 1}}
        @property
        def params(self):
            return {"param1": 1}
    best_trial = DummyTrial()
    trials = []
    best_value = 1.0

def test_confirm_per_symbol_promotion_short_circuit(monkeypatch):
    import automation.optimizer.confirm as confirm_module

    def dummy_config_dir():
        import tempfile
        from pathlib import Path
        import json
        d = Path(tempfile.mkdtemp())
        with open(d / "backtest.json", "w") as f:
            json.dump({"walk_forward": {"is_window_days": 120, "holdout_days": 45}}, f)
        return d
    monkeypatch.setattr(confirm_module, "config_dir", dummy_config_dir)

    # We set catalog_newest_ns to something clearly in the past
    now = dt.datetime.now(dt.timezone.utc)
    past_dt = now - dt.timedelta(days=365)
    catalog_newest_ns = int(past_dt.timestamp() * 1_000_000_000)

    # This should fail-fast and return the rejection dict
    result = confirm_per_symbol_promotion(
        study=DummyStudy(),
        strategy="TestStrat",
        symbol="TEST.SYM",
        global_params={},
        catalog_newest_ns=catalog_newest_ns
    )

    assert result["promote"] is False
    assert result["status"] == "REJECTED_ON_HOLDOUT"
    assert result["is_rejection_detail_override"] == "REJECT_HOLDOUT_UNREACHABLE"
