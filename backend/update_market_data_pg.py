# scripts/update_market_data_pg.py
import os
from datetime import datetime, timedelta

import yfinance as yf
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ["DATABASE_URL"]  # ex: postgres://user:pass@host:5432/db
engine = create_engine(DATABASE_URL, future=True)

def upsert_daily_prices(conn, produit_id, df: pd.DataFrame):
    # df index = DatetimeIndex, colonnes: Open, High, Low, Close, Volume
    if df is None or df.empty:
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
            "open": float(row.get("Open")) if pd.notna(row.get("Open")) else None,
            "high": float(row.get("High")) if pd.notna(row.get("High")) else None,
            "low":  float(row.get("Low"))  if pd.notna(row.get("Low"))  else None,
            "close":float(row.get("Close"))if pd.notna(row.get("Close"))else None,
            "volume": int(row.get("Volume")) if pd.notna(row.get("Volume")) else None,
        })
    conn.execute(sql, rows)

def compute_and_upsert_indicators(conn, produit_id):
    # Récupère les dernières 250 clôtures et calcule MA/RSI/MACD
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

    # UPSERT indicateurs pour les dates calculées
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

def main():
    print("⏳ Update market data (PG) ...")
    with engine.begin() as conn:
        produits = conn.execute(text("""
            SELECT id, ticker_yahoo FROM produits_invest
            WHERE ticker_yahoo IS NOT NULL AND ticker_yahoo <> ''
        """)).fetchall()

        # Récupérer J-10 → aujourd’hui (sécurisant)
        end = datetime.utcnow().date()
        start = end - timedelta(days=10)

        for p in produits:
            pid, ticker = p
            try:
                print(f"↳ {ticker} (id={pid})")
                df = yf.download(ticker, start=start, end=end + timedelta(days=1), interval="1d", auto_adjust=False)
                upsert_daily_prices(conn, pid, df)
                compute_and_upsert_indicators(conn, pid)
            except Exception as e:
                print("❌", ticker, e)

    print("✅ Terminé", datetime.utcnow().isoformat())

if __name__ == "__main__":
    main()
