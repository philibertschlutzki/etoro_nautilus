"""Issue #939 (Katalog A, Pitfall #299) — ein Fehler im Confirm/Export-Abschnitt EINES Symbols darf
nicht den GESAMTEN Sweep beenden.

Root-Cause: die Symbol-Schleife in ``run_per_symbol_sweep`` hatte kein try/except um den Confirm/
Export-Abschnitt (die vorgelagerte Optimize-Phase ist bereits seit #799 pro PAAR isoliert, siehe
``test_issue_799_per_symbol_transaction.py::test_failure_in_one_symbol_does_not_lose_prior_or_later_symbols``
— das deckt eine Exception in ``optimize_symbol``, NICHT in ``confirm``/``export_symbol_proposal``).
Bei einer Symbol-Ausfallrate von 1% ueber N=143 Symbole liegt die Lauf-Erfolgswahrscheinlichkeit
eines seriellen Ketten-Systems ohne Fehler-Isolation nur bei 0.99^143 ≈ 23.6% (Pitfall #299).
"""
import json

import pytest

from automation.optimizer import sweep

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


class _FakeTrial:
    def __init__(self, oos_evaluated=True):
        self.user_attrs = {"oos_evaluated": oos_evaluated,
                           "oos_selection_statistic_available": oos_evaluated}


class _FakeStudy:
    def __init__(self, n_trials=10):
        self.trials = [_FakeTrial() for _ in range(n_trials)]
        self._attrs = {"n_trials_budget": n_trials}
        self.best_value = 1.0
        self.directions = ["maximize"]

    @property
    def user_attrs(self):
        return dict(self._attrs)


def _patch_common(monkeypatch, tmp_path, pairs, opt_data_overrides=None):
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    if opt_data_overrides:
        base_opt_data = dict(sweep._load_optimizer_config())
        base_opt_data.update(opt_data_overrides)
        monkeypatch.setattr(sweep, "_load_optimizer_config", lambda: base_opt_data)


def test_confirm_failure_on_one_symbol_does_not_abort_remaining_symbols(monkeypatch, tmp_path):
    """5 Symbole, ``confirm`` wirft fuer Symbol 3 (nicht ``optimize_symbol`` — das deckt bereits
    #799 ab). Symbole 1-2 und 4-5 muessen trotzdem ihre Proposals liefern."""
    symbols = [f"SYM{i}.ETORO" for i in range(1, 6)]
    pairs = [("S", sym, "OK") for sym in symbols]

    def fake_opt(strategy, symbol, **k):
        return _FakeStudy()

    def fake_confirm(study, s, sym, gp, **k):
        if sym == "SYM3.ETORO":
            raise RuntimeError("[JSON_EVENT] TypeError: keys must be str (simulated #937-class bug)")
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    _patch_common(monkeypatch, tmp_path, pairs)
    out = sweep.run_per_symbol_sweep(
        ["S"], symbols, tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )
    expected = {tmp_path / f"proposal_S_{sym}.json" for sym in symbols if sym != "SYM3.ETORO"}
    assert set(out) == expected
    assert len(out) == 4

    checkpoint = json.loads((tmp_path / "sweep_progress.json").read_text("utf-8"))
    # Alle 5 Symbole gelten als "abgeschlossen" (kein endloser Retry-Loop bei Resume), obwohl
    # SYM3 fehlschlug.
    assert sorted(checkpoint["completed_symbols"]) == sorted(symbols)
    assert sweep.sweep_failed_symbols == ["SYM3.ETORO"]


def test_symbol_failure_threshold_triggers_hard_abort_when_both_conditions_violated(
    monkeypatch, tmp_path,
):
    """Mit ``max_failed_symbols_abs=1``/``max_failed_symbols_frac=0.1`` und 5 Symbolen, von denen
    3 fehlschlagen, muss der Sweep NACH Ueberschreiten BEIDER Schwellen hart abbrechen (Politik-
    Entscheidung statt Nebeneffekt einer beliebigen Exception)."""
    symbols = [f"SYM{i}.ETORO" for i in range(1, 6)]
    pairs = [("S", sym, "OK") for sym in symbols]
    failing = {"SYM1.ETORO", "SYM2.ETORO", "SYM3.ETORO"}

    def fake_opt(strategy, symbol, **k):
        return _FakeStudy()

    def fake_confirm(study, s, sym, gp, **k):
        if sym in failing:
            raise RuntimeError("boom")
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    _patch_common(monkeypatch, tmp_path, pairs, opt_data_overrides={
        "max_failed_symbols_abs": 1, "max_failed_symbols_frac": 0.1,
    })
    with pytest.raises(RuntimeError, match="Symbol-Ausfallrate"):
        sweep.run_per_symbol_sweep(
            ["S"], symbols, tier="all", n_jobs=1,
            optimize_symbol=fake_opt, confirm=fake_confirm,
        )


def test_symbol_failure_below_threshold_does_not_abort(monkeypatch, tmp_path):
    """Ein einzelner Ausfall unter dem generoesen Default (abs=5, frac=0.05) darf NICHT
    abbrechen — Akzeptanzkriterium #939: ein Lauf mit 10 Symbolen bricht nicht schon bei einem
    einzigen Ausfall ab."""
    symbols = [f"SYM{i}.ETORO" for i in range(1, 11)]
    pairs = [("S", sym, "OK") for sym in symbols]

    def fake_opt(strategy, symbol, **k):
        return _FakeStudy()

    def fake_confirm(study, s, sym, gp, **k):
        if sym == "SYM1.ETORO":
            raise RuntimeError("boom")
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    _patch_common(monkeypatch, tmp_path, pairs)
    out = sweep.run_per_symbol_sweep(
        ["S"], symbols, tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )
    assert len(out) == 9
    assert sweep.sweep_failed_symbols == ["SYM1.ETORO"]
