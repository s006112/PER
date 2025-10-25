from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app_odoo import OdooClient

log = logging.getLogger("app_odoo")

_NORMALIZE_PATTERN = re.compile(r"[\W_]+", re.UNICODE)

# Normalize a raw string into a casefolded, alphanumeric token for comparison.
# Example: _normalize_value(" ACME-123 ") -> "acme123"
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
    client: "OdooClient",
    model: str,
    input_value: str,
    *,
    fields: list[str],
    limit: int = 100,
) -> int:
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
        candidates = _fetch_candidates_for_field(
            client,
            model,
            field,
            input_value,
            limit,
        )

        if not candidates:
            continue

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
                candidate_map = {candidate[0]: candidate for candidate in active_set}
                if log.isEnabledFor(logging.WARNING):
                    log.warning(
                        "Window match | model=%s field=%s input=%s window=%s window_end=%d candidates=%s",
                        model,
                        field,
                        input_value,
                        window,
                        window_end,
                        [
                            {"id": candidate[0], "normalized": candidate[1], "value": candidate[2]}
                            for candidate in candidate_map.values()
                        ],
                    )
                current_set = active_set
                return _select_candidate(
                    list(candidate_map.values()),
                    model=model,
                    input_value=input_value,
                    normalized_input=normalized_input,
                )
            else:
                if log.isEnabledFor(logging.WARNING):
                    log.warning(
                        "Window mismatch | model=%s field=%s input=%s window=%s window_end=%d",
                        model,
                        field,
                        input_value,
                        window,
                        window_end,
                    )
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

    raise ValueError(f"No '{model}' record matches '{input_value}'.")


__all__ = ["find_id"]
