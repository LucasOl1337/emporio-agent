"""Load the store's CSV tables into typed, query-ready structures.

The CSVs are small (tens of rows), so everything lives in memory. The
cleaning done here is the part the agent must not get wrong: prices as
numbers, specs as dicts, promotions resolved to an effective price.
"""

from __future__ import annotations

import csv
import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

PREFIX = "desafio_tecnico_ai_eng - "


def normalize(text: str) -> str:
    """Lowercase and strip accents, so 'violão' matches 'violao'."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def _read(data_dir: Path, table: str) -> list[dict[str, str]]:
    path = data_dir / f"{PREFIX}{table}.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@dataclass
class Product:
    id: int
    name: str
    category_id: int
    category: str
    price: float
    description: str
    stock: int
    status: str  # active | discontinued | coming_soon
    specs: dict
    created_at: str
    promotion: dict | None = None  # active promotion, if any

    @property
    def effective_price(self) -> float:
        if self.promotion:
            return round(self.price * (1 - self.promotion["discount_percent"] / 100), 2)
        return self.price

    @property
    def availability(self) -> str:
        if self.status == "discontinued":
            return "discontinued"
        if self.status == "coming_soon":
            return "coming_soon"
        return "in_stock" if self.stock > 0 else "out_of_stock"

    def to_dict(self, detailed: bool = False) -> dict:
        out = {
            "product_id": self.id,
            "name": self.name,
            "category": self.category,
            "list_price_brl": self.price,
            "availability": self.availability,
            "stock_quantity": self.stock,
        }
        if self.promotion:
            out["active_promotion"] = {
                "description": self.promotion["description"],
                "discount_percent": self.promotion["discount_percent"],
                "promotional_price_brl": self.effective_price,
                "note": "PIX 5% discount does not stack with promotional prices",
            }
        else:
            out["pix_price_brl"] = round(self.price * 0.95, 2)
        if detailed:
            out["description"] = self.description
            out["specs"] = self.specs
        return out


@dataclass
class Store:
    products: dict[int, Product]
    categories: dict[int, dict]
    customers: dict[int, dict]
    orders: dict[int, dict]
    order_items: dict[int, list[dict]] = field(default_factory=dict)
    data_issues: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, data_dir: Path) -> "Store":
        categories = {int(r["category_id"]): r for r in _read(data_dir, "categories")}
        active_promos: dict[int, dict] = {}
        for r in _read(data_dir, "promotions"):
            if r["is_active"].strip() == "1":
                pid = int(r["product_id"])
                promo = {"description": r["description"], "discount_percent": float(r["discount_percent"])}
                # If two active promotions ever hit one product, keep the larger:
                # promotions are not cumulative (policy 6.2).
                if pid not in active_promos or promo["discount_percent"] > active_promos[pid]["discount_percent"]:
                    active_promos[pid] = promo

        products: dict[int, Product] = {}
        for r in _read(data_dir, "products"):
            pid = int(r["product_id"])
            cid = int(r["category_id"])
            try:
                specs = json.loads(r["specs"]) if r["specs"] else {}
            except json.JSONDecodeError:
                specs = {"raw": r["specs"]}
            products[pid] = Product(
                id=pid,
                name=r["name"].strip(),
                category_id=cid,
                category=categories.get(cid, {}).get("name", "?"),
                price=float(r["price_brl"]),
                description=r["description"].strip(),
                stock=int(r["stock_quantity"] or 0),
                status=r["status"].strip(),
                specs=specs,
                created_at=r["created_at"],
                promotion=active_promos.get(pid),
            )

        customers = {int(r["customer_id"]): r for r in _read(data_dir, "customers")}
        orders = {int(r["order_id"]): r for r in _read(data_dir, "orders")}
        items: dict[int, list[dict]] = {}
        for r in _read(data_dir, "order_items"):
            items.setdefault(int(r["order_id"]), []).append(
                {"product_id": int(r["product_id"]), "quantity": int(r["quantity"])}
            )

        store = cls(products, categories, customers, orders, items)
        store._check_consistency()
        return store

    def _check_consistency(self) -> None:
        """Record (not fix) mismatches, so the agent never quotes a recomputed total."""
        for oid, order in self.orders.items():
            total = sum(
                self.products[i["product_id"]].price * i["quantity"]
                for i in self.order_items.get(oid, [])
                if i["product_id"] in self.products
            )
            if abs(total - float(order["total_brl"])) > 0.01:
                self.data_issues.append(
                    f"order {oid}: total_brl {order['total_brl']} differs from current list prices sum {total:.2f}"
                )
