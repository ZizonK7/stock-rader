"""매일 실행 (클라우드 예약 실행, 한국 시간 07:37): prepare → (메인: 조사·대 보기·손 예측) → record → report.

사용법 (저장소 루트):
  python -m market.daily prepare [--no-fetch]      시세 받기 → 정산·채점 미리 보기 → 돌아볼 목록 → 오늘 조사 질문.
                                                   out/daily/<날짜>/에 prepare.json, briefing.md, input.json(메인이 채울 틀)
  python -m market.daily record <input.json>       입력을 검사하고 그날 기록을 한 번에 덧붙인다(오류면 아무것도 안 씀)
  python -m market.daily report [--force]          reports/<날짜>.md 뼈대(숫자는 기록에서). 일요일엔 주간 결산도
  python -m market.daily report --check            일지에 채우지 않은 자리([[채울 것: …]])가 남았나
흉내 내기·시험용 (기본 저장소에서는 못 씀): --root <폴더> --day YYYY-MM-DD --at HH:MM

규칙
- **첫 실행 날이 진짜 계좌 시작일**이다: 계좌 넷(live:hand 손 예측, live:calc 계산기 R_weights, baseline:equal_hold 첫날 셋을
  1/3씩 사 두기, baseline:cash 현금)이 1,000만 원 현금으로 시작한다.
- 빠진 날은 빈 날(run mode "missed")로 남기고, 지난 날짜로 예측하지 않는다(기본 저장소에서 --day 금지).
- 판단 시각 = record를 돌린 실제 시각. 주문은 그 시각 **뒤에** 정해지는 첫 공식 가격에 체결된다(늦은 실행에서 이미 나온
  가격으로 사지 않게). 판단 시각까지 끝났어야 할 일봉이 시세 파일에 없으면 그 대상은 그날 예측·주문하지 않는다.
- 07:37 업비트 현재가를 '판단 때 가격'으로 따로 적는다(결정 3, 손 예측 근거). 채점 기준(끝난 일봉 종가)은 그대로.
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

from . import calc as K
from . import config as C
from . import hand as H
from . import ledger as L
from . import pieces as P
from . import prices as PR
from . import records as R
from . import research as RS
from . import scoring as S
from . import stocks as ST
from . import versions as V

WEIGHT_HORIZON_INDEX = 1  # R_weights는 1주 기간의 오를 확률로
KIND_ORDER = ("run", "fill", "score", "account", "reading", "research", "hand_reading", "prediction", "order",
              "review", "reading_check", "correction")


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


# ---------- 시각과 시세 ----------
def expected_next(asset: str, last: dt.date) -> dt.date:
    """마지막 일봉 다음에 나올 일봉 날짜 (ETF는 주말만 건너뛴다 — 휴장일은 모른다)."""
    d = last + dt.timedelta(days=1)
    while C.on_exchange(asset) and d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


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

    # 정산 (기록에서 계좌를 되살려 판단 시각에 알려진 일봉으로 체결)
    accounts = {aid: L.rebuild(aid, recs["account"], recs["order"]) for aid in C.LIVE_ACCOUNTS}
    fills = [f for acc in accounts.values() for f in acc.settle(hists, day)]
    held = sorted(accounts["live:hand"].held())  # 정산 뒤: 들고 있거나 체결을 기다리는 종목

    # 채점
    preds_all = recs["prediction"]
    done = {s["prediction"] for s in recs["score"]}
    scores = S.score_due(preds_all, hists, day, done)
    by_id = {p["id"]: p for p in preds_all}
    counted = {S.key_of(by_id[s["prediction"]]) for s in recs["score"] if s.get("counted", True) and s["prediction"] in by_id}
    S.mark_counted(scores, by_id, counted)

    last = L.last_prices(hists)
    account_lines = [acc.record(day, last) for acc in accounts.values()]
    D = Day(day, at, hists, raw, digest, recs, already, missing, predictable, skipped, accounts, fills, scores,
            account_lines)
    D.pool, D.stock_skipped = pool, stock_skipped
    D.held = [a for a in held if a not in stock_skipped]
    D.stock_ok = [a for a in sorted(seen_stocks) if a not in stock_skipped and (a in buyable or a in held)]
    D.prices = {a: v for a, (_, v) in last.items()}
    if ticker.get("KRW-BTC"):
        D.prices["KRW-BTC"] = ticker["KRW-BTC"]["price"]  # 주문 수량은 판단 때 가격으로 잡는다(체결은 다음 공식 가격)

    # 시세 대 보기·계산기·이전 버전·기준선 예측
    run = day.isoformat()
    versions = V.active_pieces(root.versions)
    calc_w: dict[str, float | None] = {}
    reasons: dict[str, str] = {}
    for a in predictable:
        h = hists[a]
        values, numbers = P.read(h.closes)
        D.calc_values[a] = values
        D.base[a] = {"base_date": h.last.date.isoformat(), "base_close": h.last.close}
        D.readings.append({"id": f"reading:{run}:{a}", "run": run, "asset": a, "known_through": h.last.date.isoformat(),
                           "values": values, "numbers": numbers})
        for i, hz in enumerate(C.ASSETS[a].horizons):
            base = {"run": run, "asset": a, "horizon": hz, **D.base[a]}
            f = K.record_fields(K.predict(a, hz, values, pieces))
            D.preds.append({"id": f"{run}:calc:{a}:{hz}", "predictor": "calc", **base, "readings": values,
                            "knowledge": digest, **f})
            if i == WEIGHT_HORIZON_INDEX:
                calc_w[a] = L.target_weight(f["p_up"])
                reasons[a] = f"R_weights: 1주 오를 확률 {f['p_up']} → 목표 비중 {calc_w[a]}"
            for vid, vpieces, vdig in versions:
                vf = K.record_fields(K.predict(a, hz, values, vpieces))
                D.preds.append({"id": f"{run}:version:{vid}:{a}:{hz}", "predictor": f"version:{vid}", **base,
                                "readings": values, "knowledge": vdig, **vf})
            D.preds += baselines(run, a, hz, h, base)
    # 종목: 시세 상태와 기준 종가는 오늘 살 수 있는 종목 모두(손 예측 검사용), 기록 줄은 손 계좌가 든 종목과 focus만.
    # 계산기는 세 대상에만 맞춘 숫자라 종목에는 '모름'이다(기록은 남겨 손 예측과 나란히 보이게; 이전 버전은 같은 '모름'이라 뺀다).
    for a in D.stock_ok:
        h = hists[a]
        values, numbers = P.read(h.closes)
        D.calc_values[a] = values
        D.base[a] = {"base_date": h.last.date.isoformat(), "base_close": h.last.close}
        D.prices[a] = h.last.close
    D.focus = [a for a in D.stock_ok if a in set(D.held) | set(focus)]
    for a in D.focus:
        h = hists[a]
        values, numbers = P.read(h.closes)
        D.readings.append({"id": f"reading:{run}:{a}", "run": run, "asset": a, "known_through": h.last.date.isoformat(),
                           "values": values, "numbers": numbers})
        for hz in C.ASSETS[a].horizons:
            base = {"run": run, "asset": a, "horizon": hz, **D.base[a]}
            f = K.record_fields(K.predict(a, hz, values, pieces))
            D.preds.append({"id": f"{run}:calc:{a}:{hz}", "predictor": "calc", **base, "readings": values,
                            "knowledge": digest, **f})
            D.preds += baselines(run, a, hz, h, base)
    if not already:
        D.calc_orders = [o.record() for o in accounts["live:calc"].plan(day, calc_w, D.prices, 0.02, reasons, at)]
        bought = {o["asset"] for o in D.recs["order"] if o["account"] == "baseline:equal_hold"}
        first = {a: 1 / 3 for a in predictable if a not in bought}
        if first:
            D.calc_orders += [o.record() for o in accounts["baseline:equal_hold"].plan(
                day, first, D.prices, 0.0, {a: "기준선: 처음 살 수 있는 날 1/3씩 사서 그대로 둔다" for a in first}, at)]

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
    D.places = RS.load_places(root.places)
    D.stale = RS.stale_places(root.records, day, D.places)
    D.questions = RS.questions(day, raw, D.places, RS.place_cards(root.records, D.places))
    return D


def baselines(run: str, a: str, hz: int, h: PR.History, base: dict) -> list[dict]:
    """기준선 예측 둘: 늘 50%, 최근 250봉에서 오른 비율."""
    br, w = S.base_rate(h, hz)
    return [{"id": f"{run}:baseline:half:{a}:{hz}", "predictor": "baseline:half", **base, "p_up": 0.5,
             "p_range": None, "size_lo": None, "size_hi": None, "readings": {}, "used": [],
             "decisive": [], "reason": "늘 50%", "knowledge": ""},
            {"id": f"{run}:baseline:base_rate:{a}:{hz}", "predictor": "baseline:base_rate", **base,
             "p_up": round(br, 4) if br is not None else None, "p_range": None,
             "size_lo": round(w[0], 4) if w else None, "size_hi": round(w[1], 4) if w else None,
             "readings": {}, "used": [], "decisive": [],
             "reason": f"최근 {S.BASE_RATE_WINDOW}봉에서 {hz}봉 수익률이 오른 비율", "knowledge": ""}]


# ---------- prepare ----------
def prepare(root: Root, day: dt.date, at: dt.datetime, fetch: bool = True, ticker: dict | None = None,
            echo: bool = True) -> int:
    out = root.day_dir(day)
    out.mkdir(parents=True, exist_ok=True)
    fetched = (fetch_prices(root, at) if fetch else
               {a: {"status": "skipped"} for a in [*C.ASSET_IDS, *ST.codes(root.stocks)]})
    tick = ticker if ticker is not None else (fetch_ticker() if fetch else {})
    D = compute(root, day, at, fetched, tick)
    state = {"day": day.isoformat(), "prepared_at": at.isoformat(timespec="seconds"), "fetch": fetched,
             "decision_price": tick, "predictable": D.predictable, "skipped": D.skipped,
             "missing": [d.isoformat() for d in D.missing], "already": D.already}
    (out / "prepare.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "briefing.md").write_text(briefing(D, fetched, tick), encoding="utf-8")
    tpl = out / "input.json"
    if not tpl.exists():
        tpl.write_text(json.dumps(template(D), ensure_ascii=False, indent=1), encoding="utf-8")
    if echo:
        print(briefing(D, fetched, tick))
        print(f"\n→ 틀: {tpl}  (채운 뒤: python -m market.daily record {tpl})")
    elif D.already:
        print(f"{day} 기록이 이미 있다")
    return 1 if D.already else 0


def briefing(D: Day, fetched: dict, tick: dict) -> str:
    L_ = [f"# {D.day} 준비 (판단 시각 {D.at:%H:%M} KST)", ""]
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
                  + (f" / **건너뜀: {D.skipped[a]}**" if a in D.skipped else ""))
    if tick.get("KRW-BTC"):
        t = tick["KRW-BTC"]
        L_.append(f"- 판단 때 가격(업비트 현재가): {t['price']:,.0f}원 ({t['time']})")
    elif tick.get("error"):
        L_.append(f"- 판단 때 가격: 받기 실패 ({tick['error']})")
    L_ += ["", f"## 종목 (살 수 있는 목록 stocks.toml {len(D.pool)}개, 오늘 쓸 수 있는 것 {len(D.stock_ok)}개)"]
    L_.append("손 계좌가 세 대상 말고 살 수 있는 한국 상장 종목이다. 계산기는 종목에 '모름'이다(세 대상에만 맞춘 숫자). "
              "사려면 손 예측 셋(다음·1주·4주)과 1주 결론 조각(decisive)이 있어야 한다 — 지시문 '종목'.")
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
    L_ += ["", "## 정산·계좌 (오늘 07:37 평가)"]
    for f in D.fills:
        L_.append(f"- 체결 {f['account']} {f['asset']} {f['side']} {f['qty_units']}단위 @ {f['price']:,} ({f['fill_date']})")
    for a in D.account_lines:
        L_.append(f"- {a['account']}: {a['value']:,.0f}원 (현금 {a['cash']:,}원, 체결 안 된 주문 {len(a['pending'])})")
    L_ += ["", f"## 채점 (새로 {len(D.scores)}건, 한 번만 세는 것 {sum(s['counted'] for s in D.scores)}건)"]
    for s in D.scores:
        if s["predictor"] in ("hand", "calc") and s["counted"]:
            L_.append(f"- {s['prediction']}: 수익률 {s['ret'] * 100:+.2f}%, 방향 {s['direction_hit']}, 크기 {s['size_hit']}, 확률 점수 {s['brier']}")
    L_ += ["", f"## 돌아볼 손 예측 ({len(D.due_reviews)}건) — 입력 reviews에"]
    for pid, i in D.due_reviews.items():
        L_.append(f"- {pid}: {i['verdict']} (수익률 {i['ret_pct']:+.2f}%, 평소 {i['typical_pct']}%{', 잡음 가능' if i['noise'] else ''})"
                  f" / 그때 p_up {i['p_up']}, 크기 {i['size']}, 결론 조각 {i['decisive']}")
    L_ += ["", f"## 다시 볼 손 대 보기 ({len(D.due_checks)}건) — 입력 reading_checks에"]
    for (t, st), c in D.due_checks.items():
        L_.append(f"- {t} {st} = {c['then']} (그때 이유: {c['why_then'][:80]})")
    L_ += ["", "## 계산기 (오늘, 시세 조각만)"]
    for p in D.preds:
        if p["predictor"] == "calc":
            L_.append(f"- {p['asset']} {p['horizon']}봉: {p['reason']}")
    L_ += ["", "## 오늘 조사 질문 (각도별로 조사 에이전트에게)"]
    for q in D.questions:
        L_.append(f"- [{q['angle']}] `{q['question']}` {q['asked']}\n  추천 볼 곳: {', '.join(q['suggest']) or '없음'}")
    L_.append(f"\n새 곳 시도 몫: 각도마다 {RS.NEW_PER_ANGLE}곳 이상(목록에 없거나 {RS.FRESH_DAYS}일 넘게 안 본 곳). "
              f"오래 안 본 목록 곳: {', '.join(sorted(D.stale)) or '없음'}")
    if D.day.weekday() == 6:
        L_.append("\n**일요일: report가 주간 결산도 만든다.**")
    return "\n".join(L_)


def template(D: Day) -> dict:
    """메인이 채울 입력 틀. '_'로 시작하는 칸은 참고용이고 검사하지 않는다."""
    from .research import research_states
    states = [s["id"] for s in research_states(D.raw, per_asset=False)]
    per_asset = [s["id"] for s in research_states(D.raw, per_asset=True)]
    calc = {(p["asset"], p["horizon"]): p for p in D.preds if p["predictor"] == "calc"}
    hand_acc = D.accounts["live:hand"]
    units, cash = hand_acc.expected(D.prices)
    total = cash + sum(n * D.prices[a] / C.ASSETS[a].unit for a, n in units.items() if a in D.prices)
    pieces = {pid: info["state"] for pid, info in H._piece_index(D.raw).items()}
    assets = list(D.predictable) + list(D.held)
    names = {str(s["code"]): s["name"] for s in D.pool}
    return {
        "day": D.day.isoformat(), "agents": 0, "notes": [],
        "_pieces": {k: f"조건 상태 {v}" if v else "기준값(값 없이 조각 id만)" for k, v in pieces.items()},
        "_used_form": "used·decisive는 '조각 id=값'. 값은 그 조각 조건 상태의 오늘 값(조사 조각은 readings, 종목마다 대 보는 "
                      "조사 조각은 stock_readings의 그 종목 값, 계산 조각은 _values)",
        "_stocks": {a: {"name": names.get(a, a), "held": a in D.held, "base_date": D.base[a]["base_date"],
                        "close": D.base[a]["base_close"], "_values": D.calc_values.get(a, {})} for a in D.stock_ok},
        "_stock_rule": (f"종목을 사려면 hand에 그 종목의 기간 셋(1·5·20)을 더하고, 1주 p_up > 0.5와 decisive(결론 조각)를 적고, "
                        f"weights에 target(≤ {C.STOCK_MAX_WEIGHT}, 종목 {C.STOCK_MAX_COUNT}개까지)을 넣는다. 종목 몫은 한국 묶음에서 "
                        f"덜어 낸다. 든 종목은 매일 hand·weights에 남긴다(팔려면 target 0). 새 종목은 python -m market.stocks add"),
        "research": [{"question": q["question"], "state": q["state"], "angle": q["angle"], "asked": q["asked"],
                      "answer": "", "agent": "", "_suggest": q["suggest"],
                      "looked": [{"place": "", "result": "찾음", "new": False, "url": "", "date": "", "note": ""}]}
                     for q in D.questions],
        "readings": {st: {"value": "모름", "why": "", "places": []} for st in states},
        "stock_readings": {a: {st: {"value": "모름", "why": "", "places": []} for st in per_asset} for a in D.held}
                          if per_asset else {},
        "hand": [{"asset": a, "horizon": hz, "p_up": None, "size_lo": None, "size_hi": None, "used": [],
                  "decisive": [], "reason": "", "confidence": "낮음",
                  "_calc": calc[(a, hz)]["reason"] if (a, hz) in calc else "",
                  "_calc_p_up": calc[(a, hz)]["p_up"] if (a, hz) in calc else None,
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
    if D.already:
        print(f"{day} 기록이 이미 있다 — 같은 날 두 번 쓰지 않는다(정정은 correction 기록으로)")
        return 1
    ctx = {"day": day.isoformat(), "raw": D.raw,
           "research_states": [s["id"] for s in RS.research_states(D.raw, per_asset=False)],
           "stock_states": [s["id"] for s in RS.research_states(D.raw, per_asset=True)],
           "places": [p["id"] for p in D.places], "stale": D.stale, "predictable": D.predictable,
           "stocks": D.stock_ok, "held": D.held, "buyable": {str(s["code"]) for s in D.pool if s.get("buy", True)},
           "hand_units": D.accounts["live:hand"].expected(D.prices)[0],
           "calc_values": D.calc_values, "due_reviews": D.due_reviews, "due_checks": D.due_checks,
           "node_ids": {n.get("id") for k in ("goal", "state", "fact", "hypothesis", "rule") for n in D.raw.get(k, [])},
           "record_ids": {x["id"] for items in D.recs.values() for x in items}}
    errs, warns = H.validate(inp, ctx)
    if errs:
        print(f"입력 오류 {len(errs)}건 — 아무것도 쓰지 않았다:")
        for e in errs:
            print("  -", e)
        return 1
    hand = H.lines(inp, ctx, D.digest, D.base)
    warns += RS.new_share_warnings(hand["research"], D.places, D.stale)

    # 손 예측 계좌 주문
    targets = {a: float(w["target"]) for a, w in (inp.get("weights") or {}).items()}
    hand_orders = [o.record() for o in D.accounts["live:hand"].plan(
        day, targets, D.prices, 0.02, {a: str(w["reason"]) for a, w in (inp.get("weights") or {}).items()}, at)]

    run = day.isoformat()
    lines: dict[str, list[dict]] = {k: [] for k in KIND_ORDER}
    for d in D.missing:
        lines["run"].append({"id": f"missed:{d.isoformat()}", "run": d.isoformat(), "mode": "missed", "steps": {},
                             "notes": ["실행 없음 — 빈 날. 지난 날짜로 예측하지 않는다"]})
    lines["fill"] = D.fills
    lines["score"] = D.scores
    lines["account"] = D.account_lines
    lines["reading"] = D.readings
    lines["research"] = hand["research"]
    lines["hand_reading"] = hand["hand_reading"]
    lines["prediction"] = D.preds + hand["prediction"]
    lines["order"] = D.calc_orders + hand_orders
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
                  "fills": len(D.fills), "scores": len(D.scores),
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
    for w in warns:
        print("  경고:", w)
    return 0


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
    if not pos or pos[0] not in ("prepare", "record", "report"):
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
