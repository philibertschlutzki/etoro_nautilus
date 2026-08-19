"""Issue #1066/#1067/#1068 (Katalog #866-2, Kohorte B, "Stufe 0") — der automatische Suchraum-
Rückschrieb (#761/#763) klammerte nur ``max_bars_in_trade`` und nur nach oben; jede andere
Untergrenze/jeder andere Parameter konnte über wiederholte Läufe unbegrenzt divergieren
(``cross_study.boundary_solutions`` trug u. a. ``ema_period: [-325.0, 300]``, Beweis B-5).

#1066 — ``spaces._PARAM_DOMAIN_REGISTRY``/``clamp_param_bounds``/``is_bounds_admissible`` klammern
jeden automatischen Vorschlag SYMMETRISCH; ``spaces._bounds_for`` lehnt eine inadmissible
kuratierte Überschreibung fail-loud ab (Config-Fehler) und eine inadmissible AUTOMATISCHE
Überschreibung fail-open (verwirft den Cache-Eintrag, fällt auf den Default zurück);
``sweep_diagnostics.migrate_search_space_override_cache`` bereinigt einen vor diesem Fix
geschriebenen Cache; ``invariants.check_search_space_override_admissible`` bewacht den Cache-Stand
im Report; ``record_diagnosed_pair`` deckelt zusätzlich die Anzahl aufeinanderfolgender
Weitungs-Übernahmen je Parameter (``_MAX_WIDEN_APPLICATIONS``).

#1067 — ``_contracts.MIN_BARS_IN_TRADE_FLOOR`` ist die symmetrische Untergrenze zu
``MAX_BARS_IN_TRADE_HARD_CAP``; ``report._study_record`` stempelt ``winner_outside_default_bounds_after_override``,
sobald der Gewinner-Trial ausserhalb des kuratierten Default-Suchbands liegt.

#1068 — ``invariants.check_diagnosis_ledger_coherence`` FAILt, wenn der #761-Diagnose-Cache mehr
aufeinanderfolgende Bestätigungen trägt, als das Coverage-Ledger Läufe gesehen hat (einer der
beiden Stores ist dann nachweislich zurückgesetzt/verloren gegangen, vgl. #1064).
"""
import json

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import spaces
from automation.optimizer import sweep_diagnostics as sd
from automation.optimizer._contracts import MAX_BARS_IN_TRADE_HARD_CAP, MIN_BARS_IN_TRADE_FLOOR


@pytest.fixture(autouse=True)
def _enable_diagnostic_writeback(monkeypatch):
    """Issue #1090 (Katalog #923) — siehe test_issue_681_diagnosis_closed_loop.py: dieses Modul
    testet record_diagnosed_pair's Rueckschrieb-Mechanik selbst, unabhaengig vom seit #1090
    standardmaessig deaktivierten Produktions-Sicherheitsschalter."""
    monkeypatch.setattr(sd, "_read_diagnostic_writeback_enabled", lambda: True)


# ── #1066: clamp_param_bounds / is_bounds_admissible ────────────────────────────────────────────
def test_clamp_replaces_out_of_domain_lower_bound():
    lo, hi = spaces.clamp_param_bounds("ema_period", -325.0, 300)
    assert lo >= 2
    assert (lo, hi) == (2, 300)


def test_clamp_leaves_in_domain_value_bit_identical():
    assert spaces.clamp_param_bounds("cooldown_bars", 10.0, 17.8) == (10.0, 17.8)


def test_clamp_is_noop_for_unregistered_param():
    assert spaces.clamp_param_bounds("some_future_param", -999.0, 999.0) == (-999.0, 999.0)


def test_is_bounds_admissible_true_within_domain_false_outside():
    assert spaces.is_bounds_admissible("rsi_oversold", 15.0, 45.0) is True
    assert spaces.is_bounds_admissible("rsi_oversold", -30.0, 45.0) is False


# ── #1066: _widen_bounds_toward never diverges below the domain floor ───────────────────────────
def test_widen_bounds_toward_never_goes_negative_for_any_registered_param():
    for param in spaces._PARAM_DOMAIN_REGISTRY:
        current_bounds = {param: (10.0, 50.0)}
        proposal = sd._widen_bounds_toward({param: "low"}, current_bounds, widen_fraction=0.3)
        lo, _hi = proposal[param]
        dom_lo, _dom_hi, _dtype = spaces._PARAM_DOMAIN_REGISTRY[param]
        assert lo >= dom_lo, f"{param}: {lo} < {dom_lo}"


def test_widen_bounds_toward_repeated_application_converges_not_diverges():
    """Vor #1066 erzeugte jede Anwendung denselben unkalmmerten Schritt, akkumuliert ueber viele
    Laeufe auf lo0 - k*widen*span0 (Beweis B-5: k=5 ⇒ -325.0). Mit der Klammer ist bereits die
    ERSTE Anwendung am Domaenen-Rand, jede weitere bleibt dort stehen (kein Divergenzpfad mehr)."""
    current_bounds = {"ema_period": (50.0, 300.0)}
    results = [
        sd._widen_bounds_toward({"ema_period": "low"}, current_bounds, widen_fraction=0.3)["ema_period"]
        for _ in range(5)
    ]
    for lo, _hi in results:
        assert lo >= 2
    # alle fuenf "Laeufe" landen auf demselben geklammerten Ergebnis, keine fortschreitende
    # Divergenz Richtung -325.
    assert len({tuple(r) for r in results}) == 1


# ── #1066: spaces._bounds_for validation ─────────────────────────────────────────────────────────
def test_bounds_for_rejects_inadmissible_curated_override_fail_loud(monkeypatch):
    monkeypatch.setattr(spaces, "_search_space_overrides_cache",
                        {"TrendPullbackStrategy": {"TSLA.ETORO": {"ema_period": [-325.0, 300]}}})
    monkeypatch.setattr(spaces, "_auto_proposed_bounds_cache", {})
    with pytest.raises(ValueError, match="SEARCH_SPACE_OVERRIDE_INADMISSIBLE"):
        spaces._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "ema_period", 50, 300)


def test_bounds_for_falls_back_to_default_for_inadmissible_auto_override(monkeypatch, caplog):
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", {})
    monkeypatch.setattr(
        spaces, "_auto_proposed_bounds_cache",
        {"TrendPullbackStrategy": {"TSLA.ETORO": {"ema_period": [-325.0, 300]}}})
    lo, hi = spaces._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "ema_period", 50, 300)
    assert (lo, hi) == (50, 300)


def test_bounds_for_accepts_admissible_auto_override(monkeypatch):
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", {})
    monkeypatch.setattr(
        spaces, "_auto_proposed_bounds_cache",
        {"TrendPullbackStrategy": {"TSLA.ETORO": {"cooldown_bars": [1.0, 50.0]}}})
    lo, hi = spaces._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "cooldown_bars", 2, 36)
    assert (lo, hi) == (1.0, 50.0)


# ── #1066: migrate_search_space_override_cache ──────────────────────────────────────────────────
def test_migrate_clamps_stale_cache_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "_diagnosed_pairs_cache_path",
                        lambda work_dir=None: tmp_path / "diagnosed_pairs_cache.json")
    stale_cache = {
        "pairs": [
            {"strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
             "action": "search_space_override", "binding_cause": "boundary_solution",
             "proposed_bounds": {"ema_period": [-325.0, 300], "rsi_oversold": [-30.0, 45.0]}},
        ]
    }
    (tmp_path / "diagnosed_pairs_cache.json").write_text(json.dumps(stale_cache), "utf-8")

    summary = sd.migrate_search_space_override_cache()
    assert summary["entries_migrated"] == 1

    migrated = sd.load_diagnosed_pairs_cache()
    entry = migrated[("TrendPullbackStrategy", "TSLA.ETORO")]
    assert entry["proposed_bounds"]["ema_period"][0] >= 2
    assert entry["proposed_bounds"]["rsi_oversold"][0] >= 1.0


# ── #1066: invariants.check_search_space_override_admissible ────────────────────────────────────
def test_check_search_space_override_admissible_fails_on_stale_entry():
    diagnosed_pairs = [{
        "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
        "proposed_bounds": {"ema_period": [-325.0, 300]},
    }]
    result = inv.check_search_space_override_admissible(diagnosed_pairs)
    assert result.passed is False
    assert "TrendPullbackStrategy/TSLA.ETORO" in result.actual


def test_check_search_space_override_admissible_passes_on_clamped_entry():
    diagnosed_pairs = [{
        "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
        "proposed_bounds": {"ema_period": [2.0, 300]},
    }]
    result = inv.check_search_space_override_admissible(diagnosed_pairs)
    assert result.passed is True


# ── #1066 Fix Punkt 3: record_diagnosed_pair caps consecutive widen applications ─────────────────
def test_record_diagnosed_pair_caps_widen_applications(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "_diagnosed_pairs_cache_path",
                        lambda work_dir=None: tmp_path / "diagnosed_pairs_cache.json")
    base = {
        "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
        "action": "search_space_override", "binding_cause": "boundary_solution",
    }
    # Drei aufeinanderfolgende, IMMER weiter absteigende Vorschlaege fuer denselben Parameter.
    sd.record_diagnosed_pair({**base, "proposed_bounds": {"cooldown_bars": [5.0, 36.0]}})
    sd.record_diagnosed_pair({**base, "proposed_bounds": {"cooldown_bars": [3.0, 36.0]}})
    sd.record_diagnosed_pair({**base, "proposed_bounds": {"cooldown_bars": [1.0, 36.0]}})

    cache = sd.load_diagnosed_pairs_cache()
    entry = cache[("TrendPullbackStrategy", "TSLA.ETORO")]
    # _MAX_WIDEN_APPLICATIONS=2: die dritte Weitung wird NICHT mehr uebernommen, der Wert der
    # zweiten Anwendung bleibt stehen.
    assert entry["proposed_bounds"]["cooldown_bars"] == [3.0, 36.0]
    assert entry["widen_applications"]["cooldown_bars"] == 2
    assert entry["n_runs_confirmed"] == 3


# ── #1067: MIN_BARS_IN_TRADE_FLOOR ───────────────────────────────────────────────────────────────
def test_min_bars_in_trade_floor_is_positive_and_below_hard_cap():
    assert 0 < MIN_BARS_IN_TRADE_FLOOR < MAX_BARS_IN_TRADE_HARD_CAP


def test_max_bars_in_trade_domain_uses_the_floor_and_hard_cap():
    dom_lo, dom_hi, _dtype = spaces._PARAM_DOMAIN_REGISTRY["max_bars_in_trade"]
    assert dom_lo == MIN_BARS_IN_TRADE_FLOOR
    assert dom_hi == MAX_BARS_IN_TRADE_HARD_CAP


def test_max_bars_in_trade_widen_never_drops_below_floor():
    proposal = sd._widen_bounds_toward(
        {"max_bars_in_trade": "low"}, {"max_bars_in_trade": (12.0, 24.0)}, widen_fraction=3.0)
    lo, _hi = proposal["max_bars_in_trade"]
    assert lo >= MIN_BARS_IN_TRADE_FLOOR


# ── #1067: report._study_record winner_outside_default_bounds_after_override ───────────────────────────────────
def _study_with_winner(params):
    class _T:
        def __init__(self, params):
            self.value = 1.0
            self.params = params
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": True}

    class _S:
        trials = [_T(params)]
        best_value = 1.0
        user_attrs = {}

    return _S()


def test_winner_outside_default_bounds_after_override_empty_for_in_band_winner():
    from automation.optimizer.report import _study_record
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TrendPullbackStrategy"}
    study = _study_with_winner({"ema_period": 120, "cooldown_bars": 10})
    record, _checks = _study_record(proposal, study)
    assert record["winner_outside_default_bounds_after_override"] is None


def test_winner_outside_default_bounds_after_override_flags_a_below_floor_winner():
    from automation.optimizer.report import _study_record
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TrendPullbackStrategy"}
    # ema_period Default-Suchband ist (50, 300) — 18 liegt darunter (Beweis B-5/#1067-Referenzfall).
    study = _study_with_winner({"ema_period": 18, "max_bars_in_trade": 5})
    record, _checks = _study_record(proposal, study)
    assert "ema_period" in record["winner_outside_default_bounds_after_override"]
    assert record["winner_outside_default_bounds_after_override"]["ema_period"][0] == 18


# ── #1068: check_diagnosis_ledger_coherence ─────────────────────────────────────────────────────
def test_diagnosis_ledger_coherence_fails_when_cache_outpaces_ledger():
    diagnosed_pairs = [
        {"strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO", "n_runs_confirmed": 5},
    ]
    result = inv.check_diagnosis_ledger_coherence(diagnosed_pairs, total_runs_started=1)
    assert result.passed is False
    assert result.actual["max_n_runs_confirmed"] == 5


def test_diagnosis_ledger_coherence_passes_when_ledger_keeps_up():
    diagnosed_pairs = [
        {"strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO", "n_runs_confirmed": 5},
    ]
    result = inv.check_diagnosis_ledger_coherence(diagnosed_pairs, total_runs_started=5)
    assert result.passed is True


def test_diagnosis_ledger_coherence_inconclusive_without_ledger_value():
    result = inv.check_diagnosis_ledger_coherence([], total_runs_started=None)
    assert result.passed is True
    assert result.inconclusive is True
