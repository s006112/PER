from __future__ import annotations

import ast
import base64
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
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
            try:
                dt = datetime.strptime(cleaned.title(), "%d-%b-%Y")
            except ValueError as exc_two:
                raise ValueError(
                    f"Unable to parse {field_name} value '{value}' into an ISO datetime string.",
                ) from exc_two
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


_NORMALIZE_PATTERN = re.compile(r"[\W_]+", re.UNICODE)


def _normalize_value(raw_value: str) -> str:
    return _NORMALIZE_PATTERN.sub("", raw_value.casefold())


def _select_candidate(
    candidates: list[tuple[int, str, str]],
    *,
    model: str,
    input_value: str,
    normalized_input: str,
) -> int:
    selected_candidate = min(candidates, key=lambda item: (len(item[1]), item[1], item[0]))
    if selected_candidate[1] != normalized_input:
        log.warning(
            "[[%s]] not exactly found in Odoo system, replaced by [[%s]]",
            input_value,
            selected_candidate[2],
        )
    return selected_candidate[0]


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

        for substring_length in range(len(stripped_input), 0, -1):
            substring = stripped_input[:substring_length]
            substring_records = client.execute_kw(
                model,
                "search_read",
                [[[field, "ilike", f"%{substring}%"]]],
                {"fields": [field], "limit": limit},
            )
            if add_records(substring_records):
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
    # Require at least one lookup field and a non-empty value that survives normalization.
    if not fields:
        raise ValueError("At least one field must be provided to locate record IDs.")
    if not input_value:
        raise ValueError("Input value is empty; unable to determine record ID.")
    normalized_input = _normalize_value(input_value)
    if not normalized_input:
        raise ValueError(f"Input '{input_value}' is invalid after normalization.")
    best_match_length = 0
    best_match_candidates: dict[int, tuple[int, str, str]] = {}

    for field in fields:
        # Gather candidate records for this field using layered search heuristics.
        candidates = _fetch_candidates_for_field(
            client,
            model,
            field,
            input_value,
            limit,
        )

        if not candidates:
            continue

        # Return immediately when a normalized exact match surfaces.
        for candidate in candidates:
            if candidate[1] == normalized_input:
                return candidate[0]

        base_candidates = candidates
        current_set = candidates
        field_best_length = 0
        field_best_map: dict[int, tuple[int, str, str]] = {}
        for window_end in range(len(normalized_input), 0, -1):
            window = normalized_input[:window_end]
            filtered = [candidate for candidate in current_set if window in candidate[1]]
            if filtered:
                prefix_filtered = [candidate for candidate in filtered if candidate[1].startswith(window)]
                active_set = prefix_filtered or filtered
                # Keep shrinking the candidate pool as windows get shorter while recording strongest coverage.
                current_set = active_set
                if window_end > field_best_length:
                    field_best_length = window_end
                    field_best_map = {candidate[0]: candidate for candidate in active_set}
                elif window_end == field_best_length and field_best_length:
                    for candidate in active_set:
                        field_best_map.setdefault(candidate[0], candidate)
            else:
                current_set = base_candidates
                continue

        if field_best_map:
            if field_best_length == len(normalized_input):
                return _select_candidate(
                    list(field_best_map.values()),
                    model=model,
                    input_value=input_value,
                    normalized_input=normalized_input,
                )
            if field_best_length > best_match_length:
                best_match_length = field_best_length
                best_match_candidates = field_best_map.copy()
            elif field_best_length == best_match_length:
                for candidate in field_best_map.values():
                    best_match_candidates.setdefault(candidate[0], candidate)

    if best_match_candidates:
        return _select_candidate(
            list(best_match_candidates.values()),
            model=model,
            input_value=input_value,
            normalized_input=normalized_input,
        )

    # No candidates ever matched across all fields.
    raise ValueError(f"No '{model}' record matches '{input_value}'.")


def parse_po_response_text(po_response: str) -> dict[str, Any]:
    if not po_response or not po_response.strip():
        raise ValueError("PO response is empty.")
    try:
        tree = ast.parse(po_response, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"PO response has invalid syntax: {exc}") from exc

    parsed: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Attribute):
            continue
        if not isinstance(target.value, ast.Name) or target.value.id != "self":
            continue
        field_name = target.attr
        try:
            parsed[field_name] = ast.literal_eval(node.value)
        except Exception as exc:
            raise ValueError(f"Unable to parse value for '{field_name}': {exc}") from exc

    # keep required field order aligned with the expected PO text sequence
    required_fields = [
        "salesperson",
        "company",
        "customer",
        "x_studio_customer_po_number",
        "order_lines",
    ]
    missing = [field for field in required_fields if field not in parsed]
    if missing:
        raise ValueError(f"PO response missing required fields: {', '.join(missing)}")
    if not isinstance(parsed["order_lines"], list) or not parsed["order_lines"]:
        raise ValueError("order_lines must be a non-empty list.")
    return parsed


def create_sale_order(po_data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    client = get_odoo_client()
    customer_value = str(po_data["customer"])
    customer_has_acuity = "acuity" in customer_value.lower()
    customer_id = find_id(client, "res.partner", customer_value, fields=["name"])
    salesperson_id = find_id(client, "res.users", po_data["salesperson"], fields=["name"])
    order_date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    order_lines = []
    for index, line in enumerate(po_data["order_lines"], start=1):
        if not isinstance(line, dict):
            raise ValueError(f"Order line {index} is not a dictionary.")
        product_value = line.get("product")
        if not product_value:
            raise ValueError(f"Order line {index} missing 'product'.")
        quantity_value = line.get("quantity")
        if quantity_value is None:
            raise ValueError(f"Order line {index} missing 'quantity'.")
        quantity = parse_quantity(str(quantity_value))
        product_id = find_id(
            client,
            "product.product",
            str(product_value),
            fields=["default_code", "name"],
        )
        order_line_values = {
            "product_id": product_id,
            "product_uom_qty": quantity,
        }
        delivery_date = line.get("x_studio_delivery_date")
        if delivery_date and not customer_has_acuity:
            order_line_values["x_studio_delivery_date"] = normalize_odoo_datetime(
                str(delivery_date),
                f"Delivery Date (line {index})",
            )
        order_lines.append((0, 0, order_line_values))

    current_company_name = str(po_data["company"])

    company_id = find_id(client, "res.company", current_company_name, fields=["name"])
    vals = {
        "partner_id": customer_id,
        "company_id": company_id,
        "user_id": salesperson_id,
        "date_order": order_date_iso,
        "x_studio_customer_po_number": po_data["x_studio_customer_po_number"],
        # Build one2many commands with (0, 0, values) per XML-RPC protocol (contentReference[oaicite:3])
        "order_line": order_lines,
    }

    try:
        # Call create() via execute_kw to obtain the new order ID (contentReference[oaicite:6])
        order_id = client.execute_kw("sale.order", "create", [vals])
    except xmlrpc_client.Fault as exc:
        fault_message = exc.faultString or ""
        raise RuntimeError(f"Odoo error while creating sale order: {fault_message}") from exc

    log.info("Created sale.order %s (PO: %s)", order_id, po_data["x_studio_customer_po_number"])

    order_data = client.execute_kw(
        "sale.order",
        "read",
        [[order_id], ["name", "order_line"]],
    )
    log.info("Order %s readback: %s", order_id, order_data)
    return order_id, order_data[0] if order_data else {}


def create_sale_order_from_text(po_response: str) -> tuple[int, dict[str, Any]]:
    po_data = parse_po_response_text(po_response)
    return create_sale_order(po_data)


def attach_pdf_to_sale_order(
    sale_order_identifier: str,
    pdf_path: str,
    note_body: str = "Attached customer PO",
) -> int:
    client = get_odoo_client()
    order_id = find_id(client, "sale.order", sale_order_identifier, fields=["name"])

    pdf_path_clean = str(pdf_path).strip()
    with open(pdf_path_clean, "rb") as pdf_file:
        pdf_bytes = pdf_file.read()

    encoded_pdf = base64.b64encode(pdf_bytes).decode("ascii")
    attachment_vals = {
        "name": os.path.basename(pdf_path_clean),
        "type": "binary",
        "datas": encoded_pdf,
        "res_model": "sale.order",
        "res_id": order_id,
        "mimetype": "application/pdf",
    }

    try:
        attachment_result = client.execute_kw("ir.attachment", "create", [attachment_vals])
        attachment_id = int(attachment_result)
        client.execute_kw(
            "sale.order",
            "message_post",
            [[order_id]],
            {"body": note_body, "attachment_ids": [attachment_id]},
        )
    except xmlrpc_client.Fault as exc:
        log.error("Failed attaching PDF to sale.order %s: %s", sale_order_identifier, exc)
        raise RuntimeError(f"Odoo error while attaching PDF: {exc.faultString}") from exc

    log.info(
        "Attached PDF '%s' to sale.order %s (order_id=%s, attachment_id=%s)",
        os.path.basename(pdf_path_clean),
        sale_order_identifier,
        order_id,
        attachment_id,
    )
    return attachment_id


__all__ = [
    "attach_pdf_to_sale_order",
    "create_sale_order",
    "create_sale_order_from_text",
    "parse_po_response_text",
]
