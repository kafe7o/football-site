"""
Колко мача в колонка? Единичен залог, колонка по 2 мача и колонка по 3 мача - на същите мачове (собственикът, 2026-10-11: „може ли нещо да
се промени?“ - след два дни късмет и два лоши; отговорът „по-малко мачове губят по-малко“ беше само сметка, тук е измерен).

ПРОТОКОЛ (записан преди пускането): данни backtest_tips (walk-forward, само с модел, 2023-07-01 до днес), същите като columns_backtest.py.
Правилото за избор на мачове е сегашното (bets/columns.py): едната прогноза на робота с шанс >= 65%, САМО топ шанс (bets/sure.py), един мач на
първенство, без дерби, до 4 „колонки“ на ден. Сменя се само размерът: 1 (единичен залог), 2, 3 (сегашният).
Мери се в избора (до 2025-07-01) и в чистата проверка (от там):
  - колко „колонки“ на ден (колко дни имат поне една);
  - колко минават (± стандартна грешка) срещу казаното от робота (произведението на шансовете);
  - връщане от 1 € САМО за колонки, в които всеки мач има истински коефициент от букмейкър (1/X/2, двоен шанс, над/под 2.5) - за останалите цената е на
    робота и сметката е кръгова; връщането е средно на колонка (коефициент ако мине, иначе 0) ± стандартна грешка.
Общият извод се прави на двата периода заедно; нищо в правилата не се променя от този скрипт - числата са за собственика и майстора
(колонките по 3 са правило на майстора). Резултатът: data/legs_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import columns, robot                              # noqa: E402
from research import columns_backtest as cb                   # noqa: E402

OUT = ROOT / "data" / "legs_backtest.json"
SELECT_END = "2025-07-01"
SIZES = (1, 2, 3)


def run(days, size):
    st = {per: {"days": 0, "days_with": 0, "cols": 0, "pass": 0, "claimed": 0.0, "pay_all": [], "pay_book": []}
          for per in ("select", "clean")}
    for day, ms in days.items():
        per = "select" if day < SELECT_END else "clean"
        s = st[per]
        s["days"] += 1
        by_id = {m["id"]: m for m in ms}
        cb.mark_sure(ms)
        cands = columns.candidates(ms, 0.65, need_sure=True)
        cols = columns.build(cands, size=size, max_columns=4, diversify="league")
        if cols:
            s["days_with"] += 1
        for col in cols:
            ok = all(robot.hit_any(c["sel"], by_id[c["id"]]["hg"], by_id[c["id"]]["ag"]) for c in col)
            sm = columns.summary(col)
            s["cols"] += 1
            s["pass"] += ok
            s["claimed"] += sm["claimed"]
            if sm["book"]:
                s["pay_book"].append(sm["odds"] if ok else 0.0)
    return st


def stats(s):
    n = s["cols"]
    if not n:
        return {"cols": 0}
    p = s["pass"] / n
    pb = s["pay_book"]
    out = {"cols": n, "per_day": n / s["days"], "days_with": s["days_with"] / s["days"], "pass": p,
           "se": math.sqrt(p * (1 - p) / n), "claimed": s["claimed"] / n, "book_n": len(pb)}
    if len(pb) > 1:
        m = sum(pb) / len(pb)
        sd = math.sqrt(sum((x - m) ** 2 for x in pb) / (len(pb) - 1))
        out.update({"book_return": m, "book_se": sd / math.sqrt(len(pb))})
    return out


def merge(a, b):
    return {"days": a["days"] + b["days"], "days_with": a["days_with"] + b["days_with"], "cols": a["cols"] + b["cols"],
            "pass": a["pass"] + b["pass"], "claimed": a["claimed"] + b["claimed"], "pay_all": [], "pay_book": a["pay_book"] + b["pay_book"]}


def main():
    days = cb.load()
    res = {}
    for size in SIZES:
        st = run(days, size)
        res[size] = {"select": stats(st["select"]), "clean": stats(st["clean"]), "all": stats(merge(st["select"], st["clean"]))}
    for size in SIZES:
        for per in ("select", "clean", "all"):
            x = res[size][per]
            if not x["cols"]:
                print(f"по {size} {per}: няма")
                continue
            print(f"по {size} мача | {per:<6} | колонки {x['cols']:>5} ({x['per_day']:.2f} на ден) | минават {x['pass']:.1%} ± {x['se']:.1%} (роботът казва {x['claimed']:.1%})"
                  + (f" | връща {x['book_return']:.2f} ± {x['book_se']:.2f} € от 1 € на {x['book_n']} с истински коефициенти" if "book_return" in x else ""))
    OUT.write_text(json.dumps({"sizes": {str(k): v for k, v in res.items()}, "select_end": SELECT_END}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
