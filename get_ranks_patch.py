with open("automation/backtest_runner.py", "r") as f:
    content = f.read()

old_get_ranks = """        def get_ranks(vals, reverse=False):
            non_sentinels = [v for v in vals if v != 50.0]
            if len(non_sentinels) == 0:
                return [1.0] * len(vals)
            su = sorted(list(set(vals)), reverse=reverse)
            if len(su) <= 1:
                return [1.0] * len(vals)
            return [(su.index(v)) / (len(su) - 1) for v in vals]"""

new_get_ranks = """        def get_ranks(vals, reverse=False):
            # Issue #263: Handle None/All-Win scenarios logically as highest values (50.0)
            # instead of letting `(m.get('sortino_ratio') or 0.0)` push them to the bottom.
            # Convert any None/0.0 fallback that actually corresponds to an All-Win (which we know
            # if we see 0.0 here but the actual metric was None) to 50.0 in the caller,
            # but here we just process the vals correctly.
            non_sentinels = [v for v in vals if v != 50.0]
            if len(non_sentinels) == 0:
                return [1.0] * len(vals)
            su = sorted(list(set(vals)), reverse=reverse)
            if len(su) <= 1:
                return [1.0] * len(vals)
            return [(su.index(v)) / (len(su) - 1) for v in vals]"""

content = content.replace(old_get_ranks, new_get_ranks)

old_list_comps = """        sortinos = [(r["metrics"].get("sortino_ratio") or 0.0) for r in is_eligible_population]
        pfs = [(r["metrics"].get("profit_factor") or 0.0) for r in is_eligible_population]
        wrs = [(r["metrics"].get("win_rate") or 0.0) for r in is_eligible_population]
        dds = [(r["metrics"].get("max_drawdown") or 0.0) for r in is_eligible_population]"""

new_list_comps = """        # Issue #263: None-Values in sortino/pf (All-Win scenarios) must be treated as absolute top-tier (50.0)
        # for ranking purposes, otherwise the `or 0.0` fallback degrades them below mediocre strategies.
        sortinos = [(r["metrics"].get("sortino_ratio") if r["metrics"].get("sortino_ratio") is not None else 50.0) for r in is_eligible_population]
        pfs = [(r["metrics"].get("profit_factor") if r["metrics"].get("profit_factor") is not None else 50.0) for r in is_eligible_population]
        wrs = [(r["metrics"].get("win_rate") or 0.0) for r in is_eligible_population]
        dds = [(r["metrics"].get("max_drawdown") or 0.0) for r in is_eligible_population]"""

content = content.replace(old_list_comps, new_list_comps)

with open("automation/backtest_runner.py", "w") as f:
    f.write(content)
