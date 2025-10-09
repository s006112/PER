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
class DemoSettings:
    """
    Simple container for the demo sale order data using fixed demo values.
    """

    def __init__(self) -> None:
        self.company = "AMPCO LIGHTING LIMITED"
        self.customer = "Acuity Brands Lighting Inc"
        self.salesperson = "Kenny Ng"
        self.x_studio_customer_po_number = "4227475"
        self.order_lines = [
            {
                "product": "287LC5",
                "quantity": "204",
                "x_studio_delivery_date": "2025-12-18",
            },
            {
                "product": "287LC7",
                "quantity": "300",
                "x_studio_delivery_date": "2025-12-18",
            },
            {
                "product": "287LCC",
                "quantity": "504",
                "x_studio_delivery_date": "2025-11-25",
            },
        ]


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
    except ValueError:
        try:
            dt = datetime.strptime(cleaned, "%m/%d/%Y")
        except ValueError as exc:
            raise ValueError(
                f"Unable to parse {field_name} value '{value}' into an ISO datetime string.",
            ) from exc
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    # Date/datetime fields should be sent as strings (contentReference[oaicite:1])
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_quantity(raw_value: str) -> float:
    sanitized = raw_value.replace(",", " ").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", sanitized)
    if not match:
        raise ValueError(f"Invalid quantity: '{raw_value}'")
    return float(match.group())


_NORMALIZE_PATTERN = re.compile(r"[^0-9a-z]+")


def _normalize_value(raw_value: str) -> str:
    return _NORMALIZE_PATTERN.sub("", raw_value.lower())


def _select_candidate(candidates: list[tuple[int, str, str]]) -> int:
    return min(candidates, key=lambda item: (len(item[1]), item[1], item[0]))[0]


def _fetch_candidates_for_field(
    client: OdooClient,
    model: str,
    field: str,
    input_value: str,
    limit: int,
) -> list[tuple[int, str, str]]:
    stripped_input = input_value.strip()
    candidates: list[tuple[int, str, str]] = []
    seen_ids: set[int] = set()

    def add_records(records: list[dict[str, Any]]) -> bool:
        for record in records:
            record_id = int(record["id"])
            if record_id in seen_ids:
                continue
            raw_value = record.get(field)
            if not raw_value:
                continue
            normalized_value = _normalize_value(str(raw_value))
            if not normalized_value:
                continue
            candidates.append((record_id, normalized_value, str(raw_value)))
            seen_ids.add(record_id)
            if len(candidates) >= limit:
                return True
        return False

    if stripped_input:
        exact_records = client.execute_kw(
            model,
            "search_read",
            [[[field, "=", stripped_input]]],
            {"fields": [field], "limit": limit},
        )
        if add_records(exact_records):
            return candidates

        for prefix_length in range(len(stripped_input), 0, -1):
            prefix = stripped_input[:prefix_length]
            prefix_records = client.execute_kw(
                model,
                "search_read",
                [[[field, "ilike", f"{prefix}%"]]],
                {"fields": [field], "limit": limit},
            )
            if add_records(prefix_records):
                return candidates
        if candidates:
            return candidates

    normalized_input = _normalize_value(input_value)
    if normalized_input:
        wildcard = f"%{'%'.join(normalized_input)}%"
        wildcard_records = client.execute_kw(
            model,
            "search_read",
            [[[field, "ilike", wildcard]]],
            {"fields": [field], "limit": limit},
        )
        if add_records(wildcard_records):
            return candidates
        if candidates:
            return candidates

    if stripped_input:
        fuzzy_records = client.execute_kw(
            model,
            "search_read",
            [[[field, "ilike", stripped_input]]],
            {"fields": [field], "limit": limit},
        )
        add_records(fuzzy_records)

    return candidates


def find_id(
    client: OdooClient,
    model: str,
    input_value: str,
    *,
    fields: list[str],
    limit: int = 100,
) -> int:
    """
    Resolve an Odoo record ID using progressive prefix filtering across the given fields.

    The search starts with candidates fetched via ``search_read`` for each field (in order),
    then filters them by progressively extending the normalized input prefix. If a normalized
    exact match is found for any candidate, its ID is returned immediately. Otherwise, the
    winner is chosen deterministically using shortest normalized length, lexicographical order,
    then lowest record ID.
    """
    if not fields:
        raise ValueError("At least one field must be provided to locate record IDs.")
    if not input_value:
        raise ValueError("Input value is empty; unable to determine record ID.")
    normalized_input = _normalize_value(input_value)
    if not normalized_input:
        raise ValueError(f"Input '{input_value}' is invalid after normalization.")

    for field in fields:
        candidates = _fetch_candidates_for_field(
            client,
            model,
            field,
            input_value,
            limit,
        )

        if not candidates:
            continue

        for candidate in candidates:
            if candidate[1] == normalized_input:
                return candidate[0]

        current_set = candidates
        last_non_empty: Optional[list[tuple[int, str, str]]] = None
        for index in range(1, len(normalized_input) + 1):
            prefix = normalized_input[:index]
            filtered = [candidate for candidate in current_set if candidate[1].startswith(prefix)]
            if filtered:
                last_non_empty = filtered
                current_set = filtered
            else:
                if last_non_empty:
                    return _select_candidate(last_non_empty)
                break
        else:
            if current_set:
                return _select_candidate(current_set)
            if last_non_empty:
                return _select_candidate(last_non_empty)

    raise ValueError(f"No '{model}' record matches '{input_value}'.")


def create_demo_sale_order(settings: DemoSettings) -> Tuple[int, dict[str, Any]]:
    client = get_odoo_client()
    company_id = find_id(client, "res.company", settings.company, fields=["name"])
    customer_id = find_id(client, "res.partner", settings.customer, fields=["name"])
    salesperson_id = find_id(client, "res.users", settings.salesperson, fields=["name"])
    order_date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    order_lines = []
    for index, line in enumerate(settings.order_lines, start=1):
        quantity = parse_quantity(line["quantity"])
        product_id = find_id(
            client,
            "product.product",
            line["product"],
            fields=["default_code", "name"],
        )
        order_line_values = {
            "product_id": product_id,
            "product_uom_qty": quantity,
        }
        delivery_date = line.get("x_studio_delivery_date")
        if delivery_date:
            order_line_values["x_studio_delivery_date"] = normalize_odoo_datetime(
                delivery_date,
                f"Delivery Date (line {index})",
            )
        order_lines.append((0, 0, order_line_values))

    if not order_lines:
        raise ValueError("DemoSettings defines no order lines to import.")

    vals = {
        "partner_id": customer_id,
        "company_id": company_id,
        "user_id": salesperson_id,
        "date_order": order_date_iso,
        "x_studio_customer_po_number": settings.x_studio_customer_po_number,
        # Build one2many commands with (0, 0, values) per XML-RPC protocol (contentReference[oaicite:3])
        "order_line": order_lines,
    }

    # Call create() via execute_kw to obtain the new order ID (contentReference[oaicite:6])
    order_id = client.execute_kw("sale.order", "create", [vals])
    log.info(
        "Created demo sale.order %s (PO: %s)",
        order_id,
        settings.x_studio_customer_po_number,
    )

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
