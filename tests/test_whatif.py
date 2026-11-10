"""지난 결정을 가상 증권사에 대입해 보기(market/whatif.py, 2026-11-10). 가짜 시세(tests/fixtures)와 옛 장부(market/ledger.py)로 만든
작은 가짜 기록으로 돈다(네트워크 없음).

F137(시험용 가짜가 실제와 같은 값을 주면 틀린 길도 통과한다)을 피하려고: 가짜 기록의 비트코인 판단 때 가격은 계좌 줄의 평가
가격(이틀 전 종가)과 일부러 다르게 두고, 1분봉 값도 판단 때 가격과 다르게 둔다 — 잘못된 가격을 골라 쓰면 시험이 걸린다."""
import dataclasses
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from market import config as C
from market import ledger as L
from market import prices as PR
from market import whatif as W
from tests import fixtures

BTC = "KRW-BTC"
DAYS = [dt.date(2025, 9, 2), dt.date(2025, 9, 3), dt.date(2025, 9, 4), dt.date(2025, 9, 5), dt.date(2025, 9, 6)]
HAND = {DAYS[0]: {"069500": 0.3, "360750": 0.3, BTC: 0.2}, DAYS[2]: {"069500": 0.1, "360750": 0.45, BTC: 0.3}}
CALC = {DAYS[0]: {"069500": 1 / 3, "360750": 1 / 3, BTC: 1 / 3}}
EQUAL = {DAYS[0]: {"069500": 1 / 3, "360750": 1 / 3, BTC: 1 / 3}}


def decided(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(7, 41, 30), C.KST)


def coin_bar(rows, day):
    return next(r for r in rows[BTC] if r.date == day)


def build(tmp: Path, rows=None) -> tuple[Path, Path, dict, dict]:
    """옛 장부로 닷새를 돌려 기록(run·account·order·fill)과 시세 파일을 만든다. (기록 폴더, 시세 폴더, 비트코인 분봉 dict,
    날마다 옛 장부가 주문 수량을 잡은 가격)."""
    rows = rows or fixtures.market(dt.date(2024, 6, 1), dt.date(2025, 9, 30))
    prices = tmp / "data" / "prices"
    for a, rs in rows.items():
        PR.append(a, list(rs), prices)
    rec = tmp / "records"
    rec.mkdir(parents=True)
    accs = {aid: L.Account(aid) for aid in C.LIVE_ACCOUNTS}
    out = {k: [] for k in ("run", "account", "order", "fill")}
    coin = {"decision": {}, "after_open": {}}
    planned = {}
    for day in DAYS:
        at = decided(day)
        hists = {a: PR.view(a, rs, at) for a, rs in rows.items()}
        for acc in accs.values():
            out["fill"] += acc.settle(hists, day)
        last = L.last_prices(hists)
        out["account"] += [acc.record(day, last) for acc in accs.values()]
        p = {a: v for a, (_, v) in last.items()}
        # 판단 때 가격: 지금 진행 중인 코인 봉(어제 09:00 시작)의 저가–고가 사이 값 — 계좌 줄의 값(이틀 전 종가)과 다르다.
        # (봉 밖의 값을 주면 검산이 '지금 체결가가 그날 저가–고가 밖'으로 잡는다 — 처음 쓴 × 1.07이 그랬다)
        run_bar = coin_bar(rows, day - dt.timedelta(days=1))
        p[BTC] = round(run_bar.low * 0.6 + run_bar.high * 0.4)
        planned[day] = dict(p)
        for aid, plan in (("live:hand", HAND), ("live:calc", CALC), ("baseline:equal_hold", EQUAL)):
            if day in plan:
                out["order"] += [o.record() for o in accs[aid].plan(day, plan[day], p, 0.02, {}, at, C.FILL_RULE)]
        out["run"].append({"id": f"live:{day}", "run": day.isoformat(), "mode": "live", "notes": [],
                           "steps": {"decided_at": at.isoformat(timespec="seconds"), "skipped": {},
                                     "decision_price": {BTC: {"price": p[BTC],
                                                              "time": (at - dt.timedelta(minutes=7)).isoformat()}}}})
        coin["decision"][day.isoformat()] = {"kst": at.replace(second=0, tzinfo=None).isoformat(), "close": round(run_bar.low * 0.4 + run_bar.high * 0.6),
                                             "exact": True}
        coin["after_open"][day.isoformat()] = {"kst": f"{day}T09:05:00", "close": round((coin_bar(rows, day).open + coin_bar(rows, day).high) / 2),
                                               "exact": True}
    for kind, items in out.items():
        with (rec / f"{kind}.jsonl").open("w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps({"kind": kind, **it}, ensure_ascii=False) + "\n")
    return rec, prices, coin, planned


def strip(res: dict) -> dict:
    return {k: v for k, v in res.items() if k != "made_at"}


class WhatIf(unittest.TestCase):
    def test_reads_only_and_writes_only_to_out(self):
        real = {p: (p.stat().st_size, p.stat().st_mtime_ns)
                for d in (C.ROOT / "records", C.ROOT / "reports", C.PRICES) if d.exists() for p in d.rglob("*")}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rec, prices, coin, _ = build(tmp)
            before = {p.name: p.read_bytes() for p in rec.iterdir()} | {p.name: p.read_bytes() for p in prices.iterdir()}
            res = W.run(rec, prices, coin, tmp / "out" / "whatif", root=tmp)
            after = {p.name: p.read_bytes() for p in rec.iterdir()} | {p.name: p.read_bytes() for p in prices.iterdir()}
            self.assertEqual(before, after)
            self.assertTrue((tmp / "out" / "whatif" / "summary.json").exists())
            self.assertTrue((tmp / "out" / "whatif" / "once" / "records" / "account.jsonl").exists())
            for case in W.CASES:
                self.assertEqual(res["cases"][case]["check_errors"], [])
            # 진짜 기록·일지·시세 폴더 아래로는 쓰지 않는다(거절)
            for bad in (tmp / "records" / "x", tmp / "data", tmp / "reports"):
                with self.assertRaises(SystemExit):
                    W.run(rec, prices, coin, bad, root=tmp)
            with self.assertRaises(SystemExit):
                W._guard(C.ROOT / "records" / "whatif", C.ROOT)
        self.assertEqual(real, {p: (p.stat().st_size, p.stat().st_mtime_ns)
                                for d in (C.ROOT / "records", C.ROOT / "reports", C.PRICES) if d.exists() for p in d.rglob("*")})

    def test_same_input_same_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec, prices, coin, _ = build(Path(tmp))
            a = W.run(rec, prices, coin, None, root=Path(tmp))
            b = W.run(rec, prices, coin, None, root=Path(tmp))
            self.assertEqual(json.dumps(strip(a), sort_keys=True, default=str),
                             json.dumps(strip(b), sort_keys=True, default=str))

    def test_same_result_in_another_process(self):
        """다른 프로세스(문자열 해시 씨앗이 다름)에서도 같은 결과 — 목표의 차례를 집합에 맡기면 돈이 모자란 날 사는 차례가 바뀐다."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rec, prices, coin, _ = build(tmp)
            (tmp / "coin.json").write_text(json.dumps(coin), encoding="utf-8")
            got = []
            for seed in ("1", "2", "3"):
                out = tmp / f"out{seed}"
                env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"}
                r = subprocess.run([sys.executable, "-m", "market.whatif", "--records", str(rec), "--prices", str(prices),
                                    "--coin-prices", str(tmp / "coin.json"), "--out", str(out)],
                                   cwd=C.ROOT, env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                got.append(strip(json.loads((out / "summary.json").read_text(encoding="utf-8"))))
            self.assertEqual(got[0], got[1])
            self.assertEqual(got[0], got[2])

    def test_no_value_after_the_decision(self):
        """그날 판단 시각 뒤의 값을 쓰지 않는다: 셋째 날 뒤의 봉(시가·종가 모두)을 바꿔도 셋째 날까지의 계좌 값·목표가 같다.
        셋째 날 09:05 단계는 셋째 날 시가를 쓰므로(그 시각에 알 수 있는 값) 셋째 날 봉은 그대로 둔다."""
        rows = fixtures.market(dt.date(2024, 6, 1), dt.date(2025, 9, 30))
        cut = DAYS[2]
        bent = {a: tuple(r if r.date <= cut else dataclasses.replace(r, open=r.open * 1.5, high=r.high * 1.5,
                                                                     low=r.low * 1.5, close=r.close * 1.5)
                         for r in rs) for a, rs in rows.items()}
        res = []
        for rs in (rows, bent):
            with tempfile.TemporaryDirectory() as tmp:
                rec, prices, coin, _ = build(Path(tmp), rows)   # 기록은 같은 시세로 만든다(그때의 결정은 같다)
                if rs is bent:
                    for p in prices.iterdir():
                        p.unlink()
                    for a, r in rs.items():
                        PR.append(a, list(r), prices)
                res.append(W.run(rec, prices, coin, None, root=Path(tmp)))
        a, b = res
        upto = [d.isoformat() for d in DAYS if d <= cut]
        for aid in C.LIVE_ACCOUNTS:
            ka = [x for x in a["daily"][aid] if x["day"] in upto]
            kb = [x for x in b["daily"][aid] if x["day"] in upto]
            self.assertEqual(ka, kb, aid)
        for case in W.CASES:
            self.assertEqual({d: a["cases"][case]["targets_hand"][d] for d in upto},
                             {d: b["cases"][case]["targets_hand"][d] for d in upto})
        # 뒤 날짜는 실제로 달라졌다(바꾼 봉이 쓰였다 — 시험이 헛돌지 않게)
        self.assertNotEqual(a["daily"]["live:hand"][-1]["once"], b["daily"]["live:hand"][-1]["once"])

    def test_targets_are_the_old_expected_holdings(self):
        """되살린 목표 비중 = 옛 장부의 '주문까지 더한 예상 보유'(Account.expected)를 그날 주문 수량을 잡은 가격으로 친 비중."""
        with tempfile.TemporaryDirectory() as tmp:
            rec, prices, coin, planned = build(Path(tmp))
            from market import records as R
            accounts, orders = R.read(rec, "account"), R.read(rec, "order")
            runs = {r["run"]: r for r in R.read(rec, "run")}
            for day in DAYS:
                d = day.isoformat()
                w, info = W.old_targets("live:hand", d, accounts, orders, runs[d])
                acc = L.rebuild("live:hand", [a for a in accounts if a["run"] <= d], [o for o in orders if o["run"] <= d])
                units, cash = acc.expected(planned[day])
                total = cash + sum(n * planned[day][a] / C.ASSETS[a].unit for a, n in units.items())
                for a in C.ASSET_IDS:
                    self.assertAlmostEqual(w[a], units.get(a, 0) * planned[day][a] / C.ASSETS[a].unit / total, places=9)
                # 비트코인은 판단 때 가격으로 쳤다(계좌 줄의 이틀 전 종가가 아니다)
                line = next(x for x in accounts if x["account"] == "live:hand" and x["run"] == d)
                self.assertEqual(info["prices"][BTC], planned[day][BTC])
                self.assertNotEqual(info["prices"][BTC], line["prices"][BTC][1])
            # 첫날 목표는 손 계좌가 정한 비중과 거의 같다(문턱·내림만큼)
            w0, _ = W.old_targets("live:hand", DAYS[0].isoformat(), accounts, orders, runs[DAYS[0].isoformat()])
            for a, t in HAND[DAYS[0]].items():
                self.assertAlmostEqual(w0[a], t, delta=0.02)

    def test_coin_price_source(self):
        """비트코인 지금 가격: 1분봉 파일이 있으면 그 종가, 없으면 실행 기록의 판단 때 가격 — 어느 쪽을 썼는지 결과에 남는다.
        정해 둔 창(판단 시각부터 10분, 09:05부터 10분) 밖에서는 값이 없다."""
        with tempfile.TemporaryDirectory() as tmp:
            rec, prices, coin, planned = build(Path(tmp))
            with_file = W.run(rec, prices, coin, None, root=Path(tmp))
            without = W.run(rec, prices, None, None, root=Path(tmp))
        d0 = DAYS[0].isoformat()
        self.assertEqual(with_file["coin_source"][d0]["decision"], "minute")
        self.assertNotEqual(with_file["coin_source"][d0]["price"], planned[DAYS[0]][BTC])
        self.assertEqual(with_file["coin_source"][d0]["price"], coin["decision"][d0]["close"])
        self.assertEqual(without["coin_source"][d0]["decision"], "decision_price")
        self.assertEqual(without["coin_source"][d0]["price"], planned[DAYS[0]][BTC])
        self.assertIsNone(without["coin_after_open"][d0])
        self.assertNotEqual(with_file["daily"]["live:hand"][1]["once"], without["daily"]["live:hand"][1]["once"])
        rows = fixtures.market(dt.date(2024, 6, 1), dt.date(2025, 9, 30))
        q = W.WhatIfQuotes(rows, [(decided(DAYS[0]), 123.0, "x")])
        self.assertEqual(q.now(BTC, decided(DAYS[0]) + dt.timedelta(minutes=3)).price, 123.0)
        self.assertIsNone(q.now(BTC, decided(DAYS[0]) + dt.timedelta(minutes=11)))
        self.assertIsNone(q.now(BTC, decided(DAYS[0]) - dt.timedelta(seconds=1)))

    def test_attribution_adds_up(self):
        """차이 가르기: 끝 값 − 시작 값 = 보유 몫 + 치른 값 몫 − 수수료 − 세금 − 이체 수수료가 계좌마다 정확히 맞고, 대입의 끝 값은
        그날 계좌 줄의 값과 같다(같은 평가 가격). 옛 쪽은 다시 친 값과 같다."""
        with tempfile.TemporaryDirectory() as tmp:
            rec, prices, coin, _ = build(Path(tmp))
            res = W.run(rec, prices, coin, None, root=Path(tmp))
        self.assertTrue(res["same_marks"])
        for aid in C.LIVE_ACCOUNTS:
            parts = res["accounts"][aid]["parts"]
            self.assertAlmostEqual(parts["old"]["identity"], 0, places=4)
            self.assertAlmostEqual(parts["old"]["end"], res["accounts"][aid]["revalued"], delta=0.02)
            for case in W.CASES:
                self.assertAlmostEqual(parts[case]["identity"], 0, places=4)
                self.assertAlmostEqual(parts[case]["end"], res["accounts"][aid][case], delta=0.02)
                self.assertAlmostEqual(parts[case]["vs_old"]["rest"], 0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
