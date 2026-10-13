"""
НАДСТРОЙКАТА (собственикът, 2026-10-13: „дай това най-доброто - сложи го като надстройка“) - най-доброто, измерено назад от ~9.6 млн. варианта
(research/all_variants_backtest.py, all_variants_v2.py, mixed_kinds_backtest.py). Старите колонки (правилата на майстора), системата и сянката остават; надстройката е ОТГОРЕ.

ИЗВЪН правилата на майстора (решение на собственика): минималният коефициент е 1.30, не 1.40, а шансът е смес от робота и пазара (майсторът: „да не се влияе от
коефициентите, когато казва процента“). Без дерби и без „тото“ - както навсякъде.

Правилото (точно както в проверката назад):
  мач - само голове (над/под 1.5, 2.5, 3.5); шансът = (робот + пазар) / 2; роботът и пазарът са съгласни (до 5 пункта) за същото събитие; шанс >= 65%;
  истинска цена (средната на букмейкърите; за над/под 1.5 и 3.5 - оценка от пазара над/под 2.5, както назад) >= 1.30; изборът - събитието с най-голям шанс;
  10 първенства (най-добрата половина по връщане на мач в избора); подредба по шанс x най-добрата цена; по един мач от първенство;
  всеки ден 5-те най-добри мача и ВСИЧКИ 5 четворки от тях (система 4 от 5); ден без 5 такива мача от 5 първенства - без надстройка (назад ~4 дни в месеца имат).
  От 14.10 (собственикът: „най-най-най-доброто от най-доброто“) - 4 от 5 вместо 3 от 4: research/adaptive_columns_backtest.py (678 варианта) - единственото, което е на плюс
  във ВСЕКИ период и при ДВЕТЕ цени по правилото за парите (4 от 5: +27 / +32 / +35 €/месец при най-добрата цена, +34 / +3 / +20 при средната; 3 от 4: +66 / +49 / +8 и +32 / -1 / +5).
  Не е избрано от протокола (в първите две години 3 от 4 беше по-добро при най-добрата цена) - решение по всички проверки заедно; мери се на живо.
Измерено назад (последните 15 месеца, 2025-07..2026-09): 315 четворки в 63 дни, минават 31.1%, ср. коеф. 4.17, връщат 1.28 ± 0.11 € от 1 € при НАЙ-ДОБРИЯ букмейкър, 1.08 ± 0.09 при средната;
  в 67% от дните поне една четворка минава. 3 от 4 (до 14.10): 336 тройки, 39.6%, 2.91, 1.141 ± 0.077 (0.968 при средна).
Записът: първото пускане след 07:00 (стъпка 6а4 на run.py cloud), от записаните сутринта прогнози - веднъж на ден, не се мени. Таблица columns_addon:
  idx 0 - мачовете на деня (или празно и бележка защо няма), idx 1-5 - четворките. Уреждане - стъпка 5б4. Преглед час преди първия мач - ключ add:<ден>:0.
"""

import json
import logging
import math
from datetime import datetime, timezone
from itertools import combinations

from . import columns, robot, rules

log = logging.getLogger(__name__)

FROM = "2026-10-13T05:00:00+00:00"           # от 13.10 сутринта (решението на собственика) - първият запис е за оставащите мачове на 13.10
LEAGUES = frozenset({"B1", "E3", "EC", "G1", "I2", "N1", "SC0", "SC3", "SP1", "T1"})
GOALS = ("O15", "U15", "O", "U", "O35", "U35")
MIN_P, FLOOR, AGREE, TOP_N, SIZE, MAX_RATIO = 0.65, 1.30, 0.05, 5, 4, 1.25          # 5 мача, всички четворки от тях (4 от 5)
UNIT, UNIT_PL = "четворка", "четворки"
# ПАРИТЕ - решение на собственика (13.10): „в най-лошия месец да губим не 300 €, а 30 €“ и „залагаме 30 €, точка“: 30 € на ДЕН с надстройка, разделени поравно между
# колонките (5 четворки по 6 €); щом месецът стигне -30 €, до края му - без залог; залогът за деня никога не е повече от остатъка до -30 € (по уредените колонки на месеца;
# неуредените се броят като загубени). Назад при 4 от 5 (scratchpad four_of_five, 14.10): най-лош месец -30 €, средно +35 €/месец в последните 15 месеца (+31.5 € за всичките 39),
# на плюс в 6 от 15 (12 от 39), най-добър +188 € (+308 €); при средната цена +19.5 €/месец в последните 15.
STAKE_DAY, MONTH_CAP = 30.0, 30.0
STAKE = STAKE_DAY / TOP_N
MEASURED = {"form": "4 от 5", "legs": TOP_N, "cols": 5, "unit": UNIT, "unit_pl": UNIT_PL,
            "n": 315, "days": 63, "pass": 0.311, "odds": 4.17, "return_best": 1.28, "se": 0.11, "return_avg": 1.08, "day_any": 0.67, "days_per_month": 4.2,
            "stake_day": STAKE_DAY, "stake": STAKE, "cap": MONTH_CAP, "avg_month": 35, "avg_month_avg": 19.5, "avg_month_all": 31.5, "months_plus": 6, "months": 15,
            "months_plus_all": 12, "months_all": 39, "worst": -30, "best": 188, "best_all": 308}


def month_status(conn, day, include=False):
    """(резултатът на месеца по уредените тройки, заложено в неуредени, остатък до -30 €) - по записаните залози на всяка тройка; днешният ден - ако include."""
    net = pending = 0.0
    op = "<=" if include else "<"
    for r in conn.execute(f"SELECT passed, summary_json FROM columns_addon WHERE idx > 0 AND substr(day, 1, 7) = ? AND day {op} ?", (day[:7], day)):
        s = json.loads(r["summary_json"])
        stake = s.get("stake", STAKE)
        if r["passed"] is None:
            pending += stake
        else:
            net += (s["odds"] - 1) * stake if r["passed"] else -stake
    return round(net, 2), round(pending, 2), round(MONTH_CAP + net - pending, 2)
RULE = ("само голове (над/под 1.5, 2.5, 3.5); шансът е смес робот + пазар, двамата съгласни до 5 пункта, поне 65%; коефициент от 1.30; 10 първенства; "
        "по един мач от първенство; всеки ден 5-те най-добри мача (по шанс x най-добрата цена) и всички 5 четворки от тях - система 4 от 5; "
        "ако падне един мач, една четворка пак минава")


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS columns_addon (day TEXT NOT NULL, idx INTEGER NOT NULL, legs_json TEXT NOT NULL, "
                 "summary_json TEXT NOT NULL, locked_at TEXT NOT NULL, passed INTEGER, PRIMARY KEY (day, idx))")


def market_goals(avg):
    """Шансът на пазара за 6-те събития от над/под 2.5 (степенното махане на маржа тук е пропорционално, както назад - research/goal_lines_backtest.market_probs)."""
    from scipy.stats import poisson
    from .shadow import _lam_from
    po = (1 / avg["O"]) / (1 / avg["O"] + 1 / avg["U"])
    lam = _lam_from(po)
    return {"O15": 1 - poisson.cdf(1, lam), "U15": poisson.cdf(1, lam), "O": po, "U": 1 - po, "O35": 1 - poisson.cdf(3, lam), "U35": poisson.cdf(3, lam)}


def prices(sel, avg, best, probs):
    """(средна, най-добра) цена: над/под 2.5 - от букмейкърите; над/под 1.5 и 3.5 - оценка от пазара над/под 2.5 (марж 5%), а най-добрата - оценката
    x (най-добра / средна на същата страна на 2.5), ако отношението е 1.00-1.25 (иначе счупена цена - взима се средната)."""
    from .shadow import estimated_price
    if sel in ("O", "U"):
        a = avg.get(sel)
        b = best.get(sel) or a
        return a, (b if a and a <= b <= a * MAX_RATIO else a)
    a = estimated_price(sel, avg, probs)
    if a is None:
        return None, None
    side = "O" if sel in ("O15", "O35") else "U"
    ratio = (best.get(side) or 0) / avg[side] if avg.get(side) else 1.0
    return a, round(a * ratio, 2) if 1.0 <= ratio <= MAX_RATIO else a


def candidate(r):
    """Записаният мач -> изборът на надстройката или None."""
    flags = json.loads(r["flags_json"] or "{}")
    if rules.match_block(flags, r["basis"]) or flags.get("postponed"):
        return None
    pj = json.loads(r["prices_json"]) if r["prices_json"] else {}
    avg, best = (pj or {}).get("avg") or {}, (pj or {}).get("best") or {}
    rb = (json.loads(r["probs_json"]) or {}).get("robot") or {}
    if not avg.get("O") or not avg.get("U") or not rb.get("xg_home") or not rb.get("xg_away"):
        return None
    g = robot.goal_line_probs(rb)
    p = {"O15": g[1.5], "U15": 1 - g[1.5], "O": g[2.5], "U": 1 - g[2.5], "O35": g[3.5], "U35": 1 - g[3.5]}
    q = market_goals(avg)
    top = None
    for sel in GOALS:
        if abs(p[sel] - q[sel]) > AGREE:
            continue
        pa, pb = prices(sel, avg, best, rb)
        if pa is None or pa < FLOOR or robot.toto_blocked(r["league"], sel, pa):
            continue
        mix = (p[sel] + q[sel]) / 2
        if top is None or mix > top["p"]:
            top = {"sel": sel, "p": round(mix, 4), "p_robot": round(p[sel], 4), "p_market": round(q[sel], 4), "odds": round(pb, 2), "odds_avg": round(pa, 2),
                   "src": "book" if sel in ("O", "U") else "est"}
    if top is None or top["p"] < MIN_P:
        return None
    return {"id": r["fixture_id"], "league": r["league"], "kickoff": r["kickoff"], "home": r["home_src"] or r["home"], "away": r["away_src"] or r["away"], **top}


def lock(conn, now=None):
    """Надстройката за деня - веднъж, след записа в 07:00. Връща броя на колонките (5 или 0)."""
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    if now.isoformat() < FROM:
        return 0
    day = columns.day_of(now.isoformat())
    if conn.execute("SELECT 1 FROM columns_addon WHERE day = ? AND idx = 0", (day,)).fetchone():
        return 0
    stamp = now.isoformat(timespec="seconds")

    def mark(note, legs=()):
        conn.execute("INSERT OR IGNORE INTO columns_addon (day, idx, legs_json, summary_json, locked_at) VALUES (?, 0, ?, ?, ?)",
                     (day, json.dumps(list(legs), ensure_ascii=False), json.dumps({"cday": day, "note": note}, ensure_ascii=False), stamp))
        conn.commit()

    start, end = columns.day_bounds(day)
    q = ",".join("?" * len(LEAGUES))
    rows = conn.execute(f"SELECT t.*, f.home_src, f.away_src FROM tips t LEFT JOIN fixtures f ON f.id = t.fixture_id WHERE t.kickoff >= ? "
                        f"AND t.kickoff < ? AND t.basis = 'model' AND t.league IN ({q})",
                        (max(start, now).isoformat(), end.isoformat(), *LEAGUES)).fetchall()
    cands = [c for c in (candidate(r) for r in rows) if c]
    cands.sort(key=lambda c: -c["p"] * c["odds"])
    legs, lgs = [], set()
    for c in cands:
        if c["league"] in lgs:
            continue
        legs.append(c)
        lgs.add(c["league"])
        if len(legs) == TOP_N:
            break
    if not rows:
        mark("днес няма записани мачове в 10-те първенства")
        log.info("Надстройката %s: без - няма мачове в първенствата", day)
        return 0
    if len(legs) < TOP_N:
        mark(f"само {len(legs)} мача по правилото от {len(rows)} записани в 10-те първенства - трябват {TOP_N} от различни първенства")
        log.info("Надстройката %s: без - %d мача по правилото", day, len(legs))
        return 0
    net, pending, room = month_status(conn, day)
    stake = math.floor(min(STAKE, max(room, 0) / TOP_N) * 2) / 2          # по 0.50 €, никога над остатъка до -30 €
    for k, trio in enumerate(combinations(range(TOP_N), SIZE), 1):
        ls = [legs[j] for j in trio]
        summary = {"cday": day, "odds": round(math.prod(l["odds"] for l in ls), 2), "odds_avg": round(math.prod(l["odds_avg"] for l in ls), 2),
                   "claimed": round(math.prod(l["p"] for l in ls), 4), "book": all(l["src"] == "book" for l in ls), "stake": stake}
        conn.execute("INSERT OR IGNORE INTO columns_addon (day, idx, legs_json, summary_json, locked_at) VALUES (?, ?, ?, ?, ?)",
                     (day, k, json.dumps(ls, ensure_ascii=False), json.dumps(summary), stamp))
    conn.execute("INSERT OR IGNORE INTO columns_addon (day, idx, legs_json, summary_json, locked_at) VALUES (?, 0, ?, ?, ?)",
                 (day, json.dumps(legs, ensure_ascii=False), json.dumps({"cday": day, "note": "надстройка", "stake": stake, "month_net": net,
                                                                          "pending": pending, "room": room}, ensure_ascii=False), stamp))
    conn.commit()
    log.info("Надстройката %s (залог %.2f € на %s, остатък до -30 € за месеца %.2f €): %s", day, stake, UNIT, room,
             " | ".join(f"{l['home']} - {l['away']} {l['sel']} {l['p']:.0%} @{l['odds']:.2f}" for l in legs))
    return 4


def settle(conn):
    from .shadow import settle as _settle
    ensure(conn)
    return _settle(conn, "columns_addon")


def _with_results(conn, legs):
    for leg in legs:
        t = conn.execute("SELECT hg, ag FROM tips WHERE fixture_id = ?", (leg["id"],)).fetchone()
        done = bool(t and t["hg"] is not None)
        leg["hit"] = bool(robot.hit_any(leg["sel"], t["hg"], t["ag"])) if done else None
        leg["score"] = [t["hg"], t["ag"]] if done else None
    return legs


def for_site(conn, now=None):
    """За сайта: днешните мачове и колонки (или защо няма), всички досега (минали, връщане, по правилото), минали дни, по месеци, измереното назад."""
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    today = columns.day_of(now.isoformat())
    out = {"from": FROM[:10], "rule": RULE, "leagues": sorted(LEAGUES), "measured": MEASURED, "today": None, "past": [], "monthly": {}}
    days = {}
    for r in conn.execute("SELECT day, idx, legs_json, summary_json, locked_at, passed FROM columns_addon ORDER BY day DESC, idx"):
        d = days.setdefault(r["day"], {"day": r["day"], "legs": [], "trebles": [], "note": None, "locked_at": r["locked_at"]})
        s = json.loads(r["summary_json"])
        if r["idx"] == 0:
            d["legs"] = _with_results(conn, json.loads(r["legs_json"]))
            d["note"] = s.get("note")
            d["stake"] = s.get("stake", STAKE)
            d["room"] = s.get("room")
        else:
            ids = [l["id"] for l in json.loads(r["legs_json"])]
            d["trebles"].append({"idx": r["idx"], "ids": ids, "passed": r["passed"], "stake": s.get("stake", STAKE),
                                 **{k: s[k] for k in ("odds", "odds_avg", "claimed")}})
    settled = [t for d in days.values() for t in d["trebles"] if t["passed"] is not None]
    out["all"] = {"days": sum(1 for d in days.values() if d["trebles"]), "trebles": sum(len(d["trebles"]) for d in days.values()), "settled": len(settled),
                  "passed": sum(t["passed"] for t in settled), "return": (sum(t["odds"] for t in settled if t["passed"]) / len(settled)) if settled else None,
                  "net": round(sum((t["odds"] - 1) * t["stake"] if t["passed"] else -t["stake"] for t in settled), 2)}
    net, pending, room = month_status(conn, today, include=True)
    out["month"] = {"m": today[:7], "net": net, "pending": pending, "room": room, "cap": MONTH_CAP}
    for d in days.values():
        for t in d["trebles"]:
            if t["passed"] is not None:
                m = out["monthly"].setdefault(d["day"][:7], {"settled": 0, "passed": 0, "net": 0.0})
                m["settled"] += 1
                m["passed"] += t["passed"]
                m["net"] = round(m["net"] + ((t["odds"] - 1) * t["stake"] if t["passed"] else -t["stake"]), 2)
    if today in days:
        out["today"] = days[today]
    out["past"] = [d for k, d in sorted(days.items(), reverse=True) if k != today and d["trebles"]][:30]
    return out


def day_text(conn, day, name, local_time):
    """Ред за известие: надстройката за деня (сутрин) или колко тройки са минали (вечер)."""
    ensure(conn)
    m = conn.execute("SELECT legs_json, summary_json FROM columns_addon WHERE day = ? AND idx = 0", (day,)).fetchone()
    if not m:
        return None
    legs = json.loads(m["legs_json"])
    if not legs:
        return f"Надстройката днес няма - {json.loads(m['summary_json']).get('note')}."
    tr = conn.execute("SELECT passed, summary_json FROM columns_addon WHERE day = ? AND idx > 0", (day,)).fetchall()
    done = [t for t in tr if t["passed"] is not None]
    s0 = json.loads(m["summary_json"])
    stake = s0.get("stake", STAKE)
    n = len(tr)
    money = (f"залог по правилото: {n} {UNIT_PL} по {stake:.2f} € = {n * stake:.2f} €, до -30 € за месеца остават {s0.get('room', MONTH_CAP):.2f} €" if stake > 0
             else "ДНЕС БЕЗ ЗАЛОГ - месецът е на границата -30 €, записва се само за мерене")
    head = (f"НАДСТРОЙКАТА ЗА ДНЕС (извън правилата на майстора - решение на собственика; система {MEASURED['form']}: {n} {UNIT_PL} от тези {len(legs)} мача, "
            f"ако падне един мач, една {UNIT} пак минава; назад връща ~{MEASURED['return_best']:.2f} € от 1 € при най-добрия букмейкър, ~{MEASURED['return_avg']:.2f} при средния; "
            f"не е сигурен залог; {money}): ")
    body = "; ".join(f"{local_time(l['kickoff'])} {name(l['home'], l['away'])} {robot.label(l['sel'])} {l['p']:.0%} @{l['odds']:.2f}" for l in legs)
    if done:
        won = sum(t["passed"] for t in done)
        net = sum((json.loads(t["summary_json"])["odds"] - 1) * stake if t["passed"] else -stake for t in done)
        return head + body + f" | уредени {len(done)} от {n} {UNIT_PL}, минали {won}; по правилото: {net:+.2f} €"
    return head + body
