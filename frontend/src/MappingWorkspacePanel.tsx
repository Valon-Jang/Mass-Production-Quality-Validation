import { type FormEvent, useEffect, useRef, useState } from "react";

import type { ConfigurationApi } from "./api/configuration";
import {
  dataReviewApi as defaultDataReviewApi,
  DataReviewApiError,
  type DataReviewApi,
  type DataReviewCandidate,
  type DataReviewDecision,
  type DataReviewMasterEvidence,
  type DataReviewTarget,
  type DataReviewTargetsResponse,
  type DataReviewTargetStatus,
} from "./api/dataReview";
import type { IntakeReceipt } from "./api/intake";
import {
  longApi as defaultLongApi,
  LongApiError,
  type LongApi,
  type LongCandidateIssue,
  type LongCandidateSnapshot,
  type LongOperationResponse,
} from "./api/long";
import {
  mappingApi as defaultMappingApi,
  MappingApiError,
  type CreateMappingDraftRequest,
  type MappedCell,
  type MappingApi,
  type MappingCellReference,
  type MappingIdentifierDraft,
  type MappingIdentifierKind,
  type MappingInspectionRow,
  type MappingInspectionRowDraft,
  type MappingWorkflowCommandRequest,
  type MappingWorkflowSnapshot,
  type MappingWorkspaceSnapshot,
  type TaggedSourceValue,
} from "./api/mapping";
import { BulkWorkspacePanel } from "./BulkWorkspacePanel";
import { ConfigurationWorkspacePanel } from "./ConfigurationWorkspacePanel";

const SAFE_REQUEST_ERROR = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

interface MappingWorkspacePanelProps {
  projectKey: string;
  receipt: IntakeReceipt;
  api?: MappingApi;
  longApi?: LongApi;
  dataReviewApi?: DataReviewApi;
  configurationApi?: ConfigurationApi;
}

export function MappingWorkspacePanel({
  projectKey,
  receipt,
  api = defaultMappingApi,
  longApi = defaultLongApi,
  dataReviewApi = defaultDataReviewApi,
  configurationApi,
}: MappingWorkspacePanelProps) {
  const [supplierScope, setSupplierScope] = useState("");
  const [snapshot, setSnapshot] = useState<MappingWorkspaceSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const load = async (offset = 0) => {
    const scope = supplierScope.trim();
    if (!scope) {
      setError("업체 범위를 직접 입력해 주세요.");
      return;
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setError(null);
    try {
      const result = await api.getPreview(
        {
          projectKey,
          receiptId: receipt.receipt_id,
          contentSha256: receipt.content_sha256,
          supplierScope: scope,
          cellOffset: offset,
          cellLimit: 120,
        },
        controller.signal,
      );
      setSnapshot(result);
      setSupplierScope(result.supplier_scope);
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) {
        setError(reason instanceof MappingApiError ? reason.message : SAFE_REQUEST_ERROR);
      }
    } finally {
      if (controllerRef.current === controller) setBusy(false);
    }
  };

  return (
    <section className="mapping-workspace" aria-labelledby="mapping-workspace-heading">
      <div className="section-heading">
        <div>
          <p className="panel__kicker">MAPPING REVIEW</p>
          <h3 id="mapping-workspace-heading">원본 셀 매핑 검토</h3>
        </div>
        <span className="section-count">03</span>
      </div>

      <div className="boundary-banner mapping-boundary">
        이 화면은 원본 근거의 검토용 미리보기입니다. 초안 저장, 검토, 관리자 승인과 Long
        적재를 자동 실행하지 않으며 공식 데이터나 품질 결과를 만들지 않습니다.
      </div>

      <div className="field mapping-scope-field">
        <label htmlFor={`supplier-scope-${receipt.receipt_id}`}>업체 범위</label>
        <div className="mapping-scope-row">
          <input
            className="text-input"
            id={`supplier-scope-${receipt.receipt_id}`}
            value={supplierScope}
            onChange={(event) => setSupplierScope(event.target.value)}
            disabled={busy}
            required
            maxLength={200}
            autoComplete="off"
            aria-describedby={`supplier-scope-hint-${receipt.receipt_id}`}
          />
          <button className="secondary-button" type="button" onClick={() => void load()} disabled={busy}>
            {busy ? "원본 다시 확인 중…" : "원본 셀 검토 시작"}
          </button>
        </div>
        <p className="field__hint" id={`supplier-scope-hint-${receipt.receipt_id}`}>
          업체 범위는 원본 후보에서 추론하지 않습니다. 승인 이력과 대조할 정확한 범위를 직접
          입력하세요.
        </p>
      </div>

      <div className="screen-reader-only" role="status" aria-live="polite" aria-atomic="true">
        {busy ? "보존된 원본을 다시 스캔하고 있습니다." : error ?? snapshot?.message ?? "업체 범위를 입력할 수 있습니다."}
      </div>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {snapshot ? (
        <MappingResult
          key={`${snapshot.receipt.receipt_id}:${snapshot.supplier_scope}`}
          snapshot={snapshot}
          onPage={(offset) => void load(offset)}
          busy={busy}
          api={api}
          projectKey={projectKey}
          receipt={receipt}
          longApi={longApi}
          dataReviewApi={dataReviewApi}
          configurationApi={configurationApi}
        />
      ) : null}
    </section>
  );
}

function MappingResult({
  snapshot,
  onPage,
  busy,
  api,
  projectKey,
  receipt,
  longApi,
  dataReviewApi,
  configurationApi,
}: {
  snapshot: MappingWorkspaceSnapshot;
  onPage: (offset: number) => void;
  busy: boolean;
  api: MappingApi;
  projectKey: string;
  receipt: IntakeReceipt;
  longApi: LongApi;
  dataReviewApi: DataReviewApi;
  configurationApi?: ConfigurationApi;
}) {
  const approved = snapshot.state === "PREVIEW_READY";
  return (
    <div className="mapping-result">
      <div className="status-card" data-tone={approved ? "normal" : "warning"}>
        <p className="status-card__label">매핑 미리보기 상태</p>
        <h4 className="status-card__title">{snapshot.status_label}</h4>
        <p className="status-card__message">{snapshot.message}</p>
        <p className="mapping-ai-state">AI 호출 없음 · 일반 코드로만 재검사</p>
      </div>

      {snapshot.scan.issues.length ? <ScanIssueList issues={snapshot.scan.issues} /> : null}
      {snapshot.issues.length ? (
        <ul className="mapping-issue-list" aria-label="매핑 검토 사유">
          {snapshot.issues.map((issue, index) => (
            <li key={`${issue.code}-${issue.sheet_name ?? "workbook"}-${index}`}>
              <strong>{issue.code}</strong>
              <span>{issue.message}</span>
              <small>{logicalMappingLocation(issue.sheet_name, issue.coordinate)}</small>
            </li>
          ))}
        </ul>
      ) : null}

      {snapshot.template && snapshot.preview ? (
        <>
          <ApprovedPreview snapshot={snapshot} />
          <ConfigurationWorkspacePanel projectKey={projectKey} api={configurationApi} />
          <LongCandidatePanel
            api={longApi}
            projectKey={projectKey}
            supplierScope={snapshot.supplier_scope}
            receipt={receipt}
            dataReviewApi={dataReviewApi}
          />
        </>
      ) : (
        <ManualMappingDraft
          snapshot={snapshot}
          onPage={onPage}
          previewBusy={busy}
          api={api}
          projectKey={projectKey}
          receipt={receipt}
          longApi={longApi}
          dataReviewApi={dataReviewApi}
          configurationApi={configurationApi}
        />
      )}
    </div>
  );
}

function ScanIssueList({ issues }: { issues: MappingWorkspaceSnapshot["scan"]["issues"] }) {
  return (
    <div>
      <div className="section-heading"><h4>원본 스캔 확인사항</h4><span className="section-count">{issues.length}</span></div>
      <ul className="mapping-issue-list" aria-label="원본 스캔 확인사항">
        {issues.map((issue, index) => (
          <li key={`${issue.code}-${issue.location ?? "workbook"}-${index}`}>
            <strong>{issue.code}</strong><span>{issue.message}</span><small>{issue.location ?? "workbook"}</small>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ApprovedPreview({ snapshot }: { snapshot: MappingWorkspaceSnapshot }) {
  const { template, preview } = snapshot;
  if (!template || !preview) return null;
  return (
    <div>
      <div className="section-heading"><h4>승인 이력과 일치한 미리보기</h4><span className="section-count">rev. {template.revision}</span></div>
      <dl className="evidence-grid">
        <Evidence label="Template ID" value={template.template_id} />
        <Evidence label="Schema" value={template.schema_version} />
        <Evidence label="승인자" value={template.approved_by} />
        <Evidence label="원본 검사일" value={preview.source_inspection_date} />
        <Evidence label="Revision ID" value={template.revision_id} wide />
        <Evidence label="Payload SHA-256" value={template.payload_sha256} wide />
      </dl>

      <div className="section-heading"><h4>식별자 역할</h4><span className="section-count">{preview.identifiers.length}</span></div>
      <div className="mapping-role-grid">
        {preview.identifiers.map((identifier) => (
          <MappedEvidenceCard key={`${identifier.kind}-${identifier.evidence.coordinate}`} label={identifier.kind} cell={identifier.evidence} />
        ))}
      </div>

      {preview.inspection_rows.map((row) => (
        <div className="mapping-row" key={row.row_key}>
          <div className="section-heading"><h4>검사항목 역할 · {row.row_key}</h4><span className="section-count">exact cells</span></div>
          <div className="mapping-role-grid">
            {mappingRoles(row).map(({ label, cell }) => (
              <MappedEvidenceCard key={label} label={label} cell={cell} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

type AssignmentCode = "HEADER" | `IDENTIFIER:${MappingIdentifierKind}` | `ROW:${RowRole}`;

type RowRole =
  | "item"
  | "method"
  | "instrument"
  | "specification"
  | "tolerance"
  | "minimum"
  | "maximum"
  | "sample"
  | "supplier_result"
  | "section"
  | "category"
  | "unit"
  | "measurement_point"
  | "measurement_location"
  | "cavity"
  | "target"
  | "lsl"
  | "usl"
  | "source_spec_revision";

interface AssignedCell {
  assignment: AssignmentCode;
  source: MappingCellReference;
}

const IDENTIFIER_OPTIONS: ReadonlyArray<{ value: MappingIdentifierKind; label: string }> = [
  { value: "SUPPLIER", label: "식별자 · 업체" },
  { value: "INSPECTION_DATE", label: "식별자 · 검사일" },
  { value: "MODEL", label: "식별자 · 모델" },
  { value: "PART_NUMBER", label: "식별자 · 부품번호" },
  { value: "PART_NAME", label: "식별자 · 부품명" },
  { value: "LOT_NUMBER", label: "식별자 · LOT" },
  { value: "REPORT_NUMBER", label: "식별자 · 성적서번호" },
  { value: "REVISION", label: "식별자 · 원본 Revision" },
  { value: "PRODUCTION_DATE", label: "식별자 · 생산일" },
  { value: "CURRENT_SHIPMENT_QUANTITY", label: "식별자 · 현재 출하수량" },
  {
    value: "SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY",
    label: "식별자 · 업체 누적 출하수량",
  },
];

const ROW_ROLE_OPTIONS: ReadonlyArray<{ value: RowRole; label: string }> = [
  { value: "item", label: "행 · 검사항목" },
  { value: "sample", label: "행 · 샘플값" },
  { value: "method", label: "행 · 측정방법" },
  { value: "instrument", label: "행 · 측정기" },
  { value: "specification", label: "행 · 원본 사양" },
  { value: "tolerance", label: "행 · 원본 공차" },
  { value: "minimum", label: "행 · 원본 최소" },
  { value: "maximum", label: "행 · 원본 최대" },
  { value: "supplier_result", label: "행 · 업체 원본 결과" },
  { value: "section", label: "행 · 구역" },
  { value: "category", label: "행 · 분류" },
  { value: "unit", label: "행 · 단위" },
  { value: "measurement_point", label: "행 · 측정점" },
  { value: "measurement_location", label: "행 · 측정 위치" },
  { value: "cavity", label: "행 · Cavity" },
  { value: "target", label: "행 · 목표값" },
  { value: "lsl", label: "행 · 원본 하한" },
  { value: "usl", label: "행 · 원본 상한" },
  { value: "source_spec_revision", label: "행 · 원본 Spec Revision" },
];

function ManualMappingDraft({
  snapshot,
  onPage,
  previewBusy,
  api,
  projectKey,
  receipt,
  longApi,
  dataReviewApi,
  configurationApi,
}: {
  snapshot: MappingWorkspaceSnapshot;
  onPage: (offset: number) => void;
  previewBusy: boolean;
  api: MappingApi;
  projectKey: string;
  receipt: IntakeReceipt;
  longApi: LongApi;
  dataReviewApi: DataReviewApi;
  configurationApi?: ConfigurationApi;
}) {
  const [assignments, setAssignments] = useState<Record<string, AssignedCell>>({});
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [effectiveTo, setEffectiveTo] = useState("");
  const [draftReason, setDraftReason] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [approveReason, setApproveReason] = useState("");
  const [workflow, setWorkflow] = useState<MappingWorkflowSnapshot | null>(null);
  const [commandBusy, setCommandBusy] = useState(false);
  const [commandError, setCommandError] = useState<{
    title: string;
    message: string;
    code: string;
  } | null>(null);
  const commandController = useRef<AbortController | null>(null);

  useEffect(() => () => commandController.current?.abort(), []);

  const assign = (source: MappingCellReference, assignment: string) => {
    const key = cellKey(source);
    setCommandError(null);
    setAssignments((current) => {
      const next = { ...current };
      delete next[key];
      if (!assignment) return next;
      const typed = assignment as AssignmentCode;
      for (const [otherKey, other] of Object.entries(next)) {
        if (
          typed.startsWith("IDENTIFIER:") &&
          other.assignment === typed
        ) {
          delete next[otherKey];
        }
        if (
          typed.startsWith("ROW:") &&
          typed !== "ROW:sample" &&
          other.assignment === typed &&
          sourceRowKey(other.source) === sourceRowKey(source)
        ) {
          delete next[otherKey];
        }
      }
      next[key] = { assignment: typed, source };
      return next;
    });
  };

  const submitDraft = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const reason = draftReason.trim();
    const validation = validateDraft(assignments, effectiveFrom, effectiveTo, reason);
    if (validation) {
      setCommandError({ title: "Draft 입력 확인", message: validation, code: "DRAFT_INPUT_REQUIRED" });
      return;
    }
    const request = buildDraftRequest({
      assignments,
      projectKey,
      receipt,
      supplierScope: snapshot.supplier_scope,
      effectiveFrom,
      effectiveTo,
      reason,
    });
    await runCommand(() => api.createDraft(request, commandController.current?.signal));
  };

  const submitDecision = async (kind: "review" | "approve") => {
    if (!workflow) return;
    const reason = (kind === "review" ? reviewReason : approveReason).trim();
    if (!reason) {
      setCommandError({
        title: kind === "review" ? "검토 이유 필요" : "승인 이유 필요",
        message: "이 단계의 이유를 입력해 주세요.",
        code: "WORKFLOW_REASON_REQUIRED",
      });
      return;
    }
    const request: MappingWorkflowCommandRequest = {
      project_key: projectKey,
      receipt_id: receipt.receipt_id,
      content_sha256: receipt.content_sha256,
      supplier_scope: snapshot.supplier_scope,
      expected_history_row_version: workflow.workflow.history_row_version,
      expected_revision_row_version: workflow.workflow.revision_row_version,
      reason,
    };
    await runCommand(() =>
      kind === "review"
        ? api.review(
            workflow.workflow.template_id,
            workflow.workflow.revision,
            request,
            commandController.current?.signal,
          )
        : api.approve(
            workflow.workflow.template_id,
            workflow.workflow.revision,
            request,
            commandController.current?.signal,
          ),
    );
  };

  const runCommand = async (command: () => Promise<MappingWorkflowSnapshot>) => {
    commandController.current?.abort();
    const controller = new AbortController();
    commandController.current = controller;
    setCommandBusy(true);
    setCommandError(null);
    try {
      setWorkflow(await command());
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) {
        setCommandError(toCommandError(reason));
      }
    } finally {
      if (commandController.current === controller) setCommandBusy(false);
    }
  };

  const page = snapshot.source_cells;
  const disabled = previewBusy || commandBusy || workflow !== null;
  return (
    <div className="mapping-authoring">
      <aside className="mapping-limit-note" aria-label="현재 매핑 작성 지원 범위">
        <strong>현재 지원 범위</strong>
        Schema v2의 행 중심 양식만 지원합니다. 머리글 근거 1개 이상, 업체·검사일 식별자,
        그리고 각 검사항목 행의 검사항목 셀과 샘플 셀 1개 이상이 필요합니다. 여러 구역에
        흩어진 비행형 구조와 같은 history의 후속 revision 작성은 아직 지원하지 않습니다.
        Template ID와 revision 1은 서버가 발급합니다.
      </aside>

      <div className="section-heading">
        <h4>수동 검토용 원본 셀</h4>
        <span className="section-count">{Object.keys(assignments).length} / {page.total} 지정</span>
      </div>
      <div className="sheet-table-wrap" tabIndex={0} aria-label="수동 검토용 원본 셀 역할 지정 표">
        <table className="sheet-table mapping-cell-table mapping-cell-table--authoring">
          <thead>
            <tr><th>위치</th><th>저장값</th><th>캐시값</th><th>수식</th><th>표시값</th><th>서식</th><th>역할</th></tr>
          </thead>
          <tbody>
            {page.cells.map((cell) => {
              const source = { sheet_name: cell.sheet_name, coordinate: cell.coordinate };
              return (
                <tr key={`${cell.sheet_position}-${cell.coordinate}`}>
                  <td className="sheet-table__name">{cell.sheet_name}!{cell.coordinate}</td>
                  <td>{formatTagged(cell.raw_value)}</td>
                  <td>{formatTagged(cell.cached_value)}</td>
                  <td className="formula-value">{cell.formula_text ?? "없음"}</td>
                  <td>{cell.display_value ?? `미렌더링 (${cell.display_value_status})`}</td>
                  <td>{cell.number_format}</td>
                  <td>
                    <label className="screen-reader-only" htmlFor={`role-${cell.sheet_position}-${cell.coordinate}`}>
                      {cell.sheet_name}!{cell.coordinate} 역할
                    </label>
                    <select
                      className="role-select"
                      id={`role-${cell.sheet_position}-${cell.coordinate}`}
                      value={assignments[cellKey(source)]?.assignment ?? ""}
                      onChange={(event) => assign(source, event.target.value)}
                      disabled={disabled}
                    >
                      <option value="">지정 안 함</option>
                      <option value="HEADER">머리글 일치 근거</option>
                      <optgroup label="식별자">
                        {IDENTIFIER_OPTIONS.map((option) => (
                          <option key={option.value} value={`IDENTIFIER:${option.value}`}>{option.label}</option>
                        ))}
                      </optgroup>
                      <optgroup label="검사항목 행">
                        {ROW_ROLE_OPTIONS.map((option) => (
                          <option key={option.value} value={`ROW:${option.value}`}>{option.label}</option>
                        ))}
                      </optgroup>
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mapping-pagination">
        <button className="secondary-button" type="button" disabled={previewBusy || page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - page.limit))}>이전 셀</button>
        <span>{page.offset + 1}–{page.offset + page.cells.length} / {page.total}</span>
        <button className="secondary-button" type="button" disabled={previewBusy || !page.truncated} onClick={() => onPage(page.offset + page.limit)}>다음 셀</button>
      </div>

      <AssignmentSummary assignments={assignments} />

      {!workflow ? (
        <form className="mapping-command-form" onSubmit={submitDraft} noValidate>
          <div className="field-row">
            <div className="field">
              <label htmlFor="mapping-effective-from">적용 시작일</label>
              <input id="mapping-effective-from" className="text-input" type="date" value={effectiveFrom} onChange={(event) => setEffectiveFrom(event.target.value)} disabled={commandBusy} required />
              <p className="field__hint">자동 날짜를 사용하지 않습니다.</p>
            </div>
            <div className="field">
              <label htmlFor="mapping-effective-to">적용 종료일</label>
              <input id="mapping-effective-to" className="text-input" type="date" value={effectiveTo} onChange={(event) => setEffectiveTo(event.target.value)} disabled={commandBusy} />
              <p className="field__hint">기간이 열려 있으면 비워 두세요.</p>
            </div>
          </div>
          <ReasonField id="mapping-draft-reason" label="Draft 생성 이유" value={draftReason} onChange={setDraftReason} disabled={commandBusy} />
          <div className="mapping-version-note">예상 history row_version: <strong>0</strong></div>
          <button className="submit-button" type="submit" disabled={commandBusy}>매핑 Draft 생성</button>
        </form>
      ) : (
        <WorkflowCommands
          snapshot={workflow}
          reviewReason={reviewReason}
          approveReason={approveReason}
          setReviewReason={setReviewReason}
          setApproveReason={setApproveReason}
          busy={commandBusy}
          onReview={() => void submitDecision("review")}
          onApprove={() => void submitDecision("approve")}
          longApi={longApi}
          dataReviewApi={dataReviewApi}
          configurationApi={configurationApi}
          receipt={receipt}
        />
      )}

      <div className="screen-reader-only" role="status" aria-live="polite" aria-atomic="true">
        {commandBusy ? "매핑 workflow 명령을 처리하고 있습니다." : workflow ? `매핑 상태 ${workflow.workflow.status}` : "매핑 역할을 지정할 수 있습니다."}
      </div>
      {commandError ? (
        <div className="mapping-command-error" role="alert">
          <strong>{commandError.title}</strong><span>{commandError.message}</span><code>{commandError.code}</code>
        </div>
      ) : null}
    </div>
  );
}

function AssignmentSummary({ assignments }: { assignments: Record<string, AssignedCell> }) {
  const values = Object.values(assignments).sort((left, right) => compareSources(left.source, right.source));
  return (
    <div className="mapping-selection-summary" aria-label="선택한 매핑 역할">
      <div className="section-heading"><h4>선택한 역할</h4><span className="section-count">{values.length}</span></div>
      {values.length ? (
        <ul>{values.map((value) => <li key={cellKey(value.source)}><strong>{assignmentLabel(value.assignment)}</strong><span>{value.source.sheet_name}!{value.source.coordinate}</span></li>)}</ul>
      ) : <p className="empty-value">아직 지정한 셀이 없습니다.</p>}
    </div>
  );
}

function WorkflowCommands({
  snapshot,
  reviewReason,
  approveReason,
  setReviewReason,
  setApproveReason,
  busy,
  onReview,
  onApprove,
  longApi,
  dataReviewApi,
  configurationApi,
  receipt,
}: {
  snapshot: MappingWorkflowSnapshot;
  reviewReason: string;
  approveReason: string;
  setReviewReason: (value: string) => void;
  setApproveReason: (value: string) => void;
  busy: boolean;
  onReview: () => void;
  onApprove: () => void;
  longApi: LongApi;
  dataReviewApi: DataReviewApi;
  configurationApi?: ConfigurationApi;
  receipt: IntakeReceipt;
}) {
  const { workflow, proof, preview } = snapshot;
  return (
    <div className="mapping-workflow">
      <div className="status-card" data-tone="normal">
        <p className="status-card__label">현재 Mapping workflow</p>
        <h4 className="status-card__title">{workflowStatusLabel(workflow.status)}</h4>
        <p className="status-card__message">Template ID {workflow.template_id} · schema {workflow.schema_version} · revision {workflow.revision}</p>
      </div>
      <dl className="evidence-grid">
        <Evidence label="History row_version" value={String(workflow.history_row_version)} />
        <Evidence label="Revision row_version" value={String(workflow.revision_row_version)} />
        <Evidence label="Fingerprint SHA-256" value={proof.fingerprint_sha256} wide />
        <Evidence label="지정 셀 수" value={String(proof.mapped_cell_count)} />
        <Evidence label="검사항목 행 수" value={String(proof.inspection_row_count)} />
      </dl>

      {workflow.status === "DRAFT" ? (
        <div className="mapping-command-form">
          <ReasonField id="mapping-review-reason" label="검토 완료 이유" value={reviewReason} onChange={setReviewReason} disabled={busy} />
          <p className="field__hint">현재 pre-authenticated 사용자가 REVIEWER 권한일 때만 실행됩니다.</p>
          <button className="secondary-button" type="button" onClick={onReview} disabled={busy || !workflow.capabilities.can_review}>검토 완료 (REVIEWER)</button>
        </div>
      ) : null}

      {workflow.status === "REVIEWED" ? (
        <div className="mapping-command-form">
          <ReasonField id="mapping-approve-reason" label="최종 승인 이유" value={approveReason} onChange={setApproveReason} disabled={busy} />
          <p className="field__hint">현재 pre-authenticated 사용자가 ADMIN 권한일 때만 실행됩니다.</p>
          <button className="submit-button" type="button" onClick={onApprove} disabled={busy || !workflow.capabilities.can_approve}>최종 승인 (ADMIN)</button>
        </div>
      ) : null}

      {workflow.status === "APPROVED" && preview ? (
        <>
          <div className="boundary-banner">
            승인된 exact Mapping Preview가 준비되었습니다. 식별자 {preview.identifier_count}개,
            검사항목 행 {preview.inspection_row_count}개입니다. 시스템 품질 결과는 평가하지 않았고
            공식값이나 Long 데이터는 생성하지 않았습니다.
          </div>
          <BulkWorkspacePanel
            projectKey={workflow.project_key}
            supplierScope={workflow.supplier_scope}
          />
          <ConfigurationWorkspacePanel projectKey={workflow.project_key} api={configurationApi} />
          <LongCandidatePanel
            api={longApi}
            projectKey={workflow.project_key}
            supplierScope={workflow.supplier_scope}
            receipt={receipt}
            dataReviewApi={dataReviewApi}
          />
        </>
      ) : null}
      {!workflow.capabilities.additional_revisions_supported ? (
        <p className="mapping-version-limit">같은 history의 후속 revision 작성은 현재 화면에서 지원하지 않습니다.</p>
      ) : null}
    </div>
  );
}

function LongCandidatePanel({
  api,
  projectKey,
  supplierScope,
  receipt,
  dataReviewApi,
}: {
  api: LongApi;
  projectKey: string;
  supplierScope: string;
  receipt: IntakeReceipt;
  dataReviewApi: DataReviewApi;
}) {
  const [operation, setOperation] = useState<LongOperationResponse | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ title: string; message: string; code: string } | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const scope = {
    project_key: projectKey,
    receipt_id: receipt.receipt_id,
    content_sha256: receipt.content_sha256,
    supplier_scope: supplierScope,
  };

  const run = async (kind: "candidate" | "confirm") => {
    if (kind === "confirm" && (!operation || !confirmed)) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setError(null);
    try {
      const response =
        kind === "candidate"
          ? await api.createCandidate(scope, controller.signal)
          : await api.confirm(
              {
                ...scope,
                candidate_digest: operation?.candidate.candidate_digest ?? "",
                confirmed: true,
              },
              controller.signal,
            );
      setOperation(response);
      if (kind === "candidate") setConfirmed(false);
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) {
        setError(toLongError(reason));
      }
    } finally {
      if (controllerRef.current === controller) setBusy(false);
    }
  };

  const candidate = operation?.candidate ?? null;
  const persistence = operation?.persistence ?? null;
  return (
    <section className="long-workspace" aria-labelledby="long-workspace-heading">
      <div className="section-heading">
        <div>
          <p className="panel__kicker">LONG CANDIDATE</p>
          <h4 id="long-workspace-heading">Long 후보와 명시적 저장 확인</h4>
        </div>
        <span className="section-count">05</span>
      </div>
      <div className="mapping-limit-note">
        후보 생성은 승인된 Mapping과 현재 Binding catalog를 다시 확인하는 읽기 전용 단계입니다.
        Binding 누락·중복은 자동 연결하지 않고 해당 행을 보류합니다. 후보를 만든 것만으로는
        저장하지 않으며, 아래의 별도 확인을 거쳐도 PENDING 또는 HELD 상태만 저장합니다.
      </div>
      {!candidate ? (
        <button className="secondary-button" type="button" onClick={() => void run("candidate")} disabled={busy}>
          {busy ? "Long 후보 만드는 중…" : "Long 후보 만들기"}
        </button>
      ) : (
        <>
          <LongCandidateView candidate={candidate} />
          {!persistence ? (
            <div className="long-confirmation">
              <label className="long-confirm-check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                  disabled={busy || !candidate.capabilities.can_confirm}
                />
                <span>
                  이 후보 digest와 행별 PENDING/HELD 저장 범위를 확인했습니다. 공식값 계산이나
                  자동 상태 승격이 없음을 이해했습니다.
                </span>
              </label>
              <button
                className="submit-button"
                type="button"
                onClick={() => void run("confirm")}
                disabled={busy || !confirmed || !candidate.capabilities.can_confirm}
              >
                {busy ? "저장 확인 처리 중…" : "PENDING/HELD 저장 확인"}
              </button>
              {!candidate.capabilities.can_confirm ? (
                <p className="form-error">현재 후보는 서버 기준으로 확인 저장할 수 없습니다. 표시된 보류 사유를 먼저 검토하세요.</p>
              ) : null}
            </div>
          ) : operation ? (
            <LongPersistenceView
              operation={operation}
              projectKey={projectKey}
              dataReviewApi={dataReviewApi}
            />
          ) : null}
        </>
      )}
      <div className="screen-reader-only" role="status" aria-live="polite" aria-atomic="true">
        {busy ? "Long 요청을 처리하고 있습니다." : persistence ? persistence.status_label : candidate?.message ?? "Long 후보를 명시적으로 만들 수 있습니다."}
      </div>
      {error ? (
        <div className="mapping-command-error" role="alert">
          <strong>{error.title}</strong><span>{error.message}</span><code>{error.code}</code>
        </div>
      ) : null}
    </section>
  );
}

function LongCandidateView({ candidate }: { candidate: LongCandidateSnapshot }) {
  return (
    <div className="long-candidate">
      <div className="status-card" data-tone={candidate.state === "LOAD_CANDIDATE_READY" ? "normal" : "warning"}>
        <p className="status-card__label">Long 후보 상태</p>
        <h5 className="status-card__title">{candidate.status_label}</h5>
        <p className="status-card__message">{candidate.message}</p>
      </div>
      <dl className="evidence-grid">
        <Evidence label="원본 Receipt" value={candidate.receipt.receipt_id} wide />
        <Evidence label="원본 SHA-256" value={candidate.receipt.content_sha256} wide />
        <Evidence label="Mapping Revision ID" value={candidate.mapping.revision_id} wide />
        <Evidence label="Mapping Payload SHA-256" value={candidate.mapping.payload_sha256} wide />
        <Evidence label="Binding catalog revision" value={candidate.binding_catalog_revision} wide />
        <Evidence label="Candidate digest" value={candidate.candidate_digest} wide />
        <Evidence label="전체 행" value={String(candidate.row_count)} />
        <Evidence label="PENDING 예정 행" value={String(candidate.loadable_row_count)} />
        <Evidence label="HELD 예정 행" value={String(candidate.held_row_count)} />
      </dl>
      <div className="long-safety-boundary" aria-label="Long 후보 안전 경계">
        <span>공식값 생성 없음</span><span>통계·사양 계산 없음</span><span>VALID 전환 없음</span>
        <span>AI 호출 없음</span><span>자동 Binding 없음</span>
      </div>
      {candidate.issues.length ? <LongIssueList issues={candidate.issues} label="후보 전체 보류 사유" /> : null}
      <div className="section-heading"><h5>행별 적재 후보</h5><span className="section-count">{candidate.rows.length}</span></div>
      <div className="sheet-table-wrap" tabIndex={0} aria-label="행별 Long 후보 표">
        <table className="sheet-table long-row-table">
          <thead><tr><th>원본 행</th><th>후보 상태</th><th>저장 상태</th><th>샘플 수</th><th>Binding</th><th>보류 사유</th></tr></thead>
          <tbody>
            {candidate.rows.map((row) => (
              <tr key={row.row_key}>
                <td className="sheet-table__name">
                  <strong>{row.row_key}</strong><br />{row.source.sheet_name}!{row.source.coordinate}<br />{formatTagged(row.source.raw_value)}
                </td>
                <td><span className="long-state-pill" data-state={row.state}>{row.status_label}</span></td>
                <td>{row.pending_data_status}</td>
                <td>{row.measurement_count}개</td>
                <td>
                  {row.binding ? (
                    <span>{row.binding.canonical_model_key} / {row.binding.canonical_item_key}<br />rev. {row.binding.binding_revision}</span>
                  ) : (
                    <strong className="held-text">Binding 미확정 · 자동 연결 안 함</strong>
                  )}
                </td>
                <td>{row.issues.length ? row.issues.map((issue) => issue.code).join(", ") : "없음"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {candidate.rows.flatMap((row) => row.issues).length ? (
        <LongIssueList issues={candidate.rows.flatMap((row) => row.issues)} label="행별 typed 보류 사유" />
      ) : null}
      <p className="mapping-version-limit">
        확인 가능 여부는 서버 결과를 따릅니다. Candidate digest 확인이 필수이며 멱등성은 서버가 관리합니다.
      </p>
    </div>
  );
}

function LongIssueList({ issues, label }: { issues: LongCandidateIssue[]; label: string }) {
  return (
    <div>
      <div className="section-heading"><h5>{label}</h5><span className="section-count">{issues.length}</span></div>
      <ul className="mapping-issue-list" aria-label={label}>
        {issues.map((issue, index) => (
          <li key={`${issue.code}-${issue.row_key ?? "source"}-${index}`}>
            <strong>{issue.code}</strong><span>{issue.message}</span>
            <small>{longIssueLocation(issue)}</small>
          </li>
        ))}
      </ul>
    </div>
  );
}

function LongPersistenceView({
  operation,
  projectKey,
  dataReviewApi,
}: {
  operation: LongOperationResponse;
  projectKey: string;
  dataReviewApi: DataReviewApi;
}) {
  const persistence = operation.persistence;
  if (!persistence) return null;
  return (
    <div className="long-persistence">
      <div className="status-card" data-tone={persistence.status === "HELD" ? "warning" : "normal"}>
        <p className="status-card__label">Long 저장 결과</p>
        <h5 className="status-card__title">{persistence.status_label}</h5>
        <p className="status-card__message">
          {persistence.replayed
            ? "동일 확인 요청의 기존 결과를 멱등 재사용했습니다."
            : "명시적으로 확인한 후보를 새 저장 작업으로 처리했습니다."}
        </p>
      </div>
      <dl className="evidence-grid">
        <Evidence label="Ingestion Job ID" value={persistence.ingestion_job_id} wide />
        <Evidence label="Job row_version" value={String(persistence.row_version)} />
        <Evidence label="LOT 수" value={String(persistence.counts.lot_count)} />
        <Evidence label="결과 행 수" value={String(persistence.counts.result_count)} />
        <Evidence label="측정값 수" value={String(persistence.counts.measurement_count)} />
        <Evidence label="HELD 결과 수" value={String(persistence.counts.held_result_count)} />
      </dl>
      <div className="long-safety-boundary">
        <span>PENDING/HELD 전용 저장</span><span>공식값 생성 없음</span>
        <span>계산 없음</span><span>VALID 전환 없음</span>
      </div>
      <DataReviewWorkspace
        api={dataReviewApi}
        projectKey={projectKey}
        ingestionJobId={persistence.ingestion_job_id}
      />
    </div>
  );
}

function DataReviewWorkspace({
  api,
  projectKey,
  ingestionJobId,
}: {
  api: DataReviewApi;
  projectKey: string;
  ingestionJobId: string;
}) {
  const [targets, setTargets] = useState<DataReviewTargetsResponse | null>(null);
  const [selectedTarget, setSelectedTarget] = useState<DataReviewTarget | null>(null);
  const [candidate, setCandidate] = useState<DataReviewCandidate | null>(null);
  const [decision, setDecision] = useState<DataReviewDecision | null>(null);
  const [targetStatus, setTargetStatus] = useState<DataReviewTargetStatus | "">("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState<"targets" | "candidate" | "decision" | null>(null);
  const [error, setError] = useState<{ title: string; message: string; code: string } | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const beginRequest = () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setError(null);
    return controller;
  };

  const loadTargets = async () => {
    const controller = beginRequest();
    setBusy("targets");
    try {
      const response = await api.getTargets(
        { project_key: projectKey, ingestion_job_id: ingestionJobId },
        controller.signal,
      );
      setTargets(response);
      setSelectedTarget(null);
      setCandidate(null);
      setDecision(null);
      resetDecisionInput();
    } catch (reasonValue) {
      if (!(reasonValue instanceof DOMException && reasonValue.name === "AbortError")) {
        setError(toDataReviewError(reasonValue));
      }
    } finally {
      if (controllerRef.current === controller) setBusy(null);
    }
  };

  const loadCandidate = async (target: DataReviewTarget) => {
    const controller = beginRequest();
    setBusy("candidate");
    setSelectedTarget(target);
    setCandidate(null);
    setDecision(null);
    resetDecisionInput();
    try {
      const response = await api.createCandidate(
        { project_key: projectKey, result_id: target.result_id },
        controller.signal,
      );
      setCandidate(response.candidate);
    } catch (reasonValue) {
      if (!(reasonValue instanceof DOMException && reasonValue.name === "AbortError")) {
        setError(toDataReviewError(reasonValue));
      }
    } finally {
      if (controllerRef.current === controller) setBusy(null);
    }
  };

  const resetDecisionInput = () => {
    setTargetStatus("");
    setReason("");
    setConfirmed(false);
  };

  const submitDecision = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!candidate || !selectedTarget || !targetStatus) {
      setError({
        title: "결정 입력 확인",
        message: "서버가 허용한 데이터상태를 하나 선택해 주세요.",
        code: "DATA_STATUS_SELECTION_REQUIRED",
      });
      return;
    }
    const normalizedReason = reason.trim();
    if (!normalizedReason) {
      setError({
        title: "결정 이유 필요",
        message: "데이터상태 결정의 이유를 입력해 주세요.",
        code: "DATA_STATUS_REASON_REQUIRED",
      });
      return;
    }
    if (!confirmed || !canChooseStatus(candidate, selectedTarget, targetStatus)) return;

    const controller = beginRequest();
    setBusy("decision");
    try {
      const response = await api.decide(
        {
          project_key: projectKey,
          result_id: candidate.result.id,
          target_status: targetStatus,
          candidate_sha256: candidate.candidate_sha256,
          cas: candidate.cas,
          reason: normalizedReason,
          confirmed: true,
        },
        controller.signal,
      );
      setDecision(response.decision);
    } catch (reasonValue) {
      if (!(reasonValue instanceof DOMException && reasonValue.name === "AbortError")) {
        setError(toDataReviewError(reasonValue));
      }
    } finally {
      if (controllerRef.current === controller) setBusy(null);
    }
  };

  return (
    <section className="data-review-workspace" aria-labelledby="data-review-heading">
      <div className="section-heading">
        <div>
          <p className="panel__kicker">DATA STATUS REVIEW</p>
          <h5 id="data-review-heading">데이터상태 근거 검토와 명시적 결정</h5>
        </div>
        <span className="section-count">06</span>
      </div>
      <div className="data-review-boundary">
        <strong>데이터상태와 규격판정은 서로 다른 축입니다.</strong>
        규격판정이 FAIL이어도 원본·단위·샘플·승인 Master 근거가 완전하면 서버가 VALID를
        허용할 수 있습니다. HELD 또는 구조상 부적격 결과는 VALID로 바꿀 수 없습니다.
        후보 조회만으로 상태는 바뀌지 않으며 아래 명시적 확인 전에는 어떤 결정도 저장하지 않습니다.
      </div>

      {!targets ? (
        <button
          className="secondary-button"
          type="button"
          onClick={() => void loadTargets()}
          disabled={busy !== null}
        >
          {busy === "targets" ? "검토 대상 불러오는 중…" : "데이터상태 검토 대상 불러오기"}
        </button>
      ) : (
        <DataReviewTargetList
          snapshot={targets}
          selectedResultId={selectedTarget?.result_id ?? null}
          busy={busy !== null}
          onSelect={(target) => void loadCandidate(target)}
          onReload={() => void loadTargets()}
        />
      )}

      {candidate && selectedTarget ? (
        <>
          <DataReviewCandidateView candidate={candidate} target={selectedTarget} />
          {!decision ? (
            <form className="data-review-decision" onSubmit={submitDecision} noValidate>
              <fieldset disabled={busy !== null || !candidate.capabilities.can_decide}>
                <legend>결정할 데이터상태</legend>
                <div className="data-status-options">
                  {(["VALID", "SUSPECT", "EXCLUDED"] as const).map((status) => {
                    const allowed = canChooseStatus(candidate, selectedTarget, status);
                    return (
                      <label key={status} data-disabled={!allowed}>
                        <input
                          type="radio"
                          name={`data-status-${candidate.result.id}`}
                          value={status}
                          checked={targetStatus === status}
                          onChange={() => setTargetStatus(status)}
                          disabled={!allowed}
                        />
                        <span><strong>{status}</strong>{dataStatusDescription(status)}</span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>
              <ReasonField
                id={`data-review-reason-${candidate.result.id}`}
                label="데이터상태 결정 이유"
                value={reason}
                onChange={setReason}
                disabled={busy !== null || !candidate.capabilities.can_decide}
              />
              <label className="long-confirm-check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                  disabled={
                    busy !== null ||
                    !targetStatus ||
                    !candidate.capabilities.can_decide ||
                    !canChooseStatus(candidate, selectedTarget, targetStatus)
                  }
                />
                <span>
                  Candidate SHA-256, 원본·Master·샘플 근거와 선택한 데이터상태를 확인했습니다.
                  규격판정과 데이터 신뢰상태가 독립임을 이해하고 이 결정을 명시적으로 실행합니다.
                </span>
              </label>
              <button
                className="submit-button"
                type="submit"
                disabled={
                  busy !== null ||
                  !confirmed ||
                  !targetStatus ||
                  !reason.trim() ||
                  !candidate.capabilities.can_decide ||
                  !canChooseStatus(candidate, selectedTarget, targetStatus)
                }
              >
                {busy === "decision" ? "데이터상태 결정 중…" : "선택한 데이터상태로 결정"}
              </button>
              {!candidate.capabilities.can_decide ? (
                <p className="form-error">이 후보는 서버 기준으로 결정할 수 없습니다. typed 차단 사유를 확인하세요.</p>
              ) : null}
            </form>
          ) : (
            <DataReviewDecisionView decision={decision} />
          )}
        </>
      ) : null}

      <div className="screen-reader-only" role="status" aria-live="polite" aria-atomic="true">
        {busy === "targets"
          ? "데이터상태 검토 대상을 불러오고 있습니다."
          : busy === "candidate"
            ? "데이터상태 후보 근거를 불러오고 있습니다."
            : busy === "decision"
              ? "명시적 데이터상태 결정을 처리하고 있습니다."
              : decision
                ? `데이터상태 ${decision.target_status} 결정이 완료되었습니다.`
                : candidate?.message ?? "데이터상태 검토 대상을 불러올 수 있습니다."}
      </div>
      {error ? (
        <div className="mapping-command-error" role="alert">
          <strong>{error.title}</strong><span>{error.message}</span><code>{error.code}</code>
        </div>
      ) : null}
    </section>
  );
}

function DataReviewTargetList({
  snapshot,
  selectedResultId,
  busy,
  onSelect,
  onReload,
}: {
  snapshot: DataReviewTargetsResponse;
  selectedResultId: string | null;
  busy: boolean;
  onSelect: (target: DataReviewTarget) => void;
  onReload: () => void;
}) {
  return (
    <div className="data-review-targets">
      <div className="section-heading">
        <h5>서버가 확인한 검토 대상</h5>
        <span className="section-count">{snapshot.targets.length}개 · {snapshot.job_status}</span>
      </div>
      {snapshot.targets.length ? (
        <div className="sheet-table-wrap" tabIndex={0}>
          <table className="sheet-table data-review-target-table" aria-label="데이터상태 검토 대상 표">
            <thead><tr><th>원본 행</th><th>항목</th><th>LOT</th><th>현재 상태</th><th>검토 가능성</th><th>근거</th></tr></thead>
            <tbody>
              {snapshot.targets.map((target) => (
                <tr key={target.result_id} data-selected={selectedResultId === target.result_id}>
                  <td className="sheet-table__name">{target.source_row_key}</td>
                  <td>{target.canonical_item_key ?? "미확정"}</td>
                  <td>{target.source_lot_text ?? `LOT ${target.lot_ordinal}`}<br /><small>{target.inspection_date ?? "검사일 없음"}</small></td>
                  <td><span className="long-state-pill" data-state={target.data_status === "HELD" ? "ROW_HELD" : target.data_status}>{target.data_status}</span></td>
                  <td>{target.status_label}<br /><small>{target.reviewable ? "PENDING 검토 대상" : "결정 불가 상태"}</small></td>
                  <td>
                    <button className="table-action" type="button" onClick={() => onSelect(target)} disabled={busy}>
                      {selectedResultId === target.result_id ? "근거 다시 보기" : "근거 검토"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty-value">이 저장 작업에는 결과 행이 없습니다. 전체 HELD 작업이면 정상입니다.</p>
      )}
      <button className="secondary-button" type="button" onClick={onReload} disabled={busy}>대상 새로 확인</button>
    </div>
  );
}

function DataReviewCandidateView({
  candidate,
  target,
}: {
  candidate: DataReviewCandidate;
  target: DataReviewTarget;
}) {
  const selectedMaster = candidate.selected_master;
  const statusTone = candidate.state === "INELIGIBLE" ? "danger" : candidate.state === "REVIEW_ONLY" ? "warning" : "normal";
  return (
    <div className="data-review-candidate">
      <div className="status-card" data-tone={statusTone}>
        <p className="status-card__label">데이터상태 후보</p>
        <h5 className="status-card__title">{candidate.status_label}</h5>
        <p className="status-card__message">{candidate.message}</p>
      </div>

      <div className="review-axis-grid" aria-label="데이터상태와 규격판정 분리 결과">
        <div>
          <span>현재 데이터상태</span>
          <strong>{candidate.result.data_status}</strong>
          <small>결정 대상: {candidate.allowed_target_statuses.length ? candidate.allowed_target_statuses.join(" · ") : "없음"}</small>
        </div>
        <div data-judgment={candidate.proposed_system_judgment ?? "NOT_EVALUATED"}>
          <span>제안 규격판정</span>
          <strong>{candidate.proposed_system_judgment ?? "미평가"}</strong>
          <small>{candidate.proposed_spec_evaluation_status}</small>
        </div>
      </div>
      {candidate.proposed_system_judgment === "FAIL" && candidate.allowed_target_statuses.includes("VALID") ? (
        <p className="independent-status-note">규격판정은 FAIL이지만 데이터 근거가 유효하여 서버가 VALID 선택을 허용했습니다.</p>
      ) : null}
      {target.data_status === "HELD" || candidate.state === "INELIGIBLE" ? (
        <p className="held-decision-note">HELD 또는 부적격 결과는 데이터상태 결정을 실행할 수 없으며 VALID 선택도 차단됩니다.</p>
      ) : null}

      <div className="section-heading"><h5>후보 Provenance</h5><span className="section-count">exact</span></div>
      <dl className="evidence-grid">
        <Evidence label="Result ID" value={candidate.result.id} wide />
        <Evidence label="원본 File ID" value={candidate.result.source_file_id} wide />
        <Evidence label="원본 SHA-256" value={candidate.result.source_content_sha256} wide />
        <Evidence label="Source evidence SHA-256" value={candidate.result.source_evidence_sha256} wide />
        <Evidence label="Binding snapshot SHA-256" value={candidate.result.binding_snapshot_sha256 ?? "없음"} wide />
        <Evidence label="Long candidate snapshot SHA-256" value={candidate.result.candidate_snapshot_sha256} wide />
        <Evidence label="Data review candidate SHA-256" value={candidate.candidate_sha256} wide />
        <Evidence label="LOT ID" value={candidate.result.lot_id} wide />
        <Evidence label="검사일" value={candidate.result.inspection_date ?? "없음"} />
        <Evidence label="Canonical item" value={candidate.item.canonical_item_key ?? "미확정"} />
        <Evidence label="항목 disposition" value={candidate.item.disposition ?? "미확정"} />
        <Evidence label="측정 모드" value={candidate.item.measurement_mode ?? "미확정"} />
      </dl>

      <div className="section-heading"><h5>원본 단위 근거</h5><span className="section-count">변환 없음</span></div>
      {candidate.source_unit ? (
        <dl className="evidence-grid">
          <Evidence label="원본 셀" value={`${candidate.source_unit.sheet_name}!${candidate.source_unit.coordinate}`} />
          <Evidence label="원본 단위 문자열" value={candidate.source_unit.raw_value} />
          <Evidence label="단위 셀 evidence SHA-256" value={candidate.source_unit.cell_evidence_sha256} wide />
        </dl>
      ) : <p className="empty-value">정확한 원본 단위 근거가 없습니다.</p>}

      <MasterEvidenceList masters={candidate.master_candidates} selected={selectedMaster} />
      <SampleEvidenceTable samples={candidate.samples} />
      {candidate.issues.length ? (
        <div>
          <div className="section-heading"><h5>Typed 검토·차단 사유</h5><span className="section-count">{candidate.issues.length}</span></div>
          <ul className="mapping-issue-list" aria-label="데이터상태 typed 검토 차단 사유">
            {candidate.issues.map((issue, index) => (
              <li key={`${issue.code}-${index}`}><strong>{issue.code}</strong><span>{issue.message}</span><small>server evidence</small></li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="long-safety-boundary" aria-label="데이터상태 후보 안전 경계">
        <span>공식값 생성 없음</span><span>단위 변환 없음</span><span>AI 사용 없음</span><span>통계 계산 없음</span>
      </div>
    </div>
  );
}

function MasterEvidenceList({
  masters,
  selected,
}: {
  masters: DataReviewMasterEvidence[];
  selected: DataReviewMasterEvidence | null;
}) {
  return (
    <div>
      <div className="section-heading"><h5>승인 Master revision 근거</h5><span className="section-count">{masters.length}개 후보</span></div>
      {masters.length ? (
        <div className="master-evidence-list">
          {masters.map((master) => (
            <article key={master.revision_id} data-selected={selected?.revision_id === master.revision_id}>
              <header><strong>Revision {master.revision_number}</strong><span>{selected?.revision_id === master.revision_id ? "적용 Master" : "후보"}</span></header>
              <dl className="evidence-grid">
                <Evidence label="External Spec Revision" value={master.external_spec_revision} />
                <Evidence label="단위" value={master.unit} />
                <Evidence label="선언 적용 시작" value={master.declared_effective_from} />
                <Evidence label="선언 적용 종료" value={master.declared_effective_to ?? "열린 기간"} />
                <Evidence label="해결된 적용 종료" value={master.resolved_effective_to ?? "열린 기간"} />
                <Evidence label="Target / LSL / USL" value={`${master.target ?? "-"} / ${master.lsl ?? "-"} / ${master.usl ?? "-"}`} />
                <Evidence label="History ID" value={master.history_id} wide />
                <Evidence label="Revision ID" value={master.revision_id} wide />
                <Evidence label="Master payload SHA-256" value={master.payload_sha256} wide />
              </dl>
            </article>
          ))}
        </div>
      ) : <p className="empty-value">적용 가능한 승인 Master revision이 없습니다.</p>}
    </div>
  );
}

function SampleEvidenceTable({ samples }: { samples: DataReviewCandidate["samples"] }) {
  return (
    <div>
      <div className="section-heading"><h5>샘플 exact 근거</h5><span className="section-count">{samples.length}개</span></div>
      {samples.length ? (
        <div className="sheet-table-wrap" tabIndex={0}>
          <table className="sheet-table data-review-sample-table" aria-label="데이터상태 샘플 근거 표">
            <thead><tr><th>순서·셀</th><th>원본 JSON</th><th>원본 숫자 JSON</th><th>정성 원본</th><th>수식</th><th>비교값</th><th>규격 비교</th><th>Evidence SHA-256</th></tr></thead>
            <tbody>
              {samples.map((sample) => (
                <tr key={sample.measurement_id}>
                  <td className="sheet-table__name">#{sample.sample_ordinal}<br />{sample.source_cell}</td>
                  <td><code className="raw-evidence">{sample.raw_value_json}</code></td>
                  <td><code className="raw-evidence">{sample.raw_numeric_value_json ?? "없음"}</code></td>
                  <td>{sample.raw_qualitative_value ?? "없음"}</td>
                  <td>{sample.formula_flag ? "있음 · 평가 제한" : "없음"}</td>
                  <td>{sample.numeric_value ?? "미평가"}</td>
                  <td><span className="comparison-pill" data-comparison={sample.comparison}>{comparisonLabel(sample.comparison)}</span></td>
                  <td><code className="raw-evidence">{sample.evidence_sha256}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="empty-value">보존된 샘플 근거가 없습니다.</p>}
    </div>
  );
}

function DataReviewDecisionView({ decision }: { decision: DataReviewDecision }) {
  return (
    <div className="data-review-decision-result">
      <div className="status-card" data-tone="normal">
        <p className="status-card__label">명시적 데이터상태 결정</p>
        <h5 className="status-card__title">{decision.target_status}</h5>
        <p className="status-card__message">
          {decision.replayed
            ? "동일한 결정 의도의 기존 결과를 서버가 멱등 재사용했습니다."
            : "확인한 후보 근거와 이유로 데이터상태 결정을 저장했습니다."}
        </p>
      </div>
      <div className="review-axis-grid" aria-label="저장된 데이터상태와 규격판정">
        <div><span>저장된 데이터상태</span><strong>{decision.target_status}</strong><small>측정값 {decision.measurement_count}개 동일 상태 전파</small></div>
        <div data-judgment={decision.system_judgment ?? "NOT_EVALUATED"}><span>저장된 규격판정</span><strong>{decision.system_judgment ?? "미평가"}</strong><small>{decision.evaluation_mode}</small></div>
      </div>
      <dl className="evidence-grid">
        <Evidence label="Transition ID" value={decision.transition_id} wide />
        <Evidence label="Candidate SHA-256" value={decision.candidate_sha256} wide />
        <Evidence label="Intent SHA-256" value={decision.intent_sha256} wide />
        <Evidence label="Result row_version" value={String(decision.result_row_version)} />
        <Evidence label="적용 Master revision" value={decision.master ? `${decision.master.external_spec_revision} · rev. ${decision.master.revision_number}` : "없음"} />
      </dl>
      <div className="long-safety-boundary">
        <span>자동 결정 없음</span><span>AI 사용 없음</span><span>추가 계산 없음</span>
      </div>
    </div>
  );
}

function canChooseStatus(
  candidate: DataReviewCandidate,
  target: DataReviewTarget,
  status: DataReviewTargetStatus,
): boolean {
  if (!candidate.capabilities.can_decide || !candidate.allowed_target_statuses.includes(status)) {
    return false;
  }
  if (status === "VALID" && (candidate.state !== "EVALUATED" || target.data_status === "HELD")) {
    return false;
  }
  return target.data_status !== "HELD" && candidate.state !== "INELIGIBLE";
}

function dataStatusDescription(status: DataReviewTargetStatus): string {
  if (status === "VALID") return "근거가 검증된 공식 통계 대상";
  if (status === "SUSPECT") return "의심 근거를 보존한 검토 대상";
  return "명시적 제외 사유를 보존";
}

function comparisonLabel(comparison: DataReviewCandidate["samples"][number]["comparison"]): string {
  if (comparison === "WITHIN_LIMITS") return "규격 내";
  if (comparison === "BELOW_LSL") return "하한 미만";
  if (comparison === "ABOVE_USL") return "상한 초과";
  return "미평가";
}

function toDataReviewError(reason: unknown): { title: string; message: string; code: string } {
  if (reason instanceof DataReviewApiError) {
    return { title: reason.statusLabel, message: reason.message, code: reason.code };
  }
  return { title: "데이터상태 처리 오류", message: SAFE_REQUEST_ERROR, code: "DATA_REVIEW_REQUEST_FAILED" };
}

function ReasonField({ id, label, value, onChange, disabled }: { id: string; label: string; value: string; onChange: (value: string) => void; disabled: boolean }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <textarea id={id} className="text-input reason-input" value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} required maxLength={1000} />
    </div>
  );
}

function MappedEvidenceCard({ label, cell }: { label: string; cell: MappedCell | null }) {
  return (
    <div className="mapped-evidence">
      <strong>{label}</strong>
      {cell ? (
        <>
          <span>{cell.sheet_name}!{cell.coordinate}</span>
          <span>저장값: {formatTagged(cell.raw_value)}</span>
          <span>캐시값: {formatTagged(cell.cached_value)}</span>
          <span>수식: {cell.formula_text ?? "없음"}</span>
          <span>표시값: {cell.display_value ?? `미렌더링 (${cell.display_value_status})`}</span>
          <small>{cell.value_kind} · {cell.number_format}</small>
        </>
      ) : <span className="empty-value">매핑 없음</span>}
    </div>
  );
}

function mappingRoles(row: MappingInspectionRow): Array<{ label: string; cell: MappedCell | null }> {
  return [
    { label: "검사항목", cell: row.item }, { label: "측정방법", cell: row.method },
    { label: "측정기", cell: row.instrument }, { label: "원본 사양", cell: row.specification },
    { label: "원본 공차", cell: row.tolerance }, { label: "원본 최소", cell: row.minimum },
    { label: "원본 최대", cell: row.maximum }, { label: "구역", cell: row.section },
    { label: "분류", cell: row.category }, { label: "단위", cell: row.unit },
    { label: "측정점", cell: row.measurement_point }, { label: "측정 위치", cell: row.measurement_location },
    { label: "Cavity", cell: row.cavity }, { label: "목표값", cell: row.target },
    { label: "원본 하한", cell: row.lsl }, { label: "원본 상한", cell: row.usl },
    { label: "원본 Spec Revision", cell: row.source_spec_revision },
    ...row.samples.map((cell, index) => ({ label: `샘플 ${index + 1}`, cell })),
    { label: "업체 원본 결과", cell: row.supplier_result },
  ];
}

function Evidence({ label, value, wide = false }: { label: string; value: string; wide?: boolean }) {
  return <div className={`evidence-item${wide ? " evidence-item--wide" : ""}`}><dt>{label}</dt><dd className={wide ? "hash-value" : undefined}>{value}</dd></div>;
}

function formatTagged(value: TaggedSourceValue): string {
  if (value.kind === "NULL") return "없음";
  if (value.kind === "UNSUPPORTED") return `표시 불가 (${value.python_type})`;
  return String(value.value);
}

function logicalMappingLocation(sheet: string | null, coordinate: string | null): string {
  return sheet && coordinate ? `${sheet}!${coordinate}` : sheet ? `sheet:${sheet}` : "workbook";
}

function cellKey(source: MappingCellReference): string {
  return JSON.stringify([source.sheet_name, source.coordinate]);
}

function sourceRowNumber(source: MappingCellReference): number {
  const match = /([1-9][0-9]*)$/.exec(source.coordinate);
  return match ? Number(match[1]) : 0;
}

function sourceRowKey(source: MappingCellReference): string {
  return JSON.stringify([source.sheet_name, sourceRowNumber(source)]);
}

function compareSources(left: MappingCellReference, right: MappingCellReference): number {
  const sheet = left.sheet_name.localeCompare(right.sheet_name, "ko");
  if (sheet) return sheet;
  const row = sourceRowNumber(left) - sourceRowNumber(right);
  if (row) return row;
  return excelColumnNumber(left.coordinate) - excelColumnNumber(right.coordinate);
}

function excelColumnNumber(coordinate: string): number {
  const letters = /^[A-Z]+/.exec(coordinate)?.[0] ?? "";
  return [...letters].reduce((value, letter) => value * 26 + letter.charCodeAt(0) - 64, 0);
}

function assignmentLabel(assignment: AssignmentCode): string {
  if (assignment === "HEADER") return "머리글 일치 근거";
  if (assignment.startsWith("IDENTIFIER:")) {
    const kind = assignment.slice("IDENTIFIER:".length) as MappingIdentifierKind;
    return IDENTIFIER_OPTIONS.find((option) => option.value === kind)?.label ?? kind;
  }
  const role = assignment.slice("ROW:".length) as RowRole;
  return ROW_ROLE_OPTIONS.find((option) => option.value === role)?.label ?? role;
}

function validateDraft(
  assignments: Record<string, AssignedCell>,
  effectiveFrom: string,
  effectiveTo: string,
  reason: string,
): string | null {
  const values = Object.values(assignments);
  if (!effectiveFrom) return "적용 시작일을 직접 입력해 주세요.";
  if (effectiveTo && effectiveTo < effectiveFrom) {
    return "적용 종료일은 적용 시작일보다 빠를 수 없습니다.";
  }
  if (!reason) return "Draft 생성 이유를 입력해 주세요.";
  if (!values.some((value) => value.assignment === "HEADER")) {
    return "머리글 일치 근거 셀을 1개 이상 지정해 주세요.";
  }
  for (const required of ["SUPPLIER", "INSPECTION_DATE"] as const) {
    if (!values.some((value) => value.assignment === `IDENTIFIER:${required}`)) {
      return required === "SUPPLIER"
        ? "업체 식별자 셀을 지정해 주세요."
        : "검사일 식별자 셀을 지정해 주세요.";
    }
  }
  const rowGroups = groupRowAssignments(values);
  if (!rowGroups.size) return "검사항목 행을 1개 이상 지정해 주세요.";
  for (const group of rowGroups.values()) {
    if (!group.some((value) => value.assignment === "ROW:item")) {
      return `${group[0]?.source.sheet_name ?? "원본"} ${sourceRowNumber(group[0]?.source ?? { sheet_name: "", coordinate: "A1" })}행에 검사항목 셀이 필요합니다.`;
    }
    if (!group.some((value) => value.assignment === "ROW:sample")) {
      return `${group[0]?.source.sheet_name ?? "원본"} ${sourceRowNumber(group[0]?.source ?? { sheet_name: "", coordinate: "A1" })}행에 샘플값 셀이 1개 이상 필요합니다.`;
    }
  }
  return null;
}

function groupRowAssignments(values: AssignedCell[]): Map<string, AssignedCell[]> {
  const groups = new Map<string, AssignedCell[]>();
  for (const value of values) {
    if (!value.assignment.startsWith("ROW:")) continue;
    const key = sourceRowKey(value.source);
    groups.set(key, [...(groups.get(key) ?? []), value]);
  }
  return groups;
}

function buildDraftRequest({
  assignments,
  projectKey,
  receipt,
  supplierScope,
  effectiveFrom,
  effectiveTo,
  reason,
}: {
  assignments: Record<string, AssignedCell>;
  projectKey: string;
  receipt: IntakeReceipt;
  supplierScope: string;
  effectiveFrom: string;
  effectiveTo: string;
  reason: string;
}): CreateMappingDraftRequest {
  const values = Object.values(assignments).sort((left, right) =>
    compareSources(left.source, right.source),
  );
  const identifiers: MappingIdentifierDraft[] = values
    .filter((value) => value.assignment.startsWith("IDENTIFIER:"))
    .map((value) => ({
      kind: value.assignment.slice("IDENTIFIER:".length) as MappingIdentifierKind,
      source: value.source,
    }));
  const inspectionRows = [...groupRowAssignments(values).values()].map((group) =>
    buildInspectionRow(group),
  );
  return {
    project_key: projectKey,
    receipt_id: receipt.receipt_id,
    content_sha256: receipt.content_sha256,
    supplier_scope: supplierScope,
    effective_from: effectiveFrom,
    effective_to: effectiveTo || null,
    expected_history_row_version: 0,
    reason,
    header_assertion_cells: values
      .filter((value) => value.assignment === "HEADER")
      .map((value) => value.source),
    identifiers,
    inspection_rows: inspectionRows,
  };
}

function buildInspectionRow(group: AssignedCell[]): MappingInspectionRowDraft {
  const sorted = [...group].sort((left, right) => compareSources(left.source, right.source));
  const roles = new Map<RowRole, MappingCellReference>();
  const samples: MappingCellReference[] = [];
  for (const value of sorted) {
    const role = value.assignment.slice("ROW:".length) as RowRole;
    if (role === "sample") samples.push(value.source);
    else roles.set(role, value.source);
  }
  const item = roles.get("item");
  if (!item) throw new Error("validated inspection row lost its item source");
  const row: MappingInspectionRowDraft = {
    row_key: `${item.sheet_name}!ROW:${sourceRowNumber(item)}`,
    item,
    sample_cells: samples,
  };
  for (const role of ROW_ROLE_OPTIONS.map((option) => option.value)) {
    if (role === "item" || role === "sample") continue;
    const source = roles.get(role);
    if (source) row[role] = source;
  }
  return row;
}

function workflowStatusLabel(status: MappingWorkflowSnapshot["workflow"]["status"]): string {
  if (status === "DRAFT") return "Draft 생성됨";
  if (status === "REVIEWED") return "검토 완료됨";
  return "최종 승인됨";
}

function toCommandError(reason: unknown): { title: string; message: string; code: string } {
  if (reason instanceof MappingApiError) {
    const stale = /STALE|ROW_VERSION|CONFLICT/.test(reason.code);
    return {
      title: stale ? "동시 수정 충돌" : reason.statusLabel,
      message: stale
        ? `${reason.message} 최신 row_version을 확인하려면 원본 셀 검토를 다시 시작해 주세요.`
        : reason.message,
      code: reason.code,
    };
  }
  return {
    title: "매핑 명령 오류",
    message: SAFE_REQUEST_ERROR,
    code: "MAPPING_REQUEST_FAILED",
  };
}

function toLongError(reason: unknown): { title: string; message: string; code: string } {
  if (reason instanceof LongApiError) {
    return { title: reason.statusLabel, message: reason.message, code: reason.code };
  }
  return {
    title: "Long 처리 오류",
    message: SAFE_REQUEST_ERROR,
    code: "LONG_REQUEST_FAILED",
  };
}

function longIssueLocation(issue: LongCandidateIssue): string {
  if (issue.sheet_name && issue.coordinate) return `${issue.sheet_name}!${issue.coordinate}`;
  if (issue.row_key) return `row:${issue.row_key}`;
  return issue.scope.toLocaleLowerCase("en-US");
}
