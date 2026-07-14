"""Issue #617 — Sortino-/PF-``None``-Bypass im OOS-Gate schliessen.

``oos_min_trades = 1`` machte den ``None``-Guard in ``_evaluate_oos_eligibility`` VAKUANT: ein Trial
mit 9 OOS-Trades und 0 Verlusten (``sortino=None``, ``profit_factor=None``, ``win_rate>0``) passierte
``min_sortino`` UND ``min_profit_factor`` GRATIS — genau der »die Bewertung löschen statt Performance
liefern«-Kanal, den #590 schliessen sollte.

Fix (#617):
1. Der ``None``-Guard nutzt ``req_trades_guard = max(oos_min_trades, sortino_min_trades)`` — er darf
   nicht schwächer sein als die Bedingung, unter der die Kennzahl überhaupt DEFINIERT ist.
2. ``oos_min_trades`` von 1 auf 20 gehoben (>= ``sortino_min_trades``, statistisch verteidigbar).
3. Explizite, benannte Policy ``undefined_metric_policy`` in ``tournament.json`` — KEIN impliziter Pass.
"""
import json
from pathlib import Path

import pytest

from automation.backtest_runner import _evaluate_oos_eligibility

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def _oos(n_trades, *, sortino=None, pf=None, win_rate=1.0, total_return=0.10,
         expectancy=0.02, max_dd=0.05):
    return {
        "total_trades": n_trades, "max_drawdown": max_dd, "win_rate": win_rate,
        "total_return": total_return, "expectancy": expectancy,
        "sortino_ratio": sortino, "profit_factor": pf,
        "median_position_notional": 1000.0,
    }


def _cfg(**over):
    cfg = {
        "oos_min_trades": 1, "sortino_min_trades": 10,
        "oos_min_total_return": 0.0, "oos_min_expectancy": 0.0, "oos_min_win_rate": 0.0,
        "oos_min_sortino": 0.43, "oos_min_profit_factor": 1.1,
        "undefined_metric_policy": "fallback_total_return",
        "eligible_requires_all": ["min_trades", "min_sortino"],
        "eligible_requires_any": ["min_profit_factor", "min_win_rate"],
    }
    cfg.update(over)
    return cfg


# ── Akzeptanzkriterium: 9 Trades, 0 Verluste, win_rate=1.0 ⇒ nicht eligible (insufficient) ───────
def test_nine_trades_zero_losses_rejected_insufficient():
    """Der Kern-Reward-Hacking-Fall: 9 OOS-Trades, losses_count=0, win_rate=1.0.

    ``oos_min_trades=1`` (lax), aber ``sortino_min_trades=10`` ⇒ Guard = max(1,10) = 10 > 9 ⇒ das
    Gate MUSS scheitern, obwohl ``undefined_metric_policy='fallback_total_return'`` und
    ``total_return>0`` (die Stichprobe ist zu klein — der Sortino ist NICHT definierbar)."""
    ev = _evaluate_oos_eligibility(_oos(9, sortino=None, pf=None, win_rate=1.0, total_return=0.10),
                                   _cfg())
    assert ev["oos_eligible"] is False
    assert any("oos_min_sortino: None (insufficient)" in r for r in ev["oos_rejection_reasons"]), \
        ev["oos_rejection_reasons"]


# ── Invariante: kein Trial mit oos_total_trades < sortino_min_trades wird je eligible ────────────
@pytest.mark.parametrize("n", list(range(1, 10)))
def test_no_trial_below_sortino_min_trades_is_eligible(n):
    """Für JEDES n < sortino_min_trades (=10) mit undefiniertem Sortino/PF ist das Gate zu."""
    ev = _evaluate_oos_eligibility(
        _oos(n, sortino=None, pf=None, win_rate=1.0, total_return=0.5, max_dd=0.01), _cfg())
    assert ev["oos_eligible"] is False, f"n={n} darf nicht eligible sein"


# ── Policy 'fallback_total_return': verlustfreier, profitabler Fold MIT ausreichender Stichprobe ──
def test_fallback_policy_passes_zero_loss_with_sufficient_sample():
    """n=25 >= guard(10), sortino=None (verlustfrei), total_return>0 ⇒ die min_sortino-Klausel gilt
    unter 'fallback_total_return' als erfüllt (Parität zu reward.py/oos_sortino_fallback)."""
    cfg = _cfg(undefined_metric_policy="fallback_total_return")
    ev = _evaluate_oos_eligibility(
        _oos(25, sortino=None, pf=2.0, win_rate=1.0, total_return=0.10), cfg)
    assert ev["oos_eligible"] is True, ev["oos_rejection_reasons"]


def test_fallback_policy_rejects_zero_loss_with_nonpositive_return():
    """Selbst mit ausreichender Stichprobe: total_return <= 0 ⇒ auch der Fallback lässt NICHT durch."""
    cfg = _cfg(undefined_metric_policy="fallback_total_return")
    ev = _evaluate_oos_eligibility(
        _oos(25, sortino=None, pf=2.0, win_rate=1.0, total_return=-0.01), cfg)
    assert ev["oos_eligible"] is False
    assert any("fallback_total_return" in r for r in ev["oos_rejection_reasons"])


# ── Policy 'fail': KEIN impliziter Pass, auch bei ausreichender Stichprobe ────────────────────────
def test_policy_fail_no_implicit_pass():
    """undefined_metric_policy='fail' ⇒ ein undefinierter Sortino (auch bei n=25) scheitert."""
    cfg = _cfg(undefined_metric_policy="fail")
    ev = _evaluate_oos_eligibility(
        _oos(25, sortino=None, pf=2.0, win_rate=1.0, total_return=0.10), cfg)
    assert ev["oos_eligible"] is False
    assert any("undefined_metric_policy=fail" in r for r in ev["oos_rejection_reasons"])


def test_policy_defaults_to_fail_when_absent():
    """Fehlt der Key ⇒ 'fail' (strengster, sicherster Default): kein impliziter Pass."""
    cfg = _cfg()
    cfg.pop("undefined_metric_policy")
    ev = _evaluate_oos_eligibility(
        _oos(25, sortino=None, pf=2.0, win_rate=1.0, total_return=0.10), cfg)
    assert ev["oos_eligible"] is False


# ── Profit-Factor-Guard analog zum Sortino-Guard (#617, "Analog für pf_valid") ───────────────────
def test_pf_guard_symmetric_insufficient():
    """9 Trades, PF=None (0 Verluste) ⇒ auch der PF-Guard greift mit derselben Guard-Schwelle."""
    cfg = _cfg(eligible_requires_all=["min_trades", "min_profit_factor"],
               eligible_requires_any=[])
    ev = _evaluate_oos_eligibility(_oos(9, sortino=1.0, pf=None, win_rate=1.0), cfg)
    assert ev["oos_eligible"] is False
    assert any("oos_min_profit_factor: None (insufficient)" in r
               for r in ev["oos_rejection_reasons"]), ev["oos_rejection_reasons"]


# ── Config-Härtung (#617) ────────────────────────────────────────────────────────────────────────
def test_config_oos_min_trades_raised_and_policy_present():
    assert TCFG["oos_min_trades"] >= TCFG["sortino_min_trades"], \
        "oos_min_trades muss >= sortino_min_trades sein (#617)"
    assert TCFG["oos_min_trades"] == 20, "oos_min_trades auf 20 gehoben (#617)"
    assert TCFG["undefined_metric_policy"] in ("fail", "fallback_total_return")
    doc = TCFG["_schema"]["fields"]
    assert "undefined_metric_policy" in doc and "oos_min_trades" in doc, \
        "Schema muss oos_min_trades UND undefined_metric_policy dokumentieren (#617)"


def test_production_config_end_to_end_rejects_9_trade_zero_loss():
    """Mit der ECHTEN tournament.json (oos_min_trades=20, sortino_min_trades=10): ein 9-Trade-
    Zero-Loss-Trial ist weder über min_trades noch über min_sortino eligible."""
    cfg = dict(TCFG)
    ev = _evaluate_oos_eligibility(
        _oos(9, sortino=None, pf=None, win_rate=1.0, total_return=0.10, expectancy=0.02), cfg)
    assert ev["oos_eligible"] is False
