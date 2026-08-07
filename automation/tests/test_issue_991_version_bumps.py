"""Issue #991 (Katalog E, P0, GitHub-Issue #789) — Semantik-Bumps nach Katalog #965-#984
(GitHub-Issues #785/#787/#788): reward_semantics_version 22->23 (Ausloeser #977/#966), neu
inference_semantics_version = 1 (#968). Pitfall #299 verlangt, dass die _schema-Beschreibung
ausschliesslich implementiertes Verhalten nennt und gegen den Code getestet wird, nicht gegen die
Absicht (siehe test_issue_961_version_bumps.py fuer den Vorlaufer-Katalog).
"""
import json

from automation.optimizer.trial_config import config_dir


def _load_optimizer_cfg() -> dict:
    with open(config_dir() / "optimizer.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_inference_semantics_version_is_1():
    cfg = _load_optimizer_cfg()
    assert cfg["inference_semantics_version"] == 1


def test_reward_schema_v23_names_its_actual_triggers():
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["reward_semantics_version"]
    v23_segment = doc[doc.index("v23 ="):]
    for ref in ("#977", "#966"):
        assert ref in v23_segment, f"reward_semantics_version v23-Changelog muss {ref} nennen"


def test_reward_schema_v23_excludes_non_trigger_issues():
    """#965/#967/#968/#970/#976/#978/#979/#980/#981 sind explizit KEIN Bump-Ausloeser fuer die
    REWARD-Semantik (reine Invarianten-/Telemetrie-Ebene ohne veraenderten Reward-Wert eines
    bereits abgeschlossenen Trials) -- der v23-Abschnitt muss das auch so dokumentieren."""
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["reward_semantics_version"]
    v23_segment = doc[doc.index("v23 ="):]
    assert "NICHT Ausloeser" in v23_segment
    for ref in ("#965", "#967", "#968", "#970", "#976", "#978", "#979", "#980", "#981"):
        assert ref in v23_segment


def test_penalty_dd_weight_is_actually_zero():
    """Verifiziert die reward_semantics_version-v23-Behauptung gegen den tatsaechlichen Config-Wert
    (nicht nur die Absicht) -- direkter Konsument von _CONFIGURED_INACTIVE_REWARD_TERMS."""
    from automation.optimizer.invariants import _CONFIGURED_INACTIVE_REWARD_TERMS
    cfg = _load_optimizer_cfg()
    assert cfg["penalty_dd_weight"] == 0.0
    assert "dd_penalty" in _CONFIGURED_INACTIVE_REWARD_TERMS


def test_oos_expectancy_sentinel_actually_removed():
    """Verifiziert die #966-Behauptung gegen den tatsaechlichen Parsing-Code."""
    from automation.optimizer.parsing import parse_tournament
    import inspect
    src = inspect.getsource(parse_tournament)
    assert "oos_expectancy=float(oos_expectancy) if oos_expectancy is not None else None" in src


def test_inference_semantics_check_function_exists_and_is_wired():
    """Verifiziert, dass inference_semantics_version nicht nur deklariert, sondern (analog
    reward_semantics_version/simulation_semantics_version) tatsaechlich geprueft/gestempelt wird."""
    import inspect
    from automation.optimizer import run_optimization as ro
    assert hasattr(ro, "_check_inference_semantics_version")
    full_src = inspect.getsource(ro)
    assert full_src.count("_check_inference_semantics_version(study, opt_data)") == 2
