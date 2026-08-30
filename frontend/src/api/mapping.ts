export interface TaggedSourceValue {
  kind: string;
  value: string | number | boolean | null;
  python_type: string;
}

export interface MappingSourceCell {
  sheet_name: string;
  sheet_position: number;
  coordinate: string;
  raw_value: TaggedSourceValue;
  cached_value: TaggedSourceValue;
  formula_text: string | null;
  number_format: string;
  data_type: string;
  display_value: string | null;
  display_value_status: string;
}

export interface MappedCell {
  sheet_name: string;
  coordinate: string;
  raw_value: TaggedSourceValue;
  cached_value: TaggedSourceValue;
  formula_text: string | null;
  number_format: string;
  data_type: string;
  display_value: string | null;
  display_value_status: string;
  value_kind: string;
}

export interface MappingIssue {
  code: string;
  message: string;
  template_id: string | null;
  template_revision: number | null;
  sheet_name: string | null;
  coordinate: string | null;
  expected: string | null;
  observed: string | null;
}

export interface ScanIssue {
  code: string;
  severity: string;
  message: string;
  location: string | null;
}

export interface MappingSheet {
  name: string;
  kind: string;
  position: number;
  visibility: string;
  used_range: string | null;
  estimated_cells: number;
  merged_ranges: string[];
  hidden_row_ranges: Array<{ start: number; end: number }>;
  hidden_column_ranges: Array<{ start: number; end: number }>;
  protected: boolean;
  protected_actions: string[];
  formula_count: number;
  issue_codes: string[];
}

export interface MappingTemplateProof {
  history_id: string;
  revision_id: string;
  template_id: string;
  schema_version: string;
  revision: number;
  status: string;
  payload_sha256: string;
  effective_from: string;
  effective_to: string | null;
  approved_by: string;
  approved_at: string;
  history_row_version: number;
  revision_row_version: number;
}

export interface MappingInspectionRow {
  row_key: string;
  item: MappedCell;
  method: MappedCell | null;
  instrument: MappedCell | null;
  specification: MappedCell | null;
  tolerance: MappedCell | null;
  minimum: MappedCell | null;
  maximum: MappedCell | null;
  section: MappedCell | null;
  category: MappedCell | null;
  unit: MappedCell | null;
  measurement_point: MappedCell | null;
  measurement_location: MappedCell | null;
  cavity: MappedCell | null;
  target: MappedCell | null;
  lsl: MappedCell | null;
  usl: MappedCell | null;
  source_spec_revision: MappedCell | null;
  samples: MappedCell[];
  supplier_result: MappedCell | null;
}

export interface MappingWorkspaceSnapshot {
  state: "PREVIEW_READY" | "MAPPING_REQUIRED";
  mode: "APPROVED_TEMPLATE" | "MANUAL_SOURCE_REVIEW";
  status_label: string;
  message: string;
  supplier_scope: string;
  ai_state: "NOT_CALLED";
  draft_command_available: false;
  long_confirmation_available: false;
  official_values_created: false;
  calculations_performed: false;
  receipt: {
    receipt_id: string;
    content_sha256: string;
    original_filename: string;
    received_at: string;
    size_bytes: number;
    model_candidates: string[];
    lot_candidates: string[];
  };
  scan: {
    source_size_bytes: number;
    sha256_before: string;
    sha256_after: string;
    sheet_count: number;
    estimated_cells: number;
    external_link_count: number;
    macro_handling: string;
    sheets: MappingSheet[];
    issues: ScanIssue[];
  };
  source_cells: {
    offset: number;
    limit: number;
    total: number;
    truncated: boolean;
    cells: MappingSourceCell[];
  };
  issues: MappingIssue[];
  template: MappingTemplateProof | null;
  preview: {
    source_inspection_date: string;
    identifiers: Array<{ kind: string; evidence: MappedCell }>;
    inspection_rows: MappingInspectionRow[];
  } | null;
}

export interface MappingPreviewInput {
  projectKey: string;
  receiptId: string;
  contentSha256: string;
  supplierScope: string;
  cellOffset?: number;
  cellLimit?: number;
}

export interface MappingCellReference {
  sheet_name: string;
  coordinate: string;
}

export type MappingIdentifierKind =
  | "MODEL"
  | "PART_NUMBER"
  | "LOT_NUMBER"
  | "SUPPLIER"
  | "INSPECTION_DATE"
  | "REPORT_NUMBER"
  | "REVISION"
  | "PART_NAME"
  | "PRODUCTION_DATE"
  | "CURRENT_SHIPMENT_QUANTITY"
  | "SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY";

export interface MappingIdentifierDraft {
  kind: MappingIdentifierKind;
  source: MappingCellReference;
}

export interface MappingInspectionRowDraft {
  row_key: string;
  item: MappingCellReference;
  method?: MappingCellReference;
  instrument?: MappingCellReference;
  specification?: MappingCellReference;
  tolerance?: MappingCellReference;
  minimum?: MappingCellReference;
  maximum?: MappingCellReference;
  sample_cells: MappingCellReference[];
  supplier_result?: MappingCellReference;
  section?: MappingCellReference;
  category?: MappingCellReference;
  unit?: MappingCellReference;
  measurement_point?: MappingCellReference;
  measurement_location?: MappingCellReference;
  cavity?: MappingCellReference;
  target?: MappingCellReference;
  lsl?: MappingCellReference;
  usl?: MappingCellReference;
  source_spec_revision?: MappingCellReference;
}

export interface CreateMappingDraftRequest {
  project_key: string;
  receipt_id: string;
  content_sha256: string;
  supplier_scope: string;
  effective_from: string;
  effective_to: string | null;
  expected_history_row_version: 0;
  reason: string;
  header_assertion_cells: MappingCellReference[];
  identifiers: MappingIdentifierDraft[];
  inspection_rows: MappingInspectionRowDraft[];
}

export interface MappingWorkflowCommandRequest {
  project_key: string;
  receipt_id: string;
  content_sha256: string;
  supplier_scope: string;
  expected_history_row_version: number;
  expected_revision_row_version: number;
  reason: string;
}

export interface MappingWorkflowSnapshot {
  workflow: {
    template_id: string;
    schema_version: "2";
    revision: 1;
    status: "DRAFT" | "REVIEWED" | "APPROVED";
    project_key: string;
    supplier_scope: string;
    effective_from: string;
    effective_to: string | null;
    history_id: string;
    revision_id: string;
    history_row_version: number;
    revision_row_version: number;
    reviewed_by: string | null;
    reviewed_at: string | null;
    approved_by: string | null;
    approved_at: string | null;
    capabilities: {
      can_review: boolean;
      can_approve: boolean;
      additional_revisions_supported: false;
    };
  };
  proof: {
    receipt_id: string;
    content_sha256: string;
    original_filename: string;
    size_bytes: number;
    fingerprint_sha256: string;
    header_assertion_count: number;
    identifier_count: number;
    inspection_row_count: number;
    mapped_cell_count: number;
    official_values_created: false;
    calculations_performed: false;
  };
  preview: {
    state: "PREVIEW_READY";
    source_inspection_date: string;
    identifier_count: number;
    inspection_row_count: number;
    system_judgment_status: "NOT_EVALUATED";
    official_values_created: false;
    calculations_performed: false;
  } | null;
}

export interface MappingApi {
  getPreview(input: MappingPreviewInput, signal?: AbortSignal): Promise<MappingWorkspaceSnapshot>;
  createDraft(
    request: CreateMappingDraftRequest,
    signal?: AbortSignal,
  ): Promise<MappingWorkflowSnapshot>;
  review(
    templateId: string,
    revision: number,
    request: MappingWorkflowCommandRequest,
    signal?: AbortSignal,
  ): Promise<MappingWorkflowSnapshot>;
  approve(
    templateId: string,
    revision: number,
    request: MappingWorkflowCommandRequest,
    signal?: AbortSignal,
  ): Promise<MappingWorkflowSnapshot>;
}

const SAFE_FALLBACK_MESSAGE = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

export class MappingApiError extends Error {
  readonly code: string;
  readonly statusLabel: string;

  constructor(message: string, code = "MAPPING_REQUEST_FAILED", statusLabel = "매핑 검토 오류") {
    super(message);
    this.name = "MappingApiError";
    this.code = code;
    this.statusLabel = statusLabel;
  }
}

export const mappingApi: MappingApi = {
  async getPreview(input, signal) {
    const query = new URLSearchParams({
      project_key: input.projectKey,
      content_sha256: input.contentSha256,
      supplier_scope: input.supplierScope,
      cell_offset: String(input.cellOffset ?? 0),
      cell_limit: String(input.cellLimit ?? 120),
    });
    return requestSnapshot(
      `/api/v1/mapping/receipts/${encodeURIComponent(input.receiptId)}/preview?${query.toString()}`,
      signal,
    );
  },

  async createDraft(request, signal) {
    return requestJson("/api/v1/mapping/templates/drafts", request, signal);
  },

  async review(templateId, revision, request, signal) {
    return requestJson(
      `/api/v1/mapping/templates/${encodeURIComponent(templateId)}/revisions/${revision}/review`,
      request,
      signal,
    );
  },

  async approve(templateId, revision, request, signal) {
    return requestJson(
      `/api/v1/mapping/templates/${encodeURIComponent(templateId)}/revisions/${revision}/approve`,
      request,
      signal,
    );
  },
};

async function requestSnapshot(input: string, signal?: AbortSignal): Promise<MappingWorkspaceSnapshot> {
  let response: Response;
  try {
    response = await fetch(input, { method: "GET", headers: { Accept: "application/json" }, signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new MappingApiError(SAFE_FALLBACK_MESSAGE);
  }
  if (!response.ok) throw await safeApiError(response);
  try {
    return (await response.json()) as MappingWorkspaceSnapshot;
  } catch {
    throw new MappingApiError(SAFE_FALLBACK_MESSAGE);
  }
}

async function requestJson(
  input: string,
  body: CreateMappingDraftRequest | MappingWorkflowCommandRequest,
  signal?: AbortSignal,
): Promise<MappingWorkflowSnapshot> {
  let response: Response;
  try {
    response = await fetch(input, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new MappingApiError(SAFE_FALLBACK_MESSAGE);
  }
  if (!response.ok) throw await safeApiError(response);
  try {
    return (await response.json()) as MappingWorkflowSnapshot;
  } catch {
    throw new MappingApiError(SAFE_FALLBACK_MESSAGE);
  }
}

async function safeApiError(response: Response): Promise<MappingApiError> {
  if (response.status < 400 || response.status >= 500) {
    return new MappingApiError(SAFE_FALLBACK_MESSAGE);
  }
  try {
    const payload: unknown = await response.json();
    if (!isObject(payload) || !isObject(payload.detail)) {
      return new MappingApiError(SAFE_FALLBACK_MESSAGE);
    }
    const { code, message, status_label: statusLabel } = payload.detail;
    if (typeof code !== "string" || typeof message !== "string" || typeof statusLabel !== "string") {
      return new MappingApiError(SAFE_FALLBACK_MESSAGE);
    }
    return new MappingApiError(message, code, statusLabel);
  } catch {
    return new MappingApiError(SAFE_FALLBACK_MESSAGE);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
