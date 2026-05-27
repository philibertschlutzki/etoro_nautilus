import re

with open("automation/daily_orchestrator.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add _THIS_DIR to module level constants
this_dir_logic = """
from pathlib import Path
_THIS_DIR = Path(__file__).resolve().parent

# Search .env in automation/ first, then in PROJECT_ROOT (fallback)
ENV_FILE = _THIS_DIR / ".env"
if not ENV_FILE.exists():
    ENV_FILE = _THIS_DIR.parent / ".env"
from dotenv import load_dotenv
load_dotenv(str(ENV_FILE))
"""

if "_THIS_DIR = Path" not in content:
    content = content.replace("ENV_FILE        = PROJECT_ROOT / \".env\"", this_dir_logic)

# if _THIS_DIR is already added via previous patch, let's just make sure it's at the top.
# We'll just define it if not present right near imports
with open("automation/daily_orchestrator.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
has_this_dir = False
for line in lines:
    if line.startswith("_THIS_DIR"):
        has_this_dir = True
    if line.startswith("import sys") and not has_this_dir:
        new_lines.append("import sys\n")
        new_lines.append("from pathlib import Path\n")
        new_lines.append("_THIS_DIR = Path(__file__).resolve().parent\n")
        has_this_dir = True
    else:
        new_lines.append(line)

with open("automation/daily_orchestrator.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)
