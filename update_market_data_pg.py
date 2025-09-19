import requests
from bs4 import BeautifulSoup

url = "https://www.justetf.com/fr/etf-profile.html?13-1.0-&isin=IE00B5BMR087&_wicket=1"
resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
print("[*] Status:", resp.status_code)

html = resp.text
soup = BeautifulSoup(html, "html.parser")

# Exemple : récupérer le titre de l’ETF
title = soup.find("h1")
print("Nom ETF:", title.text.strip() if title else "non trouvé")
