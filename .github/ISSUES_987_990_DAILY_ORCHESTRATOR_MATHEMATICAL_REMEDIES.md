# Daily Orchestrator Mathematical Rigor & Deployment Risk Control (Issues #987–#990)

## Issue #987: Deflated Sharpe Ratio ($DSR \ge 0.95$) Requirement in Deployment Whitelist

- **Category**: Orchestrator / Deployment Interlock & Selection Bias
- **Priority**: P0 (Blocking)
- **Target Files**: [`automation/daily_orchestrator.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/daily_orchestrator.py)
- **Problem**: `Phase 5` whitelist generation currently accepts candidates based on un-deflated single-window OOS metrics (`oos_eligible`), exposing live execution to data mining bias and overfitting hazard.
- **Mathematical Specification**:
  Candidate pair $(Strategy, Symbol)$ is eligible for deployment whitelist iff:
  $$DSR(SR_{OOS}, N_{trials}, \gamma_3, \gamma_4, V[SR]) \ge 0.95 \quad \text{AND} \quad PSR_{shrunk} \ge 0.75$$
- **Acceptance Criteria**:
  1. No candidate with $DSR < 0.95$ or $PSR_{shrunk} < 0.75$ is ever included in `whitelist_tournament.json`.
  2. Guarantees statistical immunity against data-mining luck.

---

## Issue #988: Dynamic Portfolio Tail-Risk ($CVaR_{99} \le 0.15$) Deployment Gate

- **Category**: Orchestrator / Tail-Risk & Expected Shortfall
- **Priority**: P0 (Blocking)
- **Target Files**: [`automation/daily_orchestrator.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/daily_orchestrator.py), [`automation/backtest_runner.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/backtest_runner.py)
- **Problem**: Aggregate tournament selection evaluates `max_drawdown` and `sortino_ratio`, but ignores fat-tailed crash risk ($CVaR_{99}$), allowing strategies with catastrophic black-swan tail losses to pass deployment interlocks.
- **Mathematical Specification**:
  Evaluate 99% Conditional Value-at-Risk ($CVaR_{99}$):
  $$CVaR_{99} = E\left[ R \mid R \le VaR_{99} \right] \le 0.15$$
- **Acceptance Criteria**:
  1. Strategies with $CVaR_{99} > 15\%$ are automatically rejected in Phase 5 before deployment.
  2. Eliminates fat-tailed black swan deployment risks.

---

## Issue #989: Real-Time Live Portfolio Drawdown Circuit Breaker ($DD_{live\_max} \le 10\%$)

- **Category**: Orchestrator / Live Execution Safety & Circuit Breaker
- **Priority**: P0 (Blocking)
- **Target Files**: [`automation/daily_orchestrator.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/daily_orchestrator.py), [`automation/momentum_ls_run.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/momentum_ls_run.py)
- **Problem**: In detached live execution mode, `daily_orchestrator.py` lacks a real-time active monitor to halt trading and liquidate open positions if live portfolio equity drops by $> 10\%$ or diverges from backtest distribution by $> 2.5\sigma$.
- **Mathematical Specification**:
  Halt live bot sub-process immediately if:
  $$DD_{live}(t) = 1 - \frac{Equity(t)}{\max_{\tau \le t} Equity(\tau)} \ge 0.10 \quad \text{OR} \quad \frac{Return_{live} - \mu_{backtest}}{\sigma_{backtest}} < -2.5$$
- **Acceptance Criteria**:
  1. Live execution automatically terminates and flattens all positions if $DD_{live} \ge 10\%$.
  2. Prevents runaway live drawdown.

---

## Issue #990: Pre-Deployment Execution Cost Stress Multiplier ($2.0\times$) Gate

- **Category**: Orchestrator / Execution Realism & Stress Testing
- **Priority**: P1 (High)
- **Target Files**: [`automation/daily_orchestrator.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/daily_orchestrator.py), [`automation/backtest_runner.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/backtest_runner.py)
- **Problem**: Strategies validated under nominal spreads ($3.0$ bps) can have marginal expectancy ($4.0$ bps) that collapses during live market volatility when eToro spreads widen to $8.0$ bps.
- **Mathematical Specification**:
  Re-evaluate candidate OOS expectancy under $2.0\times$ spread and $2.0\times$ commission stress multiplier:
  $$Expectancy_{stressed} = Expectancy_{OOS} - (Spread_{bps} + Commission_{bps}) \times 10^{-4} \ge 0.0010$$
- **Acceptance Criteria**:
  1. Strategy must remain strictly profitable ($Expectancy_{stressed} \ge 10$ bps) under $2\times$ cost stress before deployment.
  2. Protects against live slippage and spread expansion.
