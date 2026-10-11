"""
Без първенствата, където моделът губи най-много, с най-много „тото“ и най-непредвидимите (собственикът, 2026-10-12: „ограничи от колонките лигите, където най-много губи моделът,
лигите с най-много тото и лигите, дето са най-шансови“).

Основа: най-добрата система (вариант 2 + правилата на майстора, праг 1.40, шанс 65%, по 2 мача; research/fewer_columns_backtest.py): А) чет-нед, 1 колонка на ден, шанс на колонката >= 50%;
Б) чет-нед, до 2 на ден, без условие.
ПРОТОКОЛ (записан преди пускането): трите мерки за първенство се смятат САМО от миналото спрямо чистата проверка и се прилагат еднакво в двата периода:
  (а) „губи най-много“ - връщане от 1 € на мачовете на робота в ПЕРИОДА НА ИЗБОРА (до 2025-07-01);
  (б) „най-много тото“ - фаворитът 1.30-1.55 печели по-рядко от обещаното: разликата (станало - обещано) за 2012-2019 от data/leagues.json (research/league_analysis.py);
  (в) „най-шансови“ - колко често НЕ излиза най-вероятният изход по пазара (1/X/2) в периода на избора.
Варианти: {всички първенства; половината най-добри по (а) (сегашното „най-добрите“); без най-лошите K по (а); без K по (б); без K по (в); без обединението на трите} x K {3, 5, 8}
= 2 + 4 x 3 = 14 на система, 28 общо. Мери се: колонки на месец, минават, връщане (избор / чиста ± грешка), пари при 300 €/30 €. Описателно. Резултатът: data/league_filter_backtest.json.
"""

import json
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import fewer_columns_backtest as fc                 # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import master_v2_system as ms                       # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "league_filter_backtest.json"
SELECT_END = vb.SELECT_END
KS = (3, 5, 8)


def league_measures(rows, picks, by_day):
    """(а) връщане на мач в избора; (б) тото 2012-2019; (в) непредвидимост в избора - {първенство: стойност}; по-малко = по-лошо за (а), (б); по-голямо = по-лошо за (в)."""
    lg_stats = ms.leg_stats(rows, picks, by_day, "league")
    a = {lg: d["select"]["return"] for lg, d in lg_stats.items() if d["select"].get("n", 0) >= 50}
    la = json.load(open(ROOT / "data" / "leagues.json", encoding="utf-8"))["leagues"]
    b = {lg: ((v.get("toto") or {}).get("2012-2019") or {}).get("diff") for lg, v in la.items()}
    b = {lg: x for lg, x in b.items() if x is not None}
    A = vb.arrays(rows)
    miss = defaultdict(list)
    for i, row in enumerate(rows):
        if row["day"] >= SELECT_END:
            continue
        q = A["Q"][i, :3]
        if np.isnan(q).any():
            continue
        k = int(np.argmax(q))
        miss[row["league"]].append(0.0 if A["H"][i, k] else 1.0)
    c = {lg: sum(v) / len(v) for lg, v in miss.items() if len(v) >= 100}
    return a, b, c


def worst(measure, k, low_is_bad=True):
    items = sorted(measure.items(), key=lambda kv: kv[1] if low_is_bad else -kv[1])
    return {lg for lg, _ in items[:k]}


def main():
    rows = vb.build()
    picks = mr.master_picks(rows, "вариант 2", False, False, floor=1.40)
    by_day = ms.top_picks(rows, picks, 0.65)
    all_lg = {rows[i]["league"] for ids in by_day.values() for i in ids}
    a, b, c = league_measures(rows, picks, by_day)
    print("(а) връщане на мач в избора, най-лоши:", [(lg, round(x, 3)) for lg, x in sorted(a.items(), key=lambda kv: kv[1])[:8]])
    print("(б) тото 2012-2019 (станало - обещано), най-лоши:", [(lg, round(x, 3)) for lg, x in sorted(b.items(), key=lambda kv: kv[1])[:8]])
    print("(в) непредвидимост в избора (фаворитът на пазара не излиза), най-лоши:", [(lg, round(x, 3)) for lg, x in sorted(c.items(), key=lambda kv: -kv[1])[:8]])
    filters = {"всички": all_lg, "най-добрата половина по (а)": ms.best_leagues(ms.leg_stats(rows, picks, by_day, "league"))}
    for k in KS:
        filters[f"без {k} най-губещи (а)"] = all_lg - worst(a, k)
        filters[f"без {k} най-тото (б)"] = all_lg - worst(b, k)
        filters[f"без {k} най-шансови (в)"] = all_lg - worst(c, k, low_is_bad=False)
        filters[f"без {k} по всяка от трите"] = all_lg - worst(a, k) - worst(b, k) - worst(c, k, low_is_bad=False)
    res = []
    for (sname, per_day, min_col), (fname, leagues) in product((("А: 1 на ден, колонка 50%+", 1, 0.5), ("Б: до 2 на ден", 2, 0.0)), filters.items()):
        st, months = fc.run(rows, picks, by_day, leagues, fc.DAYS["чет-нед"], per_day, min_col)
        m = ms.money(months) or {}
        res.append({"system": sname, "filter": fname, "leagues": sorted(leagues), "stats": st, "money": {k: v for k, v in m.items() if k != "by_month"},
                    "per_month": (st["select"].get("n", 0) + st["clean"].get("n", 0)) / max(1, m.get("months", 1))})
    for sname in ("А: 1 на ден, колонка 50%+", "Б: до 2 на ден"):
        print(f"\n=== {sname} (чет-нед, по 2, праг 1.40, шанс 65%) ===\n{'филтър':34} | първ. | кол/мес | избор: мин. връща | чиста: мин. връща ±  | 300/30: ср./мес +100 минус")
        for v in res:
            if v["system"] != sname:
                continue
            s, cl, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
            print(f"{v['filter']:34} | {len(v['leagues']):>4}  | {v['per_month']:>5.1f}   | {s.get('hit', 0):>4.0%} {s.get('return', 0):.3f}     | {cl.get('hit', 0):>4.0%} {cl.get('return', 0):.3f} ±{cl.get('return_se', 0):.2f} | {m.get('avg', 0):+5.0f} {m.get('plus100', 0):>3}/{m.get('months', 0)} {m.get('minus', 0):>3}")
    OUT.write_text(json.dumps({"measures": {"a": a, "b": b, "c": c}, "variants": res}, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
