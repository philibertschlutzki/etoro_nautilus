import json
import ast
import os
from pathlib import Path

class TestAutomationIsolation:
    def test_instrument_map_json_valid(self):
        data = json.loads(
            Path("automation/config/instrument_map.json").read_text()
        )
        assert "instruments" in data
        for eid, entry in data["instruments"].items():
            assert "symbol" in entry
            assert "asset_class" in entry
            assert "price_precision" in entry
            assert "size_precision" in entry

    def test_no_external_imports(self):
        automation_dir = Path("automation")
        forbidden_imports = ['adapters', 'config', 'strategies']

        for root, dirs, files in os.walk(automation_dir):
            for file in files:
                if file.endswith('.py'):
                    filepath = Path(root) / file
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()

                    try:
                        tree = ast.parse(content)
                    except SyntaxError:
                        continue # Ignore syntax errors for this test

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                for forbidden in forbidden_imports:
                                    assert not alias.name == forbidden, f"Forbidden import '{alias.name}' found in {filepath}"
                                    assert not alias.name.startswith(forbidden + '.'), f"Forbidden import '{alias.name}' found in {filepath}"
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                for forbidden in forbidden_imports:
                                    assert not node.module == forbidden, f"Forbidden import from '{node.module}' found in {filepath}"
                                    assert not node.module.startswith(forbidden + '.'), f"Forbidden import from '{node.module}' found in {filepath}"
