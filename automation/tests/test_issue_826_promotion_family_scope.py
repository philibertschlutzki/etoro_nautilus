"""Issue #826 (P1, Katalog #822-#827, GitHub-Issue #750) — ``n_family`` ist die symbolweite
Cross-Strategie-Summe, wird aber auf eine Per-Strategie-Entscheidung angewandt.

Root-Cause: ``export_symbol_proposal`` promotet je (Strategie, Symbol) unabhängig — das Maximum,
gegen das deflatiert wird, wurde nur über die Trials EINER Strategie gezogen. ``sweep._family_n_
from_studies`` summierte trotzdem über ALLE Strategien-Studies desselben Symbols und reichte diese
eine Zahl an JEDE einzelne Strategie durch (Roster-Kopplung: eine 15. Strategie würde die
Promotion-Hürde jeder bestehenden erhöhen, ohne dass sich deren Evidenz geändert hätte).

Fix: ``sweep._family_n_stage1_from_studies`` liefert N1 JE STUDY (nicht symbolweit summiert) — das
ist seit diesem Fix die an ``confirm(...)`` durchgereichte Zahl unter dem neuen Default
``promotion_family_scope='per_strategy'`` (``tournament.json``). ``_family_n_stage2_from_studies``
liefert N2 (Zahl der Strategien mit ≥1 Kandidat) als reine Telemetrie für eine HYPOTHETISCHE zweite
Korrekturstufe (``promotion_family_scope='per_symbol_best'``) — diese Stufe ist deklariert, aber
laut Katalog #750 #826 Fix Punkt 4 NICHT ohne eigenen H0-Kalibrierlauf aktivierbar und daher
fail-loud (``sweep._resolve_promotion_family_scope``), analog zum #823-T-adaptiven-Guard- und
#824-H0-Aufschub-Muster.
"""
import pytest

from automation.optimizer import sweep
from automation.optimizer.report import _family_n_stages


class _T:
    def __init__(self, has_statistic=True):
        self.user_attrs = {"oos_selection_statistic_available": has_statistic}


class _S:
    def __init__(self, n_with_statistic, n_without=0):
        self.trials = [_T(True) for _ in range(n_with_statistic)] + [_T(False) for _ in range(n_without)]
        self.user_attrs = {}


# ── _family_n_stage1_from_studies: N1 JE STUDY, nicht symbolweit summiert ──────────────────────────
def test_stage1_returns_per_study_not_summed_across_strategies():
    pairs = [("StratA", "TSLA.ETORO", "OK"), ("StratB", "TSLA.ETORO", "OK"),
             ("StratA", "AAPL.ETORO", "OK")]
    studies = [_S(50), _S(30), _S(20)]
    stage1 = sweep._family_n_stage1_from_studies(pairs, studies, tournament_cfg={})
    assert stage1 == {
        ("StratA", "TSLA.ETORO"): 50,
        ("StratB", "TSLA.ETORO"): 30,
        ("StratA", "AAPL.ETORO"): 20,
    }


def test_stage1_excludes_trials_without_selection_statistic():
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    studies = [_S(7, n_without=93)]
    stage1 = sweep._family_n_stage1_from_studies(pairs, studies, tournament_cfg={})
    assert stage1[("StratA", "TSLA.ETORO")] == 7


# ── Akzeptanzkriterium #826: unter per_strategy liegt N1 in 50-200, nicht 1500 ─────────────────────
def test_stage1_does_not_inflate_with_roster_size():
    """14 Strategien-Studies desselben Symbols (je ~100 Trials, wie im #826-Referenzlauf) — N1 EINER
    Study bleibt in der Grössenordnung 50-200, unabhängig von der Zahl der ANDEREN Studies."""
    pairs = [(f"Strat{i}", "GSAT.ETORO", "OK") for i in range(14)]
    studies = [_S(100) for _ in range(14)]
    stage1 = sweep._family_n_stage1_from_studies(pairs, studies, tournament_cfg={})
    for n in stage1.values():
        assert 50 <= n <= 200


# ── Roster-Invarianz (Akzeptanzkriterium #826 Fix Punkt 3): 14 vs. 10 Strategien ────────────────────
def test_roster_invariance_stage1_of_shared_strategy_unaffected_by_roster_size():
    """Dieselbe Study (StratA/TSLA) einmal mit 14, einmal mit 10 Strategien-Studies auf demselben
    Symbol gerechnet — N1 von StratA (und damit die SR0/DSR jeder gemeinsamen Strategie unter
    promotion_family_scope='per_strategy') darf sich NICHT ändern."""
    shared_pair = ("StratA", "TSLA.ETORO", "OK")
    shared_study = _S(80)

    pairs_14 = [shared_pair] + [(f"Extra{i}", "TSLA.ETORO", "OK") for i in range(13)]
    studies_14 = [shared_study] + [_S(60) for _ in range(13)]
    pairs_10 = [shared_pair] + [(f"Extra{i}", "TSLA.ETORO", "OK") for i in range(9)]
    studies_10 = [shared_study] + [_S(60) for _ in range(9)]

    stage1_14 = sweep._family_n_stage1_from_studies(pairs_14, studies_14, tournament_cfg={})
    stage1_10 = sweep._family_n_stage1_from_studies(pairs_10, studies_10, tournament_cfg={})
    assert stage1_14[("StratA", "TSLA.ETORO")] == stage1_10[("StratA", "TSLA.ETORO")] == 80

    # Gegenprobe: die (jetzt verworfene) symbolweite Summe WÄRE roster-gekoppelt gewesen.
    flat_14 = sweep._family_n_from_studies(pairs_14, studies_14, tournament_cfg={})
    flat_10 = sweep._family_n_from_studies(pairs_10, studies_10, tournament_cfg={})
    assert flat_14["TSLA.ETORO"] != flat_10["TSLA.ETORO"]


# ── _family_n_stage2_from_studies: N2 = Zahl der Strategien mit >=1 Kandidat ────────────────────────
def test_stage2_counts_strategies_with_at_least_one_candidate():
    pairs = [("StratA", "TSLA.ETORO", "OK"), ("StratB", "TSLA.ETORO", "OK"),
             ("StratC", "TSLA.ETORO", "OK")]
    studies = [_S(10), _S(0), _S(5)]  # StratB liefert 0 Kandidaten mit Teststatistik
    stage2 = sweep._family_n_stage2_from_studies(pairs, studies, tournament_cfg={})
    assert stage2["TSLA.ETORO"] == 2


# ── _resolve_promotion_family_scope: Default, Validierung, Fail-Loud ────────────────────────────────
def test_resolve_scope_defaults_to_per_strategy():
    assert sweep._resolve_promotion_family_scope(None) == "per_strategy"
    assert sweep._resolve_promotion_family_scope({}) == "per_strategy"


def test_resolve_scope_accepts_explicit_per_strategy():
    assert sweep._resolve_promotion_family_scope({"promotion_family_scope": "per_strategy"}) == "per_strategy"


def test_resolve_scope_fails_loud_on_per_symbol_best():
    """Issue #826 Fix Punkt 4 — 'per_symbol_best' ist deklariert, aber die zweistufige SR0-
    Komposition erfordert einen H0-Kalibrierlauf, der in diesem Environment nicht durchführbar ist:
    FAIL-LOUD statt einer stillen, unkalibrierten Formel."""
    with pytest.raises(ValueError, match="per_symbol_best"):
        sweep._resolve_promotion_family_scope({"promotion_family_scope": "per_symbol_best"})


def test_resolve_scope_rejects_unknown_value():
    with pytest.raises(ValueError):
        sweep._resolve_promotion_family_scope({"promotion_family_scope": "typo_value"})


# ── run_per_symbol_sweep: aborts fail-loud BEFORE any optimize_symbol call ─────────────────────────
def test_run_per_symbol_sweep_aborts_before_any_optimization_when_scope_is_per_symbol_best(
    monkeypatch, tmp_path,
):
    """Issue #826 Fix Punkt 2 — die Validierung laeuft VOR der Symbol-Schleife (fail-loud vor
    kostspieliger Arbeit, nicht erst beim ersten Confirm-Aufruf): kein einziger optimize_symbol-
    Aufruf darf stattfinden, wenn ``promotion_family_scope='per_symbol_best'`` deklariert ist."""
    import json

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "tournament.json").write_text(
        json.dumps({"promotion_family_scope": "per_symbol_best"}), "utf-8")
    (cfg_dir / "backtest.json").write_text(json.dumps({"walk_forward": {
        "holdout_days": 45, "is_window_days": 120, "oos_window_days": 45, "splits": 1,
        "embargo_period_days": 0}}), "utf-8")
    (cfg_dir / "optimizer.json").write_text(json.dumps({
        "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
    }), "utf-8")

    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: [("S", "A.ETORO", "OK")])
    monkeypatch.setattr(sweep, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(sweep, "WORK", tmp_path)

    calls = []
    with pytest.raises(ValueError, match="per_symbol_best"):
        sweep.run_per_symbol_sweep(
            ["S"], ["A.ETORO"], tier="all", n_jobs=1,
            optimize_symbol=lambda *a, **k: calls.append((a, k)),
            confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
        )
    assert calls == []


# ── report._family_n_stages: cross_study-Aufschlüsselung (Akzeptanzkriterium 2) ─────────────────────
def test_report_family_n_stages_exposes_stage1_and_stage2_separately():
    studies_out = [
        {"symbol": "TSLA.ETORO", "strategy": "StratA", "n_family_stage1": 80},
        {"symbol": "TSLA.ETORO", "strategy": "StratB", "n_family_stage1": 0},
        {"symbol": "AAPL.ETORO", "strategy": "StratA", "n_family_stage1": 45},
    ]
    stage1, stage2 = _family_n_stages(studies_out)
    assert stage1 == {"TSLA.ETORO": {"StratA": 80, "StratB": 0}, "AAPL.ETORO": {"StratA": 45}}
    assert stage2 == {"TSLA.ETORO": 1, "AAPL.ETORO": 1}


def test_report_family_n_stages_skips_records_without_the_field():
    """Rueckwaertskompat: ein Report-Datensatz aus einem Lauf vor #826 (kein n_family_stage1) traegt
    weder zu stage1 noch zu stage2 bei, statt mit einem falschen 0/None-Eintrag zu erscheinen."""
    studies_out = [{"symbol": "TSLA.ETORO", "strategy": "StratA"}]
    stage1, stage2 = _family_n_stages(studies_out)
    assert stage1 == {}
    assert stage2 == {}
