"""
Учи ли се роботът от собствените си резултати ден след ден? (собственикът, 2026-10-11: „симулация, в която започваш от ден едно с дадена информация
и се учиш всеки следващ ден, с правилата на майстора и логиката, която има - така ще има прогрес“).

Какво вече има: research/robot_backtest.py е walk-forward - за всяко първенство и месец моделът е обучен САМО на миналото (4 години назад) и прогнозира месеца
(на живо - всеки ден). Така „започва от нулата и се учи“ вече се прави за ШАНСОВЕТЕ (отборните сили). Тази проверка добавя липсващото: слой, който се учи от
СОБСТВЕНИТЕ грешки на робота - колко често излиза каквото казва - и поправя шансовете, преди да избере топ шанс / колонки.

ПРОТОКОЛ (записан преди пускането): данни backtest_tips (walk-forward, само с модел, 2023-07-01 до днес), едната прогноза на робота на мач (bets/robot.py one_pick).
Ден по ден, в хронологичен ред, САМО от резултатите на предишните дни (никога от същия ден):
  L0 - каквото роботът казва (сегашното);
  L1 - поправка по вид избор (1/X/2, двоен шанс, над/под 1.5 / 2.5 / 3.5, двата вкарват, ...): p' = p - b_вид, b_вид = сборът (казано - излязло) / (n + 50)
       върху всички предишни прогнози от този вид („свиване“ към 0 при малко мачове);
  L2 - L1 и още поправка по първенство: p'' = p' - b_първенство, b_първенство = сборът на остатъците (p' - излязло) / (n + 100) от предишните мачове на първенството.
Параметрите 50 и 100 са фиксирани тук, преди пускането (не се настройват). Първите 12 месеца (2023-07 до 2024-06) са само учене; отчита се от 2024-07-01:
период А 2024-07-01..2025-06-30, период Б от 2025-07-01 (както избор / чиста проверка).
Мери се:
  1) качеството на шанса на прогнозата: Brier (p - излязло)^2 - по-малко е по-добре; сдвоена разлика с L0 и стандартна грешка;
  2) подборът, ако се ползва поправеният шанс (топ шанс по bets/sure.py и праг 65% върху поправения шанс): колко минават единичните и колонките по 3 и
     връщането от 1 € (само за мачове с истински коефициент) - както research/legs_backtest.py;
  3) „прогрес“ във времето: колко излиза топ шанс по полугодия (L0).
РЕШЕНИЕ: слой се приема за кандидат за правилата само ако и в А, и в Б има по-малък Brier с над 2 грешки на разликата И подборът му (единични) излиза повече с над 2
грешки на разликата; иначе нищо не се променя и собственикът получава числата. Нищо в правилата на майстора не се пипа от този скрипт.
Резултатът: data/online_learning.json.
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import robot                                         # noqa: E402
from research import columns_backtest as cb                     # noqa: E402
from research import legs_backtest as lb                        # noqa: E402

OUT = ROOT / "data" / "online_learning.json"
EVAL_FROM = "2024-07-01"
SPLIT = "2025-07-01"
K_KIND, K_LEAGUE = 50, 100


def kind(sel):
    if sel in ("1", "X", "2"):
        return "1x2"
    if sel in ("1X", "X2", "12"):
        return "dc"
    if sel in ("O", "U"):
        return "ou25"
    if sel in ("O15", "U15"):
        return "ou15"
    if sel in ("O35", "U35"):
        return "ou35"
    if sel in ("GG", "NG"):
        return "btts"
    if sel in robot.HANDICAPS:
        return "hcp"
    if sel in robot.CORNER_SIDES:
        return "cside"
    return "extras"


def learn(days):
    """{вариант: {ден: [мач с поправен шанс]}}; всеки ден е поправен само от предишните. Връща и списък (ден, p0, p1, p2, излязло) за Brier."""
    kinds = defaultdict(lambda: [0.0, 0])            # вид -> [сбор (казано - излязло), n]
    leagues = defaultdict(lambda: [0.0, 0])          # първенство -> [сбор (p' - излязло), n]
    out = {"L0": {}, "L1": {}, "L2": {}}
    rows = []
    for day in sorted(days):
        ms = days[day]
        pending = []
        for m in ms:
            one = m["one"]
            hit = robot.hit_any(one["sel"], m["hg"], m["ag"])
            k = kind(one["sel"])
            ks, kn = kinds[k]
            p1 = one["p"] - ks / (kn + K_KIND)
            ls, ln = leagues[m["league"]]
            p2 = p1 - ls / (ln + K_LEAGUE)
            m["_p"] = (one["p"], min(max(p1, 0.01), 0.99), min(max(p2, 0.01), 0.99))
            pending.append((m, k, hit, p1))
            if day >= EVAL_FROM and hit is not None:
                rows.append((day, one["p"], m["_p"][1], m["_p"][2], float(bool(hit))))
        for name, idx in (("L0", 0), ("L1", 1), ("L2", 2)):
            out[name][day] = [{**m, "flags": dict(m["flags"]), "one": {**m["one"], "p": m["_p"][idx]}} for m in ms]
        for m, k, hit, p1 in pending:                 # резултатите стават известни чак след деня
            if hit is None:
                continue
            kinds[k][0] += m["one"]["p"] - float(bool(hit))
            kinds[k][1] += 1
            leagues[m["league"]][0] += p1 - float(bool(hit))
            leagues[m["league"]][1] += 1
    return out, rows


def paired(rows, a, b):
    """Brier на вариант a минус Brier на b (сдвоено по мачове): средна разлика и стандартна грешка; отрицателно = a е по-добър."""
    d = [(r[a] - r[4]) ** 2 - (r[b] - r[4]) ** 2 for r in rows]
    n = len(d)
    m = sum(d) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1))
    return m, sd / math.sqrt(n), n


def monthly(variants, names=("L0", "L1")):
    """По месеци (описателно, без нов избор): колонки по 3 - колко, колко минали; единични - колко, колко излезли; за всеки вариант."""
    from bets import columns
    out = defaultdict(dict)
    for name in names:
        for day, ms in sorted(variants[name].items()):
            if day < EVAL_FROM:
                continue
            mon = day[:7]
            st = out[mon].setdefault(name, {"cols": 0, "pass": 0, "singles": 0, "hit": 0})
            ms = [{**m, "flags": dict(m["flags"])} for m in ms]
            by_id = {m["id"]: m for m in ms}
            cb.mark_sure(ms)
            cands = columns.candidates(ms, 0.65, need_sure=True)
            for col in columns.build(cands, size=3, max_columns=4, diversify="league"):
                st["cols"] += 1
                st["pass"] += all(robot.hit_any(c["sel"], by_id[c["id"]]["hg"], by_id[c["id"]]["ag"]) for c in col)
            for col in columns.build(cands, size=1, max_columns=4, diversify="league"):
                h = robot.hit_any(col[0]["sel"], by_id[col[0]["id"]]["hg"], by_id[col[0]["id"]]["ag"])
                if h is not None:
                    st["singles"] += 1
                    st["hit"] += bool(h)
    return dict(sorted(out.items()))


def main():
    days = cb.load()
    variants, rows = learn(days)
    if "--monthly" in sys.argv:
        mon = monthly(variants)
        print("месец   | сегашните: колонки минали      единични    | вариант 2: колонки минали      единични")
        tot = {"L0": [0, 0, 0, 0], "L1": [0, 0, 0, 0]}
        for m, v in mon.items():
            a, b = v["L0"], v["L1"]
            for k, x in (("L0", a), ("L1", b)):
                tot[k] = [tot[k][0] + x["cols"], tot[k][1] + x["pass"], tot[k][2] + x["singles"], tot[k][3] + x["hit"]]
            f = lambda n, d: f"{n:>3}/{d:<3} ({n / d:4.0%})" if d else "   -        "
            print(f"{m} | {f(a['pass'], a['cols'])}  {f(a['hit'], a['singles'])} | {f(b['pass'], b['cols'])}  {f(b['hit'], b['singles'])}")
        print("ОБЩО  ", {k: f"колонки {v[1]}/{v[0]} ({v[1] / v[0]:.1%}), единични {v[3]}/{v[2]} ({v[3] / v[2]:.1%})" for k, v in tot.items()})
        data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
        data["monthly"] = mon
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return
    res = {"periods": {}}
    for label, sel in (("A", lambda r: r[0] < SPLIT), ("B", lambda r: r[0] >= SPLIT)):
        rs = [r for r in rows if sel(r)]
        brier = {name: sum((r[i] - r[4]) ** 2 for r in rs) / len(rs) for name, i in (("L0", 1), ("L1", 2), ("L2", 3))}
        brier["L0"] = sum((r[1] - r[4]) ** 2 for r in rs) / len(rs)
        brier["L1"] = sum((r[2] - r[4]) ** 2 for r in rs) / len(rs)
        brier["L2"] = sum((r[3] - r[4]) ** 2 for r in rs) / len(rs)
        d1 = paired(rs, 2, 1)
        d2 = paired(rs, 3, 1)
        said = sum(r[1] for r in rs) / len(rs)
        said2 = sum(r[3] for r in rs) / len(rs)
        hit = sum(r[4] for r in rs) / len(rs)
        res["periods"][label] = {"n": len(rs), "hit": hit, "said_L0": said, "said_L2": said2, "brier": brier,
                                 "d_L1_vs_L0": d1, "d_L2_vs_L0": d2}
        print(f"период {label}: {len(rs)} прогнози; излизат {hit:.1%}; роботът казва {said:.1%} (след поправката L2 {said2:.1%})")
        print(f"   Brier: L0 {brier['L0']:.4f} | L1 {brier['L1']:.4f} | L2 {brier['L2']:.4f};  L1-L0 {d1[0]:+.5f} ± {d1[1]:.5f};  L2-L0 {d2[0]:+.5f} ± {d2[1]:.5f}")
    # подборът: само от EVAL_FROM нататък
    sub = {}
    for name in ("L0", "L1", "L2"):
        dd = {d: ms for d, ms in variants[name].items() if d >= EVAL_FROM}
        sub[name] = {size: lb.run(dd, size) for size in (1, 3)}
    for size in (1, 3):
        print(f"\nподбор - {'единични' if size == 1 else 'колонки по 3'}:")
        for name in ("L0", "L1", "L2"):
            for per, lab in (("select", "А"), ("clean", "Б")):
                x = lb.stats(sub[name][size][per])
                if not x["cols"]:
                    print(f"  {name} {lab}: няма")
                    continue
                print(f"  {name} {lab}: {x['cols']:>5} ({x['per_day']:.2f} на ден) минават {x['pass']:.1%} ± {x['se']:.1%} (казва {x['claimed']:.1%})"
                      + (f" | връща {x['book_return']:.2f} ± {x['book_se']:.2f} € на {x['book_n']}" if "book_return" in x else ""))
                res.setdefault("select", {}).setdefault(name, {}).setdefault(str(size), {})[lab] = x
    # прогрес във времето (L0): топ шанс по полугодия
    print("\nпрогрес във времето - колко излиза топ шанс (L0) по полугодия:")
    half = defaultdict(lambda: [0, 0])
    for day in sorted(variants["L0"]):
        ms = [dict(m, flags=dict(m["flags"])) for m in variants["L0"][day]]
        cb.mark_sure(ms)
        for m in ms:
            if (m["flags"].get("sure") or {}).get("y"):
                h = robot.hit_any(m["one"]["sel"], m["hg"], m["ag"])
                if h is None:
                    continue
                key = day[:4] + ("H1" if day[5:7] <= "06" else "H2")
                half[key][0] += bool(h)
                half[key][1] += 1
    for key in sorted(half):
        h, n = half[key]
        print(f"  {key}: {h / n:.1%} ± {math.sqrt(h / n * (1 - h / n) / n):.1%} ({n})")
        res.setdefault("progress", {})[key] = {"hit": h / n, "n": n}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
