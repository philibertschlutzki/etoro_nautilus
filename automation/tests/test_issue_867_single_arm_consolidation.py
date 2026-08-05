"""Issue #867 (P1, Katalog #866-#869, GitHub-Issue #760, Pitfall #279) — `eligible_requires_any`
ist nach #848 eine Ein-Element-Liste und damit eine verkleidete Konjunktions-Bedingung.

Root-Cause: nach #848s Entfernung des `min_win_rate`-OR-Arms blieb `eligible_requires_any =
["min_profit_factor"]` als einziger Arm zurück. Ein `requires_any` mit genau einem Element ist
logisch identisch zu einem `requires_all`-Eintrag (`backtest_runner._evaluate_oos_eligibility`/
`_is_eligible`: `any_valid` wird nur True, wenn DIESES eine Element besteht — bit-identisches
Gating zu `requires_all`), durchlief aber unnötig den gesamten OR-Arm-Apparat
(`any_arm_unreachable_policy`, `check_any_arm_reachability_live`, `selection_rule_fingerprint`) —
genau dieser Apparat erzeugte in fünf Katalogen (#660/#668/#678/#812/#848) Nebenwirkungen. Bei
einem einzigen Arm hätte `any_arm_unreachable_policy='drop_arm'` ihn ERSATZLOS leeren können —
ein hartes Gate wäre stillschweigend verschwunden.

Fix:
1. `min_profit_factor` nach `eligible_requires_all` verschoben, `eligible_requires_any` auf `[]`.
2. `reward.assert_eligible_requires_all_not_redundant()` läuft über das erweiterte `requires_all`.
3. `reward.resolve_any_arm_policy` bricht FAIL-LOUD ab, wenn `drop_arm` eine Ein-Element-Liste
   vollständig leeren würde — statt es still zu tun.
4. Ein leeres `eligible_requires_any` ist ein gültiger, dokumentierter Zustand (keine Warnung).
"""
import json
from pathlib import Path

import pytest

from automation.backtest_runner import _evaluate_oos_eligibility
from automation.optimizer import reward as rmod

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


# ── Config-Zustand ─────────────────────────────────────────────────────────────────────────────
def test_min_profit_factor_moved_into_requires_all():
    assert "min_profit_factor" in TCFG["eligible_requires_all"]


def test_eligible_requires_any_is_empty():
    assert TCFG["eligible_requires_any"] == []


# ── Gating-Semantik: bit-identisch vor/nach der Verschiebung ────────────────────────────────────
def _oos(*, pf, psr=0.9, trades=40, dd=0.05, total_return=0.02):
    return {
        "total_trades": trades, "max_drawdown": dd, "win_rate": 0.4,
        "total_return": total_return, "expectancy": 0.02, "sortino_ratio": 1.5, "psr": psr,
        "profit_factor": pf, "median_position_notional": 1000.0,
        "oos_folds_total": 1, "oos_fold_sortinos": [1.5],
        "oos_profitable_folds": 1,
    }


def test_low_profit_factor_trial_is_rejected_via_requires_all():
    """AK — ein Trial mit profit_factor unter der Schwelle (Default min_profit_factor=1.1) wird
    ueber das JETZT aktive requires_all-Mitglied verworfen."""
    ev = _evaluate_oos_eligibility(_oos(pf=0.9), TCFG)
    assert ev["oos_eligible"] is False
    assert any("profit_factor" in r for r in ev["oos_rejection_reasons"])


def test_high_profit_factor_trial_remains_eligible():
    ev = _evaluate_oos_eligibility(_oos(pf=1.5), TCFG)
    assert ev["oos_eligible"] is True, ev["oos_rejection_reasons"]


def test_gating_outcome_identical_to_legacy_single_element_any_config():
    """Regressionsbeleg — dieselben Trials (pf ueber/unter der Schwelle) mit der ALTEN Config-Form
    (min_profit_factor in requires_any statt requires_all) ergeben EXAKT dasselbe oos_eligible-
    Urteil: die Verschiebung ist funktional bit-identisch, nur der Codepfad aendert sich."""
    legacy_cfg = dict(TCFG)
    legacy_cfg["eligible_requires_all"] = [
        k for k in TCFG["eligible_requires_all"] if k != "min_profit_factor"]
    legacy_cfg["eligible_requires_any"] = ["min_profit_factor"]

    for pf in (0.9, 1.5):
        new_ev = _evaluate_oos_eligibility(_oos(pf=pf), TCFG)
        legacy_ev = _evaluate_oos_eligibility(_oos(pf=pf), legacy_cfg)
        assert new_ev["oos_eligible"] == legacy_ev["oos_eligible"], pf


# ── reward.assert_eligible_requires_all_not_redundant läuft ueber die neue Konjunktion ──────────
def test_redundancy_guard_runs_over_expanded_requires_all_without_crashing():
    """Reine Smoke-/Coverage-Verifikation — der #697-Waechter muss ueber die erweiterte
    requires_all-Menge (inkl. min_profit_factor) laufen koennen, ohne abzustuerzen; ein leeres
    Ergebnis (keine gemeldete Redundanz) ist zulaessig, aber nicht Gegenstand dieses Tests."""
    gate_deltas_cohort = [{
        "oos_min_trades": 10.0, "oos_max_drawdown": 0.1, "oos_min_psr": 0.05,
        "oos_min_profit_factor": 0.2,
    }]
    result = rmod.assert_eligible_requires_all_not_redundant(
        gate_deltas_cohort, TCFG["eligible_requires_all"], TCFG)
    assert isinstance(result, list)


# ── resolve_any_arm_policy: Fail-Loud gegen das vollstaendige Leeren eines Ein-Element-Arms ──────
def test_drop_arm_raises_when_single_element_arm_would_be_fully_emptied(monkeypatch):
    """AK #3 — 'drop_arm' auf einem Ein-Element-Arm, der unerreichbar ist, wirft ValueError statt
    ihn still zu leeren. Nutzt min_win_rate (der einzige aktuell in _ANY_ARM_LIVE_THRESHOLD_KEYS
    verdrahtete Klausel-Name) als Stellvertreter fuer 'irgendeine kuenftig wieder aktivierte
    Ein-Element-ANY-Klausel'."""
    cfg = {
        "any_arm_unreachable_policy": "drop_arm",
        "eligible_requires_any": ["min_win_rate"],
        "oos_min_win_rate": 0.5,
    }
    observed = {"min_win_rate": [0.1] * 20}  # p99 weit unter der Schwelle -> strukturell unerreichbar

    with pytest.raises(ValueError, match="eligible_requires_any"):
        rmod.resolve_any_arm_policy(cfg, observed, n_evaluated=20)


def test_drop_arm_does_not_raise_when_only_one_of_two_clauses_is_dropped(monkeypatch):
    """Gegenprobe — bei MEHREREN Armen darf 'drop_arm' EINEN davon entfernen, ohne den Rest zu
    verlieren; die Disjunktion bleibt (mit dem verbleibenden Arm) wirksam, kein Fail-Loud."""
    monkeypatch.setitem(rmod._ANY_ARM_LIVE_THRESHOLD_KEYS, "min_win_rate", "oos_min_win_rate")
    monkeypatch.setitem(rmod._ANY_ARM_LIVE_THRESHOLD_KEYS, "min_profit_factor", "oos_min_profit_factor")
    cfg = {
        "any_arm_unreachable_policy": "drop_arm",
        "eligible_requires_any": ["min_win_rate", "min_profit_factor"],
        "oos_min_win_rate": 0.5, "oos_min_profit_factor": 1.0,
    }
    observed = {"min_win_rate": [0.1] * 20, "min_profit_factor": [1.5] * 20}

    result = rmod.resolve_any_arm_policy(cfg, observed, n_evaluated=20)
    assert result["dropped_clauses"] == ["min_win_rate"]
    assert result["any_arm_decision"] == "dropped"


def test_empty_eligible_requires_any_produces_no_warning_or_error(caplog):
    """AK #4 — ein leeres eligible_requires_any ist ein gueltiger, dokumentierter Zustand: keine
    Exception, kein WARNING-Log."""
    cfg = {"any_arm_unreachable_policy": "drop_arm", "eligible_requires_any": []}
    result = rmod.resolve_any_arm_policy(cfg, {}, n_evaluated=20)
    assert result["dropped_clauses"] == []
    assert not any(rec.levelname == "WARNING" for rec in caplog.records)


def test_drop_arm_with_reachable_single_arm_does_not_raise():
    """Der Guard greift NUR, wenn das Element tatsaechlich gedroppt wuerde — ein erreichbarer
    Ein-Element-Arm loest keinen Fail-Loud aus."""
    cfg = {
        "any_arm_unreachable_policy": "drop_arm",
        "eligible_requires_any": ["min_win_rate"],
        "oos_min_win_rate": 0.05,
    }
    observed = {"min_win_rate": [0.1] * 20}  # p99 UEBER der Schwelle -> erreichbar
    result = rmod.resolve_any_arm_policy(cfg, observed, n_evaluated=20)
    assert result["dropped_clauses"] == []
