"""
По-малко колонки, но по-верни - подбор по дни и строгост (собственикът, 2026-10-11: „да се намали броят на колонките, но да са повече верни; виж кое е по-добре - всеки ден,
петък-неделя или четвъртък-неделя“).

Основа: най-добрата система (research/master_v2_system.py) - вариант 2, правилата на майстора, праг 1.40, по 2 мача, най-добрите първенства (от избора).
ПРОТОКОЛ (записан преди пускането): дни {всеки ден, четвъртък-неделя, петък-неделя, събота-неделя} x колонки на ден {1, 2} x праг на шанса на мача {65, 70, 75%}
x съгласие робот-пазар {не, да} x минимален шанс на колонката (произведението на поправените шансове) {без, 50%} = 96 варианта.
Мери се: колонки на месец, минават, коефициент, връщане от 1 € (избор / чиста), пари при 300 €/30 € (средно на месец, +100 €, минус). Описателно.
Резултатът: data/fewer_columns_backtest.json.
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

OUT = ROOT / "data" / "fewer_columns_backtest.json"
SELECT_END = vb.SELECT_END
DAYS = {"всеки ден": (0, 1, 2, 3, 4, 5, 6), "чет-нед": (3, 4, 5, 6), "пет-нед": (4, 5, 6), "съб-нед": (5, 6)}


def run(rows, picks, by_day, leagues, days, per_day, min_col_p):
    acc = {"select": [], "clean": []}
    months = defaultdict(list)
    for day, ids in sorted(by_day.items()):
        if rows[ids[0]]["wd"] not in days:
            continue
        ids = [i for i in ids if rows[i]["league"] in leagues]
        used = set()
        for _ in range(per_day):
            col, lgs = [], set()
            for i in ids:
                if i in used or rows[i]["league"] in lgs or len(col) == 2:
                    continue
                col.append(i)
                lgs.add(rows[i]["league"])
            if len(col) < 2:
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
    for min_p, agree in product((0.65, 0.70, 0.75), (False, True)):
        picks = mr.master_picks(rows, "вариант 2", agree, False, floor=1.40)
        by_day = ms.top_picks(rows, picks, min_p)
        leagues = ms.best_leagues(ms.leg_stats(rows, picks, by_day, "league"))
        for (dname, days), per_day, min_col_p in product(DAYS.items(), (1, 2), (0.0, 0.5)):
            st, months = run(rows, picks, by_day, leagues, days, per_day, min_col_p)
            m = ms.money(months)
            res.append({"days": dname, "per_day": per_day, "min_p": min_p, "agree": agree, "min_col_p": min_col_p, "stats": st,
                        "money": {k: v for k, v in (m or {}).items() if k != "by_month"}, "per_month": (st["select"].get("n", 0) + st["clean"].get("n", 0)) / max(1, (m or {}).get("months", 1))})
    name = lambda v: f"{v['days']:9} до {v['per_day']} на ден, шанс {v['min_p']:.0%}" + (", съгласни" if v["agree"] else "") + (", колонка 50%+" if v["min_col_p"] else "")
    ok = [v for v in res if v["stats"]["clean"].get("n", 0) >= 50 and v["stats"]["select"].get("n", 0) >= 100]
    print("ПО-МАЛКО, НО ПО-ВЕРНИ (по 2, най-добрите първенства, праг 1.40) - подредени по връщане в ЧИСТАТА (истинската оценка):")
    print(f"{'вариант':52} | кол/мес | избор: минават връща | чиста: минават връща | 300/30: ср./мес +100 минус")
    for v in sorted(ok, key=lambda v: -v["stats"]["clean"]["return"])[:20]:
        s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
        print(f"{name(v):52} | {v['per_month']:>5.1f}   | {s['hit']:>5.0%} {s['return']:.3f}       | {c['hit']:>5.0%} {c['return']:.3f}       | {m['avg']:+5.0f} {m['plus100']:>3}/{m['months']} {m['minus']:>3}")
    print("\nПО ДНИ (шанс 65%, до 2 на ден, без други условия):")
    for v in res:
        if v["min_p"] == 0.65 and not v["agree"] and v["per_day"] == 2 and not v["min_col_p"]:
            s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
            print(f"  {v['days']:9} | {v['per_month']:>5.1f} кол/мес | избор {s['hit']:.0%} {s['return']:.3f} | чиста {c['hit']:.0%} {c['return']:.3f} | {m['avg']:+.0f}/мес, +100 в {m['plus100']}/{m['months']}, минус в {m['minus']}")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
