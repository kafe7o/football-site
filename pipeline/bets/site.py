"""
Сайтът: един самостоятелен HTML файл, който се отваря навсякъде без нищо инсталирано.

Съдържа три неща, в този ред на важност:
  1. залозите, намерени по цена, и дали пазарът се движи към нас;
  2. измереното - Brier на модела срещу пазара, на живо и в бектеста;
  3. мачовете ден по ден с прогнозата и резултата.

Нищо тук не решава - само чете базата и подрежда. Правилата са в model.py и value.py.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from . import config, db, decide, derbies, model, predict, results, review, teams, value
from .market import implied_row
from zoneinfo import ZoneInfo

SOFIA = ZoneInfo("Europe/Sofia")   # облакът е в UTC - часовете се показват в българско
UK = ZoneInfo("Europe/London")     # датите на football-data са британски

log = logging.getLogger(__name__)

TEMPLATE = config.ROOT / "site_template.html"
OUT = config.SITE_DIR / "index.html"
DAYS_BACK, DAYS_FORWARD = 10, 8


def local(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(SOFIA)


def day_matches(conn, since, until):
    rows = conn.execute(
        """SELECT m.id, m.league, m.date, m.kickoff, m.home_team, m.away_team, m.fthg, m.ftag,
                  p.p_home, p.p_draw, p.p_away, p.odds_home, p.odds_draw, p.odds_away,
                  p.predicted_at
             FROM matches m
             LEFT JOIN predictions p ON p.match_id = m.id
            WHERE m.date BETWEEN ? AND ?
            ORDER BY m.date, m.kickoff""", (since, until)).fetchall()
    out = []
    for r in rows:
        odds = [r["odds_home"], r["odds_draw"], r["odds_away"]]
        market = implied_row(odds) if all(o is not None for o in odds) else None
        played = r["fthg"] is not None
        out.append({
            "id": r["id"], "date": r["date"], "time": (r["kickoff"] or "")[:5],
            "derby": derbies.is_derby(r["league"], r["home_team"], r["away_team"]),
            "league": results.LEAGUES.get(r["league"], r["league"]),
            "home": r["home_team"], "away": r["away_team"],
            "model": [r["p_home"], r["p_draw"], r["p_away"]] if r["p_home"] is not None else None,
            "market": market, "odds": odds if any(odds) else None,
            "score": [r["fthg"], r["ftag"]] if played else None,
            "outcome": (0 if r["fthg"] > r["ftag"] else (1 if r["fthg"] == r["ftag"] else 2))
                       if played else None,
            "predicted_at": (r["predicted_at"] or "")[:16].replace("T", " ") or None,
        })
        out[-1]["signal"] = signal(out[-1]["model"], odds, r["home_team"], r["away_team"])
    return out


VALUE_THRESHOLD = 0.05


def signal(probs, odds, home, away):
    """Правилото за сигнал по модела (решение на собственика, виж CLAUDE.md): изходът с
    най-голяма стойност p*коефициент-1, ако е над 5%. Измерено е, че губи - затова до него
    на сайта винаги стои измереният ROI."""
    if probs is None or not odds or any(o is None for o in odds):
        return None
    values = [p * o - 1 for p, o in zip(probs, odds)]
    k = max(range(len(values)), key=lambda i: values[i])
    if values[k] <= VALUE_THRESHOLD:
        return None
    return {"pick": k, "name": (home, "Равен", away)[k], "odds": odds[k],
            "p": probs[k], "value": values[k]}


def signal_backtest():
    path = config.RESULTS_DIR / "signal_backtest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def daily_pnl(conn, stake=10):
    """Резултатът ден по ден, за да не се смята на ръка - и за да няма спор кой мач влиза.

    Броят се ВСИЧКИ прогнози за деня, не само познатите. Изборът кой мач влиза е направен
    преди мача (записан е с час), а не след като резултатите са известни.
    """
    rows = conn.execute(
        """SELECT match_date, home_team, away_team, p_home, p_draw, p_away,
                  odds_home, odds_draw, odds_away, outcome
             FROM predictions WHERE outcome IS NOT NULL ORDER BY match_date""").fetchall()
    days = {}
    for r in rows:
        probs = [r["p_home"], r["p_draw"], r["p_away"]]
        odds = [r["odds_home"], r["odds_draw"], r["odds_away"]]
        if any(o is None for o in odds):
            continue
        day = days.setdefault(r["match_date"][:10],
                              {"date": r["match_date"][:10], "n": 0, "wins": 0,
                               "model": 0.0, "signal": 0.0, "sig_n": 0})
        pick = max(range(3), key=lambda i: probs[i])
        won = pick == r["outcome"]
        day["n"] += 1
        day["wins"] += int(won)
        day["model"] += stake * (odds[pick] - 1) if won else -stake
        sig = signal(probs, odds, r["home_team"], r["away_team"])
        if sig:
            day["sig_n"] += 1
            day["signal"] += (stake * (sig["odds"] - 1)
                              if sig["pick"] == r["outcome"] else -stake)
    return {"stake": stake, "days": sorted(days.values(), key=lambda d: d["date"]),
            "total_model": sum(d["model"] for d in days.values()),
            "total_signal": sum(d["signal"] for d in days.values())}


def open_value_db(conn):
    """Залозите по цена са в книгата на облака (site/cloud.db). На лаптопа сайтът чете
    оттам, в облака - това е самата му база. Връща (връзка, трябва_ли_да_се_затвори).

    ВАЖНО: връзката се затваря в края на build(). На Windows отворен файл не може да се
    подмени, а publish.py подменя cloud.db с версията на облака - незатворена връзка
    чупеше качването (2026-09-25).
    """
    cloud = config.SITE_DIR / "cloud.db"
    if cloud.exists() and cloud.resolve() != config.DB_PATH.resolve():
        return db.init(cloud), True
    return conn, False


def value_section(conn, limit=50):
    rows = conn.execute(
        """SELECT * FROM value_bets WHERE result IS NULL AND commence_time > ?
            ORDER BY commence_time, edge DESC LIMIT ?""",
        (datetime.now().astimezone().isoformat(), limit)).fetchall()
    upcoming = []
    for r in rows:
        start = local(r["commence_time"])
        upcoming.append({"date": start.date().isoformat(), "time": start.strftime("%H:%M"),
                         "home": r["home_team"], "away": r["away_team"],
                         "selection": r["selection"], "book": r["bookmaker"],
                         "odds": r["odds"], "sharp": r["sharp_book"],
                         "p_fair": r["p_fair"], "edge": r["edge"],
                         "tier": value.tier(r["sharp_book"], r["edge"], r["n_books"])})
    order = {"A": 0, "B": 1, "C": 2}
    upcoming.sort(key=lambda b: (order[b["tier"]], -b["edge"]))
    return {"upcoming": upcoming, "record": value.record(conn),
            "by_tier": value.record_by_tier(conn)}


# Къде са букмейкърите от odds API. НИТО ЕДИН от тях не е лицензиран в България (НАП):
# efbet и winbet ги няма в никое API. Цените са истински и служат за сравнение.
BOOK_LINKS = {
    "onexbet": ("1xBet", "https://1xbet.com"),
    "leovegas_se": ("LeoVegas", "https://www.leovegas.com"),
    "leovegas": ("LeoVegas", "https://www.leovegas.com"),
    "nordicbet": ("NordicBet", "https://www.nordicbet.com"),
    "betsson": ("Betsson", "https://www.betsson.com"),
    "unibet_se": ("Unibet", "https://www.unibet.com"),
    "unibet_nl": ("Unibet", "https://www.unibet.com"),
    "unibet_fr": ("Unibet", "https://www.unibet.com"),
    "unibet_uk": ("Unibet", "https://www.unibet.com"),
    "williamhill": ("William Hill", "https://www.williamhill.com"),
    "marathonbet": ("Marathonbet", "https://www.marathonbet.com"),
    "coolbet": ("Coolbet", "https://www.coolbet.com"),
    "sport888": ("888sport", "https://www.888sport.com"),
    "betclic_fr": ("Betclic", "https://www.betclic.fr"),
    "tipico_de": ("Tipico", "https://www.tipico.de"),
    "winamax_fr": ("Winamax", "https://www.winamax.fr"),
    "winamax_de": ("Winamax", "https://www.winamax.de"),
    "pinnacle": ("Pinnacle", "https://www.pinnacle.com"),
    "betfair_ex_eu": ("Betfair", "https://www.betfair.com"),
    "betano_uk": ("Betano", "https://www.betano.com"),
    "betway": ("Betway", "https://www.betway.com"),
    "paddypower": ("Paddy Power", "https://www.paddypower.com"),
    "codere_it": ("Codere", "https://www.codere.it"),
    "everygame": ("Everygame", "https://www.everygame.eu"),
}
SOON_HOURS = 3      # "Започват скоро": мачовете в следващите три часа

MIN_EDGE_SHEET = 0.02
CHANGE_THRESHOLD = 0.02     # под 2 процентни пункта е шум от преобучаването, не новина
PREVIEW_DAYS = 30   # таблото показва 3 дни напред, но пази повече,
                    # за да има какво да се види и по време на пауза за националните отбори


# Лигите в odds API -> кодът им в базата, за да се намери историята за модела.
SPORT_TO_LEAGUE = {
    "soccer_epl": "E0", "soccer_efl_champ": "E1", "soccer_england_league1": "E2",
    "soccer_england_league2": "E3", "soccer_spain_la_liga": "SP1",
    "soccer_spain_segunda_division": "SP2", "soccer_italy_serie_a": "I1",
    "soccer_italy_serie_b": "I2", "soccer_germany_bundesliga": "D1",
    "soccer_germany_bundesliga2": "D2", "soccer_france_ligue_one": "F1",
    "soccer_france_ligue_two": "F2", "soccer_netherlands_eredivisie": "N1",
    "soccer_belgium_first_div": "B1", "soccer_portugal_primeira_liga": "P1",
    "soccer_turkey_super_league": "T1", "soccer_greece_super_league": "G1",
    "soccer_spl": "SC0",
}


def fitted_models(conn, exported=None):
    """Моделите по лиги: от базата (на лаптопа) или от снимката (в облака)."""
    if exported:
        return {league: model.Poisson.from_export(data) for league, data in exported.items()}
    models = {}
    for league in results.LEAGUES:
        history = results.history(conn, league)
        if len(history) >= model.MIN_TRAIN_MATCHES:
            models[league] = model.Poisson().fit(history)
    return models


def preview(conn, days=PREVIEW_DAYS, exported=None, events_conn=None):
    """Какво казва моделът за мачовете напред.

    Това НЕ са записаните прогнози: те се правят само в деня на мача, за да ползват
    последните резултати и последните цени (виж predict.py). Тук е поглед напред,
    пресметнат в момента на строенето на сайта.

    Мачовете идват от odds API (той знае разписанието седмици напред), а не от
    football-data, чийто fixtures.csv се обновява два-три дни преди кръга. Имената там са
    различни и минават през teams.match(); при несигурно съвпадение мачът се показва само
    с пазарните проценти и с обяснение защо няма прогноза.
    """
    now = datetime.now().astimezone()
    events = (events_conn or conn).execute(
        """SELECT sport, event_id, home_team, away_team, commence_time,
                  MAX(CASE WHEN outcome_idx = 0 THEN p_fair END) AS p_home,
                  MAX(CASE WHEN outcome_idx = 1 THEN p_fair END) AS p_draw,
                  MAX(CASE WHEN outcome_idx = 2 THEN p_fair END) AS p_away,
                  MAX(CASE WHEN outcome_idx = 0 THEN best_odds END) AS o_home,
                  MAX(CASE WHEN outcome_idx = 1 THEN best_odds END) AS o_draw,
                  MAX(CASE WHEN outcome_idx = 2 THEN best_odds END) AS o_away
             FROM fair_prices
            WHERE substr(commence_time, 1, 10) BETWEEN ? AND ?
                  AND sport LIKE 'soccer_%'
            GROUP BY event_id ORDER BY commence_time""",
        (now.date().isoformat(), (now + timedelta(days=days)).date().isoformat())).fetchall()

    models = fitted_models(conn, exported)
    out = []
    for e in events:
        league = SPORT_TO_LEAGUE.get(e["sport"])
        probs, reason = None, None
        if league is None:
            reason = "лигата не е в базата с история"
        else:
            fitted = models.get(league)
            if fitted is None:
                reason = "малко история за тази лига"
            else:
                home = (teams.match(e["home_team"], fitted.teams)
                        or teams.loose_match(e["home_team"], fitted.teams))
                away = (teams.match(e["away_team"], fitted.teams)
                        or teams.loose_match(e["away_team"], fitted.teams))
                if not home or not away or home == away:
                    reason = "непознат отбор за модела"
                else:
                    probs = fitted.probabilities(home, away)
                    if probs is None:
                        reason = "малко скорошни мачове за някой от отборите"
        start = local(e["commence_time"])
        market = [e["p_home"], e["p_draw"], e["p_away"]]
        out.append({
            "event_id": e["event_id"], "commence_iso": e["commence_time"], "sport": e["sport"],
            "derby": bool(league) and derbies.is_derby(league, e["home_team"], e["away_team"]),
            "date": start.date().isoformat(), "time": start.strftime("%H:%M"),
            "league": results.LEAGUES.get(league, e["sport"].replace("soccer_", "")),
            "home": e["home_team"], "away": e["away_team"],
            "model": list(probs) if probs else None,
            "market": market if all(p is not None for p in market) else None,
            "best_odds": [e["o_home"], e["o_draw"], e["o_away"]],
            "min_odds": [(1 + MIN_EDGE_SHEET) / p for p in probs] if probs else None,
            "reason": reason,
        })
        best = out[-1]["best_odds"]
        out[-1]["signal"] = (signal(list(probs), best, e["home_team"], e["away_team"])
                             if probs and all(o is not None for o in best) else None)
    log.info("Преглед напред: %d мача, с прогноза %d",
             len(out), sum(1 for m in out if m["model"]))
    return out


def load_types(value_conn):
    """Типът A/B/C на всеки мач сега (type_now) и пътят му във времето (type_log)."""
    out = {}
    for r in value_conn.execute("SELECT * FROM type_now"):
        pick = json.loads(r["pick_json"]) if r["pick_json"] else None
        if pick:
            name, url = BOOK_LINKS.get(pick["bookmaker"], (pick["bookmaker"], None))
            pick.update({"book_name": name, "url": url})
        out[r["event_id"]] = {"type": r["type"], "pick": pick, "path": []}
    for r in value_conn.execute("SELECT event_id, type, recorded_at FROM type_log ORDER BY id"):
        if r["event_id"] in out:
            out[r["event_id"]]["path"].append([r["type"], r["recorded_at"]])
    return out


def match_type(bets, scanned):
    """Типът по всички намерени цени - за мачове отпреди записа на типа (type_now)."""
    tiers = {value.tier(b["sharp_book"], b["edge"], b.get("n_books"))
             for b in bets if b["bookmaker"] not in value.EXCHANGES}
    return next((t for t in "ABC" if t in tiers), "-" if scanned else None)


def match_types(match_conn, value_conn, since, until):
    """Тип A/B/C за мачовете от базата - какъв е СЕГА, а за изиграните: какъв е бил при
    последното сканиране преди началото. "-" значи, че скенерът е гледал мача, но цена над
    честната няма. Мач, който скенерът не е гледал, липсва в резултата.

    Мачовете от football-data и събитията от odds API имат различни имена - свързват се
    с teams.match_fixture (домакин + гост, същата лига и ден). Ключът е id на мача в
    match_conn: на лаптопа таблото е от football.db, историята - от cloud.db.
    """
    lo = (datetime.fromisoformat(since) - timedelta(days=1)).date().isoformat()
    hi = (datetime.fromisoformat(until) + timedelta(days=1)).date().isoformat()
    events = {}
    for table in ("type_now", "fair_prices", "value_bets"):
        for r in value_conn.execute(
                f"""SELECT DISTINCT sport, event_id, home_team, away_team, commence_time FROM {table}
                     WHERE sport LIKE 'soccer_%' AND substr(commence_time, 1, 10) BETWEEN ? AND ?""",
                (lo, hi)):
            events.setdefault(r["event_id"], dict(r))
    bets = {}
    for b in value_conn.execute(
            """SELECT * FROM value_bets
                WHERE sport LIKE 'soccer_%' AND substr(commence_time, 1, 10) BETWEEN ? AND ?""",
            (lo, hi)):
        bets.setdefault(b["event_id"], []).append(dict(b))
    fixtures = {}
    for m in match_conn.execute(
            """SELECT id, league, date, home_team, away_team, fthg, ftag FROM matches
                WHERE date BETWEEN ? AND ?""", (since, until)):
        fixtures.setdefault((m["league"], m["date"]), []).append(dict(m))
    types = load_types(value_conn)
    decisions = decide.load(value_conn)
    out = {}
    for e in events.values():
        league = SPORT_TO_LEAGUE.get(e["sport"])
        if league is None:
            continue
        day = local(e["commence_time"]).astimezone(UK).date().isoformat()
        fixture = teams.match_fixture(e["home_team"], e["away_team"], fixtures.get((league, day), []))
        if fixture is None:
            continue
        found = bets.get(e["event_id"], [])
        now = types.get(e["event_id"])
        made = decisions.get(e["event_id"])
        if made:
            # Решението час преди мача е окончателното - и за историята, и за банката.
            kind, pick = made["type"], made["pick"]
            path = [t for t, _ in now["path"]] if now else []
            if pick:
                name, url = BOOK_LINKS.get(pick["bookmaker"], (pick["bookmaker"], None))
                pick = {**pick, "book_name": name, "url": url}
        elif now:
            kind, pick, path = now["type"], now["pick"], [t for t, _ in now["path"]]
        else:
            kind, pick, path = match_type(found, True), value.pick_for_match(found), []
            if pick:
                name, url = BOOK_LINKS.get(pick["bookmaker"], (pick["bookmaker"], None))
                pick.update({"book_name": name, "url": url})
        if pick:
            # Спечелен ли е: от уреждането на залога, иначе от резултата на мача в базата.
            bet = next((b for b in found if b["selection"] == pick["selection"]
                        and b["bookmaker"] == pick["bookmaker"]), None)
            result = bet["result"] if bet else None
            if result is None and fixture["fthg"] is not None and pick.get("outcome_idx") is not None:
                outcome = (0 if fixture["fthg"] > fixture["ftag"]
                           else 1 if fixture["fthg"] == fixture["ftag"] else 2)
                result = int(outcome == pick["outcome_idx"])
            pick = {**pick, "result": result}
        out[fixture["id"]] = {"type": kind, "pick": pick, "path": path,
                              "decision": [made["bet"], made["reason"], made["time"]] if made else None}
    return out


def attach_bets(conn, rows):
    """Закача към всеки мач залозите по цена, всички цени по букмейкър и типа A/B/C."""
    types = load_types(conn)
    for m in rows:
        if not m.get("event_id"):
            continue
        outcomes = conn.execute(
            """SELECT outcome_idx, selection, prices_json, updated_at FROM fair_prices
                WHERE event_id = ? ORDER BY outcome_idx""", (m["event_id"],)).fetchall()
        m["books"] = []
        for o in outcomes:
            prices = json.loads(o["prices_json"]) if o["prices_json"] else {}
            # Един букмейкър с няколко национални сайта (unibet_se, unibet_nl) - веднъж,
            # с по-добрата цена. Цените вече идват подредени от най-добрата.
            seen, listed = set(), []
            for b, odds in prices.items():
                name, url = BOOK_LINKS.get(b, (b, None))
                if name in seen:
                    continue
                seen.add(name)
                listed.append({"book": b, "name": name, "url": url, "odds": odds})
                if len(listed) == 6:
                    break
            m["books"].append({"selection": o["selection"], "prices": listed,
                               "updated": o["updated_at"]})
        found = conn.execute(
            """SELECT selection, bookmaker, odds, sharp_book, sharp_odds, p_fair, edge,
                      n_books, found_at, closing_odds, result, profit
                 FROM value_bets WHERE event_id = ? ORDER BY edge DESC""",
            (m["event_id"],)).fetchall()
        m["bets"] = [dict(b) for b in found]
        pick = value.pick_for_match(m["bets"])
        if pick:
            name, url = BOOK_LINKS.get(pick["bookmaker"], (pick["bookmaker"], None))
            pick.update({"book_name": name, "url": url})
        m["pick"] = pick
        m["type"] = match_type(m["bets"], bool(outcomes))
        m["type_path"] = []
        now = types.get(m["event_id"])
        if now:
            # Сегашното състояние е от последното сканиране, не от всички намерени някога
            # цени: цена отпреди два дни може вече да я няма.
            m["type"], m["pick"] = now["type"], now["pick"]
            m["type_path"] = now["path"]


def log_forecasts(conn, rows):
    """Записва промените в прогнозата. Нов ред само при осезаема промяна - иначе всяко
    сканиране би добавяло по ред за всеки мач и таблицата щеше да стане безполезна."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = 0
    for m in rows:
        if not m.get("event_id"):
            continue
        last = conn.execute(
            """SELECT p_model_h, p_model_d, p_model_a, p_fair_h, p_fair_d, p_fair_a
                 FROM forecast_log WHERE event_id = ? ORDER BY recorded_at DESC LIMIT 1""",
            (m["event_id"],)).fetchone()
        now_values = (m["model"] or [None] * 3) + (m["market"] or [None] * 3)
        if last is not None:
            old = [last[k] for k in range(6)]
            diffs = [abs(a - b) for a, b in zip(now_values, old) if a is not None and b is not None]
            if diffs and max(diffs) < CHANGE_THRESHOLD:
                continue
        conn.execute(
            """INSERT INTO forecast_log (event_id, home_team, away_team, commence_time,
                   p_model_h, p_model_d, p_model_a, p_fair_h, p_fair_d, p_fair_a, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (m["event_id"], m["home"], m["away"], m["commence_iso"], *now_values, now))
        added += 1
    conn.commit()
    if added:
        log.info("Записани %d промени в прогнозите", added)
    return added


def forecast_changes(conn, rows, min_change=CHANGE_THRESHOLD):
    """Кои мачове са се променили осезаемо от първия запис досега."""
    changed = []
    for m in rows:
        if not m.get("event_id") or not m.get("model"):
            continue
        history = conn.execute(
            """SELECT p_model_h, p_model_d, p_model_a, p_fair_h, p_fair_d, p_fair_a, recorded_at
                 FROM forecast_log WHERE event_id = ? ORDER BY recorded_at""",
            (m["event_id"],)).fetchall()
        if len(history) < 2:
            m["history"] = [dict(h) for h in history]
            continue
        first, last = history[0], history[-1]
        model_shift = max((abs((last[k] or 0) - (first[k] or 0)) for k in range(3)), default=0)
        fair_shift = max((abs((last[k] or 0) - (first[k] or 0)) for k in range(3, 6)), default=0)
        m["history"] = [dict(h) for h in history]
        m["shift"] = {"model": model_shift, "market": fair_shift,
                      "since": first["recorded_at"][:16].replace("T", " ")}
        if max(model_shift, fair_shift) >= min_change:
            changed.append(m)
    return changed


def fair_sheet(conn, limit=200):
    """Справочник: каква цена си струва при твоя букмейкър.

    efbet, winbet и другите български сайтове ги няма в никое API. Затова вместо да ги
    претърсваме (крехко, против условията им и рисково за сметката), даваме числото:
    честната вероятност от острия пазар и минималният коефициент, при който залогът има
    стойност. Отваряш техния сайт, гледаш едно число, сравняваш.
    """
    rows = conn.execute(
        """SELECT * FROM fair_prices WHERE commence_time > ?
            ORDER BY commence_time, outcome_idx LIMIT ?""",
        (datetime.now().astimezone().isoformat(), limit)).fetchall()
    events = {}
    for r in rows:
        start = local(r["commence_time"])
        event = events.setdefault(r["event_id"], {
            "date": start.date().isoformat(), "time": start.strftime("%H:%M"),
            "home": r["home_team"], "away": r["away_team"],
            "sharp": r["sharp_book"], "outcomes": []})
        event["outcomes"].append({
            "name": r["selection"], "p_fair": r["p_fair"],
            "min_odds": (1 + MIN_EDGE_SHEET) / r["p_fair"] if r["p_fair"] > 0 else None,
            "best_odds": r["best_odds"], "best_book": r["best_book"]})
    return list(events.values())


def _sofia_clock(day, uk_clock):
    """Часът от football-data (британско време) в българско, "HH:MM", или None."""
    if not uk_clock or ":" not in uk_clock:
        return None
    try:
        start = datetime.fromisoformat(f"{day}T{uk_clock}").replace(tzinfo=ZoneInfo("Europe/London"))
    except ValueError:
        return None
    return start.astimezone(SOFIA).strftime("%H:%M")


def history_section(vconn, days=190, types=None):
    """Изиграните мачове с прогноза, за таба "История" - САМО истинските записи
    (predictions, направени преди мача). Симулация няма: собственикът поиска историята да
    показва само реални данни (2026-09-28). Мач, чийто резултат още не е дошъл, стои с
    "чака резултат".

    Ред: [дата, лига, домакин, гост, [голове] | None, изход | None, на_живо, модел ‰,
          пазар ‰ | None, коефициенти | None, избор | None, час | None, тип | None,
          пътят на типа ("BA-") | None, дерби (0/1), решението [залог, защо, час] | None]

    Тип: A/B/C от цените (match_types), "-" без цена над честната, None - без данни за
    цените (лигата не е сканирана тогава).

    Часът (българско време) е за банката в таба: мачове с едно и също начало се залагат от
    един и същ баланс - резултатът на единия не се знае, преди другият да е започнал.
    """
    now = datetime.now(timezone.utc)
    since = (now.astimezone(SOFIA).date() - timedelta(days=days)).isoformat()
    live = {}
    for r in vconn.execute(
            """SELECT p.*, m.fthg, m.ftag, m.home_team AS mh, m.away_team AS ma
                 FROM predictions p LEFT JOIN matches m ON m.id = p.match_id
                WHERE p.match_date >= ? ORDER BY p.predicted_at""", (since,)):
        kickoff = datetime.fromisoformat(r["match_date"])
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        if kickoff > now:
            continue                      # още не е започнал - той е в "Прогнози напред"
        local = kickoff.astimezone(SOFIA)
        day = local.date().isoformat()
        live[(r["league"], r["home_team"], r["away_team"], day)] = (r, day, local.strftime("%H:%M"))

    rows = []
    for r, day, clock in live.values():
        odds = [r["odds_home"], r["odds_draw"], r["odds_away"]]
        has_odds = all(o is not None for o in odds)
        # Изборът по цена и типът - същото правило като на предстоящите мачове
        # (value.pick_for_match), върху цените, намерени преди мача.
        kind = (types or {}).get(r["match_id"]) if r["match_id"] else None
        chosen = kind["pick"] if kind else None
        pick = ([chosen["selection"], chosen["odds"], chosen["result"], chosen["tier"],
                 round(chosen["edge"], 4), round(chosen["p_fair"], 4), chosen.get("outcome_idx")]
                if chosen else None)
        settled = r["outcome"] is not None
        rows.append([day, r["league"], r["home_team"], r["away_team"],
                     [r["fthg"], r["ftag"]] if settled and r["fthg"] is not None else None,
                     r["outcome"], 1, [r["p_home"], r["p_draw"], r["p_away"]],
                     implied_row(odds) if has_odds else None, odds if has_odds else None, pick, clock,
                     kind["type"] if kind else None, "".join(kind["path"]) if kind else None,
                     int(derbies.is_derby(r["league"], r["home_team"], r["away_team"])),
                     kind["decision"] if kind else None])

    rows.sort(key=lambda x: (x[0], x[11] or "", x[1], x[2]), reverse=True)

    # Компактно: 6 месеца са ~2000 мача, а сайтът се отваря и от телефон. Лигите отиват
    # в речник, вероятностите - в промили.
    codes = sorted({x[1] for x in rows})
    index = {c: i for i, c in enumerate(codes)}
    per_mille = lambda probs: [round(v * 1000) for v in probs] if probs else None
    for x in rows:
        x[1] = index[x[1]]
        x[7], x[8] = per_mille(x[7]), per_mille(x[8])
        x[9] = [round(o, 2) for o in x[9]] if x[9] else None
    return {"leagues": [results.LEAGUES.get(c, c) for c in codes], "rows": rows}


def pipeline_status():
    path = config.LOG_DIR / "pipeline.log"
    if not path.exists():
        return {"last": None, "text": "няма лог"}
    lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
             if "=====" in ln]
    return {"last": lines[-1][:19] if lines else None,
            "text": lines[-1][20:].strip() if lines else "няма записан цикъл"}


def research_summary():
    """Изводите от бектестовете - те не се преизчисляват всеки ден."""
    path = config.RESULTS_DIR / "research_v3.json"
    price = config.RESULTS_DIR / "price_check.json"
    out = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        best = data["y1x2"]["candidates"]["stack"]["clean_a"]
        out.update({"matches": data["matches"], "brier_best": best["brier"],
                    "brier_market": best["brier_market"],
                    "shrink_weight": max(data["y1x2"]["weights_shrink"].values(), default=None)})
    if price.exists():
        data = json.loads(price.read_text(encoding="utf-8"))
        out["price"] = data["by_price"]
        out["bets"] = data["bets"]
    return out or None


def rewind_summary():
    """Лентата назад (legacy/rule_backtest.py): изиграни мачове с истинските цени отпреди мача
    и модел, обучен само на по-ранните мачове. Не се преизчислява всеки ден."""
    path = config.RESULTS_DIR / "rule_backtest.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"matches": data["matches"], "period": data.get("period"), "monthly": data.get("monthly"),
            "spread": data.get("spread"),
            "rules": {k: {kk: v.get(kk) for kk in ("name", "n", "wins", "roi", "se", "avg_odds")}
                      for k, v in data["rules"].items()},
            "bets": data.get("bets")}


SNAPSHOT = config.SITE_DIR / "snapshot.json"


def write_snapshot(conn):
    """Частите, които искат пълната история: мачовете, прогледът напред и записът на модела.

    Пишат се от лаптопа, защото само там е базата с 94 000 мача. В облака (GitHub Actions)
    те се четат оттук, а цените се смятат наново - така сайтът се обновява на всеки час,
    дори лаптопът да е изключен.
    """
    today = datetime.now().astimezone().date()
    since = (today - timedelta(days=DAYS_BACK)).isoformat()
    until = (today + timedelta(days=DAYS_FORWARD)).isoformat()
    models = fitted_models(conn)
    vconn, owned = open_value_db(conn)
    try:
        preview_rows = preview(conn, events_conn=vconn)
    finally:
        if owned:
            vconn.close()
    data = {"written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "matches": day_matches(conn, since, until),
            "models": {league: fitted.export() for league, fitted in models.items()},
            "preview": preview_rows,
            "record": {**predict.record(conn), "backtest": signal_backtest()},
            "pnl": daily_pnl(conn),
            "research": research_summary()}
    config.SITE_DIR.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    log.info("Снимка за облака: %d мача, %d напред", len(data["matches"]), len(data["preview"]))
    return data


def build(conn=None, from_snapshot=False):
    """from_snapshot=True: в облака - историята идва от snapshot.json, цените се смятат наново."""
    conn = conn or db.init()
    vconn, owned = open_value_db(conn)
    try:
        return _build(conn, vconn, from_snapshot)
    finally:
        if owned:
            vconn.close()


def _build(conn, vconn, from_snapshot):
    today = datetime.now(SOFIA).date()
    window = {(today + timedelta(days=k)).isoformat() for k in range(-DAYS_BACK, DAYS_FORWARD + 1)}

    if from_snapshot and SNAPSHOT.exists():
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        matches = snap["matches"]
        # Прогнозите се смятат НАНОВО от параметрите в снимката: така и мач, който
        # лаптопът никога не е виждал, получава проценти - стига цените му да са дошли.
        preview_rows = (preview(conn, exported=snap["models"], events_conn=vconn)
                        if snap.get("models") else snap["preview"])
        record, research = snap["record"], snap.get("research")
        source = f"история от {snap['written_at'][:16].replace('T', ' ')}, цените са пресни"
    else:
        since = (today - timedelta(days=DAYS_BACK)).isoformat()
        until = (today + timedelta(days=DAYS_FORWARD)).isoformat()
        matches = day_matches(conn, since, until)
        board_types = match_types(conn, vconn, since, until)
        for m in matches:
            kind = board_types.get(m["id"])
            m["type"], m["pick"], m["type_path"] = ((kind["type"], kind["pick"], kind["path"])
                                                    if kind else (None, None, []))
        preview_rows = preview(conn, events_conn=vconn)
        record = {**predict.record(conn), "backtest": signal_backtest()}
        research, source = research_summary(), None
        snap = {"pnl": daily_pnl(conn)}

    attach_bets(vconn, preview_rows)
    if vconn is conn:
        # Само в облака: там е книгата. Лаптопът не записва решения.
        decide.decide(vconn, preview_rows)
    made = decide.load(vconn)
    for m in preview_rows:
        m["decision"] = made.get(m["event_id"])
        if m["decision"]:
            m["decision"] = {k: m["decision"][k] for k in ("bet", "reason", "time", "min_odds", "pick", "type")}
    log_forecasts(vconn, preview_rows)
    changed = forecast_changes(vconn, preview_rows)
    if changed:
        log.info("Променени прогнози: %d", len(changed))

    data = {
        "generated_at": datetime.now(SOFIA).strftime("%d.%m.%Y %H:%M"),
        "today": today.isoformat(),
        "days": sorted(window | {m["date"] for m in matches}),
        "matches": matches,
        "value": value_section(vconn),
        "fair": fair_sheet(vconn),
        "preview": preview_rows,
        "changed": [m["event_id"] for m in changed],
        "record": record,
        "pnl": snap.get("pnl"),
        "research": research,
        "rewind": rewind_summary(),
        "review": review.summary(vconn),
        "history": history_section(vconn, types=match_types(
            vconn, vconn, (today - timedelta(days=190)).isoformat(), today.isoformat())),
        "source": source,
        "pipeline": pipeline_status(),
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    config.SITE_DIR.mkdir(exist_ok=True)
    OUT.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload),
                   encoding="utf-8")
    log.info("Сайтът е построен: %d мача, %d залога по цена, %d уредени прогнози",
             len(matches), len(data["value"]["upcoming"]), data["record"]["n"])
    return data
