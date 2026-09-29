"""
Всички първенства на едно място (2026-09-29, идеята на професионалиста: „прогнози за
АБСОЛЮТНО всички първенства и всички мачове“).

Източници на историята (резултати мач по мач, от 2012):
  fd     football-data.co.uk/mmz4281 - 22 лиги, коефициенти 1/X/2 и над/под 2.5 преди мача
  fdnew  football-data.co.uk/new     - 16 държави, само затварящи коефициенти 1/X/2
  oldb   OpenLigaDB                  - 3. Бундеслига, без коефициенти (дават се от odds API)
  api    само the-odds-api           - без история; прогнозата е на пазара, не на модела

rr - колко пъти всеки отбор приема всеки друг за редовния сезон (1 = два кръга, 2 = четири);
0 - симулация на крайното класиране не се прави (плейофи, конференции, кратки турнири).
split - след редовния сезон таблицата се дели: симулира се само редовният сезон.
up/down - колко места горе (шампион + Европа/промоция) и долу (изпадане) се оцветяват.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class League:
    code: str
    country: str
    name: str
    tier: int
    source: str                 # fd | fdnew | oldb | api
    sport: str | None = None    # ключът в odds API
    rr: int = 1
    up: int = 4
    down: int = 3
    split: bool = False
    fdnew: str | None = None    # файлът в football-data/new
    oldb: str | None = None     # съкращението в OpenLigaDB

    @property
    def title(self):
        return f"{self.country} · {self.name}"

    @property
    def has_history(self):
        return self.source != "api"


_ALL = [
    # --- football-data, основните 22 ---
    League("E0", "Англия", "Висша лига", 1, "fd", "soccer_epl", up=4, down=3),
    League("E1", "Англия", "Чемпиъншип", 2, "fd", "soccer_efl_champ", up=2, down=3),
    League("E2", "Англия", "Лига 1", 3, "fd", "soccer_england_league1", up=2, down=4),
    League("E3", "Англия", "Лига 2", 4, "fd", "soccer_england_league2", up=3, down=2),
    League("EC", "Англия", "Национална лига", 5, "fd", None, up=1, down=4),
    League("SC0", "Шотландия", "Премиършип", 1, "fd", "soccer_spl", up=2, down=1, split=True),
    League("SC1", "Шотландия", "Чемпиъншип", 2, "fd", None, rr=2, up=1, down=1),
    League("SC2", "Шотландия", "Лига 1", 3, "fd", None, rr=2, up=1, down=1),
    League("SC3", "Шотландия", "Лига 2", 4, "fd", None, rr=2, up=1, down=1),
    League("D1", "Германия", "Бундеслига", 1, "fd", "soccer_germany_bundesliga", up=4, down=2),
    League("D2", "Германия", "2. Бундеслига", 2, "fd", "soccer_germany_bundesliga2", up=2, down=2),
    League("D3", "Германия", "3. Лига", 3, "oldb", "soccer_germany_liga3", up=2, down=4, oldb="bl3"),
    League("I1", "Италия", "Серия А", 1, "fd", "soccer_italy_serie_a", up=4, down=3),
    League("I2", "Италия", "Серия Б", 2, "fd", "soccer_italy_serie_b", up=2, down=3),
    League("SP1", "Испания", "Ла Лига", 1, "fd", "soccer_spain_la_liga", up=4, down=3),
    League("SP2", "Испания", "Сегунда", 2, "fd", "soccer_spain_segunda_division", up=2, down=4),
    League("F1", "Франция", "Лига 1", 1, "fd", "soccer_france_ligue_one", up=3, down=2),
    League("F2", "Франция", "Лига 2", 2, "fd", "soccer_france_ligue_two", up=2, down=2),
    League("N1", "Нидерландия", "Ередивизи", 1, "fd", "soccer_netherlands_eredivisie", up=2, down=2),
    League("B1", "Белгия", "Про Лига", 1, "fd", "soccer_belgium_first_div", up=1, down=1, split=True),
    League("P1", "Португалия", "Примейра Лига", 1, "fd", "soccer_portugal_primeira_liga", up=2, down=2),
    League("T1", "Турция", "Суперлига", 1, "fd", "soccer_turkey_super_league", up=2, down=4),
    League("G1", "Гърция", "Суперлига", 1, "fd", "soccer_greece_super_league", up=1, down=2, split=True),
    # --- football-data/new: цялата история в един файл на държава ---
    League("ARG", "Аржентина", "Примера", 1, "fdnew", "soccer_argentina_primera_division", rr=0, fdnew="ARG"),
    League("AUT", "Австрия", "Бундеслига", 1, "fdnew", "soccer_austria_bundesliga", up=1, down=1, split=True, fdnew="AUT"),
    League("BRA", "Бразилия", "Серия А", 1, "fdnew", "soccer_brazil_campeonato", up=4, down=4, fdnew="BRA"),
    League("CHN", "Китай", "Суперлига", 1, "fdnew", "soccer_china_superleague", up=1, down=2, fdnew="CHN"),
    League("DNK", "Дания", "Суперлига", 1, "fdnew", "soccer_denmark_superliga", up=1, down=1, split=True, fdnew="DNK"),
    League("FIN", "Финландия", "Вейкауслийга", 1, "fdnew", "soccer_finland_veikkausliiga", up=1, down=1, split=True, fdnew="FIN"),
    League("IRL", "Ирландия", "Премиър дивизия", 1, "fdnew", "soccer_league_of_ireland", rr=2, up=1, down=1, fdnew="IRL"),
    League("JPN", "Япония", "Джей Лига", 1, "fdnew", "soccer_japan_j_league", up=3, down=3, fdnew="JPN"),
    League("MEX", "Мексико", "Лига MX", 1, "fdnew", "soccer_mexico_ligamx", rr=0, fdnew="MEX"),
    League("NOR", "Норвегия", "Елитсерия", 1, "fdnew", "soccer_norway_eliteserien", up=1, down=2, fdnew="NOR"),
    League("POL", "Полша", "Екстракласа", 1, "fdnew", "soccer_poland_ekstraklasa", up=1, down=3, fdnew="POL"),
    League("ROU", "Румъния", "Лига 1", 1, "fdnew", None, up=1, down=2, split=True, fdnew="ROU"),
    League("RUS", "Русия", "Премиер лига", 1, "fdnew", "soccer_russia_premier_league", up=1, down=2, fdnew="RUS"),
    League("SWE", "Швеция", "Алсвенскан", 1, "fdnew", "soccer_sweden_allsvenskan", up=1, down=2, fdnew="SWE"),
    League("SWZ", "Швейцария", "Суперлига", 1, "fdnew", "soccer_switzerland_superleague", up=1, down=1, split=True, fdnew="SWZ"),
    League("USA", "САЩ", "MLS", 1, "fdnew", "soccer_usa_mls", rr=0, fdnew="USA"),
    # --- само odds API: прогноза по пазара ---
    League("BRA2", "Бразилия", "Серия Б", 2, "api", "soccer_brazil_serie_b", rr=0),
    League("CHI", "Чили", "Примера", 1, "api", "soccer_chile_campeonato", rr=0),
    League("AUS", "Австралия", "A-Лига", 1, "api", "soccer_australia_aleague", rr=0),
    League("SWE2", "Швеция", "Суперетан", 2, "api", "soccer_sweden_superettan", rr=0),
    League("KOR", "Корея", "K Лига 1", 1, "api", "soccer_korea_kleague1", rr=0),
    League("KSA", "Саудитска Арабия", "Про Лига", 1, "api", "soccer_saudi_arabia_pro_league", rr=0),
    League("UCL", "Европа", "Шампионска лига", 0, "api", "soccer_uefa_champs_league", rr=0),
    League("UEL", "Европа", "Лига Европа", 0, "api", "soccer_uefa_europa_league", rr=0),
    League("UECL", "Европа", "Лига на конференциите", 0, "api", "soccer_uefa_europa_conference_league", rr=0),
    League("LIB", "Южна Америка", "Копа Либертадорес", 0, "api", "soccer_conmebol_copa_libertadores", rr=0),
    League("SUD", "Южна Америка", "Копа Судамерикана", 0, "api", "soccer_conmebol_copa_sudamericana", rr=0),
    League("ENGC", "Англия", "Купа на лигата", 0, "api", "soccer_england_efl_cup", rr=0),
    League("FAC", "Англия", "ФА Къп", 0, "api", "soccer_fa_cup", rr=0),
    League("DFB", "Германия", "Купа на Германия", 0, "api", "soccer_germany_dfb_pokal", rr=0),
    League("CDR", "Испания", "Купа на краля", 0, "api", "soccer_spain_copa_del_rey", rr=0),
    League("CIT", "Италия", "Купа на Италия", 0, "api", "soccer_italy_coppa_italia", rr=0),
    League("CDF", "Франция", "Купа на Франция", 0, "api", "soccer_france_coupe_de_france", rr=0),
    League("UNL", "Национални", "Лига на нациите", 0, "api", "soccer_uefa_nations_league", rr=0),
    League("WCQE", "Национални", "Световни квалификации (Европа)", 0, "api", "soccer_fifa_world_cup_qualifiers_europe", rr=0),
    League("WCQS", "Национални", "Световни квалификации (Ю. Америка)", 0, "api", "soccer_fifa_world_cup_qualifiers_south_america", rr=0),
    League("EUQ", "Национални", "Евро квалификации", 0, "api", "soccer_uefa_euro_qualification", rr=0),
]

LEAGUES = {lg.code: lg for lg in _ALL}
BY_SPORT = {lg.sport: lg for lg in _ALL if lg.sport}
FD = [lg.code for lg in _ALL if lg.source == "fd"]
FDNEW = [lg.code for lg in _ALL if lg.source == "fdnew"]
WITH_HISTORY = [lg.code for lg in _ALL if lg.has_history]

# Държавата и лигата във файла new_league_fixtures.csv -> кодът ни
FDNEW_FIXTURES = {
    ("Argentina", "Liga Profesional"): "ARG", ("Austria", "Bundesliga"): "AUT",
    ("Brazil", "Serie A"): "BRA", ("China", "Super League"): "CHN",
    ("Denmark", "Superliga"): "DNK", ("Finland", "Veikkausliiga"): "FIN",
    ("Ireland", "Premier Division"): "IRL", ("Japan", "J1 League"): "JPN",
    ("Mexico", "Liga MX"): "MEX", ("Norway", "Eliteserien"): "NOR",
    ("Poland", "Ekstraklasa"): "POL", ("Romania", "Liga 1"): "ROU",
    ("Russia", "Premier League"): "RUS", ("Sweden", "Allsvenskan"): "SWE",
    ("Switzerland", "Super League"): "SWZ", ("USA", "MLS"): "USA",
}


def title(code):
    lg = LEAGUES.get(code)
    return lg.title if lg else code
