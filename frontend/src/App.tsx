import {
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  intakeApi,
  IntakeApiError,
  type IntakeApi,
  type IntakeIssue,
  type IntakeJobSnapshot,
  type IntakeSheet,
} from "./api/intake";
import type { ConfigurationApi } from "./api/configuration";
import type { DataReviewApi } from "./api/dataReview";
import type { MappingApi } from "./api/mapping";
import type { LongApi } from "./api/long";
import { MappingWorkspacePanel } from "./MappingWorkspacePanel";

const ACCEPTED_EXTENSIONS = [".xlsx", ".xlsm"];
const SAFE_REQUEST_ERROR = "요청을 처리하지 못했습니다. 다시 시도해 주세요.";

interface AppProps {
  api?: IntakeApi;
  mappingApi?: MappingApi;
  longApi?: LongApi;
  dataReviewApi?: DataReviewApi;
  configurationApi?: ConfigurationApi;
}

interface DisplayError {
  title: string;
  message: string;
  code: string;
}

export function App({
  api = intakeApi,
  mappingApi,
  longApi,
  dataReviewApi,
  configurationApi,
}: AppProps) {
  const [projectKey, setProjectKey] = useState("");
  const [modelHint, setModelHint] = useState("");
  const [lotHint, setLotHint] = useState("");
  const [workbook, setWorkbook] = useState<File | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<DisplayError | null>(null);
  const [job, setJob] = useState<IntakeJobSnapshot | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [pollGeneration, setPollGeneration] = useState(0);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const busy = submitting || Boolean(job && !job.terminal && !requestError);

  useEffect(() => {
    if (!job || job.terminal || requestError) {
      return undefined;
    }

    const controller = new AbortController();
    const delay = Math.max(250, job.poll_after_ms ?? 500);
    const timer = window.setTimeout(() => {
      void api
        .getJob(job.job_id, job.project_key, controller.signal)
        .then((snapshot) => {
          setJob(snapshot);
        })
        .catch((error: unknown) => {
          if (isAbortError(error)) {
            return;
          }
          setRequestError(toDisplayError(error, "상태 확인 오류"));
        });
    }, delay);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [api, job, pollGeneration, requestError]);

  const chooseFile = useCallback((file: File | null) => {
    setFormError(null);
    setRequestError(null);
    if (!file) {
      setWorkbook(null);
      return;
    }
    if (!hasAcceptedExtension(file.name)) {
      setWorkbook(null);
      setFormError(".xlsx 또는 .xlsm 형식의 OQC 원본만 선택할 수 있습니다.");
      return;
    }
    setWorkbook(file);
  }, []);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    chooseFile(event.target.files?.[0] ?? null);
    event.target.value = "";
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (busy) {
      return;
    }
    chooseFile(event.dataTransfer.files?.[0] ?? null);
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedProjectKey = projectKey.trim();
    if (!normalizedProjectKey) {
      setFormError("프로젝트 키를 입력해 주세요.");
      return;
    }
    if (!workbook) {
      setFormError("스캔할 OQC 원본을 선택해 주세요.");
      return;
    }

    setFormError(null);
    setRequestError(null);
    setSubmitting(true);
    try {
      const snapshot = await api.createJob({
        projectKey: normalizedProjectKey,
        workbook,
        modelHint: optionalText(modelHint),
        lotHint: optionalText(lotHint),
      });
      setProjectKey(snapshot.project_key);
      setJob(snapshot);
    } catch (error) {
      if (!isAbortError(error)) {
        setRequestError(toDisplayError(error, "접수 요청 오류"));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const retryPoll = () => {
    setRequestError(null);
    setPollGeneration((value) => value + 1);
  };

  const reset = () => {
    setJob(null);
    setWorkbook(null);
    setModelHint("");
    setLotHint("");
    setFormError(null);
    setRequestError(null);
    setDragging(false);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar__inner">
          <div className="brand" aria-label="Mass Production Quality Validation">
            <span className="brand__mark" aria-hidden="true">
              DQ
            </span>
            <div>
              <p className="brand__name">Mass Production Quality Validation</p>
              <p className="brand__context">OQC Data Engine</p>
            </div>
          </div>
          <div className="local-badge" aria-label="개인 로컬 실행 중">
            <span>개인 로컬 확장팩</span>
          </div>
        </div>
      </header>

      <main className="page">
        <section className="hero" aria-labelledby="page-title">
          <div>
            <p className="eyebrow">Manual intake</p>
            <h1 id="page-title">OQC 원본을 안전하게 접수하세요</h1>
            <p className="hero__lead">
              프로젝트와 원본 파일을 지정하면 먼저 원본을 보존하고, 별도 작업에서 구조를
              스캔합니다. 완료 후 시트와 경고 근거를 한 화면에서 확인할 수 있습니다.
            </p>
          </div>
          <aside className="boundary-note" aria-label="현재 기능 범위">
            <strong>현재 단계 안내</strong>
            이 화면은 원본 보존과 스캔 근거를 확인하는 곳입니다. 매핑 검토 전에는 공식
            데이터로 등록되지 않으며 품질 결과를 확정하지 않습니다.
          </aside>
        </section>

        <div className="workflow-grid">
          <section className="panel" aria-labelledby="intake-heading">
            <header className="panel__header">
              <div>
                <p className="panel__kicker">SOURCE INTAKE</p>
                <h2 className="panel__title" id="intake-heading">
                  원본 선택
                </h2>
              </div>
              <span className="step-badge" aria-label="첫 번째 단계">
                01
              </span>
            </header>

            <form className="panel__body" onSubmit={handleSubmit} noValidate>
              <div className="field">
                <label htmlFor="project-key">
                  프로젝트 키<span className="required-mark" aria-hidden="true">*</span>
                </label>
                <input
                  className="text-input"
                  id="project-key"
                  name="project_key"
                  value={projectKey}
                  onChange={(event) => setProjectKey(event.target.value)}
                  disabled={busy}
                  required
                  autoComplete="off"
                  aria-describedby="project-key-hint"
                />
                <p className="field__hint" id="project-key-hint">
                  이 원본을 보관할 개인 프로젝트를 정확히 입력하세요.
                </p>
              </div>

              <div className="field-row">
                <div className="field">
                  <label htmlFor="model-hint">모델 힌트</label>
                  <input
                    className="text-input"
                    id="model-hint"
                    value={modelHint}
                    onChange={(event) => setModelHint(event.target.value)}
                    disabled={busy}
                    placeholder="선택 입력"
                    autoComplete="off"
                  />
                </div>
                <div className="field">
                  <label htmlFor="lot-hint">LOT 힌트</label>
                  <input
                    className="text-input"
                    id="lot-hint"
                    value={lotHint}
                    onChange={(event) => setLotHint(event.target.value)}
                    disabled={busy}
                    placeholder="선택 입력"
                    autoComplete="off"
                  />
                </div>
              </div>

              <div className="field">
                <label className="field__label" htmlFor="workbook-file">
                  OQC 원본 파일<span className="required-mark" aria-hidden="true">*</span>
                </label>
                <div
                  className="dropzone"
                  data-dragging={dragging}
                  data-disabled={busy}
                  onDragEnter={(event) => {
                    event.preventDefault();
                    if (!busy) setDragging(true);
                  }}
                  onDragOver={(event) => event.preventDefault()}
                  onDragLeave={(event) => {
                    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                      setDragging(false);
                    }
                  }}
                  onDrop={handleDrop}
                >
                  <input
                    ref={fileInputRef}
                    className="screen-reader-only"
                    id="workbook-file"
                    type="file"
                    accept=".xlsx,.xlsm"
                    onChange={handleFileChange}
                    disabled={busy}
                    tabIndex={-1}
                    aria-describedby="file-format-hint"
                  />
                  {workbook ? (
                    <SelectedFile
                      file={workbook}
                      disabled={busy}
                      onRemove={() => chooseFile(null)}
                    />
                  ) : (
                    <div>
                      <span className="dropzone__icon" aria-hidden="true">
                        <Icon name="upload" />
                      </span>
                      <p className="dropzone__title">파일을 여기에 놓으세요</p>
                      <p className="dropzone__description" id="file-format-hint">
                        Excel .xlsx 또는 .xlsm · 한 번에 한 파일
                      </p>
                      <button
                        className="file-trigger"
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={busy}
                      >
                        파일 찾아보기
                      </button>
                    </div>
                  )}
                </div>
                {formError ? (
                  <p className="form-error" role="alert">
                    {formError}
                  </p>
                ) : null}
              </div>

              <button className="submit-button" type="submit" disabled={busy}>
                <Icon name={busy ? "spinner" : "scan"} />
                {submitting ? "접수 요청 중…" : busy ? "스캔 작업 진행 중" : "원본 보존 및 스캔 시작"}
              </button>

              <ol className="process-steps" aria-label="처리 순서">
                <li>원본 보존</li>
                <li>구조 스캔</li>
                <li>매핑 검토 대기</li>
              </ol>
            </form>
          </section>

          <section className="panel" aria-labelledby="status-heading" aria-busy={busy}>
            <header className="panel__header">
              <div>
                <p className="panel__kicker">INTAKE EVIDENCE</p>
                <h2 className="panel__title" id="status-heading">
                  접수 상태와 근거
                </h2>
              </div>
              <span className="step-badge" aria-label="두 번째 단계">
                02
              </span>
            </header>

            <div role="status" aria-live="polite" aria-atomic="true" className="screen-reader-only">
              {liveMessage(submitting, requestError, job)}
            </div>

            {requestError ? (
              <RequestErrorView
                error={requestError}
                canRetry={Boolean(job && !job.terminal)}
                onRetry={retryPoll}
                onReset={reset}
              />
            ) : job ? (
              <JobResult
                job={job}
                onReset={reset}
                mappingApi={mappingApi}
                longApi={longApi}
                dataReviewApi={dataReviewApi}
                configurationApi={configurationApi}
              />
            ) : submitting ? (
              <LoadingView title="접수 요청을 전달하고 있습니다" />
            ) : (
              <EmptyStatus />
            )}
          </section>
        </div>
      </main>
    </div>
  );
}

function SelectedFile({
  file,
  disabled,
  onRemove,
}: {
  file: File;
  disabled: boolean;
  onRemove: () => void;
}) {
  const extension = file.name.split(".").pop()?.toUpperCase() ?? "XLSX";
  return (
    <div className="selected-file">
      <span className="selected-file__type" aria-hidden="true">
        {extension}
      </span>
      <div className="selected-file__content">
        <p className="selected-file__name" title={file.name}>
          {file.name}
        </p>
        <p className="selected-file__meta">{formatBytes(file.size)} · 선택 완료</p>
      </div>
      <button
        className="icon-button"
        type="button"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`${file.name} 선택 해제`}
      >
        <Icon name="close" />
      </button>
    </div>
  );
}

function EmptyStatus() {
  return (
    <div className="status-placeholder">
      <div>
        <div className="status-placeholder__art" aria-hidden="true">
          <Icon name="sheet" size={39} />
        </div>
        <h2>아직 접수한 원본이 없습니다</h2>
        <p>
          왼쪽에서 프로젝트와 OQC 원본을 선택하세요. 작업이 시작되면 보존 여부와 시트 구조가
          이곳에 표시됩니다.
        </p>
      </div>
    </div>
  );
}

function LoadingView({ title }: { title: string }) {
  return (
    <div className="status-card">
      <div className="status-card__row">
        <span className="status-card__icon" aria-hidden="true">
          <Icon name="spinner" />
        </span>
        <div className="status-card__content">
          <p className="status-card__label">현재 상태</p>
          <h3 className="status-card__title">{title}</h3>
          <p className="status-card__message">잠시만 기다려 주세요.</p>
        </div>
      </div>
      <div className="progress-track" aria-hidden="true" />
    </div>
  );
}

function RequestErrorView({
  error,
  canRetry,
  onRetry,
  onReset,
}: {
  error: DisplayError;
  canRetry: boolean;
  onRetry: () => void;
  onReset: () => void;
}) {
  return (
    <div>
      <div className="status-card" data-tone="danger" role="alert">
        <div className="status-card__row">
          <span className="status-card__icon" aria-hidden="true">
            <Icon name="alert" />
          </span>
          <div className="status-card__content">
            <p className="status-card__label">요청을 완료하지 못했습니다</p>
            <h3 className="status-card__title">{error.title}</h3>
            <p className="status-card__message">{error.message}</p>
            <span className="status-code">{error.code}</span>
          </div>
        </div>
      </div>
      <div className="result-content">
        <button className="secondary-button" type="button" onClick={canRetry ? onRetry : onReset}>
          <Icon name="refresh" size={16} />
          {canRetry ? "상태 다시 확인" : "다른 파일 선택"}
        </button>
      </div>
    </div>
  );
}

function JobResult({
  job,
  onReset,
  mappingApi,
  longApi,
  dataReviewApi,
  configurationApi,
}: {
  job: IntakeJobSnapshot;
  onReset: () => void;
  mappingApi?: MappingApi;
  longApi?: LongApi;
  dataReviewApi?: DataReviewApi;
  configurationApi?: ConfigurationApi;
}) {
  const active = !job.terminal;
  const tone = statusTone(job.status);

  return (
    <div>
      <div className="status-card" data-tone={tone}>
        <div className="status-card__row">
          <span className="status-card__icon" aria-hidden="true">
            <Icon name={statusIcon(job.status)} />
          </span>
          <div className="status-card__content">
            <p className="status-card__label">현재 상태</p>
            <h3 className="status-card__title">{job.status_label}</h3>
            <p className="status-card__message">{job.message}</p>
          </div>
        </div>
        {active ? <div className="progress-track" aria-hidden="true" /> : null}
      </div>

      {job.receipt || job.scan || job.issues.length > 0 || job.terminal ? (
        <div className="result-content">
          {job.receipt ? (
            <>
              <div className="boundary-banner">
                <Icon name="info" size={17} />
                <span>
                  원본 보존 기록과 스캔 근거입니다. 매핑 검토가 끝나기 전에는 공식 데이터로
                  등록되지 않습니다.
                </span>
              </div>
              <ReceiptSummary job={job} />
            </>
          ) : null}

          {job.issues.length > 0 ? <IssueList issues={job.issues} /> : null}
          {job.scan ? <SheetSummary scan={job.scan} /> : null}

          {job.status === "MAPPING_REQUIRED" && job.receipt ? (
            <MappingWorkspacePanel
              projectKey={job.project_key}
              receipt={job.receipt}
              api={mappingApi}
              longApi={longApi}
              dataReviewApi={dataReviewApi}
              configurationApi={configurationApi}
            />
          ) : null}

          {job.terminal ? (
            <button className="secondary-button" type="button" onClick={onReset}>
              <Icon name="refresh" size={16} />새 원본 접수
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ReceiptSummary({ job }: { job: IntakeJobSnapshot }) {
  const receipt = job.receipt;
  if (!receipt) return null;

  return (
    <>
      <div className="section-heading">
        <h3>원본 보존 기록</h3>
        <span className="section-count">Receipt</span>
      </div>
      <dl className="evidence-grid">
        <div className="evidence-item">
          <dt>원본 파일명</dt>
          <dd title={receipt.original_filename}>{receipt.original_filename}</dd>
        </div>
        <div className="evidence-item">
          <dt>수신 시각</dt>
          <dd>{formatDateTime(receipt.received_at)}</dd>
        </div>
        <div className="evidence-item">
          <dt>Receipt ID</dt>
          <dd title={receipt.receipt_id}>{receipt.receipt_id}</dd>
        </div>
        <div className="evidence-item">
          <dt>원본 크기</dt>
          <dd>{formatBytes(receipt.size_bytes)}</dd>
        </div>
        <div className="evidence-item evidence-item--wide">
          <dt>SHA-256</dt>
          <dd className="hash-value">{receipt.content_sha256}</dd>
        </div>
        <CandidateTags label="모델 후보" values={receipt.model_candidates} />
        <CandidateTags label="LOT 후보" values={receipt.lot_candidates} />
      </dl>
    </>
  );
}

function CandidateTags({ label, values }: { label: string; values: string[] }) {
  return (
    <div className="evidence-item">
      <dt>{label}</dt>
      <dd>
        {values.length ? (
          <ul className="tag-list" aria-label={label}>
            {values.map((value, index) => (
              <li className="tag" key={`${value}-${index}`} title={value}>
                {value}
              </li>
            ))}
          </ul>
        ) : (
          <span className="empty-value">확인된 후보 없음</span>
        )}
      </dd>
    </div>
  );
}

function IssueList({ issues }: { issues: IntakeIssue[] }) {
  return (
    <>
      <div className="section-heading">
        <h3>확인할 경고</h3>
        <span className="section-count">{issues.length}건</span>
      </div>
      <ul className="warning-list">
        {issues.map((issue, index) => (
          <li className="warning-item" key={`${issue.code}-${issue.location ?? "global"}-${index}`}>
            <Icon name="warning" size={16} />
            <span>
              {issue.message}
              {issue.location ? ` · ${issue.location}` : ""}
            </span>
            <span className="warning-item__code">{issue.code}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function SheetSummary({ scan }: { scan: NonNullable<IntakeJobSnapshot["scan"]> }) {
  return (
    <>
      <div className="section-heading">
        <h3>시트 구조</h3>
        <span className="section-count">{scan.sheet_count}개</span>
      </div>
      <div className="sheet-table-wrap" role="region" aria-label="스캔된 시트 구조" tabIndex={0}>
        <table className="sheet-table">
          <caption className="screen-reader-only">
            시트명, 종류, 표시 상태, 사용 범위, 병합 영역, 보호 여부와 경고 코드
          </caption>
          <thead>
            <tr>
              <th scope="col">시트명</th>
              <th scope="col">종류</th>
              <th scope="col">표시 상태</th>
              <th scope="col">사용 범위</th>
              <th scope="col">병합 영역</th>
              <th scope="col">보호</th>
              <th scope="col">경고</th>
            </tr>
          </thead>
          <tbody>
            {scan.sheets.map((sheet, index) => (
              <SheetRow sheet={sheet} key={`${sheet.name}-${index}`} />
            ))}
          </tbody>
        </table>
      </div>

      <details>
        <summary className="secondary-button">스캔 무결성 정보</summary>
        <dl className="evidence-grid" style={{ marginTop: 10 }}>
          <div className="evidence-item">
            <dt>스캔 원본 크기</dt>
            <dd>{formatBytes(scan.source_size_bytes)}</dd>
          </div>
          <div className="evidence-item">
            <dt>시트 수</dt>
            <dd>{scan.sheet_count}개</dd>
          </div>
          <div className="evidence-item evidence-item--wide">
            <dt>스캔 전 SHA-256</dt>
            <dd className="hash-value">{scan.sha256_before}</dd>
          </div>
          <div className="evidence-item evidence-item--wide">
            <dt>스캔 후 SHA-256</dt>
            <dd className="hash-value">{scan.sha256_after}</dd>
          </div>
        </dl>
      </details>
    </>
  );
}

function SheetRow({ sheet }: { sheet: IntakeSheet }) {
  const visibility = sheetState(sheet.state);
  return (
    <tr>
      <td className="sheet-table__name">{sheet.name}</td>
      <td>{sheetKind(sheet.kind)}</td>
      <td>
        <span className="visibility-pill" data-visibility={visibility.dataValue}>
          {visibility.label}
        </span>
      </td>
      <td>{sheet.used_range ?? "사용 범위 없음"}</td>
      <td>{sheet.merged_ranges.length ? sheet.merged_ranges.join(", ") : "없음"}</td>
      <td>{sheet.protected ? "보호됨" : "보호 없음"}</td>
      <td className="sheet-warning-summary">
        {sheet.issue_codes.length ? sheet.issue_codes.join(", ") : "없음"}
      </td>
    </tr>
  );
}

function Icon({
  name,
  size = 20,
}: {
  name: "alert" | "check" | "close" | "info" | "refresh" | "scan" | "sheet" | "spinner" | "upload" | "warning";
  size?: number;
}) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  const paths: Record<typeof name, ReactNode> = {
    alert: <><path d="M12 8v5" /><path d="M12 17h.01" /><path d="M10.3 3.8 2.4 17.5A2 2 0 0 0 4.1 20h15.8a2 2 0 0 0 1.7-2.5L13.7 3.8a2 2 0 0 0-3.4 0Z" /></>,
    check: <><path d="m5 12 4 4L19 6" /></>,
    close: <><path d="m7 7 10 10M17 7 7 17" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
    refresh: <><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /></>,
    scan: <><path d="M4 7V5a1 1 0 0 1 1-1h2M17 4h2a1 1 0 0 1 1 1v2M20 17v2a1 1 0 0 1-1 1h-2M7 20H5a1 1 0 0 1-1-1v-2M7 12h10" /></>,
    sheet: <><path d="M6 2h8l4 4v16H6z" /><path d="M14 2v5h5M9 12h6M9 16h6" /></>,
    spinner: <><path d="M21 12a9 9 0 1 1-3-6.7" /></>,
    upload: <><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" /><path d="M5 14v5h14v-5" /></>,
    warning: <><path d="M12 8v5M12 17h.01" /><path d="M10.3 3.8 2.4 17.5A2 2 0 0 0 4.1 20h15.8a2 2 0 0 0 1.7-2.5L13.7 3.8a2 2 0 0 0-3.4 0Z" /></>,
  };
  return <svg {...common}>{paths[name]}</svg>;
}

function hasAcceptedExtension(filename: string): boolean {
  const normalized = filename.toLocaleLowerCase("en-US");
  return ACCEPTED_EXTENSIONS.some((extension) => normalized.endsWith(extension));
}

function optionalText(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "크기 확인 불가";
  if (bytes < 1024) return `${bytes.toLocaleString("ko-KR")} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "수신 시각 확인 불가";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function toDisplayError(error: unknown, defaultTitle: string): DisplayError {
  if (error instanceof IntakeApiError) {
    return {
      title: error.statusLabel,
      message: error.message,
      code: error.code,
    };
  }
  return {
    title: defaultTitle,
    message: SAFE_REQUEST_ERROR,
    code: "INTAKE_REQUEST_FAILED",
  };
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function statusTone(status: IntakeJobSnapshot["status"]): "normal" | "warning" | "danger" {
  if (status === "RAW_PRESERVED_SCAN_FAILED") return "warning";
  if (status === "ERROR") return "danger";
  return "normal";
}

function statusIcon(status: IntakeJobSnapshot["status"]): Parameters<typeof Icon>[0]["name"] {
  if (status === "QUEUED" || status === "PROCESSING") return "spinner";
  if (status === "MAPPING_REQUIRED") return "info";
  if (status === "RAW_PRESERVED_SCAN_FAILED") return "warning";
  return "alert";
}

function liveMessage(
  submitting: boolean,
  requestError: DisplayError | null,
  job: IntakeJobSnapshot | null,
): string {
  if (requestError) return `${requestError.title}. ${requestError.message}`;
  if (submitting) return "접수 요청 중입니다.";
  if (job) return `${job.status_label}. ${job.message}`;
  return "원본을 선택할 수 있습니다.";
}

function sheetState(state: string): { label: string; dataValue: string } {
  switch (state.toLocaleUpperCase("en-US")) {
    case "VISIBLE":
      return { label: "표시", dataValue: "visible" };
    case "HIDDEN":
      return { label: "숨김", dataValue: "hidden" };
    case "VERY_HIDDEN":
    case "VERYHIDDEN":
      return { label: "매우 숨김", dataValue: "veryHidden" };
    default:
      return { label: "상태 확인 필요", dataValue: "unknown" };
  }
}

function sheetKind(kind: string): string {
  switch (kind.toLocaleUpperCase("en-US")) {
    case "WORKSHEET":
      return "워크시트";
    case "CHARTSHEET":
      return "차트시트";
    default:
      return "종류 확인 필요";
  }
}
