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

Записът - ДЕН ЗА ДЕН (майсторът, 09.10: „ако искаш му кажи ден за ден; нямаме бърза работа“; собственикът: „ден за ден да се вадят,
не предварително и несигурно“): всяка сутрин (първото пускане след 07:00) се записват колонките за СЪЩИЯ ден - само от записаните
сутринта прогнози (tips, с белега „топ шанс“ от записа) - и после не се променят; уреждат се, когато мачовете им свършат (lock/settle).
Предварителни колонки за следващите дни НЯМА. Блокът петък-понеделник, записан предварително в нощта срещу 09.10 по предишния ред
(„един ден преди“), е оттеглен: редовете му не се трият, а са на idx 200+ с белег withdrawn - не се показват, не се уреждат и не се броят.
"""

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, robot, rules

SOFIA = ZoneInfo("Europe/Sofia")
MIN_P = 0.65          # шанс по робота за мач в колонка
SIZE = 3              # мача в колонка
MAX_COLUMNS = 4       # най-много колонки на ден
WITHDRAWN = 200       # оттеглените колонки (записани предварително по предишния ред) - idx 200+
WINDOWS = {1: 3, 4: 4}  # блоковете вторник-четвъртък / петък-понеделник - само за проверката назад (research/columns_backtest.py)
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


def col_day(col):
    """За кой ден е колонката (денят на мачовете ѝ)."""
    return col.get("cday") or day_of(col["legs"][0]["kickoff"])


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


# ---------- запис и уреждане (таблицата columns) ----------

def withdraw_advance(conn):
    """Колонките, записани ПРЕДВАРИТЕЛНО (маркер с „days“ - блокът по предишния ред), се оттеглят: местят се на idx 200+ с белег
    withdrawn. Нищо не се трие. Еднократно - после такива маркери няма."""
    moved = 0
    for m in conn.execute("SELECT day, summary_json FROM columns WHERE idx = 0").fetchall():
        if "days" not in json.loads(m["summary_json"] or "{}"):
            continue
        for r in conn.execute("SELECT idx, summary_json FROM columns WHERE day = ? AND idx < 100", (m["day"],)).fetchall():
            s = {**json.loads(r["summary_json"] or "{}"), "withdrawn": "майсторът, 09.10: колонките са ден за ден, не предварително"}
            conn.execute("UPDATE columns SET idx = ?, summary_json = ? WHERE day = ? AND idx = ?",
                         (WITHDRAWN + r["idx"], json.dumps(s, ensure_ascii=False), m["day"], r["idx"]))
            moved += 1
    conn.commit()
    return moved


def lock(conn, now=None, upcoming=None):
    """Колонките за ДЕНЯ - ден за ден: в първото пускане след 07:00, само от записаните сутринта прогнози за деня (tips, с белега
    „топ шанс“ от записа) и само с незапочналите мачове. Веднъж на ден; не се променят. upcoming не се ползва - предварителни колонки няма."""
    now = now or datetime.now(timezone.utc)
    withdraw_advance(conn)
    day = day_of(now.isoformat())
    if conn.execute("SELECT 1 FROM columns WHERE day = ? AND idx = 0", (day,)).fetchone():
        return 0
    start, end = day_bounds(day)
    rows = conn.execute("SELECT t.*, f.home_src, f.away_src FROM tips t LEFT JOIN fixtures f ON f.id = t.fixture_id "
                        "WHERE t.kickoff >= ? AND t.kickoff < ? AND t.basis = 'model'",
                        (max(start, now).isoformat(), end.isoformat())).fetchall()
    if not rows:
        return 0                                       # записът на прогнозите за деня още не е станал - следващото пускане
    matches = []
    for r in rows:
        picks, flags = json.loads(r["picks_json"]), json.loads(r["flags_json"] or "{}")
        matches.append({"id": r["fixture_id"], "league": r["league"], "kickoff": r["kickoff"], "home": r["home"],
                        "away": r["away"], "home_src": r["home_src"], "away_src": r["away_src"], "basis": r["basis"],
                        "one": picks.get("one"), "flags": flags})
    cols = build(candidates(matches))
    for i, col in enumerate(cols, 1):
        conn.execute("INSERT OR IGNORE INTO columns (day, idx, legs_json, summary_json, locked_at) VALUES (?, ?, ?, ?, ?)",
                     (day, i, json.dumps(col, ensure_ascii=False), json.dumps({**summary(col), "cday": day}),
                      now.isoformat(timespec="seconds")))
    conn.execute("INSERT OR IGNORE INTO columns (day, idx, legs_json, summary_json, locked_at) VALUES (?, 0, '[]', ?, ?)",
                 (day, json.dumps({"cday": day}), now.isoformat(timespec="seconds")))       # маркер „денят е обработен“
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
        if m.get("hidden"):
            # скрита от собственика: не се показва и не се брои (idx 300+), но не се трие - записът се пази
            row = conn.execute("SELECT summary_json FROM columns WHERE day = ? AND idx = ?", (m["day"], idx)).fetchone()
            if row is not None:
                s = {**json.loads(row["summary_json"] or "{}"), "hidden": m["hidden"]}
                conn.execute("UPDATE columns SET idx = ?, summary_json = ? WHERE day = ? AND idx = ?",
                             (300 + i, json.dumps(s, ensure_ascii=False), m["day"], idx))
                added += 1
            continue
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
    """Уреждане: колонка пада щом един мач не излезе; минава, когато всичките са уредени и излезли.
    Колонка с отложен/прекъснат мач (tips.mark_status) НЕ се уреждава автоматично: мачът няма резултат, затова колонката не минава
    и с останалите - дали минава без него е решение на собственика, правило за анулиране няма (2026-10-11). Падне ли друг неин мач,
    колонката пада и така - това не зависи от отложения."""
    done = 0
    for r in conn.execute("SELECT day, idx, legs_json FROM columns WHERE idx > 0 AND idx < ? AND passed IS NULL", (WITHDRAWN,)).fetchall():
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
    since = (datetime.now(timezone.utc) - timedelta(days=days + 4)).date().isoformat()     # + блокът (до 4 дни)
    out, total, passed, claimed, honest = [], 0, 0, 0.0, 0.0
    for r in conn.execute("SELECT * FROM columns WHERE idx > 0 AND idx < ? AND day >= ? ORDER BY day DESC, idx", (WITHDRAWN, since)).fetchall():
        legs, s = json.loads(r["legs_json"]), json.loads(r["summary_json"])
        for leg in legs:
            t = conn.execute("SELECT hg, ag, flags_json FROM tips WHERE fixture_id = ?", (leg["id"],)).fetchone()
            leg["hit"] = None if not t or t["hg"] is None else bool(robot.hit_any(leg["sel"], t["hg"], t["ag"]))
            leg["score"] = None if not t or t["hg"] is None else [t["hg"], t["ag"]]
            fl = json.loads(t["flags_json"] or "{}") if t and t["hg"] is None else {}
            if fl.get("postponed"):
                # отложен/прекъснат мач (tips.mark_status): [код на API-Football, етикет, от кога, нов начален час]
                st = fl.get("status") or {}
                leg["st"] = [st.get("c"), st.get("bg"), st.get("since"), st.get("to")]
        out.append({"day": r["day"], "idx": r["idx"], "legs": legs, "passed": r["passed"], "locked_at": r["locked_at"], **s})
        if r["passed"] is not None and not s.get("manual"):
            total += 1
            passed += r["passed"]
            claimed += s.get("claimed", 0)
            honest += s.get("honest", 0)
    return {"columns": out, "n": total, "passed": passed, "claimed": claimed / total if total else None,
            "honest": honest / total if total else None}
