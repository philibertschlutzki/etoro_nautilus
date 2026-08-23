"""
automation/tests/test_ai_loop_ingestion.py
=============================================
Issue #1104 — tests for automation/ai_loop/ingestion.py (PerformanceParser) and
automation/ai_loop/memory.py (LedgerWriter), including a git-status proof that exercising both
modules touches nothing outside automation/ai_loop/ and logs/.
"""
import json
import subprocess
from pathlib import Path

import pytest

from automation.ai_loop.ingestion import PerformanceParser
from automation.ai_loop.memory import LedgerWriter, default_ledger_path, ensure_ledger_exists, last_n_entries, read_entries

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REAL_LOG_DIR = REPO_ROOT / "logs"


# ---- fixtures modeled exactly on the real logs/run_*.json / logs/zusammenfassung_*.md schema ---

def _make_study(symbol: str, strategy: str, *, promotion_outcome: str, binding_gate: str | None = None,
                 deployment_decision=None, extra: dict | None = None) -> dict:
    study = {
        "symbol": symbol,
        "strategy": strategy,
        "run_id": "unused-per-study-run-id",  # study-level run_id exists in the real schema too
        "promotion_outcome": promotion_outcome,
        "promotion_route": None,
        "binding_gate": binding_gate,
        "blocking_stage": "confirm_or_selection",
        "all_failed_stages": ["confirm_or_selection"],
        "rejection_chain": [{"stage": "is_gate", "detail": "REJECT_OOS_MIN_PSR"}],
        "decision_chain": [{"stage": "is_gate", "passed": False, "detail": "REJECT_OOS_MIN_PSR"}],
        "gate_inventory": [
            {"gate": "oos_min_psr", "n_rejections": 139, "n_solo_rejections": 139,
             "marginal_delta": 0.99, "n_evaluated": 140},
        ],
        "deployment_decision": deployment_decision,
        "inference_method": {
            "eligibility": {"method": "stationary_bootstrap", "applied": True, "skipped_reason": None},
            "promotion": {"method": None, "applied": False, "skipped_reason": "NO_ELIGIBLE_TRIALS",
                          "pbo": None, "pbo_n_configs_effective": None, "pbo_n_configs_raw": None,
                          "pbo_n_groups": None, "pbo_threshold": 0.5},
        },
        "holdout_gate_deltas": {"binding": {"oos_min_psr": -0.6}, "oos_min_psr": -0.6, "oos_min_sortino": None},
        "holdout_sortino_annualized": -11.6,
        "holdout_sortino_period": -0.12,
        "holdout_total_return": -0.046,
        "holdout_profit_factor": 0.8,
        "holdout_profit_factor_raw": 0.8,
        "holdout_win_rate": 0.4,
        "holdout_excess_return": -0.027,
        "holdout_excess_per_unit_exposure": -0.031,
        "holdout_alpha": -0.43,
        "holdout_alpha_tstat": -2.25,
        "holdout_beta": -0.011,
        "holdout_no_alpha_detected": False,
        "holdout_expectancy_notional_weighted": -1.2,
        "holdout_expectancy_winsorized": -1.44,
        "holdout_total_trades": 132,
        "n_trials": 140,
        "n_eligible": 1,
        "n_evaluable": 140,
        "boundary_parameter": "ema_period",
        "boundary_side": "lower",
        "boundary_directions": {"ema_period": "lower"},
        "boundary_hit_fraction": 0.42,
        "boundary_resolution_exhausted": False,
        "winner_outside_default_bounds_after_override": {"ema_period": True},
    }
    if extra:
        study.update(extra)
    return study


def _write_run_file(log_dir: Path, run_id: str, started_at_utc: str, studies: list[dict]) -> Path:
    payload = {
        "report_schema_version": 1,
        "run_id": run_id,
        "started_at_utc": started_at_utc,
        "studies": studies,
    }
    path = log_dir / f"run_{run_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_summary_md(log_dir: Path, run_id: str, strategy: str, symbol: str, holdout_return: str,
                       reason: str) -> Path:
    text = (
        f"# Sweep-Zusammenfassung {run_id}\n\n"
        "## 1. Ergebnis in einem Satz\n\n"
        "14 Studies, 0 Sweep-Promotion(en), **0 deploybar** — Test-Fixture.\n\n"
        "## 2.2 Bester abgelehnter Kandidat je Strategie\n\n"
        "| Strategie | Symbol | Holdout-Return (simuliert) | Ablehnungsgrund | Stufe |\n"
        "|---|---|---:|---|---|\n"
        f"| {strategy} | {symbol} | {holdout_return} | {reason} | confirm_or_selection |\n"
    )
    path = log_dir / f"zusammenfassung_{run_id}.md"
    path.write_text(text, encoding="utf-8")
    return path


class TestPerformanceParserSynthetic:
    def test_extracts_most_recent_history_depth_runs(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        for i, ts in enumerate(["20260101T000000000000", "20260102T000000000000",
                                 "20260103T000000000000", "20260104T000000000000"]):
            run_id = f"run{i}"
            _write_run_file(
                log_dir, run_id, f"2026-01-0{i + 1}T00:00:00+00:00",
                [_make_study("TSLA.ETORO", "AdxAtrMomentumStrategy", promotion_outcome="REJECTED_ON_HOLDOUT")],
            )

        parser = PerformanceParser(log_dir)
        ctx = parser.extract_run_context("TSLA.ETORO", "AdxAtrMomentumStrategy", history_depth=2)

        assert ctx["symbol"] == "TSLA.ETORO"
        assert ctx["strategy"] == "AdxAtrMomentumStrategy"
        # Scanning stops as soon as history_depth matches are found (newest-first) — with every
        # one of the 4 files containing a match, only the 2 needed are actually opened/parsed.
        assert ctx["n_runs_scanned"] == 2
        assert ctx["n_runs_with_pair"] == 2
        assert len(ctx["history"]) == 2
        # Newest-first ordering.
        assert ctx["history"][0]["started_at_utc"] == "2026-01-04T00:00:00+00:00"
        assert ctx["history"][1]["started_at_utc"] == "2026-01-03T00:00:00+00:00"

    def test_slim_study_carries_gate_and_metric_fields(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_run_file(
            log_dir, "abc123_20260101T000000000000", "2026-01-01T00:00:00+00:00",
            [_make_study("LULU.ETORO", "AdxAtrMomentumStrategy",
                          promotion_outcome="REJECTED_BEFORE_HOLDOUT", binding_gate="oos_min_psr")],
        )
        parser = PerformanceParser(log_dir)
        ctx = parser.extract_run_context("LULU.ETORO", "AdxAtrMomentumStrategy")
        entry = ctx["history"][0]

        assert entry["promotion_outcome"] == "REJECTED_BEFORE_HOLDOUT"
        assert entry["binding_gate"] == "oos_min_psr"
        assert entry["deployment_decision"] is None
        assert entry["metrics"]["holdout_sortino_annualized"] == -11.6
        assert entry["parameter_signals"]["boundary_parameter"] == "ema_period"
        assert entry["pbo_summary"]["skipped_reason"] == "NO_ELIGIBLE_TRIALS"
        assert entry["psr_gate_delta"] == -0.6
        assert "deployment_clause_reference" in ctx
        assert isinstance(ctx["deployment_clause_reference"], list)
        assert "dsr" in ctx["deployment_clause_reference"]
        assert "pbo" in ctx["deployment_clause_reference"]

    def test_markdown_excerpt_matches_symbol_and_strategy_row(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_run_file(
            log_dir, "abc123_20260101T000000000000", "2026-01-01T00:00:00+00:00",
            [_make_study("LULU.ETORO", "AdxAtrMomentumStrategy", promotion_outcome="REJECTED_ON_HOLDOUT")],
        )
        _write_summary_md(log_dir, "abc123_20260101T000000000000", "AdxAtrMomentumStrategy",
                           "LULU.ETORO", "-4.6 %", "REJECTED_ON_HOLDOUT")

        parser = PerformanceParser(log_dir)
        ctx = parser.extract_run_context("LULU.ETORO", "AdxAtrMomentumStrategy")
        excerpt = ctx["history"][0]["summary_md_excerpt"]

        assert "headline" in excerpt
        assert "Test-Fixture" in excerpt["headline"]
        assert len(excerpt["table_rows"]) == 1
        assert "AdxAtrMomentumStrategy" in excerpt["table_rows"][0]
        assert "-4.6 %" in excerpt["table_rows"][0]

    def test_no_matching_pair_returns_empty_history(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_run_file(log_dir, "abc", "2026-01-01T00:00:00+00:00",
                         [_make_study("AAPL.ETORO", "SmaCrossoverStrategy", promotion_outcome="REJECTED_ON_HOLDOUT")])

        parser = PerformanceParser(log_dir)
        ctx = parser.extract_run_context("MSFT.ETORO", "SmaCrossoverStrategy")

        assert ctx["history"] == []
        assert ctx["n_runs_with_pair"] == 0
        assert ctx["n_runs_scanned"] == 1

    def test_missing_log_dir_does_not_crash(self, tmp_path):
        parser = PerformanceParser(tmp_path / "does_not_exist")
        ctx = parser.extract_run_context("TSLA.ETORO", "AdxAtrMomentumStrategy")
        assert ctx["history"] == []
        assert ctx["n_runs_scanned"] == 0

    def test_corrupt_run_file_is_skipped_not_fatal(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "run_broken_20260101T000000000000.json").write_text("{not json", encoding="utf-8")
        _write_run_file(log_dir, "ok_20260102T000000000000", "2026-01-02T00:00:00+00:00",
                         [_make_study("TSLA.ETORO", "AdxAtrMomentumStrategy", promotion_outcome="REJECTED_ON_HOLDOUT")])

        parser = PerformanceParser(log_dir)
        ctx = parser.extract_run_context("TSLA.ETORO", "AdxAtrMomentumStrategy")
        assert ctx["n_runs_with_pair"] == 1

    def test_deployment_decision_passthrough_when_present(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        decision = {
            "admitted": False, "blocking_clause": "dsr",
            "clause_results": {"promotion_record_exists": True, "dsr": False},
            "promotion_run_id": "xyz", "data_snapshot_sha256": "deadbeef",
        }
        _write_run_file(log_dir, "abc", "2026-01-01T00:00:00+00:00",
                         [_make_study("TSLA.ETORO", "AdxAtrMomentumStrategy",
                                      promotion_outcome="REJECTED_ON_DEFLATION",
                                      deployment_decision=decision)])

        parser = PerformanceParser(log_dir)
        ctx = parser.extract_run_context("TSLA.ETORO", "AdxAtrMomentumStrategy")
        assert ctx["history"][0]["deployment_decision"] == decision


class TestPerformanceParserRealArchives:
    """Uses the REAL, committed logs/run_*.json / logs/zusammenfassung_*.md archives."""

    @pytest.mark.skipif(not REAL_LOG_DIR.is_dir(), reason="logs/ directory not present in this checkout")
    def test_real_archive_pair_parses_cleanly(self):
        parser = PerformanceParser(REAL_LOG_DIR)
        ctx = parser.extract_run_context("NVDA.ETORO", "AdxAtrMomentumStrategy", history_depth=3)

        assert ctx["n_runs_scanned"] >= 1
        if ctx["n_runs_with_pair"] == 0:
            pytest.skip("Real archives in this checkout no longer contain the NVDA.ETORO/AdxAtrMomentumStrategy pair")
        entry = ctx["history"][0]
        assert entry["promotion_outcome"] is not None
        assert isinstance(entry["metrics"], dict)
        assert isinstance(entry["parameter_signals"], dict)

    @pytest.mark.skipif(not REAL_LOG_DIR.is_dir(), reason="logs/ directory not present in this checkout")
    def test_real_archives_are_never_modified(self):
        run_files = sorted(REAL_LOG_DIR.glob("run_*.json"))
        if not run_files:
            pytest.skip("No real run_*.json archives in this checkout")
        before = {f: f.read_bytes() for f in run_files}

        parser = PerformanceParser(REAL_LOG_DIR)
        parser.extract_run_context("NVDA.ETORO", "AdxAtrMomentumStrategy", history_depth=5)
        parser.extract_run_context("PLTR.ETORO", "SmaCrossoverStrategy", history_depth=5)

        after = {f: f.read_bytes() for f in run_files}
        assert before == after


class TestLedgerWriter:
    def test_append_and_read_roundtrip(self, tmp_path):
        ledger_path = tmp_path / "logs" / "ai_optimization_ledger.jsonl"
        writer = LedgerWriter(ledger_path)

        written = writer.append({"symbol": "TSLA.ETORO", "strategy": "AdxAtrMomentumStrategy", "outcome": "rejected"})
        assert "ts_utc" in written
        assert "entry_id" in written

        entries = list(read_entries(ledger_path))
        assert len(entries) == 1
        assert entries[0]["symbol"] == "TSLA.ETORO"

    def test_append_is_truly_append_only(self, tmp_path):
        ledger_path = tmp_path / "ledger.jsonl"
        writer = LedgerWriter(ledger_path)
        writer.append({"i": 1})
        writer.append({"i": 2})
        writer.append({"i": 3})

        entries = list(read_entries(ledger_path))
        assert [e["i"] for e in entries] == [1, 2, 3]

    def test_last_n_entries(self, tmp_path):
        ledger_path = tmp_path / "ledger.jsonl"
        writer = LedgerWriter(ledger_path)
        for i in range(5):
            writer.append({"i": i})
        assert [e["i"] for e in last_n_entries(2, ledger_path=ledger_path)] == [3, 4]
        assert last_n_entries(0, ledger_path=ledger_path) == []

    def test_corrupt_last_line_does_not_break_reading(self, tmp_path):
        ledger_path = tmp_path / "ledger.jsonl"
        writer = LedgerWriter(ledger_path)
        writer.append({"i": 1})
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write("{not valid json\n")

        entries = list(read_entries(ledger_path))
        assert len(entries) == 1
        assert entries[0]["i"] == 1

    def test_ensure_ledger_exists_creates_empty_file(self, tmp_path):
        ledger_path = tmp_path / "logs" / "ai_optimization_ledger.jsonl"
        result = ensure_ledger_exists(ledger_path)
        assert result == ledger_path
        assert ledger_path.exists()
        assert ledger_path.read_text() == ""

    def test_default_ledger_path_points_at_repo_logs_dir(self):
        assert default_ledger_path() == REPO_ROOT / "logs" / "ai_optimization_ledger.jsonl"

    def test_read_entries_missing_file_returns_empty(self, tmp_path):
        assert list(read_entries(tmp_path / "nope.jsonl")) == []


class TestIsolationBoundary:
    """Issue #1104 acceptance: exercising ingestion.py/memory.py touches nothing outside
    automation/ai_loop/ and logs/."""

    def test_git_status_clean_outside_ai_loop_and_logs(self, tmp_path):
        before = _git_status_porcelain()

        # Exercise ingestion against the real, committed logs/ dir (read-only) ...
        parser = PerformanceParser(REAL_LOG_DIR)
        parser.extract_run_context("NVDA.ETORO", "AdxAtrMomentumStrategy")
        # ... and the ledger writer against an isolated tmp_path ledger (never the real logs/).
        LedgerWriter(tmp_path / "ledger.jsonl").append({"probe": True})

        after = _git_status_porcelain()
        new_lines = [line for line in after if line not in before]

        offending = [
            line for line in new_lines
            if not _touches_only_allowed_paths(line)
        ]
        assert offending == [], f"Unerwartete Aenderungen ausserhalb automation/ai_loop/ und logs/: {offending}"


def _git_status_porcelain() -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return proc.stdout.splitlines()


def _touches_only_allowed_paths(status_line: str) -> bool:
    path = status_line[3:].strip()
    return path.startswith("automation/ai_loop/") or path.startswith("logs/")
