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

from . import config, db, model, predict, results, teams, value
from .market import implied_row
from zoneinfo import ZoneInfo

SOFIA = ZoneInfo("Europe/Sofia")   # облакът е в UTC - часовете се показват в българско

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
            "date": r["date"], "time": (r["kickoff"] or "")[:5],
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


def value_conn(conn):
    """Залозите по цена са в книгата на облака (site/cloud.db). На лаптопа сайтът чете
    оттам, в облака - това е самата му база."""
    cloud = config.SITE_DIR / "cloud.db"
    if cloud.exists() and cloud.resolve() != config.DB_PATH.resolve():
        return db.init(cloud)
    return conn


def value_section(conn, limit=50):
    conn = value_conn(conn)
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


def preview(conn, days=PREVIEW_DAYS, exported=None):
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
    events = conn.execute(
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
                home = teams.match(e["home_team"], fitted.teams)
                away = teams.match(e["away_team"], fitted.teams)
                if not home or not away:
                    reason = "непознат отбор за модела"
                else:
                    probs = fitted.probabilities(home, away)
                    if probs is None:
                        reason = "малко скорошни мачове за някой от отборите"
        start = local(e["commence_time"])
        market = [e["p_home"], e["p_draw"], e["p_away"]]
        out.append({
            "event_id": e["event_id"], "commence_iso": e["commence_time"],
            "date": start.date().isoformat(), "time": start.strftime("%H:%M"),
            "league": results.LEAGUES.get(league, e["sport"].replace("soccer_", "")),
            "home": e["home_team"], "away": e["away_team"],
            "model": list(probs) if probs else None,
            "market": market if all(p is not None for p in market) else None,
            "best_odds": [e["o_home"], e["o_draw"], e["o_away"]],
            "min_odds": [(1 + MIN_EDGE_SHEET) / p for p in probs] if probs else None,
            "reason": reason,
        })
    log.info("Преглед напред: %d мача, с прогноза %d",
             len(out), sum(1 for m in out if m["model"]))
    return out


def attach_bets(conn, rows):
    """Закача към всеки мач залозите по цена и всички цени по букмейкър."""
    conn = value_conn(conn)
    for m in rows:
        if not m.get("event_id"):
            continue
        outcomes = conn.execute(
            """SELECT outcome_idx, selection, prices_json, updated_at FROM fair_prices
                WHERE event_id = ? ORDER BY outcome_idx""", (m["event_id"],)).fetchall()
        m["books"] = []
        for o in outcomes:
            prices = json.loads(o["prices_json"]) if o["prices_json"] else {}
            m["books"].append({
                "selection": o["selection"],
                "prices": [{"book": b, "name": BOOK_LINKS.get(b, (b, None))[0],
                            "url": BOOK_LINKS.get(b, (b, None))[1], "odds": odds}
                           for b, odds in list(prices.items())[:6]],
                "updated": o["updated_at"]})
        found = conn.execute(
            """SELECT selection, bookmaker, odds, sharp_book, sharp_odds, p_fair, edge,
                      n_books, found_at, closing_odds, result, profit
                 FROM value_bets WHERE event_id = ? ORDER BY edge DESC""",
            (m["event_id"],)).fetchall()
        m["bets"] = [dict(b) for b in found]


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
    conn = value_conn(conn)
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
    data = {"written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "matches": day_matches(conn, since, until),
            "models": {league: fitted.export() for league, fitted in models.items()},
            "preview": preview(conn),
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
    today = datetime.now(SOFIA).date()
    window = {(today + timedelta(days=k)).isoformat() for k in range(-DAYS_BACK, DAYS_FORWARD + 1)}

    if from_snapshot and SNAPSHOT.exists():
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        matches = snap["matches"]
        # Прогнозите се смятат НАНОВО от параметрите в снимката: така и мач, който
        # лаптопът никога не е виждал, получава проценти - стига цените му да са дошли.
        preview_rows = (preview(conn, exported=snap["models"]) if snap.get("models")
                        else snap["preview"])
        record, research = snap["record"], snap.get("research")
        source = f"история от {snap['written_at'][:16].replace('T', ' ')}, цените са пресни"
    else:
        since = (today - timedelta(days=DAYS_BACK)).isoformat()
        until = (today + timedelta(days=DAYS_FORWARD)).isoformat()
        matches, preview_rows = day_matches(conn, since, until), preview(conn)
        record = {**predict.record(conn), "backtest": signal_backtest()}
        research, source = research_summary(), None
        snap = {"pnl": daily_pnl(conn)}

    attach_bets(conn, preview_rows)
    log_forecasts(conn, preview_rows)
    changed = forecast_changes(conn, preview_rows)
    if changed:
        log.info("Променени прогнози: %d", len(changed))

    data = {
        "generated_at": datetime.now(SOFIA).strftime("%d.%m.%Y %H:%M"),
        "today": today.isoformat(),
        "days": sorted(window | {m["date"] for m in matches}),
        "matches": matches,
        "value": value_section(conn),
        "fair": fair_sheet(conn),
        "preview": preview_rows,
        "changed": [m["event_id"] for m in changed],
        "record": record,
        "pnl": snap.get("pnl"),
        "research": research,
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
