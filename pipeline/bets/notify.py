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
    for t in conn.execute("""SELECT t.*, f.home_src, f.away_src FROM tips t LEFT JOIN fixtures f ON f.id = t.fixture_id
                              WHERE t.hg IS NOT NULL AND t.kickoff >= ? AND t.kickoff < ?""",
                          (start.isoformat(), end.isoformat())):
        out.append(t)
    return out


def score(rows):
    """Главният съвет: познати, общо, доход в евро при STAKE на съвет; и по пазари."""
    tip_rows = [t for t in rows if t["tip"]]
    hits = sum(robot.hit(t["tip"], t["hg"], t["ag"]) for t in tip_rows)
    money = sum(((t["tip_odds"] - 1) if robot.hit(t["tip"], t["hg"], t["ag"]) else -1) * STAKE
                for t in tip_rows if t["tip_odds"])
    markets = {}
    for t in rows:
        for mkt, sel in json.loads(t["picks_json"])["robot"].items():
            m = markets.setdefault(mkt, [0, 0])
            m[0] += robot.hit(sel, t["hg"], t["ag"])
            m[1] += 1
    return {"tips": len(tip_rows), "hits": hits, "money": money, "markets": markets}


def score_text(s, label):
    if not s["markets"]:
        return f"{label}: няма уредени мачове."
    parts = []
    if s["tips"]:
        parts.append(f"{label}: съветите - {s['hits']} от {s['tips']} ({s['hits'] / s['tips']:.0%}), "
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
            chance = lambda m: m["probs"]["robot"][m["tip"]]      # шансът на робота, не на коефициентите
            with_tip = sorted((m for m in today if m.get("tip")), key=lambda m: -chance(m))
            leagues = {m["league"] for m in today}
            lines.append(f"{len(today)} мача в {len(leagues)} първенства, съвет с цена от 1.40 нагоре: {len(with_tip)}.")
            if with_tip:
                lines.append("Най-вероятните:")
                for m in with_tip[:TOP]:
                    p = chance(m)
                    lines.append(f"{local_time(m['kickoff'])} {name(m.get('home_src') or m['home'], m.get('away_src') or m['away'])}: "
                                 f"{robot.LABEL[m['tip']]} @ {m['tip_odds']:.2f} ({p:.0%})")
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
        waiting = conn.execute("SELECT COUNT(*) FROM tips WHERE hg IS NULL AND kickoff >= ? AND kickoff < ?",
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
