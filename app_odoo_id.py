from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app_odoo import OdooClient

log = logging.getLogger("app_odoo")

# Normalize a raw string into a casefolded, alphanumeric token for comparison.
# Example: _normalize_value(" ACME-123 ") -> "acme123"
def _normalize_value(raw_value: str) -> str:
    return "".join(ch for ch in raw_value.casefold() if ch.isalnum())


def _select_candidate(
    candidates: list[tuple[int, str, str]],
    *,
    model: str,
    input_value: str,
    normalized_input: str,
) -> int:
    selected_candidate = min(candidates, key=lambda item: (len(item[1]), item[1], item[0]))
#    if selected_candidate[1] != normalized_input:
#        log.warning(
#            "[[%s]] not exactly found in Odoo system, replaced by [[%s]]",
#            input_value,
#            selected_candidate[2],
#        )
    return selected_candidate[0]


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

    if stripped_input:
        exact_records = client.execute_kw(
            model,
            "search_read",
            [[[field, "=", stripped_input]]],
            {"fields": [field]},
        )
        add_records(exact_records)
        if candidates:
            return candidates

        for substring_length in range(len(stripped_input), 0, -1):
            substring = stripped_input[:substring_length]
            substring_records = client.execute_kw(
                model,
                "search_read",
                [[[field, "ilike", f"%{substring}%"]]],
                {"fields": [field]},
            )
            add_records(substring_records)
            if candidates:
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
            {"fields": [field]},
        )
        add_records(wildcard_records)
        if candidates:
            return candidates

    if stripped_input:
        fuzzy_records = client.execute_kw(
            model,
            "search_read",
            [[[field, "ilike", stripped_input]]],
            {"fields": [field]},
        )
        add_records(fuzzy_records)

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
            return _select_candidate(
                list(aggregated_candidates.values()),
                model=model,
                input_value=input_value,
                normalized_input=normalized_input,
            )


__all__ = ["find_id"]
