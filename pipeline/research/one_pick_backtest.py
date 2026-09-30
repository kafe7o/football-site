"""
ЕДНА прогноза за мач (указание на професионалиста, 2026-10-01).

Той казва: „да изработва по една прогноза за всеки мач, която според него е най-адекватна, като му
даваме пълна база с корнери, голове, картони, знаци, форми на двата отбора, липсващи играчи -
абсолютно всичко - и той сам да определи точната прогноза за мача. Иначе, ако ни напише четири
прогнози („домакинът ще спечели“, „над 3.5 картона“) и познае 3 от 4, ние трябва да гадаем коя да
изберем. Когато даде точно една, ще видим точно какъв процент държи този робот.“

Какво знае роботът (базата): головете и xG на отборите с тегло по давност (формата), домакинското
предимство, резервният модел за новаците, картоните и корнерите на отборите, съдията (където има
име). Липсващи играчи НЯМА в безплатните данни (трябва API-Football) - тук не участват.

ПРОТОКОЛ (записан преди пускането):
Данни: backtest_tips (роботът назад, walk-forward по месеци, само мачовете с модел), 2023-07-01 до
днес; картони и корнери - research/extras_backtest.run_league (същият walk-forward, 22-те лиги на
football-data). Истинските резултати, картони и корнери.

Събитията, от които роботът избира: 1, X, 2, 1X, X2, 12, над/под 1.5, 2.5 и 3.5 гола, двата вкарват
да/не, картони над/под основната линия, корнери над/под основната линия. Шансът е на робота.
Коефициентът: средният на букмейкърите, където го има в историята (1/X/2, двоен шанс от 1/X/2,
над/под 2.5), иначе честният на робота 1/шанс - както на живо.

„Най-адекватна“ - по правилата, които професионалистът вече е дал: прогноза с коефициент 1.40-1.80
(„сигурната“) и шанс по робота поне 50%; фаворит 1.30-1.55 в тото лига (N1, AUT) - не. Ако в мача
няма такова събитие - най-вероятното събитие над 1.80. Всеки мач получава точно една прогноза.
Варианти кое събитие от диапазона:
  A  най-вероятното по робота от ВСИЧКИ събития;
  A2 като A, без картони и корнери (умението им назад не е доказано);
  B  най-вероятното само от събитията с коефициент от букмейкър (1/X/2, двоен шанс, над/под 2.5) -
     сегашната „по-сигурна“;
  C  „най-характерното за мача“: събитието, чийто шанс по робота е най-много над обичайното за
     лигата (честотата на събитието в лигата в 4-те години преди 2023-07-01).

Мери се във всеки период: колко мача имат прогноза, колко често излиза (± грешка), какъв шанс е
казал роботът средно (калибрация), колко често същото събитие излиза в лигата изобщо, среден
коефициент, доход - само където има коефициент от букмейкър (за другите реалният коефициент е
неизвестен), от какви събития са прогнозите.
ИЗБОР: сезони 2023/24-2024/25 (до 2025-07-01); ЧИСТА ПРОВЕРКА: от 2025-07-01.
ПРИЕМА СЕ вариантът с най-висок процент познати в избора. Потвърждава се, ако и в чистата
проверка не е с повече от 2 грешки под най-добрия там; иначе - нищо не се приема и собственикът
получава числата. Калибрацията не е условие (изборът на най-вероятното от много събития винаги
надценява малко - „проклятието на победителя“), но се показва на сайта до шанса на робота.
Резултатът: data/one_backtest.json.

РЕЗУЛТАТ (2026-10-01): ПРИЕТО A. Познати: A 64.3% / 63.8%, A2 64.3% / 64.0%, B 56.2% / 54.9%,
C 58.3% / 57.7% (избор / чиста; ± 0.3-0.4%). A: роботът казва средно 66.4% / 66.1%; същото събитие в
лигата изобщо - 63.1% / 63.0%; ср. коеф. 1.53; с коеф. от букмейкър 34% / 31% от прогнозите, доход
-6.8% ± 0.9% / -7.9% ± 1.2%. От какво (чиста): голове над/под 64%, двоен шанс 17%, двата вкарват 8%,
1/X/2 6%, картони 3%, корнери 1%. C е по-малко вероятно, но най-много над обичайното (+7 пункта).

С ГРАНИЦИТЕ ОТ 01.10 (1.50-1.80, резерва от 2.50 - професионалистът): пак A - 56.7% / 56.8%; роботът казва
59.8% / 59.2%; в лигата изобщо 56.2% / 55.7%; ср. к 1.77; доход -7.3% / -8.4%. A2 55.7 / 56.0, B 48.3 / 46.8,
C 53.7 / 53.9. От какво (чиста): голове над/под 43%, двоен шанс 18%, двата вкарват 15%, 1/X/2 11%, картони 10%.
"""

import json
import math
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, model, robot                    # noqa: E402
from bets.leagues import FD                          # noqa: E402
from research import extras_backtest                 # noqa: E402

OUT = ROOT / "data" / "one_backtest.json"
SELECT_END = "2025-07-01"
BASE_BEFORE, BASE_YEARS = "2023-07-01", 4
# границите - тези на робота (bets/robot.py): от 2026-10-01 1.50-1.80, резерва от 2.50 нагоре
# (професионалистът: „нищо под 1.50“, „рисковата от 2.50“); при първото пускане бяха 1.40-1.80 / над 1.80
BAND = robot.SAFE_RANGE
FALLBACK_FROM = robot.RISKY_FROM
MIN_PROB = 0.50
TOTO_LEAGUES, TOTO_BAND = robot.TOTO_LEAGUES, robot.TOTO_BAND
VARIANTS = ("A", "A2", "B", "C")
BOOK_SELS = ("1", "X", "2", "1X", "X2", "12", "O", "U")
GOAL_EVENTS = ("1", "X", "2", "1X", "X2", "12", "O15", "U15", "O", "U", "O35", "U35", "GG", "NG")


def goal_events(p):
    """Шансът по робота за всяко събитие от головете (1/X/2, двоен шанс, линии 1.5/2.5/3.5, двата вкарват)."""
    grid = model.score_grid(p["xg_home"], p["xg_away"])
    k = np.arange(grid.shape[0])
    total = k[:, None] + k[None, :]
    o15, o25, o35 = (float(grid[total > x].sum()) for x in (1.5, 2.5, 3.5))
    gg = float(grid[1:, 1:].sum())
    ev = {s: p[s] for s in ("1", "X", "2", "1X", "X2", "12")}
    ev.update({"O15": o15, "U15": 1 - o15, "O": p.get("O", o25), "U": p.get("U", 1 - o25),
               "O35": o35, "U35": 1 - o35, "GG": p.get("GG", gg), "NG": 1 - p.get("GG", gg)})
    return ev


def happened(sel, hg, ag, cards, corners):
    return robot.hit_any(sel, hg, ag, cards, corners)


def base_rates(conn):
    """Честотата на всяко събитие от головете в лигата - 4 години преди 2023-07-01 (без поглед напред)."""
    since = f"{int(BASE_BEFORE[:4]) - BASE_YEARS}{BASE_BEFORE[4:]}"
    out = {}
    rows = conn.execute("SELECT league, fthg, ftag FROM matches WHERE fthg IS NOT NULL AND date >= ? AND date < ?",
                        (since, BASE_BEFORE)).fetchall()
    per = defaultdict(list)
    for r in rows:
        per[r["league"]].append((r["fthg"], r["ftag"]))
    for lg, xs in per.items():
        if len(xs) < 200:
            continue
        out[lg] = {s: sum(1 for h, a in xs if happened(s, h, a, None, None)) / len(xs) for s in GOAL_EVENTS}
    return out


def choose(variant, cands, base):
    """cands: [(събитие, шанс, коефициент, от букмейкър?, обичайно в лигата)] -> едно събитие."""
    pool = [c for c in cands if variant != "A2" or c[0][0] not in "CK"]
    if variant == "B":
        pool = [c for c in pool if c[3]]
    band = [c for c in pool if BAND[0] <= c[2] <= BAND[1] and c[1] >= MIN_PROB]
    if band:
        if variant == "C":
            return max(band, key=lambda c: c[1] - c[4]), True
        return max(band, key=lambda c: c[1]), True
    risky = [c for c in pool if c[2] >= FALLBACK_FROM]
    return (max(risky, key=lambda c: c[1]), False) if risky else (None, False)


def stat(items):
    n = len(items)
    if not n:
        return None
    h = sum(1 for x in items if x["hit"])
    p = h / n
    booked = [x for x in items if x["book"]]
    out = {"n": n, "hit": p, "hit_se": math.sqrt(p * (1 - p) / n), "said": sum(x["p"] for x in items) / n,
           "base": sum(x["base"] for x in items) / n, "odds": sum(x["odds"] for x in items) / n,
           "in_band": sum(1 for x in items if x["band"]) / n, "book_share": len(booked) / n}
    if booked:
        pr = [(x["odds"] - 1) if x["hit"] else -1 for x in booked]
        mu = sum(pr) / len(pr)
        sd = math.sqrt(sum((v - mu) ** 2 for v in pr) / max(1, len(pr) - 1))
        out.update({"n_book": len(booked), "roi": mu, "roi_se": sd / math.sqrt(len(pr)),
                    "hit_book": sum(1 for x in booked if x["hit"]) / len(booked)})
    kinds = Counter(x["kind"] for x in items)
    out["kinds"] = {k: round(v / n, 4) for k, v in kinds.most_common()}
    return out


def kind_of(sel):
    if sel[0] in "CK":
        return "картони" if sel[0] == "C" else "корнери"
    return {"1": "1/X/2", "X": "1/X/2", "2": "1/X/2", "1X": "двоен шанс", "X2": "двоен шанс", "12": "двоен шанс",
            "GG": "двата вкарват", "NG": "двата вкарват"}.get(sel, "голове над/под")


def main():
    conn = db.init()
    base = base_rates(conn)
    # картони и корнери назад - същият walk-forward като research/extras_backtest.py (в седмичния
    # анализ те току-що са сметнати там - не се смятат втори път)
    ex_rows = extras_backtest.LAST_ROWS
    if ex_rows is None:
        with Pool(4) as pool:
            ex_rows = [r for part in pool.map(extras_backtest.run_league, list(FD)) for r in part]
    ex = defaultdict(dict)
    for r in ex_rows:
        ex[(r["league"], r["date"], r["home"], r["away"])][r["kind"]] = r
    stats = {(m["league"], m["date"], m["home_team"], m["away_team"]): (m["hy"], m["ay"], m["hc"], m["ac"])
             for m in conn.execute("""SELECT m.league, m.date, m.home_team, m.away_team, s.hy, s.ay, s.hc, s.ac
                                        FROM matches m JOIN match_stats s ON s.match_id = m.id WHERE m.date >= '2023-07-01'""")}
    rows = conn.execute("SELECT league, date, home, away, probs_json, prices_json, hg, ag FROM backtest_tips "
                        "WHERE basis = 'model'").fetchall()
    res = {v: {"select": [], "clean": []} for v in VARIANTS}
    per_league = {v: defaultdict(list) for v in VARIANTS}
    skipped = 0
    for r in rows:
        if r["league"] not in base:
            skipped += 1
            continue
        p = json.loads(r["probs_json"])["robot"]
        if p.get("xg_home") is None:
            skipped += 1
            continue
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {}) if r["prices_json"] else {}
        key = (r["league"], r["date"], r["home"], r["away"])
        st = stats.get(key)
        cards = st[0] + st[1] if st and st[0] is not None and st[1] is not None else None
        corners = st[2] + st[3] if st and st[2] is not None and st[3] is not None else None
        b = base[r["league"]]
        cands = []
        for s, prob in goal_events(p).items():
            book = s in BOOK_SELS and bool(avg.get(s))
            odds = avg[s] if book else (1 / prob if prob > 0 else 99.0)
            if (r["league"] in TOTO_LEAGUES and s in ("1", "2") and book and TOTO_BAND[0] <= odds <= TOTO_BAND[1]):
                continue
            cands.append((s, prob, odds, book, b[s]))
        for kind, letter in (("cards", "C"), ("corners", "K")):
            x = ex.get(key, {}).get(kind)
            total = cards if kind == "cards" else corners
            if x and total is not None:
                for side in ("O", "U"):
                    prob = x["p"] if side == "O" else 1 - x["p"]
                    bs = x["base"] if side == "O" else 1 - x["base"]
                    cands.append((f"{letter}{side}{x['line']}", prob, 1 / prob if prob > 0 else 99.0, False, bs))
        period = "select" if r["date"] < SELECT_END else "clean"
        for v in VARIANTS:
            c, in_band = choose(v, cands, b)
            if c is None:
                continue
            ok = happened(c[0], r["hg"], r["ag"], cards, corners)
            if ok is None:
                continue
            item = {"hit": ok, "p": c[1], "odds": c[2], "book": c[3], "base": c[4], "band": in_band, "kind": kind_of(c[0])}
            res[v][period].append(item)
            per_league[v][r["league"]].append(item)
    out = {"generated": str(np.datetime64("today")), "select_end": SELECT_END, "band": BAND, "fallback_from": FALLBACK_FROM,
           "live_variant": robot.ONE_VARIANT, "skipped": skipped,
           "variants": {v: {k: stat(xs) for k, xs in d.items()} for v, d in res.items()}}
    sel = {v: out["variants"][v]["select"]["hit"] for v in VARIANTS}
    best = max(sel, key=sel.get)
    clean = out["variants"]
    top_clean = max(VARIANTS, key=lambda v: clean[v]["clean"]["hit"])
    gap = clean[top_clean]["clean"]["hit"] - clean[best]["clean"]["hit"]
    se = math.hypot(clean[top_clean]["clean"]["hit_se"], clean[best]["clean"]["hit_se"])
    out["chosen"] = best if gap <= 2 * se else None
    # по лиги - вариантът, който е на живо (bets/robot.py: ONE_VARIANT), за да съвпада със сайта
    out["leagues"] = {lg: stat(xs) for lg, xs in per_league[robot.ONE_VARIANT].items()}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for v in VARIANTS:
        for k in ("select", "clean"):
            s = out["variants"][v][k]
            print(f"{v:<3} {k:<6} {s['n']:>6} | познати {s['hit']:.1%} ± {s['hit_se']:.1%} | роботът каза {s['said']:.1%} | "
                  f"в лигата изобщо {s['base']:.1%} | ср.к {s['odds']:.2f} | в {BAND[0]:.2f}-{BAND[1]:.2f} {s['in_band']:.0%} | "
                  f"с коеф. от букмейкър {s['book_share']:.0%}" + (f", доход {s['roi']:+.1%} ± {s['roi_se']:.1%}" if s.get("roi") is not None else ""))
        print("    от какво:", out["variants"][v]["clean"]["kinds"])
    print(f"\nИзбор по протокола: {best} (най-много познати в избора); в чистата най-добрият е {top_clean}, "
          f"разлика {gap:+.1%} при грешка {se:.1%} -> {'ПРИЕТО ' + best if out['chosen'] else 'НЕ СЕ ПОТВЪРЖДАВА'}")
    return out


if __name__ == "__main__":
    main()
