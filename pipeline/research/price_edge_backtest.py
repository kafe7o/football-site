"""
Залог по ЦЕНАТА, не по прогнозата - как работят професионалистите (собственикът, 2026-10-13: „пропускаме ли нещо - мисли като професионален бетър от 10 години“).

Идеята (Kaunitz, Zhong, Kreiner 2017, „Beating the bookies with their own numbers“; „value betting“ по сравнение на коефициенти): прогнозата на пазара (средната или
Pinnacle - най-точният букмейкър) е по-добра от нашата; печалба има, когато НЯКОЙ букмейкър плаща повече от честната цена на пазара. Роботът не участва.

ПРОТОКОЛ (записан ПРЕДИ пускането):
Данни: football.db, всички първенства с AVG и MAX, 2012-07..2026-09. Пазари: 1/X/2 (3 изхода) и над/под 2.5 (2 изхода). Единични залози, по 1 € (колонки - не: маржът се умножава).
Честна вероятност: (а) средната на букмейкърите без маржа (степенно); (б) Pinnacle без маржа (където я има - до 01.2026); (в) Kaunitz: 1/средна (без махане на маржа) - 0.05.
Ръб = коефициент x честна вероятност - 1 (за (в): залог, ако коефициентът > 1 / (1/средна - 0.05)).
Плаща се: MAX (най-добрият от ~40 букмейкъра) | B365 (един голям букмейкър - близо до това, което има собственикът).
Прагове на ръба {0, 2, 3, 5, 8%}; коефициенти {до 10, до 3, до 2}; счупени цени: MAX над 50, сбор 1/MAX под 0.95, или MAX над 1.25 x средната (и вариант до 1.5) - не се залага.
Периоди: А 2012-07..2019-06 (ИЗБОР), Б 2019-07..2023-06, В 2023-07..2026-09 (чиста). Работи, ако избраното САМО по А е на плюс с над 2 грешки и в Б, и във В.
Мери се: залози, познати, ср. коеф., доход ± грешка, и CLV - дали коефициентът при залога е по-добър от затварящия (MAXC/AVGC) - доказателството на професионалистите.
Резултатът: data/price_edge_backtest.json.
"""

import json
import math
import sqlite3
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import market                                           # noqa: E402

OUT = ROOT / "data" / "price_edge_backtest.json"
PERIODS = (("А", "2012-07-01", "2019-07-01"), ("Б", "2019-07-01", "2023-07-01"), ("В", "2023-07-01", "2026-12-31"))


def load():
    c = sqlite3.connect(ROOT / "football.db")
    m = pd.read_sql("SELECT id, league, date, fthg, ftag FROM matches WHERE fthg IS NOT NULL AND date >= '2012-07-01'", c).set_index("id")
    o = pd.read_sql("SELECT match_id, bookmaker, odds_home h, odds_draw d, odds_away a FROM odds WHERE bookmaker IN ('AVG','MAX','PS','B365','AVGC','MAXC')", c)
    t = pd.read_sql("SELECT match_id, bookmaker, odds_over o, odds_under u FROM odds_totals WHERE line = 2.5 AND bookmaker IN ('AVG','MAX','PS','B365','AVGC','MAXC')", c)
    wide1 = o.pivot_table(index="match_id", columns="bookmaker", values=["h", "d", "a"], aggfunc="first")
    wide1.columns = [f"{b}_{s}" for s, b in wide1.columns]
    wide2 = t.pivot_table(index="match_id", columns="bookmaker", values=["o", "u"], aggfunc="first")
    wide2.columns = [f"{b}_{s}" for s, b in wide2.columns]
    return m.join(wide1, how="left").join(wide2, how="left")


def market_rows(df, mk):
    """-> список от изходи: (дата, лига, изход, излязло, средна, MAX, PS, B365, AVGC, MAXC, честна по AVG, честна по PS, честна по AVGC) като DataFrame."""
    sides = ("h", "d", "a") if mk == "1x2" else ("o", "u")
    if mk == "1x2":
        res = np.select([df.fthg > df.ftag, df.fthg == df.ftag], [0, 1], 2)
    else:
        res = np.where(df.fthg + df.ftag > 2.5, 0, 1)
    fair = {}
    for b in ("AVG", "PS", "AVGC"):
        cols = [f"{b}_{s}" for s in sides]
        if all(c in df for c in cols):
            fair[b] = market.implied_probs(df[cols].to_numpy())
        else:
            fair[b] = np.full((len(df), len(sides)), np.nan)
    maxcols = [f"MAX_{s}" for s in sides]
    inv_max = (1 / df[maxcols]).sum(axis=1).to_numpy()
    out = []
    for k, s in enumerate(sides):
        g = lambda b: df[f"{b}_{s}"].to_numpy() if f"{b}_{s}" in df else np.full(len(df), np.nan)
        out.append(pd.DataFrame({"date": df.date.to_numpy(), "league": df.league.to_numpy(), "side": s, "hit": res == k,
                                 "AVG": g("AVG"), "MAX": g("MAX"), "PS": g("PS"), "B365": g("B365"), "AVGC": g("AVGC"), "MAXC": g("MAXC"),
                                 "f_AVG": fair["AVG"][:, k], "f_PS": fair["PS"][:, k], "f_AVGC": fair["AVGC"][:, k], "inv_max": inv_max}))
    return pd.concat(out, ignore_index=True)


def stat(x):
    n = len(x)
    if not n:
        return {"n": 0}
    pay = np.where(x["hit"], x["price"], 0.0)
    roi = pay.mean() - 1
    clv = (x["price"] / x["MAXC"] - 1).dropna() if "MAXC" in x else pd.Series(dtype=float)
    fclv = (x["price"] * x["f_AVGC"] - 1).dropna()
    return {"n": int(n), "hit": float(x["hit"].mean()), "odds": float(x["price"].mean()), "roi": float(roi), "se": float(pay.std(ddof=1) / math.sqrt(n)) if n > 1 else 0.0,
            "clv_close_fair": float(fclv.mean()) if len(fclv) else None}


def main():
    df = load()
    print("мачове:", len(df))
    res = []
    for mk in ("1x2", "ou"):
        R = market_rows(df, mk)
        R = R[R["AVG"].notna() & R["MAX"].notna()]
        for fair, book, thr, band, cap in product(("AVG", "PS", "Kaunitz"), ("MAX", "B365"), (0.0, 0.02, 0.03, 0.05, 0.08), (10.0, 3.0, 2.0), (1.25, 1.5)):
            if fair == "Kaunitz" and thr != 0.0:
                continue                                       # Kaunitz има свой праг (0.05) - другите прагове не се прилагат
            x = R.copy()
            x["price"] = x[book]
            ok = x["price"].notna() & (x["price"] > 1.0) & (x["price"] <= band) & (x["MAX"] <= 50) & (x["inv_max"] >= 0.95) & (x["MAX"] <= x["AVG"] * cap)
            if fair == "Kaunitz":
                pc = 1 / x["AVG"] - 0.05
                bet = ok & (pc > 0) & (x["price"] > 1 / pc.where(pc > 0))
            else:
                f = x[f"f_{fair}"]
                bet = ok & f.notna() & (x["price"] * f - 1 >= thr)
            sel = x[bet]
            row = {"market": mk, "fair": fair, "book": book, "thr": thr, "band": band, "cap": cap}
            for name, a, b in PERIODS:
                row[name] = stat(sel[(sel["date"] >= a) & (sel["date"] < b)])
            res.append(row)
    # избор САМО по А: най-голямата долна граница (доход - 1 грешка), поне 300 залога в А
    ok = [r for r in res if r["А"].get("n", 0) >= 300]
    ok.sort(key=lambda r: -(r["А"]["roi"] - r["А"]["se"]))
    print(f"\n{'вариант':48} | {'А 2012-19 доход ±':>22} | {'Б 2019-23':>22} | {'В 2023-26 (чиста)':>22} | CLV В")
    for r in ok[:25]:
        nm = f"{r['market']} честна:{r['fair']} плаща:{r['book']} ръб>={r['thr']:.0%} до {r['band']:.0f} cap{r['cap']}"
        cells = " | ".join(f"{r[p]['roi']:+6.1%} ±{r[p]['se']:.1%} n{r[p]['n']:<6}" if r[p].get("n") else f"{'-':>22}" for p in ("А", "Б", "В"))
        clv = r["В"].get("clv_close_fair")
        print(f"{nm:48} | {cells} | {clv:+.1%}" if clv is not None else f"{nm:48} | {cells} |")
    best = ok[0]
    conf = all(best[p].get("n", 0) and best[p]["roi"] - 2 * best[p]["se"] > 0 for p in ("Б", "В"))
    print(f"\nИЗБРАНО по А: {best['market']} {best['fair']} {best['book']} {best['thr']} {best['band']} {best['cap']} -> потвърдено в Б и В: {conf}")
    for book in ("MAX", "B365"):
        bb = [r for r in ok if r["book"] == book]
        if bb:
            b = bb[0]
            print(f"най-доброто в А при {book}: {b['market']} {b['fair']} ръб>={b['thr']:.0%} до {b['band']:.0f} cap{b['cap']}: "
                  + "; ".join(f"{p} {b[p]['roi']:+.1%} ± {b[p]['se']:.1%} (n {b[p]['n']})" for p in ("А", "Б", "В") if b[p].get("n")))
    OUT.write_text(json.dumps({"variants": res, "chosen": best, "confirmed": conf}, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
