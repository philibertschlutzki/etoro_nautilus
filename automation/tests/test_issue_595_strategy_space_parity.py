"""Issue #595 — ``--strategies all`` evaluierte nur 6 von 10 aktiven Strategien — lautlos.

``spaces.sample_params`` kennt 6 Strategien und wirft für alle anderen ``ValueError``, der in der
Fault-Isolation des Sweeps still verschluckt wurde ⇒ 40 % des aktiven Strategieraums nie evaluiert,
0 ERROR-Zeilen. Fixes: (1) FAIL-LOUD-Parität vor dem ersten Trial (jede aktive Strategie MUSS einen
Suchraum haben); (2) ``enumerate_tunable_pairs`` fängt den ValueError explizit und emittiert
``STRATEGY_NO_SEARCH_SPACE``; (3) ``sweep_completed`` trägt strategies_requested/enumerated/skipped[].
"""
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer import sweep


def _active_strategies():
    data = json.loads((Path("automation/config") / "strategies.json").read_text("utf-8"))
    return [s["strategy_class"] for s in data["strategies"]
            if s.get("active", True) is not False and s.get("strategy_class")]


def test_every_active_strategy_has_a_search_space():
    """Für JEDE aktive Strategie in strategies.json existiert ein Suchraum in spaces.py (sonst wäre
    die Config widersprüchlich — aktiv, aber untunbar)."""
    for strat in _active_strategies():
        assert sweep.strategy_has_search_space(strat), f"{strat} ist aktiv, hat aber keinen Suchraum"
    # Der Guard läuft ohne Fehler für die ausgelieferte Config durch (fail-loud NUR bei Widerspruch).
    sweep.assert_strategy_space_parity(_active_strategies())


def test_parity_fails_loud_for_spaceless_active_strategy():
    """Eine aktive, aber untunbare Strategie ⇒ ValueError (STRATEGY_NO_SEARCH_SPACE) VOR dem Sweep."""
    with pytest.raises(ValueError, match="STRATEGY_NO_SEARCH_SPACE"):
        sweep.assert_strategy_space_parity(["SmaCrossoverStrategy", "MadeUpNoSpaceStrategy"])


def test_disabled_strategies_have_no_space_but_are_not_active():
    """Die 4 in strategies.json deaktivierten Strategien haben (korrekt) keinen Suchraum — der
    Widerspruch 'aktiv-aber-untunbar' ist damit aufgelöst."""
    data = json.loads((Path("automation/config") / "strategies.json").read_text("utf-8"))
    disabled = [s["strategy_class"] for s in data["strategies"] if s.get("active") is False]
    assert disabled, "es sollte deaktivierte Strategien geben"
    for strat in disabled:
        # deaktiviert ⇒ nicht im aktiven Set ⇒ der Parity-Guard betrifft sie nicht.
        assert strat not in _active_strategies()


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.events = []
        self.records = []

    def emit(self, record):
        self.records.append(record)
        msg = record.getMessage()
        if "[JSON_EVENT]" in msg:
            try:
                self.events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))
            except Exception:
                pass


def _events_of(events, name):
    return [e for e in events if e.get("event_type") == name]


def test_sweep_completed_carries_strategy_coverage(monkeypatch, tmp_path, capsys):
    """sweep_completed enthält strategies_requested / strategies_enumerated / strategies_skipped[];
    eine übersprungene (nicht enumerierte) Strategie erzeugt eine WARNING-Zusammenfassung."""
    _GATE_CFG = {"walk_forward": {}, "gate1_buffer_days": 30,
                 "min_bars_per_param": 200, "min_oos_bars_per_fold": 500}
    # S1 wird enumeriert, S2 nicht (keine eligiblen Symbole).
    pairs = [("S1", "A.ETORO", "OK")]
    # Issue #799 — der Sweep-Fortschritts-Checkpoint schreibt nach WORK; isoliert halten.
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"p_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})

    handler = _CaptureHandler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        sweep.run_per_symbol_sweep(
            ["S1", "S2"], ["A.ETORO"], tier="all", n_jobs=1,
            optimize_symbol=lambda *a, **k: object(),  # Fake ⇒ using_real_optimize=False (Guard geskippt)
            confirm=lambda *a, **k: {"promote": False, "status": "X", "symbol_params": {}})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)

    evt = _events_of(handler.events, "sweep_completed")[0]
    assert evt["strategies_requested"] == 2
    assert evt["strategies_enumerated"] == 1
    skipped = {d["strategy"] for d in evt["strategies_skipped"]}
    assert skipped == {"S2"}
    # WARNING-Zusammenfassung am Sweep-Ende.
    assert any("NICHT enumeriert" in r.getMessage() for r in handler.records)
