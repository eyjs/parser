"""Qwen2-VL VLM host service.

Run this on the macOS host to expose Qwen2-VL as an HTTP endpoint.
Docker containers call this via host.docker.internal:5053.

Usage:
    python vlm_service.py                  # default port 5053
    python vlm_service.py --port 5054      # custom port
"""

from __future__ import annotations

import argparse
import logging
import sys

from flask import Flask, request, jsonify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from docforge.adapters.vision_llm_engine import Qwen2VLMLXEngine
        _engine = Qwen2VLMLXEngine()
        if not _engine.is_available():
            logger.error("Qwen2-VL MLX is not available on this system")
            sys.exit(1)
        logger.info("Qwen2-VL MLX engine initialized")
    return _engine


@app.route("/health", methods=["GET"])
def health():
    engine = _get_engine()
    return jsonify({"status": "ok", "engine": "qwen2_vl_mlx", "available": engine.is_available()})


@app.route("/describe", methods=["POST"])
def describe():
    if "image" not in request.files:
        return jsonify({"error": "no image file provided"}), 400

    image_file = request.files["image"]
    image_data = image_file.read()
    fmt = request.form.get("format", "png")
    prompt_hint = request.form.get("prompt_hint", "")
    block_type = request.form.get("block_type", "")
    context_text = request.form.get("context_text", "")
    bbox_info = request.form.get("bbox_info", "")

    engine = _get_engine()
    alt_text = engine.describe_image(
        image_data=image_data,
        format=fmt,
        prompt_hint=prompt_hint,
        block_type=block_type,
        context_text=context_text,
        bbox_info=bbox_info,
    )

    logger.info("describe_image: %d bytes, result=%d chars", len(image_data), len(alt_text))
    return jsonify({"alt_text": alt_text})


@app.route("/correct", methods=["POST"])
def correct():
    if "image" not in request.files:
        return jsonify({"error": "no image file provided"}), 400

    import io
    import json
    import numpy as np
    from PIL import Image
    from docforge.domain.enums import BlockType
    from docforge.domain.models import TextBlock
    from docforge.domain.value_objects import BBox, FontInfo, RawImage

    image_file = request.files["image"]
    image_bytes = image_file.read()
    prompt_hint = request.form.get("prompt_hint", "")
    ocr_blocks_json = request.form.get("ocr_blocks", "[]")

    pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(pil_img)
    raw_image = RawImage(data=arr, width=pil_img.width, height=pil_img.height, channels=3)

    ocr_blocks = []
    for b in json.loads(ocr_blocks_json):
        ocr_blocks.append(TextBlock(
            text=b["text"],
            bbox=BBox(x0=b["bbox"]["x0"], y0=b["bbox"]["y0"], x1=b["bbox"]["x1"], y1=b["bbox"]["y1"]),
            font=FontInfo(name="remote", size=0.0, is_bold=False),
            block_type=BlockType.TEXT,
            confidence=b.get("confidence", 1.0),
        ))

    engine = _get_engine()
    result_blocks = engine.correct_page(image=raw_image, ocr_blocks=ocr_blocks, prompt_hint=prompt_hint)

    blocks_out = []
    for block in result_blocks:
        blocks_out.append({
            "text": block.text,
            "bbox": {"x0": block.bbox.x0, "y0": block.bbox.y0, "x1": block.bbox.x1, "y1": block.bbox.y1},
            "confidence": block.confidence,
        })

    logger.info("correct_page: %d input blocks -> %d output blocks", len(ocr_blocks), len(blocks_out))
    return jsonify({"blocks": blocks_out})


def main():
    parser = argparse.ArgumentParser(description="Qwen2-VL VLM Service")
    parser.add_argument("--port", type=int, default=5053)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    _get_engine()
    logger.info("Starting VLM service on %s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
