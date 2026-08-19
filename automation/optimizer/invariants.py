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
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from automation.optimizer._contracts import MAX_BARS_IN_TRADE_HARD_CAP as _MAX_BARS_IN_TRADE_CAP
from automation.optimizer._contracts import BAR_SECONDS_DEFAULT as _BAR_SECONDS_DEFAULT

_log = logging.getLogger("optimizer")


@dataclass(frozen=True)
class InvariantResult:
    name: str
    # Issue #995/#1147 — ``bool | None``: ``None`` ist reserviert fuer ``evaluable=False`` (siehe
    # dortiger Feld-Docstring) — "kein Urteil moeglich", NICHT "kein Fehler gefunden" (``True``) und
    # NICHT "Fehler gefunden" (``False``). Jeder bestehende Aufrufer setzt weiterhin ``True``/
    # ``False`` (rueckwaertskompatibel); nur die drei in #995/#1147 genannten Checks nutzen ``None``.
    passed: bool | None
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
    # Issue #941/#1107 (Katalog #960) — welche POPULATION diese Auswertung tatsaechlich gesehen hat:
    # ``{"run_id": …, "n_studies": …, "symbols": [...], "source": "in_process" | "report_scan"}``.
    # Root-Cause #1107: derselbe Check-Name (``check_effective_stop_distance``) meldete im selben
    # Lauf zwei verschiedene Offender-Zahlen (12/13/13 vs. 25/38/38), weil der Fail-Fast-Pfad gegen
    # die in-process gehaltenen Study-Records auswertet (per Konstruktion nur die eigenen), der
    # Report-Pfad dagegen gegen die aus ``WORK`` eingesammelten ``proposal_*.json`` — zwei
    # Grundgesamtheiten unter einem Namen, ohne dass das Artefakt die Kohorte deklarierte. Optional
    # auf Funktionsebene (``None`` fuer bestehende Aufrufer/Tests, rueckwaertskompatibel); das
    # PFLICHTFELD-Kriterium wird stattdessen am Aggregationspunkt durchgesetzt
    # (``report._build_report`` stempelt jeden Eintrag von ``invariant_checks``, der noch kein
    # ``cohort`` traegt, siehe dort) — jeder Eintrag im ARTEFAKT traegt ``cohort``, unabhaengig
    # davon, ob die einzelne Check-Funktion es selbst gesetzt hat.
    cohort: dict[str, Any] | None = None
    # Issue #973/#1127 (Pitfall #404 in AGENTS.md) — strukturierte Evaluierbarkeits-Auskunft fuer
    # ``severity='blocking'``-Checks: ``{"evaluable": bool, "inconclusive_reason": str | None,
    # "n_studies_measured": int}``. ``inconclusive=True`` (oben) markiert den Fall bereits als
    # "kein sauberes PASS"; ``evaluability`` macht ihn zusaetzlich MASCHINENLESBAR strukturiert
    # auswertbar (statt nur ueber den freien ``detail``-Text), damit ein Report-/Summary-Konsument
    # "PASS" von "INCONCLUSIVE" unterscheiden kann, OHNE den Detail-String zu parsen. Optional
    # (``None`` fuer bestehende Aufrufer, rueckwaertskompatibel); von JEDEM ``severity='blocking'``-
    # Check gesetzt, der ``inconclusive=True`` liefert.
    evaluability: dict[str, Any] | None = None
    # Issue #995/#1147 (Katalog #1170, Pitfall #413 in AGENTS.md) — "nicht auswertbar" ist KEIN
    # PASS. Vor diesem Fix trugen ``check_effective_stop_distance``,
    # ``check_trailing_stop_risk_calibration_acceptance`` und ``check_stop_loss_vs_bar_range`` bei
    # leerer Grundgesamtheit ``passed=True`` — MASCHINENLESBAR ununterscheidbar von einem echten,
    # sauberen PASS ausser ueber den freien ``detail``-Text (dieselbe Problemklasse wie Pitfall
    # #404, hier aber ohne den nachgelagerten Konsequenz-Fix: ``_compute_decision_admissible``/
    # ``confirm.py``s ``REJECT_STUDY_INVARIANT_BLOCKING`` lasen weiterhin nur ``passed``). Ein
    # ``severity='blocking'``-Check, der seine EIGENE Grundgesamtheit nicht herstellen kann, ist
    # ein Befund, kein Nicht-Ereignis. ``evaluable`` (Default ``True``, rueckwaertskompatibel) ist
    # das TOP-LEVEL-Analogon zu ``evaluability.evaluable`` — waehrend ``inconclusive=True``
    # weiterhin ``passed=True`` (kein Sweep-Abbruch, siehe Pitfall #404) fuer die uebergrosse
    # Mehrheit der bestehenden ``inconclusive``-Checks bedeutet, signalisiert ``evaluable=False``
    # zusammen mit ``passed=None`` GEZIELT fuer die drei genannten ``blocking``-Checks: dieser
    # Report-Konsument (``_compute_decision_admissible``, ``confirm.py``) darf das Ergebnis NICHT
    # als "OK" werten. Der LIVE-Fail-Fast-Abbruch (``sweep._first_failing_fail_fast_invariant``)
    # bleibt bewusst UNVERAENDERT (verlangt weiterhin ``passed is False`` explizit) — ein Abbruch
    # MITTEN im Sweep allein wegen fehlender Evidenz waere der in Pitfall #404 explizit
    # zurueckgewiesene zweite Fehler; NUR die POST-HOC-Zulaessigkeitsbewertung (decision_admissible,
    # Study-Reject, Report-Anzeige) behandelt ``evaluable=False`` als Befund.
    evaluable: bool = True
    # Issue #985/#1139 (Katalog #986, Pitfall #411 in AGENTS.md) — Preflight-Checks
    # (``sweep.assert_required_config_keys_valid``/``assert_instrument_metadata_coherence``) liefen
    # VOR dem eigentlichen Sweep und meldeten Verstoesse nur ueber stderr + Exit-Code 2 — bestand der
    # Preflight, existierte im Report-Artefakt eines erfolgreichen Laufs KEIN Nachweis, dass er
    # ueberhaupt ausgefuehrt wurde. ``phase="preflight"`` markiert Eintraege, die vor dem
    # eigentlichen Study-Loop liefen (optional, ``None``/unset fuer alle bestehenden Checks —
    # rueckwaertskompatibel; ``report._build_report`` stempelt sie in ``invariant_checks`` ein).
    phase: str | None = None

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
        if self.cohort is not None:
            d["cohort"] = self.cohort
        if self.evaluability is not None:
            d["evaluability"] = self.evaluability
        if not self.evaluable:
            d["evaluable"] = False
        if self.phase is not None:
            d["phase"] = self.phase
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
    trial_attrs: list[dict], *, alpha: float = 0.01, min_trades: int = 20,
) -> InvariantResult:
    """Issue #965 Fix Punkt 4 (Katalog A, P0 HEADLINE) — schärft
    ``check_selection_statistic_availability`` um einen VERTEILUNGS-Test: eine reine
    Anteilsschwelle (80 % verfügbar) fängt NICHT die Klasse „Statistik fehlt gerade dort, wo sie
    zählen würde" (Pitfall #306) — im Referenzlauf 46cf5070 waren 77 % der Kohorte MIT PSR
    verfügbar, aber die FEHLENDE Kohorte (23 %) war die profitablere (92,2 % positive Rendite vs.
    7,4 % in der verfügbaren Kohorte).

    Issue #954/#1120 (Katalog #960) — Root-Cause der ALTEN Fassung (B-12): sie verglich
    ``oos_total_return`` (ein KUMULATIVER Return, OHNE Konditionierung auf Trade-Zahl/Exposure) —
    ein Return ohne diese Konditionierung belohnt NICHT-Handeln monoton (dieselbe Fehlerklasse wie
    #1077, hier in einer ``high``-Invariante statt einer Report-Spalte; Wiederkehr der #1052-Klasse:
    eine Meldung nennt eine Ursache, die die Messung nicht trennt). Der tatsächliche Diskriminator
    zwischen den beiden Kohorten war die TRADE-ZAHL (Median 2 gegen 130, 100 % der Kohorte ohne
    Statistik unter 20 Trades), NICHT Profitabilität (beide Mediane negativ — "profitabel" traf auf
    keine der beiden Kohorten zu).

    Fix: (1) der Vergleich läuft jetzt auf ``oos_expectancy`` (Expectancy JE TRADE, nicht
    kumulativer Return — unempfindlich gegen die Trade-Zahl selbst); (2) zusätzlich konditioniert
    auf ``oos_total_trades >= min_trades`` (Default 20, aus B-12 abgeleitet) — Trials darunter
    tragen keine belastbare Expectancy-Schätzung und werden VOR dem Test ausgeschlossen, ihre Zahl
    wird separat als ``n_below_min_trades`` ausgewiesen (dritte Kategorie „zu wenige Trades für ein
    Urteil", statt sie stillschweigend in eine der beiden Kohorten zu mischen); (3) der Meldungstext
    nennt ausschliesslich das Gemessene (Median-Expectancy UND Median-Trade-Zahl je Kohorte) — keine
    Kausalaussage ohne Trennschärfe-Nachweis.

    Testet je Study (falls ``oos_evaluated`` Trials mit UND ohne ``oos_selection_statistic_
    available`` UND ``oos_total_trades >= min_trades`` vorliegen): ist die Median-Expectancy-je-
    Trade der Kohorte OHNE Statistik einseitig signifikant GRÖSSER als die der Kohorte MIT
    Statistik (Mann-Whitney, ``alpha``)? Wenn ja: FAIL — die fehlende Statistik ist nicht MCAR
    (missing completely at random), sondern korreliert mit der ökonomischen Qualität.

    ``trial_attrs``: Liste von ``user_attrs``-artigen Dicts EINER Study (nicht des ganzen Laufs —
    der Aufrufer iteriert je Study, analog ``check_reward_term_variance``)."""
    def _n_trades(t: dict) -> int:
        return int(t.get("oos_total_trades") or 0)

    n_below_min_trades = sum(
        1 for t in trial_attrs
        if t.get("oos_evaluated") is True and _n_trades(t) < min_trades)
    missing = [
        (float(t["oos_expectancy"]), _n_trades(t)) for t in trial_attrs
        if t.get("oos_evaluated") is True and t.get("oos_selection_statistic_available") is False
        and t.get("oos_expectancy") is not None and _n_trades(t) >= min_trades
    ]
    available = [
        (float(t["oos_expectancy"]), _n_trades(t)) for t in trial_attrs
        if t.get("oos_evaluated") is True and t.get("oos_selection_statistic_available") is True
        and t.get("oos_expectancy") is not None and _n_trades(t) >= min_trades
    ]
    missing_expectancy = [e for e, _n in missing]
    available_expectancy = [e for e, _n in available]
    expected = (f"p >= {alpha} (Mann-Whitney auf Expectancy je Trade, H1: Kohorte-ohne-Statistik "
                f"> Kohorte-mit-Statistik, nur Trials mit oos_total_trades >= {min_trades})")
    z, p = _mann_whitney_u_one_sided(missing_expectancy, available_expectancy)
    if z is None:
        return InvariantResult(
            name="check_selection_statistic_economic_bias",
            passed=True,
            expected=expected,
            actual={"n_below_min_trades": n_below_min_trades} if n_below_min_trades else None,
            severity="high",
            detail=f"< 2 Trials mit oos_total_trades >= {min_trades} in einer der beiden Kohorten "
                   f"— kein belastbarer Test möglich ({n_below_min_trades} Trial(s) unterhalb der "
                   "Mindest-Trade-Zahl vorab ausgeschlossen).",
        )
    passed = p >= alpha
    median_n_missing = statistics.median([n for _e, n in missing])
    median_n_available = statistics.median([n for _e, n in available])
    return InvariantResult(
        name="check_selection_statistic_economic_bias",
        passed=passed,
        expected=expected,
        actual={"z": round(z, 4), "p_one_sided": p, "n_missing": len(missing),
                "n_available": len(available), "n_below_min_trades": n_below_min_trades,
                "median_expectancy_missing": round(statistics.median(missing_expectancy), 6),
                "median_expectancy_available": round(statistics.median(available_expectancy), 6),
                "median_n_trades_missing": median_n_missing,
                "median_n_trades_available": median_n_available},
        severity="high",
        detail=("OK" if passed else
                f"Median-Expectancy-je-Trade der Kohorte OHNE Selektionsstatistik "
                f"({statistics.median(missing_expectancy):.6f}, n={len(missing)}, "
                f"median_trades={median_n_missing}) ist signifikant GRÖSSER als die der Kohorte "
                f"MIT Statistik ({statistics.median(available_expectancy):.6f}, n={len(available)}, "
                f"median_trades={median_n_available}) — z={z:.4f}, p={p:.3g} < {alpha} (nur "
                f"Trials mit oos_total_trades >= {min_trades}; {n_below_min_trades} Trial(s) "
                "darunter vorab ausgeschlossen). Die fehlende Statistik ist nicht MCAR."),
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


def check_boundary_veto_has_evidence(proposal: dict) -> InvariantResult:
    """Issue #958/#1124 (Katalog #960) — "Ohne Evidenz kein Veto": jeder
    ``REJECTED_BOUNDARY_SOLUTION``/``HOLD_BOUNDARY_UNRESOLVED``-Ausgang muss mindestens einen
    benannten Parameter mit Wert UND beiden Bandgrenzen tragen (``boundary_veto_evidence``, siehe
    ``run_optimization._boundary_veto_evidence``-Docstring).

    Root-Cause #1124: die zuvor einzige öffentlich sichtbare "Beweis"-Grösse
    (``report.winner_outside_default_bounds``) verlangte eine STRIKTE Bounds-Verletzung, während
    das Veto selbst bereits auf blosser Nähe (<= 2 % vom Rand) feuert — 5 von 6 beobachteten Vetos
    trugen dadurch KEINEN sichtbaren Grund im Report (B-Beweis im #1124-Issue). Ein Veto OHNE
    benannten Parameter ist seit diesem Fix ein blockierender TELEMETRIEFEHLER, kein legitimes
    Urteil — strukturell sollte das nicht mehr vorkommen (``boundary_frac > 0`` impliziert per
    Konstruktion mindestens einen Eintrag in ``boundary_veto_evidence``, siehe
    ``_boundary_hit_analysis``-Docstring), dieser Check ist der Regressionswächter dagegen.

    ``proposal``: der vollständige exportierte Proposal-Dict (``status`` auf der OBERSTEN Ebene,
    ``boundary_veto_evidence`` in ``proposal['holdout']['symbol']``, derselbe Zugriffspfad wie
    ``check_promotion_multiplicity_route``).

    Issue #1035/#1184 Akzeptanzkriterium 2 — ZUSAETZLICH (unabhängig vom TERMINALEN ``status``, der
    nur die ERSTE verletzte Stufe einer Prioritätskette abbildet — ``confirm.py``s ``if not
    holdout_passed: ... elif pbo_overfit: ... elif boundary_unresolved: ...``) über ``proposal[
    'stage_results']['boundary']['passed']`` geprüft: eine Study, deren Boundary-Stufe FÜR SICH
    scheiterte (``boundary_unresolved``/``boundary_overfit``, unabhängig vom Holdout-Ergebnis
    berechnet), deren terminaler ``status`` aber einer ANDEREN, höher priorisierten Stufe
    zugeschrieben wurde (z. B. ``REJECTED_ON_HOLDOUT`` gewinnt vor einer ebenfalls verletzten
    Boundary-Stufe), wurde vom alten, rein status-basierten Gate NIE erfasst — dieselbe Klasse wie
    #1035 Fix Punkt 1 (das an dieselbe Prioritätskette gekoppelte, geerbte Detail), hier auf der
    Evidenz-Prüfung. ``stage_results`` fehlt bei Alt-Proposals (Legacy-Aufrufer) ⇒ diese
    Zusatzbedingung bleibt inaktiv, bit-identisch zum Pre-#1035-Verhalten."""
    status = proposal.get("status")
    stage_boundary_failed = (
        ((proposal.get("stage_results") or {}).get("boundary") or {}).get("passed") is False)
    expected = ("boundary_veto_evidence nicht-leer für REJECTED_BOUNDARY_SOLUTION/"
                "HOLD_BOUNDARY_UNRESOLVED/stage_results.boundary.passed=false")
    if status not in ("REJECTED_BOUNDARY_SOLUTION", "HOLD_BOUNDARY_UNRESOLVED") and not stage_boundary_failed:
        return InvariantResult(
            name="check_boundary_veto_has_evidence",
            passed=True,
            expected=expected,
            actual=None,
            severity="blocking",
            detail=f"status={status!r}, stage_results.boundary.passed nicht false — kein "
                   "Randlösungs-Ausgang, nicht anwendbar.",
        )
    evidence = ((proposal.get("holdout") or {}).get("symbol") or {}).get("boundary_veto_evidence")
    passed = bool(evidence)
    return InvariantResult(
        name="check_boundary_veto_has_evidence",
        passed=passed,
        expected=expected,
        actual=evidence,
        severity="blocking",
        detail=("OK" if passed else
                f"status={status!r} trägt KEINE boundary_veto_evidence — das Randlösungs-Veto "
                "feuerte ohne einen benannten Parameter (#958/#1124), ein blockierender "
                "Telemetriefehler."),
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


def check_n_family_partition(n_family: dict[str, int],
                             n_family_stage1: dict[str, dict[str, int]]) -> InvariantResult:
    """Issue #1080 (Katalog #866-2) — ``n_family[symbol]`` (die familienweite Multiple-Testing-
    Multiplizität) muss GENAU der Summe ihrer eigenen Zerlegung entsprechen: ``n_family[symbol] ==
    Σ n_family_stage1[symbol][*]`` (die N1-Zahl JEDER Strategie auf diesem Symbol, #826). Eine
    Lücke beweist, dass mindestens eine Study aus der Summe fehlt — im #866-Katalog:
    ``n_family['TSLA.ETORO'] = 467`` gegen ``Σ n_selection_statistic_available = 1619`` (die seit
    #822 vorgeschriebene Grundgesamtheit) — TrendPullbackStrategy fehlte vollständig im
    ``n_family_stage1``-Block (#1080 Fix: ``report._family_n_stages`` fällt jetzt auf
    ``n_selection_statistic_available`` zurück, wenn ``n_family_stage1`` fehlt). Eine zu niedrig
    angesetzte Familien-Multiplizität unterschätzt Φ⁻¹(1−1/n) und begünstigt JEDE Promotions-
    entscheidung mit familienweiter Korrektur.

    Issue #1102 (Katalog #935) — Root-Cause der ZURÜCKKEHRENDEN Lücke (Faktor 2,8–5,1 trotz des
    #1080-Fixes): ``n_family[symbol]`` wurde bis zu diesem Fix aus einer ZWEITEN, unabhängig
    berechneten Funktion (``sweep._family_n_from_proposals``, ``deflation_n_eligible`` — eine
    engere, seit #784/#822 veraltete Grundgesamtheit) gespeist, statt aus der Summe der eigenen
    ``n_family_stage1``-Zerlegung. ``report.py`` leitet ``n_family[symbol]`` seither DIREKT aus
    ``Σ n_family_stage1[symbol][*]`` ab — die Schranke ist damit eine TAUTOLOGIE (kann nur noch
    durch eine künftige Regression verletzt werden, die die getrennte Berechnung wieder einführt),
    ``severity`` steht deshalb jetzt auf ``blocking`` (vorher ``high`` hätte JEDEN Lauf blockiert,
    solange die beiden Zahlen strukturell divergierten)."""
    symbols_with_data = {s for s in n_family if s in n_family_stage1}
    if not symbols_with_data:
        return InvariantResult(
            name="check_n_family_partition",
            passed=True,
            expected="n_family[symbol] == Σ n_family_stage1[symbol][*]",
            actual=None,
            severity="blocking",
            detail="Keine Symbole mit sowohl n_family als auch n_family_stage1 — nicht anwendbar.",
        )
    offenders: dict[str, dict[str, int]] = {}
    for symbol in sorted(symbols_with_data):
        total = int(n_family.get(symbol) or 0)
        partition_sum = sum(n_family_stage1[symbol].values())
        if total != partition_sum:
            offenders[symbol] = {"n_family": total, "sum_n_family_stage1": partition_sum}
    passed = not offenders
    return InvariantResult(
        name="check_n_family_partition",
        passed=passed,
        expected="n_family[symbol] == Σ n_family_stage1[symbol][*]",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Symbol(e) mit einer Lücke zwischen n_family und der Summe seiner "
                f"eigenen Stage1-Zerlegung: {offenders} — mindestens eine Study fehlt in der Summe "
                "(#1080/#1102)."),
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
                                      deflation_n_family_raw: int | None,
                                      deflation_n_family_source: str | None = None,
                                      deflation_skipped_reason: str | None = None) -> InvariantResult:
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
    ``check_reward_term_variance``). ``deflation_n_family_raw is None`` ⇒ nicht anwendbar (PASS).

    Issue #1008/#1160 (Katalog #1170) — ZUSAETZLICHE, unabhaengige Koharenzpruefung (beide neuen
    Parameter Default ``None`` ⇒ bestehende Aufrufer/Tests bit-identisch unveraendert):
    ``deflation_n_family_source`` (die tatsaechlich verwendete Herkunfts-Quelle, z. B.
    ``'n_family_stage1_per_strategy'``) und ``deflation_skipped_reason`` (warum die Deflation
    NICHT lief, z. B. ``'SMALL_COHORT'``) sind ZWEI GETRENNTE Vokabulare — ein und dasselbe Feld
    darf nie beide tragen. Root-Cause #1160: ``confirm.py``'s Fail-Loud-Fallback (#978/#1132)
    schrieb bei einer uebersprungenen Deflation ``f"SKIPPED_{reason}"`` IN ``deflation_n_family_
    source`` (2 Vokabulare, 1 Feldname — dieselbe Bug-Klasse wie #1005/#1157). FAIL (severity
    ``high``), wenn (a) ``deflation_n_family_source`` mit ``'SKIPPED_'`` beginnt (der exakte
    Regressionsfall) ODER (b) BEIDE Felder gleichzeitig gesetzt sind (widerspruechlich: eine
    Study kann nicht sowohl eine echte Quelle ALS AUCH einen Skip-Grund tragen). Die UMGEKEHRTE
    Richtung (beide ``None``, obwohl Deflation angeblich lief) wird HIER bewusst NICHT geprueft —
    ``check_family_n_statistic_coverage`` kennt an dieser Call-Site nicht zuverlaessig, ob die
    Deflation fuer diese Study ueberhaupt erreicht wurde (eine vor der Deflations-Stufe
    abgelehnte Study traegt legitim beide Felder als ``None``, siehe ``report._decision_chain``).

    Issue #1034/#1183 (Katalog #1183, Akzeptanzkriterium 2) — ZUSAETZLICHER, unbedingter FAIL
    (``severity='blocking'``), wenn ``deflation_n_family_raw`` ZWAR gesetzt ist (die Deflationsstufe
    wurde erreicht — ``confirm.py`` schreibt dieses Feld NUR innerhalb von ``if deflation_sr0 is
    not None:``, also nur, sobald SR₀ tatsaechlich berechnet wurde), aber ``<= 0`` traegt: eine
    unaufloesbare Familien-Multiplizitaet (z. B. ``family_membership == 'excluded_degenerate'``,
    #981/#1135) darf die Deflationsstufe NIE mit ``deflation_n_family ∈ {None, 0}`` erreichen —
    ``confirm_per_symbol_promotion`` lehnt diesen Fall seit #1034 mit
    ``REJECT_PROMOTION_FAMILY_UNRESOLVABLE`` ab (kein Ersatzpfad); ein Vorkommen HIER ist ein
    Regressions-Symptom (der confirm.py-Guard wurde umgangen/entfernt), kein legitimer Zustand —
    dieselbe Rolle wie jeder andere ``severity='blocking'``-Waechter in diesem Modul."""
    if deflation_n_family_raw is None:
        coverage_result = InvariantResult(
            name="check_family_n_statistic_coverage",
            passed=True,
            expected="deflation_n_family_raw <= Trials mit oos_selection_statistic_available",
            actual=None,
            detail="deflation_n_family_raw unbekannt — nicht anwendbar.",
        )
    elif deflation_n_family_raw <= 0:
        coverage_result = InvariantResult(
            name="check_family_n_statistic_coverage",
            passed=False,
            severity="blocking",
            expected="deflation_n_family_raw >= 1, sobald die Deflationsstufe erreicht wird",
            actual=deflation_n_family_raw,
            detail=(
                f"deflation_n_family_raw={deflation_n_family_raw} — die Deflationsstufe wurde "
                "erreicht (SR0 berechnet), aber die Familien-Multiplizitaet ist unaufloesbar "
                "(None/0, z. B. FAMILY_EXCLUDED_DEGENERATE, #981/#1135). Eine solche Study darf "
                "nicht promotet werden (#1034/#1183: REJECT_PROMOTION_FAMILY_UNRESOLVABLE)."
            ),
        )
    else:
        n_with_statistic = sum(
            1 for t in trials if (t or {}).get("oos_selection_statistic_available") is True)
        passed = deflation_n_family_raw <= n_with_statistic
        coverage_result = InvariantResult(
            name="check_family_n_statistic_coverage",
            passed=passed,
            expected=f"<= {n_with_statistic}",
            actual=deflation_n_family_raw,
            detail=("OK" if passed else
                    f"deflation_n_family_raw={deflation_n_family_raw} > {n_with_statistic} Trials "
                    "mit oos_selection_statistic_available=True — die Zaehlung zaehlt Trials ohne "
                    "verwertbare Teststatistik mit (#822-Regression)."),
        )

    _source_carries_a_skip_sentinel = (
        isinstance(deflation_n_family_source, str)
        and deflation_n_family_source.startswith("SKIPPED_"))
    _both_vocabularies_set = (
        deflation_n_family_source is not None and deflation_skipped_reason is not None)
    if not (_source_carries_a_skip_sentinel or _both_vocabularies_set):
        return coverage_result
    if coverage_result.passed is False:
        # Issue #1008/#1160 — beide Teilpruefungen koennen unabhaengig FAILen; die #822-Zaehlungs-
        # Verletzung hat Vorrang (sie war zuerst da), die #1160-Vokabular-Verletzung wird im
        # Detail-Text angehaengt statt das Ergebnis stillschweigend zu ueberschreiben.
        return replace(
            coverage_result,
            detail=coverage_result.detail + " AUSSERDEM: deflation_n_family_source/"
            "deflation_skipped_reason vermischen ihre Vokabulare (#1160).",
        )
    return InvariantResult(
        name="check_family_n_statistic_coverage",
        passed=False,
        severity="high",
        expected="deflation_n_family_source traegt NIE einen 'SKIPPED_'-Sentinel; source und "
                 "skipped_reason sind nie gleichzeitig gesetzt",
        actual={"deflation_n_family_source": deflation_n_family_source,
               "deflation_skipped_reason": deflation_skipped_reason},
        detail=("deflation_n_family_source traegt einen 'SKIPPED_'-Sentinel (#1160-Regression)"
                if _source_carries_a_skip_sentinel else
                "deflation_n_family_source UND deflation_skipped_reason sind gleichzeitig "
                "gesetzt — widerspruechlicher Zustand (#1160)."),
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


def check_rejection_chain_completeness(
    proposal: dict, decision_chain: list[dict] | None = None,
    holdout_metrics: dict | None = None, tournament_config: dict | None = None,
) -> InvariantResult:
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
    Report-Kontext) ⇒ FAIL (leere Kette impliziert fehlende Stufen — kein stiller Freifahrtschein).

    Issue #1001/#1153 (Katalog #1170, P0) Fix Punkt 3 — ZWEITE, unabhaengige Kohaerenz-Gegenprobe:
    ein ``holdout``-Stufeneintrag mit ``passed=True`` ist selbst ein FAIL, wenn die Messgroessen
    DESSELBEN Records das Gegenteil belegen (``holdout_metrics['holdout_gate_deltas']`` traegt ein
    negatives AKTIVES Gate-Delta, oder ``holdout_metrics['oos_sortino_period'] <= 0``) — Root-Cause
    #1153: ``_decision_chain`` LEITETE frueher "bestanden" aus dem terminalen Ablehnungsgrund AB,
    statt es zu MESSEN (siehe dortiger Docstring); diese Gegenprobe bleibt auch nach der #1152-
    Praezedenz-Korrektur und der #1153-``stage_results``-Umstellung als dauerhafter
    Regressionswaechter bestehen, falls ein kuenftiger Aufrufer erneut auf Ableitung zurueckfaellt.
    ``tournament_config`` (optional) grenzt die Delta-Pruefung auf AKTIVE Gates ein
    (``reward._active_gate_collinearity_keys``); fehlt es, werden alle Deltas geprueft
    (konservativer — kann nicht false-negativ werden, nur zusaetzliche Kandidaten pruefen)."""
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

    # Issue #1001/#1153 Fix Punkt 3.
    coherence_violations: list[str] = []
    if holdout_metrics is not None:
        chain = decision_chain if decision_chain is not None else (proposal.get("decision_chain") or [])
        holdout_entry = next((c for c in chain if c.get("stage") == "holdout"), None)
        if holdout_entry is not None and holdout_entry.get("passed") is True:
            gate_deltas = holdout_metrics.get("holdout_gate_deltas") or {}
            if tournament_config is not None:
                from automation.optimizer.reward import _active_gate_collinearity_keys
                active_keys = set(_active_gate_collinearity_keys(tournament_config))
                relevant_deltas = {k: v for k, v in gate_deltas.items() if k in active_keys}
            else:
                relevant_deltas = gate_deltas
            negative_deltas = {k: v for k, v in relevant_deltas.items()
                               if isinstance(v, (int, float)) and v < 0}
            if negative_deltas:
                coherence_violations.append(
                    f"holdout: passed=True, aber (aktives) Gate-Delta negativ: {negative_deltas}")
            sortino_period = holdout_metrics.get("oos_sortino_period")
            if sortino_period is not None and sortino_period <= 0:
                coherence_violations.append(
                    f"holdout: passed=True, aber oos_sortino_period={sortino_period} <= 0")
    if coherence_violations:
        passed = False

    return InvariantResult(
        name="check_rejection_chain_completeness",
        passed=passed,
        expected=("alle obligatorischen decision_chain-Stufen (is_gate, confirm_or_selection, "
                  "holdout) mit passed=True bei promote=True; sonst holdout_reject_detail "
                  "gesetzt; keine passed=True-Stufe im Widerspruch zu den Messgroessen"),
        actual={"status": status, "holdout_reject_detail": detail_val, "missing_stages": missing,
                "coherence_violations": coherence_violations},
        detail=(" ".join(filter(None, [
            (None if not missing else
             f"status={status!r} (promote=True), aber decision_chain fehlt die Stufe(n) "
             f"{missing} mit passed=True (#785-Invariante verletzt)."),
            (None if promote or detail_val is not None else
             f"status={status!r}, aber holdout_reject_detail ist None — Ablehnungsursache "
             "fehlt (#654/#671-Invariante verletzt)."),
            (None if not coherence_violations else
             "Kohaerenz-Widerspruch (#1153): " + " ".join(coherence_violations)),
        ])) or "OK"),
    )


def check_decision_chain_stage_detail_isolation(
    decision_chain: list[dict] | None,
) -> InvariantResult:
    """Issue #1035/#1184 Akzeptanzkriterium 1 — Regressionswächter: kein ``detail`` einer Stufe im
    ``decision_chain`` entspricht dem ``detail`` einer ANDEREN Stufe derselben Kette.

    Root-Cause #1035: ``confirm.confirm_per_symbol_promotion``s ``stage_results['boundary'][
    'detail']`` las bislang ``is_rejection_detail_override`` — eine Grösse, die die Ursache der
    ERSTEN (höchstpriorisierten) verletzten Stufe der Gesamtentscheidung trägt (``if not
    holdout_passed: ... elif boundary_unresolved: ...``), NICHT die Ursache der Boundary-Stufe
    selbst. Eine Study, die SOWOHL das Holdout-Gate verfehlte ALS AUCH (unabhängig berechnet) an
    der Boundary klemmte, erbte so den HOLDOUT-Grund (z. B. ``REJECT_HOLDOUT_GATE``) als
    vermeintlichen Boundary-Stufen-Grund — zwei disjunkte Stufen trugen denselben Detail-Code, ein
    Leser konnte die tatsächliche Boundary-Ursache nicht mehr vom geerbten Holdout-Grund
    unterscheiden. Fix: jede Stufe setzt seither ihren EIGENEN, dedizierten Code (siehe
    ``stage_results['pbo']``/``['boundary']`` in confirm.py: ``REJECT_SELECTION_PBO``/
    ``REJECT_SELECTION_BOUNDARY``) statt des terminalen ``is_rejection_detail_override``.

    Diese Invariante ist die dauerhafte Gegenprobe: für JEDES Paar verschiedener Stufen mit
    jeweils einem NICHT-``None``-``detail`` prüft sie, dass die beiden ``detail``-Werte NICHT
    identisch sind (ein legitimer decision_chain hat je Stufe ein disjunktes Code-Vokabular — zwei
    unterschiedliche Stufen teilen sich strukturell nie denselben Grund). ``decision_chain`` fehlt/
    leer (Legacy-Aufrufer) ⇒ nicht anwendbar (PASS, kein erfundener Befund)."""
    chain = decision_chain or []
    labeled = [(c.get("stage"), c.get("detail")) for c in chain if c.get("detail") is not None]
    collisions: list[dict] = []
    for i in range(len(labeled)):
        stage_a, detail_a = labeled[i]
        for j in range(i + 1, len(labeled)):
            stage_b, detail_b = labeled[j]
            if stage_a != stage_b and detail_a == detail_b:
                collisions.append({"stages": [stage_a, stage_b], "detail": detail_a})
    passed = not collisions
    return InvariantResult(
        name="check_decision_chain_stage_detail_isolation",
        passed=passed,
        severity="high",
        expected="jede decision_chain-Stufe trägt ein disjunktes detail-Vokabular",
        actual=collisions,
        detail=("OK" if passed else
                f"decision_chain-Stufen teilen sich denselben detail-Code: {collisions} — eine "
                "Stufe hat den Grund einer ANDEREN Stufe geerbt statt ihren eigenen zu tragen "
                "(#1035/#1184-Regression)."),
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
    trials: list[dict], *, min_measured_fraction: float = 0.10,
) -> InvariantResult:
    """Issue #955/#1121 (Katalog #960, ersetzt #979) — misst NICHT mehr
    ``reward_terms.branch == 'per_symbol'``.

    Root-Cause der ALTEN Fassung: ``branch == 'per_symbol'`` ist per Konstruktion IDENTISCH zu
    ``oos_eligible`` (B-13: 1804 von 1804 Trials über drei Läufe) — der ordnende Reward-Zweig wird
    NUR für eligible Trials gewählt. Die alte Fassung war damit eine blosse UMBENENNUNG von
    ``p_eligible`` (bereits je Study exportiert, ``report._study_record``): zwei Namen für dieselbe
    Beobachtung erzeugten zwei Befunde im Report und überzeichneten die Fehlerlage (der Check
    feuerte in 5/29 bzw. 11/42 Studies, obwohl die globale Eligibility-Rate mit 31,41 % gesund
    war). ``check_search_made_progress`` deckt den echten Suchstagnations-Fall bereits ab
    (Wiederkehr der #1052-Klasse: eine Meldung nennt eine Ursache, die die Messung nicht trennt).

    Neue Definition: der Anteil der INELIGIBLEN Trials (``oos_evaluated=True``,
    ``oos_eligible != True``) mit einer DEFINIERTEN Selektionsstatistik
    (``oos_selection_statistic_available=True``) — eine Grösse, die ``p_eligible`` NICHT
    duplizieren kann (sie ist auf die ineligible Teilmenge bedingt, unabhängig davon, WIE VIELE
    Trials überhaupt eligible sind). Ein niedriger Wert bedeutet: die Mehrheit der abgelehnten
    Auswertungen ist gar nicht MESSBAR gewesen — eine von der Eligibility-Rate unabhängige Diagnose
    ("wurde überhaupt gemessen, was abgelehnt wurde?"), genau dort, wo die Frage tatsächlich offen
    ist (Fix-Vorschlag 2 des Issues).

    Der CHECK-NAME bleibt unverändert: ``sweep._apply_search_budget_proposal``/
    ``report._search_budget_proposal_section`` (#1082) lesen ausschliesslich über den Namen — der
    Suchbudget-Deprioritisierungsmechanismus bleibt dadurch unverändert funktionsfähig, nur mit
    einer nicht mehr duplizierten Eingangsgrösse.

    ``trials``: ``user_attrs``-artige Dicts EINER Study mit ``oos_evaluated``/``oos_eligible``/
    ``oos_selection_statistic_available``."""
    ineligible = [
        t for t in trials
        if t.get("oos_evaluated") is True and t.get("oos_eligible") is not True
    ]
    expected = (f"Anteil ineligibler Trials mit definierter Selektionsstatistik >= "
                f"{min_measured_fraction}")
    if not ineligible:
        return InvariantResult(
            name="check_objective_branch_coverage",
            passed=True,
            expected=expected,
            actual=None,
            detail="Keine ineligiblen oos_evaluated-Trials — nicht anwendbar (jeder evaluierte "
                   "Trial dieser Study war eligible, oder kein Trial evaluiert).",
        )
    n_measured = sum(1 for t in ineligible if t.get("oos_selection_statistic_available") is True)
    fraction = n_measured / len(ineligible)
    passed = fraction >= min_measured_fraction
    return InvariantResult(
        name="check_objective_branch_coverage",
        passed=passed,
        expected=expected,
        actual=round(fraction, 4),
        detail=("OK" if passed else
                f"Nur {n_measured}/{len(ineligible)} ineligible Trials ({fraction:.2%}) tragen eine "
                f"definierte Selektionsstatistik — unter der Schwelle "
                f"({min_measured_fraction:.0%}). Die Mehrheit der abgelehnten Auswertungen dieser "
                "Study ist gar nicht MESSBAR gewesen (#955/#1121)."),
    )


def check_annualization_commensurability(
    study_records: list[dict], *, max_ratio: float = 1.05,
) -> InvariantResult:
    """Issue #948/#1114 (Katalog #960, ersetzt #978) — misst seit diesem Fix NICHT mehr die
    INTRA-Trial-Fold-Streuung des annualisierten Sortino.

    Root-Cause der ALTEN Fassung: ``F`` (``√F = oos_fold_sortino / oos_fold_sortino_period`` je
    Fold) wird empirisch aus der Beobachtungszahl JE FOLD abgeleitet (``_get_annualization_factor``)
    und variiert dadurch strukturell INNERHALB eines Trials (RTH-Instrumente auf 24/7-Raster,
    ``check_n_periods_homogeneity`` meldet Spannweiten bis 299,4). B-10 (Katalog #960, 3535 Trials):
    Median-Spannweite 1,709, 99,15 % über der alten Schwelle 1,05 — die alte Fassung feuerte damit
    auf praktisch JEDEM Trial, ohne eine echte Entscheidungsgefahr zu markieren: seit #665/#589
    konsumiert KEIN Entscheidungspfad mehr den annualisierten Fold-Sortino
    (``per_fold_oos_sortino``/``oos_fold_sortinos`` sind reine Anzeige-Telemetrie;
    ``confirm._study_pbo`` rechnet auf eigenen S>=8-Gruppen der gepoolten ``oos_period_returns``,
    ``reward.fold_dispersion`` auf ``oos_fold_returns`` — beide bereits annualisierungsfrei).

    Neue Definition: die Streuung des EINEN studienweiten, GEPOOLTEN Annualisierungsfaktors
    (``sqrt(F) = holdout_sortino_annualized / holdout_sortino_period``, aus der vollen
    Holdout-Equity-Kurve — #532/#595) ÜBER Studies DESSELBEN Symbols. Ein grosser Sprung zwischen
    zwei Studies desselben Symbols zeigt eine echte Verschiebung des effektiven Handelszeitfensters
    (RTH-Abdeckung, Datenlücken, 24/7-Padding) — eine Datenintegritätsfrage, keine triviale
    Fold-Streuung.

    Issue #980/#1134 (Katalog #986) — severity ``high`` (vorher ``low``): mit
    ``backtest_runner._get_annualization_factor_with_source`` F je Symbol EINMAL bestimmt statt je
    Study aus deren eigenem (positions-abhaengigen) mtm_series-Fenster — eine verbleibende
    Abweichung ist damit kein strukturelles Artefakt mehr, sondern ein echter Befund (die
    Hochstufung war an diesen Fix gebunden, analog #979/#1133).

    Prüft je Symbol mit >= 2 Studies mit definiertem sqrt(F): ``max(√F) / min(√F) <= max_ratio``.
    ``study_records``: Report-``studies[]``-artige Dicts mit ``symbol``/``holdout_sortino_period``/
    ``holdout_sortino_annualized`` (siehe ``report._study_record``)."""
    by_symbol: dict[str, list[float]] = {}
    for r in study_records:
        symbol = r.get("symbol")
        period = r.get("holdout_sortino_period")
        annualized = r.get("holdout_sortino_annualized")
        if not symbol or not period or annualized is None:
            continue
        ratio = annualized / period
        if ratio > 0:
            by_symbol.setdefault(symbol, []).append(ratio)

    worst_ratio = 0.0
    worst_symbol: str | None = None
    n_symbols_comparable = 0
    n_studies_comparable = 0
    n_studies_offending = 0
    offenders: dict[str, float] = {}
    for symbol, ratios in by_symbol.items():
        if len(ratios) < 2:
            continue
        n_symbols_comparable += 1
        n_studies_comparable += len(ratios)
        symbol_ratio = max(ratios) / min(ratios)
        if symbol_ratio > worst_ratio:
            worst_ratio = symbol_ratio
            worst_symbol = symbol
        if symbol_ratio > max_ratio:
            offenders[symbol] = round(symbol_ratio, 4)
            n_studies_offending += len(ratios)

    if n_symbols_comparable == 0:
        return InvariantResult(
            name="check_annualization_commensurability",
            passed=True,
            expected=f"max(sqrt(F))/min(sqrt(F)) <= {max_ratio} je Symbol ueber dessen Studies",
            actual=None,
            severity="high",
            detail="Kein Symbol mit >= 2 Studies mit definiertem studienweiten sqrt(F) — "
                   "nicht anwendbar.",
        )
    passed = not offenders
    fraction_studies_offending = round(n_studies_offending / n_studies_comparable, 4)
    return InvariantResult(
        name="check_annualization_commensurability",
        passed=passed,
        expected=f"max(sqrt(F))/min(sqrt(F)) <= {max_ratio} je Symbol ueber dessen Studies",
        actual=round(worst_ratio, 4),
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)}/{n_symbols_comparable} Symbole — schlechtestes {worst_symbol}: "
                f"sqrt(F)-Spannweite über dessen Studies beträgt Faktor {worst_ratio:.4g} "
                f"(> {max_ratio}). Seit #948/#1114 misst dieser Check NICHT mehr die (erwartete, "
                "triviale) Intra-Trial-Fold-Streuung, sondern eine echte Handelszeitfenster-"
                "Verschiebung zwischen Studies desselben Symbols."),
        provenance={
            "offenders": offenders,
            "n_symbols_comparable": n_symbols_comparable,
            "n_studies_comparable": n_studies_comparable,
            "fraction_studies_offending": fraction_studies_offending,
        } if offenders else {
            "n_symbols_comparable": n_symbols_comparable,
            "n_studies_comparable": n_studies_comparable,
        },
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
    n_trials: int | None = None,
) -> InvariantResult:
    """Issue #886 (Pitfall #276) — ersetzt die reine Anwesenheits-Prüfung für die #863/#864
    "regulären dritten Ausgänge" (``SORTINO_GUARD_TRIPPED``/``SORTINO_INSUFFICIENT_DOWNSIDE``)
    durch eine KONZENTRATIONS-Prüfung: FAIL, wenn eine Study mehr als
    ``guard_dominance_threshold`` ihrer Trials an den Inferenzpfad verliert. Das ist dieselbe
    Bedingung wie ``run_optimization._emit_study_summary``s ``STUDY_GUARD_DOMINATED`` — dieser
    Check macht sie zusätzlich zu einer MASCHINELL überprüfbaren #742-Report-Aussage (analog
    ``check_inference_diagnostics_absent``), nicht nur einer Live-Log-Zeile.

    Issue #1078 (Katalog #866-2, Pitfall #356-Wiederkehr mit exakter Wurzel) — der Nenner ist seit
    diesem Fix ``n_trials`` (die VOLLE Trial-Zahl dieser Study), NICHT mehr
    ``n_trials_informative``. Root-Cause: der Zähler (``affected`` — Trials MIT einem
    censoring-/adaptive-Code) und ``n_trials_informative`` (Trials OHNE einen solchen Code, per
    Konstruktion) sind ZWEI DISJUNKTE Teilmengen derselben Grundgesamtheit — ein Zähler geteilt
    durch die GEGENTEIL-Menge ist kein Anteil, sondern ein Verhältnis zwischen zwei
    komplementären Gruppen (Beweis B-12 im #866-Katalog: Squeeze, 81 zensierte Trials gegen 56
    informative — 81/56 = 1,4464, strukturell > 1 möglich). ``n_trials`` ist die kommensurable
    Grundgesamtheit, der ``affected`` per Konstruktion nie überschreiten kann.

    Rückwärtskompatibilität: fehlt ``n_trials`` (Legacy-/Test-Aufrufer), fällt der Nenner auf
    ``n_trials_informative`` zurück (Pre-#1078-Verhalten, inklusive der #1033-Inkommensurabilitäts-
    Prüfung als Sicherheitsnetz). ``n_trials_informative is None`` UND ``n_trials is None``
    (Legacy-Report ohne #885-Telemetrie) ⇒ nicht anwendbar (PASS)."""
    denominator = n_trials if n_trials is not None else n_trials_informative
    if denominator is None:
        return InvariantResult(
            name="check_inference_diagnostics_concentration",
            passed=True,
            expected=f"<= {guard_dominance_threshold:.0%} der Trials mit "
                     "SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE",
            actual=None,
            detail="Weder n_trials noch n_trials_informative bekannt (Pre-#885-Report) — nicht "
                   "anwendbar.",
        )
    affected = 0
    for t in trials:
        for diag in t.get("inference_diagnostics") or ():
            code = diag.get("code") if isinstance(diag, dict) else None
            if code in _REGULAR_THIRD_OUTCOME_CODES:
                affected += 1
                break
    fraction = (affected / denominator) if denominator > 0 else 0.0
    # Issue #1033 (Katalog #866, Pitfall #356) — Sicherheitsnetz für den Legacy-Nenner-Fallback
    # (``n_trials`` nicht übergeben): ``fraction > 1.0`` beweist eine Zaehler/Nenner-
    # Inkommensurabilitaet, keinen gueltigen Beobachtungswert. Mit dem #1078-Nenner (``n_trials``)
    # kann dieser Zweig strukturell nicht mehr erreicht werden (affected <= n_trials immer).
    if fraction > 1.0:
        return InvariantResult(
            name="check_inference_diagnostics_concentration",
            passed=False,
            expected="Zaehler/Nenner kommensurabel (fraction <= 1.0)",
            actual=round(fraction, 4),
            detail=f"{affected}/{denominator} — Zaehler/Nenner nicht kommensurabel "
                   "(Rate > 1.0 ist kein gueltiger Beobachtungswert, #1033).",
        )
    passed = fraction <= guard_dominance_threshold
    return InvariantResult(
        name="check_inference_diagnostics_concentration",
        passed=passed,
        expected=f"<= {guard_dominance_threshold:.0%} der Trials mit "
                 "SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE",
        actual=round(fraction, 4),
        detail=("OK" if passed else
                # Issue #948 (Katalog B, P2) — vorher stand hier "mit einem regulären Inferenzpfad-
                # Ausgang", das genaue GEGENTEIL der Bedeutung: die betroffenen Trials sind die
                # ZENSIERTEN/IRREGULÄREN Ausgänge (SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_
                # DOWNSIDE), nicht die regulären. Ein Leser, der nur diese Detail-Zeile liest (ohne
                # das korrekte `expected`-Feld daneben), zog den umgekehrten Schluss.
                f"{affected}/{denominator} Trials ({fraction:.1%}) wurden vom "
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

    ``study_counts`` fehlen die #885-Zähler (Pre-#885-Report) ⇒ nicht anwendbar (PASS).

    Issue #1079 (Pitfall #377) — ZWEITE, unabhängige Identität, wenn ``study_counts`` zusätzlich
    ``n_evaluable`` trägt (``report._study_record``s trial_attrs-basierter Zähler, eine von
    ``n_trials_informative`` VÖLLIG UNABHÄNGIG geführte Zählung): ``n_evaluable +
    n_trials_pruned + n_trials_unevaluable + n_trials_failed == n_trials_total`` muss GENAUSO
    gelten. Root-Cause #1079: vor der #1079-Quellkorrektur enthielt ``n_evaluable`` fälschlich auch
    PRUNED-state Trials (deren ``user_attrs`` noch ein veraltetes ``oos_evaluated=True`` trugen) —
    diese zweite Identität ist der Regressionswächter dagegen, unabhängig von der ersten (die
    ausschliesslich über ``n_trials_informative`` rechnet und den PRUNED-Fehlalarm daher NICHT
    sieht). Fehlt ``n_evaluable`` (Pre-#1079-Report) ⇒ nur die erste Identität zählt (bit-identisch
    zum Pre-#1079-Verhalten)."""
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
    detail_parts = []
    if not passed:
        detail_parts.append(
            f"Zerlegung ({parts_sum}) != n_trials_total ({total}): {values} — die #885-"
            "Trial-Kategorien sind nicht disjunkt/vollständig.")

    n_evaluable = study_counts.get("n_evaluable")
    if n_evaluable is not None:
        evaluable_parts_sum = (
            n_evaluable + values["n_trials_pruned"] + values["n_trials_unevaluable"]
            + values["n_trials_failed"])
        evaluable_passed = evaluable_parts_sum == total
        passed = passed and evaluable_passed
        values = {**values, "n_evaluable": n_evaluable}
        if not evaluable_passed:
            detail_parts.append(
                f"Zweite Identität (#1079): n_evaluable + n_trials_pruned + n_trials_unevaluable + "
                f"n_trials_failed ({evaluable_parts_sum}) != n_trials_total ({total}) — n_evaluable "
                "ueberlappt mit einer der anderen Kategorien (z. B. PRUNED-Trials, Beweis B-13 im "
                "#866-Katalog).")

    return InvariantResult(
        name="check_denominator_coherence",
        passed=passed,
        expected="n_trials_informative + n_trials_pruned + n_trials_unevaluable + "
                 "n_trials_failed == n_trials_total (und n_evaluable analog, sofern vorhanden)",
        actual=values,
        detail="OK" if passed else " ".join(detail_parts),
    )


_SENTINEL_GUARDED_METRIC_KEYS = (
    "oos_win_rate", "oos_profit_factor", "oos_expectancy", "oos_total_return",
    "oos_sortino", "oos_psr", "oos_sortino_period",
    # Issue #1100 (Katalog #933, siebte Instanz derselben #759/#788/#966-Fehlerklasse) —
    # ``oos_buyhold_return`` (auf Holdout-Ebene ``holdout_buyhold_return``, siehe
    # ``check_holdout_buyhold_return_coherence`` fuer den spezifischeren, symbolweiten
    # Kohaerenz-Wächter) gehoert in dieselbe Sentinel-Familie: ein nie evaluierter Trial darf
    # KEINE Benchmark-Beobachtung tragen.
    "oos_buyhold_return",
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


def check_holdout_buyhold_return_coherence(study_records: list[dict]) -> InvariantResult:
    """Issue #1100 (Katalog #933) — symbolweiter Kohärenz-Wächter, siebte Instanz derselben
    #759/#788/#966-Sentinel-Kollaps-Fehlerklasse: ``holdout_buyhold_return`` (der Buy&Hold-
    Benchmark-Return über das Holdout-Fenster, ``backtest_runner``s ``PortfolioMonitor.
    get_benchmark_series`` — eine reine Preisserie DES SYMBOLS, unabhängig davon, ob/wie oft die
    jeweilige Strategie tatsächlich handelte) MUSS für alle Studies DESSELBEN Symbols und
    Holdout-Fensters IDENTISCH sein, unabhängig von ``holdout_total_trades``.

    Symptom #1100: ``SqueezeBreakoutStrategy`` meldet in ASML/PLTR/NVDA ``holdout_buyhold_return
    = 0.0``, während Schwester-Studies DESSELBEN Symbols (u. a. ``TrendPullbackStrategy`` — trotz
    ebenfalls 0 Holdout-Trades) den korrekten, von 0 verschiedenen Wert tragen — ein Nullwert bei
    ``holdout_total_trades == 0`` ist hier NICHT per se verdächtig (0 Trades ist bei mehreren
    Strategien desselben Symbols beobachtet), aber ein Nullwert, der von der beobachteten
    Symbol-Wahrheit (ein Schwester-Record trägt einen echten Wert) ABWEICHT, beweist einen
    kollabierten Sentinel statt einer echten Marktbeobachtung.

    FAIL (severity ``high``, reine Diagnose — kein Promotion-Gate) je Study, wenn
    ``holdout_total_trades == 0 and holdout_buyhold_return == 0.0``, WÄHREND mindestens ein
    anderer Study-Record desselben Symbols einen ``holdout_buyhold_return not in (None, 0.0)``
    trägt. Symbole ohne jeden von 0 verschiedenen Schwester-Wert werden übersprungen (keine
    Vergleichsgrundlage — ein Symbol, dessen Markt über das gesamte Holdout-Fenster tatsächlich
    exakt seitwärts lief, ist nicht von diesem Wächter zu unterscheiden, aber auch kein
    beobachtbarer Fehler)."""
    by_symbol: dict[str, list[dict]] = {}
    for r in study_records:
        symbol = r.get("symbol")
        if symbol:
            by_symbol.setdefault(symbol, []).append(r)
    offenders: dict[str, dict] = {}
    for symbol, records in by_symbol.items():
        sibling_values = sorted({
            r["holdout_buyhold_return"] for r in records
            if r.get("holdout_buyhold_return") not in (None, 0.0)
        })
        if not sibling_values:
            continue
        for r in records:
            if r.get("holdout_total_trades") == 0 and r.get("holdout_buyhold_return") == 0.0:
                offenders[f"{r.get('strategy')}/{symbol}"] = {
                    "holdout_buyhold_return": 0.0,
                    "sibling_holdout_buyhold_returns": sibling_values,
                }
    passed = not offenders
    return InvariantResult(
        name="check_holdout_buyhold_return_coherence",
        passed=passed,
        expected="holdout_buyhold_return == 0.0 bei holdout_total_trades == 0 nur ohne einen von "
                 "0 verschiedenen Schwester-Wert desselben Symbols",
        actual=offenders or None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies mit holdout_buyhold_return=0.0 bei 0 Holdout-"
                "Trades, obwohl eine Schwester-Study desselben Symbols einen echten "
                "Marktwert trägt (#1100-Fehlerklasse: kollabierter Sentinel statt None)."),
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


def gate_inventory_table(
    trials: list[dict], eligible_requires_all: list[str], *,
    is_rejection_detail_counts: dict[str, int] | None = None,
    tournament_config: dict | None = None,
) -> list[dict[str, Any]]:
    """Issue #970 (Katalog A, P1) — Gate-Inventur mit Entscheidungspflicht: die Selektion war im
    Referenzlauf 46cf5070 faktisch eine Ein-Statistik-Entscheidung (``oos_min_psr`` = 97.3% aller
    Ablehnungen, 62.7% aller evaluierten Trials solo), während 7 von 10 konfigurierten
    ``eligible_requires_all``-Gates in 2135 Evaluierungen KEIN einziges Mal ablehnten.

    Je konfiguriertem Gate: ``n_rejections``, ``n_solo_rejections`` (Trials, bei denen dieses Gate
    das EINZIGE verletzte unter ``eligible_requires_all`` ist), ``marginal_delta`` (``|eligible
    ohne dieses Gate| − |eligible mit allen Gates|`` — wie viele zusätzliche Trials eligible wären,
    würde man dieses Gate aus der Konjunktion entfernen; ``0`` heisst, das Gate trägt über die
    beobachtete Kohorte NICHTS zur Selektion bei, ein Kandidat für Entfernung oder
    Neukalibrierung).

    Issue #956/#1122 (Katalog #960) — Root-Cause: ``n_rejections`` zählte VOR diesem Fix
    unabhängig, wie oft ``oos_gate_deltas[gate] > 0`` war (MEHRLABEL: ein Trial zählt je verletztem
    Gate) — dieselbe Grundgesamtheit wie ``marginal_delta``, aber NICHT dieselbe wie
    ``is_rejection_detail_counts`` (EINLABEL: nur die PRIMÄRE Ablehnungsursache,
    ``run_optimization._classify_is_rejection_detail``). B-13: ``AdxAtrMomentumStrategy/NVDA.
    ETORO`` hatte ``n_rejections['oos_min_psr'] == 0`` bei ``is_rejection_detail_counts[
    'REJECT_OOS_MIN_PSR'] == 140`` — das Gate, das JEDEN einzelnen Trial verwarf, wurde als
    beitragslos geführt (``oos_gate_deltas`` trägt den Key ``'oos_min_psr'`` NUR, wenn ``oos_psr``
    selbst definiert ist, siehe ``reward._normalized_gate_distances``-Docstring — ein struktureller
    Blinder Fleck für genau die Fälle, in denen die Statistik am häufigsten fehlschlägt).

    Fix: ``n_rejections`` wird — sofern ``is_rejection_detail_counts`` übergeben ist — DIREKT
    daraus abgeleitet (``is_rejection_detail_counts['REJECT_OOS_' + gate_ohne_oos_praefix.upper()]``,
    dieselbe Normierung wie die jetzt entfallende ``check_gate_inventory_coherence``), statt
    parallel aus ``oos_gate_deltas`` gepflegt zu werden — EINE Quelle statt zwei, die
    unterschiedliche Populationen zählen. ``n_solo_rejections``/``marginal_delta`` bleiben
    zwingend MEHRLABEL (sie beantworten "wie viele Trials waren AUSSCHLIESSLICH an diesem Gate
    gescheitert" bzw. "wie viele wären OHNE dieses Gate zusätzlich eligible" — Fragen, die
    ``is_rejection_detail_counts`` als Einlabel-Zählung strukturell nicht beantworten kann).
    ``is_rejection_detail_counts=None`` (Legacy-/Test-Aufrufer ohne dieses Argument) fällt auf die
    ALTE, gate-delta-basierte Zählung zurück (rückwärtskompatibel).

    ``trials``: ``user_attrs``-artige Dicts mit ``oos_gate_deltas`` (dict Gate→Delta, ``> 0`` =
    verletzt, aus ``reward._normalized_gate_distances``/``_compute_oos_constraints``) UND
    ``oos_evaluated``. Nur über TATSÄCHLICH OOS-evaluierte Trials (ein nicht evaluierter Trial hat
    keine Gate-Deltas und trägt zu keinem Gate bei).

    Issue #1003/#1155 (Katalog #1170, P1) — Root-Cause: ``n_solo_rejections`` wurde AUS DERSELBEN
    ``oos_gate_deltas``-Quelle gebildet wie ``marginal_delta`` (``n_eligible_without_gate −
    n_eligible_with_all`` == per Konstruktion IMMER die Anzahl der Trials mit ``violated ==
    {gate}`` — derselbe Zaehler unter zwei Namen), UND diese Quelle hat den in #956/#1122
    dokumentierten Blinden Fleck: ``oos_gate_deltas`` fehlt der Key fuer ein Gate GANZ, sobald
    dessen zugrunde liegende Statistik undefiniert war (kein numerischer Wert unter der Schwelle) —
    ``deltas.get(g, 0.0)`` liest das dann fälschlich als "bestanden", wodurch ein Trial mit
    MEHREREN tatsächlichen Ablehnungsgründen als "solo an genau diesem einen Gate gescheitert"
    erscheinen kann (Symptom: ``n_solo_rejections > n_rejections`` in 7/84 Eintraegen, ``==
    n_eligible`` in 27/30).

    Fix Punkt 1 — ``n_solo_rejections`` wird PRIMAER aus ``oos_rejection_reasons`` (der vollen,
    ungekürzten Pro-Trial-Gründeliste, ``run_optimization``s Quelle fuer ``is_rejection_detail``
    selbst — dieselbe Quelle wie ``n_rejections`` oben, keine dritte, unabhaengig driftende Zahl)
    gebildet: ein Trial zählt fuer ``gate`` als solo, wenn er GENAU EINEN Rejection-Grund traegt
    UND dessen Gate-Praefix (vor dem ersten ``":"``) diesem Gate entspricht — UND dieser eine Grund
    NICHT die #917-Undefiniert-Marke traegt (sonst waere die Klassifikation
    ``REJECT_OOS_STATISTIC_UNAVAILABLE``, nicht dieses Gate — dieselbe Vorrangregel wie
    ``run_optimization._classify_is_rejection_detail``, sonst koennte ``n_solo_rejections`` erneut
    ueber ``n_rejections`` (is_rejection_detail_counts-basiert) hinauswachsen). Traegt KEIN Trial
    der Kohorte ``oos_rejection_reasons`` (Alt-Report vor der #994/#1146-Stempelung), faellt die
    Zaehlung auf die alte ``oos_gate_deltas``-Naeherung zurueck (rueckwaertskompatibel).

    Fix Punkt 2 — ``marginal_delta`` ist, sofern ``tournament_config`` übergeben ist, seither
    ``reward.gate_marginal_pass_rate_delta`` (``P(eligible ohne Gate) − P(eligible mit Gate)`` über
    die AKTIVEN Gates der vollen Konjunktion, #811) — eine ECHTE, von ``n_solo_rejections``
    UNABHAENGIGE Groesse (eine Wahrscheinlichkeit in [0, 1], kein roher Trial-Zaehler), statt einer
    Kopie desselben Zaehlers unter zweitem Namen. ``tournament_config=None`` (Legacy-/Test-Aufrufer)
    faellt auf die alte, zaehlerbasierte Naeherung zurück.

    Fix Punkt 3 — harte, fail-loud Ordnungs-Invariante ``0 <= n_solo_rejections <= n_rejections <=
    n_evaluated`` je Gate: ein Verstoss ist ein Programmfehler in der Zaehllogik selbst (keine
    Dateninkonsistenz, die still durchgereicht werden dürfte) und wirft, statt eine falsche Zahl
    weiterzugeben (das genaue #1155-Symptom)."""
    evaluated = [t for t in trials if t.get("oos_evaluated") is True and t.get("oos_gate_deltas")]
    if not evaluated or not eligible_requires_all:
        return []

    def _canon(key: str) -> str:
        return key[4:] if key.startswith("oos_") else key

    _undefined_marker = "None (insufficient"
    try:
        from automation.optimizer.run_optimization import _OOS_UNDEFINED_STATISTIC_MARKER
        _undefined_marker = _OOS_UNDEFINED_STATISTIC_MARKER
    except Exception:
        pass

    def _solo_gate_key_from_reasons(reasons) -> str | None:
        if not reasons or len(reasons) != 1:
            return None
        reason = reasons[0]
        if not isinstance(reason, str) or ":" not in reason or _undefined_marker in reason:
            return None
        return _canon(reason.split(":", 1)[0].strip())

    has_reasons_field = any(t.get("oos_rejection_reasons") is not None for t in evaluated)

    marginal_delta_by_gate: dict[str, float | None] = {}
    if tournament_config is not None:
        from automation.optimizer.reward import gate_marginal_pass_rate_delta
        trial_gate_deltas = [t.get("oos_gate_deltas") for t in evaluated]
        for gate in eligible_requires_all:
            marginal_delta_by_gate[gate] = gate_marginal_pass_rate_delta(
                trial_gate_deltas, tournament_config, gate)

    table = []
    for gate in eligible_requires_all:
        canon_gate = _canon(gate)
        n_rejections_from_deltas = 0
        n_solo_from_deltas = 0
        n_solo_from_reasons = 0
        n_eligible_without_gate = 0
        n_eligible_with_all = 0
        for t in evaluated:
            deltas = t.get("oos_gate_deltas") or {}
            violated = {g for g in eligible_requires_all if float(deltas.get(g, 0.0) or 0.0) > 0.0}
            if gate in violated:
                n_rejections_from_deltas += 1
                if violated == {gate}:
                    n_solo_from_deltas += 1
            if not violated:
                n_eligible_with_all += 1
            if violated <= {gate}:
                n_eligible_without_gate += 1
            if _solo_gate_key_from_reasons(t.get("oos_rejection_reasons")) == canon_gate:
                n_solo_from_reasons += 1
        if is_rejection_detail_counts is not None:
            normalized = gate[4:] if gate.startswith("oos_") else gate
            code = f"REJECT_OOS_{normalized.upper()}"
            n_rejections = int(is_rejection_detail_counts.get(code) or 0)
        else:
            n_rejections = n_rejections_from_deltas
        # Issue #1003/#1155 — dieselbe "Quellen-Stufe" wie n_rejections: die reasons-basierte
        # Zaehlung ist nur dann GARANTIERT konsistent mit is_rejection_detail_counts-basiertem
        # n_rejections (0 <= n_solo <= n_rejections), wenn BEIDE aus derselben Quellenfamilie
        # stammen; ohne is_rejection_detail_counts (Legacy-/Test-Aufrufer) bleibt n_solo auf der
        # delta-basierten Naeherung, die MATHEMATISCH garantiert <= n_rejections_from_deltas ist
        # (violated == {gate} impliziert gate in violated).
        n_solo = (n_solo_from_reasons
                  if (is_rejection_detail_counts is not None and has_reasons_field)
                  else n_solo_from_deltas)
        if tournament_config is not None:
            marginal_delta = marginal_delta_by_gate.get(gate)
        else:
            marginal_delta = n_eligible_without_gate - n_eligible_with_all
        n_evaluated = len(evaluated)
        if not (0 <= n_rejections <= n_evaluated):
            raise AssertionError(
                f"gate_inventory_table (#1003/#1155): Ordnungs-Invariante verletzt fuer Gate "
                f"'{gate}': 0 <= n_rejections({n_rejections}) <= n_evaluated({n_evaluated}) "
                f"nicht erfuellt."
            )
        # Issue #1006/#1158-Regression (entdeckt bei test_issue_776/#833/#743/#1086) — die
        # ``n_solo <= n_rejections``-Teilaussage ist NUR garantiert, wenn beide aus DERSELBEN
        # Quellenfamilie stammen (siehe n_solo-Kommentar oben): Fall A (kein
        # ``is_rejection_detail_counts``, beide delta-basiert) oder Fall B (``is_rejection_detail_
        # counts`` UND ``has_reasons_field``, beide reasons-/detail-basiert). Im gemischten Fall C
        # (``is_rejection_detail_counts`` vorhanden, aber KEIN Trial traegt ``oos_rejection_
        # reasons``) ist ``n_rejections`` weiterhin is_rejection_detail_counts-basiert (Kontrakt
        # #956/#1122 — dieser Aufrufer erwartet den korrekten Wert UNABHAENGIG vom Reasons-Feld),
        # waehrend ``n_solo`` mangels Reasons auf den delta-basierten Naeherungswert zurueckfaellt —
        # ZWEI voneinander unabhaengige Messungen (``is_rejection_detail`` vs. ``oos_gate_deltas``),
        # zwischen denen KEINE Ordnungsbeziehung erzwungen werden darf (das ist exakt der #1122-
        # Blinde-Fleck, den dieses Feld-Paar per Definition ueberbrueckt). Ein Legacy-/Testaufrufer,
        # der ``is_rejection_detail_counts`` uebergibt, aber nie ``oos_rejection_reasons`` stempelt
        # (z. B. Fixtures aus #776/#833/#743/#1086, die ausschliesslich ``oos_gate_deltas`` setzen),
        # darf deshalb NICHT als Programmfehler gewertet werden.
        _solo_comparable_to_rejections = not (
            is_rejection_detail_counts is not None and not has_reasons_field)
        if _solo_comparable_to_rejections and not (0 <= n_solo <= n_rejections):
            raise AssertionError(
                f"gate_inventory_table (#1003/#1155): Ordnungs-Invariante verletzt fuer Gate "
                f"'{gate}': 0 <= n_solo_rejections({n_solo}) <= n_rejections({n_rejections}) "
                f"nicht erfuellt."
            )
        table.append({
            "gate": gate,
            "n_rejections": n_rejections,
            "n_solo_rejections": n_solo,
            "marginal_delta": marginal_delta,
            "n_evaluated": n_evaluated,
        })
    return table


def check_gate_marginal_contribution(
    study_records: list[dict], *, min_evaluated: int = 500,
    gate_consolidation_protected: list[str] | None = None,
) -> InvariantResult:
    """Issue #970 (Katalog A, P1) — kein Gate ohne jeden marginalen Beitrag darf über eine
    ausreichend grosse Kohorte (``min_evaluated`` OOS-evaluierte Trials, aufsummiert über alle
    Studies, in denen dieses Gate konfiguriert war) in ``eligible_requires_all`` verbleiben: ein
    Gate mit ``Σ marginal_delta == 0`` über >= ``min_evaluated`` Beobachtungen erhöht nur
    Rechenaufwand und Typ-II-Fehler durch Kollinearität (siehe ``gate_inventory_table``), ohne
    jemals die Eligibility-Entscheidung zu ändern.

    ``study_records``: Liste mit ``gate_inventory`` (aus ``report._study_record``, Liste von
    ``{gate, n_rejections, n_solo_rejections, marginal_delta, n_evaluated}``-Dicts).

    Issue #1076 (Katalog #866-2, Kohorte D) — ``gate_consolidation_protected``
    (``tournament.json``, #810) wird jetzt KONSULTIERT: ein geschütztes Gate (``min_trades``/
    ``max_drawdown`` — eine strukturelle Vorbedingung bzw. eine harte Risikogrenze, #776 explizit
    behalten) erhält bei ``Σ marginal_delta == 0`` KEINE Entfernungsempfehlung, sondern eine
    Neukalibrierungs-Empfehlung (die Schwelle selbst ist vermutlich zu lax kalibriert, nicht das
    Gate selbst überflüssig — dieselbe Kategorienunterscheidung wie #811). Root-Cause #1076:
    fünf Gate-Entfernungen (#677/#697/#776/#960/#848) wurden bereits auf der Marginal-Delta-
    Evidenz dieses Checks vollzogen; ohne diese Unterscheidung würde eine sechste Empfehlung
    ``eligible_requires_all`` auf ein einziges verbleibendes Gate reduzieren, selbst wenn
    ``min_trades``/``max_drawdown`` betroffen wären.

    Issue #1003/#1155 (Katalog #1170) -- marginal_delta ist seit diesem Fix (sofern
    gate_inventory_table mit tournament_config aufgerufen wurde) eine WAHRSCHEINLICHKEIT
    (reward.gate_marginal_pass_rate_delta, [0, 1]) statt eines rohen Trial-Zaehlers -- die
    Aggregation summiert seither float statt int (ein int(...)-Cast wuerde jeden Bruchwert
    < 1.0 stumm auf 0 abschneiden und faelschlich jedes Gate als beitragslos melden).

    Issue #1033/#1182 (Katalog #866-2, Pitfall #422 in AGENTS.md) — Root-Cause: eine Study-Zeile
    mit ``marginal_delta=None`` (``n_rejections == 0`` in dieser Study — das Gate wurde dort NIE
    ausgewertet, siehe ``gate_inventory_table``-Docstring) wurde ueber ``float(entry.get(
    'marginal_delta') or 0)`` als ``0.0`` GEMESSEN behandelt, UND ihr ``n_evaluated`` floss trotzdem
    in den Nenner — "nicht gemessen" wurde so zu "gemessen und null" (dieselbe Fehlerklasse wie
    #995/#1147, hier auf der Aggregationsebene). Ein Gate, dessen Zeilen AUSSCHLIESSLICH ``None``
    tragen (z. B. ``min_trades``/``max_drawdown``, wenn sie in JEDER Study bereits an der
    Studien-eigenen ``eligible_requires_all``-Konjunktion frueh binden und daher nie selbst
    "n_rejections>0" erreichen), erschien dadurch faelschlich als "0 marginaler Beitrag ueber die
    volle Kohorte" statt als "nie gemessen".

    Fix: ``None``-Beitraege werden aus Zaehler UND Nenner ausgeschlossen; ein Gate mit weniger als
    ``min_evaluated`` TATSAECHLICH GEMESSENEN Beobachtungen (nach dem Ausschluss) erscheint als
    ``inconclusive_gates`` (mit der Herkunft — Anzahl Zeilen total/gemessen — in ``provenance``),
    NICHT als ``offenders`` ("ohne marginalen Beitrag")."""
    totals: dict[str, dict[str, float]] = {}
    for r in study_records:
        for entry in r.get("gate_inventory") or []:
            gate = entry.get("gate")
            if not gate:
                continue
            agg = totals.setdefault(gate, {
                "marginal_delta": 0.0, "n_evaluated": 0,
                "n_entries_total": 0, "n_entries_measured": 0,
            })
            agg["n_entries_total"] += 1
            marginal_delta = entry.get("marginal_delta")
            if marginal_delta is None:
                continue
            agg["n_entries_measured"] += 1
            agg["marginal_delta"] += float(marginal_delta)
            agg["n_evaluated"] += int(entry.get("n_evaluated") or 0)
    if not totals:
        return InvariantResult(
            name="check_gate_marginal_contribution",
            passed=True,
            expected=f"kein Gate mit Σ marginal_delta == 0 über >= {min_evaluated} Beobachtungen",
            actual=None,
            detail="Keine Studies mit gate_inventory-Telemetrie — nicht anwendbar.",
        )
    protected = set(gate_consolidation_protected or [])
    inconclusive_gates = {
        gate: {"n_evaluated_measured": agg["n_evaluated"],
              "n_entries_total": agg["n_entries_total"],
              "n_entries_measured": agg["n_entries_measured"]}
        for gate, agg in totals.items() if agg["n_evaluated"] < min_evaluated
    }
    offenders = {
        gate: {"marginal_delta": agg["marginal_delta"], "n_evaluated": agg["n_evaluated"]}
        for gate, agg in totals.items()
        if agg["n_evaluated"] >= min_evaluated and agg["marginal_delta"] == 0
    }
    passed = not offenders
    protected_offenders = sorted(g for g in offenders if g in protected)
    removable_offenders = sorted(g for g in offenders if g not in protected)
    detail_parts = []
    if removable_offenders:
        detail_parts.append(
            f"Kandidat(en) für Entfernung aus eligible_requires_all: {removable_offenders}.")
    if protected_offenders:
        detail_parts.append(
            f"GESCHÜTZT (gate_consolidation_protected) — Neukalibrierungs-, KEINE "
            f"Entfernungsempfehlung: {protected_offenders} (#1076).")
    if inconclusive_gates:
        detail_parts.append(
            f"INCONCLUSIVE (nie gemessen oder < {min_evaluated} gemessene Beobachtungen nach "
            f"Ausschluss von marginal_delta=None): {sorted(inconclusive_gates)} (#1033/#1182).")
    return InvariantResult(
        name="check_gate_marginal_contribution",
        passed=passed,
        expected=f"kein Gate mit Σ marginal_delta == 0 über >= {min_evaluated} GEMESSENE "
                 "Beobachtungen (marginal_delta=None ausgeschlossen)",
        actual=offenders if offenders else None,
        provenance={"inconclusive_gates": inconclusive_gates} if inconclusive_gates else None,
        detail=("OK" if (passed and not inconclusive_gates) else
                (f"{len(offenders)} Gate(s) ohne jeden marginalen Beitrag über eine ausreichend "
                 f"grosse Kohorte: {offenders} — " if offenders else "") + " ".join(detail_parts)),
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
        # Issue #1089 (Katalog #922) Fix Punkt 3 — nach dem #1086-Kohorten-Fix ist ``study_records``
        # bereits strukturell einlaeufig; ``n_studies`` macht die Grundgesamtheit hinter dem Median
        # trotzdem NACHVOLLZIEHBAR (bit-identisch bleibendes ``actual`` fuer bestehende Konsumenten,
        # siehe test_issue_770_budget_execution.py).
        provenance={"n_studies": len(fractions)},
        detail=("OK" if passed else
                f"median(budget_executed_fraction)={median:.4f} < {min_median} ueber "
                f"{len(fractions)} Studies — ein grosser Teil des konfigurierten Suchbudgets wird "
                "nicht ausgefuehrt (#768/#769-Fehlerklasse)."),
    )


def assert_invariant_scope_uncontaminated(study_records: list[dict]) -> None:
    """Issue #1088 (Katalog #921) — die vom Issue beschriebene ``invariants.run_all``-Vorab-Pruefung:
    bricht MIT ``INVARIANT_SCOPE_CONTAMINATED`` ab, bevor irgendein ``check_*`` unten ein Urteil auf
    ``study_records`` faellt, falls diese NACHWEISLICH mehr als eine ``run_id`` tragen.

    Symptom #1088: ``check_sizing_identity_coherence``/``check_holding_time_cap`` FAILten auf
    Fremd-Studies (Combo/ASML, DynBreakout/TSLA, ...) eines NVDA-Laufs — keine davon war eine
    NVDA-Study. Root-Cause: Folge von #1086 (kontaminierter Report, siehe ``report._build_report``s
    ``run_id``-Filter) — die Invariantensuite urteilte auf ``study_records``, die aus MEHREREN
    Laeufen stammten, und ``n_checks`` skalierte entsprechend (319 bei 14 Studies, 1159 bei 56).

    Der #1086-Fix ist bereits NOTWENDIG UND HINREICHEND (mit sauberer Kohorte enthaelt
    ``studies_out`` automatisch nur die eigene ``run_id``) — dieser Guard ist die ZUSAETZLICHE
    Sicherung fuer jeden Aufrufer, der ``study_records`` NICHT ueber ``report._build_report``s
    Filter bezieht (z. B. ein zukuenftiger Direktaufruf der Invarianten-Suite). Jeder Record OHNE
    ``run_id``-Feld (Legacy-Pfad, #1086-Zeitfenster-Fallback) wird ignoriert — fail-open auf
    fehlender Evidenz, analog jedem anderen ``None``-Fall in diesem Modul; nur ZWEI VERSCHIEDENE,
    tatsaechlich GESETZTE ``run_id``-Werte gelten als Kontamination."""
    distinct_run_ids = sorted({
        r.get("run_id") for r in study_records if r.get("run_id")
    })
    if len(distinct_run_ids) > 1:
        offenders = [
            f"{r.get('strategy')}/{r.get('symbol')}={r.get('run_id')}"
            for r in study_records if r.get("run_id")
        ]
        raise RuntimeError(
            f"[INVARIANT_SCOPE_CONTAMINATED] study_records tragen {len(distinct_run_ids)} "
            f"verschiedene run_id-Werte ({distinct_run_ids!r}) — die Invarianten-Suite urteilt "
            "NICHT auf einer vermischten Kohorte. Betroffene Studies: " + ", ".join(offenders)
        )


def check_loss_metric_commensurability(study_records: list[dict]) -> InvariantResult:
    """Issue #1097 (Katalog #930) — prüft die Teilmengen-Schranke, die für nicht-negative
    Verluste ZWINGEND gilt: TRAILING_STOP-Verluste sind eine TEILMENGE aller Verlust-Trades
    (jeder TRAILING_STOP-Exit mit ``pnl < 0`` zählt in BEIDEN Zählern, siehe
    ``backtest_runner._aggregate_exit_telemetry``) — die Verlustsumme der Teilmenge kann die
    Verlustsumme der Gesamtmenge nie übersteigen:

        Σ(alle Verluste) >= Σ(Stop-Verluste)
        ⟺ oos_gross_loss_mean_bps_pooled · oos_n_losses
              >= oos_gross_loss_mean_bps_trailing_stop_pooled · oos_n_trailing_stop_losses

    Root-Cause #1097: diese Schranke war in 12 von 56 Studies verletzt (Faktor bis 3,57), weil
    ``report.py`` beide Seiten bislang aus INKOMMENSURABLEN Aggregationen bildete (Median der
    Trial-Mittelwerte links, Summe der Trial-Zähler rechts, Pitfall #304) — keine reale
    ökonomische Inkohärenz der Simulation, sondern ein Messartefakt. Mit den gepoolten Feldern
    (``report._pooled_mean_of_trial_field``, beide Seiten trade-gewichtet über dieselbe
    Trial-Kohorte) ist die Schranke eine mathematische Tautologie — ein FAIL hier bedeutet, dass
    mindestens eine der beiden Zählungen selbst fehlerhaft ist (z. B. eine zukünftige Regression
    in der Order-Tag-Klassifikation), nicht eine Kalibrierungsfrage.

    Issue #1024/#1173 (Katalog #866-2, Pitfall #423) — zusätzliche, unabhängige Verfügbarkeits-
    Kommensurabilität für das (Median/Median-)Paar aus ``check_trailing_stop_loss_share``:
    ``gross_loss_median_bps`` (der Nenner) muss verfügbar sein, wann immer
    ``gross_loss_median_bps_trailing_stop`` (der Zähler) verfügbar ist UND es tatsächlich
    Verlust-Trades ausserhalb der Trailing-Stop-Teilmenge gibt (``oos_n_losses >
    oos_n_trailing_stop_losses``) — sonst wiederholt eine künftige Änderung unbemerkt exakt die
    #1024-Fehlerklasse (der Zähler wurde 2021 robust gemacht, der Nenner blieb Jahre zurück)."""
    offenders: dict[str, dict] = {}
    n_measured = 0
    for r in study_records:
        label = f"{r.get('strategy')}/{r.get('symbol')}"
        n_all = r.get("oos_n_losses")
        n_stop = r.get("oos_n_trailing_stop_losses")
        mean_all = r.get("oos_gross_loss_mean_bps_pooled")
        mean_stop = r.get("oos_gross_loss_mean_bps_trailing_stop_pooled")
        if mean_all is not None and mean_stop is not None and n_all and n_stop:
            n_measured += 1
            sum_all = float(mean_all) * float(n_all)
            sum_stop = float(mean_stop) * float(n_stop)
            # Kleine Fliesskomma-Toleranz (relative 1e-6) statt einer strikten Ungleichung.
            if sum_all < sum_stop * (1.0 - 1e-6):
                offenders.setdefault(label, {}).update({
                    "sum_all_losses_bps": round(sum_all, 4),
                    "sum_trailing_stop_losses_bps": round(sum_stop, 4),
                })
        # Issue #1024/#1173 — Nenner-Verfuegbarkeit fuer das Median/Median-Paar, UNABHAENGIG von
        # der Verfuegbarkeit der gepoolten Mean-Felder oben (zwei getrennte Nachweise, derselbe
        # Kommensurabilitaets-Zweck).
        median_ts = r.get("gross_loss_median_bps_trailing_stop")
        median_all = r.get("gross_loss_median_bps")
        if median_ts is not None and median_all is None and n_all and n_stop and n_all > n_stop:
            offenders.setdefault(label, {})["median_loss_denominator_missing"] = True
    passed = not offenders
    return InvariantResult(
        name="check_loss_metric_commensurability",
        passed=passed,
        expected=("Σ(alle Verluste) >= Σ(Stop-Verluste) je Study (Teilmengen-Schranke) UND "
                  "gross_loss_median_bps verfuegbar, wann immer gross_loss_median_bps_trailing_"
                  "stop es ist"),
        actual=offenders or None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} von {n_measured} gemessenen Studies verletzen die "
                "Teilmengen-Schranke oder die Nenner-Verfuegbarkeit fuer das Median/Median-Paar "
                "— mindestens eine der beiden Zaehlungen ist inkonsistent (#1097/#1024-"
                "Fehlerklasse, Pitfall #304/#423)."),
    )


def check_trailing_stop_loss_share(
    study_records: list[dict], *,
    max_loss_share: float = 0.60, max_median_loss_ratio: float = 1.25,
) -> InvariantResult:
    """Issue #1093 (Katalog #926) — Kalibrierungswaechter fuer die #1092/#1094-Fixes: der
    Trailing-Stop ist im Referenzlauf ueber 1.084.300 Round-Trips der HAEUFIGSTE (43,74 % aller
    Ausgaenge), verlustreichste (Median-Verlustquote 83,6 % ueber 54 Studies) und teuerste
    (mittlerer Verlust 2,26x des Durchschnitts aller Ausgaenge) Ausgang — die Signatur eines
    Stops, der auf einem Docht ratscht statt eine Verlustobergrenze durchzusetzen (#1092), und
    der bei fallender Volatilitaet degeneriert nachgibt statt zu ratschen (#1094).

    FAIL (severity ``blocking``) je Study, wenn EINE der beiden Bedingungen verletzt ist:
      1. ``n_trailing_stop_losses / n_trailing_stop_exits > max_loss_share`` (Default 0.60)
      2. ``median_loss_trailing_stop / median_loss_all > max_median_loss_ratio`` (Default 1.25)

    Beide Schwellen sind ``optimizer.json``-Keys (``trailing_stop_max_loss_share``/
    ``trailing_stop_max_median_loss_ratio``, Pitfall #369 — zweiseitig dokumentiert). Studies ohne
    Trailing-Stop-Exit-Telemetrie (Pre-#899-JSON, kein Trade) werden uebersprungen (fail-open auf
    fehlender Evidenz).

    Issue #972/#1126 (Pitfall #405 in AGENTS.md) — der Zaehler von Bedingung 2 ist seit diesem Fix
    ``gross_loss_median_bps_trailing_stop`` (robuster Median-der-Trial-Mediane) statt des
    ungeschuetzten ``oos_gross_loss_mean_bps_trailing_stop``.

    Issue #1024/#1173 (Katalog #866-2, Pitfall #423 in AGENTS.md) — der NENNER war bis zu diesem
    Fix ``oos_gross_loss_mean_bps`` (ALLE Verlust-Trades, ungeschuetztes Mittel) geblieben, der
    #1126-Fix hatte ihn explizit als "ausserhalb des Scopes" dokumentiert, OHNE die Schwelle mit
    umzuziehen — zwei verschiedene Momente (Median-Zaehler / Mittel-Nenner) in einem Quotienten,
    dessen Kalibrierungsnachweis (2,26x im Median, mean/mean gemessen) fuer die entstandene
    Median/Mittel-Skala nicht mehr gilt. Fix: der Nenner ist jetzt ``gross_loss_median_bps``
    (Median ALLER Verlust-Round-Trips, robustes Gegenstueck zu ``oos_gross_loss_mean_bps`` — siehe
    ``backtest_runner.extract_metrics``) — Zaehler UND Nenner tragen seither dieselbe Statistik.
    Die Schwelle (``trailing_stop_max_median_loss_ratio``, umbenannt von
    ``trailing_stop_max_mean_loss_ratio``) ist der ALTE, unter mean/mean kalibrierte Zahlenwert,
    NICHT neu kalibriert (Pitfall #423: das waere eine stille Aufweichung, keine Korrektur) — bis
    ein Folgelauf sie auf der neuen Skala verifiziert.

    Issue #983/#1137 — solange dieser Check in 10/10 Läufen auf 100 % der Grundgesamtheit failt, ist
    seine Schwelle NICHT kalibriert: die #1126-Umstellung auf den robusten Zaehler ist die
    Vorbedingung fuer eine Neukalibrierung (nicht Teil dieses Fixes, siehe AGENTS.md).

    Issue #996/#1148 (Katalog #1170) — Root-Cause: Bedingung 2 wurde in 27 von 28 Studies **0×**
    ausgewertet, weil ``if median_loss_ts is not None and median_loss_all:`` sie kommentarlos
    UEBERSPRINGT, sobald der (seit #972/#1126 median-basierte) Zaehler fehlt — der Check failte
    in diesen Laeufen ausschliesslich ueber Bedingung 1, ohne dass ein Konsument das von "beide
    Bedingungen wurden geprueft, nur Bedingung 1 verletzt" unterscheiden konnte. Fix: jede Study
    telemetriert ``conditions_evaluated`` (welche der beiden Bedingungen ueberhaupt eine
    Eingangsgroesse hatte); fehlt der Zaehler von Bedingung 2 in MEHR ALS 50 % der Kandidaten-
    Studies (``n_ts_exits`` vorhanden), ist das GESAMTERGEBNIS ``evaluable=False``/``passed=None``
    (dieselbe #995/#1147-Tri-State-Mechanik, hier auf severity='blocking' bereits vorhanden) statt
    eines PASS/FAIL allein auf Bedingung 1. Der Detail-Text nennt seither je Offender explizit,
    welche Bedingung(en) tatsaechlich feuern (``actual`` trug das bereits, der Fliesstext nicht)."""
    offenders: dict[str, dict] = {}
    conditions_evaluated_by_study: dict[str, list[str]] = {}
    n_candidates = 0
    n_condition2_evaluated = 0
    for r in study_records:
        label = f"{r.get('strategy')}/{r.get('symbol')}"
        n_ts_exits = (r.get("exit_reason_histogram") or {}).get("TRAILING_STOP", 0)
        n_ts_losses = r.get("oos_n_trailing_stop_losses")
        if not n_ts_exits or n_ts_losses is None:
            continue
        n_candidates += 1
        conditions_evaluated = ["loss_share"]
        loss_share = n_ts_losses / n_ts_exits
        violation = {}
        if loss_share > max_loss_share:
            violation["loss_share"] = round(loss_share, 4)
        median_loss_ts = r.get("gross_loss_median_bps_trailing_stop")
        # Issue #1024/#1173 — robuster Nenner (Median aller Verlust-Round-Trips), Gegenstueck zum
        # bereits robusten Zaehler oben; ersetzt das ungeschuetzte oos_gross_loss_mean_bps.
        median_loss_all = r.get("gross_loss_median_bps")
        if median_loss_ts is not None and median_loss_all:
            n_condition2_evaluated += 1
            conditions_evaluated.append("median_loss_ratio")
            median_loss_ratio = median_loss_ts / median_loss_all
            if median_loss_ratio > max_median_loss_ratio:
                violation["median_loss_ratio"] = round(median_loss_ratio, 4)
        conditions_evaluated_by_study[label] = conditions_evaluated
        if violation:
            offenders[label] = {
                "n_trailing_stop_exits": n_ts_exits, "n_trailing_stop_losses": n_ts_losses,
                "conditions_evaluated": conditions_evaluated,
                "conditions_violated": sorted(violation),
                **violation,
            }
    passed = not offenders
    # Issue #996/#1148 — Bedingung 2 in > 50 % der Kandidaten-Studies nicht auswertbar ⇒ das
    # GESAMTERGEBNIS ist selbst nicht auswertbar, kein PASS/FAIL allein auf Bedingung 1.
    condition2_missing_fraction = (
        1.0 - (n_condition2_evaluated / n_candidates) if n_candidates else 0.0)
    evaluable = not (n_candidates > 0 and condition2_missing_fraction > 0.5)
    if not evaluable:
        n_loss_share_only = sum(
            1 for o in offenders.values() if o["conditions_violated"] == ["loss_share"])
        detail = (
            f"Bedingung 2 (median_loss_ratio) ist in {n_candidates - n_condition2_evaluated} von "
            f"{n_candidates} Kandidaten-Studies nicht auswertbar (Zaehler fehlt) — "
            f"{n_loss_share_only} der {len(offenders)} Offender wurden AUSSCHLIESSLICH ueber "
            "Bedingung 1 (loss_share) ermittelt; das Gesamtergebnis ist evaluable=False statt "
            "eines Urteils allein auf der unvollstaendig geprueften Bedingungsmenge.")
    elif passed:
        detail = "OK"
    else:
        n_loss_share_only = sum(
            1 for o in offenders.values() if o["conditions_violated"] == ["loss_share"])
        n_median_loss_only = sum(
            1 for o in offenders.values() if o["conditions_violated"] == ["median_loss_ratio"])
        n_both = len(offenders) - n_loss_share_only - n_median_loss_only
        detail = (
            f"{len(offenders)} Study/Studies mit einer Trailing-Stop-Verlustquote/-groesse "
            "ausserhalb der Kalibrierungsschwelle (#1092/#1094-Fehlerklasse: der Stop ratscht "
            f"auf einem Docht statt eine Verlustobergrenze durchzusetzen) — davon {n_loss_share_only} "
            f"ausschliesslich ueber Bedingung 1 (loss_share), {n_median_loss_only} ausschliesslich "
            f"ueber Bedingung 2 (median_loss_ratio), {n_both} ueber beide Bedingungen.")
    return InvariantResult(
        name="check_trailing_stop_loss_share",
        passed=(passed if evaluable else None),
        expected=(f"n_trailing_stop_losses/n_trailing_stop_exits <= {max_loss_share} UND "
                  f"median_loss_trailing_stop/median_loss_all <= {max_median_loss_ratio} je Study"),
        actual=offenders or None,
        severity="blocking",
        detail=detail,
        evaluable=evaluable,
        inconclusive=(not evaluable),
        evaluability={
            "evaluable": evaluable,
            "inconclusive_reason": (
                None if evaluable else "condition2_median_loss_ratio_numerator_missing_majority"),
            "n_candidates": n_candidates, "n_measured": n_condition2_evaluated,
        },
        provenance={"conditions_evaluated_by_study": conditions_evaluated_by_study} if conditions_evaluated_by_study else None,
    )


def check_family_n_stability(
    frozen_by_symbol: dict[str, int], observed_by_symbol: dict[str, int],
) -> InvariantResult:
    """Issue #1091 (Katalog #924) — vergleicht die EINGEFRORENE (budget-basierte,
    ``sweep._family_n_frozen_from_studies``) gegen die zur BERICHTSZEIT beobachtete
    (Issue #1102/Katalog #935 — seither ``report._family_n_stages``s eigene
    ``n_family_stage1``-Summe, nicht mehr das separat berechnete, veraltete
    ``sweep._family_n_from_proposals``) familienweite Multiplizität je Symbol.

    Root-Cause #1091: ``observed_at_report_time`` haengt davon ab, wie viele Proposals einer
    Symbol-Familie zum Lesezeitpunkt bereits exportiert wurden — ein Zwischenreport (Progress-
    Probe, Fail-Fast-Probe, #1083) sieht strukturell eine TEILMENGE und meldet eine kleinere Zahl
    als der finale Report derselben Familie. ``frozen`` ist ab Symbol-Dispatch-Beginn bekannt und
    bit-identisch ueber jeden Lesezeitpunkt.

    Issue #979/#1133 (Katalog #986) — dieser Check ist die ABNAHMEMESSUNG fuer #977/#1131 (die
    Umstellung von ``deflation_n_family_frozen`` auf die per-Strategie-Quelle, siehe sweep._
    run_confirm_and_export-Docstring): VOR #1131 haette ``blocking`` 8 von 8 Läufen blockiert (die
    eingefrorene, symbolweite Zahl widersprach der beobachteten per-Strategie-Summe strukturell,
    nicht nur bei einer echten Kohorten-Luecke) — die Hochstufung war deshalb an #1131 GEBUNDEN,
    nicht unabhaengig moeglich.

    Issue #1006/#1158 (Katalog #1170) — Root-Cause: eine 5 %-Toleranz-Schwelle UND ein ``frozen <=
    0: continue``-Skip verdeckten genau das Symptom, das dieser Check aufdecken soll — eine
    ``excluded_degenerate``-Study (#981/#1135) trug frozen=0/observed=1 (NVDA/SqueezeBreakout);
    der Skip liess ``frozen <= 0`` NIE auswerten, und selbst ohne den Skip haette die 5 %-Toleranz
    kleine, aber ECHTE Ein-Study-Abweichungen bei einer grossen Familie verschluckt. Seit ``sweep.
    _family_members``/``report._family_n_stages`` dieselbe Ausschluss-Semantik teilen (#1158-Fix),
    ist ``frozen == observed`` tautologisch garantiert — jede Abweichung, gleich wie klein, ist ein
    echter Befund (Zwischenreport ODER eine erneute Filter-Divergenz), kein Rundungsartefakt mehr.
    FAIL (severity ``blocking``) bei JEDER Differenz != 0, keine Toleranzschwelle mehr."""
    offenders: dict[str, dict] = {}
    for symbol, frozen in frozen_by_symbol.items():
        observed = observed_by_symbol.get(symbol)
        if observed is None:
            continue
        if frozen != observed:
            offenders[symbol] = {"frozen": frozen, "observed_at_report_time": observed,
                                 "difference": observed - frozen}
    passed = not offenders
    return InvariantResult(
        name="check_family_n_stability",
        passed=passed,
        expected="frozen == observed_at_report_time (exakt, seit #1006/#1158)",
        actual=offenders or None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Symbol(e) mit Abweichung zwischen eingefrorener und "
                "beobachteter Familien-Multiplizitaet — Zwischenreport oder erneute #1158-"
                "Filter-Divergenz."),
    )


def check_event_stream_completeness(
    expected_trial_events: int | None, actual_trial_events: int,
    expected_study_events: int | None, actual_study_events: int,
    *, tolerance: int = 0,
) -> InvariantResult:
    """Issue #1098 (Katalog #931) — vergleicht das vom Sweep-Report am Laufende geschriebene
    ``EVENTS_MANIFEST``-Ereignis (``expected_trial_events``/``expected_study_events``, aus
    ``Σ n_trials_completed``/``len(studies_out)`` — UNABHAENGIG von ``events.jsonl`` selbst
    berechnet, siehe ``report._build_report``) gegen die dort TATSAECHLICH gezaehlten
    ``optimizer_trial_completed``/``optimizer_study_completed``-Zeilen
    (``report._count_jsonl_events``).

    Root-Cause #1098: ungepufferte, nicht-atomare Zeilen-Appends aus mehreren gleichzeitigen
    Sweep-Worker-Threads (``ThreadPoolExecutor``, #400) liessen ``events.jsonl`` systematisch
    Ereignisse verlieren — beobachtet als Δ zwischen Ereigniszahl und ``Σ n_trials_completed``
    (ASML 1814/1814 Δ 0, NVDA 1940/1940 Δ 0, PLTR 1920/1926 Δ −6, TSLA 1910/1924 Δ −14; korreliert
    exakt mit dem ``INFERENCE_DIAGNOSTIC``-Volumen; in 12 von 14 TSLA-Studies fehlte genau EIN
    Ereignis). Der #1098-Fix in ``log_manager._append_jsonl_sidecar`` (ein atomarer ``os.write()``
    je Zeile statt zweier getrennter, interleaving-anfaelliger ``write()``-Aufrufe) behebt die
    Ursache; dieser Wächter macht ein Wiederauftreten (z. B. durch eine kuenftige Regression, die
    erneut auf gepuffertes ``open(..., 'a')`` zurueckfaellt) SICHTBAR statt eines stillen
    Datenverlusts.

    ``expected_trial_events is None`` oder ``expected_study_events is None`` (kein EVENTS_MANIFEST-
    Ereignis vorhanden — z. B. Zwischen-/Probe-Report vor #1083, oder ein Report ausserhalb eines
    echten Sweep-Laufs) ⇒ ``inconclusive=True`` (kein Urteil ohne Vergleichsgrundlage, kein
    stiller FAIL). severity ``high`` (Diagnose — kein Promotion-Gate, im Gegensatz zu
    ``check_loss_metric_commensurability``/``check_trailing_stop_loss_share``)."""
    if expected_trial_events is None or expected_study_events is None:
        return InvariantResult(
            name="check_event_stream_completeness",
            passed=True, inconclusive=True,
            expected="actual_trial_events == expected_trial_events UND actual_study_events == "
                     "expected_study_events (aus EVENTS_MANIFEST)",
            actual=None, severity="high",
            detail="Kein EVENTS_MANIFEST-Ereignis vorhanden — kein Urteil ohne Vergleichsgrundlage "
                   "(Zwischen-/Probe-Report oder Report ausserhalb eines Sweep-Laufs).",
        )
    delta_trial = actual_trial_events - expected_trial_events
    delta_study = actual_study_events - expected_study_events
    passed = abs(delta_trial) <= tolerance and abs(delta_study) <= tolerance
    return InvariantResult(
        name="check_event_stream_completeness",
        passed=passed,
        expected=f"delta_trial_events == 0 UND delta_study_events == 0 (Toleranz {tolerance})",
        actual={
            "expected_trial_events": expected_trial_events,
            "actual_trial_events": actual_trial_events,
            "delta_trial_events": delta_trial,
            "expected_study_events": expected_study_events,
            "actual_study_events": actual_study_events,
            "delta_study_events": delta_study,
        },
        severity="high",
        detail=("OK" if passed else
                f"events.jsonl weicht um {delta_trial} Trial- und {delta_study} Study-Ereignis(se) "
                "vom EVENTS_MANIFEST ab (#1098-Fehlerklasse: nicht-atomarer Zeilen-Append unter "
                "Nebenlaeufigkeit)."),
    )


def check_commit_coherence(
    git_commit_simulation: str | None, git_commit_report: str | None,
) -> InvariantResult:
    """Issue #1104 (Katalog #937) — ``git_commit_simulation`` (der Commit, auf dem die TRIALS
    tatsächlich liefen, vor dem ersten Trial jeder Study gestempelt, siehe run_optimization.py)
    muss mit ``git_commit_report`` (der Commit, auf dem DIESER Report gebaut wurde, zur
    Berichtszeit gelesen) übereinstimmen.

    Root-Cause #1104: eine EINZIGE ``git_commit()``-Lesung zur Berichtszeit trug bislang beide
    Bedeutungen unter demselben Feldnamen — ein Report, der NACHTRÄGLICH (``--report-only``,
    ``generate_report_for_run``) auf einem NEUEREN Checkout regeneriert wird, hatte dadurch einen
    ``git_commit``, der NICHT dem Commit entsprach, der die Trials tatsächlich erzeugt hat
    (Referenzsymptom: ``run_3910e12b_…json`` mit ``run_id``-Präfix ``3910e12b``, aber
    ``git_commit = 'b48024c4'``) — die ``invariant_checks`` DIESES Reports werden dann gegen eine
    ANDERE Codeversion ausgewertet als die, die die Trials produzierte, ohne dass das Artefakt
    selbst diese Diskrepanz auswies.

    FAIL (severity ``high`` — Provenienz-/Nachvollziehbarkeitswächter, kein Promotion-Gate), wenn
    BEIDE Commits bekannt sind UND divergieren. ``git_commit_simulation is None`` (Legacy-Study vor
    #1104, trug den Stempel noch nicht) ⇒ ``inconclusive=True`` (kein Urteil ohne
    Vergleichsgrundlage, kein stiller FAIL)."""
    if git_commit_simulation is None or git_commit_report is None:
        return InvariantResult(
            name="check_commit_coherence",
            passed=True, inconclusive=True,
            expected="git_commit_simulation == git_commit_report",
            actual=None, severity="high",
            detail="git_commit_simulation fehlt (Legacy-Study vor #1104) — kein Urteil ohne "
                   "Vergleichsgrundlage.",
        )
    passed = git_commit_simulation == git_commit_report
    return InvariantResult(
        name="check_commit_coherence",
        passed=passed,
        expected="git_commit_simulation == git_commit_report",
        actual={"git_commit_simulation": git_commit_simulation,
               "git_commit_report": git_commit_report},
        severity="high",
        detail=("OK" if passed else
                f"git_commit_simulation ({git_commit_simulation}) != git_commit_report "
                f"({git_commit_report}) — dieser Report wurde auf einer ANDEREN Codeversion "
                "gebaut als die, die die Trials simulierte (#1104-Fehlerklasse: nachträgliche "
                "Regenerierung auf einem neueren Checkout)."),
    )


def check_report_cohort_coherence(
    study_records: list[dict], *, run_id: str | None = None,
) -> InvariantResult:
    """Issue #940/#1106 (Katalog #960, HEADLINE) — urteilt seit diesem Fix ausschliesslich ueber
    KOHORTEN-IDENTITAET, nicht mehr ueber Zeit.

    Root-Cause (#1106, Beweis B-3): die Vorgaenger-Fassung dieser Funktion (Issue #1087, jetzt
    ``check_cohort_clock_drift``) bildete drei ZEITLICHE ENTHALTUNGSTESTS — sie pruefen, ob eine
    Study zeitlich AUSSERHALB des Laufsfensters liegt. Ein Nachbarlauf, der NACH dem Referenzlauf
    startet und VOR ihm endet, liegt vollstaendig INNERHALB jedes Laufsfensters und ist damit
    STRUKTURELL nicht detektierbar — unabhaengig von jeder Schwellenwahl. Report A (15 von 29
    Studies fremd, 51,7 %) bestand alle drei Zeitklauseln: ``earliest_offset_s=-4,0``,
    ``latest_overrun_s=-195,6``, Spannweite 199,9s gegen Schwelle 3141,0s. Ein gestaffelter
    Batch-Start (der reale Betriebsfall, mehrere ``sweep.py``-Prozesse kurz nacheinander gestartet)
    erzeugt genau diese Konstellation fuer den zuerst gestarteten Lauf.

    Verschaerfend (Pitfall #395, dritte Instanz nach #1043/#1070): ``study_records`` ist die
    bereits ``run_id``-gefilterte ``studies_out``-Liste (#1023/#1086-Fix in
    ``report._build_report``) — eine Pruefung, die AUF der Ausgabe des primaeren Filters arbeitet,
    kann dessen Versagen nicht unabhaengig entdecken, egal wie sie selbst misst.

    Fix: die Klausel ist jetzt Identitaet statt Zeit. ``record["run_id"]`` wird seit #1088
    gestempelt (``run_id if _own_run_trials else None``, siehe ``report._build_report``) — jede
    Study im Report muss ``record["run_id"] == run_id`` (die run_id DIESES Reports) tragen. Ein
    ``None`` (keine ``run_id``-Evidenz auf IRGENDEINEM Trial dieser Study — die Study wurde nur
    ueber den Zeitfenster-Fallback aus #1023 durchgelassen) ist ein BLOCKIERENDER Befund, kein
    fail-open: genau dieser Fallback ist der Mechanismus, der in B-3 versagte.

    Die ehemaligen drei Zeitklauseln sind KEINE Verteidigungslinie gegen Kohortenmischung mehr —
    siehe ``check_cohort_clock_drift`` (severity ``low``, reine Uhr-Drift-Diagnose, NICHT blocking).
    Eine ECHTE, von dieser Funktion unabhaengige zweite Verteidigungslinie auf einer ANDEREN
    Evidenzachse ist ``check_report_cohort_event_stream_coherence`` (Abgleich gegen den eigenen
    ``optimizer_study_completed``-Ereignisstrom statt gegen Trial-``user_attrs``).

    ``run_id=None`` (Aufrufer ohne Lauf-Kontext, z. B. ein Alt-Test) macht die Identitaetspruefung
    nicht anwendbar (``passed=True``) — der einzige explizite fail-open-Fall."""
    if run_id is None:
        return InvariantResult(
            name="check_report_cohort_coherence",
            passed=True,
            expected="record['run_id'] == run_id (die run_id dieses Reports) fuer jede Study",
            actual=None,
            detail="run_id nicht uebergeben — Identitaetspruefung nicht anwendbar.",
            severity="blocking",
        )
    violating_studies = [
        f"{r.get('strategy')}/{r.get('symbol')}" for r in study_records
        if r.get("run_id") != run_id
    ]
    passed = not violating_studies
    # Issue #971 Fix Punkt 2 (Pitfall #303) — Herkunftspflicht fuer blockierende Invarianten: die
    # Namen der verletzenden Studies muessen im selben Event mitgeliefert werden.
    provenance = {"violating_studies": violating_studies} if violating_studies else None
    return InvariantResult(
        name="check_report_cohort_coherence",
        passed=passed,
        expected="record['run_id'] == run_id fuer JEDE Study im Report",
        actual={"run_id": run_id, "n_studies": len(study_records),
                "n_violating": len(violating_studies)},
        detail=("OK" if passed else
                f"{len(violating_studies)} von {len(study_records)} Studies tragen KEINE "
                f"run_id-Evidenz fuer diesen Lauf ({run_id!r}) — Identitaet, nicht Zeit, "
                "entscheidet Kohortenzugehoerigkeit (#940/#1106): " + "; ".join(violating_studies)),
        severity="blocking",
        provenance=provenance,
    )


def check_cohort_clock_drift(
    study_records: list[dict], *, wallclock_s: float | None,
    run_started_at_utc: str | None = None,
    tolerance_s: float = 300.0,
    cohort_slack_s: float = 60.0,
) -> InvariantResult:
    """Issue #940/#1106 (Katalog #960) — die ehemalige (#1023/#1087) Zeit-basierte Fassung von
    ``check_report_cohort_coherence``, ab #1106 auf reine UHR-DRIFT-DIAGNOSE herabgestuft
    (``severity: low``) und AUSDRUECKLICH KEINE Verteidigungslinie gegen Kohortenmischung mehr —
    siehe ``check_report_cohort_coherence``-Docstring fuer die Root-Cause dieser Herabstufung
    (alle drei Klauseln sind Enthaltungstests, strukturell blind fuer einen zeitlich vollstaendig
    enthaltenen Nachbarlauf, B-3).

    Ein FAIL hier ist ein Hinweis auf Uhr-Drift zwischen Prozessen oder einen ungewoehnlich
    langsamen/schnellen Lauf — nuetzliche Betriebsdiagnose, aber weder Beleg fuer noch gegen
    Kohortenmischung. Klauseln unveraendert gegenueber #1087:

        min(study_started_at_utc) >= run_started_at_utc - cohort_slack_s   (Default 60s)
        max(study_ended_at_utc)   <= run_started_at_utc + wallclock_s + cohort_slack_s
        max(study_started) − min(study_started) < wallclock_s + tolerance_s   (Alt-Spannweite)

    ``actual`` traegt beide Offsets (``earliest_offset_s``/``latest_overrun_s``, positiv bei
    Verletzung) plus die Spannweite und die Namen der auffaelligen Studies."""
    started_timestamps: list[tuple[str, datetime]] = []
    ended_timestamps: list[tuple[str, datetime]] = []
    for r in study_records:
        label = f"{r.get('strategy')}/{r.get('symbol')}"
        raw_started = r.get("study_started_at_utc")
        if raw_started:
            try:
                started_timestamps.append((label, datetime.fromisoformat(raw_started)))
            except (TypeError, ValueError):
                pass
        raw_ended = r.get("study_ended_at_utc")
        if raw_ended:
            try:
                ended_timestamps.append((label, datetime.fromisoformat(raw_ended)))
            except (TypeError, ValueError):
                pass

    run_started_dt: datetime | None = None
    if run_started_at_utc:
        try:
            run_started_dt = datetime.fromisoformat(run_started_at_utc)
        except (TypeError, ValueError):
            run_started_dt = None

    violations: list[str] = []
    violating_studies: list[str] = []
    earliest_offset_s: float | None = None
    latest_overrun_s: float | None = None

    # Klausel 1 — kein Study-Start vor dem Laufbeginn (ueber cohort_slack_s hinaus).
    if started_timestamps and run_started_dt is not None:
        earliest_label, earliest_dt = min(started_timestamps, key=lambda t: t[1])
        earliest_offset_s = round((run_started_dt - earliest_dt).total_seconds(), 1)
        if earliest_offset_s > cohort_slack_s:
            violations.append(
                f"{earliest_label} startete {earliest_offset_s:.1f}s VOR dem Laufbeginn "
                f"(> {cohort_slack_s:.0f}s Slack)")
            violating_studies.append(earliest_label)

    # Klausel 2 — kein Study-Ende nach dem erwarteten Laufende (ueber cohort_slack_s hinaus).
    if ended_timestamps and run_started_dt is not None and wallclock_s is not None and wallclock_s > 0:
        latest_label, latest_dt = max(ended_timestamps, key=lambda t: t[1])
        expected_end_dt = run_started_dt + timedelta(seconds=wallclock_s)
        latest_overrun_s = round((latest_dt - expected_end_dt).total_seconds(), 1)
        if latest_overrun_s > cohort_slack_s:
            violations.append(
                f"{latest_label} endete {latest_overrun_s:.1f}s NACH dem erwarteten Laufende "
                f"(> {cohort_slack_s:.0f}s Slack)")
            if latest_label not in violating_studies:
                violating_studies.append(latest_label)

    # Klausel 3 (Alt-Bedingung) — Spannweite der Vereinigung gegen wallclock_s + tolerance_s.
    span_s: float | None = None
    if len(started_timestamps) >= 2 and wallclock_s is not None and wallclock_s > 0:
        _dts = [t[1] for t in started_timestamps]
        span_s = round((max(_dts) - min(_dts)).total_seconds(), 1)
        budget_s = wallclock_s + tolerance_s
        if span_s >= budget_s:
            violations.append(
                f"study_started_at_utc-Spannweite={span_s:.1f}s >= {budget_s:.1f}s")

    if earliest_offset_s is None and latest_overrun_s is None and span_s is None:
        return InvariantResult(
            name="check_cohort_clock_drift",
            passed=True,
            expected=(f"min(study_started) >= run_started - {cohort_slack_s:.0f}s UND "
                      f"max(study_ended) <= run_started + wallclock_s + {cohort_slack_s:.0f}s UND "
                      f"Spannweite < wallclock_s + {tolerance_s:.0f}s"),
            actual=None,
            detail="Keine der drei Bedingungen anwendbar (fehlende Zeitstempel/wallclock_s/run_started_at_utc).",
            severity="low",
        )
    passed = not violations
    provenance = {"violating_studies": violating_studies} if violating_studies else None
    return InvariantResult(
        name="check_cohort_clock_drift",
        passed=passed,
        expected=(f"earliest_offset_s <= {cohort_slack_s:.0f}s UND latest_overrun_s <= "
                  f"{cohort_slack_s:.0f}s UND span_s < wallclock_s + {tolerance_s:.0f}s"),
        actual={"earliest_offset_s": earliest_offset_s, "latest_overrun_s": latest_overrun_s,
                "span_s": span_s},
        detail=("OK" if passed else
                "Uhr-Drift-Diagnose (KEIN Beleg fuer/gegen Kohortenmischung, siehe "
                "check_report_cohort_coherence): mindestens eine Study liegt ausserhalb der "
                "erwarteten Sweep-Laufzeit: " + "; ".join(violations)),
        severity="low",
        provenance=provenance,
    )


def check_report_cohort_event_stream_coherence(
    study_records: list[dict], *, run_id: str | None,
    study_completed_events: list[dict] | None,
) -> InvariantResult:
    """Issue #940/#1106 Fix Punkt 3 (Katalog #960) — die ECHTE zweite, von
    ``check_report_cohort_coherence`` unabhaengige Verteidigungslinie gegen Kohortenmischung: sie
    liest eine ANDERE Evidenzachse (den forensischen ``optimizer_study_completed``-Ereignisstrom,
    #741/#1098, geschrieben von ``run_optimization.py`` UEBER ``emit_execution_event`` — die
    ``run_id`` wird dort automatisch aus der Prozess-Registry gestempelt, #780) statt der
    Trial-``user_attrs``, auf denen ``check_report_cohort_coherence`` UND der primaere
    ``run_id``-Filter in ``report._build_report`` BEIDE beruhen. B-2 zeigt: dieser Ereignisstrom
    ist lauf-sauber (genau 1 ``run_id``, 1 Symbol je Datei) — ein geeigneter unabhaengiger Zeuge.

    ``study_completed_events`` ist die UNGEFILTERTE Liste aller ``optimizer_study_completed``-
    Ereignisse, die fuer diesen Lauf-Log auffindbar sind (siehe
    ``report._read_jsonl_events(jsonl_sidecar_path(...), 'optimizer_study_completed')``) — die
    Filterung auf ``run_id`` geschieht HIER, nicht beim Aufrufer, damit ein Ereignis mit
    abweichender ``run_id`` (fremder Lauf im selben Sidecar) sichtbar bleibt statt still entfernt
    zu werden. Divergenz in EINE der beiden Richtungen (eine Study im Report ohne passendes
    Ereignis dieses Laufs, ODER ein Ereignis dieses Laufs ohne passende Study im Report) ist
    blockierend."""
    if run_id is None or study_completed_events is None:
        return InvariantResult(
            name="check_report_cohort_event_stream_coherence",
            passed=True,
            expected="{strategy}/{symbol} im Report == {strategy}/{symbol} in "
                     "optimizer_study_completed (run_id-gefiltert)",
            actual=None,
            detail="run_id oder Ereignisstrom nicht verfuegbar — nicht anwendbar.",
            severity="blocking",
        )
    own_events = [e for e in study_completed_events if e.get("run_id") == run_id]
    report_pairs = {f"{r.get('strategy')}/{r.get('symbol')}" for r in study_records}
    event_pairs = {
        f"{e.get('strategy')}/{e.get('symbol')}" for e in own_events
        if e.get("strategy") and e.get("symbol")
    }
    missing_in_events = sorted(report_pairs - event_pairs)
    missing_in_report = sorted(event_pairs - report_pairs)
    passed = not missing_in_events and not missing_in_report
    provenance = (
        {"missing_in_events": missing_in_events, "missing_in_report": missing_in_report}
        if not passed else None
    )
    return InvariantResult(
        name="check_report_cohort_event_stream_coherence",
        passed=passed,
        expected="Report-Kohorte == optimizer_study_completed-Ereignisse dieses Laufs (run_id-gefiltert)",
        actual={"run_id": run_id, "n_report": len(report_pairs), "n_events": len(event_pairs),
                "missing_in_events": missing_in_events, "missing_in_report": missing_in_report},
        detail=("OK" if passed else
                "Report-Kohorte und Ereignisstrom divergieren — unabhaengige zweite "
                f"Verteidigungslinie (#940/#1106): fehlend im Ereignisstrom={missing_in_events}, "
                f"fehlend im Report={missing_in_report}"),
        severity="blocking",
        provenance=provenance,
    )


def build_cohort_descriptor(
    study_records: list[dict], *, run_id: str | None, report_source: str,
) -> dict[str, Any]:
    """Issue #941/#1107 (Katalog #960) — die Kohorten-Deklaration, mit der
    ``report._build_report`` JEDEN Eintrag in ``invariant_checks`` stempelt, der noch kein
    ``cohort`` traegt (siehe ``InvariantResult.cohort``-Docstring). ``source`` unterscheidet die
    beiden strukturell verschiedenen Auswertungspfade aus #1107: ``'in_process'`` (der Fail-Fast-
    Pfad in ``sweep.py``, der ausschliesslich gegen die im Speicher dieses Prozesses gehaltenen
    Study-Records auswertet, per Konstruktion nur die bisher abgeschlossenen eigenen Symbole) vs.
    ``'report_scan'`` (der finale Report, ``report_source == 'final'``)."""
    return {
        "run_id": run_id,
        "n_studies": len(study_records),
        "symbols": sorted({r.get("symbol") for r in study_records if r.get("symbol")}),
        "source": "report_scan" if report_source == "final" else "in_process",
    }


def check_cohort_declaration_consistency(
    current_checks: list[dict], *, prior_probe_checks: list[dict] | None = None,
) -> InvariantResult:
    """Issue #941/#1107 (Katalog #960) — die EINZIGE Pruefung, die den Defekt aus B-2
    (``check_effective_stop_distance`` meldete 12/13/13 Offender im Fail-Fast-Pfad gegen 25/38/38
    im Report-Pfad, OHNE dass eines der beiden Artefakte die zugrunde liegende Kohorte deklarierte)
    unabhaengig von jeder Zeitheuristik findet — sie vergleicht die ``cohort``-Deklarationen
    IDENTISCHER Check-Namen desselben Laufs zwischen einer frueheren In-Process-Probe
    (``sweep.py``s Fail-Fast-Vorlauf, ``source='in_process'``) und der aktuellen, finalen Auswertung
    (``source='report_scan'``).

    Urteilsregel: eine In-Process-Probe kann per Konstruktion nur Symbole/Studies gesehen haben,
    die zum Zeitpunkt der Probe bereits abgeschlossen waren — der spaetere finale Report DESSELBEN
    Laufs (``run_id``) darf davon NIEMALS weniger sehen (Studies/Symbole koennen innerhalb eines
    Laufs nicht wieder verschwinden). Verletzt ein Check-Name diese Monotonie (ein Symbol, das die
    Probe bereits kannte, fehlt im finalen Report; oder ``n_studies`` sinkt), ist das ein struktureller
    Widerspruch — beide Zahlen koennen nicht gleichzeitig korrekte Beschreibungen DESSELBEN Laufs
    sein. ``run_id``-Abweichung zwischen den beiden Deklarationen desselben Check-Namens ist
    ebenfalls ein Widerspruch (die Probe und der finale Report muessen per Aufrufkonvention
    denselben Lauf beschreiben).

    ``prior_probe_checks=None`` (keine Fail-Fast-Probe fand in diesem Lauf statt, z. B. weil
    ``fail_fast_invariants`` leer ist oder das Laufzeit-Minimum an Symbolen nie erreicht wurde)
    macht die Pruefung nicht anwendbar."""
    if not prior_probe_checks:
        return InvariantResult(
            name="check_cohort_declaration_consistency",
            passed=True,
            expected="probe.cohort.symbols ⊆ final.cohort.symbols UND probe.n_studies <= "
                     "final.n_studies UND probe.run_id == final.run_id, je Check-Name",
            actual=None,
            detail="Keine vorherige In-Process-Probe fuer diesen Lauf verfuegbar — nicht anwendbar.",
            severity="blocking",
        )
    probe_by_name = {c.get("name"): c for c in prior_probe_checks if c.get("cohort")}
    violations: list[str] = []
    compared: list[str] = []
    for current in current_checks:
        name = current.get("name")
        probe = probe_by_name.get(name)
        current_cohort = current.get("cohort")
        if probe is None or not current_cohort:
            continue
        probe_cohort = probe["cohort"]
        compared.append(name)
        if probe_cohort.get("run_id") != current_cohort.get("run_id"):
            violations.append(
                f"{name}: probe.run_id={probe_cohort.get('run_id')!r} != "
                f"final.run_id={current_cohort.get('run_id')!r}")
            continue
        probe_symbols = set(probe_cohort.get("symbols") or [])
        final_symbols = set(current_cohort.get("symbols") or [])
        missing_symbols = sorted(probe_symbols - final_symbols)
        if missing_symbols:
            violations.append(
                f"{name}: Symbole {missing_symbols} waren der In-Process-Probe bereits bekannt, "
                "fehlen aber im finalen Report")
        probe_n = probe_cohort.get("n_studies")
        final_n = current_cohort.get("n_studies")
        if isinstance(probe_n, int) and isinstance(final_n, int) and final_n < probe_n:
            violations.append(
                f"{name}: n_studies sank von {probe_n} (Probe) auf {final_n} (final)")
    passed = not violations
    return InvariantResult(
        name="check_cohort_declaration_consistency",
        passed=passed,
        expected="probe.cohort.symbols ⊆ final.cohort.symbols UND probe.n_studies <= "
                 "final.n_studies UND probe.run_id == final.run_id, je Check-Name",
        actual={"n_check_names_compared": len(compared), "violations": violations},
        detail=("OK" if passed else
                "Kohorten-Deklarationen derselben Check-Namen widersprechen sich zwischen "
                "In-Process-Probe und finalem Report (#941/#1107): " + "; ".join(violations)),
        severity="blocking",
        provenance={"violations": violations} if violations else None,
    )


def check_expectancy_definition_coherence(
    study_records: list[dict], *, max_relative_gap: float = 0.5, min_trades: int = 10,
) -> InvariantResult:
    """Issue #1031 (Katalog #866) — ``holdout_expectancy_notional_weighted`` (Mittel von Quotienten,
    ``mean(pnl_i/notional_i)``, umbenannt #945/#1111 von ``holdout_expectancy``) und
    ``holdout_expectancy_capital_weighted`` (Σpnl/Σnotional, gegen Nennerausreisser robust) messen
    denselben zugrundeliegenden Per-Trade-Edge — eine grosse relative Luecke zwischen beiden ist die
    Signatur eines einzelnen Round-Trips mit degeneriertem Nenner (oder einer ueber eine
    Preis-Sprungstelle gehaltenen Position), der den Mittelwert der Quotienten dominiert (beobachtet:
    expectancy=0,52 vs. implizit ~0,03 auf den drei TSLA-Kandidaten des Katalogs — Faktor 16).

    Nur Studies mit ``holdout_total_trades >= min_trades`` und definierten beiden Feldern werden
    geprüft (kleine Kohorten sind statistisch zu verrauscht, um diese Diagnose sinnvoll zu tragen)."""
    with_data = [
        r for r in study_records
        if r.get("holdout_expectancy_notional_weighted") is not None
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
        expectancy = float(r["holdout_expectancy_notional_weighted"])
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


def check_session_calendar_coherence(
    study_records: list[dict], *, asset_class_by_symbol: dict[str, str],
    gated_asset_classes: frozenset[str] = frozenset({"EQUITY", "COMMODITY"}),
    max_bars_per_calendar_day: float = 8.0,
) -> InvariantResult:
    """Issue #1011/#1163 (Katalog #1170, P1) — 1h-Bars für EQUITY über einen 24/7-Kalender
    (Faktor 5,2).

    Symptom (B-7): ``n = 1079`` Regressionsperioden über 45 Kalendertage in 26/26 Studies auf zwei
    EQUITY-Symbolen ⇒ 24 Bars/Tag, 7 Tage/Woche. Erwartung für RTH-Equity: ≈ 209 (≈ 6,5
    Handelsstunden × 5 Handelstage/Woche ⇒ ≈ 4,6 Bars/Kalendertag im Mittel).

    Root-Cause: die synthetische 1h-Bar-Erzeugung kennt keine Handelszeiten-Maske für EQUITY —
    ``report.py``'s ``bars_per_calendar_day`` (siehe ``backtest_runner._bar_calendar_telemetry``)
    macht das jetzt MESSBAR, ohne die Simulation selbst zu verändern (Fix Punkt 1 dieses Issues).

    FAIL (severity ``high``), wenn für EIN Symbol mit einer ``gated_asset_classes``-Zugehörigkeit
    (Default: EQUITY/COMMODITY — RTH-Maerkte, im Gegensatz zu FOREX/CRYPTO's echten 24/7-Kalendern)
    ``bars_per_calendar_day > max_bars_per_calendar_day`` (Default 8 — grosszuegig ueber jeder
    plausiblen RTH-Session, aber weit unter der 24,0-Signatur einer 24/7-aufgefuellten Achse) —
    macht die Bar-Achse nachweislich Nicht-Handelszeit sichtbar.

    Issue Akzeptanzkriterium 2 — die Entscheidung, ob die Bar-Erzeugung auf RTH umgestellt wird,
    ist ein EIGENER Folge-Issue (aendert Annualisierung, ATR, Zeitbox und jede historische Kohorte
    gleichzeitig, erfordert einen ``simulation_semantics_version``-Bump samt Purge). Dieser Check
    STELLT NUR DIE MESSUNG HER.

    ``asset_class_by_symbol`` (Rohmaterial: ``report._asset_class_by_symbol``, Fail-open ``{}`` bei
    Importfehler) — ein Symbol ohne aufgelöste asset_class wird nicht bewertet (kein FAIL, keine
    erfundene Klassifikation)."""
    offenders: dict[str, dict] = {}
    n_evaluated = 0
    for r in study_records:
        symbol = r.get("symbol")
        bars_per_day = r.get("bars_per_calendar_day")
        if not symbol or bars_per_day is None:
            continue
        asset_class = asset_class_by_symbol.get(symbol)
        if asset_class not in gated_asset_classes:
            continue
        n_evaluated += 1
        if float(bars_per_day) > max_bars_per_calendar_day:
            key = f"{r.get('strategy')}/{symbol}"
            offenders[key] = {"asset_class": asset_class, "bars_per_calendar_day": bars_per_day}
    if n_evaluated == 0:
        return InvariantResult(
            name="check_session_calendar_coherence",
            passed=True,
            expected=f"<= {max_bars_per_calendar_day} bars_per_calendar_day für "
                     f"{sorted(gated_asset_classes)}",
            actual=None,
            severity="high",
            detail="Keine Study mit aufgelöster EQUITY/COMMODITY-asset_class und "
                   "bars_per_calendar_day — nicht anwendbar.",
        )
    passed = not offenders
    return InvariantResult(
        name="check_session_calendar_coherence",
        passed=passed,
        expected=f"<= {max_bars_per_calendar_day} bars_per_calendar_day für "
                 f"{sorted(gated_asset_classes)}",
        actual=offenders or None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)}/{n_evaluated} EQUITY/COMMODITY-Studies mit "
                f"bars_per_calendar_day > {max_bars_per_calendar_day} — die synthetische 1h-Bar-"
                "Erzeugung kennt keine Handelszeiten-Maske fuer diese Asset-Klasse (#1011/#1163)."),
        provenance={"offenders": offenders} if offenders else None,
    )


def check_summary_row_completeness(
    study_records: list[dict], *, min_exposure_for_normalization: float = 0.05,
) -> InvariantResult:
    """Issue #1013/#1165 (Katalog #1170, P2) — Study mit ``holdout_buyhold_return = null``
    verschwindet spurlos aus summary_de.py Abschnitt 2.3.

    Symptom: SqueezeBreakout/NVDA (``holdout_total_return=0.0``, ``holdout_buyhold_return=null``,
    ``holdout_total_trades=0``) erschien in Abschnitt 2.2 als „0.0 %" — ÜBER allen echten
    negativen Kandidaten — UND fehlte in Abschnitt 2.3 ganz (13 von 14 Studies gelistet, keine
    Fehlanzeige für die 14.).

    Root-Cause: Abschnitt 2.3 filtert auf ``holdout_excess_return is not None``
    (``with_benchmark``); eine Study ohne auswertbaren Holdout fällt dabei einfach aus der Menge,
    OHNE in einem "nicht gelistet"-Bucket sichtbar zu werden.

    Diese Prüfung repliziert dieselbe Drei-Wege-Partition, die ``summary_de._section_2_monetary_
    result`` Abschnitt 2.3 tatsächlich rendert (mit Benchmark + auswertbarer Exposure / mit
    Benchmark, aber Exposure < ``min_exposure_for_normalization`` / ohne Benchmark-Vergleich) und
    FAILt (severity ``high``), wenn die Summe der drei Buckets NICHT der vollen Studienmenge
    entspricht ODER ein (Strategie, Symbol)-Paar in KEINEM oder in MEHR ALS EINEM Bucket landet —
    exakt der Regressionsfall, der eine Study "spurlos verschwinden" liesse, sollte diese
    Partition künftig eine vierte, nicht abgedeckte Bedingung erhalten."""
    total = len(study_records)
    if total == 0:
        return InvariantResult(
            name="check_summary_row_completeness",
            passed=True,
            expected="Σ Zeilen über alle Abschnitt-2.3-Tabellen == Anzahl Studies",
            actual=None,
            severity="high",
            detail="Keine Studies — nicht anwendbar.",
        )
    bucket_of: dict[tuple, str] = {}
    duplicates: list[tuple] = []
    for r in study_records:
        key = (r.get("strategy"), r.get("symbol"))
        has_benchmark = r.get("holdout_excess_return") is not None
        exposure = r.get("holdout_exposure_fraction")
        if not has_benchmark:
            bucket = "without_benchmark"
        elif exposure is None or exposure < min_exposure_for_normalization:
            bucket = "not_evaluable_exposure"
        else:
            bucket = "normal"
        if key in bucket_of:
            duplicates.append(key)
        bucket_of[key] = bucket
    n_covered = len(bucket_of)
    passed = n_covered == total and not duplicates
    return InvariantResult(
        name="check_summary_row_completeness",
        passed=passed,
        expected="Σ Zeilen über alle Abschnitt-2.3-Tabellen == Anzahl Studies",
        actual={"n_studies": total, "n_covered": n_covered, "duplicates": duplicates},
        severity="high",
        detail=("OK" if passed else
                f"{total - n_covered} Study/Studies fehlen in JEDEM Abschnitt-2.3-Bucket "
                f"und/oder {len(duplicates)} (Strategie, Symbol)-Paar(e) sind mehrfach vertreten "
                "(#1013/#1165) — Abschnitt 2.3 verliert dadurch Zeilen spurlos."),
    )


def check_cost_stress_distinctness(
    study_records: list[dict], *, min_affected_fraction: float = 0.9,
) -> InvariantResult:
    """Issue #1010/#1162 (Katalog #1170, P0) — die ``'full_realism'``-Kostenstufe ist auf
    ``backtest.json['overnight_financing_bps_per_day_by_asset_class']``/``['slippage_bps_by_
    asset_class']`` = 0,0 fuer ALLE Asset-Klassen konfiguriert (siehe #987/#1141, Pitfall #412 in
    AGENTS.md — bewusst unkalibrierte Platzhalter) und damit ein reines NO-OP: ``backtest_runner.
    _full_realism_expectancy`` zieht Finanzierung UND Slippage vom Round-Trip ab, aber ``0,0 · x =
    0`` — ``holdout_expectancy_cost_stress_full_realism`` ist bit-identisch zur Basis
    (``holdout_expectancy_capital_weighted``) in praktisch JEDER Study mit Trades (B-11-Symptom:
    26/26).

    FAIL (severity ``high``), wenn ``full_realism`` in MEHR ALS ``min_affected_fraction`` (Default
    90 %) der Studies mit ``holdout_total_trades >= 1`` bit-identisch zur Basis ist — das macht den
    No-Op SICHTBAR, ohne selbst eine Kalibrierungszahl zu erfinden (Fix Punkt 3, AUSDRUECKLICH
    NICHT Teil dieses Fixes: eine Zahl > 0 ist eine Behauptung ueber die reale Kostenstruktur des
    Brokers und braucht eine Quelle, z. B. Kontoauszug/Gebuehreneuebersicht). Sobald ein Betreiber
    reale Saetze > 0 in ``backtest.json`` eintraegt, PASSt dieser Check automatisch (die Formel
    bleibt unveraendert — nur die Eingabe war/ist der fehlende Teil).

    Nur Studies mit ``holdout_total_trades >= 1`` UND beiden Feldern definiert werden gezaehlt (0
    Trades ⇒ die Stufe ist trivial identisch zur Basis, kein Symptom des No-Ops); keine solche
    Study ⇒ nicht anwendbar (PASS)."""
    with_trades = [
        r for r in study_records
        if (r.get("holdout_total_trades") or 0) >= 1
        and r.get("holdout_expectancy_capital_weighted") is not None
        and r.get("holdout_expectancy_cost_stress_full_realism") is not None
    ]
    if not with_trades:
        return InvariantResult(
            name="check_cost_stress_distinctness",
            passed=True,
            expected=f"<= {min_affected_fraction:.0%} der Studies mit >= 1 Trade identisch zur Basis",
            actual=None,
            severity="high",
            detail="Keine Studies mit >= 1 Holdout-Trade und beiden Kostenstress-Feldern — "
                   "nicht anwendbar.",
        )
    identical = [
        r for r in with_trades
        if float(r["holdout_expectancy_cost_stress_full_realism"])
        == float(r["holdout_expectancy_capital_weighted"])
    ]
    fraction = len(identical) / len(with_trades)
    passed = fraction <= min_affected_fraction
    return InvariantResult(
        name="check_cost_stress_distinctness",
        passed=passed,
        expected=f"<= {min_affected_fraction:.0%} der Studies mit >= 1 Trade identisch zur Basis",
        actual=round(fraction, 4),
        severity="high",
        detail=("OK" if passed else
                f"{len(identical)}/{len(with_trades)} Studies mit >= 1 Trade: "
                "holdout_expectancy_cost_stress_full_realism == holdout_expectancy_capital_"
                "weighted bit-exakt — die 'full_realism'-Kostenstufe ist ein No-Op (financing_bps/"
                "slippage_bps sind in backtest.json fuer alle Asset-Klassen 0.0, #1010/#1162)."),
        provenance=({"n_identical": len(identical), "n_with_trades": len(with_trades)}
                    if not passed else None),
    )


def check_cost_stress_monotonicity(
    study_records: list[dict], *, step_tolerance_bps: float = 0.05,
) -> InvariantResult:
    """Issue #945/#1111 (Katalog #960) — blockierender Regressionswaechter gegen die Root-Cause
    dieses Fixes: ``holdout_expectancy_cost_stress_1_5x``/``_2x`` werden aus
    ``holdout_expectancy_capital_weighted`` abgeleitet (``backtest_runner._expectancy_cost_stress``,
    DIESELBE 5-%-Notional-Boden-Population wie die Basis) — auf DERSELBEN Basis MUSS die Kosten-
    Stress-Leiter deshalb monoton FALLEND sein (hoehere Kosten ⇒ nie hoehere Expectancy) UND in
    GLEICHEN Schritten (0,5x Multiplikator-Schritte ⇒ gleich grosse Kostenschritte). Vor diesem Fix
    wurde die Leiter aus ``holdout_expectancy_capital_weighted`` abgeleitet, aber gegen
    ``holdout_expectancy`` (Mittel von Quotienten, andere Population) berichtet — bei
    SqueezeBreakout/PLTR (Divergenz Faktor 7,9) erschien der 2×-Kostenstress dadurch als
    VERBESSERUNG um +145,76 bps. Dieser Check FAILt auf genau diesem Altstand und besteht, sobald
    Basis und Leiter dieselbe Population teilen.

    Klausel je Study mit definierten Werten:
        exp >= exp_1_5x >= exp_2x
        |(exp - exp_1_5x) - (exp_1_5x - exp_2x)| <= step_tolerance_bps (Default 0,05 bps)

    Nur Studies mit allen drei Feldern definiert werden geprüft (kein Trade ⇒ nicht anwendbar für
    diese Study, kein FAIL)."""
    tolerance = step_tolerance_bps / 10000.0
    with_data = [
        r for r in study_records
        if r.get("holdout_expectancy_capital_weighted") is not None
        and r.get("holdout_expectancy_cost_stress_1_5x") is not None
        and r.get("holdout_expectancy_cost_stress_2x") is not None
    ]
    if not with_data:
        return InvariantResult(
            name="check_cost_stress_monotonicity",
            passed=True,
            expected="exp >= exp_1_5x >= exp_2x UND gleich grosse Schritte (+/- "
                     f"{step_tolerance_bps:.2f} bps)",
            actual=None,
            severity="blocking",
            detail="Keine Studies mit allen drei Kostenstress-Feldern — nicht anwendbar.",
        )
    offenders: dict[str, dict[str, float]] = {}
    for r in with_data:
        exp = float(r["holdout_expectancy_capital_weighted"])
        exp_1_5x = float(r["holdout_expectancy_cost_stress_1_5x"])
        exp_2x = float(r["holdout_expectancy_cost_stress_2x"])
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        if exp_1_5x > exp + tolerance or exp_2x > exp_1_5x + tolerance:
            offenders[key] = {"exp": exp, "exp_1_5x": exp_1_5x, "exp_2x": exp_2x,
                              "violation": "not_monotonic"}
            continue
        step_1 = exp - exp_1_5x
        step_2 = exp_1_5x - exp_2x
        if abs(step_1 - step_2) > tolerance:
            offenders[key] = {"exp": exp, "exp_1_5x": exp_1_5x, "exp_2x": exp_2x,
                              "step_1": step_1, "step_2": step_2, "violation": "uneven_steps"}
    passed = not offenders
    return InvariantResult(
        name="check_cost_stress_monotonicity",
        passed=passed,
        expected="exp >= exp_1_5x >= exp_2x UND gleich grosse Schritte (+/- "
                 f"{step_tolerance_bps:.2f} bps)",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies: die Kostenstress-Leiter ist nicht monoton "
                "und/oder ungleich gestuft — Basis (holdout_expectancy_capital_weighted) und "
                "Leiter (holdout_expectancy_cost_stress_1_5x/_2x) stehen auf verschiedenen "
                f"Populationen (#945/#1111): {offenders}"),
        provenance={"offenders": offenders} if offenders else None,
    )


def check_dust_round_trip_share(study_records: list[dict], *,
                                max_share: float = 0.01) -> InvariantResult:
    """Issue #1085 (Katalog #866-2, Kohorte D) — Dust-Round-Trips (Notional zwischen ~1e-14 und
    ~1e-12, Fliesskomma-Residuen eines Netto-Exposure-Nulldurchgangs beim Scale-in/Scale-out) werden
    als vollwertige Round-Trips gezählt (Beweis B-19 im #866-Katalog: 9205 von 330 083 gepoolten
    Round-Trips, bis 11,80 % einer Study). Sie füllen JEDEN gepoolten Nenner, der auf Round-Trip-
    Zählungen aufbaut (``oos_total_trades_with_exit_telemetry``, ``exit_reason_histogram`` — ein
    Dust-Leg hat keinen echten Exit-Grund und erscheint als ``UNKNOWN``, siehe #1078 — sowie
    ``timebox_violating_trades_denominator``).

    ``dust_round_trips_filtered`` (Σ ``oos_dust_round_trips_filtered_count`` über die Trials dieser
    Study; seit #946/#1112, Katalog #960, AN DER ROUND-TRIP-QUELLE verworfen —
    ``backtest_runner._filter_dust_round_trips`` — statt nur an der Expectancy-Konsumstelle, siehe
    dortiger Docstring) gegen ``oos_total_trades_with_exit_telemetry`` als Nenner. Severity
    ``high`` — eine hohe Quote ist ein Datenqualitäts-/Extraktionsbefund, kein Promotions-Blocker
    per se."""
    with_data = [
        r for r in study_records
        if r.get("dust_round_trips_filtered") is not None
        and (r.get("oos_total_trades_with_exit_telemetry") or 0) > 0
    ]
    if not with_data:
        return InvariantResult(
            name="check_dust_round_trip_share",
            passed=True,
            expected=f"dust_round_trips_filtered / oos_total_trades_with_exit_telemetry <= {max_share} je Study",
            actual=None,
            severity="high",
            detail="Keine Studies mit Dust-Round-Trip-Telemetrie — nicht anwendbar.",
        )
    offenders: dict[str, float] = {}
    for r in with_data:
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        denom = int(r["oos_total_trades_with_exit_telemetry"])
        share = round(int(r["dust_round_trips_filtered"]) / denom, 4)
        if share > max_share:
            offenders[key] = share
    passed = not offenders
    return InvariantResult(
        name="check_dust_round_trip_share",
        passed=passed,
        expected=f"dust_round_trips_filtered / oos_total_trades_with_exit_telemetry <= {max_share} je Study",
        actual=offenders if offenders else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies mit einem Dust-Round-Trip-Anteil > {max_share}: "
                f"{offenders} — Fliesskomma-Residuen eines Netto-Exposure-Nulldurchgangs werden als "
                "vollwertige Round-Trips gezählt und verdünnen jeden gepoolten Nenner (#1085)."),
    )


def check_expectancy_outlier_dependence(study_records: list[dict]) -> InvariantResult:
    """Issue #1073 (Katalog #866-2, Kohorte D) — FAIL, wenn
    ``sign(holdout_expectancy_winsorized) != sign(holdout_expectancy_notional_weighted)`` (oder die
    winsorisierte Expectancy nicht-positiv wird, während die rohe positiv ist) — das gesamte
    positive Ergebnis eines Kandidaten hängt dann an einer kleinen Zahl extremer Trades, kein
    robuster Edge (Beweis B-8 im #866-Katalog: drei Vorzeichenwechsel in einem Referenzlauf,
    darunter der ERSTGELISTETE Kandidat des Berichts — AdxAtrMomentum +17,23 bps roh → −1,44 bps
    winsorisiert, getragen von 6 von 132 Trades). Feldname ``holdout_expectancy_notional_weighted``
    seit #945/#1111 (vormals ``holdout_expectancy``).

    Severity ``high`` (nicht ``blocking``) — reine Sichtbarkeits-/Rang-Diagnose; die tatsächliche
    Konsequenz (Deployment-Blockade) trägt ``deployment_gate``s ``expectancy_outlier_robust``-
    Klausel. Nur Studies mit BEIDEN Feldern definiert werden geprüft."""
    with_data = [
        r for r in study_records
        if r.get("holdout_expectancy_notional_weighted") is not None
        and r.get("holdout_expectancy_winsorized") is not None
    ]
    if not with_data:
        return InvariantResult(
            name="check_expectancy_outlier_dependence",
            passed=True,
            expected="sign(holdout_expectancy_winsorized) == sign(holdout_expectancy_notional_weighted) "
                     "UND holdout_expectancy_winsorized > 0 (sofern holdout_expectancy_notional_weighted > 0)",
            actual=None,
            severity="high",
            detail="Keine Studies mit beiden Expectancy-Feldern — nicht anwendbar.",
        )
    offenders: dict[str, dict[str, float]] = {}
    for r in with_data:
        raw = float(r["holdout_expectancy_notional_weighted"])
        winsorized = float(r["holdout_expectancy_winsorized"])
        if raw > 0.0 and winsorized <= 0.0:
            key = f"{r.get('strategy')}/{r.get('symbol')}"
            offenders[key] = {"holdout_expectancy_notional_weighted": raw,
                              "holdout_expectancy_winsorized": winsorized}
    passed = not offenders
    return InvariantResult(
        name="check_expectancy_outlier_dependence",
        passed=passed,
        expected="sign(holdout_expectancy_winsorized) == sign(holdout_expectancy_notional_weighted) "
                 "UND holdout_expectancy_winsorized > 0 (sofern holdout_expectancy_notional_weighted > 0)",
        actual=offenders if offenders else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies mit Vorzeichenwechsel zwischen roher und "
                f"winsorisierter Expectancy: {offenders} — das positive Ergebnis hängt an einer "
                "kleinen Zahl extremer Trades, kein robuster Edge (#1073)."),
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
    (kleine Nenner nahe Null sind Divisionsartefakte ohne Information, keine echten Kandidaten).

    Issue #945/#1111 — konsumiert bewusst ``holdout_expectancy_notional_weighted`` (vormals
    ``holdout_expectancy``, das MITTEL der Per-Trade-Quotienten), NICHT
    ``holdout_expectancy_capital_weighted``: die Sizing-Identitaet ``ln(1+TR) ≈ n · expectancy``
    gilt fuer den ARITHMETISCHEN Mittelwert der Per-Trade-Returns, nicht fuer einen Summenquotienten
    — ein Wechsel auf die kapitalgewichtete Grösse wuerde die Identitaet selbst verletzen.

    Issue #989/#1143 (Katalog #986, Pitfall #412 in AGENTS.md) — ``f_implied`` ist eine algebraische
    UMKEHRUNG der Identitaet, keine Messung: sie unterstellt, dass jede Abweichung von
    ``trade_amount_pct`` eine Sizing-Anomalie ist, obwohl auch (a) variables Sizing durch
    ``_compute_quantity``s Floor-Rundung auf ``size_precision``, (b) Kompoundierung ueber die
    Holdout-Spanne bei hohem Exposure, oder (c) Dust-Round-Trips im ``n``-Zaehler die Identitaet
    selbst verletzen koennen — DANN divergiert ``f_implied`` von ``trade_amount_pct``, OHNE dass das
    tatsaechliche Sizing (die reale ``rt_notional``/Equity-Relation) je Round-Trip abweicht.
    ``holdout_f_realized_median`` (``rt_notional / equity_at_entry`` DIREKT je Round-Trip gemessen,
    siehe ``backtest_runner._finalize_round_trip``) ist das primaere Entscheidungskriterium, sofern
    verfuegbar — sie umgeht (a)-(c) vollstaendig, weil sie nicht ueber die Identitaet zurueckrechnet,
    sondern das reale Notional/Equity-Verhaeltnis direkt abliest. NUR wenn ``holdout_f_realized_
    median`` fehlt (aeltere Report-JSONs ohne dieses Feld), faellt die Pruefung auf die algebraische
    ``f_implied``-Berechnung zurueck (bit-identisch zum Pre-#989-Verhalten). ``actual`` traegt je
    Offender ``"source": "measured"|"implied"``, damit ein Bericht sofort unterscheidet, ob die
    Abweichung Sizing (measured) oder ein reiner Identitaets-/Metrik-Artefakt (implied) ist."""
    with_data = [
        r for r in study_records
        if (r.get("holdout_total_trades") or 0) >= min_trades
        and r.get("trade_amount_pct")
        and (
            r.get("holdout_f_realized_median") is not None
            or (r.get("holdout_expectancy_notional_weighted") is not None
                and abs(r["holdout_expectancy_notional_weighted"]) >= min_abs_expectancy
                and r.get("holdout_total_return") is not None)
        )
    ]
    if not with_data:
        return InvariantResult(
            name="check_sizing_identity_coherence",
            passed=True,
            expected=f"|f_realized_median|f_implied - trade_amount_pct| / trade_amount_pct <= {max_relative_gap}",
            actual=None,
            detail="Keine Studies mit Holdout-Trades/Expectancy/trade_amount_pct — nicht anwendbar.",
            severity="blocking",
        )
    offenders: dict[str, dict] = {}
    for r in with_data:
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        trade_amount_pct = float(r["trade_amount_pct"])
        f_realized_median = r.get("holdout_f_realized_median")
        if f_realized_median is not None:
            # Issue #989/#1143 — DIREKT gemessen, primaeres Kriterium (siehe Docstring).
            f_realized_pct = float(f_realized_median) * 100.0
            gap = abs(f_realized_pct - trade_amount_pct) / trade_amount_pct
            if gap > max_relative_gap:
                offenders[key] = {
                    "f_realized_pct": round(f_realized_pct, 4),
                    "trade_amount_pct": trade_amount_pct,
                    "source": "measured",
                }
        else:
            # Fallback: algebraisch implizierte Berechnung (kein holdout_f_realized_median verfuegbar).
            n = r["holdout_total_trades"]
            expectancy = float(r["holdout_expectancy_notional_weighted"])
            total_return = float(r["holdout_total_return"])
            try:
                f_implied = (math.log(1.0 + total_return) / (n * expectancy)) * 100.0
            except (ValueError, ZeroDivisionError):
                continue
            gap = abs(f_implied - trade_amount_pct) / trade_amount_pct
            if gap > max_relative_gap:
                offenders[key] = {
                    "f_implied_pct": round(f_implied, 4),
                    "trade_amount_pct": trade_amount_pct,
                    "source": "implied",
                }
    passed = not offenders
    return InvariantResult(
        name="check_sizing_identity_coherence",
        passed=passed,
        expected=f"|f_realized_median|f_implied - trade_amount_pct| / trade_amount_pct <= {max_relative_gap}",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies: der (gemessene oder implizierte, siehe je "
                f"Offender 'source') Sizing-Anteil weicht relativ um mehr als {max_relative_gap} vom "
                f"konfigurierten trade_amount_pct ab: {offenders} — bei 'source': 'measured' eine "
                "reale Sizing-Anomalie (#989/#1143); bei 'implied' moeglicherweise nur ein "
                "Identitaets-/Metrik-Artefakt, keine reale Sizing-Abweichung (#1028)."),
    )


def check_atr_scale_homogeneity(
    study_records: list[dict], *, max_ratio: float = 6.0,
    atr_floor_bps_by_symbol: dict[str, float] | None = None,
    floor_tolerance: float = 1e-6,
) -> InvariantResult:
    """Issue #1028 (Katalog #866) — ATR in bps ist skaleninvariant gegenüber einer reinen
    multiplikativen Preisadjustierung: über die Strategien EINES Symbols hinweg (dieselbe
    Preisreihe, dasselbe Fenster) sollte ``atr_median_bps`` innerhalb einer engen Bandbreite
    streuen. Ein grosser Faktor zwischen ``max``/``min`` je Symbol ist EIN MÖGLICHES Symptom einer
    Sprungstelle (Split-/Adjustierungsgrenze oder Snapshot-Naht) im ATR-Fenster einer oder mehrerer
    Strategien-Studies auf diesem Symbol — beobachtet: TSLA mit 18,2–366,2 bps (Faktor 20) gegen
    2–59 bps auf allen übrigen Symbolen des Katalog-Referenzlaufs.

    Issue #1071 (Pitfall #380-Klasse, wörtliche Wiederkehr von #1028/#1052) — Root-Cause: die
    Vor-#1071-Meldung BEHAUPTETE unbedingt "Signatur einer Sprungstelle in der Preisreihe", obwohl
    der Check nur eine SPANNWEITE misst, keine Ursache. Mindestens drei Mechanismen erzeugen
    dieselbe Spannweite: (a) eine echte Preis-Sprungstelle, (b) der NENNER liegt auf
    ``atr_floor_bps_by_asset_class`` (der Floor ist eine KONFIGURIERTE Konstante, keine
    Preis-Beobachtung — Beweis B-16 im #866-Katalog: DynamicBreakout exakt auf dem EQUITY-Floor
    2.0), (c) eine Fremdkohorte. ``atr_floor_bps_by_symbol`` (vom Aufrufer aufgelöst, z. B.
    ``backtest_runner.resolve_atr_floor_bps`` je Symbol/Asset-Klasse) macht (b) MESSBAR statt
    geraten: jede Study, deren ``atr_median_bps`` bis auf ``floor_tolerance`` auf dem Floor ihres
    Symbols liegt, wird als ``atr_floor_binding_studies`` ausgewiesen; die Meldung nennt den
    Mechanismus, den sie tatsächlich gemessen hat, statt eine Ursache zu behaupten.

    Issue #951/#1117 (Katalog #960) — der Floor ist seit #1096 Fix Punkt 1 selbst COST-GEKOPPELT
    (``backtest_runner.cost_coupled_atr_floor_bps``, hebt die Asset-Class-Konstante auf
    ``min_stop_to_cost_ratio · c_rt / atr_trailing_multiplier`` an, wenn das grösser ist) und
    variiert damit PRO STUDY (über ``atr_trailing_multiplier_median``), nicht mehr nur pro Symbol.
    Jede Study, die ``atr_floor_bps_derived`` trägt (``report._study_record``, die per-Study
    abgeleitete Grösse), wird GEGEN DIESEN Wert geprüft statt gegen die gröbere, rein
    asset-class-aufgelöste ``atr_floor_bps_by_symbol``-Konstante — ein Rückfall auf Letztere bleibt
    für Legacy-Aufrufer/-Fixtures ohne das Feld erhalten (rückwärtskompatibel).

    Issue #1026/#1175 (Katalog #866-2) — Root-Cause: ``floor_binding_studies`` wurde bislang (a)
    NUR innerhalb eines bereits OFFENDING Symbols (``ratio > max_ratio``) berechnet — ein Symbol
    mit NUR EINER Study oder einer Spannweite unter der Schwelle konnte nie als floor-gebunden
    erscheinen, obwohl die Study selbst floor-gebunden war —, und (b) über einen GLEICHHEITS-
    Vergleich des EFFEKTIVEN, bereits ratschen-gefloorten ``atr_median_bps`` gegen den Floor
    (``abs(val - floor) <= tolerance``): der Ratschen-Mechanismus haelt den STUDY-MEDIAN des
    effektiven ATR haeufig knapp OBERHALB des Floors, selbst wenn der Floor ueber grosse Teile des
    Fensters band — der Gleichheits-Vergleich sah das folgerichtig nie. Fix: der Wächter fragt
    stattdessen direkt, ob der Floor GEBUNDEN HÄTTE (``atr_raw_median_bps < atr_floor_bps_derived``,
    der ROHE, ungefloorte Median gegen den Floor selbst) — unabhängig vom Spannweiten-Offender-
    Status des Symbols, über JEDE Study mit beiden Feldern. Fehlen beide Felder in JEDER Study
    (kein Symbol misst ``atr_raw_median_bps``/``atr_floor_bps_derived``) ⇒ ``evaluable=False``
    statt einer stillen leeren Liste (Tri-State-Mechanik wie #995/#1147) — eine leere Liste war
    zuvor nicht von "nichts bindet" unterscheidbar."""
    by_symbol: dict[str, list[tuple[str, float, float | None]]] = {}
    for r in study_records:
        atr = r.get("atr_median_bps")
        symbol = r.get("symbol")
        strategy = r.get("strategy")
        if atr and symbol:
            by_symbol.setdefault(symbol, []).append(
                (strategy, float(atr), r.get("atr_floor_bps_derived")))

    # Issue #1026/#1175 — floor_binding_studies unabhaengig von der Spannweiten-Offender-Schleife
    # unten: JEDE Study mit einem aufloesbaren Floor wird direkt geprueft. Bevorzugtes Kriterium
    # (Fix 4.1): ``atr_raw_median_bps < effective_floor`` — der ROHE Median gegen den Floor,
    # unabhaengig vom Ratschen-Mechanismus, der den EFFEKTIVEN ``atr_median_bps`` haeufig knapp
    # oberhalb des Floors haelt. Legacy-Rueckfall (kein ``atr_raw_median_bps``, z. B. Pre-#1129-
    # Reports): der vorherige Gleichheits-Vergleich des EFFEKTIVEN Werts gegen den Floor bleibt
    # erhalten (rueckwaertskompatibel, bit-identisch zum Pre-#1026-Verhalten fuer solche Records).
    floor_binding_studies: list[str] = []
    floor_binding_provenance: dict[str, dict] = {}
    n_studies_measured = 0
    for r in study_records:
        symbol, strategy = r.get("symbol"), r.get("strategy")
        if not symbol or not strategy:
            continue
        derived_floor = r.get("atr_floor_bps_derived")
        sym_floor = (atr_floor_bps_by_symbol or {}).get(symbol)
        effective_floor = derived_floor if derived_floor is not None else sym_floor
        if effective_floor is None:
            continue
        label = f"{strategy}/{symbol}"
        raw = r.get("atr_raw_median_bps")
        if raw is not None:
            n_studies_measured += 1
            if float(raw) < float(effective_floor) - floor_tolerance:
                floor_binding_studies.append(label)
                floor_binding_provenance[label] = {
                    "raw": round(float(raw), 4), "floor": round(float(effective_floor), 4),
                    "faktor": (round(float(effective_floor) / float(raw), 2) if raw else None),
                    "stopdistanz_bps": r.get("stop_distance_bps"),
                    "realized_stop_loss_ratio": r.get("realized_stop_loss_ratio"),
                    "criterion": "atr_raw_median_bps_below_floor",
                }
        else:
            val = r.get("atr_median_bps")
            if val is None:
                continue
            n_studies_measured += 1
            if abs(float(val) - float(effective_floor)) <= floor_tolerance:
                floor_binding_studies.append(label)
                floor_binding_provenance[label] = {
                    "floor": round(float(effective_floor), 4), "atr_median_bps": round(float(val), 4),
                    "stopdistanz_bps": r.get("stop_distance_bps"),
                    "realized_stop_loss_ratio": r.get("realized_stop_loss_ratio"),
                    "criterion": "legacy_effective_atr_equals_floor",
                }
    # Issue #1026/#1175 Akzeptanzkriterium 3 — ``[]`` allein ist zwischen "gemessen, nichts
    # bindet" und "nicht gemessen" nicht unterscheidbar; dieses Flag macht die Evaluierbarkeit der
    # ``atr_floor_binding_studies``-Sektion selbst explizit (unabhaengig vom Spannweiten-
    # Gesamtergebnis dieses Checks, das eine ANDERE Frage beantwortet), report.py Sektion 5.3
    # liest es, um zwischen einer leeren Liste und "INCONCLUSIVE" zu unterscheiden.
    floor_binding_evaluable = n_studies_measured > 0
    _floor_provenance = {
        "atr_floor_binding_studies": sorted(set(floor_binding_studies)),
        "atr_floor_binding_studies_detail": floor_binding_provenance,
        "atr_floor_binding_evaluable": floor_binding_evaluable,
        "atr_floor_binding_n_studies_measured": n_studies_measured,
    }

    candidates = {sym: vals for sym, vals in by_symbol.items() if len(vals) >= 2}
    if not candidates:
        return InvariantResult(
            name="check_atr_scale_homogeneity",
            passed=True,
            expected=f"max(atr_median_bps)/min(atr_median_bps) <= {max_ratio} je Symbol",
            actual=None,
            detail="Kein Symbol mit >= 2 Strategien-Studies und atr_median_bps — nicht anwendbar.",
            severity="high",
            provenance=_floor_provenance,
        )
    offenders: dict[str, float] = {}
    for sym, triples in candidates.items():
        vals = [v for _s, v, _f in triples]
        lo, hi = min(vals), max(vals)
        if lo <= 0:
            continue
        ratio = hi / lo
        if ratio > max_ratio:
            offenders[sym] = round(ratio, 2)
    passed = not offenders
    if not passed and floor_binding_studies:
        mechanism = (
            f"Nenner an der Floor-Grenze ({len(floor_binding_studies)} Study/Studies: "
            f"{sorted(floor_binding_studies)}) ⇒ Spannweite ist (mindestens teilweise) ein "
            "Konfigurationsartefakt, keine nachgewiesene Preis-Sprungstelle.")
    elif not passed:
        mechanism = (
            "Nenner NICHT an einer bekannten Floor-Grenze — Preis-Sprungstelle/Fremdkohorte-"
            "Verdacht bleibt eine von mehreren möglichen Ursachen, nicht bestätigt.")
    else:
        mechanism = "OK"
    return InvariantResult(
        name="check_atr_scale_homogeneity",
        passed=passed,
        expected=f"max(atr_median_bps)/min(atr_median_bps) <= {max_ratio} je Symbol",
        actual=offenders if offenders else None,
        severity="high",
        provenance=_floor_provenance,
        detail=("OK" if passed else
                f"{len(offenders)} Symbol(e) mit einer ATR-Spannweite über {max_ratio}x zwischen "
                f"Strategien: {offenders}. {mechanism} (#1028/#1071)"),
    )


def check_sizing_parity_backtest_vs_allocator(
    trade_amount_pct_by_strategy: dict[str, float], *,
    max_symbol_exposure_fraction: float | None, tolerance: float = 0.01,
    parity_factor: float = 1.0,
) -> InvariantResult:
    """Issue #1042 (Katalog #866) E-2 — Sichtbarkeits-Wächter, KEIN Promotion-Gate. Der Backtest
    setzt jede Position mit ``trade_amount_pct`` (``strategy_defaults.json``/``strategies.json``)
    ohne Portfolio-Deckel; ``MomentumLSAllocator`` (#999, ``backtest.json['live_risk']
    ['max_symbol_exposure_fraction']``) begrenzt dieselbe Position live zusätzlich auf einen
    Gesamtdeckel (``max_total_exposure_fraction``) und einen Drawdown-Damper (``ψ(DD)``). Divergiert
    das konfigurierte ``trade_amount_pct`` einer Strategie von ``max_symbol_exposure_fraction · 100
    · parity_factor`` um mehr als ``tolerance`` (relativ, Default 1 %), sind validierter (Backtest)
    und tatsächlich ausgeführter (Live) Risikoprozess VERSCHIEDEN — jede aus dem Backtest
    abgeleitete Kennzahl (Expectancy, CVaR, Drawdown, Sizing-Identität) bezieht sich auf ein
    anderes Sizing-Regime als das live gefahrene.

    Issue #1014/#1166 (Katalog #1170) — Root-Cause: ZWEI unabhängig gepflegte Grössen für
    dieselbe Frage ("wie viel Kapital je Symbol") — ``strategy_defaults.json``'s
    ``trade_amount_pct`` (15,0 für alle 15 Strategien) und ``backtest.json['live_risk']
    ['max_symbol_exposure_fraction']`` (0,10) — divergierten UNBEGRÜNDET um den Faktor 1,5, ohne
    dass irgendwo dokumentiert war, ob diese Abweichung eine bewusste Entscheidung oder ein Bug
    ist. Fix: ``parity_factor`` (Default 1.0 — Parität, das alte Verhalten dieser Funktion bit-
    identisch) macht eine ABWEICHUNG EXPLIZIT UND BENANNT (``backtest.json['live_risk']
    ['trade_amount_pct_parity_factor']``), statt sie stillschweigend als 15 gleichlautende FAILs
    zu melden — der Check prüft seither die ABLEITUNG (``allocator_pct · parity_factor``) statt
    zweier unabhängig gepflegter Konstanten. Die Wahl des Zielwerts selbst (10 % oder 15 %, bzw.
    ob ``parity_factor`` 1.0 oder etwas anderes sein soll) bleibt eine Betreiberentscheidung —
    dieser Fix erzwingt nur, dass GENAU EINE Zahl (der Faktor) die Beziehung trägt, nicht zwei
    unabhängig driftende Konstanten.

    Bewusst additive Telemetrie statt eines blockierenden Gates: eine vollständige Sizing-
    Vereinheitlichung (den Backtest tatsächlich unter ``max_exposure_fraction`` laufen zu lassen)
    würde jede historisch kalibrierte Schwelle/jeden Reward-Gradienten ändern und braucht einen
    echten Re-Kalibrierungslauf gegen Marktdaten — ausserhalb des Scopes dieser additiven Prüfung
    (dokumentierter Scope-Cut, analog #843/#845)."""
    if max_symbol_exposure_fraction is None or not trade_amount_pct_by_strategy:
        return InvariantResult(
            name="check_sizing_parity_backtest_vs_allocator",
            passed=True,
            expected=None,
            actual=None,
            detail=("Kein max_symbol_exposure_fraction (backtest.json['live_risk']) oder keine "
                    "trade_amount_pct-Daten — nicht anwendbar."),
        )
    allocator_pct = float(max_symbol_exposure_fraction) * 100.0 * float(parity_factor)
    offenders: dict[str, dict] = {}
    if allocator_pct > 0:
        for strategy, pct in trade_amount_pct_by_strategy.items():
            if pct is None:
                continue
            rel_diff = abs(float(pct) - allocator_pct) / allocator_pct
            if rel_diff > tolerance:
                offenders[strategy] = {
                    "backtest_trade_amount_pct": round(float(pct), 4),
                    "allocator_max_symbol_pct": round(allocator_pct, 4),
                    "parity_factor": round(float(parity_factor), 4),
                }
    passed = not offenders
    return InvariantResult(
        name="check_sizing_parity_backtest_vs_allocator",
        passed=passed,
        expected=f"trade_amount_pct == {allocator_pct:.2f} % (±{tolerance * 100:.0f} %, "
                 f"parity_factor={parity_factor:g}) je Strategie",
        actual=offenders if offenders else None,
        severity="medium",
        detail=("OK" if passed else
                f"{len(offenders)} Strategie(n) mit Sizing-Divergenz Backtest vs. "
                f"MomentumLSAllocator (nach parity_factor={parity_factor:g}): {offenders} — "
                "Backtest und Live sind unter verschiedenen Risikoprozessen validiert bzw. "
                "ausgeführt (#1042 E-2, #1014/#1166)."),
    )


def check_worker_utilisation_plausible(
    worker_occupancy_wallclock: float | None, *, max_ratio: float = 1.0,
    n_studies: int | None = None,
) -> InvariantResult:
    """Issue #1038 (Katalog #866) — ``report._worker_occupancy_wallclock`` (Σ Study-Wallclock /
    (n_jobs × Sweep-Wallclock), vormals ``_worker_utilisation``) heisst "Auslastung", kann aber
    durch verschachtelte, studieneigene Worker-Pools (``backtest_runner.py``) oder — vor #1023/#1086
    — eingemischte Studies fremder Laeufe ueber ``max_ratio`` (Default 1.0, physikalisch das Maximum
    einer echten Auslastung) hinaus wachsen. Beobachtet: 151,8 %/246,5 %/332,9 % ueber drei Laeufe.
    Ein Wert ueber der Schwelle ist ein FAIL, keine unkommentierte Anzeigezahl.

    Issue #949/#1115 (Katalog #960) — der Parameter (und diese Invariante) pruefen AUSSCHLIESSLICH
    ``worker_occupancy_wallclock``, NICHT die zweite, physikalisch <= 1.0 begrenzte Grösse
    ``cpu_utilisation_backtest`` (``report._cpu_utilisation_backtest``) — beide hiessen vorher
    implizit "Worker-Auslastung" und waren dadurch im Report-Dokument (§3) UND in dieser Invariante
    ohne den expliziten Funktionsnamen nicht unterscheidbar (B-6: 0,7583/1,1251/1,1360 hier gegen
    60,2/89,6/90,5% bei der jeweils anderen Groesse).

    Issue #1089 (Katalog #922) Fix Punkt 3 — ``n_studies`` (die Kohortengrösse hinter der Σ Study-
    Wallclock) optional als ``provenance`` mitgefuehrt, damit ein FAIL/PASS nach dem #1086-Fix
    nachvollziehbar bleibt (bit-identisches ``actual`` fuer bestehende Konsumenten)."""
    if worker_occupancy_wallclock is None:
        return InvariantResult(
            name="check_worker_utilisation_plausible",
            passed=True,
            expected=f"<= {max_ratio}",
            actual=None,
            detail="Kein worker_occupancy_wallclock-Wert — nicht anwendbar.",
        )
    passed = worker_occupancy_wallclock <= max_ratio
    return InvariantResult(
        name="check_worker_utilisation_plausible",
        passed=passed,
        expected=f"<= {max_ratio}",
        actual=round(worker_occupancy_wallclock, 4),
        provenance={"n_studies": n_studies} if n_studies is not None else None,
        detail=("OK" if passed else
                f"worker_occupancy_wallclock={worker_occupancy_wallclock:.4f} > {max_ratio} — Σ "
                "Study-Wallclock ueberlappt sich (verschachtelte Worker-Pools und/oder eingemischte "
                "Studies eines anderen Laufs, #1038-Fehlerklasse)."),
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


def _constraint_feasible_reward_terms(trials: list[dict]) -> list[dict]:
    """Issue #1082 (Katalog #866-2, Kohorte E) — die per #612-Sampler-Constraint TATSAECHLICH
    feasible Teilmenge (``oos_constraint_violations`` — JEDE Komponente <= 0, Optuna-Konvention),
    im Unterschied zu ``_feasible_reward_terms`` (schliesst NUR den ``inference_failure_policy=
    'prune'``-Pfad aus, #949). Root-Cause #1082: in 13 von 14 Studies des #866-2-Referenzlaufs war
    ``reward_std_total == reward_std_feasible`` BIT-IDENTISCH, weil in diesen Läufen so gut wie nie
    tatsächlich geprunt wurde — die #949-Kohorte war damit fast immer identisch mit der
    Gesamtkohorte, obwohl gleichzeitig nur 2,8-9,4 % der Trials den ordnenden Reward-Zweig trugen
    (``check_objective_branch_coverage``). Diese Funktion misst stattdessen die Kohorte, die der
    TPE-Sampler selbst als feasible behandelt.

    Trials OHNE Constraint-Telemetrie (Legacy-Report/Test-Fixture ohne ``oos_constraint_
    violations``) werden AUSGESCHLOSSEN, nicht als feasible angenommen (anders als der
    Default-Fallback in ``run_optimization._trial_constraint_violation``) — diese Kohorte soll
    ausschliesslich NACHGEWIESEN feasible Trials tragen, kein Artefakt fehlender Telemetrie."""
    out = []
    for t in trials:
        if t.get("oos_evaluated") is not True or not t.get("reward_terms"):
            continue
        cv = t.get("oos_constraint_violations")
        if not cv:
            continue
        try:
            feasible = all(float(c) <= 0.0 for c in cv)
        except (TypeError, ValueError):
            continue
        if feasible:
            out.append(t["reward_terms"])
    return out


def _reward_std_constraint_feasible(trials: list[dict]) -> float | None:
    """Issue #1082 — ``reward_std`` über ``_constraint_feasible_reward_terms`` (< 2 Trials ⇒
    ``None``, dieselbe Konvention wie ``_reward_std_total_and_feasible``)."""
    terms = _constraint_feasible_reward_terms(trials)
    if len(terms) < 2:
        return None
    vals = [
        (t.get("base", 0.0) - t.get("divergence", 0.0) - t.get("dd_penalty", 0.0)
         - t.get("param_pen", 0.0) - t.get("turnover", 0.0) - t.get("fold_dispersion", 0.0)
         + t.get("tie_breaker", 0.0))
        for t in terms
    ]
    return statistics.pstdev(vals)


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
    # Issue #1082 Fix Punkt (b) — reward_std_constraint_feasible ist die per #612-Sampler-Constraint
    # NACHGEWIESEN feasible Teilmenge (siehe _constraint_feasible_reward_terms), eine STRENGERE
    # Kohorte als reward_std_feasible (nur pruned-Ausschluss, #949). Root-Cause: reward_std_feasible
    # entsprach in 13/14 Studies des #866-2-Referenzlaufs bit-identisch reward_std_total, weil kaum
    # je ein Trial tatsaechlich geprunt wurde — der #949-Diagnosewert trug dort keine eigene
    # Information. REWARD_FEASIBLE_PARTITION_DEGENERATE feuert, wenn WEDER reward_std_feasible NOCH
    # die strengere constraint-basierte Kohorte einen messbaren Unterschied zu reward_std_total
    # zeigen — rein informativ (aendert 'passed' NICHT, siehe #949-Regressionsschutz
    # test_pruned_trials_never_cause_a_false_positive_dominance_failure_alone), macht die Degenerierte
    # Partition aber im Report sichtbar statt sie unbeobachtet zu lassen.
    reward_std_constraint_feasible = _reward_std_constraint_feasible(trials)
    reward_feasible_partition_degenerate = (
        reward_std_total is not None
        and reward_std_feasible == reward_std_total
        and (reward_std_constraint_feasible is None
             or reward_std_constraint_feasible == reward_std_total)
    )
    detail = "OK" if passed else "; ".join(failed_clauses)
    if reward_feasible_partition_degenerate:
        detail = (detail + "; " if detail != "OK" else "") + (
            "REWARD_FEASIBLE_PARTITION_DEGENERATE: reward_std_feasible unterscheidet sich NICHT "
            "von reward_std_total (Pruning ist keine unterscheidende Ursache in dieser Study) — "
            f"reward_std_constraint_feasible={reward_std_constraint_feasible!r} liefert ebenfalls "
            "kein eigenes Signal (#1082).")
    return InvariantResult(
        name="check_reward_dynamic_range",
        passed=passed,
        expected=f">= {min_base_dominance_factor} * max(term_std) UND >= "
                 f"{min_reward_std_feasible} UND total/feasible <= {max_total_to_feasible_ratio}",
        actual={"reward_std_total": round(reward_std_total, 6) if reward_std_total is not None else None,
               "reward_std_feasible": round(reward_std_feasible, 6),
               "max_term_std": round(max_term_std, 6),
               "reward_std_constraint_feasible": (
                   round(reward_std_constraint_feasible, 6)
                   if reward_std_constraint_feasible is not None else None),
               "reward_feasible_partition_degenerate": reward_feasible_partition_degenerate},
        severity="high",
        detail=detail,
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
    ``{'stored', 'admissible', 'corroborated', 'written_back', 'skipped_by_reason', 'attempts',
    'max_corroboration_count'}``-Dict.

    Issue #1084 Fix Punkt 1 (Katalog #866-2, Kohorte E) — vorher behauptete ein FAIL UNBEDINGT
    dieselbe geratene Ursache ("#818-Regression: maybe_write_back ohne Produktions-Call-Site"),
    selbst wenn Ebene 2 längst eine Call-Site hat und aus einem ganz anderen, tatsächlich
    beobachteten Grund nicht schreibt (z. B. ein Korroborations-Deadlock, #1084 Root-Cause b — der
    eigene Check ``check_champion_corroboration_reachable`` benennt DIESEN Fall gezielt). Der
    ``detail``-Text meldet seither die BEOBACHTETE ``skipped_by_reason``-Verteilung; die
    "keine Call-Site"-Diagnose gilt nur noch, wenn ``champions_summary['attempts'] == 0`` explizit
    beobachtet wurde (kein einziger Schreibversuch) — ``attempts is None`` (Legacy-Aufrufer/Report-
    only ohne ``studies_out``, siehe ``report._champions_summary``-Docstring) fällt auf die alte,
    store-basierte Formulierung zurück (bit-identisches ``passed``-Verhalten in jedem Fall)."""
    stored = int(champions_summary.get("stored") or 0)
    written_back = int(champions_summary.get("written_back") or 0)
    attempts = champions_summary.get("attempts")
    skipped_by_reason = champions_summary.get("skipped_by_reason") or {}
    if stored == 0 and not attempts:
        return InvariantResult(
            name="check_champion_writeback_reachability",
            passed=True,
            expected="written_back > 0, sobald stored > 0 oder ein Schreibversuch stattfand",
            actual={"stored": 0, "written_back": 0, "attempts": attempts},
            detail="Kein Champion-Store-Eintrag — nicht anwendbar.",
        )
    passed = written_back > 0
    if passed:
        detail = "OK"
    elif attempts == 0:
        detail = ("0 Champion-Writeback-Versuche beobachtet — Ebene 2 (#706) hat KEINE "
                  "Produktions-Call-Site erreicht.")
    else:
        reasons_text = ", ".join(
            f"{v}x {k}" for k, v in sorted(skipped_by_reason.items(), key=lambda kv: -kv[1])
        ) if skipped_by_reason else "unbekannt (keine skipped_by_reason-Telemetrie)"
        cohort_text = f"{attempts} Versuche" if attempts is not None else f"{stored} Store-Eintraege"
        detail = (f"{cohort_text}, 0 Writebacks — beobachtete Ursachen: {reasons_text} (#1084: "
                  "die Ursache ist gemessen, nicht geraten).")
    return InvariantResult(
        name="check_champion_writeback_reachability",
        passed=passed,
        expected="written_back > 0, sobald stored > 0 oder ein Schreibversuch stattfand",
        actual={"stored": stored, "written_back": written_back, "attempts": attempts,
               "skipped_by_reason": skipped_by_reason},
        detail=detail,
    )


def check_champion_attempt_coherence(
    reported_attempts: int | None, actual_writeback_events: int | None,
) -> InvariantResult:
    """Issue #1099 (Katalog #932) — Kalibrierungswächter für den #1099-Fix: ``champions_summary
    ['attempts']`` (``report._champions_summary``) muss der TATSÄCHLICHEN Anzahl emittierter
    ``CHAMPION_WRITEBACK``-Ereignisse dieses Laufs entsprechen (``sweep._attempt_champion_writeback``
    emittiert GENAU EIN Ereignis je Versuch, auch bei Nicht-Erfolg).

    Root-Cause #1099: die #1084-``studies_out``-Rekonstruktion zählte jedes (strategy, symbol)-Paar
    der STUDY-Kohorte als "Versuch" — bei einem ``--report-only``-Report über eine inzwischen
    gewachsene ``proposal_*.json``-Menge lief diese Zahl (52/53/56 im #932-Referenzlauf) an der
    tatsächlich je Lauf KONSTANTEN Versuchszahl (14, im Ereignisstrom nachweisbar) vorbei — die
    Study-Liste ist die falsche Grundgesamtheit für "wie oft wurde ``maybe_write_back`` versucht".
    ``report._champions_summary`` leitet ``attempts`` seither bevorzugt aus dem Ereignisstrom selbst
    ab; dieser Wächter macht ein Wiederauftreten der #1099-Fehlerklasse (z. B. eine künftige
    Regression, die die ``studies_out``-Rekonstruktion fälschlich wieder bevorzugt, obwohl ein
    Ereignisstrom verfügbar wäre) sichtbar statt eines erneut stillen Auseinanderlaufens.

    ``reported_attempts is None`` oder ``actual_writeback_events is None`` (kein Ereignisstrom
    auflösbar — z. B. ein frischer ``--report-only``-Prozess ohne eigenen ``setup_bot_logging``-
    Aufruf, siehe ``report._champions_summary``-Docstring) ⇒ ``inconclusive=True`` (kein Urteil ohne
    Vergleichsgrundlage). severity ``high`` (Diagnose, kein Promotion-Gate)."""
    if reported_attempts is None or actual_writeback_events is None:
        return InvariantResult(
            name="check_champion_attempt_coherence",
            passed=True, inconclusive=True,
            expected="champions.attempts == count(CHAMPION_WRITEBACK events)",
            actual=None, severity="high",
            detail="Kein Ereignisstrom auflösbar (Legacy-/--report-only-Prozess) — kein Urteil ohne "
                   "Vergleichsgrundlage.",
        )
    passed = reported_attempts == actual_writeback_events
    return InvariantResult(
        name="check_champion_attempt_coherence",
        passed=passed,
        expected="champions.attempts == count(CHAMPION_WRITEBACK events)",
        actual={"reported_attempts": reported_attempts,
               "actual_writeback_events": actual_writeback_events},
        severity="high",
        detail=("OK" if passed else
                f"champions.attempts ({reported_attempts}) weicht von der tatsächlichen "
                f"CHAMPION_WRITEBACK-Ereigniszahl ({actual_writeback_events}) ab (#1099-Fehlerklasse: "
                "die Study-Liste statt des Ereignisstroms als Versuchs-Grundgesamtheit)."),
    )


def check_champion_corroboration_reachable(
    champions_summary: dict, *, total_runs_started: int | None = None,
    runs_completed_for_pair: int | None = None,
    corroboration_threshold: int = 2,
) -> InvariantResult:
    """Issue #1084 Fix Punkt 4 (Katalog #866-2, Kohorte E, Root-Cause b), gehärtet #1089 (Katalog
    #922) — benennt den TATSAECHLICHEN Blocker der Ebene-2-Kette (``champions.maybe_write_back``):
    Korroboration verlangt ``lifecycle.corroboration_count >= champion_promote_after_runs``
    (Default 2); ``corroboration_count`` erhöht sich NUR bei einer NEUEN ``run_id`` (#821 —
    verschiedene Läufe, nicht Schreibvorgänge innerhalb eines Laufs).

    Issue #1089 Root-Cause — die Alt-Fassung besass einen ODER-Ast
    (``max_corroboration_count >= threshold ODER total_runs_started > 1``), der PASS meldete,
    sobald ``symbol_coverage.json['total_runs_started']`` > 1 war. Dieser Zähler ist GLOBAL und
    PROZESSÜBERGREIFEND inkrementiert (jeder gleichzeitige Sweep-Prozess, unabhängig von SEINEM
    eigenen Symbol, erhöht ihn) — unter drei gleichzeitigen #1086-Läufen genügte je EIN fremder
    Prozessstart, um diesen Check auf JEDEM der drei Reports fälschlich gruen zu faerben, obwohl
    ``max_corroboration_count`` in allen vieren bei 1 verharrte (Referenzlauf: der GEFÄHRLICHSTE
    der vier gekippten Checks — ein ehrliches FAIL wurde zu einem falschen PASS). Der ODER-Ast
    entfällt ERSATZLOS: der Check prüft ausschliesslich ``max_corroboration_count >=
    corroboration_threshold`` — ein Wert, der bereits korrekt PAAR- und RUN_ID-skopiert ist
    (``champions._bump_corroboration`` zählt DISTINKTE ``run_id``-Werte je Paar, unabhängig von
    Nebenprozessen auf ANDEREN Paaren/Symbolen). ``total_runs_started``/``runs_completed_for_pair``
    werden nur noch als ``provenance`` mitgeführt (Diagnose-Kontext), OHNE jeden Einfluss auf
    PASS/FAIL — der Check kann dadurch durch KEINEN Nebenprozess mehr grün werden.

    ``champions_summary`` ist ``report._champions_summary()``'s Dict (``max_corroboration_count``,
    seit #1084). Kein Store-Eintrag ⇒ nicht anwendbar (PASS)."""
    stored = int(champions_summary.get("stored") or 0)
    max_corr = champions_summary.get("max_corroboration_count")
    if stored == 0 or max_corr is None:
        return InvariantResult(
            name="check_champion_corroboration_reachable",
            passed=True,
            expected=f"max(corroboration_count) >= {corroboration_threshold}",
            actual=None,
            detail="Kein Champion-Store-Eintrag — nicht anwendbar.",
            severity="high",
        )
    passed = max_corr >= corroboration_threshold
    return InvariantResult(
        name="check_champion_corroboration_reachable",
        passed=passed,
        expected=f"max(corroboration_count) >= {corroboration_threshold}",
        actual={"max_corroboration_count": max_corr,
               "corroboration_threshold": corroboration_threshold},
        severity="high",
        provenance={
            # Issue #1089 — rein informativ, GEHT NICHT in die PASS/FAIL-Entscheidung ein (siehe
            # Docstring): ein Nebenprozess kann diese Zahlen bewegen, ohne den Check zu bestehen.
            "total_runs_started": total_runs_started,
            "runs_completed_for_pair": runs_completed_for_pair,
        },
        detail=("OK" if passed else
                f"max(corroboration_count)={max_corr} < {corroboration_threshold} — Ebene 2 "
                "(#706) ist auf der EIGENEN Kohorte strukturell noch unerreichbar. Kein "
                "globaler/Nebenprozess-Zähler kann diesen Befund mehr entkraeften (#1089)."),
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


def check_search_space_override_admissible(diagnosed_pairs: list[dict]) -> InvariantResult:
    """Issue #1066 (Pitfall #371) — jeder ``proposed_bounds``-Eintrag im #761-Diagnose-Cache
    (``sweep_diagnostics.load_diagnosed_pairs_cache``, hier als Liste ihrer Einträge übergeben)
    liegt innerhalb ``spaces._PARAM_DOMAIN_REGISTRY``. FAILt für VOR diesem Fix geschriebene, nicht
    geklammerte Einträge (z. B. ``ema_period: [-325.0, 300]``, Beweis B-5 im #866-Katalog) — PASST,
    sobald ``sweep_diagnostics.migrate_search_space_override_cache`` gelaufen ist (die Klammer in
    ``_widen_bounds_toward``/``spaces._bounds_for`` verhindert seither auch jeden NEUEN
    inadmissiblen Eintrag)."""
    from automation.optimizer.spaces import is_bounds_admissible

    offenders: dict[str, dict] = {}
    for entry in diagnosed_pairs or []:
        proposed = entry.get("proposed_bounds")
        if not proposed:
            continue
        strategy, symbol = entry.get("strategy"), entry.get("symbol")
        bad_params = {}
        for param, bound in proposed.items():
            if not bound or len(bound) != 2:
                continue
            if not is_bounds_admissible(param, bound[0], bound[1]):
                bad_params[param] = list(bound)
        if bad_params:
            offenders[f"{strategy}/{symbol}"] = bad_params
    passed = not offenders
    return InvariantResult(
        name="check_search_space_override_admissible",
        passed=passed,
        expected="jede proposed_bounds-Untergrenze/Obergrenze innerhalb des Domänenregisters",
        actual=offenders if offenders else None,
        detail=("OK" if passed else
                f"{len(offenders)} Paar(e) mit ausserhalb des Domänenregisters liegenden "
                f"proposed_bounds: {offenders} — sweep_diagnostics.migrate_search_space_override_"
                "cache() ausfuehren, bevor der naechste Sweep startet (#1066)."),
        severity="blocking",
    )


def check_diagnosis_ledger_coherence(diagnosed_pairs: list[dict], *,
                                     total_runs_started: int | None) -> InvariantResult:
    """Issue #1068 — der #761-Diagnose-Cache (``n_runs_confirmed`` je Paar) und das Coverage-Ledger
    (``symbol_coverage.total_runs_started``) sind zwei UNABHÄNGIG PERSISTIERTE Zähler über
    denselben Lauf-Verlauf. Kein Paar kann öfter IN FOLGE bestätigt worden sein, als das Ledger
    Läufe gesehen hat — ``max(n_runs_confirmed) > total_runs_started`` beweist, dass einer der
    beiden Stores zurückgesetzt/verloren gegangen ist (im #866-Katalog: das Ledger, Beweis B-18/
    #1064 — ``n_runs_confirmed=1`` bei fünf nachgewiesenen Weitungen, ``expires_after_runs=10``
    gegen ein Ledger, das nie über 1 hinauskommt)."""
    if total_runs_started is None:
        return InvariantResult(
            name="check_diagnosis_ledger_coherence", passed=True,
            expected="max(n_runs_confirmed) <= total_runs_started",
            actual=None, detail="total_runs_started nicht verfuegbar — nicht anwendbar.",
            inconclusive=True,
        )
    max_confirmed = 0
    worst_key = None
    for entry in diagnosed_pairs or []:
        n = int(entry.get("n_runs_confirmed") or 0)
        if n > max_confirmed:
            max_confirmed = n
            worst_key = f"{entry.get('strategy')}/{entry.get('symbol')}"
    passed = max_confirmed <= total_runs_started
    return InvariantResult(
        name="check_diagnosis_ledger_coherence",
        passed=passed,
        expected="max(n_runs_confirmed) <= total_runs_started",
        actual={"max_n_runs_confirmed": max_confirmed, "total_runs_started": total_runs_started,
                "pair": worst_key},
        detail=("OK" if passed else
                f"max(n_runs_confirmed)={max_confirmed} ({worst_key}) > "
                f"total_runs_started={total_runs_started} — Diagnose-Cache oder Coverage-Ledger "
                "ist zurueckgesetzt/verloren gegangen (#1068, vgl. #1064)."),
        severity="high",
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
    #
    # Issue #947/#1113 (Katalog #960) — der gemeldete Faktor rechnet gegen ``cap_s`` (NICHT gegen
    # ``hard_threshold_s`` — der Meldungstext benannte bislang faelschlich Letzteres als "die
    # Zeitbox", waehrend HIER durch ``cap_s`` geteilt wird: jeder gemeldete Wert erschien dadurch
    # exakt um den Faktor ``hard_multiple`` (Default 3.0) zu gross gegenueber der im Text genannten
    # Referenz, bit-genau ueber 16 Offender reproduziert, B-9). ``magnitude_offenders`` bleibt der
    # flache Faktor (Rueckwaertskompatibilitaet mit der Fail-Fast-Pair-Konvention, siehe
    # ``sweep._offending_pairs_for_fail_fast_check``); ``max_holding_time_s`` je Offender wandert
    # zusaetzlich in ``provenance`` (unten), damit kein Leser mehr zurueckrechnen muss.
    magnitude_offenders = {
        f"{r.get('strategy')}/{r.get('symbol')}": round(r["max_holding_time_s"] / cap_s, 2)
        for r in study_records
        if r.get("max_holding_time_s") is not None and r["max_holding_time_s"] > hard_threshold_s
    }
    magnitude_offenders_max_holding_time_s = {
        f"{r.get('strategy')}/{r.get('symbol')}": r["max_holding_time_s"]
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
            "magnitude_hard_threshold_s": hard_threshold_s,
            "magnitude_offenders": magnitude_offenders or None,
            # Issue #947/#1113 (Katalog #960) — max_holding_time_s im Klartext je Offender, damit
            # kein Leser aus dem Faktor und einem (moeglicherweise falsch referenzierten) Nenner
            # zurueckrechnen muss.
            "magnitude_offenders_max_holding_time_s": magnitude_offenders_max_holding_time_s or None,
        }
    detail_parts = []
    if fraction_offenders:
        detail_parts.append(
            f"Anteils-Ast: {len(fraction_offenders)} Study/Studies überschreiten den Zeitbox-"
            f"Study-Toleranzwert ({study_tolerance}) auf TRADE-Ebene: {fraction_offenders} — Bug im "
            "Exit-Pfad (HourlyStrategyBase erzwingt den Zeit-Exit nicht durchgängig), keine "
            "tolerierbare Ausführungslatenz mehr.")
    if magnitude_offenders:
        # Issue #947/#1113 (Katalog #960) — Root-Cause: dieser Text nannte bislang
        # ``hard_threshold_s`` als "die Zeitbox", waehrend ``magnitude_offenders`` (oben) durch
        # ``cap_s`` teilt — jeder gemeldete Faktor war dadurch exakt um ``hard_multiple`` (3.0) zu
        # gross gegenueber der im Text genannten Referenz. Der Text nennt jetzt BEIDE Groessen
        # explizit und benennt den tatsaechlich verwendeten Nenner.
        detail_parts.append(
            f"Magnituden-Ast (#1036): {len(magnitude_offenders)} Study/Studies mit einer "
            f"Einzelposition > {hard_multiple}x der Zeitbox+Slack (cap_s={cap_s:.0f}s = "
            f"({max_bars_in_trade_cap:.0f}+{timebox_execution_slack_bars:.0f}) Bars × "
            f"{bar_seconds:.0f}s; harte Schwelle hard_threshold_s={hard_threshold_s:.0f}s): "
            f"{magnitude_offenders} (Vielfaches von cap_s — NICHT von hard_threshold_s — je Study; "
            f"max_holding_time_s je Offender: {magnitude_offenders_max_holding_time_s}) — "
            "ökonomisch untragbar unabhängig vom gepoolten Anteil (Pitfall #358).")
    # Issue #1063 (Pitfall #370) — ``actual`` traegt seit diesem Fix BEIDE Aeste (nicht nur den
    # Anteils-Ast) in der von ``sweep._offending_pairs_for_fail_fast_check`` erwarteten Pair-
    # Konvention (``{"<strategy>/<symbol>": <wert>}``). Root-Cause #1063: vor diesem Fix war
    # ``actual`` NUR der Anteils-Ast (``fraction_offenders or None``) — feuerte ausschliesslich der
    # Magnituden-Ast (#1036), blieb ``actual=None``, obwohl der Check FAILt (``passed=False`` via
    # ``magnitude_offenders``). Der Fail-Fast-Parser erwartet ein dict und faellt sonst auf den
    # konservativen "Struktur unbekannt ⇒ global abbrechen"-Zweig, der
    # ``fail_fast_min_offending_studies`` (#1016, Pitfall #349) NIE auswertet — genau die
    # Ein-Symbol-Konfiguration, fuer die diese Schwelle gebaut wurde. Ein Study-Key, der in BEIDEN
    # Aesten offendiert, behaelt den (interpretierbareren) Anteilswert; ein Key NUR im Magnituden-
    # Ast traegt sein Vielfaches der Zeitbox.
    combined_actual = dict(magnitude_offenders)
    combined_actual.update(fraction_offenders)
    return InvariantResult(
        name="check_holding_time_cap",
        passed=passed,
        expected=f"Anteil zeitbox-verletzender Trades <= {study_tolerance} UND "
                 f"max_holding_time_s <= {hard_multiple}x cap je Study",
        actual=combined_actual or None,
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
                                  max_ratio: float = 10.0,
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
    rastet (Pitfall #285/#286 in AGENTS.md).

    Issue #1070 (Pitfall #369, zweite Wiederkehr nach #1055 ``check_reward_dynamic_range``) —
    ZWEITE, symmetrische Schranke ``max_ratio`` (Default 10.0, entspricht
    ``optimizer.json['stop_distance_max_ratio']``): ein Verhältnis, das WEIT ÜBER dem konfigurierten
    Abstand liegt (Beweis B-3 im #866-Katalog: bis 36,66), ist genauso ein Beleg, dass der Stop
    keine kalibrierte Risikogrösse ist — nur in der ANDEREN Richtung (der Stop begrenzt den Verlust
    gar nicht, statt ihn zu früh zu kappen). Root-Cause: die Vor-#1070-Fassung prüfte ausschliesslich
    ``ratio < min_ratio`` — bei ``ratio=36.66`` blieb ``passed=True``, ``actual=None`` (der Wert
    erschien in KEINEM Artefakt), obwohl der Check in ``optimizer.json['fail_fast_invariants']``
    steht. Dieselbe Fehlerklasse wie #1055 (dort: ``check_reward_dynamic_range`` prüfte nur "std zu
    klein" und war auf eine BTC-Explosion blind) — die Regel "ein Verhältnis-Check braucht BEIDE
    Schranken" gehört in AGENTS.md (Pitfall #369).

    Issue #1070 Fix Punkt 2 — ``actual`` trägt seit diesem Fix IMMER die gemessenen Verhältnisse
    ALLER Studies mit ausreichender Stop-Exit-Evidenz (nicht mehr nur die Offender) — der Wert ist
    damit im Report sichtbar, UND ``sweep._offending_pairs_for_fail_fast_check`` kann ihn parsen
    (#1063), auch wenn nur der obere Ast FAILt. Ein PASSender Check trägt ``actual`` trotzdem
    (Fix Punkt 3): ``passed=True, actual=None`` bedeutet jetzt eindeutig "nichts gemessen"
    (``n_studies_measured=0``), während ``passed=True, actual={...}`` "gemessen, nichts auffällig"
    bedeutet — vorher waren beide Zustände identisch (``actual=None``).

    Issue #1097 (Katalog #930) — konsumierte diesen Check AUSSCHLIESSLICH ueber die GEPOOLTEN,
    trade-gewichteten Felder (``oos_gross_loss_mean_bps_trailing_stop_pooled``), NICHT die
    medianbasierten — Kommensurabilitaets-Argument: die Stichprobengrösse
    ``oos_n_trailing_stop_losses`` (SUMME über Trials) sei nur mit einer ebenso gepoolten Mittelwert-
    Grösse kommensurabel (Pitfall #304).

    Issue #972/#1126 (Pitfall #405 in AGENTS.md) — der gepoolte Mittelwert ist SELBST ein
    ungeschuetztes arithmetisches Mittel ueber eine potenziell extrem schiefe bps-Verteilung (kein
    Notional-/Ausreisser-Boden im Zaehler). Der Zaehler ist deshalb seit diesem Fix
    ``gross_loss_median_bps_trailing_stop`` (Median-der-Trial-Mediane, robust) statt des gepoolten
    Mittels — ``oos_n_trailing_stop_losses`` bleibt unveraendert der Stichprobengroessen-Gate (ein
    reiner Zaehler, unabhaengig davon, ob der Zaehler Mittel oder Median ist). Der gepoolte
    Mittelwert bleibt in ``actual``/``provenance`` als Vergleichsgroesse sichtbar (Pitfall #405:
    Mittel und Median nie ohne Ausreisser-Telemetrie gegeneinanderstellen).

    Issue #983/#1137 (Katalog #986) — severity ``blocking`` (vorher ``high``): dieser Check steht
    seit langem in ``optimizer.json['fail_fast_invariants']``, OHNE dass sein severity-Feld das
    ausgewiesen hatte — zwei entkoppelte Taxonomien fuer "darf einen Sweep abbrechen" (Pitfall #410
    in AGENTS.md). ``severity='blocking'`` IMPLIZIERT seither fail-fast-Faehigkeit; siehe
    ``check_fail_fast_invariants_are_blocking`` fuer den neuen Regressionswaechter gegen ein
    erneutes Auseinanderlaufen."""
    candidates = [
        r for r in study_records
        if r.get("atr_median_bps")
        and r.get("atr_trailing_multiplier_median") is not None
        and (r.get("gross_loss_median_bps_trailing_stop") is not None
             or r.get("oos_gross_loss_mean_bps_pooled") is not None)
    ]
    if not candidates:
        # Issue #995/#1147 (Pitfall #413) — ``passed=None``/``evaluable=False`` statt ``passed=
        # True``: dieser ``blocking``-Check konnte seine Grundgesamtheit gar nicht erst herstellen,
        # das ist ein Befund fuer die POST-HOC-Zulaessigkeitsbewertung, kein stiller PASS.
        return InvariantResult(
            name="check_effective_stop_distance",
            passed=None,
            expected=f"{min_ratio} <= Median-Bruttoverlust (Stop-Exits) / (k_median · ATR_median) "
                     f"<= {max_ratio} je Study",
            actual=None,
            detail="Keine Studies mit Exit-Telemetrie (Issue #899) — nicht auswertbar "
                   "(n_candidates=0, n_measured=0).",
            severity="blocking",
            inconclusive=True,
            evaluable=False,
            evaluability={"evaluable": False, "inconclusive_reason": "no_exit_telemetry",
                          "n_candidates": 0, "n_measured": 0},
        )
    offenders_low: dict[str, float] = {}
    offenders_high: dict[str, float] = {}
    all_ratios: dict[str, dict] = {}
    inconclusive_studies: dict[str, int] = {}
    with_data: list[dict] = []
    for r in candidates:
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        n_stop_exits = int(r.get("oos_n_trailing_stop_losses") or 0)
        loss_bps = r.get("gross_loss_median_bps_trailing_stop")
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
        ratio = round(float(loss_bps) / configured_distance_bps, 4)
        _pooled_mean_loss = r.get("oos_gross_loss_mean_bps_trailing_stop_pooled")
        _pooled_ratio = (
            round(float(_pooled_mean_loss) / configured_distance_bps, 4)
            if _pooled_mean_loss is not None else None)
        all_ratios[key] = {"ratio_median": ratio, "ratio_pooled_mean": _pooled_ratio}
        if ratio < min_ratio:
            offenders_low[key] = ratio
        elif ratio > max_ratio:
            offenders_high[key] = ratio
    offenders = {**offenders_low, **offenders_high}
    passed = not offenders
    if not with_data:
        # Issue #995/#1147 (Pitfall #413) — dieselbe Regel wie oben: fehlende Evidenz auf einem
        # ``blocking``-Check ist kein PASS.
        return InvariantResult(
            name="check_effective_stop_distance",
            passed=None,
            expected=f"{min_ratio} <= Median-Bruttoverlust (Stop-Exits) / (k_median · ATR_median) "
                     f"<= {max_ratio} je Study",
            actual=inconclusive_studies if inconclusive_studies else None,
            severity="blocking",
            detail=f"Keine Study mit >= {min_trailing_stop_exits} nachweislichen TRAILING_STOP-"
                   "Exits (#1034 Voraussetzung) — INCONCLUSIVE statt eines Urteils auf zu kleiner "
                   f"Stichprobe (n_candidates={len(candidates)}, n_measured=0).",
            inconclusive=True,
            evaluable=False,
            evaluability={"evaluable": False, "inconclusive_reason": "insufficient_trailing_stop_exits",
                          "n_candidates": len(candidates), "n_measured": 0},
        )
    detail_parts = []
    if offenders_low:
        detail_parts.append(
            f"{len(offenders_low)} Study/Studies UNTERSCHREITEN {min_ratio}: {offenders_low} — der "
            "Stop reagiert nicht auf seinen eigenen Multiplikator (Pitfall #286) und rastet "
            "vermutlich auf der ATR-Schätzung statt auf dem Preis-Extremum (Pitfall #285, #897).")
    if offenders_high:
        detail_parts.append(
            f"{len(offenders_high)} Study/Studies ÜBERSCHREITEN {max_ratio}: {offenders_high} — der "
            "realisierte Verlust ist von der konfigurierten Stopdistanz UNABHÄNGIG (der Stop "
            "begrenzt den Verlust nicht, unabhängig vom Multiplikator, #1069/#1070).")
    return InvariantResult(
        name="check_effective_stop_distance",
        passed=passed,
        expected=f"{min_ratio} <= Median-Bruttoverlust (Stop-Exits) / (k_median · ATR_median) "
                 f"<= {max_ratio} je Study",
        actual=all_ratios,
        severity="blocking",
        detail=("OK (n_studies_measured=%d)" % len(with_data) if passed else " ".join(detail_parts)),
        evaluable=True,
        evaluability={"evaluable": True, "inconclusive_reason": None,
                      "n_candidates": len(candidates), "n_measured": len(with_data)},
    )


def check_stop_cost_ratio(study_records: list[dict], *,
                          round_trip_cost_bps_by_symbol: dict[str, float],
                          min_stop_to_cost_ratio: float = 3.0) -> InvariantResult:
    """Issue #1072 (Wiederkehr #1050/#1051) — der konfigurierte Stop-Abstand
    (``k_median · ATR_median``, bps) muss mindestens ``min_stop_to_cost_ratio`` (Default 3.0,
    ``tournament.json['min_stop_to_cost_ratio']``) mal die Round-Trip-Kosten (``c_rt``, bps) dieses
    Symbols betragen. Root-Cause: die notwendige Bedingung ``E[MFE] > d + c_rt`` ist strukturell
    verletzt, wenn die Stopdistanz selbst schon in der Grössenordnung der Kosten liegt — eine
    Position kann den Stop nicht überleben, bevor die Kosten sie auffressen, UNABHÄNGIG vom Signal
    (Beweis B-3/E-1 im #866-Katalog: DynamicBreakout mit ``d = 0,85 · c_rt``, HourlyMeanReversion
    ``0,87 · c_rt`` — beide mit negativer Expectancy).

    ``round_trip_cost_bps_by_symbol`` — vom Aufrufer aufgelöst (``backtest_runner._read_default_
    round_trip_cost_bps``, dieselbe Auflösungskette wie das kostenrelative Expectancy-Gate, #684/
    #775); ein Symbol ohne Eintrag wird übersprungen (INCONCLUSIVE für dieses Symbol, kein FAIL auf
    einer unbekannten Kostenbasis)."""
    candidates = [
        r for r in study_records
        if r.get("atr_median_bps") and r.get("atr_trailing_multiplier_median") is not None
        and r.get("symbol") in (round_trip_cost_bps_by_symbol or {})
    ]
    if not candidates:
        return InvariantResult(
            name="check_stop_cost_ratio",
            passed=True,
            expected=f"atr_median_bps · atr_trailing_multiplier_median >= "
                     f"{min_stop_to_cost_ratio} · c_rt je Study",
            actual=None,
            severity="high",
            detail="Keine Study mit ATR-Telemetrie UND bekannter Kostenbasis (c_rt) — "
                   "nicht auswertbar.",
        )
    offenders: dict[str, float] = {}
    all_ratios: dict[str, float] = {}
    for r in candidates:
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        c_rt = round_trip_cost_bps_by_symbol[r["symbol"]]
        if not c_rt or c_rt <= 0:
            continue
        configured_distance_bps = float(r["atr_trailing_multiplier_median"]) * float(r["atr_median_bps"])
        ratio = round(configured_distance_bps / c_rt, 4)
        all_ratios[key] = ratio
        if ratio < min_stop_to_cost_ratio:
            offenders[key] = ratio
    passed = not offenders
    return InvariantResult(
        name="check_stop_cost_ratio",
        passed=passed,
        expected=f"atr_median_bps · atr_trailing_multiplier_median >= "
                 f"{min_stop_to_cost_ratio} · c_rt je Study",
        actual=all_ratios if all_ratios else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies mit einer Stopdistanz unter "
                f"{min_stop_to_cost_ratio}x der Round-Trip-Kosten: {offenders} — die Position kann "
                "den Stop strukturell nicht überleben, bevor die Kosten sie auffressen, "
                "unabhängig vom Signal (#1072, Wiederkehr #1050/#1051)."),
    )


def check_store_scan_coherence(
    store_scan: dict, n_studies: int, *, purge_provenance_documented: bool = False,
) -> InvariantResult:
    """Issue #1004/#1156 (Katalog #1170, P1) — Abnahmemessung fuer die dreiwertige
    ``report.py``-Store-Scan-Klassifikation (``n_own``/``n_foreign``/``n_unclassifiable``, siehe
    dortiger Docstring). Root-Cause: ``n_own`` war vorher ``n_studies_in_store − n_foreign`` — ein
    reiner Komplement-Zaehler, der jede nicht-ladbare/trial-lose Study STILL als "eigen" zaehlte.

    FAIL (severity ``high``), wenn:
      1. ``n_own != n_studies`` (die Anzahl der positiv als eigen nachgewiesenen Store-Eintraege
         weicht von der Anzahl der TATSAECHLICH im Report gefuehrten Studies ab — ein Report kann
         nur genau die Studies zeigen, die der Store-Scan auch als eigen bestaetigt), ODER
      2. ``n_unclassifiable > 0``, OHNE dass ``purge_provenance_documented=True`` explizit belegt,
         dass die nicht klassifizierbaren Eintraege einem dokumentierten Purge-Vorgang zuzuordnen
         sind (Default ``False`` — ein unclassifiable Rest OHNE Erklaerung ist ein Befund, kein
         hinnehmbarer Normalzustand)."""
    n_own = store_scan.get("n_own")
    n_unclassifiable = store_scan.get("n_unclassifiable")
    reasons = []
    if n_own != n_studies:
        reasons.append(f"n_own({n_own}) != n_studies({n_studies}).")
    if (n_unclassifiable or 0) > 0 and not purge_provenance_documented:
        reasons.append(
            f"n_unclassifiable({n_unclassifiable}) > 0 ohne dokumentierte Purge-Provenienz.")
    passed = not reasons
    return InvariantResult(
        name="check_store_scan_coherence",
        passed=passed,
        expected="store_scan.n_own == n_studies UND (n_unclassifiable == 0 ODER "
                 "purge_provenance_documented=True)",
        actual={"store_scan": store_scan, "n_studies": n_studies,
                "purge_provenance_documented": purge_provenance_documented},
        severity="high",
        detail=("OK" if passed else " ".join(reasons)),
    )


def check_cost_basis_resolution(
    study_records: list[dict], *,
    atr_floor_bps_by_symbol: dict[str, float],
    round_trip_cost_bps_by_symbol: dict[str, float],
    resolution_errors: dict[str, dict[str, str]] | None = None,
) -> InvariantResult:
    """Issue #998/#1150 (Katalog #1170) — ``report._atr_floor_bps_by_symbol``/``_round_trip_cost_
    bps_by_symbol`` fingen VOR diesem Fix jeden Fehler je Symbol stumm ab (``except Exception:
    continue``) — ein leeres Ergebnis-Dict war dadurch NICHT von "die Kostenbasis bindet bei
    keinem Symbol" (die tatsaechliche #1096-Abnahme) unterscheidbar, sondern bedeutete "die
    Kostenbasis ist fuer dieses Symbol UNBEKANNT". Root-Cause typischerweise
    ``InstrumentMetadataIncompleteError`` (#898) — ein Symbol ohne aufgeloeste Asset-Class in
    ``instrument_map.json``, sobald ``atr_floor_bps_by_asset_class``/``spread_bps_by_asset_class``
    konfiguriert sind.

    FAIL (severity ``high``), wenn fuer ein Symbol mit >= 1 Study WEDER der ATR-Floor NOCH die
    Round-Trip-Kostenbasis (c_rt) auflösbar ist — beide Groessen sind unabhaengige Auflösungspfade
    (``resolve_atr_floor_bps`` bzw. ``_read_default_round_trip_cost_bps``), ein Symbol mit
    mindestens EINER aufgeloesten Groesse ist kein Befund fuer DIESEN Check (die jeweils andere
    Kostenbasis fehlt dann isoliert und ist bereits ueber ``check_stop_cost_ratio``/
    ``check_atr_scale_homogeneity``s eigene "nicht auswertbar"-Pfade sichtbar).

    ``resolution_errors`` (optional, ``report._build_report``s ``cross_study.cost_model_
    resolution.errors``) — wird 1:1 in ``actual``/``provenance`` durchgereicht, damit die
    KONKRETE Fehlermeldung (nicht nur "fehlt") je Symbol im Report sichtbar ist."""
    symbols_with_studies = sorted({r.get("symbol") for r in study_records if r.get("symbol")})
    if not symbols_with_studies:
        return InvariantResult(
            name="check_cost_basis_resolution",
            passed=True,
            expected="ATR-Floor ODER Round-Trip-Kostenbasis (c_rt) fuer jedes Symbol mit >= 1 "
                     "Study aufloesbar",
            actual=None,
            severity="high",
            detail="Keine Studies — nicht auswertbar.",
        )
    offenders: dict[str, dict] = {}
    for symbol in symbols_with_studies:
        atr_resolved = symbol in (atr_floor_bps_by_symbol or {})
        c_rt_resolved = symbol in (round_trip_cost_bps_by_symbol or {})
        if not atr_resolved and not c_rt_resolved:
            offenders[symbol] = {
                "atr_floor_resolved": False, "round_trip_cost_resolved": False,
                "errors": (resolution_errors or {}).get(symbol) or {},
            }
    passed = not offenders
    return InvariantResult(
        name="check_cost_basis_resolution",
        passed=passed,
        expected="ATR-Floor ODER Round-Trip-Kostenbasis (c_rt) fuer jedes Symbol mit >= 1 Study "
                 "aufloesbar",
        actual=offenders or None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} Symbol(e) ohne aufloesbare Kostenbasis (weder ATR-Floor noch "
                f"c_rt): {sorted(offenders)} — jede darauf aufbauende Stop-/Kosten-Invariante "
                "(check_stop_cost_ratio, check_atr_scale_homogeneity) ist fuer diese Symbole "
                "'nicht auswertbar', nicht 'der Floor bindet nicht' (#998/#1150)."),
        provenance={"resolution_errors": resolution_errors} if resolution_errors else None,
    )


def _resolve_causal_hypothesis_state(
    candidates: list[dict], *, anchor_control_run_available: bool = False,
) -> str:
    """Issue #974/#1128 (Pitfall #403 in AGENTS.md) — leitet den Kausal-Zustand AUS DEN GEMESSENEN
    Feldern her, statt ihn als Konstante zu behaupten. Eine Invariante darf keine Ursache nennen,
    fuer die sie keinen Eingang hat.

    ``ANCHOR_REFUTED`` — nur, wenn ``#1092`` (Anker-Aufloesung, bereits gemergt) UND ein dedizierter
    Kontrolllauf vorliegen; ein Kontrolllauf ist ein Sitzungs-Ereignis, das keine reine Report-
    Kohorte automatisiert nachweisen kann — deshalb der explizite, per Default False gesetzte
    Parameter ``anchor_control_run_available`` (vom Aufrufer zu setzen, sobald ein solcher Lauf
    tatsaechlich vorliegt).

    ``LATENCY_REFUTED`` — nur, wenn ``stop_exit_lag_bars`` UEBER ALLE Kandidaten-Studies ≡ 0 UND die
    Absetzen-zu-Fill-Latenz SEPARAT gemessen ist (``#976/#1130``: ``n_trailing_stop_exits_with_
    fill_lag_telemetry > 0`` in mindestens einer Study) — vor #1130 war diese zweite Bedingung nie
    erfuellt, der Zustand war strukturell immer ``UNRESOLVED``.

    ``UNRESOLVED`` — Default, wenn keine der beiden Bedingungen erfuellt ist."""
    if anchor_control_run_available:
        return "ANCHOR_REFUTED"
    if not candidates:
        return "UNRESOLVED"
    lag_values = [r.get("oos_stop_exit_lag_bars") for r in candidates]
    all_lag_zero = bool(lag_values) and all(v is not None and float(v) == 0.0 for v in lag_values)
    fill_lag_measured = any(
        int(r.get("n_trailing_stop_exits_with_fill_lag_telemetry") or 0) > 0 for r in candidates)
    if all_lag_zero and fill_lag_measured:
        return "LATENCY_REFUTED"
    return "UNRESOLVED"


def check_trailing_stop_risk_calibration_acceptance(
    study_records: list[dict], *,
    min_spearman: float = 0.3,
    ratio_band: tuple[float, float] = (0.8, 3.0),
    min_ratio_in_band_fraction: float = 0.8,
    max_trailing_stop_exit_share: float = 0.35,
    min_trailing_stop_exits: int = 30,
    anchor_control_run_available: bool = False,
) -> InvariantResult:
    """Issue #950/#1116 (Katalog #960) — die verbindliche ABNAHMEMESSUNG für die #1092/#1094-
    Hypothese (Anker/Auslöser-Auflösung + monotone Ratsche): B-11 (39 saubere Studies vor dem Fix)
    zeigte Spearman(k·ATR, realisierter Stop-Verlust) = −0,6036 (der Stop reagierte INVERS auf
    seinen eigenen Multiplikator — k·ATR variierte Faktor 28,3, der realisierte Verlust nur Faktor
    3,23), und TRAILING_STOP war mit 46,68 % von 825 064 Round-Trips der häufigste Exit-Grund — der
    Trailing-Stop war keine kalibrierte Risikogrösse.

    DREI Kriterien, ALLE müssen bestehen (auszuwerten NACH ``simulation_semantics_version`` 4→5 +
    Pflicht-Purge + einem echten Re-Run auf denselben Symbolen, siehe Issue #952/#1118):
    1. ``Spearman(k_median·ATR_median, oos_gross_loss_mean_bps_trailing_stop_pooled)`` >=
       ``min_spearman`` (Default 0,3) über alle Studies mit >= ``min_trailing_stop_exits`` (Default
       30) nachweislichen TRAILING_STOP-Exits — der realisierte Verlust muss POSITIV mit der
       konfigurierten Distanz korrelieren (ein grösserer Multiplikator ⇒ ein grösserer realisierter
       Verlust), statt der vorherigen INVERSEN Korrelation.
    2. ``realized_stop_loss_ratio`` (``report._study_record``, derselbe Quotient wie
       ``check_effective_stop_distance``) liegt für >= ``min_ratio_in_band_fraction`` (Default 80 %)
       der Studies im Band ``ratio_band`` (Default ``[0.8, 3.0]``) — eine ENGERE, für diese
       #1092/#1094-Abnahme spezifische Bandbreite als die permanente
       ``check_effective_stop_distance``-Schranke (``[0.4, 10.0]``).
    3. Der GEPOOLTE Anteil TRAILING_STOP an allen Exits (über ALLE Studies summiert, NICHT je Study
       gemittelt — dieselbe Grundgesamtheit wie B-11s 825 064 Round-Trips) liegt unter
       ``max_trailing_stop_exit_share`` (Default 35 %).

    Issue #974/#1128 (Pitfall #403 in AGENTS.md) — dieser Check MISST drei Akzeptanzkriterien; er
    hat KEINEN Eingang für die Ursache eines Scheiterns. Der Meldungstext benennt seit diesem Fix
    ausschliesslich das Gemessene ("Kalibrierung nicht erreicht"); die Ursachenzuweisung
    (Anker/Auslöser-Auflösung widerlegt vs. Ein-Bar-Ausführungslatenz die verbleibende Erklärung)
    steht separat im Feld ``causal_hypothesis_state`` (siehe ``_resolve_causal_hypothesis_state``),
    dessen Wert aus TATSAECHLICH gemessenen Feldern hergeleitet wird, nicht als Konstante behauptet.

    Issue #972/#1126 (Pitfall #405 in AGENTS.md) — Kriterium 1 (Spearman) konsumiert seit diesem Fix
    ``gross_loss_median_bps_trailing_stop`` (robuster Median-der-Trial-Mediane) statt des
    ungeschuetzten gepoolten Mittels ``oos_gross_loss_mean_bps_trailing_stop_pooled``; Kriterium 2
    (``realized_stop_loss_ratio``) ist bereits ueber ``report._study_record`` median-basiert (siehe
    dortigen Fix).

    ``INCONCLUSIVE`` (statt eines Urteils), wenn weniger als 3 Studies mit ausreichender
    Stop-Exit-Evidenz für BEIDE Eingangsgrössen der Spearman-Korrelation vorliegen — dieselbe
    Untergrenze wie ``reward._spearman_rank_correlation`` (Rangkorrelation ist unter 3 Punkten nicht
    definierbar)."""
    from automation.optimizer.reward import _spearman_rank_correlation

    candidates = [
        r for r in study_records
        if r.get("atr_median_bps")
        and r.get("atr_trailing_multiplier_median") is not None
        and r.get("gross_loss_median_bps_trailing_stop") is not None
        and int(r.get("oos_n_trailing_stop_losses") or 0) >= min_trailing_stop_exits
    ]
    k_atr_values: list[float] = []
    loss_values: list[float] = []
    ratio_by_study: dict[str, float] = {}
    for r in candidates:
        k_atr = float(r["atr_trailing_multiplier_median"]) * float(r["atr_median_bps"])
        if k_atr <= 0:
            continue
        loss = float(r["gross_loss_median_bps_trailing_stop"])
        k_atr_values.append(k_atr)
        loss_values.append(loss)
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        ratio_by_study[key] = round(loss / k_atr, 4)

    total_trailing_stop = sum(
        int((r.get("exit_reason_histogram") or {}).get("TRAILING_STOP", 0)) for r in study_records)
    total_exits = sum(int(r.get("oos_total_trades_with_exit_telemetry") or 0) for r in study_records)
    trailing_stop_share = (total_trailing_stop / total_exits) if total_exits > 0 else None

    expected = (f"Spearman(k·ATR, realisierter Verlust) >= {min_spearman} UND "
                f"realized_stop_loss_ratio in {list(ratio_band)} für >= "
                f"{min_ratio_in_band_fraction:.0%} der Studies UND TRAILING_STOP-Anteil < "
                f"{max_trailing_stop_exit_share:.0%}")

    causal_hypothesis_state = _resolve_causal_hypothesis_state(
        candidates, anchor_control_run_available=anchor_control_run_available)
    if len(k_atr_values) < 3:
        # Issue #995/#1147 (Pitfall #413) — ``passed=None``/``evaluable=False``: eine zu kleine
        # Stichprobe ist kein PASS, auch nicht bei severity='high' (nur die BLOCKING-Konsequenz in
        # ``_compute_decision_admissible``/``confirm.py`` ist auf severity='blocking' beschraenkt,
        # die Auswertbarkeits-Auskunft selbst gilt unabhaengig vom Schweregrad).
        return InvariantResult(
            name="check_trailing_stop_risk_calibration_acceptance",
            passed=None,
            expected=expected,
            actual={"trailing_stop_exit_share": round(trailing_stop_share, 4)
                    if trailing_stop_share is not None else None,
                    "causal_hypothesis_state": causal_hypothesis_state},
            severity="high",
            inconclusive=True,
            evaluable=False,
            evaluability={"evaluable": False, "inconclusive_reason": "fewer_than_3_studies_with_evidence",
                          "n_candidates": len(candidates), "n_measured": len(k_atr_values)},
            detail=f"Nur {len(k_atr_values)} Study/Studies mit >= {min_trailing_stop_exits} "
                   "nachweislichen TRAILING_STOP-Exits (< 3) — Spearman-Rangkorrelation nicht "
                   "definierbar, INCONCLUSIVE statt eines Urteils.",
        )

    spearman = _spearman_rank_correlation(k_atr_values, loss_values)
    ratios = list(ratio_by_study.values())
    n_in_band = sum(1 for r in ratios if ratio_band[0] <= r <= ratio_band[1])
    fraction_in_band = round(n_in_band / len(ratios), 4)

    reasons = []
    if spearman is None or spearman < min_spearman:
        spearman_display = f"{spearman:.4g}" if spearman is not None else "undefiniert"
        reasons.append(
            f"Spearman(k·ATR, Verlust)={spearman_display} < {min_spearman} — der realisierte "
            "Stop-Verlust reagiert nicht (hinreichend) positiv auf seinen eigenen Multiplikator.")
    if fraction_in_band < min_ratio_in_band_fraction:
        reasons.append(
            f"nur {fraction_in_band:.1%} der Studies (statt >= {min_ratio_in_band_fraction:.0%}) "
            f"liegen mit realized_stop_loss_ratio in {list(ratio_band)}.")
    if trailing_stop_share is None or trailing_stop_share >= max_trailing_stop_exit_share:
        share_display = f"{trailing_stop_share:.2%}" if trailing_stop_share is not None else "undefiniert"
        reasons.append(f"TRAILING_STOP-Anteil={share_display} >= {max_trailing_stop_exit_share:.0%}.")

    passed = not reasons
    return InvariantResult(
        name="check_trailing_stop_risk_calibration_acceptance",
        passed=passed,
        expected=expected,
        actual={
            "spearman_k_atr_vs_loss": round(spearman, 4) if spearman is not None else None,
            "fraction_studies_ratio_in_band": fraction_in_band,
            "trailing_stop_exit_share": round(trailing_stop_share, 4)
                if trailing_stop_share is not None else None,
            "n_studies_measured": len(k_atr_values),
            # Issue #974/#1128 — die Ursachenzuweisung steht HIER, getrennt vom Meldungstext, und
            # wird aus gemessenen Feldern hergeleitet (siehe _resolve_causal_hypothesis_state).
            "causal_hypothesis_state": causal_hypothesis_state,
        },
        severity="high",
        # Issue #974/#1128 (Pitfall #403 in AGENTS.md) — der Text nennt nur das GEMESSENE, keine
        # Kausalaussage, fuer die dieser Check keinen Eingang hat.
        detail=("OK — die drei Abnahmekriterien sind erfuellt." if passed else
                "Kalibrierung nicht erreicht: " + " ".join(reasons)),
        provenance={"ratio_by_study": ratio_by_study} if ratio_by_study else None,
        evaluable=True,
        evaluability={"evaluable": True, "inconclusive_reason": None,
                      "n_candidates": len(candidates), "n_measured": len(k_atr_values)},
    )


def check_stop_loss_vs_bar_range(
    study_records: list[dict], *,
    bar_range_ratio_band: tuple[float, float] = (0.7, 1.4),
    min_realized_stop_loss_ratio: float = 5.0,
    min_trailing_stop_exits: int = 30,
) -> InvariantResult:
    """Issue #953/#1119 (Katalog #960) — Ein-Bar-Ausführungslatenz als eigentliche
    Verlustuntergrenze.

    Symptom (B-11): innerhalb eines Symbols ist der realisierte Stop-Verlust nahezu konstant,
    während die Stopdistanz um Faktor 20 variiert — mit "Verlust = Stopdistanz + Überschiessen"
    nicht vereinbar, wohl aber mit "Verlust = adverse Bewegung EINER Bar", weil der Exit ein
    Marktauftrag auf der FOLGE-Bar ist (der Stop ist ein Bar-Schluss-Signal, kein echter
    ``StopMarketOrder`` in der Engine — #1092B, bewusst zurückgestellt; zwischen Auslösung und Fill
    liegt mindestens eine volle Bar).

    FAIL (severity ``blocking``) je Study, wenn BEIDE Bedingungen gleichzeitig gelten:
    1. ``oos_gross_loss_mean_bps_trailing_stop_pooled / bar_range_median_bps`` liegt in
       ``bar_range_ratio_band`` (Default ``[0.7, 1.4]``) — der Verlust liegt in derselben
       Grössenordnung wie EINE Bar-Spanne.
    2. ``realized_stop_loss_ratio`` (der Quotient aus ``report._study_record``, derselbe wie
       ``check_effective_stop_distance``) ``> min_realized_stop_loss_ratio`` (Default 5.0) — der
       Verlust ist GLEICHZEITIG ein grosses Vielfaches der KONFIGURIERTEN Stopdistanz (k·ATR).

    Beide gemeinsam beweisen: der Verlust ist LATENZ-, nicht STOP-getrieben — jede
    Stop-Parametrisierung (k, ATR-Floor, #951/#1117) ist unter dieser Bedingung wirkungslos, weil
    der tatsächliche Exit-Preis von der Bar-Folge-Latenz dominiert wird, nicht vom konfigurierten
    Abstand. ``blocking``, weil dieser Befund JEDE nachgelagerte Stop-Kalibrierung entwertet. Erst
    NACH diesem Befund lohnt sich die Entscheidung über #1092B (eine echte ``StopMarketOrder`` in
    der Engine — teuer, soll nicht auf einer unbelegten Hypothese beruhen).

    Nur Studies mit >= ``min_trailing_stop_exits`` (Default 30) nachweislichen TRAILING_STOP-Exits
    UND definiertem ``bar_range_median_bps`` werden geprüft — ``INCONCLUSIVE`` sonst (dieselbe
    Konvention wie ``check_effective_stop_distance``, kein Urteil auf zu kleiner Stichprobe).

    Issue #973/#1127 (Pitfall #404 in AGENTS.md) — Root-Cause des vormaligen fail-open-Symptoms
    (8/8 Läufe ``passed=True`` mit "nicht auswertbar", ``bar_range_median_bps`` in 112/112 Studies
    ``null``): fehlende Evidenz wurde als PASS gewertet, OHNE strukturiert von einem echten,
    sauberen PASS unterscheidbar zu sein — ``inconclusive=True`` allein war nur im freien
    ``detail``-Text sichtbar. ``passed=True`` bleibt bei fehlender Evidenz bewusst erhalten (dieser
    Check ist ``severity='blocking'`` — ein Abbruch AUF FEHLENDER EVIDENZ waere ein zweiter, eigener
    Fehler, kein Fix), aber ``evaluability`` macht den Zustand seither MASCHINENLESBAR strukturiert
    aus jedem Report ablesbar: ``evaluability.evaluable`` ist die kanonische Auskunft "war dieser
    Check ueberhaupt in der Lage, ein Urteil zu faellen", unabhaengig vom freien Text. Die
    NEUE ``check_exit_telemetry_completeness`` (severity ``high``) ist der Wächter, der den
    112/112-``null``-Fall selbst aktiv erkennt und meldet (dieser Check hier kann das strukturell
    nicht — er hat keinen Eingang, WARUM die Evidenz fehlt)."""
    candidates = [
        r for r in study_records
        if r.get("bar_range_median_bps")
        and r.get("oos_gross_loss_mean_bps_trailing_stop_pooled") is not None
        and r.get("realized_stop_loss_ratio") is not None
        and int(r.get("oos_n_trailing_stop_losses") or 0) >= min_trailing_stop_exits
    ]
    expected = (f"NICHT gleichzeitig: Verlust/Bar-Spanne in {list(bar_range_ratio_band)} UND "
                f"realized_stop_loss_ratio > {min_realized_stop_loss_ratio}")
    if not candidates:
        # Issue #995/#1147 (Pitfall #413) — VERSCHAERFUNG von #973/#1127 (Pitfall #404):
        # ``evaluability.evaluable=False`` allein war nur strukturiert im Nested-Dict sichtbar,
        # waehrend ``_compute_decision_admissible``/``confirm.py``s ``REJECT_STUDY_INVARIANT_
        # BLOCKING`` weiterhin ausschliesslich ``passed`` lasen — ``passed=True`` liess das
        # Ergebnis dort als "OK" durchgehen. ``passed=None`` (statt ``True``) plus dem TOP-LEVEL
        # ``evaluable=False`` schliesst die Luecke, OHNE den Live-Fail-Fast-Abbruchpfad zu
        # beeinflussen (``sweep._first_failing_fail_fast_invariant`` verlangt weiterhin ``passed
        # is False`` explizit, siehe dortiger Kommentar).
        return InvariantResult(
            name="check_stop_loss_vs_bar_range",
            passed=None,
            expected=expected,
            actual=None,
            severity="blocking",
            inconclusive=True,
            evaluable=False,
            evaluability={
                "evaluable": False,
                "inconclusive_reason": "no_study_with_sufficient_stop_exits_and_bar_range_telemetry",
                "n_studies_measured": 0, "n_candidates": 0, "n_measured": 0,
            },
            detail=f"Keine Study mit >= {min_trailing_stop_exits} nachweislichen TRAILING_STOP-"
                   "Exits UND definierter Bar-Spannen-Telemetrie — nicht auswertbar (INCONCLUSIVE, "
                   "siehe evaluability; kein PASS im Sinne einer geprueften Grundgesamtheit).",
        )
    offenders: dict[str, dict] = {}
    all_ratios: dict[str, dict] = {}
    for r in candidates:
        key = f"{r.get('strategy')}/{r.get('symbol')}"
        bar_range = float(r["bar_range_median_bps"])
        if bar_range <= 0:
            continue
        loss = float(r["oos_gross_loss_mean_bps_trailing_stop_pooled"])
        latency_ratio = round(loss / bar_range, 4)
        stop_ratio = float(r["realized_stop_loss_ratio"])
        all_ratios[key] = {"loss_vs_bar_range": latency_ratio, "realized_stop_loss_ratio": stop_ratio}
        if (bar_range_ratio_band[0] <= latency_ratio <= bar_range_ratio_band[1]
                and stop_ratio > min_realized_stop_loss_ratio):
            offenders[key] = all_ratios[key]
    passed = not offenders
    return InvariantResult(
        name="check_stop_loss_vs_bar_range",
        passed=passed,
        expected=expected,
        actual=all_ratios if all_ratios else None,
        severity="blocking",
        evaluable=True,
        evaluability={
            "evaluable": True, "inconclusive_reason": None, "n_studies_measured": len(candidates),
            "n_candidates": len(candidates), "n_measured": len(candidates),
        },
        detail=("OK" if passed else
                f"{len(offenders)} Study/Studies: der Stop-Verlust liegt in der Grössenordnung "
                "EINER Bar-Spanne UND ist gleichzeitig ein grosses Vielfaches der konfigurierten "
                f"Stopdistanz: {offenders} — der Verlust ist latenz-, nicht stopgetrieben (#1119); "
                "jede Stop-Parametrisierung ist unter dieser Bedingung wirkungslos, siehe #1092B."),
        provenance={"ratios_by_study": all_ratios} if all_ratios else None,
    )


def check_exit_telemetry_completeness(
    study_records: list[dict], *,
    telemetry_fields: tuple[str, ...] = (
        "bar_range_median_bps", "atr_median_bps", "atr_raw_median_bps",
        "atr_trailing_multiplier_median", "realized_stop_loss_ratio",
    ),
    min_populated_fraction: float = 0.5,
) -> InvariantResult:
    """Issue #973/#1127 (Pitfall #406 in AGENTS.md) — ein Telemetriefeld, das ueber die GESAMTE
    Grundgesamtheit EXAKT konstant ``null`` ist, ist ein BEFUND, kein Messwert. Root-Cause des
    #1127-Symptoms: ``bar_range_median_bps`` war in 112/112 Studies ``null`` — die Emissionskette
    (``hourly_strategy_base`` → ``backtest_runner`` → ``parsing`` → ``report``) existierte im HEAD,
    war aber in den Lauf-Commits, die die 112 Studies erzeugten, nicht enthalten. Kein Check im HEAD
    hat das SELBST gemeldet — ``check_stop_loss_vs_bar_range`` (dessen EINGANGSGROESSE das Feld ist)
    kann es strukturell nicht: fehlende Evidenz macht IHN inconclusive, nicht das Fehlen selbst zu
    einem Befund.

    Fuer jedes Feld in ``telemetry_fields`` wird der Anteil der Studies mit einem NICHT-``None``-Wert
    gemessen. FAIL (severity ``high``), wenn dieser Anteil unter ``min_populated_fraction`` (Default
    50 %) liegt — ein Feld, das die Emissionskette im HEAD verdrahtet, aber im Lauf nie ankommt,
    alarmiert damit VON SICH AUS, statt erst indirekt ueber einen nachgelagerten INCONCLUSIVE-Check
    aufzufallen. Leere ``study_records`` ⇒ INCONCLUSIVE (kein Urteil ohne Studies)."""
    if not study_records:
        return InvariantResult(
            name="check_exit_telemetry_completeness",
            passed=True,
            expected=f"populated_fraction >= {min_populated_fraction} je Telemetriefeld",
            actual=None,
            severity="high",
            inconclusive=True,
            evaluability={"evaluable": False, "inconclusive_reason": "no_studies",
                         "n_studies_measured": 0},
            detail="Keine Studies — nicht auswertbar.",
        )
    n = len(study_records)
    populated_fraction: dict[str, float] = {}
    offenders: dict[str, float] = {}
    for field in telemetry_fields:
        n_populated = sum(1 for r in study_records if r.get(field) is not None)
        fraction = round(n_populated / n, 4)
        populated_fraction[field] = fraction
        if fraction < min_populated_fraction:
            offenders[field] = fraction
    passed = not offenders
    return InvariantResult(
        name="check_exit_telemetry_completeness",
        passed=passed,
        expected=f"populated_fraction >= {min_populated_fraction} je Telemetriefeld",
        actual=populated_fraction,
        severity="high",
        evaluability={"evaluable": True, "inconclusive_reason": None, "n_studies_measured": n},
        detail=("OK" if passed else
                f"{len(offenders)} Telemetriefeld(er) unter {min_populated_fraction:.0%} befuellt: "
                f"{offenders} — die Emissionskette ist im HEAD verdrahtet, kommt aber im Lauf nicht "
                "an (Pitfall #406 in AGENTS.md: null ueber die gesamte Grundgesamtheit ist ein "
                "Befund, kein Messwert)."),
    )


def check_symbol_bar_quality_cache_availability(
    study_records: list[dict], *, cache_path: str | None = None, cache_found: bool = False,
) -> InvariantResult:
    """Issue #1016/#1168 (Katalog #1170, Pitfall #406-Fehlerklasse in AGENTS.md) — dieselbe
    "konstantes null ist ein Befund, kein Messwert"-Logik wie ``check_exit_telemetry_completeness``
    (oben), hier fuer ``symbol_bar_quality`` (Root-Cause #1168: ``None`` in 28/28 Studies zweier
    Läufe). Zusaetzlich zur reinen Null-Rate benennt dieser Check die KONKRETE Ursachenklasse — der
    erwartete ``symbol_bar_quality.json``-Pfad UND ob die Datei ueberhaupt existierte
    (``sweep.symbol_bar_quality_cache_status``) —, statt nur die Symptomrate zu melden: "Cache-
    Datei fehlt komplett" (Schreibpfad lief nie / falsches ``WORK``) ist eine ANDERE Diagnose als
    "Cache existiert, aber dieses Symbol steht nicht darin" (z. B. ein injizierter Test ohne
    Gate-1-Preflight).

    FAIL (severity ``medium`` — Beobachtbarkeits-, keine Korrektheitsverletzung), wenn mindestens
    eine Study mit ``symbol_bar_quality is None`` existiert. Leere ``study_records`` ⇒ PASS
    (nichts zu pruefen, analog ``check_exit_telemetry_completeness``s Inconclusive-Pfad, hier aber
    ohne Studies trivial erfuellt statt inconclusive — es gibt keine Study, deren Cache fehlen
    koennte)."""
    affected = sorted({
        r.get("symbol") for r in (study_records or [])
        if r.get("symbol_bar_quality") is None and r.get("symbol")
    })
    passed = not affected
    return InvariantResult(
        name="check_symbol_bar_quality_cache_availability",
        passed=passed,
        expected="symbol_bar_quality ist fuer jedes Symbol mit >= 1 Study gesetzt, oder der "
                 "erwartete Cache-Pfad und sein Fehlen sind benannt",
        actual={"symbols_without_symbol_bar_quality": affected, "cache_path": cache_path,
               "cache_found": cache_found} if not passed else None,
        severity="medium",
        detail=("OK" if passed else
                f"{len(affected)} Symbol(e) ohne symbol_bar_quality: {affected}. Erwarteter "
                f"Cache-Pfad: {cache_path} — " +
                ("Datei gefunden, aber Symbol(e) fehlen darin (kein Gate-1-Preflight fuer dieses "
                 "Symbol in diesem Lauf, oder ein veralteter Cache-Stand)."
                 if cache_found else
                 "Datei NICHT gefunden (Schreibpfad lief nie, oder Report liest aus einem "
                 "anderen WORK als der Sweep schrieb, #1168).")),
    )


def check_n_periods_homogeneity(study_records: list[dict], *,
                                max_ratio: float = 6.0,
                                promotion_family_scope: str | None = None) -> InvariantResult:
    """Issue #923 — ``oos_n_periods_median`` (#862) streut je nach Strategie stark selbst
    INNERHALB desselben Symbols (unterschiedliche Handelsfrequenz ⇒ unterschiedlich viele Bars
    mit Rendite ≠ 0) — eine Spannweite von Faktor 11,3 auf demselben Symbol XOM wurde beobachtet.
    ``n_periods`` ist gleichzeitig (a) der Nenner jeder Sortino-/PSR-Schätzung, (b) die
    Referenzgrösse des numerischen Guards (#916), (c) — NUR unter ``promotion_family_scope !=
    'per_strategy'`` — eine Eingangsgrösse für ``deflation_max_n_periods_ratio`` (#865).

    Gruppiert ``study_records`` nach ``symbol`` und vergleicht je Symbol
    ``max(oos_n_periods_median) / min(oos_n_periods_median)`` gegen ``max_ratio`` (Default 6.0,
    der Kalibrierpunkt für ``deflation_max_n_periods_ratio``, dort heute 4.0). ``severity='high'``
    (nicht ``'blocking'``) — die Heterogenität selbst blockiert keine einzelne Study, sie ist ein
    Diagnosesignal für die Kommensurabilität symbolweiter Ranglisten/Annualisierung.

    Issue #1012/#1164 (Katalog #1170) — Root-Cause: der Meldungstext behauptete unbedingt "die
    #865-Heterogenitäts-Suppression greift vermutlich für praktisch jede Familie dieses Symbols"
    — eine Konsequenz, die die eigene Konfiguration bereits widerlegt: die #865-Suppression
    arbeitet auf ``cohort_n_periods`` (die eligiblen Trials INNERHALB einer Study, ``confirm.py``),
    diese Prüfung misst aber die Spannweite ZWISCHEN Studies desselben Symbols — unter dem
    Default ``promotion_family_scope='per_strategy'`` gibt es KEINEN Pfad, auf dem die
    studienübergreifende Spannweite eine #865-Suppression auslöst (Gegenprobe im Katalog:
    ``deflation_skipped_reason`` war in 28/28 ``None``). Der Text stammte aus der Ära der
    symbolweiten Familie (vor #826/#1131) und wurde beim Scope-Wechsel nicht nachgezogen (dieselbe
    Klasse wie #1128 — verdrahtete Schlussfolgerung). Fix: der #865-Verweis erscheint NUR NOCH,
    wenn ``promotion_family_scope`` explizit übergeben UND ungleich ``'per_strategy'`` ist (``None``
    — Legacy-/Test-Aufrufer ohne dieses Argument — unterdrückt den Verweis ebenfalls, da die
    tatsächliche Konsequenz ohne den Scope nicht behauptet werden kann).

    Issue #981/#1135 (Katalog #986) — Studies mit ``family_membership == 'excluded_degenerate'``
    (strukturell zu wenige OOS-Perioden, siehe ``sweep._study_oos_n_periods_median``) sind aus dem
    Nenner ausgeschlossen: eine 2-Perioden-Study treibt sonst JEDE Symbol-Spannweite trivial über
    ``max_ratio``, ohne dass das ein neuer Befund ist (der degenerierte Status ist bereits
    separat, EXPLIZIT ausgewiesen)."""
    by_symbol: dict[str, list[tuple[str, float]]] = {}
    for r in study_records:
        if r.get("family_membership") == "excluded_degenerate":
            continue
        symbol = r.get("symbol")
        median = r.get("oos_n_periods_median")
        if symbol is None or median is None:
            continue
        by_symbol.setdefault(symbol, []).append((r.get("strategy"), float(median)))
    offenders: dict[str, float] = {}
    # Issue #1071 — je offendierendem Symbol die Study mit dem MINIMUM (der Nenner der Ratio)
    # namentlich ausweisen: eine degenerierte Study (z. B. #1079s geprunte-Trials-Kontamination)
    # treibt die Spannweite, keine Preis-/Datenanomalie.
    denominator_studies: dict[str, str] = {}
    for symbol, pairs in by_symbol.items():
        if len(pairs) < 2:
            continue
        vals = [v for _s, v in pairs]
        lo, hi = min(vals), max(vals)
        if lo <= 0:
            continue
        ratio = hi / lo
        if ratio > max_ratio:
            offenders[symbol] = round(ratio, 2)
            min_strategy = min(pairs, key=lambda p: p[1])[0]
            denominator_studies[symbol] = f"{min_strategy}/{symbol}"
    passed = not offenders
    # Issue #1012/#1164 (Katalog #1170) — der #865-Verweis behauptet eine Konsequenz
    # (Heterogenitäts-Suppression auf ``cohort_n_periods`` INNERHALB einer Study), die diese
    # Prüfung selbst NICHT misst (sie misst die Spannweite ZWISCHEN Studies) — er erscheint daher
    # ausschliesslich, wenn der Aufrufer explizit einen Scope übergibt, der die #865-Suppression
    # tatsächlich an dieser Grösse hängen lässt (``!= 'per_strategy'``, siehe Docstring).
    _mentions_865 = (
        promotion_family_scope is not None and promotion_family_scope != "per_strategy")
    detail_suffix = (
        " Unter promotion_family_scope != 'per_strategy' kann die #865-Heterogenitäts-"
        "Suppression (deflation_max_n_periods_ratio) an dieser Spannweite hängen — vor einer "
        "#865-Interpretation die tatsächliche deflation_skipped_reason-Verteilung prüfen."
        if _mentions_865 else
        " Diese Spannweite betrifft die Kommensurabilität der symbolweiten Ranglisten und der "
        "Annualisierung (#923) — NICHT die per-Study-DSR-Kohorte (#865 arbeitet auf "
        "cohort_n_periods innerhalb einer Study, unter promotion_family_scope='per_strategy' gibt "
        "es keinen Pfad von dieser Zwischen-Study-Spannweite zu einer #865-Suppression, #1012/"
        "#1164)."
    )
    return InvariantResult(
        name="check_n_periods_homogeneity",
        passed=passed,
        expected=f"max(oos_n_periods_median) / min(oos_n_periods_median) <= {max_ratio} je Symbol",
        actual=offenders if offenders else None,
        severity="high",
        provenance=({"denominator_studies": denominator_studies} if denominator_studies else None),
        detail=("OK" if passed else
                f"{len(offenders)} Symbol(e) mit n_periods-Spannweite > {max_ratio}: {offenders} — "
                f"Nenner-Study je Symbol: {denominator_studies} (vor einer Interpretation als "
                "Datenanomalie erst prüfen, ob dieser Nenner selbst degeneriert ist, z. B. "
                f"#1079).{detail_suffix}"),
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


def check_invariant_registry_wired(
    defined_check_names: list[str], wired_check_names: list[str], *,
    deliberately_unwired: tuple[str, ...] = (),
) -> InvariantResult:
    """Issue #984/#1138 (Pitfall #409 in AGENTS.md) — eine Invariante ohne Aufrufstelle ist
    Dokumentation, keine Prüfung. Symptom, das diesen Check motiviert hat: fünf in diesem Modul
    definierte, getestete ``check_*``-Funktionen (``check_cost_model_floor``, ``check_cost_model_
    resolution``, ``check_family_n_statistic_coverage``, ``check_family_scope_coherence``,
    ``check_gate_collinearity_decision_required``) hatten NULL Aufrufstellen ausserhalb von
    ``invariants.py``/``tests/`` — darunter beide Checks, die das Kostenmodell prüfen, während
    ``COST_MODEL_RESOLVED``-Events laengst emittiert wurden.

    Jede in ``defined_check_names`` gelistete Funktion muss entweder in ``wired_check_names``
    (tatsächlich beobachtete Aufrufstellen, vom Aufrufer ermittelt) ODER in
    ``deliberately_unwired`` (ein explizit gepflegter, dokumentierter Verzichtseintrag) stehen.

    Reine Funktion: die eigentliche Introspektion (welche ``check_*``-Funktionen sind DEFINIERT,
    welche Namen tauchen an einer Aufrufstelle auf) liegt beim Aufrufer — Datei-I/O/AST-Parsing
    gehört nicht in dieses reine-Funktionen-Modul (siehe Moduldocstring)."""
    defined = set(defined_check_names or [])
    wired = set(wired_check_names or [])
    excused = set(deliberately_unwired)
    missing = sorted(defined - wired - excused)
    passed = not missing
    return InvariantResult(
        name="check_invariant_registry_wired",
        passed=passed,
        expected="jede definierte check_*-Funktion hat eine Aufrufstelle oder steht in "
                 "deliberately_unwired",
        actual=missing if missing else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(missing)} check_*-Funktion(en) ohne Aufrufstelle und ohne deliberately_"
                f"unwired-Eintrag: {missing} — eine Invariante ohne Aufrufstelle ist Dokumentation, "
                "keine Prüfung (Pitfall #409)."),
    )


def check_invariant_coverage(
    defined_check_names: list[str], stream_check_names: list[str], *,
    allowlisted_check_names: list[str] = (),
) -> InvariantResult:
    """Issue #1015/#1167 (Katalog #1170, Pitfall #413 in AGENTS.md) — eine ANDERE Frage als
    ``check_invariant_registry_wired`` (#984/#1138, oben): dieser fragt "hat die Funktion eine
    Aufrufstelle im Quelltext" (statisch) — 91 von 91 ``check_*``-Funktionen bestanden DIESEN
    Check, obwohl neun ihrer Ergebnisse nie in ``run.json['invariant_checks']`` ankamen. Acht
    davon LIEFEN (Worker-, Sweep-Schleifen-, Phase-5-Prozess), meldeten ihr Urteil aber nur bei
    FAIL (oder nie) als Ereignis — ein Leser des Reports konnte "bestanden" nicht von "nie
    geprüft" unterscheiden. Diese Funktion prüft die LAUFZEIT-Beobachtbarkeit: erschien der Name
    tatsächlich in DIESEM Report (``stream_check_names``, vom Aufrufer aus dem fertig
    zusammengeführten ``invariant_checks`` extrahiert) — oder steht er auf der Allowlist
    (``allowlisted_check_names``, mit Begründung, z. B. weil sein Ereignis strukturell in einem
    disjunkten Prozess-Sidecar landet, siehe ``report._DELIBERATELY_UNWIRED_INVARIANT_CHECKS``)?

    Akzeptanzkriterium #1167: ``n_defined - n_in_stream - n_allowlisted == 0`` — hier über die
    Menge ``defined - stream - allowlisted`` ausgewertet (robust gegen Namen, die in BEIDEN
    Mengen zugleich stehen, was die reine Subtraktion sonst verdecken würde).

    Reine Funktion (wie ``check_invariant_registry_wired``): welche ``check_*``-Funktionen
    DEFINIERT sind und welche Namen im Report-Strom AUFTAUCHTEN, ermittelt der Aufrufer."""
    defined = set(defined_check_names or [])
    stream = set(stream_check_names or [])
    allowlisted = set(allowlisted_check_names or [])
    missing = sorted(defined - stream - allowlisted)
    passed = not missing
    return InvariantResult(
        name="check_invariant_coverage",
        passed=passed,
        expected="jede definierte check_*-Funktion erscheint entweder im invariant_checks-Strom "
                 "dieses Reports oder in _DELIBERATELY_UNWIRED_INVARIANT_CHECKS mit Begründung "
                 "(n_defined - n_in_stream - n_allowlisted == 0).",
        actual={"n_defined": len(defined), "n_in_stream": len(stream & defined),
               "n_allowlisted": len(allowlisted & defined), "missing": missing} if not passed else None,
        severity="high",
        detail=("OK" if passed else
                f"{len(missing)} definierte check_*-Funktion(en) erscheinen weder im Invarianten-"
                f"Strom dieses Reports noch auf der Allowlist: {', '.join(missing)} — ihr Ergebnis "
                "(PASS oder FAIL) ist aus dem Artefakt nicht ablesbar (#1167)."),
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


def check_fail_fast_invariants_are_blocking(invariant_checks: list[dict], *,
                                            fail_fast_invariants: list[str] | None = None,
                                            ) -> InvariantResult:
    """Issue #983/#1137 (Katalog #986, Pitfall #410 in AGENTS.md) — ``severity`` und
    ``fail_fast_invariants`` waren zwei ENTKOPPELTE Taxonomien fuer dieselbe Frage ("darf dieser
    Check einen Sweep abbrechen?"): ``check_effective_stop_distance`` stand seit langem in
    ``optimizer.json['fail_fast_invariants']``, trug aber ``severity='high'`` — ein Check konnte
    einen Lauf abbrechen, OHNE dass sein eigener Schweregrad das je ausgewiesen hatte.

    EINE Quelle seither: ``severity='blocking'`` IMPLIZIERT fail-fast-Faehigkeit;
    ``fail_fast_invariants`` DARF NUR Namen von Checks listen, deren beobachtete ``severity`` in
    DIESEM Lauf ``'blocking'`` war. ``invariant_checks``: die vollen ``to_dict()``-Ergebnisse eines
    Laufs (``report.py``s ``invariant_checks``, ``{"name", "severity", ...}``-Dicts) — bei
    mehrdeutiger (mehrfach beobachteter, unterschiedlicher) Severity fuer denselben Namen zaehlt
    das ERSTE Vorkommen (dieselbe "genau ein Grund"-Zusammenfassung wie Issue #983 fordert).

    ``fail_fast_invariants`` leer/fehlend ⇒ nicht anwendbar (PASS). Ein konfigurierter Name ohne
    jedes beobachtete Ergebnis in diesem Lauf wird NICHT als Verstoss gezaehlt (das ist
    ``check_fail_fast_invariants_wired``s eigenstaendige Zustaendigkeit, keine doppelte Meldung
    fuer denselben Root-Cause)."""
    configured = sorted(set(fail_fast_invariants or []))
    if not configured:
        return InvariantResult(
            name="check_fail_fast_invariants_are_blocking",
            passed=True,
            expected="jede in fail_fast_invariants gelistete Invariante hat severity='blocking'",
            actual=None,
            detail="fail_fast_invariants leer/fehlt — nicht anwendbar.",
        )
    severity_by_name: dict[str, str] = {}
    for c in invariant_checks or []:
        name = c.get("name")
        if name is not None and name not in severity_by_name:
            severity_by_name[name] = c.get("severity")
    offenders = {
        name: severity_by_name[name] for name in configured
        if name in severity_by_name and severity_by_name[name] != "blocking"
    }
    passed = not offenders
    return InvariantResult(
        name="check_fail_fast_invariants_are_blocking",
        passed=passed,
        expected="jede in fail_fast_invariants gelistete Invariante hat severity='blocking'",
        actual=offenders if offenders else None,
        severity="blocking",
        detail=("OK" if passed else
                f"{len(offenders)} in fail_fast_invariants gelistete Invariante(n) OHNE severity="
                f"'blocking': {offenders} — zwei entkoppelte Taxonomien fuer dieselbe Frage "
                "(Issue #983, Pitfall #410 in AGENTS.md); entweder severity auf 'blocking' heben "
                "oder aus fail_fast_invariants entfernen."),
    )


def check_fail_fast_actual_convention(invariant_checks: list[dict], *,
                                      fail_fast_invariants: list[str] | None = None) -> InvariantResult:
    """Issue #1063 (Pitfall #370) — Meta-Invariante: JEDER FAILende Check, der in
    ``optimizer.json['fail_fast_invariants']`` steht, muss seine Offender in ``actual`` als
    ``{"<strategy>/<symbol>": wert}`` stempeln (die Konvention, die
    ``sweep._offending_pairs_for_fail_fast_check`` parst). Root-Cause #1063:
    ``check_holding_time_cap`` FAILte über seinen Magnituden-Ast (#1036) mit
    ``actual=None`` — der Parser fiel auf den konservativen "Struktur unbekannt ⇒ global
    abbrechen"-Zweig, der die #1016-Breitenschwelle (Pitfall #349) NIE auswertete, exakt in der
    Ein-Symbol-Konfiguration, für die sie gebaut wurde. Statt diesen stillen Konservativ-Abbruch
    unbemerkt zu lassen, macht dieser Check die Vertragsverletzung selbst SICHTBAR.

    Ein PASSender fail_fast-Check ist nie ein Offender (er hat nichts zu melden — ``actual=None``
    ist dort die korrekte, erwartete Form). Nur eine FAILende Auswertung ohne die Pair-Konvention
    zählt."""
    configured = set(fail_fast_invariants or [])
    if not configured:
        return InvariantResult(
            name="check_fail_fast_actual_convention", passed=True,
            expected="jeder FAILende fail_fast_invariants-Check traegt actual={'strategy/symbol': wert}",
            actual=None, detail="fail_fast_invariants leer/fehlt — nicht anwendbar.",
        )
    offenders = []
    for chk in invariant_checks or []:
        name = chk.get("name") or chk.get("check")
        if name not in configured or chk.get("passed", True):
            continue
        actual = chk.get("actual")
        conforms = (
            isinstance(actual, dict) and bool(actual)
            and all(isinstance(k, str) and "/" in k for k in actual))
        if not conforms:
            offenders.append(name)
    passed = not offenders
    return InvariantResult(
        name="check_fail_fast_actual_convention",
        passed=passed,
        expected="jeder FAILende fail_fast_invariants-Check traegt actual={'strategy/symbol': wert}",
        actual=offenders or None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} FAILende(r) fail_fast-Check(s) ohne die actual-Pair-Konvention: "
                f"{offenders} — sweep._offending_pairs_for_fail_fast_check faellt fuer diese auf "
                "den Konservativ-Zweig zurueck (globaler Abbruch, Breitenschwelle wird nicht "
                "ausgewertet, #1063)."),
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


def check_coverage_ledger_continuity(total_runs_started: int, has_prior_reports: bool, *,
                                     coverage_bootstrap_phase: bool = False) -> InvariantResult:
    """Issue #892 Fix Punkt 2 — FAIL (blocking), wenn ``total_runs_started == 1`` UND bereits
    MINDESTENS ein früherer Lauf-Report existiert (``data/optimizer/reports/run_*.json``): das
    Coverage-Ledger (``symbol_coverage.json``) wurde zwischen dem Vorlauf und diesem Lauf
    zurückgesetzt/verloren — ein Datenverlust (achte Wiederkehr von Pitfall #237: #794, #796,
    #797, #818, #831, #840, #856, hier), kein Normalzustand für einen Sweep, der nachweislich
    nicht der allererste ist. Reine Funktion — der Aufrufer (``report._build_report``) ermittelt
    ``has_prior_reports`` aus dem Report-Verzeichnis.

    Issue #1064 — ``coverage_bootstrap_phase`` (``symbol_coverage.coverage_report``s gleichnamiges
    Feld, #892 Fix Punkt 4/Pitfall #287) macht den Check unbedingt PASS: während der Bootstrap-
    Phase ist ``total_runs_started == 1`` der ERWARTETE Wert, kein Datenverlust — derselbe Begriff,
    den ``check_symbol_coverage`` bereits für ``never_covered`` konsultiert. Root-Cause #1064:
    dieser Check ignorierte die Bootstrap-Phase bislang vollständig, obwohl derselbe Report im
    selben Lauf sie für eine strukturell verwandte Aussage ("145 Symbole noch nie abgedeckt")
    bereits als "Telemetrie, kein FAIL" behandelte — zwei Checks über denselben Ledger-Zustand mit
    widersprüchlicher Bootstrap-Semantik."""
    if coverage_bootstrap_phase:
        # Issue #943/#1109 (Katalog #960) — der Detailtext trug bislang eine HART KODIERTE "1"
        # ("total_runs_started==1 ist hier der erwartete Wert"), unabhaengig vom TATSAECHLICH
        # gemessenen ``total_runs_started`` (im realen B-5-Referenzlauf: ``actual=2``, durch einen
        # gleichzeitigen Nebenprozess hochgezaehlt — der Text behauptete trotzdem "==1", ein
        # Widerspruch zum eigenen ``actual``-Feld im selben Ergebnis). Der Text wird jetzt AUS dem
        # Messwert erzeugt, nicht aus einer Literalzahl.
        detail = (
            "Bootstrap-Phase (symbol_coverage.coverage_bootstrap_phase) — "
            f"total_runs_started=={total_runs_started} ist waehrend der Bootstrap-Phase erwartet "
            "(kein Anspruch auf genau 1 — ein gleichzeitiger Nebenprozess kann den globalen Zaehler "
            "unabhaengig erhoehen, siehe #1089), nicht anwendbar (#1064)."
        )
        return InvariantResult(
            name="check_coverage_ledger_continuity",
            passed=True,
            expected="total_runs_started > 1, sobald mindestens ein früherer Lauf-Report existiert",
            actual=total_runs_started,
            severity="blocking",
            detail=detail,
        )
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


def check_ineligible_cohort_partition_identity(study_counts: dict) -> InvariantResult:
    """Issue #1025/#1174 (Katalog #866-2, Pitfall #424 in AGENTS.md) — die evaluierten Trials einer
    Study (``n_evaluable``) zerlegen sich disjunkt und vollstaendig in drei Klassen: eligible
    (``n_eligible``), ineligible mit einem echten, gemessenen Ablehnungsgrund
    (``n_ineligible_measured``), und ineligible, weil ein Gate auf einer undefinierten Groesse lief
    (``n_ineligible_unmeasurable``, ``REJECT_OOS_STATISTIC_UNAVAILABLE``).

    Root-Cause #1025/#1174: ``n_ineligible_measured`` wurde vor diesem Fix als ``max(0, n_evaluable
    - n_eligible - n_ineligible_unmeasurable)`` SUBTRAHIERT statt direkt gezaehlt — die beiden
    Operanden liefen zeitweise ueber verschiedene Grundgesamtheiten (``n_evaluable`` PRUNED-
    bereinigt, ``n_ineligible_unmeasurable`` nicht), wodurch die Differenz negativ werden konnte;
    ``max(0, …)`` verbarg das als stille ``0`` statt eines sichtbaren Kohortenbruchs (SqueezeBreakout:
    71 - 20 - 78 = -27, ausgewiesen als 0 statt der korrekten 51). Seit dem Fix wird
    ``n_ineligible_measured`` direkt aus ``is_rejection_detail_counts`` gezaehlt — diese Invariante
    ist der Regressionswaechter GEGEN eine erneute Divergenz der drei Zaehler, unabhaengig davon,
    WIE eine kuenftige Aenderung sie einfuehren wuerde.

    FAIL (severity ``high``), wenn ``n_eligible + n_ineligible_measured + n_ineligible_unmeasurable
    + n_unevaluable != n_trials`` (``n_unevaluable := n_trials - n_evaluable``). Fehlt einer der
    vier Zaehler ⇒ nicht anwendbar (PASS, fail-open auf fehlender Evidenz, analog
    ``check_denominator_coherence``)."""
    keys = ("n_trials", "n_evaluable", "n_eligible", "n_ineligible_measured",
            "n_ineligible_unmeasurable")
    values = {k: study_counts.get(k) for k in keys}
    if any(v is None for v in values.values()):
        return InvariantResult(
            name="check_ineligible_cohort_partition_identity",
            passed=True,
            expected="n_eligible + n_ineligible_measured + n_ineligible_unmeasurable + "
                     "n_unevaluable == n_trials",
            actual=None,
            severity="high",
            detail="Zähler unbekannt — nicht anwendbar.",
        )
    n_unevaluable = values["n_trials"] - values["n_evaluable"]
    total = (values["n_eligible"] + values["n_ineligible_measured"]
             + values["n_ineligible_unmeasurable"] + n_unevaluable)
    diff = values["n_trials"] - total
    passed = diff == 0
    return InvariantResult(
        name="check_ineligible_cohort_partition_identity",
        passed=passed,
        expected="n_eligible + n_ineligible_measured + n_ineligible_unmeasurable + "
                 "n_unevaluable == n_trials",
        actual=None if passed else {**values, "n_unevaluable": n_unevaluable, "diff": diff},
        severity="high",
        detail=("OK" if passed else
                f"Zerlegung ({total}) != n_trials ({values['n_trials']}), Differenz {diff}: "
                f"{values} — die Kohorten-Zerlegung ist nicht disjunkt/vollstaendig "
                "(#1025/#1174-Fehlerklasse, Pitfall #424)."),
    )


def check_stop_distance_microstructure_floor(study_records: list[dict]) -> InvariantResult:
    """Issue #1028/#1177 (Katalog #866-2, Pitfall #427 in AGENTS.md) — der ATR-Floor war bislang
    REIN kostengekoppelt (``backtest_runner.cost_coupled_atr_floor_bps``, #1096): er garantiert nur
    ``Stopdistanz >= min_stop_to_cost_ratio · c_rt``, nicht ``Stopdistanz >= eine Median-Bar-
    Spanne``. Ein Stop INNERHALB der Bar-Spanne ist keine Verlustobergrenze, sondern ein
    Rausch-Trigger — der realisierte Verlust wird dann von der Bewegung EINER Bar plus Fill-
    Slippage bestimmt, nicht von der konfigurierten Stopdistanz.

    FAIL (severity ``high`` — diagnostisch, siehe ``report._stamp_atr_floor_bps_derived``-
    Docstring: die Mikrostruktur-Untergrenze ist additive Report-Telemetrie, noch nicht in die
    Simulation zurückgespeist), wenn ``stop_distance_bps < bar_range_median_bps`` für eine Study
    mit beiden Feldern — die tatsächlich SIMULIERTE Stopdistanz lag unter der beobachteten
    Median-Bar-Spanne. Studies ohne beide Felder werden übersprungen (fail-open auf fehlender
    Evidenz)."""
    offenders: dict[str, dict] = {}
    n_measured = 0
    for r in study_records:
        stop_distance = r.get("stop_distance_bps")
        bar_range = r.get("bar_range_median_bps")
        if stop_distance is None or bar_range is None:
            continue
        n_measured += 1
        if float(stop_distance) < float(bar_range):
            offenders[f"{r.get('strategy')}/{r.get('symbol')}"] = {
                "stop_distance_bps": round(float(stop_distance), 4),
                "bar_range_median_bps": round(float(bar_range), 4),
            }
    passed = not offenders
    return InvariantResult(
        name="check_stop_distance_microstructure_floor",
        passed=passed,
        expected="stop_distance_bps >= bar_range_median_bps je Study",
        actual=offenders or None,
        severity="high",
        detail=("OK" if passed else
                f"{len(offenders)} von {n_measured} gemessenen Studies haben eine simulierte "
                "Stopdistanz UNTER der beobachteten Median-Bar-Spanne — der Stop ist dort ein "
                "Rausch-Trigger, keine Verlustobergrenze (#1028/#1177, Pitfall #427)."),
    )


def check_stop_exit_slippage_materiality(
    study_records: list[dict], *, max_fraction: float = 0.25,
) -> InvariantResult:
    """Issue #1029/#1178 (Katalog #866-2) — Fill-Slippage bei TRAILING_STOP-Exits erschien bislang
    in KEINEM Report-Abschnitt und in KEINER Invariante, obwohl sie in einem Referenzlauf in 14/14
    Studies befüllt war (Median −12,41 bps über alle Studies, rund 19 % des Median-Stop-Verlusts
    und rund die Hälfte der Median-Stopdistanz) — die grösste einzelne, bereits GEMESSENE und
    bislang ignorierte Ertragsposition des Laufs.

    FAIL (severity ``high`` — die Slippage selbst ist gemessene Realität, kein Bug; ein FAIL ist
    ein Aufruf, sie ins Kostenmodell zu überführen, nicht eine Korrektheitsverletzung), wenn
    ``|median(stop_exit_slippage_bps)| > max_fraction · median(gross_loss_median_bps_trailing_
    stop)`` (Default ``max_fraction=0.25``) über alle Studies mit beiden Feldern. ``stop_exit_
    slippage_bps`` ist seit #1029/#1178 seitenbereinigt und ADVERS vorzeichenbehaftet (``+`` =
    advers), der Betrag wird hier verglichen (das Vorzeichen selbst ist keine Aussage über die
    Materialität)."""
    slippages = [
        abs(float(r["stop_exit_slippage_bps"])) for r in study_records
        if r.get("stop_exit_slippage_bps") is not None
    ]
    losses = [
        float(r["gross_loss_median_bps_trailing_stop"]) for r in study_records
        if r.get("gross_loss_median_bps_trailing_stop") is not None
    ]
    if not slippages or not losses:
        return InvariantResult(
            name="check_stop_exit_slippage_materiality",
            passed=True,
            expected=f"|median(stop_exit_slippage_bps)| <= {max_fraction} · "
                     "median(gross_loss_median_bps_trailing_stop)",
            actual=None,
            severity="high",
            detail="Keine Study mit beiden Feldern — nicht auswertbar.",
        )
    median_slippage = statistics.median(slippages)
    median_loss = statistics.median(losses)
    threshold = max_fraction * median_loss
    passed = median_slippage <= threshold
    return InvariantResult(
        name="check_stop_exit_slippage_materiality",
        passed=passed,
        expected=f"|median(stop_exit_slippage_bps)| <= {max_fraction} · "
                 "median(gross_loss_median_bps_trailing_stop)",
        actual=None if passed else {
            "median_abs_slippage_bps": round(median_slippage, 4),
            "median_gross_loss_bps_trailing_stop": round(median_loss, 4),
            "threshold_bps": round(threshold, 4),
        },
        severity="high",
        detail=("OK" if passed else
                f"Median |Slippage| ({round(median_slippage, 2)} bps) > {max_fraction} × Median "
                f"Stop-Verlust ({round(median_loss, 2)} bps, Schwelle {round(threshold, 2)} bps) — "
                "die gemessene Fill-Slippage ist eine materielle, bislang im Kostenmodell "
                "unberücksichtigte Ertragsposition (#1029/#1178)."),
    )


def check_report_artifact_written(*, run_status: str | None, report_written: bool) -> InvariantResult:
    """Issue #1021/#1196 Fix 4.3 — ein Lauf, der ``run_status='complete'`` meldet, aber keinen
    ``run_<run_id>.json`` geschrieben hat, ist die Verallgemeinerung des Ausgangsbefunds: der
    zweite Sweep eines Tages rechnete 2411s durch, meldete ``SWEEP_COMPLETED``/``run_status=
    'complete'`` und schrieb dabei kein einziges Entscheidungsartefakt, weil alle vier
    Report-Aufrufstellen in ``sweep.py`` die dabei geworfene ``ReportCohortUnresolvable`` als
    "non-fatal" abfingen. Root-Cause behoben (#1021 Fix 4.1: der Wächter unterscheidet jetzt
    sequenzielle Store-Wiederverwendung von echter Nebenläufigkeit) UND dieser Check als zweite,
    unabhängige Verteidigungslinie: ``severity='blocking'`` — ``complete`` ohne geschriebenen
    Report ist niemals ein zulässiger Endzustand, unabhängig von der Ursache eines künftigen
    Schreibfehlers.

    ``run_status`` ungleich ``'complete'`` ⇒ nicht anwendbar (ein expliziter Abbruch-/
    In-Progress-Status behauptet nicht, dass ein Report existiert)."""
    if run_status != "complete":
        return InvariantResult(
            name="check_report_artifact_written",
            passed=True,
            expected="run_status='complete' impliziert einen geschriebenen run_<run_id>.json",
            actual=None,
            severity="blocking",
            detail=f"run_status={run_status!r} != 'complete' — nicht anwendbar.",
        )
    passed = bool(report_written)
    return InvariantResult(
        name="check_report_artifact_written",
        passed=passed,
        expected="run_status='complete' impliziert einen geschriebenen run_<run_id>.json",
        actual={"run_status": run_status, "report_written": report_written},
        severity="blocking",
        detail=("OK — Report geschrieben." if passed else
                "run_status='complete' gemeldet, aber KEIN run_<run_id>.json geschrieben — ein "
                "Lauf ohne Report ist nicht 'complete' (#1021/#1196)."),
    )
