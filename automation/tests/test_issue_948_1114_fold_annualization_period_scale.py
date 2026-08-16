"""Issue #948/#1114 (Katalog #960) — Fold-Annualisierung ist innerhalb desselben Trials
inkommensurabel.

Symptom (B-10): über 3535 Trials sqrt(F)-Spannweite INNERHALB eines Trials Median 1,709, p99 3,260,
99,15 % über der alten Check-Schwelle 1,05; 90 Trials wechseln zwischen annualisierter und
Perioden-Skala das Vorzeichen im Fold-Median.

Root-Cause: ``_get_annualization_factor`` bestimmt den Faktor je Fold empirisch aus der Zahl der
informativen Bars DIESES Folds — bei RTH-Instrumenten auf 24/7-Raster schwankt diese Zahl je Fold
strukturell (``check_n_periods_homogeneity``).

Fix (verifiziert in diesem Modul):
1. Grep-Test: kein Entscheidungspfad konsumiert ``per_fold_oos_sortino``/``oos_fold_sortinos``
   (den annualisierten, fold-spezifisch skalierten Wert) — bereits seit #665/#589 der Fall
   (``confirm._study_pbo`` rechnet auf eigenen S>=8-Gruppen der gepoolten ``oos_period_returns``;
   ``reward.fold_dispersion`` auf ``oos_fold_returns``; beide annualisierungsfrei).
2. ``per_fold_oos_sortino`` bleibt Anzeigegrösse (Trial-User-Attr-Stempelung), keine Entscheidung.
3. ``check_annualization_commensurability`` ist von ``high`` auf ``low`` herabgestuft und misst
   jetzt die Streuung des EINEN studienweiten (gepoolten) Faktors über Studies desselben Symbols —
   nicht mehr die triviale Intra-Trial-Fold-Streuung.
4. ``confirm._metrics_dict``/``report._study_record`` transportieren das dafür nötige Rohmaterial
   (``oos_sortino_period``/``oos_sortino_annualized`` bzw. ``holdout_sortino_period``/
   ``holdout_sortino_annualized``) bis in den Report.
"""
import ast
from pathlib import Path

from automation.optimizer import invariants as inv
from automation.optimizer import confirm


_REPO_ROOT = Path(__file__).resolve().parents[2]

# Issue #948 Akzeptanzkriterium — Dateien/Funktionen, in denen ``per_fold_oos_sortino``/
# ``oos_fold_sortinos`` (die annualisierte, fold-spezifisch skalierte Serie) LEGITIM auftreten
# duerfen: reine Definition/Berechnung (backtest_runner.py), reine Anzeige-/Telemetrie-Stempelung
# (run_optimization.py user_attrs, sweep.py Docstring), das dedizierte Kohaerenz-Diagnose-
# Werkzeug selbst (invariants.py, das die Divergenz zur Perioden-Skala MISST statt sie zu
# konsumieren) sowie Test-Fixtures.
_ALLOWED_FILES = {
    "automation/backtest_runner.py",
    "automation/optimizer/parsing.py",
    "automation/optimizer/run_optimization.py",
    "automation/optimizer/sweep.py",
    "automation/optimizer/invariants.py",
}


def test_no_decision_path_outside_backtest_runner_run_optimization_or_invariants_consumes_it():
    """Grep-Test (Akzeptanzkriterium #948): ausserhalb der erlaubten Dateien (Definition, reine
    Telemetrie-Stempelung, die Diagnose-Invariante selbst) darf ``per_fold_oos_sortino``/
    ``oos_fold_sortinos`` nirgends mehr auftauchen — insbesondere nicht in confirm.py (PBO/CSCV)
    oder reward.py (fold_dispersion), den beiden im Issue namentlich benannten Risikoorten."""
    hits = []
    for path in (_REPO_ROOT / "automation").rglob("*.py"):
        rel = path.relative_to(_REPO_ROOT).as_posix()
        if rel in _ALLOWED_FILES or rel.startswith("automation/tests/"):
            continue
        text = path.read_text(encoding="utf-8")
        if "per_fold_oos_sortino" in text or "oos_fold_sortinos" in text:
            hits.append(rel)
    assert hits == [], f"Entscheidungsnahe Dateien referenzieren die annualisierte Serie: {hits}"


def test_study_pbo_does_not_reference_the_annualized_fold_series():
    """confirm._study_pbo ist im Issue namentlich als betroffene Datei genannt; verifiziert, dass
    ihr Quelltext (nicht nur der Docstring) frei von der annualisierten Serie ist."""
    import inspect
    src = inspect.getsource(confirm._study_pbo)
    assert "per_fold_oos_sortino" not in src
    assert "oos_fold_sortinos" not in src


def test_metrics_dict_carries_pooled_sortino_period_and_annualized():
    """Issue #948 Fix Punkt 4 — confirm._metrics_dict transportiert das Rohmaterial fuer den neu
    definierten check_annualization_commensurability bis in den Holdout-Report."""
    class _M:
        oos_sortino = 1.0
        oos_max_drawdown = 0.1
        oos_evaluated = True
        oos_eligible = True
        oos_total_trades = 10
        oos_sortino_period = 0.5
        oos_sortino_annualized = 17.5

    d = confirm._metrics_dict(_M())
    assert d["oos_sortino_period"] == 0.5
    assert d["oos_sortino_annualized"] == 17.5


def _study(symbol, sortino_period, sortino_annualized, strategy="A"):
    return {
        "strategy": strategy, "symbol": symbol,
        "holdout_sortino_period": sortino_period,
        "holdout_sortino_annualized": sortino_annualized,
    }


def test_check_annualization_commensurability_severity_is_low():
    """Fix Punkt 3 — von 'high' auf 'low' herabgestuft (Diagnose statt Blocker)."""
    result = inv.check_annualization_commensurability([
        _study("NVDA.ETORO", 1.0, 35.0),
        _study("NVDA.ETORO", 0.5, 35.0, strategy="B"),  # sqrt(F)=70.0, Faktor 2.0
    ])
    assert result.severity == "low"
    assert result.passed is False


def test_check_annualization_commensurability_ignores_intra_trial_fold_data():
    """Reproduziert B-10 als NEGATIVBEWEIS: massive Intra-Trial-Fold-Streuung (die alte Fassung
    haette hier auf praktisch jedem Trial gefeuert) beeinflusst das neue, studienweite Mass NICHT
    mehr — die Funktion nimmt gar kein fold-aufgeloestes Feld mehr entgegen."""
    result = inv.check_annualization_commensurability([
        {
            "strategy": "A", "symbol": "GSAT.ETORO",
            "holdout_sortino_period": 1.0, "holdout_sortino_annualized": 35.0,
            # Fold-Rohdaten (waeren fuer die ALTE Fassung relevant) -- absichtlich stark streuend,
            # duerfen aber keinen Effekt mehr haben.
            "per_fold_oos_sortino": [36.83, 35.94, 34.08, 33.97],
            "per_fold_oos_sortino_period": [1.0, 1.0, 1.0, 1.0],
        },
        {
            "strategy": "B", "symbol": "GSAT.ETORO",
            "holdout_sortino_period": 1.0, "holdout_sortino_annualized": 35.5,
        },
    ])
    assert result.passed is True


def test_check_annualization_commensurability_not_applicable_without_sibling_study():
    result = inv.check_annualization_commensurability([_study("PLTR.ETORO", 1.0, 40.0)])
    assert result.passed is True
    assert result.actual is None


def test_check_annualization_commensurability_provenance_reports_study_fraction():
    result = inv.check_annualization_commensurability([
        _study("TSLA.ETORO", 1.0, 35.0),
        _study("TSLA.ETORO", 0.5, 35.0, strategy="B"),
        _study("TSLA.ETORO", 1.0, 35.2, strategy="C"),
    ])
    assert result.passed is False
    assert result.provenance["n_studies_comparable"] == 3
    assert result.provenance["n_symbols_comparable"] == 1
