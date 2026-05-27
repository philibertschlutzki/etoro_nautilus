import re

with open("automation/daily_orchestrator.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add _THIS_DIR logic if missing
this_dir_logic = """
# Search .env in automation/ first, then in PROJECT_ROOT (fallback)
_THIS_DIR = Path(__file__).resolve().parent
ENV_FILE = _THIS_DIR / ".env"
if not ENV_FILE.exists():
    ENV_FILE = _THIS_DIR.parent / ".env"
load_dotenv(str(ENV_FILE))
"""

if "_THIS_DIR = Path" not in content:
    content = content.replace("ENV_FILE        = PROJECT_ROOT / \".env\"\nload_dotenv(str(ENV_FILE))", this_dir_logic)

with open("automation/daily_orchestrator.py", "w", encoding="utf-8") as f:
    f.write(content)
