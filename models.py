# models.py
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, ForeignKey, Text, Date, Boolean, BigInteger,
    UniqueConstraint, Enum, Index  # ✅ AJOUT
)

from datetime import datetime


Base = declarative_base()

# ========================
# Utilisateurs
# ========================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    fullname = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    # ✅ colonnes sécurité
    use_pin = Column(Boolean, default=False)
    use_biometrics = Column(Boolean, default=False)


# ========================
# Bénéficiaires
# ========================
class Beneficiary(Base):
    __tablename__ = "beneficiaries"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fullname = Column(String(255), nullable=False)
    relation = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)


# ========================
# Actifs génériques
# ========================
class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(50), nullable=False)  # livret / immo / portfolio / other
    label = Column(String(255), nullable=False)
    current_value = Column(Numeric(14, 2))
    created_at = Column(DateTime, default=datetime.utcnow)

    beneficiary_id = Column(Integer, ForeignKey("beneficiaries.id", ondelete="SET NULL"), nullable=True)

    # relations (facultatives selon type)
    livret = relationship("AssetLivret", uselist=False, back_populates="asset", cascade="all, delete-orphan")
    immo = relationship("AssetImmo", uselist=False, back_populates="asset", cascade="all, delete-orphan")
    portfolio = relationship("AssetPortfolio", uselist=False, back_populates="asset", cascade="all, delete-orphan")
    other = relationship("AssetOther", uselist=False, back_populates="asset", cascade="all, delete-orphan")


# ========================
# Livrets
# ========================
class AssetLivret(Base):
    __tablename__ = "assets_livret"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    bank = Column(String(255))
    balance = Column(Numeric(14, 2), nullable=False)
    plafond = Column(Numeric(14, 2))
    recurring_amount = Column(Numeric(14, 2))
    recurring_frequency = Column(String(20))  # mensuel / trimestriel / annuel
    recurring_day = Column(Integer)

    asset = relationship("Asset", back_populates="livret")


# ========================
# Immobilier
# ========================
class AssetImmo(Base):
    __tablename__ = "assets_immo"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    property_type = Column(String(50))
    address = Column(Text)
    purchase_price = Column(Numeric(14, 2), nullable=False)
    notary_fees = Column(Numeric(14, 2))
    other_fees = Column(Numeric(14, 2))
    down_payment = Column(Numeric(14, 2))
    loan_amount = Column(Numeric(14, 2))
    loan_rate = Column(Numeric(5, 3))
    loan_duration_months = Column(Integer)
    insurance_monthly = Column(Numeric(14, 2))
    loan_start_date = Column(Date)
    monthly_payment = Column(Numeric(14, 2))
    rental_income = Column(Numeric(14, 2))

    # nouveaux champs
    last_estimation_value = Column(Numeric(14, 2))
    ownership_percentage = Column(Numeric(5, 2))
    is_rented = Column(Boolean, default=False)

    # ✅ lien direct vers UserIncome
    income_id = Column(Integer, ForeignKey("user_income.id", ondelete="SET NULL"), nullable=True)

    asset = relationship("Asset", back_populates="immo")
    loans = relationship("ImmoLoan", back_populates="immo", cascade="all, delete-orphan")
    expenses = relationship("ImmoExpense", back_populates="immo", cascade="all, delete-orphan")


class ImmoLoan(Base):
    __tablename__ = "immo_loans"
    id = Column(Integer, primary_key=True)
    immo_id = Column(Integer, ForeignKey("assets_immo.id", ondelete="CASCADE"), nullable=False)
    loan_amount = Column(Numeric(14, 2))
    loan_rate = Column(Numeric(5, 3))
    loan_duration_months = Column(Integer)
    loan_start_date = Column(Date)
    monthly_payment = Column(Numeric(14, 2))
    # ❌ supprimé : income_id

    immo = relationship("AssetImmo", back_populates="loans")


class ImmoExpense(Base):
    __tablename__ = "immo_expenses"
    id = Column(Integer, primary_key=True)
    immo_id = Column(Integer, ForeignKey("assets_immo.id", ondelete="CASCADE"), nullable=False)
    expense_type = Column(String(50))
    amount = Column(Numeric(14, 2))
    frequency = Column(String(20))  # mensuel / annuel / etc.

    immo = relationship("AssetImmo", back_populates="expenses")


# ========================
# Portefeuilles
# ========================
class AssetPortfolio(Base):
    __tablename__ = "assets_portfolio"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    broker = Column(String(255))
    initial_investment = Column(Numeric(14, 2))
    recurring_amount = Column(Numeric(14, 2))
    recurring_frequency = Column(String(50))
    recurring_day = Column(Integer)

    asset = relationship("Asset", back_populates="portfolio")
    products = relationship("PortfolioProduct", back_populates="portfolio", cascade="all, delete-orphan")
    lines = relationship("PortfolioLine", back_populates="portfolio", cascade="all, delete-orphan")
    transactions = relationship("PortfolioTransaction", back_populates="portfolio", cascade="all, delete-orphan")  # ✅ ICI


class PortfolioProduct(Base):
    __tablename__ = "portfolio_products"
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("assets_portfolio.id", ondelete="CASCADE"), nullable=False)
    product_type = Column(String(50), nullable=False)  # PEA / CTO / AV / PER

    portfolio = relationship("AssetPortfolio", back_populates="products")


class PortfolioLine(Base):
    __tablename__ = "portfolio_lines"
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("assets_portfolio.id", ondelete="CASCADE"), nullable=False)
    isin = Column(String(50))
    label = Column(String(255))
    units = Column(Numeric(14, 4))
    amount_allocated = Column(Numeric(14, 2))
    allocation_frequency = Column(String(20))
    purchase_date = Column(Date)

    # ✅ nouveaux / corrigés
    avg_price = Column(Numeric(14, 4))             # PRU semé depuis TR
    date_option = Column(String(10))
    beneficiary_id = Column(Integer, ForeignKey("beneficiaries.id", ondelete="SET NULL"), nullable=True)

    # ✅ pour typer la ligne (PEA/CTO/…)
    product_id = Column(Integer, ForeignKey("portfolio_products.id", ondelete="SET NULL"), nullable=True)
    product = relationship("PortfolioProduct")

    portfolio = relationship("AssetPortfolio", back_populates="lines")
    beneficiary = relationship("Beneficiary")



class PortfolioTransaction(Base):
    __tablename__ = "portfolio_transactions"
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("assets_portfolio.id", ondelete="CASCADE"), nullable=False)
    isin = Column(String(50))
    label = Column(String(255))
    transaction_type = Column(String(50))  # buy / sell / dividend
    quantity = Column(Numeric(14,4))
    amount = Column(Numeric(14,2))
    date = Column(Date, nullable=False)

    portfolio = relationship("AssetPortfolio", back_populates="transactions")

# ========================
# Autres actifs
# ========================
class AssetOther(Base):
    __tablename__ = "assets_other"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(50))
    description = Column(Text)
    estimated_value = Column(Numeric(14, 2))
    platform = Column(String(255))
    wallet_address = Column(String(255))

    asset = relationship("Asset", back_populates="other")


# ========================
# Revenus & charges
# ========================
class UserIncome(Base):
    __tablename__ = "user_income"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    label = Column(String(255))
    amount = Column(Numeric(14, 2), nullable=False)
    frequency = Column(String(50))
    end_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserExpense(Base):
    __tablename__ = "user_expenses"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    label = Column(String(255))
    amount = Column(Numeric(14, 2), nullable=False)
    frequency = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)

class ProduitInvest(Base):
    __tablename__ = "produits_invest"
    id = Column(Integer, primary_key=True)
    isin = Column(String(20), unique=True, nullable=True)
    ticker_yahoo = Column(String(50))
    label = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False)   # action / etf / fonds
    eligible_in = Column(JSONB)                 # ex: ["PEA","CTO","PER","AV"]
    currency = Column(String(10))
    market = Column(String(100))
    sector = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    histo = relationship("ProduitHisto", back_populates="produit", cascade="all, delete-orphan")
    intraday = relationship("ProduitIntraday", back_populates="produit", cascade="all, delete-orphan")
    indicateurs = relationship("ProduitIndicateurs", back_populates="produit", cascade="all, delete-orphan")


class ProduitHisto(Base):
    __tablename__ = "produits_histo"
    id = Column(BigInteger, primary_key=True)
    produit_id = Column(Integer, ForeignKey("produits_invest.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Numeric(14, 4))
    high = Column(Numeric(14, 4))
    low = Column(Numeric(14, 4))
    close = Column(Numeric(14, 4))
    volume = Column(BigInteger)

    produit = relationship("ProduitInvest", back_populates="histo")


class ProduitIntraday(Base):
    __tablename__ = "produits_intraday"
    id = Column(BigInteger, primary_key=True)
    produit_id = Column(Integer, ForeignKey("produits_invest.id", ondelete="CASCADE"), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)
    price = Column(Numeric(14, 4))
    volume = Column(BigInteger)

    produit = relationship("ProduitInvest", back_populates="intraday")


class ProduitIndicateurs(Base):
    __tablename__ = "produits_indicateurs"
    id = Column(BigInteger, primary_key=True)
    produit_id = Column(Integer, ForeignKey("produits_invest.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    ma20 = Column(Numeric(14, 4))
    ma50 = Column(Numeric(14, 4))
    rsi14 = Column(Numeric(6, 2))
    macd = Column(Numeric(14, 4))
    signal = Column(Numeric(14, 4))

    produit = relationship("ProduitInvest", back_populates="indicateurs")

class BrokerLink(Base):
    __tablename__ = "broker_links"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    broker = Column(String(50), nullable=False)
    phone_e164 = Column(String(32), nullable=False)
    pin_enc = Column(Text, nullable=True)  # ✅ au lieu de String
    remember_pin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint('user_id', 'broker', name='uq_user_broker'),)

class AssetEvent(Base):
    __tablename__ = "asset_events"

    id = Column(Integer, primary_key=True)

    user_id  = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)

    kind = Column(Enum(
        "cash_op", "transfer", "portfolio_trade", "dividend",
        "allocation_change", "loan_prepayment", "rent_change",
        "expense_change", "valuation_adjustment", "other",
        name="event_kind"
    ), nullable=False)

    status = Column(Enum("planned","posted","cancelled", name="event_status"),
                    nullable=False, default="posted")

    value_date = Column(Date, nullable=False)
    rrule      = Column(String)     # ex: "FREQ=MONTHLY;BYMONTHDAY=1"
    end_date   = Column(Date)

    # Montants / quantités (facultatifs selon kind)
    amount     = Column(Numeric(14, 2))   # cash ±
    quantity   = Column(Numeric(20, 6))   # trades
    unit_price = Column(Numeric(14, 4))

    # Ciblage portefeuille
    isin               = Column(String(12), index=True)
    portfolio_line_id  = Column(Integer, ForeignKey("portfolio_lines.id"))

    # Transferts
    target_asset_id    = Column(Integer, ForeignKey("assets.id"))
    transfer_group_id  = Column(String(36), index=True)  # UUID string

    # Métadonnées
    category = Column(String(50))   # depot/retrait/frais/interets...
    note     = Column(Text)
    data     = Column(JSONB, default=dict)  # champs libres

    # Traçabilité côté "écritures spécialisées" (ex: PortfolioTransaction)
    posted_entity_type = Column(String(50))
    posted_entity_id   = Column(Integer)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relations utiles
    asset         = relationship("Asset", foreign_keys=[asset_id])
    target_asset  = relationship("Asset", foreign_keys=[target_asset_id])
    portfolio_line = relationship("PortfolioLine")

    __table_args__ = (
        Index("ix_asset_events_user_asset_date", "user_id", "asset_id", "value_date"),
        Index("ix_asset_events_status_kind", "status", "kind"),
    )