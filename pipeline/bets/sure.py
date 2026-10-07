"""
Най-сигурните мачове (професионалистът, 2026-10-08, предадено от собственика): „да му намалим обхвата от мачове и да го
помолим да поддържа по-висока успеваемост на мачовете, които ни дава. Може да не ни дава всичките 11 мача от Испания
втора лига, да ни даде 4, ама тия 4 да са за него най-сигурните.“

Правилото (протоколът и числата: research/sure_matches_backtest.py): от мачовете на едно първенство за един „ден“ (07:00-07:00
българско време, както записът) кандидати са тези със собствена оценка на робота, без дерби и с ЕДНАТА му прогноза.
„Най-сигурни“ са най-вероятната ТРЕТИНА от тях по шанса на робота за едната прогноза (закръглено нагоре: 11 -> 4) и то само
с шанс поне 65%. Подреждането е само по шанса на робота - не по коефициентите (решение на собственика от 30.09).

Тук е САМО изборът (чисти функции) и белегът върху прогнозите. Записът е в tips.mark_sure (flags_json.sure), показването - в
bets/site.py и site_template.html, независимата проверка - в bets/rules.py, колонките вземат мачове само оттук.
"""

import json
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config

SOFIA = ZoneInfo("Europe/Sofia")
SHARE = 1 / 3
ROUNDING = "ceil"
MIN_P = 0.65
FROM = "2026-10-08T04:00:00+00:00"            # първият запис (07:00 българско време), от който мачовете имат белег


def day_of(kickoff_iso):
    """„Денят“ на прогнозите започва в 07:00 българско време - като в bets/tips.py и bets/columns.py."""
    local = datetime.fromisoformat(kickoff_iso).astimezone(SOFIA) - timedelta(hours=7)
    return local.date().isoformat()


def day_bounds(day):
    start = datetime.fromisoformat(day).replace(hour=7, tzinfo=SOFIA)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def quota(n, share, rounding):
    """Колко от n мача са „най-сигурни“: дял от тях, закръглен нагоре („ceil“) или най-близо, поне 1 („near“)."""
    if n <= 0:
        return 0
    k = math.ceil(n * share - 1e-9) if rounding == "ceil" else max(1, int(n * share + 0.5))
    return min(k, n)


def pick(items, share, rounding, min_p):
    """items = [(ключ, шанс по робота за едната му прогноза)] на едно първенство за един ден -> множество от избраните.
    Най-вероятните k = quota(n); от тях само с шанс ≥ min_p. Равните - по ключа (стабилно)."""
    ranked = sorted(items, key=lambda x: (-x[1], str(x[0])))
    return {key for key, p in ranked[:quota(len(ranked), share, rounding)] if p >= min_p}


def choose(items):
    """Правилото на сайта: множеството от най-сигурните."""
    return pick(items, SHARE, ROUNDING, MIN_P)


def info_for(items):
    """{ключ: {y: най-сигурен ли е, r: място по шанс, n: кандидати, k: колко са най-сигурни}} за едно първенство и ден."""
    chosen = choose(items)
    ranked = sorted(items, key=lambda x: (-x[1], str(x[0])))
    n, k = len(ranked), quota(len(ranked), SHARE, ROUNDING)
    return {key: {"y": key in chosen, "r": i, "n": n, "k": k} for i, (key, _) in enumerate(ranked, 1)}


def measured():
    """Числата от проверката назад (data/sure_backtest.json) за правилото на сайта; None, ако ги няма или са за друго правило."""
    path = config.DATA_DIR / "sure_backtest.json"
    if not path.exists():
        return None
    sb = json.loads(path.read_text(encoding="utf-8"))
    name = "share|{:.3f}|{}|{:.3f}".format(SHARE, ROUNDING, MIN_P)
    if sb.get("live_rule") != name or name not in (sb.get("rules") or {}):
        return None
    return {"name": name, "generated": sb.get("generated"), "select_end": sb.get("select_end"), **sb["rules"][name]}


def candidate(flags, basis, one):
    """Мач, който може да е „най-сигурен“: със собствена оценка на робота, не е дерби, има едната прогноза."""
    from . import rules
    return bool(one) and not rules.match_block(flags, basis)


def late_cut(stored):
    """Най-малкият шанс сред вече отбелязаните за най-сигурни в групата (за мач, който се появява по-късно) или None."""
    ps = [m["one"]["p"] for m in stored if m["flags"]["sure"].get("y")]
    return min(ps) if ps else None


def late_info(p, cut):
    """Мач, добавен в разписанието СЛЕД като групата е отбелязана: най-сигурен само ако е поне толкова вероятен, колкото
    най-слабият от вече отбелязаните - никой отбелязан не се сваля."""
    return {"y": bool(cut is not None and p >= cut), "late": True}


def annotate(entries):
    """Белегът върху предстоящите мачове (за сайта и колонките): flags['sure'] на всеки кандидат. Вече записаният белег
    (tips.mark_sure) се пази; останалите се смятат наново на всяко обновяване (предварително). entries - редовете на
    tips.preview: {id, league, kickoff, basis, flags, one}."""
    groups = {}
    for m in entries:
        if m.get("flags") is not None and candidate(m["flags"], m.get("basis"), m.get("one")):
            groups.setdefault((m["league"], day_of(m["kickoff"])), []).append(m)
    for ms in groups.values():
        stored = [m for m in ms if m["flags"].get("sure")]
        fresh = [m for m in ms if not m["flags"].get("sure")]
        if not fresh:
            continue
        if not stored:
            info = info_for([(m["id"], m["one"]["p"]) for m in ms])
            for m in ms:
                m["flags"]["sure"] = info[m["id"]]
        else:
            cut = late_cut(stored)
            for m in fresh:
                m["flags"]["sure"] = late_info(m["one"]["p"], cut)
    return entries
