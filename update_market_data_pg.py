# scrap_justetf_detail.py
import requests
from bs4 import BeautifulSoup
import time
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def fetch(url, session=None, retries=2, delay=1.0):
    s = session or requests.Session()
    for attempt in range(1, retries+1):
        try:
            r = s.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"[fetch] tentative {attempt} échouée: {e}")
            if attempt < retries:
                time.sleep(delay)
    return None

def extract_from_meta(soup):
    out = {}
    # og:title
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        out["og_title"] = og["content"].strip()
    # try json-ld
    ld = soup.find("script", type="application/ld+json")
    if ld:
        try:
            j = json.loads(ld.string)
            out["json_ld"] = j
        except Exception:
            pass
    return out

def find_label_row(soup, label):
    # cherche <th>Label</th><td>value</td> or same but with <dt>/<dd>
    # case-insensitive
    label_lower = label.lower()
    # table rows
    for tr in soup.select("table tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th","td","td"])]
        if len(cells) >= 2 and label_lower in cells[0].lower():
            return cells[1].strip()
    # dt/dd
    for dt in soup.find_all("dt"):
        if label_lower in dt.get_text(" ", strip=True).lower():
            dd = dt.find_next_sibling("dd")
            if dd:
                return dd.get_text(" ", strip=True)
    # fallback: search for label text anywhere
    el = soup.find(string=lambda t: t and label_lower in t.lower())
    if el:
        # try parent sibling
        p = el.parent
        if p and p.next_sibling:
            return getattr(p.next_sibling, "get_text", lambda s: str(p.next_sibling))().strip()
    return None

def parse_profile(html):
    soup = BeautifulSoup(html, "html.parser")
    out = {}

    out.update(extract_from_meta(soup))

    # common fields we want to try to extract
    fields = {
        "ISIN": ["ISIN", "Isin"],
        "Ticker": ["Ticker", "Symbol", "WKN"],   # just in case
        "Nom": ["Nom", "Name", "ETF", "Bezeichnung"],
        "Fournisseur": ["Provider", "Fondsanbieter", "Fournisseur"],
        "TER": ["TER", "Gesamtkostenquote", "Total Expense Ratio"],
        "Replication": ["Replication", "Replikation"],
        "Domicile": ["Domicile", "Listing", "Market", "Börse"]
    }

    # try extraction by label searching
    for key, labels in fields.items():
        for lab in labels:
            val = find_label_row(soup, lab)
            if val:
                out[key] = val
                break

    # some pages display main title/name in h1
    if "Nom" not in out:
        h1 = soup.find("h1")
        if h1:
            out["Nom"] = h1.get_text(" ", strip=True)

    # try to extract isin from url query params if present
    # (sometimes page URL contains ?isin=IE00...)
    # we do a quick search for pattern 'ISIN' in page text as fallback
    if "ISIN" not in out:
        text = soup.get_text(" ", strip=True)
        import re
        m = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b", text)
        if m:
            out["ISIN_guess"] = m.group(1)

    return out

def main():
    # remplace par l'URL que tu as dans le Devtools
    url = "https://www.justetf.com/fr/etf-profile.html?13-1.0-&isin=IE00B5BMR087&_wicked=1"
    print("[*] fetching", url)
    r = fetch(url)
    if not r:
        print("❌ fetch failed")
        return

    print("[*] status:", r.status_code)
    parsed = parse_profile(r.text)
    print("--- Résultats parsés ---")
    for k, v in parsed.items():
        print(f"{k}: {v}")
    print("------------------------")

if __name__ == "__main__":
    main()
