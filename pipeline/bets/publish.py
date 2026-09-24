"""
publish_site.py - качва site/index.html в GitHub Pages, за да има сайтът истински
външен адрес, който се отваря в браузър и на телефон без влизане никъде.

Адресът става https://ПОТРЕБИТЕЛ.github.io/ХРАНИЛИЩЕ/

Какво прави при първо пускане:
  1. създава хранилището, ако го няма (публично - GitHub Pages е безплатен само за такива);
  2. прави site/ отделно git хранилище, за да НЕ качи базата, .env и кода;
  3. качва index.html и .nojekyll;
  4. включва GitHub Pages от клона main.
После при всяко пускане само качва новия index.html.

Токенът се чете от .env (GITHUB_TOKEN) и никога не влиза в git remote-а, за да не остане
записан в .git/config - подава се при всяко качване през заглавка.

Употреба:
    python3 publish_site.py
"""

import json
import logging
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from . import config

log = logging.getLogger("publish_site")

SITE_DIR = config.SITE_DIR
API = "https://api.github.com"


def api(method, path, token, body=None, ok_statuses=(200, 201, 204)):
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json",
                 "User-Agent": "football-forecast-evaluator"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in ok_statuses:
            return e.code, {}
        return e.code, {"error": detail}


def git(*args, cwd=SITE_DIR, token=None, repo=None):
    """git команда в папката на сайта. Токенът влиза само като временна заглавка."""
    cmd = ["git"]
    if token:
        cmd += ["-c", f"http.https://github.com/.extraheader=Authorization: Basic "
                      f"{__import__('base64').b64encode(f'x-access-token:{token}'.encode()).decode()}"]
    cmd += list(args)
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        msg = (done.stderr or done.stdout or "").strip()
        if token:
            msg = msg.replace(token, "<GITHUB_TOKEN>")
        raise RuntimeError(f"git {' '.join(args)} падна: {msg}")
    return (done.stdout or "").strip()


def ensure_repo(token, repo):
    owner, name = repo.split("/", 1)
    status, _ = api("GET", f"/repos/{repo}", token)
    if status == 200:
        return
    if status != 404:
        raise RuntimeError(f"GitHub върна {status} при проверка на {repo}. Провери токена и правата му.")
    log.info("Хранилището %s го няма - създавам го.", repo)
    me_status, me = api("GET", "/user", token)
    if me_status != 200:
        raise RuntimeError(f"Токенът не работи (GitHub върна {me_status}).")
    path = "/user/repos" if me.get("login") == owner else f"/orgs/{owner}/repos"
    status, body = api("POST", path, token,
                       {"name": name, "private": False, "auto_init": False,
                        "description": "Дневни футболни прогнози, мерени срещу коефициентите"})
    if status not in (200, 201):
        raise RuntimeError(f"Не можах да създам {repo}: {status} {body.get('error', '')}. "
                           "Ако токенът е fine-grained, създай хранилището ръчно в GitHub.")


def ensure_pages(token, repo):
    status, _ = api("GET", f"/repos/{repo}/pages", token)
    if status == 200:
        return
    status, body = api("POST", f"/repos/{repo}/pages", token,
                       {"source": {"branch": "main", "path": "/"}})
    if status in (201, 204, 409):
        log.info("GitHub Pages е включен.")
    else:
        log.warning("Не можах да включа Pages автоматично (%s %s). Включи го веднъж ръчно: "
                    "Settings -> Pages -> Source: Deploy from a branch -> main / (root).",
                    status, body.get("error", ""))


def run():
    token, repo = config.require("GITHUB_TOKEN", "GITHUB_REPO")
    index = SITE_DIR / "index.html"
    if not index.exists():
        raise RuntimeError(f"{index} го няма - пусни първо export_site.py")

    ensure_repo(token, repo)
    if not (SITE_DIR / ".git").exists():
        log.info("Правя git хранилище в %s", SITE_DIR)
        git("init", "-b", "main")
        git("remote", "add", "origin", f"https://github.com/{repo}.git")
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")   # иначе Pages пуска Jekyll

    git("config", "user.name", "football-bot")
    git("config", "user.email", "football-bot@users.noreply.github.com")
    git("add", "-A")   # в тази папка живее само сайтът: HTML, снимката, кодът за облака
    if not git("status", "--porcelain"):
        log.info("Сайтът не се е променил - няма какво да се качва.")
    else:
        git("commit", "-m", "Обновен сайт")
        try:
            git("push", "origin", "main", token=token)
        except RuntimeError as e:
            raise RuntimeError(
                f"Качването падна: {e}\nАко историята се е разминала (например след ръчна промяна "
                f"в GitHub), оправи я веднъж ръчно - тук нарочно няма --force, за да не трие чужда работа."
            ) from None
        log.info("Качено в %s", repo)
    ensure_pages(token, repo)

    owner, name = repo.split("/", 1)
    url = f"https://{owner}.github.io/{name}/"
    log.info("Адрес на сайта: %s (първото пускане може да се забави 1-2 минути)", url)
    return url


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
