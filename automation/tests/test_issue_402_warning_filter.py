"""Issue #402 — Optuna ExperimentalWarnings (multivariate/group) spammen das Terminal.

Der Import von `run_optimization` registriert global einen gezielten `ignore`-Filter NUR
fuer `optuna.exceptions.ExperimentalWarning`. Bewusst KEIN globales set_verbosity(ERROR):
Optunas native Per-Trial-INFO-Logs (Reward-Werte) bleiben erhalten (Observability, #401/#403).

Hinweis: pytest verwaltet `warnings.filters` pro Test (und Python 3.12 `catch_warnings(
record=True)` setzt `simplefilter('always')`). Deshalb wird das Modul innerhalb eines
kontrollierten `catch_warnings`-Blocks via `importlib.reload` neu geladen — das fuehrt den
Modul-Level-`filterwarnings`-Aufruf deterministisch erneut aus.
"""
import importlib
import warnings

import optuna

from automation.optimizer import run_optimization


def test_import_registers_experimental_warning_ignore_filter():
    with warnings.catch_warnings():
        warnings.resetwarnings()  # leere Filterliste im isolierten Kontext
        importlib.reload(run_optimization)  # wendet warnings.filterwarnings(...) erneut an
        assert any(
            f[0] == "ignore" and f[2] is optuna.exceptions.ExperimentalWarning
            for f in warnings.filters
        ), "Import von run_optimization muss ExperimentalWarning global auf 'ignore' setzen (Issue #402)"


def test_sampler_creation_emits_no_experimental_warning():
    """Verhaltensbeweis: nach Modul-(Re)Import erzeugt eine TPESampler-Instanziierung mit den
    experimentellen Flags keine ExperimentalWarning mehr. Faellt der filterwarnings-Aufruf weg
    (Regress), wird die Warnung aufgezeichnet und der Test schlaegt fehl."""
    with warnings.catch_warnings(record=True) as rec:
        importlib.reload(run_optimization)  # re-applied den ignore-Filter ueber dem record-'always'
        optuna.samplers.TPESampler(multivariate=True, group=True, n_startup_trials=1, seed=1)
    experimental = [w for w in rec if issubclass(w.category, optuna.exceptions.ExperimentalWarning)]
    assert experimental == []


def test_filter_does_not_force_optuna_error_verbosity():
    """Die native Optuna-Verbosity wurde NICHT auf ERROR gedreht — Per-Trial-INFO bleibt
    moeglich (Observability-Invariante; verhindert ein Over-Fix von #402 gegen #401/#403)."""
    assert optuna.logging.get_verbosity() < optuna.logging.ERROR
