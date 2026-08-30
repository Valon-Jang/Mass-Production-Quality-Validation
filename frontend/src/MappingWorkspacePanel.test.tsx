import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  DataReviewApi,
  DataReviewCandidate,
  DataReviewDecisionResponse,
  DataReviewMasterEvidence,
  DataReviewTargetsResponse,
} from "./api/dataReview";
import type { IntakeReceipt } from "./api/intake";
import type { LongApi, LongOperationResponse } from "./api/long";
import {
  MappingApiError,
  type MappingApi,
  type MappingWorkflowSnapshot,
  type MappingWorkspaceSnapshot,
} from "./api/mapping";
import { MappingWorkspacePanel } from "./MappingWorkspacePanel";

const receipt: IntakeReceipt = {
  receipt_id: "receipt-001",
  content_sha256: "a".repeat(64),
  original_filename: "oqc.xlsx",
  received_at: "2026-08-15T09:00:00+09:00",
  size_bytes: 2048,
  model_candidates: [],
  lot_candidates: [],
};

function tagged(value: string) {
  return { kind: "TEXT", value, python_type: "str" };
}

const cells = [
  ["A1", "OQC Report"],
  ["A2", "2026-08-15"],
  ["B2", "Supplier Alpha"],
  ["A4", "Length"],
  ["B4", "1.25"],
].map(([coordinate, value]) => ({
  sheet_name: "OQC",
  sheet_position: 0,
  coordinate,
  raw_value: tagged(value),
  cached_value: { kind: "NULL", value: null, python_type: "NoneType" },
  formula_text: null,
  number_format: "General",
  data_type: "s",
  display_value: null,
  display_value_status: "NOT_RENDERED",
}));

const manualSnapshot: MappingWorkspaceSnapshot = {
  state: "MAPPING_REQUIRED",
  mode: "MANUAL_SOURCE_REVIEW",
  status_label: "수동 매핑 검토 필요",
  message: "정확한 원본 셀을 지정해 주세요.",
  supplier_scope: "supplier-alpha",
  ai_state: "NOT_CALLED",
  draft_command_available: false,
  long_confirmation_available: false,
  official_values_created: false,
  calculations_performed: false,
  receipt,
  scan: {
    source_size_bytes: receipt.size_bytes,
    sha256_before: receipt.content_sha256,
    sha256_after: receipt.content_sha256,
    sheet_count: 1,
    estimated_cells: 12,
    external_link_count: 0,
    macro_handling: "NOT_APPLICABLE",
    sheets: [],
    issues: [],
  },
  source_cells: { offset: 0, limit: 120, total: cells.length, truncated: false, cells },
  issues: [],
  template: null,
  preview: null,
};

const approvedSnapshot: MappingWorkspaceSnapshot = {
  ...manualSnapshot,
  state: "PREVIEW_READY",
  mode: "APPROVED_TEMPLATE",
  status_label: "승인된 매핑 미리보기 준비",
  message: "승인된 동일 양식과 일치합니다.",
  issues: [],
  template: {
    history_id: "history-001",
    revision_id: "revision-001",
    template_id: "server-template-001",
    schema_version: "2",
    revision: 1,
    status: "APPROVED",
    payload_sha256: "b".repeat(64),
    effective_from: "2026-08-15",
    effective_to: null,
    approved_by: "local-admin",
    approved_at: "2026-08-15T10:05:00Z",
    history_row_version: 3,
    revision_row_version: 3,
  },
  preview: {
    source_inspection_date: "2026-08-15",
    identifiers: [],
    inspection_rows: [],
  },
};

const bindingIssue = {
  code: "CANONICAL_ROW_BINDING_MISSING",
  scope: "ROW",
  message: "승인된 Canonical Row Binding이 없습니다.",
  row_key: "OQC!ROW:5",
  sheet_name: "OQC",
  coordinate: "A5",
  expected: null,
  observed: null,
};

const longCandidateResponse: LongOperationResponse = {
  candidate: {
    state: "PARTIAL_HOLD",
    status_label: "일부 행 보류",
    message: "적재 가능한 행과 보류된 행을 함께 확인해 주세요.",
    candidate_digest: "c".repeat(64),
    project_key: "PROJECT-A",
    supplier_scope: "supplier-alpha",
    receipt: {
      receipt_id: receipt.receipt_id,
      content_sha256: receipt.content_sha256,
      original_filename: receipt.original_filename,
      size_bytes: receipt.size_bytes,
    },
    mapping: {
      history_id: "history-001",
      revision_id: "revision-001",
      payload_sha256: "b".repeat(64),
      template_id: "server-template-001",
      schema_version: "2",
      revision: 1,
      approved_by: "local-admin",
      approved_at: "2026-08-15T10:05:00Z",
      effective_from: "2026-08-15",
      effective_to: null,
      source_inspection_date: "2026-08-15",
    },
    binding_catalog_revision: "sha256:catalog-001",
    row_count: 2,
    loadable_row_count: 1,
    held_row_count: 1,
    identifiers: [],
    rows: [
      {
        row_key: "OQC!ROW:4",
        state: "LOADABLE_PENDING",
        status_label: "PENDING 적재 가능",
        pending_data_status: "PENDING",
        source: { sheet_name: "OQC", coordinate: "A4", raw_value: tagged("Length") },
        measurement_count: 2,
        measurement_cells: [
          { sheet_name: "OQC", coordinate: "B4" },
          { sheet_name: "OQC", coordinate: "C4" },
        ],
        binding: {
          binding_revision: 1,
          canonical_model_key: "MODEL-A",
          canonical_supplier_key: "SUPPLIER-A",
          canonical_model_part_key: "PART-A",
          canonical_item_key: "LENGTH",
          measurement_mode: "NUMERIC",
          sample_policy: "AT_LEAST_ONE",
          approved_by: "local-admin",
          approved_at: "2026-08-15T09:00:00Z",
          effective_from: "2026-01-01",
          effective_to: null,
        },
        issues: [],
      },
      {
        row_key: "OQC!ROW:5",
        state: "ROW_HELD",
        status_label: "Binding 검토 보류",
        pending_data_status: "HELD",
        source: { sheet_name: "OQC", coordinate: "A5", raw_value: tagged("Appearance") },
        measurement_count: 1,
        measurement_cells: [{ sheet_name: "OQC", coordinate: "B5" }],
        binding: null,
        issues: [bindingIssue],
      },
    ],
    issues: [bindingIssue],
    capabilities: {
      can_confirm: true,
      confirm_requires_digest: true,
      auto_binding: false,
      idempotency_managed_by_server: true,
    },
    official_values_created: false,
    calculations_performed: false,
    auto_valid: false,
    ai_called: false,
  },
  persistence: null,
};

const longConfirmedResponse: LongOperationResponse = {
  ...longCandidateResponse,
  persistence: {
    source_file_id: "source-file-001",
    ingestion_job_id: "long-job-001",
    status: "PARTIAL_HELD",
    status_label: "PENDING 저장 · 일부 HELD",
    row_version: 2,
    replayed: true,
    reused_job_id: "long-job-001",
    blocking_job_id: null,
    counts: { lot_count: 1, result_count: 2, measurement_count: 3, held_result_count: 1 },
    pending_only: true,
    official_values_created: false,
    calculations_performed: false,
    auto_valid: false,
  },
};

const reviewMaster: DataReviewMasterEvidence = {
  project_key: "PROJECT-A",
  canonical_item_key: "LENGTH",
  history_id: "master-history-001",
  revision_id: "master-revision-002",
  revision_number: 2,
  history_row_version: 4,
  revision_row_version: 3,
  payload_sha256: "d".repeat(64),
  declared_effective_from: "2026-01-01",
  declared_effective_to: null,
  resolved_effective_to: null,
  target: "1.00",
  lsl: "0.90",
  usl: "1.10",
  unit: "mm",
  external_spec_revision: "SPEC-REV-B",
};

const dataReviewTargets: DataReviewTargetsResponse = {
  project_key: "PROJECT-A",
  ingestion_job_id: "long-job-001",
  job_status: "PARTIAL_HELD",
  targets: [
    {
      result_id: "result-pending-001",
      source_row_key: "OQC!ROW:4",
      data_status: "PENDING",
      row_version: 1,
      canonical_item_key: "LENGTH",
      lot_id: "lot-001",
      lot_ordinal: 1,
      source_lot_text: "LOT-A",
      inspection_date: "2026-08-15",
      reviewable: true,
      status_label: "검토 대기",
    },
    {
      result_id: "result-held-001",
      source_row_key: "OQC!ROW:5",
      data_status: "HELD",
      row_version: 1,
      canonical_item_key: null,
      lot_id: "lot-001",
      lot_ordinal: 1,
      source_lot_text: "LOT-A",
      inspection_date: "2026-08-15",
      reviewable: false,
      status_label: "보류 · 결정 불가",
    },
  ],
  official_values_created: false,
};

const evaluatedFailCandidate: DataReviewCandidate = {
  state: "EVALUATED",
  status_label: "규격 근거 평가 완료",
  message: "승인 Master와 exact 원본 샘플을 비교했습니다.",
  candidate_sha256: "e".repeat(64),
  project_key: "PROJECT-A",
  result: {
    id: "result-pending-001",
    source_file_id: "source-file-001",
    lot_id: "lot-001",
    source_content_sha256: receipt.content_sha256,
    inspection_date: "2026-08-15",
    data_status: "PENDING",
    current_system_judgment: null,
    current_system_judgment_status: "NOT_EVALUATED",
    current_spec_evaluation_status: "NOT_EVALUATED",
    source_evidence_sha256: "f".repeat(64),
    binding_snapshot_sha256: "1".repeat(64),
    candidate_snapshot_sha256: "2".repeat(64),
  },
  item: {
    canonical_item_key: "LENGTH",
    disposition: "MANAGED",
    measurement_mode: "NUMERIC",
  },
  source_unit: {
    sheet_name: "OQC",
    coordinate: "D4",
    raw_value: "mm",
    cell_evidence_sha256: "3".repeat(64),
  },
  master_candidates: [reviewMaster],
  selected_master: reviewMaster,
  samples: [
    {
      measurement_id: "measurement-001",
      sample_ordinal: 1,
      source_cell: "B4",
      row_version: 1,
      evidence_sha256: "4".repeat(64),
      raw_value_json: "1.25",
      raw_numeric_value_json: "1.25",
      raw_qualitative_value: null,
      formula_flag: false,
      numeric_value: "1.25",
      comparison: "ABOVE_USL",
    },
  ],
  issues: [],
  proposed_system_judgment: "FAIL",
  proposed_system_judgment_status: "EVALUATED",
  proposed_spec_evaluation_status: "EVALUATED_APPROVED_MASTER",
  allowed_target_statuses: ["EXCLUDED", "SUSPECT", "VALID"],
  cas: {
    expected_result_row_version: 1,
    expected_item_row_version: 2,
    expected_measurement_versions: [
      { sample_ordinal: 1, measurement_id: "measurement-001", row_version: 1 },
    ],
    expected_master: {
      history_id: reviewMaster.history_id,
      revision_id: reviewMaster.revision_id,
      history_row_version: reviewMaster.history_row_version,
      revision_row_version: reviewMaster.revision_row_version,
      payload_sha256: reviewMaster.payload_sha256,
    },
  },
  capabilities: {
    can_decide: true,
    explicit_confirmation_required: true,
    trusted_local_admin: true,
  },
  official_values_created: false,
  unit_conversion_performed: false,
  ai_used: false,
  statistics_calculated: false,
};

const heldCandidate: DataReviewCandidate = {
  ...evaluatedFailCandidate,
  state: "INELIGIBLE",
  status_label: "결정 부적격",
  message: "HELD 결과는 전이하지 않습니다.",
  candidate_sha256: "5".repeat(64),
  result: {
    ...evaluatedFailCandidate.result,
    id: "result-held-001",
    data_status: "HELD",
    binding_snapshot_sha256: null,
  },
  item: { canonical_item_key: null, disposition: null, measurement_mode: null },
  source_unit: null,
  master_candidates: [],
  selected_master: null,
  samples: evaluatedFailCandidate.samples.map((sample) => ({
    ...sample,
    numeric_value: null,
    comparison: "NOT_EVALUATED" as const,
  })),
  issues: [{ code: "RESULT_HELD", message: "HELD 결과는 데이터상태를 전이하지 않습니다." }],
  proposed_system_judgment: null,
  proposed_system_judgment_status: "NOT_EVALUATED",
  proposed_spec_evaluation_status: "NOT_EVALUATED",
  allowed_target_statuses: [],
  cas: {
    ...evaluatedFailCandidate.cas,
    expected_item_row_version: null,
    expected_master: null,
  },
  capabilities: {
    can_decide: false,
    explicit_confirmation_required: true,
    trusted_local_admin: true,
  },
};

const dataReviewDecision: DataReviewDecisionResponse = {
  decision: {
    transition_id: "transition-001",
    project_key: "PROJECT-A",
    result_id: "result-pending-001",
    candidate_sha256: evaluatedFailCandidate.candidate_sha256,
    intent_sha256: "6".repeat(64),
    target_status: "VALID",
    result_row_version: 2,
    measurement_count: 1,
    evaluation_mode: "EVALUATED",
    system_judgment: "FAIL",
    master: reviewMaster,
    replayed: true,
    auto_decision: false,
    ai_used: false,
    additional_calculation: false,
  },
};

function workflow(
  status: "DRAFT" | "REVIEWED" | "APPROVED",
  historyVersion: number,
  revisionVersion: number,
): MappingWorkflowSnapshot {
  return {
    workflow: {
      template_id: "server-template-001",
      schema_version: "2",
      revision: 1,
      status,
      project_key: "PROJECT-A",
      supplier_scope: "supplier-alpha",
      effective_from: "2026-08-15",
      effective_to: null,
      history_id: "history-001",
      revision_id: "revision-001",
      history_row_version: historyVersion,
      revision_row_version: revisionVersion,
      reviewed_by: status === "DRAFT" ? null : "local-reviewer",
      reviewed_at: status === "DRAFT" ? null : "2026-08-15T10:00:00Z",
      approved_by: status === "APPROVED" ? "local-admin" : null,
      approved_at: status === "APPROVED" ? "2026-08-15T10:05:00Z" : null,
      capabilities: {
        can_review: status === "DRAFT",
        can_approve: status === "REVIEWED",
        additional_revisions_supported: false,
      },
    },
    proof: {
      receipt_id: receipt.receipt_id,
      content_sha256: receipt.content_sha256,
      original_filename: receipt.original_filename,
      size_bytes: receipt.size_bytes,
      fingerprint_sha256: "b".repeat(64),
      header_assertion_count: 1,
      identifier_count: 2,
      inspection_row_count: 1,
      mapped_cell_count: 5,
      official_values_created: false,
      calculations_performed: false,
    },
    preview:
      status === "APPROVED"
        ? {
            state: "PREVIEW_READY",
            source_inspection_date: "2026-08-15",
            identifier_count: 2,
            inspection_row_count: 1,
            system_judgment_status: "NOT_EVALUATED",
            official_values_created: false,
            calculations_performed: false,
          }
        : null,
  };
}

function createApi(overrides: Partial<MappingApi> = {}): MappingApi {
  return {
    getPreview: vi.fn().mockResolvedValue(manualSnapshot),
    createDraft: vi.fn().mockResolvedValue(workflow("DRAFT", 1, 1)),
    review: vi.fn().mockResolvedValue(workflow("REVIEWED", 2, 2)),
    approve: vi.fn().mockResolvedValue(workflow("APPROVED", 3, 3)),
    ...overrides,
  };
}

function createLongApi(overrides: Partial<LongApi> = {}): LongApi {
  return {
    createCandidate: vi.fn().mockResolvedValue(longCandidateResponse),
    confirm: vi.fn().mockResolvedValue(longConfirmedResponse),
    ...overrides,
  };
}

function createDataReviewApi(overrides: Partial<DataReviewApi> = {}): DataReviewApi {
  return {
    getTargets: vi.fn().mockResolvedValue(dataReviewTargets),
    createCandidate: vi
      .fn()
      .mockResolvedValueOnce({ candidate: evaluatedFailCandidate })
      .mockResolvedValueOnce({ candidate: heldCandidate }),
    decide: vi.fn().mockResolvedValue(dataReviewDecision),
    ...overrides,
  };
}

async function openAndSelectMinimum(api: MappingApi) {
  const user = userEvent.setup();
  render(<MappingWorkspacePanel projectKey="PROJECT-A" receipt={receipt} api={api} />);
  await user.type(screen.getByLabelText("업체 범위"), " supplier-alpha ");
  await user.click(screen.getByRole("button", { name: "원본 셀 검토 시작" }));
  expect(await screen.findByText("수동 매핑 검토 필요")).toBeVisible();
  await user.selectOptions(screen.getByLabelText("OQC!A1 역할"), "HEADER");
  await user.selectOptions(
    screen.getByLabelText("OQC!A2 역할"),
    "IDENTIFIER:INSPECTION_DATE",
  );
  await user.selectOptions(screen.getByLabelText("OQC!B2 역할"), "IDENTIFIER:SUPPLIER");
  await user.selectOptions(screen.getByLabelText("OQC!A4 역할"), "ROW:item");
  await user.selectOptions(screen.getByLabelText("OQC!B4 역할"), "ROW:sample");
  fireEvent.change(screen.getByLabelText("적용 시작일"), { target: { value: "2026-08-15" } });
  await user.type(screen.getByLabelText("Draft 생성 이유"), "원본 셀을 직접 대조했습니다.");
  return user;
}

describe("수동 Mapping workflow", () => {
  it("exact 셀로 Draft를 만들고 REVIEWER 검토와 ADMIN 승인을 별도 CAS 명령으로 실행한다", async () => {
    const api = createApi();
    const user = await openAndSelectMinimum(api);

    await user.click(screen.getByRole("button", { name: "매핑 Draft 생성" }));
    await waitFor(() => expect(api.createDraft).toHaveBeenCalledTimes(1));
    const draftRequest = vi.mocked(api.createDraft).mock.calls[0]?.[0];
    expect(draftRequest).toEqual({
      project_key: "PROJECT-A",
      receipt_id: receipt.receipt_id,
      content_sha256: receipt.content_sha256,
      supplier_scope: "supplier-alpha",
      effective_from: "2026-08-15",
      effective_to: null,
      expected_history_row_version: 0,
      reason: "원본 셀을 직접 대조했습니다.",
      header_assertion_cells: [{ sheet_name: "OQC", coordinate: "A1" }],
      identifiers: [
        { kind: "INSPECTION_DATE", source: { sheet_name: "OQC", coordinate: "A2" } },
        { kind: "SUPPLIER", source: { sheet_name: "OQC", coordinate: "B2" } },
      ],
      inspection_rows: [
        {
          row_key: "OQC!ROW:4",
          item: { sheet_name: "OQC", coordinate: "A4" },
          sample_cells: [{ sheet_name: "OQC", coordinate: "B4" }],
        },
      ],
    });
    expect(JSON.stringify(draftRequest)).not.toMatch(/actor|roles|template_id|schema_version/);
    expect(screen.getByText("Draft 생성됨")).toBeVisible();
    expect(screen.getByText(/후속 revision 작성은 현재 화면에서 지원하지 않습니다/)).toBeVisible();

    await user.type(screen.getByLabelText("검토 완료 이유"), "독립 검토를 마쳤습니다.");
    await user.click(screen.getByRole("button", { name: "검토 완료 (REVIEWER)" }));
    await waitFor(() => expect(api.review).toHaveBeenCalledTimes(1));
    expect(api.review).toHaveBeenCalledWith(
      "server-template-001",
      1,
      expect.objectContaining({
        expected_history_row_version: 1,
        expected_revision_row_version: 1,
        reason: "독립 검토를 마쳤습니다.",
      }),
      expect.any(AbortSignal),
    );

    await user.type(screen.getByLabelText("최종 승인 이유"), "관리자 최종 확인을 마쳤습니다.");
    await user.click(screen.getByRole("button", { name: "최종 승인 (ADMIN)" }));
    await waitFor(() => expect(api.approve).toHaveBeenCalledTimes(1));
    expect(api.approve).toHaveBeenCalledWith(
      "server-template-001",
      1,
      expect.objectContaining({
        expected_history_row_version: 2,
        expected_revision_row_version: 2,
        reason: "관리자 최종 확인을 마쳤습니다.",
      }),
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("최종 승인됨")).toBeVisible();
    expect(screen.getByText(/공식값이나 Long 데이터는 생성하지 않았습니다/)).toBeVisible();
  }, 15_000);

  it("CAS 충돌은 서버의 안전한 코드와 재조회 안내만 표시한다", async () => {
    const api = createApi({
      createDraft: vi.fn().mockRejectedValue(
        new MappingApiError(
          "다른 변경이 먼저 저장되었습니다.",
          "STALE_MAPPING_TEMPLATE_WRITE",
          "버전 충돌",
        ),
      ),
    });
    const user = await openAndSelectMinimum(api);
    await user.click(screen.getByRole("button", { name: "매핑 Draft 생성" }));

    expect(await screen.findByText("동시 수정 충돌")).toBeVisible();
    expect(screen.getByText(/최신 row_version을 확인하려면 원본 셀 검토를 다시 시작/)).toBeVisible();
    expect(screen.getByText("STALE_MAPPING_TEMPLATE_WRITE")).toBeVisible();
  });

  it("승인된 Mapping에서 후보와 digest 확인을 분리하고 PENDING·HELD 멱등 결과를 표시한다", async () => {
    const mappingApi = createApi({ getPreview: vi.fn().mockResolvedValue(approvedSnapshot) });
    const longApi = createLongApi();
    const user = userEvent.setup();
    render(
      <MappingWorkspacePanel
        projectKey="PROJECT-A"
        receipt={receipt}
        api={mappingApi}
        longApi={longApi}
      />,
    );
    await user.type(screen.getByLabelText("업체 범위"), "supplier-alpha");
    await user.click(screen.getByRole("button", { name: "원본 셀 검토 시작" }));
    await user.click(await screen.findByRole("button", { name: "Long 후보 만들기" }));

    await waitFor(() => expect(longApi.createCandidate).toHaveBeenCalledTimes(1));
    expect(longApi.createCandidate).toHaveBeenCalledWith(
      {
        project_key: "PROJECT-A",
        receipt_id: receipt.receipt_id,
        content_sha256: receipt.content_sha256,
        supplier_scope: "supplier-alpha",
      },
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("일부 행 보류")).toBeVisible();
    expect(screen.getByText("Binding 미확정 · 자동 연결 안 함")).toBeVisible();
    expect(screen.getAllByText("CANONICAL_ROW_BINDING_MISSING").length).toBeGreaterThan(0);
    expect(screen.getByText("2개")).toBeVisible();
    expect(screen.getByText("VALID 전환 없음")).toBeVisible();

    const confirmButton = screen.getByRole("button", { name: "PENDING/HELD 저장 확인" });
    expect(confirmButton).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /후보 digest와 행별 PENDING\/HELD/ }));
    expect(confirmButton).toBeEnabled();
    await user.click(confirmButton);

    await waitFor(() => expect(longApi.confirm).toHaveBeenCalledTimes(1));
    const confirmation = vi.mocked(longApi.confirm).mock.calls[0]?.[0];
    expect(confirmation).toEqual({
      project_key: "PROJECT-A",
      receipt_id: receipt.receipt_id,
      content_sha256: receipt.content_sha256,
      supplier_scope: "supplier-alpha",
      candidate_digest: "c".repeat(64),
      confirmed: true,
    });
    expect(JSON.stringify(confirmation)).not.toMatch(/actor|role|loader|scan_contract|idempotency|valid/i);
    expect(await screen.findByText(/기존 결과를 멱등 재사용했습니다/)).toBeVisible();
    expect(screen.getByText("PENDING/HELD 전용 저장")).toBeVisible();
    expect(screen.getByText("long-job-001")).toBeVisible();
  });

  it("DQ-P1-DSTATUI-001: FAIL 규격판정과 VALID 데이터상태를 분리하고 HELD 결정은 차단한다", async () => {
    const mappingApi = createApi({ getPreview: vi.fn().mockResolvedValue(approvedSnapshot) });
    const longApi = createLongApi();
    const reviewApi = createDataReviewApi();
    const user = userEvent.setup();
    render(
      <MappingWorkspacePanel
        projectKey="PROJECT-A"
        receipt={receipt}
        api={mappingApi}
        longApi={longApi}
        dataReviewApi={reviewApi}
      />,
    );

    await user.type(screen.getByLabelText("업체 범위"), "supplier-alpha");
    await user.click(screen.getByRole("button", { name: "원본 셀 검토 시작" }));
    await user.click(await screen.findByRole("button", { name: "Long 후보 만들기" }));
    await user.click(await screen.findByRole("checkbox", { name: /후보 digest와 행별 PENDING\/HELD/ }));
    await user.click(screen.getByRole("button", { name: "PENDING/HELD 저장 확인" }));

    await user.click(await screen.findByRole("button", { name: "데이터상태 검토 대상 불러오기" }));
    await waitFor(() => expect(reviewApi.getTargets).toHaveBeenCalledTimes(1));
    expect(reviewApi.getTargets).toHaveBeenCalledWith(
      { project_key: "PROJECT-A", ingestion_job_id: "long-job-001" },
      expect.any(AbortSignal),
    );
    expect(await screen.findByRole("table", { name: "데이터상태 검토 대상 표" })).toBeVisible();
    expect(screen.getByText("PENDING 검토 대상")).toBeVisible();
    expect(screen.getByText("결정 불가 상태")).toBeVisible();

    await user.click(screen.getAllByRole("button", { name: "근거 검토" })[0]!);
    await waitFor(() => expect(reviewApi.createCandidate).toHaveBeenCalledTimes(1));
    expect(reviewApi.createCandidate).toHaveBeenCalledWith(
      { project_key: "PROJECT-A", result_id: "result-pending-001" },
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("규격 근거 평가 완료")).toBeVisible();
    expect(screen.getByText(/규격판정은 FAIL이지만 데이터 근거가 유효/)).toBeVisible();
    expect(screen.getByText("SPEC-REV-B")).toBeVisible();
    expect(screen.getByText("OQC!D4")).toBeVisible();
    expect(screen.getByText("상한 초과")).toBeVisible();
    expect(screen.getAllByText("1.25").length).toBeGreaterThan(0);

    const validOption = screen.getByRole("radio", { name: /VALID/ });
    expect(validOption).toBeEnabled();
    await user.click(validOption);
    await user.type(screen.getByLabelText("데이터상태 결정 이유"), "원본·단위·샘플과 승인 Master를 확인했습니다.");
    await user.click(screen.getByRole("checkbox", { name: /Candidate SHA-256/ }));
    await user.click(screen.getByRole("button", { name: "선택한 데이터상태로 결정" }));

    await waitFor(() => expect(reviewApi.decide).toHaveBeenCalledTimes(1));
    const request = vi.mocked(reviewApi.decide).mock.calls[0]?.[0];
    expect(request).toEqual({
      project_key: "PROJECT-A",
      result_id: "result-pending-001",
      target_status: "VALID",
      candidate_sha256: evaluatedFailCandidate.candidate_sha256,
      cas: evaluatedFailCandidate.cas,
      reason: "원본·단위·샘플과 승인 Master를 확인했습니다.",
      confirmed: true,
    });
    expect(Object.keys(request ?? {}).sort()).toEqual([
      "candidate_sha256",
      "cas",
      "confirmed",
      "project_key",
      "reason",
      "result_id",
      "target_status",
    ]);
    expect(await screen.findByText(/기존 결과를 서버가 멱등 재사용했습니다/)).toBeVisible();
    expect(screen.getAllByText("VALID").length).toBeGreaterThan(0);
    expect(screen.getAllByText("FAIL").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "근거 검토" }));
    await waitFor(() => expect(reviewApi.createCandidate).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("결정 부적격")).toBeVisible();
    expect(screen.getByText(/HELD 또는 부적격 결과는 데이터상태 결정을 실행할 수 없으며/)).toBeVisible();
    expect(screen.getByRole("radio", { name: /VALID/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "선택한 데이터상태로 결정" })).toBeDisabled();
    expect(reviewApi.decide).toHaveBeenCalledTimes(1);
  });
});
