"""
Бонус анализ час преди всеки мач от топ 5 първенствата (указание на професионалиста, 2026-10-01:
„за всеки мач от топ 5 да изготвя допълнителен анализ час преди да е започнал и да ми дава известие
дали след бонус анализа има промени; сутрин да погледне кога са мачовете и да знае кога час по-рано
да прави анализа“).

Как става точно час преди, а не „някъде около“:
  - сутрин (notify.morning) известието казва кога ще са бонус анализите за деня;
  - всяко часово пускане (run.py cloud) поглежда мачовете от топ 5, които започват в следващите
    DISPATCH_AHEAD минути, и за всеки начален час поръчва отделна задача (.github/workflows/bonus.yml);
  - задачата изчаква до точно LEAD минути преди началото и тогава прави анализа (run.py bonus).

Какво може да се промени за час: моделът на робота не се мени в рамките на деня (нови резултати
няма), затова шансовете му остават същите. Мени се пазарът - коефициентите реагират на съставите,
контузиите и парите. Бонус анализът сравнява със сутрешния запис:
  - коефициентите на 1, X, 2 и на прогнозите (промяна от MOVE_ODDS и повече);
  - шансовете на букмейкърите (промяна от MOVE_PROB и повече);
  - рисковата и по-сигурната прогноза при новите коефициенти (граници 1.50-1.80 / от 2.50, bets/robot.py);
  - съдията (ако е обявен след сутринта).
Сутрешният запис НЕ се пипа - бонусът е допълнителен и се мери отделно (таб Резултати).

Резултатът: bonus/<начален час UTC>.json в хранилището на сайта (всеки начален час - собствен
файл, за да не се сблъскват задачите) и известие по ntfy.

ПРЕГЛЕД НА КОЛОНКА (майсторът, 2026-10-10: „да не правиш предварителен преглед на определен мач, ако е в колонка; просто преглед
1 час преди най-ранния мач на колонката - на цялата колонка; безсмислено е да гледаш мача от 17:00 в 16:00, когато колонката е
пусната в 07:40 и първият ѝ мач е в 08:00“; „не от всички мачове, а от определената колонка“):
  - мач, който е в колонка, НЕ получава отделен бонус анализ (slots го пропуска);
  - за всяка колонка се поръчва ЕДНА задача (bonus.yml с column=ден:номер): чака до час преди най-ранния ѝ мач (ако колонката е
    записана по-късно - работи веднага) и прегледа САМО нейните мачове: пресни цени, отсъстващи и състави (API-Football), дали
    изборът още е по правилата (коефициент от 1.40), промените спрямо сутрешния запис; colreview/<ден>-<номер>.json + известие;
  - 20 минути преди най-ранния мач - допълнение (в първата проба 36 минути преди мача съставите още не бяха обявени) - със съставите, които вече са обявени (column_lineups).
Шансовете на робота не се менят от съставите - прегледът казва какво е станало, не „залагай / не залагай“.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import analysis, apifootball, config, db, derbies, notify, odds_api, prices as P, robot, rules, tips
from .leagues import LEAGUES

log = logging.getLogger(__name__)

TOP5 = ("E0", "SP1", "I1", "D1", "F1")
LEAD = timedelta(minutes=60)
DISPATCH_AHEAD = timedelta(minutes=125)    # часовото пускане е на ~50-55 мин - всеки мач попада веднъж
LATE_LIMIT = timedelta(minutes=5)          # по-късно от това преди мача - бонус няма
MOVE_ODDS = 0.05                           # 5% промяна на коефициента
MOVE_PROB = 0.03                           # 3 процентни пункта в шанса на букмейкърите
LINEUP_LEAD = timedelta(minutes=20)        # допълнение със съставите на колонката - толкова преди най-ранния ѝ мач
REVIEW_DIR = "colreview"
SOFIA = ZoneInfo("Europe/Sofia")


def slot_name(kickoff):
    """Името на файла за началния час: 2026-10-10T1400 (UTC)."""
    return datetime.fromisoformat(kickoff).astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M")


def column_leg_ids(conn, now):
    """Мачовете от колонки (последните дни) - за тях няма отделен преглед: прегледа го прави колонката."""
    since = (now - timedelta(days=2)).date().isoformat()
    ids = set()
    for r in conn.execute("SELECT legs_json FROM columns WHERE idx > 0 AND idx < 200 AND day >= ?", (since,)):
        ids.update(l["id"] for l in json.loads(r["legs_json"]))
    return ids


def slots(conn, now, ahead=timedelta(hours=24)):
    """{начален час (UTC ISO): [мачовете от топ 5]} за следващите ahead - без мачовете от колонките."""
    out = {}
    q = ",".join("?" * len(TOP5))
    in_columns = column_leg_ids(conn, now)
    for f in conn.execute(f"SELECT * FROM fixtures WHERE league IN ({q}) AND kickoff > ? AND kickoff <= ? ORDER BY kickoff",
                          (*TOP5, now.isoformat(), (now + ahead).isoformat())):
        if f["id"] in in_columns:
            continue
        start = datetime.fromisoformat(f["kickoff"]).astimezone(timezone.utc).isoformat(timespec="minutes")
        out.setdefault(start, []).append(f)
    return out


def schedule(conn, now=None):
    """Поръчва бонус анализите за началните часове в следващите DISPATCH_AHEAD минути (веднъж на час)."""
    now = now or datetime.now(timezone.utc)
    ordered = 0
    for start, rows in slots(conn, now, DISPATCH_AHEAD).items():
        if datetime.fromisoformat(start) - now < LATE_LIMIT:
            continue
        key = f"bonus_dispatched:{start}"
        if db.get_meta(conn, key):
            continue
        if dispatch(start):
            db.set_meta(conn, key, now.isoformat(timespec="seconds"))
            ordered += 1
            log.info("Бонус анализ поръчан за %s (%d мача)", start, len(rows))
    # преглед на колонките: ЕДНА задача на колонка, час преди най-ранния ѝ мач (веднага, ако колонката е записана по-късно)
    since = (now - timedelta(days=1)).date().isoformat()
    for r in conn.execute("SELECT day, idx, legs_json FROM columns WHERE idx > 0 AND idx < 200 AND passed IS NULL AND day >= ?", (since,)).fetchall():
        legs = json.loads(r["legs_json"])
        if not legs:
            continue
        first = column_first(legs)
        if first - now < LATE_LIMIT or first - now > DISPATCH_AHEAD:
            continue
        key = f"bonus_dispatched:col:{r['day']}:{r['idx']}"
        if db.get_meta(conn, key):
            continue
        if dispatch(first.isoformat(timespec="minutes"), column=f"{r['day']}:{r['idx']}"):
            db.set_meta(conn, key, now.isoformat(timespec="seconds"))
            ordered += 1
            log.info("Преглед на колонка %s #%s поръчан - най-ранният мач е %s", r["day"], r["idx"], first.isoformat(timespec="minutes"))
    return ordered


def dispatch(start, column=None):
    """Пуска .github/workflows/bonus.yml за началния час (и за колонка ден:номер). True при успех."""
    from .publish import api
    token = os.environ.get("GH_TOKEN") or config.GITHUB_TOKEN
    repo = os.environ.get("GITHUB_REPOSITORY") or config.GITHUB_REPO
    if not token or not repo:
        log.error("Бонус анализът не е поръчан: няма токен или хранилище")
        return False
    inputs = {"slot": start, **({"column": column} if column else {})}
    status, body = api("POST", f"/repos/{repo}/actions/workflows/bonus.yml/dispatches", token, {"ref": "main", "inputs": inputs})
    if status not in (200, 201, 204):
        log.error("Бонус анализът за %s не е поръчан: %s %s", start, status, body.get("error", ""))
        return False
    return True


# ---------- самият анализ ----------

def fresh_prices(sport):
    """Пресните цени на лигата от odds API (2 кредита): {id на мача: цените}."""
    try:
        data = odds_api.odds(sport, markets="h2h,totals", cache_minutes=0)
    except RuntimeError as e:
        log.error("%s: пресните цени не се изтеглиха - %s", sport, e)
        return {}
    out = {}
    for event in data:
        p = P.from_event(event)
        if p:
            p["src"] = "odds-api"
            out[event["id"]] = p
    return out


def pct(x):
    return f"{x * 100:.0f}%"


def compare(fx, record, now_prices, referee, base, robot_now=None):
    """Сравнение сутрин -> сега за един мач. Връща (промени като текст, новите прогнози, числата).
    Без сутрешен запис (напр. мачът се е появил след 07:00) - прогнозите се смятат сега от модела
    (robot_now) и промени няма: това е първият анализ."""
    robot_p = (json.loads(record["probs_json"])["robot"] if record and record["basis"] == "model" else None) or robot_now
    derby = derbies.is_derby(fx["league"], fx["home"], fx["away"])
    if rules.match_block({"derby": derby}, "model"):
        robot_p = None                      # дерби: без прогноза на робота, и в бонус анализа (bets/rules.py)
    morning_prices = json.loads(record["prices_json"]) if record and record["prices_json"] else None
    picks = json.loads(record["picks_json"]) if record else {}
    m_avg, n_avg = (morning_prices or {}).get("avg") or {}, (now_prices or {}).get("avg") or {}
    m_fair, n_fair = P.fair(morning_prices), P.fair(now_prices)
    changes = []
    moves = {}
    for s in ("1", "X", "2"):
        a, b = m_avg.get(s), n_avg.get(s)
        if a and b:
            moves[s] = [a, b]
            if abs(b / a - 1) >= MOVE_ODDS:
                changes.append(f"коефициентът на {s}: {a:.2f} -> {b:.2f}")
    if m_fair and n_fair:
        for s in ("1", "X", "2"):
            if abs(n_fair[s] - m_fair[s]) >= MOVE_PROB:
                changes.append(f"букмейкърите за {s}: {pct(m_fair[s])} -> {pct(n_fair[s])}")
    new = {}
    if robot_p:
        new["one"] = robot.one_pick(robot_p, n_avg or m_avg, picks.get("extras"), fx["league"])
        new["safer"] = robot.safe_by_odds(robot_p, n_avg or m_avg, fx["league"], against=[new["one"]["sel"]] if new["one"] else ())
        new["risky"] = (robot.risky_sign(robot_p, n_avg or m_avg, fx["league"], [x["sel"] for x in (new["safer"], new["one"]) if x])
                        if robot.RISKY_RULE == "under50" else
                        robot.risky_by_odds(robot_p, base, n_avg or m_avg,
                                            [x["sel"] for x in (new["safer"], new["one"]) if x]
                                            if robot.RISKY_CONSISTENT else ()))
        for kind, name in (("one", "прогнозата"), ("risky", "рисковата"), ("safer", "по-сигурната")):
            old, cur = picks.get(kind), new.get(kind)
            old_sel, cur_sel = (old or {}).get("sel"), (cur or {}).get("sel")
            if old_sel != cur_sel:
                changes.append(f"{name}: {robot.label(old_sel) if old_sel else 'няма'} -> "
                               f"{robot.label(cur_sel) + ' @' + format(cur['odds'], '.2f') if cur_sel else 'няма (нищо в границите)'}")
            elif (cur and old and old.get("odds") and cur.get("odds") and old.get("src") == cur.get("src") == "book"
                  and abs(cur["odds"] / old["odds"] - 1) >= MOVE_ODDS):
                # само букмейкър срещу букмейкър: честният коефициент на робота от сутринта не е „движение“
                changes.append(f"{name} {robot.label(cur_sel)}: коеф. {old['odds']:.2f} -> {cur['odds']:.2f}")
    morning_ref = (json.loads(record["flags_json"] or "{}").get("referee") if record else None)
    if not record:
        changes = []                      # първи анализ - няма с какво да се сравни
    elif not m_avg and n_avg:
        changes.append("сутринта още нямаше коефициенти от букмейкърите - сега прогнозите са по техните")
    if referee and referee != morning_ref:
        changes.append(f"съдия: {referee}")
    return changes, new, {"moves": moves, "fair_morning": m_fair, "fair_now": n_fair, "derby": derby}


def run(conn, start, now=None, send=True, write=True, horizon=timedelta(hours=30), prefix=""):
    """Бонус анализът за мачовете от топ 5 с начален час start (UTC ISO). Пише bonus/<час>.json.
    send=False, write=False - проба (bonus.yml с test: true): без известие и без файл."""
    now = now or datetime.now(timezone.utc)
    start = datetime.fromisoformat(start).astimezone(timezone.utc).isoformat(timespec="minutes")
    rows = slots(conn, now - timedelta(hours=3), horizon).get(start, [])
    if not rows:
        log.warning("Бонус анализ %s: няма мачове от топ 5 в този час", start)
        return None
    by_sport = {}
    for f in rows:
        by_sport.setdefault(LEAGUES[f["league"]].sport, []).append(f)
    fresh = {}
    for sport in by_sport:
        fresh.update(fresh_prices(sport))
    ctx = analysis.Context(conn, now)
    out = {"slot": start, "made_at": now.isoformat(timespec="seconds"), "matches": []}
    for f in rows:
        record = conn.execute("SELECT * FROM tips WHERE fixture_id = ?", (f["id"],)).fetchone()
        now_prices = fresh.get(f["id"]) or P.for_fixture(conn, f)
        referee = tips.referee_of(conn, f)
        fitted = tips.fitted_model(conn, f["league"], now)
        robot_now, used = tips.own_or_pyramid(conn, f, fitted, now)
        fitted = used or fitted
        changes, new, nums = compare(f, record, now_prices, referee, ctx.base(f["league"]), robot_now)
        an = analysis.build(ctx, {**dict(f), "flags": {}}, fitted, (f["home_src"] or f["home"], f["away_src"] or f["away"]), referee)
        sq = apifootball.squads_for(conn, f, now)               # отсъстващи и състав (API-Football) - само информация
        out["matches"].append({
            "id": f["id"], "league": f["league"], "kickoff": f["kickoff"],
            "home": f["home_src"] or f["home"], "away": f["away_src"] or f["away"], "sq": sq,
            "recorded": bool(record), "changes": changes, "new": new,
            # най-сигурен мач (bets/sure.py) - белегът е от записа сутринта
            "sure": bool(record and (json.loads(record["flags_json"] or "{}").get("sure") or {}).get("y")),
            "status": "first" if not record else ("changed" if changes else "same"),
            "odds_now": (now_prices or {}).get("avg"), **nums,
            "referee": (an or {}).get("referee"), "lines": (an or {}).get("text", [])[:3],
            "xg": ((an or {}).get("goals") or {}).get("xg"),
            "cards": {k: (an or {}).get("cards", {}).get(k) for k in ("total", "line")} if (an or {}).get("cards") else None})
    if write:
        folder = config.SITE_DIR / "bonus"
        folder.mkdir(exist_ok=True)
        (folder / f"{slot_name(start)}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        for m in out["matches"]:
            log.info("ПРОБА %s - %s: промени %s", m["home"], m["away"], m["changes"] or "няма")
    if send:
        notify_slot(out, prefix)
    log.info("Бонус анализ %s: %d мача, с промени %d", start, len(out["matches"]),
             sum(1 for m in out["matches"] if m["changes"]))
    return out


def notify_slot(out, prefix=""):
    local = datetime.fromisoformat(out["slot"]).astimezone(SOFIA).strftime("%d.%m %H:%M")
    changed = [m for m in out["matches"] if m["changes"]]
    lines = []
    for m in out["matches"]:
        head = (f"{m['home']} - {m['away']} ({LEAGUES[m['league']].name})" + (" [ДЕРБИ - без прогноза]" if m.get("derby") else "")
                + (" ★ топ шанс" if m.get("sure") else ""))
        cur = []
        for kind, name in (("one", "ПРОГНОЗА"), ("risky", "рискова"), ("safer", "по-сигурна")):
            x = (m.get("new") or {}).get(kind)
            if x:
                cur.append(f"{name} {robot.label(x['sel'])} @{x['odds']:.2f} ({pct(x['p'])})")
        if m.get("status") == "first":
            lines.append(f"ПЪРВИ АНАЛИЗ - {head}: " + (", ".join(cur) if cur else "роботът няма собствена оценка за тези отбори"))
        elif m["changes"]:
            lines.append(f"ПРОМЯНА - {head}: " + "; ".join(m["changes"]) + (". Сега: " + ", ".join(cur) if cur else "."))
        else:
            lines.append(f"Без промяна - {head}" + (": " + ", ".join(cur) if cur else ""))
        # кратък анализ: очаквани голове, картони, съдията
        extra = []
        if m.get("xg"):
            extra.append(f"очаквани голове {m['xg'][0]:.1f}:{m['xg'][1]:.1f}")
        if m.get("cards") and m["cards"].get("total"):
            extra.append(f"очаквани жълти {m['cards']['total']:.1f}")
        ref = m.get("referee") or {}
        if ref.get("last"):
            extra.append(f"съдия {ref['name']} - {ref['last']['yellows']:.1f} жълти/мач")
        if extra:
            lines.append("   " + ", ".join(extra))
        lines += ["   " + t for t in apifootball.describe(m.get("sq"), m["home"], m["away"])]
    first = [m for m in out["matches"] if m.get("status") == "first"]
    state = (f"промени в {len(changed)} от {len(out['matches'])}" if changed
             else f"първи анализ ({len(first)} мача)" if len(first) == len(out["matches"]) else "без промени")
    title = prefix + f"Бонус анализ за {local}: " + state
    return notify.send(title, "\n".join(lines), tags="stopwatch", priority=4 if changed else 3)


# ---------- преглед на цяла колонка ----------

def review_path(day, idx):
    return config.SITE_DIR / REVIEW_DIR / f"{day}-{idx}.json"


def column_first(legs):
    """Началото на най-ранния мач на колонката (UTC)."""
    return min(datetime.fromisoformat(l["kickoff"]).astimezone(timezone.utc) for l in legs)


def run_column(conn, day, idx, now=None, send=True, write=True, prefix=""):
    """Прегледът на колонка (ден, номер): САМО на мачовете ѝ - пресни цени (odds API: 2 кредита на първенство, а в колонката е
    най-много един мач на първенство), отсъстващи и състави (API-Football: заявки само за тези мачове) и сравнение със сутрешния
    запис. Пише colreview/<ден>-<номер>.json."""
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT * FROM columns WHERE day = ? AND idx = ?", (day, idx)).fetchone()
    legs = json.loads(row["legs_json"]) if row else []
    if not legs:
        log.warning("Преглед на колонка %s #%s: няма такава колонка", day, idx)
        return None
    fixtures = {l["id"]: conn.execute("SELECT * FROM fixtures WHERE id = ?", (l["id"],)).fetchone() for l in legs}
    fresh = {}
    for sport in {LEAGUES[l["league"]].sport for l in legs if LEAGUES[l["league"]].sport}:
        fresh.update(fresh_prices(sport))
    ctx = analysis.Context(conn, now)
    out = {"day": day, "idx": idx, "made_at": now.isoformat(timespec="seconds"),
           "first": column_first(legs).isoformat(timespec="minutes"), "legs": []}
    for l in legs:
        f = fixtures[l["id"]]
        item = {"id": l["id"], "league": l["league"], "kickoff": l["kickoff"], "home": l["home"], "away": l["away"],
                "sel": l["sel"], "p": l["p"], "odds": l["odds"], "src": l.get("src"), "odds_now": None,
                "changes": [], "flags": [], "sq": None}
        if f is None:
            item["flags"].append("мачът вече го няма в разписанието (отложен или променен) - провери го")
        else:
            record = conn.execute("SELECT * FROM tips WHERE fixture_id = ?", (l["id"],)).fetchone()
            prices = fresh.get(f["id"]) or P.for_fixture(conn, f)
            item["changes"], _new, _nums = compare(f, record, prices, tips.referee_of(conn, f), ctx.base(f["league"]))
            cur = ((prices or {}).get("avg") or {}).get(l["sel"])
            if cur:
                item["odds_now"] = round(cur, 2)
                if l.get("src") == "book" and abs(cur / l["odds"] - 1) >= MOVE_ODDS:
                    item["flags"].append(f"коефициентът на избора е {l['odds']:.2f} -> {cur:.2f}")
                if cur < robot.FLOOR:
                    item["flags"].append(f"коефициентът падна под {robot.FLOOR:.2f} ({cur:.2f}) - вече не е по правилото на майстора")
            item["sq"] = apifootball.squads_for(conn, f, now)
        out["legs"].append(item)
    out["changed"] = sum(1 for x in out["legs"] if x["flags"] or x["changes"])
    out["absent"] = sum(1 for x in out["legs"] if any(((x["sq"] or {}).get("i") or {}).get(s) for s in ("h", "a")))
    if write:
        path = review_path(day, idx)
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    elif not send:
        for x in out["legs"]:
            log.info("ПРОБА %s - %s: %s", x["home"], x["away"], x["flags"] + x["changes"] or "без промяна")
    if send:
        notify_column(out, prefix)
    log.info("Преглед на колонка %s #%s: %d мача, с промяна %d, с отсъстващи %d", day, idx, len(out["legs"]), out["changed"], out["absent"])
    return out


def notify_column(out, prefix=""):
    first = datetime.fromisoformat(out["first"]).astimezone(SOFIA).strftime("%H:%M")
    lines = []
    for x in out["legs"]:
        head = f"{x['home']} - {x['away']} ({LEAGUES[x['league']].name}) {robot.label(x['sel'])} {pct(x['p'])} @{x['odds']:.2f}"
        notes = x["flags"] + x["changes"]
        lines.append(("ВНИМАНИЕ - " if x["flags"] else "ПРОМЯНА - " if x["changes"] else "Без промяна - ") + head
                     + (": " + "; ".join(notes) if notes else ""))
        lines += ["   " + t for t in apifootball.describe(x["sq"], x["home"], x["away"])]
    state = (f"промени в {out['changed']} от {len(out['legs'])} мача" if out["changed"]
             else f"отсъстващи в {out['absent']} от {len(out['legs'])} мача" if out["absent"] else "без промени")
    title = prefix + f"Колонка {out['idx']} - преглед преди първия мач ({first}): {state}"
    lines.append("Шансовете на робота не се менят от съставите. Не е сигурен залог.")
    return notify.send(title, "\n".join(lines), tags="stopwatch", priority=4 if any(x["flags"] for x in out["legs"]) else 3)


def column_lineups(conn, day, idx, now=None, send=True, write=True):
    """20 минути преди най-ранния мач: допълва прегледа със съставите, които вече са обявени (мачовете до 90 минути напред)."""
    now = now or datetime.now(timezone.utc)
    path = review_path(day, idx)
    if not path.exists():
        log.warning("Съставите на колонка %s #%s: няма преглед, към който да се добавят", day, idx)
        return None
    out = json.loads(path.read_text(encoding="utf-8"))
    found = []
    for x in out["legs"]:
        if not (now - timedelta(minutes=5) < datetime.fromisoformat(x["kickoff"]) <= now + timedelta(minutes=90)):
            continue
        f = conn.execute("SELECT * FROM fixtures WHERE id = ?", (x["id"],)).fetchone()
        sq = apifootball.squads_for(conn, f, now) if f is not None else None
        if sq and sq.get("l"):
            x["sq"] = sq
            found.append(x)
    out["lineups_at"] = now.isoformat(timespec="seconds")
    if write:
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if send and found:
        first = datetime.fromisoformat(out["first"]).astimezone(SOFIA).strftime("%H:%M")
        lines = []
        for x in found:
            lines.append(f"{x['home']} - {x['away']} {robot.label(x['sel'])}:")
            lines += ["   " + t for t in apifootball.describe(x["sq"], x["home"], x["away"])]
        notify.send(f"Колонка {idx} - обявени състави преди първия мач ({first})", "\n".join(lines), tags="stopwatch", priority=3)
    log.info("Състави за колонка %s #%s: обявени за %d мача", day, idx, len(found))
    return out


def lineup_wait_seconds(first_kickoff, now=None):
    now = now or datetime.now(timezone.utc)
    return max(0, int((datetime.fromisoformat(first_kickoff) - LINEUP_LEAD - now).total_seconds()))


def wait_seconds(start, now=None):
    """Колко секунди да се изчака до точно LEAD преди началото (0, ако вече е време)."""
    now = now or datetime.now(timezone.utc)
    return max(0, int((datetime.fromisoformat(start) - LEAD - now).total_seconds()))
