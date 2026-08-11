import os
import json
import urllib.request
import urllib.error

REPO = "philibertschlutzki/etoro_nautilus"

ISSUES = [
    {
        "number": 977,
        "title": "Issue #977: Adaptive Kernel Downside-Shrunk PSR Estimation ($PSR_{shrunk}$)",
        "body": """### Summary
`check_selection_statistic_availability` failed with 9 out of 14 studies below 80% PSR availability (e.g., SqueezeBreakout: 3.5%, TrendPullback: 23.1%). Small-sample trade trials (N < 30) or low-downside trials emitted None for PSR, causing structural information-free study invalidation.

### Mathematical Specification
For small or low-observation trade samples N in [5, 30), apply an Empirical Bayes Shrinkage model to sample skewness gamma_3 and kurtosis gamma_4:
- gamma_3_shrunk = alpha * gamma_3 + (1 - alpha) * 0.0
- gamma_4_shrunk = alpha * gamma_4 + (1 - alpha) * 3.0
where alpha = min(1.0, N / 30.0).

Compute PSR_shrunk(SR*):
PSR_shrunk(SR*) = Phi( (SR_hat - SR*) * sqrt(N - 1) / sqrt(1 - gamma_3_shrunk * SR_hat + ((gamma_4_shrunk - 1) / 4) * SR_hat^2) )

### Acceptance Criteria
1. PSR_shrunk is defined for 100% of OOS-evaluated trials with N_trades >= 5.
2. check_selection_statistic_availability passes with availability >= 0.95 across all studies."""
    },
    {
        "number": 978,
        "title": "Issue #978: Risk-Adjusted Expectancy Replacement ($RAE$) for Zero-Loss Trend Trials",
        "body": """### Summary
`check_selection_statistic_economic_bias` failed across 12 studies because trials without raw selection statistics had significantly higher median OOS returns (+1.01%) than trials with statistics (-0.34%, z=7.61, p=1.35e-14). High-return, zero-loss trend-following trials were being discarded due to zero downside deviation (sigma_down = 0).

### Mathematical Specification
When sigma_down <= 10^-6, replace undefined Sharpe/Sortino ratios with Risk-Adjusted Expectancy (RAE):
RAE = (mean_R / (sigma_floor + epsilon)) * (1 - exp(-N / N_target))
where sigma_floor = P_close * atr_floor_bps * 10^-4.

### Acceptance Criteria
1. Zero-loss profitable trials (R_OOS > 0, sigma_down = 0) receive a positive, finite ranking score RAE > 0.
2. check_selection_statistic_economic_bias passes without statistical rejection of high-return trials (p > 0.05)."""
    },
    {
        "number": 979,
        "title": "Issue #979: Calendar-Spanned Fixed Annualization Factor ($F_{global}$)",
        "body": """### Summary
`check_annualization_commensurability` failed in 13 studies because annualization factors sqrt(F) varied across folds of the same trial by up to 1.40x (> 1.05), causing fold-averaged metrics to average mathematically incommensurable quantities.

### Mathematical Specification
Enforce a fixed calendar-spanned annualization factor F_global across all folds of a Walk-Forward trial:
F_global = (365.25 * 24 * 3600 * 10^9) / delta_t_span_ns

### Acceptance Criteria
1. Max-to-min ratio of sqrt(F) across folds within any single trial is bounded by max(sqrt(F)) / min(sqrt(F)) <= 1.05.
2. check_annualization_commensurability passes for 100% of completed studies."""
    },
    {
        "number": 980,
        "title": "Issue #980: Continuous Feasibility Distance Gradient ($D_{feas}$) for Optuna TPE",
        "body": """### Summary
`check_search_made_progress` failed in 14 out of 14 studies (p_eligible = 0.0%). Optuna TPE encountered a completely flat loss floor (-9999.0) because hard boolean gates provided zero gradient for parameter exploration.

### Mathematical Specification
For trials failing feasibility constraints, compute the continuous quadratic normalized distance:
D_feas(theta) = - sum_g ( w_g * ( max(0, (Gate_target - Gate_val(theta)) / Gate_scale) )^2 )

### Acceptance Criteria
1. Optuna TPE receives a smooth, continuous gradient D_feas in [-50.0, 0.0) for non-eligible trials.
2. check_search_made_progress passes with constraint improvement rate > 0.15."""
    },
    {
        "number": 981,
        "title": "Issue #981: Effective Horizon-Normalized $N_{effective}$ Period Standard for DSR",
        "body": """### Summary
`check_n_periods_homogeneity` failed with a max/min n_periods ratio of 290.96 > 6.0, triggering DSR heterogeneity suppression for almost all trial families.

### Mathematical Specification
Normalize n_periods to the effective trade horizon span:
N_effective = Total_OOS_Hours / max(1.0, median_holding_hours)

### Acceptance Criteria
1. The max/min ratio of N_effective across all trial families for a single symbol satisfies max(N_effective) / min(N_effective) <= 6.0.
2. check_n_periods_homogeneity passes cleanly."""
    },
    {
        "number": 982,
        "title": "Issue #982: Real-Market Cost & Execution Calibrated Search Space Overrides",
        "body": """### Summary
Parameter bounds for fast-reverting equity strategies allowed degenerate holding times (t_hold < 2h) that were eaten up by equity spread (3.0 bps) and round-trip commissions (1.0 bps).

### Mathematical Specification
Clamp minimum expected trade duration:
t_min_hold >= (2 * (Spread_bps + Commission_bps)) / (Expected ATR bps / hour)

### Acceptance Criteria
1. Minimum trade duration bound is dynamically derived from asset class transaction costs.
2. Eliminates cost-ruined degenerate micro-trades."""
    },
    {
        "number": 983,
        "title": "Issue #983: Adaptive Diagnostic Code Classification in Censoring Concentration Invariant",
        "body": """### Summary
`check_inference_diagnostics_concentration` failed because non-fatal telemetry and adaptive shrinkage codes (SORTINO_DOWNSIDE_SHRUNK) were counted as censoring defects.

### Mathematical Specification
Exclude _ADAPTIVE_DIAGNOSTIC_CODES from _CENSORING_DIAGNOSTIC_CODES in check_inference_diagnostics_concentration.

### Acceptance Criteria
1. Adaptive shrinkage diagnoses do not trigger false censoring invariant failures.
2. check_inference_diagnostics_concentration evaluates only true censoring failures."""
    },
    {
        "number": 984,
        "title": "Issue #984: Universal Selection Statistic ($PSR_{shrunk}$) Population for Evaluated Trials",
        "body": """### Summary
`check_selection_statistic_availability` failed because oos_psr was omitted for zero-trade or negative trials.

### Mathematical Specification
Populate oos_psr = calculate_downside_shrunk_psr(...) for 100% of oos_evaluated=True trials.

### Acceptance Criteria
1. check_selection_statistic_availability reaches >= 0.95 across all 14 studies."""
    },
    {
        "number": 985,
        "title": "Issue #985: Gate Priority & Marginal Delta Re-Calibration",
        "body": """### Summary
`check_gate_marginal_contribution` failed because min_trades and max_drawdown had 0 marginal delta.

### Mathematical Specification
Re-order gate evaluation in gate_consolidation_priority and calibrate oos_min_psr to allow max_drawdown and min_trades to filter independently.

### Acceptance Criteria
1. check_gate_marginal_contribution passes with non-zero marginal deltas for all active gates."""
    },
    {
        "number": 986,
        "title": "Issue #986: Dynamic Lookback Bounds Capping Against Symbol Window Span",
        "body": """### Summary
`check_window_unreachable_rate` failed (5.5%-35.5% unreachable OOS windows) due to excessive lookback bounds (ema_period, vwap_span).

### Mathematical Specification
Cap max lookback periods in spaces.py: max_lookback <= min(default_max, OOS_Fold_Hours * 0.20).

### Acceptance Criteria
1. check_window_unreachable_rate passes with unreachable window fraction <= 0.05."""
    },
    {
        "number": 987,
        "title": "Issue #987: Deflated Sharpe Ratio ($DSR \\ge 0.95$) Requirement in Deployment Whitelist",
        "body": """### Summary
`Phase 5` whitelist generation currently accepts candidates based on un-deflated single-window OOS metrics (`oos_eligible`), exposing live execution to data mining bias and overfitting hazard.

### Mathematical Specification
Candidate pair (Strategy, Symbol) is eligible for deployment whitelist iff:
DSR(SR_OOS, N_trials, gamma_3, gamma_4, V[SR]) >= 0.95 AND PSR_shrunk >= 0.75

### Acceptance Criteria
1. No candidate with DSR < 0.95 or PSR_shrunk < 0.75 is ever included in whitelist_tournament.json.
2. Guarantees statistical immunity against data-mining luck."""
    },
    {
        "number": 988,
        "title": "Issue #988: Dynamic Portfolio Tail-Risk ($CVaR_{99} \\le 0.15$) Deployment Gate",
        "body": """### Summary
Aggregate tournament selection evaluates max_drawdown and sortino_ratio, but ignores fat-tailed crash risk (CVaR_99), allowing strategies with catastrophic black-swan tail losses to pass deployment interlocks.

### Mathematical Specification
Evaluate 99% Conditional Value-at-Risk (CVaR_99):
CVaR_99 = E[ R | R <= VaR_99 ] <= 0.15

### Acceptance Criteria
1. Strategies with CVaR_99 > 15% are automatically rejected in Phase 5 before deployment.
2. Eliminates fat-tailed black swan deployment risks."""
    },
    {
        "number": 989,
        "title": "Issue #989: Real-Time Live Portfolio Drawdown Circuit Breaker ($DD_{live\\_max} \\le 10\\%$)",
        "body": """### Summary
In detached live execution mode, daily_orchestrator.py lacks a real-time active monitor to halt trading and liquidate open positions if live portfolio equity drops by > 10% or diverges from backtest distribution by > 2.5 sigma.

### Mathematical Specification
Halt live bot sub-process immediately if:
DD_live(t) = 1 - Equity(t) / max Equity(tau) >= 0.10 OR (Return_live - mu_backtest) / sigma_backtest < -2.5

### Acceptance Criteria
1. Live execution automatically terminates and flattens all positions if DD_live >= 10%.
2. Prevents runaway live drawdown."""
    },
    {
        "number": 990,
        "title": "Issue #990: Pre-Deployment Execution Cost Stress Multiplier ($2.0\\times$) Gate",
        "body": """### Summary
Strategies validated under nominal spreads (3.0 bps) can have marginal expectancy (4.0 bps) that collapses during live market volatility when eToro spreads widen to 8.0 bps.

### Mathematical Specification
Re-evaluate candidate OOS expectancy under 2.0x spread and 2.0x commission stress multiplier:
Expectancy_stressed = Expectancy_OOS - (Spread_bps + Commission_bps) * 10^-4 >= 0.0010

### Acceptance Criteria
1. Strategy must remain strictly profitable (Expectancy_stressed >= 10 bps) under 2x cost stress before deployment.
2. Protects against live slippage and spread expansion."""
    },
    {
        "number": 991,
        "title": "Issue #991: RAM Stability & Memory Footprint Bounds in Batch Backtesting",
        "body": """### Summary
When running `daily_orchestrator.py --no-deploy` across 146 instruments and 14 strategies (2,044 job combinations), submitting all task futures simultaneously into ProcessPoolExecutor creates thousands of concurrent task payload objects in RAM, causing RSS memory expansion and risking OOM crashes.

### Architectural Specification
1. Batch Submission Throttling: Submit jobs in bounded batches of N_batch = 64 tasks.
2. Worker Recycling: Maintain max_tasks_per_child=1 for worker process memory isolation.
3. Garbage Collection Interlock: Execute gc.collect() after each batch completion.
4. RSS Monitoring: Monitor RSS memory (/proc/self/status); if RSS > 4.0 GB, pause submission and force cache clearance.

### Acceptance Criteria
1. Peak RSS memory stays bounded <= 4.0 GB throughout Phase 1-5 execution.
2. Prevents OOM crashes and maintains system stability."""
    }
]

def create_issues(token: str):
    url = f"https://api.github.com/repos/{REPO}/issues"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Nautilus-Issue-Creator"
    }
    
    created = []
    for item in ISSUES:
        data = json.dumps({"title": item["title"], "body": item["body"]}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                print(f"Created Issue #{res['number']}: {res['html_url']}")
                created.append(res['html_url'])
        except urllib.error.HTTPError as e:
            print(f"HTTPError {e.code}: {e.read().decode('utf-8')}")
        except Exception as e:
            print(f"Error creating issue: {e}")
    return created

if __name__ == "__main__":
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        create_issues(token)
    else:
        print("GITHUB_TOKEN or GH_TOKEN not found in environment.")
