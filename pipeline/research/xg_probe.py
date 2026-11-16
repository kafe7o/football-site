"""
Диагноза (16.11): защо xG от Understat не идва от началото на ноември (седмичният анализ: „ПРОБЛЕМ: xG“; в базата на робота
последният мач с xG е от 01.11). Само чете: показва какво връща Understat за двете заявки на bets/xg.fetch. Нищо не записва в базата.
Пускане в облака: Actions -> research -> xg_probe.
"""

import gzip
import http.cookiejar
import sys
import urllib.error
import urllib.request

from bets import xg


def show(opener, url, headers):
    try:
        r = opener.open(urllib.request.Request(url, headers=headers), timeout=30)
        raw = r.read()
        body = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        print(f"{url}\n  статус {r.status}, {len(body)} байта, тип {r.headers.get('Content-Type')}, сървър {r.headers.get('Server')}")
        print("  начало:", body[:300].decode("utf-8", "replace").replace("\n", " "))
        return body
    except urllib.error.HTTPError as e:
        print(f"{url}\n  HTTP {e.code} {e.reason}; сървър {e.headers.get('Server')}; начало: {e.read()[:300]!r}")
    except (urllib.error.URLError, OSError) as e:
        print(f"{url}\n  грешка: {e!r}")
    return None


def main():
    season = xg.current_season()
    head = {"User-Agent": "Mozilla/5.0 (football-forecast-evaluator)"}
    for league in ("EPL", "Bundesliga"):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        page = f"https://understat.com/league/{league}/{season}"
        html = show(opener, page, head)
        if html:
            for mark in (b"getLeagueData", b"datesData", b"JSON.parse", b"cf-chl", b"captcha"):
                print(f"  в страницата „{mark.decode()}“: {mark in html}")
        show(opener, f"https://understat.com/getLeagueData/{league}/{season}", {**head, "X-Requested-With": "XMLHttpRequest", "Referer": page})
        try:
            d = xg.fetch(league, season)
            print(f"  xg.fetch: {len(d)} мача; последният с резултат: {max((m['datetime'] for m in d if m.get('isResult')), default=None)}")
        except RuntimeError as e:
            print("  xg.fetch ГРЕШКА:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
