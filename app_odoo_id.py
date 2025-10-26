from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Iterable

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


def _iter_window_indices(max_start: int) -> Iterable[int]:
    yield 0
    for index in range(1, max_start + 1):
        yield index


def _add_candidate_records(
    records: Iterable[dict[str, Any]],
    *,
    field: str,
    candidates: list[tuple[int, str, str]],
    seen_ids: set[int],
) -> None:
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


def _fetch_candidates_for_field(
    client: "OdooClient",
    model: str,
    field: str,
    input_value: str,
) -> list[tuple[int, str, str]]:
    stripped_input = input_value.strip()
    candidates: list[tuple[int, str, str]] = []
    seen_ids: set[int] = set()

    if stripped_input:
        exact_records = client.execute_kw(
            model,
            "search_read",
            [[[field, "=", stripped_input]]],
            {"fields": [field]},
        )
        _add_candidate_records(
            exact_records, field=field, candidates=candidates, seen_ids=seen_ids
        )
        if candidates:
            log.warning(
                "Exact '=' match produced candidates for stripped %r (normalized %r); skipping substring windows",
                stripped_input,
                _normalize_value(stripped_input),
            )
            return candidates

        length = len(stripped_input)
        normalized_stripped = _normalize_value(stripped_input)
        seen_substrings: set[str] = set()
        # 循序递减子串长度，保留前缀优先
        for substring_length in range(length, 0, -1):
            log.warning(
                "Sliding window length %d for stripped %r (normalized %r)",
                substring_length,
                stripped_input,
                normalized_stripped,
            )
            max_start = length - substring_length
            # 如果有空隙，则允许窗口向右滑动
            if max_start:
                log.warning("Window length %d allows %d total positions", substring_length, max_start + 1)
            # 针对该长度的每个起点尝试查询
            for start_index in _iter_window_indices(max_start):
                substring = stripped_input[start_index : start_index + substring_length]
                normalized_substring = _normalize_value(substring)
                log.warning(
                    "Evaluating window [%d:%d] slice %r (normalized %r) from stripped %r",
                    start_index,
                    start_index + substring_length,
                    substring,
                    normalized_substring,
                    stripped_input,
                )
                # 忽略被归一化成空的窗口
                if not normalized_substring:
                    log.warning("Skipping non-alphanumeric window %r (normalized %r)", substring, normalized_substring)
                    continue
                query_key = substring.casefold()
                # 避免重复查询同样的窗口
                if query_key in seen_substrings:
                    log.warning("Skipping duplicate window %r (normalized %r)", substring, normalized_substring)
                    continue
                seen_substrings.add(query_key)
                before_count = len(candidates)
                substring_records = client.execute_kw(
                    model,
                    "search_read",
                    [[[field, "ilike", f"%{substring}%"]]],
                    {"fields": [field]},
                )
                _add_candidate_records(
                    substring_records, field=field, candidates=candidates, seen_ids=seen_ids
                )
                if len(candidates) > before_count:
                    log.warning(
                        "Candidates found using window %r (normalized %r); skipping smaller windows",
                        substring,
                        normalized_substring,
                    )
                    return candidates
                if substring_records:
                    log.warning(
                        "Window %r (normalized %r) matched existing candidates; skipping smaller windows",
                        substring,
                        normalized_substring,
                    )
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
        _add_candidate_records(
            wildcard_records, field=field, candidates=candidates, seen_ids=seen_ids
        )
        if candidates:
            return candidates

    if stripped_input:
        fuzzy_records = client.execute_kw(
            model,
            "search_read",
            [[[field, "ilike", stripped_input]]],
            {"fields": [field]},
        )
        _add_candidate_records(
            fuzzy_records, field=field, candidates=candidates, seen_ids=seen_ids
        )

    return candidates


def _resolve_with_fields(
    field_candidates: dict[str, list[tuple[int, str, str]]],
    active_fields: Iterable[str],
    *,
    model: str,
    input_value: str,
    normalized_input: str,
) -> int | None:
    field_order = list(active_fields)
    if not field_order:
        return None
    normalized_length = len(normalized_input)
    for window_size in range(normalized_length, 0, -1):
        max_start = normalized_length - window_size
        for start_index in _iter_window_indices(max_start):
            window = normalized_input[start_index : start_index + window_size]
            aggregated_candidates: dict[int, tuple[int, str, str]] = {}
            for field in field_order:
                base_candidates = field_candidates.get(field)
                if not base_candidates:
                    continue
                filtered = [candidate for candidate in base_candidates if window in candidate[1]]
                prefix_filtered = [candidate for candidate in filtered if candidate[1].startswith(window)]
                active_set = prefix_filtered or filtered
                matched = bool(active_set)
                log.warning(
                    " %s | %s | %s | %s | %d | %d | %s | %s",
                    model,
                    field,
                    input_value,
                    window,
                    window_size,
                    start_index,
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
            if aggregated_candidates:
                return _select_candidate(
                    list(aggregated_candidates.values()),
                    model=model,
                    input_value=input_value,
                    normalized_input=normalized_input,
                )
    return None


def find_id(
    client: "OdooClient",
    model: str,
    input_value: str,
    *,
    fields: list[str],
) -> int:
    if not fields:
        raise ValueError("At least one field must be provided to locate record IDs.")
    if not input_value:
        raise ValueError("Input value is empty; unable to determine record ID.")
    normalized_input = _normalize_value(input_value)
    if not normalized_input:
        raise ValueError(f"Input '{input_value}' is invalid after normalization.")
    field_candidates: dict[str, list[tuple[int, str, str]]] = {}
    processed_fields: list[str] = []
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
        if any(candidate[1] == normalized_input for candidate in candidates):
            exact_matches: dict[int, tuple[int, str, str]] = {}
            for existing_candidates in field_candidates.values():
                for candidate in existing_candidates:
                    if candidate[1] == normalized_input:
                        exact_matches.setdefault(candidate[0], candidate)
            if exact_matches:
                return _select_candidate(
                    list(exact_matches.values()),
                    model=model,
                    input_value=input_value,
                    normalized_input=normalized_input,
                )
        processed_fields.append(field)
        resolved = _resolve_with_fields(
            field_candidates,
            processed_fields,
            model=model,
            input_value=input_value,
            normalized_input=normalized_input,
        )
        if resolved is not None:
            return resolved

    if not field_candidates:
        raise ValueError(f"No '{model}' record matches '{input_value}'.")

    resolved = _resolve_with_fields(
        field_candidates,
        fields,
        model=model,
        input_value=input_value,
        normalized_input=normalized_input,
    )
    if resolved is not None:
        return resolved

    raise ValueError(f"No '{model}' record matches '{input_value}'.")


__all__ = ["find_id"]
