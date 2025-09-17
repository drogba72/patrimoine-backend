-- ========================
-- Utilisateurs
-- ========================
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    fullname VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);

-- ========================
-- Actifs génériques
-- ========================
CREATE TABLE assets (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL, -- livret / immo / action / autre
    label VARCHAR(255) NOT NULL, -- nom choisi par l’utilisateur
    current_value NUMERIC(14,2), -- valeur nette ou estimée
    created_at TIMESTAMP DEFAULT NOW()
);

-- ========================
-- Livrets
-- ========================
CREATE TABLE assets_livret (
    id SERIAL PRIMARY KEY,
    asset_id INT REFERENCES assets(id) ON DELETE CASCADE,
    bank VARCHAR(255),
    balance NUMERIC(14,2) NOT NULL,
    plafond NUMERIC(14,2),
    recurring_amount NUMERIC(14,2),
    recurring_frequency VARCHAR(50), -- mensuel / trimestriel
    recurring_day INT CHECK (recurring_day BETWEEN 1 AND 31)
);

-- ========================
-- Immobilier
-- ========================
CREATE TABLE assets_immo (
    id SERIAL PRIMARY KEY,
    asset_id INT REFERENCES assets(id) ON DELETE CASCADE,
    property_type VARCHAR(50), -- résidence principale / locatif / secondaire
    address TEXT,
    purchase_price NUMERIC(14,2) NOT NULL,
    notary_fees NUMERIC(14,2),
    other_fees NUMERIC(14,2),
    down_payment NUMERIC(14,2),
    loan_amount NUMERIC(14,2),
    loan_rate NUMERIC(5,3), -- ex: 2.450
    loan_duration_months INT,
    insurance_monthly NUMERIC(14,2),
    loan_start_date DATE,
    monthly_payment NUMERIC(14,2),
    rental_income NUMERIC(14,2) -- si locatif
);

-- ========================
-- Produits financiers (PEA, CTO, AV, PER…)
-- ========================
CREATE TABLE assets_portfolio (
    id SERIAL PRIMARY KEY,
    asset_id INT REFERENCES assets(id) ON DELETE CASCADE,
    product_type VARCHAR(50), -- PEA / CTO / AV / PER
    broker VARCHAR(255), -- Boursorama, Degiro, etc.
    initial_investment NUMERIC(14,2),
    recurring_amount NUMERIC(14,2),
    recurring_frequency VARCHAR(50),
    recurring_day INT CHECK (recurring_day BETWEEN 1 AND 31)
);

-- Lignes d’investissement dans un portefeuille
CREATE TABLE portfolio_lines (
    id SERIAL PRIMARY KEY,
    portfolio_id INT REFERENCES assets_portfolio(id) ON DELETE CASCADE,
    isin VARCHAR(50),
    label VARCHAR(255),
    units NUMERIC(14,4), -- nombre de parts
    amount_invested NUMERIC(14,2),
    purchase_date DATE
);

-- ========================
-- Autres actifs (crypto, métaux, etc.)
-- ========================
CREATE TABLE assets_other (
    id SERIAL PRIMARY KEY,
    asset_id INT REFERENCES assets(id) ON DELETE CASCADE,
    category VARCHAR(50), -- crypto / or / œuvres / autres
    description TEXT,
    estimated_value NUMERIC(14,2),
    platform VARCHAR(255), -- ex: Binance si crypto
    wallet_address VARCHAR(255)
);

-- ========================
-- Revenus & charges (questionnaire utilisateur)
-- ========================
CREATE TABLE user_income (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    label VARCHAR(255), -- salaire, loyer, dividendes...
    amount NUMERIC(14,2) NOT NULL,
    frequency VARCHAR(50), -- mensuel, annuel, etc.
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE user_expenses (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    label VARCHAR(255), -- crédit conso, pension, etc.
    amount NUMERIC(14,2) NOT NULL,
    frequency VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);
