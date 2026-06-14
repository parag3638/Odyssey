from app.strategies.trailing_stop import evaluate, TrailingState

CFG = {"symbol": "TSLA", "initial_shares": 10, "stop_pct": 0.10, "trail_pct": 0.05,
       "ladder": [{"drop_pct": 0.20, "add_shares": 20}]}


def test_no_position_buys_initial_and_sets_floor():
    d = evaluate(CFG, TrailingState(qty=0, avg_entry_price=None, stop_floor=None, triggered_rungs=[]), price=100.0)
    assert len(d.buys) == 1 and d.buys[0].qty == 10 and d.buys[0].symbol == "TSLA"
    assert not d.sells
    assert round(d.new_stop_floor, 2) == 90.0  # 100 * (1 - stop_pct)


def test_floor_rises_but_never_falls():
    # price climbed to 200; trail floor = 190 which is above current floor 90
    d = evaluate(CFG, TrailingState(qty=10, avg_entry_price=100.0, stop_floor=90.0, triggered_rungs=[]), price=200.0)
    assert round(d.new_stop_floor, 2) == 190.0
    assert not d.sells and not d.buys
    # now price dips to 180; floor must NOT fall below 190
    d2 = evaluate(CFG, TrailingState(qty=10, avg_entry_price=100.0, stop_floor=190.0, triggered_rungs=[]), price=180.0)
    assert d2.new_stop_floor == 190.0
    assert len(d2.sells) == 1  # 180 <= 190 -> stop hit -> sell all


def test_stop_hit_sells_all():
    d = evaluate(CFG, TrailingState(qty=10, avg_entry_price=100.0, stop_floor=95.0, triggered_rungs=[]), price=94.0)
    assert len(d.sells) == 1 and d.sells[0].qty == 10


def test_ladder_buy_fires_once():
    # price dropped 20% below entry (100 -> 80); rung not yet triggered
    state = TrailingState(qty=10, avg_entry_price=100.0, stop_floor=70.0, triggered_rungs=[])
    d = evaluate(CFG, state, price=80.0)
    assert len(d.buys) == 1 and d.buys[0].qty == 20
    assert 0.20 in d.new_triggered_rungs
    # same rung already triggered -> no second ladder buy
    state2 = TrailingState(qty=30, avg_entry_price=93.0, stop_floor=70.0, triggered_rungs=[0.20])
    d2 = evaluate(CFG, state2, price=80.0)
    assert not d2.buys
