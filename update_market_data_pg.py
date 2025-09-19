# scripts/update_market_data_pg.py
import os
from datetime import datetime, timedelta

import requests
import yfinance as yf
import pandas as pd
from sqlalchemy import create_engine, text
from bs4 import BeautifulSoup

print("🚀 Lancement du script update_market_data_pg.py")

# -----------------------------
# Connexion BDD
# -----------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL n'est pas défini")
print("🔑 DATABASE_URL trouvé")

engine = create_engine(DATABASE_URL, future=True)
with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
print("✅ Connexion BDD OK")

# -----------------------------
# Fonctions utilitaires
# -----------------------------

def fetch_tickers_from_fmp():
    """Récupère la liste complète des tickers Euronext via FinancialModelingPrep"""
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        print("⚠️ Pas de clé FMP_API_KEY → skip")
        return []

    url = f"https://financialmodelingprep.com/api/v3/stock/list?apikey={api_key}"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print("❌ Erreur API FMP:", e)
        return []

    tickers = []
    for item in data:
        exch = str(item.get("exchangeShortName", "")).lower()
        if "paris" in exch or "euronext" in exch:
            tickers.append({
                "isin": item.get("isin"),
                "ticker": item.get("symbol"),
                "label": item.get("name"),
                "market": exch.upper()
            })
    print(f"📥 {len(tickers)} tickers récupérés via FMP")
    return tickers

def fetch_tickers_from_yahoo():
    """Scrape Yahoo Finance screener France (fallback)"""
    url = "https://finance.yahoo.com/screener/predefined/most_actives_fr"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print("❌ Erreur scrap Yahoo:", e)
        return []

    tickers = []
    for row in soup.select("table tbody tr"):
        cols = row.find_all("td")
        if not cols: 
            continue
        ticker = cols[0].get_text(strip=True)
        name = cols[1].get_text(strip=True) if len(cols) > 1 else None
        tickers.append({
            "isin": None,
            "ticker": ticker,
            "label": name,
            "market": "Yahoo FR Screener"
        })
    print(f"📥 {len(tickers)} tickers récupérés via Yahoo Finance screener")
    return tickers

def upsert_produits_invest(conn, tickers):
    """Insère / met à jour produits_invest"""
    sql = text("""
        INSERT INTO produits_invest (isin, ticker_yahoo, label, market)
        VALUES (:isin, :ticker, :label, :market)
        ON CONFLICT (ticker_yahoo)
        DO UPDATE SET
          isin = EXCLUDED.isin,
          label = EXCLUDED.label,
          market = EXCLUDED.market;
    """)
    conn.execute(sql, tickers)
    print(f"✅ {len(tickers)} produits insérés/mis à jour dans produits_invest")

def upsert_daily_prices(conn, produit_id, df: pd.DataFrame):
    if df is None or df.empty:
        print(f"⚠️ Aucun prix journalier pour produit_id={produit_id}")
        return
    sql = text("""
        INSERT INTO produits_histo (produit_id, date, open, high, low, close, volume)
        VALUES (:produit_id, :date, :open, :high, :low, :close, :volume)
        ON CONFLICT (produit_id, date) DO UPDATE SET
          open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
          close=EXCLUDED.close, volume=EXCLUDED.volume;
    """)
    rows = []
    for idx, row in df.iterrows():
        rows.append({
            "produit_id": produit_id,
            "date": idx.date(),
            "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
            "high": float(row["High"]) if pd.notna(row["High"]) else None,
            "low":  float(row["Low"]) if pd.notna(row["Low"]) else None,
            "close":float(row["Close"]) if pd.notna(row["Close"]) else None,
            "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else None,
        })
    conn.execute(sql, rows)
    print(f"💾 produits_histo +{len(rows)} lignes")

def upsert_intraday_prices(conn, produit_id, df: pd.DataFrame):
    if df is None or df.empty:
        print(f"⚠️ Pas de données intraday pour produit_id={produit_id}")
        return
    sql = text("""
        INSERT INTO produits_intraday (produit_id, datetime, open, high, low, close, volume)
        VALUES (:pid, :dt, :open, :high, :low, :close, :volume)
        ON CONFLICT (produit_id, datetime) DO UPDATE SET
          open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
          close=EXCLUDED.close, volume=EXCLUDED.volume;
    """)
    rows = []
    for idx, row in df.iterrows():
        rows.append({
            "pid": produit_id,
            "dt": idx.to_pydatetime(),
            "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
            "high": float(row["High"]) if pd.notna(row["High"]) else None,
            "low":  float(row["Low"]) if pd.notna(row["Low"]) else None,
            "close":float(row["Close"]) if pd.notna(row["Close"]) else None,
            "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else None,
        })
    conn.execute(sql, rows)
    print(f"💾 produits_intraday +{len(rows)} lignes")

def compute_and_upsert_indicators(conn, produit_id):
    closes = conn.execute(text("""
        SELECT date, close FROM produits_histo
        WHERE produit_id=:pid AND close IS NOT NULL
        ORDER BY date ASC
    """), {"pid": produit_id}).fetchall()
    if not closes: 
        return
    df = pd.DataFrame(closes, columns=["date","close"]).set_index("date")
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma50"] = df["close"].rolling(50).mean()
    delta = df["close"].diff()
    up, down = delta.clip(lower=0), -1*delta.clip(upper=0)
    rs = up.rolling(14).mean() / down.rolling(14).mean()
    df["rsi14"] = 100 - (100/(1+rs))
    ema12, ema26 = df["close"].ewm(span=12, adjust=False).mean(), df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    sql = text("""
        INSERT INTO produits_indicateurs (produit_id, date, ma20, ma50, rsi14, macd, signal)
        VALUES (:pid, :date, :ma20, :ma50, :rsi14, :macd, :signal)
        ON CONFLICT (produit_id, date) DO UPDATE SET
          ma20=EXCLUDED.ma20, ma50=EXCLUDED.ma50, rsi14=EXCLUDED.rsi14,
          macd=EXCLUDED.macd, signal=EXCLUDED.signal;
    """)
    rows = []
    for d, r in df.iterrows():
        rows.append({
            "pid": produit_id, "date": d,
            "ma20": float(r["ma20"]) if pd.notna(r["ma20"]) else None,
            "ma50": float(r["ma50"]) if pd.notna(r["ma50"]) else None,
            "rsi14": float(r["rsi14"]) if pd.notna(r["rsi14"]) else None,
            "macd": float(r["macd"]) if pd.notna(r["macd"]) else None,
            "signal": float(r["signal"]) if pd.notna(r["signal"]) else None,
        })
    conn.execute(sql, rows)
    print(f"📊 produits_indicateurs +{len(rows)} lignes")

# -----------------------------
# Main
# -----------------------------
def main():
    print("⏳ Update market data (PG) ...")
    with engine.begin() as conn:
        # Récupérer tickers
        tickers = fetch_tickers_from_fmp()
        if not tickers:
            tickers = fetch_tickers_from_yahoo()
        if not tickers:
            print("❌ Aucune source de tickers disponible")
            return
        upsert_produits_invest(conn, tickers)

        # Récupérer la liste complète depuis produits_invest
        produits = conn.execute(text("SELECT id, ticker_yahoo FROM produits_invest")).fetchall()
        print(f"📊 {len(produits)} produits à traiter")

        end, start = datetime.utcnow().date(), datetime.utcnow().date() - timedelta(days=10)
        for pid, ticker in produits:
            try:
                print(f"↳ {ticker} (id={pid})")
                df = yf.download(ticker, start=start, end=end+timedelta(days=1), interval="1d")
                upsert_daily_prices(conn, pid, df)
                compute_and_upsert_indicators(conn, pid)
                df_intra = yf.download(ticker, period="5d", interval="15m")
                upsert_intraday_prices(conn, pid, df_intra)
            except Exception as e:
                print(f"❌ Erreur {ticker} :", e)
    print("✅ Terminé", datetime.utcnow().isoformat())

if __name__ == "__main__":
    main()
