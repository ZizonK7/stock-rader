# 2026-11-10 가상 증권사 3회차 — 검토와 실제 주소 시험에서 나온 것 고치기

작업자: 구현 작업자(1·2회차를 만든 사람과 다른 새 작업자, Opus 5.5). 브랜치 `claude/wonderful-clarke-bq5e7s`(HEAD 742180a 위, 커밋하지 않음). 지시서: 메인의 3회차 지시서. 근거: [새 눈 검토](2026-11-10-broker-review.md)(발견 1–10), [실제 주소 시험](2026-11-10-broker-live-smoke.md)(S1–S5).

## 발견 번호 → 고친 곳 → 지키는 시험

시험은 따로 적지 않으면 `tests/test_broker_fixes.py`에 있다. 새 시험 21개 중 20개는 **고치기 전 코드에서 실패하는 것을 먼저 보고** 고쳤다(`git stash push -- market`로 되돌려 돌린 결과: failures 12, errors 8 — f3 '판 돈을 다 쓴 뒤' 시험은 검토와 같은 `RuntimeError: … 주머니가 음수가 됨`으로 죽었다). 나머지 하나(2a)는 '지금 못 잡는다'를 지키는 시험이라 처음부터 통과한다.

| 발견 | 고친 곳 (파일 · 함수) | 지키는 시험 |
|---|---|---|
| 1 계좌 줄이 넘겨받은 평가 가격을 버림 | `market/broker.py` `Broker.account_line`(넘겨받은 값 `given`을 먼저, 결과는 `out` — 이름 겹침 없앰), `Broker._price`(넘겨받지 않은 대상만; 한국 거래소 지금 값은 받은 상태가 '열림'일 때만) | `test_f1_account_line_uses_the_prices_it_is_given`, `test_f1_without_given_prices_a_closed_exchange_is_not_valued_at_a_loud_now`, `test_f1_daily_account_lines_follow_value_prices_when_upbit_now_fails` |
| 2a 저가로 산 지금 체결 | 막지 않음 — 문서: `docs/broker-sandbox.md` 15절 '검산이 잡는 것 / 못 잡는 것', `broker.check` 설명, 루트 `CLAUDE.md` 다음 할 일 5⑦(분봉 검산) | `test_f2a_now_fill_inside_the_days_range_is_not_caught_yet`(지금은 통과 = 못 잡음. 막으면 뒤집는다) |
| 2b 다른 날·다른 시각의 체결 | `broker._fill_when_errors`(`State._fill`이 부름) | `test_f2b_open_fill_on_another_day_or_at_another_time_is_an_error`, `test_f2b_now_fill_time_must_be_the_request_time` |
| 2c `seq` 없는 가짜 줄 | `broker._start_errors`(`check`가 부름), `market/report.py` `Book.__init__`(start 뒤 seq 없는 계좌 줄을 안 읽음) | `test_f2c_lines_without_seq_after_start_are_errors_and_the_report_ignores_them`, `test_f2c_old_shape_lines_before_start_and_old_fills_on_the_switch_day_are_fine` |
| 2d 부풀린 시작 잔고 | `broker._old_ledger`·`_start_errors` | `test_f2d_start_must_match_the_old_ledger` (+ 그대로 통과하는 `test_broker_daily.Switch` 두 시험) |
| 3 바로잡기가 돈을 옮기고 매일 실행이 죽음 | `broker.Broker._verify`(adjust 줄 돈 0, 받은 값·끝난 봉 값·둘의 출처 `source_was`·`source_final`, 경고 한 줄), `State._b_adjust`(돈·산 값을 옮기는 줄은 오류), `_fill_price_errors`(진행 중인 봉 시가: 다르면 경고, 2% 넘으면 오류 — `IN_PROGRESS_TOLERANCE`), `BrokerError`; `market/daily.py` `crash`·`stop`, `compute`·`record`·`open_phase`의 증권사 부분을 예외 → 멈춤으로; `docs/routine-prompt.md` 2절·9절 표 | `test_f3_adjust_records_but_moves_no_money`, `test_f3_in_progress_open_more_than_2pct_off_is_an_error`, `test_f3_review_case_final_bar_lower_after_spending_the_sale_does_not_crash`, `test_f3_exceptions_inside_the_broker_stop_prepare_record_and_open`, `test_broker.Check.test_t16b…`(바꿈, 아래 표) |
| 4 오래 멈췄다 돌면 | `market/quotes.py` `NetQuotes(prices_folder=…)`·`_file_bar`·`day`(시세 파일 먼저, 없으면 그 날짜까지 닿는 개수), `daily.quotes_for`(진짜 폴더에 시세 폴더를 넘김), `broker._expire_errors`(`check`) | `test_f4_net_quotes_reads_finished_bars_from_the_price_file_first`, `test_f4_a_long_gap_still_fills_the_open_order`, `test_f4_check_catches_an_expire_that_should_have_filled` |
| 5 너무 이른 `daily open` | `broker.Broker.unbooked_open`, `daily.open_accounts`(`open_phase`에서 계좌마다 일을 떼어 냄 — 걸린 장 시작 주문이 장부에 안 들어왔으면 그 계좌는 아무것도 안 하고 phase 줄도 안 남김); 지시문 6-1 | `test_f5_open_phase_before_todays_bar_is_seen_leaves_no_phase_line` |
| 6 지시문 두 문장 | `docs/routine-prompt.md` 2절 '주문은 가상 증권사를 거친다'의 두 문장 | 문서(시험 없음). 문장이 가리키는 동작은 기존 `test_broker.t10`·`test_broker_daily.e5`가 지킨다 |
| 7 갈아타는 날 0주 체결 | `report.paid_text`(`broker_notes`에서 뗌) | `test_f7_zero_unit_old_fill_is_written_as_not_bought` (+ 복사본 흉내에서 "하이브(종목) 사기는 돈이 모자라 사지 못함" 확인) |
| 8·S1·S2 네이버 지금 가격 | `quotes.Now.why`, `naver_now_item`(바깥 `datas`만), `naver_gate`(장 열림·정규장·거래 중·지연 없음 — 칸 없으면 거절), `parse_naver_now`(시각 = 응답 시각), `naver_now_fields`(받을지 가르는 칸), `show`·`main`(대상마다 새 요청 시각, `stocks.toml` 이름); `broker._order_krx`(거절 이유 = `why`); `report.REJECT_WORDS`; `rules.now_quote_max_age` 설명; 설계 15·18·19절 | `test_s1_s2_real_shape_opens_only_when_trading_in_the_regular_market_without_delay`, `test_s1_halted_stock_is_rejected_by_the_broker`, `test_s3_quotes_tool_takes_a_fresh_time_per_asset_and_shows_stock_names`, `test_quotes.Parse.test_naver_now_reads_the_real_shape`(바꿈) |
| 9 액면분할 | 문서만: `docs/broker-sandbox.md` 18절, 루트 다음 할 일 5⑧ | 없음(처리하지 않는다는 기록) |
| 10 `prices`의 비트코인 날짜 뜻 | 문서만: 루트 `CLAUDE.md` '블로그 발행'(+ `ref_price`가 '전 거래일 종가'인 것은 장 시작·장 마감 주문뿐) | 없음 |

### 바꾼 기존 시험
| 시험 | 무엇을 | 왜 |
|---|---|---|
| `tests/test_broker.py` `Check.test_t16b_in_progress_open_that_differs_makes_an_adjust_line_and_a_warning` | 전: adjust 줄이 증권 계좌 돈을 옮기고(음수) adjust를 빼면 검산 오류. 후: adjust 줄은 돈 0이고 계좌 돈이 그대로, 검산은 오류 없이 경고, adjust를 빼도 검산 결과가 같다 | 발견 3 결정(진행 중인 봉 값이 치른 값 — 적어 두기만) |
| `tests/test_quotes.py` `Parse.test_naver_now_finds_the_price_in_unknown_nesting` → `test_naver_now_reads_the_real_shape`, 예시 응답 `NOW` | 모르는 겹 구조(`result.items`)는 이제 None. `NOW`에 실제 응답의 받을지 가르는 칸(`marketSessionType`·`tradeStopType`·`tradableStatus`·`stockExchangeType.delayTime`)을 더함 | S1·S2: 실제 모양만 읽고, 거래 정지 칸이 없으면 받지 않으므로 칸이 없는 예시로는 '열림'이 안 된다 |

지운 시험·건너뛰게 한 시험은 없다.

## 한 일
- **1** 계좌 줄이 매일 실행의 `value_prices`를 그대로 쓴다(값·`priced_at` 모두). 넘겨받지 않은 대상만 공급자에게 묻고, 그때도 한국 거래소 '지금 값'은 받은 상태가 열림일 때만 — 장 밖의 값(장 전 동시호가 예상가 등)으로 평가하지 않는다.
- **2** 검산(`broker.check`)에 넷을 더했다: ⑤ 체결 줄 날짜·시각 = 주문의 장(09:00/15:30)·요청 시각 ⑥ `start` 뒤의 seq 없는 order·fill·account 줄 금지(규칙은 아래) + 진짜 계좌의 start = 옛 장부 ⑦ 끝난(expire) 주문이 그 장의 봉으로 치러졌어야 했는지 ⑧ 돈을 옮기는 adjust 줄. 일지 `Book`은 start 뒤 seq 없는 계좌 줄을 읽지 않는다.
  - **seq 없는 줄이 정당한 경우(내가 정한 규칙)**: 옛 계좌·옛 주문 줄은 그 계좌 `start` 줄의 실행 날짜(`run`) **전**, 옛 체결 줄은 실행 날짜**까지**이면서 그 계좌의 seq 없는 주문에 딸린 것(갈아타는 날 옛 장부가 끝낸 주문). 날짜로 가르는 까닭: seq 없는 줄은 날짜 말고 순서가 없고, 갈아타는 날 옛 장부가 쓰는 줄은 그 실행의 체결뿐이다(broker 모드에서는 옛 계좌·주문 줄을 쓰지 않는다 — `daily.record`). 날짜를 앞당겨 덧붙인 줄은 기존 `records check`의 'run 날짜 순서' 검사와 git HEAD 비교가 잡는다.
  - **start 견주기**: 마지막 옛 계좌 줄(start 실행 날짜 전)의 현금·보유 + 그 뒤 옛 체결 줄(실행 날짜까지)의 돈·수량을 다시 더해 start의 주머니 합·보유(0 아닌 것)와 견준다. 옛 기록이 없으면 현금 1,000만 원·보유 없음. 진짜 계좌 넷(`config.LIVE_ACCOUNTS`)만 견준다.
- **3** 바로잡기는 적어 두기만: adjust 줄 `cash`·`cost` = 0, `price_was`(받아 치른 값)·`price_final`(끝난 봉)·`source_was`·`source_final`, 경고 글 "… 치른 값은 바로잡지 않고 적어 둠(분배금·액면분할을 지난 값에 소급한 조정일 수 있다)". 검산의 체결가: 끝난 봉에서 넣은 체결은 같아야 하고, 진행 중인 봉에서 넣은 체결(`source.final` 거짓)은 다르면 경고, 2% 넘으면 오류. 증권사 안의 예외(`_emit`의 `BrokerError`와 그 밖의 예외)는 prepare·record·open에서 `가상 증권사 기록이 어긋나 있다(…)` 멈춤 글 + `증권사 안에서 예외 <종류>: <글>` 줄로 바뀌고 아무것도 쓰지 않으며 종료 코드 1.
- **4** `NetQuotes`가 끝난 봉을 시세 파일에서 먼저 읽는다(파일 줄은 출처 `file:<원래 출처>`, final). 파일에 없을 때만 실제 주소에 묻고, 받을 개수 = max(10, 그 날짜부터 오늘까지 달력 날 수 + 3)(업비트는 200까지). 고른 쪽과 까닭: 시세 파일은 덧붙이기만 하는 공식 값이고 검산도 같은 파일과 견주므로 체결 값과 검산이 같은 출처가 된다. 개수만 늘리는 길은 네이버가 지난 값을 소급해 고친 값을 다시 받게 되어 버렸다. 검산은 expire를 그 장의 봉과 견준다(지시서의 '팔기, 시가 < 한도인 사기'에 더해 '시가 ≥ 한도여도 저가 < 한도인 장 시작 사기', '종가 < 한도인 장 마감 사기'도 — 증권사가 실제로 체결하는 조건 그대로).
- **5** `open`이 계좌마다 맞추기 뒤 `unbooked_open`(그날 장의 걸린 장 시작 주문 중 오늘 봉을 아직 못 받은 것)을 보고, 있으면 그 계좌는 계획·phase 줄 없이 까닭을 출력한다("오늘 장 시작 주문 n건이 아직 장부에 안 들어왔다 … 몇 분 뒤 다시 돌린다"). 시가를 받고도 한도 때문에 걸려 있는 사기는 들지 않는다(그날 내내 걸려 있을 수 있어서). 걸린 장 시작 주문이 없던 계좌는 그대로 phase 줄을 남긴다.
- **6** 지시문: "5% 넘게 뛰어 열리면 안 사진다" → 한도 = 어제 종가 × 1.05, 그보다 높게 열리면 장중에 걸려 있다가 한도 아래로 내려오면 한도 가격에 사지고 아니면 장 마감에 끝난다(다음 실행 출력에서 본다). "이틀 뒤 실행에서야" → 결제일(판 날 + 2영업일) 아침 실행은 09:00 전이라 못 옮기고, 결제일의 장 시작 뒤 단계나 그다음 날 실행에서 옮긴다. 같은 말을 `grep`으로 찾았는데 다른 문서에는 없었다(`docs/design.md` 40줄의 '이틀 뒤'는 옛 장부 설명이라 그대로).
- **7** 일지의 '지난 주문이 치러진 값'에서 0주 체결은 "○○ 사기는 돈이 모자라 사지 못함"(옛 장부 note가 있을 때; 팔기는 "가진 것이 없어 팔지 못함", note가 없으면 "치러지지 않음").
- **8·S1·S2** 네이버 지금 가격을 실제 모양으로 읽는다: 바깥 `datas`(itemCode가 그 대상인 것), 값 `closePrice`(쉼표 글자), 시각 `localTradedAt` → 없으면 바깥 `time`(둘 다 응답 시각). 지금 주문을 받는 조건: `marketStatus` OPEN · `marketSessionType`이 있으면 regularMarket · `tradeStopType.name` TRADING이고 `tradableStatus` tradable(둘 중 하나라도 칸이 없으면 '모름'으로 거절) · `stockExchangeType.delayTime`이 있으면 0. 아니면 `Now.open` 거짓과 까닭 `Now.why` → 증권사 거절 이유, 일지 쉬운 말("그 종목의 거래가 멈춰 있어서" 등). 60초 검사는 남기고 그 한계(거래가 드문 종목의 묵은 값)를 설계 18절과 `rules.py` 설명에 적었다. `python -m market.quotes`는 대상마다 요청 시각을 새로 잡고 종목 이름을 보이며 받을지 가르는 칸을 그대로 찍는다.
- **9·10·문서**: 설계 13–15절의 바로잡기 문장, 15절 '검산이 잡는 것 / 못 잡는 것', 18절(액면분할, 응답 시각, 오래 멈춘 실행, 한도 이상 바뀐 경우), 19절 표; 루트 `CLAUDE.md`(지금 상태, 가상 증권사 절의 기록·바로잡기, 블로그 발행의 칸 뜻 둘, 다음 할 일 5②⑦⑧); `market/CLAUDE.md`·`records/CLAUDE.md`·`tests/CLAUDE.md`·`market/records.py`의 broker 칸 설명.

## 확인한 것 (명령과 결과)
- 시작 때 `python -m unittest discover -s tests -t .` → `Ran 209 tests … OK (skipped=21)`.
- 고치기 전 코드에 새 시험: `git stash push -- market && python -m unittest tests.test_broker_fixes` → `Ran 21 tests … FAILED (failures=12, errors=8)` → `git stash pop`.
- 끝날 때 `python -m unittest discover -s tests -t .` → `Ran 230 tests in 34.8s OK (skipped=21)`.
- `PROVISO_PATH=/home/user/proviso-nesy python -m unittest discover -s tests -t .` → `Ran 230 tests in 61.1s OK`(건너뜀 없음).
- `python -m market.records check` → `기록 검사 통과`. `PROVISO_PATH=/home/user/proviso-nesy python -m market.logic validate` → `문제 없음`. `python -m market.broker status` → 증권사 기록 없음(갈아타기 전), 검산 오류 0·경고 0.
- **지금 저장소 기록의 복사본으로 명령줄 흉내**(2회차 방법 그대로: 임시 폴더 `$T` = 기록·시세·toml·versions 복사본 + 11-11 뒤 가짜 봉을 임시 폴더 시세 파일에만, 입력은 틀을 최소로 채움 — 세 대상 0.3씩·종목 0; 만드는 스크립트는 세션 임시 폴더에 두고 저장소에 넣지 않음):
  - `python -m market.daily prepare --root $T --day 2026-11-11 --at 07:37 --no-fetch` → 종료 0, briefing 맨 위 "갈아타는 날".
  - `python -m market.daily record …/2026-11-11/filled.json --root $T --day 2026-11-11 --at 08:20` → `2026-11-11 기록: run 1, fill 15, score 113, account 4, …, order 3, broker 8, review 16`, `갈아탐: …`, 손 계좌 `주문 넣음`·`이체`·`내지 않음`.
  - `python -m market.daily report --root $T --day 2026-11-11` → 일지의 '지난 주문이 치러진 값'에 "하이브(종목) 사기는 돈이 모자라 사지 못함"(발견 7).
  - `python -m market.daily open --root $T --day 2026-11-11 --at 09:03` → `order 1, fill 4, broker 4`, 손 계좌 `바로 체결: KODEX 200 사기 1,884,240원어치`, 지난 주문 체결 셋. 09:40에 다시 → `쓴 줄 없음`, "이미 돌았다".
  - 다음 날 `prepare`(종료 0) → `record --day 2026-11-12 --at 08:20` → `2026-11-12 기록: run 1, score 90, account 4, …, broker 2, review 11`.
  - `python -m market.records check $T/records` → `기록 검사 통과`. `python -m market.broker status --root $T` → `검산 (오류 0건, 경고 0건)`, start 줄 4개.
- **검토의 2d를 복사본에서 다시**: 위 폴더의 사본에서 손 계좌 start 줄의 은행을 +100만 원 하고 `records check` → 실패 5건, 그중 `live:hand broker live:hand:2026-11-11:start:1: 시작 잔고(주머니 합 1,136,311원, …)가 옛 장부의 마지막 상태(현금 136,311원, …)와 다름`(나머지 넷은 계좌 줄 어긋남 — 검토처럼 계좌 줄도 맞춰 고쳐도 start 오류는 남는다).
- 바깥 그래프가 읽는 칸(`account`의 `cash`·`positions`·`prices`·`value`·`pending`, `order`의 `asset`·`side`·`qty_units`·`ref_price`·`decided_at`·`fill_rule`)은 이름·모양을 바꾸지 않았다. 바뀐 것은 계좌 줄 `prices`·`priced_at`의 값이 이제 매일 실행의 평가 가격과 같다는 것(발견 1의 뜻대로)뿐이다.

## 확인 못 한 것
- 실제 주소 전부. 이 세션의 네트워크는 시세 주소를 막는다. 네이버 지금 가격의 **값** 가운데 `tradeStopType.name` = `TRADING`, `tradableStatus` = `tradable`, `marketSessionType` = `regularMarket`은 지시서가 준 값이다 — 실제 주소 시험 보고서는 칸 이름만 적었고 값은 적지 않았다(`delayTime` 0과 `marketStatus` OPEN만 값이 있다). 값이 다르면(예: 대문자) 한국 거래소 지금 주문이 모두 거절된다(안전한 쪽).
- 장 밖·장 마감 동시호가 때의 응답 값, 거래 정지 종목의 실제 응답.
- 네이버 일봉을 '그 날짜까지 닿는 개수'(수십 줄)로 받을 때 응답 모양이 10줄일 때와 같은지(같은 주소·같은 `count` 칸이라 같을 것으로 보지만 확인 못 함).
- 블로그 그래프 코드(비공개 저장소)가 계좌 줄의 비트코인 날짜 뜻 변화에 영향을 받는지.

## 알려진 문제
- **검산이 못 잡는 것**은 그대로 남는다: 그날 저가–고가 안의 값으로 꾸민 지금 체결, 출처 칸까지 꾸민 줄(예: 끝난 봉과 1.5% 다른 장 시작 체결을 '진행 중인 봉에서 받음'으로 적으면 경고만), 증권사 코드를 고쳐 만든 줄. 설계 15절에 적었다.
- 진행 중인 봉 차이 경고는 `records check`(오류만 보여 줌)에는 안 나오고 `python -m market.broker status`의 검산 칸과 그날 briefing·record 출력(adjust 줄을 만들 때 한 번)에만 나온다.
- 증권사 부분의 `except Exception`은 코드 실수(예: 칸 이름 오타)도 '멈춤'으로 바꾼다. 죽지 않는 대신 까닭이 한 줄로 줄어든다(예외 종류와 글 앞 네 줄만).
- 시세 파일에 그날 봉이 아예 없고 실제 주소도 막히면 장 시작 주문은 여전히 오래 걸려 있다(18절 그대로).
- `open`의 '아직 장부에 안 들어왔다' 계좌도 맞추기에서 생긴 다른 줄(예: 지난 체결)은 쓴다 — 사실이라 그대로 두었다.

## 다음 작업자에게
- 실제 주소가 열리는 환경에서 `python -m market.quotes 069500 005930`을 장중·장 밖에 한 번씩 돌려 '받을지 가르는 칸'의 실제 값을 보고, `quotes.naver_gate`의 비교 값과 다르면 고친다(시험 `test_s1_s2_…`의 `REAL_NOW`도 같이).
- 분봉 검산(루트 다음 할 일 5⑦)을 하면 `test_f2a_now_fill_inside_the_days_range_is_not_caught_yet`을 뒤집는다.

## 메인 판단 필요
1. **start 뒤 seq 없는 줄의 정당한 경우를 날짜로 가름**(위 '한 일' 2): 옛 계좌·주문 줄은 start 실행 날짜 전, 옛 체결 줄은 그날까지이면서 옛 주문에 딸린 것. 날짜를 앞당겨 덧붙인 줄은 기존 'run 날짜 순서'·git HEAD 검사에 기댄다.
2. **시작 잔고 견주기는 진짜 계좌 넷만**: 다른 id(시험 계좌 등)는 옛 기록이 없어 견주지 않는다. 일지·그래프는 진짜 계좌만 읽으므로 그대로 두었다.
3. **expire 검산을 지시서보다 넓힘**: '시가 ≥ 한도여도 저가 < 한도인 장 시작 사기'와 '종가 < 한도인 장 마감 사기'도 '치러졌어야 함'으로 본다(증권사가 실제로 체결하는 조건 그대로).
4. **증권사 예외를 넓게 잡음**: `BrokerError`만이 아니라 증권사를 부르는 구간(계좌 다시 돌리기·맞추기·갈아타기 start·주문 계획·계좌 줄·장 시작 뒤 단계)의 모든 예외를 멈춤으로 보낸다. 지시서의 '`_emit`의 내부 오류 등'을 그렇게 읽었다.
5. **이른 `open`은 종료 코드 0**: 아무것도 사지 않은 계좌는 까닭을 출력하고 0으로 끝난다(오류가 아니라 '조금 뒤 다시'). 지시문 6-1에 "몇 분 뒤 한 번 더, 09:30까지 그대로면 그만두고 보고"를 적었다 — 09:30은 내가 정한 값이다.
6. **네이버 거래 정지 칸이 없으면 거절**(지시서대로)인데 장 구분 칸(`marketSessionType`)이 없으면 장 상태만으로 받는다(지시서의 '있으면'). 실제 값 확인 전이라 위 '확인 못 한 것'의 위험(값이 다르면 전부 거절)이 있다 — 안전한 쪽이라 그대로 두었다.
7. **진행 중인 봉 차이 2% 문턱을 체결가 검산과 바로잡기 경고 둘 다에 같은 수(`broker.IN_PROGRESS_TOLERANCE`)로**: 바로잡기 줄 자체는 차이가 2%를 넘어도 돈 0으로 적고, 오류는 검산(`records check`)이 낸다. 매일 실행(prepare·record)은 이 검산을 돌리지 않아 멈추지 않고, 커밋 전 `records check`가 실패한다(지시문 8절: 기록을 고치지 말고 원인을 찾는다) — 든 종목의 액면분할이 이렇게 드러난다. 매일 실행까지 멈추게 할지는 메인이 정한다.
