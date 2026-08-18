"""Issue #994/#1146 (Katalog #1170) — Stop-Risiko-Telemetrie wird nie als ``trial.user_attr``
gestempelt.

Symptom: 14 Report-Felder waren in 28/28 Studies ``None``, obwohl ``backtest_runner`` sie
berechnete und ``parsing.TournamentMetrics`` sie korrekt parste. Root-Cause: ``run_optimization.py``
stempelte den #1126/#1130-Feldblock nicht vollstaendig in ``trial.user_attrs`` —
``report._median_of_trial_field`` liest AUSSCHLIESSLICH dort.

Fix: die fehlenden ``trial.set_user_attr(...)``-Aufrufe wurden ergaenzt (siehe
``run_optimization.py``, unmittelbar neben dem bestehenden #1035/#1097-Feldblock). Dieser
Kontrakt-Test ist der dauerhafte Regressionswaechter (analog
``invariants.check_invariant_registry_wired``, #984/#1138): er iteriert JEDES ``oos_*``-Feld von
``parsing.TournamentMetrics`` und verlangt entweder eine Stempelstelle oder einen begruendeten
Allowlist-Eintrag in ``run_optimization._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS`` — ein neues Feld
ohne beides laesst diesen Test fehlschlagen.

``optuna`` ist in dieser Sandbox installierbar (reines Python, kein ``nautilus_trader``-Bezug) —
``run_optimization.py`` selbst zieht ``nautilus_trader`` nicht ein (nur ``runner.py``, das den
Backtest als Subprozess startet), daher ist dieses Modul hier direkt importierbar.
"""
import ast
from pathlib import Path

import pytest

from automation.optimizer import parsing
from automation.optimizer import run_optimization as ro

_RUN_OPTIMIZATION_PATH = Path(ro.__file__)


def _stamped_trial_user_attr_keys() -> set[str]:
    """Alle String-Literal-Schluessel aus ``trial.set_user_attr("X", ...)``-Aufrufen im Modul,
    per AST extrahiert (robust gegen Codeverschiebung, im Unterschied zu einer Regex)."""
    tree = ast.parse(_RUN_OPTIMIZATION_PATH.read_text("utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "set_user_attr"
                and isinstance(func.value, ast.Name) and func.value.id == "trial"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
    return keys


def _tournament_metrics_oos_fields() -> set[str]:
    return {
        name for name in parsing.TournamentMetrics.__dataclass_fields__
        if name.startswith("oos_")
    }


def test_every_oos_field_is_either_stamped_or_allowlisted():
    stamped = _stamped_trial_user_attr_keys()
    allowlisted = set(ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS)
    fields = _tournament_metrics_oos_fields()

    unaccounted = fields - stamped - allowlisted
    assert not unaccounted, (
        f"Neue(s) TournamentMetrics-Feld(er) ohne trial.set_user_attr-Stempelung UND ohne "
        f"_INTENTIONALLY_UNSTAMPED_METRIC_FIELDS-Begruendung: {sorted(unaccounted)} — "
        "entweder stempeln (run_optimization.py) oder mit Begruendung allowlisten."
    )


def test_allowlist_entries_all_carry_a_non_empty_justification():
    for field, reason in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS.items():
        assert isinstance(reason, str) and reason.strip(), (
            f"Allowlist-Eintrag {field!r} ohne (nicht-leere) Begruendung.")


def test_allowlist_does_not_shadow_a_field_that_is_actually_stamped():
    """Ein Feld, das TATSAECHLICH gestempelt wird, gehoert nicht zusaetzlich auf die
    Allowlist — sonst kann ein spaeterer Entfernungs-Commit unbemerkt eine Regression verstecken."""
    stamped = _stamped_trial_user_attr_keys()
    allowlisted = set(ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS)
    overlap = stamped & allowlisted
    assert not overlap, f"Felder sowohl gestempelt als auch allowlisted: {sorted(overlap)}"


def test_allowlisted_fields_are_all_real_tournament_metrics_fields():
    """Verhindert Karteileichen in der Allowlist (ein umbenanntes/entferntes Feld muss aus der
    Allowlist verschwinden, nicht als toter Eintrag stehen bleiben)."""
    fields = _tournament_metrics_oos_fields()
    stale = set(ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS) - fields
    assert not stale, f"Allowlist-Eintraege ohne entsprechendes TournamentMetrics-Feld: {sorted(stale)}"


@pytest.mark.parametrize("field", [
    "oos_gross_loss_median_bps_trailing_stop",
    "oos_gross_loss_winsorized_mean_bps_trailing_stop",
    "oos_rt_notional_p05",
    "oos_rt_notional_p50",
    "oos_rt_notional_p95",
    "oos_atr_raw_median_bps",
    "oos_stop_exit_fill_lag_bars_median",
    "oos_stop_exit_slippage_bps_median",
    "oos_n_trailing_stop_losses_dust_filtered",
    "oos_dust_round_trips_filtered_count",
])
def test_1146_field_block_is_now_stamped(field):
    """Die zehn im Issue #994/#1146 namentlich genannten Felder — die eigentliche Regression, die
    dieser Fix behebt — muessen JETZT unter den gestempelten Schluesseln erscheinen."""
    assert field in _stamped_trial_user_attr_keys()
