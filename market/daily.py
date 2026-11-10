"""매일 실행 (클라우드 예약 실행, 한국 시간 07:37): prepare → (메인: 조사·대 보기·손 예측) → record → report, 그리고 open.

사용법 (저장소 루트):
  python -m market.daily prepare [--no-fetch]      시세 받기 → 체결·채점 미리 보기 → 돌아볼 목록 → 오늘 조사 질문.
                                                   out/daily/<날짜>/에 prepare.json, briefing.md, input.json(메인이 채울 틀)
  python -m market.daily record <input.json>       입력을 검사하고 그날 기록을 한 번에 덧붙인다(오류면 아무것도 안 씀)
  python -m market.daily open                      장 시작 뒤 단계(09:00 뒤 아무 때나, 하루 한 번): 오늘 시가 체결을 장부에 넣고,
                                                   오늘 목표 비중으로 남은 주문을 지금 가격에 낸다(팔린 돈으로 사기)
  python -m market.daily report [--force]          reports/<날짜>.md 뼈대(숫자는 기록에서). 일요일엔 주간 결산도
  python -m market.daily report --check            일지에 채우지 않은 자리([[채울 것: …]])가 남았나
흉내 내기·시험용 (기본 저장소에서는 못 씀): --root <폴더> --day YYYY-MM-DD --at HH:MM

규칙
- **첫 실행 날이 진짜 계좌 시작일**이다: 계좌 넷(live:hand 손 예측, live:calc 계산기 R_weights, baseline:equal_hold 첫날 셋을
  1/3씩 사 두기, baseline:cash 현금)이 1,000만 원 현금으로 시작한다.
- 빠진 날은 빈 날(run mode "missed")로 남기고, 지난 날짜로 예측하지 않는다(기본 저장소에서 --day 금지).
- **주문은 가상 증권사(market/broker.py)를 거친다**(2026-11-10, docs/broker-sandbox.md 16절): 계좌 넷 모두. 매일 실행은 목표 비중을
  주문 계획(market/rebalance.py)에 넘기고, 받기·거절·체결·잔고는 증권사가 정한다. 비트코인은 판단 때 지금 주문으로 바로,
  한국 거래소는 팔기를 장 시작 시장가로·사기를 지금 주문 가능 금액 안에서 장 시작 지정가로(판단이 09:00 뒤면 지금 주문).
  어제 09:00 체결은 오늘 실행의 맞추기(sync)에서 장부에 들어온다. 가격은 진짜 기록 폴더에서는 실제 주소(quotes.NetQuotes),
  흉내 내기 폴더(--root)에서는 그 폴더의 시세 파일(quotes.BarQuotes)에서 받는다.
- **갈아타는 날**(17절): broker 기록에 start가 없으면 그 실행에서 갈아탄다 — 옛 대기 주문을 옛 규칙 가격으로 모두 끝내고(옛 모양
  체결 줄), 보유는 그대로·현금은 은행에 둔 start 줄을 계좌 넷에 만든다. 끝낼 수 없는 옛 주문이 하나라도 남으면 계좌 넷 모두
  그날은 옛 장부(market/ledger.py)로 돌고 다음 실행에서 다시 시도한다. 기록이 없는 폴더는 첫 실행에서 바로 start.
- 증권사 기록이 어긋나 있거나(Broker.problems) 증권사 안에서 예외가 나면(broker.BrokerError 등) prepare·record·open은 아무것도
  쓰지 않고 멈춘다(까닭 글, 종료 코드 1 — 예외로 죽지 않는다).
- 판단 시각 = record를 돌린 실제 시각. 판단 시각까지 끝났어야 할 일봉이 시세 파일에 없으면 그 대상은 그날 예측·주문하지 않는다.
- 예측 기준 = 판단 뒤 첫 시가(2026-11-05~, 결정 E2): 새 예측 줄은 base_kind "fill", base_date = 판단 뒤 첫 체결 봉 날짜
  (config.fill_bar), base_close = null. 채점이 그 봉의 시가(fill_price)에서 목표 종가까지 잰다. 장 시작 주문은 그 시가에 체결되어
  기준과 같고, 지금 주문(비트코인은 판단 때, 장 시작 뒤 사기는 09:0x)은 기준과 몇 분~한 시간 반 어긋난다(알려진 차이).
  그래서 record는 09:00 전에 끝낸다 — prepare가 08:50 뒤면, record가 09:00 뒤면 경고한다.
- 07:37 업비트 현재가를 '판단 때 가격'으로 따로 적는다(결정 3, 손 예측 근거). 계산기에는 넘기지 않는다(결정 E3).
- 예측 줄(2026-11-04): 계산기·이전 버전·손에 layer(답한 층), 계산기에 analog(같은 스위치 지난 날들, 참고 칸 — 체결가 기준),
  기준선 셋(baseline:half, baseline:base_rate, baseline:all).
- 채점은 같은 (예측기·대상·기간·기준 날짜)를 처음 말한 것 하나만 센다(score.counted, 결정 1). 예측은 매일 적는다.
- 모든 네트워크 요청에 시간 제한 30초(config.NET_TIMEOUT).
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import broker as B
from . import calc as K
from . import config as C
from . import hand as H
from . import ledger as L
from . import pieces as P
from . import prices as PR
from . import quotes as Q
from . import rebalance as RB
from . import records as R
from . import research as RS
from . import rules as RU
from . import scoring as S
from . import stocks as ST
from . import versions as V

WEIGHT_HORIZON_INDEX = 1  # R_weights는 1주 기간의 오를 확률로
KIND_ORDER = ("run", "fill", "score", "account", "reading", "research", "hand_reading", "prediction", "order",
              "broker", "review", "reading_check", "correction")
EQUAL_NOTE = "기준선: 처음 살 수 있는 날 1/3씩 사서 그대로 둔다"


@dataclass
class Root:
    path: Path

    @property
    def prices(self) -> Path:
        return self.path / "data" / "prices"

    @property
    def records(self) -> Path:
        return self.path / "records"

    @property
    def reports(self) -> Path:
        return self.path / "reports"

    @property
    def knowledge(self) -> Path:
        return self.path / "knowledge.toml"

    @property
    def places(self) -> Path:
        return self.path / "places.toml"

    @property
    def versions(self) -> Path:
        return self.path / "versions"

    @property
    def stocks(self) -> Path:
        return self.path / "stocks.toml"

    def day_dir(self, day: dt.date) -> Path:
        return self.path / "out" / "daily" / day.isoformat()

    @property
    def live(self) -> bool:
        """진짜 기록 폴더인가(실제 주소에서 가격을 받고, 요청 시각은 실제 시계)."""
        return self.path == C.ROOT.resolve()


def quotes_for(root: Root) -> Q.Quotes:
    """가상 증권사가 가격을 받을 곳: 진짜 기록 폴더는 실제 주소, 흉내 내기 폴더는 그 폴더의 시세 파일(시각에 정직한 가짜)."""
    if root.live:   # 끝난 봉은 시세 파일(덧붙이기만 하는 공식 값)에서 먼저 읽는다(오래 멈췄다 돈 실행 — 검토 발견 4)
        return Q.NetQuotes(prices_folder=root.prices)
    return Q.BarQuotes({p.stem: PR.load(p.stem, root.prices) for p in sorted(root.prices.glob("*.csv"))})


# ---------- 시각과 시세 ----------
def expected_next(asset: str, last: dt.date) -> dt.date:
    """마지막 일봉 다음에 나올 일봉 날짜 (ETF는 주말만 건너뛴다 — 휴장일은 모른다). config.next_bar와 같다."""
    return C.next_bar(asset, last)


def skip_reason(asset: str, hist: PR.History, at: dt.datetime, fetch: dict) -> str:
    """이 대상을 오늘 예측·주문하지 않을 까닭 (없으면 ''). 판단 시각까지 끝났어야 할 일봉이 파일에 없으면 건너뛴다.
    다만 시세를 그 일봉이 끝난 뒤에 받았는데도 없으면 휴장으로 본다."""
    if hist.last is None:
        return "시세가 없음"
    nxt = expected_next(asset, hist.last.date)
    fin = C.final_dt(C.ASSETS[asset], nxt)
    if fin > at:
        return ""
    f = fetch.get(asset, {})
    if (C.on_exchange(asset) and f.get("status") == "ok" and f.get("at")
            and dt.datetime.fromisoformat(f["at"]) >= fin):
        return ""  # 받았는데 없다 = 휴장 (코인은 휴장이 없으니 건너뛴다; 종목은 거래 정지도 여기서 걸러지지 않는다)
    return (f"판단 시각({at:%m-%d %H:%M})까지 끝났어야 할 {nxt} 일봉이 시세 파일에 없음"
            f"(받기 {f.get('status', '안 함')}) — 낡은 시세로 예측하면 이미 나온 결과를 맞히는 셈이다")


def fetch_prices(root: Root, at: dt.datetime) -> dict:
    """세 대상과 종목 목록(stocks.toml)의 시세를 덧붙인다. 대상마다 {status, at, added, error, warns}. 한 곳이 막혀도 나머지는
    받는다. 처음 쓰는 종목은 최근 일봉을 받아 파일을 만든다(first = true)."""
    out = {}
    for a in C.ASSET_IDS:
        now = C.now_kst()
        try:
            n, warns = PR.update(a, root.prices, now=now)
            out[a] = {"status": "ok", "at": now.isoformat(timespec="seconds"), "added": n, "warns": warns}
        except BaseException as exc:  # SystemExit 포함: 파일 없음 등
            out[a] = {"status": "fail", "at": now.isoformat(timespec="seconds"), "error": f"{type(exc).__name__}: {exc}"}
    for s in ST.register(root.stocks):
        a = str(s["code"])
        now = C.now_kst()
        try:
            n, warns, first = ST.update(a, root.prices, now, str(s.get("name", "")))
            out[a] = {"status": "ok", "at": now.isoformat(timespec="seconds"), "added": n, "warns": warns, "first": first}
        except BaseException as exc:
            out[a] = {"status": "fail", "at": now.isoformat(timespec="seconds"), "error": f"{type(exc).__name__}: {exc}"}
    return out


def fetch_ticker() -> dict:
    """07:37 업비트 현재가 (판단 때 가격, 결정 3). 실패해도 멈추지 않는다."""
    url = "https://api.upbit.com/v1/ticker?markets=KRW-BTC"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": PR.UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=C.NET_TIMEOUT) as resp:
            d = json.loads(resp.read())[0]
        ts = dt.datetime.fromtimestamp(d["trade_timestamp"] / 1000, C.KST)
        return {"KRW-BTC": {"price": float(d["trade_price"]), "time": ts.isoformat(timespec="seconds"), "source": "upbit ticker"}}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------- 그날 계산 (prepare와 record가 같이 쓴다; 기록을 쓰지 않는다) ----------
@dataclass
class Day:
    day: dt.date
    at: dt.datetime
    hists: dict
    raw: dict
    digest: str
    recs: dict[str, list[dict]]
    already: bool
    missing: list[dt.date]
    predictable: list[str]
    skipped: dict[str, str]
    accounts: dict[str, L.Account]
    fills: list[dict]
    scores: list[dict]
    account_lines: list[dict]
    readings: list[dict] = field(default_factory=list)
    calc_values: dict[str, dict] = field(default_factory=dict)
    preds: list[dict] = field(default_factory=list)
    calc_orders: list[dict] = field(default_factory=list)
    due_reviews: dict[str, dict] = field(default_factory=dict)
    due_checks: dict[tuple, dict] = field(default_factory=dict)
    open_next: dict[str, str] = field(default_factory=dict)  # 열린 '다음엔'(hand.open_next, 2026-11-07)
    questions: list[dict] = field(default_factory=list)
    places: list[dict] = field(default_factory=list)
    stale: set = field(default_factory=set)
    prices: dict[str, float] = field(default_factory=dict)
    base: dict[str, dict] = field(default_factory=dict)
    # 종목 (2026-10-29): 목록, 오늘 예측·주문할 수 있는 종목, 건너뛴 종목과 까닭, 손 계좌가 들고 있거나 기다리는 종목,
    # 오늘 기록 줄(대 보기·계산기·기준선 예측)을 만든 종목
    pool: list[dict] = field(default_factory=list)
    stock_ok: list[str] = field(default_factory=list)
    stock_skipped: dict[str, str] = field(default_factory=dict)
    held: list[str] = field(default_factory=list)
    focus: list[str] = field(default_factory=list)
    # 가상 증권사 (2026-11-10): mode = "broker"(계좌 넷이 증권사를 거침) / "ledger"(갈아타기 전 — 끝낼 수 없는 옛 주문이 남은 날)
    mode: str = "ledger"
    brokers: dict[str, B.Broker] = field(default_factory=dict)
    quotes: Q.Quotes | None = None
    problems: list[str] = field(default_factory=list)   # 증권사 기록이 어긋남 → 매일 실행은 멈춘다
    switched: bool = False                               # 이 실행에서 갈아탔다(start 줄을 만들었다)
    switch_note: str = ""                                # 갈아타지 못한 까닭(옛 장부로 돈 날)
    sync_lines: list[tuple[str, dict]] = field(default_factory=list)   # 맞추기에서 장부에 들어온 체결·만료·바로잡기 줄
    sync_warnings: list[str] = field(default_factory=list)
    value_prices: dict[str, tuple[float, dt.datetime]] = field(default_factory=dict)  # 평가 가격과 그 시각(P8-3)
    hand_units: dict[str, int] = field(default_factory=dict)   # 손 계좌가 든 수량 + 걸린 주문
    hand_total: float = 0.0
    calc_w: dict[str, float | None] = field(default_factory=dict)
    calc_reasons: dict[str, str] = field(default_factory=dict)
    equal_first: dict[str, float] = field(default_factory=dict)


def old_lines(recs: dict, kind: str) -> list[dict]:
    """옛 장부가 읽을 줄: 가상 증권사가 만든 줄(seq가 있는 줄)을 뺀다 — 옛 장부는 증권사 주문(지금 주문 등)을 모른다."""
    return [x for x in recs[kind] if "seq" not in x]


def old_fill_row(o: L.Order, hists: dict, quotes: Q.Quotes, at: dt.datetime) -> tuple[PR.Row | None, str]:
    """옛 대기 주문이 옛 규칙으로 체결될 봉. 끝난 봉(시세 파일)에 없으면 진행 중인 봉을 공급자에게 묻는다(비트코인: 09:00에 시작한
    봉의 시가는 이미 정해져 있다). (봉, 못 찾은 까닭)."""
    row = L.fill_row(o, hists[o.asset]) if o.asset in hists else None
    if row is not None:
        return row, ""
    if o.decided_at is None:
        return None, "판단 시각이 없는 옛 주문의 체결 봉이 시세 파일에 없음"
    spec = C.ASSETS[o.asset]
    kind, when = o.rule()
    d = max(o.run, o.decided_at.astimezone(C.KST).date())
    while (C.on_exchange(o.asset) and d.weekday() >= 5) or C.fill_dt(spec, d, when) <= o.decided_at:
        d += dt.timedelta(days=1)
    if C.fill_dt(spec, d, when) > at:
        return None, f"체결 시각({d} {when:%H:%M})이 아직 오지 않음"
    got = quotes.day(o.asset, d, at)
    if got is None:
        return None, f"{d} 봉의 값을 받지 못함"
    price = got.open if kind == "open" else (got.close if got.final else None)
    if not price or price <= 0:
        return None, f"{d} 봉의 {kind} 값이 아직 없음"
    return PR.Row(d, price, price, price, price, 0.0, got.source, at.isoformat(timespec="seconds")), ""


def finish_old(accounts: dict[str, L.Account], hists: dict, quotes: Q.Quotes, at: dt.datetime,
               day: dt.date) -> tuple[dict[str, L.Account] | None, list[dict], str]:
    """갈아타기: 옛 대기 주문을 옛 규칙 가격으로 모두 끝낸다. 하나라도 못 끝내면 아무 계좌도 바꾸지 않는다(섞이지 않게).
    (끝낸 계좌들 또는 None, 옛 모양 체결 줄, 못 끝낸 까닭)."""
    import copy
    out, fills = {}, []
    for aid, acc in accounts.items():
        acc = copy.deepcopy(acc)
        ready = []
        for o in acc.pending:
            row, why = old_fill_row(o, hists, quotes, at)
            if row is None:
                return None, [], f"{o.id}: {why}"
            ready.append((C.fill_dt(C.ASSETS[o.asset], row.date, o.rule()[1]), 0 if o.side == "sell" else 1, o, row))
        for _, _, o, row in sorted(ready, key=lambda x: (x[0], x[1])):
            fills.append(acc._fill(o, row, day))
            acc.pending.remove(o)
        out[aid] = acc
    return out, fills, ""


def cost_basis(fills: list[dict], aid: str) -> dict[str, float]:
    """옛 체결 기록의 이동 평균으로 구한 '산 값의 합'(대상 → 원, 수수료 뺌) — 갈아타는 날 start 줄에 넣는다(해외 지수 ETF 이익 세금)."""
    units: dict[str, int] = {}
    cost: dict[str, float] = {}
    for f in fills:
        if f["account"] != aid or not f["qty_units"]:
            continue
        a, q = f["asset"], f["qty_units"]
        held = units.get(a, 0)
        if f["side"] == "buy":
            cost[a] = cost.get(a, 0.0) + f["value"]
            units[a] = held + q
        else:
            cost[a] = cost.get(a, 0.0) * (1 - q / held) if held > q else 0.0
            units[a] = max(held - q, 0)
    return {a: round(v, 2) for a, v in cost.items() if units.get(a)}


def value_prices(hists: dict, quotes: Q.Quotes, at: dt.datetime, ticker: dict, market_open: bool,
                 ask: set[str] | None = None) -> dict:
    """평가 가격과 그 가격의 시각(P8-3: 평가하는 순간에 받을 수 있는 가장 새 실제 가격). 대상 → (값, 시각).
    비트코인: 지금 가격 → (못 받으면) prepare가 받은 판단 때 가격 → 마지막 끝난 봉. 한국 거래소: 장이 닫혀 있으면 마지막 공식
    종가(시세 파일의 끝난 봉 — 가장 새 값이다), 장중이면 지금 가격(못 받으면 마지막 종가). ask가 있으면 장중의 지금 가격은 그
    대상들만 묻는다(계좌가 든 것 — 목록의 모든 종목을 실제 주소에 묻지 않게)."""
    out = {}
    skew = dt.timedelta(seconds=RU.value("quote_clock_skew"))
    for a, h in hists.items():
        last = h.last
        got = None
        if C.ASSETS[a].kind == "coin" or (market_open and (ask is None or a in ask)):
            n = quotes.now(a, at)
            if n is not None and n.price > 0 and n.time <= at + skew and (C.ASSETS[a].kind == "coin" or n.open):
                got = (n.price, n.time)
        if got is None and ticker.get(a):
            try:
                t = dt.datetime.fromisoformat(ticker[a]["time"])
                if t <= at + skew and (last is None or t > C.final_dt(C.ASSETS[a], last.date)):
                    got = (float(ticker[a]["price"]), t)
            except (KeyError, TypeError, ValueError):
                pass
        if got is None and last is not None:
            end = (dt.datetime.combine(last.date, C.CLOSE_TIME, C.KST) if C.on_exchange(a)
                   else C.final_dt(C.ASSETS[a], last.date))
            got = (last.close, end)
        if got is not None:
            out[a] = got
    return out


def compute(root: Root, day: dt.date, at: dt.datetime, fetch: dict, ticker: dict, focus=()) -> Day:
    """focus: 오늘 기록 줄(시세 대 보기·계산기·기준선 예측)을 만들 종목(record가 입력에서 넘긴다). 손 계좌가 들고 있는 종목은 늘 든다."""
    pool = ST.register(root.stocks)
    recs = {k: R.read(root.records, k) for k in R.FIELDS}
    rows = PR.load_all(root.prices)
    # 종목: 목록 + 기록에 나온 종목(목록에서 지워졌어도 채점·평가는 해야 한다)
    seen_stocks = ({str(s["code"]) for s in pool if C.is_stock(str(s.get("code", "")))}
                   | {x["asset"] for k in ("prediction", "order") for x in recs[k] if C.is_stock(x["asset"])}
                   | {a for x in recs["account"] for a in x["positions"] if C.is_stock(a)})
    for a in sorted(seen_stocks):
        rows[a] = PR.load(a, root.prices)
    hists = {a: PR.view(a, r, at) for a, r in rows.items()}
    raw = tomllib.loads(root.knowledge.read_text(encoding="utf-8"))
    pieces, digest = K.load_pieces(root.knowledge)
    runs = sorted({r["run"] for r in recs["run"]})
    last_run = runs[-1] if runs else None
    already = last_run is not None and last_run >= day.isoformat()
    missing = []
    if last_run and not already:
        d = dt.date.fromisoformat(last_run) + dt.timedelta(days=1)
        while d < day:
            missing.append(d)
            d += dt.timedelta(days=1)

    skipped = {a: why for a in C.ASSET_IDS if (why := skip_reason(a, hists[a], at, fetch))}
    predictable = [a for a in C.ASSET_IDS if a not in skipped]
    stock_skipped = {a: why for a in sorted(seen_stocks) if (why := skip_reason(a, hists[a], at, fetch))}
    buyable = {str(s["code"]) for s in pool if s.get("buy", True)}

    # 계좌: 가상 증권사(2026-11-10). start 줄이 있으면 증권사 기록을 다시 돌려 맞추고(sync), 없으면 이 실행에서 갈아탄다.
    quotes = quotes_for(root)
    bar_days = [r.date for r in hists["069500"].rows]
    started = {aid: any(x["account"] == aid and x["type"] == "start" for x in recs["broker"]) for aid in C.LIVE_ACCOUNTS}
    brokers: dict[str, B.Broker] = {}
    problems: list[str] = []
    sync_lines: list[tuple[str, dict]] = []
    sync_warnings: list[str] = []
    accounts: dict[str, L.Account] = {}
    fills: list[dict] = []
    switched, switch_note = False, ""
    if all(started.values()):
        for aid in C.LIVE_ACCOUNTS:
            try:
                b = brokers[aid] = B.Broker(aid, quotes, recs, bar_days)
                problems += b.problems
                if not b.problems:
                    res = b.sync(at)
                    if not res.ok:
                        problems.append(f"{aid}: {res.reason}")
                    sync_lines += res.lines
                    sync_warnings += res.warnings
            except Exception as exc:   # 증권사 안의 예외는 멈춤으로(예외로 죽지 않는다 — 검토 발견 3)
                problems.append(crash(aid, exc))
    elif any(started.values()):
        problems.append(f"계좌 일부만 갈아탔다({[a for a, v in started.items() if v]}) — 섞인 채로 돌지 않는다")
    else:
        # 갈아타는 날(17절): 옛 장부를 되살려 정산하고, 남은 옛 주문을 옛 규칙 가격으로 모두 끝낸 뒤 start
        accounts = {aid: L.rebuild(aid, old_lines(recs, "account"), old_lines(recs, "order")) for aid in C.LIVE_ACCOUNTS}
        fills = [f for acc in accounts.values() for f in acc.settle(hists, day)]
        done, forced, switch_note = finish_old(accounts, hists, quotes, at, day)
        if done is not None:
            accounts, fills, switched = done, fills + forced, True
            for aid, acc in accounts.items():
                try:
                    b = brokers[aid] = B.Broker(aid, quotes, {}, bar_days)
                    units = {a: n for a, n in acc.units.items() if n}
                    res = b.start(at, bank=acc.cash, units=units, cost=cost_basis(recs["fill"] + fills, aid),
                                  note="갈아타기: 옛 장부의 보유는 그대로(ETF·종목 → 증권 계좌, 비트코인 → 업비트), 현금은 은행에")
                    if not res.ok:
                        problems.append(f"{aid}: 시작 잔고를 만들지 못함 — {res.reason}")
                except Exception as exc:
                    problems.append(crash(aid, exc))
    mode = "broker" if brokers and not problems else "ledger"
    if mode == "broker":
        hb = brokers["live:hand"]
        held = sorted({a for a in hb.expected_units() if a not in C.CORE} | {a for a in hb.holdings() if a not in C.CORE})
    else:
        held = sorted(accounts["live:hand"].held()) if accounts else []  # 정산 뒤: 들고 있거나 체결을 기다리는 종목

    # 채점
    preds_all = recs["prediction"]
    done = {s["prediction"] for s in recs["score"]}
    scores = S.score_due(preds_all, hists, day, done)
    by_id = {p["id"]: p for p in preds_all}
    counted = {S.key_of(by_id[s["prediction"]]) for s in recs["score"] if s.get("counted", True) and s["prediction"] in by_id}
    S.mark_counted(scores, by_id, counted)

    last = L.last_prices(hists)
    D = Day(day, at, hists, raw, digest, recs, already, missing, predictable, skipped, accounts, fills, scores, [])
    D.mode, D.brokers, D.quotes, D.problems = mode, brokers, quotes, problems
    D.switched, D.switch_note, D.sync_lines, D.sync_warnings = switched, switch_note, sync_lines, sync_warnings
    D.pool, D.stock_skipped = pool, stock_skipped
    D.held = [a for a in held if a not in stock_skipped]
    D.stock_ok = [a for a in sorted(seen_stocks) if a not in stock_skipped and (a in buyable or a in held)]
    D.prices = {a: v for a, (_, v) in last.items()}
    if mode == "broker":
        # 평가·주문 수량의 가격: 받을 수 있는 가장 새 실제 값(비트코인은 판단 때 지금 가격), 값마다 그 시각
        mine = set(C.ASSET_IDS) | {a for b in brokers.values() for a in b.expected_units()}
        D.value_prices = value_prices(hists, quotes, at, ticker, brokers["live:hand"].market(at) == "now", mine)
        D.prices = {a: v for a, (v, _) in D.value_prices.items()}
        try:
            D.account_lines = [b.account_line(at, day, D.value_prices)[0] for b in brokers.values()]  # 미리 보기(record가 다시 만든다)
        except Exception as exc:
            D.problems.append(crash("계좌 줄 미리 보기", exc))
        D.hand_units = brokers["live:hand"].expected_units()
        D.hand_total = RB.total_value(brokers["live:hand"], D.prices)
    elif accounts:
        D.account_lines = [acc.record(day, last) for acc in accounts.values()]
        if ticker.get("KRW-BTC"):
            D.prices["KRW-BTC"] = ticker["KRW-BTC"]["price"]  # 주문 수량은 판단 때 가격으로 잡는다(체결은 다음 공식 가격)
        units, cash = accounts["live:hand"].expected(D.prices)
        D.hand_units = units
        D.hand_total = cash + sum(n * D.prices[a] / C.ASSETS[a].unit for a, n in units.items() if a in D.prices)

    # 시세 대 보기·계산기·이전 버전·기준선 예측
    run = day.isoformat()
    versions = V.active_pieces(root.versions)
    calc_w: dict[str, float | None] = {}
    reasons: dict[str, str] = {}
    for a in predictable:
        h = hists[a]
        values, numbers = P.read(h.closes)
        D.calc_values[a] = values
        D.base[a] = fill_base(a, at)
        D.readings.append({"id": f"reading:{run}:{a}", "run": run, "asset": a, "known_through": h.last.date.isoformat(),
                           "values": values, "numbers": numbers})
        ana = K.analog(h.closes, C.ASSETS[a].horizons, values, opens=[r.open for r in h.rows])
        for i, hz in enumerate(C.ASSETS[a].horizons):
            base = {"run": run, "asset": a, "horizon": hz, **D.base[a]}
            f = K.record_fields(K.predict(a, hz, values, pieces, numbers))
            D.preds.append({"id": f"{run}:calc:{a}:{hz}", "predictor": "calc", **base, "readings": values,
                            "knowledge": digest, **f, "analog": ana[hz]})
            if i == WEIGHT_HORIZON_INDEX:
                calc_w[a] = L.target_weight(f["p_up"])
                reasons[a] = f"R_weights_v2: 1주 오를 확률 {f['p_up']} → 목표 비중 {calc_w[a]:.4f}"
            for vid, vpieces, vdig in versions:  # 판단 때 가격은 넘기지 않는다: 출발점 조각이 살아 있는 모음(v5)도 걸리지 않는다
                vf = K.record_fields(K.predict(a, hz, values, vpieces, numbers))
                D.preds.append({"id": f"{run}:version:{vid}:{a}:{hz}", "predictor": f"version:{vid}", **base,
                                "readings": values, "knowledge": vdig, **vf})
            D.preds += baselines(run, a, hz, h, base)
    # 종목: 시세 상태와 기준 종가는 오늘 살 수 있는 종목 모두(손 예측 검사용), 기록 줄은 손 계좌가 든 종목과 focus만.
    # 계산기는 세 대상에만 맞춘 숫자라 종목에는 '모름'이다(기록은 남겨 손 예측과 나란히 보이게; 이전 버전은 같은 '모름'이라 뺀다).
    for a in D.stock_ok:
        h = hists[a]
        values, numbers = P.read(h.closes)
        D.calc_values[a] = values
        D.base[a] = fill_base(a, at)
        D.prices.setdefault(a, h.last.close)
    D.focus = [a for a in D.stock_ok if a in set(D.held) | set(focus)]
    for a in D.focus:
        h = hists[a]
        values, numbers = P.read(h.closes)
        D.readings.append({"id": f"reading:{run}:{a}", "run": run, "asset": a, "known_through": h.last.date.isoformat(),
                           "values": values, "numbers": numbers})
        ana = K.analog(h.closes, C.ASSETS[a].horizons, values, opens=[r.open for r in h.rows])
        for hz in C.ASSETS[a].horizons:
            base = {"run": run, "asset": a, "horizon": hz, **D.base[a]}
            f = K.record_fields(K.predict(a, hz, values, pieces, numbers))
            D.preds.append({"id": f"{run}:calc:{a}:{hz}", "predictor": "calc", **base, "readings": values,
                            "knowledge": digest, **f, "analog": ana[hz]})
            D.preds += baselines(run, a, hz, h, base)
    # 계산기 계좌·기준선의 목표: 계산기는 R_weights_v2, 셋 나눠 사 두기는 아직 못 산 대상만 1/3(문턱 0).
    # 돈이 모자라 덜 산 대상(지난 plan 줄의 short)은 다음 실행에서 마저 산다.
    D.calc_w, D.calc_reasons = calc_w, reasons
    bought = {o["asset"] for o in D.recs["order"] if o["account"] == "baseline:equal_hold"}
    if mode == "broker":
        # 첫 사기가 돈이 잡혀(장 시작 사기는 한도만큼 잡아 둔다) 덜 된 대상은 남은 돈이 계좌의 문턱(2%) 이상일 때 마저 산다
        eb = brokers["baseline:equal_hold"]
        plans = eb.events("plan")
        if plans and sum(eb.pockets().values()) >= RB.BAND * RB.total_value(eb, D.prices):
            bought -= set(plans[-1]["data"].get("short", []))
    D.equal_first = {a: 1 / 3 for a in predictable if a not in bought}
    if not already and mode == "ledger" and accounts:   # 갈아타기 전(옛 장부로 도는 날): 옛 규칙 그대로
        D.calc_orders = [o.record() for o in accounts["live:calc"].plan(day, calc_w, D.prices, 0.02, reasons, at,
                                                                        C.FILL_RULE)]
        if D.equal_first:
            D.calc_orders += [o.record() for o in accounts["baseline:equal_hold"].plan(
                day, D.equal_first, D.prices, 0.0, {a: EQUAL_NOTE for a in D.equal_first}, at, C.FILL_RULE)]

    # 돌아볼 손 예측: 한 번만 세는 채점 중 아직 돌아보지 않은 것 (오늘 새로 채점된 것 포함)
    reviewed = {r["prediction"] for r in recs["review"]}
    for s in recs["score"] + scores:
        p = by_id.get(s["prediction"])
        if not p or p["predictor"] != "hand" or not s.get("counted", True) or s["prediction"] in reviewed:
            continue
        tm = S.typical_move(hists[p["asset"]], p["horizon"])
        D.due_reviews[p["id"]] = {
            "verdict": S.verdict_of(s), "direction_hit": s["direction_hit"], "size_hit": s["size_hit"],
            "ret_pct": round(s["ret"] * 100, 3), "typical_pct": round(tm * 100, 3) if tm else None,
            "noise": bool(tm) and abs(s["ret"]) < C.NOISE_SHARE * tm,
            "asset": p["asset"], "horizon": p["horizon"], "run": p["run"], "p_up": p["p_up"],
            "size": [p["size_lo"], p["size_hi"]], "used": p["used"], "decisive": p["decisive"], "reason": p["reason"],
            "target_date": s["target_date"]}
    D.due_checks = {(c["target"], c["state"]): c for c in RS.due_checks(root.records, day)}
    D.open_next = H.open_next(recs["review"], recs["run"], day)
    D.places = RS.load_places(root.places)
    D.stale = RS.stale_places(root.records, day, D.places)
    D.questions = RS.questions(day, raw, D.places, RS.place_cards(root.records, D.places))
    return D


def fill_base(a: str, at: dt.datetime) -> dict:
    """새 예측 줄의 기준 칸(결정 E2): 판단 시각 at 뒤 첫 체결 봉 날짜, 기준가는 예측 때 모르므로 null(채점이 그 봉의 시가를 적는다)."""
    return {"base_date": C.fill_bar(a, at).isoformat(), "base_close": None, "base_kind": "fill"}


def baselines(run: str, a: str, hz: int, h: PR.History, base: dict) -> list[dict]:
    """기준선 예측 셋: 늘 50%, 최근 250봉에서 오른 비율, 알려진 전체 시세에서 오른 비율(2026-11-04).
    체결가 기준 줄(base_kind fill)이면 비율도 같은 기준의 수익률(다음 봉 시가 → h봉째 종가)로 센다."""
    fill = base.get("base_kind") == "fill"
    br, w = S.base_rate(h, hz, fill)
    ar, aw = S.all_rate(h, hz, fill)
    what = f"(다음 봉 시가 → {hz}봉째 종가)" if fill else ""
    return [{"id": f"{run}:baseline:half:{a}:{hz}", "predictor": "baseline:half", **base, "p_up": 0.5,
             "p_range": None, "size_lo": None, "size_hi": None, "readings": {}, "used": [],
             "decisive": [], "reason": "늘 50%", "knowledge": ""},
            {"id": f"{run}:baseline:base_rate:{a}:{hz}", "predictor": "baseline:base_rate", **base,
             "p_up": round(br, 4) if br is not None else None, "p_range": None,
             "size_lo": round(w[0], 4) if w else None, "size_hi": round(w[1], 4) if w else None,
             "readings": {}, "used": [], "decisive": [],
             "reason": f"최근 {S.BASE_RATE_WINDOW}봉에서 {hz}봉 수익률{what}이 오른 비율", "knowledge": ""},
            {"id": f"{run}:baseline:all:{a}:{hz}", "predictor": "baseline:all", **base,
             "p_up": round(ar, 4) if ar is not None else None, "p_range": None,
             "size_lo": round(aw[0], 4) if aw else None, "size_hi": round(aw[1], 4) if aw else None,
             "readings": {}, "used": [], "decisive": [],
             "reason": f"알려진 전체 시세({len(h)}봉)에서 {hz}봉 수익률{what}이 오른 비율", "knowledge": ""}]


# ---------- prepare ----------
def prepare(root: Root, day: dt.date, at: dt.datetime, fetch: bool = True, ticker: dict | None = None,
            echo: bool = True) -> int:
    out = root.day_dir(day)
    out.mkdir(parents=True, exist_ok=True)
    fetched = (fetch_prices(root, at) if fetch else
               {a: {"status": "skipped"} for a in [*C.ASSET_IDS, *ST.codes(root.stocks)]})
    tick = ticker if ticker is not None else (fetch_ticker() if fetch else {})
    D = compute(root, day, at, fetched, tick)
    if D.problems:
        return stop(D.problems)
    late = late_note(at, C.PREPARE_BY, "준비(prepare)", D.mode == "broker")
    state = {"day": day.isoformat(), "prepared_at": at.isoformat(timespec="seconds"), "fetch": fetched,
             "decision_price": tick, "predictable": D.predictable, "skipped": D.skipped,
             "missing": [d.isoformat() for d in D.missing], "already": D.already, "warnings": [late] if late else []}
    (out / "prepare.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "briefing.md").write_text(briefing(D, fetched, tick, late), encoding="utf-8")
    tpl = out / "input.json"
    if not tpl.exists():
        tpl.write_text(json.dumps(template(D), ensure_ascii=False, indent=1), encoding="utf-8")
    if echo:
        print(briefing(D, fetched, tick, late))
        print(f"\n→ 틀: {tpl}  (채운 뒤: python -m market.daily record {tpl})")
    elif D.already:
        print(f"{day} 기록이 이미 있다")
    if late and not echo:
        print("경고:", late)
    return 1 if D.already else 0


def stop(problems: list[str]) -> int:
    """증권사 기록이 어긋나 있거나 증권사 안에서 예외가 나면 매일 실행은 아무것도 쓰지 않고 멈춘다(메인 결정, 2026-11-10).
    예외도 같은 글로 시작한다(지시문 9절 표의 한 줄이 둘 다 다룬다)."""
    print(f"가상 증권사 기록이 어긋나 있다({len(problems)}건) — 아무것도 쓰지 않고 멈춘다. `python -m market.broker status`로 본다:")
    for e in problems[:30]:
        print("  -", e)
    return 1


def crash(where: str, exc: BaseException) -> str:
    """증권사 안에서 난 예외를 멈춤 까닭 한 줄로(검토 발견 3 — 예외로 죽으면 다음 날도 같은 곳에서 다시 죽고 까닭이 안 남는다)."""
    first = str(exc).strip().splitlines()
    return f"{where}: 증권사 안에서 예외 {type(exc).__name__}: {' / '.join(first[:4])[:600]}"


def late_note(at: dt.datetime, by: dt.time, what: str, broker: bool = True) -> str:
    """판단 마감 경고: 장 시작 주문은 09:00 시가에 체결되고 예측 기준도 그 시가라, 늦으면 그날 시가를 놓친다. 늦지 않았으면 ''.
    broker = 가상 증권사로 도는 날(늦어도 한국 거래소 주문이 장중이면 지금 가격으로 나간다), 아니면 옛 장부(다음 시가에 체결)."""
    now = at.astimezone(C.KST)
    if now.time() < by:
        return ""
    if now.time() >= C.RECORD_BY and broker:
        return (f"{what} 시각 {now:%H:%M}이 {C.RECORD_BY:%H:%M} 시가 뒤다 — 그날 시가를 놓쳐 한국 거래소 주문은 장중이면 지금 가격으로 "
                f"나가고(장 마감 뒤면 다음 장 시작 주문), 예측 기준(체결 봉)은 다음 봉이라 계좌가 버는 구간과 어긋난다")
    if now.time() >= C.RECORD_BY:
        return (f"{what} 시각 {now:%H:%M}이 {C.RECORD_BY:%H:%M} 시가 뒤다 — 그날 시가를 놓쳐 주문은 다음 시가(ETF·종목 다음 거래일 "
                f"09:00, 코인 다음 날 09:00)에 체결되고, 예측 기준(체결 봉)도 그 봉이다")
    return (f"{what} 시각 {now:%H:%M}이 {by:%H:%M} 뒤다 — 판단(record)이 {C.RECORD_BY:%H:%M} 전에 끝나야 그날 시가에 체결된다")


def briefing(D: Day, fetched: dict, tick: dict, late: str = "") -> str:
    L_ = [f"# {D.day} 준비 (판단 시각 {D.at:%H:%M} KST)", ""]
    if D.mode == "broker":
        L_.append(f"주문은 가상 증권사를 거친다: 비트코인은 판단 때 지금 가격에 바로, ETF·종목은 장이 열리는 09:00 가격에(팔기는 "
                  f"그대로, 사기는 지금 계좌에 있는 돈만큼만 — 같은 09:00에 팔리는 돈은 아직 못 쓴다). 예측 기준은 판단 뒤 첫 "
                  f"시가다 — **record를 {C.RECORD_BY:%H:%M} 전에** 돌린다(늦으면 ETF·종목 주문이 지금 가격으로 나간다).")
        if D.switched and D.recs["account"]:
            L_.append("**갈아타는 날: 옛 대기 주문을 옛 규칙 가격으로 모두 끝내고, 계좌 넷이 오늘부터 가상 증권사로 돈다"
                      "(보유는 그대로, 현금은 은행에서 시작).**")
    else:
        L_.append(f"체결은 판단 뒤 첫 시가(ETF·종목 09:00 시가, 코인 09:00 일봉 시가)이고, 예측 기준도 그 체결가다 — "
                  f"**record를 {C.RECORD_BY:%H:%M} 전에** 돌린다.")
        L_.append(f"**오늘은 옛 장부로 돈다(가상 증권사로 갈아타지 못함): {D.switch_note} — 다음 실행에서 다시 시도한다.**")
    if late:
        L_.append(f"**경고: {late}**")
    if D.already:
        L_.append("**오늘 기록이 이미 있다 — record를 다시 돌리지 않는다.**")
    first = not D.recs["account"]
    if first:
        L_.append("**첫 실행: 오늘이 진짜 계좌 시작일이다(계좌 넷 1,000만 원 현금).**")
    if D.missing:
        L_.append(f"빠진 날 {len(D.missing)}일: {', '.join(d.isoformat() for d in D.missing)} → record가 빈 날로 남긴다(지난 날짜로 예측하지 않는다).")
    L_ += ["", "## 시세"]
    for a in C.ASSET_IDS:
        f = fetched.get(a, {})
        h = D.hists[a]
        L_.append(f"- {C.ASSETS[a].name}: 받기 {f.get('status')} {f.get('added', '')}{' ' + f.get('error', '') if f.get('error') else ''}"
                  f" / 판단에 쓰는 마지막 일봉 {h.last.date if h.last else '없음'} 종가 {h.last.close if h.last else '–'}"
                  + (f" / 체결·예측 기준: {D.base[a]['base_date']} 시가" if a in D.base else "")
                  + (f" / **건너뜀: {D.skipped[a]}**" if a in D.skipped else ""))
    if tick.get("KRW-BTC"):
        t = tick["KRW-BTC"]
        L_.append(f"- 판단 때 가격(업비트 현재가): {t['price']:,.0f}원 ({t['time']})")
    elif tick.get("error"):
        L_.append(f"- 판단 때 가격: 받기 실패 ({tick['error']})")
    L_ += ["", f"## 종목 (살 수 있는 목록 stocks.toml {len(D.pool)}개, 오늘 쓸 수 있는 것 {len(D.stock_ok)}개)"]
    L_.append("손 계좌가 세 대상 말고 살 수 있는 한국 상장 종목이다. 계산기는 종목에 '모름'이다(세 대상에만 맞춘 숫자). "
              "사려면 손 예측 셋(다음·1주·4주)과 1주 결론 조각(decisive)이 있어야 한다 — 지시문 '종목'. "
              f"종목 대 보기에 '예'가 있으면 '아니오' 종목(비교군) {C.STOCK_CONTROL_MIN}개 이상도 stock_readings에 넣는다(틀의 _stock_control).")
    for s in D.pool:
        a = str(s["code"])
        f = fetched.get(a, {})
        h = D.hists.get(a)
        mark = " **[손 계좌가 듦]**" if a in D.held else ""
        if a in D.stock_skipped or not h or not h.last:
            L_.append(f"- {a} {s['name']}{mark}: 받기 {f.get('status', '안 함')}{' ' + f['error'][:120] if f.get('error') else ''}"
                      f" / 건너뜀: {D.stock_skipped.get(a, '시세 없음')[:80]}")
            continue
        c = h.closes
        chg = lambda n: f"{(c[-1] / c[-1 - n] - 1) * 100:+.1f}%" if len(c) > n else "–"
        tm = S.typical_move(h, 1)
        vals = D.calc_values.get(a, {})
        on = [k for k, v in vals.items() if v == "예"]
        L_.append(f"- {a} {s['name']}{mark}: {h.last.date} 종가 {h.last.close:,.0f}원, 하루 {chg(1)}, 1주 {chg(5)}, 4주 {chg(20)}"
                  f", 평소 하루 움직임 {tm * 100:.1f}%" if tm else f"- {a} {s['name']}{mark}: {h.last.date} 종가 {h.last.close:,.0f}원")
        L_[-1] += f" / 켜진 시세 상태: {', '.join(on) or '없음'}" + (" / 처음 받음" if f.get("first") else "")
    L_ += ["", "## 체결·계좌 (지금 평가 — 비트코인은 판단 때 가격, ETF·종목은 마지막 장 마감 가격)"]
    for f in D.fills + [d for k, d in D.sync_lines if k == "fill"]:
        L_.append(f"- 체결 {f['account']} {f['asset']} {f['side']} {f['qty_units']}단위 @ {f['price']:,} ({f['fill_date']})")
    for k, d in D.sync_lines:
        if k == "broker" and d["type"] in ("expire", "adjust"):
            L_.append(f"- {'체결 못 하고 끝남' if d['type'] == 'expire' else '치른 값이 끝난 봉과 다름(돈은 그대로, 적어 둠)'} {d['account']}: {d['note']}")
    for w in D.sync_warnings:
        L_.append(f"- **경고: {w}**")
    for a in D.account_lines:
        pk = a.get("pockets")
        where = f" — 은행 {pk['bank']:,} · 증권 계좌 {pk['sec']:,} · 업비트 {pk['upbit']:,}" if pk else ""
        L_.append(f"- {a['account']}: {a['value']:,.0f}원 (현금 {a['cash']:,}원{where}, 체결 안 된 주문 {len(a['pending'])})")
    if D.mode == "broker":
        hb = D.brokers["live:hand"]
        L_.append(f"- 손 계좌에서 지금 살 수 있는 돈: 증권 계좌 {hb.orderable('sec', D.at) + hb.withdrawable('bank', D.at):,}원"
                  f"(은행 돈 포함), 걸린 주문 {len(hb.pending())}건. ETF·종목을 팔아 다른 것을 사는 몫은 09:00에 팔린 뒤에 살 수 있다"
                  f"(`python -m market.daily open` 또는 다음 날).")
    L_ += ["", f"## 채점 (새로 {len(D.scores)}건, 한 번만 세는 것 {sum(s['counted'] for s in D.scores)}건)"]
    for s in D.scores:
        if s["predictor"] in ("hand", "calc") and s["counted"]:
            L_.append(f"- {s['prediction']}: 수익률 {s['ret'] * 100:+.2f}%, 방향 {s['direction_hit']}, 크기 {s['size_hit']}, 확률 점수 {s['brier']}")
    L_ += ["", f"## 돌아볼 손 예측 ({len(D.due_reviews)}건) — 입력 reviews에"]
    for pid, i in D.due_reviews.items():
        L_.append(f"- {pid}: {i['verdict']} (수익률 {i['ret_pct']:+.2f}%, 평소 {i['typical_pct']}%{', 잡음 가능' if i['noise'] else ''})"
                  f" / 그때 p_up {i['p_up']}, 크기 {i['size']}, 결론 조각 {i['decisive']}")
    L_ += ["", f"## 열린 '다음엔' ({len(D.open_next)}건, 지난 {H.NEXT_DAYS}일) — 오늘 해 볼 상황이 왔으면 해 보고,"
                 " 관련 돌아보기 reason(없으면 notes)에 `지난 다음엔 <id> → 해 봄|안 해 봄 — …`"]
    for rid, t in D.open_next.items():
        L_.append(f"- {rid}: {t}")
    L_ += ["", f"## 다시 볼 손 대 보기 ({len(D.due_checks)}건) — 입력 reading_checks에"]
    for (t, st), c in D.due_checks.items():
        L_.append(f"- {t} {st} = {c['then']} (그때 이유: {c['why_then'][:80]})")
    L_ += ["", "## 계산기 (오늘, 시세 조각 — 방향 기본값은 동전, 기준은 체결가(다음 시가))"]
    for p in D.preds:
        if p["predictor"] == "calc":
            an = p.get("analog")
            ref = (f" [참고 칸, 확률에 안 씀: 같은 스위치 지난 날 {an['n']}개·{an['runs']}구간, 오른 비율 {an['up_rate']:.0%}]"
                   if an else "")
            L_.append(f"- {p['asset']} {p['horizon']}봉 ({p.get('layer', '?')}): {p['reason']}{ref}")
    L_ += ["", "## 오늘 조사 질문 (각도별로 조사 에이전트에게; [씨앗]은 관련 있는 날만 — 묻지 않으면 틀에서 그 줄을 지운다; "
           "[숫자]는 코드가 numbers로 값을 정한다; 모든 답에 numbers·as_of)"]
    for q in D.questions:
        L_.append(f"- [{q['angle']}] `{q['question']}` {q['asked']}\n  추천 볼 곳: {', '.join(q['suggest']) or '없음'}"
                  + (f"\n  숫자(numbers): {', '.join(q.get('numbers') or [])}" if q.get("numbers") else ""))
    L_.append(f"\n새 곳 시도 몫: 각도마다 {RS.NEW_PER_ANGLE}곳 이상(목록에 없거나 {RS.FRESH_DAYS}일 넘게 안 본 곳). "
              f"오래 안 본 목록 곳: {', '.join(sorted(D.stale)) or '없음'}")
    if D.day.weekday() == 6:
        L_.append("\n**일요일: report가 주간 결산도 만든다.**")
    return "\n".join(L_)


def control_note(D: Day, names: dict) -> str:
    """종목 비교군 안내 (2026-11-03, config.STOCK_CONTROL_MIN): 고르는 순서를 미리 정해 결과를 보고 고르지 않게."""
    order = [f"{a} {names.get(a, a)}" for a in D.stock_ok if a not in D.held]
    return (f"stock_readings에 '예' 종목이 있으면 '아니오' 종목도 {C.STOCK_CONTROL_MIN}개 이상 넣는다(비교군 — 없으면 종목 조각이 "
            f"시험되지 않는다). 직전 열흘 안 실적을 낸 다른 회사(좋지 않았거나 전망과 비슷했던 곳)를 먼저, 모자라면 이 순서대로 "
            f"실적 발표가 없던 회사를 채운다: {', '.join(order) or '없음'}. 비교군은 손 예측·비중 없이 대 보기만 적어도 된다"
            f"(그 종목의 계산기 '모름'·기준선 줄이 생겨 채점된다)")


def template(D: Day) -> dict:
    """메인이 채울 입력 틀. '_'로 시작하는 칸은 참고용이고 검사하지 않는다."""
    from .research import NUMBER_KEYS, deep_question, numeric_states, number_keys, research_states, seed_states
    seeds = seed_states(D.raw)
    specs = numeric_states(D.raw)
    meaning = {k: v for keys in NUMBER_KEYS.values() for k, v in keys.items()}
    # 코드가 시세로 미리 채우는 숫자(2026-11-04): 국내 묶음의 평소 하루 움직임 % — 간밤 격차를 '평소의 몇 배'로 재는 잣대
    kr = D.hists.get("069500")
    tm = S.typical_move(kr, 1) if kr is not None and kr.last else None
    prefill = {"kr_overnight_sd60_pct": round(tm * 100, 3)} if tm else {}
    states = [s["id"] for s in research_states(D.raw, per_asset=False) if s["id"] not in seeds]
    per_asset = [s["id"] for s in research_states(D.raw, per_asset=True) if s["id"] not in seeds]
    answers = {s["id"]: s.get("answers") for s in D.raw.get("state", [])}
    calc = {(p["asset"], p["horizon"]): p for p in D.preds if p["predictor"] == "calc"}
    units, total = D.hand_units, D.hand_total   # 든 수량 + 걸린 주문(증권사 기준), 계좌 평가액
    pieces = {pid: info["state"] for pid, info in H._piece_index(D.raw).items()}
    assets = list(D.predictable) + list(D.held)
    names = {str(s["code"]): s["name"] for s in D.pool}
    starts = {pid for pid, info in H._piece_index(D.raw).items() if info["start"]}
    return {
        "day": D.day.isoformat(), "agents": 0, "notes": [],
        "_pieces": {k: f"조건 상태 {v}" if v else ("출발점(값 없이 조각 id만)" if k in starts else "기준값(값 없이 조각 id만)")
                    for k, v in pieces.items()},
        "_info_form": "hand의 info = 결론을 정한 것: 조각 / 출발점(판단 때 가격·간밤 값처럼 판단 때 이미 일어난 움직임) / 기타. "
                      "decisive(결론 조각)가 비면 반드시 적는다(모름 0.5면 '기타'). decisive가 있으면 비워도 된다('조각'으로 기록)",
        "_seed_readings": {st: (f"숫자 씨앗 상태 — 값은 코드가 numbers의 {' ÷ '.join(number_keys(specs[st]))}로 정한다(규칙 "
                                f"'{specs[st]['rule']}'). research의 numbers에 그 숫자를 적으면 readings에 안 넣어도 기록된다. "
                                f"숫자가 없으면 '안 물음'" if st in specs else
                                f"씨앗 조각의 상태 — 관련 있는 날만 readings에 더한다(value·why·places). 답: {answers.get(st) or '예/아니오/일부/모름'}. "
                                f"안 물으면 넣지 않는다(안 물음)") for st in sorted(seeds)},
        "_numbers_form": ("research마다 numbers(숫자 이름_단위 → 값: 이름 끝이 단위 pct·pp·bp·x·z·krw·krw100m·usd·usdm·usdbn·pt·btc·n)와 "
                          "as_of(답의 숫자가 가리키는 시각, 예: " + D.day.isoformat() + "T07:20+09:00, 판단 시각보다 이르게). "
                          "2026-11-05부터 record가 요구한다. 숫자 상태(_seed_readings의 [숫자])의 값은 코드가 numbers로 정하니 메인은 "
                          "숫자만 옮겨 적는다. 틀에 미리 찬 숫자(평소 하루 움직임)는 코드가 시세로 계산한 것 — 지우지 않는다"),
        "_deep_form": deep_question(D.day, "<대상 코드>", "<대상 이름>"),
        "_used_form": "used·decisive는 '조각 id=값'. 값은 그 조각 조건 상태의 오늘 값(조사 조각은 readings, 종목마다 대 보는 "
                      "조사 조각은 stock_readings의 그 종목 값, 계산 조각은 _values)",
        "_stocks": {a: {"name": names.get(a, a), "held": a in D.held, "base_date": D.base[a]["base_date"],
                        "close": D.hists[a].last.close, "close_date": D.hists[a].last.date.isoformat(),
                        "_values": D.calc_values.get(a, {})} for a in D.stock_ok},
        "_stock_rule": (f"종목을 사려면 hand에 그 종목의 기간 셋(1·5·20)을 더하고, 1주 p_up > 0.5와 decisive(결론 조각)를 적고, "
                        f"weights에 target(≤ {C.STOCK_MAX_WEIGHT}, 종목 {C.STOCK_MAX_COUNT}개까지)을 넣는다. 종목 몫은 한국 묶음에서 "
                        f"덜어 낸다. 든 종목은 매일 hand·weights에 남긴다(팔려면 target 0). 새 종목은 python -m market.stocks add"),
        "_stock_control": control_note(D, names) if per_asset else "",
        "research": [{"question": q["question"], "state": q["state"], "angle": q["angle"], "asked": q["asked"],
                      "answer": "", "agent": "", "_suggest": q["suggest"],
                      "numbers": {k: v for k, v in prefill.items() if k in q.get("numbers", [])}, "as_of": "",
                      "_numbers": {k: meaning.get(k, "숫자 상태가 보는 숫자") for k in q.get("numbers", [])},
                      "looked": [{"place": "", "result": "찾음", "new": False, "url": "", "date": "", "note": ""}]}
                     for q in D.questions],
        "readings": {st: {"value": "모름", "why": "", "places": []} for st in states},
        "stock_readings": {a: {st: {"value": "모름", "why": "", "places": []} for st in per_asset} for a in D.held}
                          if per_asset else {},
        "hand": [{"asset": a, "horizon": hz, "p_up": None, "size_lo": None, "size_hi": None, "used": [],
                  "decisive": [], "info": "", "reason": "", "confidence": "낮음",
                  "_calc": calc[(a, hz)]["reason"] if (a, hz) in calc else "",
                  "_calc_p_up": calc[(a, hz)]["p_up"] if (a, hz) in calc else None,
                  "_calc_layer": calc[(a, hz)].get("layer") if (a, hz) in calc else None,
                  "_values": D.calc_values.get(a, {})}
                 for a in assets for hz in C.ASSETS[a].horizons],
        "weights": {a: {"target": round(units.get(a, 0) * D.prices[a] / C.ASSETS[a].unit / total, 4) if total else 0.0,
                        "reason": ""} for a in assets},
        "reviews": [{"prediction": pid, "why": "", "wrong": [], "reason": "", "_info": info}
                    for pid, info in D.due_reviews.items()],
        "corrections": [],
        "reading_checks": [{"target": t, "state": st, "now": "", "hit": None, "late": [], "reason": "",
                            "_then": c["then"], "_why_then": c["why_then"]} for (t, st), c in D.due_checks.items()],
    }


# ---------- record ----------
def record(root: Root, day: dt.date, at: dt.datetime, input_path: Path) -> int:
    prep_p = root.day_dir(day) / "prepare.json"
    if not prep_p.exists():
        print(f"{prep_p}가 없다 — 먼저 python -m market.daily prepare")
        return 1
    prep = json.loads(prep_p.read_text(encoding="utf-8"))
    inp = H.load_input(input_path)
    focus = {str(h.get("asset")) for h in inp.get("hand", []) if isinstance(h, dict)} | set(inp.get("weights") or {})
    focus |= set(inp.get("stock_readings") or {})
    D = compute(root, day, at, prep["fetch"], prep.get("decision_price") or {}, focus={a for a in focus if C.is_stock(a)})
    if D.problems:
        return stop(D.problems)
    if D.already:
        print(f"{day} 기록이 이미 있다 — 같은 날 두 번 쓰지 않는다(정정은 correction 기록으로)")
        return 1
    ctx = {"day": day.isoformat(), "raw": D.raw,
           "research_states": [s["id"] for s in RS.research_states(D.raw, per_asset=False)],
           "stock_states": [s["id"] for s in RS.research_states(D.raw, per_asset=True)],
           "seed_states": sorted(RS.seed_states(D.raw)),
           "places": [p["id"] for p in D.places], "stale": D.stale, "predictable": D.predictable,
           "stocks": D.stock_ok, "held": D.held, "buyable": {str(s["code"]) for s in D.pool if s.get("buy", True)},
           "hand_units": D.hand_units,
           "calc_values": D.calc_values, "due_reviews": D.due_reviews, "due_checks": D.due_checks,
           "open_next": D.open_next,
           "node_ids": {n.get("id") for k in ("goal", "state", "fact", "hypothesis", "rule") for n in D.raw.get(k, [])},
           "record_ids": {x["id"] for items in D.recs.values() for x in items}}
    ctx["at"] = at.isoformat(timespec="seconds")  # 조사 as_of가 판단 시각보다 늦으면 거절(2026-11-04, E4)
    errs, warns = H.validate(inp, ctx)
    if errs:
        print(f"입력 오류 {len(errs)}건 — 아무것도 쓰지 않았다:")
        for e in errs:
            print("  -", e)
        return 1
    hand = H.lines(inp, ctx, D.digest, D.base)
    warns += RS.new_share_warnings(hand["research"], D.places, D.stale)

    # 주문: 목표 비중 → 주문 계획 → 가상 증권사(계좌 넷 모두, 16절). 갈아타지 못한 날만 옛 장부(판단 뒤 첫 시가에 체결).
    targets = {a: float(w["target"]) for a, w in (inp.get("weights") or {}).items()}
    why = {a: str(w["reason"]) for a, w in (inp.get("weights") or {}).items()}
    plans: dict[str, RB.Plan] = {}
    hand_orders: list[dict] = []
    if D.mode == "broker":
        clock = RB.Clock(at, root.live)
        jobs = [("live:hand", targets, RB.BAND, why), ("live:calc", D.calc_w, RB.BAND, D.calc_reasons)]
        if D.equal_first:
            jobs.append(("baseline:equal_hold", D.equal_first, 0.0, {a: EQUAL_NOTE for a in D.equal_first}))
        try:
            for aid, tg, band, rs in jobs:
                plans[aid] = RB.rebalance(D.brokers[aid], tg, D.prices, clock, band, rs)
            for aid, b in D.brokers.items():   # 계좌 줄: 판단 때의 상태(그날 낸 지금 주문과 이체까지 반영), 하루 한 줄
                res = b.snapshot(clock.now(), day, D.value_prices)
                warns += [f"{aid}: {w}" for w in res.warnings] + ([] if res.ok else [f"{aid}: {res.reason}"])
        except Exception as exc:   # 증권사 안의 예외: 아무것도 쓰지 않고 멈춘다(예외로 죽지 않는다)
            return stop([crash("주문·계좌 줄", exc)])
        for aid, plan in plans.items():
            warns += [f"{aid}: {w}" for w in plan.warnings]
    else:
        hand_orders = [o.record() for o in D.accounts["live:hand"].plan(day, targets, D.prices, 0.02, why, at, C.FILL_RULE)]
        warns.append(f"오늘은 옛 장부로 돌았다(가상 증권사로 갈아타지 못함): {D.switch_note}")
    warns += D.sync_warnings
    late = late_note(at, C.RECORD_BY, "판단(record)", D.mode == "broker")
    if late:
        warns.append(late)

    run = day.isoformat()
    lines: dict[str, list[dict]] = {k: [] for k in KIND_ORDER}
    for d in D.missing:
        lines["run"].append({"id": f"missed:{d.isoformat()}", "run": d.isoformat(), "mode": "missed", "steps": {},
                             "notes": ["실행 없음 — 빈 날. 지난 날짜로 예측하지 않는다"]})
    lines["fill"] = list(D.fills)   # 옛 모양 체결 줄(갈아타기 전 정산·갈아타는 날 옛 주문 끝내기) — 옛 장부가 만든 것
    lines["score"] = D.scores
    lines["account"] = [] if D.mode == "broker" else D.account_lines
    lines["reading"] = D.readings
    lines["research"] = hand["research"]
    lines["hand_reading"] = hand["hand_reading"]
    lines["prediction"] = D.preds + hand["prediction"]
    lines["order"] = D.calc_orders + hand_orders
    for b in D.brokers.values() if D.mode == "broker" else ():   # 가상 증권사가 만든 줄(주문·체결·사건·계좌)
        for k in B.KINDS:
            lines[k] += b.new[k]
    rejects = [x for x in lines["broker"] if x["type"] == "reject"]
    lines["review"] = hand["review"]
    lines["reading_check"] = hand["reading_check"]
    lines["correction"] = hand["correction"]
    looked = [lk for r in hand["research"] for lk in r["looked"]]
    lines["run"].append({
        "id": f"live:{run}", "run": run, "mode": "live",
        "steps": {"prepared_at": prep["prepared_at"], "decided_at": at.isoformat(timespec="seconds"),
                  "fetch": {a: f.get("status") for a, f in prep["fetch"].items()},
                  "fetch_errors": {a: f["error"] for a, f in prep["fetch"].items() if f.get("error")},
                  "decision_price": prep.get("decision_price") or {}, "skipped": D.skipped,
                  "stocks": {"pool": len(D.pool), "usable": len(D.stock_ok), "held": D.held, "recorded": D.focus,
                             "skipped": {a: w for a, w in D.stock_skipped.items() if a in set(D.held) | focus}},
                  "fills": len(lines["fill"]), "scores": len(D.scores),
                  "broker": {"mode": D.mode, "switched": D.switched, "rejects": len(rejects),
                             "transfers": sum(x["type"] == "transfer" for x in lines["broker"]),
                             "expired": sum(x["type"] == "expire" for x in lines["broker"])},
                  "counted_scores": sum(s["counted"] for s in D.scores),
                  "predictions": len(lines["prediction"]), "orders": len(lines["order"]),
                  "research_questions": len(hand["research"]), "places_looked": len({lk["place"] for lk in looked}),
                  "new_place_tries": sum(bool(lk.get("new")) for lk in looked),
                  "agents": int(inp.get("agents", 0) or 0), "reviews": len(hand["review"]),
                  "reading_checks": len(hand["reading_check"]), "knowledge": D.digest,
                  "known_through": {a: h.last.date.isoformat() if h.last else None for a, h in D.hists.items()}},
        "notes": [str(n) for n in inp.get("notes", [])] + [f"경고: {w}" for w in warns]})

    # 모두 검사한 뒤에 쓴다 (하나라도 틀리면 아무것도 안 쓴다)
    problems = []
    for kind, items in lines.items():
        ids = {x["id"] for x in D.recs.get(kind, [])}
        for i, it in enumerate(items):
            problems += R.line_errors(kind, {"kind": kind, **it}, f"{kind}+{i}")
            if it["id"] in ids:
                problems.append(f"{kind}: id '{it['id']}'가 이미 있음")
            ids.add(it["id"])
    if problems:
        print(f"기록 칸 오류 {len(problems)}건 — 아무것도 쓰지 않았다:")
        for e in problems[:50]:
            print("  -", e)
        return 1
    for kind in KIND_ORDER:
        if lines[kind]:
            R.write(root.records, kind, lines[kind])
    print(f"{run} 기록: " + ", ".join(f"{k} {len(v)}" for k, v in lines.items() if v))
    if D.switched and D.recs["account"]:
        print("  갈아탐: 옛 대기 주문을 옛 규칙 가격으로 모두 끝내고 계좌 넷이 가상 증권사로 시작했다(start 줄 — 보유는 그대로, "
              "현금은 은행에). 오늘 일지 '왜'나 조사 메모에 한 줄 적는다.")
    for k, d in D.sync_lines:
        if k == "fill":
            print(f"  [{d['account']}] 지난 주문 체결: {C.ASSETS[d['asset']].name} {'사기' if d['side'] == 'buy' else '팔기'} "
                  f"{d['qty_units']}단위 @ {d['price']:,.0f}원 ({d['at'][:16]})")
        elif d.get("type") == "expire":
            print(f"  [{d['account']}] 체결 못 하고 끝난 주문: {d['data'].get('order')} — {d['note']}")
    for aid, plan in plans.items():
        for line in RB.describe(plan, lambda a: C.ASSETS[a].name if a else ""):
            if not line.startswith(("지난 주문 체결", "체결 못 하고")):
                print(f"  [{aid}] {line}")
    for w in warns:
        print("  경고:", w)
    return 0


# ---------- open (장 시작 뒤 단계) ----------
def open_phase(root: Root, day: dt.date, at: dt.datetime) -> int:
    """09:00 뒤 아무 때나, 하루 한 번(16절 '장 시작 뒤'): 계좌마다 맞추기(오늘 시가 체결을 장부에) → 오늘 plan 줄의 목표 비중으로
    계획을 다시 돌림(한국 거래소는 지금 주문 — 팔린 돈으로 바로 산다) → phase 줄. 계좌 줄은 새로 쓰지 않는다(하루 한 줄).
    오늘 판단(run 줄)이 없거나, 이미 돌았거나, 아직 갈아타기 전이면 아무것도 하지 않는다."""
    run = day.isoformat()
    recs = {k: R.read(root.records, k) for k in ("run", *B.KINDS)}
    if not any(r["run"] == run and r.get("mode") == "live" for r in recs["run"]):
        print(f"{run} 판단 기록(run)이 없다 — 장 시작 뒤 단계는 판단이 돈 날에만 돈다. 아무것도 쓰지 않았다")
        return 0
    if not all(any(x["account"] == aid and x["type"] == "start" for x in recs["broker"]) for aid in C.LIVE_ACCOUNTS):
        print("아직 가상 증권사로 갈아타기 전이다 — 아무것도 쓰지 않았다")
        return 0
    quotes = quotes_for(root)
    ST.register(root.stocks)
    rows = {p.stem: PR.load(p.stem, root.prices) for p in sorted(root.prices.glob("*.csv"))}
    hists = {a: PR.view(a, r, at) for a, r in rows.items() if a in C.CORE or C.is_stock(a)}
    bar_days = [r.date for r in hists["069500"].rows] if "069500" in hists else None
    try:
        brokers = {aid: B.Broker(aid, quotes, recs, bar_days) for aid in C.LIVE_ACCOUNTS}
    except Exception as exc:
        return stop([crash("기록 다시 돌리기", exc)])
    problems = [e for b in brokers.values() for e in b.problems]
    if problems:
        return stop(problems)
    try:
        said = open_accounts(brokers, run, at, quotes, hists, root.live)
    except Exception as exc:   # 증권사 안의 예외: 아무것도 쓰지 않고 멈춘다
        return stop([crash("장 시작 뒤 단계", exc)])
    if isinstance(said, int):
        return said
    lines = {k: [x for b in brokers.values() for x in b.new[k]] for k in B.KINDS}
    problems = []
    for kind, items in lines.items():
        ids = {x["id"] for x in recs.get(kind, [])}
        for i, it in enumerate(items):
            problems += R.line_errors(kind, {"kind": kind, **it}, f"{kind}+{i}")
            if it["id"] in ids:
                problems.append(f"{kind}: id '{it['id']}'가 이미 있음")
            ids.add(it["id"])
    if problems:
        print(f"기록 칸 오류 {len(problems)}건 — 아무것도 쓰지 않았다:")
        for e in problems[:50]:
            print("  -", e)
        return 1
    for kind in KIND_ORDER:
        if lines.get(kind):
            R.write(root.records, kind, lines[kind])
    print(f"{run} 장 시작 뒤 단계({at:%H:%M}): " + (", ".join(f"{k} {len(v)}" for k, v in lines.items() if v) or "쓴 줄 없음"))
    for x in said:
        print("  " + x)
    return 0


def open_accounts(brokers: dict, run: str, at: dt.datetime, quotes: Q.Quotes, hists: dict, live: bool) -> list[str] | int:
    """open_phase의 계좌마다 일(기록은 쓰지 않는다 — 증권사의 새 줄로 남는다). 말할 줄 목록, 멈추면 종료 코드."""
    clock = RB.Clock(at, live)
    said, live_prices = [], {}

    def price(a: str, t: dt.datetime) -> float | None:
        """지금 가격(실제로 받은 것만). 한국 거래소는 장이 열려 있을 때만."""
        if a not in live_prices:
            n = quotes.now(a, t)
            ok = n is not None and n.price > 0 and (C.ASSETS[a].kind == "coin" or n.open)
            live_prices[a] = n.price if ok else None
        return live_prices[a]

    for aid, b in brokers.items():
        if any(x["data"].get("name") == "open" and x["run"] == run for x in b.events("phase")):
            said.append(f"[{aid}] 오늘 장 시작 뒤 단계는 이미 돌았다 — 아무것도 하지 않는다")
            continue
        t = clock.now()
        res = b.sync(t)
        if not res.ok:
            return stop([f"{aid}: {res.reason}"])
        waiting = b.unbooked_open(t)
        if waiting:   # 오늘 장 시작 주문이 아직 장부에 안 들어왔다(오늘 봉을 못 받음): phase 줄을 남기지 않는다 — 다시 돌릴 수 있게
            said.append(f"[{aid}] 오늘 장 시작 주문 {len(waiting)}건이 아직 장부에 안 들어왔다(오늘 봉의 시가를 아직 못 받음: "
                        f"{', '.join(sorted({p.asset for p in waiting}))}) — 이 계좌는 아무것도 사지 않고 단계 표시도 남기지 않았다. "
                        f"몇 분 뒤 `python -m market.daily open`을 다시 돌린다")
            said += [f"[{aid}] {x}" for x in RB.describe(RB.Plan(lines=list(res.lines), warnings=list(res.warnings)),
                                                         lambda a: C.ASSETS[a].name if a else "")]
            continue
        plan = RB.Plan(lines=list(res.lines), warnings=list(res.warnings))
        todays = [x for x in b.events("plan") if x["run"] == run]
        if todays:
            data = todays[-1]["data"]
            targets = dict(data.get("targets", {}))
            prices, lost = {}, []
            for a in sorted(set(targets) | set(b.holdings())):
                got = price(a, t)
                if got is None:   # 지금 가격을 못 받은 대상은 주문하지 않고, 계좌 크기를 재는 데만 마지막 종가를 쓴다
                    lost += [a] if a in targets else []
                    got = hists[a].last.close if a in hists and hists[a].last else None
                if got is not None:
                    prices[a] = got
            more = RB.rebalance(b, {a: w for a, w in targets.items() if a not in lost}, prices, clock,
                                float(data.get("band", RB.BAND)), data.get("reasons", {}), phase="open")
            plan.lines += more.lines
            plan.notes += more.notes + ([f"지금 가격을 못 받은 대상은 건너뜀: {', '.join(lost)}"] if lost else [])
            plan.warnings += more.warnings
        else:
            plan.notes.append("오늘 목표 비중(plan 줄)이 없다 — 맞추기만 했다")
        b.mark(clock.now(), "phase", {"name": "open", "orders": len(plan.of("order")),
                                      "fills": len(plan.of("fill")), "rejects": len(plan.of("broker", "reject"))})
        said += [f"[{aid}] {x}" for x in RB.describe(plan, lambda a: C.ASSETS[a].name if a else "")]
    return said


# ---------- 명령줄 ----------
def _args(argv: list[str]) -> tuple[list[str], dict]:
    pos, opt, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a in ("--root", "--day", "--at"):
            opt[a] = argv[i + 1]
            i += 2
        else:
            (opt.__setitem__(a, True) if a.startswith("--") else pos.append(a))
            i += 1
    return pos, opt


def when(opt: dict, default_time: dt.time) -> tuple[Root, dt.date, dt.datetime]:
    root = Root(Path(opt.get("--root", C.ROOT)).resolve())
    live = root.path == C.ROOT.resolve()
    if live and ("--day" in opt or "--at" in opt):
        raise SystemExit("진짜 기록 폴더에서는 --day/--at을 쓰지 않는다(지난 날짜로 예측하지 않는다). 흉내 내기는 --root <임시 폴더>")
    if live:
        at = C.now_kst()
        return root, at.date(), at
    day = dt.date.fromisoformat(opt["--day"]) if "--day" in opt else C.now_kst().date()
    t = dt.time.fromisoformat(opt["--at"]) if "--at" in opt else default_time
    return root, day, dt.datetime.combine(day, t, C.KST)


def main(argv: list[str]) -> int:
    pos, opt = _args(argv)
    if not pos or pos[0] not in ("prepare", "record", "report", "open"):
        print(__doc__)
        return 2
    if pos[0] == "prepare":
        root, day, at = when(opt, C.DECISION_TIME)
        return prepare(root, day, at, fetch="--no-fetch" not in opt)
    if pos[0] == "record":
        if len(pos) < 2:
            print("입력 파일을 준다: python -m market.daily record out/daily/<날짜>/input.json")
            return 2
        root, day, at = when(opt, dt.time(8, 30))
        return record(root, day, at, Path(pos[1]))
    if pos[0] == "open":
        root, day, at = when(opt, dt.time(9, 5))
        return open_phase(root, day, at)
    from . import report as RP
    root, day, _ = when(opt, dt.time(9, 0))
    if "--check" in opt:
        return RP.check(root, day)
    return RP.write(root, day, force="--force" in opt)


def copy_for_simulation(dst: Path, prices_from: Path | None = None) -> Root:
    """흉내 내기용 임시 저장소: 논리집·볼 곳·종목 목록·버전(그리고 시세)을 복사한다. 기록은 빈 채로."""
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("knowledge.toml", "places.toml", "stocks.toml"):
        if (C.ROOT / name).exists():
            shutil.copyfile(C.ROOT / name, dst / name)
    shutil.copytree(C.VERSIONS, dst / "versions", dirs_exist_ok=True)
    if prices_from is not None:
        shutil.copytree(prices_from, dst / "data" / "prices", dirs_exist_ok=True)
    return Root(dst)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
