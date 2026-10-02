"""
Справка: роботът преди и след правилото „рисковата не противоречи на по-сигурната“ (2026-10-02).

Само мерене - правилото е прието от собственика („както каза майсторът“). Едни и същи мачове: backtest_tips
(walk-forward, само с модел, средните коефициенти), 2023-07 до днес; избор до 2025-07-01, чиста от там.
Първи модел = рисковата както до 02.10 (знак от 2.50, най-много над обичайното за лигата);
втори модел = същото, но само измежду знаците, които по-сигурната и едната прогноза позволяват.
По-сигурната и едната прогноза са едни и същи в двата модела (правилото не ги пипа).
Мери се: рисковата (колко мача, познати, коефициент, доход, евро при 10 € на прогноза, кои знаци), мачовете
с противоречие отделно, двойката по-сигурна + рискова (и двете излизат / едната / нито една), по лиги.
Резултатът: data/before_after_risky.json.
"""

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, robot                                   # noqa: E402
from bets.leagues import LEAGUES                             # noqa: E402
from research.consistent_risky_check import one_simple        # noqa: E402
from research.signs_backtest import base_rates               # noqa: E402

OUT = ROOT / "data" / "before_after_risky.json"
SELECT_END = "2025-07-01"
STAKE = 10
TOP10 = ("E0", "SP1", "I1", "D1", "F1", "T1", "N1", "P1", "B1")


def money(items):
    n = len(items)
    if not n:
        return {"n": 0}
    h = sum(1 for x in items if x["hit"])
    pr = [(x["odds"] - 1) if x["hit"] else -1 for x in items]
    mu = sum(pr) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in pr) / max(1, n - 1))
    signs = Counter(x["sel"] for x in items)
    return {"n": n, "hit": h / n, "odds": sum(x["odds"] for x in items) / n, "roi": mu, "roi_se": sd / math.sqrt(n),
            "eur": mu * n * STAKE, "signs": {s: signs[s] / n for s in ("1", "X", "2")}}


def main():
    conn = db.init()
    base = base_rates(conn)
    R = {m: {"select": [], "clean": []} for m in ("first", "second")}
    conflict = {m: {"select": [], "clean": []} for m in ("first", "second")}
    pair = {m: {"select": Counter(), "clean": Counter()} for m in ("first", "second")}
    lost_risky = {"select": 0, "clean": 0}
    total = {"select": 0, "clean": 0}
    per_league = {m: defaultdict(list) for m in ("first", "second")}
    safer_items = {"select": [], "clean": []}
    for r in conn.execute("SELECT league, date, probs_json, prices_json, hg, ag FROM backtest_tips "
                          "WHERE basis = 'model' AND prices_json IS NOT NULL"):
        if r["league"] not in base:
            continue
        p = json.loads(r["probs_json"])["robot"]
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {})
        if p.get("xg_home") is None or not avg.get("1"):
            continue
        period = "select" if r["date"] < SELECT_END else "clean"
        total[period] += 1
        b = base[r["league"]]
        safer = robot.safe_by_odds(p, avg)
        one = one_simple(p, avg)
        first = robot.risky_by_odds(p, b, avg)
        second = robot.risky_by_odds(p, b, avg, [x for x in ((safer or {}).get("sel"), one) if x])
        is_conflict = bool(first) and first != second
        if is_conflict and not second:
            lost_risky[period] += 1
        sh = robot.hit(safer["sel"], r["hg"], r["ag"]) if safer else None
        if safer and safer.get("src") == "book":
            safer_items[period].append({"hit": sh, "odds": safer["odds"], "sel": safer["sel"]})
        for name, x in (("first", first), ("second", second)):
            if not x or x.get("src") != "book":
                continue
            item = {"hit": robot.hit(x["sel"], r["hg"], r["ag"]), "odds": x["odds"], "sel": x["sel"]}
            R[name][period].append(item)
            per_league[name][r["league"]].append(item)
            if is_conflict:
                conflict[name][period].append(item)
            if safer:
                pair[name][period]["и двете" if item["hit"] and sh else "само по-сигурната" if sh else
                                   "само рисковата" if item["hit"] else "нито една"] += 1
    out = {"total": total, "lost_risky": lost_risky, "periods": {}, "leagues": {},
           "safer": {p: money(v) for p, v in safer_items.items()}}
    for period in ("select", "clean"):
        out["periods"][period] = {
            name: {"all": money(R[name][period]), "conflict_matches": money(conflict[name][period]),
                   "pair": {k: v / max(1, sum(pair[name][period].values())) for k, v in pair[name][period].items()}}
            for name in ("first", "second")}
    for lg in sorted(set(per_league["first"]) | set(per_league["second"])):
        out["leagues"][lg] = {name: money(per_league[name][lg]) for name in ("first", "second")}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for period in ("select", "clean"):
        print(f"\n=== {period} ({total[period]} мача; противоречие в първия: {len(conflict['first'][period])}, рисковата изчезва в {lost_risky[period]}) ===")
        for name, title in (("first", "първи (до 02.10)"), ("second", "втори (от 03.10)")):
            a = out["periods"][period][name]["all"]
            c = out["periods"][period][name]["conflict_matches"]
            pr = out["periods"][period][name]["pair"]
            print(f"{title:<18} рискова: {a['n']} мача, познати {a['hit']:.1%}, ср.к {a['odds']:.2f}, доход {a['roi']:+.1%} ± {a['roi_se']:.1%}, "
                  f"при 10 €: {a['eur']:+.0f} € | знаци 1 {a['signs']['1']:.0%} X {a['signs']['X']:.0%} 2 {a['signs']['2']:.0%}")
            if c.get("n"):
                print(f"{'':<18} само в мачовете с противоречие: {c['n']}, познати {c['hit']:.1%}, ср.к {c['odds']:.2f}, доход {c['roi']:+.1%}, при 10 €: {c['eur']:+.0f} €")
            print(f"{'':<18} двойката по-сигурна + рискова: " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(pr.items(), key=lambda kv: -kv[1])))
        s = out["safer"][period]
        print(f"по-сигурната (същата в двата): {s['n']}, познати {s['hit']:.1%}, ср.к {s['odds']:.2f}, доход {s['roi']:+.1%}")
    print("\nпо лиги (топ 9 с история, двата периода заедно):")
    for lg in TOP10:
        x = out["leagues"].get(lg)
        if x and x["first"].get("n"):
            f, s = x["first"], x["second"]
            print(f"  {LEAGUES[lg].title:<28} първи {f['hit']:.1%} / {f['roi']:+.1%} ({f['n']}) | втори {s['hit']:.1%} / {s['roi']:+.1%} ({s['n']})")
    return out


if __name__ == "__main__":
    main()
