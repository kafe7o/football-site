"""
Кой ще е шампион, кой ще влезе в Европа, кой ще изпадне (съветът на професионалиста за
дългосрочни залози „на база предишни години и старта на сезона“, 2026-09-29).

Метод: оставащите мачове на редовния сезон се изиграват 10 000 пъти със силите от модела
(атака/защита на всеки отбор - те вече съдържат предишните сезони със затихване и старта на
този). Всеки мач: голове по Poisson. Точки 3/1/0, при равенство голова разлика, после вкарани.

Оставащите мачове не се теглят от никъде: всеки отбор приема всеки друг определен брой пъти
(League.rr). Изиграните се вадят. Когато мачовете на двойка са нечетен брой (Шотландия,
Швейцария - по 3 пъти), домакин на последния е този, който е приемал по-малко.

Не се симулират: лиги с плейофи за титлата или конференции (Мексико, САЩ, Аржентина). Лигите,
които се делят след редовния сезон, се симулират до деленето - отбелязано на сайта.

Коефициенти за шампион odds API не дава за лигите (провери 2026-09-29) - затова сравнение с
букмейкърите няма; числата са на модела, който е по-неточен от пазара.
"""

import logging
from datetime import date, timedelta

import numpy as np

from .leagues import LEAGUES

log = logging.getLogger(__name__)

RUNS = 10_000
STALE_DAYS = 45       # лига без мач толкова дни - сезонът е свършил (или данните са спрели)
MEETINGS = {"SC0": 3, "SWZ": 3}


def meetings(code):
    lg = LEAGUES[code]
    return MEETINGS.get(code, int(round(2 * lg.rr)))


def current_rows(conn, code):
    last = conn.execute("SELECT season, MAX(date) d FROM matches WHERE league = ? AND fthg IS NOT NULL "
                        "GROUP BY season ORDER BY d DESC LIMIT 1", (code,)).fetchone()
    if last is None:
        return None, []
    rows = conn.execute("SELECT home_team, away_team, fthg, ftag, date FROM matches WHERE league = ? AND season = ?",
                        (code, last["season"])).fetchall()
    return last, rows


def simulate(conn, code, fitted, runs=RUNS, seed=7, today=None):
    lg = LEAGUES[code]
    per_pair = meetings(code)
    if per_pair == 0 or fitted is None:
        return None
    last, rows = current_rows(conn, code)
    today = today or date.today()
    if not rows or (today - date.fromisoformat(last["d"])).days > STALE_DAYS:
        return None
    played = [r for r in rows if r["fthg"] is not None]
    teams = sorted({r["home_team"] for r in rows} | {r["away_team"] for r in rows})
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    if n < 6:
        return None
    pts = np.zeros(n)
    gd = np.zeros(n)
    gf = np.zeros(n)
    games = np.zeros(n, dtype=int)
    hosted = np.zeros((n, n), dtype=int)
    for r in played:
        h, a = idx[r["home_team"]], idx[r["away_team"]]
        hosted[h, a] += 1
        games[h] += 1
        games[a] += 1
        gf[h] += r["fthg"]
        gf[a] += r["ftag"]
        gd[h] += r["fthg"] - r["ftag"]
        gd[a] += r["ftag"] - r["fthg"]
        if r["fthg"] > r["ftag"]:
            pts[h] += 3
        elif r["fthg"] < r["ftag"]:
            pts[a] += 3
        else:
            pts[h] += 1
            pts[a] += 1
    # оставащите мачове
    remaining = []
    for i in range(n):
        for j in range(i + 1, n):
            done = hosted[i, j] + hosted[j, i]
            left = per_pair - done
            hi, hj = hosted[i, j], hosted[j, i]
            for _ in range(max(0, left)):
                if hi <= hj:
                    remaining.append((i, j))
                    hi += 1
                else:
                    remaining.append((j, i))
                    hj += 1
    total_games = per_pair * (n - 1)
    if games.max() > total_games:
        # повече изиграни мачове от редовния сезон - форматът е друг (плейофи или грешни данни)
        log.warning("%s: отбор с %d мача при редовен сезон %d - без симулация", code, games.max(), total_games)
        return None
    avg_home = float(np.mean([r["fthg"] for r in played])) if played else 1.4
    avg_away = float(np.mean([r["ftag"] for r in played])) if played else 1.1
    lam = np.array([fitted.lambdas(teams[h], teams[a]) or (avg_home, avg_away) for h, a in remaining]).reshape(-1, 2)
    rng = np.random.default_rng(seed)
    final_pts = np.tile(pts, (runs, 1))
    final_gd = np.tile(gd, (runs, 1))
    final_gf = np.tile(gf, (runs, 1))
    if remaining:
        hg = rng.poisson(lam[:, 0], size=(runs, len(remaining)))
        ag = rng.poisson(lam[:, 1], size=(runs, len(remaining)))
        home_idx = np.array([h for h, _ in remaining])
        away_idx = np.array([a for _, a in remaining])
        win_h = (hg > ag).astype(float)
        draw = (hg == ag).astype(float)
        win_a = (hg < ag).astype(float)
        for k in range(len(remaining)):
            h, a = home_idx[k], away_idx[k]
            final_pts[:, h] += 3 * win_h[:, k] + draw[:, k]
            final_pts[:, a] += 3 * win_a[:, k] + draw[:, k]
            final_gd[:, h] += hg[:, k] - ag[:, k]
            final_gd[:, a] += ag[:, k] - hg[:, k]
            final_gf[:, h] += hg[:, k]
            final_gf[:, a] += ag[:, k]
    key = final_pts * 1e6 + final_gd * 1e3 + final_gf + rng.random((runs, n)) * 1e-3
    order = np.argsort(-key, axis=1)
    pos = np.empty_like(order)
    rows_idx = np.arange(runs)[:, None]
    pos[rows_idx, order] = np.arange(n)[None, :]
    up, down = lg.up, lg.down
    table = []
    for t in range(n):
        table.append({"team": teams[t], "played": int(games[t]), "pts": int(pts[t]), "gd": int(gd[t]),
                      "gf": int(gf[t]),
                      "p_first": round(float((pos[:, t] == 0).mean()), 4),
                      "p_up": round(float((pos[:, t] < up).mean()), 4),
                      "p_down": round(float((pos[:, t] >= n - down).mean()), 4) if down else 0.0,
                      "exp_pts": round(float(final_pts[:, t].mean()), 1),
                      "exp_pos": round(float(pos[:, t].mean() + 1), 1)})
    table.sort(key=lambda r: (-r["pts"], -r["gd"], -r["gf"], r["team"]))
    return {"league": code, "season": last["season"], "teams": n, "per_pair": per_pair,
            "rounds_total": total_games, "remaining": len(remaining), "runs": runs, "up": up, "down": down,
            "split": lg.split, "table": table}
