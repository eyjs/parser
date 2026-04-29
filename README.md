# DocForge

보험/법률/금융 문서 특화 PDF-to-Markdown 파싱 엔진.

스캔 PDF와 디지털 PDF를 모두 처리하며, OCR 전처리 품질 게이트를 통해 원본 우선 원칙으로 최적 결과를 선택한다.

## 주요 기능

- **디지털/스캔 자동 판별** — 페이지 단위로 텍스트 레이어 유무를 분석하여 최적 경로 라우팅
- **이미지 품질 진단 + 조건부 전처리** — 5개 지표(DPI, 기울기, 대비, 노이즈, 배경 균일성)를 독립 측정 후 문제 항목만 선택적 전처리
- **전처리 품질 게이트** — 원본 vs 전처리 OCR 결과를 A/B 비교하여 더 나은 쪽 채택
- **멀티컬럼 레이아웃 감지** — 2/3단 컬럼 문서의 읽기 순서 자동 복원
- **테이블 추출** — 병합셀(colspan/rowspan) 지원, 목차 리더dots 자동 필터링
- **한국어 법률 구조 인식** — 제/조/항/호/목 계층 구조 자동 분류
- **플러거블 OCR 백엔드** — EasyOCR, Apple Vision(macOS), PaddleOCR 런타임 선택
- **Flask 웹 GUI** — 파싱 결과 실시간 미리보기, PDF-마크다운 동기 스크롤, 검증 UI

## 아키텍처

```
docforge/
├── domain/          # 도메인 모델, 프로토콜 (외부 의존 없음)
│   ├── models.py        # TextBlock, Table, PageContent, ParseResult
│   ├── value_objects.py # BBox, RawImage, ImageQualityReport, QualityGateResult
│   ├── ports.py         # PDFReader, OCREngine, ImagePreprocessor 프로토콜
│   └── enums.py         # PageType, BlockType, SelectionReason
│
├── processing/      # 순수 로직 (numpy만 허용, 외부 라이브러리 금지)
│   ├── image_diagnostics.py     # 이미��� 품질 5개 지표 측정
│   ├── preprocessing_router.py  # 진단 → 판단 → 전처리 → 품질 게이트 오케스트레이션
│   ├── quality_gate.py          # 원본 vs 전처리 A/B 비교
│   ├── page_classifier.py       # DIGITAL / SCANNED / MIXED / NOISE 분류
│   ├── text_structurer.py       # 제/조/항/호/목 구조 인식
│   ├── line_merger.py           # 줄바꿈 병합 (한국어 조사/접속사 인식)
│   ├── column_detector.py       # 멀티컬럼 레이아웃 감지
│   ├── confidence_scorer.py     # 페이지별 신뢰도 점수
│   └── markdown_assembler.py    # 최종 마크다운 조립
│
├── adapters/        # 외부 라이브러리 의존 (포트 구현체)
│   ├── pymupdf_reader.py        # PyMuPDF PDF 리더
│   ├── easyocr_engine.py        # EasyOCR 어댑터
│   ├── apple_vision_engine.py   # Apple Vision OCR (macOS)
│   ├── opencv_preprocessor.py   # OpenCV 이미지 전처리
│   ├── pdfplumber_tables.py     # pdfplumber 테이블 추출
│   └── image_converter.py       # PIL ↔ RawImage 변환
│
├── usecases/        # 유스케이스 오케스트레이션
│   ├── parse_pdf.py         # 메인 파싱 파이프라인
│   └── ocr_factory.py       # OCR 백엔드 팩토리
│
├── infrastructure/  # 설정, 메타데이터
│   └── config.py
│
└── web/             # Flask 웹 GUI
    ├── app.py
    ├── routes.py
    └── templates/
```

## 파싱 파이프라인

```
PDF 입력
 │
 ├── DIGITAL 페이지 → PyMuPDF 텍스트 추출
 │                     → text_structurer → line_merger → markdown
 │
 └── SCANNED 페이지 → 이미지 렌더링 (300 DPI)
                       → 품질 진단 (5개 지표)
                       ├── 양호 → 원본 OCR
                       └── 문제 → 선택적 ���처리 → 품질 게이트 (A/B)
                                                    → 승리 결과 채택
                       → line_merger → text_structurer → markdown
```

## 설치

```bash
# 기본 (디지털 PDF만)
pip install -e .

# OCR 포함
pip install -e ".[ocr]"

# 웹 GUI 포함
pip install -e ".[web]"

# 전체
pip install -e ".[all]"

# 이미지 전처리 (선택)
pip install opencv-python-headless numpy
```

## 사용법

### CLI

```bash
# 기본 파싱
docforge parse input.pdf -o output.md

# OCR 강제 모드
docforge parse input.pdf -o output.md --force-ocr

# OCR 백엔드 지정
docforge parse input.pdf -o output.md --ocr-backend apple_vision
```

### 웹 GUI

```bash
docforge-gui
# http://localhost:5001 에서 접근
```

### Python API

```python
from pathlib import Path
from docforge.usecases.parse_pdf import parse_pdf

result = parse_pdf(Path("input.pdf"))
print(result.markdown)
print(f"Pages: {result.stats.parsed_pages}")
print(f"Tables: {result.stats.tables_found}")
```

## OCR 백엔드

| 백엔드 | 플랫폼 | 자동 우선순위 | 설치 |
|--------|--------|--------------|------|
| Apple Vision | macOS | macOS에서 1순위 | `pip install pyobjc-framework-Vision` |
| EasyOCR | 전 플랫폼 | Windows/Linux에서 1순위 | `pip install easyocr` |
| PaddleOCR | 전 플랫폼 | 폴백 | `pip install paddleocr` |

`config.ocr_backend = "auto"` (기본값)이면 플랫폼에 따라 최적 백엔드를 자동 선택한다.

## 테스트

```bash
# 유닛 테스트
pytest tests/unit/ -q

# 통합 테스트 (PDF 파일 필요)
pytest tests/integration/ -q

# 전체
pytest tests/ -q
```

## 기술스택

- Python 3.11+
- PyMuPDF, pdfplumber, Pillow
- EasyOCR / Apple Vision (pyobjc) / PaddleOCR
- OpenCV (이미지 전처리)
- Flask (웹 GUI)
- PyInstaller (데스크탑 배포)

## 라이선스

Private
