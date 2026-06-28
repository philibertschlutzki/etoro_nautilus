"""Issue #466 (Audit #467) — `splits=N` muss einen ECHTEN rollenden Walk-Forward erzeugen, NICHT
zu einem singulären, kontiguierlichen IS/OOS-Block degenerieren.

Geprüft wird die reine Single-Source-of-Truth-Geometrie ``backtest_runner.compute_fold_boundaries``
(die alle vier vormals inline duplizierten Split-Stellen speist). KEIN ``sys.modules``-Mocking mehr:
der frühere Modul-Level-MagicMock auf ``pyarrow``/``nautilus`` war (a) selbst-brechend (re-import von
pandas traf den gemockten pyarrow) und (b) eine prozessweite Verseuchung, die ~10 fremde Tests
kippte. ``backtest_runner`` wird direkt importiert (wie in test_issue_464/465).
"""
import pytest

from automation.backtest_runner import compute_fold_boundaries

DAY_NS = 86400 * 1_000_000_000

# Produktions-Geometrie aus automation/config/backtest.json
WF = {"is_window_days": 180, "oos_window_days": 45, "splits": 4, "embargo_period_days": 21}


def test_splits_produce_distinct_rolling_folds():
    """splits=4 ⇒ GENAU vier Folds, deren IS-Start je um EIN OOS-Fenster vorrollt (echtes Rolling)."""
    start_ns = 1_700_000_000 * 1_000_000_000
    folds = compute_fold_boundaries(start_ns, WF)

    assert len(folds) == 4, "splits=4 muss vier distinkte Folds liefern (keine Degeneration)"

    is_starts = [f[0] for f in folds]
    # IS-Start rollt je Fold um genau oos_window_days vor.
    for k in range(1, len(folds)):
        assert is_starts[k] - is_starts[k - 1] == WF["oos_window_days"] * DAY_NS

    # Die OOS-Start-Anker sind strikt monoton steigend ⇒ vier verschiedene Fenster, kein Single-Split.
    oos_starts = [f[1] for f in folds]
    assert oos_starts == sorted(oos_starts)
    assert len(set(oos_starts)) == 4


def test_embargo_purges_gap_between_is_and_oos():
    """Embargo > 0 ⇒ oos_start = is_start + is_window + embargo (Purge gegen Lookback-Leakage)."""
    start_ns = 0
    folds = compute_fold_boundaries(start_ns, WF)
    for is_start, oos_start, oos_end in folds:
        assert oos_start == is_start + (WF["is_window_days"] + WF["embargo_period_days"]) * DAY_NS
        # oos_end ist am IS-Start verankert (nicht am embargoten oos_start): Embargo VERKÜRZT das
        # effektive OOS-Fenster, verschiebt es nicht.
        assert oos_end == is_start + (WF["is_window_days"] + WF["oos_window_days"]) * DAY_NS
        # effektive OOS-Länge = oos_window - embargo (> 0 bei valider Config)
        assert oos_end - oos_start == (WF["oos_window_days"] - WF["embargo_period_days"]) * DAY_NS


def test_embargo_makes_oos_windows_non_contiguous():
    """Mit Embargo grenzen die OOS-Fenster NICHT mehr lückenlos aneinander (keine Union zu EINEM
    kontiguierlichen Block — exakt der in #466 beschriebene Degenerationsfall ist ausgeschlossen)."""
    folds = compute_fold_boundaries(0, WF)
    for k in range(1, len(folds)):
        prev_oos_end = folds[k - 1][2]
        cur_oos_start = folds[k][1]
        # Lücke = embargo (21d) ⇒ strikt positiv, also kein nahtloses Tiling.
        assert cur_oos_start > prev_oos_end
        assert cur_oos_start - prev_oos_end == WF["embargo_period_days"] * DAY_NS


def test_last_fold_aligns_with_walk_forward_window_end():
    """Der OOS-Ende des letzten Folds == start + is_window + splits*oos_window — exakt die äussere
    Fenster-Grenze aus trial_config.compute_walk_forward_window (Geometrie-Konsistenz #457/#466)."""
    start_ns = 1_700_000_000 * 1_000_000_000
    folds = compute_fold_boundaries(start_ns, WF)
    expected_end = start_ns + (WF["is_window_days"] + WF["splits"] * WF["oos_window_days"]) * DAY_NS
    assert folds[-1][2] == expected_end


def test_trade_classification_excludes_embargo_and_is():
    """End-to-End-Semantik der Grenzen: ein Trade im IS und einer im Embargo zählen NICHT als OOS,
    ein Trade im echten OOS schon (replicates die Worker-Klassifikation split_oos_start<=ts<split_oos_end)."""
    start_ns = 1_700_000_000 * 1_000_000_000
    folds = compute_fold_boundaries(start_ns, WF)
    is_start, oos_start, oos_end = folds[0]

    ts_is = is_start + (WF["is_window_days"] - 1) * DAY_NS          # kurz vor IS-Ende
    ts_embargo = is_start + WF["is_window_days"] * DAY_NS + 5 * DAY_NS  # innerhalb der 21d-Purge
    ts_oos = oos_start + 5 * DAY_NS                                  # echtes OOS

    def _is_oos(ts):
        return any(s <= ts < e for _is, s, e in folds)

    assert _is_oos(ts_oos) is True
    assert _is_oos(ts_embargo) is False
    assert _is_oos(ts_is) is False


def test_single_split_is_not_silently_assumed():
    """splits=1 liefert genau einen Fold (kein impliziter Mehr-Fold); splits=N liefert N Folds.
    Stellt sicher, dass `splits` die Fold-Anzahl tatsächlich steuert (Anti-Degeneration)."""
    base = dict(is_window_days=180, oos_window_days=45, embargo_period_days=0)
    for n in (1, 2, 4, 6):
        folds = compute_fold_boundaries(0, {**base, "splits": n})
        assert len(folds) == n
