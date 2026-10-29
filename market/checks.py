"""stock-rader 논리집의 자동 검사 (PROVISO 성적표·점검이 부른다: python -m market.logic check|audit).

PROVISO 분야 checks.py와 같은 모양: CHECKS(검사 이름 → 함수), SOURCE(판정 출처), SEEN_UNTIL, AUDIT(kn) → (오류, 경고).
판정은 시장 결과(채점 기록)로 한다. 가설은 **세운 뒤의 날들로만** 시험한다: 계산 조각은 예측의 기준 날짜가 조각의
seen_until 뒤인 것만, 조사 조각·손 예측 가설은 판단 날짜(run)가 seen_until 뒤인 것만.

출처를 검사마다 따로 낸다 (PROVISO 코어 2026-10-21 '검사마다 출처', 결정 4):
- records/replay/ (2026-07-01~10-19 재생, 홀드아웃) → 'replay' (실제 기록 재생)
- records/ (진짜 계좌, 클라우드 매일 실행) → 'measured' (실측)
같은 검사가 두 기록에 따로 돌아 성적표에 두 줄로 나온다.

채점은 같은 (예측기·대상·기간·기준 날짜)를 한 번만 센다(결정 1, scoring.first_only).
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from . import config as C
from . import fit as F
from . import records as R
from . import research as RS
from . import scoring as S
from . import stocks as ST
from . import versions as V

# 기본 출처: 출처 하나로만 판정하는 검사(2-튜플)에 붙는다. 진짜 계좌 기록만 보는 검사들이다.
SOURCE = "measured"
SEEN_UNTIL = {"piece_direction": "2026-06-30", "piece_size": "2026-06-30", "account_vs_equal": "2026-06-30",
              "research_direction": C.RESEARCH_SINCE, "hand_vs_calc": C.RESEARCH_SINCE,
              "place_official": C.RESEARCH_SINCE}
NEED = 0.8
MIN_SIDE = 5      # 방향 비교: 조건 있음·없음이 각각 이만큼은 있어야 그 칸을 센다
MIN_PAIRS = 20    # 손 예측 vs 계산기: 같은 열쇠의 짝이 이만큼은 있어야 판정
MIN_LOOKS = 10    # 볼 곳 가설: 공식/비공식 각각 이만큼 본 기록이 있어야 판정
MAX_PIECES = 8    # 장치(조각 수 상한): 살아 있는 계산 조각 수 (조사 조각은 따로 센다, 결정 5)
# 조사 조각 상한 6: 조각 하나가 매일 조사 질문 하나(웹 조사)와 메인의 손 대 보기 하나를 부른다. 하루 기록이 대상당 한 줄이라
# 방향 시험(양쪽 5개 이상)에 몇 주가 걸리는데, 조각이 많으면 각 조각의 '예' 날이 흩어져 시험이 더 늦어지고, 메인이
# 하루에 대 보는 수가 늘면 대 보기가 엉성해진다. 첫 묶음 4개 + 쪼갤 여유 2개.
MAX_RESEARCH_PIECES = 6
# 종목 조각 상한 3 (2026-10-29): 종목마다 대 보는 조사 조각(조건 상태에 per_asset = true)은 시장 전체 조각과 따로 센다.
# 매일 모든 날이 아니라 든 종목·살 후보에만 묻고, 시장 조각의 쪼갤 여유(6 중 남은 몫)를 종목 조각이 먹지 않게 하려는 것.
# 3은 손 계좌 종목 수 상한과 같다: 종목 하나에 조각 하나쯤이면 같은 날 대 보는 양이 시장 조각만큼에서 멈춘다.
MAX_STOCK_PIECES = 3
SPANS = dict(zip(C.HORIZON_LABEL, range(3)))   # 다음/1주/4주 → 기간 자리


def _folders() -> list[tuple[str, Path]]:
    return [("replay", C.REPLAY), ("measured", C.RECORDS)]


def _raw() -> dict:
    return tomllib.loads(C.KNOWLEDGE.read_text(encoding="utf-8"))


def _raw_node(node_id: str) -> dict | None:
    raw = _raw()
    return next((h for h in raw.get("hypothesis", []) + raw.get("rule", []) if h.get("id") == node_id), None)


def _node(node_id: str) -> dict | None:
    return _raw_node(node_id)


def _scored(folder: Path, keep) -> list[tuple[dict, dict]]:
    """(예측, 채점) — keep(예측)이 참인 것만, 같은 열쇠는 처음 말한 것 하나만."""
    preds = {p["id"]: p for p in R.read(folder, "prediction")}
    pairs = [(preds[s["prediction"]], s) for s in R.read(folder, "score")
             if s["prediction"] in preds and keep(preds[s["prediction"]])]
    return S.first_only(pairs)


def _holds(readings: dict, claim: dict, state: str) -> bool | None:
    """주장의 조건이 켜졌나: 상태 = when, 그리고 더한 조건(also)이 모두 맞음. 값을 모르면 None."""
    need = {state: claim.get("when", "예"), **(claim.get("also") or {})}
    vals = {k: readings.get(k) for k in need}
    if any(v not in ("예", "아니오") for v in vals.values()):
        return None
    return all(vals[k] == v for k, v in need.items())


def _compare(on: str, dirn: str, y: list[float], n: list[float]) -> tuple[bool, str]:
    if on == "up_odds":
        a, b = sum(r > 0 for r in y) / len(y), sum(r > 0 for r in n) / len(n)
        shown = f"오른 비율 예 {a:.2f} vs 아니오 {b:.2f}"
    else:
        a = sorted(abs(r) for r in y)[len(y) // 2]
        b = sorted(abs(r) for r in n)[len(n) // 2]
        shown = f"움직임 가운데 값 예 {a * 100:.2f}% vs 아니오 {b * 100:.2f}%"
    return ((a > b) if dirn == "up" else (a < b)), shown


def _verdict(cells: dict, what: str, cut: str) -> tuple[str, str]:
    """cells: (on, dir, 대상, 기간) → {True: [수익률], False: [...]} 을 방향 시험 판정으로."""
    lines, verdicts = [], []
    for (on, dirn, asset, h), cell in sorted(cells.items()):
        y, n = cell[True], cell[False]
        if len(y) < MIN_SIDE or len(n) < MIN_SIDE:
            lines.append(f"    {on} {asset}:{h} 예 {len(y)} / 아니오 {len(n)} — 수가 모자라 안 셈")
            continue
        ok, shown = _compare(on, dirn, y, n)
        verdicts.append(ok)
        lines.append(f"    {on} {asset}:{h} {shown} → {'맞음' if ok else '틀림 ✗'} (예 {len(y)}, 아니오 {len(n)})")
    detail = f"  시험({cut} 뒤 {what}):\n" + ("\n".join(lines) or "    없음")
    if not verdicts:
        return "시험 전 (세운 뒤 기록에 조건 있음·없음이 아직 모자람)", detail
    hits = sum(verdicts)
    word = "지지됨" if hits / len(verdicts) >= NEED else "반박됨"
    return f"{word} (방향 시험 {len(verdicts)}건 중 {hits}건 맞음)", detail


# ---------- 계산 조각 ----------
def piece_direction(node_id: str):
    """조각의 방향 주장(곱하기 효과의 dir)이 세운 뒤의 날들에서 맞나: 대상·기간마다 조건이 켜졌을 때와 아닐 때를 비교.
    오를 확률은 오른 비율, 움직임은 |수익률| 가운데 값. 겹치는 기간의 표본이라 거친 비교다. 재생·실측 따로."""
    node = _node(node_id)
    if not node:
        return "불명 (노드 없음)", ""
    cut = node.get("seen_until", "9999")
    state = (node.get("conditions") or [None])[0]
    claims = [c for c in node.get("claims", []) if c.get("op", "mul") == "mul"]
    if not state or not claims:
        return "시험 전 (방향 주장이 없는 기준값 조각)", ""
    out = []
    for src, folder in _folders():
        # 종목(2026-10-29)은 빼다: 계산 조각 숫자는 세 대상에만 맞췄고 종목의 계산기는 '모름'이다
        pairs = _scored(folder, lambda p: p["predictor"] == "calc" and p["base_date"] > cut and p["asset"] in C.CORE)
        if src == "replay" and not pairs:
            continue  # 세운 날이 재생 뒤면 재생 줄은 없다
        cells: dict = {}
        for p, s in pairs:
            for c in claims:
                on = _holds(p["readings"], c, state)
                if on is None:
                    continue
                cells.setdefault((c["on"], c.get("dir"), p["asset"], p["horizon"]), {True: [], False: []})[on].append(s["ret"])
        out.append((src, *_verdict(cells, "기준 날짜의 계산기 예측", cut)))
    return out


def piece_size(node_id: str):
    """이 조각의 움직임 효과가 쓰인 계산기 예측의 크기 적중이, 같은 날·대상·기간의 기준선(최근 비율) 크기 적중보다 나은가."""
    node = _node(node_id)
    if not node:
        return "불명 (노드 없음)", ""
    cut = node.get("seen_until", "9999")
    out = []
    for src, folder in _folders():
        rows = _scored(folder, lambda p: p["base_date"] > cut and p["asset"] in C.CORE)
        if src == "replay" and not rows:
            continue
        base = {(p["run"], p["asset"], p["horizon"]): s for p, s in rows if p["predictor"] == "baseline:base_rate"}
        mine, theirs = [], []
        for p, s in rows:
            if p["predictor"] != "calc" or s["size_hit"] is None or f"{node_id}:move" not in p["used"]:
                continue
            b = base.get((p["run"], p["asset"], p["horizon"]))
            if b is None or b["size_hit"] is None:
                continue
            mine.append(s["size_hit"])
            theirs.append(b["size_hit"])
        if not mine:
            out.append((src, "시험 전 (이 조각의 움직임 효과가 쓰인 채점이 없음)", ""))
            continue
        detail = f"  같은 {len(mine)}건: 계산기 크기 적중 {sum(mine)} vs 기준선(최근 250봉 비율) {sum(theirs)}"
        word = "지지됨" if sum(mine) >= sum(theirs) else "반박됨"
        out.append((src, f"{word} (크기 시험 {len(mine)}건 중 {sum(mine)}건 맞음, 기준선 {sum(theirs)}건)", detail))
    return out


def account_vs_equal():
    """R_weights: 계산기를 따른 계좌가 셋을 나눠 사 두기보다 나은가 (세운 뒤의 날들, 마지막 평가액). 재생·실측 따로."""
    out = []
    for (src, folder), mine_id in zip(_folders(), ("replay:calc", "live:calc")):
        accs = [a for a in R.read(folder, "account") if a["run"] > SEEN_UNTIL["account_vs_equal"]]
        mine = [a for a in accs if a["account"] == mine_id]
        eq = [a for a in accs if a["account"] == "baseline:equal_hold"]
        if mine and eq:
            m, e = mine[-1]["value"], eq[-1]["value"]
            detail = f"  {mine[-1]['run']}: 계산기 계좌 {m:,.0f}원 vs 나눠 사 두기 {e:,.0f}원"
            out.append((src, f"{'지지됨' if m >= e else '반박됨'} (시험 1건 중 {1 if m >= e else 0}건 맞음)", detail))
        else:
            out.append((src, "시험 전 (계좌 기록 없음)", ""))
    return out


# ---------- 조사 조각과 손 예측 (진짜 계좌만) ----------
def research_direction(node_id: str) -> tuple[str, str]:
    """조사 조각의 방향 주장이 세운 뒤의 판단에서 맞나: 그날 손 대 보기 값이 '예'인 날과 '아니오'인 날의 결과를 비교.
    결과(수익률)는 그날 계산기 예측의 채점에서 가져온다(대상·기간마다 하나, 같은 기준 날짜는 한 번). 범위 밖 대상은 뺀다."""
    node = _node(node_id)
    if not node:
        return "불명 (노드 없음)", ""
    cut = node.get("seen_until", C.RESEARCH_SINCE)
    state = (node.get("conditions") or [None])[0]
    claims = node.get("claims", [])
    hrs = {h["run"]: h for h in R.read(C.RECORDS, "hand_reading")}
    cells: dict = {}
    for p, s in _scored(C.RECORDS, lambda p: p["predictor"] == "calc" and p["run"] > cut):
        h = hrs.get(p["run"])
        if h is None:
            continue
        # 종목마다 대 본 값(by_asset)은 그 종목에만 (2026-10-29). 종목의 결과는 그날 종목 계산기('모름') 줄의 채점에서 온다
        by = (h.get("by_asset") or {}).get(p["asset"], {})
        vals = {**h["values"], **{k: v["value"] if isinstance(v, dict) else v for k, v in by.items()}}
        hz = C.ASSETS[p["asset"]].horizons.index(p["horizon"])
        for c in claims:
            if not C.in_scope(p["asset"], c.get("scope")):
                continue
            if c.get("span") and hz not in [SPANS[x] for x in c["span"]]:
                continue
            on = _holds(vals, c, state)
            if on is None:
                continue
            cells.setdefault((c["on"], c.get("dir"), p["asset"], p["horizon"]), {True: [], False: []})[on].append(s["ret"])
    return _verdict(cells, "판단의 손 대 보기", cut)


def hand_vs_calc() -> tuple[str, str]:
    """H_hand_vs_calc: 같은 (대상·기간·기준 날짜)의 손 예측과 계산기 예측을 짝지어 확률 점수 평균을 비교."""
    cut = SEEN_UNTIL["hand_vs_calc"]
    pairs = _scored(C.RECORDS, lambda p: p["predictor"] in ("hand", "calc") and p["run"] > cut)
    by: dict = {}
    for p, s in pairs:
        if s["brier"] is not None:
            by.setdefault((p["asset"], p["horizon"], p["base_date"]), {})[p["predictor"]] = s["brier"]
    both = [(v["hand"], v["calc"]) for v in by.values() if "hand" in v and "calc" in v]
    if len(both) < MIN_PAIRS:
        return f"시험 전 (짝 {len(both)}개 < {MIN_PAIRS})", ""
    h = sum(a for a, _ in both) / len(both)
    c = sum(b for _, b in both) / len(both)
    wins = sum(a < b for a, b in both)
    detail = f"  짝 {len(both)}개: 손 예측 확률 점수 {h:.4f} vs 계산기 {c:.4f} (손이 더 낮은 짝 {wins})"
    return f"{'지지됨' if h < c else '반박됨'} (시험 1건 중 {1 if h < c else 0}건 맞음)", detail


def place_official() -> tuple[str, str]:
    """H_place_official: 금리·일정 질문에서 공식 기관 볼 곳이 다른 곳보다 찾은 비율이 높고 늦은 비율이 낮은가."""
    official = {p["id"] for p in RS.load_places() if p.get("official")}
    cut = SEEN_UNTIL["place_official"]
    tally = {True: [0, 0, 0], False: [0, 0, 0]}  # 본 횟수, 찾음, 늦음
    for r in R.read(C.RECORDS, "research"):
        if r["run"] <= cut or r["state"] not in ("S_q_rate_up", "S_q_event_soon"):
            continue
        for lk in r["looked"]:
            t = tally[lk["place"] in official]
            t[0] += 1
            t[1] += lk["result"] == "찾음"
            t[2] += lk["result"] == "늦음"
    (on, of, ol), (xn, xf, xl) = tally[True], tally[False]
    if on < MIN_LOOKS or xn < MIN_LOOKS:
        return f"시험 전 (공식 {on}번, 다른 곳 {xn}번 봄 — 각 {MIN_LOOKS}번 필요)", ""
    ok = of / on >= xf / xn and ol / on <= xl / xn
    detail = f"  공식: {on}번 중 찾음 {of}, 늦음 {ol} / 다른 곳: {xn}번 중 찾음 {xf}, 늦음 {xl}"
    return f"{'지지됨' if ok else '반박됨'} (시험 1건 중 {int(ok)}건 맞음)", detail


CHECKS = {"piece_direction": piece_direction, "piece_size": piece_size, "account_vs_equal": account_vs_equal,
          "research_direction": research_direction, "hand_vs_calc": hand_vs_calc, "place_official": place_official}


# ---------- 점검 (audit의 분야 점검) ----------
def _names_in(text: str) -> list[str]:
    words = C.ASSET_WORDS + ST.name_words()  # 종목 이름·코드도 조각 문장에 쓰지 않는다(2026-10-29)
    return [w for w in words if re.search(re.escape(w), text, re.IGNORECASE)]


def is_research(node) -> bool:
    return node.data.get("read_by") == "조사"


def audit(kn, want: dict[str, list[dict]] | None = None) -> tuple[list[str], list[str]]:
    """오류: 조각 수 상한(계산·조사 따로), 조각 이름·문장의 대상 이름, 기록에서 맞춘 값과 다른 효과(손으로 넣은 숫자),
    효과 칸 모양, 조사 조각 모양(조건 상태·범위·기간·세운 날), 버전 목록, 볼 곳 목록.
    경고: 세운 효과가 하나도 없는 계산 조각, 세운 뒤 기록에서 방향이 틀린 조각(돌아보기 대상).
    want = 맞추기를 다시 돌린 결과(조각 id → 효과 목록). 없으면 지금 시세로 다시 맞춘다(테스트는 직접 넘긴다)."""
    errors, warns = [], []
    alive = [n for n in kn.claims(alive_only=True) if n.data.get("claims")]
    live = [n for n in alive if not is_research(n)]
    research = [n for n in alive if is_research(n)]
    per_asset = {s.id for s in kn.of("state") if s.data.get("per_asset")}
    stock_pieces = [n for n in research if (n.refs("conditions") or [None])[0] in per_asset]
    research = [n for n in research if n not in stock_pieces]
    if len(stock_pieces) > MAX_STOCK_PIECES:
        errors.append(f"[종목 조각] 살아 있는 종목 조각 {len(stock_pieces)}개가 상한 {MAX_STOCK_PIECES}개를 넘음")
    for n in stock_pieces:
        for c in n.data.get("claims", []):
            if c.get("scope") != "single":
                errors.append(f"[종목 조각] {n.id}: 종목마다 대 보는 조각의 주장은 scope = \"single\"(한 회사 주식)이어야 함")
    if len(live) > MAX_PIECES:
        errors.append(f"[계산 조각] 살아 있는 조각 {len(live)}개가 상한 {MAX_PIECES}개를 넘음 — 외워서 맞추는 쪽이다")
    if len(research) > MAX_RESEARCH_PIECES:
        errors.append(f"[조사 조각] 살아 있는 조각 {len(research)}개가 상한 {MAX_RESEARCH_PIECES}개를 넘음"
                      " — 하루 대 보기가 엉성해지고 시험이 늦어진다")
    for node in list(alive) + kn.of("state"):
        text = " ".join(str(node.data.get(k, "")) for k in ("text", "reason")) + " " + node.id
        text += " ".join(c.get("reason", "") for c in node.data.get("claims", []))
        found = _names_in(text)
        if found:
            errors.append(f"[조각] {node.id}: 이름·문장·이유에 대상 이름({', '.join(found)})이 있음 — 다른 대상에서 다시 쓰게 빼야 한다")
    for node in live:
        if not node.data.get("seen_until"):
            errors.append(f"[계산 조각] {node.id}: seen_until(맞출 때 본 마지막 날짜)이 없음")
        seen = set()
        for e in node.data.get("effect", []):
            key = (e.get("on"), e.get("asset"), e.get("horizon"))
            if e.get("asset") not in C.ASSETS or e.get("horizon") not in C.ASSETS.get(e.get("asset"), C.ASSETS["069500"]).horizons:
                errors.append(f"[계산 조각] {node.id}: 효과의 대상·기간 {key[1]}:{key[2]}이 config에 없음")
            if key in seen:
                errors.append(f"[계산 조각] {node.id}: 같은 대상·기간·값에 효과가 둘 {key}")
            seen.add(key)
        if not node.data.get("effect"):
            warns.append(f"[계산 조각] {node.id}: 세운 효과가 하나도 없음 — 모든 대상·기간에서 맞춘 시세가 방향을 받쳐 주지 않았다(not_set)")
    errors += _research_errors(kn, research + stock_pieces)
    errors += ST.check(ST.load())
    errors += V.check()
    errors += RS.check_places(RS.load_places(), {s.id for s in kn.of("state")})
    if want is None:
        try:
            want = F.expected()
        except Exception as exc:  # 시세 파일이 없는 등
            return errors + [f"[계산 조각] 맞추기를 다시 돌릴 수 없음: {exc}"], warns
    for node in live:
        have = node.data.get("effect", [])
        need = want.get(node.id, [])
        if _norm(have) != _norm(need):
            errors.append(f"[계산 조각] {node.id}: 효과 칸이 시세에서 맞춘 값과 다름 — 숫자는 손으로 넣거나 넓히지 않는다"
                          f"(python -m market.fit --write)")
    for node in alive:
        for name, fn in (("piece_direction", piece_direction), ("research_direction", research_direction)):
            if name not in node.refs("check"):
                continue
            got = fn(node.id)
            for src, verdict, _ in ([("measured", *got)] if isinstance(got, tuple) else got):
                if verdict.startswith("반박"):
                    label = {"replay": "재생", "measured": "실측"}.get(src, src)
                    warns.append(f"[{'조사' if is_research(node) else '계산'} 조각] {node.id}: 세운 뒤 {label} 기록에서 방향이 틀림"
                                 f"({verdict}) — 돌아보기에서 까닭(조건 흐림/이유 틀림/바깥 상황)을 가른다")
    return errors, warns


def _research_errors(kn, research) -> list[str]:
    errs = []
    for node in research:
        d = node.data
        conds = node.refs("conditions")
        st = kn.nodes.get(conds[0]) if conds else None
        if st is None or st.kind != "state":
            errs.append(f"[조사 조각] {node.id}: 첫 조건이 상태 노드가 아님")
        else:
            if st.data.get("known_before") is not True:
                errs.append(f"[조사 조각] {node.id}: 조건 '{st.id}'가 판단 전에 아는 상태가 아님(known_before)")
            if st.data.get("read_by") != "조사" or not st.data.get("ask"):
                errs.append(f"[조사 조각] {node.id}: 조건 '{st.id}'에 read_by = \"조사\"와 조사 질문(ask)이 있어야 함")
            if st.data.get("angle") not in RS.ANGLES:
                errs.append(f"[조사 조각] {node.id}: 조건 '{st.id}'의 angle은 {list(RS.ANGLES)} 중 하나")
        if str(d.get("seen_until", "")) < C.RESEARCH_SINCE:
            errs.append(f"[조사 조각] {node.id}: seen_until이 세운 날({C.RESEARCH_SINCE})보다 이름 — 세운 뒤의 판단으로만 시험한다")
        if "research_direction" not in node.refs("check"):
            errs.append(f"[조사 조각] {node.id}: check에 research_direction이 없음")
        if d.get("effect"):
            errs.append(f"[조사 조각] {node.id}: 조사 조각에는 효과 숫자를 두지 않는다(손 예측이 쓴다)")
        for c in d.get("claims", []):
            if c.get("on") not in ("up_odds", "move") or c.get("dir") not in ("up", "down") or not c.get("reason"):
                errs.append(f"[조사 조각] {node.id}: 주장은 on(up_odds/move)·dir(up/down)·reason이 있어야 함")
            scope = c.get("scope", "all")
            names = [scope] if isinstance(scope, str) else scope
            bad = [x for x in names if x != "all" and x not in C.TRAIT_LABEL]
            if bad:
                errs.append(f"[조사 조각] {node.id}: scope의 {bad}는 config.TRAIT_LABEL에 없음")
            if any(x not in SPANS for x in c.get("span", [])):
                errs.append(f"[조사 조각] {node.id}: span은 {list(SPANS)} 중에서")
    return errs


def _norm(effs: list[dict]) -> list[tuple]:
    return sorted((e.get("on"), e.get("op"), str(e.get("asset")), e.get("horizon"), tuple(float(x) for x in e.get("range", [])),
                   tuple(sorted((e.get("when") or {}).items())), e.get("dir")) for e in effs)


AUDIT = audit
