"""
Един вход за всичко (системата по идеите на професионалиста, 2026-09-29).

    python run.py cloud      ЦЕЛИЯТ цикъл, на всеки час - това пуска GitHub Actions
    python run.py weekly     седмичният анализ - в облака всеки понеделник (archive.yml): архивът,
                             новите мачове, анализът по първенства, веднъж месечно роботът назад
    python run.py local      лаптопът (ръчно, когато работим): архивът и копие на сайта от облака
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
import os
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

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


def refresh_results(conn, years_back):
    results.update_history(conn, years_back)
    results.update_new(conn, since=results.history_window_start() if years_back <= 1 else None)
    results.update_oldb(conn, years_back)
    results.update_fixtures(conn)


def cloud(log):
    results.KEEP_PLAYED_ODDS = False
    conn = db.init()
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
    # коефициенти - само за предстоящите мачове (виж results.KEEP_PLAYED_ODDS)
    conn.execute("INSERT INTO odds (match_id, bookmaker, odds_home, odds_draw, odds_away) "
                 "SELECT o.match_id, o.bookmaker, o.odds_home, o.odds_draw, o.odds_away FROM src.odds o JOIN matches m ON m.id = o.match_id "
                 "WHERE o.bookmaker IN ('AVG', 'MAX', 'B365', 'PS') AND m.fthg IS NULL")
    conn.execute("INSERT INTO odds_totals (match_id, bookmaker, line, odds_over, odds_under) "
                 "SELECT o.match_id, o.bookmaker, o.line, o.odds_over, o.odds_under FROM src.odds_totals o JOIN matches m ON m.id = o.match_id "
                 "WHERE o.bookmaker IN ('AVG', 'MAX') AND o.line = 2.5 AND m.fthg IS NULL")
    conn.execute("INSERT INTO xg (match_id, xg_h, xg_a, source) "
                 "SELECT x.match_id, x.xg_h, x.xg_a, x.source FROM src.xg x JOIN matches m ON m.id = x.match_id")
    conn.commit()
    conn.execute("DETACH DATABASE src")
    conn.execute("VACUUM")
    log.info("Базата на облака: %s", db.counts(conn))
    conn.close()


def read_data(name):
    path = config.DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def weekly(log, backtest=False, keep_local=False):
    """Седмичният анализ (2026-09-30) - в облака, без лаптопа.

    Архивът (football.db, всички лиги от 2012) идва шифрован от изданието „archive“ на хранилището
    (bets/archive.py), допълва се с новите мачове и xG, анализът по първенства се смята наново,
    а веднъж месечно (първия понеделник) и роботът назад. После архивът се качва обратно.
    Правилата на робота НЕ се пипат - само числата. Ако по новите данни тото лигите не са
    същите като заключените в bets/robot.py, идва известие и собственикът решава.
    """
    from bets import archive
    from research import extras_backtest, league_analysis, robot_backtest
    path = config.DB_PATH
    if not keep_local:
        archive.pull_db(path)      # без архива - нищо; грешката спира всичко и нищо не се качва
    old_bt = read_data("backtest.json")
    conn = db.init(path)
    count = lambda: conn.execute("SELECT COUNT(*) FROM matches WHERE fthg IS NOT NULL").fetchone()[0]
    before = count()
    steps = [("новите мачове", step(log, "1. Новите мачове: 22 лиги, 16 държави, 3. Бундеслига",
                                    refresh_results, conn, 2)),
             ("xG", step(log, "2. xG от Understat", xg.update, conn))]
    after = count()
    conn.close()
    out = {}
    steps.append(("анализът", step(log, "3. Анализът по първенства", lambda: out.update(la=league_analysis.main()))))
    if backtest or datetime.now(timezone.utc).day <= 7:
        steps.append(("роботът назад", step(log, "4. Роботът назад (веднъж месечно)",
                                            lambda: out.update(bt=robot_backtest.main()))))
        steps.append(("картоните и корнерите назад", step(log, "4а. Картони и корнери назад (веднъж месечно)",
                                                          lambda: out.update(xb=extras_backtest.main()))))
    if steps[0][1] and not keep_local:
        steps.append(("качването на архива", step(log, "5. Качване на архива", archive.push_db, path)))
    failed = [name for name, good in steps if not good]
    weekly_notify(after - before, after, out, old_bt, failed)
    return 1 if failed else 0


def weekly_notify(new, total, out, old_bt, failed):
    from bets import robot
    from bets.leagues import LEAGUES
    la, bt = out.get("la"), out.get("bt")
    lines = [f"+{new} нови мача (общо {total:,}).".replace(",", " ")]
    if la:
        data_toto = {c for c, v in la["leagues"].items() if v["toto"].get("confirmed")}
        names = lambda codes: ", ".join(LEAGUES[c].title for c in sorted(codes)) or "няма"
        lines.append("Тото по данните: " + names(data_toto) +
                     (" - същите като в робота." if data_toto == robot.TOTO_LEAGUES else ""))
        rank = la.get("goals_ranking") or []
        if rank:
            g = lambda c: la["leagues"][c]["goals"]["last3"]["avg"]
            lines.append("Голове: " + ", ".join(f"{i + 1}. {LEAGUES[c].title} {g(c):.2f}" for i, c in enumerate(rank[:3]))
                         + f" ... последна {LEAGUES[rank[-1]].title} {g(rank[-1]):.2f}.")
        if data_toto != robot.TOTO_LEAGUES:
            notify.send("Тото лигите по данните се промениха",
                        f"Роботът работи с: {names(robot.TOTO_LEAGUES)}.\nПо новите данни: {names(data_toto)}.\n"
                        "Нищо не е сменено - кажи дали да се приложи.", tags="warning", priority=4)
    if bt:
        a = bt["leagues"]["ALL"].get(f"tip:{bt['rule']}:all") or {}
        b = ((old_bt.get("leagues") or {}).get("ALL") or {}).get(f"tip:{old_bt.get('rule')}:all") or {}
        if a:
            lines.append(f"Роботът назад: {a['hit']:.1%} познати на {a['n']:,} съвета, доход {a['roi']:+.1%}".replace(",", " ")
                         + (f" (преди: {b['hit']:.1%}, {b['roi']:+.1%})." if b else "."))
    xb = out.get("xb")
    if xb:
        parts = []
        for kind, name in (("cards", "картони"), ("corners", "корнери")):
            a = ((xb["kinds"].get(kind) or {}).get("leagues") or {}).get("ALL", {}).get("all")
            if a:
                parts.append(f"{name} {a['hit']:.1%} познати ({'с умение' if xb['kinds'][kind]['skill'] else 'умение не е доказано'})")
        if parts:
            lines.append("Над/под назад: " + ", ".join(parts) + ".")
    if failed:
        lines.append("ПРОБЛЕМ: " + ", ".join(failed) + " - виж Actions -> archive.")
    notify.send("Седмичният анализ" + (" - с проблем" if failed else ""), "\n".join(lines),
                tags="warning" if failed else "bar_chart", priority=4 if failed else 3)
    if os.environ.get("RUNNER_TEMP"):          # в облака: archive.yml не праща второ известие
        (Path(os.environ["RUNNER_TEMP"]) / "weekly_notified").write_text("1", encoding="utf-8")


def local(log):
    """Лаптопът (ръчно, когато работим): архивът от облака + местно копие на сайта и анализите.
    Задачите на Windows са изключени от 2026-09-30 - всичко редовно върви в облака."""
    from bets import archive, publish
    ok = [step(log, "1. Архивът от облака", archive.pull_db, config.DB_PATH),
          step(log, "2. Местно копие на сайта, базата на робота и анализите", publish.pull_data)]
    return 1 if ok.count(False) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["cloud", "weekly", "local", "seed", "site", "status"])
    parser.add_argument("--target", default=None, help="за seed: къде да се запише базата на облака")
    parser.add_argument("--backtest", action="store_true", help="за weekly: и роботът назад, не само първия понеделник")
    parser.add_argument("--keep-local", action="store_true", help="за weekly: местният архив, без сваляне и качване")
    args = parser.parse_args()
    log = setup_logging()
    log.info("===== Старт: %s %s =====", args.command, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if args.command == "cloud":
        return cloud(log)
    if args.command == "local":
        return local(log)
    if args.command == "weekly":
        return weekly(log, args.backtest, args.keep_local)
    if args.command == "seed":
        from pathlib import Path
        seed(log, Path(args.target) if args.target else config.SITE_DIR / "robot.db")
        return 0
    if args.command == "site":
        conn = db.init()
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
