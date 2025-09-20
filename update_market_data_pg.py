import os
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv
from justetf_scraping import overview

def main():
    print("[*] Récupération des ETFs depuis JustETF...")

    # 1. Charger la config
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("❌ DATABASE_URL manquant dans .env")

    # 2. Connexion à la base via SQLAlchemy
    engine = create_engine(db_url)

    # 3. Récupération des données JustETF
    df = overview.load_overview()
    print(f"✅ {len(df)} ETFs récupérés")

    # 4. Normalisation (on réinitialise l’index pour inclure l’ISIN)
    df_reset = df.reset_index()

    # Garder un sous-ensemble de colonnes pour la table produits_invest
    colonnes_cibles = ["isin", "ticker", "name", "currency"]
    df_final = df_reset[colonnes_cibles]

    print("[*] Exemple des 5 premières lignes :")
    print(df_final.head())

    # 5. Sauvegarde en BDD
    df_final.to_sql("produits_invest", engine, if_exists="replace", index=False)
    print("✅ Données insérées dans la table produits_invest")

if __name__ == "__main__":
    main()
