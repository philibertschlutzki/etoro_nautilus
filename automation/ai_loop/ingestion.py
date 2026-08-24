"""
automation/ai_loop/ingestion.py
=================================
Issue #1104 — PerformanceParser: turns the EXISTING, real ``logs/run_<id>.json`` /
``logs/zusammenfassung_<id>.md`` artifacts into a single structured context dict for the R1
reasoner (``reasoning.StrategyReasoner``).

Both files are READ-ONLY inputs — this module never writes to ``logs/run_*.json`` or
``logs/zusammenfassung_*.md`` (those are produced by
``automation/optimizer/report.py::generate_sweep_report`` and
``automation/optimizer/summary_de.py``'s markdown writer respectively; see those modules'
docstrings for the write side).

Root-cause note (Owner-Klarstellung, superseding ``manuals/closedloop_issues.md``'s original
sketch): there is NO ``logs/gate_eval_<id>.json`` file in this repo — do not invent reads
against it. Gate/rejection detail already lives INSIDE each study record of
``logs/run_<id>.json``: ``promotion_outcome``, ``binding_gate``, ``blocking_stage``,
``rejection_chain``, ``decision_chain``, ``gate_inventory``, ``deployment_decision``.
``report.py``'s ``promotion_outcome`` field IS ``proposal.get("status")`` verbatim (see
``report.py``'s ``_study_record``, Issue #783) — nothing extra to read for that mapping.

Verified against the 11 real archived ``logs/run_*.json`` files in this repo: every one of them
has ``deployment_decision: null`` on every study (none of the archived sweeps promoted a
candidate past holdout) — this module surfaces that field as-is (usually ``None`` in practice)
and additionally exposes ``automation.optimizer.deployment_gate.DEPLOYMENT_CLAUSES`` (read-only
reference data — the eleven necessary deployment clauses) as static context, so the reasoner
knows the full clause vocabulary even when a given run's ``deployment_decision`` is null.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    # Read-only import (reference data only — DEPLOYMENT_CLAUSES is a static tuple of clause
    # names). See automation/tests/test_automation_isolation.py::test_no_archive_imports for the
    # "only automation.*" import-boundary convention this module also follows.
    from automation.optimizer.deployment_gate import DEPLOYMENT_CLAUSES
except ImportError:  # pragma: no cover - defensive; deployment_gate has no heavy deps normally
    DEPLOYMENT_CLAUSES: tuple[str, ...] = ()

# Metric fields pulled verbatim from a study record (automation/optimizer/report.py schema) —
# Sharpe-family/Sortino/PBO/DSR-adjacent evidence for the reasoner. There is no top-level
# "sharpe"/"dsr" field in this schema (verified against the real archives): the closest
# equivalents are holdout_sortino_* (risk-adjusted return) and inference_method.promotion.pbo /
# holdout_gate_deltas.oos_min_psr (deflation-adjacent evidence), pulled out separately below.
_STUDY_METRIC_FIELDS: tuple[str, ...] = (
    "holdout_sortino_annualized",
    "holdout_sortino_period",
    "holdout_total_return",
    "holdout_profit_factor",
    "holdout_profit_factor_raw",
    "holdout_win_rate",
    "holdout_excess_return",
    "holdout_excess_per_unit_exposure",
    "holdout_alpha",
    "holdout_alpha_tstat",
    "holdout_beta",
    "holdout_no_alpha_detected",
    "holdout_expectancy_notional_weighted",
    "holdout_expectancy_winsorized",
    "holdout_total_trades",
    "n_trials",
    "n_eligible",
    "n_evaluable",
)

# Parameter-history signals: logs/run_*.json carries no raw sampled-parameter dict per study
# (those live in data/optimizer/proposal_*.json / the Optuna store, both out of scope for this
# read-only, logs/-only ingestion), but it DOES carry boundary/override signal fields — exactly
# the evidence Path A (search-space override) hypotheses need.
_PARAMETER_SIGNAL_FIELDS: tuple[str, ...] = (
    "boundary_parameter",
    "boundary_side",
    "boundary_directions",
    "boundary_hit_fraction",
    "boundary_resolution_exhausted",
    "winner_outside_default_bounds_after_override",
)

_SECTION1_RE = re.compile(r"##\s*1\.\s*Ergebnis in einem Satz\s*\n+(.*?)(?=\n##|\Z)", re.DOTALL)


class PerformanceParser:
    """Parses ``{log_dir}/run_*.json`` (report.py schema) and
    ``{log_dir}/zusammenfassung_*.md`` (summary_de.py schema) into a structured context dict for
    ONE ``(symbol, strategy)`` pair, across up to ``history_depth`` most-recent matching runs."""

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)

    def extract_run_context(self, symbol: str, strategy: str, history_depth: int = 3) -> dict[str, Any]:
        """Returns a JSON-serializable dict:

            {
              "symbol": ..., "strategy": ..., "history_depth_requested": ...,
              "n_runs_scanned": <int, all run_*.json files in log_dir>,
              "n_runs_with_pair": <int, how many of those had a matching study>,
              "history": [ {...most-recent-first, at most history_depth entries...} ],
              "deployment_clause_reference": [...DEPLOYMENT_CLAUSES...],
            }

        Missing/unreadable/corrupt files are skipped with a warning (fail-open — ingestion must
        never crash the AI-Loop cycle over one bad archive file)."""
        history: list[dict[str, Any]] = []
        n_runs_scanned = 0

        for run_path in self._iter_run_files_newest_first():
            n_runs_scanned += 1
            data = self._load_json(run_path)
            if data is None:
                continue
            for study in data.get("studies", []) or []:
                if study.get("symbol") != symbol or study.get("strategy") != strategy:
                    continue
                history.append(self._slim_study(data, study))
                break  # at most one matching study per run (symbol+strategy is unique per study)
            if len(history) >= history_depth:
                break

        return {
            "symbol": symbol,
            "strategy": strategy,
            "history_depth_requested": history_depth,
            "n_runs_scanned": n_runs_scanned,
            "n_runs_with_pair": len(history),
            "history": history,
            "deployment_clause_reference": list(DEPLOYMENT_CLAUSES),
        }

    # ---- internals --------------------------------------------------------------------------

    def _iter_run_files_newest_first(self) -> list[Path]:
        if not self.log_dir.is_dir():
            return []
        files = sorted(self.log_dir.glob("run_*.json"))
        dated: list[tuple[str, Path]] = []
        for f in files:
            data = self._load_json(f)
            started = (data or {}).get("started_at_utc") or ""
            # Fallback sort key: the run_id's embedded timestamp suffix is lexicographically
            # sortable (YYYYMMDDTHHMMSSFFFFFF) — used only if started_at_utc is absent/unreadable.
            dated.append((started or f.stem, f))
        dated.sort(key=lambda pair: pair[0], reverse=True)
        return [f for _, f in dated]

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("PerformanceParser: %s konnte nicht gelesen/geparst werden: %s", path, exc)
            return None

    def _slim_study(self, run_data: dict[str, Any], study: dict[str, Any]) -> dict[str, Any]:
        run_id = run_data.get("run_id")
        metrics = {k: study.get(k) for k in _STUDY_METRIC_FIELDS if k in study}
        parameter_signals = {k: study.get(k) for k in _PARAMETER_SIGNAL_FIELDS if k in study}
        entry: dict[str, Any] = {
            "run_id": run_id,
            "started_at_utc": run_data.get("started_at_utc"),
            "promotion_outcome": study.get("promotion_outcome"),
            "promotion_route": study.get("promotion_route"),
            "binding_gate": study.get("binding_gate"),
            "blocking_stage": study.get("blocking_stage"),
            "all_failed_stages": study.get("all_failed_stages"),
            "rejection_chain": study.get("rejection_chain"),
            "decision_chain": study.get("decision_chain"),
            "gate_inventory": study.get("gate_inventory"),
            # DeploymentDecision.to_dict()-shaped ({admitted, blocking_clause, clause_results,
            # promotion_run_id, data_snapshot_sha256}) WHEN report.py embedded one for this
            # study; None on every archived run today (see module docstring).
            "deployment_decision": study.get("deployment_decision"),
            "pbo_summary": (study.get("inference_method") or {}).get("promotion"),
            "psr_gate_delta": (study.get("holdout_gate_deltas") or {}).get("oos_min_psr"),
            "sortino_gate_delta": (study.get("holdout_gate_deltas") or {}).get("oos_min_sortino"),
            "metrics": metrics,
            "parameter_signals": parameter_signals,
        }
        md_excerpt = self._markdown_excerpt(run_id, strategy=study.get("strategy"), symbol=study.get("symbol"))
        if md_excerpt:
            entry["summary_md_excerpt"] = md_excerpt
        return entry

    def _markdown_excerpt(
        self, run_id: str | None, *, strategy: str | None, symbol: str | None
    ) -> dict[str, Any] | None:
        if not run_id:
            return None
        md_path = self.log_dir / f"zusammenfassung_{run_id}.md"
        if not md_path.exists():
            return None
        try:
            text = md_path.read_text("utf-8")
        except OSError as exc:
            logger.warning("PerformanceParser: %s konnte nicht gelesen werden: %s", md_path, exc)
            return None

        excerpt: dict[str, Any] = {}
        headline_match = _SECTION1_RE.search(text)
        if headline_match:
            excerpt["headline"] = headline_match.group(1).strip()
        if strategy and symbol:
            rows = _extract_table_rows(text, strategy, symbol)
            if rows:
                excerpt["table_rows"] = rows
        return excerpt or None


def _extract_table_rows(md_text: str, strategy: str, symbol: str) -> list[str]:
    """Returns every markdown-table row (e.g. section 2.2 'Bester abgelehnter Kandidat je
    Strategie') whose first two pipe-delimited cells are exactly ``strategy``/``symbol``."""
    pattern = re.compile(
        r"^\|\s*" + re.escape(strategy) + r"\s*\|\s*" + re.escape(symbol) + r"\s*\|.*\|\s*$",
        re.MULTILINE,
    )
    return [m.group(0).strip() for m in pattern.finditer(md_text)]
