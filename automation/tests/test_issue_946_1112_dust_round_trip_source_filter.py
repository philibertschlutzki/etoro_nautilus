"""Issue #946/#1112 (Katalog #960) — Dust-Round-Trips schlagen bis in die berichtete Expectancy
durch.

Symptom: SqueezeBreakout/PLTR: ``holdout_expectancy_notional_degenerate_count = 1`` bei 8 Trades
(12,5 %) erzeugt −171,34 bps gegen kapitalgewichtete −21,57 bps. ``check_dust_round_trip_share``
(severity ``high``) feuert auf mehreren Studies mit Anteil > 1 %.

Root-Cause: der #1031-Median-Notional-Boden (5 %) sass an der KONSUMSTELLE der Expectancy
(``_calculate_stats``), nicht an der QUELLE des Round-Trip-Stroms. Ein Leg mit ~1e-13 Notional
blieb in der notional-gewichteten Mittelung und dominierte sie.

Fix: ``_filter_dust_round_trips`` verwirft Round-Trips unterhalb ``dust_notional_floor_frac ·
median(notional)`` AN DER QUELLE (``extract_metrics``s ``rt_*_with_ts``-Listen, unmittelbar nach
dem FIFO-Matching), bevor JEDER Konsument (``_calculate_stats``, ``_aggregate_exit_telemetry``,
Kostenstress, Portfolio-/Fold-Metriken) sie sieht — alle stromabwärts abgeleiteten Grössen sehen
dieselbe bereinigte Menge.

Hinweis: dieses Modul importiert ``automation.backtest_runner``, das ``nautilus_trader`` (>=1.226,
<2.0, Python >=3.12) voraussetzt. In Sandboxes ohne exakt gepinnte Version (dieses Environment:
Python 3.11) ist dieser Test NICHT ausfuehrbar (Collection-Error, dieselbe Einschraenkung wie
``test_issue_1032_rt_notional_peak.py``/``test_issue_1070_1081_stop_distance_and_cost_stress.py``)
— er laeuft in der realen Entwicklungsumgebung.
"""
from automation.backtest_runner import _filter_dust_round_trips


def _rt(pnl, exit_ts_ns, holding_ns, qty):
    return (pnl, exit_ts_ns, holding_ns, qty)


def test_dust_leg_is_filtered_from_all_four_parallel_lists():
    """Reproduziert SqueezeBreakout/PLTR: 7 normale Round-Trips + 1 Dust-Leg (Notional ~1e-13)."""
    rt_pnls = [_rt(-10.0, i, 3600, 1.0) for i in range(7)] + [_rt(500.0, 99, 3600, 1.0)]
    rt_notionals = [(1000.0, i) for i in range(7)] + [(1e-13, 99)]
    rt_peaks = [1000.0] * 7 + [1e-13]
    rt_meta = [{"exit_reason": "TIME_BOX"} for _ in range(7)] + [{"exit_reason": "UNKNOWN"}]

    kept_pnls, kept_notionals, kept_peaks, kept_meta, discarded, discarded_meta = _filter_dust_round_trips(
        rt_pnls, rt_notionals, rt_peaks, rt_meta)

    assert len(kept_pnls) == 7
    assert len(kept_notionals) == 7
    assert len(kept_peaks) == 7
    assert len(kept_meta) == 7
    assert discarded == [(1e-13, 99)]
    assert discarded_meta == [{"exit_reason": "UNKNOWN"}]
    # Der Dust-Leg (pnl=500, exit_ts=99) ist in KEINER der vier gefilterten Listen mehr enthalten.
    assert all(ts != 99 for _pnl, ts, _ht, _q in kept_pnls)
    assert all(reason.get("exit_reason") != "UNKNOWN" for reason in kept_meta)


def test_no_dust_below_floor_returns_lists_unchanged():
    rt_pnls = [_rt(10.0, i, 3600, 1.0) for i in range(5)]
    rt_notionals = [(1000.0 + i * 10, i) for i in range(5)]
    rt_peaks = [1000.0] * 5
    rt_meta = [{"exit_reason": "TIME_BOX"} for _ in range(5)]

    kept_pnls, kept_notionals, kept_peaks, kept_meta, discarded, discarded_meta = _filter_dust_round_trips(
        rt_pnls, rt_notionals, rt_peaks, rt_meta)

    assert kept_pnls == rt_pnls
    assert kept_notionals == rt_notionals
    assert discarded == []


def test_fewer_than_two_positive_notionals_is_a_no_op():
    rt_pnls = [_rt(10.0, 0, 3600, 1.0)]
    rt_notionals = [(1000.0, 0)]
    kept_pnls, kept_notionals, kept_peaks, kept_meta, discarded, discarded_meta = _filter_dust_round_trips(
        rt_pnls, rt_notionals, [1000.0], [{"exit_reason": "TIME_BOX"}])
    assert kept_pnls == rt_pnls
    assert discarded == []


def test_custom_floor_fraction_is_respected():
    """Bei einem Boden von 50% des Medians (statt Default 5%) wird auch ein moderat kleiner Leg
    (200 gegen Median 1000) als Dust erkannt."""
    rt_pnls = [_rt(1.0, 0, 3600, 1.0), _rt(2.0, 1, 3600, 1.0), _rt(3.0, 2, 3600, 1.0)]
    rt_notionals = [(1000.0, 0), (1000.0, 1), (200.0, 2)]
    rt_peaks = [1000.0, 1000.0, 200.0]
    rt_meta = [{}, {}, {}]

    _, kept_notionals, _, _, discarded, _discarded_meta = _filter_dust_round_trips(
        rt_pnls, rt_notionals, rt_peaks, rt_meta, dust_notional_floor_frac=0.5)

    assert len(kept_notionals) == 2
    assert discarded == [(200.0, 2)]


def test_multiple_dust_legs_all_discarded_and_counted():
    rt_pnls = [_rt(10.0, i, 3600, 1.0) for i in range(10)] + [
        _rt(500.0, 100, 3600, 1.0), _rt(-300.0, 101, 3600, 1.0)]
    rt_notionals = [(1000.0, i) for i in range(10)] + [(1e-12, 100), (5e-13, 101)]
    rt_peaks = [1000.0] * 10 + [1e-12, 5e-13]
    rt_meta = [{} for _ in range(10)] + [{}, {}]

    kept_pnls, _, _, _, discarded, discarded_meta = _filter_dust_round_trips(
        rt_pnls, rt_notionals, rt_peaks, rt_meta)

    assert len(kept_pnls) == 10
    assert len(discarded) == 2
    assert len(discarded_meta) == 2
