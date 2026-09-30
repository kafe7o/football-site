"""
Границите на професионалиста - всичко, което е казал, заедно (2026-10-01).

Той казва (собственикът го предаде наново, „както той е казал, с цялата информация досега“):
  „да не праща повече мачове под 1.50 коефициент“;
  „рисковата не може да е 1.48 - рисковата прогноза е от 2.50 нагоре“;
  „сигурната - между 1.40 и 1.80; всичко над 1.80 е рисково, до 1.80 всичко е сигурно“;
  „за мен лично рискова е над 1.80, а сигурна примерно под 1.70; сигурната да познава средно 70%“.

Всичко заедно (сечението): НИЩО под 1.50; сигурна (и едната прогноза) 1.50-1.80; рисковата прогноза -
от 2.50 нагоре; 1.80-2.50 е „рисково“ по неговата класификация, но рискова прогноза там не се дава.
„Примерно под 1.70“ и „за мен лично“ - не е правило, показва се като вариант.

ПРОТОКОЛ (записан преди пускането): тук не се избира - изборът е на собственика („както той е казал“);
мери се цената на всяка граница за ЕДНАТА прогноза (най-вероятното по робота от всички събития в
границите, вариант A от research/one_pick_backtest.py; ако няма - резервната): колко мача имат прогноза
в границите, колко често излиза, среден коефициент, доход (само с коефициент от букмейкър).
Варианти: сега 1.40-1.80 / резерва над 1.80; 1.50-1.80 / над 1.80; 1.50-1.80 / от 2.50; 1.50-1.70 / от 2.50.
Данните и периодите - като в research/one_pick_backtest.py. Резултатът: data/master_ranges.json.

РЕЗУЛТАТ (2026-10-01; избор / чиста проверка; едната прогноза, вариант A):
  1.40-1.80 / над 1.80 (досега)     в границите 97%; познати 64.3% / 63.8%, ср. к 1.53, доход -6.8% / -7.9%
  1.50-1.80 / над 1.80              91%; 58.6% / 58.7%, 1.65, -6.7% / -8.0%
  1.50-1.80 / от 2.50 (приложено)   91%; 56.7% / 56.8% (в границите 59.5% / 59.6%), 1.77, -7.3% / -8.4%
  1.50-1.70 / от 2.50               77%; 53.5% / 53.6% (в границите 60.9%), 1.97, -9.1% / -9.2%
Собственикът избра „както той е казал“ - третия ред. Процентът пада със 7 пункта, доходът е почти същият.
Рисковата (research/ranges_backtest.py): от 2.50 - 26.5% при 3.71, -6.8%; над 1.80 - 32.0% при 3.26, -7.9%.
"""

import json
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db                                          # noqa: E402
from bets.leagues import FD                                  # noqa: E402
from research import extras_backtest                         # noqa: E402
from research.one_pick_backtest import (BOOK_SELS, SELECT_END, TOTO_BAND, TOTO_LEAGUES, base_rates,  # noqa: E402
                                        goal_events, happened, kind_of, stat)

OUT = ROOT / "data" / "master_ranges.json"
MIN_PROB = 0.50
VARIANTS = {"сега 1.40-1.80, резерва над 1.80": ((1.40, 1.80), 1.80, False),
            "1.50-1.80, резерва над 1.80": ((1.50, 1.80), 1.80, False),
            "1.50-1.80, резерва от 2.50": ((1.50, 1.80), 2.50, True),
            "1.50-1.70, резерва от 2.50": ((1.50, 1.70), 2.50, True)}


def choose(cands, band, above, inclusive):
    inb = [c for c in cands if band[0] <= c[2] <= band[1] and c[1] >= MIN_PROB]
    if inb:
        return max(inb, key=lambda c: c[1]), True
    rest = [c for c in cands if (c[2] >= above if inclusive else c[2] > above)]
    return (max(rest, key=lambda c: c[1]), False) if rest else (None, False)


def main():
    conn = db.init()
    base = base_rates(conn)
    ex_rows = extras_backtest.LAST_ROWS
    if ex_rows is None:
        with Pool(4) as pool:
            ex_rows = [r for part in pool.map(extras_backtest.run_league, list(FD)) for r in part]
    ex = defaultdict(dict)
    for r in ex_rows:
        ex[(r["league"], r["date"], r["home"], r["away"])][r["kind"]] = r
    stats = {(m["league"], m["date"], m["home_team"], m["away_team"]): (m["hy"], m["ay"], m["hc"], m["ac"])
             for m in conn.execute("""SELECT m.league, m.date, m.home_team, m.away_team, s.hy, s.ay, s.hc, s.ac
                                        FROM matches m JOIN match_stats s ON s.match_id = m.id WHERE m.date >= '2023-07-01'""")}
    res = {v: {"select": [], "clean": []} for v in VARIANTS}
    total = {"select": 0, "clean": 0}
    for r in conn.execute("SELECT league, date, home, away, probs_json, prices_json, hg, ag FROM backtest_tips WHERE basis = 'model'"):
        if r["league"] not in base:
            continue
        p = json.loads(r["probs_json"])["robot"]
        if p.get("xg_home") is None:
            continue
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {}) if r["prices_json"] else {}
        key = (r["league"], r["date"], r["home"], r["away"])
        st = stats.get(key)
        cards = st[0] + st[1] if st and st[0] is not None and st[1] is not None else None
        corners = st[2] + st[3] if st and st[2] is not None and st[3] is not None else None
        b = base[r["league"]]
        cands = []
        for s, prob in goal_events(p).items():
            book = s in BOOK_SELS and bool(avg.get(s))
            odds = avg[s] if book else (1 / prob if prob > 0 else 99.0)
            if r["league"] in TOTO_LEAGUES and s in ("1", "2") and book and TOTO_BAND[0] <= odds <= TOTO_BAND[1]:
                continue
            cands.append((s, prob, odds, book, b[s]))
        for kind, letter in (("cards", "C"), ("corners", "K")):
            x = ex.get(key, {}).get(kind)
            tot = cards if kind == "cards" else corners
            if x and tot is not None:
                for side in ("O", "U"):
                    prob = x["p"] if side == "O" else 1 - x["p"]
                    cands.append((f"{letter}{side}{x['line']}", prob, 1 / prob if prob > 0 else 99.0, False,
                                  x["base"] if side == "O" else 1 - x["base"]))
        period = "select" if r["date"] < SELECT_END else "clean"
        total[period] += 1
        for name, (band, above, incl) in VARIANTS.items():
            c, in_band = choose(cands, band, above, incl)
            if c is None:
                continue
            ok = happened(c[0], r["hg"], r["ag"], cards, corners)
            if ok is None:
                continue
            res[name][period].append({"hit": ok, "p": c[1], "odds": c[2], "book": c[3], "base": c[4], "band": in_band,
                                      "kind": kind_of(c[0])})
    out = {"matches": total, "variants": {}}
    for name in VARIANTS:
        out["variants"][name] = {}
        for period in ("select", "clean"):
            s = stat(res[name][period])
            band_items = [x for x in res[name][period] if x["band"]]
            s["band_hit"] = sum(x["hit"] for x in band_items) / len(band_items) if band_items else None
            s["band_odds"] = sum(x["odds"] for x in band_items) / len(band_items) if band_items else None
            out["variants"][name][period] = s
            print(f"{name:<34} {period:<6} {s['n']:>6} | в границите {s['in_band']:.0%} (познати там {s['band_hit']:.1%}, ср.к {s['band_odds']:.2f})"
                  f" | всичко: познати {s['hit']:.1%} ± {s['hit_se']:.1%}, ср.к {s['odds']:.2f}"
                  + (f" | доход {s['roi']:+.1%} ± {s['roi_se']:.1%} на {s['n_book']}" if s.get("roi") is not None else ""))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    main()
