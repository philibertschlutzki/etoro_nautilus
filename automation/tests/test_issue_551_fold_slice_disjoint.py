"""Issue #551 — Fold-MtM-Slices dürfen sich am Rand nicht überlappen.

``pandas.loc[a:b]`` ist auf BEIDEN Seiten geschlossen. Bei kontinuierlichen Folds (Embargo=0,
``oos_end_k == oos_start_{k+1}``) lag der Grenz-Bar damit in ZWEI benachbarten Fold-Segmenten
und wurde im compoundierten Return doppelt gezählt. Der Fix schneidet halb-offen ``[s, e)``,
konsistent zur Trade-Klassifikation.
"""
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch

import automation.backtest_runner as br

DAY_NS = 86400 * 1_000_000_000
HOUR_NS = 3600 * 1_000_000_000


def _fill(iid, price, qty, ts_ns, side):
    return {"instrument_id": iid, "price": price, "quantity": qty,
            "ts_event": ts_ns, "side": side, "commission": 0}


def _build_engine(fills_df):
    engine = MagicMock()
    engine.trader.generate_fills_report.return_value = fills_df
    return engine


def test_contiguous_fold_frames_have_no_shared_boundary_bar():
    start_ns = 1000 * DAY_NS
    # Embargo=0 ⇒ kontinuierliche Folds; Grenz-Bar bei start+3d liegt sonst in Fold 0 UND 1.
    wf = {"is_window_days": 2, "oos_window_days": 1, "splits": 2, "embargo_period_days": 0}

    # Stündliche Equity-Kurve über [start, start+4d], inkl. eines Bars EXAKT auf der Fold-Grenze.
    idx_ns = list(range(start_ns, start_ns + 4 * DAY_NS + 1, HOUR_NS))
    boundary_ns = start_ns + 3 * DAY_NS
    assert boundary_ns in idx_ns, "Testaufbau: es MUSS einen Bar exakt auf der Fold-Grenze geben"
    mtm = pd.Series(np.linspace(10000.0, 11000.0, len(idx_ns)),
                    index=pd.to_datetime(idx_ns, unit="ns"))

    # Je ein Round-Trip in Fold 0 ([start+2d, start+3d)) und Fold 1 ([start+3d, start+4d)).
    f0_entry = start_ns + 2 * DAY_NS + HOUR_NS
    f0_exit = start_ns + 2 * DAY_NS + 2 * HOUR_NS
    f1_entry = start_ns + 3 * DAY_NS + HOUR_NS
    f1_exit = start_ns + 3 * DAY_NS + 2 * HOUR_NS
    fills = pd.DataFrame([
        _fill("BTC.X", 100.0, 1.0, f0_entry, "BUY"),
        _fill("BTC.X", 110.0, 1.0, f0_exit, "SELL"),
        _fill("BTC.X", 100.0, 1.0, f1_entry, "BUY"),
        _fill("BTC.X", 120.0, 1.0, f1_exit, "SELL"),
    ])
    engine = _build_engine(fills)

    captured_frames = []
    real_stats = br._calculate_stats

    def spy(*args, **kwargs):
        if kwargs.get("mtm_frames") is not None:
            captured_frames.append(kwargs["mtm_frames"])
        return real_stats(*args, **kwargs)

    with patch.object(br, "_calculate_stats", side_effect=spy):
        br.extract_metrics(engine, 10000.0, None, wf, start_ns, mtm_series=mtm)

    assert captured_frames, "OOS-_calculate_stats mit mtm_frames wurde nicht aufgerufen"
    frames = captured_frames[0]
    assert len(frames) >= 2, "Beide OOS-Folds sollten ein nicht-leeres Segment liefern"

    # Akzeptanzkriterium: kein Bar-Timestamp erscheint in zwei Fold-Segmenten.
    concat_index = pd.concat(frames).index
    assert concat_index.is_unique, "Grenz-Bar doppelt in benachbarten Fold-Segmenten (Überlappung)"
    # Σ len(fold_segment) == len(oos_mtm) (keine Überlappung).
    assert sum(len(f) for f in frames) == len(concat_index.unique())
    # Der Grenz-Bar gehört genau zu Fold 1 (halb-offene Konvention: Start inklusiv, Ende exklusiv).
    boundary_dt = pd.to_datetime(boundary_ns, unit="ns")
    assert boundary_dt in frames[1].index
    assert boundary_dt not in frames[0].index
