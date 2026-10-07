"""Issue #993 (P0, HEADLINE) — Die Deployment-Grenze.

Vor diesem Modul entschied ``daily_orchestrator.phase5_live_deployment`` ueber die Aufnahme eines
(Strategie, Symbol)-Paars in ``data/state/whitelist_tournament.json`` — und damit ueber den
tatsaechlichen Kapitaleinsatz — auf Basis EINER Bedingung:

    winner.get("oos_eligible") is True and winner.get("oos_evaluated") is True

``oos_eligible`` ist das Ergebnis eines Einzelfenster-Gates OHNE Multiplizitaetskorrektur (Phase-4-
Turnier). Die gesamte Bestaetigungskette des Optimizer-Sweeps (Holdout-Gate, Deflated Sharpe Ratio,
PBO/CSCV, Holdout-Bootstrap-CI, Boundary-Veto, R_symbol > R_global, Datenstand-Kohaerenz) wurde an
dieser Stelle NICHT konsultiert — das schwaechere der zwei parallelen Selektionssysteme im Repo
entschied ueber den Kapitaleinsatz.

Dieses Modul liefert die vollstaendige Ersatzpruefung: ``evaluate_deployment_eligibility`` prueft
ALLE acht notwendigen Bedingungen und gibt ein eingefrorenes ``DeploymentDecision`` zurueck — ohne
Fruehausstieg, sodass ``clause_results`` immer das vollstaendige Bild traegt, nicht nur die erste
verletzte Klausel.

Fail-closed-Regel (aus Issue #917 auf die Deployment-Ebene uebertragen): fehlt eine der Groessen
(``None``), gilt die zugehoerige Klausel als NICHT erfuellt. ``None`` ist keine bestandene Pruefung.

Rein & deterministisch (kein I/O ausser dem optionalen Live-Snapshot-Hash in
``evaluate_deployment_eligibility``; ``load_promotion_record``/``iter_promotion_records`` sind die
einzigen IO-tragenden Funktionen und liegen bewusst getrennt von der reinen Evaluationslogik).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from automation import bar_axis
from automation.live_params import (
    load_live_param_sources, mismatching_live_params, resolve_live_params,
)
from automation.optimizer.manifest import WORK, catalog_fingerprint

# Issue #993 — die acht notwendigen (NICHT hinreichenden) Bedingungen fuer Kapitaleinsatz, in
# Anzeige-/Auswertungsreihenfolge. Alle acht sind eine Konjunktion — keine Klausel ersetzt eine
# andere, und die Reihenfolge ist keine Prioritaet, nur die deterministische Regel, welche Klausel
# ``blocking_clause`` traegt, wenn mehrere gleichzeitig scheitern (die ERSTE in dieser Reihenfolge).
DEPLOYMENT_CLAUSES: tuple[str, ...] = (
    "promotion_record_exists",
    "status_ready_for_pr",
    "dsr",
    "psr",
    "pbo",
    "bootstrap_ci",
    "r_edge",
    "snapshot_drift",
    # Issue #1007 (Katalog #858, Fix Punkt 2) — neunte Klausel: eine Study, die eine
    # ``severity='blocking'``-Invariante verletzt (z. B. check_selection_statistic_availability,
    # check_guard_reference_stability), darf nicht kapitalwirksam werden, auch wenn sie
    # ``READY_FOR_PR`` und alle anderen acht Klauseln besteht — sonst ist die Studypopulation zwar
    # als informationsfrei markiert, wird aber trotzdem deployt.
    "study_invariants_clean",
    # Issue #1042 (Katalog #866, E-1) — zehnte Klausel: siehe _clause_cost_stress-Docstring. Bewusst
    # ans Ende gestellt (Anzeige-/Auswertungsreihenfolge, keine Prioritaet) — ein Kandidat, der
    # bereits an einer der neun bestehenden Klauseln scheitert, soll weiterhin DEREN Namen als
    # blocking_clause tragen, nicht die neueste Ergaenzung.
    "cost_stress",
    # Issue #1073 (Katalog #866-2) — elfte Klausel: siehe _clause_expectancy_outlier_robust-
    # Docstring. Ebenfalls ans Ende gestellt (Anzeige-/Auswertungsreihenfolge, keine Prioritaet).
    "expectancy_outlier_robust",
    # Issue #1360 (GH #1256, P0) — zwölfte Klausel: siehe _clause_live_params_match_promotion-
    # Docstring. Ebenfalls ans Ende gestellt (Anzeige-/Auswertungsreihenfolge, keine Prioritaet).
    "live_params_match_promotion",
    # Issue #1357 (GH #1253, P0) — dreizehnte Klausel: siehe _clause_holdout_disjoint-Docstring.
    "holdout_disjoint",
    # Issue #1381 (GH #1283, Pitfall #502) — vierzehnte Klausel: siehe _clause_config_profile_production-
    # Docstring.
    "config_profile_production",
    # Issue #1382 (GH #1284) — fünfzehnte Klausel: siehe _clause_bar_axis_live_supported-Docstring.
    "bar_axis_live_supported",
)

# Issue #993 Akzeptanzkriterium — dieselbe #663-Default-Schwelle wie confirm._study_pbo
# (``_PBO_DEFAULT_MIN_CONFIGS``), hier dupliziert statt importiert: confirm.py importiert seinerseits
# NICHT aus diesem Modul (Zirkelimport-Vermeidung, dieselbe Konvention wie champions._sanitize).
_PBO_DEFAULT_MIN_CONFIGS = 10

_READY_STATUSES = frozenset({"READY_FOR_PR"})


@dataclass(frozen=True)
class DeploymentDecision:
    """Issue #993 — atomares Urteil ueber EIN (Strategie, Symbol)-Paar.

    Kein Fruehausstieg: ALLE acht Klauseln aus ``DEPLOYMENT_CLAUSES`` werden ausgewertet, auch wenn
    eine fruehere bereits fehlschlaegt (analog der Vollstaendigkeits-Anforderung an ``DeflationResult``-
    artige Objekte im Optimizer-Pfad) — ``blocking_clause`` traegt nur die ERSTE fehlgeschlagene
    Klausel (nach ``DEPLOYMENT_CLAUSES``-Reihenfolge), ``clause_results`` das VOLLSTAENDIGE Bild
    (alle acht Keys, IMMER gefuellt, Wert ``True``/``False``/``None``)."""
    admitted: bool
    blocking_clause: str | None
    clause_results: dict[str, bool | None]
    promotion_run_id: str | None
    data_snapshot_sha256: str | None
    # Issue #1360 — Klausel-Details (z. B. die abweichenden Keys von ``live_params_match_promotion``),
    # NICHT Teil der Zulassungslogik. Default leer (rueckwaertskompatibel fuer jede direkte
    # ``DeploymentDecision(...)``-Konstruktion).
    clause_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "blocking_clause": self.blocking_clause,
            "clause_results": dict(self.clause_results),
            "promotion_run_id": self.promotion_run_id,
            "data_snapshot_sha256": self.data_snapshot_sha256,
            "clause_details": dict(self.clause_details),
        }


def _pair_key(pair) -> tuple[str, str]:
    """Normalisiert ``pair`` auf ein ``(strategy, symbol)``-Tupel. Akzeptiert ein Tupel/eine Liste
    der Laenge 2 oder einen ``"Strategy/SYMBOL"``-String (dieselbe Trennnotation wie die
    Study-Label-Konvention in ``report.py``, z. B. ``f"{strategy}/{symbol}"``)."""
    if isinstance(pair, str):
        strategy, _, symbol = pair.partition("/")
        return strategy, symbol
    strategy, symbol = pair
    return str(strategy), str(symbol)


def _clause_promotion_record_exists(record: Mapping[str, Any] | None) -> bool:
    return bool(record)


def _clause_status_ready_for_pr(record: Mapping[str, Any] | None) -> bool | None:
    if not record:
        return None
    status = record.get("status")
    if status is None:
        return None
    return status in _READY_STATUSES


def _clause_dsr(record: Mapping[str, Any] | None, *, deflation_confidence: float) -> bool | None:
    if not record:
        return None
    dsr = record.get("deflated_dsr")
    if dsr is None:
        return None
    return float(dsr) >= float(deflation_confidence)


def _clause_psr(record: Mapping[str, Any] | None, *, oos_min_psr: float) -> bool | None:
    if not record:
        return None
    psr = record.get("oos_psr")
    if psr is None:
        return None
    return float(psr) >= float(oos_min_psr)


def _clause_pbo(record: Mapping[str, Any] | None, *, pbo_min_configs: int) -> bool | None:
    """``PBO ≤ 0,5`` ODER (``PBO`` nicht schaetzbar UND die Config-Kohorte war ohnehin zu klein fuer
    ein PBO-Urteil — ``pbo is None`` ist in diesem Fall KEIN fehlender Wert, sondern das explizite
    Ergebnis von ``confirm._study_pbo`` bei zu wenigen Configs/Gruppen, siehe dessen Docstring).
    Ein fehlendes ``pbo_n_configs`` bei gleichzeitig fehlendem ``pbo`` ist dagegen nicht
    unterscheidbar von einem echten Datenausfall ⇒ fail-closed ``None``."""
    if not record:
        return None
    pbo = record.get("pbo")
    if pbo is not None:
        return float(pbo) <= 0.5
    n_configs = record.get("pbo_n_configs")
    if n_configs is None:
        return None
    return int(n_configs) < int(pbo_min_configs)


def _clause_bootstrap_ci(record: Mapping[str, Any] | None) -> bool | None:
    if not record:
        return None
    ci_lo = record.get("holdout_ci_lower_sortino")
    if ci_lo is None:
        return None
    return float(ci_lo) > 0.0


def _clause_r_edge(record: Mapping[str, Any] | None) -> bool | None:
    """Repliziert ``confirm.confirm_per_symbol_promotion``'s R-Edge-Logik bit-fuer-bit (Issue #655):
    ``R_symbol is None`` ⇒ nie ein Edge belegbar ⇒ ``False``; ``R_global is None`` ⇒ keine Baseline
    zu schlagen ⇒ trivial erfuellt; sonst ``R_symbol > R_global + promotion_margin``."""
    if not record:
        return None
    r_symbol = record.get("R_symbol")
    if r_symbol is None:
        return False
    r_global = record.get("R_global")
    if r_global is None:
        return True
    margin = record.get("promotion_margin") or 0.0
    return float(r_symbol) > float(r_global) + float(margin)


def _clause_snapshot_drift(record: Mapping[str, Any] | None, *, current_snapshot_sha256: str | None) -> bool | None:
    if not record:
        return None
    promoted_snapshot = record.get("data_snapshot_sha256")
    if promoted_snapshot is None or current_snapshot_sha256 is None:
        return None
    return promoted_snapshot == current_snapshot_sha256


def _clause_study_invariants_clean(record: Mapping[str, Any] | None) -> bool | None:
    """Issue #1007 (Katalog #858) — ``blocking_invariant_names`` (confirm.py, seit diesem Fix
    additiv in ``metrics_symbol``/dem Proposal-Export gestempelt, WENN ``confirm_per_symbol_
    promotion`` tatsaechlich ``study_invariant_results`` erhielt) ist eine leere Liste ⇒ sauber
    (``True``); eine nicht-leere Liste ⇒ mindestens eine blockierende Invariante ⇒ ``False``.
    Das Feld ABWESEND (Aufrufer hat nie ueberprueft, z. B. ein Promotion-Record aus der Zeit vor
    diesem Fix oder ein Sweep-Dispatch ohne Live-Verdrahtung der Invarianten-Vorberechnung) ⇒
    ``None`` — fail-closed wie jede andere Klausel: "nicht ueberprueft" ist KEINE bestandene
    Pruefung (dieselbe Regel wie ``dsr``/``psr``/``pbo``/``bootstrap_ci`` oben)."""
    if not record:
        return None
    names = record.get("blocking_invariant_names")
    if names is None:
        return None
    return len(names) == 0


def _clause_cost_stress(record: Mapping[str, Any] | None) -> bool | None:
    """Issue #1042 (Katalog #866) E-1 — zehnte Klausel: bei einer Median-Holdout-Expectancy von
    3,63 bps gegen 1 bps konfigurierter Kommission liegt das System am Break-even; ein Kandidat, der
    unter DOPPELTEN Round-Trip-Kosten (``expectancy_round_trip_cost_stress_2x``, additive Telemetrie
    aus ``backtest_runner._expectancy_cost_stress``) keine positive kapitalgewichtete Expectancy mehr
    trägt, hat keinen gegen realistische Kosten-Drift (Spread-Ausweitung, Slippage) robusten Edge.
    ``None`` (Feld fehlt — ein Promotion-Record aus der Zeit vor diesem Fix, oder ein Backtest-Pfad
    ohne ``notional_list``) ⇒ fail-closed, dieselbe Regel wie jede andere Klausel: "nicht geprüft"
    ist KEINE bestandene Prüfung.

    Issue #1081 (Katalog #866-2) — liest bevorzugt den umbenannten Schlüssel
    (``expectancy_round_trip_cost_stress_2x``, stresst seit diesem Fix die VOLLEN Round-Trip-Kosten
    statt nur der Kommission, siehe ``backtest_runner._expectancy_cost_stress``-Docstring); der alte
    Name (``expectancy_cost_stress_2x``) bleibt als Fallback für einen Übergangszeitraum — er trägt
    seit #1081 denselben korrigierten Wert (Alias, kein zweiter Berechnungspfad)."""
    if not record:
        return None
    value = record.get("expectancy_round_trip_cost_stress_2x")
    if value is None:
        value = record.get("expectancy_cost_stress_2x")
    if value is None:
        return None
    return float(value) > 0.0


def _clause_expectancy_outlier_robust(record: Mapping[str, Any] | None) -> bool | None:
    """Issue #1073 (Katalog #866-2, Kohorte D) — elfte Klausel: eine positive Holdout-Expectancy,
    die unter Winsorisierung (``holdout_expectancy_winsorized`` — derselbe 5-%-Median-Notional-
    Ausreisserboden wie ``expectancy_capital_weighted``, #1031) das Vorzeichen wechselt oder
    NICHT-positiv wird, verdankt ihr gesamtes positives Ergebnis einer kleinen Zahl extremer Trades
    — kein robuster Edge. Beweis B-8 im #866-Katalog: der ERSTGELISTETE Kandidat eines Laufs
    (AdxAtrMomentum, +17,23 bps roh) hatte ``holdout_expectancy_winsorized = −1,44`` bps, getragen
    von 6 von 132 Trades.

    Bedingung: ``sign(holdout_expectancy_winsorized) == sign(holdout_expectancy_notional_weighted)``
    UND ``holdout_expectancy_winsorized > 0``. ``None`` (eine der beiden Grössen fehlt — Pre-#1031-
    Record oder kein Trade) ⇒ fail-closed, dieselbe Regel wie jede andere Klausel: "nicht geprüft"
    ist KEINE bestandene Prüfung.

    Issue #945/#1111 (Katalog #960) — Feldname ``holdout_expectancy_notional_weighted`` (vormals
    ``holdout_expectancy``); unveraendertes Verhalten, nur der umbenannte Schluessel, siehe
    ``report.py``s ``_study_record``-Docstring fuer die Root-Cause der Umbenennung."""
    if not record:
        return None
    raw = record.get("holdout_expectancy_notional_weighted")
    winsorized = record.get("holdout_expectancy_winsorized")
    if raw is None or winsorized is None:
        return None
    raw, winsorized = float(raw), float(winsorized)
    if winsorized <= 0.0:
        return False
    return (raw > 0.0) == (winsorized > 0.0)


def _clause_live_params_match_promotion(
    record: Mapping[str, Any] | None, *, strategy: str, symbol: str,
    live_param_sources: tuple[Mapping[str, Any], list] | None,
) -> tuple[bool | None, dict[str, Any]]:
    """Issue #1360 (GH #1256, P0) — zwoelfte Klausel: fuer JEDEN Key in ``proposed_instrument_override``
    (die validierten Parameter, ``confirm.py``) gilt ``resolve_live_params(...)[k] == proposal[k]``
    (int/bool/str exakt, float ``math.isclose(rel_tol=1e-9)``, siehe ``live_params``). Die
    abweichenden Keys stehen im Klausel-Detail.

    Fail-closed: fehlt das Proposal-Feld (``proposed_instrument_override`` nicht vorhanden/kein Dict)
    oder lassen sich die Live-Quellen (``strategy_defaults.json``/``strategies.json``) nicht laden,
    ist die Klausel ``None`` — "nicht geprueft" ist KEINE bestandene Pruefung. Ein LEERES Override
    (``{}``) verlangt nichts und besteht trivial."""
    if not record:
        return None, {}
    proposed = record.get("proposed_instrument_override")
    if not isinstance(proposed, Mapping):
        return None, {"reason": "proposed_instrument_override_missing"}
    try:
        if live_param_sources is None:
            from automation.optimizer.trial_config import config_dir
            live_param_sources = load_live_param_sources(config_dir())
        defaults, strategies_raw = live_param_sources
    except (OSError, ValueError):
        return None, {"reason": "live_param_sources_unavailable"}
    live = resolve_live_params(strategy, symbol, defaults, strategies_raw)
    mismatching = mismatching_live_params(live, proposed) or []
    detail = {"mismatching_keys": mismatching}
    if mismatching:
        detail["live"] = {k: live.get(k) for k in mismatching}
        detail["proposed"] = {k: proposed[k] for k in mismatching}
    return (not mismatching), detail


def _clause_holdout_disjoint(record: Mapping[str, Any] | None) -> bool | None:
    """Issue #1357 (GH #1253, P0) — dreizehnte Klausel: der Confirm-Holdout enthielt keine Selektionsdaten
    (``holdout_overlap_days == 0``) UND zwischen Selektionsende und Holdout-Beginn lag mindestens
    ``holdout_embargo_days``. Fail-closed: fehlt eines der Felder (Proposal vor #1357, Stempel nicht
    möglich), ist die Klausel ``None`` — "nicht geprüft" ist KEINE bestandene Prüfung."""
    if not record:
        return None
    overlap = record.get("holdout_overlap_days")
    sel, hold, emb = (record.get("selection_end_utc"), record.get("holdout_start_utc"),
                      record.get("holdout_embargo_days"))
    if overlap is None or sel is None or hold is None or emb is None:
        return None
    import datetime as _dt
    try:
        gap_days = (_dt.datetime.fromisoformat(str(hold).replace("Z", "+00:00"))
                    - _dt.datetime.fromisoformat(str(sel).replace("Z", "+00:00"))).total_seconds() / 86_400.0
    except ValueError:
        return None
    return int(overlap) == 0 and gap_days >= float(emb)


def _clause_config_profile_production(record: Mapping[str, Any] | None) -> bool | None:
    """Issue #1381 (GH #1283, Pitfall #502) — vierzehnte Klausel: der Promotion-Record stammt aus einem Lauf mit
    ``config_profile == "production"``. Ein Smoke-/Abweichungsprofil (verkürzte Geometrie, ``gate1_buffer_days=0``)
    ist nie Evidenz und darf nicht kapitalwirksam werden. Fail-closed: fehlt das Feld (Record vor #1381), ist die
    Klausel ``None`` — "nicht geprüft" ist KEINE bestandene Prüfung."""
    if not record:
        return None
    profile = record.get("config_profile")
    if profile is None:
        return None
    return profile == "production"


def _clause_bar_axis_live_supported(record: Mapping[str, Any] | None) -> bool | None:
    """Issue #1382 (GH #1284) Fix Punkt 7 (Pitfall #487: validiert = gehandelt) — fünfzehnte Klausel: der
    Promotion-Record stammt von der Live-Achse (``bar_axis == "OneHour"``). Ein Tagesachsen-Record ist nicht
    live handelbar, bis es ein eigenes Live-Issue gibt (Live-Bot und Phase 5 laufen auf Stundenbars).
    Records vor #1382 tragen kein ``bar_axis`` und stammen per Konstruktion von der Stundenachse (die Achse war
    nicht konfigurierbar) ⇒ fehlendes Feld zählt als ``OneHour``; ein vorhandener Wert ≠ ``OneHour`` blockiert."""
    if not record:
        return None
    axis = record.get("bar_axis")
    return True if axis is None else axis == bar_axis.LIVE_AXIS


def evaluate_deployment_eligibility(
    pair,
    promotion_records: Mapping[Any, Mapping[str, Any]],
    tournament_cfg: Mapping[str, Any],
    *,
    current_snapshot_sha256: str | None = None,
    live_param_sources: tuple[Mapping[str, Any], list] | None = None,
) -> DeploymentDecision:
    """Issue #993 Fix Punkt 1 — die EINZIGE zulaessige Quelle einer Deployment-Entscheidung.

    ``pair`` — ``(strategy, symbol)``-Tupel oder ``"strategy/symbol"``-String.
    ``promotion_records`` — Mapping von ``pair`` (in beiden obigen Formen nachschlagbar) auf den
    zugehoerigen, bereits geladenen Promotion-Record (siehe ``load_promotion_record``). Ein Paar
    OHNE Eintrag ist nicht deploy-fähig — auch dann nicht, wenn es Phase-4-``per_symbol_winner`` ist.
    ``tournament_cfg`` — dieselbe geladene ``tournament.json``-Config wie der Optimizer-Sweep
    (``deflation_confidence``, ``oos_min_psr``, ``pbo_min_configs``).
    ``current_snapshot_sha256`` — der Datenstand ZUM Deployment-Zeitpunkt; ``None`` (Default) lässt
    diese Funktion ihn live via ``catalog_fingerprint()`` ermitteln (Produktionspfad); Tests
    injizieren einen festen Wert, um die Snapshot-Drift-Klausel deterministisch zu pruefen.
    ``live_param_sources`` — ``(strategy_defaults, strategies_raw)`` fuer die Klausel
    ``live_params_match_promotion`` (Issue #1360); ``None`` laedt sie aus ``config_dir()`` — dieselben
    Dateien, aus denen der Bot seine Live-Parameter baut.
    """
    strategy, symbol = _pair_key(pair)
    record = (
        promotion_records.get((strategy, symbol))
        if (strategy, symbol) in promotion_records
        else promotion_records.get(f"{strategy}/{symbol}")
    )

    deflation_confidence = float(tournament_cfg.get("deflation_confidence", 0.95))
    oos_min_psr = float(tournament_cfg.get("oos_min_psr", 0.75))
    pbo_min_configs = int(tournament_cfg.get("pbo_min_configs", _PBO_DEFAULT_MIN_CONFIGS))
    if current_snapshot_sha256 is None:
        current_snapshot_sha256 = catalog_fingerprint()

    # Issue #993 — KEIN Fruehausstieg: jede Klausel wird unabhaengig von den anderen ausgewertet,
    # damit ``clause_results`` immer das vollstaendige Bild traegt (Diagnosewert fuer
    # ``DEPLOYMENT_WHITELIST_GENERATED``'s ``rejected_by_clause``-Telemetrie).
    clause_results: dict[str, bool | None] = {
        "promotion_record_exists": _clause_promotion_record_exists(record),
        "status_ready_for_pr": _clause_status_ready_for_pr(record),
        "dsr": _clause_dsr(record, deflation_confidence=deflation_confidence),
        "psr": _clause_psr(record, oos_min_psr=oos_min_psr),
        "pbo": _clause_pbo(record, pbo_min_configs=pbo_min_configs),
        "bootstrap_ci": _clause_bootstrap_ci(record),
        "r_edge": _clause_r_edge(record),
        "snapshot_drift": _clause_snapshot_drift(record, current_snapshot_sha256=current_snapshot_sha256),
        "study_invariants_clean": _clause_study_invariants_clean(record),
        "cost_stress": _clause_cost_stress(record),
        "expectancy_outlier_robust": _clause_expectancy_outlier_robust(record),
        "holdout_disjoint": _clause_holdout_disjoint(record),
        "config_profile_production": _clause_config_profile_production(record),
        "bar_axis_live_supported": _clause_bar_axis_live_supported(record),
    }
    clause_results["live_params_match_promotion"], live_params_detail = (
        _clause_live_params_match_promotion(
            record, strategy=strategy, symbol=symbol, live_param_sources=live_param_sources))

    # Fail-closed: ``None`` (nicht auswertbar) zaehlt NICHT als erfuellt.
    admitted = all(clause_results[c] is True for c in DEPLOYMENT_CLAUSES)
    blocking_clause = next(
        (c for c in DEPLOYMENT_CLAUSES if clause_results[c] is not True), None
    ) if not admitted else None

    return DeploymentDecision(
        admitted=admitted,
        blocking_clause=blocking_clause,
        clause_results=clause_results,
        promotion_run_id=(record or {}).get("run_id"),
        data_snapshot_sha256=(record or {}).get("data_snapshot_sha256"),
        clause_details={"live_params_match_promotion": live_params_detail}
        if live_params_detail else {},
    )


def build_promotion_record_from_proposal(proposal: Mapping[str, Any], *, run_id: str | None = None) -> dict[str, Any]:
    """Flacht ein ``confirm.export_symbol_proposal``-Payload (``data/optimizer/proposal_{strategy}_
    {symbol}.json``, verschachtelt unter ``holdout.symbol.*``) auf die flache Record-Form ab, die
    ``evaluate_deployment_eligibility`` erwartet. Reine Umformung, keine neue Berechnung — jedes
    gelesene Feld stammt bit-identisch aus ``confirm.py``'s bereits persistierten Groessen
    (``deflated_dsr``/``oos_psr``/``holdout_ci_lower_sortino``/``pbo``/``pbo_n_configs`` seit Issue
    #993 additiv in ``metrics_symbol`` gestempelt)."""
    holdout_symbol = ((proposal.get("holdout") or {}).get("symbol")) or {}
    return {
        "status": proposal.get("status"),
        "R_symbol": proposal.get("R_symbol"),
        "R_global": proposal.get("R_global"),
        "promotion_margin": proposal.get("promotion_margin"),
        "data_snapshot_sha256": proposal.get("data_snapshot_sha256"),
        "deflated_dsr": holdout_symbol.get("deflated_dsr"),
        "oos_psr": holdout_symbol.get("oos_psr"),
        "holdout_ci_lower_sortino": holdout_symbol.get("holdout_ci_lower_sortino"),
        "pbo": holdout_symbol.get("pbo"),
        "pbo_n_configs": holdout_symbol.get("pbo_n_configs"),
        "blocking_invariant_names": holdout_symbol.get("blocking_invariant_names"),
        # Issue #1362 (GH #1258) — Holdout-Round-Trip-Statistik (bps auf das Notional), Referenz des
        # Live-Verteilungs-Auslösers B (live_risk); fehlt sie, trägt das Bot-Start-Event den Grund.
        "holdout_trade_return_bps_mean": holdout_symbol.get("oos_trade_return_bps_mean"),
        "holdout_trade_return_bps_std": holdout_symbol.get("oos_trade_return_bps_std"),
        "holdout_trade_return_bps_n": holdout_symbol.get("oos_trade_return_bps_n"),
        # Issue #1360 (GH #1256) — die VALIDIERTEN Parameter (confirm.py), Eingang der Klausel
        # ``live_params_match_promotion``; ``None`` (Proposal ohne das Feld) ⇒ Klausel fail-closed.
        "proposed_instrument_override": proposal.get("proposed_instrument_override"),
        # Issue #1357 (GH #1253) — Eingang der Klausel ``holdout_disjoint`` (fehlend ⇒ fail-closed).
        "selection_end_utc": proposal.get("selection_end_utc"),
        "holdout_start_utc": proposal.get("holdout_start_utc"),
        "holdout_embargo_days": proposal.get("holdout_embargo_days"),
        "holdout_overlap_days": proposal.get("holdout_overlap_days"),
        # Issue #1381 (GH #1283) — Eingang der Klausel ``config_profile_production`` (fehlend ⇒ fail-closed).
        "config_profile": proposal.get("config_profile"),
        # Issue #1382 (GH #1284) — Eingang der Klausel ``bar_axis_live_supported``.
        "bar_axis": proposal.get("bar_axis"),
        # Issue #1379 (GH #1281) — Nachweisbarkeit zur Transparenz (KEINE Klausel).
        "holdout_mds_annual": proposal.get("holdout_mds_annual"),
        "detectability_class": proposal.get("detectability_class"),
        # Issue #1042 (Katalog #866, E-1) — siehe _clause_cost_stress-Docstring.
        "expectancy_cost_stress_2x": holdout_symbol.get("oos_expectancy_cost_stress_2x"),
        # Issue #1073 (Katalog #866-2) — siehe _clause_expectancy_outlier_robust-Docstring. Issue
        # #945/#1111 — umbenannt von "holdout_expectancy".
        "holdout_expectancy_notional_weighted": holdout_symbol.get("oos_expectancy"),
        "holdout_expectancy_winsorized": holdout_symbol.get("oos_expectancy_winsorized"),
        "run_id": run_id,
    }


def load_promotion_record(strategy: str, symbol: str, *, work_dir: Path | None = None) -> dict[str, Any] | None:
    """Liest ``{work_dir}/proposal_{strategy}_{symbol}.json`` (Default ``work_dir``:
    ``manifest.WORK`` == ``data/optimizer``, falls vorhanden) und liefert den flachen Promotion-
    Record. ``None``, wenn keine Proposal-Datei existiert — ein Paar ohne Promotionsrecord ist per
    Konstruktion nicht deploy-faehig (Issue #993 Fix Punkt 3). ``work_dir`` ist explizit
    parametrisierbar (statt hart auf das Modul-``WORK`` verdrahtet), damit ein Aufrufer mit eigenem
    ``PROJECT_ROOT`` (z. B. ``daily_orchestrator.py``, dessen ``PROJECT_ROOT`` in Tests via
    monkeypatch isoliert wird) dieselbe Isolation an den Datei-Read weiterreichen kann."""
    base_dir = work_dir if work_dir is not None else WORK
    # Fail-closed statt eines Absturzes: ein ``work_dir``, das (z. B. ueber ein in Tests
    # gemocktes ``PROJECT_ROOT``) kein echter Pfad ist, wird NIE an ``open()`` gereicht — ein
    # Test-Double, das sich als Pfad ausgibt, kann in Kombination mit ``open()``/``json.load``
    # kaskadierende, teils un-fangbare OSErrors ueber mehrere Cleanup-Schritte hinweg auslösen
    # (jeder Zugriff auf das mit einem falschen Datei-Deskriptor "geoeffnete" Objekt wirft erneut).
    # Ein fehlendes Promotionsrecord ist ohnehin bereits das korrekte, sichere Ergebnis.
    if not isinstance(base_dir, Path):
        return None
    path = base_dir / f"proposal_{strategy}_{symbol}.json"
    try:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            proposal = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(proposal, dict):
        return None
    return build_promotion_record_from_proposal(proposal)


def load_promotion_records(pairs, *, work_dir: Path | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """Laedt die Promotion-Records fuer eine Menge von ``(strategy, symbol)``-Paaren (typischerweise
    die Phase-4-``per_symbol_winners``). Paare ohne Proposal-Datei sind im Ergebnis-Mapping schlicht
    ABWESEND — ``evaluate_deployment_eligibility`` behandelt ein fehlendes Mapping-Element bereits
    korrekt als ``promotion_record_exists=False``."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for pair in pairs:
        strategy, symbol = _pair_key(pair)
        record = load_promotion_record(strategy, symbol, work_dir=work_dir)
        if record is not None:
            out[(strategy, symbol)] = record
    return out
