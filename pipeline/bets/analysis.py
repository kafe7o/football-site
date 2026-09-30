"""
Подробният анализ на всеки мач (указание на професионалиста, 2026-09-30: „наистина да дава
подробен анализ за всеки един от мачовете, да не е просто съветче“).

Всичко е собствена оценка на робота или факти от историята - нищо не идва от коефициентите:
  - класиране в момента (точки, място, голова разлика) - от изиграните мачове на сезона;
  - форма: последните 5 мача общо, у дома за домакина и като гост за госта;
  - директни срещи: последните 6 (в същата държава);
  - сезонът до момента на мач: голове, картони, корнери, удари (картони/корнери/удари - само
    22-те лиги на football-data);
  - моделът: очаквани голове, най-вероятните резултати, над/под 1.5/2.5/3.5, двата вкарват;
  - картони (със съдията - Англия и Шотландия) и корнери: очакван брой и над/под (bets/extras.py);
  - текст на български от горните числа.
"""

import numpy as np

from . import extras, model
from .leagues import LEAGUES

FORM_N = 5
H2H_N = 6
LETTER = {"W": "П", "D": "Р", "L": "З"}


def ordinal(n):
    """Място: 1-во, 2-ро, 3-то, 7-мо, 11-о..."""
    if n % 100 in (11, 12, 13, 14, 17, 18):
        return f"{n}-о"
    return f"{n}-" + {1: "во", 2: "ро", 3: "то", 4: "то", 7: "мо", 8: "мо"}.get(n % 10, "о")


def pct(x):
    return f"{x * 100:.0f}%"


class Context:
    """Кеш за едно пускане: таблиците по лиги, моделите за картони и корнери."""

    def __init__(self, conn, now):
        self.conn = conn
        self.now = now
        self.tables = {}
        self.extras = {}

    def table(self, league):
        if league not in self.tables:
            self.tables[league] = standings(self.conn, league)
        return self.tables[league]

    def extra(self, league, kind):
        key = (league, kind)
        if key not in self.extras:
            self.extras[key] = (extras.fitted_cached(self.conn, league, kind, self.now)
                                if LEAGUES[league].source == "fd" else (None, None))
        return self.extras[key]


def standings(conn, league):
    row = conn.execute("SELECT season FROM matches WHERE league = ? AND fthg IS NOT NULL ORDER BY date DESC LIMIT 1",
                       (league,)).fetchone()
    if row is None:
        return {}
    season = row[0]
    t = {}
    for h, a, hg, ag in conn.execute("SELECT home_team, away_team, fthg, ftag FROM matches WHERE league = ? AND season = ? "
                                     "AND fthg IS NOT NULL", (league, season)):
        for team, gf, ga in ((h, hg, ag), (a, ag, hg)):
            x = t.setdefault(team, {"p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0})
            x["p"] += 1
            x["gf"] += gf
            x["ga"] += ga
            if gf > ga:
                x["w"] += 1
                x["pts"] += 3
            elif gf == ga:
                x["d"] += 1
                x["pts"] += 1
            else:
                x["l"] += 1
    order = sorted(t, key=lambda k: (-t[k]["pts"], -(t[k]["gf"] - t[k]["ga"]), -t[k]["gf"], k))
    for i, k in enumerate(order, 1):
        t[k]["pos"] = i
    return {"season": season, "teams": t, "n": len(order)}


def team_matches(conn, league, team, before, n=FORM_N, venue=None):
    where = {"home": "home_team = ?", "away": "away_team = ?"}.get(venue, "(home_team = ? OR away_team = ?)")
    args = (team,) if venue else (team, team)
    out = []
    for r in conn.execute(f"""SELECT date, home_team, away_team, fthg, ftag FROM matches
                               WHERE league = ? AND {where} AND fthg IS NOT NULL AND date < ?
                               ORDER BY date DESC LIMIT ?""", (league, *args, before, n)):
        home = r[1] == team
        gf, ga = (r[3], r[4]) if home else (r[4], r[3])
        out.append({"d": r[0], "o": r[2] if home else r[1], "v": "д" if home else "г", "gf": gf, "ga": ga,
                    "r": "W" if gf > ga else ("D" if gf == ga else "L")})
    return out


def head_to_head(conn, league, home, away, before, n=H2H_N):
    country = LEAGUES[league].country
    codes = [c for c, lg in LEAGUES.items() if lg.country == country and lg.has_history]
    rows = conn.execute(
        f"""SELECT date, home_team, away_team, fthg, ftag FROM matches
             WHERE league IN ({",".join("?" * len(codes))}) AND fthg IS NOT NULL AND date < ?
               AND ((home_team = ? AND away_team = ?) OR (home_team = ? AND away_team = ?))
             ORDER BY date DESC LIMIT ?""", (*codes, before, home, away, away, home, n)).fetchall()
    return [{"d": r[0], "h": r[1], "a": r[2], "s": [r[3], r[4]]} for r in rows]


def season_stats(conn, league, season, team):
    """Средно на мач за сезона: голове вкарани/допуснати; картони, корнери, удари (ако ги има)."""
    r = conn.execute(
        """SELECT COUNT(*),
                  AVG(CASE WHEN m.home_team = ? THEN m.fthg ELSE m.ftag END),
                  AVG(CASE WHEN m.home_team = ? THEN m.ftag ELSE m.fthg END),
                  AVG(CASE WHEN m.home_team = ? THEN s.hy ELSE s.ay END),
                  AVG(CASE WHEN m.home_team = ? THEN s.hc ELSE s.ac END),
                  AVG(CASE WHEN m.home_team = ? THEN s.hs ELSE s.as_ END),
                  AVG(CASE WHEN m.home_team = ? THEN s.hst ELSE s.ast END),
                  AVG(s.hy + s.ay), AVG(s.hc + s.ac)
             FROM matches m LEFT JOIN match_stats s ON s.match_id = m.id
            WHERE m.league = ? AND m.season = ? AND m.fthg IS NOT NULL AND (m.home_team = ? OR m.away_team = ?)""",
        (team, team, team, team, team, team, league, season, team, team)).fetchone()
    if not r or not r[0]:
        return None
    rnd = lambda x: None if x is None else round(x, 2)
    return {"n": r[0], "gf": rnd(r[1]), "ga": rnd(r[2]), "cards": rnd(r[3]), "corners": rnd(r[4]),
            "shots": rnd(r[5]), "sot": rnd(r[6]), "match_cards": rnd(r[7]), "match_corners": rnd(r[8])}


def goals_detail(lam_h, lam_a):
    grid = model.score_grid(lam_h, lam_a)
    k = np.arange(grid.shape[0])
    total = k[:, None] + k[None, :]
    flat = sorted(((float(grid[i, j]), i, j) for i in range(6) for j in range(6)), reverse=True)[:3]
    return {"scores": [[i, j, round(p, 4)] for p, i, j in flat],
            "o15": round(float(grid[total >= 2].sum()), 4), "o25": round(float(grid[total >= 3].sum()), 4),
            "o35": round(float(grid[total >= 4].sum()), 4), "btts": round(float(grid[1:, 1:].sum()), 4),
            "nil": round(float(grid[0, 0]), 4)}


def form_text(form):
    if not form:
        return "няма мачове"
    letters = " ".join(LETTER[f["r"]] for f in form)
    gf, ga = sum(f["gf"] for f in form), sum(f["ga"] for f in form)
    return f"{letters} ({gf}:{ga})"


def build(ctx, fx, fitted, names, referee=None):
    """Анализът на мача. names - имената за показване (домакин, гост). None, ако лигата няма история."""
    conn, league = ctx.conn, fx["league"]
    lg = LEAGUES.get(league)
    if not lg or not lg.has_history or not fx["mapped"]:
        return None
    home, away = fx["home"], fx["away"]
    hn, an = names
    before = fx["kickoff"][:10]
    table = ctx.table(league)
    th, ta = (table.get("teams") or {}).get(home), (table.get("teams") or {}).get(away)
    out = {"form": {"h": team_matches(conn, league, home, before), "a": team_matches(conn, league, away, before),
                    "hh": team_matches(conn, league, home, before, venue="home"),
                    "aa": team_matches(conn, league, away, before, venue="away")},
           "h2h": head_to_head(conn, league, home, away, before)}
    if th and ta:
        out["table"] = {"h": {k: th[k] for k in ("pos", "pts", "p", "gf", "ga")},
                        "a": {k: ta[k] for k in ("pos", "pts", "p", "gf", "ga")}, "n": table["n"]}
    season = table.get("season")
    if season:
        out["season"] = {"h": season_stats(conn, league, season, home), "a": season_stats(conn, league, season, away)}
    lam = fitted.lambdas(home, away) if fitted is not None else None
    if lam and min(fitted.seen(home), fitted.seen(away)) >= model.MIN_EFFECTIVE_MATCHES:
        out["goals"] = {"xg": [round(lam[0], 2), round(lam[1], 2)], **goals_detail(*lam)}
    for kind in extras.KINDS:
        fm, k = ctx.extra(league, kind)
        if fm is None:
            continue
        factor, ref_avg, ref_n = extras.referee_factor(conn, league, referee, before, kind)
        pr = extras.predict(fm, k, home, away, factor)
        if pr:
            pr["skill"] = extras.skill(kind)
            pr["base"], pr["base_n"] = extras.base_rate(conn, league, kind, pr["line"], before)
            if kind == "cards" and referee:
                pr["referee"] = {"name": referee, "avg": round(ref_avg, 2) if ref_avg else None, "n": ref_n}
            out[kind] = pr
    out["text"] = text(out, home, hn, an, fx.get("flags") or {})
    return out


def text(a, home, hn, an, flags):
    lines = []
    t = a.get("table")
    if t:
        lines.append(f"{hn} ({ordinal(t['h']['pos'])} място, {t['h']['pts']} т. от {t['h']['p']} мача) срещу "
                     f"{an} ({ordinal(t['a']['pos'])} място, {t['a']['pts']} т. от {t['a']['p']} мача).")
    f = a["form"]
    lines.append(f"Форма в последните 5 (най-новият мач пръв): {hn} {form_text(f['h'])}, {an} {form_text(f['a'])}. "
                 f"У дома {hn}: {form_text(f['hh'])}; като гост {an}: {form_text(f['aa'])}.")
    g = a.get("goals")
    if g:
        best = g["scores"][0]
        lines.append(f"Роботът очаква {g['xg'][0]:.1f} : {g['xg'][1]:.1f} гола. Най-вероятен резултат {best[0]}:{best[1]} "
                     f"({pct(best[2])}), после " + ", ".join(f"{s[0]}:{s[1]} ({pct(s[2])})" for s in g["scores"][1:]) + ".")
        lines.append(f"Голове: над 1.5 - {pct(g['o15'])}, над 2.5 - {pct(g['o25'])}, над 3.5 - {pct(g['o35'])}; "
                     f"двата вкарват - {pct(g['btts'])}.")
    s = a.get("season") or {}
    sh, sa = s.get("h"), s.get("a")
    if sh and sa and sh.get("cards") is not None and sa.get("cards") is not None:
        lines.append(f"Сезонът до момента, средно на мач: {hn} вкарва {sh['gf']:.1f} и допуска {sh['ga']:.1f}, "
                     f"{sh['cards']:.1f} жълти, {sh['corners']:.1f} корнера; {an} - {sa['gf']:.1f} и {sa['ga']:.1f}, "
                     f"{sa['cards']:.1f} жълти, {sa['corners']:.1f} корнера.")
    elif sh and sa:
        lines.append(f"Сезонът до момента, средно на мач: {hn} вкарва {sh['gf']:.1f} и допуска {sh['ga']:.1f}; "
                     f"{an} - {sa['gf']:.1f} и {sa['ga']:.1f}.")
    for kind, name, unit in (("cards", "Картони", "жълти"), ("corners", "Корнери", "")):
        c = a.get(kind)
        if not c:
            continue
        ref = c.get("referee")
        ref_txt = (f" Съдия {ref['name']} - средно {ref['avg']:.1f} жълти в {ref['n']} мача за 2 години."
                   if ref and ref.get("avg") else (f" Съдия {ref['name']}." if ref else ""))
        head = f"{name}: очаквани {c['total']:.1f}{' ' + unit if unit else ''} ({hn} {c['home']:.1f}, {an} {c['away']:.1f})"
        if c.get("skill"):
            side = "над" if c["pick"] == "O" else "под"
            chance = c["over"] if c["pick"] == "O" else 1 - c["over"]
            lines.append(f"{head} - {side} {c['line']} с {pct(chance)}.{ref_txt}")
        else:
            # протоколът (research/extras_backtest.py): без доказано умение - без процент на робота
            base = f" В лигата над {c['line']} има в {pct(c['base'])} от мачовете (последните 2 години)." if c.get("base") is not None else ""
            lines.append(f"{head}.{base}{ref_txt}")
    h2h = a.get("h2h") or []
    if h2h:
        winners = [m["h"] if m["s"][0] > m["s"][1] else (m["a"] if m["s"][1] > m["s"][0] else None) for m in h2h]
        hw = sum(1 for w in winners if w == home)
        dr = sum(1 for w in winners if w is None)
        last = h2h[0]
        lines.append(f"Директни срещи (последни {len(h2h)}): {hw} победи за {hn}, {dr} равни, {len(h2h) - hw - dr} победи за {an}; "
                     f"последната - {last['h']} – {last['a']} {last['s'][0]}:{last['s'][1]} ({last['d'][:4]}).")
    if flags.get("derby"):
        lines.append("Дерби - професионалистът: избягвай, формата тук значи малко. Роботът не дава съвет.")
    if flags.get("after_break"):
        lines.append("Първи кръг след паузата - професионалистът очаква повече изненади (данните досега не го потвърждават).")
    return lines
