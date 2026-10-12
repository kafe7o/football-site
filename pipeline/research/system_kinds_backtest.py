"""
Системата по вид на избора: знаци 1/X/2, двоен шанс, голове, картони, корнери - кое е най-сигурно за колонката (собственикът, 2026-10-13: „пробвай и двоен шанс, и корнери, и 1/X/2 -
сигурните на робота; има моменти, в които дава и такива“).

Основа: системата (вариант 2 + правилата на майстора, по 2 мача, една колонка на ден при шанс на колонката >= 50%, истинска цена от 1.40, най-добрите 11 първенства, чет-нед).
ПРОТОКОЛ (записан преди пускането): видове за мача = {всички 14 събития (сегашното); само знаци 1/X/2; само двоен шанс; знаци + двоен шанс; само голове над/под; картони (съседна линия,
шанс >= 75%, ДОПУСНАТ коеф. 1.40); корнери (съседна линия, >= 75%, допуснат 1.40 - корнерите назад са БЕЗ умение: Brier по-лош от средното на лигата, затова се очаква да са по-лоши);
знаци + двоен шанс + картони}. Мери се: колонки на месец, минават (± грешка), връщане (избор / чиста), пари при 300 €/30 €. Описателно. Резултатът: data/system_kinds_backtest.json.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import goal_lines_backtest as gl                    # noqa: E402
from research import master_rules_backtest as mr                  # noqa: E402
from research import master_v2_system as ms                       # noqa: E402
from research import system_cards_backtest as sc                  # noqa: E402
from research import variations_backtest as vb                    # noqa: E402

OUT = ROOT / "data" / "system_kinds_backtest.json"
FAMS = {"всички събития (сегашното)": None, "само знаци 1/X/2": {"1", "X", "2"}, "само двоен шанс": {"1X", "X2", "12"},
        "знаци + двоен шанс": {"1", "X", "2", "1X", "X2", "12"}, "само голове над/под": {"O15", "U15", "O", "U", "O35", "U35"}}


def extras_candidates(kind, thr):
    rows = json.loads((ROOT / "data" / "extras_rows.json").read_text(encoding="utf-8"))
    out = defaultdict(list)
    for r in rows:
        if r["kind"] != kind or not r.get("lines"):
            continue
        line = r["line"]
        lo, hi = r["lines"].get(f"{line - 1:.1f}"), r["lines"].get(f"{line + 1:.1f}")
        cands = [c for c in (("O", line - 1, lo), ("U", line + 1, None if hi is None else 1 - hi)) if c[2] is not None]
        if not cands:
            continue
        side, ln, p = max(cands, key=lambda c: c[2])
        if p < thr:
            continue
        hit = (r["total"] > ln) if side == "O" else (r["total"] < ln)
        out[(r["league"], r["date"])].append((f"{kind}{side}{ln}:{r['home']}", p, bool(hit), r["home"], r["away"]))
    return out


def main():
    rows = vb.build()
    base_picks = mr.master_picks(rows, "вариант 2", False, False, floor=1.40)
    base_by_day = ms.top_picks(rows, base_picks, 0.65)
    best = ms.best_leagues(ms.leg_stats(rows, base_picks, base_by_day, "league"))
    res = []
    empty = {}
    for fname, events in FAMS.items():
        picks = mr.master_picks(rows, "вариант 2", False, False, floor=1.40, events=events)
        by_day = ms.top_picks(rows, picks, 0.65)
        st, months = sc.run(rows, picks, by_day, empty, best, "чет-нед", "само голове", 1.40)
        res.append((fname, st, ms.money(months) or {}))
    cards = extras_candidates("cards", 0.75)
    corners = extras_candidates("corners", 0.75)
    for fname, cands in (("картони >= 75% (допуснат 1.40)", cards), ("корнери >= 75% (допуснат 1.40)", corners)):
        st, months = sc.run(rows, base_picks, base_by_day, cands, best, "чет-нед", "само картони", 1.40)
        res.append((fname, st, ms.money(months) or {}))
    picks = mr.master_picks(rows, "вариант 2", False, False, floor=1.40, events={"1", "X", "2", "1X", "X2", "12"})
    by_day = ms.top_picks(rows, picks, 0.65)
    st, months = sc.run(rows, picks, by_day, cards, best, "чет-нед", "голове + картони", 1.40)
    res.append(("знаци + двоен шанс + картони (допуснат 1.40)", st, ms.money(months) or {}))
    print(f"{'вид на избора':46} | кол/мес | избор: мин. връща | чиста: мин. връща ±  | 300/30: ср./мес +100 минус")
    out = {}
    for fname, st, m in res:
        s, c = st["select"], st["clean"]
        pm = (s.get("n", 0) + c.get("n", 0)) / max(1, m.get("months", 1))
        print(f"{fname:46} | {pm:>5.1f}   | {s.get('hit', 0):>4.0%} {s.get('return', 0):.3f}     | {c.get('hit', 0):>4.0%} {c.get('return', 0):.3f} ±{c.get('return_se', 0):.2f} | {m.get('avg', 0):+5.0f} {m.get('plus100', 0):>3}/{m.get('months', 0)} {m.get('minus', 0):>3}")
        out[fname] = {"stats": st, "money": {k: v for k, v in m.items() if k != "by_month"}, "per_month": pm}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("записано:", OUT)


if __name__ == "__main__":
    main()
