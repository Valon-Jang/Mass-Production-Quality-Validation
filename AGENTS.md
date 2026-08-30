# Mass Production Quality Validation 개발 에이전트 운영 계약

이 파일은 `C:\Users\tequi\Mass Production Quality Validation` 아래에서 작업하는 새 터미널 AI가 자동으로
읽을 **개발 환경·정확도·안전·검증 기준**이다. 상세 업무 규칙을 다시 정의하는 문서가
아니며, 원본 기획 패키지의 구현계약을 안전하고 효율적으로 실행하기 위한 상위 작업
규칙이다.

## 0. 현재 권한과 절대 작업 경계

- 이 파일을 만든 2026-08-15 준비 작업은 **MD 환경 안내 작성만** 승인받았다.
  프로그램 코드, 프로젝트 Bootstrap, 패키지 설치, ZIP 압축해제, DB 생성, Scheduler
  연결은 수행하지 않은 상태다.
- 새 터미널에서도 사용자가 구현 시작을 명시하기 전에는 코드나 프로젝트 구조를 만들지
  않는다. 구현 시작 지시를 받으면 Mass Production Quality Validation 범위 안에서만 단계적으로 작업한다.
- 현재 단계와 별도 Phase 5 통합 승인 전에는 `C:\Users\tequi\Cloud Scheduler`의 코드,
  문서, DB, 설정, 규칙, 설치본, 프로세스, 자동시작, 레지스트리를 **수정하지 않는다**.
  향후 사용자가 Phase 5 통합을 명시적으로 승인해도 read-only 계약 조사, 양쪽 Impact Map,
  호환성 Test와 rollback을 먼저 확정한다. Cloud Scheduler Repository 변경은 사용자가 해당
  Workspace/Terminal과 범위를 별도로 승인한 경우에만 승인 범위 안에서 수행한다.
- Scheduler 실연동은 Phase 5다. 별도의 명시적 지시와 확정된 Queue/첨부 Fetch 계약
  전에는 Scheduler 코드 변경, 공용 DB 직접 접근, live polling, 실제 adapter를 만들지
  않는다.
- 사용자 파일과 기존 변경은 사용자 소유다. 무관한 파일을 정리·이동·삭제하거나
  덮어쓰지 않는다.

## 1. 불변 기준선과 무결성

현재 폴더의 공식 입력은 다음 파일이다.

- `MASS_PRODUCTION_QUALITY_VALIDATION_CODEX_HANDOFF_PACKAGE.zip`
- 확인된 SHA-256:
  `17750504C999F8C7EC331646CE00456D71BC9A33018F9CEFE23BE8CE41EBBA03`
- ZIP 내부 총 15개: Manifest 검증 대상 14개 + `MANIFEST_SHA256.txt` 자체 1개.
  검증 대상 14개는 Manifest와 모두 일치
- 공식 요구사항: 333건, 현재 모두 `NOT_STARTED`
- 공식 상태: 요구사항·개념설계 완료 / 구현 착수 전

배포받은 원본 ZIP과 그 안의 Source 문서는 **Baseline Snapshot**으로서 불변 입력으로
취급한다. Repository에 별도로 배치한 Source 작업본은 Living Context다.

- 원본 ZIP을 수정·덮어쓰기·삭제하지 않는다.
- 구현을 시작할 때는 먼저 ZIP SHA-256과 내부 Manifest를 다시 검증한다.
- 압축 해제가 필요하면 원본은 보존하고 Repository root 아래 임시/Staging 하위 폴더에만
  푼다. Staging 폴더를 중첩 Git Repository로 만들지 말고, 필요한 Source 작업본만 규정된
  Repository 위치로 복사한다.
- Baseline Snapshot을 편집해 구현상태를 표현하지 않는다. 구현상태·ADR·Gate Report는
  새 Repository의 별도 문서로 관리한다.
- Repository의 Core, 관련 WORK, Roadmap, Traceability, Checklist 작업본은 각 문서의
  변경규칙에 따라 갱신한다. 특히 기능범위, Schema/상태, Gate/Test, Risk, Resume Point 또는
  사용자의 명시적 수정이 바뀌면 변경이력과 근거를 남긴다. 이때도 원본 ZIP은 바꾸지 않고
  Baseline과의 차이를 추적한다.
- `02_OQC_WORK_CORE.md`는 `01_OQC_PROJECT_CUSTOM_INSTRUCTIONS.md`를 언급하지만 해당
  파일은 패키지에 없다. 이를 임의 생성하거나 내용을 추측하지 않는다. 현재는 Master
  Spec이 통합된 최상위 구현계약이다. 01 부재가 실제로 막는 작업만 입력 누락으로 기록한다.
- 사용자가 새 공식 패키지를 제공하면 기존 ZIP과 Hash를 보존하고, 새 Manifest·Hash·요구사항
  수를 다시 산출한 뒤 두 Baseline의 Diff와 Source 우선순위를 기록한다. 새 패키지라는 사용자
  지정과 검토 결과가 확인되기 전에는 위 Hash와 333건 기준을 자동 교체하지 않는다.

## 2. Source of Truth와 필수 읽기 순서

업무 규칙 충돌 시 우선순위는 다음과 같다.

1. 현재 대화의 최신 사용자 지시
2. `13_MASS_PRODUCTION_QUALITY_VALIDATION_CODEX_MASTER_IMPLEMENTATION_SPEC.md`
3. 관련 `docs/source/WORK_OQC_*.md`
4. `docs/source/02_OQC_WORK_CORE.md`
5. 프로젝트 환경·맞춤 지침
6. 일반 통계·품질·소프트웨어 관행과 AI 추정

첫 작업 전 다음 순서로 읽는다.

1. `README_FIRST.md`
2. `13_MASS_PRODUCTION_QUALITY_VALIDATION_CODEX_MASTER_IMPLEMENTATION_SPEC.md` 전체
3. `13A_MASS_PRODUCTION_QUALITY_VALIDATION_REQUIREMENTS_CHECKLIST.csv`
4. `docs/source/02_OQC_WORK_CORE.md`
5. 현재 작업 영역의 `docs/source/WORK_OQC_*.md`
6. `docs/source/12_OQC_REQUIREMENTS_TRACEABILITY.md`

기존 문서의 `Valonark OQC AI`는 Mass Production Quality Validation로 해석한다. 신규 코드, UI, API, DB
Migration, Export와 문서 제목에는 공식 명칭 `Mass Production Quality Validation`만 사용한다.

## 3. 제품 정의와 현재 Resume Point

Mass Production Quality Validation는 **양산 OQC 데이터 기반 개발 품질 의사결정 및 사양 최적화 시스템**이다.
업체별 비정형 OQC Excel을 모델별 표준 Long-format DB로 바꾸고, 최신 데이터와 검증된
누적 양산 데이터를 비교해 품질 이상, 데이터 신뢰성, 출하 영향, 업체 품질수준, 공급
안정성, 개발 Spec 최적화 근거를 제공한다.

단순 Excel Viewer, 평균 그래프 도구, AI 임의 판정 도구가 아니다.

구현 순서:

1. Phase 0 — 프로젝트 기반
2. Phase 1 — OQC Data Engine
3. Phase 2 — 과거 DB
4. Phase 3 — Dashboard
5. Phase 4 — Analytics
6. Phase 5 — Scheduler 연계
7. Phase 6 — Shipment / Coverage / Issue
8. Phase 7 — Supplier / Stability
9. Phase 8 — Export / Reference / Deep Analysis

향후 사용자가 구현 시작을 명시하면 Resume Point는 Phase 0 확인 후 Phase 1 Data Engine이다.
첫 실질 목표는 대표 OQC Excel 1건을 원본과 100% 대조 가능한 표준 DB로 변환하는 것이다.
Data Engine Gate 전에 업체평가·공급안정성 같은 후속 결과를 우선 구현하지 않는다.

현재 주요 미확정 입력:

- 실제 대표 OQC 1건과 같은 양식 과거본 2~3건
- 해당 모델 Master Spec 또는 승인기준
- 실제 Header/Sample 구조와 Mapping Preview 상세
- Scheduler Queue 포맷과 첨부 Fetch 방식
- 실제 데이터량과 동시사용자 수
- Cpk, Shift, Dispersion, Outlier, Duplicate 등 공식 Threshold
- Coverage·CTQ·업체평가 가중치·등급·Hard Gate 회복기준
- Corporate Export Template

실제값이 없으면 숨은 상수로 만들지 않는다. `BLOCKED_BY_INPUT`, Versioned Config,
명시적인 `PROVISIONAL_DEFAULT` 중 적절한 상태를 사용한다. Synthetic Fixture는 구조와
Framework 검증용이지 실제 Golden Gate의 대체물이 아니다.

## 4. 첫 세션 Bootstrap 절차

사용자가 구현 시작을 명시한 뒤에만 아래를 수행한다.

1. 현재 폴더, Git 상태, 기존 코드·문서·사용자 변경을 먼저 조사한다.
2. ZIP/Manifest 무결성을 검증하고 Source 문서를 정해진 순서로 읽는다.
3. 333개 요구사항을 Tracker에 올리고 초기 상태가 원본 CSV와 일치하는지 검사한다.
4. `C:\Users\tequi\Mass Production Quality Validation` 자체를 Repository root로 사용한다. 중첩 Repository가
   불가피하면 코드 생성 전에 이 계약이 적용되도록 해당 root에도 `AGENTS.md`를 둔다.
5. 빈 Repository일 때만 Master Spec의 Reference Stack을 기본안으로 사용한다. 기존
   Repository가 생겼다면 기존 Stack과 Convention을 우선한다.
6. `docs/adr/0001-reference-stack.md`에 선택 이유, 대안, Windows 제약, 향후 전환
   경계를 기록한다.
7. `docs/IMPLEMENTATION_STATUS.md`, `docs/adr/`, `reports/gates/`, `scripts/`,
   Fixture 구조와 CI Gate를 먼저 만든다.
8. Phase 0 Gate를 확인한 다음 Phase 1의 File Store → Workbook Scanner → Mapping
   Template/Preview → Long DB → Source Cell 추적 순으로 구현한다.
9. 각 단계는 작은 Vertical Slice로 끝내고 Test/Evidence를 붙인 뒤 다음 단계로 간다.

완료 보고 순서:

1. 결론
2. 구현한 기능
3. Requirement ID
4. DB / API / UI 변경
5. Test 결과
6. Gate 통과 여부
7. `BLOCKED_BY_INPUT`
8. 다음 Action

## 5. 이 Windows 환경에서의 도구 규칙

2026-08-15에 확인한 개발 환경 Snapshot이다. Bootstrap 때 다시 탐지해 실제 환경을
우선한다.

- Windows 11 x64 / PowerShell 5.1
- Git 2.55
- Python 3.12 x64와 Python 3.9 32-bit가 함께 설치됨
- **bare `python`은 Python 3.9 32-bit를 가리킴**
- Node.js 24 / npm 11
- 현재 `pnpm`, `yarn`, `uv`, `poetry`, `docker`, `make`, `ruff` 없음

따라서:

- Python 환경 생성은 반드시 `py -3.12 -m venv .venv`로 시작한다.
- 활성화 여부에 의존하지 말고 자동화에서는
  `.\.venv\Scripts\python.exe -m pip ...`처럼 interpreter를 명시한다.
- venv 밖에서 bare `python`, bare `pip`로 설치·실행하지 않는다.
- Python/Node 의존성은 lock 파일로 고정하고 global package 설치를 피한다.
- Master Spec의 `make ...` 명령은 목표 인터페이스다. Windows에서 `make`가 없으므로
  동일 기능의 PowerShell script와 npm script를 먼저 제공하고 Makefile은 선택적 wrapper로
  둔다.
- Docker, Redis, Celery, Message Broker는 실제 규모와 운영 필요가 확인되기 전 도입하지
  않는다. Development는 SQLite와 local read-only Original File Store로 시작 가능해야 한다.
- 긴 PowerShell inline command와 복잡한 quoting을 피하고 재사용 가능한 `.ps1` script로
  만든다. 콘솔 출력은 Windows 인코딩에서도 깨지지 않는 문자를 우선한다.
- 빌드·Fixture·cache를 중복 생성하지 말고 디스크 사용량을 주기적으로 확인한다.

## 6. Reference Architecture와 소유권

빈 Repository의 기본 Reference Stack:

- Backend: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2,
  pandas/NumPy/SciPy, openpyxl, pytest/hypothesis
- Frontend: React, TypeScript, Vite, TanStack Query/Table/Router,
  ECharts 또는 Plotly, Vitest, Playwright
- Development Storage: SQLite + local read-only Original File Store
- Production-ready Boundary: PostgreSQL + shared file/object-store adapter
- AI: provider-neutral `AIProvider` interface

소유권을 섞지 않는다.

- Domain: 상태, Entity, Value Object, 업무 Rule interface
- Application: Use Case와 Transaction 경계
- Infrastructure: DB, Excel, File Store, Scheduler, AI adapter
- API: HTTP contract와 validation
- Worker: Queue, background processing, cache update
- Frontend: 조회, 승인, 설정, Drill-down UX

Dashboard와 UI는 Engine 결과를 표시할 뿐 자체 판정식을 만들지 않는다. 같은 업무 Rule을
API, Worker, UI에 중복 구현하지 않는다. Domain Event는 초기에는 DB Outbox + Background
Worker로 충분하며, 필요가 검증되기 전 Message Broker로 확장하지 않는다.

## 7. Scheduler 연동 경계 — 현재 구현 금지

Scheduler 연동은 Phase 5까지 `DEFERRED_BY_PHASE`다.

향후 책임 경계:

- Scheduler: 전체 메일 확인, OQC 메일 식별, Mail ID와 첨부 참조 Queue 전달까지만
- Mass Production Quality Validation: 미처리 ID 확인, 첨부 획득, Excel 분석·표준화·적재·알림·Dashboard
- Scheduler는 OQC 분석·판정·알림을 하지 않는다.
- Mass Production Quality Validation는 메일함 전체를 다시 검색하지 않는다.

Phase 0/1에서 허용되는 것은 Mass Production Quality Validation 내부의 provider-neutral interface와 Mock/In-memory
adapter뿐이다. 예: `SchedulerQueuePort`, `AttachmentFetcher`, Queue DTO. 이것도 현재
Phase에 실제로 필요한 최소 경계만 만든다.

계약 불변조건:

- `mail_id + attachment_id` 또는 동등한 Idempotency Key
- 중복 전달 허용, 중복 적재 금지
- `PENDING → PROCESSING → DONE / REGISTRATION_REQUIRED / MAPPING_REQUIRED / ERROR`
- 중단된 `PROCESSING`과 실패 건만 안전하게 재시도
- 첨부 Fetch 실패와 Parsing 실패를 분리
- 실제 Queue format/fetch 방식이 없으면 adapter 완성 상태를 선언하지 않고
  `BLOCKED_BY_INPUT`으로 둔다.

영구 Architecture 금지. Phase 5에도 Mass Production Quality Validation Repository에는 Versioned Contract와 외부
Adapter만 두고 Cloud Scheduler 구현을 섞지 않는다.

- Cloud Scheduler import 또는 코드 복사
- Scheduler DB/table에 직접 연결
- Mass Production Quality Validation에서 실제 메일함 전체 재탐색

별도 Phase 5 통합 승인 전 금지:

- Scheduler 폴더에 파일 생성·수정
- Scheduler 프로세스 시작·종료·재시작
- live queue polling 또는 실제 Adapter 연결

향후 사용자가 실연동과 Cloud Scheduler Workspace 변경을 별도로 승인하면 두 Repository의
영향 범위, Versioned Contract, 호환성 Test, rollback, 양쪽 전체 회귀를 먼저 설계한다.

## 8. 데이터 정확도와 추적성 계약

- 기본 데이터 계층은 `모델 → 부품 → 검사항목`이다.
- 업체, 생산공장/Line, 금형, Cavity, 생산일, 작업조, Spec Revision, 측정방법,
  생산단계는 실제 유효할 때만 비교축으로 사용한다.
- 다른 모델은 같은 통계에 합산하지 않는다. 유사·종료 모델은 Reference로만 사용한다.
- 공식 통계 기준선은 `VALID` 데이터만 사용한다.
- `PENDING`, `SUSPECT`, `EXCLUDED`, `REPLACED`는 삭제가 아니라 상태와 이력으로 남긴다.
- Spec 판정과 Trend 이상을 분리한다.
- 원본 Sheet/Cell, 원본값, 표시값, 수식, Cached Value, Mapping Revision, Source File
  Hash까지 역추적 가능해야 한다.
- 업체가 계산한 평균·판정은 원본 근거가 아니라 비교 대상이다. 공식값은 Raw Data로
  재계산한다.
- Outlier·의심값·수정본을 자동 삭제하거나 공식값으로 자동 확정하지 않는다.
- 부분 적재는 항목별 상태와 보류 이유를 남기고 정상 항목까지 버리지 않는다.
- Exposure는 실제 출하 Source와 확인된 LOT-출하 매칭을 기준으로 계산한다. 매칭정보가
  없으면 임의 배분하지 않는다.
- 모델 출하량은 별도 모델 출하 Source가 있을 때만 표시한다. 부품별 출하량을 합산하거나
  모델 출하량으로 대체하지 않는다.
- Source File 저장, Lot 적재, Cache 갱신을 한 거대한 Transaction으로 묶지 않는다.
  Raw 적재는 보존하고 Cache는 재구축 가능하게 한다.

## 9. Excel·파일 처리 안전 규칙

- 원본 Excel은 항상 읽기 전용이다. 수정·정리·자동 저장·삭제하지 않는다.
- 파일 SHA-256, 수신정보, 원본 파일명과 Source 위치를 보존한다.
- `.xlsx`/`.xlsm`은 `data_only=False`와 `data_only=True`를 모두 읽어 수식과 Cached
  Value를 구분한다.
- VBA/Macro를 실행하지 않는다.
- 암호나 Sheet 보호를 우회하지 않는다.
- 외부참조, `#REF!`, Macro 의존값은 조용히 넘기지 말고
  `CALCULATION_REFRESH_REQUIRED` 등 명시 상태로 둔다.
- Hidden Sheet/행/열, 병합셀, 반복 Header, 중간 Section을 Scan 대상에서 빼지 않는다.
- 사진은 존재와 위치 Metadata만 보존하며 분석하지 않는다.
- Upload 확장자, MIME, 크기를 검증한다.
- Export 문자열은 Excel Formula Injection을 방지하도록 escape한다.

## 10. AI 사용 계약

AI가 할 수 있는 일:

- 신규·변경·애매한 Mapping 후보
- 애매한 측정방법 검토
- 일반 코드가 검출한 실제 이상에 대한 근거 기반 설명 후보
- 사용자가 실행한 On-demand 심층분석

AI가 하면 안 되는 일:

- 평균, Cpk/Ppk, Control Limit, PASS/FAIL 계산
- 출하 누적·Exposure·업체점수·공급 안정성 공식판정
- Spec, Threshold, 가중치, 날짜, 수량, LOT, 원인 사실 생성
- DB 직접수정, 승인, 삭제, 자동발송

AI interface는 strict schema로 검증한다. 원본 Excel 전체, 메일본문 전체, 개인정보,
내부 Token을 무조건 보내지 말고 일반 코드가 압축한 최소 구조와 필요한 근거만 전달한다.
AI Provider를 끄거나 강제로 실패시켜도 기존양식 Ingestion, 통계, 출하, Issue, 평가,
Dashboard, Export가 통과해야 한다. AI 오류는 해당 후보/설명만 대기시키고 Core 처리를
중단하지 않는다.

## 11. 성능·백그라운드 원칙

- Excel 구조 해석은 최초 등록 시 한 번만 한다. 이후 조회·통계는 표준 DB를 사용한다.
- 신규 데이터가 들어오면 영향받은 모델·부품·업체·검사항목 Segment만 재계산한다.
- Dashboard는 Cache를 우선 사용하고 Raw Measurement는 Drill-down에서 조회한다.
- 기간 비교, 전체 재평가, 신규 Spec 시뮬레이션, 전체 분포는 사용자 On-demand다.
- UI 요청 thread에서 파일 Parsing, 외부 I/O, AI 호출, 대량 통계를 동기 실행하지 않는다.
- 항목 하나의 실패가 전체 Queue와 Worker를 멈추지 않게 격리하되, 예외를 조용히
  삼키지 않는다.
- marker/최근 처리시각은 최적화 값이지 Source of Truth가 아니다. 멱등키와 영속 상태로
  중복·재시작·재시도를 검증한다.
- 외부 시스템 자동 Polling에서 전체 정렬·전체 탐색·반복 복구 Scan을 사용하지 않는다.
  증분 범위, timeout, cancellation, backoff, bounded retry를 사용한다.

## 12. 변경 전 영향분석과 기존 기능 보존

모든 변경 전에 짧은 Impact Map을 작성한다.

- 바뀌는 Requirement ID와 Source 문서
- 변경 파일·DB table·API·Domain Event·Worker·UI
- 직접 호출자와 간접 소비자
- 정상·실패·재시작·중복·오프라인 경로
- 모든 UI 진입 경로, Role, 화면 모드
- Migration, 기존 데이터, 설정, Fixture, Export 호환성
- 현재 존재하는 기능과 Test Baseline

완료 조건은 세 묶음을 모두 통과하는 것이다.

1. 요청 기능의 정상·실패·경계 Test
2. 영향 범위에 포함된 기존 기능 전부의 보존 Test
3. 프로젝트 전체 회귀 Test

사용자가 명시적으로 삭제를 요청하지 않은 기능을 삭제·숨김·개명·비활성화하지 않는다.
기존 Test를 지워서 통과시키지 않는다. 승인된 Baseline이 생긴 뒤 전체 Test 개수 하한을
활성화하고, 원시 개수보다 핵심 계약 Test ID Manifest(예: `REQUIRED_REGRESSION_TEST_IDS`)를
주 보호장치로 사용한다. 현재 Phase의 릴리스 필수 Suite에서 Test 누락·개명과 예상하지 못한
정적 skip·런타임 `SkipTest`는 실패 처리한다. 미래 Phase 또는 실제 입력 의존 Test는 현재
Suite에서 명시적 Marker로 제외하고 `VERIFIED`가 아닌 `DEFERRED_BY_PHASE` 또는
`BLOCKED_BY_INPUT`으로 추적한다.

Phase 0 Gate에서 최초 릴리스 필수 Test ID Manifest와 최소 개수 Baseline을 확정한다.
이후 Test는 증가를 기본으로 하고, 감소·대체는 영향 근거와 사용자의 명시적 승인이 있어야
한다.

UI는 소스에 버튼 문자열이나 함수가 남아 있는 것만으로 통과시키지 않는다. 관련 모든
진입 경로와 Role에서 실제 렌더링, 표시, viewport 경계, 활성/비활성 상태, command 연결,
클릭 결과, 오류 상태, 최소 지원 화면크기를 확인한다. 사용자의 실제 재현조건과 화면을
우선하며 캡처 한 장만 보고 정상으로 단정하지 않는다.

## 13. 필수 Test Matrix

각 Phase에서 관련 항목을 자동화하고, 실제 환경이 필요한 항목은 별도 Acceptance
Evidence를 남긴다.

- Requirement ↔ Code ↔ Test ↔ Evidence 추적
- Unit / Integration / API Contract / E2E
- Golden Workbook 원본 100% 대조
- Synthetic Workbook: 병합, 반복 Header, 다중 Sheet/LOT, Hidden, 수식/Cached Value,
  표시값 차이, 외부참조, 보호영역, `.xlsm` Macro 미실행
- Data Trust: exact/partial duplicate, 반복패턴, 수정본, 정밀도 저하, Outlier
- 통계 기대값: Mean, Min/Max, Range, Std, Cp/Cpk, Pp/Ppk, Control Limit, one-sided
- Revision·권한·Audit·적용일
- State Machine: Queue Retry, Mapping 승인, 부분적재, 데이터상태, Issue, 4M, 재공급
- Shipment many-to-many, 누적 역전/정정, Coverage, 실제출하 기반 Exposure와 임의배분 금지
- AI-off / AI-failure Core 독립성
- 명확한 제외 Scope가 구현되지 않았다는 Negative Test
- Migration 신규설치·Backup/Restore·실패 복구. 이전 릴리스가 존재할 때만 구버전 Upgrade
- 성능: Parsing 시간, 증분 Cache, Dashboard Query, Worker 격리
- 선택된 배포형태의 Windows 실제 실행·브라우저·파일 선택/Upload·다운로드 Smoke Test.
  Desktop Packaging은 해당 배포형태가 확정된 경우에만 포함

대표 OQC, 실제 Queue, 회사 Export 양식이 없으면 그 Gate를 통과했다고 쓰지 않는다.
검증 가능한 Framework Test는 통과시키고 나머지는 `BLOCKED_BY_INPUT`으로 남긴다.

## 14. Requirement와 Phase Gate

`13A_MASS_PRODUCTION_QUALITY_VALIDATION_REQUIREMENTS_CHECKLIST.csv`가 개발상태 기준이다. 허용 상태:

- `NOT_STARTED`
- `IN_PROGRESS`
- `IMPLEMENTED`
- `VERIFIED`
- `BLOCKED_BY_INPUT`
- `DEFERRED_BY_PHASE`
- `OUT_OF_SCOPE_CONFIRMED`

`VERIFIED` 조건:

- Code Reference
- 자동 Test Reference
- Acceptance Evidence
- 관련 Phase Gate 통과

각 Phase 산출물:

- 실행 가능한 코드
- 필요한 DB Migration
- Unit / Integration / E2E Test
- `docs/IMPLEMENTATION_STATUS.md`
- `reports/gates/PHASE_N_GATE_REPORT.md`
- 요구사항 체크리스트 갱신
- 새 Risk와 미검증 입력
- 변경된 ADR

완료 Phase에 `pass`, 빈 Stub, 동작하지 않는 버튼, 근거 없는 Mock 판정을 남기지 않는다.
후속 UI Skeleton은 가능해도 선행 Gate에 의존하는 Business Result를 공식 완료로 선언하지
않는다.

## 15. DB·Migration·Audit·복구

- DB Schema 변경은 반드시 Alembic Migration과 upgrade test를 동반한다.
- destructive migration 전 정확한 대상과 데이터 보존·복구 경로를 검증한다.
- 기존 DB sample을 최신 code로 열고 migrate한 뒤 핵심 조회·상태·Audit를 재검증한다.
- Master, Mapping, Method, Submission Standard, Rule은 Revision으로 관리한다.
- 동시수정 충돌은 `row_version` 기반 낙관적 Lock으로 막고, 일반 삭제는 Soft Delete와
  상태/Revision 이력으로 처리한다.
- 계산·판정 결과에서 적용 당시의 `source_data_version`, `rule_version`, `revision_id`를
  역추적할 수 있어야 한다.
- 관리자 변경은 변경 전/후, 사용자, 시간, 적용일, 사유, 연결 Requirement/Issue/Source를
  남긴다.
- 설치/배포 파일과 사용자 데이터·원본 File Store·설정·비밀값을 분리한다.
- Cache와 파생결과는 재구축 가능해야 하며 Raw/원본을 rollback 대상으로 삼지 않는다.
- 복원 직전 현재 상태를 별도 백업하고 외부 고유 ID로 중복 생성을 방지한다.

## 16. 보안과 Test 격리

- Secret은 환경변수 또는 Secret Store로 관리하고 `.env`, 로그, Fixture, 문서, 산출물에
  실제 값을 남기지 않는다. `.env.example`만 제공한다.
- 개인정보와 메일본문 전체를 AI, 로그, Test Evidence에 남기지 않는다.
- 내부 File Path와 Connector Token을 UI/API error에 그대로 노출하지 않는다.
- 역할은 `ADMIN`, `REVIEWER`, `VIEWER`, `SYSTEM`, `AI_PROVIDER` 경계를 유지하고, 조회·승인·
  설정변경·System 처리 권한을 최소권한으로 분리해 Audit한다.
- Production은 사내 인증 Adapter를 사용하며 개발계정, Debug Route 또는 임시 우회로
  인증·권한을 건너뛰지 못하게 한다.
- Test는 임시 DB·임시 File Store·가짜 Secret·Mock connector를 사용한다.
- Test/QA가 사용자 DB, 원본 ZIP, 실제 OQC, Scheduler, Outlook, 레지스트리, 자동시작,
  외부 발송·삭제·업로드를 변경하지 않게 한다.
- 실제 외부 쓰기가 필요한 E2E는 별도 명시적 승인과 격리 계정/대상으로만 수행한다.
- 파괴 작업은 확정된 하위 경로만 대상으로 하고 실행 전 절대경로·대상 수·복구 가능성을
  확인한다.

## 17. 디버깅과 시행착오 기록 방식

문제가 생기면 재시도나 우회부터 붙이지 않는다.

1. 증상 발생 시점의 실행순서와 상태전이를 그린다.
2. 데이터값 → 계산/상태 → API → 렌더링/라벨 층을 분리한다.
3. 구버전 프로세스, 포트, cache, migration 상태, 실제 실행 산출물을 확인한다.
4. 외부 I/O는 한 항목으로 재현한 뒤 다건·주기 처리로 확대한다.
5. 실패를 더 작은 단위로 줄여 실제 원인을 확인한다.
6. 해결 후 같은 회귀가 다시 발생하면 실패하는 영구 Test를 추가한다.

기록 형식:

```text
증상 → 실제 원인 → 잘못된 초기 판단 → 해결 → 영구 회귀 Test
```

자동 Test의 사용자 입력 대기 창은 자동 응답하고, 종료 후 프로세스·창·파일·포트를
남기지 않는다. 예외를 조용히 삼키지 말고 민감정보를 제외한 실패 단계와 상태를 기록한다.

## 18. Build·Release Gate

릴리스 Pipeline은 선택된 배포 산출물 생성 전에 다음을 자동 강제한다.

- compile / lint / typecheck
- Requirement integrity
- Migration check
- 전체 Test
- 핵심 계약 Test ID 존재
- 승인된 Baseline이 존재할 때의 Test 개수 하한
- 현재 Phase 릴리스 필수 Suite의 예상하지 못한 정적·런타임 skip 0건
- 제외 Scope Negative Test

하나라도 실패하면 빌드를 시작하지 않는다. 버전은 한 곳에서 관리하고, clean Bootstrap과
실제 산출물 기준으로 검증한다. 신규 설치를 검증하고, 이전 릴리스가 존재할 때는 기존 버전
Upgrade도 검증한다. 사용자 데이터 보존, Backup/Restore, 종료·재시작, API/Worker 실패
복구를 확인한다. 최종 보고에는 버전, 산출물 Hash, 영향 범위, Test 결과, 실제 환경 검증,
제한사항을 포함한다.

## 19. 사용자 확인이 필요한 경계

다음은 사용자 확인 없이 확대하지 않는다.

- 기존 합의를 바꾸는 신규 Scope
- 지속 프로그램 부하 또는 지속 AI 호출을 크게 늘리는 구조
- 실제 Scheduler/메일/외부 시스템 쓰기 연동
- 파괴적 데이터 변경 또는 복구 불가능한 Migration
- 공식 Threshold, 평가 가중치, 등급, Hold/Spec/공급 의사결정
- 보안·배포·인증 방식의 중대한 변경

그 밖의 합의 범위 내 세부 구현은 Source 문서와 Test Evidence를 근거로 합리적으로
결정해 진행한다. 단순히 일이 어렵거나 많다는 이유로 불필요한 확인 질문을 반복하지 않는다.

## 20. 금지사항 빠른 확인

- 현재 단계의 Scheduler 실연동 및 Cloud Scheduler 수정
- AI가 공식 계산·점수·판정을 생성
- 원본 ZIP/Excel 수정·삭제
- VBA/Macro 실행
- 암호·보호 우회
- 사진 AI 분석
- 의심값·Outlier 자동삭제 또는 자동제외
- 업체 PASS/FAIL 무검증 신뢰
- 업체 공차변경 자동승인
- 다른 모델 통계 혼합
- OQC NG율을 전체 출하불량률로 확대
- 부품 출하량 합을 모델 출하량으로 대체하거나 합산. 모델 출하량은 별도 Source가 있을
  때만 표시
- Exposure 임의배분
- LOT-출하 매칭정보가 없는데 Exposure를 만들거나 배분
- 측정기 ID/Serial 및 검교정 성적서·유효기간 관리
- 업체 Action 응답속도 자동평가
- 고객 Claim 기반 시장 무이슈 실적 평가
- FMEA 본 구현. 명시된 Extension Point만 유지
- 자동 출하 Hold
- 자동 Spec 변경
- 자동 공급비율 결정
- 업체 메일 자동발송
- 실제값 없는 Threshold·가중치·등급의 공식 확정
- 데이터 부족 상태의 강한 안정판정
- 실제 Gate 미통과 상태의 완료 선언
- 사용자 승인 없는 기능 삭제·숨김·개명

## 21. 세션 종료 체크

- 변경 범위가 Mass Production Quality Validation 안에만 있는가?
- 사용자 요청 밖의 Scheduler·외부 상태를 건드리지 않았는가?
- 관련 Requirement, Source, ADR, Migration, Test, Evidence가 연결됐는가?
- 요청 기능과 영향받는 기존 기능, 전체 회귀가 모두 통과했는가?
- Test 수·핵심 ID·skip Gate가 유지되는가?
- 실제 입력이 없는 항목을 완료로 과장하지 않았는가?
- 사용자 데이터·원본·비밀값이 보존됐는가?
- 다음 Resume Point와 `BLOCKED_BY_INPUT`이 명확한가?
