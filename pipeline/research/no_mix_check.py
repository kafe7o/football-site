"""
Прогнозите за един мач да не се омесват (майсторът, 2026-10-08): „като ми даде един под, един над, аз съм на чука и наковалнята къде да сложа
парите; над 2.5 + под 3.5 иска точно 3 гола - шанс ~1 към 6“.

Това НЕ е избор между варианти - правилото е на майстора (bets/robot.py: opposes, safe_by_odds(against=...)); тук се мери цената му.
ПРОТОКОЛ: backtest_tips (walk-forward, само с модел), 2023-07-01 до днес; едната прогноза - robot.one_pick (без картони и корнери - за тях
назад няма шансове), по-сигурната - robot.safe_by_odds със и без against=[едната]. Мери се: колко често преди правилото двете са в обратна
посока на головете и колко излизат заедно; колко мача имат по-сигурна преди и след правилото и колко често излиза (избор до 2025-07-01,
чиста проверка от там). Проверка: след правилото обратна посока няма никъде. Резултатът: data/no_mix_check.json.
ДОПЪЛНЕНИЕ 08.10 вечерта (собственикът: „всичко както майсторът е казал, от сега и точка“): (1) „двата вкарват“ с линия, която иска
почти точен резултат (GG + под 2.5/1.5, NG + над 2.5/3.5) - също не се дава; (2) рисковата не противоречи на едната и по-сигурната
(майсторът, 02.10; robot.RISKY_CONSISTENT = True). Мери се и рисковата преди и след: колко мача имат рискова, колко излиза.
"""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, robot                                 # noqa: E402

OUT = ROOT / "data" / "no_mix_check.json"
SELECT_END = "2025-07-01"


def stat(n, h):
    p = h / n if n else None
    return {"n": n, "hit": p, "se": math.sqrt(p * (1 - p) / n) if n else None}


def main():
    conn = db.init()
    acc = {per: {"matches": 0, "both": 0, "conflict": 0, "both_hit": 0, "one_hit": 0, "safe_hit": 0,
                 "before_n": 0, "before_h": 0, "after_n": 0, "after_h": 0, "left_conflict": 0,
                 "rk_before_n": 0, "rk_before_h": 0, "rk_contra": 0, "rk_after_n": 0, "rk_after_h": 0, "rk_left": 0} for per in ("select", "clean")}
    for r in conn.execute("SELECT league, date, probs_json, prices_json, hg, ag FROM backtest_tips WHERE basis = 'model' "
                          "AND date >= '2023-07-01' AND hg IS NOT NULL"):
        p = json.loads(r["probs_json"])["robot"]
        if p.get("xg_home") is None:
            continue
        a = acc["select" if r["date"] < SELECT_END else "clean"]
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {}) if r["prices_json"] else {}
        one = robot.one_pick(p, avg, None, r["league"])
        before = robot.safe_by_odds(p, avg, r["league"])
        after = robot.safe_by_odds(p, avg, r["league"], against=[one["sel"]] if one else ())
        a["matches"] += 1
        hit = lambda x: bool(robot.hit_any(x["sel"], r["hg"], r["ag"]))
        # рисковата: преди (без условието) и след (само знак, който другите две позволяват)
        robot.RISKY_CONSISTENT = False
        rk0 = robot.risky_sign(p, avg, r["league"])
        robot.RISKY_CONSISTENT = True
        others = [x["sel"] for x in (after, one) if x]
        rk1 = robot.risky_sign(p, avg, r["league"], others)
        if rk0:
            a["rk_before_n"] += 1
            a["rk_before_h"] += hit(rk0)
            if any(rk0["sel"] not in robot.ALLOWS.get(o, {"1", "X", "2"}) for o in [x["sel"] for x in (before, one) if x]):
                a["rk_contra"] += 1
        if rk1:
            a["rk_after_n"] += 1
            a["rk_after_h"] += hit(rk1)
            if any(rk1["sel"] not in robot.ALLOWS.get(o, {"1", "X", "2"}) for o in others):
                a["rk_left"] += 1
        if before:
            a["before_n"] += 1
            a["before_h"] += hit(before)
        if after:
            a["after_n"] += 1
            a["after_h"] += hit(after)
            if one and robot.opposes(one["sel"], after["sel"]):
                a["left_conflict"] += 1
        if one and before:
            a["both"] += 1
            if robot.opposes(one["sel"], before["sel"]):
                a["conflict"] += 1
                a["one_hit"] += hit(one)
                a["safe_hit"] += hit(before)
                a["both_hit"] += hit(one) and hit(before)
    out = {}
    for per, a in acc.items():
        c = a["conflict"]
        out[per] = {"matches": a["matches"], "one_and_safer": a["both"], "conflict": c, "conflict_share": c / a["both"],
                    "one_hit_in_conflict": a["one_hit"] / c, "safe_hit_in_conflict": a["safe_hit"] / c, "both_hit_in_conflict": a["both_hit"] / c,
                    "safer_before": {**stat(a["before_n"], a["before_h"]), "coverage": a["before_n"] / a["matches"]},
                    "safer_after": {**stat(a["after_n"], a["after_h"]), "coverage": a["after_n"] / a["matches"]},
                    "left_conflict": a["left_conflict"],
                    "risky_before": {**stat(a["rk_before_n"], a["rk_before_h"]), "coverage": a["rk_before_n"] / a["matches"],
                                     "contradicting": a["rk_contra"]},
                    "risky_after": {**stat(a["rk_after_n"], a["rk_after_h"]), "coverage": a["rk_after_n"] / a["matches"]},
                    "risky_left_contra": a["rk_left"]}
        print(f"{per:<6} в обратна посока {c} от {a['both']} ({c / a['both']:.1%}); едната излиза {a['one_hit'] / c:.0%}, по-сигурната {a['safe_hit'] / c:.0%}, "
              f"двете заедно {a['both_hit'] / c:.0%} | по-сигурна: преди {a['before_n'] / a['matches']:.0%} от мачовете, излиза {a['before_h'] / a['before_n']:.1%}; "
              f"след {a['after_n'] / a['matches']:.0%}, излиза {a['after_h'] / a['after_n']:.1%} | останали обратни: {a['left_conflict']}")
        print(f"       рискова: преди {a['rk_before_n'] / a['matches']:.0%} от мачовете, излиза {a['rk_before_h'] / a['rk_before_n']:.1%}, противоречи в "
              f"{a['rk_contra']} ({a['rk_contra'] / max(1, a['rk_before_n']):.0%}); след {a['rk_after_n'] / a['matches']:.0%}, излиза "
              f"{a['rk_after_h'] / max(1, a['rk_after_n']):.1%} | останали противоречия: {a['rk_left']}")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    main()
