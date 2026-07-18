import json
from pathlib import Path

def resolve_params(strategy_class: str, sampled: dict, base_cfg: Path,
                   *, instrument: str | None = None) -> dict:
    """
    Reihenfolge: strategy_defaults.json < strategy_symbol_seeds.json[strategy][instrument]
    (nur falls instrument != None, Issue #706) < strategies.json[params]
    < instrument_overrides[instrument] (nur falls instrument != None) < sampled (höchste Prio).

    instrument=None ⇒ exakt bisheriges Verhalten (rückwärtskompatibel, A4.1/#706 additiv).
    """
    params = {}

    # 1. defaults
    defaults_path = base_cfg / "strategy_defaults.json"
    if defaults_path.exists():
        with open(defaults_path, "r", encoding="utf-8") as f:
            defaults_data = json.load(f)
            if strategy_class in defaults_data:
                params.update(defaults_data[strategy_class])

    # 1.5. Issue #706 (Epic #702, Ebene 2) — symbol-skopierter, automatisch nachgeführter
    # Champion-Seed-Prior. NIE der globale Cross-Symbol-Default (strategy_defaults.json) — eine
    # separate, symbol-skopierte Datei, AUSSCHLIESSLICH via champions.maybe_write_back geschrieben
    # (korroboriert + fensterfortgeschritten, siehe champions.py-Docstring). Nur wirksam, wenn ein
    # instrument gesetzt ist (dasselbe additive A4.1-Muster wie instrument_overrides unten) — ohne
    # instrument bit-identisch zum Pre-#706-Verhalten.
    if instrument is not None:
        seeds_path = base_cfg / "strategy_symbol_seeds.json"
        if seeds_path.exists():
            with open(seeds_path, "r", encoding="utf-8") as f:
                seeds_data = json.load(f) or {}
                seed = ((seeds_data.get("seeds") or {}).get(strategy_class) or {}).get(instrument)
                if seed:
                    params.update(seed)

    # 2. strategies.json[params] (+ instrument_overrides[instrument], additiv)
    strats_path = base_cfg / "strategies.json"
    if strats_path.exists():
        with open(strats_path, "r", encoding="utf-8") as f:
            strats_data = json.load(f)
            for strat_entry in strats_data.get("strategies", []):
                if strat_entry.get("strategy_class") == strategy_class:
                    if "params" in strat_entry:
                        params.update(strat_entry["params"])
                    # A4.1: symbol-spezifische Overrides — nur wenn ein instrument gesetzt ist.
                    if instrument is not None:
                        overrides = strat_entry.get("instrument_overrides") or {}
                        params.update(overrides.get(instrument) or {})
                    break

    # 3. sampled (höchste Prio)
    params.update(sampled)

    return params
