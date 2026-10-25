"""Shared data contract — Fulwood Forge lesson resources.

This is the CANONICAL shape. Two hand-maintained mirrors must stay in sync
(see the `contract-sync` skill):
  - frontend/src/types/content.ts   (TypeScript types)
  - frontend/src/schemas/content.ts (Zod runtime validation)

The output schema models a real Fulwood teaching resource: an ordered list of
`Lesson`s, each following the school's spine — DIN -> Key Spellings -> Keyword
Bank -> I DO -> Misconception Check -> WE DO -> YOU DO -> Exam Language Coach ->
Exam Questions -> Exit Ticket -> Feedback & Improvement.

Every lesson PART is optional so one uniform shape serves all three task types:
  - worksheet  -> one compact lesson: DIN + keyword bank + YOU DO + exam Qs
  - powerpoint -> one lesson delivered as slides: I DO / WE DO / YOU DO + exit
  - booklet    -> several full lessons (all parts present)
The prompt engine decides which parts to fill per task type; the renderers and
exporters draw whatever is present. New part types can be added later without
breaking existing resources.

Markdown + LaTeX convention (applies to every free-text `*_md` / body field):
GitHub-flavoured Markdown; maths in LaTeX (inline ``$...$``, display ``$$...$$``)
rendered by KaTeX; units/money/percentages/degrees stay plain text.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# A single crafted string field crashes the renderers (a 1 MB `title` 500'd the
# docx writer in a direct pentest, 30 Jul 2026): it sails under the 5 MB body cap
# but blows up python-docx/lxml downstream. Real fields are a few KB; 50 KB is
# generous headroom, so this only ever trips deliberate abuse. Enforced at the
# boundary below so the renderer never sees an oversized string.
_MAX_STR = 50_000

TaskTypeLiteral = Literal["worksheet", "powerpoint", "booklet"]
YearGroupLiteral = Literal["7", "8", "9", "10", "11"]
AbilityLiteral = Literal["foundation", "mixed", "high"]
BookletLengthLiteral = Literal["short", "standard", "extended"]

SubjectLiteral = Literal[
    "Mathematics",
    "English Language",
    "English Literature",
    "Biology",
    "Chemistry",
    "Physics",
    "Combined Science",
    "Computer Science",
    "History",
    "Geography",
    "Religious Studies",
    "French",
    "Spanish",
    "Design and Technology",
    "Art and Design",
    "Music",
    "Physical Education",
    "Business Studies",
    "Creative iMedia",
    # Preserved curriculum shorthand aliases for existing fixtures and UI presets:
    "Maths",
    "English",
    "Science",
    "Computing",
]

ExamBoardLiteral = Literal[
    "AQA",
    "Edexcel",
    "OCR",
    "WJEC",
    "Eduqas",
    "None / General",
    # Preserved UI aliases:
    "WJEC / Eduqas",
    "Not sure / general",
]

ALL_SUBJECTS: tuple[SubjectLiteral, ...] = (
    "Mathematics",
    "English Language",
    "English Literature",
    "Biology",
    "Chemistry",
    "Physics",
    "Combined Science",
    "Computer Science",
    "History",
    "Geography",
    "Religious Studies",
    "French",
    "Spanish",
    "Design and Technology",
    "Art and Design",
    "Music",
    "Physical Education",
    "Business Studies",
    "Creative iMedia",
    "Maths",
    "English",
    "Science",
    "Computing",
)

ALL_EXAM_BOARDS: tuple[ExamBoardLiteral, ...] = (
    "AQA",
    "Edexcel",
    "OCR",
    "WJEC",
    "Eduqas",
    "None / General",
    "WJEC / Eduqas",
    "Not sure / general",
)

ALL_TASK_TYPES: tuple[TaskTypeLiteral, ...] = ("worksheet", "powerpoint", "booklet")


def _reject_giant_strings(data: object) -> None:
    """Reject any single string in a parsed body longer than _MAX_STR.
    Iterative (explicit stack, no recursion) so a deep body can't blow the stack
    HERE — deep-nesting is handled separately by the RecursionError handler in
    main.py, before this ever runs."""
    stack: list[tuple[object, int]] = [(data, 0)]
    nodes = chars = dollars = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if depth > 20 or nodes > 12000:
            raise ValueError("resource structure is too complex")
        if isinstance(node, BaseModel):
            stack.append((node.model_dump(), depth))
        elif isinstance(node, str):
            chars += len(node)
            dollars += node.count("$")
            if len(node) > _MAX_STR or chars > 500_000 or dollars > 2400:
                raise ValueError("resource text or equation budget exceeded")
        elif isinstance(node, dict):
            if len(node) > 100:
                raise ValueError("too many fields")
            for key, value in node.items():
                if (
                    key in {"title", "subject", "exam_board", "spec_reference"}
                    and isinstance(value, str)
                    and len(value) > 255
                ):
                    raise ValueError(
                        f"{key} exceeds the document metadata limit of 255 characters"
                    )
                if key == "lessons" and isinstance(value, list) and len(value) > 12:
                    raise ValueError("a resource may contain at most 12 lessons")
                stack.extend(((key, depth + 1), (value, depth + 1)))
        elif isinstance(node, list):
            if len(node) > 100:
                raise ValueError("a section may contain at most 100 items")
            stack.extend((value, depth + 1) for value in node)


# ── Input schema (matches the frontend TeacherRequest / GenerateRequest) ─────

# UK Unique Pupil Number (UPN): 1 letter (excluding I, O, S) followed by 12 digits
_UK_UPN_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-HJ-NP-RT-Z][0-9]{3}[\s-]?[0-9]{4}[\s-]?[0-9]{4}[0-9]\b",
    re.IGNORECASE,
)
# UK National Insurance Number (NINO) - includes HMRC test prefix QQ
_UK_NINO_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:QQ|[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z])[\s-]?[0-9]{2}[\s-]?[0-9]{2}[\s-]?[0-9]{2}[\s-]?[A-D]\b",
    re.IGNORECASE,
)
# Email address in topic prompt
_EMAIL_PII_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
# UK mobile / landline phone number (e.g. 07xxx xxxxxx or +44 7xxx xxxxxx)
_UK_PHONE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\+44[\s-]?7\d{3}|\b07\d{3})[\s-]?\d{3}[\s-]?\d{3}\b"
)


def scan_topic_for_pupil_pii(text: str) -> str:
    """Reject lesson prompts containing pupil/staff PII identifiers (UK GDPR / KCSIE DLP)."""
    if _UK_UPN_RE.search(text):
        raise ValueError(
            "Safeguarding DLP block: Topic contains a UK Unique Pupil Number (UPN). Remove all pupil identifiers."
        )
    if _UK_NINO_RE.search(text):
        raise ValueError(
            "Safeguarding DLP block: Topic contains a UK National Insurance Number. Remove all personal identifiers."
        )
    if _EMAIL_PII_RE.search(text):
        raise ValueError(
            "Safeguarding DLP block: Topic contains an email address. Remove all personal contact details."
        )
    if _UK_PHONE_RE.search(text):
        raise ValueError(
            "Safeguarding DLP block: Topic contains a UK phone number. Remove all personal contact details."
        )
    return text


class TeacherRequest(BaseModel):
    task_type: TaskTypeLiteral
    # Constrained to strict SubjectLiteral enum (REQ-SEC-09 / VULN-09) to prevent
    # arbitrary prompt-injection strings through the subject field.
    subject: SubjectLiteral
    # Constrained to match the frontend's own enum (frontend/src/schemas/form.ts).
    # Kept a Literal of STRINGS: TeacherRequest is input-only and never goes to
    # Gemini as a response schema, so the int-enum limitation noted on Keyword.tier
    # does not apply here.
    year_group: YearGroupLiteral
    topic: str = Field(min_length=2, max_length=1000)
    ability: AbilityLiteral
    question_count: str | None = Field(default=None, max_length=20)
    slide_count: str | None = Field(default=None, max_length=20)
    booklet_length: BookletLengthLiteral | None = None
    # Only collected for GCSE years (10-11); drives exam-board mark-scheme wording.
    # Constrained to strict ExamBoardLiteral enum (REQ-SEC-09 / VULN-09).
    exam_board: ExamBoardLiteral | None = None
    include_answer_key: bool | None = False

    @field_validator("topic", mode="before")
    @classmethod
    def _strip_topic(cls, v: object) -> object:
        if isinstance(v, str):
            cleaned = v.strip()
            return scan_topic_for_pupil_pii(cleaned)
        return v

    @field_validator("exam_board", mode="before")
    @classmethod
    def _empty_exam_board_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("question_count", "slide_count")
    @classmethod
    def _whole_number(cls, v: str | None) -> str | None:
        """These are strings (the form posts strings) but reach `int()` in the prompt
        builder, which is OUTSIDE the try in generate_content — so a non-numeric value
        used to surface as a 500 instead of a 422. Reject it here, at the boundary.
        Blank/None still means "use the default", and surrounding spaces still parse."""
        if v is None or not v.strip():
            return None
        value = v.strip()
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 50:
            raise ValueError("must be a whole number between 1 and 50")
        return str(int(value))


# Canonical alias used by SPEC.md and Wave 1+ contracts
GenerateRequest = TeacherRequest


# ── Lesson parts (the Fulwood spine) ─────────────────────────────────────────


class DinQuestion(BaseModel):
    """One 'Do It Now' recall question — prior learning only, never this lesson's
    new content."""

    number: int = Field(description="Question number, 1-based.")
    type: Literal["true_false", "mcq", "short"] = Field(
        description="true_false = a statement to judge; mcq = 4 options with one "
        "plausible red herring; short = one word or one short sentence."
    )
    prompt_md: str = Field(
        description="The question, in Markdown (may contain LaTeX maths)."
    )
    options: list[str] | None = Field(
        default=None,
        description="Exactly 4 options for an mcq (A-D), omitted for other types.",
    )
    answer_md: str | None = Field(
        default=None,
        description="The answer, in Markdown (may contain LaTeX). ONLY when the "
        "teacher requested an answer key — powers the reveal slide after the DIN.",
    )


class DinTask(BaseModel):
    """Do It Now — 10 quick recall questions on PRIOR learning, incl. at least one
    from an earlier topic. Silent starter, done in a few minutes."""

    recall_focus: str = Field(
        description="One line naming the prior topics being recalled, e.g. "
        "'Prior KS4 biology — immune system, pathogens, vaccination'."
    )
    instruction: str = Field(
        default="Complete in silence · under 7 minutes",
        description="The standing instruction shown under the DIN heading.",
    )
    questions: list[DinQuestion] = Field(description="The recall questions in order.")


class Spelling(BaseModel):
    """One of the lesson's key spellings — a Work-Scrutiny evidence stamp."""

    word: str
    phonetic: str = Field(
        description="Syllable breakdown with the stress in CAPS, e.g. 'com-ple-MEN-ta-ry'."
    )
    meaning: str = Field(description="Short, student-friendly meaning.")
    dont_confuse: str | None = Field(
        default=None,
        description="Common confusion to flag, e.g. \"not 'complimentary'\".",
    )


class Keyword(BaseModel):
    """One keyword-bank entry, defined in exam-board mark-scheme language."""

    term: str
    # Plain int, not Literal[2, 3]: Gemini's structured-output schema only allows
    # STRING enum values, so an int enum errors out before the call is even made.
    tier: int = Field(
        description="Vocabulary tier: 2 (academic) or 3 (subject-specific)."
    )
    definition: str = Field(
        description="Definition using the exam board's accepted mark-scheme wording."
    )


class ModelStep(BaseModel):
    """One step of the teacher's 'I DO' worked model."""

    label: str = Field(description="Short step label, e.g. 'Step 1 — The problem'.")
    body_md: str = Field(
        description="The explanation to read aloud/annotate, in Markdown (may contain LaTeX)."
    )
    examiner_tip: str | None = Field(
        default=None,
        description="Optional examiner tip / mark-scheme pointer for this step.",
    )


class Misconception(BaseModel):
    """A Misconception Check — a real, common wrong idea and its correction."""

    misconception: str = Field(description="The wrong idea students commonly hold.")
    correction: str = Field(
        description="The correct understanding, in accepted exam language."
    )
    exam_reference: str | None = Field(
        default=None,
        description="Optional examiner-report reference, e.g. 'AQA 2022 report'.",
    )


class Task(BaseModel):
    """A practice task (WE DO collaborative, or YOU DO independent)."""

    prompt_md: str = Field(
        description="The task, in Markdown (may contain LaTeX maths)."
    )
    scaffold: str | None = Field(
        default=None,
        description="Optional sentence starter / hint. Present for WE DO and early "
        "YOU DO tasks; fades to None as tasks get harder.",
    )


class YouDo(BaseModel):
    """Independent work — the bulk of the lesson: rising difficulty, fading scaffolds."""

    tasks: list[Task] = Field(description="Independent tasks in increasing difficulty.")
    success_criteria: list[str] = Field(
        description="Student-facing success criteria for this section (a Work-Scrutiny requirement)."
    )


class CoachPoint(BaseModel):
    """An Exam Language Coach point — explicit accepted/rejected terminology."""

    point: str = Field(description="The terminology/technique being coached.")
    allow: str | None = Field(default=None, description="What the mark scheme ALLOWS.")
    do_not_accept: str | None = Field(
        default=None, description="What is NOT accepted / is insufficient."
    )


class ExamQuestion(BaseModel):
    """An exam-style question linked to the lesson, with its mark tariff."""

    prompt_md: str = Field(
        description="The exam question, in Markdown (may contain LaTeX)."
    )
    marks: int = Field(description="Mark tariff for the question.")
    answer_md: str | None = Field(
        default=None,
        description="Worked answer / mark scheme for this question, in Markdown "
        "(may contain LaTeX). ONLY when the teacher requested an answer key — "
        "powers the reveal slide after the exam questions.",
    )


class RevisionResource(BaseModel):
    """One free revision website for students to use at home. Replaces the
    green-pen Feedback section in a PowerPoint, where it isn't actionable.

    ponytail: `url` is model-generated, so it can be imperfect — the prompt pins
    the model to a short list of well-known UK sites to keep links real, but a
    teacher should still glance at them before projecting.
    """

    name: str = Field(description="The website's name, e.g. 'BBC Bitesize'.")
    url: str = Field(description="The website address, e.g. 'bbc.co.uk/bitesize'.")
    purpose: str = Field(
        description="One short line on what a student uses it for, tied to this lesson."
    )


class Feedback(BaseModel):
    """Feedback & Improvement — the green-pen response section (evidence stamp)."""

    even_better_if: str | None = Field(
        default=None,
        description="An EBI prompt aimed at a common weakness for this lesson.",
    )
    improvement_prompt: str = Field(
        description="What the student should do in the green-pen response space, "
        "e.g. 'Reattempt exit-ticket Q2 using the correct exam term.'"
    )


class Lesson(BaseModel):
    """One Fulwood lesson. All parts after `title`/`lesson_focus`/`objectives` are
    OPTIONAL — the prompt fills the subset that suits the task type."""

    title: str = Field(
        description="Lesson title, e.g. 'Lesson 3 — Monoclonal Antibodies'."
    )
    lesson_focus: str = Field(
        description="One or two lines: the learning focus of this lesson."
    )
    objectives: list[str] = Field(
        description="2-4 learning objectives, each starting with a verb."
    )

    din: DinTask | None = None
    key_spellings: list[Spelling] | None = None
    keyword_bank: list[Keyword] | None = None
    i_do: list[ModelStep] | None = None
    misconceptions: list[Misconception] | None = None
    we_do: list[Task] | None = None
    you_do: YouDo | None = None
    exam_language_coach: list[CoachPoint] | None = None
    exam_questions: list[ExamQuestion] | None = None
    exit_ticket: list[str] | None = Field(
        default=None, description="~3 short check-for-understanding questions."
    )
    exit_ticket_answers: list[str] | None = Field(
        default=None,
        description="Answers to exit_ticket, SAME ORDER and SAME LENGTH. ONLY when "
        "the teacher requested an answer key — powers the reveal slide after the "
        "exit ticket. A parallel list so exit_ticket itself stays plain strings.",
    )
    feedback: Feedback | None = None
    revision_resources: list[RevisionResource] | None = Field(
        default=None,
        description="Free websites for home revision — the PowerPoint's closing "
        "slide, used INSTEAD of feedback (which needs a book, not a projector).",
    )


# ── Overview (kept: the teacher-facing at-a-glance digest) ────────────────────


class Overview(BaseModel):
    """The 'at a glance' digest shown above the resource so a teacher can size it
    up in seconds."""

    summary: str = Field(
        description="One or two sentences: what this resource covers and how it is structured."
    )
    objectives: list[str] = Field(
        description="3-5 concise, verb-led learning objectives for the whole resource."
    )
    assessment_focus: str = Field(
        description="How it maps to assessment: difficulty progression and mark-scheme / "
        "assessment-objective conventions it reflects."
    )


# ── Output schema (matches the frontend GeneratedContent) ────────────────────


class AnswerSection(BaseModel):
    """A teacher answer-key section — only generated when the teacher requests it."""

    heading: str
    markdown_body: str = Field(
        description="Worked solutions / mark scheme, in Markdown (may contain LaTeX)."
    )


class GeneratedContent(BaseModel):
    title: str = Field(
        description="A clear, specific, plain-text title for the resource."
    )
    task_type: TaskTypeLiteral
    subject: str
    year_group: str
    exam_board: str | None = Field(
        default=None, description="Exam board for GCSE resources (else null)."
    )
    spec_reference: str | None = Field(
        default=None,
        description="Specification reference where known, e.g. 'AQA GCSE Biology 8461 · B7 Ecology'.",
    )
    audience_note: str = Field(
        description="One-line audience summary, e.g. 'Year 10 · Biology · Higher · AQA'."
    )
    overview: Overview = Field(description="The at-a-glance digest of the resource.")
    lessons: list[Lesson] = Field(
        description="The lesson(s): one for a worksheet or PowerPoint, several for a booklet."
    )
    teacher_answer_key: list[AnswerSection] | None = Field(
        default=None,
        description="Worked solutions & mark allocations — generated ONLY when requested.",
    )
    difficulty_note: str = Field(
        description="Plain-text note on how the content was pitched for the ability level."
    )

    @model_validator(mode="before")
    @classmethod
    def _bound_string_fields(cls, data: object) -> object:
        """Reject an oversized string ANYWHERE in the posted content before the
        renderers touch it. /export takes this whole object from the client, so a
        crafted 1 MB field would otherwise reach python-docx and 500. Applies to
        every field, present and future — including the nested lesson bodies."""
        if isinstance(data, dict):
            _reject_giant_strings(data)
        return data


# API response = the generated content plus where it came from. Kept separate so
# `source` is never part of the schema sent to Gemini — the server sets it.
class GenerateResponse(GeneratedContent):
    source: Literal["gemini", "cache", "fallback"] = "gemini"
