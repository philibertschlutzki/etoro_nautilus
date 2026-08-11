# GitHub Issue Catalog #977–#982 / Remote Issues #801–#806: Mathematical Excellence & Real-Market Optimization Remedies

This catalog specifies six production-grade GitHub Issues (#977–#982 / Remote Issues #801–#806) created live in the repository [`philibertschlutzki/etoro_nautilus`](https://github.com/philibertschlutzki/etoro_nautilus/issues) addressing the 85 invariant failures identified in the optimization sweep (`zusammenfassung_unknown_20260809T153327915287.md`).

- [Remote Issue #801: Adaptive Kernel Downside-Shrunk PSR Estimation](https://github.com/philibertschlutzki/etoro_nautilus/issues/801)
- [Remote Issue #802: Risk-Adjusted Expectancy Replacement ($RAE$)](https://github.com/philibertschlutzki/etoro_nautilus/issues/802)
- [Remote Issue #803: Calendar-Spanned Fixed Annualization Factor ($F_{global}$)](https://github.com/philibertschlutzki/etoro_nautilus/issues/803)
- [Remote Issue #804: Continuous Feasibility Distance Gradient ($D_{feas}$)](https://github.com/philibertschlutzki/etoro_nautilus/issues/804)
- [Remote Issue #805: Effective Horizon-Normalized $N_{effective}$ Period Standard](https://github.com/philibertschlutzki/etoro_nautilus/issues/805)
- [Remote Issue #806: Real-Market Cost & Execution Calibrated Search Space Overrides](https://github.com/philibertschlutzki/etoro_nautilus/issues/806)

---


## Issue #977: Adaptive Kernel Downside-Shrunk PSR Estimation ($PSR_{shrunk}$)

- **Category**: Optimizer / Statistical Selection / Invariance
- **Priority**: P0 (Blocking)
- **Target Files**: [`automation/optimizer/deflation.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/deflation.py), [`automation/optimizer/invariants.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/invariants.py)
- **Problem**: `check_selection_statistic_availability` failed with 9 out of 14 studies below 80% $PSR$ availability (e.g., `SqueezeBreakout`: 3.5%, `TrendPullback`: 23.1%). Small-sample trade trials ($N < 30$) or low-downside trials emitted `None` for $PSR$, causing structural information-free study invalidation.
- **Mathematical Specification**:
  For small or low-observation trade samples $N \in [5, 30)$, apply an Empirical Bayes Shrinkage model to sample skewness $\hat{\gamma}_3$ and kurtosis $\hat{\gamma}_4$:
  $$\gamma_3^{shrunk} = \alpha \cdot \hat{\gamma}_3 + (1 - \alpha) \cdot 0.0$$
  $$\gamma_4^{shrunk} = \alpha \cdot \hat{\gamma}_4 + (1 - \alpha) \cdot 3.0$$
  where $\alpha = \min\left(1.0, \frac{N}{30.0}\right)$.
  Compute $PSR_{shrunk}$:
  $$PSR_{shrunk}(SR^*) = \Phi\left( \frac{(\hat{SR} - SR^*) \sqrt{N - 1}}{\sqrt{1 - \gamma_3^{shrunk} \hat{SR} + \frac{\gamma_4^{shrunk} - 1}{4} \hat{SR}^2}} \right)$$
- **Acceptance Criteria**:
  1. $PSR_{shrunk}$ is defined for 100% of OOS-evaluated trials with $N_{trades} \ge 5$.
  2. `check_selection_statistic_availability` passes with availability $\ge 0.95$ across all studies.

---

## Issue #978: Risk-Adjusted Expectancy Replacement ($RAE$) for Zero-Loss Trend Trials

- **Category**: Optimizer / Economic Selection Bias
- **Priority**: P0 (High)
- **Target Files**: [`automation/optimizer/deflation.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/deflation.py), [`automation/optimizer/invariants.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/invariants.py)
- **Problem**: `check_selection_statistic_economic_bias` failed across 12 studies because trials *without* raw selection statistics had significantly higher median OOS returns (+1.01%) than trials *with* statistics (-0.34%, $z=7.61, p=1.35 \times 10^{-14}$). High-return, zero-loss trend-following trials were being discarded due to zero downside deviation ($\sigma_{down} = 0$).
- **Mathematical Specification**:
  When $\sigma_{down} \le 10^{-6}$, replace undefined Sharpe/Sortino ratios with Risk-Adjusted Expectancy ($RAE$):
  $$RAE = \frac{\overline{R}}{\sigma_{floor} + \epsilon} \cdot \left(1 - e^{-N / N_{target}}\right)$$
  where $\sigma_{floor} = P_{close} \cdot atr\_floor\_bps \cdot 10^{-4}$ represents the asset-class volatility floor.
- **Acceptance Criteria**:
  1. Zero-loss profitable trials ($R_{OOS} > 0, \sigma_{down} = 0$) receive a positive, finite ranking score $RAE > 0$.
  2. `check_selection_statistic_economic_bias` passes without statistical rejection of high-return trials ($p > 0.05$).

---

## Issue #979: Calendar-Spanned Fixed Annualization Factor ($F_{global}$)

- **Category**: Optimizer / Multi-Fold Commensurability
- **Priority**: P1 (High)
- **Target Files**: [`automation/optimizer/deflation.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/deflation.py), [`automation/optimizer/invariants.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/invariants.py)
- **Problem**: `check_annualization_commensurability` failed in 13 studies because annualization factors $\sqrt{F}$ varied across folds of the *same* trial by up to $1.40\times$ ($> 1.05$), causing fold-averaged metrics to average mathematically incommensurable quantities.
- **Mathematical Specification**:
  Enforce a fixed calendar-spanned annualization factor $F_{global}$ across all folds of a Walk-Forward trial:
  $$F_{global} = \frac{365.25 \times 24 \times 3600 \times 10^9}{\Delta t_{span\_ns}}$$
  where $\Delta t_{span\_ns} = \text{ts\_end} - \text{ts\_start}$ is the exact physical time span of the OOS fold window.
- **Acceptance Criteria**:
  1. Max-to-min ratio of $\sqrt{F}$ across folds within any single trial is bounded by $\frac{\max \sqrt{F}}{\min \sqrt{F}} \le 1.05$.
  2. `check_annualization_commensurability` passes for 100% of completed studies.

---

## Issue #980: Continuous Feasibility Distance Gradient ($D_{feas}$) for Optuna TPE

- **Category**: Optimizer / Search Space / Gradient Recovery
- **Priority**: P1 (High)
- **Target Files**: [`automation/optimizer/spaces.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/spaces.py), [`automation/optimizer/sweep.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/sweep.py), [`automation/optimizer/gate.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/gate.py)
- **Problem**: `check_search_made_progress` failed in 14 out of 14 studies ($p_{eligible} = 0.0\%$). Optuna TPE encountered a completely flat loss floor (-9999.0) because hard boolean gates provided zero gradient for parameter exploration.
- **Mathematical Specification**:
  For trials failing feasibility constraints, compute the continuous quadratic normalized distance:
  $$D_{feas}(\theta) = -\sum_{g \in Gates} w_g \cdot \left( \max\left(0, \frac{Gate_{target} - Gate_{val}(\theta)}{Gate_{scale}}\right) \right)^2$$
  Feed $D_{feas}(\theta)$ into Optuna as the objective value for non-eligible trials.
- **Acceptance Criteria**:
  1. Optuna TPE receives a smooth, continuous gradient $D_{feas} \in [-50.0, 0.0)$ for non-eligible trials.
  2. `check_search_made_progress` passes with constraint improvement rate $> 0.15$.

---

## Issue #981: Effective Horizon-Normalized $N_{effective}$ Period Standard for DSR

- **Category**: Optimizer / Deflated Sharpe Ratio / Heterogeneity
- **Priority**: P1 (High)
- **Target Files**: [`automation/optimizer/deflation.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/deflation.py), [`automation/optimizer/invariants.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/invariants.py)
- **Problem**: `check_n_periods_homogeneity` failed with a max/min $n_{periods}$ ratio of $290.96 > 6.0$, triggering DSR heterogeneity suppression for almost all trial families.
- **Mathematical Specification**:
  Normalize $n_{periods}$ to the effective trade horizon span:
  $$N_{effective} = \frac{T_{OOS\_total\_hours}}{\max(1.0, t_{median\_holding\_hours})}$$
- **Acceptance Criteria**:
  1. The max/min ratio of $N_{effective}$ across all trial families for a single symbol satisfies $\frac{\max N_{effective}}{\min N_{effective}} \le 6.0$.
  2. `check_n_periods_homogeneity` passes cleanly.

---

## Issue #982: Real-Market Cost & Execution Calibrated Search Space Overrides

- **Category**: Optimizer / Execution Realism & Live Calibration
- **Priority**: P1 (High)
- **Target Files**: [`automation/optimizer/spaces.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/spaces.py), [`automation/momentum_ls_allocator.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/momentum_ls_allocator.py)
- **Problem**: Parameter bounds for fast-reverting equity strategies allowed degenerate holding times ($t_{hold} < 2h$) that were eaten up by equity spread ($3.0$ bps) and round-trip commissions ($1.0$ bps).
- **Mathematical Specification**:
  Clamp minimum expected trade duration $t_{min\_hold} \ge \frac{2 \cdot (Spread_{bps} + Commission_{bps})}{\text{Expected ATR bps / hour}}$ in search space bounds.
- **Acceptance Criteria**:
  1. Minimum trade duration bound is dynamically derived from asset class transaction costs.
  2. Eliminates cost-ruined degenerate micro-trades.

---

## Issue #983: Adaptive Diagnostic Code Classification in Censoring Concentration Invariant

- **Category**: Optimizer / Invariance & Diagnostics
- **Priority**: P1 (High)
- **Target Files**: [`automation/optimizer/invariants.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/invariants.py)
- **Problem**: `check_inference_diagnostics_concentration` failed because non-fatal telemetry and adaptive shrinkage codes (`SORTINO_DOWNSIDE_SHRUNK`) were counted as censoring defects.
- **Mathematical Specification**:
  Exclude `_ADAPTIVE_DIAGNOSTIC_CODES` from `_CENSORING_DIAGNOSTIC_CODES` in `check_inference_diagnostics_concentration`.
- **Acceptance Criteria**:
  1. Adaptive shrinkage diagnoses do not trigger false censoring invariant failures.
  2. `check_inference_diagnostics_concentration` evaluates only true censoring failures.

---

## Issue #984: Universal Selection Statistic ($PSR_{shrunk}$) Population for Evaluated Trials

- **Category**: Optimizer / Selection Availability
- **Priority**: P0 (Blocking)
- **Target Files**: [`automation/optimizer/run_optimization.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/run_optimization.py), [`automation/optimizer/report.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/report.py)
- **Problem**: `check_selection_statistic_availability` failed because `oos_psr` was omitted for zero-trade or negative trials.
- **Mathematical Specification**:
  Populate `oos_psr = calculate_downside_shrunk_psr(...)` for 100% of `oos_evaluated=True` trials.
- **Acceptance Criteria**:
  1. `check_selection_statistic_availability` reaches $\ge 0.95$ across all 14 studies.

---

## Issue #985: Gate Priority & Marginal Delta Re-Calibration

- **Category**: Optimizer / Gate Sensitivity
- **Priority**: P1 (High)
- **Target Files**: [`automation/optimizer/gate.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/gate.py), [`automation/config/tournament.json`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/config/tournament.json)
- **Problem**: `check_gate_marginal_contribution` failed because `min_trades` and `max_drawdown` had 0 marginal delta.
- **Mathematical Specification**:
  Re-order gate evaluation in `gate_consolidation_priority` and calibrate `oos_min_psr` to allow `max_drawdown` and `min_trades` to filter independently.
- **Acceptance Criteria**:
  1. `check_gate_marginal_contribution` passes with non-zero marginal deltas for all active gates.

---

## Issue #986: Dynamic Lookback Bounds Capping Against Symbol Window Span

- **Category**: Optimizer / Search Space & Data Window
- **Priority**: P1 (High)
- **Target Files**: [`automation/optimizer/spaces.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/optimizer/spaces.py)
- **Problem**: `check_window_unreachable_rate` failed (5.5%–35.5% unreachable OOS windows) due to excessive lookback bounds (`ema_period`, `vwap_span`).
- **Mathematical Specification**:
  Cap max lookback periods in `spaces.py`: $\text{max\_lookback} \le \min(\text{default\_max}, \text{OOS\_Fold\_Hours} \times 0.20)$.
- **Acceptance Criteria**:
  1. `check_window_unreachable_rate` passes with unreachable window fraction $\le 0.05$.

