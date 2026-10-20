# market/ — 시세로 계산하는 부분 전체 (1단계 코드)

## 역할
시세 받기·저장, 덧붙이기만 하는 기록과 검사기, 정산(체결)과 계좌 평가, 예측 채점, 가격 조각과 계산기, 숫자 맞추기, 날짜 순서 재생, 논리집 검사 감싸기. 외부 패키지 없음(Python 3.11+ 표준 라이브러리).

| 파일 | 하는 일 | 명령 (저장소 루트) |
|---|---|---|
| `config.py` | 대상·수수료·기간·시각·경로, 판단 시각(07:37 KST), 맞추기 끝 날짜(`FIT_UNTIL` 2026-06-30), 재생 기간 | – |
| `prices.py` | 시세 받기(네이버·야후·업비트, 시간 제한 30초)·덧붙이기·읽기, `view`(그 시각에 끝나 있던 일봉만) | `python -m market.prices update / backfill / show` |
| `records.py` | 기록 종류별 칸, 쓰기(칸이 틀리면 아무것도 안 씀), 검사(칸·id·날짜 순서·git HEAD와 비교) | `python -m market.records check` |
| `ledger.py` | 계좌: 목표 비중 → 주문, 체결(정산), 평가. `target_weight` = R_weights | – |
| `scoring.py` | 채점(방향·확률 점수·크기), 기준선 예측(기저율), 모아 보기 | – |
| `pieces.py` | 가격으로 대 보는 상태 7개(예/아니오/모름) | – |
| `calc.py` | 계산기: 대 보기 값 → 오를 확률·움직임 범위·결론·결론을 바꾸는 조각 | `python -m market.calc [날짜]` (보여 주기만) |
| `fit.py` | 조각의 숫자를 `FIT_UNTIL`까지의 시세로 기계적으로 맞추고 knowledge.toml 구역을 다시 씀 | `python -m market.fit [--write]` |
| `replay.py` | 날짜 순서 재생 → `records/replay/`, 보고 표 | `python -m market.replay` / `report` |
| `checks.py` | 논리집 자동 검사(CHECKS·SOURCE·SEEN_UNTIL)와 분야 점검(AUDIT) | (logic이 부른다) |
| `logic.py` | PROVISO 코어(schema·score·audit)를 불러 이 논리집을 검사 | `python -m market.logic validate / check / audit` |

## 결정과 이유
- **미래를 못 보게 구조로 막는다**: 계산 함수는 `History`(어떤 시각에 끝난 일봉만 담은 읽기 전용 묶음)나 그 종가 목록만 받는다. 판단 시각 07:37에 ETF는 전날 종가까지(16:00에 끝난 것으로 봄), 코인은 이틀 전 일봉까지(일봉이 다음 날 09:00에 끝남). 재생은 날마다 view를 새로 만든다. 독 넣기 테스트가 증명한다.
- **체결**: ETF는 판단 날짜와 같거나 뒤인 첫 거래일 종가, 코인은 판단 날짜 일봉 시가. 정산은 체결 시각 순서대로 하고, 모든 대상의 그 시각 가격을 알 때까지 기다린다(09:00 코인 매수에 15:30 ETF 매도 대금을 못 쓴다). 사는 금액은 원 단위 올림, 파는 금액은 내림, 수수료 올림(계좌에 불리한 쪽). 현금이 모자라면 수량을 줄이고 note에 적는다.
- **계산기** (PROVISO agent_usage `predict.py`와 같은 모양): 오를 확률의 승산(오를 확률 ÷ 내릴 확률) = 기준 × 켜진 곱하기들. 범위는 로그 반폭을 제곱합 제곱근으로 합치고 2배 폭을 넘으면 가운데 둘레로 자른다. 기준값이 없으면 '모름'. 설명에는 빼면 결론(방향 쪽·확신도·크기 등급)이 바뀌는 조각만.
- **맞추기**: 오를 확률은 로지스틱 회귀, 움직임은 log|수익률| 최소제곱. 겹치는 기간 표본이라 표준오차 × √기간. 범위 = 값 ± 표준오차. 방향이 주장과 어긋나거나 1을 넘나들면, 폭이 2배를 넘으면, 곱하기 수가 유효 표본 ÷ 10을 넘으면 세우지 않는다(not_set에 까닭).
- **조각의 기준 숫자**(20·60·200·250봉, 2배 등)는 미리 정한 모양이고 기록에서 고르지 않았다. 효과 크기만 맞춘다.
- 예측 기록의 `predictor`: `calc` / `hand`(2단계) / `version:<id>`(이전 버전, 2단계) / `baseline:half` / `baseline:base_rate`.

## 규칙
- 모든 네트워크 호출에 시간 제한(`config.NET_TIMEOUT`).
- 기록은 `records.write`로만 쓴다(덧붙이기). 지난 줄을 고치는 코드를 만들지 않는다.
- 조각 id·문장에 대상 이름을 넣지 않는다(`config.ASSET_WORDS`, audit 오류).
- knowledge.toml의 effect 숫자를 손으로 고치지 않는다(`fit --write`만, audit가 다시 맞춰 비교).

## 다른 폴더와의 관계
- 읽음: `data/prices/*.csv`, `knowledge.toml`, PROVISO-Nesy 코어(`PROVISO_PATH` 또는 형제 폴더 `../PROVISO-Nesy`).
- 씀: `data/prices/`(prices update), `records/`(재생은 `records/replay/`), `knowledge.toml`의 계산 조각 구역(fit --write).
- 시험: `tests/`.
