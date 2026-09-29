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
    "Hellas Verona": "Verona", "FSV Mainz 05": "Mainz", "1. FSV Mainz 05": "Mainz", "Atalanta BC": "Atalanta", "SSC Napoli": "Napoli",
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
    # имената от odds API на отборите от дербитата (bets/derbies.py) - там сравнението е строго
    "Sporting Gijón": "Sp Gijon", "Bristol Rovers": "Bristol Rvs", "Heart of Midlothian": "Hearts",
    "Hamburger SV": "Hamburg", "FC St. Pauli": "St Pauli", "Hertha Berlin": "Hertha",
    "Olympiacos": "Olympiakos", "Olympiacos Piraeus": "Olympiakos", "AEK Athens": "AEK",
    "Sporting Lisbon": "Sp Lisbon", "Sporting CP": "Sp Lisbon", "Standard Liège": "Standard",
    "Royal Antwerp": "Antwerp", "Saint-Etienne": "St Etienne", "AS Saint-Étienne": "St Etienne",
    "Aris Thessaloniki": "Aris", "PAOK Thessaloniki": "PAOK", "Panathinaikos FC": "Panathinaikos",
    "Racing Santander": "Santander", "Real Racing Club de Santander": "Santander",
    "Olympique Marseille": "Marseille", "Dundee FC": "Dundee", "Cercle Brugge KSV": "Cercle Brugge",
    "Real Oviedo": "Oviedo", "Real Zaragoza": "Zaragoza", "Deportivo La Coruña": "La Coruna",
    "Deportivo La Coruna": "La Coruna", "SS Lazio": "Lazio", "Genoa CFC": "Genoa",
    "Newcastle United": "Newcastle", "Sunderland AFC": "Sunderland", "Norwich City FC": "Norwich",
    # Аржентина: две "Estudiantes" в лигата - odds API пише само "Estudiantes" за този от Ла Плата
    "Estudiantes": "Estudiantes L.P.", "Gimnasia La Plata": "Gimnasia L.P.",
    # Бразилия, Португалия, Швейцария, Турция, САЩ - имената на football-data
    "Atletico Mineiro": "Atletico-MG", "Atlético Mineiro": "Atletico-MG",
    "Atletico Paranaense": "Athletico-PR", "Athletico Paranaense": "Athletico-PR",
    "Atletico Goianiense": "Atletico GO", "Bragantino-SP": "Bragantino", "Red Bull Bragantino": "Bragantino",
    "Chapecoense": "Chapecoense-SC", "Vitória SC": "Guimaraes", "Vitoria SC": "Guimaraes",
    "Vitória Guimarães": "Guimaraes", "Grasshopper Zürich": "Grasshoppers", "Grasshopper Zurich": "Grasshoppers",
    "Basaksehir": "Buyuksehyr", "Istanbul Basaksehir": "Buyuksehyr", "İstanbul Başakşehir": "Buyuksehyr",
    "LA Galaxy": "Los Angeles Galaxy", "Los Angeles FC": "Los Angeles FC", "LAFC": "Los Angeles FC",
    "Union Saint-Gilloise": "St. Gilloise", "Royale Union Saint-Gilloise": "St. Gilloise",
    "Cracovia Kraków": "Cracovia", "Cracovia Krakow": "Cracovia",
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


# --- Съвпадение на цял мач, не на един отбор ---
# Когато кандидатите са само мачовете на една лига в един ден (до ~12), а всеки отбор играе
# най-много веднъж на ден, стига и свободно сравнение по думи: "AD Ceuta FC" ~ "Ceuta",
# "Stockport County FC" ~ "Stockport". Общите думи ("Real", "United", "City") не се броят -
# иначе "Real Betis" ставаше "Real Sociedad".
FILLER = {"fc", "cf", "ad", "sd", "cd", "ud", "afc", "sc", "ac", "ce", "club", "de", "the"}
GENERIC = {"real", "united", "city", "town", "county", "athletic", "atletico", "sporting",
           "deportivo", "racing", "rovers", "wanderers", "albion", "stanley", "alexandra"}


# Букви, които NFKD не разлага и иначе изчезват: "Wisła Płock" ставаше "wisa pock".
PLAIN = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ß": "ss",
                       "đ": "d", "Đ": "D", "ı": "i", "ð": "d", "þ": "th", "œ": "oe"})


def _tokens(name):
    import unicodedata
    # точките махнати: "F.C." -> "FC" (служебна дума), "St. Louis" -> "St Louis"
    plain = unicodedata.normalize("NFKD", name.translate(PLAIN).replace(".", "")).encode("ascii", "ignore").decode()
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in plain.lower()).split()
             if w not in FILLER]
    return [w for w in words if w not in GENERIC] or words


def similar(a, b):
    """Дял на общите думи (или начала на думи с 3+ букви: "peterboro" ~ "peterborough")
    от ПО-ДЪЛГОТО име. От по-краткото не става: "Sheffield United" без общата дума е само
    "sheffield" и пасваше на всеки отбор от Шефилд."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    hits = sum(1 for w in short if any(_token_eq(w, v) for v in long_))
    return hits / len(long_)


def _token_eq(w, v):
    """Една и съща дума: еднаква или едната е начало на другата (поне 3 букви). Инициали
    ("L.P.") не се тълкуват - "c" пасваше на "Celtic"; за тях има псевдоними."""
    return w == v or (min(len(w), len(v)) >= 3 and (v.startswith(w) or w.startswith(v)))


def contained(a, b):
    """Каква част от думите на по-краткото име се намират в по-дългото."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return sum(1 for w in short if any(_token_eq(w, v) for v in long_)) / len(short)


def best_match(name, candidates):
    """Отбор от списъка по думи, когато точното име, псевдонимът и близкото изписване не стигат.
    Всички думи на по-краткото име трябва да са в другото ("Instituto de Córdoba" ~ "Instituto"),
    а кандидатът да е единственият най-добър по (подобие, покритие) - иначе None:
    "Estudiantes" пасва еднакво на "Estudiantes L.P." и "Estudiantes Rio Cuarto"."""
    scored = sorted(((similar(name, c), contained(name, c), c) for c in candidates), reverse=True)
    scored = [x for x in scored if x[1] == 1.0 and x[0] >= 0.25]
    if not scored:
        return None
    if len(scored) > 1 and scored[1][:2] == scored[0][:2]:
        return None
    return scored[0][2]


def _same(name, candidate):
    """Точното сравнение на match(), без да пише в лога за всеки неуспешен кандидат."""
    return (name == candidate or ALIASES.get(name) == candidate
            or difflib.SequenceMatcher(None, name, candidate).ratio() >= CUTOFF)


def match_fixture(home, away, fixtures, key=lambda f: (f["home_team"], f["away_team"])):
    """Мачът от fixtures, на който съответстват home и away, или None.

    Първо точно име или псевдоним (match). Иначе и двата отбора трябва да съвпаднат поне
    наполовина, мачът да е единственият най-добър и да НЕ пасва също толкова добре
    обърнат (дерби с разменени имена се пропуска).
    """
    scored = []
    for f in fixtures:
        fh, fa = key(f)
        if _same(home, fh) and _same(away, fa):
            return f
        sh, sa = similar(home, fh), similar(away, fa)
        if sh >= 0.5 and sa >= 0.5 and sh + sa > similar(home, fa) + similar(away, fh):
            scored.append((sh + sa, f))
    scored.sort(key=lambda x: -x[0])
    if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


def loose_match(name, candidates):
    """Последен опит в рамките на ЕДНА лига: името с най-много общи думи (similar), ако е
    поне наполовина и е единственото най-добро. "Real Valladolid CF" -> "Valladolid",
    "Sporting Gijón" -> "Sp Gijon"; "Sheffield Wednesday" при двата отбора от Шефилд -
    нищо, защото са равни. За прегледа напред, където мачът още не е в базата."""
    scored = sorted(((similar(name, c), c) for c in candidates), reverse=True)
    if not scored or scored[0][0] < 0.5:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]
