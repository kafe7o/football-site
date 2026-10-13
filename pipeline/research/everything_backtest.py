"""
ВСИЧКО НАВЕДНЪЖ - най-доброто от цялата информация (собственикът, 2026-10-13: „от всичко най-доброто и от цялата информация“, да започне в 03:21 след съставите).

Какво се съчетава, всичко по правилата на майстора (истинска цена от 1.40, без тото, без дерби, топ шанс, шанс >= 65%, един мач на първенство, ден за ден, по 2 мача, най-много една
колонка на ден при шанс на колонката >= 50% - системата):
  1) ВАРИАНТ 2 - поправеният шанс (учи се от собствените резултати на робота);
  2) СЪСТАВИТЕ И КОНТУЗЕНИТЕ (apif_history.db, топ 5 първенства 2024-2026): поправка на очакваните голове по отсъстващите редовни играчи - a, g от research/squads_backtest.py,
     walk-forward по месеци; влиза само ако там е минала по протокола (Brier по-добър с над 2 грешки в двата периода) - иначе вариантът „със състави“ е само за сведение;
  3) КАРТОНИТЕ на съседната линия (съдиите за всички първенства; калибрирани 73%) като кандидат за мач в колонката - цена ДОПУСНАТА (1.30 / 1.40; odds API няма картони);
  4) ЦЕНАТА: средната на букмейкърите (сегашното) или най-добрият букмейкър (MAX от football.db) за головете / знаците;
  5) дните: чет-нед / всеки ден; първенствата: най-добрите 11.
ПРОТОКОЛ (записан преди пускането): решетка {състави: без / с} x {картони: без / >= 75% @1.30 / >= 75% @1.40} x {цена: средна / най-добър букмейкър} x {чет-нед / всеки ден} = 24 варианта.
Мери се: колонки на месец, минават (± грешка), връщане (избор до 2025-07-01 / чиста от там), пари при 300 €/30 € (средно на месец, +100 €, минус). Най-доброто се избира по ЧИСТАТА
проверка при поне 50 колонки в нея и се казва с грешката; парите при допусната цена за картони се отбелязват като допускане. Резултатът: data/everything_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import model                                            # noqa: E402
from research import goal_lines_backtest as gl                    # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import master_v2_system as ms                       # noqa: E402
from research import pro_price_backtest as pp                     # noqa: E402
from research import squads_backtest as sq                        # noqa: E402
from research import system_cards_backtest as sc                  # noqa: E402
from research import system_kinds_backtest as sk                  # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "everything_backtest.json"
SELECT_END = vb.SELECT_END


def event_probs(xh, xa):
    """14-те събития от очакваните голове (както research/safe_legs_backtest.load, но от xg)."""
    g = model.score_grid(xh, xa)
    k = np.arange(g.shape[0])
    tot = k[:, None] + k[None, :]
    p1, px, p2 = float(np.tril(g, -1).sum()), float(np.trace(g)), float(np.triu(g, 1).sum())
    gg = float(g[1:, 1:].sum())
    o15, o25, o35 = float(g[tot >= 2].sum()), float(g[tot >= 3].sum()), float(g[tot >= 4].sum())
    d = {"1": p1, "X": px, "2": p2, "1X": p1 + px, "X2": px + p2, "12": p1 + p2, "O15": o15, "U15": 1 - o15, "O": o25, "U": 1 - o25,
         "O35": o35, "U35": 1 - o35, "GG": gg, "NG": 1 - gg}
    return [d[e] for e in vb.EV]


def squad_adjusted_rows(rows):
    """Копие на rows с шансове от поправените очаквани голове (състави: a, g по месеци само от миналото). Мачове без състави - без промяна.
    Връща (rows', брой поправени, последните a, g, минава ли протокола според data/squads_backtest.json)."""
    feats = sq.load_features()
    srows, _ = sq.match_rows(feats)
    key_of = lambda r: (r["league"], r["date"], r["home"], r["away"])
    by_key = {key_of(r): r for r in srows}
    srows.sort(key=lambda r: r["date"])
    passed = False
    path = ROOT / "data" / "squads_backtest.json"
    if path.exists():
        res = json.loads(path.read_text(encoding="utf-8"))
        v = (res.get("variants") or {}).get("контузии (преди записа)") or {}
        per = v.get("periods") or {}
        a, b = per.get("А", {}).get("1/X/2"), per.get("Б", {}).get("1/X/2")
        passed = bool(a and b and a["diff"] < -2 * a["se"] and b["diff"] < -2 * b["se"])
    coefs = {}
    for m0 in sq.months("2025-01-01", srows[-1]["date"] if srows else "2025-01-01"):
        train = [r for r in srows if r["date"] < m0]
        if len(train) >= 300:
            coefs[m0] = sq.fit(train, "mi")
    out, n_adj, last = [], 0, (0.0, 0.0)
    for r in rows:
        s = by_key.get((r["league"], r["day"], r["home"], r["away"]))
        m0 = r["day"][:7] + "-01"
        if s is None or m0 not in coefs:
            out.append(r)
            continue
        a, g = coefs[m0]
        last = (a, g)
        mh, ma = s["mi"]
        xh, xa = s["xh"] * math.exp(a * mh + g * ma), s["xa"] * math.exp(a * ma + g * mh)
        out.append({**r, "p": event_probs(xh, xa)})
        n_adj += 1
    return out, n_adj, last, passed


def with_prices(rows, picks, prices, book):
    """Същите избори с цена от book (MAX = най-добрият букмейкър) вместо средната."""
    out = {}
    for i, (e, s, r, h) in picks.items():
        row = rows[i]
        pr = prices.get((row["league"], row["day"], row["home"], row["away"]), {})
        p = pp.price_at(row, e, r, pr, book)
        out[i] = (e, s, p if p else r, h)
    return out


def main():
    base_rows = vb.build()
    adj_rows, n_adj, (a, g), passed = squad_adjusted_rows(base_rows)
    print(f"състави: поправени {n_adj} мача; последни a = {a:+.3f}, g = {g:+.3f}; минава протокола (Brier 1/X/2 над 2 грешки в А и Б): {passed}")
    prices = pp.load_prices()
    cards = sk.extras_candidates("cards", 0.75)
    res = []
    for squads, (cname, cprice), book, days in product((False, True), (("без картони", None), ("картони >= 75% @1.30", 1.30), ("картони >= 75% @1.40", 1.40)), ("AVG", "MAX"), ("чет-нед", "всеки ден")):
        rows = adj_rows if squads else base_rows
        vb.ARR.clear()
        picks = mr.master_picks(rows, "вариант 2", False, False, floor=1.40)
        if book == "MAX":
            picks = with_prices(rows, picks, prices, "MAX")
        by_day = ms.top_picks(rows, picks, 0.65)
        best = ms.best_leagues(ms.leg_stats(rows, picks, by_day, "league"))
        st, months = sc.run(rows, picks, by_day, cards if cprice else {}, best, days, "голове + картони" if cprice else "само голове", cprice or 1.0)
        m = ms.money(months) or {}
        res.append({"squads": squads, "cards": cname, "price": book, "days": days, "stats": st, "money": {k: v for k, v in m.items() if k != "by_month"},
                    "per_month": (st["select"].get("n", 0) + st["clean"].get("n", 0)) / max(1, m.get("months", 1))})
    vb.ARR.clear()
    name = lambda v: f"{'със състави' if v['squads'] else 'без състави':12} {v['cards']:22} {'най-добър букм.' if v['price'] == 'MAX' else 'средна цена':16} {v['days']:9}"
    print(f"\n{'вариант':64} | кол/мес | избор: мин. връща | чиста: мин. връща ±  | 300/30: ср./мес +100 минус")
    for v in sorted(res, key=lambda v: -(v["stats"]["clean"].get("return") or 0)):
        s, c, m = v["stats"]["select"], v["stats"]["clean"], v["money"]
        if not c.get("n"):
            continue
        print(f"{name(v):64} | {v['per_month']:>5.1f}   | {s.get('hit', 0):>4.0%} {s.get('return', 0):.3f}     | {c['hit']:>4.0%} {c['return']:.3f} ±{c['return_se']:.2f} | {m.get('avg', 0):+5.0f} {m.get('plus100', 0):>3}/{m.get('months', 0)} {m.get('minus', 0):>3}")
    OUT.write_text(json.dumps({"squads": {"adjusted": n_adj, "a": a, "g": g, "passed": passed}, "variants": res}, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
