"""
Картони и корнери - собствената оценка на робота (указание на професионалиста, 2026-09-30:
„да включва в анализите и под/над на картони и такива неща“).

Същият модел като за головете (bets/model.py: Poisson с регуляризация и затихване 180 дни), но
целта е броят жълти картони (или корнери) на домакина и на госта: всеки отбор има склонност да
получава картони (атака) и да ги предизвиква у противника (защита). Сборът на двата отбора е
очакваният брой за мача.

Картоните варират повече, отколкото позволява Poisson (едно дерби, един строг съдия). Затова
вероятностите за над/под са по отрицателно биномно разпределение: разсейването k се оценява за
всяка лига от мачовете, на които моделът е обучен. Без излишно разсейване - Poisson.

Съдията (само Англия и Шотландия - football-data дава името и за предстоящите мачове): колко
жълти дава средно в лигата за последните 2 години, свито към средното на лигата
(REF_SHRINK мача тегло), като множител.

Коефициенти за картони и корнери нямаме - затова тук няма доход, само колко често роботът
познава (research/extras_backtest.py мери точността назад, преди да се покаже процент).

Данни: само 22-те лиги на football-data имат статистика на мача (bets/results.py: match_stats).
"""

import json
import logging
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

from . import model

log = logging.getLogger(__name__)

KINDS = {"cards": ("hy", "ay", "жълти картона"), "corners": ("hc", "ac", "корнера")}
REF_SHRINK = 10.0
REF_YEARS = 2
MIN_MATCHES = 150


def history(conn, league, kind, since=None, before=None):
    h, a, _ = KINDS[kind]
    rows = conn.execute(
        f"""SELECT m.date, m.home_team, m.away_team, s.{h}, s.{a} FROM matches m JOIN match_stats s ON s.match_id = m.id
             WHERE m.league = ? AND s.{h} IS NOT NULL AND s.{a} IS NOT NULL AND m.date >= ? AND m.date < ?
             ORDER BY m.date""", (league, since or "1900-01-01", before or "9999-12-31")).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["date", "home_team", "away_team", "fthg", "ftag"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def dispersion(fitted, df):
    """Разсейването k на отрицателно биномното за сбора на мача (method of moments).
    None - няма излишно разсейване (Poisson стига)."""
    lam = np.array([sum(fitted.lambdas(h, a) or (np.nan, np.nan)) for h, a in zip(df["home_team"], df["away_team"])])
    y = (df["fthg"] + df["ftag"]).to_numpy(dtype=float)
    ok = np.isfinite(lam)
    lam, y = lam[ok], y[ok]
    if len(lam) < 50:
        return None
    w = fitted.weights(df["date"][ok], fitted.as_of) if fitted.as_of is not None else np.ones(len(lam))
    excess = np.average((y - lam) ** 2 - lam, weights=w)
    if excess <= 0:
        return None
    return float(np.average(lam ** 2, weights=w) / excess)


def fit(conn, league, kind, as_of, since=None):
    """(обучен модел, k) или (None, None) - твърде малко мачове със статистика."""
    df = history(conn, league, kind, since=since, before=str(as_of)[:10])
    if len(df) < MIN_MATCHES:
        return None, None
    fitted = model.Poisson().fit(df, as_of=pd.Timestamp(as_of))
    return fitted, dispersion(fitted, df)


def referee_factor(conn, league, referee, before, kind="cards"):
    """Множителят за съдията - по последните REF_YEARS години в лигата. 1.0, ако не е известен."""
    if not referee or kind != "cards":
        return 1.0, None, 0
    since = (datetime.fromisoformat(str(before)[:10]) - timedelta(days=365 * REF_YEARS)).date().isoformat()
    row = conn.execute(
        """SELECT AVG(s.hy + s.ay), COUNT(*) FROM matches m JOIN match_stats s ON s.match_id = m.id
            WHERE m.league = ? AND m.date >= ? AND m.date < ? AND s.hy IS NOT NULL""",
        (league, since, str(before)[:10])).fetchone()
    league_avg = row[0]
    ref = conn.execute(
        """SELECT SUM(s.hy + s.ay), COUNT(*) FROM matches m JOIN match_stats s ON s.match_id = m.id
            WHERE m.league = ? AND m.referee = ? AND m.date >= ? AND m.date < ? AND s.hy IS NOT NULL""",
        (league, referee, since, str(before)[:10])).fetchone()
    if not league_avg or not ref[1]:
        return 1.0, None, 0
    shrunk = (ref[0] + REF_SHRINK * league_avg) / (ref[1] + REF_SHRINK)
    return shrunk / league_avg, ref[0] / ref[1], ref[1]


def line_for(lam):
    """Основната линия: половинката, най-близка до очаквания брой (3.9 -> 3.5, 4.2 -> 4.5)."""
    return math.floor(lam) + 0.5


def p_over(lam, line, k=None):
    n = math.floor(line)
    if k:
        return float(1 - nbinom.cdf(n, k, k / (k + lam)))
    return float(1 - poisson.cdf(n, lam))


def predict(fitted, k, home, away, factor=1.0):
    """Очакванията за мача и над/под за основната линия и съседните. None - непознат отбор."""
    lam = fitted.lambdas(home, away)
    if lam is None or min(fitted.seen(home), fitted.seen(away)) < model.MIN_EFFECTIVE_MATCHES:
        return None
    lh, la = lam[0] * factor, lam[1] * factor
    total = lh + la
    line = line_for(total)
    lines = {f"{x:.1f}": round(p_over(total, x, k), 4) for x in (line - 1, line, line + 1) if x > 0}
    p = p_over(total, line, k)
    return {"home": round(lh, 2), "away": round(la, 2), "total": round(total, 2), "line": line,
            "over": round(p, 4), "pick": "O" if p >= 0.5 else "U", "lines": lines,
            "k": round(k, 1) if k else None}


def base_rate(conn, league, kind, line, before, years=2):
    """Колко често в лигата броят е над линията - последните години преди мача."""
    h, a, _ = KINDS[kind]
    since = (datetime.fromisoformat(str(before)[:10]) - timedelta(days=365 * years)).date().isoformat()
    row = conn.execute(
        f"""SELECT AVG(CASE WHEN s.{h} + s.{a} > ? THEN 1.0 ELSE 0.0 END), COUNT(*) FROM matches m
              JOIN match_stats s ON s.match_id = m.id
             WHERE m.league = ? AND m.date >= ? AND m.date < ? AND s.{h} IS NOT NULL""",
        (line, league, since, str(before)[:10])).fetchone()
    return (round(row[0], 4), row[1]) if row and row[1] else (None, 0)


_SKILL = None


def skill(kind):
    """Доказано ли е умението (research/extras_backtest.py: Brier под базата и в двата периода)."""
    global _SKILL
    if _SKILL is None:
        from . import config
        path = config.DATA_DIR / "extras_backtest.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        _SKILL = {k: bool(v.get("skill")) for k, v in (data.get("kinds") or {}).items()}
    return _SKILL.get(kind, False)


def hit(pick, line, total):
    return total > line if pick == "O" else total < line


# ---------- кешът за облака: веднъж на ден за лига ----------

def fitted_cached(conn, league, kind, now=None, since=None):
    """(модел, k) за днес - от model_cache (ключ "<лига>|<вид>") или се обучава сега."""
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    key = f"{league}|{kind}"
    row = conn.execute("SELECT day, params_json FROM model_cache WHERE league = ?", (key,)).fetchone()
    if row and row["day"] == today:
        data = json.loads(row["params_json"])
        if not data:
            return None, None
        return model.Poisson.from_export(data["model"]), data.get("k")
    fitted, k = fit(conn, league, kind, today, since=since)
    payload = {"model": fitted.export(), "k": k} if fitted else {}
    conn.execute("INSERT INTO model_cache (league, day, params_json) VALUES (?, ?, ?) "
                 "ON CONFLICT(league) DO UPDATE SET day = excluded.day, params_json = excluded.params_json",
                 (key, today, json.dumps(payload)))
    conn.commit()
    return fitted, k
