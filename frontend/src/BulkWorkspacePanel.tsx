import {
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  bulkApi,
  BulkApiError,
  type BulkApi,
  type BulkBatchSnapshot,
  type BulkEntrySnapshot,
  type BulkIssue,
  type BulkSummary,
} from "./api/bulk";
import { HistoricalWorkspacePanel } from "./HistoricalWorkspacePanel";

const ACCEPTED_EXTENSIONS = [".xlsx", ".xlsm"];
const SAFE_REQUEST_ERROR = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

interface BulkWorkspacePanelProps {
  projectKey: string;
  supplierScope: string;
  api?: BulkApi;
}

interface DisplayError {
  title: string;
  message: string;
  code: string;
}

export function BulkWorkspacePanel({
  projectKey,
  supplierScope,
  api = bulkApi,
}: BulkWorkspacePanelProps) {
  const [workbooks, setWorkbooks] = useState<File[]>([]);
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [lookupBatchId, setLookupBatchId] = useState(() =>
    readStoredBatchId(projectKey, supplierScope),
  );
  const [batch, setBatch] = useState<BulkBatchSnapshot | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<DisplayError | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const commandControllerRef = useRef<AbortController | null>(null);
  const scopeGenerationRef = useRef(0);

  useEffect(() => {
    scopeGenerationRef.current += 1;
    commandControllerRef.current?.abort();
    setLookupBatchId(readStoredBatchId(projectKey, supplierScope));
    setWorkbooks([]);
    setIdempotencyKey("");
    setBatch(null);
    setFormError(null);
    setRequestError(null);
    setBusy(false);
  }, [projectKey, supplierScope]);

  useEffect(() => () => commandControllerRef.current?.abort(), []);

  useEffect(() => {
    if (!batch || batch.terminal || requestError) return undefined;

    const controller = new AbortController();
    const requestGeneration = scopeGenerationRef.current;
    const delay = Math.max(250, batch.poll_after_ms ?? 500);
    const timer = window.setTimeout(() => {
      void api
        .getBatch(batch.batch_id, batch.project_key, controller.signal)
        .then((snapshot) => {
          if (
            controller.signal.aborted ||
            requestGeneration !== scopeGenerationRef.current
          ) {
            return;
          }
          acceptSnapshot(
            snapshot,
            projectKey,
            supplierScope,
            setBatch,
            setRequestError,
          );
        })
        .catch((error: unknown) => {
          if (
            !isAbortError(error) &&
            requestGeneration === scopeGenerationRef.current
          ) {
            setRequestError(toDisplayError(error, "배치 상태 확인 오류"));
          }
        });
    }, delay);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [api, batch, projectKey, requestError, supplierScope]);

  const chooseFiles = (files: File[]) => {
    const unsupported = files.find((file) => !hasAcceptedExtension(file.name));
    if (unsupported) {
      setFormError(".xlsx 또는 .xlsm 파일만 선택할 수 있습니다.");
      return;
    }
    setWorkbooks(files);
    setIdempotencyKey(files.length > 0 ? createIdempotencyKey() : "");
    setFormError(null);
    setRequestError(null);
    setBatch(null);
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    chooseFiles(Array.from(event.target.files ?? []));
    event.target.value = "";
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (!busy) chooseFiles(Array.from(event.dataTransfer.files));
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!projectKey.trim() || !supplierScope.trim()) {
      setFormError("Project와 Supplier scope가 확인되어야 일괄 분석을 시작할 수 있습니다.");
      return;
    }
    if (workbooks.length === 0) {
      setFormError("일괄 분석할 Excel 원본을 하나 이상 선택해 주세요.");
      return;
    }
    await runRequest(
      (signal) =>
        api.createBatch(
          {
            projectKey,
            supplierScope,
            idempotencyKey: idempotencyKey || createIdempotencyKey(),
            workbooks,
          },
          signal,
        ),
      "배치 등록 오류",
    );
  };

  const reload = async () => {
    const batchId = lookupBatchId.trim();
    if (!projectKey.trim() || !supplierScope.trim()) {
      setFormError("Project와 Supplier scope가 확인되어야 배치를 조회할 수 있습니다.");
      return;
    }
    if (!batchId) {
      setFormError("다시 조회할 배치 ID를 입력해 주세요.");
      return;
    }
    await runRequest((signal) => api.getBatch(batchId, projectKey, signal), "배치 조회 오류");
  };

  const runRequest = async (
    operation: (signal: AbortSignal) => Promise<BulkBatchSnapshot>,
    errorTitle: string,
  ) => {
    commandControllerRef.current?.abort();
    const controller = new AbortController();
    const requestGeneration = scopeGenerationRef.current;
    commandControllerRef.current = controller;
    setBusy(true);
    setFormError(null);
    setRequestError(null);
    try {
      const snapshot = await operation(controller.signal);
      if (
        controller.signal.aborted ||
        requestGeneration !== scopeGenerationRef.current
      ) {
        return;
      }
      if (!acceptSnapshot(snapshot, projectKey, supplierScope, setBatch, setRequestError)) return;
      setLookupBatchId(snapshot.batch_id);
      storeBatchId(projectKey, supplierScope, snapshot.batch_id);
    } catch (error) {
      if (
        !isAbortError(error) &&
        requestGeneration === scopeGenerationRef.current
      ) {
        setRequestError(toDisplayError(error, errorTitle));
      }
    } finally {
      if (
        commandControllerRef.current === controller &&
        requestGeneration === scopeGenerationRef.current
      ) {
        setBusy(false);
      }
    }
  };

  return (
    <section className="bulk-workspace" aria-labelledby="bulk-workspace-heading" aria-busy={busy}>
      <div className="section-heading">
        <div>
          <p className="panel__kicker">BULK STAGING</p>
          <h4 id="bulk-workspace-heading">과거 OQC 일괄 분석</h4>
        </div>
        <span className="section-count">PHASE 2</span>
      </div>

      <div className="mapping-limit-note bulk-boundary">
        승인된 Mapping을 재사용해 원본을 보존하고 예외를 분리하는 단계입니다. 정상 파일은
        개별 승인하지 않습니다. 이 화면은 Long 적재, VALID 지정, 과거본 REPLACED 처리,
        수정본 승인 또는 공식 기준선 확정을 자동 실행하지 않습니다.
      </div>

      <dl className="evidence-grid bulk-scope">
        <Evidence label="Project" value={projectKey} />
        <Evidence label="Supplier scope" value={supplierScope} />
      </dl>

      <form className="bulk-upload" onSubmit={(event) => void submit(event)}>
        <div className="field">
          <label className="field__label" htmlFor="bulk-workbooks">
            과거 OQC 원본 파일<span className="required-mark" aria-hidden="true">*</span>
          </label>
          <div
            className="dropzone bulk-dropzone"
            data-dragging={dragging}
            data-disabled={busy}
            onDragEnter={(event) => {
              event.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
            }}
            onDrop={handleDrop}
          >
            <input
              ref={fileInputRef}
              className="screen-reader-only"
              id="bulk-workbooks"
              type="file"
              accept=".xlsx,.xlsm"
              multiple
              onChange={handleFileChange}
              disabled={busy}
              tabIndex={-1}
              aria-describedby="bulk-file-hint"
            />
            <p className="dropzone__title">여러 Excel 원본을 한 번에 놓으세요</p>
            <p className="dropzone__description" id="bulk-file-hint">
              .xlsx 또는 .xlsm · 파일별 원본과 SHA-256을 별도로 보존합니다.
            </p>
            <button
              className="file-trigger"
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy}
            >
              여러 파일 선택
            </button>
          </div>
          {workbooks.length > 0 ? (
            <SelectedFiles files={workbooks} disabled={busy} onClear={() => chooseFiles([])} />
          ) : null}
          {formError ? <p className="form-error" role="alert">{formError}</p> : null}
        </div>
        <button
          className="submit-button"
          type="submit"
          disabled={busy || workbooks.length === 0 || !projectKey.trim() || !supplierScope.trim()}
        >
          {busy ? "요청 처리 중…" : `원본 ${workbooks.length || 0}개 보존 및 일괄 분석`}
        </button>
      </form>

      <div className="bulk-reload" aria-labelledby="bulk-reload-heading">
        <div>
          <h5 id="bulk-reload-heading">기존 배치 다시 조회</h5>
          <p>서버나 화면을 다시 시작한 뒤에도 영속 배치 ID로 현재 상태를 조회합니다.</p>
        </div>
        <div className="bulk-reload__controls">
          <label className="screen-reader-only" htmlFor="bulk-batch-id">배치 ID</label>
          <input
            className="text-input"
            id="bulk-batch-id"
            value={lookupBatchId}
            onChange={(event) => setLookupBatchId(event.target.value)}
            placeholder="배치 ID"
            autoComplete="off"
            disabled={busy}
          />
          <button
            className="secondary-button"
            type="button"
            onClick={() => void reload()}
            disabled={busy || !projectKey.trim() || !supplierScope.trim()}
          >
            배치 조회
          </button>
        </div>
      </div>

      <div className="screen-reader-only" role="status" aria-live="polite" aria-atomic="true">
        {busy
          ? "대량 OQC 배치 요청을 처리하고 있습니다."
          : requestError
            ? `${requestError.title}: ${requestError.message}`
            : batch
              ? `${batch.status_label}. 총 ${batch.summary.total}개 중 처리 중 ${batch.summary.processing}개, 후보 준비 ${batch.summary.candidate_ready}개입니다.`
              : "대량 분석할 파일을 선택할 수 있습니다."}
      </div>

      {requestError ? <BulkErrorView error={requestError} onRetry={() => void reload()} retryable={Boolean(lookupBatchId.trim())} /> : null}
      {batch ? <BulkBatchView batch={batch} /> : null}
    </section>
  );
}

function SelectedFiles({ files, disabled, onClear }: { files: File[]; disabled: boolean; onClear: () => void }) {
  return (
    <div className="bulk-selected-files">
      <div className="bulk-selected-files__heading">
        <strong>선택한 파일 {files.length}개</strong>
        <button className="text-button" type="button" onClick={onClear} disabled={disabled}>전체 해제</button>
      </div>
      <ul>
        {files.map((file, index) => (
          <li key={`${file.name}:${file.size}:${file.lastModified}:${index}`}>
            <span title={file.name}>{file.name}</span><small>{formatBytes(file.size)}</small>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BulkErrorView({ error, onRetry, retryable }: { error: DisplayError; onRetry: () => void; retryable: boolean }) {
  return (
    <div className="mapping-command-error bulk-error" role="alert">
      <strong>{error.title}</strong><span>{error.message}</span><code>{error.code}</code>
      {retryable ? <button className="secondary-button" type="button" onClick={onRetry}>배치 상태 다시 조회</button> : null}
    </div>
  );
}

function BulkBatchView({ batch }: { batch: BulkBatchSnapshot }) {
  return (
    <div className="bulk-result">
      <div className="status-card" data-tone={batchTone(batch)}>
        <div className="status-card__content">
          <p className="status-card__label">대량 분석 배치</p>
          <h5 className="status-card__title">{batch.status_label}</h5>
          <p className="status-card__message">{batch.message}</p>
          {!batch.terminal ? <div className="progress-track" aria-hidden="true" /> : null}
        </div>
      </div>

      <dl className="evidence-grid bulk-batch-proof">
        <Evidence label="배치 ID" value={batch.batch_id} wide />
        <Evidence label="생성 시각" value={batch.created_at} />
        <Evidence label="최종 갱신" value={batch.updated_at} />
        <Evidence label="완료 시각" value={batch.finished_at ?? "진행 중"} />
        <Evidence label="멱등 재실행" value={batch.replayed ? "기존 배치 재사용" : "새 배치"} />
      </dl>

      <BulkSummaryView summary={batch.summary} />
      <CapabilityBoundary batch={batch} />

      <div className="bulk-limits" aria-label="서버 등록 한도">
        <span>최대 {batch.limits.max_files}개</span>
        <span>파일당 {formatBytes(batch.limits.max_file_bytes)}</span>
        <span>배치 합계 {formatBytes(batch.limits.max_batch_bytes)}</span>
      </div>

      <div className="section-heading bulk-entry-heading">
        <h5>파일별 결과와 예외 근거</h5>
        <span className="section-count">{batch.entries.length}</span>
      </div>
      <div className="bulk-entry-list">
        {batch.entries.map((entry) => <BulkEntryCard key={entry.entry_id} entry={entry} />)}
      </div>
      <HistoricalWorkspacePanel
        batchId={batch.batch_id}
        projectKey={batch.project_key}
        supplierScope={batch.supplier_scope}
        batchTerminal={batch.terminal}
      />
    </div>
  );
}

function BulkSummaryView({ summary }: { summary: BulkSummary }) {
  const metrics: Array<[string, number, "normal" | "warning" | "danger" | "muted"]> = [
    ["총 파일", summary.total, "muted"],
    ["처리 대기", summary.staged, "muted"],
    ["처리 중", summary.processing, "muted"],
    ["후보 준비", summary.candidate_ready, "normal"],
    ["중복 후보", summary.duplicate, "warning"],
    ["양식 변화", summary.variation, "warning"],
    ["Mapping 필요", summary.mapping_required, "warning"],
    ["스캔 실패", summary.scan_failed, "danger"],
    ["식별 보류", summary.identifier_hold, "warning"],
    ["Binding 보류", summary.binding_hold, "warning"],
    ["수정본 검토 필요", summary.revision_review_required, "danger"],
    ["처리 오류", summary.error, "danger"],
  ];
  return (
    <div className="bulk-summary" aria-label="대량 분석 요약">
      {metrics.map(([label, value, tone]) => (
        <div className="bulk-summary__item" data-tone={tone} key={label}>
          <span>{label}</span><strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}

function CapabilityBoundary({ batch }: { batch: BulkBatchSnapshot }) {
  const unsafeCapability =
    batch.capabilities.per_file_approval ||
    batch.capabilities.finalize_available ||
    batch.capabilities.auto_long ||
    batch.capabilities.auto_valid ||
    batch.capabilities.auto_replaced ||
    batch.capabilities.auto_revision ||
    batch.capabilities.ai_used;
  return (
    <div className="bulk-capabilities" data-warning={unsafeCapability}>
      <strong>{unsafeCapability ? "자동 처리 경계 확인 필요" : "자동 반영 없는 Bulk 등록 경계"}</strong>
      <ul>
        <li>원본 영속 보존: {yesNo(batch.capabilities.durable_staging)}</li>
        <li>승인 Mapping exact 재사용: {yesNo(batch.capabilities.approved_template_reuse)}</li>
        <li>파일별 승인: {enabledDisabled(batch.capabilities.per_file_approval)}</li>
        <li>Bulk 등록 단계 자동 일괄 반영: {enabledDisabled(batch.capabilities.finalize_available)}</li>
        <li>Long·VALID 자동 반영: {enabledDisabled(batch.capabilities.auto_long || batch.capabilities.auto_valid)}</li>
        <li>REPLACED·수정본 자동 승인: {enabledDisabled(batch.capabilities.auto_replaced || batch.capabilities.auto_revision)}</li>
        <li>AI 호출: {enabledDisabled(batch.capabilities.ai_used)}</li>
      </ul>
    </div>
  );
}

function BulkEntryCard({ entry }: { entry: BulkEntrySnapshot }) {
  const isException = entry.outcome !== null && entry.outcome !== "CANDIDATE_READY";
  return (
    <article className="bulk-entry" data-tone={entryTone(entry)}>
      <div className="bulk-entry__summary">
        <span className="bulk-entry__ordinal">{entry.ordinal + 1}</span>
        <div>
          <h6 title={entry.filename}>{entry.filename}</h6>
          <p>{entry.status_label} · {entry.message}</p>
        </div>
        <span className="bulk-outcome">{entry.outcome ?? entry.status}</span>
      </div>
      <details>
        <summary>{isException ? `예외 근거 보기 (${entry.issues.length})` : "원본·후보 근거 보기"}</summary>
        <div className="bulk-entry__details">
          <dl className="evidence-grid">
            <Evidence label="Upload SHA-256" value={entry.upload_sha256} wide />
            <Evidence label="MIME" value={entry.mime_type} />
            <Evidence label="파일 크기" value={formatBytes(entry.size_bytes)} />
            <Evidence label="시도 횟수" value={String(entry.attempt_count)} />
            <Evidence label="row_version" value={String(entry.row_version)} />
            {entry.duplicate_of_entry_id ? <Evidence label="중복 기준 entry" value={entry.duplicate_of_entry_id} /> : null}
            {entry.revision_baseline_entry_id ? <Evidence label="수정본 비교 기준 entry" value={entry.revision_baseline_entry_id} /> : null}
          </dl>
          {entry.receipt ? <ReceiptEvidence entry={entry} /> : null}
          {entry.mapping ? <MappingEvidence entry={entry} /> : null}
          {entry.candidate ? <CandidateEvidence entry={entry} /> : null}
          {entry.issues.length > 0 ? <IssueEvidence issues={entry.issues} filename={entry.filename} /> : (
            <p className="empty-value">기록된 예외가 없습니다. 별도 승인 없이 후보 준비 상태만 표시합니다.</p>
          )}
        </div>
      </details>
    </article>
  );
}

function ReceiptEvidence({ entry }: { entry: BulkEntrySnapshot }) {
  const receipt = entry.receipt;
  if (!receipt) return null;
  return (
    <div className="bulk-proof-group">
      <h6>원본 보존 Receipt</h6>
      <dl className="evidence-grid">
        <Evidence label="Receipt ID" value={receipt.receipt_id} />
        <Evidence label="원본 파일명" value={receipt.original_filename} />
        <Evidence label="Content SHA-256" value={receipt.content_sha256} wide />
        <Evidence label="수신 시각" value={receipt.received_at} />
        <Evidence label="보존 크기" value={formatBytes(receipt.size_bytes)} />
      </dl>
    </div>
  );
}

function MappingEvidence({ entry }: { entry: BulkEntrySnapshot }) {
  const mapping = entry.mapping;
  if (!mapping) return null;
  return (
    <div className="bulk-proof-group">
      <h6>적용된 승인 Mapping</h6>
      <dl className="evidence-grid">
        <Evidence label="Template" value={`${mapping.template_id} · rev.${mapping.revision}`} />
        <Evidence label="Template SHA-256" value={mapping.template_sha256} wide />
        <Evidence label="적용 기간" value={`${mapping.effective_from} ~ ${mapping.effective_to ?? "종료일 없음"}`} />
        <Evidence label="History row_version" value={String(mapping.history_row_version)} />
        <Evidence label="Revision row_version" value={String(mapping.revision_row_version)} />
      </dl>
    </div>
  );
}

function CandidateEvidence({ entry }: { entry: BulkEntrySnapshot }) {
  const candidate = entry.candidate;
  if (!candidate) return null;
  return (
    <div className="bulk-proof-group">
      <h6>읽기 전용 Long 후보 근거</h6>
      <dl className="evidence-grid">
        <Evidence label="후보 상태" value={candidate.state} />
        <Evidence label="적재 가능 / 보류 행" value={`${candidate.loadable_row_count} / ${candidate.held_row_count}`} />
        <Evidence label="Candidate digest" value={candidate.candidate_digest} wide />
        <Evidence label="Revision identity SHA-256" value={candidate.revision_identity_sha256} wide />
        <Evidence label="Revision evidence SHA-256" value={candidate.revision_evidence_sha256} wide />
      </dl>
    </div>
  );
}

function IssueEvidence({ issues, filename }: { issues: BulkIssue[]; filename: string }) {
  return (
    <div className="bulk-proof-group">
      <h6>Typed 예외</h6>
      <div className="sheet-table-wrap">
        <table className="bulk-issue-table">
          <caption className="screen-reader-only">{filename} 예외 근거</caption>
          <thead><tr><th>분류 / 중대도</th><th>코드·설명</th><th>원본 위치</th><th>비교 근거</th></tr></thead>
          <tbody>
            {issues.map((issue, index) => (
              <tr key={`${issue.code}:${issue.location ?? ""}:${index}`}>
                <td><span className="issue-badge" data-severity={issue.severity}>{issue.category} · {issue.severity}</span></td>
                <td><code>{issue.code}</code><p>{issue.message}</p></td>
                <td>
                  <span>{issue.location ?? "위치 없음"}</span>
                  {issue.evidence_path ? <small>{issue.evidence_path}</small> : null}
                  {issue.baseline_entry_id ? <small>기준 entry: {issue.baseline_entry_id}</small> : null}
                </td>
                <td>
                  {issue.expected_json !== null ? <EvidenceValue label="기대" value={issue.expected_json} /> : null}
                  {issue.observed_json !== null ? <EvidenceValue label="관찰" value={issue.observed_json} /> : null}
                  {issue.expected_json === null && issue.observed_json === null ? <span className="empty-value">비교값 없음</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EvidenceValue({ label, value }: { label: string; value: unknown }) {
  return <span className="bulk-json-evidence"><strong>{label}</strong><code>{formatJson(value)}</code></span>;
}

function Evidence({ label, value, wide = false }: { label: string; value: string; wide?: boolean }) {
  return <div className={wide ? "evidence-item evidence-item--wide" : "evidence-item"}><dt>{label}</dt><dd title={value}>{value}</dd></div>;
}

function acceptSnapshot(
  snapshot: BulkBatchSnapshot,
  projectKey: string,
  supplierScope: string,
  setBatch: (snapshot: BulkBatchSnapshot | null) => void,
  setError: (error: DisplayError | null) => void,
): boolean {
  if (snapshot.project_key !== projectKey || snapshot.supplier_scope !== supplierScope) {
    setBatch(null);
    setError({
      title: "배치 조회 오류",
      message: "조회된 배치의 Project 또는 Supplier scope가 현재 화면과 일치하지 않습니다.",
      code: "BULK_SCOPE_MISMATCH",
    });
    return false;
  }
  setBatch(snapshot);
  return true;
}

function toDisplayError(error: unknown, title: string): DisplayError {
  if (error instanceof BulkApiError) {
    return { title: error.statusLabel || title, message: error.message, code: error.code };
  }
  return { title, message: SAFE_REQUEST_ERROR, code: "BULK_REQUEST_FAILED" };
}

function hasAcceptedExtension(filename: string): boolean {
  const lower = filename.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((extension) => lower.endsWith(extension));
}

function createIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  return `bulk-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function storageKey(projectKey: string, supplierScope: string): string {
  return `mass-production-quality-validation:bulk:${projectKey}:${supplierScope}`;
}

function readStoredBatchId(projectKey: string, supplierScope: string): string {
  try {
    return window.localStorage.getItem(storageKey(projectKey, supplierScope)) ?? "";
  } catch {
    return "";
  }
}

function storeBatchId(projectKey: string, supplierScope: string, batchId: string): void {
  try {
    window.localStorage.setItem(storageKey(projectKey, supplierScope), batchId);
  } catch {
    // 조회 입력에는 이미 batch ID가 남아 있으므로 storage 실패가 workflow를 막지 않는다.
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function formatJson(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return "표시할 수 없는 비교 근거";
  }
}

function yesNo(value: boolean): string { return value ? "확인됨" : "확인 안 됨"; }
function enabledDisabled(value: boolean): string { return value ? "사용" : "사용 안 함"; }

function batchTone(batch: BulkBatchSnapshot): "normal" | "warning" | "danger" {
  if (batch.status === "FAILED") return "danger";
  if (batch.status === "COMPLETED_WITH_EXCEPTIONS") return "warning";
  return "normal";
}

function entryTone(entry: BulkEntrySnapshot): "normal" | "warning" | "danger" | "muted" {
  if (entry.status !== "TERMINAL") return "muted";
  if (entry.outcome === "CANDIDATE_READY") return "normal";
  if (entry.outcome === "SCAN_FAILED" || entry.outcome === "REVISION_REVIEW_REQUIRED" || entry.outcome === "ERROR") return "danger";
  return "warning";
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
