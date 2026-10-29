# records/ — 덧붙이기만 하는 기록

## 역할
결과 전에 무엇을 말했는지 남기는 곳이다. 종류마다 `<종류>.jsonl` 파일 하나, 한 줄에 기록 하나(JSON). git 커밋 시각이 "결과 전에 말했다"는 증거다.

- `records/*.jsonl`: 진짜 가상 계좌(1,000만 원) 기록 — **매일 예약 실행만 쓴다**(`python -m market.daily record`). 2026-10-21 현재 아직 없다. 첫 실행 날이 계좌 시작일이다.
- `records/replay/`: 2026-07-01~10-19 재생(계산기만, 홀드아웃) 한 번의 원본 출력. `summary.json`(모은 숫자), `report.md`(표). 해설은 [docs/replay-2026-07-10.md](../docs/replay-2026-07-10.md). **다시 돌리지 않고 고치지 않는다**(재생 명령이 폴더가 차 있으면 거절한다).

## 기록 종류와 칸 (코드 기준: `market/records.py`의 FIELDS·OPTIONAL·DESCRIBE)
모든 줄 공통: `id`(문자열, 파일 안에서 하나), `kind`(종류 = 파일 이름), `run`(판단 날짜 YYYY-MM-DD, 줄 순서대로 같거나 늘어남).

| 종류 | 무엇 | 칸 |
|---|---|---|
| `run` | 실행 한 번 | mode(replay / replay-score-only / live / missed = 빠진 날), steps(받기 결과, 판단 때 가격, 판단 시각, 건너뛴 대상, 수, 에이전트 수, 볼 곳 수 등), notes(메모·경고) |
| `reading` | 시세 대 보기: 대상 하나의 조각 값 | asset, known_through(본 마지막 일봉 날짜), values(상태 id → 예/아니오/모름), numbers(계산에 쓴 숫자) |
| `research` | 조사: 질문 하나 | question, state(대 볼 상태 id, 열린 질문이면 ''), angle, asked, answer(300자 이내 요약), looked(본 곳마다 place·result 찾음/없음/늦음/막힘·new·url·date·note), agent |
| `hand_reading` | 손 대 보기(하루 하나) | values(조사 상태 → 예/아니오/일부/모름 또는 값), why(상태 → 이유), places(상태 → 쓴 볼 곳), research(근거 조사 id), (있으면) by_asset(종목 코드 → 종목마다 대 보는 상태 → {value, why, places}, 2026-10-29~) |
| `prediction` | 예측 | predictor(calc / hand / version:<id> / baseline:half / baseline:base_rate), asset, horizon(봉 수), base_date·base_close(판단 때 알려진 마지막 끝난 종가), p_up(오를 확률), p_range, size_lo·size_hi(움직임 크기 범위, \|수익률\| %), readings, used(쓴 조각), decisive(빼면 결론이 바뀌는 조각), reason, knowledge(논리집 지문), (손 예측) confidence |
| `score` | 채점 | prediction, predictor, asset, horizon, target_date·target_close, ret(수익률), up, direction_hit(확률 0.5면 null), brier(확률 점수), size_hit, (2단계) counted(같은 예측기·대상·기간·기준 날짜 중 처음 말한 것만 true) |
| `order` | 주문 | account, asset(세 대상 또는 종목 코드), side(buy/sell), qty_units(최소 단위 개수: ETF·종목 1주, 코인 1e-8), ref_price(주문 수량을 잡은 가격), reason, (2단계) decided_at(판단한 실제 시각 — 이 뒤의 첫 공식 가격에 체결) |
| `fill` | 체결 | account, order, asset, side, fill_date, price, qty_units, value·fee(원, 정수), cash_after, note |
| `account` | 계좌 상태(07:37, 정산 뒤) | account, cash(원), positions(최소 단위; 세 대상은 늘, 종목은 들고 있을 때만), prices(대상 → [날짜, 가격]; 세 대상 + 들거나 기다리는 종목), value(평가액), pending(체결 안 된 주문 id) |
| `review` | 돌아보기: 기한이 된 손 예측 하나 | prediction, wrong(틀린 조각·상태 id), why(맞음 / 잡음 / 대 보기 틀림 / 조건 흐림 / 이유 틀림 / 바깥 상황), reason(맞았어도 적는다) |
| `reading_check` | 손 대 보기 다시 보기(7일 뒤) | target(hand_reading id), state, then, now, hit(true/false/null), late(판단 뒤에야 정보가 나온 볼 곳), reason |
| `correction` | 정정 | target(고칠 기록 id), note. 지난 줄은 고치지 않는다 |

계좌 id: 재생 `replay:calc`, 진짜 계좌 `live:hand`(손 예측이 비중을 정함), `live:calc`(계산기, R_weights), 기준선 `baseline:equal_hold`(처음 살 수 있는 날 1/3씩 사서 그대로), `baseline:cash`(전부 현금).

## 규칙
- **지난 줄은 고치지도 지우지도 않는다.** 정정은 `correction` 줄을 덧붙인다(매일 실행 입력의 `corrections`).
- `python -m market.records check`: 칸·타입·id 중복·날짜 순서·조사 칸 규칙 + **git HEAD에 있던 내용이 지금 파일의 앞부분과 글자 그대로 같은지**(`records/`와 `data/prices/`). 커밋 전에 돌린다.
- 지난 날짜로 예측을 만들지 않는다. 빠뜨린 날은 다음 실행이 빈 날(`run` mode "missed")로 남긴다.
- 채점·성적표는 같은 (예측기·대상·기간·기준 날짜)를 한 번만 센다(결정 1). 예측 자체는 매일 적는다.
- 기사 본문·비밀값·개인 정보를 넣지 않는다(공개 저장소). 조사 요약은 300자 이내(검사기가 막는다).

## 다른 폴더와의 관계
- 쓰는 코드: `market/daily.py`(매일 실행), `market/replay.py`(재생). 읽는 코드: `market/checks.py`(성적표), `market/report.py`(일지), `market/research.py`(볼 곳 성적), `market/replay.py report`.
