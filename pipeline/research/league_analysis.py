"""
Анализ на първенствата по съветите на професионалиста (2026-09-29).

ПРОТОКОЛ (записан преди пускането):
Данни: football.db, всички лиги с история, мачовете от 2012 насам. Цени: AVG преди мача
(22-те лиги на football-data), затварящата AVGC (16-те държави), 3. Бундеслига - без цени.
Вероятностите - със степенно махане на маржа (bets/market.py). Счупени редове се изхвърлят.

1) ТОТО („къде 1.40 не излиза най-често“): фаворити с коефициент 1.30-1.55 по лиги. Печелят ли
   толкова, колкото обещава коефициентът? Лигата е „тото“ (потвърдено), ако фаворитите печелят
   по-рядко от обещаното с поне 2 стандартни грешки И посоката е същата в 2012-2019 и в
   2019-2026. Само тогава роботът не дава съвет за фаворит в тази лента там.
2) ГОЛОВЕ: среден брой голове, % над 2.5, % „и двата вкарват“, % 0:0 - за последните 3 пълни
   сезона и за текущия. Подредба на лигите. Проверка на думите му: най-малко голове Испания и
   Италия (Египет го няма в данните), най-много Холандия и Германия (1., 2. и 3. Бундеслига).
3) СЛЕД ПАУЗА: първият кръг след 12-25 дни без мач в лигата (национални отбори; от 2026 г.
   септемврийският и октомврийският прозорец са слети, затова до 25 дни), без юни и юли.
   Фаворитите там срещу обещаното, и срещу останалите мачове на същата лига.
4) ДЕРБИТА (bets/derbies.py): равни и фаворит срещу обещаното.

Резултатът: data/leagues.json (за сайта и робота).
"""

import json
import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bets import db, derbies, robot                     # noqa: E402
from bets.leagues import LEAGUES, WITH_HISTORY           # noqa: E402
from bets.market import implied_probs                    # noqa: E402

OUT = ROOT / "data" / "leagues.json"
BAND = robot.TOTO_BAND


def load(conn, code):
    closing = LEAGUES[code].source == "fdnew"
    book = "AVGC" if closing else "AVG"
    odds = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        """SELECT o.match_id, o.odds_home, o.odds_draw, o.odds_away FROM odds o JOIN matches m ON m.id = o.match_id
            WHERE m.league = ? AND o.bookmaker = ?""", (code, book))}
    rows = [dict(r) for r in conn.execute(
        """SELECT m.id, m.date, m.season, m.home_team, m.away_team, m.fthg, m.ftag,
                  s.hy + s.ay AS cards, s.hc + s.ac AS corners
             FROM matches m LEFT JOIN match_stats s ON s.match_id = m.id
            WHERE m.league = ? AND m.fthg IS NOT NULL AND m.date >= '2012-07-01' ORDER BY m.date""", (code,))]
    ok = [(r["id"], odds[r["id"]]) for r in rows if r["id"] in odds]
    probs = implied_probs([o for _, o in ok]) if ok else []
    pmap = {mid: (o, p) for (mid, o), p in zip(ok, probs) if p[0] == p[0]}
    for r in rows:
        r["res"] = robot.result_of(r["fthg"], r["ftag"])
        if r["id"] in pmap:
            o, p = pmap[r["id"]]
            r["odds"], r["p"] = o, list(p)
            r["fav"] = 0 if p[0] >= p[2] else 2
    return rows


def calib(rows):
    """Фаворитът: печели ли колкото обещава коефициентът. Равни: колко срещу обещаното."""
    rows = [r for r in rows if "p" in r]
    n = len(rows)
    if n < 30:
        return None
    exp = sum(r["p"][r["fav"]] for r in rows) / n
    act = sum(r["res"] == "12"[r["fav"] // 2] for r in rows) / n
    se = math.sqrt(sum(r["p"][r["fav"]] * (1 - r["p"][r["fav"]]) for r in rows)) / n
    roi = sum((r["odds"][r["fav"]] - 1) if r["res"] == "12"[r["fav"] // 2] else -1 for r in rows) / n
    dexp = sum(r["p"][1] for r in rows) / n
    dact = sum(r["res"] == "X" for r in rows) / n
    return {"n": n, "fav_expected": round(exp, 4), "fav_actual": round(act, 4), "diff": round(act - exp, 4),
            "se": round(se, 4), "t": round((act - exp) / se, 2) if se else 0.0, "roi": round(roi, 4),
            "draw_expected": round(dexp, 4), "draw_actual": round(dact, 4)}


def goals(rows):
    n = len(rows)
    if not n:
        return None
    tot = [r["fthg"] + r["ftag"] for r in rows]
    cards = [r["cards"] for r in rows if r.get("cards") is not None]
    corners = [r["corners"] for r in rows if r.get("corners") is not None]
    extra = {}
    if len(cards) >= 30:      # картони и корнери - само 22-те лиги на football-data (указание от 2026-09-30)
        extra.update({"cards": round(sum(cards) / len(cards), 2),
                      "cards_o45": round(sum(c >= 5 for c in cards) / len(cards), 4)})
    if len(corners) >= 30:
        extra.update({"corners": round(sum(corners) / len(corners), 2),
                      "corners_o95": round(sum(c >= 10 for c in corners) / len(corners), 4)})
    return {"n": n, "avg": round(sum(tot) / n, 3), "over25": round(sum(t >= 3 for t in tot) / n, 4), **extra,
            "btts": round(sum(r["fthg"] > 0 and r["ftag"] > 0 for r in rows) / n, 4),
            "nil": round(sum(t == 0 for t in tot) / n, 4),
            "home": round(sum(r["res"] == "1" for r in rows) / n, 4),
            "draw": round(sum(r["res"] == "X" for r in rows) / n, 4),
            "away": round(sum(r["res"] == "2" for r in rows) / n, 4)}


def referees(conn, code, since):
    """Съдиите в лигата от since нататък (поне 8 мача): жълти, червени, фаулове, голове на мач.
    Имена има само за Англия и Шотландия."""
    rows = conn.execute(
        """SELECT m.referee, COUNT(*), AVG(s.hy + s.ay), AVG(s.hr + s.ar), AVG(s.hf + s.af), AVG(m.fthg + m.ftag),
                  AVG(CASE WHEN s.hy + s.ay >= 5 THEN 1.0 ELSE 0.0 END), AVG(CASE WHEN m.fthg > m.ftag THEN 1.0 ELSE 0.0 END)
             FROM matches m JOIN match_stats s ON s.match_id = m.id
            WHERE m.league = ? AND m.referee IS NOT NULL AND m.date >= ? AND s.hy IS NOT NULL
            GROUP BY m.referee HAVING COUNT(*) >= 8 ORDER BY AVG(s.hy + s.ay) DESC""", (code, since)).fetchall()
    return [{"name": r[0], "n": r[1], "yellows": round(r[2], 2), "reds": round(r[3] or 0, 2),
             "fouls": round(r[4], 1) if r[4] is not None else None, "goals": round(r[5], 2),
             "o45": round(r[6], 3), "home_win": round(r[7], 3)} for r in rows]


def break_flags(rows):
    dates = sorted({r["date"] for r in rows})
    flagged = {d for d in dates if robot.after_break(dates, d, max_gap=25)}
    for r in rows:
        r["after_break"] = r["date"] in flagged


def seasons_of(rows):
    return sorted({r["season"] for r in rows}, key=lambda s: (s[:4], s))


def main():
    conn = db.init()
    out = {}
    for code in WITH_HISTORY:
        rows = load(conn, code)
        if not rows:
            continue
        seasons = seasons_of(rows)
        current = seasons[-1]
        last3 = seasons[-4:-1] if len(seasons) >= 4 else seasons[:-1]
        low = [r for r in rows if "p" in r and BAND[0] <= r["odds"][r["fav"]] <= BAND[1]]
        early = calib([r for r in low if r["date"] < "2019-07-01"])
        late = calib([r for r in low if r["date"] >= "2019-07-01"])
        whole = calib(low)
        confirmed = bool(whole and early and late and whole["t"] <= -2 and early["diff"] < 0 and late["diff"] < 0)
        break_flags(rows)
        d_rows = [r for r in rows if derbies.is_derby(code, r["home_team"], r["away_team"])]
        out[code] = {
            "title": LEAGUES[code].title, "tier": LEAGUES[code].tier, "odds": "closing" if LEAGUES[code].source == "fdnew" else ("none" if LEAGUES[code].source == "oldb" else "prematch"),
            "seasons": {"current": current, "last3": last3},
            "goals": {"last3": goals([r for r in rows if r["season"] in last3]),
                      "current": goals([r for r in rows if r["season"] == current]),
                      "all": goals(rows)},
            "goals_by_season": {s: goals([r for r in rows if r["season"] == s]) for s in seasons},
            "toto": {"all": whole, "2012-2019": early, "2019-2026": late, "confirmed": confirmed},
            "favourites": calib([r for r in rows if "p" in r]),
            "after_break": {"after": calib([r for r in rows if r["after_break"]]),
                            "other": calib([r for r in rows if not r["after_break"]]),
                            "goals_after": goals([r for r in rows if r["after_break"]])},
            "derbies": {"n": len(d_rows), "calib": calib(d_rows)},
            "referees": referees(conn, code, f"{date.today().year - 2}-{date.today().isoformat()[5:]}"),
        }
        print(f"{code:<5} {len(rows):>6} мача  голове {out[code]['goals']['last3']['avg'] if out[code]['goals']['last3'] else '-'}  "
              f"тото {whole['diff'] if whole else '-'} t {whole['t'] if whole else '-'} {'ПОТВЪРДЕНО' if confirmed else ''}")
    # сборно: всички мачове след пауза срещу останалите
    all_rows = []
    for code in WITH_HISTORY:
        rows = load(conn, code)
        break_flags(rows)
        all_rows += rows
    summary = {"after_break": calib([r for r in all_rows if r["after_break"]]),
               "other": calib([r for r in all_rows if not r["after_break"]]),
               "after_break_2012_2019": calib([r for r in all_rows if r["after_break"] and r["date"] < "2019-07-01"]),
               "after_break_2019_2026": calib([r for r in all_rows if r["after_break"] and r["date"] >= "2019-07-01"]),
               "other_2012_2019": calib([r for r in all_rows if not r["after_break"] and r["date"] < "2019-07-01"]),
               "other_2019_2026": calib([r for r in all_rows if not r["after_break"] and r["date"] >= "2019-07-01"]),
               "low_band_after": calib([r for r in all_rows if r["after_break"] and "p" in r and BAND[0] <= r["odds"][r["fav"]] <= BAND[1]]),
               "low_band_other": calib([r for r in all_rows if not r["after_break"] and "p" in r and BAND[0] <= r["odds"][r["fav"]] <= BAND[1]])}
    ranking = sorted((c for c in out if out[c]["goals"]["last3"]), key=lambda c: -out[c]["goals"]["last3"]["avg"])
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"generated": date.today().isoformat(), "band": list(BAND), "leagues": out,
                               "summary": summary, "goals_ranking": ranking}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("\nГолове (последните 3 сезона), най-много -> най-малко:")
    for i, c in enumerate(ranking, 1):
        g = out[c]["goals"]["last3"]
        print(f"{i:>3}. {out[c]['title']:<38} {g['avg']:.2f}  над 2.5 {g['over25']:.0%}  и двата {g['btts']:.0%}  0:0 {g['nil']:.1%}")
    print("\nСлед пауза:", summary["after_break"], "\nОстанали:", summary["other"])
    print("  2012-2019:", summary["after_break_2012_2019"]["diff"], "срещу", summary["other_2012_2019"]["diff"])
    print("  2019-2026:", summary["after_break_2019_2026"]["diff"], "срещу", summary["other_2019_2026"]["diff"])
    return json.loads(OUT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
