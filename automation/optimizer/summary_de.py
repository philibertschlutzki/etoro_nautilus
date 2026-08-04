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
}


def _fmt_pct(x: float | None, *, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f} %" if x is not None else "k. A."


def _fmt_num(x: float | None, *, digits: int = 4) -> str:
    return f"{x:.{digits}f}" if isinstance(x, (int, float)) else "k. A."


def _fmt_hours(seconds: float | None) -> str:
    return f"{seconds / 3600.0:.2f} h" if seconds is not None else "k. A."


def _fmt_hms_from_s(seconds: float | None) -> str:
    if seconds is None:
        return "k. A."
    h = seconds / 3600.0
    return f"{h:.2f} h ({seconds:.0f} s)"


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
    n_deployable = sum(int(v) for k, v in counts.items() if k in _DEPLOYABLE_STATUSES)
    run_status = report.get("run_status", "complete")
    status_note = ""
    if run_status != "complete":
        status_note = (
            f" **Hinweis:** dieser Lauf ist NICHT vollständig ({_RUN_STATUS_LABELS_DE.get(run_status, run_status)}"
            f"; {report.get('symbols_completed', '?')}/{report.get('symbols_planned', '?')} Symbole"
            " abgeschlossen) — die folgenden Zahlen beziehen sich NUR auf die bereits abgeschlossene Kohorte."
        )
    if n_deployable == 0:
        sentence = (
            f"{n_studies} Studies, 0 Promotionen — kein Parametervektor hat die Holdout-Validierung "
            "bestanden. Es gibt kein deploybares Ergebnis aus diesem Lauf."
        )
    else:
        sentence = (
            f"{n_studies} Studies, {n_deployable} Promotion(en) (READY_FOR_PR/PROMOTE_GLOBAL_DEFAULT) — "
            "siehe Abschnitt 2 für die Details je Kandidat."
        )
    # Issue #849 Punkt 4 — blockierende Invarianten-FAILs (severity='blocking', z. B.
    # check_holding_time_cap/check_required_config_keys) muessen bereits HIER namentlich auftauchen,
    # nicht erst in Sektion 5 (vorher: hinter 304 Meldungen ueber inerte Reward-Terme verborgen).
    blocking_fails = sorted({
        _check_name(c) for c in (report.get("invariant_checks") or [])
        if not c.get("passed", True) and c.get("severity") == "blocking"
    })
    blocking_note = ""
    if blocking_fails:
        blocking_note = (
            f" **BLOCKIERENDE Invarianten-FAIL(s):** {', '.join(blocking_fails)} — siehe Abschnitt "
            "5.1 für Details."
        )
    return "## 1. Ergebnis in einem Satz\n\n" + sentence + status_note + blocking_note


def _section_2_monetary_result(report: dict) -> str:
    studies = _studies(report)
    counts = (report.get("cross_study") or {}).get("promotion_outcome_counts") or {}
    n_deployable = sum(int(v) for k, v in counts.items() if k in _DEPLOYABLE_STATUSES)
    lines = ["## 2. Monetäres Ergebnis", ""]

    # 2.1 Deploybar
    lines.append("### 2.1 Deploybar (Status READY_FOR_PR / PROMOTE_GLOBAL_DEFAULT)")
    lines.append("")
    deployable = [r for r in studies if r.get("promotion_outcome") in _DEPLOYABLE_STATUSES]
    if not deployable:
        lines.append(
            "**Kein deploybares Ergebnis aus diesem Lauf.** Kein Kandidat hat die Holdout-"
            "Validierung bestanden — alle folgenden Zahlen in Abschnitt 2.2 sind ABGELEHNTE, "
            "NICHT handelbare Kandidaten."
        )
    else:
        lines.append("| Strategie | Symbol | Holdout-Return | Expectancy | Win-Rate | Profit-Faktor | Trades |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for r in deployable:
            lines.append(
                f"| {r.get('strategy')} | {r.get('symbol')} | {_fmt_pct(r.get('holdout_total_return'))} | "
                f"{_fmt_num(r.get('holdout_expectancy'))} | {_fmt_pct(r.get('holdout_win_rate'))} | "
                f"{_fmt_num(r.get('holdout_profit_factor'), digits=2)} | "
                f"{r.get('holdout_total_trades', 'k. A.')} |"
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
        lines.append("| Strategie | Symbol | Holdout-Return (simuliert) | Ablehnungsgrund |")
        lines.append("|---|---|---:|---|")
        for strat, r in sorted(rejected_by_strategy.items()):
            reason = r.get("promotion_outcome") or "k. A."
            lines.append(
                f"| {strat} | {r.get('symbol')} | {_fmt_pct(r.get('holdout_total_return'))} | {reason} |"
            )
    lines.append("")

    # 2.3 Vergleich gegen Buy & Hold
    lines.append("### 2.3 Vergleich gegen Buy & Hold je Symbol")
    lines.append("")
    with_benchmark = [r for r in studies if r.get("holdout_excess_return") is not None]
    if not with_benchmark:
        lines.append("Keine Benchmark-Vergleichsdaten in diesem Lauf verfügbar.")
    else:
        lines.append("| Strategie | Symbol | Excess-Return ggü. Buy & Hold | Vorzeichen |")
        lines.append("|---|---|---:|---|")
        for r in sorted(with_benchmark, key=lambda r: r.get("holdout_excess_return") or 0.0, reverse=True):
            excess = r["holdout_excess_return"]
            sign = "positiv (schlägt Buy & Hold)" if excess > 0 else (
                "negativ (unter Buy & Hold)" if excess < 0 else "neutral")
            lines.append(f"| {r.get('strategy')} | {r.get('symbol')} | {_fmt_pct(excess)} | {sign} |")
    lines.append("")

    # 2.4 Kostenbasis
    lines.append("### 2.4 Kostenbasis")
    lines.append("")
    lines.append(
        "Alle oben genannten Zahlen sind **simulierte Backtest-Ergebnisse** über das Holdout-"
        "Fenster (45 Tage) unter dem im Lauf konfigurierten Kostenmodell (Spread + Kommission je "
        "Asset-Klasse, #774/#775) — kein garantiertes zukünftiges Ergebnis."
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
    lines.append(f"- Lauf-Status: {_RUN_STATUS_LABELS_DE.get(report.get('run_status', 'complete'), report.get('run_status'))}")
    if report.get("symbols_planned") is not None:
        lines.append(
            f"- Symbole: {report.get('symbols_completed', 'k. A.')} von {report.get('symbols_planned', 'k. A.')} abgeschlossen"
        )
    lines.append("")

    # 3.2 Laufzeit je Symbol/Strategie — aus n_trials_completed/backtest_ms nicht rekonstruierbar
    # ohne trial-Level-Daten (Report enthält keine Backtest-Zeit je Study); dokumentiert als
    # bekannte Lücke statt einer erfundenen Zahl.
    lines.append("### 3.2 Laufzeit je Symbol/Strategie")
    lines.append("")
    lines.append(
        "Der #742-Report führt keine Wallclock-Zeit je einzelner Study (nur die Sweep-Gesamtzeit "
        "aus 3.1); eine Aufschlüsselung je Symbol/Strategie ist aus diesem Artefakt nicht ableitbar."
    )
    lines.append("")

    # 3.3 Budgetausführung
    lines.append("### 3.3 Gelaufene vs. budgetierte Trials")
    lines.append("")
    budget = (report.get("cross_study") or {}).get("budget_executed_fraction") or {}
    lines.append(
        f"- Median Budgetausführung: {_fmt_pct(budget.get('median'))} "
        f"(p10: {_fmt_pct(budget.get('p10'))}, n={budget.get('n', 0)} Studies)"
    )
    total_completed = sum(r.get("n_trials_completed") or 0 for r in studies)
    total_budgeted = sum(r.get("n_trials_budgeted") or 0 for r in studies)
    lines.append(f"- Trials gesamt: {total_completed} von {total_budgeted} budgetiert")
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
    lines.append(
        "\nBarriere-Wartezeit (die Zeit, die ein Symbol auf seine langsamste Strategie wartet, "
        "#828) ist aus dem #742-Report nicht rekonstruierbar — dafür wäre eine je-Study-"
        "Zeitstempel-Telemetrie nötig, die dieser Report-Typ nicht führt."
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
    longest = (report.get("cross_study") or {}).get("longest_holding_studies") or []
    if not longest:
        lines.append("Keine Haltedauer-Telemetrie in diesem Report (Pre-#832-Lauf oder leere Kohorte).")
        return "\n".join(lines)
    lines.append("| Strategie | Symbol | Max. Haltedauer | P95 Haltedauer |")
    lines.append("|---|---|---:|---:|")
    for entry in longest:
        lines.append(
            f"| {entry.get('strategy')} | {entry.get('symbol')} | "
            f"{_fmt_hms_from_s(entry.get('max_holding_time_s'))} | "
            f"{_fmt_hms_from_s(entry.get('p95_holding_time_s'))} |"
        )
    return "\n".join(lines)


def _section_5_anomalies(report: dict) -> str:
    studies = _studies(report)
    lines = ["## 5. Auffälligkeiten", ""]

    failing_checks = [c for c in (report.get("invariant_checks") or []) if not c.get("passed", True)]

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

    n_guard_dominated = sum(1 for r in studies if r.get("study_guard_dominated"))
    total_liquidated = sum(r.get("liquidated_trials") or 0 for r in studies)
    total_boundary = len((report.get("cross_study") or {}).get("boundary_solutions") or [])
    diagnosed = (report.get("cross_study") or {}).get("diagnosed_pairs") or []
    n_denylisted = sum(1 for d in diagnosed if d.get("action") == "denylist")
    n_deprioritized = sum(1 for d in diagnosed if d.get("action") == "deprioritized")

    lines.append("### 5.3 Zusammenfassung")
    lines.append("")
    lines.append(f"- Guard-dominierte Studies (SORTINO_GUARD_TRIPPED-Mehrheit, #823): {n_guard_dominated}")
    lines.append(f"- Wirtschaftlich ruinierte Trials (EQUITY_NONPOSITIVE, #801/#825): {total_liquidated}")
    lines.append(f"- Randlösungen mit Bounds-Vorschlag (#831): {total_boundary}")
    lines.append(f"- Automatisch denylistete Paare (#829/#830): {n_denylisted}")
    lines.append(f"- Budget-deprioritisierte Paare (#830): {n_deprioritized}")
    return "\n".join(lines)


def generate_german_summary(report: dict) -> str:
    """Issue #832 — baut den vollständigen deutschsprachigen Abschlussbericht AUSSCHLIESSLICH aus
    ``report`` (dem bereits erzeugten #742-Report-Dict). Reine Funktion, kein I/O."""
    run_id = report.get("run_id", "unbekannt")
    header = f"# Sweep-Zusammenfassung {run_id}\n"
    sections = [
        _section_1_result_in_one_sentence(report),
        _section_2_monetary_result(report),
        _section_3_duration(report),
        _section_4_longest_trades(report),
        _section_5_anomalies(report),
    ]
    return header + "\n\n" + "\n\n".join(sections) + "\n"


def write_german_summary(report: dict, *, logs_dir: Path | None = None) -> Path:
    """Issue #832 Fix Punkt 2 — schreibt ``logs/zusammenfassung_<run_id>.md``. Fail-open: ein
    Schreibfehler wird geloggt, aber propagiert NICHT (der Aufrufer, sweep.main(), darf den Sweep
    nie deswegen als fehlgeschlagen behandeln — analog Champion-Store/Retention)."""
    if logs_dir is None:
        from automation.optimizer.trial_config import config_dir
        logs_dir = config_dir().parent.parent / "logs"
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    run_id = report.get("run_id", "unbekannt")
    out_path = logs_dir / f"zusammenfassung_{run_id}.md"
    out_path.write_text(generate_german_summary(report), encoding="utf-8")
    return out_path


def write_german_summary_for_report_path(report_path: Path, *, logs_dir: Path | None = None) -> Path | None:
    """Issue #832 Fix Punkt 2 — Aufruf-Wrapper für ``sweep.main()``: liest das gerade geschriebene
    #742-Report-JSON von der Platte und erzeugt daraus die Zusammenfassung. Fail-open (``None``
    bei jedem Lese-/Schreibfehler) — non-fatal, analog dem Report-Block selbst."""
    import json
    try:
        report = json.loads(Path(report_path).read_text("utf-8"))
        return write_german_summary(report, logs_dir=logs_dir)
    except Exception:
        _log.warning(
            "[#832] Deutsche Zusammenfassung konnte nicht erzeugt werden (non-fatal).", exc_info=True)
        return None
