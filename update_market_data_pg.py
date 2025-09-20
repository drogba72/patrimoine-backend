import os
import pandas as pd
from sqlalchemy import create_engine, text
from justetf_scraping import overview
from dotenv import load_dotenv

# Charger les variables d'environnement (.env)
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

def main():
    print("[*] Récupération des ETFs depuis JustETF...")
    df = overview.load_overview()   # Scraping JustETF
    print(f"✅ {len(df)} ETFs récupérés")

    # Remettre l’index (isin) comme colonne
    df = df.reset_index()

    # Normaliser les colonnes selon ta BDD
    df_final = df[["isin", "ticker", "name", "currency"]].rename(
        columns={
            "ticker": "ticker_yahoo",
            "name": "label"
        }
    )
    df_final["type"] = "etf"  # Tous les produits scrapés = ETFs

    print("[*] Exemple des 5 premières lignes :")
    print(df_final.head())

    # Connexion à la base
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
                    "type": row["type"]
                }
            )

    print("🎉 Données mises à jour dans produits_invest")

if __name__ == "__main__":
    main()
