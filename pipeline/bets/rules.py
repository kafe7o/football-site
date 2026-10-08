"""
Правилата на професионалиста (майстора) - ЕДНО място (2026-10-07, собственикът: „всички правила на майстора да се
следват, да могат да се образуват и да дават самите прогнози, кои прогнози да не се дават и играят и как да стават
колонките - по принцип трябваше да си го направил“).

Дотогава правилата бяха разпръснати (дербитата спираха само колонките, „тото“ важеше само за една от трите прогнози).
Тук е:
  RULES              - списъкът: какво е казал, откъде, КЪДЕ се прилага (едната / по-сигурната / рисковата / колонки /
                       бонус), състояние и доказателството (числата). От него се строи и редът „Правилата“ на сайта;
  match_block()      - един мач блокиран ли е за ВСИЧКИ прогнози (дерби; без собствена оценка) - ползват го tips.forecast,
                       bonus.compare и columns.candidates;
  pick_block()       - една прогноза блокирана ли е (под 1.40; „тото“; по-сигурна над 1.80 или под 50%; рискова не е знак
                       1/2 или е над 50%) - ползва го columns.candidates; одиторът проверява с него всичко записано;
  audit(conn)        - НЕЗАВИСИМ одитор: проверява записаните прогнози и колонки срещу правилата; в облака върви на всяко
                       пускане (стъпка „Проверка на правилата“) и праща известие при нарушение;
  Новото правило влиза тук, в RULES, ПРЕДИ да се пише къде другаде - иначе одиторът не го знае.
"""

import json
from datetime import datetime, timezone

from . import robot, sure

# от кога важи всяко правило за записите (по-старите записи не се одитират - записът не се пипа)
FROM_RANGES = "2026-10-03T04:00:00+00:00"     # граници 1.40 / зелена до 1.80 / рисковата знак под 50%
FROM_ALL_BLOCKS = "2026-10-07T00:00:00+00:00"  # дерби и „тото“ във всяка прогноза
FROM_SURE = sure.FROM                          # белегът „най-сигурен мач“ (bets/sure.py)

# kind: one - прогнозата на робота (рамката), safer - зелената, risky - жълтата, column, bonus
RULES = [
    {"id": "derby", "title": "Дербита - без никаква прогноза", "applies": ["one", "safer", "risky", "column", "bonus"],
     "status": "блокира", "said": "„Вечните дербита - избягвай“ (28.09)",
     "evidence": "1465 дербита (2012-2026): равни 28.3% при обещани 26.0%, фаворитът 48.8% при 50.3%"},
    {"id": "toto", "title": "„Тото“ лиги (Холандия, Австрия): без фаворит с коефициент 1.30-1.55",
     "applies": ["one", "safer", "risky", "column"], "status": "блокира",
     "said": "„Къде 1.40 не излиза - там е тото“ (28.09)",
     "evidence": "Холандия: фаворитите печелят 63.9% при обещани 68.4%; Австрия 63.4% при 68.3% (потвърдено и в двата периода)"},
    {"id": "floor", "title": "Нищо под коефициент 1.40", "applies": ["one", "safer", "risky", "column"], "status": "блокира",
     "said": "„Да не ми дава мачове на 1.20 и да казва „много са сигурни““ (02.10)", "evidence": "граница на майстора"},
    {"id": "own_model", "title": "Шансът е на робота, не на букмейкъра; без модел - няма прогноза",
     "applies": ["one", "safer", "risky", "column"], "status": "блокира",
     "said": "„Да не се влияе от коефициентите, когато казва процента“ (30.09)",
     "evidence": "мачовете без собствена оценка (национални отбори, някои лиги) остават само в „Прогнози“"},
    {"id": "sure", "title": "Топ шанс: от първенство за деня - само най-вероятната третина, шанс 65%+ (колонките - само от тях)",
     "applies": ["one", "column"], "status": "прилага се (измерено)",
     "said": "„Да му намалим обхвата от мачове и просто да го помолим да поддържа по-висока успеваемост на мачовете, които ни дава... "
             "може да не ни дава всичките 11 мача от Испания втора лига, да ни даде 4 мача, ама реално тия 4 да са за него най-сигурните... "
             "освен най-сигурната прогноза за даден мач, да ни дава и най-сигурните мачове“ (08.10)",
     "evidence": "назад третината най-вероятни излиза 66% (чиста проверка) срещу 63% за останалите и 64% за всички; роботът казва 70% - "
                 "завишава; ~14 мача на ден вместо ~33. В парите няма предимство: при истински коефициенти -7% (избор) и -12% (чиста) "
                 "срещу -7% и -5% за останалите - коефициентите им са по-ниски"},
    {"id": "one", "title": "Точно една прогноза на мач, най-вероятното събитие (шанс 50%+)", "applies": ["one"], "status": "прилага се",
     "said": "„Точно една прогноза, за да видим какъв процент държи роботът“ (01.10)",
     "evidence": "назад 63.5-64% излизат; роботът казва 66%"},
    {"id": "safer", "title": "По-сигурна (зелена): коефициент 1.40-1.80 и шанс 50%+", "applies": ["safer"], "status": "блокира",
     "said": "„Сигурната между 1.40 и 1.80“ (30.09, 02.10)", "evidence": "назад 59-60% излизат, в 87% от мачовете"},
    {"id": "risky", "title": "Рискова (жълта): знак 1 или 2 под 50% - никога X; при ясен фаворит няма",
     "applies": ["risky"], "status": "блокира",
     "said": "„Рисковата да не е само X - нека да познава знаци; рискова на 1.50 няма смисъл“ (02.10)",
     "evidence": "назад 42% излизат при среден коефициент 2.3; X 0%"},
    {"id": "column", "title": "Колонки: 3 мача от различни първенства, шанс 65%+, без дерби и „тото“",
     "applies": ["column"], "status": "първа версия", "said": "„Колонките ги правя петък-понеделник; ще ти обясня как“ (04.10)",
     "evidence": "назад минава 29% (роботът казва 36%), връща ~0.8 € от 1 €; майсторът още не е обяснил как ги прави"},
    {"id": "record", "title": "Прогнозата се записва в деня на мача и после не се променя",
     "applies": ["one", "safer", "risky", "column"], "status": "прилага се",
     "said": "честен запис (проектът)", "evidence": "записът е в 07:00; предварителното не се брои"},
    {"id": "bonus", "title": "Бонус анализ час преди мачовете от топ 5 (известие)", "applies": ["bonus"], "status": "прилага се",
     "said": "„Анализ час преди мача и известие дали нещо се е променило“ (01.10)", "evidence": "bonus.yml"},
    {"id": "after_break", "title": "След паузата за националните отбори - повече изненади", "applies": [], "status": "флаг (не е потвърдено)",
     "said": "„След паузата има повече изненади“ (28.09)",
     "evidence": "12 423 мача: фаворитът -0.2% срещу -0.1% - не се потвърждава; отбелязва се и се мери на живо"},
    {"id": "target70", "title": "70% познати за сигурната", "applies": [], "status": "не е постижимо",
     "said": "„Сигурната да познава средно 70%“ (30.09)",
     "evidence": "при коефициент 1.40-1.80 назад излизат ~59%; 70% има само при коефициент около 1.35 и под"},
    {"id": "no_stake", "title": "Без размер на залога и без „сигурен залог“", "applies": ["one", "safer", "risky", "column"],
     "status": "прилага се", "said": "правило на проекта", "evidence": "парите се показват само като мярка „при 10 €“"},
]


def match_block(flags, basis):
    """Причина мачът да няма НИКАКВА прогноза (или None). Важи за едната, по-сигурната, рисковата, колонките, бонуса."""
    if (flags or {}).get("derby"):
        return "дерби - професионалистът: в дербитата не се залага (повече равни от обещаното), прогноза няма"
    if basis != "model":
        return "роботът няма собствена оценка за тези отбори - показан е само пазарът"
    return None


def sure_block(flags):
    """Причина мачът да не е в колонка/„най-сигурен“ (или None): белегът от bets/sure.py (tips.mark_sure) липсва или е „не“."""
    s = (flags or {}).get("sure")
    if not (s and s.get("y")):
        return "няма топ шанс - не е сред най-вероятната третина на първенството за деня (поне 65%)"
    return None


def pick_block(kind, sel, p, odds, league=None):
    """Причина една прогноза да не се дава (или None). kind: one | safer | risky | column."""
    if odds is None or odds < robot.FLOOR:
        return f"коефициент {odds} - под {robot.FLOOR:.2f}"
    if robot.toto_blocked(league, sel, odds):
        return "„тото“ лига: фаворит 1.30-1.55 печели по-рядко от обещаното"
    if kind == "safer":
        if not (robot.in_safe(odds) and p >= robot.MIN_PROB):
            return f"по-сигурната е 1.40-1.80 и шанс 50%+ (тази е {odds} и {p:.0%})"
        if sel not in robot.SAFE_CANDIDATES:
            return "по-сигурната е 1, 2, двоен шанс или над/под 2.5"
    if kind == "risky":
        if sel not in ("1", "2"):
            return "рисковата е знак 1 или 2 (не X)"
        if p >= 0.5:
            return "рисковата е под 50% по робота (фаворитът не е риск)"
    if kind == "column":
        if sel and (sel[0] in "CK" or sel in robot.CORNER_SIDES):
            return "в колонките няма картони и корнери (няма истински коефициенти)"
    return None


def audit(conn, since=None):
    """Одиторът: проверява записаните прогнози и колонки срещу правилата. [(мач, прогноза, нарушено правило)]."""
    since = since or FROM_RANGES
    out = []
    flags_of = {}
    for t in conn.execute("SELECT fixture_id, league, home, away, locked_at, basis, picks_json, flags_json FROM tips "
                          "WHERE locked_at >= ?", (since,)):
        flags = json.loads(t["flags_json"] or "{}")
        flags_of[t["fixture_id"]] = (flags, t["league"])
        if flags.get("derby_late"):
            continue                       # дерби, разпознато след записа: прогнозите са само в записа, не се показват
        picks = json.loads(t["picks_json"])
        name = f"{t['home']} - {t['away']}"
        for kind in ("one", "safer", "risky"):
            x = picks.get(kind)
            if not x:
                continue
            if t["locked_at"] >= FROM_ALL_BLOCKS:
                why = match_block(flags, t["basis"]) or pick_block(kind, x["sel"], x["p"], x.get("odds"), t["league"])
            else:                                  # по-старите записи - само границите от 03.10
                why = None
                o = x.get("odds")
                if o is None or o < robot.FLOOR:
                    why = f"коефициент {o} - под {robot.FLOOR:.2f}"
                elif kind == "risky" and x["sel"] not in ("1", "2"):
                    why = "рисковата е знак 1 или 2 (не X)"
                elif kind == "safer" and not robot.in_safe(o):
                    why = f"по-сигурната е 1.40-1.80 (тази е {o})"
            if why:
                out.append((name, kind, why))
    out += audit_sure(conn)
    for c in conn.execute("SELECT day, idx, legs_json, summary_json FROM columns WHERE idx > 0"):
        if json.loads(c["summary_json"] or "{}").get("manual"):
            continue                               # ръчните колонки на собственика не са на робота
        if c["day"] < FROM_ALL_BLOCKS[:10]:
            continue
        legs = json.loads(c["legs_json"])
        leagues = [l["league"] for l in legs]
        if len(set(leagues)) != len(leagues):
            out.append((f"колонка {c['day']} #{c['idx']}", "column", "два мача от едно първенство"))
        for l in legs:
            flags, league = flags_of.get(l["id"], ({}, l["league"]))
            why = match_block(flags, "model") or pick_block("column", l["sel"], l["p"], l["odds"], league)
            if not why and c["day"] >= FROM_SURE[:10]:
                why = sure_block(flags)                # колонката е само от най-сигурните мачове
            if why:
                out.append((f"колонка {c['day']} #{c['idx']}: {l['home']} - {l['away']}", "column", why))
    return out


def audit_sure(conn):
    """Одиторът за „най-сигурните“: ПРЕСМЯТА независимо белега на всяка група (първенство × ден) от записаните шансове.
    Проверява: всеки кандидат има белег; най-сигурните не са повече от третината (нагоре) и са с шанс 65%+; никой неотбелязан
    не е по-вероятен от отбелязан; а когато всички членове на групата са отбелязани наведнъж - точно очакваното множество."""
    out, groups = [], {}
    for t in conn.execute("SELECT fixture_id, league, home, away, kickoff, basis, picks_json, flags_json FROM tips "
                          "WHERE locked_at >= ?", (FROM_SURE,)):
        flags = json.loads(t["flags_json"] or "{}")
        one = json.loads(t["picks_json"]).get("one")
        if flags.get("derby") or t["basis"] != "model" or not one:
            continue
        name, s = f"{t['home']} - {t['away']}", flags.get("sure")
        if not s:
            out.append((name, "sure", "липсва белегът „топ шанс“ (стъпка 4в не е минала)"))
            continue
        groups.setdefault((t["league"], sure.day_of(t["kickoff"])), []).append((t["fixture_id"], one["p"], bool(s["y"]), s, name))
    for (league, day), ms in groups.items():
        base = [m for m in ms if not m[3].get("late")]
        if not base:
            continue
        n = base[0][3]["n"]
        k = -(-n // 3)                                     # третина, нагоре
        yes = [m for m in base if m[2]]
        if len(yes) > k:
            out.append((f"{league} {day}", "sure", f"{len(yes)} с топ шанс при {n} кандидата (най-много {k})"))
        for m in yes:
            if m[1] < 0.65:
                out.append((m[4], "sure", f"отбелязан с топ шанс при шанс {m[1]:.0%} (под 65%)"))
        floor = min((m[1] for m in yes), default=None)
        if floor is not None:
            for m in base:
                if not m[2] and m[1] > floor + 1e-9:
                    out.append((m[4], "sure", f"няма топ шанс при шанс {m[1]:.0%}, а най-слабият отбелязан е {floor:.0%}"))
        if len(base) == n and len(ms) == len(base):        # цялата група е отбелязана наведнъж - точно очакваното
            order = sorted(base, key=lambda m: (-m[1], str(m[0])))[:k]
            expected = {m[0] for m in order if m[1] >= 0.65}
            if expected != {m[0] for m in yes}:
                out.append((f"{league} {day}", "sure", "множеството с топ шанс не съвпада с пресметнатото независимо"))
    return out


def check_and_alert(conn, now=None):
    """Стъпка в облака: одиторът върху записаното; при нарушение - грешка в лога и ЕДНО известие на ден за същия набор."""
    import hashlib
    import logging
    from . import notify
    now = now or datetime.now(timezone.utc)
    bad = audit(conn)
    if not bad:
        return True
    log = logging.getLogger(__name__)
    for name, kind, why in bad[:20]:
        log.error("НАРУШЕНО ПРАВИЛО: %s [%s] - %s", name, kind, why)
    key = hashlib.sha1(json.dumps(bad, ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
    notify.once(conn, f"rules:{now.date().isoformat()}:{key}", lambda: notify.send(
        "Нарушено правило на майстора",
        f"{len(bad)} нарушения в записаните прогнози/колонки. Първите: " + "; ".join(f"{n} [{k}] {w}" for n, k, w in bad[:3]),
        tags="warning", priority=4))
    return False


def summary():
    """Правилата за сайта."""
    return [{k: r[k] for k in ("id", "title", "applies", "status", "said", "evidence")} for r in RULES]
