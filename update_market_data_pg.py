import requests
from bs4 import BeautifulSoup

url = "https://www.justetf.com/fr/search.html?10-1.2-container-tabsContentContainer-tabsContentRepeater-1-container-content-etfsTablePanel&ajaxsortField=ter&ajaxsortOrder=asc"
headers = {"User-Agent": "Mozilla/5.0"}

r = requests.get(url, headers=headers, timeout=30)
r.raise_for_status()

soup = BeautifulSoup(r.text, "html.parser")

# Trouver le tableau
table = soup.find("table")
rows = table.find("tbody").find_all("tr")

for row in rows[:5]:
    cols = row.find_all("td")
    if not cols:
        continue
    name = cols[0].get_text(strip=True)
    isin = None

    # Cherche l’ISIN dans les liens de la ligne
    link = row.find("a", href=True)
    if link and "isin=" in link["href"]:
        isin = link["href"].split("isin=")[-1]

    print(f"Nom: {name} | ISIN: {isin}")
