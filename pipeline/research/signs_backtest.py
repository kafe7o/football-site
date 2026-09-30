"""
Рискова и по-сигурна прогноза за всеки мач (указание на професионалиста, 2026-09-30).

Той казва: „като дава прогнози, да не се съобразява с коефициенти и фаворити“; „да използва и
трите знака - странно е, че няма X, а само двоен шанс“; „за всеки мач - рискова прогноза (напр. X)
и по-сигурна (напр. 1X, под 2.5, над 3.5 картона - каквото вижда като събития в мача)“; „шансът
за X е 33% на всеки мач“.

ПРОТОКОЛ (записан преди пускането). Данни: backtest_tips (роботът назад, walk-forward, само
мачовете с модел), 2023-07-01 до днес; истинските резултати; средните коефициенти само за да се
види доходът - в избора не участват.

Рискова прогноза - един знак 1, X или 2, без коефициенти:
  A „най-вероятният“: знакът с най-голям шанс по робота (досегашното). Почти никога X - в
    мачовете рядко равенството е по-вероятно от победата на по-силния.
  B „спрямо обичайното“: знакът, чийто шанс по робота е най-много НАД обичайния за лигата
    (честотата на 1, X и 2 в лигата за 4-те години преди 2023-07-01 - без поглед в бъдещето).
    Фаворитът не получава предимство само защото е фаворит: X се избира, когато мачът е по-равен и
    по-беден на голове от обикновено.
Изборът е на собственика (указанието); тук се мери цената: колко често излиза всеки знак, колко
често се избира X, доход на средния коефициент (само за сведение).

По-сигурна прогноза - най-вероятното събитие по робота измежду: двойният шанс, който съдържа
рисковия знак (за X - по-вероятният от 1X и X2), над/под 1.5, 2.5 и 3.5 гола, двата вкарват да/не.
(Картоните и корнерите влизат на живо - в тази таблица назад шансовете им не са записани.)
Добавено след първото пускане (същия ден): 1.5 и 3.5 гола - в първото пускане по-сигурната беше
двоен шанс в 90% от мачовете, а професионалистът иска „всякакви събития“.

Резултатът: data/signs_backtest.json.
"""

import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, robot                  # noqa: E402

OUT = ROOT / "data" / "signs_backtest.json"
SELECT_END = "2025-07-01"


def base_rates(conn, before="2023-07-01", years=4):
    """Честотата на 1, X, 2 по лиги в годините преди before."""
    since = f"{int(before[:4]) - years}{before[4:]}"
    out = {}
    for lg, n, h, d, a in conn.execute(
            """SELECT league, COUNT(*), AVG(fthg > ftag), AVG(fthg = ftag), AVG(fthg < ftag) FROM matches
                WHERE fthg IS NOT NULL AND date >= ? AND date < ? GROUP BY league""", (since, before)):
        if n >= 200:
            out[lg] = {"1": h, "X": d, "2": a}
    return out


def sign_argmax(p, base):
    return max(("1", "X", "2"), key=lambda s: p[s])


def sign_lift(p, base):
    return max(("1", "X", "2"), key=lambda s: p[s] / base[s])


def goal_lines(p):
    """Шансът за над 1.5 и над 3.5 от очакваните голове на робота."""
    from bets import model
    import numpy as np
    grid = model.score_grid(p["xg_home"], p["xg_away"])
    k = np.arange(grid.shape[0])
    total = k[:, None] + k[None, :]
    return float(grid[total >= 2].sum()), float(grid[total >= 4].sum())


def safer(p, sign):
    """Най-вероятното събитие: двойният шанс с рисковия знак, над/под 1.5/2.5/3.5, двата вкарват да/не."""
    dc = {"1": "1X", "2": "X2", "X": max(("1X", "X2"), key=lambda s: p[s])}[sign]
    cands = [(dc, p[dc]), ("O" if p["O"] >= p["U"] else "U", max(p["O"], p["U"]))]
    if p.get("xg_home") is not None:
        o15, o35 = goal_lines(p)
        cands += [("O15" if o15 >= 0.5 else "U15", max(o15, 1 - o15)), ("O35" if o35 >= 0.5 else "U35", max(o35, 1 - o35))]
    if p.get("GG") is not None:
        cands.append(("GG" if p["GG"] >= 0.5 else "NG", max(p["GG"], 1 - p["GG"])))
    return max(cands, key=lambda x: x[1])


def hit(sel, hg, ag):
    if sel in ("O15", "U15", "O35", "U35"):
        line = 1.5 if "15" in sel else 3.5
        return (hg + ag > line) == sel.startswith("O")
    if sel == "GG":
        return hg > 0 and ag > 0
    if sel == "NG":
        return hg == 0 or ag == 0
    return robot.hit(sel, hg, ag)


def stat(items):
    n = len(items)
    if not n:
        return None
    h = sum(1 for x, _ in items if x)
    priced = [(x, o) for x, o in items if o]
    out = {"n": n, "hit": h / n, "hit_se": math.sqrt(h / n * (1 - h / n) / n)}
    if priced:
        pr = [(o - 1) if x else -1 for x, o in priced]
        mu = sum(pr) / len(pr)
        sd = math.sqrt(sum((v - mu) ** 2 for v in pr) / max(1, len(pr) - 1))
        out.update({"roi": mu, "roi_se": sd / math.sqrt(len(pr)), "n_priced": len(pr),
                    "odds": sum(o for _, o in priced) / len(priced)})
    return out


def main():
    conn = db.init()
    base = base_rates(conn)
    rows = conn.execute("SELECT league, date, basis, probs_json, prices_json, hg, ag FROM backtest_tips WHERE basis = 'model'").fetchall()
    res = {}
    per_league = {}
    for name, fn in (("argmax", sign_argmax), ("lift", sign_lift)):
        per = {"select": [], "clean": []}
        by_sign = {s: [] for s in "1X2"}
        safe = {"select": [], "clean": []}
        safe_kind = Counter()
        count = Counter()
        for r in rows:
            if r["league"] not in base:
                continue
            p = json.loads(r["probs_json"])["robot"]
            avg = (json.loads(r["prices_json"]) or {}).get("avg", {}) if r["prices_json"] else {}
            s = fn(p, base[r["league"]])
            count[s] += 1
            period = "select" if r["date"] < SELECT_END else "clean"
            ok = robot.hit(s, r["hg"], r["ag"])
            per[period].append((ok, avg.get(s)))
            by_sign[s].append((ok, avg.get(s)))
            sel, prob = safer(p, s)
            safe_kind[sel if sel not in ("1X", "X2") else "двоен шанс"] += 1
            if name == "lift":
                lg = per_league.setdefault(r["league"], {"risky": [], "safer": [], "x": []})
                lg["risky"].append((ok, avg.get(s)))
                lg["safer"].append((hit(sel, r["hg"], r["ag"]), avg.get(sel)))
                if s == "X":
                    lg["x"].append((ok, avg.get(s)))
            safe[period].append((hit(sel, r["hg"], r["ag"]), avg.get(sel)))
        n = sum(count.values())
        res[name] = {"share": {s: count[s] / n for s in "1X2"},
                     "risky": {k: stat(v) for k, v in per.items()}, "by_sign": {s: stat(v) for s, v in by_sign.items()},
                     "safer": {k: stat(v) for k, v in safe.items()},
                     "safer_kinds": {k: v / n for k, v in safe_kind.items()}}
        print(f"\n{name}: знаци 1 {count['1'] / n:.0%} | X {count['X'] / n:.0%} | 2 {count['2'] / n:.0%}  ({n} мача)")
        for k in ("select", "clean"):
            x = res[name]["risky"][k]
            print(f"  рискова {k:<6} {x['n']:>6} | познати {x['hit']:.1%} | ср.к {x.get('odds', 0):.2f} | доход {x.get('roi', 0):+.1%} ± {x.get('roi_se', 0):.1%}")
        for s in "1X2":
            x = res[name]["by_sign"][s]
            if x:
                print(f"    знак {s}: {x['n']:>6} | познати {x['hit']:.1%} | ср.к {x.get('odds', 0):.2f} | доход {x.get('roi', 0):+.1%}")
        for k in ("select", "clean"):
            x = res[name]["safer"][k]
            print(f"  по-сигурна {k:<6} {x['n']:>6} | познати {x['hit']:.1%} | доход {x.get('roi', 0):+.1%} (на {x.get('n_priced', 0)} с цена)")
        print("  по-сигурната е:", {k: f"{v:.0%}" for k, v in res[name]["safer_kinds"].items()})
    res["lift"]["leagues"] = {lg: {k: stat(v) for k, v in d.items()} for lg, d in per_league.items()}
    OUT.write_text(json.dumps({"base_before": "2023-07-01", "results": res,
                               "base": {k: {s: round(v, 4) for s, v in b.items()} for k, b in base.items()}},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    main()
