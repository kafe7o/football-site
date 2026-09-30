"""
Картони и корнери назад във времето - знае ли роботът нещо за тях (2026-09-30).

ПРОТОКОЛ (записан преди пускането):
Данни: football.db, 22-те лиги на football-data (само те имат статистика на мача), мачовете от
2023-07-01 до днес. Модел: bets/extras.py - Poisson с регуляризация върху жълтите картони
(корнерите) на домакина и госта, отрицателно биномно за сбора, множител за съдията (Англия и
Шотландия). Walk-forward: за всяка лига и всеки месец - обучение само на мачовете преди 1-во число
(последните 4 години), прогноза за мачовете на месеца.

За всеки мач: основната линия (половинката най-близо до очаквания брой), шансът за над, изборът
(над, ако шансът е поне 50%). Мери се:
  - колко често изборът излиза;
  - Brier на шанса за над срещу БАЗАТА - честотата на „над“ за същата линия в лигата в
    мачовете на обучението (тоест „без модел, само средното на лигата“);
  - средната грешка на очаквания брой срещу базата (средното на лигата).
ИЗБОР: 2023/24 и 2024/25; ЧИСТА ПРОВЕРКА: от 2025-07-01. Процентът за картони/корнери се
показва като оценка на робота само ако Brier е по-добър от базата И в двата периода; иначе на
сайта стои само очакваният брой с бележка, че умение не е доказано.

Резултатът: data/extras_backtest.json.
"""

import json
import math
import sys
from datetime import date
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, extras                         # noqa: E402
from bets.leagues import FD                         # noqa: E402

START = "2023-07-01"
SELECT_END = "2025-07-01"
OUT = ROOT / "data" / "extras_backtest.json"
LAST_ROWS = None          # прогнозите мач по мач от последното пускане - ползва ги one_pick_backtest.py


def months(first, last):
    y, m = int(first[:4]), int(first[5:7])
    while f"{y:04d}-{m:02d}-01" <= last:
        yield f"{y:04d}-{m:02d}-01"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def run_league(code):
    conn = db.init()
    out = []
    for kind, (hcol, acol, _) in extras.KINDS.items():
        rows = conn.execute(
            f"""SELECT m.date, m.home_team, m.away_team, m.referee, s.{hcol} h, s.{acol} a FROM matches m
                 JOIN match_stats s ON s.match_id = m.id
                WHERE m.league = ? AND m.date >= ? AND s.{hcol} IS NOT NULL AND s.{acol} IS NOT NULL
                ORDER BY m.date""", (code, START)).fetchall()
        for m0 in months(START, date.today().isoformat()):
            y, m = int(m0[:4]), int(m0[5:7])
            m1 = f"{y + (m == 12):04d}-{(m % 12) + 1:02d}-01"
            month = [r for r in rows if m0 <= r["date"] < m1]
            if not month:
                continue
            since = f"{y - 4}-{m0[5:]}"
            fitted, k = extras.fit(conn, code, kind, m0, since=since)
            if fitted is None:
                continue
            train = extras.history(conn, code, kind, since=since, before=m0)
            totals = (train["fthg"] + train["ftag"]).to_numpy()
            league_mean = float(totals.mean())
            for r in month:
                factor = extras.referee_factor(conn, code, r["referee"], m0, kind)[0] if kind == "cards" else 1.0
                pr = extras.predict(fitted, k, r["home_team"], r["away_team"], factor)
                if pr is None:
                    continue
                total = r["h"] + r["a"]
                base = float((totals > pr["line"]).mean())
                out.append({"league": code, "kind": kind, "date": r["date"], "home": r["home_team"],
                            "away": r["away_team"], "line": pr["line"],
                            "p": pr["over"], "base": base, "exp": pr["total"], "mean": league_mean,
                            "total": total, "over": int(total > pr["line"]), "pick": pr["pick"],
                            "ref": bool(r["referee"]) and factor != 1.0})
    conn.close()
    print(f"{code}: {len(out)} прогнози", flush=True)
    return out


def summarize(rows):
    n = len(rows)
    if not n:
        return None
    brier = sum((r["p"] - r["over"]) ** 2 for r in rows) / n
    base = sum((r["base"] - r["over"]) ** 2 for r in rows) / n
    diffs = [((r["p"] - r["over"]) ** 2) - ((r["base"] - r["over"]) ** 2) for r in rows]
    mu = sum(diffs) / n
    sd = math.sqrt(sum((d - mu) ** 2 for d in diffs) / max(1, n - 1))
    hits = sum(1 for r in rows if (r["pick"] == "O") == bool(r["over"]))
    return {"n": n, "hit": hits / n, "brier": brier, "brier_base": base,
            "t": mu / (sd / math.sqrt(n)) if sd else 0.0,
            "mae": sum(abs(r["exp"] - r["total"]) for r in rows) / n,
            "mae_base": sum(abs(r["mean"] - r["total"]) for r in rows) / n,
            "p_mean": sum(r["p"] for r in rows) / n, "over_rate": sum(r["over"] for r in rows) / n,
            "avg_total": sum(r["total"] for r in rows) / n}


def main(codes=None):
    codes = list(codes or FD)
    with Pool(4) as pool:
        parts = pool.map(run_league, codes)
    rows = [r for p in parts for r in p]
    global LAST_ROWS
    LAST_ROWS = rows
    out = {"generated": date.today().isoformat(), "start": START, "select_end": SELECT_END, "kinds": {}}
    for kind in extras.KINDS:
        ks = [r for r in rows if r["kind"] == kind]
        per = {}
        for scope in ["ALL"] + codes:
            xs = ks if scope == "ALL" else [r for r in ks if r["league"] == scope]
            per[scope] = {"all": summarize(xs),
                          "select": summarize([r for r in xs if r["date"] < SELECT_END]),
                          "clean": summarize([r for r in xs if r["date"] >= SELECT_END])}
        a = per["ALL"]
        skill = bool(a["select"] and a["clean"] and a["select"]["brier"] < a["select"]["brier_base"]
                     and a["clean"]["brier"] < a["clean"]["brier_base"])
        # калибрация: групи по шанса за над
        buckets = []
        for lo in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
            b = [r for r in ks if lo <= r["p"] < lo + 0.1]
            if b:
                buckets.append({"from": lo, "n": len(b), "p": sum(r["p"] for r in b) / len(b),
                                "actual": sum(r["over"] for r in b) / len(b)})
        out["kinds"][kind] = {"skill": skill, "leagues": per, "calibration": buckets}
        print(f"\n{kind}: умение {'ДА' if skill else 'НЕ'}")
        for period in ("select", "clean"):
            s = a[period]
            print(f"  {period:<6} {s['n']:>6} мача | изборът познава {s['hit']:.1%} | Brier {s['brier']:.4f} срещу база "
                  f"{s['brier_base']:.4f} (t {s['t']:+.1f}) | грешка на броя {s['mae']:.2f} срещу {s['mae_base']:.2f}")
        for b in buckets:
            print(f"    шанс {b['from']:.0%}-{b['from'] + .1:.0%}: {b['n']:>5} мача, обещано {b['p']:.1%}, излиза {b['actual']:.1%}")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    main(sys.argv[1:] or None)
