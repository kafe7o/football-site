"""
Прогнози за днешните мачове и сверяване със резултата.

Две правила, които не се заобикалят:

  1. Прогноза се прави САМО в деня на мача и САМО преди началния час. Прогноза за събота,
     записана в понеделник, пропуска пет дни резултати и движение на цената.
  2. Записът е immutable. Веднъж записана прогноза не се променя - иначе статистиката
     после е нагласена.

Цените идват от разписанието на football-data (средните на пазара). Нарочно не се ползва
odds API тук: имената на отборите там са различни и всяко разминаване е източник на тихи
грешки, а квотата трябва за скенера на цени (value.py), където имената идват от самото API.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import db, model, results

log = logging.getLogger(__name__)

UK = ZoneInfo("Europe/London")     # football-data дава часовете в британско време


def kickoff_time(day, kickoff):
    """ISO с часова зона. Без час се приема 12:00, за да не изглежда мачът минал."""
    hour, minute = (kickoff or "12:00").split(":")[:2] if kickoff else ("12", "00")
    try:
        start = datetime.fromisoformat(day).replace(hour=int(hour), minute=int(minute), tzinfo=UK)
    except ValueError:
        start = datetime.fromisoformat(day).replace(hour=12, tzinfo=UK)
    return start


def market_odds(conn, match_id):
    row = conn.execute(
        """SELECT odds_home, odds_draw, odds_away, bookmaker FROM odds
            WHERE match_id = ? AND bookmaker IN ('AVG', 'B365', 'MAX')
            ORDER BY CASE bookmaker WHEN 'AVG' THEN 1 WHEN 'B365' THEN 2 ELSE 3 END
            LIMIT 1""", (match_id,)).fetchone()
    if row is None:
        return None, None, None, None
    return row["odds_home"], row["odds_draw"], row["odds_away"], row["bookmaker"]


def run(conn=None, day=None):
    """Записва прогнози за мачовете днес. Връща броя нови записи."""
    conn = conn or db.init()
    now = datetime.now().astimezone()
    day = day or now.date().isoformat()
    logged, skipped, late = 0, 0, 0

    for league in results.LEAGUES:
        fixtures = results.upcoming(conn, league, day)
        if not fixtures:
            continue
        history = results.history(conn, league)
        if len(history) < model.MIN_TRAIN_MATCHES:
            log.warning("%s: само %d мача в базата - пропуска се", league, len(history))
            continue
        fitted = model.Poisson().fit(history)

        for fixture in fixtures:
            start = kickoff_time(fixture["date"], fixture["kickoff"])
            if start <= now:
                late += 1
                continue
            probs = fitted.probabilities(fixture["home_team"], fixture["away_team"])
            if probs is None:
                skipped += 1
                continue
            home_odds, draw_odds, away_odds, book = market_odds(conn, fixture["id"])
            cursor = conn.execute(
                """INSERT OR IGNORE INTO predictions
                   (league, home_team, away_team, match_date, predicted_at, model_version,
                    p_home, p_draw, p_away, odds_home, odds_draw, odds_away, bookmaker, match_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (league, fixture["home_team"], fixture["away_team"], start.isoformat(),
                 datetime.now(timezone.utc).isoformat(timespec="seconds"), model.VERSION,
                 *probs, home_odds, draw_odds, away_odds, book, fixture["id"]))
            if cursor.rowcount:
                logged += 1
                log.info("%s: %s - %s %s -> %.0f%% / %.0f%% / %.0f%%", league,
                         fixture["home_team"], fixture["away_team"], start.strftime("%H:%M"),
                         probs[0] * 100, probs[1] * 100, probs[2] * 100)
    conn.commit()
    log.info("Нови прогнози: %d (пропуснати заради малко данни: %d, вече започнали: %d)",
             logged, skipped, late)
    return logged


def settle(conn=None):
    """Сверява старите прогнози с резултата. Прогнозата не се пипа, само изходът."""
    conn = conn or db.init()
    rows = conn.execute(
        """SELECT p.id, p.match_id, m.fthg, m.ftag FROM predictions p
             JOIN matches m ON m.id = p.match_id
            WHERE p.outcome IS NULL AND m.fthg IS NOT NULL""").fetchall()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for row in rows:
        outcome = 0 if row["fthg"] > row["ftag"] else (1 if row["fthg"] == row["ftag"] else 2)
        conn.execute("UPDATE predictions SET outcome = ?, settled_at = ? WHERE id = ?",
                     (outcome, now, row["id"]))
    conn.commit()
    waiting = conn.execute(
        "SELECT COUNT(*) c FROM predictions WHERE outcome IS NULL").fetchone()["c"]
    log.info("Уредени %d прогнози, чакат резултат %d", len(rows), waiting)
    return len(rows)


def record(conn):
    """Моделът срещу пазара, само по прогнози с резултат."""
    from .market import implied_row
    rows = conn.execute(
        """SELECT p_home, p_draw, p_away, odds_home, odds_draw, odds_away, outcome
             FROM predictions WHERE outcome IS NOT NULL""").fetchall()
    model_total, market_total, n = 0.0, 0.0, 0
    for row in rows:
        odds = [row["odds_home"], row["odds_draw"], row["odds_away"]]
        if any(o is None for o in odds):
            continue
        fair = implied_row(odds)
        if fair is None:
            continue
        n += 1
        model_total += model.brier([row["p_home"], row["p_draw"], row["p_away"]], row["outcome"])
        market_total += model.brier(fair, row["outcome"])
    return {"n": n,
            "brier_model": model_total / n if n else None,
            "brier_market": market_total / n if n else None}
