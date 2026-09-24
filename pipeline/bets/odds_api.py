"""
the-odds-api.com - живи коефициенти и резултати за всички спортове.

Квотата е малка (500 заявки месечно на безплатния план), затова тук има три предпазителя:
  - ЕДИН регион на заявка: цената е [пазари] x [региони] кредита;
  - кеш на диска, за да не се плаща два пъти за едно и също в рамките на един цикъл;
  - remaining() чете остатъка от заглавките и се лога след всяка заявка.

Ключът не влиза в съобщенията за грешка: requests и urllib слагат целия URL в тях, а
логът се чете от хора и се копира в чатове.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger(__name__)

BASE = "https://api.the-odds-api.com/v4"
CACHE_MINUTES = 180
SCORES_CACHE_MINUTES = 120
_last_remaining = None


def redact(text):
    key = config.ODDS_API_KEY
    return text.replace(key, "<ODDS_API_KEY>") if key else text


def _cache(name):
    config.CACHE_DIR.mkdir(exist_ok=True)
    return config.CACHE_DIR / f"{name}.json"


def _get(path, params, cache_name=None, cache_minutes=0):
    global _last_remaining
    if cache_name and cache_minutes:
        path_cache = _cache(cache_name)
        if path_cache.exists():
            age = (time.time() - path_cache.stat().st_mtime) / 60
            if age < cache_minutes:
                log.info("%s: от кеша (%.0f мин.)", cache_name, age)
                return json.loads(path_cache.read_text(encoding="utf-8"))

    key = config.require("ODDS_API_KEY")
    url = f"{BASE}{path}?{urllib.parse.urlencode({'apiKey': key, **params})}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
            _last_remaining = resp.headers.get("x-requests-remaining")
    except urllib.error.HTTPError as e:
        raise RuntimeError(redact(f"{path}: API върна {e.code} - {e.read()[:200].decode('utf-8', 'replace')}")) from None
    except OSError as e:
        raise RuntimeError(redact(f"{path}: няма връзка ({e})")) from None
    if cache_name:
        _cache(cache_name).write_text(json.dumps(data), encoding="utf-8")
    return data


def remaining():
    """Колко заявки остават този месец. Заявката за списъка със спортове е безплатна."""
    _get("/sports/", {})
    return int(_last_remaining) if _last_remaining is not None else None


def sports():
    return _get("/sports/", {})


def odds(sport, regions="eu", cache_minutes=CACHE_MINUTES):
    """Коефициенти 1/X/2 за предстоящите мачове на лигата. 1 кредит на регион."""
    data = _get(f"/sports/{sport}/odds/", {"regions": regions, "markets": "h2h",
                                           "oddsFormat": "decimal"},
                cache_name=f"odds_{sport}_{regions}", cache_minutes=cache_minutes)
    log.info("%s (%s): %d мача, остават %s кредита", sport, regions, len(data), _last_remaining)
    return data


def scores(sport, days_from=3, cache_minutes=SCORES_CACHE_MINUTES):
    """Приключилите събития с резултат. 2 кредита, когато се иска история."""
    data = _get(f"/sports/{sport}/scores/", {"daysFrom": days_from},
                cache_name=f"scores_{sport}_{days_from}", cache_minutes=cache_minutes)
    out = {}
    for event in data:
        if not event.get("completed") or not event.get("scores"):
            continue
        by_name = {s["name"]: s["score"] for s in event["scores"]}
        home, away = event.get("home_team"), event.get("away_team")
        if home not in by_name or away not in by_name:
            log.warning("%s: резултат без имена на отборите (%s)", sport, event.get("id"))
            continue
        try:
            out[event["id"]] = {"home": float(by_name[home]), "away": float(by_name[away])}
        except (TypeError, ValueError):
            log.warning("%s: нечислов резултат за %s", sport, event.get("id"))
    log.info("%s: %d приключили събития с резултат", sport, len(out))
    return out


def outcome_index(score, three_way):
    """0 домакин, 1 равен, 2 гост. При спорт без равен: 0 или 1."""
    if score["home"] > score["away"]:
        return 0
    if score["home"] < score["away"]:
        return 2 if three_way else 1
    return 1 if three_way else None
