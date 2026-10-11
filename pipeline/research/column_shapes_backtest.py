"""
Как да се правят колонките, за да носят пари? Размер, състав, групиране - проба-грешка (собственикът, 2026-10-11: „идеята е с 600 € начало да правим по 400 € месечно, най-малко 200 €,
до 600-800 €; пробвай с вариации и проба-грешка как да се образуват колонките, как и защо да се групират“).

Данни: research/variations_backtest.py (мачовете с пазар над/под 2.5, 2023-07..днес, истински / оценени цени). Мач за колонка: най-вероятното събитие по СМЕС (половин робот, половин
пазар), само когато роботът и пазарът са в рамките на 5 пункта (устойчивото от variations_backtest), топ шанс (третината на първенството за деня).
Сигурни: шанс >= 80%; средни: 65-80%.

ПРОТОКОЛ (записан преди пускането): варианти = размер {2, 3, 4} x състав {само сигурни; 2 сигурни + 1 среден (за 2 мача: 1+1); 1 сигурен + останалите средни; само средни}
x групиране {един мач на първенство; до 2 от първенство} x дни {всички; петък-неделя} = 48 варианта, до 4 колонки на ден, всеки мач в една колонка, най-вероятните първи.
За сравнение - сегашните колонки (variations_backtest: СЕГАШНОТО, по 3).
Мери се: колко минават, среден коефициент, връщане от 1 € (избор до 2025-07-01 / чиста от там). Избира се най-доброто по връщане в ИЗБОРА; чистата е истинската оценка.
И ПАРИ ПО МЕСЕЦИ: всеки календарен месец започва с 600 € и по 30 € на колонка (както собственикът играе); колко е накрая на месеца - средно, най-лош и най-добър месец, в колко
месеца е +200 € и повече, в колко - на минус. Описателно - правилата на майстора не се пипат. Резултатът: data/column_shapes_backtest.json.
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

from research import goal_lines_backtest as gl                   # noqa: E402
from research import variations_backtest as vb                   # noqa: E402

OUT = ROOT / "data" / "column_shapes_backtest.json"
SELECT_END = vb.SELECT_END
BANK, STAKE = 600.0, 30.0
COMPS = {"само сигурни": lambda n: (n, 0), "сигурни + 1 среден": lambda n: (n - 1, 1), "1 сигурен + средни": lambda n: (1, n - 1), "само средни": lambda n: (0, n)}


def pool(rows):
    """{ден: [(номер, събитие, шанс, цена, излязло, първенство, ден от седмицата)]} - топ шанс по смес, съгласни робот и пазар."""
    picks = vb.variant_picks(rows, "смес", "всички", 0.5, 0.0, True, False)
    by_group = defaultdict(list)
    for i, (e, s, r, h) in picks.items():
        by_group[(rows[i]["league"], rows[i]["day"])].append((i, s))
    keep = set()
    for items in by_group.values():
        keep |= vb.top_third(items)
    out = defaultdict(list)
    for i in keep:
        e, s, r, h = picks[i]
        if s >= 0.65:
            out[rows[i]["day"]].append((i, e, s, r, h, rows[i]["league"], rows[i]["wd"]))
    return out


def build_cols(legs, size, comp, per_league):
    n_safe, n_mid = COMPS[comp](size)
    safe = sorted([x for x in legs if x[2] >= 0.80], key=lambda x: -x[2])
    mid = sorted([x for x in legs if 0.65 <= x[2] < 0.80], key=lambda x: -x[2])
    used, cols = set(), []
    for _ in range(4):
        col, lgs = [], defaultdict(int)
        for src, need in ((safe, n_safe), (mid, n_mid)):
            got = 0
            for x in src:
                if got == need:
                    break
                if x[0] in used or lgs[x[5]] >= per_league:
                    continue
                col.append(x)
                lgs[x[5]] += 1
                got += 1
        if len(col) < size:
            break
        used.update(x[0] for x in col)
        cols.append(col)
    return cols


def run(days_pool, size, comp, per_league, weekend):
    acc = {"select": [], "clean": []}
    months = defaultdict(list)
    for day in sorted(days_pool):
        legs = days_pool[day]
        if weekend and (not legs or legs[0][6] < 4):
            continue
        for col in build_cols(legs, size, comp, per_league):
            ok = all(x[4] for x in col)
            odds = math.prod(x[3] for x in col)
            acc["select" if day < SELECT_END else "clean"].append((ok, odds))
            months[day[:7]].append((ok, odds))
    return {k: gl.stat(v) for k, v in acc.items()}, months


def money(months):
    """Всеки месец от 600 €, по 30 € на колонка; ако парите свършат - спира."""
    ends = {}
    for m, cols in sorted(months.items()):
        b = BANK
        for ok, odds in cols:
            if b < STAKE:
                break
            b += STAKE * (odds - 1) if ok else -STAKE
        ends[m] = round(b - BANK, 2)
    v = list(ends.values())
    if not v:
        return None
    return {"months": len(v), "avg": sum(v) / len(v), "worst": min(v), "best": max(v), "plus200": sum(x >= 200 for x in v), "minus": sum(x < 0 for x in v), "by_month": ends}


def current_months(rows):
    """Сегашните колонки по 3 (variations_backtest СЕГАШНОТО) - по месеци."""
    picks = vb.current_picks(rows)
    _, keep = vb.evaluate(rows, picks, 0.65)
    by_day = defaultdict(list)
    for i in keep:
        by_day[rows[i]["day"]].append(i)
    months = defaultdict(list)
    for day, ids in sorted(by_day.items()):
        ids = sorted(ids, key=lambda i: -picks[i][1])
        used = set()
        for _ in range(4):
            col, lgs = [], set()
            for i in ids:
                if i in used or rows[i]["league"] in lgs or len(col) == 3:
                    continue
                col.append(i)
                lgs.add(rows[i]["league"])
            if len(col) < 3:
                break
            used.update(col)
            months[day[:7]].append((all(picks[i][3] for i in col), math.prod(picks[i][2] for i in col)))
    return months


def main():
    rows = vb.build()
    days_pool = pool(rows)
    res = []
    for size, comp, per_league, weekend in product((2, 3, 4), COMPS, (1, 2), (False, True)):
        stats, months = run(days_pool, size, comp, per_league, weekend)
        res.append({"size": size, "comp": comp, "per_league": per_league, "weekend": weekend, "stats": stats, "money": money(months)})
    name = lambda v: f"по {v['size']}, {v['comp']}, {'един' if v['per_league'] == 1 else 'до 2'} от първенство{', само пет-нед' if v['weekend'] else ''}"
    cur = money(current_months(rows))
    print(f"СЕГАШНИТЕ (по 3): месеци {cur['months']}, средно {cur['avg']:+.0f} € на месец, най-лош {cur['worst']:+.0f}, най-добър {cur['best']:+.0f}, "
          f"+200 € и повече в {cur['plus200']}, на минус в {cur['minus']}")
    ok = [v for v in res if v["stats"]["select"].get("n", 0) >= 200]
    ranked = sorted(ok, key=lambda v: -v["stats"]["select"]["return"])
    print("\nВСИЧКИ ВАРИАНТИ (подредени по връщане в избора):")
    for v in ranked:
        s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
        print(f"  {name(v):58} | избор {s['hit']:.0%} коеф {s['price']:.2f} връща {s['return']:.3f} | чиста {c.get('hit', 0):.0%} коеф {c.get('price', 0):.2f} "
              f"връща {c.get('return', 0):.3f} | 600 €/30 €: средно {m['avg']:+.0f}/мес, +200 в {m['plus200']}/{m['months']}, минус в {m['minus']}/{m['months']}")
    OUT.write_text(json.dumps({"current_money": cur, "variants": [{**{k: v[k] for k in ("size", "comp", "per_league", "weekend", "stats")},
                                                                     "money": {k: x for k, x in v["money"].items() if k != "by_month"}} for v in res]},
                              ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
