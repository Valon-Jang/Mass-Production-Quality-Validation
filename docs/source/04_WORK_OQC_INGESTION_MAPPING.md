# WORK_OQC_INGESTION_MAPPING

**Work Type:** 수행영역 Living Context  
**관리 대상:** OQC Excel 수집 / 신규등록 / 구조해석 / Mapping / 표준 DB 적재  
**상태:** 요구사항 확정 / 대표 OQC 미선정  
**Core:** `02_OQC_WORK_CORE.md`

---

## 1. 목적

업체·모델·부품마다 다른 OQC Excel 양식에 구애받지 않고, 실제 검사정보를 손실 없이 표준 DB로 변환한다.

핵심 완료조건:

> **대표 OQC의 모델·부품·업체·LOT·Spec·측정값·출하수량을 사람이 원본과 대조했을 때 누락·오매핑 없이 동일하게 추출한다.**

---

## 2. 입력 Route

### 자동 Route

`Scheduler Queue의 Mail ID → 해당 메일 첨부 Excel 확보`

### 수동 Route

- 메일 외 경로로 받은 OQC 단건 등록
- 과거 OQC 다량 일괄등록
- 대표 Golden OQC Test 등록

자동과 수동은 유입경로만 다르고 이후 Mapping·검증·DB 적재 Logic은 동일하다.

---

## 3. 신규 OQC 분류

등록되지 않은 OQC가 감지되면 바로 공식 DB에 넣지 않는다.

프로그램은 먼저 다음 중 무엇인지 분류한다.

- 신규 모델
- 기존 모델의 신규 부품
- 기존 모델·부품의 신규 업체
- 기존 업체의 OQC 양식 변경
- 기존 양식의 신규 검사항목
- 기존 데이터와 사실상 동일한 중복파일
- 동일 LOT의 재검·수정본 가능성

화면에는 예상 연결위치를 먼저 보여준다.

예:

`4695 → Out Box → 신규 업체 B의 OQC로 판단`

사용자가 `등록`을 선택한 뒤 첫 Mapping 검증으로 이동한다.

---

## 4. 대표 1건 Mapping 확정

신규 양식은 첫 1건에서 다음을 미리보기로 보여준다.

- 모델 / 부품 / 업체
- LOT / 생산일 / 검사일
- Spec Revision
- 금번 출하수량 / 누적 출하수량
- 검사항목과 상위 Section
- Target / LSL / USL / 단위
- 측정방법
- Point / Cavity
- Sample별 실제값
- 업체 판정 / 계산값

사용자는 잘못 읽힌 부분만 수정하고 `Mapping 확정`한다.

확정 후:

- 동일 업체·동일 양식은 자동처리
- 사용자가 수정한 Mapping은 이력으로 남기고 재사용
- AI 최초 추출값과 사용자 확정값을 구분

---

## 5. 대량 OQC 최초 구축

과거 OQC 수십~수백 개를 투입할 때:

1. 대표 1건 선택
2. 대표 1건 Mapping 사용자 확정
3. 나머지 전체 자동 분석
4. 대표본과 다른 파일만 예외 분리
5. 예외만 사용자 확인
6. 적재결과 요약 확인
7. 초기 DB 구축 Gate 완료

예외 기준:

- 검사항목 수 변화
- Spec / 공차 변화
- 측정방법 변화
- Sample 구조 변화
- 출하수량 항목 변화
- 날짜 / LOT / Revision 충돌
- 신규 Section / 신규 부품
- 양식 구조 변화

정상파일은 하나씩 승인받지 않는다. Queue에 여러 건이 동시에 들어와도 기존 등록양식은 연속 자동처리하고 `신규 등록 / Mapping 재확인 / 이상`처럼 사용자 판단이 필요한 항목만 묶어서 보여준다.

적재결과 예:

- 총 100건
- 정상 적재 94건
- Spec 변경 3건
- 신규 항목 2건
- 측정방법 변경 1건

---

## 6. 다중 구조 처리

### 6.1 여러 첨부파일

메일 1건에 Excel이 여러 개면 파일별로 읽고 모델·부품 단위로 라우팅한다.

### 6.2 한 Excel의 여러 모델

파일단위가 아니라 모델단위로 분리한다.
모델 식별이 불확실한 부분만 보류한다.

### 6.3 여러 Sheet

사용자가 Sheet를 고르지 않아도 전체 Sheet를 스캔한다.
부품·검사항목별로 Sheet가 나뉘어 있어도 관계를 보존한다.

### 6.4 한 파일의 여러 LOT·검사일

LOT·검사일 단위로 독립 적재한다.

### 6.5 같은 LOT의 여러 생산일

동일 LOT 안에 생산일이 여러 개면 Production Sub-group으로 분리한다.
전체 LOT와 생산일별 분석을 모두 가능하게 한다.

---

## 7. 모델·부품·업체 식별

우선순위:

1. Excel 내부 모델명 / 품번 / 도면번호 / Revision
2. 등록된 Master 및 업체별 부품명 Mapping
3. Sheet명과 문서 Section
4. 원본 파일명
5. 메일 제목

내부값과 파일명·메일제목이 충돌하면 자동 적재하지 않고 어떤 값끼리 충돌했는지 표시한다.

### 업체별 부품명 Mapping

같은 당사 부품을 업체가 다르게 표기하면 당사 기준 부품명으로 연결한다.

예:

- 당사 기준: `Top Tray`
- 업체 A: `TRAY-TOP-4695`
- 업체 B: `4695 UPPER TRAY`

화면 용어는 `Alias`보다 **업체별 부품명 매핑**을 사용한다.

---

## 8. 검사항목 Mapping

같은 의미의 업체별 항목명은 당사 기준 검사항목으로 연결한다.

예:

- Overall Height
- Total Height
- 전체 높이

단, 이름이 같아도 다음이 다르면 자동으로 합치지 않는다.

- 측정 위치
- 측정방법
- 단위의 의미
- 기준 정의
- 전체치수인지 특정 Point인지

애매하면 Mapping 후보와 근거를 보여주고 사용자가 한 번 확정한다.

신규 검사항목은 데이터 자체는 보존하되 `관리 / 분석 제외 / 기존 항목과 매핑` 결정 전까지 공식 분석에 섞지 않는다.

외관 불량유형도 같은 원칙으로 표준화한다. 예를 들어 `Scratch / 흠집 / 긁힘`은 동일 불량 후보로 제시하되, 의미가 불확실하면 사용자 확정 전 자동 통합하지 않는다.

---

## 9. 단위와 정밀도

### 9.1 단위 환산

변환관계가 명확하면 당사 기준 단위로 환산한다.

예:

`1250 μm → 1.25 mm`

원본값·원본단위와 환산값·기준단위를 모두 보존한다.
관계가 애매하면 자동 환산하지 않는다.

### 9.2 실제 셀값과 표시값

Excel 화면에 `10.1`로 보여도 실제 저장값이 `10.1234`일 수 있다.

- 실제 저장된 값
- Excel 표시형식

을 구분한다.

정밀도 변경 판정은 표시 자릿수가 아니라 실제 저장값의 분해능·패턴을 기준으로 한다.

---

## 10. Excel 구조 해석

### 10.1 병합셀·중간 제목·반복 Header

- 상단의 모델/LOT 병합셀
- 치수검사/외관검사/물성검사 Section 제목
- 인쇄용 반복 Header
- 빈 행
- 비고·서명란

을 측정값으로 오인하지 않는다.
상위 Section과 하위 검사항목 관계를 유지한다.

### 10.2 숨김 영역

숨김 Sheet·행·열도 구조를 확인한다.

- 실제 Raw Data면 추출후보
- 계산용·중복영역이면 제외
- 보이는 결과와 숨김 Raw Data가 다르면 불일치 표시

### 10.3 수식

원본 측정값과 수식 계산값을 구분한다.

- 평균 / Min / Max / PASS·FAIL은 프로그램이 Raw Data로 재계산
- 업체 계산값과 다르면 정확한 셀·항목을 표시

### 10.4 외부참조·깨진 수식

외부파일 참조, `#REF!`, 계산 불가 수식은 마지막 Cache 숫자가 보이더라도 공식값으로 조용히 사용하지 않는다.
파일 내 Raw Data가 있으면 프로그램이 재계산한다.

### 10.5 Macro

- `.xlsm` 읽기 가능
- VBA 실행 금지
- Macro가 실행되어야 값이 갱신되는 항목은 `계산 갱신 필요`

### 10.6 보호·암호화

- 읽을 수 있는 보호 Sheet는 정상 분석
- 보호 해제·비밀번호 우회 금지
- 읽을 수 없는 영역은 Sheet·범위를 정확히 표시

### 10.7 사진

사진은 존재여부와 위치를 보존할 수 있으나 현재 분석하지 않는다.

---

## 11. 필수 표준 Schema

### 식별

- Model ID / Model Name
- Part ID / Part Name / Part No.
- Supplier ID / Supplier Name
- LOT
- Production Date
- Inspection Date
- Received Date
- Spec Revision

### 출하

- Current Shipment Quantity
- Supplier Cumulative Shipment Quantity

### 검사항목

- Inspection Item ID / Name
- Item Type
- Section
- Point
- Cavity
- Unit
- Measurement Method

### Spec

- Target
- LSL
- USL
- Source Spec Revision

### 측정

- Sample No.
- Stored Raw Value
- Display Value
- Standardized Value
- Supplier Judgment
- System Judgment

### 추적·상태

- Original File Name
- Source Sheet
- Source Cell / Range
- Mapping Template Revision
- Data Status
- Superseded Measurement ID

---

## 12. 동일 LOT·수정본·재검

같은 LOT가 여러 파일에 있으면 단순 중복으로 폐기하지 않는다.

가능한 유형:

- 초회 검사
- 재검
- 수정본
- 값·판정·Spec·출하수량 정정

### 수정본 Detection

파일명에 `수정`이 없어도 기존 동일 LOT와 비교한다.

- 측정값 변경
- Spec·공차 변경
- PASS/FAIL 변경
- 출하수량 변경

### 반영 원칙

- 문구·표시형식만 변경: 자동 반영 가능
- 품질판단 영향값 변경: `수정본 확인 필요`
- 승인 후 최신본만 공식 통계 사용
- 과거값은 `REPLACED`로 이력 보존
- 수정 전 이상판정은 `수정본 반영으로 해제` 이력 유지

특히 `NG → PASS`, 공차 완화, 신뢰성 의심을 없애는 방향은 강하게 표시한다.

---

## 13. 데이터 충돌·보류

자동 확정하지 않는 사례:

- 파일명 날짜와 내부 검사일 충돌
- 동일 LOT인데 값이 다름
- Revision 누락인데 공차가 달라짐
- 모델·부품 식별 충돌
- 필수영역 암호화
- Mapping 확신도 부족

보류 시 단순 `확인 필요`가 아니라 충돌한 값과 위치를 직접 보여준다.

---

## 14. 부분 적재

파일 40개 항목 중 38개가 정상이고 2개가 의심이면:

- 정상 38개 → `VALID` 적재
- 의심 2개 → 항목단위 `PENDING` 또는 `SUSPECT`
- 보류항목은 공식 통계 제외
- Dashboard에는 빠진 데이터와 사유 표시

파일 전체를 불필요하게 막지 않는다.

---

## 15. 최초 데이터 품질 검증 리포트

과거 DB 일괄구축 후 다음 요약을 생성한다.

- 총 파일 / LOT / 측정값
- 정상 Mapping
- 날짜·LOT 충돌
- Spec Rev. 불명확
- 신규·누락 검사항목
- 반복값 의심
- 출하수량 불일치
- 수정본/재검
- 보류 데이터

문제항목을 닫은 뒤 해당 모델 DB를 공식 기준선으로 사용한다.

---

## 16. 현재 판단

### 확정

- 양식 선택을 사용자에게 요구하지 않는다.
- 신규 양식 첫 1건만 사용자와 Mapping을 확정한다.
- 다량파일은 대표 1건 후 예외만 확인한다.
- 수정본은 덮어쓰지 않고 이력관리한다.
- 원본 Excel은 보존한다.

### 미확정

- 실제 대표 OQC의 Header·Sample Layout
- 표준 Mapping Preview 화면
- Mapping 신뢰도 Threshold

---

## 17. Gate

### Mapping Gate

- 대표 OQC의 모든 관리대상 데이터가 원본과 일치
- 수식과 Raw Data 구분
- 실제 셀값과 표시값 구분
- 부품·항목 Mapping 사용자 승인
- 동일 양식 두 번째 파일 자동처리 성공

### Bulk Import Gate

- 대표 Mapping을 대량파일에 적용
- 예외파일만 정확히 분리
- 정상파일 오승인 요구 없음
- 데이터 품질 리포트 생성

---

## 18. Resume Point

대표 OQC가 확보되면 다음 순서로 진행한다.

1. 모든 Sheet 구조 Scan
2. 모델·부품·업체·LOT·날짜 위치 Mapping
3. 검사항목·Spec·Sample 범위 Mapping
4. 금번·누적 출하수량 위치 Mapping
5. 원본값·표시값·수식 구분 Test
6. 표준 Long Format 적재
7. 원본과 자동추출 결과를 한 화면에서 대조

---

## 19. Living Decision Override — 2026-08-15

### 공통 Inbox에서 프로젝트 선택

- Scheduler는 분류가 끝난 OQC 메일의 최종 Mail Locator만 공통 inbox에 전달한다.
- Mass Production Quality Validation는 메일함 전체를 다시 검색하지 않고 참조된 메일의 첨부만 획득한다.
- 여러 첨부는 각각 독립 intake 대상으로 유지하고, 중복키는 적어도 전달 ID와 첨부
  식별자를 포함한다.
- 프로젝트 routing은 workbook 내부의 모델, 품번, 도면, Revision 등 실제 식별값을
  우선한다. 파일명과 메일 제목은 보조 근거다.
- 후보가 없으면 `REGISTRATION_REQUIRED / PROJECT_NOT_REGISTERED`, 후보가 여러 개거나
  근거가 충돌하면 `MAPPING_REQUIRED / PROJECT_ROUTING_AMBIGUOUS`로 보류한다. 사용자 확인
  전에는 임의 프로젝트로 적재하지 않는다.
- 프로젝트별 원본 File Store, Mapping, 표준 DB와 통계 경계를 섞지 않는다.

### 대표 OQC Golden Acceptance

최종 Acceptance Owner는 실제 사용자다. 자동 Test만으로 Gate를 닫지 않고 다음 항목을
원본과 100% 대조한 Evidence를 제공한 뒤 사용자가 확인한다.

1. 모델·품번·도면·Revision·업체·LOT·검사일 등 식별자
2. 적용 Spec과 상·하한 및 단위
3. 금번 출하와 누적 출하 값 및 Source 위치
4. 모든 Raw Measurement
5. 각 값의 원본 Sheet/Cell, 원본값, 수식/Cached Value와 Mapping Revision

설명되지 않은 불일치는 0건이어야 한다. 실제 대표 OQC와 승인기준이 없을 때 Synthetic
Fixture는 Scanner Framework만 검증하며 Golden Gate를 대체하지 않는다.

### Phase 1 File Store 안전계약 — 2026-08-15

수동 등록과 향후 Scheduler 경로는 동일한 DQ 소유 intake를 사용한다. intake는 `.xlsx`와
`.xlsm` 확장자, 선언 MIME, OOXML 내부 Workbook Content Type, 명시적 최대크기를 서로
대조한다. 거부되면 Blob, Receipt, 임시파일을 부분적으로 남기지 않는다.

원본 저장이 성공한 뒤 Scanner를 별도 단계로 호출한다. 알려진 Scan 실패는
`RAW_PRESERVED_SCAN_FAILED`로 반환하고, 예기치 않은 Scanner 오류도 원인과 보존 Receipt를
잃지 않는다. 동일 Hash 재수신은 Blob 1개와 복수 Receipt로 표현한다.

이 계약은 `ING-052`, `ING-053`, `ARC-029`, `ARC-030`으로 추적한다.
