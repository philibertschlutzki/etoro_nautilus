"""
automation/ai_loop/synthesizer.py
==================================
Issue #1106 — code/JSON synthesis cascade ("Schreiben").

``CodeSynthesizer`` materialises a ``StrategyReasoner`` hypothesis (see ``reasoning.py``) onto
disk — and ONLY onto ``automation/ai_loop/workspace/``:

  Path A ("search_space_override") — writes ONLY
    ``automation/ai_loop/workspace/search_space_overrides.candidate.json``, using the exact
    schema ``automation.optimizer.spaces._load_search_space_overrides`` expects
    (``{"overrides": {"<strategy>": {"<symbol>": {"<param>": [low, high], ...}}}}`` — see
    ``automation/config/search_space_overrides.json`` for a real, curated example of this same
    shape). NEVER touches ``automation/config/search_space_overrides.json`` — that file is a
    human PR decision (see ``spaces.py``'s own docstring: "menschliche PR-Entscheidung").

  Path B ("signal_logic_mutation") — writes ONLY
    ``automation/ai_loop/workspace/candidates/<strategy>_<symbol>.py``, generated via
    ``deepseek-chat`` (V3, Issue #1104's client). NEVER touches
    ``automation/strategies/<strategy>.py``. The generated code follows the SAME import-boundary
    convention as the rest of ``automation/`` (only ``automation.*``/``nautilus_trader.*``
    imports — see ``_CODE_STYLE_GUIDE``) so a human could later manually copy it into
    ``automation/strategies/`` without rework.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from automation.ai_loop.client import DeepSeekClient

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(__file__).resolve().parent / "workspace"
CANDIDATES_DIR = WORKSPACE_ROOT / "candidates"
SEARCH_SPACE_CANDIDATE_PATH = WORKSPACE_ROOT / "search_space_overrides.candidate.json"

# Mirrors the real strategy files under automation/strategies/*.py (see e.g. rsi2_reversion.py,
# adx_atr_momentum.py) so a human reviewer can drop the generated file in with minimal rework.
_CODE_STYLE_GUIDE = """\
Regeln fuer den generierten Strategie-Code (verbindlich):
  - NUR "automation.*"- und "nautilus_trader.*"-Imports (dieselbe Import-Grenze wie der Rest
    von automation/, siehe automation/tests/test_automation_isolation.py::test_no_archive_imports
    — kein bloßes "strategies"/"config" ohne "automation."-Praefix).
  - Basisklasse: "from automation.strategies.hourly_strategy_base import HourlyStrategyBase, "
    "HourlyStrategyConfig, ExitReason".
  - Indikatoren: "from nautilus_trader.indicators import <Name>" (z. B. RelativeStrengthIndex,
    ExponentialMovingAverage, AverageTrueRange, KeltnerChannel, BollingerBands) — reale
    Indikator-Namen aus diesem Namespace, keine erfundenen Importpfade.
  - Genau ZWEI Top-Level-Klassen:
      "class <Strategy>Config(HourlyStrategyConfig, kw_only=True, frozen=True): ..."
      "class <Strategy>(HourlyStrategyBase): ..."
    (Strategie-Klassenname endet auf "Strategy" ODER stimmt mit dem vorgegebenen
    Strategienamen ueberein; Config-Klasse = "<Strategie-Klassenname>Config".)
  - Strategie-Klasse implementiert mindestens "__init__", "on_start", "on_bar", "on_stop";
    Order-Logik via "self.order_factory"/"self._compute_quantity"/"self.cache" wie in
    bestehenden automation/strategies/*.py-Dateien.
  - Type-Hints ueberall ("str | None"-Stil, Python 3.10+).
  - Keine "time.sleep"/synchronen Netzwerk-Calls. Keine Lookahead-Indizierung
    (kein "bars[i+1]"/".shift(-N)").
  - Antworte NUR mit dem vollstaendigen Python-Quellcode der neuen Datei — kein Markdown-Fence,
    kein Kommentartext davor/danach.
"""


class SynthesisError(RuntimeError):
    """Raised when synthesis produces no usable output, or the hypothesis is malformed."""


def _strip_code_fence(text: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    body = match.group(1) if match else text
    return body.strip() + "\n"


def _sanitize_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", symbol)


def _candidate_filename(strategy: str, symbol: str) -> str:
    return f"{strategy}_{_sanitize_symbol(symbol)}.py"


def _validate_override_bounds(overrides: dict[str, Any]) -> None:
    if not isinstance(overrides, dict) or not overrides:
        raise SynthesisError("Pfad A benoetigt eine nicht-leere 'search_space_overrides'-Hypothese.")
    for param, bound in overrides.items():
        if not (isinstance(bound, (list, tuple)) and len(bound) == 2):
            raise SynthesisError(f"search_space_overrides['{param}'] muss [low, high] sein, erhalten: {bound!r}.")
        low, high = bound
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
            raise SynthesisError(f"search_space_overrides['{param}'] muss numerisch sein, erhalten: {bound!r}.")
        if low >= high:
            raise SynthesisError(f"search_space_overrides['{param}']: low ({low}) muss < high ({high}) sein.")


class CodeSynthesizer:
    """Issue #1106 — V3 ('Schreiben'): materialisiert die R1-Hypothese ALS DATEI, ausschliesslich
    unter ``automation/ai_loop/workspace/``."""

    def __init__(self, client: DeepSeekClient | None = None):
        self.client = client

    async def apply_mutation(self, hypothesis: dict[str, Any]) -> dict[str, Any]:
        path = hypothesis.get("path")
        if path == "A":
            return self._apply_path_a(hypothesis)
        if path == "B":
            return await self._apply_path_b(hypothesis)
        raise SynthesisError(f"Unbekannter Hypothesen-Pfad: {path!r} (erwartet 'A' oder 'B').")

    # ---- Path A: search-space override candidate JSON -------------------------------------

    def _apply_path_a(self, hypothesis: dict[str, Any]) -> dict[str, Any]:
        strategy = hypothesis.get("strategy")
        symbol = hypothesis.get("symbol")
        if not strategy or not symbol:
            raise SynthesisError(f"Pfad A benoetigt 'strategy' und 'symbol' in der Hypothese: {hypothesis!r}")
        overrides = hypothesis.get("search_space_overrides") or {}
        _validate_override_bounds(overrides)

        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        payload = {
            "_schema": {
                "description": (
                    f"AI-Loop (Issues #1104-#1107) Kandidat-Override fuer {strategy}/{symbol} — "
                    "NIEMALS automatisch nach automation/config/search_space_overrides.json "
                    "uebernommen; nur eine menschliche PR-Entscheidung darf das (siehe "
                    "automation/optimizer/spaces.py::_load_search_space_overrides)."
                ),
            },
            "overrides": {strategy: {symbol: dict(overrides)}},
            "generated_by": "automation.ai_loop.synthesizer.CodeSynthesizer",
            "hypothesis_rationale": hypothesis.get("rationale", ""),
        }
        SEARCH_SPACE_CANDIDATE_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return {
            "path": "A",
            "candidate_file": str(SEARCH_SPACE_CANDIDATE_PATH),
            "strategy": strategy,
            "symbol": symbol,
            "overrides": payload["overrides"],
        }

    # ---- Path B: candidate strategy .py file -----------------------------------------------

    async def _apply_path_b(self, hypothesis: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise SynthesisError("Pfad B benoetigt einen DeepSeekClient (call_chat/V3) — keiner injiziert.")
        strategy = hypothesis.get("strategy")
        symbol = hypothesis.get("symbol")
        if not strategy or not symbol:
            raise SynthesisError(f"Pfad B benoetigt 'strategy' und 'symbol' in der Hypothese: {hypothesis!r}")

        prompt = self._build_code_prompt(hypothesis)
        raw = await self.client.call_chat(prompt)
        code = _strip_code_fence(raw)
        if not code.strip():
            raise SynthesisError("V3-Antwort enthielt keinen Code.")

        CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
        candidate_path = CANDIDATES_DIR / _candidate_filename(strategy, symbol)
        candidate_path.write_text(code, encoding="utf-8")
        return {
            "path": "B",
            "candidate_file": str(candidate_path),
            "strategy": strategy,
            "symbol": symbol,
        }

    @staticmethod
    def _build_code_prompt(hypothesis: dict[str, Any]) -> str:
        return (
            f"{_CODE_STYLE_GUIDE}\n\n"
            f"Strategie: {hypothesis.get('strategy')}\n"
            f"Symbol: {hypothesis.get('symbol')}\n"
            f"Mutationsanweisung (aus der R1-Hypothese):\n{hypothesis.get('code_mutation_instructions', '')}\n\n"
            f"Begruendung: {hypothesis.get('rationale', '')}\n"
        )
