"""
БАНКАТА НАЗАД: ако планът „200 -> 400 € за месец“ (bets/bank.py: дял 25% от текущата банка на колонка) беше вървял от януари 2024 до днес, месец по месец
(собственикът, 24.10: „ако сме започнали с това нещо през 2024 до днес колко пари щяхме да сме направили“).

ПРОТОКОЛ (записан преди пускането):
  1. Колонките са точно двете логики, върху които върви банката, построени назад по същите правила като на живо:
     СИСТЕМАТА - research/fewer_columns_backtest.py: вариант 2 (поправен шанс), правилата на майстора, праг 1.40, по 2 мача, четвъртък-неделя, най-много 1 на ден,
       най-добрите първенства (от избора), шанс на колонката >= 50%; цена - средната на букмейкърите;
     НАДСТРОЙКАТА - research/adaptive_columns_backtest.py: без филтър, 4 от 5 (5 четворки от 5-те най-добри мача), най-добрата половина първенства;
       цена - най-добрият букмейкър (както на живо) И средната (отделно - по-строгото).
  2. Всеки календарен месец започва наново с 200 € и цел 400 €. Залогът е ДЯЛ 25% от текущата банка: на колонка на системата и на ден с надстройка (разделен поравно
     на четворките). Спира се при банка >= 400 € (цел) или под 1 €. Без правилото 30 €/ден и без таван -30 € - само планът на банката.
  3. Мери се по месеци: крайна банка, стигната ли е целта, срив (под 40 €); сборът на резултатите (всеки месец наново 200 €) - общо и за периодите
     ИЗБОР (до 2025-07-01: правилата са избрани по тези данни - оптимистично) и ЧИСТА (от 2025-07-01: истинската оценка) отделно.
  4. За сведение: без цел и без наново - 200 € на 01.01.2024 с дял 25% до края (едно-единствено минало; разсейването е огромно).
Нищо в правилата не се сменя. Резултат: data/bank_history_backtest.json. Пуска се в облака (research.yml), защото иска архива.
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import adaptive_columns_backtest as ab              # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import master_v2_system as ms                       # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "bank_history_backtest.json"
FROM, SELECT_END = "2024-01", vb.SELECT_END
BANK, TARGET, SHARE, BUST = 200.0, 400.0, 0.25, 40.0
DAYS = (3, 4, 5, 6)                                                # четвъртък-неделя


def system_columns(rows):
    """{ден: [(минала, коефициент)]} - колонката на системата по правилото на живо (най-много една на ден)."""
    picks = mr.master_picks(rows, "вариант 2", False, False, floor=1.40)
    by_day = ms.top_picks(rows, picks, 0.65)
    leagues = ms.best_leagues(ms.leg_stats(rows, picks, by_day, "league"))
    out = defaultdict(list)
    for day, ids in sorted(by_day.items()):
        if rows[ids[0]]["wd"] not in DAYS:
            continue
        col, lgs = [], set()
        for i in ids:
            if rows[i]["league"] in leagues and rows[i]["league"] not in lgs and len(col) < 2:
                col.append(i)
                lgs.add(rows[i]["league"])
        if len(col) == 2 and math.prod(picks[i][1] for i in col) >= 0.5:
            out[day].append((all(picks[i][3] for i in col), math.prod(picks[i][2] for i in col)))
    return out


def addon_columns(legs, book):
    """{ден: [(минала, коефициент)] x 5} - четворките на надстройката (4 от 5) по правилото на живо."""
    dl = ab.filtered(legs, "няма", ab.season_starts(legs))
    _, items = ab.simulate(dl, lambda d: "4 от 5", book, ab.best_half(legs, book), ab.bias_by_day(legs))
    out = defaultdict(list)
    for d, ok, odds in items:
        out[d].append((ok, odds))
    return out


def run_month(days, sys_cols, add_cols):
    """Един месец по плана: (крайна банка, стигната цел, срив, колонки)."""
    b, n = BANK, 0
    for d in days:
        if b >= TARGET or b < 1:
            break
        for ok, odds in sys_cols.get(d, []):
            st = SHARE * b
            b += st * (odds - 1) if ok else -st
            n += 1
        fours = add_cols.get(d, [])
        if fours:
            st = SHARE * b / len(fours)
            b += sum(st * (odds - 1) if ok else -st for ok, odds in fours)
            n += len(fours)
    return round(b, 2), b >= TARGET, b < BUST, n


def continuous(days, sys_cols, add_cols):
    """200 € на първия ден, дял 25%, без цел и без наново - крайната банка (за сведение)."""
    b = BANK
    for d in days:
        if b < 1:
            break
        for ok, odds in sys_cols.get(d, []):
            st = SHARE * b
            b += st * (odds - 1) if ok else -st
        fours = add_cols.get(d, [])
        if fours:
            st = SHARE * b / len(fours)
            b += sum(st * (odds - 1) if ok else -st for ok, odds in fours)
    return round(b, 2)


def main():
    rows = vb.build()
    print("мачове:", len(rows), flush=True)
    sys_cols = system_columns(rows)
    legs = ab.legs_all()
    res = {"from": FROM, "select_end": SELECT_END, "bank": BANK, "target": TARGET, "share": SHARE, "books": {}}
    for book in ("max", "avg"):
        add_cols = addon_columns(legs, book)
        all_days = sorted(d for d in set(sys_cols) | set(add_cols) if d >= FROM)
        months = sorted({d[:7] for d in all_days})
        table = []
        for m in months:
            end, hit, bust, n = run_month([d for d in all_days if d[:7] == m], sys_cols, add_cols)
            table.append({"month": m, "end": end, "net": round(end - BANK, 2), "target": hit, "bust": bust, "columns": n,
                          "period": "избор" if m + "-01" < SELECT_END else "чиста"})
        summ = {}
        for per in ("избор", "чиста", "всичко"):
            t = [r for r in table if per == "всичко" or r["period"] == per]
            if t:
                summ[per] = {"months": len(t), "net": round(sum(r["net"] for r in t), 2), "target": sum(r["target"] for r in t), "bust": sum(r["bust"] for r in t),
                             "plus": sum(r["net"] > 0 for r in t), "avg": round(sum(r["net"] for r in t) / len(t), 2),
                             "worst": min(r["net"] for r in t), "best": max(r["net"] for r in t)}
        res["books"][book] = {"months": table, "summary": summ, "continuous": continuous(all_days, sys_cols, add_cols),
                              "sys_cols": sum(len(v) for d, v in sys_cols.items() if d >= FROM), "addon_fours": sum(len(v) for d, v in add_cols.items() if d >= FROM)}
        label = "най-добрият букмейкър" if book == "max" else "средната цена"
        print(f"\n=== НАДСТРОЙКАТА ПРИ {label.upper()} (системата винаги на средна цена) ===")
        print(f"колонки на системата: {res['books'][book]['sys_cols']}, четворки на надстройката: {res['books'][book]['addon_fours']}")
        print(f"{'месец':8} {'период':6} {'колонки':>7} {'край':>8} {'резултат':>9}  цел  срив")
        for r in table:
            print(f"{r['month']:8} {r['period']:6} {r['columns']:>7} {r['end']:>8.0f} {r['net']:>+9.0f}  {'ДА' if r['target'] else '-':>3}  {'ДА' if r['bust'] else '-':>3}")
        for per, s in summ.items():
            print(f"{per:8}: {s['months']} месеца, сбор {s['net']:+.0f} € (средно {s['avg']:+.0f}/мес), цел в {s['target']}, срив в {s['bust']}, на плюс {s['plus']}, "
                  f"най-лош {s['worst']:+.0f}, най-добър {s['best']:+.0f}")
        print(f"за сведение - 200 € на 01.01.2024 без цел и без наново: {res['books'][book]['continuous']:.0f} €")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("\nзаписано:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
