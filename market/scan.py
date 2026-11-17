"""대상마다 아침 조사 (2026-11-17 사용자 결정, 잼민이 지시서 p564).

  python -m market.scan [--day YYYY-MM-DD] [--root <폴더>] [--at HH:MM]   그날 조사 파일 검사 + 메인이 읽는 짧은 표

사용자 말: "8시쯤 시작해서 HAIKU 5.5 HIGH가 놓치는 거 없게 조사 종목별로 보내고 물량으로 OPUS 5.5 HIGH한테 내용 보내주고".
그래서 대상(세 대상 + 목록 종목 전부)마다 조사 에이전트 하나가 이 대상 하나만 보고, 결과를 **파일**로 낸다 — 메인 맥락에 원문
60개가 쌓이지 않게. 메인은 이 명령의 짧은 표(대상마다 정해진 칸)를 읽고, 더 볼 대상만 파일을 연다.

- prepare가 `out/daily/<날짜>/scan/_brief/<코드>.md`(그 대상의 조사 지시)와 `_tasks.json`(대상 목록)을 만든다(`write_briefs`).
- 조사 에이전트는 `scan/<코드>.json`을 쓴다(꼴은 `FORM`). 보내는 조건은 숫자로: 실제로 연 출처 MIN_OPENED곳 이상
  (못 연 곳은 blocked에 '호스트 상태'만 — 우회·추측하지 않는다), 대상 종류마다 정해진 kind(`REQUIRED`)를 하나씩
  찾음(found) 또는 못 찾음(missing)으로 — '놓치는 것 없게'를 검사로 막는다. 두 번 실패하면 메인이 failed(까닭)로 적는다.
- record가 FLOW_SINCE 판단부터 대상마다 파일이 있고 맞는지 검사하고(없으면 거절), `scan` 기록 줄로 남긴다(`record_lines`).
판단(사라·팔라)은 조사 에이전트가 하지 않는다 — 판단은 메인.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from . import config as C
from . import records as R

KINDS = ("공시", "뉴스", "실적", "수급", "일정", "간밤", "기타")
MIN_OPENED = 2          # 실제로 연 출처 수(못 연 곳이 있으면 연 곳 + 못 연 곳) — Daily Briefing 조사와 같은 '출처 둘 이상'
MAX_FOUND = 10          # 기록 크기: 대상 60개 × 하루
TEXT_MAX = 300
# 대상 종류마다 반드시 다루는 kind(찾음 또는 못 찾음). 종목: 그 회사 공시·뉴스·실적(직전 10 거래일 발표와 전망 평균)·전일 수급.
# 묶음·코인: 간밤 해외 움직임·뉴스·수급(국내 묶음은 외국인 현물·선물, 코인은 거래소·해외 상품 자금)·오늘 일정.
REQUIRED = {"stock": ("공시", "뉴스", "실적", "수급"), "etf": ("간밤", "뉴스", "수급", "일정"), "coin": ("간밤", "뉴스", "수급", "일정"),
            "sector": ("간밤", "뉴스", "수급", "일정")}   # sector = 목록에 든 국내 상장 업종 ETF(config.LISTED_ETF, 2026-11-17)

FORM = {
    "asset": "<코드>", "as_of": "<본 정보 가운데 가장 늦은 것의 시각, 예 2026-11-18T07:55+09:00>",
    "opened": ["<실제로 연 주소>", "…"], "blocked": ["<못 연 호스트와 상태, 예 data.krx.co.kr 403>"],
    "found": [{"kind": "공시|뉴스|실적|수급|일정|간밤|기타", "text": "<한두 문장 요약 — 본문을 옮기지 않는다>",
               "url": "<그 주소>", "when": "<그 정보의 시각·날짜>", "numbers": {"<이름_단위>": 0.0}}],
    "missing": ["<찾아봤지만 못 찾은 kind — 까닭을 붙이려면 'kind: 까닭'처럼 kind 낱말로 시작>"],
}

# 볼 곳(2026-11-17 예행연습 뒤 바꿈): 하위 에이전트의 WebFetch는 finance.naver.com을 못 열었다(63개 중 62개) — 아래 주소는
# 같은 날 이 환경에서 셸 curl로 열리는 것을 확인했다(200, 내용 있음). 웹 검색은 한 실행이 함께 쓰는 한도가 있어(실측 201번에서 막힘)
# 대상 조사는 정해진 주소를 받는 것으로 채우고 검색은 SEARCH_PER_TARGET번까지만 — 08:40 다시 보기 몫을 남긴다.
CURL = "curl -s -m 20 -A 'Mozilla/5.0' '{url}'"
RSS_PICK = "| grep -o '<title>[^<]*</title>\\|<pubDate>[^<]*</pubDate>' | head -40"
NAVER = "https://m.stock.naver.com/api"
YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
GNEWS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
CALENDAR = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
SEARCH_PER_TARGET = 1


def _gnews(words: str) -> str:
    from urllib.parse import quote
    return GNEWS.format(q=quote(f"{words} when:2d"))


def sources(kind: str, code: str, name: str) -> list[str]:
    """kind마다 '종류: 받는 명령 — 무엇이 나오나' 줄. 주소는 대상 코드를 채운 완성된 꼴(에이전트가 고치지 않고 그대로 받는다)."""
    c = lambda url: "`" + CURL.format(url=url) + "`"
    cr = lambda url: "`" + CURL.format(url=url) + " " + RSS_PICK + "`"   # RSS는 제목·시각만 골라 받는다
    cal = f"일정: {c(CALENDAR)} — 이번 주 지표·중앙은행 일정과 예상치(forecast)·이전치(previous), 시각은 미국 동부라 KST로 바꿔 적는다"
    night = (f"간밤: {c(YAHOO.format(sym='%5EGSPC'))}·{c(YAHOO.format(sym='%5EIXIC'))}·{c(YAHOO.format(sym='ES=F'))}·"
             f"{c(YAHOO.format(sym='KRW=X'))} — 미국 대형주·기술주 지수, 미국 지수 선물, 원·달러(meta의 regularMarketPrice·"
             f"chartPreviousClose와 마지막 봉으로 등락 %)")
    if kind == "stock":
        return [f"공시: {c(f'{NAVER}/stock/{code}/disclosure?pageSize=10&page=1')} — 공시 제목·datetime(최근 순)",
                f"뉴스: {c(f'{NAVER}/news/stock/{code}?pageSize=10&page=1')} — 기사 제목·요약·datetime(YYYYMMDDHHMM). 더 보려면 "
                f"Google 뉴스 RSS {cr(_gnews(name))}",
                f"실적: {c(f'{NAVER}/stock/{code}/finance/quarter')} — 분기 매출·영업이익(isConsensus Y 칸 = 증권사 전망). 발표일은 공시 "
                f"목록의 '잠정실적'·'영업(잠정)실적' 제목 날짜(직전 10 거래일 안인가). 같은 분기의 전망 칸이 있으면 "
                f"op_profit_surprise_pct = (실제 − 전망) ÷ 전망 × 100, 없으면 '실적: 전망 평균 못 찾음'",
                f"수급: {c(f'{NAVER}/stock/{code}/trend?pageSize=5')} — 날짜(bizdate)마다 외국인·기관·개인 순매수(주 수)",
                f"그 밖(시세·업종): {c(f'{NAVER}/stock/{code}/integration')}"]
    if kind == "coin":
        return [f"간밤: {c('https://api.upbit.com/v1/candles/minutes/60?market=KRW-BTC&count=24')} — 지난 24시간 1시간 봉(원화)",
                f"뉴스: {cr('https://cointelegraph.com/rss')} 와 Google 뉴스 RSS {cr(_gnews('비트코인'))}",
                f"수급: {c('https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP')} — 무기한 선물 자금조달률, "
                f"{c('https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=FlowInExNtv,FlowOutExNtv&frequency=1d&page_size=3&paging_from=end')}"
                f" — 거래소 들어온·나간 코인 수. 미국 현물 상품 자금 흐름은 이 환경에서 받을 곳 없음(farside 403) — '수급: 상품 흐름 받을 곳 없음'",
                cal]
    if kind == "sector":
        return [night,
                f"뉴스: {c(f'{NAVER}/news/stock/{code}?pageSize=10&page=1')} 와 Google 뉴스 RSS {cr(_gnews(name))} — "
                f"이 ETF가 담은 업종 소식",
                f"수급: {c(f'{NAVER}/stock/{code}/trend?pageSize=5')} — 이 ETF의 외국인·기관 순매수(주 수); 구성 종목은 "
                f"{c(f'{NAVER}/stock/{code}/integration')}",
                cal]
    market = "코스피" if code == "069500" else "뉴욕증시"
    return [night,
            f"뉴스: Google 뉴스 RSS {cr(_gnews(market))} 와 {c(f'{NAVER}/news/stock/{code}?pageSize=10&page=1')}",
            f"수급: {c(f'{NAVER}/index/KOSPI/integration')} — dealTrendInfo = 전일 유가증권시장 개인·외국인·기관 순매수(억 원)",
            cal]


def kind_of(asset: str) -> str:
    if asset in C.LISTED_ETF:
        return "sector"
    return "stock" if C.is_stock(asset) else C.ASSETS[asset].kind


def brief(day: dt.date, asset: str, name: str, path: Path, held: bool, turn: bool) -> str:
    k = kind_of(asset)
    req = REQUIRED[k]
    src = "\n".join(f"- {line}" for line in sources(k, asset, name))
    tags = (" — 손 계좌가 들고 있다" if held else "") + (" — 오늘 손 예측 차례" if turn else "")
    return f"""# 아침 조사: {name} ({asset}) — {day.isoformat()}{tags}

너는 stock-rader 아침 조사 에이전트 하나다. 이 대상 하나만 본다. 사라·팔라는 판단은 하지 않는다(메인이 한다).
오늘 09:00 시가 전에 알 수 있는 것 가운데 이 대상의 오늘 값을 움직일 만한 것을 **놓치지 않게** 모은다. 시각은 한국 시간.

## 받는 법
- 아래 주소는 **Bash 도구의 curl로** 받는다(명령을 그대로 붙여 넣는다). WebFetch는 이 환경에서 네이버를 못 연다. 응답이 길면 뒤에
  `| head -c 8000`. 받은 주소는 opened에 적는다(검색 결과 목록이 아니라 실제로 받은 것).
- **웹 검색(WebSearch)은 많아야 {SEARCH_PER_TARGET}번** — 아래 주소로 그 kind를 못 채웠을 때만. 한 실행의 하위 에이전트가 모두 합쳐 약
  200번을 나눠 쓰고(2026-11-17 실측), 08:40 다시 보기 몫을 남겨야 한다. '한도에 걸렸다'는 답이 오면 더 검색하지 않는다.
- 세션 만들기·메시지 보내기·예약 같은 원격 도구는 부르지 않는다(이 일과 상관없다).

## 볼 것 (kind마다 찾음 또는 못 찾음 — {', '.join(req)} 넷은 빠짐없이)
{src}
- 그 밖에 이 대상에 큰 일(거래 정지·소송·대주주 매도·지수 편입 등)이 보이면 kind '기타'.

## 보내는 조건 (숫자 — 검사가 막는다)
- 실제로 연 주소 {MIN_OPENED}곳 이상을 opened에. 못 연 곳(403·빈 응답·시간 초과)은 blocked에 '호스트 상태'만 적고 우회하거나
  추측하지 않는다. 검색 결과 목록만 보고 쓰지 않는다 — 연 페이지에서 본 것만.
- {', '.join(req)}마다 found에 하나 이상 있거나 missing에 있어야 한다. 못 찾은 것을 지어내지 않는다.
  missing 항목은 kind 낱말로 시작한다(까닭은 뒤에: '뉴스: 네이버 뉴스 페이지 403').
- found는 {MAX_FOUND}개까지, text는 {TEXT_MAX}자 안의 요약(기사 본문을 옮기지 않는다), url은 그 정보를 본 주소, 숫자가 있으면
  numbers에 '이름_단위'(단위: pct·pp·krw·krw100m·usd·n 등, 예 op_profit_surprise_pct, foreign_net_krw100m).
- as_of는 본 정보 가운데 가장 늦은 것의 시각(시간대 붙은 ISO). 지금 시각보다 늦을 수 없다.

## 결과
아래 꼴 그대로의 JSON을 Write 도구로 `{path}`에 쓴다(다른 파일은 만들지 않는다). 답장은 한 줄: `완료 {asset} 찾음 n · 못 찾음 m · 못 연 곳 k`.
파일을 못 쓰면 답장에 JSON 전부를 넣고 맨 위에 그 사실을 적는다.

```json
{json.dumps({**FORM, "asset": asset}, ensure_ascii=False, indent=1)}
```
"""


def write_briefs(day: dt.date, folder: Path, assets: list[tuple[str, str, bool, bool]]) -> Path:
    """assets = [(코드, 이름, 듦, 오늘 차례)]. scan 폴더에 _tasks.json과 _brief/<코드>.md를 쓴다. 이미 있는 결과 파일은 건드리지 않는다."""
    d = folder / "scan"
    (d / "_brief").mkdir(parents=True, exist_ok=True)
    tasks = []
    for a, name, held, turn in assets:
        p = d / f"{a}.json"
        (d / "_brief" / f"{a}.md").write_text(brief(day, a, name, p.resolve(), held, turn), encoding="utf-8")
        tasks.append({"asset": a, "name": name, "kind": kind_of(a), "held": held, "turn": turn,
                      "brief": str((d / "_brief" / f"{a}.md").resolve()), "out": str(p.resolve())})
    (d / "_tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def _time(x) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(x))
    except ValueError:
        return None
    return t if t.tzinfo else None


MISSING_SEP = " :(（—–-·,/;"


def missing_kinds(missing: list[str]) -> set[str]:
    """missing 항목이 가리키는 kind. kind 낱말 그대로이거나, kind로 시작하고 바로 뒤에 까닭이 붙은 꼴('뉴스: 네이버 막힘',
    '수급 (전일 숫자 없음)', '뉴스(지난 24시간) — …')도 그 kind다. kind로 시작하지 않는 항목은 메모로 보고 kind로 세지 않는다.
    까닭: 2026-11-17 예행연습에서 Haiku 조사 13개가 모두 까닭을 붙여 써 'kind 목록' 검사에 걸렸다 — 까닭은 버릴 것이 아니라
    무엇을 못 찾았는지의 기록이다."""
    out = set()
    for m in missing:
        s = m.strip()
        for k in KINDS:
            if s == k or (s.startswith(k) and s[len(k)] in MISSING_SEP):
                out.add(k)
                break
    return out


def check_one(data, asset: str, day: dt.date, at: dt.datetime) -> list[str]:
    """한 대상 결과의 오류(없으면 [])."""
    w = f"scan {asset}"
    if not isinstance(data, dict):
        return [f"{w}: JSON 객체가 아니다"]
    errs = []
    if data.get("asset") != asset:
        errs.append(f"{w}: asset 칸이 '{data.get('asset')}'")
    if str(data.get("failed", "")).strip():   # 두 번 실패 — 메인이 까닭을 적는다(빈 채로 넘어가지 않게)
        if not str(data.get("by", "")).strip():
            errs.append(f"{w}: failed를 적은 이(by — 예 main)가 비었다")
        return errs
    # by(조사한 에이전트)는 받지 않아도 된다(2026-11-17 예행연습: 에이전트는 자기 effort를 몰라 63개 중 20개가 '미확인' 등으로 적었다).
    # 보낸 모델·effort는 지시문 4-0이 정하고, 적혀 있으면 기록에 그대로 남긴다.
    t = _time(data.get("as_of"))
    if t is None:
        errs.append(f"{w}: as_of는 시간대 붙은 ISO 시각")
    elif t > at:
        errs.append(f"{w}: as_of {t.isoformat()}가 판단 시각보다 늦다")
    # 오래된 as_of는 막지 않는다(2026-11-17 예행연습): 지시가 as_of를 '본 정보 가운데 가장 늦은 것의 시각'으로 정하므로
    # 새 소식이 없는 대상은 며칠 전 값이 맞다(실제 Haiku 13개 중 6개가 그랬다). 오늘 조사인지는 날짜 폴더가 가른다.
    opened, blocked = data.get("opened"), data.get("blocked", [])
    if not isinstance(opened, list) or not all(isinstance(u, str) and u.startswith("http") for u in opened):
        errs.append(f"{w}: opened는 연 주소(http…) 목록")
        opened = []
    if not isinstance(blocked, list) or not all(isinstance(b, str) and b.strip() for b in blocked):
        errs.append(f"{w}: blocked는 '호스트 상태' 글 목록")
        blocked = []
    if len(set(opened)) < MIN_OPENED and len(set(opened)) + len(blocked) < MIN_OPENED:
        errs.append(f"{w}: 실제로 연 주소가 {len(set(opened))}곳 — {MIN_OPENED}곳 이상(못 연 곳은 blocked에)")
    found = data.get("found")
    if not isinstance(found, list) or len(found) > MAX_FOUND:
        errs.append(f"{w}: found는 {MAX_FOUND}개까지의 목록")
        found = []
    kinds = set()
    for i, f in enumerate(found):
        if not isinstance(f, dict) or f.get("kind") not in KINDS:
            errs.append(f"{w}: found[{i}].kind는 {list(KINDS)} 중 하나")
            continue
        kinds.add(f["kind"])
        if not str(f.get("text", "")).strip() or len(str(f["text"])) > TEXT_MAX:
            errs.append(f"{w}: found[{i}].text는 {TEXT_MAX}자 안의 요약")
        if not str(f.get("url", "")).startswith("http"):
            errs.append(f"{w}: found[{i}].url(본 주소)이 없다")
        if "numbers" in f:
            errs += R.number_errors(f["numbers"], f"{w}: found[{i}].numbers")
    missing = data.get("missing", [])
    if not isinstance(missing, list) or not all(isinstance(m, str) and m.strip() for m in missing):
        errs.append(f"{w}: missing은 kind(또는 'kind: 까닭') 글 목록")
        missing = []
    missing = missing_kinds(missing)
    left = [k for k in REQUIRED[kind_of(asset)] if k not in kinds and k not in missing]
    if left:
        errs.append(f"{w}: {', '.join(left)}를 찾음(found)이나 못 찾음(missing)으로 적지 않았다 — 놓치지 않게")
    return errs


def load(folder: Path, assets: list[str]) -> dict[str, dict | None]:
    """scan 폴더의 결과: 대상 → 데이터(없거나 JSON이 깨졌으면 None)."""
    out = {}
    for a in assets:
        p = folder / f"{a}.json"
        try:
            out[a] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (OSError, ValueError):
            out[a] = {"_broken": True}
    return out


def input_errors(folder: Path, assets: list[str], day: dt.date, at: dt.datetime) -> list[str]:
    if day.isoformat() < C.FLOW_SINCE:
        return []
    errs, data = [], load(folder, assets)
    gone = [a for a, d in data.items() if d is None]
    if gone:
        errs.append(f"scan: 아침 조사 파일이 없는 대상 {len(gone)}개 — {', '.join(gone[:8])}{' …' if len(gone) > 8 else ''} "
                    f"({folder}; 두 번 실패한 대상은 {{asset, by, failed: 까닭}}으로 적는다)")
    for a, d in data.items():
        if d is not None:
            errs += ["scan " + a + ": JSON이 깨졌다"] if d.get("_broken") else check_one(d, a, day, at)
    return errs


def record_lines(folder: Path, assets: list[str], day: dt.date) -> list[dict]:
    out = []
    for a, d in load(folder, assets).items():
        if not d or d.get("_broken"):
            continue
        line = {"id": f"scan:{day.isoformat()}:{a}", "run": day.isoformat(), "asset": a, "by": str(d.get("by", ""))}
        if str(d.get("failed", "")).strip():
            line.update(failed=str(d["failed"]), as_of="", opened=[], blocked=[], found=[], missing=[])
        else:
            line.update(as_of=str(d["as_of"]), opened=sorted(set(d["opened"])), blocked=list(d.get("blocked", [])),
                        found=[{k: f[k] for k in ("kind", "text", "url", "when", "numbers") if k in f} for f in d["found"]],
                        missing=list(d.get("missing", [])))
        out.append(line)
    return out


def table(folder: Path, assets: list[tuple[str, str]], day: dt.date, at: dt.datetime) -> list[str]:
    """메인이 읽는 짧은 표: 대상마다 한 줄(찾은 kind 수·못 찾은 것·못 연 곳·오류) + 찾은 것 앞 둘의 요약."""
    data = load(folder, [a for a, _ in assets])
    out = []
    for a, name in assets:
        d = data[a]
        if d is None:
            out.append(f"- {a} {name}: **파일 없음**")
            continue
        if d.get("_broken"):
            out.append(f"- {a} {name}: **JSON 깨짐**")
            continue
        errs = check_one(d, a, day, at)
        if str(d.get("failed", "")).strip():
            out.append(f"- {a} {name}: 실패 — {d['failed']}")
            continue
        found = d.get("found") or []
        cnt = {}
        for f in found:
            if isinstance(f, dict):
                cnt[f.get("kind")] = cnt.get(f.get("kind"), 0) + 1
        out.append(f"- {a} {name}: " + (" ".join(f"{k}{n}" for k, n in cnt.items()) or "찾은 것 없음")
                   + (f" | 못 찾음 {','.join(d.get('missing') or [])}" if d.get("missing") else "")
                   + (f" | 못 연 곳 {len(d.get('blocked') or [])}" if d.get("blocked") else "")
                   + (f" | **오류 {len(errs)}: {errs[0]}**" if errs else ""))
        for f in [f for f in found if isinstance(f, dict) and f.get("kind") != "기타"][:2] + \
                 [f for f in found if isinstance(f, dict) and f.get("kind") == "기타"][:1]:
            nums = ", ".join(f"{k}={v}" for k, v in (f.get("numbers") or {}).items())
            out.append(f"  - [{f.get('kind')}] {str(f.get('text', ''))[:140]}{' (' + nums + ')' if nums else ''}")
    return out


def main(argv: list[str]) -> int:
    from . import daily as DY
    from . import stocks as ST
    pos, opt = DY._args(argv)
    root, day, at = DY.when(opt, dt.time(8, 30))
    ST.register(root.stocks)   # 목록에 든 국내 상장 ETF를 config.LISTED_ETF에(record와 같게 — 2026-11-17 예행연습의 거짓 오류)
    folder = root.day_dir(day) / "scan"
    tasks_p = folder / "_tasks.json"
    if not tasks_p.exists():
        print(f"{tasks_p}가 없다 — 먼저 python -m market.daily prepare")
        return 1
    tasks = json.loads(tasks_p.read_text(encoding="utf-8"))
    rows = table(folder, [(t["asset"], t["name"]) for t in tasks], day, at)
    errs = input_errors(folder, [t["asset"] for t in tasks], day, at) if day.isoformat() >= C.FLOW_SINCE else []
    print(f"# 아침 조사 {day} — 대상 {len(tasks)}개, 파일 {sum(1 for t in tasks if (folder / (t['asset'] + '.json')).exists())}개, "
          f"오류 {len(errs)}건\n")
    print("\n".join(rows))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
