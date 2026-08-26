"""Issue #1286 (GH #1159, Katalog #1272-1297, P1) — ``run_fingerprint`` ist keine hinreichende
Statistik für das Ergebnis.

Symptom. ``a9d80fba`` und ``f13f29db`` tragen denselben Fingerabdruck und dieselben
``seed_effective`` je Study, unterscheiden sich aber in Trial-Zahlen (Donchian 106/120, FlashCrash
160/141, OpeningRange 106/120, Vwap 100/89) und in 52-104 numerischen Feldern je Study (M-7). Der
Sweep ist unter ``n_jobs=22`` nicht reproduzierbar; ``duplicate_of`` markiert damit eine echte,
unabhängige Stichprobe als redundant.

Fix (dieser Test deckt Fix Punkt 2/3; Fix Punkt 1 — Plateau-/Struktur-Abbruch an trial.number statt
Fertigstellungsreihenfolge binden — betrifft run_optimization.floor_plateau_callback separat).
2. ``report.compute_result_fingerprint`` — sha256 über die sortierte Liste
   ``(strategy, n_trials, best_reward, n_eligible)`` je Study.
3. ``duplicate_of`` nur gesetzt, wenn BEIDE Fingerabdrücke übereinstimmen; bei gleicher
   Eingangsmenge und abweichendem Ergebnis stattdessen ``nondeterministic_repeat_of`` mit eigener
   Invariante ``check_run_determinism`` (severity 'high').
"""
import pytest

from automation.optimizer import invariants as inv, report as rpt


# ---------------------------------------------------------------------------------------------
# report.compute_result_fingerprint
# ---------------------------------------------------------------------------------------------

def _summaries(**overrides):
    base = [
        {"strategy": "DonchianStrategy", "symbol": "TSLA.ETORO", "n_trials": 106,
         "best_reward": 1.5, "n_eligible": 3},
        {"strategy": "FlashCrashStrategy", "symbol": "NVDA.ETORO", "n_trials": 160,
         "best_reward": 0.9, "n_eligible": 1},
    ]
    return overrides.get("summaries", base)


def test_deterministic_for_identical_inputs():
    a = rpt.compute_result_fingerprint(_summaries())
    b = rpt.compute_result_fingerprint(_summaries())
    assert a == b


def test_iteration_order_does_not_matter():
    s = _summaries()
    a = rpt.compute_result_fingerprint(s)
    b = rpt.compute_result_fingerprint(list(reversed(s)))
    assert a == b


def test_differs_on_n_trials_change():
    """Kernfall #1286: derselbe run_fingerprint, aber Donchian 106 statt 120 Trials -> anderer
    result_fingerprint."""
    a = rpt.compute_result_fingerprint(_summaries())
    changed = _summaries()
    changed[0] = {**changed[0], "n_trials": 120}
    b = rpt.compute_result_fingerprint(changed)
    assert a != b


def test_differs_on_best_reward_change():
    a = rpt.compute_result_fingerprint(_summaries())
    changed = _summaries()
    changed[0] = {**changed[0], "best_reward": 1.6}
    b = rpt.compute_result_fingerprint(changed)
    assert a != b


def test_differs_on_n_eligible_change():
    a = rpt.compute_result_fingerprint(_summaries())
    changed = _summaries()
    changed[0] = {**changed[0], "n_eligible": 4}
    b = rpt.compute_result_fingerprint(changed)
    assert a != b


def test_sha256_hexdigest_shape():
    fp = rpt.compute_result_fingerprint(_summaries())
    assert isinstance(fp, str) and len(fp) == 64
    int(fp, 16)  # valid hex


def test_empty_list_is_deterministic():
    assert rpt.compute_result_fingerprint([]) == rpt.compute_result_fingerprint([])


# ---------------------------------------------------------------------------------------------
# invariants.check_run_determinism
# ---------------------------------------------------------------------------------------------

def _entry(*, run_id, fingerprint="fp1", result_fingerprint=None, study_summaries=None):
    return {"fingerprint": fingerprint, "run_id": run_id, "started_at_utc": "2026-01-01T00:00:00Z",
           "result_fingerprint": result_fingerprint, "study_summaries": study_summaries or []}


def test_no_fingerprint_is_inconclusive():
    r = inv.check_run_determinism(None, "rf1", "run2", [])
    assert r.passed is True
    assert r.inconclusive is True


def test_no_run_id_is_inconclusive():
    r = inv.check_run_determinism("fp1", "rf1", None, [])
    assert r.passed is True
    assert r.inconclusive is True


def test_no_prior_match_is_inconclusive():
    r = inv.check_run_determinism("fp1", "rf1", "run2", [_entry(run_id="run1", fingerprint="other")])
    assert r.passed is True
    assert r.inconclusive is True


def test_prior_match_without_result_fingerprint_is_inconclusive():
    """Legacy-Eintrag vor #1286 (kein result_fingerprint) -- nicht auswertbar, kein erfundenes
    Urteil."""
    r = inv.check_run_determinism(
        "fp1", "rf1", "run2", [_entry(run_id="run1", fingerprint="fp1", result_fingerprint=None)])
    assert r.passed is True
    assert r.inconclusive is True


def test_matching_run_and_result_fingerprint_passes():
    r = inv.check_run_determinism(
        "fp1", "rf1", "run2",
        [_entry(run_id="run1", fingerprint="fp1", result_fingerprint="rf1")])
    assert r.passed is True


def test_reference_symptom_same_run_fingerprint_different_result_fingerprint_fails_high():
    """Kernreproduktion: a9d80fba/f13f29db -- derselbe run_fingerprint, verschiedener
    result_fingerprint."""
    r = inv.check_run_determinism(
        "fp_shared", "rf_f13f29db", "f13f29db",
        [_entry(run_id="a9d80fba", fingerprint="fp_shared", result_fingerprint="rf_a9d80fba")])
    assert r.passed is False
    assert r.severity == "high"
    assert r.actual["prior_run_id"] == "a9d80fba"


def test_names_the_diverging_studies():
    prior_summaries = [
        {"strategy": "DonchianStrategy", "symbol": "TSLA.ETORO", "n_trials": 106, "best_reward": 1.5, "n_eligible": 3},
        {"strategy": "VwapExhaustionStrategy", "symbol": "AAPL.ETORO", "n_trials": 100, "best_reward": 0.2, "n_eligible": 0},
    ]
    current_summaries = [
        {"strategy": "DonchianStrategy", "symbol": "TSLA.ETORO", "n_trials": 120, "best_reward": 1.5, "n_eligible": 3},
        {"strategy": "VwapExhaustionStrategy", "symbol": "AAPL.ETORO", "n_trials": 100, "best_reward": 0.2, "n_eligible": 0},
    ]
    r = inv.check_run_determinism(
        "fp_shared", "rf_current", "run2",
        [_entry(run_id="run1", fingerprint="fp_shared", result_fingerprint="rf_prior",
               study_summaries=prior_summaries)],
        current_study_summaries=current_summaries,
    )
    assert r.passed is False
    assert r.actual["diverging_study_pairs"] == ["DonchianStrategy/TSLA.ETORO"]
    assert "VwapExhaustionStrategy/AAPL.ETORO" not in r.actual["diverging_study_pairs"]


def test_without_current_study_summaries_still_fails_but_names_nothing():
    r = inv.check_run_determinism(
        "fp_shared", "rf_current", "run2",
        [_entry(run_id="run1", fingerprint="fp_shared", result_fingerprint="rf_prior")])
    assert r.passed is False
    assert r.actual["diverging_study_pairs"] is None


# ---------------------------------------------------------------------------------------------
# report._build_report — duplicate_of vs. nondeterministic_repeat_of
# ---------------------------------------------------------------------------------------------

def test_wired_in_build_report():
    import inspect
    source = inspect.getsource(rpt._build_report)
    assert "check_run_determinism" in source
    assert "nondeterministic_repeat_of" in source


def test_check_run_determinism_appears_in_stream(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1286-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_run_determinism" in names
    assert "result_fingerprint" in report
    assert report.get("nondeterministic_repeat_of") is None
