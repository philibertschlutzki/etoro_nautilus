import re
with open("automation/tests/test_issue_461_reward_no_inversion.py", "r") as f:
    content = f.read()

content = content.replace("oos_total_return: float = 0.0", "oos_total_return: float = 0.0\n    oos_expectancy: float = 0.0")

with open("automation/tests/test_issue_461_reward_no_inversion.py", "w") as f:
    f.write(content)
