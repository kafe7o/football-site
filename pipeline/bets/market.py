"""
market.py - едно място за превръщане на коефициенти в пазарни вероятности.

Защо съществува: пазарът е базовата линия, с която се сравнява всичко в проекта. Досега
всеки файл си махаше маржа поотделно и по най-простия начин (дели 1/коефициент на сумата).
Този начин е измеримо по-лош - виж devig_check.py и results/devig_check.json:

  67 249 мача, 2016/17 нататък, средни коефициенти на пазара
  multiplicative (старият)   Brier 0.59590
  power (този)               Brier 0.59546      разлика -0.00044, t = -6.4, p = 2e-10

Същата посока и на Bet365 (-0.00031), и на затварящите коефициенти (-0.00038). Причината се
вижда в числата: пропорционалното махане подценява фаворита (0.4943 срещу 0.5047) и надценява
аутсайдера (0.2310 срещу 0.2252). Маржът не е разпределен поравно - букмейкърът слага повече
от него върху аутсайдера, защото там го бият по-често.

Методът power търси степен k, за която сумата от (1/коефициент)^k е точно 1.

Употреба:
    from market import implied_probs
    p = implied_probs(np.array([[2.70, 3.40, 2.90]]))     # (n, 3) -> (n, 3)
"""

import numpy as np

MIN_ODDS, MAX_ODDS, MIN_OVERROUND = 1.0, 50.0, 0.95
ITERATIONS = 60     # деление на интервала; 60 стъпки стигат за 1e-15 точност


def valid_odds(odds):
    """Маска на физически възможните редове. Виж CLAUDE.md - football-data има счупени редове."""
    odds = np.asarray(odds, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / odds
        return (np.isfinite(odds).all(1) & (odds > MIN_ODDS).all(1)
                & (odds <= MAX_ODDS).all(1) & (inv.sum(1) >= MIN_OVERROUND))


def implied_probs(odds, method="power"):
    """Пазарни вероятности без маржа. Редовете със счупени коефициенти връщат NaN.

    method="power"          - степенно, по подразбиране (измерено най-точно)
    method="multiplicative" - пропорционално, само за сравнение със стари резултати
    """
    odds = np.atleast_2d(np.asarray(odds, dtype=float))
    out = np.full(odds.shape, np.nan)
    ok = valid_odds(odds)
    if not ok.any():
        return out

    inv = 1.0 / odds[ok]
    if method == "multiplicative":
        out[ok] = inv / inv.sum(1, keepdims=True)
        return out
    if method != "power":
        raise ValueError(f"непознат метод: {method!r} (има 'power' и 'multiplicative')")

    # sum(inv^k) намалява с k, защото всяко inv е под 1. Търси се k със сума точно 1.
    lo = np.full(len(inv), 1.0)                  # при k=1 сумата е над 1 (това е маржът)
    hi = np.full(len(inv), 8.0)
    for _ in range(ITERATIONS):
        mid = (lo + hi) / 2
        too_big = (inv ** mid[:, None]).sum(1) > 1.0
        lo = np.where(too_big, mid, lo)
        hi = np.where(too_big, hi, mid)
    k = ((lo + hi) / 2)[:, None]
    probs = inv ** k
    out[ok] = probs / probs.sum(1, keepdims=True)   # остатъчна грешка от делението
    return out


def implied_row(odds, method="power"):
    """Един ред коефициенти -> списък с вероятности, или None при счупени данни."""
    p = implied_probs([list(odds)], method=method)[0]
    return None if not np.isfinite(p).all() else [float(x) for x in p]
