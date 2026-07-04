import re

with open("automation/tests/test_reward_shaping.py", "r") as f:
    content = f.read()

content = content.replace('"penalty_unevaluable_oos": -2.0', '"penalty_unevaluable_oos": -5.0')
with open("automation/tests/test_reward_shaping.py", "w") as f:
    f.write(content)
