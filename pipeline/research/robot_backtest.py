"""
Роботът назад във времето: колко често познава по лиги и по пазари (2026-09-29).

ПРОТОКОЛ (записан преди пускането):
Данни: football.db. Всички лиги с история (22 на football-data, 16 държави на football-data/new,
3. Бундеслига от OpenLigaDB). Мачовете от 2023-07-01 до днес.

Walk-forward: за всяка лига и всеки календарен месец моделът (bets/model.py, същият като на
живо, половин голове половин xG за 5-те големи лиги) се обучава САМО на мачовете преди 1-во
число на месеца (последните 4 години) и прогнозира мачовете на месеца. На живо моделът се
обучава всеки ден - тук веднъж на месец, тоест проверката е малко по-строга от живота.

Цени: средната на пазара (AVG) преди мача за 22-те лиги; за 16-те държави има само
затварящата (AVGC) - там цените са по-остри, отбелязано. Двоен шанс: от 1/X/2 на същата
цена, 1 / (1/к1 + 1/кX). Над/под 2.5: AVG от football-data (само 22-те лиги).
Счупени редове (сбор на 1/к извън 0.98-1.20) се изхвърлят.

Мери се за всяка лига и всеки пазар (1x2, dc, ou): колко пъти е познал роботът, колко -
пазарът (най-вероятното по коефициентите), доход при 1 единица на прогноза на средната цена.

ГЛАВЕН СЪВЕТ - кое правило (bets/robot.py: choose), всички с цена поне 1.40, без дербитата:
  likely         най-вероятният изход по робота
  market_likely  най-вероятният изход по пазара
  value          най-голяма стойност p*цена сред изходите с шанс поне 50% по робота
  agree          likely, само когато и пазарът дава същия изход за най-вероятен
ИЗБОР: сезони 2023/24 и 2024/25 (мачове 2023-07-01 до 2025-06-30). Приема се правилото с
най-висок доход в избора. ЧИСТА ПРОВЕРКА: от 2025-07-01 до днес - там се отчита, без избор.

РЕЗУЛТАТ (2026-09-29): прието market_likely (избор -5.44%, чиста -7.70%; likely -5.96/-8.22,
value -5.53/-7.38, agree -5.59/-7.73). Втори етап: market_likely само при шанс по пазара поне 50%
(в лигите без над/под правилото избираше аутсайдери). Критерий: по-добро и в избора, и в чистата.
Резултат: -4.75% / -7.15% - прието (bets/robot.py: MIN_PROB).

Резултатът: backtest_tips в football.db и data/backtest.json (за сайта). Това е симулация -
никога не се смесва със записаните преди мача прогнози (таблицата tips).
"""

import json
import logging
import math
import sys
from datetime import date
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, derbies, model, prices as P, results, robot, xg   # noqa: E402
from bets.leagues import LEAGUES, WITH_HISTORY                          # noqa: E402

START = "2023-07-01"
SELECT_END = "2025-07-01"
RULES = ["likely", "market_likely", "value", "agree"]
OUT = ROOT / "data" / "backtest.json"
log = logging.getLogger("backtest")


def month_starts(first, last):
    y, m = int(first[:4]), int(first[5:7])
    while f"{y:04d}-{m:02d}-01" <= last:
        yield f"{y:04d}-{m:02d}-01"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def prices_for(conn, match_id, closing):
    """Цените на мача във формата на bets/prices.py."""
    book = "AVGC" if closing else "AVG"
    r = conn.execute("SELECT odds_home, odds_draw, odds_away FROM odds WHERE match_id = ? AND bookmaker = ?",
                     (match_id, book)).fetchone()
    if r is None and closing:
        r = conn.execute("SELECT odds_home, odds_draw, odds_away FROM odds WHERE match_id = ? AND bookmaker = 'PSC'",
                         (match_id,)).fetchone()
    if r is None or not P.sane(list(r)):
        return None
    o1, ox, o2 = r
    avg = {"1": o1, "X": ox, "2": o2, "1X": 1 / (1 / o1 + 1 / ox), "X2": 1 / (1 / ox + 1 / o2), "12": 1 / (1 / o1 + 1 / o2)}
    t = conn.execute("SELECT odds_over, odds_under FROM odds_totals WHERE match_id = ? AND bookmaker = 'AVG' AND line = 2.5",
                     (match_id,)).fetchone()
    if t and t[0] and t[1] and 1.0 <= 1 / t[0] + 1 / t[1] <= 1.15:
        avg.update({"O": t[0], "U": t[1]})
    return {"avg": {k: round(v, 3) for k, v in avg.items()}}


def run_league(code, toto_leagues=()):
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s", stream=sys.stdout, force=True)
    conn = db.init()
    lg = LEAGUES[code]
    closing = lg.source == "fdnew"
    hist = results.history(conn, code, blend_xg=code in xg.LEAGUES)
    rows = conn.execute("SELECT id, date, season, home_team, away_team, fthg, ftag FROM matches "
                        "WHERE league = ? AND fthg IS NOT NULL AND date >= ? ORDER BY date", (code, START)).fetchall()
    all_dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM matches WHERE league = ?", (code,))]
    toto = code in toto_leagues
    out, fits = [], 0
    today = date.today().isoformat()
    for m0 in month_starts(START, today):
        y, m = int(m0[:4]), int(m0[5:7])
        m1 = f"{y + (m == 12):04d}-{(m % 12) + 1:02d}-01"
        month = [r for r in rows if m0 <= r["date"] < m1]
        if not month:
            continue
        train = hist[(hist["date"] < m0) & (hist["date"] >= f"{y - 4}-{m0[5:]}")]
        fitted = None
        if len(train) >= model.MIN_TRAIN_MATCHES:
            fitted = model.Poisson().fit(train, as_of=__import__("pandas").Timestamp(m0))
            fits += 1
        for r in month:
            pr = prices_for(conn, r["id"], closing)
            market = P.fair(pr)
            robot_p = fitted.markets(r["home_team"], r["away_team"]) if fitted else None
            basis = "model" if robot_p else "market"
            if robot_p is None:
                robot_p = market
            if robot_p is None:
                continue
            flags = {"derby": derbies.is_derby(code, r["home_team"], r["away_team"]),
                     "after_break": robot.after_break(all_dates, r["date"]),
                     "toto": toto, "closing": closing}
            tips = {}
            for rule in RULES:
                sel, odds, why = robot.tip(robot_p, market, pr, flags, rule)
                tips[rule] = [sel, odds]
            out.append((code, r["date"], r["home_team"], r["away_team"], r["season"], basis,
                        json.dumps({"robot": {k: round(v, 4) for k, v in robot_p.items()},
                                    "market": {k: round(v, 4) for k, v in (market or {}).items()}}),
                        json.dumps(pr) if pr else None,
                        json.dumps({"robot": robot.picks(robot_p), "market": robot.picks(market) if market else None,
                                    "tips": tips}),
                        tips[robot.RULE][0], tips[robot.RULE][1], json.dumps(flags), r["fthg"], r["ftag"]))
    conn.close()
    print(f"{code}: {len(out)} мача, {fits} обучения", flush=True)
    return out


def main():
    toto_file = ROOT / "data" / "leagues.json"
    toto = set()
    if toto_file.exists():
        toto = {c for c, v in json.loads(toto_file.read_text(encoding="utf-8"))["leagues"].items()
                if v.get("toto", {}).get("confirmed")}
    print("тото лиги:", sorted(toto))
    codes = [c for c in (sys.argv[1:] or WITH_HISTORY)]
    with Pool(4) as pool:
        parts = pool.starmap(run_league, [(c, toto) for c in codes])
    conn = db.init()
    conn.execute(f"DELETE FROM backtest_tips WHERE league IN ({','.join('?' * len(codes))})", codes)
    conn.executemany("INSERT OR REPLACE INTO backtest_tips (league, date, home, away, season, basis, probs_json, "
                     "prices_json, picks_json, tip, tip_odds, flags_json, hg, ag) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [row for part in parts for row in part])
    conn.commit()
    summary = summarize(conn)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    report(summary)


def stat(items):
    """items: [(познато ли, цена или None)] -> брой, % познати, доход на единица ± грешка."""
    n = len(items)
    if not n:
        return None
    hits = sum(1 for h, _ in items if h)
    priced = [(h, o) for h, o in items if o]
    s = {"n": n, "hit": hits / n, "hit_se": math.sqrt(hits / n * (1 - hits / n) / n)}
    if priced:
        prof = [(o - 1) if h else -1.0 for h, o in priced]
        mu = sum(prof) / len(prof)
        sd = math.sqrt(sum((x - mu) ** 2 for x in prof) / max(1, len(prof) - 1))
        s.update({"n_priced": len(prof), "roi": mu, "roi_se": sd / math.sqrt(len(prof)),
                  "avg_odds": sum(o for _, o in priced) / len(priced)})
    return s


def summarize(conn):
    rows = conn.execute("SELECT * FROM backtest_tips").fetchall()
    per = {}
    for r in rows:
        probs, pk, flags = json.loads(r["probs_json"]), json.loads(r["picks_json"]), json.loads(r["flags_json"])
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {}) if r["prices_json"] else {}
        period = "select" if r["date"] < SELECT_END else "clean"
        for scope in (r["league"], "ALL"):
            d = per.setdefault(scope, {})
            for who in ("robot", "market"):
                sel_map = pk.get(who) or {}
                for mkt, sel in sel_map.items():
                    for per_key in (period, "all"):
                        d.setdefault(f"{who}:{mkt}:{per_key}", []).append((robot.hit(sel, r["hg"], r["ag"]), avg.get(sel)))
            for rule, (sel, odds) in pk["tips"].items():
                if sel:
                    for per_key in (period, "all"):
                        d.setdefault(f"tip:{rule}:{per_key}", []).append((robot.hit(sel, r["hg"], r["ag"]), odds))
            if flags.get("after_break"):
                sel = pk["robot"]["1x2"]
                d.setdefault("robot:1x2:after_break", []).append((robot.hit(sel, r["hg"], r["ag"]), avg.get(sel)))
                if pk.get("market"):
                    sel = pk["market"]["1x2"]
                    d.setdefault("market:1x2:after_break", []).append((robot.hit(sel, r["hg"], r["ag"]), avg.get(sel)))
            # по сезони за главния съвет
            sel, odds = pk["tips"][robot.RULE]
            if sel:
                d.setdefault(f"tip_season:{r['season']}", []).append((robot.hit(sel, r["hg"], r["ag"]), odds))
    return {"generated": date.today().isoformat(), "start": START, "select_end": SELECT_END, "rule": robot.RULE,
            "leagues": {lg: {k: stat(v) for k, v in d.items()} for lg, d in per.items()}}


def report(summary):
    a = summary["leagues"]["ALL"]
    print("\nГЛАВЕН СЪВЕТ - правила (цена >= 1.40, без дербитата):")
    for rule in RULES:
        s, c = a.get(f"tip:{rule}:select"), a.get(f"tip:{rule}:clean")
        f = lambda x: (f"{x['n']:>6} | позн. {x['hit']:.1%} | ср.к {x.get('avg_odds', 0):.2f} | "
                       f"доход {x.get('roi', 0):+.2%} ± {x.get('roi_se', 0):.2%}") if x else "-"
        print(f"  {rule:<14} ИЗБОР {f(s)}")
        print(f"  {'':<14} ЧИСТА {f(c)}")
    print("\nПо пазари, всички лиги (цялият период):")
    for mkt in ("1x2", "dc", "ou"):
        for who in ("robot", "market"):
            x = a.get(f"{who}:{mkt}:all")
            if x:
                print(f"  {mkt:<4} {who:<7} {x['n']:>6} | позн. {x['hit']:.1%} | доход {x.get('roi', 0):+.2%} ± {x.get('roi_se', 0):.2%}")


if __name__ == "__main__":
    main()
