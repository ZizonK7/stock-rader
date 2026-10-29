"""종목 목록(stocks.toml): 손 계좌가 세 대상 말고 살 수 있는 한국 상장 개별 종목 (2026-10-29 사용자 "개별 종목을 사게 만들고 싶어").

사용법 (저장소 루트):
  python -m market.stocks                              목록과 시세 파일 상태
  python -m market.stocks add <코드> <이름> "<왜>" [--kosdaq] [--root <폴더>]
                                                       목록에 더한다(매일 실행 메인이 새 종목을 살 때). 시세는
                                                       prepare가 받는다(오늘 쓰려면 prepare를 한 번 더)

규칙
- 목록에서 지우지 않는다(기록이 그 코드를 가리킨다). 더 사지 않으려면 `buy = false`와 note.
- 이름은 사람용이다. 조각 id·문장에는 종목 이름·코드를 쓰지 않는다(audit가 막는다).
- 시세는 ETF와 같은 네이버 차트에서 받는다. 처음 쓰는 종목은 prepare가 최근 BACKFILL_ROWS줄을 받아 둔다
  (평소 움직임·기저율·시세 상태 계산에 충분한 만큼; 계산 조각 숫자는 종목에 맞추지 않는다).
- 받은 시세의 종목 이름이 목록 이름과 다르면 저장하지 않는다(코드를 잘못 적은 것을 막는다).
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import tomllib
from pathlib import Path

from . import config as C

BACKFILL_ROWS = 600   # 약 2년 반: 200일 평균(S_above_ma200), 기저율(250봉 + 20), 평소 움직임(60봉)에 충분
NEED = ("code", "name", "market", "added", "why")


def load(path: Path | None = None) -> list[dict]:
    path = path or C.STOCKS
    if not path.exists():
        return []
    return tomllib.loads(path.read_text(encoding="utf-8")).get("stock", [])


def register(path: Path | None = None) -> list[dict]:
    """목록을 읽어 config에 이름·시장을 알린다(보고서·briefing에 이름이 나오게). 목록을 돌려준다."""
    items = load(path)
    for s in items:
        code = str(s.get("code", ""))
        if C.is_stock(code):
            C.STOCK_NAMES[code] = str(s.get("name", code))
            C.STOCK_SUFFIX[code] = ".KQ" if s.get("market") == "KOSDAQ" else ".KS"
    return items


def codes(path: Path | None = None, buyable_only: bool = False) -> list[str]:
    return [str(s["code"]) for s in load(path) if C.is_stock(str(s.get("code", "")))
            and (not buyable_only or s.get("buy", True))]


def check(items: list[dict]) -> list[str]:
    errs, seen = [], set()
    for s in items:
        code = str(s.get("code", ""))
        if not C.is_stock(code):
            errs.append(f"[종목] 코드 '{code}'는 종목 코드 꼴(6자리 숫자·대문자, 세 대상 아님)이 아님")
        if code in seen:
            errs.append(f"[종목] 코드 중복 {code}")
        seen.add(code)
        for k in NEED:
            if not s.get(k):
                errs.append(f"[종목] {code}: '{k}' 칸이 없음")
        if s.get("market") and s["market"] not in ("KOSPI", "KOSDAQ"):
            errs.append(f"[종목] {code}: market은 KOSPI/KOSDAQ")
        if "buy" in s and not isinstance(s["buy"], bool):
            errs.append(f"[종목] {code}: buy는 true/false")
    return errs


def name_words(path: Path | None = None) -> tuple[str, ...]:
    """조각 문장에 나오면 안 되는 낱말(종목 이름·코드)."""
    out = []
    for s in load(path):
        out += [str(s.get("code", "")), str(s.get("name", ""))]
    return tuple(w for w in out if len(w) >= 2)


# ---------- 시세 ----------
NAME = re.compile(r'<chartdata[^>]*\sname="([^"]*)"')


def chart_name(text: str) -> str | None:
    m = NAME.search(text)
    return m.group(1) if m else None


def _same(a: str, b: str) -> bool:
    norm = lambda x: re.sub(r"[\s()·.]", "", x).upper()
    return norm(a) == norm(b)


def fetch(code: str, count: int, name: str | None = None):
    """네이버 차트에서 종목 일봉을 받는다(실패하면 야후). 목록 이름과 받은 이름이 다르면 ValueError."""
    from . import prices as PR
    try:
        raw = PR._get(f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0")
        text = raw.decode("euc-kr", errors="replace")
        got = chart_name(text)
        if name and got and not _same(got, name):
            raise ValueError(f"[{code}] 받은 종목 이름 '{got}'이 목록 이름 '{name}'과 다름 — 코드를 확인한다(저장하지 않음)")
        rows = PR.parse_naver(text, C.now_kst().isoformat(timespec="seconds"))
        if not rows:
            raise ValueError(f"[{code}] 네이버 차트에 일봉이 없음")
        return rows
    except ValueError:
        raise
    except Exception as exc:
        print(f"[{code}] 네이버 실패({exc}) → 야후로", file=sys.stderr)
        start = dt.date.today() - dt.timedelta(days=int(count * 1.6))
        return PR.fetch_yahoo(code, start, C.ASSETS[code].yahoo_suffix)


def update(code: str, folder: Path, now: dt.datetime, name: str | None = None) -> tuple[int, list[str], bool]:
    """시세 파일이 있으면 새 끝난 일봉만 덧붙이고, 없으면 최근 BACKFILL_ROWS줄을 받아 만든다. (덧붙인 수, 경고, 처음 받음)."""
    from . import prices as PR
    if PR.load(code, folder):
        n, warns = PR.update(code, folder, now=now, rows=fetch(code, 40, name))
        return n, warns, False
    rows = PR.finished(code, fetch(code, BACKFILL_ROWS, name), now)
    return PR.append(code, rows, folder), [], True


# ---------- 더하기 ----------
def add(path: Path, code: str, name: str, why: str, market: str = "KOSPI", day: dt.date | None = None) -> str:
    """목록 끝에 종목을 더한다(이미 있으면 거절). 지난 항목은 고치지 않는다."""
    code = code.strip().upper()
    if not C.is_stock(code):
        raise SystemExit(f"'{code}'는 종목 코드 꼴이 아님(6자리, 세 대상 아님)")
    if code in codes(path):
        raise SystemExit(f"{code}는 이미 목록에 있다")
    if not why.strip():
        raise SystemExit("왜 더하는지(why)를 적는다")
    day = day or C.now_kst().date()
    esc = lambda x: x.replace("\\", "\\\\").replace('"', '\\"')
    block = (f'\n[[stock]]\ncode = "{code}"\nname = "{esc(name)}"\nmarket = "{market}"\nadded = "{day.isoformat()}"\n'
             f'why = "{esc(why)}"\n')
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(old.rstrip("\n") + "\n" + block, encoding="utf-8")
    errs = check(load(path))
    if errs:
        path.write_text(old, encoding="utf-8")
        raise SystemExit("목록 오류 — 되돌림:\n" + "\n".join(errs))
    return code


def main(argv: list[str]) -> int:
    root = C.ROOT
    if "--root" in argv:
        i = argv.index("--root")
        root = Path(argv[i + 1]).resolve()
        argv = argv[:i] + argv[i + 2:]
    path = root / "stocks.toml"
    folder = root / "data" / "prices"
    if not argv:
        from . import prices as PR
        items = load(path)
        for s in items:
            rows = PR.load(str(s["code"]), folder)
            print(f"{s['code']} {s['name']} ({s.get('market')}, 더한 날 {s.get('added')}{', 더 사지 않음' if s.get('buy') is False else ''})"
                  f" — 시세 {len(rows)}줄" + (f", 마지막 {rows[-1].date}" if rows else " (다음 prepare가 받는다)"))
        errs = check(items)
        for e in errs:
            print(e)
        return 1 if errs else 0
    if argv[0] == "add" and len(argv) >= 4:
        market = "KOSDAQ" if "--kosdaq" in argv else "KOSPI"
        code = add(path, argv[1], argv[2], argv[3], market)
        print(f"더함: {code} {argv[2]} ({market}) → {path}")
        print("시세는 prepare가 받는다. 오늘 바로 쓰려면 python -m market.daily prepare를 한 번 더 돌린다"
              "(input.json은 덮지 않는다 — 새 종목의 손 예측·비중 줄은 직접 더한다).")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
