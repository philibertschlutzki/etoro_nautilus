"""Issue #1103 (Katalog #936) — Champion-Closed-Loop bleibt bei 0 von 56 angewandten Rueckschrieben.

Symptom: alle CHAMPION_WRITEBACK-Ereignisse eines Laufs tragen ``applied=false``, dominant
``NO_ENTRY_FOR_PAIR`` (bis zu 51 von 56 Studies) trotz eines nicht-leeren Stores. Root-Cause
(Kausalkette, unveraendert seit #1064): das Coverage-Ledger kommt nie ueber Lauf 1 hinaus,
``corroboration_count`` bleibt 1, die Schwelle (Default 2) wird nie erreicht.

Fix Teil 1: ``champions.load_champion_entry_with_reason`` traegt jetzt ein drittes
Rueckgabeelement ``provenance`` ({looked_up_key, available_keys, available_keys_total}), NUR
gesetzt fuer STORE_EMPTY/NO_ENTRY_FOR_PAIR — macht den seit #1084 offenen Verdacht auf einen
Schluessel-Mismatch (Paar-Skopierung gegen einen abweichend benannten Store-Eintrag)
nachpruefbar. ``sweep._attempt_champion_writeback`` traegt sie im CHAMPION_WRITEBACK-Ereignis
als ``skipped_provenance``.

Fix Teil 2 (Abnahmetest, nach #1089s runs_completed_for_pair-Fix): zwei sequenzielle Laeufe
(store_champion mit verschiedenen run_id) auf demselben Paar erreichen jetzt tatsaechlich
corroboration_count=2 und einen angewandten Rueckschrieb (champion_corroboration_mode=Default
'either' braucht dafuer KEINEN Fensterfortschritt -- corroboration_count>=2 allein genuegt).
"""
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer import champions, sweep, trial_config


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


def _capture_champion_writeback_events(fn):
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
    return [e for e in handler.events if e.get("event_type") == "CHAMPION_WRITEBACK"]


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(champions, "CHAMPION_ROOT", tmp_path / "champions")
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)


class _FakeStudy:
    best_value = 1.0
    directions = ["maximize"]


def _promotion(**overrides):
    # Issue #817 — holdout_reject_detail muss in der Default-Allowlist
    # (_DEFAULT_ADMISSIBLE_HOLDOUT_REJECT_DETAILS) stehen, sonst ist der Kandidat bereits am
    # champion_is_admissible-Gate REJECTION_NOT_ALLOWLISTED, bevor Korroboration ueberhaupt
    # relevant wird -- REJECT_HOLDOUT_DSR_DROP ist die im Katalog referenzierte, real admissible
    # Ablehnungsursache (Holdout-Gate SELBST bestanden, nur an der Multiplizitaetskorrektur
    # gescheitert).
    base = {
        "promote": False, "status": "REJECTED_ON_HOLDOUT",
        "is_rejection_detail_override": "REJECT_HOLDOUT_DSR_DROP",
        "symbol_params": {"sma_period": 44}, "R_symbol": 0.5, "R_global": 0.1,
        "promotion_margin": 0.1, "holdout_passed": False, "trial_dir": "t1",
        "metrics_symbol": {}, "metrics_global": {},
    }
    base.update(overrides)
    return base


# ── Fix Teil 1: NO_ENTRY_FOR_PAIR/STORE_EMPTY tragen looked_up_key/available_keys ────────────────
def test_no_entry_for_pair_carries_looked_up_key_and_available_keys(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    champions._write_entry(
        tmp_path / "champions" / "champion_Rsi2ReversionStrategy_TSLA_ETORO.json",
        {"strategy": "Rsi2ReversionStrategy", "symbol": "TSLA.ETORO"})
    entry, reason, provenance = champions.load_champion_entry_with_reason(
        "DonchianRegimeBreakoutStrategy", "TSLA.ETORO", opt_data=OPT_DATA)
    assert reason == "NO_ENTRY_FOR_PAIR"
    assert provenance["looked_up_key"] == "champion_DonchianRegimeBreakoutStrategy_TSLA_ETORO.json"
    assert provenance["available_keys"] == ["champion_Rsi2ReversionStrategy_TSLA_ETORO.json"]
    assert provenance["available_keys_total"] == 1


def test_store_empty_carries_looked_up_key_and_empty_available_keys(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    entry, reason, provenance = champions.load_champion_entry_with_reason(
        "DonchianRegimeBreakoutStrategy", "TSLA.ETORO", opt_data=OPT_DATA)
    assert reason == "STORE_EMPTY"
    assert provenance["looked_up_key"] == "champion_DonchianRegimeBreakoutStrategy_TSLA_ETORO.json"
    assert provenance["available_keys"] == []


def test_admissible_entry_carries_no_provenance(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    promotion = _promotion(status="READY_FOR_PR", is_rejection_detail_override=None)
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    entry, reason, provenance = champions.load_champion_entry_with_reason(
        "SmaCrossoverStrategy", "TSLA.ETORO", opt_data=OPT_DATA)
    assert entry is not None
    assert reason is None
    assert provenance is None


def test_provenance_truncates_available_keys_but_keeps_the_full_total(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    for i in range(champions._NO_ENTRY_PROVENANCE_MAX_KEYS + 5):
        champions._write_entry(
            tmp_path / "champions" / f"champion_Strat{i}_TSLA_ETORO.json",
            {"strategy": f"Strat{i}", "symbol": "TSLA.ETORO"})
    _, reason, provenance = champions.load_champion_entry_with_reason(
        "DonchianRegimeBreakoutStrategy", "TSLA.ETORO", opt_data=OPT_DATA)
    assert reason == "NO_ENTRY_FOR_PAIR"
    assert len(provenance["available_keys"]) == champions._NO_ENTRY_PROVENANCE_MAX_KEYS
    assert provenance["available_keys_total"] == champions._NO_ENTRY_PROVENANCE_MAX_KEYS + 5


# ── sweep._attempt_champion_writeback surfaces skipped_provenance ───────────────────────────────
def test_champion_writeback_event_carries_skipped_provenance_on_no_entry(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    events = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback("SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA))
    assert len(events) == 1
    assert events[0]["skipped_reason"] == "STORE_EMPTY"
    assert events[0]["skipped_provenance"]["looked_up_key"] == (
        "champion_SmaCrossoverStrategy_TSLA_ETORO.json")
    assert events[0]["skipped_provenance"]["available_keys"] == []


def test_champion_writeback_event_provenance_is_none_when_applied(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    promotion = _promotion(status="READY_FOR_PR", is_rejection_detail_override=None)
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    events = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback("SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA))
    assert events[0]["applied"] is True
    assert events[0]["skipped_provenance"] is None


# ── Fix Teil 2: Abnahmetest -- zwei sequenzielle Laeufe schliessen den Closed Loop ───────────────
def test_two_sequential_runs_reach_corroboration_count_2_and_apply(tmp_path, monkeypatch):
    """Der im Katalog beschriebene Abnahmetest: nach #1089 (runs_completed_for_pair) ist die
    Korroborationsschwelle erstmals erreichbar. Lauf 1 stellt corroboration_count=1 her (kein
    Rueckschrieb); Lauf 2 (andere run_id, derselbe Parametervektor -- 'unabhaengige' Bestaetigung)
    hebt sie auf 2 -- unter dem Default champion_corroboration_mode='either' genuegt das, KEIN
    Fensterfortschritt noetig."""
    _isolate(monkeypatch, tmp_path)
    promotion = _promotion()

    # Lauf 1.
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    events1 = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback("SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA))
    assert events1[0]["corroboration_count"] == 1
    assert events1[0]["applied"] is False
    assert events1[0]["skipped_reason"] == "NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED"

    # Lauf 2: andere run_id, derselbe Parametervektor -> region-gleich, corroboration_count bumped.
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run2")
    events2 = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback("SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA))
    assert events2[0]["corroboration_count"] == 2
    assert events2[0]["applied"] is True
    assert events2[0]["skipped_reason"] is None

    seeds = json.loads((tmp_path / "strategy_symbol_seeds.json").read_text("utf-8"))
    assert seeds["seeds"]["SmaCrossoverStrategy"]["TSLA.ETORO"] == {"sma_period": 44}


def test_repeated_run_id_does_not_double_count_corroboration(tmp_path, monkeypatch):
    """Regressionsschutz (#821): zwei store_champion-Aufrufe MIT DERSELBEN run_id (z. B. ein
    Confirm-Retry innerhalb desselben Sweeps) duerfen corroboration_count nicht zweimal erhoehen."""
    _isolate(monkeypatch, tmp_path)
    promotion = _promotion()
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    events = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback("SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA))
    assert events[0]["corroboration_count"] == 1
    assert events[0]["applied"] is False
