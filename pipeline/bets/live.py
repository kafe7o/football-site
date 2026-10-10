"""
Резултатите до минути след края на мача (собственикът, 2026-10-01: „като свърши мачът, системата сама
да разбира, че е свършил, и до 5 минути да е изместен от „Чакат резултат“ в „Мач по мач“ - не да чака
проверка“).

Наблюдателят върви в облака непрекъснато - в задачата „next“ на robot.yml, между две часови пускания
(там досега веригата само спеше). На всяка минута гледа записаните прогнози за мачове, започнали преди
повече от 95 минути и още без резултат, и пита odds API:
  - списъкът със събитията на лигата (/events) е БЕЗПЛАТЕН: мач, който е изчезнал от него, е свършил;
  - тогава резултатът (/scores, 2 кредита на лига) - най-много веднъж на минута за лига;
  - и без изчезване: от 108-ата минута след началото (45 + почивка + 45 + добавено) на всеки 3 минути до
    150-ата, после на 10, после на 30 - ако списъкът не пусне мача навреме.
Намереният резултат отива в results/live.json ({id на мача: {"s": [голове], "at": кога, "k": началото}},
последните 4 дни; файлът винаги съществува - страницата не получава „няма такъв файл“) и се качва веднага. Сайтът дотегля файла всяка минута и мести мача сам; часовото пускане урежда прогнозата
от същия файл (tips.settle) - без втори кредит. Официалният резултат (football-data) после само сверява.

Кредити: под 3000 - само при изчезване от списъка; под 300 - нищо (остава часовото уреждане).

Първенствата без odds API (Англия 5-то ниво, Шотландия 2-4, Румъния) - публичното табло на ESPN
(без ключ и без кредити; собственикът 01.10: „като са свършили, няма смисъл да чакат“). То е НЕОФИЦИАЛНО:
ако ESPN го смени, грешката се лога и резултатът пак идва от football-data след 1-3 дни, който и
сверява (tips.settle). Проверено 01.10: Eastleigh - Southend 1:1 и Tamworth - Sutton 2:0 - минути след края.
"""

import json
import logging
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import apifootball, config, odds_api, teams
from .leagues import LEAGUES

log = logging.getLogger(__name__)

START_AFTER = timedelta(minutes=95)     # преди това мачът не може да е свършил
GIVE_UP = timedelta(hours=6)            # по-стари - остават за часовото уреждане
FIRST_POLL = timedelta(minutes=108)
MIN_CREDITS_FALLBACK, MIN_CREDITS = 3000, 300
PUSH_EVERY = 60                         # секунди между две качвания
# първенствата без odds API -> лигата в таблото на ESPN
ESPN = {"EC": "eng.5", "SC1": "sco.2", "SC2": "sco.3", "SC3": "sco.4", "ROU": "rou.1"}
ESPN_WINDOW = timedelta(days=3)         # безплатно - гледа и по-стари мачове без резултат
ESPN_EVERY = timedelta(minutes=2)
ESPN_FINAL = {"STATUS_FULL_TIME", "STATUS_FINAL"}


def results_dir():
    return config.SITE_DIR / "results"


KEEP_DAYS = 4


def results_file():
    return results_dir() / "live.json"


def load_results():
    """Бързите резултати от последните дни: {id: {"s": [h, a], "at": ..., "k": началото}}."""
    path = results_file()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_result(fixture_id, kickoff, hg, ag, now, updated=None, src="odds-api"):
    data = load_results()
    cut = (now - timedelta(days=KEEP_DAYS)).isoformat()
    data = {k: v for k, v in data.items() if v.get("k", "") >= cut}
    # upd - последната промяна по odds API (около последния съдийски сигнал)
    data[fixture_id] = {"s": [hg, ag], "at": now.isoformat(timespec="seconds"), "upd": updated, "k": kickoff, "src": src}
    results_dir().mkdir(exist_ok=True)
    results_file().write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def tracked(now, known):
    """Записаните прогнози за мачове от odds API, започнали преди 95 мин. - 6 ч., без резултат."""
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT t.fixture_id, t.league, t.kickoff, t.home, t.away FROM tips t JOIN fixtures f ON f.id = t.fixture_id
            WHERE t.hg IS NULL AND f.source = 'api' AND t.kickoff <= ? AND t.kickoff >= ?""",
        ((now - START_AFTER).isoformat(), (now - GIVE_UP).isoformat())).fetchall()
    conn.close()
    return [dict(r) for r in rows if r["fixture_id"] not in known and LEAGUES[r["league"]].sport]


def tracked_espn(now, known):
    """Записаните прогнози без резултат в първенствата без odds API, започнали преди 95 мин. - 3 дни."""
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    marks = ",".join("?" * len(ESPN))
    rows = conn.execute(
        f"""SELECT fixture_id, league, kickoff, home, away FROM tips
             WHERE hg IS NULL AND league IN ({marks}) AND kickoff <= ? AND kickoff >= ?""",
        (*ESPN, (now - START_AFTER).isoformat(), (now - ESPN_WINDOW).isoformat())).fetchall()
    conn.close()
    return [dict(r) for r in rows if r["fixture_id"] not in known]


def espn_day(code, day):
    """Таблото на ESPN за лигата и деня (по един ден - период в заявката таблото не приема)."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{ESPN[code]}/scoreboard?dates={day:%Y%m%d}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.load(resp).get("events", [])
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"ESPN {code} {day}: {e}") from None


def espn_finished(code, first, last):
    """Свършилите мачове на лигата в таблото на ESPN между двата дни: [(начало, домакин, гост, г1, г2)]."""
    events, day = [], first
    while day <= last:
        events += espn_day(code, day)
        day += timedelta(days=1)
    out = []
    for e in events:
        st = (e.get("status") or {}).get("type") or {}
        if not st.get("completed") or st.get("name") not in ESPN_FINAL:
            continue
        side = {c.get("homeAway"): c for c in ((e.get("competitions") or [{}])[0].get("competitors") or [])}
        try:
            out.append((e["date"], side["home"]["team"]["displayName"], side["away"]["team"]["displayName"],
                        int(side["home"]["score"]), int(side["away"]["score"])))
        except (KeyError, TypeError, ValueError):
            log.warning("ESPN %s: свършил мач без резултат или имена (%s)", code, e.get("id"))
    return out


def espn_round(now, known, last_espn):
    """Една обиколка на таблото на ESPN: [(мач, голове, голове, начало по ESPN)] за новите резултати."""
    by_league = {}
    for m in tracked_espn(now, known):
        by_league.setdefault(m["league"], []).append(m)
    new = []
    for code, matches in by_league.items():
        if code in last_espn and now - last_espn[code] < ESPN_EVERY:
            continue
        last_espn[code] = now
        days = [datetime.fromisoformat(m["kickoff"]).date() for m in matches]
        try:
            done = espn_finished(code, min(days) - timedelta(days=1), max(days) + timedelta(days=1))
        except RuntimeError as e:
            log.error("%s", e)
            continue
        for start, home, away, hg, ag in done:
            day = datetime.fromisoformat(start.replace("Z", "+00:00")).date()
            near = [m for m in matches if abs((datetime.fromisoformat(m["kickoff"]).date() - day).days) <= 1
                    and m["fixture_id"] not in known and m not in [x[0] for x in new]]
            m = teams.match_fixture(home, away, near, key=lambda f: (f["home"], f["away"]))
            if m:
                new.append((m, hg, ag, start))
                log.info("Резултат (ESPN): %s - %s %d:%d (там: %s - %s)", m["home"], m["away"], hg, ag, home, away)
    return new


def due(matches, last_scores, now):
    """Трябва ли резултат по часовника: от 108-ата минута на 3 мин., от 150-ата на 10, от 200-ата на 30."""
    for m in matches:
        age = now - datetime.fromisoformat(m["kickoff"])
        step = 3 if age < timedelta(minutes=150) else 10 if age < timedelta(minutes=200) else 30
        if age >= FIRST_POLL and (last_scores is None or now - last_scores >= timedelta(minutes=step)):
            return True
    return False


def push(message):
    """Качва само папките results и squads (бързите резултати и съставите) - базата и сайта ги пише часовото пускане."""
    root = config.SITE_DIR
    run = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
    run("add", "results")
    if (root / "squads").exists():
        run("add", "squads")
    if run("diff", "--staged", "--quiet").returncode == 0:
        return True
    out = run("commit", "-q", "-m", message)
    if out.returncode:
        log.error("results: commit падна - %s", out.stderr.strip()[:200])
        return False
    for _ in range(5):
        if run("pull", "--rebase", "-q", "origin", "main").returncode == 0 and run("push", "-q").returncode == 0:
            return True
        time.sleep(10)
    log.error("results: качването не мина след 5 опита")
    return False


def watch(seconds, publish=True):
    """Наблюдателят: върти се seconds секунди; връща колко резултата е намерил."""
    deadline = time.time() + seconds
    known = load_results()
    last_events, last_scores, gone_seen, last_espn, last_lineup = {}, {}, set(), {}, {}
    found, pending_push, last_push = 0, [], 0.0
    log.info("Наблюдателят тръгна за %d мин.; вече известни резултати: %d", seconds // 60, len(known))
    while time.time() < deadline:
        now = datetime.now(timezone.utc)
        by_sport = {}
        for m in tracked(now, known):
            by_sport.setdefault(LEAGUES[m["league"]].sport, []).append(m)
        credits = odds_api.last_remaining()
        for sport, matches in by_sport.items():
            gone = []
            if now - last_events.get(sport, now - timedelta(hours=1)) >= timedelta(seconds=55):
                try:
                    live = {e["id"] for e in odds_api.events(sport)}
                    last_events[sport] = now
                    # изчезналият мач се проверява веднага, но само веднъж - ако списъкът пуска
                    # мачовете още при началото, по-нататък важи часовникът (due), не всяка минута
                    gone = [m for m in matches if m["fixture_id"] not in live and m["fixture_id"] not in gone_seen]
                    gone_seen.update(m["fixture_id"] for m in gone)
                except RuntimeError as e:
                    log.error("%s: списъкът със събитията не се изтегли - %s", sport, e)
            ask = (gone and (sport not in last_scores or now - last_scores[sport] >= timedelta(seconds=55))
                   and (credits is None or credits >= MIN_CREDITS))
            ask = ask or (due(matches, last_scores.get(sport), now) and (credits is None or credits >= MIN_CREDITS_FALLBACK))
            if not ask:
                continue
            try:
                done = {e["id"]: e for e in odds_api.finished(sport, days_from=1, cache_minutes=0)}
            except RuntimeError as e:
                log.error("%s: резултатите не се изтеглиха - %s", sport, e)
                last_scores[sport] = now
                continue
            last_scores[sport] = now
            credits = odds_api.last_remaining()
            for m in matches:
                e = done.get(m["fixture_id"])
                if not e:
                    continue
                hg, ag = int(e["home"]), int(e["away"])
                save_result(m["fixture_id"], m["kickoff"], hg, ag, now, e.get("last_update"))
                known[m["fixture_id"]] = {"s": [hg, ag]}
                mins = int((now - datetime.fromisoformat(m["kickoff"])).total_seconds() // 60)
                log.info("Резултат: %s - %s %d:%d (%d мин. след началото%s)", m["home"], m["away"], hg, ag, mins,
                         ", изчезна от списъка" if m in gone else "")
                pending_push.append(f"{m['home']} - {m['away']} {hg}:{ag}")
                found += 1
        # първенствата без odds API - таблото на ESPN (безплатно), на всеки 2 минути за лига
        for m, hg, ag, start in espn_round(now, known, last_espn):
            save_result(m["fixture_id"], m["kickoff"], hg, ag, now, start, src="espn")
            known[m["fixture_id"]] = {"s": [hg, ag]}
            pending_push.append(f"{m['home']} - {m['away']} {hg}:{ag}")
            found += 1
        # съставите (API-Football): за мачовете до 90 минути напред на всеки 7 минути, докато се обявят
        got = apifootball.live_round(now, last_lineup)
        if got:
            pending_push.append(f"Състави: {got}")
        if publish and pending_push and time.time() - last_push >= PUSH_EVERY:
            if push("Резултати: " + "; ".join(pending_push[:6])):
                pending_push, last_push = [], time.time()
        time.sleep(max(5, min(60, deadline - time.time())))
    if publish and pending_push:
        push("Резултати: " + "; ".join(pending_push[:6]))
    log.info("Наблюдателят свърши: %d резултата, кредити %s", found, odds_api.last_remaining())
    return found


def settle_from_files(conn, now):
    """Урежда записаните прогнози от бързите резултати (без кредити). Връща колко."""
    known = load_results()
    if not known:
        return 0
    stamp = now.isoformat(timespec="seconds")
    n = 0
    for fid, x in known.items():
        cur = conn.execute("UPDATE tips SET hg = ?, ag = ?, settled_at = ?, result_src = ? "
                           "WHERE fixture_id = ? AND hg IS NULL", (x["s"][0], x["s"][1], stamp, x.get("src", "odds-api"), fid))
        n += cur.rowcount
    conn.commit()
    return n
