"""
Сигурна и рискова без ленти по коефициент, всичко от 1.40 (професионалистът, 2026-10-02).

Той казва: „другият вариант е - да не слагаме ограничения в коефициентите, само да ги разделя на сигурна и на
рискова. Обаче да е поне от 1.40 нагоре цялостно - да не ми дава мачове на 1.20 и да казва „много са сигурни“, и
в следващия момент рискова на 1.50 (рискова на 1.50 - по-добре да играя обратния знак).“ И: „рисковата да не е
само X - не искам да ми познава 15 хикса, нека да познава знаци.“

Превод в правило:
  НИЩО под 1.40 (коефициентът на букмейкъра, иначе честният на робота 1/шанс).
  По-сигурна = най-вероятното по робота от 1, 2, 1X, X2, 12, над/под 2.5, с шанс поне 50% (без горна граница).
  Едната прогноза = най-вероятното по робота от всички събития, с шанс поне 50% (без горна граница); ако няма -
  най-вероятното от 1.40 нагоре.
  Рискова = ЗНАК 1 или 2 (не X), който роботът дава ПОД 50% - тоест наистина рисков (не 1.50) - по-вероятният от
  двата. Ако единият отбор е над 50% (ясен фаворит):
    R1 - рискова няма;
    R2 - рисковата е другият отбор (по-слабият).

ПРОТОКОЛ (записан преди пускането): данни backtest_tips (walk-forward, с модел, средните коефициенти), 2023-07 до
днес; избор до 2025-07-01, чиста от там. По-сигурната и едната прогноза - както той каза (не се избира, мери се
цената срещу сегашните ленти 1.50-1.80 / от 2.50). Рисковата: R1 или R2 - приема се тази с по-висок доход в
избора; потвърждава се, ако в чистата не е с повече от 2 грешки под другата; иначе - по-простата R1. За сравнение
- сегашната (знак от 2.50, най-много над обичайното за лигата). Едната прогноза тук е без картоните и корнерите
(за тях в таблицата няма шансове назад). Резултатът: data/master_split.json.

РЕЗУЛТАТ (2026-10-02; избор / чиста): по-сигурна от 1.40 - 57.9% / 57.0% в 98% от мачовете (сега 57.8% / 56.6% в
77%), доход -5.4% / -7.6%; едната прогноза от 1.40 - 63.9% / 63.7% (сега 56.8%); рискова R1 - 41.0% / 42.1% в 56%
от мачовете, ср. к 2.35 / 2.31, доход -7.5% / -6.6%, X 0% (сега 26.5% / 26.6%, X 73%, -6.5% / -7.1%); R2 - 31.1% /
31.9%, доход -10.7% / -11.8% -> по протокола R1. С картоните/корнерите и хендикапа (one_pick_backtest): A 63.7% /
63.5%, A2 (без картони и корнери) 64.0% / 63.7% - в рамките на една грешка; на живо остава A (професионалистът
иска картоните и корнерите в базата).
"""

import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, robot                             # noqa: E402
from research.signs_backtest import base_rates         # noqa: E402

OUT = ROOT / "data" / "master_split.json"
SELECT_END = "2025-07-01"
FLOOR = 1.40
SAFE_SELS = ("1", "2", "1X", "X2", "12", "O", "U")


def price(sel, p, avg):
    if avg.get(sel):
        return avg[sel], True
    return (1 / p[sel] if p.get(sel) else None), False


def safe_new(p, avg):
    c = [(s, p[s]) + price(s, p, avg) for s in SAFE_SELS if p.get(s) is not None]
    c = [x for x in c if x[2] and x[2] >= FLOOR and x[1] >= 0.5]
    return max(c, key=lambda x: x[1]) if c else None


def one_new(p, avg):
    ev = {k: v for k, v in robot.one_events(p, None).items() if k not in ("KH", "KA")}
    c = []
    for s, prob in ev.items():
        book = s in robot.SELECTIONS and bool(avg.get(s))
        odds = avg[s] if book else (1 / prob if prob > 0 else 99.0)
        if odds >= FLOOR:
            c.append((s, prob, odds, book))
    top = [x for x in c if x[1] >= 0.5]
    pool = top or c
    return max(pool, key=lambda x: x[1]) if pool else None


def risky_new(p, avg, variant):
    under = [s for s in ("1", "2") if p[s] < 0.5]
    if len(under) == 2:
        s = max(under, key=lambda k: p[k])
    elif variant == "R2" and under:
        s = under[0]                        # ясен фаворит - рисковата е другият отбор
    else:
        return None
    odds, book = price(s, p, avg)
    if not odds or odds < FLOOR:
        return None
    return (s, p[s], odds, book)


def stat(items, total):
    n = len(items)
    if not n:
        return {"n": 0}
    h = sum(1 for x in items if x["hit"])
    booked = [x for x in items if x["book"]]
    out = {"n": n, "cover": n / total, "hit": h / n, "said": sum(x["p"] for x in items) / n,
           "odds": sum(x["odds"] for x in items) / n, "x_share": sum(1 for x in items if x["sel"] == "X") / n}
    if booked:
        pr = [(x["odds"] - 1) if x["hit"] else -1 for x in booked]
        mu = sum(pr) / len(pr)
        sd = math.sqrt(sum((v - mu) ** 2 for v in pr) / max(1, len(pr) - 1))
        out.update({"n_book": len(booked), "roi": mu, "roi_se": sd / math.sqrt(len(pr))})
    out["signs"] = dict(Counter(x["sel"] for x in items).most_common(6))
    return out


def main():
    conn = db.init()
    base = base_rates(conn)
    names = ("safe_now", "safe_new", "one_new", "risky_now", "R1", "R2")
    res = {k: {"select": [], "clean": []} for k in names}
    total = {"select": 0, "clean": 0}
    for r in conn.execute("SELECT league, date, probs_json, prices_json, hg, ag FROM backtest_tips "
                          "WHERE basis = 'model' AND prices_json IS NOT NULL"):
        if r["league"] not in base:
            continue
        p = json.loads(r["probs_json"])["robot"]
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {})
        if p.get("xg_home") is None or not avg.get("1"):
            continue
        period = "select" if r["date"] < SELECT_END else "clean"
        total[period] += 1
        picks = {}
        s = robot.safe_by_odds(p, avg)
        picks["safe_now"] = (s["sel"], s["p"], s["odds"], s["src"] == "book") if s else None
        picks["safe_new"] = safe_new(p, avg)
        picks["one_new"] = one_new(p, avg)
        rk = robot.risky_by_odds(p, base[r["league"]], avg)
        picks["risky_now"] = (rk["sel"], rk["p"], rk["odds"], rk["src"] == "book") if rk else None
        picks["R1"] = risky_new(p, avg, "R1")
        picks["R2"] = risky_new(p, avg, "R2")
        for k, x in picks.items():
            if x:
                res[k][period].append({"sel": x[0], "p": x[1], "odds": x[2], "book": x[3],
                                       "hit": bool(robot.hit_any(x[0], r["hg"], r["ag"]))})
    out = {"total": total, "results": {k: {p: stat(v, total[p]) for p, v in d.items()} for k, d in res.items()}}
    R = out["results"]
    best = "R2" if R["R2"]["select"].get("roi", -9) > R["R1"]["select"].get("roi", -9) else "R1"
    other = "R1" if best == "R2" else "R2"
    gap = R[other]["clean"]["roi"] - R[best]["clean"]["roi"]
    se = math.hypot(R[other]["clean"]["roi_se"], R[best]["clean"]["roi_se"])
    out["risky_chosen"] = best if gap <= 2 * se else "R1"
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k in names:
        for per in ("select", "clean"):
            s = R[k][per]
            print(f"{k:<10} {per:<6} {s['n']:>6} ({s['cover']:.0%}) | познати {s['hit']:.1%} (роботът казва {s['said']:.1%}) | "
                  f"ср.к {s['odds']:.2f} | X {s['x_share']:.0%}" + (f" | доход {s['roi']:+.1%} ± {s['roi_se']:.1%} на {s['n_book']}" if s.get("roi") is not None else "")
                  + f" | {s['signs']}")
    print("рискова по протокола:", out["risky_chosen"])
    return out


if __name__ == "__main__":
    main()
