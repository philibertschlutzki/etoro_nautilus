"""automation/optimizer/invariants.py
=====================================
Issue #743 — wachsende Bibliothek automatisierter mathematischer/Konfigurations-Invarianzen.

Jede Prüfung ist eine REINE Funktion über plain Dicts/Listen (synthetische ``user_attrs``-artige
Fixtures) — bewusst OHNE Abhängigkeit von Optuna-``Study``/``Trial``-Objekten oder vom
Report-Generator (#742), damit jede Prüfung unabhängig mit einem PASS- und einem FAIL-Fixture
unit-testbar ist. #742 ruft diese Funktionen mit den bereits geladenen Proposal-/Trial-Daten auf
und bettet die Ergebnisse als ``invariant_checks`` in den Sweep-Report ein.

Vorbild/Präzedenzfall: ``REWARD_TERM_INERT`` (run_optimization.py, Issue #621) war bereits EIN
automatisierter "wirkt dieser Mechanismus überhaupt"-Check — ``check_reward_term_variance`` ist
seine Verallgemeinerung zu einer vollständigen Liste. Die anderen vier Prüfungen verdrahten
Forensik-Erkenntnisse, die bislang nur EINMALIG von Hand verifiziert wurden (#651, #652/#670,
#649, #654/#671), als DAUERHAFTE Regressionswächter (AGENTS.md-Pitfall #217).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InvariantResult:
    name: str
    passed: bool
    expected: Any
    actual: Any
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


def check_sr0_coherence(holdout_metrics: dict) -> InvariantResult:
    """Issue #651-Regressionswächter.

    ``confirm.confirm_per_symbol_promotion`` berechnet ``deflated_dsr`` (Entscheidung) UND
    ``deflation_dsr_z`` (Telemetrie) im selben Codeblock aus EINEM lokalen ``deflation_sr0``
    (confirm.py — ``deflated_sharpe_ratio(..., sr0=deflation_sr0)`` und
    ``psr_z(..., sr_star=deflation_sr0)``). Vor #651 divergierten beide Grössen bei kleinen
    Kohorten (Hourly N=9, Faktor ≈3.48×), weil ``deflated_sharpe_ratio`` SR₀ intern UNGEFLOORT neu
    berechnete. Auf dem EXPORTIERTEN Proposal ist die Bit-Identität nicht mehr direkt nachrechenbar
    (die Rohwerte, aus denen SR₀ abgeleitet wurde, werden nicht separat persistiert) — strukturell
    prüfbar bleibt aber die Ko-Präsenz: ``deflated_dsr``/``deflation_dsr_z`` dürfen NIE ohne ein
    begleitendes ``deflated_sr0`` auftauchen (sie werden im selben Block aus genau diesem Wert
    abgeleitet, #651/#701).
    """
    sr0 = holdout_metrics.get("deflated_sr0")
    dsr = holdout_metrics.get("deflated_dsr")
    dsr_z = holdout_metrics.get("deflation_dsr_z")
    has_sr0 = sr0 is not None
    has_dsr_signal = dsr is not None or dsr_z is not None
    passed = has_sr0 == has_dsr_signal
    return InvariantResult(
        name="check_sr0_coherence",
        passed=passed,
        expected="deflated_sr0 gesetzt genau dann, wenn deflated_dsr/deflation_dsr_z gesetzt sind (#651)",
        actual={"deflated_sr0": sr0, "deflated_dsr": dsr, "deflation_dsr_z": dsr_z},
        detail=("OK" if passed else
                "deflated_dsr/deflation_dsr_z ohne begleitendes deflated_sr0 (oder umgekehrt) — "
                "moegliche Wiederkehr der #651-Root-Cause (divergente SR0-Quellen fuer Entscheidung "
                "vs. Telemetrie)."),
    )


def check_n_family_consistency(holdout_metrics: dict) -> InvariantResult:
    """Issue #652/#670-Regressionswächter.

    Die Promotion-Entscheidung nutzt ``deflation_n_effective = max(deflation_n_eligible,
    deflation_n_family_effective)`` (confirm.py, #652/#695) als Multiplizität für SR₀. Weicht der
    exportierte ``deflation_n_effective`` von genau dieser Formel ab, hat die Entscheidung eine
    ANDERE N-/Varianzquelle konsumiert als die Telemetrie ausweist — exakt die #670-Fehlerklasse
    (eine Nachricht behauptete die falsche Varianzquelle).
    """
    n_eligible = holdout_metrics.get("deflation_n_eligible")
    n_family_eff = holdout_metrics.get("deflation_n_family_effective")
    n_effective = holdout_metrics.get("deflation_n_effective")
    if n_eligible is None and n_family_eff is None and n_effective is None:
        return InvariantResult(
            name="check_n_family_consistency",
            passed=True,
            expected="deflation_n_effective == max(deflation_n_eligible, deflation_n_family_effective)",
            actual=None,
            detail="Keine Deflations-Kohorte (deflated_selection=False oder N<2) — nicht anwendbar.",
        )
    expected_n = max(n_eligible or 0, n_family_eff or 0)
    passed = n_effective == expected_n
    return InvariantResult(
        name="check_n_family_consistency",
        passed=passed,
        expected=expected_n,
        actual=n_effective,
        detail=("OK" if passed else
                f"deflation_n_effective={n_effective} != max(N_eligible={n_eligible}, "
                f"N_family_effective={n_family_eff})={expected_n} — Entscheidung und Telemetrie "
                "koennten unterschiedliche N-/Varianzquellen konsumiert haben (#652/#670)."),
    )


# Issue #765 — deklarierte Marker-Konvention fuer eine SCHEMA-TEXT-Aussage "dieser Key ist AKTUELL
# ein hartes/aktives Konjunktions-Mitglied". Bewusst KEINE Freitext-/NLP-Erkennung von Formulierungen
# wie "NICHT MEHR in eligible_requires_all" oder "muss ... gelistet sein, um zu greifen" (mehrere
# bestehende Schema-Texte — min_expectancy, oos_min_profitable_folds_frac, oos_min_evaluable_folds —
# erwaehnen den Listennamen GENAU IN SOLCHEN NEGIERTEN/BEDINGTEN Kontexten; ein reiner Substring-Scan
# ohne diesen exakten Marker wuerde sie als Falsch-Positive markieren). Ein Schema-Text OHNE diesen
# Marker macht schlicht KEINE geprueft Aussage (nichts zu verifizieren) — das ist die Root-Cause-
# Lehre aus #765: die zwei tatsaechlich stale Behauptungen (min_sortino/oos_min_sortino_note)
# verwendeten beide bereits zufaellig genau ``"in eligible_requires_all (HART)"``.
_ELIGIBLE_ALL_CLAIM_MARKER = "in eligible_requires_all (HART)"
_ELIGIBLE_ANY_CLAIM_MARKER = "in eligible_requires_any (aktiver OR-Arm)"


def check_config_key_registry(tournament_config: dict) -> InvariantResult:
    """Issue #649/#760/#765-Regressionswächter.

    Jeder in ``eligible_requires_all``/``eligible_requires_any`` referenzierte Gate-Key MUSS (nach
    ``oos_``-Normalisierung) auf einen echten ``condition_map``-Handler in
    ``automation.backtest_runner`` resolven. ``load_tournament_config`` bricht dafuer bereits beim
    Config-Load fail-loud ab (#649) — diese Pruefung importiert DIESELBE Registry
    (``OOS_CONDITION_MAP_KEYS``/``_canonical_gate_key``), statt eine zweite, potenziell
    abweichende Kopie zu pflegen, und dient als Snapshot-Nachweis im Report (Defense-in-Depth: sie
    prueft die Config, die TATSAECHLICH in diesem Report referenziert wird, nicht nur "irgendwann
    beim Start").

    Issue #760 — ZUSÄTZLICH: jeder Key muss entweder eine ``oos_gate_deltas``-Spalte besitzen
    (``OOS_CONDITION_MAP_KEYS``-Geschwister-Registry ``OOS_GATE_DELTA_KEYS``) oder explizit als
    delta-frei bekannt sein — sonst würde ein reaktivierter Key mit Handler, aber ohne Delta-Spalte,
    lautlos aus ``reward.gate_rank_correlation_matrix`` (#760) verschwinden (dieselbe Drift-Klasse
    wie #649, nur eine Ebene tiefer: Handler vorhanden, aber die Kollinearitäts-Diagnose sieht ihn
    trotzdem nie, weil kein Delta gestempelt wird). ``min_evaluable_folds`` ist der einzige aktuell
    bekannte strukturell delta-freie Gate-Key (reiner Fold-Zähler).

    Issue #765 — ZUSÄTZLICH: ``_schema.fields``-Texte, die (via ``_ELIGIBLE_ALL_CLAIM_MARKER``/
    ``_ELIGIBLE_ANY_CLAIM_MARKER``) explizit eine AKTUELLE Konjunktions-Mitgliedschaft behaupten,
    muessen mit der TATSAECHLICHEN ``eligible_requires_all``/``eligible_requires_any``-Liste
    uebereinstimmen (nach ``oos_``-Normalisierung) — Root-Cause #765: ``min_sortino``/
    ``oos_min_sortino_note`` behaupteten weiterhin '#593 in eligible_requires_all (HART)', obwohl
    #614 den Sortino laengst durch ``oos_min_psr`` ersetzt hatte (dieselbe Fehlerklasse wie #649,
    hier bislang nur als Doku-Drift statt eines toten Handlers)."""
    from automation.backtest_runner import (
        OOS_CONDITION_MAP_KEYS, OOS_GATE_DELTA_KEYS, _canonical_gate_key,
    )

    req_all = set(tournament_config.get("eligible_requires_all", []) or [])
    req_any = set(tournament_config.get("eligible_requires_any", []) or [])
    used = req_all | req_any
    unknown = sorted(k for k in used if _canonical_gate_key(k) not in OOS_CONDITION_MAP_KEYS)
    # Issue #760 — nur ELIGIBLE_REQUIRES_ALL-Mitglieder werden korrelationsseitig ueberhaupt
    # betrachtet (eligible_requires_any fliesst nur gebündelt als "any_condition"-Proxy ein, siehe
    # reward._active_gate_collinearity_keys) — die Delta-Spalten-Pruefung gilt daher nur fuer ALL.
    no_delta_column = sorted(
        k for k in req_all
        if _canonical_gate_key(k) in OOS_CONDITION_MAP_KEYS
        and _canonical_gate_key(k) not in OOS_GATE_DELTA_KEYS
    )
    # Issue #765 — Schema-Text-Drift: eine explizite Marker-Behauptung, die die tatsaechliche Liste
    # nicht (mehr) widerspiegelt.
    canonical_all = {_canonical_gate_key(k) for k in req_all}
    canonical_any = {_canonical_gate_key(k) for k in req_any}
    schema_fields = (tournament_config.get("_schema") or {}).get("fields") or {}
    stale_claims = []
    for field_key, text in schema_fields.items():
        if not isinstance(text, str):
            continue
        # Issue #765 — ein ``<key>_note``-Begleitfeld (z. B. ``oos_min_sortino_note``) beschreibt
        # denselben Gate-Key wie sein Stamm-Feld; das ``_note``-Suffix VOR der ``oos_``-Normalisierung
        # entfernen, sonst vergleicht die Pruefung faelschlich den literalen Feldnamen.
        subject_key = field_key[:-5] if field_key.endswith("_note") else field_key
        if _ELIGIBLE_ALL_CLAIM_MARKER in text and _canonical_gate_key(subject_key) not in canonical_all:
            stale_claims.append(f"{field_key} (behauptet eligible_requires_all, #765)")
        if _ELIGIBLE_ANY_CLAIM_MARKER in text and _canonical_gate_key(subject_key) not in canonical_any:
            stale_claims.append(f"{field_key} (behauptet eligible_requires_any, #765)")
    problems = (unknown + [f"{k} (kein oos_gate_deltas-Handler, #760)" for k in no_delta_column]
                + stale_claims)
    passed = not problems
    return InvariantResult(
        name="check_config_key_registry",
        passed=passed,
        expected=[],
        actual=problems,
        detail=("OK" if passed else
                f"Gate(s) ohne condition_map-Handler nach oos_-Normalisierung (#649): {unknown}; "
                f"Gate(s) in eligible_requires_all ohne oos_gate_deltas-Spalte (#760): "
                f"{no_delta_column}; Schema-Text(e) mit stale Konjunktions-Behauptung (#765): "
                f"{stale_claims}."),
    )


# Issue #785 — die Stufen, die ein PROMOTETER Kandidat (status ∈ {READY_FOR_PR,
# PROMOTE_GLOBAL_DEFAULT}) nachweisbar mit passed=True durchlaufen haben MUSS. ``deflation``/``pbo``/
# ``boundary`` sind bewusst NICHT mandatorisch — sie werden nur durchlaufen, wenn die jeweilige
# Konfiguration/Kohorte sie ueberhaupt aktiviert (z. B. deflated_selection=false), waehrend
# is_gate/confirm_or_selection/holdout auf JEDEM Promotions-Pfad (inkl. der #682/#783-Default-Route)
# zwingend durchlaufen werden.
_MANDATORY_DECISION_STAGES = ("is_gate", "confirm_or_selection", "holdout")


def check_rejection_chain_completeness(proposal: dict, decision_chain: list[dict] | None = None) -> InvariantResult:
    """Issue #654/#671/#785-Regressionswächter.

    Ein abgelehntes Proposal (``promote=False``) MUSS eine konkrete Ablehnungsursache tragen
    (``holdout_reject_detail``/``is_rejection_detail_override``, #654/#671) — nie stillschweigend
    ``None``.

    Issue #785 — Root-Cause: fuer ``status in (None, 'READY_FOR_PR')`` war dieser Check VORHER
    UNBEDINGT ``True`` (der Erfolgsfall wurde nie geprueft) — genau dort fehlte allen 37
    `#682`-Records (heute ``PROMOTE_GLOBAL_DEFAULT``, #783) eine ganze Stufe
    (``confirm_or_selection``), und 1736/1736 Studies gingen trotzdem gruen durch. Ein promoteter
    Kandidat (``status`` ∈ ``{'READY_FOR_PR', 'PROMOTE_GLOBAL_DEFAULT'}``) muss jetzt eine
    POSITIVE Nachweiskette tragen: jede Stufe in ``_MANDATORY_DECISION_STAGES`` muss im
    uebergebenen ``decision_chain`` (``report._decision_chain``-Konvention, ``{stage, passed,
    detail}``) mit ``passed=True`` vorhanden sein. Fehlt ``decision_chain`` (Legacy-Aufrufer/kein
    Report-Kontext) ⇒ FAIL (leere Kette impliziert fehlende Stufen — kein stiller Freifahrtschein)."""
    status = proposal.get("status")
    promote = status in ("READY_FOR_PR", "PROMOTE_GLOBAL_DEFAULT")
    detail_val = proposal.get("holdout_reject_detail", proposal.get("is_rejection_detail"))
    missing: list[str] = []
    if status is None:
        passed = True
    elif promote:
        chain = decision_chain if decision_chain is not None else (proposal.get("decision_chain") or [])
        stages_passed = {c.get("stage") for c in chain if c.get("passed") is True}
        missing = [s for s in _MANDATORY_DECISION_STAGES if s not in stages_passed]
        passed = not missing
    else:
        passed = detail_val is not None
    return InvariantResult(
        name="check_rejection_chain_completeness",
        passed=passed,
        expected=("alle obligatorischen decision_chain-Stufen (is_gate, confirm_or_selection, "
                  "holdout) mit passed=True bei promote=True; sonst holdout_reject_detail gesetzt"),
        actual={"status": status, "holdout_reject_detail": detail_val, "missing_stages": missing},
        detail=("OK" if passed else
                (f"status={status!r} (promote=True), aber decision_chain fehlt die Stufe(n) "
                 f"{missing} mit passed=True (#785-Invariante verletzt)." if promote else
                 f"status={status!r}, aber holdout_reject_detail ist None — Ablehnungsursache "
                 "fehlt (#654/#671-Invariante verletzt).")),
    )


def check_promotion_inference_coverage(proposal: dict, record: dict) -> InvariantResult:
    """Issue #791-Regressionswächter.

    Zwei Invarianten in einem Check: (1) ``promote=True`` ⇒ ``inference_method.promotion.applied
    == True`` (kein promoteter Kandidat ohne dokumentierte Promotions-Inferenz — auch nicht die
    `#682`/`#783`-Default-Route); (2) ``REJECT_SELECTION_PBO`` (die Study wurde von der
    Selektions-Overfit-Prüfung abgelehnt) erfordert ebenfalls eine dokumentierte Promotions-
    Inferenz — eine PBO-Ablehnung ohne benannte Methode ist nicht nachvollziehbar (Root-Cause:
    14 von 38 ``REJECT_SELECTION_PBO``-Ablehnungen trugen ``promotion: null``)."""
    status = proposal.get("status")
    promote = status in ("READY_FOR_PR", "PROMOTE_GLOBAL_DEFAULT")
    holdout_detail = proposal.get("holdout_reject_detail", proposal.get("is_rejection_detail"))
    promotion_inference = (record.get("inference_method") or {}).get("promotion") or {}
    applied = promotion_inference.get("applied")

    if promote:
        passed = applied is True
        reason = "promote=True erfordert inference_method.promotion.applied == True (#791)."
    elif holdout_detail == "REJECT_SELECTION_PBO":
        passed = applied is True
        reason = "REJECT_SELECTION_PBO erfordert eine dokumentierte Promotions-Inferenz (#791)."
    else:
        passed = True
        reason = "Nicht anwendbar (weder promote=True noch REJECT_SELECTION_PBO)."
    return InvariantResult(
        name="check_promotion_inference_coverage",
        passed=passed,
        expected="inference_method.promotion.applied == True bei promote=True oder REJECT_SELECTION_PBO",
        actual={"status": status, "holdout_reject_detail": holdout_detail,
                "promotion_applied": applied},
        detail="OK" if passed else reason,
    )


def check_log_return_coherence(trials: list[dict]) -> InvariantResult:
    """Issue #756-Regressionswächter (folgt auf #589/#620).

    Seit `_calculate_stats` (backtest_runner.py) den Sortino-Zähler auf LOG-Returns umgestellt hat,
    gilt ``sign(oos_sortino_period) == sign(oos_total_return)`` PER KONSTRUKTION für jede
    Renditesequenz (Σ log(1+rᵢ) = log(1+total_return)) — nicht mehr nur empirisch selten verletzt.
    Ein Trial mit gesetztem ``oos_coherence_violation`` (dasselbe Flag, das
    ``_assert_sortino_return_coherence`` stempelt) ist damit ein ECHTER Aggregationsdefekt, keine
    erwartete Restrate mehr. ``trials`` ist eine Liste von ``user_attrs``-artigen Dicts (#621-
    Konvention, dieselbe Form wie ``check_reward_term_variance``)."""
    violating = [i for i, t in enumerate(trials) if t.get("oos_coherence_violation") is True]
    passed = not violating
    return InvariantResult(
        name="check_log_return_coherence",
        passed=passed,
        expected=0,
        actual=len(violating),
        detail=("OK" if passed else
                f"{len(violating)} Trial(s) mit sign(oos_sortino_period) != sign(oos_total_return) "
                "TROTZ Log-Return-Umstellung (#756) — echter Aggregationsdefekt, nicht die vor #756 "
                "erwartete Volatilitäts-Drag-Restrate."),
    )


# Issue #788 — dieselbe Sentinel-Frage wie #759 (dort nur oos_win_rate) gilt fuer JEDE OOS-Metrik,
# die make_symbol_objective als Trial-User-Attr persistiert: ein nicht evaluierter Trial darf fuer
# KEINE davon eine Beobachtung tragen. Deklarative Liste statt sechs Einzel-Wächtern.
_SENTINEL_GUARDED_METRIC_KEYS = (
    "oos_win_rate", "oos_profit_factor", "oos_expectancy", "oos_total_return",
    "oos_sortino", "oos_psr", "oos_sortino_period",
)


def check_metric_sentinel_absence(trials: list[dict]) -> InvariantResult:
    """Issue #759/#788-Regressionswächter.

    Root-Cause #759: ``oos_win_rate`` kollabierte fehlende Werte (kein Trial je evaluiert, kein
    ``win_rate``-Key im Metrics-Dict) auf ``0.0`` — ununterscheidbar von einer ECHT BEOBACHTETEN
    Null. Nachgelagerte Policies (``reward.check_any_arm_reachability_live``/
    ``resolve_any_arm_policy``) rekalibrierten Schwellen aus einer Verteilung, die teils/
    ausschliesslich aus diesen Missing-Data-Sentinels bestand. Seit #759 liefert die Parsing-Schicht
    ``None`` korrekt durch (``parsing.TournamentMetrics.oos_win_rate``) — die ERZEUGUNGSSEITE
    (``run_optimization.make_symbol_objective``) stempelte aber weiterhin eine Beobachtung fuer
    NICHT evaluierte Trials (9612 betroffene Trials in 386/1736 Studies, Root-Cause-Katalog #788).

    Issue #788 — auf ALLE OOS-Metriken derselben Erzeugungsstelle erweitert (nicht mehr nur
    ``oos_win_rate``): ``oos_profit_factor``/``oos_expectancy``/``oos_total_return``/``oos_sortino``/
    ``oos_psr``/``oos_sortino_period`` (siehe ``_SENTINEL_GUARDED_METRIC_KEYS``). Diese Prüfung
    verifiziert die Invariante FEHLSCHLAGEND, wenn eine Study fuer EINEN dieser Keys eine
    Beobachtung fuer einen Trial persistiert, dessen ``oos_evaluated`` gleichzeitig ``False`` ist
    (der Sentinel-Kollaps waere genau daran erkennbar: ein nie evaluierter Trial "beobachtet"
    trotzdem eine Metrik).

    ``trials`` ist eine Liste von ``user_attrs``-artigen Dicts (#621-Konvention, dieselbe Form wie
    ``check_reward_term_variance``)."""
    violating = [
        i for i, t in enumerate(trials)
        if t.get("oos_evaluated") is False
        and any(t.get(k) is not None for k in _SENTINEL_GUARDED_METRIC_KEYS)
    ]
    passed = not violating
    return InvariantResult(
        name="check_metric_sentinel_absence",
        passed=passed,
        expected=0,
        actual=len(violating),
        detail=("OK" if passed else
                f"{len(violating)} Trial(s) mit einer OOS-Metrik-Beobachtung TROTZ "
                "oos_evaluated=False — moeglicher Missing-Data-Sentinel-Kollaps (#759/#788-"
                "Regression: None/0.0 faelschlich als echte Beobachtung gestempelt)."),
    )


_REWARD_TERM_NUMERIC_KEYS = (
    "base", "divergence", "dd_penalty", "param_pen", "turnover", "fold_dispersion", "tie_breaker",
)

# Issue #764 — Zielkorridor je Term (Anteil an der SUMME aller Term-Varianzen, siehe
# ``reward_term_variance_table``). Ein Term dauerhaft UNTER 0.02 traegt praktisch keine
# unterscheidbare Information zur Reward-Landschaft bei (Kandidat fuer Entfernung); ein Term UEBER
# 0.30 dominiert die uebrigen sechs (Kandidat fuer Herunterskalierung). Die tatsaechliche
# Kalibrierung/Entfernung ERFORDERT eine reale Kohorte (>= 50 Studies NACH Kohorte A/B, #753-#763 —
# die im Issue #764 zitierten Referenzzahlen stammen aus dem GEBROCHENEN Vor-#753-Suchregime und
# sind fuer eine Entscheidung JETZT keine gueltige Evidenz, siehe Merge-Order-Abhaengigkeit im
# Issue selbst: "vorher gibt es keine belastbare eligible Kohorte fuer die Kalibrierung").
_REWARD_TERM_VARIANCE_CORRIDOR = (0.02, 0.30)


def _eligible_reward_terms(trials: list[dict]) -> list[dict]:
    """Gemeinsame Extraktion fuer ``check_reward_term_variance``/``reward_term_variance_table``:
    die ``reward_terms``-Dicts aller Trials, die tatsaechlich OOS-evaluiert wurden UND einer
    eligiblen/pareto-Kohorte angehoeren (der einzige Ast, auf dem ein Terme-Vergleich sinnvoll ist —
    ein unevaluierbarer Trial traegt keine Reward-Term-Zerlegung)."""
    return [
        t.get("reward_terms") for t in trials
        if t.get("oos_evaluated") is True and t.get("reward_terms")
        and t["reward_terms"].get("branch") in ("eligible", "per_symbol", "pareto")
    ]


def reward_term_variance_table(trials: list[dict]) -> list[dict[str, Any]]:
    """Issue #764 — die VOLLSTAENDIGE Varianz-Tabelle je Reward-Term fuer den #742-Report, statt nur
    der binaeren inert/nicht-inert-Klassifikation von ``check_reward_term_variance``: je Term
    ``std`` (Streuung ueber die eligible Kohorte) und ``var_contrib`` (Anteil der Term-VARIANZ an der
    SUMME aller sieben Term-Varianzen, ``var_k / Σ var_j`` — die Groesse, gegen die der
    ``_REWARD_TERM_VARIANCE_CORRIDOR`` gemessen wird). ``in_target_corridor`` markiert Terme
    ausserhalb ``[0.02, 0.30]`` (Kandidaten fuer Entfernung bzw. Herunterskalierung, siehe #764 —
    die tatsaechliche Entscheidung braucht eine reale Kohorte, diese Tabelle liefert nur die Evidenz
    dafuer).

    Leere Liste bei < 2 eligiblen Trials mit ``reward_terms`` (keine Varianz-Aussage moeglich,
    konsistent zu ``check_reward_term_variance``)."""
    eligible_terms = _eligible_reward_terms(trials)
    if len(eligible_terms) < 2:
        return []
    lo, hi = _REWARD_TERM_VARIANCE_CORRIDOR
    variances = {
        k: statistics.pvariance([float(t.get(k, 0.0)) for t in eligible_terms])
        for k in _REWARD_TERM_NUMERIC_KEYS
    }
    total_var = sum(variances.values()) or 1.0
    table = []
    for k in _REWARD_TERM_NUMERIC_KEYS:
        var_contrib = variances[k] / total_var
        table.append({
            "term": k,
            "std": round(variances[k] ** 0.5, 6),
            "var_contrib": round(var_contrib, 6),
            "in_target_corridor": bool(lo <= var_contrib <= hi),
        })
    return table


def check_gate_collinearity_consolidation(study_records: list[dict], *,
                                          max_affected_fraction: float = 0.20) -> InvariantResult:
    """Issue #776/#792-Regressionswächter.

    ``reward.assert_eligible_requires_all_not_redundant`` markiert bereits JE STUDY, ob
    ``eligible_requires_all`` noch ein von der LIVE-Kohorte als redundant ausgewiesenes
    Gate-Paar enthält (siehe ``report._study_record``s ``gate_collinearity_unconsolidated``-Feld).
    Diese sweep-weite Prüfung konsumiert den #679-Alarm ENDLICH (Root-Cause #776: der Alarm war
    reine Telemetrie ohne Konsument) — FAIL, wenn >= 20 % der Studies eines Laufs ein
    unkonsolidiertes Gate melden. Bricht NICHT automatisch die Config (welches Gate konsolidiert
    wird, bleibt eine bewusste PR-Entscheidung) — macht die Notwendigkeit aber unübersehbar."""
    with_data = [r for r in study_records if "gate_collinearity_unconsolidated" in r]
    if not with_data:
        return InvariantResult(
            name="check_gate_collinearity_consolidation",
            passed=True,
            expected=f"< {max_affected_fraction:.0%} Studies mit unkonsolidiertem Gate",
            actual=None,
            detail="Keine Studies mit Gate-Kollinearitäts-Telemetrie — nicht anwendbar.",
        )
    affected = sum(1 for r in with_data if r.get("gate_collinearity_unconsolidated"))
    fraction = affected / len(with_data)
    passed = fraction < max_affected_fraction
    return InvariantResult(
        name="check_gate_collinearity_consolidation",
        passed=passed,
        expected=f"< {max_affected_fraction:.0%} Studies mit unkonsolidiertem Gate",
        actual=round(fraction, 4),
        detail=("OK" if passed else
                f"{affected}/{len(with_data)} Studies ({fraction:.1%}) melden ein von der LIVE-"
                "Kohorte als redundant ausgewiesenes eligible_requires_all-Gate (#776/#679-Alarm)."),
    )


def check_budget_execution(study_records: list[dict], *, min_median: float = 0.5) -> InvariantResult:
    """Issue #770-Regressionswächter (siebter Invarianten-Check, Anschluss #743/#773).

    ``run_optimization.compute_budget_execution`` stempelt je Study ``budget_executed_fraction``
    (siehe dort). Diese sweep-weite Prüfung meldet FAIL, wenn der MEDIAN ueber alle Studies eines
    Laufs unter ``min_median`` liegt — die #768/#769-Klasse von Defekt (ein grosser Teil des
    konfigurierten Suchbudgets wird nie ausgefuehrt) bleibt sonst nur durch externe Log-Prosa-
    Rekonstruktion sichtbar (genau das, was den #768-Regress nach dem #753-Merge unbemerkt liess).

    ``study_records`` ist eine Liste von Report-Study-Eintraegen (``{'budget_executed_fraction': ...}``,
    #742-Konvention). Reine Telemetrie-Invariante — beruehrt NIE einen Reward-/Promotion-Pfad."""
    fractions = [
        r.get("budget_executed_fraction") for r in study_records
        if r.get("budget_executed_fraction") is not None
    ]
    if not fractions:
        return InvariantResult(
            name="check_budget_execution",
            passed=True,
            expected=f">= {min_median}",
            actual=None,
            detail="Keine Studies mit budget_executed_fraction — nicht anwendbar.",
        )
    median = statistics.median(fractions)
    passed = median >= min_median
    return InvariantResult(
        name="check_budget_execution",
        passed=passed,
        expected=f"median(budget_executed_fraction) >= {min_median}",
        actual=round(median, 4),
        detail=("OK" if passed else
                f"median(budget_executed_fraction)={median:.4f} < {min_median} ueber "
                f"{len(fractions)} Studies — ein grosser Teil des konfigurierten Suchbudgets wird "
                "nicht ausgefuehrt (#768/#769-Fehlerklasse)."),
    )


def check_reward_term_variance(trials: list[dict], *, inert_ratio: float = 0.01) -> InvariantResult:
    """Verallgemeinerung von ``REWARD_TERM_INERT`` (run_optimization.py, Issue #621): statt einer
    einzelnen WARNING-Zeile pro inertem Term liefert diese Pruefung die VOLLSTAENDIGE Liste ueber
    alle eligiblen Trials einer Study. Ein Term gilt als inert, wenn seine Streuung < ``inert_ratio``
    der Streuung des Gesamt-Rewards ist — derselbe Schwellenwert wie das Original
    (``std_k < 0.01 * rew_std``).

    ``trials`` ist eine Liste von ``user_attrs``-artigen Dicts (je Trial ``oos_evaluated`` +
    ``reward_terms``), NICHT Optuna-``Trial``-Objekte — pure Funktion, synthetisch testbar."""
    eligible_terms = _eligible_reward_terms(trials)
    if len(eligible_terms) < 2:
        return InvariantResult(
            name="check_reward_term_variance",
            passed=True,
            expected=[],
            actual=[],
            detail="< 2 eligible Trials mit reward_terms — keine Varianz-Aussage moeglich.",
        )
    rew_vals = [
        (t.get("base", 0.0) - t.get("divergence", 0.0) - t.get("dd_penalty", 0.0)
         - t.get("param_pen", 0.0) - t.get("turnover", 0.0) - t.get("fold_dispersion", 0.0)
         + t.get("tie_breaker", 0.0))
        for t in eligible_terms
    ]
    rew_std = statistics.pstdev(rew_vals)
    inert_terms = []
    for k in _REWARD_TERM_NUMERIC_KEYS:
        vals = [float(t.get(k, 0.0)) for t in eligible_terms]
        std_k = statistics.pstdev(vals)
        if std_k < inert_ratio * rew_std:
            inert_terms.append(k)
    passed = not inert_terms
    return InvariantResult(
        name="check_reward_term_variance",
        passed=passed,
        expected=[],
        actual=inert_terms,
        detail=("OK" if passed else
                f"Reward-Term(e) praktisch inert (std < {inert_ratio:.2%} von "
                f"reward_std={rew_std:.6f}): {inert_terms}."),
    )
