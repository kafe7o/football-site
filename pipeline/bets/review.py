"""
Самопроверката на правилото - системата се учи сама, но на ниво правила (2026-09-28).

Моделът се преобучава всяка седмица, но не става по-добър от пазара (legacy/rule_backtest.py).
Затова "да се учи сам" тук значи друго: веднъж седмично системата премерва собствените си
залози (таблицата decisions) по сегменти - коефициент, лига, изход - и ИЗКЛЮЧВА сегмент, който
губи ясно, а не по усещане:
  - поне MIN_BETS уредени залога в сегмента;
  - доходът на залог е под нулата с над T_LIMIT стандартни грешки.
Прагът е записан предварително и не се мени по резултата. При малко залози нищо не се
изключва - това е нарочно: 20 загубени залога са шум, не урок.
"""

import json
import logging
import math
from datetime import datetime, timedelta, timezone

from . import db, predict, teams

log = logging.getLogger(__name__)

MIN_BETS = 60
T_LIMIT = -2.0
EVERY = timedelta(days=7)
ODDS_BANDS = [(1.0, 2.0, "под 2.00"), (2.0, 3.0, "2.00-3.00"), (3.0, 5.0, "3.00-5.00"), (5.0, 1e9, "5.00+")]
OUTCOMES = ["домакин", "равен", "гост"]
SPORT_TO_LEAGUE = {v: k for k, v in predict.LEAGUE_TO_SPORT.items()}


def segments(m):
    """Сегментите на мач с избор: коефициент, лига, изход. m - ред от прегледа или решение."""
    pick = m.get("pick")
    if not pick:
        return []
    out = []
    band = next((key for lo, hi, key in ODDS_BANDS if lo <= pick["odds"] < hi), None)
    if band:
        out.append(f"коеф:{band}")
    league = SPORT_TO_LEAGUE.get(m.get("sport", ""))
    if league:
        out.append(f"лига:{league}")
    if pick.get("outcome_idx") is not None:
        out.append(f"изход:{OUTCOMES[pick['outcome_idx']]}")
    return out


def label(segment):
    kind, value = segment.split(":", 1)
    return {"коеф": f"коефициенти {value}", "лига": f"лига {value}", "изход": f"залог на {value}"}.get(kind, segment)


def excluded(conn):
    data = db.get_meta(conn, "review")
    return set(json.loads(data).get("excluded", [])) if data else set()


def _outcome(conn, decision):
    """Изходът на мача (0/1/2) или None: мачът от odds API -> мачът в базата."""
    league = SPORT_TO_LEAGUE.get(decision["sport"])
    if not league:
        return None
    day = datetime.fromisoformat(decision["commence_time"].replace("Z", "+00:00")).date()
    fixtures = [dict(r) for r in conn.execute(
        """SELECT home_team, away_team, fthg, ftag FROM matches
            WHERE league = ? AND date BETWEEN ? AND ? AND fthg IS NOT NULL""",
        (league, (day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat()))]
    hit = teams.match_fixture(decision["home_team"], decision["away_team"], fixtures)
    if hit is None:
        return None
    return 0 if hit["fthg"] > hit["ftag"] else (1 if hit["fthg"] == hit["ftag"] else 2)


def run(conn, now=None):
    """Премерва уредените залози по сегменти и записва кои се изключват (meta "review")."""
    from .decide import SCHEMA
    conn.executescript(SCHEMA)
    now = now or datetime.now(timezone.utc)
    stats = {}
    settled = 0
    for r in conn.execute("SELECT * FROM decisions WHERE bet = 1 AND pick_json IS NOT NULL"):
        d = dict(r)
        d["pick"] = json.loads(d["pick_json"])
        outcome = _outcome(conn, d)
        if outcome is None or d["pick"].get("outcome_idx") is None:
            continue
        settled += 1
        profit = d["pick"]["odds"] - 1 if outcome == d["pick"]["outcome_idx"] else -1.0
        for seg in segments(d):
            stats.setdefault(seg, []).append(profit)
    table, out = {}, []
    for seg, profits in sorted(stats.items()):
        n = len(profits)
        mean = sum(profits) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in profits) / (n - 1)) if n > 1 else 0.0
        se = sd / math.sqrt(n) if n else 0.0
        # Всички загубени (или всички спечелени) - грешката е 0; знакът решава.
        t = mean / se if se else (float("-inf") if mean < 0 else float("inf") if mean > 0 else 0.0)
        table[seg] = {"n": n, "roi": mean, "se": se, "t": max(min(t, 99.0), -99.0)}
        if n >= MIN_BETS and t <= T_LIMIT:
            out.append(seg)
    result = {"at": now.isoformat(timespec="seconds"), "settled": settled, "min_bets": MIN_BETS,
              "t_limit": T_LIMIT, "segments": table, "excluded": out}
    db.set_meta(conn, "review", json.dumps(result, ensure_ascii=False))
    log.info("Самопроверка: %d уредени залога, %d сегмента, изключени %d", settled, len(table), len(out))
    return result


def due(conn, now=None):
    now = now or datetime.now(timezone.utc)
    data = db.get_meta(conn, "review")
    return not data or now - datetime.fromisoformat(json.loads(data)["at"]) >= EVERY


def run_if_due(conn):
    return run(conn) if due(conn) else None


def summary(conn):
    data = db.get_meta(conn, "review")
    return json.loads(data) if data else None
