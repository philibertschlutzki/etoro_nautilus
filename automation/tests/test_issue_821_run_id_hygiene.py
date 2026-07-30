"""Issue #821 (P2, Katalog #817-#821, GitHub-Issue #749) — Store-Hygiene: ``run_id`` war ein
Schreibzeitstempel, versionsfremde Einträge wurden nie entfernt.

Root-Cause (a): ``_run_id()`` mintete bei JEDEM ``_build_entry_from_promotion``-Aufruf einen
frischen Zeitstempel — ``run_id`` war damit kein Lauf-Identifikator, sondern der Schreibzeitpunkt
des einzelnen Eintrags. Zwei Confirm-Durchläufe DESSELBEN Sweeps hätten fälschlich als
Korroboration gezählt.

Root-Cause (b): ``store_champion`` setzte bei einem Versions-Mismatch nur die lokale Variable
``existing = None``. War der neue Kandidat selbst unzulässig, blieb die alte, versionsfremde Datei
unangetastet auf der Platte liegen — der Store wuchs monoton um tote Einträge.

Fix: ``run_id`` ist ein Pflichtparameter (siehe test_issue_702's ``test_store_champion_requires_
run_id``/``test_store_champion_same_run_id_does_not_double_count``/``test_store_champion_
corroborates_same_region`` für die Korroborationszählung); ein ``params_schema_version``-Mismatch,
dessen Nachfolge-Kandidat selbst inadmissibel ist, quarantäniert den Alt-Eintrag nach ``_stale/``
statt ihn liegen zu lassen.
"""
import json
import logging
from pathlib import Path

from automation.optimizer import champions, trial_config


CFG_DIR = Path("automation/config")
_CURRENT_REWARD_SEMANTICS_VERSION = json.loads(
    (CFG_DIR / "optimizer.json").read_text("utf-8")
)["reward_semantics_version"]
OPT_DATA = {
    "reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
    "champion_min_R_symbol": 0.0,
    "champion_min_tuning_edge": 0.0,
    "champion_promote_after_runs": 2,
    "champion_demote_after_runs": 2,
    "champion_min_advance_days": 30,
    "champion_region_eps": 0.10,
    "champion_enabled": True,
}


class _FakeStudy:
    best_value = 1.0
    directions = ["maximize"]


def _promotion(*, symbol_params, R_symbol, R_global=0.0, status="READY_FOR_PR",
              is_rejection_detail_override=None) -> dict:
    return {
        "promote": status == "READY_FOR_PR", "status": status,
        "is_rejection_detail_override": is_rejection_detail_override,
        "symbol_params": symbol_params, "R_symbol": R_symbol, "R_global": R_global,
        "promotion_margin": 0.1, "holdout_passed": status != "REJECTED_ON_HOLDOUT",
        "trial_dir": "t1", "metrics_symbol": {}, "metrics_global": {},
    }


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)


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


# ── run_id ist ein Lauf-, kein Schreibzeitstempel (Wiring bereits in test_702 abgedeckt) ────────
def test_store_champion_run_id_persisted_verbatim(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    promo = _promotion(symbol_params={"sma_period": 20}, R_symbol=0.5)
    path = champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promo,
                                    catalog_newest_ns=1000, opt_data=OPT_DATA,
                                    run_id="3836af54_20260728T174020944733")
    stored = json.loads(path.read_text("utf-8"))
    assert stored["provenance"]["run_id"] == "3836af54_20260728T174020944733"
    assert stored["lifecycle"]["last_seen_run"] == "3836af54_20260728T174020944733"


# ── Quarantäne (Root-Cause b) ────────────────────────────────────────────────────────────────
def test_schema_mismatch_with_inadmissible_candidate_quarantines_old_entry(tmp_path, monkeypatch):
    """Ein params_schema_version-fremder Alt-Eintrag, dessen Nachfolge-Kandidat selbst inadmissibel
    ist (hier: unter dem Qualitaets-Floor), wird nach _stale/ verschoben statt am aktiven Store-Pfad
    liegen zu bleiben."""
    _isolate(monkeypatch, tmp_path)
    path = champions._champion_path("SmaCrossoverStrategy", "TSLA.ETORO")
    old_entry = {
        "strategy": "SmaCrossoverStrategy", "symbol": "TSLA.ETORO", "tier": "all",
        "params": {"sma_period": 20}, "quality": {"R_symbol": 0.5, "R_global": 0.0, "reward": 1.0},
        "provenance": {"run_id": "run0", "holdout_reject_detail": None,
                       "status_at_store": "READY_FOR_PR", "holdout_binding_gate": None,
                       "holdout_gate_deltas": {}},
        "integrity": {"reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
                      "data_snapshot_sha256": "abc", "params_schema_version": "OLD_SCHEMA_SIGNATURE",
                      "catalog_newest_ns": 1000},
        "lifecycle": {"first_seen_run": "run0", "first_seen_catalog_newest_ns": 1000,
                      "corroboration_count": 3, "last_seen_run": "run0", "last_R_symbol": 0.5,
                      "degrade_streak": 0, "writeback_applied": False,
                      "snapshot_rollover_count": 0, "semantics_migrated_from": None},
    }
    champions._write_entry(path, old_entry)
    monkeypatch.setattr(champions, "_params_schema_version", lambda strategy: "NEW_SCHEMA_SIGNATURE")

    handler = _CaptureHandler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        promo = _promotion(symbol_params={"sma_period": 21}, R_symbol=-0.5)  # unter dem Floor
        result = champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promo,
                                          catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run1")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    assert result is None
    assert not path.exists()
    stale_files = list((tmp_path / "champions" / "_stale").glob("*.json"))
    assert len(stale_files) == 1
    quarantined = json.loads(stale_files[0].read_text("utf-8"))
    assert quarantined["lifecycle"]["corroboration_count"] == 3  # unveraendert archiviert
    quarantine_events = [e for e in handler.events if e.get("event_type") == "CHAMPION_STALE_QUARANTINED"]
    assert len(quarantine_events) == 1
    assert quarantine_events[0]["strategy"] == "SmaCrossoverStrategy"


def test_schema_mismatch_with_admissible_candidate_replaces_without_quarantine(tmp_path, monkeypatch):
    """Ist der neue Kandidat trotz Schema-Mismatch admissibel, wird der Store-Pfad einfach
    ueberschrieben -- KEINE Quarantaene noetig (der aktive Store enthaelt weiterhin genau einen
    bewertbaren Eintrag)."""
    _isolate(monkeypatch, tmp_path)
    path = champions._champion_path("SmaCrossoverStrategy", "TSLA.ETORO")
    old_entry = {
        "strategy": "SmaCrossoverStrategy", "symbol": "TSLA.ETORO", "tier": "all",
        "params": {"sma_period": 20}, "quality": {"R_symbol": 0.5, "R_global": 0.0, "reward": 1.0},
        "provenance": {"run_id": "run0", "holdout_reject_detail": None,
                       "status_at_store": "READY_FOR_PR", "holdout_binding_gate": None,
                       "holdout_gate_deltas": {}},
        "integrity": {"reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
                      "data_snapshot_sha256": "abc", "params_schema_version": "OLD_SCHEMA_SIGNATURE",
                      "catalog_newest_ns": 1000},
        "lifecycle": {"first_seen_run": "run0", "first_seen_catalog_newest_ns": 1000,
                      "corroboration_count": 3, "last_seen_run": "run0", "last_R_symbol": 0.5,
                      "degrade_streak": 0, "writeback_applied": False,
                      "snapshot_rollover_count": 0, "semantics_migrated_from": None},
    }
    champions._write_entry(path, old_entry)
    monkeypatch.setattr(champions, "_params_schema_version", lambda strategy: "NEW_SCHEMA_SIGNATURE")

    promo = _promotion(symbol_params={"sma_period": 21}, R_symbol=0.8)  # admissibel
    result = champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promo,
                                      catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run1")
    assert result is not None
    stored = json.loads(path.read_text("utf-8"))
    assert stored["params"]["sma_period"] == 21
    assert stored["lifecycle"]["corroboration_count"] == 1  # frischer Lifecycle (Schema-Bruch)
    assert not (tmp_path / "champions" / "_stale").exists()


def test_no_quarantine_when_no_prior_entry_exists(tmp_path, monkeypatch):
    """Kein Vorgaenger -> auch bei einem inadmissiblen Kandidaten gibt es nichts zu quarantaenieren."""
    _isolate(monkeypatch, tmp_path)
    promo = _promotion(symbol_params={"sma_period": 21}, R_symbol=-0.5)
    result = champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promo,
                                      catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run1")
    assert result is None
    assert not (tmp_path / "champions" / "_stale").exists()
