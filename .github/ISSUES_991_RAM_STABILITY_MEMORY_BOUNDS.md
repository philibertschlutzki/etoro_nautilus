# Issue #991: RAM Stability & Memory Footprint Bounds in Batch Backtesting

- **Category**: Orchestrator / Memory Management & System Stability
- **Priority**: P0 (Blocking)
- **Target Files**: [`automation/daily_orchestrator.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/daily_orchestrator.py), [`automation/backtest_runner.py`](file:///home/user/.gemini/antigravity/worktrees/etoro_nautilus/optimize_strategies_fix_issues/automation/backtest_runner.py)
- **Problem**: When running `daily_orchestrator.py --no-deploy` across 146 instruments and 14 strategies (2,044 job combinations), submitting all task futures simultaneously into `ProcessPoolExecutor` creates thousands of concurrent task payload objects in RAM, causing RSS memory expansion and risking OOM crashes.
- **Mathematical / Architectural Specification**:
  1. **Batch Submission Throttling**: Submit jobs in bounded batches of $N_{batch} = 64$ tasks.
  2. **Worker Recycling**: Maintain `max_tasks_per_child=1` for worker process memory isolation.
  3. **Garbage Collection Interlock**: Execute `gc.collect()` after each batch completion.
  4. **RSS Monitoring**: Monitor RSS memory (`/proc/self/status`); if $RSS > 4.0\text{ GB}$, pause submission and force cache clearance.
- **Acceptance Criteria**:
  1. Peak RSS memory stays bounded $\le 4.0\text{ GB}$ throughout Phase 1–5 execution.
  2. Prevents OOM crashes and maintains system stability.
