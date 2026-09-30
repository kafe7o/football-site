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
Намереният резултат отива в results/<ден UTC>.json ({id на мача: {"s": [голове], "at": кога}}) и се
качва веднага. Сайтът дотегля файла всяка минута и мести мача сам; часовото пускане урежда прогнозата
от същия файл (tips.settle) - без втори кредит. Официалният резултат (football-data) после само сверява.

Кредити: под 3000 - само при изчезване от списъка; под 300 - нищо (остава часовото уреждане).
Първенствата без odds API (Англия 5-то ниво, Шотландия 2-4, Румъния) нямат бърз източник - там
резултатът идва от football-data след 1-3 дни.
"""

import json
import logging
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone

from . import config, odds_api
from .leagues import LEAGUES

log = logging.getLogger(__name__)

START_AFTER = timedelta(minutes=95)     # преди това мачът не може да е свършил
GIVE_UP = timedelta(hours=6)            # по-стари - остават за часовото уреждане
FIRST_POLL = timedelta(minutes=108)
MIN_CREDITS_FALLBACK, MIN_CREDITS = 3000, 300
PUSH_EVERY = 60                         # секунди между две качвания


def results_dir():
    return config.SITE_DIR / "results"


def load_results(days=4):
    """Всички бързи резултати от последните дни: {id: {"s": [h, a], "at": ...}}."""
    out = {}
    folder = results_dir()
    if not folder.exists():
        return out
    cut = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    for path in sorted(folder.glob("*.json")):
        if path.stem >= cut:
            out.update(json.loads(path.read_text(encoding="utf-8")))
    return out


def save_result(fixture_id, kickoff, hg, ag, now, updated=None):
    folder = results_dir()
    folder.mkdir(exist_ok=True)
    path = folder / f"{kickoff[:10]}.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[fixture_id] = {"s": [hg, ag], "at": now.isoformat(timespec="seconds"), "upd": updated}   # upd - последната промяна по odds API
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


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


def due(matches, last_scores, now):
    """Трябва ли резултат по часовника: от 108-ата минута на 3 мин., от 150-ата на 10, от 200-ата на 30."""
    for m in matches:
        age = now - datetime.fromisoformat(m["kickoff"])
        step = 3 if age < timedelta(minutes=150) else 10 if age < timedelta(minutes=200) else 30
        if age >= FIRST_POLL and (last_scores is None or now - last_scores >= timedelta(minutes=step)):
            return True
    return False


def push(message):
    """Качва само папката results - базата и сайта ги пише часовото пускане."""
    root = config.SITE_DIR
    run = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
    run("add", "results")
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
    last_events, last_scores, gone_seen = {}, {}, set()
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
    known = load_results(days=7)
    if not known:
        return 0
    stamp = now.isoformat(timespec="seconds")
    n = 0
    for fid, x in known.items():
        cur = conn.execute("UPDATE tips SET hg = ?, ag = ?, settled_at = ?, result_src = 'odds-api' "
                           "WHERE fixture_id = ? AND hg IS NULL", (x["s"][0], x["s"][1], stamp, fid))
        n += cur.rowcount
    conn.commit()
    return n
