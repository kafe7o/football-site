"""
Прогнозите на живо: записват се в деня на мача, ПРЕДИ началото, и после се сверяват.

Кога се записват: първото пускане след 07:00 българско време записва всички мачове до 07:00
на следващия ден („денят“ включва и нощните мачове в Америка). Мач, който се появи по-късно
през деня, се записва при следващото пускане, ако още не е започнал. Записът не се променя -
иначе статистиката после е нагласена (правилото от CLAUDE.md).

Прогнозата за следващите дни на сайта е ПРЕДВАРИТЕЛНА: смята се наново при всяко пускане и
не влиза в статистиката.

Резултатите: първо от историята (football-data, OpenLigaDB - безплатно), иначе от odds API
(2 кредита на лига, най-много веднъж на 4 часа на лига). Официалният резултат от
football-data презаписва този от API, ако се различават.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from . import analysis, db, derbies, model, odds_api, prices as P, results, robot, xg
from .leagues import LEAGUES

log = logging.getLogger(__name__)

SOFIA = ZoneInfo("Europe/Sofia")
DAY_STARTS = 7                       # часът, в който започва „денят“ за прогнозите
LOCK_MARGIN = timedelta(minutes=5)
FINISHED_AFTER = timedelta(hours=2, minutes=30)
SCORES_EVERY = timedelta(hours=4)
TOTO = robot.TOTO_LEAGUES            # заключени в кода (bets/robot.py)


# ---------- моделът: веднъж на ден за лига ----------

def fitted_model(conn, code, today=None):
    """Обученият модел на лигата за днес - от кеша, или се обучава сега. None, ако няма история."""
    lg = LEAGUES.get(code)
    if not lg or not lg.has_history:
        return None
    today = (today or datetime.now(timezone.utc)).date().isoformat()
    row = conn.execute("SELECT day, params_json FROM model_cache WHERE league = ?", (code,)).fetchone()
    if row and row["day"] == today:
        return model.Poisson.from_export(json.loads(row["params_json"]))
    since = results.history_window_start()
    hist = results.history(conn, code, blend_xg=code in xg.LEAGUES, since=since)
    if len(hist) < model.MIN_TRAIN_MATCHES:
        log.warning("%s: само %d мача в прозореца - без модел", code, len(hist))
        return None
    fitted = model.Poisson().fit(hist, as_of=pd.Timestamp(today))
    conn.execute("INSERT INTO model_cache (league, day, params_json) VALUES (?, ?, ?) "
                 "ON CONFLICT(league) DO UPDATE SET day = excluded.day, params_json = excluded.params_json",
                 (code, today, json.dumps(fitted.export())))
    conn.commit()
    log.info("%s: моделът е обучен на %d мача", code, len(hist))
    return fitted


# Новаците (2026-10-01, research/pyramid_backtest.py): моделът на лигата няма оценка за отбор с под 10
# претеглени мача в нея - новак в Бундеслигата с 5 мача. РЕЗЕРВЕН модел се учи на лигата И съседната
# дивизия заедно (новакът носи силата си отдолу) и се ползва САМО когато основният няма оценка.
# Назад (8 229 мача, топ 5 + E1, D2): покритие 89% -> 99%; при новаците (812 мача) Brier 0.6130 срещу
# 0.6911 „без модел“; при останалите - основният, без промяна.
PYRAMID = {"E0": ["E0", "E1"], "SP1": ["SP1", "SP2"], "I1": ["I1", "I2"], "D1": ["D1", "D2"], "F1": ["F1", "F2"],
           "E1": ["E0", "E1", "E2"], "D2": ["D1", "D2", "D3"]}


def fitted_pyramid(conn, code, today=None):
    """Резервният модел (лигата + съседната дивизия) за днес - от кеша (ключ "<лига>|pyr") или сега."""
    if code not in PYRAMID:
        return None
    today = (today or datetime.now(timezone.utc)).date().isoformat()
    key = f"{code}|pyr"
    row = conn.execute("SELECT day, params_json FROM model_cache WHERE league = ?", (key,)).fetchone()
    if row and row["day"] == today:
        return model.Poisson.from_export(json.loads(row["params_json"]))
    since = results.history_window_start()
    parts = [results.history(conn, c, blend_xg=c in xg.LEAGUES, since=since) for c in PYRAMID[code]]
    hist = pd.concat([p for p in parts if not p.empty], ignore_index=True).sort_values("date").reset_index(drop=True)
    if len(hist) < model.MIN_TRAIN_MATCHES:
        return None
    fitted = model.Poisson().fit(hist, as_of=pd.Timestamp(today))
    conn.execute("INSERT INTO model_cache (league, day, params_json) VALUES (?, ?, ?) "
                 "ON CONFLICT(league) DO UPDATE SET day = excluded.day, params_json = excluded.params_json",
                 (key, today, json.dumps(fitted.export())))
    conn.commit()
    log.info("%s: резервният модел (%s) е обучен на %d мача", code, "+".join(PYRAMID[code]), len(hist))
    return fitted


def own_or_pyramid(conn, fx, fitted, now=None):
    """(шансовете на робота, моделът) - основният модел, а ако няма оценка - резервният. (None, None)."""
    if fitted is not None and fx["mapped"]:
        p = fitted.markets(fx["home"], fx["away"])
        if p:
            return p, fitted
    if fx["mapped"] and fx["league"] in PYRAMID:
        pyr = fitted_pyramid(conn, fx["league"], now)
        if pyr is not None:
            p = pyr.markets(fx["home"], fx["away"])
            if p:
                return p, pyr
    return None, None


# ---------- прогноза за един мач ----------

def league_dates(conn, code):
    return [r[0] for r in conn.execute("SELECT DISTINCT date FROM matches WHERE league = ?", (code,))] + \
           [r[0][:10] for r in conn.execute("SELECT kickoff FROM fixtures WHERE league = ?", (code,))]


def referee_of(conn, fx):
    if not fx["match_id"]:
        return None
    row = conn.execute("SELECT referee FROM matches WHERE id = ?", (fx["match_id"],)).fetchone()
    return row[0] if row else None


def match_analysis(ctx, fx, fitted, flags):
    """Подробният анализ (bets/analysis.py) и изборът за картони/корнери за записа."""
    if ctx is None:
        return None, {}
    names = (fx["home_src"] or fx["home"], fx["away_src"] or fx["away"])
    an = analysis.build(ctx, {**dict(fx), "flags": flags}, fitted, names, referee_of(ctx.conn, fx))
    extras = {k: {x: an[k][x] for x in ("pick", "line", "over", "total", "home", "away")} for k in ("cards", "corners")
              if an and an.get(k)}
    return an, extras


def forecast(conn, fx, fitted, dates_cache, ctx=None):
    """Прогнозата на робота за мача: вероятности, изборът по пазари, главният съвет, флагове."""
    pr = P.for_fixture(conn, fx)
    market = P.fair(pr)
    robot_p, used = own_or_pyramid(conn, fx, fitted)
    basis = "model" if robot_p else "market"
    if used is not None:
        fitted = used                      # анализът (очаквани голове) - от модела, дал прогнозата
    if robot_p is None:
        robot_p = market
    if robot_p is None:
        return None
    day = datetime.fromisoformat(fx["kickoff"]).astimezone(ZoneInfo("Europe/London")).date().isoformat()
    if fx["league"] not in dates_cache:
        dates_cache[fx["league"]] = league_dates(conn, fx["league"])
    flags = {"derby": derbies.is_derby(fx["league"], fx["home"], fx["away"]),
             "after_break": LEAGUES[fx["league"]].has_history and robot.after_break(dates_cache[fx["league"]], day),
             "toto": fx["league"] in TOTO}
    an, extras = match_analysis(ctx, fx, fitted, flags)
    risky = safer = one = None
    avg = (pr or {}).get("avg") or {}
    if basis == "model":
        # професионалистът (30.09 вечерта): без коефициенти и без фаворити; рискова (знак) и по-сигурна
        # професионалистът (30.09 късно): сигурна 1.40-1.80, рискова над 1.80 - по шанса на робота
        base = ctx.base(fx["league"]) if ctx else robot.base_rates(conn, fx["league"])
        safer = robot.safe_by_odds(robot_p, avg)
        # професионалистът (01.10): ЕДНА прогноза за мача - нея мерим (главният съвет в tips.tip)
        one = robot.one_pick(robot_p, avg, extras, fx["league"])
        # професионалистът (02.10): рисковата не противоречи на по-сигурната и на едната прогноза
        risky = robot.risky_by_odds(robot_p, base, avg, [x["sel"] for x in (safer, one) if x])
        sel = one["sel"] if one else None
        odds = one["odds"] if one and one["src"] == "book" else None
        why = ("дерби - професионалистът: избягвай за залог" if flags.get("derby")
               else None if one else "няма очаквани голове за тези отбори")
    else:
        # професионалистът: процентът да не идва от коефициентите - без модел прогноза на робота няма
        sel, odds, why = None, None, "роботът няма собствена оценка за тези отбори (няма история) - показан е само пазарът"
    best = (pr or {}).get("best", {}).get(sel) if sel else None
    return {"basis": basis, "analysis": an, "extras": extras, "rule": robot.RULE_ONE,
            "risky": risky, "safer": safer, "one": one,
            "probs": {"robot": {k: round(v, 4) for k, v in robot_p.items()},
                      "market": {k: round(v, 4) for k, v in (market or {}).items()}},
            "prices": pr,
            "picks": {"robot": robot.picks(robot_p), "market": robot.picks(market) if market else None},
            "tip": sel, "tip_odds": odds, "tip_best": best, "why": why, "flags": flags}


# ---------- запис в деня на мача ----------

def day_end(now):
    """Краят на текущия „ден“ за прогнозите: следващото 07:00 българско време."""
    local = now.astimezone(SOFIA)
    end = local.replace(hour=DAY_STARTS, minute=0, second=0, microsecond=0)
    if local >= end:
        end += timedelta(days=1)
    return end.astimezone(timezone.utc)


def lock(conn, now=None):
    now = now or datetime.now(timezone.utc)
    end = day_end(now)
    rows = conn.execute(
        """SELECT * FROM fixtures WHERE kickoff > ? AND kickoff <= ?
             AND id NOT IN (SELECT fixture_id FROM tips) ORDER BY kickoff""",
        ((now + LOCK_MARGIN).isoformat(), end.isoformat())).fetchall()
    stamp = now.isoformat(timespec="seconds")
    models, dates, locked, skipped = {}, {}, 0, 0
    ctx = analysis.Context(conn, now)
    for fx in rows:
        if fx["league"] not in models:
            models[fx["league"]] = fitted_model(conn, fx["league"], now)
        f = forecast(conn, fx, models[fx["league"]], dates, ctx)
        if f is None:
            skipped += 1
            continue
        conn.execute(
            """INSERT OR IGNORE INTO tips (fixture_id, league, kickoff, home, away, locked_at, basis, probs_json,
                   prices_json, picks_json, tip, tip_odds, tip_best, flags_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fx["id"], fx["league"], fx["kickoff"], fx["home"], fx["away"], stamp, f["basis"],
             json.dumps(f["probs"]), json.dumps(f["prices"]) if f["prices"] else None,
             json.dumps({**f["picks"], "extras": f["extras"], "risky": f["risky"], "safer": f["safer"], "one": f["one"]}),
             f["tip"], f["tip_odds"], f["tip_best"],
             json.dumps({**f["flags"], "why": f["why"], "rule": robot.RULE_ONE, "referee": referee_of(conn, fx)})))
        locked += 1
    conn.commit()
    log.info("Записани прогнози: %d (без цени и без модел: %d), денят свършва %s", locked, skipped,
             end.astimezone(SOFIA).strftime("%d.%m %H:%M"))
    return locked


def preview(conn, now=None, days=14):
    """Предварителната прогноза за всички предстоящи мачове (за сайта). Записаните се четат от tips."""
    now = now or datetime.now(timezone.utc)
    rows = conn.execute("SELECT * FROM fixtures WHERE kickoff > ? AND kickoff <= ? ORDER BY kickoff",
                        (now.isoformat(), (now + timedelta(days=days)).isoformat())).fetchall()
    models, dates, out = {}, {}, []
    ctx = analysis.Context(conn, now)
    locked = {r["fixture_id"]: r for r in conn.execute("SELECT * FROM tips WHERE kickoff > ?", (now.isoformat(),))}
    for fx in rows:
        if fx["league"] not in models:
            models[fx["league"]] = fitted_model(conn, fx["league"], now)
        if fx["id"] in locked:
            out.append(locked_entry(conn, ctx, fx, locked[fx["id"]], models[fx["league"]]))
            continue
        f = forecast(conn, fx, models[fx["league"]], dates, ctx)
        if f is None:
            out.append({"id": fx["id"], "league": fx["league"], "kickoff": fx["kickoff"], "home": fx["home"],
                        "away": fx["away"], "home_src": fx["home_src"], "away_src": fx["away_src"], "locked": None,
                        "basis": None, "why": "няма цени и няма модел за тези отбори"})
            continue
        out.append({"id": fx["id"], "league": fx["league"], "kickoff": fx["kickoff"], "home": fx["home"],
                    "away": fx["away"], "home_src": fx["home_src"], "away_src": fx["away_src"], "locked": None, **f})
    return out


def locked_entry(conn, ctx, fx, t, fitted):
    """Записаната прогноза (ред от tips) във вида на preview. fx може да липсва - тогава без анализ."""
    flags = json.loads(t["flags_json"] or "{}")
    why = flags.pop("why", None)
    rule = robot.rule_of(flags, t["locked_at"])
    flags.pop("rule", None)
    flags.pop("referee", None)
    picks = json.loads(t["picks_json"])
    an = None
    if fx is not None:
        if t["basis"] == "model":
            fitted = own_or_pyramid(conn, fx, fitted)[1] or fitted     # новак - анализът от резервния модел
        an, _ = match_analysis(ctx, fx, fitted, flags)
    return {"id": t["fixture_id"], "league": t["league"], "kickoff": t["kickoff"], "home": t["home"],
            "away": t["away"], "home_src": fx["home_src"] if fx else None, "away_src": fx["away_src"] if fx else None,
            "locked": t["locked_at"], "basis": t["basis"], "probs": json.loads(t["probs_json"]),
            "prices": json.loads(t["prices_json"]) if t["prices_json"] else None,
            "picks": picks, "extras": picks.get("extras") or {}, "analysis": an,
            "risky": picks.get("risky"), "safer": picks.get("safer"), "one": picks.get("one"),
            "tip": t["tip"], "tip_odds": t["tip_odds"], "rule": rule,
            "tip_best": t["tip_best"], "why": why, "flags": flags}


def started(conn, now=None):
    """Записаните прогнози за започнали мачове без резултат - „Чакат резултат“ на сайта, със
    всичко, което мачът имаше в „Прогнози“ (собственикът, 2026-10-01)."""
    now = now or datetime.now(timezone.utc)
    ctx = analysis.Context(conn, now)
    models, out = {}, []
    for t in conn.execute("SELECT * FROM tips WHERE hg IS NULL AND kickoff <= ? ORDER BY kickoff",
                          (now.isoformat(),)).fetchall():
        fx = conn.execute("SELECT * FROM fixtures WHERE id = ?", (t["fixture_id"],)).fetchone()
        if fx is None:
            log.warning("Записана прогноза без мача в разписанието (без анализ): %s %s - %s",
                        t["league"], t["home"], t["away"])
        elif fx["league"] not in models:
            models[fx["league"]] = fitted_model(conn, fx["league"], now)
        out.append(locked_entry(conn, ctx, fx, t, models.get(t["league"])))
    return out


# ---------- уреждане ----------

def settle(conn, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    done = corrected = 0
    # 0. бързите резултати на наблюдателя (bets/live.py, results/*.json) - без кредити
    from . import live
    done += live.settle_from_files(conn, now)
    # 1. от историята - безплатно; презаписва и резултата от API, ако се различава
    for t in conn.execute(
            """SELECT t.fixture_id, t.league, t.kickoff, t.home, t.away, t.hg, t.ag, t.result_src, f.match_id
                 FROM tips t LEFT JOIN fixtures f ON f.id = t.fixture_id
                WHERE t.kickoff < ? AND (t.hg IS NULL OR t.result_src IN ('odds-api', 'espn'))""",
            ((now - FINISHED_AFTER).isoformat(),)).fetchall():
        m = None
        if t["match_id"]:
            m = conn.execute("SELECT id, fthg, ftag FROM matches WHERE id = ? AND fthg IS NOT NULL", (t["match_id"],)).fetchone()
        if m is None:
            day = datetime.fromisoformat(t["kickoff"]).astimezone(ZoneInfo("Europe/London")).date()
            m = conn.execute(
                """SELECT id, fthg, ftag FROM matches WHERE league = ? AND home_team = ? AND away_team = ?
                     AND date BETWEEN ? AND ? AND fthg IS NOT NULL""",
                (t["league"], t["home"], t["away"], (day - timedelta(days=1)).isoformat(),
                 (day + timedelta(days=1)).isoformat())).fetchone()
        if m is None:
            continue
        if t["hg"] is not None and (t["hg"], t["ag"]) != (m["fthg"], m["ftag"]):
            log.warning("%s - %s: резултатът е поправен %s:%s -> %s:%s", t["home"], t["away"], t["hg"], t["ag"],
                        m["fthg"], m["ftag"])
            corrected += 1
        elif t["hg"] is None:
            done += 1
        conn.execute("UPDATE tips SET hg = ?, ag = ?, settled_at = ?, result_src = 'history', match_id = ? "
                     "WHERE fixture_id = ?", (m["fthg"], m["ftag"], stamp, m["id"], t["fixture_id"]))
    conn.commit()
    # 2. от odds API - за мачовете от odds API, които историята още няма
    waiting = conn.execute(
        """SELECT t.fixture_id, t.league FROM tips t JOIN fixtures f ON f.id = t.fixture_id
            WHERE t.hg IS NULL AND f.source = 'api' AND t.kickoff < ? AND t.kickoff > ?""",
        ((now - FINISHED_AFTER).isoformat(), (now - timedelta(days=3)).isoformat())).fetchall()
    by_sport = {}
    for w in waiting:
        by_sport.setdefault(LEAGUES[w["league"]].sport, set()).add(w["fixture_id"])
    asked = 0
    for sport, ids in by_sport.items():
        last = db.get_meta(conn, f"scores_at:{sport}")
        if last and now - datetime.fromisoformat(last) < SCORES_EVERY:
            continue
        try:
            events = odds_api.finished(sport, days_from=3, cache_minutes=0)
        except RuntimeError as e:
            log.error("%s: резултатите не се изтеглиха - %s", sport, e)
            continue
        asked += 1
        db.set_meta(conn, f"scores_at:{sport}", now.isoformat(timespec="seconds"))
        for e in events:
            if e["id"] in ids:
                conn.execute("UPDATE tips SET hg = ?, ag = ?, settled_at = ?, result_src = 'odds-api' "
                             "WHERE fixture_id = ? AND hg IS NULL", (int(e["home"]), int(e["away"]), stamp, e["id"]))
                done += 1
        conn.commit()
    remaining = odds_api.last_remaining()
    if remaining is not None:
        db.set_meta(conn, "credits_remaining", remaining)
    log.info("Уредени прогнози: %d, поправени: %d, заявки за резултати: %d", done, corrected, asked)
    return done
