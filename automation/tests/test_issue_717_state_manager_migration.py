"""Issue #717 (P0) — GR-04: _StateManager-Erweiterung auf {positionId, entry_ns, entry_bar_seq}.

Migrationssicher: alte str-Werte (COID -> positionId) werden als {positionId, entry_ns: None,
entry_bar_seq: None} gelesen. get()/get_all() bleiben abwärtskompatibel (liefern nur positionId).
"""
import asyncio
import json

import pytest

from automation.adapters.etoro_state_manager import _StateManager


def _run(coro):
    return asyncio.run(coro)


# ── Migrationssicherheit: alte str-Werte ─────────────────────────────────────────────────────
def test_legacy_str_values_load_without_error(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"COID-1": "POS-1", "COID-2": "POS-2"}), encoding="utf-8")

    sm = _StateManager(str(state_path))
    _run(sm.load())

    assert _run(sm.get("COID-1")) == "POS-1"
    entry = _run(sm.get_entry("COID-1"))
    assert entry == {"positionId": "POS-1", "entry_ns": None, "entry_bar_seq": None}


def test_legacy_state_file_reverse_lookup_caches_rebuilt(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"COID-1": "POS-1"}), encoding="utf-8")

    sm = _StateManager(str(state_path))
    _run(sm.load())

    assert sm._venue_id_to_coid.get("POS-1") == "COID-1"


def test_missing_state_file_starts_empty(tmp_path):
    sm = _StateManager(str(tmp_path / "nonexistent.json"))
    _run(sm.load())
    assert sm.get_all() == {}
    assert sm.get_all_entries() == {}


# ── Neues Format: {positionId, entry_ns, entry_bar_seq} ──────────────────────────────────────
def test_set_and_get_entry_full_roundtrip(tmp_path):
    sm = _StateManager(str(tmp_path / "state.json"))
    _run(sm.load())
    _run(sm.set("COID-1", "POS-1", entry_ns=1_000_000_000, entry_bar_seq=999_000_000))

    entry = _run(sm.get_entry("COID-1"))
    assert entry == {"positionId": "POS-1", "entry_ns": 1_000_000_000, "entry_bar_seq": 999_000_000}
    assert _run(sm.get("COID-1")) == "POS-1"  # abwärtskompatibel


def test_get_all_stays_backward_compatible_string_only(tmp_path):
    sm = _StateManager(str(tmp_path / "state.json"))
    _run(sm.load())
    _run(sm.set("COID-1", "POS-1", entry_ns=1000, entry_bar_seq=900))
    _run(sm.set("COID-2", "POS-2"))

    all_ids = sm.get_all()
    assert all_ids == {"COID-1": "POS-1", "COID-2": "POS-2"}
    assert all(isinstance(v, str) for v in all_ids.values())


def test_get_all_entries_exposes_full_state(tmp_path):
    sm = _StateManager(str(tmp_path / "state.json"))
    _run(sm.load())
    _run(sm.set("COID-1", "POS-1", entry_ns=1000, entry_bar_seq=900))

    entries = sm.get_all_entries()
    assert entries["COID-1"] == {"positionId": "POS-1", "entry_ns": 1000, "entry_bar_seq": 900}


# ── entry_ns/entry_bar_seq sind "sticky" (erste Stempelung bleibt bestehen) ──────────────────
def test_entry_ns_is_sticky_not_overwritten_by_later_set(tmp_path):
    sm = _StateManager(str(tmp_path / "state.json"))
    _run(sm.load())
    _run(sm.set("COID-1", "TOKEN-1", entry_ns=1000, entry_bar_seq=900))
    # Ein späterer set() (z. B. reine venue_id-Auffrischung von token -> echte positionId) OHNE
    # neue entry_ns/entry_bar_seq darf den bereits gestempelten Entry-Anker nicht loeschen.
    _run(sm.set("COID-1", "POS-REAL-1"))

    entry = _run(sm.get_entry("COID-1"))
    assert entry["positionId"] == "POS-REAL-1"
    assert entry["entry_ns"] == 1000
    assert entry["entry_bar_seq"] == 900


def test_entry_ns_unset_until_explicitly_stamped(tmp_path):
    sm = _StateManager(str(tmp_path / "state.json"))
    _run(sm.load())
    _run(sm.set("COID-1", "TOKEN-1"))  # Submit-Zeit, kein Fill-Anker

    entry = _run(sm.get_entry("COID-1"))
    assert entry["entry_ns"] is None
    assert entry["entry_bar_seq"] is None


def test_persist_and_reload_roundtrip_preserves_new_format(tmp_path):
    state_path = tmp_path / "state.json"
    sm = _StateManager(str(state_path))
    _run(sm.load())
    _run(sm.set("COID-1", "POS-1", entry_ns=1234, entry_bar_seq=1200))
    _run(sm._persist_now())

    sm2 = _StateManager(str(state_path))
    _run(sm2.load())
    assert _run(sm2.get_entry("COID-1")) == {"positionId": "POS-1", "entry_ns": 1234, "entry_bar_seq": 1200}


# ── delete() korrekt für das neue dict-Format ─────────────────────────────────────────────────
def test_delete_removes_entry_and_reverse_lookup(tmp_path):
    sm = _StateManager(str(tmp_path / "state.json"))
    _run(sm.load())
    _run(sm.set("COID-1", "POS-1", entry_ns=1000, entry_bar_seq=900))
    _run(sm.delete("COID-1"))

    assert _run(sm.get("COID-1")) is None
    assert _run(sm.get_entry("COID-1")) is None
    assert "POS-1" not in sm._venue_id_to_coid
