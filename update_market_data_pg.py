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


def upsert_daily_prices(conn, produit_id, df: pd.DataFrame):
    if df is None or df.empty:
        print(f"⚠️ Aucun prix à insérer pour produit_id={produit_id}")
        return

    print(f"💾 Insertion prix pour produit_id={produit_id}, {len(df)} lignes")
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
            "open": float(row.get("Open")) if pd.notna(row.get("Open")) else None,
            "high": float(row.get("High")) if pd.notna(row.get("High")) else None,
            "low":  float(row.get("Low"))  if pd.notna(row.get("Low"))  else None,
            "close":float(row.get("Close"))if pd.notna(row.get("Close"))else None,
            "volume": int(row.get("Volume")) if pd.notna(row.get("Volume")) else None,
        })
    conn.execute(sql, rows)
    print(f"✅ Insertion terminée pour produit_id={produit_id}")


def compute_and_upsert_indicators(conn, produit_id):
    print(f"📊 Calcul indicateurs pour produit_id={produit_id}")
    closes = conn.execute(text("""
        SELECT date, close FROM produits_histo
        WHERE produit_id = :pid AND close IS NOT NULL
        ORDER BY date ASC
    """), {"pid": produit_id}).fetchall()

    if not closes:
        print(f"⚠️ Pas de données de clôture pour produit_id={produit_id}")
        return

    df = pd.DataFrame(closes, columns=["date","close"]).set_index("date")
    df["ma20"] = df["close"].rolling(window=20).mean()
    df["ma50"] = df["close"].rolling(window=50).mean()

    # RSI 14
    delta = df["close"].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    roll_up = up.rolling(14).mean()
    roll_down = down.rolling(14).mean()
    rs = roll_up / roll_down
    df["rsi14"] = 100 - (100 / (1 + rs))

    # MACD (12/26 EMA) + signal 9
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
    print(f"✅ Indicateurs insérés pour produit_id={produit_id}, {len(payload)} lignes")


def main():
    print("⏳ Début de la mise à jour des données marché ...")
    with engine.begin() as conn:
        produits = conn.execute(text("""
            SELECT id, ticker_yahoo FROM produits_invest
            WHERE ticker_yahoo IS NOT NULL AND ticker_yahoo <> ''
        """)).fetchall()

        print(f"📦 {len(produits)} produits trouvés")

        end = datetime.utcnow().date()
        start = end - timedelta(days=10)

        for pid, ticker in produits:
            try:
                print(f"🔎 Téléchargement {ticker} (id={pid}) du {start} au {end}")
                df = yf.download(ticker, start=start, end=end + timedelta(days=1), interval="1d", auto_adjust=False)
                print(f"📥 {len(df)} lignes récupérées pour {ticker}")

                upsert_daily_prices(conn, pid, df)
                compute_and_upsert_indicators(conn, pid)
            except Exception as e:
                print(f"❌ Erreur sur {ticker} (id={pid}):", e)

    print("🏁 Terminé à", datetime.utcnow().isoformat())


if __name__ == "__main__":
    main()
