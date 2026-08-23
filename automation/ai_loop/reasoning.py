"""
automation/ai_loop/reasoning.py
================================
Issue #1106 — R1 hypothesis formulation ("Denken").

``StrategyReasoner`` takes the context dict produced by ``ingestion.PerformanceParser`` and asks
``deepseek-reasoner`` (R1, via the Issue #1104 ``DeepSeekClient``) to choose between:

  Path A ("search_space_override") — the search space for one or more EXISTING parameters is
    too narrow/mis-centered (e.g. ``boundary_hit_fraction > 0.3`` in the ingestion context).
  Path B ("signal_logic_mutation") — the signal LOGIC itself needs to change.

This module NEVER writes a file or generates code itself — that separation ("Denken" vs.
"Schreiben") is deliberate (see ``manuals/closedloop_issues.md`` Issue 3's root-cause note: an
LLM degrades when asked to both diagnose AND write code in one prompt). Materialising the
hypothesis is ``synthesizer.CodeSynthesizer``'s job.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from automation.ai_loop.client import DeepSeekClient

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Du bist ein quantitativer Research-Assistent fuer ein NautilusTrader-Backtest-System. Du "
    "bekommst historischen Lauf-Kontext (Kennzahlen, Ablehnungsgruende, Parameter-Grenzwert-"
    "Signale) fuer EIN (Strategie, Symbol)-Paar aus logs/run_*.json/logs/zusammenfassung_*.md "
    "und entscheidest zwischen genau zwei Optimierungspfaden:\n"
    "  Pfad A ('A'): der Suchraum fuer einen oder mehrere EXISTIERENDE Parameter ist zu eng "
    "oder falsch zentriert (typisches Signal: boundary_hit_fraction > 0.3 oder "
    "winner_outside_default_bounds_after_override ist gesetzt).\n"
    "  Pfad B ('B'): die Signal-LOGIK selbst muss sich aendern (z. B. ein zusaetzlicher "
    "Filter, ein anderer Entry-/Exit-Trigger) — keine reine Grenzwert-Verschiebung erklaert "
    "die Ablehnung.\n"
    "Antworte AUSSCHLIESSLICH mit einem einzigen JSON-Objekt (kein Markdown-Fence, kein Text "
    "davor/danach) mit EXAKT diesen Feldern:\n"
    '{"path": "A" oder "B", "strategy": <string>, "symbol": <string>, '
    '"rationale": <string, Begruendung auf Deutsch>, '
    '"confidence": <float zwischen 0 und 1>, '
    '"search_space_overrides": {"<param>": [low, high], ...} (NUR bei Pfad A, sonst {}), '
    '"code_mutation_instructions": <string> (NUR bei Pfad B, sonst "")}'
)


class ReasoningError(RuntimeError):
    """Raised when the R1 response cannot be parsed into a structured action plan."""


class StrategyReasoner:
    """Issue #1106 — R1 ('Denken'): analysiert den Ingestion-Kontext und waehlt Pfad A/B.
    Erzeugt NIEMALS Code oder JSON-Override-Dateien selbst."""

    def __init__(self, client: DeepSeekClient):
        self.client = client

    async def formulate_hypothesis(self, context: dict[str, Any]) -> dict[str, Any]:
        """Sends ``context`` (the dict from ``ingestion.PerformanceParser.extract_run_context``)
        to R1 and returns a structured action plan (see ``_SYSTEM_PROMPT`` for the exact
        schema). Raises ``ReasoningError`` if the response cannot be parsed."""
        payload = {
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": self._build_prompt(context)},
            ],
        }
        raw = await self.client.call_reasoner(payload)
        return self._parse_response(raw, context)

    @staticmethod
    def _build_prompt(context: dict[str, Any]) -> str:
        return (
            "Ingestion-Kontext (automation/ai_loop/ingestion.py, read-only aus logs/):\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2, default=str)}\n\n"
            "Formuliere die Hypothese fuer den naechsten Optimierungsversuch."
        )

    @staticmethod
    def _parse_response(raw: str, context: dict[str, Any]) -> dict[str, Any]:
        candidate = _extract_json_object(raw)
        if candidate is None:
            raise ReasoningError(f"R1-Antwort enthaelt kein parsbares JSON-Objekt: {raw!r}")
        try:
            plan = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ReasoningError(f"R1-Antwort ist kein valides JSON: {exc}: {candidate!r}") from exc

        if not isinstance(plan, dict):
            raise ReasoningError(f"R1-Antwort ist kein JSON-Objekt: {plan!r}")

        path = plan.get("path")
        if path not in ("A", "B"):
            raise ReasoningError(f"R1-Antwort traegt weder Pfad 'A' noch 'B': {plan!r}")

        plan.setdefault("strategy", context.get("strategy"))
        plan.setdefault("symbol", context.get("symbol"))
        plan.setdefault("search_space_overrides", {})
        plan.setdefault("code_mutation_instructions", "")
        plan.setdefault("rationale", "")
        plan.setdefault("confidence", None)
        plan["raw_response"] = raw
        return plan


def _extract_json_object(text: str) -> str | None:
    """Extracts the first top-level ``{...}`` JSON object from ``text`` — tolerant of a
    ```json fenced block or leading/trailing prose around the object (both common LLM output
    shapes), without requiring the WHOLE response to be pure JSON."""
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1]
