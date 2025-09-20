import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from justetf_scraping import overview

def main():
    print("[*] Récupération des ETFs depuis JustETF...")

    # Charger la config
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("❌ DATABASE_URL manquant dans .env")

    engine = create_engine(db_url)

    # Récupération JustETF
    df = overview.load_overview()
    print(f"✅ {len(df)} ETFs récupérés")

    df_reset = df.reset_index()
    colonnes_cibles = ["isin", "ticker", "name", "currency"]
    df_final = df_reset[colonnes_cibles]

    print("[*] Exemple des 5 premières lignes :")
    print(df_final.head())

    # UPSERT en base
    insert_sql = """
        INSERT INTO produits_invest (isin, ticker, name, currency)
        VALUES (:isin, :ticker, :name, :currency)
        ON CONFLICT (isin) DO UPDATE SET
            ticker = EXCLUDED.ticker,
            name = EXCLUDED.name,
            currency = EXCLUDED.currency;
    """

    with engine.begin() as conn:
        for _, row in df_final.iterrows():
            conn.execute(
                text(insert_sql),
                {
                    "isin": row["isin"],
                    "ticker": row["ticker"],
                    "name": row["name"],
                    "currency": row["currency"],
                },
            )

    print("✅ Données insérées/mises à jour dans produits_invest (UPSERT)")

if __name__ == "__main__":
    main()
