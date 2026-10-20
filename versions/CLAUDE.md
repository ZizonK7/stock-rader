# versions/ — 이전 버전 조각 모음

## 역할
조각을 고치기(쪼개기) **전의** 논리집을 통째로 떠 둔 파일과 그 목록(`versions.toml`). 매일 실행이 active인 버전을 같은 날의 시세 대 보기 값으로 다시 계산해 predictor `version:<id>`로 적고 같이 채점한다(설계 "세 가지를 나란히 적는다": 손 예측 · 계산기 · 이전 버전). 가상이라 공짜로 나란히 돌릴 수 있고, "쪼갠 버전이 쪼개기 전보다 나아지나"를 같은 날들로 비교한다.

| 파일 | 무엇 |
|---|---|
| `versions.toml` | 목록: id, file, saved(떠 둔 날), digest(지문), active, why |
| `v1.toml` | 1단계 재생(2026-07-01~10-19)에 쓴 논리집 그대로(지문 3a322e89980b). 2026-10-21 C_trend_20을 쪼개기 전 |

## 결정과 이유
- 떠 두기는 `python -m market.versions save <id> "<왜>"`. 논리집 파일 전체를 복사한다(조각만 뽑지 않는다: 계산기 코드가 그대로 읽게).
- 동시에 도는 버전은 최대 3개(`versions.MAX_ACTIVE`, 설계: 직전, 지금까지 성적이 가장 좋은 것 등). 늘 도는 '지금 것'은 calc다.
- 지문은 줄바꿈을 LF로 맞춰 잰다(`calc.digest`): 윈도 체크아웃(CRLF)과 클라우드(LF)가 같은 지문을 내게. `.gitattributes`가 이 폴더의 `.toml`을 LF로 고정한다.

## 규칙
- 떠 둔 파일은 고치지 않는다(지문이 다르면 `python -m market.logic audit` 오류).
- 그만 돌릴 버전은 `active = false`로만 바꾼다. 지우지 않는다(지난 예측 기록의 `knowledge` 지문이 이 파일을 가리킨다).

## 다른 폴더와의 관계
- 읽는 코드: `market/versions.py`(목록·검사), `market/daily.py`(매일 예측), `market/checks.py`(audit).
- 원본: 루트 `knowledge.toml`.
