from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app_odoo import OdooClient

log = logging.getLogger("app_odoo")

# " ACME-123 ") -> "acme123"
def _normalize_value(raw_value: str) -> str:
    return "".join(ch for ch in raw_value.casefold() if ch.isalnum())


def _fetch_candidates_for_field(
    client: "OdooClient",
    model: str,
    field: str,
    input_value: str,
) -> list[tuple[int, str, str]]:
    stripped_input = input_value.strip()
    candidates: list[tuple[int, str, str]] = []
    seen_ids: set[int] = set()

    def add_records(records: list[dict[str, Any]]) -> None:
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

    def fetch(domain: list[list[Any]]) -> bool:
        records = client.execute_kw(
            model,
            "search_read",
            [domain],
            {"fields": [field]},
        )
        add_records(records)
        return bool(candidates)

    if stripped_input:
        for substring_length in range(len(stripped_input), 0, -1):
            substring = stripped_input[:substring_length]
            if fetch([[field, "ilike", f"%{substring}%"]]):
                return candidates

    return candidates


def find_id(
    client: "OdooClient",
    model: str,
    input_value: str,
    *,
    fields: list[str],
) -> int:
    normalized_input = _normalize_value(input_value)
    field_candidates: dict[str, list[tuple[int, str, str]]] = {}
    field_current: dict[str, list[tuple[int, str, str]]] = {}
    for field in fields:
        candidates = _fetch_candidates_for_field(
            client,
            model,
            field,
            input_value,
        )
        total_count = client.execute_kw(
            model,
            "search_count",
            [[[field, "!=", False]]],
        )
        log.warning(
            " %s | %s | fetched=%d | available=%d",
            model,
            field,
            len(candidates),
            total_count,
        )
        if not candidates:
            continue
        field_candidates[field] = candidates
        field_current[field] = candidates

    for window_end in range(len(normalized_input), 0, -1):
        window = normalized_input[:window_end]
        aggregated_candidates: dict[int, tuple[int, str, str]] = {}
        for field in fields:
            base_candidates = field_candidates.get(field)
            if not base_candidates:
                continue
            current_set = field_current[field]
            filtered = [candidate for candidate in current_set if window in candidate[1]]
            prefix_filtered = [candidate for candidate in filtered if candidate[1].startswith(window)]
            active_set = prefix_filtered or filtered
            matched = bool(active_set)
            log.warning(
                " %s | %s | %s | %s | %d | %s | %s",
                model,
                field,
                input_value,
                window,
                window_end,
                matched,
                "\n"
                + str(
                    [
                        {"id": candidate[0], "normalized": candidate[1], "value": candidate[2]}
                        for candidate in active_set
                    ]
                )
                if matched
                else None,
            )
            if matched:
                for candidate in active_set:
                    aggregated_candidates.setdefault(candidate[0], candidate)
                field_current[field] = active_set
            else:
                field_current[field] = base_candidates
        if aggregated_candidates:
            selected = min(
                aggregated_candidates.values(),
                key=lambda item: (len(item[1]), item[1], item[0]),
            )
            return selected[0]


__all__ = ["find_id"]