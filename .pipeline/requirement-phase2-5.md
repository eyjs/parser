# DocForge Phase 2~5 — OCR + GUI + 패키징

## 1. 개요

### 현재 상태 (Phase 1 완료)
- 클린 아키텍처 PDF 파싱 엔진 동작 중 (106 tests passed)
- 디지털 PDF 파싱 품질 검증 완료 (보험약관 81p, 사업방법서 2p, 상품요약서 5p)
- PaddleOCR 설치 완료 (paddlepaddle 3.3.1 + paddleocr 3.5.0, Python 3.13.7)
- 스켈레톤 어댑터 구현: paddle_ocr.py, paddle_table.py, ocr_corrector.py
- venv: `.venv/Scripts/python`

### Phase 2~5 목표
1. **Phase 2**: 스캔/이미지 PDF OCR 통합 — PaddleOCR 어댑터 활성화 + 파이프라인 연결 + 품질 검증
2. **Phase 3**: 기타 문서 포맷 대응 — engine.py 디스패처 + HTML/DOCX/PPTX/XLSX 파서 (PDF 우선, 나머지 스캐폴드)
3. **Phase 4**: 웹 기반 GUI — Flask + SSE, 드래그앤드롭, 좌우 분할 검증 뷰, 마크다운 편집기
4. **Phase 5**: PyInstaller 패키징 — 폴더 배포, 단일 exe → 서버+브라우저 자동 열기

### 우선순위
- PDF가 핵심. 다른 확장자는 스캐폴드만 두고 후순위
- Phase 2 (OCR) > Phase 4 (GUI) > Phase 5 (패키징) > Phase 3 (기타 포맷)

---

## 2. Phase 2 — 스캔 PDF + OCR 통합

### 2-1. 목표
사업방법서 2p 중 1p가 `image_heavy`로 분류됨. 이 페이지를 PaddleOCR로 인식하여 텍스트 추출.

### 2-2. 현재 자산
| 파일 | 상태 | 필요 작업 |
|---|---|---|
| `adapters/paddle_ocr.py` | 완성 (PaddleOCREngine) | 실제 OCR 호출 테스트 + 결과 검증 |
| `adapters/paddle_table.py` | 완성 (PaddleTableExtractor) | PP-Structure 가용성 확인 + 실제 테스트 |
| `processing/ocr_corrector.py` | 완성 (correct_blocks) | 보험 용어 사전 확장 + 실제 OCR 결과 기반 보정 맵 보강 |
| `usecases/parse_pdf.py` | OCR 분기 로직 존재 | 실제 동작 확인 (SCANNED/MIXED 분기) |
| `infrastructure/config.py` | OCR 관련 설정 있음 | PaddleOCR 모델 경로, DPI 등 설정 점검 |

### 2-3. 구현 태스크

#### T2-1: PaddleOCR 실제 동작 검증
- `.venv/Scripts/python`에서 PaddleOCR import + 한국어 OCR 실행 테스트
- PP-OCRv5 모델 자동 다운로드 확인 (오프라인 번들은 Phase 5에서)
- PP-StructureV3 import 가능 여부 확인 → 불가 시 테이블 OCR 대안 마련
- 테스트 이미지: 사업방법서 p2를 PyMuPDF render_page_image()로 추출

#### T2-2: OCR 파이프라인 통합 테스트
- `parse_pdf.py`에서 `force_ocr=True`로 사업방법서 2p 파싱
- SCANNED 페이지 → PaddleOCREngine.recognize() 호출 확인
- OCR 결과 → noise_detector → text_structurer → line_merger → markdown_assembler 전체 파이프 검증
- MIXED 페이지의 digital + OCR 병합 (overlap 제거) 동작 확인

#### T2-3: OCR 결과 보정 강화
- 실제 OCR 출력 기반으로 `config.ocr_correction_map` 보강
- 보험 용어 사전 추가: `ParserConfig.insurance_terms` 필드
  - "피보험자", "보험수익자", "보험금", "보험료", "해지환급금", "만기", "갱신"
  - "보험계약자", "보험회사", "보장", "약관", "특약", "면책", "자기부담금"
- OCR confidence 기반 페이지 레벨 품질 리포트

#### T2-4: OCR 전용 테스트
- `tests/unit/test_paddle_ocr.py` — 모킹으로 recognize() 결과 형태 테스트
- `tests/unit/test_paddle_table.py` — 모킹으로 extract_from_image() 테스트
- `tests/integration/test_ocr_pipeline.py` — 실제 PDF로 OCR 파이프라인 E2E (OCR 마커)
- 기존 106개 테스트 깨지지 않아야 함

### 2-4. OCR 설정 (ParserConfig 추가)
```python
# PaddleOCR
ocr_lang: str = "korean"
ocr_use_angle_cls: bool = True
ocr_det_db_thresh: float = 0.3
ocr_rec_batch_num: int = 6

# OCR 품질 임계값
ocr_confidence_low: float = 0.8   # 이하 → 경고 마킹
ocr_confidence_fail: float = 0.5  # 이하 → 실패 마킹

# 보험 용어 사전 (OCR 보정 시 참조)
insurance_terms: tuple[str, ...] = (
    "피보험자", "보험수익자", "보험금", "보험료", "해지환급금",
    "만기", "갱신", "보험계약자", "보험회사", "보장", "약관",
    "특약", "면책", "자기부담금", "보상", "담보",
)
```

### 2-5. 성공 기준
1. 사업방법서 image_heavy 페이지에서 텍스트 추출됨 (OCR)
2. OCR 결과가 한국어 보정을 거쳐 읽을 수 있는 품질
3. OCR confidence < 0.8 블록에 경고 표시
4. 디지털 PDF 파싱이 깨지지 않음 (기존 106 테스트 통과)
5. OCR 비설치 환경에서 graceful skip (ImportError 없음)

---

## 3. Phase 3 — 기타 문서 포맷 (스캐폴드)

### 3-1. 목표
PDF 외 문서 포맷 대응의 기반을 마련. 실제 구현은 최소한, 확장 포인트만 제공.

### 3-2. 아키텍처

#### engine.py — 문서 디스패처
```python
# docforge/usecases/engine.py
# ai-platform engine.py 패턴 채택: 확장자 → MIME → 파서 라우팅

class DocumentEngine:
    """문서 파싱 진입점. 확장자/MIME 기반으로 적절한 파서 라우팅."""

    def parse(self, path: Path) -> ParseResult:
        """파일 확장자를 감지하여 적절한 파서로 위임."""
        ...
```

#### FormatParser Protocol
```python
# docforge/domain/ports.py에 추가
class FormatParser(Protocol):
    """문서 포맷별 파서 인터페이스."""
    def can_parse(self, path: Path) -> bool: ...
    def parse(self, path: Path, config: ParserConfig) -> ParseResult: ...
    def supported_extensions(self) -> tuple[str, ...]: ...
```

### 3-3. 구현 태스크

#### T3-1: DocumentEngine + FormatParser Protocol
- `docforge/usecases/engine.py` — 확장자→MIME→파서 디스패치
- `docforge/domain/ports.py`에 FormatParser Protocol 추가
- PDF 파서를 FormatParser로 래핑 (기존 parse_pdf를 어댑터로)

#### T3-2: HTML 파서 (스캐폴드)
- `docforge/adapters/html_parser.py`
- BeautifulSoup으로 본문 추출 + 노이즈(nav, sidebar, footer) 제거
- 기본 동작만 구현, 테스트 1-2개

#### T3-3: DOCX 파서 (스캐폴드)
- `docforge/adapters/docx_parser.py`
- python-docx로 텍스트 + 표 + 이미지 추출 → Markdown
- 기본 동작만 구현, 테스트 1-2개

#### T3-4: 기타 포맷 (최소 스캐폴드)
- `docforge/adapters/pptx_parser.py` — 슬라이드별 텍스트
- `docforge/adapters/excel_parser.py` — 시트별 Markdown 테이블
- 각 파서는 `FormatParser` Protocol 준수
- 선택적 의존성 (설치 안 되면 graceful skip)

### 3-4. 선택적 의존성
```toml
[project.optional-dependencies]
html = ["beautifulsoup4>=4.12.0", "lxml>=5.0.0"]
office = ["python-docx>=1.1.0", "python-pptx>=1.0.0", "openpyxl>=3.1.0"]
```

### 3-5. 성공 기준
1. `DocumentEngine.parse("file.pdf")` → 기존 PDF 파서로 라우팅
2. 지원하지 않는 확장자 → 명확한 에러 메시지
3. HTML/DOCX 파서 기본 동작 (선택적 의존성 설치 시)
4. 기존 PDF 파싱 테스트 깨지지 않음

---

## 4. Phase 4 — 웹 기반 GUI

### 4-1. 목표
비개발자가 exe 더블클릭 → 브라우저에서 문서 파싱 + 검증 + 편집.

### 4-2. 기술 스택
| 구분 | 선택 | 이유 |
|---|---|---|
| 서버 | Flask | 경량, 빠른 구현 |
| 실시간 통신 | SSE (Server-Sent Events) | 단방향 스트림, 복잡도 낮음 |
| 프론트엔드 | 순수 HTML/CSS/JS | 빌드 도구 불필요, PyInstaller 번들 용이 |
| 마크다운 렌더링 | marked.js | 클라이언트 렌더링, 경량 |
| PDF 뷰어 | pdf.js | 브라우저 내 PDF 렌더링 |
| 코드 편집기 | CodeMirror 6 | 마크다운 편집 + 신택스 하이라이팅 |

### 4-3. 화면 구성

#### 메인 대시보드 (`/`)
- 드래그 앤 드롭 파일 업로드 영역
- 최근 변환 이력 (로컬 SQLite or JSON)
- 파싱 진행률 (SSE 스트림)
- 파싱 완료 → 자동으로 검증 뷰 이동

#### 검증 뷰 (`/verify/<id>`)
- 좌: 원본 PDF 렌더링 (pdf.js)
- 우: 파싱 결과 마크다운 렌더링 (marked.js)
- 페이지 동기 네비게이션 (좌우 연동)
- 품질 지표 사이드 패널 (heading_count, table_count, warnings)

#### 편집 뷰 (`/edit/<id>`)
- CodeMirror 마크다운 편집기
- 실시간 프리뷰 (split view)
- 저장/내보내기 (MD, DOCX 선택)

### 4-4. API 설계

```
POST   /api/parse          — 파일 업로드 + 파싱 시작 → task_id 반환
GET    /api/parse/<id>/status — SSE 스트림 (진행률)
GET    /api/parse/<id>/result — 파싱 결과 (markdown, metadata, stats)
GET    /api/history         — 최근 변환 이력
DELETE /api/history/<id>    — 이력 삭제
POST   /api/save/<id>       — 편집 내용 저장
GET    /api/export/<id>     — 파일 다운로드 (format=md|docx)
```

### 4-5. 구현 태스크

#### T4-1: Flask 서버 기반
- `docforge/web/__init__.py`
- `docforge/web/app.py` — Flask 앱 팩토리
- `docforge/web/routes.py` — API 라우트
- `docforge/web/sse.py` — SSE 이벤트 스트림 헬퍼
- 정적 파일 서빙 (`docforge/web/static/`)
- 템플릿 (`docforge/web/templates/`)

#### T4-2: 파싱 API + SSE 진행률
- 파일 업로드 → 임시 저장 → 백그라운드 파싱
- threading.Thread로 파싱 실행 (Flask dev server)
- SSE로 단계별 진행률 스트림:
  - `profiling`, `noise_learning`, `page_processing(N/total)`, `table_merging`, `assembling`, `done`
- 에러 시 SSE로 에러 이벤트 전송

#### T4-3: 메인 대시보드 UI
- 드래그 앤 드롭 파일 업로드 (HTML5 File API)
- 파싱 진행률 프로그레스 바 (SSE EventSource)
- 최근 변환 이력 리스트
- 완료 시 검증 뷰 버튼

#### T4-4: 검증 뷰 UI
- pdf.js로 원본 PDF 렌더링
- marked.js로 마크다운 렌더링
- 좌우 분할 레이아웃 (CSS flex/grid)
- 페이지 번호 클릭 시 양쪽 동기 스크롤
- 품질 지표 패널

#### T4-5: 마크다운 편집기 UI
- CodeMirror 6 편집기 임베드
- 실시간 프리뷰 (debounced marked.js 렌더링)
- 저장 버튼 → POST /api/save
- 내보내기 (MD 다운로드)

#### T4-6: 변환 이력 관리
- `docforge/web/storage.py` — JSON 기반 로컬 스토리지
- 파싱 결과 + 메타데이터 저장 (uploads/ 디렉토리)
- 이력 CRUD

### 4-6. 파일 구조
```
docforge/web/
├── __init__.py
├── app.py              # Flask 앱 팩토리
├── routes.py           # API 라우트
├── sse.py              # SSE 헬퍼
├── storage.py          # 로컬 파일 스토리지
├── static/
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   ├── main.js         # 대시보드 로직
│   │   ├── verify.js       # 검증 뷰 로직
│   │   ├── editor.js       # 편집기 로직
│   │   └── lib/
│   │       ├── marked.min.js
│   │       └── pdf.min.js   # + pdf.worker.min.js
│   └── img/
└── templates/
    ├── base.html
    ├── dashboard.html
    ├── verify.html
    └── editor.html
```

### 4-7. 디자인 가이드
- 깔끔한 미니멀 UI (사내 도구)
- 다크/라이트 테마 토글 (CSS 변수)
- 반응형 불필요 (데스크톱 전용)
- 한국어 UI 텍스트
- CSS 변수로 모든 색상/폰트/간격 관리

### 4-8. 성공 기준
1. 드래그 앤 드롭으로 PDF 업로드 → 파싱 완료
2. 파싱 진행률이 실시간으로 표시됨
3. 원본 PDF와 파싱 결과를 좌우 비교 가능
4. 마크다운 편집 + 실시간 프리뷰 동작
5. 저장/내보내기 동작
6. 기존 CLI 파싱 기능이 깨지지 않음

---

## 5. Phase 5 — PyInstaller 패키징

### 5-1. 목표
비개발자가 exe 더블클릭 → Flask 서버 기동 → 브라우저 자동 열림.

### 5-2. 빌드 전략
- **PyInstaller `--onedir`** (폴더 배포)
- 단일 진입점 exe: `docforge.exe`
- 실행 시: Flask 서버 시작 → 기본 브라우저 `http://localhost:PORT` 자동 열기
- 빈 포트 자동 탐색 (8000~8100)
- 트레이 아이콘 or 콘솔 창에서 서버 상태 표시 + 종료 버튼

### 5-3. 번들 구성
```
docforge_dist/
├── docforge.exe           # 메인 진입점
├── _internal/             # PyInstaller 내부
│   ├── docforge/          # 패키지 코드
│   ├── web/static/        # 정적 파일
│   ├── web/templates/     # 템플릿
│   └── ...
├── models/                # PaddleOCR 모델 (선택, ~300-500MB)
│   ├── det/               # 텍스트 감지 모델
│   ├── rec/               # 텍스트 인식 모델
│   └── cls/               # 각도 분류 모델
└── uploads/               # 업로드/결과 저장 (런타임 생성)
```

### 5-4. 구현 태스크

#### T5-1: 진입점 스크립트
- `docforge/launcher.py` — exe 진입점
  - 빈 포트 탐색
  - Flask 서버 서브프로세스 시작
  - 브라우저 자동 열기 (`webbrowser.open`)
  - Ctrl+C / 창 닫기 시 서버 graceful shutdown
  - 시스템 트레이 아이콘 (pystray, 선택)

#### T5-2: PyInstaller 스펙 파일
- `docforge.spec` — PyInstaller 빌드 설정
- hidden imports: flask, paddleocr, paddlepaddle 등
- data files: templates, static, models
- 리소스 경로를 sys._MEIPASS 대응

#### T5-3: 리소스 경로 추상화
- `docforge/infrastructure/paths.py`
  - `get_base_dir()` — 개발 vs PyInstaller 분기
  - `get_static_dir()`, `get_template_dir()`, `get_upload_dir()`, `get_model_dir()`
  - 모든 파일 접근 이 함수 경유

#### T5-4: PaddleOCR 모델 번들링
- 모델 파일 사전 다운로드 스크립트: `scripts/download_models.py`
- `ParserConfig.model_dir` 설정으로 모델 경로 지정
- PaddleOCR 초기화 시 `model_dir` 전달
- 모델 없으면 OCR 비활성화 (graceful degradation)

#### T5-5: 빌드 + 테스트 스크립트
- `scripts/build.py` or `Makefile`
  - `python -m PyInstaller docforge.spec`
  - 빌드 후 `dist/docforge/docforge.exe` 실행 테스트
- 빌드 시간/크기 최적화 (excludes 설정)

### 5-5. 성능 요구
| 항목 | 목표 |
|---|---|
| exe 시작 → 브라우저 열림 | 5초 이내 |
| 번들 크기 (OCR 미포함) | 100MB 이내 |
| 번들 크기 (OCR 포함) | 500MB 이내 |
| 메모리 (idle) | 200MB 이내 |

### 5-6. 성공 기준
1. `docforge.exe` 더블클릭 → 브라우저에서 대시보드 열림
2. PDF 업로드 → 파싱 → 결과 확인까지 GUI에서 완료
3. OCR 모델 없어도 디지털 PDF 파싱 동작
4. Windows 10/11에서 동작

---

## 6. 공통 제약사항

### 필수
- venv 필수 (`.venv/Scripts/python`), 전역 pip 금지
- 기존 Phase 1 아키텍처 유지 (클린 아키텍처, 포트/어댑터)
- 테스트 필수 (각 Phase별 단위 + 통합)
- 3개 샘플 PDF로 검증
- 오프라인 완결, GPU 불필요

### 선택적 의존성 정책
- PaddleOCR: 없으면 OCR 비활성화, 디지털 PDF는 정상 동작
- HTML/Office 파서: 없으면 해당 포맷 비지원 메시지
- Flask: 없으면 CLI만 동작 (GUI 비활성화)

### pyproject.toml 의존성 그룹
```toml
[project.optional-dependencies]
ocr = ["paddlepaddle>=2.6.0", "paddleocr>=2.7.0"]
html = ["beautifulsoup4>=4.12.0", "lxml>=5.0.0"]
office = ["python-docx>=1.1.0", "python-pptx>=1.0.0", "openpyxl>=3.1.0"]
web = ["flask>=3.0.0"]
build = ["pyinstaller>=6.0.0"]
dev = ["pytest>=8.0.0", "pytest-cov>=5.0.0", "mypy>=1.8.0", "ruff>=0.3.0"]
all = ["docforge[ocr,html,office,web,build,dev]"]
```

---

## 7. 실행 순서

1. **Phase 2** (OCR) — 기존 스켈레톤 활성화 + 통합 테스트
2. **Phase 4** (GUI) — Flask 서버 + 웹 UI
3. **Phase 5** (패키징) — PyInstaller exe
4. **Phase 3** (기타 포맷) — 스캐폴드만 (PDF 우선)

Phase 3은 스캐폴드 수준이므로 Phase 4/5와 병행 또는 후속 가능.

---

## 8. 전체 성공 기준

1. `docforge.exe` 더블클릭 → 브라우저에서 PDF 업로드 → 파싱 → 좌우 비교 → 편집 → 저장
2. 디지털 PDF: 기존 품질 유지 (구조 보존, 줄바꿈 병합, 표 추출)
3. 스캔 PDF: PaddleOCR로 텍스트 추출 + 한국어 보정
4. 파싱 진행률 실시간 표시
5. 오프라인 동작, GPU 불필요
6. 비개발자가 사용 가능한 UX
