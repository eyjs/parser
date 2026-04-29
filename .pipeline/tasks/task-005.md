# Task-005: parse_pdf on_page_done callback
## Scope
- Add on_page_done parameter to parse_pdf
- Call on_page_done(page_num, page_markdown) after each page's markdown is assembled
- Must not break existing on_progress callback
## Dependencies: None
## Files: docforge/usecases/parse_pdf.py
