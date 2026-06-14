from app.data.capitol_trades import parse_capitol_trades, signal_hash

RAW = [
    {"txType": "buy", "txDate": "2026-05-30", "pubDate": "2026-06-01",
     "politician": {"firstName": "Michael", "lastName": "McCaul"},
     "asset": {"assetTicker": "NVDA"}, "value": "250K–500K", "_txId": "t1"},
    {"txType": "sell", "txDate": "2026-05-27", "pubDate": "2026-05-28",
     "politician": {"firstName": "Michael", "lastName": "McCaul"},
     "asset": {"assetTicker": "AMZN"}, "value": "15K–50K", "_txId": "t2"},
    # missing ticker -> dropped
    {"txType": "buy", "txDate": "2026-05-20", "pubDate": "2026-05-22",
     "politician": {"firstName": "X", "lastName": "Y"},
     "asset": {"assetTicker": None}, "value": "1K–15K", "_txId": "t3"},
]


def test_parse_normalizes_and_drops_unticketed():
    out = parse_capitol_trades(RAW)
    assert len(out) == 2
    s = out[0]
    assert s["politician"] == "Michael McCaul"
    assert s["symbol"] == "NVDA"
    assert s["tx_type"] == "buy"
    assert s["tx_date"] == "2026-05-30"
    assert s["disclosed_date"] == "2026-06-01"
    assert s["amount_range"] == "250K–500K"
    assert len(s["hash"]) == 40  # sha1 hexdigest


def test_hash_is_stable_and_distinct():
    h1 = signal_hash("Michael McCaul", "NVDA", "buy", "2026-05-30", "250K–500K")
    h2 = signal_hash("Michael McCaul", "NVDA", "buy", "2026-05-30", "250K–500K")
    h3 = signal_hash("Michael McCaul", "NVDA", "sell", "2026-05-30", "250K–500K")
    assert h1 == h2 and h1 != h3
