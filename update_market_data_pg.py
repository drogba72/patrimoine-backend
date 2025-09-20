import os
import psycopg2
import pandas as pd
from dotenv import load_dotenv
from justetf_scraping import overview

# Charger variables d'environnement (.env)
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

def main():
    print("[*] Récupération des ETFs depuis JustETF...")
    df = overview.load_overview()
    print(f"✅ {len(df)} ETFs récupérés")

    # On réduit le DataFrame aux colonnes utiles
    df_simple = df.reset_index()[["isin", "ticker", "name", "currency"]]

    print("[*] Exemple des 5 premiers :")
    print(df_simple.head())

    # Connexion à PostgreSQL
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # Création de la table si elle n’existe pas déjà
    cur.execute("""
    CREATE TABLE IF NOT EXISTS produits_invest (
        isin TEXT PRIMARY KEY,
        ticker TEXT,
        name TEXT,
        currency TEXT
    )
    """)

    # Insertion / mise à jour
    for _, row in df_simple.iterrows():
        cur.execute("""
            INSERT INTO produits_invest (isin, ticker, name, currency)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (isin) DO UPDATE
            SET ticker = EXCLUDED.ticker,
                name = EXCLUDED.name,
                currency = EXCLUDED.currency
        """, (row["isin"], row["ticker"], row["name"], row["currency"]))

    conn.commit()
    cur.close()
    conn.close()

    print("✅ Données enregistrées dans produits_invest")

if __name__ == "__main__":
    main()
