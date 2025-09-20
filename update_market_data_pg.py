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

    # 🔹 Normalisation pour produits_invest
    df_invest = df.reset_index()[["isin", "ticker", "name", "currency"]].rename(
        columns={
            "ticker": "ticker_yahoo",
            "name": "label",
        }
    )
    df_invest["type"] = "etf"

    # 🔹 Normalisation pour produits_meta
    df_meta = df.reset_index()[[
        "isin",
        "inception_date",
        "domicile_country",
        "replication",
        "ter",
        "size",
        "number_of_holdings",
        "is_sustainable",
        "hedged",
        "securities_lending",
        "dividends",
        "last_dividends"
    ]].rename(columns={
        "domicile_country": "domicile",
        "dividends": "distribution_policy",
        "last_dividends": "last_dividend_date"
    })

    # 🔥 Convertir NaN/NA en None (SQL-compatible)
    df_invest = df_invest.where(pd.notnull(df_invest), None)
    df_meta = df_meta.where(pd.notnull(df_meta), None)

    print("[*] Exemple produits_invest :")
    print(df_invest.head())
    print("[*] Exemple produits_meta :")
    print(df_meta.head())

    # Connexion SQLAlchemy
    engine = create_engine(DB_URL)

    with engine.begin() as conn:
        for _, row in df_invest.iterrows():
            # 1️⃣ Insérer/mettre à jour produits_invest
            result = conn.execute(
                text("""
                    INSERT INTO produits_invest (isin, ticker_yahoo, label, currency, type)
                    VALUES (:isin, :ticker_yahoo, :label, :currency, :type)
                    ON CONFLICT (isin) DO UPDATE SET
                        ticker_yahoo = EXCLUDED.ticker_yahoo,
                        label = EXCLUDED.label,
                        currency = EXCLUDED.currency,
                        type = EXCLUDED.type
                    RETURNING id;
                """),
                {
                    "isin": row["isin"],
                    "ticker_yahoo": row["ticker_yahoo"],
                    "label": row["label"],
                    "currency": row["currency"],
                    "type": row["type"],
                }
            )
            produit_id = result.scalar()

            # 2️⃣ Trouver la ligne correspondante dans df_meta
            meta_row = df_meta[df_meta["isin"] == row["isin"]]
            if meta_row.empty:
                continue
            meta_row = meta_row.iloc[0]

            # 3️⃣ Insérer/mettre à jour produits_meta
            conn.execute(
                text("""
                    INSERT INTO produits_meta (
                        produit_id, inception_date, domicile, replication,
                        ter, size, number_of_holdings, is_sustainable,
                        hedged, securities_lending, distribution_policy, last_dividend_date
                    ) VALUES (
                        :produit_id, :inception_date, :domicile, :replication,
                        :ter, :size, :number_of_holdings, :is_sustainable,
                        :hedged, :securities_lending, :distribution_policy, :last_dividend_date
                    )
                    ON CONFLICT (produit_id) DO UPDATE SET
                        inception_date = EXCLUDED.inception_date,
                        domicile = EXCLUDED.domicile,
                        replication = EXCLUDED.replication,
                        ter = EXCLUDED.ter,
                        size = EXCLUDED.size,
                        number_of_holdings = EXCLUDED.number_of_holdings,
                        is_sustainable = EXCLUDED.is_sustainable,
                        hedged = EXCLUDED.hedged,
                        securities_lending = EXCLUDED.securities_lending,
                        distribution_policy = EXCLUDED.distribution_policy,
                        last_dividend_date = EXCLUDED.last_dividend_date;
                """),
                {
                    "produit_id": produit_id,
                    "inception_date": meta_row["inception_date"],
                    "domicile": meta_row["domicile"],
                    "replication": meta_row["replication"],
                    "ter": meta_row["ter"],
                    "size": meta_row["size"],
                    "number_of_holdings": meta_row["number_of_holdings"],
                    "is_sustainable": meta_row["is_sustainable"],
                    "hedged": meta_row["hedged"],
                    "securities_lending": meta_row["securities_lending"],
                    "distribution_policy": meta_row["distribution_policy"],
                    "last_dividend_date": meta_row["last_dividend_date"],
                }
            )

    print("🎉 Import terminé avec succès !")

if __name__ == "__main__":
    main()
