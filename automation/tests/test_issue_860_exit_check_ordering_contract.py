"""Issue #860 (P1, Katalog #856-#861, GitHub-Issue #758) — `AdxAtrMomentumStrategy.on_bar` rief
den Exit-Check nach dem Indikator-Guard auf; 13 von 14 handelnden Strategien tun es davor.

Der Vertrag "`_check_exits_and_update` ist die ERSTE Anweisung nach dem Indikator-Update" ist im
Docstring von `HourlyStrategyBase` beschrieben, war aber nirgends erzwungen (Formgleich mit
Pitfall #262 — ein Vorbedingungs-Check am Funktionsanfang sperrt alles, was die Funktion tut).
`AdxAtrMomentumStrategy.on_bar` rief `_check_exits_and_update` VOR diesem Fix nirgends auf — der
Indikator-Guard (`if not (self.adx.initialized and ...): return`) war der erste `return` im
Funktionskörper, ohne dass der Exit-Pfad je zum Zug kam. Derzeit folgenlos, weil AdxAtr auf diesem
Universum 0 Positionen eröffnet (#870) — sobald das behoben ist, wird der Defekt aktiv: während
einer Indikator-Warmup-Phase (z. B. nach einem Fold-Wechsel oder Reconnect mit rehydrierter
Position, #717/GR-04) liefe weder der Bar-Zähler noch der Trailing-Stop noch der Zeit-Exit.

Dieser Test iteriert über `strategies.json` (nicht über eine gepflegte Liste) — dieselbe
Fehlerklasse wie das eingefrorene `WIRED_OVERRIDE_STRATEGIES`-Tupel (#831): eine 16. Strategie wird
automatisch mitgeprüft, ohne den Test anzufassen.
"""
import ast
import importlib
import inspect
import json
import textwrap
from pathlib import Path

CONFIG_PATH = Path("automation/config/strategies.json")


def _strategy_classes() -> list[tuple[str, type]]:
    data = json.loads(CONFIG_PATH.read_text("utf-8"))
    classes = []
    for entry in data["strategies"]:
        module = importlib.import_module(entry["strategy_module"])
        cls = getattr(module, entry["strategy_class"])
        classes.append((entry["strategy_class"], cls))
    return classes


def _first_return_lineno(tree: ast.AST) -> int | None:
    linenos = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Return)]
    return min(linenos) if linenos else None


def _first_check_exits_call_lineno(tree: ast.AST) -> int | None:
    linenos = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None)
        if name == "_check_exits_and_update":
            linenos.append(n.lineno)
    return min(linenos) if linenos else None


def _on_bar_ast(cls: type) -> ast.AST:
    source = textwrap.dedent(inspect.getsource(cls.on_bar))
    return ast.parse(source)


def test_every_active_strategy_module_is_discovered_from_strategies_json():
    """Sanity-Beleg, dass die Enumeration tatsächlich etwas findet (kein leerer, vakuant grüner
    Test) — 15 Strategien, konsistent mit dem restlichen Katalog (#831/#870)."""
    classes = _strategy_classes()
    names = {name for name, _ in classes}
    assert "AdxAtrMomentumStrategy" in names
    assert len(classes) == 15


def test_check_exits_and_update_precedes_the_first_return_in_every_strategy():
    """AST-Vertragstest: in JEDER Strategie aus strategies.json darf der erste `return` in
    `on_bar` nicht VOR dem ersten Aufruf von `_check_exits_and_update` liegen. Ein Aufruf, der auf
    DERSELBEN Zeile wie sein bewachender `return` steht (`if self._check_exits_and_update(bar):
    return`), zählt als "davor" (<=), nicht als Verstoss."""
    offenders = {}
    for name, cls in _strategy_classes():
        tree = _on_bar_ast(cls)
        check_lineno = _first_check_exits_call_lineno(tree)
        return_lineno = _first_return_lineno(tree)
        if check_lineno is None:
            offenders[name] = "on_bar ruft _check_exits_and_update nirgends auf"
        elif return_lineno is not None and check_lineno > return_lineno:
            offenders[name] = (
                f"erster return (Zeile {return_lineno}) liegt vor dem ersten "
                f"_check_exits_and_update-Aufruf (Zeile {check_lineno})")
    assert offenders == {}


def test_adx_atr_momentum_specifically_calls_check_exits_first():
    """Regressionswächter für den konkreten #860-Fall: schlägt auf dem Pre-Fix-HEAD fehl (vorher
    kein einziger Aufruf von _check_exits_and_update in dieser Datei)."""
    from automation.strategies.adx_atr_momentum import AdxAtrMomentumStrategy

    tree = _on_bar_ast(AdxAtrMomentumStrategy)
    check_lineno = _first_check_exits_call_lineno(tree)
    return_lineno = _first_return_lineno(tree)
    assert check_lineno is not None
    assert return_lineno is not None
    assert check_lineno <= return_lineno
