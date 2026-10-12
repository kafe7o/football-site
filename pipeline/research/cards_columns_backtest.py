"""
Картоните като мач в колонка (майсторът, 2026-10-12: „нека да може да добавя и картони; съдия, който обича да дава картони, и отбори, които получават картони“).

Данни: data/extras_rows.json - прогнозите мач по мач от research/extras_backtest.py (walk-forward, 22 първенства, 2023-07..днес, със съдиите за всички първенства):
за всеки мач основната линия на картоните и шансът за над при три линии (линия-1, линия, линия+1).
ПРОТОКОЛ (записан преди пускането): кандидат за колонка е изборът с най-голям шанс измежду „над линия-1“ и „под линия+1“ (съседните линии - там шансът е по-голям от ~50%);
прагове на шанса {65, 70, 75%}. Мери се: колко излиза (избор до 2025-07-01 / чиста от там) срещу казаното, по първенства; ЦЕНА за картони odds API няма - връщането се
показва при три допускания за коефициента на такъв избор (1.30, 1.40, 1.50), ясно отбелязани като допускане. Описателно - дали картоните влизат в системата се решава в
общата проверка (система + картони + състави) по протокола там. Резултатът: data/cards_columns_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cards_columns_backtest.json"
SELECT_END = "2025-07-01"
THRESH = (0.65, 0.70, 0.75)
ASSUMED = (1.30, 1.40, 1.50)


def main():
    rows = [r for r in json.loads((ROOT / "data" / "extras_rows.json").read_text(encoding="utf-8")) if r["kind"] == "cards" and r.get("lines")]
    print("мачове с картони:", len(rows))
    res = {}
    for thr in THRESH:
        acc = defaultdict(lambda: defaultdict(list))
        for r in rows:
            line = r["line"]
            lo, hi = r["lines"].get(f"{line - 1:.1f}"), r["lines"].get(f"{line + 1:.1f}")
            cands = []
            if lo is not None:
                cands.append(("O", line - 1, lo))                      # над линия-1
            if hi is not None:
                cands.append(("U", line + 1, 1 - hi))                  # под линия+1
            if not cands:
                continue
            side, ln, p = max(cands, key=lambda c: c[2])
            if p < thr:
                continue
            hit = (r["total"] > ln) if side == "O" else (r["total"] < ln)
            per = "select" if r["date"] < SELECT_END else "clean"
            acc[per]["ALL"].append((hit, p))
            acc[per][r["league"]].append((hit, p))
        res[str(thr)] = {}
        for per in ("select", "clean"):
            out = {}
            for lg, xs in acc[per].items():
                n = len(xs)
                h = sum(x for x, _ in xs) / n
                out[lg] = {"n": n, "hit": h, "se": math.sqrt(h * (1 - h) / n) if n else 0, "said": sum(p for _, p in xs) / n,
                           "return": {str(o): h * o for o in ASSUMED}}
            res[str(thr)][per] = out
        a, c = res[str(thr)]["select"].get("ALL"), res[str(thr)]["clean"].get("ALL")
        c_all, c = c, res[str(thr)]["clean"]
        if a and c_all:
            c_lg = {lg: v for lg, v in c.items() if lg != "ALL"}
            c = c_all
            print(f"\nшанс >= {thr:.0%}: избор {a['n']} мача, излизат {a['hit']:.1%} ± {a['se']:.1%} (казано {a['said']:.1%}) | чиста {c['n']} мача, излизат {c['hit']:.1%} ± {c['se']:.1%} (казано {c['said']:.1%})")
            print("   връщане при допуснат коефициент: " + ", ".join(f"{o}: {c['return'][str(o)]:.2f} €" for o in ASSUMED) + " (чиста; допускане - цена за картони няма)")
            lgs = sorted((lg for lg in c_lg if c_lg[lg]["n"] >= 40), key=lambda lg: -c_lg[lg]["hit"])
            print("   по първенства (чиста): " + ", ".join(f"{lg} {c_lg[lg]['hit']:.0%} ({c_lg[lg]['n']})" for lg in lgs[:12]) + " ... " + ", ".join(f"{lg} {c_lg[lg]['hit']:.0%}" for lg in lgs[-3:]))
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
