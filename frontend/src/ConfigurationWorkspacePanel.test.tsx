import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  CanonicalInspectionItemResource,
  CanonicalModelPartResource,
  CanonicalModelResource,
  CanonicalSupplierResource,
  ConfigurationApi,
  ConfigurationSnapshot,
  MasterSpecResource,
  RowBindingResource,
} from "./api/configuration";
import { ConfigurationWorkspacePanel } from "./ConfigurationWorkspacePanel";

const model: CanonicalModelResource = {
  project_key: "PROJECT-A",
  model_key: "MODEL-A",
  display_name: "모델 A",
  row_version: 1,
};

const supplier: CanonicalSupplierResource = {
  project_key: "PROJECT-A",
  supplier_key: "SUPPLIER-A",
  display_name: "업체 A",
  row_version: 1,
};

const part: CanonicalModelPartResource = {
  project_key: "PROJECT-A",
  model_key: "MODEL-A",
  model_part_key: "PART-A",
  display_name: "부품 A",
  row_version: 1,
};

const candidateItem: CanonicalInspectionItemResource = {
  project_key: "PROJECT-A",
  model_part_key: "PART-A",
  item_key: "LENGTH",
  display_name: "길이",
  disposition: "CANDIDATE",
  row_version: 1,
};

const managedItem: CanonicalInspectionItemResource = {
  ...candidateItem,
  disposition: "MANAGED",
  row_version: 2,
};

function master(status: MasterSpecResource["status"], version: number): MasterSpecResource {
  return {
    project_key: "PROJECT-A",
    canonical_item_key: "LENGTH",
    revision: 1,
    status,
    target: "1.00",
    lsl: "0.90",
    usl: "1.10",
    unit: "mm",
    external_spec_revision: "SPEC-REV-A",
    declared_effective_from: "2026-08-15",
    declared_effective_to: null,
    resolved_effective_to: null,
    change_reason: "승인 기준 원문 대조",
    source_reference: "SPEC-DOC-001",
    reviewed_by: status === "DRAFT" ? null : "local-owner",
    reviewed_at: status === "DRAFT" ? null : "2026-08-15T10:00:00Z",
    approved_by: status === "APPROVED" ? "local-owner" : null,
    approved_at: status === "APPROVED" ? "2026-08-15T10:05:00Z" : null,
    history_id: "master-history-001",
    revision_id: "master-revision-001",
    payload_sha256: "a".repeat(64),
    history_row_version: version,
    revision_row_version: version,
  };
}

function binding(status: RowBindingResource["status"], version: number): RowBindingResource {
  return {
    project_key: "PROJECT-A",
    supplier_scope: "supplier-alpha",
    template_id: "template-001",
    template_revision: 3,
    row_key: "OQC!ROW:4",
    binding_revision: 1,
    status,
    source_model_values: ["MODEL-A RAW"],
    canonical_model_key: "MODEL-A",
    canonical_supplier_key: "SUPPLIER-A",
    canonical_model_part_key: "PART-A",
    canonical_item_key: "LENGTH",
    measurement_mode: "NUMERIC",
    sample_policy: "AT_LEAST_ONE",
    declared_effective_from: "2026-08-15",
    declared_effective_to: null,
    resolved_effective_to: null,
    change_reason: "승인 Mapping 행 대조",
    source_reference: "mapping-template:PROJECT-A:supplier-alpha:template-001:3:OQC!ROW:4",
    reviewed_by: status === "DRAFT" ? null : "local-owner",
    reviewed_at: status === "DRAFT" ? null : "2026-08-15T11:00:00Z",
    approved_by: status === "APPROVED" ? "local-owner" : null,
    approved_at: status === "APPROVED" ? "2026-08-15T11:05:00Z" : null,
    history_id: "binding-history-001",
    revision_id: "binding-revision-001",
    payload_sha256: "b".repeat(64),
    history_row_version: version,
    revision_row_version: version,
  };
}

const snapshot: ConfigurationSnapshot = {
  project_key: "PROJECT-A",
  models: [],
  suppliers: [],
  model_parts: [],
  inspection_items: [],
  master_specs: [],
  row_bindings: [],
  approved_mapping_revisions: [
    {
      project_key: "PROJECT-A",
      supplier_scope: "supplier-alpha",
      template_id: "template-001",
      revision: 3,
      schema_version: "2",
      status: "APPROVED",
      history_id: "mapping-history-001",
      revision_id: "mapping-revision-003",
      payload_sha256: "c".repeat(64),
      history_row_version: 4,
      revision_row_version: 2,
      declared_effective_from: "2026-01-01",
      declared_effective_to: null,
      resolved_effective_to: null,
      supplier_source_aliases: ["Supplier Alpha"],
      model_source: { sheet_name: "OQC", coordinate: "B2" },
      rows: [
        {
          row_key: "OQC!ROW:4",
          sheet_name: "OQC",
          row_index: 4,
          item_source: { sheet_name: "OQC", coordinate: "A4" },
          method_source: { sheet_name: "OQC", coordinate: "D4" },
          instrument_source: null,
          specification_source: { sheet_name: "OQC", coordinate: "E4" },
          tolerance_source: null,
          minimum_source: null,
          maximum_source: null,
          sample_cells: [{ sheet_name: "OQC", coordinate: "B4" }],
          supplier_result_source: { sheet_name: "OQC", coordinate: "C4" },
          section_source: null,
          category_source: null,
          unit_source: { sheet_name: "OQC", coordinate: "F4" },
          measurement_point_source: null,
          measurement_location_source: null,
          cavity_source: null,
          target_source: null,
          lsl_source: null,
          usl_source: null,
          source_spec_revision_source: null,
        },
      ],
    },
  ],
  capabilities: {
    first_master_revision_only: true,
    first_binding_revision_only: true,
    later_revisions_supported: false,
    supersession_supported: false,
    actor_source: "TRUSTED_LOCAL_OWNER",
  },
  official_values_created: false,
  auto_effects: false,
  ai_used: false,
};

function createApi(): ConfigurationApi {
  return {
    getSnapshot: vi.fn().mockResolvedValue(snapshot),
    createModel: vi.fn().mockResolvedValue(model),
    createSupplier: vi.fn().mockResolvedValue(supplier),
    createModelPart: vi.fn().mockResolvedValue(part),
    createInspectionItem: vi.fn().mockResolvedValue(candidateItem),
    setItemDisposition: vi.fn().mockResolvedValue(managedItem),
    createMasterDraft: vi.fn().mockResolvedValue(master("DRAFT", 1)),
    reviewMaster: vi.fn().mockResolvedValue(master("REVIEWED", 2)),
    approveMaster: vi.fn().mockResolvedValue(master("APPROVED", 3)),
    createBindingDraft: vi.fn().mockResolvedValue(binding("DRAFT", 1)),
    reviewBinding: vi.fn().mockResolvedValue(binding("REVIEWED", 2)),
    approveBinding: vi.fn().mockResolvedValue(binding("APPROVED", 3)),
  };
}

describe("canonical configuration first setup", () => {
  it("DQ-P1-CFGUI-001: canonical 생성부터 Master와 Mapping row Binding의 분리 승인까지 exact CAS로 수행한다", async () => {
    const api = createApi();
    const user = userEvent.setup();
    render(<ConfigurationWorkspacePanel projectKey="PROJECT-A" api={api} />);

    await user.click(screen.getByRole("button", { name: "설정 현황 불러오기" }));
    await waitFor(() => expect(api.getSnapshot).toHaveBeenCalledWith("PROJECT-A", expect.any(AbortSignal)));
    expect(await screen.findByText(/후속 revision·supersede 미지원/)).toBeVisible();

    await user.type(screen.getByLabelText("Model key"), "MODEL-A");
    await user.type(screen.getByLabelText("Model 표시명"), "모델 A");
    await user.type(screen.getByLabelText("Model 생성 이유"), "프로젝트 기준 모델 등록");
    await user.click(screen.getByRole("button", { name: "Model 저장" }));
    await waitFor(() => expect(api.createModel).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.createModel).mock.calls[0]?.[0]).toEqual({
      project_key: "PROJECT-A", model_key: "MODEL-A", display_name: "모델 A", reason: "프로젝트 기준 모델 등록",
    });

    await user.type(screen.getByLabelText("Supplier key"), "SUPPLIER-A");
    await user.type(screen.getByLabelText("Supplier 표시명"), "업체 A");
    await user.type(screen.getByLabelText("Supplier 생성 이유"), "독립 공급업체 등록");
    await user.click(screen.getByRole("button", { name: "Supplier 저장" }));
    await waitFor(() => expect(api.createSupplier).toHaveBeenCalledTimes(1));

    await screen.findByRole("option", { name: "모델 A (MODEL-A)" });
    await user.selectOptions(screen.getByLabelText("상위 Model"), "MODEL-A");
    await user.type(screen.getByLabelText("ModelPart key"), "PART-A");
    await user.type(screen.getByLabelText("ModelPart 표시명"), "부품 A");
    await user.type(screen.getByLabelText("ModelPart 생성 이유"), "모델 하위 부품 등록");
    await user.click(screen.getByRole("button", { name: "ModelPart 저장" }));
    await waitFor(() => expect(api.createModelPart).toHaveBeenCalledTimes(1));

    await screen.findByRole("option", { name: "부품 A (PART-A)" });
    await user.selectOptions(screen.getByLabelText("상위 ModelPart"), "PART-A");
    await user.type(screen.getByLabelText("InspectionItem key"), "LENGTH");
    await user.type(screen.getByLabelText("InspectionItem 표시명"), "길이");
    await user.type(screen.getByLabelText("InspectionItem 생성 이유"), "원본 길이 항목 등록");
    await user.click(screen.getByRole("button", { name: "InspectionItem 저장" }));
    await waitFor(() => expect(api.createInspectionItem).toHaveBeenCalledTimes(1));
    expect((await screen.findAllByText(/CANDIDATE로 등록했습니다/)).length).toBeGreaterThan(0);

    await user.selectOptions(screen.getByLabelText("CANDIDATE 항목"), "LENGTH");
    expect(screen.getByText(/서버 snapshot CAS/)).toHaveTextContent("row_version 1");
    await user.click(screen.getByRole("radio", { name: /MANAGED/ }));
    await user.type(screen.getByLabelText("처리방향 결정 이유"), "숫자 Master 관리 항목으로 검토");
    await user.click(screen.getByRole("button", { name: "처리방향 결정" }));
    await waitFor(() => expect(api.setItemDisposition).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.setItemDisposition).mock.calls[0]?.[0]).toEqual({
      project_key: "PROJECT-A", item_key: "LENGTH", disposition: "MANAGED", expected_row_version: 1, reason: "숫자 Master 관리 항목으로 검토",
    });

    await screen.findByRole("option", { name: /길이 \(LENGTH\) · MANAGED/ });
    await user.selectOptions(screen.getByLabelText("MANAGED 항목"), "LENGTH");
    await user.type(screen.getByLabelText("Target Decimal (선택)"), "1.00");
    await user.type(screen.getByLabelText("LSL Decimal (둘 중 하나 필수)"), "0.90");
    await user.type(screen.getByLabelText("USL Decimal (둘 중 하나 필수)"), "1.10");
    await user.type(screen.getByLabelText("단위"), "mm");
    await user.type(screen.getByLabelText("외부 Spec revision"), "SPEC-REV-A");
    fireEvent.change(screen.getByLabelText("적용 시작일", { selector: "#config-master-from" }), { target: { value: "2026-08-15" } });
    await user.type(screen.getByLabelText("Master 출처 참조"), "SPEC-DOC-001");
    await user.type(screen.getByLabelText("Master Draft 생성 이유"), "승인 기준 원문 대조");
    await user.click(screen.getByRole("button", { name: "Master Draft 생성" }));
    await waitFor(() => expect(api.createMasterDraft).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.createMasterDraft).mock.calls[0]?.[0]).toEqual({
      project_key: "PROJECT-A", canonical_item_key: "LENGTH", target: "1.00", lsl: "0.90", usl: "1.10", unit: "mm", external_spec_revision: "SPEC-REV-A", effective_from: "2026-08-15", effective_to: null, source_reference: "SPEC-DOC-001", expected_history_row_version: 0, reason: "승인 기준 원문 대조",
    });

    await user.type(await screen.findByLabelText("Master 검토 완료 이유"), "Reviewer 단계 독립 확인");
    expect(api.approveMaster).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Master 검토 완료 (REVIEWER)" }));
    await waitFor(() => expect(api.reviewMaster).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.reviewMaster).mock.calls[0]?.[0]).toEqual({
      project_key: "PROJECT-A", canonical_item_key: "LENGTH", revision: 1, expected_history_row_version: 1, expected_revision_row_version: 1, reason: "Reviewer 단계 독립 확인",
    });
    await user.type(await screen.findByLabelText("Master 최종 승인 이유"), "Admin 단계 별도 승인");
    await user.click(screen.getByRole("button", { name: "Master 최종 승인 (ADMIN)" }));
    await waitFor(() => expect(api.approveMaster).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.approveMaster).mock.calls[0]?.[0]).toEqual(expect.objectContaining({ expected_history_row_version: 2, expected_revision_row_version: 2, reason: "Admin 단계 별도 승인" }));

    await user.selectOptions(screen.getByLabelText("승인 Mapping revision"), "0");
    expect(screen.getByText("OQC!B2")).toBeVisible();
    expect(screen.getByText("cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc")).toBeVisible();
    await user.selectOptions(screen.getByLabelText("exact Mapping row_key"), "OQC!ROW:4");
    expect(screen.getAllByText("OQC!A4").length).toBeGreaterThan(0);
    expect(screen.getByText("OQC!F4")).toBeVisible();
    await user.type(screen.getByLabelText("원본 MODEL exact 문자열 (한 줄에 하나)"), "MODEL-A RAW");
    await user.selectOptions(screen.getByLabelText("Canonical Model"), "MODEL-A");
    await user.selectOptions(screen.getByLabelText("Canonical Supplier"), "SUPPLIER-A");
    await user.selectOptions(screen.getByLabelText("Canonical ModelPart"), "PART-A");
    await user.selectOptions(screen.getByLabelText("결정된 InspectionItem"), "LENGTH");
    await user.selectOptions(screen.getByLabelText("측정 모드"), "NUMERIC");
    await user.selectOptions(screen.getByLabelText("샘플 정책"), "AT_LEAST_ONE");
    fireEvent.change(screen.getByLabelText("적용 시작일", { selector: "#config-binding-from" }), { target: { value: "2026-08-15" } });
    await user.type(screen.getByLabelText("Row Binding Draft 생성 이유"), "승인 Mapping 행 대조");
    await user.click(screen.getByRole("button", { name: "Row Binding Draft 생성" }));
    await waitFor(() => expect(api.createBindingDraft).toHaveBeenCalledTimes(1));
    const bindingDraftRequest = vi.mocked(api.createBindingDraft).mock.calls[0]?.[0];
    expect(bindingDraftRequest).toEqual({
      project_key: "PROJECT-A", supplier_scope: "supplier-alpha", template_id: "template-001", template_revision: 3, row_key: "OQC!ROW:4", source_model_values: ["MODEL-A RAW"], canonical_model_key: "MODEL-A", canonical_supplier_key: "SUPPLIER-A", canonical_model_part_key: "PART-A", canonical_item_key: "LENGTH", measurement_mode: "NUMERIC", sample_policy: "AT_LEAST_ONE", effective_from: "2026-08-15", effective_to: null, expected_history_row_version: 0, reason: "승인 Mapping 행 대조",
    });
    expect(JSON.stringify(bindingDraftRequest)).not.toMatch(/actor|roles|history_id|revision_id|ai|valid/i);

    await user.type(await screen.findByLabelText("Row Binding 검토 완료 이유"), "Binding Reviewer 독립 확인");
    expect(api.approveBinding).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Row Binding 검토 완료 (REVIEWER)" }));
    await waitFor(() => expect(api.reviewBinding).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.reviewBinding).mock.calls[0]?.[0]).toEqual(expect.objectContaining({ template_revision: 3, binding_revision: 1, expected_history_row_version: 1, expected_revision_row_version: 1 }));
    await user.type(await screen.findByLabelText("Row Binding 최종 승인 이유"), "Binding Admin 별도 승인");
    await user.click(screen.getByRole("button", { name: "Row Binding 최종 승인 (ADMIN)" }));
    await waitFor(() => expect(api.approveBinding).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.approveBinding).mock.calls[0]?.[0]).toEqual(expect.objectContaining({ expected_history_row_version: 2, expected_revision_row_version: 2 }));
    await waitFor(() => expect(screen.getAllByText(/Long 후보 만들기를 다시 실행하세요/).length).toBeGreaterThan(0));
  }, 20_000);
});
