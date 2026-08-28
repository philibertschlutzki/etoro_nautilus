"""Issue #1306 (GH #1183, P1) — Randlösungs-Erkennung aus einer degenerierten Kohorte.

Symptom. ``report._study_record`` prüft ``winner_outside_default_bounds_after_override``
ausschliesslich gegen die Parameter des GEWINNER-Trials, ohne zu prüfen, ob die Kohorte
überhaupt informativ ist. Bei ``n_evaluable == 0`` oder ``reward_std_total == 0`` (jeder Trial
trägt denselben Reward, z. B. weil alle ``penalty_unevaluable_oos`` unbedingt tragen) ist "der
Gewinner" per Optuna-Konstruktion nur der ERSTE Trial in Iterationsreihenfolge einer All-Tie-
Kohorte — eine daraus abgeleitete Randlösung ist ein Tie-Break-Artefakt, keine Suchraum-Aussage.

Fix. Die Erkennung wird vorab gegated: liegt ``n_evaluable == 0`` ODER ist ``reward_std_total ==
0``, wird ``boundary_solutions`` für diese Study NICHT befüllt; stattdessen
``boundary_resolution_skipped_reason = "DEGENERATE_COHORT"``. ``report._boundary_solutions_
section`` überspringt solche Studies GANZ (auch bei ``boundary_hit_fraction > 0.3`` — dieselbe
Tie-Break-Artefakt-Aussage). ``summary_de``-§5.3 zählt übersprungene Studies getrennt aus.
"""
from automation.optimizer import report
from automation.optimizer.report import _boundary_solutions_section, _study_record


# ── report._study_record: Vorab-Gate ─────────────────────────────────────────────────────────────

class _T:
    def __init__(self, value, params, user_attrs):
        self.value = value
        self.params = params
        self.user_attrs = user_attrs


class _S:
    def __init__(self, trials):
        self.trials = trials
        self.best_value = max(t.value for t in trials)
        self.user_attrs = {}


def test_skipped_when_n_evaluable_is_zero():
    """0 evaluable Trials (oos_evaluated=False) — der Gewinner traegt trotzdem einen ausserhalb
    des Default-Suchbands liegenden Parameter (ema_period=18, Default-Band (50, 300))."""
    study = _S([_T(1.0, {"ema_period": 18}, {"oos_evaluated": False})])
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TrendPullbackStrategy"}
    record, _checks = _study_record(proposal, study)
    assert record["n_evaluable"] == 0
    assert record["winner_outside_default_bounds_after_override"] is None
    assert record["boundary_resolution_skipped_reason"] == "DEGENERATE_COHORT"


def test_skipped_when_reward_std_total_is_zero():
    """Zwei evaluierte Trials mit IDENTISCHEN reward_terms (reward_std_total == 0) — der
    'Gewinner' (erster Trial bei Tie) traegt einen ausserhalb liegenden Parameter."""
    attrs = {"oos_evaluated": True, "oos_eligible": True, "reward_terms": {"base": 1.0}}
    study = _S([
        _T(1.0, {"ema_period": 18}, dict(attrs)),
        _T(1.0, {"ema_period": 20}, dict(attrs)),
    ])
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TrendPullbackStrategy"}
    record, _checks = _study_record(proposal, study)
    assert record["reward_std_total"] == 0.0
    assert record["winner_outside_default_bounds_after_override"] is None
    assert record["boundary_resolution_skipped_reason"] == "DEGENERATE_COHORT"


def test_not_skipped_for_an_informative_cohort():
    """Regressionsschutz: eine informative Kohorte (>= 2 evaluierte Trials, unterschiedliche
    reward_terms) bleibt unveraendert — Randlösung wird weiterhin erkannt, kein Skip-Grund."""
    study = _S([
        _T(1.0, {"ema_period": 18}, {
            "oos_evaluated": True, "oos_eligible": True, "reward_terms": {"base": 1.0}}),
        _T(0.5, {"ema_period": 120}, {
            "oos_evaluated": True, "oos_eligible": True, "reward_terms": {"base": 0.5}}),
    ])
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TrendPullbackStrategy"}
    record, _checks = _study_record(proposal, study)
    assert record["boundary_resolution_skipped_reason"] is None
    assert "ema_period" in record["winner_outside_default_bounds_after_override"]


# ── report._boundary_solutions_section: übersprungene Studies fehlen GANZ ───────────────────────

def _study_dict(strategy, symbol, *, skipped=None, override=None, fraction=None):
    return {
        "strategy": strategy, "symbol": symbol,
        "boundary_resolution_skipped_reason": skipped,
        "winner_outside_default_bounds_after_override": override,
        "boundary_hit_fraction": fraction,
        "boundary_parameter": next(iter(override), None) if override else None,
        "boundary_side": "low",
        "boundary_veto_evidence": {"p": {"direction": "low"}} if override else None,
    }


def test_boundary_solutions_section_excludes_degenerate_cohort_study(monkeypatch):
    monkeypatch.setattr(report, "_diagnosed_pairs_all", lambda: [])
    studies = [
        _study_dict("TrendPullbackStrategy", "TSLA.ETORO", skipped="DEGENERATE_COHORT",
                    override={"ema_period": [18, [50, 300]]}, fraction=0.9),
        _study_dict("SqueezeBreakoutStrategy", "NVDA.ETORO",
                    override={"max_bars_in_trade": [6, [12, 24]]}, fraction=0.111),
    ]
    result = _boundary_solutions_section(studies)
    names = {(r["strategy"], r["symbol"]) for r in result}
    assert names == {("SqueezeBreakoutStrategy", "NVDA.ETORO")}


def test_boundary_solutions_section_excludes_even_on_high_fraction_alone(monkeypatch):
    """Auch OHNE ``winner_outside_default_bounds_after_override`` (nur ``boundary_hit_fraction >
    0.3``) bleibt eine als degeneriert markierte Study aussen vor — dieselbe Tie-Break-Aussage."""
    monkeypatch.setattr(report, "_diagnosed_pairs_all", lambda: [])
    studies = [_study_dict("S", "A.ETORO", skipped="DEGENERATE_COHORT", fraction=0.8)]
    assert _boundary_solutions_section(studies) == []
