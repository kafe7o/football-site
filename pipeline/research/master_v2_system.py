"""
СИСТЕМАТА: вариант 2 + правилата на майстора - всички вариации на колонки, и парите по месеци (собственикът, 2026-10-11: „имаш правилата на майстора; пробвай всички вариации на
метод номер 2; разрешавам прагът да падне от 1.40 на 1.30; виж къде моделът работи най-добре; колонки от 2 до 5; всеки ден или петък-неделя; по колко колонки; 300 € в началото
на месеца, по 30 € на колонка, цел - поне 100-150 € печалба на месец“).

Данни: research/variations_backtest.py (мачовете с пазар над/под 2.5 - 22-те лиги на football-data, 2023-07..днес; истински средни цени на букмейкърите за 1/X/2, двоен шанс,
над/под 2.5; над/под 1.5 / 3.5 и двата вкарват - оценка от пазара с 5% марж, проверена на истински цени).
Шанс: ВАРИАНТ 2 = шансът на робота, поправен ден по ден от собствените му резултати (research/master_rules_backtest.calibrated; само от предишните дни).
Правилата на майстора, всички наведнъж: най-вероятното събитие; истинска цена >= праг; без „тото“ (N1, AUT: фаворит 1.30-1.55); без дерби; топ шанс (третината на първенството за деня);
шанс >= 65%; един мач на първенство в колонка; ден за ден. ДОБАВКА по разрешение: праг 1.30 освен 1.40.

ПРОТОКОЛ (записан преди пускането):
Решетка: праг {1.40, 1.30} x размер {2, 3, 4, 5} x дни {всички, петък-неделя} x колонки на ден {1, 2, 4, 8} x първенства {всички; най-добрите} x съгласие робот-пазар {не, да}
= 256 варианта. „Най-добрите първенства“ = половината първенства с най-добро връщане на мач в ПЕРИОДА НА ИЗБОРА (до 2025-07-01, поне 100 мача) - изборът е от миналото, прилага се
и в чистата проверка (честно). Колонките на деня: най-вероятните първи, всеки мач в най-много една колонка (без повторения).
Мери се: колко минават, коефициент, връщане от 1 € (избор / чиста); пари: всеки месец започва с 300 €, по 30 € на колонка, спира щом банката е под залога - средно на месец,
медиана, най-лош, най-добър, месеци с +100 € и повече, месеци на минус; и същото без лимит на банката (чистият резултат от залозите).
Избор: най-добрите по връщане в избора (поне 150 колонки в избора); чистата проверка е истинската оценка - 256 варианта означава, че най-добрият в избора е оптимистичен.
Отделно: къде моделът работи най-добре (по първенство и по вид събитие - мачове, не колонки); и има ли ден с повече от една колонка от едни и същи мачове (повторения) - не, по построение.
Нищо в правилата на сайта не се сменя от този скрипт. Резултатът: data/master_v2_system.json.
"""

import json
import math
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import robot                                            # noqa: E402
from research import goal_lines_backtest as gl                    # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "master_v2_system.json"
SELECT_END = vb.SELECT_END
BANK, STAKE = 300.0, 30.0
FLOORS = (1.40, 1.30)
SIZES = (2, 3, 4, 5)
PER_DAY = (1, 2, 4, 8)
MIN_COLS = 150


def top_picks(rows, picks):
    """Топ шанс (третината на първенството за деня) и шанс >= 65% -> {ден: [номера на мачове, най-вероятните първи]}."""
    by_group = defaultdict(list)
    for i, (e, s, r, h) in picks.items():
        by_group[(rows[i]["league"], rows[i]["day"])].append((i, s))
    keep = set()
    for items in by_group.values():
        keep |= vb.top_third(items)
    by_day = defaultdict(list)
    for i in keep:
        if picks[i][1] >= 0.65:
            by_day[rows[i]["day"]].append(i)
    return {d: sorted(ids, key=lambda i: -picks[i][1]) for d, ids in by_day.items()}


def leg_stats(rows, picks, by_day, key):
    """По първенство / по вид събитие: мачовете (не колонките), избор и чиста."""
    acc = defaultdict(lambda: {"select": [], "clean": []})
    for day, ids in by_day.items():
        per = "select" if day < SELECT_END else "clean"
        for i in ids:
            e, s, r, h = picks[i]
            acc[rows[i]["league"] if key == "league" else robot.label(e)][per].append((h, r))
    return {k: {per: gl.stat(v) for per, v in d.items()} for k, d in acc.items()}


def best_leagues(league_stats):
    """Половината първенства с най-добро връщане на мач в ИЗБОРА (поне 100 мача)."""
    ok = [(lg, d["select"]["return"]) for lg, d in league_stats.items() if d["select"].get("n", 0) >= 100]
    ok.sort(key=lambda x: -x[1])
    return {lg for lg, _ in ok[: max(1, len(ok) // 2)]}


def columns_for(rows, picks, by_day, size, weekend, per_day, leagues):
    acc = {"select": [], "clean": []}
    months = defaultdict(list)
    for day, ids in sorted(by_day.items()):
        if weekend and rows[ids[0]]["wd"] < 4:
            continue
        ids = [i for i in ids if leagues is None or rows[i]["league"] in leagues]
        used = set()
        for _ in range(per_day):
            col, lgs = [], set()
            for i in ids:
                if i in used or rows[i]["league"] in lgs or len(col) == size:
                    continue
                col.append(i)
                lgs.add(rows[i]["league"])
            if len(col) < size:
                break
            used.update(col)
            item = (all(picks[i][3] for i in col), math.prod(picks[i][2] for i in col))
            acc["select" if day < SELECT_END else "clean"].append(item)
            months[day[:7]].append(item)
    return {k: gl.stat(v) for k, v in acc.items()}, months


def money(months, bank=BANK, stake=STAKE):
    ends, nets = {}, {}
    for m, cols in sorted(months.items()):
        b, net = bank, 0.0
        for ok, odds in cols:
            pnl = stake * (odds - 1) if ok else -stake
            net += pnl
            if b >= stake:
                b += pnl
        ends[m] = round(b - bank, 2)
        nets[m] = round(net, 2)
    v = sorted(ends.values())
    if not v:
        return None
    return {"months": len(v), "avg": sum(v) / len(v), "median": v[len(v) // 2], "worst": v[0], "best": v[-1],
            "plus100": sum(x >= 100 for x in v), "plus150": sum(x >= 150 for x in v), "minus": sum(x < 0 for x in v),
            "avg_no_limit": sum(nets.values()) / len(nets), "by_month": ends}


def main():
    rows = vb.build()
    print("мачове:", len(rows))
    res = {"legs": {}, "variants": []}
    pick_sets = {}
    for floor, agree in product(FLOORS, (False, True)):
        picks = mr.master_picks(rows, "вариант 2", agree, False, floor=floor)
        by_day = top_picks(rows, picks)
        pick_sets[(floor, agree)] = (picks, by_day)
    # къде работи най-добре - мачовете по първенство и по вид (праг 1.30, без съгласие)
    picks, by_day = pick_sets[(1.30, False)]
    lg_stats = leg_stats(rows, picks, by_day, "league")
    kind_stats = leg_stats(rows, picks, by_day, "kind")
    res["legs"] = {"by_league": lg_stats, "by_kind": kind_stats}
    print("\nМАЧОВЕТЕ ПО ПЪРВЕНСТВО (вариант 2, правилата, праг 1.30; избор | чиста):")
    for lg, d in sorted(lg_stats.items(), key=lambda kv: -kv[1]["select"].get("return", 0)):
        s, c = d["select"], d["clean"]
        if s.get("n", 0) >= 100:
            print(f"  {lg:5} избор {s['n']:>4} {s['hit']:.0%} връща {s['return']:.3f} | чиста {c.get('n', 0):>4} {c.get('hit', 0):.0%} връща {c.get('return', 0):.3f}")
    print("\nМАЧОВЕТЕ ПО ВИД СЪБИТИЕ:")
    for k, d in sorted(kind_stats.items(), key=lambda kv: -kv[1]["select"].get("n", 0)):
        s, c = d["select"], d["clean"]
        if s.get("n", 0) >= 50:
            print(f"  {k:22} избор {s['n']:>4} {s['hit']:.0%} цена {s['price']:.2f} връща {s['return']:.3f} | чиста {c.get('n', 0):>4} {c.get('hit', 0):.0%} връща {c.get('return', 0):.3f}")
    best_lg = {}
    for floor, agree in product(FLOORS, (False, True)):
        picks, by_day = pick_sets[(floor, agree)]
        best_lg[(floor, agree)] = best_leagues(leg_stats(rows, picks, by_day, "league"))
    print("\nнай-добрите първенства (от избора, праг 1.30):", sorted(best_lg[(1.30, False)]))
    for floor, agree, size, weekend, per_day, only_best in product(FLOORS, (False, True), SIZES, (False, True), PER_DAY, (False, True)):
        picks, by_day = pick_sets[(floor, agree)]
        st, months = columns_for(rows, picks, by_day, size, weekend, per_day, best_lg[(floor, agree)] if only_best else None)
        res["variants"].append({"floor": floor, "agree": agree, "size": size, "weekend": weekend, "per_day": per_day, "best_leagues": only_best,
                                "stats": st, "money": money(months)})
    name = lambda v: (f"праг {v['floor']:.2f}, по {v['size']}, {'пет-нед' if v['weekend'] else 'всеки ден'}, до {v['per_day']} на ден"
                      + (", най-добрите първенства" if v["best_leagues"] else "") + (", съгласни" if v["agree"] else ""))
    ok = [v for v in res["variants"] if v["stats"]["select"].get("n", 0) >= MIN_COLS and v["money"]]
    print(f"\nВАРИАНТИ С ПОНЕ {MIN_COLS} КОЛОНКИ В ИЗБОРА: {len(ok)} от {len(res['variants'])}")
    print("\nНАЙ-ДОБРИ ПО ВРЪЩАНЕ В ИЗБОРА (чистата е истинската оценка):")
    for v in sorted(ok, key=lambda v: -v["stats"]["select"]["return"])[:12]:
        s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
        print(f"  {name(v):70} | избор {s['n']:>4} {s['hit']:.0%} коеф {s['price']:.2f} връща {s['return']:.3f} | чиста {c.get('n', 0):>4} {c.get('hit', 0):.0%} връща {c.get('return', 0):.3f}"
              f" | 300 €/30 €: средно {m['avg']:+.0f}/мес (медиана {m['median']:+.0f}), +100 в {m['plus100']}/{m['months']}, минус в {m['minus']}/{m['months']}")
    print("\nНАЙ-ДОБРИ ПО ПАРИ НА МЕСЕЦ (300 €/30 €) - и в избора, и в чистата над 0.90:")
    both = [v for v in ok if v["stats"]["clean"].get("n", 0) >= 60 and v["stats"]["clean"]["return"] >= 0.9 and v["stats"]["select"]["return"] >= 0.9]
    for v in sorted(both, key=lambda v: -v["money"]["avg"])[:12]:
        s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
        print(f"  {name(v):70} | избор {s['hit']:.0%} връща {s['return']:.3f} | чиста {c['hit']:.0%} връща {c['return']:.3f}"
              f" | средно {m['avg']:+.0f}/мес, +100 в {m['plus100']}/{m['months']}, +150 в {m['plus150']}, минус в {m['minus']}, най-лош {m['worst']:+.0f}, най-добър {m['best']:+.0f}")
    for v in res["variants"]:
        v["money"] = {k: x for k, x in (v["money"] or {}).items() if k != "by_month"}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
