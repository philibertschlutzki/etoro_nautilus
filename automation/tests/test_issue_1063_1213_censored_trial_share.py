"""Issue #1063/#1213 (P1, Katalog #1196-1221) — Mehrheits-Zähler für guard-dominierte Studies
feuert bei 85 % nicht.

Symptom (B-9): Squeeze/NVDA 153/180 = 85,0 % (`f48a3f77`) und 152/180 = 84,4 % (`c429c992`)
zensierte Trials; §5.3 „Guard-dominierte Studies (#823): 0" in beiden Läufen.

Root-Cause: ZWEI unabhängige Defekte:
1. ``run_optimization.py``'s #823-Zähler (nur ``SORTINO_GUARD_TRIPPED``) und
   ``invariants.check_inference_diagnostics_concentration`` (breiterer Code-Satz) massen dieselbe
   Größe über verschiedene Kategorien — jetzt beide über ``invariants._censored_trial_share``.
2. ``report._study_record`` kopierte das von ``run_optimization._emit_study_summary`` gestempelte
   ``study_guard_dominated``-User-Attr NIE in den Study-Record — die Bruecke fehlte komplett
   (dasselbe Fehlermuster wie #1022/#1171, Pitfall #421). Das war die tatsaechliche Ursache des
   "0"-Symptoms bei 84-85% Zensur (weit ueber der bestehenden 10%-Schwelle).

Fix:
1. ``invariants._censored_trial_share(trials, *, codes=...)`` — EINE Zaehl-Funktion, konsumiert von
   ``check_inference_diagnostics_concentration`` (breiter Code-Satz, unveraendertes Verhalten) UND
   von ``run_optimization._emit_study_summary``s #823-Zaehler (mit den vom Issue explizit
   genannten Kategorien SORTINO_GUARD_TRIPPED ∪ SORTINO_INSUFFICIENT_DOWNSIDE).
2. ``report._study_record`` bruecke: ``record["study_guard_dominated"]`` aus
   ``study_user_attrs.get("study_guard_dominated")``.
3. ``summary_de.py`` §5.3 listet die dominierten Studies NAMENTLICH, nicht nur die Zahl.

Akzeptanzkriterien: für jede Study mit Zensur-Anteil > 50 % erscheint sie in §5.3; Regressionstest
mit 85 %.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import summary_de as sd


# --- invariants._censored_trial_share --------------------------------------------------------------

def _trial(codes):
    return {"inference_diagnostics": [{"code": c} for c in codes]}


def test_counts_only_the_explicit_default_categories():
    trials = [
        _trial(["SORTINO_GUARD_TRIPPED"]),
        _trial(["SORTINO_INSUFFICIENT_DOWNSIDE"]),
        _trial(["SORTINO_GUARD_REFERENCE_UNAVAILABLE"]),  # NICHT in der Default-Kategorie
        _trial([]),
    ]
    assert inv._censored_trial_share(trials) == 2


def test_counts_each_trial_at_most_once_even_with_both_codes():
    trials = [_trial(["SORTINO_GUARD_TRIPPED", "SORTINO_INSUFFICIENT_DOWNSIDE"])]
    assert inv._censored_trial_share(trials) == 1


def test_reproduces_the_b9_85_percent_scenario():
    """153/180 zensiert (85,0%) — dieselbe Zahl wie im f48a3f77-Referenzlauf."""
    trials = [_trial(["SORTINO_GUARD_TRIPPED"]) for _ in range(153)] + [_trial([]) for _ in range(27)]
    assert inv._censored_trial_share(trials) == 153
    assert 153 / 180 == 0.85


def test_custom_codes_parameter_lets_callers_use_a_wider_set():
    """check_inference_diagnostics_concentration ruft dieselbe Funktion mit dem breiteren,
    etablierten _REGULAR_THIRD_OUTCOME_CODES-Satz auf."""
    trials = [_trial(["SORTINO_GUARD_REFERENCE_UNAVAILABLE"])]
    assert inv._censored_trial_share(trials, codes=inv._CENSORED_TRIAL_MAJORITY_CODES) == 0
    assert inv._censored_trial_share(trials, codes=inv._REGULAR_THIRD_OUTCOME_CODES) == 1


def test_check_inference_diagnostics_concentration_still_fails_at_85_percent():
    trials = [_trial(["SORTINO_GUARD_TRIPPED"]) for _ in range(153)] + [_trial([]) for _ in range(27)]
    result = inv.check_inference_diagnostics_concentration(trials, n_trials_informative=180, n_trials=180)
    assert result.passed is False
    assert result.actual == 0.85


# --- summary_de.py §5.3: Liste statt nur Zahl ------------------------------------------------------

def _report_with_studies(studies):
    return {"run_id": "r", "studies": studies, "cross_study": {}, "invariant_checks": []}


def test_section_5_3_lists_dominated_studies_by_name():
    report = _report_with_studies([
        {"strategy": "SqueezeBreakoutStrategy", "symbol": "NVDA.ETORO", "study_guard_dominated": True},
        {"strategy": "MeanReversionStrategy", "symbol": "TSLA.ETORO", "study_guard_dominated": False},
    ])
    text = sd._section_5_anomalies(report)
    assert "Guard-dominierte Studies (SORTINO_GUARD_TRIPPED-Mehrheit, #823): 1" in text
    assert "SqueezeBreakoutStrategy/NVDA.ETORO" in text
    # der NICHT-dominierte Study-Eintrag darf nicht in der Namensliste erscheinen.
    _list_line = next(l for l in text.splitlines() if "SqueezeBreakoutStrategy/NVDA.ETORO" in l)
    assert "MeanReversionStrategy/TSLA.ETORO" not in _list_line


def test_section_5_3_shows_zero_and_no_list_without_any_dominated_study():
    report = _report_with_studies([
        {"strategy": "A", "symbol": "X.ETORO", "study_guard_dominated": False},
    ])
    text = sd._section_5_anomalies(report)
    assert "Guard-dominierte Studies (SORTINO_GUARD_TRIPPED-Mehrheit, #823): 0" in text
