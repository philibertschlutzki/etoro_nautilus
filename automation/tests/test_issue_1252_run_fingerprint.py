"""Issue #1252 (GH #1122) — Lauf-Fingerabdruck + Duplikat-Erkennung + Suchvarianz.

Symptom. Drei aufeinanderfolgende Sweeps lieferten 208 von 218 Study-Feldern bit-identisch, ohne
dass ein Artefakt das ausgewiesen hätte — drei Reports lasen sich wie drei unabhängige Belege.

Root-Cause. Kein Report trug einen Fingerabdruck der EINGANGSMENGE (Commit, Config-Hashes,
Katalog-Stand, Seed, Symbol-/Strategie-Universum, Reward-/Simulations-Semantik) — ein
Wiederholungslauf ohne jede Änderung war aus keinem einzelnen Report als solcher erkennbar.

Fix.
1. ``report.compute_run_fingerprint`` — sha256 über die zehn Eingangskomponenten (inkl. optionalem
   ``seed_salt``, siehe #1253/GH #1123), Record-Separator-getrennt, Symbole/Strategien sortiert.
2. ``manifest.RUN_FINGERPRINT_INDEX_PATH`` (PROJECT_ROOT-verankert, siehe #1270/GH #1140) +
   ``manifest.append_jsonl_atomic``/``read_jsonl`` — ein wachsender, über WORK-Recycling hinweg
   persistenter Index.
3. ``invariants.check_run_is_not_duplicate`` (severity 'high') — FAIL, wenn ``run_fingerprint``
   bereits unter einer ANDEREN ``run_id`` im Index steht.
4. ``report._compute_search_variance`` (Fix Punkt 3 aus #1253/GH #1123) — Median/IQR/Spannweite von
   best_reward/best_eligible_reward/n_eligible je (Strategie, Symbol) über >= 3 Läufe derselben
   ``fingerprint_base``-Familie.
"""
import json

from automation.optimizer import invariants as inv, manifest, report as rpt, summary_de


# ---------------------------------------------------------------------------------------------
# report.compute_run_fingerprint
# ---------------------------------------------------------------------------------------------

def _kwargs(**overrides):
    base = dict(
        git_commit_simulation="abc123",
        tournament_config_sha256="sha_t",
        optimizer_config_sha256="sha_o",
        catalog_fingerprint_value="cat_fp",
        seed=42,
        symbols={"AAPL.ETORO", "TSLA.ETORO"},
        strategies={"SmaCrossoverStrategy", "AdxAtrStrategy"},
        reward_semantics_version=27,
        simulation_semantics_version=3,
    )
    base.update(overrides)
    return base


def test_deterministic_for_identical_inputs():
    fp1 = rpt.compute_run_fingerprint(**_kwargs())
    fp2 = rpt.compute_run_fingerprint(**_kwargs())
    assert fp1 == fp2


def test_sha256_hexdigest_shape():
    fp = rpt.compute_run_fingerprint(**_kwargs())
    assert isinstance(fp, str)
    assert len(fp) == 64
    int(fp, 16)  # raises ValueError if not valid hex


def test_symbol_set_iteration_order_does_not_matter():
    fp1 = rpt.compute_run_fingerprint(**_kwargs(symbols={"AAPL.ETORO", "TSLA.ETORO"}))
    fp2 = rpt.compute_run_fingerprint(**_kwargs(symbols={"TSLA.ETORO", "AAPL.ETORO"}))
    assert fp1 == fp2


def test_symbol_strategy_boundary_is_not_ambiguous():
    # Ohne Trennzeichen wuerde ('a','bc') und ('ab','c') denselben Payload ergeben.
    fp1 = rpt.compute_run_fingerprint(**_kwargs(symbols={"a"}, strategies={"bc"}))
    fp2 = rpt.compute_run_fingerprint(**_kwargs(symbols={"ab"}, strategies={"c"}))
    assert fp1 != fp2


def test_differs_on_seed_change():
    fp1 = rpt.compute_run_fingerprint(**_kwargs(seed=42))
    fp2 = rpt.compute_run_fingerprint(**_kwargs(seed=43))
    assert fp1 != fp2


def test_differs_on_commit_change():
    fp1 = rpt.compute_run_fingerprint(**_kwargs(git_commit_simulation="abc123"))
    fp2 = rpt.compute_run_fingerprint(**_kwargs(git_commit_simulation="def456"))
    assert fp1 != fp2


def test_differs_on_reward_semantics_version_change():
    fp1 = rpt.compute_run_fingerprint(**_kwargs(reward_semantics_version=27))
    fp2 = rpt.compute_run_fingerprint(**_kwargs(reward_semantics_version=28))
    assert fp1 != fp2


def test_seed_salt_default_none_matches_pre_1253_behaviour():
    fp_no_kwarg = rpt.compute_run_fingerprint(**_kwargs())
    fp_explicit_none = rpt.compute_run_fingerprint(**_kwargs(), seed_salt=None)
    assert fp_no_kwarg == fp_explicit_none


def test_seed_salt_changes_fingerprint():
    fp_unsalted = rpt.compute_run_fingerprint(**_kwargs())
    fp_salted = rpt.compute_run_fingerprint(**_kwargs(), seed_salt="salt-1")
    assert fp_unsalted != fp_salted


def test_different_salts_yield_different_fingerprints():
    fp_a = rpt.compute_run_fingerprint(**_kwargs(), seed_salt="salt-a")
    fp_b = rpt.compute_run_fingerprint(**_kwargs(), seed_salt="salt-b")
    assert fp_a != fp_b


def test_base_fingerprint_ignores_salt_for_family_grouping():
    fp_base_1 = rpt.compute_run_fingerprint(**_kwargs(), seed_salt=None)
    fp_base_2 = rpt.compute_run_fingerprint(**_kwargs(), seed_salt=None)
    fp_salted = rpt.compute_run_fingerprint(**_kwargs(), seed_salt="salt-x")
    assert fp_base_1 == fp_base_2
    assert fp_salted != fp_base_1


# ---------------------------------------------------------------------------------------------
# invariants.check_run_is_not_duplicate
# ---------------------------------------------------------------------------------------------

def test_no_fingerprint_is_inconclusive():
    r = inv.check_run_is_not_duplicate(None, "run-2", [])
    assert r.passed is True
    assert r.inconclusive is True


def test_no_run_id_is_inconclusive():
    r = inv.check_run_is_not_duplicate("fp-1", None, [])
    assert r.passed is True
    assert r.inconclusive is True


def test_empty_prior_entries_passes():
    r = inv.check_run_is_not_duplicate("fp-1", "run-2", [])
    assert r.passed is True
    assert r.inconclusive is False


def test_matching_fingerprint_under_different_run_id_fails():
    prior = [{"fingerprint": "fp-1", "run_id": "run-1", "started_at_utc": "2026-01-01T00:00:00Z"}]
    r = inv.check_run_is_not_duplicate("fp-1", "run-2", prior)
    assert r.passed is False
    assert r.severity == "high"
    assert r.actual["duplicate_of_run_id"] == "run-1"


def test_matching_fingerprint_under_same_run_id_does_not_count_as_duplicate():
    # Ein erneutes Rendern desselben Laufs (Zwischenstand/Re-Report) ist keine Wiederholung.
    prior = [{"fingerprint": "fp-1", "run_id": "run-2", "started_at_utc": "2026-01-01T00:00:00Z"}]
    r = inv.check_run_is_not_duplicate("fp-1", "run-2", prior)
    assert r.passed is True


def test_non_matching_fingerprint_passes():
    prior = [{"fingerprint": "fp-OTHER", "run_id": "run-1"}]
    r = inv.check_run_is_not_duplicate("fp-1", "run-2", prior)
    assert r.passed is True


# ---------------------------------------------------------------------------------------------
# manifest.append_jsonl_atomic / read_jsonl
# ---------------------------------------------------------------------------------------------

def test_read_jsonl_missing_file_returns_empty_list(tmp_path):
    assert manifest.read_jsonl(tmp_path / "does_not_exist.jsonl") == []


def test_append_then_read_round_trip(tmp_path):
    path = tmp_path / "run_fingerprints.jsonl"
    manifest.append_jsonl_atomic(path, {"fingerprint": "fp-1", "run_id": "run-1"})
    manifest.append_jsonl_atomic(path, {"fingerprint": "fp-2", "run_id": "run-2"})
    entries = manifest.read_jsonl(path)
    assert [e["run_id"] for e in entries] == ["run-1", "run-2"]


def test_read_jsonl_skips_corrupt_line(tmp_path):
    path = tmp_path / "run_fingerprints.jsonl"
    manifest.append_jsonl_atomic(path, {"fingerprint": "fp-1", "run_id": "run-1"})
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    manifest.append_jsonl_atomic(path, {"fingerprint": "fp-2", "run_id": "run-2"})
    entries = manifest.read_jsonl(path)
    assert [e["run_id"] for e in entries] == ["run-1", "run-2"]


def test_run_fingerprint_index_path_survives_work_dir_override(monkeypatch, tmp_path):
    # Pitfall #447-Klasse — RUN_FINGERPRINT_INDEX_PATH darf sich NICHT aendern, nur weil
    # OPTIMIZER_WORK_DIR (per-Lauf recycled, siehe executor.sh E-1) auf ein frisches Verzeichnis
    # zeigt. Modul-Reload noetig, da beide Konstanten einmalig beim Import gebunden werden.
    import importlib
    monkeypatch.delenv("OPTIMIZER_RUN_FINGERPRINT_INDEX", raising=False)
    monkeypatch.setenv("OPTIMIZER_WORK_DIR", str(tmp_path / "fresh_work_dir"))
    reloaded = importlib.reload(manifest)
    try:
        assert reloaded.WORK == tmp_path / "fresh_work_dir"
        assert reloaded.RUN_FINGERPRINT_INDEX_PATH == (
            reloaded.PROJECT_ROOT / "data" / "optimizer" / "run_fingerprints.jsonl")
        assert reloaded.WORK not in reloaded.RUN_FINGERPRINT_INDEX_PATH.parents
    finally:
        monkeypatch.undo()
        importlib.reload(manifest)


# ---------------------------------------------------------------------------------------------
# Issue #1325 (Katalog #1323-1329, P0) — _build_report liest symbols/strategies/seed_salt primaer
# aus cli_args (die tatsaechliche ANFRAGE), nicht mehr aus studies_out (das ERGEBNIS). Bei
# n_studies=0 kollabierten symbols/strategies vormals zu leeren Mengen und seed_salt zu None,
# UNABHAENGIG vom tatsaechlich angeforderten Symbol/Salt — mehrere Laeufe mit unterschiedlicher
# Anfrage trugen dadurch denselben run_fingerprint.
# ---------------------------------------------------------------------------------------------

def test_zero_studies_runs_with_different_cli_args_symbols_get_different_fingerprints(tmp_path):
    report_nvda = rpt._build_report(
        [], run_id="run-nvda", started_at_utc="2026-01-01T00:00:00Z", wallclock_s=1.0,
        cli_args={"symbols": "NVDA.ETORO", "strategies": "SmaCrossoverStrategy"},
        reports_dir=tmp_path / "nvda",
    )
    report_tsla = rpt._build_report(
        [], run_id="run-tsla", started_at_utc="2026-01-01T00:00:00Z", wallclock_s=1.0,
        cli_args={"symbols": "TSLA.ETORO", "strategies": "SmaCrossoverStrategy"},
        reports_dir=tmp_path / "tsla",
    )
    assert report_nvda["run_fingerprint"] != report_tsla["run_fingerprint"]


def test_zero_studies_runs_with_different_seed_salt_get_different_fingerprints(tmp_path):
    report_a = rpt._build_report(
        [], run_id="run-salt-a", started_at_utc="2026-01-01T00:00:00Z", wallclock_s=1.0,
        cli_args={"symbols": "NVDA.ETORO", "strategies": "all", "seed_salt": "salt-a"},
        reports_dir=tmp_path / "salt_a",
    )
    report_b = rpt._build_report(
        [], run_id="run-salt-b", started_at_utc="2026-01-01T00:00:00Z", wallclock_s=1.0,
        cli_args={"symbols": "NVDA.ETORO", "strategies": "all", "seed_salt": "salt-b"},
        reports_dir=tmp_path / "salt_b",
    )
    assert report_a["run_fingerprint"] != report_b["run_fingerprint"]


def test_zero_studies_run_without_cli_args_falls_back_to_studies_out_derivation(tmp_path):
    """``cli_args`` fehlend (Alt-Artefakte/Tests ohne cli_args) ⇒ Fallback auf die bisherige
    studies_out-Ableitung — bleibt bit-identisch zum Alt-Verhalten, keine Verhaltensaenderung
    ausserhalb des #1325-Symptomfalls."""
    report_no_cli_args = rpt._build_report(
        [], run_id="run-no-cli-args", started_at_utc="2026-01-01T00:00:00Z", wallclock_s=1.0,
        cli_args=None, reports_dir=tmp_path / "no_cli_args",
    )
    report_empty_cli_args = rpt._build_report(
        [], run_id="run-empty-cli-args", started_at_utc="2026-01-01T00:00:00Z", wallclock_s=1.0,
        cli_args={}, reports_dir=tmp_path / "empty_cli_args",
    )
    # Beide degenerieren identisch auf die leere studies_out-Ableitung (symbols=set(),
    # strategies=set(), seed_salt=None) -- derselbe Fingerabdruck fuer denselben Rest-Input.
    assert report_no_cli_args["run_fingerprint"] == report_empty_cli_args["run_fingerprint"]


# ---------------------------------------------------------------------------------------------
# report._compute_search_variance
# ---------------------------------------------------------------------------------------------

def _entry(run_id, *, fingerprint_base="base-1", best_reward, best_eligible_reward, n_eligible=5,
           strategy="SmaCrossoverStrategy", symbol="AAPL.ETORO"):
    return {
        "fingerprint": f"fp-{run_id}", "fingerprint_base": fingerprint_base, "run_id": run_id,
        "study_summaries": [
            {"strategy": strategy, "symbol": symbol, "best_reward": best_reward,
             "best_eligible_reward": best_eligible_reward, "n_eligible": n_eligible},
        ],
    }


def test_fewer_than_three_family_runs_returns_none():
    entries = [_entry("r1", best_reward=1.0, best_eligible_reward=0.9),
               _entry("r2", best_reward=1.1, best_eligible_reward=1.0)]
    assert rpt._compute_search_variance("base-1", entries) is None


def test_three_family_runs_computes_stats():
    entries = [
        _entry("r1", best_reward=1.0, best_eligible_reward=0.9),
        _entry("r2", best_reward=1.2, best_eligible_reward=1.0),
        _entry("r3", best_reward=1.4, best_eligible_reward=1.1),
    ]
    result = rpt._compute_search_variance("base-1", entries)
    assert result is not None
    assert result["n_runs_in_family"] == 3
    stats = result["per_study"]["SmaCrossoverStrategy/AAPL.ETORO"]
    assert stats["best_reward"]["median"] == 1.2
    assert stats["best_reward"]["n"] == 3


def test_different_fingerprint_base_excluded_from_family():
    entries = [
        _entry("r1", best_reward=1.0, best_eligible_reward=0.9, fingerprint_base="base-1"),
        _entry("r2", best_reward=1.2, best_eligible_reward=1.0, fingerprint_base="base-1"),
        _entry("r3", best_reward=99.0, best_eligible_reward=99.0, fingerprint_base="base-OTHER"),
    ]
    assert rpt._compute_search_variance("base-1", entries) is None


def test_legacy_entry_without_study_summaries_still_counts_toward_family_size():
    entries = [
        {"fingerprint_base": "base-1", "run_id": "r1"},
        _entry("r2", best_reward=1.0, best_eligible_reward=0.9),
        _entry("r3", best_reward=1.2, best_eligible_reward=1.0),
    ]
    result = rpt._compute_search_variance("base-1", entries)
    assert result is not None
    assert result["n_runs_in_family"] == 3


# ---------------------------------------------------------------------------------------------
# summary_de._section_1_result_in_one_sentence — Fix Punkt 4 (Duplikat-Hinweis im ersten Satz)
# ---------------------------------------------------------------------------------------------

def _minimal_report(**overrides):
    base = {
        "studies": [], "cross_study": {}, "run_status": "complete",
        "work_completed": True, "decision_admissible": True, "invariant_checks": [],
    }
    base.update(overrides)
    return base


def test_duplicate_of_none_produces_no_note():
    text = summary_de._section_1_result_in_one_sentence(_minimal_report(duplicate_of=None))
    assert "bit-identische Wiederholung" not in text


def test_duplicate_of_set_surfaces_in_first_sentence():
    text = summary_de._section_1_result_in_one_sentence(
        _minimal_report(duplicate_of="ba5796c7_20260101T000000000000"))
    assert "bit-identische Wiederholung" in text
    assert "ba5796c7_20260101T000000000000" in text
