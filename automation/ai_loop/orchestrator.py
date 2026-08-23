"""
automation/ai_loop/orchestrator.py
====================================
Issue #1107 — State machine wiring: Ingest (#1104, read-only) -> Reason (#1106) ->
Synthesize (#1106, writes only to automation/ai_loop/workspace/) -> Validate (#1105, with
self-healing retries) -> Backtest (via backtest_bridge.py, #1106's option (b)) -> Evaluate
(read-only, informative-only deployment-gate check) -> Ledger entry (#1104's memory.py).

Fully self-contained (see the architecture constraint in ``automation/ai_loop/__init__.py``):
  * NO git operations anywhere in this module or anything it calls.
  * NO writes outside ``automation/ai_loop/`` (primarily ``workspace/``) and
    ``logs/ai_optimization_ledger.jsonl``.
  * The "Evaluate" step calls ``automation.optimizer.deployment_gate.evaluate_deployment_eligibility``
    PURELY to log a would-pass/would-fail verdict — it is handed an IN-MEMORY-ONLY promotion
    record built from this cycle's own backtest result (see ``_build_inmemory_promotion_record``),
    never a record read from ``data/optimizer/proposal_*.json``, and its verdict is NEVER written
    back to ``data/optimizer/champions/``, ``strategy_symbol_seeds.json``, or ``strategies.json``.
  * On rejection, the candidate file(s) in ``automation/ai_loop/workspace/`` are moved into
    ``automation/ai_loop/workspace/rejected/`` — a pure filesystem move. No repo-level rollback
    is needed because nothing outside ``automation/ai_loop/``+``logs/`` was ever touched (this
    supersedes ``manuals/closedloop_issues.md``'s original ``git checkout --``-based rollback
    sketch — see that file's own status banner and the Owner-Klarstellung this package follows).

``run_cycle`` is ``async`` (unlike the manual's original synchronous sketch) because every LLM
call in the pipeline it wires together (Reason via R1, Synthesize Path B / self-healing via V3)
is itself async — see ``client.DeepSeekClient``.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from automation.ai_loop import backtest_bridge, memory
from automation.ai_loop.client import DeepSeekClient
from automation.ai_loop.ingestion import PerformanceParser
from automation.ai_loop.reasoning import ReasoningError, StrategyReasoner
from automation.ai_loop.synthesizer import CodeSynthesizer, SynthesisError
from automation.ai_loop.validator import StaticValidator

logger = logging.getLogger(__name__)

_THIS_DIR = Path(__file__).resolve().parent          # automation/ai_loop
_AUTOMATION_DIR = _THIS_DIR.parent                    # automation/
_ENV_FILE = _AUTOMATION_DIR / ".env"
if not _ENV_FILE.exists():
    _ENV_FILE = _AUTOMATION_DIR.parent / ".env"
load_dotenv(str(_ENV_FILE))

REJECTED_DIR = _THIS_DIR / "workspace" / "rejected"
DEFAULT_LOG_DIR = _AUTOMATION_DIR.parent / "logs"


def _default_max_iterations() -> int:
    return int(os.getenv("AI_LOOP_MAX_ITERATIONS", 1))


@dataclasses.dataclass
class CycleResult:
    cycle_id: str
    symbol: str
    strategy: str
    stage_reached: str
    outcome: str  # "accepted" | "rejected" | "error"
    hypothesis: dict[str, Any] | None = None
    synthesis: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    backtest_metrics: dict[str, Any] | None = None
    deployment_verdict: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class AILoopOrchestrator:
    """Issue #1107 — wires ingestion/reasoning/synthesis/validation/backtest/evaluation into a
    single bounded cycle and appends exactly one ledger entry per cycle via ``memory.py``."""

    def __init__(
        self,
        *,
        log_dir: Path | None = None,
        client: DeepSeekClient | None = None,
        ledger_path: Path | None = None,
        backtest_fn: Callable[..., dict[str, Any]] | None = None,
        reasoner: StrategyReasoner | None = None,
        synthesizer: CodeSynthesizer | None = None,
        validator: StaticValidator | None = None,
    ) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
        self.client = client or DeepSeekClient()
        self.parser = PerformanceParser(self.log_dir)
        self.reasoner = reasoner or StrategyReasoner(self.client)
        self.synthesizer = synthesizer or CodeSynthesizer(self.client)
        self.validator = validator or StaticValidator(client=self.client)
        self.ledger = memory.LedgerWriter(ledger_path)
        self._backtest_fn = backtest_fn or backtest_bridge.run_candidate_backtest

    async def run_cycle(self, symbol: str, strategy: str, *, history_depth: int = 3) -> CycleResult:
        """Runs exactly one Ingest->Reason->Synthesize->Validate->Backtest->Evaluate->Ledger
        cycle for ``(symbol, strategy)``. Never raises — every failure mode is captured into
        the returned ``CycleResult`` (``outcome="error"``) AND logged to the ledger, so a caller
        looping over many pairs (``run_loop``) never has one bad cycle abort the whole run."""
        cycle_id = uuid.uuid4().hex
        result = CycleResult(cycle_id=cycle_id, symbol=symbol, strategy=strategy,
                              stage_reached="ingest", outcome="error")
        try:
            context = self.parser.extract_run_context(symbol, strategy, history_depth=history_depth)

            result.stage_reached = "reason"
            hypothesis = await self.reasoner.formulate_hypothesis(context)
            result.hypothesis = hypothesis

            result.stage_reached = "synthesize"
            synthesis = await self.synthesizer.apply_mutation(hypothesis)
            result.synthesis = synthesis

            if synthesis["path"] == "B":
                result.stage_reached = "validate"
                validation = await self.validator.validate_code(Path(synthesis["candidate_file"]))
                result.validation = validation.to_dict()
                if not validation.ok:
                    result.outcome = "rejected"
                    self._archive_rejected(synthesis)
                    self._write_ledger(result, reason="validation_failed")
                    return result

            result.stage_reached = "backtest"
            result.backtest_metrics = self._run_backtest(synthesis, symbol, strategy, cycle_id)

            result.stage_reached = "evaluate"
            result.deployment_verdict = self._evaluate(strategy, symbol, result.backtest_metrics)

            result.stage_reached = "ledger"
            if result.deployment_verdict.get("admitted"):
                result.outcome = "accepted"
            else:
                result.outcome = "rejected"
                self._archive_rejected(synthesis)

            self._write_ledger(result, reason=result.deployment_verdict.get("blocking_clause"))
            return result

        except (ReasoningError, SynthesisError, backtest_bridge.BacktestBridgeError) as exc:
            result.outcome = "error"
            result.error = f"{type(exc).__name__}: {exc}"
            self._write_ledger(result, reason="pipeline_error")
            return result
        except Exception as exc:  # fail-loud into the ledger, never crash the caller's loop
            logger.exception("AI-Loop-Zyklus %s (%s/%s) abgebrochen (unerwarteter Fehler).",
                              cycle_id, strategy, symbol)
            result.outcome = "error"
            result.error = f"{type(exc).__name__}: {exc}"
            self._write_ledger(result, reason="unexpected_error")
            return result

    # ---- internals --------------------------------------------------------------------------

    def _run_backtest(self, synthesis: dict[str, Any], symbol: str, strategy: str, cycle_id: str) -> dict[str, Any]:
        if synthesis["path"] == "A":
            # A search-space override changes the OPTIMIZER's sampling bounds for the NEXT
            # sweep — it isn't itself a single backtestable artifact. The orchestrator logs the
            # candidate override file to the ledger for human pickup; it never runs a sweep
            # itself (no Optuna study, no writes to data/optimizer/ — that would violate the
            # "logs/ + automation/ai_loop/ only" write boundary).
            return {"path": "A", "note": "search_space_override_candidate_logged_only",
                     "candidate_file": synthesis.get("candidate_file")}
        return self._backtest_fn(
            Path(synthesis["candidate_file"]),
            symbol=symbol,
            params={},
            run_id=cycle_id,
            strategy_class_hint=strategy,
        )

    def _evaluate(self, strategy: str, symbol: str, backtest_metrics: dict[str, Any]) -> dict[str, Any]:
        """Read-only, informative-only deployment-gate check: NEVER writes to
        ``data/optimizer/champions/``, ``strategy_symbol_seeds.json``, or ``strategies.json`` —
        only returns a would-pass/would-fail verdict for the ledger."""
        if backtest_metrics.get("path") == "A":
            return {"admitted": False, "blocking_clause": "path_a_not_individually_deployable",
                     "clause_results": {}}
        try:
            from automation.optimizer.deployment_gate import evaluate_deployment_eligibility  # lazy
        except ImportError as exc:
            return {"admitted": False, "blocking_clause": "deployment_gate_unavailable",
                     "clause_results": {}, "error": str(exc)}

        record = _build_inmemory_promotion_record(backtest_metrics)
        try:
            decision = evaluate_deployment_eligibility(
                (strategy, symbol), {(strategy, symbol): record}, {},
            )
        except Exception as exc:
            return {"admitted": False, "blocking_clause": "evaluation_error",
                     "clause_results": {}, "error": str(exc)}
        return decision.to_dict()

    def _archive_rejected(self, synthesis: dict[str, Any]) -> None:
        """Pure filesystem move of a rejected candidate into ``workspace/rejected/`` — no
        repo-level rollback is needed since nothing outside ``automation/ai_loop/``+``logs/``
        was ever touched (see module docstring)."""
        candidate_file = synthesis.get("candidate_file")
        if not candidate_file:
            return
        src = Path(candidate_file)
        if not src.exists():
            return
        REJECTED_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        dest = REJECTED_DIR / f"{stamp}_{src.name}"
        shutil.move(str(src), str(dest))
        synthesis["archived_to"] = str(dest)

    def _write_ledger(self, result: CycleResult, *, reason: str | None) -> dict[str, Any]:
        entry = result.to_dict()
        entry["reason"] = reason
        return self.ledger.append(entry)


def _build_inmemory_promotion_record(metrics: dict[str, Any]) -> dict[str, Any]:
    """Best-effort, IN-MEMORY-ONLY mapping from a single-shot candidate backtest result onto the
    flat record shape ``deployment_gate.evaluate_deployment_eligibility`` expects (see
    ``deployment_gate.build_promotion_record_from_proposal``'s docstring for the canonical field
    names) — NEVER read from or written to disk.

    A single ad-hoc backtest genuinely cannot supply most of these fields: DSR/PBO/bootstrap-CI
    need a full multi-trial Optuna study, not one backtest. Those clauses legitimately evaluate
    to ``None`` (fail-closed, per ``deployment_gate``'s own "nicht geprueft ist keine bestandene
    Pruefung" rule) — this is the CORRECT, honest outcome for a single-shot AI-Loop candidate,
    not a bug: it accurately signals "not enough evidence yet", matching the project's
    human-in-the-loop promotion philosophy (``automation/AGENTS.md`` §12.5)."""
    if not metrics:
        return {}
    total_trades = metrics.get("total_trades") or metrics.get("holdout_total_trades") or 0
    return {
        "status": "READY_FOR_PR" if total_trades else None,
        "data_snapshot_sha256": None,
        "deflated_dsr": None,
        "oos_psr": None,
        "holdout_ci_lower_sortino": None,
        "pbo": None,
        "pbo_n_configs": None,
        "blocking_invariant_names": None,
        "expectancy_cost_stress_2x": None,
        "holdout_expectancy_notional_weighted": metrics.get("expectancy") or metrics.get("total_return"),
        "holdout_expectancy_winsorized": None,
        "R_symbol": None,
        "R_global": None,
        "promotion_margin": None,
        "run_id": None,
    }


async def run_cycle(symbol: str, strategy: str, **kwargs: Any) -> CycleResult:
    """Module-level convenience wrapper (matches the task's ``run_cycle(...)`` naming) —
    constructs a default ``AILoopOrchestrator`` and delegates."""
    return await AILoopOrchestrator().run_cycle(symbol, strategy, **kwargs)


async def run_loop(
    pairs: list[tuple[str, str]], *, max_iterations: int | None = None,
    orchestrator: AILoopOrchestrator | None = None,
) -> list[CycleResult]:
    """Bounded loop over ``pairs`` (``(symbol, strategy)``) — bounded by
    ``AI_LOOP_MAX_ITERATIONS`` from the shared env (or the ``max_iterations`` override)."""
    limit = max_iterations if max_iterations is not None else _default_max_iterations()
    orch = orchestrator or AILoopOrchestrator()
    results: list[CycleResult] = []
    for symbol, strategy in pairs[:limit]:
        results.append(await orch.run_cycle(symbol, strategy))
    return results
