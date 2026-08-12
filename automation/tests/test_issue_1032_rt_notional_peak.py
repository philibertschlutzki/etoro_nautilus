"""Issue #1032 (Katalog #866) — ``rt_notional`` (Summe der Entry-Notionale ALLER FIFO-Matches eines
Round-Trips) ueberschaetzt das tatsaechliche maximale gleichzeitige Exposure fuer eine Position, die
innerhalb EINES Round-Trips mehrfach auf-/abgebaut wird (Pyramidisierung, Teilausstieg + Nachkauf) —
dasselbe Kapital wird recycelt, aber jede Wiederaufnahme zaehlt erneut in die Summe.
``_round_trip_notional_peak`` liefert stattdessen den Spitzenbestand ueber eine Sweep-Line.
"""
from automation.backtest_runner import _round_trip_notional_peak


def _match(pnl, exit_ts_ns, holding_ns, qty, notional):
    return (pnl, exit_ts_ns, holding_ns, qty, notional)


def test_pyramided_position_peak_is_strictly_below_summed_notional():
    """Kauf 100 @t0=0 → Verkauf 30 @t1=10 → Kauf 50 @t2=15 → Verkauf 120 @t3=20 (ein Round-Trip,
    FIFO). Drei Matches:
    Match 1: 30 Einheiten des ersten Kaufs, entry@t0=0, exit@t1=10 (holding_ns=10).
    Match 2: die verbleibenden 70 Einheiten des ersten Kaufs, entry@t0=0, exit@t3=20 (holding_ns=20).
    Match 3: 50 Einheiten des zweiten Kaufs, entry@t2=15, exit@t3=20 (holding_ns=5).

    Offene Notional ueber die Zeit (die Positionsgroesse aus dem Issue-Beispiel):
      [0, 10):  Match1(30) + Match2(70) offen = 100 (der urspruengliche Kauf 100)
      [10, 15): nur Match2(70) offen = 70 (nach Verkauf 30)
      [15, 20): Match2(70) + Match3(50) offen = 120 (nach Kauf 50 — der Spitzenbestand)
    Spitzenbestand = 120, strikt kleiner als die Summe aller Entry-Notionale (30+70+50=150)."""
    matches = [
        _match(pnl=1.0, exit_ts_ns=10, holding_ns=10, qty=30.0, notional=30.0),   # Match 1: t0..t1
        _match(pnl=2.0, exit_ts_ns=20, holding_ns=20, qty=70.0, notional=70.0),   # Match 2: t0..t3
        _match(pnl=3.0, exit_ts_ns=20, holding_ns=5, qty=50.0, notional=50.0),    # Match 3: t2..t3
    ]
    rt_notional = sum(m[4] for m in matches)
    assert rt_notional == 150.0

    peak = _round_trip_notional_peak(matches)
    assert peak == 120.0
    assert peak <= rt_notional


def test_single_match_peak_equals_summed_notional():
    """Ein Round-Trip ohne Pyramidisierung (ein einziger FIFO-Match): peak == rt_notional (kein
    Overlap, keine Ueberschaetzung moeglich)."""
    matches = [_match(pnl=5.0, exit_ts_ns=100, holding_ns=50, qty=10.0, notional=1000.0)]
    assert _round_trip_notional_peak(matches) == sum(m[4] for m in matches) == 1000.0


def test_sequential_non_overlapping_matches_peak_is_the_largest_single_match():
    """Zwei FIFO-Matches, die sich zeitlich NICHT ueberlappen (Match 2 beginnt erst, nachdem Match 1
    bereits geschlossen ist) — der Spitzenbestand ist das Maximum der EINZELNEN Notionale, nicht
    ihre Summe."""
    matches = [
        _match(pnl=1.0, exit_ts_ns=10, holding_ns=10, qty=1.0, notional=40.0),   # t0=0..t1=10
        _match(pnl=1.0, exit_ts_ns=30, holding_ns=10, qty=1.0, notional=60.0),   # t2=20..t3=30
    ]
    assert _round_trip_notional_peak(matches) == 60.0
    assert sum(m[4] for m in matches) == 100.0


def test_peak_never_exceeds_summed_notional_property():
    """Eigenschaftstest ueber mehrere Szenarien: rt_notional_peak <= rt_notional gilt immer
    (Akzeptanzkriterium #1032)."""
    scenarios = [
        [_match(1.0, 10, 10, 1.0, 30.0), _match(2.0, 20, 20, 1.0, 70.0), _match(3.0, 20, 5, 1.0, 50.0)],
        [_match(1.0, 5, 5, 1.0, 10.0)],
        [_match(1.0, 10, 10, 1.0, 40.0), _match(1.0, 30, 10, 1.0, 60.0)],
        [_match(1.0, 10, 10, 1.0, 25.0), _match(1.0, 10, 5, 1.0, 25.0), _match(1.0, 10, 2, 1.0, 25.0)],
    ]
    for matches in scenarios:
        rt_notional = sum(m[4] for m in matches)
        peak = _round_trip_notional_peak(matches)
        assert peak <= rt_notional + 1e-9


def test_empty_matches_peak_is_zero():
    assert _round_trip_notional_peak([]) == 0.0
