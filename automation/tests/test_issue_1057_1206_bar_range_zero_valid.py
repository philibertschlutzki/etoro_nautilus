"""Issue #1057/#1206 (P0, Katalog #1196-1221) — ``#1171`` ist nicht wirksam: die Emissionskette
fuer ``bar_range_median_bps`` (siehe ``test_issue_1022_1171_bar_range_median_bps.py``: Tag ->
``rt_exit_meta.append`` -> ``parsing`` -> ``set_user_attr`` -> Study-Record) liefert seit diesem
Fix bereits einen echten Messwert je Study — aber in ALLEN 14 Studies eines beobachteten Laufs war
dieser Wert exakt ``0.0`` statt ``None``/fehlend.

Root-Cause (NEU, verschieden von #1022/#1171): ``check_stop_loss_vs_bar_range`` filterte seine
Kandidatenmenge mit ``if r.get("bar_range_median_bps")`` — einem WAHRHEITSWERT-Test statt einer
``is not None``-Pruefung. ``0.0`` ist in Python falsy, obwohl es ein valider, GEMESSENER Wert ist
(anders als ein fehlendes/``None``-Feld). Jede Study mit dieser (durchaus moeglichen) Bar-Spanne
von 0 bps fiel dadurch aus der Kandidatenmenge -- bei ALLEN 14 Studies blieb ``candidates`` leer
und der blockierende Check war strukturell IMMER INCONCLUSIVE, obwohl die Emissionskette
funktionierte.

Fix: 1) das Praedikat auf ``is not None`` umgestellt (ein echter 0.0-Messwert zaehlt als Kandidat).
2) Pitfall #404 ("ein blocking-Check darf auf leerer Grundgesamtheit nie passed=True liefern"):
wenn NACH der Korrektur trotzdem kein einziges Verhaeltnis bildbar ist (alle Kandidaten mit
``bar_range_median_bps <= 0``, ``loss / bar_range`` waere undefiniert), liefert der Check jetzt
explizit ``passed=False`` mit ``inconclusive_reason='POPULATION_UNAVAILABLE_AFTER_FIX'`` -- wie in
#1206 gefordert -- statt eines stillen PASS oder eines erneuten INCONCLUSIVE."""
from automation.optimizer import invariants as inv


def _study(strategy, symbol, *, loss_pooled, bar_range, stop_loss_ratio, n_ts_losses=40):
    return {
        "strategy": strategy, "symbol": symbol,
        "oos_gross_loss_mean_bps_trailing_stop_pooled": loss_pooled,
        "bar_range_median_bps": bar_range,
        "realized_stop_loss_ratio": stop_loss_ratio,
        "oos_n_trailing_stop_losses": n_ts_losses,
    }


def test_zero_bar_range_median_bps_is_a_measured_value_not_missing_evidence():
    """Vorher: bar_range=0.0 wurde wie ein fehlendes Feld behandelt -> INCONCLUSIVE trotz
    hinreichender TRAILING_STOP-Exits und aller anderen Eingangsgroessen. Jetzt: der Check ist
    evaluable (auch wenn dieser einzelne Kandidat wegen bar_range<=0 kein Verhaeltnis beitraegt,
    siehe test_all_candidates_zero_bar_range_is_a_named_fail unten fuer den reinen 1-Study-Fall)."""
    result = inv.check_stop_loss_vs_bar_range([
        _study("A", "X.ETORO", loss_pooled=10.0, bar_range=0.0, stop_loss_ratio=6.0),
    ])
    assert result.evaluability["n_candidates"] == 1, (
        "bar_range_median_bps=0.0 muss als GEMESSEN zaehlen (is not None), nicht als fehlend "
        "(truthy-Test war der #1206-Bug)."
    )


def test_all_candidates_zero_bar_range_is_a_named_fail_not_a_silent_pass():
    result = inv.check_stop_loss_vs_bar_range([
        _study("A", "X.ETORO", loss_pooled=10.0, bar_range=0.0, stop_loss_ratio=6.0),
        _study("B", "Y.ETORO", loss_pooled=20.0, bar_range=0.0, stop_loss_ratio=9.0),
    ])
    assert result.passed is False
    assert result.evaluable is True
    assert result.inconclusive is not True  # kein INCONCLUSIVE-Zweig
    assert result.evaluability["inconclusive_reason"] == "POPULATION_UNAVAILABLE_AFTER_FIX"
    assert result.evaluability["n_candidates"] == 2
    assert result.evaluability["n_measured"] == 0


def test_mixed_zero_and_positive_bar_range_still_evaluates_the_positive_ones():
    """Ein gemischter Lauf (manche Studies mit echter Bar-Spanne, manche mit 0.0) darf die
    auswertbaren Studies nicht wegen der 0.0-Studies verlieren."""
    result = inv.check_stop_loss_vs_bar_range([
        _study("A", "X.ETORO", loss_pooled=10.0, bar_range=0.0, stop_loss_ratio=6.0),
        _study("SqueezeBreakout", "PLTR.ETORO", loss_pooled=10.0, bar_range=12.0,
               stop_loss_ratio=40.0),
    ])
    assert result.evaluable is True
    assert result.evaluability["n_candidates"] == 2
    assert result.evaluability["n_measured"] == 1
    assert result.passed is False  # das zweite Study reproduziert die B-11-Signatur (siehe #953/#1119)
    assert "SqueezeBreakout/PLTR.ETORO" in result.actual


def test_still_inconclusive_when_bar_range_field_is_genuinely_missing():
    """Regressionsschutz: ein echtes ``None`` (kein Messwert erreichte den Study-Record) bleibt
    weiterhin vom Kandidatenkreis ausgeschlossen -- nur der 0.0-Fall war der Bug."""
    result = inv.check_stop_loss_vs_bar_range([
        _study("A", "X.ETORO", loss_pooled=10.0, bar_range=None, stop_loss_ratio=6.0),
    ])
    assert result.evaluability["n_candidates"] == 0
    assert result.inconclusive is True
