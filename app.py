# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from models import (
    Base, User, Beneficiary, Asset, AssetLivret, AssetImmo, AssetPortfolio, PortfolioLine,
    AssetOther, UserIncome, UserExpense, PortfolioProduct, ImmoLoan, ImmoExpense,
    ProduitInvest, ProduitHisto, ProduitIndicateurs, ProduitIntraday, PortfolioTransaction,
    BrokerLink,  # ✅ ajout
)


from utils import amortization_monthly_payment
from datetime import datetime, timedelta
import traceback
from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from sqlalchemy.exc import SQLAlchemyError
import json
from sqlalchemy import and_
from scraper_tr import connect as tr_connect_api, validate_2fa as tr_validate_api, fetch_data as tr_fetch_api
import logging
import sys
from cryptography.fernet import Fernet, InvalidToken

# Configure root logger
logging.basicConfig(
    level=logging.DEBUG,  # ou INFO si tu veux moins verbeux
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)  # affiche sur stdout -> Render
    ]
)


# Charger variables d’environnement
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL required in env")

# Config DB
engine = create_engine(DATABASE_URL, echo=False, future=True, pool_pre_ping=True)  # ✅

Session = sessionmaker(bind=engine)

# App Flask
app = Flask(__name__)

# --- CORS ---
CORS(
    app,
    resources={r"/api/*": {"origins": [
        "http://localhost:8081",
        "https://ton-front.onrender.com"
    ]}},
    supports_credentials=True
)

bcrypt = Bcrypt(app)
app.config["JWT_SECRET_KEY"] = os.getenv("SECRET_KEY", "supersecret")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=12)
app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(days=30)
jwt = JWTManager(app)

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
_key = os.environ.get("BROKER_CRYPT_KEY")
if not _key:
    raise RuntimeError("BROKER_CRYPT_KEY missing")

# La clé doit être une base64 urlsafe de 32 octets (ex: Fernet.generate_key().decode())
try:
    _fernet = Fernet(_key.encode())  # ✅ toujours bytes
except Exception as e:
    raise RuntimeError("BROKER_CRYPT_KEY must be a 32-byte urlsafe base64 key") from e

def enc_secret(s: str) -> str:
    return _fernet.encrypt(s.encode()).decode("ascii")   # ✅ str

def dec_secret(token: str) -> str:
    return _fernet.decrypt(token.encode("ascii")).decode()  # ✅ str in -> str out


def parse_float(val):
    try:
        return float(val) if val is not None else None
    except Exception:
        return None

def parse_int(val):
    try:
        return int(val) if val is not None else None
    except Exception:
        return None

def parse_date(val):
    try:
        if not val:
            return None
        s = str(val).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).date()
    except Exception:
        return None

def get_or_create_product(session, pf_id, ptype):
    if not ptype:
        return None
    row = (session.query(PortfolioProduct)
           .filter_by(portfolio_id=pf_id, product_type=ptype)
           .first())
    if row:
        return row
    row = PortfolioProduct(portfolio_id=pf_id, product_type=ptype)
    session.add(row); session.flush()
    return row



def map_tr_product_type(s: str):
    if not s: return None
    s = s.lower()
    if "pea" in s: return "PEA"
    if "per" in s: return "PER"
    if "life" in s or "assurance" in s: return "AV"
    # TR utilise souvent "securities" pour CTO
    if "securities" in s or "cto" in s: return "CTO"
    return "CTO"


def serialize_asset(asset: Asset, session) -> dict:
    base = {
        "id": asset.id,
        "user_id": asset.user_id,
        "type": asset.type,
        "label": asset.label,
        "current_value": float(asset.current_value) if asset.current_value is not None else None,
        "beneficiary_id": asset.beneficiary_id,
        "created_at": asset.created_at.isoformat() if asset.created_at else None
    }

    if asset.type == "livret" and asset.livret:
        lv = asset.livret
        base["details"] = {
            "bank": lv.bank,
            "balance": float(lv.balance) if lv.balance is not None else None,
            "plafond": float(lv.plafond) if lv.plafond is not None else None,
            "recurring_amount": float(lv.recurring_amount) if lv.recurring_amount is not None else None,
            "recurring_frequency": lv.recurring_frequency,
            "recurring_day": lv.recurring_day
        }

    elif asset.type == "immo" and asset.immo:
        im = asset.immo
        base["details"] = {
            "property_type": im.property_type,
            "address": im.address,
            "purchase_price": float(im.purchase_price) if im.purchase_price is not None else None,
            "notary_fees": float(im.notary_fees) if im.notary_fees is not None else None,
            "other_fees": float(im.other_fees) if im.other_fees is not None else None,
            "down_payment": float(im.down_payment) if im.down_payment is not None else None,
            "loan_amount": float(im.loan_amount) if im.loan_amount is not None else None,
            "loan_rate": float(im.loan_rate) if im.loan_rate is not None else None,
            "loan_duration_months": im.loan_duration_months,
            "insurance_monthly": float(im.insurance_monthly) if im.insurance_monthly is not None else None,
            "loan_start_date": im.loan_start_date.isoformat() if im.loan_start_date else None,
            "monthly_payment": float(im.monthly_payment) if im.monthly_payment is not None else None,
            "rental_income": float(im.rental_income) if im.rental_income is not None else None,
            "income_id": im.income_id,
            "last_estimation_value": float(im.last_estimation_value) if im.last_estimation_value is not None else None,
            "ownership_percentage": float(im.ownership_percentage) if im.ownership_percentage is not None else None,
            "is_rented": bool(im.is_rented),
            "loans": [{
                "id": ln.id,
                "loan_amount": float(ln.loan_amount) if ln.loan_amount is not None else None,
                "loan_rate": float(ln.loan_rate) if ln.loan_rate is not None else None,
                "loan_duration_months": ln.loan_duration_months,
                "loan_start_date": ln.loan_start_date.isoformat() if ln.loan_start_date else None,
                "monthly_payment": float(ln.monthly_payment) if ln.monthly_payment is not None else None
            } for ln in im.loans],
            "expenses": [{
                "id": ex.id,
                "expense_type": ex.expense_type,
                "amount": float(ex.amount) if ex.amount is not None else None,
                "frequency": ex.frequency
            } for ex in im.expenses],
        }

        # 🔥 Injection des revenus liés
        incomes_list = []
        if im.income_id:
            inc = session.query(UserIncome).filter_by(id=im.income_id, user_id=asset.user_id).first()
            if inc:
                incomes_list.append({
                    "id": inc.id,
                    "label": inc.label,
                    "amount": float(inc.amount),
                    "frequency": inc.frequency,
                    "end_date": inc.end_date.isoformat() if inc.end_date else None
                })
        base["details"]["incomes"] = incomes_list

    elif asset.type == "portfolio" and asset.portfolio:
        pf = asset.portfolio
        base["details"] = {
            "broker": pf.broker,
            "initial_investment": float(pf.initial_investment) if pf.initial_investment is not None else None,
            "recurring_amount": float(pf.recurring_amount) if pf.recurring_amount is not None else None,
            "recurring_frequency": pf.recurring_frequency,
            "recurring_day": pf.recurring_day,
            "products": [{"id": p.id, "product_type": p.product_type} for p in pf.products],
            "lines": [{
                "id": ln.id,
                "isin": ln.isin,
                "label": ln.label,
                "units": float(ln.units) if ln.units is not None else None,
                "amount_allocated": float(ln.amount_allocated) if ln.amount_allocated is not None else None,
                "allocation_frequency": ln.allocation_frequency,
                "date_option": ln.date_option,                 # ✅
                "beneficiary_id": ln.beneficiary_id,           # ✅
                "purchase_date": ln.purchase_date.isoformat() if ln.purchase_date else None,
                "avg_price": float(ln.avg_price) if ln.avg_price is not None else None,
                "product_id": ln.product_id,
                "product_type": ln.product.product_type if ln.product else None
            } for ln in pf.lines]
        }

    elif asset.type == "other" and asset.other:
        oth = asset.other
        base["details"] = {
            "category": oth.category,
            "description": oth.description,
            "estimated_value": float(oth.estimated_value) if oth.estimated_value is not None else None,
            "platform": oth.platform,
            "wallet_address": oth.wallet_address
        }

    return base


# ---------------------------------------------------------
# Auth routes (inchangées)
# ---------------------------------------------------------
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    email, password = data.get("email"), data.get("password")
    fullname = data.get("fullname")

    if not email or not password:
        return jsonify({"ok": False, "error": "Email et mot de passe requis"}), 400

    session = Session()
    try:
        if session.query(User).filter_by(email=email).first():
            return jsonify({"ok": False, "error": "Utilisateur déjà existant"}), 400

        hashed = bcrypt.generate_password_hash(password).decode("utf-8")
        user = User(email=email, password_hash=hashed, fullname=fullname)
        session.add(user)
        session.commit()

        # register()
        token = create_access_token(identity=str(user.id))  # ✅ au lieu de user.id

        return jsonify({
            "ok": True,
            "token": token,
            "user": {"id": user.id, "email": user.email, "fullname": user.fullname}
        }), 201
    except SQLAlchemyError as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    email, password = data.get("email"), data.get("password")

    if not email or not password:
        return jsonify({"ok": False, "error": "Email et mot de passe requis"}), 400

    session = Session()
    try:
        user = session.query(User).filter_by(email=email).first()
        if not user or not bcrypt.check_password_hash(user.password_hash, password):
            return jsonify({"ok": False, "error": "Identifiants invalides"}), 401

        token = create_access_token(identity=str(user.id))
        return jsonify({
            "ok": True,
            "token": token,
            "user": {"id": user.id, "email": user.email, "fullname": user.fullname}
        }), 200
    finally:
        session.close()


@app.route("/api/auth/me", methods=["GET"])
@jwt_required()
def me():
    user_id = int(get_jwt_identity())
    session = Session()
    try:
        user = session.query(User).get(user_id)
        if not user:
            return jsonify({"ok": False, "error": "Utilisateur non trouvé"}), 404
        return jsonify({
            "ok": True,
            "id": user.id,
            "email": user.email,
            "fullname": user.fullname,
            "use_pin": user.use_pin,
            "use_biometrics": user.use_biometrics
        }), 200
    finally:
        session.close()


@app.route("/api/users/me/security", methods=["PUT"])
@jwt_required()
def update_my_security():
    user_id = int(get_jwt_identity())
    payload = request.get_json() or {}
    use_pin = payload.get("use_pin", False)
    use_biometrics = payload.get("use_biometrics", False)

    session = Session()
    try:
        user = session.query(User).filter_by(id=user_id).first()
        if not user:
            return jsonify({"ok": False, "error": "User not found"}), 404

        user.use_pin = bool(use_pin)
        user.use_biometrics = bool(use_biometrics)
        session.commit()

        return jsonify({
            "ok": True,
            "use_pin": user.use_pin,
            "use_biometrics": user.use_biometrics
        }), 200
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


# ---------------------------------------------------------
# Incomes (inchangé)
# ---------------------------------------------------------
@app.route("/api/users/me/incomes", methods=["POST"])
@jwt_required()
def add_income():
    user_id = int(get_jwt_identity())
    payload = request.get_json() or {}

    label = payload.get("label")
    amount = parse_float(payload.get("amount"))
    frequency = payload.get("frequency")
    end_date = parse_date(payload.get("end_date"))

    if not label or not amount:
        return jsonify({"ok": False, "error": "Label et montant requis"}), 400

    session = Session()
    try:
        inc = UserIncome(
            user_id=user_id,
            label=label,
            amount=amount,
            frequency=frequency,
            end_date=end_date
        )
        session.add(inc)
        session.commit()
        return jsonify({
            "ok": True,
            "id": inc.id,
            "label": inc.label,
            "amount": float(inc.amount),
            "frequency": inc.frequency,
            "end_date": inc.end_date.isoformat() if inc.end_date else None
        }), 201
    finally:
        session.close()


@app.route("/api/users/me/incomes", methods=["GET"])
@jwt_required()
def list_incomes():
    user_id = int(get_jwt_identity())
    session = Session()
    try:
        rows = session.query(UserIncome).filter_by(user_id=user_id).all()
        return jsonify([{
            "id": r.id,
            "label": r.label,
            "amount": float(r.amount),
            "frequency": r.frequency,
            "end_date": r.end_date.isoformat() if r.end_date else None
        } for r in rows])
    finally:
        session.close()


@app.route("/api/users/me/income/<int:income_id>", methods=["PUT"])
@jwt_required()
def update_income(income_id):
    user_id = int(get_jwt_identity())
    payload = request.get_json() or {}

    session = Session()
    try:
        income = session.query(UserIncome).filter_by(id=income_id, user_id=user_id).first()
        if not income:
            return jsonify({"ok": False, "error": "Revenu introuvable"}), 404

        if "label" in payload:
            income.label = payload["label"]
        if "amount" in payload:
            income.amount = float(payload["amount"])
        if "frequency" in payload:
            income.frequency = payload["frequency"]
        if "end_date" in payload:
            income.end_date = datetime.fromisoformat(payload["end_date"]).date() if payload["end_date"] else None

        session.commit()
        return jsonify({
            "ok": True,
            "id": income.id,
            "label": income.label,
            "amount": float(income.amount),
            "frequency": income.frequency,
            "end_date": income.end_date.isoformat() if income.end_date else None
        }), 200
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/users/me/income/<int:income_id>", methods=["DELETE"])
@jwt_required()
def delete_income(income_id):
    user_id = int(get_jwt_identity())
    session = Session()
    try:
        income = session.query(UserIncome).filter_by(id=income_id, user_id=user_id).first()
        if not income:
            return jsonify({"ok": False, "error": "Revenu introuvable"}), 404

        session.delete(income)
        session.commit()
        return jsonify({"ok": True, "deleted_id": income_id}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


# ---------------------------------------------------------
# Beneficiaries (inchangé)
# ---------------------------------------------------------
@app.route("/api/users/me/beneficiaries", methods=["GET", "POST"])
@jwt_required()
def my_beneficiaries():
    user_id = int(get_jwt_identity())
    session = Session()
    try:
        if request.method == "GET":
            bens = session.query(Beneficiary).filter_by(user_id=user_id).all()
            return jsonify([{
                "id": b.id,
                "fullname": b.fullname,
                "relation": b.relation
            } for b in bens]), 200
        else:
            data = request.get_json() or {}
            fullname = data.get("fullname")
            relation = data.get("relation")
            if not fullname:
                return jsonify({"ok": False, "error": "Nom requis"}), 400
            ben = Beneficiary(user_id=user_id, fullname=fullname, relation=relation)
            session.add(ben)
            session.commit()
            return jsonify({
                "ok": True,
                "id": ben.id,
                "fullname": ben.fullname,
                "relation": ben.relation
            }), 201
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/users/me/beneficiaries/<int:ben_id>", methods=["PUT"])
@jwt_required()
def update_beneficiary(ben_id):
    user_id = int(get_jwt_identity())
    data = request.get_json() or {}
    session = Session()
    try:
        ben = session.query(Beneficiary).filter_by(id=ben_id, user_id=user_id).first()
        if not ben:
            return jsonify({"ok": False, "error": "Bénéficiaire introuvable"}), 404

        if "fullname" in data:
            ben.fullname = data["fullname"]
        if "relation" in data:
            ben.relation = data["relation"]

        session.commit()
        return jsonify({
            "ok": True,
            "id": ben.id,
            "fullname": ben.fullname,
            "relation": ben.relation
        }), 200
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/users/me/beneficiaries/<int:ben_id>", methods=["DELETE"])
@jwt_required()
def delete_beneficiary(ben_id):
    user_id = int(get_jwt_identity())
    session = Session()
    try:
        ben = session.query(Beneficiary).filter_by(id=ben_id, user_id=user_id).first()
        if not ben:
            return jsonify({"ok": False, "error": "Bénéficiaire introuvable"}), 404
        session.delete(ben)
        session.commit()
        return jsonify({"ok": True, "deleted_id": ben_id}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


# ---------------------------------------------------------
# Assets CRUD complets
# ---------------------------------------------------------

# Payloads attendus (exemples — détails dans serialize / parsing) :
# type=livret:
# { "type":"livret", "label":"Livret A", "current_value":1234.56,
#   "details": { "bank":"CA", "balance":1234.56, "plafond":22950,
#                "recurring_amount":50, "recurring_frequency":"mensuel", "recurring_day":5 } }
#
# type=portfolio:
# { "type":"portfolio", "label":"Mes comptes titres",
#   "details": { "broker":"Degiro", "initial_investment":null,
#                "recurring_amount":100, "recurring_frequency":"mensuel", "recurring_day":1,
#                "products":[{"product_type":"PEA"},{"product_type":"CTO"}],
#                "lines":[{"isin":"FR0000131104","label":"LVMH","units":2.5,
#                          "amount_allocated":500,"allocation_frequency":"mensuel","purchase_date":"2024-01-10"}] } }
#
# type=immo:
# { "type":"immo", "label":"Studio Paris",
#   "details": { "property_type":"locatif","address":"...","purchase_price":220000, ...,
#                "last_estimation_value":240000,"ownership_percentage":100,"is_rented":true,
#                "loans":[{"loan_amount":180000,"loan_rate":2.2,"loan_duration_months":300,
#                          "loan_start_date":"2022-06-01","monthly_payment":750,"income_id":12}],
#                "expenses":[{"expense_type":"assurance","amount":20,"frequency":"mensuel"}] } }
#
# type=other:
# { "type":"other", "label":"Pièces d'or", "details": { "category":"or","description":"...", "estimated_value":3500 } }

@app.route("/api/assets", methods=["POST"])
@jwt_required()
def add_asset():
    user_id = int(get_jwt_identity())
    payload = request.get_json(silent=True) or {}
    required = ["type", "label"]
    for r in required:
        if r not in payload:
            return jsonify({"error": f"{r} required"}), 400

    type_ = payload["type"]
    label = payload["label"]
    current_value = parse_float(payload.get("current_value"))
    details = payload.get("details", {}) or {}
    beneficiary_id = payload.get("beneficiary_id")

    session = Session()
    try:
        asset = Asset(
            user_id=user_id,
            type=type_,
            label=label,
            current_value=current_value,
            beneficiary_id=beneficiary_id
        )
        session.add(asset)
        session.flush()

        # Livret
        if type_ == "livret":
            lv = AssetLivret(
                asset_id=asset.id,
                bank=details.get("bank"),
                balance=parse_float(details.get("balance")) or 0,
                plafond=parse_float(details.get("plafond")),
                recurring_amount=parse_float(details.get("recurring_amount")),
                recurring_frequency=details.get("recurring_frequency"),
                recurring_day=parse_int(details.get("recurring_day"))
            )
            session.add(lv)

        # Immobilier
        elif type_ == "immo":
            loan_amount = parse_float(details.get("loan_amount"))
            loan_rate = parse_float(details.get("loan_rate"))
            loan_duration_months = parse_int(details.get("loan_duration_months"))
            monthly_payment = parse_float(details.get("monthly_payment"))

            if not monthly_payment and loan_amount and (loan_rate is not None) and loan_duration_months:
                try:
                    monthly_payment = amortization_monthly_payment(
                        loan_amount, loan_rate, loan_duration_months
                    )
                except Exception:
                    monthly_payment = None

            im = AssetImmo(
                asset_id=asset.id,
                property_type=details.get("property_type"),
                address=details.get("address"),
                purchase_price=parse_float(details.get("purchase_price")),
                notary_fees=parse_float(details.get("notary_fees")),
                other_fees=parse_float(details.get("other_fees")),
                down_payment=parse_float(details.get("down_payment")),
                loan_amount=loan_amount,
                loan_rate=loan_rate,
                loan_duration_months=loan_duration_months,
                insurance_monthly=parse_float(details.get("insurance_monthly")),
                loan_start_date=parse_date(details.get("loan_start_date")),
                monthly_payment=monthly_payment,
                income_id=parse_int(details.get("income_id")),  # ✅ nouvel emplacement
                rental_income=parse_float(details.get("rental_income")),
                last_estimation_value=parse_float(details.get("last_estimation_value")),
                ownership_percentage=parse_float(details.get("ownership_percentage")),
                is_rented=bool(details.get("is_rented"))
            )
            session.add(im)
            session.flush()

            # prêts multiples
            for ln in details.get("loans", []) or []:
                loan = ImmoLoan(
                    immo_id=im.id,
                    loan_amount=parse_float(ln.get("loan_amount")),
                    loan_rate=parse_float(ln.get("loan_rate")),
                    loan_duration_months=parse_int(ln.get("loan_duration_months")),
                    loan_start_date=parse_date(ln.get("loan_start_date")),
                    monthly_payment=parse_float(ln.get("monthly_payment"))
                )
                session.add(loan)

            # frais multiples
            for ex in details.get("expenses", []) or []:
                expense = ImmoExpense(
                    immo_id=im.id,
                    expense_type=ex.get("expense_type"),
                    amount=parse_float(ex.get("amount")),
                    frequency=ex.get("frequency")
                )
                session.add(expense)

        # Portefeuille
        elif type_ == "portfolio":
            pf = AssetPortfolio(
                asset_id=asset.id,
                broker=details.get("broker"),
                initial_investment=parse_float(details.get("initial_investment")),
                recurring_amount=parse_float(details.get("recurring_amount")),
                recurring_frequency=details.get("recurring_frequency"),
                recurring_day=parse_int(details.get("recurring_day"))
            )
            session.add(pf)
            session.flush()

            # produits
            for prod in details.get("products", []) or []:
                p = PortfolioProduct(
                    portfolio_id=pf.id,
                    product_type=prod.get("product_type")
                )
                session.add(p)

            # lignes
            for ln in details.get("lines", []) or []:
                # priorité: product_id explicite > product_type (string) > rien
                prod = None
                pid = ln.get("product_id")
                ptype = ln.get("product_type")
                if pid:
                    prod = session.query(PortfolioProduct).filter_by(id=pid, portfolio_id=pf.id).first()
                elif ptype:
                    prod = get_or_create_product(session, pf.id, ptype)

                pl = PortfolioLine(
                    portfolio_id=pf.id,
                    isin=ln.get("isin"),
                    label=ln.get("label"),
                    units=parse_float(ln.get("units")),
                    avg_price=parse_float(ln.get("avg_price")),                 # ✅ PRU
                    amount_allocated=parse_float(ln.get("amount_allocated")),
                    allocation_frequency=ln.get("allocation_frequency"),
                    date_option=ln.get("date_option"),
                    beneficiary_id=parse_int(ln.get("beneficiary_id") or ln.get("beneficiaryId")),
                    purchase_date=parse_date(ln.get("purchase_date")),
                    product_id=(prod.id if prod else None)                      # ✅ lien produit
                )
                session.add(pl)

        # Autres
        elif type_ == "other":
            oth = AssetOther(
                asset_id=asset.id,
                category=details.get("category"),
                description=details.get("description"),
                estimated_value=parse_float(details.get("estimated_value")),
                platform=details.get("platform"),
                wallet_address=details.get("wallet_address")
            )
            session.add(oth)

        session.commit()
        return jsonify({"success": True, "asset_id": asset.id}), 201

    except Exception as e:
        session.rollback()
        print("❌ ERROR /api/assets (POST):", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        session.close()


@app.route("/api/assets", methods=["GET"])
@jwt_required()
def list_assets():
    user_id = int(get_jwt_identity())
    session = Session()
    try:
        rows = session.query(Asset).filter_by(user_id=user_id).all()
        return jsonify([serialize_asset(a, session) for a in rows]), 200
    finally:
        session.close()


@app.route("/api/assets/<int:asset_id>", methods=["GET"])
@jwt_required()
def get_asset(asset_id):
    user_id = int(get_jwt_identity())
    session = Session()
    try:
        asset = session.query(Asset).filter_by(id=asset_id, user_id=user_id).first()
        if not asset:
            return jsonify({"error": "Actif introuvable"}), 404
        return jsonify(serialize_asset(asset, session)), 200
    finally:
        session.close()


@app.route("/api/assets/<int:asset_id>", methods=["PUT"])
@jwt_required()
def update_asset(asset_id):
    user_id = int(get_jwt_identity())
    payload = request.get_json(silent=True) or {}
    session = Session()
    try:
        asset = session.query(Asset).filter_by(id=asset_id, user_id=user_id).first()
        if not asset:
            return jsonify({"error": "Actif introuvable"}), 404

        # champs génériques
        if "label" in payload:
            asset.label = payload["label"]
        if "current_value" in payload:
            asset.current_value = parse_float(payload["current_value"])
        if "beneficiary_id" in payload:
            asset.beneficiary_id = payload["beneficiary_id"]

        details = payload.get("details", None)

        # Mise à jour par type
        if asset.type == "livret" and details is not None:
            if not asset.livret:
                asset.livret = AssetLivret(asset_id=asset.id, balance=0)
            lv = asset.livret
            for k in ["bank", "recurring_frequency"]:
                if k in details:
                    setattr(lv, k, details[k])
            for k_num in ["balance", "plafond", "recurring_amount"]:
                if k_num in details:
                    setattr(lv, k_num, parse_float(details[k_num]))
            if "recurring_day" in details:
                lv.recurring_day = parse_int(details["recurring_day"])

        elif asset.type == "immo" and details is not None:
            if not asset.immo:
                asset.immo = AssetImmo(asset_id=asset.id, purchase_price=0)
            im = asset.immo
            # champs simples
            copy_simple = [
                "property_type", "address", "loan_duration_months", "recurring_frequency"
            ]
            for k in copy_simple:
                if k in details:
                    setattr(im, k, details[k])
            # numériques & dates
            num_fields = [
                "purchase_price", "notary_fees", "other_fees", "down_payment",
                "loan_amount", "loan_rate", "insurance_monthly",
                "monthly_payment", "rental_income", "last_estimation_value", "ownership_percentage"
            ]
            for k in num_fields:
                if k in details:
                    setattr(im, k, parse_float(details[k]))
            if "loan_start_date" in details:
                im.loan_start_date = parse_date(details["loan_start_date"])
            if "is_rented" in details:
                im.is_rented = bool(details["is_rented"])
            # Gestion de l’association income
            if "income_id" in details:
                im.income_id = parse_int(details["income_id"])

            if "income_new" in details and details["income_new"]:
                income_new = details["income_new"]
                new_inc = UserIncome(
                    user_id=user_id,
                    label=income_new.get("label"),
                    amount=parse_float(income_new.get("amount")),
                    frequency=income_new.get("frequency"),
                    end_date=parse_date(income_new.get("end_date"))
                )
                session.add(new_inc)
                session.flush()
                im.income_id = new_inc.id

            # possibilité de détacher un revenu
            if details.get("unlink_income") is True:
                im.income_id = None

            # remplacer prêts & frais si fournis
            if "loans" in details and details["loans"] is not None:
                # purge
                for old in list(im.loans):
                    session.delete(old)
                # recrée
                for ln in details.get("loans", []):
                    session.add(ImmoLoan(
                        immo_id=im.id,
                        loan_amount=parse_float(ln.get("loan_amount")),
                        loan_rate=parse_float(ln.get("loan_rate")),
                        loan_duration_months=parse_int(ln.get("loan_duration_months")),
                        loan_start_date=parse_date(ln.get("loan_start_date")),
                        monthly_payment=parse_float(ln.get("monthly_payment"))
                    ))

            if "expenses" in details and details["expenses"] is not None:
                for old in list(im.expenses):
                    session.delete(old)
                for ex in details.get("expenses", []):
                    session.add(ImmoExpense(
                        immo_id=im.id,
                        expense_type=ex.get("expense_type"),
                        amount=parse_float(ex.get("amount")),
                        frequency=ex.get("frequency")
                    ))

        elif asset.type == "portfolio" and details is not None:
            if not asset.portfolio:
                asset.portfolio = AssetPortfolio(asset_id=asset.id)
            pf = asset.portfolio

            for k in ["broker", "recurring_frequency"]:
                if k in details:
                    setattr(pf, k, details[k])
            for k_num in ["initial_investment", "recurring_amount"]:
                if k_num in details:
                    setattr(pf, k_num, parse_float(details[k_num]))
            if "recurring_day" in details:
                pf.recurring_day = parse_int(details["recurring_day"])

            # remplacer produits si fournis
            if "products" in details and details["products"] is not None:
                for old in list(pf.products):
                    session.delete(old)
                for prod in details.get("products", []):
                    session.add(PortfolioProduct(
                        portfolio_id=pf.id,
                        product_type=prod.get("product_type")
                    ))

            # remplacer lignes si fournies
            if "lines" in details and details["lines"] is not None:
                for old in list(pf.lines):
                    session.delete(old)
                for ln in details.get("lines", []):
                    prod = None
                    pid = ln.get("product_id")
                    ptype = ln.get("product_type")
                    if pid:
                        prod = session.query(PortfolioProduct).filter_by(id=pid, portfolio_id=pf.id).first()
                    elif ptype:
                        prod = get_or_create_product(session, pf.id, ptype)

                    session.add(PortfolioLine(
                        portfolio_id=pf.id,
                        isin=ln.get("isin"),
                        label=ln.get("label"),
                        units=parse_float(ln.get("units")),
                        avg_price=parse_float(ln.get("avg_price")),                 # ✅
                        amount_allocated=parse_float(ln.get("amount_allocated")),
                        allocation_frequency=ln.get("allocation_frequency"),
                        date_option=ln.get("date_option"),
                        beneficiary_id=parse_int(ln.get("beneficiary_id") or ln.get("beneficiaryId")),
                        purchase_date=parse_date(ln.get("purchase_date")),
                        product_id=(prod.id if prod else None)                      # ✅
                    ))

        elif asset.type == "other" and details is not None:
            if not asset.other:
                asset.other = AssetOther(asset_id=asset.id)
            oth = asset.other
            for k in ["category", "description", "platform", "wallet_address"]:
                if k in details:
                    setattr(oth, k, details[k])
            if "estimated_value" in details:
                oth.estimated_value = parse_float(details["estimated_value"])

        session.commit()
        return jsonify({"ok": True, "asset": serialize_asset(asset, session)}), 200

    except Exception as e:
        session.rollback()
        print("❌ ERROR /api/assets/<id> (PUT):", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/assets/<int:asset_id>", methods=["DELETE"])
@jwt_required()
def delete_asset(asset_id):
    user_id = int(get_jwt_identity())
    session = Session()
    try:
        asset = session.query(Asset).filter_by(id=asset_id, user_id=user_id).first()
        if not asset:
            return jsonify({"error": "Actif introuvable"}), 404
        session.delete(asset)
        session.commit()
        return jsonify({"ok": True, "deleted_id": asset_id}), 200
    except Exception as e:
        session.rollback()
        print("❌ ERROR /api/assets/<id> (DELETE):", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()

@app.route("/api/produits", methods=["GET"])
@jwt_required()
def list_produits():
    session = Session()
    try:
        q = session.query(ProduitInvest)

        # Filtres facultatifs
        q_type = request.args.get("type")          # action / etf / fonds
        eligible = request.args.get("eligible")    # PEA / CTO / PER / AV
        search = request.args.get("search")        # texte libre sur label / isin / ticker
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))

        if q_type:
            q = q.filter(ProduitInvest.type == q_type)
        if eligible:
            # JSONB array contains ? operator
            q = q.filter(ProduitInvest.eligible_in.op("?")(eligible))
        if search:
            like = f"%{search}%"
            q = q.filter(
                (ProduitInvest.label.ilike(like)) |
                (ProduitInvest.isin.ilike(like)) |
                (ProduitInvest.ticker_yahoo.ilike(like))
            )

        rows = q.order_by(ProduitInvest.label.asc()).limit(limit).offset(offset).all()

        return jsonify([{
            "id": r.id,
            "isin": r.isin,
            "ticker_yahoo": r.ticker_yahoo,
            "label": r.label,
            "type": r.type,
            "eligible_in": r.eligible_in,
            "currency": r.currency,
            "market": r.market,
            "sector": r.sector
        } for r in rows]), 200
    finally:
        session.close()


@app.route("/api/produits", methods=["POST"])
@jwt_required()
def add_produit():
    data = request.get_json() or {}
    session = Session()
    try:
        p = ProduitInvest(
            isin=data.get("isin"),
            ticker_yahoo=data.get("ticker_yahoo"),
            label=data.get("label"),
            type=data.get("type"),
            eligible_in=data.get("eligible_in") or [],   # JSONB direct
            currency=data.get("currency"),
            market=data.get("market"),
            sector=data.get("sector")
        )
        session.add(p)
        session.commit()
        return jsonify({"ok": True, "id": p.id}), 201
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/produits/<int:pid>", methods=["PUT"])
@jwt_required()
def update_produit(pid):
    data = request.get_json() or {}
    session = Session()
    try:
        p = session.query(ProduitInvest).get(pid)
        if not p:
            return jsonify({"ok": False, "error": "Produit introuvable"}), 404

        for key in ["isin","ticker_yahoo","label","type","currency","market","sector"]:
            if key in data:
                setattr(p, key, data[key])
        if "eligible_in" in data:
            p.eligible_in = data["eligible_in"]

        session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/produits/<int:pid>", methods=["DELETE"])
@jwt_required()
def delete_produit(pid):
    session = Session()
    try:
        p = session.query(ProduitInvest).get(pid)
        if not p:
            return jsonify({"ok": False, "error": "Produit introuvable"}), 404
        session.delete(p)
        session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.route("/api/produits/<int:pid>/histo", methods=["GET"])
@jwt_required()
def get_produit_histo(pid):
    session = Session()
    try:
        dfrom = request.args.get("from")  # YYYY-MM-DD
        dto   = request.args.get("to")    # YYYY-MM-DD
        q = session.query(ProduitHisto).filter(ProduitHisto.produit_id == pid)
        if dfrom:
            q = q.filter(ProduitHisto.date >= dfrom)
        if dto:
            q = q.filter(ProduitHisto.date <= dto)
        rows = q.order_by(ProduitHisto.date.asc()).all()
        return jsonify([{
            "date": r.date.isoformat(),
            "open": float(r.open) if r.open is not None else None,
            "high": float(r.high) if r.high is not None else None,
            "low": float(r.low) if r.low is not None else None,
            "close": float(r.close) if r.close is not None else None,
            "volume": int(r.volume) if r.volume is not None else None
        } for r in rows]), 200
    finally:
        session.close()


@app.route("/api/produits/<int:pid>/indicateurs", methods=["GET"])
@jwt_required()
def get_produit_indicateurs(pid):
    session = Session()
    try:
        dfrom = request.args.get("from")
        dto   = request.args.get("to")
        q = session.query(ProduitIndicateurs).filter(ProduitIndicateurs.produit_id == pid)
        if dfrom:
            q = q.filter(ProduitIndicateurs.date >= dfrom)
        if dto:
            q = q.filter(ProduitIndicateurs.date <= dto)
        rows = q.order_by(ProduitIndicateurs.date.asc()).all()
        return jsonify([{
            "date": r.date.isoformat(),
            "ma20": float(r.ma20) if r.ma20 is not None else None,
            "ma50": float(r.ma50) if r.ma50 is not None else None,
            "rsi14": float(r.rsi14) if r.rsi14 is not None else None,
            "macd": float(r.macd) if r.macd is not None else None,
            "signal": float(r.signal) if r.signal is not None else None
        } for r in rows]), 200
    finally:
        session.close()


@app.route("/api/produits/<int:pid>/intraday", methods=["GET"])
@jwt_required()
def get_produit_intraday(pid):
    session = Session()
    try:
        limit = int(request.args.get("limit", 500))
        rows = (session.query(ProduitIntraday)
                .filter(ProduitIntraday.produit_id == pid)
                .order_by(ProduitIntraday.ts.desc())
                .limit(limit)
                .all())
        return jsonify([{
            "ts": r.ts.isoformat(),
            "price": float(r.price) if r.price is not None else None,
            "volume": int(r.volume) if r.volume is not None else None
        } for r in rows]), 200
    finally:
        session.close()

# ---------------------------------------------------------
# Trade Republic Broker API (mock pour l’instant)
# ---------------------------------------------------------
from flask import current_app

@app.route("/api/broker/traderepublic/connect", methods=["POST"])
@jwt_required()
def tr_connect():
    uid = int(get_jwt_identity())
    data = request.get_json() or {}
    phone = data.get("phone")
    pin   = data.get("pin")

    s = Session()
    try:
        if not phone:
            link = s.query(BrokerLink).filter_by(user_id=uid, broker="Trade Republic").first()
            if not link:
                return jsonify({"ok": False, "error": "No saved phone. Send phone (and pin)"}), 400
            phone = link.phone_e164
            if not pin and link.pin_enc:
                try:
                    pin = dec_secret(link.pin_enc)
                except Exception:
                    pin = None  # si déchiffrage échoue, on exigera pin côté front

        if not phone or not pin:
            return jsonify({"ok": False, "error": "phone and pin required"}), 400

        resp = tr_connect_api(phone, pin)
        return jsonify({"ok": True, "processId": resp["processId"], "countdown": resp["countdownInSeconds"]}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        s.close()



@app.route("/api/broker/traderepublic/2fa", methods=["POST"])
@jwt_required()
def tr_2fa():
    data = request.get_json() or {}
    process_id, code = data.get("processId"), data.get("code")
    if not process_id or not code:
        return jsonify({"ok": False, "error": "ProcessId et code requis"}), 400
    try:
        token = tr_validate_api(process_id, code)
        return jsonify({"ok": True, "status": "AUTHENTICATED", "token": token}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/broker/traderepublic/portfolio", methods=["GET","POST"])
@jwt_required()
def tr_portfolio():
    if request.method == "GET":
        token = request.args.get("token")
    else:
        data = request.get_json() or {}
        token = data.get("token")
    if not token:
        return jsonify({"ok": False, "error": "Token requis"}), 400
    try:
        raw = tr_fetch_api(token)

        # Normalisation des positions
        accounts = []
        for acc in raw.get("accounts", []):
            normalized = []
            # 🔧 il faut descendre dans categories
            for cat in acc.get("positions", []):  # ici "positions" contient en fait les catégories
                for p in cat.get("positions", []):
                    normalized.append({
                        "isin": p.get("isin"),
                        "name": p.get("name"),
                        "units": p.get("netSize") or p.get("quantity"),
                        "avgPrice": p.get("averageBuyIn") or (p.get("avgPrice") or {}).get("value"),
                    })


            accounts.append({
                "cashAccountNumber": acc.get("cashAccountNumber"),
                "securitiesAccountNumber": acc.get("securitiesAccountNumber"),
                "productType": acc.get("productType"),
                "positions": normalized
            })


        # ✅ Debug: première position extraite
        first_position = None
        if accounts and accounts[0].get("positions"):
            first_position = accounts[0]["positions"][0]

        return jsonify({
            "cash": raw.get("cash"),
            "positions": accounts,
            "transactions": raw.get("transactions", []),
            "debug_first_position": first_position  # <--- ajouté ici
        }), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500





@app.route("/api/broker/traderepublic/import", methods=["POST"])
@jwt_required()
def tr_import():
    user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    cash = parse_float(data.get("cash"))
    positions = data.get("positions", [])
    transactions = data.get("transactions", [])
    allocations = data.get("allocations", [])
    label = data.get("label") or "Portefeuille Trade Republic"
    broker = data.get("broker") or "Trade Republic"


    session = Session()
    try:
        # 0) Créer asset + portfolio
        asset = Asset(user_id=user_id, type="portfolio", label=label, current_value=cash)
        session.add(asset); session.flush()

        pf = AssetPortfolio(asset_id=asset.id, broker=broker)
        session.add(pf); session.flush()

        # helper pour upsert un produit (PEA/CTO/AV/PER) côté portfolio
        def _get_or_create_product(pf_id, ptype):
            if not ptype:
                return None
            row = (session.query(PortfolioProduct)
                   .filter_by(portfolio_id=pf_id, product_type=ptype)
                   .first())
            if row: return row
            row = PortfolioProduct(portfolio_id=pf_id, product_type=ptype)
            session.add(row); session.flush()
            return row

        # 1) Positions -> PortfolioLine (PRU ≠ Allocation)
        for pos in positions:
            ptype_raw = pos.get("productType") or pos.get("product_type")
            ptype = map_tr_product_type(ptype_raw) if ptype_raw else None
            prod = get_or_create_product(session, pf.id, ptype) if ptype else None

            pl = PortfolioLine(
                portfolio_id=pf.id,
                isin=pos.get("isin"),
                label=pos.get("name"),
                units=parse_float(pos.get("units")),
                avg_price=parse_float(pos.get("avgPrice")),     # ✅ PRU stocké ici
                amount_allocated=None,                          # ⚠️ pas le PRU !
                allocation_frequency=None,
                purchase_date=None,
                product_id=prod.id if prod else None            # ✅ typage de la ligne
            )
            session.add(pl)

        # 2) Transactions TR (inchangé si tu veux)
        for tx in transactions:
            session.add(PortfolioTransaction(
                portfolio_id=pf.id,
                isin=tx.get("isin"),
                label=tx.get("label") or tx.get("name"),
                transaction_type=tx.get("transaction_type") or tx.get("type"),
                quantity=parse_float(tx.get("quantity")),
                amount=parse_float(tx.get("amount")),
                date=parse_date(tx.get("date"))
            ))

        session.flush()

        # 3) Allocations utilisateur -> compléter les lignes existantes
        for alloc in allocations:
            line = (session.query(PortfolioLine)
                    .filter_by(portfolio_id=pf.id, isin=alloc.get("isin"))
                    .first())
            if line:
                line.amount_allocated      = parse_float(alloc.get("amount") or alloc.get("amount_allocated"))
                line.allocation_frequency  = alloc.get("frequency") or alloc.get("allocation_frequency")
                line.date_option           = alloc.get("date_option")
                ben = alloc.get("beneficiary_id") or alloc.get("beneficiaryId")
                line.beneficiary_id        = parse_int(None if ben in (None, "", "self") else ben)

        session.commit()
        return jsonify({"ok": True, "asset_id": asset.id}), 201

    except Exception as e:
        session.rollback()
        app.logger.error(f"❌ tr_import error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()



# ---------------------------------------------------------
# Portfolio Transactions
# ---------------------------------------------------------

@app.route("/api/portfolios/<int:portfolio_id>/transactions", methods=["GET"])
@jwt_required()
def list_portfolio_transactions(portfolio_id):
    user_id = int(get_jwt_identity())
    session = Session()
    try:
        # sécurité : vérifier que le portfolio appartient bien au user
        pf = (session.query(AssetPortfolio)
              .join(Asset)
              .filter(AssetPortfolio.id == portfolio_id, Asset.user_id == user_id)
              .first())
        if not pf:
            return jsonify({"error": "Portfolio introuvable"}), 404

        txs = pf.transactions
        return jsonify([{
            "id": tx.id,
            "isin": tx.isin,
            "label": tx.label,
            "transaction_type": tx.transaction_type,
            "quantity": float(tx.quantity) if tx.quantity else None,
            "amount": float(tx.amount) if tx.amount else None,
            "date": tx.date.isoformat()
        } for tx in txs]), 200
    finally:
        session.close()


@app.route("/api/portfolios/<int:portfolio_id>/transactions", methods=["POST"])
@jwt_required()
def add_portfolio_transaction(portfolio_id):
    user_id = int(get_jwt_identity())
    data = request.get_json() or {}
    session = Session()
    try:
        pf = (session.query(AssetPortfolio)
              .join(Asset)
              .filter(AssetPortfolio.id == portfolio_id, Asset.user_id == user_id)
              .first())
        if not pf:
            return jsonify({"error": "Portfolio introuvable"}), 404

        tx = PortfolioTransaction(
            portfolio_id=portfolio_id,
            isin=data.get("isin"),
            label=data.get("label"),
            transaction_type=data.get("transaction_type"),
            quantity=parse_float(data.get("quantity")),
            amount=parse_float(data.get("amount")),
            date=parse_date(data.get("date"))
        )
        session.add(tx)
        session.commit()
        return jsonify({"ok": True, "id": tx.id}), 201
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/transactions/<int:tx_id>", methods=["PUT"])
@jwt_required()
def update_portfolio_transaction(tx_id):
    user_id = int(get_jwt_identity())
    data = request.get_json() or {}
    session = Session()
    try:
        tx = (session.query(PortfolioTransaction)
              .join(AssetPortfolio)
              .join(Asset)
              .filter(PortfolioTransaction.id == tx_id, Asset.user_id == user_id)
              .first())
        if not tx:
            return jsonify({"error": "Transaction introuvable"}), 404

        for key in ["isin", "label", "transaction_type"]:
            if key in data:
                setattr(tx, key, data[key])
        if "quantity" in data:
            tx.quantity = parse_float(data["quantity"])
        if "amount" in data:
            tx.amount = parse_float(data["amount"])
        if "date" in data:
            tx.date = parse_date(data["date"])

        session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/transactions/<int:tx_id>", methods=["DELETE"])
@jwt_required()
def delete_portfolio_transaction(tx_id):
    user_id = int(get_jwt_identity())
    session = Session()
    try:
        tx = (session.query(PortfolioTransaction)
              .join(AssetPortfolio)
              .join(Asset)
              .filter(PortfolioTransaction.id == tx_id, Asset.user_id == user_id)
              .first())
        if not tx:
            return jsonify({"error": "Transaction introuvable"}), 404
        session.delete(tx)
        session.commit()
        return jsonify({"ok": True, "deleted_id": tx_id}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()

@app.route("/api/broker/traderepublic/link", methods=["GET"])
@jwt_required()
def tr_link_get():
    uid = int(get_jwt_identity())
    s = Session()
    try:
        link = s.query(BrokerLink).filter_by(user_id=uid, broker="Trade Republic").first()
        if not link:
            return jsonify({"hasLink": False}), 200
        masked = link.phone_e164[:-4] + "****" if len(link.phone_e164) > 4 else "****"
        return jsonify({
            "hasLink": True,
            "phone": link.phone_e164,
            "remember_pin": bool(link.remember_pin),
            "hasPin": bool(link.remember_pin),        # ✅ pour compat ascendante côté front
        }), 200
    finally:
        s.close()

@app.route("/api/broker/traderepublic/link", methods=["POST"])
@jwt_required()
def tr_link_post():
    uid = int(get_jwt_identity())
    data = request.get_json() or {}
    phone = data.get("phone")
    pin   = data.get("pin")
    remember = bool(data.get("remember_pin"))

    if not phone:
        return jsonify({"ok": False, "error": "phone required"}), 400

    s = Session()
    try:
        link = s.query(BrokerLink).filter_by(user_id=uid, broker="Trade Republic").first()
        if not link:
            link = BrokerLink(user_id=uid, broker="Trade Republic", phone_e164=phone)
            s.add(link)
        else:
            link.phone_e164 = phone

        if remember and pin:
            link.pin_enc = enc_secret(pin)
            link.remember_pin = True
        elif not remember:
            link.pin_enc = None
            link.remember_pin = False

        link.updated_at = datetime.utcnow()
        s.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        s.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        s.close()

@app.route("/api/broker/traderepublic/link", methods=["DELETE"])
@jwt_required()
def tr_link_delete():
    uid = int(get_jwt_identity())
    s = Session()
    try:
        s.query(BrokerLink).filter_by(user_id=uid, broker="Trade Republic").delete()
        s.commit()
        return jsonify({"ok": True}), 200
    finally:
        s.close()


@app.route("/api/fixtures", methods=["GET"])
def fixtures():
    return jsonify({"ok": True, "now": datetime.utcnow().isoformat()})

from auth_google import register_google_auth_route
register_google_auth_route(app, app.config["JWT_SECRET_KEY"], engine)

# ✅ log
app.logger.info("🔎 URL MAP: %s", app.url_map)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
