"""
Състави, контузени и наказани от API-Football (собственикът купи плана Pro на 2026-10-10: 7 500 заявки на ден; „да влиза в анализа и
в бонус анализа, по сегашната логика, като надстройка със съставите“).

ВАЖНО - това е ИНФОРМАЦИЯ. Моделът и шансовете на робота НЕ се менят от съставите (правилото на проекта и решението на собственика от
01.10): колко струва липсата на играч не е измерено назад. Показват се: кой отсъства (контузен, наказан, под въпрос), и когато се обявят -
началният състав със системата. Отговорът на „ИИ да прегледа мача със съставите“ е в чата: `run.py ask` ги добавя към анализа.

Как работи (всичко през един ключ, APIFOOTBALL_KEY в .env / тайна на GitHub):
  - map_fixtures: за днешния и утрешния ден (по UTC) ЕДНА заявка „всички мачове на деня“ и мачовете ни се свързват с тези на API-Football по
    първенство (LEAGUE_IDS), имената на отборите (teams.similar) и началния час (до 4 ч разлика); неоднозначното не се свързва;
  - refresh_injuries: контузени и наказани за мачовете до 30 часа напред - на 6 ч, а в последните 90 минути - на 90 минути;
  - refresh_lineups: в последните 90 минути преди мача на всеки 7 минути, докато се обяви съставът;
  - бюджет: в базата се брои на ден (meta apif:<ден>), най-много DAILY_CAP от 7 500 - запасът е за ръчни проби; под 800 останали (заглавката
    x-ratelimit-requests-remaining на API-Football) - спира всичко освен съставите на мачовете до 45 минути;
  - refresh_status (2026-10-11): статусът на мачовете, започнали преди над 3 часа и още без резултат - отложен (PST), спрян (SUSP), прекъснат (INT),
    прекратен (ABD), отменен (CANC) или пренасрочен (върнат на „не е започнал“ с по-късен час). Такъв мач иначе стои завинаги „чака резултат“ и държи
    колонката (Rayo - Athletic 10.10 беше INT при 0:0, Walsall - Crawley 03.10 - PST). Белегът е само допълнение във флаговете на прогнозата
    (bets/tips.py mark_status); записът, шансовете и правилата не се пипат.
Данните са в таблицата squads (един ред на мач); сайтът ги взима от там (bets/site.py), а наблюдателят на резултатите (bets/live.py) пише
по-често състави в squads/live.json - за минути след обявяването, преди часовото пускане.
"""

import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config, teams
from .leagues import LEAGUES

log = logging.getLogger(__name__)

BASE = "https://v3.football.api-sports.io/"
DAILY_CAP = 6500
LOW_REMAINING = 800
MAP_EVERY = timedelta(hours=3)
INJ_FAR, INJ_NEAR = timedelta(hours=6), timedelta(minutes=90)
INJ_AHEAD = timedelta(hours=30)
LINEUP_FROM, LINEUP_RETRY = timedelta(minutes=90), timedelta(minutes=7)
KICKOFF_TOLERANCE = timedelta(hours=4)
KEEP_LIVE = timedelta(days=2)

# статус на неуредени мачове (refresh_status)
STATUS_AFTER = timedelta(hours=3)                # мачът е започнал преди над 3 часа и още няма резултат
STATUS_WINDOW = timedelta(days=30)               # по-старите неуредени мачове се оставят за ръчен преглед
STATUS_EVERY_NEW, STATUS_EVERY_OLD = timedelta(hours=3), timedelta(hours=24)     # колко често се пита за един мач (по-нов / по-стар от 2 дни)
STATUS_MAP_EVERY = timedelta(hours=6)            # наново „всички мачове на деня“ за несвързаните
STATUS_MAX_REQUESTS = 12                         # най-много заявки за статус при едно пускане
STATUS_CAP = DAILY_CAP - 300                     # над това за деня статус не се пита - остава за съставите и контузените
STATUS_BATCH = 20                                # fixtures?ids= приема до 20 номера
STATUS_BG = {"PST": "отложен", "SUSP": "спрян", "INT": "прекъснат", "ABD": "прекратен", "CANC": "отменен"}

# наши кодове -> номер на първенството в API-Football (проверени срещу leagues?current=true на 2026-10-10)
LEAGUE_IDS = {
    "E0": 39, "E1": 40, "E2": 41, "E3": 42, "EC": 43, "SC0": 179, "SC1": 180, "SC2": 183, "SC3": 184,
    "D1": 78, "D2": 79, "D3": 80, "I1": 135, "I2": 136, "SP1": 140, "SP2": 141, "F1": 61, "F2": 62,
    "N1": 88, "B1": 144, "P1": 94, "T1": 203, "G1": 197, "AUT": 218, "BRA": 71, "BRA2": 72, "CHN": 169,
    "DNK": 119, "FIN": 244, "IRL": 357, "JPN": 98, "NOR": 103, "POL": 106, "ROU": 283, "RUS": 235,
    "SWE": 113, "SWZ": 207, "USA": 253, "MEX": 262, "ARG": 128, "CHI": 265, "AUS": 188, "SWE2": 114,
    "KOR": 292, "KSA": 307, "UCL": 2, "UEL": 3, "UECL": 848, "LIB": 13, "SUD": 11, "ENGC": 48, "FAC": 45,
    "DFB": 81, "CDR": 143, "CIT": 137, "CDF": 66, "UNL": 5, "WCQE": 32, "WCQS": 34, "EUQ": 960,
}
ID_TO_CODE = {v: k for k, v in LEAGUE_IDS.items()}

SCHEMA = """
CREATE TABLE IF NOT EXISTS squads (
    fixture_id   TEXT PRIMARY KEY,
    apif_id      INTEGER,
    apif_home    TEXT,
    apif_away    TEXT,
    injuries_json TEXT,
    injuries_at  TEXT,
    lineups_json TEXT,
    lineups_at   TEXT,
    checked_at   TEXT,
    mapped_at    TEXT
);
"""

_session = {"used": 0, "remaining": None}       # без база (наблюдателят): брои в паметта


def ensure(conn):
    conn.executescript(SCHEMA)
    if "status_at" not in {r[1] for r in conn.execute("PRAGMA table_info(squads)")}:
        conn.execute("ALTER TABLE squads ADD COLUMN status_at TEXT")          # кога за последно е питан статусът на мача (refresh_status)


def enabled():
    return bool(config.APIFOOTBALL_KEY)


def _context():
    """Сертификатите от certifi: Windows на лаптопа няма новия корен на Let's Encrypt (проверено 10.10); облакът е с актуални."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _day_key(now):
    return f"apif:{now.astimezone(timezone.utc).date().isoformat()}"


def used_today(conn, now=None):
    from . import db
    return int(db.get_meta(conn, _day_key(now or datetime.now(timezone.utc))) or 0)


def request(path, params=None, conn=None, now=None, essential=False):
    """Една заявка към API-Football. Връща списъка „response“ или None (без ключ, над бюджета, мрежова грешка). Никога не хвърля -
    грешката се лога и пускането продължава; ключът не се печата."""
    key = config.APIFOOTBALL_KEY
    if not key:
        return None
    now = now or datetime.now(timezone.utc)
    if conn is not None:
        used = used_today(conn, now)
        if used >= DAILY_CAP:
            log.warning("API-Football: дневният бюджет е изчерпан (%d)", used)
            return None
    elif _session["used"] >= 600:
        return None
    rem = _session["remaining"]
    if rem is not None and rem < LOW_REMAINING and not essential:
        return None
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"x-apisports-key": key})
    for attempt in (1, 2):                       # еднократен повторен опит при срив на сървъра (HTTP 5xx) или мрежата
        try:
            with urllib.request.urlopen(req, timeout=40, context=_context()) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                left = resp.headers.get("x-ratelimit-requests-remaining")
            break
        except (urllib.error.URLError, OSError, ValueError) as e:
            if attempt == 1 and not (isinstance(e, urllib.error.HTTPError) and e.code < 500):
                time.sleep(2)
                continue
            log.error("API-Football %s: %s", path, str(e).replace(key, "<КЛЮЧ>"))
            return None
    _session["used"] += 1
    if left is not None and left.isdigit():
        _session["remaining"] = int(left)
    if conn is not None:
        from . import db
        db.set_meta(conn, _day_key(now), str(used_today(conn, now) + 1))
        if left is not None:
            db.set_meta(conn, "apif_remaining", left)
    if body.get("errors"):
        log.error("API-Football %s: %s", path, str(body["errors"])[:200])
        return None
    return body.get("response") or []


# ---------- свързване на мачовете ----------

def _kick(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _best(f, api_fixtures):
    """Мачът на API-Football, който съответства на нашия (fixtures ред f), или None - само при единствен най-добър."""
    homes = [n for n in (f["home_src"], f["home"]) if n]
    aways = [n for n in (f["away_src"], f["away"]) if n]
    lid = LEAGUE_IDS.get(f["league"])
    ko = _kick(f["kickoff"])
    scored = []
    for a in api_fixtures:
        if a["league"]["id"] != lid or abs(_kick(a["fixture"]["date"]) - ko) > KICKOFF_TOLERANCE:
            continue
        sh = max(teams.similar(n, a["teams"]["home"]["name"]) for n in homes)
        sa = max(teams.similar(n, a["teams"]["away"]["name"]) for n in aways)
        if sh >= 0.5 and sa >= 0.5:
            scored.append((sh + sa, a))
    scored.sort(key=lambda x: -x[0])
    if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    if not scored:
        # единият отбор съвпада точно, другият е записан другояче (Hearts / Heart Of Midlothian, Brest / Stade Brestois 29):
        # единственият мач на лигата в рамките на 30 минути, в който участва точно този отбор
        exact = [a for a in api_fixtures if a["league"]["id"] == lid and abs(_kick(a["fixture"]["date"]) - ko) <= timedelta(minutes=30)
                 and (max(teams.similar(n, a["teams"]["home"]["name"]) for n in homes) >= 0.99
                      or max(teams.similar(n, a["teams"]["away"]["name"]) for n in aways) >= 0.99)]
        if len(exact) == 1:
            return exact[0]
    return None


def _by_elimination(left, api_fixtures, taken):
    """Останалите несвързани (преименувани отбори: Qingdao West Coast -> Qingdao Youth Island): един наш мач и един мач на API-Football
    в същото първенство в рамките на 5 минути, който още не е зает - те са един и същ мач. {id на наш мач: мач на API}."""
    out = {}
    for f in left:
        ko = _kick(f["kickoff"])
        peers = [g for g in left if g["league"] == f["league"] and abs(_kick(g["kickoff"]) - ko) <= timedelta(minutes=5)]
        free = [a for a in api_fixtures if a["league"]["id"] == LEAGUE_IDS.get(f["league"]) and a["fixture"]["id"] not in taken
                and abs(_kick(a["fixture"]["date"]) - ko) <= timedelta(minutes=5)]
        if len(peers) == 1 and len(free) == 1:
            out[f["id"]] = free[0]
    return out


def map_fixtures(conn, now=None):
    """Свързва предстоящите ни мачове (до 36 ч напред) с мачовете на API-Football: по една заявка на ден (UTC). Връща броя нови връзки."""
    from . import db
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    rows = conn.execute("SELECT * FROM fixtures WHERE kickoff > ? AND kickoff <= ? AND id NOT IN (SELECT fixture_id FROM squads WHERE apif_id IS NOT NULL)",
                        ((now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=36)).isoformat())).fetchall()
    rows = [r for r in rows if r["league"] in LEAGUE_IDS]
    by_day = {}
    for r in rows:
        by_day.setdefault(_kick(r["kickoff"]).astimezone(timezone.utc).date().isoformat(), []).append(r)
    new = 0
    for day, fs in sorted(by_day.items()):
        last = db.get_meta(conn, f"apif_map:{day}")
        if last and now - datetime.fromisoformat(last) < MAP_EVERY:
            continue
        api_fixtures = request("fixtures", {"date": day}, conn, now)
        if api_fixtures is None:
            continue
        db.set_meta(conn, f"apif_map:{day}", now.isoformat(timespec="seconds"))
        api_fixtures = [a for a in api_fixtures if a["league"]["id"] in ID_TO_CODE]
        matched = 0
        found = {}
        for f in fs:
            a = _best(f, api_fixtures)
            if a is not None:
                found[f["id"]] = a
        taken = {a["fixture"]["id"] for a in found.values()} | {r[0] for r in conn.execute("SELECT apif_id FROM squads WHERE apif_id IS NOT NULL")}
        found.update(_by_elimination([f for f in fs if f["id"] not in found], api_fixtures, taken))
        for f in fs:
            a = found.get(f["id"])
            if a is None:
                continue
            matched += 1
            conn.execute("INSERT INTO squads (fixture_id, apif_id, apif_home, apif_away, mapped_at) VALUES (?, ?, ?, ?, ?) "
                         "ON CONFLICT(fixture_id) DO UPDATE SET apif_id = excluded.apif_id, apif_home = excluded.apif_home, "
                         "apif_away = excluded.apif_away, mapped_at = excluded.mapped_at",
                         (f["id"], a["fixture"]["id"], a["teams"]["home"]["name"], a["teams"]["away"]["name"], now.isoformat(timespec="seconds")))
            new += 1
        log.info("API-Football %s: %d мача в нашите първенства, свързани %d от %d наши", day, len(api_fixtures), matched, len(fs))
    conn.commit()
    return new


# ---------- контузени и състави ----------

def _reason_bg(kind, reason):
    r = (reason or "").lower()
    if "suspen" in r or "red card" in r or "yellow card" in r:
        return "наказан"
    if any(w in r for w in ("illness", "flu", "virus", "covid", "sick")):
        return "болен"
    if any(w in r for w in ("injur", "knock", "muscle", "knee", "ankle", "hamstring", "thigh", "calf", "groin", "back", "foot", "hip", "surgery", "fracture", "strain", "sprain", "shoulder", "achilles", "concussion", "ligament", "tendon", "head",
                            "hernia", "health", "broken", "tear", "pain", "bruise", "problem", "rib", "toe", "leg", "arm", "wrist", "elbow", "neck")):
        return "контузия"
    if any(w in r for w in ("inactive", "missing fixture", "rest", "coach", "decision", "not in squad", "international", "national", "personal", "family")):
        return "не е в групата"
    return reason or ("под въпрос" if kind == "Questionable" else "отсъства")


def normalize_injuries(items):
    """[{team, player, type, reason}] -> {отбор: [[име, състояние, причина], ...]}; състояние: out (няма да играе) / doubt (под въпрос)."""
    out = {}
    seen = set()
    for x in items or []:
        p, t = x.get("player") or {}, (x.get("team") or {}).get("name")
        key = (t, p.get("name"))
        if not t or not p.get("name") or key in seen:
            continue
        seen.add(key)
        out.setdefault(t, []).append([p["name"], "doubt" if p.get("type") == "Questionable" else "out", _reason_bg(p.get("type"), p.get("reason"))])
    return out


def normalize_lineups(items):
    """[{team, formation, coach, startXI}] -> {отбор: {f, c, xi: [[номер, име, пост], ...]}}; празно, ако съставът още не е обявен."""
    out = {}
    for x in items or []:
        xi = [[(p.get("player") or {}).get("number"), (p.get("player") or {}).get("name"), (p.get("player") or {}).get("pos")]
              for p in x.get("startXI") or []]
        if len(xi) >= 11:
            out[(x.get("team") or {}).get("name")] = {"f": x.get("formation"), "c": (x.get("coach") or {}).get("name"), "xi": xi}
    return out


def fetch_injuries(apif_id, conn=None, now=None):
    r = request("injuries", {"fixture": apif_id}, conn, now)
    return None if r is None else normalize_injuries(r)


def fetch_lineups(apif_id, conn=None, now=None, essential=True):
    r = request("fixtures/lineups", {"fixture": apif_id}, conn, now, essential=essential)
    return None if r is None else normalize_lineups(r)


def refresh_injuries(conn, now=None, limit=150):
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    n = 0
    rows = conn.execute("SELECT s.fixture_id, s.apif_id, s.injuries_at, f.kickoff FROM squads s JOIN fixtures f ON f.id = s.fixture_id "
                        "WHERE s.apif_id IS NOT NULL AND f.kickoff > ? AND f.kickoff <= ? ORDER BY f.kickoff",
                        (now.isoformat(), (now + INJ_AHEAD).isoformat())).fetchall()
    for r in rows:
        if n >= limit:
            break
        near = _kick(r["kickoff"]) - now <= timedelta(hours=3)
        every = INJ_NEAR if near else INJ_FAR
        if r["injuries_at"] and now - datetime.fromisoformat(r["injuries_at"]) < every:
            continue
        inj = fetch_injuries(r["apif_id"], conn, now)
        if inj is None:
            continue
        conn.execute("UPDATE squads SET injuries_json = ?, injuries_at = ? WHERE fixture_id = ?",
                     (json.dumps(inj, ensure_ascii=False), now.isoformat(timespec="seconds"), r["fixture_id"]))
        n += 1
    conn.commit()
    return n


def refresh_lineups(conn, now=None, limit=120):
    """Съставите на мачовете, които започват в следващите LINEUP_FROM минути (или са започнали преди по-малко от 5), още необявени."""
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    n = 0
    rows = conn.execute("SELECT s.fixture_id, s.apif_id, s.checked_at, f.kickoff FROM squads s JOIN fixtures f ON f.id = s.fixture_id "
                        "WHERE s.apif_id IS NOT NULL AND s.lineups_json IS NULL AND f.kickoff > ? AND f.kickoff <= ? ORDER BY f.kickoff",
                        ((now - timedelta(minutes=5)).isoformat(), (now + LINEUP_FROM).isoformat())).fetchall()
    for r in rows:
        if n >= limit:
            break
        if r["checked_at"] and now - datetime.fromisoformat(r["checked_at"]) < LINEUP_RETRY:
            continue
        lu = fetch_lineups(r["apif_id"], conn, now, essential=_kick(r["kickoff"]) - now <= timedelta(minutes=45))
        if lu is None:
            continue
        conn.execute("UPDATE squads SET checked_at = ?, lineups_json = ?, lineups_at = ? WHERE fixture_id = ?",
                     (now.isoformat(timespec="seconds"), json.dumps(lu, ensure_ascii=False) if lu else None,
                      now.isoformat(timespec="seconds") if lu else None, r["fixture_id"]))
        n += 1
    conn.commit()
    return n


def refresh(conn, now=None):
    """Стъпката на облака (след прогнозите, преди сайта): свързване, контузени, състави. Без ключ - нищо (не е грешка)."""
    if not enabled():
        log.info("API-Football: няма ключ - състави и отсъствия не се теглят")
        return 0
    now = now or datetime.now(timezone.utc)
    m = map_fixtures(conn, now)
    i = refresh_injuries(conn, now)
    l = refresh_lineups(conn, now)
    log.info("API-Football: нови връзки %d, контузени обновени %d, състави проверени %d; заявки днес %d", m, i, l, used_today(conn, now))
    return m + i + l


# ---------- статус на неуредени мачове (отложен / прекъснат / ...) ----------

def match_state(a, our_kickoff, now):
    """(код, етикет или None, нов начален час или None) за мача a на API-Football. Етикет има само за мач, който не се е играл както е
    уговорено: PST, SUSP, INT, ABD, CANC, или „не е започнал“ с начален час по-късно от сега (пренасрочен). Нищо друго не се брои."""
    fx = a.get("fixture") or {}
    code = (fx.get("status") or {}).get("short")
    moved = None
    if fx.get("date") and _kick(fx["date"]) > now and _kick(fx["date"]) - _kick(our_kickoff) > KICKOFF_TOLERANCE:
        moved = fx["date"]
    if code in STATUS_BG:
        return code, STATUS_BG[code], moved
    if code in ("NS", "TBD") and moved:
        return code, "пренасрочен", moved
    return code, None, None


def _status_candidates(conn, now):
    """Записаните прогнози без резултат, започнали преди над STATUS_AFTER (и не по-стари от STATUS_WINDOW), в първенства на API-Football."""
    rows = conn.execute(
        """SELECT t.fixture_id, t.league, t.kickoff, t.home, t.away, f.home_src, f.away_src, s.apif_id, s.status_at
             FROM tips t LEFT JOIN fixtures f ON f.id = t.fixture_id LEFT JOIN squads s ON s.fixture_id = t.fixture_id
            WHERE t.hg IS NULL AND t.kickoff < ? AND t.kickoff >= ? ORDER BY t.kickoff""",
        ((now - STATUS_AFTER).isoformat(), (now - STATUS_WINDOW).isoformat())).fetchall()
    return [dict(r) for r in rows if r["league"] in LEAGUE_IDS]


def _status_due(r, now):
    if not r["status_at"]:
        return True
    every = STATUS_EVERY_NEW if now - _kick(r["kickoff"]) < timedelta(days=2) else STATUS_EVERY_OLD
    return now - datetime.fromisoformat(r["status_at"]) >= every


def refresh_status(conn, now=None):
    """Статусът на мачовете, започнали преди над 3 часа и още без резултат: свързаните (таблица squads) - по номер, до 20 в заявка; още
    несвързаните (старите мачове, записани преди API-Football) - с една заявка „всички мачове на деня“, с което се свързват както в
    map_fixtures. Резултатът се записва като допълнение във флаговете на прогнозата (tips.mark_status): записът не се пипа.
    Бюджет: най-много STATUS_MAX_REQUESTS заявки на пускане, нищо над STATUS_CAP за деня; един мач се пита на 3 часа (на 24 - след 2 дни).
    Връща броя мачове с белег (отложен/прекъснат/...). Без ключ - нищо (не е грешка)."""
    from . import tips
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    seen, asked = {}, 0
    cands = _status_candidates(conn, now) if enabled() else []
    if not enabled():
        log.info("API-Football: няма ключ - статусът на неуредените мачове не се проверява")
    due = [r for r in cands if _status_due(r, now)]

    def can_ask():
        if asked >= STATUS_MAX_REQUESTS:
            return False
        if used_today(conn, now) >= STATUS_CAP:
            log.warning("API-Football: над %d заявки днес - статусът на неуредените мачове чака до утре", STATUS_CAP)
            return False
        return True

    # 1. свързаните - по номер
    linked = [r for r in due if r["apif_id"]]
    for i in range(0, len(linked), STATUS_BATCH):
        if not can_ask():
            break
        chunk = linked[i:i + STATUS_BATCH]
        api = request("fixtures", {"ids": "-".join(str(r["apif_id"]) for r in chunk)}, conn, now)
        asked += 1
        if api is None:
            break                                          # мрежа / бюджет - следващото пускане
        by_id = {a["fixture"]["id"]: a for a in api}
        for r in chunk:
            conn.execute("UPDATE squads SET status_at = ? WHERE fixture_id = ?", (stamp, r["fixture_id"]))
            a = by_id.get(r["apif_id"])
            if a is not None:
                seen[r["fixture_id"]] = match_state(a, r["kickoff"], now)
            else:
                log.warning("API-Football: мач %s не е върнат за статус (номер %s)", r["fixture_id"], r["apif_id"])
    # 2. несвързаните - по ден (UTC): свързват се с мачовете на деня
    by_day = {}
    for r in due:
        if not r["apif_id"]:
            by_day.setdefault(_kick(r["kickoff"]).astimezone(timezone.utc).date().isoformat(), []).append(r)
    for day, rs in sorted(by_day.items(), reverse=True):
        if not can_ask():
            break
        api = request("fixtures", {"date": day}, conn, now)
        asked += 1
        if api is None:
            break
        api = [a for a in api if a["league"]["id"] in ID_TO_CODE]
        taken = {x[0] for x in conn.execute("SELECT apif_id FROM squads WHERE apif_id IS NOT NULL")}
        for r in rs:
            a = _best(r, api)
            if a is None or a["fixture"]["id"] in taken:
                # не е намерен: пазим кога е питан (ред без номер), за да не се пита на всеки час
                conn.execute("INSERT INTO squads (fixture_id, status_at) VALUES (?, ?) ON CONFLICT(fixture_id) DO UPDATE SET status_at = excluded.status_at",
                             (r["fixture_id"], stamp))
                continue
            taken.add(a["fixture"]["id"])
            conn.execute("INSERT INTO squads (fixture_id, apif_id, apif_home, apif_away, mapped_at, status_at) VALUES (?, ?, ?, ?, ?, ?) "
                         "ON CONFLICT(fixture_id) DO UPDATE SET apif_id = excluded.apif_id, apif_home = excluded.apif_home, "
                         "apif_away = excluded.apif_away, mapped_at = excluded.mapped_at, status_at = excluded.status_at",
                         (r["fixture_id"], a["fixture"]["id"], a["teams"]["home"]["name"], a["teams"]["away"]["name"], stamp, stamp))
            seen[r["fixture_id"]] = match_state(a, r["kickoff"], now)
    conn.commit()
    flagged = tips.mark_status(conn, seen, now)
    log.info("API-Football: статус на неуредени мачове - за проверка %d от %d, получени %d, заявки %d (днес общо %d); с белег отложен/прекъснат: %d",
             len(due), len(cands), len(seen), asked, used_today(conn, now), flagged)
    return flagged


# ---------- за сайта и известията ----------

def _side(name_map, apif_home, apif_away):
    return {"h": name_map.get(apif_home), "a": name_map.get(apif_away)}


def load(conn, ids):
    """{fixture_id: компактен вид за сайта}: i - отсъстващи {h, a: [[име, out/doubt, причина]]}, ia - кога; l - състав {h, a: {f, c, xi}}, la - кога."""
    ensure(conn)
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        for r in conn.execute(f"SELECT * FROM squads WHERE fixture_id IN ({','.join('?' * len(chunk))}) AND apif_id IS NOT NULL", chunk):
            item = {"n": r["apif_home"], "m": r["apif_away"]}
            if r["injuries_json"] is not None:
                item["i"] = _side(json.loads(r["injuries_json"]), r["apif_home"], r["apif_away"])
                item["ia"] = r["injuries_at"]
            if r["lineups_json"]:
                item["l"] = _side(json.loads(r["lineups_json"]), r["apif_home"], r["apif_away"])
                item["la"] = r["lineups_at"]
            if len(item) > 2:
                out[r["fixture_id"]] = item
    return out


def describe(sq, home, away):
    """Кратък текст за известие/чат: отсъстващи и съставът (ако е обявен)."""
    if not sq:
        return []
    lines = []
    for side, name in (("h", home), ("a", away)):
        inj = ((sq.get("i") or {}).get(side)) or []
        if inj:
            lines.append(f"{name} - отсъстват: " + ", ".join(f"{p} ({r}{', под въпрос' if k == 'doubt' else ''})" for p, k, r in inj))
    lu = sq.get("l") or {}
    for side, name in (("h", home), ("a", away)):
        x = lu.get(side)
        if x:
            lines.append(f"{name} - състав {x.get('f') or ''}: " + ", ".join(str(p[1]) for p in x["xi"]))
    return lines


# ---------- един мач на живо (за „питай робота“ и бонус анализа) ----------

def squads_for(conn, fx, now=None, fresh=True):
    """Отсъстващи и състав за един мач (fixtures ред или речник): свързва го при нужда (1 заявка за деня), тегли контузените и съставите
    (до 3 заявки) и ги записва в squads. Връща компактния вид или None (няма ключ / не е свързан)."""
    if not enabled():
        return None
    ensure(conn)
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT * FROM squads WHERE fixture_id = ?", (fx["id"],)).fetchone()
    if row is None or row["apif_id"] is None:
        day = _kick(fx["kickoff"]).astimezone(timezone.utc).date().isoformat()
        fixtures = request("fixtures", {"date": day}, conn, now, essential=True)
        a = _best(fx, [x for x in (fixtures or []) if x["league"]["id"] in ID_TO_CODE])
        if a is None:
            return None
        conn.execute("INSERT INTO squads (fixture_id, apif_id, apif_home, apif_away, mapped_at) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(fixture_id) DO UPDATE SET apif_id = excluded.apif_id, apif_home = excluded.apif_home, apif_away = excluded.apif_away",
                     (fx["id"], a["fixture"]["id"], a["teams"]["home"]["name"], a["teams"]["away"]["name"], now.isoformat(timespec="seconds")))
        conn.commit()
        row = conn.execute("SELECT * FROM squads WHERE fixture_id = ?", (fx["id"],)).fetchone()
    if fresh:
        inj = fetch_injuries(row["apif_id"], conn, now)
        lu = fetch_lineups(row["apif_id"], conn, now)
        if inj is not None:
            conn.execute("UPDATE squads SET injuries_json = ?, injuries_at = ? WHERE fixture_id = ?",
                         (json.dumps(inj, ensure_ascii=False), now.isoformat(timespec="seconds"), fx["id"]))
        if lu:
            conn.execute("UPDATE squads SET lineups_json = ?, lineups_at = ?, checked_at = ? WHERE fixture_id = ?",
                         (json.dumps(lu, ensure_ascii=False), now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), fx["id"]))
        conn.commit()
    return load(conn, [fx["id"]]).get(fx["id"])


# ---------- наблюдателят (без запис в базата): състави на минути след обявяването ----------

def live_file():
    return config.SITE_DIR / "squads" / "live.json"


def load_live():
    path = live_file()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def live_round(now, last_try):
    """Една обиколка на наблюдателя: за мачовете до 90 минути напред без състав в базата и в squads/live.json - заявка на 7 минути на мач.
    Връща броя новооткрити състави; записва squads/live.json (качването го прави bets/live.py)."""
    import sqlite3
    if not enabled():
        return 0
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT s.fixture_id, s.apif_id, s.apif_home, s.apif_away, f.kickoff FROM squads s JOIN fixtures f ON f.id = s.fixture_id "
                            "WHERE s.apif_id IS NOT NULL AND s.lineups_json IS NULL AND f.kickoff > ? AND f.kickoff <= ?",
                            ((now - timedelta(minutes=5)).isoformat(), (now + LINEUP_FROM).isoformat())).fetchall()
    except sqlite3.OperationalError:
        return 0                                       # таблицата още я няма (преди първото пускане с API-Football)
    finally:
        conn.close()
    data = load_live()
    cut = (now - KEEP_LIVE).isoformat()
    data = {k: v for k, v in data.items() if v.get("k", "") >= cut}
    found = 0
    for r in rows:
        if r["fixture_id"] in data or now - last_try.get(r["fixture_id"], now - timedelta(hours=1)) < LINEUP_RETRY:
            continue
        last_try[r["fixture_id"]] = now
        lu = fetch_lineups(r["apif_id"], None, now, essential=_kick(r["kickoff"]) - now <= timedelta(minutes=45))
        if lu:
            data[r["fixture_id"]] = {"l": _side(lu, r["apif_home"], r["apif_away"]), "la": now.isoformat(timespec="seconds"), "k": r["kickoff"]}
            found += 1
            log.info("Състав обявен: %s - %s", r["apif_home"], r["apif_away"])
    if found:
        live_file().parent.mkdir(exist_ok=True)
        live_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return found
