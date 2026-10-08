"""
„Питай робота“ - какво мисли роботът за ЕДИН мач (собственикът, 2026-10-08: майсторът пита за мач и иска мнението на бота,
а не неговото; „да включва всички правила и начина на анализ, да няма отклонения от начина до сега“).

    python run.py ask "CFR Cluj" "U. Cluj"                    първенството се намира само
    python run.py ask "Lecce" "Juventus" --league I1           или се казва
    python run.py ask "Home" "Away" --kickoff "2026-10-10 19:00"   час (българско време) - само за деня на мача
    python run.py ask ... --offline                           без връзка с облака: местното копие на базата

Няма НИКАКЪВ втори път за изчисление: командата вика същите функции като часовото пускане на облака -
tips.fitted_model (моделът), tips.forecast (шансове, едната/по-сигурната/рисковата, дерби, „тото“, анализ),
tips.locked_entry (ако мачът вече е записан - показва ЗАПИСА, не нова прогноза), sure.annotate (най-сигурен ли е)
и rules (правилата на майстора). Базата е СВЕЖО копие на базата на облака (същите 3 сезона история като в облака) във
временна папка, която се трие накрая - в нищо реално не се пише, известия не се пращат, кредити на odds API не се харчат.
Ако мачът не е в разписанието на робота (напр. отложен мач или първенство без източник на предстоящи мачове), се
изчислява със същия модел и се казва, че не е в разписанието; такъв отговор не влиза в записа и в статистиката.

Роботът НЕ знае контузии, състави и новини (няма безплатен източник) - отговорът го казва.
"""

import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import analysis, config, db, publish, robot, sure, teams, tips
from .leagues import LEAGUES

log = logging.getLogger(__name__)
SOFIA = ZoneInfo("Europe/Sofia")
HISTORY_DAYS = 400                       # отбор е „познат“, ако е играл в първенството през последната година и нещо


def cloud_copy(workdir, offline=False):
    """Свежо копие на базата на облака във временната папка. (път, откъде е)."""
    target = Path(workdir) / "robot.db"
    if not offline:
        try:
            publish.git("fetch", "-q", "origin", "main", token=config.GITHUB_TOKEN)
            data = subprocess.run(["git", "-C", str(config.SITE_DIR), "show", "origin/main:robot.db"],
                                  capture_output=True, check=True).stdout
            target.write_bytes(data)
            stamp = publish.git("log", "-1", "--format=%cd", "--date=iso", "origin/main")
            return target, f"базата на облака (последна промяна там {stamp[:16]})"
        except (RuntimeError, subprocess.CalledProcessError, OSError) as e:
            log.warning("Няма връзка с облака (%s) - ползвам местното копие", e)
    local = config.SITE_DIR / "robot.db"
    if not local.exists():
        raise SystemExit("Няма база: нито връзка с облака, нито местно копие (python run.py local).")
    shutil.copy2(local, target)
    age = datetime.fromtimestamp(local.stat().st_mtime)
    return target, f"МЕСТНО копие на базата от {age:%d.%m %H:%M} (няма връзка с облака - може да е остаряло)"


def resolve(name, candidates):
    """Името на отбора, както е в базата (същото търсене като при разписанието), или None."""
    low = {c.lower(): c for c in candidates}
    if name.lower() in low:
        return low[name.lower()]
    return teams.match(name, candidates) or teams.best_match(name, candidates) or teams.loose_match(name, candidates)


def find_league(conn, home, away, league, now):
    """(първенство, домакин, гост) както са в базата; при неяснота спира с обяснение."""
    if re.search("[а-яА-Я]", home + away):
        raise SystemExit("Имената на отборите са в базата на латиница (напр. „CFR Cluj“, „U. Cluj“, „Lecce“) - напиши ги така.")
    since = (now - timedelta(days=HISTORY_DAYS)).date().isoformat()
    by = {}
    for r in conn.execute("SELECT league, home_team AS t FROM matches WHERE date >= ? UNION SELECT league, away_team FROM matches WHERE date >= ?",
                          (since, since)):
        by.setdefault(r["league"], set()).add(r["t"])
    found = []
    for code, cands in by.items():
        if league and code != league:
            continue
        h, a = resolve(home, cands), resolve(away, cands)
        if h and a and h != a:
            found.append((code, h, a))
    if not found:
        near = lambda name: sorted({t for c in by.values() for t in c}, key=lambda t: -teams.similar(name, t))[:5]
        raise SystemExit(f"Не намирам и двата отбора в едно първенство. Най-близки до „{home}“: {', '.join(near(home))}; "
                         f"до „{away}“: {', '.join(near(away))}. Пиши ги като в базата (латиница) или добави --league.")
    if len(found) > 1:
        count = lambda code, h, a: conn.execute("SELECT COUNT(*) FROM matches WHERE league = ? AND date >= ? AND (home_team IN (?, ?) OR away_team IN (?, ?))",
                                                (code, since, h, a, h, a)).fetchone()[0]
        scored = sorted(((count(*f), f) for f in found), reverse=True)
        if scored[0][0] == scored[1][0]:
            raise SystemExit("Отборите са в няколко първенства: " + ", ".join(f"{c} ({LEAGUES[c].title})" for c, _, _ in found)
                             + ". Добави --league с кода.")
        found = [scored[0][1]]
    return found[0]


def parse_kickoff(text, now):
    """„2026-10-10 19:00“ (българско време) или „19:00“ (днес) -> UTC; None, ако няма."""
    if not text:
        return None
    text = text.strip()
    if len(text) <= 5:
        local = datetime.combine(now.astimezone(SOFIA).date(), datetime.strptime(text, "%H:%M").time(), tzinfo=SOFIA)
    else:
        local = datetime.fromisoformat(text.replace(" ", "T")).replace(tzinfo=SOFIA)
    return local.astimezone(timezone.utc)


def make_entry(conn, ctx, fitted, dates, fx, now):
    """Един мач като в „Прогнози“: записаният (tips), а ако не е записан - предварителният (tips.forecast)."""
    t = conn.execute("SELECT * FROM tips WHERE fixture_id = ?", (fx["id"],)).fetchone()
    if t:
        return tips.locked_entry(conn, ctx, fx, t, fitted)
    f = tips.forecast(conn, fx, fitted, dates, ctx)
    if f is None:
        return None
    return {"id": fx["id"], "league": fx["league"], "kickoff": fx["kickoff"], "home": fx["home"], "away": fx["away"],
            "home_src": fx["home_src"], "away_src": fx["away_src"], "locked": None, **f}


def pick_line(name, x, home, away):
    if not x:
        return None
    lab = robot.label(x["sel"]).replace("домакинът", home).replace("гостът", away)
    odd = ""
    if x.get("odds"):
        odd = f" · коеф. {x['odds']:.2f}" + (" (по робота - от букмейкър няма)" if x.get("src") == "robot" else "")
    return f"{name}: {lab} - шанс {x['p']:.0%}{odd}"


def read_data(name):
    path = config.DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def report(conn, ctx, e, status, source_note):
    """Отговорът на български: мнение, правилата, най-сигурен ли е, колко да се вярва, анализът."""
    lg = LEAGUES[e["league"]]
    home, away = e.get("home_src") or e["home"], e.get("away_src") or e["away"]
    flags = e.get("flags") or {}
    model = e.get("basis") == "model"
    pr = e["probs"]["robot"] if e.get("probs") else {}
    an = e.get("analysis") or {}
    L = [f"ПИТАЙ РОБОТА: {home} – {away}", f"{lg.title} · {status}", f"({source_note})", ""]

    # --- мнението: по шансовете, без значение дали има съвет ---
    if not model:
        L.append("МНЕНИЕ: роботът няма собствена оценка за тези отбори (няма достатъчно история) - показан е само пазарът, ако го има.")
    if pr.get("1") is not None:
        base = ctx.base(e["league"])
        order = sorted((("1", pr["1"]), ("X", pr["X"]), ("2", pr["2"])), key=lambda x: -x[1])
        name = {"1": home, "X": "равен", "2": away}
        lead, second = order[0], order[1]
        gap = lead[1] - second[1]
        strength = ("ясен фаворит" if lead[1] >= 0.6 else "леко предимство" if gap >= 0.10 else "почти равен мач")
        if model:
            L.append(f"МНЕНИЕ: най-вероятно {name[lead[0]]} ({lead[1]:.0%}) - {strength}; после {name[second[0]]} ({second[1]:.0%}).")
        L.append(f"Шансове: {home} {pr['1']:.0%} · равен {pr['X']:.0%} · {away} {pr['2']:.0%}"
                 f"  (двоен шанс: 1X {pr['1X']:.0%}, X2 {pr['X2']:.0%}, 12 {pr['12']:.0%})")
        L.append(f"Обичайното в {lg.name}: домакин {base['1']:.0%} · равен {base['X']:.0%} · гост {base['2']:.0%}")
    g = an.get("goals")
    if g:
        sc = ", ".join(f"{a}:{b} ({p:.0%})" for a, b, p in g["scores"])
        L.append(f"Очаквани голове {g['xg'][0]:.1f} : {g['xg'][1]:.1f}; най-вероятни резултати {sc}")
        L.append(f"Голове: над 1.5 {g['o15']:.0%} · над 2.5 {g['o25']:.0%} · над 3.5 {g['o35']:.0%} · двата вкарват {g['btts']:.0%}")
    pk = (e.get("prices") or {}).get("avg")
    if pk and all(k in pk for k in ("1", "X", "2")):
        L.append(f"Коефициенти (средни на букмейкърите, {(e['prices'] or {}).get('n', '?')}): 1 {pk['1']:.2f} · X {pk['X']:.2f} · 2 {pk['2']:.2f}")
    else:
        L.append("Коефициенти от букмейкър за този мач: няма (затова не мога да кажа има ли стойност).")
    L.append("")

    # --- правилата на майстора ---
    L.append("ПО ПРАВИЛАТА НА МАЙСТОРА (прогноза за залог):")
    one, safer, risky = e.get("one"), e.get("safer"), e.get("risky")
    if flags.get("derby"):
        L.append("  ДЕРБИ - професионалистът: избягвай. Роботът НЕ дава прогноза; шансовете по-горе са само за сведение.")
    elif not model or not (one or safer or risky):
        L.append(f"  Няма прогноза: {e.get('why') or 'няма събитие по правилата'}.")
    else:
        for line in (pick_line("Прогноза на робота (една)", one, home, away), pick_line("По-сигурна (1.40-1.80)", safer, home, away),
                     pick_line("Рискова (знак 1/2 под 50%)", risky, home, away)):
            if line:
                L.append("  " + line)
        if not risky:
            L.append("  Рискова: няма - ясен фаворит над 50% (фаворитът не е риск) или знакът е блокиран.")
        if not safer:
            L.append("  По-сигурна: няма събитие с коефициент 1.40-1.80 и шанс 50%+.")
    if flags.get("toto"):
        L.append("  Лига „тото“ (Холандия, Австрия): фаворит с коефициент 1.30-1.55 не се дава.")
    if flags.get("after_break"):
        L.append("  Първи кръг след пауза - професионалистът очаква изненади (данните не го потвърждават; отбелязва се и се мери).")

    # --- най-сигурен ли е ---
    s = flags.get("sure")
    if flags.get("derby") or not model or not one:
        L.append("Най-сигурен мач: не може (дерби / без собствена оценка / без прогноза).")
    elif s and s.get("y"):
        L.append(f"Най-сигурен мач: ДА ★ - {sure_pos(s)} (най-сигурни са най-вероятната третина с шанс {sure.MIN_P:.0%}+).")
    elif s:
        L.append(f"Най-сигурен мач: не - {sure_pos(s)}; най-сигурни са най-вероятната третина с шанс {sure.MIN_P:.0%}+.")
    L.append("")

    # --- колко да му вярвам ---
    one_bt = ((read_data("one_backtest.json").get("leagues") or {}).get(e["league"]) or {})
    sb = sure.measured()
    su_lg = ((read_data("sure_backtest.json").get("leagues") or {}).get(e["league"]) or {})
    L.append("КОЛКО ДА СЕ ВЯРВА:")
    if one_bt:
        L.append(f"  В {lg.name} едната прогноза на робота е излизала {one_bt['hit']:.0%} назад ({one_bt['n']} мача)"
                 + (f"; най-сигурните - {su_lg['sure']['hit']:.0%} ({su_lg['sure']['n']})" if su_lg.get("sure") else "") + ".")
    if sb:
        c = sb["clean"]
        L.append(f"  Общо: най-сигурните излизат ~{c['sure']['hit']:.0%}, а роботът им казва ~{c['sure']['said']:.0%} (завишава); "
                 f"в парите няма предимство пред останалите.")
    L.append("  Моделът е малко по-неточен от пазара (измерено). Роботът НЕ знае контузии, състави, мотивация и новини.")
    if flags.get("derby"):
        L.append("  Дербита: назад равните са повече от обещаното и формата значи малко - затова без съвет.")
    L.append("")

    # --- подробният анализ ---
    L.append("АНАЛИЗ:")
    for t in an.get("text") or []:
        L.append("  - " + t)
    return "\n".join(L)


def sure_pos(s):
    return (f"{s['r']}-о място от {s['n']} кандидата на първенството за деня" if s.get("r")
            else "добавен в разписанието по-късно през деня")


def main(names, league=None, kickoff=None, offline=False):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(answer(names, league, kickoff, offline)[1])
    return 0


def answer(names, league=None, kickoff=None, offline=False):
    """(редът на мача както в „Прогнози“, текстът на отговора)."""
    if len(names) != 2:
        raise SystemExit('Употреба: python run.py ask "Домакин" "Гост" [--league КОД] [--kickoff "2026-10-10 19:00"] [--offline]')
    now = datetime.now(timezone.utc)
    work = tempfile.mkdtemp(prefix="ask_")
    conn = None
    try:
        path, source_note = cloud_copy(work, offline)
        conn = db.init(path)
        code, home, away = find_league(conn, names[0], names[1], league, now)
        ko = parse_kickoff(kickoff, now)
        fx = conn.execute("SELECT * FROM fixtures WHERE league = ? AND home = ? AND away = ? AND kickoff >= ? ORDER BY kickoff LIMIT 1",
                          (code, home, away, (now - timedelta(hours=6)).isoformat())).fetchone()
        swapped = None
        if fx is None:
            swapped = conn.execute("SELECT * FROM fixtures WHERE league = ? AND home = ? AND away = ? AND kickoff >= ? ORDER BY kickoff LIMIT 1",
                                   (code, away, home, (now - timedelta(hours=6)).isoformat())).fetchone()
            if swapped is not None:
                fx = swapped
        scheduled = fx is not None
        if fx is None:
            fx = {"id": f"ask:{code}:{home}:{away}", "league": code, "kickoff": (ko or now).isoformat(timespec="minutes"),
                  "home": home, "away": away, "home_src": None, "away_src": None, "mapped": 1, "source": "ask",
                  "match_id": None, "prices_json": None, "prices_at": None, "updated_at": now.isoformat()}
        fitted = tips.fitted_model(conn, code, now)
        ctx = analysis.Context(conn, now)
        dates = {}
        e = make_entry(conn, ctx, fitted, dates, fx, now)
        if e is None:
            raise SystemExit("Няма нито модел, нито цени за тези отбори - роботът не може да каже нищо.")
        # най-сигурен ли е: заедно с другите мачове на първенството в същия „ден“ (bets/sure.py)
        start, end = sure.day_bounds(sure.day_of(fx["kickoff"]))
        group = [e]
        for row in conn.execute("SELECT * FROM fixtures WHERE league = ? AND kickoff >= ? AND kickoff < ? AND id != ?",
                                (code, start.isoformat(), end.isoformat(), fx["id"])).fetchall():
            other = make_entry(conn, ctx, fitted, dates, row, now)
            if other is not None:
                group.append(other)
        sure.annotate(group)
        if e.get("locked"):
            when = datetime.fromisoformat(e["locked"]).astimezone(SOFIA).strftime("%d.%m %H:%M")
            status = f"ЗАПИСАНА преди мача в {when} (записът не се променя)"
        elif scheduled:
            status = "в разписанието на робота, прогнозата е ПРЕДВАРИТЕЛНА (записва се в 07:00 в деня на мача)"
        else:
            status = "НЕ Е в разписанието на робота - изчислено ръчно със същия модел и правила; не влиза в записа и в статистиката"
        if swapped is not None:
            status += f"; в разписанието домакин е {home if swapped['home'] == home else away}"
        if scheduled or ko:
            status += f" · начало {datetime.fromisoformat(fx['kickoff']).astimezone(SOFIA).strftime('%d.%m %H:%M')} (БГ)"
        return e, report(conn, ctx, e, status, source_note)
    finally:
        if conn is not None:
            conn.close()
        try:
            shutil.rmtree(work)
        except OSError as err:
            log.warning("Временната папка %s не се изтри: %s", work, err)
