"""
automation/tests/test_ai_loop_orchestrator.py
=================================================
Issue #1107 — tests for automation/ai_loop/orchestrator.py (AILoopOrchestrator.run_cycle).
"""
import ast
import hashlib
import subprocess
from pathlib import Path

import pytest

from automation.ai_loop.memory import LedgerWriter, read_entries
from automation.ai_loop.orchestrator import AILoopOrchestrator, run_loop
from automation.ai_loop.synthesizer import CANDIDATES_DIR, SEARCH_SPACE_CANDIDATE_PATH
from automation.ai_loop.validator import ValidationResult

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AI_LOOP_DIR = REPO_ROOT / "automation" / "ai_loop"
STRATEGIES_DIR = REPO_ROOT / "automation" / "strategies"
CONFIG_DIR = REPO_ROOT / "automation" / "config"
CHAMPIONS_DIR = REPO_ROOT / "data" / "optimizer" / "champions"
STRATEGIES_JSON = CONFIG_DIR / "strategies.json"

CANDIDATE_CODE = '''"""Fixture candidate."""
from __future__ import annotations


class DummyOrchStrategyConfig:
    ema_period: int = 20


class DummyOrchStrategy:
    def on_bar(self, close: float) -> str | None:
        return "BUY" if close > 0 else None
'''


# ---------------------------------------------------------------------------------------------
# Fakes / stubs
# ---------------------------------------------------------------------------------------------

class _FakeReasoner:
    def __init__(self, hypothesis: dict):
        self.hypothesis = hypothesis
        self.calls: list[dict] = []

    async def formulate_hypothesis(self, context: dict) -> dict:
        self.calls.append(context)
        return dict(self.hypothesis)


class _FakeSynthesizer:
    def __init__(self, result: dict):
        self.result = result
        self.calls: list[dict] = []

    async def apply_mutation(self, hypothesis: dict) -> dict:
        self.calls.append(hypothesis)
        return dict(self.result)


class _FakeValidator:
    def __init__(self, ok: bool):
        self.ok = ok
        self.calls: list[Path] = []

    async def validate_code(self, candidate_path: Path) -> ValidationResult:
        self.calls.append(candidate_path)
        return ValidationResult(ok=self.ok, attempts=1, final_code="", history=[])


def _fake_backtest_fn(metrics: dict):
    calls = []

    def _fn(candidate_path, *, symbol, params, run_id, strategy_class_hint=None):
        calls.append({"candidate_path": candidate_path, "symbol": symbol, "params": params,
                       "run_id": run_id, "strategy_class_hint": strategy_class_hint})
        return dict(metrics)

    _fn.calls = calls
    return _fn


def _make_orchestrator(tmp_path, *, hypothesis, synthesis, validator_ok=True, backtest_metrics=None,
                        log_dir=None):
    ledger_path = tmp_path / "ai_optimization_ledger.jsonl"
    orch = AILoopOrchestrator(
        log_dir=log_dir or (tmp_path / "logs"),
        client=object(),  # never actually used — every LLM-touching collaborator is stubbed
        ledger_path=ledger_path,
        backtest_fn=_fake_backtest_fn(backtest_metrics or {"total_trades": 10, "total_return": 0.02}),
        reasoner=_FakeReasoner(hypothesis),
        synthesizer=_FakeSynthesizer(synthesis),
        validator=_FakeValidator(validator_ok),
    )
    return orch, ledger_path


def _write_candidate(name: str) -> Path:
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    path = CANDIDATES_DIR / name
    path.write_text(CANDIDATE_CODE, encoding="utf-8")
    return path


def _hash_dir_or_none(directory: Path) -> dict[str, str] | None:
    if not directory.is_dir():
        return None
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*")) if p.is_file()
    }


def _hash_file_or_none(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


@pytest.fixture
def cleanup_rejected():
    from automation.ai_loop.orchestrator import REJECTED_DIR
    yield
    if REJECTED_DIR.is_dir():
        for p in REJECTED_DIR.glob("*_bridge_orch_*"):
            p.unlink(missing_ok=True)
        for p in REJECTED_DIR.glob("*_orch_*"):
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------------------------
# Happy path: Path B, validation passes, backtest returns metrics, evaluation informative-only
# ---------------------------------------------------------------------------------------------

class TestRunCycleHappyPath:
    @pytest.mark.asyncio
    async def test_full_cycle_reaches_ledger_and_records_verdict(self, tmp_path, cleanup_rejected):
        candidate_path = _write_candidate("orch_happy_candidate.py")
        hypothesis = {"path": "B", "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO",
                      "code_mutation_instructions": "x", "rationale": "r"}
        synthesis = {"path": "B", "candidate_file": str(candidate_path),
                     "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO"}
        orch, ledger_path = _make_orchestrator(
            tmp_path, hypothesis=hypothesis, synthesis=synthesis, validator_ok=True,
            backtest_metrics={"total_trades": 50, "total_return": 0.1},
        )

        result = await orch.run_cycle("TSLA.ETORO", "DummyOrchStrategy")

        assert result.stage_reached == "ledger"
        assert result.outcome in ("accepted", "rejected")  # deployment gate is fail-closed by design
        assert result.deployment_verdict is not None
        assert result.deployment_verdict["admitted"] is False  # no DSR/PBO evidence from ONE backtest
        assert result.deployment_verdict["blocking_clause"] is not None

        entries = list(read_entries(ledger_path))
        assert len(entries) == 1
        assert entries[0]["cycle_id"] == result.cycle_id
        assert entries[0]["symbol"] == "TSLA.ETORO"
        assert entries[0]["outcome"] == result.outcome

        # Rejected (as expected, fail-closed) => candidate archived out of candidates/.
        if result.outcome == "rejected":
            assert not candidate_path.exists()

    @pytest.mark.asyncio
    async def test_path_a_never_calls_backtest_fn(self, tmp_path):
        hypothesis = {"path": "A", "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO",
                      "search_space_overrides": {"ema_period": [5, 25]}}
        synthesis = {"path": "A", "candidate_file": str(SEARCH_SPACE_CANDIDATE_PATH),
                     "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO"}
        orch, ledger_path = _make_orchestrator(tmp_path, hypothesis=hypothesis, synthesis=synthesis)

        result = await orch.run_cycle("TSLA.ETORO", "DummyOrchStrategy")

        assert orch._backtest_fn.calls == []
        assert result.backtest_metrics["path"] == "A"
        assert result.outcome == "rejected"  # path A is never individually deployable
        assert result.deployment_verdict["blocking_clause"] == "path_a_not_individually_deployable"


class TestRunCycleValidationFailure:
    @pytest.mark.asyncio
    async def test_validation_failure_archives_candidate_and_skips_backtest(self, tmp_path, cleanup_rejected):
        candidate_path = _write_candidate("orch_valfail_candidate.py")
        hypothesis = {"path": "B", "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO"}
        synthesis = {"path": "B", "candidate_file": str(candidate_path),
                     "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO"}
        orch, ledger_path = _make_orchestrator(tmp_path, hypothesis=hypothesis, synthesis=synthesis,
                                                 validator_ok=False)

        result = await orch.run_cycle("TSLA.ETORO", "DummyOrchStrategy")

        assert result.outcome == "rejected"
        assert result.stage_reached == "validate"
        assert orch._backtest_fn.calls == []
        assert not candidate_path.exists()  # moved into workspace/rejected/

        entries = list(read_entries(ledger_path))
        assert entries[0]["reason"] == "validation_failed"


class TestRunCycleErrorHandling:
    @pytest.mark.asyncio
    async def test_reasoner_exception_is_captured_not_raised(self, tmp_path):
        class _RaisingReasoner:
            async def formulate_hypothesis(self, context):
                raise RuntimeError("boom")

        ledger_path = tmp_path / "ledger.jsonl"
        orch = AILoopOrchestrator(
            log_dir=tmp_path / "logs", client=object(), ledger_path=ledger_path,
            backtest_fn=_fake_backtest_fn({}), reasoner=_RaisingReasoner(),
            synthesizer=_FakeSynthesizer({}), validator=_FakeValidator(True),
        )

        result = await orch.run_cycle("TSLA.ETORO", "DummyOrchStrategy")

        assert result.outcome == "error"
        assert "boom" in result.error
        entries = list(read_entries(ledger_path))
        assert entries[0]["outcome"] == "error"


class TestRunLoopBounding:
    @pytest.mark.asyncio
    async def test_run_loop_respects_max_iterations(self, tmp_path):
        hypothesis = {"path": "A", "strategy": "S", "symbol": "SYM", "search_space_overrides": {"x": [1, 2]}}
        synthesis = {"path": "A", "candidate_file": str(SEARCH_SPACE_CANDIDATE_PATH), "strategy": "S", "symbol": "SYM"}
        orch, _ = _make_orchestrator(tmp_path, hypothesis=hypothesis, synthesis=synthesis)

        pairs = [("SYM1", "S1"), ("SYM2", "S2"), ("SYM3", "S3")]
        results = await run_loop(pairs, max_iterations=2, orchestrator=orch)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_run_loop_reads_max_iterations_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AI_LOOP_MAX_ITERATIONS", "1")
        hypothesis = {"path": "A", "strategy": "S", "symbol": "SYM", "search_space_overrides": {"x": [1, 2]}}
        synthesis = {"path": "A", "candidate_file": str(SEARCH_SPACE_CANDIDATE_PATH), "strategy": "S", "symbol": "SYM"}
        orch, _ = _make_orchestrator(tmp_path, hypothesis=hypothesis, synthesis=synthesis)

        pairs = [("SYM1", "S1"), ("SYM2", "S2")]
        results = await run_loop(pairs, orchestrator=orch)

        assert len(results) == 1


# ---------------------------------------------------------------------------------------------
# Issue #1107 acceptance: no writes outside automation/ai_loop/+logs/, and no git ops anywhere.
# ---------------------------------------------------------------------------------------------

class TestNoProductionFileMutation:
    @pytest.mark.asyncio
    async def test_full_run_cycle_leaves_production_paths_byte_for_byte_unchanged(self, tmp_path, cleanup_rejected):
        strategies_before = _hash_dir_or_none(STRATEGIES_DIR)
        config_before = _hash_dir_or_none(CONFIG_DIR)
        champions_before = _hash_dir_or_none(CHAMPIONS_DIR)
        strategies_json_before = _hash_file_or_none(STRATEGIES_JSON)

        candidate_path = _write_candidate("orch_immutability_candidate.py")
        hypothesis = {"path": "B", "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO"}
        synthesis = {"path": "B", "candidate_file": str(candidate_path),
                     "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO"}
        orch, _ = _make_orchestrator(tmp_path, hypothesis=hypothesis, synthesis=synthesis,
                                       backtest_metrics={"total_trades": 30, "total_return": 0.03})

        await orch.run_cycle("TSLA.ETORO", "DummyOrchStrategy")

        assert _hash_dir_or_none(STRATEGIES_DIR) == strategies_before
        assert _hash_dir_or_none(CONFIG_DIR) == config_before
        assert _hash_dir_or_none(CHAMPIONS_DIR) == champions_before
        assert _hash_file_or_none(STRATEGIES_JSON) == strategies_json_before


class TestNoGitOperations:
    @pytest.mark.asyncio
    async def test_run_cycle_never_shells_out_to_git(self, tmp_path, monkeypatch, cleanup_rejected):
        real_run = subprocess.run
        recorded_argvs: list[list[str]] = []

        def _spying_run(argv, *args, **kwargs):
            recorded_argvs.append(list(argv) if isinstance(argv, (list, tuple)) else [str(argv)])
            return real_run(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", _spying_run)

        candidate_path = _write_candidate("orch_nogit_candidate.py")
        hypothesis = {"path": "B", "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO"}
        synthesis = {"path": "B", "candidate_file": str(candidate_path),
                     "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO"}
        orch, _ = _make_orchestrator(tmp_path, hypothesis=hypothesis, synthesis=synthesis)

        await orch.run_cycle("TSLA.ETORO", "DummyOrchStrategy")

        for argv in recorded_argvs:
            assert argv[0] != "git" and "git" not in Path(argv[0]).name
            assert not any(a == "git" for a in argv)

    def test_no_ai_loop_source_file_shells_out_to_git(self):
        """Static AST scan (same convention as test_automation_isolation.py): no module under
        automation/ai_loop/ (excluding the runtime workspace/) ever calls subprocess.run/Popen/
        os.system with a 'git' argv[0], and none imports the `git` package."""
        offenders: list[str] = []
        for py_file in AI_LOOP_DIR.rglob("*.py"):
            if "workspace" in py_file.relative_to(AI_LOOP_DIR).parts:
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = (
                        [a.name for a in node.names] if isinstance(node, ast.Import)
                        else [node.module or ""]
                    )
                    if any(n == "git" or n.startswith("git.") for n in names):
                        offenders.append(f"{py_file}: imports 'git'")
                if isinstance(node, ast.Call):
                    first_arg_literal = _first_string_literal_in_call(node)
                    if first_arg_literal and ("git " in first_arg_literal or first_arg_literal.strip() == "git"):
                        offenders.append(f"{py_file}:{node.lineno}: literal 'git' command: {first_arg_literal!r}")
                    for elt in _list_literal_elements(node):
                        if elt == "git":
                            offenders.append(f"{py_file}:{node.lineno}: argv containing 'git'")
        assert offenders == [], f"AI-Loop-Code enthaelt Git-Operationen: {offenders}"


def _first_string_literal_in_call(node: ast.Call) -> str | None:
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    return None


def _list_literal_elements(node: ast.Call) -> list[str]:
    if not node.args:
        return []
    first = node.args[0]
    if isinstance(first, (ast.List, ast.Tuple)):
        return [e.value for e in first.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


# ---------------------------------------------------------------------------------------------
# Ledger writer sanity (re-exercised here at the orchestrator boundary)
# ---------------------------------------------------------------------------------------------

class TestLedgerAtOrchestratorBoundary:
    @pytest.mark.asyncio
    async def test_ledger_entry_contains_full_evaluation_result(self, tmp_path, cleanup_rejected):
        candidate_path = _write_candidate("orch_ledger_candidate.py")
        hypothesis = {"path": "B", "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO"}
        synthesis = {"path": "B", "candidate_file": str(candidate_path),
                     "strategy": "DummyOrchStrategy", "symbol": "TSLA.ETORO"}
        orch, ledger_path = _make_orchestrator(tmp_path, hypothesis=hypothesis, synthesis=synthesis)

        await orch.run_cycle("TSLA.ETORO", "DummyOrchStrategy")

        entries = list(read_entries(ledger_path))
        assert len(entries) == 1
        entry = entries[0]
        assert "deployment_verdict" in entry
        assert "clause_results" in entry["deployment_verdict"]
        assert "hypothesis" in entry
        assert "synthesis" in entry
        assert "backtest_metrics" in entry
