"""
Дербитата: в тях не се залага (съвет на професионален залагач, предаден от собственика на
2026-09-28, проверен на данни).

Проверка на 94 188 мача от 18-те лиги (2012-2026), коефициентите преди мача:
  дербита (1465):     фаворитът печели 48.8% при обещани 50.3%; равни 28.3% при обещани 26.0%
  останалите (92657): фаворитът печели 50.5% при обещани 50.5%; равни 26.5% при обещани 26.2%
Дербитата са по-непредсказуеми от коефициентите си - основно повече равни. Ефектът е около
2 пп, не "пълна лотария", но стига, за да не се залага в тях.

Имената са като във football-data; имената от odds API се свързват с teams.match_fixture.
"""

from . import teams

# (лига, отбор, отбор) - и двете посоки са дерби
DERBIES = [
    ("E0", "Man United", "Man City"), ("E0", "Liverpool", "Everton"), ("E0", "Arsenal", "Tottenham"),
    ("E0", "Chelsea", "Tottenham"), ("E0", "Arsenal", "Chelsea"), ("E0", "Newcastle", "Sunderland"),
    ("E0", "West Brom", "Wolves"), ("E0", "Liverpool", "Man United"), ("E0", "Chelsea", "Fulham"),
    ("E0", "Aston Villa", "Birmingham"), ("E0", "Crystal Palace", "Brighton"),
    ("E1", "Sheffield United", "Sheffield Weds"), ("E1", "Nott'm Forest", "Derby"),
    ("E1", "Portsmouth", "Southampton"), ("E1", "Ipswich", "Norwich"), ("E1", "Cardiff", "Swansea"),
    ("E1", "West Brom", "Wolves"), ("E1", "Birmingham", "Aston Villa"), ("E1", "Leeds", "Millwall"),
    ("E1", "Bristol City", "Bristol Rvs"), ("E1", "Stoke", "Port Vale"),
    ("E2", "Sheffield United", "Sheffield Weds"), ("E2", "Bristol City", "Bristol Rvs"),
    ("E2", "Portsmouth", "Southampton"), ("E2", "Stoke", "Port Vale"),
    ("E3", "Bristol Rvs", "Bristol City"), ("E3", "Stoke", "Port Vale"),
    ("SC0", "Celtic", "Rangers"), ("SC0", "Hearts", "Hibernian"), ("SC0", "Dundee", "Dundee United"),
    ("SC0", "Aberdeen", "Rangers"),
    ("SP1", "Real Madrid", "Ath Madrid"), ("SP1", "Barcelona", "Espanol"), ("SP1", "Sevilla", "Betis"),
    ("SP1", "Ath Bilbao", "Sociedad"), ("SP1", "Valencia", "Levante"), ("SP1", "Celta", "La Coruna"),
    ("SP1", "Real Madrid", "Barcelona"), ("SP1", "Getafe", "Leganes"), ("SP1", "Villarreal", "Valencia"),
    ("SP2", "Oviedo", "Sp Gijon"), ("SP2", "Zaragoza", "Huesca"), ("SP2", "Santander", "Sp Gijon"), ("SP1", "Santander", "Sp Gijon"),
    ("I1", "Inter", "Milan"), ("I1", "Roma", "Lazio"), ("I1", "Juventus", "Torino"),
    ("I1", "Genoa", "Sampdoria"), ("I1", "Juventus", "Inter"), ("I1", "Napoli", "Roma"),
    ("I2", "Genoa", "Sampdoria"), ("I2", "Bari", "Lecce"),
    ("D1", "Dortmund", "Schalke 04"), ("D1", "Hamburg", "St Pauli"), ("D1", "Bayern Munich", "Dortmund"),
    ("D1", "FC Koln", "M'gladbach"), ("D1", "Hertha", "Union Berlin"), ("D1", "Werder Bremen", "Hamburg"),
    ("D2", "Hamburg", "St Pauli"), ("D2", "Nurnberg", "Greuther Furth"), ("D2", "Schalke 04", "Bochum"),
    ("F1", "Paris SG", "Marseille"), ("F1", "Lyon", "St Etienne"), ("F1", "Nice", "Monaco"),
    ("F1", "Lens", "Lille"), ("F1", "Marseille", "Lyon"),
    ("N1", "Ajax", "Feyenoord"), ("N1", "PSV Eindhoven", "Ajax"), ("N1", "Feyenoord", "Sparta Rotterdam"),
    ("N1", "PSV Eindhoven", "Feyenoord"),
    ("B1", "Club Brugge", "Cercle Brugge"), ("B1", "Anderlecht", "Standard"), ("B1", "Antwerp", "Beerschot VA"),
    ("B1", "Anderlecht", "Club Brugge"),
    ("P1", "Benfica", "Sp Lisbon"), ("P1", "Porto", "Benfica"), ("P1", "Porto", "Sp Lisbon"),
    ("T1", "Galatasaray", "Fenerbahce"), ("T1", "Besiktas", "Fenerbahce"), ("T1", "Galatasaray", "Besiktas"),
    ("T1", "Trabzonspor", "Fenerbahce"),
    ("G1", "Olympiakos", "Panathinaikos"), ("G1", "AEK", "Olympiakos"), ("G1", "AEK", "Panathinaikos"),
    ("G1", "PAOK", "Aris"),
]

_BY_LEAGUE = {}
for _league, _a, _b in DERBIES:
    _BY_LEAGUE.setdefault(_league, []).extend([{"home_team": _a, "away_team": _b},
                                               {"home_team": _b, "away_team": _a}])


def same_team(name, target):
    """Строго: същото име, псевдоним, близко изписване или съвсем същите думи. Свободното
    сравнение на teams.match_fixture тук не става - "Liverpool - Man City" излизаше дерби
    заради "Liverpool - Man United" (Man ~ Manchester)."""
    return name == target or teams._same(name, target) or _words(name) == _words(target)


def _words(name):
    """Думите без "FC", "AC" и подобни, но С общите думи: иначе "Atlético Madrid" и
    "Real Madrid" стават едно и също ("madrid")."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return {w for w in "".join(ch if ch.isalnum() else " " for ch in plain).split()
            if w not in teams.FILLER}


def is_derby(league, home, away):
    """Дерби ли е мачът. league е кодът на football-data (E0, SP1...)."""
    return any(same_team(home, p["home_team"]) and same_team(away, p["away_team"])
               for p in _BY_LEAGUE.get(league, []))
