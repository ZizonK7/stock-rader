"""일지·주간 결산 뼈대: 숫자는 기록에서 코드가 채우고, 말로 쓰는 칸은 자리만 남긴다 (docs/report-style.md).

`python -m market.daily report` 가 부른다. 만드는 파일:
- reports/YYYY-MM-DD.md (매일 일지)
- reports/YYYY-MM-DD-week.md (일요일, 주간 결산)
말로 쓰는 자리는 `[[채울 것: …]]`로 남는다. 메인이 채운 뒤 `python -m market.daily report --check`로 남은 자리가 없는지 본다.
채울 때 참고할 사실은 `<!-- 참고: … -->` 주석으로 붙어 있다(보이지 않는다, 지워도 된다). 숫자는 고치지 않는다.
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from . import config as C
from . import records as R
from . import research as RS
from . import scoring as S
from . import stocks as ST

MARK = "[[채울 것"
DISCLAIM = "실제 돈이 아닌 가상 투자 실험이며 투자 권유가 아니다."
REPO_URL = "https://github.com/ZizonK7/stock-rader/blob/main"
# 붙어 있는 물결표 둘(47~49%, 0.6~1.2%)은 GitHub·블로그 Markdown에서 그 사이 글을 취소선으로 바꾼다(2026-10-21 첫 블로그 글).
TILDE = re.compile(r"(?<![\\\s])~(?!\s)")


def won(x: float, sign: bool = True) -> str:
    s = f"{abs(x):,.0f}원"
    return (("+" if x >= 0 else "−") + s) if sign else s


def won_about(x: float) -> str:
    """글 속 금액: 블로그로도 읽히므로 만 원 단위로 반올림한다(정확한 숫자는 계좌 표와 기록에)."""
    return f"약 {round(abs(x) / 10_000):,}만 원" if abs(x) >= 10_000 else f"약 {abs(x):,.0f}원"


def pct(x: float) -> str:
    return ("+" if x >= 0 else "−") + f"{abs(x) * 100:.2f}%"


def units_text(asset: str, units: int) -> str:
    spec = C.ASSETS[asset]
    return f"{units}주" if C.on_exchange(asset) else f"{units / spec.unit:.8f}개"


def asset_label(asset: str) -> str:
    """표·글 속 이름. 종목은 이름 뒤에 '(종목)'을 붙여 묶음 상품과 구별한다."""
    return f"{C.ASSETS[asset].name}(종목)" if C.is_stock(asset) else C.ASSETS[asset].name


class Book:
    """한 기록 폴더를 읽어 날짜별 계좌·체결·주문·채점을 꺼내는 도우미."""

    def __init__(self, folder: Path):
        ST.register(folder.parent / "stocks.toml")  # 종목 이름 (기록 폴더의 저장소 루트)
        self.recs = {k: R.read(folder, k) for k in R.FIELDS}
        self.acc = defaultdict(dict)  # 계좌 → 날짜 → 기록
        # 가상 증권사로 갈아탄 계좌(start 줄)는 start 실행 날짜부터 seq 있는 계좌 줄만 읽는다 — 증권사가 만들지 않은 줄(손으로 덧붙인
        # 옛 모양 줄)이 그날 평가액이 되지 않게(검토 발견 2c; records check도 그 줄을 오류로 잡는다)
        started = {x["account"]: x["run"] for x in self.recs.get("broker", []) if x.get("type") == "start"}
        for a in self.recs["account"]:
            if "seq" not in a and a["account"] in started and a["run"] >= started[a["account"]]:
                continue
            self.acc[a["account"]][a["run"]] = a
        self.preds = {p["id"]: p for p in self.recs["prediction"]}
        # 갈아타는 날(2026-11-10 3회) 옛 주문을 그대로 다시 넣은 줄: 그 주문·체결과 갈아타는 시각(phase 'carry' 줄의 시각) 전의 사건은
        # 그날 '오늘 한 일'이 아니다(지난 일을 장부에 다시 넣은 것) — 일지의 오늘 문장·표에서 뺀다
        self.replayed = {o["id"] for o in self.recs["order"] if o.get("replay_of")}
        self.carry_at = {x["account"]: x["at"] for x in self.recs.get("broker", [])
                         if x.get("type") == "phase" and (x.get("data") or {}).get("name") == "carry"}
        self.switch_run = started

    def replay(self, x: dict, kind: str) -> bool:
        """그대로 다시 넣은 줄인가(주문·체결·증권사 사건)."""
        if kind == "order":
            return x["id"] in self.replayed
        if kind == "fill":
            return x.get("order") in self.replayed
        until = self.carry_at.get(x.get("account"))
        return bool((x.get("data") or {}).get("replay_of")) or (until is not None and x.get("at", "") < until)

    def value(self, account: str, run: str) -> float | None:
        a = self.acc[account].get(run)
        return a["value"] if a else None

    def prev_run(self, account: str, run: str) -> str | None:
        older = sorted(r for r in self.acc[account] if r < run)
        return older[-1] if older else None

    def invested(self, account: str, asset: str, until: str, since: str = "", upto: dict | None = None,
                 after: dict | None = None) -> int:
        """체결 기준 순투자(산 금액 + 수수료 − (판 금액 − 수수료 − 세금)). 옛 장부 체결은 실행 날짜(since < run ≤ until)로 센다.
        가상 증권사 체결(seq가 있는 줄)은 계좌 줄 upto에 반영된 것까지(seq ≤ upto의 seq), after 계좌 줄 뒤의 것만 센다 —
        계좌 줄을 쓴 뒤 같은 날 들어온 체결(장 시작 뒤 단계)이 그날 표의 숫자에 섞이지 않게."""
        hi = (upto or {}).get("seq")
        lo = (after or {}).get("seq")
        t = 0
        for f in self.recs["fill"]:
            if f["account"] != account or f["asset"] != asset:
                continue
            if "seq" not in f and hi is not None:
                continue   # 가상 증권사 계좌 줄(seq)의 역사는 증권사 줄뿐이다 — 갈아탄 뒤 옛 장부 체결은 견주지 않는다(비교용)
            if "seq" in f:
                if (f["seq"] > hi if hi is not None else f["run"] > until) or (lo is not None and f["seq"] <= lo):
                    continue
                if lo is None and after is None and not since < f["run"]:
                    continue
            elif not since < f["run"] <= until:
                continue
            t += (f["value"] + f["fee"]) if f["side"] == "buy" else -(f["value"] - f["fee"] - f.get("tax", 0))
        return t

    def window(self, kind: str, account: str, me: dict, pa: dict | None) -> list[dict]:
        """가상 증권사가 만든 줄 중 앞 계좌 줄(pa) 뒤 ~ 이 계좌 줄(me)까지의 것(seq 순서)."""
        lo = (pa or {}).get("seq", 0)
        return sorted((x for x in self.recs[kind] if x.get("account") == account and "seq" in x and lo < x["seq"] <= me["seq"]),
                      key=lambda x: x["seq"])

    def counted(self, run_from: str = "", run_to: str = "9999") -> list[tuple[dict, dict]]:
        pairs = [(self.preds[s["prediction"]], s) for s in self.recs["score"]
                 if s["prediction"] in self.preds and run_from <= s["run"] <= run_to and s.get("counted", True)]
        return pairs


def asset_value(acc: dict, asset: str) -> float:
    pr = acc["prices"].get(asset)
    return acc["positions"].get(asset, 0) * pr[1] / C.ASSETS[asset].unit if pr else 0.0


def order_amount(o: dict) -> float:
    """주문의 어림 금액(원): 수량 × 주문 수량을 잡은 가격(ref_price). 업비트 금액 사기는 그 금액."""
    if o.get("amount"):
        return float(o["amount"])
    return o["qty_units"] * o["ref_price"] / C.ASSETS[o["asset"]].unit


def queued_size(o: dict) -> str:
    """걸어 둔 주문의 크기를 일지 말로: '약 30만 원어치'. 수량을 잡은 가격을 모르는 주문(ref_price 0 — 전날 마감 가격을 아직
    못 받은 때의 팔기)은 금액을 지어내지 않고 수량으로('15주')."""
    amt = order_amount(o)
    if amt > 0:
        return f"{won_about(amt)}어치"
    n = o["qty_units"] / C.ASSETS[o["asset"]].unit
    return f"{n:,.0f}주" if C.on_exchange(o["asset"]) else f"{n:.8f}개"


# 거절 이유(증권사의 글)를 일지에 쓸 쉬운 말로. 앞에서부터 처음 맞는 것
REJECT_WORDS = (("거래 정지 여부를 모름", "거래할 수 있는 상태인지 알 수 없어서"), ("돈이 모자람", "그때 계좌에 쓸 수 있는 돈이 모자라서"), ("옮길 수 있는 돈", "아직 꺼낼 수 없는 돈이라서"),
                ("가진 것보다", "가진 수량보다 많아서"), ("받지 못함", "그 순간의 가격을 받지 못해서"),
                ("묵었음", "받은 가격이 너무 오래된 것이라서"), ("시계가", "받은 가격의 시각이 맞지 않아서"),
                ("최소 주문", "금액이 너무 작아서"), ("닫혀", "장이 닫혀 있어서"), ("장중", "주문을 낼 수 없는 시간이라서"),
                ("15:20", "주문을 낼 수 없는 시간이라서"), ("전 거래일 종가를 몰라", "전날 마감 가격을 아직 받지 못해서"),
                ("가격 제한", "하루에 움직일 수 있는 값의 범위를 벗어나서"), ("전 거래일 종가", "전날 마감 가격을 받지 못해서"), ("지난 시각", "지난 시각으로는 주문할 수 없어서"),
                ("거래 정지", "그 종목의 거래가 멈춰 있어서"), ("정규장", "정규 거래 시간의 가격이 아니라서"),
                ("지연 시세", "늦게 오는 가격이라서"))


def reject_reason(note: str) -> str:
    return next((plain for key, plain in REJECT_WORDS if key in note), "증권사 규칙에 맞지 않아서")


def md(text: str) -> str:
    """'2026-11-10T09:00:00+09:00'이나 '2026-11-10' → '11월 10일'."""
    return f"{int(text[5:7])}월 {int(text[8:10])}일"


def broker_first(b: Book, me: dict, pa: dict | None, run: str) -> tuple[str, list[str]]:
    """가상 증권사로 도는 날의 첫 문장과 참고 사실: 오늘 바로 치러진 것(실제 값) / 장이 열리면 치를 주문 / 받아들여지지 않은 주문."""
    events = [e for e in b.window("broker", "live:hand", me, pa) if e["run"] == run and not b.replay(e, "broker")]
    gone = {e["data"].get("order") for e in events if e["type"] == "cancel"}   # 같은 날 취소한 주문은 '넣었다'에서 뺀다
    orders = [o for o in b.window("order", "live:hand", me, pa) if o["run"] == run and o["id"] not in gone
              and not b.replay(o, "order")]
    fills = {f["order"]: f for f in b.window("fill", "live:hand", me, pa)}
    parts, ref = [], []
    for side, did in (("buy", "샀다"), ("sell", "팔았다")):
        got = [f"{asset_label(o['asset'])} {won_about(fills[o['id']]['value'])}어치" for o in orders
               if o["side"] == side and o["id"] in fills]
        if got:
            parts.append(", ".join(got) + f"를 바로 {did}")
    if parts:
        parts[-1] += "(그 순간의 실제 가격)"
    queued = []
    for side, verb in (("buy", "사는"), ("sell", "파는")):
        got = [f"{asset_label(o['asset'])} {queued_size(o)}" for o in orders
               if o["side"] == side and o["id"] not in fills]
        if got:
            queued.append(", ".join(got) + f"를 {verb} 주문")
    if queued:
        days = sorted({o["session"] for o in orders if o["id"] not in fills and o.get("session")})
        when = "오늘 장이 열리는 09:00 가격" if days == [run] else (
            f"다음 장이 열리는 {md(days[0])} 09:00 가격" if days else "장이 열리는 09:00 가격")
        parts.append("과 ".join(queued) + f"을 넣었다({when}에 치른다)")
    rejects = [e for e in events if e["type"] == "reject" and e["data"].get("type") == "order"]
    if rejects:
        parts.append("받아들여지지 않은 주문이 " + ", ".join(
            f"{asset_label(e['data']['asset'])} {'사기' if e['data'].get('side') == 'buy' else '팔기'}({reject_reason(e['note'])})"
            for e in rejects) + " 있었다")
        ref += [f"거절: {e['data'].get('asset')} {e['data'].get('side')} — {e['note']}" for e in rejects]
    first = ("오늘 " + ". ".join(parts) + ".") if parts else "오늘은 거래하지 않았다."
    return first, ref


def broker_notes(b: Book, me: dict, pa: dict | None, run: str) -> list[str]:
    """표 아래 문장들: 지난 주문이 치러진 값, 치러지지 못하고 끝난 주문, 아직 치러지지 않은 주문, 현금이 있는 곳과 오늘 옮긴 돈."""
    out = []
    orders = {o["id"]: o for o in b.recs["order"] if o["account"] == "live:hand"}
    todays = {o["id"] for o in b.window("order", "live:hand", me, pa) if o["run"] == run}
    earlier = [f for f in b.window("fill", "live:hand", me, pa) if f["order"] not in todays and not b.replay(f, "fill")]
    if earlier:
        out.append(paid_text(earlier))
    events = [e for e in b.window("broker", "live:hand", me, pa) if not b.replay(e, "broker")]
    ended = [e for e in events if e["type"] == "expire"]
    if ended:
        out.append("값이 맞지 않아 치러지지 못하고 끝난 주문: " + ", ".join(
            f"{asset_label(orders[e['data']['order']]['asset'])} {'사기' if orders[e['data']['order']]['side'] == 'buy' else '팔기'}"
            for e in ended if e["data"].get("order") in orders) + ".")
    dropped = [e for e in events if e["type"] == "cancel" and e["data"].get("order") in orders]
    if dropped:   # 주문 계획이 목표가 바뀐 걸린 주문을 취소하고 다시 낸다(2026-11-10 2회차)
        out.append("목표가 바뀌어 걸어 둔 주문을 취소했다(필요한 만큼은 위 주문으로 다시 냈다): " + ", ".join(
            f"{asset_label(orders[e['data']['order']]['asset'])} {won_about(order_amount(orders[e['data']['order']]))}어치 "
            f"{'사기' if orders[e['data']['order']]['side'] == 'buy' else '팔기'}" for e in dropped) + ".")
    waiting = [orders[i] for i in me["pending"] if i in orders]
    if waiting:
        out.append("아직 치러지지 않은 주문(장이 열리는 09:00 가격에 치러지고, 다음 일지의 표에 들어간다): " + ", ".join(
            f"{asset_label(o['asset'])} {won_about(order_amount(o))}어치 {'사기' if o['side'] == 'buy' else '팔기'}"
            for o in waiting) + ".")
    pk = me["pockets"]
    where = f"현금은 은행 {won(pk['bank'], False)}, 증권 계좌 {won(pk['sec'], False)}, 업비트 {won(pk['upbit'], False)}에 나뉘어 있다"
    names = {"bank": "은행", "sec": "증권 계좌", "upbit": "업비트"}
    moved = [e for e in events if e["type"] == "transfer" and e["run"] == run]
    if moved:
        where += "(오늘 옮긴 돈: " + ", ".join(
            f"{names[e['data']['src']]} → {names[e['data']['dst']]} {won_about(e['data']['amount'])}"
            + (f", 수수료 {won(e['data']['fee'], False)}" if e["data"].get("fee") else "") for e in moved) + ")"
    if any(f["side"] == "sell" and f["settle"] > run for f in b.recs["fill"]
           if f.get("account") == "live:hand" and f.get("settle") and f.get("seq", 0) <= me["seq"]):
        where += ". 증권 계좌에서 판 돈은 이틀 뒤(영업일 기준)부터 다른 곳으로 옮길 수 있다"
    out.append(where + ".")
    return out


def carry_sentence(b: Book, me: dict, pa: dict, run: str) -> str:
    """갈아타는 날의 둘째 문장: 오늘부터 실제 규칙으로 다시 계산한 잔고에서 이어 간다 — 옛 장부 값 → 다시 계산한 값, 받아지지 않은
    옛 주문 수와 큰 것 몇 개(쉬운 말)."""
    v = me["value"]
    replayed = [o for o in b.recs["order"] if o["account"] == "live:hand" and o.get("replay_of")]
    rejects = [e for e in b.recs.get("broker", []) if e.get("account") == "live:hand" and e.get("type") == "reject"
               and (e.get("data") or {}).get("replay_of")]
    expired = [e for e in b.recs.get("broker", []) if e.get("account") == "live:hand" and e.get("type") == "expire"
               and (e.get("data") or {}).get("order") in b.replayed]
    old = {o["id"]: o for o in b.recs["order"] if "seq" not in o}
    n = len(replayed) + len(rejects)
    text = (f"오늘부터 계좌를 실제 규칙으로 다시 계산한 잔고에서 이어 간다. 그동안의 장부는 실제로는 못 하는 거래(같은 시각에 판 돈으로 "
            f"사기 등)를 해 준 것이라, 지난 주문 {n}건을 그때 값 그대로 다시 넣어 계산했더니 어제 옛 장부의 {won(pa['value'], False)}이 "
            f"{won(v, False)}(시작 이후 {pct(v / C.START_CASH - 1)})이 됐다.")
    bad = []
    for e in rejects:
        o = old.get(e["data"]["replay_of"]) or {}
        bad.append((o.get("qty_units", 0) * o.get("ref_price", 0) / C.ASSETS[o["asset"]].unit if o else 0,
                    f"{md(o.get('run', run))} {asset_label(e['data']['asset'])} {'사기' if e['data'].get('side') == 'buy' else '팔기'}"
                    f"({reject_reason(e['note'])})"))
    for e in expired:
        o = next((x for x in replayed if x["id"] == e["data"]["order"]), None)
        if o:
            bad.append((order_amount(o), f"{md(old.get(o['replay_of'], {}).get('run', run))} {asset_label(o['asset'])} 사기(값이 한도를 넘어 치러지지 못함)"))
    if bad:
        top = [t for _, t in sorted(bad, key=lambda x: -x[0])[:3]]
        text += (f" 그 가운데 {len(bad)}건은 그 순간 돈이나 주식이 없어 받아지지 않았다(체결된 줄 알았는데 안 된 것) — 큰 것: "
                 + ", ".join(top) + ".")
    return text


def paid_text(fills: list[dict]) -> str:
    """'지난 주문이 치러진 값' 문장. 0주 체결(갈아타는 날 옛 장부가 수량을 0으로 줄인 주문)은 값 대신 까닭으로 쓴다(검토 발견 7)."""
    def one(f: dict) -> str:
        side = "사기" if f["side"] == "buy" else "팔기"
        if not f["qty_units"]:
            why = ("돈이 모자라 사지 못함" if f["side"] == "buy" else "가진 것이 없어 팔지 못함") if f.get("note") else "치러지지 않음"
            return f"{asset_label(f['asset'])} {side}는 {why}"
        return (f"{asset_label(f['asset'])} {side} {units_text(f['asset'], f['qty_units'])} × {f['price']:,.0f}원"
                f"({md(f.get('at') or f['fill_date'])})" + (f", 세금 {won(f['tax'], False)}" if f.get("tax") else ""))
    return "지난 주문이 치러진 값: " + ", ".join(one(f) for f in fills) + "."


def priced_text(me: dict) -> str:
    """평가 가격의 때(가상 증권사 계좌 줄의 priced_at)."""
    parts = []
    for a, t in me.get("priced_at", {}).items():
        when = f"{md(t)} {t[11:16]}" if C.ASSETS[a].kind == "coin" else f"{md(t)} 장 마감" if t[11:16] == "15:30" else f"{md(t)} {t[11:16]}"
        parts.append(f"{asset_label(a)} {when}")
    return ", ".join(parts)


# ---------- 매일 일지 ----------
def daily(folder: Path, day: dt.date) -> str:
    b = Book(folder)
    run = day.isoformat()
    me, eq = b.acc["live:hand"].get(run), b.acc["baseline:equal_hold"].get(run)
    if me is None:
        raise SystemExit(f"{run} 계좌 기록이 없다 — record를 먼저 돌린다")
    prev = b.prev_run("live:hand", run)
    pa = b.acc["live:hand"].get(prev) if prev else None
    start = C.START_CASH
    lines = [f"# {day.month}월 {day.day}일 — [[채울 것: 오늘의 한 줄 결론]]", ""]

    # 첫 두 문장 (고정 꼴)
    brk = "pockets" in me   # 가상 증권사 계좌 줄(2026-11-10~)인가 — 옛 날짜 일지는 아래 옛 길로 예전처럼 나온다
    orders = [o for o in b.recs["order"] if o["account"] == "live:hand" and o["run"] == run]
    ref_orders: list[str] = []
    if brk:
        first, ref_orders = broker_first(b, me, pa, run)
    elif orders:
        parts = []
        for side, verb in (("buy", "사는"), ("sell", "파는")):
            mine = [f"{asset_label(o['asset'])} {won_about(o['qty_units'] * o['ref_price'] / C.ASSETS[o['asset']].unit)}어치"
                    for o in orders if o["side"] == side]
            if mine:
                parts.append(", ".join(mine) + f"를 {verb} 주문")
        kinds = "ETF·종목은" if any(C.is_stock(o["asset"]) for o in orders) else "ETF는"
        if all(o.get("fill_rule") == C.FILL_RULE for o in orders):  # 2026-11-05~(결정 E1): 모두 판단 뒤 첫 시가
            first = ("오늘 " + "과 ".join(parts) + "을 냈다(값은 주문 뒤 처음 정해지는 시가로 치른다:"
                     f" 비트코인은 09:00 일봉 시가, {kinds} 장이 열리는 09:00 가격).")
        else:
            first = ("오늘 " + "과 ".join(parts) + "을 냈다(값은 주문 뒤 처음 정해지는 공식 가격으로 치른다:"
                     f" 비트코인은 다음 09:00 시가, {kinds} 장 마감 가격).")
    else:
        first = "오늘은 거래하지 않았다."
    v = me["value"]
    # 갈아타는 날(2026-11-10 3회): 어제까지는 옛 장부의 계좌 줄, 오늘부터 가상 증권사 계좌 줄 — 두 값의 차이는 하루 손익이 아니라
    # '실제 규칙으로 다시 계산한 몫'이다. 표의 '오늘' 칸도 그 차이를 손익으로 보이지 않는다
    switch = brk and pa is not None and "seq" not in pa
    if switch:
        second = carry_sentence(b, me, pa, run)
    elif pa is None:
        second = "오늘 가상 1,000만 원으로 계좌를 시작했다."
    else:
        d = v - pa["value"]
        second = (f"계좌는 {won(d)}({pct(d / pa['value'])}) {'늘어' if d >= 0 else '줄어'} {won(v, False)}"
                  f"(시작 이후 {pct(v / start - 1)})이 됐다.")
    if eq and pa is not None and not switch:  # 첫날은 견줄 것이 없다
        gap = (v / start - 1) - (eq["value"] / start - 1)
        second += (f" 셋을 나눠 사 두기만 했을 때({pct(eq['value'] / start - 1)})보다 {abs(gap) * 100:.2f}%p "
                   f"{'앞섰다' if gap >= 0 else '뒤졌다'}.")
    lines += [first + " " + second, ""]

    # 참고 사실
    hands = [p for p in b.recs["prediction"] if p["run"] == run and p["predictor"] == "hand"]
    ref_why = []
    for p in hands:
        if p["horizon"] == C.ASSETS[p["asset"]].horizons[1]:
            rec = ", ".join(f"'{d}' {piece_record(b, d.partition('=')[0])}" for d in p["decisive"]) or "결론 조각 없음"
            ref_why.append(f"{asset_label(p['asset'])} 1주 오를 확률 {p['p_up']}, 확신 {p.get('confidence', '?')}, {rec}")
    reviews = [r for r in b.recs["review"] if r["run"] == run]
    good = [f"{r['prediction']}: {r['reason']}" for r in reviews if r["why"] == "맞음"]
    bad = [f"{r['prediction']}: {r['why']}, 틀린 조각 {', '.join(r['wrong']) or '–'} — {r['reason']}" for r in reviews
           if r["why"] != "맞음"]
    research = [r for r in b.recs["research"] if r["run"] == run]
    found = sorted({lk["place"] for r in research for lk in r["looked"] if lk["result"] == "찾음"})
    none = sorted({lk["place"] for r in research for lk in r["looked"] if lk["result"] in ("없음", "막힘")})
    late = sorted({lk["place"] for r in research for lk in r["looked"] if lk["result"] == "늦음"})
    new = sorted({lk["place"] for r in research for lk in r["looked"] if lk.get("new")})
    lines += [f"**왜** [[채울 것: 근거 조각 1–3개(짧은 이름과 성적), 조사에서 나온 새 사실과 출처, 확신도]]",
              _ref(ref_why + ref_orders),
              f"**잘된 것** [[채울 것: 없으면 '없음']]", _ref(good),
              f"**잘못된 것** [[채울 것: 틀린 예측 → 틀린 조각 → 까닭 → 바꾼 것. 없으면 '없음']]", _ref(bad),
              f"**조사 메모** [[채울 것: 한두 줄. 오늘 판단에 쓸모 있던 곳과 끝내 못 찾은 것(막힌 곳 목록은 기록에만)]]",
              _ref([f"찾음: {', '.join(found) or '없음'}", f"없음·막힘: {', '.join(none) or '없음'}",
                    f"늦음(판단 뒤에 나옴): {', '.join(late) or '없음'}", f"새로 시도: {', '.join(new) or '없음'}"]),
              f"**내일 볼 것** [[채울 것: 숫자 조건 + 확신도]]", ""]

    # 계좌 표
    lines += ["| 대상 | 보유 | 평가액 | 오늘 | 누적 |", "|---|--:|--:|--:|--:|"]
    redo = "다시 계산"   # 갈아타는 날의 '오늘' 칸
    for a in C.ASSET_IDS:
        val = asset_value(me, a)
        today = 0.0
        if switch:
            today = None
        elif brk:   # 오늘 = 오늘 평가액 − 어제 평가액 − 그 사이 체결 순투자(그날 바로 치른 것까지 맞게)
            today = val - (asset_value(pa, a) if pa else 0.0) - b.invested("live:hand", a, run, prev or "", me, pa)
        elif pa and pa["prices"].get(a) and me["prices"].get(a):
            today = pa["positions"].get(a, 0) * (me["prices"][a][1] - pa["prices"][a][1]) / C.ASSETS[a].unit
        cum = val - b.invested("live:hand", a, run, upto=me)
        lines.append(f"| {C.ASSETS[a].name} | {units_text(a, me['positions'].get(a, 0))} | {won(val, False)} | "
                     f"{redo if today is None else won(today)} | {won(cum)} |")
    # 종목 (2026-10-29): 오늘 들고 있거나, 어제 들었거나, 오늘 체결된 것. 오늘 = 오늘 평가액 − 어제 평가액 − 오늘 체결 순투자
    shown = sorted({a for a in [*me["positions"], *(pa["positions"] if pa else {})] if C.is_stock(a)}
                   | {f["asset"] for f in b.recs["fill"] if f["account"] == "live:hand" and f["run"] == run and C.is_stock(f["asset"])
                      and f.get("seq", 0) <= me.get("seq", 0) and not b.replay(f, "fill")})
    for a in shown:
        val = asset_value(me, a)
        v0 = asset_value(pa, a) if pa else 0.0
        today = val - v0 - b.invested("live:hand", a, run, prev or "", me, pa)
        cum = val - b.invested("live:hand", a, run, upto=me)
        lines.append(f"| {asset_label(a)} | {units_text(a, me['positions'].get(a, 0))} | {won(val, False)} | "
                     f"{redo if switch else won(today)} | {won(cum)} |")
    lines.append(f"| 현금 | | {won(me['cash'], False)} | | |")
    d_me = v - pa["value"] if pa else 0.0
    lines.append(f"| **계좌 합계** | | **{won(v, False)}** | "
                 + (f"{redo}(옛 장부 {won(pa['value'], False)}에서)" if switch else
                    f"{won(d_me)} ({pct(d_me / pa['value']) if pa else '+0.00%'})")
                 + f" | {won(v - start)} ({pct(v / start - 1)}) |")
    if eq:
        pe = b.acc["baseline:equal_hold"].get(prev) if prev else None
        de = eq["value"] - pe["value"] if pe else 0.0
        lines.append(f"| 기준선: 셋을 나눠 사 두기만 | | {won(eq['value'], False)} | {redo if switch else won(de)} | "
                     f"{won(eq['value'] - start)} ({pct(eq['value'] / start - 1)}) |")
    filled = {f["order"] for f in b.recs["fill"]}
    waiting = [o for o in b.recs["order"] if o["account"] == "live:hand" and o["run"] <= run and o["id"] not in filled]
    calc_acc = b.acc["live:calc"].get(run)
    if calc_acc:
        lines.append(f"| 참고: 계산기 계좌 | | {won(calc_acc['value'], False)} | | {pct(calc_acc['value'] / start - 1)} |")
    if brk:
        waiting = [o for o in b.recs["order"] if o["id"] in set(me["pending"])]
        lines += [""] + broker_notes(b, me, pa, run)
    elif waiting:
        # 체결은 그 가격의 일봉이 끝난 뒤 실행에서 정산된다(시가 체결은 코인·ETF 모두 D+2, 옛 ETF 종가 체결은 대개 D+3) — 그때까지 표의 보유는 그대로다.
        lines += ["", "체결을 기다리는 주문(값이 확정되는 날 실행에서 표에 들어간다): " + ", ".join(
            f"{asset_label(o['asset'])} {won_about(o['qty_units'] * o['ref_price'] / C.ASSETS[o['asset']].unit)}어치 "
            f"{'사기' if o['side'] == 'buy' else '팔기'}({int(o['run'][5:7])}월 {int(o['run'][8:])}일 주문)" for o in waiting) + "."]
    has_stock = bool(shown) or any(C.is_stock(o["asset"]) for o in waiting)
    fee = "ETF·종목 0.015%" if has_stock else "ETF 0.015%"
    tax = "세금(종목을 팔 때의 거래세 포함)" if has_stock else "세금"
    if brk:   # 2026-11-10~: 평가는 받을 수 있는 가장 새 실제 가격, 세금을 뗀다
        taxes = ("종목을 팔 때 0.20%, " if has_stock else "") + "미국 묶음을 팔아 남긴 이익의 15.4%"
        lines += ["", f"평가 가격은 판단할 때 받을 수 있던 가장 새 값이다({priced_text(me)})."
                  f" 수수료 가정({fee}, 업비트 0.05%)과 팔 때 떼는 세금({taxes})을 넣었다.", ""]
    else:
        lines += ["", f"평가 가격은 07:37에 알려진 마지막 끝난 종가다({', '.join(f'{asset_label(a)} {d}' for a, (d, _) in me['prices'].items())})."
                  f" 수수료 가정({fee}, 업비트 0.05%)을 넣었고 {tax}은 계산하지 않았다.", ""]
    # 링크 전체 목록은 블로그 글에서 본문보다 길어진다(2026-10-21). 근거로 쓴 링크는 '왜'에 달고, 나머지는 기록을 가리킨다.
    srcs = {lk["url"] for r in research for lk in r["looked"] if lk["result"] in ("찾음", "늦음") and lk.get("url")}
    lines.append("출처: 시세 — 네이버 차트·업비트 공개 API. 조사 — " + (
        f"오늘 본 곳 {len(srcs)}곳의 링크는 [조사 기록]({REPO_URL}/records/research.jsonl)에 있다." if srcs else "없음"))
    lines += [DISCLAIM, ""]
    return "\n".join(lines)


def piece_record(b: Book, piece: str) -> str:
    """조각 성적(일지용): 이 조각을 결론 조각으로 쓴 손 예측(한 번만 세는 채점)의 방향 적중."""
    calls = [s["direction_hit"] for p, s in b.counted() if p["predictor"] == "hand"
             and any(d.partition("=")[0] == piece for d in p["decisive"]) and s["direction_hit"] is not None]
    return f"(지금까지 {len(calls)}번 중 {sum(calls)}번)" if calls else "(아직 채점 없음)"


def _ref(items: list[str]) -> str:
    return "<!-- 참고: " + (" / ".join(i.replace("--", "—") for i in items) if items else "없음") + " -->"


# ---------- 주간 결산 ----------
def weekly(folder: Path, day: dt.date, places_path: Path | None = None) -> str:
    b = Book(folder)
    run = day.isoformat()
    mon = (day - dt.timedelta(days=day.weekday())).isoformat()
    start = C.START_CASH
    lines = [f"# {day.month}월 {day.day}일 주간 결산 ({mon} ~ {run}) — [[채울 것: 이번 주 한 줄 요약]]", "",
             "## 1. 요약", "[[채울 것: 이번 주와 누적 손익(원·%), 기준선 넷과의 비교를 두세 문장으로]]", "",
             "| 계좌 | 주초 | 지금 | 이번 주 | 누적 |", "|---|--:|--:|--:|--:|"]
    names = {"live:hand": "이 계좌(손 예측)", "live:calc": "계산기 계좌(R_weights)",
             "baseline:equal_hold": "기준선: 셋을 나눠 사 두기", "baseline:cash": "기준선: 전부 현금"}
    for aid, label in names.items():
        runs = sorted(b.acc[aid])
        if not runs or run not in b.acc[aid]:
            continue
        before = [r for r in runs if r < mon]
        s0 = b.acc[aid][before[-1]]["value"] if before else start
        v = b.acc[aid][run]["value"]
        lines.append(f"| {label} | {won(s0, False)} | {won(v, False)} | {won(v - s0)} ({pct(v / s0 - 1)}) | "
                     f"{won(v - start)} ({pct(v / start - 1)}) |")
    sw = b.switch_run.get("live:hand")
    if sw and mon <= sw <= run:   # 갈아탄 주(2026-11-10 3회): '이번 주'에 다시 계산한 몫이 들어 있다
        lines += ["", f"{md(sw)}부터 계좌를 실제 규칙으로 다시 계산한 잔고에서 이어 갔다 — 위 '이번 주' 칸에는 옛 장부 값에서 다시 계산한 "
                      f"값으로 바뀐 몫이 같이 들어 있다(그날 일지 참고)."]
    lines += ["", "## 2. 대상별 (이 계좌)", "| 대상 | 이번 주 손익 | 지금 평가액 |", "|---|--:|--:|"]
    me = b.acc["live:hand"].get(run)
    runs = sorted(r for r in b.acc["live:hand"] if r < mon)
    s_acc = b.acc["live:hand"][runs[-1]] if runs else None
    best = []
    week_stocks = sorted({a for acc in (me, s_acc) if acc for a in acc["positions"] if C.is_stock(a)}
                         | {f["asset"] for f in b.recs["fill"] if f["account"] == "live:hand" and mon <= f["run"] <= run
                            and C.is_stock(f["asset"])})
    for a in [*C.ASSET_IDS, *week_stocks]:
        if not me:
            break
        v1 = asset_value(me, a)
        v0 = asset_value(s_acc, a) if s_acc else 0.0
        pnl = v1 - v0 - b.invested("live:hand", a, run, runs[-1] if runs else "", me, s_acc)
        best.append((pnl, a))
        lines.append(f"| {asset_label(a)} | {won(pnl)} | {won(v1, False)} |")
    if best:
        hi, lo = max(best), min(best)
        lines.append(f"\n가장 많이 번 것: {asset_label(hi[1])} ({won(hi[0])}), 가장 많이 잃은 것: {asset_label(lo[1])} ({won(lo[0])})")
    lines += ["", "## 3. 이번 주 거래와 근거"]
    week_orders = [o for o in b.recs["order"] if o["account"] == "live:hand" and mon <= o["run"] <= run]
    for o in week_orders:
        lines.append(f"- {o['run']} {asset_label(o['asset'])} {'샀다' if o['side'] == 'buy' else '팔았다'} {won_about(order_amount(o))}"
                     f" — {o['reason']}")
    if not week_orders:
        lines.append("- 거래 없음")
    lines.append("[[채울 것: 거래마다 무엇을 → 왜 → 켜진 조각]]")
    lines += ["", "## 4. 반성 → 바꾼 것"]
    bad = [r for r in b.recs["review"] if mon <= r["run"] <= run and r["why"] != "맞음"]
    for i, r in enumerate(bad, 1):
        lines.append(f"반성 {i}. {r['prediction']} — {r['why']}, 틀린 조각 {', '.join(r['wrong']) or '–'}: {r['reason']}")
        lines.append("→ [[채울 것: 조각 ○○ v1 → v2: 더한 조건 …, 또는 '그대로 둠'과 이유]]")
    if not bad:
        lines.append("없음")
    changes = knowledge_log(folder.parent, mon, run)
    if changes:
        lines += ["", "이번 주 논리집 변경(커밋):"] + [f"- {c}" for c in changes]
    lines += ["", "## 5. 성적표 (채점은 같은 대상·기간·기준 날짜를 한 번만 센다)",
              "| 예측기 | 이번 주 채점 | 확률 점수 | 방향 적중 | 크기 적중 | 누적 채점 | 누적 확률 점수 | 누적 방향 적중 |",
              "|---|--:|--:|--:|--:|--:|--:|--:|"]
    wk = S.summarize([s for _, s in b.counted(mon, run)], lambda s: s["predictor"])
    al = S.summarize([s for _, s in b.counted()], lambda s: s["predictor"])
    for k in sorted(al, key=_pred_order):
        w = wk.get(k, {"n": 0, "brier": None, "hits": 0, "calls": 0, "size_hits": 0, "size_n": 0})
        a = al[k]
        lines.append(f"| {k} | {w['n']} | {w['brier'] if w['brier'] is not None else '–'} | {w['hits']}/{w['calls']} | "
                     f"{w['size_hits']}/{w['size_n']} | {a['n']} | {a['brier'] if a['brier'] is not None else '–'} | "
                     f"{a['hits']}/{a['calls']} |")
    lines.append("\n확률 점수는 낮을수록 좋다(늘 50%라고 말하면 0.25). [[채울 것: 손 예측 vs 계산기 vs 이전 버전, 기준선 대비 한두 문장]]")
    lines += ["", "### 볼 곳 성적 (지금까지)", "\n".join(RS.table(RS.place_cards(folder, RS.load_places(places_path))).split("\n")[:12]),
              "[[채울 것: 이번 주 볼 곳 성적의 변화 한두 문장]]",
              "", "## 6. 다음 주 볼 것", "[[채울 것: 예정된 일정과 숫자 조건]]", "", DISCLAIM, ""]
    return "\n".join(lines)


def _pred_order(k: str) -> tuple:
    order = ["hand", "calc"]
    return (order.index(k) if k in order else 2 if k.startswith("version:") else 3, k)


def knowledge_log(repo: Path, since: str, until: str) -> list[str]:
    try:
        r = subprocess.run(["git", "log", f"--since={since} 00:00", f"--until={until} 23:59", "--format=%h %ad %s",
                            "--date=short", "--", "knowledge.toml"], cwd=repo, capture_output=True, timeout=30)
        return r.stdout.decode("utf-8", "replace").splitlines() if r.returncode == 0 else []
    except Exception:
        return []


# ---------- 파일 ----------
def write(root, day: dt.date, force: bool = False) -> int:
    root.reports.mkdir(parents=True, exist_ok=True)
    made = []
    targets = [(root.reports / f"{day.isoformat()}.md", lambda: daily(root.records, day))]
    if day.weekday() == 6:
        targets.append((root.reports / f"{day.isoformat()}-week.md", lambda: weekly(root.records, day, root.places)))
    for path, make in targets:
        if path.exists() and not force:
            print(f"{path}가 이미 있다 — 메인이 채운 글을 덮지 않는다(다시 만들려면 --force)")
            continue
        path.write_text(make(), encoding="utf-8")
        made.append(path)
    for p in made:
        print(f"만듦: {p}")
    return 0


def check(root, day: dt.date) -> int:
    bad = []
    for p in sorted(root.reports.glob(f"{day.isoformat()}*.md")):
        text = p.read_text(encoding="utf-8")
        n = len(re.findall(re.escape(MARK), text))
        if n:
            bad.append(f"{p.name}: 채우지 않은 자리 {n}곳")
        tildes = TILDE.findall(re.sub(r"<!--.*?-->", "", text, flags=re.S))
        if tildes:
            bad.append(f"{p.name}: 붙은 물결표(~) {len(tildes)}곳 — 취소선으로 보인다. 범위는 '47–49%'처럼 줄표(–)로 쓴다")
    if not list(root.reports.glob(f"{day.isoformat()}*.md")):
        bad.append(f"{day} 일지가 없다")
    for b in bad:
        print(b)
    print("일지 점검 통과" if not bad else "일지 점검 실패")
    return 1 if bad else 0
