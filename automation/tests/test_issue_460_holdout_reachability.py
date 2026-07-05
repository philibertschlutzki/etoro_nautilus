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
            json.dump({"walk_forward": {"is_window_days": 120, "holdout_days": 45, "oos_window_days": 45, "splits": 1, "embargo_period_days": 0}}, f)
        return d
    monkeypatch.setattr(confirm_module, "config_dir", dummy_config_dir)

    # We set catalog_newest_ns to something clearly in the past, BUT to make it evaluate properly for the holdout check
    # (where window_start is clamped by catalog_newest_ns), we need to ensure the condition `catalog_newest_ns < oos_lo_ns` triggers.
    # oos_lo_ns = window_start + is_window_days + (n_folds * oos_window_days) + embargo_period_days
    # If window_start is clamped by catalog_newest_ns, then `window_start = catalog_newest_ns - holdout_days - is_window_days - ...`
    # So `oos_lo_ns` will be strictly GREATER than `catalog_newest_ns` if we use a specific mock or if we just bypass the clamp logic in the test.
    # Wait, if window_start is derived from catalog_newest_ns (clamped), then:
    # window_start = catalog_end - holdout - (is + n_folds*oos)
    # oos_lo_ns = window_start + is + n_folds*oos + embargo
    # oos_lo_ns = catalog_end - holdout + embargo
    # The condition is: catalog_newest_ns < oos_lo_ns.
    # So catalog_newest_ns < catalog_end - holdout + embargo
    # Since catalog_end is just catalog_newest_ns rounded to midnight, this is approx:
    # 0 < -holdout + embargo => 0 < -45 + 0 => False!
    # So the clamp PREVENTS the rejection from ever triggering!

    # We need to mock `compute_walk_forward_window` so `window_start` isn't clamped by `catalog_newest_ns` to test this branch!
    def mock_compute_walk_forward_window(*args, **kwargs):
        # Return a fixed window start so that catalog_newest_ns < oos_lo_ns evaluates to True
        # E.g. window_start = now - 200 days
        now = dt.datetime.now(dt.timezone.utc)
        return now - dt.timedelta(days=200), now

    import automation.optimizer.trial_config as tc
    monkeypatch.setattr(tc, "compute_walk_forward_window", mock_compute_walk_forward_window)
    # Because confirm.py imports it locally inside the function, we need to mock it where it's called
    import sys
    monkeypatch.setitem(sys.modules, "automation.optimizer.trial_config", tc)

    now = dt.datetime.now(dt.timezone.utc)
    # Set catalog_newest_ns to be BEFORE oos_lo_ns (which is now - 200 + 120 + 45 = now - 35 days)
    catalog_newest_ns = int((now - dt.timedelta(days=100)).timestamp() * 1_000_000_000)

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
