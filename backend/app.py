# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from models import Base, Asset, AssetLivret, AssetImmo, AssetPortfolio, PortfolioLine, AssetOther
from utils import amortization_monthly_payment
from datetime import datetime
import traceback
from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from models import User

# Charger variables d’environnement
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL required in env")

# Config DB
engine = create_engine(DATABASE_URL, echo=False, future=True)
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
jwt = JWTManager(app)

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
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
        return datetime.fromisoformat(val).date() if val else None
    except Exception:
        return None

# ---------------------------------------------------------
# Routes API
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

        token = create_access_token(identity=user.id)
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

        token = create_access_token(identity=user.id)
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
    user_id = get_jwt_identity()
    session = Session()
    try:
        user = session.query(User).get(user_id)
        if not user:
            return jsonify({"ok": False, "error": "Utilisateur non trouvé"}), 404
        return jsonify({
            "ok": True,
            "id": user.id,
            "email": user.email,
            "fullname": user.fullname
        }), 200
    finally:
        session.close()

@app.route("/api/users/<int:user_id>/security", methods=["PUT"])
def update_user_security(user_id):
    payload = request.get_json() or {}
    use_pin = payload.get("use_pin", False)
    use_biometrics = payload.get("use_biometrics", False)

    session = Session()
    try:
        user = session.query(User).filter_by(id=user_id).first()
        if not user:
            return jsonify({"ok": False, "error": "User not found"}), 404

        user.use_pin = use_pin
        user.use_biometrics = use_biometrics
        session.commit()

        return jsonify({"ok": True, "message": "Security preferences updated"})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.route("/api/assets", methods=["POST"])
def add_asset():
    payload = request.get_json(silent=True) or {}
    required = ["type", "label"]
    for r in required:
        if r not in payload:
            return jsonify({"error": f"{r} required"}), 400

    type_ = payload["type"]
    label = payload["label"]
    current_value = parse_float(payload.get("current_value"))
    details = payload.get("details", {}) or {}

    user_id = 1  # TODO: remplacer plus tard par authentification
    session = Session()

    try:
        asset = Asset(user_id=user_id, type=type_, label=label, current_value=current_value)
        session.add(asset)
        session.flush()  # avoir asset.id

        # Cas Livret
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

        # Cas Immobilier
        elif type_ == "immo":
            loan_amount = parse_float(details.get("loan_amount"))
            loan_rate = parse_float(details.get("loan_rate"))
            loan_duration_months = parse_int(details.get("loan_duration_months"))
            monthly_payment = parse_float(details.get("monthly_payment"))

            if not monthly_payment and loan_amount and loan_rate is not None and loan_duration_months:
                try:
                    monthly_payment = amortization_monthly_payment(
                        loan_amount,
                        loan_rate,
                        loan_duration_months
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
                rental_income=parse_float(details.get("rental_income"))
            )
            session.add(im)

        # Cas Portefeuille
        elif type_ == "portfolio":
            pf = AssetPortfolio(
                asset_id=asset.id,
                product_type=details.get("product_type"),
                broker=details.get("broker"),
                initial_investment=parse_float(details.get("initial_investment")),
                recurring_amount=parse_float(details.get("recurring_amount")),
                recurring_frequency=details.get("recurring_frequency"),
                recurring_day=parse_int(details.get("recurring_day"))
            )
            session.add(pf)
            session.flush()

            lines = details.get("lines", [])
            for ln in lines:
                pl = PortfolioLine(
                    portfolio_id=pf.id,
                    isin=ln.get("isin"),
                    label=ln.get("label"),
                    units=parse_float(ln.get("units")),
                    amount_invested=parse_float(ln.get("amount_invested")),
                    purchase_date=parse_date(ln.get("purchase_date"))
                )
                session.add(pl)

        # Cas Autre
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
        # trace complète dans les logs Render pour debug
        print("❌ ERROR /api/assets:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        session.close()


@app.route("/api/fixtures", methods=["GET"])
def fixtures():
    return jsonify({"ok": True, "now": datetime.utcnow().isoformat()})

from auth_google import register_google_auth_route
register_google_auth_route(app, app.config["JWT_SECRET_KEY"], engine)

# ✅ Logge immédiatement la carte des routes pour vérif
app.logger.info("🔎 URL MAP: %s", app.url_map)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
