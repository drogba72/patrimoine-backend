# scraper_tr.py
import os
import json
import asyncio
import logging
import requests
import websockets

# --- Config ---
TR_LOGIN_URL = "https://api.traderepublic.com/api/v1/auth/web/login"
logger = logging.getLogger(__name__)

# =========================================================
# AUTHENTICATION
# =========================================================
def connect(phone: str, pin: str) -> dict:
    """Init login → retourne processId et countdown."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/118.0.5993.117 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/json",
            "Origin": "https://app.traderepublic.com",
            "Referer": "https://app.traderepublic.com/",
        }

        r = requests.post(TR_LOGIN_URL, headers=headers,
                          json={"phoneNumber": phone, "pin": pin})
        r.raise_for_status()
        data = r.json()
        if "processId" not in data:
            raise RuntimeError("Connexion échouée : vérifiez numéro ou PIN")
        return data  # {processId, countdownInSeconds}
    except Exception as e:
        logger.error(f"❌ TR connect error: {e}")
        raise


def validate_2fa(process_id: str, code: str) -> str:
    """Valide code 2FA et renvoie un token de session."""
    try:
        url = f"{TR_LOGIN_URL}/{process_id}/{code}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/118.0.5993.117 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/json",
            "Origin": "https://app.traderepublic.com",
            "Referer": "https://app.traderepublic.com/",
        }

        r = requests.post(url, headers=headers)
        r.raise_for_status()
        cookies = r.cookies.get_dict()
        token = cookies.get("tr_session")
        if not token:
            raise RuntimeError("Token TR non reçu après validation 2FA")
        return token
    except Exception as e:
        logger.error(f"❌ TR 2FA error: {e}")
        raise


# =========================================================
# HELPERS
# =========================================================
async def safe_recv(ws, timeout=5):
    """Wrapper avec timeout pour éviter blocage infini."""
    try:
        return await asyncio.wait_for(ws.recv(), timeout=timeout)
    except asyncio.TimeoutError:
        return ""


# =========================================================
# FETCH TRANSACTIONS
# =========================================================
async def fetch_all_transactions(token: str, max_pages=50):
    """Récupère l’historique des transactions via WebSocket."""
    all_data = []
    message_id = 0

    async with websockets.connect("wss://api.traderepublic.com") as ws:
        locale_config = {
            "locale": "fr",
            "platformId": "webtrading",
            "platformVersion": "safari - 18.3.0",
            "clientId": "app.traderepublic.com",
            "clientVersion": "3.151.3"
        }
        await ws.send(f"connect 31 {json.dumps(locale_config)}")
        await safe_recv(ws)

        after_cursor = None
        for _ in range(max_pages):
            payload = {"type": "timelineTransactions", "token": token}
            if after_cursor:
                payload["after"] = after_cursor

            message_id += 1
            await ws.send(f"sub {message_id} {json.dumps(payload)}")
            response = await safe_recv(ws, timeout=10)
            await ws.send(f"unsub {message_id}")
            await safe_recv(ws, timeout=5)

            if not response:
                break

            start, end = response.find("{"), response.rfind("}")
            data = json.loads(response[start:end + 1] if start != -1 else "{}")

            items = data.get("items", [])
            if not items:
                break
            all_data.extend(items)

            after_cursor = data.get("cursors", {}).get("after")
            if not after_cursor:
                break

    return all_data


# =========================================================
# FETCH PORTFOLIO
# =========================================================
async def fetch_portfolio(token: str):
    """Récupère cash + comptes + positions via WebSocket."""
    message_id = 2000
    portfolio_data = {"cash": None, "accounts": []}

    async with websockets.connect("wss://api.traderepublic.com") as ws:
        locale_config = {
            "locale": "fr",
            "platformId": "webtrading",
            "platformVersion": "safari - 18.3.0",
            "clientId": "app.traderepublic.com",
            "clientVersion": "3.151.3"
        }
        await ws.send(f"connect 31 {json.dumps(locale_config)}")
        await safe_recv(ws)

        # --- Solde espèces
        message_id += 1
        await ws.send(f"sub {message_id} " + json.dumps({"type": "availableCash", "token": token}))
        resp_cash = await safe_recv(ws, timeout=10)
        await ws.send(f"unsub {message_id}")
        await safe_recv(ws)
        start, end = resp_cash.find("{"), resp_cash.rfind("}")
        portfolio_data["cash"] = json.loads(resp_cash[start:end+1]) if start != -1 else {}

        # --- Comptes titres
        message_id += 1
        await ws.send(f"sub {message_id} " + json.dumps({"type": "accountPairs", "token": token}))
        resp_accounts = await safe_recv(ws, timeout=10)
        await ws.send(f"unsub {message_id}")
        await safe_recv(ws)
        start, end = resp_accounts.find("{"), resp_accounts.rfind("}")
        accounts = json.loads(resp_accounts[start:end+1]) if start != -1 else {}
        portfolio_data["accounts"] = accounts.get("accounts", [])

        # --- Positions par compte
        for acc in portfolio_data["accounts"]:
            sec_acc_no = acc.get("securitiesAccountNumber")
            if not sec_acc_no:
                continue
            message_id += 1
            payload = {"type": "compactPortfolioByType", "secAccNo": sec_acc_no, "token": token}
            await ws.send(f"sub {message_id} " + json.dumps(payload))
            resp_positions = await safe_recv(ws, timeout=10)
            await ws.send(f"unsub {message_id}")
            await safe_recv(ws)
            start, end = resp_positions.find("{"), resp_positions.rfind("}")
            positions = json.loads(resp_positions[start:end+1]) if start != -1 else {}
            acc["positions"] = []
            for cat in positions.get("categories", []):
                for pos in cat.get("positions", []):
                    acc["positions"].append({
                        "isin": pos.get("instrument", {}).get("isin"),
                        "name": pos.get("instrument", {}).get("title"),
                        "units": pos.get("quantity"),
                        "avgPrice": pos.get("avgPrice", {}).get("value")
                    })


    return portfolio_data


# =========================================================
# PUBLIC WRAPPER
# =========================================================
def fetch_data(token: str) -> dict:
    """Récupère cash + positions + transactions pour un utilisateur TR."""
    try:
        portfolio, transactions = asyncio.run(_fetch_data_async(token))
        return {
            "cash": portfolio.get("cash"),
            "positions": portfolio.get("accounts", []),
            "transactions": transactions or []
        }
    except Exception as e:
        logger.error(f"❌ TR fetch_data error: {e}")
        raise


async def _fetch_data_async(token: str):
    portfolio = await fetch_portfolio(token)
    transactions = await fetch_all_transactions(token)
    return portfolio, transactions
