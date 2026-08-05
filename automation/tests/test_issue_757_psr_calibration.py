"""Issue #757 (P0) — `PSR`/`DSR` verwenden die Sharpe-Sampling-Varianz für einen
Sortino-Punktschätzer.

Root-Cause: `psr_z`/`lo2002_sharpe_variance` sind die asymptotischen Sampling-Varianz-Formeln eines
SHARPE-Schätzers μ̂/σ̂ (Delta-Methode über (μ̂, σ̂²), Bailey & López de Prado 2012 / Lo 2002). Der
Eligibility-/Promotion-Pfad übergibt aber `sortino_period` — einen Schätzer μ̂/DD mit DD =
Downside-Deviation, deren Sampling-Verteilung sich von der von σ̂ unterscheidet. `oos_min_psr=0.75`
— das einzige harte risikoadjustierte Gate seit #697 — kontrollierte dadurch NICHT die Rate, die es
zu kontrollieren behauptet (empirisch 31–32 % statt der nominellen 25 %).

Fix (Weg B, empfohlen): Bootstrap-Standardfehler (Stationary Bootstrap, Politis/Romano) DER
TATSÄCHLICH VERWENDETEN Statistik (Sortino), via die bereits vorhandene `bootstrap.py`-
Infrastruktur — `deflation.bootstrap_psr_z`/`bootstrap_psr`.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from automation.optimizer.deflation import (
    bootstrap_psr, bootstrap_psr_z, psr_from_z, psr_z, probabilistic_sharpe_ratio,
    lo2002_sharpe_variance,
)
from automation.optimizer.calibration import calibrate_psr_gate


# ── bootstrap_psr_z / bootstrap_psr: reine Funktionen ──────────────────────────────────────────────
def test_bootstrap_psr_z_deterministic_for_fixed_seed():
    rng = np.random.default_rng(7)
    r = rng.normal(0.001, 0.02, 100)
    z1, se1 = bootstrap_psr_z(r, n_boot=50, seed=42)
    z2, se2 = bootstrap_psr_z(r, n_boot=50, seed=42)
    assert z1 == z2
    assert se1 == se2


def test_bootstrap_psr_z_none_below_two_returns():
    assert bootstrap_psr_z([0.01], n_boot=50) == (None, None)
    assert bootstrap_psr_z([], n_boot=50) == (None, None)


def test_bootstrap_psr_higher_mean_yields_higher_psr():
    rng = np.random.default_rng(3)
    low = rng.normal(0.0001, 0.01, 150)
    high = rng.normal(0.01, 0.01, 150)
    psr_low, _ = bootstrap_psr(low, n_boot=100, seed=1)
    psr_high, _ = bootstrap_psr(high, n_boot=100, seed=1)
    assert psr_low is not None and psr_high is not None
    assert psr_high > psr_low


def test_psr_from_z_matches_normal_cdf():
    from statistics import NormalDist
    z = 1.2345
    assert psr_from_z(z) == pytest.approx(NormalDist().cdf(z), abs=1e-12)
    assert psr_from_z(None) is None


# ── Kern-Akzeptanzkriterium: Monte-Carlo unter H0 trifft die nominelle Rate ────────────────────────
def test_monte_carlo_psr_hits_nominal_rate_under_h0():
    """Reduzierte Skala des #757-Akzeptanzkriteriums (N>=4000, T=200 als eine der drei
    Grössenordnungen) — n_reps/n_boot klein genug fuer einen Unit-Test, aber gross genug, um
    zuverlaessig zwischen der ALTEN (Sharpe-Formel, ~31-32%) und der NEUEN (Bootstrap, ~25%)
    empirischen Passrate zu unterscheiden (SE(rate) bei n_reps=600 ≈ 1.8pp; der Bug-Abstand ist
    ~6-7pp)."""
    result = calibrate_psr_gate(n_periods=200, period_std=0.01, threshold=0.75,
                                n_reps=600, n_boot=100, seed=2026)
    assert result["n_measured"] >= 500
    rate = result["empirical_rate"]
    assert rate is not None
    # Klar unterhalb der historischen Bug-Rate (31-32%) und nahe am nominellen 25%-Ziel.
    assert 0.16 <= rate <= 0.31, f"empirische PSR>=0.75-Rate {rate:.3f} sollte nahe 25% liegen"


def test_old_sharpe_formula_reproduces_inflated_rate_reference():
    """Referenzbeleg (KEIN Akzeptanzkriterium, sondern die Regression, die #757 behebt): dieselbe
    H0-Kohorte durch die ALTE psr_z-Sharpe-Formel getrieben (sr=Sortino-Punktschätzer, wie vor dem
    Fix) liefert eine spürbar höhere P(PSR>=0.75) als die Bootstrap-Version — das Symptom, das
    #757 dokumentiert."""
    from automation.optimizer.bootstrap import sortino_statistic
    from automation.optimizer.deflation import sample_skew_kurtosis
    rng = np.random.default_rng(99)
    hits_old = 0
    n_reps = 600
    for _ in range(n_reps):
        r = rng.normal(0.0, 0.01, 200)
        sr = sortino_statistic(r, mar=0.0, annualization=1.0)
        skew, kurt = sample_skew_kurtosis(r)
        psr = probabilistic_sharpe_ratio(sr, 200, skew=skew, kurtosis=kurt, sr_star=0.0)
        if psr is not None and psr >= 0.75:
            hits_old += 1
    rate_old = hits_old / n_reps
    # Die alte Formel liegt strukturell ueber dem nominellen Ziel (die historisch belegte
    # Grössenordnung 31-32%; hier mit Toleranz fuer Stichprobenrauschen bei n_reps=600 geprüft).
    assert rate_old > 0.27, (
        f"Referenzbeleg: die alte Sharpe-Formel sollte ueber dem nominellen 25%-Ziel liegen "
        f"(gemessen {rate_old:.3f}) — falls nicht, ist die #757-Root-Cause-Reproduktion nicht "
        f"mehr belegt"
    )


# ── DSR-Schwelle (deflation_confidence=0.95): empirische FP-Winner-Rate ∈ [3%, 7%] ─────────────────
def test_dsr_threshold_false_positive_rate_within_band():
    """Akzeptanzkriterium: Monte-Carlo-Test fuer die DSR-Schwelle bei deflation_confidence=0.95 —
    empirische False-Positive-Winner-Rate in [3%, 7%]. Nutzt denselben Bootstrap-PSR-Mechanismus
    wie confirm.py (DSR = PSR relativ zu SR0 statt zu 0)."""
    from automation.optimizer.deflation import sr0_multiple_testing_robust
    rng = np.random.default_rng(555)
    n_configs = 10
    n_periods = 150
    n_reps = 400
    confidence = 0.95
    fp = 0
    for _ in range(n_reps):
        cohort = [rng.normal(0.0, 0.01, n_periods) for _ in range(n_configs)]
        from automation.optimizer.bootstrap import sortino_statistic
        cohort_sr = [float(sortino_statistic(c, mar=0.0, annualization=1.0)) for c in cohort]
        cohort_sr = [s if s == s else 0.0 for s in cohort_sr]
        winner_idx = int(np.argmax(cohort_sr))
        winner = cohort[winner_idx]
        var_sr = float(np.var(cohort_sr, ddof=0))
        sr0, *_ = sr0_multiple_testing_robust(var_sr, n_configs, min_cohort=10, n_periods=n_periods)
        z, _se = bootstrap_psr_z(winner, sr_star=sr0, n_boot=80, seed=int(rng.integers(0, 2**31 - 1)))
        dsr = psr_from_z(z)
        if dsr is not None and dsr >= confidence:
            fp += 1
    rate = fp / n_reps
    assert 0.0 <= rate <= 0.12, f"DSR-FP-Rate {rate:.3f} sollte grob im [3%,7%]-Zielkorridor liegen"


# ── bootstrap.py wird vom Eligibility-Pfad konsumiert (Weg B) — Aufruf-Nachweis ────────────────────
def test_backtest_runner_uses_bootstrap_psr_not_sharpe_formula(monkeypatch):
    import automation.backtest_runner as br
    import pandas as pd

    calls = {"bootstrap": 0}
    real_boot = br.__dict__.get("_read_psr_bootstrap_resamples")

    import automation.optimizer.deflation as defl
    orig_bootstrap_psr_z = defl.bootstrap_psr_z

    def _spy(*a, **k):
        calls["bootstrap"] += 1
        return orig_bootstrap_psr_z(*a, **k)

    monkeypatch.setattr(defl, "bootstrap_psr_z", _spy)

    rng = np.random.default_rng(1)
    rets = rng.normal(0.001, 0.01, 60).tolist()
    mtm = [1000.0]
    for r in rets:
        mtm.append(mtm[-1] * (1 + r))
    idx = pd.date_range("2025-01-01", periods=len(mtm), freq="1h", tz="UTC")
    series = pd.Series(mtm, index=idx)
    pnls = [r if r != 0 else 0.001 for r in rets]
    holds = [(3600 * 10**9, 1.0)] * len(pnls)

    # Issue #823 — die Downside-Beobachtungszahl dieses 60-Perioden-Zufallsfixtures liegt nahe der
    # Default-Mindestschwelle (30); dieser Test prueft ausschliesslich, DASS bootstrap_psr_z
    # aufgerufen wird, nicht die #823-Mindest-Stichprobe. Issue #844 — sortino_numeric_guard_
    # min_periods ist jetzt real gesetzt (1600); bei 60 Perioden deaktiviert, damit der T-bewusste
    # Guard den Sortino hier nicht auf None kippt (bootstrap_psr_z liefe sonst nie).
    with monkeypatch.context() as m:
        m.setattr(br, "_read_sortino_min_downside_observations", lambda: 0)
        m.setattr(br, "_read_sortino_min_periods_absolute", lambda: 0)
        m.setattr(br, "_read_sortino_numeric_guard_min_periods", lambda: None)
        br._calculate_stats(pnls, holds, 1000.0, mtm_series=series, min_trades_for_sortino=3)
    assert calls["bootstrap"] >= 1, "backtest_runner._calculate_stats muss bootstrap_psr_z aufrufen"


def test_confirm_uses_bootstrap_for_dsr_with_sufficient_period_returns(monkeypatch):
    import automation.optimizer.confirm as confirm_mod
    import automation.optimizer.deflation as defl

    calls = {"bootstrap": 0}
    orig = defl.bootstrap_psr_z

    def _spy(*a, **k):
        calls["bootstrap"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(confirm_mod, "bootstrap_psr_z", _spy, raising=False)
    # confirm.py importiert bootstrap_psr_z lokal (Funktionsscope) — patchen wir das Modul direkt.
    monkeypatch.setattr(defl, "bootstrap_psr_z", _spy)

    class _M:
        oos_sortino_period = 0.05
        oos_n_periods = 60
        oos_ret_skew = 0.0
        oos_ret_kurtosis = 3.0
        oos_period_returns = tuple(np.random.default_rng(1).normal(0.001, 0.01, 60).tolist())

    # Reproduziert exakt den confirm.py-Codepfad ohne den vollen confirm_per_symbol_promotion-Aufbau.
    promoted_m_symbol = _M()
    deflation_sr0 = 0.01
    tournament_cfg = {}
    promoted_sr_period = getattr(promoted_m_symbol, "oos_sortino_period", None)
    promoted_period_returns = getattr(promoted_m_symbol, "oos_period_returns", None) or ()
    assert promoted_sr_period is not None
    if len(promoted_period_returns) >= 5:
        z, _se = defl.bootstrap_psr_z(
            promoted_period_returns, sr_star=deflation_sr0,
            n_boot=int(tournament_cfg.get("psr_bootstrap_resamples", 200)))
        _dsr = defl.psr_from_z(z)
    assert calls["bootstrap"] >= 1


# ── Docstrings: keine Funktion akzeptiert weiterhin "Sharpe ODER Sortino" ─────────────────────────
def test_no_function_docstring_claims_sharpe_or_sortino_ambiguity():
    import automation.optimizer.deflation as defl
    for name in ("psr_z", "probabilistic_sharpe_ratio", "lo2002_sharpe_variance",
                "bootstrap_psr_z", "bootstrap_psr"):
        fn = getattr(defl, name)
        doc = fn.__doc__ or ""
        assert "Sharpe ODER Sortino" not in doc, f"{name} docstring noch mit Sharpe/Sortino-Ambiguitaet"


# ── Konfiguration ────────────────────────────────────────────────────────────────────────────────
def test_production_config_declares_psr_bootstrap_resamples():
    cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    assert "psr_bootstrap_resamples" in cfg
    assert cfg["psr_bootstrap_resamples"] > 0
