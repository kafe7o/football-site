"""
Подреждането на мачовете в колонките и удвояването на залога (собственикът, 2026-10-11: „как да станат нещата с подреждането; дали има удвояване в някои пускания и как би се образувало“).

Основа: най-добрата система от research/master_v2_system.py - вариант 2, правилата на майстора, праг 1.40, по 2 мача, най-добрите първенства (избрани от периода на избора),
петък-неделя и всеки ден, до 2 колонки на ден. Тук се сменя само КАК се подреждат кандидатите на деня преди да се режат на колонки, и как се залага.

ПРОТОКОЛ (записан преди пускането):
Подредби: (а) по шанс - най-вероятните първи (сегашното); (б) по цена - най-високият коефициент първи; (в) по очаквана стойност шанс x цена; (г) по съгласие - най-малка разлика робот-пазар
първи; (д) смесена колонка - най-вероятният + най-високият коефициент; (е) по шанс, но без мачове с цена над 1.70 (само „сигурните“ в границите).
Залог: по 30 € на колонка (сегашното); удвояване след загуба (30, 60, 120, ... до 4 удвоявания, после пак 30) - МАРТИНГЕЙЛ; и „на минус - по-малко“ (30 € при печалба, 15 € след загуба).
Пари: всеки месец от 300 €, спира щом банката е под залога. Мери се: минават, коефициент, връщане (избор / чиста) и парите на месец (средно, +100 €, минус, най-лош, колко месеца
парите са свършили). Описателно. Резултатът: data/ordering_backtest.json.
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

from research import goal_lines_backtest as gl                    # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import master_v2_system as ms                       # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "ordering_backtest.json"
SELECT_END = vb.SELECT_END
BANK, STAKE = 300.0, 30.0
ORDERS = ("по шанс", "по цена", "по шанс x цена", "по съгласие", "смесена", "по шанс, цена до 1.70")


def order_ids(rows, picks, ids, how):
    A = vb.arrays(rows)
    def gap(i):
        k = vb.EV.index(picks[i][0])
        q = A["Q"][i, k]
        return abs(picks[i][1] - q) if not np.isnan(q) else 1.0
    if how == "по шанс":
        return sorted(ids, key=lambda i: -picks[i][1])
    if how == "по цена":
        return sorted(ids, key=lambda i: -picks[i][2])
    if how == "по шанс x цена":
        return sorted(ids, key=lambda i: -picks[i][1] * picks[i][2])
    if how == "по съгласие":
        return sorted(ids, key=gap)
    if how == "по шанс, цена до 1.70":
        return sorted([i for i in ids if picks[i][2] <= 1.70], key=lambda i: -picks[i][1])
    if how == "смесена":
        return sorted(ids, key=lambda i: -picks[i][1])      # редът е по шанс, смесването е в build
    raise ValueError(how)


def build(rows, picks, ids, size, per_day, how):
    used, cols = set(), []
    by_price = sorted(ids, key=lambda i: -picks[i][2])
    for _ in range(per_day):
        col, lgs = [], set()
        srcs = [ids] if how != "смесена" else [ids, by_price]      # смесена: първият по шанс, вторият - най-високата цена
        for n, src in enumerate(srcs if how == "смесена" else [ids] * size):
            for i in src:
                if i in used or rows[i]["league"] in lgs or i in col:
                    continue
                col.append(i)
                lgs.add(rows[i]["league"])
                break
            if how != "смесена" and len(col) == size:
                break
        if len(col) < size:
            break
        used.update(col)
        cols.append(col)
    return cols


def run(rows, picks, by_day, leagues, size, weekend, per_day, how):
    acc = {"select": [], "clean": []}
    months = defaultdict(list)
    for day, ids in sorted(by_day.items()):
        if weekend and rows[ids[0]]["wd"] < 4:
            continue
        ids = order_ids(rows, picks, [i for i in ids if rows[i]["league"] in leagues], how)
        for col in build(rows, picks, ids, size, per_day, how):
            item = (all(picks[i][3] for i in col), math.prod(picks[i][2] for i in col))
            acc["select" if day < SELECT_END else "clean"].append(item)
            months[day[:7]].append(item)
    return {k: gl.stat(v) for k, v in acc.items()}, months


def money(months, staking):
    ends, busts = {}, 0
    for m, cols in sorted(months.items()):
        b, stake, losses = BANK, STAKE, 0
        bust = False
        for ok, odds in cols:
            if b < stake:
                bust = True
                break
            b += stake * (odds - 1) if ok else -stake
            if staking == "удвояване":
                losses = 0 if ok else min(losses + 1, 4)
                stake = STAKE * (2 ** losses) if losses else STAKE
            elif staking == "на минус по-малко":
                stake = STAKE if ok else STAKE / 2
        busts += bust
        ends[m] = round(b - BANK, 2)
    v = sorted(ends.values())
    return {"months": len(v), "avg": sum(v) / len(v), "median": v[len(v) // 2], "worst": v[0], "best": v[-1],
            "plus100": sum(x >= 100 for x in v), "minus": sum(x < 0 for x in v), "busts": busts}


def main():
    rows = vb.build()
    picks = mr.master_picks(rows, "вариант 2", False, False, floor=1.40)
    by_day = ms.top_picks(rows, picks)
    leagues = ms.best_leagues(ms.leg_stats(rows, picks, by_day, "league"))
    print("най-добрите първенства:", sorted(leagues))
    res = []
    for weekend, how in product((True, False), ORDERS):
        st, months = run(rows, picks, by_day, leagues, 2, weekend, 2, how)
        row = {"weekend": weekend, "order": how, "stats": st, "money": {stk: money(months, stk) for stk in ("по 30 €", "удвояване", "на минус по-малко")}}
        res.append(row)
    print("\nПОДРЕЖДАНЕ (по 2, до 2 на ден, най-добрите първенства, праг 1.40) - залог по 30 €:")
    for v in res:
        s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]["по 30 €"]
        print(f"  {'пет-нед ' if v['weekend'] else 'всеки ден'} {v['order']:24} | избор {s.get('n', 0):>3} {s.get('hit', 0):.0%} коеф {s.get('price', 0):.2f} връща {s.get('return', 0):.3f} "
              f"| чиста {c.get('n', 0):>3} {c.get('hit', 0):.0%} връща {c.get('return', 0):.3f} | средно {m['avg']:+.0f}/мес, +100 в {m['plus100']}/{m['months']}, минус в {m['minus']}")
    print("\nЗАЛОГЪТ (подредба по шанс):")
    for v in res:
        if v["order"] != "по шанс":
            continue
        for stk, m in v["money"].items():
            print(f"  {'пет-нед ' if v['weekend'] else 'всеки ден'} {stk:20} | средно {m['avg']:+.0f}/мес (медиана {m['median']:+.0f}), +100 в {m['plus100']}/{m['months']}, минус в {m['minus']}, "
                  f"най-лош {m['worst']:+.0f}, най-добър {m['best']:+.0f}, парите свършват в {m['busts']} месеца")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
