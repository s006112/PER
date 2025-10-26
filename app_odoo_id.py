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
) -> tuple[list[tuple[int, str, str]], int]:
    stripped_input = input_value.strip()
    candidates: list[tuple[int, str, str]] = []
    seen_ids: set[int] = set()
    matched_length = 0

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
        total_length = len(stripped_input)
        seen_substrings: set[str] = set()
        for substring_length in range(total_length, 0, -1):
            max_left_trim = total_length - substring_length
            for left_trim in range(0, max_left_trim + 1):
                substring = stripped_input[left_trim : left_trim + substring_length]
                if substring in seen_substrings:
                    continue
                seen_substrings.add(substring)
                if fetch([[field, "ilike", f"%{substring}%"]]):
                    matched_length = substring_length
                    return candidates, matched_length

    return candidates, matched_length


def find_id(
    client: "OdooClient",
    model: str,
    input_value: str,
    *,
    fields: list[str],
) -> int:
    normalized_input = _normalize_value(input_value)
    field_candidates: dict[str, list[tuple[int, str, str]]] = {}
    field_match_lengths: dict[str, int] = {}
    for field in fields:
        candidates, match_length = _fetch_candidates_for_field(
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
        field_match_lengths[field] = match_length
    best_match_length = max(field_match_lengths.values(), default=0)
    aggregated_candidates: dict[int, tuple[int, str, str]] = {}
    for field in fields:
        candidates = field_candidates.get(field)
        if not candidates:
            continue
        match_length = field_match_lengths.get(field, 0)
        if best_match_length > 0 and match_length < best_match_length:
            continue
        filtered = [
            candidate
            for candidate in candidates
            if normalized_input
            and (normalized_input in candidate[1] or candidate[1] in normalized_input)
        ]
        active_set = filtered or candidates
        log.warning(
            " %s | %s | %s | %s | %d | %s",
            model,
            field,
            input_value,
            normalized_input,
            match_length,
            "\n"
            + str(
                [
                    {"id": candidate[0], "normalized": candidate[1], "value": candidate[2]}
                    for candidate in active_set
                ]
            ),
        )
        for candidate in active_set:
            aggregated_candidates.setdefault(candidate[0], candidate)
    if aggregated_candidates:
        selected = min(
            aggregated_candidates.values(),
            key=lambda item: (len(item[1]), item[1], item[0]),
        )
        return selected[0]


__all__ = ["find_id"]
