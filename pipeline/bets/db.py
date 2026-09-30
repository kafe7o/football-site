"""
Базата (2026-09-29, системата по идеите на професионалиста).

  matches       резултат мач по мач; предстоящият мач е без резултат
  odds          коефициенти 1/X/2 по букмейкър (AVG, MAX, B365, PS и затварящите им)
  odds_totals   коефициенти над/под 2.5
  fixtures      предстоящите мачове от всички източници, с последните цени
  tips          прогнозата на робота, записана ПРЕДИ мача; после само резултатът
  backtest_tips същата прогноза, симулирана назад (walk-forward) - никога не се смесва с tips
  match_stats   картони, корнери, удари, фаулове на мача (22-те лиги на football-data)
  model_cache   обученият модел на лигата за деня (за да не се обучава на всеки час)
  meta          служебни стойности

`tips` е смисълът на проекта: без запис отпреди мача всяка статистика после е нагласена.
Затова е immutable - пише се веднъж, допълва се само резултатът.
"""

import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    league      TEXT NOT NULL,
    season      TEXT NOT NULL,
    date        TEXT NOT NULL,          -- ISO, напр. 2026-09-20 (британско време)
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    fthg        INTEGER,                -- NULL, докато мачът не е изигран
    ftag        INTEGER,
    hthg        INTEGER,
    htag        INTEGER,
    kickoff     TEXT,                   -- HH:MM британско време
    UNIQUE(league, date, home_team, away_team)
);

CREATE TABLE IF NOT EXISTS odds (
    match_id    INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    bookmaker   TEXT NOT NULL,
    odds_home   REAL,
    odds_draw   REAL,
    odds_away   REAL,
    UNIQUE(match_id, bookmaker)
);

CREATE TABLE IF NOT EXISTS odds_totals (
    match_id    INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    bookmaker   TEXT NOT NULL,
    line        REAL NOT NULL,
    odds_over   REAL,
    odds_under  REAL,
    UNIQUE(match_id, bookmaker, line)
);

CREATE TABLE IF NOT EXISTS fixtures (
    id          TEXT PRIMARY KEY,       -- събитието в odds API или "fd:<лига>:<дата>:<домакин>:<гост>"
    league      TEXT NOT NULL,
    kickoff     TEXT NOT NULL,          -- UTC ISO
    home        TEXT NOT NULL,          -- името от историята, ако е намерено (за модела)
    away        TEXT NOT NULL,
    home_src    TEXT,                   -- името в източника
    away_src    TEXT,
    mapped      INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL,          -- api | fd | oldb
    match_id    INTEGER,                -- редът в matches, ако го има
    prices_json TEXT,                   -- последните цени (виж prices.py)
    prices_at   TEXT,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fixtures_kickoff ON fixtures(kickoff);

CREATE TABLE IF NOT EXISTS tips (
    fixture_id  TEXT PRIMARY KEY,
    league      TEXT NOT NULL,
    kickoff     TEXT NOT NULL,
    home        TEXT NOT NULL,
    away        TEXT NOT NULL,
    locked_at   TEXT NOT NULL,          -- ЗАДЪЛЖИТЕЛНО преди kickoff
    basis       TEXT NOT NULL,          -- model | market
    probs_json  TEXT NOT NULL,          -- вероятностите на робота и на пазара
    prices_json TEXT,                   -- цените в момента на записа
    picks_json  TEXT NOT NULL,          -- изборът по пазари: 1x2, dc, ou
    tip         TEXT,                   -- главният съвет; NULL - без съвет
    tip_odds    REAL,                   -- средната цена за съвета
    tip_best    REAL,                   -- най-добрата цена за съвета
    flags_json  TEXT,                   -- дерби, след пауза...
    hg          INTEGER,
    ag          INTEGER,
    settled_at  TEXT,
    result_src  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tips_kickoff ON tips(kickoff);

CREATE TABLE IF NOT EXISTS backtest_tips (
    league      TEXT NOT NULL,
    date        TEXT NOT NULL,
    home        TEXT NOT NULL,
    away        TEXT NOT NULL,
    season      TEXT,
    basis       TEXT NOT NULL,
    probs_json  TEXT NOT NULL,
    prices_json TEXT,
    picks_json  TEXT NOT NULL,
    tip         TEXT,
    tip_odds    REAL,
    flags_json  TEXT,
    hg          INTEGER NOT NULL,
    ag          INTEGER NOT NULL,
    UNIQUE(league, date, home, away)
);

-- Статистиката на мача (съветът на професионалиста от 2026-09-30: анализ и на картоните и
-- корнерите). Само 22-те лиги на football-data я имат; съдията - само Англия и Шотландия.
CREATE TABLE IF NOT EXISTS match_stats (
    match_id    INTEGER PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    hs INTEGER, as_ INTEGER,        -- удари
    hst INTEGER, ast INTEGER,       -- удари в целта
    hf INTEGER, af INTEGER,         -- фаулове
    hc INTEGER, ac INTEGER,         -- корнери
    hy INTEGER, ay INTEGER,         -- жълти картони
    hr INTEGER, ar INTEGER          -- червени картони
);

CREATE TABLE IF NOT EXISTS model_cache (
    league      TEXT PRIMARY KEY,
    day         TEXT NOT NULL,
    params_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_league_date ON matches(league, date);
"""

MIGRATIONS = [
    ("matches", "hthg", "INTEGER"),
    ("matches", "htag", "INTEGER"),
    ("matches", "kickoff", "TEXT"),
    ("matches", "referee", "TEXT"),     # съдията (Англия и Шотландия - и за предстоящите мачове)
    ("tips", "match_id", "INTEGER"),    # редът в matches, когато резултатът дойде от историята
]


def migrate(conn):
    added = []
    for table, column, kind in MIGRATIONS:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if existing and column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            added.append(f"{table}.{column}")
    if added:
        conn.commit()
    return added


def connect(path=None):
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(path=None):
    conn = connect(path)
    conn.executescript(SCHEMA)
    from .xg import SCHEMA as XG_SCHEMA      # таблицата с xG (bets/xg.py)
    conn.executescript(XG_SCHEMA)
    migrate(conn)
    conn.commit()
    return conn


def upsert_match(conn, league, season, date, home, away, fthg=None, ftag=None,
                 hthg=None, htag=None, kickoff=None):
    """Записва или допълва мач. Резултатът може да дойде по-късно от разписанието."""
    conn.execute(
        """INSERT INTO matches (league, season, date, home_team, away_team, fthg, ftag,
                                hthg, htag, kickoff)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(league, date, home_team, away_team) DO UPDATE SET
               fthg = COALESCE(excluded.fthg, fthg),
               ftag = COALESCE(excluded.ftag, ftag),
               hthg = COALESCE(excluded.hthg, hthg),
               htag = COALESCE(excluded.htag, htag),
               kickoff = COALESCE(excluded.kickoff, kickoff)""",
        (league, season, date, home, away, fthg, ftag, hthg, htag, kickoff))
    row = conn.execute(
        "SELECT id FROM matches WHERE league=? AND date=? AND home_team=? AND away_team=?",
        (league, date, home, away)).fetchone()
    return row["id"]


def upsert_odds(conn, match_id, bookmaker, home, draw, away):
    conn.execute(
        """INSERT INTO odds (match_id, bookmaker, odds_home, odds_draw, odds_away)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(match_id, bookmaker) DO UPDATE SET
               odds_home = excluded.odds_home, odds_draw = excluded.odds_draw,
               odds_away = excluded.odds_away""",
        (match_id, bookmaker, home, draw, away))


def upsert_totals(conn, match_id, bookmaker, line, over, under):
    conn.execute(
        """INSERT INTO odds_totals (match_id, bookmaker, line, odds_over, odds_under)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(match_id, bookmaker, line) DO UPDATE SET
               odds_over = excluded.odds_over, odds_under = excluded.odds_under""",
        (match_id, bookmaker, line, over, under))


STATS = ["hs", "as_", "hst", "ast", "hf", "af", "hc", "ac", "hy", "ay", "hr", "ar"]


def upsert_stats(conn, match_id, values):
    """values: {колона: число} - записват се само наличните, другите остават."""
    cols = [c for c in STATS if values.get(c) is not None]
    if not cols:
        return
    conn.execute(
        f"""INSERT INTO match_stats (match_id, {", ".join(cols)}) VALUES (?, {", ".join("?" * len(cols))})
            ON CONFLICT(match_id) DO UPDATE SET {", ".join(f"{c} = excluded.{c}" for c in cols)}""",
        (match_id, *(values[c] for c in cols)))


def counts(conn):
    tables = ["matches", "odds", "odds_totals", "fixtures", "tips", "backtest_tips"]
    return {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in tables}


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()
