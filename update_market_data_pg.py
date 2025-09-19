# update_market_data_pg.py
import os
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf
import pandas as pd
from sqlalchemy import create_engine, text

print("🚀 Lancement du script update_market_data_pg.py")

# =========================
# Connexion BDD
# =========================
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL n'est pas défini")
print("🔑 DATABASE_URL trouvé")

engine = create_engine(DATABASE_URL, future=True)
with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
print("✅ Connexion BDD OK")

# =========================
# Sources de tickers
# =========================

def _clean(s):
    if s is None:
        return None
    s = str(s).strip()
    return s or None

def fetch_tickers_from_fmp():
    """
    Récupère une large liste de tickers via FinancialModelingPrep.
    Filtre sur Euronext (toutes places) + Paris.
    Remplit label, isin, ticker, market, type/currency/sector quand dispo.
    """
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        print("⚠️ Pas de clé FMP_API_KEY → skip FMP")
        return []

    url = f"https://financialmodelingprep.com/api/v3/stock/list?apikey={api_key}"
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print("❌ Erreur API FMP:", e)
        return []

    tickers = []
    for it in data:
        exch = _clean(it.get("exchangeShortName"))
        sym  = _clean(it.get("symbol"))
        name = _clean(it.get("name"))

        # On garde Euronext (AMS/BRU/PAR/LIS/DUB/MIL, etc.) et Paris explicite
        keep = False
        if exch:
            exl = exch.lower()
            if "euronext" in exl or "paris" in exl or exl in {"epa", "par"}:
                keep = True

        if not keep:
            continue
        if not sym:
            continue

        # Mapping "type" -> nos valeurs {action, etf, fonds}
        typ = _clean(it.get("type"))  # ex: stock, etf, adr ...
        if typ:
            tl = typ.lower()
            if "etf" in tl:
                typ_norm = "etf"
            elif "fund" in tl or "funds" in tl:
                typ_norm = "fonds"
            else:
                typ_norm = "action"
        else:
            typ_norm = "action"

        tickers.append({
            "isin": _clean(it.get("isin")),
            "ticker": sym,
            "label": name or sym,
            "market": exch or "EURONEXT",
            "type": typ_norm,
            "currency": _clean(it.get("currency")),
            "sector": _clean(it.get("sector")) or _clean(it.get("industry"))  # au mieux
        })

    # Dédupe par ticker
    seen = set()
    dedup = []
    for t in tickers:
        if t["ticker"] in seen:
            continue
        seen.add(t["ticker"])
        dedup.append(t)

    print(f"📥 {len(dedup)} tickers récupérés via FMP (après filtre/dédup)")
    return dedup

def fetch_tickers_fallback():
    """
    Fallback simple et fiable (sans scrap JS) pour tester le flux :
    une liste Euronext Paris connue.
    """
    base = [
        # CAC40 / grosses caps Euronext Paris
        "AI.PA",   # Air Liquide
        "OR.PA",   # L'Oréal
        "MC.PA",   # LVMH
        "BNP.PA",  # BNP Paribas
        "DG.PA",   # Vinci
        "ENGI.PA", # Engie
        "SAN.PA",  # Sanofi
        "AIR.PA",  # Airbus
        "ACA.PA",  # Crédit Agricole
        "GLE.PA",  # Société Générale
    ]
    tickers = [{
        "isin": None,
        "ticker": t,
        "label": t,
        "market": "EURONEXT PARIS",
        "type": "action",
        "currency": "EUR",
        "sector": None
    } for t in base]
    print(f"📥 {len(tickers)} tickers fallback (liste fixe)")
    return tickers

# =========================
# Upserts
# =========================

def upsert_produits_invest(conn, tickers):
    """
    Insère / met à jour produits_invest.
    ⚠️ La table a 'type' NOT NULL → on fournit 'type' (défaut: action).
    D'autres colonnes sont facultatives.
    Conflit sur ticker_yahoo (contrainte unique unique_ticker).
    """
    if not tickers:
        return

    # Normalisation + valeurs par défaut
    for t in tickers:
        t["isin"]    = _clean(t.get("isin"))
        t["ticker"]  = _clean(t.get("ticker"))
        t["label"]   = _clean(t.get("label")) or t["ticker"]
        t["market"]  = _clean(t.get("market")) or "EURONEXT"
        t["type"]    = _clean(t.get("type")) or "action"
        t["currency"]= _clean(t.get("currency"))
        t["sector"]  = _clean(t.get("sector"))

    sql = text("""
        INSERT INTO produits_invest (isin, ticker_yahoo, label, market, type, currency, sector)
        VALUES (:isin, :ticker, :label, :market, :type, :currency, :sector)
        ON CONFLICT (ticker_yahoo)
        DO UPDATE SET
          isin    = EXCLUDED.isin,
          label   = EXCLUDED.label,
          market  = EXCLUDED.market,
          type    = EXCLUDED.type,
          currency= EXCLUDED.currency,
          sector  = EXCLUDED.sector;
    """)
    conn.execute(sql, tickers)
    print(f"✅ {len(tickers)} produits upsert dans produits_invest")

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
        d = idx.date() if isinstance(idx, (pd.Timestamp, datetime)) else pd.to_datetime(idx).date()
        rows.append({
            "produit_id": produit_id,
            "date": d,
            "open":  float(row.get("Open"))   if pd.notna(row.get("Open"))   else None,
            "high":  float(row.get("High"))   if pd.notna(row.get("High"))   else None,
            "low":   float(row.get("Low"))    if pd.notna(row.get("Low"))    else None,
            "close": float(row.get("Close"))  if pd.notna(row.get("Close"))  else None,
            "volume":int(row.get("Volume"))   if pd.notna(row.get("Volume")) else None,
        })

    if rows:
        conn.execute(sql, rows)
        print(f"💾 produits_histo +{len(rows)} lignes (id={produit_id})")

def upsert_intraday_prices(conn, produit_id, df: pd.DataFrame):
    """
    Alimente produits_intraday(produit_id, ts, price, volume).
    L’index est un DatetimeIndex; on force UTC et on enlève le tzinfo pour TIMESTAMPTZ.
    """
    if df is None or df.empty:
        print(f"⚠️ Pas de données intraday pour produit_id={produit_id}")
        return

    # Sécurité : s'assurer que l'index est bien en UTC, sans tzinfo
    idx = df.index
    if isinstance(idx, pd.DatetimeIndex):
        if idx.tz is not None:
            df = df.tz_convert("UTC")
        else:
            # on considère que c'est déjà UTC
            df.index = df.index.tz_localize("UTC")
    else:
        df.index = pd.to_datetime(df.index, utc=True)

    sql = text("""
        INSERT INTO produits_intraday (produit_id, ts, price, volume)
        VALUES (:pid, :ts, :price, :volume)
        ON CONFLICT (produit_id, ts) DO UPDATE SET
          price = EXCLUDED.price,
          volume = EXCLUDED.volume;
    """)

    rows = []
    for ts, row in df.iterrows():
        # ts est timezone-aware UTC → pour TIMESTAMPTZ, on peut passer un datetime aware
        rows.append({
            "pid": produit_id,
            "ts": ts.to_pydatetime(),
            "price": float(row.get("Close"))  if pd.notna(row.get("Close"))  else None,
            "volume": int(row.get("Volume"))  if pd.notna(row.get("Volume")) else None,
        })

    if rows:
        conn.execute(sql, rows)
        print(f"💾 produits_intraday +{len(rows)} lignes (id={produit_id})")

def compute_and_upsert_indicators(conn, produit_id):
    closes = conn.execute(text("""
        SELECT date, close FROM produits_histo
        WHERE produit_id=:pid AND close IS NOT NULL
        ORDER BY date ASC
    """), {"pid": produit_id}).fetchall()
    if not closes:
        print(f"ℹ️ Pas assez de données pour indicateurs (id={produit_id})")
        return

    df = pd.DataFrame(closes, columns=["date", "close"]).set_index("date")
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma50"] = df["close"].rolling(50).mean()

    delta = df["close"].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    rs = up.rolling(14).mean() / down.rolling(14).mean()
    df["rsi14"] = 100 - (100/(1+rs))

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
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
            "pid": produit_id,
            "date": d,
            "ma20":  float(r["ma20"])   if pd.notna(r["ma20"])   else None,
            "ma50":  float(r["ma50"])   if pd.notna(r["ma50"])   else None,
            "rsi14": float(r["rsi14"])  if pd.notna(r["rsi14"])  else None,
            "macd":  float(r["macd"])   if pd.notna(r["macd"])   else None,
            "signal":float(r["signal"]) if pd.notna(r["signal"]) else None,
        })

    if rows:
        conn.execute(sql, rows)
        print(f"📊 produits_indicateurs +{len(rows)} lignes (id={produit_id})")

# =========================
# Main
# =========================
def main():
    print("⏳ Update market data (PG) ...")
    max_tickers = int(os.environ.get("MAX_TICKERS", "0") or "0")  # 0 = illimité

    with engine.begin() as conn:
        # 1) Récupération des tickers
        tickers = fetch_tickers_from_fmp()
        if not tickers:
            tickers = fetch_tickers_fallback()
        if not tickers:
            print("❌ Aucune source de tickers disponible")
            return

        # Limite éventuelle pour maîtriser les quotas API
        if max_tickers > 0:
            tickers = tickers[:max_tickers]
            print(f"✂️ Limite MAX_TICKERS={max_tickers} → {len(tickers)} gardés")

        # 2) Upsert produits
        upsert_produits_invest(conn, tickers)

        # 3) Liste complète des produits à traiter
        produits = conn.execute(text("""
            SELECT id, ticker_yahoo FROM produits_invest
            WHERE ticker_yahoo IS NOT NULL AND ticker_yahoo <> ''
        """)).fetchall()
        print(f"📊 {len(produits)} produits à traiter")

        # Fenêtre d'historique (10 jours)
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=10)

        # 4) Boucle de téléchargement
        for pid, ticker in produits:
            try:
                print(f"↳ {ticker} (id={pid})")

                # Daily OHLC
                df_day = yf.download(
                    ticker,
                    start=start,
                    end=end + timedelta(days=1),
                    interval="1d",
                    auto_adjust=False,   # pour garder Open/High/Low/Close bruts
                    progress=False
                )
                print(f"   • Daily: {len(df_day)} lignes")
                upsert_daily_prices(conn, pid, df_day)

                # Indicateurs
                compute_and_upsert_indicators(conn, pid)

                # Intraday Close/Volume (5 jours, 15m)
                df_intra = yf.download(
                    ticker,
                    period="5d",
                    interval="15m",
                    auto_adjust=False,
                    progress=False
                )
                print(f"   • Intraday: {len(df_intra)} lignes")
                upsert_intraday_prices(conn, pid, df_intra)

            except Exception as e:
                print(f"❌ Erreur {ticker} :", e)

    print("✅ Terminé", datetime.utcnow().isoformat())

if __name__ == "__main__":
    main()
