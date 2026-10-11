"""
Историята на съставите и контузените от API-Football (за проверката дали помагат: research/squads_backtest.py) - собственикът, 2026-10-11:
„да, изтегли ги за миналото и провери дали подобряват прогнозите“.

Тегли за топ 5 първенства (Англия, Испания, Италия, Германия, Франция), сезони 2025/26, 2024/25 и текущия 2026/27, за всеки приключил мач:
  fixtures/lineups?fixture=ID  - стартовите 11 на двата отбора;
  injuries?fixture=ID          - кой е обявен за отсъстващ (контузия, наказание, под въпрос).
Записва в apif_history.db (на лаптопа, не се качва никъде). Може да се прекъсва и пуска наново - не тегли два пъти. Ключът е само в .env и не се печата.

БЮДЖЕТ: ~8 000 заявки общо. Дневният лимит на плана (7 500) е общ с облака, който ползва 1 200-3 200 на ден за живите състави - затова скриптът спира, щом
остатъкът на плана падне под RESERVE (3 500), и продължава след подновяването в 00:00 UTC. Така облакът винаги има достатъчно. Не повече от ~5 заявки в секунда.

    python research/apif_history_pull.py            тегли докато стигне всичко (спи между дните)
    python research/apif_history_pull.py --once     само до резервата, после излиза
    python research/apif_history_pull.py --status   колко е изтеглено
"""

import json
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                              # noqa: E402

load_dotenv(ROOT / ".env")

import os                                                    # noqa: E402

import certifi                                               # noqa: E402

DB = ROOT / "apif_history.db"
BASE = "https://v3.football.api-sports.io/"
LEAGUES = {39: "E0", 140: "SP1", 135: "I1", 78: "D1", 61: "F1"}
SEASONS = (2025, 2024, 2026)
RESERVE = 3500
PAUSE = 0.22
CTX = ssl.create_default_context(cafile=certifi.where())
state = {"remaining": None, "used": 0}

SCHEMA = """
CREATE TABLE IF NOT EXISTS fixtures (fixture_id INTEGER PRIMARY KEY, league TEXT, season INTEGER, date TEXT, home TEXT, away TEXT,
    home_id INTEGER, away_id INTEGER, hg INTEGER, ag INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS lineups (fixture_id INTEGER, team_id INTEGER, team TEXT, formation TEXT, xi_json TEXT, PRIMARY KEY (fixture_id, team_id));
CREATE TABLE IF NOT EXISTS injuries (fixture_id INTEGER, team_id INTEGER, player_id INTEGER, player TEXT, type TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS pulled (fixture_id INTEGER, kind TEXT, at TEXT, PRIMARY KEY (fixture_id, kind));
CREATE INDEX IF NOT EXISTS injuries_fx ON injuries (fixture_id);
"""


def get(path, params):
    """Една заявка; връща списъка response или None. Ключът никога не се печата."""
    key = os.environ.get("APIFOOTBALL_KEY")
    if not key:
        raise SystemExit("няма APIFOOTBALL_KEY в .env")
    req = urllib.request.Request(BASE + path + "?" + urllib.parse.urlencode(params), headers={"x-apisports-key": key})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=40, context=CTX) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                left = resp.headers.get("x-ratelimit-requests-remaining")
            break
        except (urllib.error.URLError, OSError, ValueError) as e:
            if attempt == 3 or (isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429):
                print("грешка:", path, str(e).replace(key, "<КЛЮЧ>"), flush=True)
                return None
            time.sleep(3 * attempt)
    state["used"] += 1
    if left is not None and left.isdigit():
        state["remaining"] = int(left)
    if body.get("errors"):
        print("грешка от API:", path, str(body["errors"])[:160], flush=True)
        return None
    time.sleep(PAUSE)
    return body.get("response") or []


def init():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def need_fixture_lists(conn):
    for lid, code in LEAGUES.items():
        for season in SEASONS:
            if conn.execute("SELECT 1 FROM pulled WHERE fixture_id = ? AND kind = 'list'", (-(lid * 10000 + season),)).fetchone():
                continue
            rows = get("fixtures", {"league": lid, "season": season})
            if rows is None:
                continue
            for f in rows:
                conn.execute("INSERT OR REPLACE INTO fixtures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (f["fixture"]["id"], code, season, f["fixture"]["date"], f["teams"]["home"]["name"], f["teams"]["away"]["name"],
                              f["teams"]["home"]["id"], f["teams"]["away"]["id"], f["goals"]["home"], f["goals"]["away"], f["fixture"]["status"]["short"]))
            conn.execute("INSERT OR REPLACE INTO pulled VALUES (?, 'list', ?)", (-(lid * 10000 + season), datetime.now(timezone.utc).isoformat()))
            conn.commit()
            print(f"списък {code} {season}: {len(rows)} мача", flush=True)


def todo(conn):
    order = {s: i for i, s in enumerate(SEASONS)}
    rows = conn.execute("SELECT fixture_id, league, season, date FROM fixtures WHERE status = 'FT' AND fixture_id NOT IN "
                        "(SELECT fixture_id FROM pulled WHERE kind = 'both') ORDER BY season DESC, date").fetchall()
    # сезон 2025, после 2024, после 2026 (по реда в SEASONS); вътре - хронологично
    return sorted(rows, key=lambda r: (order[r["season"]], r["date"]))


def pull_one(conn, fid):
    lu = get("fixtures/lineups", {"fixture": fid})
    if lu is None:
        return False
    inj = get("injuries", {"fixture": fid})
    if inj is None:
        return False
    for x in lu:
        conn.execute("INSERT OR REPLACE INTO lineups VALUES (?, ?, ?, ?, ?)",
                     (fid, x["team"]["id"], x["team"]["name"], x.get("formation"),
                      json.dumps([[p["player"]["id"], p["player"]["name"], p["player"].get("pos"), p["player"].get("grid")] for p in x.get("startXI") or []], ensure_ascii=False)))
    conn.execute("DELETE FROM injuries WHERE fixture_id = ?", (fid,))
    for x in inj:
        conn.execute("INSERT INTO injuries VALUES (?, ?, ?, ?, ?, ?)",
                     (fid, x["team"]["id"], x["player"].get("id"), x["player"].get("name"), x["player"].get("type"), x["player"].get("reason")))
    conn.execute("INSERT OR REPLACE INTO pulled VALUES (?, 'both', ?)", (fid, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return True


def status(conn):
    tot = conn.execute("SELECT COUNT(*) FROM fixtures WHERE status = 'FT'").fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM pulled WHERE kind = 'both'").fetchone()[0]
    print(f"приключили мачове: {tot}; със състави и контузени: {done} ({done / tot:.0%})" if tot else "още няма списъци")
    for r in conn.execute("SELECT f.league, f.season, COUNT(*) n, SUM(p.fixture_id IS NOT NULL) d FROM fixtures f LEFT JOIN pulled p "
                          "ON p.fixture_id = f.fixture_id AND p.kind = 'both' WHERE f.status = 'FT' GROUP BY f.league, f.season ORDER BY f.season DESC, f.league"):
        print(f"  {r['league']} {r['season']}: {r['d']}/{r['n']}")


def sleep_until_reset():
    now = datetime.now(timezone.utc)
    wake = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    print(f"остатъкът е под резерва ({state['remaining']}); чакам до {wake:%d.%m %H:%M} UTC", flush=True)
    time.sleep(max(60, (wake - now).total_seconds()))


def main():
    conn = init()
    if "--status" in sys.argv:
        status(conn)
        return
    once = "--once" in sys.argv
    while True:
        need_fixture_lists(conn)
        work = todo(conn)
        if not work:
            print("всичко е изтеглено", flush=True)
            status(conn)
            return
        stopped = False
        for i, r in enumerate(work):
            if state["remaining"] is not None and state["remaining"] < RESERVE:
                stopped = True
                break
            if not pull_one(conn, r["fixture_id"]):
                time.sleep(5)
            if i % 100 == 0:
                print(f"{datetime.now():%H:%M:%S} остават {len(work) - i} мача; остатък на плана {state['remaining']}", flush=True)
        if once:
            status(conn)
            return
        if stopped:
            sleep_until_reset()
            state["remaining"] = None


if __name__ == "__main__":
    main()
