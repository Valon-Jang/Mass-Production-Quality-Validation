export type ConfigurationRevisionStatus = "DRAFT" | "REVIEWED" | "APPROVED";
export type ItemDisposition = "CANDIDATE" | "MANAGED" | "EXCLUDED";
export type DecidedItemDisposition = Exclude<ItemDisposition, "CANDIDATE">;
export type MeasurementMode = "NUMERIC" | "QUALITATIVE" | "JUDGMENT_ONLY";
export type SamplePolicy = "AT_LEAST_ONE" | "ZERO_ALLOWED";

export interface CanonicalModelResource {
  project_key: string;
  model_key: string;
  display_name: string;
  row_version: number;
}

export interface CanonicalSupplierResource {
  project_key: string;
  supplier_key: string;
  display_name: string;
  row_version: number;
}

export interface CanonicalModelPartResource {
  project_key: string;
  model_key: string;
  model_part_key: string;
  display_name: string;
  row_version: number;
}

export interface CanonicalInspectionItemResource {
  project_key: string;
  model_part_key: string;
  item_key: string;
  display_name: string;
  disposition: ItemDisposition;
  row_version: number;
}

export interface MasterSpecResource {
  project_key: string;
  canonical_item_key: string;
  revision: number;
  status: ConfigurationRevisionStatus;
  target: string | null;
  lsl: string | null;
  usl: string | null;
  unit: string;
  external_spec_revision: string;
  declared_effective_from: string;
  declared_effective_to: string | null;
  resolved_effective_to: string | null;
  change_reason: string;
  source_reference: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  history_id: string;
  revision_id: string;
  payload_sha256: string;
  history_row_version: number;
  revision_row_version: number;
}

export interface RowBindingResource {
  project_key: string;
  supplier_scope: string;
  template_id: string;
  template_revision: number;
  row_key: string;
  binding_revision: number;
  status: ConfigurationRevisionStatus;
  source_model_values: string[];
  canonical_model_key: string;
  canonical_supplier_key: string;
  canonical_model_part_key: string;
  canonical_item_key: string;
  measurement_mode: MeasurementMode;
  sample_policy: SamplePolicy;
  declared_effective_from: string;
  declared_effective_to: string | null;
  resolved_effective_to: string | null;
  change_reason: string;
  source_reference: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  history_id: string;
  revision_id: string;
  payload_sha256: string;
  history_row_version: number;
  revision_row_version: number;
}

export interface ConfigurationCellSource {
  sheet_name: string;
  coordinate: string;
}

export interface MappingRowSelection {
  row_key: string;
  sheet_name: string;
  row_index: number;
  item_source: ConfigurationCellSource;
  method_source: ConfigurationCellSource | null;
  instrument_source: ConfigurationCellSource | null;
  specification_source: ConfigurationCellSource | null;
  tolerance_source: ConfigurationCellSource | null;
  minimum_source: ConfigurationCellSource | null;
  maximum_source: ConfigurationCellSource | null;
  sample_cells: ConfigurationCellSource[];
  supplier_result_source: ConfigurationCellSource | null;
  section_source: ConfigurationCellSource | null;
  category_source: ConfigurationCellSource | null;
  unit_source: ConfigurationCellSource | null;
  measurement_point_source: ConfigurationCellSource | null;
  measurement_location_source: ConfigurationCellSource | null;
  cavity_source: ConfigurationCellSource | null;
  target_source: ConfigurationCellSource | null;
  lsl_source: ConfigurationCellSource | null;
  usl_source: ConfigurationCellSource | null;
  source_spec_revision_source: ConfigurationCellSource | null;
}

export interface ApprovedMappingSelection {
  project_key: string;
  supplier_scope: string;
  template_id: string;
  revision: number;
  schema_version: "2";
  status: "APPROVED";
  history_id: string;
  revision_id: string;
  payload_sha256: string;
  history_row_version: number;
  revision_row_version: number;
  declared_effective_from: string;
  declared_effective_to: string | null;
  resolved_effective_to: string | null;
  supplier_source_aliases: string[];
  model_source: ConfigurationCellSource | null;
  rows: MappingRowSelection[];
}

export interface ConfigurationSnapshot {
  project_key: string;
  models: CanonicalModelResource[];
  suppliers: CanonicalSupplierResource[];
  model_parts: CanonicalModelPartResource[];
  inspection_items: CanonicalInspectionItemResource[];
  master_specs: MasterSpecResource[];
  row_bindings: RowBindingResource[];
  approved_mapping_revisions: ApprovedMappingSelection[];
  capabilities: {
    first_master_revision_only: true;
    first_binding_revision_only: true;
    later_revisions_supported: false;
    supersession_supported: false;
    actor_source: "TRUSTED_LOCAL_OWNER";
  };
  official_values_created: false;
  auto_effects: false;
  ai_used: false;
}

export interface CreateModelRequest {
  project_key: string;
  model_key: string;
  display_name: string;
  reason: string;
}

export interface CreateSupplierRequest {
  project_key: string;
  supplier_key: string;
  display_name: string;
  reason: string;
}

export interface CreateModelPartRequest {
  project_key: string;
  model_key: string;
  model_part_key: string;
  display_name: string;
  reason: string;
}

export interface CreateInspectionItemRequest {
  project_key: string;
  model_part_key: string;
  item_key: string;
  display_name: string;
  reason: string;
}

export interface SetItemDispositionRequest {
  project_key: string;
  item_key: string;
  disposition: DecidedItemDisposition;
  expected_row_version: number;
  reason: string;
}

export interface CreateMasterSpecDraftRequest {
  project_key: string;
  canonical_item_key: string;
  target: string | null;
  lsl: string | null;
  usl: string | null;
  unit: string;
  external_spec_revision: string;
  effective_from: string;
  effective_to: string | null;
  source_reference: string;
  expected_history_row_version: 0;
  reason: string;
}

export interface MasterSpecWorkflowRequest {
  project_key: string;
  canonical_item_key: string;
  revision: 1;
  expected_history_row_version: number;
  expected_revision_row_version: number;
  reason: string;
}

export interface CreateRowBindingDraftRequest {
  project_key: string;
  supplier_scope: string;
  template_id: string;
  template_revision: number;
  row_key: string;
  source_model_values: string[];
  canonical_model_key: string;
  canonical_supplier_key: string;
  canonical_model_part_key: string;
  canonical_item_key: string;
  measurement_mode: MeasurementMode;
  sample_policy: SamplePolicy;
  effective_from: string;
  effective_to: string | null;
  expected_history_row_version: 0;
  reason: string;
}

export interface RowBindingWorkflowRequest {
  project_key: string;
  supplier_scope: string;
  template_id: string;
  template_revision: number;
  row_key: string;
  binding_revision: 1;
  expected_history_row_version: number;
  expected_revision_row_version: number;
  reason: string;
}

export interface ConfigurationApi {
  getSnapshot(projectKey: string, signal?: AbortSignal): Promise<ConfigurationSnapshot>;
  createModel(request: CreateModelRequest, signal?: AbortSignal): Promise<CanonicalModelResource>;
  createSupplier(
    request: CreateSupplierRequest,
    signal?: AbortSignal,
  ): Promise<CanonicalSupplierResource>;
  createModelPart(
    request: CreateModelPartRequest,
    signal?: AbortSignal,
  ): Promise<CanonicalModelPartResource>;
  createInspectionItem(
    request: CreateInspectionItemRequest,
    signal?: AbortSignal,
  ): Promise<CanonicalInspectionItemResource>;
  setItemDisposition(
    request: SetItemDispositionRequest,
    signal?: AbortSignal,
  ): Promise<CanonicalInspectionItemResource>;
  createMasterDraft(
    request: CreateMasterSpecDraftRequest,
    signal?: AbortSignal,
  ): Promise<MasterSpecResource>;
  reviewMaster(
    request: MasterSpecWorkflowRequest,
    signal?: AbortSignal,
  ): Promise<MasterSpecResource>;
  approveMaster(
    request: MasterSpecWorkflowRequest,
    signal?: AbortSignal,
  ): Promise<MasterSpecResource>;
  createBindingDraft(
    request: CreateRowBindingDraftRequest,
    signal?: AbortSignal,
  ): Promise<RowBindingResource>;
  reviewBinding(
    request: RowBindingWorkflowRequest,
    signal?: AbortSignal,
  ): Promise<RowBindingResource>;
  approveBinding(
    request: RowBindingWorkflowRequest,
    signal?: AbortSignal,
  ): Promise<RowBindingResource>;
}

const PREFIX = "/api/v1/configuration";
const SAFE_FALLBACK_MESSAGE = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

export class ConfigurationApiError extends Error {
  readonly code: string;
  readonly statusLabel: string;

  constructor(
    message: string,
    code = "CONFIGURATION_REQUEST_FAILED",
    statusLabel = "설정 처리 오류",
  ) {
    super(message);
    this.name = "ConfigurationApiError";
    this.code = code;
    this.statusLabel = statusLabel;
  }
}

export const configurationApi: ConfigurationApi = {
  async getSnapshot(projectKey, signal) {
    return getJson<ConfigurationSnapshot>(
      `${PREFIX}/snapshot?project_key=${encodeURIComponent(projectKey)}`,
      signal,
    );
  },
  async createModel(request, signal) {
    return postJson<CanonicalModelResource>(`${PREFIX}/models`, request, signal);
  },
  async createSupplier(request, signal) {
    return postJson<CanonicalSupplierResource>(`${PREFIX}/suppliers`, request, signal);
  },
  async createModelPart(request, signal) {
    return postJson<CanonicalModelPartResource>(`${PREFIX}/model-parts`, request, signal);
  },
  async createInspectionItem(request, signal) {
    return postJson<CanonicalInspectionItemResource>(`${PREFIX}/inspection-items`, request, signal);
  },
  async setItemDisposition(request, signal) {
    return postJson<CanonicalInspectionItemResource>(
      `${PREFIX}/inspection-items/dispositions`,
      request,
      signal,
    );
  },
  async createMasterDraft(request, signal) {
    return postJson<MasterSpecResource>(`${PREFIX}/master-specs/drafts`, request, signal);
  },
  async reviewMaster(request, signal) {
    return postJson<MasterSpecResource>(`${PREFIX}/master-specs/reviews`, request, signal);
  },
  async approveMaster(request, signal) {
    return postJson<MasterSpecResource>(`${PREFIX}/master-specs/approvals`, request, signal);
  },
  async createBindingDraft(request, signal) {
    return postJson<RowBindingResource>(`${PREFIX}/row-bindings/drafts`, request, signal);
  },
  async reviewBinding(request, signal) {
    return postJson<RowBindingResource>(`${PREFIX}/row-bindings/reviews`, request, signal);
  },
  async approveBinding(request, signal) {
    return postJson<RowBindingResource>(`${PREFIX}/row-bindings/approvals`, request, signal);
  },
};

async function getJson<T>(input: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(input, { headers: { Accept: "application/json" }, signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ConfigurationApiError(SAFE_FALLBACK_MESSAGE);
  }
  return parseResponse<T>(response);
}

async function postJson<T>(input: string, body: object, signal?: AbortSignal): Promise<T> {
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
    throw new ConfigurationApiError(SAFE_FALLBACK_MESSAGE);
  }
  return parseResponse<T>(response);
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) throw await safeApiError(response);
  try {
    return (await response.json()) as T;
  } catch {
    throw new ConfigurationApiError(SAFE_FALLBACK_MESSAGE);
  }
}

async function safeApiError(response: Response): Promise<ConfigurationApiError> {
  try {
    const payload: unknown = await response.json();
    if (!isObject(payload) || !isObject(payload.detail)) {
      return new ConfigurationApiError(SAFE_FALLBACK_MESSAGE);
    }
    const { code, message, status_label: statusLabel } = payload.detail;
    if (typeof code !== "string" || typeof message !== "string" || typeof statusLabel !== "string") {
      return new ConfigurationApiError(SAFE_FALLBACK_MESSAGE);
    }
    return new ConfigurationApiError(message, code, statusLabel);
  } catch {
    return new ConfigurationApiError(SAFE_FALLBACK_MESSAGE);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
