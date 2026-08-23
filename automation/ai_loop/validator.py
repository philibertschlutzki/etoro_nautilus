"""
automation/ai_loop/validator.py
=================================
Issue #1105 — Static verification & self-healing pipeline.

Validates ONLY files under ``automation/ai_loop/workspace/candidates/`` (the synthesizer's,
Issue #1106, own scratch output) — it NEVER touches or references
``automation/strategies/*.py``. Every path this module is asked to inspect is checked against
that boundary (``_assert_candidate_path``) before any AST parse, subprocess, or self-healing
call happens.

Deliberate non-goal: the "state bleed" pattern documented in ``README.md`` §9 (no NautilusTrader
engine reset at the IS/OOS walk-forward boundary — open positions/account balance/warmed-up
indicators intentionally carry over) is a backtest-ENGINE characteristic, not a code-level
lookahead bias in strategy signal logic, and every strategy under ``automation/strategies/``
already relies on exactly that (an indicator's ``.value`` simply reflects everything fed to it
so far). ``check_ast_safety`` therefore does NOT flag a strategy consuming its own running
indicator/position state across bars — see that method's docstring for what it DOES flag
(blocking calls, and the concrete, detectable shape of a forward-looking index/shift).
"""
from __future__ import annotations

import ast
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(__file__).resolve().parent / "workspace"
CANDIDATES_ROOT = WORKSPACE_ROOT / "candidates"
_RUFF_CONFIG = Path(__file__).resolve().parent / "ruff.toml"
_MYPY_CONFIG = Path(__file__).resolve().parent / "mypy.ini"

# time.sleep / socket.socket(...) block the whole (async) event loop the strategy runs in —
# AGENTS.md §14/§15: "Async: asyncio.sleep, nie time.sleep". Modules whose mere presence signals
# synchronous networking (requests/urllib/http.client) are flagged at the import statement.
_BLOCKING_DOTTED_CALLS = {"time.sleep", "socket.socket"}
_SYNC_HTTP_MODULES = {"requests", "urllib", "urllib2", "http.client", "httplib"}


@dataclass
class ASTSafetyResult:
    safe: bool
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"safe": self.safe, "violations": list(self.violations)}


@dataclass
class LintResult:
    ok: bool
    ruff_output: str
    mypy_output: str

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "ruff_output": self.ruff_output, "mypy_output": self.mypy_output}


@dataclass
class ValidationResult:
    ok: bool
    attempts: int
    final_code: str
    ast_result: ASTSafetyResult | None = None
    lint_result: LintResult | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "attempts": self.attempts,
            "ast_result": self.ast_result.to_dict() if self.ast_result else None,
            "lint_result": self.lint_result.to_dict() if self.lint_result else None,
            "history": list(self.history),
        }


class UnsafeWorkspacePathError(ValueError):
    """Raised when a path handed to the validator is not inside
    ``automation/ai_loop/workspace/candidates/``."""


def _assert_candidate_path(path: Path) -> Path:
    resolved = Path(path).resolve()
    candidates_root = CANDIDATES_ROOT.resolve()
    try:
        resolved.relative_to(candidates_root)
    except ValueError:
        raise UnsafeWorkspacePathError(
            f"StaticValidator darf ausschliesslich Dateien unter {candidates_root} pruefen "
            f"(automation/strategies/ ist tabu) — erhalten: {resolved}"
        ) from None
    return resolved


class StaticValidator:
    """Issue #1105 — AST safety + ruff/mypy (scoped to ONE candidate file) + self-healing via
    ``deepseek-chat`` (V3, the Issue #1104 client), bounded by ``AI_LOOP_MAX_RETRIES``."""

    def __init__(self, *, client: Any = None, max_retries: int | None = None):
        self.client = client
        self.max_retries = (
            max_retries if max_retries is not None else int(os.getenv("AI_LOOP_MAX_RETRIES", 3))
        )

    # ---- 1. AST safety ------------------------------------------------------------------

    def check_ast_safety(self, code: str) -> ASTSafetyResult:
        """Parses ``code`` (source text, not a file) and flags:

          * blocking synchronous calls (``time.sleep``, raw ``socket.socket(...)``) and
            synchronous-HTTP imports (``requests``/``urllib``/``http.client``) — this is an
            async NautilusTrader strategy-callback context.
          * genuine lookahead bias, in its concrete AST-detectable shape: a subscript/slice
            index built from a POSITIVE forward offset (``bars[i + 1]``, ``data.iloc[idx + 2:]``)
            or a ``.shift(-N)`` call with a negative shift (pandas convention: negative shift
                pulls a FUTURE value into the current row).

        Does NOT flag the README.md §9 "state bleed" pattern — see module docstring."""
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return ASTSafetyResult(safe=False, violations=[f"SyntaxError: {exc}"])

        violations: list[str] = []
        imported_as: dict[str, str] = {}  # local alias -> fully-qualified name

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_as[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imported_as[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                root_module = node.module.split(".")[0]
                if node.module in _SYNC_HTTP_MODULES or root_module in _SYNC_HTTP_MODULES:
                    violations.append(
                        f"Zeile {node.lineno}: synchroner HTTP-Import '{node.module}' "
                        "(blockiert den Event-Loop; in Strategie-Callbacks verboten)."
                    )
            elif isinstance(node, ast.Call):
                violations.extend(self._check_call(node, imported_as))
            elif isinstance(node, ast.Subscript):
                v = self._check_lookahead_subscript(node)
                if v:
                    violations.append(v)

        return ASTSafetyResult(safe=not violations, violations=violations)

    def _check_call(self, node: ast.Call, imported_as: dict[str, str]) -> list[str]:
        violations: list[str] = []
        dotted = _dotted_name(node.func)
        if not dotted:
            return violations
        resolved = _resolve_dotted(dotted, imported_as)

        if resolved in _BLOCKING_DOTTED_CALLS:
            violations.append(
                f"Zeile {node.lineno}: blockierender Call '{resolved}(...)' — "
                "'time.sleep'/rohes Networking sind im async Strategie-Callback verboten "
                "(AGENTS.md §14/§15: asyncio.sleep statt time.sleep)."
            )
        elif resolved.split(".")[0] in _SYNC_HTTP_MODULES:
            violations.append(f"Zeile {node.lineno}: synchroner HTTP-Call '{resolved}(...)' verboten.")
        elif resolved.endswith(".shift"):
            for arg in node.args:
                if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
                    violations.append(
                        f"Zeile {node.lineno}: '.shift(-N)' liest eine ZUKUENFTIGE Zeile in die "
                        "aktuelle (Lookahead-Bias)."
                    )
        return violations

    @staticmethod
    def _check_lookahead_subscript(node: ast.Subscript) -> str | None:
        """Flags an index/slice built from a POSITIVE forward offset on a bar/tick-like buffer,
        e.g. ``bars[i + 1]`` / ``data.iloc[idx + 2 :]`` — the concrete, detectable shape of
        lookahead bias this checker targets. Does not (and cannot, at the AST level) reason
        about engine-level state continuity across the IS/OOS boundary — see module docstring."""
        sl = node.slice
        targets: list[ast.AST] = []
        if isinstance(sl, ast.Slice):
            if sl.lower is not None:
                targets.append(sl.lower)
        else:
            targets.append(sl)
        for t in targets:
            if isinstance(t, ast.BinOp) and isinstance(t.op, ast.Add):
                right = t.right
                if isinstance(right, ast.Constant) and isinstance(right.value, int) and right.value > 0:
                    return (
                        f"Zeile {node.lineno}: Index-Offset '+{right.value}' liest moeglicherweise "
                        "eine zukuenftige Bar (Lookahead-Bias)."
                    )
        return None

    # ---- 2. Linting (scoped to ONE candidate file) ---------------------------------------

    def run_linters(self, candidate_path: Path) -> LintResult:
        """Runs ``ruff check`` and ``mypy --strict``, EACH scoped to exactly
        ``candidate_path`` — never the whole ``automation/ai_loop`` package — using the config
        files shipped alongside this module (``ruff.toml``/``mypy.ini``, both explicitly scoped
        to ``automation/ai_loop/workspace/``, see their headers)."""
        path = _assert_candidate_path(Path(candidate_path))

        ruff_proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(_RUFF_CONFIG), str(path)],
            capture_output=True, text=True, timeout=60,
        )
        mypy_proc = subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", "--config-file", str(_MYPY_CONFIG), str(path)],
            capture_output=True, text=True, timeout=120,
        )
        ok = ruff_proc.returncode == 0 and mypy_proc.returncode == 0
        return LintResult(
            ok=ok,
            ruff_output=(ruff_proc.stdout + ruff_proc.stderr).strip(),
            mypy_output=(mypy_proc.stdout + mypy_proc.stderr).strip(),
        )

    # ---- 3. Orchestration + self-healing --------------------------------------------------

    async def validate_code(self, candidate_path: Path, *, max_retries: int | None = None) -> ValidationResult:
        """Orchestrates AST safety -> linting, bounded by ``max_retries`` (default
        ``AI_LOOP_MAX_RETRIES``). On any failed stage, sends a feedback prompt (with the
        violation/traceback detail) to ``deepseek-chat`` (V3) for self-correction, rewrites
        ``candidate_path`` with the corrected code, and retries from the top. Returns as soon as
        one attempt passes BOTH stages, or once retries are exhausted."""
        path = _assert_candidate_path(Path(candidate_path))
        retries = max_retries if max_retries is not None else self.max_retries
        history: list[dict[str, Any]] = []
        code = path.read_text(encoding="utf-8")

        ast_result: ASTSafetyResult | None = None
        lint_result: LintResult | None = None

        for attempt in range(1, max(retries, 1) + 1):
            ast_result = self.check_ast_safety(code)
            if not ast_result.safe:
                history.append({"attempt": attempt, "stage": "ast", "ok": False, "detail": ast_result.to_dict()})
                if attempt >= retries:
                    break
                code = await self._self_heal(code, _format_feedback("AST-Sicherheitspruefung", ast_result.violations))
                path.write_text(code, encoding="utf-8")
                continue

            lint_result = self.run_linters(path)
            if not lint_result.ok:
                history.append({"attempt": attempt, "stage": "lint", "ok": False, "detail": lint_result.to_dict()})
                if attempt >= retries:
                    break
                code = await self._self_heal(
                    code, _format_feedback("Linting (ruff/mypy --strict)", [lint_result.ruff_output, lint_result.mypy_output])
                )
                path.write_text(code, encoding="utf-8")
                continue

            history.append({"attempt": attempt, "stage": "ast+lint", "ok": True})
            return ValidationResult(ok=True, attempts=attempt, final_code=code,
                                     ast_result=ast_result, lint_result=lint_result, history=history)

        return ValidationResult(ok=False, attempts=len(history), final_code=code,
                                 ast_result=ast_result, lint_result=lint_result, history=history)

    async def _self_heal(self, code: str, feedback: str) -> str:
        if self.client is None:
            logger.warning("StaticValidator: kein DeepSeekClient injiziert — Self-Healing uebersprungen.")
            return code
        prompt = (
            "Der folgende NautilusTrader-Strategie-Kandidat hat die Validierung NICHT bestanden.\n\n"
            f"Feedback:\n{feedback}\n\nAktueller Code:\n```python\n{code}\n```\n\n"
            "Korrigiere AUSSCHLIESSLICH die gemeldeten Probleme, ohne die Signal-Logik unnoetig zu "
            "veraendern. Antworte NUR mit dem vollstaendigen, korrigierten Python-Quellcode der "
            "Datei (kein Markdown-Fence, kein Kommentartext davor/danach)."
        )
        corrected = await self.client.call_chat(prompt)
        return _strip_code_fence(corrected)


def _format_feedback(stage: str, details: list[str]) -> str:
    joined = "\n".join(d for d in details if d)
    return f"[{stage}] fehlgeschlagen:\n{joined}"


def _strip_code_fence(text: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    body = match.group(1) if match else text
    return body.strip() + "\n"


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _resolve_dotted(dotted: str, imported_as: dict[str, str]) -> str:
    head, _, rest = dotted.partition(".")
    real_head = imported_as.get(head, head)
    return f"{real_head}.{rest}" if rest else real_head
