"""
Колонки НА СЯНКА - с поправен шанс (собственикът, 2026-10-11: „роботът със слой, който се учи от собствените си резултати - по-добър е“).

Проверката назад (research/online_learning_backtest.py): поправка на шанса по вид избор (1/X/2, двоен шанс, над/под 1.5 / 2.5 / 3.5, ...) от собствените резултати на робота прави казаното
честно (66.7% казани срещу 64.5% излезли) и колонките по 3 минават 34-35% вместо 29%, но подборът се мести към избори без цена от букмейкър (над 1.5, под 3.5), затова парите не са измерени
и правилата на майстора НЕ са сменени. Тук: всеки ден след записа в 07:00 се правят и колонки на сянка - по същите правила и от същите записани прогнози, но с поправения шанс
(топ шанс по поправения шанс, праг 65% върху него, един мач на първенство, до 4 на ден). Те не се играят и не са на сайта като колонки; записват се и се уреждат, за да се сравнят на живо
със сегашните няколко седмици. Таблицата на поправката: data/calibration.json (месечно от research/calibration_build.py). Таблица columns_shadow (не е в db.SCHEMA - ensure).

СИСТЕМАТА (2026-10-12, собственикът: „дай най-доброто; някои дни без колонки, вместо 2 - само една; логика и мислене в кода“) - най-доброто от ~1 000 варианта назад
(research/master_v2_system.py, fewer_columns_backtest.py, pro_price_backtest.py; чиста проверка 1.09 € от 1 € на средна цена, 1.16 € на най-добрия букмейкър, 78 колонки):
вариант 2 + ВСИЧКИ правила на майстора, по 2 мача, НАЙ-МНОГО ЕДНА колонка на ден и само когато шансът на колонката (произведението на поправените шансове) е >= 50%,
четвъртък-неделя, най-добрите 11 първенства (от периода на избора), истинска цена от 1.40 (за над/под 1.5 / 3.5 и двата вкарват - от odds API на мач, иначе оценка от пазара
над/под 2.5). Дни без колонка са част от логиката. Таблица columns_system; не се играе и не е правило - мери се на живо (таб Колонки -> „Системата“).
"""

import json
import logging
from datetime import datetime, timezone

from . import columns, config, robot, rules, sure

log = logging.getLogger(__name__)

FROM = "2026-10-12T04:00:00+00:00"          # първият запис на сянката (12.10, 07:00 българско време) - целият ден, не частичен
K = 50


def ensure(conn):
    for t in ("columns_shadow", "columns_system"):
        conn.execute(f"CREATE TABLE IF NOT EXISTS {t} (day TEXT NOT NULL, idx INTEGER NOT NULL, legs_json TEXT NOT NULL, "
                     "summary_json TEXT NOT NULL, locked_at TEXT NOT NULL, passed INTEGER, PRIMARY KEY (day, idx))")


def table():
    path = config.DATA_DIR / "calibration.json"
    return json.loads(path.read_text(encoding="utf-8"))["kinds"] if path.exists() else {}


def kind(sel):
    """Видът избор за поправката - същото разделение като research/calibration_build.py и online_learning_backtest.py."""
    if sel in ("1", "X", "2"):
        return "1x2"
    if sel in ("1X", "X2", "12"):
        return "dc"
    if sel in ("O", "U"):
        return "ou25"
    if sel in ("O15", "U15"):
        return "ou15"
    if sel in ("O35", "U35"):
        return "ou35"
    if sel in ("GG", "NG"):
        return "btts"
    if sel in robot.HANDICAPS:
        return "hcp"
    if sel in robot.CORNER_SIDES:
        return "cside"
    return "extras"


def calibrate(one, tbl=None):
    """Поправеният шанс на едната прогноза: p - сбор/(n + K) по вида на избора (виж research/calibration_build.py); без данни за вида - същият."""
    tbl = table() if tbl is None else tbl
    t = tbl.get(kind(one["sel"]))
    if not t:
        return one["p"]
    return min(max(one["p"] - t["sum"] / (t["n"] + K), 0.01), 0.99)


def lock(conn, now=None):
    """Колонките на сянка за деня - веднъж, в първото пускане след записа; от записаните прогнози (tips) с поправения шанс. Връща броя колонки."""
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    if now.isoformat() < FROM:
        return 0
    day = columns.day_of(now.isoformat())
    if conn.execute("SELECT 1 FROM columns_shadow WHERE day = ? AND idx = 0", (day,)).fetchone():
        return 0
    tbl = table()
    if not tbl:
        log.warning("Колонки на сянка: няма data/calibration.json")
        return 0
    start, end = columns.day_bounds(day)
    rows = conn.execute("SELECT t.*, f.home_src, f.away_src FROM tips t LEFT JOIN fixtures f ON f.id = t.fixture_id "
                        "WHERE t.kickoff >= ? AND t.kickoff < ? AND t.basis = 'model'", (max(start, now).isoformat(), end.isoformat())).fetchall()
    if not rows:
        return 0
    ms, raw = [], {}
    for r in rows:
        picks, flags = json.loads(r["picks_json"]), json.loads(r["flags_json"] or "{}")
        one = picks.get("one")
        if not one:
            continue
        flags.pop("sure", None)                              # топ шанс - наново, по поправения шанс
        pc = calibrate(one, tbl)
        raw[r["fixture_id"]] = one["p"]
        ms.append({"id": r["fixture_id"], "league": r["league"], "kickoff": r["kickoff"], "home": r["home"], "away": r["away"],
                   "home_src": r["home_src"], "away_src": r["away_src"], "basis": r["basis"], "one": {**one, "p": pc}, "flags": flags})
    groups = {}
    for m in ms:
        if sure.candidate(m["flags"], m["basis"], m["one"]):
            groups.setdefault(m["league"], []).append(m)
    for g in groups.values():
        info = sure.info_for([(m["id"], m["one"]["p"]) for m in g])
        for m in g:
            m["flags"]["sure"] = info[m["id"]]
    cols = columns.build(columns.candidates(ms))
    stamp = now.isoformat(timespec="seconds")
    for i, col in enumerate(cols, 1):
        for leg in col:
            leg["p_raw"] = raw.get(leg["id"])
        conn.execute("INSERT OR IGNORE INTO columns_shadow (day, idx, legs_json, summary_json, locked_at) VALUES (?, ?, ?, ?, ?)",
                     (day, i, json.dumps(col, ensure_ascii=False), json.dumps({**columns.summary(col), "cday": day}), stamp))
    conn.execute("INSERT OR IGNORE INTO columns_shadow (day, idx, legs_json, summary_json, locked_at) VALUES (?, 0, '[]', ?, ?)",
                 (day, json.dumps({"cday": day}), stamp))
    conn.commit()
    log.info("Колонки на сянка за %s: %d", day, len(cols))
    return len(cols)


def settle(conn, table_name="columns_shadow"):
    """Колонка на сянка (или на системата) пада, щом един мач не излезе; минава, когато всичките са излезли."""
    ensure(conn)
    done = 0
    for r in conn.execute(f"SELECT day, idx, legs_json FROM {table_name} WHERE idx > 0 AND passed IS NULL").fetchall():
        state = []
        for leg in json.loads(r["legs_json"]):
            t = conn.execute("SELECT hg, ag FROM tips WHERE fixture_id = ?", (leg["id"],)).fetchone()
            state.append(None if not t or t["hg"] is None else bool(robot.hit_any(leg["sel"], t["hg"], t["ag"])))
        if any(s is False for s in state):
            passed = 0
        elif all(s is True for s in state):
            passed = 1
        else:
            continue
        conn.execute(f"UPDATE {table_name} SET passed = ? WHERE day = ? AND idx = ?", (passed, r["day"], r["idx"]))
        done += 1
    conn.commit()
    return done


def settle_system(conn):
    return settle(conn, "columns_system")


# ---------- СИСТЕМАТА ----------

SYSTEM_FROM = "2026-10-15T04:00:00+00:00"              # първият запис: четвъртък 15.10, 07:00 българско време
SYSTEM_LEAGUES = frozenset({"D1", "D2", "E1", "E2", "EC", "G1", "I1", "I2", "SP1", "SP2", "T1"})   # най-добрата половина по връщане на мач в избора
SYSTEM_DAYS = (3, 4, 5, 6)                             # четвъртък-неделя
SYSTEM_MIN_P, SYSTEM_MIN_COL, SYSTEM_FLOOR, SYSTEM_SIZE = 0.65, 0.50, 1.40, 2
EST_MARGIN = 0.05
SYSTEM_RULE = ("вариант 2 + правилата на майстора; по 2 мача, най-много 1 колонка на ден и само при шанс на колонката >= 50%; "
               "четвъртък-неделя; 11 първенства; истинска цена от 1.40")


def _lam_from(p_over):
    from scipy.stats import poisson
    lo, hi = 0.2, 8.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if 1 - poisson.cdf(2, mid) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def estimated_price(sel, avg, probs):
    """Цена за над/под 1.5 / 3.5 и двата вкарват от пазара над/под 2.5 (λ по Poisson, марж 5%) - research/safe_legs_backtest.py; None без пазар."""
    import math
    from scipy.stats import poisson
    if not avg.get("O") or not avg.get("U"):
        return None
    po = (1 / avg["O"]) / (1 / avg["O"] + 1 / avg["U"])
    lam = _lam_from(po)
    xh, xa = probs.get("xg_home"), probs.get("xg_away")
    share = xh / (xh + xa) if xh and xa else 0.55
    lh, la = lam * share, lam * (1 - share)
    gg = (1 - math.exp(-lh)) * (1 - math.exp(-la))
    p = {"O15": 1 - poisson.cdf(1, lam), "U15": poisson.cdf(1, lam), "O35": 1 - poisson.cdf(3, lam), "U35": poisson.cdf(3, lam),
         "GG": gg, "NG": 1 - gg}.get(sel)
    return round(1 / (p * (1 + EST_MARGIN)), 2) if p and p > 0.01 else None


def real_price(conn, fixture_id, league, sel, avg, probs, now, kickoff=None):
    """(цена, източник): 1/X/2, двоен шанс, над/под 2.5 - средната на букмейкърите от записа; над/под 1.5 / 3.5, двата вкарват - odds API
    на мач (alternate_totals / btts, 1 кредит на пазар, кеш 6 ч), иначе оценка от пазара над/под 2.5."""
    from . import odds_api
    from .leagues import LEAGUES
    if sel in avg:
        return round(avg[sel], 2), "book"
    spec = {"O15": ("alternate_totals", "Over", 1.5), "U15": ("alternate_totals", "Under", 1.5), "O35": ("alternate_totals", "Over", 3.5),
            "U35": ("alternate_totals", "Under", 3.5), "GG": ("btts", "Yes", None), "NG": ("btts", "No", None)}.get(sel)
    sport = LEAGUES[league].sport if league in LEAGUES else None
    remaining = odds_api.last_remaining()
    if spec and sport and (remaining is None or remaining > 500) and (kickoff is None or kickoff > now.isoformat()):
        market, name, point = spec
        try:
            d = odds_api._get(f"/sports/{sport}/events/{fixture_id}/odds", {"regions": "eu", "markets": market, "oddsFormat": "decimal"},
                              cache_name=f"sys_{fixture_id}_{market}", cache_minutes=360)
            prices = [o["price"] for b in d.get("bookmakers", []) for m in b["markets"] for o in m["outcomes"]
                      if o["name"] == name and (point is None or o.get("point") == point)]
            if prices:
                return round(sum(prices) / len(prices), 2), "book"
        except RuntimeError as e:
            log.warning("Системата: цената за %s %s не се взе от odds API - %s", fixture_id, sel, e)
    est = estimated_price(sel, avg, probs)
    return (est, "est") if est else (None, None)


def system_lock(conn, now=None):
    """Колонката на системата за деня - веднъж, след записа в 07:00; четвъртък-неделя; най-много една; дни без колонка са нормални. Връща 1 или 0."""
    import math
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    if now.isoformat() < SYSTEM_FROM:
        return 0
    day = columns.day_of(now.isoformat())
    if conn.execute("SELECT 1 FROM columns_system WHERE day = ? AND idx = 0", (day,)).fetchone():
        return 0
    stamp = now.isoformat(timespec="seconds")

    def mark(note):
        conn.execute("INSERT OR IGNORE INTO columns_system (day, idx, legs_json, summary_json, locked_at) VALUES (?, 0, '[]', ?, ?)",
                     (day, json.dumps({"cday": day, "note": note}, ensure_ascii=False), stamp))
        conn.commit()

    if datetime.fromisoformat(day).weekday() not in SYSTEM_DAYS:
        mark("не е четвъртък-неделя")
        return 0
    tbl = table()
    if not tbl:
        log.warning("Системата: няма data/calibration.json")
        return 0
    start, end = columns.day_bounds(day)
    q = ",".join("?" * len(SYSTEM_LEAGUES))
    rows = conn.execute(f"SELECT t.*, f.home_src, f.away_src FROM tips t LEFT JOIN fixtures f ON f.id = t.fixture_id WHERE t.kickoff >= ? "
                        f"AND t.kickoff < ? AND t.basis = 'model' AND t.league IN ({q})",
                        (max(start, now).isoformat(), end.isoformat(), *SYSTEM_LEAGUES)).fetchall()
    if not rows:
        mark("няма записани прогнози в 11-те първенства")
        return 0
    cands = []
    for r in rows:
        picks, flags = json.loads(r["picks_json"]), json.loads(r["flags_json"] or "{}")
        one = picks.get("one")
        if not one or rules.match_block(flags, r["basis"]) or flags.get("postponed"):
            continue
        avg = ((json.loads(r["prices_json"]) or {}).get("avg") or {}) if r["prices_json"] else {}
        cands.append({"id": r["fixture_id"], "league": r["league"], "kickoff": r["kickoff"], "home": r["home_src"] or r["home"],
                      "away": r["away_src"] or r["away"], "sel": one["sel"], "p_raw": one["p"], "p": calibrate(one, tbl), "avg": avg,
                      "probs": json.loads(r["probs_json"])["robot"]})
    groups = {}
    for c in cands:
        groups.setdefault(c["league"], []).append(c)
    top = []
    for g in groups.values():
        chosen = sure.pick([(c["id"], c["p"]) for c in g], sure.SHARE, sure.ROUNDING, 0.0)
        top += [c for c in g if c["id"] in chosen and c["p"] >= SYSTEM_MIN_P]
    top.sort(key=lambda c: -c["p"])
    col, lgs, checked = [], set(), 0
    for c in top:
        if c["league"] in lgs:
            continue
        price, src = real_price(conn, c["id"], c["league"], c["sel"], c["avg"], c["probs"], now, c["kickoff"])
        checked += 1
        if price is None or price < SYSTEM_FLOOR or robot.toto_blocked(c["league"], c["sel"], price):
            continue
        col.append({**{k: c[k] for k in ("id", "league", "kickoff", "home", "away", "sel", "p", "p_raw")}, "odds": price, "src": src})
        lgs.add(c["league"])
        if len(col) == SYSTEM_SIZE:
            break
    if len(col) < SYSTEM_SIZE:
        mark(f"не стигат мачове по правилата ({len(top)} с топ шанс, {checked} с проверена цена)")
        log.info("Системата %s: без колонка - %d кандидати", day, len(top))
        return 0
    chance = math.prod(c["p"] for c in col)
    if chance < SYSTEM_MIN_COL:
        mark(f"шансът на колонката {chance:.1%} е под 50% - без колонка")
        log.info("Системата %s: без колонка - шанс %.0f%%", day, chance * 100)
        return 0
    odds = math.prod(c["odds"] for c in col)
    summary = {"odds": round(odds, 2), "claimed": round(chance, 4), "honest": round(chance, 4), "return": round(chance * odds, 3),
               "book": all(c["src"] == "book" for c in col), "cday": day}
    conn.execute("INSERT OR IGNORE INTO columns_system (day, idx, legs_json, summary_json, locked_at) VALUES (?, 1, ?, ?, ?)",
                 (day, json.dumps(col, ensure_ascii=False), json.dumps(summary), stamp))
    mark("колонка")
    log.info("Системата %s: колонка %s (шанс %.0f%%, коеф. %.2f)", day, " + ".join(f"{c['home']} {c['sel']}" for c in col), chance * 100, summary["odds"])
    return 1


def system_for_site(conn, now=None):
    """За сайта: днешната колонка на системата (или защо няма), всички досега, колко са минали и парите при 30 €, по месеци."""
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    days = [r[0] for r in conn.execute("SELECT DISTINCT day FROM columns_system WHERE idx = 0 ORDER BY day")]
    today = columns.day_of(now.isoformat())
    out = {"today": [], "days": len(days), "from": days[0] if days else SYSTEM_FROM[:10], "rule": SYSTEM_RULE, "leagues": sorted(SYSTEM_LEAGUES),
           "note": None, "measured": {"return_avg": 1.09, "return_best": 1.16, "pass": 0.51, "per_month": 7, "n": 78}}
    m = conn.execute("SELECT summary_json FROM columns_system WHERE day = ? AND idx = 0", (today,)).fetchone()
    if m:
        out["note"] = json.loads(m["summary_json"]).get("note")
    for r in conn.execute("SELECT idx, legs_json, summary_json, locked_at, passed FROM columns_system WHERE day = ? AND idx > 0", (today,)):
        legs = json.loads(r["legs_json"])
        for leg in legs:
            t = conn.execute("SELECT hg, ag FROM tips WHERE fixture_id = ?", (leg["id"],)).fetchone()
            settled = bool(t and t["hg"] is not None)
            leg["hit"] = bool(robot.hit_any(leg["sel"], t["hg"], t["ag"])) if settled else None
            leg["score"] = [t["hg"], t["ag"]] if settled else None
        out["today"].append({"idx": r["idx"], "day": today, "legs": legs, "passed": r["passed"], "locked_at": r["locked_at"], **json.loads(r["summary_json"])})
    rows = conn.execute("SELECT day, legs_json, summary_json, passed FROM columns_system WHERE idx > 0 ORDER BY day DESC").fetchall()
    settled = [r for r in rows if r["passed"] is not None]
    ret = sum(json.loads(r["summary_json"])["odds"] if r["passed"] else 0.0 for r in settled)
    out["all"] = {"cols": len(rows), "settled": len(settled), "passed": sum(r["passed"] for r in settled),
                  "return": ret / len(settled) if settled else None,
                  "net30": round(sum((json.loads(r["summary_json"])["odds"] - 1) * 30 if r["passed"] else -30 for r in settled), 2)}
    out["past"] = [{"day": r["day"], "legs": json.loads(r["legs_json"]), "passed": r["passed"], **json.loads(r["summary_json"])}
                   for r in rows[:40] if r["day"] != today]
    for r in conn.execute("SELECT substr(day, 1, 7) AS m, COUNT(*) AS n, SUM(passed) AS p FROM columns_system WHERE idx > 0 AND passed IS NOT NULL "
                          "GROUP BY m ORDER BY m"):
        out.setdefault("monthly", {})[r["m"]] = {"settled": r["n"], "passed": r["p"]}
    return out


def _legs_hits(conn, cols):
    n = hit = 0
    for legs in cols:
        for leg in legs:
            t = conn.execute("SELECT hg, ag FROM tips WHERE fixture_id = ?", (leg["id"],)).fetchone()
            if t and t["hg"] is not None:
                h = robot.hit_any(leg["sel"], t["hg"], t["ag"])
                if h is not None:
                    n += 1
                    hit += bool(h)
    return n, hit


def for_site(conn, now=None):
    """За сайта: днешните колонки на сянка и мерките срещу сегашните колонки за същите дни (уредени колонки и мачове в тях)."""
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    days = [r[0] for r in conn.execute("SELECT DISTINCT day FROM columns_shadow WHERE idx = 0 ORDER BY day")]
    if not days:
        return None
    today = columns.day_of(now.isoformat())
    out = {"today": [], "days": len(days), "from": days[0], "rule": "поправен шанс: p - сбор/(n+50) по вид избор; топ шанс и праг 65% върху него"}
    for r in conn.execute("SELECT idx, legs_json, summary_json, locked_at, passed FROM columns_shadow WHERE day = ? AND idx > 0 ORDER BY idx", (today,)):
        legs = json.loads(r["legs_json"])
        for leg in legs:                                      # резултатът на мача, както в columns.record
            t = conn.execute("SELECT hg, ag FROM tips WHERE fixture_id = ?", (leg["id"],)).fetchone()
            settled = bool(t and t["hg"] is not None)
            leg["hit"] = bool(robot.hit_any(leg["sel"], t["hg"], t["ag"])) if settled else None
            leg["score"] = [t["hg"], t["ag"]] if settled else None
        out["today"].append({"idx": r["idx"], "legs": legs, "passed": r["passed"], "locked_at": r["locked_at"], **json.loads(r["summary_json"])})
    for name, table_name, extra in (("shadow", "columns_shadow", ""), ("official", "columns", " AND idx < 100")):
        q = f"SELECT legs_json, passed FROM {table_name} WHERE idx > 0{extra} AND day IN ({','.join('?' * len(days))})"
        rows = conn.execute(q, days).fetchall()
        settled = [r for r in rows if r["passed"] is not None]
        ln, lh = _legs_hits(conn, [json.loads(r["legs_json"]) for r in rows])
        out[name] = {"cols": len(rows), "settled": len(settled), "passed": sum(r["passed"] for r in settled), "legs": ln, "legs_hit": lh}
        # по месеци - уредените колонки (собственикът, 11.10: „сравни помесечно как ще се развиват нещата и при двете“)
        q = f"SELECT substr(day, 1, 7) AS m, COUNT(*) AS n, SUM(passed) AS p FROM {table_name} WHERE idx > 0{extra} AND passed IS NOT NULL "             f"AND day IN ({','.join('?' * len(days))}) GROUP BY m ORDER BY m"
        for r in conn.execute(q, days):
            out.setdefault("monthly", {}).setdefault(r["m"], {})[name] = {"settled": r["n"], "passed": r["p"]}
    return out
