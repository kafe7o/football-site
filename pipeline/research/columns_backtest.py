"""
Колонките назад във времето (собственикът, 2026-10-04: таб „Колонки“ по логиката на майстора).

Какво се мери: ако всеки ден правим колонки по правилото на bets/columns.py (прогнозата на робота за мача, шанс
поне MIN_P, един мач на първенство и на вид пазар, без дерби), колко често колонките минават, колко е
казвал роботът и колко от 1 € се връща.

ПРОТОКОЛ (записан преди пускането): данни backtest_tips (walk-forward, само с модел), 2023-07-01 до днес; по ден
на мача; едната прогноза - robot.one_pick без картони и корнери (за тях назад няма шансове); средните
коефициенти, а където няма - честният 1/шанс. ИЗБОР до 2025-07-01, ЧИСТА от там. Варианти: MIN_P 0.60 / 0.65 /
0.70; размер 2 / 3 / 4; с разнообразие и без (един мач на първенство и вид пазар). Мери се: колонки, колко
минават (срещу казаното от робота), дни, в които НИТО ЕДНА колонка не минава (сред дните с поне 2 колонки),
връщане от 1 € само за колонки, в които всеки мач има истински коефициент (иначе е кръгова сметка).
ДОПЪЛНЕНИЕ 2026-10-08 (най-сигурните мачове, bets/sure.py): колонките се правят само от мачовете, белязани като най-сигурни
(третината с най-голям шанс на първенство за деня, поне 65%); мери се същото с добавка „_sure“. Старите варианти остават
без това ограничение (need_sure=False), за да са сравними с предишните числа.
ДОПЪЛНЕНИЕ 2026-10-08 вечерта (собственикът: „всичко както майсторът е казал“): колонките по БЛОКОВЕ на майстора (04.10) - във
вторник за вторник-четвъртък, в петък за петък-понеделник; мачовете на блока заедно, до 4 колонки на ден от блока, един мач на
първенство в колонка, всеки мач в една колонка, само с топ шанс (по дни). Мери се същото с добавка „_window“; „days“ тук = блокове.
Параметрите по подразбиране на сайта: MIN_P 0.65, размер 3, с разнообразие. Променят се само ако избраните
варианти са по-добри и в избора, и в чистата проверка по „дни без нито една минала колонка“ и броя колонки на
ден, а минаването не е с повече от 2 грешки под казаното. Резултатът: data/columns_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import columns, db, robot, sure                 # noqa: E402

OUT = ROOT / "data" / "columns_backtest.json"
SELECT_END = "2025-07-01"


def load():
    conn = db.init()
    days = defaultdict(list)
    for r in conn.execute("SELECT league, date, home, away, probs_json, prices_json, flags_json, hg, ag FROM backtest_tips "
                          "WHERE basis = 'model' AND date >= '2023-07-01'"):
        p = json.loads(r["probs_json"])["robot"]
        if p.get("xg_home") is None:
            continue
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {}) if r["prices_json"] else {}
        one = robot.one_pick(p, avg, None, r["league"])
        if not one:
            continue
        flags = json.loads(r["flags_json"] or "{}")
        days[r["date"]].append({"id": f"{r['league']}:{r['date']}:{r['home']}", "league": r["league"], "kickoff": r["date"],
                                "home": r["home"], "away": r["away"], "basis": "model", "one": one,
                                "flags": {"derby": flags.get("derby")}, "hg": r["hg"], "ag": r["ag"]})
    return days


def mark_sure(ms):
    """Белегът „най-сигурен“ върху мачовете на един ден - както на живо (bets/sure.py)."""
    groups = {}
    for m in ms:
        if sure.candidate(m["flags"], m["basis"], m["one"]):
            groups.setdefault(m["league"], []).append(m)
    for g in groups.values():
        info = sure.info_for([(m["id"], m["one"]["p"]) for m in g])
        for m in g:
            m["flags"]["sure"] = info[m["id"]]


def evaluate_windows(days, min_p=0.65, size=3):
    """Колонките по блоковете на майстора (bets/columns.window_of): мачовете на всички дни от блока заедно."""
    blocks = defaultdict(list)
    for day, ms in days.items():
        mark_sure(ms)                                   # топ шанс - по ден, както на живо
        blocks[columns.window_of(day)[0]].append((day, ms))
    stats = {per: {"n": 0, "pass": 0, "claimed": 0.0, "honest": 0.0, "days": 0, "days_none": 0, "book_n": 0, "book_ret": 0.0}
             for per in ("select", "clean")}
    for start, parts in blocks.items():
        per = "select" if start < SELECT_END else "clean"
        ms = [m for _, part in parts for m in part]
        by_id = {m["id"]: m for m in ms}
        n_days = len(columns.window_of(start)[1])
        cols = columns.build(columns.candidates(ms, min_p, need_sure=True), size=size, max_columns=columns.MAX_COLUMNS * n_days,
                             diversify="league")
        if not cols:
            continue
        s = stats[per]
        s["days"] += 1
        passed = 0
        for col in cols:
            ok = all(robot.hit_any(c["sel"], by_id[c["id"]]["hg"], by_id[c["id"]]["ag"]) for c in col)
            sm = columns.summary(col)
            s["n"] += 1
            s["pass"] += ok
            s["claimed"] += sm["claimed"]
            s["honest"] += sm["honest"]
            passed += ok
            if sm["book"]:
                s["book_n"] += 1
                s["book_ret"] += sm["odds"] if ok else 0.0
        s["days_none"] += passed == 0
    out = {}
    for per, s in stats.items():
        n = s["n"]
        p = s["pass"] / n if n else 0
        out[per] = {"n": n, "pass": p, "se": math.sqrt(p * (1 - p) / n) if n else None, "claimed": s["claimed"] / n if n else None,
                    "honest": s["honest"] / n if n else None, "days": s["days"], "days_none": s["days_none"] / s["days"] if s["days"] else None,
                    "per_block": n / s["days"] if s["days"] else None, "book_n": s["book_n"],
                    "book_return": (s["book_ret"] / s["book_n"]) if s["book_n"] else None}
    return out


def evaluate(days, min_p, size, diversify, with_sure=False):
    stats = {per: {"n": 0, "pass": 0, "claimed": 0.0, "honest": 0.0, "days": 0, "days_none": 0, "book_n": 0, "book_ret": 0.0}
             for per in ("select", "clean")}
    for day, ms in days.items():
        per = "select" if day < SELECT_END else "clean"
        by_id = {m["id"]: m for m in ms}
        if with_sure:
            mark_sure(ms)
        cols = columns.build(columns.candidates(ms, min_p, need_sure=with_sure), size=size, diversify=diversify)
        if len(cols) < 2:
            continue
        s = stats[per]
        s["days"] += 1
        passed = 0
        for col in cols:
            ok = all(robot.hit_any(c["sel"], by_id[c["id"]]["hg"], by_id[c["id"]]["ag"]) for c in col)
            sm = columns.summary(col)
            s["n"] += 1
            s["pass"] += ok
            s["claimed"] += sm["claimed"]
            s["honest"] += sm["honest"]
            passed += ok
            if sm["book"]:
                s["book_n"] += 1
                s["book_ret"] += sm["odds"] if ok else 0.0
        s["days_none"] += passed == 0
    out = {}
    for per, s in stats.items():
        n = s["n"]
        if not n:
            out[per] = {"n": 0}
            continue
        p = s["pass"] / n
        out[per] = {"n": n, "pass": p, "se": math.sqrt(p * (1 - p) / n), "claimed": s["claimed"] / n, "honest": s["honest"] / n,
                    "days": s["days"], "days_none": s["days_none"] / s["days"],
                    "book_n": s["book_n"], "book_return": (s["book_ret"] / s["book_n"]) if s["book_n"] else None}
    return out


def main():
    days = load()
    print(f"дни с мачове: {len(days)}")
    res = {}
    # ДОПЪЛНЕНИЕ СЛЕД ПЪРВОТО ПУСКАНЕ (04.10): пълното разнообразие дава твърде малко колонки на ден (417 срещу 1757),
    # затова се добавят и по-леки варианти: „league“ - един мач на първенство; „family2“ - и най-много 2 от вид пазар.
    for min_p in (0.60, 0.65):
        for size in (2, 3):
            for div in (True, "league", "family2", False):
                key = f"p{min_p:.2f}_n{size}_{div if isinstance(div, str) else ('full' if div else 'free')}"
                res[key] = evaluate(days, min_p, size, div)
                for per in ("select", "clean"):
                    x = res[key][per]
                    if x["n"]:
                        print(f"{key:<18} {per:<6} колонки {x['n']:>5} в {x['days']:>4} дни | минават {x['pass']:.1%} ± {x['se']:.1%} "
                              f"(роботът казва {x['claimed']:.1%}, поправено {x['honest']:.1%}) | дни без нито една {x['days_none']:.0%}"
                              + (f" | връща {x['book_return']:.2f} € от 1 € (на {x['book_n']} с истински коефициенти)" if x["book_return"] is not None else ""))
    for size in (2, 3):                      # най-сигурните мачове: само един вариант на разнообразие - като на живо
        key = f"p0.65_n{size}_league_sure"
        res[key] = evaluate(days, 0.65, size, "league", with_sure=True)
        for per in ("select", "clean"):
            x = res[key][per]
            if x["n"]:
                print(f"{key:<22} {per:<6} колонки {x['n']:>5} в {x['days']:>4} дни | минават {x['pass']:.1%} ± {x['se']:.1%} "
                      f"(роботът казва {x['claimed']:.1%}, поправено {x['honest']:.1%}) | дни без нито една {x['days_none']:.0%}"
                      + (f" | връща {x['book_return']:.2f} € от 1 € (на {x['book_n']} с истински коефициенти)" if x["book_return"] is not None else ""))
    res["p0.65_n3_league_sure_window"] = evaluate_windows(days)
    for per in ("select", "clean"):
        x = res["p0.65_n3_league_sure_window"][per]
        print(f"БЛОКОВЕ {per:<6} колонки {x['n']:>5} в {x['days']:>4} блока ({x['per_block']:.1f} на блок) | минават {x['pass']:.1%} ± {x['se']:.1%} "
              f"(роботът казва {x['claimed']:.1%}) | блокове без нито една {x['days_none']:.0%}"
              + (f" | връща {x['book_return']:.2f} € от 1 € (на {x['book_n']} с истински коефициенти)" if x["book_return"] is not None else ""))
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    main()
