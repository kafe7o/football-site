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
TYPE_QUIET = timedelta(hours=2)     # най-много едно известие за смяна на типа на мач за толкова
TYPE_NAMES = {"A": "A", "B": "B", "C": "C", "-": "без"}
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
            f" - тип {pick['tier']}, +{pick['edge'] * 100:.1f}% над честната, шанс {pick['p_fair']:.0%}")


def rule_text(m):
    """Правилото на собственика (2026-09-28): сигналът на модела се залага само в мач от
    тип A или B. Тип C - никога; без цена над честната - също не."""
    sig = m.get("signal")
    if not sig:
        return "сигнал на модела: няма"
    head = f"сигнал на модела: {name(sig['name'])} @ {sig['odds']:.2f}"
    kind = m.get("type")
    if kind in ("A", "B"):
        return f"{head} - залага се (тип {kind})"
    why = {"C": "тип C", "-": "няма цена над честната"}.get(kind, "няма данни за цените")
    return f"{head} - НЕ се залага ({why})"


def prematch(conn, rows, now=None):
    """Известие за мачовете, които започват след около час. Всеки мач - веднъж; мачовете
    с еднакъв начален час - в едно известие."""
    conn.execute(SCHEMA)
    now = now or datetime.now(timezone.utc)
    groups = {}
    for m in rows:
        if not m.get("event_id") or not m.get("commence_iso"):
            continue
        start = datetime.fromisoformat(m["commence_iso"].replace("Z", "+00:00"))
        left = start - now
        if not (WINDOW[0] <= left <= WINDOW[1]):
            continue
        if conn.execute("SELECT 1 FROM alerts_sent WHERE event_id = ?", (m["event_id"],)).fetchone():
            continue
        groups.setdefault(start, []).append(m)

    sent = 0
    for start, group in sorted(groups.items()):
        minutes = int((start - now).total_seconds() // 60)
        if len(group) == 1:
            title, message = single_prematch(group[0], minutes)
        else:
            # Първо мачовете с избор - те са причината известието да е важно.
            group.sort(key=lambda m: (m.get("pick") is None, m["home"]))
            with_pick = sum(1 for m in group if m.get("pick"))
            title = (f"{len(group)} мача след {minutes} мин ({start.astimezone(SOFIA):%H:%M})"
                     f" - с избор {with_pick}")
            message = "\n".join(f"{m['home']} - {m['away']}: {pick_text(m.get('pick'))}; {rule_text(m)}"
                                for m in group)
        if send(title, message):
            sent += 1
        for m in group:
            conn.execute("INSERT OR REPLACE INTO alerts_sent (event_id, sent_at) VALUES (?, ?)",
                         (m["event_id"], now.isoformat(timespec="seconds")))
    conn.commit()
    if sent:
        log.info("Пратени известия преди мач: %d", sent)
    return sent


def single_prematch(m, minutes):
    """Известието за един мач - изборът, моделът, пазарът и най-добрите цени."""
    lines = []
    pick = m.get("pick")
    lines.append(f"Избор по цена: {pick_text(pick)}" if pick
                 else "Без избор по цена - никоя цена не е над честната")
    lines.append(rule_text(m)[0].upper() + rule_text(m)[1:])
    if m.get("model"):
        lines.append("модел (домакин / равен / гост) " + " / ".join(pct(p) for p in m["model"]))
    if m.get("market"):
        lines.append("пазар " + " / ".join(pct(p) for p in m["market"]))
    for o in m.get("books", []):
        if o["prices"]:
            best = o["prices"][0]
            lines.append(f"{name(o['selection'])}: {best['odds']:.2f} ({best['name']})")
    return f"{m['home']} - {m['away']} след {minutes} мин", "\n".join(lines)


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
                WHERE event_id = ? AND notified_at IS NOT NULL AND notified_at NOT IN ('skip', 'late')
                ORDER BY id DESC LIMIT 1""", (event_id,)).fetchone()
        if start <= now:
            mark(conn, ids, "late")
            continue
        if known is None:
            continue
        if known["type"] == latest["type"]:
            mark(conn, ids, "skip")            # мигна и се върна - нищо ново
            continue
        if known["notified_at"] not in ("baseline", "backfill"):
            last_sent = datetime.fromisoformat(known["notified_at"])
            if now - last_sent < TYPE_QUIET:
                continue                       # чака - при следващия цикъл пак се проверява
        pick = json.loads(latest["pick_json"]) if latest["pick_json"] else None
        when = start.astimezone(SOFIA).strftime("%d.%m %H:%M")
        lines.append((f"{latest['home_team']} - {latest['away_team']} ({when}): "
                      f"{TYPE_NAMES[known['type']]} -> {TYPE_NAMES[latest['type']]}"
                      + (f", избор {pick_text(pick)}" if pick else "")
                      + ("" if latest["type"] in ("A", "B") else " - сигнал на модела тук НЕ се залага"),
                      latest, known["type"]))
        mark(conn, ids, stamp)
    conn.commit()
    if not lines:
        return 0
    if len(lines) == 1:
        text, latest, old = lines[0]
        title = (f"Тип {TYPE_NAMES[old]} -> {TYPE_NAMES[latest['type']]}: "
                 f"{latest['home_team']} - {latest['away_team']}")
        message = text
    else:
        title = f"Смяна на типа: {len(lines)} мача"
        message = "\n".join(text for text, _, _ in lines[:20])
        if len(lines) > 20:
            message += f"\nи още {len(lines) - 20} - виж сайта"
    send(title, message, tags="arrows_counterclockwise")
    log.info("Известие за смяна на типа: %d мача", len(lines))
    return len(lines)


def mark(conn, ids, value):
    conn.executemany("UPDATE type_log SET notified_at = ? WHERE id = ?", [(value, i) for i in ids])


def forecast_changes(conn, rows):
    """Известие, когато прогнозата за мач се премести осезаемо (над 2 пп). Всяка промяна -
    веднъж: ключът е мачът + часът на последния запис в дневника."""
    conn.execute(SCHEMA)
    now = datetime.now(timezone.utc)
    sent = 0
    for m in rows:
        history = m.get("history") or []
        if len(history) < 2 or not m.get("shift"):
            continue
        key = f"change:{m['event_id']}:{history[-1]['recorded_at']}"
        if conn.execute("SELECT 1 FROM alerts_sent WHERE event_id = ?", (key,)).fetchone():
            continue
        first, last = history[0], history[-1]
        fmt = lambda h, k: " / ".join(pct(h[f"{k}_{x}"]) for x in "hda")
        lines = [f"модел: {fmt(first, 'p_model')} -> {fmt(last, 'p_model')}",
                 f"пазар: {fmt(first, 'p_fair')} -> {fmt(last, 'p_fair')}"]
        pick = m.get("pick")
        lines.append(f"Избор сега: {pick['selection']} @ {pick['odds']:.2f} ({pick.get('book_name', pick['bookmaker'])})"
                     if pick else "Избор сега: няма")
        if send(f"Промяна: {m['home']} - {m['away']}", "\n".join(lines),
                tags="chart_with_upwards_trend"):
            sent += 1
        conn.execute("INSERT OR REPLACE INTO alerts_sent (event_id, sent_at) VALUES (?, ?)",
                     (key, now.isoformat(timespec="seconds")))
    conn.commit()
    if sent:
        log.info("Пратени известия за промени: %d", sent)
    return sent
