import re

with open("automation/backtest_runner.py", "r") as f:
    content = f.read()

# Fix the issue: what if OOS is disabled or missing entirely in a standard backtest?
# The prompt's comment asks: "Was passiert bei einem Standard-Backtest, der gar kein Walk-Forward/OOS konfiguriert hat?"
# In the current implementation, if check_oos was false in the past, it just checked IS.
# Wait, the codebase actually evaluates OOS unconditionally in the runner if we pass the tests.
# But actually, there's `check_oos` flag usually passed to the runner or defined in `tournament.json`.
# We should probably only enforce OOS if `oos_metrics` is required by the orchestrator, or if `check_oos` was passed?
# The user's prompt originally said: "Da die Funktion `_evaluate_oos_eligibility` bereits perfekt und robust für OOS funktioniert... das kombinierte Gating direkt in `select_winners` verschränken."
# But the reviewer noticed a flaw: if OOS is disabled, the comprehension still evaluates `_evaluate_oos_eligibility(...).get("oos_eligible", False)`. Since `oos_metrics` would be None, `_evaluate_oos_eligibility` returns `{"oos_eligible": False}`. Thus, ALL strategies are rejected if there's no OOS.

# Let's see if there is a tournament configuration flag for check_oos. No, in `test_tournament_validation.py` we saw `tournament_cfg` and it has `oos_min_trades` etc.
# Usually, in Phase 5 the orchestrator evaluates this.

# Let's adjust `select_winners` to allow skipping OOS gating if OOS is not evaluated.
# Wait, let's look at `_evaluate_oos_eligibility`.
# We could do:
# `and (not tournament_cfg.get("check_oos", False) or _evaluate_oos_eligibility(...)`
# Is there a `check_oos`? Wait, `select_winners` previously DID NOT have `check_oos` parameter! The old code:
# `if r.get("metrics") and _is_eligible(r["metrics"], tournament_cfg, ...)`
# `check_oos` was NOT passed to `_is_eligible` inside `select_winners`, so it defaulted to `False`. This means previously `select_winners` ONLY checked IS!
# Wait, so how did it check OOS?
# Reviewer comment: "Die eigentliche OOS-Prüfung (`_evaluate_oos_eligibility`) wird erst bei der Zuweisung in `per_symbol_winners` aufgerufen, filtert die Paare zu diesem Zeitpunkt aber nicht mehr hart aus der Kandidatenliste."
# "Das kombinierte Gate muss prüfen, ob OOS laut tournament_cfg überhaupt aktiv ist." or "Besitzt einen OOS-Disabled-Bypass".

# Let's modify `select_winners` to check if `r.get("oos_metrics")` is None, we should fail IF OOS is expected? Or we can check if tournament_cfg has OOS enabled?
# Actually, the user comment explicitly asked: "Das kombinierte Gate muss prüfen, ob OOS laut tournament_cfg überhaupt aktiv ist."

# Let's look at `select_winners` signature and usage.
# If `tournament_cfg` has `check_oos`? Or do we pass `check_oos`?
# The `daily_orchestrator` says `walk_forward_active: false` but it uses `oos_span_days`.
# If `oos_metrics` is present, we evaluate it. If not, do we pass?
