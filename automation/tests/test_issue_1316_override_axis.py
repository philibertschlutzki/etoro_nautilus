"""Issue #1316 (GH #1193, P1) — Alle 18 kuratierten Suchraum-Overrides sind achsen-stale.

Symptom. ``search_space_overrides.json`` enthält ausschliesslich bar-denominierte Bounds, die auf
der 24/7-Achse kalibriert wurden und nach #1275 unverändert gelten (B-13). Der Suchraum mischt zwei
Achsen im Faktor 4,17 (``RTH_AXIS_FACTOR=0.24``).

Root-Cause. #1275 hat Cap, Floor und ``time_box_bars`` umskaliert und kuratierte Overrides bewusst
ausgenommen — ohne Mechanismus, der diese Ausnahme sichtbar macht.

Fix.
1. ``search_space_overrides.json`` erhält je Override-Block ein Pflichtfeld ``axis`` (Geschwister
   der Parameter-Bounds, ``spaces._OVERRIDE_METADATA_KEYS``) und ``calibrated_in_run_id``.
2. ``spaces._bounds_for`` vergleicht ``axis`` gegen ``optimizer.json['time_box_bars_axis']``. Bei
   Divergenz gilt für bar-denominierte Parameter (``spaces._BAR_DENOMINATED_PARAMS``) fail-loud:
   ``StaleAxisOverrideError``. Kein stilles Weiterverwenden.
3. Migration: alle 18 vorbestehenden Einträge tragen ``axis: "calendar_24_7"`` und einen
   umgerechneten ``proposed_rth_bounds``-Kommentar (Faktor ``RTH_AXIS_FACTOR``, ganzzahlig gerundet,
   Untergrenze mindestens ``MIN_BARS_IN_TRADE_FLOOR``). Die Umschaltung selbst bleibt eine bewusste
   PR-Entscheidung.
4. Neue Invariante ``check_override_axis_coherence`` (severity ``high``), report-seitig; §5.4 zeigt
   je Override eine Achsen-Spalte.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import bounds
from automation.optimizer import invariants as inv
from automation.optimizer import spaces
from automation.optimizer._contracts import MIN_BARS_IN_TRADE_FLOOR, RTH_AXIS_FACTOR

OPT_CFG_PATH = Path("automation/config/optimizer.json")
OVERRIDES_PATH = Path("automation/config/search_space_overrides.json")


def _reset_caches(monkeypatch):
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", None)
    # Issue #1316 — leer statt None: ``None`` faellt bei einem Cache-Miss auf den echten #761-
    # Diagnose-Cache auf der Festplatte zurueck (stray Artefakte aus frueheren Testlaeufen dieser
    # Session), was Tests, die ``bounds.active_bounds_overrides()`` (BEIDE Quellen) aufrufen,
    # unvorhersehbar verunreinigen wuerde.
    monkeypatch.setattr(spaces, "_auto_proposed_bounds_cache", {})


# ── Akzeptanzkriterium 1 — rth-Lauf + calendar_24_7-Override auf max_bars_in_trade bricht ab ─────

def test_divergent_axis_on_bar_denominated_param_raises_fail_loud(monkeypatch):
    _reset_caches(monkeypatch)
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", {
        "TrendPullbackStrategy": {"TSLA.ETORO": {
            "axis": "calendar_24_7", "max_bars_in_trade": [1, 4],
        }},
    })
    monkeypatch.setattr(spaces, "_current_time_box_bars_axis", lambda: "rth")
    with pytest.raises(spaces.StaleAxisOverrideError, match="STALE_AXIS_OVERRIDE"):
        spaces._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "max_bars_in_trade", 3, 6)


def test_matching_axis_resolves_normally(monkeypatch):
    _reset_caches(monkeypatch)
    # max_bars_in_trade=[2, 2] statt [1, 1] — Issue #1317/GH #1194 hob MIN_BARS_IN_TRADE_FLOOR auf
    # 2 an, [1, 1] waere seither SEARCH_SPACE_OVERRIDE_INADMISSIBLE (ausserhalb des Domaenen-
    # registers), unabhaengig von der hier getesteten axis-Kohaerenz.
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", {
        "TrendPullbackStrategy": {"TSLA.ETORO": {
            "axis": "rth", "max_bars_in_trade": [2, 2],
        }},
    })
    monkeypatch.setattr(spaces, "_current_time_box_bars_axis", lambda: "rth")
    lo, hi = spaces._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "max_bars_in_trade", 3, 6)
    assert (lo, hi) == (2, 2)


def test_the_real_production_config_currently_diverges_against_rth():
    """Regressionsschutz/Akzeptanzkriterium 1 gegen die ECHTE, committete Config: die 18
    vorbestehenden Overrides sind bewusst als axis='calendar_24_7' migriert (Fix Punkt 3), waehrend
    das echte optimizer.json['time_box_bars_axis'] seit #1275 'rth' ist — ein tatsaechlicher Lauf
    MUSS also fail-loud abbrechen, bis eine separate PR die Umschaltung vornimmt."""
    with pytest.raises(spaces.StaleAxisOverrideError):
        spaces._bounds_for("VwapExhaustionStrategy", "TSLA.ETORO", "max_bars_in_trade", 3, 6)


# ── Akzeptanzkriterium 2 — Override ohne axis-Feld bricht ebenfalls ab (kein Default) ─────────────

def test_missing_axis_field_raises_even_when_run_axis_matches_nothing_specific(monkeypatch):
    _reset_caches(monkeypatch)
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", {
        "TrendPullbackStrategy": {"TSLA.ETORO": {"cooldown_bars": [2, 12]}},
    })
    monkeypatch.setattr(spaces, "_current_time_box_bars_axis", lambda: "rth")
    with pytest.raises(spaces.StaleAxisOverrideError, match="STALE_AXIS_OVERRIDE"):
        spaces._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "cooldown_bars", 2, 36)


def test_missing_axis_field_raises_even_when_run_axis_itself_is_unknown(monkeypatch):
    """Kein Default: selbst wenn die Lauf-Achse NICHT bestimmbar ist (z. B. optimizer.json fehlt
    der Schluessel), bleibt ein fehlendes axis-Feld am Override ein Verstoss."""
    _reset_caches(monkeypatch)
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", {
        "TrendPullbackStrategy": {"TSLA.ETORO": {"cooldown_bars": [2, 12]}},
    })
    monkeypatch.setattr(spaces, "_current_time_box_bars_axis", lambda: None)
    with pytest.raises(spaces.StaleAxisOverrideError):
        spaces._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "cooldown_bars", 2, 36)


# ── Akzeptanzkriterium 3 — nicht-bar-denominierte Parameter sind nicht betroffen ─────────────────

def test_non_bar_denominated_param_without_axis_is_unaffected(monkeypatch):
    _reset_caches(monkeypatch)
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", {
        "FlashCrashReversalStrategy": {"TSLA.ETORO": {"rsi_oversold": [12, 28]}},
    })
    monkeypatch.setattr(spaces, "_current_time_box_bars_axis", lambda: "rth")
    lo, hi = spaces._bounds_for("FlashCrashReversalStrategy", "TSLA.ETORO", "rsi_oversold", 10, 30)
    assert (lo, hi) == (12, 28)


def test_bar_denominated_params_constant_matches_the_params_actually_used_in_overrides():
    overrides = json.loads(OVERRIDES_PATH.read_text("utf-8"))["overrides"]
    used_params = {
        param
        for symbols in overrides.values()
        for entry in symbols.values()
        for param in entry
        if param not in spaces._OVERRIDE_METADATA_KEYS
    }
    assert used_params <= spaces._BAR_DENOMINATED_PARAMS


# ── active_bounds_overrides(): Metadaten-Schluessel werden uebersprungen, axis wird ausgewiesen ──

def test_active_bounds_overrides_skips_metadata_keys_without_crashing(monkeypatch):
    _reset_caches(monkeypatch)
    from automation.optimizer import sweep_diagnostics as sd
    # Issue #1316 — ``bounds.active_bounds_overrides`` liest den #761-Auto-Cache DIREKT ueber
    # ``sweep_diagnostics.load_diagnosed_pairs_cache`` (nicht ueber ``spaces._auto_proposed_bounds_
    # cache``) — muss separat stillgelegt werden, sonst mischen sich stray Artefakte von frueheren
    # Testlaeufen dieser Session in dieses Ergebnis (siehe test_issue_1040_1189-Praezedenzfall).
    monkeypatch.setattr(sd, "load_diagnosed_pairs_cache", lambda: {})
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", {
        "TrendPullbackStrategy": {"TSLA.ETORO": {
            "axis": "calendar_24_7", "calibrated_in_run_id": None,
            "ema_period": [5, 25],
            "proposed_rth_bounds": {"ema_period": [1, 6]},
        }},
    })
    rows = bounds.active_bounds_overrides()
    params_seen = {r["parameter"] for r in rows}
    assert params_seen == {"ema_period"}
    assert rows[0]["axis"] == "calendar_24_7"


def test_active_bounds_overrides_on_real_config_has_18_curated_rows_all_calendar_24_7():
    rows = bounds.active_bounds_overrides()
    curated = [r for r in rows if r["source"] == "curated"]
    assert len(curated) == 18
    assert all(r["axis"] == "calendar_24_7" for r in curated)


def test_auto_proposed_rows_carry_axis_none():
    from automation.optimizer.sweep_diagnostics import load_diagnosed_pairs_cache  # noqa: F401
    rows = bounds.active_bounds_overrides()
    auto = [r for r in rows if r["source"] == "auto_proposed"]
    assert all(r["axis"] is None for r in auto)


# ── invariants.check_override_axis_coherence ─────────────────────────────────────────────────────

def test_check_override_axis_coherence_flags_divergent_bar_param():
    rows = [{
        "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO", "parameter": "max_bars_in_trade",
        "source": "curated", "axis": "calendar_24_7",
    }]
    result = inv.check_override_axis_coherence(rows, run_axis="rth")
    assert result.passed is False
    assert result.severity == "high"
    assert "max_bars_in_trade" in str(result.actual)


def test_check_override_axis_coherence_flags_missing_axis():
    rows = [{
        "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO", "parameter": "cooldown_bars",
        "source": "curated", "axis": None,
    }]
    result = inv.check_override_axis_coherence(rows, run_axis="rth")
    assert result.passed is False


def test_check_override_axis_coherence_ignores_non_bar_param():
    rows = [{
        "strategy": "FlashCrashReversalStrategy", "symbol": "TSLA.ETORO", "parameter": "rsi_oversold",
        "source": "curated", "axis": None,
    }]
    result = inv.check_override_axis_coherence(rows, run_axis="rth")
    assert result.passed is True


def test_check_override_axis_coherence_ignores_auto_proposed_source():
    rows = [{
        "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO", "parameter": "max_bars_in_trade",
        "source": "auto_proposed", "axis": None,
    }]
    result = inv.check_override_axis_coherence(rows, run_axis="rth")
    assert result.passed is True


def test_check_override_axis_coherence_passes_on_matching_axis():
    rows = [{
        "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO", "parameter": "max_bars_in_trade",
        "source": "curated", "axis": "rth",
    }]
    result = inv.check_override_axis_coherence(rows, run_axis="rth")
    assert result.passed is True


def test_check_override_axis_coherence_on_the_real_config_against_its_own_declared_axis_passes():
    """Regressionsschutz: gegen die EIGENE axis-Deklaration (nicht gegen die Lauf-Achse) sind die
    18 Eintraege in sich konsistent — der Verstoss entsteht erst im Vergleich mit run_axis='rth'."""
    rows = bounds.active_bounds_overrides()
    result = inv.check_override_axis_coherence(rows, run_axis="calendar_24_7")
    assert result.passed is True


def test_check_override_axis_coherence_on_the_real_config_against_rth_fails():
    rows = bounds.active_bounds_overrides()
    result = inv.check_override_axis_coherence(rows, run_axis="rth")
    assert result.passed is False
    assert len(result.actual) == 18


# ── Akzeptanzkriterium 4 — §5.4 des Reports weist je Override die Achse aus ──────────────────────

def _minimal_report(**overrides):
    base = {
        "run_id": "run-1", "run_status": "complete",
        "started_at_utc": "2026-08-19T00:00:00Z", "wallclock_s": 10.0,
        "cli_args": {"n_jobs": 1, "n_jobs_source": "CLI"},
        "symbols_completed": 1, "symbols_planned": 1,
        "studies": [],
        "cross_study": {
            "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }
    base.update(overrides)
    return base


def test_section_5_4_renders_an_achse_column_header():
    from automation.optimizer import summary_de

    report = _minimal_report(
        studies=[{"strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO"}],
        cross_study={
            "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
            "active_bounds_overrides": [{
                "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO", "parameter": "ema_period",
                "active_bounds": [5, 25], "default_bounds": [50, 300], "source": "curated",
                "set_in_run_id": None, "rationale": "Issue #669", "axis": "calendar_24_7",
            }],
        })
    text = summary_de.generate_german_summary(report)
    assert "| Achse |" in text
    assert "calendar_24_7" in text


def test_section_5_4_shows_k_a_when_axis_is_absent():
    from automation.optimizer import summary_de

    report = _minimal_report(
        studies=[{"strategy": "SqueezeBreakoutStrategy", "symbol": "NVDA.ETORO"}],
        cross_study={
            "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
            "active_bounds_overrides": [{
                "strategy": "SqueezeBreakoutStrategy", "symbol": "NVDA.ETORO",
                "parameter": "max_bars_in_trade", "active_bounds": [4, 18], "default_bounds": [3, 6],
                "source": "auto_proposed", "set_in_run_id": "abcd1234", "rationale": "boundary_solution",
                "axis": None,
            }],
        })
    text = summary_de.generate_german_summary(report)
    section_5_4 = text.split("### 5.4")[1]
    lines = [l for l in section_5_4.splitlines() if "SqueezeBreakoutStrategy" in l]
    assert len(lines) == 1
    assert "| k. A. |" in lines[0]


# ── proposed_rth_bounds-Migrationsvorschlag: rechnerisch korrekt gegen RTH_AXIS_FACTOR ────────────

def test_proposed_rth_bounds_are_correctly_rounded_and_floored():
    overrides = json.loads(OVERRIDES_PATH.read_text("utf-8"))["overrides"]
    for strategy, symbols in overrides.items():
        for symbol, entry in symbols.items():
            proposed = entry.get("proposed_rth_bounds")
            if not proposed:
                continue
            for param, (prop_lo, prop_hi) in proposed.items():
                active_lo, active_hi = entry[param]
                expected_lo = max(MIN_BARS_IN_TRADE_FLOOR, round(active_lo * RTH_AXIS_FACTOR))
                expected_hi = max(MIN_BARS_IN_TRADE_FLOOR, round(active_hi * RTH_AXIS_FACTOR))
                assert (prop_lo, prop_hi) == (expected_lo, expected_hi), (
                    f"{strategy}/{symbol}.{param}: {(prop_lo, prop_hi)} != "
                    f"{(expected_lo, expected_hi)}")


def test_optimizer_json_time_box_bars_axis_is_rth():
    cfg = json.loads(OPT_CFG_PATH.read_text("utf-8"))
    assert cfg["time_box_bars_axis"] == "rth"


def test_every_curated_block_has_axis_and_calibrated_in_run_id_keys():
    overrides = json.loads(OVERRIDES_PATH.read_text("utf-8"))["overrides"]
    for strategy, symbols in overrides.items():
        for symbol, entry in symbols.items():
            assert "axis" in entry, f"{strategy}/{symbol} missing 'axis'"
            assert "calibrated_in_run_id" in entry, f"{strategy}/{symbol} missing 'calibrated_in_run_id'"
