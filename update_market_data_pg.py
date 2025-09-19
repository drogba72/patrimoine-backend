import requests
from bs4 import BeautifulSoup

def test_scrap_justetf(limit=5):
    url = "https://www.justetf.com/fr/search.html?search=ETFS&sortOrder=asc&sortField=ter"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print("❌ Erreur scrap JustETF:", e)
        return

    rows = soup.select("table.search-result-table tbody tr")
    if not rows:
        print("⚠️ Aucun résultat trouvé (probablement un chargement JS ?)")
        return

    print(f"📊 {len(rows)} lignes trouvées dans le tableau")
    for i, row in enumerate(rows[:limit]):
        cols = row.find_all("td")
        if len(cols) < 2:
            continue
        name = cols[0].get_text(strip=True)
        isin = cols[1].get_text(strip=True)
        url_profile = f"https://www.justetf.com/fr/etf-profile.html?isin={isin}"

        print(f"{i+1}. {name} | ISIN={isin} | Profil={url_profile}")

if __name__ == "__main__":
    test_scrap_justetf()
