# scripts/update_market_data_pg.py
import os
from datetime import datetime, timedelta

import yfinance as yf
import pandas as pd
from sqlalchemy import create_engine, text

print("🚀 Lancement du script update_market_data_pg.py")

# Connexion à la BDD
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL n'est pas défini dans les variables d'environnement")
print("🔑 DATABASE_URL trouvé")

try:
    engine = create_engine(DATABASE_URL, future=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("✅ Connexion BDD OK")
except Exception as e:
    print("❌ Erreur connexion BDD :", e)
    raise


# --- Étape 1 : peupler produits_invest ---
def sync_produits_invest(conn):
    print("📥 Sync produits_invest ...")

    # Exemple de liste (remplacer par scraping/API plus tard)
    produits = [
        {"isin": "FR0000120073", "ticker": "TOTF.PA", "label": "TotalEnergies", "type": "action", "currency": "EUR", "market": "Euronext Paris", "sector": "Énergie"},
        {"isin": "FR0000121014", "ticker": "AI.PA", "label": "Air Liquide", "type": "action", "currency": "EUR", "market": "Euronext Paris", "sector": "Industrie"},
        {"isin": "FR0000131104", "ticker": "MC.PA", "label": "LVMH", "type": "action", "currency": "EUR", "market": "Euronext Paris", "sector": "Luxe"},
        {"isin": "FR0000133308", "ticker": "OR.PA", "label": "L'Oréal", "type": "action", "currency": "EUR", "market": "Euronext Paris", "sector": "Consommation"}
    ]

    sql = text("""
        INSERT INTO produits_invest (isin, ticker_yahoo, label, type, currency, market, sector, eligible_in)
        VALUES (:isin, :ticker, :label, :type, :currency, :market, :sector, '["PEA","CTO","PER","AV"]')
        ON CONFLICT (isin) DO UPDATE SET
          ticker_yahoo = EXCLUDED.ticker_yahoo,
          label = EXCLUDED.label,
          type = EXCLUDED.type,
          currency = EXCLUDED.currency,
          market = EXCLUDED.market,
          sector = EXCLUDED.sector;
    """)
    conn.execute(sql, produits)
    print(f"✅ {len(produits)} produits insérés/mis à jour dans produits_invest")


# --- Étape 2 : données journalières ---
def upsert_daily_prices(conn, produit_id, df: pd.DataFrame):
    if df is None or df.empty:
        print(f"⚠️ Aucun prix journalier pour produit_id={produit_id}")
        return

    sql = text("""
        INSERT INTO produits_histo (produit_id, date, open, high, low, close, volume)
        VALUES (:produit_id, :date, :open, :high, :low, :close, :volume)
        ON CONFLICT (produit_id, date)
        DO UPDATE SET
          open = EXCLUDED.open,
          high = EXCLUDED.high,
          low  = EXCLUDED.low,
          close = EXCLUDED.close,
          volume = EXCLUDED.volume;
    """)
    rows = []
    for idx, row in df.iterrows():
        d = idx.date()
        rows.append({
            "produit_id": produit_id,
            "date": d,
            "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
            "high": float(row["High"]) if pd.notna(row["High"]) else None,
            "low":  float(row["Low"])  if pd.notna(row["Low"])  else None,
            "close":float(row["Close"])if pd.notna(row["Close"])else None,
            "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else None,
        })
    conn.execute(sql, rows)
    print(f"✅ {len(rows)} lignes insérées dans produits_histo (produit_id={produit_id})")


# --- Étape 3 : données intraday ---
def upsert_intraday_prices(conn, produit_id, ticker):
    print(f"⏱ Récupération intraday {ticker}")
    try:
        df = yf.download(ticker, period="5d", interval="15m")
        if df.empty:
            print(f"⚠️ Pas de données intraday pour {ticker}")
            return
        sql = text("""
            INSERT INTO produits_intraday (produit_id, datetime, open, high, low, close, volume)
            VALUES (:produit_id, :datetime, :open, :high, :low, :close, :volume)
            ON CONFLICT (produit_id, datetime)
            DO UPDATE SET
              open = EXCLUDED.open,
              high = EXCLUDED.high,
              low  = EXCLUDED.low,
              close = EXCLUDED.close,
              volume = EXCLUDED.volume;
        """)
        rows = []
        for idx, row in df.iterrows():
            rows.append({
                "produit_id": produit_id,
                "datetime": idx.to_pydatetime(),
                "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
                "high": float(row["High"]) if pd.notna(row["High"]) else None,
                "low":  float(row["Low"])  if pd.notna(row["Low"])  else None,
                "close":float(row["Close"])if pd.notna(row["Close"])else None,
                "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else None,
            })
        conn.execute(sql, rows)
        print(f"✅ {len(rows)} lignes intraday insérées (produit_id={produit_id})")
    except Exception as e:
        print(f"❌ Intraday {ticker} : {e}")


# --- Étape 4 : indicateurs techniques ---
def compute_and_upsert_indicators(conn, produit_id):
    closes = conn.execute(text("""
        SELECT date, close FROM produits_histo
        WHERE produit_id = :pid AND close IS NOT NULL
        ORDER BY date ASC
    """), {"pid": produit_id}).fetchall()

    if not closes:
        return

    df = pd.DataFrame(closes, columns=["date","close"]).set_index("date")
    df["ma20"] = df["close"].rolling(window=20).mean()
    df["ma50"] = df["close"].rolling(window=50).mean()

    # RSI
    delta = df["close"].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    roll_up = up.rolling(14).mean()
    roll_down = down.rolling(14).mean()
    rs = roll_up / roll_down
    df["rsi14"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    sql = text("""
        INSERT INTO produits_indicateurs (produit_id, date, ma20, ma50, rsi14, macd, signal)
        VALUES (:pid, :date, :ma20, :ma50, :rsi14, :macd, :signal)
        ON CONFLICT (produit_id, date)
        DO UPDATE SET
          ma20 = EXCLUDED.ma20,
          ma50 = EXCLUDED.ma50,
          rsi14 = EXCLUDED.rsi14,
          macd = EXCLUDED.macd,
          signal = EXCLUDED.signal;
    """)
    payload = []
    for d, r in df.iterrows():
        payload.append({
            "pid": produit_id,
            "date": d,
            "ma20": float(r["ma20"]) if pd.notna(r["ma20"]) else None,
            "ma50": float(r["ma50"]) if pd.notna(r["ma50"]) else None,
            "rsi14": float(r["rsi14"]) if pd.notna(r["rsi14"]) else None,
            "macd": float(r["macd"]) if pd.notna(r["macd"]) else None,
            "signal": float(r["signal"]) if pd.notna(r["signal"]) else None,
        })
    conn.execute(sql, payload)
    print(f"✅ Indicateurs insérés (produit_id={produit_id}, {len(payload)} lignes)")


# --- MAIN ---
def main():
    print("⏳ Update market data (PG) ...")
    with engine.begin() as conn:
        # Étape 1 : mise à jour liste des produits
        sync_produits_invest(conn)

        produits = conn.execute(text("""
            SELECT id, ticker_yahoo FROM produits_invest
            WHERE ticker_yahoo IS NOT NULL AND ticker_yahoo <> ''
        """)).fetchall()

        print(f"📊 Produits trouvés : {len(produits)}")

        end = datetime.utcnow().date()
        start = end - timedelta(days=10)

        for pid, ticker in produits:
            try:
                print(f"↳ {ticker} (id={pid})")

                # Historique daily
                df = yf.download(ticker, start=start, end=end + timedelta(days=1), interval="1d")
                upsert_daily_prices(conn, pid, df)

                # Intraday
                upsert_intraday_prices(conn, pid, ticker)

                # Indicateurs
                compute_and_upsert_indicators(conn, pid)

            except Exception as e:
                print(f"❌ Erreur {ticker} : {e}")

    print("✅ Terminé", datetime.utcnow().isoformat())


if __name__ == "__main__":
    main()
