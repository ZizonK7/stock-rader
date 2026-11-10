# 2026-11-10 가상 증권사 1회차 — 핵심 모듈과 시험

작업자: 구현 작업자(하위 에이전트, 지시서 `briefs/impl1.md`). 설계 기준: [docs/broker-sandbox.md](../broker-sandbox.md) 13–19절. 커밋·push는 하지 않았다(메인이 한다). 브랜치 `claude/wonderful-clarke-bq5e7s` 그대로, 시작 HEAD `84be0e0`.

## 한 일
- **`market/rules.py`(새 파일)**: 19절 표의 규칙 25개 + 표 밖 1개(`quote_clock_skew`, 아래 '메인 판단 필요' 6). 규칙마다 `id`·값·`status`(확인/두 곳 일치/확인 전/가정/설계 결정/확인 중)·`source`·`checked`(2026-11-10). '확인 중'(p308 조사 중인 거래소·증권 계좌 값 7개)은 표의 값을 쓰고 상태를 그대로 적었다. 함수: 호가 단위(`tick`·`on_tick`·`floor_tick`·`ceil_tick`·`step` — 아래로 한 칸은 바로 아래 구간의 단위, 2,000 → 1,999), 가격 제한 폭(`price_limits`, 위 끝 내림·아래 끝 올림), 영업일(`is_business_day`·`next_business_day`·`add_business_days` — 넘겨받은 봉 날짜가 덮는 날은 봉으로, 그 뒤는 주말 + 휴장일 표), 결제일(`settle_date`), 출금 시각(`settled_by` — 결제일 09:00 '뒤'), 표와 시세 파일이 어긋나는 날 경고(`calendar_warnings`), 수수료·세금(`fee`·`sell_tax`), 장 시작 사기 기본 한도(`default_open_limit`).
- **`market/quotes.py`(새 파일)**: 공급자 `Quotes`(`now`·`book`·`day`·`minute`·`known_days`)와 값 모양(`Now`·`Book`·`Day`·`Minute`). `NetQuotes` = 13절 주소들(업비트 ticker·orderbook·candles/days·candles/minutes/1, 네이버 fchart·polling realtime·api.stock 분봉; 네이버 호가 주소는 쓰지 않음). 시간 제한·User-Agent는 `prices._get` 그대로, 실패하면 None, 같은 `at`의 같은 주소는 한 번만 받음, **지금 값·호가는 실제 시계 ±5분의 at에만 줌**(흉내 내기에서 NetQuotes를 잘못 끼워 지난 시각에 오늘 값을 쓰는 일을 막음). 응답 → 값은 순수 함수 `parse_upbit_ticker/book/days/minutes`, `parse_naver_days/now/minutes`. `BarQuotes` = 일봉 가짜(09:00 전 그날 봉 없음, 끝나기 전 시가만, 끝난 뒤 전부; 지금 값은 장중 시가·장 밖 마지막 종가; 업비트 호가는 지금 값 둘레 `book_sizes` 단계; 늦은 값 시험용 `stale`).
- **`market/broker.py`(새 파일)**: 상태 `State`(주머니 셋, 증권 계좌 결제 흐름, 보유, 산 값의 합, 걸린 주문, 같은 시각 체결 몫 `fresh`)를 기록 줄에서 다시 계산. `Broker`: `start`, `sync`(체결·만료·진행 중인 봉 값 검산 → `adjust`), `order`(open/close/now, 한국 거래소·업비트), `cancel`, `transfer`, `mark`(plan/phase), `snapshot`(옛 account 모양 + `pockets`·`priced_at`·`at`·`seq`), 조회(`orderable`·`withdrawable`·`holdings`·`avg_cost`·`pending`·`pockets`), 쓰기 도우미 `write`(모든 새 줄을 `records.line_errors`로 검사한 뒤 종류별로 덧붙임), `Broker.load`. 검산 `check(lines, bars)`·`check_folder`. 요청 함수는 모두 먼저 `sync(at)`을 부른다(P8-2: 알 수 있게 된 체결은 바로 장부에). 안 되는 요청은 예외가 아니라 `Result(ok=False, reason)` + `broker` 줄 `reject`.
- **`market/records.py` 고침**: 새 종류 `broker`(account·at·type·data·note·seq), 있을 때만 보는 칸 — order: pocket·order_kind·limit·amount·reserved·session·seq, fill: at·pocket·tax·source·settle·gain·seq, account: pockets·priced_at·at·seq. `PRICE_KINDS`에 now·limit, 낱말 `POCKETS`·`ORDER_KINDS`·`BROKER_TYPES`(broker.py가 이것을 씀), 증권사 줄의 모양 검사(pocket이 있는 주문은 order_kind = fill_rule ∈ open/close/now, 시각 칸은 시간대 붙은 ISO, 세금 음수 금지, account의 cash = 세 주머니 합), `DESCRIBE`. **`records.check`가 broker.jsonl이 있는 폴더마다 `broker.check_folder`를 부른다**(15절 'records check가 본다', F051). 옛 줄(더한 칸 없음)은 그대로 통과한다.
- **시험** `tests/test_broker.py`(25개), `tests/test_quotes.py`(10개). 아래 표.
- **문서**: `market/CLAUDE.md`(표에 세 파일, '결정과 이유'에 가상 증권사, '규칙'에 broker 줄은 broker.py만), `tests/CLAUDE.md`(두 파일, 시험 수), `records/CLAUDE.md`(broker 종류와 order·fill·account의 더한 칸, records check의 증권사 검산). 루트 CLAUDE.md·`docs/routine-prompt.md`·`daily.py`·`report.py`·`hand.py`·`ledger.py`·`scoring.py`와 `records/`·`reports/`·`data/prices/` 아래는 건드리지 않았다.

### 시험 목록 번호 → 시험 이름
| 번호 | 시험 |
|---|---|
| 1 | `test_broker.Accounts.test_t01_start_state_and_bank_to_sec_is_instant` |
| 2 | `test_broker.OpenOrders.test_t02_open_buy_reserves_fills_below_limit_refunds_and_settles_d_plus_2` |
| 3 | `test_broker.OpenOrders.test_t03_short_money_rejects_the_whole_order` |
| 4 | `test_broker.OpenOrders.test_t04_selling_and_buying_with_the_same_nine_oclock_money_is_rejected` (+ `test_t04b_same_time_fill_money_cannot_be_used_at_that_time` — T1을 업비트로) |
| 5 | `test_broker.OpenOrders.test_t05_case_2026_11_05_etf_money_cannot_buy_coin_until_settled` |
| 6 | `test_broker.OpenOrders.test_t06_cannot_sell_more_than_held_minus_pending_sells` |
| 7 | `test_broker.NowOrders.test_t07_krx_now_order_needs_open_market_and_a_fresh_price` |
| 8 | `test_broker.Rules.test_t08_tick_boundaries_and_limit_price_rules` |
| 9 | `test_broker.NowOrders.test_t09_upbit_now_walks_the_book_with_fee_and_8_digit_units` |
| 10 | `test_broker.OpenOrders.test_t10_open_buy_at_limit_hangs_then_fills_at_limit_or_expires` |
| 11 | `test_broker.OpenOrders.test_t11_close_order_only_before_1520_fills_at_close` |
| 12 | `test_broker.Rules.test_t12_taxes_on_selling` |
| 13 | `test_broker.Money.test_t13_withdrawable_by_date` |
| 14 | `test_broker.Accounts.test_t14_transfers_fee_routes_and_limits` |
| 15 | `test_broker.Honesty.test_t15_late_insert_equals_immediate` |
| 16 | `test_broker.Check.test_t16_check_catches_forged_price_negative_pocket_adjust_and_now_range` + `test_t16b_in_progress_open_that_differs_makes_an_adjust_line_and_a_warning` |
| 17 | `test_broker.Money.test_t17_past_time_rejected_and_cancel_only_before_fill_time` |
| 18 | `test_broker.Honesty.test_t18_poison_future_bars_does_not_change_results` |
| 19 | `test_broker.Rules.test_t19_every_rule_has_status_source_and_date_and_the_doc_table_ids_exist` (설계 문서 19절 표를 읽어 id를 대조 — 문서와 코드가 갈라지면 실패) |
| 20 | `test_broker.Records.test_t20_new_kinds_pass_records_check_and_old_lines_still_pass` |
| 21 | `test_quotes.Parse.*`(7개), `test_quotes.Net.*`(2개), `test_quotes.Bars.test_t21_bar_quotes_are_honest_about_time` (+ `test_broker.Honesty.test_bar_quotes_are_honest_about_time`) |
| 더함 | `test_broker.Rules.test_business_days_and_calendar_warnings`(휴장일·연말 결제일, 봉 날짜로 센 지난 날), `test_broker.Records.test_only_broker_writes_broker_lines`(14절 '기록 줄은 여기뿐' — 지금은 broker 종류만) |

### 미래 정보를 어떻게 막았나
- 증권사가 가격을 보는 길은 `quotes` 공급자의 `now(asset, at)`·`book(asset, at)`·`day(asset, date, at)`·`known_days(at)`뿐이고, 증권사 함수는 모두 자기 `at`을 그대로 넘긴다. 공급자는 그 시각에 알 수 있는 값만 준다: `BarQuotes`는 그날 09:00 전엔 그날 봉을 주지 않고, 봉이 끝나기 전(config.final_dt)엔 고가·저가·종가를 None으로 준다. `NetQuotes`는 `at`보다 뒤에 시작한 봉을 버리고, 지금 값·호가는 실제 시계와 5분 넘게 다른 `at`에는 주지 않는다.
- `sync(at)`은 체결 시각이 `at` 이하인 것만 만든다(장 시작 = 그 장 09:00, 장중 한도·만료·장 마감 = 15:30이고 봉이 끝난 뒤에만). 결제일은 체결 때 정해 줄에 적고(`settle`), 다시 계산할 때는 적힌 값을 쓴다(나중에 휴장일 표가 바뀌어도 지난 상태가 바뀌지 않게).
- 같은 시각의 체결 돈·수량은 같은 시각의 요청에 못 쓴다(`State.fresh`).
- 요청 시각이 그 계좌의 마지막 사건보다 이르면 거절(지난 시각에 끼워 넣기 금지). 시각에 시간대가 없으면 예외.
- 독 넣기(t18): 판단 시각 뒤의 봉 값을 세 배로, 그날 아직 안 끝난 봉의 고가·저가·종가를 바꿔도 장 시작·지금 주문·업비트·평가 줄이 글자 그대로 같다.

## 확인한 것 (명령과 결과)
- 시작 때 `python -m unittest discover -s tests -t .` → `Ran 155 tests … OK (skipped=21)`. `python -m market.records check` → `기록 검사 통과`.
- 끝날 때 `python -m unittest discover -s tests -t .` → `Ran 190 tests in 17.8s OK (skipped=21)` (새 35개, 건너뜀 21개는 시작 때와 같은 것).
- `python -m unittest tests.test_broker tests.test_quotes` → `Ran 35 tests … OK`.
- `python -m market.records check` → `기록 검사 통과` (지금 records/에는 broker.jsonl이 없어 증권사 검산은 돌지 않는다. 증권사 검산이 records check에서 도는 것은 t20이 임시 폴더로 확인: 깨끗한 줄은 통과, 체결가를 손으로 바꾸면 실패).
- `PROVISO_PATH=/home/user/proviso-nesy python -m market.logic validate` → `문제 없음`.
- 지시서 '돌리지 말 것' 명령은 돌리지 않았다. `records/`·`reports/`·`data/prices/`에 쓴 것 없음(`git status`: records.py 고침, 새 파일 다섯 + 문서 넷).
- 피드백 `/home/user/proviso-nesy/feedback/main/*.md` 15개 파일을 모두 열어 읽었다.

## 확인 못 한 것
- **실제 주소 호출 전부**(이 세션 네트워크는 막혀 있음, 지시서대로 시도하지 않음). 응답 → 값 변환은 예시 응답으로만 시험했다. 칸 이름별로:
  - 업비트: `trade_price`·`trade_timestamp`·`opening_price`·`timestamp`·`orderbook_units[].ask_price/bid_price/ask_size/bid_size`·`candle_date_time_kst`·`high_price`·`low_price`는 지시서·업비트 공개 문서·`prices.py`와 같은 이름이다. 분봉 `to`의 꼴은 `prices.py`처럼 "YYYY-MM-DD HH:MM:SS"(UTC)로 보냈다 — 이 꼴이 분봉 주소에서도 받아지는지는 확인 못 함.
  - 네이버 지금 가격(`polling.finance.naver.com/…/stock/<코드>`): **겹 구조와 시각 칸 이름을 확인 못 했다.** `closePrice`가 든 사전을 겹 4단까지 찾고, 시각은 그 사전의 `localTradedAt`·`tradedAt`·`localDateTime`·`time` 중 첫 것, 없으면 겉의 `time`(YYYYMMDDHHMMSS)으로 읽는다. 시각을 못 찾으면 None → 지금 주문 거절(좁게). 장 상태는 `marketStatus == "OPEN"`으로 읽는다(칸 이름 확인 못 함). 그 시각이 '마지막 체결 시각'인지 '응답 시각'인지도 모른다.
  - 네이버 분봉(`api.stock.naver.com/…/minute`): 칸 이름을 확인 못 했다. 사전이면 `localDateTime`(등)·`highPrice`·`lowPrice`, 목록이면 [시각, 시가, 고가, 저가, 종가, 거래량]으로 읽는다. 분봉은 지금 증권사 흐름에서 쓰지 않는다(검산은 일봉 저가–고가).
  - 네이버 fchart 진행 중인 봉은 `prices.parse_naver` 그대로라 모양은 같다고 보지만, 장중에 마지막 줄이 실제로 오늘 날짜로 오는지는 이 세션에서 보지 못했다(지시서의 다른 세션 보고에 따름).
- 지시서 '먼저 읽을 것' 4의 **F137**(시험에만 있고 실제 쓸 때 없는 입력)은 `feedback/main/*.md`에 없었다(F133·F135까지). 읽지 못했으므로 뜻은 지시서 괄호로만 따랐다: `BarQuotes`의 `stale`·`book_sizes`는 시험·흉내 내기 전용이고, 실제 쓸 때 `NetQuotes`에는 그런 입력이 없다.
- 19절이 가리키는 조사 원문 목록 `docs/worklog/2026-11-10-broker-rules-research.md`가 저장소에 없다(링크가 끊김). 규칙의 `source` 글은 19절 표의 '근거' 칸과 1–12절 출처로 썼다.

## 알려진 문제
- 다시 계산은 seq(증권사가 줄을 만든 순서) 순서다. 요청끼리는 seq 순서 = 시각 순서(지난 시각 요청을 거절하므로)지만, 늦게 넣은 체결은 seq로는 뒤다. 돈·수량은 더하기라 같지만 **평균 산 값(이동 평균)과 그로 정한 해외 지수 ETF 세금은 '알게 된 순서'로 계산된다** — 같은 대상의 늦은 사기 체결과 그 사이 팔기가 겹칠 때만 시각 순서 계산과 다를 수 있다(메인 판단 필요 2).
- 진행 중인 봉 시가가 한도보다 낮아 장 시작 사기로 넣었는데 끝난 봉 시가가 한도 이상이면, 체결을 되돌리지 않고 값만 `adjust`로 바로잡는다(실제로는 체결되지 않았을 주문). 드물지만 경고 글에 따로 표시하지 않았다.
- `adjust` 줄이 돈을 줄이는 쪽이면 그 사이에 받은 주문의 '받을 때 조건'이 뒤에서 깨질 수 있다(검산은 seq 순서라 받을 때의 상태로 보므로 오류로 잡히지 않는다 — 실제로 받은 때의 판단이 맞았기 때문).
- `Broker(...)`가 지난 줄을 다시 돌리다 어긋남을 찾으면 예외 대신 `problems` 목록에 담는다. 2회차에서 매일 실행이 problems가 있으면 멈추게 해야 한다.
- 계좌 줄 id가 옛 장부처럼 `계좌:날짜`라, 같은 날 두 번(판단 때 + 장 시작 뒤 단계)은 못 만든다(두 번째는 ok=False). 갈아타는 날 옛 장부 계좌 줄과도 id가 겹친다.
- `BarQuotes.known_days`는 첫 거래소 대상(보통 069500)의 봉 날짜만 본다.
- 검산 `check`는 계좌 줄 수에 비례하지만, 매 `Broker` 생성 때 모든 줄을 다시 돈다(몇 년 쌓여도 수천 줄이라 문제 없다고 봄, 재 보지 않음).
- `market/broker.py`가 `records.py`를 부르고, `records.check`가 함수 안에서 `broker`를 부른다(서로 부르기 — 모듈 머리에서는 부르지 않아 순환 import는 없다).

## 다음 작업자에게 (2회차)
- `daily.py`에 이을 때: 계좌마다 `Broker.load(root.records, 계좌, NetQuotes())`(흉내 내기는 `BarQuotes(시세 파일)`), 요청 → 거절이면 이유를 보고 다시 계획, 끝에 `write`를 `daily.record`의 '한 번에 덧붙임' 안에서. `problems`가 있으면 멈춤.
- 옛 장부(`ledger.rebuild`)가 증권사 주문(pocket이 있는 order 줄)을 읽지 않게 걸러야 한다 — `config.fill_rule()`은 "now"를 모르는 규칙으로 보고 종가로 체결한다.
- 16절의 `rebalance`(목표 비중 → 주문 계획, 돈 옮기기 순서)와 17절 갈아타는 날(`start` 줄: 보유는 그대로, 현금은 은행, 평균 산 값은 옛 체결 기록에서)은 이번에 만들지 않았다. `start(at, bank=…, units=…, cost=…)`가 받을 준비는 되어 있다.
- `test_only_broker_writes_broker_lines`를 옛 장부를 뗄 때 order·fill로 넓힌다.
- 루트 CLAUDE.md '구조와 명령' 표와 `docs/routine-prompt.md`에 세 파일·새 기록 종류를 적는다(이번엔 지시대로 안 고침). `docs/broker-sandbox.md` 19절 표에 `quote_clock_skew` 줄을 더하고, 끊긴 조사 원문 링크를 고친다.

## 메인 판단 필요
1. **주문 줄의 `kind` 칸 이름 → `order_kind`**: 14절은 order에 `kind`(open/close/now)를 더하라고 했지만, 모든 기록 줄의 `kind`는 이미 기록 종류("order")다(`records.write`가 `{"kind": kind, **it}`로 씀). 그래서 `order_kind`로 바꿨고 `fill_rule`에도 같은 값을 적는다. 블로그 그래프가 `fill_rule`을 읽는다면 값 "close"·"now"가 새로 나온다.
2. **다시 계산 순서 = seq(알게 된 순서)**: 설계는 '일이 일어난 시각 순서'로 다시 계산하라고 했다. 시각 순서로 하면 늦게 넣은 09:00 체결이 그 사이에 받은 주문의 '받을 때 조건'을 바꿔 보이게 하고, 같은 시각 줄의 순서(이체 둘을 이어 하기 등)를 파일 셋에서 되살릴 수 없다. 그래서 모든 증권사 줄에 계좌 안 일련번호 `seq`를 달고 그 순서로 돌리되, 각 줄에 일이 일어난 시각(`at`·`decided_at`)을 같이 적어 T1·결제·출금 시각은 그 시각으로 따진다. 돈·수량은 순서와 상관없이 같고(t15), 다른 것은 평균 산 값뿐이다(위 '알려진 문제').
3. **출금 가능 금액에서 걸린 사기 주문이 잡은 돈을 '지금 돈'에서 뺀다**: 판 돈(결제 전)으로 산 주문이 걸려 있으면 출금 가능이 0이 될 수 있다(t13). 사기 주문의 돈이 결제일에 나간다고 보면 더 많이 뺄 수 있지만, 실제 증권사 규칙을 확인하지 못해 좁은 쪽(P5)을 골랐다. 16절 4단계(다른 주머니로 돈 옮기기)에 영향이 있다.
4. **업비트 호가 단위는 200만 원 이상(1,000원)만**: 19절이 '아래 구간은 공식 표'라고만 해서 그 아래는 `None`(모름) → 그 가격대의 업비트 지금 주문은 호가를 못 만들어 거절된다(BTC는 해당 없음). 공식 표를 옮기면 풀린다.
5. **취소 시각**: '체결 시각 전'을 장 시작 주문 = 그 장 09:00, 장 마감 주문 = 15:30으로 정했다. 장 시작에 못 사고 장중에 걸린 사기는 언제 체결될지 몰라 취소를 받지 않는다(15절은 이 경우를 말하지 않음). 실제 장 마감 동시호가(15:20–15:30) 중 취소가 되는지는 확인 못 함.
6. **표 밖 숫자 `quote_clock_skew` 5초(설계 결정)**: 실제 주문에서는 주문 시각을 정한 뒤 가격을 받으므로 가격 시각이 주문 시각보다 몇 초 뒤일 수 있다. 0초로 두면 실제 실행에서 지금 주문이 늘 거절될 수 있어 5초를 받았다. 19절 표에 더할지 정해야 한다.
7. **업비트 → 은행 출금 수수료는 옮기는 금액과 따로 업비트 원화에서 뗀다**(옮긴 금액은 그대로 은행에 들어감). 19절은 '건당 1,000원'만 적었다 — 확인 전이라 더 비싼 쪽으로 읽었다.
8. **업비트 금액 사기는 주문 금액 전부를 쓰고 수량을 8자리로 내린다**(평균값 = 금액 ÷ 내린 수량, 1원 안쪽으로 불리한 쪽). 실제로는 내린 수량 × 값만 빠질 수 있다.
9. **한국 거래소 지금 주문의 '60초'를 받은 가격의 시각(네이버의 시각 칸)으로 잰다**: 그 칸이 마지막 체결 시각이면 거래가 드문 종목은 장중에도 거절될 수 있다(좁은 쪽). 칸의 뜻을 확인한 뒤 정한다.
10. **결제일 09:00 '뒤'**를 `>`로 따져 09:00:00 정각에는 못 뺀다(t05·t13). 19절 '09:00 뒤'를 글자 그대로 읽었다.
11. **증권 계좌 체결의 `cash_after`**는 결제 예정까지 더한 증권 계좌 원화(주문 가능 금액의 바탕)다. 옛 장부의 '체결 뒤 현금'과 뜻이 조금 다르다(records/CLAUDE.md에 적음).
12. **장 시작 주문의 날에 봉이 없을 때**: 그 뒤 날짜의 봉이 보여야 만료한다(휴장·거래 정지로 봄). 가격을 못 받은 것(네트워크)과 구별하려고 이렇게 했다 — 네트워크가 계속 막히면 주문이 오래 걸려 있다.
