"""Issue #807 — symbolweite Datendegeneration wurde 14-mal als strategiespezifisches
Suchraumproblem diagnostiziert: `HYPE.ETORO` erzeugte ueber SECHS strukturell verschiedene
Strategien 0 auswertbare Trials — jeweils mit eigenem `STRUCTURAL_ALL_UNEVALUABLE`-Ereignis, eigenem
`diagnose_trade_frequency`-Aufruf und eigenem `diagnosed_pairs_cache`-Eintrag (14 × 16 = 224
verbrannte Trials). `HYPE` bestand Gate 1 (Datenspanne) — die Ursache war die BAR-QUALITAET
(degenerierte/konstante Bars), nicht die Datenspanne oder 14 unabhaengige Suchraum-Probleme.

Fix: (1) `sweep_diagnostics.check_bar_quality` — eine billige, reine Bar-Qualitaetspruefung
(high==low-Anteil, identische aufeinanderfolgende Closes, Anzahl distinkter Closes). (2) Ein
Preflight in `sweep.run_per_symbol_sweep` weist ein Symbol mit degenerierten Bars EINMAL
(`REJECT_DATA_DEGENERATE`) ab, BEVOR auch nur eine Study fuer es startet — analog dem bestehenden
Gate-1-Pfad. (3) `sweep_diagnostics.diagnose_symbol_degeneracy` — eine reine Aggregationsfunktion
fuer mehrere per-Strategie-Diagnosen DESSELBEN Symbols (Sekundaer-Signal, blockiert selbst nichts).
"""
import json
import logging

import pytest

from automation.optimizer import sweep
from automation.optimizer.sweep_diagnostics import check_bar_quality, diagnose_symbol_degeneracy


# ---------------------------------------------------------------------------
# check_bar_quality: reine Funktion
# ---------------------------------------------------------------------------

def _healthy_bars(n=200):
    import random
    rng = random.Random(807)
    closes = []
    highs = []
    lows = []
    price = 100.0
    for _ in range(n):
        price *= (1.0 + rng.uniform(-0.01, 0.01))
        closes.append(price)
        highs.append(price * 1.002)
        lows.append(price * 0.998)
    return highs, lows, closes


def test_healthy_bars_pass():
    highs, lows, closes = _healthy_bars()
    result = check_bar_quality(highs, lows, closes)
    assert result["passed"] is True
    assert result["reason"] == "OK"


def test_empty_bars_fail_closed():
    result = check_bar_quality([], [], [])
    assert result["passed"] is False
    assert result["n_bars"] == 0


def test_all_high_eq_low_fails():
    n = 50
    closes = [100.0 + i * 0.01 for i in range(n)]  # genug distinkte Closes
    highs = list(closes)
    lows = list(closes)
    result = check_bar_quality(highs, lows, closes)
    assert result["passed"] is False
    assert "frac_high_eq_low" in result["reason"]


def test_mostly_identical_consecutive_closes_fails():
    n = 50
    # 90% der Bars wiederholen den Vorgaenger-Close exakt.
    closes = []
    price = 100.0
    for i in range(n):
        if i % 10 != 0:
            closes.append(price)
        else:
            price += 1.0
            closes.append(price)
    highs = [c * 1.001 for c in closes]
    lows = [c * 0.999 for c in closes]
    result = check_bar_quality(highs, lows, closes)
    assert result["passed"] is False
    assert "frac_identical_consecutive_closes" in result["reason"]


def test_too_few_distinct_closes_fails():
    n = 50
    # Nur 3 distinkte Preis-Level ueber die gesamte Kohorte.
    closes = [100.0, 101.0, 102.0] * (n // 3) + [100.0] * (n % 3)
    highs = [c * 1.002 for c in closes]
    lows = [c * 0.998 for c in closes]
    result = check_bar_quality(highs, lows, closes, min_distinct_closes=10)
    assert result["passed"] is False
    assert "n_distinct_closes" in result["reason"]


def test_custom_thresholds_are_respected():
    highs, lows, closes = _healthy_bars()
    # Ein absurd strenger Schwellenwert laesst sogar gesunde Bars durchfallen.
    result = check_bar_quality(highs, lows, closes, min_distinct_closes=10_000)
    assert result["passed"] is False


# ---------------------------------------------------------------------------
# diagnose_symbol_degeneracy: reine Aggregationsfunktion (blockiert selbst nichts)
# ---------------------------------------------------------------------------

def _diag(binding_cause, median_is_trades):
    return {"binding_cause": binding_cause, "median_is_trades": median_is_trades}


def test_three_signal_absent_strategies_mark_symbol_degenerate():
    diagnoses = [_diag("signal_absent", 0) for _ in range(3)]
    result = diagnose_symbol_degeneracy("HYPE.ETORO", diagnoses)
    assert result["is_degenerate"] is True
    assert result["n_signal_absent"] == 3


def test_two_signal_absent_strategies_do_not_mark_symbol_degenerate():
    diagnoses = [_diag("signal_absent", 0) for _ in range(2)] + [_diag("signal_sparse", 5)]
    result = diagnose_symbol_degeneracy("HYPE.ETORO", diagnoses)
    assert result["is_degenerate"] is False
    assert result["n_signal_absent"] == 2


def test_signal_absent_with_nonzero_median_does_not_count():
    """binding_cause=='signal_absent' erfordert PER DEFINITION median_is_trades==0 — dieser Test
    schuetzt gegen eine zukuenftige Regression, die dieses Invariant bricht."""
    diagnoses = [_diag("signal_absent", 0), _diag("signal_absent", 0), _diag("signal_absent", 5)]
    result = diagnose_symbol_degeneracy("HYPE.ETORO", diagnoses)
    assert result["is_degenerate"] is False
    assert result["n_signal_absent"] == 2


def test_custom_min_strategies_threshold():
    diagnoses = [_diag("signal_absent", 0) for _ in range(5)]
    result = diagnose_symbol_degeneracy("X", diagnoses, min_strategies=6)
    assert result["is_degenerate"] is False
    result2 = diagnose_symbol_degeneracy("X", diagnoses, min_strategies=5)
    assert result2["is_degenerate"] is True


def test_empty_diagnoses_list_is_not_degenerate():
    result = diagnose_symbol_degeneracy("X", [])
    assert result["is_degenerate"] is False
    assert result["n_strategies_checked"] == 0


# ---------------------------------------------------------------------------
# run_per_symbol_sweep: Bar-Qualitaets-Preflight (Akzeptanzkriterium #807)
# ---------------------------------------------------------------------------

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def _patch_common(monkeypatch, tmp_path, pairs):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    # Issue #807 — filtert wie die ECHTE enumerate_tunable_pairs auf die uebergebenen (bereits vom
    # Bar-Qualitaets-Preflight bereinigten) Symbole, statt die volle Fixture-Liste blind zurueckzugeben.
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs",
                        lambda strategies, syms, **k: [p for p in pairs if p[1] in (syms or [])])
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)


def test_degenerate_symbol_is_rejected_before_any_study_starts(monkeypatch, tmp_path):
    """Akzeptanzkriterium #807: HYPE.ETORO (hier simuliert) erscheint mit GENAU einem
    REJECT_DATA_DEGENERATE-Ereignis und NULL gestarteten Studies — trotz zweier konfigurierter
    Strategien, die sonst je eine eigene Study gestartet haetten."""
    pairs = [("S1", "HYPE.ETORO", "OK"), ("S2", "HYPE.ETORO", "OK")]
    _patch_common(monkeypatch, tmp_path, pairs)

    started = []

    def fake_opt(strategy, symbol, **k):
        started.append((strategy, symbol))
        return object()

    def _degenerate_sample(symbol):
        # Konstante Bars: high==low ueberall, jeder Close identisch zum Vorgaenger.
        return {"highs": [100.0] * 50, "lows": [100.0] * 50, "closes": [100.0] * 50}

    handler_events = []

    class _H(logging.Handler):
        def emit(self, r):
            msg = r.getMessage()
            if "[JSON_EVENT]" in msg:
                handler_events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))

    logger = logging.getLogger("optimizer")
    logger.addHandler(_H())
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        out = sweep.run_per_symbol_sweep(
            ["S1", "S2"], ["HYPE.ETORO"], tier="all", n_jobs=1,
            optimize_symbol=fake_opt,
            confirm=lambda *a, **k: {"promote": False, "status": "X", "symbol_params": {}},
            bar_quality_fn=_degenerate_sample,
        )
    finally:
        logger.removeHandler(_H())
        logger.setLevel(old_level)

    assert started == [], "HYPE.ETORO durfte NIE eine Study starten (0 Studies)"
    assert out == []
    reject_events = [e for e in handler_events if e.get("event_type") == "REJECT_DATA_DEGENERATE"]
    assert len(reject_events) == 1
    assert reject_events[0]["symbol"] == "HYPE.ETORO"


def test_healthy_symbol_is_not_rejected_and_proceeds_normally(monkeypatch, tmp_path):
    """Ein Symbol mit gesunden Bars wird NICHT abgewiesen — die Bar-Qualitaet ist die notwendige
    Bedingung, keine unabhaengig davon feuernde Diagnosezahl."""
    pairs = [("S1", "GOOD.ETORO", "OK")]
    _patch_common(monkeypatch, tmp_path, pairs)

    started = []

    def fake_opt(strategy, symbol, **k):
        started.append((strategy, symbol))
        return object()

    def _healthy_sample(symbol):
        highs, lows, closes = _healthy_bars(200)
        return {"highs": highs, "lows": lows, "closes": closes}

    out = sweep.run_per_symbol_sweep(
        ["S1"], ["GOOD.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=fake_opt,
        confirm=lambda *a, **k: {"promote": False, "status": "X", "symbol_params": {}},
        bar_quality_fn=_healthy_sample,
    )
    assert started == [("S1", "GOOD.ETORO")]
    assert out == [tmp_path / "proposal_S1_GOOD.ETORO.json"]


def test_only_the_degenerate_symbol_is_excluded_others_proceed(monkeypatch, tmp_path):
    pairs = [("S1", "HYPE.ETORO", "OK"), ("S1", "GOOD.ETORO", "OK")]
    _patch_common(monkeypatch, tmp_path, pairs)

    started = []

    def fake_opt(strategy, symbol, **k):
        started.append((strategy, symbol))
        return object()

    def _mixed_sample(symbol):
        if symbol == "HYPE.ETORO":
            return {"highs": [100.0] * 50, "lows": [100.0] * 50, "closes": [100.0] * 50}
        highs, lows, closes = _healthy_bars(200)
        return {"highs": highs, "lows": lows, "closes": closes}

    out = sweep.run_per_symbol_sweep(
        ["S1"], ["HYPE.ETORO", "GOOD.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=fake_opt,
        confirm=lambda *a, **k: {"promote": False, "status": "X", "symbol_params": {}},
        bar_quality_fn=_mixed_sample,
    )
    assert started == [("S1", "GOOD.ETORO")]
    assert out == [tmp_path / "proposal_S1_GOOD.ETORO.json"]


def test_missing_bar_sample_fails_open_gate1_stays_authoritative(monkeypatch, tmp_path):
    """Kein Katalog/Lesefehler (bar_quality_fn liefert None) ⇒ das Symbol wird NICHT abgewiesen —
    Gate 1 (Datenspanne) bleibt die unabhaengige, bereits bestehende Absicherung; ein eigener
    Lesefehler darf den Sweep nie blockieren."""
    pairs = [("S1", "NODATA.ETORO", "OK")]
    _patch_common(monkeypatch, tmp_path, pairs)
    started = []

    def fake_opt(strategy, symbol, **k):
        started.append((strategy, symbol))
        return object()

    out = sweep.run_per_symbol_sweep(
        ["S1"], ["NODATA.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=fake_opt,
        confirm=lambda *a, **k: {"promote": False, "status": "X", "symbol_params": {}},
        bar_quality_fn=lambda symbol: None,
    )
    assert started == [("S1", "NODATA.ETORO")]
    assert out == [tmp_path / "proposal_S1_NODATA.ETORO.json"]


def test_diagnosed_pairs_cache_gets_one_symbol_scoped_entry_not_per_strategy(monkeypatch, tmp_path):
    """Akzeptanzkriterium #807 (Umsetzung 3): der Rueckschrieb in diagnosed_pairs_cache.json ist
    symbol-skopiert (EIN Eintrag), nicht 14-fach paar-skopiert — hier mit zwei Strategien auf dem
    degenerierten Symbol simuliert (0 statt 2 Cache-Eintraege waeren falsch, 2 statt 1 ebenfalls)."""
    from automation.optimizer.sweep_diagnostics import load_diagnosed_pairs_cache

    pairs = [("S1", "HYPE.ETORO", "OK"), ("S2", "HYPE.ETORO", "OK")]
    _patch_common(monkeypatch, tmp_path, pairs)

    def _degenerate_sample(symbol):
        return {"highs": [100.0] * 50, "lows": [100.0] * 50, "closes": [100.0] * 50}

    sweep.run_per_symbol_sweep(
        ["S1", "S2"], ["HYPE.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=lambda *a, **k: object(),
        confirm=lambda *a, **k: {"promote": False, "status": "X", "symbol_params": {}},
        bar_quality_fn=_degenerate_sample,
    )
    cache = load_diagnosed_pairs_cache(work_dir=tmp_path)
    hype_entries = [v for (strat, sym), v in cache.items() if sym == "HYPE.ETORO"]
    assert len(hype_entries) == 1, "genau EIN symbol-skopierter Eintrag, nicht 14-fach paar-skopiert"
    assert hype_entries[0]["binding_cause"] == "data_degenerate"


def test_secondary_signal_from_cache_does_not_reject_a_healthy_bar_symbol(monkeypatch, tmp_path,
                                                                          caplog):
    """Akzeptanzkriterium #807 (3. Punkt): ein Symbol mit gesunden Bars, fuer das der Diagnose-Cache
    bereits >= symbol_degeneracy_min_strategies 'signal_absent'-Befunde aus VORHERIGEN Laeufen
    enthaelt, wird TROTZDEM nicht abgewiesen — die Bar-Qualitaet ist die notwendige Bedingung, die
    Diagnosezahl allein reicht nicht. Das Sekundaer-Signal darf nur LOGGEN, nie blockieren."""
    from automation.optimizer.sweep_diagnostics import _diagnosed_pairs_cache_path
    import json as _json

    cache_path = _diagnosed_pairs_cache_path(tmp_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(_json.dumps({"pairs": [
        {"strategy": f"Strat{i}", "symbol": "GOOD.ETORO", "action": "denylist",
         "binding_cause": "signal_absent", "median_is_trades": 0}
        for i in range(3)
    ]}), "utf-8")

    pairs = [("S1", "GOOD.ETORO", "OK")]
    _patch_common(monkeypatch, tmp_path, pairs)
    started = []

    def fake_opt(strategy, symbol, **k):
        started.append((strategy, symbol))
        return object()

    def _healthy_sample(symbol):
        highs, lows, closes = _healthy_bars(200)
        return {"highs": highs, "lows": lows, "closes": closes}

    with caplog.at_level(logging.WARNING, logger="optimizer"):
        out = sweep.run_per_symbol_sweep(
            ["S1"], ["GOOD.ETORO"], tier="all", n_jobs=1,
            optimize_symbol=fake_opt,
            confirm=lambda *a, **k: {"promote": False, "status": "X", "symbol_params": {}},
            bar_quality_fn=_healthy_sample,
        )
    assert started == [("S1", "GOOD.ETORO")], "gesunde Bars duerfen NIE durch die Cache-Diagnosezahl allein abgewiesen werden"
    assert out == [tmp_path / "proposal_S1_GOOD.ETORO.json"]
    assert any("SYMBOL_DATA_DEGENERATE" in r.message for r in caplog.records)
