"""
ГЪВКАВИ КОЛОНКИ И ОГРАНИЧАВАНЕ НА МАЧОВЕ за надстройката (собственикът, 2026-10-13 вечерта: „как да ограничиш, ако трябва, някои мачове и/или колонки; дали да има
adjustable колонки - в определен период 4 мача, в друг 3, в друг 2 - и колко колонки“).

Основа: мачовете на надстройката (bets/addon.py, research/all_variants_backtest.py): голове, шанс смес робот + пазар, съгласни до 5 пункта, >= 65%, средна цена >= 1.30,
подредба шанс x цена, един мач от първенство. Парите - ПРАВИЛОТО НА СОБСТВЕНИКА: 30 € на ден с колонки, разделени поравно между колонките на деня (по 0.50 €),
месецът спира на -30 € и залогът за деня никога не е над остатъка до -30 €.

ПРОТОКОЛ (записан ПРЕДИ пускането):
ФИЛТРИ НА МАЧОВЕТЕ (само от миналото): няма | ценови ръб (най-добра / средна >= 1.03) | коефициент до 1.60 | коефициент от 1.40 | само „под“ | само „над“ |
  първенства, в които методът е излизал поне колкото казва (последните 365 дни, поне 30 мача) | без първите 21 дни на сезона на първенството |
  пауза, ако мачовете на метода в последните 14 дни са излезли под 55% (поне 10) | само петък-неделя.
ФОРМА НА ДЕНЯ (от наредените мачове): единичен | 2 единични | 4 единични | двойка | тройка | четворка | 2 двойки | 2 от 3 | 2 от 4 | 3 от 4 (сегашното) | 3 от 5 | 4 от 5;
ГЪВКАВИ: А (5+ -> 3 от 5, 4 -> 3 от 4, 3 -> тройка, 2 -> двойка) | Б (4+ -> 2 от 4, 3 -> 2 от 3, 2 -> двойка, 1 -> единичен) | В (4+ -> 3 от 4, 3 -> 2 от 3, 2 -> двойка) |
  ОЧАКВАНЕ: от {единичен, двойка, тройка, 2 от 3, 2 от 4, 3 от 4} - формата с най-голяма очаквана печалба по шанса (поправен с миналата разлика казано-излязло) x цената, само ако е над 1.00 |
  ПЛЮСОВИТЕ: една колонка от всички мачове с очаквана печалба над 1.00 (до 4).
ПЕРИОД ОТ СЕЗОНА: за август-септември, октомври-декември, януари-март, април-юни - най-добрата неподвижна форма В ИЗБОРА за периода; после се прилага в чистата.
Цена: най-добрата (MAX) | средната. Първенства: най-добрата половина (както надстройката) | всички.
ИЗБОР: само по 2023/24 и 2024/25 - критерият е по-малкото от двете средни на месец по правилото за парите; поне 6 месеца със залог във всяка година. Сравнява се със
сегашното (3 от 4, без филтър, MAX, най-добрата половина). Чистата проверка (2025-07..) не участва в избора. Казва се и спадът на 20-те най-добри.
Резултатът: data/adaptive_columns_backtest.json.
"""

import json
import math
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations, product
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import robot                                            # noqa: E402
from research import pro_price_backtest as pp                     # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "adaptive_columns_backtest.json"
S1_END, SELECT_END = "2024-07-01", vb.SELECT_END
GOALS = {"O15", "U15", "O", "U", "O35", "U35"}
DAY_BUDGET, CAP = 30.0, 30.0
STATIC = ("единичен", "2 единични", "4 единични", "двойка", "тройка", "четворка", "2 двойки", "2 от 3", "2 от 4", "3 от 4", "3 от 5", "4 от 5")
DYNAMIC = ("гъвкава А", "гъвкава Б", "гъвкава В", "по очакване", "плюсовите")
FILTERS = ("няма", "ценови ръб", "коеф. до 1.60", "коеф. от 1.40", "само под", "само над", "първенства, където излиза", "без началото на сезона",
           "пауза след лоша серия", "петък-неделя")
SEASON_P = {8: "авг-сеп", 9: "авг-сеп", 10: "окт-дек", 11: "окт-дек", 12: "окт-дек", 1: "яну-мар", 2: "яну-мар", 3: "яну-мар", 4: "апр-юни", 5: "апр-юни", 6: "апр-юни", 7: "авг-сеп"}


def legs_all():
    """Всички мачове на надстройката (без праг на първенствата): ден, лига, събитие, шанс (смес), средна и най-добра цена, излязло."""
    rows = vb.build()
    A = vb.arrays(rows)
    P, Q, R, H = A["P"], A["Q"], A["R"], A["H"]
    n = len(rows)
    toto = np.zeros((n, len(vb.EV)), dtype=bool)
    for i, row in enumerate(rows):
        if row["league"] in robot.TOTO_LEAGUES:
            for k, e in enumerate(vb.EV):
                toto[i, k] = robot.toto_blocked(row["league"], e, R[i, k])
    S = (P + Q) / 2
    with np.errstate(invalid="ignore"):
        valid = ~np.isnan(R) & (R >= 1.30) & ~np.isnan(S) & np.array([e in GOALS for e in vb.EV])[None, :] & ~np.isnan(Q) & (np.abs(P - Q) <= 0.05)
    valid &= ~A["derby"][:, None] & ~toto
    Sm = np.where(valid, S, -np.inf)
    best = Sm.argmax(axis=1)
    score = Sm[np.arange(n), best]
    prices = pp.load_prices()
    out = []
    for i in np.nonzero(np.isfinite(score) & (score >= 0.65))[0]:
        k = best[i]
        row = rows[i]
        r = float(R[i, k])
        m = pp.price_at(row, vb.EV[k], r, prices.get((row["league"], row["day"], row["home"], row["away"])) or {}, "MAX")
        out.append({"day": row["day"], "league": row["league"], "sel": vb.EV[k], "p": float(score[i]), "avg": r,
                    "max": m if (m is not None and r <= m <= r * 1.25) else r, "hit": bool(H[i, k])})
    return out


def season_starts(legs):
    """(лига, сезон) -> първият ден с мач на метода в сезона (сезонът започва от юли)."""
    first = {}
    for l in legs:
        y = int(l["day"][:4]) - (1 if int(l["day"][5:7]) < 7 else 0)
        key = (l["league"], y)
        if key not in first or l["day"] < first[key]:
            first[key] = l["day"]
    return first


def best_half(legs, book):
    acc = defaultdict(list)
    for l in legs:
        if l["day"] < SELECT_END:
            acc[l["league"]].append(l["hit"] * l[book])
    st = sorted(((lg, sum(v) / len(v)) for lg, v in acc.items() if len(v) >= 100), key=lambda x: -x[1])
    return {lg for lg, _ in st[: max(1, len(st) // 2)]}


def filtered(legs, flt, starts):
    """-> {ден: [мачове]} след филтъра; филтрите с история гледат само дни преди деня."""
    by_day = defaultdict(list)
    for l in legs:
        by_day[l["day"]].append(l)
    days = sorted(by_day)
    out = {}
    hist = []                                                    # (ден, лига, шанс, излязло) - уредените преди деня
    for d in days:
        today = by_day[d]
        if flt == "първенства, където излиза":
            lim = (date.fromisoformat(d) - timedelta(days=365)).isoformat()
            agg = defaultdict(lambda: [0, 0.0, 0])
            for hd, lg, p, h in hist:
                if hd >= lim:
                    a = agg[lg]
                    a[0] += 1; a[1] += p; a[2] += h
            today = [l for l in today if agg[l["league"]][0] >= 30 and agg[l["league"]][2] / agg[l["league"]][0] >= agg[l["league"]][1] / agg[l["league"]][0]]
        elif flt == "пауза след лоша серия":
            lim = (date.fromisoformat(d) - timedelta(days=14)).isoformat()
            rec = [h for hd, lg, p, h in hist if hd >= lim]
            if len(rec) >= 10 and sum(rec) / len(rec) < 0.55:
                today = []
        elif flt == "без началото на сезона":
            today = [l for l in today if date.fromisoformat(l["day"]) >= date.fromisoformat(starts[(l["league"], int(l["day"][:4]) - (1 if int(l["day"][5:7]) < 7 else 0))]) + timedelta(days=21)]
        elif flt == "ценови ръб":
            today = [l for l in today if l["max"] >= l["avg"] * 1.03]
        elif flt == "коеф. до 1.60":
            today = [l for l in today if l["avg"] <= 1.60]
        elif flt == "коеф. от 1.40":
            today = [l for l in today if l["avg"] >= 1.40]
        elif flt == "само под":
            today = [l for l in today if l["sel"].startswith("U")]
        elif flt == "само над":
            today = [l for l in today if l["sel"].startswith("O")]
        elif flt == "петък-неделя":
            today = today if date.fromisoformat(d).weekday() >= 4 else []
        out[d] = today
        hist += [(d, l["league"], l["p"], l["hit"]) for l in by_day[d]]
    return out


def bias_by_day(legs):
    """Поправката на шанса: средното (казано - излязло) по всички мачове ПРЕДИ деня (свиване 50)."""
    by_day = defaultdict(list)
    for l in legs:
        by_day[l["day"]].append(l)
    out, s, n = {}, 0.0, 0
    for d in sorted(by_day):
        out[d] = s / (n + 50)
        for l in by_day[d]:
            s += l["p"] - l["hit"]; n += 1
    return out


def structure(L, form, book, bias):
    """L - наредените мачове на деня (един от първенство) -> [колонки като списъци от мачове]."""
    n = len(L)
    combo = lambda k, m: [list(c) for c in combinations(L[:k], m)] if n >= k else []
    if form == "единичен":
        return [[L[0]]] if n >= 1 else []
    if form == "2 единични":
        return [[l] for l in L[:2]] if n >= 2 else []
    if form == "4 единични":
        return [[l] for l in L[:4]] if n >= 4 else []
    if form == "двойка":
        return [L[:2]] if n >= 2 else []
    if form == "тройка":
        return [L[:3]] if n >= 3 else []
    if form == "четворка":
        return [L[:4]] if n >= 4 else []
    if form == "2 двойки":
        return [L[:2], L[2:4]] if n >= 4 else []
    if form in ("2 от 3", "2 от 4", "3 от 4", "3 от 5", "4 от 5"):
        m, k = int(form[0]), int(form[-1])
        return combo(k, m)
    if form == "гъвкава А":
        return combo(5, 3) if n >= 5 else combo(4, 3) if n == 4 else [L[:3]] if n == 3 else [L[:2]] if n == 2 else []
    if form == "гъвкава Б":
        return combo(4, 2) if n >= 4 else combo(3, 2) if n == 3 else [L[:2]] if n == 2 else [[L[0]]] if n == 1 else []
    if form == "гъвкава В":
        return combo(4, 3) if n >= 4 else combo(3, 2) if n == 3 else [L[:2]] if n == 2 else []
    ev = lambda col: math.prod((l["p"] - bias) * l[book] for l in col)
    if form == "по очакване":
        best, best_ev = [], 1.0
        for f in ("единичен", "двойка", "тройка", "2 от 3", "2 от 4", "3 от 4"):
            cols = structure(L, f, book, bias)
            if cols:
                e = sum(ev(c) for c in cols) / len(cols)
                if e > best_ev:
                    best, best_ev = cols, e
        return best
    if form == "плюсовите":
        plus = [l for l in L if (l["p"] - bias) * l[book] > 1.0][:4]
        return [plus] if plus else []
    raise ValueError(form)


def simulate(day_legs, form_of, book, leagues, bias):
    """-> {месец: (резултат, колонки, минали, заложено)}, и [(излязла, коефициент)] на колонките. form_of(ден) -> форма."""
    months = defaultdict(lambda: [0.0, 0, 0, 0.0])
    items = []
    for d in sorted(day_legs):
        L = [l for l in day_legs[d] if leagues is None or l["league"] in leagues]
        L.sort(key=lambda l: -l["p"] * l[book])
        seen, U = set(), []
        for l in L:
            if l["league"] not in seen:
                U.append(l); seen.add(l["league"])
        cols = structure(U, form_of(d), book, bias[d]) if U else []
        if not cols:
            continue
        m = months[d[:7]]
        room = CAP + m[0]
        stake = math.floor(min(DAY_BUDGET, max(room, 0)) / len(cols) * 2) / 2
        for c in cols:
            ok, odds = all(l["hit"] for l in c), math.prod(l[book] for l in c)
            items.append((d, ok, odds))
            if stake > 0:
                m[0] += stake * (odds - 1) if ok else -stake
                m[1] += 1; m[2] += ok; m[3] += stake
    return months, items


def summarize(months, items, a, b):
    ms = [v for k, v in months.items() if a <= k + "-01" < b]
    its = [(ok, o) for d, ok, o in items if a <= d < b]
    pay = [o if ok else 0.0 for ok, o in its]
    n = len(pay)
    cal = []
    y, mo = int(a[:4]), int(a[5:7])
    while f"{y:04d}-{mo:02d}-01" < b and f"{y:04d}-{mo:02d}" <= "2026-09":
        cal.append(f"{y:04d}-{mo:02d}"); mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    per = [months[c][0] if c in months else 0.0 for c in cal]
    active = sum(1 for c in cal if c in months and months[c][1])
    return {"cols": n, "hit": sum(ok for ok, _ in its) / n if n else None, "ret": sum(pay) / n if n else None,
            "se": float(np.std(pay, ddof=1) / math.sqrt(n)) if n > 1 else None, "avg_month": sum(per) / len(per) if per else 0.0,
            "total": sum(per), "plus": sum(x > 0 for x in per), "minus": sum(x < 0 for x in per), "worst": min(per) if per else 0, "best": max(per) if per else 0,
            "months": len(per), "active": active, "staked": sum(v[3] for v in ms)}


def main():
    t0 = time.time()
    legs = legs_all()
    print(f"мачове на метода (без праг на първенствата): {len(legs)} ({time.time() - t0:.0f} с)", flush=True)
    starts = season_starts(legs)
    bias = bias_by_day(legs)
    halves = {book: best_half(legs, book) for book in ("max", "avg")}
    res = []
    periods = (("2023/24", "2023-07-01", S1_END), ("2024/25", S1_END, SELECT_END), ("чиста", SELECT_END, "2026-12-31"))
    for flt in FILTERS:
        dl = filtered(legs, flt, starts)
        for book, lname in product(("max", "avg"), ("най-добрата половина", "всички")):
            leagues = halves[book] if lname == "най-добрата половина" else None
            for form in STATIC + DYNAMIC:
                months, items = simulate(dl, lambda d, f=form: f, book, leagues, bias)
                row = {"filter": flt, "book": book, "leagues": lname, "form": form, **{p: summarize(months, items, a, b) for p, a, b in periods}}
                res.append(row)
            # по периоди от сезона: най-добрата неподвижна форма за всеки период В ИЗБОРА (критерий - по-малкото от двете години по правилото за парите)
            best_form = {}
            for sp in set(SEASON_P.values()):
                cand = []
                for form in STATIC:
                    months, items = simulate({d: v for d, v in dl.items() if SEASON_P[int(d[5:7])] == sp}, lambda d, f=form: f, book, leagues, bias)
                    s1, s2 = summarize(months, items, "2023-07-01", S1_END), summarize(months, items, S1_END, SELECT_END)
                    cand.append((min(s1["avg_month"], s2["avg_month"]), form))
                best_form[sp] = max(cand)[1]
            months, items = simulate(dl, lambda d: best_form[SEASON_P[int(d[5:7])]], book, leagues, bias)
            res.append({"filter": flt, "book": book, "leagues": lname, "form": "по сезон: " + ", ".join(f"{k} {v}" for k, v in sorted(best_form.items())),
                        **{p: summarize(months, items, a, b) for p, a, b in periods}})
        print(f"  {flt} ({time.time() - t0:.0f} с)", flush=True)
    ok = [r for r in res if r["2023/24"]["active"] >= 6 and r["2024/25"]["active"] >= 6]
    crit = lambda r: min(r["2023/24"]["avg_month"], r["2024/25"]["avg_month"])
    ok.sort(key=lambda r: -crit(r))
    base = next(r for r in res if r["filter"] == "няма" and r["book"] == "max" and r["leagues"] == "най-добрата половина" and r["form"] == "3 от 4")
    fmt = lambda s: f"{s['avg_month']:+6.1f} €/м ({s['plus']:>2}+ {s['minus']:>2}-) връща {s['ret']:.2f}" if s["ret"] is not None else "-"
    print(f"\nСЕГАШНОТО: 2023/24 {fmt(base['2023/24'])} | 2024/25 {fmt(base['2024/25'])} | ЧИСТА {fmt(base['чиста'])}; място {ok.index(base) + 1 if base in ok else '-'} от {len(ok)}")
    print(f"\n{'#':>3} {'2023/24':>34} | {'2024/25':>34} | {'ЧИСТА 2025/26':>34} | вариант")
    for i, r in enumerate(ok[:30], 1):
        print(f"{i:>3} {fmt(r['2023/24']):>34} | {fmt(r['2024/25']):>34} | {fmt(r['чиста']):>34} | {r['filter']} · {'най-добър' if r['book'] == 'max' else 'средна'} · {r['leagues']} · {r['form']}")
    top20 = ok[:20]
    print(f"\n20-те най-добри по двете години: {np.mean([crit(r) for r in top20]):+.1f} €/м -> чиста {np.mean([r['чиста']['avg_month'] for r in top20]):+.1f} €/м "
          f"(сегашното в чистата {base['чиста']['avg_month']:+.1f})")
    for fam, test in (("филтри (при 3 от 4, най-добър, най-добрата половина)", lambda r: r["form"] == "3 от 4" and r["book"] == "max" and r["leagues"] == "най-добрата половина"),
                      ("форми (без филтър, най-добър, най-добрата половина)", lambda r: r["filter"] == "няма" and r["book"] == "max" and r["leagues"] == "най-добрата половина")):
        print(f"\n{fam}:")
        for r in sorted([r for r in res if test(r)], key=lambda r: -crit(r)):
            print(f"   {r['filter'] if 'филтри' in fam else r['form']:44} 2023/24 {fmt(r['2023/24'])} | 2024/25 {fmt(r['2024/25'])} | ЧИСТА {fmt(r['чиста'])}")
    OUT.write_text(json.dumps({"generated": date.today().isoformat(), "variants": res, "baseline": base}, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"\nзаписано: {OUT} ({time.time() - t0:.0f} с)")


if __name__ == "__main__":
    main()
