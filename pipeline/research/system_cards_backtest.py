"""
Системата + картони (майсторът, 2026-10-12: „картони и всичко; една-две колонки, но максимално сигурни“) - проверка назад преди нещо да влезе на сайта.

Основа: системата (research/master_v2_system.py: вариант 2 + правилата на майстора, по 2 мача, най-много 1 колонка на ден, шанс на колонката >= 50%, истинска цена от 1.40,
най-добрите първенства, чет-нед). Добавка: мачове с КАРТОНИ като кандидат за колонка - изборът „над линия-1“ / „под линия+1“ от research/cards_columns_backtest.py
(data/extras_rows.json; шансът на картоните е калибриран: казано 73%, излиза 73%), със съдия където има.
ПРОТОКОЛ (записан преди пускането): кандидатите на деня = мачовете на системата (голове/знаци, истинска цена) + картоните с шанс >= праг; подредба по шанс, един мач на първенство,
един мач = най-много един избор (ако мачът има и голове, и картони - по-вероятният); колонка по 2 само при произведение >= 50%.
Варианти: състав {само голове (сегашната система); голове + картони; само картони} x праг за картоните {65, 70, 75%} x първенства {най-добрите 11; всички 22 с картони}
x дни {чет-нед, всеки ден} x ДОПУСНАТ коефициент за картони {1.30, 1.40} (odds API няма картони - допускане, казва се). Мери се: колонки на месец, минават (± грешка),
връщане от 1 € (избор / чиста), пари при 300 €/30 €. Описателно; истинските цени за картони трябва да дойдат от букмейкъра на собственика. Резултатът: data/system_cards_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import fewer_columns_backtest as fc                 # noqa: E402
from research import goal_lines_backtest as gl                    # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import master_v2_system as ms                       # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "system_cards_backtest.json"
SELECT_END = vb.SELECT_END
BANK, STAKE = 300.0, 30.0


def cards_candidates(thr):
    """{(първенство, ден): [(ключ, шанс, излязло, домакин, гост)]} - картоните с шанс >= thr на съседната линия."""
    rows = json.loads((ROOT / "data" / "extras_rows.json").read_text(encoding="utf-8"))
    out = defaultdict(list)
    for r in rows:
        if r["kind"] != "cards" or not r.get("lines"):
            continue
        line = r["line"]
        lo, hi = r["lines"].get(f"{line - 1:.1f}"), r["lines"].get(f"{line + 1:.1f}")
        cands = []
        if lo is not None:
            cands.append(("O", line - 1, lo))
        if hi is not None:
            cands.append(("U", line + 1, 1 - hi))
        if not cands:
            continue
        side, ln, p = max(cands, key=lambda c: c[2])
        if p < thr:
            continue
        hit = (r["total"] > ln) if side == "O" else (r["total"] < ln)
        out[(r["league"], r["date"])].append((f"C{side}{ln}:{r['home']}", p, bool(hit), r["home"], r["away"]))
    return out


def run(rows, picks, by_day, cards, leagues, days, mix, price):
    acc = {"select": [], "clean": []}
    months = defaultdict(list)
    all_days = set(by_day) | {d for _, d in cards}
    for day in sorted(all_days):
        if fc.DAYS[days] and __import__("datetime").date.fromisoformat(day).weekday() not in fc.DAYS[days]:
            continue
        pool = []                                   # (шанс, цена, излязло, първенство, мач)
        if mix != "само картони":
            for i in by_day.get(day, []):
                if rows[i]["league"] in leagues:
                    e, s, r, h = picks[i]
                    pool.append((s, r, h, rows[i]["league"], (rows[i]["home"], rows[i]["away"])))
        if mix != "само голове":
            for (lg, d), xs in ((k, v) for k, v in cards.items() if k[1] == day):
                if lg in leagues:
                    for key, p, hit, home, away in xs:
                        pool.append((p, price, hit, lg, (home, away)))
        pool.sort(key=lambda x: -x[0])
        col, lgs, used = [], set(), set()
        for p, pr, hit, lg, match in pool:
            if lg in lgs or match in used:
                continue
            col.append((p, pr, hit))
            lgs.add(lg)
            used.add(match)
            if len(col) == 2:
                break
        if len(col) < 2 or col[0][0] * col[1][0] < 0.5:
            continue
        item = (col[0][2] and col[1][2], col[0][1] * col[1][1])
        acc["select" if day < SELECT_END else "clean"].append(item)
        months[day[:7]].append(item)
    return {k: gl.stat(v) for k, v in acc.items()}, months


def main():
    rows = vb.build()
    picks = mr.master_picks(rows, "вариант 2", False, False, floor=1.40)
    by_day = ms.top_picks(rows, picks, 0.65)
    best = ms.best_leagues(ms.leg_stats(rows, picks, by_day, "league"))
    all22 = {r["league"] for r in rows}
    res = []
    for thr in (0.65, 0.70, 0.75):
        cards = cards_candidates(thr)
        for mix, (lname, leagues), days, price in product(("само голове", "голове + картони", "само картони"), (("най-добрите 11", best), ("всички 22", all22)), ("чет-нед", "всеки ден"), (1.30, 1.40)):
            if mix == "само голове" and (thr != 0.65 or price != 1.30):
                continue                            # базата не зависи от картоните
            st, months = run(rows, picks, by_day, cards, leagues, days, mix, price)
            m = ms.money(months) or {}
            res.append({"mix": mix, "thr": thr, "leagues": lname, "days": days, "price": price, "stats": st,
                        "money": {k: v for k, v in m.items() if k != "by_month"},
                        "per_month": (st["select"].get("n", 0) + st["clean"].get("n", 0)) / max(1, m.get("months", 1))})
    name = lambda v: f"{v['mix']:16} {v['leagues']:13} {v['days']:9}" + ("" if v["mix"] == "само голове" else f" картони >= {v['thr']:.0%} @{v['price']:.2f}")
    print(f"{'вариант':62} | кол/мес | избор: мин. връща | чиста: мин. връща ±  | 300/30: ср./мес +100 минус")
    for v in sorted(res, key=lambda v: (v["mix"] != "само голове", -(v["stats"]["clean"].get("return") or 0))):
        s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
        if not c.get("n"):
            continue
        print(f"{name(v):62} | {v['per_month']:>5.1f}   | {s.get('hit', 0):>4.0%} {s.get('return', 0):.3f}     | {c['hit']:>4.0%} {c['return']:.3f} ±{c['return_se']:.2f} | {m.get('avg', 0):+5.0f} {m.get('plus100', 0):>3}/{m.get('months', 0)} {m.get('minus', 0):>3}")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
