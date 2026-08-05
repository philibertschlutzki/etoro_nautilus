"""Issue #872 (P2, Katalog #870-#875, GitHub-Issue #761) — kein je-Study-Zeitstempel; jede
Durchsatzaussage musste aus Log-Zeitstempeln rekonstruiert werden.

Scope-Stand dieser Sitzung:
1. ``started_at_utc``/``ended_at_utc``/``wallclock_s``/``worker_id`` je Study im #742-Report —
   BEREITS vorhanden (Issue #851, siehe ``report._study_record``); dieser Testfall ist ein reiner
   Regressionswächter.
2. ``exit_reason`` je Trade aus dem #838-``ExitReason``-Enum aggregieren (``exit_reason_counts``) —
   erfordert, den Exit-Grund durch die FIFO-Round-Trip-Matching-Pipeline in ``backtest_runner.py``
   zu fädeln (5+ Aufrufstellen einer bereits etablierten ``(pnl, exit_ts_ns, holding_ns, qty)``-
   Tupel-Form) — bewusst ZURÜCKGESTELLT: eine Restrukturierung dieser Kernstruktur ohne einen
   echten Referenzlauf zur empirischen Verifikation ist in dieser Sitzung nicht verantwortbar
   umsetzbar (dieselbe Scope-Disziplin wie #862s ``family_median``-Deferral).
3. ``longest_trades`` Top-k nur für den Confirm-Kandidaten (Speicherdisziplin #735/#798) — bereits
   erfüllt: ``report._longest_holding_studies`` exportiert ausschließlich Study-AGGREGATE
   (``max_holding_time_s``/``p95_holding_time_s``, zwei Floats je Study), niemals eine
   ungebundene Liste einzelner Trades.
4. ``data/optimizer/pair_runtime_stats.json`` als abgeleitetes Artefakt am Laufende — NEU
   implementiert (``report._write_pair_runtime_stats``, aufgerufen aus
   ``report.generate_sweep_report``), Eingabe für die (zurückgestellte) #871-LPT-Terminplanung.
"""
import json

from automation.optimizer import report as rpt


# ── AK-1 Regressionswächter: per-Study-Zeitstempel bereits im Report (#851) ─────────────────────
def test_study_record_still_carries_per_study_timing_fields():
    class _T:
        def __init__(self, user_attrs):
            self.user_attrs = user_attrs
            self.value = 1.0

    class _S:
        def __init__(self, trials):
            self.trials = trials
            self.user_attrs = {}

        @property
        def best_value(self):
            return 1.0

    trials = [_T({"oos_evaluated": True, "oos_eligible": True})]
    study = _S(trials)
    study.user_attrs = {
        "study_started_at_utc": "2026-08-05T10:00:00.000Z",
        "study_ended_at_utc": "2026-08-05T10:20:00.000Z",
        "study_wallclock_s": 1200.0,
        "worker_id": "worker-3",
    }
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TrendPullbackStrategy", "status": "READY_FOR_PR"}
    record, _checks = rpt._study_record(proposal, study)
    assert record["study_started_at_utc"] == "2026-08-05T10:00:00.000Z"
    assert record["study_ended_at_utc"] == "2026-08-05T10:20:00.000Z"
    assert record["study_wallclock_s"] == 1200.0
    assert record["worker_id"] == "worker-3"


# ── AK-3: longest_holding_studies exportiert nur Aggregate, keine Trade-Listen ───────────────────
def test_longest_holding_studies_exports_only_scalar_aggregates_not_trade_lists():
    studies_out = [
        {"strategy": "Rsi2ReversionStrategy", "symbol": "AAPL.ETORO",
         "max_holding_time_s": 90000.0, "p95_holding_time_s": 80000.0},
        {"strategy": "VwapExhaustionStrategy", "symbol": "MSFT.ETORO",
         "max_holding_time_s": 50000.0, "p95_holding_time_s": 40000.0},
    ]
    ranked = rpt._longest_holding_studies(studies_out, top_k=10)
    assert ranked[0]["symbol"] == "AAPL.ETORO"
    for row in ranked:
        assert set(row.keys()) == {"strategy", "symbol", "max_holding_time_s", "p95_holding_time_s"}
        assert all(isinstance(v, (float, int, str)) or v is None for v in row.values())


# ── AK-4: pair_runtime_stats.json ─────────────────────────────────────────────────────────────────
def test_write_pair_runtime_stats_writes_expected_payload(tmp_path):
    report = {
        "run_id": "run_test_001",
        "cross_study": {
            "wallclock_by_strategy": {
                "TrendPullbackStrategy": {"median": 1200.0, "p90": 1500.0, "n": 34},
                "AdxAtrMomentumStrategy": {"median": 900.0, "p90": 1100.0, "n": 34},
            },
        },
    }
    path = rpt._write_pair_runtime_stats(report, work_dir=tmp_path)
    assert path == tmp_path / "pair_runtime_stats.json"
    assert path.exists()
    payload = json.loads(path.read_text("utf-8"))
    assert payload["run_id"] == "run_test_001"
    assert payload["by_strategy"]["TrendPullbackStrategy"]["median"] == 1200.0
    assert payload["by_strategy"]["AdxAtrMomentumStrategy"]["n"] == 34
    assert "generated_at_utc" in payload


def test_write_pair_runtime_stats_fail_open_on_malformed_report(tmp_path):
    path = rpt._write_pair_runtime_stats({"cross_study": {}}, work_dir=tmp_path)
    assert not path.exists()


def test_generate_sweep_report_also_writes_pair_runtime_stats(tmp_path, monkeypatch):
    """End-to-End — generate_sweep_report ruft die neue Schreibfunktion tatsaechlich auf (kein
    toter Code, Pitfall #237-Waechter analog zu den anderen #742-Artefakten dieser Sitzung)."""
    calls = []
    monkeypatch.setattr(rpt, "_write_pair_runtime_stats", lambda report, **k: calls.append(report))
    monkeypatch.setattr(rpt, "_build_report", lambda *a, **k: {"run_id": "x", "cross_study": {}})
    monkeypatch.setattr(rpt, "REPORTS_DIR", tmp_path)
    rpt.generate_sweep_report([], run_id="x")
    assert len(calls) == 1
    assert calls[0]["run_id"] == "x"
