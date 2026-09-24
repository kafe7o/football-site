"""
Тайните - само от .env, никога в кода (виж CLAUDE.md).

Употреба:
    from bets import config
    key = config.require("ODDS_API_KEY")
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
APIFOOTBALL_KEY = os.environ.get("APIFOOTBALL_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")

# Пътищата може да се пренасочат с променливи на средата - в облака (GitHub Actions)
# базата и папката на сайта са на друго място, а кодът е същият.
DB_PATH = Path(os.environ.get("FOOTBALL_DB", ROOT / "football.db"))
SITE_DIR = Path(os.environ.get("FOOTBALL_SITE", ROOT / "site"))
RESULTS_DIR = Path(os.environ.get("FOOTBALL_RESULTS", ROOT / "results"))
CACHE_DIR = ROOT / ".cache"
LOG_DIR = Path(os.environ.get("FOOTBALL_LOGS", ROOT / "logs"))

HINTS = {
    "ODDS_API_KEY": "ключ от the-odds-api.com",
    "APIFOOTBALL_KEY": "ключ от dashboard.api-football.com (безплатен)",
    "GITHUB_TOKEN": "токен с право за запис в хранилището на сайта",
    "GITHUB_REPO": "потребител/хранилище, напр. kafe7o/football-site",
}


def require(*names):
    """Връща стойностите или обяснява точно какво липсва и откъде се взима."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        lines = "\n".join(f"  {n}= ... ({HINTS.get(n, 'виж .env.example')})" for n in missing)
        raise RuntimeError(f"Липсва в .env:\n{lines}")
    values = [globals()[n] for n in names]
    return values[0] if len(values) == 1 else tuple(values)
