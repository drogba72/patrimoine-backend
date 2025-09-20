import os
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from justetf_scraping import overview

# Charger les variables d'environnement
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")


def to_python(val):
    """
    Convertit proprement les types NumPy/pandas en types Python natifs compatibles PostgreSQL.
    """
    if pd.isna(val):   # gère NaN et NaT
        return None
    if isinstance(val, (pd.Timestamp, pd.NaT.__class__)):
        return val.to_pydatetime()
    if isinstance(val, (pd.Series, pd.DataFrame)):
        return None
    if hasattr(val, "item"):  # numpy scalars ont .item()
        return val.item()
    return val


def main():
    print("[*] Récupération des ETFs depuis JustETF...")
    df = overview.load_overview()
    print(f"✅ {len(df)} ETFs récupérés")

    # ---- produits_invest ----
    df_invest = df.reset_index()[["isin", "ticker", "name", "currency"]].rename(
        columns={
            "ticker": "ticker_yahoo",
            "name": "label",
        }
    )
    df_invest["type"] = "etf"
    df_invest = df_invest.where(pd.notnull(df_invest), None)

    print("[*] Exemple produits_invest :")
    print(df_invest.head(), "\n")

    # ---- produits_meta ----
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
        "dividends",  # sera mappé sur distribution_policy
        "last_dividends"  # sera mappé sur last_dividend_date
    ]].rename(
        columns={
            "domicile_country": "domicile",
            "dividends": "distribution_policy",
            "last_dividends": "last_dividend_date",
        }
    )
    df_meta = df_meta.where(pd.notnull(df_meta), None)

    print("[*] Exemple produits_meta :")
    print(df_meta.head(), "\n")

    # Connexion SQLAlchemy
    engine = create_engine(DB_URL)

    with engine.begin() as conn:
        for _, row in df_invest.iterrows():
            # 1) Insert/Update produits_invest
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
                    "isin": to_python(row["isin"]),
                    "ticker_yahoo": to_python(row["ticker_yahoo"]),
                    "label": to_python(row["label"]),
                    "currency": to_python(row["currency"]),
                    "type": to_python(row["type"]),
                }
            )
            produit_id = result.scalar()

            # 2) Récupérer la ligne meta correspondante
            meta_row = df_meta[df_meta["isin"] == row["isin"]]
            if not meta_row.empty:
                meta_row = meta_row.iloc[0]

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
                        "inception_date": to_python(meta_row["inception_date"]),
                        "domicile": to_python(meta_row["domicile"]),
                        "replication": to_python(meta_row["replication"]),
                        "ter": to_python(meta_row["ter"]),
                        "size": to_python(meta_row["size"]),
                        "number_of_holdings": to_python(meta_row["number_of_holdings"]),
                        "is_sustainable": to_python(meta_row["is_sustainable"]),
                        "hedged": to_python(meta_row["hedged"]),
                        "securities_lending": to_python(meta_row["securities_lending"]),
                        "distribution_policy": to_python(meta_row["distribution_policy"]),
                        "last_dividend_date": to_python(meta_row["last_dividend_date"]),
                    }
                )


if __name__ == "__main__":
    main()
