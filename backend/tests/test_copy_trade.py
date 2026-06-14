from app.strategies.copy_trade import evaluate_copy

SIGS = [
    {"hash": "h1", "symbol": "NVDA", "tx_type": "buy"},
    {"hash": "h2", "symbol": "AMZN", "tx_type": "sell"},
    {"hash": "h3", "symbol": "GOOGL", "tx_type": "buy"},
]


def test_buys_new_buy_signals_sized_by_notional():
    d = evaluate_copy(signals=SIGS, held={}, copied_hashes=set(),
                      prices={"NVDA": 100.0, "GOOGL": 200.0},
                      per_trade_notional=1000.0, follow_buys=True, follow_sells=True)
    syms = {b.symbol: b.qty for b in d.buys}
    assert syms["NVDA"] == 10  # 1000/100
    assert syms["GOOGL"] == 5  # 1000/200
    assert "h1" in d.new_copied_hashes and "h3" in d.new_copied_hashes


def test_skips_already_copied():
    d = evaluate_copy(signals=SIGS, held={}, copied_hashes={"h1", "h3"},
                      prices={"NVDA": 100.0, "GOOGL": 200.0},
                      per_trade_notional=1000.0)
    assert not d.buys


def test_sells_only_held_symbols():
    d = evaluate_copy(signals=SIGS, held={"AMZN": 4}, copied_hashes=set(),
                      prices={"NVDA": 100.0, "GOOGL": 200.0, "AMZN": 50.0},
                      per_trade_notional=1000.0)
    assert len(d.sells) == 1 and d.sells[0].symbol == "AMZN" and d.sells[0].qty == 4
    assert "h2" in d.new_copied_hashes


def test_buy_without_price_is_deferred():
    d = evaluate_copy(signals=[{"hash": "h9", "symbol": "TSLA", "tx_type": "buy"}],
                      held={}, copied_hashes=set(), prices={}, per_trade_notional=1000.0)
    assert not d.buys and "h9" not in d.new_copied_hashes  # retried next tick
