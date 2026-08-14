from app.services.major_movers import build_major_movers


def test_build_major_movers_ranks_large_caps_and_filters_bad_data():
    tickers = [
        {"symbol": "AAPL", "name": "Apple"},
        {"symbol": "MSFT", "name": "Microsoft"},
        {"symbol": "NVDA", "name": "NVIDIA"},
        {"symbol": "AMZN", "name": "Amazon"},
        {"symbol": "SPIKE", "name": "Bad spike"},
        {"symbol": "PENNY", "name": "Penny stock"},
        {"symbol": "BROKEN", "name": "Broken quote"},
    ]
    quotes = {
        "AAPL": {"price": 220, "change_pct": 2.5},
        "MSFT": {"price": 410, "change_pct": -1.2},
        "NVDA": {"price": 180, "change_pct": 5.1},
        "AMZN": {"price": 215, "change_pct": -3.4},
        "SPIKE": {"price": 10, "change_pct": 348.09},
        "PENNY": {"price": 0.25, "change_pct": 12.0},
        "BROKEN": {"price": 20, "change_pct": float("nan")},
    }

    result = build_major_movers(tickers, quotes)

    assert [row["symbol"] for row in result["gainers"]] == ["NVDA", "AAPL"]
    assert [row["symbol"] for row in result["losers"]] == ["AMZN", "MSFT"]
    assert result["gainers"][0]["name"] == "NVIDIA"
