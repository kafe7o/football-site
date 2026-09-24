"""
Моделът: Poisson с атака и защита за всеки отбор, домакинско предимство, затихване по време.

    lambda_домакин = exp(атака[домакин] - защита[гост] + предимство)
    lambda_гост    = exp(атака[гост]    - защита[домакин])

Оценка по максимално правдоподобие, тежест на мач отпреди 180 дни - наполовина.
Регуляризация 1.0 върху атаките и защитите.

ЗАЩО точно този и защо нищо не се променя без число (виж CLAUDE.md):
  - регуляризация 1.0 е приета срещу 0.01 с t = -6.65;
  - Dixon-Coles, двумерен Poisson, отрицателно биномен, zero-inflated и Poisson-ът на
    penaltyblog са тествани на 14 207 мача в 18 лиги (2026-09-24) и ВСИЧКИ са по-лоши
    от този модел (t = +4.4 до +7.0). Регуляризацията тежи повече от избора на семейство;
  - и този модел остава по-неточен от пазара (Brier 0.6151 срещу 0.5992). Тоест моделът
    е независимо мнение, а не предимство.

Отбор с малко скорошни мачове не се прогнозира изобщо: при 2-3 мача параметрите му са
безсмислени и дават уверени глупости (реален случай: Hull с 3 мача получи защита +3.677
и свали противника до 1.8% за домакинска победа).
"""

import logging

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

log = logging.getLogger(__name__)

VERSION = "poisson_v2_reg1"
HALF_LIFE_DAYS = 180.0
REG_STRENGTH = 1.0
MAX_GOALS = 10
MIN_TRAIN_MATCHES = 100
MIN_EFFECTIVE_MATCHES = 10.0     # претеглени, не сурови - виж docstring-а


class Poisson:
    def __init__(self, half_life=HALF_LIFE_DAYS, reg=REG_STRENGTH):
        self.half_life = half_life
        self.reg = reg
        self.teams = []
        self.index = {}
        self.params = None
        self.as_of = None
        self.weight_by_team = {}

    def weights(self, dates, as_of):
        age = (as_of - dates).dt.days.clip(lower=0).to_numpy(dtype=float)
        return 0.5 ** (age / self.half_life)

    def fit(self, df, as_of=None):
        """df: date, home_team, away_team, fthg, ftag - само изиграни мачове."""
        if len(df) < MIN_TRAIN_MATCHES:
            raise ValueError(f"Само {len(df)} мача за обучение, трябват поне {MIN_TRAIN_MATCHES}")
        as_of = as_of or df["date"].max()
        self.as_of = as_of
        self.teams = sorted(set(df["home_team"]) | set(df["away_team"]))
        n = len(self.teams)
        self.index = {t: i for i, t in enumerate(self.teams)}

        hi = df["home_team"].map(self.index).to_numpy()
        ai = df["away_team"].map(self.index).to_numpy()
        hg = df["fthg"].to_numpy(dtype=float)
        ag = df["ftag"].to_numpy(dtype=float)
        w = self.weights(df["date"], as_of)

        # Колко мача реално е "видял" моделът за всеки отбор, след затихването.
        both = pd.concat([pd.DataFrame({"team": df["home_team"], "w": w}),
                          pd.DataFrame({"team": df["away_team"], "w": w})])
        self.weight_by_team = both.groupby("team")["w"].sum().to_dict()

        def neg_loglik(p):
            att, dfn, adv = p[:n], p[n:2 * n], p[2 * n]
            lh = np.exp(att[hi] - dfn[ai] + adv)
            la = np.exp(att[ai] - dfn[hi])
            ll = w * ((hg * np.log(lh) - lh) + (ag * np.log(la) - la))
            return -ll.sum() + self.reg * (np.sum(att ** 2) + np.sum(dfn ** 2))

        start = np.concatenate([np.zeros(n), np.zeros(n), [0.25]])
        result = minimize(neg_loglik, start, method="SLSQP",
                          constraints=({"type": "eq", "fun": lambda p: p[:n].mean()},),
                          options={"maxiter": 300, "ftol": 1e-7})
        if not result.success:
            log.warning("Оптимизацията не се събра: %s", result.message)
        self.params = result.x
        return self

    def seen(self, team):
        return float(self.weight_by_team.get(team, 0.0))

    def lambdas(self, home, away):
        if self.params is None:
            raise RuntimeError("Моделът не е обучен")
        if home not in self.index or away not in self.index:
            return None
        n = len(self.teams)
        att, dfn, adv = self.params[:n], self.params[n:2 * n], self.params[2 * n]
        h, a = self.index[home], self.index[away]
        return float(np.exp(att[h] - dfn[a] + adv)), float(np.exp(att[a] - dfn[h]))

    def probabilities(self, home, away, check_history=True):
        """(p1, pX, p2) или None - непознат отбор или твърде малко скорошни мачове."""
        lam = self.lambdas(home, away)
        if lam is None:
            return None
        if check_history:
            thin = [t for t in (home, away) if self.seen(t) < MIN_EFFECTIVE_MATCHES]
            if thin:
                log.info("%s - %s: малко скорошни мачове (%s), без прогноза", home, away,
                         ", ".join(f"{t}={self.seen(t):.1f}" for t in thin))
                return None
        return outcome_probabilities(*lam)


def score_grid(lam_home, lam_away, max_goals=MAX_GOALS):
    k = np.arange(max_goals + 1)
    joint = np.outer(poisson.pmf(k, lam_home), poisson.pmf(k, lam_away))
    return joint / joint.sum()


def outcome_probabilities(lam_home, lam_away):
    grid = score_grid(lam_home, lam_away)
    return (float(np.tril(grid, -1).sum()), float(np.trace(grid)), float(np.triu(grid, 1).sum()))


def brier(probs, outcome):
    """Разстояние до истината за един мач; по-малкото е по-добро."""
    return float(sum((p - (1.0 if i == outcome else 0.0)) ** 2 for i, p in enumerate(probs)))
