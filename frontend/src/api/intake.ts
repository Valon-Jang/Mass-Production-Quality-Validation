export type IntakeJobStatus =
  | "QUEUED"
  | "PROCESSING"
  | "MAPPING_REQUIRED"
  | "RAW_PRESERVED_SCAN_FAILED"
  | "ERROR";

export interface IntakeIssue {
  code: string;
  message: string;
  location: string | null;
}

export interface IntakeReceipt {
  receipt_id: string;
  content_sha256: string;
  original_filename: string;
  received_at: string;
  size_bytes: number;
  model_candidates: string[];
  lot_candidates: string[];
}

export interface IntakeSheet {
  name: string;
  kind: string;
  state: string;
  used_range: string | null;
  merged_ranges: string[];
  protected: boolean;
  issue_codes: string[];
}

export interface IntakeScan {
  source_size_bytes: number;
  sha256_before: string;
  sha256_after: string;
  sheet_count: number;
  sheets: IntakeSheet[];
}

export interface IntakeJobSnapshot {
  job_id: string;
  project_key: string;
  status: IntakeJobStatus;
  status_label: string;
  message: string;
  created_at: string;
  updated_at: string;
  terminal: boolean;
  poll_after_ms: number | null;
  receipt: IntakeReceipt | null;
  scan: IntakeScan | null;
  issues: IntakeIssue[];
}

export interface CreateIntakeJobInput {
  projectKey: string;
  workbook: File;
  modelHint?: string;
  lotHint?: string;
}

export interface IntakeApi {
  createJob(input: CreateIntakeJobInput, signal?: AbortSignal): Promise<IntakeJobSnapshot>;
  getJob(
    jobId: string,
    projectKey: string,
    signal?: AbortSignal,
  ): Promise<IntakeJobSnapshot>;
}

const SAFE_FALLBACK_MESSAGE = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

export class IntakeApiError extends Error {
  readonly code: string;
  readonly statusLabel: string;

  constructor(message: string, code = "INTAKE_REQUEST_FAILED", statusLabel = "요청 오류") {
    super(message);
    this.name = "IntakeApiError";
    this.code = code;
    this.statusLabel = statusLabel;
  }
}

export const intakeApi: IntakeApi = {
  async createJob(input, signal) {
    const body = new FormData();
    body.append("project_key", input.projectKey);
    body.append("workbook", input.workbook, input.workbook.name);
    if (input.modelHint) {
      body.append("model_hint", input.modelHint);
    }
    if (input.lotHint) {
      body.append("lot_hint", input.lotHint);
    }

    return requestSnapshot(
      "/api/v1/intake/jobs",
      { method: "POST", body, signal },
    );
  },

  async getJob(jobId, projectKey, signal) {
    const query = new URLSearchParams({ project_key: projectKey });
    return requestSnapshot(
      `/api/v1/intake/jobs/${encodeURIComponent(jobId)}?${query.toString()}`,
      { method: "GET", signal },
    );
  },
};

async function requestSnapshot(
  input: string,
  init: RequestInit,
): Promise<IntakeJobSnapshot> {
  let response: Response;
  try {
    response = await fetch(input, {
      ...init,
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new IntakeApiError(SAFE_FALLBACK_MESSAGE);
  }

  if (!response.ok) {
    throw await safeApiError(response);
  }

  try {
    return (await response.json()) as IntakeJobSnapshot;
  } catch {
    throw new IntakeApiError(SAFE_FALLBACK_MESSAGE);
  }
}

async function safeApiError(response: Response): Promise<IntakeApiError> {
  if (response.status < 400 || response.status >= 500) {
    return new IntakeApiError(SAFE_FALLBACK_MESSAGE);
  }

  try {
    const payload: unknown = await response.json();
    if (!isObject(payload) || !isObject(payload.detail)) {
      return new IntakeApiError(SAFE_FALLBACK_MESSAGE);
    }
    const { code, message, status_label: statusLabel } = payload.detail;
    if (
      typeof code !== "string" ||
      typeof message !== "string" ||
      typeof statusLabel !== "string" ||
      !code ||
      !message ||
      !statusLabel
    ) {
      return new IntakeApiError(SAFE_FALLBACK_MESSAGE);
    }
    return new IntakeApiError(message, code, statusLabel);
  } catch {
    return new IntakeApiError(SAFE_FALLBACK_MESSAGE);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
