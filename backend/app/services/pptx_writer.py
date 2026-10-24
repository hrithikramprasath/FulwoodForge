"""Native, editable PowerPoint writer for the Fulwood lesson spine (`REQ-SEC-01`, `REQ-SEC-02`).

Provides:
- `build_pptx(content)`: Renders `GeneratedContent` to an editable `.pptx` with native OMML math.
- `calculate_slide_count(content)` (and alias `count_slides`): Deterministic pure-Python slide
  counter (`< 1ms`, zero subprocesses, zero Pandoc calls, zero `.pptx` builds) that matches
  `len(Presentation(BytesIO(build_pptx(content))).slides)` across single, multi-lesson, and
  auto-split overflow decks (`VULN-02` / `REQ-SEC-02`).
- Zero hardcoded emojis (`VULN-01` / `REQ-SEC-01`).
"""

from __future__ import annotations

import contextlib
import math
import re
from collections.abc import Sequence
from io import BytesIO
from itertools import batched
from typing import Any, TypedDict

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

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
    RevisionResource,
    Spelling,
)
from app.schemas.content import (
    ModelStep as IDoStep,
)
from app.schemas.content import (
    Task as WeDoTask,
)
from app.schemas.content import (
    YouDo as YouDoBlock,
)
from app.services.docx_writer import (
    _BG,
    _BORDER,
    _TXT,
    _collect_latex,
    option_letter,
    scrub_control_chars,
)
from app.services.omml import alt_content_from_omath, latex_to_omath_xml

_FONT = "Segoe UI"
_RGB_CTOR: Any = RGBColor

_WHITE: RGBColor = _RGB_CTOR(0xFF, 0xFF, 0xFF)
_INK: RGBColor = _RGB_CTOR(0x0F, 0x17, 0x2A)  # Academic Slate
_MUTED: RGBColor = _RGB_CTOR(0x64, 0x74, 0x8B)
_TIER3: RGBColor = _RGB_CTOR(0x0F, 0x17, 0x2A)
_TIER2: RGBColor = _RGB_CTOR(0x0F, 0x4C, 0x81)  # Oxford Navy

_EMU_IN = 914400
_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.DOTALL)
_STRIP_MD = re.compile(r"^\s*(#{1,6}\s+|[-*+]\s+|>\s+)")

_CW: float = 12.53
_PAD: float = 0.35
_HDR_H: float = 0.62
_MARGIN: float = 0.4
_CARD_Y: float = 0.72
_MAX_H: float = 6.45
_AVAIL_BODY_H: float = _MAX_H - _HDR_H - 0.40


class _SpecDict(TypedDict):
    t: str
    sz: float
    col: RGBColor | None
    bold: bool
    bullet: str | None
    gap: float


def _rgb(h: str) -> RGBColor:
    res: RGBColor = _RGB_CTOR.from_string(h)
    return res


def _est_lines(text: str | None, size: float, width_in: float) -> int:
    """Rough wrapped-line count for a run of text at `size` in `width_in` inches."""
    cleaned = _STRIP_MD.sub("", (text or "").strip())
    cpl = max(8, int(width_in * 72 / (size * 0.52)))
    return sum(max(1, math.ceil(len(ln) / cpl)) for ln in (cleaned or " ").split("\n"))


def _spec(
    t: str,
    sz: float,
    col: RGBColor | None = None,
    bold: bool = False,
    bullet: str | None = None,
    gap: float = 6,
) -> _SpecDict:
    return {
        "t": t,
        "sz": sz,
        "col": col,
        "bold": bold,
        "bullet": bullet,
        "gap": gap,
    }


def _spec_h(s: _SpecDict) -> float:
    """Estimated rendered height (inches) of one paragraph spec."""
    return (
        _est_lines(s["t"], s["sz"], _CW - 2 * _PAD) * s["sz"] * 1.32 / 72
        + s["gap"] / 72
    )


def _group_specs(specs: Sequence[_SpecDict]) -> list[list[_SpecDict]]:
    """Group paragraph specs into slide-sized chunks based on `_AVAIL_BODY_H`."""
    groups: list[list[_SpecDict]] = []
    cur: list[_SpecDict] = []
    h = 0.0
    for s in specs:
        sh = _spec_h(s)
        if cur and h + sh > _AVAIL_BODY_H:
            groups.append(cur)
            cur = []
            h = 0.0
        cur.append(s)
        h += sh
    if cur:
        groups.append(cur)
    return groups or [[]]


def _group_keywords(kws: Sequence[Keyword]) -> list[list[Keyword]]:
    """Group keywords into slide-sized table chunks based on `_AVAIL_BODY_H`."""
    groups: list[list[Keyword]] = []
    cur: list[Keyword] = []
    h = 0.42
    for k in kws:
        kh = 0.24 + _est_lines(k.definition, 15, 7.6) * 0.24
        if cur and h + kh > _AVAIL_BODY_H:
            groups.append(cur)
            cur = []
            h = 0.42
        cur.append(k)
        h += kh
    if cur:
        groups.append(cur)
    return groups


def _din_chunk_specs(din: DINBlock) -> list[list[_SpecDict]]:
    chunks_out: list[list[_SpecDict]] = []
    for chunk in batched(din.questions, 5):
        specs: list[_SpecDict] = [
            _spec(
                f"Recall: {din.recall_focus}  ·  {din.instruction}",
                13,
                _MUTED,
                gap=8,
            )
        ]
        for q in chunk:
            specs.append(_spec(f"{q.number}. {q.prompt_md}", 18, gap=4))
            if q.type == "mcq" and q.options:
                for i, opt in enumerate(q.options):
                    specs.append(
                        _spec(
                            f"{option_letter(i)})  {opt}",
                            15,
                            _MUTED,
                            bullet=" ",
                            gap=2,
                        )
                    )
            elif q.type == "true_false":
                specs.append(_spec("True / False", 15, _MUTED, bullet=" ", gap=2))
        chunks_out.append(specs)
    return chunks_out


def _spellings_specs(spellings: Sequence[Spelling]) -> list[_SpecDict]:
    specs: list[_SpecDict] = []
    for s in spellings:
        specs.append(
            _spec(f"**{s.word}**   ({s.phonetic})", 21, _rgb(_TXT["spell"]), gap=2)
        )
        specs.append(_spec(s.meaning, 17, _MUTED, gap=2))
        if s.dont_confuse:
            specs.append(
                _spec(f"Careful: {s.dont_confuse}", 15, _rgb("B91C1C"), gap=10)
            )
    return specs


def _ido_step_specs(st: IDoStep) -> list[_SpecDict]:
    specs: list[_SpecDict] = [
        _spec(st.label, 21, _rgb(_TXT["ido"]), bold=True, gap=4),
        _spec(st.body_md, 19, gap=6),
    ]
    if st.examiner_tip:
        specs.append(_spec(f"**Examiner tip:** {st.examiner_tip}", 15, _MUTED))
    return specs


def _misconceptions_specs(ms: Sequence[Misconception]) -> list[_SpecDict]:
    specs: list[_SpecDict] = []
    for m in ms:
        specs.append(
            _spec(f"Misconception:  {m.misconception}", 18, _rgb("B91C1C"), gap=2)
        )
        specs.append(_spec(f"Correction:  {m.correction}", 18, _rgb("15803D"), gap=10))
    return specs


def _wedo_specs(tasks: Sequence[WeDoTask]) -> list[_SpecDict]:
    specs: list[_SpecDict] = []
    for i, t in enumerate(tasks):
        specs.append(_spec(f"{i + 1}. {t.prompt_md}", 19, gap=2))
        if t.scaffold:
            specs.append(_spec(f"Hint: {t.scaffold}", 15, _rgb(_TXT["wedo"]), gap=8))
    return specs


def _youdo_specs(y: YouDoBlock) -> list[_SpecDict]:
    specs: list[_SpecDict] = []
    if y.success_criteria:
        specs.append(_spec("**Success criteria**", 17, _rgb("15803D"), gap=2))
        for c in y.success_criteria:
            specs.append(_spec(c, 15, _MUTED, bullet="•", gap=2))
        specs[-1]["gap"] = 10
    for i, t in enumerate(y.tasks):
        specs.append(_spec(f"{i + 1}. {t.prompt_md}", 19, gap=2))
        if t.scaffold:
            specs.append(_spec(f"Hint: {t.scaffold}", 15, _rgb(_TXT["youdo"]), gap=8))
    return specs


def _coach_specs(points: Sequence[ExamLanguageItem]) -> list[_SpecDict]:
    specs: list[_SpecDict] = []
    for c in points:
        specs.append(_spec(c.point, 19, bold=True, gap=2))
        if c.allow:
            specs.append(_spec(f"ACCEPT: {c.allow}", 16, _rgb("15803D"), gap=2))
        if c.do_not_accept:
            specs.append(
                _spec(f"DO NOT ACCEPT: {c.do_not_accept}", 16, _rgb("B91C1C"), gap=10)
            )
    return specs


def _examq_specs(qs: Sequence[ExamQuestion]) -> list[_SpecDict]:
    return [
        _spec(
            f"{i + 1}. {q.prompt_md}  **[{q.marks} mark{'' if q.marks == 1 else 's'}]**",
            19,
            gap=6,
        )
        for i, q in enumerate(qs)
    ]


def _exit_specs(items: Sequence[str]) -> list[_SpecDict]:
    return [_spec(f"{i + 1}. {q}", 19, gap=8) for i, q in enumerate(items)]


def _revision_specs(resources: Sequence[RevisionResource]) -> list[_SpecDict]:
    specs: list[_SpecDict] = []
    for r in resources:
        specs.append(_spec(f"**{r.name}**", 21, _rgb(_TXT["rev"]), gap=1))
        specs.append(_spec(r.url, 16, _rgb("0F4C81"), gap=1))
        specs.append(_spec(r.purpose, 16, _MUTED, gap=10))
    return specs


def _answers_specs(lines: Sequence[str]) -> list[_SpecDict]:
    return [_spec(ln, 17, gap=6) for ln in lines]


def calculate_slide_count(content: GeneratedContent) -> int:
    """Compute the exact slide count for `content` using pure-Python height arithmetic (`REQ-SEC-02`).

    Executes in `< 1ms` with zero subprocesses, zero Pandoc invocations, and zero `.pptx` builds,
    while matching `len(Presentation(BytesIO(build_pptx(content))).slides)` identically.
    """
    c = scrub_control_chars(content)
    total_slides = 1  # Title slide

    for lesson in c.lessons:
        total_slides += 1  # Lesson intro slide
        if lesson.din:
            for chunk_specs in _din_chunk_specs(lesson.din):
                total_slides += len(_group_specs(chunk_specs))
            din_ans = [
                f"{q.number}. {q.answer_md}"
                for q in lesson.din.questions
                if q.answer_md
            ]
            if din_ans:
                total_slides += len(_group_specs(_answers_specs(din_ans)))
        if lesson.key_spellings:
            total_slides += len(_group_specs(_spellings_specs(lesson.key_spellings)))
        if lesson.keyword_bank:
            total_slides += len(_group_keywords(lesson.keyword_bank))
        if lesson.i_do:
            for st in lesson.i_do:
                total_slides += len(_group_specs(_ido_step_specs(st)))
        if lesson.misconceptions:
            total_slides += len(
                _group_specs(_misconceptions_specs(lesson.misconceptions))
            )
        if lesson.we_do:
            total_slides += len(_group_specs(_wedo_specs(lesson.we_do)))
        if lesson.you_do:
            total_slides += len(_group_specs(_youdo_specs(lesson.you_do)))
        if lesson.exam_language_coach:
            total_slides += len(_group_specs(_coach_specs(lesson.exam_language_coach)))
        if lesson.exam_questions:
            total_slides += len(_group_specs(_examq_specs(lesson.exam_questions)))
            examq_ans = [
                f"{i + 1}. {q.answer_md}"
                for i, q in enumerate(lesson.exam_questions)
                if q.answer_md
            ]
            if examq_ans:
                total_slides += len(_group_specs(_answers_specs(examq_ans)))
        if lesson.exit_ticket:
            total_slides += len(_group_specs(_exit_specs(lesson.exit_ticket)))
            exit_ans = [
                f"{i + 1}. {a}"
                for i, a in enumerate(lesson.exit_ticket_answers or [])
                if a
            ]
            if exit_ans:
                total_slides += len(_group_specs(_answers_specs(exit_ans)))
        if lesson.revision_resources:
            total_slides += len(
                _group_specs(_revision_specs(lesson.revision_resources))
            )

    return total_slides


class _Deck:
    CW = _CW
    PAD = _PAD
    HDR_H = _HDR_H
    M = _MARGIN
    CARD_Y = _CARD_Y
    MAX_H = _MAX_H

    def __init__(self, content: GeneratedContent) -> None:
        self.c = scrub_control_chars(content)
        self.ctx = ""
        self.prs: Any = Presentation()
        self.prs.slide_width = Inches(13.333)
        self.prs.slide_height = Inches(7.5)
        self.blank = self.prs.slide_layouts[6]
        latex: list[str] = []
        _collect_latex(self.c.model_dump(mode="json"), latex)
        uniq = list(dict.fromkeys(latex))
        self.math: dict[str, str] = (
            dict(zip(uniq, latex_to_omath_xml(uniq), strict=False)) if uniq else {}
        )

    def _style(
        self,
        run: Any,
        size: float,
        bold: bool = False,
        color: RGBColor | None = None,
    ) -> None:
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.name = _FONT
        run.font.color.rgb = color or _INK

    def _round(self, shape: Any, radius_in: float) -> None:
        with contextlib.suppress(
            AttributeError,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            ZeroDivisionError,
        ):
            short = min(shape.width, shape.height) / _EMU_IN
            shape.adjustments[0] = max(0.0, min(0.5, radius_in / short))

    def _omml(self, latex: str, size: float) -> Any:
        xml = self.math.get(latex.strip()) or latex_to_omath_xml([latex])[0]
        return alt_content_from_omath(xml, latex, size_pt=size)

    def _runs(
        self,
        p: Any,
        segment: str,
        size: float,
        color: RGBColor | None,
        bold: bool,
    ) -> None:
        for i, part in enumerate(segment.split("**")):
            if part:
                self._style(p.add_run(), size, bold or (i % 2 == 1), color)
                p.runs[-1].text = part

    def _para(self, tf: Any, s: _SpecDict, first: bool) -> None:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        p.space_after = Pt(s["gap"])
        text = _STRIP_MD.sub("", (s["t"] or "").strip())
        if s["bullet"]:
            r = p.add_run()
            r.text = s["bullet"] + "  "
            self._style(r, s["sz"], False, s["col"] or _INK)
        pos = 0
        for m in _MATH_RE.finditer(text):
            if m.start() > pos:
                self._runs(p, text[pos : m.start()], s["sz"], s["col"], s["bold"])
            p._p.append(self._omml(m.group(1) or m.group(2), s["sz"]))
            pos = m.end()
        if pos < len(text):
            self._runs(p, text[pos:], s["sz"], s["col"], s["bold"])

    def _slide(self) -> Any:
        return self.prs.slides.add_slide(self.blank)

    def _eyebrow(self, slide: Any) -> None:
        if not self.ctx:
            return
        box = slide.shapes.add_textbox(
            Inches(self.M), Inches(0.28), Inches(self.CW), Inches(0.32)
        )
        r = box.text_frame.paragraphs[0].add_run()
        self._style(r, 11, True, _MUTED)
        r.text = re.sub(r"\$", "", self.ctx).upper()

    def _card_frame(
        self,
        kind: str,
        label: str,
        body_h: float,
    ) -> tuple[Any, float, float, float, float]:
        slide = self._slide()
        self._eyebrow(slide)
        total_h = min(self.MAX_H, max(1.25, self.HDR_H + 0.22 + body_h + 0.18))
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(self.M),
            Inches(self.CARD_Y),
            Inches(self.CW),
            Inches(total_h),
        )
        card.fill.solid()
        card.fill.fore_color.rgb = _WHITE
        card.line.color.rgb = _rgb(_BORDER[kind])
        card.line.width = Pt(1.25)
        card.shadow.inherit = False
        self._round(card, 0.14)
        hdr = slide.shapes.add_shape(
            MSO_SHAPE.ROUND_2_SAME_RECTANGLE,
            Inches(self.M),
            Inches(self.CARD_Y),
            Inches(self.CW),
            Inches(self.HDR_H),
        )
        hdr.fill.solid()
        hdr.fill.fore_color.rgb = _rgb(_BG[kind])
        hdr.line.fill.background()
        hdr.shadow.inherit = False
        self._round(hdr, 0.14)
        htf = hdr.text_frame
        htf.margin_left = Inches(0.3)
        htf.word_wrap = True
        htf.vertical_anchor = MSO_ANCHOR.MIDDLE
        htf.paragraphs[0].alignment = PP_ALIGN.LEFT
        r = htf.paragraphs[0].add_run()
        r.text = label
        self._style(r, 17, True, _rgb(_TXT[kind]))
        return (
            slide,
            self.M + self.PAD,
            self.CARD_Y + self.HDR_H + 0.12,
            self.CW - 2 * self.PAD,
            total_h - self.HDR_H - 0.24,
        )

    def _card(
        self,
        kind: str,
        label: str,
        specs: Sequence[_SpecDict],
        note: str | None = None,
    ) -> Any:
        groups = _group_specs(specs)
        slides: list[Any] = []
        for g in groups:
            slide, bx, by, bw, bh = self._card_frame(
                kind, label, sum(_spec_h(s) for s in g)
            )
            tf = slide.shapes.add_textbox(
                Inches(bx), Inches(by), Inches(bw), Inches(bh)
            ).text_frame
            tf.word_wrap = True
            for i, s in enumerate(g):
                self._para(tf, s, first=(i == 0))
            slides.append(slide)
        if note and slides:
            self._notes(slides[0], note)
        return slides[-1]

    def _notes(self, slide: Any, text: str) -> None:
        if text:
            slide.notes_slide.notes_text_frame.text = text

    def _clean(
        self,
        kicker: str,
        title: str,
        subtitle: str,
        bullets: Sequence[str] | None,
        pill: str | None = None,
        pill_col: str | None = None,
        start_y: float = 2.05,
    ) -> Any:
        slide = self._slide()
        box = slide.shapes.add_textbox(
            Inches(0.9), Inches(start_y), Inches(11.5), Inches(4.5)
        )
        tf = box.text_frame
        tf.word_wrap = True
        k = tf.paragraphs[0].add_run()
        k.text = kicker.upper()
        self._style(k, 14, True, _MUTED)
        tp = tf.add_paragraph()
        tp.space_before = Pt(4)
        self._para_into(tp, title, 38, _INK, bold=True)
        if subtitle:
            sp = tf.add_paragraph()
            sp.space_before = Pt(6)
            self._para_into(sp, subtitle, 20, _MUTED)
        for b in bullets or []:
            bp = tf.add_paragraph()
            bp.space_before = Pt(8)
            r = bp.add_run()
            r.text = "•  "
            self._style(r, 19, False, _INK)
            self._para_into(bp, b, 19, _INK)
        if pill:
            self._pill(slide, pill, pill_col or "4338ca", start_y - 0.02)
        return slide

    def _para_into(
        self,
        p: Any,
        text: str | None,
        size: float,
        color: RGBColor | None,
        bold: bool = False,
    ) -> None:
        clean = _STRIP_MD.sub("", (text or "").strip())
        pos = 0
        for m in _MATH_RE.finditer(clean):
            if m.start() > pos:
                self._runs(p, clean[pos : m.start()], size, color, bold)
            p._p.append(self._omml(m.group(1) or m.group(2), size))
            pos = m.end()
        if pos < len(clean):
            self._runs(p, clean[pos:], size, color, bold)

    def _pill(self, slide: Any, text: str, hexcol: str, y: float) -> None:
        w = min(11.0, 0.11 * len(text) + 0.5)
        pill = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(0.9),
            Inches(y),
            Inches(w),
            Inches(0.42),
        )
        pill.fill.solid()
        pill.fill.fore_color.rgb = _rgb(_BG["examq"])
        pill.line.fill.background()
        pill.shadow.inherit = False
        self._round(pill, 0.21)
        pill.text_frame.word_wrap = True
        self._style(pill.text_frame.paragraphs[0].add_run(), 13, True, _rgb(hexcol))
        pill.text_frame.paragraphs[0].runs[0].text = text

    def _title_slide(self) -> None:
        c = self.c
        self._clean(
            c.audience_note,
            c.title,
            c.overview.summary if c.overview else "",
            (c.overview.objectives if c.overview else [])[:4],
            pill=c.spec_reference,
            pill_col="4338ca",
            start_y=2.4,
        )

    def _lesson_intro(self, lesson: Lesson) -> None:
        self._clean(
            "Learning focus",
            lesson.title,
            lesson.lesson_focus,
            lesson.objectives,
            start_y=2.0,
        )

    def _din(self, din: DINBlock) -> None:
        note = (
            f"Silent starter recalling {din.recall_focus}. {din.instruction}. "
            "Cold-call answers before moving on."
        )
        for specs in _din_chunk_specs(din):
            self._card("din", "Do It Now", specs, note=note)

    def _spellings(self, spellings: Sequence[Spelling]) -> None:
        specs = _spellings_specs(spellings)
        self._card(
            "spell",
            f"{len(spellings)} Key Spelling{'' if len(spellings) == 1 else 's'}",
            specs,
            note="Get these into books — students misspell them in exams. Test the spellings again later.",
        )

    def _keywords(self, kws: Sequence[Keyword]) -> None:
        for g in _group_keywords(kws):
            self._keyword_table(g)

    def _keyword_table(self, kws: Sequence[Keyword]) -> None:
        rows = len(kws) + 1
        row_h = [_est_lines(k.definition, 15, 7.6) for k in kws]
        body_h = 0.42 + sum(0.24 + n * 0.24 for n in row_h)
        slide, bx, by, bw, _bh = self._card_frame("kw", "Keyword Bank", body_h)
        tbl = slide.shapes.add_table(
            rows, 2, Inches(bx), Inches(by), Inches(bw), Inches(0.4)
        ).table
        tbl.first_row = False
        tbl.columns[0].width = Inches(3.5)
        tbl.columns[1].width = Inches(bw - 3.5)
        hdr = "Definition (mark-scheme wording)" if self.c.exam_board else "Definition"
        for j, head in enumerate(("Key term", hdr)):
            c = tbl.cell(0, j)
            c.fill.solid()
            c.fill.fore_color.rgb = _rgb("E0F2FE")
            self._cell(c, [_spec(head, 14, _rgb(_TXT["kw"]), bold=True)])
        for i, k in enumerate(kws, 1):
            tier = _TIER3 if k.tier == 3 else _TIER2
            term = tbl.cell(i, 0)
            term.fill.solid()
            term.fill.fore_color.rgb = _WHITE
            term.vertical_anchor = MSO_ANCHOR.MIDDLE
            term.margin_top = term.margin_bottom = Inches(0.04)
            term.margin_left = Inches(0.1)
            term.text_frame.word_wrap = True
            p = term.text_frame.paragraphs[0]
            self._para_into(p, k.term, 14, _INK, bold=True)
            r2 = p.add_run()
            r2.text = "   Tier " + str(k.tier)
            self._style(r2, 11, True, tier)
            dcell = tbl.cell(i, 1)
            dcell.fill.solid()
            dcell.fill.fore_color.rgb = _WHITE
            self._cell(dcell, [_spec(k.definition, 13)])
        self._notes(
            slide,
            "Key terms in the exam board's accepted wording. Flag the Tier 2 words they'll meet in the tasks.",
        )

    def _cell(self, cell: Any, specs: Sequence[_SpecDict]) -> None:
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.margin_top = cell.margin_bottom = Inches(0.04)
        cell.margin_left = Inches(0.1)
        tf = cell.text_frame
        tf.word_wrap = True
        for i, s in enumerate(specs):
            self._para(tf, s, first=(i == 0))

    def _ido(self, steps: Sequence[IDoStep]) -> None:
        for st in steps:
            specs = _ido_step_specs(st)
            note = (
                f"Examiner tip: {st.examiner_tip}"
                if st.examiner_tip
                else "Model this explicitly — think aloud through each step so students can annotate."
            )
            self._card("ido", "I DO — Watch, listen & annotate", specs, note=note)

    def _misconceptions(self, ms: Sequence[Misconception]) -> None:
        self._card(
            "mis",
            "Misconception Check",
            _misconceptions_specs(ms),
            note="Surface the wrong idea first, then correct it in the exam's accepted wording.",
        )

    def _wedo(self, tasks: Sequence[WeDoTask]) -> None:
        self._card(
            "wedo",
            "WE DO — Together",
            _wedo_specs(tasks),
            note="Do the first one together, then release. Scaffolds fade as you go.",
        )

    def _youdo(self, y: YouDoBlock) -> None:
        note = "Independent work — the bulk of the lesson. Circulate; the last task is the stretch."
        if y.success_criteria:
            note += " Success criteria: " + "; ".join(y.success_criteria) + "."
        self._card(
            "youdo",
            "YOU DO — Independent work",
            _youdo_specs(y),
            note=note,
        )

    def _coach(self, points: Sequence[ExamLanguageItem]) -> None:
        self._card(
            "coach",
            "Exam Language Coach",
            _coach_specs(points),
            note="Drill the exact accepted wording — marks are lost on terminology, not on ideas.",
        )

    def _examq(self, qs: Sequence[ExamQuestion]) -> None:
        ms = "\n".join(
            f"{i + 1}. {q.answer_md}" for i, q in enumerate(qs) if q.answer_md
        )
        note = (
            f"Mark scheme:\n{ms}"
            if ms
            else "Exam-style questions — set under timed conditions and mark against the AO wording."
        )
        self._card("examq", "Exam Questions", _examq_specs(qs), note=note)

    def _exit(self, items: Sequence[str]) -> None:
        self._card(
            "exit",
            "Exit Ticket",
            _exit_specs(items),
            note="Quick check for understanding — collect responses before they leave.",
        )

    def _revision(self, resources: Sequence[RevisionResource]) -> None:
        self._card(
            "rev",
            "Revision Material — study at home",
            _revision_specs(resources),
            note="Point students at these before they leave. "
            + "; ".join(f"{r.name} ({r.url})" for r in resources),
        )

    def _answers_card(self, label: str, lines: Sequence[str]) -> None:
        if not lines:
            return
        self._card(
            "ans",
            f"Answers — {label}",
            _answers_specs(lines),
            note="Reveal after students have attempted.\n" + "\n".join(lines),
        )

    def _din_answers(self, din: DINBlock) -> None:
        self._answers_card(
            "Do It Now",
            [f"{q.number}. {q.answer_md}" for q in din.questions if q.answer_md],
        )

    def _examq_answers(self, qs: Sequence[ExamQuestion]) -> None:
        self._answers_card(
            "Exam Questions",
            [f"{i + 1}. {q.answer_md}" for i, q in enumerate(qs) if q.answer_md],
        )

    def _exit_answers(self, answers: Sequence[str]) -> None:
        self._answers_card(
            "Exit Ticket",
            [f"{i + 1}. {a}" for i, a in enumerate(answers) if a],
        )

    def compose(self) -> Any:
        """Lay out every slide and return the Presentation, unsaved."""
        self._title_slide()
        for lesson in self.c.lessons:
            self.ctx = ""
            self._lesson_intro(lesson)
            self.ctx = lesson.title
            if lesson.din:
                self._din(lesson.din)
                self._din_answers(lesson.din)
            if lesson.key_spellings:
                self._spellings(lesson.key_spellings)
            if lesson.keyword_bank:
                self._keywords(lesson.keyword_bank)
            if lesson.i_do:
                self._ido(lesson.i_do)
            if lesson.misconceptions:
                self._misconceptions(lesson.misconceptions)
            if lesson.we_do:
                self._wedo(lesson.we_do)
            if lesson.you_do:
                self._youdo(lesson.you_do)
            if lesson.exam_language_coach:
                self._coach(lesson.exam_language_coach)
            if lesson.exam_questions:
                self._examq(lesson.exam_questions)
                self._examq_answers(lesson.exam_questions)
            if lesson.exit_ticket:
                self._exit(lesson.exit_ticket)
                self._exit_answers(lesson.exit_ticket_answers or [])
            if lesson.revision_resources:
                self._revision(lesson.revision_resources)
        self.prs.core_properties.title = self.c.title
        self.prs.core_properties.author = "Fulwood Forge"
        self.prs.core_properties.last_modified_by = "Fulwood Forge"
        return self.prs

    def build(self) -> bytes:
        buf = BytesIO()
        self.compose().save(buf)
        return buf.getvalue()


def build_pptx(content: GeneratedContent) -> bytes:
    """Render `content` to a native, editable Fulwood-format .pptx (bytes)."""
    return _Deck(content).build()


def count_slides(content: GeneratedContent) -> int:
    """Alias for `calculate_slide_count(content)` preserved for worker compatibility."""
    return calculate_slide_count(content)
