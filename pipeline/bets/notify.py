"""
Известия на телефона, 24/7 - пращат се от облака, лаптопът не трябва да е включен.

Каналът е ntfy.sh: безплатен, без регистрация. На телефона се инсталира приложението
ntfy и се абонира за темата в NTFY_TOPIC. Темата е дълъг случаен низ - който не я знае,
не може да чете известията. Пращат се само имена на мачове, проценти и коефициенти.

Какво се праща: веднъж за всеки мач, между 45 и 75 минути преди началото (облакът върви на
всеки час, значи всеки мач попада в прозореца точно веднъж). Съдържанието е фактите -
модел, пазар, най-добра цена и къде, степента на намерен залог. НЕ съдържа "залагай на":
това е информация за решение, което взимаш ти.

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
        if m.get("model"):
            lines.append("модел " + " / ".join(pct(p) for p in m["model"]))
        if m.get("market"):
            lines.append("пазар " + " / ".join(pct(p) for p in m["market"]))
        for o in m.get("books", []):
            if o["prices"]:
                best = o["prices"][0]
                lines.append(f"{o['selection']}: {best['odds']:.2f} ({best['name']})")
        from .value import tier
        tiers = sorted({tier(b["sharp_book"], b["edge"], b.get("n_books"))
                        for b in m.get("bets", [])})
        if tiers:
            lines.append("цена над честната: степен " + ", ".join(tiers))

        title = f"{m['home']} - {m['away']} след {int(left.total_seconds() // 60)} мин"
        if send(title, "\n".join(lines)):
            sent += 1
        conn.execute("INSERT OR REPLACE INTO alerts_sent (event_id, sent_at) VALUES (?, ?)",
                     (m["event_id"], now.isoformat(timespec="seconds")))
    conn.commit()
    if sent:
        log.info("Пратени известия преди мач: %d", sent)
    return sent
