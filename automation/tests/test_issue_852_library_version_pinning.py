"""Issue #852 (P2) — `optuna` ohne Versions-Pin, während pandas aus genau diesem Grund gepinnt ist.

Root-Cause: ``automation/requirements.txt`` pinnt ``pandas`` (Issue #802) mit Begründung, warum der
numerische Ausgang der Simulation von der Installationsumgebung abhängen kann — ``optuna`` stand
daneben OHNE jede Versionsangabe, obwohl ``TPESampler``s Defaults (``multivariate``/``group``/
``constant_liar``), die ``constraints_func``-Interaktion (#612) und ``TrialState.PRUNED`` (#845)
ebenfalls nicht über Optuna-Majors garantiert sind.

Fix:
1. ``requirements.txt`` pinnt jetzt ``optuna``, ``numpy``, ``nautilus_trader`` mit oberer Grenze
   (``pandas`` war bereits gepinnt).
2. ``optimizer.json['pinned_library_versions']`` registriert dieselben Bereiche maschinenlesbar.
3. ``invariants.check_library_version_drift`` FAILt (severity='blocking'), wenn eine installierte
   Version ausserhalb ihres Pins liegt.
4. ``sweep.assert_pinned_library_versions_valid`` führt denselben Vergleich als Preflight aus
   (Exit-Code 2, gemeinsam mit #844).

Akzeptanzkriterien:
- AK-1: requirements.txt pinnt optuna, numpy, nautilus_trader mit oberer Grenze.
- AK-2: check_library_version_drift FAILt bei einer installierten Version ausserhalb des Pins.
- AK-3: Der #742-Report weist library_versions für alle gepinnten Pakete aus.
- AK-4: Der Drift-Check läuft als Preflight (gemeinsam mit #844) und bricht mit Exit-Code 2 ab.
"""
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import sweep
from automation.optimizer.manifest import library_versions


_REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements.txt"


# ── AK-1: requirements.txt pinnt mit oberer Grenze ───────────────────────────────────────────────
def test_requirements_txt_pins_optuna_numpy_nautilus_with_upper_bound():
    text = _REQUIREMENTS_PATH.read_text("utf-8")
    for line in text.splitlines():
        line = line.strip()
        for pkg in ("optuna", "numpy", "nautilus_trader", "pandas"):
            if line.startswith(pkg + ">=") or line.startswith(pkg + "=="):
                assert "<" in line, f"{pkg} hat keine obere Grenze: {line!r}"


# ── AK-2: check_library_version_drift (reine Funktion) ──────────────────────────────────────────
def test_installed_version_within_pin_passes():
    result = inv.check_library_version_drift(
        {"optuna": "4.9.0"}, {"optuna": ">=4.9,<5.0"})
    assert result.passed is True
    assert result.actual is None


def test_installed_version_above_pin_fails():
    result = inv.check_library_version_drift(
        {"optuna": "5.1.0"}, {"optuna": ">=4.9,<5.0"})
    assert result.passed is False
    assert "optuna" in result.actual
    assert result.severity == "blocking"


def test_installed_version_below_pin_fails():
    result = inv.check_library_version_drift(
        {"pandas": "2.0.0"}, {"pandas": ">=2.2,<4.0"})
    assert result.passed is False
    assert "pandas" in result.actual


def test_missing_installation_is_fail_open_not_a_drift():
    """Eine (noch) nicht installierte, aber gepinnte Bibliothek ist kein FAIL — analog jedem
    anderen Preflight-Check dieses Moduls (fail-open bei fehlenden Daten)."""
    result = inv.check_library_version_drift({"optuna": None}, {"optuna": ">=4.9,<5.0"})
    assert result.passed is True


def test_unpinned_library_is_ignored():
    result = inv.check_library_version_drift(
        {"optuna": "99.0.0", "requests": "2.25.0"}, {"optuna": ">=1.0,<2.0"}
    )
    # optuna IST gepinnt und driftet -> FAIL; requests ist NICHT gepinnt -> irrelevant.
    assert result.passed is False
    assert set(result.actual.keys()) == {"optuna"}


def test_exact_lower_bound_is_satisfied():
    result = inv.check_library_version_drift({"pandas": "2.2.0"}, {"pandas": ">=2.2,<4.0"})
    assert result.passed is True


def test_multiple_offenders_all_reported():
    result = inv.check_library_version_drift(
        {"optuna": "5.5.0", "numpy": "0.9.0"},
        {"optuna": ">=4.9,<5.0", "numpy": ">=1.26,<3.0"})
    assert result.passed is False
    assert set(result.actual.keys()) == {"optuna", "numpy"}


# ── AK-3: manifest.library_versions() deckt alle gepinnten Pakete ab ────────────────────────────
def test_library_versions_covers_all_currently_pinned_packages():
    from automation.optimizer.trial_config import config_dir
    import json
    optimizer_cfg = json.loads((config_dir() / "optimizer.json").read_text("utf-8"))
    pinned = optimizer_cfg.get("pinned_library_versions") or {}
    versions = library_versions()
    for name in pinned:
        assert name in versions, f"{name} ist gepinnt, aber nicht in library_versions() erfasst"


# ── AK-4: sweep.assert_pinned_library_versions_valid() als Preflight ────────────────────────────
def test_assert_exits_with_code_2_on_drift(monkeypatch):
    monkeypatch.setattr(
        sweep, "_load_optimizer_config",
        lambda: {"pinned_library_versions": {"optuna": ">=4.9,<5.0"}})
    monkeypatch.setattr(sweep, "library_versions", lambda: {"optuna": "6.0.0"})
    with pytest.raises(SystemExit) as exc_info:
        sweep.assert_pinned_library_versions_valid()
    assert exc_info.value.code == 2


def test_assert_is_a_noop_when_versions_within_pin(monkeypatch):
    monkeypatch.setattr(
        sweep, "_load_optimizer_config",
        lambda: {"pinned_library_versions": {"optuna": ">=4.9,<5.0"}})
    monkeypatch.setattr(sweep, "library_versions", lambda: {"optuna": "4.9.0"})
    sweep.assert_pinned_library_versions_valid()  # darf NICHT raisen


def test_assert_is_a_noop_when_pin_registry_is_empty(monkeypatch):
    monkeypatch.setattr(sweep, "_load_optimizer_config", lambda: {})
    monkeypatch.setattr(
        sweep, "library_versions", lambda: {"optuna": "99.0.0"})  # waere sonst driftend
    sweep.assert_pinned_library_versions_valid()  # darf NICHT raisen (kein Pin registriert)


def test_preflight_is_wired_into_run_per_symbol_sweep(monkeypatch, tmp_path):
    """Regressionswaechter: assert_pinned_library_versions_valid wird TATSAECHLICH im echten
    Preflight-Pfad aufgerufen, nicht nur isoliert getestet (analog #844s Verdrahtungs-Test)."""
    called = {"n": 0}

    def _fake_assert():
        called["n"] += 1
        raise SystemExit(2)

    monkeypatch.setattr(sweep, "assert_pinned_library_versions_valid", _fake_assert)
    monkeypatch.setattr(sweep, "assert_strategy_space_parity", lambda *a, **k: None)
    monkeypatch.setattr(sweep, "_assert_gate_reward_parity", lambda: None)
    monkeypatch.setattr(sweep, "assert_pandas_version_supported", lambda: None)
    monkeypatch.setattr(sweep, "assert_structural_min_modelled_trials_valid", lambda *a, **k: None)
    monkeypatch.setattr(sweep, "assert_required_config_keys_valid", lambda: None)

    with pytest.raises(SystemExit):
        sweep.run_per_symbol_sweep(["S"], ["A.ETORO"], tier="all", n_jobs=1)
    assert called["n"] == 1
