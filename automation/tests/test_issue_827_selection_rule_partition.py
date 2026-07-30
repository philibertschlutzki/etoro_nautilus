"""Issue #827 (P1, Katalog #822-#827, GitHub-Issue #750) — zwei ``selection_rule_fingerprint`` je
Familie: die DSR-Voraussetzung ist verletzt, die Korrektur läuft trotzdem.

Root-Cause: die DSR-Multiplizitätskorrektur (E[max_N]) setzt eine über die Familie KONSTANTE
Selektionsregel voraus (Pitfall #248). ``reward.selection_rule_fingerprint`` (#812) erkannte eine
Abweichung bereits — ``sweep._family_n_from_studies`` zählte die betroffenen Studies TROTZDEM als
EINE Familie und reichte diese gemeinsame Zahl an die Deflation weiter.

Fix: Punkt 1/2 sind seit #826 strukturell erledigt — ``sweep._family_n_stage1_from_studies``
liefert das N JEDER EINZELNEN Study (nicht mehr über die Familie summiert), unabhängig von deren
Fingerprint; zwei Studies mit unterschiedlichem Fingerprint erhalten damit automatisch je ihr
EIGENES N, nie eine gemeinsame Summe (Akzeptanzkriterium #827: 2 Studies x 100 Trials ⇒ je N=100,
nicht N=200). Punkt 3 (dieser Fix): ``selection_rule_homogeneity_policy ∈ {partition, fail}``
(``tournament.json``, Default ``'partition'`` = Status quo, reine Warnung) — ``'fail'`` bricht ein
Symbol mit nachweislich heterogenen Fingerprints fail-loud ab (``SYMBOL_ABORTED_ON_SELECTION_
HETEROGENEITY``), für Kalibrierläufe, die eine homogene Selektionsregel als harte Vorbedingung
brauchen. Punkt 4 (``min_win_rate``-OR-Arm ersatzlos streichen) ist eine EMPFEHLUNG des Katalogs,
kein Bestandteil des #834-Semantik-Bumps dieser Kohorte, und bleibt bewusst unangetastet — eine
Änderung der Eligibility-Definition (``eligible_requires_any``) ist ein eigener, weitreichender
Schritt, der eine eigene Entscheidung/einen eigenen Bump verdient statt reflexiv mitgezogen zu
werden.
"""
import json
import logging

import pytest

from automation.optimizer import sweep


class _T:
    def __init__(self, has_statistic=True):
        self.user_attrs = {"oos_selection_statistic_available": has_statistic}


class _S:
    def __init__(self, n, fingerprint=None):
        self.trials = [_T(True) for _ in range(n)]
        self.user_attrs = {} if fingerprint is None else {"selection_rule_fingerprint": fingerprint}


# ── Akzeptanzkriterium #827 (Unit-Test aus dem Issue-Text) ──────────────────────────────────────────
def test_two_studies_with_different_fingerprints_each_get_their_own_n_not_the_sum():
    """2 Studies mit verschiedenen Fingerprints, je 100 evaluierte Trials ⇒ jede erhält N = 100,
    nicht N = 200 — strukturell durch #826 (N1 ist JE STUDY) sichergestellt, unabhängig vom
    Fingerprint selbst."""
    pairs = [("StratA", "ADBE.ETORO", "OK"), ("StratB", "ADBE.ETORO", "OK")]
    studies = [_S(100, fingerprint="fp1"), _S(100, fingerprint="fp2")]
    stage1 = sweep._family_n_stage1_from_studies(pairs, studies, tournament_cfg={})
    assert stage1[("StratA", "ADBE.ETORO")] == 100
    assert stage1[("StratB", "ADBE.ETORO")] == 100


def test_family_fingerprints_from_studies_reports_both_fingerprints():
    pairs = [("StratA", "ADBE.ETORO", "OK"), ("StratB", "ADBE.ETORO", "OK")]
    studies = [_S(100, fingerprint="fp1"), _S(100, fingerprint="fp2")]
    fingerprints = sweep._family_fingerprints_from_studies(pairs, studies, tournament_cfg={})
    assert fingerprints["ADBE.ETORO"] == {"fp1", "fp2"}


def test_family_fingerprints_from_studies_single_fingerprint_when_homogeneous():
    pairs = [("StratA", "ADBE.ETORO", "OK"), ("StratB", "ADBE.ETORO", "OK")]
    studies = [_S(100, fingerprint="fp1"), _S(50, fingerprint="fp1")]
    fingerprints = sweep._family_fingerprints_from_studies(pairs, studies, tournament_cfg={})
    assert fingerprints["ADBE.ETORO"] == {"fp1"}


# ── _resolve_selection_rule_homogeneity_policy: Default, Validierung, Fail-Loud ─────────────────────
def test_resolve_policy_defaults_to_partition():
    assert sweep._resolve_selection_rule_homogeneity_policy(None) == "partition"
    assert sweep._resolve_selection_rule_homogeneity_policy({}) == "partition"


def test_resolve_policy_accepts_fail():
    assert sweep._resolve_selection_rule_homogeneity_policy(
        {"selection_rule_homogeneity_policy": "fail"}) == "fail"


def test_resolve_policy_rejects_unknown_value():
    with pytest.raises(ValueError):
        sweep._resolve_selection_rule_homogeneity_policy(
            {"selection_rule_homogeneity_policy": "typo"})


# ── Integration: run_per_symbol_sweep unter 'fail' bricht NUR das heterogene Symbol ab ──────────────
_GATE_CFG_FILES = {
    "backtest.json": {"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                       "oos_window_days": 45, "splits": 1,
                                       "embargo_period_days": 0}},
    "optimizer.json": {"gate1_buffer_days": 30, "min_bars_per_param": 200,
                       "min_oos_bars_per_fold": 500},
}


def _write_cfg(cfg_dir, tournament_extra):
    cfg_dir.mkdir(exist_ok=True)
    for name, payload in _GATE_CFG_FILES.items():
        (cfg_dir / name).write_text(json.dumps(payload), "utf-8")
    (cfg_dir / "tournament.json").write_text(json.dumps(tournament_extra), "utf-8")


class _FakeStudy:
    def __init__(self, n_trials=10, fingerprint=None):
        self.trials = [_T(True) for _ in range(n_trials)]
        self._attrs = {} if fingerprint is None else {"selection_rule_fingerprint": fingerprint}
        self.best_value = 1.0
        self.directions = ["maximize"]

    @property
    def user_attrs(self):
        return dict(self._attrs)


def test_fail_policy_aborts_only_the_heterogeneous_symbol(monkeypatch, tmp_path, caplog):
    cfg_dir = tmp_path / "config"
    _write_cfg(cfg_dir, {"selection_rule_homogeneity_policy": "fail"})
    pairs = [("S1", "HET.ETORO", "OK"), ("S2", "HET.ETORO", "OK"), ("S3", "HOMO.ETORO", "OK")]

    def fake_opt(strategy, symbol, **k):
        if symbol == "HET.ETORO":
            fp = "fp1" if strategy == "S1" else "fp2"
        else:
            fp = "fp_same"
        return _FakeStudy(n_trials=10, fingerprint=fp)

    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(sweep, "WORK", tmp_path)

    with caplog.at_level(logging.ERROR, logger="optimizer"):
        out = sweep.run_per_symbol_sweep(
            ["S1", "S2", "S3"], ["HET.ETORO", "HOMO.ETORO"], tier="all", n_jobs=1,
            optimize_symbol=fake_opt,
            confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
        )
    # HET.ETORO liefert KEIN Proposal (Symbol abgebrochen); HOMO.ETORO liefert seins ganz normal.
    assert {p.name for p in out} == {"proposal_S3_HOMO.ETORO.json"}
    assert any("SYMBOL_ABORTED_ON_SELECTION_HETEROGENEITY" in r.message for r in caplog.records)
    checkpoint = json.loads((tmp_path / "sweep_progress.json").read_text("utf-8"))
    assert sorted(checkpoint["completed_symbols"]) == ["HET.ETORO", "HOMO.ETORO"]


def test_partition_policy_default_still_produces_proposals_for_heterogeneous_symbol(
    monkeypatch, tmp_path, caplog,
):
    """Rückwärtskompat: unter dem Default 'partition' bleibt das Verhalten bit-identisch zum
    Status quo — heterogene Fingerprints erzeugen nur die [#812]-WARNUNG, kein Abbruch."""
    cfg_dir = tmp_path / "config"
    _write_cfg(cfg_dir, {})  # kein selection_rule_homogeneity_policy -> Default 'partition'
    pairs = [("S1", "HET.ETORO", "OK"), ("S2", "HET.ETORO", "OK")]

    def fake_opt(strategy, symbol, **k):
        fp = "fp1" if strategy == "S1" else "fp2"
        return _FakeStudy(n_trials=10, fingerprint=fp)

    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(sweep, "WORK", tmp_path)

    with caplog.at_level(logging.WARNING, logger="optimizer"):
        out = sweep.run_per_symbol_sweep(
            ["S1", "S2"], ["HET.ETORO"], tier="all", n_jobs=1,
            optimize_symbol=fake_opt,
            confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
        )
    assert {p.name for p in out} == {"proposal_S1_HET.ETORO.json", "proposal_S2_HET.ETORO.json"}
    assert any("#812" in r.message for r in caplog.records)
    assert not any("SYMBOL_ABORTED_ON_SELECTION_HETEROGENEITY" in r.message for r in caplog.records)
