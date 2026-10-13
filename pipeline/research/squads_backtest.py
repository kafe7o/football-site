"""
Помагат ли съставите и контузените на прогнозите? (собственикът, 2026-10-11: „изтегли ги за миналото и провери дали подобряват прогнозите“).

Данни: apif_history.db (research/apif_history_pull.py - стартови 11 и списъци с отсъстващи от API-Football, топ 5 първенства, сезони 2024/25, 2025/26, 2026/27 до днес) и
backtest_tips в football.db (walk-forward прогнозите на робота с очакваните голове xg_home / xg_away и пазара). Мачовете се свързват по първенство, дата и имена.

ПРОТОКОЛ (записан преди пускането):
Важност на играч: за всеки отбор и мач - в колко от ПОСЛЕДНИТЕ 10 мача на отбора (от изтеглените) е бил в стартовите 11: w = старти / 10; мач се ползва само ако
отборът има поне 6 предишни мача в данните.
Две мерки на отсъствието за отбор в мач:
  М_контузии - сборът на w за играчите, обявени за отсъстващи за мача (injuries, тип "Missing Fixture"; „под въпрос“ не се брои). Това е известно предния ден, т.е. ПРЕДИ записа в 07:00.
  М_състав   - сборът на w за редовните (w >= 0.5), които НЕ са в стартовите 11 на мача (контузени, наказани, почивка). Известно е чак ~час преди мача (не влиза в записа).
Поправка на очакваните голове (2 параметъра, общи за домакин и гост): xg_дом' = xg_дом * exp(a * М_дом + g * М_гост), xg_гост' = xg_гост * exp(a * М_гост + g * М_дом);
очаквано a < 0 (собственото отсъствие намалява голове), g > 0 (отсъствие при противника вдига моите). a и g се оценяват по Poisson (с малка регуляризация) САМО върху мачовете
ПРЕДИ 1-во число на месеца и се прилагат върху мачовете на месеца (walk-forward). Нова прогноза за 1/X/2, над 2.5 и двата вкарват - от същата Poisson решетка (bets/model.py score_grid).
Мери се: Brier на 1/X/2 (сборът на трите), над 2.5 и двата вкарват, срещу същата Poisson решетка с НЕпоправените очаквани голове; сдвоена разлика по мачове със стандартна грешка;
отчита се и пазарът (за ориентир). Период А = 2025-01-01..2025-06-30, период Б = от 2025-07-01 (преди 2025-01-01 само се учи).
РЕШЕНИЕ: вариант е кандидат за промяна на модела само ако 1/X/2 е по-добър с над 2 грешки и в А, и в Б, и знаците на a и g са както се очаква; иначе нищо не се променя и собственикът
получава числата. Използване на М_състав като „втори поглед“ час преди мача (не в записа) се решава отделно, пак по тези числа.
Резултатът: data/squads_backtest.json.
"""

import json
import math
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import model, teams                                  # noqa: E402

HIST = ROOT / "apif_history.db"
MAIN = ROOT / "football.db"
OUT = ROOT / "data" / "squads_backtest.json"
LEAGUES = ("E0", "SP1", "I1", "D1", "F1")
TEST_FROM = "2025-01-01"
SPLIT = "2025-07-01"
N_LAST, MIN_PREV, REGULAR, LAM = 10, 6, 0.5, 20.0


def load_features():
    """{fixture_id: {league, date, home, away, mi: (дом, гост), ml: (дом, гост)}} - мерките на отсъствие по мачове."""
    h = sqlite3.connect(HIST)
    h.row_factory = sqlite3.Row
    fx = h.execute("SELECT * FROM fixtures WHERE status = 'FT' AND fixture_id IN (SELECT fixture_id FROM pulled WHERE kind = 'both') "
                   "ORDER BY date").fetchall()
    lineups = defaultdict(dict)
    for r in h.execute("SELECT fixture_id, team_id, xi_json FROM lineups"):
        lineups[r["fixture_id"]][r["team_id"]] = {p[0] for p in json.loads(r["xi_json"])}
    inj = defaultdict(lambda: defaultdict(set))
    for r in h.execute("SELECT fixture_id, team_id, player_id FROM injuries WHERE type = 'Missing Fixture' AND player_id IS NOT NULL"):
        inj[r["fixture_id"]][r["team_id"]].add(r["player_id"])
    recent = defaultdict(lambda: deque(maxlen=N_LAST))          # team_id -> последните стартови 11
    out = {}
    for f in fx:
        xi = lineups.get(f["fixture_id"], {})
        if f["home_id"] not in xi or f["away_id"] not in xi:
            continue
        feat = {}
        ok = True
        for side, tid in (("h", f["home_id"]), ("a", f["away_id"])):
            hist = recent[tid]
            if len(hist) < MIN_PREV:
                ok = False
                break
            w = defaultdict(float)
            for s in hist:
                for p in s:
                    w[p] += 1.0 / len(hist)
            feat[side] = (sum(w.get(p, 0.0) for p in inj[f["fixture_id"]].get(tid, ())),
                          sum(v for p, v in w.items() if v >= REGULAR and p not in xi[tid]))
        if ok:
            out[f["fixture_id"]] = {"league": f["league"], "date": f["date"][:10], "home": f["home"], "away": f["away"],
                                    "mi": (feat["h"][0], feat["a"][0]), "ml": (feat["h"][1], feat["a"][1])}
        for tid in (f["home_id"], f["away_id"]):                 # след мача: съставът му влиза в „последните 10“
            recent[tid].append(xi[tid])
    return out


def match_rows(features):
    """Свързва с backtest_tips: [{...мерки, xg_home, xg_away, пазар, резултат}]."""
    m = sqlite3.connect(MAIN)
    m.row_factory = sqlite3.Row
    by_key = defaultdict(list)
    for fid, f in features.items():
        by_key[(f["league"], f["date"])].append((fid, f))
    rows, unmatched = [], 0
    for r in m.execute("SELECT league, date, home, away, probs_json, hg, ag FROM backtest_tips WHERE basis = 'model' AND league IN (%s) AND date >= '2024-08-01' "
                       "AND hg IS NOT NULL" % ",".join("?" * len(LEAGUES)), LEAGUES):
        p = json.loads(r["probs_json"])
        rb = p["robot"]
        if rb.get("xg_home") is None:
            continue
        best = None
        from datetime import date, timedelta
        d0 = date.fromisoformat(r["date"])
        for dd in (0, -1, 1):
            for fid, f in by_key.get((r["league"], (d0 + timedelta(days=dd)).isoformat()), []):
                sh, sa = teams.similar(r["home"], f["home"]), teams.similar(r["away"], f["away"])
                if sh >= 0.5 and sa >= 0.5 and (best is None or sh + sa > best[0]):
                    best = (sh + sa, f)
        if best is None:
            unmatched += 1
            continue
        f = best[1]
        rows.append({"league": r["league"], "date": r["date"], "home": r["home"], "away": r["away"], "mi": f["mi"], "ml": f["ml"], "xh": rb["xg_home"], "xa": rb["xg_away"],
                     "hg": r["hg"], "ag": r["ag"], "market": p.get("market")})
    return rows, unmatched


def probs(xh, xa):
    g = model.score_grid(xh, xa)
    k = np.arange(g.shape[0])
    tot = k[:, None] + k[None, :]
    return (float(np.tril(g, -1).sum()), float(np.trace(g)), float(np.triu(g, 1).sum()), float(g[tot >= 3].sum()), float(g[1:, 1:].sum()))


def fit(rows, key):
    """a, g по Poisson върху rows (мярка key = 'mi' или 'ml'), с малка регуляризация."""
    xh = np.array([r["xh"] for r in rows]); xa = np.array([r["xa"] for r in rows])
    hg = np.array([r["hg"] for r in rows]); ag = np.array([r["ag"] for r in rows])
    mh = np.array([r[key][0] for r in rows]); ma = np.array([r[key][1] for r in rows])

    def nll(t):
        a, g = t
        lh = xh * np.exp(a * mh + g * ma)
        la = xa * np.exp(a * ma + g * mh)
        return float((lh - hg * np.log(lh)).sum() + (la - ag * np.log(la)).sum() + LAM * (a * a + g * g))
    res = minimize(nll, [0.0, 0.0], method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-6})
    return float(res.x[0]), float(res.x[1])


def months(first, last):
    y, mo = int(first[:4]), int(first[5:7])
    while f"{y:04d}-{mo:02d}-01" <= last:
        yield f"{y:04d}-{mo:02d}-01"
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1


def next_month(m0):
    y, mo = int(m0[:4]), int(m0[5:7]) + 1
    return f"{y + (mo == 13):04d}-{(mo - 1) % 12 + 1:02d}-01"


def score(rows, preds):
    """Brier за 1/X/2 (сумата на трите), над 2.5, двата вкарват на мач."""
    out = []
    for r, p in zip(rows, preds):
        y1, yx, y2 = r["hg"] > r["ag"], r["hg"] == r["ag"], r["hg"] < r["ag"]
        out.append(((p[0] - y1) ** 2 + (p[1] - yx) ** 2 + (p[2] - y2) ** 2, (p[3] - (r["hg"] + r["ag"] >= 3)) ** 2,
                    (p[4] - (r["hg"] > 0 and r["ag"] > 0)) ** 2))
    return np.array(out)


def main():
    features = load_features()
    rows, unmatched = match_rows(features)
    rows.sort(key=lambda r: r["date"])
    print(f"мачове със състави: {len(features)}; свързани с прогнозите на робота: {len(rows)} (несвързани {unmatched})")
    res = {"matches": len(rows), "variants": {}}
    base_all = {}
    for key, name in (("mi", "контузии (преди записа)"), ("ml", "състав (час преди мача)")):
        test_rows, base_p, adj_p = [], [], []
        coefs = []
        for m0 in months(TEST_FROM, rows[-1]["date"]):
            m1 = next_month(m0)
            train = [r for r in rows if r["date"] < m0]
            test = [r for r in rows if m0 <= r["date"] < m1]
            if len(train) < 300 or not test:
                continue
            a, g = fit(train, key)
            coefs.append((m0, a, g, len(train)))
            for r in test:
                test_rows.append(r)
                base_p.append(probs(r["xh"], r["xa"]))
                adj_p.append(probs(r["xh"] * math.exp(a * r[key][0] + g * r[key][1]), r["xa"] * math.exp(a * r[key][1] + g * r[key][0])))
        sb, sa_ = score(test_rows, base_p), score(test_rows, adj_p)
        v = {"n": len(test_rows), "coefs": coefs[-1][1:3] if coefs else None, "periods": {}}
        print(f"\n=== {name}: {len(test_rows)} мача; последни a = {coefs[-1][1]:+.3f}, g = {coefs[-1][2]:+.3f} (на {coefs[-1][3]} мача)")
        for lab, sel in (("А", lambda r: r["date"] < SPLIT), ("Б", lambda r: r["date"] >= SPLIT), ("всичко", lambda r: True)):
            idx = [i for i, r in enumerate(test_rows) if sel(r)]
            if len(idx) < 50:
                continue
            d = sa_[idx] - sb[idx]
            n = len(idx)
            line = {"n": n}
            txt = []
            for j, nm in enumerate(("1/X/2", "над 2.5", "двата вкарват")):
                m = float(d[:, j].mean())
                se = float(d[:, j].std(ddof=1) / math.sqrt(n))
                line[nm] = {"base": float(sb[idx][:, j].mean()), "adj": float(sa_[idx][:, j].mean()), "diff": m, "se": se}
                txt.append(f"{nm}: {sb[idx][:, j].mean():.4f} -> {sa_[idx][:, j].mean():.4f} ({m:+.5f} ± {se:.5f}, {m / se:+.1f} грешки)")
            mk = [i for i in idx if test_rows[i]["market"] and "X" in test_rows[i]["market"]]
            if mk:
                mp = [(test_rows[i]["market"]["1"], test_rows[i]["market"]["X"], test_rows[i]["market"]["2"], 0, 0) for i in mk]
                mb = float(score([test_rows[i] for i in mk], mp)[:, 0].mean())
                line["market_1x2"] = mb
                txt.append(f"пазар 1/X/2: {mb:.4f}")
            print(f"  {lab} ({n}): " + " | ".join(txt))
            v["periods"][lab] = line
        res["variants"][name] = v
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("\nзаписано:", OUT)


if __name__ == "__main__":
    main()
