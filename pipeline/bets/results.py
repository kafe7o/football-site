"""
Резултати, коефициенти и разписание - безплатни източници, без ключ.

  football-data.co.uk/mmz4281/<сезон>/<лига>.csv   22 лиги: резултат, 1/X/2 и над/под 2.5
  football-data.co.uk/new/<държава>.csv            16 държави: цялата история в един файл,
                                                   само затварящи коефициенти 1/X/2
  football-data.co.uk/fixtures.csv                 предстоящите мачове на 22-те лиги
  football-data.co.uk/new_league_fixtures.csv      предстоящите мачове на 16-те държави
  api.openligadb.de                                3. Бундеслига: целият сезон, и бъдещите мачове

Всичко влиза в таблицата `matches`: предстоящият мач - без резултат, резултатът се допълва,
когато мачът се изиграе.
"""

import io
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from . import db
from .leagues import FD, FDNEW, LEAGUES

log = logging.getLogger(__name__)

BASE = "https://www.football-data.co.uk"
FIXTURES_URL = f"{BASE}/fixtures.csv"
NEW_FIXTURES_URL = f"{BASE}/new_league_fixtures.csv"
OLDB = "https://api.openligadb.de"

# Колоните с коефициенти: в CSV-то -> име на "букмейкър" в базата.
ODDS_COLUMNS = {
    "B365": ("B365H", "B365D", "B365A"), "B365C": ("B365CH", "B365CD", "B365CA"),
    "PS": ("PSH", "PSD", "PSA"), "PSC": ("PSCH", "PSCD", "PSCA"),
    "MAX": ("MaxH", "MaxD", "MaxA"), "MAXC": ("MaxCH", "MaxCD", "MaxCA"),
    "AVG": ("AvgH", "AvgD", "AvgA"), "AVGC": ("AvgCH", "AvgCD", "AvgCA"),
}
TOTALS_COLUMNS = {
    "B365": ("B365>2.5", "B365<2.5"), "B365C": ("B365C>2.5", "B365C<2.5"),
    "PS": ("P>2.5", "P<2.5"), "PSC": ("PC>2.5", "PC<2.5"),
    "MAX": ("Max>2.5", "Max<2.5"), "MAXC": ("MaxC>2.5", "MaxC<2.5"),
    "AVG": ("Avg>2.5", "Avg<2.5"), "AVGC": ("AvgC>2.5", "AvgC<2.5"),
}
# Файловете на /new/ са с други имена на колоните
NEW_COLUMNS = {"Home": "HomeTeam", "Away": "AwayTeam", "HG": "FTHG", "AG": "FTAG"}
NEW_COUNTRY = {"Argentina": "ARG", "Austria": "AUT", "Brazil": "BRA", "China": "CHN",
               "Denmark": "DNK", "Finland": "FIN", "Ireland": "IRL", "Japan": "JPN",
               "Mexico": "MEX", "Norway": "NOR", "Poland": "POL", "Romania": "ROU",
               "Russia": "RUS", "Sweden": "SWE", "Switzerland": "SWZ", "USA": "USA"}


def season_code(year):
    """2026 -> '2627' (сезон 2026/27, както го пише football-data)."""
    return f"{year % 100:02d}{(year + 1) % 100:02d}"


def season_name(code):
    return f"20{code[:2]}/20{code[2:]}"


def this_season_year(today=None):
    today = today or datetime.now()
    return today.year - (1 if today.month < 7 else 0)


def download(url, timeout=40):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url}: сървърът върна {e.code}") from None
    except OSError as e:
        raise RuntimeError(f"{url}: няма връзка ({e})") from None


def read_csv(raw, sep=","):
    """CSV-тата на football-data са ту с UTF-8 BOM, ту в latin-1 (имена като Süper Lig).
    Декодира се ред по ред: един байт latin-1 някъде във файла иначе обръщаше ЦЕЛИЯ файл в
    latin-1 и всяко UTF-8 име ставаше боклук ('PreuÃen MÃ¼nster') - отборът се оказваше два."""
    lines = []
    for line in raw.splitlines():
        try:
            lines.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            lines.append(line.decode("latin-1"))
    text = "\n".join(lines).lstrip("﻿")
    df = pd.read_csv(io.StringIO(text), sep=sep, on_bad_lines="skip", dtype=str)
    df.columns = [str(c).strip().lstrip("﻿") for c in df.columns]
    df = df.rename(columns=NEW_COLUMNS)
    for col in ("Div", "Country", "League", "Season", "HomeTeam", "AwayTeam"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    if "Div" not in df.columns and "Country" not in df.columns:
        raise RuntimeError("CSV-то няма колона Div/Country - форматът на football-data се е сменил")
    return df


def unmangle(name):
    """'PreuÃen MÃ¼nster' -> 'Preußen Münster': UTF-8, прочетен като latin-1."""
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def repair_names(conn):
    """Слива мачовете, записани под счупено име, с верните им двойници."""
    bad = conn.execute(
        """SELECT * FROM matches WHERE home_team LIKE '%Ã%' OR away_team LIKE '%Ã%'
                                     OR home_team LIKE '%Â%' OR away_team LIKE '%Â%'""").fetchall()
    merged = renamed = 0
    for row in bad:
        home, away = unmangle(row["home_team"]), unmangle(row["away_team"])
        if (home, away) == (row["home_team"], row["away_team"]):
            continue
        good = conn.execute(
            "SELECT id, fthg FROM matches WHERE league = ? AND date = ? AND home_team = ? AND away_team = ?",
            (row["league"], row["date"], home, away)).fetchone()
        if good is None:
            conn.execute("UPDATE matches SET home_team = ?, away_team = ? WHERE id = ?", (home, away, row["id"]))
            renamed += 1
            continue
        if good["fthg"] is None and row["fthg"] is not None:
            conn.execute("UPDATE matches SET fthg=?, ftag=?, hthg=?, htag=? WHERE id=?",
                         (row["fthg"], row["ftag"], row["hthg"], row["htag"], good["id"]))
        for table in ("odds", "odds_totals"):
            conn.execute(f"UPDATE OR IGNORE {table} SET match_id = ? WHERE match_id = ?", (good["id"], row["id"]))
            conn.execute(f"DELETE FROM {table} WHERE match_id = ?", (row["id"],))
        conn.execute("DELETE FROM matches WHERE id = ?", (row["id"],))
        merged += 1
    conn.commit()
    if merged or renamed:
        log.warning("Счупени имена на отбори: %d мача слети с верните, %d поправени", merged, renamed)
    return merged + renamed


def parse_date(value):
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _num(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x else None       # NaN -> None


def store_rows(conn, df, season=None, league=None):
    """Общата логика за история и разписание. league - кодът, ако файлът е на една държава."""
    stored = 0
    for _, row in df.iterrows():
        code = league or str(row.get("Div", "")).strip()
        if code not in LEAGUES:
            continue
        day = parse_date(row.get("Date"))
        if day is None:
            log.warning("%s: непозната дата %r", code, row.get("Date"))
            continue
        home, away = str(row.get("HomeTeam", "")).strip(), str(row.get("AwayTeam", "")).strip()
        if not home or not away or home == "nan" or away == "nan":
            continue
        goals = {k: (int(float(row[k])) if k in row and _num(row.get(k)) is not None else None)
                 for k in ("FTHG", "FTAG", "HTHG", "HTAG")}
        kickoff = str(row.get("Time", "")).strip()
        kickoff = kickoff if ":" in kickoff else None
        this_season = season or str(row.get("Season", "")).strip() or "?"
        match_id = db.upsert_match(conn, code, this_season, day.isoformat(), home, away,
                                   goals["FTHG"], goals["FTAG"], goals["HTHG"], goals["HTAG"], kickoff)
        stored += 1
        for book, cols in ODDS_COLUMNS.items():
            values = [_num(row.get(c)) for c in cols]
            if all(v is not None for v in values):
                db.upsert_odds(conn, match_id, book, *values)
        for book, cols in TOTALS_COLUMNS.items():
            values = [_num(row.get(c)) for c in cols]
            if all(v is not None for v in values):
                db.upsert_totals(conn, match_id, book, 2.5, *values)
    conn.commit()
    return stored


def update_history(conn, years_back=2, leagues=None):
    """22-те лиги на football-data: последните сезони. Старите сезони не се променят."""
    year = this_season_year()
    total, failed = 0, []
    for code in (leagues or FD):
        if LEAGUES[code].source != "fd":
            continue
        for offset in range(years_back):
            sc = season_code(year - offset)
            try:
                raw = download(f"{BASE}/mmz4281/{sc}/{code}.csv")
            except RuntimeError as e:
                failed.append(f"{code} {season_name(sc)}: {e}")
                continue
            stored = store_rows(conn, read_csv(raw), season_name(sc))
            total += stored
            log.info("%s %s: %d мача", code, season_name(sc), stored)
    repair_names(conn)
    for message in failed:
        log.error("не се изтегли - %s", message)
    if total == 0:
        raise RuntimeError("Нищо не се изтегли от football-data. Провери интернет.")
    return total


def update_new(conn, leagues=None, since=None):
    """16-те държави на football-data/new. Един файл с цялата история на държава.
    since - само мачовете от тази дата нататък (облакът пази прозорец)."""
    total, failed = 0, []
    for code in (leagues or FDNEW):
        lg = LEAGUES[code]
        try:
            df = read_csv(download(f"{BASE}/new/{lg.fdnew}.csv"))
        except RuntimeError as e:
            failed.append(f"{code}: {e}")
            continue
        if since:
            dates = df["Date"].map(parse_date)
            df = df[dates.map(lambda d: d is not None and d.isoformat() >= since)]
        stored = store_rows(conn, df, league=code)
        total += stored
        log.info("%s: %d мача", code, stored)
    repair_names(conn)
    for message in failed:
        log.error("не се изтегли - %s", message)
    return total


def _oldb_matches(short, year):
    return json.loads(download(f"{OLDB}/getmatchdata/{short}/{year}", timeout=60))


def update_oldb(conn, years_back=1, leagues=None):
    """OpenLigaDB (3. Бундеслига): резултатите и предстоящите мачове на сезона, безплатно.
    Часът е в UTC и се пази в kickoff като HH:MM по британско време, както при football-data."""
    from zoneinfo import ZoneInfo
    uk = ZoneInfo("Europe/London")
    total = 0
    for code in (leagues or [c for c, lg in LEAGUES.items() if lg.source == "oldb"]):
        lg = LEAGUES[code]
        for offset in range(years_back):
            year = this_season_year() - offset
            try:
                data = _oldb_matches(lg.oldb, year)
            except RuntimeError as e:
                log.error("%s %d: %s", code, year, e)
                continue
            season = f"{year}/{year + 1}"
            for m in data:
                names = [(m.get(t) or {}).get("teamName") for t in ("team1", "team2")]
                if not all(names) or not (m.get("matchDateTimeUTC") or "").startswith("20"):
                    continue
                start = datetime.fromisoformat(m["matchDateTimeUTC"].replace("Z", "+00:00")).astimezone(uk)
                full = half = None
                if m.get("matchIsFinished"):
                    for r in m.get("matchResults") or []:
                        if r.get("resultTypeID") == 2:
                            full = (r["pointsTeam1"], r["pointsTeam2"])
                        elif r.get("resultTypeID") == 1:
                            half = (r["pointsTeam1"], r["pointsTeam2"])
                db.upsert_match(conn, code, season, start.date().isoformat(),
                                names[0].strip(), names[1].strip(),
                                *(full or (None, None)), *(half or (None, None)),
                                start.strftime("%H:%M"))
                total += 1
            conn.commit()
            log.info("%s %s: %d мача (с бъдещите)", code, season, len(data))
    return total


def update_fixtures(conn):
    """Предстоящите мачове (обикновено следващите 7-10 дни), с коефициенти - и двата файла."""
    stored = store_rows(conn, read_csv(download(FIXTURES_URL)), season_name(season_code(this_season_year())))
    try:
        df = read_csv(download(NEW_FIXTURES_URL), sep="\t")
    except RuntimeError as e:
        log.error("Разписанието на 16-те държави не се изтегли: %s", e)
        df = None
    new = 0
    if df is not None and "Country" in df.columns:
        for country, part in df.groupby("Country"):
            code = NEW_COUNTRY.get(country.strip())
            if code:
                new += store_rows(conn, part, season=current_season(conn, code), league=code)
            else:
                log.warning("Непозната държава в разписанието: %r", country)
    log.info("Разписание: %d мача в 22-те лиги, %d в 16-те държави", stored, new)
    return stored + new


def current_season(conn, league):
    row = conn.execute("SELECT season FROM matches WHERE league = ? AND fthg IS NOT NULL "
                       "ORDER BY date DESC LIMIT 1", (league,)).fetchone()
    return row["season"] if row else "?"


def history(conn, league, blend_xg=False, since=None):
    """Изиграните мачове на лигата, подредени по дата - входът за модела.

    blend_xg=True: за мачовете с xG (bets/xg.py) целта на модела е половин голове, половин xG -
    приета по xg_check (по-точен модел в избора и в чистата проверка). Само за обучението на
    модела; резултатите си остават истинските голове навсякъде другаде."""
    since = since or "1900-01-01"
    has_xg = blend_xg and conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'xg'").fetchone()
    if has_xg:
        rows = conn.execute(
            """SELECT m.date, m.home_team, m.away_team,
                      CASE WHEN x.xg_h IS NULL THEN m.fthg ELSE (m.fthg + x.xg_h) / 2.0 END,
                      CASE WHEN x.xg_a IS NULL THEN m.ftag ELSE (m.ftag + x.xg_a) / 2.0 END
                 FROM matches m LEFT JOIN xg x ON x.match_id = m.id
                WHERE m.league = ? AND m.fthg IS NOT NULL AND m.date >= ? ORDER BY m.date""",
            (league, since)).fetchall()
    else:
        rows = conn.execute(
            """SELECT date, home_team, away_team, fthg, ftag FROM matches
                WHERE league = ? AND fthg IS NOT NULL AND date >= ? ORDER BY date""", (league, since)).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["date", "home_team", "away_team", "fthg", "ftag"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


# Облакът пази само последните три пълни сезона + текущия. Измерено на 2026-09-25 в 8 лиги:
# моделът, обучен от 2023-07-01, се различава от обучения върху цялата история с най-много
# 0.66 процентни пункта (средно 0.1-0.2). Причината е затихването: мач отпреди три години тежи 1.5%.
WINDOW_SEASONS = 3


def history_window_start(today=None):
    return f"{this_season_year(today) - WINDOW_SEASONS}-07-01"


def prune_history(conn, since=None):
    """Маха изиграните мачове преди прозореца - за да остане базата на облака малка."""
    since = since or history_window_start()
    removed = conn.execute("DELETE FROM matches WHERE date < ? AND fthg IS NOT NULL", (since,)).rowcount
    conn.commit()
    if removed:
        log.info("Махнати %d мача преди %s", removed, since)
    return removed


def now_utc():
    return datetime.now(timezone.utc)
