"""Issue #623 — kein Config-Key ohne Call-Site (tote Keys = falsches Robustheitsversprechen).

fold_winsorize_lower/upper (0 Referenzen ⇒ jetzt in apply_fold_aggregation via _winsorize verdrahtet),
'--tier refine' (stiller No-Op ⇒ NotImplementedError), holdout_top_k (fehlte ⇒ deklariert, #615).
CI-Check: jeder Top-Level-Key aus tournament.json/optimizer.json hat >= 1 grep-Treffer im Produktionscode.
"""
import json
import re
from pathlib import Path

_SRC = Path("automation")
_PY = [p for p in _SRC.rglob("*.py")
       if "/tests/" not in p.as_posix() and "__pycache__" not in p.as_posix()]
_CODE = "\n".join(p.read_text("utf-8", errors="ignore") for p in _PY)


def _keys(name):
    d = json.loads((_SRC / "config" / name).read_text("utf-8"))
    return [k for k in d if k != "_schema"]


def test_no_dead_config_keys_tournament():
    dead = [k for k in _keys("tournament.json") if not re.search(re.escape(k), _CODE)]
    assert dead == [], f"tote tournament.json-Keys ohne Call-Site: {dead}"


def test_no_dead_config_keys_optimizer():
    dead = [k for k in _keys("optimizer.json") if not re.search(re.escape(k), _CODE)]
    assert dead == [], f"tote optimizer.json-Keys ohne Call-Site: {dead}"


def test_fold_winsorize_and_holdout_top_k_wired():
    assert "fold_winsorize_lower" in _CODE or "_read_fold_winsorize" in _CODE
    assert "holdout_top_k" in _CODE


def test_tier_refine_fails_loud():
    sweep_src = (_SRC / "optimizer" / "sweep.py").read_text("utf-8")
    assert "NotImplementedError" in sweep_src and "refine" in sweep_src
