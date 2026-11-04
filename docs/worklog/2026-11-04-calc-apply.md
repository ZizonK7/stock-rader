# 2026-11-04 계산기 분석 반영 — 결정 D1–D10을 넣음

**사용자용 세 줄 요약**
1. 계산기가 말하는 '오를 확률'의 출발점을 '맞춘 기간의 평균'에서 동전(50%)으로 바꿨어요. 판단할 때 이미 값이 움직인 칸(지금은 코인 하루치)은 그 움직임으로 확률을 내는 '출발점' 조각이 먼저 답해요. 계산기 계좌도 손 계좌와 같은 식이라, 모르면 셋을 1/3씩 들어요.
2. 예측마다 '어느 층이 답했나'(계산기는 출발점·조각·동전, 손은 조각·출발점·기타)를 남겨요. 그래서 조각 덕에 맞힌 건지 판단 때 정보 덕인지 따로 볼 수 있어요. 조사 조각 자리(6개)가 차도, 근거를 확인한 새 까닭은 '씨앗 조각'(따로 12개, 관련 있는 날만 대 봄)으로 시험을 시작해요. 첫 씨앗은 '간밤 해외 값이 크게 움직이면 다음 날도 같은 쪽'이에요.
3. 바꾸기 전 논리집은 v4로 떠 두어 나란히 채점하고, 새 규칙은 오늘 뒤의 날로만 시험해요. 성적표가 '같은 상태가 이어진 구간'으로 세도록 바뀌어 반박 경고가 7개에서 2개로 줄었어요. 조각이 나아진 게 아니라 세는 법이 바뀐 거예요.

작업자: 대화 세션이 보낸 하위 에이전트 두 회차(Opus 5.5).
- **앞 회차**(지시서 p181): 코드·논리집·지시문과 시험을 거의 끝낸 뒤 컨테이너 재시작으로 끊겼다. 보고서는 남기지 못했다.
- **이번 회차**(지시서 p182, 2026-11-04 10:40–11:10 KST쯤): 작업 트리에 남은 diff(22개 파일 + `versions/v4.toml`)를 그대로 이어받았다. `git stash`·`checkout`은 쓰지 않았다. 시작할 때 diff를 scratchpad에 떠 두었다(`/tmp/claude-0/-home-user/2e605ecd-81dc-5c6d-a100-11a6dc5b37a0/scratchpad/p182/inherited.diff`).
- 두 회차 모두 기록(`records/*.jsonl`, `reports/`, `data/prices/`)을 쓰지 않았다. 네트워크는 쓰지 않았고, 커밋·push도 하지 않았다. PROVISO 코어도 고치지 않았다.

## 대조표 — 결정마다 앞 회차가 한 것 / 빠진 것 / 결정과 다른 것
앞 회차의 diff를 파일별로 읽고 결정과 대조했다. '이번 회차'는 빠진 것을 채운 것이고, '다름'은 결정과 다르게 들어간 곳이다. 다르게 들어간 곳은 그대로 두었고, 까닭은 아래 '메인 판단 필요'에 적었다.

| D | 앞 회차가 한 것 | 빠진 것 → 이번 회차가 채움 | 결정과 다른 것 |
|---|---|---|---|
| D1 사용자 의도 | 지시문 머리에 한 줄 | 루트 `CLAUDE.md`의 '사용자 의도' 2026-11-04 줄 둘, 새 절 '계산기 분석 반영' → **넣음** | 없음 |
| D2 동전 기준값 | `C_base_coin`(오를 확률 `fixed = true`, range [1,1] 아홉 칸), `C_base_vol` retired + replaced_by, `fit.py` 고정값 처리, audit 고정값 처리, 곱하기 일곱 note, `versions save v4`, v1 쉼 | v1을 쉰 까닭을 `versions/CLAUDE.md`에, `knowledge.toml` 머리 주석을 지금 기준으로 → **넣음**. `calc` 명령에 층 표시 | audit가 고정값을 '건너뛰는' 대신 **주장에 적은 range와 견준다**(더 엄격하다. 목적인 '맞춘 절편과 다르다는 오류가 안 남'은 같다) |
| D3 R_weights_v2 | `ledger.target_weight` v2, v1은 `target_weight_v1`(재생만 씀), 규칙 `R_weights` retired + `R_weights_v2` | 문서 → **넣음** | `account_vs_equal`를 '그대로' 두지 않았다. 규칙의 `seen_until` 뒤 첫 계좌 기록부터 늘어난 **비율**을 견준다(메인 판단 필요 3) |
| D4 출발점 조각 | `calc.start_p`(식·남은 시간·아래 끝 10분·가격 없으면 안 걸림·기준 봉이 끝나기 전 가격이면 안 걸림), `predict(…, decision)`, `daily.decision_of`가 넘김, `C_started_move`(fit 구역 밖) | 출발점 확률을 글에 소수 한 자리로(0.9987이 '100%'로 보였다). 문서 → **넣음** | ① 남은 시간의 시작을 '판단 시각(record 시각)'이 아니라 **판단 때 가격의 시각**으로 잰다(메인 판단 필요 4). ② 결정에 없던 것: 계산 조각 상한 8이 **숫자를 맞추는 조각만** 센다. 출발점 조각이 아홉째라 이게 없으면 audit 오류였다(메인 판단 필요 6) |
| D5 예측·입력 칸 | `layer`(계산기·이전 버전·손), 손 `info`(decisive가 비면 필수), 계산기 `analog`, `records.py` OPTIONAL·LAYERS | `info = "조각"`인데 decisive가 비면 거절(그대로 두면 기록의 layer가 거짓이 된다) + 시험 → **넣음** | 손 layer: decisive가 출발점 조각뿐이면 '출발점'으로 채운다(결정은 '조각'). 작은 확장이라 그대로 뒀다 |
| D6 baseline:all | `scoring.all_rate`, `daily.baselines` 셋째 줄(세 대상·종목) | 문서 → **넣음** | 종목의 계산기 줄은 '모름'으로 남기고 `baseline:all` 줄을 더했다. 결정 문구('계산기 모름 대신 이 줄')를 D2의 "'모름'은 그대로이되 종목은 D6의 기준선으로"와 맞춰 읽었다 |
| D7 성적표 | ① 동점 → 세지 않음, ② 양쪽 5구간(`MIN_SIDE_RUNS`, 구간 끊김 `GAP_DAYS` 7일), ③ `hand_vs_calc` 세부(대상·기간별, 판단 때 정보 칸 뺀 값), ④ `layer_vs_baselines`(60/12, 20일 덩어리 부트스트랩 1,000번) | 문서 → **넣음** | 없음. 구간을 끊는 '7일'은 앞 회차가 정한 숫자다(결정에는 '벌어지면 새 구간'만 있다, 메인 판단 필요 7) |
| D8 R_layer_v0 | `[[rule]]`(method·reason·hypothesis·v1·G_predict·layer_vs_baselines·seen_until·'시험 전에 적음' note) | 문서 → **넣음** | note에 적은 '크기는 같은 날 적중 수를 짝지어 본다'와 '3층 참고 칸이 두 번 연속 동전보다 나으면'을 볼 **검사 코드가 아직 없다**(메인 판단 필요 5) |
| D9 씨앗 조각 | ① `seed`·`MAX_SEED_PIECES` 12·근거 강도 '낮음' 검사, ② 손 대 보기에 값이 없어도 됨·성적표 '안 물음'·질문에 `[씨앗: …]` 표시·틀 `_seed_readings`, ③ audit '은퇴 후보' 경고(`RETIRE_AFTER_RUNS` 30)·지시문의 올리기·내리기, ④ `Q_overnight_lead`(seed)·`S_q_overnight_move`·`F_record_overnight`·씨앗 후보 주석, 볼 곳 둘의 covers | 올리기·내리기 규칙을 `CLAUDE.md`에 글로 → **넣음** | ① `S_q_overnight_move`의 답이 '예/아니오/모름'이 아니라 **오름/내림/아니오/모름**이다. 그래서 `checks._holds`가 예/아니오 밖의 셀 수 있는 답도 받게 고쳤다(메인 판단 필요 1). ② 근거 숫자가 결정('10-30~11-04 9/9')과 다르다. 기록으로 다시 세면 **10-22~11-03 17번 중 시가 방향 15·종가 방향 11, 10-30 뒤 6번은 시가 6/6·종가 4/6**이다. 이번 회차가 따로 다시 세어 같은 숫자를 얻었다(메인 판단 필요 2) |
| D10 지시문 | ① 2차 조사 → 근거 확인 뒤 씨앗, 랜덤 금지, ② `info`·코인 1봉은 출발점 값에서, ③ 씨앗 대 보기, ④ 은퇴 후보 처리, ⑤ 입력 예시에 `info`·씨앗 상태 | `info`의 '`조각`은 decisive가 있을 때만' 한 구절을 더했다. 입력 예시의 종목 줄에 남은 은퇴 조각 id(`Q_rate_up=예` → `Q_rate_up_shift=예`)를 고쳤다 | 없음 |

## D마다 한 것 / 파일:줄 / 확인 방법
줄 번호는 이번 회차가 끝났을 때의 작업 트리 기준이다.

**D1 사용자 의도**
- 한 것: '사용자 의도'에 2026-11-04 줄 둘을 넣었다. ① 목표만 주면 메인이 정하고 까닭을 적는다. ② 모르면 더 공부를 유지하고, 랜덤은 검토했지만 안 쓴다. 새 절 '계산기 분석 반영 (대화 세션, 2026-11-04)'에 D2–D10을 결정과 까닭으로 적었다.
- 파일: `CLAUDE.md` '사용자 의도' 끝 두 줄, '## 계산기 분석 반영' 1–9. 지시문 `docs/routine-prompt.md:24`.
- 확인 방법: 읽기.

**D2 방향 기본값 = 동전**
- 한 것:
  - `C_base_coin`(`knowledge.toml:951`): 오를 확률 기준값은 `fixed = true`, range [1.0, 1.0]이다. 움직임 기준값은 `C_base_vol`과 같은 흔들림 배수다. `seen_until` 2026-11-04.
  - `C_base_vol`(`:783`)은 retired + `replaced_by = ["C_base_coin"]`이고, note에 까닭(홀드아웃 숫자)을 적었다.
  - `fit.py`: `Claim.fixed`(`:150`)를 더했다. 고정값 효과는 맞춘 결과와 상관없이 주장의 range를 적는다(`:341`). 출력에 `fixed_note`(`:445`)를 붙였다.
  - audit: `checks._norm`이 `fixed` 표시까지 본다. 고정값 주장의 모양 검사도 있다(`checks.py` audit 안).
  - 곱하기 조각 일곱의 note에 '그대로 둔다(사후 고르기 금지)'를 적었다.
  - `versions/v4.toml`·`versions.toml`: v4를 떠 두었고 v1은 쉰다.
- 확인 방법:
  - `python -m market.fit`(쓰기 없음)을 돌리면 끝에 "고정값(맞추지 않음): C_base_coin up_odds [1, 1]"이 나온다.
  - **`knowledge.toml`을 scratchpad에 복사해 `fit.write(복사본)`을 돌리고 원본과 diff → 0줄.** `fit --write`를 다시 돌려도 고정값과 곱하기가 그대로라는 뜻이다.
  - `git show HEAD:knowledge.toml | diff - versions/v4.toml` → 같음. v4는 고치기 전 논리집 그대로다.
  - `python -m market.calc 2026-11-04`: 아홉 칸 모두 "오를 확률 50% (기준 50% 고정)"이다. 크기는 흔들림 배수다(예: 한국 묶음 1봉 1.90–3.80% = 3.87%의 0.491–0.982배). 미국 묶음 1봉만 `C_big_move` 움직임 곱하기가 걸려 층이 '조각'이다. 이 명령은 판단 때 가격을 넘기지 않으므로 코인 1봉도 '동전'으로 나온다(출력 끝에 그렇게 적었다).
  - 시험: `tests/test_calc_fit.py` `CoinBaseAndStart.test_fixed_base_is_coin_and_layer_coin`·`test_fixed_claim_is_not_fitted`, `tests/test_logic.py` `test_fixed_value_cannot_be_changed_by_hand`.

**D3 R_weights_v2**
- 한 것:
  - `ledger.target_weight`(`market/ledger.py:216`) = 1/3 + (p − 0.5) × 4/3, 0~0.5로 자르고, 모름이면 1/3이다.
  - 옛 식은 `target_weight_v1`(`:225`)로 남겼다. `replay.py:80`만 v1을 쓴다.
  - 주문 이유 글은 `R_weights_v2: …`(`daily.py`)다.
  - `knowledge.toml:419`의 `R_weights`는 retired다. `:430`에 `R_weights_v2`를 세웠다(seen_until 2026-11-04).
  - `checks.account_vs_equal(node_id)`(`checks.py:232`)는 seen_until 뒤 첫 기록부터 늘어난 비율을 견준다.
- 확인 방법:
  - `tests/test_ledger.py`의 v2 식과 `test_target_weight_v1_kept_for_replay`.
  - `python -m market.logic check`에서 `R_weights_v2`는 "시험 전 (2026-11-04 뒤 계좌 기록이 모자람)"이다. 옛 `R_weights`의 옛 판정은 재생 반박·실측 지지로 예전과 같다.
  - 흉내 내기 A에서 계산기 계좌 주문 이유는 셋 다 "R_weights_v2: 1주 오를 확률 0.5 → 목표 비중 0.3333"이었다.

**D4 출발점 조각 `C_started_move`**
- 한 것:
  - `calc.start_p`(`market/calc.py:233`): z = ln(가격 ÷ 기준 종가) ÷ (vol60 × √(남은 분 ÷ 1440)), p = `NormalDist().cdf(z)`.
  - 남은 시간 = `final_dt(next_bar(기준 날짜))` − 가격의 시각이고, 아래 끝은 `START_MIN_MINUTES` 10분(`:42`)이다. `config.next_bar`(`config.py:151`)를 새로 만들었다.
  - 걸리지 않는 경우: 첫 기간이 아니거나, 가격·vol60이 없거나, 가격이 기준 봉이 끝나기 전 것일 때.
  - 걸리면 `Result.p_up`이 이 값을 쓰고 `p_range`는 null이다. `used`·`decisive`에도 들어간다.
  - `daily.decision_of`(`daily.py:313`)가 run의 판단 때 가격을 넘긴다(`:252`). 이전 버전 모음에도 넘기지만 그 모음에는 출발점 조각이 없어 걸리지 않는다.
  - 조각은 `knowledge.toml:1537`(fit 구역 밖)에 있고, `claims = [{ on = "up_odds", op = "start", fixed = true, … }]`다.
  - 이번 회차: 글의 확률을 소수 한 자리로 바꿨다(`calc.py:215`). `calc` 명령에 층과 안내 줄을 붙였다(`calc.py:342` 뒤).
- 확인 방법:
  - 시험: `test_start_formula`·`test_start_boundaries`(남은 시간 아래 끝, 가격 없음)·`test_start_piece_is_loaded_from_knowledge`. `test_daily`의 나흘 흉내에서 출발점은 첫날 코인 1봉에만 걸린다(뒤 날들은 가격이 기준 봉 전 것).
  - 흉내 내기 A(실제 시세, 11-04 실제 판단 때 가격 104,729,000원 07:40:47): 코인 1봉이 '출발점'이었다. 기준 종가보다 +1.04%, 80분 남음, 60봉 흔들림 1.47% → z +3.00, p_up 0.9987.

**D5 예측·입력 칸**
- 한 것:
  - `Result.layer()`(`calc.py:198`)를 만들고 `record_fields`에 `layer`를 넣었다(`:295`).
  - `calc.analog`(`:317`): 750봉 창, 상태 7개가 같고 t + 기간 ≤ 마지막 봉, 결과는 {n, runs, eff, up_rate}이고 없으면 null이다. `daily.py:249,277`이 계산기 줄에 붙인다.
  - `records.py:54`의 OPTIONAL에 `layer`·`analog`를, `:59`에 `LAYERS`를 더했다.
  - `hand.INFO`(`hand.py:38`), `info_of`(`:64`), 손 줄의 `layer`(`:363`).
  - 틀에 `_info_form`(`daily.py:469`), `hand[].info`, `_calc_layer`를 넣었다.
  - 이번 회차: `info = "조각"`인데 decisive가 비면 거절한다(`hand.py:212`). `tests/test_daily.py` `test_info_required_without_decisive`에 그 경우를 더했다.
- 확인 방법:
  - 시험: `Analog.test_matches_brute_force_and_never_sees_the_future`(하나하나 센 값과 같고, 뒤 시세에 독을 넣어도 같다), `test_unknown_values_give_none`, `test_info_required_without_decisive`.
  - 흉내 내기 A·B의 예측 줄 `layer`(아래).

**D6 baseline:all**
- 한 것: `scoring.all_rate`(`scoring.py:65`, 표본 30개 미만이면 없음)를 만들고, `daily.baselines`(`daily.py:322`)가 세 대상과 종목에 셋째 기준선을 쓴다.
- 확인 방법:
  - 시험: `tests/test_scoring.py` `test_all_rate_uses_whole_known_history`, `tests/test_stocks.py`(종목에 `baseline:all` 줄).
  - 흉내 내기 A: 한국 묶음 1·5·20봉 0.538·0.557·0.575, 미국 묶음 0.552·0.612·0.670, 코인 0.515·0.528·0.544.
  - 흉내 내기 B: 종목에도 생겼다(예: 005380 1봉 0.471).

**D7 성적표**
- 한 것:
  - `_compare`는 동점이면 None을 낸다(`checks.py:120`).
  - `_runs`(`:124`)와 `_verdict`(`:143`): 양쪽 `MIN_SIDE_RUNS` 5구간(`:43`), `GAP_DAYS` 7(`:44`). 세부에 날 수와 구간 수, 동점을 보인다.
  - `piece_direction`·`research_direction`은 모르는 날도 칸에 넣어 구간을 끊는다.
  - `hand_vs_calc`(`:320`): 판정은 그대로이고, 세부에 '판단 때 가격을 적는 대상의 1봉을 뺀 값'과 대상·기간별 값을 보인다(`_info_cells` `:310`).
  - `layer_vs_baselines`(`:373`)와 `_block_boot`(`:356`)를 만들고 CHECKS에 등록했다(`:458`).
- 확인 방법:
  - 시험: `tests/test_research.py` `Verdict` 3개, `LayerVsBaselines` 3개.
  - `python -m market.logic check`: `H_hand_vs_calc` "짝 48개: 손 0.1996 vs 계산기 0.2451 … 13짝을 뺀 35짝: 손 0.2514 vs 계산기 0.2427"이고, 분석 보고서와 같다. `C_started_move`·`R_layer_v0`는 "시험 전 (판정 문턱에 닿은 칸 없음 …)"이다.
  - audit 경고가 7 → 2로 줄었다. 재생 반박이던 C_trend_60(재생·실측)·C_above_ma200·C_vol_high·C_big_move의 방향 시험이 구간이 모자라 '시험 전'이 됐다. 남은 경고는 C_above_ma200 효과 없음과 C_drop_5 재생 반박(6칸 중 4, 양쪽 5구간 이상인 칸만)이다.

**D8 R_layer_v0**
- 한 것: `knowledge.toml:441`. 방향 층·크기 층·답하지 않을 때·판정 시점·미리 적는 기대·버릴 조건을 method와 note에 적었다. check는 `layer_vs_baselines`, seen_until은 2026-11-04다.
- 확인 방법: `validate` 문제 없음. check에서 '시험 전'. `test_attached_to_layer_nodes`.

**D9 씨앗 조각**
- 한 것:
  - `checks.MAX_SEED_PIECES` 12(`checks.py:68`, 까닭 주석), 씨앗은 근거 강도 '낮음' 검사, `RETIRE_AFTER_RUNS` 30(`:71`), 은퇴 후보 경고(`:568`).
  - `research.seed_states`(`research.py:81`), `SEED_MARK`(`:33`).
  - `hand.validate`는 씨앗 상태의 값이 없어도 된다(`hand.py:114,135`).
  - `research_direction`은 상태의 answers 중 셀 수 있는 답(`known`, `checks.py:278`)을 받고, 값이 없으면 '안 물음'으로 센다.
  - 틀은 씨앗 상태를 `readings` 대신 `_seed_readings`로 안내한다.
  - `knowledge.toml`: `S_q_overnight_move`(`:198`), `F_record_overnight`(`:409`), 씨앗 후보 주석(`:458`), `Q_overnight_lead`(`:592`, seed, strength 낮음, 주장 둘: 오름 → 위, 내림 → 아래, scope equity, span 다음). `places.toml`의 `P_yahoo_chart`·`P_cboe_delayed_api`가 covers에 새 상태를 갖는다.
- 확인 방법:
  - 시험: `Seeds` 2개, `test_seed_cap_and_strength`, `test_retire_candidate_warning`, `test_seed_state_is_asked_only_when_relevant`.
  - 흉내 내기 A(물음 → hand_reading에 '오름', 손 한국 묶음 1봉이 `Q_overnight_lead=오름`으로 layer '조각')와 B(안 물음 → hand_reading에 그 상태 없음, record 통과).
  - `F_record_overnight`의 숫자는 `records/prediction.jsonl`의 손 1봉(묶음 둘, decisive 빈 것, 같은 기준 날짜는 처음 것, 이유 글에 간밤·선물·미국 장 등)을 `data/prices`의 다음 봉 시가·종가와 견주어 이번 회차가 따로 다시 셌다. 17번, 시가 15, 종가 11, 10-30 뒤 6번 6/6·4/6으로 같았다.

**D10 지시문**
- 한 것(`docs/routine-prompt.md`):
  - 머리에 사용자 의도(`:24`).
  - 3 '쪼개기'에 씨앗 상한과 은퇴 후보·씨앗 올리기(`:76–79`).
  - 4 '모르면 더 판다' 3·4: 근거 확인 → 씨앗, 랜덤 금지(`:122–123`).
  - 손 대 보기의 씨앗(`:130`).
  - 손 예측 1(`_calc_layer`, 코인 1봉 출발점, `:136`), 크기 4, 6 `info`(`:141`).
  - 목표 비중의 R_weights_v2.
  - 기록 6의 기준선 셋.
  - 입력 예시에 `info` 셋과 씨앗 상태(`:243–251`).
  - 이번 회차가 더한 것: '`조각`은 decisive가 있을 때만'과 예시의 `Q_rate_up_shift`.
- 확인 방법: 읽기. 흉내 내기에서 틀이 같은 칸(`info`, `_seed_readings`, `_calc_layer`)을 안내했다.

## 하루 흐름 흉내 내기 (기본 저장소 밖, `--root` 임시 폴더)
모두 `daily.copy_for_simulation(<scratchpad>/…/repo, prices_from=data/prices)`로 복사한 뒤 돌렸다. 입력은 scratchpad의 `sim_fill.py`·`sim_fill_b.py`로 채웠다(가짜 조사·손 예측). 기본 저장소의 `records/`·`reports/`·`data/prices/`는 바뀌지 않았다(`git status`).

**A. 2026-11-04, 빈 기록(계좌 시작일), 실제 시세 + 실제 판단 때 가격** (최종 코드로 `simC`에서 한 번 더 돌려 같은 결과)
- `prepare --no-fetch`(파이썬으로 ticker 넘김) → 0.
- 틀에는 `_info_form`, `_seed_readings`(`S_q_overnight_move`, 답 오름/내림/아니오/모름)가 있었다. `readings`에 씨앗 상태는 없었다. 질문에는 `[씨앗: 관련 있는 날만 — 안 물으면 이 줄을 지운다]`가 붙었다. 손 줄에는 `info`·`_calc_layer`가 있었다.
- 일부러 손 한 줄의 `info`와 decisive를 비운 입력 → record가 "결론 조각(decisive)이 없으면 info…" 오류 1건을 내고 **아무것도 쓰지 않았다**.
- 고친 입력의 record → 0. 쓴 것: run 1, account 4, reading 3, research 13, hand_reading 1, prediction 72, order 9.
- 예측 줄(predictor, layer):

  | predictor | layer별 줄 수 |
  |---|---|
  | calc | 동전 7, 조각 1(미국 묶음 1봉, `C_big_move` 움직임), 출발점 1(코인 1봉, 0.9987) |
  | hand | 조각 1(한국 묶음 1봉, `Q_overnight_lead=오름`), 출발점 2(info 출발점 1, decisive `C_started_move` 1), 기타 6 |
  | version:v2 · v3 · v4 | v2·v3 기준값 6 · 조각 3, v4 기준값 8 · 조각 1 |
  | baseline:half · base_rate · all | 각 9(layer 칸 없음) |

- 계산기 `analog` 예: 한국 묶음 1봉 {n 44, runs 14, eff 44.0, up_rate 0.5455}, 20봉 {n 36, runs 1, eff 1.8, up_rate 0.3889}. 미국 묶음 5·20봉은 null(같은 상태 조합이 750봉 안에 없음), 코인은 세 기간 모두 n 1.
- 계산기 계좌 주문 이유: 셋 다 "R_weights_v2: 1주 오를 확률 0.5 → 목표 비중 0.3333".
- `report` → 0(일지 뼈대). `report --check` → 1(채울 자리 6곳. 메인이 말로 채우는 자리라 정상). `records.check_folder` 오류 0.

**B. 2026-11-05, 진짜 기록을 임시 폴더로 복사 + 가짜 다음 봉** (내일 실행과 가장 비슷하게)
- 진짜 `records/`를 임시 폴더로 복사했다(읽기만). ETF·종목 11-04 봉과 코인 11-03 봉은 가짜로 덧붙였다(직전 종가 +0.5%, 코인은 판단 때 가격). 판단 때 가격은 105,200,000원 07:38로 넣었다.
- 틀: 든 종목 셋의 `stock_readings`(S_q_earnings_beat), 돌아보기 11건, 다시 보기 5건이 나왔다.
- 첫 입력은 돌아보기 '잡음' 핑계 5건 거절(기존 장치가 그대로 작동). 고친 뒤 씨앗을 **안 물은** 입력의 record → 0. 쓴 것: fill 8, score 71, account 4, reading 6, research 12, hand_reading 1, prediction 117, order 9, review 11, reading_check 5.
- 예측 줄: calc 동전 7·모름 9(종목)·조각 1·출발점 1, hand 기타 17·출발점 1, version:v2·v3·v4(위와 같은 모양), baseline 셋 각 18.
- 종목에도 `baseline:all`이 생겼다(005380 1·5·20봉 0.471·0.501·0.505).
- hand_reading에 `S_q_overnight_move`가 없었다(안 물음).
- 채점 71건에 `version:v1` 6건이 있었다. 쉰 버전도 기한이 된 지난 예측은 채점된다.
- `report` → 0. `records.check_folder` 오류 0.

## 확인한 것 (명령과 결과)
저장소 루트에서 `export PROVISO_PATH=/home/user/proviso-nesy PYTHONIOENCODING=utf-8`로 돌렸다. 아래는 이번 회차 끝의 작업 트리 기준이다.

| 명령 | 결과 |
|---|---|
| `python -m unittest discover -s tests -t .` | **115개 OK**(약 30초). 이어받은 때도 115개였다. 이번 회차는 새 시험 함수를 더하지 않고 `test_info_required_without_decisive`에 한 경우를 더했다 |
| `python -m market.logic validate` | 문제 없음(목표 6, 상태 17, 사실 23, 가설 25, 규칙 3) |
| `python -m market.logic audit` | 오류 없음, **경고 2**(C_above_ma200 세운 효과 없음, C_drop_5 재생 방향 반박), 참고 3(조건 칸 없는 개정: C_base → C_base_vol, C_base_vol → C_base_coin, R_weights → R_weights_v2. 셋 다 '이유 틀림 → 새 버전'이라 조건을 더하지 않는 것이 맞다) |
| `python -m market.logic check` | 끝까지 돎. 새 노드 넷(C_base_coin, C_started_move, R_weights_v2, R_layer_v0)과 Q_overnight_lead는 시험 전 |
| `python -m market.records check` | 기록 검사 통과 |
| `python -m market.fit` (쓰기 없음) + 복사본 `fit.write` diff | 고정값 안내 줄이 나오고, 복사본 diff는 0줄 |
| `python -m market.calc 2026-11-04` | 아홉 칸 50%(고정), 흔들림 배수 크기, 층 동전 8·조각 1 |
| `python -m market.versions` | v1 쉼, v2·v3·v4 active |
| `git status --short records reports data` | `records/CLAUDE.md`(문서)만 바뀌었다. `*.jsonl`·`reports/`·`data/prices/`는 그대로다 |
| `git diff`에서 이메일·토큰·비밀값 낱말 훑기 | 없음 |

## 이번 회차가 바꾼 파일
- 코드:
  - `market/hand.py`: `info = "조각"`인데 decisive가 없으면 거절.
  - `market/calc.py`: 출발점 확률을 소수 한 자리로, `calc` 명령에 층과 안내 줄.
  - `tests/test_daily.py`: 위 거절의 시험.
- 논리집·지시문:
  - `knowledge.toml`: 머리 주석만(현재 기준: 동전·fixed·출발점·상한·R_layer_v0·씨앗·셀 수 있는 답).
  - `docs/routine-prompt.md`: 두 곳(위 D10).
- 문서: 루트 `CLAUDE.md`, `docs/design.md`, `market/CLAUDE.md`, `records/CLAUDE.md`, `versions/CLAUDE.md`, `tests/CLAUDE.md`, 이 보고서.
- 나머지 diff(`market/` 아홉 파일, 시험 일곱 파일, `places.toml`, `versions/`, 논리집 본문, 지시문 대부분)는 앞 회차의 것이다.

## 확인 못 한 것
- 진짜 예약 실행(네트워크로 시세·업비트 현재가를 받는 것)은 돌리지 않았다. 흉내 내기 B는 가짜 다음 봉으로 돌렸다.
- `layer_vs_baselines`가 '지지/반박/모름'을 내는 경로는 시험(가짜 기록)으로만 확인했다. 진짜 기록에서는 문턱(60·12)에 닿으려면 몇 달이 걸린다.
- '은퇴 후보' 경고는 시험으로만 확인했다. 지금 정식 조사 조각 중 세운 뒤 30 판단일이 지난 것은 없다(가장 이른 2026-10-21 조각도 14일).
- `F_record_overnight`의 '간밤 값으로 정했다'는 이유 글의 낱말로 가렸다. 사람이 한 줄씩 읽어 확인하지는 않았다.

## 알려진 문제
- **출발점 확률이 아주 확신에 차기 쉽다.** 11-04 코인 1봉은 +1.04%에 80분 남아 0.9987이었다. 식은 정규분포를 가정하는데, 분석 보고서에 따르면 이 분야의 3σ 넘는 날은 정규의 5–8배다. 남은 시간에 큰 움직임이 오면 확률 점수 하나가 크게 깎인다. 미리 정한 식이라 맞추지 않았고, `R_layer_v0`의 판정으로 본다.
- `analog`는 미국 묶음·코인에서 거의 비거나 n 1이다. 상태 7개 조합이 750봉 안에서 드물다. 참고 칸의 판정(R_layer_v0 8)까지 오래 걸린다.
- `layer_vs_baselines`의 1주·4주 '겹치지 않는 표본'은 기준 날짜 목록을 기간 칸씩 건너뛰어 고른다. 빠진 날이 있으면 실제 간격이 기간보다 길어진다(보수적인 쪽).
- 주간 결산의 계좌 이름은 그대로 '계산기 계좌(R_weights)'다(규칙 묶음 이름으로 읽힌다).
- 시험 시간이 13초 남짓에서 30초 남짓으로 늘었다(참고 칸·부트스트랩 시험).

## 다음 작업자에게
- 내일 첫 실행 뒤 볼 것: 손 1봉의 `layer`가 '조각'(씨앗을 대 봄)인지 '출발점'·'기타'인지, 코인 1봉 계산기가 '출발점'인지, 계산기 계좌 주문이 R_weights_v2인지.
- 계산 조각 상한 8이 찼다(숫자를 맞추는 조각 8: C_base_coin + 곱하기 일곱). 새 계산 조각은 하나를 은퇴시키거나 쪼개야 들어간다.

## 메인 판단 필요
1. **`S_q_overnight_move`의 답**(D9④는 예/아니오/모름): 앞 회차가 **오름/내림/아니오/모름**으로 두었고, 이번 회차도 그대로 뒀다.
   - 까닭: 조각의 주장은 '다음 봉은 **같은 쪽**'이다. '예(크게 움직였다)'만으로는 어느 쪽인지 적을 곳이 없어, `research_direction`이 주장을 시험할 수 없다.
   - 이를 위해 `checks._holds`가 상태의 answers 중 예/아니오 밖의 셀 수 있는 답(모름·일부 제외)도 `when`과 견주게 고쳤다. 다른 상태에는 영향이 없다(answers가 없거나 예/아니오/모름뿐).
   - 결정대로 예/아니오/모름으로 되돌리려면 주장을 한쪽(예 → 위)만 남기거나 상태를 둘로 나눠야 한다.
2. **`F_record_overnight`의 숫자**: 결정은 '10-30~11-04 하루 예측을 이 방법으로 9/9'였다. 기록으로는 재현하지 못했다.
   - 다시 세면(같은 기준 날짜는 처음 말한 것만, 11-04 판단은 아직 채점 전) 10-22~11-03에 17번이다. 다음 봉 **시가** 방향은 15/17, **종가** 방향(채점 기준)은 11/17이다. 10-30 뒤 6번은 시가 6/6, 종가 4/6이다.
   - 공개 저장소의 사실 노드라 다시 센 숫자를 그대로 두었다. 시가 쪽이 9/9에 가깝다. 메인이 시가를 셌다면 note에 그 뜻을 더할지 정해야 한다.
3. **`account_vs_equal` 고침**(D3는 '그대로'): `R_weights_v2`는 이어지는 같은 계좌(`live:calc`)를 물려받는다. 그대로면 v2의 판정이 v1 시절 성적(10-21~11-04)을 그대로 안고 시작한다. 그래서 seen_until 뒤 첫 기록부터 늘어난 비율로 견주게 했다. v1의 판정은 예전 셈과 같다(시작일 두 계좌 모두 1,000만 원). 되돌리면 v2가 첫날부터 '지지'로 나온다.
4. **출발점의 남은 시간을 언제부터 재나**: 결정은 '목표 봉 끝 − 판단 시각'이다. 코드는 '목표 봉 끝 − **판단 때 가격의 시각**(prepare가 받은 업비트 현재가 시각)'으로 잰다. 가격을 받은 뒤 record까지의 움직임은 모르는 몫이라 이쪽이 식의 뜻에 맞다고 봤다. record 시각으로 바꾸면 남은 시간이 짧아져 확률이 더 0·1 쪽으로 간다.
5. **R_layer_v0 note가 약속한 검사 중 코드가 없는 것**: ① 크기의 같은 날 적중 수 짝 비교(부호 검정), ② 3층 참고 칸(`analog`)의 확률 점수. `layer_vs_baselines`는 방향 확률 점수만 본다. 1봉 판정(약 2–3달 뒤) 전에 만들지 정해야 한다.
6. **계산 조각 상한을 '숫자를 맞추는 조각만' 세게 바꾼 것**(결정에 없음, 앞 회차): 출발점 조각이 아홉째라 이 고침 없이는 audit가 상한 오류를 낸다. 상한의 까닭('맞춘 숫자가 많으면 외워서 맞춘다')에 맞춰 미리 정한 식은 뺐다. 받아들일지 정해야 한다. 지금 맞추는 조각이 8/8로 찼다.
7. **구간을 끊는 7일(`GAP_DAYS`)**: 결정에는 '벌어지면 새 구간'만 있었다. 앞 회차가 7일로 정했다(주말·명절 휴장 5일 안팎은 잇는다). 기록에서 고른 숫자는 아니다.
8. 작은 확장 둘(그대로 둠): 손 decisive가 출발점 조각뿐이면 layer '출발점'(결정은 '조각'). `info = "조각"`인데 decisive가 비면 거절(이번 회차).

## 메인 결정 (2026-11-04, 보고서를 읽고)
'메인 판단 필요' 여덟에 대한 답. 모두 시험 전(2026-11-04 뒤 기준 날짜로만 시험)이라 지금 정해도 사후 맞춤이 아니다.
1. `S_q_overnight_move`의 답 오름/내림/아니오/모름 — **받아들임**. '같은 쪽'을 시험하려면 방향이 있어야 한다.
2. `F_record_overnight` 숫자 — **다시 센 값(17번 중 시가 15·종가 11)으로 받아들임**. 메인의 '9/9'는 run 메모의 말을 옮긴 것이고 기록으로 센 값이 아니었다.
3. `account_vs_equal`을 seen_until 뒤 늘어난 비율로 — **받아들임**(v2가 v1 성적을 물려받지 않게).
4. 남은 시간을 판단 때 가격의 시각부터 — **받아들임**(가격과 시각은 짝이어야 한다).
5. R_layer_v0 note의 크기 짝 비교·참고 칸 확률 점수 검사 코드 없음 — **다음 할 일로**(첫 판정 시점인 기준 날짜 60개가 모이기 전에 만들면 된다).
6. 계산 조각 상한 8을 '숫자를 맞추는 조각만'으로 — **받아들임**. 상한의 까닭('외워서 맞추기')은 맞춘 숫자에만 해당한다.
7. 구간을 끊는 7일 — **받아들임**(1주 기간과 같은 길이, 미리 정한 값).
8. decisive가 출발점 조각뿐이면 손 layer '출발점', info '조각'인데 decisive가 비면 거절 — **받아들임**.
- **더한 것(메인, 커밋 전)**: 출발점 확률을 **0.10–0.90으로 자른다**(`calc.START_P_MIN`). 작업자가 짚은 위험대로 흉내 내기에서 0.9987이 나왔다. 이 분야는 큰 움직임이 정규분포보다 5–8배 잦아(분석 E2) 극단 확률이 한 번 빗나가면 확률 점수가 1 가까이 깎인다. 양 끝은 기록에서 고른 값이 아니라 시험 전에 정한 값이다. 테스트·논리집 note·CLAUDE.md·design.md에 같이 적었다.
