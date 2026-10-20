"""재생: 날짜 순서대로 하루씩 걸으며, 그날 07:37에 알려진 시세만 보고 대 보기 → 계산기 예측 → 채점 → 재생 계좌.

사용법 (저장소 루트):
  python -m market.replay                         2026-07-01 ~ 2026-10-19 재생 → records/replay/ (이미 있으면 거절)
  python -m market.replay --start 2026-01-02 --end 2026-03-31 --out <폴더>   다른 기간·폴더 (디버깅용)
  python -m market.replay report [폴더]           재생 기록을 모아 표로 (docs 보고서 재료)

규칙
- **미래 누설 방지(구조)**: 하루마다 prices.view(판단 시각에 끝나 있던 일봉만 담은 묶음)를 만들고, 그날의 모든 계산
  (정산·채점·대 보기·예측·주문)은 그 묶음만 받는다. 전체 시세는 view를 만드는 데만 쓴다. tests/test_replay.py가
  뒤 시세에 독을 넣어도 앞날의 결과가 같은지 확인한다.
- 마지막 날 다음 날(end+1) 07:37에 채점·정산만 한 번 더 한다(새 예측·주문 없음): 기간 끝 무렵 예측의 목표 종가를 거둔다.
- 계좌 셋: replay:calc(계산기를 따름, R_weights), baseline:equal_hold(첫날 셋을 1/3씩 사 두기), baseline:cash(전부 현금).
- 재생 기간은 홀드아웃이다. 결과를 본 뒤 조각·숫자를 고치지 않는다. 출력 폴더가 이미 있으면 덮어쓰지 않는다.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from . import calc as K
from . import config as C
from . import ledger as L
from . import pieces as P
from . import prices as PR
from . import records as R
from . import scoring as S

PREDICTORS = ("calc", "baseline:half", "baseline:base_rate")
ACCOUNTS = ("replay:calc", "baseline:equal_hold", "baseline:cash")
WEIGHT_HORIZON_INDEX = 1  # 1주 (5거래일 / 7일)


def days(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


class Replay:
    def __init__(self, pieces: list[K.Piece], digest: str):
        self.pieces, self.digest = pieces, digest
        self.accounts = {a: L.Account(a) for a in ACCOUNTS}
        self.preds: list[dict] = []
        self.done: set[str] = set()
        self.first = True

    def step(self, day: dt.date, hists: dict[str, PR.History], predict: bool = True) -> dict[str, list[dict]]:
        """하루. hists = 그날 07:37에 알려진 일봉만. 돌려주는 것 = 그날 덧붙일 기록(종류 → 줄들)."""
        out: dict[str, list[dict]] = {k: [] for k in ("fill", "score", "account", "reading", "prediction", "order", "run")}
        run = day.isoformat()
        for acc in self.accounts.values():
            out["fill"] += acc.settle(hists, day)
        out["score"] = S.score_due(self.preds, hists, day, self.done)
        last = L.last_prices(hists)
        price = {a: v for a, (_, v) in last.items()}
        for acc in self.accounts.values():
            out["account"].append(acc.record(day, last))
        weights: dict[str, float | None] = {}
        reasons: dict[str, str] = {}
        if predict:
            for asset in C.ASSET_IDS:
                h = hists[asset]
                if h.last is None:
                    continue
                values, numbers = P.read(h.closes)
                out["reading"].append({"id": f"reading:{run}:{asset}", "run": run, "asset": asset,
                                       "known_through": h.last.date.isoformat(), "values": values, "numbers": numbers})
                for i, hz in enumerate(C.ASSETS[asset].horizons):
                    base = {"run": run, "asset": asset, "horizon": hz, "base_date": h.last.date.isoformat(),
                            "base_close": h.last.close}
                    r = K.predict(asset, hz, values, self.pieces)
                    f = K.record_fields(r)
                    out["prediction"].append({"id": f"{run}:calc:{asset}:{hz}", "predictor": "calc", **base,
                                              "readings": values, "knowledge": self.digest, **f})
                    if i == WEIGHT_HORIZON_INDEX:
                        weights[asset] = L.target_weight(f["p_up"])
                        reasons[asset] = f"R_weights: 1주 오를 확률 {f['p_up']} → 목표 비중 {weights[asset]}"
                    out["prediction"].append({"id": f"{run}:baseline:half:{asset}:{hz}", "predictor": "baseline:half",
                                              **base, "p_up": 0.5, "p_range": None, "size_lo": None, "size_hi": None,
                                              "readings": {}, "used": [], "decisive": [], "reason": "늘 50%",
                                              "knowledge": ""})
                    br, w = S.base_rate(h, hz)
                    out["prediction"].append({"id": f"{run}:baseline:base_rate:{asset}:{hz}",
                                              "predictor": "baseline:base_rate", **base,
                                              "p_up": round(br, 4) if br is not None else None, "p_range": None,
                                              "size_lo": round(w[0], 4) if w else None,
                                              "size_hi": round(w[1], 4) if w else None, "readings": {}, "used": [],
                                              "decisive": [], "reason": f"최근 {S.BASE_RATE_WINDOW}봉에서 {hz}봉 수익률이 오른 비율",
                                              "knowledge": ""})
            self.preds += out["prediction"]
            out["order"] += [o.record() for o in self.accounts["replay:calc"].plan(day, weights, price, 0.02, reasons)]
            if self.first:
                eq = {a: 1 / 3 for a in C.ASSET_IDS}
                out["order"] += [o.record() for o in self.accounts["baseline:equal_hold"].plan(
                    day, eq, price, 0.0, {a: "기준선: 첫날 1/3씩 사서 그대로 둔다" for a in C.ASSET_IDS})]
                self.first = False
        out["run"].append({"id": f"replay:{run}", "run": run, "mode": "replay" if predict else "replay-score-only",
                           "steps": {"fills": len(out["fill"]), "scores": len(out["score"]),
                                     "predictions": len(out["prediction"]), "orders": len(out["order"]),
                                     "known_through": {a: h.last.date.isoformat() if h.last else None
                                                       for a, h in hists.items()}},
                           "notes": []})
        return out


def run(rows_by_asset: dict[str, tuple[PR.Row, ...]], start: dt.date, end: dt.date, pieces: list[K.Piece],
        digest: str, out_dir: Path | None) -> dict[str, list[dict]]:
    """start~end를 걷고 end+1에 채점만. out_dir이 있으면 날마다 덧붙여 쓴다. 모든 기록을 종류별로 돌려준다."""
    rp = Replay(pieces, digest)
    allrec: dict[str, list[dict]] = {}
    for day in days(start, end + dt.timedelta(days=1)):
        at = C.decision_dt(day)
        hists = {a: PR.view(a, rows_by_asset[a], at) for a in C.ASSET_IDS}  # 그날 알려진 것만
        recs = rp.step(day, hists, predict=day <= end)
        for kind, items in recs.items():
            if not items:
                continue
            allrec.setdefault(kind, []).extend(items)
            if out_dir is not None:
                R.write(out_dir, kind, items)
    return allrec


# ---------- 모아 보기 ----------
def summary(rec: dict[str, list[dict]]) -> dict:
    scores = rec.get("score", [])
    preds = rec.get("prediction", [])
    by = S.summarize(scores, lambda s: (s["predictor"], s["asset"], s["horizon"]))
    by_pred = S.summarize(scores, lambda s: s["predictor"])
    by_pred_h = S.summarize(scores, lambda s: (s["predictor"], s["horizon"] if s["asset"] != "KRW-BTC" else
                                               {1: 1, 7: 5, 28: 20}[s["horizon"]]))
    accounts: dict[str, list] = {}
    for a in rec.get("account", []):
        accounts.setdefault(a["account"], []).append((a["run"], a["value"]))
    pieces = S.piece_table(preds, scores, list(P.STATES))
    return {"by": {"|".join(map(str, k)): v for k, v in by.items()},
            "by_predictor": by_pred,
            "by_predictor_horizon": {"|".join(map(str, k)): v for k, v in by_pred_h.items()},
            "accounts": accounts,
            "pieces": {"|".join(map(str, k)): v for k, v in pieces.items()},
            "decisive_counts": _decisive_counts(preds),
            "fills": len(rec.get("fill", [])), "orders": len(rec.get("order", []))}


def _decisive_counts(preds: list[dict]) -> dict:
    c: dict[str, int] = {}
    for p in preds:
        if p["predictor"] == "calc":
            for d in p["decisive"]:
                c[d] = c.get(d, 0) + 1
    return dict(sorted(c.items()))


def load_records(folder: Path) -> dict[str, list[dict]]:
    return {k: R.read(folder, k) for k in R.FIELDS}


def report(folder: Path) -> str:
    sm = summary(load_records(folder))
    lines = ["## 예측기별 (모든 대상·기간)", "| 예측기 | 채점 수 | 확률 점수(낮을수록 좋음) | 방향 적중 | 크기 적중 | 실제 오른 비율 |",
             "|---|--:|--:|--:|--:|--:|"]
    for k, v in sm["by_predictor"].items():
        lines.append(_row(k, v))
    lines += ["", "## 예측기 × 기간 (다음/1주/4주, 코인 1·7·28일을 1·5·20에 맞춤)",
              "| 예측기·기간 | 채점 수 | 확률 점수 | 방향 적중 | 크기 적중 | 실제 오른 비율 |", "|---|--:|--:|--:|--:|--:|"]
    for k, v in sm["by_predictor_horizon"].items():
        lines.append(_row(k, v))
    lines += ["", "## 예측기 × 대상 × 기간", "| 예측기·대상·기간 | 채점 수 | 확률 점수 | 방향 적중 | 크기 적중 | 실제 오른 비율 |",
              "|---|--:|--:|--:|--:|--:|"]
    for k, v in sm["by"].items():
        lines.append(_row(k, v))
    lines += ["", "## 계좌 (날마다 07:37 평가액, 원)", "| 계좌 | 첫날 | 가장 낮음 | 가장 높음 | 마지막 | 마지막 날 |", "|---|--:|--:|--:|--:|---|"]
    for acc, series in sm["accounts"].items():
        vals = [v for _, v in series]
        lines.append(f"| {acc} | {vals[0]:,.0f} | {min(vals):,.0f} | {max(vals):,.0f} | {vals[-1]:,.0f} | {series[-1][0]} |")
    lines += ["", "## 조각별: 예/아니오일 때 실제 오른 비율과 움직임(|수익률| 가운데 값, %)",
              "| 상태·대상·기간 | 예: 수 | 예: 오른 비율 | 예: 움직임 | 아니오: 수 | 아니오: 오른 비율 | 아니오: 움직임 |",
              "|---|--:|--:|--:|--:|--:|--:|"]
    for k, v in sm["pieces"].items():
        y, n = v["예"], v["아니오"]
        lines.append(f"| {k} | {y['n']} | {y['up_rate']} | {y['abs_med']} | {n['n']} | {n['up_rate']} | {n['abs_med']} |")
    lines += ["", "## 결론을 바꾼 조각 (계산기 예측 중 그 조각을 빼면 결론이 바뀐 횟수)"]
    lines += [f"- {k}: {v}" for k, v in sm["decisive_counts"].items()] or ["- 없음"]
    lines += ["", f"주문 {sm['orders']}건, 체결 {sm['fills']}건"]
    return "\n".join(lines)


def _row(k: str, v: dict) -> str:
    calls = f"{v['hits']}/{v['calls']}" + (f" ({100 * v['hits'] / v['calls']:.0f}%)" if v["calls"] else "")
    size = f"{v['size_hits']}/{v['size_n']}" + (f" ({100 * v['size_hits'] / v['size_n']:.0f}%)" if v["size_n"] else "")
    return f"| {k} | {v['n']} | {v['brier'] if v['brier'] is not None else '–'} | {calls} | {size} | {v['up_rate']} |"


def main(argv: list[str]) -> int:
    if argv and argv[0] == "report":
        folder = Path(argv[1]) if len(argv) > 1 else C.REPLAY
        print(report(folder))
        return 0
    args = dict(zip(argv[::2], argv[1::2]))
    start = dt.date.fromisoformat(args.get("--start", C.REPLAY_START.isoformat()))
    end = dt.date.fromisoformat(args.get("--end", C.REPLAY_END.isoformat()))
    out = Path(args.get("--out", str(C.REPLAY)))
    if any(out.glob("*.jsonl")):
        print(f"{out}에 재생 기록이 이미 있다 — 재생은 한 번만 돌리고 그대로 둔다. 다시 돌려야 하면 다른 --out 폴더를 쓰고 보고서에 까닭을 적는다")
        return 1
    pieces, digest = K.load_pieces()
    rows = PR.load_all()
    rec = run(rows, start, end, pieces, digest, out)
    sm = summary(rec)
    (out / "summary.json").write_text(json.dumps(sm, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"재생 끝: {start} ~ {end} (+{end + dt.timedelta(days=1)} 채점만), 논리집 {digest}, 출력 {out}")
    print(report(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
