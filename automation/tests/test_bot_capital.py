from types import SimpleNamespace as P

from automation.account_view import EToroAccountView
from automation.bot_capital import BotCapital


def _pos(pid, amount, pnl):
    return P(position_id=pid, amount=amount, pnl=pnl)


def test_equity_ist_kapital_plus_realisiert_plus_unrealisiert(tmp_path):
    bc = BotCapital(10_000, tmp_path / "s.json")
    assert bc.fresh
    assert bc.observe([_pos("1", 200, 5.0)]) == 10_005.0
    assert not bc.fresh
    assert bc.observe([_pos("1", 200, 8.0), _pos("2", 100, -2.0)]) == 10_006.0
    # Position 1 verschwindet: letzter PnL (8.0) wird realisiert
    assert bc.observe([_pos("2", 100, -3.0)]) == 10_000 + 8.0 - 3.0
    assert bc.observe([]) == 10_000 + 8.0 - 3.0


def test_teilschliessung_realisiert_anteilig(tmp_path):
    bc = BotCapital(10_000, tmp_path / "s.json")
    bc.observe([_pos("1", 200, 10.0)])
    eq = bc.observe([_pos("1", 100, 5.0)])      # Hälfte geschlossen: 5 realisiert, 5 offen
    assert eq == 10_000 + 5.0 + 5.0


def test_zustand_ueberlebt_neustart_und_kapitalwechsel_ist_neue_basis(tmp_path):
    p = tmp_path / "s.json"
    BotCapital(10_000, p).observe([_pos("1", 200, 4.0)])
    again = BotCapital(10_000, p)
    assert not again.fresh
    assert again.observe([]) == 10_004.0          # Position während Pause geschlossen
    assert BotCapital(20_000, p).fresh


def test_kontoguthaben_aendert_die_bot_equity_nicht(tmp_path):
    def pnl(credit):
        return {"clientPortfolio": {"credit": credit, "positions": [
            {"positionID": 1, "instrumentID": 5, "amount": 200, "isBuy": True, "leverage": 1, "unrealizedPnL": {"pnL": 3.0}}]}}
    seq = iter([pnl(86_700), pnl(56_700)])         # Copy-Trading schichtet 30.000 USD um
    view = EToroAccountView("k", "u", "demo", fetch=lambda *a: next(seq), ttl_s=0.0,
                            bot_capital=BotCapital(10_000, tmp_path / "s.json"))
    assert view.snapshot().equity == 10_003.0
    assert view.snapshot().equity == 10_003.0
    assert view.uses_bot_capital
