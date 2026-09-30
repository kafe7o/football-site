"""
Сигурна и рискова прогноза по коефициент (указание на професионалиста, 2026-09-30 вечерта).

Той казва: „рисковата не може да е 1.48 - рисковата е от 2.50 нагоре“; „сигурната - между 1.40 и
1.80; всичко над 1.80 е рисково, до 1.80 всичко е сигурно“; „за мен рискова е над 1.80, сигурна -
под 1.70“; „сигурната да познава средно 70%“; да не се пращат мачове под 1.50.

ПРОТОКОЛ (записан преди пускането). Данни: backtest_tips (роботът назад, walk-forward, само мачовете
с модел и със средни коефициенти), 2023-07-01 до днес. Кое събитие - по шанса на РОБОТА (както от
30.09: процентът не идва от коефициентите); коефициентът само определя дали е сигурно или рисково.

По-сигурна: най-вероятното по робота събитие с коефициент в диапазона - кандидати 1, 2, 1X, X2,
12, над/под 2.5 (само те имат коефициенти в историята). Варианти на диапазона:
  S1 1.40-1.80 (казаното); S2 1.40-1.70 („за мен лично“); S3 1.50-1.80 („не под 1.50“).
  И за всеки - с допълнително условие шансът по робота да е поне 65% и поне 70% (за целта „70%“).
Рискова: знакът 1, X или 2 с коефициент над границата, избран като досега - най-много над
обичайното за лигата. Варианти: R1 над 1.80, R2 от 2.50 нагоре.
Мери се: за колко мача има прогноза, колко често излиза, среден коефициент, доход (за сведение).
Изборът е на собственика; тук е цената на всеки вариант.
"""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, robot                          # noqa: E402
from research.signs_backtest import base_rates      # noqa: E402

OUT = ROOT / "data" / "ranges_backtest.json"
SAFE_CANDS = ["1", "2", "1X", "X2", "12", "O", "U"]


def stat(items, total):
    n = len(items)
    if not n:
        return {"n": 0, "cover": 0}
    h = sum(1 for x, _ in items if x)
    pr = [(o - 1) if x else -1 for x, o in items]
    mu = sum(pr) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in pr) / max(1, n - 1))
    return {"n": n, "cover": n / total, "hit": h / n, "odds": sum(o for _, o in items) / n,
            "roi": mu, "roi_se": sd / math.sqrt(n)}


def main():
    conn = db.init()
    base = base_rates(conn)
    rows = []
    for r in conn.execute("SELECT league, date, probs_json, prices_json, hg, ag FROM backtest_tips "
                          "WHERE basis = 'model' AND prices_json IS NOT NULL"):
        if r["league"] in base:
            rows.append((r, json.loads(r["probs_json"])["robot"], (json.loads(r["prices_json"]) or {}).get("avg", {})))
    total = len(rows)
    out = {"matches": total, "safe": {}, "risky": {}}
    print(f"мачове с модел и коефициенти: {total}\n\nПО-СИГУРНА (най-вероятното по робота в диапазона):")
    for name, lo, hi in (("S1 1.40-1.80", 1.40, 1.80), ("S2 1.40-1.70", 1.40, 1.70), ("S3 1.50-1.80", 1.50, 1.80)):
        for floor in (0.0, 0.65, 0.70):
            items = []
            for r, p, avg in rows:
                c = [(s, p[s], avg[s]) for s in SAFE_CANDS if avg.get(s) and lo <= avg[s] <= hi and p.get(s, 0) >= floor]
                if c:
                    s, _, o = max(c, key=lambda x: x[1])
                    items.append((robot.hit(s, r["hg"], r["ag"]), o))
            st = stat(items, total)
            key = f"{name}, шанс по робота >= {floor:.0%}" if floor else name
            out["safe"][key] = st
            print(f"  {key:<36} мачове {st['n']:>6} ({st['cover']:.0%}) | познати {st.get('hit', 0):.1%} | ср.к {st.get('odds', 0):.2f} | доход {st.get('roi', 0):+.1%} ± {st.get('roi_se', 0):.1%}")
    print("\nРИСКОВА (знакът над границата, най-много над обичайното за лигата):")
    for name, lo, strict in (("R1 над 1.80", 1.80, True), ("R2 от 2.50", 2.50, False)):
        items, xs = [], []
        for r, p, avg in rows:
            b = base[r["league"]]
            c = [s for s in ("1", "X", "2") if avg.get(s) and (avg[s] > lo if strict else avg[s] >= lo)]
            if c:
                s = max(c, key=lambda k: p[k] / b[k])
                items.append((robot.hit(s, r["hg"], r["ag"]), avg[s]))
                if s == "X":
                    xs.append((robot.hit(s, r["hg"], r["ag"]), avg[s]))
        st, sx = stat(items, total), stat(xs, total)
        out["risky"][name] = {**st, "x": sx}
        print(f"  {name:<14} мачове {st['n']:>6} ({st['cover']:.0%}) | познати {st['hit']:.1%} | ср.к {st['odds']:.2f} | доход {st['roi']:+.1%} ± {st['roi_se']:.1%}"
              f" | X: {sx['n']} ({sx['n'] / st['n']:.0%}), познати {sx.get('hit', 0):.1%}")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    main()
