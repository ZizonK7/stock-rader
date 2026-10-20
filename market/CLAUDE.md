# market/ — 시세·기록·계산기·조사·매일 실행 코드

## 역할
시세 받기·저장, 덧붙이기만 하는 기록과 검사기, 정산(체결)과 계좌 평가, 예측 채점, 가격 조각과 계산기, 숫자 맞추기, 날짜 순서 재생(1단계), 그리고 2단계의 매일 실행(조사 질문·볼 곳 성적, 손 대 보기·손 예측 검사, 이전 버전 나란히, 일지 뼈대), 논리집 검사 감싸기. 외부 패키지 없음(Python 3.11+ 표준 라이브러리).

| 파일 | 하는 일 | 명령 (저장소 루트) |
|---|---|---|
| `config.py` | 대상·수수료·기간·시각·경로, 판단 시각(07:37 KST), 맞추기 끝 날짜(`FIT_UNTIL` 2026-06-30), 재생 기간, 대상의 성질(`TRAITS`), 진짜 계좌 id, 조사 조각 세운 날(`RESEARCH_SINCE`), 잡음 기준 | – |
| `prices.py` | 시세 받기(네이버·야후·업비트, 시간 제한 30초)·덧붙이기·읽기, `view`(그 시각에 끝나 있던 일봉만) | `python -m market.prices update / backfill / show` |
| `records.py` | 기록 종류별 칸(있을 때만 보는 칸 `OPTIONAL` 포함), 쓰기(칸이 틀리면 아무것도 안 씀), 검사(칸·id·날짜 순서·git HEAD와 비교) | `python -m market.records check` |
| `ledger.py` | 계좌: 목표 비중 → 주문, 체결(정산), 평가, 기록에서 되살리기(`rebuild`). `target_weight` = R_weights | – |
| `scoring.py` | 채점(방향·확률 점수·크기), 기준선 예측(기저율), 모아 보기, **한 번만 세기**(`first_only`, `mark_counted`), 평소 움직임·잡음 | – |
| `pieces.py` | 가격으로 대 보는 상태 7개(예/아니오/모름) | – |
| `calc.py` | 계산기: 대 보기 값 → 오를 확률·움직임 범위·결론·결론을 바꾸는 조각. 지식 파일 지문(`digest`, 줄바꿈 LF로 맞춤) | `python -m market.calc [날짜]` (보여 주기만) |
| `fit.py` | 조각의 숫자를 `FIT_UNTIL`까지의 시세로 기계적으로 맞추고 knowledge.toml 구역을 다시 씀. 주장에 더한 조건(`also`) 지원 | `python -m market.fit [--write]` |
| `replay.py` | 날짜 순서 재생 → `records/replay/`(1단계, 한 번 돌림), 보고 표 | `python -m market.replay report` |
| `versions.py` | 이전 버전 조각 모음 떠 두기·목록·검사, active 버전의 조각 | `python -m market.versions [save <id> "<왜>"]` |
| `research.py` | 볼 곳 목록(`places.toml`), 오늘 조사 질문, 볼 곳 성적표, 다시 볼 손 대 보기, 새 곳 시도 몫 | `python -m market.research places / questions [날짜]` |
| `hand.py` | 메인 입력(조사·손 대 보기·손 예측·비중·돌아보기·다시 보기·정정) 검사 → 기록 줄 | (daily가 부른다) |
| `daily.py` | **매일 실행**: prepare(시세→정산→채점→돌아볼 목록→조사 질문, 틀) / record(검사 후 한 번에 덧붙임) / report | `python -m market.daily prepare / record <입력> / report [--check]` |
| `report.py` | 일지·주간 결산 뼈대(숫자는 기록에서, 말은 `[[채울 것]]` 자리) | (daily가 부른다) |
| `checks.py` | 논리집 자동 검사(CHECKS·SOURCE·SEEN_UNTIL, 재생·실측 따로)와 분야 점검(AUDIT) | (logic이 부른다) |
| `logic.py` | PROVISO 코어(schema·score·audit)를 불러 이 논리집을 검사 | `python -m market.logic validate / check / audit` |

## 결정과 이유
- **미래를 못 보게 구조로 막는다**: 계산 함수는 `History`(어떤 시각에 끝난 일봉만 담은 읽기 전용 묶음)나 그 종가 목록만 받는다. 판단 시각 07:37에 ETF는 전날 종가까지(16:00에 끝난 것으로 봄), 코인은 이틀 전 일봉까지(일봉이 다음 날 09:00에 끝남). 재생·매일 실행 모두 view를 새로 만든다. 독 넣기 테스트가 증명한다.
- **체결**: 1단계 재생은 ETF 판단 날짜와 같거나 뒤인 첫 거래일 종가, 코인 판단 날짜 일봉 시가. **2단계 진짜 계좌는 주문에 판단한 실제 시각(`decided_at`)을 적고, 그 시각 뒤에 정해지는 첫 공식 가격에 체결한다**(늦은 실행에서 이미 나온 가격으로 사지 않게; 코인 일봉이 빠져도 다음 일봉에 체결). 정산은 체결 시각 순서대로, 모든 대상의 그 시각 가격을 알 때까지 기다린다 → 코인 D+2, ETF 대개 D+3 실행에서 정산. 사는 금액 원 단위 올림, 파는 금액 내림, 수수료 올림.
- **매일 실행의 판단 시각 = record를 돌린 실제 시각.** 그 시각까지 끝났어야 할 일봉이 시세 파일에 없으면 그 대상은 그날 예측·주문하지 않는다(`daily.skip_reason`; 시세를 그 일봉이 끝난 뒤에 받았는데도 없으면 ETF는 휴장으로 본다, 코인은 휴장이 없으니 건너뛴다). 낡은 시세로 예측하면 이미 나온 결과를 맞히는 셈이라서다.
- **prepare는 기록을 쓰지 않는다.** 계산(정산·채점)은 prepare와 record가 같은 함수(`daily.compute`)로 다시 한다. record는 모두 검사한 뒤 한 번에 덧붙인다(하나라도 틀리면 아무것도 안 씀). 같은 날 두 번 쓰지 않는다.
- **한 번만 세기**(결정 1): 예측은 매일 적고, 채점·성적표는 같은 (예측기·대상·기간·기준 날짜) 중 **처음 말한 것**만 센다. 고르는 규칙이 결과를 보기 전에 정해지고, 나중 것을 고르면 더 본 뒤의 말에 무게가 쏠려서다. 새 채점 줄은 `counted` 칸을 단다. 재생 기록도 검사(`checks`)에서는 같은 규칙으로 센다.
- **계산기** (PROVISO agent_usage `predict.py`와 같은 모양): 오를 확률의 승산 = 기준 × 켜진 곱하기들. 범위는 로그 반폭을 제곱합 제곱근으로 합치고 2배 폭을 넘으면 가운데 둘레로 자른다. 기준값이 없으면 '모름'. 설명에는 빼면 결론이 바뀌는 조각만.
- **맞추기**: 오를 확률은 로지스틱 회귀, 움직임은 log|수익률| 최소제곱. 겹치는 기간 표본이라 표준오차 × √기간. 범위 = 값 ± 표준오차. 방향이 어긋나거나 1을 넘나들면, 폭이 2배를 넘으면, 곱하기 수가 유효 표본 ÷ 10을 넘으면 세우지 않는다. 이전 버전(retired) 조각은 맞춘 기록(effect·fit·not_set)을 지우지 않는다.
- **손 예측 검사**(`hand.py`): 쓴 조각의 값은 그날 대 본 값과 같아야 하고, 크기 범위 폭 2배 이하, 이유·확신도 필수, 비중 합 1 이하. '잡음'은 움직임이 평소(60봉 하루 변동성 × √기간)의 0.25배보다 작을 때만.
- **판단 때 가격**(결정 3): prepare가 07:37 업비트 현재가를 받아 run 기록과 briefing에 남기고, 주문 수량을 잡는 데 쓴다. 채점 기준(끝난 일봉 종가)은 그대로.
- 예측 기록의 `predictor`: `calc` / `hand` / `version:<id>` / `baseline:half` / `baseline:base_rate`.

## 규칙
- 모든 네트워크 호출에 시간 제한(`config.NET_TIMEOUT` 30초). 네이버는 User-Agent가 없으면 응답 없이 걸린다(`prices.UA`).
- 기록은 `records.write`로만 쓴다(덧붙이기). 매일 실행의 기록은 `daily.record`로만 쓴다. 지난 줄을 고치는 코드를 만들지 않는다.
- 조각 id·문장에 대상 이름을 넣지 않는다(`config.ASSET_WORDS`, audit 오류). 조사 조각의 주장 범위는 대상 이름 대신 성질(`config.TRAITS`)로.
- knowledge.toml의 effect 숫자를 손으로 고치지 않는다(`fit --write`만, audit가 다시 맞춰 비교).
- 기본 저장소에서 `daily`에 `--day`/`--at`을 쓰지 못한다(지난 날짜로 예측하지 않는다). 흉내 내기는 `--root <임시 폴더>`(`daily.copy_for_simulation`).

## 다른 폴더와의 관계
- 읽음: `data/prices/*.csv`, `knowledge.toml`, `places.toml`, `versions/`, PROVISO-Nesy 코어(`PROVISO_PATH` 또는 형제 폴더 `../PROVISO-Nesy`).
- 씀: `data/prices/`(prices update), `records/`(재생은 `records/replay/`, 진짜 계좌는 `daily record`), `reports/`(daily report), `knowledge.toml`의 계산 조각 구역(fit --write), `versions/`(versions save), `out/daily/<날짜>/`(prepare의 중간 파일, git 제외).
- 시험: `tests/`.
