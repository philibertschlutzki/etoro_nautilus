"""Issue #784 (P0) — Die DSR-Deflations-Familie zählt Überlebende statt Versuche; vorzeitig
abgebrochene Suchen bekommen dadurch eine weichere Schwelle.

Root-Cause: ``sweep._family_n_from_studies`` zählte ``oos_eligible is True`` (ÜBERLEBENDE des
Eligibility-Filters) statt der tatsächlich gezogenen Kandidaten. Die Deflated Sharpe Ratio korrigiert
für die Multiplizität der SUCHE (N gezogene Kandidaten) — der Eligibility-Filter ist selbst ein
Selektionsschritt und gehört IN die Korrektur, nicht davor. Über den Katalog-Lauf: 16 318 eligible
von 125 339 gelaufenen Trials = 13,02 % ⇒ ``n_family`` unterschätzte die reale Multiplizität um
Faktor ~7,7. Zweiter Effekt: Spearman(n_family, Budgetausführung)=+0,220 über 63 Symbole — ein
Symbol, dessen Studies früh abgebrochen wurden (#768/#769), wurde MILDER deflatiert als eines, das
vollständig durchlief; die Korrektur bestrafte gründliche Suche und belohnte abgebrochene.

Fix: ``_family_n_from_studies``/``_family_period_returns_from_studies`` zählen ``oos_evaluated is
True`` (Versuche); ``tournament.json['deflation_family_floor_mode']`` (Default ``'budgeted'``) hebt
die je-Study-Zahl zusätzlich auf ``n_trials_budget`` an, wenn dieser Wert grösser ist als die
tatsächlich evaluierten Trials — ein Abbruch reduziert die gezogenen Kandidaten, aber der Suchraum,
aus dem selektiert wurde, war der volle geplante.
"""
import json
from pathlib import Path

from automation.optimizer import sweep

CFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


class _T:
    def __init__(self, evaluated, eligible=False, rets=None):
        # Issue #822 — _family_n_from_studies zaehlt seit dem Fix
        # oos_selection_statistic_available statt oos_evaluated; dieses Fixture testet die
        # #784-Versuche-vs-Ueberlebende-Unterscheidung, nicht die #822-Teststatistik-Unterscheidung.
        attrs = {"oos_evaluated": evaluated, "oos_eligible": eligible,
                "oos_selection_statistic_available": evaluated}
        if rets is not None:
            attrs["oos_period_returns"] = rets
        self.user_attrs = attrs


class _Study:
    def __init__(self, trials, n_trials_budget=None):
        self.trials = trials
        self.user_attrs = {} if n_trials_budget is None else {"n_trials_budget": n_trials_budget}


# ── Config: deklarativer Floor-Modus ───────────────────────────────────────────────────────────
def test_config_declares_attempted_as_default_floor_mode():
    # Issue #814 — der Default wanderte von 'budgeted' (#784) weiter auf 'attempted'.
    assert CFG["deflation_family_floor_mode"] == "attempted"


def test_schema_documents_the_784_rationale():
    doc = CFG["_schema"]["fields"]["deflation_family_floor_mode"]
    assert "#784" in doc
    assert "#814" in doc


# ── Akzeptanzkriterium #784/1: n_family_raw zählt Versuche, nicht Überlebende ─────────────────────
def test_study_with_100_evaluated_10_eligible_trials_yields_n_family_100():
    """Vor #784 wäre dies 10 (nur die eligiblen) gewesen."""
    trials = [_T(evaluated=True, eligible=(i < 10)) for i in range(100)]
    studies = [_Study(trials)]
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    family_n = sweep._family_n_from_studies(
        pairs, studies, tournament_cfg={"deflation_family_floor_mode": "attempted"})
    assert family_n["TSLA.ETORO"] == 100


# ── Akzeptanzkriterium #784/2/4: Budget-Untergrenze koppelt n_family NICHT invers an Abbruch ─────
def test_budgeted_mode_lifts_early_stopped_study_to_planned_budget():
    """Nur 8 von geplanten 120 Trials evaluiert (früher #768/#769-Plateau-Abbruch): 'budgeted'
    (Default) hebt die Multiplizität auf das GEPLANTE Budget an, statt die abgebrochene Study
    milder zu deflatieren als eine vollständig durchlaufene."""
    early_stopped = _Study([_T(evaluated=True)] * 8, n_trials_budget=120)
    pairs = [("StratA", "NKE.ETORO", "OK")]

    budgeted = sweep._family_n_from_studies(
        pairs, [early_stopped], tournament_cfg={"deflation_family_floor_mode": "budgeted"})
    assert budgeted["NKE.ETORO"] == 120

    attempted = sweep._family_n_from_studies(
        pairs, [early_stopped], tournament_cfg={"deflation_family_floor_mode": "attempted"})
    assert attempted["NKE.ETORO"] == 8


def test_missing_floor_mode_key_defaults_to_attempted():
    """Issue #814 — der Code-Fallback (kein Key in tournament_cfg) wanderte mit dem Default von
    'budgeted' auf 'attempted': 'budgeted' war eine Fehlspezifikation der Nullverteilung, kein
    rueckwaertskompatibler Legacy-Zustand, der erhalten bleiben musste."""
    early_stopped = _Study([_T(evaluated=True)] * 8, n_trials_budget=120)
    pairs = [("StratA", "NKE.ETORO", "OK")]
    family_n = sweep._family_n_from_studies(pairs, [early_stopped], tournament_cfg={})
    assert family_n["NKE.ETORO"] == 8
    assert sweep._family_n_from_studies(pairs, [early_stopped], tournament_cfg=None)["NKE.ETORO"] == 8


def test_budgeted_floor_never_lowers_n_family_below_actually_evaluated():
    """Ein VOLLSTAENDIG durchlaufener Study (evaluierte Trials >= Budget, z. B. Retry-Ueberschuss)
    wird durch den Floor NICHT nach unten gezogen — max(), nicht Ersetzung."""
    fully_run = _Study([_T(evaluated=True)] * 130, n_trials_budget=120)
    pairs = [("StratA", "ZS.ETORO", "OK")]
    family_n = sweep._family_n_from_studies(
        pairs, [fully_run], tournament_cfg={"deflation_family_floor_mode": "budgeted"})
    assert family_n["ZS.ETORO"] == 130


def test_synthetic_early_stop_no_longer_correlates_family_n_with_budget_execution():
    """Akzeptanzkriterium #784/2 (Spearman(n_family, Budgetausführung) ≈ 0 nach dem Fix): über eine
    synthetische Kohorte mit stark variierender Budgetausführung liefert 'budgeted' für JEDES Symbol
    dieselbe (geplante) Multiplizität — die Korrelation mit der tatsächlichen Ausführung ist damit
    strukturell null, nicht nur empirisch klein."""
    pairs = []
    studies = []
    n_trials_budget = 100
    for i, frac in enumerate([0.13, 0.30, 0.55, 0.80, 1.0]):
        n_evaluated = int(round(n_trials_budget * frac))
        symbol = f"SYM{i}.ETORO"
        pairs.append(("StratA", symbol, "OK"))
        studies.append(_Study([_T(evaluated=True)] * n_evaluated, n_trials_budget=n_trials_budget))
    family_n = sweep._family_n_from_studies(
        pairs, studies, tournament_cfg={"deflation_family_floor_mode": "budgeted"})
    # Alle Symbole erhalten dieselbe Multiplizität (das geplante Budget) — unabhängig von der
    # tatsächlichen Ausführungs-Fraktion.
    assert len(set(family_n.values())) == 1
    assert set(family_n.values()) == {n_trials_budget}


# ── Akzeptanzkriterium #784/3: jedes Symbol mit >=1 abgeschlossener Study hat n_family > 0 ───────
def test_symbol_with_zero_eligible_but_evaluated_trials_gets_nonzero_family_n():
    """Vor #784: 61 von 124 Symbolen im Katalog-Lauf hatten keinen einzigen eligiblen Trial ⇒
    deflation_n_family=0, die familienweite Korrektur fiel dort ersatzlos weg — ausgerechnet dort,
    wo am meisten gesucht wurde."""
    trials = [_T(evaluated=True, eligible=False) for _ in range(40)]  # kein einziger eligible
    pairs = [("SqueezeBreakoutStrategy", "ZS.ETORO", "OK")]
    studies = [_Study(trials)]
    family_n = sweep._family_n_from_studies(
        pairs, studies, tournament_cfg={"deflation_family_floor_mode": "attempted"})
    assert family_n["ZS.ETORO"] == 40
    assert family_n["ZS.ETORO"] > 0


# ── _family_period_returns_from_studies zieht dieselbe erweiterte Menge ───────────────────────────
def test_family_period_returns_uses_evaluated_not_eligible_trials():
    trials = [
        _T(evaluated=True, eligible=True, rets=[0.01, 0.02]),
        _T(evaluated=True, eligible=False, rets=[0.03, 0.04]),  # evaluiert, aber NICHT eligible
        _T(evaluated=False, eligible=False, rets=[0.99, 0.99]),  # nicht evaluiert ⇒ ausgeschlossen
    ]
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    studies = [_Study(trials)]
    family_returns = sweep._family_period_returns_from_studies(pairs, studies)
    assert family_returns["TSLA.ETORO"] == [[0.01, 0.02], [0.03, 0.04]]
