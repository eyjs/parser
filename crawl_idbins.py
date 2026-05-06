"""Crawl insurance product PDFs from DB Insurance (idbins.com)."""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


BASE = "https://www.idbins.com"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Referer": f"{BASE}/FWMAIV1534.do",
    "X-Requested-With": "XMLHttpRequest",
}

CATEGORIES = [
    ("장기보험", "Off-Line", "건강"),
    ("장기보험", "Off-Line", "상해"),
    ("장기보험", "Off-Line", "질병"),
    ("장기보험", "Off-Line", "재물"),
    ("일반보험", "Off-Line", "배상책임"),
]

OUT_DIR = Path("test_pdfs")


def _post_json(path: str, data: dict) -> dict:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=body, headers=HEADERS, method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _download_pdf(filename: str, out_path: Path) -> bool:
    encoded = urllib.parse.quote(filename, safe="")
    url = f"{BASE}/cYakgwanDown.do?FilePath=InsProduct/{encoded}"
    req = urllib.request.Request(url)
    req.add_header("Referer", f"{BASE}/FWMAIV1534.do")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            if len(content) < 1000 or not content[:5].startswith(b"%PDF"):
                print(f"  SKIP (not a PDF): {filename} ({len(content)} bytes)")
                return False
            out_path.write_bytes(content)
            size_mb = len(content) / (1024 * 1024)
            print(f"  OK: {out_path.name} ({size_mb:.1f} MB)")
            return True
    except Exception as e:
        print(f"  FAIL: {filename} — {e}")
        return False


def crawl(max_products: int = 5, max_pdfs: int = 8) -> list[Path]:
    OUT_DIR.mkdir(exist_ok=True)
    downloaded: list[Path] = []

    for lgcg, chn, mdcg in CATEGORIES:
        if len(downloaded) >= max_pdfs:
            break
        print(f"\n=== {lgcg} > {chn} > {mdcg} ===")

        products = _post_json(
            "/insuPcPbanFindProductStep2_AX.do",
            {
                "arc_knd_lgcg_nm": lgcg,
                "sl_chn_nm": chn,
                "arc_knd_mdcg_nm": mdcg,
                "arc_pdc_sl_yn": "1",
            },
        )
        names = [p["PDC_NM"] for p in products.get("result", [])]
        print(f"  상품 {len(names)}건")

        for name in names[:max_products]:
            if len(downloaded) >= max_pdfs:
                break
            print(f"\n  [{name}]")

            versions = _post_json(
                "/insuPcPbanFindProductStep3_AX.do",
                {"pdc_nm": name, "arc_pdc_sl_yn": "1"},
            )
            for ver in versions.get("result", [])[:1]:
                sqno = ver.get("SQNO")
                if not sqno:
                    continue

                details = _post_json(
                    "/insuPcPbanFindProductStep4_AX.do",
                    {"sqno": sqno},
                )
                for item in details.get("result", []):
                    for key, label in [
                        ("INPL_FINM", "약관"),
                        ("BIZ_MDDC_FINM", "사업방법서"),
                        ("CNSL_SMAR_FINM", "상품요약서"),
                    ]:
                        fname = item.get(key)
                        if not fname:
                            continue
                        safe_name = fname.replace("/", "_")
                        out_path = OUT_DIR / safe_name
                        if out_path.exists():
                            print(f"  CACHED: {safe_name}")
                            downloaded.append(out_path)
                            continue
                        if _download_pdf(fname, out_path):
                            downloaded.append(out_path)
                        time.sleep(0.5)

    print(f"\n총 {len(downloaded)}개 PDF 다운로드 완료")
    return downloaded


if __name__ == "__main__":
    crawl()
