from nautilus_trader.persistence.catalog import ParquetDataCatalog

catalog = ParquetDataCatalog("data/nautilus")
# Nimm ein Instrument, das garantiert geladen wurde
ticks = catalog.quote_ticks("GOOGL.ETORO") 

print(f"Anzahl Ticks: {len(ticks)}")
if len(ticks) > 0:
    print(f"Erster Bid Preis: {ticks[0].bid_price}")
    print(f"Erster Ask Preis: {ticks[0].ask_price}")