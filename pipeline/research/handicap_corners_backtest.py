"""
Хендикап при голям фаворит и кой отбор ще изпълни повече корнери (професионалистът, 2026-10-01).

Той казва: „нека да се опита да анализира, когато единият отбор е голям фаворит (Байерн - Аугсбург),
дали мачът може да излезе хендикап едно - от нула на едно: домакинът да спечели с два или повече
гола разлика, и тем подобни залози. Кой отбор ще изпълни повече корнери - там е ясно, Байерн; но
в мач като Арсенал - Челси да се опита да анализира и такива неща.“

Какво знае роботът: очакваните голове на домакина и госта (bets/model.py) -> вероятност за всяка
разлика в резултата; очакваните корнери на всеки отбор (bets/extras.py, същият модел върху корнерите).

ПРОТОКОЛ (записан преди пускането):
Данни: backtest_tips (walk-forward, само мачовете с модел) от 2023-07-01; корнерите - research/
extras_backtest.run_league (walk-forward, 22-те лиги на football-data). ИЗБОР до 2025-07-01, ЧИСТА от там.
Събития:
  хендикап  H1 „1 (0:1)“ - домакинът с 2+ гола разлика;  H2 „2 (1:0)“ - гостът с 2+;
            H1_2 „1 (0:2)“ - домакинът с 3+;  H2_2 „2 (2:0)“ - гостът с 3+;
  корнери   KH - домакинът изпълнява повече; KD - равно; KA - гостът повече (Poisson за всеки отбор).
Мери се за всяко събитие: Brier на робота срещу ОБИЧАЙНОТО за лигата (честотата в 4-те години преди
2023-07-01 - „без модел“), сдвоено t; калибрация по групи; при голям фаворит (шанс за победа по робота
поне 60%) - колко често фаворитът печели с 2+ и какво казва роботът; при равни мачове (разлика в
шанса за 1 и 2 под 15 пункта) - колко често познава кой ще изпълни повече корнери.
Пазар: където средната линия на азиатския хендикап е точно -1.5/+1.5 (= хендикап 0:1 / 1:0), Brier на
робота срещу пазара (без маржа) на същите мачове и доход при залог на фаворита -1.5, когато роботът
дава поне 50% (за сведение).
ПРИЕМА СЕ:
  1) процент на робота за хендикапа / за корнерите се показва на сайта само ако Brier е по-добър от
     обичайното за лигата И в избора, И в чистата проверка (като за картоните и корнерите над/под);
     иначе - само очакваната разлика в головете / очакваните корнери на всеки отбор, без процент;
  2) тези събития влизат в кандидатите за ЕДНАТА прогноза само ако имат умение по 1) И едната прогноза
     с тях (A+) познава поне колкото без тях (A) в избора, а в чистата не е с повече от 2 грешки по-зле.
Резултатът: data/handicap_corners.json.
"""

import json
import math
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.stats import poisson

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, model, robot                     # noqa: E402
from bets.leagues import FD                           # noqa: E402
from bets.market import implied_row                   # noqa: E402
from research import extras_backtest                  # noqa: E402
from research.one_pick_backtest import (BOOK_SELS, TOTO_BAND, TOTO_LEAGUES, base_rates, goal_events,  # noqa: E402
                                        happened)

OUT = ROOT / "data" / "handicap_corners.json"
SELECT_END = "2025-07-01"
BASE_BEFORE, BASE_YEARS = "2023-07-01", 4
HCP = {"H1": (1, 2), "H2": (-1, 2), "H1_2": (1, 3), "H2_2": (-1, 3)}     # (страна, поне толкова гола разлика)
BAND, FALLBACK = robot.SAFE_RANGE, robot.RISKY_FROM


def hcp_probs(lh, la):
    grid = model.score_grid(lh, la)
    k = np.arange(grid.shape[0])
    diff = k[:, None] - k[None, :]
    return {code: float(grid[side * diff >= n].sum()) for code, (side, n) in HCP.items()}


def hcp_hit(code, hg, ag):
    side, n = HCP[code]
    return side * (hg - ag) >= n


def corner_probs(lh, la, top=40):
    ph, pa = poisson.pmf(np.arange(top), lh), poisson.pmf(np.arange(top), la)
    joint = np.outer(ph, pa)
    return {"KH": float(np.tril(joint, -1).sum()), "KD": float(np.trace(joint)), "KA": float(np.triu(joint, 1).sum())}


def bases(conn):
    """Обичайното за лигата преди 2023-07-01: хендикапите (от головете) и кой изпълнява повече корнери."""
    since = f"{int(BASE_BEFORE[:4]) - BASE_YEARS}{BASE_BEFORE[4:]}"
    g, c = defaultdict(list), defaultdict(list)
    for r in conn.execute("""SELECT m.league, m.fthg, m.ftag, s.hc, s.ac FROM matches m LEFT JOIN match_stats s ON s.match_id = m.id
                              WHERE m.fthg IS NOT NULL AND m.date >= ? AND m.date < ?""", (since, BASE_BEFORE)):
        g[r["league"]].append((r["fthg"], r["ftag"]))
        if r["hc"] is not None and r["ac"] is not None:
            c[r["league"]].append((r["hc"], r["ac"]))
    out = {}
    for lg, xs in g.items():
        if len(xs) < 200:
            continue
        out[lg] = {code: sum(hcp_hit(code, h, a) for h, a in xs) / len(xs) for code in HCP}
        ys = c.get(lg) or []
        if len(ys) >= 200:
            out[lg].update({"KH": sum(h > a for h, a in ys) / len(ys), "KD": sum(h == a for h, a in ys) / len(ys),
                            "KA": sum(h < a for h, a in ys) / len(ys)})
    return out


def paired(rows, key_m, key_b):
    d = [r[key_m] - r[key_b] for r in rows]
    n = len(d)
    if n < 2:
        return None
    mu = sum(d) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in d) / (n - 1))
    return {"n": n, "robot": sum(r[key_m] for r in rows) / n, "base": sum(r[key_b] for r in rows) / n,
            "diff": mu, "t": mu / (sd / math.sqrt(n)) if sd else 0.0}


def calib(rows, pkey, hkey):
    out = []
    for lo in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
        b = [r for r in rows if lo <= r[pkey] < lo + 0.1]
        if len(b) >= 50:
            out.append({"from": lo, "n": len(b), "said": sum(r[pkey] for r in b) / len(b), "real": sum(r[hkey] for r in b) / len(b)})
    return out


def main():
    conn = db.init()
    base = base_rates(conn)                 # за кандидатите на едната прогноза (голове)
    extra_base = bases(conn)
    ex_rows = extras_backtest.LAST_ROWS
    if ex_rows is None:
        with Pool(4) as pool:
            ex_rows = [r for part in pool.map(extras_backtest.run_league, list(FD)) for r in part]
    ex = defaultdict(dict)
    for r in ex_rows:
        ex[(r["league"], r["date"], r["home"], r["away"])][r["kind"]] = r
    stats = {(m["league"], m["date"], m["home_team"], m["away_team"]): (m["hy"], m["ay"], m["hc"], m["ac"], m["id"])
             for m in conn.execute("""SELECT m.id, m.league, m.date, m.home_team, m.away_team, s.hy, s.ay, s.hc, s.ac
                                        FROM matches m LEFT JOIN match_stats s ON s.match_id = m.id WHERE m.date >= '2023-07-01'""")}
    ah = {r["match_id"]: (r["line"], r["odds_home"], r["odds_away"]) for r in conn.execute(
        "SELECT match_id, line, odds_home, odds_away FROM odds_ah WHERE bookmaker = 'AVG'")}
    H, K, ONE = [], [], {"A": {"select": [], "clean": []}, "A+": {"select": [], "clean": []}}
    for r in conn.execute("SELECT league, date, home, away, probs_json, prices_json, hg, ag FROM backtest_tips WHERE basis = 'model'"):
        lg = r["league"]
        if lg not in base or lg not in extra_base:
            continue
        p = json.loads(r["probs_json"])["robot"]
        if p.get("xg_home") is None:
            continue
        period = "select" if r["date"] < SELECT_END else "clean"
        key = (lg, r["date"], r["home"], r["away"])
        st = stats.get(key)
        hp = hcp_probs(p["xg_home"], p["xg_away"])
        row = {"period": period, "league": lg, "fav": max(p["1"], p["2"]), "fav_side": "H1" if p["1"] >= p["2"] else "H2"}
        for code in HCP:
            hit = int(hcp_hit(code, r["hg"], r["ag"]))
            row[code] = hp[code]
            row[code + "_hit"] = hit
            row[code + "_bm"] = (hp[code] - hit) ** 2
            row[code + "_bb"] = (extra_base[lg][code] - hit) ** 2
        # пазарът: средната линия точно -1.5 / +1.5
        mid = st[4] if st else None
        a = ah.get(mid)
        if a and a[0] in (-1.5, 1.5) and a[1] and a[2]:
            mk = implied_row([a[1], a[2]])
            code = "H1" if a[0] == -1.5 else "H2"            # линията е за домакина: -1.5 -> домакинът с 2+
            row["mk"] = {"code": code, "p": mk[0] if code == "H1" else mk[1], "odds": a[1] if code == "H1" else a[2]}
        H.append(row)
        # корнери
        cx = ex.get(key, {}).get("corners")
        cs = (st[2], st[3]) if st and st[2] is not None and st[3] is not None else None
        if cx and cs and "KH" in extra_base[lg] and cx.get("he") is not None:
            cp = corner_probs(cx["he"], cx["ae"])
            res = "KH" if cs[0] > cs[1] else ("KD" if cs[0] == cs[1] else "KA")
            K.append({"period": period, "balanced": abs(p["1"] - p["2"]) < 0.15, "fav": max(p["1"], p["2"]) >= 0.6,
                      "bm": sum((cp[c] - (c == res)) ** 2 for c in cp),
                      "bb": sum((extra_base[lg][c] - (c == res)) ** 2 for c in cp),
                      "pick": max(("KH", "KA"), key=lambda c: cp[c]), "res": res, **cp})
        # едната прогноза: A и A+ (с хендикапа и корнерите)
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {}) if r["prices_json"] else {}
        cards = st[0] + st[1] if st and st[0] is not None and st[1] is not None else None
        corners = cs[0] + cs[1] if cs else None
        cands = []
        for s, prob in goal_events(p).items():
            book = s in BOOK_SELS and bool(avg.get(s))
            odds = avg[s] if book else (1 / prob if prob > 0 else 99.0)
            if lg in TOTO_LEAGUES and s in ("1", "2") and book and TOTO_BAND[0] <= odds <= TOTO_BAND[1]:
                continue
            cands.append((s, prob, odds, lambda s=s: happened(s, r["hg"], r["ag"], cards, corners)))
        for kind, letter in (("cards", "C"), ("corners", "K")):
            x = ex.get(key, {}).get(kind)
            tot = cards if kind == "cards" else corners
            if x and tot is not None:
                for side in ("O", "U"):
                    prob = x["p"] if side == "O" else 1 - x["p"]
                    sel = f"{letter}{side}{x['line']}"
                    cands.append((sel, prob, 1 / prob if prob > 0 else 99.0, lambda sel=sel: happened(sel, r["hg"], r["ag"], cards, corners)))
        plus = list(cands)
        for code in HCP:
            plus.append((code, hp[code], 1 / hp[code] if hp[code] > 0 else 99.0, lambda code=code: hcp_hit(code, r["hg"], r["ag"])))
        if cx and cs:
            cp = corner_probs(cx["he"], cx["ae"])
            res = "KH" if cs[0] > cs[1] else ("KD" if cs[0] == cs[1] else "KA")
            for c in ("KH", "KA"):
                plus.append((c, cp[c], 1 / cp[c] if cp[c] > 0 else 99.0, lambda c=c, res=res: c == res))
        for name, pool in (("A", cands), ("A+", plus)):
            band = [c for c in pool if BAND[0] <= c[2] <= BAND[1] and c[1] >= 0.5]
            pick = max(band, key=lambda c: c[1]) if band else max([c for c in pool if c[2] >= FALLBACK], key=lambda c: c[1], default=None)
            if pick:
                h = pick[3]()
                if h is not None:
                    ONE[name][period].append({"hit": bool(h), "sel": pick[0]})
    out = {"select_end": SELECT_END, "handicap": {}, "corners": {}, "one": {}}
    skill = {}
    for code in HCP:
        per = {}
        for period in ("select", "clean"):
            xs = [x for x in H if x["period"] == period]
            per[period] = paired(xs, code + "_bm", code + "_bb")
            per[period]["hit_rate"] = sum(x[code + "_hit"] for x in xs) / len(xs)
            per[period]["said"] = sum(x[code] for x in xs) / len(xs)
        per["calibration"] = calib(H, code, code + "_hit")
        skill[code] = all(per[p]["robot"] < per[p]["base"] for p in ("select", "clean"))
        out["handicap"][code] = {**per, "skill": skill[code]}
    for period in ("select", "clean"):
        big = [x for x in H if x["period"] == period and x["fav"] >= 0.6]
        out["handicap"].setdefault("big_favourite", {})[period] = {
            "n": len(big), "fav_by_2": sum(x[x["fav_side"] + "_hit"] for x in big) / len(big) if big else None,
            "said": sum(x[x["fav_side"]] for x in big) / len(big) if big else None}
        mk = [x for x in H if x["period"] == period and "mk" in x]
        if mk:
            bm = [(x[x["mk"]["code"]] - x[x["mk"]["code"] + "_hit"]) ** 2 for x in mk]
            bk = [(x["mk"]["p"] - x[x["mk"]["code"] + "_hit"]) ** 2 for x in mk]
            bets = [x for x in mk if x[x["mk"]["code"]] >= 0.5]
            pr = [(x["mk"]["odds"] - 1) if x[x["mk"]["code"] + "_hit"] else -1 for x in bets]
            out["handicap"].setdefault("market_1_5", {})[period] = {
                "n": len(mk), "brier_robot": sum(bm) / len(mk), "brier_market": sum(bk) / len(mk),
                "bets": len(bets), "roi": sum(pr) / len(pr) if pr else None}
    for period in ("select", "clean"):
        xs = [x for x in K if x["period"] == period]
        d = paired(xs, "bm", "bb")
        bal = [x for x in xs if x["balanced"]]
        fav = [x for x in xs if x["fav"]]
        out["corners"][period] = {**d, "hit_pick": sum(x["pick"] == x["res"] for x in xs) / len(xs),
                                  "balanced": {"n": len(bal), "hit_pick": sum(x["pick"] == x["res"] for x in bal) / len(bal) if bal else None},
                                  "favourite": {"n": len(fav), "hit_pick": sum(x["pick"] == x["res"] for x in fav) / len(fav) if fav else None},
                                  "draw_rate": sum(x["res"] == "KD" for x in xs) / len(xs)}
    out["corners"]["calibration"] = calib([{"p": x["KH"], "h": int(x["res"] == "KH")} for x in K], "p", "h")
    skill["K"] = all(out["corners"][p]["robot"] < out["corners"][p]["base"] for p in ("select", "clean"))
    out["corners"]["skill"] = skill["K"]
    for name in ONE:
        for period in ("select", "clean"):
            xs = ONE[name][period]
            hrate = sum(x["hit"] for x in xs) / len(xs)
            out["one"].setdefault(name, {})[period] = {"n": len(xs), "hit": hrate, "se": math.sqrt(hrate * (1 - hrate) / len(xs)),
                                                       "new_share": sum(x["sel"] in HCP or x["sel"] in ("KH", "KA") for x in xs) / len(xs)}
    a, ap = out["one"]["A"], out["one"]["A+"]
    one_ok = (ap["select"]["hit"] >= a["select"]["hit"]
              and ap["clean"]["hit"] >= a["clean"]["hit"] - 2 * math.hypot(a["clean"]["se"], ap["clean"]["se"]))
    out["show_percent"] = {"handicap": {c: skill[c] for c in HCP}, "corners": skill["K"]}
    out["one_extended"] = bool(one_ok and all(skill[c] for c in HCP) and skill["K"])
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for code in HCP:
        h = out["handicap"][code]
        print(f"{code:<5} " + " | ".join(f"{p}: излиза {h[p]['hit_rate']:.1%}, роботът {h[p]['said']:.1%}, Brier {h[p]['robot']:.4f} срещу лигата {h[p]['base']:.4f} (t {h[p]['t']:+.1f})"
                                     for p in ("select", "clean")) + f" -> умение {'ДА' if h['skill'] else 'НЕ'}")
    for p in ("select", "clean"):
        b = out["handicap"]["big_favourite"][p]
        m = out["handicap"].get("market_1_5", {}).get(p)
        print(f"голям фаворит ({p}): {b['n']} мача, печели с 2+ в {b['fav_by_2']:.1%}, роботът казва {b['said']:.1%}"
              + (f" | линия 1.5: {m['n']} мача, Brier робот {m['brier_robot']:.4f} / пазар {m['brier_market']:.4f}, залози {m['bets']}, доход {m['roi'] if m['roi'] is None else format(m['roi'], '+.1%')}" if m else ""))
    for p in ("select", "clean"):
        c = out["corners"][p]
        print(f"корнери {p}: {c['n']} мача | кой повече - познава {c['hit_pick']:.1%} (равни мачове {c['balanced']['hit_pick']:.1%} на {c['balanced']['n']}; "
              f"голям фаворит {c['favourite']['hit_pick']:.1%}) | равно {c['draw_rate']:.1%} | Brier {c['robot']:.4f} срещу лигата {c['base']:.4f} (t {c['t']:+.1f})")
    for name in ONE:
        print(f"едната прогноза {name}: " + " | ".join(f"{p} {out['one'][name][p]['hit']:.1%} ± {out['one'][name][p]['se']:.1%} (нови събития {out['one'][name][p]['new_share']:.0%})" for p in ("select", "clean")))
    print("процент на сайта:", out["show_percent"], "| в едната прогноза:", "ДА" if out["one_extended"] else "НЕ")
    return out


if __name__ == "__main__":
    main()
