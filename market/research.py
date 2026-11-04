"""조사: 볼 곳 목록, 오늘의 조사 질문, 볼 곳 성적표 (조사 범위도 논리집에 — 사용자 제안 2026-10-20).

사용법 (저장소 루트):
  python -m market.research places              볼 곳마다 성적 (records/의 조사 기록에서 계산)
  python -m market.research questions [날짜]     그날 조사 질문 (살아 있는 조사 조각에서 나온다)
  python -m market.research numeric <input.json> 그 입력으로 코드가 정할 숫자 상태 값 미리 보기(기록하지 않음)

- 조사 질문은 논리집이 정한다: 살아 있는 조사 조각(read_by = "조사")의 조건 상태마다 질문 하나(상태의 ask),
  그리고 각도마다 '오늘 판단용' 각도 질문 하나(ANGLE_ASK, 2026-11-04 사용자 "조사는 빠른 정보를 써야 한다 — 과거로부터
  공부하는 거 아니면 지금 당장의 판단을 위해 도움되는 조사로"). 각도 넷: 간밤 반응(숫자) / 일정·예상치(오늘·내일 일정과 시장
  예상치, 이미 나왔으면 놀람) / 수급(전일 매매 주체 숫자) / 공시·뉴스(든 종목·후보·대상의 밤사이 소식, 사실 한 줄 + 숫자).
  '최근 1주 왜 움직였나' 같은 사후 설명 질문은 묻지 않는다. 2차 조사도 같은 네 각도로 대상 하나를 깊이 판다(deep_question).
- 모든 조사 답에 numbers(숫자 이름_단위 → 값, 이름은 records.NUMBER_KEY)와 as_of(정보 시각)를 적는다 — FAST_SINCE부터
  hand.validate가 요구한다. 각도마다 적을 숫자 이름은 NUMBER_KEYS(같은 뜻은 날마다 같은 이름으로).
- 숫자 상태(2026-11-04, 상태에 number = { from, per?, rule, unit }): 손 대 보기 값을 메인이 아니라 코드가 숫자로 정한다
  (number_value — numbers[from](÷ numbers[per])를 rule로 견줘 예/아니오, 숫자가 없으면 모름). rule은 작은 문법만
  ('> 1', '<= -0.5', 'abs > 1' — parse_rule, eval을 쓰지 않는다). 질문 앞에 NUMBER_MARK가 붙는다.
- 씨앗 조각(2026-11-04, 조각에 seed = true): 씨앗만 쓰는 상태는 '오늘 물을지 메인이 정하는 질문'이다(seed_states).
  관련 있는 날만 묻고 대 본다. 안 물은 날은 손 대 보기에 값이 없고, 성적표는 '안 물음'으로 센다. 숫자 씨앗 상태는
  숫자가 있는 날(그날 조사 numbers에 그 이름이 있는 날)이 곧 관련 있는 날이다 — 코드가 값을 채운다.
- 볼 곳 성적(코드가 기록에서 계산): 다룬 질문 수, 본 횟수, 찾음/없음/늦음/막힘, 새로 시도한 횟수,
  그곳 정보로 한 대 보기를 나중에 다시 봤을 때 맞은 비율(reading_check), 판단 뒤에야 나와 늦었던 횟수(늦음 + reading_check.late).
- 새 곳 시도 몫: 각도마다 하루 한 곳 이상은 목록에 없거나 최근 14일 안 본 곳을 시도한다(쓰던 곳만 보면 그곳이 좋아서인지
  거기만 봐서인지 모른다 — PROVISO '사용 습관과 능력 구분'). 못 하면 경고로 남는다.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

from . import config as C
from . import records as R

# 조사 각도 (2026-11-04 전면 개편, 결정 E4): 시장별(금리·환율·정책 / 한국 / 미국 / 코인)에서 '오늘 판단에 쓰는 정보의 종류'로.
# 까닭: 아침 판단에 쓸 것은 판단 직전까지 새로 나온 숫자다 — 간밤 반응, 오늘·내일 일정과 예상치, 전일 수급, 밤사이 소식.
ANGLES = ("간밤 반응", "일정·예상치", "수급", "공시·뉴스")
FAST_SINCE = "2026-11-05"  # 이 판단 날짜부터 모든 조사 답에 numbers·as_of가 있어야 한다(hand.validate)
# 각도 질문 (state 없음, 질문 id q:<날짜>:now:<각도>). 300자(records.SUMMARY_MAX) 안. 숫자 이름은 NUMBER_KEYS
ANGLE_ASK = {
    "간밤 반응": "[간밤 반응] 국내 장 마감(15:30) 뒤 판단 직전까지 숫자: 미국 지수(대형주·기술주)와 지수 선물, 해외 상장 한국 "
               "묶음·한국 지수 야간 선물, 달러-원, 유가, 코인 24시간, 미국 공포 지수 — 각각 등락 %와 값의 시각(KST). "
               "평소 하루 움직임(최근 60거래일 하루 변화의 표준편차) 대비 배수도. numbers에 이름_단위로",
    "일정·예상치": "[일정·예상치] 판단 시각부터 다음 거래일 장 마감까지의 일정(경제 지표·금리 결정·지수 큰 몫 종목 실적·만기): "
                "KST 시각, 시장 예상치(전망 평균과 최저~최고 범위), 이전치. 판단 전 24시간 안에 이미 나온 것은 발표치와 놀람"
                "(발표 − 예상)을 numbers에",
    "수급": "[수급] 전일 숫자: 국내 주식시장 외국인·기관·개인 순매수(억 원)와 외국인 순매수의 최근 20거래일 표준편차, 든 종목·"
           "후보의 외국인·기관 순매수, 미국 현물 코인 상품 순유입(백만 달러), 코인 무기한 선물 자금조달률(%)·거래소 순입출금",
    "공시·뉴스": "[공시·뉴스] 전일 장 마감 뒤 판단 직전까지 든 종목·살 후보·세 대상에 걸린 공시·뉴스: 사실 한 줄 + 숫자(금액·%·"
              "시각). 오늘 판단을 바꿀 새 일이면 지금 조각 목록에 없는 판단이 필요한지도 적는다",
}
# 각도마다 적을 숫자 이름(numbers 키; 끝 낱말이 단위 — records.NUMBER_UNITS). 같은 뜻은 날마다 같은 이름으로 적어야 견줄 수
# 있다. 이 목록은 안내다: 목록에 없는 숫자도 이름 모양(records.NUMBER_KEY)만 맞으면 적는다. 숫자 상태의 from·per는 여기 있어야 한다
NUMBER_KEYS: dict[str, dict[str, str]] = {
    "간밤 반응": {
        "kr_overnight_pct": "국내 장 마감 뒤 판단 직전까지 한국 지수 야간 선물(없으면 해외 상장 한국 묶음)의 등락 %",
        "kr_overnight_sd60_pct": "국내 묶음의 평소 하루 움직임 %(최근 60거래일 하루 변화의 표준편차) — 틀에 코드가 시세로 미리 채운다",
        "us_large_pct": "미국 대형주 지수의 전일 정규장 등락 %",
        "us_tech_pct": "미국 기술주 지수의 전일 정규장 등락 %",
        "us_large_fut_pct": "미국 대형주 지수 선물의 판단 직전 등락 %(정규장 마감 대비)",
        "usdkrw_krw": "달러-원 판단 직전 값(원)",
        "usdkrw_pct": "달러-원 전일 국내 마감 대비 등락 %",
        "oil_pct": "유가(서부 텍사스유) 전일 등락 %",
        "coin_24h_pct": "코인 원화 값 24시간 등락 %",
        "fear_us_pt": "미국 주식 공포 지수 판단 직전 마지막 값",
    },
    "일정·예상치": {
        "surprise_z": "판단 전 24시간 안에 나온 큰 지표 중 놀람이 가장 큰 것: (발표 − 전망 평균) ÷ 전망 범위 반폭(최고−최저의 절반). "
                      "부호는 '금리를 덜 올리는 쪽 = +'(물가·성장·고용이 예상보다 낮거나 약하면 +)",
        "events_n": "판단 시각부터 다음 거래일 장 마감까지 큰 일정 수",
        "rate_hike_prob_pct": "다음 미국 금리 결정의 인상 확률 %",
    },
    "수급": {
        "kr_foreign_net_krw100m": "전일 국내 주식시장(유가증권시장) 외국인 순매수(억 원, 팔면 −)",
        "kr_foreign_sd20_krw100m": "외국인 순매수의 최근 20거래일 표준편차(억 원)",
        "kr_inst_net_krw100m": "전일 기관 순매수(억 원)",
        "kr_indiv_net_krw100m": "전일 개인 순매수(억 원)",
        "coin_fund_flow_usdm": "미국 현물 코인 상품 전일 순유입(백만 달러)",
        "coin_funding_pct": "코인 무기한 선물 자금조달률(%, 8시간)",
        "coin_exch_netflow_btc": "거래소 코인 순입금(개수, 나가면 −)",
    },
    "공시·뉴스": {
        "<종목코드>_<무엇>_<단위>": "예: 000660_op_vs_est_pct(영업이익이 전망 평균보다 몇 % 높았나), 000660_buyback_krw100m",
    },
}
NUMBER_MARK = "[숫자: 코드가 numbers로 값을 정한다] "
NEW_PER_ANGLE = 1        # 각도마다 하루에 새로 시도할 곳 수 (최소)
FRESH_DAYS = 14          # 이만큼 안 본 곳은 다시 봐도 '새 곳'으로 친다
SUGGEST = 3              # 질문마다 추천하는 볼 곳 수 (성적 순)
CHECK_AFTER_DAYS = 7     # 손 대 보기를 이만큼 지난 뒤 다시 본다(reading_check)
# 씨앗 상태 질문 앞 표시: 오늘 물을지 메인이 정한다. 안 물으면 이 질문 줄을 research에서 지우고 readings에도 넣지 않는다(안 물음)
SEED_MARK = "[씨앗: 관련 있는 날만 — 안 물으면 이 줄을 지운다] "


# ---------- 볼 곳 ----------
def load_places(path: Path | None = None) -> list[dict]:
    path = path or C.PLACES
    if not path.exists():
        return []
    return tomllib.loads(path.read_text(encoding="utf-8")).get("place", [])


def check_places(places: list[dict], state_ids: set[str] | None = None) -> list[str]:
    errs, seen = [], set()
    for p in places:
        pid = p.get("id", "")
        if not pid.startswith("P_"):
            errs.append(f"[볼 곳] id '{pid}'는 P_로 시작한다")
        if pid in seen:
            errs.append(f"[볼 곳] id 중복 {pid}")
        seen.add(pid)
        for k in ("name", "url", "angle", "added"):
            if not p.get(k):
                errs.append(f"[볼 곳] {pid}: '{k}' 칸이 없음")
        if p.get("angle") and p["angle"] not in ANGLES:
            errs.append(f"[볼 곳] {pid}: angle은 {list(ANGLES)} 중 하나")
        if "official" in p and not isinstance(p["official"], bool):
            errs.append(f"[볼 곳] {pid}: official은 true/false")
        if state_ids is not None:
            for s in p.get("covers", []):
                if s not in state_ids and not R.NUMBER_KEY.match(str(s)):
                    errs.append(f"[볼 곳] {pid}: covers의 '{s}'는 논리집에 없는 상태이고 숫자 이름(이름_단위)도 아님")
    return errs


# ---------- 숫자 상태 (2026-11-04, 결정 E5) ----------
_RULE = re.compile(r"^\s*(abs\s+)?(>=|<=|>|<)\s*([-+]?\d+(?:\.\d+)?)\s*$")


def parse_rule(text: str) -> tuple[bool, str, float]:
    """'> 1' · '<= -0.5' · 'abs > 1' → (절댓값인가, 비교, 문턱). 다른 모양이면 ValueError (eval을 쓰지 않는다)."""
    m = _RULE.match(str(text))
    if not m:
        raise ValueError(f"rule '{text}'는 '[abs] >|>=|<|<= 수' 모양이어야 함")
    return bool(m.group(1)), m.group(2), float(m.group(3))


def rule_holds(rule: tuple[bool, str, float], x: float) -> bool:
    use_abs, op, thr = rule
    v = abs(x) if use_abs else x
    return {">": v > thr, ">=": v >= thr, "<": v < thr, "<=": v <= thr}[op]


def numeric_states(raw: dict) -> dict[str, dict]:
    """number 칸이 있는 상태 id → {from, per, rule(글), parsed, unit}. 모양이 틀린 것은 뺀다(audit가 오류로 잡는다)."""
    out = {}
    for st in raw.get("state", []):
        spec = st.get("number")
        if not isinstance(spec, dict) or not spec.get("from"):
            continue
        try:
            parsed = parse_rule(spec.get("rule", ""))
        except ValueError:
            continue
        out[st["id"]] = {"from": str(spec["from"]), "per": str(spec["per"]) if spec.get("per") else None,
                         "rule": str(spec["rule"]), "parsed": parsed, "unit": str(spec.get("unit", ""))}
    return out


def number_keys(spec: dict) -> list[str]:
    return [spec["from"]] + ([spec["per"]] if spec.get("per") else [])


def number_of(spec: dict, numbers: dict) -> float | None:
    """숫자 상태가 보는 수: numbers[from], per가 있으면 numbers[from] ÷ numbers[per](per가 0 이하면 None). 없으면 None."""
    def num(k):
        v = numbers.get(k)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    x = num(spec["from"])
    if x is None:
        return None
    if spec.get("per"):
        d = num(spec["per"])
        if d is None or d <= 0:
            return None
        x = x / d
    return x


def number_value(spec: dict, numbers: dict) -> tuple[str, float | None]:
    """(값, 수): 수가 rule에 맞으면 '예', 아니면 '아니오', 수가 없으면 '모름'."""
    x = number_of(spec, numbers)
    if x is None:
        return "모름", None
    return ("예" if rule_holds(spec["parsed"], x) else "아니오"), x


def number_why(spec: dict, numbers: dict, x: float | None) -> str:
    keys = number_keys(spec)
    if x is None:
        return f"숫자 상태 — {' ÷ '.join(keys)}가 없어 코드가 '모름'으로 정함"
    got = " ÷ ".join(f"{k} = {numbers[k]:g}" for k in keys)
    return f"숫자 상태 — {got} → {x:.3g}, 규칙 '{spec['rule']}'로 코드가 정함"


# ---------- 조사 조각 ----------
def research_pieces(raw: dict) -> list[tuple[dict, dict]]:
    """살아 있는 조사 조각과 그 조건 상태 (조각, 상태)."""
    states = {s["id"]: s for s in raw.get("state", [])}
    out = []
    for h in raw.get("hypothesis", []):
        if h.get("read_by") != "조사" or h.get("status", "hypothesis") == "retired":
            continue
        conds = h.get("conditions") or []
        if conds and conds[0] in states:
            out.append((h, states[conds[0]]))
    return out


def seed_states(raw: dict) -> set[str]:
    """씨앗 조각(seed = true)만 쓰는 조사 상태 id. 정식 조사 조각이 하나라도 쓰는 상태는 매일 묻는다(여기 들지 않는다)."""
    seed, formal = set(), set()
    for h, _ in research_pieces(raw):
        (seed if h.get("seed") else formal).update(h.get("conditions") or [])
    return seed - formal


def research_states(raw: dict, per_asset: bool | None = None) -> list[dict]:
    """살아 있는 조사 조각이 쓰는 상태 (중복 없이, 처음 나온 순서). per_asset: False = 하루 하나로 대 보는 상태(시장 전체),
    True = 종목마다 대 보는 상태(state에 per_asset = true, 2026-10-29), None = 둘 다.
    첫 조건만이 아니라 조사로 대 보는 조건(read_by = "조사") 모두를 낸다(2026-11-03): 쪼개며 더한 조건(예: Q_rate_up_shift의
    S_q_rate_up)도 매일 물어 기록해야 검사(checks._holds의 also)가 그 조건을 볼 수 있다."""
    states = {s["id"]: s for s in raw.get("state", [])}
    seen, out = set(), []
    for h, _ in research_pieces(raw):
        for sid in h.get("conditions") or []:
            st = states.get(sid)
            if not st or st.get("read_by") != "조사":
                continue
            if per_asset is not None and bool(st.get("per_asset")) != per_asset:
                continue
            if sid not in seen:
                seen.add(sid)
                out.append(st)
    return out


# ---------- 성적 ----------
def place_cards(folder: Path, places: list[dict]) -> dict[str, dict]:
    """볼 곳 id → 성적. 목록에 없는 id도 기록에 있으면 나온다(listed = False)."""
    names = {p["id"]: p for p in places}
    cards: dict[str, dict] = defaultdict(lambda: {"questions": set(), "looks": 0, "찾음": 0, "없음": 0, "늦음": 0,
                                                  "막힘": 0, "new": 0, "checked": 0, "hits": 0, "late_after": 0,
                                                  "last": ""})
    for r in R.read(folder, "research"):
        for lk in r["looked"]:
            c = cards[lk["place"]]
            c["questions"].add(r["question"])
            c["looks"] += 1
            c[lk["result"]] += 1
            c["new"] += bool(lk.get("new"))
            c["last"] = max(c["last"], r["run"])
    readings = {h["id"]: h for h in R.read(folder, "hand_reading")}
    for chk in R.read(folder, "reading_check"):
        h = readings.get(chk["target"])
        for pid in set(h["places"].get(chk["state"], [])) if h else ():
            if chk["hit"] is not None:
                cards[pid]["checked"] += 1
                cards[pid]["hits"] += bool(chk["hit"])
        for pid in chk["late"]:
            cards[pid]["late_after"] += 1
    out = {}
    for pid in sorted(set(cards) | set(names)):
        c = cards[pid]
        out[pid] = {**c, "questions": len(c["questions"]), "listed": pid in names,
                    "name": names.get(pid, {}).get("name", "(목록에 없음)"),
                    "official": bool(names.get(pid, {}).get("official", False)),
                    "angle": names.get(pid, {}).get("angle", "")}
    return out


def usefulness(card: dict) -> tuple:
    """추천 순서: 찾은 비율(라플라스 보정) → 맞은 대 보기 → 덜 늦음. 처음 보는 곳은 가운데쯤(0.5)."""
    found = (card["찾음"] + 1) / (card["looks"] + 2)
    hit = (card["hits"] + 1) / (card["checked"] + 2)
    return (round(found, 3), round(hit, 3), -(card["늦음"] + card["late_after"]))


def table(cards: dict[str, dict]) -> str:
    lines = ["| 볼 곳 | 각도 | 공식 | 질문 | 본 횟수 | 찾음 | 없음 | 늦음(판단 뒤) | 막힘 | 새로 시도 | 대 보기 맞음 | 마지막 |",
             "|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|---|"]
    for pid, c in sorted(cards.items(), key=lambda kv: usefulness(kv[1]), reverse=True):
        hit = f"{c['hits']}/{c['checked']}" if c["checked"] else "–"
        lines.append(f"| {pid} {c['name']} | {c['angle']} | {'예' if c['official'] else ''} | {c['questions']} | "
                     f"{c['looks']} | {c['찾음']} | {c['없음']} | {c['늦음'] + c['late_after']} | {c['막힘']} | "
                     f"{c['new']} | {hit} | {c['last'] or '–'} |")
    return "\n".join(lines)


# ---------- 오늘의 질문 ----------
def questions(day: dt.date, raw: dict, places: list[dict], cards: dict[str, dict]) -> list[dict]:
    """그날 조사 질문: 조사 상태마다 하나 + 각도마다 '오늘 판단용' 각도 질문 하나(ANGLE_ASK).
    질문마다 numbers = 적을 숫자 이름(숫자 상태는 그 from·per, 각도 질문은 NUMBER_KEYS[각도])."""
    pieces = research_pieces(raw)
    seeds = seed_states(raw)
    specs = numeric_states(raw)
    out = []
    for st in research_states(raw):
        users = [h["id"] for h, _ in pieces if st["id"] in (h.get("conditions") or [])]
        keys = number_keys(specs[st["id"]]) if st["id"] in specs else []
        cand = [p for p in places if st["id"] in p.get("covers", []) or set(keys) & set(p.get("covers", []))]
        cand.sort(key=lambda p: usefulness(cards.get(p["id"], _empty())), reverse=True)
        ask = st.get("ask", st["text"])
        if st.get("per_asset"):
            ask = "[종목마다] " + ask + " (손 계좌가 든 종목과 살 후보 종목마다 따로 답한다)"
        if st["id"] in specs:
            ask = NUMBER_MARK + ask  # 숫자 상태: 숫자가 있는 날이 곧 관련 있는 날(씨앗이어도 줄을 지우지 않는다)
        elif st["id"] in seeds:
            ask = SEED_MARK + ask  # 조사 기록의 asked는 300자 안이어야 한다 — 표시는 짧게
        out.append({"question": f"q:{day.isoformat()}:{st['id']}", "state": st["id"], "angle": st.get("angle", ""),
                    "asked": ask, "pieces": users, "seed": st["id"] in seeds, "numbers": keys,
                    "suggest": [p["id"] for p in cand[:SUGGEST]]})
    for ang in ANGLES:
        keys = [k for k in NUMBER_KEYS.get(ang, {}) if R.NUMBER_KEY.match(k)]
        cand = [p for p in places if p.get("angle") == ang or set(keys) & set(p.get("covers", []))]
        cand.sort(key=lambda p: usefulness(cards.get(p["id"], _empty())), reverse=True)
        out.append({"question": f"q:{day.isoformat()}:now:{ang}", "state": "", "angle": ang,
                    "asked": ANGLE_ASK[ang], "pieces": [], "seed": False, "numbers": keys,
                    "suggest": [p["id"] for p in cand[:SUGGEST]]})
    return out


# 2차 조사(4 '모르면 더 판다')의 질문: 같은 네 각도를 대상 하나에 맞춰 깊이. 사후 설명('최근 1주 왜 움직였나')은 묻지 않는다
DEEP_ASK = ("[2차: {label}] 오늘 판단용으로 이 대상 하나만: ① 간밤 이 대상(또는 따라가는 해외 값)의 숫자와 평소 대비 배수 "
            "② 판단부터 1주 안 이 대상의 일정과 시장 예상치(KST 시각) ③ 전일 이 대상의 매매 주체 숫자 ④ 밤사이 이 대상의 "
            "공시·뉴스(사실 한 줄 + 숫자). 지금 조각에 없는 오를 까닭·내릴 까닭을 숫자와 함께 둘 다")


def deep_question(day: dt.date, asset: str, label: str) -> dict:
    """2차 조사 질문 줄(틀의 _deep_form): 질문 id q:<날짜>:deep:<대상>, 각도 '<대상>-2차'."""
    return {"question": f"q:{day.isoformat()}:deep:{asset}", "state": "", "angle": f"{asset}-2차",
            "asked": DEEP_ASK.format(label=label), "numbers": {}, "as_of": "", "answer": "", "agent": f"{asset}-2차",
            "looked": []}


def _empty() -> dict:
    return {"looks": 0, "찾음": 0, "checked": 0, "hits": 0, "늦음": 0, "late_after": 0}


def stale_places(folder: Path, day: dt.date, places: list[dict]) -> set[str]:
    """목록에 있지만 최근 FRESH_DAYS일 안 본 곳 (다시 봐도 '새 곳'으로 칠 수 있다)."""
    last: dict[str, str] = {}
    for r in R.read(folder, "research"):
        for lk in r["looked"]:
            last[lk["place"]] = max(last.get(lk["place"], ""), r["run"])
    cut = (day - dt.timedelta(days=FRESH_DAYS)).isoformat()
    return {p["id"] for p in places if last.get(p["id"], "") < cut}


def due_checks(folder: Path, day: dt.date) -> list[dict]:
    """다시 볼 손 대 보기: CHECK_AFTER_DAYS일 넘게 지났고, 값이 모름이 아니고, 아직 다시 보지 않은 것."""
    done = {(c["target"], c["state"]) for c in R.read(folder, "reading_check")}
    cut = (day - dt.timedelta(days=CHECK_AFTER_DAYS)).isoformat()
    out = []
    for h in R.read(folder, "hand_reading"):
        if h["run"] > cut:
            continue
        for st, v in h["values"].items():
            if str(v) != "모름" and (h["id"], st) not in done:
                out.append({"target": h["id"], "state": st, "then": str(v), "run": h["run"],
                            "why_then": h["why"].get(st, ""), "places": h["places"].get(st, [])})
    return out


def new_share_warnings(research: list[dict], places: list[dict], stale: set[str]) -> list[str]:
    """각도마다 새 곳 시도가 NEW_PER_ANGLE 이상인가 (목록에 없는 곳, 또는 오래 안 본 곳)."""
    listed = {p["id"] for p in places}
    by_angle: dict[str, int] = defaultdict(int)
    for r in research:
        for lk in r["looked"]:
            if lk.get("new") and (lk["place"] not in listed or lk["place"] in stale):
                by_angle[r["angle"]] += 1
    return [f"새 곳 시도 몫: '{a}' 각도에서 새로 시도한 곳이 {by_angle[a]}곳 (몫 {NEW_PER_ANGLE})"
            for a in ANGLES if by_angle[a] < NEW_PER_ANGLE]


def preview_numbers(inp: dict, raw: dict) -> list[str]:
    """입력(input.json)의 조사 numbers로 코드가 정할 숫자 상태 값(record와 같은 함수 hand.resolve_numbers)."""
    from . import hand as H  # hand가 이 모듈을 부르므로 여기서만
    states = [st["id"] for st in research_states(raw, per_asset=False) if st["id"] in numeric_states(raw)]
    readings, _, numbers, errs, warns = H.resolve_numbers(inp, raw, states, seed_states(raw))
    out = []
    for st in states:
        if st in readings:
            out.append(f"- {st} = {readings[st]['value']} ({readings[st]['why']}; 볼 곳 {readings[st].get('places') or '없음'})")
        else:
            out.append(f"- {st}: 숫자 없음 → 안 물음(씨앗)")
    return out + [f"  오류: {e}" for e in errs] + [f"  경고: {w}" for w in warns]


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("places", "questions", "numeric"):
        print(__doc__)
        return 2
    if argv[0] == "numeric":
        import json
        if len(argv) < 2:
            print("입력 파일을 준다: python -m market.research numeric out/daily/<날짜>/input.json")
            return 2
        raw = tomllib.loads(C.KNOWLEDGE.read_text(encoding="utf-8"))
        print("\n".join(preview_numbers(json.loads(Path(argv[1]).read_text(encoding="utf-8")), raw)))
        return 0
    places = load_places()
    cards = place_cards(C.RECORDS, places)
    if argv[0] == "places":
        print(table(cards))
        return 0
    raw = tomllib.loads(C.KNOWLEDGE.read_text(encoding="utf-8"))
    day = dt.date.fromisoformat(argv[1]) if len(argv) > 1 else C.now_kst().date()
    for q in questions(day, raw, places, cards):
        print(f"- [{q['angle']}] {q['question']}: {q['asked']}\n    추천 볼 곳: {', '.join(q['suggest']) or '없음'}"
              + (f"\n    숫자(numbers): {', '.join(q['numbers'])}" if q["numbers"] else ""))
    if day.isoformat() >= FAST_SINCE:
        print(f"\n모든 답에 numbers(이름_단위 → 값)와 as_of(정보 시각, 예: {day.isoformat()}T07:20+09:00)를 적는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
