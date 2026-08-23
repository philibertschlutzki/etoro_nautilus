"""Issue #1060/#1209 (P0, Katalog #1196-1221) — Sizing-Cap nicht hart gedeckelt.

Symptom (B-10): der Backtest zeigt +66% über dem konfigurierten Sizing-Anteil (``f_realized`` bis
zu 1,66x ``trade_amount_pct``) — Aufstockung (Scale-in) innerhalb eines Round-Trips liess das
realisierte Notional ueber den konfigurierten Anteil hinaus anwachsen, weil
``hourly_strategy_base._compute_quantity`` jeden Sizing-Schritt nur gegen ``trade_amount_pct`` der
AKTUELLEN Equity bemisst, ohne das bereits offene Exposure des Instruments zu beruecksichtigen.

Fix:
1. ``_compute_quantity`` deckelt das AGGREGIERTE Netto-Exposure (bestehende offene Positionen +
   neue Order) hart auf ``trade_amount_pct * equity`` — ein Ueberschuss wird auf den verbleibenden
   Spielraum gekappt (oder auf 0/kein Entry, wenn kein Spielraum mehr besteht) und als
   ``SIZING_CAP_HIT`` geloggt statt still durchgereicht.
2. Neue Invariante ``invariants.check_sizing_cap_enforcement``: das GEMESSENE Maximum
   (``holdout_f_realized_max``, Rohmaterial aus ``backtest_runner._aggregate_exit_telemetry``)
   darf ``trade_amount_pct`` nur um eine Rundungstoleranz (1,05x) uebersteigen.

Hinweis: ``hourly_strategy_base.py`` importiert ``nautilus_trader.config.StrategyConfig`` als
Basisklasse fuer ``HourlyStrategyConfig`` — unter der sys.modules-Mock-Technik (kein installierbares
nautilus_trader in dieser Sandbox, Python 3.11 statt der geforderten >=3.12) wird diese Basisklasse
selbst zu einem MagicMock, wodurch echte Config-/Strategie-INSTANZIIERUNG (anders als der reine
Modul-Import fuer freie Funktionen, siehe test_issue_953_1119) nicht zuverlaessig moeglich ist. Die
harte Deckel-LOGIK selbst wird daher als eigenstaendige, reine Funktion extrahiert und direkt
getestet (arithmetisch identisch zum Inline-Code in ``_compute_quantity``, siehe dortiger
Kommentarverweis) plus ein Quelltext-Regressionswaechter, der sicherstellt, dass
``_compute_quantity`` diese Funktion tatsaechlich aufruft."""
import re
from pathlib import Path

from automation.optimizer import invariants as inv

# Hinweis: hourly_strategy_base.py definiert ``class HourlyStrategyBase(Strategy):`` — echte
# Vererbung von einer nautilus_trader-Basisklasse. Unter der sys.modules-Mock-Technik (kein
# installierbares nautilus_trader in dieser Sandbox) wird ``Strategy`` selbst zu einer MagicMock-
# INSTANZ, wodurch die Klassenanweisung keine echte Klasse mehr erzeugt (anders als der reine
# Funktions-/Modul-Import in backtest_runner.py, siehe test_issue_953_1119) — ``inspect.getsource``
# auf der importierten "Klasse" liefert dadurch keine verlaessliche Quelle. Die beiden Regressions-
# waechter unten lesen die Quelldatei stattdessen DIREKT (kein Import, kein Mocking noetig).
_SOURCE = Path(__file__).resolve().parents[1] / "strategies" / "hourly_strategy_base.py"


def _compute_quantity_source() -> str:
    text = _SOURCE.read_text("utf-8")
    start = text.index("def _compute_quantity(self, bar: Bar)")
    end = text.index("\n    def on_position_opened(self, event)")
    return text[start:end]


# --- Quelltext-Regressionswaechter: der Deckel ist tatsaechlich in _compute_quantity verdrahtet ----

def test_compute_quantity_source_contains_the_hard_cap_logic():
    source = _compute_quantity_source()
    assert "SIZING_CAP_HIT" in source
    assert "_existing_notional" in source
    assert "_cap_notional" in source
    # Der Deckel muss VOR der finalen Unit-Umrechnung (trade_amount_usd / price) greifen. Sucht
    # gezielt die CODE-Zeile (nicht die #716-Kommentarerwaehnung derselben Formel weiter oben).
    cap_pos = source.index("_cap_notional")
    conversion_pos = source.index("\n        units = trade_amount_usd / price")
    assert cap_pos < conversion_pos


def test_compute_quantity_caps_against_existing_open_position_notional():
    """Root-Cause-Regressionswaechter: der Deckel MUSS cache.positions_open(instrument_id=...)
    lesen (das bestehende Exposure DIESES Instruments), nicht nur die neue Ordergroesse isoliert
    betrachten -- sonst reproduziert der Fix exakt B-10 (Scale-in ignoriert bestehende Exposure)."""
    source = _compute_quantity_source()
    match = re.search(
        r"_existing_notional\s*=\s*sum\(\s*(.*?)\bfor p in self\.cache\.positions_open\("
        r"instrument_id=self\.instrument_id\)", source, re.DOTALL)
    assert match, "Der Deckel muss das bestehende Exposure ueber positions_open(instrument_id=...) lesen"


# --- Reine Arithmetik, identisch zur Inline-Logik: die Kappungs-Formel selbst -----------------------

def _apply_sizing_cap(trade_amount_usd, *, equity, trade_amount_pct, existing_notional):
    """Dieselbe Arithmetik wie der Inline-Block in _compute_quantity (siehe #1209-Kommentar dort),
    isoliert fuer einen direkten Unit-Test der Kappungs-Formel selbst."""
    if trade_amount_pct is None or trade_amount_pct <= 0 or not equity or equity <= 0:
        return trade_amount_usd, False
    cap_notional = equity * (trade_amount_pct / 100.0)
    headroom = cap_notional - existing_notional
    if headroom <= 0.0:
        return None, True
    if trade_amount_usd > headroom:
        return headroom, True
    return trade_amount_usd, False


def test_formula_no_existing_exposure_leaves_normal_sizing_untouched():
    granted, capped = _apply_sizing_cap(1500.0, equity=10000.0, trade_amount_pct=15.0,
                                        existing_notional=0.0)
    assert granted == 1500.0
    assert capped is False


def test_formula_scale_in_is_capped_to_remaining_headroom():
    granted, capped = _apply_sizing_cap(1500.0, equity=10000.0, trade_amount_pct=15.0,
                                        existing_notional=1000.0)
    assert granted == 500.0
    assert capped is True


def test_formula_no_headroom_left_rejects_entirely():
    granted, capped = _apply_sizing_cap(1500.0, equity=10000.0, trade_amount_pct=15.0,
                                        existing_notional=2000.0)
    assert granted is None
    assert capped is True


def test_formula_aggregate_never_exceeds_configured_percentage():
    """Fuer bestehendes Exposure INNERHALB des Deckels darf das Aggregat (bestehend + neu gewaehrt)
    den Deckel nie ueberschreiten."""
    cap = 10000.0 * 15.0 / 100.0
    for existing in (0.0, 500.0, 1000.0, 1490.0, 1500.0):
        granted, _ = _apply_sizing_cap(1500.0, equity=10000.0, trade_amount_pct=15.0,
                                       existing_notional=existing)
        assert existing + (granted or 0.0) <= cap + 1e-9
    # existing > cap (ein Zustand ausserhalb der Kontrolle DIESES Aufrufs): die Funktion darf
    # trotzdem KEIN zusaetzliches Exposure gewaehren.
    granted_over_cap, _ = _apply_sizing_cap(1500.0, equity=10000.0, trade_amount_pct=15.0,
                                            existing_notional=2000.0)
    assert granted_over_cap is None


def test_formula_inactive_without_configured_trade_amount_pct():
    granted, capped = _apply_sizing_cap(100.0, equity=10000.0, trade_amount_pct=None,
                                        existing_notional=1_000_000.0)
    assert granted == 100.0
    assert capped is False


# --- invariants.check_sizing_cap_enforcement --------------------------------------------------------

def _study(strategy, symbol, *, trade_amount_pct, f_realized_max):
    return {"strategy": strategy, "symbol": symbol, "trade_amount_pct": trade_amount_pct,
            "holdout_f_realized_max": f_realized_max}


def test_check_passes_when_max_within_tolerance():
    result = inv.check_sizing_cap_enforcement([
        _study("A", "X.ETORO", trade_amount_pct=15.0, f_realized_max=0.153),  # 15.3% <= 1.05*15
    ])
    assert result.passed is True


def test_check_fails_reproducing_b10_signature():
    """f_realized bis zu 1,66x trade_amount_pct -- weit ueber der 1,05x-Toleranz."""
    result = inv.check_sizing_cap_enforcement([
        _study("AdxAtrMomentumStrategy", "KRYS.ETORO", trade_amount_pct=15.0, f_realized_max=0.249),
    ])
    assert result.passed is False
    assert result.severity == "blocking"
    offender = result.actual["AdxAtrMomentumStrategy/KRYS.ETORO"]
    assert abs(offender["overshoot_factor"] - 1.66) < 0.01


def test_check_not_applicable_without_data():
    """Issue #1084/#1232 Fix Punkt 2 — VERSCHAERFUNG: eine fehlende Eingabe (kein Study mit
    holdout_f_realized_max) darf bei einem 'blocking'-Check nicht wie ein sauberer PASS aussehen.
    ``passed=None``/``evaluable=False`` (INCONCLUSIVE) ersetzt das vormalige fail-open
    ``passed=True`` — exakt das Symptom aus #1084 (11/11 Laeufe passed=True, actual=None, weil
    confirm.py das Feld nie in den Study-Record durchreichte)."""
    result = inv.check_sizing_cap_enforcement([{"strategy": "A", "symbol": "X.ETORO"}])
    assert result.passed is None
    assert result.evaluable is False
    assert result.actual is None
