# DocForge PDF 파싱 시스템 — 상용화 Phase 1

## 생성일시
2026-04-28 19:00

## 목적
- 왜 만드는가: 보험/법률/금융 문서의 PDF 파싱 품질을 상용 수준으로 끌어올리기 위한 독립 R&D 프로젝트. 파싱 엔진이 충분히 성숙하면 AI Platform의 PDF 파싱 엔진으로 이식한다.
- 누가 사용하는가: 1차 타겟 — 보험/법률 업무 담당자 (데스크탑 앱). 2차 타겟 — AI Platform 내부 파싱 엔진으로 편입
- 기대 효과: 복잡한 보험약관/법률 문서의 텍스트·테이블 추출 정확도를 95% 이상으로 높여 후처리 LLM 비용 절감 및 데이터 품질 신뢰성 확보

---

## 스코프

### 포함 (이번에 만드는 것)

#### Phase 1 — 현재 파이프라인 고도화
- [ ] 줄바꿈 병합 로직 완결성 95%+ 달성 (현재 85%)
- [ ] 테이블 추출 개선: 병합셀(merged cell) 지원
- [ ] 테이블 추출 개선: 목차 리더dots(……) 행 필터링
- [ ] 멀티컬럼 레이아웃 자동 감지 및 읽기 순서 보장
- [ ] Apple Vision Framework OCR 어댑터 구현 (macOS / pyobjc)
- [ ] OCR 백엔드 플러거블 아키텍처 확립 (EasyOCR / AppleVision / 향후 확장)
- [ ] 파싱 신뢰도 점수 시스템 — 페이지별 신뢰도 산출 및 저신뢰 페이지 표시

#### Phase 2 — LLM 통합
- [ ] Qwen2-VL-7B MLX VisionLLMBackend 어댑터 구현
- [ ] 하이브리드 폴백: 규칙 기반 파싱 실패 시 LLM 자동 투입
- [ ] LLM 프롬프트 최적화 (보험약관 테이블/조항 추출 특화)

#### Phase 3 — 이식 준비
- [ ] 파싱 엔진 코어를 웹 UI에서 완전 분리 (독립 라이브러리화)
- [ ] REST API 모드 추가 (JSON structured output)
- [ ] AI Platform 이식용 인터페이스 정의 (추상 계층 명세)

### 제외 (이번에 만들지 않는 것)
- AI Platform 실제 연동 및 이식 (Phase 3 이후 별도 프로젝트)
- 클라우드 배포 / SaaS 형태 서비스
- 외부 OCR API (Google Vision, AWS Textract 등) 연동
- Word/Excel 등 비-PDF 포맷 지원
- 모바일 지원

---

## 기술스택

### 기존 (유지)
- 언어: Python 3.11+
- 웹 UI: Flask 3.x
- PDF 처리: PyMuPDF 1.24+, pdfplumber 0.11+
- OCR: EasyOCR 1.7+ (기존 어댑터 유지)
- 이미지 처리: Pillow 10+
- 빌드: PyInstaller 6+ (데스크탑 앱 배포)
- 테스트: pytest 8+, pytest-cov
- 린트/타입: ruff, mypy (strict)

### 추가 (Phase별)
- Phase 1: pyobjc-framework-Vision (macOS Apple Vision OCR)
- Phase 2: mlx, mlx-lm, qwen2-vl (Qwen2-VL-7B MLX 추론)

### 인프라
- 실행 환경: Mac Studio M1 Max, 64GB RAM (로컬 전용)
- 외부 API 의존: 없음 (전량 로컬 처리)

---

## 핵심 기능

### P0 (필수 — Phase 1 완료 기준)

**줄바꿈 병합 완결성 95%+**
- 현재 `processing/line_merger.py` 기준 85% 수준
- 보험약관 특유의 짧은 항목 줄바꿈, 법률 문서의 조항 번호 줄바꿈 패턴 처리
- 완결 기준: 테스트셋 기준 줄바꿈 오병합/미병합 합산 오류율 5% 이하

**테이블 추출 개선 — 병합셀 지원**
- 현재 `adapters/pdfplumber_tables.py` + `processing/table_merger.py`에서 병합셀 미지원
- colspan/rowspan 구조 감지 후 Markdown 테이블 또는 구조화 JSON으로 표현
- 보험료율표, 보장내용표 등 복잡 테이블 정확 추출

**테이블 추출 개선 — 목차 리더dots 필터링**
- "제1조 ………………………… 3" 형태 목차 행을 테이블이 아닌 목차 블록으로 재분류
- 파싱 결과의 테이블 오분류 제거

**멀티컬럼 레이아웃 감지 및 읽기 순서 보장**
- 2단/3단 컬럼 문서의 좌→우 읽기 순서 자동 복원
- `processing/page_classifier.py`에서 레이아웃 유형 감지 후 `processing/text_structurer.py`에 컬럼 순서 전달

**이미지 품질 진단 + 조건부 전처리 (Image Quality Assessment)**
- `processing/image_preprocessor.py` 신규 구현
- 스캔/이미지 페이지에 대해 **전처리 적용 여부를 측정값 기반으로 개별 판단**
- 판단 기준 (각 항목을 독립적으로 측정 후 해당 전처리만 적용):

| 진단 항목 | 측정 방법 | 전처리 적용 조건 | 적용 기법 |
|-----------|-----------|-----------------|-----------|
| 해상도 | 이미지 DPI 또는 텍스트 높이(px) 추정 | DPI < 200 또는 텍스트 높이 < 20px | Lanczos 업스케일 → 300DPI |
| 기울기 | Hough Line 또는 Projection Profile 분산 | skew > 0.5도 | affine 회전 보정 |
| 대비 | Michelson contrast 또는 히스토그램 분석 | contrast ratio < 임계값 | CLAHE 적응형 대비 보정 |
| 노이즈 | 라플라시안 분산(blur 감지) + salt-pepper 비율 | noise score > 임계값 | median filter (kernel 3) |
| 배경 균일성 | 지역별 밝기 분산 측정 | 분산 > 임계값 (불균일 조명) | adaptive binarization (Sauvola) |

- **핵심 원칙**: 양호한 항목은 원본 유지. 전처리는 문제가 감지된 항목에만 선택적 적용
- **안티패턴 방지**:
  - 깨끗한 고해상도 스캔에 이진화 적용 금지 (정보 손실)
  - 이미 양호한 대비에 CLAHE 적용 금지 (과보정)
  - 과도한 노이즈 제거로 세리프체 미세 획 손상 금지
- 진단 결과를 `ImageQualityReport` 데이터 클래스로 반환 → 파싱 신뢰도 점수 산출에 활용
- DIGITAL 페이지는 이 단계를 완전히 건너뜀 (PyMuPDF 직접 추출)

**전처리 품질 게이트 (Preprocessing Quality Gate)**
- 전처리 적용 전/후 OCR 결과를 비교하여 더 나은 쪽을 채택하는 검증 로직
- 비교 기준:
  - OCR 엔진의 confidence score 평균 비교 (원본 vs 전처리)
  - 인식된 문자 수 비교 (전처리로 텍스트가 소실되면 탈락)
  - 한글 비율 검증 (readable character ratio가 하락하면 탈락)
- 판정 흐름:
  1. 원본 이미지 → OCR → 결과A + 신뢰도A
  2. 전처리 이미지 → OCR → 결과B + 신뢰도B
  3. 신뢰도B > 신뢰도A + margin(0.02) → 결과B 채택
  4. 그 외 → 결과A 채택 (원본 우선 원칙)
- **원본 우선 원칙**: 동점이거나 미미한 차이면 전처리를 적용하지 않은 원본 결과를 우선
- 비용 최적화: 품질 진단에서 "양호" 판정된 페이지는 A/B 비교 없이 원본으로 직행
- 비교 결과 로깅: 어떤 페이지에서 전처리가 채택/기각되었는지 기록 → 임계값 튜닝에 활용

### P1 (중요 — Phase 1~2 내 완료 목표)

**Apple Vision OCR 어댑터**
- `adapters/apple_vision_engine.py` 신규 구현
- pyobjc-framework-Vision 기반, macOS 전용
- 기존 `adapters/easyocr_engine.py` 인터페이스와 동일한 추상 계층 준수
- 한글/영문 혼용 문서 인식 정확도 95%+

**OCR 백엔드 플러거블 아키텍처**
- `OCRBackend` 추상 기반 클래스 정의 (기존 `domain/ports.py` 확장 또는 정비)
- 런타임 설정(config)으로 백엔드 선택: `easyocr` / `apple_vision` / `vision_llm`
- 신규 백엔드 추가 시 기존 코드 수정 없이 어댑터만 추가

**Qwen2-VL-7B MLX VisionLLMBackend 어댑터** (Phase 2)
- `adapters/vision_llm_engine.py` 신규 구현
- MLX 프레임워크 기반, M1 Max 최적화
- 입력: 페이지 이미지, 출력: 구조화 텍스트/테이블 JSON

**하이브리드 폴백 파이프라인** (Phase 2)
- 규칙 기반 파싱 신뢰도 점수가 임계값(예: 0.7) 미만인 페이지에 대해 LLM 백엔드 자동 투입
- 폴백 발생 이력 로깅 (어떤 페이지가 왜 폴백됐는지)

### P2 (있으면 좋음)

**파싱 신뢰도 점수 시스템**
- 페이지별 신뢰도 점수 산출 (0.0~1.0)
- 저신뢰 페이지 웹 UI에 시각적 표시 (배지 또는 경고)
- 신뢰도 점수를 JSON output에 포함

**LLM 프롬프트 최적화** (Phase 2)
- 보험약관 테이블/면책조항/특약 조항 추출 전용 프롬프트 템플릿
- Few-shot 예시 포함

**파싱 엔진 코어 독립 라이브러리화** (Phase 3)
- Flask 웹 레이어와 파싱 코어 완전 분리
- `docforge-core` 독립 패키지로 추출 가능한 구조

**REST API 모드** (Phase 3)
- `POST /api/parse` 엔드포인트: PDF → JSON structured output
- 응답 포맷: `{ success, data: { pages: [...], tables: [...], confidence: {...} }, error }`

### P3 (Phase 3 이후)

**AI Platform 이식용 인터페이스 정의**
- 파싱 엔진 추상 인터페이스 문서화
- AI Platform 연동 시 구현해야 할 어댑터 명세

---

## 제약사항

| 항목 | 내용 |
|------|------|
| 실행 환경 | Mac Studio M1 Max, 64GB RAM 전용 최적화 |
| 배포 방식 | PyInstaller 데스크탑 앱 유지 (Phase 3 전까지 변경 없음) |
| 외부 의존 | 외부 API 호출 금지 — 전량 로컬 처리 |
| AI Platform 연동 | Phase 3 이후 별도 프로젝트로 분리 — 현재 R&D 단계에서는 연동 불필요 |
| Python 버전 | 3.11+ |
| 기존 인터페이스 | 현재 CLI (docforge) 및 웹 GUI (docforge-gui) 동작 유지 |
| Apple Vision | macOS 전용 — 크로스플랫폼 빌드 시 graceful degradation (EasyOCR 폴백) |
| MLX | Apple Silicon 전용 — 향후 비-Apple 환경 지원 불필요 |

---

## 성공 기준

| 지표 | 목표값 | 측정 방법 |
|------|--------|-----------|
| 텍스트 추출 정확도 (디지털 PDF) | 95%+ | 보험약관 테스트셋 기준 문자 단위 정확도 |
| OCR 정확도 (스캔 PDF) | 95%+ | Apple Vision 기준, 동일 테스트셋 |
| 복잡 테이블(병합셀) 추출 정확도 | 90%+ | 보험료율표 20건 수동 검증 |
| 처리 속도 — 디지털 PDF | A4 1페이지 < 2초 | M1 Max 로컬 측정 |
| 처리 속도 — 스캔 PDF (OCR) | A4 1페이지 < 5초 | Apple Vision 기준 |
| 줄바꿈 병합 오류율 | 5% 이하 | 테스트셋 오병합+미병합 합산 |
| 멀티컬럼 읽기 순서 정확도 | 95%+ | 2단 컬럼 문서 10건 수동 검증 |
| 기존 테스트 커버리지 | 유지 또는 향상 (현재 수준 이상) | pytest-cov |

---

## 특이사항

- **도메인 특화**: 보험/법률/금융 문서는 일반 PDF와 달리 중첩 테이블, 각주 번호, 조항 계층 구조, 리더dots 목차가 빈번하므로 범용 PDF 파서 대비 도메인 특화 후처리가 필수
- **스캔 비중**: 입력 PDF의 약 50%가 스캔본이므로 OCR 파이프라인은 1등 시민(first-class) 처리 경로로 취급
- **하이브리드 전략**: 규칙 기반(빠름/저비용)과 LLM 기반(정확/고비용)을 신뢰도 점수로 자동 라우팅하여 처리 시간과 정확도 균형 확보
- **이식 대비 설계**: Phase 1~2에서 작성하는 모든 어댑터는 추상 인터페이스 기반으로 구현하여 Phase 3 라이브러리화 시 재작업 최소화
- **기존 아키텍처 준수**: `adapters/`, `processing/`, `domain/`, `usecases/` 레이어 구조 유지. 신규 기능은 기존 레이어 규칙에 따라 배치
- **테스트셋 구축 필요**: 성공 기준 측정을 위한 보험약관/법률 문서 샘플 테스트셋 (`tests/fixtures/`) 정비가 선행되어야 함
- **기존 requirement.md 관계**: 이 문서는 기존 Phase 1 클린 아키텍처 구현 requirement를 대체하는 것이 아니라, 그 위에서 상용화 품질을 끌어올리는 2차 목표를 정의한다. 클린 아키텍처 구조(domain/usecases/processing/adapters)는 그대로 유지한다.
