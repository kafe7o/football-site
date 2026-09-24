"""
Един вход за всичко.

    python run.py daily      целият дневен цикъл (това пуска и Task Scheduler)
    python run.py scan       само скенерът за цени, по желание с други спортове
    python run.py site       само построява и качва сайта
    python run.py status     какво има в базата и колко квота е останала
    python run.py snapshot   записва site/snapshot.json за облака (историята + прегледът напред)
    python run.py cloud      сканиране + сайт БЕЗ история - това пуска GitHub Actions
    python run.py changes    кои прогнози са се променили осезаемо (за известията)

Редът в `daily` не е произволен: първо резултати, после уреждане (има с какво да сверява),
после нови прогнози върху вече обновената история, и накрая сайтът.

Всяка стъпка се лога отделно. Ако една падне, останалите пак се пускат, а скриптът излиза
с код 1, за да е видимо в Task Scheduler. Никакви тихи except-и (виж CLAUDE.md).
"""

import argparse
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

from bets import config, db, model, odds_api, predict, publish, results, site, value


def setup_logging():
    config.LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[RotatingFileHandler(config.LOG_DIR / "pipeline.log", maxBytes=2_000_000,
                                      backupCount=3, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
        force=True)
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


def daily(log, args):
    conn = db.init()
    ok = [
        step(log, "1. Резултати от football-data", results.update_history, conn, args.years_back),
        step(log, "2. Разписание на предстоящите мачове", results.update_fixtures, conn),
        step(log, "3. Уреждане на стари прогнози", predict.settle, conn),
    ]
    if args.skip_predictions:
        log.info("4. Прогнози и скенер: пропуснато (--skip-predictions)")
    else:
        ok.append(step(log, "4. Прогнози за днешните мачове", predict.run, conn))
    # Цените, залозите по цена и уреждането им са работа САМО на облака (на всеки час).
    # Преди и лаптопът ги сканираше и се получаваха две отделни книги, които се разминаваха.
    ok.append(step(log, "5. Снимка за облака (история, модел, прогнози)", site.write_snapshot, conn))
    ok.append(step(log, "6. Сайт", site.build, conn))
    if config.GITHUB_TOKEN and config.GITHUB_REPO:
        ok.append(step(log, "7. Качване на сайта", publish.run))
    else:
        log.info("7. Качване: пропуснато (GITHUB_TOKEN/GITHUB_REPO липсват в .env)")
    conn.close()
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command",
                        choices=["daily", "scan", "site", "status", "snapshot", "cloud", "changes"])
    parser.add_argument("--years-back", type=int, default=2)
    parser.add_argument("--skip-predictions", action="store_true",
                        help="само резултати и уреждане, без да яде квота")
    parser.add_argument("--sports", nargs="*", default=None, help="ключове на лиги в odds API")
    parser.add_argument("--edge", type=float, default=value.MIN_EDGE)
    parser.add_argument("--books", nargs="*", default=None,
                        help="само тези букмейкъри (там, където реално можеш да играеш)")
    args = parser.parse_args()

    log = setup_logging()
    log.info("===== Старт: %s %s =====", args.command, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if args.command == "status":
        conn = db.init()
        print("В базата:", db.counts(conn))
        print("Модел:", model.VERSION)
        print("Квота на odds API:", odds_api.remaining(), "заявки до края на месеца")
        print("Прогнози срещу пазара:", predict.record(conn))
        print("Залози по цена:", value.record(conn))
        conn.close()
        return 0

    if args.command == "scan":
        conn = db.init()
        found = value.scan(conn, args.sports, min_edge=args.edge,
                           allowed=set(args.books) if args.books else None)
        for bet in sorted(found, key=lambda b: -b["edge"])[:30]:
            print(f"{bet['home_team'][:22]:<24}{bet['away_team'][:22]:<24}"
                  f"{bet['selection'][:18]:<20}{bet['odds']:>6.2f} {bet['bookmaker'][:14]:<16}"
                  f"{bet['edge']:>+7.1%}")
        value.settle(conn)
        conn.close()
        return 0

    if args.command == "changes":
        # За известията. Чете дневника на облака (site/cloud.db): там се записва на всеки
        # час, дори лаптопът да е бил изключен, затова той е единственият пълен.
        cloud = config.SITE_DIR / "cloud.db"
        conn = db.init(cloud if cloud.exists() else None)
        now = datetime.now().astimezone().isoformat()
        events = conn.execute(
            """SELECT event_id, home_team, away_team, MIN(commence_time) AS start,
                      COUNT(*) AS n FROM forecast_log
                WHERE commence_time > ? GROUP BY event_id HAVING n >= 2""", (now,)).fetchall()
        shown = 0
        for e in events:
            log_rows = conn.execute(
                """SELECT * FROM forecast_log WHERE event_id = ? ORDER BY recorded_at""",
                (e["event_id"],)).fetchall()
            first, last = log_rows[0], log_rows[-1]
            shift = max(abs((last[k] or 0) - (first[k] or 0))
                        for k in ("p_model_h", "p_model_d", "p_model_a",
                                  "p_fair_h", "p_fair_d", "p_fair_a"))
            if shift < site.CHANGE_THRESHOLD:
                continue
            shown += 1
            fmt = lambda r, k: " / ".join("–" if r[f"{k}_{x}"] is None else f"{r[f'{k}_{x}']:.0%}"
                                          for x in "hda")
            print(f"{e['start'][:16].replace('T', ' ')}  {e['home_team']} - {e['away_team']}")
            print(f"    модел: {fmt(first, 'p_model')}  ->  {fmt(last, 'p_model')}")
            print(f"    пазар: {fmt(first, 'p_fair')}  ->  {fmt(last, 'p_fair')}")
        if not shown:
            print("Няма осезаема промяна в прогнозите.")
        conn.close()
        return 0

    if args.command == "snapshot":
        conn = db.init()
        site.write_snapshot(conn)
        site.build(conn)
        conn.close()
        return 0

    if args.command == "cloud":
        # Пуска се от GitHub Actions: няма база с история, има snapshot.json от лаптопа.
        conn = db.init()
        ok = [step(log, "1. Скенер за цени", value.scan, conn),
              step(log, "2. Уреждане", value.settle, conn),
              step(log, "3. Сайт от снимката", site.build, conn, True)]
        conn.close()
        return 1 if ok.count(False) else 0

    if args.command == "site":
        conn = db.init()
        site.build(conn)
        conn.close()
        if config.GITHUB_TOKEN and config.GITHUB_REPO:
            publish.run()
        return 0

    ok = daily(log, args)
    failed = ok.count(False)
    log.info("===== Край. Стъпки: %d, паднали: %d =====", len(ok), failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
