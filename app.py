# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload
import os
from dotenv import load_dotenv
from models import (
    Base, User, Beneficiary, Asset, AssetLivret, AssetImmo, AssetPortfolio, PortfolioLine,
    AssetOther, UserIncome, UserExpense, PortfolioProduct, ImmoLoan, ImmoExpense,
    ProduitInvest, ProduitHisto, ProduitIndicateurs, ProduitIntraday, BrokerLink, AssetEvent, # ✅ ajout
)

import re
from utils import amortization_monthly_payment
from datetime import datetime, timedelta
import traceback
from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from sqlalchemy.exc import SQLAlchemyError
import json
from sqlalchemy import and_, func
from scraper_tr import connect as tr_connect_api, validate_2fa as tr_validate_api, fetch_data as tr_fetch_api
import logging
import sys
from cryptography.fernet import Fernet, InvalidToken
from typing import Union
import hashlib
from decimal import Decimal

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


TR_EXEC_TYPES     = {"trading_savingsplan_executed", "trading_trade_executed"}
TR_DIV_TYPES      = {"dividend_payout", "dividend"}
TR_INTEREST_TYPES = {"interest_payout", "interest_credit"}
TR_FEE_TYPES      = {"fee_charged", "trading_fee_charged"}
TR_DEPOSIT_TYPES  = {"incoming_transfer_delegation", "deposit_credit", "pea_activation_deposit"}
TR_PAYIN_TYPES    = {"pea_savings_plan_pay_in", "pea_deposit_debit"}

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

def parse_date(val):
    """
    Accepte: '2025-09-16', '2025-09-16T08:22:22Z', '...+00:00', '...+0000'
    Retourne date (UTC-agnostic).
    """
    try:
        if not val:
            return None
        s = str(val).strip()
        # Z -> +00:00
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # +HHMM -> +HH:MM (ex: +0000 -> +00:00)
        m = re.search(r"([+-]\d{2})(\d{2})$", s)
        if m:
            s = s[:m.start()] + f"{m.group(1)}:{m.group(2)}"
        try:
            return datetime.fromisoformat(s).date()
        except Exception:
            # dernier recours: juste YYYY-MM-DD
            return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None

def _extract_cash_amount(cash_obj):
    # Accepts number, string, or dicts from TR.
    if cash_obj is None:
        return None
    # numeric or string-like
    if isinstance(cash_obj, (int, float, Decimal, str)):
        return parse_float(cash_obj)
    # dict shape: try common keys
    if isinstance(cash_obj, dict):
        for key in ("amount", "value", "balance", "cash", "cashBalance"):
            if key in cash_obj:
                return parse_float(cash_obj.get(key))
    return None

def enc_secret(s: str) -> str:
    return _fernet.encrypt(s.encode()).decode("ascii")   # ✅ str

def dec_secret(token: Union[str, bytes, bytearray, memoryview]) -> str:
    """
    Déchiffre un jeton Fernet provenant d'une colonne TEXT (str) ou BYTEA
    (bytes/memoryview). Retourne le PIN en clair (str).
    """
    if token is None:
        raise ValueError("empty token")

    # Normalise en bytes
    if isinstance(token, memoryview):
        token_bytes = token.tobytes()
    elif isinstance(token, bytearray):
        token_bytes = bytes(token)
    elif isinstance(token, bytes):
        token_bytes = token
    elif isinstance(token, str):
        # Jeton Fernet = base64 urlsafe ASCII
        token_bytes = token.encode("ascii")
    else:
        # fallback ultra-sûr (cas exotique SQLAlchemy): on cast en str
        token_bytes = str(token).encode("ascii")

    plain = _fernet.decrypt(token_bytes)
    return plain.decode("utf-8")


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

import uuid

def ensure_user_asset(session, user_id: int, asset_id: int):
    return session.query(Asset).filter_by(id=asset_id, user_id=user_id).first()

def ensure_isin_known(session, isin: str) -> bool:
    if not isin:
        return True
    return session.query(ProduitInvest).filter(ProduitInvest.isin == isin).first() is not None


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

import re

def map_tr_product_type(s: str) -> str | None:
    if not s:
        return None
    t = s.strip().lower()

    # enums TR fréquents
    if t in ("default", "securities", "broker"):
        return "CTO"

    # éviter "wrapPER" -> "PER"
    if "tax_wrapper" in t:
        return None  # ambigu: PEA/PER/AV -> à déduire autrement

    # vrais mots seulement
    if re.search(r"\bpea\b", t):
        return "PEA"
    if re.search(r"\bper\b", t):
        return "PER"
    if re.search(r"\b(cto|securities)\b", t):
        return "CTO"
    if re.search(r"\b(assurance(?:[\s-]?vie)?|life)\b", t):
        return "AV"

    return None

def upsert_produits_from_positions(session, positions):
    """
    S'assure que chaque position (par ISIN) existe dans ProduitInvest.
    Crée les manquants avec les infos minimales disponibles.
    Retourne un récapitulatif {existing: [...], created: [...] }.
    """
    if not positions:
        return {"existing": [], "created": []}

    # 1) Collecte des métadonnées par ISIN
    meta_by_isin = {}
    for pos in positions:
        isin = (pos.get("isin") or "").strip().upper()
        if not isin:
            continue  # on ignore les lignes sans ISIN
        name = pos.get("name") or pos.get("label")
        ptype_raw = pos.get("productType") or pos.get("product_type")
        ptype = map_tr_product_type(ptype_raw) if ptype_raw else None

        d = meta_by_isin.setdefault(isin, {"labels": set(), "eligible": set()})
        if name:
            d["labels"].add(name)
        if ptype:
            d["eligible"].add(ptype)

    if not meta_by_isin:
        return {"existing": [], "created": []}

    # 2) Recherche des ISIN déjà présents
    existing_rows = (session.query(ProduitInvest.isin)
                     .filter(ProduitInvest.isin.in_(list(meta_by_isin.keys())))
                     .all())
    existing_isins = {r.isin for r in existing_rows}

    # 3) Création des manquants
    created = []
    for isin, meta in meta_by_isin.items():
        if isin in existing_isins:
            continue
        label = next(iter(meta["labels"]), isin)  # fallback = ISIN comme label
        eligible_in = sorted(list(meta["eligible"]))  # ex: ["PEA"] ou []
        p = ProduitInvest(
            isin=isin,
            label=label,
            type=None,                  # inconnu à ce stade
            eligible_in=eligible_in,    # JSONB
            currency=None,
            market=None,
            sector=None,
            ticker_yahoo=None
        )
        session.add(p)
        session.flush()  # pour récupérer p.id si besoin
        created.append({"id": p.id, "isin": p.isin, "label": p.label})

    return {"existing": list(existing_isins), "created": created}

# app.py (ajoute près des routes TR)

def _normalize_tr_accounts(raw):
    """
    Reprend la logique de /broker/traderepublic/portfolio pour normaliser comptes & positions.

    Entrée attendue (exemples possibles côté TR) :
    - raw["positions"] : liste de comptes { productType, cashAccountNumber, securitiesAccountNumber, positions:[...] }
    - OU raw["accounts"] : même idée selon la source
    - raw["transactions"] : utilisé pour déduire PEA/PER quand TR renvoie un type générique (ex: 'tax_wrapper')

    Sortie :
    {
        "cash": {...} | None,
        "accounts": [
            {
                "productType": "PEA" | "CTO" | "PER",
                "cashAccountNumber": "...",
                "securitiesAccountNumber": "...",
                "positions": [
                    {
                        "isin": str | None,
                        "name": str | None,
                        "units": float | None,
                        "avgPrice": float | None,
                        "productType": "PEA" | "CTO" | "PER",
                    },
                    ...
                ]
            },
            ...
        ],
        "positions_flat": [ ... même schéma qu'une position ... ]
    }
    """
    # ---------- helpers ----------
    def _num(x):
        try:
            if x is None:
                return None
            if isinstance(x, (int, float)):
                return float(x)
            # "28,116" → 28.116
            return float(str(x).replace(",", "."))
        except Exception:
            return None

    def _flatten_positions(lst):
        """
        Aplati une liste qui peut contenir des objets { positions:[...] } ou directement des positions.
        """
        flat = []
        for it in lst or []:
            if isinstance(it, dict) and not it.get("isin") and isinstance(it.get("positions"), list):
                flat.extend(it["positions"])
            else:
                flat.append(it)
        return flat

    def _fallback_map_type(ptype_raw: str | None) -> str | None:
        """
        Mapping minimal si la fonction globale map_tr_product_type n'existe pas.
        """
        if not ptype_raw:
            return None
        p = ptype_raw.lower()
        if "pea" in p:
            return "PEA"
        if "per" in p or "retire" in p or "pension" in p:
            return "PER"
        if "cto" in p or "broker" in p or "depot" in p or "depot" in p:
            return "CTO"
        if "tax_wrapper" in p:
            return None  # on laissera les hints décider
        # Valeur par défaut raisonnable
        return "CTO"

    def _map_type(ptype_raw):
        # Si l'app définit déjà map_tr_product_type(...), on l'utilise.
        mapper = globals().get("map_tr_product_type")
        if callable(mapper):
            return mapper(ptype_raw)
        return _fallback_map_type(ptype_raw)

    # ---------- 1) indices PEA/PER via historique (hints) ----------
    hints: dict[str, str] = {}
    for tx in (raw.get("transactions") or []):
        acc = (str(tx.get("cashAccountNumber") or "").strip())
        if not acc:
            continue
        evt = (tx.get("eventType") or "").lower()
        sub = (tx.get("subtitle") or "").lower()
        # Heuristiques simples
        if evt.startswith("pea_") or "pea" in sub:
            hints[acc] = "PEA"
        elif evt.startswith("per_") or "retirement" in sub or " per " in f" {sub} ":
            hints[acc] = "PER"

    # ---------- 2) source des "comptes" ----------
    src_accounts = (raw.get("accounts") or raw.get("positions") or [])

    accounts = []
    for acc in src_accounts:
        cash_acc  = (acc.get("cashAccountNumber") or "").strip()
        sec_acc   = (acc.get("securitiesAccountNumber") or "").strip()
        ptype_raw = (acc.get("productType") or "").lower()

        # Si TR renvoie un type "générique" (ex: 'tax_wrapper'), on privilégie les hints.
        if "tax_wrapper" in ptype_raw:
            normalized_type = hints.get(cash_acc) or _map_type(ptype_raw) or "CTO"
        else:
            normalized_type = _map_type(ptype_raw) or "CTO"

        # ---------- 3) positions normalisées ----------
        norm_positions = []
        for p in _flatten_positions(acc.get("positions")):
            if not (p.get("isin") or p.get("name") or p.get("label")):
                continue

            # avgPrice peut être 'averageBuyIn' (string/float) ou 'avgPrice' (float ou dict {value})
            avg = p.get("averageBuyIn")
            if avg is None:
                ap = p.get("avgPrice")
                if isinstance(ap, dict):
                    avg = ap.get("value")
                else:
                    avg = ap

            # units peut exister sous différents noms
            units = (
                p.get("netSize") or
                p.get("virtualSize") or
                p.get("quantity") or
                p.get("units")
            )

            norm_positions.append({
                "isin": p.get("isin"),
                "name": p.get("name") or p.get("label"),
                "units": _num(units),
                "avgPrice": _num(avg),
                "productType": normalized_type,
            })

        accounts.append({
            "productType": normalized_type,
            "cashAccountNumber": cash_acc or None,
            "securitiesAccountNumber": sec_acc or None,
            "positions": norm_positions,
        })

    # ---------- 4) sortie ----------
    positions_flat = [pos for a in accounts for pos in a.get("positions", [])]

    cash_value = _extract_cash_amount(raw.get("cash"))
    return {
        "cash": cash_value,           # always a float/None
        "accounts": accounts,
        "positions_flat": positions_flat,
    }



def _tr_tx_uid(tx: dict) -> str:
    raw = tx.get("id") or tx.get("uuid")
    if raw:
        return f"tr:{raw}"
    payload = "|".join(str(tx.get(k) or "") for k in ("type","time","date","label","name","isin","amount"))
    return "tr:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()

def _map_tr_tx_to_event(tx: dict) -> dict:
    # 1) type / libellé / date
    t = (tx.get("type") or tx.get("eventType") or "").lower()
    label = tx.get("label") or tx.get("name") or tx.get("title")
    value_date = (parse_date(tx.get("time") or tx.get("date") or tx.get("timestamp")))

    # 2) montant (peut être un dict TR {value, currency,...})
    raw_amount = tx.get("amount")
    if isinstance(raw_amount, dict):
        amount = parse_float(raw_amount.get("value"))
    else:
        amount = parse_float(raw_amount)

    # 3) quantité si dispo (souvent absente dans la timeline “compacte”)
    quantity = parse_float(tx.get("quantity"))

    # 4) ISIN : direct ou à partir de l’asset d’icône (logos/ISIN/v2)
    isin = (tx.get("isin") or "").strip().upper() or None
    if not isin:
        for k in ("icon", "avatar"):
            v = tx.get(k) or {}
            asset = v.get("asset") if isinstance(v, dict) else None
            if asset:
                m = re.search(r"logos/([A-Z0-9]{12})/", asset)
                if m:
                    isin = m.group(1)
                    break

    ev = {
        "kind": "other",
        "category": t,
        "value_date": value_date,
        "amount": amount,
        "quantity": None,
        "unit_price": None,
        "isin": isin,
        "note": label,
        "tx_type": None,
    }

    # 5) catégorisation
    if t in TR_EXEC_TYPES:
        ev["kind"] = "portfolio_trade"
        ev["quantity"] = quantity  # peut rester None si non fourni
        ev["unit_price"] = (abs(amount) / quantity) if (amount is not None and quantity) else None
        ev["tx_type"] = "buy" if (amount or 0) < 0 else "sell"
    elif t in TR_DIV_TYPES:
        ev["kind"] = "dividend"
        ev["tx_type"] = "dividend"
    elif t in TR_INTEREST_TYPES:
        ev["kind"] = "portfolio_trade"; ev["category"] = "interest"
    elif t in TR_FEE_TYPES:
        ev["kind"] = "portfolio_trade"; ev["category"] = "fee"
    elif t in TR_DEPOSIT_TYPES:
        ev["kind"] = "portfolio_trade"; ev["category"] = "deposit"
    elif t in TR_PAYIN_TYPES:
        ev["kind"] = "portfolio_trade"; ev["category"] = "pay_in"

    return ev

def upsert_tr_asset_events(session, user_id: int, asset: Asset, tr_transactions: list) -> dict:
    """
    Idempotent: insère/MAJ uniquement dans AssetEvent (status=posted).
    Ne crée plus de PortfolioTransaction et ne renseigne plus posted_entity_*.
    """
    created = updated = 0

    for tx in (tr_transactions or []):
        uid = _tr_tx_uid(tx)
        mapped = _map_tr_tx_to_event(tx)

        # garde-fous
        if not mapped["value_date"] or mapped["amount"] is None:
            continue

        # idempotence via data.tr_uid
        existing = (session.query(AssetEvent)
                    .filter(AssetEvent.user_id == user_id,
                            AssetEvent.asset_id == asset.id,
                            AssetEvent.data['tr_uid'].astext == uid)
                    .first())

        if existing:
            changed = False
            for k in ("kind","value_date","amount","quantity","unit_price","isin","category","note"):
                v = mapped[k]
                if v is not None and getattr(existing, k) != v:
                    setattr(existing, k, v); changed = True
            if changed:
                existing.data = {**(existing.data or {}), "tr_raw": tx, "tr_type": tx.get("type"), "tr_uid": uid}
                updated += 1
        else:
            ev = AssetEvent(
                user_id=user_id, asset_id=asset.id, status="posted",
                kind=mapped["kind"], value_date=mapped["value_date"],
                amount=mapped["amount"], quantity=mapped["quantity"],
                unit_price=mapped["unit_price"], isin=mapped["isin"],
                category=mapped["category"], note=mapped["note"],
                data={"tr_uid": uid, "tr_type": tx.get("type"), "tr_raw": tx}
            )
            session.add(ev)
            created += 1

    session.commit()
    return {"created": created, "updated": updated}

def _events_to_tx_view(session, uid: int, asset_id: int) -> list[dict]:
    evs = (session.query(AssetEvent)
           .filter(AssetEvent.user_id == uid,
                   AssetEvent.asset_id == asset_id,
                   AssetEvent.status == "posted",
                   AssetEvent.kind.in_(["portfolio_trade","dividend"]))
           .order_by(AssetEvent.value_date.asc(), AssetEvent.id.asc())
           .all())

    out = []
    for e in evs:
        if e.kind == "dividend":
            tx_type = "dividend"; qty = None
        else:
            tx_type = "buy" if (e.quantity or 0) > 0 else "sell"
            qty = float(e.quantity) if e.quantity is not None else None
        out.append({
            "id": e.id,
            "date": e.value_date.isoformat(),
            "transaction_type": tx_type,
            "isin": e.isin,
            "label": e.note,
            "quantity": qty,
            "amount": float(e.amount) if e.amount is not None else None,
        })
    return out


@app.route("/api/broker/traderepublic/resync", methods=["POST"])
@jwt_required()
def tr_resync_dryrun():
    """
    Resync Trade Republic en mode 'crescendo'
    - sans token -> démarre la connexion (si PIN mémorisé) et renvoie 412 + processId
    - avec token + list_only=true -> listing pur (dry-run)
    - avec token + list_only=false -> dry-run global, avec possibilité d'appliquer UNIQUEMENT l'upsert des transactions
      si apply = { "transactions": true }
    """
    uid = int(get_jwt_identity())
    data = request.get_json() or {}
    asset_ids = data.get("asset_ids") or []
    token = data.get("token")
    list_only = bool(data.get("list_only"))
    apply = data.get("apply") or {}
    apply_tx = bool(apply.get("transactions"))  # 👈 n'appliquer que les transactions
    apply_cash = bool(apply.get("cash"))   # 👈 nouveau

    s = Session()
    try:
        # 1) Récupérer le lien en BDD AVANT de s'en servir
        link = s.query(BrokerLink).filter_by(user_id=uid, broker="Trade Republic").first()
        app.logger.info(
            "[TR][resync] link? %s phone? %s pin? %s",
            bool(link), bool(link and link.phone_e164), bool(link and link.pin_enc)
        )

        # 2) Pas de token -> on déclenche le challenge (ou on dit au front de le faire)
        if not token:
            phone = (link.phone_e164 or "").strip() if link else ""
            pin = None
            if link and link.remember_pin and link.pin_enc:
                try:
                    pin = dec_secret(link.pin_enc)
                    app.logger.info("[TR][resync] decrypt_ok=%s pin_len=%s", True, len(pin))
                except Exception as e:
                    app.logger.warning("[TR][resync] decrypt_failed: %s", e)
                    pin = None

            if not phone or not pin:
                app.logger.info("[TR][resync] Missing phone or stored PIN -> besoin 2FA manuel")
                # Le front appellera /connect pour obtenir un processId puis /2fa
                return jsonify({"ok": False, "needs2fa": True, "reason": "PIN not stored"}), 412

            # Si on a tout, on peut lancer le connect pour récupérer un processId tout de suite
            try:
                resp = tr_connect_api(phone, pin)
                process_id = resp.get("processId")
                app.logger.info(
                    "[TR][resync] connect lancé processId=%s challenge=%s",
                    process_id, resp.get("challengeType")
                )
                return jsonify({
                    "ok": False,
                    "needs2fa": True,
                    "processId": process_id,
                    "challengeType": resp.get("challengeType"),
                    "countdown": resp.get("countdownInSeconds"),
                }), 412
            except Exception as e:
                app.logger.exception("[TR][resync] connect failed")
                return jsonify({"ok": False, "error": str(e)}), 500

        # 3) Avec token -> fetch + normalisation
        raw = tr_fetch_api(token)
        norm = _normalize_tr_accounts(raw)
        new_cash_raw = norm.get("cash")
        new_cash = _extract_cash_amount(new_cash_raw)
        new_positions = norm.get("positions_flat")  # [{isin, name, units, avgPrice, productType}]
        tr_txs = raw.get("transactions") or []
        tx_total_tr = len(tr_txs)

        # 4) Charger les portefeuilles TR visés (avec lignes + produits + transactions pour l'état BDD)
        q = (
            s.query(Asset)
            .options(
                joinedload(Asset.portfolio)
                    .joinedload(AssetPortfolio.lines)
                    .joinedload(PortfolioLine.product),
                joinedload(Asset.portfolio)
                    .joinedload(AssetPortfolio.products),
            )
            .join(AssetPortfolio, AssetPortfolio.asset_id == Asset.id)
            .filter(Asset.user_id == uid, Asset.type == "portfolio",
                    AssetPortfolio.broker.ilike("%Trade Republic%"))
        )
        if asset_ids:
            q = q.filter(Asset.id.in_(asset_ids))

        portfolios = q.all()

        # 5) Mode LIST-ONLY : listing et logs, aucune écriture
        if list_only:
            tr_summary = {
                "cash": norm.get("cash"),
                "accounts": [
                    {
                        "productType": acc.get("productType"),
                        "positions_count": len(acc.get("positions") or []),
                    } for acc in (norm.get("accounts") or [])
                ],
                "positions_total": len(norm.get("positions_flat") or []),
                "transactions_total": tx_total_tr,
            }

            def _pt_for_line(pf, ln):
                if ln.product and ln.product.product_type:
                    return ln.product.product_type
                for pp in pf.products or []:
                    if ln.product_id == pp.id:
                        return pp.product_type
                return None

            db_portfolios.append({
                "asset_id": a.id,
                "asset_label": a.label,
                "broker": pf.broker,
                "products": [{"id": pp.id, "product_type": pp.product_type} for pp in (pf.products or [])],
                "lines": [{
                    "id": ln.id,
                    "isin": ln.isin,
                    "label": ln.label,
                    "units": float(ln.units) if ln.units is not None else None,
                    "avg_price": float(ln.avg_price) if ln.avg_price is not None else None,
                    "product_type": _pt_for_line(pf, ln),
                    "beneficiary_id": ln.beneficiary_id,
                } for ln in (pf.lines or [])],
                # ✅ transactions = projection des AssetEvent
                "transactions": _events_to_tx_view(s, uid, a.id),
            })


            try:
                app.logger.info("📦 [TR][LIST] GLOBAL = %s", json.dumps(tr_summary, ensure_ascii=False, indent=2)[:80000])
                # dans list_only (pour compact)
                compact = []
                for d in db_portfolios:
                    tx_count = (s.query(func.count(AssetEvent.id))
                                .filter(AssetEvent.user_id == uid,
                                        AssetEvent.asset_id == d["asset_id"],
                                        AssetEvent.status == "posted",
                                        AssetEvent.kind.in_(["portfolio_trade","dividend"]))
                                .scalar() or 0)
                    compact.append({
                        "asset_id": d["asset_id"],
                        "asset_label": d["asset_label"],
                        "products": d["products"],
                        "lines_count": len(d["lines"]),
                        "transactions_count": int(tx_count),
                    })
            except Exception:
                pass

            return jsonify({
                "ok": True,
                "mode": "listing",
                "dry_run": True,
                "tr": tr_summary,
                "db": {"portfolios": db_portfolios}
            }), 200

        # 6) Mode DRY-RUN (avec option apply.transactions)
        #    -> on ne touche ni cash ni positions (on log seulement)
        diff_summary = {
            "cash": {"tr": new_cash, "db": None, "delta": None},
            "positions": {
                "tr_count": len(new_positions or []),
                "db_count": sum(len(a.portfolio.lines or []) for a in portfolios),
            }
        }

        # Application sélective des transactions
        tx_stats_global = {"created": 0, "updated": 0, "linked": 0}

        if apply_tx:
            if not portfolios:
                return jsonify({
                    "ok": False,
                    "error": "Aucun portefeuille Trade Republic ciblé. Passe 'asset_ids' si nécessaire."
                }), 400

            for asset in portfolios:
                stats = upsert_tr_asset_events(s, uid, asset, tr_txs)
                # upsert_tr_asset_events commit déjà; on agrège les stats
                for k in tx_stats_global:
                    tx_stats_global[k] += int(stats.get(k, 0))

            app.logger.info("🧾 [TR][APPLY_TX] Upsert events OK: %s (source tx=%d)",
                            tx_stats_global, tx_total_tr)
        else:
            app.logger.info("🧪 [TR][DRYRUN] Transactions détectées: %d (aucune écriture)", tx_total_tr)

        cash_update = {"applied": False, "updated_assets": [], "new_value": new_cash}

        if apply_cash:
            if new_cash is None:
                return jsonify({"ok": False, "error": "Cash TR manquant/illisible"}), 400
            if not portfolios:
                return jsonify({"ok": False, "error": "Aucun portefeuille Trade Republic ciblé."}), 400

            for a in portfolios:
                a.current_value = Decimal(str(new_cash))  # ✅ avoid float rounding
            s.commit()
            cash_update["applied"] = True
        else:
            app.logger.info("🧪 [TR][DRYRUN] Cash détecté: %s (aucune écriture)", new_cash)


        # Vue compacte BDD pour retour
        db_compact = []
        for a in portfolios:
            tx_count = (s.query(func.count(AssetEvent.id))
                        .filter(AssetEvent.user_id == uid,
                                AssetEvent.asset_id == a.id,
                                AssetEvent.status == "posted",
                                AssetEvent.kind.in_(["portfolio_trade","dividend"]))
                        .scalar() or 0)
            db_compact.append({
                "asset_id": a.id,
                "asset_label": a.label,
                "broker": a.portfolio.broker,
                "lines_count": len(a.portfolio.lines or []),
                "transactions_count": int(tx_count),
            })

        return jsonify({
            "ok": True,
            "mode": "dry_run_except_transactions" if apply_tx else "dry_run",
            "apply": {"transactions": apply_tx, "cash": apply_cash},     # 👈
            "tr": {
                "cash": new_cash,
                "positions_total": len(new_positions or []),
                "transactions_total": tx_total_tr
            },
            "db": {"portfolios": db_compact},
            "diff": diff_summary,
            "tx_upsert_stats": tx_stats_global if apply_tx else None,
            "cash_update": cash_update                                     # 👈
        }), 200


    except Exception as e:
        app.logger.exception("[TR][resync] failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        s.close()

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

        # ⚖️ Ajustement par les événements 'posted' jusqu'à aujourd'hui
        try:
           today = datetime.utcnow().date()
           delta = (session.query(func.coalesce(func.sum(AssetEvent.amount), 0.0))
                    .filter(
                        AssetEvent.user_id == asset.user_id,
                        AssetEvent.asset_id == asset.id,
                        AssetEvent.status == "posted",
                        AssetEvent.kind.in_(["cash_op", "transfer"]),
                        AssetEvent.value_date <= today
                    ).scalar() or 0.0)
           effective = (lv.balance or 0.0) + float(delta)
           base["details"]["balance_effective"] = float(effective)
        except Exception:
            # en cas de pépin, on n’empêche pas la réponse
            base["details"]["balance_effective"] = base["details"]["balance"]

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
    beneficiary_id = parse_int(payload.get("beneficiary_id"))

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
            asset.beneficiary_id = parse_int(payload["beneficiary_id"])

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
    phone = (data.get("phone") or "").strip()
    pin   = (data.get("pin")   or "").strip()

    s = Session()
    try:
        link = s.query(BrokerLink).filter_by(user_id=uid, broker="Trade Republic").first()

        # ➜ compléter avec ce qui est en base si manquant
        if (not phone) and link and link.phone_e164:
            phone = link.phone_e164

        if (not pin) and link and link.pin_enc:
            try:
                pin = dec_secret(link.pin_enc)
            except Exception:
                pin = ""

        if not phone or not pin:
            # Petit debug safe (ne log pas le PIN)
            app.logger.debug("TR /connect missing fields: phone=%s pin_len=%s",
                             bool(phone), len(pin) if pin else 0)
            return jsonify({"ok": False, "error": "phone and pin required"}), 400

        resp = tr_connect_api(phone, pin)
        return jsonify({
            "ok": True,
            "processId": resp.get("processId"),
            "countdown": resp.get("countdownInSeconds"),
            "challengeType": resp.get("challengeType"),   # p.ex. "PUSH_NOTIFICATION" / "SMS"
            "otpType": resp.get("otpType"),               # p.ex. "SMS"
            "delivery": resp.get("delivery"),             # selon l’API, si dispo
            "message": resp.get("message"),
        }), 200

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

# --- helper à garder hors route ---
def _flatten_positions(pos_list):
    flat = []
    for item in pos_list or []:
        # groupe TR: pas d'ISIN mais une clé "positions"
        if isinstance(item, dict) and not item.get("isin") and isinstance(item.get("positions"), list):
            flat.extend(item["positions"])
        else:
            flat.append(item)
    return flat

@app.route("/api/broker/traderepublic/portfolio", methods=["GET","POST"])
@jwt_required()
def tr_portfolio():
    # token via GET ou POST
    if request.method == "GET":
        token = request.args.get("token")
    else:
        data = request.get_json() or {}
        token = data.get("token")

    if not token:
        return jsonify({"ok": False, "error": "Token requis"}), 400

    try:
        def _num(x):
            try:
                if x is None:
                    return None
                if isinstance(x, (int, float)):
                    return float(x)
                return float(str(x).replace(",", "."))
            except Exception:
                return None

        raw = tr_fetch_api(token)
        # raw doit contenir au moins: cash, accounts (avec positions), transactions
        norm = _normalize_tr_accounts(raw)

        new_cash = _extract_cash_amount(norm.get("cash"))  # ✅ one source of truth
        new_positions = norm.get("positions_flat")
        tr_txs = raw.get("transactions") or []

        # ---------- 1) Indices PEA/PER par compte depuis l'historique ----------
        hints = {}
        for tx in raw.get("transactions", []) or []:
            acc = (str(tx.get("cashAccountNumber") or "").strip())
            evt = (tx.get("eventType") or "").lower()
            sub = (tx.get("subtitle") or "").lower()
            if not acc:
                continue
            if evt.startswith("pea_") or "pea" in sub:
                hints[acc] = "PEA"
            elif evt.startswith("per_") or "retirement" in sub or " per " in f" {sub} ":
                hints[acc] = "PER"

        # ---------- 2) Normalisation des comptes + positions ----------
        accounts = []
        for acc in raw.get("accounts", []) or []:
            cash_acc  = (acc.get("cashAccountNumber") or "").strip()
            sec_acc   = (acc.get("securitiesAccountNumber") or "").strip()
            ptype_raw = (acc.get("productType") or "").lower()

            # Certains comptes TR renvoient productType = "tax_wrapper_*"
            inferred = hints.get(cash_acc) if "tax_wrapper" in ptype_raw else None
            normalized_type = inferred or map_tr_product_type(ptype_raw) or "CTO"

            # positions dans cet "account" (aplatissement des groupes éventuels)
            normalized_positions = []
            for p in _flatten_positions(acc.get("positions")):
                # ignorer les entrées vides
                if not (p.get("isin") or p.get("name") or p.get("label")):
                    continue

                # TR: averageBuyIn / avgPrice.value...
                avg = p.get("averageBuyIn")
                if avg is None and isinstance(p.get("avgPrice"), dict):
                    avg = p["avgPrice"].get("value")
                elif avg is None:
                    avg = p.get("avgPrice")

                units = p.get("netSize") or p.get("virtualSize") or p.get("quantity") or p.get("units")

                normalized_positions.append({
                    "isin": p.get("isin"),
                    "name": p.get("name") or p.get("label"),
                    "units": _num(units),
                    "avgPrice": _num(avg),
                    "productType": normalized_type,   # 👈 ajouté
                })

            accounts.append({
                "cashAccountNumber": cash_acc,
                "securitiesAccountNumber": sec_acc,
                "productType": normalized_type,   # "PEA"/"CTO"/"PER"/"AV"
                "positions": normalized_positions,
            })

        first_position = accounts[0]["positions"][0] if (accounts and accounts[0]["positions"]) else None

        return jsonify({
            "ok": True,
            "cash": raw.get("cash"),
            "positions": accounts,                 # <== idem shape, positions = vraies lignes
            "transactions": raw.get("transactions", []),
            "debug_first_position": first_position,
        }), 200

    except Exception as e:
        app.logger.exception("❌ /api/broker/traderepublic/portfolio failed")
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

        # 0.b) Vérifier/compléter produits_invest par ISIN
        try:
            products_sync = upsert_produits_from_positions(session, positions)
            app.logger.info("produits_invest sync: created=%d existing=%d",
                            len(products_sync.get("created", [])),
                            len(products_sync.get("existing", [])))
        except Exception as _e:
            app.logger.warning("produits_invest sync failed: %s", _e)
            products_sync = {"created": [], "existing": []}

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
        app.logger.info("TR import: %d positions, %d transactions, %d allocations",
                    len(positions or []), len(transactions or []), len(allocations or []))
        
        stats = upsert_tr_asset_events(session, user_id, asset, transactions)
        app.logger.info("TR events upsert: %s", stats)

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

# ---------------------------------------------------------
# Asset Events (grand livre des impondérables)
# ---------------------------------------------------------

@app.route("/api/events", methods=["POST"])
@jwt_required()
def create_event():
    user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    kind = data.get("kind")
    if not kind:
        return jsonify({"ok": False, "error": "kind required"}), 400

    status = data.get("status") or "posted"
    value_date = parse_date(data.get("value_date"))
    if not value_date:
        return jsonify({"ok": False, "error": "value_date (YYYY-MM-DD) required"}), 400

    rrule = data.get("rrule")
    end_date = parse_date(data.get("end_date"))
    note = data.get("note")
    category = data.get("category")
    extra = data.get("data") or {}

    session = Session()
    try:
        # ---- TRANSFER (crée 2 events) ----
        if kind == "transfer":
            amount = parse_float(data.get("amount"))
            if amount is None or amount <= 0:
                return jsonify({"ok": False, "error": "amount > 0 required for transfer"}), 400

            src_id = data.get("source_asset_id") or data.get("asset_id")
            dst_id = data.get("target_asset_id")
            if not src_id or not dst_id:
                return jsonify({"ok": False, "error": "source_asset_id and target_asset_id required"}), 400
            if src_id == dst_id:
                return jsonify({"ok": False, "error": "source and target must differ"}), 400

            src = ensure_user_asset(session, user_id, int(src_id))
            dst = ensure_user_asset(session, user_id, int(dst_id))
            if not src or not dst:
                return jsonify({"ok": False, "error": "asset(s) not found or not owned"}), 404

            group_id = str(uuid.uuid4())

            debit = AssetEvent(
                user_id=user_id, asset_id=src.id, target_asset_id=dst.id,
                kind="transfer", status=status, value_date=value_date,
                rrule=rrule, end_date=end_date,
                amount=-abs(amount), category=category, note=note,
                transfer_group_id=group_id, data=extra
            )
            credit = AssetEvent(
                user_id=user_id, asset_id=dst.id, target_asset_id=src.id,
                kind="transfer", status=status, value_date=value_date,
                rrule=rrule, end_date=end_date,
                amount=abs(amount), category=category, note=note,
                transfer_group_id=group_id, data=extra
            )
            session.add_all([debit, credit])
            session.commit()
            return jsonify({"ok": True, "ids": [debit.id, credit.id], "transfer_group_id": group_id}), 201

        # ---- AUTRES KINDS ----
        asset_id = data.get("asset_id")
        if not asset_id:
            return jsonify({"ok": False, "error": "asset_id required"}), 400

        asset = ensure_user_asset(session, user_id, int(asset_id))
        if not asset:
            return jsonify({"ok": False, "error": "asset not found or not owned"}), 404

        amount     = parse_float(data.get("amount"))
        quantity   = parse_float(data.get("quantity"))
        unit_price = parse_float(data.get("unit_price"))
        isin       = (data.get("isin") or "").strip().upper() or None

        # Validation ISIN pour trades/dividendes
        if kind in ("portfolio_trade", "dividend") and not ensure_isin_known(session, isin):
            return jsonify({"ok": False, "error": "ISIN inconnu dans produits_invest"}), 400

        ev = AssetEvent(
            user_id=user_id, asset_id=asset.id,
            kind=kind, status=status, value_date=value_date,
            rrule=rrule, end_date=end_date,
            amount=amount, quantity=quantity, unit_price=unit_price,
            isin=isin, category=category, note=note, data=extra
        )

        # Lier à une ligne portefeuille si possible
        if isin and asset.type == "portfolio":
            line = (session.query(PortfolioLine)
                    .join(AssetPortfolio, PortfolioLine.portfolio_id == AssetPortfolio.id)
                    .filter(AssetPortfolio.asset_id == asset.id, PortfolioLine.isin == isin)
                    .first())
            if line:
                ev.portfolio_line_id = line.id

        session.add(ev)
        session.flush()

        session.commit()
        return jsonify({"ok": True, "id": ev.id}), 201

    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/events", methods=["GET"])
@jwt_required()
def list_events():
    user_id = int(get_jwt_identity())
    asset_id = request.args.get("asset_id", type=int)
    kind = request.args.get("kind")
    status = request.args.get("status")
    isin = request.args.get("isin")
    dfrom = request.args.get("from")
    dto   = request.args.get("to")
    limit = request.args.get("limit", type=int) or 200
    offset= request.args.get("offset", type=int) or 0

    session = Session()
    try:
        q = session.query(AssetEvent).filter(AssetEvent.user_id == user_id)
        if asset_id:
            q = q.filter(AssetEvent.asset_id == asset_id)
        if kind:
            q = q.filter(AssetEvent.kind == kind)
        if status:
            q = q.filter(AssetEvent.status == status)
        if isin:
            q = q.filter(AssetEvent.isin == isin.upper())
        if dfrom:
            q = q.filter(AssetEvent.value_date >= dfrom)
        if dto:
            q = q.filter(AssetEvent.value_date <= dto)

        rows = (q.order_by(AssetEvent.value_date.asc(), AssetEvent.id.asc())
                  .limit(limit).offset(offset).all())

        def ser(e: AssetEvent):
            return {
                "id": e.id,
                "user_id": e.user_id,
                "asset_id": e.asset_id,
                "target_asset_id": e.target_asset_id,
                "transfer_group_id": e.transfer_group_id,
                "kind": e.kind,
                "status": e.status,
                "value_date": e.value_date.isoformat(),
                "rrule": e.rrule,
                "end_date": e.end_date.isoformat() if e.end_date else None,
                "amount": float(e.amount) if e.amount is not None else None,
                "quantity": float(e.quantity) if e.quantity is not None else None,
                "unit_price": float(e.unit_price) if e.unit_price is not None else None,
                "isin": e.isin,
                "portfolio_line_id": e.portfolio_line_id,
                "category": e.category,
                "note": e.note,
                "data": e.data or {},
                "posted_entity_type": e.posted_entity_type,
                "posted_entity_id": e.posted_entity_id,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            }

        return jsonify([ser(r) for r in rows]), 200
    finally:
        session.close()


@app.route("/api/events/<int:event_id>", methods=["PUT"])
@jwt_required()
def update_event(event_id):
    user_id = int(get_jwt_identity())
    data = request.get_json() or {}
    cascade = request.args.get("cascade") == "true"

    session = Session()
    try:
        ev = session.query(AssetEvent).filter_by(id=event_id, user_id=user_id).first()
        if not ev:
            return jsonify({"ok": False, "error": "event not found"}), 404

        # Mise à jour basique (on ne permet pas de changer 'kind')
        updatable = {
            "status": data.get("status"),
            "value_date": parse_date(data.get("value_date")) if "value_date" in data else ev.value_date,
            "rrule": data.get("rrule") if "rrule" in data else ev.rrule,
            "end_date": parse_date(data.get("end_date")) if "end_date" in data else ev.end_date,
            "amount": parse_float(data.get("amount")) if "amount" in data else ev.amount,
            "quantity": parse_float(data.get("quantity")) if "quantity" in data else ev.quantity,
            "unit_price": parse_float(data.get("unit_price")) if "unit_price" in data else ev.unit_price,
            "isin": (data.get("isin") or ev.isin or "").strip().upper() or None if "isin" in data else ev.isin,
            "category": data.get("category") if "category" in data else ev.category,
            "note": data.get("note") if "note" in data else ev.note,
            "data": data.get("data") if "data" in data else ev.data,
        }

        # Validation ISIN si modifié pour trade/dividend
        if ev.kind in ("portfolio_trade","dividend") and "isin" in data:
            if updatable["isin"] and not ensure_isin_known(session, updatable["isin"]):
                return jsonify({"ok": False, "error": "ISIN inconnu dans produits_invest"}), 400

        # Transfert => option cascade
        if ev.kind == "transfer" and cascade:
            siblings = (session.query(AssetEvent)
                        .filter(AssetEvent.transfer_group_id == ev.transfer_group_id,
                                AssetEvent.user_id == user_id)
                        .all())
            # on MAJ date, status, rrule, note, category, data, amount symétrique
            for s in siblings:
                s.status    = updatable["status"] or s.status
                s.value_date= updatable["value_date"] or s.value_date
                s.rrule     = updatable["rrule"] if "rrule" in data else s.rrule
                s.end_date  = updatable["end_date"] if "end_date" in data else s.end_date
                s.note      = updatable["note"] if "note" in data else s.note
                s.category  = updatable["category"] if "category" in data else s.category
                s.data      = updatable["data"] if "data" in data else s.data
                if "amount" in data and updatable["amount"] is not None:
                    s.amount = abs(updatable["amount"]) if s.amount and s.amount > 0 else -abs(updatable["amount"])
        else:
            # MAJ simple
            for k, v in updatable.items():
                setattr(ev, k, v)

        session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/events/<int:event_id>", methods=["DELETE"])
@jwt_required()
def delete_event(event_id):
    user_id = int(get_jwt_identity())
    cascade = request.args.get("cascade") == "true"

    session = Session()
    try:
        ev = session.query(AssetEvent).filter_by(id=event_id, user_id=user_id).first()
        if not ev:
            return jsonify({"ok": False, "error": "event not found"}), 404

        if ev.kind == "transfer" and cascade and ev.transfer_group_id:
            session.query(AssetEvent).filter_by(
                transfer_group_id=ev.transfer_group_id, user_id=user_id
            ).delete(synchronize_session=False)
        else:
            session.delete(ev)

        session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


from auth_google import register_google_auth_route
register_google_auth_route(app, app.config["JWT_SECRET_KEY"], engine)

# ✅ log
app.logger.info("🔎 URL MAP: %s", app.url_map)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
