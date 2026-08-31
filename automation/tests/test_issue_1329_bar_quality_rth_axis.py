"""Issue #1329 (Katalog #1323-1329, P1) — Der Bar-Qualitaets-Preflight (``sweep.
_load_symbol_bar_quality_sample``) resamplete bislang UNGEFILTERT auf eine reine 24/7-
Kalenderstunden-Achse, obwohl ``check_tick_population``s ``n_ticks_after_session_filter``-Feld
(Issue #1298) fuer DIESELBE Tickmenge desselben Laufs bereits seit #1275 auf der RTH-Session-Achse
misst — zwei Meta-Checks pruefen faktisch zwei verschiedene Populationen fuer dasselbe Symbol.

Fix. ``_load_symbol_bar_quality_sample`` wendet vor dem Resampling dieselbe
``session_hours_by_asset_class``-Maske an wie ``probe_symbol_tick_population`` (ueber dasselbe
lokale Helferpaar ``_resolve_session_window_utc``/``_is_ts_ns_within_session_utc``). Die
konfigurierten Schwellenwerte selbst werden NICHT neu kalibriert — nur die Achse der Messung wird
korrigiert.
"""
from automation.optimizer import sweep

_NS_PER_HOUR = 3_600_000_000_000
# 2024-01-01T00:00:00Z ist ein Montag -- fixer Anker, damit Wochentags-/Wochenend-Ticks
# deterministisch platziert werden koennen.
_MONDAY_UTC_NS = 1_704_067_200_000_000_000
_EQUITY_SESSION = {"EQUITY": {"open_utc": "14:30", "close_utc": "21:00"}}


def _write_quote_tick_parquet(tmp_path, symbol, ts_ns_list, price=100.0):
    # Issue #1213 — bid_price/ask_price sind im echten Katalog rohe pa.binary(16)-FSB16-Werte.
    import pyarrow as pa
    import pyarrow.parquet as pq
    from automation._serde import encode_price_fsb16
    d = tmp_path / "data" / "quote_tick" / symbol
    d.mkdir(parents=True, exist_ok=True)
    n = len(ts_ns_list)
    _FSB16 = pa.binary(16)
    table = pa.table({
        "bid_price": pa.array([encode_price_fsb16(price, 2)] * n, type=_FSB16),
        "ask_price": pa.array([encode_price_fsb16(price + 0.02, 2)] * n, type=_FSB16),
        "ts_event": pa.array(ts_ns_list, type=pa.int64()),
    })
    pq.write_table(table, str(d / "data.parquet"))


def _in_session_ticks_for_five_weekdays():
    """Je Wochentag (Mo-Fr) sechs Ticks zu vollen Stunden zwischen 15:00 und 20:00 UTC --
    vollstaendig innerhalb des [14:30, 21:00)-Session-Fensters."""
    ts = []
    for day in range(5):
        day_start = _MONDAY_UTC_NS + day * 24 * _NS_PER_HOUR
        for hour in (15, 16, 17, 18, 19, 20):
            ts.append(day_start + hour * _NS_PER_HOUR)
    return ts


def _out_of_session_ticks_for_five_weekdays():
    """Je Wochentag ein Tick weit ausserhalb des Session-Fensters (03:00 UTC), PLUS ein
    Wochenend-Tick (Samstag) -- keiner davon darf Zaehler oder Nenner beeinflussen."""
    ts = []
    for day in range(5):
        day_start = _MONDAY_UTC_NS + day * 24 * _NS_PER_HOUR
        ts.append(day_start + 3 * _NS_PER_HOUR)  # 03:00 UTC, ausserhalb [14:30, 21:00).
    saturday_start = _MONDAY_UTC_NS + 5 * 24 * _NS_PER_HOUR
    ts.append(saturday_start + 16 * _NS_PER_HOUR)  # Samstag 16:00 UTC -- im Fenster, aber Wochenende.
    return ts


def test_out_of_session_ticks_never_affect_bar_coverage_ratio_or_n_sample_ticks(tmp_path):
    """Akzeptanzkriterium 2 (Issue #1329) — eine synthetische Tick-Serie mit bewusst Ticks
    ausserhalb der Session-Fenster darf weder Zaehler noch Nenner von bar_coverage_ratio
    beeinflussen, wenn eine Session-Maske konfiguriert ist."""
    in_session = _in_session_ticks_for_five_weekdays()
    augmented = sorted(in_session + _out_of_session_ticks_for_five_weekdays())

    _write_quote_tick_parquet(tmp_path / "baseline", "AAPL.ETORO", in_session)
    _write_quote_tick_parquet(tmp_path / "augmented", "AAPL.ETORO", augmented)

    sample_baseline = sweep._load_symbol_bar_quality_sample(
        "AAPL.ETORO", catalog_path=tmp_path / "baseline",
        session_hours_by_asset_class=_EQUITY_SESSION, asset_class_key="EQUITY")
    sample_augmented = sweep._load_symbol_bar_quality_sample(
        "AAPL.ETORO", catalog_path=tmp_path / "augmented",
        session_hours_by_asset_class=_EQUITY_SESSION, asset_class_key="EQUITY")

    assert sample_baseline is not None and sample_augmented is not None
    assert sample_augmented["n_sample_ticks"] == sample_baseline["n_sample_ticks"]
    assert sample_augmented["bar_coverage_ratio"] == sample_baseline["bar_coverage_ratio"]
    assert sample_augmented["highs"] == sample_baseline["highs"]
    assert sample_augmented["lows"] == sample_baseline["lows"]
    assert sample_augmented["closes"] == sample_baseline["closes"]


def test_n_sample_ticks_matches_probe_symbol_tick_population_after_session_filter(tmp_path):
    """Akzeptanzkriterium 1 (Issue #1329) — fuer ein EQUITY-Symbol stimmt
    BAR_QUALITY_PROFILE.n_sample_ticks (hier: sample['n_sample_ticks']) mit
    check_tick_population.n_ticks_after_session_filter ueberein -- dieselbe Population desselben
    Laufs, gemessen von zwei unabhaengigen Preflight-Funktionen."""
    ts_list = sorted(
        _in_session_ticks_for_five_weekdays() + _out_of_session_ticks_for_five_weekdays())
    _write_quote_tick_parquet(tmp_path, "MSFT.ETORO", ts_list)

    sample = sweep._load_symbol_bar_quality_sample(
        "MSFT.ETORO", catalog_path=tmp_path,
        session_hours_by_asset_class=_EQUITY_SESSION, asset_class_key="EQUITY")
    probe = sweep.probe_symbol_tick_population(
        "MSFT.ETORO", catalog_path=tmp_path,
        session_hours_by_asset_class=_EQUITY_SESSION, asset_class_key="EQUITY")

    assert sample is not None and probe is not None
    assert sample["n_sample_ticks"] == probe["n_ticks_after_session_filter"]
    # Beide muessen tatsaechlich gefiltert haben (Kontrolle gegen einen no-op-Test): weniger als
    # die rohe Tick-Zahl, da die Out-of-Session-/Wochenend-Ticks herausfallen.
    assert probe["n_ticks_after_session_filter"] < probe["n_ticks_raw"]


def test_without_session_config_behaviour_is_unchanged_fail_open(tmp_path):
    """Regressionsschutz — fehlende session_hours_by_asset_class/asset_class_key (der bisherige
    Aufruf-Vertrag) lässt ``df`` unveraendert (fail-open, bit-identisches Alt-Verhalten): alle
    Ticks (auch ausserhalb jeder Session/an Wochenenden) zaehlen weiterhin mit."""
    ts_list = sorted(
        _in_session_ticks_for_five_weekdays() + _out_of_session_ticks_for_five_weekdays())
    _write_quote_tick_parquet(tmp_path, "CRYPTO_LIKE.ETORO", ts_list)

    sample_no_kwargs = sweep._load_symbol_bar_quality_sample(
        "CRYPTO_LIKE.ETORO", catalog_path=tmp_path)
    sample_explicit_none = sweep._load_symbol_bar_quality_sample(
        "CRYPTO_LIKE.ETORO", catalog_path=tmp_path,
        session_hours_by_asset_class=_EQUITY_SESSION, asset_class_key=None)

    assert sample_no_kwargs is not None and sample_explicit_none is not None
    assert sample_no_kwargs["n_sample_ticks"] == len(ts_list)
    assert sample_explicit_none["n_sample_ticks"] == len(ts_list)
