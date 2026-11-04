"""stock-rader 논리집의 자동 검사 (PROVISO 성적표·점검이 부른다: python -m market.logic check|audit).

PROVISO 분야 checks.py와 같은 모양: CHECKS(검사 이름 → 함수), SOURCE(판정 출처), SEEN_UNTIL, AUDIT(kn) → (오류, 경고).
판정은 시장 결과(채점 기록)로 한다. 가설은 **세운 뒤의 날들로만** 시험한다: 계산 조각은 예측의 기준 날짜가 조각의
seen_until 뒤인 것만, 조사 조각·손 예측 가설은 판단 날짜(run)가 seen_until 뒤인 것만.

출처를 검사마다 따로 낸다 (PROVISO 코어 2026-10-21 '검사마다 출처', 결정 4):
- records/replay/ (2026-07-01~10-19 재생, 홀드아웃) → 'replay' (실제 기록 재생)
- records/ (진짜 계좌, 클라우드 매일 실행) → 'measured' (실측)
같은 검사가 두 기록에 따로 돌아 성적표에 두 줄로 나온다.

채점은 같은 (예측기·대상·기간·기준 날짜)를 한 번만 센다(결정 1, scoring.first_only).

방향 시험(2026-11-04 고침, 분석 보고서 C2): 칸마다 조건 있음·없음이 각각 **5구간**(같은 값이 이어진 날들의 묶음) 이상이어야
센다 — 한 국면 19일이 관찰 19개로 세어져 하루 차이로 판정이 뒤집혔다. 동점(예 = 아니오)은 틀림이 아니라 세지 않는다(모름).
"""
from __future__ import annotations

import datetime as dt
import math
import random
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
              "place_official": C.RESEARCH_SINCE, "layer_vs_baselines": "2026-11-04"}
NEED = 0.8
# 방향 비교: 조건 있음·없음이 각각 이만큼의 '구간'(같은 값이 이어진 날들, GAP_DAYS일 넘게 벌어지거나 값이 바뀌거나 모르는 날이
# 끼면 새 구간)은 있어야 그 칸을 센다(2026-11-04, 전에는 '날 5개'). 상태는 수십 일씩 이어지므로 날을 세면 국면 하나가 관찰
# 수십 개로 불어난다. 5는 예전 날 문턱을 그대로 구간에 옮긴 것이다(기록에서 고르지 않았다).
MIN_SIDE_RUNS = 5
GAP_DAYS = 7      # 구간 끊김: 같은 값 사이가 이만큼(달력 날)보다 벌어지면 새 구간 — 주말·명절 휴장(5일 안팎)은 잇는다
MIN_PAIRS = 20    # 손 예측 vs 계산기: 같은 열쇠의 짝이 이만큼은 있어야 판정
# layer_vs_baselines (2026-11-04, R_layer_v0 6 '판정 시점'): 계산기를 동전·기준선과 견주는 판정은 표본이 이만큼 모일 때마다
# 한 번씩(겹치지 않는 묶음으로) 낸다. 1봉은 대상마다 서로 다른 기준 날짜 60개(묶음 약 3달, 코인 약 2달), 그 밖의 기간은 겹치지
# 않는 표본(기간마다 하나) 12개. 우연 범위는 20일 덩어리 부트스트랩 90% 구간(1,000번) — 이웃한 날끼리 같이 움직여서 덩어리로 뽑는다
LAYER_MIN_FIRST = 60
LAYER_MIN_SPACED = 12
BOOT_DAYS = 20
BOOT_REPS = 1000
COIN_BRIER = 0.25
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
# 씨앗 조각 상한 12 (2026-11-04): 조사 조각에 seed = true. 시장 조사 조각 상한 6에 세지 않고 따로 센다. 씨앗은 관련 있는 날만
# 대 보므로(안 물은 날은 '안 물음') 매일의 대 보기 부담이 없다 — 상한 6의 까닭(하루 대 보기가 엉성해짐)이 걸리지 않는다.
# 12는 분기(약 60 판단일)에 한 번 정리할 때 다룰 수 있는 수로 미리 정했다. 2차 조사가 찾은 까닭을 근거와 함께 씨앗으로 세워,
# 정식 자리(6)가 찼어도 시험이 시작되게 한다(2026-10-29~11-03 후보 열 개가 하나도 조각이 못 됐다).
MAX_SEED_PIECES = 12
# 은퇴 후보(2026-11-04): 정식 조사 조각이 세운 뒤 이만큼의 판단일(실행 날)이 지나도 시험 전이면(모든 칸이 한쪽 값뿐이거나
# 구간이 모자람) audit 경고. 30 = 약 6주 — 묶음 1봉 칸에서 양쪽 5구간이 찰 만한 날 수로 미리 정했다. 은퇴는 메인이 돌아보기에서 한다
RETIRE_AFTER_RUNS = 30
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


def _holds(readings: dict, claim: dict, state: str, known: dict | None = None) -> bool | None:
    """주장의 조건이 켜졌나: 상태 = when, 그리고 더한 조건(also)이 모두 맞음. 값을 모르면 None.
    known: 상태 → 예/아니오 말고도 셀 수 있는 답(상태의 answers에서 모름·일부를 뺀 것, 예: 오름/내림)."""
    need = {state: claim.get("when", "예"), **(claim.get("also") or {})}
    vals = {k: readings.get(k) for k in need}
    if any(v not in ("예", "아니오") and v not in (known or {}).get(k, ()) for k, v in vals.items()):
        return None
    return all(vals[k] == v for k, v in need.items())


def _compare(on: str, dirn: str, y: list[float], n: list[float]) -> tuple[bool | None, str]:
    """(주장대로인가 — 동점이면 None, 보일 글).
    dirn = "sign"(2026-11-04, 숫자 상태의 부호 쪽): 수익률은 부르는 쪽이 그날 숫자의 부호를 곱해 넘긴다(research_direction) —
    그러면 '오름' = 부호 쪽으로 감이다. 주장은 '예'인 날(숫자가 크다) 부호 쪽으로 간 비율이 '아니오'인 날보다 높다."""
    if on == "up_odds":
        a, b = sum(r > 0 for r in y) / len(y), sum(r > 0 for r in n) / len(n)
        shown = (f"부호 쪽으로 간 비율 예 {a:.2f} vs 아니오 {b:.2f}" if dirn == "sign"
                 else f"오른 비율 예 {a:.2f} vs 아니오 {b:.2f}")
    else:
        a = sorted(abs(r) for r in y)[len(y) // 2]
        b = sorted(abs(r) for r in n)[len(n) // 2]
        shown = f"움직임 가운데 값 예 {a * 100:.2f}% vs 아니오 {b * 100:.2f}%"
    if a == b:
        return None, shown
    return ((a > b) if dirn in ("up", "sign") else (a < b)), shown


def _runs(entries: list[tuple], side: bool) -> int:
    """entries: (날짜, 묶음, 값 True/False/None, 수익률). 값이 side인 구간 수. 묶음(대상)마다 날짜 순서로 보며, 값이 바뀌거나
    모르는 날(None)이 끼거나 같은 값 사이가 GAP_DAYS일보다 벌어지면 새 구간이다. 종목을 한 칸으로 묶은 칸은 종목마다 센다."""
    n = 0
    groups: dict = {}
    for d, g, s, _ in entries:
        groups.setdefault(g, []).append((d, s))
    for seq in groups.values():
        seq.sort(key=lambda x: x[0])
        prev_d, prev_s = None, None
        for d, s in seq:
            joined = (prev_s == side and prev_d is not None
                      and (dt.date.fromisoformat(d) - dt.date.fromisoformat(prev_d)).days <= GAP_DAYS)
            if s == side and not joined:
                n += 1
            prev_d, prev_s = d, s
    return n


def _verdict(cells: dict, what: str, cut: str) -> tuple[str, str]:
    """cells: (on, dir, 대상, 기간) → [(날짜, 묶음, 조건 켜짐 True/False/모름 None, 수익률)] 을 방향 시험 판정으로.
    양쪽 MIN_SIDE_RUNS구간 미만이면 안 센다. 동점은 세지 않는다(모름)."""
    lines, verdicts, ties = [], [], 0
    for (on, dirn, asset, h), entries in sorted(cells.items()):
        y = [r for _, _, s, r in entries if s is True]
        n = [r for _, _, s, r in entries if s is False]
        if not y and not n:
            continue
        ry, rn = _runs(entries, True), _runs(entries, False)
        sides = f"예 {len(y)}일 {ry}구간, 아니오 {len(n)}일 {rn}구간"
        if ry < MIN_SIDE_RUNS or rn < MIN_SIDE_RUNS:
            lines.append(f"    {on} {asset}:{h} {sides} — 구간이 모자라 안 셈(양쪽 {MIN_SIDE_RUNS}구간 필요)")
            continue
        ok, shown = _compare(on, dirn, y, n)
        if ok is None:
            ties += 1
            lines.append(f"    {on} {asset}:{h} {shown} → 동점 — 세지 않음(모름) ({sides})")
            continue
        verdicts.append(ok)
        lines.append(f"    {on} {asset}:{h} {shown} → {'맞음' if ok else '틀림 ✗'} ({sides})")
    detail = f"  시험({cut} 뒤 {what}):\n" + ("\n".join(lines) or "    없음")
    if not verdicts:
        tie = f", 동점 {ties}칸" if ties else ""
        return f"시험 전 (세운 뒤 기록에 조건 있음·없음이 양쪽 {MIN_SIDE_RUNS}구간씩 찬 칸이 아직 없음{tie})", detail
    hits = sum(verdicts)
    word = "지지됨" if hits / len(verdicts) >= NEED else "반박됨"
    tie = f", 동점이라 안 셈 {ties}" if ties else ""
    return f"{word} (방향 시험 {len(verdicts)}건 중 {hits}건 맞음{tie})", detail


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
                cells.setdefault((c["on"], c.get("dir"), p["asset"], p["horizon"]), []).append(
                    (p["base_date"], p["asset"], on, s["ret"]))
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


def account_vs_equal(node_id: str | None = None):
    """R_weights: 계산기를 따른 계좌가 셋을 나눠 사 두기보다 나은가 (세운 뒤의 날들). 재생·실측 따로.
    세운 뒤 첫 계좌 기록(그날 07:37 평가 — 그 규칙의 첫 주문이 체결되기 전)에서 마지막 기록까지 늘어난 비율을 견준다.
    규칙에 seen_until이 없으면 SEEN_UNTIL(2026-06-30) — 두 계좌가 같은 시작 금액에서 출발하므로 마지막 평가액 비교와 같다
    (R_weights v1의 판정은 예전 그대로). 2026-11-04: 이어지는 한 계좌를 새 버전(R_weights_v2)이 물려받아도 그 버전의 몫만 보이게."""
    node = _node(node_id) if node_id else None
    cut = str((node or {}).get("seen_until") or SEEN_UNTIL["account_vs_equal"])
    out = []
    for (src, folder), mine_id in zip(_folders(), ("replay:calc", "live:calc")):
        accs = [a for a in R.read(folder, "account") if a["run"] > cut]
        mine = [a for a in accs if a["account"] == mine_id]
        eq = [a for a in accs if a["account"] == "baseline:equal_hold"]
        # 판정할 수 있나: 두 계좌 모두 세운 뒤 기록이 둘 이상이거나, 첫 기록이 둘 다 시작 금액(계좌 시작일 — 예전 셈과 같다)
        ready = bool(mine and eq) and ((len(mine) > 1 and len(eq) > 1)
                                       or mine[0]["value"] == eq[0]["value"] == C.START_CASH)
        if ready:
            m, e = mine[-1]["value"] / mine[0]["value"], eq[-1]["value"] / eq[0]["value"]
            detail = (f"  {mine[0]['run']}~{mine[-1]['run']}: 계산기 계좌 {mine[-1]['value']:,.0f}원({m - 1:+.2%}) vs "
                      f"나눠 사 두기 {eq[-1]['value']:,.0f}원({e - 1:+.2%})")
            out.append((src, f"{'지지됨' if m >= e else '반박됨'} (시험 1건 중 {1 if m >= e else 0}건 맞음)", detail))
        elif src == "replay" and not mine:
            continue  # 세운 날이 재생 뒤면 재생 줄은 없다
        else:
            out.append((src, f"시험 전 ({cut} 뒤 계좌 기록이 모자람)", ""))
    return out


# ---------- 조사 조각과 손 예측 (진짜 계좌만) ----------
POOLED = "종목 묶어 봄"  # 종목마다 대 보는 조각의 칸 이름 (대상 칸 대신)


def research_direction(node_id: str) -> tuple[str, str]:
    """조사 조각의 방향 주장이 세운 뒤의 판단에서 맞나: 그날 손 대 보기 값이 '예'인 날과 '아니오'인 날의 결과를 비교.
    결과(수익률)는 그날 계산기 예측의 채점에서 가져온다(대상·기간마다 하나, 같은 기준 날짜는 한 번). 범위 밖 대상은 뺀다.
    종목마다 대 보는 조각(조건 상태 per_asset)은 종목을 한 칸으로 묶는다(2026-11-03): 주장이 '어떤 회사든'에 대한 것이고,
    한 종목은 실적이 분기에 한 번이라 종목마다 칸을 나누면 '예'·'아니오'가 다섯씩 모이지 않는다. 같은 날 '예' 종목과
    '아니오' 종목(비교군, 지시문 '종목마다 묻는 질문')을 견준다.
    예/아니오가 아니라 세지 않은 값(일부·모름·안 물음)은 세부에 수를 보인다 — '시험 전'의 까닭이 보이게.
    주장의 dir = "sign"(2026-11-04): 조건 상태가 숫자 상태면 그날 손 대 보기에 남은 숫자(hand_reading.numbers)의 부호를 수익률에
    곱한다 — '예'인 날과 '아니오'인 날 모두 '부호 쪽으로 갔나'를 견준다. 부호가 없는 날(숫자 0·없음)은 세지 않는다."""
    node = _node(node_id)
    if not node:
        return "불명 (노드 없음)", ""
    cut = node.get("seen_until", C.RESEARCH_SINCE)
    state = (node.get("conditions") or [None])[0]
    claims = node.get("claims", [])
    raw = _raw()
    pooled = any(s.get("id") == state and s.get("per_asset") for s in raw.get("state", []))
    known = {s["id"]: set(s.get("answers", [])) - {"모름", "일부"} for s in raw.get("state", []) if s.get("answers")}
    spec = RS.numeric_states(raw).get(state)
    hrs = {h["run"]: h for h in R.read(C.RECORDS, "hand_reading")}
    cells: dict = {}
    skipped: dict = {}
    for p, s in _scored(C.RECORDS, lambda p: p["predictor"] == "calc" and p["run"] > cut):
        h = hrs.get(p["run"])
        if h is None:
            continue
        # 종목마다 대 본 값(by_asset)은 그 종목에만 (2026-10-29). 종목의 결과는 그날 종목 계산기('모름') 줄의 채점에서 온다
        by = (h.get("by_asset") or {}).get(p["asset"], {})
        vals = {**h["values"], **{k: v["value"] if isinstance(v, dict) else v for k, v in by.items()}}
        nums = (by.get(state) or {}).get("numbers") if isinstance(by.get(state), dict) else None
        nums = nums if isinstance(nums, dict) else (h.get("numbers") or {}).get(state, {})
        hz = C.ASSETS[p["asset"]].horizons.index(p["horizon"])
        for c in claims:
            if not C.in_scope(p["asset"], c.get("scope")):
                continue
            if c.get("span") and hz not in [SPANS[x] for x in c["span"]]:
                continue
            on = _holds(vals, c, state, known)
            if on is None:
                for k in dict.fromkeys([state, *(c.get("also") or {})]):
                    v = vals.get(k, "안 물음")
                    if v not in ("예", "아니오") and v not in known.get(k, ()):
                        skipped[f"{k}={v}"] = skipped.get(f"{k}={v}", 0) + 1
            ret = s["ret"]
            if c.get("dir") == "sign":  # 숫자의 부호 쪽으로 갔나: 수익률 × 부호
                x = RS.number_of(spec, nums) if spec else None
                sign = (x > 0) - (x < 0) if x is not None else 0
                if on is not None and not sign:
                    skipped[f"{state}=부호 없음"] = skipped.get(f"{state}=부호 없음", 0) + 1
                    on = None
                ret = ret * sign
            where = POOLED if pooled else p["asset"]
            cells.setdefault((c["on"], c.get("dir"), where, p["horizon"]), []).append(
                (p["base_date"], p["asset"], on, ret))
    verdict, detail = _verdict(cells, "판단의 손 대 보기", cut)
    if skipped:
        detail += "\n    세지 않은 채점(예/아니오가 아닌 값): " + ", ".join(f"{k} {n}" for k, n in sorted(skipped.items()))
    return verdict, detail


def _info_cells() -> dict[str, set[str]]:
    """판단 날짜 → 판단 때 가격을 적은 대상(run 기록 steps.decision_price). 그 대상의 첫 기간(1봉)은 판단 때 이미 결과의 일부가
    보인다(분석 보고서 A6: 코인 1봉은 목표 봉이 판단 뒤 평균 82분이면 끝났다)."""
    out = {}
    for r in R.read(C.RECORDS, "run"):
        dp = (r.get("steps") or {}).get("decision_price") or {}
        out[r["run"]] = {a for a, v in dp.items() if isinstance(v, dict) and v.get("price")}
    return out


def hand_vs_calc() -> tuple[str, str]:
    """H_hand_vs_calc: 같은 (대상·기간·기준 날짜)의 손 예측과 계산기 예측을 짝지어 확률 점수 평균을 비교.
    판정 규칙은 세울 때 그대로다(사후에 시험 규칙을 바꾸지 않는다). 2026-11-04부터 세부에 대상·기간별 확률 점수와
    '판단 때 정보가 있는 칸(판단 때 가격을 적는 대상의 1봉)을 뺀 값'을 같이 보인다(분석 보고서 C4: 지지가 그 칸에서 나왔다)."""
    cut = SEEN_UNTIL["hand_vs_calc"]
    pairs = _scored(C.RECORDS, lambda p: p["predictor"] in ("hand", "calc") and p["run"] > cut)
    info = _info_cells()
    by: dict = {}
    for p, s in pairs:
        if s["brier"] is not None:
            v = by.setdefault((p["asset"], p["horizon"], p["base_date"]), {})
            v[p["predictor"]] = s["brier"]
            if p["predictor"] == "hand":
                v["info"] = p["asset"] in info.get(p["run"], set()) and p["horizon"] == C.ASSETS[p["asset"]].horizons[0]
    keyed = [(k, v) for k, v in by.items() if "hand" in v and "calc" in v]
    both = [(v["hand"], v["calc"]) for _, v in keyed]
    if len(both) < MIN_PAIRS:
        return f"시험 전 (짝 {len(both)}개 < {MIN_PAIRS})", ""
    h = sum(a for a, _ in both) / len(both)
    c = sum(b for _, b in both) / len(both)
    wins = sum(a < b for a, b in both)
    detail = f"  짝 {len(both)}개: 손 예측 확률 점수 {h:.4f} vs 계산기 {c:.4f} (손이 더 낮은 짝 {wins})"
    rest = [(v["hand"], v["calc"]) for _, v in keyed if not v.get("info")]
    if rest and len(rest) < len(both):
        detail += (f"\n  판단 때 정보가 있는 칸(판단 때 가격을 적는 대상의 1봉) {len(both) - len(rest)}짝을 뺀 {len(rest)}짝: "
                   f"손 {sum(a for a, _ in rest) / len(rest):.4f} vs 계산기 {sum(b for _, b in rest) / len(rest):.4f}"
                   f" (손이 더 낮은 짝 {sum(a < b for a, b in rest)})")
    cells: dict = {}
    for (a, hz, _), v in keyed:
        cells.setdefault((a, hz), []).append((v["hand"], v["calc"]))
    parts = [f"{a}:{hz} {len(xs)}짝 손 {sum(x for x, _ in xs) / len(xs):.3f}·계산기 {sum(y for _, y in xs) / len(xs):.3f}"
             for (a, hz), xs in sorted(cells.items())]
    detail += "\n  대상·기간별: " + "; ".join(parts)
    return f"{'지지됨' if h < c else '반박됨'} (시험 1건 중 {1 if h < c else 0}건 맞음)", detail


def _block_boot(diffs: list[float], block: int, seed: str) -> tuple[float, float]:
    """덩어리 부트스트랩 90% 구간: 이어진 block개씩 덩어리로 다시 뽑아(이웃한 날끼리 같이 움직이는 몫을 살린다) 평균을
    BOOT_REPS번 구하고 5%·95% 지점. 씨앗을 문자열로 고정해 같은 기록이면 같은 구간이 나온다."""
    rng = random.Random(seed)
    n = len(diffs)
    block = max(1, min(block, n))
    means = []
    for _ in range(BOOT_REPS):
        got: list[float] = []
        while len(got) < n:
            i = rng.randrange(0, n - block + 1)
            got.extend(diffs[i:i + block])
        means.append(sum(got[:n]) / n)
    means.sort()
    return means[int(0.05 * BOOT_REPS)], means[int(0.95 * BOOT_REPS) - 1]


def layer_vs_baselines(node_id: str) -> tuple[str, str]:
    """계산기(층 구조, R_layer_v0)가 동전(0.25)·기준선(최근 250봉 baseline:base_rate, 알려진 전체 baseline:all)보다 확률 점수가
    낮은가. 노드의 seen_until 뒤 기준 날짜의 계산기 예측(한 번만 세기)만, 대상·기간마다. 출발점 조각(주장 op = "start")에
    걸면 그 층이 답한 칸(layer = 출발점)만 본다. 판정(R_layer_v0 6, 미리 적음): 1봉은 대상마다 기준 날짜 LAYER_MIN_FIRST개,
    그 밖은 겹치지 않는 표본(기간마다 하나) LAYER_MIN_SPACED개가 모일 때마다 한 묶음으로 한 번 — '계산기 − 동전'의 덩어리
    부트스트랩 90% 구간이 0 아래면 지지, 위면 반박, 걸치면 모름. 칸의 판정은 마지막 묶음의 것. 모이기 전엔 시험 전."""
    node = _node(node_id)
    if not node:
        return "불명 (노드 없음)", ""
    cut = str(node.get("seen_until", "9999"))
    start_only = any(c.get("op") == "start" for c in node.get("claims", []))
    names = ("calc", "hand", "baseline:base_rate", "baseline:all")
    pairs = _scored(C.RECORDS, lambda p: p["predictor"] in names and p["base_date"] > cut)
    by: dict = {}
    for p, s in pairs:
        if s["brier"] is None:
            continue
        if p["predictor"] == "calc" and start_only and p.get("layer") != "출발점":
            continue
        by.setdefault((p["asset"], p["horizon"], p["base_date"]), {})[p["predictor"]] = s["brier"]
    cells: dict = {}
    for (a, hz, d), v in by.items():
        if "calc" in v:
            cells.setdefault((a, hz), []).append((d, v))
    lines, verdicts = [], []
    for (a, hz), rows in sorted(cells.items()):
        rows.sort(key=lambda x: x[0])
        first = hz == C.ASSETS[a].horizons[0]
        sample = rows if first else rows[::hz]   # 겹치지 않는 표본: 기준 날짜 기간마다 하나
        need = LAYER_MIN_FIRST if first else LAYER_MIN_SPACED

        def mean(k: str) -> str:
            xs = [v[k] for _, v in sample if k in v]
            return f"{sum(xs) / len(xs):.4f}" + (f"({len(xs)})" if len(xs) != len(sample) else "") if xs else "–"
        shown = (f"확률 점수 계산기 {mean('calc')} / 동전 {COIN_BRIER:.4f} / 최근 250봉 {mean('baseline:base_rate')}"
                 f" / 전체 {mean('baseline:all')} / 손 {mean('hand')}")
        count = f"기준 날짜 {len(rows)}개" + ("" if first else f"(겹치지 않는 표본 {len(sample)}개)")
        rounds = len(sample) // need
        if not rounds:
            lines.append(f"    {a}:{hz} {count} — 시험 전({need}개 필요). {shown}")
            continue
        got = []
        for k in range(rounds):
            part = sample[k * need:(k + 1) * need]
            diffs = [v["calc"] - COIN_BRIER for _, v in part]
            lo, hi = _block_boot(diffs, math.ceil(BOOT_DAYS / hz), f"{node_id}:{a}:{hz}:{k}")
            word = "지지" if hi < 0 else "반박" if lo > 0 else "모름"
            got.append(f"{k + 1}번째({part[0][0]}~{part[-1][0]}) 계산기−동전 {sum(diffs) / len(diffs):+.4f} "
                       f"[90% {lo:+.4f}, {hi:+.4f}] → {word}")
        verdicts.append(word)
        lines.append(f"    {a}:{hz} {count}: " + "; ".join(got) + f". {shown}")
    detail = (f"  시험({cut} 뒤 기준 날짜의 계산기 예측{', 출발점 층이 답한 칸만' if start_only else ''}; "
              f"동전 = 늘 50%):\n" + ("\n".join(lines) or "    없음"))
    if not verdicts:
        return (f"시험 전 (판정 문턱에 닿은 칸 없음 — 1봉은 대상마다 기준 날짜 {LAYER_MIN_FIRST}개, 그 밖은 겹치지 않는 "
                f"표본 {LAYER_MIN_SPACED}개)"), detail
    yes, no, unk = verdicts.count("지지"), verdicts.count("반박"), verdicts.count("모름")
    text = f"판정 {len(verdicts)}건 중 {yes}건 지지 — 반박 {no}, 모름 {unk}"
    word = ("지지됨" if yes and not no else "반박됨" if no and not yes else "일부" if yes and no else "모름")
    return f"{word} ({text})", detail


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
          "research_direction": research_direction, "hand_vs_calc": hand_vs_calc, "place_official": place_official,
          "layer_vs_baselines": layer_vs_baselines}


# ---------- 점검 (audit의 분야 점검) ----------
def _names_in(text: str) -> list[str]:
    words = C.ASSET_WORDS + ST.name_words()  # 종목 이름·코드도 조각 문장에 쓰지 않는다(2026-10-29)
    return [w for w in words if re.search(re.escape(w), text, re.IGNORECASE)]


def is_research(node) -> bool:
    return node.data.get("read_by") == "조사"


def fitted_claims(node) -> list[dict]:
    """기록에서 숫자를 맞추는 주장: op base·mul이고 고정값(fixed)이 아닌 것. 출발점(op start)·고정값 주장은 숫자를 맞추지 않는다."""
    return [c for c in node.data.get("claims", []) if c.get("op", "mul") in ("base", "mul") and not c.get("fixed")]


def audit(kn, want: dict[str, list[dict]] | None = None) -> tuple[list[str], list[str]]:
    """오류: 조각 수 상한(계산·조사·종목·씨앗 따로), 조각 이름·문장의 대상 이름, 기록에서 맞춘 값과 다른 효과(손으로 넣은 숫자;
    고정값은 주장에 적은 값과 견준다), 효과 칸 모양, 고정값·출발점 주장 모양, 조사 조각 모양(조건 상태·범위·기간·세운 날),
    씨앗의 근거 강도, 버전 목록, 볼 곳 목록.
    경고: 세운 효과가 하나도 없는 계산 조각, 세운 뒤 기록에서 방향이 틀린 조각(돌아보기 대상), 은퇴 후보(정식 조사 조각이
    세운 뒤 RETIRE_AFTER_RUNS 판단일이 지나도 시험 전).
    want = 맞추기를 다시 돌린 결과(조각 id → 효과 목록). 없으면 지금 시세로 다시 맞춘다(테스트는 직접 넘긴다)."""
    errors, warns = [], []
    alive = [n for n in kn.claims(alive_only=True) if n.data.get("claims")]
    live = [n for n in alive if not is_research(n)]
    research = [n for n in alive if is_research(n)]
    seeds = [n for n in research if n.data.get("seed")]
    research = [n for n in research if n not in seeds]
    per_asset = {s.id for s in kn.of("state") if s.data.get("per_asset")}
    stock_pieces = [n for n in research if (n.refs("conditions") or [None])[0] in per_asset]
    research = [n for n in research if n not in stock_pieces]
    if len(stock_pieces) > MAX_STOCK_PIECES:
        errors.append(f"[종목 조각] 살아 있는 종목 조각 {len(stock_pieces)}개가 상한 {MAX_STOCK_PIECES}개를 넘음")
    if len(seeds) > MAX_SEED_PIECES:
        errors.append(f"[씨앗 조각] 살아 있는 씨앗 조각 {len(seeds)}개가 상한 {MAX_SEED_PIECES}개를 넘음 — 시험이 시작된 것은 "
                      f"정식으로 올리고, 오래 시험 전인 것은 은퇴시킨다")
    for n in seeds:
        if n.data.get("strength") != "낮음":
            errors.append(f"[씨앗 조각] {n.id}: 씨앗은 근거 강도 strength = \"낮음\"으로 세운다(정식으로 올릴 때 다시 정한다)")
    for n in stock_pieces + [s for s in seeds if (s.refs("conditions") or [None])[0] in per_asset]:
        for c in n.data.get("claims", []):
            if c.get("scope") != "single":
                errors.append(f"[종목 조각] {n.id}: 종목마다 대 보는 조각의 주장은 scope = \"single\"(한 회사 주식)이어야 함")
    # 계산 조각 상한은 기록에서 숫자를 맞추는 조각만 센다(2026-11-04): 상한의 까닭은 '맞춘 숫자가 많으면 외워서 맞추는 쪽'이라,
    # 숫자를 맞추지 않는 미리 정한 식(출발점 조각)은 그 위험이 없다
    counted = [n for n in live if fitted_claims(n)]
    if len(counted) > MAX_PIECES:
        errors.append(f"[계산 조각] 살아 있는 조각 {len(counted)}개가 상한 {MAX_PIECES}개를 넘음 — 외워서 맞추는 쪽이다")
    for node in live:
        for c in node.data.get("claims", []):
            if c.get("fixed") and not (c.get("op") == "start" or (
                    c.get("op") == "base" and isinstance(c.get("range"), list) and len(c["range"]) == 2
                    and 0 < float(c["range"][0]) <= float(c["range"][1]))):
                errors.append(f"[계산 조각] {node.id}: 고정값 주장(fixed)은 op = \"base\"와 range([하한, 상한])가 있어야 함")
            if c.get("op") == "start" and (c.get("on") != "up_odds" or not c.get("reason")):
                errors.append(f"[계산 조각] {node.id}: 출발점 주장(op = \"start\")은 on = \"up_odds\"와 reason이 있어야 함")
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
        if not node.data.get("effect") and fitted_claims(node):
            warns.append(f"[계산 조각] {node.id}: 세운 효과가 하나도 없음 — 모든 대상·기간에서 맞춘 시세가 방향을 받쳐 주지 않았다(not_set)")
    errors += _research_errors(kn, research + stock_pieces + seeds)
    errors += _number_errors(kn)
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
    live_runs = [r["run"] for r in R.read(C.RECORDS, "run") if r.get("mode") == "live"]
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
                if name == "research_direction" and not node.data.get("seed") and verdict.startswith("시험 전"):
                    days = sum(1 for r in live_runs if r > str(node.data.get("seen_until", "")))
                    if days >= RETIRE_AFTER_RUNS:
                        warns.append(f"[조사 조각] {node.id}: 은퇴 후보 — 세운 뒤 판단 {days}일이 지나도 시험 전(모든 칸이 한쪽 값뿐"
                                     f"이거나 양쪽 {MIN_SIDE_RUNS}구간이 안 참). 돌아보기에서 은퇴(retired + 까닭)를 정한다 — 자리가 "
                                     f"비면 시험이 시작된 씨앗을 올린다")
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
            if c.get("on") not in ("up_odds", "move") or c.get("dir") not in ("up", "down", "sign") or not c.get("reason"):
                errs.append(f"[조사 조각] {node.id}: 주장은 on(up_odds/move)·dir(up/down/sign)·reason이 있어야 함")
            if c.get("dir") == "sign" and (c.get("on") != "up_odds" or st is None or not isinstance(st.data.get("number"), dict)):
                errs.append(f"[조사 조각] {node.id}: dir = \"sign\"(숫자의 부호 쪽)은 on = \"up_odds\"이고 첫 조건이 숫자 상태"
                            f"(number 칸)일 때만 — 부호를 읽을 숫자가 있어야 한다")
            scope = c.get("scope", "all")
            names = [scope] if isinstance(scope, str) else scope
            bad = [x for x in names if x != "all" and x not in C.TRAIT_LABEL]
            if bad:
                errs.append(f"[조사 조각] {node.id}: scope의 {bad}는 config.TRAIT_LABEL에 없음")
            if any(x not in SPANS for x in c.get("span", [])):
                errs.append(f"[조사 조각] {node.id}: span은 {list(SPANS)} 중에서")
    return errs


def _number_errors(kn) -> list[str]:
    """숫자 상태(2026-11-04, 상태에 number = { from, per?, rule, unit }): 이름 모양(records.NUMBER_KEY), rule 문법(research.
    parse_rule), 단위 글, 답(answers)은 예/아니오/모름 안에서, 조사로 대 보는 상태(read_by = "조사")."""
    errs = []
    for st in kn.of("state"):
        spec = st.data.get("number")
        if spec is None:
            continue
        if not isinstance(spec, dict):
            errs.append(f"[숫자 상태] {st.id}: number는 {{ from, rule, unit }} 표여야 함")
            continue
        for k in ("from", "per"):
            if (k == "from" or spec.get(k)) and not R.NUMBER_KEY.match(str(spec.get(k, ""))):
                errs.append(f"[숫자 상태] {st.id}: number.{k} '{spec.get(k)}'는 숫자 이름(이름_단위, records.NUMBER_KEY)이어야 함")
        try:
            RS.parse_rule(spec.get("rule", ""))
        except ValueError as exc:
            errs.append(f"[숫자 상태] {st.id}: {exc}")
        if not str(spec.get("unit", "")).strip():
            errs.append(f"[숫자 상태] {st.id}: number.unit(값의 단위·뜻)이 없음")
        if set(st.data.get("answers", ["예", "아니오", "모름"])) - {"예", "아니오", "모름"}:
            errs.append(f"[숫자 상태] {st.id}: 숫자 상태의 답은 예/아니오/모름 — 코드가 rule로 정한다")
        if st.data.get("read_by") != "조사":
            errs.append(f"[숫자 상태] {st.id}: 숫자 상태는 조사 답의 숫자로 정한다(read_by = \"조사\")")
    return errs


def _norm(effs: list[dict]) -> list[tuple]:
    """효과 비교 열쇠. 고정값(fixed) 효과는 맞추지 않으므로 '맞춘 값'이 아니라 주장에 적은 고정값과 같아야 한다(fit.expected가
    주장의 range를 그대로 낸다) — 고정 표시까지 같이 본다."""
    return sorted((e.get("on"), e.get("op"), str(e.get("asset")), e.get("horizon"), tuple(float(x) for x in e.get("range", [])),
                   tuple(sorted((e.get("when") or {}).items())), e.get("dir"), e.get("scale") or "", bool(e.get("fixed")))
                  for e in effs)


AUDIT = audit
