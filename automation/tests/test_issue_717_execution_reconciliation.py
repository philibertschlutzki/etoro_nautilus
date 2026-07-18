"""Issue #717 (P0) — GR-04: execution-client-seitige Portfolio-Reconcile-Diff-Logik.

`extract_open_position_ids`/`find_phantom_positions` sind die reinen Entscheidungs-Funktionen
hinter `EToroExecutionClient._reconcile_positions_on_connect` (async I/O-Orchestrierung), separat
gehalten, damit sie ohne aiohttp-/Nautilus-Cython-Mocking direkt unit-testbar sind — dasselbe
Muster wie `backtest_runner.resolve_spread_bps`/`apply_fold_aggregation` in diesem Codebase.

Akzeptanzkriterium: eine bei eToro bereits geschlossene, in Nautilus offene Position wird
automatisch (ohne etoro_close_orphans.py) reconciled.
"""
from automation.adapters.etoro_execution import extract_open_position_ids, find_phantom_positions


# ── extract_open_position_ids ────────────────────────────────────────────────────────────────
def test_extract_open_position_ids_pascal_case():
    data = {"clientPortfolio": {"Positions": [{"PositionID": 111}, {"PositionID": 222}]}}
    assert extract_open_position_ids(data) == {"111", "222"}


def test_extract_open_position_ids_camel_case_fallback():
    data = {"positions": [{"positionId": "abc"}, {"positionID": "def"}]}
    assert extract_open_position_ids(data) == {"abc", "def"}


def test_extract_open_position_ids_no_client_portfolio_wrapper():
    data = {"Positions": [{"PositionID": 5}]}
    assert extract_open_position_ids(data) == {"5"}


def test_extract_open_position_ids_empty():
    assert extract_open_position_ids({}) == set()
    assert extract_open_position_ids({"positions": []}) == set()


def test_extract_open_position_ids_skips_entries_without_id():
    data = {"positions": [{"foo": "bar"}, {"PositionID": 1}]}
    assert extract_open_position_ids(data) == {"1"}


# ── find_phantom_positions ────────────────────────────────────────────────────────────────────
def test_phantom_detected_when_stored_id_not_in_etoro_open_set():
    nautilus_positions = [("TSLA.ETORO", "POS-1")]
    etoro_open_ids = {"POS-2", "POS-3"}
    assert find_phantom_positions(nautilus_positions, etoro_open_ids) == [("TSLA.ETORO", "POS-1")]


def test_no_phantom_when_still_open_at_etoro():
    nautilus_positions = [("TSLA.ETORO", "POS-1")]
    etoro_open_ids = {"POS-1", "POS-2"}
    assert find_phantom_positions(nautilus_positions, etoro_open_ids) == []


def test_unknown_stored_position_id_skipped_no_judgement():
    """Kein gespeicherter positionId-Anker (None) ⇒ kein Urteil (übersprungen, kein False-Positive)."""
    nautilus_positions = [("TSLA.ETORO", None)]
    assert find_phantom_positions(nautilus_positions, set()) == []


def test_multiple_positions_mixed_phantom_and_alive():
    nautilus_positions = [
        ("TSLA.ETORO", "POS-1"),   # phantom (nicht bei eToro)
        ("AAPL.ETORO", "POS-2"),   # noch offen
        ("CAT.ETORO", None),       # unbekannt -> übersprungen
    ]
    etoro_open_ids = {"POS-2"}
    assert find_phantom_positions(nautilus_positions, etoro_open_ids) == [("TSLA.ETORO", "POS-1")]


def test_empty_nautilus_positions_yields_no_phantoms():
    assert find_phantom_positions([], {"POS-1"}) == []
