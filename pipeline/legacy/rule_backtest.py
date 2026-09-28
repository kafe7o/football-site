"""
Кое правило за залог е по-добро - с ИСТИНСКИ исторически цени (2026-09-28).

Повод: собственикът залага по „сигнал на модела + група A/B“ и вижда, че на 37-те записани
мача това е 0 от 8. 37 мача не решават нищо. Тук - 741 изиграни мача от 6-те големи лиги.

ПРОТОКОЛ (записан преди пускането):
- Мачове: sim_predictions за E0, SP1, I1, D1, F1, E1 (21.03 - 20.09.2026). Прогнозата на модела
  е walk-forward (обучение само на мачовете отпреди седмицата) - същият модел като на живо.
- Цени: историческа снимка на the-odds-api (регион eu) 30 минути преди първия мач на лигата
  за деня. Една снимка на (лига, ден), 10 кредита. Групата и изборът по цена - със същия код
  като на живо (value.scan_event, value.current_type); цената за сигнала - най-добрата
  не-борсова цена в снимката.
- Правила:
    R1 правилото:           сигнал на модела, само ако мачът е група A или B
    R2 избор по цена A/B:   най-добрата цена над честната, група A или B
    R3 избор по цена A:     същото, само група A
    R4 съгласие:            сигнал на модела на СЪЩИЯ изход като избора по цена (A/B)
    R5 всички сигнали:      за сравнение (залага и на група C - на живо не се ползва)
- Мерки: брой, познати, ROI при еднакъв залог ± стандартна грешка; банка 100 € с 10% от
  текущия баланс и дневния лимит на сайта (група A - до 8, група B - докато общо са под 5).
- Решение: правило е „по-добро“ само ако ROI-то му е с над 2 стандартни грешки над другото.
  Иначе разликата е шум.
"""

import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import config, derbies, odds_api, predict, teams, value      # noqa: E402

UK = ZoneInfo("Europe/London")
LEAGUES = ["E0", "SP1", "I1", "D1", "F1", "E1"]
OUT = config.RESULTS_DIR / "rule_backtest.json"
# python legacy/rule_backtest.py E2,E3,SP2,I2,D2,F2,N1 rule_backtest_more.json - други лиги
# (съветът на професионалиста за по-ниските дивизии и Холандия, 2026-09-28)
if len(sys.argv) > 1:
    LEAGUES = sys.argv[1].split(",")
    OUT = config.RESULTS_DIR / (sys.argv[2] if len(sys.argv) > 2 else "rule_backtest_more.json")
DAY_MAX, DAY_B_MAX, SHARE = 8, 5, 0.10


def snapshot_time(conn, league, day):
    kicks = [r[0] for r in conn.execute(
        "SELECT kickoff FROM matches WHERE league = ? AND date = ? AND kickoff LIKE '%:%'", (league, day))]
    clock = min(kicks) if kicks else "12:00"
    start = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=UK).astimezone(timezone.utc)
    return (start - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def best_prices(event):
    books = value.prices_by_book(event)
    order = value.outcome_order(event, books)
    if len(order) != 3:
        return None
    best = []
    for name in order:
        prices = [p[name] for b, p in books.items() if name in p and b not in value.EXCHANGES]
        if not prices:
            return None
        best.append(max(prices))
    return best


def collect(conn):
    rows = []
    for league in LEAGUES:
        sport = predict.LEAGUE_TO_SPORT[league]
        days = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM sim_predictions WHERE league = ? ORDER BY date", (league,))]
        for day in days:
            sims = [dict(r) for r in conn.execute(
                "SELECT * FROM sim_predictions WHERE league = ? AND date = ?", (league, day))]
            snap = None
            for attempt in range(3):            # мрежата понякога прекъсва - до 3 опита
                try:
                    snap = odds_api.historical_odds(sport, snapshot_time(conn, league, day))
                    break
                except RuntimeError as e:
                    print(f"  {league} {day}: опит {attempt + 1} - {e}", flush=True)
                    import time
                    time.sleep(5)
            if snap is None:
                print(f"  {league} {day}: пропуснат след 3 опита", flush=True)
                continue
            for event in snap.get("data", []):
                start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
                if start.astimezone(UK).date().isoformat() != day:
                    continue
                sim = teams.match_fixture(event["home_team"], event["away_team"], sims)
                if sim is None:
                    continue
                books = value.prices_by_book(event)
                order = value.outcome_order(event, books)
                if len(order) != 3 or len(books) < value.MIN_BOOKS:
                    continue
                sharp, _, fair = value.reference(books, order)
                if not sharp:
                    continue
                kind, pick = value.current_type(value.scan_event(event, sport))
                best = best_prices(event)
                if best is None:
                    continue
                outcome = 0 if sim["fthg"] > sim["ftag"] else (1 if sim["fthg"] == sim["ftag"] else 2)
                probs = [sim["p_home"], sim["p_draw"], sim["p_away"]]
                values = [p * o - 1 for p, o in zip(probs, best)]
                j = max(range(3), key=lambda i: values[i])
                rows.append({
                    "league": league, "date": day, "start": event["commence_time"],
                    "home": sim["home_team"], "away": sim["away_team"], "outcome": outcome,
                    "model": probs, "fair": list(fair),
                    "derby": derbies.is_derby(league, sim["home_team"], sim["away_team"]),
                    "type": kind,
                    "pick": {"idx": pick["outcome_idx"], "odds": pick["odds"], "tier": pick["tier"],
                             "edge": pick["edge"], "p_fair": pick["p_fair"]} if pick else None,
                    "signal": {"idx": j, "odds": best[j], "value": values[j]} if values[j] > 0.05 else None,
                })
    return rows


def bets_for(rows, rule):
    out = []
    for r in rows:
        s, p, t = r["signal"], r["pick"], r["type"]
        if rule == "R1" and s and t in ("A", "B"):
            out.append((r, s["idx"], s["odds"], t, s["value"]))
        elif rule == "R2" and p:
            out.append((r, p["idx"], p["odds"], p["tier"], p["edge"]))
        elif rule == "R3" and p and p["tier"] == "A":
            out.append((r, p["idx"], p["odds"], "A", p["edge"]))
        elif rule == "R4" and s and p and s["idx"] == p["idx"]:
            out.append((r, p["idx"], p["odds"], p["tier"], p["edge"]))
        elif rule == "R5" and s:
            out.append((r, s["idx"], s["odds"], t, s["value"]))
        elif rule == "R6" and p and p["tier"] == "A" and not r["derby"]:
            out.append((r, p["idx"], p["odds"], "A", p["edge"]))
    return out


def measure(bets):
    profits = [(o - 1) if idx == r["outcome"] else -1.0 for r, idx, o, _, _ in bets]
    n = len(profits)
    if not n:
        return {"n": 0}
    mean = sum(profits) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in profits) / (n - 1)) if n > 1 else 0.0
    # банката: дневният лимит от сайта, после 10% от текущия баланс по реда на мачовете
    by_day = {}
    for b in bets:
        by_day.setdefault(b[0]["date"], []).append(b)
    chosen = []
    for day in by_day.values():
        day.sort(key=lambda b: (b[0]["start"], b[0]["home"]))
        chosen.extend(day[:DAY_MAX])
    chosen.sort(key=lambda b: (b[0]["start"], b[0]["home"]))
    bank, low, high = 100.0, 100.0, 100.0
    for r, idx, o, _, _ in chosen:
        if bank < 1:
            break
        stake = bank * SHARE
        bank += stake * (o - 1) if idx == r["outcome"] else -stake
        low, high = min(low, bank), max(high, bank)
    return {"n": n, "wins": sum(1 for x in profits if x > 0), "roi": mean, "se": sd / math.sqrt(n),
            "avg_odds": sum(o for _, _, o, _, _ in bets) / n,
            "bank": {"end": bank, "low": low, "high": high, "bets": len(chosen)}}


def main():
    conn = sqlite3.connect(config.SITE_DIR / "cloud.db")
    conn.row_factory = sqlite3.Row
    rows = collect(conn)
    names = {"R1": "правилото: сигнал + група A/B", "R2": "избор по цена A/B", "R3": "избор по цена само A",
             "R4": "сигнал на същия изход като избора (A/B)", "R5": "всички сигнали (и група C)",
             "R6": "избор по цена A без дербита (правилото)"}
    bets = {k: [[b[0]["date"], b[0]["start"], b[0]["home"], b[0]["away"], b[1], round(b[2], 2),
                 int(b[1] == b[0]["outcome"]), b[3], round(b[4], 4)] for b in bets_for(rows, k)]
            for k in ("R1", "R2", "R6")}
    brier = lambda p, o: sum((p[j] - (j == o)) ** 2 for j in range(3))
    months = {}
    for r in rows:
        m = months.setdefault(r["date"][:7], {"n": 0, "model": 0.0, "market": 0.0, "hit_model": 0, "hit_market": 0})
        m["n"] += 1
        m["model"] += brier(r["model"], r["outcome"])
        m["market"] += brier(r["fair"], r["outcome"])
        m["hit_model"] += int(max(range(3), key=lambda j: r["model"][j]) == r["outcome"])
        m["hit_market"] += int(max(range(3), key=lambda j: r["fair"][j]) == r["outcome"])
    monthly = {k: {"n": v["n"], "brier_model": v["model"] / v["n"], "brier_market": v["market"] / v["n"],
                   "hit_model": v["hit_model"] / v["n"], "hit_market": v["hit_market"] / v["n"]}
               for k, v in sorted(months.items())}
    # Банката по един-единствен път е късмет или нещастие. 10 000 повторения на същите залози
    # с честния шанс показват какво става ОБИКНОВЕНО с 2 € на залог и с 10% от баланса.
    import random
    random.seed(7)
    picks = [(b[0]["pick"]["p_fair"], b[2]) for b in bets_for(rows, "R6")]
    spread = {}
    for mode in ("flat", "share"):
        ends = []
        for _ in range(10000):
            bank = 100.0
            for p_fair, odds in picks:
                stake = 2.0 if mode == "flat" else bank * SHARE
                bank += stake * (odds - 1) if random.random() < p_fair else -stake
            ends.append(bank)
        ends.sort()
        spread[mode] = {"median": ends[5000], "p10": ends[1000], "p90": ends[9000],
                        "below_start": sum(e < 100 for e in ends) / len(ends),
                        "below_20": sum(e < 20 for e in ends) / len(ends)}
    # Може ли моделът да "балансира" избора по цена? (собственикът, 2026-09-28) - три начина,
    # решени ПРЕДИ мача; приема се само ако е по-добър от избора A и в двата периода.
    periods = {"март-май": ("2026-01", "2026-06"), "август-септември": ("2026-06", "2026-12")}
    ways = {"избор A, 2 € (сега)": lambda r, agree: 2.0,
            "3 € ако моделът е съгласен, 1 € ако не": lambda r, agree: 3.0 if agree else 1.0,
            "само ако моделът е съгласен": lambda r, agree: 2.0 if agree else 0.0}
    balance = {}
    for way, stake_of in ways.items():
        balance[way] = {}
        for label, (lo, hi) in periods.items():
            staked = profit = n = 0
            for r in rows:
                p = r["pick"]
                if not p or p["tier"] != "A" or r["derby"] or not lo <= r["date"] < hi:
                    continue
                stake = stake_of(r, r["model"][p["idx"]] >= p["p_fair"])
                if not stake:
                    continue
                n += 1
                staked += stake
                profit += stake * (p["odds"] - 1) if p["idx"] == r["outcome"] else -stake
            balance[way][label] = {"n": n, "roi": profit / staked if staked else None}
    # по лиги: правилото (R6) - познати срещу очакваните по честния шанс
    per_league = {}
    for r, idx, odds, tier, edge in bets_for(rows, "R6"):
        d = per_league.setdefault(r["league"], {"n": 0, "wins": 0, "expected": 0.0, "profit": 0.0})
        d["n"] += 1
        d["wins"] += int(idx == r["outcome"])
        d["expected"] += r["pick"]["p_fair"]
        d["profit"] += odds - 1 if idx == r["outcome"] else -1.0
    for d in per_league.values():
        d["roi"] = d["profit"] / d["n"]
    result = {"matches": len(rows), "credits_left": odds_api._last_remaining, "spread": spread,
              "leagues": LEAGUES, "per_league": per_league,
              "balance": balance,
              "period": [min(r["date"] for r in rows), max(r["date"] for r in rows)],
              "bets": bets, "monthly": monthly,
              "types": {k: sum(1 for r in rows if r["type"] == k) for k in ("A", "B", "C", "-")},
              "rules": {k: {"name": v, **measure(bets_for(rows, k))} for k, v in names.items()},
              "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"мачове с цени: {result['matches']}; групи: {result['types']}; кредити: {result['credits_left']}")
    for k, m in result["rules"].items():
        if not m["n"]:
            print(f"{k} {m['name']}: няма залози")
            continue
        b = m["bank"]
        print(f"{k} {m['name']:<42} {m['n']:>4} залога, познати {m['wins']:>3} ({m['wins'] / m['n']:.0%}), "
              f"ср. коеф. {m['avg_odds']:.2f}, ROI {m['roi']:+.1%} ± {m['se']:.1%}  | банка 100 € -> {b['end']:.2f} € "
              f"(най-ниско {b['low']:.2f}, най-високо {b['high']:.2f}, {b['bets']} залога)")


if __name__ == "__main__":
    main()
