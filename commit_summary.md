Multi-Objective Optimization (MOO) via Optuna Pareto (Issue #507)

- Added `reward_mode="pareto"` in `reward.py` to return an unscaled raw metrics tuple.
- Modified `run_optimization.py` to support `NSGAIISampler` and dynamically inject constraints and directions when in pareto mode.
- Modified `confirm.py` to handle `study.best_trials` natively, iteratively testing Pareto front candidates on holdout and selecting the one with maximum scalar reward.
- Updated `AGENTS.md` docs.
- Maintained scalar mode bit-identically.
