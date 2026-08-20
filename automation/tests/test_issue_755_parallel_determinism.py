"""Issue #755 (P1, aber zwingend gleichzeitig mit #753) — Durchsatz: `seed` erzwang sweep-weite
Serialisierung.

Root-Cause: ``sweep.main`` warf einen harten ``ValueError``, sobald ``optimizer.json['seed']``
gesetzt UND ``--n-jobs > 1`` war. Determinismus wurde auf der falschen Ebene erzwungen — jede Study
hat eine EIGENE SQLite-Datei (``resolve_storage``, #400) und einen eigenen Sampler; zwei Studies
unterschiedlicher (Strategie, Symbol)-Paare teilen keinerlei Sampler-Zustand. Reproduzierbarkeit
erfordert nur, dass JEDE Study einen deterministischen Seed erhält — nicht, dass sie nacheinander
laufen.

Fix: ``seed_effective = seed XOR stable_hash(study_name)`` (Blake2b, auf 32 Bit maskiert für
numpy-Kompatibilität) statt eines sweep-weit identischen Seeds; der ``ValueError`` entfällt;
``sweep_max_workers`` steuert den Default, wenn ``--n-jobs`` nicht explizit gesetzt ist.
"""
import json
import time
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro
from automation.optimizer import sweep
from automation.optimizer import trial_config


# ── seed_effective: reine Funktion ─────────────────────────────────────────────────────────────
def test_seed_effective_constant_for_same_study_name():
    a = ro.seed_effective(42, "study_SmaCrossoverStrategy_TSLA_ETORO")
    b = ro.seed_effective(42, "study_SmaCrossoverStrategy_TSLA_ETORO")
    assert a == b


def test_seed_effective_differs_across_study_names():
    a = ro.seed_effective(42, "study_SmaCrossoverStrategy_TSLA_ETORO")
    b = ro.seed_effective(42, "study_SmaCrossoverStrategy_AAPL_ETORO")
    c = ro.seed_effective(42, "study_AdxAtrMomentumStrategy_TSLA_ETORO")
    assert len({a, b, c}) == 3


def test_seed_effective_none_seed_stays_none():
    assert ro.seed_effective(None, "study_X") is None


def test_seed_effective_within_numpy_valid_range():
    """Akzeptanzkriterium (Regressionstest): der Wert MUSS in [0, 2**32-1] liegen — numpy.random.
    RandomState (Optuna-Sampler-Backend) wirft sonst ValueError('Seed must be between 0 and
    2**32 - 1'). Ueber viele Study-Namen geprüft (deckt die Maskierung ab, nicht nur einen Treffer)."""
    for i in range(500):
        v = ro.seed_effective(42, f"study_Strategy{i}_SYMBOL{i}.ETORO")
        assert 0 <= v <= 2**32 - 1


def test_seed_effective_actually_seeds_a_tpe_sampler_without_error():
    """Reproduktion des realen Crashs vor dem Fix: ein aus einem willkuerlichen study_name
    abgeleiteter seed_effective muss TPESampler(seed=...) ohne ValueError instanziieren."""
    import optuna
    for name in ("study_ComboTrendVwapStrategy_NVDA_ETORO", "study_SmaCrossoverStrategy_A_ETORO",
                "x" * 200):
        v = ro.seed_effective(42, name)
        optuna.samplers.TPESampler(seed=v)  # darf nicht werfen


# ── sweep.main: kein ValueError mehr bei seed + --n-jobs > 1 ──────────────────────────────────────
def test_sweep_main_allows_n_jobs_greater_one_with_seed_set(monkeypatch, capsys):
    """Akzeptanzkriterium: --n-jobs 4 bei gesetztem seed laeuft ohne ValueError."""
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: [])
    # Produktions-optimizer.json hat seed=42 gesetzt (config_dir() default) — kein Monkeypatch noetig,
    # das ist genau das Szenario, das vorher hart abbrach.
    sweep.main(["--strategies", "SmaCrossoverStrategy", "--symbols", "A.ETORO",
               "--tier", "all", "--n-jobs", "4"])
    out = capsys.readouterr().out
    assert "Aktive Konfiguration" in out


def test_sweep_main_passes_effective_n_jobs_to_run_per_symbol_sweep(monkeypatch):
    captured = {}

    def _fake_run(strategies, symbols, *, tier, n_jobs, n_jobs_source, **_kwargs):
        captured["n_jobs"] = n_jobs
        captured["n_jobs_source"] = n_jobs_source
        return []

    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _fake_run)
    sweep.main(["--strategies", "SmaCrossoverStrategy", "--symbols", "A.ETORO",
               "--tier", "all", "--n-jobs", "4"])
    assert captured["n_jobs"] == 4
    assert captured["n_jobs_source"] == "CLI"


def test_sweep_main_default_n_jobs_uses_sweep_max_workers_config(monkeypatch, tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "optimizer.json").write_text(json.dumps({"seed": 7, "sweep_max_workers": 3}), "utf-8")
    monkeypatch.setattr(sweep, "config_dir", lambda: cfg_dir)

    captured = {}

    def _fake_run(strategies, symbols, *, tier, n_jobs, n_jobs_source, **_kwargs):
        captured["n_jobs"] = n_jobs
        captured["n_jobs_source"] = n_jobs_source
        return []

    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _fake_run)
    monkeypatch.setattr(sweep, "log_active_config", lambda *a, **k: None)
    sweep.main(["--strategies", "SmaCrossoverStrategy", "--symbols", "A.ETORO", "--tier", "all"])
    assert captured["n_jobs"] == 3
    assert captured["n_jobs_source"] == "CONFIG_SWEEP_MAX_WORKERS"


def test_sweep_main_default_n_jobs_falls_back_to_cpu_minus_2(monkeypatch, tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "optimizer.json").write_text(json.dumps({"seed": 7}), "utf-8")  # kein sweep_max_workers
    monkeypatch.setattr(sweep, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr("os.cpu_count", lambda: 6)

    captured = {}

    def _fake_run(strategies, symbols, *, tier, n_jobs, n_jobs_source, **_kwargs):
        captured["n_jobs"] = n_jobs
        captured["n_jobs_source"] = n_jobs_source
        return []

    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _fake_run)
    monkeypatch.setattr(sweep, "log_active_config", lambda *a, **k: None)
    sweep.main(["--strategies", "SmaCrossoverStrategy", "--symbols", "A.ETORO", "--tier", "all"])
    assert captured["n_jobs"] == 4  # max(1, 6-2)
    assert captured["n_jobs_source"] == "DEFAULT_CPU_MINUS_2"


# ── optimize_symbol: TPESampler erhaelt seed_effective, nicht den rohen seed ───────────────────────
def test_optimize_symbol_seeds_sampler_with_seed_effective_not_raw_seed(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path / "config")
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "optimizer.json").write_text(json.dumps({"seed": 42}), "utf-8")
    # Issue #796 — optimize_symbol friert jetzt vor study.optimize eine Study-Config ein.
    (tmp_path / "config" / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45}}
    ), "utf-8")

    sampler_calls = []

    def mock_sampler(**kwargs):
        sampler_calls.append(kwargs)
        class _Dummy:
            pass
        return _Dummy()

    # Issue #1067/#1217 (Katalog #1196-1221) — der Sweep-Pfad instanziiert seither
    # ``_WindowedTPESampler`` (eine ``optuna.samplers.TPESampler``-Subklasse mit sliding-window
    # Trial-Slice), nicht mehr ``optuna.samplers.TPESampler`` direkt -- die Subklasse bindet ihre
    # Basisklasse zur DEFINITIONSZEIT, ein spaeteres Monkeypatchen von
    # ``optuna.samplers.TPESampler`` faengt den Konstruktoraufruf daher nicht mehr ab.
    monkeypatch.setattr(ro, "_WindowedTPESampler", mock_sampler)

    class DummyStudy:
        study_name = "test"
        def set_user_attr(self, k, v): pass
        def enqueue_trial(self, params): pass
        def optimize(self, obj, n_trials=None, n_jobs=None, **kwargs): pass

    monkeypatch.setattr(ro.optuna, "create_study", lambda **k: DummyStudy())
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {})

    ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=1)

    assert len(sampler_calls) == 1
    expected = ro.seed_effective(42, "study_SmaCrossoverStrategy_AAA_ETORO")
    assert sampler_calls[0]["seed"] == expected
    assert sampler_calls[0]["seed"] != 42


# ── Report-Telemetrie: seed_effective/n_trials_budget je Study, n_jobs am Sweep-Level ─────────────
def test_study_record_exposes_seed_effective_and_budget():
    from automation.optimizer.report import _study_record

    class _T:
        def __init__(self, value):
            self.value = value
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": True}

    class _S:
        def __init__(self):
            self.trials = [_T(1.0), _T(2.0)]
            self.best_value = 2.0
            self._attrs = {"seed_effective": 123456, "n_trials_budget": 120, "n_startup_trials": 16}

        @property
        def user_attrs(self):
            return dict(self._attrs)

    record, _checks = _study_record({"symbol": "AAA.ETORO", "strategy": "SmaCrossoverStrategy"}, _S())
    assert record["seed_effective"] == 123456
    assert record["n_trials_budget"] == 120


def test_generate_sweep_report_cli_args_carry_n_jobs(monkeypatch, tmp_path):
    from automation.optimizer import report as report_mod

    monkeypatch.setattr(report_mod, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(report_mod, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: [])

    captured = {}
    monkeypatch.setattr(report_mod, "generate_sweep_report",
                        lambda *a, **k: captured.setdefault("cli_args", k.get("cli_args")) or Path("x"))
    sweep.main(["--strategies", "SmaCrossoverStrategy", "--symbols", "A.ETORO",
               "--tier", "all", "--n-jobs", "4"])
    assert captured["cli_args"]["n_jobs"] == 4
    assert captured["cli_args"]["n_jobs_source"] == "CLI"
