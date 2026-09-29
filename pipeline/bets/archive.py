"""
Пълният архив (football.db) и платените исторически цени - в облака, но не публично (2026-09-30).

Хранилището на сайта е публично (GitHub Pages е безплатен само така), а архивът е 128 MB -
повече от лимита на git за файл. Затова двата файла се пазят като ПРИКАЧЕНИ файлове на
издание (GitHub Release „archive“) в същото хранилище - извън историята на git, до 2 GB на файл:

  football.db.gz.enc        архивът: компресиран (~35 MB) и шифрован; седмичната задача го обновява
  odds_snapshots.tar.gz.enc историческите цени от odds API (платени ~4 900 кредита, 264 снимки);
                            качват се веднъж и никога не се презаписват автоматично

Шифроването е с PyNaCl (SecretBox, XSalsa20-Poly1305) и ключ ARCHIVE_KEY - в .env на лаптопа и
като шифрована тайна в GitHub. Без ключа файловете не се четат; платените данни не са публични.
"""

import gzip
import json
import logging
import os
import urllib.error
import urllib.request

import nacl.secret

from . import config

log = logging.getLogger(__name__)

TAG = "archive"
DB_ASSET = "football.db.gz.enc"
SNAPSHOTS_ASSET = "odds_snapshots.tar.gz.enc"
API = "https://api.github.com"


def _key():
    raw = os.environ.get("ARCHIVE_KEY")
    if not raw:
        raise RuntimeError("Липсва ARCHIVE_KEY (в .env или като тайна в GitHub)")
    return bytes.fromhex(raw.strip())


def encrypt(data):
    return nacl.secret.SecretBox(_key()).encrypt(gzip.compress(data, compresslevel=6))


def decrypt(blob):
    return gzip.decompress(nacl.secret.SecretBox(_key()).decrypt(blob))


def _auth():
    token = os.environ.get("GH_TOKEN") or config.GITHUB_TOKEN
    repo = os.environ.get("GITHUB_REPOSITORY") or config.GITHUB_REPO
    if not token or not repo:
        raise RuntimeError("Липсва токен или име на хранилището (GITHUB_TOKEN/GITHUB_REPO)")
    return token, repo


def _call(method, url, token, body=None, content_type="application/json", timeout=300):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "Content-Type": content_type, "User-Agent": "football-archive"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300].replace(token, "<TOKEN>")
        raise RuntimeError(f"GitHub {method} {url.split('?')[0]}: {e.code} {detail}") from None


def release(create=True):
    """Изданието „archive“ - намира го или го създава."""
    token, repo = _auth()
    try:
        return _call("GET", f"{API}/repos/{repo}/releases/tags/{TAG}", token)
    except RuntimeError as e:
        if " 404 " not in str(e) or not create:
            raise
    log.info("Изданието „%s“ го няма - създавам го", TAG)
    return _call("POST", f"{API}/repos/{repo}/releases", token, {
        "tag_name": TAG, "name": "Архив (шифрован)", "make_latest": "false",
        "body": "Пълният архив на мачовете и историческите цени - шифровани, за седмичния анализ. "
                "Не е за сваляне: без ключа файловете не се четат."})


def upload(name, data, replace=True):
    """Качва (шифровани) байтове като прикачен файл. replace=False - ако вече го има, не пипа."""
    token, repo = _auth()
    rel = release()
    old = [a for a in rel.get("assets", []) if a["name"] == name]
    if old and not replace:
        log.info("%s вече е качен - не се презаписва", name)
        return old[0]
    blob = encrypt(data)
    # първо се качва под временно име: ако качването падне, старият файл остава
    tmp = name + ".new"
    for a in rel.get("assets", []):
        if a["name"] == tmp:
            _call("DELETE", f"{API}/repos/{repo}/releases/assets/{a['id']}", token)
    up = rel["upload_url"].split("{")[0]
    asset = _call("POST", f"{up}?name={tmp}", token, blob, "application/octet-stream", timeout=900)
    for a in old:
        _call("DELETE", f"{API}/repos/{repo}/releases/assets/{a['id']}", token)
    _call("PATCH", f"{API}/repos/{repo}/releases/assets/{asset['id']}", token, {"name": name})
    log.info("Качено %s: %.1f MB (от %.1f MB)", name, len(blob) / 1e6, len(data) / 1e6)
    return asset


def download(name):
    """Сваля и разшифрова прикачения файл. Байтовете или RuntimeError."""
    token, repo = _auth()
    rel = release(create=False)
    asset = next((a for a in rel.get("assets", []) if a["name"] == name), None)
    if asset is None:
        raise RuntimeError(f"{name} го няма в изданието „{TAG}“")
    req = urllib.request.Request(asset["url"], headers={
        "Authorization": f"Bearer {token}", "Accept": "application/octet-stream", "User-Agent": "football-archive"})
    # GitHub препраща към временен адрес за сваляне; там заглавката с токена не трябва да отива
    class NoAuthRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req_, fp, code, msg, headers, newurl):
            new = super().redirect_request(req_, fp, code, msg, headers, newurl)
            if new is not None:
                new.headers.pop("Authorization", None)
                new.unredirected_hdrs.pop("Authorization", None)
            return new
    with urllib.request.build_opener(NoAuthRedirect).open(req, timeout=900) as resp:
        blob = resp.read()
    return decrypt(blob)


def pull_db(path):
    """Архивът от облака -> файл (заменя местния само след успешно сваляне и разшифроване)."""
    data = download(DB_ASSET)
    tmp = path.with_suffix(".download")
    tmp.write_bytes(data)
    tmp.replace(path)
    log.info("Архивът е свален: %s (%.1f MB)", path, len(data) / 1e6)
    return path


def push_db(path):
    """Файлът -> архивът в облака. Базата трябва да е затворена."""
    return upload(DB_ASSET, path.read_bytes())
