# 2026-11-03 논리집 점검 반영 — 제안 여섯을 넣음

**사용자용 세 줄 요약**
1. 점검 보고서([2026-11-03-logic-check.md](2026-11-03-logic-check.md))의 고침 여섯을 넣었어요(사용자 말 "반영해."). 계산기의 움직임 크기는 이제 '최근 1년 흔들림'에 맞춰 늘고 줄어요.
2. 바뀌기 전 논리집은 `versions/v3`로 떠 두었고, 내일부터 같은 날짜로 나란히 채점돼요. 새 조각은 모두 오늘 뒤의 기록으로만 시험해요. 결과를 보고 낸 생각이라 재생 숫자(크기 적중 164/680, 기준선 164)는 '맞춰 본 것'으로만 적었어요.
3. 내일 07:37 실행부터 바뀌는 것이 셋 있어요. 금리 '올리는 쪽' 질문을 다시 묻고, 공포 지수는 '1년 평균보다 높나'로 딱 잘라 답해요('일부' 금지). 실적이 좋았던 종목을 대 보는 날에는 그렇지 않은 종목 3개도 같이 대 봐요.

작업자: 대화 세션이 보낸 하위 에이전트(Opus 5.5), 2026-11-03 23:45–23:59 KST(커밋 전까지). 기록(`records/`, `reports/`, `data/prices/`)은 쓰지 않았고 네트워크로 시세를 받지 않았다.

## 한 일
| # | 고침 | 바꾼 곳 |
|---|---|---|
| 0 | 지금 모음을 떠 둠: `python -m market.versions save v3 "…"` (지문 f380e5a5fa5e). 이제 active는 v1·v2·v3이고, 상한 3개가 찼다 | `versions/v3.toml`, `versions/versions.toml` |
| 1 | **C_drawdown → C_drawdown_volup**(조건 흐림). 움직임 주장에만 `also = { S_vol_high = "예" }`를 더했다. 옛 조각은 retired + replaced_by. 맞추기 결과 효과는 미국 묶음 1봉 ×[1.54, 1.98], 5봉 ×[1.04, 1.82]만 섰다(한국 묶음 1봉은 세우지 않음) | `knowledge.toml` |
| 2 | Q_rate_up_shift 주장에 `also = { S_q_rate_up = "예" }`를 붙였다. `research_states`는 이제 조사 조건을 모두 묻는다(첫 것만이 아니라). `questions`의 '쓰는 조각'도 모든 조건으로 센다 | `knowledge.toml`, `market/research.py` |
| 3 | `research_direction` 세부에 '세지 않은 채점(예/아니오가 아닌 값)'의 수를 보인다(일부·모름·안 물음) | `market/checks.py` |
| 4 | **종목 비교군**: per_asset 상태에 '예' 종목이 있는 날은 '아니오' 종목이 `config.STOCK_CONTROL_MIN = 3`개 이상 있어야 한다(`hand.validate`가 거절). 틀 `_stock_control`은 고르는 순서(직전 열흘 실적을 낸 다른 회사 먼저, 모자라면 목록 순서)를, briefing은 같은 안내를 준다. 성적표는 종목을 한 칸(`checks.POOLED` = '종목 묶어 봄')으로 묶어 같은 날의 '예' 종목과 '아니오' 종목을 견준다. 지시문의 '종목마다 묻는 질문'과 '종목 대 보기', 입력 예도 고쳤다 | `market/config.py`, `market/hand.py`, `market/daily.py`, `market/checks.py`, `docs/routine-prompt.md` |
| 5 | **C_base → C_base_vol**(이유 틀림 → 새 버전). 움직임 기준값 주장에 `scale = "vol250"`을 단다. `fit.fit_one`은 움직임을 그때의 '최근 250봉 하루 변동성 × √기간'(`pieces.vol250_series`, `Samples.units`)으로 나눈 배수로 맞추고(곱하기도 같은 배수 위에서, 2026-06-30까지만), 효과에 `scale`을 적는다. `calc.predict(…, numbers)`는 판단 때의 `vol250` × √기간 × 100을 곱한다. 숫자가 없으면 움직임은 '모름'이다. `daily`(계산기·이전 버전·종목)와 `replay`, `calc` 명령이 대 보기 숫자를 넘긴다. audit의 효과 비교(`_norm`)도 `scale`을 본다 | `market/pieces.py`, `market/fit.py`, `market/calc.py`, `market/daily.py`, `market/replay.py`, `market/checks.py`, `knowledge.toml` |
| 6 | **Q_risk_off → Q_risk_off_level**(대 보는 방법 고침). 새 상태 `S_q_fear_above_year`는 "공포 지수의 판단 직전 마지막 값 > 최근 1년 평균"이고 `answers = ["예", "아니오", "모름"]`이다. 손 대 보기에 그 밖의 값('일부')을 쓰면 `hand.validate`가 거절한다. 옛 상태 `S_q_risk_off`는 지우지 않고 note를 달았다. 볼 곳 18곳의 `covers`에 새 상태를 더했다 | `knowledge.toml`, `market/hand.py`, `places.toml` |
| – | 문서 | 루트 `CLAUDE.md`('지금 상태', 새 절 '논리집 점검 반영', '개별 종목' 6), `market/CLAUDE.md`, `docs/design.md`(새 소절), `docs/routine-prompt.md`, `versions/CLAUDE.md`(v2·v3 줄), `tests/CLAUDE.md`, `records/CLAUDE.md`(by_asset), `knowledge.toml` 머리 주석 |
| – | 시험 | `tests/test_calc_fit.py` ScaledBase 4개, `tests/test_research.py` 2개, `tests/test_daily.py` 1개, `tests/test_stocks.py`(가짜 종목 S5, 비교군 도우미, '비교군 모자람' 거절, 비교군 종목 줄, 묶은 칸). `tests/test_logic.py:42`의 숫자는 다시 맞춘 값 `[1.11, 1.25]`로 바꿨다. 효과 숫자는 함께 풀리므로 다른 조각을 바꾸면 이 숫자도 바뀐다 |

맞춰 본 것(시험 아님, 결과를 본 뒤 낸 생각): 반영한 코드와 논리집으로 재생·실측 계산기 예측을 다시 계산했다. 저장된 대 보기 값과 숫자를 썼고, 같은 기준 날짜는 한 번만, 세 대상만 셌다. 크기 적중은 아래와 같다. note에도 같은 숫자를 '맞춰 본 것'으로 적었다.

| | 재생 (2026-07~10) | 실측 (10-21~11-03 실행에서 채점된 것) |
|---|---|---|
| 바꾸기 전 계산기 | 136/680 | 8/48 |
| 기준선(최근 250봉 비율) | 164/680 | 17/48 |
| **바꾼 뒤 계산기** | **164/680** (한국 43/193, 미국 59/193, 코인 62/294) | **22/48** (한국 7/14, 미국 5/14, 코인 10/20) |
| 조각별 (바꾼 뒤, 그 효과가 쓰인 것) | C_vol_high 65/273 vs 63, C_drawdown_volup 4/20 vs 3, C_big_move 2/17 vs 7 | C_big_move 1/2 vs 1 |

오를 확률 쪽은 바뀌지 않았다(오를 확률 주장과 숫자가 그대로다).

## 확인한 것 (명령과 결과)
저장소 루트에서 `export PROVISO_PATH=/home/user/proviso-nesy PYTHONIOENCODING=utf-8`로 돌렸다.

| 명령 | 결과 |
|---|---|
| `python -m market.versions save v3 "…"` | 떠 둠: v3 (지문 f380e5a5fa5e) |
| `python -m market.fit --write` | 계산 조각 구역을 다시 씀. 움직임 기준값은 이제 배수다(예: 한국 묶음 1봉 [0.491, 0.982], 코인 1봉 [0.385, 0.77]) |
| `python -m market.logic validate` | 문제 없음 (상태 16, 가설 22) |
| `python -m market.logic audit` | 오류 없음, 경고 7 (C_drawdown 두 줄이 빠짐; 새 조각은 시험 전). 참고 1줄: "조건 칸이 없어 쪼갰는지 못 본 개정 1건 (C_base → C_base_vol)" — 이유 틀림에 따른 새 버전이라 조건을 더하지 않은 게 맞다 |
| `python -m market.records check` | 기록 검사 통과 |
| `python -m unittest discover -s tests -t .` | 92개 OK (85 → 92) |
| `python -m market.logic check` | Q_rate_up_shift 세부에 `세지 않은 채점: S_q_rate_up=안 물음 9`, Q_risk_off_level·C_base_vol·C_drawdown_volup은 시험 전 |
| `python -m market.calc` | 한국 묶음 1봉: 움직임 1.90–3.80% [기준: 최근 250봉 흔들림 × √1 = 3.87%의 0.491–0.982배] |
| **매일 실행 흉내 내기** (임시 폴더): `daily.copy_for_simulation(<tmp>, prices_from=data/prices)`, 이어서 `python -m market.daily prepare --no-fetch --root <tmp> --day 2026-11-03 --at 07:40` → 틀 채우기 → `record --root <tmp> --day 2026-11-03 --at 07:55` → `report`·`report --check` | prepare: 틀 readings에 `S_q_rate_up`·`S_q_fear_above_year`가 있고 `S_q_risk_off`는 없다. `_stock_control`이 나오고 계산기 설명에 흔들림 기준이 나온다. record: '예' 종목 1 + '아니오' 2면 **거절**("비교군을 3개 이상"), 아무것도 쓰지 않음. '아니오' 3이면 기록됨(run 1, account 4, reading 7, research 12, hand_reading 1, prediction 99, order 9). by_asset에 '예' 1 + '아니오' 3, 비교군 종목에는 계산기('모름')·기준선 줄만 있다. 한국 묶음 계산기 범위 1.90–3.80 / 5.15–10.28 / 7.47–14.93%. 흉내 폴더 `records.check_folder` 문제 없음. report는 만들어졌고 `--check`는 채울 자리 6곳(메인이 채울 말 자리라 정상) |

흉내 내기는 기록이 빈 임시 폴더(첫날)에서 했다. 진짜 기록과 이어지는 정산·채점은 기존 시험(`test_daily`, `test_stocks`)이 맡는다.

## 확인 못 한 것
- 진짜 내일(11-04) 실행에서 조사 에이전트가 공포 지수의 1년 평균을 실제로 찾는지는 모른다. 못 찾으면 '모름'이 되고, 그날 그 조각은 시험에서 빠진다.
- 비교군 3개를 고르는 데 드는 조사량. 목록 종목(지금 16개)의 직전 열흘 실적 발표 여부를 같이 물어야 한다.
- 비교군 종목 줄이 늘어 일지 표가 어떻게 보이는지는 흉내 첫날만 봤다. 종목 표는 든 종목만 싣는다.
- `research_direction`의 묶은 칸은 같은 날 종목끼리 함께 움직이는 몫을 빼지 않는다. 그래서 묶은 칸의 수만큼 독립된 증거는 아니다.

## 알려진 문제
- 버전 상한이 찼다(v1·v2·v3 active 3/3). 매일 실행이 다음에 쪼개려면 하나를 `active = false`로 돌려야 한다(지시문 '쪼개기' 1에 이미 적혀 있다).
- `tests/test_logic.py:42`가 효과 숫자를 글자 그대로 쓴다. 다른 조각의 모양만 바꿔도 함께 풀리는 맞추기 때문에 이 숫자가 바뀐다.
- C_base_vol·C_drawdown_volup은 `seen_until = 2026-11-03`이라 기준 날짜가 11-04 이후인 예측부터 센다. 묶음 상품은 11-05 실행, 코인은 11-06 실행부터다(C_trend_20_calm과 같은 관례).

## 다음 작업자에게
- 내일부터 `version:v3`(바꾸기 전)와 `calc`(바꾼 뒤)가 같은 날의 대 보기로 나란히 채점된다. 크기 적중은 기준선(`baseline:base_rate`)과 함께 보면 된다.
- Q_rate_up_shift는 S_q_rate_up이 다시 기록되는 11-04부터 두 조건이 모두 '예'인 날이 생길 수 있다. 10-31~11-03 채점은 늘 '안 물음'으로 남는다.

## 메인 판단 필요
- 묶은 칸(종목 한 칸) 판정을 성적표의 다른 칸과 같은 무게로 볼지. 지금은 같은 `_verdict` 규칙(양쪽 5개 이상, 80% 맞음이면 지지)이다.
