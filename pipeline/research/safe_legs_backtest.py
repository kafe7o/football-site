"""
Колонки със „сигурните“ мачове на ИСТИНСКИ цени (собственикът, 2026-10-11: „дай там да влизат тия сигурните - да може реално да се залага; направи тест, като влизат
тия мачове в колонки“). Повод: над 1.5 / под 3.5 нямат цена от букмейкър в историята, роботът ползва своята (1/шанс), а реално букмейкърите плащат по-малко
(12.10: Elfsborg над 1.5 - робот 1.43, букмейкъри 1.18; Torino под 3.5 - 1.41 срещу 1.26). Майсторът (02.10): „поне от 1.40 цялостно - да не ми дава 1.20 „много сигурни“ “.

ЦЕНИ: 1/X/2, двоен шанс, над/под 2.5 - средната на букмейкърите (backtest_tips.prices_json). Над/под 1.5 и 3.5, двата вкарват - ОЦЕНКА от пазара: общите голове λ от цената на
над/под 2.5 (без маржа), Poisson; разделени между отборите като очакваните голове на робота; цена = 1 / (шанс по пазара * 1.05). Проверено на 8 истински цени от 12.10 преди
пускането: 1.17/1.18, 1.22/1.22, 1.25/1.26, 1.33/1.33, 1.46/1.49, 1.46/1.49, 1.50/1.56 (оценката е малко под истинската при по-високите цени - консервативна).
Само мачове с пазар над/под 2.5 (22-те лиги на football-data).

ПРОТОКОЛ (записан преди пускането): данни backtest_tips (walk-forward), шансът е на робота (без поправка), колонки по 3, топ шанс (bets/sure.py), шанс >= 65%, един мач на първенство, до 4 на ден.
  R0 - сегашното: едната прогноза с праг 1.40 върху цената, която роботът вижда (букмейкър или своята 1/шанс); парите - на истински / оценени цени;
  S1 - „сигурните вътре“: най-вероятното събитие от 1/X/2, двоен шанс, над/под 1.5/2.5/3.5, двата вкарват - БЕЗ праг на цената;
  S2 - правилото на майстора върху истинската цена: най-вероятното събитие с истинска / оценена цена >= 1.40.
Мери се в избора (до 2025-07-01) и в чистата проверка (от там): колонки на ден, колко минават (± грешка), среден коефициент, връщане от 1 € (± грешка); и за отделните мачове в колонките.
Описателно - правилата на майстора не се пипат. Резултатът: data/safe_legs_backtest.json.
"""

import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from scipy.stats import poisson

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import columns, config, robot                       # noqa: E402
from research import columns_backtest as cb                    # noqa: E402

OUT = ROOT / "data" / "safe_legs_backtest.json"
SELECT_END = "2025-07-01"
MARGIN = 0.05
SELS = ("1", "X", "2", "1X", "X2", "12", "O15", "U15", "O", "U", "O35", "U35", "GG", "NG")


def lam_from(p_over):
    lo, hi = 0.2, 8.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if 1 - poisson.cdf(2, mid) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def real_prices(rb, avg):
    """{избор: истинска или оценена цена} за мача; None, ако няма пазар над/под 2.5."""
    if not avg.get("O") or not avg.get("U"):
        return None
    po = (1 / avg["O"]) / (1 / avg["O"] + 1 / avg["U"])
    lam = lam_from(po)
    share = rb["xg_home"] / (rb["xg_home"] + rb["xg_away"])
    lh, la = lam * share, lam * (1 - share)
    est = {"O15": 1 - poisson.cdf(1, lam), "U15": poisson.cdf(1, lam), "O35": 1 - poisson.cdf(3, lam), "U35": poisson.cdf(3, lam),
           "GG": (1 - math.exp(-lh)) * (1 - math.exp(-la))}
    est["NG"] = 1 - est["GG"]
    out = {s: 1 / (p * (1 + MARGIN)) for s, p in est.items() if p > 0.01}
    for s in ("1", "X", "2", "1X", "X2", "12", "O", "U"):
        if avg.get(s):
            out[s] = avg[s]
    return out


def load():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    days = defaultdict(list)
    for r in conn.execute("SELECT league, date, home, away, probs_json, prices_json, flags_json, hg, ag FROM backtest_tips "
                          "WHERE basis = 'model' AND date >= '2023-07-01' AND hg IS NOT NULL"):
        rb = json.loads(r["probs_json"])["robot"]
        if rb.get("xg_home") is None:
            continue
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {}) if r["prices_json"] else {}
        real = real_prices(rb, avg)
        if real is None:
            continue
        g = robot.goal_line_probs(rb)
        p = {**{s: rb[s] for s in ("1", "X", "2", "1X", "X2", "12")}, "O15": g[1.5], "U15": 1 - g[1.5], "O": g[2.5], "U": 1 - g[2.5],
             "O35": g[3.5], "U35": 1 - g[3.5], "GG": g["GG"], "NG": 1 - g["GG"]}
        flags = json.loads(r["flags_json"] or "{}")
        days[r["date"]].append({"id": f"{r['league']}:{r['date']}:{r['home']}", "league": r["league"], "kickoff": r["date"], "home": r["home"],
                                "away": r["away"], "basis": "model", "flags": {"derby": flags.get("derby")}, "hg": r["hg"], "ag": r["ag"],
                                "p": p, "rb": rb, "avg": avg, "real": real})
    return days


def pick(m, variant):
    if variant == "R0":
        one = robot.one_pick(m["rb"], m["avg"], None, m["league"])
        return {**one, "odds_real": m["real"].get(one["sel"], one["odds"])} if one else None
    best = None
    for s in SELS:
        p = m["p"][s]
        price = m["real"].get(s)
        if price is None or p < 0.5:
            continue
        if variant == "S2" and price < robot.FLOOR:
            continue
        if best is None or p > best["p"]:
            best = {"sel": s, "p": p, "odds": round(price, 2), "src": "book", "odds_real": price}
    return best


def run(days, variant):
    st = {per: {"days": 0, "cols": 0, "pass": 0, "odds": 0.0, "pay": [], "legs": 0, "legs_hit": 0, "legs_pay": 0.0} for per in ("select", "clean")}
    for day, ms in sorted(days.items()):
        per = "select" if day < SELECT_END else "clean"
        s = st[per]
        s["days"] += 1
        cur = []
        for m in ms:
            one = pick(m, variant)
            if one:
                cur.append({**m, "one": one, "flags": dict(m["flags"])})
        by_id = {m["id"]: m for m in cur}
        cb.mark_sure(cur)
        cands = [c for c in columns.candidates(cur, 0.65, need_sure=True)] if variant == "R0" else \
                [c for c in sure_cands(cur)]
        for col in columns.build(cands, size=3, max_columns=4, diversify="league"):
            odds = 1.0
            ok = True
            for c in col:
                m = by_id[c["id"]]
                o = m["one"]["odds_real"]
                h = bool(robot.hit_any(c["sel"], m["hg"], m["ag"]))
                odds *= o
                ok &= h
                s["legs"] += 1
                s["legs_hit"] += h
                s["legs_pay"] += o if h else 0.0
            s["cols"] += 1
            s["pass"] += ok
            s["odds"] += odds
            s["pay"].append(odds if ok else 0.0)
    out = {}
    for per, s in st.items():
        n = s["cols"]
        if not n:
            out[per] = {"cols": 0}
            continue
        p = s["pass"] / n
        m = sum(s["pay"]) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in s["pay"]) / (n - 1))
        out[per] = {"cols": n, "per_day": n / s["days"], "pass": p, "se": math.sqrt(p * (1 - p) / n), "odds": s["odds"] / n,
                    "return": m, "return_se": sd / math.sqrt(n), "legs": s["legs"], "legs_hit": s["legs_hit"] / s["legs"],
                    "legs_return": s["legs_pay"] / s["legs"]}
    return out


def sure_cands(ms):
    """Кандидатите за колонка без проверката за цена от 1.40 (за S1/S2 цената е вече решена в pick): топ шанс, шанс >= 65%, без дерби."""
    from bets import rules
    out = []
    for m in ms:
        one = m["one"]
        if rules.match_block(m["flags"], m["basis"]) or rules.sure_block(m["flags"]) or one["p"] < 0.65:
            continue
        out.append({"id": m["id"], "league": m["league"], "kickoff": m["kickoff"], "home": m["home"], "away": m["away"],
                    "sel": one["sel"], "p": one["p"], "odds": one["odds"], "src": "book", "sure": m["flags"].get("sure")})
    return sorted(out, key=lambda c: -c["p"])


def main():
    days = load()
    print("дни:", len(days), "мачове:", sum(len(v) for v in days.values()))
    res = {}
    names = {"R0": "сегашното (праг 1.40 върху цената на робота)", "S1": "сигурните вътре (без праг)", "S2": "праг 1.40 върху истинската цена"}
    for v in ("R0", "S1", "S2"):
        res[v] = run(days, v)
        for per, lab in (("select", "избор"), ("clean", "чиста")):
            x = res[v][per]
            print(f"{v} {names[v]:44} | {lab:5} | {x['cols']:>4} колонки ({x['per_day']:.2f} на ден) минават {x['pass']:.1%} ± {x['se']:.1%}, ср. коеф. {x['odds']:.2f}, "
                  f"връща {x['return']:.2f} ± {x['return_se']:.2f} € | мачове {x['legs_hit']:.1%}, връщат {x['legs_return']:.2f}")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
