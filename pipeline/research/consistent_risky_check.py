"""
Рисковата да не противоречи на по-сигурната (професионалистът, 2026-10-02).

Той казва: „няма как да ми дава сигурна прогноза 1X, рискова прогноза 2 - той си противоречи сам на себе си
тоя алгоритъм. Може примерно сигурна прогноза 1X, рискова прогноза чисто едно. Когато ти дава едното 1X,
другото 2, нещата се припокриват.“ Собственикът: „всичко да е по негова логика и както е досега“.

Правилото: рисковата (знак 1, X или 2 от 2.50 нагоре, най-много над обичайното за лигата - както досега) се
избира САМО измежду знаците, които по-сигурната и едната прогноза позволяват: 1X -> 1 или X; X2 -> X или 2;
12 -> 1 или 2; 1 / хендикап на домакина -> 1; 2 / хендикап на госта -> 2. Голове, картони, корнери - не
ограничават. Ако няма такъв знак от 2.50 нагоре - рискова прогноза няма.

ПРОТОКОЛ (записан преди пускането): тук не се избира - собственикът реши („както каза майсторът“); мери се
цената. Данни: backtest_tips (walk-forward, само с модел), средните коефициенти, 2023-07 до днес; по-сигурната
- bets/robot.safe_by_odds; едната прогноза - най-вероятното в 1.50-1.80 от головете, двойния шанс, 1/X/2 и
хендикапите (без картоните и корнерите - за тях назад няма шансове в тази таблица; те и не ограничават).
Мери се: в колко мача има рискова, колко често излиза, среден коефициент, доход - сега и с правилото; колко
често сега рисковата противоречи. Резултатът: data/consistent_risky.json.

РЕЗУЛТАТ (2026-10-02; избор / чиста): сега рисковата противоречи в 18% / 17% от мачовете. С правилото рискова
има в 85% от мачовете; познава 26.9% / 27.1% (сега 26.5% / 26.6%), ср. к 3.67 / 3.58, доход -6.6% / -7.3%
(сега -6.5% / -7.1%) - практически същото. Приложено от записа на 03.10 (07:00).
"""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, robot                             # noqa: E402
from research.signs_backtest import base_rates         # noqa: E402

OUT = ROOT / "data" / "consistent_risky.json"
SELECT_END = "2025-07-01"


def one_simple(p, avg):
    """Едната прогноза без картоните и корнерите (за ограничението те не значат)."""
    ev = {k: v for k, v in robot.one_events(p, None).items() if k not in ("KH", "KA")}
    cands = []
    for s, prob in ev.items():
        book = s in robot.SELECTIONS and bool(avg.get(s))
        odds = avg[s] if book else (1 / prob if prob > 0 else 99.0)
        cands.append((s, prob, odds))
    band = [c for c in cands if robot.SAFE_RANGE[0] <= c[2] <= robot.SAFE_RANGE[1] and c[1] >= 0.5]
    pool = band or [c for c in cands if c[2] >= robot.RISKY_FROM]
    return max(pool, key=lambda c: c[1])[0] if pool else None


def stat(items, total):
    n = len(items)
    if not n:
        return {"n": 0}
    h = sum(1 for x, _ in items if x)
    pr = [(o - 1) if x else -1 for x, o in items]
    mu = sum(pr) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in pr) / max(1, n - 1))
    return {"n": n, "cover": n / total, "hit": h / n, "odds": sum(o for _, o in items) / n, "roi": mu, "roi_se": sd / math.sqrt(n)}


def main():
    conn = db.init()
    base = base_rates(conn)
    res = {k: {"select": [], "clean": []} for k in ("now", "rule")}
    total = {"select": 0, "clean": 0}
    conflicts = {"select": 0, "clean": 0}
    for r in conn.execute("SELECT league, date, probs_json, prices_json, hg, ag FROM backtest_tips "
                          "WHERE basis = 'model' AND prices_json IS NOT NULL"):
        if r["league"] not in base:
            continue
        p = json.loads(r["probs_json"])["robot"]
        if p.get("xg_home") is None:
            continue
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {})
        if not avg.get("1"):
            continue
        period = "select" if r["date"] < SELECT_END else "clean"
        total[period] += 1
        b = base[r["league"]]
        safer = robot.safe_by_odds(p, avg)
        one = one_simple(p, avg)
        against = [x for x in ((safer or {}).get("sel"), one) if x]
        now = robot.risky_by_odds(p, b, avg)
        rule = robot.risky_by_odds(p, b, avg, against)
        if now and rule != now:
            conflicts[period] += 1
        for key, x in (("now", now), ("rule", rule)):
            if x and x.get("src") == "book":
                res[key][period].append((robot.hit(x["sel"], r["hg"], r["ag"]), x["odds"]))
    out = {"total": total, "conflicts": conflicts,
           "risky": {k: {p: stat(v, total[p]) for p, v in d.items()} for k, d in res.items()}}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for p in ("select", "clean"):
        print(f"{p}: мачове {total[p]}, рисковата сега противоречи в {conflicts[p]} ({conflicts[p] / total[p]:.0%})")
        for k, name in (("now", "сега"), ("rule", "без противоречие")):
            s = out["risky"][k][p]
            print(f"   {name:<17} рискова в {s['cover']:.0%} от мачовете | познати {s['hit']:.1%} | ср.к {s['odds']:.2f} | доход {s['roi']:+.1%} ± {s['roi_se']:.1%}")
    return out


if __name__ == "__main__":
    main()
