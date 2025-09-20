import os
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from justetf_scraping import overview

# Charger les variables d'environnement
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

def main():
    print("[*] Récupération des ETFs depuis JustETF...")
    df = overview.load_overview()
    print(f"✅ {len(df)} ETFs récupérés")

    # Normaliser les colonnes pour ta BDD
    df_final = df.reset_index()[["isin", "ticker", "name", "currency"]].rename(
        columns={
            "ticker": "ticker_yahoo",
            "name": "label",
        }
    )
    df_final["type"] = "etf"

    # 🔥 FIX : convertir les valeurs manquantes pandas en None (SQL-compatible)
    df_final = df_final.where(pd.notnull(df_final), None)

    print("[*] Exemple des 5 premières lignes :")
    print(df_final.head())

    # Connexion SQLAlchemy
    engine = create_engine(DB_URL)

    with engine.begin() as conn:
        for _, row in df_final.iterrows():
            conn.execute(
                text("""
                    INSERT INTO produits_invest (isin, ticker_yahoo, label, currency, type)
                    VALUES (:isin, :ticker_yahoo, :label, :currency, :type)
                    ON CONFLICT (isin) DO UPDATE SET
                        ticker_yahoo = EXCLUDED.ticker_yahoo,
                        label = EXCLUDED.label,
                        currency = EXCLUDED.currency,
                        type = EXCLUDED.type;
                """),
                {
                    "isin": row["isin"],
                    "ticker_yahoo": row["ticker_yahoo"],
                    "label": row["label"],
                    "currency": row["currency"],
                    "type": row["type"],
                }
            )

if __name__ == "__main__":
    main()
