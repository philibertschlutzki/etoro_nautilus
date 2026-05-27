import sys

with open("automation/backtest_runner.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
inserted_sys_path = False
for line in lines:
    if line.startswith("import sys") and not inserted_sys_path:
        new_lines.append(line)
        new_lines.append("import os\n")
        new_lines.append("from pathlib import Path\n")
        new_lines.append("_AUTOMATION_DIR = Path(__file__).resolve().parent.parent\n")
        new_lines.append("if str(_AUTOMATION_DIR) not in sys.path:\n")
        new_lines.append("    sys.path.insert(0, str(_AUTOMATION_DIR))\n")
        inserted_sys_path = True
    else:
        new_lines.append(line)

with open("automation/backtest_runner.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)
