import json
from pathlib import Path

def resolve_params(strategy_class: str, sampled: dict, base_cfg: Path) -> dict:
    """
    Reihenfolge: strategy_defaults.json < strategies.json[params] < sampled (höchste Prio).
    """
    params = {}

    # 1. defaults
    defaults_path = base_cfg / "strategy_defaults.json"
    if defaults_path.exists():
        with open(defaults_path, "r", encoding="utf-8") as f:
            defaults_data = json.load(f)
            if strategy_class in defaults_data:
                params.update(defaults_data[strategy_class])

    # 2. strategies.json[params]
    strats_path = base_cfg / "strategies.json"
    if strats_path.exists():
        with open(strats_path, "r", encoding="utf-8") as f:
            strats_data = json.load(f)
            for strat_entry in strats_data.get("strategies", []):
                if strat_entry.get("strategy_class") == strategy_class:
                    if "params" in strat_entry:
                        params.update(strat_entry["params"])
                    break

    # 3. sampled
    params.update(sampled)

    return params
