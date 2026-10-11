"""
Колонки само петък-неделя? (собственикът, 2026-10-11: „с данните - защо не ги правиш само тогава“).

Повод: в research/online_learning_backtest.py (07.2024-09.2026) колонките по 3 в петък-неделя минават по-често, отколкото понеделник-четвъртък
(сегашните 30.7% срещу 26.1%, вариант 2 35.9% срещу 30.6%). Това е видяно СЛЕД като са погледнати данните - затова тук се проверява честно.

ПРОТОКОЛ (записан преди пускането):
Данни backtest_tips (walk-forward). Колонките - сегашното правило (bets/columns.py: по 3, топ шанс, шанс >= 65%, един мач на първенство, до 4 на ден), за двата варианта:
L0 - сегашните, L1 - вариант 2 (поправеният шанс от research/online_learning_backtest.py, ден след ден само от миналото).
Периоди: Ч = 2023-07-01..2024-06-30 - НЕВИЖДАН при наблюдението (то е от 07.2024); А = 2024-07-01..2025-06-30; Б = от 2025-07-01 (А и Б са видени).
Мери се за всеки период: колонки петък-неделя срещу понеделник-четвъртък - колко минават (± грешка) и колко връщат от 1 € (цена от букмейкър, иначе честната на робота
минус 6% марж - реалистичният сценарий от online_learning_backtest.py --bank).
РЕШЕНИЕ: „само петък-неделя“ се препоръчва на собственика, само ако в невиждания период Ч петък-неделя минават повече с над 2 грешки на разликата И в А и в Б посоката е
същата; иначе - не (разликата е случайна). Правилата на майстора не се пипат от този скрипт. Резултатът: data/weekend_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import columns, robot                                # noqa: E402
from research import columns_backtest as cb                     # noqa: E402
from research import online_learning_backtest as olb            # noqa: E402

OUT = ROOT / "data" / "weekend_backtest.json"
PERIODS = (("Ч", "2023-07-01", "2024-07-01"), ("А", "2024-07-01", "2025-07-01"), ("Б", "2025-07-01", "9999-12-31"))
MARGIN = 0.06


def period_of(day):
    for name, a, b in PERIODS:
        if a <= day < b:
            return name
    return None


def run(variant_days):
    st = defaultdict(lambda: {"n": 0, "pass": 0, "ret": 0.0})
    for day, ms in sorted(variant_days.items()):
        per = period_of(day)
        if per is None:
            continue
        part = "пет-нед" if date.fromisoformat(day).weekday() >= 4 else "пон-чет"
        ms = [{**m, "flags": dict(m["flags"])} for m in ms]
        by_id = {m["id"]: m for m in ms}
        cb.mark_sure(ms)
        for col in columns.build(columns.candidates(ms, 0.65, need_sure=True), size=3, max_columns=4, diversify="league"):
            ok = all(robot.hit_any(c["sel"], by_id[c["id"]]["hg"], by_id[c["id"]]["ag"]) for c in col)
            odds = 1.0
            for c in col:
                odds *= c["odds"] if c.get("src") == "book" else c["odds"] * (1 - MARGIN)
            s = st[(per, part)]
            s["n"] += 1
            s["pass"] += ok
            s["ret"] += odds if ok else 0.0
    return st


def main():
    days = cb.load()
    variants, _ = olb.learn(days)
    res = {}
    verdict = {}
    for v, lab in (("L0", "сегашните"), ("L1", "вариант 2")):
        st = run(variants[v])
        print(lab)
        res[v] = {}
        diffs = {}
        for per, _, _ in PERIODS:
            line = []
            for part in ("пет-нед", "пон-чет"):
                s = st[(per, part)]
                p = s["pass"] / s["n"] if s["n"] else 0
                se = math.sqrt(p * (1 - p) / s["n"]) if s["n"] else 0
                res[v][f"{per}|{part}"] = {"n": s["n"], "pass": p, "se": se, "return": s["ret"] / s["n"] if s["n"] else None}
                line.append(f"{part}: {s['pass']}/{s['n']} = {p:.1%} ± {se:.1%}, връща {s['ret'] / s['n']:.2f}" if s["n"] else f"{part}: няма")
            a, b = res[v][f"{per}|пет-нед"], res[v][f"{per}|пон-чет"]
            d = a["pass"] - b["pass"]
            se = math.hypot(a["se"], b["se"])
            diffs[per] = (d, se)
            print(f"  {per}: " + " | ".join(line) + f" | разлика {d:+.1%} ({d / se:+.1f} грешки)" if se else "")
        ok = diffs["Ч"][0] > 2 * diffs["Ч"][1] and diffs["А"][0] > 0 and diffs["Б"][0] > 0
        verdict[lab] = ok
        res[v]["diffs"] = {k: {"diff": d, "se": s} for k, (d, s) in diffs.items()}
    print("\nВЕРДИКТ (само петък-неделя - препоръка ли е):", verdict)
    res["verdict"] = verdict
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
