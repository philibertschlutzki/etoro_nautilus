import json, datetime as dt
from pathlib import Path
from automation.optimizer.trial_config import build_trial, config_dir

UTC = dt.timezone.utc

def _parse(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

def _holdout_days():
    with open(config_dir() / "backtest.json") as f:
        return json.load(f).get("walk_forward", {}).get("holdout_days", 45)

def test_holdout_oos_disjoint_from_optimization_window(tmp_path, monkeypatch):
    now = dt.datetime(2026, 6, 11, 9, 0, tzinfo=UTC)
    hd = _holdout_days()
    # Optimierungs-Trial
    _, opt_mf = build_trial("ComboTrendVwapStrategy", {}, study_name="s", trial_number=0,
                            seed=42, now=now, holdout_days=hd, n_folds=4)
    opt = json.loads(Path(opt_mf).read_text())["global_settings"]
    opt_start, opt_end = _parse(opt["start_time"]), _parse(opt["end_time"])

    # Holdout-Confirm-Trial
    _, ho_mf = build_trial("ComboTrendVwapStrategy", {}, study_name="s_holdout", trial_number=0,
                           seed=42, now=now, holdout_days=0, n_folds=1, oos_window_days_override=hd)
    ho = json.loads(Path(ho_mf).read_text())["global_settings"]
    ho_end = _parse(ho["end_time"])
    ho_oos_start = ho_end - dt.timedelta(days=hd)

    # bewerteter OOS [ho_oos_start, ho_end] disjunkt von Optimierung [opt_start, opt_end]
    assert ho_oos_start >= opt_end          # kein echter Overlap
    assert ho_end > opt_end                 # Holdout liegt strikt nach der Optimierung

def test_trial_config_walkforward_matches_sizing(tmp_path):
    now = dt.datetime(2026, 6, 11, tzinfo=UTC)
    td, _ = build_trial("ComboTrendVwapStrategy", {}, study_name="s", trial_number=1,
                        seed=42, now=now, holdout_days=45, n_folds=4)
    wf = json.loads((Path(td) / "config" / "backtest.json").read_text())["walk_forward"]
    assert wf["splits"] == 4
    assert wf["holdout_days"] == 45

def test_confirm_passes_holdout_days_as_oos_override(monkeypatch):
    from automation.optimizer import confirm as cmod
    from automation.optimizer.parsing import TournamentMetrics

    captured = {}
    def spy_build(**kw):
        captured.update(kw)
        return (Path("/tmp/x"), Path("/tmp/x/m.json"))
    def fake_run(td, mp, **kw): return Path("/tmp/x/tournament_result.json")

    def cmod_dummy_metrics():
        return TournamentMetrics(
            oos_evaluated=True,
            oos_eligible=True,
            is_sortino_median=1.0,
            oos_sortino=1.0,
            oos_max_drawdown=0.1,
            oos_total_trades=10,
            win_count=5,
            fully_eligible_pairs=5,
            is_total_trades=20,
            hit_trade_cap=False
        )

    # parse_tournament/Output minimal mocken, sodass confirm nicht crasht:
    monkeypatch.setattr(cmod, "parse_tournament", lambda p: cmod_dummy_metrics())

    class DummyTrial:
        def __init__(self):
            self.params = {}
            self.user_attrs = {"sampled_params": {}}
            self.number = 0
            self.value = -1.0

    class DummyStudy:
        def __init__(self):
            self.best_trial = DummyTrial()
            self.study_name = "s"

    study = DummyStudy()
    cmod.confirm_on_holdout(study, "ComboTrendVwapStrategy",
                            run_backtest=fake_run, build_trial=spy_build)
    hd = _holdout_days()
    assert captured.get("oos_window_days_override") == hd
    assert captured.get("holdout_days") == 0 and captured.get("n_folds") == 1

def test_confirm_per_symbol_promotion_property_sweep(monkeypatch, tmp_path):
    """Property-Test: Iterativer Sweep für catalog_end zur Überprüfung der OOS-Coverage Invariante."""
    from automation.optimizer import confirm as cmod
    from automation.optimizer.trial_config import compute_walk_forward_window
    import json

    # Mock configs
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "backtest.json").write_text(json.dumps({
        "walk_forward": {
            "is_window_days": 120,
            "holdout_days": 45,
                "oos_window_days": 30,
                "splits": 1,
                "embargo_period_days": 0
        }
    }))

    monkeypatch.setattr(cmod, "config_dir", lambda: cfg_dir)

    now = dt.datetime.now(dt.timezone.utc)
    window_start, _ = compute_walk_forward_window(
        now=now,
        holdout_days=45,
        is_window_days=120,
        oos_window_days=30,
        n_folds=1,
    )
    oos_lo_ns = int((window_start + dt.timedelta(days=120 + 30 + 0)).timestamp() * 1_000_000_000) # is_window_days + (n_folds * oos_window_days) + embargo_period_days

    class DummyTrial:
        def __init__(self):
            self.params = {}
            self.user_attrs = {"sampled_params": {}}
            self.number = 0
            self.value = -1.0

    class DummyStudy:
        def __init__(self):
            self.best_trial = DummyTrial()
            self.study_name = "s"
            # Issue #615 — confirm_per_symbol_promotion selektiert eligible Trials aus ``study.trials``
            # (Filter auf gestempeltes ``oos_eligible``). Für diesen reinen Reachability-Preflight-Sweep
            # reicht eine leere Trials-Liste: für offset >= 0 (Katalog abgedeckt) greift dann fail-loud
            # HOLDOUT_NO_ELIGIBLE_TRIALS (Rejection OHNE den REJECT_HOLDOUT_UNREACHABLE-Override) ⇒
            # is_rejected bleibt False, exakt wie der Sweep es erwartet.
            self.trials = []
            self.directions = ["maximize"]

    study = DummyStudy()

    def fake_metrics(*args, **kwargs):
        class DummyMetrics:
            def __init__(self):
                self.oos_evaluated = True
                self.oos_eligible = True
                self.oos_sortino = 1.0
                self.oos_max_drawdown = 0.1
                self.oos_total_trades = 10
        return DummyMetrics()

    monkeypatch.setattr(cmod, "_holdout_metrics_for_params", fake_metrics)
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kwargs: 1.0)

    def fake_compute(*args, **kwargs):
        # Always return the fixed window_start ignoring catalog_newest_ns to guarantee boundary condition evaluates exactly based on `now`.
        return window_start, window_start
    import automation.optimizer.trial_config as tc_mod
    monkeypatch.setattr(tc_mod, "compute_walk_forward_window", fake_compute)

    # Iterate through offsets from -10 to +10 days relative to oos_lo_ns
    day_ns = int(dt.timedelta(days=1).total_seconds() * 1_000_000_000)
    for offset in range(-10, 11):
        test_ns = oos_lo_ns + (offset * day_ns)

        res = cmod.confirm_per_symbol_promotion(
            study, "Strat", "SYM", {},
            run_backtest=lambda *a, **k: None,
            build_trial=lambda *a, **k: (None, None),
            catalog_newest_ns=test_ns
        )

        # Issue #1002/#1154 (Katalog #1170) — REJECT_HOLDOUT_UNREACHABLE erreicht die Holdout-
        # Stufe nie (Datenstand deckt das Fenster noch nicht ab) und traegt seither
        # REJECTED_BEFORE_HOLDOUT statt des geteilten REJECTED_ON_HOLDOUT.
        is_rejected = res.get("status") == "REJECTED_BEFORE_HOLDOUT" and res.get("is_rejection_detail_override") == "REJECT_HOLDOUT_UNREACHABLE"

        # Assertion: Preflight evaluiert zu False (is_rejected ist True) exakt dann, wenn geometrische Abdeckung nicht gegeben ist (test_ns < oos_lo_ns)
        assert is_rejected == (test_ns < oos_lo_ns)
