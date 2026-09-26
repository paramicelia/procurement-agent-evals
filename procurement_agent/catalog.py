"""A small reference catalog + fixed FX table, used by compare_to_catalog.

Both are synthetic and fixed on purpose: reproducible eval runs must not
depend on a live FX feed or a live product database.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

# Fixed synthetic FX rates (NOT live market rates), so a run today and a run
# next year produce the same numbers. 1 unit of currency = FX_TO_EUR[ccy] EUR.
FX_TO_EUR: dict[str, float] = {
    "EUR": 1.0,
    "USD": 0.92,
    "GBP": 1.17,
    "JPY": 0.0061,
}


@dataclass(frozen=True)
class CatalogItem:
    sku: str
    name: str
    unit_price_eur: float


CATALOG: list[CatalogItem] = [
    CatalogItem("BOLT-M6-HEX", "M6 Hex Bolt (zinc)", 0.11),
    CatalogItem("SCR-M4-PAN", "M4 Pan Head Screw", 0.05),
    CatalogItem("PANEL-AL-8MM", "8mm Aluminum Panel", 14.50),
    CatalogItem("CABLE-USBC-1M", "USB-C Cable 1m", 2.30),
    CatalogItem("RES-10K-0805", "10k Ohm Resistor 0805", 0.02),
    CatalogItem("CAP-100UF", "100uF Electrolytic Capacitor", 0.09),
    CatalogItem("BRKT-STEEL-L", "Steel L-Bracket", 1.75),
    CatalogItem("GASKET-RUBBER-40", "Rubber Gasket 40mm", 0.60),
    CatalogItem("PCB-2L-100x80", "2-Layer PCB 100x80mm", 3.40),
    CatalogItem("CONN-JST-4P", "JST 4-Pin Connector", 0.18),
    CatalogItem("FAN-40MM-12V", "40mm 12V Cooling Fan", 2.90),
    CatalogItem("HEATSINK-AL-S", "Small Aluminum Heatsink", 0.75),
    CatalogItem("WIRE-22AWG-100M", "22AWG Hookup Wire, 100m spool", 8.20),
    CatalogItem("BOX-ABS-100x60", "ABS Enclosure 100x60x25mm", 1.95),
    CatalogItem("LABEL-ROLL-1000", "Barcode Label Roll (1000 labels)", 6.10),
]

_CATALOG_BY_SKU = {c.sku: c for c in CATALOG}


def to_eur(amount: float | None, currency: str | None) -> float | None:
    if amount is None or not currency:
        return None
    rate = FX_TO_EUR.get(currency.strip().upper())
    if rate is None:
        return None
    return amount * rate


def find_catalog_match(description: str, threshold: float = 0.55) -> CatalogItem | None:
    """Fuzzy-match a free-text line-item description against the catalog.

    Deterministic, dependency-free string matching (difflib), not an
    embedding lookup: good enough for a ~15-SKU catalog and easy to audit.
    """
    if not description:
        return None
    norm = description.strip().lower()
    best: CatalogItem | None = None
    best_ratio = 0.0
    for item in CATALOG:
        for candidate in (item.name.lower(), item.sku.lower()):
            ratio = difflib.SequenceMatcher(None, norm, candidate).ratio()
            if candidate in norm or norm in candidate:
                ratio = max(ratio, 0.75)
            if ratio > best_ratio:
                best_ratio = ratio
                best = item
    return best if best_ratio >= threshold else None


def get_by_sku(sku: str) -> CatalogItem | None:
    return _CATALOG_BY_SKU.get(sku)
