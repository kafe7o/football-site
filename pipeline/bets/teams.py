"""
Имената на отборите в odds API и в football-data не съвпадат: "Manchester United" срещу
"Man United", "Inter Milan" срещу "Inter". Тук е превеждането, на едно място.

Правилото е строго нарочно: по-добре липсваща прогноза, отколкото прогноза за грешен отбор.
Затова подобието трябва да е поне CUTOFF, а всяко разминаване се лога. Старият код ползваше
праг 0.45 и връщаше първия отбор с обща първа дума - така "Real Madrid" можеше да стане
"Real Sociedad".
"""

import difflib
import logging

log = logging.getLogger(__name__)

CUTOFF = 0.82

ALIASES = {
    "Wolverhampton Wanderers": "Wolves", "Brighton and Hove Albion": "Brighton",
    "Manchester United": "Man United", "Manchester City": "Man City",
    "Tottenham Hotspur": "Tottenham", "West Ham United": "West Ham",
    "Nottingham Forest": "Nott'm Forest", "Newcastle United": "Newcastle",
    "Leeds United": "Leeds", "Leicester City": "Leicester",
    "Sheffield United": "Sheffield United", "Ipswich Town": "Ipswich",
    "Athletic Bilbao": "Ath Bilbao", "Atletico Madrid": "Ath Madrid",
    "Atlético Madrid": "Ath Madrid", "Celta Vigo": "Celta", "Real Betis": "Betis",
    "Real Sociedad": "Sociedad", "Rayo Vallecano": "Vallecano", "Espanyol": "Espanol",
    "Deportivo Alaves": "Alaves", "Real Valladolid": "Valladolid",
    "Inter Milan": "Inter", "AC Milan": "Milan", "AS Roma": "Roma",
    "Hellas Verona": "Verona", "Atalanta BC": "Atalanta", "SSC Napoli": "Napoli",
    "Borussia Dortmund": "Dortmund", "Bayer Leverkusen": "Leverkusen",
    "Borussia Monchengladbach": "M'gladbach", "Bayern Munich": "Bayern Munich",
    "Eintracht Frankfurt": "Ein Frankfurt", "FC Koln": "FC Koln", "1. FC Köln": "FC Koln",
    "VfB Stuttgart": "Stuttgart", "Werder Bremen": "Werder Bremen",
    "Paris Saint Germain": "Paris SG", "Paris Saint-Germain": "Paris SG",
    "Olympique Marseille": "Marseille", "Olympique Lyonnais": "Lyon",
    "AS Monaco": "Monaco", "Stade Rennais": "Rennes", "RC Lens": "Lens",
    "Lille OSC": "Lille", "OGC Nice": "Nice",
    # Втора английска дивизия - имената там се разминават най-често
    "Queens Park Rangers": "QPR", "Sheffield Wednesday": "Sheffield Weds",
    "West Bromwich Albion": "West Brom", "Blackburn Rovers": "Blackburn",
    "Bolton Wanderers": "Bolton", "Derby County": "Derby", "Preston North End": "Preston",
    "Cardiff City": "Cardiff", "Swansea City": "Swansea", "Norwich City": "Norwich",
    "Hull City": "Hull", "Stoke City": "Stoke", "Coventry City": "Coventry",
    "Birmingham City": "Birmingham", "Bristol City": "Bristol City",
    "Charlton Athletic": "Charlton", "Oxford United": "Oxford", "Luton Town": "Luton",
    "Plymouth Argyle": "Plymouth", "Portsmouth FC": "Portsmouth",
}

# Наставки, които едната страна пише, а другата - не.
SUFFIXES = (" City", " Athletic", " Albion", " Wanderers", " Rangers", " Town",
            " County", " Rovers", " Hotspur", " United", " FC", " CF", " AFC")


def match(name, candidates):
    """Името на отбора, както е в базата, или None когато не е достатъчно сигурно."""
    if not name or not candidates:
        return None
    if name in candidates:
        return name
    alias = ALIASES.get(name)
    if alias and alias in candidates:
        return alias
    close = difflib.get_close_matches(name, list(candidates), n=1, cutoff=CUTOFF)
    if close:
        return close[0]
    # Последен опит: без представки и наставки, които често се различават
    stripped = name
    for word in ("FC ", "AC ", "AS ", "SSC ", "RC ", "SC ", "CF ", "OGC ", "OSC "):
        stripped = stripped.replace(word, " ")
    for suffix in SUFFIXES:
        if stripped.endswith(suffix) and len(stripped) > len(suffix) + 3:
            stripped = stripped[:-len(suffix)]
            break
    stripped = " ".join(stripped.split())
    if stripped in candidates:
        return stripped
    close = difflib.get_close_matches(stripped, list(candidates), n=1, cutoff=CUTOFF)
    if close:
        return close[0]
    log.info("непознат отбор: %r", name)
    return None
