"""
НАЙ-СИГУРНИТЕ МАЧОВЕ (указание на професионалиста, 2026-10-08, предадено от собственика).

Той казва: „цялата ми идея е да му намалим обхвата от мачове и да го помолим да поддържа по-висока успеваемост на
мачовете, които ни дава. Може да не ни дава всичките 11 мача от Испания втора лига, да ни даде 4, ама тия 4 да са за него
най-сигурните. Освен най-сигурната прогноза за даден мач, да ни дава и най-сигурните мачове - не всички. Нас ни трябват
10 000 евро, за да играем всички мачове, всички колонки; ако играя 5 дни така, трябва поне един ден да ми е вървяло.“

Какво се мери: ако от мачовете на първенство за деня се обявят за „най-сигурни“ само някои (по шанса на робота за
едната му прогноза), колко по-често излиза прогнозата им, отколкото на останалите; колко мача на ден остават; и какво се
получава, ако от тях се играят много колонки (системата, която той описва).

ПРОТОКОЛ (записан преди пускането):
Данни: backtest_tips (роботът назад, walk-forward по месеци, само с модел), 2023-07-01 до днес. Единица: първенство × ден
на мача (както на живо: записът е по ден). Кандидат: мач с модел, който НЕ е дерби (bets/derbies.py, сегашният списък) и
има едната прогноза (robot.one_pick без картони и корнери - за тях назад няма шансове; това е вариант A2 от
one_pick_backtest.py: 64.3% / 64.0% срещу 64.3% / 63.8% за A). Подреждането е САМО по шанса на робота за едната прогноза
(решение на собственика от 30.09: процентът да не идва от коефициентите); равните - по името на мача.
Правило = (дял, закръгляне, праг): от n кандидата на първенството за деня се взимат най-вероятните k = quota(n), а от тях
- само тези с шанс ≥ праг. Дял 1/2, 1/3, 1/4; закръгляне „нагоре“ (11 мача при 1/3 -> 4, както в примера му) и
„най-близо, поне 1“; праг 0 / 60% / 65%. За сравнение: само праг без дял (60 / 65 / 70 / 75%), най-много 4 на първенство
и ден, и дял от ВСИЧКИ мачове на деня (без разделяне по първенства).
Мери се във всяка правило и период: колко от кандидатите са избрани (покритие) и колко мача на ден, колко често излиза
прогнозата (± грешка), какъв шанс е казал роботът (калибрация), колко излиза при НЕизбраните и разликата (в грешки), доход
само при мачове с истински коефициент (иначе е кръгова сметка).
ИЗБОР: до 2025-07-01; ЧИСТА ПРОВЕРКА: от там. Допустими са само правила с покритие 25%-45% от кандидатите в избора
(„4 от 11“ е 36%). От тях се приема това с най-висок процент познати в избора; при разлика под 1 грешка - по-простото
(по-малко параметри: без праг, после без закръгляне). ПОТВЪРЖДАВА СЕ, ако в чистата проверка: (1) избраните излизат с
повече от неизбраните с над 2 грешки на разликата; (2) не са с повече от 2 грешки под най-добрия допустим вариант там;
(3) казаният шанс не е над излизането с повече от 3 пункта. Иначе - нищо не се приема и собственикът получава числата.

СИСТЕМАТА (вторично, без избор): във всеки ден с поне 3 избрани мача - всички колонки по 3 от най-много 7-те най-сигурни
(до 35 колонки, ~ „30 колонки“ на собственика). Мери се: дни без нито една минала колонка; връщане от 1 € на колонка
(само колонки, в които всеки мач има истински коефициент); и в 5 последователни такива дни - колко често нито един ден няма
минала колонка и колко често общият резултат е положителен. Резултатът: data/sure_backtest.json.
"""

import json
import math
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, derbies, robot, sure                      # noqa: E402

OUT = ROOT / "data" / "sure_backtest.json"
SELECT_END = "2025-07-01"
COVER_MIN, COVER_MAX = 0.25, 0.45
POOL, SYSTEM_SIZE, WINDOW = 7, 3, 5


def load():
    conn = db.init()
    items = []
    for r in conn.execute("SELECT league, date, home, away, probs_json, prices_json, hg, ag FROM backtest_tips "
                          "WHERE basis = 'model' AND date >= '2023-07-01' AND hg IS NOT NULL"):
        p = json.loads(r["probs_json"])["robot"]
        if p.get("xg_home") is None or derbies.is_derby(r["league"], r["home"], r["away"]):
            continue
        avg = (json.loads(r["prices_json"]) or {}).get("avg", {}) if r["prices_json"] else {}
        one = robot.one_pick(p, avg, None, r["league"])
        if not one:
            continue
        items.append({"key": f"{r['league']}|{r['date']}|{r['home']}|{r['away']}", "league": r["league"], "date": r["date"],
                      "p": one["p"], "odds": one["odds"], "book": one.get("src") == "book", "sel": one["sel"],
                      "hit": bool(robot.hit_any(one["sel"], r["hg"], r["ag"]))})
    return items


def groups_of(items, by):
    g = defaultdict(list)
    for x in items:
        g[(x["league"], x["date"]) if by == "league" else x["date"]].append(x)
    return g


def choose(items, rule):
    """rule = (вид, ...) -> множество от ключове на избраните мачове за целия период."""
    kind = rule[0]
    chosen = set()
    if kind == "share":                       # (share, f, rounding, min_p)
        _, f, rounding, min_p = rule
        for ms in groups_of(items, "league").values():
            chosen |= sure.pick([(x["key"], x["p"]) for x in ms], f, rounding, min_p)
    elif kind == "dayshare":                  # дял от всички мачове на деня
        _, f, rounding, min_p = rule
        for ms in groups_of(items, "day").values():
            chosen |= sure.pick([(x["key"], x["p"]) for x in ms], f, rounding, min_p)
    elif kind == "threshold":
        chosen = {x["key"] for x in items if x["p"] >= rule[1]}
    elif kind == "cap":                       # най-много N на първенство и ден, с праг
        _, n, min_p = rule
        for ms in groups_of(items, "league").values():
            ranked = sorted(ms, key=lambda x: (-x["p"], x["key"]))[:n]
            chosen |= {x["key"] for x in ranked if x["p"] >= min_p}
    return chosen


def stat(xs):
    n = len(xs)
    if not n:
        return None
    h = sum(x["hit"] for x in xs) / n
    out = {"n": n, "hit": h, "se": math.sqrt(h * (1 - h) / n), "said": sum(x["p"] for x in xs) / n,
           "odds": sum(x["odds"] for x in xs) / n}
    booked = [x for x in xs if x["book"]]
    if booked:
        pr = [(x["odds"] - 1) if x["hit"] else -1 for x in booked]
        mu = sum(pr) / len(pr)
        sd = math.sqrt(sum((v - mu) ** 2 for v in pr) / max(1, len(pr) - 1))
        out.update({"roi": mu, "roi_se": sd / math.sqrt(len(pr)), "n_book": len(booked)})
    return out


def evaluate(items, rule):
    chosen = choose(items, rule)
    res = {}
    for per, test in (("select", lambda d: d < SELECT_END), ("clean", lambda d: d >= SELECT_END)):
        part = [x for x in items if test(x["date"])]
        yes = [x for x in part if x["key"] in chosen]
        no = [x for x in part if x["key"] not in chosen]
        s, o = stat(yes), stat(no)
        days = {x["date"] for x in yes}
        d = {"cover": len(yes) / len(part), "per_day": len(yes) / len(days) if days else 0, "sure": s, "rest": o}
        if s and o:
            d["diff"] = s["hit"] - o["hit"]
            d["diff_se"] = math.hypot(s["se"], o["se"])
        res[per] = d
    return res, chosen


def simple(rule):
    """Колкото по-малко параметри, толкова по-просто: (без праг, закръгляне „нагоре“) са най-простите."""
    if rule[0] != "share":
        return 9
    return (rule[3] > 0) * 2 + (rule[2] != "ceil")


def system(items, chosen):
    """Всички колонки по 3 от най-много POOL най-сигурни мачове на ден (дни с поне 3 избрани)."""
    days = defaultdict(list)
    for x in items:
        if x["key"] in chosen:
            days[x["date"]].append(x)
    daily = []
    for day in sorted(days):
        pool = sorted(days[day], key=lambda x: (-x["p"], x["key"]))[:POOL]
        if len(pool) < SYSTEM_SIZE:
            continue
        cols = list(combinations(pool, SYSTEM_SIZE))
        won = [all(c["hit"] for c in col) for col in cols]
        book = [col for col in cols if all(c["book"] for c in col)]
        ret = [math.prod(c["odds"] for c in col) if all(c["hit"] for c in col) else 0.0 for col in book]
        daily.append({"day": day, "cols": len(cols), "won": sum(won), "book_cols": len(book),
                      "book_ret": sum(ret), "all_lost": sum(won) == 0,
                      "net": (sum(ret) - len(book)) if book else None})
    out = {}
    for per, test in (("select", lambda d: d < SELECT_END), ("clean", lambda d: d >= SELECT_END)):
        ds = [d for d in daily if test(d["day"])]
        if not ds:
            continue
        book_cols = sum(d["book_cols"] for d in ds)
        wins = [ds[i:i + WINDOW] for i in range(len(ds) - WINDOW + 1)]
        netw = [sum(d["net"] for d in w if d["net"] is not None) for w in wins]
        out[per] = {"days": len(ds), "cols_per_day": sum(d["cols"] for d in ds) / len(ds),
                    "day_all_lost": sum(d["all_lost"] for d in ds) / len(ds),
                    "return_per_euro": (sum(d["book_ret"] for d in ds) / book_cols) if book_cols else None,
                    "windows": len(wins),
                    "window_no_win": sum(all(d["all_lost"] for d in w) for w in wins) / len(wins) if wins else None,
                    "window_net_positive": sum(v > 0 for v in netw) / len(netw) if wins else None}
    return out


def main():
    items = load()
    sel_n = sum(x["date"] < SELECT_END for x in items)
    print(f"кандидати: {len(items)} (избор {sel_n}, чиста {len(items) - sel_n}); общо излиза "
          f"{sum(x['hit'] for x in items) / len(items):.1%}")
    rules = []
    for f in (1 / 2, 1 / 3, 1 / 4):
        for rounding in ("ceil", "near"):
            for min_p in (0.0, 0.60, 0.65):
                rules.append(("share", f, rounding, min_p))
    rules += [("threshold", t) for t in (0.60, 0.65, 0.70, 0.75)]
    rules += [("cap", 4, 0.60), ("cap", 3, 0.60)]
    rules += [("dayshare", f, "near", 0.0) for f in (1 / 3, 1 / 4)]
    res, chosen_of = {}, {}
    for rule in rules:
        name = "|".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in rule)
        res[name], chosen_of[name] = evaluate(items, rule)
        s, c = res[name]["select"], res[name]["clean"]
        print(f"{name:<24} покритие {s['cover']:.0%}/{c['cover']:.0%} | {c['per_day']:.1f} на ден | познати "
              f"{s['sure']['hit']:.1%} ± {s['sure']['se']:.1%} / {c['sure']['hit']:.1%} ± {c['sure']['se']:.1%} | "
              f"казано {s['sure']['said']:.1%} / {c['sure']['said']:.1%} | неизбрани {s['rest']['hit']:.1%} / {c['rest']['hit']:.1%}")
    eligible = [n for n, r in res.items() if COVER_MIN <= r["select"]["cover"] <= COVER_MAX]
    best_hit = max(res[n]["select"]["sure"]["hit"] for n in eligible)
    best_se = min(res[n]["select"]["sure"]["se"] for n in eligible)
    near = [n for n in eligible if res[n]["select"]["sure"]["hit"] >= best_hit - best_se]       # в рамките на 1 грешка
    chosen = min(near, key=lambda n: (simple(tuple(_rule(n))), -res[n]["select"]["sure"]["hit"]))
    # правилото на сайта (bets/sure.py): тук - сравнено с избраното по протокола
    live = "share|{:.3f}|{}|{:.3f}".format(sure.SHARE, sure.ROUNDING, sure.MIN_P)
    if live not in res:
        res[live], chosen_of[live] = evaluate(items, ("share", sure.SHARE, sure.ROUNDING, sure.MIN_P))
    top_name = max(eligible, key=lambda n: res[n]["clean"]["sure"]["hit"])
    top_clean, top_se = res[top_name]["clean"]["sure"]["hit"], res[top_name]["clean"]["sure"]["se"]

    def verdict(name):
        c = res[name]["clean"]
        return {"diff_over_2se": c["diff"] > 2 * c["diff_se"],
                "within_2se_of_best": top_clean - c["sure"]["hit"] <= 2 * math.hypot(top_se, c["sure"]["se"]),
                "calibrated_3pp": c["sure"]["said"] - c["sure"]["hit"] <= 0.03}
    checks_protocol, checks_live = verdict(chosen), verdict(live)
    print(f"\nдопустими (покритие {COVER_MIN:.0%}-{COVER_MAX:.0%} в избора): {eligible}")
    print(f"ИЗБРАНО по протокола: {chosen}; проверки в чистата: {checks_protocol}")
    print(f"НА САЙТА (по примера на майстора 11 -> 4): {live}; проверки в чистата: {checks_live}")
    for tag, name in (("протокол", chosen), ("сайт", live)):
        c, s_ = res[name]["clean"], res[name]["select"]
        print(f"  {tag}: избор {s_['sure']['hit']:.1%} ± {s_['sure']['se']:.1%} (неизбрани {s_['rest']['hit']:.1%}); "
              f"чиста {c['sure']['hit']:.1%} ± {c['sure']['se']:.1%} при казани {c['sure']['said']:.1%} "
              f"(неизбрани {c['rest']['hit']:.1%}); {c['per_day']:.1f} мача на ден, {c['cover']:.0%} от кандидатите")
    sysres = {"live": system(items, chosen_of[live]), "protocol": system(items, chosen_of[chosen])}
    for per, s_ in sysres["live"].items():
        print(f"системата на сайта ({per}): {s_['days']} дни, {s_['cols_per_day']:.0f} колонки на ден | дни без нито една минала "
              f"{s_['day_all_lost']:.0%} | връща {s_['return_per_euro']:.2f} € от 1 € на колонка | в {WINDOW} дни подред: "
              f"нито един ден с минала {s_['window_no_win']:.1%}, общо плюс {s_['window_net_positive']:.1%}")
    # по първенства: избраните срещу всичките (за таб „Лиги“)
    per_league = {}
    for lg in sorted({x["league"] for x in items}):
        allx = [x for x in items if x["league"] == lg and x["date"] >= "2023-07-01"]
        yes = [x for x in allx if x["key"] in chosen_of[live]]
        per_league[lg] = {"all": stat(allx), "sure": stat(yes)}
    out = {"generated": str(__import__("datetime").date.today()), "select_end": SELECT_END, "protocol_choice": chosen,
           "live_rule": live, "checks_protocol": checks_protocol, "checks_live": checks_live, "eligible": eligible,
           "rules": res, "system": sysres, "leagues": per_league}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _rule(name):
    parts = name.split("|")
    if parts[0] == "share":
        return ("share", float(parts[1]), parts[2], float(parts[3]))
    return (parts[0],)


if __name__ == "__main__":
    main()
