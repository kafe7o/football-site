"""
ВСИЧКИ ВАРИАНТИ НАВЕДНЪЖ - най-добрият метод за колонките (собственикът, 2026-10-13: „с цялата информация да ползваш всеки един вариант, дето сме говорили до сега, и за
колонки, и за излизане на колонки, и комбинации, и пермутации, и вариации, и накрая да ми дадеш най-добрия метод“).

ПРОТОКОЛ (записан ПРЕДИ пускането):
Данни: backtest_tips (роботът walk-forward, 2023-07..), пазарът (средните цени), football.db MAX (най-добрият букмейкър; цена над 1.25 x средната се смята за счупена и
се взима средната), data/extras_rows.json (картони walk-forward, със съдиите за всички първенства).
МАЧЪТ (кракът на колонката):
  шанс по: робот | пазар | смес (робот + пазар) / 2 | вариант 2 (поправеният, ден по ден само от миналото)
  вид на избора: всички 14 събития | голове над/под | знаци + двоен шанс | всички 14 + картони | само картони
     картони: „над линия-1“ / „под линия+1“ (калибрирани назад). Корнери - НЕ (без умение назад и 0 мача с 75%). Съставите - НЕ (не минаха протокола 13.10).
  праг на цената (истинска средна): 1.30 | 1.40;  съгласие робот-пазар (до 5 пункта): не | да;  без дерби и без „тото“ - винаги
  праг на шанса на мача: 65 | 70 | 75%;  за картоните отделно: 65 | 70 | 75%
  топ шанс (третината на първенството за деня): да | не
  плаща се: средната цена | най-добрият букмейкър (MAX). Картоните - ДОПУСНАТА цена 1.30 за подредбата (цени за картони няма), после 1.25 и 1.40 за сравнение.
КОЛОНКАТА:
  първенства: всички | най-добрата половина (по връщане на мач в ИЗБОРА) | без най-лошите (5, но не повече от 1/3; в ИЗБОРА)
  подредба: по шанс | по шанс x цена;  от едно първенство: един | до два
  форма: единичен | по 2 | по 3 | по 4 (отделни колонки, всеки мач в една) | системи: всички двойки от топ 3 | всички двойки от топ 4 | всички тройки от топ 4
     (подредбата на мачовете вътре в колонката не променя нищо - „пермутациите“ са една и съща колонка)
  колонки на ден: 1 | 2 | 4 (за отделните; системите - всичките им);  праг на колонката: няма | шансът ѝ >= 0.5^(размер/2) (единичен 71%, по 2 - 50%, по 3 - 35%, по 4 - 25%)
  дни: всеки ден | чет-нед | пет-нед | съб-нед
МЕРКИ: колонки на месец, минават (± грешка), връщане от 1 € и грешката - ИЗБОР 2023-07..2025-06 и ЧИСТА 2025-07..; печалба на месец при 30 € на колонка.
ИЗБОРЪТ НА МЕТОДА - САМО по ИЗБОРА; чистата проверка го потвърждава или не:
  условие: поне 100 колонки в избора и поне 40 в чистата;
  критерий: долната граница на печалбата на месец в ИЗБОРА = (връщане - 1 - грешката) x колонки на месец x 30 €.
  Победители: (А) най-доброто изобщо (картони при 1.30); (Б) по правилата на майстора - цена от 1.40, топ шанс, шансът по робота или вариант 2 (не по пазара), подредба по шанс,
  един от първенство, най-много 2 колонки на ден (без системи), без картони (допуснатата им цена е под 1.40); (В) същото, но с картоните (майсторът 12.10) - показва се и при 1.40;
  (Г) без допускания - без картони, само истински цени, всякакви други правила.
  ПОТВЪРДЕН е метод, чието връщане в ЧИСТАТА е над 1 € с над 2 грешки. Казва се колко падат от избора към чистата 20-те най-добри в избора (нагласянето при над 1 милион варианта).
  Чистата проверка е гледана в предишните проверки на проекта (11-13.10) - не е напълно „невиждана“; казва се.
Резултатът: data/all_variants_backtest.json (победителите, топ 50, проверката на котвата), пълната таблица - data/all_variants_full.npz (не е в git).
"""

import json
import math
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
from research import goal_lines_backtest as gl                    # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import master_v2_system as ms                       # noqa: E402
from research import pro_price_backtest as pp                     # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "all_variants_backtest.json"
FULL = ROOT / "data" / "all_variants_full.npz"
SELECT_END = vb.SELECT_END
STAKE = 30.0
CARD_PRICE = 1.30
EV = vb.EV
SRCS = ("робот", "пазар", "смес", "вариант 2")
EVSETS = (("всички 14", None), ("голове", {"O15", "U15", "O", "U", "O35", "U35"}), ("знаци + двоен шанс", {"1", "X", "2", "1X", "X2", "12"}))
FLOORS = (1.30, 1.40)
AGREE = (False, True)
LEG_T = (0.65, 0.70, 0.75)
CARD_T = (0.65, 0.70, 0.75)
TOP3 = (True, False)
BOOKS = ("AVG", "MAX")
KINDS = ("всички 14", "голове", "знаци + двоен шанс", "всички 14 + картони", "само картони")
LEAGUES = ("всички", "най-добрата половина", "без най-лошите")
ORDERS = ("шанс", "шанс x цена")
OPL = (1, 2)
SCHEMES = (1, 2, 3, 4, "2 от 3", "2 от 4", "3 от 4")
COMBO = {"2 от 3": (3, 2), "2 от 4": (4, 2), "3 от 4": (4, 3)}
PER_DAY = (1, 2, 4)
COLTHR = (False, True)
DAYS = {"всеки ден": (0, 1, 2, 3, 4, 5, 6), "чет-нед": (3, 4, 5, 6), "пет-нед": (4, 5, 6), "съб-нед": (5, 6)}
FIELDS = ("sel_n", "sel_hit", "sel_ret", "sel_se", "cl_n", "cl_hit", "cl_ret", "cl_se", "sel_cpm", "cl_cpm")
G = {}


# ---------------------------------------------------------------- подготовка (главният процес)

def adjacent_cards():
    """Картоните: изборът „над линия-1“ / „под линия+1“ с по-големия шанс -> [(лига, ден, домакин, шанс, излязло)]."""
    out = []
    for r in json.loads((ROOT / "data" / "extras_rows.json").read_text(encoding="utf-8")):
        if r["kind"] != "cards" or not r.get("lines"):
            continue
        line = r["line"]
        lo, hi = r["lines"].get(f"{line - 1:.1f}"), r["lines"].get(f"{line + 1:.1f}")
        cands = [c for c in (("O", line - 1, lo), ("U", line + 1, None if hi is None else 1 - hi)) if c[2] is not None]
        if not cands:
            continue
        side, ln, p = max(cands, key=lambda c: c[2])
        out.append((r["league"], r["date"], r["home"], float(p), bool((r["total"] > ln) if side == "O" else (r["total"] < ln))))
    return out


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
    cards = adjacent_cards()
    print(f"мачове {n}, картони {len(cards)} ({time.time() - t0:.0f} с)", flush=True)
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
    bases, broken = [], 0
    for src, (ename, evs), floor, agree in product(SRCS, EVSETS, FLOORS, AGREE):
        S = P if src == "робот" else Q if src == "пазар" else PC if src == "вариант 2" else (P + Q) / 2
        with np.errstate(invalid="ignore"):
            valid = ~np.isnan(R) & (R >= floor) & ~np.isnan(S)
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
        groups = defaultdict(list)
        for i, s in zip(idx.tolist(), score[idx].tolist()):
            groups[(rows[i]["league"], rows[i]["day"])].append((i, s))
        keep = set()
        for items in groups.values():
            keep |= vb.top_third(items)
        ravg = R[idx, k]
        rmax = np.empty(len(idx))
        for j, (i, kk, r) in enumerate(zip(idx.tolist(), k.tolist(), ravg.tolist())):
            row = rows[i]
            m = pp.price_at(row, EV[kk], r, prices.get((row["league"], row["day"], row["home"], row["away"])) or {}, "MAX")
            if m is None or not (r <= m <= r * 1.25):
                broken += m is not None and m > r * 1.25
                m = r
            rmax[j] = m
        bases.append({"src": src, "ev": ename, "floor": floor, "agree": agree, "idx": idx, "p": score[idx], "ravg": ravg, "rmax": rmax,
                      "hit": H[idx, k], "top": np.array([i in keep for i in idx.tolist()])})
    print(f"основи {len(bases)}; цени MAX над 1.25 x средната (счупени, взета средната): {broken} ({time.time() - t0:.0f} с)", flush=True)
    last = days[-1]
    clean_months = (int(last[:4]) - 2025) * 12 + int(last[5:7]) - 7 + 1
    day_wd = np.array([date.fromisoformat(d).weekday() for d in days])
    shared = {"bases": bases, "cards": cd, "row_day": row_day, "row_lg": row_lg, "row_key": row_key, "card_price": CARD_PRICE,
              "day_clean": np.array([d >= SELECT_END for d in days]), "day_in": {k: np.isin(day_wd, v) for k, v in DAYS.items()},
              "months": (24, clean_months), "n_leagues": len(leagues)}
    return shared, days, leagues


def tasks_list():
    out = []
    for b in range(len(SRCS) * len(EVSETS) * len(FLOORS) * len(AGREE)):
        ev = EVSETS[(b // (len(FLOORS) * len(AGREE))) % len(EVSETS)][0]
        for lt, top3, book in product(LEG_T, TOP3, BOOKS):
            out.append((ev, b, lt, None, top3, book))
            if ev == "всички 14":
                for ct in CARD_T:
                    out.append(("всички 14 + картони", b, lt, ct, top3, book))
    for ct in CARD_T:
        out.append(("само картони", None, None, ct, None, None))
    return out


# ---------------------------------------------------------------- работникът

def init(shared):
    G.update(shared)


def pool_legs(task):
    """Мачовете на деня -> [(ден, [(шанс, цена, излязло, първенство)] - най-вероятните първи)] + връщането на мач по първенство в избора."""
    kind, b, lt, ct, top3, book = task
    parts = []
    if kind != "само картони":
        B = G["bases"][b]
        m = B["p"] >= lt
        if top3:
            m &= B["top"]
        rid = B["idx"][m]
        parts.append((G["row_day"][rid], G["row_lg"][rid], G["row_key"][rid], B["p"][m], (B["ravg"] if book == "AVG" else B["rmax"])[m], B["hit"][m]))
    if ct is not None:
        C = G["cards"]
        m = C["p"] >= ct
        parts.append((C["day"][m], C["lg"][m], C["key"][m], C["p"][m], np.full(int(m.sum()), G["card_price"]), C["hit"][m]))
    day, lg, key, p, pr, hit = (np.concatenate(x) for x in zip(*parts))
    if len(day) == 0:
        return [], set(), set()
    o = np.lexsort((-p, key))                                   # един мач - един избор (по-вероятният)
    _, first = np.unique(key[o], return_index=True)
    sel = o[first]
    day, lg, p, pr, hit = day[sel], lg[sel], p[sel], pr[sel], hit[sel]
    o = np.lexsort((-p, day))
    day, lg, p, pr, hit = day[o], lg[o], p[o], pr[o], hit[o]
    # връщането на мач по първенство в ИЗБОРА
    sel_m = ~G["day_clean"][day]
    stats = []
    for j in range(G["n_leagues"]):
        mm = sel_m & (lg == j)
        nn = int(mm.sum())
        if nn >= 100:
            stats.append((j, float((hit[mm] * pr[mm]).mean())))
    stats.sort(key=lambda x: -x[1])
    best = {j for j, _ in stats[: max(1, len(stats) // 2)]}
    worst = {j for j, _ in stats[::-1][: min(5, len(stats) // 3)]}
    bounds = np.flatnonzero(np.diff(day)) + 1
    out = []
    for s, e in zip(np.r_[0, bounds], np.r_[bounds, len(day)]):
        out.append((int(day[s]), list(zip(p[s:e].tolist(), pr[s:e].tolist(), hit[s:e].tolist(), lg[s:e].tolist()))))
    return out, best, worst


def assemble(daylegs, scheme, opl):
    """-> [(ден, номер на колонката в деня, излязла, коефициент, шанс, размер)]."""
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


def measure(cols, mask_fn):
    """[(...)] -> 10 мерки (избор и чиста)."""
    res = np.zeros(len(FIELDS), dtype=np.float64)
    if not cols:
        return res
    a = np.array(cols, dtype=float)
    m = mask_fn(a)
    clean = G["day_clean"][a[:, 0].astype(int)]
    pay = a[:, 2] * a[:, 3]
    for off, per, months in ((0, ~clean, G["months"][0]), (4, clean, G["months"][1])):
        mm = m & per
        n = int(mm.sum())
        if n:
            x = pay[mm]
            res[off:off + 4] = (n, a[mm, 2].mean(), x.mean(), x.std(ddof=1) / math.sqrt(n) if n > 1 else 0.0)
        res[8 + off // 4] = n / months
    return res


def run_task(args):
    t, task = args
    try:
        return _run_task(t, task)
    except Exception:                                           # noqa: BLE001 - задачата се отчита като неуспешна, не се мълчи
        import traceback
        print(f"ГРЕШКА в задача {t} {task}: " + traceback.format_exc(), flush=True)
        return np.zeros((0, len(FIELDS)), dtype=np.float32), np.zeros((0, 8), dtype=np.int16)


def _run_task(t, task):
    daylegs, best, worst = pool_legs(task)
    rows, desc = [], []
    for li, lname in enumerate(LEAGUES):
        if lname == "всички":
            dl = daylegs
        else:
            ok = (lambda j: j in best) if lname == "най-добрата половина" else (lambda j: j not in worst)
            dl = [(d, [l for l in L if ok(l[3])]) for d, L in daylegs]
        for oi, order in enumerate(ORDERS):
            dlo = dl if order == "шанс" else [(d, sorted(L, key=lambda l: -l[0] * l[1])) for d, L in dl]
            for pi, opl in enumerate(OPL):
                for si, scheme in enumerate(SCHEMES):
                    cols = assemble(dlo, scheme, opl)
                    for di, per_day in enumerate(PER_DAY if isinstance(scheme, int) else (99,)):
                        for ci, ct in enumerate(COLTHR):
                            for wi, dname in enumerate(DAYS):
                                def mask_fn(a, per_day=per_day, ct=ct, dname=dname):
                                    m = (a[:, 1] < per_day) & G["day_in"][dname][a[:, 0].astype(int)]
                                    if ct:
                                        m &= a[:, 4] >= 0.5 ** (a[:, 5] / 2)
                                    return m
                                rows.append(measure(cols, mask_fn))
                                desc.append((t, li, oi, pi, si, di if per_day != 99 else 3, ci, wi))
    return np.array(rows, dtype=np.float32), np.array(desc, dtype=np.int16)


# ---------------------------------------------------------------- подробно за един вариант (главният процес)

def detail(task, li, oi, pi, si, di, ci, wi, days, card_price=None):
    old = G["card_price"]
    if card_price is not None:
        G["card_price"] = card_price
    daylegs, best, worst = pool_legs(task)
    G["card_price"] = old
    lname = LEAGUES[li]
    if lname != "всички":
        ok = (lambda j: j in best) if lname == "най-добрата половина" else (lambda j: j not in worst)
        daylegs = [(d, [l for l in L if ok(l[3])]) for d, L in daylegs]
    if ORDERS[oi] != "шанс":
        daylegs = [(d, sorted(L, key=lambda l: -l[0] * l[1])) for d, L in daylegs]
    cols = assemble(daylegs, SCHEMES[si], OPL[pi])
    per_day = PER_DAY[di] if di < 3 else 99
    acc, months = {"select": [], "clean": []}, defaultdict(list)
    for d, rank, ok_, odds, colp, size in cols:
        if rank >= per_day or not G["day_in"][list(DAYS)[wi]][d] or (COLTHR[ci] and colp < 0.5 ** (size / 2)):
            continue
        acc["clean" if G["day_clean"][d] else "select"].append((ok_, odds))
        months[days[d][:7]].append((ok_, odds))
    return {k: gl.stat(v) for k, v in acc.items()}, ms.money(months), dict(months)


def name_of(task, li, oi, pi, si, di, ci, wi):
    kind, b, lt, ct, top3, book = task
    parts = [kind]
    if b is not None:
        B = G["bases"][b]
        parts += [f"шанс: {B['src']}", f"цена от {B['floor']:.2f}", "робот и пазар съгласни" if B["agree"] else "", f"мач от {lt:.0%}",
                  "топ шанс" if top3 else "без топ шанс", "най-добър букмейкър" if book == "MAX" else "средна цена"]
    if ct is not None:
        parts.append(f"картони от {ct:.0%}")
    s = SCHEMES[si]
    parts += [f"първенства: {LEAGUES[li]}", f"подредба: {ORDERS[oi]}", f"от първенство: {OPL[pi]}",
              (f"по {s}" if s != 1 else "единични") if isinstance(s, int) else f"система {s}",
              f"до {PER_DAY[di]} на ден" if di < 3 else "", "колонка с праг" if COLTHR[ci] else "", list(DAYS)[wi]]
    return ", ".join(x for x in parts if x)


def main():
    t0 = time.time()
    import os
    import pickle
    cache = os.environ.get("ALLVAR_CACHE")
    if cache and Path(cache).exists():
        shared, days, leagues = pickle.loads(Path(cache).read_bytes())
        print("подготовката - от кеша", flush=True)
    else:
        shared, days, leagues = prepare()
        if cache:
            Path(cache).write_bytes(pickle.dumps((shared, days, leagues)))
    init(shared)
    tasks = tasks_list()
    print(f"задачи {len(tasks)} x {len(LEAGUES) * len(ORDERS) * len(OPL) * (4 * len(PER_DAY) + 3) * len(COLTHR) * len(DAYS)} варианта", flush=True)
    # котвата: системата (вариант 2, всички 14, 1.40, без съгласие, 65%, топ шанс, средна, най-добрата половина, по шанс, един, по 2, 1 на ден, праг 50%, чет-нед) -
    # трябва да даде ~7 колонки/месец, избор 53% / 1.142, чиста 51% / 1.088 (research/fewer_columns_backtest.py)
    b_sys = [j for j, B in enumerate(shared["bases"]) if B["src"] == "вариант 2" and B["ev"] == "всички 14" and B["floor"] == 1.40 and not B["agree"]][0]
    anchor = detail(("всички 14", b_sys, 0.65, None, True, "AVG"), 1, 0, 0, 1, 0, 1, 1, days)
    a_s, a_c = anchor[0]["select"], anchor[0]["clean"]
    print(f"КОТВА (системата): избор {a_s['n']} кол., {a_s['hit']:.1%}, {a_s['return']:.3f} | чиста {a_c['n']} кол., {a_c['hit']:.1%}, {a_c['return']:.3f} ± {a_c['return_se']:.2f}", flush=True)
    res, desc = [], []
    with Pool(4, initializer=init, initargs=(shared,)) as pool:
        for j, (r, d) in enumerate(pool.imap_unordered(run_task, list(enumerate(tasks)), chunksize=2)):
            res.append(r)
            desc.append(d)
            if j % 100 == 0:
                print(f"  {j}/{len(tasks)} задачи ({time.time() - t0:.0f} с)", flush=True)
    R = np.concatenate(res).astype(np.float64)
    D = np.concatenate(desc)
    per_task = len(LEAGUES) * len(ORDERS) * len(OPL) * (4 * len(PER_DAY) + 3) * len(COLTHR) * len(DAYS)
    print(f"неуспешни задачи: {len(tasks) - len(R) // per_task}", flush=True)
    np.savez_compressed(FULL, R=R, D=D)
    F = {f: R[:, k] for k, f in enumerate(FIELDS)}
    print(f"вариантите: {len(R)} ({time.time() - t0:.0f} с)", flush=True)
    eligible = (F["sel_n"] >= 100) & (F["cl_n"] >= 40)
    lb = (F["sel_ret"] - 1 - F["sel_se"]) * F["sel_cpm"] * STAKE
    cl_month = (F["cl_ret"] - 1) * F["cl_cpm"] * STAKE
    sel_month = (F["sel_ret"] - 1) * F["sel_cpm"] * STAKE
    confirmed = eligible & (F["cl_ret"] - 2 * F["cl_se"] > 1)

    def task_of(i):
        return tasks[int(D[i, 0])]

    tk = np.array([KINDS.index(t[0]) for t in tasks])[D[:, 0]]
    t_b = np.array([-1 if t[1] is None else t[1] for t in tasks])[D[:, 0]]
    t_top = np.array([bool(t[4]) for t in tasks])[D[:, 0]]
    b_src = np.array([B["src"] for B in shared["bases"]] + [""])[t_b]
    b_floor = np.array([B["floor"] for B in shared["bases"]] + [0.0])[t_b]
    has_cards = np.isin(tk, (3, 4))
    is_combo = D[:, 4] >= 4
    master = (b_floor == 1.40) & t_top & np.isin(b_src, ("робот", "вариант 2")) & (D[:, 2] == 0) & (D[:, 3] == 0) & ~is_combo & (D[:, 5] <= 1)
    groups = {"А - най-доброто изобщо (картони при допусната цена 1.30)": eligible,
              "Б - по правилата на майстора, без картони": eligible & master & ~has_cards,
              "В - по правилата на майстора + картони (майсторът 12.10)": eligible & master & (tk == 3),
              "Г - без допускания (само истински цени, всякакви други правила)": eligible & ~has_cards}
    out = {"generated": date.today().isoformat(), "variants": int(len(R)), "eligible": int(eligible.sum()), "confirmed_in_clean": int(confirmed.sum()),
           "anchor": {"select": a_s, "clean": a_c}, "winners": {}, "top50": []}

    def row_info(i):
        return {"name": name_of(task_of(i), *[int(x) for x in D[i, 1:]]), **{f: float(F[f][i]) for f in FIELDS},
                "sel_eur_month": float(sel_month[i]), "cl_eur_month": float(cl_month[i]), "lb_sel": float(lb[i])}

    print(f"\nдопустими (избор >= 100, чиста >= 40): {int(eligible.sum())}; потвърдени в чистата (над 1 € с над 2 грешки): {int(confirmed.sum())}")
    order = np.argsort(-np.where(eligible, lb, -np.inf))
    top20 = order[:20]
    print(f"20-те най-добри в избора: избор ср. {F['sel_ret'][top20].mean():.3f} € -> чиста ср. {F['cl_ret'][top20].mean():.3f} €  (нагласянето)")
    out["top20_decay"] = {"select": float(F["sel_ret"][top20].mean()), "clean": float(F["cl_ret"][top20].mean())}
    print(f"\n{'#':>3} {'избор: мин. връща ±':>22} {'€/мес':>6} | {'чиста: мин. връща ±':>22} {'€/мес':>6} | {'кол/мес':>7} | вариант")
    for r_, i in enumerate(order[:50], 1):
        info = row_info(i)
        out["top50"].append(info)
        if r_ <= 30:
            print(f"{r_:>3} {F['sel_hit'][i]:>5.0%} {F['sel_ret'][i]:.3f} ±{F['sel_se'][i]:.2f} {sel_month[i]:>+6.0f} | {F['cl_hit'][i]:>5.0%} {F['cl_ret'][i]:.3f} ±{F['cl_se'][i]:.2f} {cl_month[i]:>+6.0f} | {F['sel_cpm'][i]:>7.1f} | {info['name']}")
    for gname, gm in groups.items():
        if not gm.any():
            print(f"\n{gname}: няма допустим вариант")
            continue
        i = int(np.argmax(np.where(gm, lb, -np.inf)))
        task = task_of(i)
        args = [int(x) for x in D[i, 1:]]
        st, money, _ = detail(task, *args, days)
        info = {**row_info(i), "detail": st, "money": {k: v for k, v in (money or {}).items() if k != "by_month"}, "by_month": (money or {}).get("by_month")}
        if task[3] is not None:
            for cp in (1.25, 1.40):
                st2, m2, _ = detail(task, *args, days, card_price=cp)
                info[f"cards_{cp:.2f}"] = {"detail": st2, "money": {k: v for k, v in (m2 or {}).items() if k != "by_month"}}
        out["winners"][gname] = info
        s, c = st["select"], st["clean"]
        print(f"\n{gname}:\n   {info['name']}\n   избор: {s['n']} кол., минават {s['hit']:.1%}, връща {s['return']:.3f} ± {s['return_se']:.3f} | "
              f"чиста: {c['n']} кол., минават {c['hit']:.1%}, връща {c['return']:.3f} ± {c['return_se']:.3f} | {F['sel_cpm'][i]:.1f} / {F['cl_cpm'][i]:.1f} кол. на месец")
        if money:
            print(f"   300 €/30 €: средно {money['avg']:+.0f} €/месец, +100 € в {money['plus100']} от {money['months']}, +150 € в {money['plus150']}, минус в {money['minus']}, "
                  f"най-лош {money['worst']:+.0f}, най-добър {money['best']:+.0f}")
        for cp in (1.25, 1.40):
            k2 = f"cards_{cp:.2f}"
            if k2 in info:
                c2, m2 = info[k2]["detail"]["clean"], info[k2]["money"]
                print(f"   картони при {cp:.2f}: чиста връща {c2['return']:.3f} ± {c2['return_se']:.3f}; 300/30: {m2.get('avg', 0):+.0f} €/месец")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"\nзаписано: {OUT} ({time.time() - t0:.0f} с)")


if __name__ == "__main__":
    main()
