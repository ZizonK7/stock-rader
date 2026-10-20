"""시세 받기·저장·읽기.

사용법 (저장소 루트):
  python -m market.prices backfill [대상...]   파일이 없을 때 받을 수 있는 만큼 과거부터 채운다 (있으면 거절)
  python -m market.prices update [대상...]     끝난 일봉 중 새 것만 덧붙인다 (지난 줄은 고치지 않는다)
  python -m market.prices show                 파일별 줄 수와 첫·마지막 날짜

규칙
- **끝난 일봉만** 저장한다: ETF는 그날 16:00(종가 15:30 뒤 여유), 코인은 다음 날 09:00 이후의 일봉만.
- 파일은 대상마다 CSV 하나, 덧붙이기만 한다. 칸: date, open, high, low, close, volume, source, fetched_at.
- 모든 네트워크 요청에 시간 제한(config.NET_TIMEOUT)을 건다.
- 계산 쪽은 History(어떤 시각에 알려진 줄만 담은 묶음)만 받는다 → 미래 시세를 구조로 못 읽는다(view).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config as C

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) stock-rader/1.0"
FIELDS = ("date", "open", "high", "low", "close", "volume", "source", "fetched_at")


@dataclass(frozen=True)
class Row:
    date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str = ""
    fetched_at: str = ""


class History:
    """한 대상의, 어떤 시각까지 알려진 일봉만 담은 읽기 전용 묶음. 뒤 줄을 담지 않는다."""

    __slots__ = ("asset", "_rows", "known_at")

    def __init__(self, asset: str, rows: tuple[Row, ...], known_at: dt.datetime | None = None):
        self.asset = asset
        self._rows = tuple(rows)
        self.known_at = known_at

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def rows(self) -> tuple[Row, ...]:
        return self._rows

    @property
    def closes(self) -> list[float]:
        return [r.close for r in self._rows]

    @property
    def last(self) -> Row | None:
        return self._rows[-1] if self._rows else None

    def on_or_after(self, day: dt.date) -> Row | None:
        for r in self._rows:
            if r.date >= day:
                return r
        return None

    def on(self, day: dt.date) -> Row | None:
        for r in reversed(self._rows):
            if r.date == day:
                return r
            if r.date < day:
                return None
        return None

    def index_of(self, day: dt.date) -> int | None:
        for i in range(len(self._rows) - 1, -1, -1):
            if self._rows[i].date == day:
                return i
        return None

    def until(self, day: dt.date) -> "History":
        """day 이하 날짜의 줄만 (맞추기·시험용)."""
        return History(self.asset, tuple(r for r in self._rows if r.date <= day), self.known_at)


def view(asset_id: str, rows: tuple[Row, ...] | list[Row], at: dt.datetime) -> History:
    """시각 at에 끝나 있던 일봉만 담은 History. 판단 시각 07:37이면 ETF는 전날까지, 코인은 이틀 전 일봉까지."""
    asset = C.ASSETS[asset_id]
    return History(asset_id, tuple(r for r in rows if C.final_dt(asset, r.date) <= at), at)


# ---------- 파일 ----------
def path_of(asset_id: str, folder: Path | None = None) -> Path:
    return (folder or C.PRICES) / f"{asset_id}.csv"


def _num(s: str) -> float:
    return float(s)


def load(asset_id: str, folder: Path | None = None) -> tuple[Row, ...]:
    p = path_of(asset_id, folder)
    if not p.exists():
        return ()
    with p.open(encoding="utf-8", newline="") as f:
        return tuple(Row(dt.date.fromisoformat(r["date"]), _num(r["open"]), _num(r["high"]), _num(r["low"]),
                         _num(r["close"]), _num(r["volume"]), r["source"], r["fetched_at"]) for r in csv.DictReader(f))


def load_all(folder: Path | None = None) -> dict[str, tuple[Row, ...]]:
    return {a: load(a, folder) for a in C.ASSET_IDS}


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def _line(r: Row) -> list[str]:
    return [r.date.isoformat(), _fmt(r.open), _fmt(r.high), _fmt(r.low), _fmt(r.close), _fmt(r.volume), r.source, r.fetched_at]


def append(asset_id: str, rows: list[Row], folder: Path | None = None) -> int:
    """파일 끝에 덧붙인다. 마지막 날짜보다 뒤인 줄만. 파일이 없으면 머리줄과 함께 만든다."""
    p = path_of(asset_id, folder)
    old = load(asset_id, folder)
    last = old[-1].date if old else None
    new = sorted((r for r in rows if last is None or r.date > last), key=lambda r: r.date)
    if not new:
        return 0
    p.parent.mkdir(parents=True, exist_ok=True)
    fresh = not p.exists()
    with p.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if fresh:
            w.writerow(FIELDS)
        for r in new:
            w.writerow(_line(r))
    return len(new)


# ---------- 받기 ----------
def _get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=C.NET_TIMEOUT) as resp:
        return resp.read()


ITEM = re.compile(r'<item data="(\d{8})\|([\d.]+)\|([\d.]+)\|([\d.]+)\|([\d.]+)\|([\d.]+)"')


def parse_naver(text: str, fetched_at: str) -> list[Row]:
    out = []
    for d, o, h, lo, c, v in ITEM.findall(text):
        out.append(Row(dt.date(int(d[:4]), int(d[4:6]), int(d[6:])), float(o), float(h), float(lo), float(c), float(v),
                       "naver", fetched_at))
    return out


def fetch_naver(symbol: str, count: int) -> list[Row]:
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={symbol}&timeframe=day&count={count}&requestType=0"
    raw = _get(url)
    text = raw.decode("euc-kr", errors="replace")
    return parse_naver(text, C.now_kst().isoformat(timespec="seconds"))


def parse_yahoo(data: dict, fetched_at: str) -> list[Row]:
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(res.get("timestamp", [])):
        vals = [q[k][i] for k in ("open", "high", "low", "close", "volume")]
        if any(v is None for v in vals):
            continue
        day = dt.datetime.fromtimestamp(ts, C.KST).date()
        out.append(Row(day, *[float(round(v)) if k != "volume" else float(v) for k, v in
                              zip(("open", "high", "low", "close", "volume"), vals)], "yahoo", fetched_at))
    return out


def fetch_yahoo(symbol: str, start: dt.date) -> list[Row]:
    p1 = int(dt.datetime.combine(start, dt.time(), C.KST).timestamp())
    p2 = int(C.now_kst().timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.KS?period1={p1}&period2={p2}"
           f"&interval=1d&events=history")
    return parse_yahoo(json.loads(_get(url)), C.now_kst().isoformat(timespec="seconds"))


def parse_upbit(items: list[dict], fetched_at: str) -> list[Row]:
    out = []
    for it in items:
        day = dt.date.fromisoformat(it["candle_date_time_kst"][:10])
        out.append(Row(day, float(it["opening_price"]), float(it["high_price"]), float(it["low_price"]),
                       float(it["trade_price"]), float(it["candle_acc_trade_volume"]), "upbit", fetched_at))
    return out


def fetch_upbit(market: str, count: int = 200, to: str | None = None) -> list[Row]:
    q = {"market": market, "count": str(count)}
    if to:
        q["to"] = to
    url = "https://api.upbit.com/v1/candles/days?" + urllib.parse.urlencode(q)
    return parse_upbit(json.loads(_get(url, {"Accept": "application/json"})), C.now_kst().isoformat(timespec="seconds"))


def fetch_upbit_all(market: str, max_pages: int = 60) -> list[Row]:
    rows: dict[dt.date, Row] = {}
    to = None
    for _ in range(max_pages):
        page = fetch_upbit(market, 200, to)
        if not page:
            break
        for r in page:
            rows.setdefault(r.date, r)
        oldest = min(r.date for r in page)
        to = f"{oldest.isoformat()} 00:00:00"  # UTC 기준: 이 시각 앞의 일봉들 (KST 09:00 = UTC 00:00)
        if len(page) < 200:
            break
        time.sleep(0.2)  # 업비트 요청 한도(초당 10회)보다 느리게
    return sorted(rows.values(), key=lambda r: r.date)


def finished(asset_id: str, rows: list[Row], now: dt.datetime) -> list[Row]:
    asset = C.ASSETS[asset_id]
    return [r for r in rows if C.final_dt(asset, r.date) <= now]


def fetch_recent(asset_id: str, count: int = 40) -> list[Row]:
    asset = C.ASSETS[asset_id]
    if asset.kind == "coin":
        return fetch_upbit(asset_id, count)
    try:
        return fetch_naver(asset_id, count)
    except Exception as exc:  # 보조 출처
        print(f"[{asset_id}] 네이버 실패({exc}) → 야후로", file=sys.stderr)
        return fetch_yahoo(asset_id, dt.date.today() - dt.timedelta(days=count * 2))


def fetch_history(asset_id: str) -> list[Row]:
    asset = C.ASSETS[asset_id]
    if asset.kind == "coin":
        return fetch_upbit_all(asset_id)
    try:
        return fetch_naver(asset_id, 10000)  # 네이버는 최대 3,000줄을 준다
    except Exception as exc:
        print(f"[{asset_id}] 네이버 실패({exc}) → 야후로", file=sys.stderr)
        return fetch_yahoo(asset_id, dt.date(2000, 1, 1))


def backfill(asset_id: str, folder: Path | None = None, now: dt.datetime | None = None) -> int:
    if load(asset_id, folder):
        raise SystemExit(f"[{asset_id}] 파일이 이미 있다 — backfill은 처음 한 번만. 덧붙이기는 update")
    rows = finished(asset_id, fetch_history(asset_id), now or C.now_kst())
    return append(asset_id, rows, folder)


def update(asset_id: str, folder: Path | None = None, now: dt.datetime | None = None,
           rows: list[Row] | None = None) -> tuple[int, list[str]]:
    """새 끝난 일봉을 덧붙인다. (덧붙인 수, 경고). 이미 있는 날의 값이 다르면 고치지 않고 경고만 낸다."""
    old = {r.date: r for r in load(asset_id, folder)}
    if not old:
        raise SystemExit(f"[{asset_id}] 파일이 없다 — 먼저 backfill")
    got = finished(asset_id, rows if rows is not None else fetch_recent(asset_id), now or C.now_kst())
    warns = []
    for r in got:
        o = old.get(r.date)
        if o and (abs(o.close - r.close) > 1e-9 or abs(o.open - r.open) > 1e-9):
            warns.append(f"[{asset_id}] {r.date} 저장된 값과 새로 받은 값이 다름 (저장 종가 {_fmt(o.close)}, 새 {_fmt(r.close)}) — 고치지 않음")
    last = max(old)
    if got and min(r.date for r in got) > last:
        warns.append(f"[{asset_id}] 받은 줄이 저장된 마지막 날({last}) 뒤에서 시작 — 사이가 빠졌을 수 있다")
    return append(asset_id, got, folder), warns


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("backfill", "update", "show"):
        print(__doc__)
        return 2
    targets = argv[1:] or list(C.ASSET_IDS)
    if argv[0] == "show":
        for a in targets:
            rows = load(a)
            print(f"{a}: {len(rows)}줄" + (f", {rows[0].date} ~ {rows[-1].date}" if rows else ""))
        return 0
    code = 0
    for a in targets:
        try:
            if argv[0] == "backfill":
                print(f"{a}: {backfill(a)}줄 저장")
            else:
                n, warns = update(a)
                print(f"{a}: {n}줄 덧붙임")
                for w in warns:
                    print("  경고:", w)
        except SystemExit as exc:
            print(exc)
            code = 1
        except Exception as exc:  # 한 대상이 실패해도 나머지는 받는다
            print(f"{a}: 실패 — {exc}")
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
