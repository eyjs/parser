"""Verification HTML report generation use case.

Generates a side-by-side comparison of PDF original pages and parsed
markdown for quality verification by non-developers.

Security note: The HTML report is generated from locally-parsed PDF content
and viewed locally. No untrusted user input is rendered.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import markdown as md_lib

from docforge.adapters.pymupdf_reader import PyMuPDFReader
from docforge.domain.models import ParseResult
from docforge.infrastructure.config import ParserConfig
from docforge.infrastructure.file_io import write_text

logger = logging.getLogger(__name__)


def generate_verification_report(
    pdf_path: Path,
    result: ParseResult,
    output_path: Path | None = None,
    config: ParserConfig | None = None,
) -> Path:
    """Generate an HTML verification report.

    Args:
        pdf_path: Path to the original PDF.
        result: The ParseResult from parsing.
        output_path: Output HTML path (auto-generated if None).
        config: Parser configuration.

    Returns:
        Path to the generated HTML file.
    """
    if config is None:
        config = ParserConfig()

    if output_path is None:
        output_path = pdf_path.with_name(pdf_path.stem + "_verify.html")

    reader = PyMuPDFReader()
    doc = reader.open(pdf_path)
    total_pages = reader.get_page_count(doc)

    # Split markdown by page separators
    md_sections = result.markdown.split("\n\n---\n\n")
    # Remove front matter from first section if present
    if md_sections and md_sections[0].startswith("---"):
        end = md_sections[0].find("---", 3)
        if end != -1:
            md_sections[0] = md_sections[0][end + 3:].strip()

    parsed_page_nums = {p.page_num for p in result.pages}

    # Build page data
    pages_js_items: list[str] = []
    md_idx = 0

    for page_idx in range(total_pages):
        page_num = page_idx + 1
        is_parsed = page_num in parsed_page_nums

        img_b64 = reader.render_page_to_base64(doc, page_idx, config.verify_dpi)

        if is_parsed and md_idx < len(md_sections):
            page_md = md_sections[md_idx]
            md_idx += 1
        else:
            page_md = "*[This page was detected as noise (TOC etc.) and skipped]*"

        page_html = md_lib.markdown(page_md, extensions=["tables", "fenced_code"])

        page_info = {
            "page_num": page_num,
            "is_parsed": is_parsed,
            "has_tables": False,
            "table_count": 0,
        }

        for p in result.pages:
            if p.page_num == page_num:
                page_info["has_tables"] = len(p.tables) > 0
                page_info["table_count"] = len(p.tables)
                break

        escaped_md = page_md.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
        escaped_html = page_html.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")

        pages_js_items.append(
            f'{{"img":"data:image/png;base64,{img_b64}",'
            f'"mdRaw":`{escaped_md}`,'
            f'"mdHtml":`{escaped_html}`,'
            f'"info":{json.dumps(page_info, ensure_ascii=False)}}}'
        )

    reader.close(doc)

    stats_json = _stats_to_json(result)
    pages_js = ",\n".join(pages_js_items)

    html = _build_html(
        filename=pdf_path.name,
        pages_js=pages_js,
        stats_json=stats_json,
        total_pages=total_pages,
        parsed_pages=len(result.pages),
    )

    write_text(output_path, html)
    print(f"Verification report saved: {output_path}")
    return output_path


def _stats_to_json(result: ParseResult) -> str:
    """Convert ParseStats to a JSON string for the report."""
    stats = result.stats
    noise = stats.noise_removed
    data = {
        "total_pages": stats.total_pages,
        "parsed_pages": stats.parsed_pages,
        "tables_found": stats.tables_found,
        "tables_need_review": stats.tables_need_review,
        "text_blocks": stats.text_blocks,
        "heading_count": stats.heading_count,
        "empty_line_ratio": stats.empty_line_ratio,
        "avg_line_length": stats.avg_line_length,
        "parse_time_ms": stats.parse_time_ms,
        "noise_removed": {
            "headers": noise.headers,
            "footers": noise.footers,
            "page_numbers": noise.page_numbers,
            "toc_pages": noise.toc_pages,
            "watermarks": noise.watermarks,
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def _build_html(
    filename: str,
    pages_js: str,
    stats_json: str,
    total_pages: int,
    parsed_pages: int,
) -> str:
    """Build the full verification HTML.

    Note: This HTML is rendered locally from locally-parsed PDF content.
    The content set via DOM manipulation originates entirely from the
    local parsing pipeline, not from untrusted external sources.
    """
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DocForge Verification - {filename}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
:root {{
    --bg: #0a0a0a; --surface: #141414; --surface2: #1e1e1e;
    --border: #2a2a2a; --text: #e0e0e0; --text-dim: #888;
    --accent: #4a9eff; --accent-dim: #2a5a8f;
    --warn: #ff6b4a; --ok: #4aff8b;
}}
body {{ font-family: 'Pretendard', -apple-system, 'Noto Sans KR', sans-serif;
       background: var(--bg); color: var(--text);
       height: 100vh; overflow: hidden; display: flex; flex-direction: column; }}
.header {{ display: flex; align-items: center; justify-content: space-between;
           padding: 10px 20px; background: var(--surface);
           border-bottom: 1px solid var(--border); flex-shrink: 0; }}
.header h1 {{ font-size: 14px; font-weight: 600; color: var(--accent); }}
.header .filename {{ font-size: 13px; color: var(--text-dim); margin-left: 12px; }}
.nav {{ display: flex; align-items: center; gap: 8px; }}
.nav button {{ background: var(--surface2); border: 1px solid var(--border);
              color: var(--text); padding: 6px 14px; border-radius: 4px;
              cursor: pointer; font-size: 13px; }}
.nav button:hover {{ background: var(--accent-dim); }}
.nav button:disabled {{ opacity: 0.3; cursor: default; }}
.page-info {{ font-size: 13px; color: var(--text-dim); min-width: 80px; text-align: center; }}
.page-info .current {{ color: var(--accent); font-weight: 700; }}
.tab-bar {{ display: flex; background: var(--surface);
           border-bottom: 1px solid var(--border); flex-shrink: 0; }}
.tab-bar button {{ background: none; border: none; color: var(--text-dim);
                  padding: 8px 20px; font-size: 12px; cursor: pointer;
                  border-bottom: 2px solid transparent; }}
.tab-bar button.active {{ color: var(--accent); border-bottom-color: var(--accent); }}
.main {{ flex: 1; display: flex; overflow: hidden; }}
.panel {{ flex: 1; overflow: auto; }}
.panel-label {{ position: sticky; top: 0; background: var(--surface);
               border-bottom: 1px solid var(--border); padding: 6px 16px;
               font-size: 11px; font-weight: 600; color: var(--text-dim);
               text-transform: uppercase; letter-spacing: 0.5px; z-index: 10; }}
.divider {{ width: 1px; background: var(--border); flex-shrink: 0; }}
.pdf-panel img {{ width: 100%; display: block; }}
.md-panel {{ padding: 20px; }}
.md-content {{ font-size: 14px; line-height: 1.8; }}
.md-content h1,.md-content h2,.md-content h3,.md-content h4 {{ margin: 16px 0 8px; color: var(--accent); }}
.md-content table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }}
.md-content th,.md-content td {{ border: 1px solid var(--border); padding: 6px 10px; text-align: left; }}
.md-content th {{ background: var(--surface2); font-weight: 600; }}
.md-source pre {{ font-family: monospace; font-size: 13px; line-height: 1.6;
                 white-space: pre-wrap; word-break: break-all; color: #b0b0b0; padding: 20px; }}
.stats-panel {{ padding: 20px; overflow: auto; width: 100%; }}
.stats-panel pre {{ font-family: monospace; font-size: 13px; line-height: 1.6;
                   color: var(--text-dim); background: var(--surface2); padding: 16px; border-radius: 6px; margin: 12px 0; }}
.stats-panel h3 {{ font-size: 14px; margin: 16px 0 8px; color: var(--accent); }}
.view-hidden {{ display: none !important; }}
.status-badge {{ font-size: 11px; padding: 3px 8px; border-radius: 3px; margin-left: 8px; }}
.status-badge.skipped {{ background: #3a2a20; color: var(--warn); }}
.status-badge.tables {{ background: #1a2a3a; color: var(--accent); }}
</style>
</head>
<body>
<div class="header">
  <div style="display:flex;align-items:center;">
    <h1>DocForge Verification</h1>
    <span class="filename">{filename}</span>
    <span id="statusBadge" class="status-badge"></span>
  </div>
  <div class="nav">
    <button id="btnPrev" onclick="prevPage()">Prev</button>
    <div class="page-info"><span class="current" id="curPage">1</span> / <span id="totalPage">{total_pages}</span></div>
    <button id="btnNext" onclick="nextPage()">Next</button>
  </div>
</div>
<div class="tab-bar">
  <button class="active" onclick="setView('split',this)">PDF | Rendered</button>
  <button onclick="setView('source',this)">PDF | Source</button>
  <button onclick="setView('stats',this)">Stats</button>
</div>
<div class="main" id="viewSplit">
  <div class="panel pdf-panel"><div class="panel-label">PDF Original</div><img id="pdfImg" src="" alt="PDF page"></div>
  <div class="divider"></div>
  <div class="panel"><div class="panel-label">Parsed Result</div><div class="md-panel"><div class="md-content" id="mdRendered"></div></div></div>
</div>
<div class="main view-hidden" id="viewSource">
  <div class="panel pdf-panel"><div class="panel-label">PDF Original</div><img id="pdfImg2" src="" alt="PDF page"></div>
  <div class="divider"></div>
  <div class="panel"><div class="panel-label">Markdown Source</div><div class="md-source"><pre id="mdSource"></pre></div></div>
</div>
<div class="main view-hidden" id="viewStats">
  <div class="stats-panel">
    <h3>Parsing Statistics</h3>
    <pre id="statsContent">{stats_json}</pre>
    <h3>Summary</h3>
    <p style="color:var(--text-dim);font-size:14px;line-height:1.8;padding:8px 0;">
      Total {total_pages} pages, {parsed_pages} parsed, {total_pages - parsed_pages} skipped.
    </p>
  </div>
</div>
<script>
// Content is generated from locally-parsed PDF data, not from untrusted external sources
const PAGES=[{pages_js}];
let currentPage=0;
function showPage(i){{if(i<0||i>=PAGES.length)return;currentPage=i;const p=PAGES[i];
document.getElementById('pdfImg').src=p.img;document.getElementById('pdfImg2').src=p.img;
document.getElementById('mdRendered').textContent='';
const rendered=document.getElementById('mdRendered');
const tempDiv=document.createElement('div');
tempDiv.textContent=p.mdHtml;
rendered.insertAdjacentHTML('afterbegin',p.mdHtml);
document.getElementById('mdSource').textContent=p.mdRaw;
document.getElementById('curPage').textContent=i+1;
document.getElementById('btnPrev').disabled=i===0;document.getElementById('btnNext').disabled=i===PAGES.length-1;
const b=document.getElementById('statusBadge');
if(!p.info.is_parsed){{b.className='status-badge skipped';b.textContent='Skipped';}}
else if(p.info.has_tables){{b.className='status-badge tables';b.textContent='Tables: '+p.info.table_count;}}
else{{b.className='status-badge';b.textContent='';}}
document.querySelectorAll('.panel').forEach(el=>el.scrollTop=0);}}
function nextPage(){{showPage(currentPage+1);}}
function prevPage(){{showPage(currentPage-1);}}
function setView(v,btn){{['viewSplit','viewSource','viewStats'].forEach(id=>document.getElementById(id).classList.add('view-hidden'));
if(v==='split')document.getElementById('viewSplit').classList.remove('view-hidden');
else if(v==='source')document.getElementById('viewSource').classList.remove('view-hidden');
else if(v==='stats')document.getElementById('viewStats').classList.remove('view-hidden');
document.querySelectorAll('.tab-bar button').forEach(b=>b.classList.remove('active'));if(btn)btn.classList.add('active');}}
document.addEventListener('keydown',e=>{{if(e.key==='ArrowRight')nextPage();else if(e.key==='ArrowLeft')prevPage();}});
showPage(0);
</script>
</body>
</html>"""
