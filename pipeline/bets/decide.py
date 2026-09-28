"""
Решението за залог - веднъж, около час преди мача (2026-09-28; собственикът: „да няма ами ако“).

До решението групата и изборът са ПРЕДВАРИТЕЛНИ - менят се с цените. Първото пускане на
облака в последните WINDOW минути преди мача записва окончателно: залог или не, на какво, при
каква цена и защо. После нищо не го мени; известието час преди мача казва точно него. Облакът
върви на ~51 минути, затова прозорецът е 80 - всеки мач попада в него поне веднъж (старият
прозорец 45-75 мин изпускаше ~4 от 10 мача).

Правилото (legacy/rule_backtest.py - 735 изиграни мача с истинските цени отпреди мача):
  - залог = изборът по цена от група A (цена над честната по Pinnacle, 2-8%, 10+ букмейкъра);
  - никога в дерби (bets/derbies.py);
  - никога в сегмент, който самопроверката е изключила (bets/review.py);
  - най-много DAY_MAX залога на ден - по реда на мачовете;
  - при твоя букмейкър (efbet, winbet) - само ако дава поне min_odds: честната цена + 2%.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import review

log = logging.getLogger(__name__)

SOFIA = ZoneInfo("Europe/Sofia")
WINDOW = timedelta(minutes=80)
DAY_MAX = 8            # същото като в site_template.html
# Съвет на професионалния залагач, проверен (legacy/pro_checks.py): в Холандия фаворитът на
# 1.30-1.55 печели 63.9% при обещани 68.4% - и през 2012-2019, и през 2019-2026. Там такъв залог
# не се прави. Същото е в site_template.html (SHORT_FAV_SKIP).
SHORT_FAV_SKIP = {"soccer_netherlands_eredivisie": (1.30, 1.55)}
MIN_OWN_EDGE = 0.02    # колко над честната трябва да плаща твоят букмейкър

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    event_id      TEXT PRIMARY KEY,
    sport         TEXT NOT NULL,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    commence_time TEXT NOT NULL,
    day           TEXT NOT NULL,       -- денят по българско време (за лимита)
    decided_at    TEXT NOT NULL,
    type          TEXT,                -- групата в момента на решението
    bet           INTEGER NOT NULL,    -- 1 залог, 0 без залог
    reason        TEXT NOT NULL,
    pick_json     TEXT,                -- изборът в момента на решението
    min_odds      REAL,                -- най-ниската цена, при която си струва при твоя букмейкър
    derby         INTEGER NOT NULL DEFAULT 0
);
"""


def min_odds(pick):
    return round((1 + MIN_OWN_EDGE) / pick["p_fair"], 2) if pick and pick.get("p_fair") else None


def verdict(m, taken_today, excluded=()):
    """(залог ли е, защо) по правилото. taken_today - вече решените залози за деня."""
    pick = m.get("pick")
    if m.get("derby"):
        return False, "дерби - там не се залага"
    if not pick:
        return False, {"C": "група C - почти винаги замръзнала цена",
                       "-": "без група - никоя цена не е над честната"}.get(m.get("type"), "няма данни за цените")
    if pick.get("tier") != "A":
        return False, f"група {pick.get('tier')} - залага се само група A"
    band = SHORT_FAV_SKIP.get(m.get("sport", ""))
    if band and band[0] <= pick["odds"] <= band[1]:
        return False, "Холандия: фаворитите на 1.30-1.55 печелят по-рядко от обещаното"
    hit = [s for s in review.segments(m) if s in excluded]
    if hit:
        return False, f"самопроверката изключи: {', '.join(review.label(s) for s in hit)}"
    if taken_today >= DAY_MAX:
        return False, f"вече има {DAY_MAX} залога за деня"
    return True, "група A"


def decide(conn, rows, now=None):
    """Записва решението за мачовете от прегледа (site.preview + attach_bets), които започват в
    следващите WINDOW минути и още нямат решение. Връща новите решения."""
    conn.executescript(SCHEMA)
    now = now or datetime.now(timezone.utc)
    excluded = review.excluded(conn)
    stamp = now.isoformat(timespec="seconds")
    new = []
    for m in sorted((m for m in rows if m.get("event_id") and m.get("commence_iso")),
                    key=lambda m: (m["commence_iso"], m["home"])):
        start = datetime.fromisoformat(m["commence_iso"].replace("Z", "+00:00"))
        if not now < start <= now + WINDOW:
            continue
        if conn.execute("SELECT 1 FROM decisions WHERE event_id = ?", (m["event_id"],)).fetchone():
            continue
        day = start.astimezone(SOFIA).date().isoformat()
        taken = conn.execute("SELECT COUNT(*) FROM decisions WHERE day = ? AND bet = 1", (day,)).fetchone()[0]
        bet, reason = verdict(m, taken, excluded)
        pick = m.get("pick")
        conn.execute(
            """INSERT INTO decisions (event_id, sport, home_team, away_team, commence_time, day,
                   decided_at, type, bet, reason, pick_json, min_odds, derby)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (m["event_id"], m.get("sport", ""), m["home"], m["away"], m["commence_iso"], day, stamp,
             m.get("type"), int(bet), reason, json.dumps(pick) if pick else None,
             min_odds(pick) if bet else None, int(bool(m.get("derby")))))
        new.append(m["event_id"])
    conn.commit()
    if new:
        log.info("Решения за залог: %d нови", len(new))
    return new


def load(conn):
    """{event_id: решението} - за сайта и известията."""
    conn.executescript(SCHEMA)
    out = {}
    for r in conn.execute("SELECT * FROM decisions"):
        d = dict(r)
        d["pick"] = json.loads(d.pop("pick_json")) if d.get("pick_json") else None
        d["time"] = datetime.fromisoformat(d["decided_at"]).astimezone(SOFIA).strftime("%H:%M")
        out[r["event_id"]] = d
    return out
