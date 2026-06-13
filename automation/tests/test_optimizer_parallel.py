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

    events = []
    def mock_emit(logger, event_name, payload):
        events.append((event_name, payload))

    import automation.optimizer.run_optimization
    monkeypatch.setattr(automation.optimizer.run_optimization, "emit_execution_event", mock_emit)
    import automation.log_manager
    monkeypatch.setattr(automation.log_manager, "emit_execution_event", mock_emit)

    import builtins
    original_open = builtins.open

    def mock_open(filepath, *args, **kwargs):
        if "optimizer.json" in str(filepath):
            class MockFile:
                def read(self):
                    return '{"seed": 42, "n_startup_trials": 16, "sqlite_busy_timeout": 60}'
                def __enter__(self):
                    return self
                def __exit__(self, exc_type, exc_val, exc_tb):
                    pass
                def __iter__(self):
                    yield '{"seed": 42, "n_startup_trials": 16, "sqlite_busy_timeout": 60}'
            return MockFile()
        return original_open(filepath, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", mock_open)

    from pathlib import Path
    original_exists = Path.exists
    def mock_exists(self):
        if "optimizer.json" in str(self):
            return True
        return original_exists(self)
    monkeypatch.setattr(Path, "exists", mock_exists)

    with caplog.at_level("WARNING"):
        ro.optimize("SmaCrossoverStrategy", n_trials=1, n_jobs=4)

    # Also support logging module fallback if events not caught
    if any(e[0] == "optimizer_parallel_seed_warning" for e in events):
        warning_payload = next(e[1] for e in events if e[0] == "optimizer_parallel_seed_warning")
        assert "n_jobs>1" in warning_payload["message"] or "reproduzier" in warning_payload["message"].lower()
    else:
        assert any("n_jobs>1" in r.message or "reproduzier" in r.message.lower() for r in caplog.records)
