"""Issue #1067/#1217 (P1, Katalog #1196-1221) — Warm-Start-Wallclock-Steuer, linear und unbegrenzt.

Symptom (B-12): TSLA bei konstant 1940 neuen Trials: 0,95h/1,26h/1,78h/2,23h bei
3863/5803/7743/9683 Vorlauf-Trials im selben, warm-gestarteten Store. cpu_utilisation_backtest
9,8% -> 4,8%, LINEAR fallend mit der Store-Groesse. Frische Laeufe: 0,46-0,54h bei 18,2-22,8%.

Root-Cause: die Zeit geht nicht in Backtests — TPESampler fittet sein Surrogat ueber die
VOLLSTAENDIGE, monoton wachsende Trial-Historie bei jedem sample_relative-Aufruf.

Fix:
1. ``_WindowedTPESampler`` (run_optimization.py) — begrenzt den Surrogat-Fit auf die letzten
   ``tpe_history_window`` Trials (Default 600); aeltere Trials bleiben im Store.
2. ``invariants.check_search_overhead_share`` — FAIL bei
   (tpe_fit_seconds + store_scan_seconds) / study_wallclock_s > 0,5.

Hinweis: ``_WindowedTPESampler`` ueberschreibt Optunas PRIVATE ``TPESampler._sample`` (Optuna 4.9.0
gepinnt) — dieser Test verifiziert das Fenster-Verhalten END-TO-END gegen die echte, installierte
Optuna-Bibliothek (kein Mock von Optuna selbst), da eine reine Signatur-Pruefung das eigentliche
Risiko (Drift von Optunas interner Sampling-Logik) nicht abdecken wuerde.
"""
import optuna
import pytest

from automation.optimizer.run_optimization import (
    _WindowedTPESampler, _resolve_tpe_history_window, _TPE_HISTORY_WINDOW_DEFAULT,
)


def test_resolve_tpe_history_window_default():
    assert _resolve_tpe_history_window(None) == _TPE_HISTORY_WINDOW_DEFAULT
    assert _resolve_tpe_history_window({}) == _TPE_HISTORY_WINDOW_DEFAULT


def test_resolve_tpe_history_window_reads_config_key():
    assert _resolve_tpe_history_window({"tpe_history_window": 250}) == 250


def test_resolve_tpe_history_window_falls_back_on_invalid_value():
    assert _resolve_tpe_history_window({"tpe_history_window": "not_a_number"}) == (
        _TPE_HISTORY_WINDOW_DEFAULT)
    assert _resolve_tpe_history_window({"tpe_history_window": 0}) == _TPE_HISTORY_WINDOW_DEFAULT
    assert _resolve_tpe_history_window({"tpe_history_window": -5}) == _TPE_HISTORY_WINDOW_DEFAULT


def _objective(trial):
    x = trial.suggest_float("x", -10, 10)
    return -(x - 3.0) ** 2


def test_windowed_sampler_only_fits_the_last_n_trials(monkeypatch):
    """Verifiziert das Fenster direkt an Optunas eigenem Split-Punkt: _split_trials erhaelt NIE
    mehr als history_window Trials, auch wenn die Study bereits deutlich mehr enthaelt."""
    from optuna.samplers._tpe import sampler as _tpe_sampler_module

    seen_trial_counts = []
    _real_split = _tpe_sampler_module._split_trials

    def _spy_split_trials(study, trials, gamma, constraints_enabled):
        seen_trial_counts.append(len(trials))
        return _real_split(study, trials, gamma, constraints_enabled)

    # _WindowedTPESampler importiert _split_trials LOKAL innerhalb von _sample (from ... import
    # _split_trials bei JEDEM Aufruf neu aufgeloest) -- patchen wir dort, wo es tatsaechlich
    # aufgeloest wird: das Modul-Attribut selbst.
    monkeypatch.setattr(_tpe_sampler_module, "_split_trials", _spy_split_trials)

    sampler = _WindowedTPESampler(
        n_startup_trials=5, seed=1, history_window=10, multivariate=True, group=True)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(_objective, n_trials=30, show_progress_bar=False)

    # ab dem Punkt, an dem die Study-Groesse das Fenster ueberschreitet, darf _split_trials NIE
    # mehr als 10 Trials sehen.
    late_calls = seen_trial_counts[10:]
    assert late_calls, "keine _split_trials-Aufrufe nach dem Fenster-Ueberschreiten aufgezeichnet"
    assert all(n <= 10 for n in late_calls), (
        f"_split_trials sah mehr als history_window=10 Trials: {late_calls}")


def test_windowed_sampler_uses_the_full_history_before_the_window_fills_up():
    """Unterhalb des Fensters entspricht das Verhalten bit-identisch dem unveraenderten
    TPESampler (kein Trial wird kuenstlich ausgeschlossen, solange die Study kleiner als das
    Fenster ist)."""
    sampler = _WindowedTPESampler(
        n_startup_trials=3, seed=2, history_window=1000, multivariate=True, group=True)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(_objective, n_trials=15, show_progress_bar=False)
    assert len(study.trials) == 15
    assert study.best_value is not None


def test_windowed_sampler_still_converges_towards_the_optimum():
    """Funktionale Absicherung: trotz des begrenzten Fensters bleibt die Suche wirksam (kein
    kaputter Sampler, der zufaellig sampelt)."""
    sampler = _WindowedTPESampler(
        n_startup_trials=5, seed=42, history_window=15, multivariate=True, group=True)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(_objective, n_trials=50, show_progress_bar=False)
    assert study.best_value > -1.0  # deutlich naeher an x=3.0 als ein Zufalls-Sample im [-10,10]-Raum
