"""
Над 1.5 срещу под 3.5 - кое да се избира и може ли система с 80% познати (собственикът, 2026-10-11: „почти всички над 1.5 излизат, а под 3.5 - на косъм; в някои мачове
ботът дава под 3.5, а по-сигурното е над 1.5 - виж как да се избира за най-доброто решение, система с 80%“).

Данни: backtest_tips (walk-forward), мачовете с пазар над/под 2.5 (22-те лиги). Шансове: на робота (Poisson от очакваните му голове) и на ПАЗАРА (общите голове λ от цената на
над/под 2.5 без маржа, Poisson). Цени: 1/X/2, двоен шанс, над/под 2.5 - средните на букмейкърите; над/под 1.5 / 3.5 - оценка от пазара с 5% марж (проверена в safe_legs_backtest.py).

ПРОТОКОЛ (записан преди пускането):
  1) За всички мачове: колко излизат над 1.5 и под 3.5; какво казва роботът и какво пазарът (калибровка по групи от шанса).
  2) Мачовете, в които роботът избира под 3.5: колко излиза под 3.5 и колко над 1.5 в същите мачове; правило „от двете - по-вероятното по ПАЗАРА“ срещу „по робота“.
  3) „Система 80%“: единични прогнози - най-вероятното от 1/X/2, двоен шанс, над/под 1.5/2.5/3.5 с шанс по пазара >= 80% (и вариант: най-вероятното по робота >= 80%),
     по едно на първенство на ден, топ шанс; колко излизат, средна цена, връщане от 1 €; и колонки по 2 от тях.
Мери се в избора (до 2025-07-01) и в чистата проверка (от там). Описателно - правилата на майстора не се пипат. Резултатът: data/goal_lines_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from scipy.stats import poisson

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import columns, robot                                 # noqa: E402
from research import columns_backtest as cb                     # noqa: E402
from research import safe_legs_backtest as sl                   # noqa: E402

OUT = ROOT / "data" / "goal_lines_backtest.json"
SELECT_END = sl.SELECT_END
PICK_SELS = ("1", "X", "2", "1X", "X2", "12", "O15", "U15", "O", "U", "O35", "U35")


def market_probs(m):
    avg = m["avg"]
    po = (1 / avg["O"]) / (1 / avg["O"] + 1 / avg["U"])
    lam = sl.lam_from(po)
    q = {"O15": 1 - poisson.cdf(1, lam), "U15": poisson.cdf(1, lam), "O": po, "U": 1 - po, "O35": 1 - poisson.cdf(3, lam), "U35": poisson.cdf(3, lam)}
    if all(avg.get(s) for s in ("1", "X", "2")):
        inv = {s: 1 / avg[s] for s in ("1", "X", "2")}
        tot = sum(inv.values())
        q.update({s: inv[s] / tot for s in ("1", "X", "2")})
        q.update({"1X": q["1"] + q["X"], "X2": q["X"] + q["2"], "12": q["1"] + q["2"]})
    return q


def stat(items):
    """items: [(излязло, цена)] -> {n, hit, se, price, return, return_se}"""
    n = len(items)
    if not n:
        return {"n": 0}
    h = sum(x for x, _ in items) / n
    pay = [p if x else 0.0 for x, p in items]
    m = sum(pay) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in pay) / (n - 1)) if n > 1 else 0
    return {"n": n, "hit": h, "se": math.sqrt(h * (1 - h) / n), "price": sum(p for _, p in items) / n, "return": m, "return_se": sd / math.sqrt(n)}


def fmt(x):
    return (f"{x['n']:>5} | излизат {x['hit']:.1%} ± {x['se']:.1%} | ср. цена {x['price']:.2f} | връща {x['return']:.3f} ± {x['return_se']:.3f} €"
            if x.get("n") else "    0")


def main():
    days = sl.load()
    res = {}
    per_of = lambda d: "select" if d < SELECT_END else "clean"
    # 1) всички мачове: над 1.5 и под 3.5 - излизане и калибровка
    cal = defaultdict(lambda: defaultdict(list))
    base = defaultdict(lambda: defaultdict(list))
    for day, ms in days.items():
        for m in ms:
            q = market_probs(m)
            for s in ("O15", "U35"):
                h = bool(robot.hit_any(s, m["hg"], m["ag"]))
                base[per_of(day)][s].append((h, m["real"][s]))
                for src, p in (("робот", m["p"][s]), ("пазар", q[s])):
                    cal[(s, src)][min(int(p * 10), 9)].append(h)
    print("1) ВСИЧКИ МАЧОВЕ")
    for per in ("select", "clean"):
        for s in ("O15", "U35"):
            print(f"  {per:6} {robot.label(s):9}: " + fmt(stat(base[per][s])))
    print("  калибровка (шанс по групи -> колко излиза):")
    for (s, src), g in sorted(cal.items()):
        print(f"   {robot.label(s):9} по {src}: " + "  ".join(f"{b * 10}-{b * 10 + 10}%: {sum(v) / len(v):.0%} ({len(v)})" for b, v in sorted(g.items()) if len(v) >= 50))
    # 2) мачовете, където роботът избира под 3.5 (едната прогноза по сегашното правило)
    print("\n2) РОБОТЪТ ИЗБИРА ПОД 3.5 - какво излиза в същите мачове")
    two = defaultdict(lambda: defaultdict(list))
    for day, ms in days.items():
        for m in ms:
            one = robot.one_pick(m["rb"], m["avg"], None, m["league"])
            if not one or one["sel"] != "U35":
                continue
            q = market_probs(m)
            per = per_of(day)
            for s in ("U35", "O15"):
                two[per][s].append((bool(robot.hit_any(s, m["hg"], m["ag"])), m["real"][s]))
            best_m = "O15" if q["O15"] > q["U35"] else "U35"
            best_r = "O15" if m["p"]["O15"] > m["p"]["U35"] else "U35"
            two[per]["по пазара"].append((bool(robot.hit_any(best_m, m["hg"], m["ag"])), m["real"][best_m]))
            two[per]["по робота"].append((bool(robot.hit_any(best_r, m["hg"], m["ag"])), m["real"][best_r]))
            two[per]["пазарът казва по-вероятно над 1.5"].append((q["O15"] > q["U35"], 1.0))
    for per in ("select", "clean"):
        for k in ("U35", "O15", "по робота", "по пазара"):
            print(f"  {per:6} {robot.label(k) if k in robot.LABELS else k:10}: " + fmt(stat(two[per][k])))
        x = two[per]["пазарът казва по-вероятно над 1.5"]
        print(f"  {per:6} в {sum(a for a, _ in x) / len(x):.0%} от тези мачове пазарът дава над 1.5 по-голям шанс от под 3.5")
    res["under35_picks"] = {per: {k: stat(v) for k, v in d.items()} for per, d in two.items()}
    # 3) система 80%
    print("\n3) СИСТЕМА 80% (по едно на първенство на ден, само топ шанс)")
    sys80 = defaultdict(lambda: defaultdict(list))
    for day, ms in sorted(days.items()):
        per = per_of(day)
        for basis in ("пазар", "робот"):
            cur = []
            for m in ms:
                q = market_probs(m)
                src = q if basis == "пазар" else m["p"]
                best = max(((s, src[s]) for s in PICK_SELS if s in src and s in m["real"]), key=lambda x: x[1], default=None)
                if not best or best[1] < 0.80:
                    continue
                cur.append({**m, "flags": dict(m["flags"]), "one": {"sel": best[0], "p": best[1], "odds": round(m["real"][best[0]], 2), "src": "book"}})
            cb.mark_sure(cur)
            cands = [c for c in sl.sure_cands(cur) if c["p"] >= 0.80]
            by_id = {m["id"]: m for m in cur}
            for col in columns.build(cands, size=1, max_columns=99, diversify="league"):
                c = col[0]
                sys80[per][f"единични по {basis}"].append((bool(robot.hit_any(c["sel"], by_id[c["id"]]["hg"], by_id[c["id"]]["ag"])), by_id[c["id"]]["real"][c["sel"]]))
            for col in columns.build(cands, size=2, max_columns=4, diversify="league"):
                ok = all(robot.hit_any(c["sel"], by_id[c["id"]]["hg"], by_id[c["id"]]["ag"]) for c in col)
                odds = math.prod(by_id[c["id"]]["real"][c["sel"]] for c in col)
                sys80[per][f"колонки по 2 по {basis}"].append((bool(ok), odds))
    for per in ("select", "clean"):
        for k in sorted(sys80[per]):
            print(f"  {per:6} {k:22}: " + fmt(stat(sys80[per][k])))
    res["system80"] = {per: {k: stat(v) for k, v in d.items()} for per, d in sys80.items()}
    res["all"] = {per: {s: stat(v) for s, v in d.items()} for per, d in base.items()}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
