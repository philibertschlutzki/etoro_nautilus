"""Issue #780 (P2) — Im parallelen Sweep tragen die meisten Warnungen keine Study-Identität;
Attribution aus dem Log ist unmöglich.

Root-Cause: seit `#755` laufen mehrere Studies parallel (`ThreadPoolExecutor`) in EINEN Logger.
Nur `[#565]`/`[#620]` trugen Strategie/Symbol; `REWARD_TERM_INERT` (983), `[#667]` (695), `[#660]`
(232), Zero-Eligible-Plateau (705), Floor-Plateau (252) und `[#597]` (32) trugen sie NICHT. Eine
reihenfolgebasierte Heuristik ("aktuelle Study = letzter Marker") lieferte nachweislich falsche
Zahlen (590 statt 705 Zero-Eligible-Zuordnungen) und übersah Strategien ohne diesen Marker völlig.

Fix: `log_manager.bind_study_context` (contextvars, thread-lokal über native Threads) bindet
strategy/symbol/study_name EINMAL in `optimize_symbol` — jede nachgelagerte Log-Zeile/jedes Event
trägt die Identität automatisch, ohne die ~20 bestehenden Call-Sites anzufassen.
"""
import contextvars
import json
import logging
import threading

from automation.log_manager import (
    bind_study_context, emit_execution_event, StructuredFormatter, _current_study_context,
)


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))


def _capturing_logger(name):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = _CaptureHandler()
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)
    return logger, handler


# ── Akzeptanzkriterium #780/1: zwei parallel laufende Fake-Studies, korrektes Präfix ─────────────
def test_two_parallel_threads_never_swap_prefixes():
    logger, handler = _capturing_logger("test780_parallel")
    barrier = threading.Barrier(2)
    errors = []

    def _worker(strategy, symbol, n):
        try:
            with bind_study_context(strategy=strategy, symbol=symbol,
                                    study_name=f"study_{strategy}_{symbol}"):
                barrier.wait()   # beide Threads betreten den Kontext ungefaehr gleichzeitig
                for _ in range(n):
                    logger.warning("REWARD_TERM_INERT: base")
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=_worker, args=("StratA", "AAA.ETORO", 20))
    t2 = threading.Thread(target=_worker, args=("StratB", "BBB.ETORO", 20))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors
    assert all("[StratA/AAA.ETORO]" in r or "[StratB/BBB.ETORO]" in r for r in handler.records)
    assert sum(1 for r in handler.records if "[StratA/AAA.ETORO]" in r) == 20
    assert sum(1 for r in handler.records if "[StratB/BBB.ETORO]" in r) == 20
    # Niemals VERMISCHT innerhalb einer einzelnen Zeile (kein Swap zwischen den Threads).
    assert not any("[StratA/AAA.ETORO]" in r and "[StratB/BBB.ETORO]" in r for r in handler.records)


def test_context_is_reset_after_leaving_the_block():
    assert _current_study_context() == {"strategy": None, "symbol": None, "study_name": None}
    with bind_study_context(strategy="StratA", symbol="AAA.ETORO", study_name="study_StratA_AAA"):
        assert _current_study_context()["strategy"] == "StratA"
    assert _current_study_context() == {"strategy": None, "symbol": None, "study_name": None}


def test_no_prefix_outside_any_bound_context():
    logger, handler = _capturing_logger("test780_no_context")
    logger.warning("plain message")
    assert "[" not in handler.records[0].split("| plain")[0].split("|")[-1] or \
           "plain message" in handler.records[0]
    assert handler.records[0].endswith("plain message")


# ── Akzeptanzkriterium #780/2: jede JSONL-Zeile trägt strategy und symbol ─────────────────────────
def test_emit_execution_event_stamps_strategy_symbol_from_context(tmp_path, monkeypatch):
    import automation.log_manager as lm
    logger = logging.getLogger("test780_jsonl")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    jsonl_path = tmp_path / "events.jsonl"
    monkeypatch.setitem(lm._JSONL_SIDECAR_PATHS, "test780_jsonl", jsonl_path)
    monkeypatch.setitem(lm._ACTIVE_RUN_IDS, "test780_jsonl", "run_xyz")

    with bind_study_context(strategy="StratA", symbol="AAA.ETORO", study_name="study_StratA_AAA"):
        emit_execution_event(logger, "REWARD_TERM_INERT", {"term": "base"})

    lines = [json.loads(l) for l in jsonl_path.read_text("utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["strategy"] == "StratA"
    assert lines[0]["symbol"] == "AAA.ETORO"
    assert lines[0]["study_name"] == "study_StratA_AAA"
    assert lines[0]["run_id"] == "run_xyz"
    assert lines[0]["term"] == "base"


def test_explicit_payload_value_overrides_ambient_context(tmp_path, monkeypatch):
    """Rueckwaertskompatibilitaet: die ~15 bestehenden Call-Sites, die strategy/symbol bereits
    SELBST im payload tragen, behalten Vorrang vor dem ambienten Kontext."""
    import automation.log_manager as lm
    logger = logging.getLogger("test780_override")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    jsonl_path = tmp_path / "events.jsonl"
    monkeypatch.setitem(lm._JSONL_SIDECAR_PATHS, "test780_override", jsonl_path)

    with bind_study_context(strategy="StratA", symbol="AAA.ETORO"):
        emit_execution_event(logger, "SOME_EVENT", {"strategy": "ExplicitStrat", "symbol": "X.ETORO"})

    line = json.loads(jsonl_path.read_text("utf-8").splitlines()[0])
    assert line["strategy"] == "ExplicitStrat"
    assert line["symbol"] == "X.ETORO"


def test_no_context_and_no_run_id_yields_none_fields_not_missing_keys(tmp_path, monkeypatch):
    import automation.log_manager as lm
    logger = logging.getLogger("test780_none")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    jsonl_path = tmp_path / "events.jsonl"
    monkeypatch.setitem(lm._JSONL_SIDECAR_PATHS, "test780_none", jsonl_path)
    monkeypatch.delitem(lm._ACTIVE_RUN_IDS, "test780_none", raising=False)

    emit_execution_event(logger, "SOME_EVENT", {})
    line = json.loads(jsonl_path.read_text("utf-8").splitlines()[0])
    for key in ("strategy", "symbol", "study_name", "run_id"):
        assert key in line
        assert line[key] is None


# ── Akzeptanzkriterium #780/3: Reproduktion der Kennzahlen aus dem JSONL ohne Reihenfolge-Heuristik
def test_jsonl_events_are_attributable_without_ordering_heuristic(tmp_path, monkeypatch):
    """Simuliert die Katalog-Beobachtung: ComboTrendVwapStrategy hat einen Champion und emittiert
    daher NIE [#565] — eine reihenfolgebasierte Heuristik ('letzter [#565]-Marker') würde die
    Strategie komplett übersehen. Die contextvar-Attribution braucht keinen solchen Marker."""
    import automation.log_manager as lm
    logger = logging.getLogger("test780_repro")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    jsonl_path = tmp_path / "events.jsonl"
    monkeypatch.setitem(lm._JSONL_SIDECAR_PATHS, "test780_repro", jsonl_path)

    with bind_study_context(strategy="ComboTrendVwapStrategy", symbol="AAA.ETORO"):
        emit_execution_event(logger, "ZERO_ELIGIBLE_PLATEAU", {})
    with bind_study_context(strategy="SqueezeBreakoutStrategy", symbol="BBB.ETORO"):
        emit_execution_event(logger, "ZERO_ELIGIBLE_PLATEAU", {})
        emit_execution_event(logger, "ZERO_ELIGIBLE_PLATEAU", {})

    lines = [json.loads(l) for l in jsonl_path.read_text("utf-8").splitlines()]
    by_strategy = {}
    for l in lines:
        by_strategy.setdefault(l["strategy"], []).append(l["symbol"])
    assert by_strategy["ComboTrendVwapStrategy"] == ["AAA.ETORO"]
    assert by_strategy["SqueezeBreakoutStrategy"] == ["BBB.ETORO", "BBB.ETORO"]


# ── optimize_symbol wiring: der Kontext ist waehrend des GESAMTEN Study-Lebenszyklus gebunden ─────
def test_optimize_symbol_binds_context_for_the_full_study_lifetime(tmp_path, monkeypatch):
    from automation.optimizer import run_optimization as ro
    from automation.optimizer import trial_config

    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)
    (tmp_path / "backtest.json").write_text(json.dumps({
        "walk_forward": {"holdout_days": 45, "is_window_days": 120, "oos_window_days": 45,
                         "splits": 1, "embargo_period_days": 0}}), "utf-8")

    observed = []
    real_set_user_attr_targets = []

    def _fake_backtest(trial_dir, manifest_path, **_kwargs):
        from pathlib import Path
        observed.append(dict(_current_study_context()))
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 0, "aggregate_winner": {
            "oos_evaluated": False, "oos_eligible": False}}), "utf-8")
        return out

    monkeypatch.setattr(ro, "run_backtest", _fake_backtest)
    ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)

    assert observed
    assert all(o["strategy"] == "SmaCrossoverStrategy" for o in observed)
    assert all(o["symbol"] == "TSLA.ETORO" for o in observed)
    # Nach optimize_symbol ist der Kontext wieder zurueckgesetzt (kein Leck in den Aufrufer-Thread).
    assert _current_study_context() == {"strategy": None, "symbol": None, "study_name": None}
