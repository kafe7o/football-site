"""
Известия на телефона (ntfy.sh), от облака - лаптопът не трябва да е включен.

Четири вида, всяко веднъж:
  нощ (първото пускане 04:00-07:00)     ПРЕГЛЕД за предстоящия запис (не е запис) - топ шанс и предварителни колонки; сутринта се сравнява със записа
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

from . import config, db, robot, sure
from .leagues import LEAGUES

log = logging.getLogger(__name__)

NTFY_URL = "https://ntfy.sh"
SOFIA = ZoneInfo("Europe/Sofia")
STAKE = 10          # евро на прогноза - само за превод на дохода в пари
TOP = 8
TOP_SURE = 12       # най-сигурни мачове в сутрешното известие (по шанс, най-вероятните първи)


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
    rows = [t for t in rows if t["basis"] == "model" or t["tip"]]    # само мачовете с прогноза на робота (02.10)
    out = {}
    done, sure_hits, rest_hits = [], [], []
    rows = [t for t in rows if not json.loads(t["flags_json"] or "{}").get("derby_late")]   # дерби, разпознато след записа
    for t in rows:
        x = json.loads(t["picks_json"]).get("one")
        if x:
            h = robot.hit_any(x["sel"], t["hg"], t["ag"], t["cards"], t["corners"], (t["hc"], t["ac"]))
            if h is not None:
                done.append((h, x.get("odds") if x.get("src") == "book" else None))
                s = json.loads(t["flags_json"] or "{}").get("sure")
                if s:                                      # най-сигурните мачове (bets/sure.py) срещу останалите с белег
                    (sure_hits if s["y"] else rest_hits).append(h)
    out["one"] = {"n": len(done), "hits": sum(h for h, _ in done),
                  "money": sum(((o - 1) if h else -1) * STAKE for h, o in done if o), "n_money": sum(1 for _, o in done if o)}
    out["sure"] = {"n": len(sure_hits), "hits": sum(sure_hits), "rest_n": len(rest_hits), "rest_hits": sum(rest_hits)}
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
    su = s.get("sure") or {}
    if su.get("n"):
        parts.append(f"{label}: ТОП ШАНС (най-вероятните мачове) - {su['hits']} от {su['n']} ({su['hits'] / su['n']:.0%})"
                     + (f"; останалите с прогноза - {su['rest_hits']} от {su['rest_n']} ({su['rest_hits'] / su['rest_n']:.0%})"
                        if su["rest_n"] else "") + ".")
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


def night(conn, upcoming, now=None):
    """Нощен ПРЕГЛЕД на предстоящия запис (собственикът, 11.10: „да ми снася информацията за утре в друго време“; „в 4 моето време, около час
    след като излязат резултатите“). Първото пускане между 04:00 и 07:00 българско време, веднъж на ден; от 04:00 моделът на деня (обучава се
    в 00:00 UTC = 03:00 българско) е същият като за записа в 07:00, така че разлика остава само от цените и новите мачове. НЕ Е ЗАПИС: прогнозите и колонките се записват в 07:00 и не се менят;
    прегледът е по същите правила и от данните към момента, нищо не се записва, на сайта колонки напред няма (майсторът: ден за ден).
    Снимката се пази в meta (night_preview:<ден>) - сутринта morning() я сравнява със записа и казва колко мача са се сменили."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(SOFIA)
    if not 4 <= local.hour < 7:
        return False
    target = local.date().isoformat()
    start = local.replace(hour=7, minute=0, second=0, microsecond=0)
    lo, hi = start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)
    day_ms = [m for m in upcoming if lo <= datetime.fromisoformat(m["kickoff"]) < hi and m.get("basis")]
    if not day_ms:
        return False

    def build():
        from . import columns
        with_pred = [m for m in day_ms if m.get("one")]
        sure_ms = [m for m in with_pred if ((m.get("flags") or {}).get("sure") or {}).get("y")]
        cols = columns.for_days([m for m in day_ms if m.get("basis") == "model"], days=1).get(target, [])
        db.set_meta(conn, f"night_preview:{target}", json.dumps({
            "cols": [[{"id": l["id"], "home": l["home"], "away": l["away"], "sel": l["sel"]} for l in c["legs"]] for c in cols],
            "star": [{"id": m["id"], "home": m.get("home_src") or m["home"], "away": m.get("away_src") or m["away"]} for m in sure_ms]},
            ensure_ascii=False))
        lines = ["Това е ПРЕГЛЕД, не запис: записът е в 07:00 и не се променя; тогава ще получиш потвърждение какво се е сменило. "
                 "Моделът е същият като за записа; до 07:00 се движат само коефициентите, затова 0-2 мача от колонките могат да се сменят.",
                 f"{len(day_ms)} мача, прогноза на робота за {len(with_pred)}; топ шанс: {len(sure_ms)}."]
        order = sorted(sure_ms, key=lambda m: -m["one"]["p"])
        for m in order[:TOP_SURE]:
            x = m["one"]
            odd = f" @{x['odds']:.2f}" if x.get("odds") else ""
            lines.append(f"{local_time(m['kickoff'])} {name(m.get('home_src') or m['home'], m.get('away_src') or m['away'])}: "
                         f"{robot.label(x['sel']).replace('домакинът', m.get('home_src') or m['home']).replace('гостът', m.get('away_src') or m['away'])}"
                         f"{odd}{'' if x.get('src') == 'book' else ' (цена по робота)'} ({x['p']:.0%})")
        if len(order) > TOP_SURE:
            lines.append(f"... и още {len(order) - TOP_SURE} с топ шанс - на сайта след записа.")
        if cols:
            lines.append(f"ПРЕДВАРИТЕЛНИ КОЛОНКИ ({len(cols)}; не са записани; назад колонка минава ~29% и връща ~0.8 € от 1 €, не е сигурен залог): " + "; ".join(
                f"{i}) " + " + ".join(f"{local_time(l['kickoff'])} {name(l['home'], l['away'])} {robot.label(l['sel'])} @{l['odds']:.2f}"
                                      + ("" if l.get("src") == "book" else " (цена по робота)") for l in c["legs"])
                + f" [коеф. {c['odds']:.2f}]" for i, c in enumerate(cols, 1)))
            if any(l.get("src") != "book" for c in cols for l in c["legs"]):
                lines.append("„Цена по робота“ е честната цена на робота (1/шанса), не на букмейкъра - провери я при него; под 1.40 не е по правилото на майстора.")
        else:
            lines.append("Предварителни колонки няма - не стигат 3 мача с топ шанс от различни първенства.")
        return send(f"ПРЕГЛЕД за {local.strftime('%d.%m')} (не е запис)", "\n".join(lines), tags="mag", priority=3)

    return once(conn, f"night:{target}", build)


def night_check(snap, todays, sure_ms):
    """Сутрин: записът срещу нощния преглед - колко от мачовете в колонките и колко от топ шанс са същите (текст за известието)."""
    prev_legs = {l["id"]: l for c in snap.get("cols") or [] for l in c}
    cur_legs = {l["id"]: l for c in todays for l in c["legs"]}
    same = len(prev_legs.keys() & cur_legs.keys())
    text = f"СПРЯМО НОЩНИЯ ПРЕГЛЕД: колонки - {same} от {len(cur_legs)} мача са същите" if cur_legs else "СПРЯМО НОЩНИЯ ПРЕГЛЕД: записът няма колонки"
    new = [name(l["home"], l["away"]) for i, l in cur_legs.items() if i not in prev_legs]
    gone = [name(l["home"], l["away"]) for i, l in prev_legs.items() if i not in cur_legs]
    if new:
        text += "; нови: " + ", ".join(new)
    if gone:
        text += "; отпаднали: " + ", ".join(gone)
    prev_star = {s["id"] for s in snap.get("star") or []}
    cur_star = {m.get("id") for m in sure_ms}
    text += f". Топ шанс: {len(prev_star & cur_star)} от {len(cur_star)} са същите" + (f" ({len(prev_star - cur_star)} отпаднали, {len(cur_star - prev_star)} нови)" if prev_star != cur_star else "") + "."
    return text


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
            # професионалистът (08.10): не всички мачове, а най-сигурните - третината с най-голям шанс на първенство (bets/sure.py)
            sure_ms = [m for m in with_pred if ((m.get("flags") or {}).get("sure") or {}).get("y")]
            meas = sure.measured()
            lines.append(f"{len(today)} мача в {len(leagues)} първенства; прогноза на робота за {len(with_pred)}. "
                         f"ТОП ШАНС (най-вероятните мачове): {len(sure_ms)} - третината с най-голям шанс на първенство, поне {sure.MIN_P:.0%}; по една прогноза на мач"
                         + (f". Назад такива излизат {meas['clean']['sure']['hit']:.0%} (роботът казва {meas['clean']['sure']['said']:.0%}), "
                            f"останалите {meas['clean']['rest']['hit']:.0%}" if meas else "") + ". Другите мачове и рисковата - на сайта.")
            order = sorted(sure_ms, key=lambda m: -(m.get("one") or m.get("safer") or m.get("risky"))["p"])
            for m in order[:TOP_SURE]:
                x = m.get("one") or m.get("safer") or m.get("risky")
                odd = f" @{x['odds']:.2f}" if x.get("odds") else ""
                lines.append(f"{local_time(m['kickoff'])} {name(m.get('home_src') or m['home'], m.get('away_src') or m['away'])}: "
                             f"{robot.label(x['sel']).replace('домакинът', m.get('home_src') or m['home']).replace('гостът', m.get('away_src') or m['away'])}{odd} ({x['p']:.0%})"
                             + (" - дерби, не за залог" if (m.get("flags") or {}).get("derby") else ""))
            if len(order) > TOP_SURE:
                lines.append(f"... и още {len(order) - TOP_SURE} с топ шанс - на сайта.")
            from .bonus import TOP5, LEAD
            slots = {}
            for m in today:
                if m["league"] in TOP5:
                    slots.setdefault(m["kickoff"], []).append(m)
            if slots:
                lines.append("Бонус анализ час преди мачовете от топ 5 (известие дали има промени): " + "; ".join(
                    f"{local_time((datetime.fromisoformat(k) - LEAD).isoformat())} за {local_time(k)} ({len(v)} мача)"
                    for k, v in sorted(slots.items())))
            from . import columns
            today_d = columns.day_of(now.isoformat())
            rec = [c for c in columns.record(conn, days=10)["columns"] if not c.get("manual")]
            todays = [c for c in rec if columns.col_day(c) == today_d]
            if todays:
                lines.append(f"КОЛОНКИ ЗА ДНЕС ({len(todays)}, записани сега - ден за ден, не се променят; не са сигурни - назад минават ~29% "
                             f"и връщат ~0.8 € от 1 €): " + "; ".join(
                    f"{c['idx']}) " + " + ".join(f"{name(l['home'], l['away'])} {robot.label(l['sel'])}" for l in c["legs"])
                    + f" [коеф. {c['odds']:.2f}]" for c in todays))
            if not todays:
                lines.append("Колонки за днес няма - не стигнаха 3 мача с топ шанс от различни първенства (ден за ден, по майстора).")
            snap = db.get_meta(conn, f"night_preview:{today_d}")
            if snap:
                lines.append(night_check(json.loads(snap), todays, sure_ms))
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
        # отложените и прекъснатите (tips.mark_status) не чакат резултат - броят се отделно
        flags = [json.loads(r[0] or "{}") for r in conn.execute(
            "SELECT flags_json FROM tips WHERE hg IS NULL AND (basis = 'model' OR tip IS NOT NULL) AND kickoff >= ? AND kickoff < ?",
            (start.isoformat(), now.isoformat()))]
        waiting, off = sum(1 for f in flags if not f.get("postponed")), sum(1 for f in flags if f.get("postponed"))
        text = score_text(s, "Днес") + (f"\nЧакат резултат: {waiting}." if waiting else "") + (f"\nОтложени или прекъснати: {off}." if off else "")
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
