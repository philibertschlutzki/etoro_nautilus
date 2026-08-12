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

import logging
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from automation.optimizer._contracts import MAX_BARS_IN_TRADE_HARD_CAP as _MAX_BARS_IN_TRADE_CAP
from automation.optimizer._contracts import BAR_SECONDS_DEFAULT as _BAR_SECONDS_DEFAULT

_log = logging.getLogger("optimizer")


@dataclass(frozen=True)
class InvariantResult:
    name: str
    passed: bool
    expected: Any
    actual: Any
    detail: str
    # Issue #849 — Schweregrad, damit Sektion 5 des #832-Berichts nach Dringlichkeit statt nach
    # Auftrittsreihenfolge sortieren kann. "blocking" macht eine Study ungültig (siehe #839
    # check_holding_time_cap); Default "medium" für alle bisherigen Checks (rückwärtskompatibel —
    # kein bestehender Aufrufer muss das Feld setzen).
    severity: str = "medium"
    # Issue #971 Fix Punkt 2 (Pitfall #303 in AGENTS.md) — Herkunftspflicht für ``severity=
    # 'blocking'``-Invarianten: ein Wächter, der einen Lauf abbricht, muss seine Rechengrundlage
    # im selben Event mitliefern, sonst ist sie aus der Telemetrie nicht nachrechenbar (genau das
    # Symptom, das #971 aufdeckte). ``provenance`` ist optional (``None`` für alle bestehenden,
    # nicht-blockierenden Checks — rückwärtskompatibel) und trägt je offending Study/Symbol
    # ``{numerator, denominator, numerator_definition, source_field}``.
    provenance: dict[str, Any] | None = None
    # Issue #981 (Katalog C, P1, Pitfall #312 in AGENTS.md) — ein dritter Zustand neben PASS/FAIL:
    # ein Wächter, dessen EIGENE Eingabe zu grob quantisiert ist, um zwischen "defekt" und "nicht
    # messbar" zu unterscheiden, soll das explizit sagen, statt FAIL zu melden ("nicht messbar" wird
    # sonst als "defekt" fehlinterpretiert — genau die Ursachenzuschreibung, die zu einer
    # Denylistung führen kann, obwohl das Paar funktioniert). ``inconclusive=True`` impliziert
    # ``passed=True`` (kein Abbruch/Alarm), macht den Zustand aber im Report von einem echten,
    # sauberen PASS unterscheidbar.
    inconclusive: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name,
            # Issue #849 — Uebergangs-Alias: report.py's INVARIANT_CHECK_FAILED-Event mappte
            # bereits korrekt auf "check" (result.name), waehrend summary_de.py "check" LAS, ohne
            # dass to_dict() diesen Schluessel je geschrieben hat (519x "**None**" im Bericht).
            # Beide Schluessel tragen denselben Wert, bis alle Konsumenten auf "name" migriert sind.
            "check": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
            "severity": self.severity,
        }
        if self.provenance is not None:
            d["provenance"] = self.provenance
        if self.inconclusive:
            d["inconclusive"] = True
        return d


# Issue #902 — KEINE eigene ``_BAR_SECONDS``-Kopie mehr. Vor diesem Fix pflegte dieses Modul ein
# eigenes ``_BAR_SECONDS = 3600.0``-Literal NEBEN ``run_optimization.py``s unabhängiger Kopie
# desselben Werts (Pitfall #271, dritte Instanz) — beide konnten divergieren, ohne dass ein Test es
# gemerkt hätte. Die einzige verbleibende Referenz ist ``_contracts.BAR_SECONDS_DEFAULT``
# (importiert oben als ``_BAR_SECONDS_DEFAULT``).


def resolve_effective_bar_cap(sampled_params: dict | None, *, strategy: str | None = None,
                              strategy_defaults: dict | None = None,
                              global_cap: float = _MAX_BARS_IN_TRADE_CAP) -> tuple[float, str]:
    """Issue #861 — gemeinsame Referenz-Auflösung für den Zeitbox-Deckel, konsumiert von
    ``compute_trial_timebox_violations`` UND ``check_holding_time_cap`` (vorher zwei unabhängig
    implementierte Antworten auf dieselbe Fachfrage "hat dieser Trial seinen Zeitbox-Vertrag
    eingehalten?" — Pitfall #271: ein Trial mit gesampeltem Cap 12 und 20 Bars Haltedauer war für
    die alte ``check_holding_time_cap`` sauber (20 < 24, globaler Deckel), für
    ``compute_trial_timebox_violations`` eine Verletzung).

    Reihenfolge: gesampelter Wert (Trial-Suchraum, ``sampled_params['max_bars_in_trade']``) →
    ``strategy_defaults.json``-Eintrag der Strategie → globaler #714/GR-01-Deckel. Rückgabe
    ``(cap_bars, source)`` mit ``source in {'sampled', 'default', 'global'}`` — das
    ``timebox_cap_source``-Telemetriefeld macht sichtbar, welche Strategien (heute typischerweise
    ~die Hälfte, siehe ``SmaCrossoverStrategy``, das ``max_bars_in_trade`` gar nicht sampelt) auf
    den ungenaueren Fallback zurückfallen."""
    sampled = sampled_params or {}
    cap = sampled.get("max_bars_in_trade")
    if cap is not None:
        return float(cap), "sampled"
    if strategy and strategy_defaults:
        default_cap = (strategy_defaults.get(strategy) or {}).get("max_bars_in_trade")
        if default_cap is not None:
            return float(default_cap), "default"
    return float(global_cap), "global"


def compute_trial_timebox_violations(trial_attrs: list[dict], *,
                                     bar_seconds: float,
                                     strategy: str | None = None,
                                     strategy_defaults: dict | None = None,
                                     max_bars_in_trade_cap: float = _MAX_BARS_IN_TRADE_CAP,
                                     tolerance_bars: float = 3.0) -> dict[str, Any]:
    """Issue #839 — je-Trial-Zeitbox-Verletzung: vergleicht die tatsächlich beobachtete Haltedauer
    gegen den für DIESEN Trial GESAMPELTEN ``max_bars_in_trade`` (``sampled_params``, seit #669 je
    Trial mitgeführt) — fehlt dieser Wert (Strategie sampelt ihn nicht), gegen den globalen
    #714/GR-01-Deckel (dieselbe konservative obere Schranke wie ``check_holding_time_cap``).

    Issue #858 — ``tolerance_bars`` (Default 3.0 = ``exit_close_max_bars + 1``, siehe
    ``tournament.json['timebox_execution_slack_bars']``) ist die zulässige Ausführungs-Latenz
    zwischen Entscheidungs-Bar und Fill: der #836-Watchdog toleriert KONSTRUKTIONSGEMÄSS bis zu
    ``exit_close_max_bars`` Bars Verzögerung auf eine Cancel-Bestätigung, bevor er den Markt-Close
    erzwingt, zzgl. eines Bars unvermeidlicher Fill-Latenz (der Exit wird auf dem GESCHLOSSENEN Bar
    entschieden, die Order füllt frühestens beim nächsten Bar-Ereignis). Der vorherige Default
    0.01 Bars (≈ 36 Sekunden) bestrafte exakt die vom Exit-Mechanismus selbst vorgesehene
    Verzögerung als Vertragsbruch (Pitfall #271) — 206 von 462 Studies eines Referenzlaufs wurden
    dadurch auf Study-Ebene verworfen, obwohl der Exit-Pfad nach #836/#837 intakt war.

    Issue #902 — ``bar_seconds`` ist ein PFLICHTPARAMETER (kein Default mehr): ein Aufruf ohne ihn
    wirft ``TypeError`` statt (wie bis #858, drei Läufe lang folgenlos) auf den 24/7-Stundenraster-
    Default zurückzufallen und nur zu WARNEN (Pitfall #271, #280). Aufrufer lösen ``bar_seconds``
    bevorzugt aus der #900-Bar-Qualitäts-Telemetrie (``median_delta_t_s``) auf, mit
    ``_contracts.BAR_SECONDS_DEFAULT`` als fail-loud protokolliertem Fallback.

    Issue #903 — TRIAL- und ROUND-TRIP-Ebene werden GETRENNT gezählt: ``timebox_violating_trials``/
    ``timebox_evaluated_trials`` (mind. 1 verletzender Round-Trip im Trial — treibt die
    #878-Study-Toleranz) UND ``timebox_violating_round_trips``/``timebox_evaluated_round_trips``
    (Diagnose-relevant — welcher ANTEIL der Trades tatsächlich verletzt, nicht nur ob der
    Trial-Maximum-Trade es tut). Die Round-Trip-Ebene braucht die rohe ``oos_holding_times_s``-Liste
    (#899); fehlt sie (Pre-#899-JSON), fällt sie auf den einzigen verfügbaren Punkt
    (``oos_max_holding_time_s``) als konservative Ein-Element-Approximation zurück
    (rückwärtskompatibel, zählt dann höchstens 1 Round-Trip je Trial).

    Ein Treffer bedeutet: dieser Trial wurde auf einer Simulation bewertet, die den eigenen
    Zeit-Exit-Vertrag verletzt hat (Bug im Exit-Pfad, siehe #836/#837) — seine Metriken sind dann
    keine gültige Grundlage für Eligibility/Reward/Promotion. Reine Funktion über bereits geladene
    ``trial.user_attrs``-Dicts, unabhängig von Optuna-Objekten (siehe Moduldocstring)."""
    violating_trials = 0
    evaluated_trials = 0
    violating_round_trips = 0
    evaluated_round_trips = 0
    cap_source_counts: dict[str, int] = {}
    p95_holding_times: list[float] = []
    for attrs in trial_attrs or []:
        holding_s = attrs.get("oos_max_holding_time_s")
        if holding_s is None:
            continue
        evaluated_trials += 1
        cap_bars, cap_source = resolve_effective_bar_cap(
            attrs.get("sampled_params"), strategy=strategy, strategy_defaults=strategy_defaults,
            global_cap=max_bars_in_trade_cap)
        cap_source_counts[cap_source] = cap_source_counts.get(cap_source, 0) + 1
        cap_s = (cap_bars + tolerance_bars) * bar_seconds

        round_trip_holds = attrs.get("oos_holding_times_s") or [holding_s]
        trial_violated = False
        for ht in round_trip_holds:
            evaluated_round_trips += 1
            if ht > cap_s:
                violating_round_trips += 1
                trial_violated = True
        if trial_violated:
            violating_trials += 1
        p95_h = attrs.get("oos_p95_holding_time_s")
        if p95_h is not None:
            p95_holding_times.append(float(p95_h))

    trial_fraction = round(violating_trials / evaluated_trials, 4) if evaluated_trials else 0.0
    rt_fraction = round(violating_round_trips / evaluated_round_trips, 4) if evaluated_round_trips else 0.0
    return {
        "timebox_violating_trials": violating_trials,
        "timebox_evaluated_trials": evaluated_trials,
        "timebox_violation_fraction": trial_fraction,
        # Issue #903 Fix 1/2 — Round-Trip-(Trade-)Ebene, GETRENNT von der Trial-Ebene oben. Die
        # #878-Study-Toleranz wirkt auf DIESER Grösse (siehe confirm.py), nicht auf der Trial-Ebene.
        "timebox_violating_round_trips": violating_round_trips,
        "timebox_evaluated_round_trips": evaluated_round_trips,
        "timebox_round_trip_violation_fraction": rt_fraction,
        # Issue #903 Fix 3 — Median der je-Trial-P95-Haltedauer (bereits vorliegende Grösse,
        # #832) als Verletzungs-INTENSITÄTS-Signal, unabhängig vom Fraction-Zähler oben.
        "timebox_violation_intensity_p95": (
            statistics.median(p95_holding_times) if p95_holding_times else None),
        "timebox_violated": violating_trials > 0,
        # Issue #861 — Verteilung der Deckel-Referenzquelle über die ausgewerteten Trials (Report-
        # Telemetrie, macht sichtbar, wie oft der ungenauere strategy_defaults/global-Fallback statt
        # des gesampelten Werts greift).
        "timebox_cap_source_counts": cap_source_counts,
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


def check_family_n_periods_homogeneity(holdout_metrics: dict, *, max_ratio: float = 4.0) -> InvariantResult:
    """Issue #845-Regressionswächter.

    ``confirm.confirm_per_symbol_promotion`` berechnet ``deflation_var`` (die Kohorten-Varianz, die
    ``deflation_sr0`` treibt) über ALLE eligiblen Trials der DSR-Kohorte, als traegen sie eine
    annaehernd konstante Stichprobengroesse T (``oos_n_periods``) — dieselbe Voraussetzung, die den
    Lo-2002-T-bewussten Varianz-Floor motiviert. In der Praxis wurde ein Faktor 45 zwischen dem
    kleinsten und groessten ``oos_n_periods`` derselben Kohorte beobachtet: die gepoolten
    per-Trial-Sortinos sind dann nicht kommensurabel, DSR/PSR über die Kohorte hinweg nicht
    vergleichbar. Der Fix (confirm.py, ``deflation_n_periods_ratio``) unterdrückt DSR/SR₀ mit
    ``deflation_skipped_reason='N_PERIODS_HETEROGENEOUS'``, sobald
    ``max(oos_n_periods)/min(oos_n_periods)`` (der Kohorte) ``deflation_max_n_periods_ratio``
    (tournament.json, Default 4.0) überschreitet — dieser Wächter prüft, dass diese Unterdrückung
    tatsächlich griff (kein ``deflated_dsr``/``deflation_dsr_z`` trotz überschrittener Ratio).

    Issue #1013 (Katalog #858, Pitfall #345) — VORHER prüfte dieser Wächter hart die Semantik von
    ``deflation_heterogeneity_policy='suppress_dsr'`` (Kohorte heterogen ⇒ NIE ein DSR-Signal),
    UNABHÄNGIG davon, welche Politik tatsächlich konfiguriert ist. Unter dem seit #865 aktiven
    ``'per_stratum'``-Default berechnet die Politik SR₀/DSR ABSICHTLICH auf dem (kommensurablen)
    Stratum des promoteten Trials neu und BEHÄLT ein DSR-Signal — das ist ihr dokumentierter Zweck
    (confirm.py, ``_stratify_cohort_by_n_periods``), keine fehlgeschlagene Suppression. Unter der
    ausgelieferten Konfiguration FAILte dieser Wächter also GENAU DANN, wenn die Politik korrekt
    arbeitete — vier garantierte False Positives (severity 'high') in jedem Lauf. Der Wächter
    konsumiert jetzt ``deflation_heterogeneity_policy`` und prüft je Politik die JEWEILS zutreffende
    Semantik (Pitfall #345 — "eine Invariante, die eine Politik-Konstante ignoriert, ist unter der
    falschen Politik invertiert").

    ``holdout_metrics`` ist derselbe Export-Dict wie bei ``check_sr0_coherence``
    (``proposal['holdout']['symbol']``). ``deflation_n_periods_ratio is None`` ⇒ keine Kohorte mit
    >= 2 Mitgliedern mit bekanntem ``oos_n_periods`` (nicht anwendbar, PASS)."""
    ratio = holdout_metrics.get("deflation_n_periods_ratio")
    if ratio is None:
        return InvariantResult(
            name="check_family_n_periods_homogeneity",
            passed=True,
            expected=f"<= {max_ratio}",
            actual=None,
            detail="deflation_n_periods_ratio unbekannt (keine >=2-Kohorte mit bekanntem "
                    "oos_n_periods) — nicht anwendbar.",
        )
    exceeded = ratio > max_ratio
    has_dsr_signal = (holdout_metrics.get("deflated_dsr") is not None
                       or holdout_metrics.get("deflation_dsr_z") is not None)
    if not exceeded:
        return InvariantResult(
            name="check_family_n_periods_homogeneity",
            passed=True,
            expected=f"<= {max_ratio}",
            actual=ratio,
            severity="high",
            detail="OK (Kohorte kommensurabel, keine Suppression erwartet).",
        )
    # Issue #1013 — konsumiert die tatsächlich konfigurierte Politik; fehlt sie im Export (Legacy-
    # Proposal vor #865 oder ein Test-Fixture), gilt derselbe Default wie confirm.py selbst
    # (``tournament_cfg.get('deflation_heterogeneity_policy', 'per_stratum')``).
    policy = holdout_metrics.get("deflation_heterogeneity_policy", "per_stratum")
    if policy in ("suppress_dsr", "reject"):
        passed = not has_dsr_signal
        expected = f"policy={policy!r}: kein deflated_dsr/deflation_dsr_z bei ratio > {max_ratio}"
        detail = ("OK" if passed else
                   f"deflation_n_periods_ratio={ratio:.3g} > max_ratio={max_ratio}, policy="
                   f"{policy!r}, aber deflated_dsr/deflation_dsr_z sind trotzdem gesetzt — die "
                   "#845-Heterogenitäts-Suppression hat nicht gegriffen.")
    elif policy == "per_stratum":
        if not has_dsr_signal:
            # Das gewählte Stratum hatte < 2 Mitglieder ⇒ Fallback auf suppress_dsr-Verhalten
            # (confirm.py) — korrekt, kein Reject-Grund.
            passed = True
            expected = "policy='per_stratum': DSR-Signal ODER Stratum < 2 Mitglieder (Fallback)"
            detail = "OK (Stratum < 2 Mitglieder — korrekter Fallback auf Suppression)."
        else:
            stratum_id = holdout_metrics.get("deflation_stratum_id")
            stratum_n = holdout_metrics.get("deflation_stratum_n")
            stratum_ratio = holdout_metrics.get("deflation_stratum_n_periods_ratio")
            passed = (
                stratum_id is not None and stratum_n is not None and stratum_n >= 2
                and stratum_ratio is not None and stratum_ratio <= max_ratio
            )
            expected = (
                f"policy='per_stratum': deflation_stratum_id/deflation_stratum_n gesetzt, "
                f"deflation_stratum_n >= 2, deflation_stratum_n_periods_ratio <= {max_ratio}")
            detail = ("OK (DSR korrekt auf einem kommensurablen Stratum neu berechnet)." if passed
                       else f"deflation_n_periods_ratio={ratio:.3g} > max_ratio={max_ratio}, "
                            f"policy='per_stratum', aber stratum_id={stratum_id!r}, "
                            f"stratum_n={stratum_n!r}, stratum_n_periods_ratio={stratum_ratio!r} "
                            f"belegen KEINE kommensurable Stratifizierung — das DSR-Signal steht "
                            f"auf einer ungeprüften Grundlage.")
    else:
        # Unbekannte Politik: confirm.py selbst bricht dann bereits fail-loud ab (Katalog-
        # ValueError); dieser Wächter bleibt defensiv statt eine dritte Semantik zu raten.
        passed = False
        expected = "deflation_heterogeneity_policy ∈ {'suppress_dsr', 'reject', 'per_stratum'}"
        detail = f"Unbekannte Politik {policy!r} — nicht auswertbar."
    return InvariantResult(
        name="check_family_n_periods_homogeneity",
        passed=passed,
        expected=expected,
        actual={"deflation_n_periods_ratio": ratio, "deflation_heterogeneity_policy": policy,
                "has_dsr_signal": has_dsr_signal,
                "deflation_stratum_n_periods_ratio": holdout_metrics.get(
                    "deflation_stratum_n_periods_ratio")},
        severity="high",
        detail=detail,
    )


def check_guard_reference_coherence(configured_min_periods: float | None,
                                    observed_n_periods_medians: list[float], *,
                                    max_factor: float = 2.0,
                                    reference_mode: str | None = None,
                                    observed_guard_reference_sources: list[str] | None = None) -> InvariantResult:
    """Issue #862/#882/#901 — FAIL, wenn der konfigurierte ``tournament.json['sortino_numeric_
    guard_min_periods']``-Referenzwert um mehr als ``max_factor`` vom über den Lauf beobachteten
    Median der tatsächlichen ``oos_n_periods``-Verteilung abweicht (in beide Richtungen).

    Issue #901 (siebte Wiederkehr Pitfall #267) — Root-Cause des #882-Fix-3-Fehlers: dieser Zweig
    liess ``reference_mode == 'family_median'`` UNBEDINGT passieren, mit der Begründung, der
    absolute Anker sei dann "inert". Das war eine Annahme über den CODE, nicht eine Messung an
    ihm — ``backtest_runner._effective_sortino_numeric_guard`` fiel VOR dem #901-Fix ohne
    bereitgestelltes ``family_median_n_periods`` still auf den absoluten Anker zurück UND
    stempelte ``guard_reference_source='absolute'``. Ein Wächter, der die Konfiguration nur mit
    sich selbst vergleicht (statt mit der tatsächlich gestempelten Telemetrie), ist tautologisch
    und kann diese Fehlerklasse per Konstruktion nicht fangen (Pitfall #288).

    Fix — unter ``reference_mode == 'family_median'`` prüft dieser Wächter jetzt
    ``observed_guard_reference_sources`` (die gesammelten ``guard_reference_source``-Werte aus
    ``SORTINO_GUARD_TRIPPED``/``SORTINO_GUARD_REFERENCE_UNAVAILABLE``-Diagnosen des Laufs, siehe
    ``parsing.py['inference_diagnostics']``): TRIFFT ``'absolute'`` unter ihnen auf — ein Event, das
    den (verbotenen) alten Fail-Open-Pfad genommen hätte — FAILt der Wächter blocking. Der ehrliche
    dritte Zustand ``'family_median_unavailable'`` (Issue #901 Fix 1) ist KEIN Widerspruch (er lügt
    nicht über die verwendete Referenz) und lässt den Check PASSen, solange kein ``'absolute'``
    auftaucht.

    Ohne ``observed_mode == 'family_median'`` bleibt die ABSOLUTE Referenzprüfung unverändert:

    Root-Cause: ein Referenzwert, der gegen eine ABGELEITETE Grösse kalibriert wurde (hier:
    ``n_periods``, die informative Periodenzahl), wird ungültig, sobald die Definition dieser
    Grösse sich ändert (#823 hat sie von der vollen 24/7-Bar-Achse auf die informative Teilmenge
    umgestellt — Faktor ~13,5 kleiner). #844 hat den Wert (1600) NIE gegen die neue Definition
    nachgezogen; dieser Wächter hätte den Fehler nach dem ersten Symbol gemeldet, statt ihn erst
    durch Rückrechnung aus 689 Log-Zeilen sichtbar werden zu lassen (Pitfall #274).

    ``observed_n_periods_medians``: je Study der Median von ``oos_n_periods`` über ihre
    ``oos_evaluated``-Trials (``report._study_record``). Leer/``configured_min_periods is None``
    ⇒ nicht anwendbar (PASS)."""
    if reference_mode == "family_median":
        # Issue #915 — 'absolute' bleibt der verbotene Fail-Open-Pfad (Pitfall #267/#901).
        # 'family_median_unavailable' ist SEIT #915 EBENFALLS ein FAIL: der Zustand ist ehrlich
        # (er behauptet keine falsche Referenz), aber im Produktivbetrieb unzulässig — er bedeutet
        # 'kein Guard, sortino/psr=None' für den betroffenen Trial. 'absolute_bootstrap' (#913
        # Fix 2 — Kaltstart-Phase MIT explizit gestempelter, unterscheidbarer Quelle) bleibt
        # zulässig und PASSt weiterhin, ebenso wie 'family_median' selbst.
        offending_sources = sorted({
            s for s in (observed_guard_reference_sources or [])
            if s in ("absolute", "family_median_unavailable")
        })
        passed = not offending_sources
        return InvariantResult(
            name="check_guard_reference_coherence",
            passed=passed,
            expected="kein guard_reference_source in {'absolute', 'family_median_unavailable'} "
                     "unter sortino_numeric_guard_reference='family_median'",
            actual=offending_sources if offending_sources else None,
            severity="blocking",
            detail=("sortino_numeric_guard_reference='family_median' — kein Event nahm den "
                    "fail-open absolute-Pfad oder blieb unbewertet." if passed else
                    "sortino_numeric_guard_reference='family_median', aber mindestens ein Event "
                    "meldet guard_reference_source in {'absolute', 'family_median_unavailable'} — "
                    "entweder der verbotene Fail-Open-Pfad (Issue #901) oder ein ehrlicher, aber "
                    "im Produktivbetrieb unzulässiger Kaltstart ohne Bootstrap-Guard (Issue #915)."),
        )
    if configured_min_periods is None or not observed_n_periods_medians:
        return InvariantResult(
            name="check_guard_reference_coherence",
            passed=True,
            expected=f"Faktor <= {max_factor} zwischen konfiguriertem Referenzwert und "
                     "beobachtetem Median(n_periods)",
            actual=None,
            severity="blocking",
            detail="sortino_numeric_guard_min_periods nicht konfiguriert oder keine Studies mit "
                   "n_periods-Telemetrie — nicht anwendbar.",
        )
    observed_median = statistics.median(observed_n_periods_medians)
    if observed_median <= 0:
        return InvariantResult(
            name="check_guard_reference_coherence", passed=True,
            expected=f"Faktor <= {max_factor}", actual=None,
            severity="blocking",
            detail="beobachteter Median(n_periods) <= 0 — nicht anwendbar.",
        )
    ratio = configured_min_periods / observed_median
    passed = (1.0 / max_factor) <= ratio <= max_factor
    return InvariantResult(
        name="check_guard_reference_coherence",
        passed=passed,
        expected=f"Faktor <= {max_factor} zwischen konfiguriertem Referenzwert und "
                 "beobachtetem Median(n_periods)",
        actual=round(ratio, 4),
        # Issue #882 Fix Punkt 4 — vorher severity='high' ohne Entscheidungspflicht (Pitfall #280);
        # ein Guard, der gegen die falsche Groessenordnung ankert, zensiert die Suche systematisch
        # (kein Hinweis, ein Abbruchgrund) — jetzt blocking und in fail_fast_invariants gelistet.
        severity="blocking",
        detail=("OK" if passed else
                f"sortino_numeric_guard_min_periods={configured_min_periods:g} vs. beobachteter "
                f"Median(n_periods)={observed_median:g} (Faktor {ratio:.2g}) — der Referenzwert "
                "ist gegen eine andere Grössenordnung kalibriert als der aktuelle Lauf zeigt "
                "(Pitfall #274)."),
    )


def check_guard_reference_stability(study_records: list[dict]) -> InvariantResult:
    """Issue #968 (Katalog A, P0 HEADLINE, Pitfall #307 in AGENTS.md) — Reproduzierbarkeits-
    Regressionswächter: der Sortino-Numerik-Guard wandert innerhalb EINES Laufs, wenn
    ``sortino_numeric_guard_reference='family_median'`` konfiguriert ist — die Referenz ist dann
    ein LAUFZEITABHÄNGIGER, aus der eigenen Suchpopulation gebildeter Anker (der Median von
    ``oos_n_periods`` über bereits abgeschlossene Sibling-Trials), der mit jedem neu abgeschlossenen
    Trial wächst und dabei je nach Scheduler-Reihenfolge (``n_jobs > 1``) unterschiedliche Werte
    annehmen kann — derselbe Parametervektor kann so, abhängig allein von seiner Ankunftsreihenfolge,
    ein umgekehrtes Guard-Urteil erhalten (Referenzlauf 46cf5070: Trial 20 kippte von getrippt zu
    bestanden, je nachdem ob die Referenz 14,99 oder 19,47 war — identischer Parametervektor).

    Für JEDE Study MUSS gelten: ``len(set(guard_reference_value)) <= 1`` UND
    ``len(set(guard_reference_source)) <= 1`` — die Referenz darf sich innerhalb einer Study NIE
    ändern. Ein Verstoss beweist, dass die Guard-Referenz NICHT vor dem ersten Trial eingefroren
    wurde, sondern während des Laufs weiterhin von der eigenen Suchpopulation abhängt (##968s Fix
    verlangt eine VOR dem ersten Trial berechnete, eingefrorene H0-Tabelle — dieser Wächter macht
    jede verbleibende Abweichung von diesem Ziel sichtbar, unabhängig davon, welcher konkrete
    Referenz-Modus konfiguriert ist)."""
    with_data = [r for r in study_records if r.get("guard_reference_values") or r.get("guard_reference_sources")]
    if not with_data:
        return InvariantResult(
            name="check_guard_reference_stability",
            passed=True,
            expected="genau EIN guard_reference_value und EINE guard_reference_source je Study",
            actual=None,
            severity="blocking",
            detail="Keine Studies mit Guard-Referenz-Telemetrie (kein getrippter/unbewertbarer "
                   "Trial in diesem Lauf) — nicht anwendbar.",
        )
    offenders: dict[str, dict[str, object]] = {}
    for r in with_data:
        values = sorted({round(float(v), 6) for v in (r.get("guard_reference_values") or [])})
        sources = sorted(set(r.get("guard_reference_sources") or []))
        if len(values) > 1 or len(sources) > 1:
            offenders[f"{r.get('strategy')}/{r.get('symbol')}"] = {
                "guard_reference_values": values, "guard_reference_sources": sources,
            }
    passed = not offenders
    return InvariantResult(
        name="check_guard_reference_stability",
        passed=passed,
        expected="genau EIN guard_reference_value und EINE guard_reference_source je Study",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies mit einer wandernden Guard-Referenz: {offenders} — "
                "der Guard zensiert nach Ankunftsreihenfolge/Scheduler-Timing statt nach "
                "statistischer Implausibilität; der Lauf ist trotz festem Seed nicht bitweise "
                "reproduzierbar (Pitfall #307)."),
    )


def check_exit_reason_coverage(study_records: list[dict]) -> InvariantResult:
    """Issue #919 Fix 4 — die Summe des je-Study aufsummierten ``exit_reason_histogram`` (aus
    Order-Tags, #899) muss GENAU der Anzahl der Round-Trips entsprechen, die eine
    Exit-Telemetrie beigetragen haben (``oos_total_trades_with_exit_telemetry``,
    ``report._study_record``). Eine Lücke bedeutet einen Exit-Pfad, der keinen Order-Tag setzt —
    ein Round-Trip ohne Attribution.

    Studies ohne jede Exit-Telemetrie (leeres Histogramm, z. B. Pre-#899-Daten oder 0 Trades)
    sind nicht anwendbar (PASS)."""
    offenders: dict[str, str] = {}
    for r in study_records:
        histogram = r.get("exit_reason_histogram") or {}
        expected = r.get("oos_total_trades_with_exit_telemetry")
        if not histogram or expected is None:
            continue
        observed = sum(histogram.values())
        if observed != expected:
            key = f"{r.get('strategy')}/{r.get('symbol')}"
            offenders[key] = f"histogram_sum={observed} != oos_total_trades={expected}"
    passed = not offenders
    return InvariantResult(
        name="check_exit_reason_coverage",
        passed=passed,
        expected="sum(exit_reason_histogram.values()) == oos_total_trades_with_exit_telemetry je Study",
        actual=offenders if offenders else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies mit einer Lücke zwischen Exit-Reason-Histogramm "
                f"und Round-Trip-Zahl: {offenders} — mindestens ein Exit-Pfad setzt keinen "
                "Order-Tag (Issue #919)."),
    )


def check_instrument_metadata_coherence(instruments: dict[str, dict], *,
                                        spread_bps_by_asset_class: dict | None = None) -> InvariantResult:
    """Issue #920 (Pitfall #298) — Metadaten sind gegen sich selbst prüfbar, ohne externe Quelle.
    Root-Cause #920: der #898-Fix schloss die Lücke "asset_class fehlt" durch einen pauschalen
    Backfill auf 'equity' — 12 Krypto-Symbole (``size_precision=8``, unmöglich für eine Aktie mit
    eToro-Bruchteilshandel) lösten seither auf den falschen, um Faktor 4 zu niedrigen
    Spread/Kommission auf. Ein fail-loud-Wächter plus ein flächendeckender Backfill ergibt einen
    Wächter, der nie feuert (#297) — diese Invariante prüft die Metadaten stattdessen GEGEN SICH
    SELBST.

    Regeln (jede Verletzung ist ``severity='blocking'``):
      * ``size_precision >= 6`` ⇒ ``asset_class`` MUSS ``'crypto'`` sein (eToro-Aktien-
        Bruchteilshandel geht nicht auf sechs oder mehr Nachkommastellen).
      * ``asset_class == 'forex'`` ⇒ ``price_precision >= 4``.
      * jede vorkommende ``asset_class`` (normiert auf Grossschreibung) muss einen Eintrag in
        ``spread_bps_by_asset_class`` haben, sofern diese Map übergeben wird.

    ``instruments``: das ``instrument_map.json['instruments']``-Dict (ID → {'symbol',
    'asset_class', 'price_precision', 'size_precision'})."""
    offenders: dict[str, str] = {}
    for iid, data in (instruments or {}).items():
        symbol = data.get("symbol", iid)
        asset_class = (data.get("asset_class") or "").strip().lower()
        size_precision = data.get("size_precision")
        price_precision = data.get("price_precision")
        if size_precision is not None and int(size_precision) >= 6 and asset_class != "crypto":
            offenders[symbol] = (
                f"size_precision={size_precision} >= 6 impliziert 'crypto', asset_class="
                f"'{asset_class}'")
            continue
        if asset_class == "forex" and price_precision is not None and int(price_precision) < 4:
            offenders[symbol] = f"asset_class='forex' verlangt price_precision >= 4, hat {price_precision}"
            continue
        if spread_bps_by_asset_class is not None and asset_class:
            normalized = {str(k).strip().upper() for k in spread_bps_by_asset_class}
            if asset_class.upper() not in normalized:
                offenders[symbol] = (
                    f"asset_class='{asset_class}' hat keinen Eintrag in spread_bps_by_asset_class "
                    f"({sorted(normalized)})")
    passed = not offenders
    return InvariantResult(
        name="check_instrument_metadata_coherence",
        passed=passed,
        expected="size_precision>=6 impliziert asset_class='crypto'; asset_class='forex' impliziert "
                 "price_precision>=4; jede asset_class hat einen Kosten-Eintrag",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Instrument(e) mit inkohärenten Metadaten: {offenders} — "
                "Issue #920: ein pauschaler Backfill auf 'equity' entwertet fail-loud-Wächter, "
                "die nur auf FEHLENDE (nicht auf FALSCHE) Metadaten prüfen."),
    )


def check_search_made_progress(study_records: list[dict]) -> InvariantResult:
    """Issue #929 Fix 3 — eigenständiges Frühwarnsignal: ``constraint_improvement_rate`` (die
    Änderung der mittleren Constraint-Verletzung zwischen erster und zweiter Hälfte der
    modellierten Trials, ``run_optimization._constraint_violation_progress``) ist UNABHÄNGIG von
    der Eligibility auswertbar — ein negativer Wert über eine ganze Study ist ein Befund, egal ob
    p_eligible bereits 0 ist. FAILt (severity 'high'), wenn ``constraint_improvement_rate <= 0``
    UND ``p_eligible == 0`` UND ``n_modelled_trials >= plateau_min_modelled_trials`` — der TPE hat
    dann nachweislich NICHTS gelernt (die Constraint-Verletzung wuchs oder stagnierte trotz
    ausreichend modellierter Trials), nicht nur 'noch keinen eligiblen Trial gefunden'.

    Issue #981 (Katalog C, P1, Pitfall #312) — Root-Cause einer Fehl-Zuschreibung im Referenzlauf
    46cf5070: ``constraint_improvement_rate`` wird aus ``min_constraint_violation_first/last``
    berechnet, die bei fehlender Selektionsstatistik (#966) auf einer DREIWERTIGEN
    Treppenfunktion ({0.0, 0.5, 1.0}) STATT einer kontinuierlichen Distanz beruhten — eine Grösse,
    die per Konstruktion keinen Gradienten anzeigen KANN, wurde als "der Sampler hat nachweislich
    keinen Gradienten gefunden" fehlinterpretiert. Diese Prüfung testet jetzt zusätzlich die
    AUFLÖSUNG ihrer eigenen Eingabe (``constraint_violations_observed``, die rohen
    je-Trial-Konstraint-Distanzen dieser Study): ``len(set(...)) < 10`` ⇒ Ergebnis ``INCONCLUSIVE``
    statt ``FAIL`` (mit #966 gefixt sollte dieser Fall nicht mehr auftreten, diese Prüfung bleibt
    aber die Regressionssicherung dagegen)."""
    offenders: dict[str, float] = {}
    inconclusive_studies: list[str] = []
    with_data = [
        r for r in study_records
        if r.get("constraint_improvement_rate") is not None
        and r.get("n_modelled_trials") is not None
        and r.get("plateau_min_modelled_trials") is not None
    ]
    for r in with_data:
        if not (float(r["constraint_improvement_rate"]) <= 0.0
                and (r.get("p_eligible") or 0.0) == 0.0
                and int(r["n_modelled_trials"]) >= int(r["plateau_min_modelled_trials"])):
            continue
        observed = r.get("constraint_violations_observed")
        if observed is not None and len(set(round(float(v), 6) for v in observed)) < 10:
            inconclusive_studies.append(f"{r.get('strategy')}/{r.get('symbol')}")
            continue
        offenders[f"{r.get('strategy')}/{r.get('symbol')}"] = round(
            float(r["constraint_improvement_rate"]), 6)
    passed = not offenders
    detail_suffix = ""
    if inconclusive_studies:
        detail_suffix = (f" {len(inconclusive_studies)} weitere Study/Studies waren nach der "
                         f"FAIL-Bedingung auffällig, aber ihre Eingabe ist zu grob quantisiert "
                         f"(< 10 verschiedene Werte) für eine belastbare Aussage — als "
                         f"INCONCLUSIVE statt FAIL gezählt: {inconclusive_studies}.")
    return InvariantResult(
        name="check_search_made_progress",
        passed=passed,
        expected="constraint_improvement_rate > 0 ODER p_eligible > 0 ODER "
                 "n_modelled_trials < plateau_min_modelled_trials, je Study",
        actual=offenders if offenders else None,
        severity="high",
        inconclusive=bool(inconclusive_studies) and not offenders,
        detail=(("OK" if not inconclusive_studies else "OK (kein FAIL) —" + detail_suffix)
                if passed else
                f"{len(offenders)} Study/Studies mit stagnierender/wachsender Constraint-"
                f"Verletzung bei 0 eligiblen Trials nach ausreichend modellierten Trials: "
                f"{offenders} — der TPE-Sampler hat nachweislich keinen Gradienten gefunden."
                + detail_suffix),
    )


def _mann_whitney_u_one_sided(missing: list[float], available: list[float]) -> tuple[float, float] | tuple[None, None]:
    """Issue #965 Fix Punkt 4 (Katalog A, P0 HEADLINE, Pitfall #306 in AGENTS.md) — einseitiger
    Mann-Whitney-U-Test (Normalapproximation mit Tie-Korrektur, KEINE scipy-Abhängigkeit): H1 =
    "``missing`` (die Kohorte OHNE Selektionsstatistik) ist stochastisch GRÖSSER als ``available``
    (die Kohorte MIT Selektionsstatistik)". Genau diese Richtung ist der Referenzlauf-Befund
    (46cf5070: Median-OOS-Return +9,50 % [PSR fehlt] vs. −25,73 % [PSR verfügbar]) — eine fehlende
    Selektionsstatistik ist NIE "neutral": prüfen, ob die Ausfallmenge ökonomisch verschieden von
    der Erfolgsmenge ist (Pitfall #306).

    Rückgabe ``(z, p_one_sided)`` — ``z > 0``/``p`` klein bedeutet: ``missing`` tendiert zu grösseren
    Werten. ``(None, None)`` bei < 2 Beobachtungen in einer der beiden Gruppen (kein belastbarer
    Test möglich)."""
    n1, n2 = len(missing), len(available)
    if n1 < 2 or n2 < 2:
        return None, None
    combined = sorted((v, 0) for v in missing) + sorted((v, 1) for v in available)
    combined.sort(key=lambda t: t[0])
    ranks: list[float] = [0.0] * len(combined)
    i = 0
    tie_correction = 0.0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = avg_rank
        t = j - i
        if t > 1:
            tie_correction += t ** 3 - t
        i = j
    rank_sum_missing = sum(r for r, (_, grp) in zip(ranks, combined) if grp == 0)
    u_missing = rank_sum_missing - n1 * (n1 + 1) / 2.0
    mean_u = n1 * n2 / 2.0
    n_total = n1 + n2
    if n_total <= 1:
        return None, None
    var_u = (n1 * n2 / 12.0) * ((n_total + 1) - tie_correction / (n_total * (n_total - 1)))
    if var_u <= 0.0:
        return None, None
    z = (u_missing - mean_u) / math.sqrt(var_u)
    # Obere Schwanzwahrscheinlichkeit P(Z >= z) via Normal-CDF-Approximation (Abramowitz/Stegun
    # 26.2.17, Standardfehler < 7.5e-8) — vermeidet eine scipy-Abhaengigkeit fuer einen einzigen Test.
    p_one_sided = 1.0 - _standard_normal_cdf(z)
    return z, p_one_sided


def _standard_normal_cdf(z: float) -> float:
    """Reine Normal-CDF ohne scipy (``math.erf`` ist Teil der Standardbibliothek)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def check_selection_statistic_economic_bias(
    trial_attrs: list[dict], *, alpha: float = 0.01,
) -> InvariantResult:
    """Issue #965 Fix Punkt 4 (Katalog A, P0 HEADLINE) — schärft
    ``check_selection_statistic_availability`` um einen VERTEILUNGS-Test: eine reine
    Anteilsschwelle (80 % verfügbar) fängt NICHT die Klasse „Statistik fehlt gerade dort, wo sie
    zählen würde" (Pitfall #306) — im Referenzlauf 46cf5070 waren 77 % der Kohorte MIT PSR
    verfügbar, aber die FEHLENDE Kohorte (23 %) war die profitablere (92,2 % positive Rendite vs.
    7,4 % in der verfügbaren Kohorte).

    Testet je Study (falls ``oos_evaluated`` Trials mit UND ohne ``oos_selection_statistic_
    available`` vorliegen): ist der Median-``oos_total_return`` der Kohorte OHNE Statistik
    einseitig signifikant GRÖSSER als der der Kohorte MIT Statistik (Mann-Whitney, ``alpha``)? Wenn
    ja: FAIL — die fehlende Statistik ist nicht MCAR (missing completely at random), sondern
    korreliert mit der ökonomischen Qualität, und die Selektion ist dadurch systematisch verzerrt.

    ``trial_attrs``: Liste von ``user_attrs``-artigen Dicts EINER Study (nicht des ganzen Laufs —
    der Aufrufer iteriert je Study, analog ``check_reward_term_variance``)."""
    missing = [
        float(t["oos_total_return"]) for t in trial_attrs
        if t.get("oos_evaluated") is True and t.get("oos_selection_statistic_available") is False
        and t.get("oos_total_return") is not None
    ]
    available = [
        float(t["oos_total_return"]) for t in trial_attrs
        if t.get("oos_evaluated") is True and t.get("oos_selection_statistic_available") is True
        and t.get("oos_total_return") is not None
    ]
    z, p = _mann_whitney_u_one_sided(missing, available)
    if z is None:
        return InvariantResult(
            name="check_selection_statistic_economic_bias",
            passed=True,
            expected=f"p >= {alpha} (Mann-Whitney, H1: Kohorte-ohne-Statistik > Kohorte-mit-Statistik)",
            actual=None,
            severity="high",
            detail="< 2 Trials in einer der beiden Kohorten — kein belastbarer Test möglich.",
        )
    passed = p >= alpha
    return InvariantResult(
        name="check_selection_statistic_economic_bias",
        passed=passed,
        expected=f"p >= {alpha} (Mann-Whitney, H1: Kohorte-ohne-Statistik > Kohorte-mit-Statistik)",
        actual={"z": round(z, 4), "p_one_sided": p, "n_missing": len(missing),
                "n_available": len(available),
                "median_missing": round(statistics.median(missing), 6),
                "median_available": round(statistics.median(available), 6)},
        severity="high",
        detail=("OK" if passed else
                f"Median-OOS-Return der Kohorte OHNE Selektionsstatistik "
                f"({statistics.median(missing):.4f}, n={len(missing)}) ist signifikant GRÖSSER als "
                f"der der Kohorte MIT Statistik ({statistics.median(available):.4f}, "
                f"n={len(available)}) — z={z:.4f}, p={p:.3g} < {alpha}. Die fehlende Statistik ist "
                "nicht MCAR: die Selektion verwirft bevorzugt die profitable Kohorte aus einem "
                "numerischen, nicht ökonomischen Grund (Pitfall #306)."),
    )


def check_selection_statistic_availability(study_records: list[dict], *,
                                           min_available_fraction: float = 0.80) -> InvariantResult:
    """Issue #915 (Pitfall #295) — die WIRKUNGS-Invariante, die ``check_guard_reference_coherence``
    NICHT ist: jene fragt "wird die konfigurierte Referenz auch verwendet?" (eine Quellen-Prüfung,
    die unter dem #913-Defekt trotz 0 bewertbarer Trials PASSte, weil der ehrliche dritte Zustand
    ``'family_median_unavailable'`` keine falsche Referenz behauptete). Diese Invariante fragt
    stattdessen "liefert der Guard eine BENUTZBARE Schwelle?": der Anteil der ``oos_evaluated=True``
    Trials mit definiertem ``oos_psr`` muss ``min_available_fraction`` (Default 0.80) erreichen.

    Severity ``blocking`` — ein Sweep, dessen erste Symbole 0 % definierte ``oos_psr`` liefern,
    soll nach diesem Symbol abbrechen statt 170 Stunden informationsfrei weiterzulaufen (siehe
    Issue #913 Katalog-Vorbemerkung: 2187 Trials, 0 mit definiertem Sortino/PSR)."""
    with_evaluated = [r for r in study_records if (r.get("n_evaluable") or 0) > 0]
    if not with_evaluated:
        return InvariantResult(
            name="check_selection_statistic_availability",
            passed=True,
            expected=f"Anteil oos_evaluated-Trials mit definiertem oos_psr >= "
                     f"{min_available_fraction} je Study",
            actual=None,
            severity="blocking",
            detail="Keine Study mit oos_evaluated-Trials — nicht anwendbar.",
        )
    offenders: dict[str, float] = {}
    for r in with_evaluated:
        n_evaluable = int(r["n_evaluable"])
        n_available = int(r.get("n_selection_statistic_available") or 0)
        fraction = n_available / n_evaluable if n_evaluable else 0.0
        if fraction < min_available_fraction:
            offenders[f"{r.get('strategy')}/{r.get('symbol')}"] = round(fraction, 4)
    passed = not offenders
    return InvariantResult(
        name="check_selection_statistic_availability",
        passed=passed,
        expected=f"Anteil oos_evaluated-Trials mit definiertem oos_psr >= "
                 f"{min_available_fraction} je Study",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies unter der Mindestverfügbarkeit "
                f"({min_available_fraction}) einer definierten Selektions-Teststatistik: "
                f"{offenders} — die Eligibility-Auswertung dieser Studies ist strukturell "
                "informationsfrei (Issue #913/#915), keine Aussage über die Strategien."),
    )


def check_promotion_multiplicity_route(proposal: dict) -> InvariantResult:
    """Issue #887 (Pitfall #278) — FAIL, wenn ein Proposal mit
    ``promotion_route == 'global_default_on_symbol'`` (#682/#783 — der globale Default wurde
    promotet, weil kein einziger Trial symbol-eligibel war) ein ``deflation_n_family > 1`` in die
    DSR-Berechnung getragen hat.

    Root-Cause: der globale Default nahm an der Stufe-1-Selektion NICHT teil — er wurde nicht aus
    ``deflation_n_family`` Kandidaten ausgewählt. ``confirm.resolve_promotion_multiplicity``
    (die EINE Quelle für ``N``) liefert für diese Route immer ``N=1``; ein exportiertes Proposal
    mit einem grösseren Wert zeigt, dass eine ANDERE (veraltete) Codepfad die volle
    Stufe-1-Familiengrösse auf einen Kandidaten angewendet hat, der diese Suche nie durchlaufen
    hat — die Deflationsschwelle wäre dann strukturell unerreichbar (E[max_N] wächst mit
    ``sqrt(2 ln N)``).

    ``proposal``: der vollständige exportierte Proposal-Dict (``promotion_route`` liegt auf der
    OBERSTEN Ebene, ``deflation_n_family`` in ``proposal['holdout']['symbol']``)."""
    route = proposal.get("promotion_route")
    if route != "global_default_on_symbol":
        return InvariantResult(
            name="check_promotion_multiplicity_route",
            passed=True,
            expected="deflation_n_family <= 1 für promotion_route == 'global_default_on_symbol'",
            actual=None,
            detail=f"promotion_route={route!r} — nicht die global_default-Route, nicht anwendbar.",
        )
    n_family = ((proposal.get("holdout") or {}).get("symbol") or {}).get("deflation_n_family")
    passed = n_family is None or n_family <= 1
    return InvariantResult(
        name="check_promotion_multiplicity_route",
        passed=passed,
        expected="deflation_n_family <= 1 für promotion_route == 'global_default_on_symbol'",
        actual=n_family,
        severity="high",
        detail=("OK" if passed else
                f"promotion_route='global_default_on_symbol' trägt deflation_n_family={n_family} "
                "> 1 in die DSR-Berechnung — resolve_promotion_multiplicity wurde umgangen "
                "(Pitfall #278)."),
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


def check_deflation_cluster_coverage(holdout_metrics: dict, *, min_coverage: float = 0.9) -> InvariantResult:
    """Issue #813-Regressionswächter.

    ``sweep._family_n_from_studies`` zählt seit #784 ``oos_evaluated`` (Versuche); die familienweite
    Decluster-Matrix (``sweep._family_period_returns_from_studies`` → ``confirm.deflation_cluster_
    coverage``) fand ihre Renditeserien vor #813 nur in der viel kleineren ``oos_eligible``-
    Teilmenge (``oos_period_returns`` wurde nur für eligible Trials gestempelt) — ``deflation_n_
    effective`` stieg dadurch um den Kehrwert der Eligibility-Passrate (empirisch ~7,7×), während
    die tatsächlich declusterte Config-Zahl auf der alten, kleinen Menge stehen blieb: systematische
    Über-Deflation. ``deflation_cluster_coverage`` (#813) ist der Anteil der gezählten Kandidaten,
    für die überhaupt eine Renditeserie vorlag — unter ``min_coverage`` (Default 0.9, siehe
    #813-Katalogtext) ist das ein Invarianten-FAIL: die Declusterung sieht nicht (mehr) genug von
    der gezählten Kohorte, um ``E[max_N]`` auf einer repräsentativen Stichprobe zu bilden.

    ``None``/fehlende Werte (keine Deflations-Kohorte oder ``deflation_n_family == 0``) ⇒ nicht
    anwendbar (PASS, kein Urteil möglich)."""
    coverage = holdout_metrics.get("deflation_cluster_coverage")
    n_family = holdout_metrics.get("deflation_n_family")
    if coverage is None or not n_family:
        return InvariantResult(
            name="check_deflation_cluster_coverage",
            passed=True,
            expected=f">= {min_coverage}",
            actual=None,
            detail="Keine Familien-Kohorte (deflation_n_family=0 oder Coverage unbekannt) — nicht anwendbar.",
        )
    passed = coverage >= min_coverage
    return InvariantResult(
        name="check_deflation_cluster_coverage",
        passed=passed,
        expected=f">= {min_coverage}",
        actual=coverage,
        detail=("OK" if passed else
                f"deflation_cluster_coverage={coverage:.3f} < {min_coverage} — die Decluster-Matrix "
                f"sieht nur einen Bruchteil der gezählten (oos_evaluated) Kandidaten; E[max_N] "
                "riskiert eine systematische Über-Deflation (#813)."),
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


def check_family_n_statistic_coverage(trials: list[dict], *,
                                      deflation_n_family_raw: int | None) -> InvariantResult:
    """Issue #822-Regressionswächter.

    ``sweep._family_n_from_studies`` zählte bis #822 ``oos_evaluated is True`` (blosse Aktivität)
    statt ``oos_selection_statistic_available is True`` (eine verwertbare Selektions-Teststatistik,
    ``oos_psr``) — ein Trial mit ``SORTINO_GUARD_TRIPPED``/``EQUITY_NONPOSITIVE`` ist
    ``oos_evaluated=True``, trägt aber keinen Sortino/PSR und hat das Maximum unter H₀ nicht
    beeinflusst (dieselbe Argumentation wie #814 für nie gezogene Trials). Diese Prüfung
    rekonstruiert die Zahl der Trials MIT tatsächlich vorhandener Teststatistik unabhängig aus den
    ``trials``-User-Attrs (``oos_selection_statistic_available``) und vergleicht sie gegen die
    TATSÄCHLICH in die Deflation eingeflossene Zahl (``deflation_n_family_raw``) — FAIL, wenn
    Letztere die rekonstruierte Zahl übersteigt (der Zähler hätte Trials ohne Teststatistik
    mitgezählt, exakt die #822-Root-Cause).

    ``trials`` ist eine Liste von ``user_attrs``-artigen Dicts (pure/synthetisch testbar, analog
    ``check_reward_term_variance``). ``deflation_n_family_raw is None`` ⇒ nicht anwendbar (PASS)."""
    if deflation_n_family_raw is None:
        return InvariantResult(
            name="check_family_n_statistic_coverage",
            passed=True,
            expected="deflation_n_family_raw <= Trials mit oos_selection_statistic_available",
            actual=None,
            detail="deflation_n_family_raw unbekannt — nicht anwendbar.",
        )
    n_with_statistic = sum(
        1 for t in trials if (t or {}).get("oos_selection_statistic_available") is True)
    passed = deflation_n_family_raw <= n_with_statistic
    return InvariantResult(
        name="check_family_n_statistic_coverage",
        passed=passed,
        expected=f"<= {n_with_statistic}",
        actual=deflation_n_family_raw,
        detail=("OK" if passed else
                f"deflation_n_family_raw={deflation_n_family_raw} > {n_with_statistic} Trials mit "
                "oos_selection_statistic_available=True — die Zaehlung zaehlt Trials ohne "
                "verwertbare Teststatistik mit (#822-Regression)."),
    )


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


_CENSORED_STATISTIC_FLAG_SUFFIX = "_censored"


def check_censored_statistic_in_decision(proposal: dict, holdout_metrics: dict) -> InvariantResult:
    """Issue #1004 (Katalog #858, Fix Punkt 4, Pitfall #342). Ein Cap ist eine Zensur, kein Wert:
    ``backtest_runner._calculate_stats`` stempelt seit #1004 ``profit_factor_censored`` (und
    perspektivisch jedes weitere ``*_censored``-Flag), sobald der gemeldete Wert nicht der wahre
    (unbeschränkte) Punktschätzer ist — entweder weil ``profit_factor_cap`` band oder weil der
    Nenner numerisch degeneriert war (``PROFIT_FACTOR_DENOMINATOR_DEGENERATE``). Keine Promotion
    darf auf einer Kennzahl beruhen, deren zugehöriges ``*_censored``-Flag gesetzt ist — severity
    'blocking', denn eine zensierte Zahl trägt keine Information, mit der eine Kapitalentscheidung
    begründet werden könnte (im Unterschied zu #1007, das eine STUDY wegen einer verletzten
    Invariante ablehnt: hier ist die Zahl selbst, auf der die Entscheidung beruht, unzuverlässig).

    ``proposal``: der vollständige exportierte Proposal-Dict (``status`` auf oberster Ebene).
    ``holdout_metrics``: ``proposal['holdout']['symbol']`` (dieselbe Quelle wie jeder andere
    Check dieser Datei, der ``holdout_metrics`` konsumiert, z. B. ``check_sr0_coherence``)."""
    status = proposal.get("status")
    promote = status in ("READY_FOR_PR", "PROMOTE_GLOBAL_DEFAULT")
    censored_fields = sorted(
        k for k, v in (holdout_metrics or {}).items()
        if k.endswith(_CENSORED_STATISTIC_FLAG_SUFFIX) and v is True
    )
    if not promote:
        return InvariantResult(
            name="check_censored_statistic_in_decision",
            passed=True,
            expected="Keine *_censored-Flags bei promote=True",
            actual={"status": status, "censored_fields": censored_fields},
            detail="Nicht anwendbar (status ist keine Promotion).",
        )
    passed = not censored_fields
    return InvariantResult(
        name="check_censored_statistic_in_decision",
        passed=passed,
        expected="Keine *_censored-Flags bei promote=True",
        actual={"status": status, "censored_fields": censored_fields},
        severity="blocking",
        detail=("OK" if passed else
                f"Promotion (status={status!r}) beruht auf zensierter/gecappter Kennzahl: "
                f"{', '.join(censored_fields)} — der wahre Wert ist unbekannt (#1004)."),
    )


def check_promotion_deployment_coherence(proposal: dict, deployment_decision: dict | None) -> InvariantResult:
    """Issue #1006 (Katalog #858, Fix Punkt 3). Zwei Selektionssysteme mit unterschiedlicher
    Strenge (Wiederkehr des #993-Musters, hier mit vertauschten Vorzeichen: der Sweep ist der
    SCHWÄCHERE Pfad) — ``deployment_gate.evaluate_deployment_eligibility`` prüft acht (seit #1007
    neun) Klauseln, u. a. DSR UNBEDINGT (``_clause_dsr``, kein Ersatzpfad), während der Sweep
    selbst über ``promotion_correction_mode='dsr_or_robust_pair'`` einen DSR-Miss ersetzen kann.
    Ein Kandidat, der im Sweep ``READY_FOR_PR``/``PROMOTE_GLOBAL_DEFAULT`` wird, aber die
    Deployment-Grenze ablehnt, ist KEIN Fehler an sich — aber er MUSS sichtbar sein
    (``summary_de.py`` Abschnitt 2.1 darf niemals "Deploybar" ohne ein begleitendes
    ``DeploymentDecision``-Objekt behaupten), nicht implizit unter derselben Statuszeile
    verschwinden. severity='high' (nicht 'blocking'): die Study selbst bleibt ein valider
    Sweep-Gewinner, nur (noch) nicht kapitalwirksam.

    ``deployment_decision``: ``deployment_gate.DeploymentDecision.to_dict()`` oder ``None``
    (Aufrufer konnte/musste keine Bewertung durchführen, z. B. weil ``proposal`` kein
    Promotionskandidat ist — dann ist diese Prüfung nicht anwendbar)."""
    status = proposal.get("status")
    promote = status in ("READY_FOR_PR", "PROMOTE_GLOBAL_DEFAULT")
    if not promote:
        return InvariantResult(
            name="check_promotion_deployment_coherence",
            passed=True,
            expected="deployment_decision.admitted == True bei promote=True",
            actual={"status": status, "deployment_decision": deployment_decision},
            detail="Nicht anwendbar (status ist keine Promotion).",
        )
    admitted = (deployment_decision or {}).get("admitted")
    blocking_clause = (deployment_decision or {}).get("blocking_clause")
    passed = admitted is True
    return InvariantResult(
        name="check_promotion_deployment_coherence",
        passed=passed,
        expected="deployment_decision.admitted == True bei promote=True",
        actual={"status": status, "admitted": admitted, "blocking_clause": blocking_clause},
        severity="high",
        detail=("OK" if passed else
                f"Promotion (status={status!r}) besteht die Deployment-Grenze nicht "
                f"(blocking_clause={blocking_clause!r}) — der Kandidat ist ein Sweep-Gewinner, "
                "aber laut deployment_gate.evaluate_deployment_eligibility NICHT deploybar (#1006)."),
    )


def check_log_return_coherence(trials: list[dict]) -> InvariantResult:
    """Issue #756-Regressionswächter (folgt auf #589/#620).

    Seit `_calculate_stats` (backtest_runner.py) den Sortino-Zähler auf LOG-Returns umgestellt hat,
    gilt ``sign(oos_sortino_period) == sign(oos_total_return)`` PER KONSTRUKTION für jede
    Renditesequenz (Σ log(1+rᵢ) = log(1+total_return)) — nicht mehr nur empirisch selten verletzt.
    Ein Trial mit gesetztem ``oos_coherence_violation`` (dasselbe Flag, das
    ``_assert_sortino_return_coherence`` stempelt) ist damit ein ECHTER Aggregationsdefekt, keine
    erwartete Restrate mehr. ``trials`` ist eine Liste von ``user_attrs``-artigen Dicts (#621-
    Konvention, dieselbe Form wie ``check_reward_term_variance``).

    Issue #803 — dieser REPORT-Check bleibt bestehen (Regressionswächter über den GESAMTEN Lauf),
    ist aber seit #803 nicht mehr die einzige Instanz, die auf ``oos_coherence_violation`` reagiert:
    ``backtest_runner._evaluate_oos_eligibility`` disqualifiziert JEDEN betroffenen Trial bereits
    individuell (``REJECT_OOS_INVALID_METRICS``), und
    ``run_optimization.check_study_coherence_violation_rate``/
    ``coherence_violation_early_abort_callback`` beenden eine systematisch betroffene Study bereits
    waehrend ``study.optimize()``. Ein ``passed=False`` hier ist damit ein STAERKERES Signal als vor
    #803 (die Trial-/Study-Ebene haetten dieselbe Kohorte bereits abgefangen)."""
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


def check_window_unreachable_rate(
    study_records: list[dict], *, max_fraction: float = 0.05,
) -> InvariantResult:
    """Issue #976 (Katalog B, P2) — ``REJECT_OOS_WINDOW_UNREACHABLE`` (Warmup-Bedarf des
    Indikators überschreitet die verfügbare IS-Historie bei bestimmten Parametervektoren) ist ein
    SUCHRAUM-/BOUNDS-Defekt, kein Laufzeitzustand — der Referenzlauf 46cf5070 zeigte 96 von 516
    nicht auswertbaren Trials mit diesem Code. Diese Prüfung ist die DIAGNOSE-Hälfte des #976-Fixes
    (Detektion): eine Study, deren Trials überproportional an unerreichbaren OOS-Fenstern statt an
    einer echten Eligibility-Frage scheitern, hat zu weite Lookback-Bounds für ihre Datenlage.

    Die PRÄVENTIONS-Hälfte (den betroffenen Parametervektor bereits VOR dem Backtest über
    ``constraints_func`` als infeasible zu markieren, siehe #976-Fix Punkt 1/2) erfordert eine
    strategie-spezifische Ableitung des maximalen Lookback-Bedarfs aus den gesampelten Parametern
    (``spaces.py``) — bewusst NICHT Teil dieser Änderung (siehe #992-Merge-Order-Anmerkung: eine
    falsch abgeleitete Lookback-Grenze würde gültige Parametervektoren stillschweigend aus dem
    Suchraum entfernen, ein Risiko, das eine Detektions-Invariante nicht trägt).

    ``study_records``: Liste mit ``n_trials`` und ``is_rejection_detail_counts`` (dict Code→Anzahl,
    aus den aggregierten ``is_rejection_detail``-Werten dieser Study, sofern vorhanden)."""
    with_data = [
        r for r in study_records
        if r.get("n_trials") and r.get("is_rejection_detail_counts") is not None
    ]
    if not with_data:
        return InvariantResult(
            name="check_window_unreachable_rate",
            passed=True,
            expected=f"Anteil REJECT_OOS_WINDOW_UNREACHABLE <= {max_fraction} je Study",
            actual=None,
            detail="Keine Studies mit is_rejection_detail_counts-Telemetrie — nicht anwendbar.",
        )
    offenders = {}
    for r in with_data:
        n_unreachable = int((r.get("is_rejection_detail_counts") or {}).get(
            "REJECT_OOS_WINDOW_UNREACHABLE", 0))
        fraction = n_unreachable / int(r["n_trials"])
        if fraction > max_fraction:
            offenders[f"{r.get('strategy')}/{r.get('symbol')}"] = round(fraction, 4)
    passed = not offenders
    return InvariantResult(
        name="check_window_unreachable_rate",
        passed=passed,
        expected=f"Anteil REJECT_OOS_WINDOW_UNREACHABLE <= {max_fraction} je Study",
        actual=offenders if offenders else None,
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies mit überproportional vielen unerreichbaren "
                f"OOS-Fenstern: {offenders} — zu weite Lookback-Bounds für die Datenlage dieses "
                "Symbols (spaces.py gegen data_window_days deckeln, #976)."),
    )


def check_objective_branch_coverage(
    trials: list[dict], *, min_ordering_fraction: float = 0.10,
) -> InvariantResult:
    """Issue #979 (Katalog C, P0) — der TPE optimiert eine Zielfunktion, deren ORDNENDER Zweig
    (``reward_terms.branch == 'per_symbol'`` — die einzigen Trials, deren Reward auf der
    kontinuierlichen ``psr_z``-Qualitätsordnung beruht, nicht auf einer Konstante oder der
    Rangfolge einer Nebenbedingung) im Referenzlauf 46cf5070 nur 2.15% aller Auswertungen ausmachte
    (57 von 2651): 78.4% (`branch == 'failure'`) tragen die Rangfolge einer Nebenbedingung
    (überwiegend #977s dd_penalty, siehe dortiger Fix), 19.5% (`branch == 'unevaluable'`) eine
    KONSTANTE (``std(reward) = 0.116`` über den gesamten Zweig).

    Root-Cause: Feasibility ist doppelt kodiert (Pitfall #124) — einmal als #612-Sampler-Constraint
    (korrekt) und einmal als Reward-Zweig-Klippe zwischen ``branch``-Werten. Diese Invariante ist
    additiv/observational (kein Eingriff in die Zweig-Architektur selbst — siehe #992-Merge-Order:
    die vollständige Entfernung der Zweig-Klippe zugunsten einer durchgängig stetigen Reward-Skala
    ist eine grössere, hier bewusst NICHT umgesetzte Restrukturierung, analog #916s
    ``sortino_guard_family_scope='symbol'``-Zurückstellung).

    ``trials``: ``user_attrs``-artige Dicts mit ``reward_terms`` (dict mit ``branch``-Schlüssel)."""
    branches = [
        t["reward_terms"]["branch"] for t in trials
        if isinstance(t.get("reward_terms"), dict) and t["reward_terms"].get("branch")
    ]
    if not branches:
        return InvariantResult(
            name="check_objective_branch_coverage",
            passed=True,
            expected=f"Anteil reward_terms.branch=='per_symbol' >= {min_ordering_fraction}",
            actual=None,
            detail="Keine Trials mit reward_terms.branch-Telemetrie — nicht anwendbar (Pre-#621-"
                   "Report oder globaler Multi-Symbol-Reward-Modus ohne 'per_symbol'-Branch).",
        )
    n_ordering = sum(1 for b in branches if b == "per_symbol")
    fraction = n_ordering / len(branches)
    passed = fraction >= min_ordering_fraction
    return InvariantResult(
        name="check_objective_branch_coverage",
        passed=passed,
        expected=f"Anteil reward_terms.branch=='per_symbol' >= {min_ordering_fraction}",
        actual=round(fraction, 4),
        detail=("OK" if passed else
                f"Nur {n_ordering}/{len(branches)} Trials ({fraction:.2%}) tragen die ordnende "
                f"Qualitätsinformation (branch=='per_symbol') — unter der Schwelle "
                f"({min_ordering_fraction:.0%}). Die Suche hat über den Grossteil der Auswertungen "
                "kein Ziel (Pitfall #124 — doppelt kodierte Feasibility)."),
    )


def check_annualization_commensurability(
    trials: list[dict], *, max_ratio: float = 1.05,
) -> InvariantResult:
    """Issue #978 (Katalog C, P0) — der Annualisierungsfaktor F (``√F = oos_fold_sortino /
    oos_fold_sortino_period`` je Fold) wird empirisch aus der Beobachtungszahl je Fold abgeleitet
    (``_get_annualization_factor``) und variiert dadurch INNERHALB eines Trials: vier Folds
    DESSELBEN Trials (dieselbe Kalenderlänge) annualisierten im Referenzlauf 46cf5070 mit
    √F = 36.83/35.94/34.08/33.97 — vier verschiedene Faktoren für vier gleich lange Fenster.

    Root-Cause: ``F`` ist eine FUNKTION der tatsächlich gehandelten Perioden (ereignisgetrieben,
    nicht kalenderbasiert) — und damit eine Funktion der Entscheidungsvariablen der Optimierung
    selbst. Der Fold-Median/-Mittelwert des annualisierten Sortino MITTELT dadurch inkommensurable
    Grössen (Pitfall #310 in AGENTS.md); jede nachgelagerte Statistik (Sortino-Guard, CSCV/PBO,
    Deflation), die auf annualisierten Werten operiert, ist betroffen.

    Prüft je Trial mit >= 2 Folds: ``max(√F) / min(√F) <= max_ratio``. ``trials``: ``user_attrs``-
    artige Dicts mit ``per_fold_oos_sortino``/``per_fold_oos_sortino_period`` (bzw. den Trial-
    User-Attr-Namen ``oos_fold_sortinos``/``oos_fold_sortino_periods`` — beide Namenspaare werden
    akzeptiert, je nachdem ob der Aufrufer das Log-Event oder den User-Attr-Snapshot übergibt)."""
    worst_ratio = 0.0
    worst_trial_idx: int | None = None
    n_trials_checked = 0
    for i, t in enumerate(trials):
        annualized = t.get("per_fold_oos_sortino") or t.get("oos_fold_sortinos") or []
        period = t.get("per_fold_oos_sortino_period") or t.get("oos_fold_sortino_periods") or []
        if len(annualized) < 2 or len(annualized) != len(period):
            continue
        sqrt_f_values = []
        for a, p in zip(annualized, period):
            if a is None or p is None or p == 0:
                continue
            ratio = a / p
            if ratio > 0:
                sqrt_f_values.append(ratio)
        if len(sqrt_f_values) < 2:
            continue
        n_trials_checked += 1
        trial_ratio = max(sqrt_f_values) / min(sqrt_f_values)
        if trial_ratio > worst_ratio:
            worst_ratio = trial_ratio
            worst_trial_idx = i
    if n_trials_checked == 0:
        return InvariantResult(
            name="check_annualization_commensurability",
            passed=True,
            expected=f"max(sqrt(F))/min(sqrt(F)) <= {max_ratio} je Trial",
            actual=None,
            severity="high",
            detail="Keine Trials mit >= 2 Folds und beiden Sortino-Skalen — nicht anwendbar.",
        )
    passed = worst_ratio <= max_ratio
    return InvariantResult(
        name="check_annualization_commensurability",
        passed=passed,
        expected=f"max(sqrt(F))/min(sqrt(F)) <= {max_ratio} je Trial",
        actual=round(worst_ratio, 4),
        severity="high",
        detail=("OK" if passed else
                f"Trial-Index {worst_trial_idx}: sqrt(F)-Spannweite innerhalb des Trials "
                f"beträgt Faktor {worst_ratio:.4g} (> {max_ratio}) — die Fold-Annualisierung "
                "ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median "
                "des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310)."),
    )


# Issue #886 (Pitfall #276) — SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE sind SEIT
# #863/#864 REGULÄRE dritte Ausgänge ("nicht messbar" ≠ "schlecht", der Trial wird korrekt geprunt
# statt als negative Beobachtung gewertet) — ihre blosse ANWESENHEIT ist seither kein Defekt mehr,
# nur noch ihre KONZENTRATION (siehe check_inference_diagnostics_concentration). Alle übrigen Codes
# (allen voran EQUITY_NONPOSITIVE) bleiben echte Defekt-Indikatoren — ihre Abwesenheit ist weiterhin
# die Norm.
#
# Issue #918 — SORTINO_GUARD_REFERENCE_UNAVAILABLE (#901/#913) gehört in dieselbe Klasse wie die
# beiden bestehenden Codes: 'kein belastbarer Familien-Median (noch)' ist eine Kaltstart-Aussage
# über die Study-Historie, keine über die Strategie — derselbe "nicht messbar ≠ schlecht"-Fall.
# BEWUSST NICHT aus _contracts.INFERENCE_DIAGNOSTIC_CODES.failure_policy abgeleitet (dort tragen
# SORTINO_GUARD_TRIPPED UND EQUITY_NONPOSITIVE identisch failure_policy='prune', gehören aber
# HIER in unterschiedliche Klassen — EQUITY_NONPOSITIVE bleibt ein echter Defekt-Indikator trotz
# ebenfalls geprunter Trial-Behandlung). failure_policy beschreibt die REWARD-Konsequenz, dieses
# Set beschreibt die DEFEKT-Konsequenz — zwei unabhängige Dimensionen desselben Codes.
#
# Issue #967 (Katalog A, P0, Pitfall — Severity-Klassifikation) — die "regulären dritten Ausgänge"
# zerfallen in ZWEI kausal verschiedene Klassen, die #886 noch als eine Menge behandelte:
#   CENSORING — der Guard trippt, der Trial verschwindet aus der Zielverteilung (sortino/psr=None,
#     kein verwertbares Ergebnis).
#   ADAPTIVE  — ein Korrekturmechanismus greift (z. B. James-Stein-Shrinkage, #944), der Trial
#     BLEIBT mit einer korrigierten, gültigen Statistik im Suchraum.
# Root-Cause #967: ``check_inference_diagnostics_absent`` FAILte bei GENAU EINEM
# ``SORTINO_DOWNSIDE_SHRUNK``-Ereignis (ADAPTIVE — der Mechanismus, den #944 einführte und der
# NACHWEISLICH korrekt arbeitet), weil dieser Code nicht in der Ausnahmeliste war — die Invariante
# bestrafte damit den einzigen funktionierenden Fix. ``SORTINO_DOWNSIDE_SHRUNK`` ist bereits in
# ``_contracts.INFERENCE_DIAGNOSTIC_CODES`` als ``failure_policy='telemetry_only'`` markiert (der
# Trial wird NICHT geprunt) — die genaue Signatur eines ADAPTIVE-Codes.
_CENSORING_DIAGNOSTIC_CODES = frozenset({
    "SORTINO_GUARD_TRIPPED", "SORTINO_INSUFFICIENT_DOWNSIDE", "SORTINO_GUARD_REFERENCE_UNAVAILABLE",
    # Issue #967 — die drei vorher STUMMEN Rückgabepfade (#967-Fix in backtest_runner.py) sind
    # ebenfalls "nicht messbar", keine Defekt-Indikatoren.
    "SORTINO_INSUFFICIENT_TRADES", "SORTINO_DOWNSIDE_DEVIATION_UNDEFINED",
    "SORTINO_ANNUALIZED_NONFINITE", "PSR_BOOTSTRAP_UNDEFINED",
})
_ADAPTIVE_DIAGNOSTIC_CODES = frozenset({"SORTINO_DOWNSIDE_SHRUNK"})
# Rückwärtskompat-Alias: der volle Ausschluss-Satz für check_inference_diagnostics_absent (#886s
# ursprüngliche Bedeutung — "kein Defekt", CENSORING UND ADAPTIVE gemeinsam).
_REGULAR_THIRD_OUTCOME_CODES = _CENSORING_DIAGNOSTIC_CODES | _ADAPTIVE_DIAGNOSTIC_CODES


def check_inference_diagnostics_absent(trials: list[dict]) -> InvariantResult:
    """Issue #804/#886 — sechster Regressionswächter: über die GESAMTE Study hinweg dürfen KEINE
    strukturierten Inferenzpfad-Diagnosen ausserhalb der #886-Ausnahmeliste
    (``EQUITY_NONPOSITIVE``, ``PERIOD_RETURNS_NOT_FINITE``,
    ``RETURN_SERIES_IDENTITY_VIOLATION``/``_UNDEFINED``, ``NON_CONTIGUOUS_FOLD_SEGMENTS``,
    ``COHERENCE_INVARIANT_VIOLATION`` — aus ``backtest_runner._calculate_stats``, je Trial unter
    ``inference_diagnostics`` gestempelt, #804) aufgetreten sein.

    Issue #886 — ``SORTINO_GUARD_TRIPPED``/``SORTINO_INSUFFICIENT_DOWNSIDE`` sind SEIT #863/#864
    reguläre dritte Ausgänge (Pitfall #276) und daher NICHT mehr Teil dieser Abwesenheits-Prüfung
    (ihre KONZENTRATION prüft ``check_inference_diagnostics_concentration`` stattdessen) — vorher
    meldete dieser Wächter korrektes Verhalten als Fehler und trug zur Alarm-Gewöhnung (#884) bei.

    Root-Cause #804: diese Diagnosen liefen bislang NUR im Backtest-SUBPROZESS über ``logging`` —
    0 Treffer über ein vollstaendiges Lauf-Log trotz 35 ``STUDY_ABORTED_ON_INVARIANT`` im
    Elternprozess. Dieser Check macht ihre Abwesenheit (oder Praesenz) zu einer MASCHINELL
    ueberpruefbaren #742-Report-Aussage, nicht nur einer live emittierten Log-Zeile (siehe
    ``run_optimization._reemit_inference_diagnostics`` fuer die Live-Emission je Trial).

    ``trials`` ist eine Liste von ``user_attrs``-artigen Dicts (#621-Konvention). Rein additiv/
    observational — WARNING-Klasse, kein Abbruch (analog ``check_log_return_coherence``)."""
    total = 0
    by_code: dict[str, int] = {}
    for t in trials:
        for diag in t.get("inference_diagnostics") or ():
            code = diag.get("code") if isinstance(diag, dict) else None
            if code and code not in _REGULAR_THIRD_OUTCOME_CODES:
                total += 1
                by_code[code] = by_code.get(code, 0) + 1
    passed = total == 0
    return InvariantResult(
        name="check_inference_diagnostics_absent",
        passed=passed,
        expected=0,
        actual=total,
        detail=("OK" if passed else
                f"{total} Inferenzpfad-Diagnose(n) über die Study ({by_code}) — siehe "
                f"INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial."),
    )


def check_adaptive_diagnostic_rate(
    trials: list[dict], *, n_trials_informative: int | None, max_rate: float = 0.3,
) -> InvariantResult:
    """Issue #967 Fix Punkt 2 (Katalog A, P0) — eigene Rate-Invariante für ADAPTIVE-Diagnosen
    (``_ADAPTIVE_DIAGNOSTIC_CODES``, aktuell ``SORTINO_DOWNSIDE_SHRUNK``): ihre blosse Anwesenheit
    ist (anders als CENSORING-Codes) KEIN Defekt — ``check_inference_diagnostics_absent`` schliesst
    sie seit diesem Fix aus —, aber eine Study, in der die Shrinkage bei > ``max_rate`` der
    informativen Trials greift, verlässt sich strukturell auf einen Korrekturmechanismus statt auf
    genügend Downside-Beobachtungen — eine eigene, von CENSORING getrennte Beobachtung wert (analog
    ``check_inference_diagnostics_concentration`` für CENSORING-Codes).

    ``trials``: ``user_attrs``-artige Dicts EINER Study. ``n_trials_informative``: derselbe #885-
    Nenner wie ``check_inference_diagnostics_concentration`` (``None`` ⇒ nicht anwendbar, Pre-#885-
    Report)."""
    if not n_trials_informative or n_trials_informative <= 0:
        return InvariantResult(
            name="check_adaptive_diagnostic_rate",
            passed=True,
            expected=f"Anteil ADAPTIVE-Diagnosen <= {max_rate}",
            actual=None,
            detail="n_trials_informative fehlt/ist 0 — nicht anwendbar (Pre-#885-Report oder leere "
                   "Kohorte).",
        )
    n_adaptive = 0
    for t in trials:
        codes = {
            diag.get("code") for diag in (t.get("inference_diagnostics") or ())
            if isinstance(diag, dict)
        }
        if codes & _ADAPTIVE_DIAGNOSTIC_CODES:
            n_adaptive += 1
    rate = n_adaptive / n_trials_informative
    # Issue #1033 (Katalog #866, Pitfall #356) — ``n_adaptive`` zaehlt hoechstens EINMAL je Trial
    # (Set-Schnitt oben) und kann daher ``n_trials_informative`` (dieselben Trials) strukturell
    # nicht ueberschreiten; ``rate > 1.0`` ist kein gueltiger Beobachtungswert, sondern ein Beweis,
    # dass Zaehler und Nenner NICHT dieselbe Grundgesamtheit messen (z. B. ``trials`` enthaelt
    # Duplikate oder ``n_trials_informative`` stammt aus einer anderen Kohorte) — eigene FAIL-
    # Meldung statt einer unplausiblen Prozentzahl.
    if rate > 1.0:
        return InvariantResult(
            name="check_adaptive_diagnostic_rate",
            passed=False,
            expected="Zaehler/Nenner kommensurabel (rate <= 1.0)",
            actual=round(rate, 4),
            detail=f"{n_adaptive}/{n_trials_informative} — Zaehler/Nenner nicht kommensurabel "
                   "(Rate > 1.0 ist kein gueltiger Beobachtungswert, #1033).",
        )
    passed = rate <= max_rate
    return InvariantResult(
        name="check_adaptive_diagnostic_rate",
        passed=passed,
        expected=f"Anteil ADAPTIVE-Diagnosen <= {max_rate}",
        actual=round(rate, 4),
        detail=("OK" if passed else
                f"{n_adaptive}/{n_trials_informative} informative Trials ({rate:.2%}) verlassen "
                f"sich auf einen ADAPTIVE-Korrekturmechanismus (SORTINO_DOWNSIDE_SHRUNK) — über "
                f"der Schwelle ({max_rate:.0%}), strukturell zu wenige Downside-Beobachtungen in "
                "dieser Study."),
    )


def check_inference_diagnostics_concentration(
    trials: list[dict], *, n_trials_informative: int | None,
    guard_dominance_threshold: float = 0.10,
) -> InvariantResult:
    """Issue #886 (Pitfall #276) — ersetzt die reine Anwesenheits-Prüfung für die #863/#864
    "regulären dritten Ausgänge" (``SORTINO_GUARD_TRIPPED``/``SORTINO_INSUFFICIENT_DOWNSIDE``)
    durch eine KONZENTRATIONS-Prüfung: FAIL, wenn eine Study mehr als
    ``guard_dominance_threshold`` ihrer INFORMATIVEN Trials (Issue #885 — ``n_trials_informative``,
    NICHT die rohe Trial-Zahl) an den Inferenzpfad verliert. Das ist dieselbe Bedingung wie
    ``run_optimization._emit_study_summary``s ``STUDY_GUARD_DOMINATED`` — dieser Check macht sie
    zusätzlich zu einer MASCHINELL überprüfbaren #742-Report-Aussage (analog
    ``check_inference_diagnostics_absent``), nicht nur einer Live-Log-Zeile.

    ``n_trials_informative is None`` (Legacy-Report ohne #885-Telemetrie) ⇒ nicht anwendbar
    (PASS) — kein stiller Fallback auf eine potenziell falsche rohe Trial-Zahl."""
    if n_trials_informative is None:
        return InvariantResult(
            name="check_inference_diagnostics_concentration",
            passed=True,
            expected=f"<= {guard_dominance_threshold:.0%} der informativen Trials mit "
                     "SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE",
            actual=None,
            detail="n_trials_informative unbekannt (Pre-#885-Report) — nicht anwendbar.",
        )
    affected = 0
    for t in trials:
        for diag in t.get("inference_diagnostics") or ():
            code = diag.get("code") if isinstance(diag, dict) else None
            if code in _REGULAR_THIRD_OUTCOME_CODES:
                affected += 1
                break
    fraction = (affected / n_trials_informative) if n_trials_informative > 0 else 0.0
    # Issue #1033 (Katalog #866, Pitfall #356) — ``affected`` zaehlt hoechstens einmal je Trial
    # (der ``break`` oben); ``fraction > 1.0`` beweist eine Zaehler/Nenner-Inkommensurabilitaet
    # (siehe check_adaptive_diagnostic_rate-Kommentar), keinen gueltigen Beobachtungswert.
    if fraction > 1.0:
        return InvariantResult(
            name="check_inference_diagnostics_concentration",
            passed=False,
            expected="Zaehler/Nenner kommensurabel (fraction <= 1.0)",
            actual=round(fraction, 4),
            detail=f"{affected}/{n_trials_informative} — Zaehler/Nenner nicht kommensurabel "
                   "(Rate > 1.0 ist kein gueltiger Beobachtungswert, #1033).",
        )
    passed = fraction <= guard_dominance_threshold
    return InvariantResult(
        name="check_inference_diagnostics_concentration",
        passed=passed,
        expected=f"<= {guard_dominance_threshold:.0%} der informativen Trials mit "
                 "SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE",
        actual=round(fraction, 4),
        detail=("OK" if passed else
                # Issue #948 (Katalog B, P2) — vorher stand hier "mit einem regulären Inferenzpfad-
                # Ausgang", das genaue GEGENTEIL der Bedeutung: die betroffenen Trials sind die
                # ZENSIERTEN/IRREGULÄREN Ausgänge (SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_
                # DOWNSIDE), nicht die regulären. Ein Leser, der nur diese Detail-Zeile liest (ohne
                # das korrekte `expected`-Feld daneben), zog den umgekehrten Schluss.
                f"{affected}/{n_trials_informative} informative Trials ({fraction:.1%}) wurden vom "
                "Inferenz-Wächter zensiert (SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE, "
                "kein regulärer Ausgang) — die Suche ist faktisch zensiert (analog "
                "STUDY_GUARD_DOMINATED, #823)."),
    )


# Issue #788 — dieselbe Sentinel-Frage wie #759 (dort nur oos_win_rate) gilt fuer JEDE OOS-Metrik,
# die make_symbol_objective als Trial-User-Attr persistiert: ein nicht evaluierter Trial darf fuer
# KEINE davon eine Beobachtung tragen. Deklarative Liste statt sechs Einzel-Wächtern.
def check_denominator_coherence(study_counts: dict) -> InvariantResult:
    """Issue #885 Fix Punkt 3 — FAIL, wenn ``n_trials_informative + n_trials_pruned +
    n_trials_unevaluable + n_trials_failed != n_trials_total`` für eine Study
    (``run_optimization._emit_study_summary`` stempelt alle fünf Zähler als Study-User-Attrs).

    Ein disjunktes, vollständiges Zerlegen der Trial-Menge ist die Voraussetzung dafür, dass
    ``n_trials_informative`` (statt der rohen Trial-Zahl) als EIN Nenner für alle Raten-Meldungen
    (Budget-Ausführung, Plateau-Anteile, Guard-Dominanz) tragfähig ist — eine Lücke oder
    Doppelzählung hier würde jede dieser Raten still verfälschen.

    ``study_counts`` fehlen die #885-Zähler (Pre-#885-Report) ⇒ nicht anwendbar (PASS)."""
    keys = ("n_trials_total", "n_trials_informative", "n_trials_pruned",
            "n_trials_unevaluable", "n_trials_failed")
    values = {k: study_counts.get(k) for k in keys}
    if any(v is None for v in values.values()):
        return InvariantResult(
            name="check_denominator_coherence",
            passed=True,
            expected="n_trials_informative + n_trials_pruned + n_trials_unevaluable + "
                     "n_trials_failed == n_trials_total",
            actual=None,
            detail="Zähler unbekannt (Pre-#885-Report) — nicht anwendbar.",
        )
    total = values["n_trials_total"]
    parts_sum = (values["n_trials_informative"] + values["n_trials_pruned"]
                 + values["n_trials_unevaluable"] + values["n_trials_failed"])
    passed = parts_sum == total
    return InvariantResult(
        name="check_denominator_coherence",
        passed=passed,
        expected="n_trials_informative + n_trials_pruned + n_trials_unevaluable + "
                 "n_trials_failed == n_trials_total",
        actual=values,
        detail=("OK" if passed else
                f"Zerlegung ({parts_sum}) != n_trials_total ({total}): {values} — die #885-"
                "Trial-Kategorien sind nicht disjunkt/vollständig."),
    )


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
    # Issue #927 — vorher NICHT in der Varianz-Tabelle, obwohl beide Terme in jedem Trial
    # gestempelt werden (reward.py): gate_distance_penalty ist unter dem #913-Defekt der GRÖSSTE
    # Einzelterm (0,14–0,31), time_box_penalty ist strukturell inert (Gewicht 0.0 seit v14, siehe
    # _CONFIGURED_INACTIVE_REWARD_TERMS unten) — beide waren dadurch bislang unbeobachtbar.
    "gate_distance_penalty", "time_box_penalty",
)

# Issue #927 Fix 2 — Terme, deren niedrige/keine Varianz ein DOKUMENTIERTER Normalzustand ist,
# keine REWARD_TERM_INERT-Auffälligkeit: tie_breaker ist konstruktionsbedingt nahezu konstant
# (reines Tie-Break-Signal); time_box_penalty ist inert, weil sein konfiguriertes Gewicht
# (optimizer.json['penalty_time_box_weight']) seit v14 auf 0.0 steht. Eine dynamische, aus der
# Config abgeleitete Fassung (jeder Term mit Gewicht 0.0) wäre die vollständigere Lösung, würde
# aber das Config-Objekt bis in report._study_record durchreichen müssen — hier bewusst auf die
# beiden aktuell bekannten Fälle beschränkt (dokumentiert, kein stiller Fallback).
# Issue #977 (Katalog C, P0 HEADLINE) — dd_penalty (optimizer.json['penalty_dd_weight'], jetzt 0.0)
# reiht sich hier ein: der Term dominierte die Zielfunktion um Faktor 2.7-8.5 gegenüber der Base,
# aber ausschliesslich im bereits verworfenen failure-Zweig (im eligiblen Zweig 1426x kleiner) —
# das Risiko ist bereits über das oos_max_drawdown-Gate abgedeckt, eine zusätzliche weiche Strafe
# war Doppelzählung (Pitfall #124).
_CONFIGURED_INACTIVE_REWARD_TERMS = frozenset({"tie_breaker", "time_box_penalty", "dd_penalty"})

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
    """Die ``reward_terms``-Dicts aller Trials, die tatsaechlich OOS-evaluiert wurden UND einer
    eligiblen/pareto-Kohorte angehoeren. Issue #927 — NICHT mehr die primaere Kohorte fuer
    ``check_reward_term_variance``/``reward_term_variance_table`` (siehe ``_evaluated_reward_
    terms``): bei ``p_eligible == 0`` (z. B. unter dem #913-Defekt) ist diese Liste IMMER leer,
    obwohl jeder evaluierte Trial eine vollstaendige reward_terms-Zerlegung traegt (branch=
    'failure'). Bleibt als ZUSAETZLICHE, engere Teilmengen-Sicht erhalten, wenn sie nicht leer
    ist (#927 Fix 1: 'mit einer zusaetzlichen Spalte fuer die eligible Teilmenge')."""
    return [
        t.get("reward_terms") for t in trials
        if t.get("oos_evaluated") is True and t.get("reward_terms")
        and t["reward_terms"].get("branch") in ("eligible", "per_symbol", "pareto")
    ]


def _evaluated_reward_terms(trials: list[dict]) -> list[dict]:
    """Issue #927 (Pitfall #302) — die PRIMAERE Kohorte fuer die Reward-Term-Varianzanalyse: JEDER
    ``oos_evaluated=True``-Trial traegt eine vollstaendige ``reward_terms``-Zerlegung, unabhaengig
    vom ``branch`` (auch ``'failure'`` — der Reward ist seit #629 ausdruecklich auf der
    EVALUIERTEN, nicht der eligiblen Kohorte definiert: 'evaluated-aber-ineligible Trials teilen
    den Qualitaets-Kern der eligiblen'). Eine Varianz-Analyse, die auf der eligiblen (=
    AUSGEWAEHLTEN) Kohorte rechnet, ist Selection-on-the-dependent-variable (Pitfall #302) — auf
    der Menge der Ueberlebenden sind praktisch alle Gates erfuellt, die Varianz, die man messen
    will, ist dort weggeschnitten."""
    return [
        t.get("reward_terms") for t in trials
        if t.get("oos_evaluated") is True and t.get("reward_terms")
    ]


def _feasible_reward_terms(trials: list[dict]) -> list[dict]:
    """Issue #949 (Katalog C, P0 HEADLINE, Pitfall #298) — dieselbe Kohorte wie
    ``_evaluated_reward_terms``, aber OHNE Trials, die ``run_optimization``s
    ``inference_failure_policy='prune'``-Pfad (#864/#918) tatsaechlich gepruned hat
    (``trial.user_attrs['trial_pruned_inference_codes']`` wird DORT gesetzt, unmittelbar bevor
    ``optuna.TrialPruned()`` geworfen wird — VOR dem Prune bereits berechnete ``reward_terms``
    bleiben als User-Attr-Artefakt im Trial stehen, obwohl der Wert fuer Optuna selbst nie
    zaehlte). Ohne diesen Ausschluss traegt ein einzelner EQUITY_NONPOSITIVE/SORTINO_GUARD_
    TRIPPED-Trial (Reward-Betrag bis zu Faktor 50+ ueber der Skala der Shaping-Terme) die
    gemessene Reward-Varianz, obwohl der TPE-Sampler diesen Trial laengst korrekt ignoriert —
    ``check_reward_term_variance``/``reward_term_variance_table`` wuerden sonst gegen ein
    Artefakt der Failure-Branch-Varianz kalibrieren, nicht gegen die tatsaechliche
    Shaping-Landschaft der ZULAESSIGEN Region."""
    return [
        t.get("reward_terms") for t in trials
        if t.get("oos_evaluated") is True and t.get("reward_terms")
        and not t.get("trial_pruned_inference_codes")
    ]


def reward_term_variance_table(trials: list[dict]) -> list[dict[str, Any]]:
    """Issue #764/#927 — die VOLLSTAENDIGE Varianz-Tabelle je Reward-Term fuer den #742-Report,
    statt nur der binaeren inert/nicht-inert-Klassifikation von ``check_reward_term_variance``: je
    Term ``std`` (Streuung ueber die EVALUIERTE Kohorte, #927 — vorher die eligible Kohorte, die
    bei ``p_eligible == 0`` immer leer war) und ``var_contrib`` (Anteil der Term-VARIANZ an der
    SUMME aller Term-Varianzen, ``var_k / Σ var_j`` — die Groesse, gegen die der
    ``_REWARD_TERM_VARIANCE_CORRIDOR`` gemessen wird). ``in_target_corridor`` markiert Terme
    ausserhalb ``[0.02, 0.30]`` (Kandidaten fuer Entfernung bzw. Herunterskalierung, siehe #764 —
    die tatsaechliche Entscheidung braucht eine reale Kohorte, diese Tabelle liefert nur die Evidenz
    dafuer). ``configured_inactive`` markiert Terme mit konfiguriertem Gewicht 0.0 (aktuell
    ``tie_breaker``/``time_box_penalty``, siehe ``_CONFIGURED_INACTIVE_REWARD_TERMS``) — inert ist
    ihr dokumentierter Normalzustand, kein REWARD_TERM_INERT-Befund. ``eligible_std``/
    ``eligible_var_contrib`` ergaenzen die engere eligible Teilmenge, wenn sie nicht leer ist
    (``None`` sonst).

    Leere Liste bei < 2 evaluierten Trials mit ``reward_terms``."""
    evaluated_terms = _evaluated_reward_terms(trials)
    if len(evaluated_terms) < 2:
        return []
    eligible_terms = _eligible_reward_terms(trials)
    lo, hi = _REWARD_TERM_VARIANCE_CORRIDOR
    variances = {
        k: statistics.pvariance([float(t.get(k, 0.0)) for t in evaluated_terms])
        for k in _REWARD_TERM_NUMERIC_KEYS
    }
    total_var = sum(variances.values()) or 1.0
    eligible_variances: dict[str, float] | None = None
    eligible_total_var = 1.0
    if len(eligible_terms) >= 2:
        eligible_variances = {
            k: statistics.pvariance([float(t.get(k, 0.0)) for t in eligible_terms])
            for k in _REWARD_TERM_NUMERIC_KEYS
        }
        eligible_total_var = sum(eligible_variances.values()) or 1.0
    table = []
    for k in _REWARD_TERM_NUMERIC_KEYS:
        var_contrib = variances[k] / total_var
        entry = {
            "term": k,
            "std": round(variances[k] ** 0.5, 6),
            "var_contrib": round(var_contrib, 6),
            "in_target_corridor": bool(lo <= var_contrib <= hi),
            "configured_inactive": k in _CONFIGURED_INACTIVE_REWARD_TERMS,
            "eligible_std": None,
            "eligible_var_contrib": None,
        }
        if eligible_variances is not None:
            entry["eligible_std"] = round(eligible_variances[k] ** 0.5, 6)
            entry["eligible_var_contrib"] = round(eligible_variances[k] / eligible_total_var, 6)
        table.append(entry)
    return table


def gate_inventory_table(trials: list[dict], eligible_requires_all: list[str]) -> list[dict[str, Any]]:
    """Issue #970 (Katalog A, P1) — Gate-Inventur mit Entscheidungspflicht: die Selektion war im
    Referenzlauf 46cf5070 faktisch eine Ein-Statistik-Entscheidung (``oos_min_psr`` = 97.3% aller
    Ablehnungen, 62.7% aller evaluierten Trials solo), während 7 von 10 konfigurierten
    ``eligible_requires_all``-Gates in 2135 Evaluierungen KEIN einziges Mal ablehnten.

    Je konfiguriertem Gate: ``n_rejections`` (Trials, deren ``oos_gate_deltas[gate] > 0`` — das
    Gate verfehlt die Schwelle), ``n_solo_rejections`` (Trials, bei denen dieses Gate das EINZIGE
    verletzte unter ``eligible_requires_all`` ist — ein Trial, der ausschliesslich an diesem Gate
    scheitert), ``marginal_delta`` (``|eligible ohne dieses Gate| − |eligible mit allen Gates|`` —
    wie viele zusätzliche Trials eligible wären, würde man dieses Gate aus der Konjunktion
    entfernen; ``0`` heisst, das Gate trägt über die beobachtete Kohorte NICHTS zur Selektion bei,
    ein Kandidat für Entfernung oder Neukalibrierung).

    ``trials``: ``user_attrs``-artige Dicts mit ``oos_gate_deltas`` (dict Gate→Delta, ``> 0`` =
    verletzt, aus ``reward._normalized_gate_distances``/``_compute_oos_constraints``) UND
    ``oos_evaluated``. Nur über TATSÄCHLICH OOS-evaluierte Trials (ein nicht evaluierter Trial hat
    keine Gate-Deltas und trägt zu keinem Gate bei)."""
    evaluated = [t for t in trials if t.get("oos_evaluated") is True and t.get("oos_gate_deltas")]
    if not evaluated or not eligible_requires_all:
        return []
    table = []
    for gate in eligible_requires_all:
        n_rejections = 0
        n_solo = 0
        n_eligible_without_gate = 0
        n_eligible_with_all = 0
        for t in evaluated:
            deltas = t.get("oos_gate_deltas") or {}
            violated = {g for g in eligible_requires_all if float(deltas.get(g, 0.0) or 0.0) > 0.0}
            if gate in violated:
                n_rejections += 1
                if violated == {gate}:
                    n_solo += 1
            if not violated:
                n_eligible_with_all += 1
            if violated <= {gate}:
                n_eligible_without_gate += 1
        table.append({
            "gate": gate,
            "n_rejections": n_rejections,
            "n_solo_rejections": n_solo,
            "marginal_delta": n_eligible_without_gate - n_eligible_with_all,
            "n_evaluated": len(evaluated),
        })
    return table


def check_gate_marginal_contribution(
    study_records: list[dict], *, min_evaluated: int = 500,
) -> InvariantResult:
    """Issue #970 (Katalog A, P1) — kein Gate ohne jeden marginalen Beitrag darf über eine
    ausreichend grosse Kohorte (``min_evaluated`` OOS-evaluierte Trials, aufsummiert über alle
    Studies, in denen dieses Gate konfiguriert war) in ``eligible_requires_all`` verbleiben: ein
    Gate mit ``Σ marginal_delta == 0`` über >= ``min_evaluated`` Beobachtungen erhöht nur
    Rechenaufwand und Typ-II-Fehler durch Kollinearität (siehe ``gate_inventory_table``), ohne
    jemals die Eligibility-Entscheidung zu ändern.

    ``study_records``: Liste mit ``gate_inventory`` (aus ``report._study_record``, Liste von
    ``{gate, n_rejections, n_solo_rejections, marginal_delta, n_evaluated}``-Dicts)."""
    totals: dict[str, dict[str, int]] = {}
    for r in study_records:
        for entry in r.get("gate_inventory") or []:
            gate = entry.get("gate")
            if not gate:
                continue
            agg = totals.setdefault(gate, {"marginal_delta": 0, "n_evaluated": 0})
            agg["marginal_delta"] += int(entry.get("marginal_delta") or 0)
            agg["n_evaluated"] += int(entry.get("n_evaluated") or 0)
    if not totals:
        return InvariantResult(
            name="check_gate_marginal_contribution",
            passed=True,
            expected=f"kein Gate mit Σ marginal_delta == 0 über >= {min_evaluated} Beobachtungen",
            actual=None,
            detail="Keine Studies mit gate_inventory-Telemetrie — nicht anwendbar.",
        )
    offenders = {
        gate: agg for gate, agg in totals.items()
        if agg["n_evaluated"] >= min_evaluated and agg["marginal_delta"] == 0
    }
    passed = not offenders
    return InvariantResult(
        name="check_gate_marginal_contribution",
        passed=passed,
        expected=f"kein Gate mit Σ marginal_delta == 0 über >= {min_evaluated} Beobachtungen",
        actual=offenders if offenders else None,
        detail=("OK" if passed else
                f"{len(offenders)} Gate(s) ohne jeden marginalen Beitrag über eine ausreichend "
                f"grosse Kohorte: {offenders} — Kandidat(en) für Entfernung aus "
                "eligible_requires_all oder Neukalibrierung gegen die realisierte Verteilung."),
    )


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


def check_report_cohort_coherence(
    study_records: list[dict], *, wallclock_s: float | None,
    tolerance_s: float = 300.0,
) -> InvariantResult:
    """Issue #1023 (Katalog #866) Akzeptanzkriterium 2 — Regressionswaechter gegen eine erneute
    Vermischung fremder Laeufe im Report (siehe ``report._build_report``s ``run_id``-Filter): fuer
    JEDEN erzeugten Report muss gelten ``max(study_started_at_utc) − min(study_started_at_utc) <
    wallclock_s`` (plus ``tolerance_s`` Slack fuer Preflight/Dispatch-Overhead vor der ersten und
    Retention/Report-Schreibzeit nach der letzten Study) — eine Study, deren Startzeitstempel ausserhalb
    der Sweep-Laufzeit selbst liegt, kann nicht Teil DIESES Laufs sein, unabhaengig davon, ob sie
    einen ``run_id``-Stempel traegt (z. B. eine Uhr-Drift oder ein manuell verschobener Timestamp).

    ``study_records`` ist die bereits ``run_id``-gefilterte ``studies_out``-Liste (#1023 Fix Punkt
    1) — diese Invariante ist die zweite, unabhaengige Verteidigungslinie GEGEN denselben
    Fehlerklasse, nicht deren einzige."""
    timestamps = []
    for r in study_records:
        raw = r.get("study_started_at_utc")
        if not raw:
            continue
        try:
            timestamps.append(datetime.fromisoformat(raw))
        except (TypeError, ValueError):
            continue
    if len(timestamps) < 2 or wallclock_s is None or wallclock_s <= 0:
        return InvariantResult(
            name="check_report_cohort_coherence",
            passed=True,
            expected=f"< wallclock_s + {tolerance_s}s",
            actual=None,
            detail="Weniger als 2 Studies mit study_started_at_utc oder kein wallclock_s — nicht anwendbar.",
            severity="blocking",
        )
    span_s = (max(timestamps) - min(timestamps)).total_seconds()
    budget_s = wallclock_s + tolerance_s
    passed = span_s < budget_s
    return InvariantResult(
        name="check_report_cohort_coherence",
        passed=passed,
        expected=f"< {budget_s:.1f}s (wallclock_s={wallclock_s:.1f} + {tolerance_s:.0f}s Slack)",
        actual=round(span_s, 1),
        detail=("OK" if passed else
                f"study_started_at_utc-Spannweite={span_s:.1f}s >= {budget_s:.1f}s — mindestens "
                "eine Study liegt ausserhalb der Sweep-Laufzeit (#1023-Fehlerklasse: Report enthaelt "
                "Studies eines anderen Laufs)."),
        severity="blocking",
    )


def check_expectancy_definition_coherence(
    study_records: list[dict], *, max_relative_gap: float = 0.5, min_trades: int = 10,
) -> InvariantResult:
    """Issue #1031 (Katalog #866) — ``holdout_expectancy`` (Mittel von Quotienten,
    ``mean(pnl_i/notional_i)``) und ``holdout_expectancy_capital_weighted`` (Σpnl/Σnotional, gegen
    Nennerausreisser robust) messen denselben zugrundeliegenden Per-Trade-Edge — eine grosse
    relative Luecke zwischen beiden ist die Signatur eines einzelnen Round-Trips mit degeneriertem
    Nenner (oder einer ueber eine Preis-Sprungstelle gehaltenen Position), der den Mittelwert der
    Quotienten dominiert (beobachtet: expectancy=0,52 vs. implizit ~0,03 auf den drei TSLA-
    Kandidaten des Katalogs — Faktor 16).

    Nur Studies mit ``holdout_total_trades >= min_trades`` und definierten beiden Feldern werden
    geprüft (kleine Kohorten sind statistisch zu verrauscht, um diese Diagnose sinnvoll zu tragen)."""
    with_data = [
        r for r in study_records
        if r.get("holdout_expectancy") is not None
        and r.get("holdout_expectancy_capital_weighted") is not None
        and (r.get("holdout_total_trades") or 0) >= min_trades
    ]
    if not with_data:
        return InvariantResult(
            name="check_expectancy_definition_coherence",
            passed=True,
            expected=f"|expectancy - expectancy_capital_weighted| / max(|expectancy|, 1e-6) <= {max_relative_gap}",
            actual=None,
            detail=f"Keine Studies mit >= {min_trades} Holdout-Trades und beiden Expectancy-Feldern — nicht anwendbar.",
        )
    offenders: dict[str, float] = {}
    for r in with_data:
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        expectancy = float(r["holdout_expectancy"])
        capital_weighted = float(r["holdout_expectancy_capital_weighted"])
        gap = abs(expectancy - capital_weighted) / max(abs(expectancy), 1e-6)
        if gap > max_relative_gap:
            offenders[key] = round(gap, 4)
    passed = not offenders
    return InvariantResult(
        name="check_expectancy_definition_coherence",
        passed=passed,
        expected=f"<= {max_relative_gap}",
        actual=offenders if offenders else None,
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies: expectancy und expectancy_capital_weighted "
                f"divergieren relativ um mehr als {max_relative_gap} — mindestens ein Round-Trip mit "
                "degeneriertem Notional dominiert den Mittelwert der Quotienten (#1031)."),
    )


def check_open_position_at_data_end(
    study_records: list[dict], *, max_fraction: float = 0.02,
) -> InvariantResult:
    """Issue #1037 (Katalog #866) — ein Round-Trip, dessen Position am Ende der verfuegbaren
    Backtest-Daten noch offen war (``backtest_runner._finalize_round_trip``s ``is_data_end_
    fallback``, ``ExitReason.DATA_END``), ist keine echte Handelsentscheidung, sondern ein Artefakt
    des Datenendes — eine Position, die potenziell ueber eine Sprungstelle/mehrere Tage gehalten
    wird, mit entsprechend unrealistischen Per-Trade-Returns (dieselbe Konstellation wie die
    bimodale Haltedauerverteilung des Katalogs: kleiner Median, riesiges Maximum). FAIL, wenn mehr
    als ``max_fraction`` der Round-Trips einer Study auf diese Weise finalisiert wurden."""
    # Issue #1037 — ``n_round_trips_data_end`` ist ueber DIESELBE ``trial_attrs``-Summe abgeleitet
    # wie ``oos_total_trades_with_exit_telemetry`` (report._study_record, Issue #919) — beide
    # zaehlen ausschliesslich Trials mit Order-Tag-Exit-Telemetrie. ``holdout_total_trades`` ist
    # eine ANDERE Population (der einzelne, spaetere Holdout-Backtest) und waere ein
    # Nenner-Kategorienfehler (analog #1033/Pitfall #356).
    with_data = [
        r for r in study_records
        if r.get("n_round_trips_data_end") is not None and r.get("oos_total_trades_with_exit_telemetry")
    ]
    if not with_data:
        return InvariantResult(
            name="check_open_position_at_data_end",
            passed=True,
            expected=f"<= {max_fraction:.0%} der Round-Trips via DATA_END finalisiert",
            actual=None,
            detail="Keine Studies mit n_round_trips_data_end/oos_total_trades_with_exit_telemetry — nicht anwendbar.",
            severity="high",
        )
    offenders: dict[str, float] = {}
    for r in with_data:
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        total = r["oos_total_trades_with_exit_telemetry"]
        fraction = r["n_round_trips_data_end"] / total if total else 0.0
        if fraction > max_fraction:
            offenders[key] = round(fraction, 4)
    passed = not offenders
    return InvariantResult(
        name="check_open_position_at_data_end",
        passed=passed,
        expected=f"<= {max_fraction:.0%} der Round-Trips via DATA_END finalisiert",
        actual=offenders if offenders else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies: mehr als {max_fraction:.0%} der Round-Trips "
                "wurden am Datenende zwangsweise finalisiert (Position nie flat geworden), keine "
                "echte Handelsentscheidung (#1037)."),
    )


def check_sizing_identity_coherence(
    study_records: list[dict], *, max_relative_gap: float = 0.35,
    min_trades: int = 10, min_abs_expectancy: float = 1e-4,
) -> InvariantResult:
    """Issue #1028 (Katalog #866) — Sizing-Identität als stehender Kohärenztest (Pitfall #354): bei
    fixem, ungehebeltem Sizing (``trade_amount_pct``, gleich für JEDE Strategie) und
    nicht-überlappenden Positionen MUSS gelten
    ``f_implied = ln(1 + total_return) / (n · expectancy) · 100 ≈ trade_amount_pct``
    (``expectancy``/``total_return`` als Bruchteile, ``trade_amount_pct``/``f_implied`` in Prozent
    — siehe Referenzrechnung: Donchian/ADBE, n=32, expectancy=0,0071, TR=3,3 % ⇒ f_implied=14,3 %
    gegen konfigurierte 15 %). Eine grosse Abweichung ist die Signatur einer Datenanomalie in der
    zugrundeliegenden Preisreihe (Split-/Adjustierungsgrenze, Snapshot-Naht) — beobachtet: die drei
    TSLA-Kandidaten des Katalogs mit f_implied = 0,93 %/1,02 %/7,82 % gegen konfigurierte 15 %
    (Faktor 2–16), während 65 von 71 übrigen Studies desselben Laufs einen Median von 14,25 %
    trugen (Code dadurch entlastet — die Divergenz liegt in der Datenlage, nicht im Sizing-Pfad).

    Nur Studies mit ``n >= min_trades`` und ``|expectancy| >= min_abs_expectancy`` werden geprüft
    (kleine Nenner nahe Null sind Divisionsartefakte ohne Information, keine echten Kandidaten)."""
    with_data = [
        r for r in study_records
        if (r.get("holdout_total_trades") or 0) >= min_trades
        and r.get("holdout_expectancy") is not None
        and abs(r["holdout_expectancy"]) >= min_abs_expectancy
        and r.get("holdout_total_return") is not None
        and r.get("trade_amount_pct")
    ]
    if not with_data:
        return InvariantResult(
            name="check_sizing_identity_coherence",
            passed=True,
            expected=f"|f_implied - trade_amount_pct| / trade_amount_pct <= {max_relative_gap}",
            actual=None,
            detail="Keine Studies mit Holdout-Trades/Expectancy/trade_amount_pct — nicht anwendbar.",
            severity="blocking",
        )
    offenders: dict[str, dict] = {}
    for r in with_data:
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        n = r["holdout_total_trades"]
        expectancy = float(r["holdout_expectancy"])
        total_return = float(r["holdout_total_return"])
        trade_amount_pct = float(r["trade_amount_pct"])
        try:
            f_implied = (math.log(1.0 + total_return) / (n * expectancy)) * 100.0
        except (ValueError, ZeroDivisionError):
            continue
        gap = abs(f_implied - trade_amount_pct) / trade_amount_pct
        if gap > max_relative_gap:
            offenders[key] = {"f_implied_pct": round(f_implied, 4), "trade_amount_pct": trade_amount_pct}
    passed = not offenders
    return InvariantResult(
        name="check_sizing_identity_coherence",
        passed=passed,
        expected=f"|f_implied - trade_amount_pct| / trade_amount_pct <= {max_relative_gap}",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies: der aus (total_return, expectancy, n) implizierte "
                f"Sizing-Anteil weicht relativ um mehr als {max_relative_gap} vom konfigurierten "
                f"trade_amount_pct ab: {offenders} — Signatur einer Datenanomalie in der "
                "zugrundeliegenden Preisreihe, nicht des Sizing-Pfads (#1028)."),
    )


def check_atr_scale_homogeneity(
    study_records: list[dict], *, max_ratio: float = 6.0,
) -> InvariantResult:
    """Issue #1028 (Katalog #866) — ATR in bps ist skaleninvariant gegenüber einer reinen
    multiplikativen Preisadjustierung: über die Strategien EINES Symbols hinweg (dieselbe
    Preisreihe, dasselbe Fenster) sollte ``atr_median_bps`` innerhalb einer engen Bandbreite
    streuen. Ein grosser Faktor zwischen ``max``/``min`` je Symbol ist die Signatur einer
    Sprungstelle (Split-/Adjustierungsgrenze oder Snapshot-Naht) im ATR-Fenster einer oder mehrerer
    Strategien-Studies auf diesem Symbol — beobachtet: TSLA mit 18,2–366,2 bps (Faktor 20) gegen
    2–59 bps auf allen übrigen Symbolen des Katalog-Referenzlaufs."""
    by_symbol: dict[str, list[float]] = {}
    for r in study_records:
        atr = r.get("atr_median_bps")
        symbol = r.get("symbol")
        if atr and symbol:
            by_symbol.setdefault(symbol, []).append(float(atr))
    candidates = {sym: vals for sym, vals in by_symbol.items() if len(vals) >= 2}
    if not candidates:
        return InvariantResult(
            name="check_atr_scale_homogeneity",
            passed=True,
            expected=f"max(atr_median_bps)/min(atr_median_bps) <= {max_ratio} je Symbol",
            actual=None,
            detail="Kein Symbol mit >= 2 Strategien-Studies und atr_median_bps — nicht anwendbar.",
            severity="high",
        )
    offenders: dict[str, float] = {}
    for sym, vals in candidates.items():
        lo, hi = min(vals), max(vals)
        if lo <= 0:
            continue
        ratio = hi / lo
        if ratio > max_ratio:
            offenders[sym] = round(ratio, 2)
    passed = not offenders
    return InvariantResult(
        name="check_atr_scale_homogeneity",
        passed=passed,
        expected=f"max(atr_median_bps)/min(atr_median_bps) <= {max_ratio} je Symbol",
        actual=offenders if offenders else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Symbol(e) mit einer ATR-Spannweite über {max_ratio}x zwischen "
                f"Strategien: {offenders} — Signatur einer Sprungstelle in der Preisreihe (#1028)."),
    )


def check_worker_utilisation_plausible(
    worker_utilisation: float | None, *, max_ratio: float = 1.0,
) -> InvariantResult:
    """Issue #1038 (Katalog #866) — ``report._worker_utilisation`` (Σ Study-Wallclock / (n_jobs ×
    Sweep-Wallclock)) heisst "Auslastung", kann aber durch verschachtelte, studieneigene Worker-
    Pools (``backtest_runner.py``) oder — vor #1023 — eingemischte Studies fremder Laeufe ueber
    ``max_ratio`` (Default 1.0, physikalisch das Maximum einer echten Auslastung) hinaus wachsen.
    Beobachtet: 151,8 %/246,5 %/332,9 % ueber drei Laeufe. Ein Wert ueber der Schwelle ist ein FAIL,
    keine unkommentierte Anzeigezahl."""
    if worker_utilisation is None:
        return InvariantResult(
            name="check_worker_utilisation_plausible",
            passed=True,
            expected=f"<= {max_ratio}",
            actual=None,
            detail="Kein worker_utilisation-Wert — nicht anwendbar.",
        )
    passed = worker_utilisation <= max_ratio
    return InvariantResult(
        name="check_worker_utilisation_plausible",
        passed=passed,
        expected=f"<= {max_ratio}",
        actual=round(worker_utilisation, 4),
        detail=("OK" if passed else
                f"worker_utilisation={worker_utilisation:.4f} > {max_ratio} — Σ Study-Wallclock "
                "ueberlappt sich (verschachtelte Worker-Pools und/oder eingemischte Studies eines "
                "anderen Laufs, #1038-Fehlerklasse)."),
    )


def check_reward_term_variance(trials: list[dict], *, inert_ratio: float = 0.01) -> InvariantResult:
    """Verallgemeinerung von ``REWARD_TERM_INERT`` (run_optimization.py, Issue #621): statt einer
    einzelnen WARNING-Zeile pro inertem Term liefert diese Pruefung die VOLLSTAENDIGE Liste ueber
    alle EVALUIERTEN Trials einer Study (Issue #927 — vorher die eligible Kohorte, siehe
    ``_evaluated_reward_terms``-Docstring/Pitfall #302). Ein Term gilt als inert, wenn seine
    Streuung < ``inert_ratio`` der Streuung des Gesamt-Rewards ist — derselbe Schwellenwert wie das
    Original (``std_k < 0.01 * rew_std``). Terme in ``_CONFIGURED_INACTIVE_REWARD_TERMS`` (Issue
    #927 Fix 2 — Gewicht 0.0 ist ihr dokumentierter Normalzustand) werden von der Alarm-Liste
    ausgenommen, auch wenn ihre Streuung technisch unter der Schwelle liegt.

    ``trials`` ist eine Liste von ``user_attrs``-artigen Dicts (je Trial ``oos_evaluated`` +
    ``reward_terms``), NICHT Optuna-``Trial``-Objekte — pure Funktion, synthetisch testbar."""
    evaluated_terms = _evaluated_reward_terms(trials)
    if len(evaluated_terms) < 2:
        return InvariantResult(
            name="check_reward_term_variance",
            passed=True,
            expected=[],
            actual=[],
            detail="< 2 evaluierte Trials mit reward_terms — keine Varianz-Aussage moeglich.",
        )
    rew_vals = [
        (t.get("base", 0.0) - t.get("divergence", 0.0) - t.get("dd_penalty", 0.0)
         - t.get("param_pen", 0.0) - t.get("turnover", 0.0) - t.get("fold_dispersion", 0.0)
         + t.get("tie_breaker", 0.0))
        for t in evaluated_terms
    ]
    rew_std = statistics.pstdev(rew_vals)
    inert_terms = []
    for k in _REWARD_TERM_NUMERIC_KEYS:
        if k in _CONFIGURED_INACTIVE_REWARD_TERMS:
            continue
        vals = [float(t.get(k, 0.0)) for t in evaluated_terms]
        if all(v == 0.0 for v in vals):
            continue
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


def _reward_std_total_and_feasible(trials: list[dict]) -> tuple[float | None, float | None, list[dict]]:
    """Issue #949 — gemeinsame Berechnung von ``reward_std_total`` (ueber ALLE oos_evaluated
    Trials, inkl. spaeter gepruneter) und ``reward_std_feasible`` (ohne geprunte Trials, siehe
    ``_feasible_reward_terms``). Rueckgabe: ``(reward_std_total, reward_std_feasible,
    feasible_terms)`` — ``feasible_terms`` wird von ``check_reward_dynamic_range`` fuer die
    Term-Varianzen wiederverwendet, damit beide gegen DIESELBE Kohorte rechnen."""
    total_terms = _evaluated_reward_terms(trials)
    feasible_terms = _feasible_reward_terms(trials)

    def _rew_std(terms: list[dict]) -> float | None:
        if len(terms) < 2:
            return None
        vals = [
            (t.get("base", 0.0) - t.get("divergence", 0.0) - t.get("dd_penalty", 0.0)
             - t.get("param_pen", 0.0) - t.get("turnover", 0.0) - t.get("fold_dispersion", 0.0)
             + t.get("tie_breaker", 0.0))
            for t in terms
        ]
        return statistics.pstdev(vals)

    return _rew_std(total_terms), _rew_std(feasible_terms), feasible_terms


def check_reward_dynamic_range(trials: list[dict], *,
                               min_base_dominance_factor: float = 4.0,
                               min_reward_std_feasible: float = 0.05,
                               max_total_to_feasible_ratio: float = 3.0) -> InvariantResult:
    """Issue #949 (Katalog C, P0 HEADLINE, Pitfall #298) — der eigentliche Wächter gegen den
    Katalog-C-Kernbefund: die Reward-Varianz einer Study wurde von Zweig-Indikatoren
    (EQUITY_NONPOSITIVE/SORTINO_GUARD_TRIPPED-Trials, Reward-Betrag bis Faktor ~50 ueber der
    Shaping-Term-Skala) getragen statt von der Qualitaetsordnung innerhalb der zulaessigen
    Region — ``reward_std`` bis zu 53.99 gegen Shaping-Terme, die nur auf einer Skala < 1 wirken
    duerfen (< 1 % von 53.99 = 0.54). Drei Klauseln, ALLE muessen erfuellt sein:

    1. ``reward_std_feasible >= min_base_dominance_factor * max_j std(term_j)`` — die Basis
       (Qualitaetsordnung) dominiert die Straf-Terme auf der ZULAESSIGEN Region, nicht umgekehrt.
    2. ``reward_std_feasible >= min_reward_std_feasible`` — das Objektiv ist auf der zulaessigen
       Region nicht flach (TPE hat ueberhaupt einen Gradienten zum Lernen).
    3. ``reward_std_total / reward_std_feasible <= max_total_to_feasible_ratio`` — der
       Failure-Zweig (inkl. gepruneter Trials, deren `reward_terms` als User-Attr-Artefakt
       stehen bleiben) dominiert die Gesamt-Varianz NICHT gegenueber der zulaessigen Basis. Diese
       dritte Klausel ist der eigentliche Wächter gegen den Katalog-C-Defekt.

    ``trials`` — dieselbe ``user_attrs``-artige Liste wie ``check_reward_term_variance``.
    < 2 auswertbare (feasible) Trials ⇒ nicht anwendbar (PASS, keine Varianz-Aussage möglich)."""
    reward_std_total, reward_std_feasible, feasible_terms = _reward_std_total_and_feasible(trials)
    if reward_std_feasible is None:
        return InvariantResult(
            name="check_reward_dynamic_range",
            passed=True,
            expected=f">= {min_base_dominance_factor} * max(term_std) UND >= "
                     f"{min_reward_std_feasible} UND total/feasible <= {max_total_to_feasible_ratio}",
            actual=None,
            detail="< 2 feasible (nicht-gepruntete) evaluierte Trials — keine Varianz-Aussage "
                   "möglich.",
        )
    term_stds = {
        # Issue #949 — "base" (die Qualitaetsordnung selbst) ist ABSICHTLICH ausgeschlossen: die
        # Klausel misst, ob die STRAF-/SHAPING-Terme die Basis dominieren, nicht ob die Basis sich
        # selbst dominiert (das waere tautologisch nie erfuellbar, sobald die Straf-Terme nahe
        # null liegen — reward ~= base, std(reward) ~= std(base), Faktor 1x statt >= 4x).
        k: statistics.pstdev([float(t.get(k, 0.0)) for t in feasible_terms])
        for k in _REWARD_TERM_NUMERIC_KEYS
        if k not in _CONFIGURED_INACTIVE_REWARD_TERMS and k != "base"
    }
    max_term_std = max(term_stds.values()) if term_stds else 0.0
    dominance_ok = reward_std_feasible >= min_base_dominance_factor * max_term_std
    not_flat_ok = reward_std_feasible >= min_reward_std_feasible
    ratio = (reward_std_total / reward_std_feasible) if reward_std_feasible > 0 else float("inf")
    ratio_ok = (reward_std_total is None) or ratio <= max_total_to_feasible_ratio
    passed = dominance_ok and not_flat_ok and ratio_ok
    failed_clauses = []
    if not dominance_ok:
        failed_clauses.append(
            f"reward_std_feasible={reward_std_feasible:.4f} < {min_base_dominance_factor} * "
            f"max_term_std={max_term_std:.4f}")
    if not not_flat_ok:
        failed_clauses.append(
            f"reward_std_feasible={reward_std_feasible:.4f} < {min_reward_std_feasible} "
            "(Objektiv praktisch flach)")
    if not ratio_ok:
        failed_clauses.append(
            f"reward_std_total/reward_std_feasible={ratio:.2f} > {max_total_to_feasible_ratio} "
            "(Failure-Zweig dominiert die Basis)")
    return InvariantResult(
        name="check_reward_dynamic_range",
        passed=passed,
        expected=f">= {min_base_dominance_factor} * max(term_std) UND >= "
                 f"{min_reward_std_feasible} UND total/feasible <= {max_total_to_feasible_ratio}",
        actual={"reward_std_total": round(reward_std_total, 6) if reward_std_total is not None else None,
               "reward_std_feasible": round(reward_std_feasible, 6),
               "max_term_std": round(max_term_std, 6)},
        severity="high",
        detail="OK" if passed else "; ".join(failed_clauses),
    )


def check_champion_writeback_reachability(champions_summary: dict) -> InvariantResult:
    """Issue #818-Regressionswächter (achter Invarianten-Check, siehe #742/#749).

    ``champions.maybe_write_back`` (Ebene 2 des Epics #702, Issue #706) hatte KEINE Produktions-
    Call-Site — die dokumentierte, getestete Funktion wurde vom laufenden Sweep nie aufgerufen
    (76 von 76 Store-Einträgen trugen ``lifecycle.writeback_applied == false``, exakt die
    Fehlerklasse aus Pitfall #237: ein Fix gilt als "gemergt", sobald der Code existiert, statt
    sobald ein gemessenes Akzeptanzkriterium in einem realen Lauf erfüllt ist).

    FAIL, wenn der Champion-Store nicht-leer ist (``stored > 0``) UND KEIN einziger Eintrag jemals
    zurückgeschrieben wurde (``written_back == 0``) — die reine Anwesenheit von Champions ohne
    JEDEN Writeback-Erfolg über potenziell viele Läufe hinweg ist der direkte Fingerabdruck einer
    unerreichbaren Ebene 2. ``champions_summary`` ist ``report._champions_summary()``'s
    ``{'stored', 'admissible', 'corroborated', 'written_back', 'skipped_by_reason'}``-Dict."""
    stored = int(champions_summary.get("stored") or 0)
    written_back = int(champions_summary.get("written_back") or 0)
    if stored == 0:
        return InvariantResult(
            name="check_champion_writeback_reachability",
            passed=True,
            expected="written_back > 0, sobald stored > 0",
            actual={"stored": 0, "written_back": 0},
            detail="Kein Champion-Store-Eintrag — nicht anwendbar.",
        )
    passed = written_back > 0
    return InvariantResult(
        name="check_champion_writeback_reachability",
        passed=passed,
        expected="written_back > 0, sobald stored > 0",
        actual={"stored": stored, "written_back": written_back},
        detail=("OK" if passed else
                f"{stored} Champion-Store-Eintraege, aber 0 Writebacks ueber den gesamten "
                "Store-Stand — Ebene 2 (#706) ist vermutlich unerreichbar (#818-Regression: "
                "maybe_write_back ohne Produktions-Call-Site)."),
    )


def check_diagnosis_actionability(diagnosed_pairs: list[dict], *, min_count: int = 50) -> InvariantResult:
    """Issue #829 Fix Punkt 5 (neunter Invarianten-Check, Pitfall #258) — die maschinelle Form der
    Frage "warum ändert sich hier nichts?": FAIL, wenn ``diagnosed_pairs_cache.json`` (aus
    ``sweep_diagnostics.load_diagnosed_pairs_cache``, hier als Liste ihrer Einträge übergeben) für
    dieselbe ``(strategy, binding_cause)``-Kombination MINDESTENS ``min_count`` Paare mit
    ``action == 'none'`` trägt.

    Root-Cause #829/#258: zwei Evidenzschwellen desselben Mechanismus (eine Abbruchregel, die den
    Budgetanteil kappt — #805 — und eine Aktionsregel, die einen Mindest-Budgetanteil verlangt —
    #778) können einen Deadlock bilden, in dem die Ursache, die den Mechanismus auslösen soll, die
    Schwelle STRUKTURELL nie erreicht (im Referenzlauf: 138 Studies über zwei Strategien,
    ``action == 'none'`` in JEDEM Fall). Ein Diagnose-Cache, der über viele Symbole hinweg
    ausschliesslich ``'none'`` für dieselbe Ursache meldet, ist der direkte Fingerabdruck eines
    solchen Deadlocks — unabhängig davon, ob die konkrete Ursache ``signal_absent`` (#829) oder eine
    künftige, strukturell ähnliche Kombination ist."""
    from collections import Counter
    none_counts: Counter = Counter()
    for entry in diagnosed_pairs or []:
        if entry.get("action") != "none":
            continue
        key = (entry.get("strategy"), entry.get("binding_cause"))
        if key[0] is None or key[1] is None:
            continue
        none_counts[key] += 1
    offenders = {f"{strategy}/{cause}": n for (strategy, cause), n in none_counts.items() if n >= min_count}
    passed = not offenders
    return InvariantResult(
        name="check_diagnosis_actionability",
        passed=passed,
        expected=f"< {min_count} Paare je (strategy, binding_cause) mit action=='none'",
        actual=offenders if offenders else None,
        detail=("OK" if passed else
                f"{len(offenders)} (strategy, binding_cause)-Kombination(en) melden >= {min_count} "
                f"Paare mit action=='none': {offenders} — vermutlich ein Evidenzschwellen-Deadlock "
                "(Pitfall #258), analog #829."),
    )


def check_holding_time_cap(study_records: list[dict], *,
                           study_tolerance: float = 0.25,
                           hard_multiple: float = 3.0,
                           max_bars_in_trade_cap: float = _MAX_BARS_IN_TRADE_CAP,
                           timebox_execution_slack_bars: float = 3.0,
                           bar_seconds: float = _BAR_SECONDS_DEFAULT) -> InvariantResult:
    """Issue #832 Fix Punkt 1 (Katalog #828-#835, GitHub-Issue #751) — Plausibilitätswächter gegen
    die #714/GR-01-Zeitbox: eine Study, deren Anteil zeitbox-verletzender TRADES
    ``study_tolerance`` überschreitet, hat einen Bug im Exit-Pfad (defekter Watchdog/Cancel-Pfad),
    keine tolerierbare Ausführungslatenz mehr.

    Issue #1036 (Katalog #866) — ZWEITER, magnitudenbasierter Ast neben dem Anteils-Ast oben:
    ``timebox_violating_trades_frac`` verdünnt eine EINZELNE, ökonomisch untragbare Position (z. B.
    3991 h bei einer 24-Bar-Zeitbox) über einen gross gepoolten Round-Trip-Nenner (beobachtet:
    128 347 Round-Trips ⇒ eine Verletzung um Faktor 166 verdünnt, 3 % statt eines Alarms) —
    ``check_holding_time_cap`` stand auf PASS, während ``timebox_violation_intensity_p95``
    telemetriert, aber nie GEGATET war. "Wie viele" (Anteil) und "wie schlimm" (Magnitude) sind
    zwei verschiedene Fragen (Pitfall #358); dieser Ast beantwortet die zweite: JEDE Study mit
    ``max_holding_time_s > hard_multiple · cap_s`` (``cap_s = (max_bars_in_trade_cap +
    timebox_execution_slack_bars) · bar_seconds``, ``hard_multiple`` Default 3.0,
    ``tournament.json['timebox_violation_hard_multiple']``) FAILt UNABHÄNGIG vom Anteil.

    Issue #971 (Katalog B, P0 HEADLINE, Pitfall #303/#304 in AGENTS.md) — konsumiert jetzt
    ``timebox_violating_trades_frac`` (TRADE-/Round-Trip-Ebene, ``report._study_record``) statt der
    vorherigen ``timebox_violation_fraction`` (TRIAL-Ebene). Root-Cause der Divergenz: der #857-Fix
    stempelt ``oos_evaluated=False`` auf JEDEN Trial mit MINDESTENS EINEM zeitbox-verletzenden
    Round-Trip — ein Trial mit z. B. 150 sauberen Trades und einem einzigen Ausreisser zählte damit
    TRIAL-weise zu 100% "verletzend", obwohl nur ein Bruchteil seiner Trades betroffen war. Auf einem
    Referenzlauf (``46cf5070``) reproduzierte die TRIAL-Quote für ``DynamicBreakoutStrategy/
    GSAT.ETORO`` bit-genau ``(n_trials - evaluable_trials) / n_trials`` (0.2985 = 20/67) — eine
    Grösse, die nichts mit der Haltedauer zu tun hat (``hit_trade_cap`` war in 2651/2651 Trials
    ``False``, ``time_box_penalty`` in 2651/2651 Trials ``0.0``, siehe #973). Die TRADE-Ebene
    (``timebox_violating_trades_frac``, dieselbe Berechnung wie ``confirm.py``s
    ``timebox_round_trip_violation_fraction``-Prüfung, #903 Fix 2) misst die tatsächlich betroffene
    Trade-Fraktion und ist die einzige Grösse, die der Docstring/Name des Checks verspricht.

    Issue #861 (Unifikation, Pitfall #271) — konsumiert dieselbe per-Trial-aware Berechnung wie
    ``compute_trial_timebox_violations``. ``study_tolerance`` ist dieselbe Schwelle wie
    ``tournament.json['timebox_violation_study_tolerance']`` (#857) — beide Wächter beantworten
    jetzt exakt dieselbe Frage ("ist dieser Exit-Pfad strukturell defekt?") gegen dieselbe Referenz
    UND dieselbe Aggregationsebene (Trades, #903 Fix 2)."""
    cap_s = (float(max_bars_in_trade_cap) + float(timebox_execution_slack_bars)) * float(bar_seconds)
    hard_threshold_s = hard_multiple * cap_s
    # Issue #1036 — der Magnituden-Ast konsumiert max_holding_time_s (report._study_record,
    # bereits vorhanden seit #832) unabhängig von timebox_violating_trades_denominator — eine
    # Study kann eine ökonomisch untragbare Einzelposition tragen, ohne dass der Anteils-Zähler
    # (Round-Trip-Ebene) je verdrahtet war.
    magnitude_offenders = {
        f"{r.get('strategy')}/{r.get('symbol')}": round(r["max_holding_time_s"] / cap_s, 2)
        for r in study_records
        if r.get("max_holding_time_s") is not None and r["max_holding_time_s"] > hard_threshold_s
    }

    with_data = [r for r in study_records if r.get("timebox_violating_trades_denominator")]
    fraction_offenders = {
        f"{r.get('strategy')}/{r.get('symbol')}": r.get("timebox_violating_trades_frac")
        for r in with_data
        if (r.get("timebox_violating_trades_frac") or 0.0) > study_tolerance
    }

    if not with_data and not magnitude_offenders:
        return InvariantResult(
            name="check_holding_time_cap",
            passed=True,
            expected=f"Anteil zeitbox-verletzender Trades <= {study_tolerance} UND "
                     f"max_holding_time_s <= {hard_multiple}x cap je Study",
            actual=None,
            detail="Keine Studies mit Haltedauer-Telemetrie (Pre-#832-Report oder leere Kohorte) — "
                   "nicht anwendbar.",
            severity="blocking",
        )
    passed = not fraction_offenders and not magnitude_offenders
    # Issue #971 Fix Punkt 2 — Herkunftspflicht für blockierende Invarianten: numerator/denominator/
    # numerator_definition/source_field je offending Study, damit die gemeldete Zahl aus der
    # Telemetrie nachrechenbar bleibt (statt wie zuvor eine unbelegte Rundungszahl zu sein).
    # ``actual``/``provenance['per_study']`` bleiben fuer den Anteils-Ast BIT-IDENTISCH zum Pre-
    # #1036-Schema (bestehende Konsumenten lesen ``actual[key]`` als flachen Bruchwert); der neue
    # Magnituden-Ast wird als EIGENER, zusaetzlicher provenance-Schluessel gefuehrt statt das
    # bestehende Schema zu brechen.
    provenance = None
    if fraction_offenders or magnitude_offenders:
        provenance = {
            "source_field": "timebox_violating_trades_frac",
            "numerator_definition": (
                "timebox_violating_trades_numerator = Anzahl OOS-Round-Trips (Trades) mit "
                "holding_bars > max_bars_in_trade + timebox_execution_slack_bars, über alle Trials "
                "mit OOS-Handelsaktivität dieser Study (unabhängig davon, ob der Trial "
                "nachträglich wegen eben dieser Verletzung auf oos_evaluated=False umgestempelt "
                "wurde, #857)."),
            "per_study": {
                key: {
                    "numerator": r.get("timebox_violating_trades_numerator"),
                    "denominator": r.get("timebox_violating_trades_denominator"),
                }
                for r in with_data
                if (key := f"{r.get('strategy')}/{r.get('symbol')}") in fraction_offenders
            },
            # Issue #1036 — zweiter, magnitudenbasierter Ast (Pitfall #358): eine Study kann eine
            # ökonomisch untragbare Einzelposition tragen, ohne dass der gepoolte Anteils-Ast sie
            # detektiert (siehe check_holding_time_cap-Docstring).
            "magnitude_hard_multiple": hard_multiple,
            "magnitude_cap_s": cap_s,
            "magnitude_offenders": magnitude_offenders or None,
        }
    detail_parts = []
    if fraction_offenders:
        detail_parts.append(
            f"Anteils-Ast: {len(fraction_offenders)} Study/Studies überschreiten den Zeitbox-"
            f"Study-Toleranzwert ({study_tolerance}) auf TRADE-Ebene: {fraction_offenders} — Bug im "
            "Exit-Pfad (HourlyStrategyBase erzwingt den Zeit-Exit nicht durchgängig), keine "
            "tolerierbare Ausführungslatenz mehr.")
    if magnitude_offenders:
        detail_parts.append(
            f"Magnituden-Ast (#1036): {len(magnitude_offenders)} Study/Studies mit einer "
            f"Einzelposition > {hard_multiple}x der Zeitbox ({hard_threshold_s:.0f}s): "
            f"{magnitude_offenders} (Vielfaches der Zeitbox je Study) — ökonomisch untragbar "
            "unabhängig vom gepoolten Anteil (Pitfall #358).")
    return InvariantResult(
        name="check_holding_time_cap",
        passed=passed,
        expected=f"Anteil zeitbox-verletzender Trades <= {study_tolerance} UND "
                 f"max_holding_time_s <= {hard_multiple}x cap je Study",
        actual=fraction_offenders or None,
        severity="blocking",
        provenance=provenance,
        detail="OK" if passed else " ".join(detail_parts),
    )


def check_counter_partition_consistency(study_records: list[dict]) -> InvariantResult:
    """Issue #972 (Katalog B, Pitfall #304 in AGENTS.md) — Regressionswächter gegen den Zero-
    Eligible-Plateau-Zähler-Widerspruch: der Plateau-Zähler (``plateau_counter_breakdown``,
    ``run_optimization._optimize_symbol_impl``) meldet, wie sich ``n_trials`` in
    ``n_evaluated`` (Überlebende) und eine Zerlegung der ENTFERNTEN Trials
    (``invalidated_timebox``/``invalidated_trade_cap``/``discarded_is_gate``/
    ``window_unreachable``/``not_evaluated``) aufteilt. Für JEDES Study-Paar, dessen Zähler
    dieselbe Grundgesamtheit (``n_trials``) beschreiben, MUSS gelten:
    ``n_evaluated + sum(breakdown.values()) == n_trials`` — sonst zielt mindestens einer der
    beiden Zähler NICHT auf dieselbe Grundgesamtheit (Root-Cause #972: der alte Plateau-Zähler
    lief über die bereits vorgefilterten Überlebenden statt über ``n_trials``, wodurch
    "0/N trafen die Grenze" strukturell nicht widerlegbar war)."""
    with_data = [r for r in study_records if r.get("plateau_counter_breakdown") is not None]
    if not with_data:
        return InvariantResult(
            name="check_counter_partition_consistency",
            passed=True,
            expected="n_evaluated + sum(plateau_counter_breakdown.values()) == n_trials je Study",
            actual=None,
            detail="Keine Studies mit Plateau-Zerlegung (kein Zero-Eligible-Plateau in diesem Lauf "
                   "oder Pre-#972-Report) — nicht anwendbar.",
            severity="high",
        )
    offenders: dict[str, dict[str, int]] = {}
    for r in with_data:
        n_trials = r.get("n_trials")
        n_eval = r.get("plateau_n_evaluated")
        breakdown = r.get("plateau_counter_breakdown") or {}
        if n_trials is None or n_eval is None:
            continue
        total = int(n_eval) + sum(int(v) for v in breakdown.values())
        if total != int(n_trials):
            offenders[f"{r.get('strategy')}/{r.get('symbol')}"] = {
                "n_trials": int(n_trials), "n_evaluated": int(n_eval),
                "breakdown_sum": total - int(n_eval), "reconstructed_total": total,
            }
    passed = not offenders
    return InvariantResult(
        name="check_counter_partition_consistency",
        passed=passed,
        expected="n_evaluated + sum(plateau_counter_breakdown.values()) == n_trials je Study",
        actual=offenders if offenders else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies: der Plateau-Zähler und n_trials zerlegen die "
                f"Trial-Menge NICHT disjunkt/vollständig: {offenders} — mindestens einer der "
                "beteiligten Zähler zielt nicht auf dieselbe Grundgesamtheit (Pitfall #304)."),
    )


def check_effective_stop_distance(study_records: list[dict], *,
                                  min_ratio: float = 0.4,
                                  min_trailing_stop_exits: int = 30) -> InvariantResult:
    """Issue #897 Fix 3 — je Study wird der Median des realisierten Ø-Bruttoverlusts gegen den
    konfigurierten Stop-Abstand ``k_median · ATR_median`` (``atr_trailing_multiplier_median`` ×
    ``atr_median_bps``) geprüft.

    Issue #1035 (Katalog #866) — Root-Cause: der Zähler mass bislang über ALLE Verlust-Trades
    (``oos_gross_loss_mean_bps``), nicht nur über nachweisliche Stop-Exits. Bei überwiegend
    UNKNOWN-/TIME_BOX-Exits (vor #1034 häufig > 50 %) hat der Stop den grössten Teil der Trades nie
    berührt — der Check FAILte auf der FALSCHEN Grundgesamtheit (0,09 statt der tatsächlichen
    Stop-Exit-Quote; bestätigt Hypothese (a) aus #1008, entkräftet Hypothese (b) "ATR kollabiert").
    Der Zähler ist jetzt ``oos_gross_loss_mean_bps_trailing_stop`` (NUR TRAILING_STOP-Exits, #1034
    Voraussetzung: die Order-Tag-Klassifikation muss überhaupt Stop-Exits von anderen Ausgängen
    unterscheiden können). Liegen weniger als ``min_trailing_stop_exits`` Stop-Exits vor, ist die
    Stichprobe zu klein für ein Urteil — ``INCONCLUSIVE`` (impliziert ``passed=True``, aber vom
    PASS unterscheidbar) statt eines FAILs auf einer Handvoll Beobachtungen.

    Fällt der Quotient unter ``min_ratio`` (Default 0.4, entspricht
    ``optimizer.json['stop_distance_min_ratio']``), reagiert der realisierte Stop-Verlust nicht
    (mehr) auf seinen eigenen Multiplikator — der Mechanismus ist keine kalibrierte Risikogrösse,
    sondern eine Breakeven-Klemme, die auf der Volatilitätsschätzung statt auf dem Preis-Extremum
    rastet (Pitfall #285/#286 in AGENTS.md)."""
    candidates = [
        r for r in study_records
        if r.get("atr_median_bps")
        and r.get("atr_trailing_multiplier_median") is not None
        and (r.get("oos_gross_loss_mean_bps_trailing_stop") is not None
             or r.get("oos_gross_loss_mean_bps") is not None)
    ]
    if not candidates:
        return InvariantResult(
            name="check_effective_stop_distance",
            passed=True,
            expected=f"Ø-Bruttoverlust (Stop-Exits) / (k_median · ATR_median) >= {min_ratio} je Study",
            actual=None,
            detail="Keine Studies mit Exit-Telemetrie (Issue #899) — nicht auswertbar.",
            severity="high",
        )
    offenders: dict[str, float] = {}
    inconclusive_studies: dict[str, int] = {}
    with_data: list[dict] = []
    for r in candidates:
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        n_stop_exits = int(r.get("oos_n_trailing_stop_losses") or 0)
        loss_bps = r.get("oos_gross_loss_mean_bps_trailing_stop")
        if loss_bps is None or n_stop_exits < min_trailing_stop_exits:
            # Issue #1035 — auch OHNE #1034/#1035-Telemetrie (Legacy-Report, nur das ungefilterte
            # oos_gross_loss_mean_bps) ist die Grundgesamtheit unbekannt/unbelegt: INCONCLUSIVE
            # statt eines FAILs auf einer nicht nachweislich richtigen Zahl.
            inconclusive_studies[key] = n_stop_exits
            continue
        with_data.append(r)
        configured_distance_bps = float(r["atr_trailing_multiplier_median"]) * float(r["atr_median_bps"])
        if configured_distance_bps <= 0:
            continue
        ratio = float(loss_bps) / configured_distance_bps
        if ratio < min_ratio:
            offenders[key] = round(ratio, 4)
    passed = not offenders
    if not with_data:
        return InvariantResult(
            name="check_effective_stop_distance",
            passed=True,
            expected=f"Ø-Bruttoverlust (Stop-Exits) / (k_median · ATR_median) >= {min_ratio} je Study",
            actual=inconclusive_studies if inconclusive_studies else None,
            severity="high",
            detail=f"Keine Study mit >= {min_trailing_stop_exits} nachweislichen TRAILING_STOP-"
                   "Exits (#1034 Voraussetzung) — INCONCLUSIVE statt eines Urteils auf zu kleiner "
                   "Stichprobe.",
            inconclusive=True,
        )
    return InvariantResult(
        name="check_effective_stop_distance",
        passed=passed,
        expected=f"Ø-Bruttoverlust (Stop-Exits) / (k_median · ATR_median) >= {min_ratio} je Study",
        actual=offenders if offenders else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies unterschreiten das Verhältnis Ø-Bruttoverlust "
                f"(Stop-Exits) / konfigurierter Stop-Abstand ({min_ratio}): {offenders} — der Stop "
                "reagiert nicht auf seinen eigenen Multiplikator (Pitfall #286) und rastet "
                "vermutlich auf der ATR-Schätzung statt auf dem Preis-Extremum (Pitfall #285, "
                "Issue #897)."),
    )


def check_n_periods_homogeneity(study_records: list[dict], *,
                                max_ratio: float = 6.0) -> InvariantResult:
    """Issue #923 — ``oos_n_periods_median`` (#862) streut je nach Strategie stark selbst
    INNERHALB desselben Symbols (unterschiedliche Handelsfrequenz ⇒ unterschiedlich viele Bars
    mit Rendite ≠ 0) — eine Spannweite von Faktor 11,3 auf demselben Symbol XOM wurde beobachtet.
    ``n_periods`` ist gleichzeitig (a) der Nenner jeder Sortino-/PSR-Schätzung, (b) die
    Referenzgrösse des numerischen Guards (#916), (c) die Eingangsgrösse für
    ``deflation_max_n_periods_ratio`` (#865) — bei starker Heterogenität greift die
    #865-Heterogenitäts-Suppression für praktisch jede Familie, ``deflated_dsr`` bleibt ``None``,
    und ``None`` muss nach Pitfall #277 ablehnen. Die Heterogenität ist damit ein stiller
    Promotions-Blocker, unabhängig von #913.

    Gruppiert ``study_records`` nach ``symbol`` und vergleicht je Symbol
    ``max(oos_n_periods_median) / min(oos_n_periods_median)`` gegen ``max_ratio`` (Default 6.0,
    der Kalibrierpunkt für ``deflation_max_n_periods_ratio``, dort heute 4.0). ``severity='high'``
    (nicht ``'blocking'``) — die Heterogenität selbst blockiert keine einzelne Study, sie ist ein
    Diagnosesignal für die #865-Kalibrierung."""
    by_symbol: dict[str, list[float]] = {}
    for r in study_records:
        symbol = r.get("symbol")
        median = r.get("oos_n_periods_median")
        if symbol is None or median is None:
            continue
        by_symbol.setdefault(symbol, []).append(float(median))
    offenders: dict[str, float] = {}
    for symbol, medians in by_symbol.items():
        if len(medians) < 2:
            continue
        lo, hi = min(medians), max(medians)
        if lo <= 0:
            continue
        ratio = hi / lo
        if ratio > max_ratio:
            offenders[symbol] = round(ratio, 2)
    passed = not offenders
    return InvariantResult(
        name="check_n_periods_homogeneity",
        passed=passed,
        expected=f"max(oos_n_periods_median) / min(oos_n_periods_median) <= {max_ratio} je Symbol",
        actual=offenders if offenders else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Symbol(e) mit n_periods-Spannweite > {max_ratio}: {offenders} — "
                "die #865-Heterogenitäts-Suppression (deflation_max_n_periods_ratio) greift "
                "vermutlich für praktisch jede Familie dieses Symbols (Issue #923)."),
    )


def check_cost_model_resolution(cost_model_events: list[dict], *,
                                max_default_fallback_fraction: float = 0.0) -> InvariantResult:
    """Issue #898 Fix 4 — je Symbol wird ``(asset_class_key, spread_bps, source)`` als
    ``COST_MODEL_RESOLVED``-Event gestempelt (``run_single_backtest_worker``). Diese Invariante
    prüft die gesammelten Events eines Laufs: kein Symbol darf über ``asset_class_key == 'UNKNOWN'``
    ODER ``source == 'default'`` (der explizit opt-in fail-open Zweig, siehe
    ``backtest.json['unknown_asset_class_policy']``) aufgelöst haben, solange der Anteil
    ``max_default_fallback_fraction`` (Default 0.0 — KEIN Symbol darf über DEFAULT auflösen)
    überschritten wird. Ein Lauf, in dem >0% der Symbole über DEFAULT liefen, hat die #898-Root-
    Cause (47% des Universums über den Fail-Open-Pfad) NICHT behoben."""
    if not cost_model_events:
        return InvariantResult(
            name="check_cost_model_resolution",
            passed=True,
            expected=f"Anteil DEFAULT-aufgelöster Symbole <= {max_default_fallback_fraction}",
            actual=None,
            detail="Keine COST_MODEL_RESOLVED-Events — nicht auswertbar.",
            severity="high",
        )
    n = len(cost_model_events)
    offenders = [
        e.get("symbol") for e in cost_model_events
        if e.get("asset_class_key") == "UNKNOWN" or e.get("source") == "default"
    ]
    fraction = round(len(offenders) / n, 4) if n else 0.0
    passed = fraction <= max_default_fallback_fraction
    return InvariantResult(
        name="check_cost_model_resolution",
        passed=passed,
        expected=f"Anteil DEFAULT-aufgelöster Symbole <= {max_default_fallback_fraction}",
        actual=fraction,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)}/{n} Symbole ({fraction:.1%}) lösten über den fail-open DEFAULT-"
                f"Pfad auf statt über eine EQUITY/CRYPTO/FOREX/COMMODITY-Asset-Class: {offenders} "
                "— dieselbe Kostenüberschätzung wie im #898-Symptom (Issue #287 in AGENTS.md: ein "
                "Enum-Wert, der in der Datenquelle steht, aber nicht in der Nachschlagetabelle, darf "
                "nie still auf DEFAULT fallen)."),
    )


def check_cost_model_floor(cost_model_events: list[dict], *,
                           tolerance_bps: float = 1e-6) -> InvariantResult:
    """Issue #956 (Katalog D, Pitfall #301) — kein Symbol darf mit einem Spread simuliert werden,
    der UNTER der physikalischen Tick-Untergrenze liegt (``backtest_runner.tick_floor_spread_bps``:
    ``1e4 * tick_size / median_price``). Eine Kostenkonstante je Asset-Klasse (z. B. EQUITY=3.0bps)
    unterschätzt den Round-Trip bei einem Micro-Cap um bis zu Faktor ~17 — genau dort, wo der
    Backtest die höchsten Roh-Renditen meldet (Pitfall #301). Diese Invariante bestätigt, dass
    ``resolve_spread_bps``s ``max(config_wert, tick_floor_bps)``-Absicherung tatsächlich griff, für
    jedes ``COST_MODEL_RESOLVED``-Event (dasselbe Schema wie ``check_cost_model_resolution``, jetzt
    mit dem seit #956 mitgeführten ``tick_floor_bps``-Feld).

    Events ohne ``tick_floor_bps``-Feld (ältere Läufe VOR #956, oder ein Symbol, für das der Floor
    fail-open nicht berechenbar war — kein instrument_map-Eintrag/keine Preis-Stichprobe) sind NICHT
    auswertbar und zählen nicht als Verstoß (kein rückwirkender FAIL auf Alt-Telemetrie)."""
    evaluable = [e for e in (cost_model_events or []) if e.get("tick_floor_bps") is not None]
    if not evaluable:
        return InvariantResult(
            name="check_cost_model_floor",
            passed=True,
            expected="spread_bps >= tick_floor_bps für jedes Symbol",
            actual=None,
            detail="Keine COST_MODEL_RESOLVED-Events mit tick_floor_bps-Feld — nicht auswertbar.",
            severity="high",
        )
    offenders = [
        {"symbol": e.get("symbol"), "spread_bps": e.get("spread_bps"),
         "tick_floor_bps": e.get("tick_floor_bps")}
        for e in evaluable
        if float(e.get("spread_bps") or 0.0) < float(e.get("tick_floor_bps") or 0.0) - tolerance_bps
    ]
    passed = not offenders
    return InvariantResult(
        name="check_cost_model_floor",
        passed=passed,
        expected="spread_bps >= tick_floor_bps für jedes Symbol",
        actual=len(offenders),
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)}/{len(evaluable)} Symbole simulierten GÜNSTIGER als die "
                f"physikalische Tick-Untergrenze: {offenders} — resolve_spread_bps's max(...)-"
                "Absicherung hat nicht gegriffen (#956)."),
    )


def check_family_scope_coherence(study_family_records: list[dict], *,
                                 promotion_family_scope: str | None = None) -> InvariantResult:
    """Issue #904 Fix 4 — bei ``promotion_family_scope == 'per_strategy'`` müssen zwei Studies
    DESSELBEN Symbols mit VERSCHIEDENER Trialzahl auch VERSCHIEDENE ``deflation_n_family_raw``
    melden (N1 ist per Definition die Study-eigene Trialzahl unter diesem Scope, siehe
    ``sweep._family_n_stage1_from_studies``). Identische ``deflation_n_family_raw``-Werte über ALLE
    Studies eines Symbols — unabhängig von ihrer tatsächlichen Trialzahl — sind der Fingerabdruck
    des #904-Bugs (die entfernte ``max()``-Zeile in ``confirm.py`` hatte jede Study auf dieselbe,
    symbolweite Renditeserien-Zahl hochkorrigiert).

    ``study_family_records``: ``[{"symbol", "strategy", "n_trials", "deflation_n_family_raw"}, ...]``
    (typischerweise aus den Confirm-Proposals eines Laufs). Nur Symbole mit >= 2 Studies UND
    >= 2 DISTINKTEN Trialzahlen sind aussagekräftig (bei identischer Trialzahl wäre identisches N1
    kein Fehler, sondern Zufall) — alles andere ist nicht anwendbar."""
    if promotion_family_scope not in (None, "per_strategy"):
        return InvariantResult(
            name="check_family_scope_coherence",
            passed=True,
            expected="verschiedene deflation_n_family_raw je Study bei verschiedener Trialzahl",
            actual=None,
            detail=f"promotion_family_scope={promotion_family_scope!r} — nicht 'per_strategy', "
                   "nicht anwendbar.",
        )
    by_symbol: dict[str, list[dict]] = {}
    for r in study_family_records or []:
        sym = r.get("symbol")
        if sym is None or r.get("deflation_n_family_raw") is None or r.get("n_trials") is None:
            continue
        by_symbol.setdefault(sym, []).append(r)

    offenders: dict[str, dict] = {}
    for sym, records in by_symbol.items():
        if len(records) < 2:
            continue
        distinct_trial_counts = {r["n_trials"] for r in records}
        distinct_family_raw = {r["deflation_n_family_raw"] for r in records}
        if len(distinct_trial_counts) >= 2 and len(distinct_family_raw) == 1:
            offenders[sym] = {
                "n_trials": sorted(distinct_trial_counts),
                "deflation_n_family_raw": next(iter(distinct_family_raw)),
            }
    passed = not offenders
    return InvariantResult(
        name="check_family_scope_coherence",
        passed=passed,
        expected="verschiedene deflation_n_family_raw je Study bei verschiedener Trialzahl",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Symbol(e) melden IDENTISCHES deflation_n_family_raw über "
                f"Studies mit unterschiedlicher Trialzahl: {offenders} — Fingerabdruck des "
                "#904-Bugs (Pitfall #289: eine spätere max()-artige Korrektur stellt die "
                "symbolweite Multiplizität wieder her)."),
    )


def check_gate_collinearity_decision_required(gate_correlations: dict[tuple[str, str], float | None], *,
                                              threshold: float = 0.90,
                                              accepted_pairs: list[dict] | None = None,
                                              policy: str = "require_decision") -> InvariantResult:
    """Issue #907 (Pitfall #280) — der ``[#667]``-Kollinearitäts-Alarm (``reward.
    assert_gate_collinearity_guard``) feuerte bislang bei jedem Lauf, den ein Gate-Paar über
    ``threshold`` traf, OHNE jede Konsequenz: sechs Alarme derselben Klasse im ``ea4c409d``-Lauf,
    alle korrekt, alle folgenlos. Ein Diagnose-Alarm ohne Entscheidungspflicht erzeugt Gewöhnung.

    ``gate_collinearity_policy`` (``tournament.json``, Default ``'require_decision'``):
    - ``'warn'`` — Alt-Verhalten, PASSt immer (reine Telemetrie, bit-identisch zu Pre-#907).
    - ``'require_decision'``/``'block'`` — jedes Paar mit ``|ρ| > threshold`` MUSS in
      ``gate_collinearity_accepted_pairs`` stehen (mit den Pflichtfeldern ``rationale`` und
      ``decided_in_issue`` — eine dokumentierte, bewusste Entscheidung, keine stille Duldung).
      Ein geflaggtes Paar OHNE Eintrag dort FAILt blocking — der Sweep bricht (via
      ``fail_fast_invariants``) VOR Phase 1 ab, statt nach 24h Rechenzeit erneut sechsmal
      denselben unbeantworteten Alarm zu protokollieren.

    ``accepted_pairs``: ``[{"pair": [k1, k2], "rationale": str, "decided_in_issue": int}, ...]``."""
    if policy == "warn":
        return InvariantResult(
            name="check_gate_collinearity_decision_required",
            passed=True,
            expected="jedes Paar mit |ρ| > threshold ist in gate_collinearity_accepted_pairs "
                     "dokumentiert",
            actual=None,
            detail="gate_collinearity_policy='warn' — reine Telemetrie, keine Entscheidungspflicht.",
        )
    accepted_key_pairs = {
        frozenset(a["pair"]) for a in (accepted_pairs or [])
        if isinstance(a, dict) and a.get("pair") and a.get("rationale") and a.get("decided_in_issue")
    }
    offenders = []
    for (k1, k2), rho in (gate_correlations or {}).items():
        if rho is None or abs(rho) <= threshold:
            continue
        if frozenset((k1, k2)) not in accepted_key_pairs:
            offenders.append({"pair": [k1, k2], "rho": round(rho, 4)})
    passed = not offenders
    return InvariantResult(
        name="check_gate_collinearity_decision_required",
        passed=passed,
        expected="jedes Paar mit |ρ| > threshold ist in gate_collinearity_accepted_pairs "
                 "dokumentiert",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} kollineare(s) Gate-Paar(e) ohne dokumentierte Entscheidung "
                f"({policy!r}): {offenders} — jedes Paar braucht einen Eintrag in "
                "gate_collinearity_accepted_pairs mit rationale + decided_in_issue, sonst bricht "
                "der Lauf (Issue #907)."),
    )


def check_fail_fast_invariants_wired(invariant_check_names: list[str], *,
                                     fail_fast_invariants: list[str] | None = None) -> InvariantResult:
    """Issue #907 Fix 3 — symmetrisch zum Gate-Kollinearitäts-Fix: eine in
    ``optimizer.json['fail_fast_invariants']`` gelistete Invariante, die in einem vollständigen
    Lauf KEIN EINZIGES Ergebnis (PASS oder FAIL) meldet, ist nicht verdrahtet — ein Name in der
    Config-Liste ohne einen tatsächlich ausgewerteten Check dahinter ist dieselbe Gewöhnungsfalle
    wie ein Alarm ohne Entscheidungspflicht (Pitfall #280), nur eine Ebene tiefer: der Wächter
    existiert nicht einmal als Beobachtung.

    ``invariant_check_names``: die ``name``-Werte ALLER tatsächlich ausgewerteten
    ``InvariantResult``s eines Laufs (``report.py``s ``invariant_checks``, unabhängig vom
    Pass/Fail-Ergebnis)."""
    configured = set(fail_fast_invariants or [])
    if not configured:
        return InvariantResult(
            name="check_fail_fast_invariants_wired",
            passed=True,
            expected="jede in fail_fast_invariants gelistete Invariante wurde mindestens einmal "
                     "ausgewertet",
            actual=None,
            detail="fail_fast_invariants leer/fehlt — nicht anwendbar.",
        )
    evaluated = set(invariant_check_names or [])
    missing = sorted(configured - evaluated)
    passed = not missing
    return InvariantResult(
        name="check_fail_fast_invariants_wired",
        passed=passed,
        expected="jede in fail_fast_invariants gelistete Invariante wurde mindestens einmal "
                 "ausgewertet",
        actual=missing if missing else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(missing)} in fail_fast_invariants gelistete Invariante(n) ohne jedes "
                f"Ergebnis in diesem Lauf: {missing} — der Name existiert in der Config, aber kein "
                "Code-Pfad wertet ihn tatsächlich aus (Issue #907)."),
    )


def check_required_config_keys(configs: dict[str, dict], required_keys_spec: dict) -> InvariantResult:
    """Issue #844 (Pitfall #267, 5. Wiederkehr nach #488/#753/#769/#805) — FAIL, wenn ein in
    ``required_keys_spec`` (``automation/config/_required_keys.json``) als ``required`` markierter
    Key in seiner Config-Datei fehlt, ``null`` ist, oder einen ``reject_values``-Eintrag trägt.

    ``configs``: ``{"tournament.json": {...bereits geladen...}, "optimizer.json": {...}}``.
    ``required_keys_spec``: ``{"tournament.json": {key: {"required": bool, "reject_values": [...]}},
    ...}`` — Metadaten-Keys wie ``schema_version``/``_comment`` werden ignoriert.

    Reine Funktion über bereits geladene Dicts — kein Datei-I/O hier (der Aufrufer, z. B.
    ``sweep.assert_required_config_keys_valid``, lädt beide Dateien)."""
    offenders: dict[str, str] = {}
    for filename, fields in (required_keys_spec or {}).items():
        if not isinstance(fields, dict):
            continue  # schema_version/_comment — keine Feld-Spezifikation
        cfg = configs.get(filename) or {}
        for key, spec in fields.items():
            if not isinstance(spec, dict) or not spec.get("required", False):
                continue
            offender_key = f"{filename}::{key}"
            if key not in cfg:
                offenders[offender_key] = "fehlt"
                continue
            value = cfg.get(key)
            if value is None:
                offenders[offender_key] = "ist null"
                continue
            reject_values = spec.get("reject_values") or []
            if value in reject_values:
                offenders[offender_key] = f"verbotener Wert {value!r}"
    passed = not offenders
    return InvariantResult(
        name="check_required_config_keys",
        passed=passed,
        expected="alle in _required_keys.json als required markierten Keys gesetzt, nicht null, "
                 "kein reject_values-Treffer",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Config-Key(s) verletzen die #844-Registry: {offenders}"),
    )


def check_selection_rule_homogeneity(selection_rule_families: dict[str, dict[str, int]]) -> InvariantResult:
    """Issue #848 (5. Katalog: #660 → #668 → #678 → #812 → #848) — FAIL (statt der bisherigen
    ``[#812]``-WARNUNG in ``sweep.py``), wenn mehr als EIN ``selection_rule_fingerprint`` je
    Symbol/Familie auftritt. Vor #848 war die Ursache bekannt (der unerreichbare
    ``min_win_rate``-OR-Arm liess ``any_arm_unreachable_policy='drop_arm'`` je Study
    unterschiedlich greifen); nach der Entfernung dieses Arms ist ein zweiter Fingerprint eine
    ANDERE, bislang unbekannte Ursache — und verletzt die Voraussetzung der DSR-Multiplizitäts-
    korrektur (Pitfall #248), die eine über die Familie konstante Selektionsregel voraussetzt.

    ``selection_rule_families``: ``{symbol: {fingerprint: n_family}}``
    (``report._selection_rule_families``)."""
    offenders = {
        symbol: len(fingerprints)
        for symbol, fingerprints in (selection_rule_families or {}).items()
        if len(fingerprints) > 1
    }
    passed = not offenders
    return InvariantResult(
        name="check_selection_rule_homogeneity",
        passed=passed,
        expected="genau 1 selection_rule_fingerprint je Symbol/Familie",
        actual=offenders if offenders else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Symbol(e) mit >1 selection_rule_fingerprint: {offenders} — "
                "verletzt die DSR-Multiplizitätskorrektur-Voraussetzung (Pitfall #248)."),
    )


def check_symbol_coverage(coverage: dict, universe: list[str], *, max_age_runs: int = 3) -> InvariantResult:
    """Issue #841/#892 — FAIL, wenn ein Symbol des aktuellen Universums seit mehr als
    ``max_age_runs`` abgeschlossenen Sweep-Läufen nicht ERNEUT abgedeckt wurde (``stale``), ODER
    ein Symbol AUSSERHALB der Bootstrap-Phase noch NIE abgedeckt wurde (``never_covered``).
    Konsumiert ``symbol_coverage.coverage_report`` (dasselbe Ledger, das
    ``sweep_symbol_order_policy='least_recently_covered'`` für die Dispatch-Reihenfolge nutzt) —
    macht eine Abdeckungslücke messbar, statt sie nur implizit über ``symbols_completed``/
    ``symbols_planned``-Telemetrie erahnen zu lassen.

    Issue #892 Fix Punkt 3/4 (Pitfall #287) — ``never_covered`` und ``stale_symbols`` sind ZWEI
    verschiedene Zustände (ein nie abgedecktes Symbol hat kein "Alter" im Sinne von ``max_age_runs``
    — der bisherige Code stempelte es fälschlich mit ``total_runs_started``, dem STALE-Text). Bei
    ``len(universe)`` Symbolen braucht die ERSTE Vollabdeckung mehrere Läufe (Bootstrap-Phase,
    ``coverage_report``s ``coverage_bootstrap_phase``) — währenddessen ist ``never_covered`` der
    ERWARTETE Zustand (Telemetrie, kein FAIL); ``stale_symbols`` bleibt IMMER ein FAIL (ein bereits
    abgedecktes Symbol, das die Rotation seither übersprungen hat, ist unabhängig von der
    Bootstrap-Phase ein echter Befund)."""
    from automation.optimizer import symbol_coverage as _sc
    report = _sc.coverage_report(coverage, universe, max_age_runs=max_age_runs)
    stale = report.get("stale_symbols") or {}
    never = report.get("never_covered") or []
    bootstrap = bool(report.get("coverage_bootstrap_phase"))
    never_offenders = [] if bootstrap else list(never)
    offenders: dict[str, Any] = dict(stale)
    for sym in never_offenders:
        offenders[sym] = "never_covered"
    passed = not offenders
    detail_parts: list[str] = []
    if stale:
        detail_parts.append(
            f"{len(stale)} Symbol(e) seit mehr als {max_age_runs} Läufen nicht ERNEUT abgedeckt "
            f"(stale, least_recently_covered-Rotation sollte das verhindern): {stale}")
    if never_offenders:
        detail_parts.append(
            f"{len(never_offenders)} Symbol(e) noch NIE abgedeckt, ausserhalb der Bootstrap-Phase "
            f"({report.get('total_runs_started', 0)} Läufe): {sorted(never_offenders)}")
    if bootstrap and never:
        detail_parts.append(
            f"{len(never)} Symbol(e) noch nie abgedeckt, aber INNERHALB der Bootstrap-Phase "
            "(Erstabdeckung des Universums läuft noch) — Telemetrie, kein FAIL.")
    return InvariantResult(
        name="check_symbol_coverage",
        passed=passed,
        expected=(f"<= {max_age_runs} Läufe seit letzter Abdeckung (stale) und kein Symbol "
                  "ausserhalb der Bootstrap-Phase ohne jede Abdeckung (never_covered)"),
        actual=offenders if offenders else None,
        severity="high",
        detail=" | ".join(detail_parts) if detail_parts else "OK",
    )


def check_coverage_ledger_continuity(total_runs_started: int, has_prior_reports: bool) -> InvariantResult:
    """Issue #892 Fix Punkt 2 — FAIL (blocking), wenn ``total_runs_started == 1`` UND bereits
    MINDESTENS ein früherer Lauf-Report existiert (``data/optimizer/reports/run_*.json``): das
    Coverage-Ledger (``symbol_coverage.json``) wurde zwischen dem Vorlauf und diesem Lauf
    zurückgesetzt/verloren — ein Datenverlust (achte Wiederkehr von Pitfall #237: #794, #796,
    #797, #818, #831, #840, #856, hier), kein Normalzustand für einen Sweep, der nachweislich
    nicht der allererste ist. Reine Funktion — der Aufrufer (``report._build_report``) ermittelt
    ``has_prior_reports`` aus dem Report-Verzeichnis."""
    offending = bool(has_prior_reports) and int(total_runs_started) == 1
    return InvariantResult(
        name="check_coverage_ledger_continuity",
        passed=not offending,
        expected="total_runs_started > 1, sobald mindestens ein früherer Lauf-Report existiert",
        actual=total_runs_started,
        severity="blocking",
        detail=("OK" if not offending else
                f"total_runs_started={total_runs_started}, obwohl bereits frühere Lauf-Reports "
                "existieren — das Coverage-Ledger wurde zurückgesetzt/verloren (Pitfall #237, "
                "achte Wiederkehr)."),
    )


def _parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """Wie ``sweep._parse_version_tuple`` — absichtlich DUPLIZIERT statt importiert, damit
    ``invariants.py`` frei von jeder Abhängigkeit auf ein anderes ``automation.optimizer``-Modul
    bleibt (Moduldocstring: reine Funktionen über plain Dicts/Listen). Parst die führenden
    numerischen Komponenten einer Versions-Zeichenkette (``'2.3.3'`` -> ``(2, 3, 3)``); ein
    nicht-numerischer Suffix (rc/dev/post) bricht das Parsing an dieser Stelle ab."""
    parts: list[int] = []
    for chunk in (version_str or "").split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


_VERSION_COMPARATORS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def _version_satisfies(installed: str, spec: str) -> bool:
    """Minimaler, abhängigkeitsfreier Spezifizierer-Vergleich (nur ``>=``/``<=``/``==``/``>``/``<``,
    kommagetrennt UND-verknüpft) — ausreichend für die in
    ``optimizer.json['pinned_library_versions']`` verwendeten Bereichs-Pins (z. B. ``'>=4.9,<5.0'``,
    dasselbe Format wie ``requirements.txt``). KEIN vollständiger PEP-440-Parser (kein Umgang mit
    Pre-Release-/Post-Release-Suffixen) — für die hier gepinnten, regulär veröffentlichten
    Major.Minor.Patch-Versionen ausreichend."""
    installed_t = _parse_version_tuple(installed)
    for clause in (spec or "").split(","):
        clause = clause.strip()
        if not clause:
            continue
        op = next((o for o in (">=", "<=", "==", ">", "<") if clause.startswith(o)), None)
        if op is None:
            continue
        bound_t = _parse_version_tuple(clause[len(op):].strip())
        if not _VERSION_COMPARATORS[op](installed_t, bound_t):
            return False
    return True


def check_library_version_drift(installed_versions: dict[str, str | None],
                                 pinned_ranges: dict[str, str]) -> InvariantResult:
    """Issue #852 (P2) — FAIL, wenn eine installierte Bibliotheksversion
    (``manifest.library_versions()``) ausserhalb ihres gepinnten Bereichs
    (``optimizer.json['pinned_library_versions']``, z. B. ``'>=4.9,<5.0'``) liegt.

    Root-Cause: ``optuna`` stand in ``requirements.txt`` OHNE jede Versionsangabe, obwohl derselbe
    Kommentar dort erklärt, warum das für ``pandas`` (#802) inakzeptabel war — ``TPESampler``s
    Defaults für ``multivariate``/``group``/``constant_liar``, die Interaktion von
    ``constraints_func`` (#612) mit ``n_startup_trials``, und die Behandlung von
    ``TrialState.PRUNED`` sind alle NICHT über Optuna-Majors garantiert. Damit hängt der
    NUMERISCHE AUSGANG der Selektion an der Installationsumgebung statt der Konfiguration —
    dieselbe Fehlerklasse wie #801/#802 bei pandas.

    Nur Bibliotheken, die SOWOHL installiert (``installed_versions[name] is not None``) ALS AUCH
    gepinnt (``pinned_ranges`` enthält einen Eintrag) sind, werden geprüft — eine fehlende
    Installation oder ein (noch) ungepinnter Eintrag ist kein Drift-FAIL (fail-open, analog jedem
    anderen Preflight-Check dieses Moduls)."""
    offenders: dict[str, str] = {}
    for name, spec in (pinned_ranges or {}).items():
        installed = (installed_versions or {}).get(name)
        if installed is None:
            continue
        if not _version_satisfies(installed, spec):
            offenders[name] = f"installiert={installed}, gepinnt={spec}"
    passed = not offenders
    return InvariantResult(
        name="check_library_version_drift",
        passed=passed,
        expected=dict(pinned_ranges or {}),
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Bibliothek(en) ausserhalb des gepinnten Bereichs: {offenders} — "
                "der numerische Ausgang der Selektion haengt damit an der Installationsumgebung "
                "statt der Konfiguration (#802-Fehlerklasse)."),
    )


def check_champion_seed_coverage(seed_source_counts: dict[str, int], *,
                                 threshold: float = 0.9) -> InvariantResult:
    """Issue #853 Fix Punkt 4 — WARNUNG (severity='low'), wenn ``seed_source == 'strategy_defaults'``
    für mehr als ``threshold`` (Default 90 %) der Studies EINES Laufs gilt: der Champion-Store-
    Closed-Loop (Epic #702, Ebene 1+2) ist dann nachweislich unwirksam — unabhängig davon, WELCHES
    Glied bricht (#834-Store-Entwertung durch einen Semantik-Bump, fehlendes #840/#841-Resume/
    Ledger-Vollabdeckung, oder die #821-``corroboration_count >= 2``-Hürde können je einzeln oder
    gemeinsam ursächlich sein — dieser Check macht nur das SYMPTOM sichtbar, nicht die Ursache;
    ``seed_source_counts`` selbst unterscheidet die möglichen Werte für die Detailanalyse).

    SCOPE-HINWEIS: der Issue-Text verlangt die Schwelle über ZWEI AUFEINANDERFOLGENDE Läufe
    (persistente Historie nötig, analog ``symbol_coverage.py``). Diese Prüfung ist bewusst auf
    EINEN Lauf reduziert (siehe ``champions.py``-Moduldocstring für die vollständige
    Scope-Begründung) — ein Lauf mit ≥ 90 % ``strategy_defaults`` ist bereits für sich genommen ein
    aussagekräftiges Signal; die Zwei-Lauf-Persistenz bleibt als dokumentierte Erweiterung offen.

    ``seed_source_counts``: ``{seed_source_value: n_studies}``
    (``report._seed_source_distribution``)."""
    total = sum(seed_source_counts.values())
    if total == 0:
        return InvariantResult(
            name="check_champion_seed_coverage",
            passed=True,
            expected=f"strategy_defaults-Anteil <= {threshold:.0%}",
            actual=None,
            severity="low",
            detail="Keine Studies mit seed_source-Telemetrie — nicht anwendbar.",
        )
    defaults_fraction = seed_source_counts.get("strategy_defaults", 0) / total
    passed = defaults_fraction <= threshold
    return InvariantResult(
        name="check_champion_seed_coverage",
        passed=passed,
        expected=f"<= {threshold:.0%}",
        actual=defaults_fraction,
        severity="low",
        detail=("OK" if passed else
                f"strategy_defaults-Anteil={defaults_fraction:.1%} > {threshold:.0%} — der "
                f"Champion-Store-Closed-Loop (#702) ist fuer diesen Lauf nachweislich unwirksam "
                f"({seed_source_counts})."),
    )


def check_semantics_version_coherence(admissible_despite_simulation_stale: int) -> InvariantResult:
    """Issue #854 Fix Punkt 6 — FAIL, wenn ein Champion-Store-Eintrag mit einer VERALTETEN
    ``simulation_semantics_version`` (siehe ``optimizer.json``-Schema für die vollständige
    reward/simulation/params_schema-Abgrenzung) trotzdem als ``champion_is_admissible`` gilt und
    damit als Seed/in einer Multiplizitäts-Zählung verwendet werden könnte.

    ``champions.champion_is_admissible`` schliesst einen ``simulation_semantics_version``-Mismatch
    seit #854 HART aus (anders als ein reiner ``reward_semantics_version``-Mismatch, der nur die
    Quality-Bewertung entwertet, #819) — dieser Wächter verifiziert, dass diese Garantie
    TATSÄCHLICH hält, statt sie nur zu behaupten (dieselbe Klasse wie
    ``check_champion_writeback_reachability``: ein struktureller Soll-Zustand wird gegen den
    IST-Zustand des Stores geprüft, nicht nur im Code-Pfad angenommen).

    ``admissible_despite_simulation_stale``: ``report._champions_summary``-Zähler (0 im
    Regelfall). Die parallele SQLite-Study-Ebene (ein simulation-stales Trial, das noch in
    ``n_family`` einfliesst) ist strukturell bereits durch
    ``run_optimization._check_simulation_semantics_version`` verhindert — jede geladene Study mit
    veralteter Version wird beim Laden fail-loud gepurgt (dieselbe Mechanik wie
    ``REJECT_STALE_STUDY_SEMANTICS``), BEVOR ihre Trials in irgendeine Multiplizitäts-Zählung
    einfliessen könnten."""
    passed = admissible_despite_simulation_stale <= 0
    return InvariantResult(
        name="check_semantics_version_coherence",
        passed=passed,
        expected=0,
        actual=admissible_despite_simulation_stale,
        severity="blocking",
        detail=("OK" if passed else
                f"{admissible_despite_simulation_stale} Champion-Store-Eintrag/Eintraege mit "
                "veralteter simulation_semantics_version gelten trotzdem als admissible — der "
                "#854-Hartausschluss (champions.champion_is_admissible) hat nicht gegriffen."),
    )


def check_deployment_gate_completeness(whitelisted_winners: dict[str, dict]) -> InvariantResult:
    """Issue #993 Fix Punkt 4 (P0-blocking) — BLOCKIERENDER Regressionswächter der Deployment-Grenze
    selbst: jeder Eintrag in ``data/state/whitelist_tournament.json``'s ``per_symbol_winners`` muss
    ein vollständiges ``deployment_gate.clause_results``-Dict tragen — alle acht Klauseln aus
    ``deployment_gate.DEPLOYMENT_CLAUSES``, keine davon ``None`` (fail-closed bedeutet hier: ein
    Eintrag, dessen Klausel NICHT auswertbar war, hätte gemäss ``evaluate_deployment_eligibility``
    gar nicht erst als ``admitted`` in die Whitelist gelangen dürfen — ein ``None`` an dieser Stelle
    ist ein Bug in der Verdrahtung, keine harmlose Lücke).

    Bei 0 Promotionen (der Ausgangszustand vor #994) ist die Whitelist leer ⇒ 0 Einträge zu prüfen
    ⇒ dieser Check ist dann trivial erfüllt — das ist der korrekte Startzustand, nicht ein
    aussagekräftiges PASS über eine geprüfte Kohorte (Pitfall #330: ein Wächter, der (noch) nie
    feuert, ist deshalb nicht falsch)."""
    from automation.optimizer.deployment_gate import DEPLOYMENT_CLAUSES

    offenders: dict[str, list[str]] = {}
    for symbol, winner in whitelisted_winners.items():
        gate = winner.get("deployment_gate")
        if not isinstance(gate, dict):
            offenders[symbol] = ["deployment_gate fehlt am Whitelist-Eintrag"]
            continue
        clause_results = gate.get("clause_results") or {}
        missing = [c for c in DEPLOYMENT_CLAUSES if clause_results.get(c) is not True]
        if missing:
            offenders[symbol] = missing
    passed = not offenders
    return InvariantResult(
        name="check_deployment_gate_completeness",
        passed=passed,
        expected=f"alle {len(DEPLOYMENT_CLAUSES)} Klauseln ({', '.join(DEPLOYMENT_CLAUSES)}) == True je Whitelist-Eintrag",
        actual=offenders if offenders else None,
        detail=("OK" if passed else
                f"{len(offenders)} Whitelist-Eintrag/Einträge mit unvollständiger/fehlender "
                f"Deployment-Grenzen-Prüfung (Issue #993): {offenders} — Exit-Code 1 vor dem "
                "Bot-Start (P0-blocking, kein Kandidat ohne vollständige Prüfung geht live)."),
    )


def check_live_exposure_budget(exposure_snapshots: list[dict], *,
                               max_total_exposure_fraction: float = 0.60,
                               tolerance: float = 1e-9) -> InvariantResult:
    """Issue #999 Fix Punkt 4 — die Budget-Erhaltungsbedingung der Live-Allokation
    (``automation.momentum_ls_allocator.MomentumLSAllocator``) als Telemetrie-Nachweis: in JEDEM
    aufgezeichneten Live-Snapshot (``{"open_exposure_fraction": Σ w_i, ...}``) darf die Summe der
    offenen Positions-Gewichte ``max_total_exposure_fraction`` nie überschreiten (Toleranz ``1e-9``
    gegen Float-Rundung — dieselbe Konvention wie ``live_risk.evaluate_circuit_breaker``'s
    Drawdown-Schwellenvergleich). Eine Verletzung ist ein Bug in der Budget-Formel selbst
    (``get_allocation``/``update_risk_state``), keine Dateneigenart — die Erhaltungsbedingung ist
    eine mathematische Invariante der Formel, nicht ein empirischer Schwellenwert."""
    with_data = [s for s in exposure_snapshots if s.get("open_exposure_fraction") is not None]
    if not with_data:
        return InvariantResult(
            name="check_live_exposure_budget",
            passed=True,
            expected=f"Σ w_i <= {max_total_exposure_fraction} in jedem Live-Snapshot",
            actual=None,
            detail="Keine Live-Exposure-Snapshots vorhanden (Bot noch nicht gestartet oder keine "
                   "Telemetrie) — nicht anwendbar.",
        )
    offenders = [
        s for s in with_data
        if s["open_exposure_fraction"] > max_total_exposure_fraction + tolerance
    ]
    passed = not offenders
    return InvariantResult(
        name="check_live_exposure_budget",
        passed=passed,
        expected=f"Σ w_i <= {max_total_exposure_fraction} in jedem Live-Snapshot",
        actual=offenders if offenders else None,
        detail=("OK" if passed else
                f"{len(offenders)} Snapshot(s) überschreiten das Gesamt-Expositions-Budget "
                f"({max_total_exposure_fraction}): {offenders} — Bug in der #999-Budget-Formel, "
                "keine Dateneigenart."),
    )
