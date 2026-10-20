"""네트워크 없이 도는 시험용 시세: 정해진 씨앗으로 만든 가짜 일봉 (실제 시세가 아니다)."""
from __future__ import annotations

import datetime as dt
import math
import random

from market import config as C
from market.prices import Row


def series(asset: str, start: dt.date, end: dt.date, seed: int = 7, drift: float = 0.0003,
           vol: float = 0.012, price: float = 10000.0) -> tuple[Row, ...]:
    rng = random.Random(f"{asset}:{seed}")
    etf = C.ASSETS[asset].kind == "etf"
    rows, d, c = [], start, price
    while d <= end:
        if not (etf and d.weekday() >= 5):
            o = c * math.exp(rng.gauss(0, vol / 4))
            c = o * math.exp(rng.gauss(drift, vol))
            hi, lo = max(o, c) * 1.003, min(o, c) * 0.997
            if etf:
                o, c, hi, lo = round(o), round(c), round(hi), round(lo)
            rows.append(Row(d, float(o), float(hi), float(lo), float(c), 1000.0, "fixture", "2026-01-01T00:00:00+09:00"))
        d += dt.timedelta(days=1)
    return tuple(rows)


def market(start: dt.date = dt.date(2024, 6, 1), end: dt.date = dt.date(2025, 9, 30)) -> dict[str, tuple[Row, ...]]:
    return {"069500": series("069500", start, end, price=30000),
            "360750": series("360750", start, end, price=15000, drift=0.0005),
            "KRW-BTC": series("KRW-BTC", start, end, price=50_000_000, vol=0.03)}
