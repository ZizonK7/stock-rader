# 2026-11-04 — 조사를 '오늘 판단용 빠른 정보'로, 숫자 상태와 문턱 조건 (팀 B, 결정 E4·E5·E6)

**사용자용 세 줄 요약**
1. 아침 조사가 '무슨 일이 있었나'를 넓게 묻던 것을, 오늘 판단에 바로 쓰는 네 가지로 바꿨어요: 간밤에 해외 값이 얼마나 움직였나, 오늘·내일 무슨 발표가 있고 시장은 뭘 예상하나, 어제 누가 사고 팔았나, 밤사이 공시·뉴스. '지난주 왜 올랐나' 같은 풀이 질문은 뺐어요.
2. 조사 답마다 숫자(이름_단위 → 값)와 그 정보의 시각을 꼭 적게 했어요(11월 5일부터 빠지면 기록이 거절돼요). 판단 시각보다 늦은 정보도 거절돼요.
3. '간밤에 크게 움직였나', '발표가 예상 밖이었나', '외국인이 평소보다 많이 샀나'는 이제 메인이 아니라 코드가 숫자로 예/아니오를 정해요. 이 셋으로 '그 쪽으로 더 간다'는 씨앗 조각 셋을 세웠는데, 체결이 시가로 바뀌면 이미 시가에 들어간 소식이라 기대는 낮아요(맞추기 기간 시세로 세어 보니 받쳐 주지 않음 — 반대 근거로 달아 둠).

에이전트: 팀 B 구현 하위 에이전트 (Opus 5.5, effort xhigh). 하위 에이전트 호출: 없음.
작업 폴더: `/home/user/sr-B`(stock-rader worktree), 브랜치 `fast-B`(시작 때 `git rev-parse --abbrev-ref HEAD` → `fast-B`, `pwd` → `/home/user/sr-B`, HEAD d79b91b).
커밋: 지시서대로 브랜치 `fast-B`에 커밋함(push 안 함). 기록(`records/*.jsonl`, `reports/`, `data/prices/`)은 쓰지 않았다. 시세를 받지 않았다. PROVISO-Nesy는 고치지 않았다. 팀 A 몫 파일(config.py, ledger.py, scoring.py, fit.py, calc.py, versions/, 계산 조각 구역, 지시문 6·7절)은 고치지 않았다.

## 결정마다 한 것 / 파일:줄 / 확인 방법
줄 번호는 커밋 때의 작업 트리 기준이다.

**E4 조사 각도 개편**
- 한 것:
  - 각도를 시장별 넷(금리·환율·정책 / 한국 / 미국 / 코인)에서 정보 종류 넷으로 바꿨다: 간밤 반응 / 일정·예상치 / 수급 / 공시·뉴스(`market/research.py:40` `ANGLES`).
  - 각도마다 '각도 질문'(`q:<날짜>:now:<각도>`, `ANGLE_ASK` `:43`)이 판단 직전까지의 숫자를 묻는다. 예전의 '열린 질문'(앞으로 며칠~4주에 영향을 줄 새 일)은 없앴다. 공시·뉴스 각도가 '조각 목록에 없는 판단이 필요한가'를 이어 묻는다.
  - 각도별 숫자 이름 안내 `NUMBER_KEYS`(`:57`)를 두었다. 이름 끝 낱말이 단위다.
  - 2차 조사 질문 `DEEP_ASK`·`deep_question`(`:327`·`:332`)도 같은 네 각도를 대상 하나에 맞춘다. '최근 1주 왜 움직였나'는 뺐다. 틀에 `_deep_form`으로 들어간다.
  - 살아 있는 조사 상태의 angle을 새 넷에 다시 붙였다(`knowledge.toml` 상태 구역; 질문 ask는 그대로). 볼 곳 69곳의 angle도 모두 다시 붙였다(`places.toml`).
  - 조사 칸 `numbers`·`as_of`를 더했다:
    - 기록 칸: `records.py:63`(OPTIONAL), 이름 규칙 `NUMBER_UNITS`·`NUMBER_KEY` `:73`, 검사 `number_errors` `:180`, `as_of_time` `:193`.
    - 입력 요구: `research.FAST_SINCE` = 2026-11-05 판단부터 `hand.validate`가 모든 조사 답에 요구한다(`hand.py:185` 부근). numbers가 비면 경고, as_of가 판단 시각보다 늦으면 거절한다.
    - 판단 시각은 `daily.record`가 ctx에 넘긴다(`daily.py:547` 한 줄).
  - briefing·`questions` 명령이 질문마다 적을 숫자 이름을 보인다(`daily.py:435`, `research.py` main).
  - 틀의 research 줄에 `numbers`·`as_of`·`_numbers`(이름과 뜻)를, 머리에 `_numbers_form`을 넣었다(`daily.py:484`).
- 확인 방법:
  - `python -m market.research questions 2026-11-05` → 상태 질문 11개 + 각도 질문 4개, 숫자 상태 질문 앞에 `[숫자: …]`, 질문마다 '숫자(numbers): …'가 나오고 끝에 numbers·as_of 안내가 붙는다. 모든 asked는 300자 안이다(최장 230자).
  - 시험: `tests/test_fast_research.py` `Questions`·`RecordFields`·`FastInput`.

**E5 숫자 상태와 문턱 조건**
- 한 것:
  - 상태에 `number = { from, per?, rule, unit }`을 둔다. `per`(잣대)는 결정의 `{ from, rule, unit }`에 내가 더한 칸이다(아래 메인 판단 필요 5).
  - `research.numeric_states`(`:147`), rule 문법 `parse_rule`(`:133`)이 있다. rule은 정규식 하나로 '[abs] >|>=|<|<= 수'만 받고 eval은 쓰지 않는다. 값은 `number_value`(`:183`)가 정한다.
  - `hand.resolve_numbers`(`hand.py:95`)가 숫자를 찾는다. 순서는 readings[상태].numbers → 그 상태를 물은 조사 답 → 그날 다른 조사 답이다.
  - 찾은 숫자를 rule로 견줘 예/아니오를 정한다. 숫자가 없으면 모름이고, 씨앗은 '안 물음'으로 둔다.
  - 메인이 적은 값이 다르면 거절한다. 이유·볼 곳이 비어 있으면 코드가 채운다(볼 곳은 숫자가 나온 조사 답의 '찾음' 곳).
  - 쓴 숫자는 `hand_reading.numbers`(상태 → 이름 → 값)에 남는다(`hand.py:466`).
  - `validate`와 `lines`가 같은 함수를 쓴다. 종목마다 대 보는 숫자 상태도 그 종목 대 보기의 numbers로 처리한다(지금은 그런 상태가 없다).
  - 미리 보기 `python -m market.research numeric <input.json>`(`research.py:380`)을 더했다. 지시서에 없던 것이고, 메인이 used에 쓸 값을 미리 보게 하려고 넣었다.
  - 틀이 `kr_overnight_sd60_pct`(국내 묶음 60봉 하루 변동성 %)를 시세로 미리 채운다(`daily.py:461`).
  - `checks._holds`는 그대로 두었다(값으로 본다).
  - `dir = "sign"`:
    - `_compare`(`checks.py:110`)가 sign을 up처럼 견주고, 글은 '부호 쪽으로 간 비율'로 낸다.
    - `research_direction`(`:310`)은 그날 `hand_reading.numbers`로 숫자를 다시 계산해 수익률에 부호를 곱한다. 부호 없는 날은 세지 않고 세부에 '부호 없음'으로 보인다.
  - audit: `_number_errors`(`checks.py:628`, `:556`에서 부름)가 이름 모양·rule·unit·답(예/아니오/모름)·read_by를 본다. sign 주장은 on = up_odds이고 첫 조건이 숫자 상태일 때만 받는다(`:615`).
  - 볼 곳 `covers`에 숫자 이름도 받는다(`research.check_places:106`). 숫자 상태 질문은 그 숫자를 주는 곳을 추천한다.
  - PROVISO 코어 validate는 모르는 칸(`number`, 주장의 `dir = "sign"`)을 막지 않았다. 코어의 dir 검사는 효과(effect)에만 걸린다. 그래서 우회는 없다.
- 씨앗 조각 셋(모두 `seed = true`, 강도 낮음, seen_until 2026-11-04, 주장 `dir = "sign"`, `when = "예"`, span 다음):

  | 조각 | 조건 상태와 숫자 | 걸리는 대상 | 근거 / 반대 근거 |
  |---|---|---|---|
  | (a) `Q_overnight_lead_v2` (`knowledge.toml:684`) | `S_q_overnight_gap` (`:216`) = `kr_overnight_pct` ÷ `kr_overnight_sd60_pct`, `abs > 1` | 국내 원화(scope home) | 근거 `F_record_overnight`(있던 것) / 반대 `F_price_gap_intraday`(`:461`, 새로 셈) |
  | (b) `Q_surprise_follow` (`:701`) | `S_q_surprise` (`:228`) = `surprise_z`, `abs > 1` — (발표 − 전망 평균) ÷ 전망 범위 반폭, + = 금리를 덜 올리는 쪽 | 주식(equity) | `F_lit_news_cycle`(`:470`, 새로)·`F_lit_policy_stock`(있던 것) |
  | (c) `Q_foreign_flow` (`:716`) | `S_q_foreign_net` (`:240`) = `kr_foreign_net_krw100m` ÷ `kr_foreign_sd20_krw100m`, `abs > 1` | 국내 원화(home) | `F_lit_intl_flows`(`:480`, 새로) |

  - (a) 옛 `Q_overnight_lead`(`:667`)는 retired + `replaced_by = ["Q_overnight_lead_v2"]`다. 옛 상태 `S_q_overnight_move`(오름/내림)는 지우지 않고 note만 달았다.
  - 새 문헌 둘은 웹 검색으로 서지를 확인했다:
    - `F_lit_news_cycle`: Andersen·Bollerslev·Diebold·Vega 2007, JIE 73(2) 251–277.
    - `F_lit_intl_flows`: Froot·O'Connell·Seasholes 2001, JFE 59(2) 151–193.
  - `F_price_gap_intraday`는 이 저장소의 맞추기 기간 시세(2026-06-30까지)로 내가 센 것이다. 스크립트: `/tmp/claude-0/-home-user/2e605ecd-81dc-5c6d-a100-11a6dc5b37a0/scratchpad/p184B/gap.py`.
  - 근거를 못 찾은 대목은 '통념'으로 적었다. (b)의 '다음 봉까지 이어진다'가 그렇다.
- 확인 방법:
  - `python -m market.logic validate` → 문제 없음.
  - `audit` → 오류 없음. 경고 2는 전과 같은 계산 조각 둘이다.
  - `check` → 새 씨앗 셋 '시험 전'.
  - 시험: `tests/test_fast_research.py` `RuleAndNumbers`·`SignDirection`·`NumericAudit`·`FastInput`, `tests/test_daily.py` `test_numeric_seed_state_is_set_by_code`.

**E6 기록 칸**
- 한 것: research `numbers`·`as_of`, hand_reading `numbers`(by_asset 안에도 들어갈 수 있음)를 OPTIONAL로 두고 모양을 검사한다. 지난 줄은 그대로다(빈 as_of는 적지 않는다). prediction 칸(`base_kind`·`fill_price`)은 팀 A 몫이라 건드리지 않았다.
- 확인 방법: `python -m market.records check` → 통과(지난 기록 전부 그대로). 흉내 내기 기록 폴더도 `records check` 통과.

**볼 곳(places.toml)**
- 한 것:
  - 머리 주석을 고쳤다(`places.toml:8`).
  - 새 곳 넷을 더했고, 모두 note에 '확인 전'을 적었다(`:693`~): `P_naver_investor_day`(투자자별 매매동향 일별), `P_yahoo_futures`(미국 지수 선물), `P_investing_k200_futures`(한국 지수 선물), `P_econoday`(전망 범위를 주는 일정표).
  - 이미 열린 곳의 covers에 숫자 이름을 더했다: `P_yahoo_ewy`·`P_newsis`(간밤 격차), `P_naver_index_integration`·`P_krx`(외국인 순매수), `P_okx_api`(자금조달률), `P_tradingeconomics`(놀람) 등.
  - 미국 현물 코인 상품 순유입표(farside)는 넣지 않았다. 조사 기록 15번이 모두 막힘(HTTP 403)이었다.
- 확인 방법: audit의 볼 곳 검사가 오류 0이다. `NumericAudit.test_places_cover_number_names`.

**지시문 4·5절과 입력 예시**
- 한 것:
  - `docs/routine-prompt.md` 4절을 다시 썼다(`:82`~):
    - 네 각도와 사용자 말.
    - 조사 에이전트 지시에 numbers·as_of 규칙과 출력 JSON.
    - 숫자 상태 안내, 종목 질문은 공시·뉴스 각도로.
    - '받은 뒤': numbers 대조, 11-05부터 필수, 확인 전 볼 곳.
    - 2차 조사: 같은 네 각도, 숫자 상태로 세우기, 20분 안, record는 08:59 전.
  - 5절:
    - 숫자 상태를 코드가 정한다는 것과 씨앗 셋, used에 코드가 정한 값.
    - 손 예측 1을 E1–E3 결정 문구대로 고쳤다(체결가 기준, 출발점 조각 은퇴).
    - info 안내를 v2로, 종목 1의 은퇴 조각 id(`Q_rate_up` → `Q_rate_up_shift`), 종목 8의 체결(시가).
  - 10절 입력 예시: research에 numbers·as_of와 각도 질문 줄, 씨앗 readings 줄 삭제, `Q_overnight_lead_v2=예`, 코인 출발점 예를 '기타'로, p_up 기준 문장(E2).
- 확인 방법: 읽기.

**문서**
- 고친 곳:
  - `records/CLAUDE.md`: research·hand_reading 줄.
  - `market/CLAUDE.md`: research·hand 표 줄, 결정과 이유에 '조사는 오늘 판단용'·'숫자 상태'·'dir = sign'.
  - `docs/design.md`: 새 절 '조사를 오늘 판단용 빠른 정보로'(`:102`)와 하루의 흐름 5번 줄(각도 이름).
  - `knowledge.toml`: 머리 주석(`:23`), 조사 상태 구역 머리, 조사 조각 구역 머리 주석(`:530`).
  - `tests/CLAUDE.md`: 새 시험 파일 줄, test_research·test_daily 줄.
- 루트 `CLAUDE.md`는 건드리지 않았다(메인 몫).

## 확인한 것 (어떻게)
환경: `export PROVISO_PATH=/home/user/proviso-nesy PYTHONIOENCODING=utf-8`, 저장소 `/home/user/sr-B`.
- `python -m market.logic validate` → "목표 6, 상태 20, 사실 26, 가설 28, 규칙 3 … 문제 없음".
- `python -m market.logic audit` → 오류 없음(종료 0). 경고 2(C_above_ma200 효과 없음, C_drop_5 재생 반박)는 시작 때와 같다.
- `python -m market.logic check` → 종료 0. Q_overnight_lead_v2·Q_surprise_follow·Q_foreign_flow는 '시험 전'이다.
- `python -m market.records check` → "기록 검사 통과".
- `python -m unittest discover -s tests -t .` → 132개 통과(시작 때 115개; 새 시험 17개(test_fast_research.py), 바꾼 시험 4개 — test_daily 하나는 바꿔 끼움).
- `python -m market.research questions 2026-11-05` → 위 E4 확인 방법대로 나왔다.
- **흉내 내기 하루 흐름(`--root`)**:
  - 임시 폴더는 `/tmp/claude-0/-home-user/2e605ecd-81dc-5c6d-a100-11a6dc5b37a0/scratchpad/p184B/sim`이다. `daily.copy_for_simulation`으로 실제 시세 파일을 복사했고, 받기는 하지 않았다.
  - `python -m market.daily prepare --root <sim> --day 2026-11-05 --at 07:40 --no-fetch`:
    - 세 대상 모두 '건너뜀'이었다. 11-04 일봉이 파일에 없기 때문이고, 낡은 시세를 막는 장치가 그대로 동작한 것이다.
    - 틀에는 `_numbers_form`·`_deep_form`이 있고, research 줄마다 numbers·as_of가 있었다.
    - 간밤 질문 두 줄에는 `kr_overnight_sd60_pct = 3.092`가 미리 차 있었다.
  - 가짜 조사 숫자로 채운 입력을 기록했다(채우는 스크립트 `…/p184B/fill_sim.py`):
    - as_of를 비운 입력으로 `record … --at 08:40` → "입력 오류 15건 — 아무것도 쓰지 않았다"(as_of 없음).
    - 채운 입력 → "run 1, account 4, research 15, hand_reading 1", 숫자 없는 답 11건은 경고.
  - 기록된 hand_reading:
    - `S_q_overnight_gap = 아니오`: 1.72 ÷ 3.092 = 0.556.
    - `S_q_foreign_net = 예`: −15298 ÷ 9000 = −1.7.
    - `S_q_surprise`는 숫자가 없어 '안 물음'.
    - `numbers`에 쓴 숫자가 남았다.
  - `python -m market.research numeric <filled.json>`이 같은 값을 미리 보였다. 흉내 기록 폴더 `records check`도 통과했다.
- `git status`: `records/`(CLAUDE.md 말고)·`reports/`·`data/prices/`에 변화가 없다.

## 확인 못 한 것
- 새 볼 곳 넷(`P_naver_investor_day`, `P_yahoo_futures`, `P_investing_k200_futures`, `P_econoday`)이 키 없이 열리는지 확인하지 못했다.
  - 이 세션의 웹 읽기(WebFetch)는 이름 풀이 실패로 막혔다(ideas.repec.org, www.rba.gov.au). 웹 검색만 됐다. URL도 내 기억이고, 네이버 투자자별 매매동향 주소는 검색으로도 확인하지 못했다.
  - 첫 매일 실행이 열어 보고 note를 고쳐야 한다.
- Richards (2005) 논문의 '가격 영향이 오래 남는가'는 검색 요약으로 확인되지 않았다. 그래서 근거로 쓰지 않고 note에만 적었다.
- 진짜 조사 에이전트가 숫자 이름을 지시대로 받아 오는지(특히 20거래일 외국인 순매수 표준편차, 전망 범위)는 실제 실행에서만 보인다.
- 팀 A 변경(체결 = 시가, 예측 기준 = 체결가, C_started_move 은퇴)과 합친 뒤의 시험·흉내 내기는 돌려 보지 못했다(그쪽 코드가 이 브랜치에 없다).

## 알려진 문제
- 간밤 격차의 잣대(`kr_overnight_sd60_pct`)를 틀에 미리 채우는 곳은 국내 묶음(069500) 시세가 있어야 한다. 없으면 빈 numbers이고, 그날 간밤 격차는 '안 물음'이 된다.
- 숫자 씨앗은 숫자가 있는 날 메인이 readings에 안 적어도 기록된다. 메인이 그 조각을 used에 쓰려면 코드가 정한 값을 알아야 한다. 그래서 `numeric` 미리 보기를 두었고, 틀린 값이면 record가 거절하면서 맞는 값을 알려 준다.
- 상태 질문의 ask 문장은 그대로 두었다(지시서 '상태 질문은 그대로'). 그래서 숫자 칸을 따로 묻지 않는 상태 질문(금리·환율·공포 지수·일정·해킹·실적)은 에이전트가 알아서 numbers를 채워야 한다. 비면 경고만 난다.
- 예전 조사 기록의 angle은 옛 이름 그대로다(기록 불변). 볼 곳 성적표의 angle 칸은 places.toml 기준이라 새 이름으로 보인다.

## 다음 작업자에게
- 메인이 팀 A 브랜치와 합칠 때 겹칠 수 있는 곳(같은 파일, 가까운 줄):
  - `market/records.py` OPTIONAL 사전: 내가 research·hand_reading 줄을 고쳤고, 바로 위가 팀 A의 prediction 줄이다. 머리 import(datetime·math·re를 더함)도 겹칠 수 있다.
  - `market/daily.py`: `record()`의 `ctx["at"]` 한 줄(`H.validate` 바로 위), `template()`, `briefing()`의 조사 질문 줄.
  - `market/checks.py`: `_compare`·`research_direction`·`_research_errors`·새 `_number_errors`, `audit()` 안 한 줄. 팀 A의 기준선·layer_vs_baselines와는 다른 함수다.
  - `docs/routine-prompt.md` 5절:
    - 손 예측 1(출발점 은퇴·체결가 기준)과 종목 8(시가 체결)을 결정 E1–E3 문구대로 내가 썼다. 팀 A 구현과 맞는지 본다.
    - 10절 p_up 문장, 4절 6번의 'record는 08:59 전'과 '늦으면 다음 거래일 시가(record가 경고)'는 팀 A의 경고 문구에 맞춘다.
  - `docs/design.md` 하루의 흐름 5번 줄(각도 이름).
  - `knowledge.toml` 머리 주석(팀 A도 E2·E3 줄을 더할 자리).
  - `tests/test_daily.py`에서 `test_seed_state_is_asked_only_when_relevant`를 `test_numeric_seed_state_is_set_by_code`로 바꿨다.
- 첫 실행(2026-11-05) 뒤 볼 것:
  - 조사 답의 numbers가 채워졌나, 경고 '숫자 없는 답'이 몇 건인가.
  - 숫자 씨앗 셋이 기록됐나(`hand_reading.numbers`).
  - 확인 전 볼 곳 넷이 열렸나.

## 메인 판단 필요
1. **체결가 기준(E2)과 숫자 씨앗의 기대**: 판단 전에 나온 소식(간밤 격차·놀람·전일 수급)은 체결가(시가)에 먼저 들어간다. 그래서 세 씨앗은 모두 '시가 뒤에도 그 쪽으로 이어진다'를 주장하는 셈이다.
   - 맞추기 기간 시세로 세어 보니 받쳐 주지 않았다. 국내 묶음은 큰 시가 격차(평소 하루 움직임의 1배 넘음) 317일 중 시가 → 종가가 격차 쪽으로 간 비율이 0.486이었다(작은 격차 0.498). 해외 지수를 따르는 국내 상장 묶음은 0.559 대 0.556이었다.
   - 결정대로 세우되 `F_price_gap_intraday`를 `against`로 달았다.
   - 메인이 정할 것: 세 씨앗을 그대로 둘지, (a)를 '되돌림' 쪽으로 바꿀지, 안 세울지. 또 손 예측이 간밤 숫자로 크게 기울지 않게 지시문 5절 1에 '0.05 안팎'을 적었는데 그대로 둘지.
2. **(a)의 범위를 주식 → 국내 원화(home)로 좁혔다**. 숫자 하나(국내 지수 야간 선물)는 해외 지수를 따르는 국내 상장 묶음의 간밤을 대신하지 못한다. 그 묶음까지 하려면 숫자 상태를 하나 더 두어야 한다.
3. **놀람의 부호** — 결정의 '그쪽'을 '금리를 덜 올리는 쪽 = +'(물가·성장·고용이 예상보다 낮거나 약하면 +)로 정했다.
   - '주식에 좋은 쪽'은 경기 국면에 따라 뒤집힌다(F_lit_news_cycle: 확장기에는 약한 지표가 주식에 좋다). 그래서 고정하려면 이렇게 정할 수밖에 없었다. 지금이 금리를 올리는 확장기라는 가정이다.
   - 잣대는 전망 범위 반폭이다(1 넘음 = 범위 밖). 범위를 주는 무료 출처는 확인 전이다.
4. **숫자 씨앗은 '숫자가 있는 날 = 관련 있는 날'**로 읽었다. 각도 질문이 매일 그 숫자를 묻기 때문에, 숫자가 있으면 메인이 readings에 안 적어도 코드가 기록한다. 그래야 '아니오' 쪽도 쌓여 양쪽 5구간이 찬다. 예전 씨앗 규칙('관련 있는 날만')과 다르다.
5. **결정에 없던 것**:
   - `number`에 `per`(잣대 숫자) 칸을 더했다. 나눗셈을 에이전트 대신 코드가 하려는 것이다.
   - 간밤 격차의 잣대는 '해외 값의 평소 흔들림'이 아니라 국내 묶음의 60봉 하루 변동성이다. prepare가 시세로 틀에 미리 채운다. 해외 값의 60일 표준편차는 에이전트가 구하기 어렵다. 맞추기 기간 셈도 같은 잣대로 했다.
   - `python -m market.research numeric <input.json>` 미리 보기를 더했다.
6. **numbers가 비면 경고만** 낸다(오류 아님). 못 찾은 숫자를 지어내게 만들지 않으려는 것이다. 경고가 매일 많으면 오류로 올릴지 정한다.
7. **as_of는 판단 시각보다 늦으면 거절**한다. `daily.record`의 ctx에 판단 시각 한 줄을 더해서다. as_of가 오래된 것(예: 사흘 전)은 막지 않는다 — 일정 질문은 확인 시각이 as_of라서다.
8. **상태 질문(ask)을 그대로 두었다.** `S_q_rate_shift_up`('최근 1주 사이 기대가 바뀌었나')·`S_q_home_currency_weak`('한 달 사이')는 상태의 정의라 사후 설명이 아니라고 봤다. 다만 '빠른 정보'라는 뜻에서 보면 이 둘은 느린 정보다. 숫자 상태로 바꿀지(예: 1주 인상 확률 변화 > 10%p를 코드가 정함)는 다음 판단이다.
9. 볼 곳 '확인 전' 넷과 코인 거래소 순입출금 출처 없음: 첫 매일 실행의 새 곳 시도로 확인하게 지시문 4절 '받은 뒤'에 적었다.
