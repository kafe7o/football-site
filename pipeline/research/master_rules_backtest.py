"""
Колонките С ВСИЧКИ ПРАВИЛА НА МАЙСТОРА, на истински цени (собственикът, 2026-10-11: „пробвай и с правилата на майстора - може да не си ги интегрирал“).

В research/column_shapes_backtest.py и variations_backtest.py правилата бяха само отчасти: без дербита, топ шанс, един мач на първенство - да; НО без цена от 1.40 върху истинската
цена и без „тото“. Тук всички правила на майстора за колонка (bets/rules.py, bets/columns.py) важат едновременно:
  1) прогнозата е най-вероятното събитие (1/X/2, двоен шанс, над/под 1.5/2.5/3.5, двата вкарват);   2) цена от 1.40 нагоре - ИСТИНСКАТА (букмейкър или оценка от пазара);
  3) „тото“ (Нидерландия, Австрия): без фаворит 1/2 с цена 1.30-1.55;   4) без дерби;   5) топ шанс - третината на първенството за деня;   6) шанс >= 65%;
  7) един мач на първенство;   8) до 4 колонки на ден, ден за ден;   9) по 3 мача (правилото), за сравнение и по 2 и по 4.

ПРОТОКОЛ (записан преди пускането): варианти вътре в правилата = шанс за подреждане и прага {по робота (както майсторът); смес робот+пазар; пазар} x {всякакви; роботът и пазарът
в рамките на 5 пункта} x {всички дни; петък-неделя} x размер {2, 3, 4} = 36. Мери се: колко минават, коефициент, връщане от 1 € (избор до 2025-07-01 / чиста от там) и пари по месеци
(всеки месец от 600 €, по 30 € на колонка: средно на месец, +200 € и повече, на минус). Сравнение: СЕГАШНИТЕ колонки (цена от 1.40 върху цената, която роботът вижда).
Описателно - правилата на сайта не се сменят от този скрипт. Резултатът: data/master_rules_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import robot                                            # noqa: E402
from research import column_shapes_backtest as cs                 # noqa: E402
from research import goal_lines_backtest as gl                    # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "master_rules_backtest.json"
SELECT_END = vb.SELECT_END


def calibrated(rows):
    """Вариант 2 (поправеният шанс) за всички 14 събития: p - сбор/(n + 50) по вид избор, ден по ден САМО от предишните дни (едната прогноза на робота и излязло ли е) -
    както слой L1 в research/online_learning_backtest.py."""
    from bets.shadow import kind
    A = vb.arrays(rows)
    if "PC" in A:
        return A["PC"]
    kinds = [kind(e) for e in vb.EV]
    sums = defaultdict(lambda: [0.0, 0])
    PC = A["P"].copy()
    by_day = defaultdict(list)
    for i, row in enumerate(rows):
        by_day[row["day"]].append(i)
    for day in sorted(by_day):
        for i in by_day[day]:
            for k in range(len(vb.EV)):
                sm, n = sums[kinds[k]]
                PC[i, k] = min(max(A["P"][i, k] - sm / (n + 50), 0.01), 0.99)
        for i in by_day[day]:                       # резултатите стават известни след деня
            one = rows[i]["current"]
            if not one or one["sel"] not in vb.EV:
                continue
            k = vb.EV.index(one["sel"])
            sums[kinds[k]][0] += one["p"] - float(A["H"][i, k])
            sums[kinds[k]][1] += 1
    A["PC"] = PC
    return PC


def master_picks(rows, src, agree, weekend):
    """Най-вероятното събитие по правилата на майстора (цена >= 1.40 истинска, без „тото“, без дерби); шанс >= 0.5 за групата на топ шанс."""
    A = vb.arrays(rows)
    P, Q, R = A["P"], A["Q"], A["R"]
    S = P if src == "робот" else Q if src == "пазар" else calibrated(rows) if src == "вариант 2" else (P + Q) / 2
    with np.errstate(invalid="ignore"):
        valid = ~np.isnan(R) & (R >= robot.FLOOR) & ~np.isnan(S) & (S >= 0.5)
        if agree:
            valid &= ~np.isnan(Q) & (np.abs(P - Q) <= 0.05)
    valid &= ~A["derby"][:, None]
    if weekend:
        valid &= (A["wd"] >= 4)[:, None]
    toto = np.zeros_like(valid)
    for i, row in enumerate(rows):
        if row["league"] in robot.TOTO_LEAGUES:
            for k, e in enumerate(vb.EV):
                toto[i, k] = robot.toto_blocked(row["league"], e, R[i, k])
    valid &= ~toto
    Sm = np.where(valid, S, -np.inf)
    best = Sm.argmax(axis=1)
    score = Sm[np.arange(len(rows)), best]
    return {int(i): (vb.EV[best[i]], float(score[i]), float(R[i, best[i]]), bool(A["H"][i, best[i]])) for i in np.nonzero(np.isfinite(score))[0]}


def columns_of(rows, picks, size):
    by_group = defaultdict(list)
    for i, (e, s, r, h) in picks.items():
        by_group[(rows[i]["league"], rows[i]["day"])].append((i, s))
    keep = set()
    for items in by_group.values():
        keep |= vb.top_third(items)
    keep = {i for i in keep if picks[i][1] >= 0.65}
    by_day = defaultdict(list)
    for i in keep:
        by_day[rows[i]["day"]].append(i)
    acc = {"select": [], "clean": []}
    months = defaultdict(list)
    for day, ids in sorted(by_day.items()):
        ids = sorted(ids, key=lambda i: -picks[i][1])
        used = set()
        for _ in range(4):
            col, lgs = [], set()
            for i in ids:
                if i in used or rows[i]["league"] in lgs or len(col) == size:
                    continue
                col.append(i)
                lgs.add(rows[i]["league"])
            if len(col) < size:
                break
            used.update(col)
            item = (all(picks[i][3] for i in col), math.prod(picks[i][2] for i in col))
            acc["select" if day < SELECT_END else "clean"].append(item)
            months[day[:7]].append(item)
    return {k: gl.stat(v) for k, v in acc.items()}, cs.money(months)


def main():
    rows = vb.build()
    cur = cs.money(cs.current_months(rows))
    print(f"СЕГАШНИТЕ (цена от 1.40 върху цената на робота): средно {cur['avg']:+.0f} €/месец, +200 € в {cur['plus200']}/{cur['months']}, минус в {cur['minus']}/{cur['months']}")
    res = []
    srcs = ("вариант 2",) if "--v2" in sys.argv else ("робот", "смес", "пазар", "вариант 2")
    for src, agree, weekend, size in product(srcs, (False, True), (False, True), (3, 2, 4)):
        picks = master_picks(rows, src, agree, weekend)
        st, mon = columns_of(rows, picks, size)
        res.append({"src": src, "agree": agree, "weekend": weekend, "size": size, "stats": st, "money": {k: v for k, v in (mon or {}).items() if k != "by_month"}})
    name = lambda v: f"по {v['size']}, шанс по {v['src']}" + (", съгласни" if v["agree"] else "") + (", пет-нед" if v["weekend"] else "")
    print("\nПО ПРАВИЛАТА НА МАЙСТОРА (истинска цена от 1.40, без тото, без дерби, топ шанс, 65%, един от първенство), подредени по връщане в избора:")
    for v in sorted([v for v in res if v["stats"]["select"].get("n", 0) >= 100], key=lambda v: -v["stats"]["select"]["return"]):
        s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
        print(f"  {name(v):38} | избор {s['n']:>4} {s['hit']:.0%} коеф {s['price']:.2f} връща {s['return']:.3f} | чиста {c.get('n', 0):>4} {c.get('hit', 0):.0%} "
              f"коеф {c.get('price', 0):.2f} връща {c.get('return', 0):.3f} | 600 €/30 €: средно {m.get('avg', 0):+.0f}/мес, +200 в {m.get('plus200', 0)}/{m.get('months', 0)}")
    OUT.write_text(json.dumps({"current_money": {k: v for k, v in cur.items() if k != "by_month"}, "variants": res}, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
