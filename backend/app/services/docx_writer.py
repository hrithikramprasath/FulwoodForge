"""Native Fulwood-format Word writer (.docx) (`REQ-SEC-05` / `VULN-05`).

Builds student and answer-key `.docx` packages using `python-docx` and `safe_xml_fromstring`.
Unidirectional Dependency Law:
- Imports ONLY from `app.core.hardening`, `app.schemas.content`, `app.services.omml`,
  `docx`, and Python standard library.
"""

from __future__ import annotations

import logging
import re
import zipfile
from collections.abc import Sequence
from io import BytesIO
from itertools import zip_longest
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import nsdecls, qn
from docx.shared import Mm, Pt, RGBColor

from app.core.hardening import safe_xml_fromstring
from app.schemas.content import (
    CoachPoint as ExamLanguageItem,
)
from app.schemas.content import (
    DinTask as DINBlock,
)
from app.schemas.content import (
    ExamQuestion,
    GeneratedContent,
    Keyword,
    Lesson,
    Misconception,
    Overview,
    RevisionResource,
    Spelling,
)
from app.schemas.content import (
    Feedback as FeedbackBlock,
)
from app.schemas.content import (
    ModelStep as IDoStep,
)
from app.schemas.content import (
    Task as WeDoTask,
)
from app.schemas.content import (
    Task as YouDoTask,
)
from app.services.omml import M as _M_NS
from app.services.omml import latex_to_omath_xml

logger = logging.getLogger(__name__)

_FONT = "Segoe UI"

# Shared document palette: Oxford Navy for the teaching phases, Academic Slate for ink,
# and semantic red / green / amber. pptx_writer imports these so all three export
# formats (pdf, docx, pptx) share one visual identity.
NAVY = "0F4C81"
NAVY_DARK = "0B3861"
NAVY_SOFT = "E0F2FE"
NAVY_LINE = "BAE6FD"
SLATE = "0F172A"
SLATE_MUTED = "64748B"
SLATE_SOFT = "F1F5F9"
SLATE_LINE = "CBD5E1"
_GREEN, _GREEN_BG, _GREEN_LINE = "15803D", "DCFCE7", "BBF7D0"
_RED, _RED_BG, _RED_LINE = "B91C1C", "FEE2E2", "FECACA"
_AMBER, _AMBER_BG, _AMBER_LINE = "B45309", "FEF3C7", "FDE68A"

_TEACHING_PHASES = ("din", "spell", "kw", "ido", "wedo", "examq")
_TXT: dict[str, str] = {
    **dict.fromkeys(_TEACHING_PHASES, NAVY),
    "mis": _RED,
    "youdo": _GREEN,
    "coach": _AMBER,
    "exit": "334155",
    "fb": "334155",
    "rev": _AMBER,
    "ans": _GREEN,
    "ov": NAVY,
}
_BG: dict[str, str] = {
    **dict.fromkeys(_TEACHING_PHASES, NAVY_SOFT),
    "mis": _RED_BG,
    "youdo": _GREEN_BG,
    "coach": _AMBER_BG,
    "exit": SLATE_SOFT,
    "fb": SLATE_SOFT,
    "rev": _AMBER_BG,
    "ans": _GREEN_BG,
    "ov": SLATE_SOFT,
}
_BORDER: dict[str, str] = {
    **dict.fromkeys(_TEACHING_PHASES, NAVY_LINE),
    "mis": _RED_LINE,
    "youdo": _GREEN_LINE,
    "coach": _AMBER_LINE,
    "exit": SLATE_LINE,
    "fb": SLATE_LINE,
    "rev": _AMBER_LINE,
    "ans": _GREEN_LINE,
    "ov": SLATE_LINE,
}

_INK = RGBColor.from_string(SLATE)
_MUTED = RGBColor.from_string(SLATE_MUTED)
_RULE = "E2E8F0"

_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.DOTALL)
_STRIP_MD = re.compile(r"^\s*(#{1,6}\s+|[-*+]\s+|>\s+)")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def scrub_control_chars(content: GeneratedContent) -> GeneratedContent:
    """Return `content` with XML-illegal control characters removed from every string."""

    def walk(o: Any) -> Any:
        if isinstance(o, str):
            return _CTRL.sub("", o)
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        return o

    return GeneratedContent.model_validate(walk(content.model_dump()))


def option_letter(i: int) -> str:
    """Label for MCQ option `i`: A, B, C ... Z, then plain numbers."""
    return chr(65 + i) if i < 26 else str(i + 1)


_EMOJI = re.compile("[\U0001f300-\U0001faff\u2705\u274c\u26a0\u2717\u2713\ufe0f]")


def _rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


def _collect_latex(obj: Any, out: list[str]) -> None:
    if isinstance(obj, str):
        for m in _MATH_RE.finditer(obj):
            out.append((m.group(1) or m.group(2)).strip())
    elif isinstance(obj, list):
        for x in obj:
            _collect_latex(x, out)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_latex(v, out)


def _shade(cell: Any, hex_fill: str) -> None:
    cell._tc.get_or_add_tcPr().append(
        safe_xml_fromstring(
            f'<w:shd {nsdecls("w")} w:val="clear" w:fill="{hex_fill}"/>'
        )
    )


def _cell_borders(
    cell: Any,
    hex_col: str,
    sz: int = 6,
    edges: tuple[str, ...] = ("top", "left", "bottom", "right"),
) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tag = "".join(
        f'<w:{e} w:val="single" w:sz="{sz}" w:space="0" w:color="{hex_col}"/>'
        for e in edges
    )
    tc_pr.append(
        safe_xml_fromstring(f"<w:tcBorders {nsdecls('w')}>{tag}</w:tcBorders>")
    )


def _no_borders(table: Any) -> None:
    tbl_pr = table._tbl.tblPr
    tbl_pr.append(
        safe_xml_fromstring(
            f"<w:tblBorders {nsdecls('w')}>"
            + "".join(
                f'<w:{e} w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
                for e in ("top", "left", "bottom", "right", "insideH", "insideV")
            )
            + "</w:tblBorders>"
        )
    )


def _keep_together(paragraph: Any) -> None:
    paragraph.paragraph_format.keep_with_next = True


_CRUFT_PARTS = ("docProps/thumbnail.jpeg", "word/stylesWithEffects.xml")
_CRUFT_PREFIX = "customXml/"
_REL_RE = re.compile(r"<Relationship\b[^>]*?/>")
_OVERRIDE_RE = re.compile(r"<Override\b[^>]*?/>")


def _strip_template_cruft(data: bytes, title: str) -> bytes:
    """Rebuild the .docx package without python-docx's template leftovers."""
    try:
        src = zipfile.ZipFile(BytesIO(data))
        drop = {
            n
            for n in src.namelist()
            if n in _CRUFT_PARTS or n.startswith(_CRUFT_PREFIX)
        }
        if not drop:
            return data
        stems = tuple(n.rsplit("/", 1)[-1] for n in drop) + ("customXml",)

        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                if item.filename in drop:
                    continue
                blob = src.read(item.filename)
                if item.filename.endswith(".rels"):
                    txt = blob.decode("utf-8")
                    txt = _REL_RE.sub(
                        lambda m: (
                            "" if any(s in m.group(0) for s in stems) else m.group(0)
                        ),
                        txt,
                    )
                    blob = txt.encode("utf-8")
                elif item.filename == "[Content_Types].xml":
                    txt = blob.decode("utf-8")
                    txt = _OVERRIDE_RE.sub(
                        lambda m: (
                            "" if any(s in m.group(0) for s in stems) else m.group(0)
                        ),
                        txt,
                    )
                    txt = txt.replace(
                        '<Default Extension="jpeg" ContentType="image/jpeg"/>', ""
                    )
                    blob = txt.encode("utf-8")
                elif item.filename == "docProps/app.xml":
                    txt = blob.decode("utf-8")
                    txt = re.sub(
                        r"<Application>.*?</Application>",
                        "<Application>Fulwood Forge</Application>",
                        txt,
                    )
                    txt = re.sub(
                        r"<Template>.*?</Template>", "<Template></Template>", txt
                    )
                    txt = re.sub(
                        r"<Company>.*?</Company>",
                        "<Company>Fulwood Forge</Company>",
                        txt,
                    )
                    blob = txt.encode("utf-8")
                dst.writestr(item, blob)
        return out.getvalue()
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Could not strip template parts from %r; shipping as-is",
            title,
            exc_info=True,
        )
        return data


_USABLE_MM: float = 210.0 - 13.0 - 13.0


def _fix_layout(table: Any, widths_mm: Sequence[float] | None = None) -> None:
    """Pin a table to a fixed layout with consistent widths."""
    table.autofit = False
    if not widths_mm:
        return
    resolved_widths = [float(w) for w in widths_mm]
    total = sum(resolved_widths)
    if total > _USABLE_MM:
        resolved_widths = [w * _USABLE_MM / total for w in resolved_widths]
    resolved_widths = resolved_widths[: len(table.columns)]
    for i, w in enumerate(resolved_widths):
        table.columns[i].width = Mm(w)

    for row in table.rows:
        col = 0
        for tc in row._tr.tc_lst:
            span = max(1, int(tc.grid_span))
            tc.width = Mm(sum(resolved_widths[col : col + span]) or resolved_widths[-1])
            col += span


class _Doc:
    """Builds one Fulwood-styled Word document."""

    def __init__(self, content: GeneratedContent) -> None:
        self.c = scrub_control_chars(content)
        self.doc: Any = Document()
        self._page_setup()
        latex: list[str] = []
        _collect_latex(self.c.model_dump(mode="json"), latex)
        uniq = list(dict.fromkeys(latex))
        self.math: dict[str, str] = (
            dict(zip(uniq, latex_to_omath_xml(uniq), strict=False)) if uniq else {}
        )

    def _page_setup(self) -> None:
        for s in self.doc.sections:
            s.page_width, s.page_height = Mm(210), Mm(297)
            s.top_margin = s.bottom_margin = Mm(14)
            s.left_margin = s.right_margin = Mm(13)
        style: Any = self.doc.styles["Normal"]
        style.font.name = _FONT
        style.font.size = Pt(10)
        style.font.color.rgb = _INK
        style.paragraph_format.space_after = Pt(3)
        rpr = style.element.get_or_add_rPr().get_or_add_rFonts()
        rpr.set(qn("w:eastAsia"), _FONT)

    def _omath(self, latex: str, size: float = 10) -> Any:
        """One inline equation, with its size pinned to the surrounding text."""
        xml = self.math.get(latex.strip()) or latex_to_omath_xml([latex])[0]
        el = safe_xml_fromstring(
            xml.replace("<m:oMath>", f"<m:oMath {nsdecls('m', 'w')}>", 1)
        )
        half = str(round(size * 2))
        for r in el.findall(f".//{{{_M_NS}}}r"):
            w_rpr = safe_xml_fromstring(
                f"<w:rPr {nsdecls('w')}>"
                f'<w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/>'
                f'<w:sz w:val="{half}"/><w:szCs w:val="{half}"/>'
                f"</w:rPr>"
            )
            m_rpr = r.find(f"{{{_M_NS}}}rPr")
            r.insert(list(r).index(m_rpr) + 1 if m_rpr is not None else 0, w_rpr)
        return el

    def _runs(
        self,
        p: Any,
        seg: str,
        size: float,
        color: RGBColor | None,
        bold: bool,
    ) -> None:
        for i, part in enumerate(seg.split("**")):
            if not part:
                continue
            r = p.add_run(part)
            r.font.size = Pt(size)
            r.font.bold = bold or (i % 2 == 1)
            r.font.name = _FONT
            r.font.color.rgb = color or _INK

    def _write(
        self,
        p: Any,
        text: str | None,
        size: float = 10,
        color: RGBColor | None = None,
        bold: bool = False,
    ) -> Any:
        """Render text with **bold** and $maths$ into an existing paragraph."""
        clean = _STRIP_MD.sub("", _CTRL.sub("", _EMOJI.sub("", text or "")).strip())
        clean = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", clean)
        pos = 0
        for m in _MATH_RE.finditer(clean):
            if m.start() > pos:
                self._runs(p, clean[pos : m.start()], size, color, bold)
            p._p.append(self._omath(m.group(1) or m.group(2), size))
            pos = m.end()
        if pos < len(clean):
            self._runs(p, clean[pos:], size, color, bold)
        return p

    def _cell_text(
        self,
        cell: Any,
        text: str | None,
        size: float = 10,
        color: RGBColor | None = None,
        bold: bool = False,
        first: bool = True,
        space: float = 2,
    ) -> Any:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        p.paragraph_format.space_after = Pt(space)
        self._write(p, text, size, color, bold)
        return p

    def _lines(self, cell: Any, n: int = 2, first: bool = False) -> None:
        """Ruled writing lines for handwritten answers."""
        for _ in range(n):
            p = (
                cell.paragraphs[0]
                if (first and not cell.paragraphs[0].text)
                else cell.add_paragraph()
            )
            p.paragraph_format.space_after = Pt(9)
            p.paragraph_format.space_before = Pt(6)
            p._p.get_or_add_pPr().append(
                safe_xml_fromstring(
                    f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="4" '
                    f'w:space="1" w:color="{_RULE}"/></w:pBdr>'
                )
            )
            first = False

    def _card(
        self,
        kind: str,
        label: str,
        meta: str | None = None,
    ) -> Any:
        """A section card: tinted header row + bordered body."""
        t = self.doc.add_table(rows=2, cols=1)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        _no_borders(t)
        t.autofit = False

        hc = t.rows[0].cells[0]
        _shade(hc, _BG[kind])
        _cell_borders(hc, _BORDER[kind])
        hp = hc.paragraphs[0]
        hp.paragraph_format.space_after = Pt(1)
        _keep_together(hp)
        self._runs(hp, label, 11.5, _rgb(_TXT[kind]), True)
        if meta:
            mp = hc.add_paragraph()
            mp.paragraph_format.space_after = Pt(1)
            self._write(mp, meta, 8.5, _MUTED, False)

        body = t.rows[1].cells
        for c in body:
            _cell_borders(c, _BORDER[kind])
        _fix_layout(t, [_USABLE_MM])
        self.doc.add_paragraph().paragraph_format.space_after = Pt(4)
        return body

    def _grid(
        self,
        kind: str,
        label: str,
        rows: int,
        cols: int,
        widths: Sequence[float] | None = None,
        meta: str | None = None,
    ) -> Any:
        """A card whose body is itself an n-column grid."""
        t = self.doc.add_table(rows=rows + 1, cols=cols)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        _no_borders(t)
        head = t.rows[0].cells
        head[0].merge(head[cols - 1])
        hc = t.rows[0].cells[0]
        _shade(hc, _BG[kind])
        _cell_borders(hc, _BORDER[kind])
        hp = hc.paragraphs[0]
        hp.paragraph_format.space_after = Pt(1)
        _keep_together(hp)
        self._runs(hp, label, 11.5, _rgb(_TXT[kind]), True)
        if meta:
            mp = hc.add_paragraph()
            mp.paragraph_format.space_after = Pt(1)
            self._write(mp, meta, 8.5, _MUTED, False)
        for r in t.rows[1:]:
            for c in r.cells:
                _cell_borders(c, _BORDER[kind])
        _fix_layout(t, widths or [_USABLE_MM / cols] * cols)
        return t

    def _doc_head(
        self,
        title: str,
        subtitle: str,
        pill: str | None = None,
    ) -> None:
        t = self.doc.add_table(rows=1, cols=1)
        _no_borders(t)
        _fix_layout(t, [_USABLE_MM])
        c = t.rows[0].cells[0]
        _shade(c, NAVY)
        _cell_borders(c, NAVY_DARK)
        p = c.paragraphs[0]
        p.paragraph_format.space_after = Pt(2)
        self._write(p, title, 19, RGBColor(0xFF, 0xFF, 0xFF), True)
        sp = c.add_paragraph()
        sp.paragraph_format.space_after = Pt(1)
        sr = sp.add_run(subtitle)
        sr.font.size, sr.font.name = Pt(9.5), _FONT
        sr.font.color.rgb = RGBColor(0xE0, 0xF2, 0xFE)
        if pill:
            pp = c.add_paragraph()
            pp.paragraph_format.space_after = Pt(1)
            pr = pp.add_run(pill)
            pr.font.size, pr.font.bold, pr.font.name = Pt(8.5), True, _FONT
            pr.font.color.rgb = RGBColor(0xBA, 0xE6, 0xFD)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(4)

    def _name_box(self) -> None:
        t = self.doc.add_table(rows=1, cols=3)
        _no_borders(t)
        _fix_layout(t, [_USABLE_MM / 3] * 3)
        for i, lbl in enumerate(
            (
                "Name:  ______________________",
                "Class:  ______________",
                "Date:  ______________",
            )
        ):
            c = t.rows[0].cells[i]
            _shade(c, "F8FAFC")
            _cell_borders(c, "E2E8F0")
            r = c.paragraphs[0].add_run(lbl)
            r.font.size, r.font.name, r.font.bold = Pt(9), _FONT, True
            r.font.color.rgb = _MUTED
        self.doc.add_paragraph().paragraph_format.space_after = Pt(4)

    def _overview(self, ov: Overview) -> None:
        body = self._card("ov", "AT A GLANCE")[0]
        self._cell_text(body, ov.summary, 10)
        if ov.objectives:
            self._cell_text(body, "**Learning objectives**", 9.5, _MUTED, first=False)
            for o in ov.objectives:
                self._cell_text(body, f"•  {o}", 9.5, first=False, space=1)
        self._cell_text(body, "**Difficulty & assessment**", 9.5, _MUTED, first=False)
        self._cell_text(body, ov.assessment_focus, 9.5, first=False)

    def _lesson_head(self, lesson: Lesson, show_objectives: bool) -> None:
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(1)
        _keep_together(p)
        self._write(p, lesson.title, 14, _INK, True)
        fp = self.doc.add_paragraph()
        fp.paragraph_format.space_after = Pt(3)
        self._write(fp, lesson.lesson_focus, 9.5, _MUTED)
        if show_objectives and lesson.objectives:
            for o in lesson.objectives:
                op = self.doc.add_paragraph()
                op.paragraph_format.space_after = Pt(1)
                self._write(op, f"•  {o}", 9.5)

    def _din(self, d: DINBlock) -> None:
        qs = d.questions
        t = self._grid(
            "din",
            "DO IT NOW",
            rows=len(qs) + 1,
            cols=2,
            widths=[118, 62],
            meta=f"{len(qs)} questions  ·  {d.instruction}  ·  Recall: {d.recall_focus}",
        )
        for i, q in enumerate(qs, 1):
            qc, ac = t.rows[i].cells
            self._cell_text(qc, f"**{q.number}.**  {q.prompt_md}", 9.5)
            if q.type == "mcq" and q.options:
                for j, opt in enumerate(q.options):
                    self._cell_text(
                        qc,
                        f"{option_letter(j)})  {opt}",
                        8.5,
                        _MUTED,
                        first=False,
                        space=0,
                    )
            if q.type == "true_false":
                self._cell_text(
                    ac, "True   /   False", 9.5, _rgb(_TXT["din"]), bold=True
                )
            elif q.type == "mcq":
                letters = [
                    option_letter(j) for j in range(len(q.options or []))
                ] or list("ABCD")
                self._cell_text(
                    ac, "   /   ".join(letters), 9.5, _rgb(_TXT["din"]), bold=True
                )
            else:
                self._cell_text(ac, "Answer:", 8.5, _MUTED)
                self._lines(ac, 1)
        sc = t.rows[len(qs) + 1].cells[1]
        _shade(sc, "F8FAFC")
        self._cell_text(
            sc, f"Score:  ______ / {len(qs)}", 9, _rgb(_TXT["din"]), bold=True
        )
        self.doc.add_paragraph().paragraph_format.space_after = Pt(4)

    def _spellings(self, sp: Sequence[Spelling]) -> None:
        cols = min(3, len(sp))
        rows = (len(sp) + cols - 1) // cols
        t = self._grid(
            "spell",
            f"{len(sp)} KEY SPELLING{'' if len(sp) == 1 else 'S'} FOR THIS LESSON",
            rows=rows,
            cols=cols,
        )
        for i, s in enumerate(sp):
            c = t.rows[1 + i // cols].cells[i % cols]
            self._cell_text(c, s.word, 11, _rgb(_TXT["spell"]), bold=True)
            self._cell_text(c, s.phonetic, 8.5, _MUTED, first=False, space=1)
            self._cell_text(c, s.meaning, 9, first=False)
            if s.dont_confuse:
                self._cell_text(
                    c,
                    f"Careful: {s.dont_confuse}",
                    8,
                    _rgb("DC2626"),
                    first=False,
                )
            self._cell_text(c, "Practice:", 8, _MUTED, first=False, space=0)
            self._lines(c, 2)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(4)

    def _keywords(self, kws: Sequence[Keyword], has_board: bool) -> None:
        t = self._grid(
            "kw", "KEYWORD BANK", rows=len(kws) + 1, cols=2, widths=[52, 128]
        )
        t.rows[0]._tr.get_or_add_trPr().append(
            safe_xml_fromstring(f"<w:tblHeader {nsdecls('w')}/>")
        )
        t.rows[1]._tr.get_or_add_trPr().append(
            safe_xml_fromstring(f"<w:tblHeader {nsdecls('w')}/>")
        )
        hdr = "DEFINITION (mark-scheme wording)" if has_board else "DEFINITION"
        for i, txt in enumerate(("KEY TERM", hdr)):
            c = t.rows[1].cells[i]
            _shade(c, "F0FDFA")
            self._cell_text(c, txt, 8.5, _rgb(_TXT["kw"]), bold=True)
        for i, k in enumerate(kws, 2):
            tc, dc = t.rows[i].cells
            p = self._cell_text(tc, k.term, 9.5, bold=True)
            r = p.add_run(f"   Tier {k.tier}")
            r.font.size, r.font.bold, r.font.name = Pt(7.5), True, _FONT
            r.font.color.rgb = _rgb(SLATE if k.tier == 3 else NAVY)
            self._cell_text(dc, k.definition, 9)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(4)

    def _ido(self, steps: Sequence[IDoStep]) -> None:
        t = self._grid(
            "ido",
            "I DO — Watch, listen & annotate",
            rows=len(steps),
            cols=2,
            widths=[34, 146],
        )
        for i, s in enumerate(steps, 1):
            lc, bc = t.rows[i].cells
            _shade(lc, "EEF2FF")
            self._cell_text(lc, s.label, 9, _rgb(_TXT["ido"]), bold=True)
            self._cell_text(bc, s.body_md, 9.5)
            if s.examiner_tip:
                self._cell_text(
                    bc,
                    f"Examiner tip: {s.examiner_tip}",
                    8.5,
                    _rgb("B45309"),
                    first=False,
                )
        self.doc.add_paragraph().paragraph_format.space_after = Pt(4)

    def _misconceptions(self, ms: Sequence[Misconception]) -> None:
        body = self._card("mis", "MISCONCEPTION CHECK")[0]
        for i, m in enumerate(ms):
            self._cell_text(
                body,
                f"**Misconception:** {m.misconception}",
                9.5,
                _rgb("DC2626"),
                first=(i == 0),
                space=1,
            )
            ref = f"  ({m.exam_reference})" if m.exam_reference else ""
            self._cell_text(
                body,
                f"**Correction:** {m.correction}{ref}",
                9.5,
                _rgb("059669"),
                first=False,
            )

    def _tasks(
        self,
        kind: str,
        label: str,
        tasks: Sequence[WeDoTask | YouDoTask],
        lines_each: int,
        meta: str | None = None,
        extra: Sequence[str] | None = None,
    ) -> None:
        body = self._card(kind, label, meta=meta)[0]
        first = True
        if extra:
            for e in extra:
                self._cell_text(body, e, 9, _MUTED, first=first, space=1)
                first = False
        for i, t in enumerate(tasks, 1):
            self._cell_text(body, f"**{i}.**  {t.prompt_md}", 9.5, first=first)
            first = False
            scaffold = getattr(t, "scaffold", None)
            if scaffold:
                self._cell_text(
                    body, f"Hint: {scaffold}", 8.5, _rgb(_TXT[kind]), first=False
                )
            self._lines(body, lines_each)

    def _coach(self, points: Sequence[ExamLanguageItem]) -> None:
        body = self._card("coach", "EXAM LANGUAGE COACH")[0]
        first = True
        for c in points:
            self._cell_text(body, c.point, 9.5, bold=True, first=first, space=1)
            first = False
            if c.allow:
                self._cell_text(
                    body,
                    f"ACCEPT: {c.allow}",
                    9,
                    _rgb("059669"),
                    first=False,
                    space=1,
                )
            if c.do_not_accept:
                self._cell_text(
                    body,
                    f"DO NOT ACCEPT: {c.do_not_accept}",
                    9,
                    _rgb("DC2626"),
                    first=False,
                )

    def _examq(self, qs: Sequence[ExamQuestion]) -> None:
        body = self._card("examq", "EXAM QUESTIONS")[0]
        for i, q in enumerate(qs, 1):
            self._cell_text(
                body,
                f"**{i}.**  {q.prompt_md}   **[{q.marks} mark"
                f"{'' if q.marks == 1 else 's'}]**",
                9.5,
                first=(i == 1),
            )
            self._lines(body, max(2, min(6, q.marks + 1)))

    def _exit(self, items: Sequence[str]) -> None:
        t = self._grid(
            "exit",
            "EXIT TICKET — Complete before leaving",
            rows=len(items),
            cols=2,
            widths=[110, 70],
        )
        for i, q in enumerate(items, 1):
            qc, ac = t.rows[i].cells
            self._cell_text(qc, f"**{i}.**  {q}", 9.5)
            self._lines(ac, 2, first=True)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(4)

    def _feedback(self, f: FeedbackBlock) -> None:
        t = self._grid("fb", "FEEDBACK & IMPROVEMENT", rows=2, cols=2, widths=[90, 90])
        for i, txt in enumerate(
            ("Even Better If (EBI)", "My improvement response (green pen)")
        ):
            c = t.rows[1].cells[i]
            _shade(c, "F5F3FF")
            self._cell_text(c, txt, 8.5, _rgb(_TXT["fb"]), bold=True)
        ebi, resp = t.rows[2].cells
        self._cell_text(ebi, f.even_better_if or "", 9)
        self._cell_text(resp, f.improvement_prompt, 9, _MUTED)
        self._lines(resp, 3)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(4)

    def _revision(self, rs: Sequence[RevisionResource]) -> None:
        body = self._card("rev", "REVISION MATERIAL — study at home")[0]
        for i, r in enumerate(rs):
            self._cell_text(
                body,
                r.name,
                10,
                _rgb(_TXT["rev"]),
                bold=True,
                first=(i == 0),
                space=0,
            )
            self._cell_text(body, r.url, 9, _rgb("2563EB"), first=False, space=0)
            self._cell_text(body, r.purpose, 9, _MUTED, first=False)

    def student(self) -> bytes:
        c = self.c
        self._doc_head(c.title, c.audience_note, c.spec_reference)
        self._name_box()
        if c.overview:
            self._overview(c.overview)
        multi = len(c.lessons) > 1
        for i, lesson in enumerate(c.lessons):
            if i:
                self.doc.add_page_break()
            self._lesson_head(lesson, show_objectives=multi)
            if lesson.din:
                self._din(lesson.din)
            if lesson.key_spellings:
                self._spellings(lesson.key_spellings)
            if lesson.keyword_bank:
                self._keywords(lesson.keyword_bank, bool(c.exam_board))
            if lesson.i_do:
                self._ido(lesson.i_do)
            if lesson.misconceptions:
                self._misconceptions(lesson.misconceptions)
            if lesson.we_do:
                self._tasks("wedo", "WE DO — Together", lesson.we_do, 2)
            if lesson.you_do:
                self._tasks(
                    "youdo",
                    "YOU DO — Independent work",
                    lesson.you_do.tasks,
                    3,
                    extra=(
                        ["Success criteria:"]
                        + [f"•  {s}" for s in lesson.you_do.success_criteria]
                        if lesson.you_do.success_criteria
                        else None
                    ),
                )
            if lesson.exam_language_coach:
                self._coach(lesson.exam_language_coach)
            if lesson.exam_questions:
                self._examq(lesson.exam_questions)
            if lesson.exit_ticket:
                self._exit(lesson.exit_ticket)
            if lesson.feedback:
                self._feedback(lesson.feedback)
            if lesson.revision_resources:
                self._revision(lesson.revision_resources)
        self._footer(
            "AI can make mistakes. Always review generated content before using in class."
        )
        return self._save()

    def answers(self) -> bytes:
        c = self.c
        self._doc_head(
            f"{c.title} — ANSWER KEY",
            f"TEACHER COPY  ·  {c.audience_note}",
            c.spec_reference,
        )
        for li, lesson in enumerate(c.lessons):
            if li:
                self.doc.add_page_break()
            p = self.doc.add_paragraph()
            p.paragraph_format.space_after = Pt(3)
            _keep_together(p)
            self._write(p, lesson.title, 13, _INK, True)

            din_ans = [
                (q.number, q.answer_md)
                for q in (lesson.din.questions if lesson.din else [])
                if q.answer_md
            ]
            if din_ans:
                body = self._card("ans", "DO IT NOW — ANSWERS")[0]
                for i, (n, a) in enumerate(din_ans):
                    self._cell_text(
                        body, f"**{n}.**  {a}", 9.5, first=(i == 0), space=1
                    )
            ex_ans = [
                (i, q)
                for i, q in enumerate(lesson.exam_questions or [], 1)
                if q.answer_md
            ]
            if ex_ans:
                body = self._card("ans", "EXAM QUESTIONS — MARK SCHEME")[0]
                for j, (i, q) in enumerate(ex_ans):
                    self._cell_text(
                        body,
                        f"**{i}.**  {q.prompt_md}   **[{q.marks} marks]**",
                        9,
                        _MUTED,
                        first=(j == 0),
                        space=1,
                    )
                    self._cell_text(body, q.answer_md, 9.5, first=False)
            if lesson.exit_ticket_answers:
                body = self._card("ans", "EXIT TICKET — ANSWERS")[0]
                pairs = list(
                    zip_longest(lesson.exit_ticket or [], lesson.exit_ticket_answers)
                )
                for i, (q_txt, a_txt) in enumerate(pairs, 1):
                    self._cell_text(
                        body,
                        f"**{i}.**  {q_txt or '(no question generated)'}",
                        9,
                        _MUTED,
                        first=(i == 1),
                        space=1,
                    )
                    self._cell_text(
                        body, a_txt or "(no answer generated)", 9.5, first=False
                    )
            sec = c.teacher_answer_key or []
            s = sec[li] if li < len(sec) else None
            if s:
                body = self._card("ans", f"WORKED SOLUTIONS — {s.heading}")[0]
                first = True
                for line in [x for x in s.markdown_body.splitlines() if x.strip()]:
                    self._cell_text(body, line, 9.5, first=first, space=1)
                    first = False
        self._footer("TEACHER COPY — contains answers. Do not hand to students.")
        return self._save()

    def _footer(self, text: str) -> None:
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(8)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        r.font.size, r.font.italic, r.font.name = Pt(8), True, _FONT
        r.font.color.rgb = _MUTED

    def _save(self) -> bytes:
        self.doc.core_properties.title = self.c.title.replace("$", "")
        self.doc.core_properties.author = "Fulwood Forge"
        self.doc.core_properties.last_modified_by = "Fulwood Forge"
        self.doc.core_properties.comments = "Generated by Fulwood Forge. AI can make mistakes — review before use in class."
        buf = BytesIO()
        self.doc.save(buf)
        return _strip_template_cruft(buf.getvalue(), self.c.title)


def build_docx(content: GeneratedContent) -> bytes:
    """The STUDENT copy — Fulwood cards, writing space, never any answers."""
    return _Doc(content).student()


def build_answers_docx(content: GeneratedContent) -> bytes:
    """The teacher's separate ANSWER KEY document."""
    return _Doc(content).answers()
