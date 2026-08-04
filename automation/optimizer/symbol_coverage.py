"""automation/optimizer/symbol_coverage.py
===========================================
Issue #841 (Katalog #840-#843, GitHub-Issue #754) — Symbol-Abdeckungs-Ledger.

Der gemessene Takt (~24,7 min/Symbol) und ein begrenztes Laufzeit-Budget (#842) bedeuten: nicht
jeder Lauf deckt das gesamte Universum ab. Die Symbolreihenfolge war bislang die reine
Einfüge-Reihenfolge aus ``data/universe/momentum_ls.json`` (Momentum-Rang) — ohne Rotation des
Startoffsets und ohne Gedächtnis, wann ein Symbol zuletzt optimiert wurde. Solange dieses Ranking
stabil bleibt, werden immer dieselben Symbole optimiert und dieselben ignoriert; das koppelt die
Abdeckung des Optimizers an eine Rangliste, die nichts mit der Frage zu tun hat, welches Symbol
eine frische Parameter-Kalibrierung braucht.

``data/optimizer/symbol_coverage.json`` (ausserhalb ``logs/``, also nicht von
``cleanup_old_logs`` betroffen — analog zur #742-Report-Entscheidung) hält je Symbol
``last_completed_run_id``/``last_completed_run_index``/``last_completed_at_utc``/
``n_completions``/``last_data_snapshot_sha256``, sowie global ``total_runs_started`` (ein
monoton wachsender Zähler realer Sweep-Starts — die Basis für "seit wie vielen LÄUFEN nicht
abgedeckt", nicht nur "seit wann"). ``sweep.py`` schreibt es atomar am selben Punkt wie den
#799-Fortschritts-Checkpoint.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from automation.optimizer.manifest import WORK, write_json_atomic

_SCHEMA_VERSION = 1


def coverage_path(work_dir: Path | None = None) -> Path:
    return Path(work_dir or WORK) / "symbol_coverage.json"


def load_coverage(path: Path | None = None) -> dict[str, Any]:
    """Fail-open (leeres Ledger) bei fehlender/kaputter Datei — ein defektes Ledger darf den
    Sweep nie verhindern, nur die Rotation auf die 'universe'-Reihenfolge zurückfallen lassen."""
    p = path or coverage_path()
    try:
        data = json.loads(Path(p).read_text("utf-8"))
        if isinstance(data, dict) and isinstance(data.get("symbols"), dict):
            data.setdefault("schema_version", _SCHEMA_VERSION)
            data.setdefault("total_runs_started", 0)
            return data
    except (OSError, ValueError):
        pass
    return {"schema_version": _SCHEMA_VERSION, "total_runs_started": 0, "symbols": {}}


def start_new_run(coverage: dict[str, Any]) -> dict[str, Any]:
    """Issue #841 — EINMAL beim Start eines echten Sweep-Laufs aufgerufen: erhöht
    ``total_runs_started`` um 1. Reine Funktion (neues Dict), analog ``record_symbol_completion``."""
    out = {
        "schema_version": coverage.get("schema_version", _SCHEMA_VERSION),
        "total_runs_started": int(coverage.get("total_runs_started", 0)) + 1,
        "symbols": dict(coverage.get("symbols") or {}),
    }
    return out


def record_symbol_completion(coverage: dict[str, Any], symbol: str, *, run_id: str,
                             run_index: int, completed_at_utc: str,
                             data_snapshot_sha256: str | None = None) -> dict[str, Any]:
    """Reine Funktion: liefert ein NEUES Ledger-Dict mit ``symbol`` aktualisiert (kein
    In-Place-Mutieren des Aufrufer-Dicts, damit ein Test Vorher/Nachher vergleichen kann)."""
    out = {
        "schema_version": coverage.get("schema_version", _SCHEMA_VERSION),
        "total_runs_started": coverage.get("total_runs_started", 0),
        "symbols": dict(coverage.get("symbols") or {}),
    }
    prior = out["symbols"].get(symbol) or {}
    out["symbols"][symbol] = {
        "last_completed_run_id": run_id,
        "last_completed_run_index": run_index,
        "last_completed_at_utc": completed_at_utc,
        "n_completions": int(prior.get("n_completions", 0)) + 1,
        "last_data_snapshot_sha256": data_snapshot_sha256,
    }
    return out


def mark_gate1_covered(coverage: dict[str, Any], symbol: str, *, run_id: str, run_index: int,
                       completed_at_utc: str) -> dict[str, Any]:
    """Issue #841 Punkt 5 — ein Symbol, das Gate 1 verwirft (REJECT_DATA_INSUFFICIENT_GEOMETRY),
    zählt als abgedeckt (``covered_gate1``), damit es die #841-Invariante nicht dauerhaft rot
    färbt — es wird nachweislich JEDEN Lauf geprüft, nur strukturell nie optimiert."""
    out = record_symbol_completion(coverage, symbol, run_id=run_id, run_index=run_index,
                                   completed_at_utc=completed_at_utc, data_snapshot_sha256=None)
    out["symbols"][symbol]["covered_gate1"] = True
    return out


def write_coverage(coverage: dict[str, Any], path: Path | None = None) -> None:
    write_json_atomic(path or coverage_path(), coverage)


def order_symbols(symbols: list[str], coverage: dict[str, Any], *,
                  policy: str = "least_recently_covered") -> list[str]:
    """Issue #841 Punkt 2 — ``'universe'`` reproduziert die Einfüge-Reihenfolge bitgleich
    (Rückwärtskompatibilität); ``'least_recently_covered'`` (Default) sortiert aufsteigend nach
    ``last_completed_at_utc`` (nie abgedeckte Symbole zuerst — fehlender Eintrag sortiert vor
    jedem echten Zeitstempel), Tie-Break durch einen stabilen Sort auf dem Universums-Rang
    (deterministisch: zwei Aufrufe mit identischem Ledger liefern dieselbe Liste)."""
    if policy == "universe":
        return list(symbols)
    if policy != "least_recently_covered":
        raise ValueError(f"unbekannte sweep_symbol_order_policy: {policy!r}")
    entries = coverage.get("symbols") or {}

    def _sort_key(sym: str) -> str:
        rec = entries.get(sym)
        if not rec:
            return ""  # nie abgedeckt -> lexikografisch kleinstmoeglich -> zuerst
        return rec.get("last_completed_at_utc") or ""

    return sorted(symbols, key=_sort_key)


def coverage_report(coverage: dict[str, Any], universe: list[str], *, max_age_runs: int = 3) -> dict[str, Any]:
    """Issue #841 Punkt 4 — ``cross_study.symbol_coverage``-Rohmaterial UND Grundlage von
    ``invariants.check_symbol_coverage``: ``{covered_this_run, never_covered, stale_symbols,
    oldest_coverage_age_runs}``. ``covered_this_run`` ist NICHT Teil dieses Ledger-Schnappschusses
    (das ist Sache des Aufrufers, der weiss, welche Symbole DIESER Lauf abgeschlossen hat) — hier
    ausschliesslich die Alters-Perspektive über das GESAMTE Universum."""
    entries = coverage.get("symbols") or {}
    total_runs = int(coverage.get("total_runs_started", 0))
    never_covered: list[str] = []
    stale_symbols: dict[str, int] = {}
    oldest_age = 0
    for sym in universe:
        rec = entries.get(sym)
        if not rec:
            never_covered.append(sym)
            oldest_age = max(oldest_age, total_runs)
            continue
        last_idx = rec.get("last_completed_run_index")
        if last_idx is None:
            never_covered.append(sym)
            oldest_age = max(oldest_age, total_runs)
            continue
        age = max(0, total_runs - int(last_idx))
        oldest_age = max(oldest_age, age)
        if age > max_age_runs:
            stale_symbols[sym] = age
    return {
        "never_covered": sorted(never_covered),
        "stale_symbols": stale_symbols,
        "oldest_coverage_age_runs": oldest_age,
        "total_runs_started": total_runs,
    }
