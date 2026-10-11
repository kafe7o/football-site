"""
Всякакви вариации на прогнозите - кое излиза, кога, защо, и кой вариант е най-добър срещу сегашния (собственикът, 2026-10-11: „направи анализ и на подобни прогнози,
виж какво, как, кога и защо излиза, пробвай всякакви вариации и ми дай най-добрата и сегашната и логиката“).

Данни: backtest_tips (walk-forward), мачовете с пазар над/под 2.5 (22-те лиги), 2023-07..днес. За всеки мач 14 събития: 1, X, 2, 1X, X2, 12, над/под 1.5 / 2.5 / 3.5, двата вкарват да/не.
Шанс: на робота, на пазара (от средните коефициенти без маржа; над/под 1.5 / 3.5 и двата вкарват - от λ по Poisson), смес (половин-половин).
Цена: истинската средна на букмейкърите (1/X/2, двоен шанс, над/под 2.5), иначе оценка от пазара с 5% марж (research/safe_legs_backtest.py).

ПРОТОКОЛ (записан преди пускането):
Решетка от варианти (всички комбинации): шанс по {робот, пазар, смес} x видове {всички, само голове, голове + двоен шанс} x праг на шанса {65, 70, 75, 80%} x минимална цена
{без, 1.25, 1.40} x {всякакви, само когато роботът и пазарът са в рамките на 5 пункта} x {всички дни, само петък-неделя} = 432 варианта; за всеки - по мач най-вероятното
допустимо събитие, после топ шанс (третината с най-голям шанс в първенството за деня) и: единични, колонки по 2 и колонки по 3 (един мач на първенство, до 4 на ден).
За сравнение: СЕГАШНОТО правило (bets/robot.py one_pick с цена от 1.40, която роботът вижда - букмейкър или своята 1/шанс) с истински цени.
Избор: най-доброто по връщане от 1 € в периода на ИЗБОРА (до 2025-07-01), с поне 300 залога; отделно - най-доброто по излизане сред тези, които връщат поне колкото
сегашното. ЧИСТАТА ПРОВЕРКА (от 2025-07-01) се отчита само за избраните - тя е истинската оценка (432 варианта = най-добрият в избора е оптимистичен).
Плюс описание „кога и защо“: за избрания и за сегашния - излизане по вид събитие, по цена, по ден, по това дали роботът е над пазара.
Описателно - правилата на майстора не се пипат. Резултатът: data/variations_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from datetime import date
from itertools import product
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import robot, sure                                     # noqa: E402
from research import goal_lines_backtest as gl                   # noqa: E402
from research import safe_legs_backtest as sl                    # noqa: E402

OUT = ROOT / "data" / "variations_backtest.json"
SELECT_END = sl.SELECT_END
EV = ("1", "X", "2", "1X", "X2", "12", "O15", "U15", "O", "U", "O35", "U35", "GG", "NG")
FAMILIES = {"всички": set(EV), "само голове": {"O15", "U15", "O", "U", "O35", "U35"}, "голове + двоен шанс": {"O15", "U15", "O", "U", "O35", "U35", "1X", "X2", "12"}}
SOURCES = ("робот", "пазар", "смес")
THRESH = (0.65, 0.70, 0.75, 0.80)
FLOORS = (0.0, 1.25, 1.40)
MIN_N = 300


def build():
    days = sl.load()
    rows = []
    for day, ms in sorted(days.items()):
        wd = date.fromisoformat(day).weekday()
        for m in ms:
            q = gl.market_probs(m)
            share = m["rb"]["xg_home"] / (m["rb"]["xg_home"] + m["rb"]["xg_away"])
            lam = sl.lam_from(q["O"])
            lh, la = lam * share, lam * (1 - share)
            q["GG"] = (1 - math.exp(-lh)) * (1 - math.exp(-la))
            q["NG"] = 1 - q["GG"]
            rows.append({"day": day, "wd": wd, "league": m["league"], "home": m["home"], "away": m["away"], "derby": bool(m["flags"].get("derby")),
                         "p": [m["p"][e] for e in EV], "q": [q.get(e, np.nan) for e in EV],
                         "r": [m["real"].get(e, np.nan) for e in EV],
                         "hit": [bool(robot.hit_any(e, m["hg"], m["ag"])) for e in EV],
                         "current": robot.one_pick(m["rb"], m["avg"], None, m["league"])})
    return rows


def top_third(idx_scores):
    """[(индекс, шанс)] на една група първенство-ден -> множеството от топ шанс (bets/sure.py: третината, нагоре, шанс >= 65%)."""
    chosen = sure.pick([(i, s) for i, s in idx_scores], sure.SHARE, sure.ROUNDING, 0.0)
    return set(chosen)


def evaluate(rows, picks, thr):
    """picks: {номер на мача: (събитие, шанс, цена, излязло)} -> единични, колонки по 2 и по 3 по периоди. Топ шанс - третината на групата първенство-ден
    по шанса (както на живо, сред всички мачове с прогноза), после само с шанс >= thr."""
    by_group = defaultdict(list)
    for i, (e, s, r, h) in picks.items():
        by_group[(rows[i]["league"], rows[i]["day"])].append((i, s))
    keep = set()
    for g, items in by_group.items():
        keep |= top_third(items)
    keep = {i for i in keep if picks[i][1] >= thr}
    out = {}
    by_day = defaultdict(list)
    for i in keep:
        by_day[rows[i]["day"]].append(i)
    for per in ("select", "clean"):
        acc = {1: [], 2: [], 3: []}
        for day, ids in by_day.items():
            if (day < SELECT_END) != (per == "select"):
                continue
            ids = sorted(ids, key=lambda i: -picks[i][1])
            for i in ids:
                acc[1].append((picks[i][3], picks[i][2]))
            for size in (2, 3):
                used = set()
                for _ in range(4):
                    col, lgs = [], set()
                    for i in ids:
                        if i in used or rows[i]["league"] in lgs or len(col) == size:
                            continue
                        col.append(i)
                        lgs.add(rows[i]["league"])
                    if len(col) < size:
                        break
                    used.update(col)
                    acc[size].append((all(picks[i][3] for i in col), math.prod(picks[i][2] for i in col)))
        out[per] = {str(k): gl.stat(v) for k, v in acc.items()}
    return out, keep


ARR = {}


def arrays(rows):
    """Матриците (мачове x 14 събития) за бързото търсене."""
    if not ARR:
        ARR["P"] = np.array([r["p"] for r in rows], dtype=float)
        ARR["Q"] = np.array([r["q"] for r in rows], dtype=float)
        ARR["R"] = np.array([r["r"] for r in rows], dtype=float)
        ARR["H"] = np.array([r["hit"] for r in rows], dtype=bool)
        ARR["derby"] = np.array([r["derby"] for r in rows], dtype=bool)
        ARR["wd"] = np.array([r["wd"] for r in rows])
    return ARR


def variant_picks(rows, src, fam, thr, floor, agree, weekend):
    A = arrays(rows)
    P, Q, R = A["P"], A["Q"], A["R"]
    S = P if src == "робот" else Q if src == "пазар" else (P + Q) / 2
    allowed = np.array([e in FAMILIES[fam] for e in EV])
    with np.errstate(invalid="ignore"):
        valid = allowed[None, :] & ~np.isnan(R) & (R > 1.0) & (R >= floor) & ~np.isnan(S) & (S >= thr)
        if agree:
            valid &= ~np.isnan(Q) & (np.abs(P - Q) <= 0.05)
    valid &= ~A["derby"][:, None]
    if weekend:
        valid &= (A["wd"] >= 4)[:, None]
    Sm = np.where(valid, S, -np.inf)
    best = Sm.argmax(axis=1)
    score = Sm[np.arange(len(rows)), best]
    picks = {}
    for i in np.nonzero(np.isfinite(score))[0]:
        k = best[i]
        picks[int(i)] = (EV[k], float(score[i]), float(R[i, k]), bool(A["H"][i, k]))
    return picks


def current_picks(rows):
    picks = {}
    for i, row in enumerate(rows):
        one = row["current"]
        if row["derby"] or not one:
            continue
        k = EV.index(one["sel"]) if one["sel"] in EV else None
        if k is None or np.isnan(row["r"][k]):
            continue
        picks[i] = (one["sel"], one["p"], row["r"][k], row["hit"][k])
    return picks


def why(rows, picks, keep):
    """Кога и защо излиза: по вид, по цена, по ден, по разликата робот-пазар."""
    g = defaultdict(lambda: defaultdict(list))
    for i in keep:
        e, s, r, h = picks[i]
        k = EV.index(e)
        q = rows[i]["q"][k]
        g["вид"][robot.label(e)].append((h, r))
        g["цена"]["под 1.20" if r < 1.20 else "1.20-1.40" if r < 1.40 else "1.40-1.70" if r < 1.70 else "над 1.70"].append((h, r))
        g["ден"]["петък-неделя" if rows[i]["wd"] >= 4 else "понеделник-четвъртък"].append((h, r))
        if not np.isnan(q):
            d = rows[i]["p"][k] - q
            g["робот срещу пазар"]["роботът над пазара с 5+ пункта" if d > 0.05 else "роботът под пазара с 5+ пункта" if d < -0.05 else "съгласни (до 5 пункта)"].append((h, r))
    return {dim: {k: gl.stat(v) for k, v in d.items() if len(v) >= 30} for dim, d in g.items()}


def main():
    rows = build()
    print("мачове:", len(rows))
    cur_picks = current_picks(rows)
    cur, cur_keep = evaluate(rows, cur_picks, 0.65)
    results = []
    for src, fam, floor, agree, weekend in product(SOURCES, FAMILIES, FLOORS, (False, True), (False, True)):
        picks = variant_picks(rows, src, fam, 0.5, floor, agree, weekend)
        for thr in THRESH:
            ev, keep = evaluate(rows, picks, thr)
            results.append({"src": src, "fam": fam, "thr": thr, "floor": floor, "agree": agree, "weekend": weekend, "res": ev})
    name = lambda v: (f"шанс по {v['src']}, {v['fam']}, праг {v['thr']:.0%}, цена от {v['floor']:.2f}" + (", робот и пазар съгласни" if v["agree"] else "")
                      + (", само пет-нед" if v["weekend"] else ""))
    out = {"current": cur, "best": {}}
    for size in ("1", "2", "3"):
        lab = {"1": "ЕДИНИЧНИ", "2": "КОЛОНКИ ПО 2", "3": "КОЛОНКИ ПО 3"}[size]
        c = cur
        print(f"\n===== {lab} =====")
        print(f"  СЕГАШНОТО: избор {gl.fmt(c['select'][size])}\n             чиста {gl.fmt(c['clean'][size])}")
        ok = [v for v in results if v["res"]["select"][size].get("n", 0) >= MIN_N]
        by_ret = sorted(ok, key=lambda v: -v["res"]["select"][size]["return"])[:3]
        cur_ret = c["select"][size].get("return", 0)
        by_hit = sorted([v for v in ok if v["res"]["select"][size]["return"] >= cur_ret], key=lambda v: -v["res"]["select"][size]["hit"])[:3]
        out["best"][size] = {"by_return": [], "by_hit": []}
        for title, lst, key in (("най-добри по ВРЪЩАНЕ (избрани в избора)", by_ret, "by_return"), ("най-добри по ИЗЛИЗАНЕ (и връщат поне колкото сегашното)", by_hit, "by_hit")):
            print(f"  {title}:")
            for v in lst:
                print(f"   - {name(v)}\n       избор {gl.fmt(v['res']['select'][size])}\n       чиста {gl.fmt(v['res']['clean'][size])}")
                out["best"][size][key].append({**{k: v[k] for k in ("src", "fam", "thr", "floor", "agree", "weekend")}, "res": v["res"]})
    # кога и защо - за сегашното и за най-доброто по връщане при единичните
    top = sorted([v for v in results if v["res"]["select"]["1"].get("n", 0) >= MIN_N], key=lambda v: -v["res"]["select"]["1"]["return"])[0]
    tp = variant_picks(rows, top["src"], top["fam"], 0.5, top["floor"], top["agree"], top["weekend"])
    _, tk = evaluate(rows, tp, top["thr"])
    out["why"] = {"current": why(rows, cur_picks, cur_keep), "best": why(rows, tp, tk), "best_name": name(top)}
    for lab, w in (("СЕГАШНОТО", out["why"]["current"]), ("НАЙ-ДОБРОТО (" + name(top) + ")", out["why"]["best"])):
        print(f"\nКОГА И ЗАЩО - {lab}")
        for dim, d in w.items():
            print(f"  {dim}: " + " | ".join(f"{k} {x['hit']:.0%} ({x['n']}, връща {x['return']:.2f})" for k, x in sorted(d.items(), key=lambda kv: -kv[1]["n"])))
    out["all"] = [{**{k: v[k] for k in ("src", "fam", "thr", "floor", "agree", "weekend")}, "res": v["res"]} for v in results]
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
