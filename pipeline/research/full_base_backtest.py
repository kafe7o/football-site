"""
Пълната база в мозъка на робота (указание на професионалиста, 2026-10-01).

Той казва: „даваме му пълна база с корнери, голове, картони, знаци, форми на двата отбора, липсващи
играчи - абсолютно всичко - и той сам да определи точната прогноза“. Сега шансовете на робота идват
само от головете (и xG за топ 5) с тегло по давност. Въпросът: стават ли по-точни, ако роботът вижда
и останалото?

ПРОТОКОЛ (записан преди пускането):
Данни: backtest_tips (роботът назад, walk-forward по месеци - очакваните голове на домакина и госта
от сегашния модел, xg_home / xg_away), истинските резултати; football.db - мачовете и статистиката им
от 2019-07-01 (за формата преди 2023-07). Статистика (удари, в целта, корнери, картони) има само за
22-те лиги на football-data; в другите тези признаци са 0 („като средното“).

Признаци за всеки отбор, само от мачовете ПРЕДИ мача (последните 10, формата - последните 5), като
разлика от средното на лигата за последните 365 дни преди мача:
  удари в целта - направени (атакуващият) и допуснати (защитаващият се);
  удари - направени и допуснати; корнери - спечелени и допуснати; жълти картони - на атакуващия;
  голове в последните 5 - вкарани и допуснати; точки на мач в последните 5 - разлика между отборите;
  почивка в дни (3-14) - разлика; директни срещи - средна голова разлика в последните 4 (0, ако няма).
Липсващите играчи НЕ са в безплатните данни - не участват.

Модел F („пълна база“): очакваните голове на отбора = очакваните от сегашния модел × exp(β·признаците +
поправка за домакин/гост); β - Poisson с L2 наказание (α = 5 върху стандартизираните признаци), един
вектор за всички лиги. Walk-forward: за всеки месец от 2023-10 β се учи САМО на мачовете преди 1-во
число (от 2023-07 нататък) и се прилага за месеца.
Сравнение на едни и същи мачове със сегашния робот G: Brier за 1/X/2 (главното), Brier за над/под 2.5,
сдвоен t по мачове. ИЗБОР: 2023-10 до 2025-06; ЧИСТА ПРОВЕРКА: от 2025-07-01.
ПРИЕМА СЕ, ако F е по-точен за 1/X/2 с t < -2 И в избора, И в чистата проверка, И за над/под 2.5 не е
по-лош (t < +2) в двата периода. Иначе мозъкът на робота остава същият.
Резултатът: data/full_base_backtest.json.

РЕЗУЛТАТ (2026-10-01): НЕ Е ПРИЕТО. 35 992 мача. 1/X/2 Brier: избор 0.6127 -> 0.6120 (t -2.6), чиста
0.6131 -> 0.6127 (t -1.1) - в чистата проверка подобрението не се потвърждава. Над/под 2.5: 0.2460 ->
0.2455 (t -3.0) и 0.2461 -> 0.2453 (t -4.2) - малко по-точно и в двата периода, но условието беше 1/X/2.
Тоест пълната база почти не мести шансовете (около 0.3% по-малка грешка при головете); ако се пробва
само за головете, трябва проверка на НОВИ мачове (тези вече са видени).
"""

import json
import math
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, model                           # noqa: E402

OUT = ROOT / "data" / "full_base_backtest.json"
HIST_FROM, EVAL_FROM, SELECT_END = "2019-07-01", "2023-10-01", "2025-07-01"
ALPHA = 5.0
LAST, FORM = 10, 5
STATS = [("st", "hst", "ast"), ("sh", "hs", "as_"), ("co", "hc", "ac"), ("ye", "hy", "ay")]
FEATURES = ["st_for", "st_against", "sh_for", "sh_against", "co_for", "co_against", "ye_for",
            "g_for", "g_against", "ppg_diff", "rest_diff", "h2h"]


def team_rows(conn):
    """Мачовете като редове по отбор: отборът, противникът, колко вкара/допусна, удари, корнери, картони."""
    m = pd.read_sql_query(
        """SELECT m.id, m.league, m.date, m.home_team, m.away_team, m.fthg, m.ftag,
                  s.hs, s.as_, s.hst, s.ast, s.hc, s.ac, s.hy, s.ay
             FROM matches m LEFT JOIN match_stats s ON s.match_id = m.id
            WHERE m.fthg IS NOT NULL AND m.date >= ? ORDER BY m.date""", conn, params=(HIST_FROM,))
    m["date"] = pd.to_datetime(m["date"])
    home = pd.DataFrame({"id": m["id"], "league": m["league"], "date": m["date"], "team": m["home_team"],
                         "opp": m["away_team"], "home": 1, "gf": m["fthg"], "ga": m["ftag"],
                         "st_f": m["hst"], "st_a": m["ast"], "sh_f": m["hs"], "sh_a": m["as_"],
                         "co_f": m["hc"], "co_a": m["ac"], "ye_f": m["hy"]})
    away = pd.DataFrame({"id": m["id"], "league": m["league"], "date": m["date"], "team": m["away_team"],
                         "opp": m["home_team"], "home": 0, "gf": m["ftag"], "ga": m["fthg"],
                         "st_f": m["ast"], "st_a": m["hst"], "sh_f": m["as_"], "sh_a": m["hs"],
                         "co_f": m["ac"], "co_a": m["hc"], "ye_f": m["ay"]})
    t = pd.concat([home, away], ignore_index=True).sort_values(["team", "date", "id"]).reset_index(drop=True)
    t["pts"] = np.where(t["gf"] > t["ga"], 3, np.where(t["gf"] == t["ga"], 1, 0))
    return m, t


def rolling_features(m, t):
    """За всеки мач и отбор - средните от ПРЕДИШНИТЕ мачове (shift(1): без самия мач)."""
    g = t.groupby("team", sort=False)
    for col in ("st_f", "st_a", "sh_f", "sh_a", "co_f", "co_a", "ye_f"):
        t[col + "_r"] = g[col].transform(lambda s: s.shift(1).rolling(LAST, min_periods=3).mean())
    for col in ("gf", "ga", "pts"):
        t[col + "_r"] = g[col].transform(lambda s: s.shift(1).rolling(FORM, min_periods=3).mean())
    t["rest"] = g["date"].transform(lambda s: (s - s.shift(1)).dt.days).clip(3, 14)
    # средното на лигата за 365 дни преди мача (по мачове, без самия ден)
    lg = m.sort_values("date").set_index("date")
    avg = {}
    for code, x in lg.groupby("league"):
        x = x.copy()
        x["st"], x["sh"], x["co"], x["ye"] = (x["hst"] + x["ast"]) / 2, (x["hs"] + x["as_"]) / 2, (x["hc"] + x["ac"]) / 2, (x["hy"] + x["ay"]) / 2
        x["g"] = (x["fthg"] + x["ftag"]) / 2
        r = x[["st", "sh", "co", "ye", "g"]].rolling("365D", closed="left", min_periods=50).mean()
        r["id"] = x["id"].to_numpy()
        avg[code] = r.reset_index(drop=True).set_index("id")
    lavg = pd.concat(avg.values())
    return t, lavg


def h2h_diffs(m):
    """Средната голова разлика в последните 4 директни срещи (от гледна точка на домакина), преди мача."""
    past = defaultdict(lambda: deque(maxlen=4))
    out = {}
    for r in m.itertuples():
        key = tuple(sorted((r.home_team, r.away_team)))
        xs = past[key]
        out[r.id] = float(np.mean([d if h == r.home_team else -d for h, d in xs])) if xs else 0.0
        past[key].append((r.home_team, r.fthg - r.ftag))
    return out


def build(conn):
    m, t = team_rows(conn)
    t, lavg = rolling_features(m, t)
    h2h = h2h_diffs(m)
    key = t.set_index(["id", "home"])
    bt = conn.execute("SELECT league, date, home, away, probs_json, hg, ag FROM backtest_tips "
                      "WHERE basis = 'model' AND date >= '2023-07-01'").fetchall()
    ids = {(r.league, r.date.strftime("%Y-%m-%d"), r.home_team, r.away_team): r.id for r in m.itertuples()}
    rows = []
    for b in bt:
        mid = ids.get((b["league"], b["date"], b["home"], b["away"]))
        p = json.loads(b["probs_json"])["robot"]
        if mid is None or p.get("xg_home") is None or mid not in lavg.index:
            continue
        la = lavg.loc[mid]
        if isinstance(la, pd.DataFrame):
            la = la.iloc[0]
        th, ta = key.loc[(mid, 1)], key.loc[(mid, 0)]

        def dev(v, base):
            return 0.0 if pd.isna(v) or pd.isna(base) else float(v - base)

        def side(att, dfn, sign):
            return [dev(att["st_f_r"], la["st"]), dev(dfn["st_a_r"], la["st"]), dev(att["sh_f_r"], la["sh"]),
                    dev(dfn["sh_a_r"], la["sh"]), dev(att["co_f_r"], la["co"]), dev(dfn["co_a_r"], la["co"]),
                    dev(att["ye_f_r"], la["ye"]), dev(att["gf_r"], la["g"]), dev(dfn["ga_r"], la["g"]),
                    dev(att["pts_r"], dfn["pts_r"]) if not (pd.isna(att["pts_r"]) or pd.isna(dfn["pts_r"])) else 0.0,
                    dev(att["rest"], dfn["rest"]) if not (pd.isna(att["rest"]) or pd.isna(dfn["rest"])) else 0.0,
                    sign * h2h.get(mid, 0.0)]

        rows.append({"date": b["date"], "league": b["league"], "hg": b["hg"], "ag": b["ag"],
                     "lh": p["xg_home"], "la": p["xg_away"], "xh": side(th, ta, 1), "xa": side(ta, th, -1)})
    return rows


def fit(train):
    """β и поправките за домакин/гост: Poisson с изместване log(очакваните по сегашния модел) и L2."""
    X = np.array([r["xh"] for r in train] + [r["xa"] for r in train])
    y = np.array([r["hg"] for r in train] + [r["ag"] for r in train], dtype=float)
    off = np.log(np.array([r["lh"] for r in train] + [r["la"] for r in train]))
    side = np.array([1.0] * len(train) + [0.0] * len(train))
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    A = np.column_stack([side, 1 - side, Z])
    k = A.shape[1]

    def f(w):
        eta = off + A @ w
        lam = np.exp(eta)
        pen = ALPHA * np.sum(w[2:] ** 2)
        return float(np.sum(lam - y * eta) + pen), A.T @ (lam - y) + np.r_[0, 0, 2 * ALPHA * w[2:]]

    w = minimize(f, np.zeros(k), jac=True, method="L-BFGS-B").x
    return w, mu, sd


def apply(w, mu, sd, r):
    zh, za = (np.array(r["xh"]) - mu) / sd, (np.array(r["xa"]) - mu) / sd
    return r["lh"] * math.exp(w[0] + zh @ w[2:]), r["la"] * math.exp(w[1] + za @ w[2:])


def probs(lh, la):
    grid = model.score_grid(lh, la)
    k = np.arange(grid.shape[0])
    total = k[:, None] + k[None, :]
    return (float(np.tril(grid, -1).sum()), float(np.trace(grid)), float(np.triu(grid, 1).sum())), float(grid[total >= 3].sum())


def paired(d):
    n = len(d)
    mu = sum(d) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in d) / (n - 1))
    return {"n": n, "diff": mu, "se": sd / math.sqrt(n), "t": mu / (sd / math.sqrt(n)) if sd else 0.0}


def main():
    conn = db.init()
    rows = build(conn)
    print(f"мачове с модел и признаци: {len(rows)}", flush=True)
    months = sorted({r["date"][:7] for r in rows if r["date"] >= EVAL_FROM})
    res, betas = [], {}
    for mo in months:
        start = mo + "-01"
        train = [r for r in rows if r["date"] < start]
        test = [r for r in rows if r["date"][:7] == mo]
        w, mu, sd = fit(train)
        betas[mo] = dict(zip(["home", "away"] + FEATURES, np.round(w, 4).tolist()))
        for r in test:
            res_ = 0 if r["hg"] > r["ag"] else (1 if r["hg"] == r["ag"] else 2)
            over = int(r["hg"] + r["ag"] >= 3)
            pg, og = probs(r["lh"], r["la"])
            pf, of = probs(*apply(w, mu, sd, r))
            bg = sum((pg[i] - (i == res_)) ** 2 for i in range(3))
            bf = sum((pf[i] - (i == res_)) ** 2 for i in range(3))
            res.append({"date": r["date"], "league": r["league"], "bg": bg, "bf": bf,
                        "og": (og - over) ** 2, "of": (of - over) ** 2})
    out = {"alpha": ALPHA, "features": FEATURES, "periods": {}, "betas_last": betas[months[-1]]}
    ok = True
    for name, keep in (("select", lambda x: x["date"] < SELECT_END), ("clean", lambda x: x["date"] >= SELECT_END)):
        xs = [x for x in res if keep(x)]
        a = paired([x["bf"] - x["bg"] for x in xs])
        o = paired([x["of"] - x["og"] for x in xs])
        out["periods"][name] = {"n": len(xs), "brier_G": sum(x["bg"] for x in xs) / len(xs),
                                "brier_F": sum(x["bf"] for x in xs) / len(xs), "x12": a,
                                "ou_G": sum(x["og"] for x in xs) / len(xs), "ou_F": sum(x["of"] for x in xs) / len(xs), "ou": o}
        ok = ok and a["t"] < -2 and o["t"] < 2
        print(f"{name:<6} {len(xs):>6} мача | 1/X/2 Brier: сега {out['periods'][name]['brier_G']:.4f}, с пълната база "
              f"{out['periods'][name]['brier_F']:.4f} (разлика {a['diff']:+.4f}, t {a['t']:+.1f}) | над/под 2.5: "
              f"{out['periods'][name]['ou_G']:.4f} -> {out['periods'][name]['ou_F']:.4f} (t {o['t']:+.1f})")
    out["accepted"] = ok
    print("тежестите (последният месец, на стандартизираните признаци):", betas[months[-1]])
    print("ПРИЕТО" if ok else "НЕ Е ПРИЕТО - мозъкът на робота остава същият")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    main()
