"""LaTeX -> OMML for native, editable PowerPoint equations via Pandoc (`REQ-SEC-05`).

Unidirectional Dependency Law:
- Imports ONLY from `app.core.hardening`, `app.core.processes`, `lxml`, `pypandoc`, and stdlib.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import pypandoc
from lxml import etree

from app.core.hardening import safe_xml_fromstring
from app.core.processes import renderer_env

logger = logging.getLogger(__name__)

# OOXML namespaces.
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
A14 = "http://schemas.microsoft.com/office/drawing/2010/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"

CAMBRIA_MATH = "Cambria Math"

_OMATH_RE = re.compile(r"<m:oMath>.*?</m:oMath>", re.DOTALL)
_OVER_ESCAPED_CMD = re.compile(r"\\\\(?=[A-Za-z])")


def _norm(latex: str) -> str:
    return _OVER_ESCAPED_CMD.sub(r"\\", latex.strip())


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pandoc_omaths(md: str) -> list[str]:
    """Run one markdown doc through pandoc in `--sandbox` mode and extract `<m:oMath>` blocks."""
    with tempfile.TemporaryDirectory(prefix="omml_") as d:
        out = Path(d) / "m.docx"
        subprocess.run(
            [
                pypandoc.get_pandoc_path(),
                "--sandbox",
                "-f",
                "markdown",
                "-t",
                "docx",
                "-o",
                str(out),
            ],
            input=md.encode(),
            capture_output=True,
            check=True,
            timeout=10,
            env=renderer_env(),
        )
        with zipfile.ZipFile(out) as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")
    return _OMATH_RE.findall(doc_xml)


def _text_omath(latex: str) -> str:
    """Last-resort fallback: render the raw expression as a plain math run."""
    return f"<m:oMath><m:r><m:t>{_escape(latex)}</m:t></m:r></m:oMath>"


def latex_to_omath_xml(latex_list: list[str]) -> list[str]:
    """Batch-convert LaTeX snippets -> inline `<m:oMath>` XML strings in one pandoc call."""
    if not latex_list:
        return []
    if len(latex_list) > 1200 or any(len(s) > 4000 for s in latex_list):
        raise ValueError("Too many or oversized equations")
    started = time.monotonic()
    md = "\n\n".join("$" + _norm(s) + "$" for s in latex_list)
    omaths = _pandoc_omaths(md)
    if len(omaths) == len(latex_list):
        return omaths

    logger.warning(
        "pandoc batch produced %d oMath for %d inputs — converting per-equation",
        len(omaths),
        len(latex_list),
    )
    result: list[str] = []
    for s in latex_list:
        if time.monotonic() - started > 15:
            result.extend(_text_omath(value) for value in latex_list[len(result) :])
            break
        single = _pandoc_omaths("$" + _norm(s) + "$")
        if len(single) == 1:
            result.append(single[0])
        else:
            logger.warning("equation not convertible to OMML, showing as text: %r", s)
            result.append(_text_omath(s))
    return result


def _pin_font(root: Any, sz: int) -> None:
    """Pin sized Cambria Math on every math run so slide equations aren't tiny."""
    for r in root.iter(_q(M, "r")):
        a_rpr = etree.Element(_q(A, "rPr"))
        a_rpr.set("sz", str(sz))
        etree.SubElement(a_rpr, _q(A, "latin")).set("typeface", CAMBRIA_MATH)
        m_rpr = r.find(_q(M, "rPr"))
        r.insert(list(r).index(m_rpr) + 1 if m_rpr is not None else 0, a_rpr)


def _wrap(
    omath_xml: str,
    latex: str,
    size_pt: float,
    fallback: str | None,
) -> Any:
    """Wrap one pandoc `<m:oMath>` string in the PowerPoint MCE structure using `safe_xml_fromstring`."""
    fb = _escape(latex if fallback is None else fallback)
    xml = (
        f'<mc:AlternateContent xmlns:mc="{MC}" xmlns:a14="{A14}" xmlns:m="{M}" xmlns:a="{A}">'
        f'<mc:Choice Requires="a14"><a14:m>{omath_xml}</a14:m></mc:Choice>'
        f"<mc:Fallback><a:r><a:t>{fb}</a:t></a:r></mc:Fallback>"
        f"</mc:AlternateContent>"
    )
    root = safe_xml_fromstring(xml.encode("utf-8"))
    _pin_font(root, round(size_pt * 100))
    return root


def alt_content_from_omath(
    omath_xml: str,
    latex: str,
    size_pt: float = 28.0,
    fallback: str | None = None,
) -> Any:
    """Build one `<mc:AlternateContent>` element from an already-converted `<m:oMath>` string."""
    return _wrap(omath_xml, latex, size_pt, fallback)
