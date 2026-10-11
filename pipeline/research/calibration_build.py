"""
Таблицата за поправка на шансовете (за колонките на сянка, bets/shadow.py) - от walk-forward прогнозите назад (backtest_tips).

За всеки вид избор (1/X/2, двоен шанс, над/под 1.5 / 2.5 / 3.5, двата вкарват, ...) се пази сборът на (казано - излязло) и броят на едните прогнози на робота. Живо: p' = p - сбор / (n + 50)
(свиване към 0 при малко мачове) - същото като слой L1 в research/online_learning_backtest.py (там е измерено: казаното 66.7% срещу излязло 64.5% се поправя, колонките по 3
минават 34-35% вместо 29%, но подборът се мести към избори без цена от букмейкър - затова е само на сянка, не в правилата).
Месечно в седмичния анализ (run.py weekly, стъпка 4е). Резултатът: data/calibration.json.
"""

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import robot                                         # noqa: E402
from bets.shadow import kind                                    # noqa: E402
from research import columns_backtest as cb                     # noqa: E402

OUT = ROOT / "data" / "calibration.json"
K = 50


def main():
    days = cb.load()
    kinds = defaultdict(lambda: [0.0, 0])
    first = last = None
    for day in sorted(days):
        for m in days[day]:
            one = m["one"]
            hit = robot.hit_any(one["sel"], m["hg"], m["ag"])
            if hit is None:
                continue
            k = kind(one["sel"])
            kinds[k][0] += one["p"] - float(bool(hit))
            kinds[k][1] += 1
            first, last = first or day, day
    out = {"generated": date.today().isoformat(), "k": K, "period": [first, last], "n": sum(v[1] for v in kinds.values()),
           "kinds": {k: {"sum": round(v[0], 4), "n": v[1], "bias": round(v[0] / (v[1] + K), 4)} for k, v in sorted(kinds.items())}}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in out["kinds"].items():
        print(f"  {k:7} n={v['n']:>6}  поправка {v['bias']:+.3f}")
    print("записано:", OUT)
    return out


if __name__ == "__main__":
    main()
