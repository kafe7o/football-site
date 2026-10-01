"""
Известия на телефона (ntfy.sh), от облака - лаптопът не трябва да е включен.

Три вида, всяко веднъж:
  сутрин (първото пускане след 07:00)   прогнозите за деня: колко мача, най-вероятните съвети,
                                        дербитата и първият кръг след пауза; и как мина вчера
  вечер (първото пускане след 23:00)    резултатите от деня: колко са познати, по пазари, в пари
  понеделник сутрин                     седмицата по лиги: къде роботът познава най-много и най-малко

Без NTFY_TOPIC нищо не се праща - само се записва в лога какво би се пратило.
Пращат се само имена на мачове, проценти и коефициенти. Без размер на залога и без „сигурно“.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, db, robot
from .leagues import LEAGUES

log = logging.getLogger(__name__)

NTFY_URL = "https://ntfy.sh"
SOFIA = ZoneInfo("Europe/Sofia")
STAKE = 10          # евро на прогноза - само за превод на дохода в пари
TOP = 8


def send(title, message, tags="soccer", priority=3):
    """True, ако е пратено. Грешката се лога - известието не бива да вали цикъла."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        log.info("Известие (няма NTFY_TOPIC, не е пратено): %s | %s", title, message)
        return False
    body = json.dumps({"topic": topic, "title": title, "message": message, "tags": [tags],
                       "priority": priority, "click": config.SITE_URL}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(NTFY_URL, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError) as e:
        log.error("Известието не тръгна: %s", e)
        return False


def once(conn, key, fn):
    """Пуска fn само веднъж за ключа (напр. сутрин:2026-10-10)."""
    if db.get_meta(conn, f"notified:{key}"):
        return False
    sent = fn()
    db.set_meta(conn, f"notified:{key}", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return sent


def local_time(iso):
    return datetime.fromisoformat(iso).astimezone(SOFIA).strftime("%H:%M")


def name(h, a):
    return f"{h} - {a}"


def settled_between(conn, start, end):
    out = []
    for t in conn.execute("""SELECT t.*, f.home_src, f.away_src, s.hy + s.ay AS cards, s.hc + s.ac AS corners,
                                     s.hc AS hc, s.ac AS ac
                               FROM tips t LEFT JOIN fixtures f ON f.id = t.fixture_id
                               LEFT JOIN match_stats s ON s.match_id = t.match_id
                              WHERE t.hg IS NOT NULL AND t.kickoff >= ? AND t.kickoff < ?""",
                          (start.isoformat(), end.isoformat())):
        out.append(t)
    return out


def score(rows):
    """Рисковата и по-сигурната прогноза: познати, общо, доход в евро при STAKE (където има коефициент);
    и по пазари. Картоните/корнерите се броят, когато football-data донесе статистиката."""
    rows = [t for t in rows if t["basis"] == "model"]       # само прогнозите на робота (собственикът, 02.10)
    out = {}
    done = []
    for t in rows:
        x = json.loads(t["picks_json"]).get("one")
        if x:
            h = robot.hit_any(x["sel"], t["hg"], t["ag"], t["cards"], t["corners"], (t["hc"], t["ac"]))
            if h is not None:
                done.append((h, x.get("odds") if x.get("src") == "book" else None))
    out["one"] = {"n": len(done), "hits": sum(h for h, _ in done),
                  "money": sum(((o - 1) if h else -1) * STAKE for h, o in done if o), "n_money": sum(1 for _, o in done if o)}
    for kind in ("risky", "safer"):
        done = []
        for t in rows:
            x = json.loads(t["picks_json"]).get(kind)
            if not x:
                continue
            h = robot.hit_any(x["sel"], t["hg"], t["ag"], t["cards"], t["corners"], (t["hc"], t["ac"]))
            if h is not None:
                done.append((h, x.get("odds"), x["sel"]))
        out[kind] = {"n": len(done), "hits": sum(h for h, _, _ in done),
                     "money": sum(((o - 1) if h else -1) * STAKE for h, o, _ in done if o),
                     "x": [h for h, _, s in done if s == "X"]}
    tip_rows = [t for t in rows if t["tip"]
                and robot.rule_of(json.loads(t["flags_json"] or "{}"), t["locked_at"]) in robot.OLD_RULES]
    hits = sum(robot.hit(t["tip"], t["hg"], t["ag"]) for t in tip_rows)
    money = sum(((t["tip_odds"] - 1) if robot.hit(t["tip"], t["hg"], t["ag"]) else -1) * STAKE
                for t in tip_rows if t["tip_odds"])
    markets = {}
    for t in rows:
        if t["basis"] != "model":      # без модел изборите са на букмейкъра - не се броят за робота
            continue
        for mkt, sel in json.loads(t["picks_json"])["robot"].items():
            m = markets.setdefault(mkt, [0, 0])
            m[0] += robot.hit(sel, t["hg"], t["ag"])
            m[1] += 1
    return {"tips": len(tip_rows), "hits": hits, "money": money, "markets": markets, **out}


def score_text(s, label):
    if not s["markets"]:
        return f"{label}: няма уредени мачове."
    parts = []
    one = s.get("one") or {}
    if one.get("n"):
        parts.append(f"{label}: ПРОГНОЗАТА на робота (една за мач) - {one['hits']} от {one['n']} ({one['hits'] / one['n']:.0%})"
                     + (f"; при {STAKE} € (където има коефициент от букмейкър, {one['n_money']}): {one['money']:+.0f} €" if one["n_money"] else "") + ".")
    for kind, name in (("risky", "рисковите"), ("safer", "по-сигурните")):
        k = s.get(kind) or {}
        if k.get("n"):
            x = k["x"]
            parts.append(f"{label}: {name} - {k['hits']} от {k['n']} ({k['hits'] / k['n']:.0%})"
                         + (f", от тях X: {sum(x)} от {len(x)}" if kind == "risky" and x else "")
                         + (f"; при {STAKE} € на прогноза (където има коефициент): {k['money']:+.0f} €" if k["money"] else "") + ".")
    if s["tips"]:
        parts.append(f"{label}: съветите по старото правило - {s['hits']} от {s['tips']} ({s['hits'] / s['tips']:.0%}), "
                     f"при {STAKE} € на съвет: {s['money']:+.0f} €.")
    names = {"1x2": "1/X/2", "dc": "двоен шанс", "ou": "над/под 2.5"}
    parts.append("По пазари: " + ", ".join(f"{names[k]} {v[0]}/{v[1]} ({v[0] / v[1]:.0%})"
                                           for k, v in s["markets"].items() if v[1]))
    return "\n".join(parts)


def morning(conn, upcoming, now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(SOFIA)
    if not 7 <= local.hour < 13:      # след обяд сутрешното известие вече е безсмислено
        return False
    day = local.date().isoformat()

    def build():
        end = (local.replace(hour=7, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)
        today = [m for m in upcoming if datetime.fromisoformat(m["kickoff"]) <= end and m.get("basis")]
        start_y = (local.replace(hour=7, minute=0, second=0, microsecond=0) - timedelta(days=1)).astimezone(timezone.utc)
        yesterday = score(settled_between(conn, start_y, start_y + timedelta(days=1)))
        lines = []
        if today:
            with_pred = [m for m in today if m.get("one") or m.get("risky") or m.get("safer")]
            leagues = {m["league"] for m in today}
            lines.append(f"{len(today)} мача в {len(leagues)} първенства; прогноза на робота за {len(with_pred)} - "
                         f"ПО ЕДНА на мач (най-вероятното по робота с коеф. {robot.SAFE_RANGE[0]:.2f}-{robot.SAFE_RANGE[1]:.2f}). "
                         f"Рисковата (от {robot.RISKY_FROM:.2f}) и по-сигурната - на сайта.")
            top10 = ("E0", "SP1", "I1", "D1", "F1", "BUL", "T1", "N1", "P1", "B1")
            order = sorted(with_pred, key=lambda m: (m["league"] not in top10, m["kickoff"]))
            for m in order[:TOP]:
                x = m.get("one") or m.get("safer") or m.get("risky")
                odd = f" @{x['odds']:.2f}" if x.get("odds") else ""
                lines.append(f"{local_time(m['kickoff'])} {name(m.get('home_src') or m['home'], m.get('away_src') or m['away'])}: "
                             f"{robot.label(x['sel']).replace('домакинът', m.get('home_src') or m['home']).replace('гостът', m.get('away_src') or m['away'])}{odd} ({x['p']:.0%})"
                             + (" - дерби, не за залог" if (m.get("flags") or {}).get("derby") else ""))
            if len(order) > TOP:
                lines.append(f"... и още {len(order) - TOP} - на сайта.")
            from .bonus import TOP5, LEAD
            slots = {}
            for m in today:
                if m["league"] in TOP5:
                    slots.setdefault(m["kickoff"], []).append(m)
            if slots:
                lines.append("Бонус анализ час преди мачовете от топ 5 (известие дали има промени): " + "; ".join(
                    f"{local_time((datetime.fromisoformat(k) - LEAD).isoformat())} за {local_time(k)} ({len(v)} мача)"
                    for k, v in sorted(slots.items())))
            derbies = [m for m in today if (m.get("flags") or {}).get("derby")]
            if derbies:
                lines.append("Дерби - без съвет: " + "; ".join(name(m.get("home_src") or m["home"], m.get("away_src") or m["away"]) for m in derbies))
            breaks = sorted({LEAGUES[m["league"]].title for m in today if (m.get("flags") or {}).get("after_break")})
            if breaks:
                lines.append("Първи кръг след паузата (професионалистът: повече изненади): " + ", ".join(breaks[:8])
                             + (f" и още {len(breaks) - 8}" if len(breaks) > 8 else ""))
        else:
            lines.append("Днес няма мачове с прогноза.")
        if yesterday["markets"]:
            lines.append(score_text(yesterday, "Вчера"))
        return send(f"Прогнози за {local.strftime('%d.%m')}", "\n".join(lines), tags="soccer")

    return once(conn, f"morning:{day}", build)


def evening(conn, now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(SOFIA)
    if local.hour < 23:
        return False
    day = local.date().isoformat()

    def build():
        start = local.replace(hour=7, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        rows = settled_between(conn, start, now)
        if not rows:
            return False
        s = score(rows)
        waiting = conn.execute("SELECT COUNT(*) FROM tips WHERE hg IS NULL AND basis = 'model' AND kickoff >= ? AND kickoff < ?",
                               (start.isoformat(), now.isoformat())).fetchone()[0]
        text = score_text(s, "Днес") + (f"\nЧакат резултат: {waiting}." if waiting else "")
        return send(f"Резултати {local.strftime('%d.%m')}", text, tags="bar_chart")

    return once(conn, f"evening:{day}", build)


def weekly(conn, now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(SOFIA)
    if local.weekday() != 0 or local.hour < 7:
        return False
    week = local.date().isoformat()

    def build():
        rows = settled_between(conn, now - timedelta(days=7), now)
        if not rows:
            return False
        s = score(rows)
        per = {}
        for t in rows:
            sel = json.loads(t["picks_json"])["robot"]["1x2"]
            p = per.setdefault(t["league"], [0, 0])
            p[0] += robot.hit(sel, t["hg"], t["ag"])
            p[1] += 1
        ranked = sorted(((v[0] / v[1], v[1], k) for k, v in per.items() if v[1] >= 5), reverse=True)
        lines = [score_text(s, "Седмицата")]
        if ranked:
            lines.append("1/X/2 по лиги (поне 5 мача), най-точен: " +
                         "; ".join(f"{LEAGUES[k].title} {r:.0%} от {n}" for r, n, k in ranked[:3]))
            lines.append("Най-слаб: " + "; ".join(f"{LEAGUES[k].title} {r:.0%} от {n}" for r, n, k in ranked[-3:]))
        return send("Седмицата на робота", "\n".join(lines), tags="calendar")

    return once(conn, f"weekly:{week}", build)
