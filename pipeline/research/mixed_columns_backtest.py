"""
Смесени колонки в един ден и праг 1.30 (собственикът, 2026-10-11: „с 1.30 и с най-сигурното; повече от една колонка на ден - 2 мача в едната, 3 в другата, 3 в третата;
и с мачовете от 69-70% нагоре; дай ми всички вариации“).

Основа: вариант 2 + правилата на майстора (research/master_v2_system.py), най-добрите първенства (от избора), най-вероятните мачове първи, един мач на първенство в колонка,
всеки мач в най-много една колонка на ден.
ПРОТОКОЛ (записан преди пускането): праг на цената {1.40, 1.30} x праг на шанса на мача {65, 70%} x шанс на колонката (произведението) {без, 50%} x дни {всеки ден, чет-нед, пет-нед}
x форма на деня {2 | 3 | 4 | 2+2 | 2+3 | 3+3 | 2+4 | 2+2+2 | 2+2+3 | 2+3+3 | 3+3+3 | 2+2+2+2} = 288 варианта. Формата казва колко колонки и по колко мача се правят в деня, подред
от най-вероятните; ако мачовете не стигат за дадена колонка, тя не се прави.
Мери се: колонки на месец, минават, коефициент, връщане от 1 € (избор / чиста ± грешка), пари при 300 €/30 € (средно на месец, +100 €, минус). Описателно - 288 варианта,
най-добрият е оптимистичен. Резултатът: data/mixed_columns_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import goal_lines_backtest as gl                    # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import master_v2_system as ms                       # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "mixed_columns_backtest.json"
SELECT_END = vb.SELECT_END
DAYS = {"всеки ден": (0, 1, 2, 3, 4, 5, 6), "чет-нед": (3, 4, 5, 6), "пет-нед": (4, 5, 6)}
SHAPES = ((2,), (3,), (4,), (2, 2), (2, 3), (3, 3), (2, 4), (2, 2, 2), (2, 2, 3), (2, 3, 3), (3, 3, 3), (2, 2, 2, 2))


def run(rows, picks, by_day, leagues, days, shape, min_col_p):
    acc = {"select": [], "clean": []}
    months = defaultdict(list)
    for day, ids in sorted(by_day.items()):
        if rows[ids[0]]["wd"] not in days:
            continue
        ids = [i for i in ids if rows[i]["league"] in leagues]
        used = set()
        for size in shape:
            col, lgs = [], set()
            for i in ids:
                if i in used or rows[i]["league"] in lgs or len(col) == size:
                    continue
                col.append(i)
                lgs.add(rows[i]["league"])
            if len(col) < size:
                break
            used.update(col)
            if math.prod(picks[i][1] for i in col) < min_col_p:
                continue
            item = (all(picks[i][3] for i in col), math.prod(picks[i][2] for i in col))
            acc["select" if day < SELECT_END else "clean"].append(item)
            months[day[:7]].append(item)
    return {k: gl.stat(v) for k, v in acc.items()}, months


def main():
    rows = vb.build()
    res = []
    for floor, min_p in product((1.40, 1.30), (0.65, 0.70)):
        picks = mr.master_picks(rows, "вариант 2", False, False, floor=floor)
        by_day = ms.top_picks(rows, picks, min_p)
        leagues = ms.best_leagues(ms.leg_stats(rows, picks, by_day, "league"))
        for (dname, days), shape, min_col_p in product(DAYS.items(), SHAPES, (0.0, 0.5)):
            st, months = run(rows, picks, by_day, leagues, days, shape, min_col_p)
            m = ms.money(months) or {}
            res.append({"floor": floor, "min_p": min_p, "days": dname, "shape": "+".join(map(str, shape)), "min_col_p": min_col_p, "stats": st,
                        "money": {k: v for k, v in m.items() if k != "by_month"},
                        "per_month": (st["select"].get("n", 0) + st["clean"].get("n", 0)) / max(1, m.get("months", 1))})
    name = lambda v: f"цена {v['floor']:.2f}, шанс {v['min_p']:.0%}, {v['days']:9} форма {v['shape']:8}" + (" колонка 50%+" if v["min_col_p"] else "")
    ok = [v for v in res if v["stats"]["clean"].get("n", 0) >= 50 and v["stats"]["select"].get("n", 0) >= 100]
    print(f"варианти: {len(res)}, с достатъчно колонки: {len(ok)}")
    print(f"\nНАЙ-ДОБРИ ПО ЧИСТАТА ПРОВЕРКА:\n{'вариант':62} | кол/мес | избор: мин. връща | чиста: мин. връща ± | 300/30: ср./мес +100 минус")
    for v in sorted(ok, key=lambda v: -v["stats"]["clean"]["return"])[:25]:
        s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
        print(f"{name(v):62} | {v['per_month']:>5.1f}   | {s['hit']:>4.0%} {s['return']:.3f}     | {c['hit']:>4.0%} {c['return']:.3f} ±{c['return_se']:.2f} | {m['avg']:+5.0f} {m['plus100']:>3}/{m['months']} {m['minus']:>3}")
    print("\nПО ФОРМА (цена 1.40, шанс 65%, чет-нед, без условие за колонката):")
    for v in res:
        if v["floor"] == 1.40 and v["min_p"] == 0.65 and v["days"] == "чет-нед" and not v["min_col_p"]:
            s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
            print(f"  форма {v['shape']:8} | {v['per_month']:>5.1f} кол/мес | избор {s.get('hit', 0):.0%} {s.get('return', 0):.3f} | чиста {c.get('hit', 0):.0%} {c.get('return', 0):.3f} | {m.get('avg', 0):+.0f}/мес, +100 в {m.get('plus100', 0)}/{m.get('months', 0)}, минус в {m.get('minus', 0)}")
    print("\nПРАГ 1.30 срещу 1.40 и 65% срещу 70% (чет-нед, форма 2, без условие):")
    for v in res:
        if v["days"] == "чет-нед" and v["shape"] == "2" and not v["min_col_p"]:
            s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
            print(f"  цена {v['floor']:.2f}, шанс {v['min_p']:.0%} | {v['per_month']:>5.1f} кол/мес | избор {s['hit']:.0%} {s['return']:.3f} | чиста {c['hit']:.0%} {c['return']:.3f} | {m['avg']:+.0f}/мес, +100 в {m['plus100']}/{m['months']}, минус в {m['minus']}")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
