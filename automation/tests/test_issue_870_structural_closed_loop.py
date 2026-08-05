"""Issue #870 (P1, Katalog #870-#875, GitHub-Issue #761) — der #829-Deadlock besteht im siebten
Vollauf in Folge; 3300 Trials je Lauf ohne jede Information.

Symptom: 67 Floor-Plateau-Warnungen (AdxAtrMomentum/TrendPullback), aber im gesamten Log KEIN
einziges SEARCH_SPACE_AUTO_OVERRIDE-Event und ``denylist.pairs = []``/``overrides = {}`` bleiben
leer — der #829-Fix (stop_reason statt budget_executed_fraction als Eskalations-Evidenz) ist im
Code vorhanden, aber die Closed-Loop-Diagnose-Schreibrücke (``recommend_diagnosis_action``/
``record_diagnosed_pair`` in ``run_optimization.floor_plateau_callback``) läuft hinter einem
``except Exception: logger.debug(...)`` — derselben Fehlerklasse (Pitfall #270), die #856 an der
Fail-Fast-Probe bereits behoben hat: ein wiederholt fehlschlagender Wächter bleibt unsichtbar.

Fix:
1. Zwei aufeinanderfolgende Fehlschläge der Diagnose-Schreibrücke eskalieren auf ein strukturiertes
   ``DIAGNOSIS_WRITEBACK_BROKEN``-ERROR-Event (``run_optimization._record_diagnosis_writeback_outcome``),
   statt für immer bei ``logger.debug`` zu bleiben.
2. Neuer, VOM Closed-Loop-Evidenzregime UNABHÄNGIGER harter Deckel: ``tournament.json[
   'max_consecutive_structural_runs']`` (Default 2). Ein Paar, das zwei Läufe IN FOLGE mit
   ``stop_reason == 'STRUCTURAL_ALL_UNEVALUABLE'`` endet, wird im dritten Lauf nicht mehr enumeriert
   (``sweep_diagnostics.is_pair_structurally_suspended`` + ``sweep.enumerate_tunable_pairs``) —
   unabhängig davon, ob ``recommend_diagnosis_action``s eigene (strukturell einen Lauf langsamere)
   Eskalation bereits selbst auf 'denylist' geschaltet hat.
3. ``report._structural_suspension_summary`` weist die ausgesetzten Paare + die geschätzte
   Trial-Ersparnis im #742-Report aus (``pairs_suspended``/``trials_saved``).
"""
import json

from automation.optimizer import sweep
from automation.optimizer import sweep_diagnostics as sd
from automation.optimizer import run_optimization as ro

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


# ── sweep_diagnostics.record_diagnosed_pair: consecutive_structural_runs-Zaehlung ────────────────
def test_consecutive_structural_runs_increments_across_repeated_stop_reason(tmp_path):
    rec = {"strategy": "AdxAtrMomentumStrategy", "symbol": "XOM.ETORO", "action": "none",
           "binding_cause": "signal_absent", "stop_reason": "STRUCTURAL_ALL_UNEVALUABLE"}
    sd.record_diagnosed_pair(rec, work_dir=tmp_path)
    sd.record_diagnosed_pair(rec, work_dir=tmp_path)
    entry = sd.load_diagnosed_pairs_cache(tmp_path)[("AdxAtrMomentumStrategy", "XOM.ETORO")]
    assert entry["consecutive_structural_runs"] == 2


def test_consecutive_structural_runs_resets_on_non_structural_stop_reason(tmp_path):
    rec_structural = {"strategy": "TrendPullbackStrategy", "symbol": "AAPL.ETORO", "action": "none",
                      "binding_cause": "signal_sparse", "stop_reason": "STRUCTURAL_ALL_UNEVALUABLE"}
    rec_other = {"strategy": "TrendPullbackStrategy", "symbol": "AAPL.ETORO", "action": "none",
                "binding_cause": "signal_sparse", "stop_reason": None}
    sd.record_diagnosed_pair(rec_structural, work_dir=tmp_path)
    sd.record_diagnosed_pair(rec_structural, work_dir=tmp_path)
    sd.record_diagnosed_pair(rec_other, work_dir=tmp_path)
    entry = sd.load_diagnosed_pairs_cache(tmp_path)[("TrendPullbackStrategy", "AAPL.ETORO")]
    assert entry["consecutive_structural_runs"] == 0


def test_consecutive_structural_runs_independent_of_action_binding_cause_changes(tmp_path):
    """AK — die Zaehlung ist UNABHAENGIG von recommend_diagnosis_action's eigener (langsamerer)
    n_runs_confirmed-Eskalation: selbst wenn 'action' sich zwischen Laeufen aendert (z. B. 'none' ->
    'denylist'), bleibt consecutive_structural_runs am rohen stop_reason orientiert."""
    rec_1 = {"strategy": "AdxAtrMomentumStrategy", "symbol": "XOM.ETORO", "action": "none",
             "binding_cause": "signal_absent", "stop_reason": "STRUCTURAL_ALL_UNEVALUABLE"}
    rec_2 = {"strategy": "AdxAtrMomentumStrategy", "symbol": "XOM.ETORO", "action": "denylist",
             "binding_cause": "signal_absent", "stop_reason": "STRUCTURAL_ALL_UNEVALUABLE"}
    sd.record_diagnosed_pair(rec_1, work_dir=tmp_path)
    sd.record_diagnosed_pair(rec_2, work_dir=tmp_path)
    entry = sd.load_diagnosed_pairs_cache(tmp_path)[("AdxAtrMomentumStrategy", "XOM.ETORO")]
    assert entry["consecutive_structural_runs"] == 2


# ── is_pair_structurally_suspended / read_max_consecutive_structural_runs ────────────────────────
def test_is_pair_structurally_suspended_threshold():
    assert sd.is_pair_structurally_suspended(
        {"consecutive_structural_runs": 2}, max_consecutive_structural_runs=2) is True
    assert sd.is_pair_structurally_suspended(
        {"consecutive_structural_runs": 1}, max_consecutive_structural_runs=2) is False
    assert sd.is_pair_structurally_suspended({}) is False


def test_read_max_consecutive_structural_runs_default(tmp_path):
    assert sd.read_max_consecutive_structural_runs(tmp_path) == 2


def test_read_max_consecutive_structural_runs_honors_config(tmp_path):
    (tmp_path / "tournament.json").write_text(
        json.dumps({"max_consecutive_structural_runs": 5}), "utf-8")
    assert sd.read_max_consecutive_structural_runs(tmp_path) == 5


# ── enumerate_tunable_pairs: der harte Deckel greift AUCH ohne 'denylist'-Action ─────────────────
def test_enumerate_tunable_pairs_skips_structurally_suspended_pair(monkeypatch):
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_symbol_strategy_denylist", lambda: {})
    monkeypatch.setattr(sweep, "read_max_consecutive_structural_runs", lambda: 2)
    monkeypatch.setattr(sweep, "age_diagnosed_pairs_cache", lambda: {
        ("AdxAtrMomentumStrategy", "XOM.ETORO"): {
            "strategy": "AdxAtrMomentumStrategy", "symbol": "XOM.ETORO",
            "action": "none", "binding_cause": "signal_absent",
            "consecutive_structural_runs": 2, "runs_since_recorded": 0,
            "expires_after_runs": 10,
        },
    })
    bars = {"XOM.ETORO": 10_000, "AAPL.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["AdxAtrMomentumStrategy"], ["XOM.ETORO", "AAPL.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("AdxAtrMomentumStrategy", "AAPL.ETORO", "OK")]


def test_enumerate_tunable_pairs_below_threshold_is_not_suspended(monkeypatch):
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_symbol_strategy_denylist", lambda: {})
    monkeypatch.setattr(sweep, "read_max_consecutive_structural_runs", lambda: 2)
    monkeypatch.setattr(sweep, "age_diagnosed_pairs_cache", lambda: {
        ("AdxAtrMomentumStrategy", "XOM.ETORO"): {
            "strategy": "AdxAtrMomentumStrategy", "symbol": "XOM.ETORO",
            "action": "none", "binding_cause": "signal_absent",
            "consecutive_structural_runs": 1, "runs_since_recorded": 0,
            "expires_after_runs": 10,
        },
    })
    bars = {"XOM.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["AdxAtrMomentumStrategy"], ["XOM.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("AdxAtrMomentumStrategy", "XOM.ETORO", "OK")]


def test_enumerate_tunable_pairs_retests_expired_suspension(monkeypatch):
    """AK — dieselbe expires_after_runs-Alterung wie beim 'denylist'-Zweig gilt auch fuer die
    strukturelle Aussetzung (kein permanenter Ausschluss)."""
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_symbol_strategy_denylist", lambda: {})
    monkeypatch.setattr(sweep, "read_max_consecutive_structural_runs", lambda: 2)
    monkeypatch.setattr(sweep, "age_diagnosed_pairs_cache", lambda: {
        ("AdxAtrMomentumStrategy", "XOM.ETORO"): {
            "strategy": "AdxAtrMomentumStrategy", "symbol": "XOM.ETORO",
            "action": "none", "binding_cause": "signal_absent",
            "consecutive_structural_runs": 2, "runs_since_recorded": 10,
            "expires_after_runs": 10,
        },
    })
    bars = {"XOM.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["AdxAtrMomentumStrategy"], ["XOM.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("AdxAtrMomentumStrategy", "XOM.ETORO", "OK")]


def test_denylist_action_takes_precedence_over_structural_suspension_check(monkeypatch):
    """Regressionswaechter — ein bereits 'denylist'-empfohlenes Paar wird ueber den BESTEHENDEN Pfad
    uebersprungen; der neue Deckel darf diesen Pfad nicht doppelt/anders behandeln."""
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_symbol_strategy_denylist", lambda: {})
    monkeypatch.setattr(sweep, "read_max_consecutive_structural_runs", lambda: 2)
    monkeypatch.setattr(sweep, "age_diagnosed_pairs_cache", lambda: {
        ("AdxAtrMomentumStrategy", "XOM.ETORO"): {
            "strategy": "AdxAtrMomentumStrategy", "symbol": "XOM.ETORO",
            "action": "denylist", "binding_cause": "signal_absent",
            "consecutive_structural_runs": 5, "runs_since_recorded": 0,
            "expires_after_runs": 10,
        },
    })
    bars = {"XOM.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["AdxAtrMomentumStrategy"], ["XOM.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == []


# ── run_optimization: DIAGNOSIS_WRITEBACK_BROKEN-Eskalation (Pitfall #270, analog #856) ──────────
def test_diagnosis_writeback_escalates_after_two_consecutive_failures(monkeypatch, caplog):
    ro._diagnosis_writeback_errors = 0
    events = []
    monkeypatch.setattr(ro, "emit_execution_event", lambda *a, **k: events.append((a, k)))
    ro._record_diagnosis_writeback_outcome(False)
    assert not events
    ro._record_diagnosis_writeback_outcome(False)
    assert len(events) == 1
    assert events[0][0][1] == "DIAGNOSIS_WRITEBACK_BROKEN"
    assert events[0][0][2]["consecutive_errors"] == 2


def test_diagnosis_writeback_success_resets_the_counter(monkeypatch):
    ro._diagnosis_writeback_errors = 0
    events = []
    monkeypatch.setattr(ro, "emit_execution_event", lambda *a, **k: events.append((a, k)))
    ro._record_diagnosis_writeback_outcome(False)
    ro._record_diagnosis_writeback_outcome(True)
    ro._record_diagnosis_writeback_outcome(False)
    assert not events
    assert ro._diagnosis_writeback_errors == 1


# ── report._structural_suspension_summary ─────────────────────────────────────────────────────────
def test_structural_suspension_summary_lists_suspended_pairs_and_estimates_trials_saved(
        tmp_path, monkeypatch):
    from automation.optimizer import report as rpt

    (tmp_path / "diagnostics").mkdir(parents=True, exist_ok=True)
    (tmp_path / "diagnostics" / "diagnosed_pairs_cache.json").write_text(json.dumps({
        "pairs": [
            {"strategy": "AdxAtrMomentumStrategy", "symbol": "XOM.ETORO",
             "action": "none", "binding_cause": "signal_absent",
             "consecutive_structural_runs": 2},
            {"strategy": "TrendPullbackStrategy", "symbol": "AAPL.ETORO",
             "action": "none", "binding_cause": "signal_sparse",
             "consecutive_structural_runs": 0},
        ],
    }), "utf-8")
    monkeypatch.setattr(rpt, "config_dir", lambda: tmp_path)

    import automation.optimizer.manifest as _manifest
    monkeypatch.setattr(_manifest, "WORK", tmp_path)

    summary = rpt._structural_suspension_summary({"n_trials": 100})
    assert summary["max_consecutive_structural_runs"] == 2
    assert len(summary["pairs_suspended"]) == 1
    assert summary["pairs_suspended"][0]["strategy"] == "AdxAtrMomentumStrategy"
    assert summary["trials_saved"] > 0


def test_structural_suspension_summary_fail_open_on_missing_cache(tmp_path, monkeypatch):
    from automation.optimizer import report as rpt
    monkeypatch.setattr(rpt, "config_dir", lambda: tmp_path)
    import automation.optimizer.manifest as _manifest
    monkeypatch.setattr(_manifest, "WORK", tmp_path)
    summary = rpt._structural_suspension_summary({})
    assert summary["pairs_suspended"] == []
    assert summary["trials_saved"] == 0
