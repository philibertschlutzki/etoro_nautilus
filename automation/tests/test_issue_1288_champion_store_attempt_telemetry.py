"""Issue #1288 (GH #1161, Katalog #1272-1297, P1) — Champion-Store: 14 Versuche, 0 Writebacks,
widersprüchliche Ursache.

Symptom. In allen vier Läufen ``attempts=14``, ``written_back=0``, ``entry_count=0``. Lauf
``3e792e68`` meldet ``skipped_by_reason={'STORE_PATH_MISSING': 14}`` bei gleichzeitig
``store_found=True`` UND gültigem ``store_path``; die drei Folgeläufe melden ``STORE_EMPTY``.
``check_champion_writeback_reachability``/``check_champion_seed_coverage`` FAILen 4/4.

Root-Cause. (a) ``STORE_PATH_MISSING`` wurde bei ``store_found=True`` gemeldet — die Bindung an
``store_found`` (bloße Verzeichnis-Existenz) greift auf dem Pfad des ersten Laufs nicht: JEDER
Champion-Store-Zugriff (auch ein reiner Lookup) legt das Verzeichnis als Seiteneffekt an
(``_champions_dir``s ``mkdir(exist_ok=True)``), lange bevor ein einziger Eintrag je gespeichert
wurde. (b) Es wird nie geschrieben, weil kein Kandidat die Admissibilitäts-Bedingungen
(``champion_min_R_symbol``/``champion_min_tuning_edge``/``champion_admissible_reject_details``)
erreicht — ``store_champion`` verwarf den granularen Ablehnungscode bislang stillschweigend.

Fix.
1. ``STORE_PATH_MISSING`` nur, wenn ``entry_count == 0`` (seiteneffektfrei) — nicht ``store_found``.
   Erstanlage (``entry_count > 0`` nach vorherigem ``STORE_EMPTY``) ⇒ ``STORE_CREATED_THIS_RUN``.
2. ``champions.champion_admissibility_skip_detail`` + ``CHAMPION_STORE_ATTEMPT``-Ereignis: je
   Speicherversuch der spezifische Blocker MIT Ist-/Sollwert.
3. ``check_champion_writeback_reachability`` FAILt ``high``, wenn ein Versuch keinen aufloesbaren
   ``skip_detail['reason']`` traegt (``'UNKNOWN'``).
"""
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer import champions, invariants as inv, sweep, trial_config

CFG_DIR = Path("automation/config")
_CURRENT_REWARD_SEMANTICS_VERSION = json.loads(
    (CFG_DIR / "optimizer.json").read_text("utf-8")
)["reward_semantics_version"]
OPT_DATA = {
    "reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
    "champion_min_R_symbol": 0.5,
    "champion_min_tuning_edge": 0.1,
    "champion_promote_after_runs": 2,
    "champion_demote_after_runs": 2,
    "champion_min_advance_days": 30,
    "champion_region_eps": 0.10,
    "champion_enabled": True,
}


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.events = []

    def emit(self, record):
        msg = record.getMessage()
        if "[JSON_EVENT]" in msg:
            try:
                self.events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))
            except Exception:
                pass


def _capture_events(fn, event_type):
    handler = _CaptureHandler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        fn()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    return [e for e in handler.events if e.get("event_type") == event_type]


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(champions, "CHAMPION_ROOT", tmp_path / "champions")
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)


class _FakeStudy:
    best_value = 1.0
    directions = ["maximize"]


def _promotion(*, r_symbol, r_global=0.0, status="READY_FOR_PR"):
    return {
        "promote": True, "status": status, "is_rejection_detail_override": None,
        "symbol_params": {"sma_period": 33}, "R_symbol": r_symbol, "R_global": r_global,
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "trial_0001",
        "metrics_symbol": {}, "metrics_global": {},
    }


# ---------------------------------------------------------------------------------------------
# Fix Punkt 1 — STORE_PATH_MISSING vs. STORE_CREATED_THIS_RUN (entry_count, nicht store_found)
# ---------------------------------------------------------------------------------------------

def test_store_path_missing_reflects_entry_count_not_bare_directory_existence(tmp_path, monkeypatch):
    """Kernreproduktion: ein reiner Lookup legt das Verzeichnis via mkdir-Seiteneffekt an, OHNE
    dass je ein Eintrag existierte -- store_found waere danach True, aber entry_count bleibt 0."""
    _isolate(monkeypatch, tmp_path)
    events = _capture_events(
        lambda: sweep._attempt_champion_writeback(
            "SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA, store_found_at_run_start=False),
        "CHAMPION_WRITEBACK")
    assert events[0]["skipped_reason"] == "STORE_PATH_MISSING"
    # Das Verzeichnis existiert jetzt (mkdir-Seiteneffekt), aber ohne einen einzigen Eintrag.
    assert (tmp_path / "champions").is_dir()
    assert champions.store_status().get("entry_count") == 0


def test_store_created_this_run_once_an_entry_actually_exists(tmp_path, monkeypatch):
    """TOCTOU-Nachbildung (der eigentliche Multi-Worker-Fall, den #1288 abdeckt): zum Zeitpunkt
    DIESES Lookups meldet die LIVE-Glob-Pruefung noch 'kein Eintrag' (ein ANDERER paralleler
    Worker hat gerade erst geschrieben, race), aber ``store_status().entry_count`` (seiteneffektfrei
    gelesen NACH dem Lookup) sieht bereits den neuen Eintrag -- STORE_CREATED_THIS_RUN statt
    STORE_PATH_MISSING, weil der Store nachweislich nicht mehr durchgehend leer ist."""
    _isolate(monkeypatch, tmp_path)
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO",
                             _promotion(r_symbol=0.9), catalog_newest_ns=1000,
                             opt_data=OPT_DATA, run_id="run1")
    assert champions.store_status().get("entry_count") == 1
    monkeypatch.setattr(champions, "_champion_store_has_any_entry", lambda: False)
    events = _capture_events(
        lambda: sweep._attempt_champion_writeback(
            "SmaCrossoverStrategy", "NVDA.ETORO", OPT_DATA, store_found_at_run_start=False),
        "CHAMPION_WRITEBACK")
    assert events[0]["skipped_reason"] == "STORE_CREATED_THIS_RUN"


# ---------------------------------------------------------------------------------------------
# Fix Punkt 2 — champions.champion_admissibility_skip_detail + CHAMPION_STORE_ATTEMPT
# ---------------------------------------------------------------------------------------------

def test_below_quality_floor_carries_actual_and_expected_r_symbol(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    events = _capture_events(
        lambda: champions.store_champion(
            _FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO",
            _promotion(r_symbol=0.1), catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1"),
        "CHAMPION_STORE_ATTEMPT")
    assert len(events) == 1
    assert events[0]["stored"] is False
    detail = events[0]["skip_detail"]
    assert detail["reason"] == "BELOW_QUALITY_FLOOR"
    assert detail["criterion"] == "champion_min_R_symbol"
    assert detail["actual_R_symbol"] == pytest.approx(0.1)
    assert detail["expected_min_R_symbol"] == pytest.approx(0.5)


def test_no_tuning_edge_carries_actual_and_expected_edge(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    events = _capture_events(
        lambda: champions.store_champion(
            _FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO",
            _promotion(r_symbol=0.55, r_global=0.5), catalog_newest_ns=1000,
            opt_data=OPT_DATA, run_id="run1"),
        "CHAMPION_STORE_ATTEMPT")
    detail = events[0]["skip_detail"]
    assert detail["reason"] == "NO_TUNING_EDGE"
    assert detail["criterion"] == "champion_min_tuning_edge"
    assert detail["actual_R_symbol"] == pytest.approx(0.55)
    assert detail["actual_R_global"] == pytest.approx(0.5)
    assert detail["expected_min_tuning_edge"] == pytest.approx(0.1)


def test_rejection_not_allowlisted_carries_actual_detail_and_allowlist(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    promotion = _promotion(r_symbol=0.9, status="REJECTED_ON_HOLDOUT")
    promotion["is_rejection_detail_override"] = "REJECT_SOME_UNLISTED_CODE"
    events = _capture_events(
        lambda: champions.store_champion(
            _FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
            catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1"),
        "CHAMPION_STORE_ATTEMPT")
    detail = events[0]["skip_detail"]
    assert detail["reason"] == "REJECTION_NOT_ALLOWLISTED"
    assert detail["criterion"] == "champion_admissible_reject_details"
    assert detail["actual_holdout_reject_detail"] == "REJECT_SOME_UNLISTED_CODE"
    assert isinstance(detail["expected_allowlist"], list)


def test_successful_store_emits_stored_true_without_skip_detail(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    events = _capture_events(
        lambda: champions.store_champion(
            _FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO",
            _promotion(r_symbol=0.9), catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1"),
        "CHAMPION_STORE_ATTEMPT")
    assert len(events) == 1
    assert events[0]["stored"] is True


def test_champion_disabled_falls_back_to_bare_reason_with_no_numeric_criterion():
    detail = champions.champion_admissibility_skip_detail({}, {"champion_enabled": False},
                                                           "CHAMPION_DISABLED")
    assert detail == {"reason": "CHAMPION_DISABLED"}


def test_none_reason_maps_to_unknown():
    detail = champions.champion_admissibility_skip_detail({}, {}, None)
    assert detail == {"reason": "UNKNOWN"}


# ---------------------------------------------------------------------------------------------
# Fix Punkt 3 — invariants.check_champion_writeback_reachability FAILt high bei UNKNOWN
# ---------------------------------------------------------------------------------------------

def test_all_attempts_with_resolvable_reason_passes_the_new_dimension():
    result = inv.check_champion_writeback_reachability({
        "stored": 0, "written_back": 0, "attempts": 14,
        "skipped_by_reason": {"STORE_PATH_MISSING": 14},
        "champion_store_attempts": [
            {"strategy": "S", "symbol": f"SYM{i}.ETORO", "stored": False,
             "skip_detail": {"reason": "BELOW_QUALITY_FLOOR"}}
            for i in range(14)
        ],
    })
    # written_back == 0 laesst den bestehenden Befund weiterhin FAILen (medium) -- aber KEIN
    # unresolved_champion_store_attempts-Eintrag.
    assert result.actual["unresolved_champion_store_attempts"] is None
    assert result.severity == "medium"


def test_unknown_skip_detail_reason_fails_high():
    result = inv.check_champion_writeback_reachability({
        "stored": 0, "written_back": 0, "attempts": 14,
        "skipped_by_reason": {"STORE_PATH_MISSING": 14},
        "champion_store_attempts": [
            {"strategy": "S", "symbol": "TSLA.ETORO", "stored": False, "skip_detail": {"reason": "UNKNOWN"}},
        ],
    })
    assert result.passed is False
    assert result.severity == "high"
    assert "S/TSLA.ETORO" in result.actual["unresolved_champion_store_attempts"]


def test_missing_skip_detail_dict_entirely_counts_as_unresolved():
    result = inv.check_champion_writeback_reachability({
        "stored": 0, "written_back": 0, "attempts": 1,
        "skipped_by_reason": {"STORE_PATH_MISSING": 1},
        "champion_store_attempts": [
            {"strategy": "S", "symbol": "TSLA.ETORO", "stored": False, "skip_detail": None},
        ],
    })
    assert result.passed is False
    assert result.severity == "high"


def test_stored_true_attempts_are_never_flagged_as_unresolved():
    result = inv.check_champion_writeback_reachability({
        "stored": 1, "written_back": 1, "attempts": 1,
        "skipped_by_reason": {},
        "champion_store_attempts": [
            {"strategy": "S", "symbol": "TSLA.ETORO", "stored": True, "skip_detail": None},
        ],
    })
    assert result.passed is True
    assert result.severity == "medium"


def test_missing_champion_store_attempts_field_is_backward_compatible():
    """Legacy-Aufrufer/Report vor #1288 ohne das neue Feld -- bit-identisches Pre-#1288-Verhalten."""
    result = inv.check_champion_writeback_reachability({
        "stored": 1, "written_back": 1, "attempts": 1, "skipped_by_reason": {},
    })
    assert result.passed is True
    assert result.severity == "medium"
