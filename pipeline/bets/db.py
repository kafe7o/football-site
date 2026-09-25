"""
Базата: четири таблици и нищо излишно.

  matches      - резултат мач по мач
  odds         - коефициенти за мач, по букмейкър
  predictions  - прогноза на модела, записана ПРЕДИ мача, с час; после резултатът
  value_bets   - залог, намерен по ЦЕНА (бавен букмейкър срещу остър), пак преди мача

Двете последни таблици са смисълът на проекта: без запис отпреди мача всяка статистика
после е нагласена. Затова са immutable - пише се веднъж, допълва се само резултатът.
"""

import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    league      TEXT NOT NULL,
    season      TEXT NOT NULL,
    date        TEXT NOT NULL,          -- ISO, напр. 2026-09-20
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    fthg        INTEGER,                -- NULL, докато мачът не е изигран
    ftag        INTEGER,
    hthg        INTEGER,
    htag        INTEGER,
    kickoff     TEXT,
    UNIQUE(league, date, home_team, away_team)
);

CREATE TABLE IF NOT EXISTS odds (
    match_id    INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    bookmaker   TEXT NOT NULL,          -- B365, AVG, MAX, PS и затварящите им варианти
    odds_home   REAL,
    odds_draw   REAL,
    odds_away   REAL,
    UNIQUE(match_id, bookmaker)
);

CREATE TABLE IF NOT EXISTS predictions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    league        TEXT NOT NULL,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    match_date    TEXT NOT NULL,        -- начало на мача, ISO с часова зона
    predicted_at  TEXT NOT NULL,        -- ЗАДЪЛЖИТЕЛНО преди match_date
    model_version TEXT NOT NULL,
    p_home        REAL NOT NULL,
    p_draw        REAL NOT NULL,
    p_away        REAL NOT NULL,
    odds_home     REAL,                 -- пазарът в момента на прогнозата
    odds_draw     REAL,
    odds_away     REAL,
    bookmaker     TEXT,
    match_id      INTEGER REFERENCES matches(id) ON DELETE SET NULL,
    outcome       INTEGER,              -- 0 домакин, 1 равен, 2 гост; NULL до уреждане
    settled_at    TEXT,
    UNIQUE(league, home_team, away_team, match_date, model_version)
);

CREATE TABLE IF NOT EXISTS value_bets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sport         TEXT NOT NULL,        -- ключ на лигата в odds API
    event_id      TEXT NOT NULL,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    commence_time TEXT NOT NULL,
    selection     TEXT NOT NULL,
    outcome_idx   INTEGER NOT NULL,
    bookmaker     TEXT NOT NULL,        -- къде е цената
    odds          REAL NOT NULL,        -- нетна цена (борсите: минус комисионата)
    sharp_book    TEXT NOT NULL,        -- еталонът
    sharp_odds    REAL NOT NULL,
    p_fair        REAL NOT NULL,
    edge          REAL NOT NULL,
    n_books       INTEGER,              -- колко букмейкъра са имали цена за мача
    found_at      TEXT NOT NULL,
    closing_odds  REAL,                 -- последната видяна цена преди мача
    closing_fair  REAL,
    match_id      INTEGER REFERENCES matches(id) ON DELETE SET NULL,
    result        INTEGER,              -- 1 спечелен, 0 загубен
    profit        REAL,
    settled_at    TEXT,
    UNIQUE(event_id, selection, bookmaker)
);

-- Честната цена за всеки предстоящ мач, каквато я вижда острият пазар. Не е залог -
-- това е числото, с което се сравнява цената на твоя букмейкър, когато него го няма в
-- никое API (efbet, winbet и другите български сайтове).
CREATE TABLE IF NOT EXISTS fair_prices (
    sport         TEXT NOT NULL,
    event_id      TEXT NOT NULL,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    commence_time TEXT NOT NULL,
    selection     TEXT NOT NULL,
    outcome_idx   INTEGER NOT NULL,
    p_fair        REAL NOT NULL,
    sharp_book    TEXT NOT NULL,
    sharp_odds    REAL NOT NULL,
    best_book     TEXT,               -- най-добрата цена, която API-то вижда
    best_odds     REAL,
    prices_json   TEXT,               -- всички цени за изхода, {букмейкър: цена}
    updated_at    TEXT NOT NULL,
    UNIQUE(event_id, selection)
);

-- Как се мени прогнозата за един и същ мач през дните. Записва се нов ред само когато
-- нещо се е променило осезаемо (над CHANGE_THRESHOLD), за да не расте безсмислено.
-- Оттук идват известията "този мач се промени" и историята в подробния изглед.
CREATE TABLE IF NOT EXISTS forecast_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    commence_time TEXT NOT NULL,
    p_model_h     REAL, p_model_d REAL, p_model_a REAL,
    p_fair_h      REAL, p_fair_d  REAL, p_fair_a  REAL,
    recorded_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_forecast_event ON forecast_log(event_id, recorded_at);
-- Служебни стойности: кога за последно облакът е теглил резултатите и т.н.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fair_time ON fair_prices(commence_time);
CREATE INDEX IF NOT EXISTS idx_matches_league_date ON matches(league, date);
CREATE INDEX IF NOT EXISTS idx_predictions_open ON predictions(outcome) WHERE outcome IS NULL;
CREATE INDEX IF NOT EXISTS idx_value_open ON value_bets(result) WHERE result IS NULL;
"""


# Колони, добавени след първото създаване на таблиците. CREATE TABLE IF NOT EXISTS не пипа
# вече съществуваща таблица, затова всяка база - на лаптопа, резервното копие, базата в
# облака - се надгражда тук при отваряне. Без това кодът и базата се разминават тихо:
# облакът падна на 2026-09-25, защото неговата база нямаше колоната n_books.
MIGRATIONS = [
    ("value_bets", "n_books", "INTEGER"),
    ("fair_prices", "prices_json", "TEXT"),   # {букмейкър: цена} за изхода
    ("matches", "hthg", "INTEGER"),
    ("matches", "htag", "INTEGER"),
    ("matches", "kickoff", "TEXT"),
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
               odds_home = excluded.odds_home,
               odds_draw = excluded.odds_draw,
               odds_away = excluded.odds_away""",
        (match_id, bookmaker, home, draw, away))


def counts(conn):
    tables = ["matches", "odds", "predictions", "value_bets", "fair_prices", "forecast_log"]
    return {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in tables}


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()
