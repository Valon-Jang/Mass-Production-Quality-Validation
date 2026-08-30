# Mass Production Quality Validation Windows 확장팩 실제 설치·업데이트 Smoke Evidence

- 실행일: 2026-08-15 (Asia/Seoul)
- 실행환경: Windows 11, Python 3.12, Chrome 실제 브라우저
- 확장팩 버전: `0.1.0`
- 확장팩 ID: `com.massproductionqualityvalidation.oqc-local`
- 최종 ZIP: `.staging/releases/MASS-PRODUCTION-QUALITY-VALIDATION-extension-0.1.0.zip`
- 최종 SHA-256:
  `75F922BCDA58A948F7A7C1179AE2F1FE3D4FD8430127A4BA5C5610606852A39A`
- 파일 수 / 크기: 61개 / 274,455 bytes

## 실제 설치·업데이트 경로

1. 격리된 `.validation/winpkg-final-e2e-132/code`에 Python 3.12 가상환경을
   새로 만들고 Hash가 고정된 `runtime.lock`만으로 Runtime을 설치했다.
2. `pip check`가 통과했고 FastAPI/Alembic/SQLite Runtime이 실제로 기동됐다.
3. 사용자 데이터는 별도 `.validation/winpkg-final-e2e-132/data`에 두었다.
4. 최종 ZIP을 같은 코드 Root에 `Update`로 적용했다. 결과는
   `cleanup_pending=false`, `runtime_provisioned=true`, `data_preserved=true`였다.
5. 업데이트 후 `/api/v1/health/ready`는 `Mass Production Quality Validation 0.1.0`, DB `ready`를
   반환했고 `/`는 HTTP 200이었다.
6. 설치본 프로세스는 localhost `127.0.0.1:8891`에서만 실행했고 검증 후
   Launcher, Python 자식 프로세스와 Listener를 모두 종료했다.

검증 Root는 전부 Repository의 `.validation` 아래였다. 기본 `.localdata`,
Cloud Scheduler, Outlook, Registry, 자동시작, Windows Service와 외부 AI에는
접근하거나 쓰지 않았다.

## 업데이트 전후 사용자 데이터 보존

- 업데이트 전에 저장된 Receipt
  `d8073be042f7451b80deda067900df5b`를 업데이트 후 다시 조회했다.
- 프로젝트 `install-e2e`, 원본 SHA-256
  `782283EE4F60BD005F47A1D153846C632B35F21B2D5ED017D604315255FEBEBF`가
  정확히 일치했다.
- 업데이트 후에도 `MAPPING_REQUIRED / MANUAL_SOURCE_REVIEW`, AI
  `NOT_CALLED`, 원본 셀 942개가 그대로 재구성됐다.
- 두 번째 페이지는 offset 120에서 120개를 반환했다.
- 격리 Data Root의 intake staging 파일은 0개였다.

## 최종 ZIP 실제 브라우저 경로

1. 최종 설치본의 한글 화면에서 프로젝트 `install-e2e-final`, 모델 후보
   `DNX-가상-100`, LOT 후보 `가상LOT-260815-B`와 합성 OQC `.xlsx`를
   선택했다.
2. `원본 보존 및 스캔 시작` 후 `매핑 등록 필요`가 표시됐다.
3. 업체 범위 `가상업체`를 명시하고 `원본 셀 검토 시작`을 실행했다.
4. `수동 매핑 검토 필요`, `AI 호출 없음`, `1–120 / 942`가 표시됐고
   원본 수식 `=AVERAGE(H10:O10)`도 그대로 확인됐다.
5. `다음 셀`로 `121–240 / 942` 페이지가 실제로 이동했다.
6. 최종 설치본은 JavaScript `index-BhOD4xmZ.js`와 수정된 반응형 CSS
   `index-Deei0Jgz.css`를 제공했다.

## 화면·안전 확인

- 데스크톱: `clientWidth=1521`, `scrollWidth=1521`, 페이지 가로 넘침 0.
- 390px 모바일: document `clientWidth=375`, `scrollWidth=375`, 페이지
  가로 넘침 0.
- 넓은 원본 셀 표는 화면 밖으로 잘리지 않고, 314px 컨테이너 안에서만
  `scrollWidth=640`의 독립 좌우 스크롤을 제공했다.
- 브라우저 Console warning/error는 0건이었다.
- 내부 절대경로, Secret, 예외 원문은 화면/API에 노출되지 않았다.
- 공식값 생성, 계산, 자동 Mapping 승인, Long 적재, 자동 `VALID` 승격은
  발생하지 않았다.

## 판정과 제한

- `DQ-P1-WINPKG-001~006`: `PASS`
- `DQ-P1-MAPUI-001~004`: `PASS`
- 최종 Release Gate: 132/132 PASS, Frontend 13/13 PASS, skip/xfail 0
- Windows 개인용 확장팩 설치·업데이트·한글 접수·원본 셀 검토 Smoke: `PASS`
- Phase 1 전체: `IN_PROGRESS`
- Golden Acceptance (`GOV-013`, `ING-051`): `BLOCKED_BY_INPUT`
- 현재 ZIP에는 Python Runtime과 Wheelhouse가 내장되지 않았다. Python 3.12와
  Package index 또는 사전 Cache가 필요하다.
- Cloud Scheduler 설치본·발견 계약과 공용 AI Profile/Secret 전달은 Phase 5
  입력이므로 아직 `UNVERIFIED / DEFERRED_BY_PHASE`다.
