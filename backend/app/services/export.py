"""Server-side document export service (`.docx` / `.pptx` / `.pdf`) (`REQ-SEC-05` / `VULN-05`).

Dispatches `GeneratedContent` to:
- `docx_writer` (`build_docx`, `build_answers_docx`)
- `pptx_writer` (`build_pptx`)
- `render.mjs` + sandboxed Playwright Chromium (`_render_pdf`)

Unidirectional Dependency Law:
- Imports ONLY from `app.core.hardening`, `app.core.processes`, `app.schemas.content`,
  `app.services.docx_writer`, `app.services.pptx_writer`, `playwright`, and Python stdlib.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from playwright.sync_api import Route, sync_playwright

from app.core.hardening import safe_xml_fromstring
from app.core.processes import renderer_env
from app.schemas.content import GeneratedContent
from app.services.docx_writer import build_answers_docx, build_docx
from app.services.pptx_writer import build_pptx

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RENDER_SCRIPT = _REPO_ROOT / "frontend" / "renderer" / "render.mjs"

_DOCX_MT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

MEDIA_TYPES: dict[str, str] = {
    "docx": _DOCX_MT,
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "answers-docx": _DOCX_MT,
    "answers-pdf": "application/pdf",
}

_CHROMIUM_ARGS: list[str] = ["--disable-dev-shm-usage", "--disable-gpu"]


def _launch_browser(p: Any) -> Any:
    browser = p.chromium.launch(
        channel="chrome",
        chromium_sandbox=True,
        args=_CHROMIUM_ARGS,
        env=renderer_env(),
        timeout=30000,
    )
    logger.info("PDF renderer: sandboxed Chrome %s", browser.version)
    return browser


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:120] or "resource"


def _run_render(content: GeneratedContent, *args: str) -> bytes:
    payload = json.dumps(content.model_dump(mode="json")).encode()
    proc = subprocess.run(
        ["node", str(_RENDER_SCRIPT), *args],
        input=payload,
        capture_output=True,
        timeout=30,
        env=renderer_env(),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"render.mjs failed: {proc.stderr.decode()[:400]}")
    return proc.stdout


def _render_pdf(content: GeneratedContent, answers: bool = False) -> bytes:
    html = _run_render(content, *(["--answers"] if answers else []))
    with tempfile.TemporaryDirectory(prefix="pdf_") as tmp:
        html_path = (Path(tmp) / "doc.html").resolve()
        html_path.write_bytes(html)
        pdf_path = Path(tmp) / "doc.pdf"
        with sync_playwright() as p:
            browser = _launch_browser(p)
            page = browser.new_page(java_script_enabled=False, service_workers="block")
            katex = (_REPO_ROOT / "frontend/renderer/node_modules/katex/dist").resolve()

            def allowed(route: Route) -> None:
                url = urlsplit(route.request.url)
                path = Path(unquote(url.path)).resolve()
                if (
                    url.scheme == "file"
                    and not url.netloc
                    and (path == html_path or path.is_relative_to(katex))
                ):
                    route.continue_()
                else:
                    route.abort()

            page.route("**/*", allowed)
            page.goto(html_path.as_uri(), wait_until="networkidle")
            page.pdf(path=str(pdf_path), format="A4", print_background=True)
            browser.close()
        return pdf_path.read_bytes()


def verify_docx_xml_invariants(docx_bytes: bytes) -> None:
    """Validate `.docx` XML math-run sizing and table grid widths using `safe_xml_fromstring` (`REQ-SEC-05`)."""
    with zipfile.ZipFile(BytesIO(docx_bytes)) as zf:
        raw_doc_xml = zf.read("word/document.xml")

    root = safe_xml_fromstring(raw_doc_xml)
    m_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
    w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    math_runs = root.findall(f".//{m_ns}oMath//{m_ns}r")
    unsized = [r for r in math_runs if r.find(f"{w_ns}rPr/{w_ns}sz") is None]
    if unsized:
        raise ValueError(
            f"{len(unsized)}/{len(math_runs)} docx math runs carry no explicit w:sz."
        )

    for tbl in root.findall(f".//{w_ns}tbl"):
        tbl_grid = tbl.find(f"{w_ns}tblGrid")
        if tbl_grid is None:
            continue
        grid_cols = [
            int(g.get(f"{w_ns}w") or "0") for g in tbl_grid.findall(f"{w_ns}gridCol")
        ]
        if not grid_cols:
            continue
        for tr in tbl.findall(f"{w_ns}tr"):
            col_idx = 0
            for tc in tr.findall(f"{w_ns}tc"):
                gs = tc.find(f"{w_ns}tcPr/{w_ns}gridSpan")
                span = int(gs.get(f"{w_ns}val") or "1") if gs is not None else 1
                tc_w = tc.find(f"{w_ns}tcPr/{w_ns}tcW")
                if tc_w is not None and tc_w.get(f"{w_ns}type") == "dxa":
                    declared_w = int(tc_w.get(f"{w_ns}w") or "0")
                    expected_w = sum(grid_cols[col_idx : col_idx + span])
                    if abs(declared_w - expected_w) > 2:
                        raise ValueError(
                            f"Cell spanning {span} col(s) declares {declared_w} twips "
                            f"but grid columns total {expected_w}."
                        )
                col_idx += span


def export_content(content: GeneratedContent, fmt: str) -> tuple[bytes, str, str]:
    """Render `content` to `fmt` and return `(bytes, filename, media_type)`."""
    if fmt not in MEDIA_TYPES:
        raise ValueError(f"Unsupported export format: {fmt!r}")

    if fmt == "answers-docx":
        data = build_answers_docx(content)
    elif fmt == "answers-pdf":
        data = _render_pdf(content, answers=True)
    elif fmt == "pdf":
        data = _render_pdf(content)
    elif fmt == "pptx":
        data = build_pptx(content)
    else:
        data = build_docx(content)

    suffix = "-answers" if fmt.startswith("answers-") else ""
    ext = fmt.split("-")[-1]
    return data, f"{_slug(content.title)}{suffix}.{ext}", MEDIA_TYPES[fmt]
