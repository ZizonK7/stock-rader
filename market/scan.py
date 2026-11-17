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
    "asset": "<코드>", "by": "<에이전트: 예 haiku-5.5 high>", "as_of": "<본 정보 가운데 가장 늦은 것의 시각, 예 2026-11-18T07:55+09:00>",
    "opened": ["<실제로 연 주소>", "…"], "blocked": ["<못 연 호스트와 상태, 예 data.krx.co.kr 403>"],
    "found": [{"kind": "공시|뉴스|실적|수급|일정|간밤|기타", "text": "<한두 문장 요약 — 본문을 옮기지 않는다>",
               "url": "<그 주소>", "when": "<그 정보의 시각·날짜>", "numbers": {"<이름_단위>": 0.0}}],
    "missing": ["<찾아봤지만 못 찾은 kind — 까닭을 붙이려면 'kind: 까닭'처럼 kind 낱말로 시작>"],
}

SOURCES = {
    "stock": ["공시: DART 최근 공시 https://dart.fss.or.kr/dsab007/main.do (회사 이름으로), 네이버 공시 "
              "https://finance.naver.com/item/news_notice.naver?code={code}",
              "뉴스: https://finance.naver.com/item/news_news.naver?code={code} (지난 24시간), 필요하면 웹 검색 '{name} 공시' '{name} 실적'",
              "실적: 직전 10 거래일 안에 실적 발표가 있었나 — 있으면 실제 영업이익·매출과 전망 평균(컨센서스)의 차이(%). 없으면 다음 발표 예정일",
              "수급: 전일 외국인·기관 순매수 https://finance.naver.com/item/frgn.naver?code={code} (주 수 또는 억 원)"],
    "etf": ["간밤: 간밤 미국 지수 마감(S&P 500·나스닥)과 지금 미국 지수 선물(https://query1.finance.yahoo.com/v8/finance/chart/ES=F), "
            "원·달러 환율",
            "뉴스: 이 묶음이 따르는 시장 전체를 움직일 지난 24시간 소식(금리·정책·큰 회사 실적)",
            "수급: 전일 외국인·기관 순매수(코스피 현물, 가능하면 선물) https://finance.naver.com/sise/sise_trans_style.naver",
            "일정: 오늘·내일 발표(경제 지표·중앙은행·큰 실적)와 예상치"],
    "sector": ["간밤: 이 ETF가 담은 업종과 같은 업종의 해외 종목·지수의 간밤 움직임(등락 %), 원·달러 환율",
               "뉴스: 이 ETF가 담은 업종의 지난 24시간 소식과 큰 구성 종목의 공시(구성 종목은 운용사 상품 페이지나 "
               "https://finance.naver.com/item/main.naver?code={code} 에서)",
               "수급: 전일 이 ETF와 큰 구성 종목의 외국인·기관 순매수 https://finance.naver.com/item/frgn.naver?code={code}",
               "일정: 오늘·내일 그 업종에 걸린 발표(정책·수주·큰 구성 종목 실적)"],
    "coin": ["간밤: 지난 24시간 가격 흐름(https://api.upbit.com/v1/candles/minutes/60?market=KRW-BTC&count=24), 해외 거래소 값과의 차이",
             "뉴스: 지난 24시간 코인 시장 소식(규제·해킹·큰 보유자)",
             "수급: 해외 현물 상장 상품 자금 흐름(전일), 거래소 입출금 — 못 찾으면 못 찾음",
             "일정: 오늘·내일 미국 지표·규제 일정"],
}


def kind_of(asset: str) -> str:
    if asset in C.LISTED_ETF:
        return "sector"
    return "stock" if C.is_stock(asset) else C.ASSETS[asset].kind


def brief(day: dt.date, asset: str, name: str, path: Path, held: bool, turn: bool) -> str:
    k = kind_of(asset)
    req = REQUIRED[k]
    src = "\n".join(f"- {s.format(code=asset, name=name)}" for s in SOURCES[k])
    tags = (" — 손 계좌가 들고 있다" if held else "") + (" — 오늘 손 예측 차례" if turn else "")
    return f"""# 아침 조사: {name} ({asset}) — {day.isoformat()}{tags}

너는 stock-rader 아침 조사 에이전트 하나다. 이 대상 하나만 본다. 사라·팔라는 판단은 하지 않는다(메인이 한다).
오늘 09:00 시가 전에 알 수 있는 것 가운데 이 대상의 오늘 값을 움직일 만한 것을 **놓치지 않게** 모은다. 시각은 한국 시간.

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
    if not str(data.get("by", "")).strip():
        errs.append(f"{w}: by(조사한 에이전트)가 비었다")
    if str(data.get("failed", "")).strip():   # 두 번 실패 — 메인이 까닭을 적는다(빈 채로 넘어가지 않게)
        return errs
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
    pos, opt = DY._args(argv)
    root, day, at = DY.when(opt, dt.time(8, 30))
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
