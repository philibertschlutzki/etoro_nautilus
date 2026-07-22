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


def check_config_key_registry(tournament_config: dict) -> InvariantResult:
    """Issue #649/#760-Regressionswächter.

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
    """
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
    problems = unknown + [f"{k} (kein oos_gate_deltas-Handler, #760)" for k in no_delta_column]
    passed = not problems
    return InvariantResult(
        name="check_config_key_registry",
        passed=passed,
        expected=[],
        actual=problems,
        detail=("OK" if passed else
                f"Gate(s) ohne condition_map-Handler nach oos_-Normalisierung (#649): {unknown}; "
                f"Gate(s) in eligible_requires_all ohne oos_gate_deltas-Spalte (#760): "
                f"{no_delta_column}."),
    )


def check_rejection_chain_completeness(proposal: dict) -> InvariantResult:
    """Ein abgelehntes Proposal (``status != READY_FOR_PR``) MUSS eine konkrete Ablehnungsursache
    tragen (``holdout_reject_detail``/``is_rejection_detail_override``, #654/#671) — nie
    stillschweigend ``None``. ``status is None``/``READY_FOR_PR`` gilt als nicht-abgelehnt (kein
    Fehlschlag der Kette moeglich)."""
    status = proposal.get("status")
    detail_val = proposal.get("holdout_reject_detail", proposal.get("is_rejection_detail"))
    passed = True if status in (None, "READY_FOR_PR") else detail_val is not None
    return InvariantResult(
        name="check_rejection_chain_completeness",
        passed=passed,
        expected="holdout_reject_detail gesetzt bei status != READY_FOR_PR",
        actual={"status": status, "holdout_reject_detail": detail_val},
        detail=("OK" if passed else
                f"status={status!r}, aber holdout_reject_detail ist None — Ablehnungsursache "
                "fehlt (#654/#671-Invariante verletzt)."),
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


def check_metric_sentinel_absence(trials: list[dict]) -> InvariantResult:
    """Issue #759-Regressionswächter.

    Root-Cause #759: ``oos_win_rate`` kollabierte fehlende Werte (kein Trial je evaluiert, kein
    ``win_rate``-Key im Metrics-Dict) auf ``0.0`` — ununterscheidbar von einer ECHT BEOBACHTETEN
    Null. Nachgelagerte Policies (``reward.check_any_arm_reachability_live``/
    ``resolve_any_arm_policy``) rekalibrierten Schwellen aus einer Verteilung, die teils/
    ausschliesslich aus diesen Missing-Data-Sentinels bestand. Seit #759 liefert die Parsing-Schicht
    ``None`` korrekt durch (``parsing.TournamentMetrics.oos_win_rate``) — diese Prüfung verifiziert
    die Invariante FEHLSCHLAGEND, wenn eine Study eine ``oos_win_rate``-Beobachtung fuer einen Trial
    persistiert, dessen ``oos_evaluated`` gleichzeitig ``False`` ist (der Sentinel-Kollaps waere
    genau daran erkennbar: ein nie evaluierter Trial "beobachtet" trotzdem eine win_rate).

    ``trials`` ist eine Liste von ``user_attrs``-artigen Dicts (#621-Konvention, dieselbe Form wie
    ``check_reward_term_variance``)."""
    violating = [
        i for i, t in enumerate(trials)
        if t.get("oos_evaluated") is False and t.get("oos_win_rate") is not None
    ]
    passed = not violating
    return InvariantResult(
        name="check_metric_sentinel_absence",
        passed=passed,
        expected=0,
        actual=len(violating),
        detail=("OK" if passed else
                f"{len(violating)} Trial(s) mit oos_win_rate-Beobachtung TROTZ oos_evaluated=False "
                "— moeglicher Missing-Data-Sentinel-Kollaps (#759-Regression: None faelschlich zu "
                "0.0 kollabiert)."),
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
