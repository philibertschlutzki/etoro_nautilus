import re

with open('automation/fractional_trading.py', 'r') as f:
    content = f.read()

# Replace comment in docstring referencing safe_compute_quantity
content = content.replace(
    '    4. make_qty-Sicherheitswrapper (pre-check + try/except).',
    ''
)

# Also remove the note in AGENTS.md that says fractional_trading.py has safe_compute_quantity
with open('automation/AGENTS.md', 'r') as f:
    agents_content = f.read()

agents_content = agents_content.replace(
    '├── fractional_trading.py       # By-Amount-USD-Orders + safe_compute_quantity (Pitfall-#14-Utilities)',
    '├── fractional_trading.py       # By-Amount-USD-Orders (Pitfall-#14-Utilities)'
)

# And remove it from the docs section mentioning it
agents_content = agents_content.replace(
    '`safe_compute_quantity()` (zweistufig: Pre-Check `units < size_increment` + `try/except ValueError`), ',
    ''
)

with open('automation/fractional_trading.py', 'w') as f:
    f.write(content)

with open('automation/AGENTS.md', 'w') as f:
    f.write(agents_content)
