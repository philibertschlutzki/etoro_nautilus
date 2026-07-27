"""Issue #798 — period_returns bleibt nach dem Confirm dauerhaft im Trial-Output.

Root-Cause: die per-Perioden-Renditeserie wird ausschliesslich von zwei Konsumenten gebraucht
(confirm._study_pbo/CSCV #663, der Stationary-Bootstrap-CI #619), beide lesen sie aus
``trial.user_attrs`` NACH Study-Abschluss — nicht aus dem Trial-Verzeichnis. Die Kopie auf der
Platte hat keinen Leser, kostet aber bis zu ``period_returns_cap`` Floats je Ebene (IS/OOS/Fold).
Dieser Test deckt drei Teile ab: (1) die Kappungsgrenze ist jetzt ein deklarativer Config-Key statt
einer hartkodierten Konstante, (2) ``period_returns_truncated`` telemetriert eine tatsaechliche
Kappung, (3) ``parsing.parse_tournament`` entfernt ``period_returns`` aus der persistierten Datei,
nachdem es in ``TournamentMetrics.oos_period_returns`` gehoben wurde.
"""
import json
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from automation.backtest_runner import _calculate_stats, _read_period_returns_cap
from automation.optimizer.parsing import parse_tournament


def _metrics_from_returns(period_rets: list[float]) -> dict:
    mtm = [1000.0]
    for r in period_rets:
        mtm.append(mtm[-1] * (1 + r))
    idx = pd.date_range("2025-01-01", periods=len(mtm), freq="1h", tz="UTC")
    series = pd.Series(mtm, index=idx)
    pnls = [v if v != 0 else 0.01 for v in period_rets]
    holds = [(3600 * 10**9, 1.0)] * len(pnls)
    with patch("automation.backtest_runner._read_sortino_numeric_guard", return_value=1e9):
        return _calculate_stats(pnls, holds, 1000.0, mtm_series=series, min_trades_for_sortino=3)


def test_read_period_returns_cap_defaults_to_2000(monkeypatch):
    import automation.backtest_runner as br
    monkeypatch.setattr(br, "_period_returns_cap_cache", None)
    monkeypatch.setattr(br, "config_dir", lambda: __import__("pathlib").Path("/nonexistent"))
    assert _read_period_returns_cap() == 2000


def test_read_period_returns_cap_reads_config(tmp_path, monkeypatch):
    import automation.backtest_runner as br
    monkeypatch.setattr(br, "_period_returns_cap_cache", None)
    (tmp_path / "optimizer.json").write_text(json.dumps({"period_returns_cap": 50}), "utf-8")
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    assert _read_period_returns_cap() == 50


def test_period_returns_capped_and_truncated_flag_set(monkeypatch):
    import automation.backtest_runner as br
    monkeypatch.setattr(br, "_period_returns_cap_cache", 10)  # klein, damit die 60 Returns kappen
    rng = np.random.default_rng(7)
    rets = rng.normal(0.001, 0.01, 60).tolist()
    m = _metrics_from_returns(rets)
    assert len(m["period_returns"]) == 10
    assert m["period_returns_truncated"] is True
    assert m["n_periods"] == 60  # len(period_rets) == len(mtm)-1 == 61-1


def test_period_returns_not_truncated_when_within_cap(monkeypatch):
    import automation.backtest_runner as br
    monkeypatch.setattr(br, "_period_returns_cap_cache", 2000)
    rng = np.random.default_rng(8)
    rets = rng.normal(0.001, 0.01, 30).tolist()
    m = _metrics_from_returns(rets)
    assert len(m["period_returns"]) == len(m["period_returns"])  # sanity, no cap hit
    assert m["period_returns_truncated"] is False


def test_parse_tournament_strips_period_returns_from_disk(tmp_path):
    """Nach dem Parsen enthaelt die persistierte Datei keinen period_returns-Schluessel mehr,
    waehrend TournamentMetrics.oos_period_returns die volle geparste Serie traegt."""
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "oos_metrics": {
                "sortino_ratio": 1.2, "max_drawdown": 0.05,
                "period_returns": [0.01, -0.02, 0.03],
                "period_returns_truncated": False,
            },
        },
    }
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(payload), "utf-8")

    metrics = parse_tournament(p)

    assert metrics.oos_period_returns == (0.01, -0.02, 0.03)
    assert metrics.period_returns_truncated is False

    on_disk = json.loads(p.read_text("utf-8"))
    assert "period_returns" not in on_disk["aggregate_winner"]["oos_metrics"]
    # die uebrigen Metriken bleiben unangetastet
    assert on_disk["aggregate_winner"]["oos_metrics"]["sortino_ratio"] == 1.2


def test_parse_tournament_passes_through_truncated_flag(tmp_path):
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True,
            "oos_metrics": {
                "period_returns": list(range(5)),
                "period_returns_truncated": True,
            },
        },
    }
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(payload), "utf-8")
    metrics = parse_tournament(p)
    assert metrics.period_returns_truncated is True


def test_parse_tournament_no_op_write_when_no_period_returns_present(tmp_path):
    """Fehlt period_returns ganz (Legacy-JSON), darf parse_tournament die Datei nicht anfassen."""
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": None}
    p = tmp_path / "tournament_result.json"
    original_text = json.dumps(payload)
    p.write_text(original_text, "utf-8")
    mtime_before = p.stat().st_mtime_ns

    metrics = parse_tournament(p)

    assert metrics.period_returns_truncated is False
    assert p.stat().st_mtime_ns == mtime_before, "Datei ohne period_returns darf nicht neu geschrieben werden"
