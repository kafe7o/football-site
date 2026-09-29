"""
xG (очаквани голове) от Understat за 5-те големи лиги - вход за модела (2026-09-28).

legacy/xg_check.py (протоколът е в docstring-а му): моделът, обучен на половин голове и
половин xG, е по-точен от модела само на голове - Brier -0.0045 в избора (2024/25, t = -3.4)
и -0.0049 в чистата проверка (2025/26, t = -3.7). Приет по правилото. Остава малко по-неточен
от пазара, но затваря около една трета от разликата.

Understat няма официален API: страницата на лигата зарежда данните от getLeagueData. Теглим
само текущия сезон, веднъж на 11 часа - 5 заявки. Ако не стане, моделът продължава с головете
за новите мачове; нищо друго не спира.
"""

import gzip
import http.cookiejar
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import db, teams

log = logging.getLogger(__name__)

LEAGUES = {"E0": "EPL", "SP1": "La_liga", "D1": "Bundesliga", "I1": "Serie_A", "F1": "Ligue_1"}
EVERY = timedelta(hours=11)

SCHEMA = """
CREATE TABLE IF NOT EXISTS xg (
    match_id  INTEGER PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    xg_h      REAL NOT NULL,
    xg_a      REAL NOT NULL,
    source    TEXT NOT NULL
);
"""


def current_season(today=None):
    """Understat брои сезона по годината, в която започва (2026 = 2026/27)."""
    today = today or datetime.now(timezone.utc).date()
    return today.year if today.month >= 7 else today.year - 1


def fetch(league, season):
    """Мачовете на Understat за лига и сезон (списък), или RuntimeError."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    head = {"User-Agent": "Mozilla/5.0 (football-forecast-evaluator)"}
    page = f"https://understat.com/league/{league}/{season}"
    try:
        opener.open(urllib.request.Request(page, headers=head), timeout=30).read()
        raw = opener.open(urllib.request.Request(
            f"https://understat.com/getLeagueData/{league}/{season}",
            headers={**head, "X-Requested-With": "XMLHttpRequest", "Referer": page}), timeout=30).read()
        return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)["dates"]
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        raise RuntimeError(f"Understat {league} {season}: {e}") from None


def store(conn, code, matches):
    """Записва xG към мачовете от базата (дата +-1 ден и двата отбора). Връща броя."""
    conn.executescript(SCHEMA)
    done = 0
    for m in matches:
        if not m.get("isResult") or not m.get("xG"):
            continue
        day = datetime.fromisoformat(m["datetime"][:10]).date()
        fixtures = [dict(r) for r in conn.execute(
            "SELECT id, home_team, away_team FROM matches WHERE league = ? AND date BETWEEN ? AND ?",
            (code, (day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat()))]
        hit = teams.match_fixture(m["h"]["title"], m["a"]["title"], fixtures)
        if hit is None:
            continue
        conn.execute(
            """INSERT INTO xg (match_id, xg_h, xg_a, source) VALUES (?, ?, ?, 'understat')
               ON CONFLICT(match_id) DO UPDATE SET xg_h = excluded.xg_h, xg_a = excluded.xg_a""",
            (hit["id"], float(m["xG"]["h"]), float(m["xG"]["a"])))
        done += 1
    conn.commit()
    return done


def update(conn, seasons=None):
    """Текущият сезон (или дадените) за петте лиги. Грешка в една лига не спира другите."""
    conn.executescript(SCHEMA)
    total, failed = 0, []
    for code, league in LEAGUES.items():
        for season in seasons or [current_season()]:
            try:
                total += store(conn, code, fetch(league, season))
            except RuntimeError as e:
                failed.append(str(e))
    for message in failed:
        log.error("xG не се изтегли: %s", message)
    log.info("xG: %d мача записани, %d неуспешни заявки", total, len(failed))
    if failed and not total:
        raise RuntimeError("xG: нито една лига не се изтегли")
    return total


def update_if_due(conn):
    """Веднъж на 11 часа - отделно от football-data, за да не го тегли наново при грешка тук."""
    last = db.get_meta(conn, "xg_refreshed")
    now = datetime.now(timezone.utc)
    if last and now - datetime.fromisoformat(last) < EVERY:
        return 0
    try:
        done = update(conn)
    except RuntimeError as e:
        # Грешката се записва в лога; цикълът продължава - моделът ползва головете за мачовете
        # без xG. Следващ опит след 11 часа, за да не се тропа на Understat всеки час.
        log.error("%s - моделът продължава с головете", e)
        done = 0
    db.set_meta(conn, "xg_refreshed", now.isoformat(timespec="seconds"))
    return done
