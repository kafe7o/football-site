"""
Колонки за деня (собственикът, 2026-10-04: „таб Колонки - всеки ден с най-новата информация, да не зависят едно от
друго, по логиката на майстора“).

Колонката е няколко мача, от които ТРЯБВА да излязат всичките. Затова:
  - вземаме само „Прогнозата на робота“ (една за мач, bets/robot.one_pick) с шанс поне MIN_P - по една на мач;
  - без дерби, без мачове без собствена оценка на робота и без картони/корнери (за тях няма истински коефициенти);
  - само от НАЙ-СИГУРНИТЕ мачове (bets/sure.py, от 2026-10-08): третината с най-голям шанс на първенство за деня;
  - в колонката най-много един мач от първенство (иначе мачовете имат обща причина да паднат - 4 аржентински
    „под 2.5“); мачовете се подреждат по шанс, всеки е най-много в една колонка - колонките на деня не зависят една
    от друга;
  - показва се шансът на колонката (произведение), поправен с ИЗМЕРЕНОТО надценяване на колонките, коефициентът ѝ и
    колко от 1 € се връщат средно - БЕЗ размер на залога.
Параметрите са избрани по research/columns_backtest.py (1091 дни, 2023-2026): колонка от 3 минава в 29% (роботът
казва 36%), връща средно 0.8 € от 1 €. Не е „сигурен залог“: маржът на букмейкъра се умножава на всеки мач.

Записът - ПО РЕДА НА МАЙСТОРА (04.10: „колонките ги правя петък, събота, неделя, понеделник; вторник за вторник-четвъртък,
петък за петък-понеделник“; от 09.10): колонките се правят за цял БЛОК - във вторник (вторник-четвъртък) и в петък (петък-понеделник),
в първото пускане след 07:00, и после не се променят; уреждат се, когато мачовете им свършат (lock/settle). Мачовете за следващите
дни на блока са предварителните им прогнози в момента на записа (същият код като на сайта) - кракът пази избора и белега си.
Предварителните колонки за следващия блок се смятат наново на всяко пускане и не са запис.
"""

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, robot, rules

SOFIA = ZoneInfo("Europe/Sofia")
MIN_P = 0.65          # шанс по робота за мач в колонка
SIZE = 3              # мача в колонка
MAX_COLUMNS = 4       # най-много колонки на ден от блока (вторник-четвъртък: 12, петък-понеделник: 16)
WINDOWS = {1: 3, 4: 4}  # вторник -> 3 дни (вторник-четвъртък), петък -> 4 дни (петък-понеделник) - майсторът, 04.10
DIVERSIFY = "league"  # един мач на първенство
# роботът казва 36% за колонка от 3, излиза 29%: 0.79 = 0.925 на мач (и 0.845 при 2 мача = 0.92) - по-голямо
# надценяване, отколкото за единичните прогнози (0.965), защото колонките събират най-вероятните избори
CALIBRATION = 0.92


def family(sel):
    if sel in robot.HANDICAPS or sel in ("1", "X", "2", "1X", "X2", "12"):
        return "result"
    if sel in ("GG", "NG"):
        return "btts"
    return "goals"


def day_of(kickoff_iso):
    """„Денят“ на прогнозите започва в 07:00 българско време - като в bets/tips.py."""
    local = datetime.fromisoformat(kickoff_iso).astimezone(SOFIA) - timedelta(hours=7)
    return local.date().isoformat()


def day_bounds(day):
    start = datetime.fromisoformat(day).replace(hour=7, tzinfo=SOFIA)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def window_of(day):
    """(първият ден, [дните]) на блока, в който е денят: вторник-четвъртък или петък-понеделник."""
    d = date.fromisoformat(day)
    start = d - timedelta(days={1: 0, 2: 1, 3: 2, 4: 0, 5: 1, 6: 2, 0: 3}[d.weekday()])
    return start.isoformat(), [(start + timedelta(days=i)).isoformat() for i in range(WINDOWS[start.weekday()])]


def next_window(day):
    """Блокът след този, в който е денят."""
    _, days = window_of(day)
    return window_of((date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat())


def candidates(matches, min_p=MIN_P, need_sure=True):
    """Мачовете, от които може да се прави колонка: [{id, league, kickoff, home, away, sel, p, odds, src}].
    need_sure=False - само за проверката назад на старото правило (research/columns_backtest.py)."""
    out = []
    for m in matches:
        one = m.get("one")
        if not one or rules.match_block(m.get("flags"), m.get("basis")):
            continue                                       # дерби / без собствена оценка (bets/rules.py)
        if need_sure and rules.sure_block(m.get("flags")):
            continue                                       # само най-сигурните мачове (bets/sure.py)
        if one["p"] < min_p or not one.get("odds") or rules.pick_block("column", one["sel"], one["p"], one["odds"], m["league"]):
            continue
        out.append({"id": m.get("id") or m.get("i"), "league": m["league"], "kickoff": m["kickoff"],
                    "home": m.get("home_src") or m["home"], "away": m.get("away_src") or m["away"],
                    "sel": one["sel"], "p": one["p"], "odds": one["odds"], "src": one.get("src"),
                    "sure": (m.get("flags") or {}).get("sure")})       # белегът „топ шанс“ в момента на избора
    return sorted(out, key=lambda c: -c["p"])


def build(cands, size=SIZE, max_columns=MAX_COLUMNS, diversify=DIVERSIFY):
    """Колонките за един ден: списък от списъци с кандидати. Всеки мач е най-много в една колонка."""
    used, columns = set(), []
    for _ in range(max_columns):
        col, leagues, fams = [], set(), []
        for c in cands:
            if c["id"] in used or len(col) == size:
                continue
            fam = family(c["sel"])
            if diversify in (True, "full") and (c["league"] in leagues or fam in fams):
                continue
            if diversify == "league" and c["league"] in leagues:          # един мач на първенство
                continue
            if diversify == "family2" and (c["league"] in leagues or fams.count(fam) >= 2):
                continue
            col.append(c)
            leagues.add(c["league"])
            fams.append(fam)
        if len(col) < size:
            break                                         # няма достатъчно различни мачове - по-къса колонка не правим
        used.update(c["id"] for c in col)
        columns.append(sorted(col, key=lambda c: c["kickoff"]))
    return columns


def summary(col):
    """Числата на колонката: коефициент, шанс по робота, поправен шанс, очакван връщане от 1 €."""
    odds = claimed = 1.0
    for c in col:
        odds *= c["odds"]
        claimed *= c["p"]
    honest = claimed * CALIBRATION ** len(col)
    return {"odds": round(odds, 2), "claimed": round(claimed, 4), "honest": round(honest, 4),
            "return": round(honest * odds, 3), "book": all(c.get("src") == "book" for c in col)}


def for_days(matches, skip_day=None, days=2):
    """{ден: [колонки]} за предстоящите мачове по часовника на прогнозите (без skip_day - записания ден)."""
    by_day = {}
    for m in matches:
        d = day_of(m["kickoff"])
        if d != skip_day:
            by_day.setdefault(d, []).append(m)
    out = {}
    for day in sorted(by_day)[:days]:
        out[day] = [{"legs": col, **summary(col)} for col in build(candidates(by_day[day]))]
    return out


def window_columns(matches, days, now):
    """Колонките на един блок от мачовете, които още не са започнали (до MAX_COLUMNS на ден от блока)."""
    sel = [m for m in matches if day_of(m["kickoff"]) in days and datetime.fromisoformat(m["kickoff"]) > now]
    return build(candidates(sel), max_columns=MAX_COLUMNS * len(days))


def for_windows(matches, now, locked_start=None):
    """Предварителните колонки: текущият блок (ако още не е записан) и следващият. {първи ден: {days, columns}}."""
    out = {}
    cur = window_of(day_of(now.isoformat()))
    for start, days in (cur, next_window(cur[0])):
        if start == locked_start:
            continue
        out[start] = {"days": days, "columns": [{"legs": col, **summary(col)} for col in window_columns(matches, days, now)]}
    return out


# ---------- запис и уреждане (таблицата columns) ----------

def lock(conn, now=None, upcoming=None):
    """Записът на колонките за БЛОКА (майсторът: вторник за вторник-четвъртък, петък за петък-понеделник) - в първото пускане след
    07:00 в първия му ден (ако то падне - в следващото, само с още незапочналите мачове). Веднъж за блок; не се променят.
    Днешните мачове - от записа (tips, с белега „топ шанс“ от сутринта); следващите дни на блока - предварителните прогнози
    (upcoming = tips.preview, същото като на сайта). Кракът пази избора, шанса, коефициента и белега си."""
    now = now or datetime.now(timezone.utc)
    start, days = window_of(day_of(now.isoformat()))
    if conn.execute("SELECT 1 FROM columns WHERE day = ? AND idx = 0", (start,)).fetchone():
        return 0
    recorded, matches = set(), []
    lo, hi = day_bounds(days[0])[0], day_bounds(days[-1])[1]
    for r in conn.execute("SELECT t.*, f.home_src, f.away_src FROM tips t LEFT JOIN fixtures f ON f.id = t.fixture_id "
                          "WHERE t.kickoff >= ? AND t.kickoff < ? AND t.basis = 'model'",
                          (max(lo, now).isoformat(), hi.isoformat())).fetchall():
        picks, flags = json.loads(r["picks_json"]), json.loads(r["flags_json"] or "{}")
        recorded.add(r["fixture_id"])
        matches.append({"id": r["fixture_id"], "league": r["league"], "kickoff": r["kickoff"], "home": r["home"],
                        "away": r["away"], "home_src": r["home_src"], "away_src": r["away_src"], "basis": r["basis"],
                        "one": picks.get("one"), "flags": flags})
    if upcoming is None:
        from . import tips
        upcoming = tips.preview(conn, now, days=len(days) + 1)
    for m in upcoming:
        if m["id"] in recorded or m.get("basis") != "model":
            continue
        matches.append({"id": m["id"], "league": m["league"], "kickoff": m["kickoff"], "home": m["home"], "away": m["away"],
                        "home_src": m.get("home_src"), "away_src": m.get("away_src"), "basis": "model", "one": m.get("one"),
                        "flags": m.get("flags") or {}})
    cols = window_columns(matches, days, now)
    for i, col in enumerate(cols, 1):
        conn.execute("INSERT OR IGNORE INTO columns (day, idx, legs_json, summary_json, locked_at) VALUES (?, ?, ?, ?, ?)",
                     (start, i, json.dumps(col, ensure_ascii=False), json.dumps({**summary(col), "days": days}),
                      now.isoformat(timespec="seconds")))
    conn.execute("INSERT OR IGNORE INTO columns (day, idx, legs_json, summary_json, locked_at) VALUES (?, 0, '[]', ?, ?)",
                 (start, json.dumps({"days": days}), now.isoformat(timespec="seconds")))     # маркер „блокът е обработен“
    conn.commit()
    return len(cols)


def sync_manual(conn):
    """Ръчно записаните колонки на собственика (data/manual_columns.json) - в таблицата с белег manual. Веднъж;
    не се броят в мерките на автоматичните колонки."""
    path = config.DATA_DIR / "manual_columns.json"
    if not path.exists():
        return 0
    added = 0
    for i, m in enumerate(json.loads(path.read_text(encoding="utf-8"))):
        idx = 100 + i
        s = {**summary(m["legs"]), "manual": True, "name": m.get("name"), "note": m.get("note")}
        legs = json.dumps(m["legs"], ensure_ascii=False)
        row = conn.execute("SELECT legs_json FROM columns WHERE day = ? AND idx = ?", (m["day"], idx)).fetchone()
        if row is None:
            conn.execute("INSERT INTO columns (day, idx, legs_json, summary_json, locked_at) VALUES (?, ?, ?, ?, ?)",
                         (m["day"], idx, legs, json.dumps(s), m["placed_at"]))
            added += 1
        elif row["legs_json"] != legs:
            # ръчният запис се допълва от собственика (не е автоматична колонка) - уреждането започва наново
            conn.execute("UPDATE columns SET legs_json = ?, summary_json = ?, passed = NULL WHERE day = ? AND idx = ?",
                         (legs, json.dumps(s), m["day"], idx))
            added += 1
    conn.commit()
    return added


def settle(conn):
    """Уреждане: колонка пада щом един мач не излезе; минава, когато всичките са уредени и излезли."""
    done = 0
    for r in conn.execute("SELECT day, idx, legs_json FROM columns WHERE idx > 0 AND passed IS NULL").fetchall():
        legs, state = json.loads(r["legs_json"]), []
        for leg in legs:
            t = conn.execute("SELECT hg, ag FROM tips WHERE fixture_id = ?", (leg["id"],)).fetchone()
            state.append(None if not t or t["hg"] is None else bool(robot.hit_any(leg["sel"], t["hg"], t["ag"])))
        if any(s is False for s in state):
            passed = 0
        elif all(s is True for s in state):
            passed = 1
        else:
            continue
        conn.execute("UPDATE columns SET passed = ? WHERE day = ? AND idx = ?", (passed, r["day"], r["idx"]))
        done += 1
    conn.commit()
    return done


def record(conn, days=30):
    """Записаните колонки за сайта: по дни, с уреден ли е всеки мач, и общо колко минават."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    out, total, passed, claimed, honest = [], 0, 0, 0.0, 0.0
    for r in conn.execute("SELECT * FROM columns WHERE idx > 0 AND day >= ? ORDER BY day DESC, idx", (since,)).fetchall():
        legs, s = json.loads(r["legs_json"]), json.loads(r["summary_json"])
        for leg in legs:
            t = conn.execute("SELECT hg, ag FROM tips WHERE fixture_id = ?", (leg["id"],)).fetchone()
            leg["hit"] = None if not t or t["hg"] is None else bool(robot.hit_any(leg["sel"], t["hg"], t["ag"]))
            leg["score"] = None if not t or t["hg"] is None else [t["hg"], t["ag"]]
        out.append({"day": r["day"], "idx": r["idx"], "legs": legs, "passed": r["passed"], **s})
        if r["passed"] is not None and not s.get("manual"):
            total += 1
            passed += r["passed"]
            claimed += s.get("claimed", 0)
            honest += s.get("honest", 0)
    return {"columns": out, "n": total, "passed": passed, "claimed": claimed / total if total else None,
            "honest": honest / total if total else None}
