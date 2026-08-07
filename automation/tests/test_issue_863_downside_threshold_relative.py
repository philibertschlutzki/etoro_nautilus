"""Issue #863 (P1, Katalog #862-#865, GitHub-Issue #759) — `sortino_min_downside_observations = 30`
ist eine flache Konstante gegen dieselbe geschrumpfte Achse wie #862.

Root-Cause: derselbe absolute Default (30) wurde VOR #823 gewählt (als `n_periods` noch die volle
Bar-Achse war). Für eine hochselektive Strategie wie SqueezeBreakout (Median 27 informative
Perioden je OOS-Fenster) ist die Schwelle STRUKTURELL unerreichbar — sie kann nicht durch bessere
Parameter erfüllt werden, nur durch häufigeres Handeln, was die Strategie definitionsgemäss nicht
tut (372 von 408 `SORTINO_INSUFFICIENT_DOWNSIDE`-Events eines Referenzlaufs betrafen
SqueezeBreakout).

Fix:
1. `sortino_min_downside_observations` akzeptiert einen Wert in `(0, 1]` als Mindest-ANTEIL an
   `n_periods` (analog #677s relativem `oos_min_evaluable_folds`). Neuer Default 0.5. Ein Wert
   `>= 1` bleibt der absolute Zähler (Legacy).
2. `sortino_min_periods_absolute` (Default 20) — eine DAVON UNABHÄNGIGE harte Untergrenze für jede
   Sortino-Schätzung.
3. `downside_obs`/`n_periods` sind bereits als Trial-`user_attrs` gestempelt (seit #845).
"""
import pandas as pd
import pytest
from unittest.mock import patch

from automation.backtest_runner import _calculate_stats


def _mtm_from_rets(rets, start=1000.0):
    idx = pd.date_range("2025-01-01", periods=len(rets) + 1, freq="1h", tz="UTC")
    vals = [start]
    for r in rets:
        vals.append(vals[-1] * (1.0 + r))
    return pd.Series(vals, index=idx)


def _stats_for(rets, *, min_downside_obs, min_periods_absolute, guard_min_periods=None):
    pnl_list = [1.0 if r >= 0 else -1.0 for r in rets]
    holds = [(3600 * 10**9, 1.0)] * len(rets)
    series = _mtm_from_rets(rets)
    with patch("automation.backtest_runner._read_sortino_min_downside_observations",
               return_value=min_downside_obs), \
         patch("automation.backtest_runner._read_sortino_min_periods_absolute",
               return_value=min_periods_absolute), \
         patch("automation.backtest_runner._read_sortino_numeric_guard_min_periods",
               return_value=guard_min_periods), \
         patch("automation.backtest_runner._read_sortino_numeric_guard", return_value=1e9):
        return _calculate_stats(pnl_list, holds, 1000.0, mtm_series=series,
                                min_trades_for_sortino=1)


def _rets_with_downside_fraction(n_periods, downside_frac, *, seed=0):
    import random
    rng = random.Random(seed)
    n_downside = round(n_periods * downside_frac)
    n_downside = min(max(n_downside, 0), n_periods)
    rets = [-abs(rng.uniform(0.001, 0.02)) for _ in range(n_downside)]
    rets += [abs(rng.uniform(0.001, 0.02)) for _ in range(n_periods - n_downside)]
    rng.shuffle(rets)
    return rets


# ── AK #863: relativer Anteil statt absoluter Zähler ─────────────────────────────────────────────
def test_downside_obs_24_of_n_periods_27_is_no_longer_insufficient():
    """Akzeptanzkriterium — downside_obs=24, n_periods=27 (Anteil 0.89) gilt NICHT mehr als
    insufficient (vorher: 24 < 30 absolut -> insufficient)."""
    rets = _rets_with_downside_fraction(27, 24 / 27, seed=1)
    stats = _stats_for(rets, min_downside_obs=0.5, min_periods_absolute=20)
    codes = [d["code"] for d in stats["inference_diagnostics"]]
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" not in codes
    assert stats["sortino_ratio"] is not None


def test_downside_obs_3_of_n_periods_200_is_shrunk_not_rejected():
    """Issue #944 (Pitfall #296) — downside_obs=3, n_periods=200 (Anteil 0.015) wird NICHT MEHR
    verworfen (das war der Anti-Selektions-Filter: je weniger Verlustperioden, desto sicherer die
    Verwerfung — bei nur 3 Verlustperioden von 200 ist das eine SEHR GUTE Strategie). Stattdessen
    wird dd_dev Richtung der Gesamtstreuung geschrumpft, der Trial bleibt bewertbar."""
    rets = _rets_with_downside_fraction(200, 3 / 200, seed=2)
    stats = _stats_for(rets, min_downside_obs=0.5, min_periods_absolute=20)
    codes = [d["code"] for d in stats["inference_diagnostics"]]
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" not in codes
    assert "SORTINO_DOWNSIDE_SHRUNK" in codes
    assert stats["sortino_ratio"] is not None


def test_n_periods_12_rejected_via_absolute_floor():
    """Akzeptanzkriterium — ein Trial mit n_periods=12 wird über sortino_min_periods_absolute
    verworfen, unabhängig vom (hier sogar 100%) Downside-Anteil."""
    rets = _rets_with_downside_fraction(12, 1.0, seed=3)
    stats = _stats_for(rets, min_downside_obs=0.5, min_periods_absolute=20)
    codes = [d["code"] for d in stats["inference_diagnostics"]]
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" in codes
    assert stats["sortino_ratio"] is None
    detail = next(d["detail"] for d in stats["inference_diagnostics"]
                 if d["code"] == "SORTINO_INSUFFICIENT_DOWNSIDE")
    assert "sortino_min_periods_absolute" in detail


def test_absolute_mode_still_available_via_value_over_one():
    """Ein konfigurierter Wert >= 1 bleibt der absolute Zähler für die Schrumpfungs-Schwelle
    (Legacy-Config-Semantik unverändert). Issue #944 — die Konsequenz beim Unterschreiten ist
    seither Schrumpfung statt Verwerfung (SORTINO_DOWNSIDE_SHRUNK statt
    SORTINO_INSUFFICIENT_DOWNSIDE)."""
    rets = _rets_with_downside_fraction(100, 0.25, seed=4)  # 25 downside von 100
    stats_pass = _stats_for(rets, min_downside_obs=20, min_periods_absolute=20)
    codes_pass = [d["code"] for d in stats_pass["inference_diagnostics"]]
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" not in codes_pass
    assert "SORTINO_DOWNSIDE_SHRUNK" not in codes_pass

    stats_shrunk = _stats_for(rets, min_downside_obs=30, min_periods_absolute=20)
    codes_shrunk = [d["code"] for d in stats_shrunk["inference_diagnostics"]]
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" not in codes_shrunk
    assert "SORTINO_DOWNSIDE_SHRUNK" in codes_shrunk


def test_downside_obs_and_n_periods_are_stamped_as_trial_user_attrs():
    """Akzeptanzkriterium — oos_downside_obs/oos_n_periods sind je Trial persistiert (bereits seit
    #845 implementiert; Regressionswächter für #863)."""
    import inspect
    from automation.optimizer import run_optimization as ro
    source = inspect.getsource(ro.make_symbol_objective)
    assert 'set_user_attr("oos_downside_obs"' in source
    assert 'set_user_attr("oos_n_periods"' in source


# ── invariants.check_guard_reference_coherence (#862) auch fuer #863 relevant, siehe dortige Tests.
def test_default_config_values_are_wired_end_to_end(tmp_path, monkeypatch):
    """Ohne jede Mock-Ueberschreibung liest _calculate_stats den ECHTEN Default (0.5 relativ,
    20 absolut) aus tournament.json."""
    import automation.backtest_runner as br
    monkeypatch.setattr(br, "_sortino_min_downside_observations_cache", None)
    monkeypatch.setattr(br, "_sortino_min_periods_absolute_cache", None)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    assert br._read_sortino_min_downside_observations() == 0.5
    assert br._read_sortino_min_periods_absolute() == 20
