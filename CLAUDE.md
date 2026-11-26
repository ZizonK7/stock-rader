# stock-rader — Claude 작업 문서 (루트)
Claude가 세션 시작 때 읽는 작업 문서다. 매일 도는 예약 실행도 모두 이 파일을 읽고 시작한다. 그래서 **찾아 읽지 않아도 무조건 알아야 할 것**만 둔다 — 소개, 사용자 의도, 꼭 지킬 것, 예약, 경로. 자세한 결정은 [docs/current.md](docs/current.md), 다음 할 일은 [docs/todo.md](docs/todo.md), 사용자 말 원문과 결정의 경위는 [docs/decisions.md](docs/decisions.md). 다른 문서가 "루트 CLAUDE.md '…' 절"이라고 가리키는데 여기 없는 절은 docs/current.md나 decisions.md의 같은 이름 절이다.

## 소개
실제 돈 없이 Claude가 혼자 가상 투자(1,000만 원)를 하면서 **논리 조각화**를 시험하는 투자 에이전트다. 매일 조사하고 예측하고 사고팔고, 틀리면 왜 틀렸는지 돌아보며 피드백(뿌리 목록)을 쌓는다. 이 저장소는 **공개**이고 실행을 맡는다(예약 실행·코드·기록). 그 피드백을 이론으로 키우는 학회·설계·의도는 비공개 상위 저장소(stock-rader-lab)가 맡고, 이 저장소는 거기에 submodule로 들어 있다. 예약 실행은 이 저장소만 본다.

## 사용자 의도 (요지 — 원문과 날짜는 docs/decisions.md '사용자 의도')
판단이 갈릴 때 기준이다.
- 실제 돈은 넣지 않는다. 논리 구조를 만드는 실험이다. 조사를 매일 하고(**현재 상황이 중요**), **모든 작업이 예측이자 시험**이며, 틀리면 왜 그랬는지 **논리를 쪼갠다**. 시기가 중요한 문제도 논리 작업으로 풀리는지, **조사 범위 자체도 논리집에** 넣어 본다.
- 조사는 **오늘 판단에 쓰는 빠른 정보**(간밤 반응·일정과 예상치·수급·공시).
- 사용자의 실제 종목 질문에 답할 수 있게 **종목을 넓게** 들고, 한계가 있으면 말한다.
- 아침 흐름은 사용자가 정한 대로(아래 표): 7시대 시작, 08:40 다시 보기, **09:00 전에만 체결**, 피드백 정리·일지는 9시 뒤 따로.
- 분야·대상·숫자·문턱은 **에이전트가 정하고 까닭과 다시 볼 기준을 적는다**(사용자는 주식·코인을 모른다 — 사용자에게는 일상어로 말하고 의도만 묻는다). 사용자가 볼 것은 '의도대로 도나'와 '그래프로 벌고 있나'.
- **기록을 꼼꼼히**, 일지는 **매일**, 블로그 쓰듯 쉬운 말로(무엇을 왜 샀고 얼마 벌고 잃었나). 같은 글을 pfkfks.org 블로그에도 올린다.
- **적극적으로**: 켜진 조각 쪽으로 기울이고 개별 종목도 산다. 모르면 더 공부한 뒤 판단한다(랜덤 판단은 쓰지 않는다).
- 피드백은 "이런 문제가 있었다 → **다음엔 이렇게** 해 보겠다"의 연속. 쪼개기 전에 '어디서만 틀렸나'를 먼저 나눠 본다.
- 시뮬레이션이라 실제로 못 하는 거래는 **무조건 막는다**. 시세를 실제와 다르게·늦게 반영하지 않는다.
- 실행은 클라우드에서, 비용·시간은 상관없다. 지금 계좌는 **2기**(2026-11-17 다시 출발) — 웬만하면 다시 시작하지 않는다.

## 꼭 지킬 것
- **기록은 덧붙이기만 한다.** 지난 예측·시세·채점을 고치지 않는다. 정정은 새 기록으로. 지난 날짜로 예측을 만들지 않고, 빠뜨린 날은 빈 날로.
- **기록(`records/`·`reports/`·`data/prices/`)은 예약 실행만 쓴다.** 대화·로컬 세션은 코드·문서·논리집 모양만 고친다. 흉내 내기는 `--root <임시 폴더>`로만(기본 저장소에서 금지).
- 과거 시세는 조각을 만드는 재료로만, 채점에 쓰지 않는다.
- **공개 저장소다**: 비밀값·개인 정보·상위 저장소의 비공개 자료를 두지 않는다. 기사 본문을 옮기지 않는다(요약 + 링크).
- **09:00 문**: 판단 시각이 09:00 KST 이상이면 주문·예측을 하나도 넣지 않는다(코드가 막는다 — `market/flow.py`). 주문은 가상 증권사(`market/broker.py`)를 거치고, 안 되는 주문은 통째로 거절해 기록한다.
- **예약은 실행마다 새 세션**이다. 이어지는 것은 main에 커밋된 파일뿐이다. 지시문은 `docs/routine-*.md` 파일이 기준이다(예약 안에 박힌 짧은 지시와 다르면 파일을 따른다).
- **가설만 있다**: 모든 주장은 성적표가 붙은 가설('검증됨' 없음)이고 세운 뒤의 날들로만 시험한다. 조각 이름·문장에 상품·코인·종목 이름을 쓰지 않는다.
- 숫자에는 이유를 붙인다. 숫자만 넓히는 개정은 하지 않는다.
- 사용자가 실제 종목을 물으면: 가상 투자 실험이라 투자 권유가 아니고, 할 말에 한계가 있다(docs/current.md '할 수 있는 말의 한계').
- `records/account.jsonl`·`order.jsonl`의 칸 모양을 바꾸면 블로그 '내 돈 그래프'(pfkfks-main)가 깨진다 — 그쪽도 고치거나 docs/todo.md에 남긴다.
- 구조나 결정이 바뀌면 같은 작업 안에서 docs/current.md(또는 이 파일)의 해당 줄을 고치고, 원문·까닭은 docs/decisions.md 맨 끝 '이후 결정'에 날짜를 붙여 덧붙인다. 새 폴더를 만들면 `CLAUDE.md`를 같이 만든다. 작업 보고서는 `docs/worklog/`.
- 커밋 전에 `python -m market.records check`, 코드를 고쳤으면 `python -m unittest discover -s tests -t .`. 한글이 깨지면 `PYTHONIOENCODING=utf-8`.

## 아침 흐름과 예약 (2026-11-18 판단부터 — 까닭은 decisions.md '아침 흐름', 자세한 줄은 docs/current.md)
앞 단계가 main에 올린 줄(`live:`/`late:` → `feedback:`)을 뒤 단계가 확인하고 시작한다.

| 시각(KST) | 예약 | 모델(사용자 지정) | 지시문 | 하는 일 |
|---|---|---|---|---|
| 07:17 | 「stock-rader 매일 실행」 `trig_018Jr1V7Tubftrt7M9UrY8fC` | Opus 5.5 extra | [docs/routine-prompt.md](docs/routine-prompt.md) | prepare → 대상마다 아침 조사·조사 넷(Haiku 5.5 high) → 뿌리 목록·최근 돌아보기 읽기 → 초안 → 08:40 다시 보기(Haiku 5.5 medium 셋) + 장전 값 → **record 08:55 전** → 커밋 |
| 09:05 | 「stock-rader 장 시작 뒤 실행」 `trig_01XsbE5uzbXaNuJTwNWqG8LG` | Opus 5.5 | [docs/routine-open-prompt.md](docs/routine-open-prompt.md) | `daily open` — 09:00 전에 정한 목표의 나머지(팔린 돈으로 사기)만. 새 판단 없음 |
| 09:13 | 「stock-rader 피드백 정리」 `trig_01QGgnsr4qBoRXUsojduVM7a` | Opus 5.5 extra | [docs/routine-feedback-prompt.md](docs/routine-feedback-prompt.md) | 돌아보기 전부·쪼개기·뿌리 묶기(`daily feedback`), 일요일 주간 결산(`report --week`) |
| 09:41 | 「stock-rader 일지 쓰기」 `trig_01VNtrXZnXBTJYpw34xFUKuJ` | Opus 5.5 medium | [docs/routine-report-prompt.md](docs/routine-report-prompt.md) | 기록만 보고 매일 일지(`daily report`, `feedback:` 줄이 있어야) |
| 10:47 | 「stock-rader 블로그 발행」 `trig_013rNFcywD85A81YYJZhdBfJ` (환경 「Daily Briefing」) | Haiku 5.5 | pfkfks-main `docs/stock-rader-blog-routine.md` | `reports/<오늘>.md`(일요일엔 `-week.md`도)를 **고치지 않고** 올림 |

- 일요일 11:13 「주식 고리 주간 점검」(`trig_01EF8CGaGRrbXqQkok62XWnm`, 사용자 비서 쪽 예약)이 이 저장소를 읽기만 해서 '의도대로 도나'를 사용자에게 알린다 — 이 절, docs/current.md '아침 흐름과 예약 — 자세한 줄', 지시문 넷을 읽는다.

## 경로
- 사람용 소개: README.md
- 지금 기준 결정(역할·확정된 결정·아침 흐름 자세한 줄·할 수 있는 말의 한계·구조와 명령): docs/current.md
- 다음 할 일: docs/todo.md
- 결정의 원문·경위: docs/decisions.md · 설계: docs/design.md · 가상 증권사: docs/broker-sandbox.md · 일지 문체: docs/report-style.md · 재생 결과: docs/replay-2026-07-10.md · 작업 보고서: docs/worklog/
- 예약 지시문 넷: docs/routine-prompt.md · routine-open-prompt.md · routine-feedback-prompt.md · routine-report-prompt.md
- 코드: market/ ([market/CLAUDE.md](market/CLAUDE.md)) · 논리집 검사 코어: proviso/ · 시험: tests/
- 기록: records/(덧붙이기만) · 일지: reports/ · 시세: data/prices/
- 논리집과 목록: knowledge.toml(논리집) · lessons.toml(뿌리 목록 씨앗, 늘어나는 것은 records/lesson.jsonl) · places.toml(볼 곳) · stocks.toml(종목) · asks.toml(대화 세션 몫) · versions/(이전 버전 조각)
- 하위 조사 에이전트 정의: .claude/agents/stock-rader-research.md
- 학회(논문·심사): 비공개 상위 저장소에 둔다(이 저장소에는 두지 않는다)
- 매일 실행 명령: `python -m market.daily prepare` → `record <input.json>` → (09:05) `open` → (09:13) `feedback` → (09:41) `report` → `report --check`. 그 밖의 명령은 docs/current.md '구조와 명령'.
