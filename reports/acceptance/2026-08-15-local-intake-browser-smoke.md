# Mass Production Quality Validation 한글 로컬 접수 브라우저 Smoke Evidence

- 실행일: 2026-08-15 (Asia/Seoul)
- 실행형태: Windows 11, Python 3.12 FastAPI 단일 로컬 프로세스,
  Vite production build, Chrome 실제 브라우저
- 검증 URL: `http://127.0.0.1:8876/`
- 격리: DB URL, Original File Store, upload staging을 모두
  `.validation/ui-e2e-runtime` 아래로 지정했다. 기본 `.localdata`, Scheduler,
  Outlook, 외부 API에는 접근하지 않았다.

## 입력

- 파일: `outputs/qwen_mapping_oqc_samples_ko_20260815/01_기준_한글_OQC_성적서.xlsx`
- 입력 SHA-256:
  `782283EE4F60BD005F47A1D153846C632B35F21B2D5ED017D604315255FEBEBF`
- 입력 크기: 13,051 bytes
- 프로젝트: `browser-e2e`
- 모델 후보: `DNX-가상-100`
- LOT 후보: `가상LOT-260815-A`
- 이 파일은 합성 Framework Fixture이며 Golden Workbook이 아니다.

## 실제 사용자 경로

1. 한글 초기 화면에서 프로젝트, 모델·LOT 힌트, `.xlsx` 파일을 선택했다.
2. `원본 보존 및 스캔 시작`을 클릭했다.
3. HTTP create는 `202`, 같은 프로젝트의 opaque job GET은 `200`이었다.
4. 작업은 `MAPPING_REQUIRED`로 종료했다. 자동 Mapping, 공식 등록, 품질판정은
   발생하지 않았다.
5. 화면에서 Receipt ID, 원본명, 수신시각, 13,051-byte 크기, SHA-256,
   모델·LOT 후보를 확인했다.
6. 세 시트가 표시됐다.
   - `출하검사성적서`: `A1:R13`, visible, 병합영역과 P10:P13 수식 cache 경고
   - `원시데이터`: `A1:P49`, hidden
   - `합성자료안내`: `A1:B7`, visible
7. `CALCULATION_REFRESH_REQUIRED`, `FORMULA_CACHE_MISSING`,
   `DISPLAY_VALUE_NOT_RENDERED`가 안전한 위치 근거와 함께 표시됐다.

## 무결성·안전 확인

- 저장 Blob SHA-256은 입력과 동일했다:
  `782283EE4F60BD005F47A1D153846C632B35F21B2D5ED017D604315255FEBEBF`.
- 저장 결과는 Blob 1개와 Receipt JSON 1개였고 staging entry는 0개였다.
- 화면과 API에는 내부 절대경로나 예외 원문이 노출되지 않았다.
- 데스크톱 viewport는 `clientWidth=1521`, `scrollWidth=1521`이었다.
- 390px override에서는 실제 client width와 scroll width가 모두 375px로
  같아 가로 넘침이 없었다.
- HTML `lang=ko`, live status가 유지됐고 브라우저 console warning/error는
  0건이었다.
- 검증 브라우저 tab, viewport override, localhost server와 port 8876을
  종료했다.
- 최초 확인 중 누락된 favicon 요청 1건은 `frontend/public/favicon.svg`로
  보완했고 production build와 전체 Gate에서 다시 생성됨을 확인했다.

## 판정

- `DQ-P1-UIINTAKE-001~007` 범위: `PASS`
- 한글 로컬 수동 접수 UI/API slice: `PASS`
- Phase 1 전체: `IN_PROGRESS`
- Golden Acceptance (`GOV-013`, `ING-051`): `BLOCKED_BY_INPUT`
- 다음 경계: 이 Receipt/Scan을 승인된 Mapping Preview와 사용자 승인 UI로
  연결한다. 실제 대표 OQC와 회사 Master 기준이 들어오기 전에는 공식 완료를
  선언하지 않는다.
