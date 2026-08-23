"""
automation/tests/test_ai_loop_synthesizer.py
================================================
Issue #1106 — tests for automation/ai_loop/synthesizer.py (CodeSynthesizer),
automation/ai_loop/reasoning.py (StrategyReasoner), and automation/ai_loop/backtest_bridge.py.
"""
import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest

import automation.optimizer.spaces as spaces_module
from automation.ai_loop.backtest_bridge import (
    BacktestBridgeError,
    find_strategy_and_config_names,
    load_candidate_module,
    run_candidate_backtest,
)
from automation.ai_loop.reasoning import ReasoningError, StrategyReasoner
from automation.ai_loop.synthesizer import (
    CANDIDATES_DIR,
    SEARCH_SPACE_CANDIDATE_PATH,
    CodeSynthesizer,
    SynthesisError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STRATEGIES_DIR = REPO_ROOT / "automation" / "strategies"
REAL_OVERRIDES_JSON = REPO_ROOT / "automation" / "config" / "search_space_overrides.json"


# ---------------------------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------------------------

class _FakeReasonerClient:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    async def call_reasoner(self, payload: dict) -> str:
        self.calls.append(payload)
        return self.response


class _FakeChatClient:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[str] = []

    async def call_chat(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


CANDIDATE_STRATEGY_CODE = '''"""AI-Loop-generated candidate."""
from __future__ import annotations


class DummyMomentumConfig:
    ema_period: int = 20


class DummyMomentumStrategy:
    def __init__(self, config: DummyMomentumConfig) -> None:
        self.config = config

    def on_bar(self, close: float) -> str | None:
        return "BUY" if close > 0 else None
'''


def _cleanup_workspace_file(path: Path) -> None:
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------------------------
# StrategyReasoner
# ---------------------------------------------------------------------------------------------

class TestStrategyReasoner:
    @pytest.mark.asyncio
    async def test_parses_clean_json_response(self):
        response = json.dumps({
            "path": "A", "strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
            "rationale": "boundary_hit_fraction hoch", "confidence": 0.8,
            "search_space_overrides": {"adx_period": [5, 25]}, "code_mutation_instructions": "",
        })
        client = _FakeReasonerClient(response)
        reasoner = StrategyReasoner(client)

        hypothesis = await reasoner.formulate_hypothesis({"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO"})

        assert hypothesis["path"] == "A"
        assert hypothesis["search_space_overrides"] == {"adx_period": [5, 25]}
        assert len(client.calls) == 1
        assert client.calls[0]["messages"][0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_parses_markdown_fenced_json_response(self):
        response = "Hier ist meine Analyse:\n```json\n" + json.dumps({
            "path": "B", "code_mutation_instructions": "Filter ergaenzen",
        }) + "\n```\nEnde."
        client = _FakeReasonerClient(response)
        reasoner = StrategyReasoner(client)

        hypothesis = await reasoner.formulate_hypothesis({"strategy": "X", "symbol": "Y"})
        assert hypothesis["path"] == "B"
        assert hypothesis["code_mutation_instructions"] == "Filter ergaenzen"

    @pytest.mark.asyncio
    async def test_defaults_strategy_and_symbol_from_context(self):
        response = json.dumps({"path": "B", "code_mutation_instructions": "x"})
        client = _FakeReasonerClient(response)
        reasoner = StrategyReasoner(client)

        hypothesis = await reasoner.formulate_hypothesis({"strategy": "S1", "symbol": "SYM1"})
        assert hypothesis["strategy"] == "S1"
        assert hypothesis["symbol"] == "SYM1"

    @pytest.mark.asyncio
    async def test_missing_path_raises_reasoning_error(self):
        client = _FakeReasonerClient(json.dumps({"rationale": "kein Pfad"}))
        reasoner = StrategyReasoner(client)
        with pytest.raises(ReasoningError):
            await reasoner.formulate_hypothesis({"strategy": "S", "symbol": "T"})

    @pytest.mark.asyncio
    async def test_unparsable_response_raises_reasoning_error(self):
        client = _FakeReasonerClient("Ich kann das nicht als JSON ausdruecken.")
        reasoner = StrategyReasoner(client)
        with pytest.raises(ReasoningError):
            await reasoner.formulate_hypothesis({"strategy": "S", "symbol": "T"})


# ---------------------------------------------------------------------------------------------
# CodeSynthesizer — Path A
# ---------------------------------------------------------------------------------------------

class TestCodeSynthesizerPathA:
    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        _cleanup_workspace_file(SEARCH_SPACE_CANDIDATE_PATH)

    @pytest.mark.asyncio
    async def test_writes_only_candidate_json_with_correct_schema(self):
        hypothesis = {
            "path": "A", "strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
            "rationale": "boundary_hit_fraction=0.42 > 0.3",
            "search_space_overrides": {"adx_period": [7, 21], "cooldown_bars": [2, 12]},
        }
        synthesizer = CodeSynthesizer(client=None)  # Path A needs no LLM call

        result = await synthesizer.apply_mutation(hypothesis)

        assert result["path"] == "A"
        assert Path(result["candidate_file"]) == SEARCH_SPACE_CANDIDATE_PATH
        assert SEARCH_SPACE_CANDIDATE_PATH.exists()

        payload = json.loads(SEARCH_SPACE_CANDIDATE_PATH.read_text("utf-8"))
        assert "overrides" in payload
        assert payload["overrides"] == {
            "AdxAtrMomentumStrategy": {"TSLA.ETORO": {"adx_period": [7, 21], "cooldown_bars": [2, 12]}}
        }

    @pytest.mark.asyncio
    async def test_never_touches_real_search_space_overrides_json(self):
        before = REAL_OVERRIDES_JSON.read_bytes() if REAL_OVERRIDES_JSON.exists() else None
        hypothesis = {
            "path": "A", "strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
            "search_space_overrides": {"adx_period": [7, 21]},
        }
        await CodeSynthesizer(client=None).apply_mutation(hypothesis)

        after = REAL_OVERRIDES_JSON.read_bytes() if REAL_OVERRIDES_JSON.exists() else None
        assert before == after

    @pytest.mark.asyncio
    async def test_rejects_malformed_bounds(self):
        hypothesis = {
            "path": "A", "strategy": "S", "symbol": "T",
            "search_space_overrides": {"adx_period": [21]},  # not a [low, high] pair
        }
        with pytest.raises(SynthesisError):
            await CodeSynthesizer(client=None).apply_mutation(hypothesis)

    @pytest.mark.asyncio
    async def test_rejects_inverted_bounds(self):
        hypothesis = {
            "path": "A", "strategy": "S", "symbol": "T",
            "search_space_overrides": {"adx_period": [25, 5]},
        }
        with pytest.raises(SynthesisError):
            await CodeSynthesizer(client=None).apply_mutation(hypothesis)

    @pytest.mark.asyncio
    async def test_candidate_json_loads_through_real_spaces_loader(self, monkeypatch, tmp_path):
        """Issue #1106 acceptance: the candidate file uses the EXACT schema
        automation.optimizer.spaces._load_search_space_overrides expects — proven by loading it
        through the REAL loader function (pointed, via ETORO_CONFIG_DIR, at a throwaway config
        dir seeded from the candidate's own JSON — automation/config/ itself is never touched)."""
        hypothesis = {
            "path": "A", "strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
            "search_space_overrides": {"adx_period": [7, 21]},
        }
        await CodeSynthesizer(client=None).apply_mutation(hypothesis)
        candidate_payload = json.loads(SEARCH_SPACE_CANDIDATE_PATH.read_text("utf-8"))

        fake_config_dir = tmp_path / "config"
        fake_config_dir.mkdir()
        # spaces.py hardcodes the filename "search_space_overrides.json" under config_dir().
        (fake_config_dir / "search_space_overrides.json").write_text(
            json.dumps({"overrides": candidate_payload["overrides"]}), encoding="utf-8"
        )
        monkeypatch.setenv("ETORO_CONFIG_DIR", str(fake_config_dir))
        monkeypatch.setattr(spaces_module, "_search_space_overrides_cache", None)

        loaded = spaces_module._load_search_space_overrides()

        assert loaded == candidate_payload["overrides"]
        assert loaded["AdxAtrMomentumStrategy"]["TSLA.ETORO"]["adx_period"] == [7, 21]

        monkeypatch.setattr(spaces_module, "_search_space_overrides_cache", None)


# ---------------------------------------------------------------------------------------------
# CodeSynthesizer — Path B
# ---------------------------------------------------------------------------------------------

class TestCodeSynthesizerPathB:
    @pytest.fixture(autouse=True)
    def _cleanup(self):
        created: list[Path] = []
        self._created = created
        yield
        for p in created:
            _cleanup_workspace_file(p)

    @pytest.mark.asyncio
    async def test_writes_only_candidate_py_file_in_candidates_dir(self):
        hypothesis = {
            "path": "B", "strategy": "DummyMomentumStrategy", "symbol": "TSLA.ETORO",
            "code_mutation_instructions": "Add a volume filter", "rationale": "signal quality",
        }
        client = _FakeChatClient(CANDIDATE_STRATEGY_CODE)
        result = await CodeSynthesizer(client=client).apply_mutation(hypothesis)
        self._created.append(Path(result["candidate_file"]))

        candidate_path = Path(result["candidate_file"])
        assert candidate_path.parent == CANDIDATES_DIR
        assert candidate_path.name == "DummyMomentumStrategy_TSLA_ETORO.py"
        assert candidate_path.read_text("utf-8").strip() == CANDIDATE_STRATEGY_CODE.strip()
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_strips_markdown_code_fence(self):
        fenced = "```python\n" + CANDIDATE_STRATEGY_CODE + "\n```"
        hypothesis = {"path": "B", "strategy": "S", "symbol": "SYM", "code_mutation_instructions": "x"}
        client = _FakeChatClient(fenced)
        result = await CodeSynthesizer(client=client).apply_mutation(hypothesis)
        self._created.append(Path(result["candidate_file"]))

        code = Path(result["candidate_file"]).read_text("utf-8")
        assert "```" not in code
        assert "class DummyMomentumStrategy" in code

    @pytest.mark.asyncio
    async def test_never_touches_automation_strategies_dir(self):
        before = _hash_dir(STRATEGIES_DIR)
        hypothesis = {"path": "B", "strategy": "S", "symbol": "SYM", "code_mutation_instructions": "x"}
        client = _FakeChatClient(CANDIDATE_STRATEGY_CODE)
        result = await CodeSynthesizer(client=client).apply_mutation(hypothesis)
        self._created.append(Path(result["candidate_file"]))

        after = _hash_dir(STRATEGIES_DIR)
        assert before == after

    @pytest.mark.asyncio
    async def test_raises_without_client(self):
        hypothesis = {"path": "B", "strategy": "S", "symbol": "SYM", "code_mutation_instructions": "x"}
        with pytest.raises(SynthesisError):
            await CodeSynthesizer(client=None).apply_mutation(hypothesis)

    @pytest.mark.asyncio
    async def test_unknown_path_raises(self):
        with pytest.raises(SynthesisError):
            await CodeSynthesizer(client=_FakeChatClient("x")).apply_mutation({"path": "C"})


class TestFullSynthesisRunLeavesProductionFilesUnchanged:
    """Issue #1106 acceptance: after a full synthesis run (Path A + Path B), automation/strategies/
    and automation/config/search_space_overrides.json are byte-for-byte unchanged."""

    @pytest.mark.asyncio
    async def test_hash_unchanged_after_full_run(self):
        strategies_before = _hash_dir(STRATEGIES_DIR)
        overrides_before = REAL_OVERRIDES_JSON.read_bytes() if REAL_OVERRIDES_JSON.exists() else None

        path_a_hypothesis = {
            "path": "A", "strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
            "search_space_overrides": {"adx_period": [7, 21]},
        }
        path_b_hypothesis = {
            "path": "B", "strategy": "DummyMomentumStrategy", "symbol": "TSLA.ETORO",
            "code_mutation_instructions": "x",
        }
        synth_a = CodeSynthesizer(client=None)
        synth_b = CodeSynthesizer(client=_FakeChatClient(CANDIDATE_STRATEGY_CODE))
        result_a = await synth_a.apply_mutation(path_a_hypothesis)
        result_b = await synth_b.apply_mutation(path_b_hypothesis)

        try:
            strategies_after = _hash_dir(STRATEGIES_DIR)
            overrides_after = REAL_OVERRIDES_JSON.read_bytes() if REAL_OVERRIDES_JSON.exists() else None
            assert strategies_before == strategies_after
            assert overrides_before == overrides_after
        finally:
            _cleanup_workspace_file(Path(result_a["candidate_file"]))
            _cleanup_workspace_file(Path(result_b["candidate_file"]))


def _hash_dir(directory: Path) -> dict[str, str]:
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*")) if p.is_file()
    }


# ---------------------------------------------------------------------------------------------
# backtest_bridge
# ---------------------------------------------------------------------------------------------

class TestBacktestBridgeModuleLoading:
    @pytest.fixture
    def candidate_file(self):
        created: list[Path] = []

        def _make(code: str, name: str | None = None) -> Path:
            CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
            filename = name or f"bridge_test_{uuid.uuid4().hex}.py"
            path = CANDIDATES_DIR / filename
            path.write_text(code, encoding="utf-8")
            created.append(path)
            return path

        yield _make
        for p in created:
            p.unlink(missing_ok=True)

    def test_load_candidate_module_registers_namespaced_sys_module(self, candidate_file):
        path = candidate_file(CANDIDATE_STRATEGY_CODE, name="bridge_load_test.py")
        module = load_candidate_module(path)
        try:
            assert module.__name__ == "automation.ai_loop.workspace.candidates.bridge_load_test"
            assert module.__name__ in sys.modules
            assert hasattr(module, "DummyMomentumStrategy")
            # Never registered under the real automation.strategies namespace.
            assert "automation.strategies.bridge_load_test" not in sys.modules
        finally:
            sys.modules.pop(module.__name__, None)

    def test_load_candidate_module_missing_file_raises(self, tmp_path):
        with pytest.raises(BacktestBridgeError):
            load_candidate_module(tmp_path / "does_not_exist.py")

    def test_find_strategy_and_config_names_via_hint(self, candidate_file):
        path = candidate_file(CANDIDATE_STRATEGY_CODE, name="bridge_hint_test.py")
        module = load_candidate_module(path)
        try:
            strategy_name, config_name = find_strategy_and_config_names(module, "DummyMomentumStrategy")
            assert strategy_name == "DummyMomentumStrategy"
            assert config_name == "DummyMomentumConfig"
        finally:
            sys.modules.pop(module.__name__, None)

    def test_find_strategy_and_config_names_via_heuristic(self, candidate_file):
        path = candidate_file(CANDIDATE_STRATEGY_CODE, name="bridge_heuristic_test.py")
        module = load_candidate_module(path)
        try:
            strategy_name, config_name = find_strategy_and_config_names(module, strategy_class_hint=None)
            assert strategy_name == "DummyMomentumStrategy"
            assert config_name == "DummyMomentumConfig"
        finally:
            sys.modules.pop(module.__name__, None)

    def test_find_strategy_and_config_names_raises_when_ambiguous(self, candidate_file):
        ambiguous_code = CANDIDATE_STRATEGY_CODE + '''

class SecondStrategy:
    pass
'''
        path = candidate_file(ambiguous_code, name="bridge_ambiguous_test.py")
        module = load_candidate_module(path)
        try:
            with pytest.raises(BacktestBridgeError):
                find_strategy_and_config_names(module, strategy_class_hint=None)
        finally:
            sys.modules.pop(module.__name__, None)


class TestRunCandidateBacktestWiring:
    """Exercises the wiring around run_single_backtest_worker via dependency injection
    (worker_fn) — nautilus_trader/pyarrow are not required to run this test."""

    @pytest.fixture
    def candidate_file(self):
        created: list[Path] = []

        def _make(code: str, name: str) -> Path:
            CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
            path = CANDIDATES_DIR / name
            path.write_text(code, encoding="utf-8")
            created.append(path)
            return path

        yield _make
        for p in created:
            p.unlink(missing_ok=True)

    def test_run_candidate_backtest_invokes_injected_worker_with_expected_strat_dict(self, candidate_file):
        path = candidate_file(CANDIDATE_STRATEGY_CODE, "bridge_run_test.py")
        captured = {}

        def fake_worker(**kwargs):
            captured.update(kwargs)
            return {"total_trades": 42, "total_return": 0.05}

        run_id = f"test_{uuid.uuid4().hex}"
        result = run_candidate_backtest(
            path, symbol="TSLA.ETORO", params={"ema_period": 15}, run_id=run_id,
            strategy_class_hint="DummyMomentumStrategy", worker_fn=fake_worker,
        )

        assert result == {"total_trades": 42, "total_return": 0.05}
        assert captured["inst_id_str"] == "TSLA.ETORO"
        assert captured["bar_type"] == "TSLA.ETORO-1-HOUR-MID-INTERNAL"
        assert captured["strat"]["strategy_class"] == "DummyMomentumStrategy"
        assert captured["strat"]["config_class"] == "DummyMomentumConfig"
        assert captured["strat"]["strategy_module"] == "automation.ai_loop.workspace.candidates.bridge_run_test"
        assert captured["strat"]["params"] == {"ema_period": 15}
        assert captured["generate_html_report"] is False

        # Module is unregistered from sys.modules again after the run.
        assert captured["strat"]["strategy_module"] not in sys.modules

        # Every write this triggered lives under automation/ai_loop/workspace/backtest_runs/<run_id>/.
        run_dir = Path("automation/ai_loop/workspace/backtest_runs") / run_id
        assert run_dir.is_dir()
        assert (run_dir / "worker.log").exists()
        for p in run_dir.rglob("*"):
            if p.is_file():
                p.unlink()
        for p in sorted(run_dir.rglob("*"), reverse=True):
            if p.is_dir():
                p.rmdir()
        run_dir.rmdir()

    def test_run_candidate_backtest_wraps_worker_exception(self, candidate_file):
        path = candidate_file(CANDIDATE_STRATEGY_CODE, "bridge_error_test.py")

        def failing_worker(**kwargs):
            raise RuntimeError("boom")

        run_id = f"test_{uuid.uuid4().hex}"
        with pytest.raises(BacktestBridgeError):
            run_candidate_backtest(
                path, symbol="TSLA.ETORO", params={}, run_id=run_id,
                strategy_class_hint="DummyMomentumStrategy", worker_fn=failing_worker,
            )
        # Cleanup any partial run dir.
        run_dir = Path("automation/ai_loop/workspace/backtest_runs") / run_id
        if run_dir.exists():
            for p in sorted(run_dir.rglob("*"), reverse=True):
                p.unlink() if p.is_file() else p.rmdir()
            run_dir.rmdir()
