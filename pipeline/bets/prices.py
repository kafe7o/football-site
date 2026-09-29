"""
Цените за предстоящите мачове: средна (какво дава обикновеният букмейкър) и най-добра, за
всички пазари на робота - 1, X, 2, двоен шанс (1X, X2, 12), над/под 2.5.

Източници:
  1. the-odds-api, пазари h2h + totals, регион eu - 2 кредита на лига на заявка;
  2. football-data (разписанието) - безплатно, AVG/MAX за 1/X/2 и над/под 2.5, но се обновява
     два пъти седмично. Ползва се, когато лигата я няма в odds API или още няма цени там.

Двоен шанс: odds API не го дава в основните пазари. Цената се смята от цените 1/X/2 на същия
букмейкър: 1 / (1/к1 + 1/кX). Точно толкова плаща разделянето на залога между двата изхода, и е
много близо до двойния шанс, който предлагат букмейкърите.

Кредитите: заявка се прави само за лиги с мач в следващите 3 дни - на 3 часа, ако мачът е до 14
часа, иначе на 12 часа. Под RESERVE кредита се теглят само цените за днешните мачове.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from statistics import mean

from . import db, odds_api
from .leagues import LEAGUES
from .market import implied_row

log = logging.getLogger(__name__)

EXCHANGES = {"betfair_ex_eu", "betfair_ex_uk", "smarkets", "matchbook"}
SELECTIONS = ["1", "X", "2", "1X", "X2", "12", "O", "U"]
NEAR, NEAR_TTL, FAR, FAR_TTL = timedelta(hours=14), timedelta(hours=3), timedelta(days=3), timedelta(hours=12)
RESERVE = 1500      # под толкова кредита - само цените за мачовете в следващите 14 часа
STOP = 200          # под толкова - никакви заявки за цени


def sane(prices):
    """Сборът на обратните стойности трябва да е реален (0.98-1.20): иначе е празен или сбъркан пазар."""
    total = sum(1 / p for p in prices if p and p > 1)
    return all(p and p > 1 for p in prices) and 0.98 <= total <= 1.20


def from_event(event):
    """Един мач от odds API -> {"avg": {...}, "best": {...}, "n": брой букмейкъри}."""
    home, away = event["home_team"], event["away_team"]
    per_book = []
    for book in event.get("bookmakers", []):
        if book["key"] in EXCHANGES:
            continue
        row = {}
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                o = {x["name"]: float(x["price"]) for x in market["outcomes"]}
                draw = next((n for n in o if n not in (home, away)), None)
                trio = [o.get(home), o.get(draw), o.get(away)]
                if draw and sane(trio):
                    row.update({"1": trio[0], "X": trio[1], "2": trio[2],
                                "1X": 1 / (1 / trio[0] + 1 / trio[1]),
                                "X2": 1 / (1 / trio[1] + 1 / trio[2]),
                                "12": 1 / (1 / trio[0] + 1 / trio[2])})
            elif market["key"] == "totals":
                o = {(x["name"], x.get("point")): float(x["price"]) for x in market["outcomes"]}
                pair = [o.get(("Over", 2.5)), o.get(("Under", 2.5))]
                if all(pair) and 1.0 <= sum(1 / p for p in pair) <= 1.15:
                    row.update({"O": pair[0], "U": pair[1]})
        if row:
            per_book.append(row)
    return summarize(per_book)


def summarize(per_book):
    if not per_book:
        return None
    out = {"avg": {}, "best": {}, "n": len(per_book)}
    for sel in SELECTIONS:
        values = [b[sel] for b in per_book if sel in b]
        if values:
            out["avg"][sel] = round(mean(values), 3)
            out["best"][sel] = round(max(values), 3)
    out["n_ou"] = sum(1 for b in per_book if "O" in b)
    return out


def from_football_data(conn, match_id):
    """Цените от разписанието на football-data за мача (AVG и MAX), или None."""
    if not match_id:
        return None
    rows = {r["bookmaker"]: r for r in conn.execute(
        "SELECT * FROM odds WHERE match_id = ? AND bookmaker IN ('AVG', 'MAX', 'B365', 'PS')", (match_id,))}
    tot = {r["bookmaker"]: r for r in conn.execute(
        "SELECT * FROM odds_totals WHERE match_id = ? AND line = 2.5 AND bookmaker IN ('AVG', 'MAX')", (match_id,))}
    base = rows.get("AVG") or rows.get("B365") or rows.get("PS")
    if base is None or not sane([base["odds_home"], base["odds_draw"], base["odds_away"]]):
        return None

    def full(r, t):
        o1, ox, o2 = r["odds_home"], r["odds_draw"], r["odds_away"]
        d = {"1": o1, "X": ox, "2": o2, "1X": 1 / (1 / o1 + 1 / ox), "X2": 1 / (1 / ox + 1 / o2),
             "12": 1 / (1 / o1 + 1 / o2)}
        if t is not None and t["odds_over"] and t["odds_under"]:
            d.update({"O": t["odds_over"], "U": t["odds_under"]})
        return {k: round(v, 3) for k, v in d.items()}

    best_row = rows.get("MAX") or base
    return {"avg": full(base, tot.get("AVG")), "best": full(best_row, tot.get("MAX") or tot.get("AVG")),
            "n": 0, "src": "football-data"}


def fair(prices):
    """Пазарните вероятности без маржа (степенно махане, bets/market.py) - от средните цени."""
    if not prices or not prices.get("avg"):
        return None
    avg = prices["avg"]
    if not all(k in avg for k in ("1", "X", "2")):
        return None
    p = implied_row([avg["1"], avg["X"], avg["2"]])
    if p is None:
        return None
    out = {"1": p[0], "X": p[1], "2": p[2], "1X": p[0] + p[1], "X2": p[1] + p[2], "12": p[0] + p[2]}
    if "O" in avg and "U" in avg:
        q = _two_way(avg["O"], avg["U"])
        if q:
            out.update({"O": q[0], "U": q[1]})
    return out


def _two_way(a, b):
    """Степенно махане на маржа за два изхода."""
    inv = [1 / a, 1 / b]
    if sum(inv) < 1:
        return inv[0] / sum(inv), inv[1] / sum(inv)
    lo, hi = 1.0, 8.0
    for _ in range(60):
        k = (lo + hi) / 2
        if inv[0] ** k + inv[1] ** k > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    x, y = inv[0] ** k, inv[1] ** k
    return x / (x + y), y / (x + y)


def refresh(conn, now=None):
    """Тегли цените от odds API за лигите с мачове скоро и ги записва в fixtures.prices_json.
    Връща броя на заявките."""
    now = now or datetime.now(timezone.utc)
    remaining = db.get_meta(conn, "credits_remaining")
    remaining = int(remaining) if remaining and remaining.isdigit() else None
    by_sport = {}
    for f in conn.execute("SELECT f.id, f.league, f.kickoff, f.source FROM fixtures f "
                          "WHERE f.source = 'api' AND f.kickoff > ?", (now.isoformat(),)):
        lg = LEAGUES.get(f["league"])
        by_sport.setdefault(lg.sport if lg else None, []).append(f)
    asked = 0
    for sport, rows in sorted(by_sport.items()):
        if not sport:
            continue
        soonest = min(datetime.fromisoformat(r["kickoff"]) for r in rows) - now
        if soonest > FAR:
            continue
        ttl = NEAR_TTL if soonest <= NEAR else FAR_TTL
        if remaining is not None and (remaining < STOP or (remaining < RESERVE and soonest > NEAR)):
            continue
        last = db.get_meta(conn, f"prices_at:{sport}")
        if last and now - datetime.fromisoformat(last) < ttl:
            continue
        try:
            data = odds_api.odds(sport, markets="h2h,totals", cache_minutes=0)
        except RuntimeError as e:
            log.error("%s: цените не се изтеглиха - %s", sport, e)
            continue
        asked += 1
        remaining = odds_api.last_remaining() or remaining
        db.set_meta(conn, f"prices_at:{sport}", now.isoformat(timespec="seconds"))
        for event in data:
            p = from_event(event)
            if p:
                p["src"] = "odds-api"
                conn.execute("UPDATE fixtures SET prices_json = ?, prices_at = ? WHERE id = ?",
                             (json.dumps(p), now.isoformat(timespec="seconds"), event["id"]))
        conn.commit()
    if remaining is not None:
        db.set_meta(conn, "credits_remaining", remaining)
    log.info("Цени: %d заявки към odds API, остават %s кредита", asked, remaining)
    return asked


def for_fixture(conn, fixture):
    """Цените за мача: от odds API, ако ги има, иначе от football-data."""
    if fixture["prices_json"]:
        return json.loads(fixture["prices_json"])
    return from_football_data(conn, fixture["match_id"])
