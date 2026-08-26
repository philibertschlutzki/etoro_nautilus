"""Issue #669 — Suchraum-Diagnose-Artefakt + deklarative (Symbol, Strategie)-Deaktivierungsliste.

Symptom: 7 von 10 Strategien tragen auf TSLA-1h NICHTS zur Familie bei — 0 evaluable (nie ≥
``oos_min_trades`` OOS-Round-Trips, ``STRUCTURAL_ALL_UNEVALUABLE``, #656) ODER 0 eligible bei
VOLLER Evaluierbarkeit (``ZERO_ELIGIBLE_PLATEAU``, #656). Der #656-Diagnose-/Early-Stop-Mechanismus
feuert korrekt, unterscheidet aber NICHT zwischen den beiden strukturell unterschiedlichen
Kollaps-Ursachen: ein Suchraum, der zu wenige Trades erzeugt (``spaces.py``-Bounds-Problem), und
ein Suchraum, der genug Trades, aber nie ein risikoadjustiert eligibles Ergebnis erzeugt
(Signal-Qualität, kein Bounds-Problem). Dieses Modul trennt beide Fälle explizit.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

# Issue #669/#769 — die moeglichen bindenden Ursachen. 'none' ⇒ kein Kollaps (mind. 1 eligible
# Trial). Issue #769 — 'signal_frequency' wurde in 'signal_absent' (parameterunabhaengig, echte
# Datengeometrie) und 'signal_sparse' (parameterabhaengig, tunebar) AUFGESPALTEN: die alte Kategorie
# warf beide Faelle zusammen (median_is_trades==0 UND median_is_trades==9 galten identisch als
# 'signal_frequency'), obwohl sie entgegengesetzte Handlungsempfehlungen verlangen (AGENTS.md
# Pitfall #229).
_BINDING_CAUSES = frozenset(
    {"signal_absent", "signal_sparse", "hold_duration", "signal_quality", "inference_unavailable",
     "none", "no_data"}
)


def resolve_ineligible_binding_cause(trials: list[dict], *, median_oos_trades: float | None,
                                     low_trade_threshold: float = 2.0,
                                     unmeasurable_fraction_threshold: float = 0.5) -> tuple[str, dict]:
    """Issue #926/#921 — die BINDENDE Ursache eines 0-eligible-Kollapses bei VOLLER Evaluierbarkeit
    (``n_evaluable == n_trials``), in der korrekten Priorität ermittelt. Root-Cause #926: die
    frühere Ableitung nahm stillschweigend an, dass die Eligibility-Prüfung STATTGEFUNDEN hat —
    es gab keinen dritten Zweig für "die Prüfung war nicht durchführbar" (dieselbe Missing-Data-
    Klasse wie #917, eine Ebene höher: eine nicht durchgeführte Messung wird als negatives
    Messergebnis interpretiert).

    Priorität:
      1. ``'inference_unavailable'`` — mehr als ``unmeasurable_fraction_threshold`` (Default 0.5)
         der evaluierten Trials trugen KEINE messbare Selektions-Teststatistik
         (``is_rejection_detail == 'REJECT_OOS_STATISTIC_UNAVAILABLE'``, #917). Weder Denylist
         noch Bounds-Override sind hier eine sinnvolle Konsequenz — die Diagnose selbst ist nicht
         durchführbar (siehe ``recommend_diagnosis_action``: ``action='none'``, keine Frist).
      2. ``'signal_sparse'`` (Issue #921) — ein Signal, das nicht/kaum auftritt
         (``median_oos_trades <= low_trade_threshold``, Default 2), ist KEINE Qualitäts-Messung,
         sondern ein Frequenz-Befund. Die Qualität eines Signals, das nicht auftritt, ist keine
         Messung (SqueezeBreakout-Referenzfall: 178 Trials, Median 1 OOS-Trade, fälschlich
         ``'signal_quality'``).
      3. ``'signal_quality'`` — der Rest: eine reale, gemessene Kohorte ohne eligiblen Trial.

    ``trials``: Liste von Dicts je EVALUIERTEM (``oos_evaluated=True``) Trial, mit mindestens
    ``is_rejection_detail``. Rückgabe ``(binding_cause, detail)`` — ``detail`` trägt die
    Rohzahlen für die Telemetrie (``ZERO_ELIGIBLE_PLATEAU``-Event)."""
    n_evaluated = len(trials)
    n_unmeasurable = sum(
        1 for t in trials if t.get("is_rejection_detail") == "REJECT_OOS_STATISTIC_UNAVAILABLE")
    frac_unmeasurable = (n_unmeasurable / n_evaluated) if n_evaluated else 0.0
    detail = {
        "n_ineligible_unmeasurable": n_unmeasurable,
        "frac_ineligible_unmeasurable": round(frac_unmeasurable, 4),
    }
    if n_evaluated > 0 and frac_unmeasurable > unmeasurable_fraction_threshold:
        return "inference_unavailable", detail
    if median_oos_trades is not None and median_oos_trades <= low_trade_threshold:
        return "signal_sparse", detail
    return "signal_quality", detail


# Issue #1045/#1194 — welche ``is_rejection_detail``-Gates eine FREQUENZ- (Bounds-Erweiterung ist
# ein plausibler Hebel) vs. eine QUALITAETS-Groesse (Bounds-Erweiterung ist ein No-Op — die Trades
# fanden bereits statt und wurden gemessen; Denylist/Budget-Deprioritisierung ist die einzig
# sinnvolle Konsequenz) messen. Nur ``REJECT_OOS_MIN_TRADES`` misst direkt die Trade-ANZAHL; jede
# andere OOS-Ablehnung (Issue-Referenz: ``oos_min_psr`` ⇒ Qualitaet, siehe Issue-Text) urteilt ueber
# eine STATISTISCHE Eigenschaft der bereits stattgefundenen Trades. ``REJECT_OOS_MICRO_SIZING``/
# ``REJECT_OOS_NOT_EVALUABLE``/``REJECT_OOS_OTHER`` bleiben ABSICHTLICH unklassifiziert (weder
# Frequenz- noch reine Qualitaetsaussage) — ``classify_rejection_detail_gate_type`` gibt fuer sie
# ``None`` zurueck statt zu raten (dieselbe Fail-open-Konvention wie ueberall in diesem Modul).
_FREQUENCY_REJECTION_DETAILS = frozenset({"REJECT_OOS_MIN_TRADES"})
_QUALITY_REJECTION_DETAILS = frozenset({
    "REJECT_OOS_MIN_TOTAL_RETURN", "REJECT_OOS_MIN_EXPECTANCY", "REJECT_OOS_MAX_DRAWDOWN",
    "REJECT_OOS_MIN_WIN_RATE", "REJECT_OOS_MIN_SORTINO", "REJECT_OOS_MIN_PROFIT_FACTOR",
    "REJECT_OOS_MIN_PSR", "REJECT_OOS_MIN_EXCESS_RETURN",
})
# Issue #1264 (GH #1134) — ``REJECT_OOS_MIN_ALPHA_TSTAT`` ist WEDER eine Trade-FREQUENZ-Aussage
# (wie ``REJECT_OOS_MIN_TRADES``) NOCH eine Aussage ueber die STICHPROBEN-Qualitaet einer bereits
# stattgefundenen Handelskohorte (wie ``REJECT_OOS_MAX_DRAWDOWN`` etc.) — ein Trial, das dieses Gate
# 100 % der Zeit verfehlt, hat ein STRUKTURELLES Problem mit der GATE-SCHWELLE selbst relativ zur
# erreichbaren Alpha-Signifikanz dieser Strategie/dieses Symbols (eine Config-Eigenschaft), nicht mit
# der Trade-Anzahl oder der gemessenen Performance-Verteilung. Root-Cause #1264-Symptom: 13 Studies,
# 100 % ``REJECT_OOS_MIN_ALPHA_TSTAT``, ``diagnosed_pairs`` leer, weil ``classify_rejection_detail_
# gate_type`` fuer diesen Code bislang ``None`` (unklassifiziert) lieferte.
_GATE_UNREACHABLE_REJECTION_DETAILS = frozenset({"REJECT_OOS_MIN_ALPHA_TSTAT"})


def classify_rejection_detail_gate_type(is_rejection_detail: str | None) -> str | None:
    """Issue #1045/#1194, erweitert #1264 (GH #1134) — ``'frequency'``/``'quality'``/``'gate'``/
    ``None`` (unklassifiziert) fuer einen ``is_rejection_detail``-Code (siehe
    ``run_optimization._OOS_REASON_PREFIX_MAP`` fuer die vollstaendige Enum). ``'gate'`` (neu,
    #1264) markiert ein Eligibility-GATE, dessen Schwelle strukturell (nicht paarspezifisch)
    unerreichbar sein kann — die Ursache liegt in der KONFIGURIERTEN Schwelle, nicht im (Strategie,
    Symbol)-Paar selbst (siehe ``diagnose_structural_zero_eligible_gate``s ``gate_unreachable``-
    Zweig: erzeugt bewusst KEINE Denylist-Empfehlung). Rein, deterministisch, kein I/O."""
    if is_rejection_detail in _FREQUENCY_REJECTION_DETAILS:
        return "frequency"
    if is_rejection_detail in _QUALITY_REJECTION_DETAILS:
        return "quality"
    if is_rejection_detail in _GATE_UNREACHABLE_REJECTION_DETAILS:
        return "gate"
    return None


def diagnose_structural_zero_eligible_gate(
    is_rejection_detail_counts: dict[str, int] | None, *, homogeneity_threshold: float = 1.0,
    stop_reason: str | None = None,
) -> dict:
    """Issue #1045/#1194 — Root-Cause: ``resolve_ineligible_binding_cause`` klassifiziert einen
    ZERO_ELIGIBLE-Kollaps zwar bereits korrekt als ``'signal_quality'`` (voll evaluierte Kohorte,
    kein Frequenz-/Inferenz-Problem), schliesst diesen Befund aber NICHT zu einer KONKRETEN
    Handlungsempfehlung (Bounds-Erweiterung vs. Denylist/Budget-Deprioritisierung), OBWOHL die
    Study bereits ``is_rejection_detail_counts`` traegt (100 % desselben Gates im Referenzfall:
    123/123 Trials ``REJECT_OOS_MIN_PSR``). Diese Funktion schliesst genau diese Luecke: schlaegt
    das GATE (nicht die Trade-Anzahl, siehe ``resolve_ineligible_binding_cause``) selbst 100 % der
    evaluierten Trials fehl, ist die Gate-Identitaet selbst die staerkste verfuegbare Diagnose —
    unabhaengig davon, ob der (langsamere, mehrstufigen Evidenz-/Cache-Regime unterliegende)
    ``run_optimization.py``-Rueckschriebpfad in DIESEM Lauf griff.

    ``homogeneity_threshold`` (Default 1.0, wie im Issue-Text — "100 % desselben
    is_rejection_detail") — der Anteil des HAEUFIGSTEN Gates an ALLEN gezaehlten Ablehnungen muss
    diese Schwelle ERREICHEN, sonst bleibt der Befund ``binding_cause='none'`` (ein gemischter
    Cohort ist von hier aus nicht eindeutig einem einzelnen Gate zuzuordnen — kein Rateversuch).

    Issue #1096/#1244 (P2, Katalog #1247+) — ``stop_reason='STRUCTURAL_ALL_UNEVALUABLE'`` (0
    EVALUABLE Trials, nicht nur 0 eligible) nimmt einen EIGENEN, VORGELAGERTEN Zweig: die vier
    ``is_rejection_detail``-Codes, die ein nie-OOS-evaluierter Trial tragen kann
    (``REJECT_OOS_WINDOW_UNREACHABLE``/``REJECT_OOS_INACTIVE``/``REJECT_OOS_DISCARDED_BY_IS_GATE``/
    ``REJECT_OOS_NOT_EVALUATED``, siehe ``run_optimization._classify_is_rejection_detail``) messen
    per Konstruktion NIEMALS eine Trade-QUALITAET (dafuer braucht es evaluierte Trades, die es hier
    per Definition nicht gibt) — dieselbe architektonische Garantie, die ``diagnose_trade_
    frequency``s ``n_evaluable==0``-Zweig bereits einhaelt (der dort NIEMALS ``'signal_quality'``
    zurueckgibt). Der Befund ist deshalb UNBEDINGT ``gate_type='frequency'``/``binding_cause=
    'signal_sparse'``/``proposed_action='search_space_override'`` — unabhaengig von der homogeneity_
    threshold-Pruefung unten (die ausschliesslich fuer die STRUCTURAL_ZERO_ELIGIBLE-Qualitaetsfrage
    gilt) und selbst bei LEEREN ``is_rejection_detail_counts`` (die vier Codes bleiben informativ in
    ``dominant_rejection_detail``, entscheiden aber nicht mehr ueber ``binding_cause``).

    Rueckgabe: ``{dominant_rejection_detail, dominant_fraction, gate_type, binding_cause,
    proposed_action}``. ``proposed_action`` ist ``'search_space_override'`` (Frequenz-Gate),
    ``'budget_deprioritization'`` (Qualitaets-Gate, MILDER als ``'denylist'`` — dieselbe
    Vorsichtsstufe, die #830 fuer 'signal_quality' bereits etabliert hat) oder ``'none'``
    (unklassifiziertes oder nicht-homogenes Gate). Rein, deterministisch, kein I/O — DER Vorschlag
    wird HIER nur FORMULIERT, nicht angewendet (siehe ``report._structural_zero_eligible_diagnosed_
    pairs``-Docstring fuer die Report-Verdrahtung; die Anwendung bleibt ein separater, bestaetigter
    Schritt, exakt wie im Issue-Text gefordert)."""
    counts = is_rejection_detail_counts or {}
    total = sum(counts.values())
    if stop_reason == "STRUCTURAL_ALL_UNEVALUABLE":
        dominant_detail, dominant_count = (
            max(counts.items(), key=lambda kv: kv[1]) if counts else (None, 0))
        return {
            "dominant_rejection_detail": dominant_detail,
            "dominant_fraction": round(dominant_count / total, 4) if total else None,
            "gate_type": "frequency", "binding_cause": "signal_sparse",
            "proposed_action": "search_space_override",
        }
    if total == 0:
        return {"dominant_rejection_detail": None, "dominant_fraction": None,
                "gate_type": None, "binding_cause": "none", "proposed_action": "none"}
    dominant_detail, dominant_count = max(counts.items(), key=lambda kv: kv[1])
    dominant_fraction = dominant_count / total
    if dominant_fraction < homogeneity_threshold:
        return {"dominant_rejection_detail": dominant_detail,
                "dominant_fraction": round(dominant_fraction, 4),
                "gate_type": None, "binding_cause": "none", "proposed_action": "none"}
    gate_type = classify_rejection_detail_gate_type(dominant_detail)
    if gate_type == "frequency":
        binding_cause, proposed_action = "signal_sparse", "search_space_override"
    elif gate_type == "quality":
        binding_cause, proposed_action = "signal_quality", "budget_deprioritization"
    elif gate_type == "gate":
        # Issue #1264 (GH #1134) Fix Punkt 2 — ``action='none'`` ist HIER bewusst KEIN "kein
        # Befund" (anders als der allgemeine ``binding_cause in (None, 'none')``-Fall unten): die
        # Ursache liegt in der KONFIGURIERTEN Gate-Schwelle (``tournament.json``), nicht im
        # (Strategie, Symbol)-Paar — eine Denylist/Bounds-Weitung fuer DIESES Paar waere hier keine
        # sinnvolle Konsequenz (ein anderes Paar mit derselben Config-Schwelle traefe dasselbe
        # strukturelle Limit). ``check_structural_zero_eligible_has_diagnosis`` akzeptiert
        # ``binding_cause='gate_unreachable'`` dennoch als vollwertigen Diagnose-Eintrag.
        binding_cause, proposed_action = "gate_unreachable", "none"
    else:
        binding_cause, proposed_action = "none", "none"
    return {
        "dominant_rejection_detail": dominant_detail,
        "dominant_fraction": round(dominant_fraction, 4),
        "gate_type": gate_type,
        "binding_cause": binding_cause,
        "proposed_action": proposed_action,
    }


def diagnose_trade_frequency(trials: list[dict], *, oos_min_trades: int) -> dict:
    """Bestimmt die BINDENDE Ursache eines 0-evaluable/0-eligible-Kollapses für ein
    (Symbol, Strategie)-Paar aus den per-Trial-Kennzahlen (``oos_evaluated``, ``oos_eligible``,
    ``oos_total_trades``, ``is_total_trades``, ``hit_trade_cap`` — bereits als Trial-User-Attrs
    gestempelt, run_optimization.make_symbol_objective).

    Trennt (Akzeptanzkriterium #669/#769):
      * ``'signal_absent'`` — 0 evaluable UND die IS-Aktivität ist über ALLE abgeschlossenen Trials
        NULL (``median_is_trades == 0`` UND ``max(is_total_trades) == 0``) ⇒ PARAMETERUNABHÄNGIG:
        die Strategie feuert im gesamten Suchraum nie (echte Datengeometrie-/Indikator-Degeneration).
        Ein constrained TPE kann das nicht lösen — Bounds-Kalibrierung ist hier ein No-Op.
      * ``'signal_sparse'`` — 0 evaluable UND ``0 < median_is_trades < oos_min_trades`` ODER
        ``max(is_total_trades) > 0`` (die Strategie feuert bei MANCHEN Parameterkombinationen,
        median bleibt aber unter der Schwelle) ⇒ PARAMETERABHÄNGIG/tunebar (Frequenz-Parameter wie
        ``cooldown_bars``/Signal-Schwellen) — ein constrained TPE ist genau dafür da, diese Richtung
        zu finden (Root-Cause #769: der alte Guard urteilte hier auf NULL modellierten Trials).
      * ``'hold_duration'`` — 0 evaluable, IS-Aktivität AUSREICHEND (``median_is_trades >=
        oos_min_trades``), aber die Mehrheit der Trials trifft die Haltedauer-/Trade-Cap-Grenze
        (``hit_trade_cap``) ⇒ Signale entstehen, überleben aber nicht bis zur Realisierung im
        OOS-Fenster (Haltedauer-Obergrenze zu lang/kurz falsch kalibriert) — ebenfalls tunebar.
      * ``'signal_quality'`` — ALLE Trials wurden evaluiert (echte OOS-Backtests, genug Trades),
        aber KEINER war je eligible ⇒ ein Suchraum-/Bounds-Problem ist NICHT die Ursache; die
        Strategie ist auf diesem Symbol/Tier strukturell nicht kanten-fähig (oder das Gate ist zu
        streng) — Bounds-Kalibrierung würde hier NICHTS beheben.
      * ``'none'`` — mindestens ein Trial war eligible (kein Kollaps).

    Rückgabe: ``{'n_trials', 'n_evaluable', 'n_eligible', 'median_oos_trades', 'median_is_trades',
    'max_is_trades', 'frac_hit_trade_cap', 'binding_cause'}``. Rein, deterministisch, kein I/O."""
    n = len(trials)
    if n == 0:
        return {
            "n_trials": 0, "n_evaluable": 0, "n_eligible": 0,
            "median_oos_trades": None, "median_is_trades": None, "max_is_trades": None,
            "frac_hit_trade_cap": None, "binding_cause": "no_data",
        }

    n_evaluable = sum(1 for t in trials if t.get("oos_evaluated"))
    n_eligible = sum(1 for t in trials if t.get("oos_eligible"))
    oos_trade_counts = [int(t.get("oos_total_trades") or 0) for t in trials]
    median_oos_trades = statistics.median(oos_trade_counts) if oos_trade_counts else 0
    frac_hit_cap = sum(1 for t in trials if t.get("hit_trade_cap")) / n

    if n_evaluable == 0:
        is_trade_counts = [int(t.get("is_total_trades") or 0) for t in trials]
        median_is_trades = statistics.median(is_trade_counts) if is_trade_counts else 0
        max_is_trades = max(is_trade_counts) if is_trade_counts else 0
        if median_is_trades < oos_min_trades:
            # Issue #769 — 'signal_absent' NUR, wenn die IS-Aktivität ÜBER JEDEN Trial null ist
            # (echte Datengeometrie); jede Restaktivität (max > 0) ist ein Beleg, dass MANCHE
            # Parameterkombinationen sehr wohl feuern ⇒ 'signal_sparse' (parameterabhängig).
            if median_is_trades == 0 and max_is_trades == 0:
                binding_cause = "signal_absent"
            else:
                binding_cause = "signal_sparse"
        elif frac_hit_cap > 0.5:
            binding_cause = "hold_duration"
        else:
            binding_cause = "signal_sparse"
        return {
            "n_trials": n, "n_evaluable": 0, "n_eligible": 0,
            "median_oos_trades": median_oos_trades, "median_is_trades": median_is_trades,
            "max_is_trades": max_is_trades,
            "frac_hit_trade_cap": frac_hit_cap, "binding_cause": binding_cause,
        }

    if n_eligible == 0:
        # Issue #926/#921 — 'signal_quality' war hier bislang UNBEDINGT vergeben, sobald alle
        # Trials evaluiert wurden. resolve_ineligible_binding_cause trennt jetzt den Fall "nicht
        # messbar" (#917) und den Fall "Signal tritt kaum auf" (#921) vom echten Qualitätsurteil.
        evaluated_trials = [t for t in trials if t.get("oos_evaluated")]
        binding_cause, _detail = resolve_ineligible_binding_cause(
            evaluated_trials, median_oos_trades=median_oos_trades)
        return {
            "n_trials": n, "n_evaluable": n_evaluable, "n_eligible": 0,
            "median_oos_trades": median_oos_trades, "median_is_trades": None,
            "frac_hit_trade_cap": frac_hit_cap, "binding_cause": binding_cause,
            **_detail,
        }

    return {
        "n_trials": n, "n_evaluable": n_evaluable, "n_eligible": n_eligible,
        "median_oos_trades": median_oos_trades, "median_is_trades": None,
        "frac_hit_trade_cap": frac_hit_cap, "binding_cause": "none",
    }


# Issue #761 — die frequenz-treibenden Parameter, deren Bounds bei einem ``'signal_frequency'``/
# ``'hold_duration'``-Kollaps ein plausibler Hebel sind (Cooldown/Haltedauer-Obergrenze und die
# Signal-Trägheits-Fenster der Trend-/Regime-Indikatoren). Nicht jede Strategie sampelt jeden
# dieser Parameter — ``propose_bounds_widening`` überspringt fehlende Keys stillschweigend.
_FREQUENCY_DRIVING_PARAMS = (
    "cooldown_bars", "max_bars_in_trade", "ema_period", "adx_period", "keltner_period",
)


def _widen_bounds_toward(direction_by_param: dict[str, str],
                         current_bounds: dict[str, tuple[float, float]], *,
                         widen_fraction: float) -> dict[str, list[float]]:
    """Issue #761/#763/#777/#1066 — gemeinsame Weitungs-Arithmetik: ``direction_by_param[param] ==
    "low"`` senkt die Untergrenze um ``widen_fraction`` der aktuellen Spannweite ab (Obergrenze
    bleibt), ``"high"`` hebt die Obergrenze an (Untergrenze bleibt). Parameter ohne bekannte Bounds
    werden übersprungen (defensiv).

    Issue #1066 (Pitfall #371) — JEDER Vorschlag wird nach der Weitung SYMMETRISCH gegen
    ``spaces._PARAM_DOMAIN_REGISTRY`` geklammert (vorher nur ``max_bars_in_trade`` und nur nach
    oben, siehe #777). Root-Cause #1066: ein Rückschrieb, der bei jedem Lauf um denselben Betrag
    weitet, erreicht ohne Klammer nach ``k`` Anwendungen ``lo₀ − k·widen_fraction·span₀`` —
    negative Perioden, negative Bar-Anzahlen, ein RSI-Schwellwert ausserhalb [0, 100]. Die Klammer
    macht jede EINZELNE Anwendung bereits konvergent gegen ``min_admissible``/``max_admissible``,
    unabhängig davon, wie oft sie wiederholt wird — es gibt keinen Divergenzpfad mehr."""
    from automation.optimizer.spaces import _MAX_BARS_IN_TRADE_CAP, clamp_param_bounds
    proposals: dict[str, list[float]] = {}
    for param, direction in direction_by_param.items():
        if param not in current_bounds:
            continue
        lo, hi = current_bounds[param]
        span = (hi - lo) or 1.0
        if direction == "low":
            new_lo, new_hi = round(lo - widen_fraction * span, 6), hi
        else:
            new_hi = round(hi + widen_fraction * span, 6)
            if param == "max_bars_in_trade":
                new_hi = min(new_hi, float(_MAX_BARS_IN_TRADE_CAP))
            new_lo, new_hi = lo, new_hi
        clamped_lo, clamped_hi = clamp_param_bounds(param, new_lo, new_hi)
        proposals[param] = [clamped_lo, clamped_hi]
    return proposals


def propose_bounds_from_boundary_hits(
    directions: dict[str, str], strategy: str, *,
    current_bounds: dict[str, tuple[float, float]] | None = None,
    widen_fraction: float = 0.3,
) -> dict[str, list[float]]:
    """Issue #763 — schliesst die Randlösungs-Diagnose (``run_optimization._boundary_hit_
    directions``) zu einem KONKRETEN Bounds-Vorschlag, analog ``propose_bounds_widening``, aber
    OHNE Korrelationsschätzung: klebt der Gewinner-Parameter an der UNTEREN Grenze
    (``directions[param] == "low"``), wird die Untergrenze um ``widen_fraction`` (Default 30 %) der
    aktuellen Spannweite abgesenkt; bei ``"high"`` wird die Obergrenze angehoben. Root-Cause #763:
    ein Winner, der zur Hälfte an Suchraumgrenzen klebt, könnte dort ein echtes Optimum ODER ein
    Suchraum-Artefakt gefunden haben — nur ein Re-Run mit ausgeweiteten Bounds unterscheidet die
    beiden Fälle; dieser Vorschlag ist die Voraussetzung dafür (schreibt in den #761-Cache).

    ``current_bounds`` Default: ``bounds.extract_numeric_bounds(strategy)``. Rückgabe:
    ``{param: [new_low, new_high]}`` — NUR für Parameter aus ``directions``, deren Bounds bekannt
    sind. Rein, deterministisch, kein I/O (ausser dem optionalen ``extract_numeric_bounds``-
    Lookup)."""
    if current_bounds is None:
        try:
            from automation.optimizer.bounds import extract_numeric_bounds
            current_bounds = extract_numeric_bounds(strategy)
        except Exception:
            current_bounds = {}
    return _widen_bounds_toward(directions, current_bounds, widen_fraction=widen_fraction)


def propose_bounds_widening(
    trial_rows: list[dict], strategy: str, *,
    current_bounds: dict[str, tuple[float, float]] | None = None,
    widen_fraction: float = 0.3, min_samples: int = 8, correlation_threshold: float = 0.3,
) -> dict[str, list[float]]:
    """Issue #761 — schliesst die #669-Diagnose zu einem KONKRETEN Bounds-Vorschlag: für jeden
    frequenz-treibenden Parameter (``_FREQUENCY_DRIVING_PARAMS``), den diese Strategie tatsächlich
    sampelt, wird die Spearman-Rangkorrelation zwischen dem gesampelten Parameterwert und
    ``is_total_trades`` (derselben Aktivitäts-Grösse, die ``diagnose_trade_frequency`` schon für die
    ``'signal_frequency'``-Klassifikation nutzt) über die Trial-Kohorte bestimmt.

    Eine hinreichend starke Korrelation (``|ρ| > correlation_threshold``, Default 0.3, über
    mindestens ``min_samples`` Trials mit definiertem Parameter+Aktivität) zeigt an, IN WELCHE
    RICHTUNG der Suchraum verengt ist: eine NEGATIVE Korrelation (grösserer Parameterwert ⇒ weniger
    Trades, z. B. längerer Cooldown) schlägt vor, die UNTERGRENZE abzusenken (Suchraum nach unten
    weiten); eine POSITIVE Korrelation schlägt vor, die OBERGRENZE anzuheben. Die Weitung selbst ist
    ``widen_fraction`` (Default 30 %) der aktuellen Spannweite — konservativ, kein Sprung ins
    Unbekannte.

    ``trial_rows``: Liste von ``{'params': {...}, 'is_total_trades': int}`` (aus den Trial-User-
    Attrs des STRUCTURAL_ALL_UNEVALUABLE-Kollaps-Cohorts, ``run_optimization.floor_plateau_
    callback``). ``current_bounds`` Default: ``bounds.extract_numeric_bounds(strategy)``.

    Rückgabe: ``{param: [new_low, new_high]}`` — NUR für Parameter mit einer hinreichend starken,
    hinreichend gestützten Korrelation. Leeres Dict, wenn kein Parameter qualifiziert (kein
    Bounds-Problem erkennbar, oder die Strategie sampelt keinen der Kandidaten-Parameter). Rein,
    deterministisch, kein I/O (ausser dem optionalen ``extract_numeric_bounds``-Lookup)."""
    if current_bounds is None:
        try:
            from automation.optimizer.bounds import extract_numeric_bounds
            current_bounds = extract_numeric_bounds(strategy)
        except Exception:
            current_bounds = {}

    from automation.optimizer.reward import _spearman_rank_correlation

    direction_by_param: dict[str, str] = {}
    for param in _FREQUENCY_DRIVING_PARAMS:
        if param not in current_bounds:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for row in trial_rows:
            params = row.get("params") or {}
            trades = row.get("is_total_trades")
            if param not in params or trades is None:
                continue
            try:
                xs.append(float(params[param]))
                ys.append(float(trades))
            except (TypeError, ValueError):
                continue
        if len(xs) < min_samples:
            continue
        rho = _spearman_rank_correlation(xs, ys)
        if rho is None or abs(rho) < correlation_threshold:
            continue
        direction_by_param[param] = "low" if rho < 0 else "high"
    return _widen_bounds_toward(direction_by_param, current_bounds, widen_fraction=widen_fraction)


def eligibility_curve(trials: list[dict], *, window: int = 16) -> list[float]:
    """Issue #700 — die per-Trial ``oos_eligible``-Fraktion je kontiguierlichem Fenster von
    ``window`` Trials (in Trial-Reihenfolge), NICHT nur die aggregierte Gesamtzahl. Unterscheidet
    TRANSIENTE Null-Eligibilität (irgendwo zwischenzeitlich eligible Trials, die spaeter — z. B.
    durch eine spaetere confirm-Filterung — nicht mehr im finalen Cohort auftauchen) von
    PERMANENTER Null-Eligibilität (jedes Fenster zeigt 0.0) — genau die im #700-Symptom offene
    Frage: "reproduziert der Squeeze-Fall einen sauberen 16-Trial-Stop, oder weist die Telemetrie
    transiente eligible Trials aus (dann ist es keine strukturelle Ursache, sondern eine spaetere
    Filterung, die selbst zu erklaeren ist)". Das letzte Fenster darf kuerzer als ``window`` sein.

    Rein, deterministisch, kein I/O. Rückgabe: Liste der Fensteranteile ``eligible/n`` (``[]`` bei
    leerer ``trials``-Liste)."""
    fractions = []
    for i in range(0, len(trials), window):
        chunk = trials[i:i + window]
        n_eligible = sum(1 for t in chunk if t.get("oos_eligible") is True)
        fractions.append(n_eligible / len(chunk))
    return fractions


def _strategy_supports_search_space_override(strategy: str) -> bool:
    """Issue #831 Fix Punkt 2 — ERSETZT die vorherige ``WIRED_OVERRIDE_STRATEGIES``-Allowlist (eine
    seit #681 EINGEFRORENE, handgepflegte 3-Strategien-Teilmenge — ``{'TrendPullbackStrategy',
    'AdxAtrMomentumStrategy', 'HourlyMeanReversionStrategy'}`` von 14 aktiven Strategien).

    Root-Cause #831: ``spaces._bounds_for`` liest den ``search_space_overrides.json``-/#761-Auto-
    Cache-Override-Pfad UNIVERSELL für JEDEN numerischen ``suggest_*``-Aufruf JEDER Strategie — die
    Allowlist war eine künstliche, nie nachgezogene Einschränkung derselben bereits universellen
    Mechanik (dieselbe Fehlerklasse wie das eingefrorene ``reward._GATE_COLLINEARITY_KEYS``-Tupel
    aus #760: eine Strategie-Erweiterung von 10 auf 14 zog die Allowlist nie mit). Eine Strategie
    ist override-fähig GENAU DANN, wenn ``bounds.extract_numeric_bounds(strategy)`` einen NICHT-
    LEEREN Bereich liefert — das ruft ``spaces.sample_params`` tatsächlich auf und beweist damit
    beide Vorbedingungen zugleich (der Override-Pfad wird gelesen UND es gibt mindestens einen
    numerischen Parameter, den eine Weitung überhaupt betreffen könnte). ``False`` für eine
    unbekannte Strategie (propagiertes ``ValueError``, defensiv abgefangen)."""
    try:
        from automation.optimizer import bounds as _bounds
        return bool(_bounds.extract_numeric_bounds(strategy))
    except Exception:
        return False


def has_existing_search_space_override(strategy: str, symbol: str, base_cfg: Path | None = None) -> bool:
    """Issue #681 — prüft, ob ``search_space_overrides.json`` bereits einen (nicht-leeren)
    Bounds-Override für ``(strategy, symbol)`` enthält (irgendein Parameter genügt). Fehlt die
    Datei/der Eintrag ⇒ ``False`` (bit-identisch zum Pre-#681-Verhalten, kein Override vorhanden)."""
    if base_cfg is None:
        from automation.optimizer.trial_config import config_dir
        base_cfg = config_dir()
    path = base_cfg / "search_space_overrides.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return False
    entry = (data.get("overrides", {}) or {}).get(strategy, {}).get(symbol)
    return bool(entry)


# Issue #830 Fix Punkt 3 — die Ablauf-Frist (record_diagnosed_pair/is_diagnosed_pair_expired) je
# ``binding_cause`` GETRENNT: ``'signal_absent'`` ist ein strukturelles, dauerhaftes Urteil (echte
# Datengeometrie) und behält die alte Frist (10 Läufe); ``'signal_quality'`` ist häufiger ein
# REGIME-Urteil (siehe #830-Root-Cause) und verfällt schneller (3 Läufe) — ein Re-Test lohnt sich
# hier eher, weil sich das Marktregime zwischen Läufen ändern kann. Fehlender Eintrag ⇒
# ``_DEFAULT_EXPIRES_AFTER_RUNS`` (unveränderte Rückfalllösung für alle übrigen Ursachen).
_EXPIRES_AFTER_RUNS_BY_CAUSE = {"signal_absent": 10, "signal_quality": 3}


def recommend_diagnosis_action(strategy: str, symbol: str, diagnosis: dict, *,
                               has_existing_override: bool = False,
                               previously_recommended_override: bool = False,
                               proposed_bounds: dict | None = None,
                               budget_executed_fraction: float | None = None,
                               n_runs_confirmed: int = 0,
                               stop_reason: str | None = None,
                               max_consecutive_structural_runs: int = 2,
                               simulation_semantics_version: int | None = None,
                               blocking_invariants_failing: bool | None = None) -> dict:
    """Issue #681 — schliesst die #669-Diagnose zu einer KONKRETEN Aktions-Empfehlung: die
    Diagnose (``diagnose_trade_frequency``) feuert bereits (STRUCTURAL_ALL_UNEVALUABLE /
    ZERO_ELIGIBLE_PLATEAU), schreibt aber nicht zurück — dieselben strukturell toten Paare werden
    bei JEDEM Lauf neu enumeriert (Root-Cause #681).

    Fallunterscheidung nach ``binding_cause`` (#669/#769):
      * ``'signal_quality'`` (ALLE Trials evaluiert, 0 eligible) — Bounds-Kalibrierung hilft NICHT
        (kein Frequenz-, ein Qualitätsproblem). SEIT #830 GESTUFT statt unbedingt: volle Evidenz
        (``n_runs_confirmed >= 2`` UND ``budget_executed_fraction >= 0.9``) ⇒ ``'denylist'``;
        mindestens EINE Bestätigung, aber (noch) nicht volle Evidenz ⇒ ``'deprioritized'``
        (reduziertes statt volles Budget, Suchraum bleibt erreichbar); keine Bestätigung
        (``n_runs_confirmed == 0``) ⇒ ``'none'``.
      * ``'signal_absent'`` (0 evaluable, IS-Aktivität über JEDEN Trial null) — PARAMETERUNABHÄNGIG:
        kein Override kann eine Strategie zum Feuern bringen, die im gesamten Suchraum nie feuert
        ⇒ ``'denylist'`` (Bounds-Kalibrierung wäre hier ein No-Op, #769).
      * ``'signal_sparse'``/``'hold_duration'`` (0 evaluable, PARAMETERABHÄNGIG) UND die Strategie
        ist override-fähig (``_strategy_supports_search_space_override`` — Issue #831, ABGELEITET
        aus ``bounds.extract_numeric_bounds``, nicht mehr eine eingefrorene Allowlist) UND es
        existiert noch KEIN Override für dieses Paar ⇒ ``'search_space_override'`` (Bounds-
        Kalibrierung probieren, BEVOR das Paar aufgegeben wird). Existiert bereits ein Override
        (Bounds-Kalibrierung wurde schon versucht) UND das Paar ist TROTZDEM tot ⇒ Eskalation auf
        ``'denylist'``.
      * Frequenzproblem bei einer NICHT verdrahteten Strategie ⇒ ``'denylist'`` (ein Override hätte
        ohnehin keine Wirkung, spaces.py._bounds_for ist fail-open no-op für sie).
      * ``'none'``/``'no_data'`` ⇒ ``'none'`` (kein Kollaps, nichts zu tun).

    Issue #699 — ``previously_recommended_override`` schliesst die verbleibende Lücke der
    #681-Closed-Loop: eine ``'search_space_override'``-Empfehlung OHNE Eskalationspfad wiederholt
    sich bei JEDEM Lauf identisch, solange niemand tatsächlich einen Override in
    ``search_space_overrides.json`` einträgt — genau das #699-Symptom ("jeder Lauf verbrennt
    3 × 16 Trials für dieselben nicht-viablen Paare"), weil NUR ``'denylist'``-Empfehlungen den
    Budget-Skip in ``enumerate_tunable_pairs`` auslösen. Wurde für dasselbe (``strategy``,
    ``symbol``)-Paar in einem VORHERIGEN Lauf bereits ``'search_space_override'`` empfohlen (aus dem
    ``diagnosed_pairs_cache``, vom Aufrufer geprüft) UND existiert weiterhin KEIN Override UND das
    Paar scheitert WEITERHIN an derselben Frequenz-/Haltedauer-Ursache, wird die Empfehlung dieses
    Mal auf ``'denylist'`` eskaliert — die Override-Chance wird genau EINMAL gewährt, dann greift
    der Budget-Skip. Default ``False`` ⇒ bit-identisch zum Pre-#699-Verhalten.

    Issue #761 — ``proposed_bounds`` (aus ``propose_bounds_widening``, vom Aufrufer berechnet) wird
    NUR bei ``action == 'search_space_override'`` in die Empfehlung übernommen — ein konkreter,
    verengter/geweiteter Bereich statt nur der Empfehlung "probiere einen Override". ``sweep.py``
    wendet ihn im NÄCHSTEN Lauf automatisch an (``SEARCH_SPACE_AUTO_OVERRIDE``-Telemetrie), bis ein
    Operator den kuratierten Override in ``search_space_overrides.json`` einträgt.

    Issue #778 — die 'denylist'-Eskalation für ``'signal_absent'`` (die PARAMETERUNABHÄNGIGE #769-
    Ursache) erfordert jetzt ZUSÄTZLICH AUSREICHENDE EVIDENZ: ``budget_executed_fraction >= 0.9``
    (der Vorlauf muss überwiegend ausgeführt worden sein, #770) UND ``n_runs_confirmed >= 2`` (die
    Ursache muss sich über mindestens zwei Läufe bestätigt haben). Root-Cause #778: AdxAtrMomentum
    (116/124 Symbole) und TrendPullback (100/124) wären sonst NACH NUR 16 ZUFALLSZIEHUNGEN (13 %
    Budgetausführung, #769) dauerhaft deaktiviert worden — eine verfrühte Evidenzbasis für eine
    schwer reversible Entscheidung. Fehlende Evidenz (``budget_executed_fraction=None`` oder
    ``n_runs_confirmed=0``, der Default für Legacy-/Test-Aufrufer) ⇒ ``'none'`` (bit-identisch zu
    "noch nichts entschieden"), NIE mehr direkt 'denylist'.

    Issue #829 — ``budget_executed_fraction >= 0.9`` ist für ``'signal_absent'`` STRUKTURELL NIE
    erreichbar: ``run_optimization.py`` stoppt eine Study mit ``STRUCTURAL_ALL_UNEVALUABLE`` (#805)
    bereits bei ``n_startup_trials + structural_min_modelled_trials`` — für AdxAtrMomentum/
    TrendPullback beobachtet bei 28 %/46 % des Budgets. #778s Schwelle und #805s früher Abbruch
    blockierten sich damit gegenseitig: die Ursache, die den Mechanismus ausgelöst hat, konnte die
    Schwelle NIE erreichen (Pitfall #258, dieselbe Fehlerklasse wie #754 eine Ebene weiter). Root-
    Cause-Korrektur: ``sufficient_evidence`` zielt jetzt auf das GEMESSENE ERGEBNIS statt auf den
    Budgetanteil — ``stop_reason == 'STRUCTURAL_ALL_UNEVALUABLE'`` ALLEIN ist bereits die
    aussagekräftigere Evidenz (dieser Wert wird von ``compute_budget_execution`` NUR gesetzt,
    NACHDEM die Study ihre eigene ``len(completed) >= n_startup_trials +
    structural_min_modelled_trials``-Vorbedingung bereits erfüllt hat — ``budget_executed_fraction``
    bleibt als ALTERNATIVER, weiterhin gültiger Nachweispfad erhalten (z. B. für eine Study, die aus
    einem anderen Grund bis zum vollen Budget lief). Fehlendes ``stop_reason`` (Default ``None``,
    Legacy-/Test-Aufrufer) ⇒ bit-identisch zum Pre-#829-Verhalten (nur der Budgetanteil zählt).

    Issue #778 — ``'signal_sparse'``/``'hold_duration'`` (PARAMETERABHÄNGIG) eskaliert seither NIE
    mehr auf 'denylist' — die frühere ``previously_recommended_override``-Eskalation (#699) hätte
    genau dieselbe verfrühte Deaktivierung auf Basis einer einzigen, mutmasslich budget-limitierten
    Suche riskiert. Bleibt ein Override-Versuch wirkungslos, bleibt das Paar in Rotation (``'none'``)
    statt endgültig ausgeschlossen zu werden — Bounds-Kalibrierung ist der einzige Hebel.

    Issue #957 (Katalog D, Pitfall #292-Durchsetzung) — ``blocking_invariants_failing``
    generalisiert die #911-Aussetzung: #911 setzte 'signal_quality' UNBEDINGT auf
    ``'quarantined_pending_simulation_review'`` (nie 'denylist'), speziell wegen der #897-Episode.
    Das schützt korrekt, verhindert aber AUCH nach einem verifiziert sauberen Lauf jemals wieder
    einen echten 'denylist'-Rückschrieb ('signal_quality' bedeutet 'das Signal ist schlecht' — eine
    Aussage, die nur zulässig ist, wenn die Simulationsschicht selbst gültig ist, Pitfall #292).
    ``blocking_invariants_failing`` ist der fehlende Schalter dafür:
      * ``None`` (Default, Legacy-/Test-Aufrufer) ⇒ bit-identisches #911-Verhalten (immer
        quarantänisieren, NIE denylisten) — sicherer Default, solange kein Aufrufer den aktuellen
        Invarianten-Status tatsächlich kennt.
      * ``True`` (mindestens eine blockierende Invariante FAILt in DIESEM Lauf) ⇒ ``binding_cause``
        wird auf ``'simulation_invalid'`` UMGESCHRIEBEN (statt 'signal_quality' zu behaupten) und
        ``action`` bleibt ``'none'`` — kein Rückschrieb, solange die Simulation nachweislich defekt
        ist.
      * ``False`` (explizit verifiziert: KEINE blockierende Invariante FAILt) ⇒ die #911-Aussetzung
        entfällt: bei ausreichender Evidenz wird tatsächlich ``'denylist'`` empfohlen (nicht mehr
        nur quarantänisiert) — die Simulationsschicht ist nachweislich gültig, 'signal_quality' ist
        dann eine zulässige Aussage.

    Rein, deterministisch, kein I/O. Rückgabe: ``{'strategy', 'symbol', 'action', 'binding_cause',
    'median_oos_trades', 'median_is_trades', 'proposed_bounds'}``."""
    cause = diagnosis.get("binding_cause")
    # Issue #829/#911 — zwei UNABHAENGIGE Nachweispfade fuer "das Ergebnis ist verlaesslich
    # vollstaendig ausgewertet": entweder der eigene strukturelle Stop-Grund der Study (STRUCTURAL_
    # ALL_UNEVALUABLE, siehe Docstring) ODER ein hoher Budgetanteil. n_runs_confirmed bleibt in
    # JEDEM Fall Pflicht (die Mehrlauf-Bestaetigung ist der eigentliche Schutz, #778) — die
    # Schwelle ist seit #911 KONFIGURIERBAR (``max_consecutive_structural_runs``, Default 2, siehe
    # ``optimizer.json``) statt eines eingefrorenen Literals. Gilt AUSSCHLIESSLICH fuer
    # ``signal_absent``/``signal_sparse`` — dort, wo die Diagnose von der Simulationsqualitaet
    # unabhaengig ist (reine Suchraum-/Datengeometrie-Frage).
    sufficient_evidence = n_runs_confirmed >= max_consecutive_structural_runs and (
        stop_reason == "STRUCTURAL_ALL_UNEVALUABLE"
        or (budget_executed_fraction is not None and budget_executed_fraction >= 0.9)
    )
    if cause in ("none", "no_data", None):
        action = "none"
    elif cause == "inference_unavailable":
        # Issue #926 — KEINE Konsequenz: weder Denylist noch Bounds-Override sind sinnvoll, wenn
        # die Eligibility-Prüfung selbst nicht durchführbar war (#913/#917). Der Zustand ist eine
        # Aussage über die Simulations-/Inferenz-Schicht, keine über die Strategie — er darf den
        # 'signal_quality'-Strike-Zähler NICHT erhöhen (dieser Cause ist von 'signal_quality'
        # disjunkt, die n_runs_confirmed-Kontinuität des Aufrufers bricht daher automatisch ab).
        action = "none"
    elif cause == "signal_quality":
        # Issue #830 — unterliegt seit diesem Fix demselben Evidenzregime wie 'signal_absent'
        # (#829/#778): volle Deaktivierung ('denylist') NUR nach n_runs_confirmed >= 2 UND
        # budget_executed_fraction >= 0.9 (bewusst OHNE den #829-stop_reason-Kurzschluss — dieser
        # gilt spezifisch der STRUCTURAL_ALL_UNEVALUABLE-Semantik von 'signal_absent'; hier ist der
        # Budgetanteil die richtige, tatsaechlich erreichbare Messgroesse, da diese Studies fast
        # immer das volle Budget ausfuehren, #830-Symptom). Root-Cause #830: vorher deaktivierte
        # EINE einzige Beobachtung (n=1) das Paar unbedingt fuer 10 Laeufe — ein Typ-II-Verstaerker,
        # der bevorzugt die Paare entfernt, deren Nicht-Ergebnis regimebedingt war. Statt direkt
        # 'none' bei fehlender Evidenz: ab der ERSTEN Bestaetigung (n_runs_confirmed >= 1)
        # 'deprioritized' — der Suchraum bleibt erreichbar (reduziertes statt volles Budget, siehe
        # run_optimization._apply_deprioritized_budget), statt entweder jeden Lauf voll zu suchen
        # oder nach einer einzigen Beobachtung komplett zu verschwinden. n_runs_confirmed == 0 (kein
        # Vorlauf-Eintrag) bleibt 'none' — bit-identisch zu "noch nichts entschieden".
        quality_sufficient_evidence = (
            n_runs_confirmed >= max_consecutive_structural_runs
            and budget_executed_fraction is not None and budget_executed_fraction >= 0.9
        )
        if blocking_invariants_failing:
            # Issue #957 — 'signal_quality' ist keine zulaessige Aussage, solange die
            # Simulationsschicht selbst nachweislich defekt ist (Pitfall #292): die Klassifikation
            # wird auf 'simulation_invalid' umgeschrieben, KEIN Rueckschrieb (weder denylist noch
            # deprioritized) unabhaengig von der Evidenzlage.
            cause = "simulation_invalid"
            action = "none"
        elif quality_sufficient_evidence:
            if blocking_invariants_failing is False:
                # Issue #957 — die #911-Aussetzung gilt NICHT mehr, wenn der Aufrufer den aktuellen
                # Invarianten-Status tatsaechlich verifiziert hat (keine blockierende Invariante
                # FAILt): 'signal_quality' ist dann eine zulaessige Aussage, der volle Rueckschrieb
                # ('denylist') greift wie vor #911.
                action = "denylist"
            else:
                # Issue #911 (wichtigste Aussage des Katalogs) — der Closed-Loop-Rueckschrieb fuer
                # 'signal_quality' wird bis nach dem #897-Kalibrierlauf AUSGESETZT: eine Strategie,
                # deren Trades zu 94% durch die #897-Sperrklinke nahe Breakeven beendet werden, kann
                # KEINEN eligiblen Trial erzeugen, egal wie der Suchraum aussieht — ein Denylist-
                # Rueckschrieb auf dieser Basis wuerde funktionierende Strategien dauerhaft
                # ausschliessen, weil die Simulationsschicht defekt war. 'quarantined_pending_
                # simulation_review' PROTOKOLLIERT das Paar (Reihenfolge/Evidenz bleiben sichtbar),
                # schreibt es aber NICHT auf die Denylist — enumerate_tunable_pairs skippt
                # ausschliesslich action=='denylist', ein quarantaenierter Eintrag bleibt regulaer
                # enumeriert, solange simulation_semantics_version seit der Diagnose NICHT gebumpt
                # wurde (siehe diagnosed_simulation_semantics_version unten). Default
                # (``blocking_invariants_failing is None``) — kein Aufrufer hat den aktuellen
                # Invarianten-Status verifiziert, also bleibt die sichere Annahme in Kraft.
                action = "quarantined_pending_simulation_review"
        elif n_runs_confirmed >= 1:
            action = "deprioritized"
        else:
            action = "none"
    elif cause == "signal_absent":
        action = "denylist" if sufficient_evidence else "none"
    elif cause in ("signal_sparse", "hold_duration"):
        if _strategy_supports_search_space_override(strategy) and not has_existing_override:
            action = "search_space_override"
        else:
            action = "none"
    elif cause == "search_stagnation":
        # Issue #1069/#1219 (P2, Katalog #1196-1221) — Suchstagnation (``invariants.check_search_
        # made_progress``-FAIL: constraint_improvement_rate <= 0 UND p_eligible == 0 trotz
        # ausreichend modellierter Trials; ODER stop_reason == 'STRUCTURAL_ZERO_ELIGIBLE' ohne
        # jede diagnosed_pairs-Zuordnung) wird EIGENSTAENDIG eskaliert, mit einer vom Issue
        # EXPLIZIT benannten, von den uebrigen Ursachen UNABHAENGIGEN Schwelle (2 dann 4 Laeufe,
        # nicht die konfigurierbare ``max_consecutive_structural_runs`` der signal_quality/
        # signal_absent-Faelle). Root-Cause #1219: "Diagnose ohne Konsequenz" (#1194 unveraendert)
        # — dieser Zweig ist die erste tatsaechliche Konsequenz.
        #
        # Schwelle bewusst >= 1 (nicht >= 2) fuer 'deprioritized': ``n_runs_confirmed`` zaehlt
        # Bestaetigungen VOR diesem Lauf (0 beim allerersten Sichten) — >= 1 als Eingabe bedeutet
        # bereits "dies ist der ZWEITE aufeinanderfolgende Lauf mit demselben Befund", erfuellt
        # also "nach zwei Laeufen" bereits beim Uebergang. Zusaetzlich (Pitfall-Klasse): sobald der
        # Uebergang 'none' -> 'deprioritized' feuert, setzt ``record_diagnosed_pair`` (gleiche
        # Aktion+Ursache wie zuvor? nein -> neue Serie) den persistierten ``n_runs_confirmed`` auf 1
        # zurueck (Serienneustart bei jedem Aktionswechsel, siehe dortiger Docstring) — eine Schwelle
        # von >= 2 fuer 'deprioritized' wuerde direkt NACH dem Erreichen durch genau diesen Reset
        # wieder unter die Schwelle fallen und in einer 'none'/'deprioritized'-Oszillation ohne
        # jemals 'denylist' zu erreichen enden. Die Schwelle >= 1 ist deckungsgleich mit der
        # Reset-Landestelle (1) und daher selbstverstaerkend (dieselbe Konstruktion wie der
        # 'signal_quality'-Zweig oben, ``elif n_runs_confirmed >= 1: action = 'deprioritized'``).
        if n_runs_confirmed >= 4:
            action = "denylist"
        elif n_runs_confirmed >= 1:
            action = "deprioritized"
        else:
            action = "none"
    else:
        action = "none"
    result = {
        "strategy": strategy, "symbol": symbol, "action": action, "binding_cause": cause,
        "median_oos_trades": diagnosis.get("median_oos_trades"),
        "median_is_trades": diagnosis.get("median_is_trades"),
        "budget_executed_fraction": budget_executed_fraction,
        "n_runs_confirmed": n_runs_confirmed,
        # Issue #829 — welcher der beiden Nachweispfade zur Empfehlung fuehrte, im Report/Cache
        # nachvollziehbar (nicht nur die binaere sufficient_evidence-Entscheidung).
        "stop_reason": stop_reason,
        # Issue #830 Fix Punkt 3 — je binding_cause unterschiedliche Ablauf-Frist (siehe
        # _EXPIRES_AFTER_RUNS_BY_CAUSE); record_diagnosed_pair uebernimmt diesen Wert (setdefault
        # greift nur, wenn er fehlt — Legacy-/Test-Aufrufer ohne diese Zeile bleiben unveraendert
        # beim alten globalen Default).
        "expires_after_runs": _EXPIRES_AFTER_RUNS_BY_CAUSE.get(cause, _DEFAULT_EXPIRES_AFTER_RUNS),
    }
    if action == "search_space_override" and proposed_bounds:
        result["proposed_bounds"] = proposed_bounds
    if cause == "simulation_invalid":
        # Issue #957 — auditierbar, DASS und WARUM ein Rueckschrieb unterdrueckt wurde (statt nur
        # stillschweigend action='none' zu liefern, ununterscheidbar von "keine Evidenz").
        result["writeback_suppressed_reason"] = "blocking_invariant"
    if action == "quarantined_pending_simulation_review":
        # Issue #911 Fix 2 — Gueltigkeitsstempel: ein Closed-Loop-Rueckschrieb ist nur solange
        # gueltig, wie die Simulationsschicht, die ihn erzeugt hat, gueltig ist (Pitfall #292 in
        # AGENTS.md). Ein kuenftiger Aufrufer vergleicht diesen Wert gegen die AKTUELLE
        # simulation_semantics_version und verwirft die Quarantaene bei einem Bump dazwischen.
        result["diagnosed_simulation_semantics_version"] = simulation_semantics_version
    return result


_DEFAULT_EXPIRES_AFTER_RUNS = 10

# Issue #1066 Fix Punkt 3 — Deckel für die Anzahl aufeinanderfolgender Weitungs-Übernahmen je
# (Strategie, Symbol, Parameter), unabhängig von der Domänenklammer (siehe record_diagnosed_pair-
# Docstring).
_MAX_WIDEN_APPLICATIONS = 2


def _read_diagnostic_writeback_enabled() -> bool:
    """Issue #926 Fix 1 — ``optimizer.json['diagnostic_writeback_enabled']`` (Fallback ``True``,
    wenn KEINE ``optimizer.json`` gefunden wird — Legacy-/Unit-Test-Aufrufer ohne Config-Fixture
    bleiben bit-identisch zum Pre-#1090-Verhalten). Sofortmassnahme-Schalter für die
    Reparaturphase: auf ``False`` gestellt, unterdrückt ``record_diagnosed_pair`` jeden
    Rückschrieb.

    Issue #1090 (Katalog #923) Fix Punkt 2 — die AUSGELIEFERTE ``automation/config/optimizer.json``
    setzt diesen Key jetzt EXPLIZIT auf ``false`` (war ``true``): Symptom #1090 — unter drei
    gleichzeitigen Sweep-Prozessen auf demselben ``{WORK}``-Store (#1086) wurden
    ``SqueezeBreakout/ASML``/``AdxAtrMomentum/ASML`` faelschlich auf
    ``action='deprioritized'``/``n_runs_confirmed=3`` geschrieben, OHNE dass ein zusaetzlicher
    realer ASML-Lauf stattfand; ein Paar erhielt ausserdem einen ``search_space_override``-
    Eintrag mit ``binding_cause='boundary_solution'``, OBWOHL der #1066-Sperrvermerk den
    Suchraum-Rueckschrieb bis zur Bounds-Klammer aussetzen sollte (zurueckgenommen, siehe
    ``diagnosed_pairs_cache.json``). Der PRODUKTIONS-Rückschrieb bleibt damit AUS, bis #1066
    (Bounds-Klammer) UND #1086 (Kohorten-Isolation) an einem echten Mehrprozess-Lauf abgenommen
    sind — ein Operator, der ihn frueher braucht, setzt den Key explizit auf ``true`` (Runbook
    #936). Der Python-Fallback bleibt bewusst ``True``, statt eine zweite Kopie desselben
    Sicherheits-Defaults im Code zu pflegen — die AUSGELIEFERTE Config ist die einzige Quelle für
    das Produktionsverhalten."""
    try:
        from automation.optimizer.trial_config import config_dir
        cfg_path = config_dir() / "optimizer.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text("utf-8")) or {}
            return bool(data.get("diagnostic_writeback_enabled", True))
    except Exception:
        pass
    return True


def _diagnosed_pairs_cache_path(work_dir: Path | None = None) -> Path:
    if work_dir is None:
        from automation.optimizer.manifest import WORK
        work_dir = WORK
    # Issue #761 — unter data/optimizer/diagnostics/ (NICHT direkt unter WORK): der Cache ist
    # orthogonal zur reward_semantics_version-Purge-Grenze (purge_stale_studies.py leert
    # WORK/sweep/*.db + WORK/<study_name>/trial_*/, beides study-namens-adressiert) und soll auch
    # einen manuellen "WORK-Verzeichnis leeren"-Reflex überleben — die Diagnose selbst bleibt
    # gültig, unabhängig davon, ob Studies purged wurden (#733/#701-Anschluss).
    return Path(work_dir) / "diagnostics" / "diagnosed_pairs_cache.json"


def load_diagnosed_pairs_cache(work_dir: Path | None = None) -> dict[tuple[str, str], dict]:
    """Issue #681/#761 — der AUTOMATISCH gepflegte Diagnose-Cache (``data/optimizer/diagnostics/
    diagnosed_pairs_cache.json``) — bewusst GETRENNT von der menschlich-kuratierten
    ``symbol_strategy_denylist.json``: dieser Cache schliesst NUR die Budget-Schleife
    (``enumerate_tunable_pairs`` überspringt ein ``'denylist'``-empfohlenes Paar automatisch ab dem
    NÄCHSTEN Lauf), ändert aber NIE die versionierte, PR-gebundene Config-Denylist selbst. Fehlt die
    Datei ⇒ ``{}`` (bit-identisch zum Pre-#681-Verhalten)."""
    path = _diagnosed_pairs_cache_path(work_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return {}
    out: dict[tuple[str, str], dict] = {}
    for entry in data.get("pairs", []) or []:
        strat, sym = entry.get("strategy"), entry.get("symbol")
        if strat and sym:
            out[(strat, sym)] = entry
    return out


def _save_diagnosed_pairs_cache(cache: dict[tuple[str, str], dict], *,
                                work_dir: Path | None = None) -> Path:
    path = _diagnosed_pairs_cache_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pairs": list(cache.values())}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def study_fingerprint(study_name: str | None, study_started_at_utc: str | None,
                      n_trials_completed: int | None) -> str | None:
    """Issue #1090 (Katalog #923) — deterministischer Fingerabdruck EINER Study-Beobachtung
    (``SHA256(study_name|study_started_at_utc|n_trials_completed)``), Grundlage der Dedup-Prüfung
    in ``record_diagnosed_pair``: dieselbe Study darf ``n_runs_confirmed`` nur EINMAL erhöhen,
    unabhängig davon, wie oft ``record_diagnosed_pair`` für sie aufgerufen wird (z. B. durch
    mehrere gleichzeitige Sweep-Prozesse auf demselben Store, #1086). ``None``, wenn eine der drei
    Komponenten fehlt (Legacy-/Test-Aufrufer ohne Study-Kontext) — der Aufrufer fällt dann auf das
    alte, ungedupte Zählverhalten zurück (fail-open auf fehlender Evidenz)."""
    if not study_name or not study_started_at_utc or n_trials_completed is None:
        return None
    import hashlib
    raw = f"{study_name}|{study_started_at_utc}|{n_trials_completed}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_diagnosed_pair(recommendation: dict, *, work_dir: Path | None = None,
                          run_id: str | None = None) -> Path:
    """Issue #681/#761 — schreibt/aktualisiert den (Symbol, Strategie)-Diagnose-Eintrag im
    automatisch gepflegten Cache (siehe ``load_diagnosed_pairs_cache``-Docstring). Ein No-Op-Eintrag
    (``action == 'none'``) wird NICHT gespeichert (kein Kollaps ⇒ nichts zu cachen). Idempotent:
    ein erneuter Diagnose-Lauf für dasselbe Paar überschreibt den vorherigen Eintrag UND setzt
    ``runs_since_recorded`` auf 0 zurück (frischer Befund, neue Ablauf-Frist beginnt).

    Issue #761 — jeder gespeicherte Eintrag trägt ``expires_after_runs`` (aus der Empfehlung, sonst
    Default 10) und ``runs_since_recorded=0``. ``enumerate_tunable_pairs`` (sweep.py) lässt ein
    Paar, dessen ``runs_since_recorded >= expires_after_runs`` erreicht hat, genau EINMAL wieder zu
    (Re-Test) — ein einmaliger Befund zementiert das Paar sonst dauerhaft, auch wenn Kohorte-A/B-
    Fixes (#753/#756/#757) die Lage grundlegend ändern.

    Issue #778 — jeder Eintrag trägt zusätzlich ``first_seen_run_id`` (der ``run_id`` des Laufs, in
    dem diese (``action``, ``binding_cause``)-Kombination ERSTMALIG auftrat) und
    ``n_runs_confirmed`` (wie oft sie seither IN FOLGE bestätigt wurde, inkl. dieses Laufs) — die
    Reproduzierbarkeits-Grundlage für ``recommend_diagnosis_action``s Evidenz-Gate. Ändert sich
    ``action`` ODER ``binding_cause`` gegenüber dem Vorgänger-Eintrag, beginnt die Zählung bei 1
    (neuer Befund, keine Fortsetzung einer Bestätigungsserie). ``run_id=None`` (Default, Legacy-/
    Test-Aufrufer) ⇒ ``first_seen_run_id`` bleibt ``None``, ``n_runs_confirmed`` wird unverändert
    gezählt (die Zählung selbst braucht keine Run-Identität, nur die Wiederholung).

    Issue #778 — die Persistenz-Bedingung ist jetzt ``binding_cause`` (nicht mehr ``action``): eine
    ``'none'``-Empfehlung mit einer ECHTEN diagnostizierten Ursache (z. B. ``'signal_absent'`` ohne
    (noch) ausreichende Evidenz) WIRD gespeichert — sonst könnte ``n_runs_confirmed`` nie über
    mehrere Läufe akkumulieren (die Eskalationsbedingung selbst wäre unerreichbar). Nur ein echter
    Nicht-Befund (``binding_cause in (None, 'none', 'no_data')``) bleibt ungespeichert.

    Issue #926 Fix 1 — Sofortmassnahme-Schalter: solange
    ``optimizer.json['diagnostic_writeback_enabled']`` (Default ``True``) auf ``False`` steht, ist
    JEDER Rückschrieb unterdrückt (No-Op, gibt nur den Cache-Pfad zurück). Schutz gegen eine
    Diagnose, die auf einer durch #913 (0 % definierter oos_psr) verzerrten Kohorte lief — genau
    der Zustand, der diesen Lauf zu Strike 1 von 2 gemacht hätte, wäre der Schalter nicht gesetzt
    gewesen.

    Issue #1066 Fix Punkt 3 (Pitfall #371) — ZUSÄTZLICH zur Domänenklammer (``_widen_bounds_toward``,
    ``spaces.clamp_param_bounds``) trägt jeder ``'search_space_override'``-Eintrag jetzt
    ``widen_applications`` (``{param: Anzahl bereits übernommener Weitungen IN FOLGE}``). Ab
    ``_MAX_WIDEN_APPLICATIONS`` (Default 2) je Parameter wird ein NEUER, weiter geweiteter Vorschlag
    NICHT mehr übernommen — der zuletzt gespeicherte ``proposed_bounds``-Wert für diesen Parameter
    bleibt stehen (``n_runs_confirmed``/``runs_since_recorded`` laufen unabhängig davon weiter).
    Zweite, unabhängige Sperre neben der Domänenklammer: selbst ein Parameter, dessen Domäne (noch)
    nicht im Register steht, kann so nicht unbegrenzt oft nachgeweitet werden.

    Issue #1090 (Katalog #923) Fix Punkt 1 — ``recommendation['study_fingerprint']`` (siehe
    ``study_fingerprint()`` oben, SHA256 über ``study_name``/``study_started_at_utc``/
    ``n_trials_completed``) dedupliziert ``n_runs_confirmed`` auf der zugrundeliegenden
    BEOBACHTUNG, nicht auf dem Funktionsaufruf: Root-Cause #1090 — derselbe reale Trial-Datensatz
    einer Study wurde unter mehreren gleichzeitigen Sweep-Prozessen (#1086, geteilter Store) mehr-
    fach als Bestätigung gezählt (beobachtet: ``n_runs_confirmed=3`` bei EINEM tatsächlichen ASML-
    Lauf, identische ``study_wallclock_s``). Trägt der neue UND der vorherige Eintrag denselben
    ``study_fingerprint``, ist es dieselbe Beobachtung — ``n_runs_confirmed`` bleibt UNVERÄNDERT
    (nicht 0, nicht neu gezählt). Fehlt der Fingerprint (``None``, Legacy-/Test-Aufrufer ohne
    Study-Kontext) fällt die Zählung auf das alte, ungedupte Verhalten zurück."""
    if not _read_diagnostic_writeback_enabled():
        return _diagnosed_pairs_cache_path(work_dir)
    if recommendation.get("binding_cause") in (None, "none", "no_data"):
        return _diagnosed_pairs_cache_path(work_dir)
    cache = load_diagnosed_pairs_cache(work_dir)
    key = (recommendation["strategy"], recommendation["symbol"])
    prior = cache.get(key)
    entry = dict(recommendation)
    entry.setdefault("expires_after_runs", _DEFAULT_EXPIRES_AFTER_RUNS)
    entry["runs_since_recorded"] = 0
    same_series = bool(
        prior and prior.get("action") == entry.get("action")
        and prior.get("binding_cause") == entry.get("binding_cause"))
    new_fingerprint = entry.get("study_fingerprint")
    is_duplicate_observation = bool(
        same_series and new_fingerprint is not None
        and new_fingerprint == (prior or {}).get("study_fingerprint"))
    if is_duplicate_observation:
        # Issue #1090 — dieselbe Study wurde bereits gezählt (z. B. ein Nebenprozess auf demselben
        # Store, #1086) — n_runs_confirmed bleibt stehen, nur die Ablauf-Frist wird aufgefrischt.
        entry["n_runs_confirmed"] = int(prior.get("n_runs_confirmed", 1))
        entry["first_seen_run_id"] = prior.get("first_seen_run_id", run_id)
    elif same_series:
        entry["n_runs_confirmed"] = int(prior.get("n_runs_confirmed", 1)) + 1
        entry["first_seen_run_id"] = prior.get("first_seen_run_id", run_id)
    else:
        entry["n_runs_confirmed"] = 1
        entry["first_seen_run_id"] = run_id
    if entry.get("action") == "search_space_override" and entry.get("proposed_bounds"):
        prior_widen_applications = (prior or {}).get("widen_applications", {}) if same_series else {}
        prior_proposed_bounds = (prior or {}).get("proposed_bounds", {}) if same_series else {}
        widen_applications = dict(prior_widen_applications)
        new_proposed_bounds = dict(entry["proposed_bounds"])
        for param, bound in entry["proposed_bounds"].items():
            count = int(prior_widen_applications.get(param, 0))
            if count >= _MAX_WIDEN_APPLICATIONS and param in prior_proposed_bounds:
                new_proposed_bounds[param] = prior_proposed_bounds[param]
            else:
                widen_applications[param] = count + 1
        entry["proposed_bounds"] = new_proposed_bounds
        entry["widen_applications"] = widen_applications
    cache[key] = entry
    return _save_diagnosed_pairs_cache(cache, work_dir=work_dir)


def migrate_search_space_override_cache(*, work_dir: Path | None = None) -> dict:
    """Issue #1066 Fix Punkt 5 — EINMALIGE Migration eines VOR diesem Fix geschriebenen #761-Caches:
    jeder ``proposed_bounds``-Eintrag wird gegen ``spaces._PARAM_DOMAIN_REGISTRY``
    (``spaces.clamp_param_bounds``) geklammert. Ein Eintrag mit einer Untergrenze unterhalb der
    Domäne (z. B. ``ema_period: [-325.0, 300]``, Beweis B-5 im #866-Katalog) wird auf den
    zulässigen Bereich zurückgesetzt, statt weiter negativ gesampelt zu werden.

    Idempotent (ein bereits geklammerter Cache bleibt unverändert) und sicher wiederholt aufrufbar
    (z. B. als Vorlauf-Schritt vor jedem Sweep, analog ``purge_stale_studies``). Rückgabe:
    ``{'entries_checked', 'entries_migrated', 'params_clamped'}`` — Telemetrie für den Aufrufer,
    kein I/O ausser dem Cache selbst."""
    from automation.optimizer.spaces import clamp_param_bounds

    cache = load_diagnosed_pairs_cache(work_dir)
    entries_migrated = 0
    params_clamped: list[dict] = []
    for entry in cache.values():
        proposed = entry.get("proposed_bounds")
        if not proposed:
            continue
        changed = False
        new_proposed = {}
        for param, bound in proposed.items():
            if not bound or len(bound) != 2:
                new_proposed[param] = bound
                continue
            clamped = list(clamp_param_bounds(param, bound[0], bound[1]))
            if clamped != list(bound):
                changed = True
                params_clamped.append({
                    "strategy": entry.get("strategy"), "symbol": entry.get("symbol"),
                    "param": param, "before": list(bound), "after": clamped,
                })
            new_proposed[param] = clamped
        if changed:
            entry["proposed_bounds"] = new_proposed
            entries_migrated += 1
    if entries_migrated:
        _save_diagnosed_pairs_cache(cache, work_dir=work_dir)
    return {
        "entries_checked": len(cache), "entries_migrated": entries_migrated,
        "params_clamped": params_clamped,
    }


def age_diagnosed_pairs_cache(*, work_dir: Path | None = None) -> dict[tuple[str, str], dict]:
    """Issue #761 — EINMAL je Sweep-Lauf aufgerufen (``sweep.run_per_symbol_sweep``, VOR der
    Paar-Enumeration): erhöht ``runs_since_recorded`` jedes Cache-Eintrags um 1. Ein Paar, das DIESEN
    Lauf tatsächlich neu diagnostiziert wird, überschreibt seinen Eintrag danach ohnehin via
    ``record_diagnosed_pair`` (``runs_since_recorded`` zurück auf 0) — das Altern hier betrifft nur
    Paare, die WEGEN des Cache-Skips gar nicht erst re-diagnostiziert wurden. Kein Cache ⇒ No-Op.
    Rückgabe: der gealterte Cache (für ``enumerate_tunable_pairs``, ohne ihn ein zweites Mal von der
    Platte zu laden)."""
    cache = load_diagnosed_pairs_cache(work_dir)
    if not cache:
        return cache
    for entry in cache.values():
        entry["runs_since_recorded"] = int(entry.get("runs_since_recorded", 0)) + 1
    _save_diagnosed_pairs_cache(cache, work_dir=work_dir)
    return cache


def is_diagnosed_pair_expired(entry: dict) -> bool:
    """Issue #761 — ``True``, wenn ein 'denylist'-Cache-Eintrag seine Ablauf-Frist erreicht hat
    (``runs_since_recorded >= expires_after_runs``) und daher DIESEN Lauf wieder regulär enumeriert
    (re-getestet) werden soll, statt automatisch übersprungen zu werden."""
    expires_after = int(entry.get("expires_after_runs", _DEFAULT_EXPIRES_AFTER_RUNS))
    runs_since = int(entry.get("runs_since_recorded", 0))
    return runs_since >= expires_after


def load_continuous_bar_invalid_strategies(base_cfg: Path | None = None) -> frozenset[str]:
    """Issue #698 — Strategien, deren Signal auf synthetischen, KONTINUIERLICHEN 24/7-1h-Bars
    (die einzige in diesem System verfügbare Bar-Semantik, kein RTH-Session-Katalog) strukturell
    etwas anderes misst als beabsichtigt — z. B. ``GapContinuationStrategy`` (Variante A,
    Vortagsschluss-vs-Tagesbeginn-Gap): ohne echte Handelspausen zwischen Kalendertagen degeneriert
    der Gap zur Differenz zweier AUFEINANDERFOLGENDER Bars, die keinerlei Continuation-Edge trägt
    (bekannter 24/7-Boundary-Caveat, SPEC_05/#693). Kein Bounds-Weiten behebt eine ungültige
    Messung — die Entscheidung ist an der BAR-SEMANTIK des Systems festzumachen, nicht am
    Backtest-Ergebnis (die Root-Cause-Präzisierung aus #698).

    Deklariert in ``strategies.json`` je Strategie-Eintrag als ``invalid_on_continuous_bars: true``
    (Zero-Hardcoding — keine Python-Konstante). ``sweep.enumerate_tunable_pairs`` überspringt eine
    gelistete Strategie VOLLSTÄNDIG (alle Symbole, EIN strukturiertes Event statt N nutzloser
    Trials) und weist sie im ``sweep_completed``-Event als ``strategies_skipped`` mit dem Grund
    ``SKIPPED_INVALID_ON_CONTINUOUS_BARS`` aus — kein stiller 16/180-Trial-Budget-Verbrauch für ein
    Signal, das auf dieser Datenquelle strukturell ungültig ist. Fehlt der Key (Default) ⇒ die
    Strategie läuft unverändert (bit-identisch zum Pre-#698-Verhalten). Eine RTH-session-bewusste
    Variante B (``session_open_hour``) bliebe der Re-Aktivierungspfad, sollte künftig ein
    Session-Kalender für ein Symbol verfügbar werden — bis dahin ist die Deaktivierung die
    ehrlichere Wahl gegenüber einer strukturell ungültigen Messung."""
    if base_cfg is None:
        from automation.optimizer.trial_config import config_dir
        base_cfg = config_dir()
    path = base_cfg / "strategies.json"
    if not path.exists():
        return frozenset()
    try:
        data = json.loads(path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return frozenset()
    out = set()
    for entry in data.get("strategies", []) or []:
        if entry.get("invalid_on_continuous_bars") is True and entry.get("strategy_class"):
            out.add(entry["strategy_class"])
    return frozenset(out)


def diagnose_symbol_degeneracy(symbol: str, per_strategy_diagnoses: list[dict], *,
                               min_strategies: int = 3) -> dict:
    """Issue #807 — aggregiert ``diagnose_trade_frequency``-Befunde MEHRERER Strategien DESSELBEN
    Symbols zu EINER symbolweiten Aussage, statt dasselbe Datenproblem N-mal unabhaengig (einmal je
    Strategie) zu diagnostizieren.

    Root-Cause #807: ``HYPE.ETORO`` erzeugte ueber SECHS strukturell voellig verschiedene Strategien
    (SmaCrossover, MeanReversion, DynamicBreakout, FlashCrashReversal, VolatilityBreakoutPump,
    ComboTrendVwap) null auswertbare Trials — jeweils mit eigenem ``STRUCTURAL_ALL_UNEVALUABLE``-
    Ereignis, eigenem ``diagnose_trade_frequency``-Aufruf und eigenem Cache-Eintrag (14 × 16 = 224
    verbrannte Trials, 14 falsch etikettierte Cache-Eintraege). ``HYPE`` bestand Gate 1 (Datenspanne)
    — die Ursache liegt in der BAR-QUALITAET (degenerierte/konstante Bars), nicht in der Datenspanne
    ODER in 14 unabhaengigen Suchraum-Problemen.

    Ein Symbol gilt als ``SYMBOL_DATA_DEGENERATE``, sobald mindestens ``min_strategies`` (Default 3)
    STRUKTURELL VERSCHIEDENE Strategien ``binding_cause == 'signal_absent'`` UND
    ``median_is_trades == 0`` melden — ``'signal_absent'`` ist per Definition (``diagnose_trade_
    frequency``) bereits PARAMETERUNABHAENGIG (die Strategie feuert im GESAMTEN Suchraum nie); mehrere
    strukturell unterschiedliche Strategien, die UNABHAENGIG voneinander alle parameterunabhaengig
    scheitern, sind kein Zufall der Suchraum-Bounds mehr, sondern ein Beleg fuer ein Datenproblem.

    ``per_strategy_diagnoses`` ist eine Liste von ``diagnose_trade_frequency``-Rueckgabe-Dicts (ein
    Eintrag je bereits geprueften (Symbol, Strategie)-Paar). Rein, deterministisch, kein I/O."""
    n_checked = len(per_strategy_diagnoses)
    n_signal_absent = sum(
        1 for d in per_strategy_diagnoses
        if d.get("binding_cause") == "signal_absent" and d.get("median_is_trades") == 0
    )
    is_degenerate = n_signal_absent >= min_strategies
    return {
        "symbol": symbol,
        "n_strategies_checked": n_checked,
        "n_signal_absent": n_signal_absent,
        "min_strategies": min_strategies,
        "is_degenerate": is_degenerate,
    }


def check_bar_quality(highs: list[float], lows: list[float], closes: list[float], *,
                      max_frac_high_eq_low: float = 0.20,
                      max_frac_identical_consecutive_closes: float = 0.5,
                      min_distinct_closes: int = 10,
                      max_frac_zero_true_range: float = 0.25,
                      min_atr_median_bps: float = 5.0,
                      min_bar_coverage_ratio: float = 0.6,
                      bar_coverage_ratio: float | None = None,
                      median_delta_t_s: float | None = None,
                      ticks_per_bar_median: float | None = None,
                      ticks_per_bar_p05: float | None = None,
                      frac_bars_single_tick: float | None = None,
                      max_frac_bars_single_tick: float = 0.5) -> dict:
    """Issue #807/#900 — billige Bar-QUALITAETSPRUEFUNG (Preflight statt Post-Mortem): erkennt
    degenerierte/konstante Bars VOR Phase 1 eines Symbols, statt erst nach 14 × 16 verbrannten
    Trials ueber 14 unabhaengige ``STRUCTURAL_ALL_UNEVALUABLE``-Diagnosen (siehe
    ``diagnose_symbol_degeneracy``-Docstring fuer den vollen Root-Cause-Befund, ``HYPE.ETORO``
    Pitfall #446 zu ``volume=1.0``-artigen degenerierten Bars).

    Fuenf Kennzahlen ueber die uebergebene Bar-Kohorte (bereits aggregiert — diese Funktion selbst
    macht KEIN I/O, keine Bar-Aggregation):
      * ``frac_high_eq_low`` — Anteil Bars mit ``high == low`` (keinerlei Intra-Bar-Bewegung).
      * ``frac_identical_consecutive_closes`` — Anteil Bars, deren Close IDENTISCH zum
        Vorgaenger-Close ist (der Preis "steht still").
      * ``n_distinct_closes`` — Anzahl DISTINKTER Close-Werte ueber die gesamte Kohorte.
      * ``frac_zero_true_range`` (Issue #900) — Anteil Bars mit True Range == 0 (ATR-Nullranges
        ueber die GESAMTE Kohorte, nicht nur ``high==low`` — eine Bar mit ``high==low``, aber
        einer Kurslücke zum Vorgaenger-Close hat trotzdem TR > 0; der #897-Sperrklinken-Defekt
        greift umso haerter, je mehr Nullranges in der ATR-Historie liegen).
      * ``atr_median_bps`` (Issue #900) — Median der True Range in bps des Preises (Skalen-Check:
        eine ATR, die systematisch bei 2 bps statt 30 bps liegt, passiert die relativen
        Degenerations-Kennzahlen oben vollstaendig).
      * ``bar_coverage_ratio`` (Issue #923) — tatsaechliche Bars / erwartete Kalender-Bars der
        Stichprobe (aus ``sweep._load_symbol_bar_quality_sample``, hier nur geprueft, nicht neu
        berechnet — diese Funktion macht kein I/O). Ein Symbol mit grosser Datenspanne (besteht
        Gate 1) kann trotzdem ueberwiegend LUECKEN im Bar-Raster haben (30% Abdeckung ueber 1099
        Tage bestand Gate 1 vor #923 unveraendert); ``None`` (kein Preflight-Aufrufer liefert den
        Wert) ⇒ nicht pruefbar, kein Fail.
      * ``ticks_per_bar_median``/``ticks_per_bar_p05``/``frac_bars_single_tick`` (Issue #1272/GH
        #1145, Katalog #1272-1297) — misst die TICK-DICHTE je Bar direkt, statt sie nur aus dem
        AGGREGIERTEN ``frac_zero_true_range``-Symptom zu erschliessen: ``frac_zero_true_range``
        (oben) beobachtet, DASS eine Bar keine Intrabar-Spanne traegt, misst aber nicht, WARUM
        (ein einzelner Tick je Stundenfenster macht ``high == low`` MECHANISCH unvermeidlich,
        unabhaengig von der tatsaechlichen Marktvolatilitaet — Pitfall #458). Bei
        ``ticks_per_bar_median <= 1`` ODER ``frac_bars_single_tick > max_frac_bars_single_tick``
        (Default 0.5) ist die Bar-Achse strukturell UNFAEHIG, eine Intrabar-Ausloese-Distanz zu
        tragen — dieser Zustand FAILt mit dem eigenen Code ``BAR_AXIS_NO_INTRABAR_INFORMATION``
        und ``severity='blocking'`` (staerker als die uebrigen, ``'high'``-gewichteten
        Degenerations-Kriterien dieser Funktion), da er JEDE nachgelagerte Stop-/Trigger-Pruefung
        (``check_effective_stop_distance`` u. a., siehe #1274/GH #1147) invalidiert. ``None`` (kein
        Aufrufer liefert die Tick-Zahlen, z. B. ein Legacy-Preflight-Pfad) ⇒ nicht pruefbar fuer
        DIESES Kriterium, kein Fail allein deswegen.

    ``passed=False``, sobald EINE der Schwellen verletzt ist (alle aus
    ``optimizer.json['bar_quality']``, Zero-Hardcoding — Issue #900 verschaerft
    ``max_frac_high_eq_low`` 0.5 → 0.20 und ergaenzt ``max_frac_zero_true_range``/
    ``min_atr_median_bps``). Leere Eingabe ⇒ ``passed=False`` (keine Bars ⇒ nichts zu handeln,
    dieselbe Konsequenz wie degenerierte Bars — kein stiller Pass). Rein, deterministisch, kein
    I/O.

    Rueckgabe traegt seit #1272/GH #1145 zusaetzlich ``severity`` (``'blocking'``, wenn
    ``BAR_AXIS_NO_INTRABAR_INFORMATION`` unter den Gruenden ist, sonst ``'high'`` — der
    Aufrufer (``sweep.py``s ``INVARIANT_STREAM_RESULT``-Emission) uebernimmt diesen Wert statt
    eine feste Severity zu stempeln)."""
    n = len(closes)
    if n == 0:
        return {
            "n_bars": 0, "frac_high_eq_low": None, "frac_identical_consecutive_closes": None,
            "n_distinct_closes": 0, "frac_zero_true_range": None, "atr_median_bps": None,
            "bar_coverage_ratio": bar_coverage_ratio, "median_delta_t_s": median_delta_t_s,
            "ticks_per_bar_median": ticks_per_bar_median, "ticks_per_bar_p05": ticks_per_bar_p05,
            "frac_bars_single_tick": frac_bars_single_tick,
            "passed": False, "reason": "no_bars", "severity": "high",
        }
    frac_high_eq_low = sum(1 for h, l in zip(highs, lows) if h == l) / n
    if n > 1:
        frac_identical = sum(
            1 for i in range(1, n) if closes[i] == closes[i - 1]
        ) / (n - 1)
    else:
        frac_identical = 0.0
    n_distinct = len(set(closes))

    # Issue #900 Fix 1 — True Range je Bar: max(high-low, |high-prev_close|, |low-prev_close|).
    # Die erste Bar hat keinen Vorgaenger-Close ⇒ TR = high-low (Standard-ATR-Konvention).
    true_ranges: list[float] = []
    prev_close: float | None = None
    for h, l, c in zip(highs, lows, closes):
        tr = (h - l) if prev_close is None else max(h - l, abs(h - prev_close), abs(l - prev_close))
        true_ranges.append(tr)
        prev_close = c
    frac_zero_true_range = sum(1 for tr in true_ranges if tr <= 0.0) / n
    tr_bps = sorted(
        (tr / c * 10_000.0) for tr, c in zip(true_ranges, closes) if c > 0
    )
    atr_median_bps = tr_bps[len(tr_bps) // 2] if tr_bps and len(tr_bps) % 2 == 1 else (
        (tr_bps[len(tr_bps) // 2 - 1] + tr_bps[len(tr_bps) // 2]) / 2.0 if tr_bps else None
    )

    reasons = []
    # Issue #1272 (GH #1145) — die Tick-Dichte-Kriterien werden ZUERST geprueft: ein
    # BAR_AXIS_NO_INTRABAR_INFORMATION-Befund ist die Ursache, nicht nur ein weiteres Symptom
    # (siehe check_effective_stop_distance/#1274, das diesen Grund als vorgelagerte Ursache
    # zitiert) — er soll in der Grunde-Liste VOR den abgeleiteten frac_zero_true_range/
    # frac_high_eq_low-Symptomen erscheinen.
    is_blocking = False
    if ticks_per_bar_median is not None and ticks_per_bar_median <= 1:
        reasons.append(
            f"BAR_AXIS_NO_INTRABAR_INFORMATION: ticks_per_bar_median={ticks_per_bar_median:.3f} <= 1")
        is_blocking = True
    if frac_bars_single_tick is not None and frac_bars_single_tick > max_frac_bars_single_tick:
        reasons.append(
            f"BAR_AXIS_NO_INTRABAR_INFORMATION: frac_bars_single_tick={frac_bars_single_tick:.3f} "
            f"> {max_frac_bars_single_tick}")
        is_blocking = True
    if frac_high_eq_low > max_frac_high_eq_low:
        reasons.append(f"frac_high_eq_low={frac_high_eq_low:.3f} > {max_frac_high_eq_low}")
    if frac_identical > max_frac_identical_consecutive_closes:
        reasons.append(
            f"frac_identical_consecutive_closes={frac_identical:.3f} > "
            f"{max_frac_identical_consecutive_closes}")
    if n_distinct < min_distinct_closes:
        reasons.append(f"n_distinct_closes={n_distinct} < {min_distinct_closes}")
    if frac_zero_true_range > max_frac_zero_true_range:
        reasons.append(
            f"frac_zero_true_range={frac_zero_true_range:.3f} > {max_frac_zero_true_range}")
    if atr_median_bps is not None and atr_median_bps < min_atr_median_bps:
        reasons.append(f"atr_median_bps={atr_median_bps:.3f} < {min_atr_median_bps}")
    if bar_coverage_ratio is not None and bar_coverage_ratio < min_bar_coverage_ratio:
        reasons.append(f"bar_coverage_ratio={bar_coverage_ratio:.3f} < {min_bar_coverage_ratio}")

    return {
        "n_bars": n,
        "frac_high_eq_low": frac_high_eq_low,
        "frac_identical_consecutive_closes": frac_identical,
        "n_distinct_closes": n_distinct,
        "frac_zero_true_range": frac_zero_true_range,
        "atr_median_bps": atr_median_bps,
        "bar_coverage_ratio": bar_coverage_ratio,
        "median_delta_t_s": median_delta_t_s,
        "ticks_per_bar_median": ticks_per_bar_median,
        "ticks_per_bar_p05": ticks_per_bar_p05,
        "frac_bars_single_tick": frac_bars_single_tick,
        "severity": "blocking" if is_blocking else "high",
        "passed": not reasons,
        "reason": "; ".join(reasons) if reasons else "OK",
    }


def load_symbol_strategy_denylist(base_cfg: Path | None = None) -> dict[tuple[str, str], str]:
    """Issue #669 — deklarative (Strategie, Symbol)-Deaktivierungsliste aus
    ``symbol_strategy_denylist.json`` (Zero-Hardcoding): ``{(strategy, symbol): reason}``.

    Ein Paar in dieser Liste wird in ``sweep.enumerate_tunable_pairs`` VOR dem Sweep übersprungen
    (Log-Zeile mit ``reason``), statt 16 strukturell nutzlose Trials zu verbrennen. Fehlt die Datei
    oder ist ``pairs`` leer ⇒ ``{}`` (bit-identisch zum Pre-#669-Verhalten — KEIN Paar wird ohne
    einen dokumentierten Diagnose-Befund deaktiviert)."""
    if base_cfg is None:
        from automation.optimizer.trial_config import config_dir
        base_cfg = config_dir()
    path = base_cfg / "symbol_strategy_denylist.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return {}
    out: dict[tuple[str, str], str] = {}
    for entry in data.get("pairs", []) or []:
        strat = entry.get("strategy")
        sym = entry.get("symbol")
        if strat and sym:
            out[(strat, sym)] = entry.get("reason", "DECLARED_NON_VIABLE")
    return out
