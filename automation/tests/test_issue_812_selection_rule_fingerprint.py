"""Issue #812 (P1) — ``any_arm_unreachable_policy='recalibrate'`` macht die Selektionsregel
study-abhängig und entzieht der DSR-Multiplizitätskorrektur ihre Voraussetzung.

Symptom: 230 Warnungen der Form "eligible_requires_any-Klausel 'min_win_rate': Schwelle 0.1500 >
beobachtetes p99=0.0000". ``recalibrate`` setzte die Schwelle daraufhin auf
``max(p99, min_win_rate_recalibration_floor)`` = 0.05 — ein bedeutungsloser Filter.

Root-Cause: Die Deflated Sharpe Ratio korrigiert für das Maximum von N Kandidaten, die DERSELBEN
Selektionsprozedur unterworfen wurden. ``recalibrate`` macht die Prozedur je (Strategie, Symbol)
verschieden; p99=0.0 ist seit #788/#759 ein ECHTES Ergebnis (keine gewinnenden Trades), keine
Rechtfertigung für eine gesenkte Hürde.

Fix: Default-Wechsel ``any_arm_unreachable_policy: 'recalibrate' → 'drop_arm'``.
``min_win_rate_recalibration_floor`` entfernt (toter Schlüssel). Neues Feld
``selection_rule_fingerprint`` (SHA-256 über die effektiv wirksame Gate-Konfiguration inkl. aller
#668-Policy-Anpassungen) je Study, im #742-Report als ``selection_rule_families`` je Symbol
aufgeschlüsselt.
"""
import hashlib
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer.reward import (
    resolve_any_arm_policy,
    selection_rule_fingerprint,
    _effective_gate_thresholds,
)
from automation.optimizer.report import _selection_rule_families, _study_record
from automation.optimizer import sweep

CFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


# ── Config: Default-Wechsel + toter Schlüssel entfernt ─────────────────────────────────────────────
def test_real_config_defaults_to_drop_arm():
    assert CFG["any_arm_unreachable_policy"] == "drop_arm"


def test_min_win_rate_recalibration_floor_key_is_gone():
    assert "min_win_rate_recalibration_floor" not in CFG
    assert "min_win_rate_recalibration_floor" not in CFG["_schema"]["fields"]


def test_schema_documents_812_for_the_policy_default():
    doc = CFG["_schema"]["fields"]["any_arm_unreachable_policy"]
    assert "#812" in doc


# ── Akzeptanzkriterium #812: any_arm_recalibrated_thresholds leer im Default-Modus ─────────────────
def test_any_arm_recalibrated_thresholds_is_empty_under_default_drop_arm_policy():
    # Strukturell unerreichbare min_win_rate-Klausel (Schwelle 0.15 > beobachtetes p99=0.0).
    observed = {"min_win_rate": [0.0] * 20}
    decision = resolve_any_arm_policy(CFG, observed, n_evaluated=20)
    assert decision["policy"] == "drop_arm"
    assert decision["recalibrated_thresholds"] == {}


# ── Akzeptanzkriterium #812: any_arm_reduced enthaelt min_win_rate fuer betroffene Studies ─────────
def test_any_arm_reduced_contains_min_win_rate_when_structurally_unreachable():
    observed = {"min_win_rate": [0.0] * 20}
    decision = resolve_any_arm_policy(CFG, observed, n_evaluated=20)
    assert decision["dropped_clauses"] == ["min_win_rate"]
    assert decision["any_arm_decision"] == "dropped"


def test_reachable_clause_is_neither_dropped_nor_recalibrated():
    # p99 der Beobachtungen liegt UEBER der konfigurierten Schwelle -> nichts zu tun.
    observed = {"min_win_rate": [0.30] * 20}
    decision = resolve_any_arm_policy(CFG, observed, n_evaluated=20)
    assert decision["dropped_clauses"] == []
    assert decision["recalibrated_thresholds"] == {}
    assert decision["any_arm_decision"] is None


# ── recalibrate bleibt als Option, aber OHNE Floor + mit WARNUNG ───────────────────────────────────
def test_recalibrate_no_longer_applies_a_floor():
    """Vor #812 waere die rekalibrierte Schwelle max(p99, 0.05) = 0.05 gewesen -- jetzt das rohe
    p99, auch wenn es (weit) unter dem ehemaligen Floor liegt."""
    tcfg = {**CFG, "any_arm_unreachable_policy": "recalibrate"}
    observed = {"min_win_rate": [0.01] * 20}  # p99 == 0.01, deutlich unter dem alten 0.05-Floor
    decision = resolve_any_arm_policy(tcfg, observed, n_evaluated=20)
    assert decision["recalibrated_thresholds"]["oos_min_win_rate"] == pytest.approx(0.01)


def test_recalibrate_warns_that_no_cross_study_dsr_comparison_is_valid(caplog):
    tcfg = {**CFG, "any_arm_unreachable_policy": "recalibrate"}
    observed = {"min_win_rate": [0.01] * 20}
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        resolve_any_arm_policy(tcfg, observed, n_evaluated=20)
    assert any("#812" in r.message and "DSR-Vergleich" in r.message for r in caplog.records)


def test_drop_arm_emits_no_recalibrate_warning(caplog):
    observed = {"min_win_rate": [0.0] * 20}
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        resolve_any_arm_policy(CFG, observed, n_evaluated=20)
    assert not any("DSR-Vergleich" in r.message for r in caplog.records)


# ── _effective_gate_thresholds ──────────────────────────────────────────────────────────────────
def test_effective_thresholds_include_all_conjunction_keys_with_their_config_values():
    tcfg = {"eligible_requires_all": ["min_trades", "max_drawdown"], "min_trades": 20,
            "max_drawdown": 0.3}
    assert _effective_gate_thresholds(tcfg) == {"min_trades": 20, "max_drawdown": 0.3}


def test_effective_thresholds_drop_arm_removes_the_dropped_clause():
    tcfg = {"eligible_requires_any": ["min_profit_factor", "min_win_rate"],
            "min_profit_factor": 1.1, "min_win_rate": 0.15}
    policy = {"dropped_clauses": ["min_win_rate"], "recalibrated_thresholds": {}}
    effective = _effective_gate_thresholds(tcfg, policy)
    assert effective == {"min_profit_factor": 1.1}


def test_effective_thresholds_recalibrate_overrides_the_config_value():
    tcfg = {"eligible_requires_any": ["min_win_rate"], "min_win_rate": 0.15}
    policy = {"dropped_clauses": [], "recalibrated_thresholds": {"oos_min_win_rate": 0.01}}
    effective = _effective_gate_thresholds(tcfg, policy)
    assert effective == {"min_win_rate": 0.01}


# ── selection_rule_fingerprint: deterministisch + sensitiv gegenueber der EFFEKTIVEN Regel ─────────
def test_fingerprint_is_deterministic():
    tcfg = {"eligible_requires_all": ["min_trades"], "min_trades": 20}
    assert selection_rule_fingerprint(tcfg) == selection_rule_fingerprint(tcfg)


def test_fingerprint_matches_manual_sha256_over_the_effective_thresholds():
    tcfg = {"eligible_requires_all": ["min_trades"], "min_trades": 20}
    effective = _effective_gate_thresholds(tcfg)
    expected = hashlib.sha256(json.dumps(effective, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    assert selection_rule_fingerprint(tcfg) == expected


def test_fingerprint_differs_when_a_clause_is_dropped():
    tcfg = {"eligible_requires_any": ["min_profit_factor", "min_win_rate"],
            "min_profit_factor": 1.1, "min_win_rate": 0.15}
    baseline = selection_rule_fingerprint(tcfg)
    dropped = selection_rule_fingerprint(
        tcfg, {"dropped_clauses": ["min_win_rate"], "recalibrated_thresholds": {}})
    assert baseline != dropped


def test_fingerprint_differs_when_a_threshold_is_recalibrated():
    tcfg = {"eligible_requires_any": ["min_win_rate"], "min_win_rate": 0.15}
    baseline = selection_rule_fingerprint(tcfg)
    recalibrated = selection_rule_fingerprint(
        tcfg, {"dropped_clauses": [], "recalibrated_thresholds": {"oos_min_win_rate": 0.01}})
    assert baseline != recalibrated


def test_fingerprint_identical_across_two_studies_with_the_same_effective_policy():
    """Zwei Studies desselben Symbols, bei denen dieselbe Klausel unter derselben Policy dieselbe
    Entscheidung trifft, tragen denselben Fingerprint (duerfen in derselben Familie gezaehlt werden)."""
    tcfg = {"eligible_requires_any": ["min_win_rate"], "min_win_rate": 0.15}
    policy_a = {"dropped_clauses": ["min_win_rate"], "recalibrated_thresholds": {}}
    policy_b = {"dropped_clauses": ["min_win_rate"], "recalibrated_thresholds": {}}
    assert selection_rule_fingerprint(tcfg, policy_a) == selection_rule_fingerprint(tcfg, policy_b)


# ── report._study_record: Fingerprint wird aus study.user_attrs durchgereicht ──────────────────────
class _T:
    def __init__(self, evaluated=True, eligible=False):
        self.value = 1.0
        self.user_attrs = {"oos_evaluated": evaluated, "oos_eligible": eligible}


class _Study:
    def __init__(self, trials, fingerprint=None):
        self.trials = trials
        self.best_value = 1.0
        self.user_attrs = {} if fingerprint is None else {"selection_rule_fingerprint": fingerprint}


def test_study_record_exposes_the_stamped_fingerprint():
    proposal = {"symbol": "BTC.ETORO", "strategy": "TrendFollow"}
    study = _Study([_T()], fingerprint="abc123")
    record, _checks = _study_record(proposal, study, CFG)
    assert record["selection_rule_fingerprint"] == "abc123"


def test_study_record_fingerprint_is_none_for_a_pre_812_study():
    proposal = {"symbol": "BTC.ETORO", "strategy": "TrendFollow"}
    study = _Study([_T()])  # kein user_attr gestempelt (Alt-Lauf)
    record, _checks = _study_record(proposal, study, CFG)
    assert record["selection_rule_fingerprint"] is None


# ── report._selection_rule_families: Gruppierung je Symbol nach Fingerprint ────────────────────────
def test_selection_rule_families_groups_by_symbol_and_fingerprint():
    studies_out = [
        {"symbol": "BTC.ETORO", "selection_rule_fingerprint": "fp1", "n_evaluable": 40},
        {"symbol": "BTC.ETORO", "selection_rule_fingerprint": "fp1", "n_evaluable": 60},
        {"symbol": "BTC.ETORO", "selection_rule_fingerprint": "fp2", "n_evaluable": 20},
        {"symbol": "ETH.ETORO", "selection_rule_fingerprint": "fp3", "n_evaluable": 10},
    ]
    families = _selection_rule_families(studies_out)
    assert families == {
        "BTC.ETORO": {"fp1": 100, "fp2": 20},
        "ETH.ETORO": {"fp3": 10},
    }


def test_selection_rule_families_missing_fingerprint_falls_back_to_unknown_bucket():
    studies_out = [{"symbol": "BTC.ETORO", "selection_rule_fingerprint": None, "n_evaluable": 5}]
    assert _selection_rule_families(studies_out) == {"BTC.ETORO": {"unknown": 5}}


# ── sweep._family_n_from_studies: unveraendertes Interface + #812-Heterogenitaets-Warnung ─────────
def _sweep_study(n_evaluated, fingerprint):
    trials = [_T(evaluated=True) for _ in range(n_evaluated)]
    s = _Study(trials, fingerprint=fingerprint)
    return s


def test_family_n_from_studies_interface_unchanged_per_symbol_scalar():
    pairs = [("StratA", "TSLA.ETORO", "OK"), ("StratB", "TSLA.ETORO", "OK")]
    studies = [_sweep_study(50, "fp1"), _sweep_study(30, "fp1")]
    family_n = sweep._family_n_from_studies(
        pairs, studies, tournament_cfg={"deflation_family_floor_mode": "attempted"})
    assert family_n["TSLA.ETORO"] == 80


def test_family_n_from_studies_warns_on_heterogeneous_fingerprints(caplog):
    pairs = [("StratA", "TSLA.ETORO", "OK"), ("StratB", "TSLA.ETORO", "OK")]
    studies = [_sweep_study(50, "fp1"), _sweep_study(30, "fp2")]
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        sweep._family_n_from_studies(
            pairs, studies, tournament_cfg={"deflation_family_floor_mode": "attempted"})
    assert any("#812" in r.message and "TSLA.ETORO" in r.message for r in caplog.records)


def test_family_n_from_studies_no_warning_when_fingerprints_match(caplog):
    pairs = [("StratA", "TSLA.ETORO", "OK"), ("StratB", "TSLA.ETORO", "OK")]
    studies = [_sweep_study(50, "fp1"), _sweep_study(30, "fp1")]
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        sweep._family_n_from_studies(
            pairs, studies, tournament_cfg={"deflation_family_floor_mode": "attempted"})
    assert not any("#812" in r.message for r in caplog.records)
