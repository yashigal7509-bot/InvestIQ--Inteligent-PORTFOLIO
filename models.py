from datetime import datetime

from sqlalchemy import (
    Column, Date, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(160), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    profile = relationship("RiskProfile", back_populates="user", uselist=False)
    portfolios = relationship("Portfolio", back_populates="user")


class RiskProfile(Base):
    __tablename__ = "risk_profiles"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    score = Column(Integer, nullable=False)
    category = Column(String(20), nullable=False)
    horizon = Column(String(40), nullable=False)
    answers = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="profile")


class Asset(Base):
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False, unique=True)
    name = Column(String(120), nullable=False)
    sector = Column(String(60), nullable=False)
    asset_class = Column(String(30), nullable=False)
    expected_drift = Column(Float, default=0.10)
    volatility = Column(Float, default=0.20)
    base_price = Column(Float, default=100.0)

    prices = relationship("PriceHistory", back_populates="asset")


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (UniqueConstraint("asset_id", "date", name="uq_asset_date"),)

    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, default=0)

    asset = relationship("Asset", back_populates="prices")


class Portfolio(Base):
    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(120), nullable=False, default="Main Portfolio")
    cash = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="portfolios")
    holdings = relationship("Holding", back_populates="portfolio")
    transactions = relationship("Transaction", back_populates="portfolio")
    plans = relationship("AllocationPlan", back_populates="portfolio")


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("portfolio_id", "asset_id", name="uq_portfolio_asset"),)

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    quantity = Column(Float, nullable=False, default=0.0)
    avg_cost = Column(Float, nullable=False, default=0.0)

    portfolio = relationship("Portfolio", back_populates="holdings")
    asset = relationship("Asset")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    side = Column(String(4), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    executed_at = Column(DateTime, default=datetime.utcnow)

    portfolio = relationship("Portfolio", back_populates="transactions")
    asset = relationship("Asset")


class AllocationPlan(Base):
    __tablename__ = "allocation_plans"

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    method = Column(String(60), nullable=False)
    risk_category = Column(String(20), nullable=False)
    expected_return = Column(Float, nullable=False)
    volatility = Column(Float, nullable=False)
    sharpe = Column(Float, nullable=False)

    portfolio = relationship("Portfolio", back_populates="plans")
    weights = relationship("PlanWeight", back_populates="plan")


class PlanWeight(Base):
    __tablename__ = "plan_weights"
    __table_args__ = (UniqueConstraint("plan_id", "asset_id", name="uq_plan_asset"),)

    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("allocation_plans.id"), nullable=False)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    weight = Column(Float, nullable=False)
    target_amount = Column(Float, nullable=False)

    plan = relationship("AllocationPlan", back_populates="weights")
    asset = relationship("Asset")


class RebalanceLog(Base):
    __tablename__ = "rebalance_logs"

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    current_weight = Column(Float, nullable=False)
    target_weight = Column(Float, nullable=False)
    drift = Column(Float, nullable=False)
    side = Column(String(4), nullable=False)
    quantity = Column(Float, nullable=False)
    executed_at = Column(DateTime, default=datetime.utcnow)

    portfolio = relationship("Portfolio")
    asset = relationship("Asset")
