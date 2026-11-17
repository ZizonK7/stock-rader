"""물어보면 답하는 명령 (2026-11-17, 잼민이 지시서 p562 — 사용자 "내 실제 주식에서 너네한테 물어볼 게 생기면 … 삼양식품 같은 그런 거").

  python -m market.ask <종목 코드 | 이름> [--dry]

한 화면에: 지금 시세 상태(일곱 + 자기 시장 묶음과 같이 움직임), 평소 하루 움직임, 기준선 두 개의 오를 비율(1·5·20 거래일),
그 종목에 걸리는 조사 조각과 오늘(마지막 손 대 보기) 값, 그 조각의 그 종목·비슷한 성질 종목에서의 지난 성적과 표본 수,
이 저장소가 그 종목에 대해 할 수 있는 말의 한계.

기록:
- 목록(stocks.toml)에 없으면 지켜보기만 하는 종목(buy = false, watch = true)으로 더한다 — 다음 매일 실행부터 시세 대 보기·계산기
  ('모름')·기준선이 매일 기록·채점된다. 시세 파일은 만들지 않는다(첫 파일은 매일 실행의 prepare가 만든다 — 저장소 규칙).
- 물어본 것을 asked.toml에 덧붙인다. 다음 아침 실행은 그 종목을 '오늘 손 예측 차례'의 맨 앞에 두어 손 예측(조사 포함)을 기록한다
  — 그 예측이 채점되어 물어본 것이 피드백 재료가 된다. 이 명령 자체는 records/에 쓰지 않는다(기록은 매일 실행만 쓴다).
- --dry: 아무 파일도 쓰지 않고 보여 주기만.

가상 투자 실험의 기록이고 투자 권유가 아니다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import tomllib
import urllib.parse
from pathlib import Path

from . import config as C
from . import hand as H
from . import pieces as P
from . import prices as PR
from . import records as R
from . import scoring as S
from . import stocks as ST

ASKED = C.ROOT / "asked.toml"


def load_asked(path: Path = ASKED) -> list[dict]:
    if not path.exists():
        return []
    return tomllib.loads(path.read_text(encoding="utf-8")).get("q", [])


def lookup(query: str) -> tuple[str, str, str] | None:
    """(코드, 이름, 시장) — 목록에서 먼저, 없으면 네이버 종목 찾기(국내 주식만). 못 찾으면 None."""
    q = query.strip()
    for s in ST.load():
        if str(s["code"]) == q.upper() or ST._same(str(s["name"]), q):
            return str(s["code"]), str(s["name"]), str(s.get("market", "KOSPI"))
    try:
        d = json.loads(PR._get("https://ac.stock.naver.com/ac?q=" + urllib.parse.quote(q) + "&target=stock"))
    except Exception:
        return None
    items = [i for i in d.get("items", []) if i.get("nationCode") == "KOR" and i.get("typeCode") in ("KOSPI", "KOSDAQ")]
    exact = [i for i in items if i["code"] == q.upper() or ST._same(i["name"], q)]
    it = (exact or items or [None])[0]
    return (it["code"], it["name"], it["typeCode"]) if it else None


def add_asked(path: Path, code: str, name: str, at: dt.datetime) -> None:
    esc = lambda x: x.replace("\\", "\\\\").replace('"', '\\"')
    old = path.read_text(encoding="utf-8") if path.exists() else (
        "# 사용자가 물어본 종목 (python -m market.ask가 덧붙인다, 2026-11-17). 다음 매일 실행이 이 종목을 손 예측 차례 맨 앞에 둔다.\n")
    path.write_text(old.rstrip("\n") + f'\n\n[[q]]\ncode = "{code}"\nname = "{esc(name)}"\nat = "{at.isoformat(timespec="seconds")}"\n',
                    encoding="utf-8")


def rows_of(code: str, name: str) -> tuple[list, str]:
    """시세: 저장된 파일이 있으면 그것(끝난 봉), 없으면 받아서 메모리에만(정규장 값, ST.fetch). (줄, 출처 설명)."""
    rows = list(PR.load(code))
    if rows:
        return rows, f"저장된 시세 {len(rows)}줄"
    got = ST.fetch(code, ST.BACKFILL_ROWS, name)
    return got, f"방금 받은 시세 {len(got)}줄(파일에 쓰지 않음)"


def piece_record(pid: str, code: str, corr: float | None) -> tuple[str, str]:
    """(그 종목에서, 같이 움직임이 같은 쪽인 다른 종목에서) 조각이 켜졌던 창의 '주장대로' 수와 독립인 날."""
    from . import breakdown as BD
    try:
        items = BD.collect(pid)["on"]
    except SystemExit:
        return "–", "–"
    mine = [i for i in items if i["asset"] == code]
    side = None if corr is None else corr >= P.COMOVE_MIN
    like = [i for i in items if C.is_stock(i["asset"]) and i["asset"] != code and i["comove"] is not None
            and side is not None and (i["comove"] >= P.COMOVE_MIN) == side]

    def say(xs):
        if not xs:
            return "기록 없음"
        r = BD.row(xs)
        d, rl = r["dir"], r["rel"]
        s = f"창 {r['n']}·독립인 날 {r['days']}"
        if d[1]:
            s += f"·주장대로 {d[0]}/{d[1]}"
        if rl[1]:
            s += f"·묶음 대비 {rl[0]}/{rl[1]}"
        return s
    return say(mine), say(like)


def answer(query: str, now: dt.datetime | None = None, write: bool = True) -> str:
    now = now or C.now_kst()
    ST.register()
    found = lookup(query)
    if not found:
        return f"'{query}'를 국내 상장 종목에서 찾지 못했다(미국 주식·ETF 밖의 상품은 이 저장소가 다루지 않는다)."
    code, name, market = found
    C.STOCK_NAMES.setdefault(code, name)
    C.STOCK_SUFFIX.setdefault(code, ".KQ" if market == "KOSDAQ" else ".KS")
    listed = code in {str(s["code"]) for s in ST.load()}
    notes = []
    if write and not listed:
        ST.add(C.STOCKS, code, name, f"사용자 질문 {now.date().isoformat()} — 지켜보기(python -m market.ask)", market,
               now.date(), watch_only=True)
        notes.append("목록(stocks.toml)에 지켜보기만 하는 종목으로 더했다 — 다음 매일 실행부터 매일 기록·채점된다.")
    if write:
        add_asked(ASKED, code, name, now)
        notes.append("asked.toml에 적었다 — 다음 아침 실행이 이 종목의 손 예측(조사 포함)을 맨 먼저 기록한다.")
    rows, src = rows_of(code, name)
    h = PR.view(code, rows, now)
    b = PR.view(C.HOME_BUNDLE, PR.load(C.HOME_BUNDLE), now)
    L = [f"# {name} ({code}, {market}) — {now:%Y-%m-%d %H:%M} KST 기준", ""]
    if len(h) < P.NEED:
        L.append(f"시세가 {len(h)}봉뿐이라(필요 {P.NEED}봉) 시세 상태를 못 읽는다.")
    values, numbers = P.read(h.closes)
    cm, corr = P.comove(h.rows, b.rows)
    last = h.last
    tm = S.typical_move(h, 1)
    L.append(f"- 마지막 끝난 봉 {last.date} 종가 {last.close:,.0f}원 ({src}; 종목 종가는 2026-11-17부터 정규장 값, 그 전 저장분은 시간외 포함)")
    if tm:
        L.append(f"- 평소 하루 움직임(60봉) 약 {tm * 100:.1f}% — 한국 묶음은 약 {S.typical_move(b, 1) * 100:.1f}%")
    rets = {k: numbers.get(k) for k in ("r1", "r5", "r20", "r60")}
    L.append("- 최근 수익률: " + ", ".join(f"{k[1:]}봉 {v * 100:+.1f}%" for k, v in rets.items() if v is not None))
    L.append("- 시세 상태: " + ", ".join(f"{k}={v}" for k, v in values.items()))
    L.append(f"- 자기 시장 묶음과 같이 움직임(60봉 상관): {corr if corr is not None else '모름'} → {P.COMOVE_STATE}={cm}"
             f" (문턱 {P.COMOVE_MIN}; '아니오'면 금리 기대 같은 시장 전체 조각이 이 종목에 안 걸린다)")
    L += ["", "## 기준선(지난 시세의 비율 — 예측이 아니라 출발점)", "| 기간 | 최근 250봉 오른 비율 | 알려진 전체 오른 비율 | 크기 창(%) |",
          "|---|---|---|---|"]
    for hz in (1, 5, 20):
        br, bw = S.base_rate(h, hz, fill=True)
        ar, _ = S.all_rate(h, hz, fill=True)
        L.append(f"| {hz}거래일 | {br if br is None else f'{br:.2f}'} | {ar if ar is None else f'{ar:.2f}'} | "
                 f"{'–' if not bw else f'{bw[0]:.1f}–{bw[1]:.1f}'} |")
    raw = tomllib.loads(C.KNOWLEDGE.read_text(encoding="utf-8"))
    pieces = H._piece_index(raw)
    hrs = R.read(C.RECORDS, "hand_reading")
    lastr = hrs[-1] if hrs else {}
    vals = dict(lastr.get("values", {}))
    by = (lastr.get("by_asset") or {}).get(code, {})
    vals.update({k: v["value"] if isinstance(v, dict) else v for k, v in by.items()})
    vals[P.COMOVE_STATE] = cm
    L += ["", f"## 이 종목에 걸리는 조사 조각 (값은 마지막 손 대 보기 {lastr.get('run', '없음')} 기준)",
          "| 조각 | 조건 값 | 켜짐 | 이 종목에서 | 같이 움직임이 같은 쪽 종목에서 |", "|---|---|---|---|---|"]
    n_rows = 0
    for pid, info in sorted(pieces.items()):
        if not info["research"] or not any(C.in_scope(code, sc) for sc in info["scope"]):
            continue
        node = next(x for x in raw["hypothesis"] if x["id"] == pid)
        claim = next((c for c in node["claims"] if C.in_scope(code, c.get("scope"))), node["claims"][0])
        need = {info["state"]: claim.get("when", "예"), **(claim.get("also") or {})}
        got = {k: vals.get(k, "안 물음") for k in need}
        on = "예" if all(got[k] == v for k, v in need.items()) else (
            "모름" if any(g not in ("예", "아니오") for g in got.values()) else "아니오")
        mine, like = piece_record(pid, code, corr)
        L.append(f"| {pid}{' (씨앗)' if info['seed'] else ''} | {', '.join(f'{k}={v}' for k, v in got.items())} | {on} | {mine} | {like} |")
        n_rows += 1
    if not n_rows:
        L.append("| (없음) | | | | |")
    own = [p for p in R.read(C.RECORDS, "prediction") if p["asset"] == code and p["predictor"] == "hand"]
    sc = {s["prediction"]: s for s in R.read(C.RECORDS, "score")}
    done = [sc[p["id"]] for p in own if p["id"] in sc and sc[p["id"]].get("counted", True)]
    L += ["", f"## 이 종목의 지난 손 예측: {len(own)}건, 채점·한 번만 센 것 {len(done)}건"
              + (f", 확률 점수 평균 {sum(s['brier'] for s in done if s['brier'] is not None) / max(1, sum(s['brier'] is not None for s in done)):.3f}(동전 0.25)"
                 if done else "")]
    L += ["", "## 한계 (이 답을 믿기 전에)",
          "- 가상 투자 실험의 기록이다. 투자 권유가 아니다.",
          "- 계산기는 종목에 '모름'이다(계산 조각 숫자는 세 대상 시세로만 맞췄다). 위 비율은 지난 시세의 비율일 뿐 예측이 아니다.",
          "- 종목 하나를 가리키는 조각은 '실적이 예상보다 좋았다' 하나뿐이다 — 회사 사정(신제품·수출·소송 등)에 대해 할 말이 얕다.",
          "- 기록은 2026-10-21부터 몇 주뿐이다. 위 성적의 표본은 대개 한 자릿수이고 같은 날 여러 종목은 한 사건이다.",
          "- 한국 상장 종목만 다룬다(미국 주식은 안 된다). 코스닥 종목도 코스피 200 묶음에 견준다.",
          "- 2026-11-17 전에 저장된 종목 종가는 시간외(20:00까지)를 친 값이라 그 전 채점에 하루 0–3% 차이가 섞여 있다."]
    if notes:
        L += ["", "## 이 명령이 한 일"] + [f"- {n}" for n in notes]
    return "\n".join(L)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    print(answer(" ".join(args), write="--dry" not in argv))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
