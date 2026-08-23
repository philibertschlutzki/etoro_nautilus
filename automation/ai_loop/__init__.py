"""
automation/ai_loop — AI-Loop (Issues #1104-#1107): a fully self-contained closed-loop research
assistant on top of the existing optimizer/backtest artifacts.

Architecture constraint (binding for every module in this package):

    The AI-Loop has EXACTLY TWO interfaces to the rest of the repository:

      1. Writing its own log files into the existing ``logs/`` directory
         (``logs/ai_optimization_ledger.jsonl`` — see ``memory.py``).
      2. Reading the SAME shared ``.env``/``.env.example`` the rest of ``automation/`` uses
         (no separate AI-Loop ``.env`` file — see ``client.py``).

    Everything else lives entirely inside ``automation/ai_loop/`` (this package), primarily
    under ``automation/ai_loop/workspace/`` (a runtime scratch area — candidates, rejected
    archives, backtest-run artifacts; see ``workspace/.gitignore``).

    Ingestion (``ingestion.py``) MAY read ``logs/run_*.json`` / ``logs/zusammenfassung_*.md``
    (read-only) and MAY import ``automation.optimizer.deployment_gate`` read-only (reference
    data / informative-only eligibility checks). No module in this package ever performs a git
    operation, and none ever writes to ``automation/strategies/``, ``automation/config/``,
    ``data/optimizer/champions/``, ``strategies.json``, ``automation/daily_orchestrator.py``, or
    ``automation/momentum_ls_run.py``.

    This supersedes the earlier, git-worktree-based design sketched in
    ``manuals/closedloop_issues.md`` (that document predates GitHub Issues #1104-#1107 and is
    kept only as historical background — see its own status banner).

Module map:
    client.py        — Issue #1104: async DeepSeek (R1/V3) HTTP client.
    ingestion.py      — Issue #1104: PerformanceParser (logs/ -> structured context dict).
    memory.py         — Issue #1104: append-only JSONL ledger (logs/ai_optimization_ledger.jsonl).
    validator.py      — Issue #1105: AST safety + scoped ruff/mypy + self-healing.
    reasoning.py       — Issue #1106: StrategyReasoner (R1 hypothesis formulation).
    synthesizer.py     — Issue #1106: CodeSynthesizer (Path A/B mutation materialisation).
    backtest_bridge.py — Issue #1106/#1107: executes a Path-B candidate via the existing engine.
    orchestrator.py    — Issue #1107: AILoopOrchestrator state machine (run_cycle).
"""
