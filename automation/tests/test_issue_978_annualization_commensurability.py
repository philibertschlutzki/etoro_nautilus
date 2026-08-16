"""Issue #978 (Katalog C, P0) — SUPERSEDED durch #948/#1114 (Katalog #960).

Die urspruengliche Fassung von ``check_annualization_commensurability`` massed die
INTRA-Trial-Fold-Streuung des annualisierten Sortino (``max(sqrt(F))/min(sqrt(F))`` je Trial). B-10
(Katalog #960, 3535 Trials) zeigte: diese Streuung ist STRUKTURELL und trivial (Median 1,709, 99,15 %
ueber der alten Schwelle 1,05) — ``_get_annualization_factor`` leitet F empirisch aus der je-Fold-
Beobachtungszahl ab, die bei RTH-Instrumenten auf 24/7-Raster je Fold schwankt. Seit #665/#589
konsumiert ohnehin KEIN Entscheidungspfad mehr den annualisierten Fold-Sortino — die alte Fassung
warnte also praktisch immer, ohne eine echte Gefahr zu markieren.

#948/#1114 definiert die Funktion neu: sie misst jetzt die Streuung des EINEN studienweiten,
gepoolten Annualisierungsfaktors (``holdout_sortino_annualized / holdout_sortino_period``) ÜBER
Studies DESSELBEN Symbols (severity ``low``, Diagnose statt Blocker) — siehe
``test_issue_948_1114_fold_annualization_period_scale.py`` für die neuen Akzeptanztests. Diese Datei
bleibt als historischer Marker erhalten (verweist auf die neue Signatur), damit ihr Dateiname im
Issue-Katalog auffindbar bleibt.
"""
from automation.optimizer import invariants as inv


def _study(symbol, sortino_period, sortino_annualized, strategy="A"):
    return {
        "strategy": strategy, "symbol": symbol,
        "holdout_sortino_period": sortino_period,
        "holdout_sortino_annualized": sortino_annualized,
    }


def test_single_study_per_symbol_is_not_applicable():
    """Ohne eine zweite Study desselben Symbols gibt es keine Vergleichsbasis — kein FAIL."""
    result = inv.check_annualization_commensurability([
        _study("NVDA.ETORO", 1.0, 35.0),
    ])
    assert result.passed is True
    assert result.severity == "low"


def test_commensurable_factor_across_studies_of_the_same_symbol_passes():
    result = inv.check_annualization_commensurability([
        _study("NVDA.ETORO", 1.0, 35.0),
        _study("NVDA.ETORO", 1.0, 35.5, strategy="B"),
    ])
    assert result.passed is True


def test_large_factor_jump_across_studies_of_the_same_symbol_fails():
    """sqrt(F)=35.0 vs. 70.0 (Faktor 2.0) fuer dasselbe Symbol -- ueber der 1.05-Schwelle."""
    result = inv.check_annualization_commensurability([
        _study("TSLA.ETORO", 1.0, 35.0),
        _study("TSLA.ETORO", 0.5, 35.0, strategy="B"),  # sqrt(F) = 70.0
    ])
    assert result.passed is False
    assert result.actual == 2.0
    assert result.severity == "low"
    assert "TSLA.ETORO" in result.provenance["offenders"]
