"""
Колонки със ЗАДЪЛЖИТЕЛНА смес от видове (собственикът, 2026-10-13: „едните да са голове, другите картони, трети 1/X/2 и двоен шанс - всяка комбинация“).
Допълва research/all_variants_backtest.py: там всеки мач дава най-вероятното си събитие и колонката взима най-вероятните мачове, без значение от вида; тук видът на всеки мач е
предварително зададен.

ПРОТОКОЛ (записан ПРЕДИ пускането):
Групи: Г = голове (най-вероятното от над/под 1.5, 2.5, 3.5), З = знаци и двоен шанс (най-вероятното от 1, X, 2, 1X, X2, 12), К = картони (съседната линия; ДОПУСНАТА цена 1.30,
после 1.40 за сравнение). Една група - мачовете с прогноза от нея, със същите правила като в all_variants_backtest (без дерби, без „тото“, истинска цена от прага, шанс от прага).
Състави на колонката: ГК, ГЗ, КЗ, ГКЗ, ГГК, ГКК, ГГЗ. Колонката взима най-вероятния свободен мач от всяка поискана група; един мач - една прогноза; един мач от първенство.
Решетка: шанс {робот, пазар, смес, вариант 2} x цена от {1.30, 1.40} x съгласие {не, да} x праг на мача {65, 70, 75%} x праг на картоните {65, 70, 75%} x топ шанс {да, не}
x плащане {средна, най-добрият букмейкър} x състав (7) x първенства {всички, най-добрата половина, без най-лошите} x колонки на ден {1, 2, 4} x праг на колонката {няма, 0.5^(размер/2)}
x дни {всеки ден, чет-нед, пет-нед, съб-нед}.
ИЗБОРЪТ - САМО по избора (2023-07..2025-06), както в all_variants_backtest: поне 100 колонки в избора и 40 в чистата; критерий - долната граница на печалбата на месец
(връщане - 1 - грешката) x колонки на месец x 30 €. Победители: най-доброто изобщо и по правилата на майстора (цена от 1.40, топ шанс, шанс по робота или вариант 2, до 2 на ден).
ПОТВЪРДЕН - чиста над 1 € с над 2 грешки. Сравнение с най-добрите от all_variants_backtest (1.141 ± 0.077 и 1.097 ± 0.042 в чистата, и двата при най-добрия букмейкър).
Резултатът: data/mixed_kinds_backtest.json.
"""

import json
import os
import pickle
import sys
import time
from datetime import date
from itertools import product
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import all_variants_backtest as av                  # noqa: E402
from research import goal_lines_backtest as gl                    # noqa: E402
from research import master_v2_system as ms                       # noqa: E402

OUT = ROOT / "data" / "mixed_kinds_backtest.json"
COMPS = ("ГК", "ГЗ", "КЗ", "ГКЗ", "ГГК", "ГКК", "ГГЗ")
G = av.G


def base_of(src, ev, floor, agree):
    return [j for j, B in enumerate(G["bases"]) if B["src"] == src and B["ev"] == ev and B["floor"] == floor and B["agree"] == agree][0]


def groups(task):
    """-> {ден: {група: [(шанс, цена, излязло, първенство, мач)]}}, най-добрата половина, най-лошите."""
    src, floor, agree, lt, ct, top3, book = task
    parts = {}
    for name, ev in (("Г", "голове"), ("З", "знаци + двоен шанс")):
        B = G["bases"][base_of(src, ev, floor, agree)]
        m = B["p"] >= lt
        if top3:
            m &= B["top"]
        rid = B["idx"][m]
        parts[name] = (G["row_day"][rid], G["row_lg"][rid], G["row_key"][rid], B["p"][m], (B["ravg"] if book == "AVG" else B["rmax"])[m], B["hit"][m])
    C = G["cards"]
    m = C["p"] >= ct
    parts["К"] = (C["day"][m], C["lg"][m], C["key"][m], C["p"][m], np.full(int(m.sum()), G["card_price"]), C["hit"][m])
    by_day = {}
    sel_pay = {}
    for name, (day, lg, key, p, pr, hit) in parts.items():
        o = np.lexsort((-p, day))
        for d, l, k, pp_, r, h in zip(day[o].tolist(), lg[o].tolist(), key[o].tolist(), p[o].tolist(), pr[o].tolist(), hit[o].tolist()):
            by_day.setdefault(d, {"Г": [], "З": [], "К": []})[name].append((pp_, r, h, l, k))
            if not G["day_clean"][d]:
                sel_pay.setdefault(l, []).append(h * r)
    stats = sorted(((l, sum(v) / len(v)) for l, v in sel_pay.items() if len(v) >= 100), key=lambda x: -x[1])
    best = {l for l, _ in stats[: max(1, len(stats) // 2)]}
    worst = {l for l, _ in stats[::-1][: min(5, len(stats) // 3)]}
    return by_day, best, worst


def assemble(by_day, comp, leagues_ok):
    cols = []
    for d in sorted(by_day):
        lists = by_day[d]
        used = set()
        for rank in range(4):
            col, lgs, keys = [], set(), set()
            for g in comp:
                leg = next((l for l in lists[g] if l[4] not in used and l[4] not in keys and l[3] not in lgs and leagues_ok(l[3])), None)
                if leg is None:
                    col = None
                    break
                col.append(leg)
                lgs.add(leg[3])
                keys.add(leg[4])
            if col is None:
                break
            used |= keys
            cols.append((d, rank, all(l[2] for l in col), float(np.prod([l[1] for l in col])), float(np.prod([l[0] for l in col])), len(col)))
    return cols


def run_task(args):
    t, task = args
    try:
        by_day, best, worst = groups(task)
        rows, desc = [], []
        for li, lname in enumerate(av.LEAGUES):
            ok = (lambda j: True) if lname == "всички" else (lambda j: j in best) if lname == "най-добрата половина" else (lambda j: j not in worst)
            for ci_, comp in enumerate(COMPS):
                cols = assemble(by_day, comp, ok)
                for di, per_day in enumerate(av.PER_DAY):
                    for ci, ct in enumerate(av.COLTHR):
                        for wi, dname in enumerate(av.DAYS):
                            def mask_fn(a, per_day=per_day, ct=ct, dname=dname):
                                m = (a[:, 1] < per_day) & G["day_in"][dname][a[:, 0].astype(int)]
                                if ct:
                                    m &= a[:, 4] >= 0.5 ** (a[:, 5] / 2)
                                return m
                            rows.append(av.measure(cols, mask_fn))
                            desc.append((t, li, ci_, di, ci, wi))
        return np.array(rows, dtype=np.float32), np.array(desc, dtype=np.int16)
    except Exception:                                           # noqa: BLE001 - отчита се, не се мълчи
        import traceback
        print(f"ГРЕШКА в задача {t} {task}: " + traceback.format_exc(), flush=True)
        return np.zeros((0, len(av.FIELDS)), dtype=np.float32), np.zeros((0, 6), dtype=np.int16)


def detail(task, li, ci_, di, ci, wi, days, card_price=None):
    old = G["card_price"]
    if card_price is not None:
        G["card_price"] = card_price
    by_day, best, worst = groups(task)
    G["card_price"] = old
    lname = av.LEAGUES[li]
    ok = (lambda j: True) if lname == "всички" else (lambda j: j in best) if lname == "най-добрата половина" else (lambda j: j not in worst)
    cols = assemble(by_day, COMPS[ci_], ok)
    acc, months = {"select": [], "clean": []}, {}
    for d, rank, hit, odds, colp, size in cols:
        if rank >= av.PER_DAY[di] or not G["day_in"][list(av.DAYS)[wi]][d] or (av.COLTHR[ci] and colp < 0.5 ** (size / 2)):
            continue
        acc["clean" if G["day_clean"][d] else "select"].append((hit, odds))
        months.setdefault(days[d][:7], []).append((hit, odds))
    return {k: gl.stat(v) for k, v in acc.items()}, ms.money(months)


def name_of(task, li, ci_, di, ci, wi):
    src, floor, agree, lt, ct, top3, book = task
    return (f"състав {COMPS[ci_]}, шанс: {src}, цена от {floor:.2f}{', съгласни' if agree else ''}, мач от {lt:.0%}, картони от {ct:.0%}, {'топ шанс' if top3 else 'без топ шанс'}, "
            f"{'най-добър букмейкър' if book == 'MAX' else 'средна цена'}, първенства: {av.LEAGUES[li]}, до {av.PER_DAY[di]} на ден{', колонка с праг' if av.COLTHR[ci] else ''}, {list(av.DAYS)[wi]}")


def main():
    t0 = time.time()
    shared, days, leagues = pickle.loads(Path(os.environ["ALLVAR_CACHE"]).read_bytes())
    av.init(shared)
    tasks = list(product(av.SRCS, av.FLOORS, av.AGREE, av.LEG_T, av.CARD_T, av.TOP3, av.BOOKS))
    print(f"задачи {len(tasks)}", flush=True)
    res, desc = [], []
    with Pool(4, initializer=av.init, initargs=(shared,)) as pool:
        for j, (r, d) in enumerate(pool.imap_unordered(run_task, list(enumerate(tasks)), chunksize=2)):
            res.append(r)
            desc.append(d)
            if j % 100 == 0:
                print(f"  {j}/{len(tasks)} ({time.time() - t0:.0f} с)", flush=True)
    R = np.concatenate(res).astype(np.float64)
    D = np.concatenate(desc)
    F = {f: R[:, k] for k, f in enumerate(av.FIELDS)}
    el = (F["sel_n"] >= 100) & (F["cl_n"] >= 40)
    lb = (F["sel_ret"] - 1 - F["sel_se"]) * F["sel_cpm"] * 30
    task_arr = lambda k: np.array([t[k] for t in tasks])[D[:, 0]]
    master = (task_arr(1) == 1.40) & task_arr(5).astype(bool) & np.isin(task_arr(0), ("робот", "вариант 2")) & (D[:, 3] <= 1)
    print(f"\nвариантите: {len(R)}; допустими: {int(el.sum())}; чиста над 1 с над 2 грешки: {int((el & (F['cl_ret'] - 2 * F['cl_se'] > 1)).sum())}")
    order = np.argsort(-np.where(el, lb, -np.inf))[:20]
    print(f"20-те най-добри в избора: {F['sel_ret'][order].mean():.3f} -> чиста {F['cl_ret'][order].mean():.3f}")
    print("\nпо състав (средно, допустимите):")
    out = {"generated": date.today().isoformat(), "variants": int(len(R)), "by_comp": {}, "winners": {}}
    for ci_, comp in enumerate(COMPS):
        m = el & (D[:, 2] == ci_)
        if m.any():
            i = int(np.argmax(np.where(m, lb, -np.inf)))
            out["by_comp"][comp] = {"sel_mean": float(F["sel_ret"][m].mean()), "cl_mean": float(F["cl_ret"][m].mean()), "best": {"name": name_of(tasks[D[i, 0]], *[int(x) for x in D[i, 1:]]),
                                    **{f: float(F[f][i]) for f in av.FIELDS}}}
            print(f"  {comp:4}: средно избор {F['sel_ret'][m].mean():.3f}, чиста {F['cl_ret'][m].mean():.3f} | най-добрият в избора: {F['sel_ret'][i]:.3f} -> чиста {F['cl_hit'][i]:.0%} {F['cl_ret'][i]:.3f} ± {F['cl_se'][i]:.2f} ({F['cl_cpm'][i]:.1f}/мес)")
    for gname, gm in (("най-доброто изобщо", el), ("по правилата на майстора", el & master)):
        i = int(np.argmax(np.where(gm, lb, -np.inf)))
        task, d = tasks[D[i, 0]], [int(x) for x in D[i, 1:]]
        st, money = detail(task, *d, days)
        info = {"name": name_of(task, *d), "detail": st, "money": {k: v for k, v in (money or {}).items() if k != "by_month"}, "by_month": (money or {}).get("by_month")}
        avg = detail(task[:6] + ("AVG",), *d, days)[0] if task[6] == "MAX" else None
        c140 = detail(task, *d, days, card_price=1.40)[0] if "К" in COMPS[d[1]] else None
        out["winners"][gname] = {**info, "avg_price": avg, "cards_1.40": c140}
        s, c = st["select"], st["clean"]
        cl = {m: v for m, v in (money or {}).get("by_month", {}).items() if m >= "2025-07"}
        print(f"\n{gname}:\n   {info['name']}\n   избор {s['n']} кол., {s['hit']:.0%}, {s['return']:.3f} ± {s['return_se']:.3f} | чиста {c['n']} кол., {c['hit']:.0%}, {c['return']:.3f} ± {c['return_se']:.3f}"
              + (f" | чиста по месеци 300/30: средно {sum(cl.values()) / len(cl):+.0f}, плюс {sum(v > 0 for v in cl.values())} от {len(cl)}" if cl else ""))
        if avg:
            print(f"   при средна цена: чиста {avg['clean']['return']:.3f} ± {avg['clean']['return_se']:.3f}")
        if c140:
            print(f"   картони при 1.40: чиста {c140['clean']['return']:.3f} ± {c140['clean']['return_se']:.3f}")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"\nзаписано: {OUT} ({time.time() - t0:.0f} с)")


if __name__ == "__main__":
    main()
