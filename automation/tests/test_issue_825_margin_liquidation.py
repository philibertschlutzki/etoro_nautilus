"""Issue #825 (P2, Katalog #822-#827, GitHub-Issue #750) — MARGIN-Konto ohne Liquidations-/
Negativsaldo-Modell: ``PortfolioMonitor`` ist ein reiner Beobachter (kein Order-Router), eine echte
Margin-Call-/Zwangsliquidations-Simulation innerhalb der nautilus_trader-Ausführungs-Engine würde
Order-/Fill-/PnL-Aggregation an einer hochriskanten Stelle verändern, ohne dass dieses Environment
über einen realen Marktdaten-Katalog verfügt, um sie zu integrationstesten.

Scope-Entscheidung (dokumentiert, analog zum #823-T-adaptiven-Guard- und #824-H0-Aufschub-Muster):
ein Trial, dessen Equity-Kurve während des OOS-Fensters nicht-positiv wird (``assert_positive_
equity``/``EQUITY_NONPOSITIVE``, #801), ist bereits über ``REJECT_OOS_INVALID_METRICS`` (#801/#803,
_evaluate_oos_eligibility, backtest_runner.py) UNBEDINGT von ``oos_eligible`` und damit von jeder
Promotion ausgeschlossen — unabhängig davon, ob eine spätere Order in Wahrheit durch einen Margin-
Call zwangsglattgestellt worden wäre. Diese Tests dokumentieren/verankern diesen bereits bestehenden
Ausschluss als Regressionswächter und fügen die neue Report-Telemetrie ``liquidated_trials``
(_study_record, report.py) hinzu, die die HÄUFIGKEIT ökonomisch ruinierter Trials je Study sichtbar
macht, ohne die #804-Zähl-Logik zu duplizieren.

Explizit AUSSERHALB des Scopes dieses Fixes (siehe AGENTS.md #825-Eintrag): eine echte
Margin-Call-/Zwangsliquidations-Simulation innerhalb der nautilus_trader BacktestEngine
(Order-Submission durch einen Beobachter-Actor) sowie die NOK-Sizing-Defekt-Diagnose aus der
Ursprungs-Issue — beide erfordern reale Marktdaten bzw. Ausführungs-Engine-Integrationstests, die in
diesem Sandbox-Environment nicht verfügbar sind.
"""
from automation.backtest_runner import _evaluate_oos_eligibility
from automation.optimizer.report import _study_record


# ── backtest_runner._evaluate_oos_eligibility: equity_ruined ist bereits ein UNBEDINGTES Gate ────
def test_equity_ruined_trial_is_never_oos_eligible():
    oos_metrics = {
        "equity_ruined": True, "sortino_ratio": 5.0, "max_drawdown": 0.01,
        "total_trades": 20, "win_rate": 1.0, "total_return": 10.0, "expectancy": 1.0,
    }
    result = _evaluate_oos_eligibility(oos_metrics, tournament_cfg={})
    assert result["oos_eligible"] is False
    assert any("REJECT_OOS_INVALID_METRICS" in r for r in result["oos_rejection_reasons"])


def test_non_ruined_trial_unaffected_by_the_equity_ruin_clause():
    oos_metrics = {
        "equity_ruined": False, "sortino_ratio": 1.0, "max_drawdown": 0.05,
        "total_trades": 20, "win_rate": 0.6, "total_return": 0.1, "expectancy": 0.01,
    }
    result = _evaluate_oos_eligibility(oos_metrics, tournament_cfg={})
    assert not any("REJECT_OOS_INVALID_METRICS" in r for r in result["oos_rejection_reasons"])


# ── report._study_record: liquidated_trials-Telemetrie ────────────────────────────────────────────
class _T:
    def __init__(self, *, ruined=False):
        self.value = 0.1
        diagnostics = [{"code": "EQUITY_NONPOSITIVE"}] if ruined else []
        self.user_attrs = {
            "oos_evaluated": True, "oos_eligible": not ruined,
            "inference_diagnostics": diagnostics,
        }


class _S:
    def __init__(self, trials):
        self.trials = trials
        self.best_value = 0.1
        self.user_attrs = {}


def _proposal():
    return {"symbol": "BTC.ETORO", "strategy": "TrendFollow"}


def test_liquidated_trials_counts_equity_nonpositive_diagnostics():
    study = _S([_T(ruined=True), _T(ruined=True), _T(ruined=False)])
    record, _checks = _study_record(_proposal(), study, {})
    assert record["liquidated_trials"] == 2


def test_liquidated_trials_is_zero_without_any_ruined_trial():
    study = _S([_T(ruined=False), _T(ruined=False)])
    record, _checks = _study_record(_proposal(), study, {})
    assert record["liquidated_trials"] == 0


def test_liquidated_trials_matches_inference_diagnostics_by_code_alias():
    """#825 fügt keine neue Zähl-Logik hinzu -- ``liquidated_trials`` ist ein reiner Alias auf den
    bereits von #804 aggregierten ``inference_diagnostics_by_code['EQUITY_NONPOSITIVE']``-Zähler."""
    study = _S([_T(ruined=True), _T(ruined=False), _T(ruined=False)])
    record, _checks = _study_record(_proposal(), study, {})
    assert record["liquidated_trials"] == record["inference_diagnostics_by_code"].get(
        "EQUITY_NONPOSITIVE", 0)
