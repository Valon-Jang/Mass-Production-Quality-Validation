"""Build and validate offline, review-only AI Mapping candidate exchanges."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.ai_mapping import (
    AI_MAPPING_RESPONSE_SCHEMA_VERSION,
    AIMappingCandidate,
    AIMappingCandidateRole,
    AIMappingConfidence,
    AIMappingIssue,
    AIMappingIssueCode,
    AIMappingOutcome,
    AIMappingOutcomeState,
    AIMappingProvider,
    AIMappingProviderError,
    AIMappingProviderTimeoutError,
    AIMappingRequest,
    AIMappingRequestLimits,
    AIResponseWarning,
    AIRowShape,
    AISheetStructure,
    AISourceToken,
    AISourceValueKind,
    AIUnresolvedCode,
    AIUnresolvedItem,
    AIWarningCode,
    UntrustedDataTag,
)
from app.domain.mapping import MappingPreviewState
from app.domain.workbook_scan import CellEvidence, SheetScan, WorkbookScan

_COORDINATE_PARTS_PATTERN = re.compile(r"([A-Z]{1,3})([1-9][0-9]{0,6})")
_TOP_STRUCTURE_ROW_LIMIT = 32
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?i)(?:^|\s)[A-Z]:\\"),
    re.compile(r"(?:^|\s)\\\\[^\\\s]+\\"),
    re.compile(r"(?i)\b(?:api[_ -]?key|password|passwd|authorization)\s*[:=]"),
    re.compile(r"(?i)\bbearer\s+[A-Z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)https?://"),
)
_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions?|prompts?)"),
    re.compile(r"(?i)(?:reveal|print|return)\s+(?:the\s+)?system\s+prompt"),
    re.compile(r"이전\s*(?:지시|명령|프롬프트)(?:를|을)?\s*무시"),
    re.compile(r"시스템\s*프롬프트(?:를|을)?\s*(?:공개|출력|반환)"),
    re.compile(r"JSON\s*(?:대신|형식은\s*무시)"),
)
_FORBIDDEN_ACTION_KEYS = {
    "action",
    "actions",
    "approve",
    "approved",
    "approval",
    "auto_approve",
    "calculation",
    "cpk",
    "db_write",
    "decision",
    "mapping_template",
    "mean",
    "official_value",
    "pass_fail",
    "persist",
    "spec_value",
    "standardized_value",
    "system_judgment",
    "threshold",
    "승인",
    "계산",
    "판정",
}
_FORBIDDEN_ACTION_TEXT_PATTERNS = (
    re.compile(
        r"(?i)(?<![A-Z])(?:PASS|FAIL)(?![A-Z])\s*(?:로|as)?\s*"
        r"(?:판정|결정|확정|처리|decision)"
    ),
    re.compile(r"(?:합격|불합격)\s*(?:판정|결정|확정|처리)"),
    re.compile(r"(?:자동|공식)\s*승인"),
    re.compile(r"(?:승인|저장|영구\s*반영)\s*(?:하라|한다|완료|실행)"),
    re.compile(r"(?i)(?:Cpk|Ppk|평균|관리\s*한계)\s*(?:을|를)?\s*(?:계산|산출)"),
    re.compile(r"(?i)(?:Spec|규격|공차)\s*(?:을|를)?\s*(?:생성|변경|확정)"),
)

_RoleLiteral = Literal[
    "MODEL",
    "PART_NUMBER",
    "LOT_NUMBER",
    "INSPECTION_DATE",
    "SUPPLIER",
    "REPORT_NUMBER",
    "REVISION",
    "INSPECTION_ITEM",
    "METHOD",
    "INSTRUMENT",
    "SPECIFICATION_SOURCE",
    "TOLERANCE_SOURCE",
    "MINIMUM_SOURCE",
    "MAXIMUM_SOURCE",
    "SAMPLE_SOURCE",
    "SUPPLIER_RESULT_SOURCE",
]
_ConfidenceLiteral = Literal["HIGH", "MEDIUM", "LOW"]
_ValueKindLiteral = Literal[
    "TEXT",
    "INTEGER",
    "NUMBER",
    "BOOLEAN",
    "DATE",
    "DATETIME",
    "DECIMAL",
]
_UnresolvedCodeLiteral = Literal["AMBIGUOUS_SOURCE", "SOURCE_NOT_FOUND", "MEANING_UNCLEAR"]
_WarningCodeLiteral = Literal["AMBIGUOUS_STRUCTURE", "LOW_CONFIDENCE", "INCOMPLETE_COVERAGE"]


class _StrictCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: _RoleLiteral
    source_id: Annotated[str, Field(min_length=1, max_length=64)]
    sheet_name: Annotated[str, Field(min_length=1, max_length=31)]
    sheet_position: Annotated[int, Field(ge=0)]
    coordinate: Annotated[str, Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")]
    source_token: str | int | float | bool
    source_value_kind: _ValueKindLiteral
    confidence: _ConfidenceLiteral
    reason_ko: Annotated[str, Field(min_length=1, max_length=240)]


class _StrictUnresolved(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_role: _RoleLiteral
    code: _UnresolvedCodeLiteral
    reason_ko: Annotated[str, Field(min_length=1, max_length=240)]


class _StrictWarning(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: _WarningCodeLiteral
    message_ko: Annotated[str, Field(min_length=1, max_length=240)]


class _StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["mass-production-quality-validation.ai-mapping-response.v1"]
    request_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    candidates: Annotated[list[_StrictCandidate], Field(max_length=128)]
    unresolved: Annotated[list[_StrictUnresolved], Field(max_length=128)]
    warnings: Annotated[list[_StrictWarning], Field(max_length=128)]


class _DuplicateJSONKey(ValueError):
    pass


class _NonFiniteJSONValue(ValueError):
    pass


class AIMappingRequestRejected(ValueError):
    """Pre-provider review hold with no outbound payload."""

    def __init__(self, issue: AIMappingIssue) -> None:
        self.issue = issue
        super().__init__(issue.code.value)


def build_ai_mapping_request(
    scan: WorkbookScan,
    *,
    limits: AIMappingRequestLimits | None = None,
) -> AIMappingRequest:
    """Minimize a scan into bounded Korean structural data with inert text tokens."""

    resolved_limits = limits or AIMappingRequestLimits()
    _validate_scan_for_ai(scan)
    ordered_sheets = tuple(sorted(scan.sheets, key=lambda sheet: sheet.position))
    selected_sheets = ordered_sheets[: resolved_limits.max_sheets]
    remaining_token_capacity = resolved_limits.max_tokens
    structures: list[AISheetStructure] = []
    omitted_total = 0

    for sheet in selected_sheets:
        structure, consumed_tokens = _sheet_structure(
            sheet,
            limits=resolved_limits,
            remaining_token_capacity=remaining_token_capacity,
        )
        structures.append(structure)
        remaining_token_capacity -= consumed_tokens
        omitted_total += structure.omitted_token_count

    for sheet in ordered_sheets[len(selected_sheets) :]:
        omitted_total += sum(_safe_source_value(cell) is not None for cell in sheet.cells)

    return AIMappingRequest(
        sheets=tuple(structures),
        total_sheet_count=len(scan.sheets),
        total_estimated_cells=scan.estimated_cells,
        omitted_sheet_count=len(scan.sheets) - len(structures),
        omitted_token_count=omitted_total,
    )


def propose_ai_mapping_candidates(
    scan: WorkbookScan,
    provider: AIMappingProvider | None,
    *,
    enabled: bool = True,
    timeout_seconds: float = 30.0,
    limits: AIMappingRequestLimits | None = None,
    mapping_preview_state: MappingPreviewState | None = None,
) -> AIMappingOutcome:
    """Call an injected provider and fail closed to typed, non-mutating outcomes."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if mapping_preview_state == MappingPreviewState.PREVIEW_READY:
        return AIMappingOutcome(
            state=AIMappingOutcomeState.REVIEW_HOLD,
            request=None,
            candidates=(),
            issues=(
                AIMappingIssue(
                    code=AIMappingIssueCode.AI_NOT_REQUIRED_EXISTING_MAPPING,
                    message_ko="승인된 Mapping Preview 경로가 있어 AI 후보를 호출하지 않습니다.",
                ),
            ),
        )
    resolved_limits = limits or AIMappingRequestLimits()
    try:
        request = build_ai_mapping_request(scan, limits=resolved_limits)
    except AIMappingRequestRejected as rejected:
        return AIMappingOutcome(
            state=AIMappingOutcomeState.REVIEW_HOLD,
            request=None,
            candidates=(),
            issues=(rejected.issue,),
        )
    if not request.tokens:
        return AIMappingOutcome(
            state=AIMappingOutcomeState.REVIEW_HOLD,
            request=request,
            candidates=(),
            issues=(
                AIMappingIssue(
                    code=AIMappingIssueCode.NO_STRUCTURAL_TOKENS,
                    message_ko="외부 AI에 보낼 수 있는 안전한 구조 텍스트가 없습니다.",
                ),
            ),
        )
    if not enabled or provider is None:
        return _unavailable(
            request,
            AIMappingIssueCode.PROVIDER_DISABLED,
            "AI Provider가 비활성화되어 수동 Mapping 경로를 유지합니다.",
        )

    try:
        raw_response = provider.complete(request.to_json(), timeout_seconds=timeout_seconds)
    except (AIMappingProviderTimeoutError, TimeoutError):
        return _unavailable(
            request,
            AIMappingIssueCode.PROVIDER_TIMEOUT,
            "AI Provider가 제한 시간 안에 응답하지 않아 수동 검토로 전환합니다.",
        )
    except AIMappingProviderError:
        return _unavailable(
            request,
            AIMappingIssueCode.PROVIDER_FAILURE,
            "AI Provider 호출이 실패하여 수동 Mapping 경로를 유지합니다.",
        )
    except Exception:
        # A future adapter may fail before it can normalize its own exception. The
        # exception text is deliberately discarded so secrets/URLs never cross this boundary.
        return _unavailable(
            request,
            AIMappingIssueCode.PROVIDER_FAILURE,
            "AI Provider 호출이 실패하여 수동 Mapping 경로를 유지합니다.",
        )

    return _validate_response(
        scan,
        request,
        raw_response,
        limits=resolved_limits,
    )


def _validate_scan_for_ai(scan: WorkbookScan) -> None:
    sheet_names: set[str] = set()
    sheet_positions: set[int] = set()
    for sheet in scan.sheets:
        if (
            not sheet.name.strip()
            or sheet.name in sheet_names
            or sheet.position < 0
            or sheet.position in sheet_positions
        ):
            raise AIMappingRequestRejected(
                AIMappingIssue(
                    code=AIMappingIssueCode.SCAN_SOURCE_AMBIGUOUS,
                    message_ko="중복되거나 잘못된 Sheet 위치 때문에 AI 후보 요청을 보류합니다.",
                )
            )
        if _contains_sensitive_text(sheet.name):
            raise AIMappingRequestRejected(
                AIMappingIssue(
                    code=AIMappingIssueCode.SENSITIVE_STRUCTURE_REJECTED,
                    message_ko="민감정보로 보이는 Sheet 이름은 AI에 보내지 않습니다.",
                )
            )
        if _contains_prompt_injection(sheet.name):
            raise AIMappingRequestRejected(_prompt_injection_issue())
        sheet_names.add(sheet.name)
        sheet_positions.add(sheet.position)

        coordinates: set[str] = set()
        for cell in sheet.cells:
            if not _coordinate_is_canonical(cell.coordinate) or cell.coordinate in coordinates:
                raise AIMappingRequestRejected(
                    AIMappingIssue(
                        code=AIMappingIssueCode.SCAN_SOURCE_AMBIGUOUS,
                        message_ko="중복되거나 잘못된 Cell 위치 때문에 AI 후보 요청을 보류합니다.",
                    )
                )
            coordinates.add(cell.coordinate)
            if isinstance(cell.stored_value, str) and _contains_prompt_injection(cell.stored_value):
                raise AIMappingRequestRejected(_prompt_injection_issue())


def _sheet_structure(
    sheet: SheetScan,
    *,
    limits: AIMappingRequestLimits,
    remaining_token_capacity: int,
) -> tuple[AISheetStructure, int]:
    structural_rows = {
        candidate.row_index for candidate in sheet.row_candidates if candidate.row_index >= 1
    }
    tokens: list[AISourceToken] = []
    omitted_tokens = 0
    sheet_capacity = min(limits.max_tokens_per_sheet, remaining_token_capacity)
    scalar_token_count = 0
    scalar_tokens_by_row: dict[int, int] = defaultdict(int)
    for cell in sorted(
        sheet.cells,
        key=lambda candidate: _coordinate_sort_key(candidate.coordinate),
    ):
        safe_value = _safe_source_value(cell)
        if safe_value is None:
            continue
        value, value_kind, trust = safe_value
        coordinate_row = _coordinate_row(cell.coordinate)
        selected_as_structure = (
            coordinate_row <= _TOP_STRUCTURE_ROW_LIMIT or coordinate_row in structural_rows
        )
        is_scalar = value_kind != AISourceValueKind.TEXT
        if (
            not selected_as_structure
            or (isinstance(value, str) and len(value) > limits.max_token_characters)
            or (isinstance(value, str) and _CONTROL_CHARACTER_PATTERN.search(value))
            or (isinstance(value, str) and _contains_sensitive_text(value))
            or (is_scalar and scalar_token_count >= limits.max_scalar_tokens_per_sheet)
            or (
                is_scalar
                and scalar_tokens_by_row[coordinate_row] >= limits.max_scalar_tokens_per_row
            )
            or len(tokens) >= sheet_capacity
        ):
            omitted_tokens += 1
            continue
        if is_scalar:
            scalar_token_count += 1
            scalar_tokens_by_row[coordinate_row] += 1
        tokens.append(
            AISourceToken(
                source_id=f"S{sheet.position:03d}-{cell.coordinate}",
                sheet_name=sheet.name,
                sheet_position=sheet.position,
                coordinate=cell.coordinate,
                value=value,
                value_kind=value_kind,
                trust=trust,
            )
        )

    row_coordinates: dict[int, list[str]] = defaultdict(list)
    for cell in sheet.cells:
        row_coordinates[_coordinate_row(cell.coordinate)].append(cell.coordinate)
    ordered_rows = sorted(row_coordinates.items())
    row_shapes = tuple(
        AIRowShape(
            row_index=row_index,
            non_empty_coordinates=tuple(
                sorted(coordinates, key=_coordinate_sort_key)[: limits.max_coordinates_per_row]
            ),
        )
        for row_index, coordinates in ordered_rows[: limits.max_rows_per_sheet]
    )
    merged_ranges = sheet.merged_ranges[: limits.max_merged_ranges_per_sheet]
    return (
        AISheetStructure(
            name=sheet.name,
            position=sheet.position,
            kind=sheet.kind.value,
            visibility=sheet.visibility,
            used_range=sheet.used_range,
            merged_ranges=merged_ranges,
            row_shapes=row_shapes,
            tokens=tuple(tokens),
            omitted_token_count=omitted_tokens,
            row_shape_limit_reached=len(ordered_rows) > len(row_shapes),
            merge_limit_reached=len(sheet.merged_ranges) > len(merged_ranges),
        ),
        len(tokens),
    )


def _validate_response(
    scan: WorkbookScan,
    request: AIMappingRequest,
    raw_response: object,
    *,
    limits: AIMappingRequestLimits,
) -> AIMappingOutcome:
    if not isinstance(raw_response, str):
        return _invalid(
            request,
            AIMappingIssueCode.RESPONSE_SCHEMA_INVALID,
            "AI 응답은 UTF-8 JSON 문자열이어야 합니다.",
        )
    if len(raw_response.encode("utf-8")) > limits.max_response_bytes:
        return _invalid(
            request,
            AIMappingIssueCode.RESPONSE_TOO_LARGE,
            "AI 응답이 허용된 크기를 초과했습니다.",
        )
    try:
        decoded = json.loads(
            raw_response,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite_constant,
        )
    except (json.JSONDecodeError, _DuplicateJSONKey, _NonFiniteJSONValue, UnicodeError):
        return _invalid(
            request,
            AIMappingIssueCode.MALFORMED_JSON,
            "AI 응답이 중복 키나 비표준 값을 포함한 올바르지 않은 JSON입니다.",
        )
    if _contains_forbidden_action(decoded):
        return _invalid(
            request,
            AIMappingIssueCode.FORBIDDEN_ACTION_ATTEMPTED,
            "AI 응답이 승인·저장·계산·규격 변경·PASS/FAIL 판정을 시도했습니다.",
        )
    try:
        response = _StrictResponse.model_validate(decoded)
    except ValidationError:
        return _invalid(
            request,
            AIMappingIssueCode.RESPONSE_SCHEMA_INVALID,
            "AI 응답이 엄격한 후보 Schema와 일치하지 않습니다.",
        )
    if response.schema_version != AI_MAPPING_RESPONSE_SCHEMA_VERSION:
        return _invalid(
            request,
            AIMappingIssueCode.RESPONSE_SCHEMA_INVALID,
            "AI 응답 Schema 버전이 일치하지 않습니다.",
        )
    if response.request_digest != request.request_digest:
        return _invalid(
            request,
            AIMappingIssueCode.REQUEST_DIGEST_MISMATCH,
            "AI 응답이 현재 구조 요청과 연결되지 않아 폐기했습니다.",
        )
    if len(response.candidates) > limits.max_candidates:
        return _invalid(
            request,
            AIMappingIssueCode.RESPONSE_SCHEMA_INVALID,
            "AI 후보 수가 요청별 제한을 초과했습니다.",
        )
    unresolved = tuple(
        AIUnresolvedItem(
            target_role=AIMappingCandidateRole(item.target_role),
            code=AIUnresolvedCode(item.code),
            reason_ko=item.reason_ko,
        )
        for item in response.unresolved
    )
    warnings = tuple(
        AIResponseWarning(
            code=AIWarningCode(warning.code),
            message_ko=warning.message_ko,
        )
        for warning in response.warnings
    )
    if not response.candidates:
        return AIMappingOutcome(
            state=AIMappingOutcomeState.REVIEW_HOLD,
            request=request,
            candidates=(),
            issues=(
                AIMappingIssue(
                    code=AIMappingIssueCode.NO_CANDIDATES,
                    message_ko="AI가 위치 후보를 제안하지 않아 수동 검토가 필요합니다.",
                ),
            ),
            unresolved=unresolved,
            warnings=warnings,
        )

    request_tokens = {token.source_id: token for token in request.tokens}
    scan_cells = _scan_cell_index(scan)
    used_sources: set[str] = set()
    candidates: list[AIMappingCandidate] = []
    for raw_candidate in response.candidates:
        if raw_candidate.source_id in used_sources:
            return _invalid(
                request,
                AIMappingIssueCode.DUPLICATE_CANDIDATE,
                "동일한 원본 Cell이 둘 이상의 AI 후보로 중복 제안되었습니다.",
                source_id=raw_candidate.source_id,
            )
        used_sources.add(raw_candidate.source_id)
        source = request_tokens.get(raw_candidate.source_id)
        if source is None:
            return _invalid(
                request,
                AIMappingIssueCode.UNKNOWN_SOURCE,
                "요청에 없던 원본 Cell을 AI가 만들어냈습니다.",
                source_id=raw_candidate.source_id,
            )
        if (
            raw_candidate.sheet_name != source.sheet_name
            or raw_candidate.sheet_position != source.sheet_position
            or raw_candidate.coordinate != source.coordinate
        ):
            return _invalid(
                request,
                AIMappingIssueCode.SOURCE_POSITION_MISMATCH,
                "AI 후보의 Sheet 또는 Cell 위치가 요청 증거와 일치하지 않습니다.",
                source_id=source.source_id,
            )
        if (
            raw_candidate.source_value_kind != source.value_kind.value
            or type(raw_candidate.source_token) is not type(source.value)
            or raw_candidate.source_token != source.value
        ):
            return _invalid(
                request,
                AIMappingIssueCode.SOURCE_TOKEN_MISMATCH,
                "AI 후보의 원본 텍스트가 요청 증거와 정확히 일치하지 않습니다.",
                source_id=source.source_id,
            )
        exact_scan_matches = scan_cells.get(
            (source.sheet_name, source.sheet_position, source.coordinate),
            (),
        )
        if len(exact_scan_matches) != 1 or _safe_source_value(exact_scan_matches[0]) != (
            source.value,
            source.value_kind,
            source.trust,
        ):
            return _invalid(
                request,
                AIMappingIssueCode.SOURCE_TOKEN_MISMATCH,
                "AI 후보의 원본 텍스트를 Scanner 증거에서 정확히 재확인할 수 없습니다.",
                source_id=source.source_id,
            )
        candidates.append(
            AIMappingCandidate(
                role=AIMappingCandidateRole(raw_candidate.role),
                source=source,
                confidence=AIMappingConfidence(raw_candidate.confidence),
                reason_ko=raw_candidate.reason_ko,
            )
        )
    return AIMappingOutcome(
        state=AIMappingOutcomeState.REVIEW_REQUIRED,
        request=request,
        candidates=tuple(candidates),
        issues=(),
        unresolved=unresolved,
        warnings=warnings,
    )


def _scan_cell_index(
    scan: WorkbookScan,
) -> dict[tuple[str, int, str], tuple[CellEvidence, ...]]:
    grouped: dict[tuple[str, int, str], list[CellEvidence]] = defaultdict(list)
    for sheet in scan.sheets:
        for cell in sheet.cells:
            grouped[(sheet.name, sheet.position, cell.coordinate)].append(cell)
    return {key: tuple(cells) for key, cells in grouped.items()}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey(key)
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> object:
    raise _NonFiniteJSONValue(value)


def _contains_forbidden_action(value: object, *, parent_key: str | None = None) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = key.strip().casefold()
            if normalized_key in _FORBIDDEN_ACTION_KEYS:
                return True
            if _contains_forbidden_action(child, parent_key=normalized_key):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_action(child, parent_key=parent_key) for child in value)
    if isinstance(value, str) and parent_key == "reason_ko":
        return any(pattern.search(value) is not None for pattern in _FORBIDDEN_ACTION_TEXT_PATTERNS)
    return False


def _contains_sensitive_text(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _SENSITIVE_TEXT_PATTERNS)


def _contains_prompt_injection(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _PROMPT_INJECTION_PATTERNS)


def _prompt_injection_issue() -> AIMappingIssue:
    return AIMappingIssue(
        code=AIMappingIssueCode.PROMPT_INJECTION_DETECTED,
        message_ko="명령처럼 보이는 Cell 텍스트를 발견해 AI 전송을 보류합니다.",
    )


def _safe_source_value(
    cell: CellEvidence,
) -> tuple[str | int | float | bool, AISourceValueKind, UntrustedDataTag] | None:
    if cell.formula_text is not None:
        return None
    value = cell.stored_value
    if isinstance(value, str):
        if not value.strip():
            return None
        return value, AISourceValueKind.TEXT, UntrustedDataTag.CELL_TEXT
    if isinstance(value, bool):
        return value, AISourceValueKind.BOOLEAN, UntrustedDataTag.CELL_SCALAR
    if isinstance(value, int):
        return value, AISourceValueKind.INTEGER, UntrustedDataTag.CELL_SCALAR
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value, AISourceValueKind.NUMBER, UntrustedDataTag.CELL_SCALAR
    if isinstance(value, datetime):
        return value.isoformat(), AISourceValueKind.DATETIME, UntrustedDataTag.CELL_SCALAR
    if isinstance(value, date):
        return value.isoformat(), AISourceValueKind.DATE, UntrustedDataTag.CELL_SCALAR
    if isinstance(value, Decimal) and value.is_finite():
        return format(value, "f"), AISourceValueKind.DECIMAL, UntrustedDataTag.CELL_SCALAR
    return None


def _coordinate_row(coordinate: str) -> int:
    match = _COORDINATE_PARTS_PATTERN.fullmatch(coordinate)
    if match is None:  # protected by scan preflight
        raise AssertionError("preflight accepted an invalid coordinate")
    return int(match.group(2))


def _coordinate_sort_key(coordinate: str) -> tuple[int, str]:
    match = _COORDINATE_PARTS_PATTERN.fullmatch(coordinate)
    if match is None:  # protected by scan preflight
        raise AssertionError("preflight accepted an invalid coordinate")
    return int(match.group(2)), match.group(1)


def _coordinate_is_canonical(coordinate: str) -> bool:
    match = _COORDINATE_PARTS_PATTERN.fullmatch(coordinate)
    if match is None:
        return False
    column_number = 0
    for character in match.group(1):
        column_number = column_number * 26 + (ord(character) - ord("A") + 1)
    return column_number <= 16_384 and int(match.group(2)) <= 1_048_576


def _unavailable(
    request: AIMappingRequest,
    code: AIMappingIssueCode,
    message_ko: str,
) -> AIMappingOutcome:
    return AIMappingOutcome(
        state=AIMappingOutcomeState.AI_UNAVAILABLE,
        request=request,
        candidates=(),
        issues=(AIMappingIssue(code=code, message_ko=message_ko),),
    )


def _invalid(
    request: AIMappingRequest,
    code: AIMappingIssueCode,
    message_ko: str,
    *,
    source_id: str | None = None,
) -> AIMappingOutcome:
    return AIMappingOutcome(
        state=AIMappingOutcomeState.AI_RESPONSE_INVALID,
        request=request,
        candidates=(),
        issues=(AIMappingIssue(code=code, message_ko=message_ko, source_id=source_id),),
    )
