"""
Известия на телефона, 24/7 - пращат се от облака, лаптопът не трябва да е включен.

Каналът е ntfy.sh: безплатен, без регистрация. На телефона се инсталира приложението
ntfy и се абонира за темата в NTFY_TOPIC. Темата е дълъг случаен низ - който не я знае,
не може да чете известията. Пращат се само имена на мачове, проценти и коефициенти.

Какво се праща: веднъж за всеки мач, между 45 и 75 минути преди началото (облакът върви на
всеки час, значи всеки мач попада в прозореца точно веднъж). Съдържанието е фактите -
изборът (най-добрата цена над честната, степен A или B), модел, пазар, най-добрите цени;
изборът е по цена (виж value.pick_for_match), без размер на залога.

Без NTFY_TOPIC нищо не се праща - само се записва в лога какво би се пратило.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

NTFY_URL = "https://ntfy.sh"
WINDOW = (timedelta(minutes=45), timedelta(minutes=75))

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


def prematch(conn, rows, now=None):
    """Известие за всеки мач, който започва след около час. Всеки мач - веднъж."""
    conn.execute(SCHEMA)
    now = now or datetime.now(timezone.utc)
    sent = 0
    for m in rows:
        if not m.get("event_id") or not m.get("commence_iso"):
            continue
        start = datetime.fromisoformat(m["commence_iso"].replace("Z", "+00:00"))
        left = start - now
        if not (WINDOW[0] <= left <= WINDOW[1]):
            continue
        if conn.execute("SELECT 1 FROM alerts_sent WHERE event_id = ?", (m["event_id"],)).fetchone():
            continue

        lines = []
        pick = m.get("pick")
        if pick:
            lines.append(f"Избор: {pick['selection']} @ {pick['odds']:.2f} ({pick.get('book_name', pick['bookmaker'])})"
                         f" - степен {pick['tier']}, +{pick['edge'] * 100:.1f}% над честната")
        else:
            lines.append("Без избор - никоя цена не е над честната")
        if m.get("model"):
            lines.append("модел " + " / ".join(pct(p) for p in m["model"]))
        if m.get("market"):
            lines.append("пазар " + " / ".join(pct(p) for p in m["market"]))
        for o in m.get("books", []):
            if o["prices"]:
                best = o["prices"][0]
                lines.append(f"{o['selection']}: {best['odds']:.2f} ({best['name']})")

        title = f"{m['home']} - {m['away']} след {int(left.total_seconds() // 60)} мин"
        if send(title, "\n".join(lines)):
            sent += 1
        conn.execute("INSERT OR REPLACE INTO alerts_sent (event_id, sent_at) VALUES (?, ?)",
                     (m["event_id"], now.isoformat(timespec="seconds")))
    conn.commit()
    if sent:
        log.info("Пратени известия преди мач: %d", sent)
    return sent


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
