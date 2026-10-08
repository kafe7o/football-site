"""
Роботът - прогноза за всеки мач от всяко първенство (идеята на професионалиста, 2026-09-29).

За всеки мач роботът избира по един изход на всеки пазар:
  1x2   най-вероятното от 1 / X / 2
  dc    най-вероятният двоен шанс: 1X / X2 / 12
  ou    над или под 2.5 гола
и ГЛАВЕН СЪВЕТ - един изход с коефициент поне MIN_ODDS (1.40, прагът на професионалиста),
избран по правилото RULE. Правилото е избрано по проверката назад (research/robot_backtest.py,
протоколът е в docstring-а му), не по усещане. 2026-09-29, 39 лиги, 2023/24-2026/27:

  правило          избор (24 600 съвета)          чиста проверка (14 500)
  market_likely    58.2% познати, доход -5.44%    57.0%, -7.70%     <- прието (най-висок доход в избора)
  value            54.8%, -5.53%                  54.1%, -7.38%
  agree            58.1%, -5.59%                  57.0%, -7.73%
  likely (модела)  56.1%, -5.96%                  54.9%, -8.22%

Втори етап (същия ден): в лигите без коефициенти над/под, когато всички вероятни изходи са под
1.40, правилото избираше аутсайдер (X2 @ 3.50 - Норвегия: 48.8% познати). Добавено условие шансът
по пазара да е поне MIN_PROB = 50% (съветът по-скоро излиза, отколкото не). Прието, защото е по-добро
и в избора (-4.75% срещу -5.44%), и в чистата проверка (-7.15% срещу -7.70%); 59.7% / 58.6% познати.

СМЕНЕНО 2026-09-30 по указание на професионалиста (собственикът го предаде, „КАКТО ТОЙ Е КАЗАЛ“):
„да не се влияе от коефициентите, когато казва процента“ - при правилото по пазара шансът беше
просто коефициентът без маржа (1.60 -> ~60%). Сега RULE = "likely": най-вероятният изход ПО
СОБСТВЕНИЯ МОДЕЛ на робота, с шанс поне 50% по модела; коефициентът служи само за прага 1.40 и
за парите. Където модел няма (лига без история, непознат отбор) - съвет НЯМА: роботът няма
собствена оценка. Цената на решението, измерена на същите мачове (само тези с модел, без дерби):

  правило                          избор 23/24-24/25            чиста 25/26-26/27
  пазарът, шанс >= 50% (до 30.09)  59.7% познати, -4.80%        58.5%, -7.30%
  роботът сам, шанс >= 50% (сега)  57.2% познати, -5.35%        56.0%, -7.78%

Около 2.5 пункта по-малко познати и 0.5 пункта по-лош доход - в рамките на две грешки в чистата
проверка. И двете губят колкото маржа; предимство няма нито по едното, нито по другото.

Вероятностите на робота са от модела (Poisson с атака/защита, половин голове половин xG за
5-те големи лиги, bets/model.py). Където модел няма (лига без история, непознат отбор, малко
мачове) - от пазара (средните цени без маржа). Кое е ползвано, пише в `basis`.

Съветът НЕ се дава:
  - в дерби (съветът на професионалиста, потвърден на 1465 дербита: повече равни от обещаното);
  - на фаворит 1.30-1.55 в „тото“ лига (потвърдено за Холандия - фаворитите там печелят 63.9%
    при обещани 68.4%, и в 2012-2019, и в 2019-2026).
Прогнозата по пазари се записва и тогава - за статистиката.
"""

MIN_ODDS = 1.40
MIN_PROB = 0.50       # главният съвет - само изход с шанс поне 50% (по модела на робота)
SELECTIONS = ["1", "X", "2", "1X", "X2", "12", "O", "U"]
MARKET_OF = {"1": "1x2", "X": "1x2", "2": "1x2", "1X": "dc", "X2": "dc", "12": "dc", "O": "ou", "U": "ou"}
LABEL = {"1": "1", "X": "X", "2": "2", "1X": "1X", "X2": "X2", "12": "12", "O": "над 2.5", "U": "под 2.5"}
# „Тото“ лиги по съвета на професионалиста: фаворит в лентата печели по-рядко от обещаното -
# и в 2012-2019, и в 2019-2026 (research/league_analysis.py, 2026-09-29). ЗАКЛЮЧЕНИ в кода:
# седмичният анализ в облака само съобщава, ако списъкът по данните се промени - роботът не се
# мени без решение на собственика (2026-09-30: „алгоритмите да не се променят“).
TOTO_BAND = (1.30, 1.55)
TOTO_LEAGUES = frozenset({"N1", "AUT"})
RULE = "likely"
# Кога е сменено правилото (облакът тръгна с новия код). Съвет, записан по-рано, е по старото
# market_likely - записът не се пипа, на сайта е отбелязан като такъв.
RULE_CHANGED_AT = "2026-09-30T09:48:00+00:00"


# правилата, при които главният съвет беше 1/X/2, двоен шанс или над/под 2.5 (до 30.09)
OLD_RULES = ("market_likely", "likely")


def rule_of(flags, locked_at):
    """По кое правило е записан съветът: от записа, а за старите - по часа."""
    return (flags or {}).get("rule") or (RULE if locked_at >= RULE_CHANGED_AT else "market_likely")


# ---------- рискова и по-сигурна прогноза (от 2026-09-30 вечерта) ----------
# Професионалистът: „като дава прогнози, да не се съобразява с коефициенти и фаворити“; „да
# използва и трите знака - странно е, че няма X“; „за всеки мач рискова прогноза (напр. X) и
# по-сигурна (1X, под 2.5, над 3.5 картона - каквото вижда като събития в мача)“. Проверено назад
# (research/signs_backtest.py, 36 000 мача, 2023-2026):
#   рисковата „спрямо обичайното за лигата“: знаци 1 47% / X 13% / 2 40%; познава 46%
#     (най-вероятният знак - 49%, но X почти никога); X познава 29.6% при ср. коеф. 3.27;
#   по-сигурната - най-вероятното събитие: познава 80%; двоен шанс 27%, над 1.5 - 37%, под 3.5 - 36%.
# Коефициентите не участват в избора - само се показват до него.
RULE_SIGNS = "signs"
GOAL_LINES = {"O15": ("O", 1.5), "U15": ("U", 1.5), "O": ("O", 2.5), "U": ("U", 2.5), "O35": ("O", 3.5), "U35": ("U", 3.5)}
LABELS = {**LABEL, "O15": "над 1.5", "U15": "под 1.5", "O35": "над 3.5", "U35": "под 3.5",
          "GG": "двата вкарват", "NG": "не вкарват и двата",
          # професионалистът (01.10): хендикап при голям фаворит и кой изпълнява повече корнери
          "H1": "1 с хендикап 0:1 (домакинът с 2+ гола разлика)", "H2": "2 с хендикап 1:0 (гостът с 2+ гола разлика)",
          "H1_2": "1 с хендикап 0:2 (домакинът с 3+ гола разлика)", "H2_2": "2 с хендикап 2:0 (гостът с 3+ гола разлика)",
          "KH": "повече корнери - домакинът", "KD": "равен брой корнери", "KA": "повече корнери - гостът"}
# хендикапите: (страна, поне толкова гола разлика)
HANDICAPS = {"H1": (1, 2), "H2": (-1, 2), "H1_2": (1, 3), "H2_2": (-1, 3)}
CORNER_SIDES = ("KH", "KD", "KA")


def label(sel):
    """Етикет за всеки избор, и за картоните/корнерите: CO4.5 = картони над 4.5, KU9.5 = корнери под 9.5."""
    if sel and sel[0] in "CK" and len(sel) > 2 and sel[1] in "OU":
        return f"{'картони' if sel[0] == 'C' else 'корнери'} {'над' if sel[1] == 'O' else 'под'} {sel[2:]}"
    return LABELS.get(sel, sel)


def risky_sign(p, base):
    """Рисковата прогноза: знакът, чийто шанс по робота е най-много НАД обичайния за лигата
    (base = честотата на 1, X и 2 в лигата). Фаворитът не получава предимство само защото е фаворит."""
    return max(("1", "X", "2"), key=lambda s: p[s] / base[s])


def goal_line_probs(p):
    """Шансът за над 1.5 / 2.5 / 3.5 и двата вкарват - от очакваните голове на робота."""
    import numpy as np
    from .model import score_grid
    grid = score_grid(p["xg_home"], p["xg_away"])
    k = np.arange(grid.shape[0])
    total = k[:, None] + k[None, :]
    return {1.5: float(grid[total >= 2].sum()), 2.5: float(grid[total >= 3].sum()),
            3.5: float(grid[total >= 4].sum()), "GG": float(grid[1:, 1:].sum())}


def handicap_probs(p):
    """Шансът по робота за победа с 2+ и 3+ гола разлика на всяка страна - от очакваните голове."""
    import numpy as np
    from .model import score_grid
    grid = score_grid(p["xg_home"], p["xg_away"])
    k = np.arange(grid.shape[0])
    diff = k[:, None] - k[None, :]
    return {code: float(grid[side * diff >= n].sum()) for code, (side, n) in HANDICAPS.items()}


def corner_sides(home, away, top=40):
    """Кой изпълнява повече корнери: шансът за домакина, равно и госта (Poisson за очакваните на всеки отбор)."""
    import numpy as np
    from scipy.stats import poisson
    joint = np.outer(poisson.pmf(np.arange(top), home), poisson.pmf(np.arange(top), away))
    return {"KH": float(np.tril(joint, -1).sum()), "KD": float(np.trace(joint)), "KA": float(np.triu(joint, 1).sum())}


def safer_pick(p, sign, extras=None):
    """По-сигурната прогноза: най-вероятното събитие измежду двойния шанс с рисковия знак, над/под
    1.5/2.5/3.5 гола, двата вкарват да/не, картони и корнери над/под основната линия.
    Връща (избор, шанс)."""
    dc = {"1": "1X", "2": "X2", "X": max(("1X", "X2"), key=lambda s: p[s])}[sign]
    cands = [(dc, p[dc])]
    if p.get("xg_home") is not None:
        g = goal_line_probs(p)
        for line, code in ((1.5, "15"), (2.5, ""), (3.5, "35")):
            over = g[line]
            cands.append((("O" if over >= 0.5 else "U") + code, max(over, 1 - over)))
        cands.append(("GG" if g["GG"] >= 0.5 else "NG", max(g["GG"], 1 - g["GG"])))
    for kind, letter in (("cards", "C"), ("corners", "K")):
        x = (extras or {}).get(kind)
        if x:
            cands.append((f"{letter}{x['pick']}{x['line']}", x["over"] if x["pick"] == "O" else 1 - x["over"]))
    return max(cands, key=lambda c: c[1])


def hit_any(sel, hg, ag, cards=None, corners=None, corner_split=None):
    """Излязъл ли е изборът - и за линиите на головете, картоните, корнерите, хендикапа и кой изпълнява
    повече корнери (corner_split = (на домакина, на госта)). None - още няма данни."""
    if sel in HANDICAPS:
        side, n = HANDICAPS[sel]
        return side * (hg - ag) >= n
    if sel in CORNER_SIDES:
        if not corner_split or corner_split[0] is None or corner_split[1] is None:
            return None
        h, a = corner_split
        return {"KH": h > a, "KD": h == a, "KA": h < a}[sel]
    if sel in GOAL_LINES:
        side, line = GOAL_LINES[sel]
        return (hg + ag > line) == (side == "O")
    if sel == "GG":
        return hg > 0 and ag > 0
    if sel == "NG":
        return hg == 0 or ag == 0
    if sel and sel[0] in "CK" and len(sel) > 2 and sel[1] in "OU":
        total = cards if sel[0] == "C" else corners
        if total is None:
            return None
        return (total > float(sel[2:])) == (sel[1] == "O")
    return hit(sel, hg, ag)


def base_rates(conn, league, years=4):
    """Честотата на 1, X, 2 в лигата за последните години (за рисковата прогноза)."""
    from datetime import date
    since = f"{date.today().year - years}-{date.today().isoformat()[5:]}"
    row = conn.execute("SELECT COUNT(*), AVG(fthg > ftag), AVG(fthg = ftag), AVG(fthg < ftag) FROM matches "
                       "WHERE league = ? AND fthg IS NOT NULL AND date >= ?", (league, since)).fetchone()
    if not row or row[0] < 150:
        return {"1": 0.44, "X": 0.26, "2": 0.30}          # средното на всички 39 лиги от 2016
    return {"1": row[1], "X": row[2], "2": row[3]}


# ---------- по коефициент: сигурна 1.40-1.80, рискова над 1.80 (от 2026-10-01) ----------
# Професионалистът (30.09 вечерта): „рисковата не може да е 1.48“; „сигурната - между 1.40 и 1.80;
# всичко над 1.80 е рисково, до 1.80 всичко е сигурно“; „за мен рискова е над 1.80“. Кое събитие -
# пак по шанса на робота; коефициентът само казва сигурно ли е или рисково. Без коефициент от
# букмейкър (мачът е след повече от 3 дни или лигата няма цени) - честният коефициент на робота 1/шанс.
# research/ranges_backtest.py (35 000 мача, 2023-2026): сигурната 1.40-1.80 - прогноза в 89% от
# мачовете, 59.2% познати при ср. коеф. 1.60; рисковата над 1.80 - 32.0% при ср. коеф. 3.26 (X в 45%).
# Целта „70% познати“ в този диапазон не се достига: букмейкърът дава ~62% при 1.60, а роботът не го бие.
RULE_RANGES = "ranges"
# СМЕНЕНО 2026-10-01 (собственикът: „както той е казал, с цялата информация досега“) - всичко, което
# професионалистът е казал, заедно: „да не праща повече мачове под 1.50“; „рисковата прогноза е от 2.50
# нагоре“ („не може да е 1.48“); „сигурната между 1.40 и 1.80, до 1.80 всичко е сигурно, над 1.80 - рисково“.
# Сечението: нищо под 1.50; по-сигурна (и едната прогноза) 1.50-1.80; рисковата прогноза - от 2.50;
# 1.80-2.50 е „рисково“, но прогноза там не се дава. Цената - research/master_ranges_check.py.
# СМЕНЕНО ОТНОВО 2026-10-02 (професионалистът: „да не слагаме ограничения в коефициентите, само да ги разделя на
# сигурна и рискова, обаче поне от 1.40 нагоре цялостно - да не ми дава 1.20 „много сигурни“, а после рискова на 1.50“;
# „рисковата да не е само X - не искам 15 хикса, нека да познава знаци“). research/master_split_check.py:
#   по-сигурна от 1.40 (без горна граница), шанс 50%+: 57.9% / 57.0% познати, в 98% от мачовете, доход -5.4% / -7.6%
#   едната прогноза от 1.40: 63.9% / 63.7% (при 1.50-1.80 беше 56.8%), доход -6.7% / -7.7%
#   рискова = знак 1 или 2, който роботът дава ПОД 50% (наистина рисков), по-вероятният от двата; при ясен фаворит
#   (единият над 50%) - рискова няма (R1; R2 с по-слабия отбор: -10.7% / -11.8% - отхвърлено): 41.0% / 42.1%
#   познати, ср. к 2.33, в 56% от мачовете, доход -7.5% / -6.6%; никога X (досега: X в 74%, 26.6% познати)
FLOOR = 1.40                                          # нищо под 1.40
# зелената (по-сигурна) - до 1.80 (собственикът, 02.10: „тези в зелено трябва да са до 1.80 таван“ - професионалистът
# на 30.09: „сигурната между 1.40 и 1.80“). Назад: в 87% от мачовете, 60.0% / 59.0% познати, ср. к 1.59, доход
# -5.0% / -7.4% (без таван: 98%, 58.0% / 57.1%, -5.4% / -7.5%). Рамката (едната прогноза) - от 1.40 без таван.
SAFE_RANGE = (FLOOR, 1.80)
ONE_RANGE = (FLOOR, None)
ONE_FALLBACK_FROM = FLOOR
RISKY_RULE = "under50"
RANGES_CHANGED_AT = "2026-10-03T04:00:00+00:00"       # първият запис по тези правила (03.10, 07:00)
# старите граници (01.10-02.10) - за проверките назад: 1.50-1.80 и рискова от 2.50
RISKY_FROM = 2.50
SAFE_CANDIDATES = ["1", "2", "1X", "X2", "12", "O", "U"]


def price(sel, p, avg):
    """(коефициент, откъде): средният на букмейкърите или честният на робота 1/шанс."""
    if (avg or {}).get(sel):
        return round(avg[sel], 2), "book"
    return (round(1 / p[sel], 2), "robot") if p.get(sel) else (None, None)


# Кои знаци позволява всяка прогноза (професионалистът, 02.10: „няма как сигурна 1X, рискова 2 - противоречи си;
# може сигурна 1X, рискова чисто 1“). Голове, картони, корнери - не ограничават (None).
ALLOWS = {"1": {"1"}, "X": {"X"}, "2": {"2"}, "1X": {"1", "X"}, "X2": {"X", "2"}, "12": {"1", "2"},
          "H1": {"1"}, "H1_2": {"1"}, "H2": {"2"}, "H2_2": {"2"}}
# ИЗКЛЮЧЕНО 2026-10-02 (собственикът, след справката research/before_after_risky.py: „да върнем стария модел“) -
# рисковата пак се избира както до 02.10, без ограничението. Кодът остава, за да се включи с True.
RISKY_CONSISTENT = False


def in_range(odds, rng):
    return odds is not None and odds >= rng[0] and (rng[1] is None or odds <= rng[1])


def in_safe(odds):
    """В границите на зелената (по-сигурната)."""
    return in_range(odds, SAFE_RANGE)


def in_one(odds):
    """В границите на рамката (едната прогноза)."""
    return in_range(odds, ONE_RANGE)


def risky_sign(p, avg, league=None):
    """Рисковата (от 03.10): знак 1 или 2, който роботът дава под 50%, по-вероятният от двата; коефициент от 1.40.
    При ясен фаворит (единият отбор над 50%) - None: за него рисков знак няма."""
    under = [s for s in ("1", "2") if p.get(s) is not None and p[s] < 0.5]
    if len(under) != 2:
        return None
    s = max(under, key=lambda k: p[k])
    o, src = price(s, p, avg)
    if not o or o < FLOOR or toto_blocked(league, s, o):
        return None
    return {"sel": s, "p": round(p[s], 4), "odds": o, "src": src}


def risky_by_odds(p, base, avg, against=()):
    """Рисковата: знакът 1, X или 2 с коефициент от 2.50 нагоре, най-много над обичайното за лигата - само
    измежду знаците, които по-сигурната и едната прогноза (against) позволяват. Ако няма такъв - None."""
    allowed = {"1", "X", "2"}
    for sel in against:
        allowed &= ALLOWS.get(sel, {"1", "X", "2"})
    cands = [s for s in ("1", "X", "2") if s in allowed and (price(s, p, avg)[0] or 0) >= RISKY_FROM]
    if not cands:
        return None
    s = max(cands, key=lambda k: p[k] / base[k])
    o, src = price(s, p, avg)
    return {"sel": s, "p": round(p[s], 4), "odds": o, "src": src, "base": round(base[s], 4)}


def toto_blocked(league, sel, odds):
    """„Тото“ лига (Холандия, Австрия): фаворит с коефициент 1.30-1.55 печели по-рядко от обещаното - без съвет за него.
    Важи за ВСЯКА прогноза (едната, по-сигурната, рисковата), не само за една (2026-10-07)."""
    return bool(league in TOTO_LEAGUES and sel in ("1", "2") and odds and TOTO_BAND[0] <= odds <= TOTO_BAND[1])


OVER_LINES, UNDER_LINES = ("O15", "O", "O35"), ("U15", "U", "U35")


def goal_direction(sel):
    """„над“ / „под“ за линиите на головете, иначе None (1/X/2, двоен шанс, двата вкарват, картони, корнери...)."""
    return "over" if sel in OVER_LINES else "under" if sel in UNDER_LINES else None


def opposes(a, b):
    """Две прогнози за ЕДИН мач са в обратна посока на головете (майсторът, 08.10: „над 2.5 + под 3.5“ иска точно 3 гола - шанс ~1 към 6,
    „над 1.5 + под 2.5“ - точно 2 гола). Една и съща посока е позволена („под 2.5“ и „под 3.5“)."""
    da, db = goal_direction(a), goal_direction(b)
    return bool(da and db and da != db)


def safe_by_odds(p, avg, league=None, against=()):
    """Сигурната: най-вероятното по робота събитие с коефициент 1.50-1.80 (SAFE_RANGE) и шанс по робота поне 50%.
    Без условието за 50% в границите понякога оставаше само събитие, което роботът смята за по-малко
    вероятно (над 2.5 с 47% - тестът на бонус анализа, 01.10). Назад: 79% от мачовете, 59.6% познати
    (без условието - 89%, 59.2%), същият доход."""
    cands = []
    for s in SAFE_CANDIDATES:
        if any(opposes(s, a) for a in against):
            continue               # не се омесва с другата прогноза за мача (against = избора на едната прогноза)
        o, src = price(s, p, avg)
        if o and in_safe(o) and p.get(s, 0) >= MIN_PROB and not toto_blocked(league, s, o):
            cands.append((s, p[s], o, src))
    if not cands:
        return None
    s, prob, o, src = max(cands, key=lambda c: c[1])
    return {"sel": s, "p": round(prob, 4), "odds": o, "src": src}


def result_of(hg, ag):
    return "1" if hg > ag else ("X" if hg == ag else "2")


def hit(sel, hg, ag):
    r = result_of(hg, ag)
    if sel in ("1", "X", "2"):
        return sel == r
    if sel in ("1X", "X2", "12"):
        return r in sel
    if sel == "O":
        return hg + ag >= 3
    if sel == "U":
        return hg + ag <= 2
    raise ValueError(sel)


def picks(p):
    """Изборът на всеки пазар по вероятностите p."""
    out = {"1x2": max(("1", "X", "2"), key=lambda s: p[s]),
           "dc": max(("1X", "X2", "12"), key=lambda s: p[s])}
    if p.get("O") is not None:
        out["ou"] = "O" if p["O"] >= p["U"] else "U"
    return out


def candidates(p, prices):
    """(изход, вероятност, средна цена) за изходите с цена поне MIN_ODDS."""
    avg = (prices or {}).get("avg") or {}
    return [(s, p[s], avg[s]) for s in SELECTIONS
            if p.get(s) is not None and avg.get(s) and avg[s] >= MIN_ODDS]


def choose(rule, robot, market, prices):
    """Главният съвет по правилото: (изход, средна цена) или (None, None)."""
    if rule == "likely":            # най-вероятното по робота, ако шансът по модела е поне 50%
        c = [x for x in candidates(robot, prices) if x[1] >= MIN_PROB]
        best = max(c, key=lambda x: x[1], default=None)
    elif rule == "market_likely":   # най-вероятното по пазара, ако шансът е поне 50%
        c = [x for x in candidates(market or {}, prices) if x[1] >= MIN_PROB]
        best = max(c, key=lambda x: x[1], default=None)
    elif rule == "value":           # най-голяма стойност p*цена сред изходите с шанс поне 50%
        c = [x for x in candidates(robot, prices) if x[1] >= 0.5]
        best = max(c, key=lambda x: x[1] * x[2], default=None)
    elif rule == "agree":           # най-вероятното по робота, само ако и пазарът го дава за най-вероятно
        c = candidates(robot, prices)
        best = max(c, key=lambda x: x[1], default=None)
        mc = candidates(market or {}, prices)
        mbest = max(mc, key=lambda x: x[1], default=None)
        if not best or not mbest or best[0] != mbest[0]:
            best = None
    else:
        raise ValueError(rule)
    return (best[0], best[2]) if best else (None, None)


def block_reason(sel, odds, flags):
    """Защо съветът не се дава (правилата на професионалиста), или None."""
    if flags.get("derby"):
        return "дерби - там не се залага"
    if (flags.get("toto") and sel in ("1", "2") and odds
            and TOTO_BAND[0] <= odds <= TOTO_BAND[1]):
        return "тото лига: фаворитите на 1.30-1.55 тук печелят по-рядко от обещаното"
    return None


def tip(robot, market, prices, flags, rule=RULE):
    """(изход, цена, защо няма) - главният съвет за мача."""
    sel, odds = choose(rule, robot, market, prices)
    if sel is None:
        return None, None, "няма изход с цена от 1.40 и шанс над 50%" if prices else "цените идват до 3 дни преди мача"
    reason = block_reason(sel, odds, flags)
    if reason:
        return None, None, reason
    return sel, odds, None


def after_break(dates, day, min_gap=12, max_gap=25, within=4):
    """Първи кръг след пауза: преди тази дата лигата е спряла 12-25 дни (от 2026 г. септемврийският
    и октомврийският прозорец за националните отбори са слети - паузата е до 3 седмици).
    dates - датите с мачове на лигата (изиграни и предстоящи), day - датата на мача (ISO)."""
    from datetime import date
    d = date.fromisoformat(day)
    prev = sorted({date.fromisoformat(x) for x in dates if x < day}, reverse=True)
    start = d
    for p in prev:
        gap = (start - p).days
        if gap >= min_gap:
            return gap <= max_gap and (d - start).days <= within and start.month not in (6, 7)
        if (d - p).days > within:
            return False
        start = p
    return False


# ---------- ЕДНА прогноза за мач (от 2026-10-02) ----------
# Професионалистът (01.10): „по една прогноза за всеки мач, която според него е най-адекватна - той сам
# да определи точната прогноза; иначе при четири прогнози и 3 от 4 познати ние трябва да гадаем коя да
# изберем; с точно една ще видим точно какъв процент държи този робот.“ Изборът е по неговите правила:
# коефициент 1.40-1.80 и шанс по робота поне 50%, без тото фаворит; ако в мача няма такова събитие -
# най-вероятното над 1.80. Кой вариант - research/one_pick_backtest.py (протоколът е в docstring-а му).
# Назад (36 000 мача, 2023-2026; избор / чиста проверка):
#   A  най-вероятното от всички събития      64.3% / 63.8% познати  <- прието (най-много познати в избора)
#   A2 без картони и корнери                 64.3% / 64.0%
#   B  само с коефициент от букмейкър        56.2% / 54.9%
#   C  най-характерното за мача              58.3% / 57.7% (но +7 пункта над обичайното за лигата)
# Роботът казва средно 66%, излиза 64% (изборът на най-вероятното от много събития надценява малко).
# Същото събитие излиза в лигата изобщо в 63% от мачовете - роботът добавя около 1 пункт. Прогнозите
# са предимно голове над/под (64%), двоен шанс (17%), двата вкарват (8%), 1/X/2 (6%), картони/корнери (5%).
# Където има коефициент от букмейкър: доход -6.8% / -7.9% (губи колкото маржа).
# С границите от 01.10 (1.50-1.80, резерва от 2.50): A 56.7% / 56.8%, роботът казва 59%, в лигата изобщо 56%,
# ср. к 1.77, доход -7.3% / -8.4%; прогнозите - голове 43%, двоен шанс 18%, двата вкарват 15%, 1/X/2 11%, картони 10%.
RULE_ONE = "one"
ONE_VARIANT = "A"
# хендикапите и кой изпълнява повече корнери - в кандидатите (research/handicap_corners_backtest.py,
# 01.10: умение и в двата периода; едната прогноза с тях 56.9% / 56.6% срещу 56.7% / 56.8% - приета)
ONE_EXTRA = True
ONE_GOAL_SELS = ("1", "X", "2", "1X", "X2", "12", "O15", "U15", "O", "U", "O35", "U35", "GG", "NG")


def one_events(p, extras=None):
    """Шансът по робота за всички събития, от които се избира едната прогноза."""
    g = goal_line_probs(p)
    ev = {s: p[s] for s in ("1", "X", "2", "1X", "X2", "12")}
    ev.update({"O15": g[1.5], "U15": 1 - g[1.5], "O": p.get("O", g[2.5]), "U": p.get("U", 1 - g[2.5]),
               "O35": g[3.5], "U35": 1 - g[3.5], "GG": p.get("GG", g["GG"]), "NG": 1 - p.get("GG", g["GG"])})
    for kind, letter in (("cards", "C"), ("corners", "K")):
        x = (extras or {}).get(kind)
        if x:
            ev[f"{letter}O{x['line']}"] = x["over"]
            ev[f"{letter}U{x['line']}"] = 1 - x["over"]
    if ONE_EXTRA:
        ev.update(handicap_probs(p))
        c = (extras or {}).get("corners")
        if c and c.get("home") is not None and c.get("away") is not None:
            sides = corner_sides(c["home"], c["away"])
            ev.update({"KH": sides["KH"], "KA": sides["KA"]})
    return ev


def one_pick(p, avg, extras=None, league=None, variant=ONE_VARIANT):
    """Едната прогноза: {sel, p, odds, src, band} или None (без очаквани голове - без прогноза)."""
    if p.get("xg_home") is None:
        return None
    cands = []
    for s, prob in one_events(p, extras).items():
        if variant == "A2" and s[0] in "CK":
            continue
        book = s in SELECTIONS and bool((avg or {}).get(s))
        if variant == "B" and not book:
            continue
        odds = round(avg[s], 2) if book else (round(1 / prob, 2) if prob > 0 else None)
        if not odds:
            continue
        if league in TOTO_LEAGUES and s in ("1", "2") and book and TOTO_BAND[0] <= odds <= TOTO_BAND[1]:
            continue
        cands.append((s, prob, odds, "book" if book else "robot"))
    band = [c for c in cands if in_one(c[2]) and c[1] >= MIN_PROB]
    # ако няма събитие с шанс 50%+ от 1.40 нагоре - най-вероятното от 1.40 нагоре
    pool, in_band = (band, True) if band else ([c for c in cands if c[2] >= ONE_FALLBACK_FROM], False)
    if not pool:
        return None
    s, prob, odds, src = max(pool, key=lambda c: c[1])
    return {"sel": s, "p": round(prob, 4), "odds": odds, "src": src, "band": in_band}
