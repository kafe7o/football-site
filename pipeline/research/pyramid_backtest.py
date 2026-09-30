"""
Новаците: моделът да се учи и от съседната дивизия (2026-10-01).

Проблемът (намерен при показния бонус анализ): моделът се учи поотделно за всяко първенство. Новак в
Бундеслигата (Elversberg, Paderborn) има там 5-6 мача - под прага от 10 претеглени мача - и роботът
няма оценка за мачовете му. В топ 5 това са 2-3 отбора на лига, ~15% от мачовете в началото на сезона.

Идеята: моделът за първата дивизия се учи на мачовете и на първа, и на втора дивизия заедно. Новакът
носи силата си от втора дивизия; отборите, които са минали от едната в другата, свързват двете скали.
Същият модел (bets/model.py, reg=1.0, затихване 180 дни), същото смесване с xG.

ПРОТОКОЛ (записан преди пускането):
Лиги и с какво се учат:  E0+E1, SP1+SP2, I1+I2, D1+D2, F1+F2 (топ 5); E1+E0+E2, D2+D1+D3 (вторите
с двата съседа, където има данни). Walk-forward по месеци 2023-07 до днес, обучение на последните 4
години преди 1-во число - точно като research/robot_backtest.py (сегашният модел е записан там).
Сравнение на едни и същи мачове (Brier за 1/X/2, по-ниско е по-добре):
  A) мачовете, за които и двата модела дават прогноза - сдвоена разлика ± грешка;
  B) мачовете, за които само новият дава прогноза (новаците) - Brier срещу обичайното за лигата
     (честотата на 1/X/2 в 4-те години преди - прогноза „без модел“) и срещу пазара.
Приема се, ако в А новият НЕ е по-лош (разлика под +2 грешки) и в избора (2023/24-2024/25), и в
чистата проверка (от 2025-07), И в Б е по-добър от обичайното за лигата. Иначе не влиза.
Правилата на робота (рискова/по-сигурна) не се пипат - сменя се само откъде идват шансовете.
Резултатът: data/pyramid_backtest.json.
"""

import json
import math
import sys
from datetime import date
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, model, results, xg                    # noqa: E402
from bets.market import implied_row                        # noqa: E402

START, SELECT_END = "2023-07-01", "2025-07-01"
PYRAMID = {"E0": ["E0", "E1"], "SP1": ["SP1", "SP2"], "I1": ["I1", "I2"], "D1": ["D1", "D2"], "F1": ["F1", "F2"],
           "E1": ["E0", "E1", "E2"], "D2": ["D1", "D2", "D3"]}
OUT = ROOT / "data" / "pyramid_backtest.json"


def months(first, last):
    y, m = int(first[:4]), int(first[5:7])
    while f"{y:04d}-{m:02d}-01" <= last:
        yield f"{y:04d}-{m:02d}-01"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def combined_history(conn, codes):
    parts = [results.history(conn, c, blend_xg=c in xg.LEAGUES) for c in codes]
    df = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    return df.sort_values("date").reset_index(drop=True)


def brier(p, res):
    return sum((p[i] - (1.0 if i == res else 0.0)) ** 2 for i in range(3))


def run_league(code):
    conn = db.init()
    hist_own = results.history(conn, code, blend_xg=code in xg.LEAGUES)
    hist_all = combined_history(conn, PYRAMID[code])
    rows = conn.execute("""SELECT m.id, m.date, m.home_team, m.away_team, m.fthg, m.ftag, o.odds_home, o.odds_draw, o.odds_away
                             FROM matches m LEFT JOIN odds o ON o.match_id = m.id AND o.bookmaker = 'AVG'
                            WHERE m.league = ? AND m.fthg IS NOT NULL AND m.date >= ? ORDER BY m.date""",
                        (code, START)).fetchall()
    out = []
    for m0 in months(START, date.today().isoformat()):
        y, mo = int(m0[:4]), int(m0[5:7])
        m1 = f"{y + (mo == 12):04d}-{(mo % 12) + 1:02d}-01"
        month = [r for r in rows if m0 <= r["date"] < m1]
        if not month:
            continue
        since = f"{y - 4}-{m0[5:]}"
        base_rows = hist_own[(hist_own["date"] < m0) & (hist_own["date"] >= since)]
        base = [float((base_rows["fthg"] > base_rows["ftag"]).mean()), float((base_rows["fthg"] == base_rows["ftag"]).mean()),
                float((base_rows["fthg"] < base_rows["ftag"]).mean())] if len(base_rows) else [0.44, 0.26, 0.30]
        fits = {}
        for name, h in (("own", hist_own), ("all", hist_all)):
            train = h[(h["date"] < m0) & (h["date"] >= since)]
            fits[name] = model.Poisson().fit(train, as_of=pd.Timestamp(m0)) if len(train) >= model.MIN_TRAIN_MATCHES else None
        for r in month:
            res = 0 if r["fthg"] > r["ftag"] else (1 if r["fthg"] == r["ftag"] else 2)
            p_own = fits["own"].probabilities(r["home_team"], r["away_team"]) if fits["own"] else None
            p_all = fits["all"].probabilities(r["home_team"], r["away_team"]) if fits["all"] else None
            mk = implied_row([r["odds_home"], r["odds_draw"], r["odds_away"]]) if r["odds_home"] else None
            out.append({"league": code, "date": r["date"], "res": res, "own": p_own, "all": p_all, "base": base, "market": mk})
    conn.close()
    print(f"{code}: {len(out)} мача", flush=True)
    return out


def paired(rows, a, b):
    d = [brier(r[b], r["res"]) - brier(r[a], r["res"]) for r in rows]
    n = len(d)
    if n < 2:
        return None
    mu = sum(d) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in d) / (n - 1))
    return {"n": n, "diff": mu, "se": sd / math.sqrt(n), "t": mu / (sd / math.sqrt(n)) if sd else 0.0,
            a: sum(brier(r[a], r["res"]) for r in rows) / n, b: sum(brier(r[b], r["res"]) for r in rows) / n}


def main(codes=None):
    codes = list(codes or PYRAMID)
    with Pool(4) as pool:
        rows = [r for part in pool.map(run_league, codes) for r in part]
    out = {"generated": date.today().isoformat(), "periods": {}}
    for period, keep in (("select", lambda r: r["date"] < SELECT_END), ("clean", lambda r: r["date"] >= SELECT_END),
                         ("all", lambda r: True)):
        rs = [r for r in rows if keep(r)]
        both = [r for r in rs if r["own"] and r["all"]]
        only_new = [r for r in rs if r["all"] and not r["own"]]
        only_new_mk = [r for r in only_new if r["market"]]
        out["periods"][period] = {
            "matches": len(rs), "own_covered": sum(1 for r in rs if r["own"]), "all_covered": sum(1 for r in rs if r["all"]),
            "A_both": paired(both, "own", "all"),
            "B_new_vs_base": paired(only_new, "base", "all"),
            "B_new_vs_market": paired(only_new_mk, "market", "all")}
    a_ok = all(out["periods"][p]["A_both"] and out["periods"][p]["A_both"]["t"] < 2 for p in ("select", "clean"))
    b = out["periods"]["all"]["B_new_vs_base"]
    b_ok = bool(b and b["diff"] < 0)
    out["accepted"] = a_ok and b_ok
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for period, v in out["periods"].items():
        a, bb, bm = v["A_both"], v["B_new_vs_base"], v["B_new_vs_market"]
        print(f"\n{period}: мачове {v['matches']} | прогноза сега {v['own_covered']} ({v['own_covered'] / v['matches']:.1%}) | "
              f"с новия {v['all_covered']} ({v['all_covered'] / v['matches']:.1%})")
        if a:
            print(f"  А общите {a['n']}: сега {a['own']:.4f}, новият {a['all']:.4f}, разлика {a['diff']:+.4f} (t {a['t']:+.1f})")
        if bb:
            print(f"  Б новаците {bb['n']}: новият {bb['all']:.4f} срещу обичайното {bb['base']:.4f} (t {bb['t']:+.1f})"
                  + (f"; срещу пазара {bm['market']:.4f} ({bm['n']} с цени)" if bm else ""))
    print("\nПРИЕТО" if out["accepted"] else "\nНЕ Е ПРИЕТО")
    return out


if __name__ == "__main__":
    main(sys.argv[1:] or None)
