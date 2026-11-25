# 지금 기준 결정 (자세히)

루트 [CLAUDE.md](../CLAUDE.md)는 꼭 알아야 할 것만 두고, 결정의 자세한 줄은 여기 둔다(2026-11-25 루트를 줄이며 옮김 — 글은 그대로). 원문·까닭·경위는 [decisions.md](decisions.md), 설계는 [design.md](design.md). 구조나 결정이 바뀌면 같은 작업 안에서 여기 해당 줄을 고치고 decisions.md 맨 끝 '이후 결정'에 날짜를 붙여 덧붙인다.

## 역할
- **사용자**: 의도만 준다. 시장에 관한 사실은 에이전트가 직접 확인한다.
- **채점자**: 시장 결과. 판정은 코드가 계산한다.
- **예약 실행**(아래 표): 아침 판단 메인이 조사 지시·대 보기·손 예측·주문, 피드백 정리 실행이 돌아보기·쪼개기·뿌리 묶기·주간 결산, 일지 쓰기 실행이 매일 일지. 하위 조사 에이전트는 정해진 양식으로 내고 판단은 메인이 한다.
- **로컬·대화 세션**: 코드·문서·설계·논리집 모양만 고친다. 기록(`records/`·`reports/`·`data/prices/`)은 쓰지 않는다.

## 확정된 결정 (지금 기준 — 까닭은 docs/decisions.md 같은 이름 절, 설계는 docs/design.md)
- **대상**: KODEX 200, TIGER 미국S&P500, 비트코인(업비트), 현금 + 손 계좌만 `stocks.toml`의 한국 상장 개별 종목(60개, 하나는 국내 업종 ETF `kind = "etf"`; 모두 살 수 있음, `buy = false`는 예전에 더 사지 않기로 한 것만). 원화, 빚·공매도 없음, 수수료 포함, 팔 때 세금(종목 0.20%, 해외 지수 ETF는 판 이익의 15.4%; 분배금·배당은 아직 안 넣음).
- **계좌 넷**: 손(`live:hand`), 계산기(`live:calc`, 규칙 `R_weights_v2`), 기준선 셋 나눠 사 두기·현금. 지금은 **2기** — id 끝 `@2`(`config.period_id`), 1기는 닫아 비교용으로 남김(decisions.md '아침 흐름' 9).
- **주문은 가상 증권사를 거친다**(`market/broker.py`): Claude는 목표 비중만, 받기·거절·체결·잔고는 프로그램이 실제 규칙대로. 비트코인은 판단 순간의 실제 가격, ETF·종목은 09:00 공식 시가(사기는 그때 계좌에 있는 돈만 — 같은 09:00에 팔리는 돈은 못 씀). 안 되는 주문은 줄이지 않고 통째로 거절해 기록. 규칙 숫자와 출처는 `market/rules.py`, 설계 [docs/broker-sandbox.md](docs/broker-sandbox.md) 13–19절.
- **예측**: 대상마다 1·5·20 거래일, 오를 확률과 크기 범위. 기준 = **판단 뒤 첫 시가(체결가)**. 손 예측·계산기·이전 버전(`versions/`, 활성 셋)·기준선 셋(늘 50%, 최근 250봉 비율, 알려진 전체 비율)을 나란히, 줄마다 답한 층(`layer`).
- **계산기**: 층으로 답한다(`R_layer_v0`) — 켜진 계산 조각 → 동전(50%). 방향 기본값은 동전(`C_base_coin`). 종목에는 '모름'(세 대상 시세로만 맞춤).
- **손 비중**: 세 대상 ≈ 1/3 + (p − 0.5) × 4/3, 0~0.5, 모르면 1/3. 종목은 기준 0, 목표 ≈ (1주 p − 0.5) × 2, 한국 묶음 몫에서 덜어 냄. **장치(record가 거절)**: 한 종목 0.15, 종목 8개, 종목 비중 합 0.45, 목표 > 0이면 1주 p > 0.5와 결론 조각 필수, 든 종목은 매일 손 예측 셋. 거래 문턱 2%.
- **채점**: 방향·확률 점수(Brier)·크기·수익률, 기준선 다섯과 비교. 같은 (대상·기간·기준 날짜)는 한 번만 센다. 종목은 묶음 대비 칸(`rel_ret`)도.
- **가설만 있다**: 모든 주장은 성적표가 붙은 가설('검증됨' 없음), 세운 뒤의 날들로만 시험. 조각 이름·문장에 상품·코인·종목 이름 금지(audit가 막음). 계산 조각 상한 8(지금 7), 정식 조사 조각 6, 종목 조각 3, 씨앗 조각 12.
- **틀리면 돌아본다**: 잡음 / 대 보기 틀림을 먼저 거르고, 나머지는 조건 흐림 → 쪼갬 / 이유 틀림 → 새 버전 / 바깥 상황 → 예외. 쪼개기 전에 `python -m market.breakdown`으로 나눠 보고, 문법이 못 적으면 넓은 조건으로 바꾸지 않고 잴 수 있는 상태를 세우거나 **대화 세션 몫**(`asks.toml`)으로 연다. 돌아보기 `reason`은 늘 `다음엔: <행동>`으로 끝나고, 다음 같은 상황에서 `지난 다음엔 <id> → 해 봄/안 해 봄 — …`으로 잇는다(record가 검사). '다음엔'은 **뿌리 목록**(`lessons.toml` + `records/lesson.jsonl`)에 같음·새 뿌리·충돌·고침·합침·내림으로 묶는다.
- **조사 범위도 논리집에**: 볼 곳 노드(`places.toml`)와 성적, 질문별 찾음/못 찾음. 조사 답에는 `numbers`·`as_of` 필수, 숫자 상태는 코드가 값을 정한다.
- **재생**: 코드로만 계산하는 조각은 2026-07-01~10-19 시세로 한 번 재생해 미리 시험했다(결과: 시세 모양 조각만으로는 시기 변화를 못 알아봄 — [docs/replay-2026-07-10.md](docs/replay-2026-07-10.md)). 다시 돌리지 않는다.
- **보고서**: 매일 일지 + 일요일 주간 결산. 형식·말투는 [docs/report-style.md](docs/report-style.md), 숫자는 코드가 기록에서 채운다.

## 아침 흐름과 예약 — 자세한 줄
시각·예약 표는 루트 CLAUDE.md '아침 흐름과 예약'.

- **09:00 문**(`market/flow.py`): record 시각이 09:00:00 KST 이상이면 주문·예측을 하나도 넣지 않고 '못 넣음' run 줄(`late:<날짜>`)만 남긴다(모든 대상). 같은 날 다시 못 하고 다음 날 빈 날 없이 이어 간다. 09:05 실행은 09:00 전 plan 줄만 읽으므로 문에 걸리지 않는다.
- **꼭 할 단계는 record가 막는다**(문서가 아니라 코드): 대상마다 아침 조사 파일(`scan.py`), 아침 입력 `precheck`(전날까지 정리된 뿌리 목록·돌아보기를 읽었다는 표시)·`recheck`(08:40 다시 보기, 바꾼 것은 from·to 둘 다)·`timeline`. 아침 입력에 `reviews`·`reading_checks`·`lessons`가 있으면 거절(9시 뒤 몫).
- 아침 예약 안에 박힌 짧은 지시는 옛 문장(07:37 시작 등)이지만 "파일과 다르면 파일을 따른다"라 지시문 파일이 기준이다. 지시를 바꾸려면 그 파일을 고쳐 main에 push한다.
- 블로그 토큰은 「Daily Briefing」 환경에만 있다(이 저장소 예약 환경 「stock-rader」는 네트워크 Full, 비밀값 없음). 블로그 '내 돈 그래프'(pfkfks-main, 비공개)가 main의 `records/account.jsonl`·`order.jsonl`을 직접 읽는다 — 계좌·주문 기록의 칸 모양을 바꾸면 그쪽도 고치거나 '다음 할 일'에 남긴다. 2기(`@2`)는 그쪽이 아직 안 읽어 그래프가 1기에서 멈춰 보인다(그쪽 몫).
- 일요일 11:13 「주식 고리 주간 점검」(`trig_01EF8CGaGRrbXqQkok62XWnm`, 사용자 비서 쪽 예약)이 이 저장소를 읽기만 해서 '의도대로 도나'를 사용자에게 알린다 — 이 파일의 '아침 흐름' 절과 지시문 넷을 읽는다.

## 할 수 있는 말의 한계 (사용자가 실제 종목을 물을 때)
- 가상 투자 실험이다. 답은 투자 권유가 아니다.
- 계산기는 종목에 '모름'이다. 종목에 대한 숫자는 지난 시세 비율(기준선)과 손 예측뿐이다.
- 한 회사를 가리키는 조각은 '실적이 예상보다 좋았다'(`Q_earnings_beat_rel`) 하나뿐 — 신제품·수출·소송·경영에는 할 말이 얕다.
- 기록은 2026-10-21부터 몇 주뿐이고 조각 표본은 대개 한 자릿수, 같은 날 여러 종목은 한 사건이다.
- 한국 상장(코스피·코스닥)만. 코스닥도 코스피 200 묶음에 견준다. 업종 ETF는 '한 회사' 조각이 걸리지 않고 한국 묶음과 견준다.
- 2026-11-17 전 종목 종가는 시간외를 친 값이라 그 전 채점에 하루 0–3% 차이가 섞여 있다(지금은 야후 정규장 값).
- 든 종목이 아니면 손 예측은 하루 세 종목씩 차례로(약 3주에 한 번, 물어본 종목은 다음 날 먼저).

## 구조와 명령 (저장소 루트, 외부 패키지 없음, Python 3.11+)
| 폴더·파일 | 역할 |
|---|---|
| `market/` | 시세·기록·가상 증권사(`rules.py`·`quotes.py`·`broker.py`·`rebalance.py`)·채점·조각·계산기·조사·손 예측 검사·매일 실행·흐름 문(`flow.py`)·아침 조사(`scan.py`)·장전 값·뿌리 목록·일지 뼈대 ([market/CLAUDE.md](market/CLAUDE.md)) |
| `data/prices/` | 끝난 일봉 CSV, 덧붙이기만 ([data/CLAUDE.md](data/CLAUDE.md)) |
| `records/` | 덧붙이기만 하는 기록. `records/*.jsonl` = 진짜 계좌(예약 실행만 씀), `records/replay/` = 재생 원본 ([records/CLAUDE.md](records/CLAUDE.md)) |
| `reports/` | 매일 일지·일요일 주간 결산 ([reports/CLAUDE.md](reports/CLAUDE.md)) |
| `knowledge.toml` | 논리집(PROVISO 양식): 목표·상태·근거 문헌·계산 조각·조사 조각(씨앗 포함)·규칙·가설 |
| `proviso/` | 논리집 검사 코어(`schema`·`score`·`audit`), 메인 피드백 `feedback/main/common.md`·`stock_rader.md`(briefing이 보임), 쌓는 방식 `stacking.md` — 2026-11-22 PROVISO-Nesy 08c6c93에서 옮김 ([proviso/CLAUDE.md](proviso/CLAUDE.md)) |
| `places.toml` | 볼 곳(조사 출처) 노드. 성적은 기록에서 계산 |
| `stocks.toml` | 개별 종목 목록(코드·이름·시장·더한 날·왜). 지우지 않고 덧붙인다 |
| `lessons.toml` | 뿌리 목록 씨앗(교훈 L…·충돌을 가른 조건 C…). 늘어나는 것은 `records/lesson.jsonl` |
| `asks.toml` | 대화 세션 몫: 예약 실행이 '코드·문법이 필요해 못 한다'고 연 일과 닫은 것 |
| `asked.toml` | 물어본 종목(`market.ask`가 덧붙임) — 다음 아침 손 예측 차례 맨 앞 |
| `versions/` | 이전 버전 조각 모음, 매일 같이 채점 ([versions/CLAUDE.md](versions/CLAUDE.md)) |
| `tests/` | unittest ([tests/CLAUDE.md](tests/CLAUDE.md)) |
| `.claude/agents/` | 하위 조사 에이전트 정의 `stock-rader-research.md`(도구를 Bash·Read·Write·WebSearch·WebFetch로 묶음 — 원격 도구 없음, 2026-11-22). 이 폴더의 `.md`는 모두 에이전트 정의로 읽혀 CLAUDE.md를 두지 않는다(설명은 이 줄과 decisions.md) |
| `docs/` | 설계·결정 기록·보고서 문체·재생 결과·**예약 지시문 넷**·작업 보고서(`worklog/`) |
| `out/` | 매일 실행의 작업 파일(git 제외): `out/daily/<날짜>/briefing.md`·`input.json`·`scan/` |

- **예약 실행 명령**(지시문이 차례를 정함): `python -m market.daily prepare` → `record <input.json>` → (09:05) `open` → (09:13) `feedback [<feedback.json>]` → (09:41) `report` → `report --check`. 흉내 내기는 `--root <임시 폴더> --day … --at …`(기본 저장소에서는 금지).
- 읽기 전용 보기: `python -m market.broker status`(계좌·주머니·걸린 주문·검산), `market.quotes [대상…]`(실제 지금 가격), `market.scan`(오늘 아침 조사 표), `market.premarket [코드…]`(장전 값 원문), `market.lessons`(뿌리 목록), `market.asks`(열린 대화 세션 몫), `market.breakdown <조각 id> [--horizon N]`(쪼개기 전 나눠 보기), `market.research places|questions [날짜]`, `market.calc [날짜]`, `market.replay report`, `market.whatif`(지난 결정 다시 대입, 결과는 `out/whatif/`).
- 고치는 명령: `python -m market.stocks [add <코드> <이름> "<왜>" [--kosdaq]]`, `market.versions [save <id> "<왜>"]`, `market.ask <코드|이름> [--dry]`(한 종목 상태·한계를 한 화면에, 목록에 없으면 더하고 `asked.toml`에), `market.fit [--write]`, `market.prices update|show`.
- 검사: `python -m unittest discover -s tests -t .`(30초쯤), `python -m market.records check`(기록 칸·git HEAD 비교·가상 증권사 검산, 커밋 전에), `python -m market.logic validate|check|audit`(코어는 저장소 안 `proviso/` — 형제 폴더·`PROVISO_PATH`는 보지 않는다).
- 한글이 깨지면 `PYTHONIOENCODING=utf-8`.
