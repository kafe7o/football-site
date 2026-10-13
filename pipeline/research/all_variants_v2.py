"""
ВСИЧКИ ВАРИАНТИ, ВТОРО ТЪРСЕНЕ - „под дърво и камък“ (собственикът, 2026-10-13: „да потърсиш дали има по-добър вариант от най-печелившия; да пробваш всеки ама всеки
вариант, и тия най-сигурните независимо какви са; и да е възможно най-печелившото нещо“; „има ли вариант, в който се вади най-доброто и при 65, и при 70 процента“).

Разширява research/all_variants_backtest.py (1.66 млн. варианта, 13.10), където минималният коефициент беше винаги 1.30 или 1.40 и шансът до 75%.
ПРОТОКОЛ (записан ПРЕДИ пускането):
МАЧЪТ: шанс по {робот, пазар, смес, вариант 2}; вид {всички 14, голове, знаци + двоен шанс, всички 14 + картони, само картони}; минимален коефициент (истински, средна цена)
  {без минимум, 1.10, 1.20, 1.30, 1.40} - „най-сигурните, независимо какви са“ са вариантите без минимум и с висок праг на шанса; съгласие робот-пазар {не, да};
  праг на шанса {65, 70, 75, 80, 85%}; картони {65, 75%} (ДОПУСНАТА цена 1.30, после 1.40 за сравнение); топ шанс {да, не}; плаща се {средна, най-добрият букмейкър}.
  Винаги: без дерби, без „тото“, ден за ден, една прогноза на мач. Корнери - не (без умение); състави - не (не минаха 13.10).
КОЛОНКАТА: първенства {всички, най-добрата половина, без най-лошите - по избора}; подредба {шанс, шанс x цена}; от първенство {1, 2}; форма {1, 2, 3, 4, 5, 6 мача - отделни колонки;
  системи 2 от 3, 2 от 4, 3 от 4, 3 от 5, 4 от 5, 4 от 6, 5 от 6}; колонки на ден {1, 2, 4}; праг на колонката {няма, 0.5^(размер/2)}; дни {всеки ден, чет-нед, пет-нед, съб-нед}.
СМЕСЕНИ СЪСТАВИ (видът на всеки мач е зададен): ГК, ГЗ, КЗ, ГКЗ, ГГК, ГКК, ГГЗ (Г голове, З знаци и двоен шанс, К картони) - със същите минимуми, прагове, първенства, дни.
ИЗБОРЪТ - ПО-СТРОГ от първото търсене: първите 2 години се делят на две години (2023/24 и 2024/25) и вариантът трябва да е добър И В ДВЕТЕ (правилото на проекта: една посока
  в два периода). Условие: поне 50 колонки във всяка от двете години и 40 в чистата (2025-07..). Критерий: ПО-МАЛКАТА от двете долни граници на печалбата на месец
  (връщане - 1 - грешката) x колонки на месец x 30 €. Чистата проверка не участва в избора.
ПОБЕДИТЕЛИ: (А) най-доброто изобщо; (Б) по правилата на майстора (минимум 1.40, топ шанс, шанс по робота или вариант 2, подредба по шанс, един от първенство, до 2 колонки на ден,
  без системи, без картони); (В) най-сигурните (минимум до 1.20); (Г) без допускания (без картони); (Д) смесените състави; (Е) най-добро ЕДНОВРЕМЕННО при 65% и 70% - същите
  настройки при двата прага, критерият е по-малкият от двата. ПОТВЪРДЕН е, ако в чистата връща над 1 € с над 2 грешки. Сравнение с най-доброто от първото търсене
  (голове, смес, съгласни, 65%, без топ шанс, 1.30, най-добрата половина, шанс x цена, система 3 от 4, най-добрият букмейкър: чиста 1.141 ± 0.077). Казва се спадът на 20-те най-добри.
Резултатът: data/all_variants_v2.json; пълната таблица (само допустимите) - data/all_variants_v2_full.npz (не отива в облака).
"""

import json
import math
import os
import pickle
import sys
import time
from collections import defaultdict
from datetime import date
from itertools import combinations, product
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import robot                                            # noqa: E402
from research import all_variants_backtest as av                  # noqa: E402
from research import goal_lines_backtest as gl                    # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import master_v2_system as ms                       # noqa: E402
from research import pro_price_backtest as pp                     # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "all_variants_v2.json"
FULL = ROOT / "data" / "all_variants_v2_full.npz"
S1_END, SELECT_END = "2024-07-01", vb.SELECT_END
STAKE = 30.0
CARD_PRICE = 1.30
EV = vb.EV
SRCS, EVSETS = av.SRCS, av.EVSETS
FLOORS = (1.0, 1.10, 1.20, 1.30, 1.40)
AGREE = (False, True)
LEG_T = (0.65, 0.70, 0.75, 0.80, 0.85)
CARD_T = (0.65, 0.75)
TOP3 = (True, False)
BOOKS = ("AVG", "MAX")
LEAGUES, ORDERS, OPL, PER_DAY, COLTHR, DAYS = av.LEAGUES, av.ORDERS, (1, 2), (1, 2, 4), (False, True), av.DAYS
SCHEMES = (1, 2, 3, 4, 5, 6, "2 от 3", "2 от 4", "3 от 4", "3 от 5", "4 от 5", "4 от 6", "5 от 6")
COMBO = {"2 от 3": (3, 2), "2 от 4": (4, 2), "3 от 4": (4, 3), "3 от 5": (5, 3), "4 от 5": (5, 4), "4 от 6": (6, 4), "5 от 6": (6, 5)}
COMPS = ("ГК", "ГЗ", "КЗ", "ГКЗ", "ГГК", "ГКК", "ГГЗ")
FIELDS = ("s1_n", "s1_hit", "s1_ret", "s1_se", "s2_n", "s2_hit", "s2_ret", "s2_se", "cl_n", "cl_hit", "cl_ret", "cl_se")
G = {}


# ---------------------------------------------------------------- подготовка

def prepare():
    t0 = time.time()
    rows = vb.build()
    A = vb.arrays(rows)
    P, Q, R, H = A["P"], A["Q"], A["R"], A["H"]
    PC = mr.calibrated(rows)
    n = len(rows)
    toto = np.zeros((n, len(EV)), dtype=bool)
    for i, row in enumerate(rows):
        if row["league"] in robot.TOTO_LEAGUES:
            for k, e in enumerate(EV):
                toto[i, k] = robot.toto_blocked(row["league"], e, R[i, k])
    cards = av.adjacent_cards()
    days = sorted({r["day"] for r in rows} | {c[1] for c in cards})
    day_id = {d: j for j, d in enumerate(days)}
    leagues = sorted({r["league"] for r in rows} | {c[0] for c in cards})
    lg_id = {lg: j for j, lg in enumerate(leagues)}
    keys = {}
    key_of = lambda lg, d, h: keys.setdefault((lg, d, h), len(keys))
    row_day = np.array([day_id[r["day"]] for r in rows])
    row_lg = np.array([lg_id[r["league"]] for r in rows])
    row_key = np.array([key_of(r["league"], r["day"], r["home"]) for r in rows])
    cd = {"day": np.array([day_id[c[1]] for c in cards]), "lg": np.array([lg_id[c[0]] for c in cards]),
          "key": np.array([key_of(c[0], c[1], c[2]) for c in cards]), "p": np.array([c[3] for c in cards]), "hit": np.array([c[4] for c in cards])}
    prices = pp.load_prices()
    print(f"мачове {n}, картони {len(cards)} ({time.time() - t0:.0f} с)", flush=True)
    bases, bmap = [], {}
    for src, (ename, evs), floor, agree in product(SRCS, EVSETS, FLOORS, AGREE):
        S = P if src == "робот" else Q if src == "пазар" else PC if src == "вариант 2" else (P + Q) / 2
        with np.errstate(invalid="ignore"):
            valid = ~np.isnan(R) & (R > 1.0) & (R >= floor) & ~np.isnan(S)
            if evs is not None:
                valid &= np.array([e in evs for e in EV])[None, :]
            if agree:
                valid &= ~np.isnan(Q) & (np.abs(P - Q) <= 0.05)
        valid &= ~A["derby"][:, None] & ~toto
        Sm = np.where(valid, S, -np.inf)
        best = Sm.argmax(axis=1)
        score = Sm[np.arange(n), best]
        idx = np.nonzero(np.isfinite(score))[0]
        k = best[idx]
        grp = defaultdict(list)
        for i, s in zip(idx.tolist(), score[idx].tolist()):
            grp[(rows[i]["league"], rows[i]["day"])].append((i, s))
        keep = set()
        for items in grp.values():
            keep |= vb.top_third(items)
        ravg = R[idx, k]
        rmax = np.empty(len(idx))
        for j, (i, kk, r) in enumerate(zip(idx.tolist(), k.tolist(), ravg.tolist())):
            row = rows[i]
            m = pp.price_at(row, EV[kk], r, prices.get((row["league"], row["day"], row["home"], row["away"])) or {}, "MAX")
            rmax[j] = m if (m is not None and r <= m <= r * 1.25) else r
        bmap[(src, ename, floor, agree)] = len(bases)
        bases.append({"src": src, "ev": ename, "floor": floor, "agree": agree, "idx": idx, "p": score[idx], "ravg": ravg, "rmax": rmax,
                      "hit": H[idx, k], "top": np.array([i in keep for i in idx.tolist()])})
    last = days[-1]
    clean_months = (int(last[:4]) - 2025) * 12 + int(last[5:7]) - 7 + 1
    day_wd = np.array([date.fromisoformat(d).weekday() for d in days])
    period = np.array([0 if d < S1_END else 1 if d < SELECT_END else 2 for d in days])
    print(f"основи {len(bases)} ({time.time() - t0:.0f} с)", flush=True)
    return {"bases": bases, "bmap": bmap, "cards": cd, "row_day": row_day, "row_lg": row_lg, "row_key": row_key, "card_price": CARD_PRICE,
            "period": period, "day_in": {k: np.isin(day_wd, v) for k, v in DAYS.items()}, "months": (12, 12, clean_months), "n_leagues": len(leagues)}, days, leagues


def tasks_list():
    out = []
    for src, (ename, _), floor, agree in product(SRCS, EVSETS, FLOORS, AGREE):
        for lt, top3, book in product(LEG_T, TOP3, BOOKS):
            out.append(("one", ename, src, floor, agree, lt, None, top3, book))
            if ename == "всички 14":
                for ct in CARD_T:
                    out.append(("one", "всички 14 + картони", src, floor, agree, lt, ct, top3, book))
    for ct in CARD_T:
        out.append(("one", "само картони", None, None, None, None, ct, None, None))
    for src, floor, agree, lt, ct, top3, book in product(SRCS, FLOORS, AGREE, LEG_T, CARD_T, TOP3, BOOKS):
        out.append(("mix", "смесени", src, floor, agree, lt, ct, top3, book))
    return out


# ---------------------------------------------------------------- работникът

def init(shared):
    G.update(shared)


def _legs(src, ev, floor, agree, lt, top3, book):
    B = G["bases"][G["bmap"][(src, ev, floor, agree)]]
    m = B["p"] >= lt
    if top3:
        m &= B["top"]
    rid = B["idx"][m]
    return G["row_day"][rid], G["row_lg"][rid], G["row_key"][rid], B["p"][m], (B["ravg"] if book == "AVG" else B["rmax"])[m], B["hit"][m]


def _cards(ct):
    C = G["cards"]
    m = C["p"] >= ct
    return C["day"][m], C["lg"][m], C["key"][m], C["p"][m], np.full(int(m.sum()), G["card_price"]), C["hit"][m]


def _leagues(day, lg, pr, hit):
    sel = G["period"][day] < 2
    stats = []
    for j in range(G["n_leagues"]):
        mm = sel & (lg == j)
        if int(mm.sum()) >= 100:
            stats.append((j, float((hit[mm] * pr[mm]).mean())))
    stats.sort(key=lambda x: -x[1])
    return {j for j, _ in stats[: max(1, len(stats) // 2)]}, {j for j, _ in stats[::-1][: min(5, len(stats) // 3)]}


def pool_one(task):
    _, kind, src, floor, agree, lt, ct, top3, book = task
    parts = []
    if kind != "само картони":
        parts.append(_legs(src, "всички 14" if kind == "всички 14 + картони" else kind, floor, agree, lt, top3, book))
    if ct is not None:
        parts.append(_cards(ct))
    day, lg, key, p, pr, hit = (np.concatenate(x) for x in zip(*parts))
    if len(day) == 0:
        return [], set(), set()
    o = np.lexsort((-p, key))
    _, first = np.unique(key[o], return_index=True)
    s = o[first]
    day, lg, p, pr, hit = day[s], lg[s], p[s], pr[s], hit[s]
    o = np.lexsort((-p, day))
    day, lg, p, pr, hit = day[o], lg[o], p[o], pr[o], hit[o]
    best, worst = _leagues(day, lg, pr, hit)
    bounds = np.flatnonzero(np.diff(day)) + 1
    return [(int(day[a]), list(zip(p[a:b].tolist(), pr[a:b].tolist(), hit[a:b].tolist(), lg[a:b].tolist())))
            for a, b in zip(np.r_[0, bounds], np.r_[bounds, len(day)])], best, worst


def pool_mix(task):
    _, _, src, floor, agree, lt, ct, top3, book = task
    parts = {"Г": _legs(src, "голове", floor, agree, lt, top3, book), "З": _legs(src, "знаци + двоен шанс", floor, agree, lt, top3, book), "К": _cards(ct)}
    by_day = {}
    allp = [np.concatenate(x) for x in zip(*parts.values())]
    for name, (day, lg, key, p, pr, hit) in parts.items():
        o = np.lexsort((-p, day))
        for d, l, k, q, r, h in zip(day[o].tolist(), lg[o].tolist(), key[o].tolist(), p[o].tolist(), pr[o].tolist(), hit[o].tolist()):
            by_day.setdefault(d, {"Г": [], "З": [], "К": []})[name].append((q, r, h, l, k))
    best, worst = _leagues(allp[0], allp[1], allp[4], allp[5]) if len(allp[0]) else (set(), set())
    return by_day, best, worst


def assemble(daylegs, scheme, opl):
    cols = []
    for d, L in daylegs:
        if isinstance(scheme, int):
            used = [False] * len(L)
            for rank in range(4):
                col, cnt = [], {}
                for j, l in enumerate(L):
                    if used[j] or cnt.get(l[3], 0) >= opl:
                        continue
                    col.append(j)
                    cnt[l[3]] = cnt.get(l[3], 0) + 1
                    if len(col) == scheme:
                        break
                if len(col) < scheme:
                    break
                for j in col:
                    used[j] = True
                legs = [L[j] for j in col]
                cols.append((d, rank, all(l[2] for l in legs), math.prod(l[1] for l in legs), math.prod(l[0] for l in legs), scheme))
        else:
            top_n, k = COMBO[scheme]
            top, cnt = [], {}
            for l in L:
                if cnt.get(l[3], 0) >= opl:
                    continue
                top.append(l)
                cnt[l[3]] = cnt.get(l[3], 0) + 1
                if len(top) == top_n:
                    break
            if len(top) < top_n:
                continue
            for legs in combinations(top, k):
                cols.append((d, 0, all(l[2] for l in legs), math.prod(l[1] for l in legs), math.prod(l[0] for l in legs), k))
    return cols


def assemble_mix(by_day, comp, ok):
    cols = []
    for d in sorted(by_day):
        lists = by_day[d]
        used = set()
        for rank in range(4):
            col, lgs, keys = [], set(), set()
            for g in comp:
                leg = next((l for l in lists[g] if l[4] not in used and l[4] not in keys and l[3] not in lgs and ok(l[3])), None)
                if leg is None:
                    col = None
                    break
                col.append(leg)
                lgs.add(leg[3])
                keys.add(leg[4])
            if col is None:
                break
            used |= keys
            cols.append((d, rank, all(l[2] for l in col), math.prod(l[1] for l in col), math.prod(l[0] for l in col), len(col)))
    return cols


def measure(a, m):
    res = np.zeros(len(FIELDS), dtype=np.float64)
    per = G["period"][a[:, 0].astype(int)]
    pay = a[:, 2] * a[:, 3]
    for k in range(3):
        mm = m & (per == k)
        n = int(mm.sum())
        if n:
            x = pay[mm]
            res[4 * k:4 * k + 4] = (n, a[mm, 2].mean(), x.mean(), x.std(ddof=1) / math.sqrt(n) if n > 1 else 0.0)
    return res


def filters(cols, is_system):
    """-> [(di, ci, wi, мерки)] за всички колонки на ден / праг / дни."""
    out = []
    if not cols:
        return out
    a = np.array(cols, dtype=float)
    dayi = a[:, 0].astype(int)
    thr_ok = a[:, 4] >= 0.5 ** (a[:, 5] / 2)
    for di, per_day in enumerate((99,) if is_system else PER_DAY):
        m0 = a[:, 1] < per_day
        for ci, ct in enumerate(COLTHR):
            m1 = m0 & thr_ok if ct else m0
            for wi, dname in enumerate(DAYS):
                out.append((3 if is_system else di, ci, wi, measure(a, m1 & G["day_in"][dname][dayi])))
    return out


def ok_league(lname, best, worst):
    return (lambda j: True) if lname == "всички" else (lambda j: j in best) if lname == "най-добрата половина" else (lambda j: j not in worst)


def run_task(args):
    t, task = args
    try:
        rows, desc = [], []
        if task[0] == "one":
            daylegs, best, worst = pool_one(task)
            for li, lname in enumerate(LEAGUES):
                ok = ok_league(lname, best, worst)
                dl = daylegs if lname == "всички" else [(d, [l for l in L if ok(l[3])]) for d, L in daylegs]
                for oi, order in enumerate(ORDERS):
                    dlo = dl if order == "шанс" else [(d, sorted(L, key=lambda l: -l[0] * l[1])) for d, L in dl]
                    for pi, opl in enumerate(OPL):
                        for si, scheme in enumerate(SCHEMES):
                            for di, ci, wi, r in filters(assemble(dlo, scheme, opl), not isinstance(scheme, int)):
                                rows.append(r)
                                desc.append((t, li, oi, pi, si, di, ci, wi))
        else:
            by_day, best, worst = pool_mix(task)
            for li, lname in enumerate(LEAGUES):
                ok = ok_league(lname, best, worst)
                for ki, comp in enumerate(COMPS):
                    for di, ci, wi, r in filters(assemble_mix(by_day, comp, ok), False):
                        rows.append(r)
                        desc.append((t, li, 0, 0, 100 + ki, di, ci, wi))
        if not rows:
            return np.zeros((0, len(FIELDS)), dtype=np.float32), np.zeros((0, 8), dtype=np.int16)
        R = np.array(rows)
        keep = (R[:, 0] >= 50) & (R[:, 4] >= 50) & (R[:, 8] >= 40)
        return R[keep].astype(np.float32), np.array(desc, dtype=np.int16)[keep]
    except Exception:                                           # noqa: BLE001 - отчита се, не се мълчи
        import traceback
        print(f"ГРЕШКА в задача {t} {task}: " + traceback.format_exc(), flush=True)
        return np.zeros((0, len(FIELDS)), dtype=np.float32), np.zeros((0, 8), dtype=np.int16)


# ---------------------------------------------------------------- подробно за един вариант

def columns_of(task, li, oi, pi, si, di, ci, wi, card_price=None):
    old = G["card_price"]
    if card_price is not None:
        G["card_price"] = card_price
    try:
        if task[0] == "one":
            daylegs, best, worst = pool_one(task)
            ok = ok_league(LEAGUES[li], best, worst)
            dl = [(d, [l for l in L if ok(l[3])]) for d, L in daylegs]
            if ORDERS[oi] != "шанс":
                dl = [(d, sorted(L, key=lambda l: -l[0] * l[1])) for d, L in dl]
            cols = assemble(dl, SCHEMES[si], OPL[pi])
        else:
            by_day, best, worst = pool_mix(task)
            cols = assemble_mix(by_day, COMPS[si - 100], ok_league(LEAGUES[li], best, worst))
    finally:
        G["card_price"] = old
    per_day = 99 if di == 3 else PER_DAY[di]
    return [c for c in cols if c[1] < per_day and G["day_in"][list(DAYS)[wi]][c[0]] and (not COLTHR[ci] or c[4] >= 0.5 ** (c[5] / 2))]


def detail(task, d, days, card_price=None):
    cols = columns_of(task, *d, card_price=card_price)
    acc, months = {0: [], 1: [], 2: []}, defaultdict(list)
    for c in cols:
        acc[int(G["period"][c[0]])].append((c[2], c[3]))
        months[days[c[0]][:7]].append((c[2], c[3]))
    return {("2023/24", "2024/25", "чиста")[k]: gl.stat(v) for k, v in acc.items()}, ms.money(months)


def name_of(task, d):
    li, oi, pi, si, di, ci, wi = d
    typ, kind, src, floor, agree, lt, ct, top3, book = task
    parts = [kind if typ == "one" else f"смесени {COMPS[si - 100]}"]
    if src is not None:
        parts += [f"шанс: {src}", "без минимум на коефициента" if floor == 1.0 else f"коефициент от {floor:.2f}", "робот и пазар съгласни" if agree else "",
                  f"мач от {lt:.0%}", "топ шанс" if top3 else "без топ шанс", "най-добрият букмейкър" if book == "MAX" else "средна цена"]
    if ct is not None:
        parts.append(f"картони от {ct:.0%}")
    parts += [f"първенства: {LEAGUES[li]}"]
    if typ == "one":
        s = SCHEMES[si]
        parts += [f"подредба: {ORDERS[oi]}", f"от първенство: {OPL[pi]}", (f"по {s}" if s != 1 else "единични") if isinstance(s, int) else f"система {s}"]
    parts += ["" if di == 3 else f"до {PER_DAY[di]} на ден", "колонка с праг" if COLTHR[ci] else "", list(DAYS)[wi]]
    return ", ".join(x for x in parts if x)


# ---------------------------------------------------------------- главното

def main():
    t0 = time.time()
    cache = os.environ.get("ALLVAR2_CACHE")
    if cache and Path(cache).exists():
        shared, days, leagues = pickle.loads(Path(cache).read_bytes())
        print("подготовката - от кеша", flush=True)
    else:
        shared, days, leagues = prepare()
        if cache:
            Path(cache).write_bytes(pickle.dumps((shared, days, leagues)))
    init(shared)
    tasks = tasks_list()
    if "--analyze" in sys.argv:
        z = np.load(FULL)
        analyze(shared, days, tasks, z["R"], z["D"], t0)
        return
    # котвата: системата (вариант 2, 14 събития, 1.40, 65%, топ шанс, средна, най-добрата половина, шанс, един, по 2, 1 на ден, праг, чет-нед) - избор 146 кол. / 1.142, чиста 78 / 1.088
    anchor = detail(("one", "всички 14", "вариант 2", 1.40, False, 0.65, None, True, "AVG"), (1, 0, 0, 1, 0, 1, 1), days)[0]
    a12 = anchor["2023/24"]["n"] + anchor["2024/25"]["n"]
    a_ret = (anchor["2023/24"]["return"] * anchor["2023/24"]["n"] + anchor["2024/25"]["return"] * anchor["2024/25"]["n"]) / a12
    print(f"КОТВА: избор {a12} кол., {a_ret:.3f} | чиста {anchor['чиста']['n']} кол., {anchor['чиста']['return']:.3f}", flush=True)
    print(f"задачи {len(tasks)}", flush=True)
    res, desc = [], []
    with Pool(4, initializer=init, initargs=(shared,)) as pool:
        for j, (r, d) in enumerate(pool.imap_unordered(run_task, list(enumerate(tasks)), chunksize=4)):
            res.append(r)
            desc.append(d)
            if j % 250 == 0:
                print(f"  {j}/{len(tasks)} задачи ({time.time() - t0:.0f} с)", flush=True)
    R = np.concatenate(res)
    D = np.concatenate(desc)
    np.savez_compressed(FULL, R=R, D=D)
    print(f"допустими варианти: {len(R)} ({time.time() - t0:.0f} с)", flush=True)
    analyze(shared, days, tasks, R, D, t0)


def analyze(shared, days, tasks, R, D, t0):
    F = {f: R[:, k].astype(np.float64) for k, f in enumerate(FIELDS)}
    mo = shared["months"]
    lb1 = (F["s1_ret"] - 1 - F["s1_se"]) * F["s1_n"] / mo[0] * STAKE
    lb2 = (F["s2_ret"] - 1 - F["s2_se"]) * F["s2_n"] / mo[1] * STAKE
    crit = np.minimum(lb1, lb2)
    cl_month = (F["cl_ret"] - 1) * F["cl_n"] / mo[2] * STAKE
    confirmed = F["cl_ret"] - 2 * F["cl_se"] > 1
    TA = {k: np.array([t[j] if t[j] is not None else ("" if k in ("src", "book") else -1) for t in tasks], dtype=object)[D[:, 0]]
          for j, k in enumerate(("typ", "kind", "src", "floor", "agree", "lt", "ct", "top3", "book"))}
    typ, kind, src = TA["typ"].astype(str), TA["kind"].astype(str), TA["src"].astype(str)
    floor, lt = TA["floor"].astype(float), TA["lt"].astype(float)
    top3 = TA["top3"] == True                                   # noqa: E712
    has_cards = np.isin(kind, ("всички 14 + картони", "само картони")) | (typ == "mix")
    is_system = (D[:, 4] >= 6) & (D[:, 4] < 100)
    master = (floor == 1.40) & top3 & np.isin(src, ("робот", "вариант 2")) & (D[:, 2] == 0) & (D[:, 3] == 0) & ~is_system & (D[:, 5] <= 1) & ~has_cards
    out = {"generated": date.today().isoformat(), "eligible": int(len(R)), "confirmed_in_clean": int(confirmed.sum()), "winners": {}}
    order = np.argsort(-crit)
    top20 = order[:20]
    print(f"\nпотвърдени в чистата (над 1 € с над 2 грешки): {int(confirmed.sum())} от {len(R)} - сред тях има и случайни при толкова варианти")
    print(f"20-те най-добри по двете години: 2023/24 {F['s1_ret'][top20].mean():.3f}, 2024/25 {F['s2_ret'][top20].mean():.3f} -> чиста {F['cl_ret'][top20].mean():.3f}")
    out["top20"] = {"s1": float(F["s1_ret"][top20].mean()), "s2": float(F["s2_ret"][top20].mean()), "clean": float(F["cl_ret"][top20].mean())}
    print(f"\n{'#':>3} {'2023/24':>13} {'2024/25':>13} | {'чиста ±':>20} {'€/мес':>6} | вариант")
    out["top50"] = []
    for r_, i in enumerate(order[:50], 1):
        nm = name_of(tasks[D[i, 0]], [int(x) for x in D[i, 1:]])
        out["top50"].append({"name": nm, **{f: float(F[f][i]) for f in FIELDS}, "crit": float(crit[i]), "cl_eur_month": float(cl_month[i])})
        if r_ <= 25:
            print(f"{r_:>3} {F['s1_hit'][i]:>4.0%} {F['s1_ret'][i]:.3f} {F['s2_hit'][i]:>5.0%} {F['s2_ret'][i]:.3f} | {F['cl_hit'][i]:>4.0%} {F['cl_ret'][i]:.3f} ±{F['cl_se'][i]:.2f} n{int(F['cl_n'][i]):<4} {cl_month[i]:>+6.0f} | {nm}")

    def report(gname, gm, idx=None):
        if idx is None:
            if not gm.any():
                print(f"\n{gname}: няма допустим вариант")
                return
            idx = int(np.argmax(np.where(gm, crit, -np.inf)))
        task, d = tasks[D[idx, 0]], [int(x) for x in D[idx, 1:]]
        st, money = detail(task, d, days)
        cl = {m: v for m, v in ((money or {}).get("by_month") or {}).items() if m >= "2025-07"}
        info = {"name": name_of(task, d), "detail": st, "clean_months": cl}
        if task[0] in ("one", "mix") and task[8] == "MAX":
            info["avg_price"] = detail(task[:8] + ("AVG",), d, days)[0]
        if task[6] is not None:
            info["cards_1.40"] = detail(task, d, days, card_price=1.40)[0]
        out["winners"][gname] = info
        s1, s2, c = st["2023/24"], st["2024/25"], st["чиста"]
        print(f"\n{gname}:\n   {info['name']}\n   2023/24: {s1['n']} кол., {s1['hit']:.0%}, {s1['return']:.3f} ± {s1['return_se']:.3f} | 2024/25: {s2['n']} кол., {s2['hit']:.0%}, {s2['return']:.3f} ± {s2['return_se']:.3f}"
              f" | ЧИСТА: {c['n']} кол., {c['hit']:.0%}, ср. коеф. {c['price']:.2f}, {c['return']:.3f} ± {c['return_se']:.3f} {'ПОТВЪРДЕН' if c['return'] - 2 * c['return_se'] > 1 else ''}")
        if cl:
            v = list(cl.values())
            print(f"   чиста по месеци 300 €/30 €: средно {sum(v) / len(v):+.0f} €, плюс в {sum(x > 0 for x in v)} от {len(v)}, най-лош {min(v):+.0f}, най-добър {max(v):+.0f}")
        if "avg_price" in info:
            a = info["avg_price"]["чиста"]
            print(f"   при СРЕДНА цена: чиста {a['return']:.3f} ± {a['return_se']:.3f}")
        if "cards_1.40" in info:
            a = info["cards_1.40"]["чиста"]
            print(f"   картони при 1.40: чиста {a['return']:.3f} ± {a['return_se']:.3f}")

    report("А - най-доброто изобщо", np.ones(len(R), dtype=bool))
    report("Б - по правилата на майстора", master)
    report("В - най-сигурните (минимум на коефициента до 1.20)", floor <= 1.20)
    report("Г - без допускания (без картони)", ~has_cards)
    report("Д - смесени състави", typ == "mix")
    # Е - едновременно при 65% и 70%: същите настройки, критерият е по-малкият от двата
    tnl = {}
    tid = np.array([tnl.setdefault(t[:5] + t[6:], len(tnl)) for t in tasks])[D[:, 0]]
    key = tid.astype(np.int64)
    for col_, base in ((1, 3), (2, 2), (3, 2), (4, 200), (5, 4), (6, 2), (7, 4)):
        key = key * base + D[:, col_].astype(np.int64)
    k65, k70 = np.nonzero(lt == 0.65)[0], np.nonzero(lt == 0.70)[0]
    _, a_i, b_i = np.intersect1d(key[k65], key[k70], return_indices=True)
    i65, i70 = k65[a_i], k70[b_i]
    both = np.minimum(crit[i65], crit[i70])
    j = int(np.argmax(both))
    print(f"\nЕ - най-доброто ЕДНОВРЕМЕННО при 65% и 70% ({len(both)} двойки настройки):")
    report("Е - при 65%", None, int(i65[j]))
    report("Е - при 70%", None, int(i70[j]))
    # най-доброто от първото търсене - къде е тук
    inc = ("one", "голове", "смес", 1.30, True, 0.65, None, False, "MAX")
    ti = tasks.index(inc)
    m = (D[:, 0] == ti) & (D[:, 1] == 1) & (D[:, 2] == 1) & (D[:, 3] == 0) & (D[:, 4] == SCHEMES.index("3 от 4")) & (D[:, 6] == 0) & (D[:, 7] == 0)
    if m.any():
        i = int(np.nonzero(m)[0][0])
        rank = int((crit > crit[i]).sum()) + 1
        print(f"\nнай-доброто от първото търсене (1.141 в чистата): тук 2023/24 {F['s1_ret'][i]:.3f}, 2024/25 {F['s2_ret'][i]:.3f}, чиста {F['cl_ret'][i]:.3f}; място по новия критерий: {rank} от {len(R)}")
        out["incumbent"] = {"s1": float(F["s1_ret"][i]), "s2": float(F["s2_ret"][i]), "clean": float(F["cl_ret"][i]), "rank": rank}
    # със задна дата (НЕ е избор - само колко е най-доброто, ако знаехме бъдещето)
    big = np.nonzero(F["cl_n"] >= 100)[0]
    hb = big[np.argsort(-F["cl_ret"][big])][:5]
    print("\nсъс задна дата (най-добрите в ЧИСТАТА при 100+ колонки - така не се избира, показва само тавана):")
    for i in hb:
        print(f"   чиста {F['cl_ret'][i]:.3f} ± {F['cl_se'][i]:.2f} (n {int(F['cl_n'][i])}), 2023/24 {F['s1_ret'][i]:.3f}, 2024/25 {F['s2_ret'][i]:.3f} | {name_of(tasks[D[i, 0]], [int(x) for x in D[i, 1:]])}")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"\nзаписано: {OUT} ({time.time() - t0:.0f} с)")


if __name__ == "__main__":
    main()
