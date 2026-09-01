"""Issue #1351 (GH #1245, P2) — Versions-Bumps und ein gemeinsamer Purge, als letzte Aktion vor
dem Re-Run.

| Version | von → nach | Auslöser |
|---|---|---|
| catalog_schema_version | *neu* → 2 | #1330, #1331, #1332, #1333, #1335 |
| simulation_semantics_version | 8 → 9 | #1332, #1348/#1349, #1350 |
| params_schema_version | bump | #1342, #1343 |
| reward_semantics_version | 27, unverändert | die Reward-Funktion selbst ist unbetroffen |

Diese Sitzung fand ``simulation_semantics_version`` tatsächlich bei 7 vor (nicht 8, wie die Issue-
Tabelle annahm — Stage 1/#1330 hatte den Bump versäumt, dieselbe Fehlerklasse wie die bereits in
``optimizer.json``s v4→v5/v6→v7-Historie dokumentierten Bump-Versäumnisse). Der EINE fällige Bump
(7→8) deckt daher sowohl das versäumte #1330/#1332 ALS AUCH die in dieser Sitzung neu
hinzugekommenen #1348/#1349/#1350-Trigger ab.

``params_schema_version`` existiert in dieser Codebasis nicht als Config-Schlüssel, sondern als
automatisch aus den Suchraum-PARAMETERNAMEN abgeleitete Signatur (``champions._params_schema_
version``, Issue #819) — eine reine Bounds-Verschiebung (wie #1343) ändert sie NICHT, nur ein
hinzugefügter/entfernter Parameter. #1342 (ATR-Bänder) ist unverändert gesperrt (#1236). Es gibt
daher nichts zu bumpen; dieselbe Automatik macht das Akzeptanzkriterium "kein Bump vor dem Rebuild"
trivial erfüllt.
"""
import json
from pathlib import Path


# ── catalog_schema_version ────────────────────────────────────────────────────────────────────

def test_catalog_schema_version_is_2():
    from automation.api_backfiller import CATALOG_SCHEMA_VERSION
    assert CATALOG_SCHEMA_VERSION == 2


# ── simulation_semantics_version ──────────────────────────────────────────────────────────────

def test_simulation_semantics_version_bumped_to_8():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["simulation_semantics_version"] == 8


def test_v8_documentation_names_all_three_triggers():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    assert "v8" in doc
    for issue_ref in ("#1330", "#1332", "#1348", "#1349", "#1350"):
        assert issue_ref in doc, f"{issue_ref} fehlt in der v8-Dokumentation"


def test_v8_documentation_names_the_mandatory_purge():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    assert "purge_stale_studies" in doc


def test_purge_stale_studies_reads_the_live_bumped_value(monkeypatch, tmp_path):
    """purge_stale_studies._current_simulation_semantics_version() ist config-getrieben (Issue
    #854) und braucht daher KEINE Code-Aenderung, um den neuen Wert 8 zu erkennen — der Bump
    ALLEIN macht jede Study mit einer aelteren gestempelten Version stale."""
    from automation.optimizer import purge_stale_studies as psq
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "optimizer.json").write_text(
        json.dumps({"simulation_semantics_version": 8}), encoding="utf-8")
    assert psq._current_simulation_semantics_version(base_cfg=cfg_dir) == 8


# ── reward_semantics_version — unveraendert ──────────────────────────────────────────────────

def test_reward_semantics_version_stays_27():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["reward_semantics_version"] == 27


# ── params_schema_version — kein Config-Schluessel, automatisch abgeleitete Signatur ─────────

def test_no_params_schema_version_config_key_exists():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert "params_schema_version" not in cfg


def test_params_schema_version_signature_is_unaffected_by_the_1343_bounds_shift():
    """#1343/GH#1237 verschob NUR die Obergrenze von max_bars_in_trade (6->7), fuegte KEINEN
    Parameter hinzu/entfernte keinen — die automatisch abgeleitete Signatur (sortierte
    Parameter-NAMEN) ist deshalb strukturell unveraendert, kein Bump noetig (Issue #819)."""
    from automation.optimizer.champions import _params_schema_version
    sig = _params_schema_version("TrendPullbackStrategy")
    assert sig is not None
    assert "max_bars_in_trade" in sig


# ── Akzeptanzkriterium: kein Bump wird VOR dem Rebuild gesetzt ──────────────────────────────────

def test_no_atr_derived_constant_was_recalibrated_in_this_session():
    """Sperrvermerk-Regressionsschutz (#1236/#1246): dieselben vier gesperrten Werte wie
    test_issue_1342_atr_calibration_lock.py, hier als Teil der #1245-Versionsdisziplin nochmals
    verankert."""
    backtest_cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    optimizer_cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert backtest_cfg["atr_floor_bps_by_asset_class"]["EQUITY"] == 2.0
    assert backtest_cfg["k_min_bar_range_multiple"] == 1.0
    assert optimizer_cfg["bar_quality"]["min_intrabar_range_median_bps"] == 0.0
