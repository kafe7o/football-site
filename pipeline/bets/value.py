"""
Залози по ЦЕНА, а не по прогноза.

Откъде идва идеята: в собствения ни бектест едни и същи залози дадоха +2.6% на най-добрата
цена и -5.1% на средната (`legacy/price_check.py`). Разликата не беше прогноза, а цена.
Значи работата не е да познаваме мачовете, а да намираме кой букмейкър е изостанал.

Как:
  1. Еталон - Pinnacle, после борсите, после медианата на поне 8 букмейкъра.
  2. От еталона се маха маржът (степенно, виж market.py) -> честни вероятности.
  3. За всеки друг букмейкър: edge = честна вероятност * неговата цена - 1.
  4. Всичко над прага се записва ПРЕДИ мача, с час.
  5. Всяко следващо сканиране презаписва "затварящата" цена. Така се мери движението:
     ако пазарът идва към нас, разликата е истинска. Това се вижда на десетки залози,
     докато ROI иска стотици.

Тук не се отварят и не се ползват никакви сметки при букмейкъри. Само публични коефициенти.
"""

import json
import logging
from datetime import datetime, timezone

from . import db, odds_api
from .market import implied_probs

log = logging.getLogger(__name__)

SHARP_ORDER = ["pinnacle", "betfair_ex_eu", "betfair_ex_uk", "smarkets", "matchbook"]
# Борсата не е букмейкър: цената ѝ е брутна и сама по себе си е еталон.
EXCHANGES = {"betfair_ex_eu": 0.05, "betfair_ex_uk": 0.05, "smarkets": 0.02, "matchbook": 0.015}
MIN_BOOKS = 4          # под толкова пазарът е твърде тънък, за да му се вярва
MIN_CONSENSUS = 8      # медиана за еталон само при поне толкова букмейкъра
MIN_EDGE = 0.02
SUSPICIOUS_EDGE = 0.15  # над това почти винаги е замръзнала или сбъркана цена

# Степен на сигурност - кои разлики е най-вероятно да са истински, а не грешка в четенето.
# НЕ е гаранция: това е подредба по качество на доказателството. Коя степен реално работи,
# се мери отделно по движението на цената (record) - след 30+ залога във всяка степен
# ще е ясно дали "A" наистина е по-добра от "B", или подредбата е била само надежда.
#   A: еталонът е остър (Pinnacle/борса), разликата е 2-8%, поне 10 букмейкъра видели мача
#   B: разликата е 2-8%, но еталонът е по-слаб или букмейкърите са по-малко
#   C: разликата е над 8% - при остър еталон това почти винаги е замръзнала цена
SHARP_BOOKS = {"pinnacle", "betfair_ex_eu", "betfair_ex_uk", "smarkets", "matchbook"}
TIER_MAX_EDGE = 0.08
TIER_MIN_BOOKS = 10


def tier(sharp_book, edge, n_books):
    if edge > TIER_MAX_EDGE:
        return "C"
    if sharp_book in SHARP_BOOKS and (n_books or 0) >= TIER_MIN_BOOKS:
        return "A"
    return "B"

FOOTBALL = ["soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
            "soccer_germany_bundesliga", "soccer_france_ligue_one", "soccer_efl_champ"]
OTHER_SPORTS = ["basketball_euroleague", "basketball_nba", "americanfootball_nfl",
                "baseball_mlb", "icehockey_nhl"]


def prices_by_book(event):
    books = {}
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                books[book["key"]] = {o["name"]: float(o["price"]) for o in market["outcomes"]}
    return books


def outcome_order(event, books):
    """Домакин, (равен), гост - футболът има три изхода, другите спортове два."""
    names = {n for prices in books.values() for n in prices}
    home, away = event["home_team"], event["away_team"]
    draw = next((n for n in names if n not in (home, away)), None)
    return [n for n in (home, draw, away) if n in names]


def median(values):
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def reference(books, order):
    """(име на еталона, цените му, честните вероятности) или (None, None, None)."""
    for name in SHARP_ORDER:
        prices = books.get(name)
        if prices and all(n in prices for n in order):
            probs = implied_probs([[prices[n] for n in order]])[0]
            if all(p == p for p in probs):
                return name, [prices[n] for n in order], list(probs)
    complete = [p for p in books.values() if all(n in p for n in order)]
    if len(complete) < MIN_CONSENSUS:
        return None, None, None
    consensus = [median([p[n] for p in complete]) for n in order]
    probs = implied_probs([consensus])[0]
    if not all(p == p for p in probs):
        return None, None, None
    return f"consensus{len(complete)}", consensus, list(probs)


def net_price(book, odds):
    """Цената, която реално получаваш: при борса минус комисионата."""
    commission = EXCHANGES.get(book)
    return 1 + (odds - 1) * (1 - commission) if commission else odds


def scan_event(event, sport, min_edge=MIN_EDGE, allowed=None, include_exchanges=False):
    books = prices_by_book(event)
    if len(books) < MIN_BOOKS:
        return []
    order = outcome_order(event, books)
    if len(order) not in (2, 3):
        return []
    sharp, sharp_prices, fair = reference(books, order)
    if sharp is None:
        return []

    found = []
    for book, prices in books.items():
        if book == sharp or (book in EXCHANGES and not include_exchanges):
            continue
        if allowed and book not in allowed:
            continue
        for i, name in enumerate(order):
            if name not in prices:
                continue
            odds = net_price(book, prices[name])
            edge = fair[i] * odds - 1
            if edge < min_edge:
                continue
            if edge > SUSPICIOUS_EDGE:
                log.warning("%s - %s: %s @ %.2f при %s дава %+.0f%% срещу %s - толкова голяма "
                            "разлика почти винаги е сбъркана цена, не пропуск",
                            event["home_team"], event["away_team"], name, odds, book,
                            edge * 100, sharp)
            found.append({"sport": sport, "event_id": event["id"],
                          "home_team": event["home_team"], "away_team": event["away_team"],
                          "commence_time": event["commence_time"], "selection": name,
                          "outcome_idx": i, "bookmaker": book, "odds": odds,
                          "sharp_book": sharp, "sharp_odds": sharp_prices[i],
                          "p_fair": fair[i], "edge": edge, "n_books": len(books)})
    return found


def store_fair(conn, event, sport, books, order, sharp, sharp_prices, fair):
    """Честната цена за всеки изход - справочникът, по който се сравнява цена на
    букмейкър, който го няма в API-то (efbet, winbet)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i, name in enumerate(order):
        prices = [(b, p[name]) for b, p in books.items() if name in p and b not in EXCHANGES]
        best_book, best_odds = max(prices, key=lambda x: x[1], default=(None, None))
        all_prices = json.dumps(dict(sorted(prices, key=lambda x: -x[1])))
        conn.execute(
            """INSERT INTO fair_prices (sport, event_id, home_team, away_team, commence_time,
                   selection, outcome_idx, p_fair, sharp_book, sharp_odds, best_book, best_odds,
                   prices_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_id, selection) DO UPDATE SET
                   p_fair = excluded.p_fair, sharp_odds = excluded.sharp_odds,
                   sharp_book = excluded.sharp_book, best_book = excluded.best_book,
                   best_odds = excluded.best_odds, prices_json = excluded.prices_json,
                   updated_at = excluded.updated_at""",
            (sport, event["id"], event["home_team"], event["away_team"], event["commence_time"],
             name, i, fair[i], sharp, sharp_prices[i], best_book, best_odds, all_prices, now))


def store(conn, rows):
    """Нов залог се записва веднъж. Повторната поява е същият залог, не нов."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = 0
    for row in rows:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO value_bets
               (sport, event_id, home_team, away_team, commence_time, selection, outcome_idx,
                bookmaker, odds, sharp_book, sharp_odds, p_fair, edge, n_books, found_at)
               VALUES (:sport, :event_id, :home_team, :away_team, :commence_time, :selection,
                       :outcome_idx, :bookmaker, :odds, :sharp_book, :sharp_odds, :p_fair,
                       :edge, :n_books, :found_at)""", {**row, "found_at": now})
        added += cursor.rowcount
    conn.commit()
    return added


def refresh_closing(conn, events):
    """Последната видяна цена преди мача - с нея се мери движението."""
    updated = 0
    for event in events:
        books = prices_by_book(event)
        order = outcome_order(event, books)
        if len(order) not in (2, 3):
            continue
        sharp, _, fair = reference(books, order)
        if sharp is None:
            continue
        for i, name in enumerate(order):
            for book, prices in books.items():
                if name in prices:
                    updated += conn.execute(
                        """UPDATE value_bets SET closing_odds = ?, closing_fair = ?
                            WHERE event_id = ? AND selection = ? AND bookmaker = ?
                              AND result IS NULL""",
                        (net_price(book, prices[name]), fair[i], event["id"], name, book)).rowcount
    conn.commit()
    return updated


def scan(conn=None, sports=None, regions="eu", min_edge=MIN_EDGE, allowed=None):
    conn = conn or db.init()
    now = datetime.now(timezone.utc)
    found, seen = [], 0
    for sport in sports or FOOTBALL:
        try:
            events = odds_api.odds(sport, regions)
        except RuntimeError as e:
            log.error("%s: %s", sport, e)
            continue
        upcoming = [e for e in events
                    if datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) > now]
        seen += len(upcoming)
        refresh_closing(conn, upcoming)
        for event in upcoming:
            found.extend(scan_event(event, sport, min_edge, allowed))
            books = prices_by_book(event)
            order = outcome_order(event, books)
            if len(order) in (2, 3):
                sharp, sharp_prices, fair = reference(books, order)
                if sharp:
                    store_fair(conn, event, sport, books, order, sharp, sharp_prices, fair)
        conn.commit()
    added = store(conn, found)
    log.info("Прегледани %d мача, намерени %d предложения, нови %d", seen, len(found), added)
    return found


def settle(conn=None):
    """Уреждане: футболът от базата (безплатно), останалите спортове от odds API."""
    conn = conn or db.init()
    now = datetime.now(timezone.utc)
    pending = conn.execute("SELECT * FROM value_bets WHERE result IS NULL").fetchall()
    due = [b for b in pending
           if datetime.fromisoformat(b["commence_time"].replace("Z", "+00:00")) <= now]
    settled, unknown, scores = 0, 0, {}

    for bet in due:
        start = datetime.fromisoformat(bet["commence_time"].replace("Z", "+00:00"))
        from_db = _from_db(conn, bet, start)
        outcome, match_id = from_db if from_db else (None, None)
        if outcome is None:
            sport = bet["sport"]
            if sport not in scores:
                try:
                    scores[sport] = odds_api.scores(sport, days_from=min(max((now - start).days + 1, 1), 3))
                except RuntimeError as e:
                    log.error("%s: резултатите не се изтеглиха - %s", sport, e)
                    scores[sport] = {}
            score = scores[sport].get(bet["event_id"])
            outcome = (odds_api.outcome_index(score, sport.startswith("soccer_"))
                       if score else None)
        if outcome is None:
            unknown += 1
            continue
        won = int(outcome == bet["outcome_idx"])
        conn.execute(
            "UPDATE value_bets SET result=?, profit=?, match_id=?, settled_at=? WHERE id=?",
            (won, bet["odds"] - 1 if won else -1.0, match_id,
             now.isoformat(timespec="seconds"), bet["id"]))
        settled += 1
    conn.commit()
    log.info("Уредени %d залога, без намерен резултат %d, чакат мача %d",
             settled, unknown, len(pending) - len(due))
    return settled


def _from_db(conn, bet, start):
    """Футболен мач в базата по дата и приблизително име. Връща (изход, id) или None."""
    if not bet["sport"].startswith("soccer_"):
        return None
    row = conn.execute(
        """SELECT id, fthg, ftag FROM matches
            WHERE fthg IS NOT NULL
              AND date BETWEEN date(?, '-1 day') AND date(?, '+1 day')
              AND (home_team LIKE ? OR ? LIKE '%' || home_team || '%')
              AND (away_team LIKE ? OR ? LIKE '%' || away_team || '%')
            LIMIT 1""",
        (start.date().isoformat(), start.date().isoformat(),
         f"%{bet['home_team'].split()[0]}%", bet["home_team"],
         f"%{bet['away_team'].split()[0]}%", bet["away_team"])).fetchone()
    if row is None:
        return None
    return (0 if row["fthg"] > row["ftag"] else (1 if row["fthg"] == row["ftag"] else 2)), row["id"]


def pick_for_match(bets):
    """Изборът за мача: най-добрата цена над честната, от степен A, ако има, иначе от B.

    Степен C се изключва - разлика над 8% почти винаги е замръзнала цена, а такъв залог
    обикновено се анулира. Ако няма нищо от A или B, изборът е "без избор": никоя цена не
    е над честната и най-добрият залог за този мач е никакъв залог.

    Изборът е по ЦЕНА, не по модела: моделът е измерено губещ (-54% по правилото за сигнал),
    а цената над честната е единственото с шанс да е на плюс. Дали е - се мери по степени
    (record_by_tier) и стои до избора на сайта.
    """
    # Борсите не стават за избор: цената им е брутна (комисионата е отделно), а и те са
    # еталонът. Стари записи отпреди изключването им още стоят в книгата.
    usable = [b for b in bets
              if b["bookmaker"] not in EXCHANGES
              and tier(b["sharp_book"], b["edge"], b.get("n_books")) in ("A", "B")]
    if not usable:
        return None
    order = {"A": 0, "B": 1}
    best = min(usable, key=lambda b: (order[tier(b["sharp_book"], b["edge"], b.get("n_books"))],
                                      -b["edge"]))
    return {"selection": best["selection"], "odds": best["odds"], "bookmaker": best["bookmaker"],
            "edge": best["edge"], "p_fair": best["p_fair"],
            "sharp_book": best["sharp_book"], "sharp_odds": best["sharp_odds"],
            "n_books": best.get("n_books"), "found_at": best.get("found_at"),
            "tier": tier(best["sharp_book"], best["edge"], best.get("n_books"))}


def record_by_tier(conn):
    """Движение на цената и ROI поотделно за A, B и C. Това е проверката дали
    "най-сигурните" наистина са по-добри - или подредбата е само подредба."""
    rows = conn.execute(
        """SELECT sharp_book, edge, n_books, odds, closing_odds, result, profit
             FROM value_bets""").fetchall()
    out = {}
    for r in rows:
        t = tier(r["sharp_book"], r["edge"], r["n_books"])
        d = out.setdefault(t, {"n": 0, "drift": [], "profits": []})
        d["n"] += 1
        if r["closing_odds"]:
            d["drift"].append(r["odds"] / r["closing_odds"] - 1)
        if r["result"] is not None:
            d["profits"].append(r["profit"])
    for t, d in out.items():
        drift, profits = d.pop("drift"), d.pop("profits")
        d["clv"] = sum(drift) / len(drift) if drift else None
        d["clv_n"] = len(drift)
        d["roi"] = sum(profits) / len(profits) if profits else None
        d["settled"] = len(profits)
    return out


def record(conn):
    """Измереното: ROI и движението на цената."""
    done = conn.execute("SELECT profit, result FROM value_bets WHERE result IS NOT NULL").fetchall()
    moved = conn.execute(
        "SELECT odds, closing_odds FROM value_bets WHERE closing_odds IS NOT NULL").fetchall()
    out = {"n": len(done)}
    if done:
        profits = [r["profit"] for r in done]
        roi = sum(profits) / len(profits)
        out.update({"wins": sum(r["result"] for r in done), "profit": sum(profits), "roi": roi,
                    "roi_se": ((sum((p - roi) ** 2 for p in profits) / (len(profits) - 1)) ** 0.5
                               / len(profits) ** 0.5) if len(profits) > 1 else None})
    drift = [r["odds"] / r["closing_odds"] - 1 for r in moved if r["closing_odds"]]
    if drift:
        out["clv"] = {"n": len(drift), "avg": sum(drift) / len(drift),
                      "better": sum(1 for d in drift if d > 0)}
    return out
