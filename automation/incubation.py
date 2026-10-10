"""automation/incubation.py
==========================
Issue #1368 (GH #1265, P1, Ertragshebel) — Forward-Evidenz statt Jahreswartezeit: Demo-Inkubation mit
eingefrorenen Parametern und sequenziell korrigiertem Promotionstest (``optimizer/sequential.py``).

Zustandsmaschine je Paar (``data/state/deployment_stages.json``)::

    CANDIDATE → INCUBATING (Demo) → LIVE_SMALL → LIVE_FULL      Rückfall jederzeit → RETIRED

* **Inkubations-Selektion** (``select_incubation_candidates``): dieselben Eligibility-Gates wie heute
  (``oos_eligible``), ohne Holdout-/DSR-Promotion; höchstens ``incubation.max_concurrent`` Paare, Rangfolge nach
  deflationierter OOS-PSR. Ergebnis ``incubation_<strategy>_<symbol>.json`` mit EINGEFRORENEM ``params_sha256``
  (``live_params.resolve_live_params`` — dieselbe Auflösung wie Gate und Bot, #1360).
* **Demo-Betrieb**: eigene Bot-Instanz mit ``environment = "demo"`` (``assert_stage_environment``: INCUBATING
  startet NIE im ``real``-Environment). Evidenz-Ledger ``data/state/incubation/<strategy>_<symbol>.jsonl`` mit
  Netto-Renditen je Session-Bar (#1361), gebunden an ``params_sha256`` — jede Parameteränderung beginnt ein
  neues Ledger, alte Evidenz zählt nicht (``EvidenceLedger``).
* **Sequenzieller Test** an geplanten, wöchentlichen Prüfzeitpunkten (höchstens ``K_max``): ``PROMOTE`` ⇒
  ``LIVE_SMALL`` (nur über die Deployment-Grenze, ``admitted is True``), ``RETIRE``/``EXHAUSTED`` ⇒ ``RETIRED``.
  ``LIVE_SMALL`` handelt mit ``capital_fraction_small`` der regulären Allokation, bis ``t_full_bars`` zusätzliche
  Echtgeld-Bars dieselbe Schranke erneut erfüllen ⇒ ``LIVE_FULL``. Der Verteilungs-Auslöser (#1362) zieht jede
  Stufe zurück.
* **Kapital** (``daily_orchestrator.phase5_live_deployment``): mit ``incubation.enabled`` ist die Deployment-Grenze
  notwendig, aber nicht hinreichend — nur ``LIVE_SMALL``/``LIVE_FULL`` mit unverändertem Fingerabdruck werden
  gewhitelistet. Die Forward-Evidenz ersetzt KEINE Klausel der Grenze (die Schwelle sinkt nicht).

Rein bis auf das explizite JSON-I/O der Zustands-/Ledger-Dateien; kein Netz, kein ``nautilus_trader``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from automation.optimizer.manifest import write_json_atomic
from automation.optimizer.sequential import (
    CONFIDENCE_DEFAULT, K_MAX_DEFAULT, N_CONCURRENT_DEFAULT, bonferroni_threshold, sequential_decision,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "data" / "state"
STAGES_PATH = STATE_DIR / "deployment_stages.json"
INCUBATION_DIR = STATE_DIR / "incubation"
# Echtgeld-Ledger der LIVE_SMALL-Stufe (dieselbe Schranke, eigene Evidenz: Demo-Bars zählen nicht doppelt).
LIVE_LEDGER_DIR = INCUBATION_DIR / "live"
# Der Demo-Bot ist eine EIGENE Instanz: eigene Sperre (läuft neben dem Echtgeld-Bot), eigenes
# Risikogedächtnis (der Hochwasserstand ist an das Environment gebunden, #1362).
INCUBATION_WHITELIST_PATH = STATE_DIR / "incubation_whitelist.json"
INCUBATION_LOCK_PATH = STATE_DIR / "incubation_bot.lock"
INCUBATION_HWM_PATH = STATE_DIR / "incubation_equity_state.json"
INCUBATION_TOURNAMENT_PATH = STATE_DIR / "incubation_tournament.json"
# Verteilungs-Auslöser (#1362) je Symbol — der Bot schreibt, der tägliche Zyklus zieht zurück.
DISTRIBUTION_TRIPS_PATH = INCUBATION_DIR / "distribution_trips.json"

CANDIDATE, INCUBATING, LIVE_SMALL, LIVE_FULL, RETIRED = (
    "CANDIDATE", "INCUBATING", "LIVE_SMALL", "LIVE_FULL", "RETIRED")
STAGES = (CANDIDATE, INCUBATING, LIVE_SMALL, LIVE_FULL, RETIRED)
_ALLOWED: dict[str, set[str]] = {
    CANDIDATE: {INCUBATING, RETIRED},
    INCUBATING: {LIVE_SMALL, RETIRED},
    LIVE_SMALL: {LIVE_FULL, RETIRED},
    LIVE_FULL: {RETIRED},
    RETIRED: {CANDIDATE},
}
LIVE_STAGES = (LIVE_SMALL, LIVE_FULL)

INCUBATION_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "walk_forward": {"is_window_days": 40, "embargo_period_days": 3, "splits": 3, "oos_window_days": 12},
    "max_concurrent": N_CONCURRENT_DEFAULT,
    "k_max": K_MAX_DEFAULT,
    "look_interval_days": 7,
    "capital_fraction_small": 0.25,
    "t_full_bars": 420,
}


class StageTransitionError(ValueError):
    """Unzulässiger Stufenwechsel (Zustandsmaschine oder Deployment-Grenze)."""


class IncubationEnvironmentError(RuntimeError):
    """Eine Inkubation sollte in einem ``real``-Environment laufen — nie zulässig."""


def incubation_config(tournament_cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    """``tournament.json["incubation"]`` über den Defaults; ``confidence`` = ``deflation_confidence`` (die
    familienweite 0,95 aus #1246 — die Schwelle sinkt nicht)."""
    cfg = dict(INCUBATION_DEFAULTS)
    cfg.update(((tournament_cfg or {}).get("incubation") or {}))
    cfg["walk_forward"] = {**INCUBATION_DEFAULTS["walk_forward"], **(cfg.get("walk_forward") or {})}
    cfg["confidence"] = float((tournament_cfg or {}).get("deflation_confidence", CONFIDENCE_DEFAULT))
    return cfg


def bonferroni_threshold_for(cfg: Mapping[str, Any]) -> float:
    """Die je Prüfzeitpunkt und Kandidat geforderte PSR der aufgelösten Inkubations-Config."""
    return bonferroni_threshold(float(cfg["confidence"]), int(cfg["k_max"]), int(cfg["max_concurrent"]))


def pair_key(strategy: str, symbol: str) -> str:
    return f"{strategy}/{symbol}"


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Zustandsmaschine ────────────────────────────────────────────────────────────────

class DeploymentStages:
    """Persistente Stufe je Paar (``deployment_stages.json``: ``{pair: {stage, params_sha256, since_utc,
    history: [...]}}``)."""

    def __init__(self, path: Path = STAGES_PATH) -> None:
        self.path = Path(path)
        try:
            self.data: dict[str, dict] = json.loads(self.path.read_text("utf-8")) or {}
        except (OSError, ValueError):
            self.data = {}

    def stage(self, strategy: str, symbol: str) -> str | None:
        return (self.data.get(pair_key(strategy, symbol)) or {}).get("stage")

    def entry(self, strategy: str, symbol: str) -> dict:
        return dict(self.data.get(pair_key(strategy, symbol)) or {})

    def pairs_in(self, *stages: str) -> list[tuple[str, str]]:
        out = []
        for key, entry in sorted(self.data.items()):
            if entry.get("stage") in stages:
                strategy, _, symbol = key.partition("/")
                out.append((strategy, symbol))
        return out

    def transition(self, strategy: str, symbol: str, to_stage: str, *, reason: str,
                   params_sha256: str | None = None, deployment_decision: Mapping[str, Any] | None = None,
                   now: datetime | None = None, extra: Mapping[str, Any] | None = None) -> dict:
        """Wechselt die Stufe gemäss Zustandsmaschine. ``LIVE_*`` NUR mit einer zugelassenen Deployment-
        Entscheidung (``deployment_decision['admitted'] is True`` — alle Klauseln inklusive #1357/#1360)."""
        if to_stage not in STAGES:
            raise StageTransitionError(f"Unbekannte Stufe {to_stage!r}.")
        key = pair_key(strategy, symbol)
        current = (self.data.get(key) or {}).get("stage")
        allowed = _ALLOWED.get(current, {CANDIDATE}) if current else {CANDIDATE, INCUBATING}
        if to_stage not in allowed:
            raise StageTransitionError(f"{key}: {current} → {to_stage} ist unzulässig (erlaubt: {sorted(allowed)}).")
        if to_stage in LIVE_STAGES and (deployment_decision or {}).get("admitted") is not True:
            raise StageTransitionError(
                f"{key}: {to_stage} nur über die Deployment-Grenze (admitted is True), erhalten: "
                f"{(deployment_decision or {}).get('admitted')!r}.")
        entry = dict(self.data.get(key) or {})
        history = list(entry.get("history") or [])
        history.append({"from": current, "to": to_stage, "utc": _now_iso(now), "reason": reason})
        entry.update({"stage": to_stage, "since_utc": _now_iso(now), "history": history[-50:],
                      **({"params_sha256": params_sha256} if params_sha256 else {}), **dict(extra or {})})
        self.data[key] = entry
        write_json_atomic(self.path, self.data)
        return entry


def assert_stage_environment(stage: str, environment: str) -> None:
    """Issue #1368 — INCUBATING läuft ausschliesslich im Demo-Konto; jedes andere Environment ist ein Fehler."""
    if stage == INCUBATING and str(environment).lower() != "demo":
        raise IncubationEnvironmentError(
            f"Inkubation (INCUBATING) darf nie im Environment {environment!r} laufen — nur 'demo'.")


# ─── Evidenz-Ledger ──────────────────────────────────────────────────────────────────

class EvidenceLedger:
    """``<dir>/<strategy>_<symbol>.jsonl``: eine Zeile je Session-Bar ``{ts_event, net_return, params_sha256}``.
    Die ERSTE Zeile bindet das Ledger an ``params_sha256``; ein Eintrag mit anderem Fingerabdruck archiviert das
    Ledger (``.<alt-sha[:12]>.jsonl``) und beginnt ein neues — alte Evidenz zählt nicht."""

    def __init__(self, strategy: str, symbol: str, directory: Path = INCUBATION_DIR) -> None:
        self.path = Path(directory) / f"{strategy}_{symbol.replace('/', '_')}.jsonl"

    def _rows(self) -> list[dict]:
        try:
            return [json.loads(line) for line in self.path.read_text("utf-8").splitlines() if line.strip()]
        except (OSError, ValueError):
            return []

    def params_sha256(self) -> str | None:
        rows = self._rows()
        return rows[0].get("params_sha256") if rows else None

    def append(self, ts_event: int, net_return: float, params_sha256: str) -> None:
        current = self.params_sha256()
        if current is not None and current != params_sha256:
            archived = self.path.with_name(f"{self.path.stem}.{current[:12]}.jsonl")
            self.path.replace(archived)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts_event": int(ts_event), "net_return": float(net_return),
                                "params_sha256": params_sha256}) + "\n")

    def returns(self, params_sha256: str) -> list[float]:
        """Netto-Renditen NUR des Ledgers mit genau diesem Fingerabdruck (sonst leer)."""
        rows = self._rows()
        if not rows or rows[0].get("params_sha256") != params_sha256:
            return []
        return [float(r["net_return"]) for r in rows if r.get("params_sha256") == params_sha256]


def bar_net_return(prev_equity: float | None, equity: float, capital_base: float) -> float | None:
    """Netto-Rendite einer Session-Bar: Änderung der (realisierten + unrealisierten) Paar-Equity relativ zur
    zugeteilten Kapitalbasis. ``None`` ohne Vorwert oder Kapitalbasis."""
    if prev_equity is None or not capital_base:
        return None
    return (float(equity) - float(prev_equity)) / float(capital_base)


class SessionBarLedgerRecorder:
    """Beobachter je Strategie-Instanz (``HourlyStrategyBase._session_bar_observer``): an JEDER In-Session-Bar
    (#1361-Achse, Aufruf nach dem Session-Gate) die Paar-Equity lesen und die Netto-Rendite seit der vorigen
    Session-Bar ins Ledger schreiben. ``equity_fn(strategy) -> float | None`` (realisiert + unrealisiert,
    Kosten in den Fills), ``capital_base_fn(strategy) -> float | None`` (die zugeteilte Kapitalbasis, beim
    ersten Aufruf fixiert). Doppelte Aufrufe derselben Bar (``ts_event`` nicht neuer) zählen nicht."""

    def __init__(self, ledger: EvidenceLedger, params_sha256: str, *, equity_fn, capital_base_fn) -> None:
        self._ledger = ledger
        self._params_sha256 = params_sha256
        self._equity_fn = equity_fn
        self._capital_base_fn = capital_base_fn
        self._capital_base: float | None = None
        self._prev_equity: float | None = None
        self._last_ts: int | None = None

    def __call__(self, strategy, bar) -> None:
        ts = int(bar.ts_event)
        if self._last_ts is not None and ts <= self._last_ts:
            return
        equity = self._equity_fn(strategy)
        if equity is None:
            return
        if not self._capital_base:
            self._capital_base = self._capital_base_fn(strategy)
        net = bar_net_return(self._prev_equity, float(equity), self._capital_base)
        self._prev_equity, self._last_ts = float(equity), ts
        if net is not None:
            self._ledger.append(ts, net, self._params_sha256)


def record_distribution_trip(symbol: str, *, path: Path = DISTRIBUTION_TRIPS_PATH,
                             now: datetime | None = None, detail: Mapping[str, Any] | None = None) -> None:
    """Vom Bot (``on_trip`` des Watchdogs) bei ``trigger == "distribution"`` aufgerufen."""
    try:
        data = json.loads(Path(path).read_text("utf-8")) or {}
    except (OSError, ValueError):
        data = {}
    data[str(symbol)] = {"utc": _now_iso(now), **dict(detail or {})}
    write_json_atomic(Path(path), data)


def read_distribution_trips(path: Path = DISTRIBUTION_TRIPS_PATH) -> dict[str, dict]:
    try:
        return dict(json.loads(Path(path).read_text("utf-8")) or {})
    except (OSError, ValueError):
        return {}


def stage_capital_fractions(stages: DeploymentStages, tournament_cfg: Mapping[str, Any] | None) -> dict[str, float]:
    """``{symbol: Anteil der regulären Allokation}`` je Paar in ``LIVE_SMALL`` (``capital_fraction_small``)
    oder ``LIVE_FULL`` (1,0) — die Allocator-Skalierung des Echtgeld-Bots."""
    cfg = incubation_config(tournament_cfg)
    out: dict[str, float] = {}
    for strategy, symbol in stages.pairs_in(LIVE_SMALL, LIVE_FULL):
        out[symbol] = (float(cfg["capital_fraction_small"])
                       if stages.stage(strategy, symbol) == LIVE_SMALL else 1.0)
    return out


def read_incubation_record(strategy: str, symbol: str, *, directory: Path = INCUBATION_DIR) -> dict | None:
    try:
        return json.loads((Path(directory) / f"incubation_{strategy}_{symbol}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None


def write_incubation_whitelist(stages: DeploymentStages, *, directory: Path = INCUBATION_DIR,
                               path: Path = INCUBATION_WHITELIST_PATH, now: datetime | None = None) -> dict:
    """Die Paare des Demo-Bots: je ``INCUBATING``-Paar die EINGEFRORENEN Parameter aus
    ``incubation_<strategy>_<symbol>.json`` samt ``params_sha256`` (``per_symbol_winners``-Form, damit der
    Bot dieselbe Universums-Verschneidung nutzt). Ein Paar ohne Record oder mit abweichendem Fingerabdruck
    wird ausgelassen (nicht prüfbar ⇒ nicht gehandelt)."""
    winners: dict[str, dict] = {}
    for strategy, symbol in stages.pairs_in(INCUBATING):
        record = read_incubation_record(strategy, symbol, directory=directory)
        sha = stages.entry(strategy, symbol).get("params_sha256")
        if not record or record.get("params_sha256") != sha or params_fingerprint(record["params"]) != sha:
            continue
        winners[symbol] = {"strategy": strategy, "stage": INCUBATING, "params": record["params"],
                           "params_sha256": sha}
    payload = {"generated_at": _now_iso(now), "stage": INCUBATING, "per_symbol_winners": winners}
    write_json_atomic(Path(path), payload)
    return payload


# ─── Selektion & Zyklus ──────────────────────────────────────────────────────────────

def params_fingerprint(params: Mapping[str, Any]) -> str:
    """Derselbe Fingerabdruck wie der Whitelist-Eintrag ``live_params_sha256`` (#1360) — EINE Hashfunktion,
    damit Stufe, Ledger und Bot denselben Kandidaten meinen."""
    from automation.live_params import live_params_sha256

    return live_params_sha256(params)


def _oos_psr(winner: Mapping[str, Any]) -> float | None:
    """Rangfolge-Grösse: deflationierte OOS-PSR, wo vorhanden, sonst die OOS-PSR des Turniers
    (``oos_metrics.psr``) — die Familien-Korrektur trägt der sequenzielle Test (Bonferroni)."""
    for value in (winner.get("deflated_oos_psr"), winner.get("oos_psr"),
                  (winner.get("oos_metrics") or {}).get("psr")):
        if value is not None:
            return float(value)
    return None


def select_incubation_candidates(
    winners: Mapping[str, Mapping[str, Any]], *, max_concurrent: int, already_active: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> list[tuple[str, str, Mapping[str, Any]]]:
    """Paare aus den Tournament-Gewinnern (``{symbol: {strategy, oos_eligible, oos_psr|deflated_psr, ...}}``),
    die die Eligibility-Gates bestehen, sortiert nach deflationierter OOS-PSR (absteigend), aufgefüllt bis
    ``max_concurrent`` aktive Inkubationen. Bereits aktive Paare (``already_active``) zählen mit; ``exclude``
    (bereits live oder zurückgezogen) belegt keinen freien Platz."""
    active = set(already_active)
    excluded = set(exclude)
    free = max(0, int(max_concurrent) - len(active))
    rows = []
    for symbol, w in (winners or {}).items():
        if not w or w.get("oos_eligible") is not True:
            continue
        key = pair_key(w.get("strategy"), symbol)
        if key in active or key in excluded:
            continue
        score = _oos_psr(w)
        rows.append((-(float(score) if score is not None else -1.0), w.get("strategy"), symbol, w))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return [(strategy, symbol, w) for _, strategy, symbol, w in rows[:free]]


def write_incubation_record(strategy: str, symbol: str, params: Mapping[str, Any], *,
                            directory: Path = INCUBATION_DIR, now: datetime | None = None,
                            source: Mapping[str, Any] | None = None) -> dict:
    """``incubation_<strategy>_<symbol>.json`` mit eingefrorenen Parametern und ``params_sha256``."""
    record = {"strategy": strategy, "symbol": symbol, "params": dict(params),
              "params_sha256": params_fingerprint(params), "frozen_utc": _now_iso(now),
              "source": dict(source or {})}
    write_json_atomic(Path(directory) / f"incubation_{strategy}_{symbol}.json", record)
    return record


def look_index(since_utc: str, *, now: datetime, look_interval_days: int) -> int:
    """Der wievielte geplante Prüfzeitpunkt (1-basiert) seit Inkubationsbeginn ``since_utc`` erreicht ist."""
    start = datetime.fromisoformat(since_utc.replace("Z", "+00:00"))
    return max(0, int((now - start) / timedelta(days=look_interval_days)))


@dataclass
class CycleResult:
    evaluated: list[dict]
    started: list[dict]

    def to_dict(self) -> dict:
        return {"evaluated": self.evaluated, "started": self.started}


def _tripped_since(trips: Mapping[str, Any], symbol: str, since_utc: str | None) -> bool:
    trip = (trips or {}).get(symbol)
    if not trip:
        return False
    if not since_utc or not trip.get("utc"):
        return True
    return str(trip["utc"]) >= str(since_utc)


def _set_look(stages: DeploymentStages, strategy: str, symbol: str, idx: int, **extra) -> None:
    stages.data[pair_key(strategy, symbol)].update({"last_look_index": idx, **extra})
    write_json_atomic(stages.path, stages.data)


def run_incubation_cycle(
    stages: DeploymentStages, *, winners: Mapping[str, Mapping[str, Any]], tournament_cfg: Mapping[str, Any],
    resolve_params, deployment_decision_fn=None, now: datetime | None = None,
    ledger_dir: Path = INCUBATION_DIR, live_ledger_dir: Path | None = None, psr_fn=None,
    distribution_trips: Mapping[str, Any] | None = None, inc_cfg: Mapping[str, Any] | None = None,
) -> CycleResult:
    """Ein täglicher Zyklus:

    0. Verteilungs-Auslöser (#1362) seit Stufenbeginn ⇒ ``RETIRED`` (jede Stufe).
    1. ``INCUBATING`` an einem fälligen Prüfzeitpunkt sequenziell testen: ``PROMOTE`` ⇒ ``LIVE_SMALL`` NUR über
       ``deployment_decision_fn(strategy, symbol)['admitted'] is True`` (alle Klauseln, inklusive #1357/#1360;
       sonst bleibt das Paar inkubiert, ``blocked_by = deployment_gate``); ``RETIRE``/``EXHAUSTED`` ⇒ ``RETIRED``.
    2. ``LIVE_SMALL``: sobald ``t_full_bars`` Echtgeld-Bars (eigenes Ledger) vorliegen, dieselbe Schranke erneut:
       ``PROMOTE`` ∧ Gate ⇒ ``LIVE_FULL``; ``RETIRE`` ⇒ ``RETIRED``.
    3. Freie Plätze mit neuen Kandidaten füllen (``CANDIDATE → INCUBATING``, eingefrorene Parameter aus
       ``resolve_params(strategy, symbol)`` — ``live_params.resolve_live_params``).

    ``inc_cfg``: bereits aufgelöste Inkubations-Config (z. B. das Paper-Overlay mit mehr Plätzen); ohne sie gilt
    ``incubation_config(tournament_cfg)``."""
    cfg = dict(inc_cfg) if inc_cfg is not None else incubation_config(tournament_cfg)
    now = now or datetime.now(timezone.utc)
    live_ledger_dir = Path(live_ledger_dir) if live_ledger_dir is not None else Path(ledger_dir) / "live"
    gate_fn = deployment_decision_fn or (lambda *_a: {"admitted": None})
    seq_kwargs = dict(k_max=int(cfg["k_max"]), n_concurrent=int(cfg["max_concurrent"]),
                      confidence=float(cfg["confidence"]), psr_fn=psr_fn)
    evaluated: list[dict] = []

    for strategy, symbol in stages.pairs_in(INCUBATING, LIVE_SMALL, LIVE_FULL):
        entry = stages.entry(strategy, symbol)
        if _tripped_since(distribution_trips or {}, symbol, entry.get("since_utc")):
            stages.transition(strategy, symbol, RETIRED, reason="distribution_trigger", now=now)
            evaluated.append({"pair": pair_key(strategy, symbol), "decision": "RETIRE",
                              "reason": "Verteilungs-Auslöser (#1362)"})

    for strategy, symbol in stages.pairs_in(INCUBATING):
        entry = stages.entry(strategy, symbol)
        idx = look_index(entry["since_utc"], now=now, look_interval_days=int(cfg["look_interval_days"]))
        if idx <= int(entry.get("last_look_index", 0)):
            continue
        returns = EvidenceLedger(strategy, symbol, ledger_dir).returns(entry.get("params_sha256") or "")
        decision = sequential_decision(returns, look_index=idx, **seq_kwargs)
        result = {"pair": pair_key(strategy, symbol), "stage": INCUBATING, **decision.to_dict()}
        if decision.decision == "PROMOTE":
            gate = gate_fn(strategy, symbol) or {}
            if gate.get("admitted") is True:
                stages.transition(strategy, symbol, LIVE_SMALL, reason="sequential_promote",
                                  deployment_decision=gate, now=now,
                                  extra={"capital_fraction": float(cfg["capital_fraction_small"]),
                                         "last_look_index": 0, "incubation_look_index": idx})
            else:
                result["blocked_by"] = "deployment_gate"
                result["blocking_clause"] = gate.get("blocking_clause")
                _set_look(stages, strategy, symbol, idx)
        elif decision.decision in ("RETIRE", "EXHAUSTED"):
            stages.transition(strategy, symbol, RETIRED, reason=f"sequential_{decision.decision.lower()}",
                              now=now, extra={"last_look_index": idx})
        else:
            _set_look(stages, strategy, symbol, idx)
        evaluated.append(result)

    for strategy, symbol in stages.pairs_in(LIVE_SMALL):
        # Dieselbe Schranke auf NEUER Echtgeld-Evidenz, an denselben wöchentlichen Prüfzeitpunkten (gezählt ab
        # Beginn von LIVE_SMALL, höchstens k_max) — erst ab ``t_full_bars`` Bars; kein täglicher Neutest.
        entry = stages.entry(strategy, symbol)
        idx = look_index(entry["since_utc"], now=now, look_interval_days=int(cfg["look_interval_days"]))
        if idx <= int(entry.get("last_look_index", 0)):
            continue
        returns = EvidenceLedger(strategy, symbol, live_ledger_dir).returns(entry.get("params_sha256") or "")
        if len(returns) < int(cfg["t_full_bars"]) and idx < int(cfg["k_max"]):
            continue
        decision = sequential_decision(returns, look_index=idx, min_bars=int(cfg["t_full_bars"]), **seq_kwargs)
        result = {"pair": pair_key(strategy, symbol), "stage": LIVE_SMALL, **decision.to_dict()}
        if decision.decision == "PROMOTE":
            gate = gate_fn(strategy, symbol) or {}
            if gate.get("admitted") is True:
                stages.transition(strategy, symbol, LIVE_FULL, reason="sequential_promote_full",
                                  deployment_decision=gate, now=now, extra={"capital_fraction": 1.0})
            else:
                result["blocked_by"] = "deployment_gate"
                result["blocking_clause"] = gate.get("blocking_clause")
                _set_look(stages, strategy, symbol, idx)
        elif decision.decision in ("RETIRE", "EXHAUSTED"):
            stages.transition(strategy, symbol, RETIRED, reason=f"sequential_{decision.decision.lower()}_live",
                              now=now, extra={"last_look_index": idx})
        else:
            _set_look(stages, strategy, symbol, idx)
        evaluated.append(result)

    started: list[dict] = []
    active = [pair_key(s, y) for s, y in stages.pairs_in(INCUBATING)]
    done = [pair_key(s, y) for s, y in stages.pairs_in(LIVE_SMALL, LIVE_FULL, RETIRED)]
    for strategy, symbol, _w in select_incubation_candidates(
            winners, max_concurrent=int(cfg["max_concurrent"]), already_active=active, exclude=done):
        params = resolve_params(strategy, symbol)
        record = write_incubation_record(strategy, symbol, params, directory=ledger_dir, now=now,
                                         source={"oos_psr": _oos_psr(_w)})
        if stages.stage(strategy, symbol) is None:
            stages.transition(strategy, symbol, CANDIDATE, reason="incubation_selection", now=now)
        stages.transition(strategy, symbol, INCUBATING, reason="incubation_start",
                          params_sha256=record["params_sha256"], now=now, extra={"last_look_index": 0})
        started.append({"pair": pair_key(strategy, symbol), "params_sha256": record["params_sha256"]})
    return CycleResult(evaluated, started)


def incubation_bot_spec(stages: DeploymentStages, *, environment: str) -> list[dict]:
    """Die Paare, die die Demo-Bot-Instanz handelt (``INCUBATING``), mit eingefrorenem Fingerabdruck — wirft
    ``IncubationEnvironmentError``, sobald ``environment`` nicht ``demo`` ist."""
    pairs = stages.pairs_in(INCUBATING)
    for _ in pairs:
        assert_stage_environment(INCUBATING, environment)
    return [{"strategy": s, "symbol": y, "params_sha256": stages.entry(s, y).get("params_sha256")}
            for s, y in pairs]
