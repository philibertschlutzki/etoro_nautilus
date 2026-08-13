"""Issue #892 (Katalog #866, #1041) — Champion-/Diagnose-Closed-Loop end-to-end verifiziert.

Root-Cause laut Katalog: der Rückschrieb-Pfad (Diagnose -> ``diagnosed_pairs_cache.json`` ->
nächster Lauf liest sie via ``enumerate_tunable_pairs``) ist SEIT #681/#778/#829 vollständig
implementiert und in ``run_optimization.py`` verdrahtet (``compute_budget_execution`` ->
``recommend_diagnosis_action`` -> ``record_diagnosed_pair``) — der Grund, warum er auf realen
Läufen wirkungslos blieb, war NICHT eine fehlende Verdrahtung, sondern dass seine beiden
entscheidenden Eingaben defekt waren: ``stop_reason`` (Issue #1026 — vorher fälschlich 'EXCEPTION'
für jede unvollständige Study) und ``budget_executed_fraction`` (Issue #1027 — an 3 von 5
Aufrufstellen ungefiltert über die GESAMTE SQLite-Historie statt des aktuellen Laufs berechnet).
Mit beiden jetzt behoben (Kohorte A/B), muss der Closed-Loop end-to-end funktionieren: dieser Test
fährt drei aufeinanderfolgende, simulierte Läufe für dasselbe (Strategie, Symbol)-Paar mit
KORREKT berechneten (``run_id``-gefilterten) Werten und verifiziert, dass (a) das Paar nach
ausreichender Bestätigung tatsächlich als 'denylist' in den Auto-Cache geschrieben wird und (b)
``enumerate_tunable_pairs`` es im darauffolgenden (vierten) Lauf tatsächlich überspringt.
"""
from automation.optimizer import run_optimization as ro
from automation.optimizer import sweep
from automation.optimizer.sweep_diagnostics import (
    recommend_diagnosis_action, record_diagnosed_pair, load_diagnosed_pairs_cache,
)

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


class _FakeTrial:
    """Minimaler Trial-Stub für ``compute_budget_execution``: traegt nur den ``run_id``-Stempel,
    den #1025 (``make_symbol_objective``) auf JEDEM Trial dieses Laufs setzt."""
    def __init__(self, run_id: str):
        self.user_attrs = {"run_id": run_id}


def _simulate_one_run(*, run_id: str, n_trials: int, prior_cache_entry: dict | None,
                      work_dir) -> dict:
    """Reproduziert den run_optimization.py:707-736-Ablauf für EINEN Lauf: korrekt
    run_id-gefilterte ``compute_budget_execution`` (#1023/#1027) -> ``recommend_diagnosis_action``
    -> ``record_diagnosed_pair``. Rückgabe: die geschriebene Empfehlung."""
    # Issue #1023/#1025 — JEDER Trial dieses Laufs traegt den run_id-Stempel (die Study selbst
    # koennte zusaetzlich unGestempelte Trials aus einem VORHERIGEN Lauf enthalten -- hier bewusst
    # NICHT beigemischt, um den Kernpfad isoliert zu pruefen; die Mischkohorten-Robustheit selbst
    # ist bereits durch test_issue_770_budget_execution.py abgedeckt).
    trials = [_FakeTrial(run_id) for _ in range(n_trials)]
    # Issue #1026 — die Study hat KEINE gefangene Exception; das strukturelle Plateau-Flag ist
    # gesetzt (der reale floor_plateau_callback-Pfad, hier direkt simuliert statt durchlaufen).
    study_user_attrs = {"floor_plateau_warned": True, "n_trials_exception": 0}
    budget_execution = ro.compute_budget_execution(
        trials, n_trials_budget=n_trials, n_startup_trials=16,
        study_user_attrs=study_user_attrs, run_id=run_id)
    assert budget_execution["stop_reason"] == "STRUCTURAL_ALL_UNEVALUABLE", (
        "Vorbedingung dieses Tests: #1026 muss STRUCTURAL_ALL_UNEVALUABLE korrekt aus dem "
        "Plateau-Flag ableiten, nicht faelschlich EXCEPTION.")

    diagnosis = {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0}
    n_runs_confirmed = (
        int(prior_cache_entry.get("n_runs_confirmed", 0))
        if prior_cache_entry and prior_cache_entry.get("binding_cause") == diagnosis["binding_cause"]
        else 0
    )
    rec = recommend_diagnosis_action(
        "FlashCrashReversalStrategy", "TSLA.ETORO", diagnosis,
        has_existing_override=False, previously_recommended_override=False,
        proposed_bounds={},
        budget_executed_fraction=budget_execution["budget_executed_fraction"],
        n_runs_confirmed=n_runs_confirmed,
        stop_reason=budget_execution["stop_reason"],
        max_consecutive_structural_runs=2,
    )
    record_diagnosed_pair(rec, work_dir=work_dir, run_id=run_id)
    return rec


def test_structural_all_unevaluable_confirmed_across_runs_escalates_to_denylist(tmp_path):
    (tmp_path / "optimizer.json").write_text("{}", "utf-8")
    cache = {}

    rec1 = _simulate_one_run(run_id="run-1", n_trials=48, prior_cache_entry=None, work_dir=tmp_path)
    assert rec1["action"] == "none"  # n_runs_confirmed=0 -> noch nicht genug Evidenz

    cache = load_diagnosed_pairs_cache(tmp_path)
    entry = cache[("FlashCrashReversalStrategy", "TSLA.ETORO")]
    assert entry["n_runs_confirmed"] == 1

    rec2 = _simulate_one_run(run_id="run-2", n_trials=48, prior_cache_entry=entry, work_dir=tmp_path)
    assert rec2["action"] == "none"  # n_runs_confirmed=1 < max_consecutive_structural_runs=2

    cache = load_diagnosed_pairs_cache(tmp_path)
    entry = cache[("FlashCrashReversalStrategy", "TSLA.ETORO")]
    assert entry["n_runs_confirmed"] == 2

    rec3 = _simulate_one_run(run_id="run-3", n_trials=48, prior_cache_entry=entry, work_dir=tmp_path)
    # Issue #892/#1041 — DAS ist der end-to-end-Nachweis: mit korrekt berechnetem stop_reason
    # (#1026) und run_id-gefiltertem budget_executed_fraction (#1027) schliesst der Closed-Loop
    # tatsaechlich -- das Paar wird nach ausreichender Mehrlauf-Bestaetigung denylisted.
    assert rec3["action"] == "denylist"

    cache = load_diagnosed_pairs_cache(tmp_path)
    assert cache[("FlashCrashReversalStrategy", "TSLA.ETORO")]["action"] == "denylist"


def test_denylisted_pair_is_skipped_by_enumerate_tunable_pairs_on_next_run(monkeypatch, tmp_path):
    """Schliesst die zweite Haelfte des Loops: 'Diagnose -> Datei' (oben) UND 'naechster Lauf liest
    sie' (hier) -- ``enumerate_tunable_pairs`` (sweep.py) MUSS das denylistete Paar tatsaechlich
    ueberspringen, sonst brennt das System weiterhin Trial-Budget auf einem strukturell toten Paar."""
    (tmp_path / "optimizer.json").write_text("{}", "utf-8")
    rec = None
    prior = None
    for run_id in ("run-1", "run-2", "run-3"):
        rec = _simulate_one_run(run_id=run_id, n_trials=48, prior_cache_entry=prior, work_dir=tmp_path)
        prior = load_diagnosed_pairs_cache(tmp_path)[("FlashCrashReversalStrategy", "TSLA.ETORO")]
    assert rec["action"] == "denylist"

    # Der REALE, durch die drei simulierten Laeufe oben geschriebene Cache-Stand (nicht ein
    # handgebautes Fixture) speist jetzt die tatsaechliche enumerate_tunable_pairs-Konsumstelle --
    # das ist der end-to-end-Nachweis "naechster Lauf liest sie".
    real_cache = load_diagnosed_pairs_cache(tmp_path)
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_symbol_strategy_denylist", lambda: {})
    monkeypatch.setattr(sweep, "age_diagnosed_pairs_cache", lambda: real_cache)

    bars = {"TSLA.ETORO": 10_000, "MSFT.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["FlashCrashReversalStrategy"], ["TSLA.ETORO", "MSFT.ETORO"], tier="all",
        available_bars=bars, config=_GATE_CFG)
    assert pairs == [("FlashCrashReversalStrategy", "MSFT.ETORO", "OK")]  # TSLA uebersprungen
