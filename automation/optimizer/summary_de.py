"""Issue #832 (P1, Katalog #828-#835, GitHub-Issue #751) — deutschsprachiger Abschlussbericht.

Direkte Nutzeranforderung: nach Abschluss eines Sweeps soll eine Zusammenfassung in deutscher
Sprache entstehen über (a) den monetären Erfolg, (b) die Zeitdauer und (c) die Trades mit der
längsten Haltedauer.

``generate_german_summary`` liest AUSSCHLIESSLICH das bereits erzeugte #742-Report-Dict (siehe
``report.generate_sweep_report``/``generate_report_for_run``) — keine zweite Datenquelle, keine
Neuberechnung, kein Zugriff auf ``data/optimizer/`` oder ``tournament_result.json``. Das macht die
Funktion mit einem synthetischen Report-Fixture vollständig testbar und erbt automatisch die
#833-Abbruchfestigkeit: ein Teilreport (``run_status != 'complete'``) erzeugt trotzdem eine
Zusammenfassung, nur über eine kleinere/unvollständige Kohorte.

Scope-Entscheidung (Katalog #832 Fix Punkt 1, siehe auch ``optimizer.json['report_longest_
trades_k']``-Schema-Text und ``backtest_runner.py``s Kommentar an der ``max_holding_time_s``-
Berechnung): Abschnitt 4 listet die Top-K STUDIES (Strategie/Symbol) nach ``max_holding_time_s``,
NICHT individuelle Einzel-Trades mit Entry-/Exit-Zeitstempel — letzteres würde eine neue State-
Verfolgung in der FIFO-Match-Schleife von ``backtest_runner.extract_metrics`` voraussetzen (der
höchstriskantesten P&L-Aggregationsstelle des Systems), ohne einen realen Marktdaten-Lauf zur
Regressionsverifikation in diesem Environment. Diese bewusste Abweichung vom im Issue-Text
skizzierten Einzel-Trade-Format ist an dieser Stelle dokumentiert, nicht stillschweigend.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger("optimizer")

# Issue #783 — READY_FOR_PR und PROMOTE_GLOBAL_DEFAULT sind BEIDE "deploybar" (der #682/#783-
# Default-Route-Fall zählt als Promotion, nur ohne symbolspezifisches Tuning).
_DEPLOYABLE_STATUSES = ("READY_FOR_PR", "PROMOTE_GLOBAL_DEFAULT")

_RUN_STATUS_LABELS_DE = {
    "complete": "vollständig abgeschlossen",
    "aborted_disk": "abgebrochen (Speicherplatz-Budget überschritten, #795)",
    "aborted_wallclock": "abgebrochen (Laufzeit-Budget überschritten, #828)",
    "aborted_signal": "abgebrochen (SIGINT/SIGTERM)",
    "aborted_error": "abgebrochen (unerwartete Exception)",
    # Issue #1024 (Katalog #866) Fix Punkt 4 — ``sweep.py:2891`` setzt diesen Status seit #939, aber
    # dieses Mapping fehlte: der Wert erschien als roher englischer String in einem sonst
    # deutschsprachigen Bericht (Fallback ``_RUN_STATUS_LABELS_DE.get(status, status)``).
    "completed_with_quarantine": "abgeschlossen mit Quarantäne (#939 — mindestens ein Symbol fehlgeschlagen)",
    "aborted_invariant": "abgebrochen (blockierende Invariante, echter Arbeitsabbruch)",
    # Issue #1065 — getrennt von 'aborted_invariant': alle geplanten Symbole wurden VOLLSTÄNDIG
    # gerechnet, der Lauf ist aber wegen mindestens einer blockierenden Invariante nicht
    # entscheidungsfähig (siehe sweep.py, Downgrade-Regel bei symbols_completed >= symbols_planned).
    "completed_invalid": "vollständig gerechnet, aber wegen blockierender Invarianten nicht entscheidungsfähig",
}

def _run_status_label_de(report: dict) -> str:
    """Issue #942/#1108 (Katalog #960) — die statische ``_RUN_STATUS_LABELS_DE``-Zuordnung allein
    beschriftete ``run_status='aborted_invariant'`` UNBEDINGT als "echter Arbeitsabbruch", auch dann,
    wenn ``work_completed`` (die #942-Achse, siehe ``report._build_report``) bereits bekannt und
    wahr war — Root-Cause #1108: derselbe Report, der oben (Sektion 1) korrekt "vollständig
    gerechnet, aber ungültig" sagte, zeigte hier trotzdem "echter Arbeitsabbruch" fuer denselben
    Lauf. Wenn ``work_completed`` bekannt ist, entscheidet es NEBEN dem rohen ``run_status``-Label."""
    run_status = report.get("run_status", "complete")
    if report.get("work_completed") is True and report.get("decision_admissible") is False:
        # ERSETZT das rohe Label (statt es zu ergaenzen): "abgebrochen ... echter Arbeitsabbruch"
        # und die Korrektur im selben Satz waeren sich selbst widersprechende Text.
        return (
            "vollständig gerechnet, aber wegen blockierender Invarianten nicht "
            "entscheidungsfähig (kein Arbeitsabbruch)"
        )
    return _RUN_STATUS_LABELS_DE.get(run_status, run_status)


def _fmt_pct(x: float | None, *, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f} %" if x is not None else "k. A."


def _fmt_num(x: float | None, *, digits: int = 4) -> str:
    return f"{x:.{digits}f}" if isinstance(x, (int, float)) else "k. A."


def _fmt_profit_factor(r: dict, *, digits: int = 2) -> str:
    """Issue #1004 (Katalog #858, Pitfall #342) — ``holdout_profit_factor`` ist gecappt
    (``tournament.json['profit_factor_cap']``); ein zensierter Wert wird NIE als glatte Zahl
    angezeigt (das war der Bug: ein Cap ist eine Zensur, kein Messwert), sondern mit ``≥``-Präfix
    ausgewiesen — ``holdout_profit_factor_raw`` (falls vorhanden) macht die Grössenordnung des
    tatsächlichen, unbeschränkten Quotienten sichtbar."""
    val = r.get("holdout_profit_factor")
    if not isinstance(val, (int, float)):
        return "k. A."
    if not r.get("holdout_profit_factor_censored"):
        return f"{val:.{digits}f}"
    return f"≥{val:.{digits}f}*"


def _fmt_hours(seconds: float | None) -> str:
    return f"{seconds / 3600.0:.2f} h" if seconds is not None else "k. A."


def _fmt_hms_from_s(seconds: float | None) -> str:
    if seconds is None:
        return "k. A."
    h = seconds / 3600.0
    return f"{h:.2f} h ({seconds:.0f} s)"


def _fmt_holding_duration_with_bar_note(seconds: float | None) -> str:
    """Issue #1011/#1163 (Katalog #1170, Fix Punkt 3) — dieselbe Basis-Formatierung wie
    ``_fmt_hms_from_s``, ergänzt um die Bar-/Kalendertag-Äquivalenz (1h-Bar-Konvention, siehe
    ``backtest_runner._BAR_SECONDS_METRICS``), damit z. B. "24,00 h" NICHT als "~1 Handelstag"
    (RTH-Lesart) fehlgedeutet wird — auf der aktuellen, über einen 24/7-Kalender aufgefüllten
    synthetischen 1h-Bar-Achse (siehe ``invariants.check_session_calendar_coherence``) sind 24 h
    buchstäblich 24 Bars UND 1 vollen KALENDERtag, keine Handelstags-Näherung."""
    base = _fmt_hms_from_s(seconds)
    if seconds is None:
        return base
    n_bars = seconds / 3600.0
    n_calendar_days = seconds / 86400.0
    return f"{base} = {n_bars:.0f} synthetische Bars = {n_calendar_days:.2f} Kalendertag(e)"


def _studies(report: dict) -> list[dict[str, Any]]:
    return list(report.get("studies") or [])


# Issue #849 — Sortierreihenfolge fuer Sektion 5.1 (am dringendsten zuerst). Ein unbekannter/
# fehlender severity-Wert faellt auf denselben Rang wie "medium" (rueckwaertskompatibel zu
# Pre-#849-invariant_checks-Eintraegen ohne das Feld).
_SEVERITY_ORDER = {"blocking": 0, "high": 1, "medium": 2, "low": 3}


def _check_name(c: dict) -> str:
    """Issue #849 — 'name' ist der EIGENTLICHE Vertrag (InvariantResult.to_dict()); 'check' bleibt
    ein Uebergangs-Alias fuer Report-Fixtures, die ihn (wie vor #849) direkt selbst setzen."""
    return c.get("name") or c.get("check") or "unbekannt"


def _section_1_result_in_one_sentence(report: dict) -> str:
    studies = _studies(report)
    n_studies = len(studies)
    counts = (report.get("cross_study") or {}).get("promotion_outcome_counts") or {}
    # Issue #1029 (Katalog #866) — Root-Cause: dieser Satz zaehlte bislang NUR
    # ``promotion_outcome_counts`` (READY_FOR_PR/PROMOTE_GLOBAL_DEFAULT, eine reine Sweep-Selektions-
    # Zahl) und nannte das Ergebnis "Promotion(en)", OHNE ``deployment_decision.admitted`` zu pruefen
    # — genau die Klausel, die Abschnitt 2.1 (Deployment-Spalte) UND ``check_promotion_deployment_
    # coherence`` bereits kennen. Auf einem Lauf mit ``snapshot_drift=false``-Kandidaten meldete der
    # Kopfsatz "3 Promotion(en)"/"5 Promotion(en)", waehrend Abschnitt 2.1 explizit "noch NICHT
    # deploybar" ausweist und der Invarianten-Check FAILt — der Satz, den ein Leser zuerst liest, mass
    # eine ANDERE Groesse als die Tabelle darunter. ``n_promotions_sweep`` (die alte Zahl) und
    # ``n_deployable`` (= admitted is True) werden jetzt GETRENNT genannt.
    n_promotions_sweep = sum(int(v) for k, v in counts.items() if k in _DEPLOYABLE_STATUSES)
    n_deployable = sum(
        1 for r in studies
        if r.get("promotion_outcome") in _DEPLOYABLE_STATUSES
        and (r.get("deployment_decision") or {}).get("admitted") is True
    )
    run_status = report.get("run_status", "complete")
    _symbols_completed_v = report.get("symbols_completed")
    _symbols_planned_v = report.get("symbols_planned")
    _coverage_incomplete = (
        _symbols_completed_v is not None and _symbols_planned_v is not None
        and _symbols_completed_v < _symbols_planned_v
    )
    status_note = ""
    # Issue #942/#1108 (Katalog #960) — die KANONISCHE Quelle fuer diesen Satz sind seither die drei
    # orthogonalen Achsen (``report._build_report``, EINE Berechnung fuer JEDEN Erzeugungspfad),
    # nicht mehr der ueberladene ``run_status``-String. Root-Cause #1108: derselbe Faktenstand
    # (14/14 Studies, volles Budget, Fail-Fast-Abbruch NACH Abschluss der Arbeit) liess diesen Satz
    # je nach Report-Pfad ENTWEDER "vollständig gerechnet" ODER "echter Arbeitsabbruch" sagen —
    # ``work_completed`` entscheidet das jetzt EINDEUTIG, unabhaengig davon, welcher Pfad den
    # Report erzeugte.
    _work_completed = report.get("work_completed")
    _decision_admissible = report.get("decision_admissible")
    # Issue #1037/#1186 — umbenannt von ``fail_fast_triggered`` (der alte Name behauptete
    # faelschlich einen Abbruch, siehe ``report._build_report``-Docstring).
    _blocking_invariant_triggered = report.get("blocking_invariant_triggered")
    if _work_completed is False:
        status_note = (
            f" **Hinweis:** dieser Lauf ist NICHT vollständig ({_RUN_STATUS_LABELS_DE.get(run_status, run_status)}"
            f"; {report.get('symbols_completed', '?')}/{report.get('symbols_planned', '?')} Symbole"
            " abgeschlossen) — die folgenden Zahlen beziehen sich NUR auf die bereits abgeschlossene Kohorte."
        )
    elif _work_completed is True and _decision_admissible is False:
        # Vollständige Abdeckung, aber ein FAIL-Fast-Verdikt hat den Lauf als ungültig markiert —
        # die blockierenden Checks werden unten (blocking_note) namentlich genannt, hier steht nur
        # die Abdeckungs-Klarstellung. KEINE Formulierung, die einen Arbeitsabbruch behauptet.
        _coverage_str = (
            f"{_symbols_completed_v}/{_symbols_planned_v} Symbole"
            if _symbols_completed_v is not None and _symbols_planned_v is not None
            else "alle geplanten Symbole"
        )
        _blocking_invariant_str = (
            f" ({_blocking_invariant_triggered})" if _blocking_invariant_triggered else "")
        status_note = (
            f" **Hinweis:** Vollständig gerechnet ({_coverage_str}), aber wegen blockierender "
            f"Invarianten{_blocking_invariant_str} nicht entscheidungsfähig — siehe unten."
        )
    elif _work_completed is None:
        # Legacy-Fallback: ein Report ohne die #942-Felder (aeltere Artefakte/Test-Fixtures) faellt
        # auf die vorherige, ausschliesslich run_status/Coverage-basierte Heuristik zurueck.
        if run_status != "complete" and _coverage_incomplete:
            status_note = (
                f" **Hinweis:** dieser Lauf ist NICHT vollständig ({_RUN_STATUS_LABELS_DE.get(run_status, run_status)}"
                f"; {report.get('symbols_completed', '?')}/{report.get('symbols_planned', '?')} Symbole"
                " abgeschlossen) — die folgenden Zahlen beziehen sich NUR auf die bereits abgeschlossene Kohorte."
            )
        elif run_status in ("aborted_invariant", "completed_invalid"):
            _coverage_str = (
                f"{_symbols_completed_v}/{_symbols_planned_v} Symbole"
                if _symbols_completed_v is not None and _symbols_planned_v is not None
                else "alle geplanten Symbole"
            )
            status_note = (
                f" **Hinweis:** Vollständig gerechnet ({_coverage_str}), aber wegen blockierender "
                "Invarianten nicht entscheidungsfähig — siehe unten."
            )
    if n_deployable == 0:
        sentence = (
            f"{n_studies} Studies, {n_promotions_sweep} Sweep-Promotion(en), **0 deploybar** — "
            "kein Kandidat hat sowohl die Holdout-Validierung als auch das Deployment-Gate "
            "(``deployment_gate.evaluate_deployment_eligibility``) bestanden. Es gibt kein "
            "deploybares Ergebnis aus diesem Lauf."
        )
    else:
        sentence = (
            f"{n_studies} Studies, {n_promotions_sweep} Sweep-Promotion(en), {n_deployable} "
            "deploybar — siehe Abschnitt 2 für die Details je Kandidat."
        )
    # Issue #849 Punkt 4 — blockierende Invarianten-FAILs (severity='blocking', z. B.
    # check_holding_time_cap/check_required_config_keys) muessen bereits HIER namentlich auftauchen,
    # nicht erst in Sektion 5 (vorher: hinter 304 Meldungen ueber inerte Reward-Terme verborgen).
    # Issue #1016 (Katalog #858, Fix Punkt 3) — zusaetzlich die Zahl der BETROFFENEN Studies je
    # blockierendem Check (distincte 'scope'-Werte, report.py stempelt "{strategy}/{symbol}" für
    # study-lokale Checks bzw. "global" für laufweite) — eine Namensliste allein beantwortet nicht,
    # ob EIN Ausreisser oder die halbe Kohorte betroffen ist.
    #
    # Issue #1036/#1185 — Root-Cause: ``not c.get("passed", True)`` ist fuer ``passed=None``
    # (INCONCLUSIVE, #995/#1147 — der Check konnte seine Grundgesamtheit nicht herstellen, siehe
    # Pitfall #413 in AGENTS.md) ebenfalls ``True`` (``not None == True``) — dieselbe Klassifikation
    # wie ein NACHGEWIESENES FAIL (``passed=False``). Section 5.1/5.1b trennt diese beiden Zustaende
    # bereits korrekt (``failing_checks``/``inconclusive_checks`` unten in ``_section_5_
    # anomalies``); Section 1 muss aus DERSELBEN Klassifikation speisen, sonst nennen die beiden
    # Abschnitte desselben Berichts unterschiedliche Mengen fuer denselben Check.
    _blocking_fail_scopes: dict[str, set[str]] = {}
    _blocking_inconclusive_scopes: dict[str, set[str]] = {}
    for c in (report.get("invariant_checks") or []):
        if c.get("severity") != "blocking":
            continue
        if c.get("passed") is False:
            _blocking_fail_scopes.setdefault(_check_name(c), set()).add(c.get("scope") or "global")
        elif c.get("passed") is None or c.get("evaluable") is False:
            _blocking_inconclusive_scopes.setdefault(
                _check_name(c), set()).add(c.get("scope") or "global")

    def _scoped_parts(scopes_by_name: dict[str, set[str]]) -> list[str]:
        return [
            f"{name} ({len(scopes)} Study/Studies)" if scopes != {"global"} else name
            for name, scopes in sorted(scopes_by_name.items())
        ]

    blocking_note = ""
    if _blocking_fail_scopes:
        blocking_note += (
            f" **BLOCKIERENDE Invarianten-FAIL(s):** {', '.join(_scoped_parts(_blocking_fail_scopes))} "
            "— siehe Abschnitt 5.1 für Details."
        )
    if _blocking_inconclusive_scopes:
        blocking_note += (
            " Zusätzlich nicht auswertbar (blockierend): "
            f"{', '.join(_scoped_parts(_blocking_inconclusive_scopes))} — siehe Abschnitt 5.1b "
            "für Details."
        )
    return "## 1. Ergebnis in einem Satz\n\n" + sentence + status_note + blocking_note


def _is_data_integrity_quarantined(r: dict) -> bool:
    """Issue #1028 (Katalog #866) Sofortmassnahme 1 — ein Kandidat, dessen
    ``deployment_decision.clause_results['snapshot_drift']`` explizit ``False`` ist (der promotete
    Datenstand weicht nachweislich vom aktuellen Katalog-Snapshot ab), darf NIE in der
    Promotionstabelle erscheinen — ``None``/fehlend ist NICHT dasselbe (nicht geprüft ≠ Drift
    nachgewiesen) und bleibt in der regulären Tabelle."""
    clause_results = ((r.get("deployment_decision") or {}).get("clause_results")) or {}
    return clause_results.get("snapshot_drift") is False


def _section_2_monetary_result(report: dict) -> str:
    studies = _studies(report)
    counts = (report.get("cross_study") or {}).get("promotion_outcome_counts") or {}
    n_deployable = sum(int(v) for k, v in counts.items() if k in _DEPLOYABLE_STATUSES)
    lines = ["## 2. Monetäres Ergebnis", ""]

    # 2.1 Promotionskandidaten — Issue #1006 (Katalog #858): "Deploybar" behauptete bislang eine
    # Eigenschaft, die deployment_gate.evaluate_deployment_eligibility NIE geprüft hatte (acht,
    # seit #1007 neun Klauseln, u. a. DSR UNBEDINGT — genau die Klausel, die
    # promotion_correction_mode='dsr_or_robust_pair' im Sweep ersetzbar macht). Ein
    # READY_FOR_PR/PROMOTE_GLOBAL_DEFAULT-Kandidat ist ab jetzt NUR noch "Promotionskandidat",
    # niemals implizit "Deploybar" — die Deployment-Spalte macht das explizite Urteil sichtbar.
    #
    # Issue #1028 (Katalog #866) Sofortmassnahme 1 — Kandidaten mit nachgewiesenem ``snapshot_drift
    # = false`` (Datenstand-Inkohärenz, siehe ``_is_data_integrity_quarantined``) erscheinen NIE in
    # dieser Tabelle, auch nicht als "abgelehnt" — sie wandern vollständig in Abschnitt 2.1b
    # ("Quarantäne — Datenintegrität"), weil ihre gemeldeten Kennzahlen (Expectancy, Holdout-Return)
    # selbst nicht vertrauenswürdig sind (arithmetisch unmögliche Sizing-Identität, siehe #1028).
    lines.append(
        "### 2.1 Promotionskandidaten (Status READY_FOR_PR / PROMOTE_GLOBAL_DEFAULT) — "
        "noch NICHT deploybar"
    )
    lines.append("")
    all_candidates = [r for r in studies if r.get("promotion_outcome") in _DEPLOYABLE_STATUSES]
    quarantined = [r for r in all_candidates if _is_data_integrity_quarantined(r)]
    deployable = [r for r in all_candidates if not _is_data_integrity_quarantined(r)]
    if not deployable:
        # Bei 0 Promotionskandidaten ist "kein deploybares Ergebnis" akkurat (keine implizite
        # Behauptung, nur eine leere Menge) — der #1006-Bug betraf ausschliesslich die Zeile
        # darunter, die tatsaechliche Kandidaten unbedingt als "Deploybar" bezeichnete.
        lines.append(
            "**Kein deploybares Ergebnis aus diesem Lauf.** Kein Kandidat hat die Holdout-"
            "Validierung bestanden — alle folgenden Zahlen in Abschnitt 2.2 sind ABGELEHNTE, "
            "NICHT handelbare Kandidaten."
        )
    else:
        lines.append(
            "Diese Kandidaten haben die Holdout-Validierung des Sweeps bestanden. Das ist NICHT "
            "dasselbe wie Deploybarkeit — die letzte Spalte zeigt das tatsächliche Urteil von "
            "``deployment_gate.evaluate_deployment_eligibility`` (dieselbe Funktion, die vor jedem "
            "Live-Kapitaleinsatz entscheidet)."
        )
        lines.append("")
        # Issue #1073 (Katalog #866-2, Pitfall #?) — Root-Cause: ``holdout_expectancy_winsorized``
        # (nennerausreisser-robust, #1031/#1042) wird telemetriert, aber weder gerankt noch
        # gegatet — die Rangfolge sortierte implizit auf der ausreisser-EMPFINDLICHSTEN verfügbaren
        # Grösse (Einfüge-/``holdout_total_return``-Reihenfolge). Beweis B-8 im #866-Katalog: der
        # ERSTGELISTETE Kandidat (AdxAtr, +17,23 bps roh) hatte eine NEGATIVE winsorisierte
        # Expectancy (−1,44 bps), getragen von 6 von 132 Trades. Sortierung jetzt nach der
        # winsorisierten Expectancy (robuster Wert zuerst); ``holdout_total_return`` bleibt
        # sichtbar, damit der Wechsel nachvollziehbar bleibt.
        #
        # Issue #945/#1111 (Katalog #960) — der Fallback (zweite Prioritaet, wenn keine
        # winsorisierte Expectancy vorliegt) UND die angezeigte "Expectancy"-Spalte lesen seither
        # ``holdout_expectancy_capital_weighted`` statt des vormaligen ``holdout_expectancy``
        # (Mittel von Quotienten, KEIN Notional-Boden): der Kostenstress-Ladder
        # (``holdout_expectancy_cost_stress_1_5x``/``_2x``) wird bereits aus
        # ``holdout_expectancy_capital_weighted`` abgeleitet (``backtest_runner.
        # _expectancy_cost_stress``, DIESELBE 5-%-Notional-Boden-Logik) — eine hier ANDERS
        # definierte "Expectancy"-Spalte liess den "2×-Kostenstress" bei SqueezeBreakout/PLTR
        # (Divergenz Faktor 7,9) faelschlich als Verbesserung um +145,76 bps erscheinen
        # (Root-Cause, vierte Instanz der Klasse #304/#1033/#1097).
        def _expectancy_rank_key(r: dict) -> float:
            for field in ("holdout_expectancy_winsorized", "holdout_expectancy_capital_weighted",
                         "holdout_total_return"):
                value = r.get(field)
                if value is not None:
                    return float(value)
            return float("-inf")

        deployable_ranked = sorted(deployable, key=_expectancy_rank_key, reverse=True)
        lines.append(
            "| Strategie | Symbol | Holdout-Return | Expectancy (kapitalgew.) | "
            "Expectancy (winsorisiert) | Ausreisser/Trades | Win-Rate | Profit-Faktor | Trades | "
            "Deployment-Urteil |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
        any_censored = False
        for r in deployable_ranked:
            any_censored = any_censored or bool(r.get("holdout_profit_factor_censored"))
            decision = r.get("deployment_decision") or {}
            if decision.get("admitted") is True:
                deploy_verdict = "**deploybar**"
            elif decision:
                deploy_verdict = f"abgelehnt ({decision.get('blocking_clause') or 'k. A.'})"
            else:
                deploy_verdict = "nicht bewertet"
            winsorized = r.get("holdout_expectancy_winsorized")
            capital_weighted = r.get("holdout_expectancy_capital_weighted")
            outlier_count = r.get("holdout_expectancy_outlier_count") or 0
            total_trades = r.get("holdout_total_trades")
            outlier_frac = (
                f"{outlier_count}/{total_trades}" if total_trades else f"{outlier_count}/k. A.")
            sign_flip = (
                winsorized is not None and capital_weighted is not None
                and (winsorized > 0) != (capital_weighted > 0)
            )
            winsorized_cell = _fmt_num(winsorized) + (" ⚠ Vorzeichenwechsel" if sign_flip else "")
            lines.append(
                f"| {r.get('strategy')} | {r.get('symbol')} | {_fmt_pct(r.get('holdout_total_return'))} | "
                f"{_fmt_num(capital_weighted)} | {winsorized_cell} | {outlier_frac} | "
                f"{_fmt_pct(r.get('holdout_win_rate'))} | "
                f"{_fmt_profit_factor(r)} | "
                f"{r.get('holdout_total_trades', 'k. A.')} | {deploy_verdict} |"
            )
        if any_censored:
            # Issue #1004 (Pitfall #342) — ein Cap ist eine Zensur, kein Messwert: ``≥15.00`` heisst
            # "der Nenner war degeneriert oder der Cap band", NICHT "der Profit-Faktor ist 15,00".
            lines.append(
                "\n*`≥`-Werte sind gecappt/zensiert (`tournament.json['profit_factor_cap']` oder "
                "ein numerisch degenerierter Bruttoverlust-Nenner) — der tatsächliche Profit-Faktor "
                "ist unbekannt und liegt darüber, siehe #1004."
            )
    lines.append("")

    # 2.1b Quarantäne — Datenintegrität — Issue #1028 (Katalog #866) Sofortmassnahme 1. Getrennt von
    # 2.1, NICHT als weitere Tabellenzeile mit "abgelehnt"-Urteil: die hier gelisteten Kandidaten
    # haben einen nachgewiesenen Datenstand-Bruch (``snapshot_drift = false``), sodass ihre
    # Kennzahlen selbst (Expectancy, Holdout-Return) nicht als Mess-, sondern als Artefaktwerte zu
    # lesen sind — z. B. eine arithmetisch unmögliche Sizing-Identität
    # (``check_sizing_identity_coherence``) oder eine anomale ATR-Skala
    # (``check_atr_scale_homogeneity``).
    lines.append("### 2.1b Quarantäne — Datenintegrität")
    lines.append("")
    if not quarantined:
        lines.append("Keine Kandidaten mit nachgewiesenem Datenstand-Bruch in diesem Lauf.")
    else:
        lines.append(
            "Diese Kandidaten hätten die Holdout-Validierung des Sweeps bestanden, tragen aber "
            "einen nachgewiesenen Bruch zwischen dem promoteten Datenstand und dem aktuellen "
            "Katalog-Snapshot (``deployment_decision.clause_results['snapshot_drift'] = false``, "
            "#993). Ihre Kennzahlen sind NICHT belastbar — sie werden hier ausschliesslich zur "
            "Nachvollziehbarkeit gelistet, nie als Promotions- oder Ablehnungskandidat."
        )
        lines.append("")
        lines.append("| Strategie | Symbol | Promotion-Ausgang | Holdout-Return (nicht belastbar) |")
        lines.append("|---|---|---|---:|")
        for r in quarantined:
            lines.append(
                f"| {r.get('strategy')} | {r.get('symbol')} | {r.get('promotion_outcome') or 'k. A.'} | "
                f"{_fmt_pct(r.get('holdout_total_return'))} |"
            )
    lines.append("")

    # 2.2 Bester abgelehnter Kandidat je Strategie
    lines.append("### 2.2 Bester abgelehnter Kandidat je Strategie (NICHT deploybar)")
    lines.append("")
    rejected_by_strategy: dict[str, dict[str, Any]] = {}
    for r in studies:
        if r.get("promotion_outcome") in _DEPLOYABLE_STATUSES:
            continue
        strat = r.get("strategy")
        if strat is None:
            continue
        cur = rejected_by_strategy.get(strat)
        cur_ret = (cur or {}).get("holdout_total_return")
        r_ret = r.get("holdout_total_return")
        if cur is None or (r_ret is not None and (cur_ret is None or r_ret > cur_ret)):
            rejected_by_strategy[strat] = r
    if not rejected_by_strategy:
        lines.append("Keine abgelehnten Kandidaten in diesem Lauf.")
    else:
        lines.append(
            "**Diese Kandidaten sind ausdrücklich NICHT deploybar** — der Backtest-Ertrag ist eine "
            "Simulationszahl, kein handelbares Ergebnis."
        )
        lines.append("")
        # Issue #1002/#1154 (Katalog #1170) — ``blocking_stage`` als EIGENE Spalte: vorher liess
        # sich fuer eine ``REJECTED_ON_HOLDOUT``-Zeile nicht ohne Blick in run.json unterscheiden,
        # ob die Holdout-Stufe nie erreicht wurde (``confirm_or_selection``), das Holdout-Gate
        # selbst scheiterte (``holdout``) oder die nachgelagerte Deflation ablehnte (``deflation``).
        lines.append("| Strategie | Symbol | Holdout-Return (simuliert) | Ablehnungsgrund | Stufe |")
        lines.append("|---|---|---:|---|---|")
        for strat, r in sorted(rejected_by_strategy.items()):
            reason = r.get("promotion_outcome") or "k. A."
            stage = r.get("blocking_stage") or "k. A."
            lines.append(
                f"| {strat} | {r.get('symbol')} | {_fmt_pct(r.get('holdout_total_return'))} | "
                f"{reason} | {stage} |"
            )
    lines.append("")

    # Issue #850 — Abschnitt 2.3 mass vorher das SYMBOL, nicht die Strategie: holdout_excess_return
    # ist im Bärenmarkt näherungsweise −Buy&Hold, also eine Symbol-Konstante (Varianzanteil Symbol
    # 99,1 % auf den Katalog-Daten). Vier Änderungen: (1) Strategie-/B&H-Return + Zeit im Markt
    # zusätzlich zur Excess-Spalte, (2) Sortierung nach Strategie-Return statt Excess, (3) ein
    # negativer B&H-Return unterdrückt die "schlägt Buy & Hold"-Wertung (trivial positiver Excess
    # gegen einen fallenden Markt ist kein Alpha-Nachweis), (4) excess_per_exposure normiert den
    # Excess auf die tatsächlich eingegangene Marktzeit.
    lines.append("### 2.3 Vergleich gegen Buy & Hold je Symbol")
    lines.append("")
    with_benchmark = [r for r in studies if r.get("holdout_excess_return") is not None]
    # Issue #1013/#1165 (Katalog #1170, Fix Punkt 2) — Root-Cause: eine Study ohne auswertbaren
    # Holdout (``holdout_excess_return is None`` — z. B. weil ``mtm_series``/Benchmark-Serie nie
    # berechnet wurden, siehe report._study_record's holdout_total_return-Kommentar) fiel bislang
    # STILLSCHWEIGEND aus der ``with_benchmark``-Menge, ohne in einer der beiden Buckets darunter
    # aufzutauchen — 13 von 14 Studies gelistet, keine Fehlanzeige für die 14. Diese dritte Tabelle
    # macht die fehlende Auswertung SELBST zu einem sichtbaren Befund, damit die Zeilensumme über
    # alle drei Tabellen IMMER ``n_studies`` ergibt (Akzeptanzkriterium 1, ``invariants.check_
    # summary_row_completeness`` bewacht das strukturell).
    without_benchmark = [r for r in studies if r.get("holdout_excess_return") is None]
    if not with_benchmark:
        lines.append("Keine Benchmark-Vergleichsdaten in diesem Lauf verfügbar.")
    else:
        # Issue #1077 (Pitfall #376) — Root-Cause: ``excess / max(exposure, _EXPOSURE_EPSILON)``
        # setzte einen ERFUNDENEN Nenner (1 %) ein, sobald ``exposure`` fehlte oder winzig war —
        # der Kommentar an ``_EXPOSURE_EPSILON`` nannte das selbst ausdrücklich "reine Anzeige-
        # Sicherung, kein kalibrierter Schwellenwert", die Zahl erschien aber UNGEKENNZEICHNET in
        # einer Spalte, die als Qualitätsnormierung gelesen wird — und ordnete monoton nach
        # WENIGER Handel (die grösste Zahl der Tabelle gehörte der Strategie mit 0 Trades). Fix:
        # kein Ersatznenner. Eine Study mit fehlender/zu geringer Exposition (< 5 %) wandert in
        # einen eigenen "nicht bewertbar"-Block statt eine erfundene Normierung in der regulären
        # Rangfolge zu tragen.
        _min_exposure_for_normalization = 0.05
        normal_rows = []
        not_evaluable_rows = []
        for r in with_benchmark:
            exposure = r.get("holdout_exposure_fraction")
            if exposure is None or exposure < _min_exposure_for_normalization:
                not_evaluable_rows.append(r)
            else:
                normal_rows.append(r)
        # Issue #986/#1140 (Katalog #986, Pitfall #412 in AGENTS.md) — Root-Cause: `holdout_excess_
        # return = strategy − benchmark` ist im fallenden Markt für JEDE Strategie mit Exposure
        # < 100 % positiv, unabhängig von jedem Edge (0/39 auf steigenden, 62/65 auf fallenden
        # Symbolen). Fix: (1) primäre Rangfolgengröße ist jetzt `excess_per_unit_exposure` (vorher
        # nur Anzeige-Spalte, Sortierung lief nach `holdout_total_return`) — `report._study_record`
        # liefert das Feld bereits vorberechnet; hier zusaetzlich ein Inline-Fallback (identische
        # Formel) fuer Aufrufer, die ``studies``-Dicts direkt ohne den vollen Report-Pfad bauen
        # (Legacy-JSONs, Tests). (2) Zusätzliche α/β/t(α)-Spalten (OLS-Regression Strategie- vs.
        # Benchmark-Perioden-Returns, backtest_runner._alpha_beta_regression) — ein Kandidat mit
        # β ≈ 0,2 und α ≈ 0 ist kein Edge, sondern ein Teilzeit-Long. |t(α)| < 1 ⇒ NO_ALPHA_DETECTED.
        def _excess_per_exposure(r: dict) -> float | None:
            v = r.get("holdout_excess_per_unit_exposure")
            if v is not None:
                return v
            excess_ = r.get("holdout_excess_return")
            exposure_ = r.get("holdout_exposure_fraction")
            # normal_rows garantiert bereits exposure_ >= _min_exposure_for_normalization.
            return excess_ / exposure_ if excess_ is not None and exposure_ else None

        # Issue #1038/#1187 (Katalog #1187) — Root-Cause: ``α`` (Grössenordnung 1e-6/Bar) mit 5
        # Nachkommastellen zeigte in 13/13 Zeilen ``0.00000``/``-0.00000``/``-0.00001`` — technisch
        # korrekt gerundet, aber ökonomisch unlesbar. Ersetzt durch ``α·n (%)`` (das KUMULIERTE
        # Holdout-Alpha über das gesamte Fenster, n = ``holdout_alpha_n_periods`` — dieselbe Grösse,
        # die die Issue-Referenzwerte −1,450 % … +0,449 % zeigt) plus ``α (bps/Bar)`` (die
        # Rohgrösse selbst, jetzt in einer Einheit, in der 1e-6 nicht auf Null rundet). ``t(α)``
        # bleibt unverändert (bereits eine dimensionslose t-Statistik, keine Rundungsprobe nötig).
        lines.append(
            "| Strategie | Symbol | Strategie-Return | Buy&Hold-Return | Excess | Zeit im Markt | "
            "Excess/Exposure | α·n (%) | α (bps/Bar) | β | t(α) | Vorzeichen |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for r in sorted(normal_rows, key=lambda r: _excess_per_exposure(r) or 0.0, reverse=True):
            excess = r["holdout_excess_return"]
            buyhold = r.get("holdout_buyhold_return")
            exposure = r.get("holdout_exposure_fraction")
            excess_per_exposure = _excess_per_exposure(r)
            alpha_tstat = r.get("holdout_alpha_tstat")
            alpha_times_n_pct = r.get("holdout_alpha_times_n_pct")
            alpha_bps = r.get("holdout_alpha_bps_per_bar")
            if r.get("holdout_no_alpha_detected"):
                sign = "NO_ALPHA_DETECTED (|t(α)| < 1)"
            elif buyhold is not None and buyhold < 0:
                sign = "B&H negativ — Excess trivial positiv"
            elif excess > 0:
                sign = "positiv (schlägt Buy & Hold)"
            elif excess < 0:
                sign = "negativ (unter Buy & Hold)"
            else:
                sign = "neutral"
            lines.append(
                f"| {r.get('strategy')} | {r.get('symbol')} | {_fmt_pct(r.get('holdout_total_return'))} | "
                f"{_fmt_pct(buyhold)} | {_fmt_pct(excess)} | {_fmt_pct(exposure)} | "
                f"{_fmt_pct(excess_per_exposure) if excess_per_exposure is not None else 'k. A.'} | "
                f"{_fmt_num(alpha_times_n_pct, digits=3) + ' %' if alpha_times_n_pct is not None else 'k. A.'} | "
                f"{_fmt_num(alpha_bps, digits=2) if alpha_bps is not None else 'k. A.'} | "
                f"{_fmt_num(r.get('holdout_beta'), digits=3)} | "
                f"{_fmt_num(alpha_tstat, digits=2)} | {sign} |"
            )
        lines.append("")
        if not_evaluable_rows:
            lines.append(
                f"**Nicht bewertbar (Zeit im Markt < {_fmt_pct(_min_exposure_for_normalization)}, "
                "keine sinnvolle Excess/Exposure-Normierung):**"
            )
            lines.append("")
            lines.append("| Strategie | Symbol | Strategie-Return | Buy&Hold-Return | Excess | Zeit im Markt | Trades |")
            lines.append("|---|---|---:|---:|---:|---:|---:|")
            for r in sorted(not_evaluable_rows,
                            key=lambda r: r.get("holdout_total_return") or 0.0, reverse=True):
                exposure = r.get("holdout_exposure_fraction")
                lines.append(
                    f"| {r.get('strategy')} | {r.get('symbol')} | "
                    f"{_fmt_pct(r.get('holdout_total_return'))} | "
                    f"{_fmt_pct(r.get('holdout_buyhold_return'))} | "
                    f"{_fmt_pct(r.get('holdout_excess_return'))} | "
                    f"{_fmt_pct(exposure) if exposure is not None else 'k. A.'} | "
                    f"{r.get('holdout_total_trades', 'k. A.')} |"
                )
            lines.append("")
        decomposition = (report.get("cross_study") or {}).get("excess_variance_decomposition")
        if decomposition and decomposition.get("symbol_share") is not None:
            lines.append(
                "Anteil der Streuung, der auf das Symbol statt auf die Strategie entfällt: "
                f"{_fmt_pct(decomposition['symbol_share'])} (Strategie + Rest: "
                f"{_fmt_pct(decomposition.get('strategy_share'))}, n={decomposition.get('n_rows', 0)}) "
                "— ein hoher Symbol-Anteil bedeutet: der Excess-Return ist in diesem Lauf kein "
                "Strategie-Unterscheidungsmerkmal (#850, Pitfall #268)."
            )
    lines.append("")

    # Issue #1013/#1165 (Katalog #1170, Fix Punkt 2) — dritte Tabelle: Studies OHNE
    # Benchmark-Vergleich, damit sie nicht spurlos aus Abschnitt 2.3 verschwinden.
    if without_benchmark:
        lines.append(
            "**Ohne Benchmark-Vergleich (keine Holdout-Auswertung):** diese Studies haben "
            "keinen auswertbaren Benchmark-Vergleich — entweder wurde der Holdout selbst nie "
            "ausgewertet (Strategie-Return zeigt dann \"k. A.\", NICHT \"0,0 %\", siehe "
            "`report._study_record`), oder es lag keine Benchmark-Preisserie für das Symbol vor."
        )
        lines.append("")
        lines.append("| Strategie | Symbol | Strategie-Return | Trades | Ablehnungsgrund |")
        lines.append("|---|---|---:|---:|---|")
        for r in sorted(without_benchmark, key=lambda r: (r.get("strategy") or "", r.get("symbol") or "")):
            lines.append(
                f"| {r.get('strategy')} | {r.get('symbol')} | "
                f"{_fmt_pct(r.get('holdout_total_return'))} | "
                f"{r.get('holdout_total_trades', 'k. A.')} | "
                f"{r.get('promotion_outcome') or 'k. A.'} |"
            )
        lines.append("")

    # Issue #1071/#1221 (Katalog #1196-1221) — Studies mit strukturell duenner Perioden-Basis
    # (oos_n_periods_median < 1/6 des Medians ihres eigenen Symbols, typisch Squeeze) werden HIER
    # separat ausgewiesen statt unmarkiert in derselben Vergleichstabelle wie ihre gut besetzten
    # Symbol-Geschwister zu erscheinen — ihr annualisierter Sortino beruht auf einer duennen
    # Beobachtungsbasis, unabhaengig davon, dass der Annualisierungsfaktor selbst seit diesem Fix
    # symbolweit stabil ist (siehe backtest_runner._get_annualization_factor_with_source).
    _annualization_excluded = (report.get("cross_study") or {}).get("annualization_excluded_studies") or []
    if _annualization_excluded:
        lines.append(
            "**Von der Annualisierungs-/Sortino-Vergleichbarkeit ausgeschlossen** "
            "(oos_n_periods_median < 1/6 des Symbol-Medians):"
        )
        lines.append("")
        lines.append("| Strategie | Symbol | n_periods (Study) | n_periods-Median (Symbol) | Schwelle |")
        lines.append("|---|---|---:|---:|---:|")
        for e in _annualization_excluded:
            lines.append(
                f"| {e.get('strategy')} | {e.get('symbol')} | "
                f"{_fmt_num(e.get('oos_n_periods_median'), digits=1)} | "
                f"{_fmt_num(e.get('symbol_oos_n_periods_median'), digits=1)} | "
                f"{_fmt_num(e.get('threshold'), digits=1)} |"
            )
        lines.append("")

    # 2.4 Kostenbasis
    lines.append("### 2.4 Kostenbasis")
    lines.append("")
    # Issue #1059/#1208 (Katalog #1196-1221) — Root-Cause: die Slippage-Tabelle unten war OOS-
    # skopiert (Median ueber ALLE Sweep-Trials), stand aber OHNE Scope-Kennzeichnung direkt unter
    # den Holdout-Ertragszahlen (Beweis: SqueezeBreakout/ASML trug 133,29 bps Slippage bei 0
    # Holdout-Trades). Scope-Hinweis analog zu Sektion 4 (Katalog #832 Fix Punkt 1).
    lines.append(
        "**Scope-Hinweis:** die Slippage-Tabelle unten weist ZWEI getrennte Spalten aus — "
        "`Slippage (OOS, Median)` (ueber ALLE OOS-evaluierten Sweep-Trials dieser Study) und "
        "`Slippage (Holdout, Median)` (ausschliesslich aus dem promotierten Holdout-Re-"
        "Evaluations-Pfad, `k. A.` ohne Holdout-Trades) — eine Study OHNE Holdout-Trades kann "
        "einen OOS-Wert tragen, aber NIE einen Holdout-Wert."
    )
    lines.append("")
    lines.append(
        "Alle oben genannten Zahlen sind **simulierte Backtest-Ergebnisse** über das Holdout-"
        "Fenster (45 Tage) unter dem im Lauf konfigurierten Kostenmodell (Spread + Kommission je "
        "Asset-Klasse, #774/#775) — kein garantiertes zukünftiges Ergebnis."
    )
    # Issue #1010/#1162 (Katalog #1170, P0) — Akzeptanzkriterium 2: Abschnitt 2.4 nennt explizit
    # den methodischen Umfang, wenn financing_bps/slippage_bps ueberall 0.0 sind (backtest.json,
    # #987/#1141) — die 'full_realism'-Kostenstress-Stufe ist dann ein No-Op, jede Ertragsaussage
    # oben ist ohne Overnight-Finanzierung, ohne Slippage, ohne Market Impact. Quelle:
    # ``cross_study.cost_model_zero_realism`` (report._cost_model_has_zero_realism) — dieselbe,
    # einzige erlaubte Datenquelle (das bereits geschriebene Report-JSON) wie jede andere Zeile in
    # diesem Modul.
    if (report.get("cross_study") or {}).get("cost_model_zero_realism"):
        lines.append("")
        lines.append(
            "⚠️ **Kalibrierungslücke:** Overnight-Finanzierung und Slippage sind in diesem Lauf "
            "für alle Asset-Klassen mit 0,0 bps konfiguriert (`backtest.json`, unkalibrierte "
            "Platzhalter, #987/#1141) — die Kostenstress-Stufe `full_realism` ist damit ein "
            "No-Op. Jede oben genannte Ertragsaussage gilt **ohne Overnight-Finanzierung, ohne "
            "Slippage, ohne Market Impact** (siehe `invariants.check_cost_stress_distinctness`, "
            "#1010/#1162). Eine Kalibrierung mit realen Broker-Sätzen (Kontoauszug/"
            "Gebührenübersicht) ist ausdrücklich NICHT Teil dieses Fixes."
        )
    # Issue #1029/#1178 (Katalog #866-2) — stop_exit_slippage_bps war in KEINEM Report-Abschnitt
    # und in KEINER Invariante ausgewiesen, obwohl sie in 14/14 Studies eines Referenzlaufs befuellt
    # war (Median −12,41 bps, ~19 % des Median-Stop-Verlusts) — die groesste einzelne, gemessene
    # und bislang ignorierte Ertragsposition. Seitenbereinigt und ADVERS vorzeichenbehaftet (``+``
    # = advers), siehe backtest_runner.resolve_stop_exit_slippage_bps-Docstring.
    _slippage_rows = [
        r for r in studies
        if r.get("stop_exit_slippage_bps") is not None or r.get("round_trip_cost_bps") is not None
    ]
    if _slippage_rows:
        lines.append("")
        lines.append(
            "**Fill-Slippage bei TRAILING_STOP-Exits** (gemessene Ausführungsdifferenz, NICHT "
            "Teil des obigen Kostenmodells — positiv = advers, d. h. schlechter gefüllt als der "
            "Stop-Level):"
        )
        lines.append("")
        # Issue #1059/#1208 Fix — zwei getrennte Spalten statt einer einzelnen (scope-vermischten)
        # Slippage-Spalte; die Holdout-Spalte ist ``k. A.``, solange holdout_total_trades 0/None
        # ist (Akzeptanzkriterium: "Keine Study mit 0 Holdout-Trades trägt in der Holdout-Spalte
        # einen Wert").
        lines.append(
            "| Strategie | Symbol | c_rt (bps) | Slippage (OOS, Median, bps, advers=+) | "
            "Slippage (Holdout, Median, bps, advers=+) |")
        lines.append("|---|---|---:|---:|---:|")
        for r in sorted(_slippage_rows, key=lambda r: (r.get("strategy") or "", r.get("symbol") or "")):
            _has_holdout_trades = bool(r.get("holdout_total_trades"))
            lines.append(
                f"| {r.get('strategy')} | {r.get('symbol')} | "
                f"{_fmt_num(r.get('round_trip_cost_bps'), digits=2)} | "
                f"{_fmt_num(r.get('stop_exit_slippage_bps'), digits=2)} | "
                f"{_fmt_num(r.get('holdout_stop_exit_slippage_bps'), digits=2) if _has_holdout_trades else 'k. A.'} |"
            )
    # Issue #1054/#1203 (Katalog #1196-1221) — Verlust-Zerlegung "realized_loss_bps =
    # stop_distance_bps + trigger_to_fill_gap_bps" (drei Spalten statt einer einzelnen
    # Verlustzahl), damit ein Leser sieht, welcher Anteil des Stop-Verlusts aus der konfigurierten
    # Distanz (k · ATR) und welcher aus der Absetzen-zu-Fill-Latenz stammt (#1203-Root-Cause).
    _decomp_rows = [r for r in studies if r.get("realized_loss_bps") is not None]
    if _decomp_rows:
        lines.append("")
        lines.append(
            "**Verlust-Zerlegung bei TRAILING_STOP-Exits** (Median je Study, bps, advers=+; "
            "`realized_loss_bps = stop_distance_bps + trigger_to_fill_gap_bps`, siehe "
            "`invariants.check_stop_loss_decomposition_identity`):"
        )
        lines.append("")
        lines.append(
            "| Strategie | Symbol | Stopdistanz (bps) | Absetzen-zu-Fill-Gap (bps) | "
            "Realisierter Verlust (bps) |"
        )
        lines.append("|---|---|---:|---:|---:|")
        for r in sorted(_decomp_rows, key=lambda r: (r.get("strategy") or "", r.get("symbol") or "")):
            lines.append(
                f"| {r.get('strategy')} | {r.get('symbol')} | "
                f"{_fmt_num(r.get('stop_distance_bps_measured'), digits=2)} | "
                f"{_fmt_num(r.get('trigger_to_fill_gap_bps'), digits=2)} | "
                f"{_fmt_num(r.get('realized_loss_bps'), digits=2)} |"
            )
    return "\n".join(lines)


def _section_3_duration(report: dict) -> str:
    studies = _studies(report)
    lines = ["## 3. Zeitdauer", ""]

    # 3.1 Gesamtlaufzeit
    lines.append("### 3.1 Gesamtlaufzeit")
    lines.append("")
    cli_args = report.get("cli_args") or {}
    lines.append(f"- Start: {report.get('started_at_utc') or 'k. A.'}")
    lines.append(f"- Gesamtlaufzeit: {_fmt_hours(report.get('wallclock_s'))}")
    lines.append(f"- n_jobs: {cli_args.get('n_jobs', 'k. A.')} (Quelle: {cli_args.get('n_jobs_source', 'k. A.')})")
    lines.append(f"- Lauf-Status: {_run_status_label_de(report)}")
    # Issue #1021/#1196 Fix 4.2 — macht sichtbar, dass dieser Lauf per Warm-Start (Optuna
    # load_if_exists) auf Trials eines VORLAUFS aufsetzt: das veraendert deflation_n_family,
    # constraint_improvement_rate, n_modelled_trials und den TPE-Seed und darf nicht unsichtbar
    # bleiben.
    _store_reuse = (report.get("cross_study") or {}).get("store_reuse") or {}
    if _store_reuse.get("reused"):
        lines.append(
            f"- ⚠️ **Store-Wiederverwendung (Warm-Start):** {_store_reuse.get('studies_affected', 0)} "
            f"Study/Studies setzt/setzen auf Trials von Vorlauf/Vorläufen "
            f"{_store_reuse.get('prior_run_ids', [])} auf ({_store_reuse.get('n_trials_prior', 0)} "
            f"Trials Vorlauf + {_store_reuse.get('n_trials_own', 0)} Trials dieser Lauf) — "
            "beeinflusst deflation_n_family/TPE-Seed, siehe #1021/#1196."
        )
    if report.get("symbols_planned") is not None:
        lines.append(
            f"- Symbole: {report.get('symbols_completed', 'k. A.')} von {report.get('symbols_planned', 'k. A.')} abgeschlossen"
        )
    lines.append("")

    # Issue #851 — Root-Cause behoben: run_optimization._optimize_symbol_impl persistiert jetzt
    # study_wallclock_s/study_started_at_utc/study_ended_at_utc/worker_id als Study-User-Attrs
    # (auch bei vorzeitigem Abbruch, #833-Stil); report.py leitet daraus wallclock_by_strategy ab.
    lines.append("### 3.2 Laufzeit je Symbol/Strategie")
    lines.append("")
    wallclock_by_strategy = (report.get("cross_study") or {}).get("wallclock_by_strategy") or {}
    if not wallclock_by_strategy:
        lines.append(
            "Keine Study-Wallclock-Telemetrie in diesem Report (Pre-#851-Lauf oder leere Kohorte)."
        )
    else:
        lines.append("| Strategie | Median-Wallclock | p90-Wallclock | n Studies |")
        lines.append("|---|---:|---:|---:|")
        for strategy, stats in sorted(
                wallclock_by_strategy.items(), key=lambda kv: kv[1].get("median") or 0.0, reverse=True):
            lines.append(
                f"| {strategy} | {_fmt_hms_from_s(stats.get('median'))} | "
                f"{_fmt_hms_from_s(stats.get('p90'))} | {stats.get('n', 0)} |"
            )
    lines.append("")

    # 3.3 Budgetausführung
    lines.append("### 3.3 Gelaufene vs. budgetierte Trials")
    lines.append("")
    budget = (report.get("cross_study") or {}).get("budget_executed_fraction") or {}
    # Issue #1065/#1215 (Katalog #1196-1221, P2) — Root-Cause: Median/p10 werden ueber STUDIES
    # gebildet, das Defizit selbst ist eine Summe ueber TRIALS — einzelne Studies mit Ausfall
    # verschwinden im Median, solange die Mehrheit ihr Budget voll ausfuehrt (Beweis: c429c992
    # fehlten 5,4% der Trials bei p10=100,0%). ``min`` (das strengste Perzentil) ergaenzt seither
    # Median/p10 — ein einziger vollstaendiger Ausfall macht sich dort UNBEDINGT bemerkbar.
    lines.append(
        f"- Median Budgetausführung: {_fmt_pct(budget.get('median'))} "
        f"(p10: {_fmt_pct(budget.get('p10'))}, min: {_fmt_pct(budget.get('min'))}, "
        f"n={budget.get('n', 0)} Studies)"
    )
    total_completed = sum(r.get("n_trials_completed") or 0 for r in studies)
    total_budgeted = sum(r.get("n_trials_budgeted") or 0 for r in studies)
    lines.append(f"- Trials gesamt: {total_completed} von {total_budgeted} budgetiert")
    # Issue #1065/#1215 Fix Punkt 2 — Akzeptanzkriterium: "Jeder Lauf mit Σ trials < Σ budget nennt
    # die verantwortlichen Studies" (Study, ist, soll, Grund), statt die Luecke nur als Rate zu
    # zeigen (in der ein einzelner Ausfall neben vielen vollstaendigen Studies untergeht).
    if total_completed < total_budgeted:
        _deficit_studies = (report.get("cross_study") or {}).get("budget_deficit_studies") or []
        if _deficit_studies:
            lines.append("")
            lines.append(
                f"  Σ trials ({total_completed}) < Σ budget ({total_budgeted}) — verantwortliche "
                "Studies:"
            )
            for d in sorted(_deficit_studies, key=lambda d: (d.get("strategy") or "", d.get("symbol") or "")):
                lines.append(
                    f"  - {d.get('strategy')}/{d.get('symbol')}: "
                    f"{d.get('n_trials_completed')} von {d.get('n_trials_budgeted')} "
                    f"(Defizit {d.get('deficit')}, Grund: {d.get('stop_reason') or 'k. A.'})"
                )
    lines.append("")

    # 3.4 Verlorene Zeit
    lines.append("### 3.4 Verlorene Zeit: abgebrochene Studies")
    lines.append("")
    stop_reasons: dict[str, int] = {}
    for r in studies:
        reason = r.get("stop_reason")
        if reason:
            stop_reasons[reason] = stop_reasons.get(reason, 0) + 1
    if not stop_reasons:
        lines.append("Keine Studies mit dokumentiertem vorzeitigem Abbruchgrund.")
    else:
        for reason, n in sorted(stop_reasons.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- {reason}: {n} Studies")
    lines.append("")

    # Issue #851 — Barriere-Wartezeit (die Zeit, die ein Symbol auf seine langsamste Strategie
    # wartet, #828) UND Worker-Auslastung, jetzt aus der Study-Zeitstempel-Telemetrie ableitbar.
    barrier_wait = (report.get("cross_study") or {}).get("symbol_barrier_wait_s") or {}
    if not barrier_wait:
        lines.append(
            "Keine Barriere-Wartezeit-Telemetrie in diesem Report (Pre-#851-Lauf, leere Kohorte, "
            "oder jedes Symbol hatte nur eine Strategie-Study)."
        )
    else:
        worst = sorted(barrier_wait.items(), key=lambda kv: kv[1], reverse=True)[:5]
        lines.append(
            f"Barriere-Wartezeit (Symbol-Wallclock minus schnellste Study) — die {len(worst)} "
            f"Symbole mit der längsten Wartezeit:"
        )
        for symbol, wait_s in worst:
            lines.append(f"- {symbol}: {_fmt_hms_from_s(wait_s)}")
    # Issue #1038 (Katalog #866), umbenannt #949/#1115 (Katalog #960) — zwei GETRENNTE, eindeutig
    # benannte Groessen (vormals ``worker_utilisation``/``worker_utilisation_backtest_ms``, beide
    # implizit "Worker-Auslastung" genannt, B-6): ``worker_occupancy_wallclock`` kann strukturell
    # > 100 % liegen (verschachtelte Worker-Pools je Study überlappen sich, siehe
    # report._worker_occupancy_wallclock-Docstring) und ist deshalb explizit als "über Kapazität",
    # nicht als Auslastung, beschriftet; ``cpu_utilisation_backtest`` (echte, ueberlappungsfreie
    # Backtest-CPU-Zeit) ist die Grösse, die tatsächlich <= 100 % liegen sollte
    # (``check_worker_utilisation_plausible`` prueft GENAU ``worker_occupancy_wallclock``).
    worker_occupancy_wallclock = (report.get("cross_study") or {}).get("worker_occupancy_wallclock")
    cpu_utilisation_backtest = (report.get("cross_study") or {}).get("cpu_utilisation_backtest")
    if worker_occupancy_wallclock is not None:
        lines.append(f"\nStudy-Wallclock über Kapazität (worker_occupancy_wallclock = Σ "
                     f"Study-Wallclock / (n_jobs × Sweep-Wallclock); kann > 100 % liegen, siehe "
                     f"#1038): {_fmt_pct(worker_occupancy_wallclock)}")
    if cpu_utilisation_backtest is not None:
        lines.append(f"Echte CPU-Auslastung (cpu_utilisation_backtest = Σ Backtest-CPU-Zeit je "
                     f"Trial / (n_jobs × Sweep-Wallclock)): {_fmt_pct(cpu_utilisation_backtest)}")
    lines.append("")

    # Issue #1027/#1176 Schritt 1 (Katalog #866-2) — Sichtbarkeit ohne Semantikbruch: die
    # synthetische 1h-Bar-Erzeugung kennt fuer EQUITY/COMMODITY keine Handelszeiten-Maske
    # (invariants.check_session_calendar_coherence); bis eine RTH-Maske gebaut ist (Schritt 2,
    # eigener Semantik-Bump + Pflicht-Purge, siehe AGENTS.md-Sperrvermerk), macht dieser Abschnitt
    # den Bar-Achsen-Zustand je Study SICHTBAR, statt ihn implizit anzunehmen.
    lines.append("### 3.5 Bar-Achse / Handelszeiten-Abdeckung")
    lines.append("")
    _bar_axis_rows = [
        r for r in studies
        if r.get("bars_per_calendar_day") is not None or r.get("session_coverage_fraction") is not None
    ]
    if not _bar_axis_rows:
        lines.append(
            "Keine Bar-Achsen-Telemetrie in diesem Report (Pre-#1011/#1163-Lauf oder leere Kohorte)."
        )
    else:
        lines.append(
            "`bars_per_calendar_day` > 8 auf EQUITY/COMMODITY ist die Signatur einer 24/7-"
            "aufgefüllten Bar-Achse (24,0 = kein Handelszeiten-Filter); "
            "`session_coverage_fraction` ist der Anteil der Bars innerhalb der erwarteten Session "
            "(siehe `invariants.check_session_calendar_coherence`, #1011/#1163/#1027/#1176)."
        )
        lines.append("")
        lines.append("| Strategie | Symbol | Bars/Kalendertag | Session-Abdeckung |")
        lines.append("|---|---|---:|---:|")
        for r in sorted(_bar_axis_rows, key=lambda r: (r.get("strategy") or "", r.get("symbol") or "")):
            lines.append(
                f"| {r.get('strategy')} | {r.get('symbol')} | "
                f"{_fmt_num(r.get('bars_per_calendar_day'), digits=2)} | "
                f"{_fmt_pct(r.get('session_coverage_fraction'))} |"
            )
    return "\n".join(lines)


def _section_4_longest_trades(report: dict) -> str:
    lines = ["## 4. Trades mit der längsten Haltedauer", ""]
    lines.append(
        "**Scope-Hinweis:** diese Sektion listet die längste beobachtete Haltedauer JE STUDY "
        "(Strategie/Symbol, Maximum über alle OOS-evaluierten Trials), NICHT einzelne Trades mit "
        "Entry-/Exit-Zeitstempel — siehe Modul-Docstring für die Begründung dieser Scope-"
        "Entscheidung (Katalog #832 Fix Punkt 1)."
    )
    lines.append("")
    # Issue #1011/#1163 (Katalog #1170, Fix Punkt 3, Akzeptanzkriterium 2) — die synthetische 1h-
    # Bar-Achse ist ueber einen 24/7-Kalender aufgefuellt (siehe invariants.check_session_calendar_
    # coherence): "24,00 h" ist deshalb NICHT "~1 Handelstag" (RTH-Naeherung), sondern buchstaeblich
    # 1 KALENDERtag. Die Entscheidung, die Bar-Erzeugung auf RTH umzustellen, ist ein eigener
    # Folge-Issue -- diese Sektion stellt nur die Messung/Lesart klar.
    lines.append(
        "**Lesart-Hinweis:** die Haltedauer beruht auf der synthetischen 1h-Bar-Achse, die "
        "aktuell über einen 24/7-Kalender aufgefüllt wird (siehe `invariants.check_session_"
        "calendar_coherence`) — \"24,00 h\" bedeutet **24 synthetische Bars = 1 Kalendertag**, "
        "NICHT \"~1 Handelstag\". Eine Umstellung der Bar-Erzeugung auf reale Handelszeiten (RTH) "
        "ist ein eigener Folge-Issue (#1011/#1163)."
    )
    lines.append("")
    longest = (report.get("cross_study") or {}).get("longest_holding_studies") or []
    if not longest:
        lines.append("Keine Haltedauer-Telemetrie in diesem Report (Pre-#832-Lauf oder leere Kohorte).")
    else:
        lines.append("| Strategie | Symbol | Max. Haltedauer | P95 Haltedauer |")
        lines.append("|---|---|---:|---:|")
        for entry in longest:
            lines.append(
                f"| {entry.get('strategy')} | {entry.get('symbol')} | "
                f"{_fmt_holding_duration_with_bar_note(entry.get('max_holding_time_s'))} | "
                f"{_fmt_holding_duration_with_bar_note(entry.get('p95_holding_time_s'))} |"
            )

    # Issue #1030/#1179 (Katalog #866-2) — TIME_BOX ist mit ~49 % der haeufigste Exit-Mechanismus
    # ueberhaupt, misst aber KALENDER-Bars auf der (bis #1176 Schritt 2) 24/7-aufgefuellten Achse:
    # 24 Bars sind heute buchstaeblich 1 Kalendertag, nicht ~1 Handelstag. Bis zur RTH-Umstellung
    # (Schritt 2) wird der Median zusaetzlich in HANDELSSTUNDEN ausgewiesen
    # (bars · session_coverage_fraction), damit ein Leser die beiden Achsen nicht verwechselt.
    _timebox_rows = (report.get("studies") or [])
    _timebox_rows = [
        r for r in _timebox_rows
        if r.get("time_box_exit_fraction") is not None or r.get("median_bars_held") is not None
    ]
    if _timebox_rows:
        lines.append("")
        lines.append("### 4.1 Zeitbox-Anteil und Median-Haltedauer (Kalender- vs. Handelszeit)")
        lines.append("")
        lines.append(
            "`Handels-Bars (geschätzt)` = `Median-Bars · session_coverage_fraction` — solange die "
            "Bar-Achse ungefiltert 24/7 läuft (#1027/#1176 Schritt 1), ist das eine grobe Näherung, "
            "keine echte Handelszeiten-Zählung (die kommt erst mit Schritt 2)."
        )
        lines.append("")
        lines.append(
            "| Strategie | Symbol | TIME_BOX-Anteil | Median-Bars (Kalender) | "
            "Handels-Bars (geschätzt) |"
        )
        lines.append("|---|---|---:|---:|---:|")
        for r in sorted(_timebox_rows, key=lambda r: (r.get("strategy") or "", r.get("symbol") or "")):
            _bars = r.get("median_bars_held")
            _coverage = r.get("session_coverage_fraction")
            _trading_bars = (
                round(_bars * _coverage, 2) if _bars is not None and _coverage is not None else None)
            lines.append(
                f"| {r.get('strategy')} | {r.get('symbol')} | "
                f"{_fmt_pct(r.get('time_box_exit_fraction'))} | "
                f"{_fmt_num(_bars, digits=2)} | "
                f"{_fmt_num(_trading_bars, digits=2)} |"
            )
    return "\n".join(lines)


def _section_5_anomalies(report: dict) -> str:
    studies = _studies(report)
    lines = ["## 5. Auffälligkeiten", ""]

    all_checks = report.get("invariant_checks") or []
    # Issue #995/#1147 (Pitfall #413 in AGENTS.md) — "nicht auswertbar" (``passed is None`` bzw.
    # ``evaluable is False``, siehe ``invariants.InvariantResult``) ist KEIN PASS, gehoert aber auch
    # NICHT in dieselbe Tabelle wie ein echtes, NACHGEWIESENES FAIL (``passed is False``) — sonst
    # waere ein Check, der seine Grundgesamtheit nicht herstellen konnte, von einem tatsaechlich
    # defekten Mechanismus nicht mehr unterscheidbar. Beide Zustaende werden deshalb getrennt
    # gezaehlt und in getrennten Tabellen gefuehrt.
    failing_checks = [c for c in all_checks if c.get("passed") is False]
    inconclusive_checks = [
        c for c in all_checks
        if c.get("passed") is None or c.get("evaluable") is False]

    # Issue #849 — Root-Cause der 519-Zeilen-Sektion: JEDER einzelne FAIL war eine gleichrangige
    # Zeile (304× check_reward_term_variance neben 1× check_holding_time_cap, dem eigentlich
    # wichtigsten Befund). Ab hier: EINE Zeile je Check (5.1), Details nur noch als begrenzte
    # Stichprobe je Check (5.2) — Reihenfolge in BEIDEN Abschnitten identisch (nach Schweregrad,
    # dann nach FAIL-Anzahl absteigend).
    by_check: dict[str, list[dict]] = {}
    for c in failing_checks:
        by_check.setdefault(_check_name(c), []).append(c)
    ordered_names = sorted(
        by_check.keys(),
        key=lambda name: (
            _SEVERITY_ORDER.get(by_check[name][0].get("severity", "medium"), 2),
            -len(by_check[name]),
            name,
        ),
    )

    max_details = report.get("summary_max_details_per_check")
    if not isinstance(max_details, int) or max_details < 1:
        max_details = 5

    lines.append(f"### 5.1 Übersicht — Invarianten-FAILs ({len(failing_checks)})")
    lines.append("")
    if not ordered_names:
        lines.append("Keine.")
    else:
        lines.append("| Check | FAILs | betroffene Studies | Schweregrad |")
        lines.append("|---|---:|---:|---|")
        for name in ordered_names:
            entries = by_check[name]
            n_studies_affected = len({c.get("scope") for c in entries})
            severity = entries[0].get("severity", "medium")
            lines.append(f"| {name} | {len(entries)} | {n_studies_affected} | {severity} |")
    lines.append("")

    # Issue #995/#1147 Fix Punkt 2 — eigene Zeile/Tabelle "nicht auswertbar", NICHT unter den
    # bestandenen (und NICHT unter den FAILs) gefuehrt.
    by_check_inconclusive: dict[str, list[dict]] = {}
    for c in inconclusive_checks:
        by_check_inconclusive.setdefault(_check_name(c), []).append(c)
    inconclusive_names = sorted(
        by_check_inconclusive.keys(),
        key=lambda name: (
            _SEVERITY_ORDER.get(by_check_inconclusive[name][0].get("severity", "medium"), 2),
            -len(by_check_inconclusive[name]),
            name,
        ),
    )
    lines.append(f"### 5.1b Nicht auswertbar ({len(inconclusive_checks)} Checks)")
    lines.append("")
    if not inconclusive_names:
        lines.append("Keine.")
    else:
        lines.append(
            "Grundgesamtheit konnte nicht hergestellt werden — kein PASS im Sinne einer "
            "geprüften Population, aber auch kein nachgewiesenes FAIL (siehe Pitfall #413).")
        lines.append("")
        lines.append("| Check | Vorkommen | betroffene Studies | Schweregrad |")
        lines.append("|---|---:|---:|---|")
        for name in inconclusive_names:
            entries = by_check_inconclusive[name]
            n_studies_affected = len({c.get("scope") for c in entries})
            severity = entries[0].get("severity", "medium")
            lines.append(f"| {name} | {len(entries)} | {n_studies_affected} | {severity} |")
    lines.append("")

    lines.append("### 5.2 Details")
    lines.append("")
    if not ordered_names:
        lines.append("Keine.")
    else:
        for name in ordered_names:
            entries = by_check[name]
            lines.append(f"**{name}**")
            lines.append("")
            for c in entries[:max_details]:
                lines.append(f"- (scope={c.get('scope')}): {c.get('detail')}")
            remaining = len(entries) - max_details
            if remaining > 0:
                lines.append(f"- … und {remaining} weitere")
            lines.append("")

    # Issue #1063/#1213 (Katalog #1196-1221) — vormals strukturell IMMER 0 (die Bruecke
    # ``study_guard_dominated`` -> Study-Record fehlte, siehe report._study_record-Feldkommentar).
    _guard_dominated_studies = [
        f"{r.get('strategy')}/{r.get('symbol')}" for r in studies if r.get("study_guard_dominated")]
    n_guard_dominated = len(_guard_dominated_studies)
    total_liquidated = sum(r.get("liquidated_trials") or 0 for r in studies)
    total_boundary = len((report.get("cross_study") or {}).get("boundary_solutions") or [])
    diagnosed = (report.get("cross_study") or {}).get("diagnosed_pairs") or []
    n_denylisted = sum(1 for d in diagnosed if d.get("action") == "denylist")
    n_deprioritized = sum(1 for d in diagnosed if d.get("action") == "deprioritized")

    lines.append("### 5.3 Zusammenfassung")
    lines.append("")
    lines.append(f"- Guard-dominierte Studies (SORTINO_GUARD_TRIPPED-Mehrheit, #823): {n_guard_dominated}")
    # Issue #1063/#1213 Fix — die LISTE der dominierten Studies, nicht nur die Zahl
    # (Akzeptanzkriterium: "Für jede Study mit Zensur-Anteil > 50% erscheint sie in §5.3").
    if _guard_dominated_studies:
        lines.append(f"  - {', '.join(sorted(_guard_dominated_studies))}")
    lines.append(f"- Wirtschaftlich ruinierte Trials (EQUITY_NONPOSITIVE, #801/#825): {total_liquidated}")
    lines.append(f"- Randlösungen mit Bounds-Vorschlag (#831): {total_boundary}")
    lines.append(f"- Automatisch denylistete Paare (#829/#830): {n_denylisted}")
    lines.append(f"- Budget-deprioritisierte Paare (#830): {n_deprioritized}")
    # Issue #1026/#1175 (Katalog #866-2) — ATR-Floor-Bindung war bislang NUR im rohen
    # invariant_checks-Provenance-Blob sichtbar, nicht in der Zusammenfassung selbst.
    _atr_floor = (report.get("cross_study") or {}).get("atr_floor_binding_studies") or {}
    if _atr_floor.get("evaluable") is False:
        lines.append(
            "- ATR-Floor-gebundene Studies: nicht auswertbar (atr_raw_median_bps/"
            "atr_floor_bps_derived fehlen in jeder Study, #1026/#1175)")
    else:
        _atr_floor_studies = _atr_floor.get("studies") or []
        lines.append(f"- ATR-Floor-gebundene Studies (#1175): {len(_atr_floor_studies)}")
        if _atr_floor_studies:
            lines.append(f"  {', '.join(_atr_floor_studies)}")

    # Issue #1040/#1189 (Katalog #1189) — Root-Cause: ``boundary_veto_evidence`` belegt aktive
    # Overrides bereits INDIREKT (active_bounds vs. default_bounds je klemmendem Parameter EINES
    # Gewinner-Trials), waehrend der Report gleichzeitig "diagnosed_pairs: []"/"Automatisch
    # denylistete Paare: 0" meldete — ein Leser konnte nicht erkennen, dass z. B. TrendPullback.
    # ema_period ueber [5, 25] statt der kuratierten Default-Bounds [50, 300] gesucht wurde. Eigener
    # Abschnitt, NUR wenn nicht leer (Fix-Vorgabe #1040) — kein leerer Abschnitt in jedem Report.
    # Issue #1064/#1214 (Katalog #1196-1221, P2) — Root-Cause: die Sektion rendert vormals das
    # GESAMTE kuratierte Inventar (``bounds.active_bounds_overrides()``, ALLE Strategie/Symbol-
    # Paare mit einem Override, unabhaengig davon, ob dieser Lauf das Symbol ueberhaupt enthielt),
    # statt der in DIESEM Lauf tatsaechlich angewandten Overrides — ein Leser eines NATGAS-Reports
    # sah dieselben 18 TSLA-Zeilen wie ein TSLA-Report. Fix: auf ``symbol ∈`` die Symbole DIESES
    # Laufs filtern (aus ``studies``, derselben Quelle wie jede andere Report-Zeile).
    _run_symbols = {r.get("symbol") for r in studies if r.get("symbol")}
    _active_overrides = [
        o for o in ((report.get("cross_study") or {}).get("active_bounds_overrides") or [])
        if o.get("symbol") in _run_symbols
    ]
    lines.append("")
    lines.append(f"### 5.4 Aktive Suchraum-Overrides ({len(_active_overrides)})")
    lines.append("")
    if not _active_overrides:
        lines.append("Keine aktiven Overrides für die Symbole dieses Laufs.")
    else:
        lines.append("| Strategie | Symbol | Parameter | Aktiv | Default | Quelle | Lauf | Begründung |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for o in sorted(_active_overrides, key=lambda o: (
                o.get("strategy") or "", o.get("symbol") or "", o.get("parameter") or "")):
            active_b = o.get("active_bounds")
            default_b = o.get("default_bounds")
            # Issue #1064/#1214 Fix — ``set_in_run_id`` ist fuer kuratierte Eintraege PER DESIGN
            # ``None`` (ein statischer Config-Eintrag ist an keinen einzelnen Lauf gebunden, siehe
            # bounds.active_bounds_overrides-Docstring) — das ist kein "unbekannt" (k. A.), sondern
            # die explizite Herkunftsangabe "curated".
            _lauf = "curated" if o.get("source") == "curated" else (o.get("set_in_run_id") or "k. A.")
            lines.append(
                f"| {o.get('strategy')} | {o.get('symbol')} | {o.get('parameter')} | "
                f"{active_b if active_b else 'k. A.'} | {default_b if default_b else 'k. A.'} | "
                f"{o.get('source') or 'k. A.'} | {_lauf} | "
                f"{o.get('rationale') or 'k. A.'} |"
            )
    return "\n".join(lines)


def generate_german_summary(report: dict, *, report_sha256: str | None = None) -> str:
    """Issue #832 — baut den vollständigen deutschsprachigen Abschlussbericht AUSSCHLIESSLICH aus
    ``report`` (dem bereits erzeugten #742-Report-Dict). Reine Funktion, kein I/O.

    Issue #1024 (Katalog #866) — ``report_sha256`` (Default ``None``, bit-identisch zum
    Pre-Fix-Verhalten): der SHA-256-Hash der Report-JSON-DATEI, aus der dieser Text erzeugt wurde.
    Im Header eingebettet, macht er eine spaetere Divergenz zwischen committeter ``.md`` und
    committetem ``.json`` DIREKT nachweisbar (Root-Cause #1024: die beiden Artefakte des
    ``34b99e6e``-Laufs beschrieben nachweislich verschiedene Daten — 219 Diff-Zeilen, inklusive
    Vorzeichenwechsel beim TSLA-Buy&Hold — ohne dass ein Leser das am Text selbst erkennen konnte)."""
    run_id = report.get("run_id", "unbekannt")
    header = f"# Sweep-Zusammenfassung {run_id}\n"
    if report_sha256:
        header += f"<!-- report_sha256: {report_sha256} -->\n"
    sections = [
        _section_1_result_in_one_sentence(report),
        _section_2_monetary_result(report),
        _section_3_duration(report),
        _section_4_longest_trades(report),
        _section_5_anomalies(report),
    ]
    return header + "\n\n" + "\n\n".join(sections) + "\n"


def write_german_summary(
    report: dict, *, logs_dir: Path | None = None, report_sha256: str | None = None,
) -> Path:
    """Issue #832 Fix Punkt 2 — schreibt ``logs/zusammenfassung_<run_id>.md``. Fail-open: ein
    Schreibfehler wird geloggt, aber propagiert NICHT (der Aufrufer, sweep.main(), darf den Sweep
    nie deswegen als fehlgeschlagen behandeln — analog Champion-Store/Retention).

    Issue #1024 (Katalog #866) — ``report_sha256`` wird unveraendert an ``generate_german_summary``
    durchgereicht (siehe dortiger Docstring)."""
    if logs_dir is None:
        from automation.optimizer.trial_config import config_dir
        logs_dir = config_dir().parent.parent / "logs"
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    run_id = report.get("run_id", "unbekannt")
    out_path = logs_dir / f"zusammenfassung_{run_id}.md"
    out_path.write_text(generate_german_summary(report, report_sha256=report_sha256), encoding="utf-8")
    return out_path


def write_german_summary_for_report_path(report_path: Path, *, logs_dir: Path | None = None) -> Path | None:
    """Issue #832 Fix Punkt 2 — Aufruf-Wrapper für ``sweep.main()``: liest das gerade geschriebene
    #742-Report-JSON von der Platte und erzeugt daraus die Zusammenfassung. Fail-open (``None``
    bei jedem Lese-/Schreibfehler) — non-fatal, analog dem Report-Block selbst.

    Issue #1024 (Katalog #866) Fix Punkt 1 — stempelt den SHA-256 der GENAU GELESENEN Report-Datei
    in den ``.md``-Header (``generate_german_summary``s ``report_sha256``), damit eine spaetere
    Regeneration die Prüfsumme vor jedem inhaltlichen Diff prüfen kann."""
    import json
    from automation.optimizer.manifest import sha256_file
    try:
        report_path = Path(report_path)
        report = json.loads(report_path.read_text("utf-8"))
        return write_german_summary(
            report, logs_dir=logs_dir, report_sha256=sha256_file(report_path))
    except Exception:
        _log.warning(
            "[#832] Deutsche Zusammenfassung konnte nicht erzeugt werden (non-fatal).", exc_info=True)
        return None
