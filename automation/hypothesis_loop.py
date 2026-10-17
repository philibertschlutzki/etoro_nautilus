"""Geschlossener Hypothesen-Regelkreis (wöchentlich, systemd-User-Timer): Reports einlesen -> Kandidaten prüfen -> dokumentieren.

Ablauf (``python3 -m automation.hypothesis_loop``):
  1. Ingestion: neueste Reports unter ``results/weekly`` und ``results/daily`` (Markdown-Tabellen) werden strukturiert gelesen, auffällige Werte als Hinweise
     ausgegeben (nur Auslöser für neue Hypothesen, keine Entscheidung).
  2. Kandidaten: JSON-Dateien in ``data/hypotheses/candidates`` (Schema siehe ``load_candidate``). Wer sie schreibt (Mensch, LLM-Loop in ``automation/ai_loop``),
     ist egal; hier zählt nur die Prüfung. Ein Kandidat braucht einen kausalen Marktgrund (``mechanism_class`` aus ``CAUSAL_CLASSES`` plus Text).
  3. Falsifikation, fail-closed: Kosten (Spread + Gebühr je Klasse, Quelle ``backtest.json``) werden vom Brutto abgezogen, dann DSR (Mehrfachtests inkl. aller
     bisherigen Registry-Trials), PBO (CSCV über die Varianten), Mindestzahl Perioden und Profit Factor. Fehlt etwas oder ist es nicht berechenbar -> Verwurf.
  4. Dokumentation: ``results/hypotheses/<Datum>.md``; Gewinner als Vorschlag ``results/hypotheses/proposals/<id>.json``; Verworfene nach
     ``data/hypotheses/rejected``. ``config/optimizer.json`` und der Bot werden NIE angefasst, die Promotion entscheidet der Mensch.

Das Registry (``data/state/hypothesis_registry.json``) startet aus ``automation/config/hypothesis_seed.json`` (H1-H9, Kalibrierung, Drift). Jede geprüfte
Variante erhöht ``n_trials`` und damit die Deflationshürde der Folgeläufe; bereits registrierte Ids/Fingerprints werden nicht erneut geprüft.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from automation.optimizer.cpcv import probability_of_backtest_overfitting
from automation.optimizer.deflation import (
    deflated_sharpe_ratio,
    sample_skew_kurtosis,
    sr0_multiple_testing,
)

ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = ROOT / "automation" / "config" / "hypothesis_seed.json"
BACKTEST_CFG = ROOT / "automation" / "config" / "backtest.json"
REGISTRY_PATH = ROOT / "data" / "state" / "hypothesis_registry.json"
CANDIDATES_DIR = ROOT / "data" / "hypotheses" / "candidates"
REJECTED_DIR = ROOT / "data" / "hypotheses" / "rejected"
OUT_DIR = ROOT / "results" / "hypotheses"
REPORT_DIRS = (ROOT / "results" / "weekly", ROOT / "results" / "daily")

# Nur Mechanismen mit Marktgrund; beliebige Indikator-Kombinationen ohne Begründung fallen durch.
CAUSAL_CLASSES = frozenset({
    "trend_strength", "liquidity_profile", "session_overlap", "cost_structure",
    "volatility_regime", "financing_carry", "event_calendar",
})
# Gemessene Spreads je Klasse (bps, einfach). US-Aktien in der Regular Session 7 (nicht die 15-26 über alle Stunden), Gebühren kommen aus backtest.json.
SPREAD_BPS = {"EQUITY": 7.0, "CRYPTO": 15.0, "COMMODITY": 10.0, "FX": 1.0, "INDEX": 5.0}
FEE_FALLBACK_BPS = {"EQUITY": 10.0, "CRYPTO": 200.0}
DEFAULT_CRITERIA = {"min_dsr": 0.95, "max_pbo": 0.20, "min_periods": 100, "min_variants": 8, "min_profit_factor": 1.25}
CSCV_GROUPS = 8
MIN_MECHANISM_CHARS = 40


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n", "utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------- Registry

def load_registry(path: Path = REGISTRY_PATH, seed_path: Path = SEED_PATH) -> dict:
    reg = _load_json(path, None)
    if isinstance(reg, dict) and "hypotheses" in reg:
        return reg
    seed = _load_json(seed_path, {"hypotheses": []})
    return {"hypotheses": {h["id"]: dict(h, source="seed") for h in seed.get("hypotheses", [])}}


def total_trials(reg: dict) -> int:
    return int(sum(int(h.get("n_trials") or 0) for h in reg["hypotheses"].values()))


def fingerprint(cand: dict) -> str:
    key = json.dumps({"m": " ".join(str(cand.get("mechanism", "")).lower().split()), "i": cand.get("intervention")}, sort_keys=True, default=str)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- Ingestion

def parse_md_tables(text: str) -> list[dict]:
    """Markdown-Tabellen -> [{'heading': str, 'rows': [dict]}]. Zellen bleiben Text, Zahlen holt ``num``."""
    out, heading, lines = [], "", text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("#"):
            heading = ln.lstrip("# ").strip()
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?$", lines[i + 1]):
            hdr = [c.strip() for c in ln.strip().strip("|").split("|")]
            rows, i = [], i + 2
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(dict(zip(hdr, cells)))
                i += 1
            out.append({"heading": heading, "rows": rows})
            continue
        i += 1
    return out


def num(s) -> float | None:
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(s).replace("−", "-"))
    return float(m.group(0).replace(",", ".")) if m else None


def ingest_reports(dirs=REPORT_DIRS) -> dict:
    """Liest je Verzeichnis den neuesten Report. Hinweise sind nur Auslöser für neue Hypothesen."""
    notes, files = [], []
    for d in dirs:
        reports = sorted(d.glob("*.md")) if d.is_dir() else []
        if not reports:
            continue
        f = reports[-1]
        files.append(str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f))
        for tbl in parse_md_tables(f.read_text("utf-8", "replace")):
            for row in tbl["rows"]:
                dd, ret = num(row.get("MaxDD")), num(row.get("Rendite"))
                if dd is not None and dd <= -10.0:
                    notes.append(f"{f.name}: {row.get('Arm', '?')} MaxDD {dd:.1f} % (<= -10 %), Risikoprüfung nötig")
                if ret is not None and ret <= -3.0:
                    notes.append(f"{f.name}: {row.get('Arm', '?')} Rendite {ret:.2f} % (<= -3 %)")
                med, mod = num(row.get("Median p50 bps")), num(row.get("Modell bps"))
                if med is not None and mod and med > 2.0 * mod:
                    notes.append(f"{f.name}: {row.get('Klasse', '?')} gemessener Spread {med:g} bps > 2x Modell {mod:g} bps (Session getrennt prüfen, Aktien 7 bps in der US-Session sind korrekt)")
    return {"files": files, "notes": notes}


# ---------------------------------------------------------------- Falsifikation

def round_trip_cost_bps(asset_class: str, cfg_path: Path = BACKTEST_CFG) -> float:
    """Spread + Gebühr je Round-Trip in bps; unbekannte Klasse -> Fehler (fail-closed)."""
    ac = asset_class.upper()
    if ac not in SPREAD_BPS:
        raise ValueError(f"unbekannte Anlageklasse {asset_class!r}")
    fees = (_load_json(cfg_path, {}).get("commission_bps_by_asset_class") or {})
    fee = float(fees.get(ac, FEE_FALLBACK_BPS.get(ac, 0.0)))
    return SPREAD_BPS[ac] + fee


def load_candidate(path: Path) -> dict:
    """Schema: id, category, asset_class, observation, mechanism_class, mechanism, intervention{...},
    variants{name: [Brutto-Rendite je Trade in bps, gleiche Länge, zeitlich geordnet]}, primary (Name der Variante),
    falsification{min_dsr, max_pbo, min_periods, min_variants, min_profit_factor} (nur Verschärfung)."""
    return json.loads(path.read_text("utf-8"))


def validate_schema(c: dict) -> list[str]:
    errs = []
    for k in ("id", "asset_class", "mechanism_class", "mechanism", "variants", "primary"):
        if not c.get(k):
            errs.append(f"Feld {k} fehlt")
    if errs:
        return errs
    if c["mechanism_class"] not in CAUSAL_CLASSES:
        errs.append(f"mechanism_class {c['mechanism_class']!r} ist kein kausaler Marktgrund ({sorted(CAUSAL_CLASSES)})")
    if len(str(c["mechanism"]).strip()) < MIN_MECHANISM_CHARS:
        errs.append(f"mechanism kürzer als {MIN_MECHANISM_CHARS} Zeichen, kein erkennbarer Marktgrund")
    v = c["variants"]
    if not isinstance(v, dict) or c["primary"] not in v:
        errs.append("primary muss in variants vorkommen")
        return errs
    lens = {len(x) for x in v.values()}
    if len(lens) != 1:
        errs.append("variants haben unterschiedliche Längen")
    return errs


def effective_criteria(c: dict) -> dict:
    """Kandidat darf Schwellen nur verschärfen, nie lockern."""
    crit = dict(DEFAULT_CRITERIA)
    for k, v in (c.get("falsification") or {}).items():
        if k not in crit:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        crit[k] = min(crit[k], fv) if k == "max_pbo" else max(crit[k], fv)
    return crit


def pbo_cscv(matrix: np.ndarray, n_groups: int = CSCV_GROUPS) -> float:
    """PBO nach CSCV: alle Hälften der Zeitgruppen als IS, Rest OOS, Sharpe je Variante (T x N-Matrix)."""
    t, n = matrix.shape
    if n < 2 or t < n_groups * 2:
        return float("nan")
    bounds = np.linspace(0, t, n_groups + 1).round().astype(int)
    groups = [matrix[bounds[i]:bounds[i + 1]] for i in range(n_groups)]

    def sharpe(rows: np.ndarray) -> np.ndarray:
        sd = rows.std(axis=0)
        return np.where(sd > 0, rows.mean(axis=0) / np.where(sd > 0, sd, 1.0), 0.0)

    is_p, oos_p = [], []
    for combo in itertools.combinations(range(n_groups), n_groups // 2):
        rest = [g for g in range(n_groups) if g not in combo]
        is_p.append(sharpe(np.vstack([groups[g] for g in combo])))
        oos_p.append(sharpe(np.vstack([groups[g] for g in rest])))
    return probability_of_backtest_overfitting(np.asarray(is_p), np.asarray(oos_p))


def falsify(c: dict, prior_trials: int, cfg_path: Path = BACKTEST_CFG) -> dict:
    """Urteil mit Einzelprüfungen; ``passed`` nur wenn ALLE bestanden. Jeder Fehler/NaN zählt als nicht bestanden."""
    res = {"id": c.get("id"), "checks": {}, "passed": False, "reasons": []}
    errs = validate_schema(c)
    if errs:
        res["reasons"] = errs
        return res
    crit = effective_criteria(c)
    try:
        cost = round_trip_cost_bps(c["asset_class"], cfg_path)
        names = list(c["variants"])
        mat = np.asarray([c["variants"][n] for n in names], dtype=float).T - cost  # (T, N) netto je Trade
    except (ValueError, TypeError) as e:
        res["reasons"] = [f"Eingabe nicht lesbar: {e}"]
        return res
    if not np.isfinite(mat).all():
        res["reasons"] = ["NaN/inf in den Renditen"]
        return res
    t, n = mat.shape
    prim = mat[:, names.index(c["primary"])]
    n_trials = prior_trials + n
    sd = float(prim.std())
    sr = float(prim.mean() / sd) if sd > 0 else 0.0
    srs = np.where(mat.std(axis=0) > 0, mat.mean(axis=0) / np.where(mat.std(axis=0) > 0, mat.std(axis=0), 1.0), 0.0)
    sr0 = sr0_multiple_testing(float(np.var(srs, ddof=1)) if n > 1 else 0.0, n_trials)
    skew, kurt = sample_skew_kurtosis(prim)
    dsr = deflated_sharpe_ratio(sr, t, sr0=sr0, skew=skew, kurtosis=kurt)
    pbo = pbo_cscv(mat)
    wins, losses = prim[prim > 0].sum(), -prim[prim < 0].sum()
    pf = float(wins / losses) if losses > 0 else (math.inf if wins > 0 else 0.0)
    res["metrics"] = {"cost_bps_rt": cost, "periods": t, "variants": n, "n_trials_deflation": n_trials, "mean_net_bps": float(prim.mean()),
                      "sharpe_per_trade": sr, "sr0": sr0, "dsr": dsr, "pbo": pbo, "profit_factor": pf}
    checks = {
        "net_mean_positive": prim.mean() > 0,
        "min_periods": t >= crit["min_periods"],
        "min_variants": n >= crit["min_variants"],
        "dsr": dsr is not None and not math.isnan(dsr) and dsr >= crit["min_dsr"],
        "pbo": not math.isnan(pbo) and pbo <= crit["max_pbo"],
        "profit_factor": pf >= crit["min_profit_factor"],
    }
    res["checks"] = {k: bool(v) for k, v in checks.items()}
    res["criteria"] = crit
    res["reasons"] = [k for k, ok in res["checks"].items() if not ok]
    res["passed"] = not res["reasons"]
    return res


# ---------------------------------------------------------------- Lauf

def run(candidates_dir: Path = CANDIDATES_DIR, out_dir: Path = OUT_DIR, registry_path: Path = REGISTRY_PATH,
        rejected_dir: Path = REJECTED_DIR, report_dirs=REPORT_DIRS, cfg_path: Path = BACKTEST_CFG, today: str | None = None) -> dict:
    today = today or _now().strftime("%Y-%m-%d")
    reg = load_registry(registry_path)
    ingest = ingest_reports(report_dirs)
    known_fp = {h.get("fingerprint") for h in reg["hypotheses"].values() if h.get("fingerprint")}
    verdicts, skipped = [], []
    for f in sorted(candidates_dir.glob("*.json")) if candidates_dir.is_dir() else []:
        try:
            cand = load_candidate(f)
        except ValueError as e:
            verdicts.append({"id": f.stem, "passed": False, "checks": {}, "reasons": [f"JSON ungültig: {e}"], "file": f})
            continue
        cid, fp = str(cand.get("id", f.stem)), fingerprint(cand)
        if cid in reg["hypotheses"] or fp in known_fp:
            skipped.append(cid)
            continue
        v = falsify(cand, total_trials(reg), cfg_path)
        v["file"] = f
        verdicts.append(v)
        n_new = len(cand.get("variants") or {})
        reg["hypotheses"][cid] = {
            "id": cid, "status": "passed_proposal" if v["passed"] else "rejected", "mechanism_class": cand.get("mechanism_class"),
            "claim": cand.get("observation") or cand.get("mechanism"), "evidence": f"results/hypotheses/{today}.md",
            "n_trials": n_new if v.get("metrics") else 0, "fingerprint": fp, "tested": today, "source": "loop",
            "metrics": v.get("metrics"), "reasons": v["reasons"],
        }
        if v["passed"]:
            _write_json(out_dir / "proposals" / f"{cid}.json", {
                "id": cid, "asof": today, "intervention": cand.get("intervention"), "mechanism": cand.get("mechanism"),
                "metrics": v["metrics"], "hinweis": "Nur Vorschlag. Keine Änderung an optimizer.json oder Bot; Phili entscheidet, danach Live-Gates und Forward-Shadow."})
        else:
            rejected_dir.mkdir(parents=True, exist_ok=True)
            _write_json(rejected_dir / f"{cid}.json", {"candidate": {k: v2 for k, v2 in cand.items() if k != "variants"}, "verdict": {k: x for k, x in v.items() if k != "file"}})
    _write_json(registry_path, reg)
    report = render_report(today, ingest, verdicts, skipped, reg)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{today}.md").write_text(report, "utf-8")
    return {"verdicts": verdicts, "skipped": skipped, "ingest": ingest, "report": out_dir / f"{today}.md"}


def render_report(today: str, ingest: dict, verdicts: list[dict], skipped: list[str], reg: dict) -> str:
    L = [f"# Hypothesen-Regelkreis {today}", "",
         f"Registry: {len(reg['hypotheses'])} Hypothesen, {total_trials(reg)} bisher geprüfte Varianten (zählen in die Deflation).", "",
         "## Telemetrie-Hinweise", ""]
    L += [f"- {n}" for n in ingest["notes"]] or ["- keine Auffälligkeiten"]
    L += ["", f"Gelesen: {', '.join(ingest['files']) or 'keine Reports gefunden'}", "", "## Kandidaten", ""]
    if not verdicts:
        L.append("Keine neuen Kandidaten in `data/hypotheses/candidates`." + (f" Übersprungen (schon geprüft): {', '.join(skipped)}." if skipped else ""))
    for v in verdicts:
        m = v.get("metrics") or {}
        L.append(f"### {v['id']}: {'BESTANDEN (nur Vorschlag)' if v['passed'] else 'VERWORFEN'}")
        if m:
            L.append(f"Netto {m['mean_net_bps']:.1f} bps/Trade nach {m['cost_bps_rt']:.0f} bps Round-Trip-Kosten, {m['periods']} Trades, {m['variants']} Varianten "
                     f"(Deflation über {m['n_trials_deflation']} Trials): DSR {m['dsr']:.3f}, PBO {m['pbo']:.2f}, PF {m['profit_factor']:.2f}.")
        if v["reasons"]:
            L.append("Durchgefallen: " + ", ".join(v["reasons"]))
        L.append("")
    if skipped and verdicts:
        L.append(f"Übersprungen (schon geprüft): {', '.join(skipped)}")
    L += ["", "## Stand der Hypothesen", "", "| Id | Status | Kausaler Grund | Befund |", "|---|---|---|---|"]
    for h in sorted(reg["hypotheses"].values(), key=lambda x: x["id"]):
        L.append(f"| {h['id']} | {h.get('status')} | {h.get('mechanism_class') or ''} | {str(h.get('claim', ''))[:110].replace('|', '/')} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--candidates-dir", type=Path, default=CANDIDATES_DIR)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    r = run(a.candidates_dir, a.out_dir)
    print(f"Report: {r['report']}  Kandidaten geprüft: {len(r['verdicts'])}, bestanden: {sum(v['passed'] for v in r['verdicts'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
