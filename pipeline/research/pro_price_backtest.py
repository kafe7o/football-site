"""
Системата през очите на професионалист: ЦЕНАТА (най-добрият букмейкър, Pinnacle, затварящата) и CLV (собственикът, 2026-10-12: „гледай го от перспективата на професионален
бетър, който го прави 20 години“).

Какво прави професионалистът различно от нас досега: не залага на средната цена, а на НАЙ-ДОБРАТА измежду много букмейкъри (line shopping), и мери дали цената му е по-добра от
затварящата (CLV - closing line value): който редовно взима цена над затварящата, печели в дългосрочен план, независимо от резултатите за месец.

Данни: най-добрата система (вариант 2 + правилата на майстора, праг 1.40, шанс 65%, по 2, най-добрите първенства; research/fewer_columns_backtest.py - А: чет-нед, 1 колонка на ден,
шанс на колонката >= 50%; Б: чет-нед, до 2 на ден) и цените от football.db (odds, odds_totals): AVG (средна - сегашното), MAX (най-добрият букмейкър), PS (Pinnacle), AVGC/MAXC
(затварящи). Двоен шанс от 1/X/2 на същия източник; над/под 1.5 / 3.5 и двата вкарват - оценката от пазара (research/safe_legs_backtest.py), умножена по отношението MAX/AVG на
същата страна на над/под 2.5 за мача (прокси за по-добра цена).
ПРОТОКОЛ (записан преди пускането): същите колонки, същите резултати, сменя се само цената: връщане от 1 € при AVG, MAX, PS, и CLV = средно (цена сутрин / затваряща цена - 1) за
мачовете на робота срещу всички мачове с цена (положително = пазарът се движи КЪМ нашия избор). Описателно. Резултатът: data/pro_price_backtest.json.
"""

import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import config                                           # noqa: E402
from research import fewer_columns_backtest as fc                 # noqa: E402
from research import goal_lines_backtest as gl                    # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import master_v2_system as ms                       # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "pro_price_backtest.json"
SELECT_END = vb.SELECT_END
BOOKS = ("AVG", "MAX", "PS", "AVGC", "MAXC")


def load_prices():
    """{(league, date, home, away): {book: {sel: price}}} от football.db."""
    c = sqlite3.connect(config.ROOT / "football.db")
    c.row_factory = sqlite3.Row
    out = defaultdict(lambda: defaultdict(dict))
    mid = {}
    for r in c.execute("SELECT id, league, date, home_team, away_team FROM matches WHERE date >= '2023-07-01'"):
        mid[r["id"]] = (r["league"], r["date"], r["home_team"], r["away_team"])
    q = ",".join("?" * len(BOOKS))
    for r in c.execute(f"SELECT match_id, bookmaker, odds_home, odds_draw, odds_away FROM odds WHERE bookmaker IN ({q})", BOOKS):
        k = mid.get(r["match_id"])
        if not k or not (r["odds_home"] and r["odds_draw"] and r["odds_away"]):
            continue
        h, d, a = r["odds_home"], r["odds_draw"], r["odds_away"]
        out[k][r["bookmaker"]].update({"1": h, "X": d, "2": a, "1X": 1 / (1 / h + 1 / d), "X2": 1 / (1 / d + 1 / a), "12": 1 / (1 / h + 1 / a)})
    for r in c.execute(f"SELECT match_id, bookmaker, line, odds_over, odds_under FROM odds_totals WHERE bookmaker IN ({q}) AND line = 2.5", BOOKS):
        k = mid.get(r["match_id"])
        if not k or not (r["odds_over"] and r["odds_under"]):
            continue
        out[k][r["bookmaker"]].update({"O": r["odds_over"], "U": r["odds_under"]})
    return out


def price_at(row, sel, base_price, prices, book):
    """Цената на избора при даден източник; над/под 1.5 / 3.5 и двата вкарват - оценката x (MAX/AVG на същата страна на над/под 2.5)."""
    bk, avg = prices.get(book) or {}, prices.get("AVG") or {}
    if sel in bk:
        return bk[sel]
    side = "O" if sel in ("O15", "O35", "GG") else "U"
    if bk.get(side) and avg.get(side):
        return base_price * bk[side] / avg[side]
    return None


def main():
    rows = vb.build()
    prices = load_prices()
    picks = mr.master_picks(rows, "вариант 2", False, False, floor=1.40)
    by_day = ms.top_picks(rows, picks, 0.65)
    leagues = ms.best_leagues(ms.leg_stats(rows, picks, by_day, "league"))
    res = {}
    for sname, per_day, min_col in (("А: 1 на ден, колонка 50%+", 1, 0.5), ("Б: до 2 на ден", 2, 0.0)):
        # същите колонки като fewer_columns_backtest.run, но пазим мачовете
        cols = []
        for day, ids in sorted(by_day.items()):
            if rows[ids[0]]["wd"] not in fc.DAYS["чет-нед"]:
                continue
            ids = [i for i in ids if rows[i]["league"] in leagues]
            used = set()
            for _ in range(per_day):
                col, lgs = [], set()
                for i in ids:
                    if i in used or rows[i]["league"] in lgs or len(col) == 2:
                        continue
                    col.append(i)
                    lgs.add(rows[i]["league"])
                if len(col) < 2:
                    break
                used.update(col)
                if math.prod(picks[i][1] for i in col) < min_col:
                    continue
                cols.append((day, col))
        out = {}
        for book in BOOKS:
            acc = {"select": [], "clean": []}
            missing = 0
            for day, col in cols:
                odds = 1.0
                ok = True
                for i in col:
                    r = rows[i]
                    p = price_at(r, picks[i][0], picks[i][2], prices.get((r["league"], r["day"], r["home"], r["away"]), {}), book)
                    if p is None:
                        p = picks[i][2]
                        missing += 1
                    odds *= p
                    ok &= picks[i][3]
                acc["select" if day < SELECT_END else "clean"].append((ok, odds))
            out[book] = {per: gl.stat(v) for per, v in acc.items()}
            out[book]["missing_legs"] = missing
        # CLV: сутрешна AVG срещу затваряща AVGC за мачовете на робота и за всички мачове
        clv_sel, clv_all = [], []
        for day, col in cols:
            for i in col:
                r = rows[i]
                pr = prices.get((r["league"], r["day"], r["home"], r["away"]), {})
                a, c_ = price_at(r, picks[i][0], picks[i][2], pr, "AVG"), price_at(r, picks[i][0], picks[i][2], pr, "AVGC")
                if a and c_:
                    clv_sel.append(a / c_ - 1)
        for k, pr in prices.items():
            if "AVG" in pr and "AVGC" in pr:
                for s in ("1", "X", "2"):
                    if pr["AVG"].get(s) and pr["AVGC"].get(s):
                        clv_all.append(pr["AVG"][s] / pr["AVGC"][s] - 1)
        out["clv"] = {"robot": sum(clv_sel) / len(clv_sel) if clv_sel else None, "n": len(clv_sel),
                      "all": sum(clv_all) / len(clv_all) if clv_all else None}
        res[sname] = out
        print(f"\n=== {sname} ({len(cols)} колонки) ===")
        for book, lab in (("AVG", "средна цена (сегашното)"), ("MAX", "най-добрият букмейкър"), ("PS", "Pinnacle"), ("AVGC", "средна затваряща"), ("MAXC", "най-добра затваряща")):
            s, c_ = out[book]["select"], out[book]["clean"]
            print(f"  {lab:26} | избор {s['n']:>3} коеф {s['price']:.2f} връща {s['return']:.3f} ± {s['return_se']:.2f} | чиста {c_['n']:>3} коеф {c_['price']:.2f} връща {c_['return']:.3f} ± {c_['return_se']:.2f}"
                  + (f" | без цена при {out[book]['missing_legs']} мача" if out[book]["missing_legs"] else ""))
        cl = out["clv"]
        print(f"  CLV (сутрин / затваряща - 1): мачовете на робота {cl['robot']:+.2%} ({cl['n']}), всички изходи {cl['all']:+.2%}")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
