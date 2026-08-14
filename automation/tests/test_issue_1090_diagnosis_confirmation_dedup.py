"""Issue #1090 (Katalog #923) — ``record_diagnosed_pair`` dedupliziert ``n_runs_confirmed`` auf der
zugrundeliegenden Study-Beobachtung (``study_fingerprint``), nicht auf dem Funktionsaufruf.

Root-Cause: unter mehreren gleichzeitigen Sweep-Prozessen auf demselben ``{WORK}``-Store (#1086)
wurde derselbe reale Trial-Datensatz einer Study mehrfach als eigenständige Bestätigung gezählt
(beobachtet: ``n_runs_confirmed=3`` bei EINEM tatsächlichen ASML-Lauf). Fix: ``study_fingerprint()``
(SHA256 über ``study_name``/``study_started_at_utc``/``n_trials_completed``) macht dieselbe
Beobachtung wiedererkennbar; ``record_diagnosed_pair`` erhöht den Zähler nur bei einem NEUEN
Fingerprint. Zusätzlich: ``diagnostic_writeback_enabled`` ist jetzt standardmäßig ``false``.
"""
import json

import pytest

from automation.optimizer import sweep_diagnostics
from automation.optimizer.sweep_diagnostics import (
    record_diagnosed_pair, load_diagnosed_pairs_cache, study_fingerprint,
    _read_diagnostic_writeback_enabled,
)


@pytest.fixture(autouse=True)
def _enable_diagnostic_writeback_by_default(request, monkeypatch):
    """Every test in this module exercises record_diagnosed_pair's dedup MECHANIC directly and
    therefore wants writeback enabled — except the tests that specifically assert the opposite
    (they opt out by name)."""
    if request.node.name in (
        "test_writeback_disabled_writes_no_file",
        "test_shipped_production_config_disables_writeback_by_default",
        "test_no_config_file_falls_back_to_legacy_true_default",
    ):
        return
    monkeypatch.setattr(sweep_diagnostics, "_read_diagnostic_writeback_enabled", lambda: True)


def _rec(**overrides) -> dict:
    base = {
        "strategy": "SqueezeBreakout", "symbol": "ASML.ETORO",
        "action": "deprioritized", "binding_cause": "signal_sparse",
    }
    base.update(overrides)
    return base


def test_study_fingerprint_deterministic_and_sensitive_to_each_component():
    fp1 = study_fingerprint("study_SqueezeBreakout_ASML_ETORO", "2026-08-13T15:45:10Z", 178)
    fp2 = study_fingerprint("study_SqueezeBreakout_ASML_ETORO", "2026-08-13T15:45:10Z", 178)
    assert fp1 == fp2 and fp1 is not None

    assert study_fingerprint("study_Other", "2026-08-13T15:45:10Z", 178) != fp1
    assert study_fingerprint("study_SqueezeBreakout_ASML_ETORO", "2026-08-13T16:00:00Z", 178) != fp1
    assert study_fingerprint("study_SqueezeBreakout_ASML_ETORO", "2026-08-13T15:45:10Z", 179) != fp1


def test_study_fingerprint_none_on_missing_component():
    assert study_fingerprint(None, "2026-08-13T15:45:10Z", 178) is None
    assert study_fingerprint("study_X", None, 178) is None
    assert study_fingerprint("study_X", "2026-08-13T15:45:10Z", None) is None


def test_three_concurrent_processes_reporting_the_same_study_confirm_once(tmp_path):
    """Akzeptanzkriterium #1090: drei gleichzeitige Reports über dieselbe Study-Menge erzeugen
    n_runs_confirmed = 1 (nicht 3), solange sie denselben study_fingerprint tragen."""
    fp = study_fingerprint("study_SqueezeBreakout_ASML_ETORO", "2026-08-13T15:45:10.000+00:00", 178)
    for run_id in ("run_tsla", "run_pltr", "run_nvda"):
        record_diagnosed_pair(
            _rec(study_fingerprint=fp), work_dir=tmp_path, run_id=run_id,
        )
    cache = load_diagnosed_pairs_cache(tmp_path)
    entry = cache[("SqueezeBreakout", "ASML.ETORO")]
    assert entry["n_runs_confirmed"] == 1
    assert entry["first_seen_run_id"] == "run_tsla"


def test_two_genuinely_different_studies_each_confirm(tmp_path):
    fp1 = study_fingerprint("study_X_A", "2026-08-13T15:45:10.000+00:00", 178)
    fp2 = study_fingerprint("study_X_A", "2026-08-14T09:00:00.000+00:00", 178)
    record_diagnosed_pair(_rec(study_fingerprint=fp1), work_dir=tmp_path, run_id="run_1")
    record_diagnosed_pair(_rec(study_fingerprint=fp2), work_dir=tmp_path, run_id="run_2")
    cache = load_diagnosed_pairs_cache(tmp_path)
    assert cache[("SqueezeBreakout", "ASML.ETORO")]["n_runs_confirmed"] == 2


def test_missing_fingerprint_falls_back_to_legacy_always_increment_behaviour(tmp_path):
    record_diagnosed_pair(_rec(), work_dir=tmp_path, run_id="run_1")
    record_diagnosed_pair(_rec(), work_dir=tmp_path, run_id="run_2")
    cache = load_diagnosed_pairs_cache(tmp_path)
    assert cache[("SqueezeBreakout", "ASML.ETORO")]["n_runs_confirmed"] == 2


def test_writeback_disabled_writes_no_file(tmp_path, monkeypatch):
    """Akzeptanzkriterium #1090: ein Sweep mit writeback_enabled=false schreibt keine Datei."""
    from automation.optimizer import trial_config
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)
    (tmp_path / "optimizer.json").write_text(
        json.dumps({"diagnostic_writeback_enabled": False}), "utf-8")
    assert _read_diagnostic_writeback_enabled() is False
    out_path = record_diagnosed_pair(_rec(), work_dir=tmp_path, run_id="run_1")
    assert not out_path.exists()


def test_shipped_production_config_disables_writeback_by_default():
    """Issue #1090 Fix Punkt 2 — die ausgelieferte automation/config/optimizer.json setzt
    diagnostic_writeback_enabled explizit auf false, bis #1066+#1086 an einem echten
    Mehrprozess-Lauf abgenommen sind."""
    from automation.optimizer.trial_config import config_dir
    cfg_path = config_dir() / "optimizer.json"
    data = json.loads(cfg_path.read_text("utf-8"))
    assert data["diagnostic_writeback_enabled"] is False


def test_no_config_file_falls_back_to_legacy_true_default(tmp_path, monkeypatch):
    """Der Python-Fallback (KEINE optimizer.json gefunden) bleibt True — nur die ausgelieferte
    Config-Datei traegt den neuen Sicherheits-Default, damit bestehende Unit-Tests ohne eigene
    Config-Fixture unveraendert funktionieren."""
    from automation.optimizer import trial_config
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)
    assert _read_diagnostic_writeback_enabled() is True


def test_writeback_enabled_true_in_config_still_writes(tmp_path, monkeypatch):
    from automation.optimizer import trial_config
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)
    (tmp_path / "optimizer.json").write_text(
        json.dumps({"diagnostic_writeback_enabled": True}), "utf-8")
    assert _read_diagnostic_writeback_enabled() is True
    record_diagnosed_pair(_rec(), work_dir=tmp_path, run_id="run_1")
    cache = load_diagnosed_pairs_cache(tmp_path)
    assert ("SqueezeBreakout", "ASML.ETORO") in cache
