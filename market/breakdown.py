"""쪼개기 전에 나눠 보기 (2026-11-17): 조각 하나가 켜졌던 채점을 대상별·대상 성질별·기간별로, 독립인 날 수와 함께 찍는다.

  python -m market.breakdown <조각 id> [--horizon N] [--since 날짜]

까닭(사용자 2026-11-17 "그러면 그거를 좀 더 분석을 해야 하는 거 아닌가"): 돌아보기가 "금리와 상관이 작은 회사에서 틀렸다"고
진단해 놓고, 처방은 범위 문법에 있는 더 넓은 조건('한 회사' 전부 빼기)으로 넓어졌다. 쪼개기 전에 '어디서만 틀렸나'를 먼저
나눠 보면, 진단과 쪼개는 조건이 같은 것인지 볼 수 있다.

두 무리를 따로 찍는다.
- **쓴 것**: 손 예측의 used에 이 조각이 든 예측(손이 실제로 이 조각을 걸고 말한 것).
- **켜진 날 전체**: 판단 날짜가 seen_until 뒤이고, 그날 대 본 값으로 이 조각 주장의 조건(when + also)이 맞았고, 대상이 주장 범위
  안인 계산기 예측 줄의 결과(시장이 어떻게 갔나 — 손이 쓰지 않은 대상도 든다). 성적표(checks.research_direction·piece_direction)가
  보는 무리와 같다.

세는 법:
- 같은 창(대상·기간·체결 봉(옛 줄은 기준 날짜)·목표 봉)을 여러 번 말했으면 한 번만 센다(먼저 말한 것). '창'이 그 한 번이다.
- 독립인 날 = 그 칸에 든 서로 다른 (시작 봉, 목표 봉) 쌍의 수. 같은 날의 여러 대상은 한 사건이라 하나로 센다.
- 주장대로 = 주장의 dir 쪽으로 갔나(up: 올랐다, down: 내렸다, sign: 그날 숫자의 부호 쪽). 움직임 주장(on = move)은 맞음을 세지
  않고 |수익률| 가운데 값만 보인다.
- 묶음 대비 = 같은 창의 자기 시장 묶음 수익률을 뺀 값(scoring.relative — 종목만)으로 본 '주장대로'.
- 같이 움직임 = 판단 때 '자기 시장 묶음과 같이 움직임'의 상관(pieces.comove; 기록에 없으면 판단 날 07:37에 끝나 있던 봉으로 다시 잰다).

판정이 아니다: 표본이 작고 겹친다. 어느 조건으로 쪼갤지 고르는 재료로만 쓴다(지시문 '쪼개기 전에 나눠 본다').
"""
from __future__ import annotations

import datetime as dt
import sys
import tomllib
from pathlib import Path

from . import config as C
from . import pieces as P
from . import prices as PR
from . import records as R
from . import research as RS
from . import scoring as S
from . import stocks as ST


def _node(raw: dict, node_id: str) -> dict | None:
    return next((h for h in raw.get("hypothesis", []) + raw.get("rule", []) if h.get("id") == node_id), None)


def _holds(vals: dict, claim: dict, state: str | None, known: dict) -> bool | None:
    need = {**({state: claim.get("when", "예")} if state else {}), **(claim.get("also") or {})}
    got = {k: vals.get(k) for k in need}
    if any(v not in ("예", "아니오") and v not in known.get(k, ()) for k, v in got.items()):
        return None
    return all(got[k] == v for k, v in need.items())


class _Prices:
    """시세 파일을 한 번씩만 읽는다. at이 있으면 그 시각에 끝나 있던 봉만(prices.view)."""

    def __init__(self, folder: Path):
        self.folder, self.rows = folder, {}

    def full(self, a: str) -> PR.History:
        if a not in self.rows:
            self.rows[a] = PR.load(a, self.folder)
        return PR.History(a, self.rows[a])

    def at(self, a: str, when: dt.datetime) -> PR.History:
        self.full(a)
        return PR.view(a, self.rows[a], when)


def _comove(p: dict, px: _Prices) -> float | None:
    """판단 때 '자기 시장 묶음과 같이 움직임' 상관. 기록(readings)에 값만 있고 숫자가 없으니 숫자는 시세로 다시 잰다."""
    a = p["asset"]
    when = C.decision_dt(dt.date.fromisoformat(p["run"]))
    return P.comove(px.at(a, when).rows, px.at(C.market_bundle(a), when).rows)[1]


def collect(node_id: str, records: Path = C.RECORDS, knowledge: Path = C.KNOWLEDGE, prices: Path = C.PRICES,
            since: str | None = None) -> dict:
    """{'node', 'used': [항목], 'on': [항목]}. 항목 = {asset, horizon, start, target, run, ret, rel, dir_ok, rel_ok, comove, traits}."""
    raw = tomllib.loads(knowledge.read_text(encoding="utf-8"))
    node = _node(raw, node_id)
    if node is None:
        raise SystemExit(f"조각 '{node_id}'가 논리집에 없다")
    cut = since or str(node.get("seen_until", C.RESEARCH_SINCE))
    state = (node.get("conditions") or [None])[0]
    claims = node.get("claims", [])
    known = {s["id"]: set(s.get("answers", [])) - {"모름", "일부"} for s in raw.get("state", []) if s.get("answers")}
    spec = RS.numeric_states(raw).get(state) if state else None
    preds = {p["id"]: p for p in R.read(records, "prediction")}
    pairs = [(preds[s["prediction"]], s) for s in R.read(records, "score") if s["prediction"] in preds]
    hrs = {h["run"]: h for h in R.read(records, "hand_reading")}
    px = _Prices(prices)

    def vals_of(p: dict) -> tuple[dict, dict]:
        h = hrs.get(p["run"]) or {}
        by = (h.get("by_asset") or {}).get(p["asset"], {})
        vals = {**(p.get("readings") or {}), **(h.get("values") or {}),
                **{k: v["value"] if isinstance(v, dict) else v for k, v in by.items()}}
        nums = (by.get(state) or {}).get("numbers") if isinstance(by.get(state), dict) else None
        nums = nums if isinstance(nums, dict) else (h.get("numbers") or {}).get(state, {})
        return vals, nums

    def item(p: dict, s: dict, claim: dict, nums: dict) -> dict:
        start = s.get("fill_date") or p["base_date"]
        rel = S.relative_of(p, s, {C.market_bundle(p["asset"]): px.full(C.market_bundle(p["asset"]))}).get("rel_ret")
        sign = 1
        if claim.get("dir") == "sign":
            x = RS.number_of(spec, nums) if spec else None
            sign = (x > 0) - (x < 0) if x is not None else 0
        want = {"up": 1, "down": -1, "sign": sign}.get(claim.get("dir"), 0)

        def ok(r):
            if claim.get("on") != "up_odds" or not want or r is None or r == 0:
                return None
            return (r > 0) == (want > 0)
        return {"asset": p["asset"], "horizon": p["horizon"], "start": start, "target": s["target_date"], "run": p["run"],
                "ret": s["ret"], "rel": rel, "dir_ok": ok(s["ret"]), "rel_ok": ok(rel), "on": claim.get("on"),
                "comove": _comove(p, px), "traits": C.traits(p["asset"])}

    def claim_for(p: dict) -> dict | None:
        hz = C.ASSETS[p["asset"]].horizons.index(p["horizon"])
        spans = {"다음": 0, "1주": 1, "4주": 2}
        for c in claims:
            if C.in_scope(p["asset"], c.get("scope")) and (not c.get("span") or hz in [spans[x] for x in c["span"]]):
                return c
        return None

    used, on = [], []
    for p, s in S.first_only([(p, s) for p, s in pairs if p["predictor"] == "hand" and p["run"] > cut
                              and any(str(u).partition("=")[0] == node_id for u in p.get("used", []))]):
        c = claim_for(p)
        if c is None:
            continue
        _, nums = vals_of(p)
        used.append(item(p, s, c, nums))
    for p, s in S.first_only([(p, s) for p, s in pairs if p["predictor"] == "calc" and p["run"] > cut]):
        c = claim_for(p)
        if c is None:
            continue
        vals, nums = vals_of(p)
        if _holds(vals, c, state, known) is True:
            on.append(item(p, s, c, nums))
    return {"node": node_id, "cut": cut, "used": _dedupe(used), "on": _dedupe(on)}


def _dedupe(items: list[dict]) -> list[dict]:
    """같은 창(대상·기간·시작 봉·목표 봉)은 먼저 말한 것 하나만."""
    seen, out = set(), []
    for it in sorted(items, key=lambda x: (x["run"], x["asset"], x["horizon"])):
        k = (it["asset"], it["horizon"], it["start"], it["target"])
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def row(items: list[dict]) -> dict:
    """한 칸의 요약: 창 수, 독립인 날, 주장대로 간 창(절대·묶음 대비), |수익률| 가운데 값, 같이 움직임 상관 범위."""
    dirs = [i["dir_ok"] for i in items if i["dir_ok"] is not None]
    rels = [i["rel_ok"] for i in items if i["rel_ok"] is not None]
    cm = [i["comove"] for i in items if i["comove"] is not None]
    absr = sorted(abs(i["ret"]) for i in items)
    return {"n": len(items), "days": len({(i["start"], i["target"]) for i in items}),
            "dir": (sum(dirs), len(dirs)), "rel": (sum(rels), len(rels)),
            "abs_med_pct": round(absr[len(absr) // 2] * 100, 2) if absr else None,
            "comove": (min(cm), max(cm)) if cm else None}


def tables(items: list[dict]) -> dict[str, dict]:
    """대상별(대상 × 기간), 성질별(성질 × 기간), 기간별."""
    out: dict[str, dict] = {"대상": {}, "성질": {}, "기간": {}}
    for key, f in (("대상", lambda i: (i["asset"], i["horizon"])),
                   ("성질", lambda i: ("·".join(i["traits"]), i["horizon"])), ("기간", lambda i: (i["horizon"],))):
        groups: dict = {}
        for i in items:
            groups.setdefault(f(i), []).append(i)
        out[key] = {k: row(v) for k, v in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0]))}
    return out


def _name(a: str) -> str:
    return C.ASSETS[a].name if a in C.CORE else (C.STOCK_NAMES.get(a) or a)


def render(res: dict, horizon: int | None = None) -> str:
    L = [f"# 나눠 보기: {res['node']} (판단 {res['cut']} 뒤, 같은 창은 한 번)",
         "판정이 아니다 — 쪼갤 조건을 고르는 재료. '주장대로' = 주장의 방향 쪽으로 간 창 / 방향을 셀 수 있는 창."]
    for label, key in (("쓴 것 (손 예측 used에 이 조각)", "used"), ("켜진 날 전체 (그날 조건이 맞은 대상의 계산기 줄 결과)", "on")):
        items = [i for i in res[key] if horizon is None or i["horizon"] == horizon]
        L += ["", f"## {label} — 창 {len(items)}개"]
        if not items:
            L.append("없음")
            continue
        for tname, t in tables(items).items():
            L += ["", f"### {tname}별", "| 칸 | 창 | 독립인 날 | 주장대로 | 묶음 대비 주장대로 | |수익률| 가운데 | 같이 움직임 상관 |",
                  "|---|---|---|---|---|---|---|"]
            for k, r in t.items():
                if tname == "대상":
                    cell = f"{_name(k[0])} ({k[0]}) {k[1]}봉"
                elif tname == "성질":
                    cell = f"{k[0]} {k[1]}봉"
                else:
                    cell = f"{k[0]}봉"
                d = f"{r['dir'][0]} / {r['dir'][1]}" if r["dir"][1] else "–"
                rl = f"{r['rel'][0]} / {r['rel'][1]}" if r["rel"][1] else "–"
                cm = f"{r['comove'][0]:.2f} ~ {r['comove'][1]:.2f}" if r["comove"] else "–"
                L.append(f"| {cell} | {r['n']} | {r['days']} | {d} | {rl} | {r['abs_med_pct']}% | {cm} |")
    L += ["", f"같이 움직임 상관: 판단 때 최근 {P.COMOVE_BARS}봉, 자기 시장 묶음과의 하루 수익률 상관(문턱 {P.COMOVE_MIN} — 상태 "
          f"{P.COMOVE_STATE}). 묶음 자신은 1."]
    return "\n".join(L)


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    node, horizon, since = argv[0], None, None
    rest = argv[1:]
    while rest:
        k = rest.pop(0)
        if k == "--horizon" and rest:
            horizon = int(rest.pop(0))
        elif k == "--since" and rest:
            since = rest.pop(0)
        else:
            print(f"모르는 인자 {k}")
            return 2
    ST.register()
    print(render(collect(node, since=since), horizon))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
