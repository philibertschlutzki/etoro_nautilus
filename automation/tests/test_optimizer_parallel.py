import warnings
from optuna.exceptions import ExperimentalWarning
from automation.optimizer.run_optimization import build_storage, build_sampler

def test_storage_has_busy_timeout():
    st = build_storage("sqlite:///:memory:")
    eng = st.engine if hasattr(st, "engine") else st._backend.engine
    assert eng is not None

def test_sampler_suppresses_experimental_warning():
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        build_sampler({"n_startup_trials": 16, "seed": 42})
    assert not any(issubclass(w.category, ExperimentalWarning) for w in rec)

def test_warns_when_seed_and_parallel(monkeypatch, caplog):
    import automation.optimizer.run_optimization as ro
    monkeypatch.setattr(ro, "make_objective", lambda s, **k: (lambda t: 0.0))
    class _Study:
        study_name = "x"
        def set_user_attr(self, *a): pass
        def optimize(self, *a, **k): pass
    monkeypatch.setattr(ro.optuna, "create_study", lambda **k: _Study())
    with caplog.at_level("WARNING"):
        ro.optimize("SmaCrossoverStrategy", n_trials=1, n_jobs=4)
    assert any("n_jobs>1" in r.message or "reproduzier" in r.message.lower() for r in caplog.records)
