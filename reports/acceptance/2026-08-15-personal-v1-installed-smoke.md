# Mass Production Quality Validation 개인용 1차 완성본 설치·실행 Smoke Evidence

- 실행일: 2026-08-15 (Asia/Seoul)
- 실행환경: Windows 11, Python 3.12, 로컬 브라우저
- 제품 버전: `0.1.0`
- 확장팩 ID: `com.massproductionqualityvalidation.oqc-local`
- 설치 ZIP: `.staging/releases/MASS-PRODUCTION-QUALITY-VALIDATION-extension-0.1.0.zip`
- ZIP SHA-256:
  `E685E513864F4504B28D8C7033FCBF08B89D865F09EBA19CAEE423CDD8F3BDD3`
- 파일 수 / 크기: 68개 / 335,298 bytes
- Inventory SHA-256:
  `066582AB856916BED6C6701065938C0D823BD0CD3B669164C159BFC6BF57F9DE`

## Release Gate

- Backend: 159/159 PASS
- 핵심 회귀 계약 ID: 149개, 중복 0
- Frontend: TypeScript, 18/18 Vitest, Vite production build PASS
- Ruff/format: 95 files PASS
- Strict Mypy: 63 sources PASS
- Migration graph: `0001 -> 0005`, single head
- 예상하지 못한 skip/xfail/xpass: 0
- 알려진 비차단 경고: FastAPI TestClient의 upstream `httpx2` 전환 경고 1건

## 격리 설치와 실행

검증 경로는 Repository 아래
`.validation/personal-v1-final-159/`로 한정했다.

- Code Root: `.validation/personal-v1-final-159/code`
- Data Root: `.validation/personal-v1-final-159/data`
- localhost: `127.0.0.1:8892`
- Hash 고정 `runtime.lock`으로 사설 Python 3.12 환경 설치 PASS
- `pip check` PASS
- Alembic `upgrade head` PASS
- `/api/v1/health/live`: `status=ok`, `service=Mass Production Quality Validation`, `version=0.1.0`
- `/api/v1/health/ready`: HTTP 200, `database=ready`
- 검증 종료 후 Browser tab, launcher, uvicorn listener 종료 확인: port listener 0

기본 `.localdata`, Cloud Scheduler, Outlook, Registry, 자동시작, Windows
Service, 외부 AI에는 접근하거나 쓰지 않았다.

## 실제 설치본 브라우저 확인

- 문서 제목: `Mass Production Quality Validation · OQC 원본 접수`
- 한글 Intake 화면, 개인 로컬 확장팩 표시, 프로젝트/모델/LOT/파일 입력,
  원본 보존 및 스캔 시작 버튼이 실제 설치본에서 렌더링됐다.
- 로드된 최종 자산:
  - `/assets/index-BjbooXw4.js`
  - `/assets/index-tt3TI22Z.css`
- Desktop document: `clientWidth=1521`, `scrollWidth=1521`, 가로 넘침 0
- Browser console warning/error: 0

격리 브라우저의 native 파일선택 창은 자동 경로 입력을 허용하지 않아,
파일선택 자체는 이전 설치본 Chrome acceptance와 자동 Frontend 계약 Test로
보존하고 이번 설치본의 실제 파일 처리는 동일 실행 프로세스의 HTTP Intake
API로 검증했다. 이는 제품 오류가 아니다.

## 실제 설치본 OQC 접수와 설정 Smoke

원본 `MASS_PRODUCTION_QUALITY_VALIDATION_OQC_Demo.xlsx`는 읽기 전용 입력으로 사용했고 수정하지 않았다.

- Project: `personal-v1-smoke`
- Intake terminal state: `MAPPING_REQUIRED`
- Receipt ID: `af5969fb08d84b79bf6cb4785057edc1`
- Source SHA-256:
  `E516A88B4D450EA9499C2D23BA0491AD276AF4286B45FFEAE9547EB4B43B9AEA`
- Scan sheet count: 3
- Terminal `poll_after_ms`: `null`
- Typed issue count: 1

같은 설치본에서 빈 project configuration snapshot을 조회한 뒤 Model
`DEMO-MODEL`을 explicit reason과 함께 생성했다.

- 생성 row version: 1
- 재조회 Model count: 1
- `official_values_created=false`
- `auto_effects=false`
- `ai_used=false`

이는 최초 설정 API가 실제 설치 DB와 Audit 경계에서 동작함을 확인하는 smoke다.
실제 회사 Master 값, 실제 승인 Mapping, 실제 품질 판정은 만들지 않았다.

## 판정과 남은 경계

- 개인용 1차 설치·실행·원본접수·설정 Smoke: `PASS`
- Phase 1 Framework: `IN_PROGRESS`
- Golden Workbook (`GOV-013`, `ING-051`): `BLOCKED_BY_INPUT`
- 실제 Qwen 호출과 Scheduler profile/secret 전달: Phase 5
- 실제 회사 Master와 대표 OQC 100% 사용자 대조: 입력 수신 후 Acceptance
- 현재 ZIP은 embedded Python/wheelhouse를 포함하지 않으므로 Python 3.12와
  package index 또는 사전 cache가 필요하다.
