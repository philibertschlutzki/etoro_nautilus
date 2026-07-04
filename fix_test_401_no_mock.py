with open("automation/tests/test_issue_401_flat_reward.py", "r") as f:
    content = f.read()

content = content.replace("stats = _calculate_stats(pnl_list, hold_list=[], starting_capital=10000.0,\n                             min_trades_for_sortino=2)", "import pandas as pd\n    stats = _calculate_stats(pnl_list, hold_list=[], starting_capital=10000.0,\n                             min_trades_for_sortino=2, mtm_series=pd.Series([10000.0, 10010.0, 10000.0, 10018.0, 10010.0], index=pd.date_range(\"2020-01-01\", periods=5)))")

with open("automation/tests/test_issue_401_flat_reward.py", "w") as f:
    f.write(content)
