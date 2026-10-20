# records/ — 덧붙이기만 하는 기록

## 역할
결과 전에 무엇을 말했는지 남기는 곳이다. 종류마다 `<종류>.jsonl` 파일 하나, 한 줄에 기록 하나(JSON). git 커밋 시각이 "결과 전에 말했다"는 증거다.

- `records/*.jsonl`: 진짜 가상 계좌(1,000만 원) 기록 — **2단계 매일 예약 실행만 쓴다. 아직 없다.**
- `records/replay/`: 2026-07-01~10-19 재생(계산기만, 홀드아웃) 한 번의 원본 출력. `summary.json`(모은 숫자), `report.md`(표). 해설은 [docs/replay-2026-07-10.md](../docs/replay-2026-07-10.md). **다시 돌리지 않는다**(재생 명령이 폴더가 차 있으면 거절한다).

## 기록 종류와 칸 (코드 기준: `market/records.py`의 FIELDS·DESCRIBE)
모든 줄 공통: `id`(문자열, 파일 안에서 하나), `kind`(종류 = 파일 이름), `run`(판단 날짜 YYYY-MM-DD, 줄 순서대로 같거나 늘어남).

| 종류 | 무엇 | 칸 |
|---|---|---|
| `run` | 실행 한 번 | mode(replay / replay-score-only / live), steps(단계별 수·실패), notes |
| `reading` | 대 보기: 대상 하나의 조각 값 | asset, known_through(본 마지막 일봉 날짜), values(상태 id → 예/아니오/모름), numbers(계산에 쓴 숫자) |
| `prediction` | 예측 | predictor(calc / hand / version:<id> / baseline:half / baseline:base_rate), asset, horizon(봉 수), base_date·base_close(판단 때 알려진 마지막 끝난 종가), p_up(오를 확률), p_range, size_lo·size_hi(움직임 크기 범위, \|수익률\| %), readings, used(쓴 조각:값), decisive(빼면 결론이 바뀌는 조각), reason, knowledge(논리집 지문) |
| `score` | 채점 | prediction(예측 id), predictor, asset, horizon, target_date·target_close, ret(수익률), up, direction_hit(확률 0.5면 null), brier(확률 점수), size_hit |
| `order` | 주문 | account, asset, side(buy/sell), qty_units(최소 단위 개수: ETF 1주, 코인 1e-8), ref_price(판단 때 가격), reason |
| `fill` | 체결 | account, order, asset, side, fill_date, price, qty_units, value·fee(원, 정수), cash_after, note(수량을 줄였으면 까닭) |
| `account` | 계좌 상태(07:37) | account, cash(원), positions(최소 단위), prices(대상 → [날짜, 가격]), value(평가액), pending(체결 안 된 주문 id) |
| `review` | 돌아보기(2단계) | prediction, wrong(틀린 조각 id), why(조건 흐림 / 이유 틀림 / 바깥 상황 / 잡음 / 대 보기 틀림), reason |
| `correction` | 정정 | target(고칠 기록 id), note. 지난 줄은 고치지 않는다 |

계좌 id: `replay:calc`(계산기를 따름, R_weights), `baseline:equal_hold`(첫날 1/3씩 사서 그대로), `baseline:cash`(전부 현금). 진짜 계좌는 2단계에서 정한다.

## 규칙
- **지난 줄은 고치지도 지우지도 않는다.** 정정은 `correction` 줄을 덧붙인다.
- `python -m market.records check`: 칸·타입·id 중복·날짜 순서 + **git HEAD에 있던 내용이 지금 파일의 앞부분과 글자 그대로 같은지**(`records/`와 `data/prices/`). 커밋 전에 돌린다.
- 지난 날짜로 예측을 만들지 않는다. 빠뜨린 날은 다음 실행에서 빈 날(`run` 기록, notes)로 남긴다.
- 기사 본문·비밀값·개인 정보를 넣지 않는다(공개 저장소).

## 다른 폴더와의 관계
- 쓰는 코드: `market/replay.py`(재생), 2단계 매일 실행. 읽는 코드: `market/checks.py`(성적표), `market/replay.py report`.
