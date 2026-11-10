# records/ — 덧붙이기만 하는 기록

## 역할
결과 전에 무엇을 말했는지 남기는 곳이다. 종류마다 `<종류>.jsonl` 파일 하나, 한 줄에 기록 하나(JSON). git 커밋 시각이 "결과 전에 말했다"는 증거다.

- `records/*.jsonl`: 진짜 가상 계좌(1,000만 원) 기록 — **매일 예약 실행만 쓴다**(`python -m market.daily record`). 첫 예약 실행 날(2026-10-21)이 계좌 시작일이다.
- `records/replay/`: 2026-07-01~10-19 재생(계산기만, 홀드아웃) 한 번의 원본 출력. `summary.json`(모은 숫자), `report.md`(표). 해설은 [docs/replay-2026-07-10.md](../docs/replay-2026-07-10.md). **다시 돌리지 않고 고치지 않는다**(재생 명령이 폴더가 차 있으면 거절한다).

## 기록 종류와 칸 (코드 기준: `market/records.py`의 FIELDS·OPTIONAL·DESCRIBE)
모든 줄 공통: `id`(문자열, 파일 안에서 하나), `kind`(종류 = 파일 이름), `run`(판단 날짜 YYYY-MM-DD, 줄 순서대로 같거나 늘어남).

| 종류 | 무엇 | 칸 |
|---|---|---|
| `run` | 실행 한 번 | mode(replay / replay-score-only / live / missed = 빠진 날), steps(받기 결과, 판단 때 가격, 판단 시각, 건너뛴 대상, 수, 에이전트 수, 볼 곳 수 등), notes(메모·경고) |
| `reading` | 시세 대 보기: 대상 하나의 조각 값 | asset, known_through(본 마지막 일봉 날짜), values(상태 id → 예/아니오/모름), numbers(계산에 쓴 숫자) |
| `research` | 조사: 질문 하나 | question, state(대 볼 상태 id, 각도 질문·2차 조사면 ''), angle(2026-11-04부터 간밤 반응/일정·예상치/수급/공시·뉴스, 2차는 `<대상>-2차`; 그 전 줄은 금리·환율·정책/한국 시장/미국 시장/코인), asked, answer(300자 이내 요약), looked(본 곳마다 place·result 찾음/없음/늦음/막힘·new·url·date·note), agent. 있으면(2026-11-05 판단부터 매일 실행 입력에 필수): **numbers**(숫자 이름 → 값; 이름은 소문자·숫자·밑줄이고 끝 낱말이 단위 — pct·pp·bp·x·z·krw·krw100m·usd·usdm·usdbn·pt·btc·n, `records.NUMBER_KEY`; 값은 유한한 수), **as_of**(답의 숫자가 가리키는 시각, 시간대가 붙은 ISO — 판단 시각보다 늦으면 입력이 거절된다) |
| `hand_reading` | 손 대 보기(하루 하나) | values(조사 상태 → 예/아니오/일부/모름 또는 값; 상태에 answers가 있으면 그 안에서, 예: 오름/내림/아니오/모름), why(상태 → 이유), places(상태 → 쓴 볼 곳), research(근거 조사 id), (있으면) by_asset(종목 코드 → 종목마다 대 보는 상태 → {value, why, places[, numbers]}, 2026-10-29~; 2026-11-03부터 '예' 종목이 있는 날은 비교군 '아니오' 종목 3개 이상도). **씨앗 조각의 상태**(2026-11-04~)는 관련 있는 날만 들어 있다 — 값이 없으면 그날은 '안 물음'(성적표가 그렇게 센다). (있으면, 2026-11-05~) **numbers**(상태 → {숫자 이름: 값}): 숫자 상태(상태에 `number`)의 값을 코드가 정할 때 쓴 숫자와 메인이 대 보기에 적은 숫자. 숫자 상태의 values는 메인이 아니라 코드가 이 숫자로 정한 값(예/아니오/모름)이고, 숫자 씨앗은 숫자가 있는 날만 들어 있다. 성적표가 dir = "sign" 주장(숫자의 부호 쪽)을 시험할 때 이 숫자의 부호를 읽는다 |
| `prediction` | 예측 | predictor(calc / hand / version:<id> / baseline:half / baseline:base_rate / baseline:all — 2026-11-04~ 알려진 전체 시세에서 오른 비율), asset, horizon(봉 수), base_date·base_close(옛 줄: 판단 때 알려진 마지막 끝난 종가 날짜·값. **2026-11-05~ `base_kind` = "fill"**(결정 E2): base_date = 판단 뒤 첫 체결 봉 날짜(그 봉의 시가가 기준), base_close = null — 예측 때는 체결가를 모른다; 채점 줄이 그 시가를 적는다), p_up(오를 확률 — 체결가 기준 줄은 '목표 종가가 체결가보다 높을' 확률), p_range(출발점 층이 답하면 null), size_lo·size_hi(움직임 크기 범위, \|수익률\| %), readings, used(쓴 조각), decisive(빼면 결론이 바뀌는 조각), reason, knowledge(논리집 지문), (손 예측) confidence. 2026-11-04~ 있으면: **layer**(답한 층 — 계산기 출발점/조각/동전/모름, 이전 버전 조각/기준값, 손 예측은 입력 info 또는 decisive에서 조각/출발점/기타; 기준선 줄에는 없음), **analog**(계산기 줄만, 참고 칸 — 같은 대상 최근 750봉에서 시세 상태 7개가 오늘과 같고 목표 봉이 판단 때 이미 끝난 날들의 {n, runs 이어진 구간 수, eff = n ÷ 기간, up_rate}, 없으면 null. 확률에 쓰지 않는다) |
| `score` | 채점 | prediction, predictor, asset, horizon, target_date·target_close, ret(수익률), up, direction_hit(확률 0.5면 null), brier(확률 점수), size_hit, (2단계) counted(같은 예측기·대상·기간·기준 날짜 중 처음 말한 것만 true), (체결가 기준 줄, 2026-11-05~) fill_date·fill_price = 체결 봉(base_date 이상이고 시가가 있는 첫 봉 — 휴장·거래 정지면 다음 봉) 날짜와 시가. 이때 목표 = 체결 봉부터 h봉째 봉(1봉이면 체결 봉 자체의 종가), ret = target_close ÷ fill_price − 1. 옛 줄의 ret = target_close ÷ base_close − 1(h봉 뒤) |
| `order` | 주문 | account, asset(세 대상 또는 종목 코드), side(buy/sell), qty_units(최소 단위 개수: ETF·종목 1주, 코인 1e-8), ref_price(주문 수량을 잡은 가격), reason, (2단계) decided_at(판단한 실제 시각 — 이 뒤의 첫 공식 가격에 체결), (2026-11-05~) fill_rule = "open"(판단 뒤 첫 시가: ETF·종목 09:00 시가, 코인 09:00 일봉 시가; 결정 E1). fill_rule이 없는 주문은 옛 규칙(ETF·종목 판단 뒤 첫 15:30 종가, 코인 시가). **가상 증권사 주문**(pocket이 있는 줄, 2026-11-10~ `market/broker.py`만 만든다): decided_at = 주문 시각, pocket(bank/sec/upbit), order_kind(open 장 시작 / close 장 마감 / now 지금 — 기록 종류 칸 `kind`와 겹치지 않게 이 이름; fill_rule에도 같은 값), limit(한도 가격, 장 시작·장 마감 사기), amount(업비트 금액 사기 — 이때 qty_units는 0), reserved(잡아 둔 돈, 원), session(체결될 장의 날짜), seq(계좌 안에서 증권사가 줄을 만든 순서) |
| `fill` | 체결 | account, order, asset, side, fill_date, price, (2026-11-05~) price_kind(open 시가 / close 종가; 가상 증권사는 now 지금 / limit 장중 한도 가격도), qty_units, value·fee(원, 정수), cash_after(옛 장부: 체결 뒤 현금, 증권사: 그 주머니 원화 — 증권 계좌는 결제 예정 포함), note. **가상 증권사 체결**: at(체결 시각 — 늦게 넣어도 일어난 시각), pocket, tax(세금, 원), source(가격을 받은 곳·그 가격의 시각·진행 중인 봉이었나 final), settle(결제일, 업비트는 null), gain(판 이익 = 판 금액 − 평균 산 값 × 수량, 팔기만), seq. fill_date는 가격이 나온 봉의 날짜(업비트는 09:00에 시작한 봉) |
| `account` | 계좌 상태(07:37, 정산 뒤) | account, cash(원), positions(최소 단위; 세 대상은 늘, 종목은 들고 있을 때만), prices(대상 → [날짜, 가격]; 세 대상 + 들거나 기다리는 종목), value(평가액), pending(체결 안 된 주문 id). **가상 증권사 계좌 줄**: cash = 세 주머니 원화 합, pockets(bank·sec·upbit → 원), priced_at(대상 → 평가 가격의 시각 — 받을 수 있는 가장 새 실제 값), at(상태의 시각), seq |
| `broker` | 가상 증권사 사건(2026-11-10~, `market/broker.py`만 만든다) | account, at(사건 시각, 시간대 붙은 ISO), type(start 시작 잔고 / reject 거절 — data = 요청, note = 이유 / transfer 이체 — src·dst·amount·fee / cancel 취소 / expire 체결 못 하고 끝남 / adjust 진행 중인 봉 값과 끝난 봉 값의 차이 바로잡기 — fill·price_was·price_final·cash·cost / plan 그날의 목표 비중 / phase 단계 표시), data, note, seq. 계좌 상태는 start 뒤의 order·fill·broker·account 줄을 seq 순서로 다시 계산해 얻고, `records check`가 그 검산(주머니 음수·판 수량·받을 때 조건·account 줄·체결가 = 끝난 일봉)을 같이 한다 |
| `review` | 돌아보기: 기한이 된 손 예측 하나 | prediction, wrong(틀린 조각·상태 id), why(맞음 / 잡음 / 대 보기 틀림 / 조건 흐림 / 이유 틀림 / 바깥 상황), reason(맞았어도 적는다) |
| `reading_check` | 손 대 보기 다시 보기(7일 뒤) | target(hand_reading id), state, then, now, hit(true/false/null), late(판단 뒤에야 정보가 나온 볼 곳), reason |
| `correction` | 정정 | target(고칠 기록 id), note. 지난 줄은 고치지 않는다 |

계좌 id: 재생 `replay:calc`(R_weights v1), 진짜 계좌 `live:hand`(손 예측이 비중을 정함), `live:calc`(계산기; 이 변경이 main에 들어간 뒤의 실행(2026-11-05 예정)부터 R_weights_v2, 그 전 주문은 v1), 기준선 `baseline:equal_hold`(처음 살 수 있는 날 1/3씩 사서 그대로), `baseline:cash`(전부 현금).

## 규칙
- **지난 줄은 고치지도 지우지도 않는다.** 정정은 `correction` 줄을 덧붙인다(매일 실행 입력의 `corrections`).
- `python -m market.records check`: 칸·타입·id 중복·날짜 순서·조사 칸 규칙 + **git HEAD에 있던 내용이 지금 파일의 앞부분과 글자 그대로 같은지**(`records/`와 `data/prices/`) + broker.jsonl이 있는 폴더는 가상 증권사 검산(`broker.check_folder`). 커밋 전에 돌린다.
- 지난 날짜로 예측을 만들지 않는다. 빠뜨린 날은 다음 실행이 빈 날(`run` mode "missed")로 남긴다.
- 채점·성적표는 같은 (예측기·대상·기간·기준 날짜)를 한 번만 센다(결정 1). 예측 자체는 매일 적는다.
- 기사 본문·비밀값·개인 정보를 넣지 않는다(공개 저장소). 조사 요약은 300자 이내(검사기가 막는다).

## 다른 폴더와의 관계
- 쓰는 코드: `market/daily.py`(매일 실행), `market/replay.py`(재생). 가상 증권사 줄(order·fill·account의 seq 있는 줄과 broker)은 `market/broker.py`가 만들고 부른 쪽이 한 번에 쓴다(2회차에서 매일 실행에 잇는다 — 지금은 아직 쓰이지 않는다). 읽는 코드: `market/checks.py`(성적표), `market/report.py`(일지), `market/research.py`(볼 곳 성적), `market/replay.py report`.
