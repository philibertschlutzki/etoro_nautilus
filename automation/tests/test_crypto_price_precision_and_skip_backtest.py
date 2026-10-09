"""Krypto-Preis-Precision (Kleinpreis-Coins nicht auf 2 Stellen quantisieren) und --skip-backtest ohne Tagesdatei."""
import logging

from automation.utils import _fallback_precisions, apply_price_precision_floor


def test_small_price_coins_get_fine_price_precision():
    assert _fallback_precisions("DOGE.ETORO") == (5, 8)
    assert _fallback_precisions("XRP.ETORO")[0] == 4
    assert _fallback_precisions("ADA.ETORO")[0] == 4
    assert _fallback_precisions("BTC.ETORO") == (2, 8)
    assert _fallback_precisions("PEPExM.ETORO") == (8, 8)


def test_equity_precision_unchanged():
    assert _fallback_precisions("TSLA.ETORO") == (2, 2)
    assert apply_price_precision_floor("TSLA.ETORO", (2, 2)) == (2, 2)


def test_floor_raises_coarse_api_precision_for_crypto_only():
    assert apply_price_precision_floor("DOGE.ETORO", (2, 8)) == (5, 8)
    assert apply_price_precision_floor("DOGE.ETORO", (6, 8)) == (6, 8)


def test_phase5_no_deploy_without_tournament_is_not_an_error(tmp_path):
    from automation import daily_orchestrator as d
    log = logging.getLogger("t")
    rc = d.phase5_live_deployment(log, {}, {"tournament_path": str(tmp_path / "x.json")}, no_deploy=True)
    assert rc == 0
    rc = d.phase5_live_deployment(log, {}, {"tournament_path": str(tmp_path / "x.json")}, no_deploy=False)
    assert rc == 1


def test_latest_tournament_path(tmp_path):
    from automation import daily_orchestrator as d
    assert d.latest_tournament_path(tmp_path) is None
    (tmp_path / "tournament_2026-10-01.json").write_text("{}")
    (tmp_path / "tournament_2026-10-07.json").write_text("{}")
    assert d.latest_tournament_path(tmp_path).name == "tournament_2026-10-07.json"
