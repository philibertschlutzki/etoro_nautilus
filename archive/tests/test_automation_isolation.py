import json
import ast
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

    def test_no_archive_imports(self):
        """Prüft, dass keine Datei in automation/ aus archive/, adapters/, config/ oder strategies/ (Root) importiert."""
        automation_dir = Path("automation")
        disallowed_prefixes = ("archive", "adapters", "config", "strategies")

        for py_file in automation_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for prefix in disallowed_prefixes:
                            if alias.name == prefix or alias.name.startswith(f"{prefix}."):
                                assert False, f"{py_file} contains disallowed import: {alias.name}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        for prefix in disallowed_prefixes:
                            if node.module == prefix or node.module.startswith(f"{prefix}."):
                                assert False, f"{py_file} contains disallowed import from: {node.module}"
