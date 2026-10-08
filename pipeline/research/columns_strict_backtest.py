"""
По-СТРОГ избор на мачове за колонките (собственикът, 2026-10-08: „аз ще залагам само на колонки, затова там подбирай и внимавай много“).

Повод: при колонки, където роботът казва „връща над 1 €“, реално се връщат 0.80 € (367 колонки); най-много надценява там, където
роботът е много по-оптимистичен от букмейкъра (напр. роботът 74%, коефициентът 1.79 = ~56%). Въпросът: колонки само от мачове, в които
роботът и букмейкърът са съгласни / с повече шанс / само с истински цени, по-надеждни ли са от сегашните?

ПРОТОКОЛ (записан преди пускането): данни backtest_tips (walk-forward, само с модел), 2023-07-01 до днес; сегашното правило е V0 =
колонки по 3, мачове с „топ шанс“ (bets/sure.py), едната прогноза с шанс ≥ 65%, един мач на първенство, до 4 колонки на ден (bets/columns.py).
Варианти (всеки е V0 + допълнително условие за мача):
  V1 шанс ≥ 70%;
  V2 само мачове с истински коефициент от букмейкър (1/X/2, двоен шанс, над/под 2.5) - иначе цената е на робота и не може да се заложи;
  V3 = V2 + роботът не е повече от 10 пункта над букмейкъра (шанс по робота − 1/коефициент ≤ 0.10);
  V4 = V2 + не е повече от 5 пункта над букмейкъра;
  V5 само първото място по шанс в първенството за деня (най-вероятният мач на първенството).
Мери се в избора (до 2025-07-01) и в чистата проверка (от там): колонки на ден (колко от дните имат поне една), колко минават (± грешка)
срещу казаното от робота, връщане от 1 € само за колонки с истински коефициент във всеки мач (иначе е кръгова сметка).
РЕШЕНИЕ: вариант заменя V0 само ако и в избора, и в чистата проверка колонките му минават повече от V0 с над 2 грешки на разликата И
има колонка поне в половината от дните, в които V0 има; иначе нищо не се променя и собственикът получава числата.
Резултатът: data/columns_strict.json.
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

OUT = ROOT / "data" / "columns_strict.json"
SELECT_END = "2025-07-01"


def keep_variant(name, c, rank):
    if name == "V0":
        return True
    if name == "V1":
        return c["p"] >= 0.70
    book = c.get("src") == "book"
    if name == "V2":
        return book
    if name == "V3":
        return book and c["p"] - 1 / c["odds"] <= 0.10
    if name == "V4":
        return book and c["p"] - 1 / c["odds"] <= 0.05
    if name == "V5":
        return rank == 1
    raise ValueError(name)


def run(days, name, size=3):
    st = {per: {"days": 0, "days_with": 0, "cols": 0, "pass": 0, "claimed": 0.0, "book_n": 0, "book_ret": 0.0} for per in ("select", "clean")}
    for day, ms in days.items():
        per = "select" if day < SELECT_END else "clean"
        s = st[per]
        s["days"] += 1
        by_id = {m["id"]: m for m in ms}
        cb.mark_sure(ms)
        # място по шанс в първенството за деня (за V5)
        ranks = {}
        for lg in {m["league"] for m in ms}:
            g = sorted((m for m in ms if m["league"] == lg and m.get("one")), key=lambda m: -m["one"]["p"])
            for i, m in enumerate(g, 1):
                ranks[m["id"]] = i
        cands = [c for c in columns.candidates(ms, 0.65, need_sure=True) if keep_variant(name, c, ranks.get(c["id"]))]
        cols = columns.build(cands, size=size, diversify="league")
        if cols:
            s["days_with"] += 1
        for col in cols:
            ok = all(robot.hit_any(c["sel"], by_id[c["id"]]["hg"], by_id[c["id"]]["ag"]) for c in col)
            sm = columns.summary(col)
            s["cols"] += 1
            s["pass"] += ok
            s["claimed"] += sm["claimed"]
            if sm["book"]:
                s["book_n"] += 1
                s["book_ret"] += sm["odds"] if ok else 0.0
    out = {}
    for per, s in st.items():
        n = s["cols"]
        if not n:
            out[per] = {"cols": 0}
            continue
        p = s["pass"] / n
        out[per] = {"cols": n, "per_day": n / s["days"], "days_with": s["days_with"] / s["days"], "pass": p,
                    "se": math.sqrt(p * (1 - p) / n), "claimed": s["claimed"] / n,
                    "book_n": s["book_n"], "book_return": (s["book_ret"] / s["book_n"]) if s["book_n"] else None}
    return out


def main():
    days = cb.load()
    names = ("V0", "V1", "V2", "V3", "V4", "V5")
    res = {n: run(days, n) for n in names}
    for n in names:
        for per in ("select", "clean"):
            x = res[n][per]
            if not x["cols"]:
                print(f"{n} {per}: няма колонки")
                continue
            print(f"{n} {per:<6} колонки {x['cols']:>5} ({x['per_day']:.2f} на ден, ден с колонка {x['days_with']:.0%}) | минават {x['pass']:.1%} ± {x['se']:.1%} "
                  f"(роботът казва {x['claimed']:.1%})"
                  + (f" | връща {x['book_return']:.2f} € от 1 € на {x['book_n']} с истински коеф." if x["book_return"] is not None else ""))
    verdict = {}
    for n in names[1:]:
        ok = True
        for per in ("select", "clean"):
            a, b = res[n][per], res["V0"][per]
            if not a["cols"] or not b["cols"]:
                ok = False
                continue
            diff, se = a["pass"] - b["pass"], math.hypot(a["se"], b["se"])
            ok &= diff > 2 * se and a["days_with"] >= 0.5 * b["days_with"]
        verdict[n] = ok
    print("\nВЕРДИКТ по протокола (заменя V0 само ако е по-добър с над 2 грешки и в двата периода):", verdict)
    OUT.write_text(json.dumps({"results": res, "verdict": verdict, "select_end": SELECT_END}, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    main()
