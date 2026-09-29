"""
Един вход за всичко (системата по идеите на професионалиста, 2026-09-29).

    python run.py cloud      ЦЕЛИЯТ цикъл, на всеки час - това пуска GitHub Actions
    python run.py local      лаптопът: пълният архив за проучванията + местно копие от облака
    python run.py seed       строи базата на облака от архива (последните 3 сезона + текущия)
    python run.py site       само строи сайта от текущата база
    python run.py status     какво има в базата и колко кредита са останали

Редът в `cloud` не е произволен: резултати -> предстоящи мачове -> цени -> запис на днешните
прогнози -> уреждане -> сайт -> известия. Всяка стъпка се лога отделно; ако една падне,
останалите пак се пускат, а скриптът излиза с код 1. Никакви тихи except-и (виж CLAUDE.md).
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

from bets import config, db, fixtures, notify, odds_api, prices, results, site, tips, xg
from bets.leagues import LEAGUES

RESULTS_EVERY = timedelta(hours=11)     # football-data е безплатен сървър - два пъти на ден стига
FIXTURES_EVERY = timedelta(hours=2)


def setup_logging():
    config.LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[RotatingFileHandler(config.LOG_DIR / "pipeline.log", maxBytes=2_000_000, backupCount=3,
                                      encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
        force=True)
    logging.getLogger("bets.model").setLevel(logging.WARNING)
    return logging.getLogger("run")


def step(log, name, fn, *args, **kwargs):
    log.info("--- %s ---", name)
    try:
        fn(*args, **kwargs)
        log.info("--- %s: ок ---", name)
        return True
    except Exception:
        log.exception("--- %s: ПАДНА ---", name)
        return False


def due(conn, key, every, now):
    last = db.get_meta(conn, key)
    return last is None or now - datetime.fromisoformat(last) >= every


def load_toto():
    path = config.DATA_DIR / "leagues.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        tips.TOTO = {c for c, v in data["leagues"].items() if v["toto"].get("confirmed")}


def refresh_results(conn, years_back):
    results.update_history(conn, years_back)
    results.update_new(conn, since=results.history_window_start() if years_back <= 1 else None)
    results.update_oldb(conn, years_back)
    results.update_fixtures(conn)


def cloud(log):
    conn = db.init()
    load_toto()
    now = datetime.now(timezone.utc)
    ok = []
    if due(conn, "results_at", RESULTS_EVERY, now):
        r = [step(log, "1. Резултати и разписание (football-data, OpenLigaDB)", refresh_results, conn, 1),
             step(log, "1а. Прозорец на историята", results.prune_history, conn),
             step(log, "1б. xG от Understat", xg.update_if_due, conn)]
        ok += r
        if r[0]:
            db.set_meta(conn, "results_at", now.isoformat(timespec="seconds"))
    if due(conn, "fixtures_at", FIXTURES_EVERY, now):
        f = step(log, "2. Предстоящите мачове (odds API, безплатно)", fixtures.refresh, conn, now)
        ok.append(f)
        if f:
            db.set_meta(conn, "fixtures_at", now.isoformat(timespec="seconds"))
    ok += [step(log, "3. Цени", prices.refresh, conn, now),
           step(log, "4. Запис на днешните прогнози", tips.lock, conn, now),
           step(log, "5. Уреждане", tips.settle, conn, now),
           step(log, "5а. Стари мачове без прогноза", fixtures.prune, conn, now)]
    if conn.execute("SELECT 1 FROM tips LIMIT 1").fetchone() and not db.get_meta(conn, "first_tip"):
        db.set_meta(conn, "first_tip", conn.execute("SELECT MIN(locked_at) FROM tips").fetchone()[0])
    upcoming = []
    ok.append(step(log, "6. Прогнозите напред", lambda: upcoming.extend(tips.preview(conn, now))))
    ok.append(step(log, "7. Сайт", site.build, conn, now, upcoming))
    ok += [step(log, "8. Известие сутрин", notify.morning, conn, upcoming, now),
           step(log, "9. Известие вечер", notify.evening, conn, now),
           step(log, "10. Известие за седмицата", notify.weekly, conn, now)]
    conn.close()
    log.info("===== Край. Стъпки: %d, паднали: %d =====", len(ok), ok.count(False))
    return 1 if ok.count(False) else 0


def seed(log, target, source=None):
    """Базата на облака: историята от прозореца (3 сезона + текущия) за всички лиги + xG.
    source - архивът на лаптопа (football.db)."""
    source = source or (config.ROOT / "football.db")
    since = results.history_window_start()
    target.unlink(missing_ok=True)
    conn = db.init(target)
    conn.execute("ATTACH DATABASE ? AS src", (str(source),))
    conn.execute("""INSERT INTO matches (id, league, season, date, home_team, away_team, fthg, ftag, hthg, htag, kickoff)
                    SELECT id, league, season, date, home_team, away_team, fthg, ftag, hthg, htag, kickoff
                      FROM src.matches WHERE date >= ? AND league IN (%s)""" % ",".join("?" * len(LEAGUES)),
                 (since, *LEAGUES))
    conn.execute("INSERT INTO odds (match_id, bookmaker, odds_home, odds_draw, odds_away) "
                 "SELECT o.match_id, o.bookmaker, o.odds_home, o.odds_draw, o.odds_away FROM src.odds o JOIN matches m ON m.id = o.match_id "
                 "WHERE o.bookmaker IN ('AVG', 'MAX', 'B365', 'PS')")
    conn.execute("INSERT INTO odds_totals (match_id, bookmaker, line, odds_over, odds_under) "
                 "SELECT o.match_id, o.bookmaker, o.line, o.odds_over, o.odds_under FROM src.odds_totals o JOIN matches m ON m.id = o.match_id "
                 "WHERE o.bookmaker IN ('AVG', 'MAX') AND o.line = 2.5")
    conn.execute("INSERT INTO xg (match_id, xg_h, xg_a, source) "
                 "SELECT x.match_id, x.xg_h, x.xg_a, x.source FROM src.xg x JOIN matches m ON m.id = x.match_id")
    conn.commit()
    conn.execute("DETACH DATABASE src")
    conn.execute("VACUUM")
    log.info("Базата на облака: %s", db.counts(conn))
    conn.close()


def local(log):
    """Лаптопът: пълният архив (football.db) за проучванията и местно копие на сайта от облака."""
    conn = db.init()
    ok = [step(log, "1. Архивът: 22 лиги, 16 държави, 3. Бундеслига", refresh_results, conn, 2),
          step(log, "2. xG от Understat", xg.update, conn)]
    conn.close()
    if config.GITHUB_TOKEN and config.GITHUB_REPO:
        from bets import publish
        ok.append(step(log, "3. Местно копие на сайта и базата от облака", publish.pull_data))
    return 1 if ok.count(False) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["cloud", "local", "seed", "site", "status"])
    parser.add_argument("--target", default=None, help="за seed: къде да се запише базата на облака")
    args = parser.parse_args()
    log = setup_logging()
    log.info("===== Старт: %s %s =====", args.command, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if args.command == "cloud":
        return cloud(log)
    if args.command == "local":
        return local(log)
    if args.command == "seed":
        from pathlib import Path
        seed(log, Path(args.target) if args.target else config.SITE_DIR / "robot.db")
        return 0
    if args.command == "site":
        conn = db.init()
        load_toto()
        site.build(conn)
        conn.close()
        return 0
    conn = db.init()
    print("В базата:", db.counts(conn))
    print("Кредити (последно видяни):", db.get_meta(conn, "credits_remaining"))
    print("Кредити сега:", odds_api.remaining())
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
