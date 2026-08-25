"""Issue #1270 (GH #1140), Pitfall #447-Klasse in AGENTS.md — persistente Zustände dürfen nicht
WORK-relativ liegen.

Symptom. ``symbol_bar_quality_cache.cache_found = false`` in 3/3 Läufen; der Champion-Store las
sich in jedem Lauf als ``STORE_EMPTY``/``STORE_PATH_MISSING`` — jeweils, obwohl ein Vorlauf
denselben Store bereits befüllt hatte.

Root-Cause. ``champions._champions_dir()`` (= ``WORK / "champions"``), der Symbol-Bar-Qualitäts-
Cache, der kalibrierte-Slippage-Cache und der Annualisierungsfaktor-Cache lasen/schrieben alle
unter ``WORK`` — dem Wegwerf-Verzeichnis, das ``logs/executor.sh`` (Empfehlung E-1 aus Issue
#1142) je Lauf FRISCH anlegt. E-1 (frisches WORK je Lauf) und "Zustand überlebt einen Lauf" schließen
sich KONSTRUKTIV aus, solange der Zustand unter WORK liegt.

Fix.
1. ``manifest.CHAMPION_ROOT``/``RUN_FINGERPRINT_INDEX_PATH``/``PERSISTENT_CACHE_ROOT`` — alle drei
   an ``PROJECT_ROOT`` verankert (nicht an ``WORK``), je per eigener Env-Var überschreibbar.
2. ``champions._champions_dir()``/``store_status()`` lesen/schreiben seither ``CHAMPION_ROOT``.
3. ``sweep.write_symbol_bar_quality_cache``/``calibrate_and_write_slippage_cache`` und
   ``report.py``s Lesestellen sowie ``backtest_runner._annualization_factor_cache_path`` verwenden
   seither ``PERSISTENT_CACHE_ROOT`` statt ``WORK``.
"""
import ast
import importlib
from pathlib import Path

from automation.optimizer import champions, manifest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------------------------
# manifest — PROJECT_ROOT-Verankerung, unabhängig von OPTIMIZER_WORK_DIR
# ---------------------------------------------------------------------------------------------

def test_champion_root_default_is_project_root_anchored():
    assert manifest.CHAMPION_ROOT == manifest.PROJECT_ROOT / "data" / "optimizer" / "champions"


def test_persistent_cache_root_default_is_project_root_anchored():
    assert manifest.PERSISTENT_CACHE_ROOT == manifest.PROJECT_ROOT / "data" / "optimizer" / "cache"


def test_persistent_roots_survive_work_dir_override(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIMIZER_CHAMPION_DIR", raising=False)
    monkeypatch.delenv("OPTIMIZER_PERSISTENT_CACHE_DIR", raising=False)
    monkeypatch.setenv("OPTIMIZER_WORK_DIR", str(tmp_path / "fresh_work_dir_run_2"))
    reloaded = importlib.reload(manifest)
    try:
        assert reloaded.WORK == tmp_path / "fresh_work_dir_run_2"
        assert reloaded.CHAMPION_ROOT == reloaded.PROJECT_ROOT / "data" / "optimizer" / "champions"
        assert reloaded.PERSISTENT_CACHE_ROOT == reloaded.PROJECT_ROOT / "data" / "optimizer" / "cache"
        assert reloaded.WORK not in reloaded.CHAMPION_ROOT.parents
        assert reloaded.WORK not in reloaded.PERSISTENT_CACHE_ROOT.parents
    finally:
        monkeypatch.undo()
        importlib.reload(manifest)


def test_champion_root_overridable_via_own_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTIMIZER_CHAMPION_DIR", str(tmp_path / "custom_champions"))
    reloaded = importlib.reload(manifest)
    try:
        assert reloaded.CHAMPION_ROOT == tmp_path / "custom_champions"
    finally:
        monkeypatch.undo()
        importlib.reload(manifest)


# ---------------------------------------------------------------------------------------------
# champions.py — reads CHAMPION_ROOT, not WORK
# ---------------------------------------------------------------------------------------------

def test_champions_module_does_not_import_work():
    # champions.py braucht WORK fuer gar nichts mehr (siehe Summary: Import wurde entfernt) —
    # ein wiedereingefuehrter WORK-Import waere ein Rueckfall in die #1270-Root-Cause.
    assert "WORK" not in dir(champions) or champions.WORK is not manifest.WORK


def test_champions_dir_returns_champion_root(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "CHAMPION_ROOT", tmp_path / "champions_override")
    d = champions._champions_dir()
    assert d == tmp_path / "champions_override"
    assert d.is_dir()


def test_store_status_reports_champion_root_path(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "CHAMPION_ROOT", tmp_path / "champions_status_check")
    status = champions.store_status()
    assert str(tmp_path / "champions_status_check") in str(status.get("store_path"))


# ---------------------------------------------------------------------------------------------
# Grep-Regressionsschutz — kein Pfad im Repo klettert von WORK aus zu einem persistenten Store
# ---------------------------------------------------------------------------------------------

def _source_lines(relative_path: str) -> list[str]:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8").splitlines()


def test_no_champions_dir_construction_relative_to_work():
    for path in ("automation/optimizer/champions.py",):
        for i, line in enumerate(_source_lines(path), start=1):
            assert 'WORK / "champions"' not in line, f"{path}:{i}: {line}"


def test_no_persistent_cache_construction_relative_to_work():
    targets = [
        ("automation/optimizer/sweep.py", ["write_symbol_bar_quality_cache(WORK"]),
        ("automation/optimizer/report.py", ["read_symbol_bar_quality_cache(WORK",
                                             "symbol_bar_quality_cache_status(WORK",
                                             "read_calibrated_slippage_cache(WORK"]),
    ]
    for path, forbidden_snippets in targets:
        src = (REPO_ROOT / path).read_text(encoding="utf-8")
        for snippet in forbidden_snippets:
            assert snippet not in src, f"{path} still contains forbidden WORK-relative snippet: {snippet!r}"


def test_annualization_factor_cache_path_defaults_to_persistent_cache_root():
    src = (REPO_ROOT / "automation/backtest_runner.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    func = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_annualization_factor_cache_path")
    func_src = ast.get_source_segment(src, func)
    assert "PERSISTENT_CACHE_ROOT" in func_src
    assert "from automation.optimizer.manifest import WORK" not in func_src


def test_sweep_symbol_bar_quality_write_uses_persistent_cache_root():
    src = (REPO_ROOT / "automation/optimizer/sweep.py").read_text(encoding="utf-8")
    assert "write_symbol_bar_quality_cache(PERSISTENT_CACHE_ROOT" in src


def test_sweep_calibrate_and_write_slippage_cache_defaults_to_persistent_cache_root():
    from automation.optimizer import sweep
    import inspect
    src = inspect.getsource(sweep.calibrate_and_write_slippage_cache)
    assert "work_dir = PERSISTENT_CACHE_ROOT" in src


# ---------------------------------------------------------------------------------------------
# champions.py docstrings no longer point at WORK (informational drift guard)
# ---------------------------------------------------------------------------------------------

def test_champions_docstrings_reference_champion_root_not_work():
    import inspect
    for fn in (champions.store_status, champions.load_champion_seed):
        doc = inspect.getdoc(fn) or ""
        assert "CHAMPION_ROOT" in doc
