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

NONSINGLES_URL_CANDIDATES = [
    "https://downloads.s3.cardmarket.com/productCatalog/productList/products_nonsingles_6.json",
    "https://downloads.s3.cardmarket.com/productCatalog/products/products_nonsingles_6.json",
]

# Producttype-woorden die we van setnamen afknippen bij één enkel product
TYPE_WORDS = {
    "booster", "boosters", "display", "box", "bundle", "elite", "trainer",
    "etb", "blister", "tin", "collection", "pack", "packs", "case", "sleeved",
    "premium", "checklane", "build", "battle", "stadium", "mini", "half",
}


def common_token_prefix(names):
    """Langste gemeenschappelijke woord-prefix over meerdere productnamen."""
    token_lists = [n.split() for n in names if n]
    if not token_lists:
        return None
    prefix = []
    for tokens in zip(*token_lists):
        if all(t == tokens[0] for t in tokens):
            prefix.append(tokens[0])
        else:
            break
    # Bij één product: knip producttype-woorden van het einde
    if len(token_lists) == 1:
        prefix = token_lists[0]
        while prefix and prefix[-1].lower().strip(":&()") in TYPE_WORDS:
            prefix = prefix[:-1]
    name = " ".join(prefix).rstrip(" :-–")
    return name if len(name) >= 3 else None


def derive_expansion_names(nonsingles):
    by_exp = defaultdict(list)
    for p in nonsingles:
        if p.get("idExpansion") and p.get("name"):
            by_exp[p["idExpansion"]].append(p["name"])
    return {eid: nm for eid, names in by_exp.items() if (nm := common_token_prefix(names))}

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

    # Setnamen afleiden uit non-singles (boosters/ETB's dragen de setnaam)
    exp_map = {}
    exp_debug = []
    for url in NONSINGLES_URL_CANDIDATES:
        try:
            ns_data = fetch_json(url)
            ns_products = ns_data.get("products", ns_data if isinstance(ns_data, list) else [])
            exp_map = derive_expansion_names(ns_products)
            exp_debug.append({"url": url, "ok": True, "names_derived": len(exp_map)})
            print(f"    Non-singles gevonden: {len(ns_products)} producten → {len(exp_map)} setnamen afgeleid")
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
