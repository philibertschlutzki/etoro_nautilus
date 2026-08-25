"""Issue #1263 (GH #1133) — eine gefloorte Suchraum-Dimension muss deaktiviert werden, nicht
bezahlt.

Symptom. ``atr_raw_median_bps = 0,0`` (AdxAtr, Dynamic, Hourly); ``atr_floor_binding_trial_
fraction`` 0,768–1,0 in sieben Studies; ``stop_distance_bps_measured = 9,0000 = 3·c_rt`` in acht
Studies. Spearman(``atr_trailing_multiplier``, gemessene Distanz) = 0,2594. Rund 881 von 1742
Trials (50,6 %) tunen eine wirkungslose Dimension.

Root-Cause. ``atr_floor_source = 'cost'`` klemmt die Stopdistanz auf ``min_stop_to_cost_ratio ·
c_rt``. Der Optimizer sampelt ``atr_trailing_multiplier`` weiter über den vollen Bereich, obwohl
das Ergebnis konstant ist.

Scope-Entscheidung (bewusst dokumentiert, siehe ``optimizer.json``-Schema-Dokumentation für
``atr_floor_dimension_freeze_threshold`` und ``invariants.check_atr_floor_dimension_freeze_
candidates``-Docstring). Fix Punkt 1 (die LIVE-Einfrierung von ``atr_trailing_multiplier`` während
einer laufenden Study, ``spaces.sample_params`` liest dafür progressive Study-Telemetrie) und Fix
Punkt 2 (Budget-Umverteilung) sind NICHT implementiert — sie brauchen neue Live-Per-Trial-
Infrastruktur quer durch ``run_optimization.py``s Trial-Schleife UND alle 14+ ``sample_params``-
Aufrufstellen in ``spaces.py``, mit einer "bit-identisch ohne Bindung"-Anforderung, die ohne einen
echten End-to-End-Optimierungslauf (kein ``nautilus_trader`` in dieser Sandbox) nicht verifizierbar
wäre — dasselbe Risikoprofil wie die in Stage 6 (#1139) bewusst zurückgestellte progressive
Symbol-Probe. Fix Punkt 4 (Spearman-Kalibrierungsbeleg für eingefrorene Studies auf INCONCLUSIVE
setzen) ist AN Fix Punkt 1 gekoppelt (nur relevant, wenn tatsächlich eingefroren wird) und daher
ebenfalls zurückgestellt.

Implementiert (Fix Punkt 3, Beobachtbarkeits-Seite von Fix Punkt 1/2).
1. ``optimizer.json['atr_floor_dimension_freeze_threshold']`` (Default 0.60).
2. ``invariants.check_atr_floor_dimension_freeze_candidates`` (severity 'medium') — identifiziert
   retrospektiv, welche Studies (>= 30 Trials, ``atr_floor_binding_trial_fraction >`` Schwelle)
   qualifiziert hätten.
3. ``report._atr_floor_dominant_diagnosed_pairs`` — schliesst die #1244-Lücke für
   ``binding_cause='atr_floor_dominant'`` (``action='none'``, dieselbe report-sichtbare,
   cache-unabhängige Live-Ableitung wie ``_structural_zero_eligible_diagnosed_pairs``).
"""
from automation.optimizer import invariants as inv, report as rpt


def _study_record(strategy, symbol, *, fraction, n_trials=100, budget_fraction=1.0):
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_floor_binding_trial_fraction": fraction,
        "n_trials_completed": n_trials,
        "budget_executed_fraction": budget_fraction,
    }


# ---------------------------------------------------------------------------------------------
# invariants.check_atr_floor_dimension_freeze_candidates
# ---------------------------------------------------------------------------------------------

def test_no_study_records_passes():
    r = inv.check_atr_floor_dimension_freeze_candidates([])
    assert r.passed is True
    assert r.severity == "medium"


def test_reference_symptom_binding_fraction_above_threshold_is_a_candidate():
    records = [_study_record("AdxAtrStrategy", "TSLA.ETORO", fraction=0.768, n_trials=100)]
    r = inv.check_atr_floor_dimension_freeze_candidates(records, freeze_threshold=0.60)
    assert r.passed is False
    assert "AdxAtrStrategy/TSLA.ETORO" in r.actual


def test_binding_fraction_below_threshold_passes():
    records = [_study_record("AdxAtrStrategy", "TSLA.ETORO", fraction=0.30, n_trials=100)]
    r = inv.check_atr_floor_dimension_freeze_candidates(records, freeze_threshold=0.60)
    assert r.passed is True


def test_below_min_trials_excluded_even_with_high_fraction():
    records = [_study_record("AdxAtrStrategy", "TSLA.ETORO", fraction=1.0, n_trials=10)]
    r = inv.check_atr_floor_dimension_freeze_candidates(records, freeze_threshold=0.60, min_trials=30)
    assert r.passed is True


def test_missing_fraction_field_excluded():
    records = [{"strategy": "S", "symbol": "X.ETORO", "n_trials_completed": 100}]
    r = inv.check_atr_floor_dimension_freeze_candidates(records)
    assert r.passed is True


def test_exactly_at_threshold_does_not_qualify():
    # Issue-Text: "atr_floor_binding_trial_fraction > atr_floor_dimension_freeze_threshold" — strikt
    # groesser, ein Wert GLEICH der Schwelle qualifiziert nicht.
    records = [_study_record("S", "X.ETORO", fraction=0.60, n_trials=100)]
    r = inv.check_atr_floor_dimension_freeze_candidates(records, freeze_threshold=0.60)
    assert r.passed is True


def test_seven_studies_reference_count():
    # Akzeptanzkriterium #1263: "mindestens sieben Studies eingefroren" — hier reproduziert als
    # "mindestens sieben Kandidaten identifiziert" (Beobachtbarkeits-Scope dieses Fixes).
    records = [
        _study_record(f"Strat{i}", "TSLA.ETORO", fraction=0.80, n_trials=100) for i in range(7)
    ]
    r = inv.check_atr_floor_dimension_freeze_candidates(records, freeze_threshold=0.60)
    assert r.passed is False
    assert len(r.actual) == 7


# ---------------------------------------------------------------------------------------------
# report._atr_floor_dominant_diagnosed_pairs
# ---------------------------------------------------------------------------------------------

def test_candidate_produces_atr_floor_dominant_entry_with_no_denylist_action():
    records = [_study_record("AdxAtrStrategy", "TSLA.ETORO", fraction=0.90, n_trials=100)]
    out = rpt._atr_floor_dominant_diagnosed_pairs(records, freeze_threshold=0.60, min_trials=30)
    assert len(out) == 1
    assert out[0]["binding_cause"] == "atr_floor_dominant"
    assert out[0]["action"] == "none"
    assert out[0]["strategy"] == "AdxAtrStrategy"
    assert out[0]["symbol"] == "TSLA.ETORO"


def test_non_candidate_produces_no_entry():
    records = [_study_record("AdxAtrStrategy", "TSLA.ETORO", fraction=0.10, n_trials=100)]
    out = rpt._atr_floor_dominant_diagnosed_pairs(records)
    assert out == []


def test_merged_into_diagnosed_pairs_section(monkeypatch):
    monkeypatch.setattr(rpt, "_diagnosed_pairs_all", lambda: [])
    records = [_study_record("AdxAtrStrategy", "TSLA.ETORO", fraction=0.90, n_trials=100)]
    section = rpt._diagnosed_pairs_section(records, atr_floor_dimension_freeze_threshold=0.60)
    keys = {(e["strategy"], e["symbol"]) for e in section}
    assert ("AdxAtrStrategy", "TSLA.ETORO") in keys
    entry = next(e for e in section if e["strategy"] == "AdxAtrStrategy")
    assert entry["binding_cause"] == "atr_floor_dominant"


def test_cache_entry_overrides_live_derivation_for_the_same_pair(monkeypatch):
    # Konvention #1045/#1194 — ein Cache-Eintrag (mehr Historie) ueberschreibt den Live-Befund fuer
    # dasselbe Paar.
    monkeypatch.setattr(rpt, "_diagnosed_pairs_all", lambda: [
        {"strategy": "AdxAtrStrategy", "symbol": "TSLA.ETORO", "action": "denylist",
         "binding_cause": "signal_quality", "n_runs_confirmed": 5, "expires_after_runs": 10,
         "budget_executed_fraction": 1.0},
    ])
    records = [_study_record("AdxAtrStrategy", "TSLA.ETORO", fraction=0.90, n_trials=100)]
    section = rpt._diagnosed_pairs_section(records, atr_floor_dimension_freeze_threshold=0.60)
    entry = next(e for e in section if e["strategy"] == "AdxAtrStrategy")
    assert entry["binding_cause"] == "signal_quality"
    assert entry["source"] == "diagnosis_cache"


# ---------------------------------------------------------------------------------------------
# report.py / invariants.py wiring
# ---------------------------------------------------------------------------------------------

def test_check_is_wired_into_build_report():
    import inspect
    source = inspect.getsource(rpt._build_report)
    assert "check_atr_floor_dimension_freeze_candidates(" in source
    assert 'optimizer_cfg.get("atr_floor_dimension_freeze_threshold"' in source


def test_production_config_default():
    import json
    from pathlib import Path
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg.get("atr_floor_dimension_freeze_threshold") == 0.60
