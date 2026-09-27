"""
Историята: какво е казал моделът за вече изиграните мачове и кое е излязло.

Два източника, които НИКОГА не се смесват в едно число:

  на живо    - таблица predictions: записано ПРЕДИ мача, с час. Единственият честен запис.
  симулация  - таблица sim_predictions: какво БИ казал моделът, обучен само върху мачовете
               преди седмицата на мача (walk-forward). Нужна е, защото записите на живо
               започват от 2026-09-14 - без нея историята за 6 месеца щеше да е празна.

Симулацията е по-мека проверка от записа на живо: цените са средните на пазара от
football-data (не тези в момента на прогнозата), а моделът се преобучава веднъж седмично,
не всеки час. Затова на сайта двете стоят поотделно и са надписани.
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import model, results
from .market import implied_row

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_predictions (
    league      TEXT NOT NULL,
    date        TEXT NOT NULL,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    fthg        INTEGER NOT NULL,
    ftag        INTEGER NOT NULL,
    p_home      REAL NOT NULL, p_draw REAL NOT NULL, p_away REAL NOT NULL,
    m_home      REAL, m_draw REAL, m_away REAL,           -- пазарът, без маржа
    odds_home   REAL, odds_draw REAL, odds_away REAL,     -- средните коефициенти
    trained_to  TEXT NOT NULL,                           -- моделът е видял мачове до тук
    computed_at TEXT NOT NULL,
    UNIQUE(league, date, home_team, away_team)
);
"""
DAYS_KEPT = 190      # малко над 6 месеца - колкото показва сайтът


def _market(conn, league, date, home, away):
    row = conn.execute(
        """SELECT o.odds_home, o.odds_draw, o.odds_away FROM odds o
             JOIN matches m ON m.id = o.match_id
            WHERE m.league = ? AND m.date = ? AND m.home_team = ? AND m.away_team = ?
              AND o.bookmaker IN ('AVG', 'B365')
            ORDER BY CASE o.bookmaker WHEN 'AVG' THEN 1 ELSE 2 END LIMIT 1""",
        (league, date, home, away)).fetchone()
    if row is None:
        return None, None
    odds = [row[0], row[1], row[2]]
    return odds, implied_row(odds)


def simulate(conn, out_conn, since, until=None, leagues=None):
    """Walk-forward по седмици: за всяка седмица моделът се обучава на мачовете ПРЕДИ нея
    и прогнозира мачовете в нея. conn - базата с история и коефициенти; out_conn - там,
    където се пишат симулираните прогнози (може да е същата)."""
    out_conn.executescript(SCHEMA)
    until = until or datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    for league in leagues or results.LEAGUES:
        history = results.history(conn, league)
        if history.empty:
            continue
        played = history[(history["date"] >= since) & (history["date"] < until)]
        if played.empty:
            continue
        done = {(r[0], r[1], r[2]) for r in out_conn.execute(
            "SELECT date, home_team, away_team FROM sim_predictions WHERE league = ?", (league,))}
        week_start = pd.Timestamp(since)
        end = pd.Timestamp(until)
        while week_start < end:
            week_end = week_start + pd.Timedelta(days=7)
            week = played[(played["date"] >= week_start) & (played["date"] < week_end)]
            todo = [r for r in week.itertuples()
                    if (r.date.date().isoformat(), r.home_team, r.away_team) not in done]
            train = history[history["date"] < week_start]
            if todo and len(train) >= model.MIN_TRAIN_MATCHES:
                fitted = model.Poisson().fit(train)
                for r in todo:
                    probs = fitted.probabilities(r.home_team, r.away_team)
                    if probs is None:
                        continue
                    day = r.date.date().isoformat()
                    odds, market = _market(conn, league, day, r.home_team, r.away_team)
                    out_conn.execute(
                        """INSERT OR IGNORE INTO sim_predictions
                           (league, date, home_team, away_team, fthg, ftag, p_home, p_draw, p_away,
                            m_home, m_draw, m_away, odds_home, odds_draw, odds_away,
                            trained_to, computed_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (league, day, r.home_team, r.away_team, int(r.fthg), int(r.ftag), *probs,
                         *(market or (None, None, None)), *(odds or (None, None, None)),
                         train["date"].max().date().isoformat(), now))
                    written += 1
            week_start = week_end
        out_conn.commit()
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=DAYS_KEPT)).isoformat()
    out_conn.execute("DELETE FROM sim_predictions WHERE date < ?", (cutoff,))
    out_conn.commit()
    log.info("Симулирани прогнози: %d нови", written)
    return written


def extend(conn):
    """За облака: добавя симулация за мачовете, изиграни след последната симулирана дата.
    Лек е - на седмица се обучава по един модел на лига, и то само за новите мачове."""
    conn.executescript(SCHEMA)
    last = conn.execute("SELECT MAX(date) FROM sim_predictions").fetchone()[0]
    since = ((datetime.fromisoformat(last) - timedelta(days=7)).date().isoformat() if last
             else (datetime.now(timezone.utc).date() - timedelta(days=DAYS_KEPT)).isoformat())
    return simulate(conn, conn, since)
