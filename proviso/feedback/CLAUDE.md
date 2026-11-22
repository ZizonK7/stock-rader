# proviso/feedback/ — 메인 피드백

## 역할
`main/common.md`(모든 일)·`main/stock_rader.md`(이 실험)는 "이런 상황이면 이렇게 하겠다" 줄(`- **F…**`)이다. 매일 briefing의 '논리집 메인 피드백'에 나오고(`market/lessons.py` `feedback_lines`가 `- **F`로 시작하는 줄만 읽는다), 아침·피드백 정리 지시문이 판단 전에 통째로 읽게 한다.

## 결정과 이유
- 2026-11-22: PROVISO-Nesy main이 2026-11-21 0447979로 비워져, 바로 앞 커밋 08c6c93의 `feedback/main/` 두 파일을 글자 그대로 옮겼다(사용자 "1번으로 옮기자"). 원래는 그 저장소에서 관리했다(이 저장소 지시문의 옛 문장: "잼민이 쪽이 논리집 백서로 옮긴다").
- 줄 끝의 번호(F008 등)와 파일 머리의 `../whitepaper.md` 링크는 그곳 백서를 가리킨다. 백서는 옮기지 않았다(그 저장소 히스토리 08c6c93에만 있다).

## 규칙
- 예약 실행은 고치지 않는다(교훈의 원본은 이 저장소의 `lessons.toml`·`records/lesson.jsonl`이다). 줄을 더하거나 고치는 것은 대화 세션 몫이고, 고치면 decisions.md '이후 결정'에 적는다.
- briefing이 읽는 꼴(`- **F<번호>** …`)을 지킨다.

## 다른 폴더와의 관계
- 읽는 곳: `market/lessons.py`(`logic.FEEDBACK` 자리), `docs/routine-prompt.md` 1-3, `docs/routine-feedback-prompt.md`.
