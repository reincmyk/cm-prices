#!/usr/bin/env python3
"""
Cardmarket Pokemon prijs-sync
Downloadt de officiele, dagelijks gepubliceerde Cardmarket prijsgids en
productcatalogus, en schrijft per expansie een compact JSON-bestand naar docs/prices/.

Bronnen (officieel gepubliceerd door Cardmarket voor alle gebruikers):
  - price_guide_6.json      (prijzen per idProduct, dagelijks ververst)
  - products_singles_6.json (catalogus: idProduct -> naam + expansie)

Gebruik:  python3 sync_prices.py
Vereist:  alleen Python 3 standaardbibliotheek.
"""

import json
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PRICE_GUIDE_URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json"

# Cardmarket heeft de catalogus-bestandsnaam weleens gewijzigd; probeer kandidaten.
CATALOG_URL_CANDIDATES = [
    "https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_6.json",
    "https://downloads.s3.cardmarket.com/productCatalog/products/products_singles_6.json",
    "https://downloads.s3.cardmarket.com/productCatalog/productList/products_6.json",
]

EXPANSION_URL_CANDIDATES = [
    "https://downloads.s3.cardmarket.com/productCatalog/expansionList/expansions_6.json",
    "https://downloads.s3.cardmarket.com/productCatalog/expansions/expansions_6.json",
    "https://downloads.s3.cardmarket.com/productCatalog/expansionCatalog/expansions_6.json",
]

OUT_DIR = Path(__file__).parent / "docs" / "prices"
DEBUG_FILE = Path(__file__).parent / "docs" / "debug.json"


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (persoonlijke prijs-sync)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "onbekend"


def main() -> int:
    print("1/4 Prijsgids downloaden...")
    guide = fetch_json(PRICE_GUIDE_URL)
    prices = {p["idProduct"]: p for p in guide.get("priceGuides", [])}
    created = guide.get("createdAt", datetime.now(timezone.utc).isoformat())
    print(f"    {len(prices)} producten, aangemaakt {created}")

    print("2/4 Productcatalogus downloaden...")
    catalog = None
    for url in CATALOG_URL_CANDIDATES:
        try:
            catalog = fetch_json(url)
            print(f"    Gevonden: {url}")
            break
        except Exception as e:
            print(f"    Niet via {url} ({e})")
    if catalog is None:
        print("FOUT: geen catalogus-URL werkte. Check op cardmarket.com/en/Pokemon/Data/Product-List")
        return 1

    products = catalog.get("products", catalog if isinstance(catalog, list) else [])
    print(f"    {len(products)} catalogus-items")

    # Expansielijst proberen (idExpansion -> naam)
    exp_map = {}
    exp_debug = []
    for url in EXPANSION_URL_CANDIDATES:
        try:
            exp_data = fetch_json(url)
            items = exp_data.get("expansions", exp_data if isinstance(exp_data, list) else [])
            for e in items:
                eid = e.get("idExpansion") or e.get("id")
                ename = e.get("enName") or e.get("name")
                if eid and ename:
                    exp_map[eid] = ename
            exp_debug.append({"url": url, "ok": True, "count": len(exp_map)})
            print(f"    Expansielijst gevonden: {url} ({len(exp_map)} namen)")
            break
        except Exception as e:
            exp_debug.append({"url": url, "ok": False, "error": str(e)[:120]})

    # Zelfdiagnose: ruwe structuur wegschrijven zodat problemen op afstand leesbaar zijn
    DEBUG_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEBUG_FILE.write_text(json.dumps({
        "product_sample": products[:2],
        "product_keys": sorted(products[0].keys()) if products else [],
        "expansion_candidates": exp_debug,
        "expansion_names_found": len(exp_map),
    }, indent=1, default=str), encoding="utf-8")

    # Expansienamen: soms als apart veld per product ("expansionName" / "expansion"),
    # soms alleen idExpansion. Vang beide af.
    print("3/4 Koppelen per expansie...")
    per_set = defaultdict(list)
    missing_price = 0
    for prod in products:
        pid = prod.get("idProduct")
        pg = prices.get(pid)
        if not pg:
            missing_price += 1
            continue
        website = prod.get("website") or ""
        m = re.search(r"/Singles/([^/]+)/", website)
        exp_name = (
            prod.get("expansionName")
            or prod.get("expansion")
            or exp_map.get(prod.get("idExpansion"))
            or (m.group(1).replace("-", " ") if m else None)
            or f"expansion-{prod.get('idExpansion', 'onbekend')}"
        )
        entry = {
            "id": pid,
            "name": prod.get("name"),
            "low": pg.get("low"),
            "trend": pg.get("trend"),
            "avg1": pg.get("avg1"),
            "avg7": pg.get("avg7"),
            "avg30": pg.get("avg30"),
        }
        if website:
            entry["url"] = "https://www.cardmarket.com" + website
        # Reverse holo-varianten meenemen als aanwezig
        if pg.get("trend-holo") is not None:
            entry["trend_holo"] = pg.get("trend-holo")
            entry["low_holo"] = pg.get("low-holo")
        per_set[exp_name].append(entry)

    print(f"    {len(per_set)} expansies, {missing_price} items zonder prijsdata")

    print("4/4 Schrijven naar docs/prices/ ...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = {"updated": created, "expansions": {}}
    for exp_name, cards in per_set.items():
        slug = slugify(exp_name)
        out = {"expansion": exp_name, "updated": created, "cards": cards}
        (OUT_DIR / f"{slug}.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        index["expansions"][slug] = {"name": exp_name, "count": len(cards)}
    (OUT_DIR / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"    Klaar: {len(per_set)} bestanden + index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
