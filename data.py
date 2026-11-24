from datetime import date, timedelta

import numpy as np
from sqlalchemy import func

from models import Asset, PriceHistory

ASSET_SEED = [
    ("CASH", "Cash Reserve", "Cash", "Cash", 0.065, 0.001, 1.0),
    ("RELIANCE", "Reliance Industries", "Energy", "Equity", 0.13, 0.28, 2450.0),
    ("TCS", "Tata Consultancy Services", "Technology", "Equity", 0.14, 0.24, 3350.0),
    ("HDFCBANK", "HDFC Bank", "Finance", "Equity", 0.13, 0.26, 1550.0),
    ("INFY", "Infosys", "Technology", "Equity", 0.14, 0.27, 1450.0),
    ("ICICIBANK", "ICICI Bank", "Finance", "Equity", 0.14, 0.29, 1050.0),
    ("SBIN", "State Bank of India", "Finance", "Equity", 0.12, 0.31, 620.0),
    ("BHARTIARTL", "Bharti Airtel", "Telecom", "Equity", 0.15, 0.30, 890.0),
    ("ITC", "ITC Limited", "FMCG", "Equity", 0.11, 0.22, 430.0),
    ("KOTAKBANK", "Kotak Mahindra Bank", "Finance", "Equity", 0.13, 0.27, 1780.0),
    ("LT", "Larsen & Toubro", "Infrastructure", "Equity", 0.13, 0.26, 2950.0),
    ("HINDUNILVR", "Hindustan Unilever", "FMCG", "Equity", 0.10, 0.20, 2450.0),
    ("ASIANPAINT", "Asian Paints", "FMCG", "Equity", 0.11, 0.24, 2980.0),
    ("MARUTI", "Maruti Suzuki", "Automobile", "Equity", 0.12, 0.29, 9800.0),
    ("TITAN", "Titan Company", "Consumer", "Equity", 0.15, 0.30, 3200.0),
    ("BAJFINANCE", "Bajaj Finance", "Finance", "Equity", 0.16, 0.34, 6800.0),
    ("NIFTYBEES", "Nifty 50 Index ETF", "Index", "ETF", 0.12, 0.18, 230.0),
    ("GOLDBEES", "Gold ETF", "Gold", "Commodity", 0.09, 0.14, 58.0),
    ("SGB", "Sovereign Gold Bond", "Gold", "Bond", 0.08, 0.11, 520.0),
    ("BONDBEES", "Government Bond ETF", "Bonds", "Bond", 0.07, 0.06, 62.0),
    ("LIQUIDBEES", "Liquid ETF", "Cash", "ETF", 0.065, 0.01, 1000.0),
]

TRADING_DAYS = 1260


def business_dates(count, end=None):
    end = end or date.today()
    dates = np.busday_offset(np.datetime64(end, "D"), -(count - 1), roll="backward")
    days = np.busday_offset(dates, np.arange(count), roll="forward")
    return [date.fromisoformat(str(d)[:10]) for d in days]


def make_prices(asset, days):
    rng = np.random.default_rng(sum(ord(c) for c in asset.symbol))
    if asset.symbol == "CASH":
        close = np.ones(len(days), dtype=float)
    else:
        daily_mu = asset.expected_drift / 252.0
        daily_sigma = asset.volatility / np.sqrt(252.0)
        shocks = rng.normal(daily_mu - 0.5 * daily_sigma ** 2, daily_sigma, len(days))
        close = np.maximum(asset.base_price * np.exp(np.cumsum(shocks)), 0.01)
        close = np.round(close, 2)
    previous = np.concatenate(([close[0]], close[:-1]))
    opens = np.round(previous * (1 + rng.normal(0, 0.002, len(days))), 2)
    highs = np.round(np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.004, len(days)))), 2)
    lows = np.round(np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.004, len(days)))), 2)
    volume = rng.integers(100000, 9000000, len(days)) if asset.symbol != "CASH" else np.zeros(len(days), dtype=int)
    return list(zip(days, opens, highs, lows, close, volume))


def seed_database(session):
    if session.query(func.count(Asset.id)).scalar():
        return False
    days = business_dates(TRADING_DAYS)
    assets = []
    for symbol, name, sector, asset_class, drift, vol, base in ASSET_SEED:
        assets.append(Asset(symbol=symbol, name=name, sector=sector, asset_class=asset_class,
                            expected_drift=drift, volatility=vol, base_price=base))
    session.add_all(assets)
    session.flush()
    rows = []
    for asset in assets:
        for d, o, h, l, c, v in make_prices(asset, days):
            rows.append(PriceHistory(asset_id=asset.id, date=d, open=o, high=h, low=l, close=c, volume=int(v)))
    for i in range(0, len(rows), 1500):
        session.add_all(rows[i:i + 1500])
        session.flush()
    session.commit()
    return True


def extend_market_data(session):
    assets = session.query(Asset).all()
    last_date = session.query(func.max(PriceHistory.date)).scalar()
    next_day = last_date + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    rng = np.random.default_rng(int(next_day.strftime("%Y%m%d")))
    added = 0
    for asset in assets:
        exists = session.query(PriceHistory.id).filter_by(asset_id=asset.id, date=next_day).first()
        if exists:
            continue
        previous = session.query(PriceHistory.close).filter_by(asset_id=asset.id)\
            .order_by(PriceHistory.date.desc()).first()[0]
        if asset.symbol == "CASH":
            close = 1.0
        else:
            sigma = asset.volatility / np.sqrt(252.0)
            close = max(0.01, round(previous * np.exp(rng.normal(asset.expected_drift / 252.0 - 0.5 * sigma ** 2, sigma)), 2))
        session.add(PriceHistory(asset_id=asset.id, date=next_day, open=previous, high=max(previous, close),
                                 low=min(previous, close), close=close, volume=int(rng.integers(100000, 9000000))))
        added += 1
    session.commit()
    return next_day, added


def latest_prices(session):
    rows = session.query(PriceHistory.asset_id, PriceHistory.close).filter(
        PriceHistory.date == session.query(func.max(PriceHistory.date)).scalar()
    ).all()
    return {asset_id: close for asset_id, close in rows}


def price_series(session, asset_id, limit=90):
    rows = session.query(PriceHistory.date, PriceHistory.close)\
        .filter(PriceHistory.asset_id == asset_id)\
        .order_by(PriceHistory.date.desc()).limit(limit).all()
    return [(d.isoformat(), c) for d, c in reversed(rows)]
