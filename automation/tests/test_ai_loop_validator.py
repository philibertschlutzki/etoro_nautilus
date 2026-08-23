"""
automation/tests/test_ai_loop_validator.py
=============================================
Issue #1105 — tests for automation/ai_loop/validator.py (StaticValidator).

Candidate files are written under the REAL automation/ai_loop/workspace/candidates/ directory
(the only path StaticValidator is allowed to inspect — see _assert_candidate_path) and removed
again by the `candidate_file` fixture; that directory is already git-ignored (see
automation/ai_loop/workspace/.gitignore) so this never pollutes `git status`.
"""
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

from automation.ai_loop.validator import (
    CANDIDATES_ROOT,
    StaticValidator,
    UnsafeWorkspacePathError,
)

CLEAN_CODE = '''"""Clean AI-Loop candidate fixture."""
from __future__ import annotations


class DummyCandidate:
    """A trivial, syntactically/typewise clean stand-in for a strategy candidate."""

    def add(self, a: int, b: int) -> int:
        return a + b
'''

BLOCKING_CALL_CODE = '''"""Candidate with a blocking call."""
from __future__ import annotations

import time


class DummyCandidate:
    def on_bar(self) -> None:
        time.sleep(1)
'''

SYNC_HTTP_CODE = '''"""Candidate importing a synchronous HTTP client."""
from __future__ import annotations

import requests


class DummyCandidate:
    def on_bar(self) -> None:
        requests.get("https://example.invalid")
'''

LOOKAHEAD_SHIFT_CODE = '''"""Candidate reading a future row via a negative pandas shift."""
from __future__ import annotations

import pandas as pd


class DummyCandidate:
    def on_bar(self, series: "pd.Series") -> "pd.Series":
        return series.shift(-1)
'''

LOOKAHEAD_INDEX_CODE = '''"""Candidate indexing a future bar via a positive offset."""
from __future__ import annotations


class DummyCandidate:
    def on_bar(self, bars: list, i: int) -> object:
        return bars[i + 1]
'''

STATE_BLEED_STYLE_CODE = '''"""Candidate that merely reads its own running indicator state across bars.

This is the README.md §9 "state bleed" pattern (continuous engine, no IS/OOS reset) as it
appears in ordinary strategy code: consuming self.rsi.value across on_bar calls. It must NOT be
flagged by check_ast_safety.
"""
from __future__ import annotations


class DummyCandidate:
    def __init__(self) -> None:
        self.rsi_value = 0.0

    def on_bar(self, new_value: float) -> bool:
        previous = self.rsi_value
        self.rsi_value = new_value
        return new_value > previous
'''

LINT_ERROR_CODE = '''"""Candidate with an unused import (ruff F401)."""
from __future__ import annotations

import os


class DummyCandidate:
    def add(self, a: int, b: int) -> int:
        return a + b
'''

TYPE_ERROR_CODE = '''"""Candidate with a type error (mypy --strict)."""
from __future__ import annotations


class DummyCandidate:
    def add(self, a: int, b: int) -> str:
        return a + b
'''


@pytest.fixture
def candidate_file():
    created: list[Path] = []

    def _make(code: str, name: str | None = None) -> Path:
        CANDIDATES_ROOT.mkdir(parents=True, exist_ok=True)
        filename = name or f"test_candidate_{uuid.uuid4().hex}.py"
        path = CANDIDATES_ROOT / filename
        path.write_text(code, encoding="utf-8")
        created.append(path)
        return path

    yield _make

    for path in created:
        path.unlink(missing_ok=True)


class TestCheckAstSafety:
    def test_clean_code_is_safe(self):
        result = StaticValidator().check_ast_safety(CLEAN_CODE)
        assert result.safe is True
        assert result.violations == []

    def test_time_sleep_is_flagged(self):
        result = StaticValidator().check_ast_safety(BLOCKING_CALL_CODE)
        assert result.safe is False
        assert any("time.sleep" in v for v in result.violations)

    def test_synchronous_http_import_is_flagged(self):
        result = StaticValidator().check_ast_safety(SYNC_HTTP_CODE)
        assert result.safe is False
        assert any("requests" in v for v in result.violations)

    def test_negative_shift_lookahead_is_flagged(self):
        result = StaticValidator().check_ast_safety(LOOKAHEAD_SHIFT_CODE)
        assert result.safe is False
        assert any("shift" in v.lower() for v in result.violations)

    def test_positive_index_offset_lookahead_is_flagged(self):
        result = StaticValidator().check_ast_safety(LOOKAHEAD_INDEX_CODE)
        assert result.safe is False
        assert any("lookahead" in v.lower() for v in result.violations)

    def test_state_bleed_style_own_indicator_state_is_not_flagged(self):
        """README.md §9: no engine reset at IS/OOS boundary is an accepted, documented
        characteristic — a strategy simply reading its own running state must not false-positive."""
        result = StaticValidator().check_ast_safety(STATE_BLEED_STYLE_CODE)
        assert result.safe is True
        assert result.violations == []

    def test_syntax_error_is_unsafe(self):
        result = StaticValidator().check_ast_safety("def broken(:\n    pass")
        assert result.safe is False
        assert any("SyntaxError" in v for v in result.violations)


class TestRunLinters:
    def test_clean_candidate_passes_both_linters(self, candidate_file):
        path = candidate_file(CLEAN_CODE)
        result = StaticValidator().run_linters(path)
        assert result.ok is True

    def test_lint_error_fails_ruff(self, candidate_file):
        path = candidate_file(LINT_ERROR_CODE)
        result = StaticValidator().run_linters(path)
        assert result.ok is False
        assert "os" in result.ruff_output

    def test_type_error_fails_mypy(self, candidate_file):
        path = candidate_file(TYPE_ERROR_CODE)
        result = StaticValidator().run_linters(path)
        assert result.ok is False
        assert "error" in result.mypy_output.lower()

    def test_rejects_path_outside_candidates_root(self, tmp_path):
        outside = tmp_path / "outside.py"
        outside.write_text(CLEAN_CODE, encoding="utf-8")
        with pytest.raises(UnsafeWorkspacePathError):
            StaticValidator().run_linters(outside)

    def test_rejects_automation_strategies_path(self):
        strategies_dir = Path("automation/strategies").resolve()
        fake_path = strategies_dir / "rsi2_reversion.py"
        with pytest.raises(UnsafeWorkspacePathError):
            StaticValidator().run_linters(fake_path)


class _FakeChatClient:
    """Minimal stand-in for DeepSeekClient — only implements call_chat (async), used to drive
    StaticValidator's self-healing loop deterministically without any network access."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[str] = []

    async def call_chat(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._responses:
            raise AssertionError("_FakeChatClient exhausted its scripted responses")
        return self._responses.pop(0)


class TestValidateCodeSelfHealing:
    @pytest.mark.asyncio
    async def test_clean_candidate_passes_on_first_attempt_without_client(self, candidate_file):
        path = candidate_file(CLEAN_CODE)
        result = await StaticValidator().validate_code(path)
        assert result.ok is True
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_self_heals_after_ast_violation_then_passes(self, candidate_file):
        path = candidate_file(BLOCKING_CALL_CODE)
        fake_client = _FakeChatClient([CLEAN_CODE])
        validator = StaticValidator(client=fake_client, max_retries=3)

        result = await validator.validate_code(path)

        assert result.ok is True
        assert result.attempts == 2
        assert len(fake_client.calls) == 1
        assert "time.sleep" in fake_client.calls[0] or "AST" in fake_client.calls[0]
        # The file on disk was rewritten with the corrected code.
        assert path.read_text(encoding="utf-8").strip() == CLEAN_CODE.strip()

    @pytest.mark.asyncio
    async def test_self_heals_after_lint_failure_then_passes(self, candidate_file):
        path = candidate_file(LINT_ERROR_CODE)
        fake_client = _FakeChatClient([CLEAN_CODE])
        validator = StaticValidator(client=fake_client, max_retries=3)

        result = await validator.validate_code(path)

        assert result.ok is True
        assert result.attempts == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries_and_reports_failure(self, candidate_file):
        path = candidate_file(BLOCKING_CALL_CODE)
        # Every self-heal attempt returns the SAME broken code — never converges.
        fake_client = _FakeChatClient([BLOCKING_CALL_CODE, BLOCKING_CALL_CODE])
        validator = StaticValidator(client=fake_client, max_retries=3)

        result = await validator.validate_code(path)

        assert result.ok is False
        assert result.attempts == 3
        assert len(fake_client.calls) == 2  # retries - 1 self-heal calls (last attempt just fails)

    @pytest.mark.asyncio
    async def test_no_client_skips_self_healing_and_fails_fast(self, candidate_file):
        path = candidate_file(BLOCKING_CALL_CODE)
        validator = StaticValidator(client=None, max_retries=3)

        result = await validator.validate_code(path)

        assert result.ok is False
        # Code is unchanged (no client to correct it).
        assert path.read_text(encoding="utf-8") == BLOCKING_CALL_CODE

    @pytest.mark.asyncio
    async def test_validate_code_rejects_path_outside_candidates_root(self, tmp_path):
        outside = tmp_path / "outside.py"
        outside.write_text(CLEAN_CODE, encoding="utf-8")
        with pytest.raises(UnsafeWorkspacePathError):
            await StaticValidator().validate_code(outside)


class TestDynamicCandidateImport:
    """Issue #1105 acceptance: candidates are loaded dynamically via their AI-Loop workspace
    path (importlib), NEVER via the normal `automation.strategies.` import path — they never
    live there."""

    def test_candidate_is_not_importable_via_automation_strategies(self, candidate_file):
        path = candidate_file(CLEAN_CODE, name="dummy_candidate_not_a_real_strategy.py")
        module_name = path.stem
        # It genuinely does not exist under automation/strategies/.
        assert not (Path("automation/strategies") / path.name).exists()
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"automation.strategies.{module_name}")

    def test_candidate_loads_via_importlib_from_its_workspace_path(self, candidate_file):
        path = candidate_file(CLEAN_CODE, name="dummy_candidate_dynamic_load.py")
        module_name = f"automation.ai_loop.workspace.candidates.{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            instance = module.DummyCandidate()
            assert instance.add(2, 3) == 5
        finally:
            sys.modules.pop(module_name, None)

    def test_validator_passes_a_dynamically_loadable_candidate(self, candidate_file):
        """End-to-end: a candidate that both passes StaticValidator AND is loadable via
        importlib from its real workspace path — proving the two concerns (validation scope,
        dynamic loading) operate on the exact same file/path convention."""
        path = candidate_file(CLEAN_CODE, name="dummy_candidate_e2e.py")

        ast_result = StaticValidator().check_ast_safety(path.read_text(encoding="utf-8"))
        assert ast_result.safe is True
        lint_result = StaticValidator().run_linters(path)
        assert lint_result.ok is True

        module_name = f"automation.ai_loop.workspace.candidates.{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            assert hasattr(module, "DummyCandidate")
        finally:
            sys.modules.pop(module_name, None)
