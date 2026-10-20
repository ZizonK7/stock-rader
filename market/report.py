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

MARK = "[[채울 것"
DISCLAIM = "실제 돈이 아닌 가상 투자 실험이며 투자 권유가 아니다."
REPO_URL = "https://github.com/ZizonK7/stock-rader/blob/main"


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
    return f"{units}주" if spec.kind == "etf" else f"{units / spec.unit:.8f}개"


class Book:
    """한 기록 폴더를 읽어 날짜별 계좌·체결·주문·채점을 꺼내는 도우미."""

    def __init__(self, folder: Path):
        self.recs = {k: R.read(folder, k) for k in R.FIELDS}
        self.acc = defaultdict(dict)  # 계좌 → 날짜 → 기록
        for a in self.recs["account"]:
            self.acc[a["account"]][a["run"]] = a
        self.preds = {p["id"]: p for p in self.recs["prediction"]}

    def value(self, account: str, run: str) -> float | None:
        a = self.acc[account].get(run)
        return a["value"] if a else None

    def prev_run(self, account: str, run: str) -> str | None:
        older = sorted(r for r in self.acc[account] if r < run)
        return older[-1] if older else None

    def invested(self, account: str, asset: str, until: str, since: str = "") -> int:
        """체결 기준 순투자(산 금액 + 수수료 − 판 금액 + 수수료)."""
        t = 0
        for f in self.recs["fill"]:
            if f["account"] == account and f["asset"] == asset and since < f["run"] <= until:
                t += (f["value"] + f["fee"]) if f["side"] == "buy" else -(f["value"] - f["fee"])
        return t

    def counted(self, run_from: str = "", run_to: str = "9999") -> list[tuple[dict, dict]]:
        pairs = [(self.preds[s["prediction"]], s) for s in self.recs["score"]
                 if s["prediction"] in self.preds and run_from <= s["run"] <= run_to and s.get("counted", True)]
        return pairs


def asset_value(acc: dict, asset: str) -> float:
    pr = acc["prices"].get(asset)
    return acc["positions"].get(asset, 0) * pr[1] / C.ASSETS[asset].unit if pr else 0.0


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
    orders = [o for o in b.recs["order"] if o["account"] == "live:hand" and o["run"] == run]
    if orders:
        parts = []
        for side, verb in (("buy", "사는"), ("sell", "파는")):
            mine = [f"{C.ASSETS[o['asset']].name} {won_about(o['qty_units'] * o['ref_price'] / C.ASSETS[o['asset']].unit)}어치"
                    for o in orders if o["side"] == side]
            if mine:
                parts.append(", ".join(mine) + f"를 {verb} 주문")
        first = ("오늘 " + "과 ".join(parts) + "을 냈다(값은 주문 뒤 처음 정해지는 공식 가격으로 치른다:"
                 " 비트코인은 다음 09:00 시가, ETF는 장 마감 가격).")
    else:
        first = "오늘은 거래하지 않았다."
    v = me["value"]
    if pa is None:
        second = "오늘 가상 1,000만 원으로 계좌를 시작했다."
    else:
        d = v - pa["value"]
        second = (f"계좌는 {won(d)}({pct(d / pa['value'])}) {'늘어' if d >= 0 else '줄어'} {won(v, False)}"
                  f"(시작 이후 {pct(v / start - 1)})이 됐다.")
    if eq and pa is not None:  # 첫날은 견줄 것이 없다
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
            ref_why.append(f"{C.ASSETS[p['asset']].name} 1주 오를 확률 {p['p_up']}, 확신 {p.get('confidence', '?')}, {rec}")
    reviews = [r for r in b.recs["review"] if r["run"] == run]
    good = [f"{r['prediction']}: {r['reason']}" for r in reviews if r["why"] == "맞음"]
    bad = [f"{r['prediction']}: {r['why']}, 틀린 조각 {', '.join(r['wrong']) or '–'} — {r['reason']}" for r in reviews
           if r["why"] != "맞음"]
    research = [r for r in b.recs["research"] if r["run"] == run]
    found = sorted({lk["place"] for r in research for lk in r["looked"] if lk["result"] == "찾음"})
    none = sorted({lk["place"] for r in research for lk in r["looked"] if lk["result"] in ("없음", "막힘")})
    late = sorted({lk["place"] for r in research for lk in r["looked"] if lk["result"] == "늦음"})
    new = sorted({lk["place"] for r in research for lk in r["looked"] if lk.get("new")})
    lines += [f"**왜** [[채울 것: 근거 조각 1~3개(짧은 이름과 성적), 조사에서 나온 새 사실과 출처, 확신도]]",
              _ref(ref_why),
              f"**잘된 것** [[채울 것: 없으면 '없음']]", _ref(good),
              f"**잘못된 것** [[채울 것: 틀린 예측 → 틀린 조각 → 까닭 → 바꾼 것. 없으면 '없음']]", _ref(bad),
              f"**조사 메모** [[채울 것: 한두 줄. 오늘 판단에 쓸모 있던 곳과 끝내 못 찾은 것(막힌 곳 목록은 기록에만)]]",
              _ref([f"찾음: {', '.join(found) or '없음'}", f"없음·막힘: {', '.join(none) or '없음'}",
                    f"늦음(판단 뒤에 나옴): {', '.join(late) or '없음'}", f"새로 시도: {', '.join(new) or '없음'}"]),
              f"**내일 볼 것** [[채울 것: 숫자 조건 + 확신도]]", ""]

    # 계좌 표
    lines += ["| 대상 | 보유 | 평가액 | 오늘 | 누적 |", "|---|--:|--:|--:|--:|"]
    for a in C.ASSET_IDS:
        val = asset_value(me, a)
        today = 0.0
        if pa and pa["prices"].get(a) and me["prices"].get(a):
            today = pa["positions"].get(a, 0) * (me["prices"][a][1] - pa["prices"][a][1]) / C.ASSETS[a].unit
        cum = val - b.invested("live:hand", a, run)
        lines.append(f"| {C.ASSETS[a].name} | {units_text(a, me['positions'].get(a, 0))} | {won(val, False)} | "
                     f"{won(today)} | {won(cum)} |")
    lines.append(f"| 현금 | | {won(me['cash'], False)} | | |")
    d_me = v - pa["value"] if pa else 0.0
    lines.append(f"| **계좌 합계** | | **{won(v, False)}** | {won(d_me)} ({pct(d_me / pa['value']) if pa else '+0.00%'}) | "
                 f"{won(v - start)} ({pct(v / start - 1)}) |")
    if eq:
        pe = b.acc["baseline:equal_hold"].get(prev) if prev else None
        de = eq["value"] - pe["value"] if pe else 0.0
        lines.append(f"| 기준선: 셋을 나눠 사 두기만 | | {won(eq['value'], False)} | {won(de)} | "
                     f"{won(eq['value'] - start)} ({pct(eq['value'] / start - 1)}) |")
    calc_acc = b.acc["live:calc"].get(run)
    if calc_acc:
        lines.append(f"| 참고: 계산기 계좌 | | {won(calc_acc['value'], False)} | | {pct(calc_acc['value'] / start - 1)} |")
    lines += ["", f"평가 가격은 07:37에 알려진 마지막 끝난 종가다({', '.join(f'{C.ASSETS[a].name} {d}' for a, (d, _) in me['prices'].items())})."
              " 수수료 가정(ETF 0.015%, 업비트 0.05%)을 넣었고 세금은 계산하지 않았다.", ""]
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
    lines += ["", "## 2. 대상별 (이 계좌)", "| 대상 | 이번 주 손익 | 지금 평가액 |", "|---|--:|--:|"]
    me = b.acc["live:hand"].get(run)
    runs = sorted(r for r in b.acc["live:hand"] if r < mon)
    s_acc = b.acc["live:hand"][runs[-1]] if runs else None
    best = []
    for a in C.ASSET_IDS:
        if not me:
            break
        v1 = asset_value(me, a)
        v0 = asset_value(s_acc, a) if s_acc else 0.0
        pnl = v1 - v0 - b.invested("live:hand", a, run, since=runs[-1] if runs else "")
        best.append((pnl, a))
        lines.append(f"| {C.ASSETS[a].name} | {won(pnl)} | {won(v1, False)} |")
    if best:
        hi, lo = max(best), min(best)
        lines.append(f"\n가장 많이 번 것: {C.ASSETS[hi[1]].name} ({won(hi[0])}), 가장 많이 잃은 것: {C.ASSETS[lo[1]].name} ({won(lo[0])})")
    lines += ["", "## 3. 이번 주 거래와 근거"]
    week_orders = [o for o in b.recs["order"] if o["account"] == "live:hand" and mon <= o["run"] <= run]
    for o in week_orders:
        amt = o["qty_units"] * o["ref_price"] / C.ASSETS[o["asset"]].unit
        lines.append(f"- {o['run']} {C.ASSETS[o['asset']].name} {'샀다' if o['side'] == 'buy' else '팔았다'} {won_about(amt)}"
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
        n = len(re.findall(re.escape(MARK), p.read_text(encoding="utf-8")))
        if n:
            bad.append(f"{p.name}: 채우지 않은 자리 {n}곳")
    if not list(root.reports.glob(f"{day.isoformat()}*.md")):
        bad.append(f"{day} 일지가 없다")
    for b in bad:
        print(b)
    print("일지 점검 통과" if not bad else "일지 점검 실패")
    return 1 if bad else 0
