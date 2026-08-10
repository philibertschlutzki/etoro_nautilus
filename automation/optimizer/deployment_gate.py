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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "blocking_clause": self.blocking_clause,
            "clause_results": dict(self.clause_results),
            "promotion_run_id": self.promotion_run_id,
            "data_snapshot_sha256": self.data_snapshot_sha256,
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


def evaluate_deployment_eligibility(
    pair,
    promotion_records: Mapping[Any, Mapping[str, Any]],
    tournament_cfg: Mapping[str, Any],
    *,
    current_snapshot_sha256: str | None = None,
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
    }

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
