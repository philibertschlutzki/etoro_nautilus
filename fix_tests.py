with open("automation/tests/test_issue_468_penalty_conditioning.py", "r") as f:
    text = f.read()

text = text.replace('"oos_min_trades": 10,', '"oos_min_trades": 10, "oos_min_sortino": 0.3, "oos_min_profit_factor": 1.1,')

with open("automation/tests/test_issue_468_penalty_conditioning.py", "w") as f:
    f.write(text)
