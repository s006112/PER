from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from xmlrpc import client as xmlrpc_client

from dotenv import load_dotenv

load_dotenv()

# Basic logging setup shared with the demo verifier
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO), format="%(levelname)s: %(message)s")
log = logging.getLogger("app_odoo")


@dataclass
class OdooConfig:
    url: str
    db: str
    username: str
    password: str


@dataclass
class OdooClient:
    config: OdooConfig
    models: xmlrpc_client.ServerProxy
    uid: int

    def execute_kw(
        self,
        model: str,
        method: str,
        args: Optional[list[Any]] = None,
        kwargs: Optional[dict[str, Any]] = None,
    ) -> Any:
        args = args or []
        kwargs = kwargs or {}
        return self.models.execute_kw(
            self.config.db,
            self.uid,
            self.config.password,
            model,
            method,
            args,
            kwargs,
        )


_ODOO_CLIENT_CACHE: Optional[OdooClient] = None
_SALE_ORDER_LINE_HAS_COMMITMENT: Optional[bool] = None
_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m/%d/%y",
    "%d/%m/%y",
    "%m-%d-%Y",
    "%d-%m-%Y",
]


class DemoSettings:
    """
    Simple container for the demo sale order data. Customize via .env variables:
      ODOO_DEMO_COMPANY, ODOO_DEMO_CUSTOMER, ODOO_DEMO_SALESPERSON,
      ODOO_DEMO_ORDER_DATE, ODOO_DEMO_CUSTOMER_PO, ODOO_DEMO_PRODUCT,
      ODOO_DEMO_QUANTITY, ODOO_DEMO_DELIVERY_DATE.
    """

    def __init__(self) -> None:
        self.company = os.getenv("ODOO_DEMO_COMPANY", "Ampco Products Limited")
        self.customer = os.getenv("ODOO_DEMO_CUSTOMER", "Focal Point, LLC")
        self.salesperson = os.getenv("ODOO_DEMO_SALESPERSON", "Kenny Ng")
        self.order_date = os.getenv("ODOO_DEMO_ORDER_DATE", "2024-12-31")
        self.customer_po = os.getenv("ODOO_DEMO_CUSTOMER_PO", "DEMO-PO-001")
        self.product = os.getenv("ODOO_DEMO_PRODUCT", "A36773-04")
        self.quantity = os.getenv("ODOO_DEMO_QUANTITY", "12")
        self.delivery_date = os.getenv("ODOO_DEMO_DELIVERY_DATE", "2025-01-15")


def load_odoo_config() -> OdooConfig:
    url = os.getenv("ODOO_URL")
    db = os.getenv("ODOO_DB")
    username = os.getenv("ODOO_USERNAME")
    password = os.getenv("ODOO_PASSWORD")
    missing = [name for name, value in {
        "ODOO_URL": url,
        "ODOO_DB": db,
        "ODOO_USERNAME": username,
        "ODOO_PASSWORD": password,
    }.items() if not value]
    if missing:
        raise RuntimeError(f"Missing Odoo environment variables: {', '.join(missing)}")
    return OdooConfig(url=url, db=db, username=username, password=password)


def get_odoo_client() -> OdooClient:
    global _ODOO_CLIENT_CACHE
    if _ODOO_CLIENT_CACHE:
        return _ODOO_CLIENT_CACHE

    config = load_odoo_config()
    common = xmlrpc_client.ServerProxy(f"{config.url}/xmlrpc/2/common", allow_none=True)
    # Authenticate via xmlrpc/2/common.authenticate per Odoo external API (contentReference[oaicite:0])
    uid = common.authenticate(config.db, config.username, config.password, {})
    if not uid:
        raise RuntimeError("Odoo authentication failed; check credentials.")
    models = xmlrpc_client.ServerProxy(f"{config.url}/xmlrpc/2/object", allow_none=True)
    _ODOO_CLIENT_CACHE = OdooClient(config=config, models=models, uid=uid)
    return _ODOO_CLIENT_CACHE


def normalize_odoo_datetime(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} is empty.")
    normalized = cleaned.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        # Date/datetime fields should be sent as strings (contentReference[oaicite:1])
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        for fmt in _DATE_FORMATS:
            try:
                dt = datetime.strptime(cleaned, fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
    raise ValueError(f"Unable to parse {field_name} value '{value}' into an ISO datetime string.")


def parse_quantity(raw_value: str) -> float:
    sanitized = raw_value.replace(",", " ").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", sanitized)
    if not match:
        raise ValueError(f"Invalid quantity: '{raw_value}'")
    return float(match.group())


def find_company_id(client: OdooClient, name: str) -> int:
    result = client.execute_kw(
        "res.company",
        "search",
        [[["name", "=", name]]],
        {"limit": 1},
    )
    if not result:
        raise ValueError(f"Company '{name}' was not found in Odoo.")
    return result[0]


def find_or_create_customer_id(client: OdooClient, name: str) -> int:
    result = client.execute_kw(
        "res.partner",
        "search",
        [[["name", "=", name]]],
        {"limit": 1},
    )
    if result:
        return result[0]
    log.info("Customer '%s' not found; creating new partner.", name)
    customer_id = client.execute_kw("res.partner", "create", [[{"name": name}]])
    return customer_id


def find_salesperson_id(client: OdooClient, name: str) -> int:
    result = client.execute_kw(
        "res.users",
        "search",
        [[["name", "=", name]]],
        {"limit": 1},
    )
    if not result:
        raise ValueError(f"Salesperson '{name}' was not found in Odoo.")
    return result[0]


def find_product_id(client: OdooClient, product_label: str) -> int:
    candidates = [product_label.strip()]
    tokens = product_label.split()
    if tokens:
        first_token = tokens[0]
        if first_token and first_token not in candidates:
            candidates.append(first_token)
    if "(" in product_label and ")" in product_label:
        inner = product_label.split("(", 1)[1].split(")", 1)[0].strip()
        if inner and inner not in candidates:
            candidates.append(inner)

    for candidate in candidates:
        for field in ("default_code", "name"):
            domain = [[field, "=", candidate]]
            result = client.execute_kw(
                "product.product",
                "search",
                [domain],
                {"limit": 1},
            )
            if result:
                return result[0]
        # fall back to case-insensitive match if no exact match
        domain = [["name", "ilike", candidate]]
        result = client.execute_kw(
            "product.product",
            "search",
            [domain],
            {"limit": 1},
        )
        if result:
            return result[0]
    raise ValueError(f"Product '{product_label}' was not found in Odoo.")


def sale_order_line_supports_commitment_date(client: OdooClient) -> bool:
    global _SALE_ORDER_LINE_HAS_COMMITMENT
    if _SALE_ORDER_LINE_HAS_COMMITMENT is not None:
        return _SALE_ORDER_LINE_HAS_COMMITMENT
    try:
        fields = client.execute_kw(
            "sale.order.line",
            "fields_get",
            [["commitment_date"]],
        )
    except Exception as exc:
        log.debug("fields_get for commitment_date failed: %s", exc)
        _SALE_ORDER_LINE_HAS_COMMITMENT = False
        return False
    _SALE_ORDER_LINE_HAS_COMMITMENT = "commitment_date" in fields
    return _SALE_ORDER_LINE_HAS_COMMITMENT


def create_demo_sale_order(settings: DemoSettings) -> Tuple[int, dict[str, Any]]:
    client = get_odoo_client()
    company_id = find_company_id(client, settings.company)
    customer_id = find_or_create_customer_id(client, settings.customer)
    salesperson_id = find_salesperson_id(client, settings.salesperson)
    order_date_iso = normalize_odoo_datetime(settings.order_date, "Order Date")
    quantity = parse_quantity(settings.quantity)

    product_id = find_product_id(client, settings.product)
    order_line = {
        "product_id": product_id,
        "product_uom_qty": quantity,
    }
    if settings.delivery_date and sale_order_line_supports_commitment_date(client):
        order_line["commitment_date"] = normalize_odoo_datetime(
            settings.delivery_date,
            "Delivery Date",
        )
    elif settings.delivery_date:
        log.info("Skipping commitment_date because the field is not available on sale.order.line.")

    vals = {
        "partner_id": customer_id,
        "company_id": company_id,
        "user_id": salesperson_id,
        "date_order": order_date_iso,
        "client_order_ref": settings.customer_po,
        # Build one2many commands with (0, 0, values) per XML-RPC protocol (contentReference[oaicite:3])
        "order_line": [(0, 0, order_line)],
    }

    # Call create() via execute_kw to obtain the new order ID (contentReference[oaicite:6])
    order_id = client.execute_kw("sale.order", "create", [vals])
    log.info("Created demo sale.order %s (PO: %s)", order_id, settings.customer_po)

    order_data = client.execute_kw(
        "sale.order",
        "read",
        [[order_id], ["name", "order_line"]],
    )
    log.info("Order %s readback: %s", order_id, order_data)
    return order_id, order_data[0] if order_data else {}


def main() -> None:
    try:
        settings = DemoSettings()
        order_id, order_data = create_demo_sale_order(settings)
        print(f"Created sale.order ID: {order_id}")
        print(f"Order data: {order_data}")
    except Exception as exc:
        log.exception("Demo sale order creation failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
