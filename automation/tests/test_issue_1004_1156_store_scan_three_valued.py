"""Issue #1004/#1156 (Katalog #1170, P1) — ``store_scan.n_own`` ist fail-open.

Symptom (B-10): ``n_own = 42`` bzw. ``28`` bei je 14 Studies.

Root-Cause: ``n_own`` war ``len(_store_scan_paths) - n_foreign`` — ein reiner Komplement-Zaehler.
Jede nicht ladbare Study (``_load_study_for_proposal`` scheitert) oder Study ohne Trials zaehlte
automatisch als "eigen", weil sie nicht nachweislich fremd war.

Fix:
1. Dreiwertige Klassifikation: ``n_own`` (Proposal mit >= 1 Trial dieser ``run_id``), ``n_foreign``
   (nur fremde Trials), ``n_unclassifiable`` (Study nicht ladbar / keine Trials / weder eigen noch
   fremd).
2. ``n_own`` zaehlt ausschliesslich positiv nachgewiesene eigene Proposals.
3. Neue Invariante ``check_store_scan_coherence`` (severity ``high``): FAIL, wenn
   ``n_own != len(studies)`` oder ``n_unclassifiable > 0`` ohne dokumentierte Purge-Provenienz.
"""
import json
import sys
import types
import unittest.mock as mock
from pathlib import Path

import pytest

# Issue #1004/#1156 — ``report._build_report`` reaches ``invariants.check_config_key_registry``,
# which does an UNGUARDED lazy ``from automation.backtest_runner import ...`` (pre-existing, not
# introduced by this fix) -- ``backtest_runner`` itself pulls in ``nautilus_trader`` at module
# scope. Same mock-injection technique as ``test_issue_953_1119_stop_loss_vs_bar_range.py`` so the
# REAL (pure-Python) ``backtest_runner`` constants/functions load in this sandbox (no installable
# ``nautilus_trader``, Python 3.11 instead of the required >=3.12).
if "nautilus_trader" not in sys.modules:
    class _MockModule(types.ModuleType):
        def __getattr__(self, name):
            return mock.MagicMock()

    for _mod in (
        "nautilus_trader", "nautilus_trader.backtest", "nautilus_trader.backtest.engine",
        "nautilus_trader.backtest.models", "nautilus_trader.model", "nautilus_trader.model.data",
        "nautilus_trader.model.enums", "nautilus_trader.model.identifiers",
        "nautilus_trader.model.currencies", "nautilus_trader.model.objects",
        "nautilus_trader.model.instruments", "nautilus_trader.config", "nautilus_trader.common",
        "nautilus_trader.common.enums", "nautilus_trader.common.actor", "nautilus_trader.core",
        "nautilus_trader.core.message", "nautilus_trader.portfolio", "nautilus_trader.test_engine",
        "nautilus_trader.persistence", "nautilus_trader.persistence.catalog",
        "nautilus_trader.execution", "nautilus_trader.execution.messages",
    ):
        sys.modules[_mod] = _MockModule(_mod)

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


# --- invariants.check_store_scan_coherence ---------------------------------------------------------

def test_scenario_a_from_the_issue_fails():
    """A: n_own=14, n_foreign=0, n_unclassifiable=28 -- muss failen (n_unclassifiable > 0)."""
    store_scan = {"n_studies_in_store": 42, "n_own": 14, "n_foreign": 0, "n_unclassifiable": 28}
    result = inv.check_store_scan_coherence(store_scan, n_studies=14)
    assert result.passed is False
    assert result.severity == "high"


def test_scenario_b_from_the_issue_fails():
    """B: n_own=14, n_foreign=14, n_unclassifiable=14 -- muss ebenfalls failen."""
    store_scan = {"n_studies_in_store": 42, "n_own": 14, "n_foreign": 14, "n_unclassifiable": 14}
    result = inv.check_store_scan_coherence(store_scan, n_studies=14)
    assert result.passed is False


def test_passes_when_n_own_matches_studies_and_nothing_unclassifiable():
    store_scan = {"n_studies_in_store": 14, "n_own": 14, "n_foreign": 0, "n_unclassifiable": 0}
    result = inv.check_store_scan_coherence(store_scan, n_studies=14)
    assert result.passed is True


def test_fails_when_n_own_diverges_from_n_studies_even_without_unclassifiable():
    store_scan = {"n_studies_in_store": 14, "n_own": 12, "n_foreign": 2, "n_unclassifiable": 0}
    result = inv.check_store_scan_coherence(store_scan, n_studies=14)
    assert result.passed is False


def test_documented_purge_provenance_suppresses_the_unclassifiable_fail():
    store_scan = {"n_studies_in_store": 42, "n_own": 14, "n_foreign": 0, "n_unclassifiable": 28}
    result = inv.check_store_scan_coherence(
        store_scan, n_studies=14, purge_provenance_documented=True)
    assert result.passed is True


# --- report.py store scan: three-valued classification via a real WORK directory ------------------

def _write_proposal(work: Path, strategy: str, symbol: str, extra: dict | None = None) -> Path:
    payload = {"strategy": strategy, "symbol": symbol, "status": "REJECTED_ON_HOLDOUT"}
    if extra:
        payload.update(extra)
    path = work / f"proposal_{strategy}_{symbol}.json"
    path.write_text(json.dumps(payload), "utf-8")
    return path


def _wire_minimal_config(tmp_path, monkeypatch):
    """Dieselbe Vorgehensweise wie test_issue_742_sweep_report.py's ``wired_storage``-Fixture:
    ``resolve_storage`` direkt auf dem ``report``-Modul-Namensraum patchen (der einzige Ort, an
    dem der Name nach dem ``from run_optimization import resolve_storage`` noch aufgeloest wird),
    plus einen minimalen ``config_dir()`` mit leerem tournament.json (keine konfigurierten Gates
    -- ``check_config_key_registry`` bleibt dadurch trivial gruen)."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "tournament.json").write_text(json.dumps({}), "utf-8")
    monkeypatch.setattr(rpt, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(rpt, "resolve_storage",
                        lambda *, study_name, base_cfg=None: f"sqlite:///{tmp_path / 'nonexistent.db'}")


def test_store_scan_classifies_unloadable_study_as_unclassifiable_not_own(tmp_path, monkeypatch):
    """Kern-Regressionswaechter: ein Proposal-File, dessen Optuna-Study NICHT ladbar ist (kein
    entsprechender SQLite-Store), darf nicht als 'eigen' gezaehlt werden."""
    monkeypatch.setattr(rpt, "WORK", tmp_path)
    _wire_minimal_config(tmp_path, monkeypatch)
    # Proposal ohne ladbare Study (die gepatchte Storage-URL zeigt auf eine nicht existierende
    # SQLite-Datei) -- _load_study_for_proposal wird None liefern.
    _write_proposal(tmp_path, "GhostStrategy", "GHOST.ETORO")

    report = rpt._build_report(
        proposals=[], run_id="run_own_test", started_at_utc="2026-08-18T00:00:00Z",
        wallclock_s=1.0, cli_args={},
    )
    store_scan = report["store_scan"]
    assert store_scan["n_studies_in_store"] == 1
    assert store_scan["n_own"] == 0
    assert store_scan["n_foreign"] == 0
    assert store_scan["n_unclassifiable"] == 1


def test_store_scan_counts_already_seen_proposals_as_own(tmp_path, monkeypatch):
    monkeypatch.setattr(rpt, "WORK", tmp_path)
    _wire_minimal_config(tmp_path, monkeypatch)
    _write_proposal(tmp_path, "RealStrategy", "REAL.ETORO")
    proposals = [{
        "strategy": "RealStrategy", "symbol": "REAL.ETORO", "status": "REJECTED_ON_HOLDOUT",
        "is_rejection_detail_override": "REJECT_HOLDOUT_GATE", "symbol_params": {},
        "R_symbol": 0.0, "R_global": 0.0, "promotion_margin": 0.1, "holdout_passed": False,
        "trial_dir": None, "metrics_symbol": {}, "metrics_global": {},
    }]
    report = rpt._build_report(
        proposals=proposals, run_id="run_own_test", started_at_utc="2026-08-18T00:00:00Z",
        wallclock_s=1.0, cli_args={},
    )
    store_scan = report["store_scan"]
    assert store_scan["n_own"] == 1
    assert store_scan["n_unclassifiable"] == 0


def test_store_scan_coherence_check_is_wired_into_the_report():
    """Regressionswaechter (#984/#1138-Konvention): die neue Invariante muss tatsaechlich eine
    Aufrufstelle in report.py haben."""
    import inspect
    source = Path(rpt.__file__).read_text("utf-8")
    assert "_inv.check_store_scan_coherence(" in source
