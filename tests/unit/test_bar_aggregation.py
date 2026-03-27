from engines.pipeline import aggregate_bars


def test_aggregate_4_one_hour_bars_to_1_four_hour_bar():
    bars_1h = [
        {"open": 100, "high": 105, "low": 99, "close": 103, "volume": 1000, "timestamp": "2026-01-01T00:00"},
        {"open": 103, "high": 108, "low": 102, "close": 106, "volume": 1200, "timestamp": "2026-01-01T01:00"},
        {"open": 106, "high": 110, "low": 104, "close": 107, "volume": 800, "timestamp": "2026-01-01T02:00"},
        {"open": 107, "high": 109, "low": 105, "close": 108, "volume": 900, "timestamp": "2026-01-01T03:00"},
    ]
    result = aggregate_bars(bars_1h, factor=4)
    assert len(result) == 1
    assert result[0]["open"] == 100
    assert result[0]["high"] == 110
    assert result[0]["low"] == 99
    assert result[0]["close"] == 108
    assert result[0]["volume"] == 3900


def test_aggregate_8_bars_to_2():
    bars = [{"open": i, "high": i + 5, "low": i - 1, "close": i + 2, "volume": 100, "timestamp": f"t{i}"} for i in range(8)]
    result = aggregate_bars(bars, factor=4)
    assert len(result) == 2


def test_aggregate_partial_group_dropped():
    bars = [{"open": i, "high": i + 5, "low": i - 1, "close": i + 2, "volume": 100, "timestamp": f"t{i}"} for i in range(6)]
    result = aggregate_bars(bars, factor=4)
    assert len(result) == 1


def test_aggregate_empty_input():
    result = aggregate_bars([], factor=4)
    assert result == []
