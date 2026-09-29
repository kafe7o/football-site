"""
Предстоящите мачове от всички източници - един ред на мач в таблицата `fixtures`.

  - лигите, които odds API покрива: списъкът идва от /events (безплатно, не яде от квотата);
  - останалите (Англия 5, Шотландия 2-4, Румъния, неактивните в API): от разписанието на
    football-data и OpenLigaDB, които вече са в `matches` без резултат.

Имената на отборите в odds API се разминават с историята ("Manchester United" / "Man United").
Моделът знае само имената от историята, затова всяко име се превежда:
  1. ако в същия ден има мач от football-data в същата лига - сравнява се целият мач
     (teams.match_fixture, най-сигурно), и преводът се запомня в `meta` (team:<лига>:<име>);
  2. иначе по отбор: запомнен превод, псевдоним, близко изписване (teams.match, loose_match).
Непреведен отбор = мач без модел: роботът дава прогнозата на пазара за него.
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, odds_api, teams
from .leagues import LEAGUES

log = logging.getLogger(__name__)

UK = ZoneInfo("Europe/London")
HORIZON = timedelta(days=14)     # професионалистът иска прогнозите за следващия кръг предварително


def known_teams(conn, league, since_days=500):
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).date().isoformat()
    rows = conn.execute("SELECT home_team, away_team FROM matches WHERE league = ? AND date >= ?", (league, since))
    return sorted({t for r in rows for t in r})


def uk_kickoff(day, clock):
    """Датата и часът от football-data (британско време) -> UTC ISO."""
    hh, mm = (clock or "15:00").split(":")[:2]
    start = datetime.fromisoformat(day).replace(hour=int(hh), minute=int(mm), tzinfo=UK)
    return start.astimezone(timezone.utc).isoformat(timespec="minutes")


def translate(conn, league, name, known):
    remembered = db.get_meta(conn, f"team:{league}:{name}")
    if remembered and remembered in known:
        return remembered
    return teams.match(name, known) or teams.best_match(name, known) or teams.loose_match(name, known)


def remember(conn, league, src, known_name):
    if src != known_name:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (f"team:{league}:{src}", known_name))


def active_sports():
    """Кои ключове в odds API са активни сега (безплатна заявка)."""
    try:
        return {s["key"] for s in odds_api.sports() if s.get("active")}
    except RuntimeError as e:
        log.error("Списъкът на odds API не се изтегли: %s", e)
        return set()


def refresh(conn, now=None):
    now = now or datetime.now(timezone.utc)
    end = now + HORIZON
    stamp = now.isoformat(timespec="seconds")
    active = active_sports()
    from_api = set()
    added = 0
    for code, lg in LEAGUES.items():
        if not lg.sport or lg.sport not in active:
            continue
        try:
            events = odds_api.events(lg.sport)
        except RuntimeError as e:
            log.error("%s: мачовете не се изтеглиха - %s", code, e)
            continue
        from_api.add(code)
        known = known_teams(conn, code) if lg.has_history else []
        upcoming = conn.execute(
            "SELECT id, date, home_team, away_team FROM matches WHERE league = ? AND fthg IS NULL AND date BETWEEN ? AND ?",
            (code, (now - timedelta(days=1)).date().isoformat(), end.date().isoformat())).fetchall() if known else []
        for e in events:
            start = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
            if not now - timedelta(hours=3) < start <= end:
                continue
            home = away = None
            match_id = None
            if known:
                day = start.astimezone(UK).date()
                same_day = [r for r in upcoming if abs((datetime.fromisoformat(r["date"]).date() - day).days) <= 1]
                hit = teams.match_fixture(e["home_team"], e["away_team"], same_day)
                if hit is not None:
                    home, away, match_id = hit["home_team"], hit["away_team"], hit["id"]
                    remember(conn, code, e["home_team"], home)
                    remember(conn, code, e["away_team"], away)
                else:
                    home = translate(conn, code, e["home_team"], known)
                    away = translate(conn, code, e["away_team"], known)
                    if home and away and home == away:
                        home = away = None
            mapped = int(bool(home and away))
            added += upsert(conn, e["id"], code, start.isoformat(timespec="minutes"),
                            home or e["home_team"], away or e["away_team"], e["home_team"], e["away_team"],
                            mapped, "api", match_id, stamp)
        # отменени или отложени: мачът вече не е в списъка на API-то
        ids = {e["id"] for e in events}
        for row in conn.execute("SELECT id FROM fixtures WHERE league = ? AND source = 'api' AND kickoff > ?",
                                (code, now.isoformat())).fetchall():
            if row["id"] not in ids and not conn.execute("SELECT 1 FROM tips WHERE fixture_id = ?", (row["id"],)).fetchone():
                conn.execute("DELETE FROM fixtures WHERE id = ?", (row["id"],))
    # лигите без odds API: от разписанието в matches
    for row in conn.execute(
            "SELECT * FROM matches WHERE fthg IS NULL AND date BETWEEN ? AND ?",
            ((now - timedelta(days=1)).date().isoformat(), end.date().isoformat())).fetchall():
        if row["league"] in from_api or row["league"] not in LEAGUES:
            continue
        kickoff = uk_kickoff(row["date"], row["kickoff"])
        if datetime.fromisoformat(kickoff) < now - timedelta(hours=3):
            continue
        fid = f"fd:{row['league']}:{row['date']}:{row['home_team']}:{row['away_team']}"
        added += upsert(conn, fid, row["league"], kickoff, row["home_team"], row["away_team"],
                        row["home_team"], row["away_team"], 1, "fd", row["id"], stamp)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM fixtures WHERE kickoff > ?", (now.isoformat(),)).fetchone()[0]
    unmapped = conn.execute("SELECT COUNT(*) FROM fixtures f WHERE kickoff > ? AND mapped = 0", (now.isoformat(),)).fetchone()[0]
    log.info("Предстоящи мачове: %d (нови %d), от тях без превод на имената: %d", total, added, unmapped)
    return total


def upsert(conn, fid, league, kickoff, home, away, home_src, away_src, mapped, source, match_id, stamp):
    """Мач, чиято прогноза вече е записана, не се пипа - записът е окончателен."""
    if conn.execute("SELECT 1 FROM tips WHERE fixture_id = ?", (fid,)).fetchone():
        return 0
    new = conn.execute("SELECT 1 FROM fixtures WHERE id = ?", (fid,)).fetchone() is None
    conn.execute(
        """INSERT INTO fixtures (id, league, kickoff, home, away, home_src, away_src, mapped, source, match_id, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET kickoff = excluded.kickoff, home = excluded.home, away = excluded.away,
               mapped = excluded.mapped, match_id = COALESCE(excluded.match_id, match_id),
               updated_at = excluded.updated_at""",
        (fid, league, kickoff, home, away, home_src, away_src, mapped, source, match_id, stamp))
    return int(new)


def prune(conn, now=None, keep_days=3):
    """Махат се минали мачове без прогноза (напр. без цени и без модел)."""
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=keep_days)).isoformat()
    n = conn.execute("DELETE FROM fixtures WHERE kickoff < ? AND id NOT IN (SELECT fixture_id FROM tips)", (cut,)).rowcount
    conn.commit()
    return n
