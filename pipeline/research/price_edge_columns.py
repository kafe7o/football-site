"""
Колонки от мачове с ЦЕНОВИ РЪБ (продължение на research/price_edge_backtest.py, 2026-10-13). Логиката: колонката връща произведението на мачовете - мачове на минус
дават колонка на минус, но мачове на ПЛЮС (коефициент над честната цена на Pinnacle) би трябвало да дадат колонка на плюс.
ПРОТОКОЛ (записан преди пускането): мачове 1/X/2 и над/под 2.5 с ръб >= {2, 3, 5%} спрямо Pinnacle без маржа, плаща се MAX (счупени цени - както в price_edge_backtest),
коефициент до {2, 3}; всеки ден - най-големият ръб първи, един мач от първенство и един избор на мач; колонки по {1, 2, 3} мача, до {1, 2} на ден.
Периоди А 2012-07..2019-06 (избор), Б 2019-07..2023-06, В 2023-07..2026-09 (Pinnacle до 01.2026). Описателно - колко се умножава ръбът и колко често минава.
"""
import math, sys
from itertools import product
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from research import price_edge_backtest as pe   # noqa: E402

def main():
    df = pe.load()
    rows = []
    for mk in ("1x2", "ou"):
        R = pe.market_rows(df, mk)
        R["mid"] = np.tile(np.arange(len(df)), len(R) // len(df)) + (0 if mk == "1x2" else 10_000_000)
        rows.append(R)
    R = pd.concat(rows, ignore_index=True)
    R = R[R["MAX"].notna() & R["f_PS"].notna() & (R["MAX"] <= 50) & (R["inv_max"] >= 0.95) & (R["MAX"] <= R["AVG"] * 1.25)]
    R["edge"] = R["MAX"] * R["f_PS"] - 1
    R["mkey"] = R["date"] + "|" + R["league"] + "|" + (R["mid"] % 10_000_000).astype(str)
    print(f"{'вариант':34} | {'А':>26} | {'Б':>26} | {'В':>26}")
    for thr, band, size, per_day in product((0.02, 0.03, 0.05), (2.0, 3.0), (1, 2, 3), (1, 2)):
        x = R[(R["edge"] >= thr) & (R["MAX"] <= band)].sort_values(["date", "edge"], ascending=[True, False])
        x = x.drop_duplicates("mkey")
        out = {}
        for name, a, b in pe.PERIODS:
            y = x[(x["date"] >= a) & (x["date"] < b)]
            items = []
            for d, g in y.groupby("date"):
                used_lg, legs = set(), []
                for r in g.itertuples():
                    if r.league in used_lg:
                        continue
                    legs.append(r); used_lg.add(r.league)
                for k in range(per_day):
                    col = legs[k * size:(k + 1) * size]
                    if len(col) < size:
                        break
                    items.append((all(c.hit for c in col), math.prod(c.MAX for c in col)))
            n = len(items)
            if n:
                pay = np.array([o if h else 0.0 for h, o in items])
                out[name] = f"{np.mean([h for h, _ in items]):>4.0%} {pay.mean() - 1:+6.1%} ±{pay.std(ddof=1) / math.sqrt(n):.1%} n{n:<5}"
            else:
                out[name] = "-"
        print(f"ръб>={thr:.0%} до {band:.0f} по {size} до {per_day}/ден | " + " | ".join(f"{out[p]:>26}" for p in ("А", "Б", "В")))

if __name__ == "__main__":
    main()
