"""
Известия на телефона, 24/7 - пращат се от облака, лаптопът не трябва да е включен.

Каналът е ntfy.sh: безплатен, без регистрация. На телефона се инсталира приложението
ntfy и се абонира за темата в NTFY_TOPIC. Темата е дълъг случаен низ - който не я знае,
не може да чете известията. Пращат се само имена на мачове, проценти и коефициенти.

Какво се праща: веднъж за всеки мач, между 45 и 75 минути преди началото (облакът върви на
всеки час, значи всеки мач попада в прозореца точно веднъж). Съдържанието е фактите -
изборът (най-добрата цена над честната, степен A или B), модел, пазар, най-добрите цени;
изборът е по цена (виж value.pick_for_match), без размер на залога.

Мачовете с еднакъв начален час идват в ЕДНО известие: скенерът следи 18 лиги и в събота
в 15:00 започват десетки мачове - известие за всеки би било шум.

Смяна на типа A/B/C (type_changes): когато мач мине от B в A, от A в C или изгуби цената си
над честната. Не повече от веднъж на 2 часа за мач, защото цените мигат.

Без NTFY_TOPIC нищо не се праща - само се записва в лога какво би се пратило.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

NTFY_URL = "https://ntfy.sh"
WINDOW = (timedelta(minutes=45), timedelta(minutes=75))
TYPE_QUIET = timedelta(hours=2)     # най-много едно известие за смяна на групата на мач за толкова
NOTICE_AHEAD = timedelta(hours=48)  # за смяна на групата - само в последните 48 часа
TYPE_NAMES = {"A": "A", "B": "B", "C": "C", "-": "без група"}
SOFIA = ZoneInfo("Europe/Sofia")

SCHEMA = """CREATE TABLE IF NOT EXISTS alerts_sent (
    event_id TEXT PRIMARY KEY,
    sent_at  TEXT NOT NULL
)"""


def topic():
    return os.environ.get("NTFY_TOPIC")


def send(title, message, tags="soccer"):
    """True, ако е пратено. Грешката се лога - известието не бива да вали цикъла."""
    name = topic()
    if not name:
        log.info("Известие (няма NTFY_TOPIC, не е пратено): %s | %s", title, message)
        return False
    # JSON вместо заглавки: заглавките на HTTP не носят кирилица, JSON носи.
    body = json.dumps({"topic": name, "title": title, "message": message,
                       "tags": [tags]}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(NTFY_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError) as e:
        log.error("Известието не тръгна: %s", e)
        return False


def pct(x):
    return "–" if x is None else f"{x:.0%}"


def name(selection):
    """odds API пише равния като "Draw"."""
    return "равен" if selection == "Draw" else selection


def pick_text(pick):
    if not pick:
        return "без избор"
    return (f"{name(pick['selection'])} @ {pick['odds']:.2f} ({pick.get('book_name', pick['bookmaker'])})"
            f" - група {pick['tier']}, +{pick['edge'] * 100:.1f}% над честната, шанс {pick['p_fair']:.0%}")


def decision_text(m):
    """Решението час преди мача - окончателно."""
    d = m["decision"]
    pick = d.get("pick")
    if d["bet"]:
        head = f"ЗАЛОГ: {pick_text(pick)}"
        if d.get("min_odds"):
            head += f"; при твоя букмейкър - само ако дава поне {d['min_odds']:.2f}"
    else:
        head = f"без залог - {d['reason']}"
    sig = m.get("signal")
    if not sig:
        opinion = "моделът: няма сигнал"
    elif pick and pick.get("outcome_idx") == sig.get("pick"):
        opinion = f"моделът е съгласен ({name(sig['name'])})"
    else:
        opinion = f"моделът сочи друго: {name(sig['name'])} @ {sig['odds']:.2f}"
    return f"{head}; {opinion}"


def prematch(conn, rows, now=None):
    """Известие с РЕШЕНИЕТО за мачовете, за които току-що е взето (bets/decide.py - веднъж,
    в последните 80 минути преди мача). Всеки мач - веднъж; мачовете с еднакъв начален час -
    в едно известие."""
    conn.execute(SCHEMA)
    now = now or datetime.now(timezone.utc)
    groups = {}
    for m in rows:
        if not m.get("decision") or not m.get("commence_iso"):
            continue
        start = datetime.fromisoformat(m["commence_iso"].replace("Z", "+00:00"))
        if start <= now:
            continue
        if conn.execute("SELECT 1 FROM alerts_sent WHERE event_id = ?", (m["event_id"],)).fetchone():
            continue
        groups.setdefault(start, []).append(m)

    sent = 0
    for start, group in sorted(groups.items()):
        minutes = int((start - now).total_seconds() // 60)
        group.sort(key=lambda m: (not m["decision"]["bet"], m["home"]))
        bets = [m for m in group if m["decision"]["bet"]]
        if len(group) == 1:
            m = group[0]
            pick = m["decision"].get("pick")
            title = (f"ЗАЛОГ: {name(pick['selection'])} @ {pick['odds']:.2f} - {m['home']} - {m['away']} след {minutes} мин"
                     if bets else f"{m['home']} - {m['away']} след {minutes} мин - без залог")
            lines = [decision_text(m)]
            if m.get("model"):
                lines.append("модел (домакин / равен / гост) " + " / ".join(pct(p) for p in m["model"]))
            if m.get("market"):
                lines.append("пазар " + " / ".join(pct(p) for p in m["market"]))
            message = "\n".join(lines)
        else:
            clock = start.astimezone(SOFIA).strftime("%H:%M")
            title = (f"{len(group)} мача след {minutes} мин ({clock}) - ЗАЛОГ: {len(bets)}" if bets
                     else f"{len(group)} мача след {minutes} мин ({clock}) - без залог")
            message = "\n".join(f"{m['home']} - {m['away']}: {decision_text(m)}" for m in group)
        if send(title, message):
            sent += 1
        for m in group:
            conn.execute("INSERT OR REPLACE INTO alerts_sent (event_id, sent_at) VALUES (?, ?)",
                         (m["event_id"], now.isoformat(timespec="seconds")))
    conn.commit()
    if sent:
        log.info("Пратени известия преди мач: %d", sent)
    return sent


def type_changes(conn, now=None):
    """Известие, когато типът на мач се смени (B -> A, A -> C, B -> без...).

    Не повече от веднъж на TYPE_QUIET за мач: цените мигат и всяко мигане не е новина.
    Ако типът се е върнал към последния, който знаеш, известие няма. За започнал мач -
    също няма, късно е.
    """
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    pending = {}
    for r in conn.execute("SELECT * FROM type_log WHERE notified_at IS NULL ORDER BY id"):
        pending.setdefault(r["event_id"], []).append(r)
    lines = []
    for event_id, rows in pending.items():
        latest = rows[-1]
        ids = [r["id"] for r in rows]
        start = datetime.fromisoformat(latest["commence_time"].replace("Z", "+00:00"))
        known = conn.execute(
            """SELECT type, notified_at FROM type_log
                WHERE event_id = ? AND notified_at IS NOT NULL
                  AND notified_at NOT IN ('skip', 'late', 'minor', 'decided')
                ORDER BY id DESC LIMIT 1""", (event_id,)).fetchone()
        if start <= now:
            mark(conn, ids, "late")
            continue
        decided = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'decisions'").fetchone() and \
            conn.execute("SELECT 1 FROM decisions WHERE event_id = ?", (event_id,)).fetchone()
        if decided:
            mark(conn, ids, "decided")         # решението е взето - то е окончателното
            continue
        if known is None:
            continue
        if (known["type"] == "A") == (latest["type"] == "A"):
            mark(conn, ids, "minor")           # не влиза и не излиза от група A - без значение
            continue
        if start - now > NOTICE_AHEAD:
            continue                           # по-рано от 48 часа - чака
        if known["type"] == latest["type"]:
            mark(conn, ids, "skip")            # мигна и се върна - нищо ново
            continue
        if known["notified_at"] not in ("baseline", "backfill"):
            last_sent = datetime.fromisoformat(known["notified_at"])
            if now - last_sent < TYPE_QUIET:
                continue                       # чака - при следващия цикъл пак се проверява
        # Смяната трябва да издържи едно сканиране (час), иначе е мигане на цените - освен ако
        # мачът започва до 90 минути и няма време за чакане (2026-09-28: Man City - Ipswich
        # мина A -> без -> A за час и прати две известия).
        recorded = datetime.fromisoformat(latest["recorded_at"])
        if now - recorded < timedelta(minutes=40) and start - now > timedelta(minutes=90):
            continue
        pick = json.loads(latest["pick_json"]) if latest["pick_json"] else None
        when = start.astimezone(SOFIA).strftime("%d.%m %H:%M")
        lines.append((f"{latest['home_team']} - {latest['away_team']} ({when}): "
                      f"{TYPE_NAMES[known['type']]} -> {TYPE_NAMES[latest['type']]}"
                      + (f", избор {pick_text(pick)}" if pick else "")
                      + (" - предварително: ЗАЛОГ (решението е час преди мача)" if latest["type"] == "A"
                         else " - предварително: без залог"),
                      latest, known["type"]))
        mark(conn, ids, stamp)
    conn.commit()
    if not lines:
        return 0
    if len(lines) == 1:
        text, latest, old = lines[0]
        title = (f"Група {TYPE_NAMES[old]} -> {TYPE_NAMES[latest['type']]}: "
                 f"{latest['home_team']} - {latest['away_team']}")
        message = text
    else:
        title = f"Смяна на групата: {len(lines)} мача"
        message = "\n".join(text for text, _, _ in lines[:20])
        if len(lines) > 20:
            message += f"\nи още {len(lines) - 20} - виж сайта"
    send(title, message, tags="arrows_counterclockwise")
    log.info("Известие за смяна на типа: %d мача", len(lines))
    return len(lines)


def mark(conn, ids, value):
    conn.executemany("UPDATE type_log SET notified_at = ? WHERE id = ?", [(value, i) for i in ids])


def forecast_changes(conn, rows, model_tag=None):
    """Едно известие за осезаемите промени в прогнозата на модела (над 2 пп) - само за мачове в
    следващите NOTICE_AHEAD часа и без решение. Всяка промяна - веднъж.

    Смяна на самия модел (model_tag, напр. когато влезе xG) мести всички прогнози наведнъж - това
    не е новина. Тогава промените се записват тихо като нова отправна точка. На 2026-09-28 при
    влизането на xG тръгнаха 42 отделни известия - затова и са събрани в едно."""
    from . import db
    conn.execute(SCHEMA)
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    quiet = False
    if model_tag and db.get_meta(conn, "forecast_model") != model_tag:
        db.set_meta(conn, "forecast_model", model_tag)
        quiet = True
    lines = []
    for m in rows:
        history = m.get("history") or []
        if len(history) < 2 or not m.get("shift"):
            continue
        key = f"change:{m['event_id']}:{history[-1]['recorded_at']}"
        if conn.execute("SELECT 1 FROM alerts_sent WHERE event_id = ?", (key,)).fetchone():
            continue
        conn.execute("INSERT OR REPLACE INTO alerts_sent (event_id, sent_at) VALUES (?, ?)", (key, stamp))
        start = (datetime.fromisoformat(m["commence_iso"].replace("Z", "+00:00"))
                 if m.get("commence_iso") else None)
        if quiet or m.get("decision") or not start or not (now < start <= now + NOTICE_AHEAD):
            continue
        first, last = history[0], history[-1]
        fmt = lambda h, k: " / ".join(pct(h[f"{k}_{x}"]) for x in "hda")
        lines.append(f"{m['home']} - {m['away']} ({start.astimezone(SOFIA):%d.%m %H:%M}): "
                     f"модел {fmt(first, 'p_model')} -> {fmt(last, 'p_model')}")
    conn.commit()
    if quiet:
        log.info("Нов модел (%s) - промените в прогнозите са записани без известие", model_tag)
        return 0
    if not lines:
        return 0
    title = (f"Промяна в прогнозата: {len(lines)} мача" if len(lines) > 1
             else f"Промяна в прогнозата: {lines[0].split(' (')[0]}")
    body = "\n".join(lines[:15]) + (f"\nи още {len(lines) - 15}" if len(lines) > 15 else "")
    send(title, body + "\n(моделът е само мнение - залогът се решава по цена час преди мача)",
         tags="chart_with_upwards_trend")
    log.info("Известие за промени в прогнозите: %d мача", len(lines))
    return len(lines)
