"""Phase 0 — Apple Vision OCR Quality Test.

Renders PDF pages to images, runs Apple Vision OCR, and compares
against embedded text (ground truth) to measure quality scores.
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).parent))

from docforge.adapters.apple_vision_engine import AppleVisionOCREngine
from docforge.adapters.image_converter import pil_to_raw_image


def render_page(pdf_path: Path, page_idx: int, dpi: int = 200):
    doc = fitz.open(str(pdf_path))
    page = doc[page_idx]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    from PIL import Image
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


def get_ground_truth(pdf_path: Path, page_idx: int) -> str:
    doc = fitz.open(str(pdf_path))
    text = doc[page_idx].get_text().replace(chr(1), " ")
    doc.close()
    return normalize(text)


def normalize(text: str) -> str:
    text = re.sub(r"[·…]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def char_accuracy(ocr_text: str, ground_truth: str) -> float:
    if not ground_truth:
        return 1.0 if not ocr_text else 0.0
    sm = difflib.SequenceMatcher(None, ground_truth, ocr_text)
    return sm.ratio()


def word_accuracy(ocr_text: str, ground_truth: str) -> float:
    gt_words = ground_truth.split()
    ocr_words = ocr_text.split()
    if not gt_words:
        return 1.0 if not ocr_words else 0.0
    sm = difflib.SequenceMatcher(None, gt_words, ocr_words)
    return sm.ratio()


def line_order_score(ocr_text: str, ground_truth: str) -> float:
    gt_lines = [l.strip() for l in ground_truth.split("\n") if l.strip()]
    ocr_lines = [l.strip() for l in ocr_text.split("\n") if l.strip()]
    if not gt_lines:
        return 1.0
    matched = 0
    ocr_idx = 0
    for gt_line in gt_lines:
        for i in range(ocr_idx, len(ocr_lines)):
            if difflib.SequenceMatcher(None, gt_line, ocr_lines[i]).ratio() > 0.6:
                matched += 1
                ocr_idx = i + 1
                break
    return matched / len(gt_lines)


def main():
    pdf_path = Path("/Users/eyjs/Desktop/WorkSpace/ai-platform/tmp/insurance_docs/약관_31123(02)_20260101.pdf")
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        return 1

    engine = AppleVisionOCREngine()
    if not engine.is_available():
        print("Apple Vision OCR not available!")
        return 1
    print(f"Engine: AppleVisionOCREngine (available={engine.is_available()})")

    test_pages = [31, 45, 47, 53, 57]
    results = []

    for pg_idx in test_pages:
        print(f"\n{'='*60}")
        print(f"Page {pg_idx + 1}")
        print(f"{'='*60}")

        gt_raw = get_ground_truth(pdf_path, pg_idx)
        if len(gt_raw) < 20:
            print(f"  Skipping — too little ground truth ({len(gt_raw)} chars)")
            continue

        img = render_page(pdf_path, pg_idx, dpi=200)
        raw_img = pil_to_raw_image(img)
        blocks = engine.recognize(raw_img)

        ocr_text = normalize("\n".join(b.text for b in blocks))
        gt_norm = normalize(gt_raw)

        char_acc = char_accuracy(ocr_text, gt_norm)
        word_acc = word_accuracy(ocr_text, gt_norm)
        line_ord = line_order_score(ocr_text, gt_raw)

        composite = char_acc * 0.5 + word_acc * 0.3 + line_ord * 0.2
        results.append({
            "page": pg_idx + 1,
            "char_acc": char_acc,
            "word_acc": word_acc,
            "line_order": line_ord,
            "composite": composite,
            "gt_chars": len(gt_norm),
            "ocr_chars": len(ocr_text),
            "blocks": len(blocks),
        })

        print(f"  Ground truth: {len(gt_norm)} chars")
        print(f"  OCR output:   {len(ocr_text)} chars, {len(blocks)} blocks")
        print(f"  Char accuracy:  {char_acc:.1%}")
        print(f"  Word accuracy:  {word_acc:.1%}")
        print(f"  Line order:     {line_ord:.1%}")
        print(f"  Composite:      {composite:.1%}")

        if char_acc < 0.9:
            print(f"\n  GT sample:  {gt_norm[:200]}")
            print(f"  OCR sample: {ocr_text[:200]}")

    if results:
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        avg_char = sum(r["char_acc"] for r in results) / len(results)
        avg_word = sum(r["word_acc"] for r in results) / len(results)
        avg_line = sum(r["line_order"] for r in results) / len(results)
        avg_composite = sum(r["composite"] for r in results) / len(results)
        print(f"  Pages tested:     {len(results)}")
        print(f"  Avg char acc:     {avg_char:.1%}")
        print(f"  Avg word acc:     {avg_word:.1%}")
        print(f"  Avg line order:   {avg_line:.1%}")
        print(f"  Avg composite:    {avg_composite:.1%}")
        print(f"  Score (0-100):    {avg_composite * 100:.0f}")

        score = avg_composite * 100
        if score >= 90:
            print(f"\n  PASS: Score {score:.0f} >= 90 — Phase 1-3 priority lowered")
        elif score >= 80:
            print(f"\n  MARGINAL: Score {score:.0f} — Phase 1-3 normal priority")
        else:
            print(f"\n  NEEDS WORK: Score {score:.0f} < 80 — Phase 1-3 accelerated")

    return 0


if __name__ == "__main__":
    sys.exit(main())
