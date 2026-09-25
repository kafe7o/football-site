"""
football-data.co.uk - резултати, коефициенти и разписание. Безплатно, без ключ.

Два файла на сезон и лига:
  mmz4281/<сезон>/<лига>.csv   - изиграните мачове с резултат и коефициенти
  fixtures.csv                 - предстоящите мачове за следващите дни, с коефициенти

Историята и разписанието влизат в една и съща таблица `matches`: разписанието без
резултат, резултатът се допълва, когато мачът се изиграе. Така прогнозата и уреждането
гледат едно място.
"""

import io
import logging
import urllib.error
import urllib.request
from datetime import datetime

import pandas as pd

from . import db

log = logging.getLogger(__name__)

BASE = "https://www.football-data.co.uk"
FIXTURES_URL = f"{BASE}/fixtures.csv"

LEAGUES = {
    "E0": "Англия · Premier League", "E1": "Англия · Championship",
    "E2": "Англия · League One", "E3": "Англия · League Two",
    "SP1": "Испания · La Liga", "SP2": "Испания · Segunda",
    "I1": "Италия · Serie A", "I2": "Италия · Serie B",
    "D1": "Германия · Bundesliga", "D2": "Германия · 2. Bundesliga",
    "F1": "Франция · Ligue 1", "F2": "Франция · Ligue 2",
    "N1": "Нидерландия · Eredivisie", "B1": "Белгия · Pro League",
    "P1": "Португалия · Primeira Liga", "T1": "Турция · Süper Lig",
    "G1": "Гърция · Super League", "SC0": "Шотландия · Premiership",
}

# Колоните с коефициенти: в CSV-то -> име на "букмейкър" в базата.
ODDS_COLUMNS = {
    "B365": ("B365H", "B365D", "B365A"), "B365C": ("B365CH", "B365CD", "B365CA"),
    "PS": ("PSH", "PSD", "PSA"), "PSC": ("PSCH", "PSCD", "PSCA"),
    "MAX": ("MaxH", "MaxD", "MaxA"), "MAXC": ("MaxCH", "MaxCD", "MaxCA"),
    "AVG": ("AvgH", "AvgD", "AvgA"), "AVGC": ("AvgCH", "AvgCD", "AvgCA"),
}


def season_code(year):
    """2026 -> '2627' (сезон 2026/27, както го пише football-data)."""
    return f"{year % 100:02d}{(year + 1) % 100:02d}"


def season_name(code):
    return f"20{code[:2]}/20{code[2:]}"


def download(url):
    try:
        with urllib.request.urlopen(url, timeout=40) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url}: сървърът върна {e.code}") from None
    except OSError as e:
        raise RuntimeError(f"{url}: няма връзка ({e})") from None


def read_csv(raw):
    """CSV-тата на football-data са ту с UTF-8 BOM, ту в latin-1 (имена като Süper Lig).
    Прочетени с грешната кодировка, първата колона става 'ï»¿Div' и всичко се пропуска тихо.
    """
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding=encoding, on_bad_lines="skip")
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
        df.columns = [str(c).strip().lstrip("﻿") for c in df.columns]
        if "Div" in df.columns:
            return df
    raise RuntimeError("CSV-то не се разчете нито като UTF-8, нито като latin-1")


def parse_date(value, dayfirst=True):
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def store_rows(conn, df, season, is_fixture=False):
    """Общата логика за история и разписание - едни и същи колони, различен източник."""
    stored, with_odds = 0, 0
    for _, row in df.iterrows():
        league = str(row.get("Div", "")).strip()
        if league not in LEAGUES:
            continue
        day = parse_date(row.get("Date"))
        if day is None:
            log.warning("%s: непозната дата %r", league, row.get("Date"))
            continue
        home, away = str(row.get("HomeTeam", "")).strip(), str(row.get("AwayTeam", "")).strip()
        if not home or not away or home == "nan":
            continue
        goals = {k: (int(row[k]) if k in row and pd.notna(row.get(k)) else None)
                 for k in ("FTHG", "FTAG", "HTHG", "HTAG")}
        kickoff = str(row.get("Time", "")).strip() or None
        match_id = db.upsert_match(conn, league, season, day.isoformat(), home, away,
                                   goals["FTHG"], goals["FTAG"], goals["HTHG"], goals["HTAG"],
                                   kickoff)
        stored += 1
        for book, cols in ODDS_COLUMNS.items():
            values = [row.get(c) for c in cols]
            if all(pd.notna(v) for v in values):
                db.upsert_odds(conn, match_id, book, *(float(v) for v in values))
                with_odds += 1
    conn.commit()
    return stored, with_odds


def update_history(conn, years_back=2, leagues=None):
    """Тегли последните сезони за всяка лига. Старите сезони не се променят."""
    this_year = datetime.now().year - (1 if datetime.now().month < 7 else 0)
    total, failed = 0, []
    for league in (leagues or LEAGUES):
        for offset in range(years_back):
            code = season_code(this_year - offset)
            url = f"{BASE}/mmz4281/{code}/{league}.csv"
            try:
                raw = download(url)
            except RuntimeError as e:
                failed.append(f"{league} {season_name(code)}: {e}")
                continue
            df = read_csv(raw)
            stored, _ = store_rows(conn, df, season_name(code))
            total += stored
            log.info("%s %s: %d мача", league, season_name(code), stored)
    for message in failed:
        log.error("не се изтегли - %s", message)
    if total == 0:
        raise RuntimeError("Нищо не се изтегли. Провери интернет и кодовете на лигите.")
    return total


def update_fixtures(conn):
    """Предстоящите мачове (обикновено следващите 7-10 дни), с коефициенти."""
    df = read_csv(download(FIXTURES_URL))
    this_year = datetime.now().year - (1 if datetime.now().month < 7 else 0)
    stored, _ = store_rows(conn, df, season_name(season_code(this_year)), is_fixture=True)
    log.info("Разписание: %d предстоящи мача", stored)
    return stored


def history(conn, league):
    """Изиграните мачове на лигата, подредени по дата - входът за модела."""
    rows = conn.execute(
        """SELECT date, home_team, away_team, fthg, ftag FROM matches
            WHERE league = ? AND fthg IS NOT NULL ORDER BY date""", (league,)).fetchall()
    df = pd.DataFrame(rows, columns=["date", "home_team", "away_team", "fthg", "ftag"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def upcoming(conn, league, day):
    """Мачовете на лигата за даден ден, които още нямат резултат."""
    return conn.execute(
        """SELECT id, home_team, away_team, date, kickoff FROM matches
            WHERE league = ? AND date = ? AND fthg IS NULL ORDER BY kickoff""",
        (league, day)).fetchall()


# Облакът пази само последните три пълни сезона + текущия. Измерено на 2026-09-25 в 8 лиги:
# моделът, обучен от 2023-07-01, се различава от обучения върху цялата история с най-много
# 0.66 процентни пункта (средно 0.1-0.2) - под точността, с която се показват процентите.
# Два сезона вече дават до 2.07 пп - твърде много. Причината е затихването: мач отпреди три
# години тежи 1.5%.
WINDOW_SEASONS = 3


def history_window_start(today=None):
    today = today or datetime.now()
    season_start = today.year - (1 if today.month < 7 else 0)
    return f"{season_start - WINDOW_SEASONS}-07-01"


def prune_history(conn, since=None):
    """Маха изиграните мачове преди прозореца - за да остане базата на облака малка.
    Мачове с прогноза или залог по цена не се махат: те са записът."""
    since = since or history_window_start()
    removed = conn.execute(
        """DELETE FROM matches WHERE date < ? AND fthg IS NOT NULL
             AND id NOT IN (SELECT match_id FROM predictions WHERE match_id IS NOT NULL)""",
        (since,)).rowcount
    conn.commit()
    if removed:
        log.info("Махнати %d мача преди %s", removed, since)
    return removed
