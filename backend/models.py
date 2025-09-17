# models.py
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, ForeignKey, Text, Date, JSON
)
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    fullname = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)  # can be FK to users.id if you want
    type = Column(String(50), nullable=False)  # livret / immo / action / autre
    label = Column(String(255), nullable=False)
    current_value = Column(Numeric(14,2))
    created_at = Column(DateTime, default=datetime.utcnow)

class AssetLivret(Base):
    __tablename__ = "assets_livret"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    bank = Column(String(255))
    balance = Column(Numeric(14,2), nullable=False)
    plafond = Column(Numeric(14,2))
    recurring_amount = Column(Numeric(14,2))
    recurring_frequency = Column(String(50))
    recurring_day = Column(Integer)

class AssetImmo(Base):
    __tablename__ = "assets_immo"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    property_type = Column(String(50))
    address = Column(Text)
    purchase_price = Column(Numeric(14,2), nullable=False)
    notary_fees = Column(Numeric(14,2))
    other_fees = Column(Numeric(14,2))
    down_payment = Column(Numeric(14,2))
    loan_amount = Column(Numeric(14,2))
    loan_rate = Column(Numeric(5,3))
    loan_duration_months = Column(Integer)
    insurance_monthly = Column(Numeric(14,2))
    loan_start_date = Column(Date)
    monthly_payment = Column(Numeric(14,2))
    rental_income = Column(Numeric(14,2))

class AssetPortfolio(Base):
    __tablename__ = "assets_portfolio"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    product_type = Column(String(50))  # PEA/CTO/AV/PER
    broker = Column(String(255))
    initial_investment = Column(Numeric(14,2))
    recurring_amount = Column(Numeric(14,2))
    recurring_frequency = Column(String(50))
    recurring_day = Column(Integer)

class PortfolioLine(Base):
    __tablename__ = "portfolio_lines"
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("assets_portfolio.id", ondelete="CASCADE"), nullable=False)
    isin = Column(String(50))
    label = Column(String(255))
    units = Column(Numeric(14,4))
    amount_invested = Column(Numeric(14,2))
    purchase_date = Column(Date)

class AssetOther(Base):
    __tablename__ = "assets_other"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(50))
    description = Column(Text)
    estimated_value = Column(Numeric(14,2))
    platform = Column(String(255))
    wallet_address = Column(String(255))

class UserIncome(Base):
    __tablename__ = "user_income"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False) # FK optional
    label = Column(String(255))
    amount = Column(Numeric(14,2), nullable=False)
    frequency = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)

class UserExpense(Base):
    __tablename__ = "user_expenses"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    label = Column(String(255))
    amount = Column(Numeric(14,2), nullable=False)
    frequency = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
