"""
БАНКАТА (майсторът, 22.10, предадено от собственика): „Искам така да го прави тоя чат бот, че да ни разпредели банка. Примерно казваме му, че
разполагаме с 200 евро... искам да ми контролираш банката и от 1 ноември до 1 декември, един месец, да ми направиш банката да е по две.
Почваме с 200, гоним 400... Нека да ни казва как да процедираме.“

Какво прави:
  1. Собственикът задава банка, период и цел: data/bank.json (`python run.py bank 200 2026-11-01 2026-12-01 400`).
  2. Планът = какъв ДЯЛ от текущата банка се залага на колонка (системата) и на ден с надстройка (разделен на петте четворки). Избира се
     делът с НАЙ-ГОЛЯМ ШАНС целта да се стигне до края на периода - Монте Карло върху измереното назад за двете логики, които се записват
     (системата: ~7 колонки на месец, чет-нед, минават 51%, коеф. 2.11; надстройката: ~4.2 дни на месец, 5 четворки от 5 мача, мач минава ~76%,
     коеф. 4.17). Протоколът и таблицата по дялове - research/bank_backtest.py (data/bank_backtest.json).
  3. Всеки ден: залогът по плана за днешните колонки (сайтът, таб Колонки -> Банката; сутрешното известие) и честният шанс целта да се стигне
     от сегашното положение - и при измереното назад, и ако предимството е нула (CLV 0: печалбата назад е от цената, нулата не е изключена).
  4. Води банката: от началната, по уредените колонки със залозите по плана (замяна назад, детерминирано; отделна таблица няма).
     Стигне ли банката целта - планът спира („цел стигната“); падне ли под 1 € - също.
Целта не е обещание. Назад двете логики връщат ~1.08-1.09 € от 1 € (± 0.1) - удвояване за месец става само с голям дял и голям шанс за загуба.
Числото се казва; решението е на собственика. Залог по плана има само тук; прогнозите и известията иначе остават без размер на залога.
"""

import json
import logging
import random
from datetime import date, timedelta

from . import config

log = logging.getLogger(__name__)

FILE = "bank.json"
# измереното назад (чиста проверка; rules.py „system“ и „addon“, addon.MEASURED): шанс, коефициент и колко често има колонка
SYSTEM = {"p": 0.51, "odds": 2.11, "per_thu_sun": 7 / 17.4}     # 7 колонки на месец върху ~17.4 дни чет-нед
ADDON = {"leg_p": 0.76, "odds": 4.17, "per_day": 4.2 / 30.4, "legs": 5, "cols": 5}   # 76% на мач дава 31% на четворка и 67% на ден - измереното
SHARES = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)
PATHS = 4000
BUST = 0.2        # „срив“: банката под 1/5 от началната


def load():
    path = config.DATA_DIR / FILE
    if not path.exists():
        return None
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return cfg if cfg.get("bank") and cfg.get("start") and cfg.get("end") and cfg.get("target") else None


def save(bank, start, end, target):
    cfg = {"bank": float(bank), "start": start, "end": end, "target": float(target), "set": date.today().isoformat()}
    (config.DATA_DIR / FILE).write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return cfg


def _days(start, end):
    d, out = date.fromisoformat(start), []
    while d < date.fromisoformat(end):
        out.append(d)
        d += timedelta(days=1)
    return out


def simulate(bank, start, end, target, share, edge=True, paths=PATHS, seed=1):
    """Монте Карло: дял `share` от текущата банка на колонка; връща {шанс за целта, шанс за срив, медиана накрая}.
    edge=False: предимство нула - мачът минава точно толкова често, колкото казва цената (1/коеф.)."""
    rng = random.Random(seed)
    sp = SYSTEM["p"] if edge else 1 / SYSTEM["odds"]
    lp = ADDON["leg_p"] if edge else (1 / ADDON["odds"]) ** 0.25      # четворката минава 1/коеф. -> мачът (1/коеф.)^(1/4)
    days = _days(start, end)
    hit = bust = 0
    ends = []
    for _ in range(paths):
        b = float(bank)
        for d in days:
            if b >= target or b < 1:
                break
            if d.weekday() >= 3 and rng.random() < SYSTEM["per_thu_sun"]:
                st = share * b
                b += st * (SYSTEM["odds"] - 1) if rng.random() < sp else -st
            if rng.random() < ADDON["per_day"]:
                st = share * b / ADDON["cols"]
                k = sum(rng.random() < lp for _ in range(ADDON["legs"]))
                won = ADDON["cols"] if k == 5 else 1 if k == 4 else 0
                b += won * st * (ADDON["odds"] - 1) - (ADDON["cols"] - won) * st
        hit += b >= target
        bust += b < BUST * bank
        ends.append(b)
    ends.sort()
    return {"target": hit / paths, "bust": bust / paths, "median": round(ends[paths // 2], 2)}


def plans(bank, start, end, target):
    """Таблицата по дялове - при измереното назад и при предимство нула; `best` е делът с най-голям шанс за целта."""
    rows = []
    for s in SHARES:
        e = simulate(bank, start, end, target, s)
        rows.append({"share": s, "target": e["target"], "bust": e["bust"], "median": e["median"],
                     "zero": simulate(bank, start, end, target, s, edge=False)})
    best = max(rows, key=lambda r: r["target"] - r["bust"])      # шансът за целта само расте с дяла - мери се срещу шанса за срив
    return {"rows": rows, "best": best["share"]}


def ledger(conn, cfg, today, share):
    """Банката ден по ден от началото до днес по уредените колонки със залозите по плана. Връща (банка сега, дни, състояние, чакащи €)."""
    from . import addon, shadow
    shadow.ensure(conn)
    addon.ensure(conn)
    b, days, state, pending = float(cfg["bank"]), [], "върви", 0.0
    for d in _days(cfg["start"], min(today, cfg["end"])):
        day = d.isoformat()
        if b >= cfg["target"]:
            state = "цел стигната"
            break
        if b < 1:
            state = "банката свърши"
            break
        before, net, items = b, 0.0, []
        sy = conn.execute("SELECT passed, summary_json FROM columns_system WHERE day = ? AND idx = 1 AND json_extract(summary_json, '$.retro') IS NULL",
                          (day,)).fetchone()
        if sy:
            st = round(share * before, 2)
            s = json.loads(sy["summary_json"])
            r = None if sy["passed"] is None else (round(st * (s["odds"] - 1), 2) if sy["passed"] else -st)
            items.append({"what": "системата", "stake": st, "odds": s["odds"], "net": r})
        ad = conn.execute("SELECT passed, summary_json FROM columns_addon WHERE day = ? AND idx > 0", (day,)).fetchall()
        if ad:
            st = round(share * before / ADDON["cols"], 2)
            for a in ad:
                s = json.loads(a["summary_json"])
                r = None if a["passed"] is None else (round(st * (s["odds"] - 1), 2) if a["passed"] else -st)
                items.append({"what": "надстройката", "stake": st, "odds": s["odds"], "net": r})
        if not items:
            continue
        for it in items:
            if it["net"] is None:
                pending += it["stake"]
            else:
                net += it["net"]
        b = round(b + net, 2)
        days.append({"day": day, "before": before, "items": items, "net": round(net, 2), "after": b})
    return b, days, state, round(pending, 2)


def for_site(conn, now=None):
    """За сайта: настройката, планът, банката сега, залогът за днес, шансът за целта от сегашното положение, дните."""
    from datetime import datetime, timezone
    from . import columns
    cfg = load()
    if not cfg:
        return None
    now = now or datetime.now(timezone.utc)
    today = columns.day_of(now.isoformat())
    bt = json.loads((config.DATA_DIR / "bank_backtest.json").read_text(encoding="utf-8")) if (config.DATA_DIR / "bank_backtest.json").exists() else None
    share = (bt or {}).get("best") or plans(cfg["bank"], cfg["start"], cfg["end"], cfg["target"])["best"]
    bank, days, state, pending = ledger(conn, cfg, today, share)
    out = {"cfg": cfg, "share": share, "bank": bank, "days": days, "state": state, "pending": pending, "today": today, "table": (bt or {}).get("rows")}
    if today < cfg["start"]:
        out["state"] = "започва на " + cfg["start"]
    elif today >= cfg["end"] and state == "върви":
        out["state"] = "периодът свърши"
    if out["state"] == "върви":
        out["stake"] = {"system": round(share * bank, 2), "addon_each": round(share * bank / ADDON["cols"], 2), "addon_day": round(share * bank, 2)}
        rest = max(today, cfg["start"])
        out["chance"] = {"edge": simulate(bank, rest, cfg["end"], cfg["target"], share, paths=2000),
                         "zero": simulate(bank, rest, cfg["end"], cfg["target"], share, edge=False, paths=2000)}
    elif today < cfg["start"]:
        out["stake"] = {"system": round(share * bank, 2), "addon_each": round(share * bank / ADDON["cols"], 2), "addon_day": round(share * bank, 2)}
        out["chance"] = {"edge": simulate(bank, cfg["start"], cfg["end"], cfg["target"], share, paths=2000),
                         "zero": simulate(bank, cfg["start"], cfg["end"], cfg["target"], share, edge=False, paths=2000)}
    return out


def day_text(conn, now=None):
    """Редът за сутрешното известие: банката сега, залогът по плана за днес и шансът за целта (или None без банка/извън периода)."""
    s = for_site(conn, now)
    if not s or s["state"] != "върви":
        return None
    c, ch = s["cfg"], s["chance"]
    return (f"БАНКАТА: {s['bank']:.2f} € (от {c['bank']:.0f} €, цел {c['target']:.0f} € до {c['end'][8:10]}.{c['end'][5:7]}). Днес по плана ({s['share']:.0%} от банката): "
            f"системата {s['stake']['system']:.2f} €; надстройката {s['stake']['addon_day']:.2f} € = 5 x {s['stake']['addon_each']:.2f} €. "
            f"Шанс за целта оттук: ~{ch['edge']['target']:.0%} при измереното назад, ~{ch['zero']['target']:.0%} ако предимство няма; "
            f"шанс банката да падне под {BUST * c['bank']:.0f} €: ~{ch['edge']['bust']:.0%} / ~{ch['zero']['bust']:.0%}. Не е обещание - решението е твое.")
