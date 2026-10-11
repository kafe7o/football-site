"""
Колонки НА СЯНКА - с поправен шанс (собственикът, 2026-10-11: „роботът със слой, който се учи от собствените си резултати - по-добър е“).

Проверката назад (research/online_learning_backtest.py): поправка на шанса по вид избор (1/X/2, двоен шанс, над/под 1.5 / 2.5 / 3.5, ...) от собствените резултати на робота прави казаното
честно (66.7% казани срещу 64.5% излезли) и колонките по 3 минават 34-35% вместо 29%, но подборът се мести към избори без цена от букмейкър (над 1.5, под 3.5), затова парите не са измерени
и правилата на майстора НЕ са сменени. Тук: всеки ден след записа в 07:00 се правят и колонки на сянка - по същите правила и от същите записани прогнози, но с поправения шанс
(топ шанс по поправения шанс, праг 65% върху него, един мач на първенство, до 4 на ден). Те не се играят и не са на сайта като колонки; записват се и се уреждат, за да се сравнят на живо
със сегашните няколко седмици. Таблицата на поправката: data/calibration.json (месечно от research/calibration_build.py). Таблица columns_shadow (не е в db.SCHEMA - ensure).
"""

import json
import logging
from datetime import datetime, timezone

from . import columns, config, robot, sure

log = logging.getLogger(__name__)

FROM = "2026-10-12T04:00:00+00:00"          # първият запис на сянката (12.10, 07:00 българско време) - целият ден, не частичен
K = 50


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS columns_shadow (day TEXT NOT NULL, idx INTEGER NOT NULL, legs_json TEXT NOT NULL, "
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


def settle(conn):
    """Колонка на сянка пада, щом един мач не излезе; минава, когато всичките са излезли."""
    ensure(conn)
    done = 0
    for r in conn.execute("SELECT day, idx, legs_json FROM columns_shadow WHERE idx > 0 AND passed IS NULL").fetchall():
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
        conn.execute("UPDATE columns_shadow SET passed = ? WHERE day = ? AND idx = ?", (passed, r["day"], r["idx"]))
        done += 1
    conn.commit()
    return done


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
    return out
