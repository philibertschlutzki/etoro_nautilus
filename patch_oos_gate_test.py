import re

with open("automation/tests/test_oos_gate.py", "r") as f:
    content = f.read()

# Add scenario B to the test
# Scenario B: Paar fällt IS durch, hätte aber OOS bestanden.

new_fixture = """
    # Add a negative fixture that fails IS min_sortino but passes OOS
    all_results.append({
        "symbol": "AMZN.ETORO",
        "strategy": "MeanReversionStrategy",
        "metrics": {
            "total_trades": 30,
            "sortino_ratio": 0.1,  # Fails IS min_sortino (0.3)
            "profit_factor": 1.5,
            "max_drawdown": 0.1,
            "win_rate": 0.6,
            "total_return": 0.1
        },
        "oos_metrics": {
            "total_trades": 25,
            "sortino_ratio": 0.5,
            "profit_factor": 1.3,
            "max_drawdown": 0.1,
            "win_rate": 0.5,
            "total_return": 0.05
        }
    })
"""

content = content.replace("per_symbol_winners, aggregate_winner, warnings = select_winners(all_results, tournament_cfg)", new_fixture + "\n    per_symbol_winners, aggregate_winner, warnings = select_winners(all_results, tournament_cfg)")

content = content.replace("assert \"AAPL.ETORO\" not in per_symbol_winners\n    assert \"TSLA.ETORO\" not in per_symbol_winners", "assert \"AAPL.ETORO\" not in per_symbol_winners\n    assert \"TSLA.ETORO\" not in per_symbol_winners\n    assert \"AMZN.ETORO\" not in per_symbol_winners")

with open("automation/tests/test_oos_gate.py", "w") as f:
    f.write(content)
