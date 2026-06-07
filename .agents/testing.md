# Agent Task Specification: `etoro_nautilus` — Full Test & Optimization Suite

**Repository:** `https://github.com/philibertschlutzki/etoro_nautilus`
**Execution Mode:** Autonomous. Complete all phases sequentially without waiting for user input.
**Phase Gate Rule:** Do NOT proceed to the next phase until the current phase has 100% passing acceptance criteria. If a phase cannot reach 100%, write a `BLOCKED_<phase>.md` report in `/logs/` detailing the blocker and continue with non-blocked tasks within that phase.

---

## Pre-Flight: Environment Setup

Before starting Phase 1, verify and document the following:

1. All Python dependencies from `requirements.txt` (or equivalent) are installed and importable.
2. The eToro Demo API key is valid: run a single authenticated GET request to confirm connectivity and log the response.
3. The demo account balance reads **10,000 USD**. If it differs, log the actual value and continue.
4. Confirm Nautilus core engine initializes without errors (`from nautilus_trader.core import ...` or equivalent entry point in the repo).
5. Write findings to `/logs/PREFLIGHT_<YYYY-MM-DD>.md`.

**Acceptance Criteria:** API connectivity confirmed, Nautilus imports without errors, initial balance logged.

---

## Phase 1 — Strategy Verification & Forced Trigger Testing

**Goal:** Every strategy in `automation/strategies/` executes at least one BUY and one SELL/CLOSE signal within a 10–15 minute live demo run.

### Tasks

1. **Audit all strategy files.** Target files (adjust if names differ in repo):
   - `automation/strategies/dynamic_breakout.py`
   - `automation/strategies/flash_crash_reversal.py`
   - `automation/strategies/mean_reversion.py`
   - `automation/strategies/sma_crossover.py`
   - `automation/strategies/tesla_combo_strategy.py`
   - `automation/strategies/volatility_breakout.py`
   - `automation/strategies/vwap_exhaustion.py`

2. **Use the JSON config system (`automation/config/strategies.json` -> `params`)** to override strategy parameters to maximally sensitive values (e.g., SMA periods of 2/3, breakout thresholds of 0.01%, ATR multipliers of 0.1) instead of monkey-patching legacy files. Do not modify `strategy_defaults.json`.

3. **Create `automation/tests/run_live_strategy_test.py`.** This script must:
   - Load each strategy with test overrides.
   - Connect to the eToro demo account via the existing data adapter/execution client in the repo.
   - Run each strategy for 10–15 minutes against live tick data.
   - Confirm at least 1 BUY and 1 SELL/CLOSE event per strategy by parsing emitted order/fill events.
   - Wrap each strategy run in a `try/except` block; log the full traceback on failure without halting the test loop.
   - Output a per-strategy result table to stdout and write raw JSON results to `/logs/phase1_strategy_results.json`.

4. **Error hardening:** For any strategy that fails to trigger within the test window, emit a `WARNING` log entry and add a synthetic forced signal (for test purposes only) to verify the order-routing pipeline itself is functional. Document this fallback in the log.

**Acceptance Criteria:**
- All strategy files parse without `SyntaxError` or `ImportError`.
- `/logs/phase1_strategy_results.json` exists and contains an entry for each strategy.
- Each entry has `buy_count >= 1` and `sell_count >= 1`, OR documents a confirmed routing test with a forced signal.
- Zero unhandled exceptions.

---

## Phase 2 — API Interface Stress Testing & Order Execution

**Goal:** Full transparency of eToro REST API responses and systematic validation of all order types.

### Tasks

1. **Extend `automation/tests/etoro_api_probe.py` and `automation/tests/etoro_api_probe_all.py`:**
   - Add a `DebugHTTPAdapter` (using `requests` `HTTPAdapter` or equivalent) that logs:
     - Full request URL, method, headers, body.
     - HTTP status code, response headers, full JSON response body.
     - Round-trip latency in milliseconds.
   - Write all log output to `/logs/api_probe_<YYYY-MM-DD>.jsonl` (one JSON object per line).
   - Do not suppress any HTTP 4xx or 5xx errors; log them with full detail and continue.

2. **Extend `automation/tests/etoro_execution_tests_all_orders.py`** to cover the following order types systematically. For each type, log request payload, response, resulting order status, and fill confirmation (or failure reason):

   | Order Type | Test Scenario |
   |---|---|
   | Market Buy | Standard size on available instrument |
   | Market Sell | Close the position opened above |
   | Limit Buy | Price set 5% below current market (should remain open) |
   | Limit Sell | Price set 5% above current market (should remain open) |
   | Limit Buy + Stop-Loss | SL set 2% below limit price |
   | Limit Buy + Take-Profit | TP set 3% above limit price |
   | Cancel Open Order | Cancel the pending limit orders created above |
   | Verify Cancellation | Confirm order status is `CANCELLED` via API |

3. **Extend `automation/tests/etoro_balance.py` (or equivalent):**
   - After every order execution step above, query the account balance endpoint.
   - Assert the returned balance is numerically consistent with the prior balance ± the trade PnL.
   - Log discrepancies as `ERROR` (do not raise, continue testing).
   - Write a balance timeline to `/logs/phase2_balance_timeline.json`.

**Acceptance Criteria:**
- `/logs/api_probe_<date>.jsonl` contains at least one entry per endpoint probed.
- All 8 order type tests executed; results in `/logs/phase2_order_results.json`.
- Zero Python exceptions escaping test wrappers.
- Balance timeline file exists and shows at least 3 data points.

---

## Phase 3 — Backtesting: Real Data (1 Hour) + Synthetic Data (6 Months)

**Goal:** Each strategy is backtested on real tick data and on 6 months of synthetic data, with validated HTML report output.

### Tasks

1. **Real-data backtest (≈1 hour of tick data):**
   - Locate all `.parquet` files under `/data/nautilus/data/quote_tick/` (instruments: BTC, ETH, ADA, TSLA — or whatever is available).
   - Using `python -m automation.backtest_runner` (or `python -m automation.daily_orchestrator`), run each strategy against ≈1 hour of tick data per instrument.
   - Capture stdout/stderr per run; write to `/logs/phase3_real_backtest_<strategy>_<instrument>.log`.

2. **Synthetic data generator — create `automation/tests/generate_synthetic_data.py`:**
   - Generate 6 months of synthetic `QuoteTick` data (bid/ask) using **Geometric Brownian Motion** with configurable drift and volatility parameters.
   - Parameters (configurable via CLI args or constants at top of file):
     - `--instrument` (e.g., `BTC-USD`)
     - `--start` (ISO date)
     - `--end` (ISO date, default: start + 180 days)
     - `--freq` (tick frequency in seconds, default: 1)
     - `--mu` (drift, default: 0.0001)
     - `--sigma` (volatility, default: 0.02)
   - Output: `.parquet` files written to `/data/synthetic/quote_tick/<instrument>/` in the same schema as the real data files. **CRITICAL:** Data MUST be strictly encoded as `FixedSizeBinary(16)` (e.g., `round(val * 10**16)`) and PyArrow metadata (`b"size_precision"`, `b"price_precision"`) MUST be injected following the exact pattern in `automation/api_backfiller.py` to prevent Rust FFI aborts.
   - After writing, validate the output by reading it back and asserting row count > 0.

3. **6-month backtest:** Run every strategy against the synthetic 6-month dataset using `python -m automation.backtest_runner`. The backtest and logs MUST evaluate and report Out-of-Sample (OOS) metrics (e.g., `oos_metrics`, `oos_eligible`) to correctly mirror the Phase 5 `daily_orchestrator` behavior. Write per-run logs to `/logs/phase3_synthetic_backtest_<strategy>.log`.

4. **HTML report validation:** For each backtest run, locate the output HTML tearsheet (check the repo's config for the output path). Validate programmatically:
   - File exists and is non-empty.
   - File parses as valid HTML (`html.parser` in Python stdlib).
   - File contains at least one of the expected metric strings: `"Sharpe"`, `"Drawdown"`, `"Win Rate"` (case-insensitive).
   - Write a validation summary to `/logs/phase3_report_validation.json`.

**Acceptance Criteria:**
- Real backtest logs exist for each strategy × instrument combination.
- `/data/synthetic/quote_tick/` contains at least one instrument's parquet file spanning ≥ 180 days.
- Synthetic backtest logs exist for all strategies.
- `/logs/phase3_report_validation.json` shows `valid: true` for each run (or documents why a report could not be generated).

---

## Phase 4 — Markdown Test Reports

**Goal:** Traceable, structured test documentation for every strategy and module tested.

### Tasks

For each strategy tested in Phases 1–3, and for the API test suite from Phase 2, create a report file in `/logs/` with the naming convention:

```
TESTREPORT_<StrategyName>_<YYYY-MM>.md
TESTREPORT_API_OrderExecution_<YYYY-MM>.md
```

Each report must contain the following sections (no exceptions):

```markdown
## Test Report: <Name>

### Metadata
- Date/Time: <ISO 8601>
- Git Commit: <output of `git rev-parse HEAD`>
- Environment: eToro Demo Account | Starting Capital: 10,000 USD

### Instruments Tested
<list>

### Test Steps
<numbered list of exact actions taken>

### Results

| Metric | Expected | Actual |
|--------|----------|--------|
| OOS Eligible | ... | ... |
| ... | ... | ... |

Include: API latency (ms), slippage (if measurable), error codes encountered.

### Status
**PASS** / **FAIL** — <one-sentence reason>

### Notes
<any anomalies, deviations from expected behavior, or follow-up recommendations>
```

**Acceptance Criteria:**
- One report file per strategy + one for the API test suite.
- All reports follow the template exactly.
- No report has an empty `Results` table.

---

## Phase 5 — Documentation Update

**Goal:** All manuals reflect current system state; a new `TESTING.md` gives developers a clear entry point.

### Tasks

1. **Update existing manuals** based on findings from Phases 1–4:
   - `manuals/deployment.md` — add any new dependencies, environment variables, or setup steps discovered.
   - `manuals/backtesting_manual.md` — document the synthetic data generator and the 6-month backtest workflow.
   - `manuals/new_tickers.md` — add any instruments used in testing; document how to add new instruments to the synthetic data generator.

2. **Create `manuals/TESTING.md`** with the following structure:
   - **Prerequisites** (Python version, dependencies, API key setup)
   - **Running the API probe** (`python automation/tests/etoro_api_probe.py --help`)
   - **Running the order execution tests** (exact command, expected output)
   - **Generating synthetic data** (exact command with all flags explained)
   - **Running the full backtest suite** (exact command)
   - **Interpreting test reports** (where to find them, what PASS/FAIL means)

3. **Update `README.md`:**
   - Add a `## Testing` section near the top (after the project description).
   - Link to `manuals/TESTING.md` and `/logs/` for generated reports.
   - Add a `## Reports` section listing the generated log files.

**Acceptance Criteria:**
- All three existing manuals have a `Last updated:` line at the top with today's date.
- `manuals/TESTING.md` exists and every command in it is copy-paste executable.
- `README.md` contains both the `Testing` and `Reports` sections.

---

## Phase 6 — Strategy Optimization Guide

**Goal:** Data-driven optimization recommendations derived from the 6-month synthetic backtest results.

### Tasks

Create `/strategies/OPTIMIZATION_GUIDE.md` with the following content, populated with actual numbers from Phase 3 backtest results:

```markdown
# Strategy Optimization Guide

Generated: <ISO date>
Data source: 6-month synthetic backtest (GBM, μ=0.0001, σ=0.02)

## Performance Summary

| Strategy | Sharpe Ratio | Max Drawdown | Win Rate | Total Trades |
|----------|-------------|--------------|----------|--------------|
| adx_atr_momentum | ... | ... | ... | ... |
| dynamic_breakout | ... | ... | ... | ... |
| ... | | | | |

## Weaknesses by Strategy

### adx_atr_momentum.py
- [Data-derived finding, e.g.: "Sharpe drops below 0.3 in low-volatility regimes"]
- Recommended fix: [specific parameter change with rationale]

### [Repeat for each strategy]

## Parameter Optimization Recommendations

For each strategy: list the top 1–3 parameters most likely to improve Sharpe Ratio,
with a suggested search range and rationale from the backtest data.

## False Signal Reduction Ideas

- Proposed new filters (volume filter, regime detection, etc.) with implementation sketch.
- Which strategies would benefit most and why.

## Next Steps

Prioritized list of optimizations by expected impact.
```

Parse the actual backtest metrics from the log files or HTML reports generated in Phase 3. Do not fabricate numbers — if a metric cannot be extracted, write `N/A (extraction failed)` and note the log file to check.

**Acceptance Criteria:**
- `/strategies/OPTIMIZATION_GUIDE.md` exists.
- The Performance Summary table has a row for every strategy.
- At least 3 strategies have concrete parameter recommendations (not placeholders).
- No fabricated metrics — all numbers traceable to Phase 3 output files.

---

## Final Deliverables Checklist

Upon completion of all phases, verify and report the status of each item:

```
[ ] /logs/PREFLIGHT_<date>.md
[ ] /logs/phase1_strategy_results.json
[ ] /logs/api_probe_<date>.jsonl
[ ] /logs/phase2_order_results.json
[ ] /logs/phase2_balance_timeline.json
[ ] /logs/phase3_real_backtest_*.log (one per strategy/instrument)
[ ] /logs/phase3_synthetic_backtest_*.log (one per strategy)
[ ] /logs/phase3_report_validation.json
[ ] /logs/TESTREPORT_*.md (one per strategy + API suite)
[ ] automation/tests/run_live_strategy_test.py
[ ] automation/tests/generate_synthetic_data.py
[ ] automation/config/strategy_defaults.json
[ ] automation/strategies/OPTIMIZATION_GUIDE.md
[ ] /manuals/TESTING.md
[ ] /manuals/deployment.md (updated)
[ ] /manuals/backtesting_manual.md (updated)
[ ] /manuals/new_tickers.md (updated)
[ ] README.md (updated)
```

Write this checklist with `[x]`/`[ ]` status to `/logs/FINAL_DELIVERABLES_<date>.md` as your last action.