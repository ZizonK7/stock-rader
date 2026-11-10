"""옛 주문을 그대로 넣어 다시 계산하기(market/whatif.carry, 2026-11-10 3회 — 사용자 결정 '판단은 기록에 남아 있으니 그때 가격으로
그대로'). 지시서 'C. 시험'의 1–4·7과, 갈아타는 날 검산(broker._start_errors)이 시작 잔고·끼운 주문·뺀 주문을 잡는지(5의 일부).
갈아타는 날 매일 실행 흉내(5·6)는 tests/test_broker_daily.py Switch.

F137(시험용 가짜가 실제와 같은 값을 주면 틀린 길도 통과한다): 가짜 옛 기록은 일부러 옛 장부라면 치러졌을 주문(같은 시각에 판 돈으로
사기, 결제 전 돈으로 비트코인 사기)을 넣어, 옛 장부 값과 다시 계산한 값이 달라지게 한다. 시세는 가짜다(네트워크 없음)."""
import copy
import dataclasses
import datetime as dt
import math
import unittest

from market import broker as B
from market import config as C
from market import quotes as Q
from market import rules as RU
from market import whatif as W
from tests import fixtures

E, U, BTC = "069500", "360750", "KRW-BTC"
ROWS = fixtures.market(dt.date(2024, 6, 1), dt.date(2025, 9, 30))
D2, D3, D4, D5, D6, D7, D8 = (dt.date(2025, 9, d) for d in (2, 3, 4, 5, 6, 7, 8))   # 9월 2일(화) … 8일(월)


def t(day, h, m, s=0):
    return dt.datetime.combine(day, dt.time(h, m, s), C.KST)


def bar(a, day, rows=ROWS):
    return next(r for r in rows[a] if r.date == day)


def prev_close(a, day, rows=ROWS):
    return [r.close for r in rows[a] if r.date < day][-1]


def old(aid, day, asset, side, qty, when, rule="open", tag=""):
    """옛 장부의 주문 줄 모양(pocket·seq 없음). rule None = 옛 규칙(fill_rule 없음 — 한국 거래소는 종가)."""
    o = {"id": f"{aid}:{day}:{asset}{tag}", "run": day.isoformat(), "account": aid, "asset": asset, "side": side,
         "qty_units": qty, "ref_price": float(prev_close(asset, day) if C.on_exchange(asset) else bar(asset, day).open),
         "reason": "옛 주문", "decided_at": when.isoformat(timespec="seconds")}
    if rule:
        o["fill_rule"] = rule
    return o


def runs(*whens):
    return [{"id": f"live:{w.date()}", "run": w.date().isoformat(), "mode": "live", "notes": [],
             "steps": {"decided_at": w.isoformat(timespec="seconds")}} for w in whens]


def do(orders, run_lines, until, rows=ROWS, book_day=None):
    return W.carry({"order": orders, "run": run_lines}, rows, until, None, book_day)


def step(K, oid):
    return next(s for s in K.steps if s["id"] == oid)


def lines_of(K, orders, run_lines):
    ls = {k: [d for b in K.brokers.values() for d in b.new[k]] for k in B.KINDS}
    ls["order"] = orders + ls["order"]
    ls["run"] = run_lines
    return ls


def fill_of(K, s):
    return next(f for b in K.brokers.values() for f in b.new["fill"] if f["order"] == s["order"])


def big_buy(asset, day, keep):
    """은행 1,000만 원에서 keep원쯤만 남기고 그날 장 시작에 살 수 있는 수량(기본 한도로 돈을 잡는다)."""
    lim = RU.default_open_limit(asset, prev_close(asset, day))
    return int((C.START_CASH - keep) // (lim * (1 + C.ASSETS[asset].fee)))


class Verbatim(unittest.TestCase):
    def test_c1_same_time_sale_cannot_fund_a_buy_and_a_later_sell_of_it_is_rejected(self):
        """같은 09:00에 판 돈으로 사는 옛 주문 → 사기 거절, 그 뒤 그 주식을 파는 옛 주문 → 가진 것이 없어 거절. 끝 상태 = 손으로 센 값."""
        n = big_buy(E, D2, 300_000)
        m = int(5_000_000 // prev_close(U, D3))
        orders = [old("live:hand", D2, E, "buy", n, t(D2, 7, 40)),
                  old("live:hand", D3, E, "sell", n, t(D3, 7, 40)),
                  old("live:hand", D3, U, "buy", m, t(D3, 7, 40)),
                  old("live:hand", D4, U, "sell", m, t(D4, 7, 40))]
        rl = runs(t(D2, 7, 40), t(D3, 7, 40), t(D4, 7, 40))
        K = do(orders, rl, t(D5, 7, 0))
        self.assertEqual([step(K, o["id"])["result"] for o in orders], ["체결", "체결", "거절", "거절"])
        self.assertIn("돈이 모자람", step(K, orders[2]["id"])["why"])
        self.assertIn("가진 것보다", step(K, orders[3]["id"])["why"])
        b = K.brokers["live:hand"]
        vb = math.ceil(n * bar(E, D2).open)
        vs = math.floor(n * bar(E, D3).open)
        want = C.START_CASH - vb - RU.fee(E, vb) + vs - RU.fee(E, vs)
        self.assertEqual(sum(b.pockets().values()), want)
        self.assertEqual(b.holdings(), {})
        self.assertEqual([p.id for p in b.pending()], [])
        # 옛 장부라면 같은 시각에 판 돈으로 미국 묶음을 샀다(그 몫이 다시 계산에서는 없다 — 두 값이 달라진다)
        self.assertGreater(m * bar(U, D3).open, b.pockets()["bank"])
        self.assertEqual(B.check(lines_of(K, orders, rl), ROWS)[0], [])
        # 다른 계좌는 옛 주문이 없어 1,000만 원 그대로, 계좌마다 start는 첫 옛 실행 시각
        for aid in C.LIVE_ACCOUNTS:
            st = K.brokers[aid].new["broker"][0]
            self.assertEqual((st["type"], st["at"], st["data"]["pockets"]), ("start", t(D2, 7, 40).isoformat(),
                                                                             {"bank": C.START_CASH, "sec": 0, "upbit": 0}))
            self.assertEqual(st["run"], D5.isoformat())   # 장부에 넣은 날 = 갈아타는 날(until)

    def test_c2_each_kind_is_paid_at_its_official_price_and_weekend_orders(self):
        """옛 종가 주문 = 그 장의 종가, 옛 시가 주문 = 그 장의 시가, 비트코인 = 옛 규칙의 그 09:00 시가. 토요일에 낸 옛 종가 주문은
        월요일 08:30에 장 마감 주문으로, 토요일의 장 시작 주문은 그 시각에 월요일 장으로, 토요일 09:00 뒤의 비트코인은 일요일 09:00에."""
        a = "live:calc"
        orders = [old(a, D2, E, "buy", 10, t(D2, 7, 40), rule=None),
                  old(a, D3, U, "buy", 10, t(D3, 7, 40)),
                  old(a, D3, BTC, "buy", 1_000_000, t(D3, 7, 40)),
                  old(a, D6, E, "sell", 5, t(D6, 8, 0), rule=None),
                  old(a, D6, U, "sell", 5, t(D6, 8, 0)),
                  old(a, D6, BTC, "sell", 500_000, t(D6, 10, 0), tag=":late")]
        rl = runs(t(D2, 7, 40), t(D3, 7, 40), t(D6, 8, 0))
        K = do(orders, rl, t(dt.date(2025, 9, 9), 7, 0))
        want = [(D2, "close", bar(E, D2).close, t(D2, 15, 30)), (D3, "open", bar(U, D3).open, t(D3, 9, 0)),
                (D3, "now", None, t(D3, 9, 0, 5)), (D8, "close", bar(E, D8).close, t(D8, 15, 30)),
                (D8, "open", bar(U, D8).open, t(D8, 9, 0)), (D7, "now", None, t(D7, 9, 0, 5))]
        for o, (day, kind, price, when) in zip(orders, want):
            s = step(K, o["id"])
            self.assertEqual(s["result"], "체결", (o["id"], s))
            f = fill_of(K, s)
            self.assertEqual((f["fill_date"], f["price_kind"], f["at"]), (day.isoformat(), kind, when.isoformat()), o["id"])
            if price is not None:
                self.assertEqual(f["price"], price, o["id"])
            else:   # 비트코인: 그 봉의 시가에, 기록된 수량 그대로(원 단위 금액이라 1e-8 한 단위·1원까지)
                self.assertLessEqual(abs(f["value"] - f["qty_units"] * bar(BTC, day).open / 1e8), 1.0)
                self.assertLessEqual(abs(f["qty_units"] - o["qty_units"]), 1)
        # 토요일 옛 종가 주문은 월요일 08:30에 냈다(토요일엔 장 마감 주문을 받지 않는다), 장 시작 주문은 토요일 그 시각에
        self.assertTrue(step(K, orders[3]["id"])["at"].startswith("2025-09-08T08:30"))
        self.assertIn("moved", step(K, orders[3]["id"]))
        self.assertTrue(step(K, orders[4]["id"])["at"].startswith("2025-09-06T08:00"))
        self.assertEqual(B.check(lines_of(K, orders, rl), ROWS)[0], [])

    def test_c3_coin_bought_with_unsettled_exchange_sale_is_rejected_until_settlement(self):
        """2026-11-05 사례: 증권 계좌에서 판 돈으로 같은 시각·결제 전에 비트코인을 사는 옛 주문 → 거절. 결제일 09:00 뒤의 주문은 받아짐."""
        n = big_buy(E, D2, 50_000)
        sale = n * prev_close(E, D3)
        coin = lambda day, share: int(sale * share / bar(BTC, day).open * 1e8)
        orders = [old("live:hand", D2, E, "buy", n, t(D2, 7, 40)),
                  old("live:hand", D3, E, "sell", n, t(D3, 7, 40)),
                  old("live:hand", D3, BTC, "buy", coin(D3, 0.8), t(D3, 7, 40)),    # 같은 09:00에 판 돈으로
                  old("live:hand", D4, BTC, "buy", coin(D4, 0.8), t(D4, 7, 40)),    # 결제(9월 5일) 전
                  old("live:hand", D5, BTC, "buy", coin(D5, 0.5), t(D5, 7, 40))]    # 결제일 09:00 뒤
        rl = runs(t(D2, 7, 40), t(D3, 7, 40), t(D4, 7, 40), t(D5, 7, 40))
        K = do(orders, rl, t(D8, 7, 0))
        self.assertEqual([step(K, o["id"])["result"] for o in orders], ["체결", "체결", "거절", "거절", "체결"])
        self.assertIn("upbit", step(K, orders[2]["id"])["why"])
        moves = [d for d in K.brokers["live:hand"].new["broker"] if d["type"] == "transfer"]
        self.assertTrue(any(d["data"]["src"] == "sec" and d["at"].startswith("2025-09-05T09:00") for d in moves), moves)
        self.assertEqual(B.check(lines_of(K, orders, rl), ROWS)[0], [])

    def test_c4_buy_limit_is_lowered_to_what_the_money_allows_but_not_below_the_previous_close(self):
        prev = prev_close(E, D2)
        top = RU.default_open_limit(E, prev)
        q_ok = int(9_990_000 // prev)                   # 전 거래일 종가로는 살 돈이 있고, 기본 한도로는 없다
        q_no = int(10_100_000 // prev)                  # 전 거래일 종가로도 모자람
        self.assertGreater(q_ok * top, C.START_CASH)
        orders = [old("live:hand", D2, E, "buy", q_ok, t(D2, 7, 40)), old("live:calc", D2, E, "buy", q_no, t(D2, 7, 40))]
        rl = runs(t(D2, 7, 40))
        K = do(orders, rl, t(D3, 7, 0))
        ok, no = step(K, orders[0]["id"]), step(K, orders[1]["id"])
        self.assertNotEqual(ok["result"], "거절", ok)
        self.assertTrue(prev <= ok["limit"] < top and RU.on_tick(E, ok["limit"]), ok)
        self.assertIn("낮춤", ok["limit_note"])
        line = next(o for o in K.brokers["live:hand"].new["order"])
        self.assertEqual((line["limit"], line["replay_of"]), (ok["limit"], orders[0]["id"]))
        self.assertEqual(no["result"], "거절")
        self.assertIn("돈이 모자람", no["why"])
        rej = next(d for d in K.brokers["live:calc"].new["broker"] if d["type"] == "reject")
        self.assertEqual((rej["data"]["limit"], rej["data"]["replay_of"]), (top, orders[1]["id"]))
        # 한도 함수 하나만: 돈이 넉넉하면 기본 한도, 한 칸 모자라면 낮춘 한도, 종가 아래면 기본 한도(→ 거절)
        need = lambda lim: math.ceil(q_ok * lim) + RU.fee(E, math.ceil(q_ok * lim))
        self.assertEqual(W.buy_limit(E, q_ok, prev, need(top))[0], top)
        lim, _ = W.buy_limit(E, q_ok, prev, need(top) - 1)
        self.assertEqual(lim, RU.step(E, top, -1))
        self.assertEqual(W.buy_limit(E, q_ok, prev, need(prev) - 1)[0], top)
        self.assertEqual(W.buy_limit(E, q_ok, None, 10**9), (None, ""))
        self.assertEqual(B.check(lines_of(K, orders, rl), ROWS)[0], [])

    def test_c7_decisions_do_not_use_values_after_their_time(self):
        """그대로 넣기에서 주문을 낼 때의 판단(한도·돈 옮기기·받음/거절)은 그 시각 뒤의 값을 쓰지 않는다: 9월 4일 뒤 봉을 바꿔도
        그 전에 낸 주문의 받음/거절·한도·옮긴 돈이 같다. 체결 값은 그 봉의 공식 값이라 바뀐 봉의 주문은 달라지는 것이 맞다."""
        cut = D4
        bent = {a: tuple(r if r.date <= cut else dataclasses.replace(r, open=r.open * 1.5, high=r.high * 1.5, low=r.low * 1.5,
                                                                      close=r.close * 1.5) for r in rs) for a, rs in ROWS.items()}
        n = big_buy(E, D2, 300_000)
        orders = [old("live:hand", D2, E, "buy", n, t(D2, 7, 40)),
                  old("live:hand", D3, E, "sell", n // 2, t(D3, 7, 40)),
                  old("live:hand", D3, U, "buy", 200, t(D3, 7, 40)),
                  old("live:hand", D4, BTC, "buy", 2_000_000, t(D4, 7, 40)),
                  old("live:hand", D5, U, "buy", 100, t(D5, 7, 40)),
                  old("live:hand", D8, E, "sell", n // 4, t(D8, 7, 40))]
        rl = runs(*(t(d, 7, 40) for d in (D2, D3, D4, D5, D8)))
        a = do(orders, rl, t(dt.date(2025, 9, 10), 7, 0))
        b = do(orders, rl, t(dt.date(2025, 9, 10), 7, 0), rows=bent)
        early = lambda K: [(s["id"], s["at"], s["result"], s.get("limit"), s.get("moves")) for s in K.steps
                           if s["at"] < cut.isoformat() + "T23:59"]
        self.assertEqual(early(a), early(b))
        self.assertTrue(early(a))
        moves = lambda K: [d for d in K.brokers["live:hand"].new["broker"] if d["type"] == "transfer"
                           and d["at"] < cut.isoformat() + "T23:59"]
        self.assertEqual(moves(a), moves(b))
        # 뒤 날짜의 체결 값은 실제로 달라졌다(바꾼 봉이 쓰였다 — 시험이 헛돌지 않게)
        self.assertNotEqual(fill_of(a, step(a, orders[5]["id"]))["price"], fill_of(b, step(b, orders[5]["id"]))["price"])

    def test_unpaid_old_order_stays_on_the_book_after_the_switch(self):
        """갈아타는 날에 아직 치러지지 않은 옛 주문(토요일에 낸 월요일 장 시작 주문, 월요일 07:00에 갈아탐)은 증권사에 걸린 주문으로
        넘어가고, 갈아탄 뒤 맞추기(src = 시세 파일)에서 그 장의 시가로 치러진다."""
        orders = [old("live:hand", D6, E, "buy", 10, t(D6, 8, 0))]
        K = do(orders, runs(t(D6, 8, 0)), t(D8, 7, 0))
        s = step(K, orders[0]["id"])
        self.assertEqual(s["result"], "걸려 있음")
        b = K.brokers["live:hand"]
        self.assertEqual([(p.asset, p.qty, str(p.session)) for p in b.pending()], [(E, 10, D8.isoformat())])
        b.sync(t(D8, 9, 30))
        f = fill_of(K, s)
        self.assertEqual((f["fill_date"], f["price"]), (D8.isoformat(), bar(E, D8).open))

    def test_coin_waits_when_its_nine_oclock_has_not_come_or_has_no_value(self):
        """그 09:00이 아직 오지 않은 비트코인 옛 주문은 내지 않고 기다린다(갈아타기는 그날 옛 장부로). 진행 중인 봉은 src에서 받은
        시가만 쓴다(시세 파일에 없는 어제 봉)."""
        orders = [old("live:hand", D3, BTC, "buy", 1_000_000, t(D3, 9, 30))]   # 9시 뒤 → 다음 날 09:00
        rl = runs(t(D3, 9, 30))
        K = do(orders, rl, t(D4, 7, 0))
        self.assertEqual([w["id"] for w in K.waiting], [orders[0]["id"]])
        cut = {a: tuple(r for r in rs if r.date < D4) for a, rs in ROWS.items()}   # 9월 4일 봉은 시세 파일에 없다
        K = W.carry({"order": orders, "run": rl}, cut, t(D5, 7, 0), Q.BarQuotes(ROWS))   # 진행 중인 봉 = src에서
        self.assertEqual((K.waiting, step(K, orders[0]["id"])["result"]), ([], "체결"))

        class NoRunning(Q.BarQuotes):
            def day(self, asset, date, at):
                got = super().day(asset, date, at)
                return None if got is not None and not got.final else got
        K = W.carry({"order": orders, "run": rl}, cut, t(D5, 7, 0), NoRunning(ROWS))
        self.assertEqual([w["id"] for w in K.waiting], [orders[0]["id"]])


class StartCheck(unittest.TestCase):
    """갈아타는 날 검산(2026-11-10 3회): 진짜 계좌의 start = 첫 옛 실행 시각의 은행 1,000만 원, 갈아타는 시각 전의 증권사 주문 줄이
    옛 주문 줄과 하나씩 맞는다. 손으로 시작 잔고를 부풀리거나 주문을 끼우거나 빼면 걸린다."""

    def setUp(self):
        n = big_buy(E, D2, 300_000)
        self.orders = [old("live:hand", D2, E, "buy", n, t(D2, 7, 40)),
                       old("live:hand", D3, E, "sell", n, t(D3, 7, 40)),
                       old("live:hand", D3, U, "buy", 200, t(D3, 7, 40))]   # 같은 시각에 판 돈으로 — 거절
        self.runs = runs(t(D2, 7, 40), t(D3, 7, 40))
        self.K = do(self.orders, self.runs, t(D5, 7, 0))
        self.lines = lines_of(self.K, self.orders, self.runs)
        self.assertEqual(B.check(self.lines, ROWS)[0], [])

    def errors(self, edit):
        ls = copy.deepcopy(self.lines)
        edit(ls)
        return B.check(ls, ROWS)[0]

    def hand(self, ls, kind):
        return [d for d in ls[kind] if d.get("account") == "live:hand" and "seq" in d]

    def test_inflated_start(self):
        def edit(ls):
            st = next(d for d in ls["broker"] if d["account"] == "live:hand" and d["type"] == "start")
            st["data"]["pockets"]["bank"] += 1_000_000
        self.assertTrue(any("시작 잔고" in e for e in self.errors(edit)))

        def old_style(ls):   # 고치기 전 갈아타기: 옛 장부의 보유를 그대로 들고 시작
            st = next(d for d in ls["broker"] if d["account"] == "live:hand" and d["type"] == "start")
            st["data"]["units"] = {E: 3}
        self.assertTrue(any("시작 잔고" in e for e in self.errors(old_style)))

        def late(ls):
            st = next(d for d in ls["broker"] if d["account"] == "live:hand" and d["type"] == "start")
            st["at"] = t(D2, 7, 45).isoformat()
        self.assertTrue(any("첫 옛 실행" in e for e in self.errors(late)))

    def test_inserted_order(self):
        def edit(ls):
            d = copy.deepcopy(next(o for o in self.hand(ls, "order") if o.get("replay_of")))
            del d["replay_of"]
            d.update(id=d["id"] + ":x", seq=max(x["seq"] for k in B.KINDS for x in ls[k]
                                              if x.get("account") == "live:hand" and "seq" in x) + 1)
            ls["order"].append(d)
        self.assertTrue(any("끼워 넣은" in e for e in self.errors(edit)))

    def test_missing_or_changed_replay(self):
        def drop(ls):   # 거절된 옛 주문 줄을 빼면(그 주문이 없던 것처럼)
            ls["broker"] = [d for d in ls["broker"] if not (d["type"] == "reject" and d["data"].get("replay_of"))]
        self.assertTrue(any("하나씩 맞지 않음" in e for e in self.errors(drop)))

        def change(ls):   # 다시 넣은 거절 줄의 수량을 바꾸면
            r = next(d for d in ls["broker"] if d["type"] == "reject" and d["data"].get("replay_of"))
            r["data"]["qty"] += 1
        self.assertTrue(any("수량" in e for e in self.errors(change)))

        def no_carry(ls):   # 갈아타는 시각 표시가 없으면
            ls["broker"] = [d for d in ls["broker"] if not (d["type"] == "phase" and d["data"].get("name") == B.CARRY)]
        self.assertTrue(any(B.CARRY in e for e in self.errors(no_carry)))

    def test_fresh_live_account_starts_with_the_start_cash(self):
        b = B.Broker("live:hand", Q.BarQuotes(ROWS))
        b.start(t(D2, 7, 0), bank=C.START_CASH + 1_000_000)
        self.assertTrue(any("시작 잔고" in e for e in B.check(b.lines(), ROWS)[0]))
        b = B.Broker("live:hand", Q.BarQuotes(ROWS))
        b.start(t(D2, 7, 0), bank=C.START_CASH)
        self.assertEqual(B.check(b.lines(), ROWS)[0], [])


if __name__ == "__main__":
    unittest.main()


class EqualHoldBuysWhatItCan(unittest.TestCase):
    """2회차(메인 결정): 기준선 셋 나눠 사 두기(baseline:equal_hold)는 판단이 아니라 규칙이라, 돈이 모자란 사기는 '그때 쓸 수 있는 돈
    안에서 살 수 있는 만큼' 산다. 손 계좌·계산기 계좌는 그대로 통째로. F137: 같은 모양의 주문이 두 계좌에서 다른 결과(줄여서 받음 /
    통째로 거절)를 내야 시험이 길을 가른다."""

    def first_day(self, aid):
        """옛 장부 첫날처럼: 장 마감 규칙(옛 종가 주문) 한국 거래소 사기 둘 + 비트코인 사기 하나, 각각 약 1/3. 한도(전날 마감 × 1.05)로
        잡은 돈 때문에 비트코인 몫이 모자란다."""
        third = C.START_CASH / 3
        e = int(third // (prev_close(E, D2) * 1.00015))
        u = int(third // (prev_close(U, D2) * 1.00015))
        c = int(third * 0.999 / bar(BTC, D2).open * 1e8)
        return [old(aid, D2, E, "buy", e, t(D2, 7, 40), rule=None), old(aid, D2, U, "buy", u, t(D2, 7, 40), rule=None),
                old(aid, D2, BTC, "buy", c, t(D2, 7, 40))]

    def setUp(self):
        self.orders = self.first_day("baseline:equal_hold") + self.first_day("live:hand")
        self.runs = runs(t(D2, 7, 40))
        self.K = do(self.orders, self.runs, t(D4, 7, 0))

    def test_a1_baseline_buys_what_it_can_and_the_hand_account_does_not(self):
        eq = [step(self.K, o["id"]) for o in self.orders[:3]]
        hand = [step(self.K, o["id"]) for o in self.orders[3:]]
        self.assertEqual([s["result"] for s in eq], ["체결", "체결", "체결"])
        coin = eq[2]
        self.assertIn("shrunk", coin)
        f = fill_of(self.K, coin)
        self.assertLess(f["qty_units"], self.orders[2]["qty_units"])
        self.assertGreater(f["qty_units"], 0)
        self.assertEqual([s["result"] for s in hand], ["체결", "체결", "거절"])   # 같은 모양인데 손 계좌는 통째로 거절
        self.assertNotIn("shrunk", hand[2])
        carry = next(d for d in self.K.brokers["baseline:equal_hold"].new["broker"]
                     if d["type"] == "phase" and d["data"]["name"] == B.CARRY)
        self.assertEqual(carry["data"]["short"], [BTC])
        self.assertEqual(B.check(lines_of(self.K, self.orders, self.runs), ROWS)[0], [])

    def test_a2_the_switch_day_buys_the_short_part(self):
        from market import daily as D
        eb = self.K.brokers["baseline:equal_hold"]
        prices = {E: prev_close(E, D4), U: prev_close(U, D4), BTC: bar(BTC, D3).close}
        self.assertEqual(D.equal_targets(self.orders, eb, prices, [E, U, BTC]), {BTC: 1 / 3})

    def test_a3_check_allows_a_smaller_replay_only_for_baseline_buys(self):
        def errs(edit):
            ls = copy.deepcopy(lines_of(self.K, self.orders, self.runs))
            edit({o["id"]: o for o in ls["order"] if "seq" not in o})
            return B.check(ls, ROWS)[0]
        eq_e, hand_e = self.orders[0]["id"], self.orders[3]["id"]

        def grow(oid):   # 옛 주문을 더 크게 → 다시 넣은 줄이 옛 수량보다 작다(줄여서 넣은 것처럼)
            return lambda old: old[oid].update(qty_units=old[oid]["qty_units"] + 3)

        def cut(oid):    # 옛 주문을 더 작게 → 다시 넣은 줄이 옛 수량보다 크다(늘려서 넣은 것처럼)
            return lambda old: old[oid].update(qty_units=old[oid]["qty_units"] - 3)
        self.assertEqual(errs(grow(eq_e)), [])
        self.assertTrue(any("수량" in e for e in errs(cut(eq_e))))
        self.assertTrue(any("수량" in e for e in errs(grow(hand_e))))
        self.assertTrue(any("수량" in e for e in errs(cut(hand_e))))
