"""
Сайтът: един самостоятелен HTML файл (site_template.html + данните като JSON вътре в него).

Четири таба, всичко по идеите на професионалиста:
  Прогнози      всички мачове от всички първенства за следващите 7 дни - роботът по пазари
                (1/X/2, двоен шанс, над/под 2.5) и главният съвет с цена поне 1.40
  Резултати     записаните преди мача прогнози и кои са познати - за 7 дни до 6 месеца назад,
                по лиги и по пазари, с дохода в пари
  Лиги          голове, „тото“, паузите, дербитата, шампион и изпадащи, колко познава роботът
                в лигата (назад във времето и на живо)
  Професионалист  неговите съвети, проверката им на данни и как роботът ги прилага
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from . import config, db, live, robot, season, tips
from .leagues import LEAGUES

log = logging.getLogger(__name__)

KEEP_DAYS = 190          # колко назад се пазят резултатите в сайта (филтърът стига до 6 месеца)


def read_json(name, default=None):
    path = config.DATA_DIR / name
    if not path.exists():
        log.warning("%s го няма - частта от сайта ще е празна", path)
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def short(p):
    return None if p is None else round(p, 3)


def compact_analysis(a):
    """Анализът на мача - по-кратко (формата и директните срещи като списъци)."""
    if not a:
        return None
    form = lambda xs: [[x["r"], x["gf"], x["ga"], x["v"], x["o"]] for x in xs]
    out = {"t": a.get("text") or [],
           "f": {k: form(v) for k, v in a["form"].items()},
           "h2h": [[x["d"], x["h"], x["a"], x["s"][0], x["s"][1]] for x in a.get("h2h") or []]}
    for k in ("table", "season", "goals", "cards", "corners", "recent", "referee"):
        if a.get(k):
            out[k] = a[k]
    return out


def _pred(x):
    """Рисковата/по-сигурната прогноза: [избор, шанс по робота, коефициент или None, обичайното за лигата]."""
    if not x:
        return None
    return [x["sel"], x["p"], x.get("odds"), x.get("base"), x.get("src")]


SELS = ("1", "X", "2", "1X", "X2", "12", "O", "U")


def _one(x):
    """Едната прогноза: [избор, шанс по робота, коефициент, откъде (book/robot), в 1.40-1.80?]."""
    if not x:
        return None
    return [x["sel"], x["p"], x.get("odds"), x.get("src"), x.get("band", True)]


def compact_forecast(m):
    """Един мач за таба с прогнозите - кратки ключове, за да е малък файлът."""
    out = {"i": m["id"], "l": m["league"], "k": m["kickoff"],
           "h": m.get("home_src") or m["home"], "a": m.get("away_src") or m["away"],
           "lk": m.get("locked")}
    if not m.get("basis"):
        out["w"] = m.get("why")
        return out
    r, mk = m["probs"]["robot"], m["probs"].get("market") or {}
    avg = (m.get("prices") or {}).get("avg") or {}
    out.update({
        "b": m["basis"],
        "r": {k: short(r.get(k)) for k in ("1", "X", "2", "1X", "X2", "12", "O", "U") if r.get(k) is not None},
        "m": {k: short(mk.get(k)) for k in ("1", "X", "2", "1X", "X2", "12", "O", "U") if mk.get(k) is not None},
        "o": {k: round(v, 2) for k, v in avg.items()},
        "n": (m.get("prices") or {}).get("n"),
        "pk": m["picks"]["robot"], "t": m.get("tip"), "to": m.get("tip_odds"), "tb": m.get("tip_best"),
        "rl": m.get("rule"),
        "rk": _pred(m.get("risky")), "sf": _pred(m.get("safer")), "one": _one(m.get("one")),
        "w": m.get("why"), "f": [k for k, v in (m.get("flags") or {}).items() if v is True],
    })
    if r.get("xg_home") is not None:
        out["xg"] = [round(r["xg_home"], 2), round(r["xg_away"], 2)]
    an = compact_analysis(m.get("analysis"))
    if an:
        out["an"] = an
    return out


def _corner_sides(picks):
    c = (picks.get("extras") or {}).get("corners") or {}
    if c.get("home") is None or c.get("away") is None:
        return None
    return {k: short(v) for k, v in robot.corner_sides(c["home"], c["away"]).items()}


def hc_summary():
    """Хендикапът и повече корнери назад (data/handicap_corners.json) - за сайта."""
    d = read_json("handicap_corners.json", {})
    if not d:
        return None
    big = ((d.get("handicap") or {}).get("big_favourite") or {}).get("clean") or {}
    cor = (d.get("corners") or {}).get("clean") or {}
    return {"fav_by_2": big.get("fav_by_2"), "said": big.get("said"), "n_fav": big.get("n"),
            "k_hit": cor.get("hit_pick"), "k_fav": (cor.get("favourite") or {}).get("hit_pick"),
            "k_bal": (cor.get("balanced") or {}).get("hit_pick"), "k_draw": cor.get("draw_rate"),
            "skill": d.get("show_percent")}


def record(conn, now):
    """Уредените прогнози от последните KEEP_DAYS дни - за таба с резултатите."""
    since = (now - timedelta(days=KEEP_DAYS)).isoformat()
    out = []
    for t in conn.execute("""SELECT t.*, f.home_src, f.away_src, s.hy + s.ay AS cards, s.hc + s.ac AS corners,
                                     s.hy AS hy, s.ay AS ay, s.hc AS hc, s.ac AS ac, mm.referee AS referee
                               FROM tips t LEFT JOIN fixtures f ON f.id = t.fixture_id
                               LEFT JOIN match_stats s ON s.match_id = t.match_id
                               LEFT JOIN matches mm ON mm.id = t.match_id
                              WHERE t.kickoff >= ? AND t.hg IS NOT NULL AND t.basis = 'model'
                              ORDER BY t.kickoff""", (since,)):
        probs, picks = json.loads(t["probs_json"]), json.loads(t["picks_json"])
        avg = ((json.loads(t["prices_json"]) or {}).get("avg") or {}) if t["prices_json"] else {}
        flags = json.loads(t["flags_json"] or "{}")
        rp, mp = picks["robot"], picks.get("market") or {}
        out.append({"i": t["fixture_id"], "k": t["kickoff"], "l": t["league"], "h": t["home_src"] or t["home"],
                    "a": t["away_src"] or t["away"], "b": t["basis"], "s": [t["hg"], t["ag"]],
                    "pk": rp, "mk": mp,
                    # шансовете (на робота, а без модел - на букмейкъра) и средните коефициенти от записа
                    "r": {k: short(probs["robot"].get(k)) for k in SELS if probs["robot"].get(k) is not None},
                    "o": {k: round(v, 2) for k, v in avg.items() if k in SELS and v and v > 1.01},
                    "xg": ([round(probs["robot"]["xg_home"], 2), round(probs["robot"]["xg_away"], 2)]
                           if probs["robot"].get("xg_home") is not None else None),
                    "t": t["tip"], "to": t["tip_odds"], "tb": t["tip_best"],
                    "rl": robot.rule_of(flags, t["locked_at"]), "lk": t["locked_at"],
                    "rk": _pred(picks.get("risky")), "sf": _pred(picks.get("safer")), "one": _one(picks.get("one")),
                    "ct": [t["cards"], t["corners"]],
                    "f": [k for k, v in flags.items() if v is True], "w": flags.get("why"),
                    # картони и корнери: [избор, линия, колко станаха] - колко станаха идва от
                    # football-data 1-3 дни след мача; дотогава None
                    "x": {k: [v["pick"], v["line"], t[k], v.get("total")] for k, v in (picks.get("extras") or {}).items()},
                    "ref": t["referee"] or flags.get("referee"), "ya": [t["hy"], t["ay"]], "cs": [t["hc"], t["ac"]],
                    # хендикапът и повече корнери - по записа преди мача
                    "hp": ({k: short(v) for k, v in robot.handicap_probs(probs["robot"]).items()}
                           if t["basis"] == "model" and probs["robot"].get("xg_home") is not None else None),
                    "kp": _corner_sides(picks)})
    return out


def backtest_summary():
    """От data/backtest.json - само нужното за сайта."""
    bt = read_json("backtest.json", {})
    rule = bt.get("rule", "likely")
    keep = {}
    for code, d in (bt.get("leagues") or {}).items():
        item = {k: d.get(k) for k in (f"tip:{rule}:all", f"tip:{rule}:clean", f"tip:{rule}:select",
                                      "robot:1x2:all", "market:1x2:all", "robot:dc:all", "market:dc:all",
                                      "robot:ou:all", "market:ou:all", "robot:1x2:after_break", "market:1x2:after_break")}
        item["seasons"] = {k.split(":", 1)[1]: v for k, v in d.items() if k.startswith("tip_season:")}
        keep[code] = {k: v for k, v in item.items() if v}
    return {"rule": rule, "start": bt.get("start"), "select_end": bt.get("select_end"),
            "generated": bt.get("generated"), "leagues": keep}


def one_summary():
    """Едната прогноза назад (data/one_backtest.json): приетият вариант - избор, чиста проверка, по лиги."""
    ob = read_json("one_backtest.json", {})
    v = robot.ONE_VARIANT                    # на живо е този вариант - и числата назад са неговите
    if v not in (ob.get("variants") or {}):
        return None
    keep = ("n", "hit", "hit_se", "said", "base", "odds", "roi", "roi_se", "n_book", "kinds")
    pick = lambda d: {k: d[k] for k in keep if d and k in d} if d else None
    return {"variant": v, "select": pick(ob["variants"][v]["select"]), "clean": pick(ob["variants"][v]["clean"]),
            "leagues": {lg: {"n": d["n"], "hit": d["hit"]} for lg, d in (ob.get("leagues") or {}).items() if d}}


def signs_summary():
    """Рисковата и по-сигурната назад (data/signs_backtest.json): общо и по лиги."""
    sb = read_json("signs_backtest.json", {})
    lift = ((sb.get("results") or {}).get("lift")) or {}
    keep = ("n", "hit", "roi", "roi_se", "odds")
    pick = lambda d: {k: d[k] for k in keep if d and k in d} if d else None
    return {"share": lift.get("share"), "by_sign": {k: pick(v) for k, v in (lift.get("by_sign") or {}).items()},
            "risky": {k: pick(v) for k, v in (lift.get("risky") or {}).items()},
            "safer": {k: pick(v) for k, v in (lift.get("safer") or {}).items()},
            "safer_kinds": lift.get("safer_kinds"),
            "leagues": {lg: {k: pick(v) for k, v in d.items()} for lg, d in (lift.get("leagues") or {}).items()}}


def extras_summary():
    """Картони и корнери назад (data/extras_backtest.json) - има ли умение и колко познава по лиги."""
    xb = read_json("extras_backtest.json", {})
    keep = ("n", "hit", "brier", "brier_base", "mae", "mae_base")
    return {kind: {"skill": d["skill"],
                   "leagues": {c: {"all": {k: v["all"][k] for k in keep}} for c, v in d["leagues"].items() if v.get("all")}}
            for kind, d in (xb.get("kinds") or {}).items()}


def analysis_summary():
    la = read_json("leagues.json", {})
    out = {}
    for code, d in (la.get("leagues") or {}).items():
        out[code] = {"goals": d["goals"], "gs": {s: (g or {}).get("avg") for s, g in d["goals_by_season"].items()},
                     "toto": d["toto"], "fav": d["favourites"], "ab": d["after_break"], "derbies": d["derbies"],
                     "odds": d["odds"], "refs": d.get("referees") or []}
    return {"generated": la.get("generated"), "band": la.get("band"), "leagues": out,
            "summary": la.get("summary"), "ranking": la.get("goals_ranking")}


def seasons(conn, now):
    """Симулацията на сезона за всяка лига - веднъж на ден (кешът е в meta)."""
    today = now.date().isoformat()
    out = {}
    for code, lg in LEAGUES.items():
        if not lg.has_history or season.meetings(code) == 0:
            continue
        cached = db.get_meta(conn, f"season:{code}")
        if cached:
            data = json.loads(cached)
            if data.get("day") == today:
                if data.get("sim"):
                    out[code] = data["sim"]
                continue
        fitted = tips.fitted_model(conn, code, now)
        sim = season.simulate(conn, code, fitted) if fitted else None
        db.set_meta(conn, f"season:{code}", json.dumps({"day": today, "sim": sim}))
        if sim:
            out[code] = sim
    return out


def bonus_data(now):
    """Бонус анализите (bonus/*.json от задачите час преди мача) - по мач, за последните KEEP_DAYS дни."""
    folder = config.SITE_DIR / "bonus"
    out = {}
    if not folder.exists():
        return out
    since = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H%M")
    for path in sorted(folder.glob("*.json")):
        if path.stem < since:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for m in data.get("matches", []):
            new = m.get("new") or {}
            out[m["id"]] = {"at": data["made_at"], "ch": m["changes"],
                            "rk": _pred(new.get("risky")), "sf": _pred(new.get("safer")), "one": _one(new.get("one"))}
    return out


def build(conn, now=None, upcoming=None):
    now = now or datetime.now(timezone.utc)
    upcoming = upcoming if upcoming is not None else tips.preview(conn, now)
    data = {
        "generated": now.isoformat(timespec="seconds"),
        "credits": db.get_meta(conn, "credits_remaining"),
        "leagues": {c: {"c": lg.country, "n": lg.name, "tier": lg.tier, "src": lg.source,
                        "rr": season.meetings(c) if lg.has_history else 0, "split": lg.split,
                        "fast": bool(lg.sport) or c in live.ESPN}      # бърз резултат (bets/live.py): odds API или ESPN
                    for c, lg in LEAGUES.items()},
        "upcoming": [compact_forecast(m) for m in upcoming],
        "record": record(conn, now),
        # започнали, без резултат - страницата ги мести в „Чакат резултат“ 10 мин. след началото
        # собственикът (02.10): в резултатите - само мачовете с прогноза на робота (без оценка - само в „Прогнози“)
        "pending": [compact_forecast(m) for m in tips.started(conn, now) if m.get("basis") == "model"],
        "first_tip": db.get_meta(conn, "first_tip"),
        "backtest": backtest_summary(),
        "analysis": analysis_summary(),
        "extras_bt": extras_summary(),
        "signs_bt": signs_summary(),
        "one_bt": one_summary(),
        "hc_bt": hc_summary(),
        # границите на професионалиста (bets/robot.py) - сайтът ги пише от тук, за да не се разминат
        "ranges": {"safe": list(robot.SAFE_RANGE), "risky_from": robot.RISKY_FROM},
        "bonus": bonus_data(now),
        "seasons": seasons(conn, now),
        "pro": read_json("pro_tips.json", []),
        "url": config.SITE_URL,
    }
    template = config.TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = template.replace("/*__DATA__*/null", payload)
    config.SITE_DIR.mkdir(exist_ok=True)
    (config.SITE_DIR / "index.html").write_text(html, encoding="utf-8")
    log.info("Сайтът е построен: %d предстоящи, %d уредени, %d KB", len(data["upcoming"]), len(data["record"]),
             len(html) // 1024)
    return data
