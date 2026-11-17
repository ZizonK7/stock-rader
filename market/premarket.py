"""장전 값 원문 받기 (2026-11-17, 잼민이 지시서 p564 '장전 숫자').

  python -m market.premarket [코드…]     지금 네이버 실시간 응답의 장 상태·장외 값 묶음과 미국 지수 선물을 받아
                                          out/daily/<오늘>/premarket.json에 원문 그대로 두고 짧은 표를 찍는다

까닭: 08:40 다시 보기(지시문 4-0)가 '장 전 거래 값이 전날 종가에서 얼마나 떨어졌나'를 재료로 쓰려 하는데, 네이버 응답의
`overMarketPriceInfo`(장외 값 묶음)가 08:00–08:50 장 전 거래 값으로 채워지는지는 **그 시각에만 확인된다 — 아직 못 봤다**
(p563, 2026-11-17 장중에는 REGULAR_MARKET 값으로 차 있었다). 그래서 칸의 뜻을 코드에 못박지 않고 원문을 그대로 받아 두고,
record가 그날 받은 파일이 있으면 `premarket` 기록 줄로 남긴다(첫 며칠 — 칸이 무엇으로 차는지 기록으로 확인하려고).
표의 '장외 값 − 전일 종가'는 원문 칸을 그대로 읽은 것이다(채워졌는지는 tradingSessionType·localTradedAt을 같이 본다).
동시호가 예상 체결가 칸은 이 응답에 없다(p563). 한국 야간 선물은 받을 곳을 못 찾았다(다음 403, 네이버 지수 빈 응답).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

from . import config as C

NAVER = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
YAHOO_ES = "https://query1.finance.yahoo.com/v8/finance/chart/ES=F?interval=5m&range=1d"
KEEP = ("itemCode", "stockName", "closePrice", "compareToPreviousClosePrice", "fluctuationsRatio", "marketStatus",
        "marketStatusDetailType", "marketSessionType", "localTradedAt", "tradeStopType", "overMarketPriceInfo",
        "integratedPriceInfo")


def _get(url: str) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=C.NET_TIMEOUT) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:   # 시간 초과·빈 응답 — 값을 지어내지 않고 없음으로
        return 0, str(e)[:120]


def pick(data) -> dict | None:
    """네이버 실시간 응답(바깥 datas) → 남길 칸만 원문 그대로. 모양이 다르면 None."""
    try:
        it = data["datas"][0]
    except (KeyError, IndexError, TypeError):
        return None
    return {k: it[k] for k in KEEP if k in it}


def es_pick(data) -> dict | None:
    try:
        m = data["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError):
        return None
    return {k: m.get(k) for k in ("symbol", "regularMarketPrice", "chartPreviousClose", "previousClose", "regularMarketTime")}


def fetch(codes: list[str], at: dt.datetime | None = None) -> dict:
    at = at or C.now_kst()
    items = []
    for c in codes:
        st, data = _get(NAVER.format(code=c))
        items.append({"asset": c, "status": st, "fetched_at": C.now_kst().isoformat(timespec="seconds"),
                      "raw": pick(data) if isinstance(data, dict) else None})
    st, data = _get(YAHOO_ES)
    return {"fetched_at": at.isoformat(timespec="seconds"), "items": items,
            "es": {"status": st, "raw": es_pick(data) if isinstance(data, dict) else None}}


def _num(x) -> float | None:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def summary(snap: dict) -> list[str]:
    """짧은 표: 코드 이름 | 장 상태 | 장외 묶음(세션·상태·값·전일 대비 %·시각). 칸의 뜻은 해석하지 않고 보이는 그대로."""
    out = []
    for it in snap.get("items", []):
        r = it.get("raw")
        if not r:
            out.append(f"- {it['asset']}: 받지 못함(상태 {it.get('status')})")
            continue
        o = r.get("overMarketPriceInfo") or {}
        out.append(f"- {it['asset']} {r.get('stockName', '')}: 장 {r.get('marketStatus')}/{r.get('marketSessionType')} 정규장 값 "
                   f"{r.get('closePrice')} | 장외 {o.get('tradingSessionType', '없음')}/{o.get('overMarketStatus', '')} "
                   f"{o.get('overPrice', '')} ({o.get('fluctuationsRatio', '')}%) {str(o.get('localTradedAt', ''))[:19]}")
    es = (snap.get("es") or {}).get("raw")
    if es:
        p, c = _num(es.get("regularMarketPrice")), _num(es.get("chartPreviousClose") or es.get("previousClose"))
        chg = f" ({(p / c - 1) * 100:+.2f}% 전날 종가 대비)" if p and c else ""
        out.append(f"- 미국 지수 선물 {es.get('symbol')}: {p}{chg}")
    else:
        out.append(f"- 미국 지수 선물: 받지 못함(상태 {(snap.get('es') or {}).get('status')})")
    return out


def filled(snap: dict, day: dt.date) -> tuple[int, int]:
    """(장외 값 시각이 그날 09:00 전인 것, 아닌 것) — 장 전 값으로 채워졌는지 보는 첫 잣대(확인 전이라 세기만 한다)."""
    yes = no = 0
    lo, hi = dt.datetime.combine(day, dt.time(0, 0), C.KST), dt.datetime.combine(day, C.OPEN_TIME, C.KST)
    for it in snap.get("items", []):
        o = ((it.get("raw") or {}).get("overMarketPriceInfo") or {})
        try:
            t = dt.datetime.fromisoformat(str(o.get("localTradedAt")))
            ok = t.tzinfo is not None and lo <= t < hi and str(o.get("overPrice", "")).strip() != ""
        except ValueError:
            ok = False
        yes, no = (yes + 1, no) if ok else (yes, no + 1)
    return yes, no


def record_lines(path: Path, day: dt.date) -> list[dict]:
    """그날 받은 파일이 있으면 대상마다 한 줄 + 미국 지수 선물 한 줄(원문 그대로)."""
    if not path.exists():
        return []
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
        if dt.datetime.fromisoformat(snap["fetched_at"]).astimezone(C.KST).date() != day:
            return []
    except (OSError, ValueError, KeyError):
        return []
    run = day.isoformat()
    out = [{"id": f"premarket:{run}:{it['asset']}", "run": run, "asset": it["asset"], "fetched_at": it["fetched_at"],
            "status": int(it.get("status") or 0), "raw": it.get("raw") or {}} for it in snap.get("items", [])]
    es = snap.get("es") or {}
    out.append({"id": f"premarket:{run}:ES=F", "run": run, "asset": "ES=F", "fetched_at": snap["fetched_at"],
                "status": int(es.get("status") or 0), "raw": es.get("raw") or {}})
    return out


def main(argv: list[str]) -> int:
    from . import stocks as ST
    at = C.now_kst()
    base = C.ROOT   # --root <폴더>: 그 폴더의 종목 목록·out에(흉내 폴더, 2026-11-17 예행연습 — 그 전엔 늘 진짜 저장소 out에 썼다)
    if "--root" in argv:
        i = argv.index("--root")
        base, argv = Path(argv[i + 1]).resolve(), argv[:i] + argv[i + 2:]
    codes = argv or ["069500", "360750"] + [str(s["code"]) for s in ST.register(base / "stocks.toml")]
    snap = fetch(codes, at)
    out = base / "out" / "daily" / at.date().isoformat() / "premarket.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    y, n = filled(snap, at.date())
    print(f"# 장전 값 {at:%Y-%m-%d %H:%M} — 대상 {len(codes)}개, 장외 값 시각이 오늘 09:00 전인 것 {y}개·아닌 것 {n}개 → {out}\n")
    print("\n".join(summary(snap)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
