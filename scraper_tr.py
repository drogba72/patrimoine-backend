# scraper_tr.py
import requests
import asyncio
import logging
from main import fetch_all_transactions_and_overview, fetch_portfolio

# --- Config ---
TR_LOGIN_URL = "https://api.traderepublic.com/api/v1/auth/web/login"

logger = logging.getLogger(__name__)


# -------------------------------------------------
# 1. Connexion initiale (PIN + téléphone)
# -------------------------------------------------
def connect(phone: str, pin: str) -> dict:
    """
    Lance la connexion avec TR.
    Retourne {processId, countdownInSeconds}.
    """
    try:
        headers = {"Content-Type": "application/json"}
        resp = requests.post(TR_LOGIN_URL, headers=headers, json={"phoneNumber": phone, "pin": pin})
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"✅ TR connect OK: {data}")
        return data
    except Exception as e:
        logger.error(f"❌ TR connect error: {e}")
        raise


# -------------------------------------------------
# 2. Validation 2FA (code SMS)
# -------------------------------------------------
def validate_2fa(process_id: str, code: str) -> str:
    """
    Valide le code SMS et retourne le token de session (cookie tr_session).
    """
    try:
        url = f"{TR_LOGIN_URL}/{process_id}/{code}"
        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, headers=headers)
        resp.raise_for_status()
        cookies = resp.cookies.get_dict()
        token = cookies.get("tr_session")
        if not token:
            raise RuntimeError("Token TR non reçu après validation 2FA")
        logger.info("✅ TR 2FA validé, token récupéré")
        return token
    except Exception as e:
        logger.error(f"❌ TR 2FA error: {e}")
        raise


# -------------------------------------------------
# 3. Récupération portefeuille + transactions
# -------------------------------------------------
async def _fetch_data_async(token: str):
    """
    Utilise les fonctions async de main.py pour récupérer
    portefeuille + transactions.
    """
    transactions = await fetch_all_transactions_and_overview(
        token,
        extract_details=False,
        output_format="json",
        output_folder="out"
    )
    portfolio = await fetch_portfolio(token, "out")
    return portfolio, transactions


def fetch_data(token: str) -> dict:
    """
    Récupère cash, positions et transactions pour un utilisateur TR.
    Retourne un dict prêt à être consommé par Flask.
    """
    try:
        portfolio, transactions = asyncio.run(_fetch_data_async(token))

        return {
            "cash": portfolio.get("cash"),
            "positions": portfolio.get("accounts", []),  # liste de positions
            "transactions": transactions or []
        }
    except Exception as e:
        logger.error(f"❌ TR fetch_data error: {e}")
        raise
