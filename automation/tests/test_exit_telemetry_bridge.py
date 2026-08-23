"""Issue #1084/#1232 (Katalog #1247+, P0) — ``f_realized_max`` erreicht den Study-Record nicht; der
blockierende Cap-Check ist tot.

Symptom: ``holdout_f_realized_max`` war in 154/154 Studies ``null``. ``check_sizing_cap_enforcement``
(severity ``blocking``) meldete in 11/11 Laeufen ``passed=True, actual=None`` — der #1209-Deckel
hatte keinen Wirkungsnachweis.

Root-Cause: ``backtest_runner._aggregate_exit_telemetry`` berechnet ``f_realized_max`` in
DERSELBEN Anweisung wie ``f_realized_median`` (aus derselben ``f_realized_values``-Serie).
``parsing.TournamentMetrics`` parst BEIDE korrekt (``oos_f_realized_median``/``oos_f_realized_max``).
Aber ``confirm.py::_metrics_dict`` — die kuratierte Teilmenge, die den promotierten Holdout-Kandidaten
in ``proposal.holdout.global``/``metrics_symbol`` serialisiert — kopierte NUR
``oos_f_realized_median`` in ihr Rueckgabe-Dict; ``oos_f_realized_max`` fehlte trotz identischem
Muster (sechste Instanz der Bruecken-Fehlerklasse aus #953/#1119/#1171/#1172, Pitfall #421 in
AGENTS.md: eine Kette ist vollstaendig gebaut, an EINER Stelle unterbrochen).

Fix:
1. ``confirm.py::_metrics_dict`` kopiert jetzt auch ``oos_f_realized_max``.
2. ``check_sizing_cap_enforcement`` liefert bei fehlender Evidenz ``passed=None``/
   ``evaluable=False`` (INCONCLUSIVE) statt fail-open ``passed=True`` (siehe
   ``test_issue_1060_1209_sizing_cap_enforcement.py``).
3. Dieser generische Bruecken-Test (statt einer siebten Einzelreparatur): fuer jedes ``oos_*``-Feld
   von ``parsing.TournamentMetrics``, das in ``run_optimization._INTENTIONALLY_UNSTAMPED_METRIC_
   FIELDS`` als "holdout-only (confirm.py-Re-Evaluation, ...)" begruendet ist, MUSS der Feldname
   auch tatsaechlich als Schluessel in ``confirm.py::_metrics_dict`` auftauchen — eine Begruendung,
   die eine Bruecke behauptet, ohne dass die Bruecke existiert, ist genau das #1084-Symptom.
"""
import ast
from pathlib import Path

from automation.optimizer import confirm
from automation.optimizer import invariants as inv
from automation.optimizer import parsing
from automation.optimizer import run_optimization as ro

_CONFIRM_PATH = Path(confirm.__file__)


def _metrics_dict_keys() -> set[str]:
    """Alle String-Literal-Schluessel aus dem von ``confirm._metrics_dict`` zurueckgegebenen
    Dict-Literal, per AST extrahiert (robust gegen Codeverschiebung)."""
    tree = ast.parse(_CONFIRM_PATH.read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_metrics_dict":
            keys: set[str] = set()
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Dict)):
                    for k in sub.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            keys.add(k.value)
            return keys
    raise AssertionError("confirm._metrics_dict nicht gefunden — Modul umgebaut?")


def test_oos_f_realized_max_now_reaches_metrics_dict():
    """Regressionstest fuer die konkrete #1084-Bruchstelle."""
    assert "oos_f_realized_max" in _metrics_dict_keys()


def test_every_holdout_only_allowlisted_field_actually_reaches_metrics_dict():
    """Generischer Bruecken-Test (Akzeptanzkriterium 3) — schliesst die GESAMTE Fehlerklasse: jedes
    als 'holdout-only (confirm.py-Re-Evaluation, ...)' begruendete Feld muss den behaupteten Pfad
    tatsaechlich nehmen."""
    metrics_dict_keys = _metrics_dict_keys()
    oos_fields = {
        name for name in parsing.TournamentMetrics.__dataclass_fields__
        if name.startswith("oos_")
    }
    holdout_only_fields = {
        field for field, reason in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS.items()
        if field in oos_fields and "confirm.py-Re-Evaluation" in reason
    }
    assert holdout_only_fields, "Sanity: es sollten holdout-only-Felder existieren."
    missing = holdout_only_fields - metrics_dict_keys
    assert not missing, (
        f"Feld(er), die als 'holdout-only (confirm.py-Re-Evaluation, ...)' begruendet sind, aber "
        f"NICHT in confirm._metrics_dict ankommen (die Bruecke, die die Begruendung behauptet, "
        f"existiert nicht): {sorted(missing)} — #1084/#1232-Fehlerklasse."
    )


def test_check_sizing_cap_enforcement_is_inconclusive_not_passed_true_without_data():
    """Siehe test_issue_1060_1209_sizing_cap_enforcement.py fuer die ausfuehrliche Fassung; hier
    zusaetzlich als Teil der #1084-Regressionsgruppe."""
    result = inv.check_sizing_cap_enforcement([{"strategy": "A", "symbol": "X.ETORO"}])
    assert result.passed is None
    assert result.evaluable is False
