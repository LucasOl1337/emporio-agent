"""Tools the model can call. Each returns plain JSON-serializable data.

Rule of thumb from the policy manual (7.1): never state price, stock or
deadlines without querying the system. So every number the agent quotes
must come out of one of these functions.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .policies import PolicyIndex
from .store import Store, normalize

STORE_TZ = ZoneInfo("America/Campo_Grande")
ORDER_STATUS = {
    "pending": "awaiting payment confirmation",
    "confirmed": "payment confirmed, being prepared, not shipped yet",
    "shipped": "shipped, in transit",
    "delivered": "delivered",
    "cancelled": "cancelled",
}
# Opening hours from policy section 2. Holidays are not in the data; see README.
HOURS = {0: (9, 18), 1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18), 5: (9, 13), 6: None}


class Toolbox:
    def __init__(self, store: Store, policies: PolicyIndex, now: datetime | None = None):
        self.store = store
        self.policies = policies
        self._now = now

    def now(self) -> datetime:
        return self._now or datetime.now(STORE_TZ)

    # ---- catalog -------------------------------------------------------
    def search_products(
        self,
        query: str = "",
        category: str = "",
        min_price: float | None = None,
        max_price: float | None = None,
        available_only: bool = False,
        limit: int = 10,
    ) -> dict:
        words = [w for w in normalize(query).split() if len(w) > 1]
        cat = normalize(category)
        hits = []
        for p in self.store.products.values():
            if cat and cat not in normalize(p.category):
                continue
            price = p.effective_price
            if min_price is not None and price < min_price:
                continue
            if max_price is not None and price > max_price:
                continue
            if available_only and p.availability != "in_stock":
                continue
            hay = normalize(f"{p.name} {p.category} {p.description} {' '.join(map(str, p.specs.values()))}")
            score = sum(1 for w in words if w in hay)
            if words and score == 0:
                continue
            hits.append((score, p))
        hits.sort(key=lambda t: (-t[0], t[1].effective_price))
        return {
            "count": len(hits),
            "products": [p.to_dict() for _, p in hits[: max(1, min(limit, 25))]],
            "note": "Prices in BRL. Only products with availability=in_stock can be promised.",
        }

    def get_product(self, product_id: int | None = None, name: str = "") -> dict:
        if product_id is not None and product_id in self.store.products:
            return self.store.products[product_id].to_dict(detailed=True)
        words = [w for w in normalize(name).split() if len(w) > 1]
        if not words:
            return {"error": "Provide product_id or name."}
        scored = []
        for p in self.store.products.values():
            hay = normalize(p.name)
            score = sum(1 for w in words if w in hay)
            if score:
                scored.append((score, p))
        if not scored:
            return {"error": f"No product matching '{name}' in the catalog."}
        scored.sort(key=lambda t: -t[0])
        best = scored[0][0]
        top = [p for s, p in scored if s == best]
        if len(top) == 1:
            return top[0].to_dict(detailed=True)
        return {"ambiguous": True, "candidates": [p.to_dict() for p in top[:8]]}

    def list_categories(self) -> dict:
        counts: dict[int, int] = {}
        for p in self.store.products.values():
            if p.availability == "in_stock":
                counts[p.category_id] = counts.get(p.category_id, 0) + 1
        return {
            "categories": [
                {"name": c["name"], "description": c["description"], "products_in_stock": counts.get(cid, 0)}
                for cid, c in self.store.categories.items()
            ]
        }

    def list_active_promotions(self) -> dict:
        promos = [p.to_dict() for p in self.store.products.values() if p.promotion]
        return {"active_promotions": promos, "count": len(promos)}

    # ---- orders --------------------------------------------------------
    def get_order_status(self, order_id: int, customer_email_or_phone: str) -> dict:
        """Order lookup requires a second identifier (LGPD, policy section 9)."""
        order = self.store.orders.get(order_id)
        if not order:
            return {"error": f"Order {order_id} not found."}
        customer = self.store.customers.get(int(order["customer_id"]), {})
        given = customer_email_or_phone.strip().lower()
        digits = re.sub(r"\D", "", given)
        ok_email = given and given == customer.get("email", "").lower()
        ok_phone = len(digits) >= 8 and re.sub(r"\D", "", customer.get("phone", "")).endswith(digits[-8:])
        if not (ok_email or ok_phone):
            return {
                "error": "verification_failed",
                "message": "The email or phone does not match this order. Do not reveal any order details.",
            }
        items = [
            {
                "product": self.store.products[i["product_id"]].name if i["product_id"] in self.store.products else f"#{i['product_id']}",
                "quantity": i["quantity"],
            }
            for i in self.store.order_items.get(order_id, [])
        ]
        today = self.now().date()
        out = {
            "order_id": order_id,
            "customer_first_name": customer.get("name", "").split(" ")[0],
            "order_date": order["order_date"],
            "status": order["status"],
            "status_meaning": ORDER_STATUS.get(order["status"], order["status"]),
            "total_brl": float(order["total_brl"]),
            "payment_method": order["payment_method"],
            "items": items,
            "tracking_code": order["tracking_code"] or None,
            "estimated_delivery": order["estimated_delivery"] or None,
            "notes": order["notes"] or None,
            "today": today.isoformat(),
        }
        if order["estimated_delivery"]:
            d = datetime.fromisoformat(order["estimated_delivery"]).date()
            out["days_since_estimated_delivery"] = (today - d).days
            out["caveat"] = "The data has no actual receipt date; estimated_delivery is the best available proxy."
        out["days_since_order"] = (today - datetime.fromisoformat(order["order_date"]).date()).days
        return out

    def check_return_options(self, order_id: int, customer_email_or_phone: str) -> dict:
        """Apply the date windows of policies 4.1, 4.2, 4.3 and 8.1 to a verified order.

        Date arithmetic is done here, not by the model: the first recorded
        run showed the model miscounting a 30-day window.
        """
        order = self.get_order_status(order_id, customer_email_or_phone)
        if "error" in order:
            return order
        if order["status"] not in ("delivered", "shipped"):
            return {
                "order_id": order_id,
                "status": order["status"],
                "message": "Order not delivered yet; return windows have not started. Cancellation requests go to the team.",
            }
        today = self.now().date()
        bought = datetime.fromisoformat(order["order_date"]).date()
        received = datetime.fromisoformat(order["estimated_delivery"]).date() if order["estimated_delivery"] else None
        since_buy = (today - bought).days
        since_recv = (today - received).days if received else None
        def window(days: int, elapsed: int | None, basis: str) -> dict:
            if elapsed is None:
                return {"eligible": None, "reason": f"no {basis} date in the data"}
            return {"eligible": 0 <= elapsed <= days, "days_elapsed": elapsed, "limit_days": days, "counted_from": basis}
        return {
            "order_id": order_id,
            "today": today.isoformat(),
            "order_date": bought.isoformat(),
            "received_date_assumed": received.isoformat() if received else None,
            "regret_return_7d_policy_4_1": window(7, since_recv, "receipt (estimated_delivery used as proxy)"),
            "preference_exchange_7d_policy_4_3": window(7, since_recv, "receipt (estimated_delivery used as proxy)"),
            "defect_exchange_at_store_30d_policy_4_2": window(30, since_buy, "purchase date"),
            "legal_warranty_90d_policy_8_1": window(90, since_recv, "receipt (estimated_delivery used as proxy)"),
            "after_30_days_defect": "Policy 4.2: after 30 days the customer uses the manufacturer warranty; the store can intermediate on request.",
            "not_covered": "Misuse, drops, humidity, unauthorized modifications (4.2, 8.3). A defect must be assessed by the team before any promise.",
        }

    # ---- policies ------------------------------------------------------
    def get_policy(self, topic: str) -> dict:
        found = self.policies.get(topic)
        if not found:
            return {"error": f"No policy section for '{topic}'.", "table_of_contents": self.policies.table_of_contents()}
        return {"sections": [{"section": s.number, "title": s.title, "text": s.text.strip()} for s in found]}

    def store_status_now(self) -> dict:
        now = self.now()
        hours = HOURS[now.weekday()]
        is_open = bool(hours) and hours[0] <= now.hour < hours[1]
        return {
            "local_time": now.strftime("%Y-%m-%d %H:%M"),
            "weekday": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][now.weekday()],
            "open_now": is_open,
            "note": "Holidays are closed but there is no holiday calendar in the data; confirm on holidays.",
        }

    def installment_options(self, amount_brl: float) -> dict:
        """Apply policy 3.1. 'Até 3x' minimum is read as R$50; see README assumptions."""
        options = []
        for n in range(1, 13):
            minimum = 50 if n <= 3 else 80 if n <= 6 else 100
            value = amount_brl / n
            if n == 1 or value >= minimum:
                options.append({"installments": n, "value_brl": math.floor(value * 100) / 100})
        return {
            "amount_brl": amount_brl,
            "max_installments_without_interest": options[-1]["installments"],
            "options": options,
            "pix_price_brl": round(amount_brl * 0.95, 2),
            "rules": "No interest up to 12x. Min installment: R$50 up to 3x, R$80 for 4-6x, R$100 for 7-12x. "
            "PIX 5% off does not apply to promotional prices. Mixed payment only above R$2,000.",
        }


SCHEMAS = [
    {
        "name": "search_products",
        "description": "Search the instrument catalog by free text, category and price range. Use for 'what do you have', price ranges and alternatives.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, e.g. 'violão nylon', 'yamaha', 'iniciante'."},
                "category": {"type": "string", "description": "Category name, e.g. 'Violões', 'Guitarras', 'Ukuleles'."},
                "min_price": {"type": "number"},
                "max_price": {"type": "number", "description": "Compared against the promotional price when a promotion is active."},
                "available_only": {"type": "boolean", "description": "Only items in stock."},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_product",
        "description": "Full details (price, stock, promotion, specs) of one product by id or name.",
        "parameters": {
            "type": "object",
            "properties": {"product_id": {"type": "integer"}, "name": {"type": "string"}},
        },
    },
    {"name": "list_categories", "description": "Categories the store sells and how many items are in stock.", "parameters": {"type": "object", "properties": {}}},
    {"name": "list_active_promotions", "description": "Products with a currently active promotion.", "parameters": {"type": "object", "properties": {}}},
    {
        "name": "get_order_status",
        "description": "Status, items, tracking and dates of an order. Requires the order number AND the customer's email or phone for verification.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "integer"}, "customer_email_or_phone": {"type": "string"}},
            "required": ["order_id", "customer_email_or_phone"],
        },
    },
    {
        "name": "check_return_options",
        "description": "For a verified order, compute which return, exchange and warranty windows still apply today. Use whenever a customer wants to return, exchange or report a defect on a specific order.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "integer"}, "customer_email_or_phone": {"type": "string"}},
            "required": ["order_id", "customer_email_or_phone"],
        },
    },
    {
        "name": "get_policy",
        "description": "Read the store policy manual. Pass a section number ('4', '4.1') or a topic ('devolução', 'frete', 'garantia', 'pagamento', 'horário', 'endereço').",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]},
    },
    {"name": "store_status_now", "description": "Current local time in Campo Grande and whether the store is open now.", "parameters": {"type": "object", "properties": {}}},
    {
        "name": "installment_options",
        "description": "Valid credit card installment options and PIX price for an amount, per the payment policy.",
        "parameters": {"type": "object", "properties": {"amount_brl": {"type": "number"}}, "required": ["amount_brl"]},
    },
]
