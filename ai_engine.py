import math
from datetime import date

import numpy as np
from sqlalchemy import func

from models import Asset, PriceHistory, PlanWeight, AllocationPlan

try:
    from sklearn.ensemble import RandomForestRegressor
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

QUESTIONS = [
    ("What is your investment horizon?", [("3", "Less than 1 year"), ("6", "1 to 3 years"),
                                          ("8", "3 to 7 years"), ("10", "More than 7 years")]),
    ("How stable is your income?", [("3", "Irregular"), ("6", "Moderately stable"),
                                    ("8", "Stable"), ("10", "Very stable")]),
    ("An investment falls 20% in a month. What do you do?", [
        ("2", "Sell everything immediately"), ("5", "Sell part of it"),
        ("8", "Hold and wait"), ("10", "Buy more at lower prices")]),
    ("How much of your savings can you invest?", [("3", "More than 70%"), ("6", "40% to 70%"),
                                                  ("8", "20% to 40%"), ("10", "Less than 20%")]),
    ("What is your experience with financial markets?", [
        ("2", "None"), ("5", "Basic"), ("8", "Good"), ("10", "Professional")]),
    ("What is your primary goal?", [("3", "Preserve capital"), ("6", "Beat inflation"),
                                    ("8", "Long-term growth"), ("10", "Maximize returns")]),
    ("Do you have an emergency fund?", [("2", "No"), ("6", "Partially"), ("10", "Yes, 6+ months")]),
    ("How do you react to market volatility?", [("2", "Panic"), ("5", "Worry but hold"),
                                                 ("8", "Accept it"), ("10", "Welcome it")]),
    ("How important is liquidity?", [("3", "Very important"), ("6", "Important"),
                                     ("8", "Moderate"), ("10", "Not important")]),
    ("Have you borrowed money to invest?", [("2", "Yes, heavily"), ("6", "Yes, small amount"),
                                             ("8", "Never"), ("10", "Never, and avoid leverage")]),
]

BANDS = {
    "Conservative": {"label": "Conservative", "color": "#1b9aaa", "min_vol": 0.0, "max_vol": 0.11,
                     "cap": 0.16, "blurb": "Capital preservation with low volatility, bonds and gold."},
    "Balanced": {"label": "Balanced", "color": "#e8b84b", "min_vol": 0.07, "max_vol": 0.18,
                 "cap": 0.15, "blurb": "A measured mix of equity, gold and debt for steady growth."},
    "Aggressive": {"label": "Aggressive", "color": "#d9603b", "min_vol": 0.12, "max_vol": 0.32,
                   "cap": 0.14, "blurb": "Growth-oriented, equity-heavy allocation with higher volatility."},
}

_CACHE = {}


def categorize(score):
    if score < 40:
        return "Conservative"
    if score <= 70:
        return "Balanced"
    return "Aggressive"


def score_answers(form):
    values = []
    for index, (_, options) in enumerate(QUESTIONS):
        raw = form.get(f"q_{index}", "0")
        allowed = [int(v) for v, _ in options]
        values.append(int(raw) if int(raw) in allowed else int(allowed[0]))
    return min(100, sum(values))


def _features(close, returns, t):
    r5 = close[t] / close[t - 5] - 1.0 if t >= 5 else 0.0
    r20 = close[t] / close[t - 20] - 1.0 if t >= 20 else 0.0
    vol20 = float(np.std(returns[t - 19:t + 1])) * math.sqrt(252) if t >= 19 else 0.0
    mom60 = close[t] / close[t - 60] - 1.0 if t >= 60 else 0.0
    window = returns[max(0, t - 13):t + 1]
    gains = float(np.sum(window[window > 0]))
    losses = float(-np.sum(window[window < 0]))
    rsi = 100.0 if losses == 0 else 100.0 - 100.0 / (1.0 + gains / losses)
    return [r5, r20, vol20, mom60, rsi / 100.0]


def _market_matrices(session):
    assets = session.query(Asset).filter(Asset.symbol != "CASH").order_by(Asset.id).all()
    ids = [a.id for a in assets]
    rows = session.query(PriceHistory.asset_id, PriceHistory.date, PriceHistory.close)\
        .filter(PriceHistory.asset_id.in_(ids)).order_by(PriceHistory.date, PriceHistory.asset_id).all()
    by_date = {}
    for aid, d, c in rows:
        by_date.setdefault(d, {})[aid] = c
    dates = sorted(by_date)
    close = np.array([[by_date[d].get(aid, np.nan) for aid in ids] for d in dates], dtype=float)
    for column in range(close.shape[1]):
        values = close[:, column]
        missing = np.isnan(values)
        if missing.any():
            index = np.where(~missing, np.arange(len(values)), 0)
            np.maximum.accumulate(index, out=index)
            values[missing] = values[index[missing]]
    returns = np.diff(close, axis=0) / close[:-1]
    return assets, dates[1:], close, returns


def expected_returns(session):
    assets, dates, close, returns = _market_matrices(session)
    newest = str(dates[-1])
    key = (newest, len(close))
    if key in _CACHE:
        return assets, dates, close, returns, _CACHE[key]
    samples, targets = [], []
    T, N = returns.shape
    for i in range(N):
        for t in range(60, T - 20, 4):
            samples.append(_features(close[:t + 1, i], returns[:t + 1, i], t))
            targets.append(close[t + 20, i] / close[t, i] - 1.0)
    X = np.asarray(samples, dtype=float)
    y = np.asarray(targets, dtype=float)
    latest = np.asarray([_features(close[:, i], returns[:, i], T - 1) for i in range(N)], dtype=float)
    if HAS_SKLEARN and len(y) > 400:
        model = RandomForestRegressor(n_estimators=60, max_depth=6, min_samples_leaf=25,
                                      random_state=42, n_jobs=-1)
        model.fit(X, y)
        predicted = model.predict(latest)
        method = "Random Forest + Markowitz"
    else:
        predicted = 0.018 + 0.28 * latest[:, 3] - 0.22 * latest[:, 2] + 0.05 * latest[:, 1]
        method = "Momentum Signal + Markowitz"
    mu = np.clip(0.08 + 6.0 * predicted, 0.01, 0.28)
    _CACHE[key] = (mu, method)
    return assets, dates, close, returns, (mu, method)


def optimize(mu, cov, category, cap):
    band = BANDS[category]
    n = len(mu)
    rng = np.random.default_rng(42)
    weights = rng.dirichlet(np.ones(n) * 0.55, size=22000)
    for _ in range(3):
        weights = np.minimum(weights, cap)
        weights /= weights.sum(axis=1, keepdims=True)
    expected = weights @ mu
    variance = np.einsum("ij,jk,ik->i", weights, cov, weights)
    volatility = np.sqrt(np.maximum(variance, 1e-12))
    sharpe = (expected - 0.065) / volatility
    mask = (volatility >= band["min_vol"]) & (volatility <= band["max_vol"])
    if not mask.any():
        mask = np.ones(len(weights), dtype=bool)
    best = int(np.argmax(np.where(mask, sharpe, -np.inf)))
    w = weights[best].copy()
    best_sharpe = float(sharpe[best])
    for _ in range(3000):
        candidate = w + rng.normal(0, 0.012, n)
        candidate = np.clip(candidate, 0, cap)
        candidate /= candidate.sum()
        ret = float(candidate @ mu)
        vol = float(np.sqrt(max(candidate @ cov @ candidate, 1e-12)))
        if band["min_vol"] <= vol <= band["max_vol"]:
            cand_sharpe = (ret - 0.065) / vol
            if cand_sharpe > best_sharpe:
                w, best_sharpe = candidate, cand_sharpe
    w = np.round(w, 4)
    w[np.argmax(w)] += round(1.0 - float(w.sum()), 4)
    ret = float(w @ mu)
    vol = float(np.sqrt(max(w @ cov @ w, 1e-12)))
    sharpe_value = (ret - 0.065) / vol
    frontier = []
    for target in np.linspace(float(expected.min()), float(expected.max()), 28):
        idx = np.argmin(np.abs(expected - target))
        frontier.append({"vol": round(float(volatility[idx] * 100), 2),
                         "ret": round(float(expected[idx] * 100), 2)})
    frontier = sorted({(p["vol"], p["ret"]): p for p in frontier}.values(), key=lambda p: p["vol"])
    return w, {"expected_return": ret, "volatility": vol, "sharpe": sharpe_value}, frontier


def create_plan(session, portfolio, category, total_value):
    assets, dates, close, returns, (mu, method) = expected_returns(session)
    cov = np.cov(returns, rowvar=False) * 252.0
    cov = cov + np.eye(len(mu)) * 1e-5
    weights, metrics, frontier = optimize(mu, cov, category, BANDS[category]["cap"])
    plan = AllocationPlan(portfolio_id=portfolio.id, method=method, risk_category=category,
                          expected_return=metrics["expected_return"],
                          volatility=metrics["volatility"], sharpe=metrics["sharpe"])
    session.add(plan)
    session.flush()
    for asset, weight in zip(assets, weights):
        if weight <= 0:
            continue
        session.add(PlanWeight(plan_id=plan.id, asset_id=asset.id, weight=float(weight),
                               target_amount=float(weight * total_value)))
    session.commit()
    return plan, frontier


def compute_frontier(session, category):
    """Re-derive the (deterministic, seeded) risk/return cloud shown on the allocation page."""
    _, _, _, returns, (mu, _) = expected_returns(session)
    cov = np.cov(returns, rowvar=False) * 252.0 + np.eye(len(mu)) * 1e-5
    return optimize(mu, cov, category, BANDS[category]["cap"])[2]
