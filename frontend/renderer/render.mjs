// Server-side renderer for the PDF export engine (Fulwood lesson format).
// Reads a GeneratedContent JSON on stdin, emits a print-ready, ENGAGING HTML
// document on stdout, using the SAME markdown + KaTeX pipeline as the on-screen
// preview so the PDF matches what the teacher saw. Chromium (Playwright) turns
// this HTML into the PDF — see backend/app/services/export.py.
//
// Design goal: keep every ounce of the school's pedagogical rigour, but replace
// the source booklets' wall-of-tables with clear hierarchy, colour-coded section
// stamps, and room to breathe — something a student actually wants to read.
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkRehype from "remark-rehype";
import rehypeKatex from "rehype-katex";
import rehypeStringify from "rehype-stringify";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
// pathToFileURL, not a hand-built `file://` + path. On Windows resolve() returns
// `C:\…\katex.min.css`, and `file://C:\…` parses `C:` as the HOSTNAME — the
// stylesheet silently never loads and every equation renders unstyled. This is the
// project's recurring failure shape: it still produces a PDF, just a wrong one.
// pathToFileURL emits `file:///C:/…` on Windows and `file:///…` on POSIX.
const katexCss = pathToFileURL(
  resolve(here, "./node_modules/katex/dist/katex.min.css"),
).href;

const pipeline = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkMath)
  .use(remarkRehype)
  .use(rehypeKatex, { throwOnError: false, trust: false })
  .use(rehypeStringify);

const MATH_SEGMENT_RE = /(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\))/g;
const EMOJI_RE = /[\u{1F300}-\u{1FAFF}\u2600-\u27BF]|\uFE0F/gu;

const stripEmojis = (s = "") => String(s ?? "").replace(EMOJI_RE, "");

// Sanitize markdown before parsing: preserve `<` and `>` inside math delimiters
// (`$a < b$`, `$$x > 0$$`) for KaTeX, but escape all `<` and `>` outside math
// everywhere on a line so inline `<script>`, `<iframe`, `<img onerror=...>` can
// never survive remarkParse -> remarkRehype (`REQ-SEC-01` / `VULN-01`).
const sanitizeMarkdownForRender = (raw = "") => {
  const clean = stripEmojis(raw);
  const parts = clean.split(MATH_SEGMENT_RE);
  return parts
    .map((part, idx) =>
      idx % 2 === 1
        ? part
        : part.replace(/</g, "&lt;").replace(/>/g, "&gt;"),
    )
    .join("");
};

const md = (s) =>
  String(pipeline.processSync(sanitizeMarkdownForRender(s ?? "")))
    .replace(/&#x3C;/gi, "&lt;")
    .replace(/&#x3E;/gi, "&gt;")
    .trim();
// Inline markdown: render, then drop a single wrapping <p> so short content (an
// objective, a success criterion, a definition) can carry LaTeX maths without
// turning into a block. Multi-paragraph content is left as-is.
const mdi = (s) => {
  const h = md(s);
  const m = h.match(/^<p>([\s\S]*)<\/p>$/);
  return m && !m[1].includes("<p>") ? m[1] : h;
};
// Escapes quotes as well as &<>. Today every esc() call site is element TEXT, where quotes
// are harmless — the only attribute in this file interpolating a variable is the KaTeX
// stylesheet href, which is a local path we build ourselves. So this is not fixing a live
// bug. It is removing a footgun: the day someone writes href="${esc(r.url)}" with a
// model-supplied URL, a bare " would break out of the attribute, and nothing in review
// would flag it because esc() *looks* like it escapes for HTML. Make the helper safe for
// the context people will assume it is safe for.
const esc = (s = "") =>
  stripEmojis(s).replace(/[&<>"']/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
// N ruled answer lines for hand-writing.
const lines = (n = 2) => `<div class="lines">${'<span></span>'.repeat(n)}</div>`;

// ── A section "card": coloured stamp + label + body ──────────────────────────
const card = (kind, stamp, label, body, meta = "") => `
<section class="card ${kind}">
  <div class="card-head"><span class="stamp">${esc(stamp)}</span><span class="label">${esc(label)}</span>${meta ? `<span class="meta">${meta}</span>` : ""}</div>
  <div class="card-body">${body}</div>
</section>`;

// ── Per-part renderers ───────────────────────────────────────────────────────

function din(d) {
  const q = d.questions
    .map((it) => {
      let inner = `<div class="q-prompt">${md(it.prompt_md)}</div>`;
      if (it.type === "mcq" && it.options) {
        inner += `<ol class="opts">${it.options.map((o) => `<li>${md(o)}</li>`).join("")}</ol>`;
      } else if (it.type === "true_false") {
        inner += `<div class="tf">True&nbsp;/&nbsp;False</div>`;
      } else {
        inner += lines(1);
      }
      return `<li class="q"><span class="qn">${it.number}</span><div class="q-main">${inner}</div></li>`;
    })
    .join("");
  // mdi, not esc: the model puts LaTeX in short "label" fields too (seen live:
  // an i_do label "Differentiating $y = ax^n$"), and esc() renders it literally.
  const meta = `<span class="chip">Recall: ${mdi(d.recall_focus)}</span>`;
  return card(
    "din", "[DIN]", "Do It Now",
    `<p class="instruction">${esc(d.instruction)}</p><ol class="qs">${q}</ol><p class="score">Score: <span class="blank">__</span> / ${d.questions.length}</p>`,
    meta,
  );
}

function spellings(list) {
  const body = `<div class="spell-grid">${list
    .map(
      (s) => `<div class="spell">
        <div class="spell-word">${esc(s.word)}</div>
        <div class="spell-phon">${esc(s.phonetic)}</div>
        <div class="spell-mean">${mdi(s.meaning)}</div>
        ${s.dont_confuse ? `<div class="spell-warn">Careful: ${mdi(s.dont_confuse)}</div>` : ""}
        <div class="spell-practice">Practice: ${lines(2)}</div>
      </div>`,
    )
    .join("")}</div>`;
  // Count from the data — the prompt asks for 3 but nothing enforces it.
  return card("spell", "[SPELL]", `${list.length} Key Spelling${list.length === 1 ? "" : "s"}`, body);
}

function keywords(list, hasBoard) {
  const rows = list
    .map(
      (k) => `<tr><td class="kw-term">${mdi(k.term)} <span class="tier tier-${k.tier}">Tier ${k.tier}</span></td><td>${md(k.definition)}</td></tr>`,
    )
    .join("");
  // "(mark-scheme wording)" only makes sense at GCSE (a board is set); KS3 has no
  // mark scheme, so it reads as the GCSE template leaking in (QA A4.1).
  const defHeader = hasBoard ? "Definition (mark-scheme wording)" : "Definition";
  return card("kw", "[KW]", "Keyword Bank", `<table class="kw"><thead><tr><th>Key term</th><th>${defHeader}</th></tr></thead><tbody>${rows}</tbody></table>`);
}

function iDo(steps) {
  const body = steps
    .map(
      (s) => `<div class="step">
        <div class="step-label">${mdi(s.label)}</div>
        <div class="step-body">${md(s.body_md)}</div>
        ${s.examiner_tip ? `<div class="tip"><strong>Examiner tip:</strong> ${md(s.examiner_tip)}</div>` : ""}
      </div>`,
    )
    .join("");
  return card("ido", "[I DO]", "I DO — Watch, listen & annotate", `<div class="ido-head"><span>Worked Example</span><span>Step-by-Step Rationale</span></div>${body}`);
}

function misconceptions(list) {
  const body = list
    .map(
      (m) => `<div class="miscon">
        <div class="mis-wrong"><span>[X]</span> ${md(m.misconception)}</div>
        <div class="mis-right"><span>[OK]</span> ${md(m.correction)}</div>
        ${m.exam_reference ? `<div class="mis-ref">${esc(m.exam_reference)}</div>` : ""}
      </div>`,
    )
    .join("");
  return card("mis", "[CHECK]", "Misconception Check", body);
}

const taskList = (tasks, cls) =>
  `<ol class="tasks ${cls}">${tasks
    .map(
      (t) => `<li>${md(t.prompt_md)}${t.scaffold ? `<div class="scaffold">Hint: ${md(t.scaffold)}</div>` : ""}${lines(2)}</li>`,
    )
    .join("")}</ol>`;

function weDo(tasks) {
  return card("wedo", "[WE DO]", "WE DO — Together", taskList(tasks, "we"));
}

function youDo(y) {
  const sc = y.success_criteria?.length
    ? `<div class="success"><div class="success-head">Success criteria</div><ul>${y.success_criteria
        .map((c) => `<li>${mdi(c)}</li>`)
        .join("")}</ul></div>`
    : "";
  return card("youdo", "[YOU DO]", "YOU DO — Independent work", sc + taskList(y.tasks, "you"));
}

function examCoach(list) {
  const body = list
    .map(
      (c) => `<div class="coach">
        <div class="coach-point">${md(c.point)}</div>
        ${c.allow ? `<div class="allow"><strong>ALLOW:</strong> ${mdi(c.allow)}</div>` : ""}
        ${c.do_not_accept ? `<div class="reject"><strong>DO NOT ACCEPT:</strong> ${mdi(c.do_not_accept)}</div>` : ""}
      </div>`,
    )
    .join("");
  return card("coach", "[COACH]", "Exam Language Coach", body);
}

function examQuestions(list) {
  const body = list
    .map(
      (q) => `<div class="examq"><div class="examq-prompt">${md(q.prompt_md)} <span class="marks">[${q.marks} mark${q.marks === 1 ? "" : "s"}]</span></div>${lines(3)}</div>`,
    )
    .join("");
  return card("examq", "[EXAM]", "Exam Questions", body);
}

function exitTicket(list) {
  const body = `<ol class="exit">${list.map((q) => `<li>${md(q)}${lines(1)}</li>`).join("")}</ol>`;
  return card("exit", "[EXIT]", "Exit Ticket", body);
}

function feedback(f) {
  const body = `${f.even_better_if ? `<div class="ebi"><strong>Even Better If:</strong> ${md(f.even_better_if)}</div>` : ""}
    <div class="improve"><strong>My improvement (green pen):</strong> ${mdi(f.improvement_prompt)}${lines(3)}</div>`;
  return card("fb", "[EBI]", "Feedback & Improvement", body);
}

// Free home-revision sites. Only present on a PowerPoint (its closing slide), so
// on the PDF handout this simply doesn't render for a worksheet/booklet.
function revision(list) {
  const body = list
    .map(
      (r) => `<div class="revision">
        <div class="rev-name">${esc(r.name)}</div>
        <div class="rev-url">${esc(r.url)}</div>
        <div class="rev-purpose">${mdi(r.purpose)}</div>
      </div>`,
    )
    .join("");
  return card("rev", "[REV]", "Revision Material — study at home", body);
}

// `showObjectives` is false for a single-lesson resource: its objectives just
// restate the "At a glance" set a few centimetres above, which cost a third of
// page 1. A booklet's lessons each get their own.
function renderLesson(l, idx, hasBoard, showObjectives = true) {
  const parts = [];
  parts.push(`<header class="lesson-head">
    <div class="lesson-title">${mdi(l.title)}</div>
    <div class="lesson-focus">${mdi(l.lesson_focus)}</div>
    ${showObjectives && l.objectives?.length ? `<ul class="objectives">${l.objectives.map((o) => `<li>${mdi(o)}</li>`).join("")}</ul>` : ""}
  </header>`);
  if (l.din) parts.push(din(l.din));
  if (l.key_spellings?.length) parts.push(spellings(l.key_spellings));
  if (l.keyword_bank?.length) parts.push(keywords(l.keyword_bank, hasBoard));
  if (l.i_do?.length) parts.push(iDo(l.i_do));
  if (l.misconceptions?.length) parts.push(misconceptions(l.misconceptions));
  if (l.we_do?.length) parts.push(weDo(l.we_do));
  if (l.you_do) parts.push(youDo(l.you_do));
  if (l.exam_language_coach?.length) parts.push(examCoach(l.exam_language_coach));
  if (l.exam_questions?.length) parts.push(examQuestions(l.exam_questions));
  if (l.exit_ticket?.length) parts.push(exitTicket(l.exit_ticket));
  if (l.feedback) parts.push(feedback(l.feedback));
  if (l.revision_resources?.length) parts.push(revision(l.revision_resources));
  return `<article class="lesson${idx > 0 ? " page-break" : ""}">${parts.join("\n")}</article>`;
}

// ── Print stylesheet ─────────────────────────────────────────────────────────
const CSS = `
@page { size: A4; margin: 14mm 13mm; }
* { box-sizing: border-box; }
:root {
  --navy:#0F4C81; --navy-soft:#E0F2FE; --navy-line:#BAE6FD; --navy-dark:#0B3861;
  --ink:#0F172A; --muted:#64748B; --line:#E2E8F0; --soft:#F8FAFC;
  --din:#0F4C81; --spell:#0F4C81; --kw:#0F4C81; --ido:#0F4C81; --mis:#B91C1C;
  --wedo:#0F4C81; --youdo:#15803D; --coach:#B45309; --examq:#0F4C81; --exit:#334155; --fb:#334155;
  --rev:#B45309; --ans:#15803D;
}
body { font-family:"Plus Jakarta Sans","Segoe UI",system-ui,-apple-system,sans-serif; font-size:10.5pt;
  line-height:1.5; color:var(--ink); margin:0; }
h1,h2,h3,h4 { margin:0; line-height:1.2; }
p { margin:0 0 6pt; }
ul,ol { margin:0 0 6pt; padding-left:18pt; }
li { margin-bottom:4pt; }
strong { font-weight:700; }
table { border-collapse:collapse; width:100%; }

/* Cover / doc header */
.doc-head { background:var(--navy); color:#fff; border-bottom:3pt solid var(--navy-dark);
  padding:14pt 16pt; border-radius:4pt; margin-bottom:12pt; }
.doc-head h1 { font-size:21pt; font-weight:800; letter-spacing:-.01em; }
.doc-head .sub { opacity:.85; font-size:10pt; margin-top:3pt; }
.doc-head .spec { display:inline-block; margin-top:7pt; background:rgba(255,255,255,.16);
  padding:2pt 8pt; border-radius:20pt; font-size:8.5pt; font-weight:600; }

/* At a glance */
.overview { border:1px solid var(--line); border-left:4px solid var(--navy); background:var(--soft);
  border-radius:8pt; padding:9pt 12pt; margin-bottom:12pt; break-inside:avoid; }
.overview .oh { font-size:8pt; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); font-weight:700; }
.overview h2 { font-size:12pt; margin:1pt 0 4pt; }
.overview .lbl { font-weight:700; font-size:9.5pt; margin:6pt 0 2pt; }

.lesson.page-break { break-before:page; }
.lesson-head { margin:10pt 0 8pt; padding-bottom:6pt; border-bottom:2px solid var(--line); }
.lesson-title { font-size:15pt; font-weight:800; letter-spacing:-.01em; }
.lesson-focus { color:var(--muted); font-size:10pt; margin-top:2pt; }
.objectives { margin:5pt 0 0; padding-left:16pt; }
.objectives li { margin-bottom:2pt; }

/* Section cards. Cards may break across a page (overflow visible so nothing is
   clipped at the boundary) — forcing break-inside:avoid was leaving pages ~40-60%
   empty as whole cards jumped to the next page (QA A4.5). Headers stay with their
   body, and the small indivisible units below keep break-inside:avoid. */
.card { border:1px solid var(--line); border-radius:8pt; margin:8pt 0; overflow:visible; }
/* flex-wrap, not a single line: the DIN's meta chip carries recall_focus, whose length
   is model-controlled. Seen live at 100+ chars ("Prior KS4 mathematics — rounding, error
   intervals, evaluating functions, linear graphs, and algebraic rearrangement"), which
   overflowed the band, collided with the label and broke "Do It Now" onto two lines.
   Wrapping lets a long chip take its own row and the header simply grows. */
.card-head { display:flex; align-items:center; gap:7pt; padding:5pt 10pt;
  font-weight:700; font-size:10.5pt; break-after:avoid; flex-wrap:wrap; }
.card-head .label { flex:0 0 auto; }
.spell,.miscon,.step,.coach,.examq,.success,.tip,.ebi{break-inside:avoid;}
.card-head .stamp { font-size:12pt; }
.card-head .meta { margin-left:auto; min-width:0; max-width:100%; }
.card-head .chip { display:inline-block; max-width:100%; background:rgba(255,255,255,.7);
  border-radius:20pt; padding:1pt 8pt; font-size:8pt; font-weight:600; color:var(--ink);
  font-weight:600; line-height:1.35; }
.card-body { padding:8pt 12pt; }
/* section theming: Oxford Navy for teaching phases, semantic red/green/amber elsewhere */
.card{border-color:var(--navy-line);}
.card .card-head{background:var(--navy-soft);color:var(--navy);border-bottom:1.5pt solid var(--navy);}
.card.mis{border-color:#FECACA;} .card.mis .card-head{background:#FEE2E2;color:var(--mis);border-bottom-color:var(--mis);}
.card.youdo,.card.ans{border-color:#BBF7D0;} .card.youdo .card-head,.card.ans .card-head{background:#DCFCE7;color:var(--ans);border-bottom-color:var(--ans);}
.card.coach,.card.rev{border-color:#FDE68A;} .card.coach .card-head,.card.rev .card-head{background:#FEF3C7;color:var(--rev);border-bottom-color:var(--rev);}
.card.exit,.card.fb{border-color:var(--line);} .card.exit .card-head,.card.fb .card-head{background:#F1F5F9;color:var(--ink);border-bottom-color:#CBD5E1;}

/* DIN */
.instruction{font-style:italic;color:var(--muted);font-size:9pt;margin-bottom:5pt;}
.qs{list-style:none;padding:0;margin:0;}
.q{display:flex;gap:8pt;margin-bottom:6pt;break-inside:avoid;}
.qn{flex:0 0 auto;width:16pt;height:16pt;border-radius:50%;background:var(--din);color:#fff;
  font-size:8.5pt;font-weight:700;display:flex;align-items:center;justify-content:center;}
.q-main{flex:1;} .q-prompt p{margin:0;}
.opts{list-style:upper-alpha;columns:2;margin:3pt 0 0;padding-left:16pt;}
.opts li{margin-bottom:1pt;}
.tf{font-weight:600;color:var(--muted);margin-top:2pt;font-size:9.5pt;}
.score{text-align:right;font-weight:700;margin-top:4pt;font-variant-numeric:tabular-nums;} .blank{letter-spacing:1pt;}

/* Spellings */
.spell-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8pt;}
.spell{border:1px solid var(--navy-line);border-radius:4pt;padding:6pt 8pt;background:var(--soft);}
.spell-word{font-weight:800;font-size:11pt;color:var(--spell);}
.spell-phon{font-size:8.5pt;color:var(--muted);letter-spacing:.03em;margin-bottom:3pt;}
.spell-mean{font-size:9pt;} .spell-warn{font-size:8pt;color:var(--mis);margin-top:3pt;}
.spell-practice{font-size:8.5pt;color:var(--muted);margin-top:4pt;}

/* Keyword bank */
table.kw th{background:var(--navy-soft);color:var(--navy);text-align:left;font-size:9pt;padding:4pt 8pt;border:1px solid var(--navy-line);}
table.kw td{padding:4pt 8pt;border:1px solid var(--line);vertical-align:top;font-size:9.5pt;}
.kw-term{font-weight:700;white-space:nowrap;}
.tier{font-size:7pt;font-weight:700;padding:0 5pt;border-radius:10pt;vertical-align:middle;}
.tier-2{background:var(--navy-soft);color:var(--navy);} .tier-3{background:#F1F5F9;color:var(--ink);border:1px solid #CBD5E1;}

/* I DO */
.step{margin-bottom:7pt;} .step-label{font-weight:700;color:var(--ido);font-size:10pt;margin-bottom:2pt;}
.tip{background:#eef2ff;border-left:3px solid var(--ido);padding:4pt 8pt;margin-top:3pt;
  font-size:8.5pt;border-radius:0 4pt 4pt 0;}

/* Misconceptions */
.miscon{margin-bottom:6pt;padding-bottom:6pt;border-bottom:1px dashed #fecaca;}
.miscon:last-child{border-bottom:0;margin-bottom:0;padding-bottom:0;}
.mis-wrong span{color:var(--mis);font-weight:800;} .mis-right span{color:var(--youdo);font-weight:800;}
.mis-wrong,.mis-right{display:flex;gap:5pt;} .mis-wrong p,.mis-right p{margin:0;}
.mis-ref{font-size:8pt;color:var(--muted);font-style:italic;margin-top:2pt;}

/* WE DO / YOU DO */
.tasks{padding-left:18pt;} .tasks li{margin-bottom:6pt;}
.scaffold{color:var(--wedo);font-size:8.5pt;margin:2pt 0;}
.success{background:#ecfdf5;border:1px solid #a7f3d0;border-radius:6pt;padding:5pt 9pt;margin-bottom:6pt;}
.success-head{font-weight:700;color:var(--youdo);font-size:9.5pt;} .success ul{margin:2pt 0 0;}

/* Exam coach */
.coach{margin-bottom:5pt;} .coach-point{font-weight:600;}
.allow{color:var(--youdo);font-size:9pt;} .reject{color:var(--mis);font-size:9pt;}

/* Exam questions */
.examq{margin-bottom:6pt;}
.examq-prompt{display:flex;gap:8pt;justify-content:space-between;align-items:flex-start;}
.examq-prompt>div,.examq-prompt>p{flex:1;}
.marks{margin-left:auto;flex:0 0 auto;border:1px solid var(--navy);color:var(--navy);background:#fff;border-radius:3pt;
  padding:0 5pt;font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums;font-size:9pt;}
.tier-badge{display:inline-block;font-size:7.5pt;font-weight:700;padding:1pt 6pt;border-radius:3pt;margin-right:6pt;text-transform:uppercase;letter-spacing:.05em;}
.tier-foundation{background:#DCFCE7;color:#15803D;} .tier-core{background:var(--navy-soft);color:var(--navy);} .tier-stretch{background:#FEF3C7;color:#B45309;}
.step .tip{grid-column:1/-1;}
.ido-head,.step{display:grid;grid-template-columns:1fr 1fr;gap:10pt;}
.ido-head{font-size:8pt;text-transform:uppercase;letter-spacing:.08em;font-weight:700;color:var(--navy);border-bottom:1.5pt solid var(--navy);padding-bottom:2pt;margin-bottom:4pt;}
.spec-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:0;margin-top:9pt;border:1px solid rgba(255,255,255,.35);border-radius:3pt;}
.spec-grid div{padding:3pt 8pt;font-size:8.5pt;border-right:1px solid rgba(255,255,255,.35);} .spec-grid div:last-child{border-right:0;}
.spec-grid b{display:block;font-size:7pt;text-transform:uppercase;letter-spacing:.08em;opacity:.8;font-weight:600;}

/* Feedback */
.ebi{background:#FEF3C7;border-left:3px solid #B45309;padding:4pt 8pt;border-radius:0 4pt 4pt 0;margin-bottom:5pt;}
.improve{font-size:9.5pt;}

/* Revision material */
.revision{margin-bottom:6pt;}
.rev-name{font-weight:700;color:var(--rev);font-size:10.5pt;}
.rev-url{font-size:9pt;color:#2563eb;word-break:break-all;}
.rev-purpose{font-size:9pt;color:var(--muted);}

/* Answer-writing lines */
.lines{margin-top:4pt;} .lines span{display:block;border-bottom:1px solid var(--line);height:15pt;}

/* Maths */
.katex-display{margin:5pt 0;text-align:left;overflow:visible;}
.katex{font-size:1.05em;}

footer.doc-foot{margin-top:12pt;padding-top:6pt;border-top:1px solid var(--line);
  font-size:8pt;color:var(--muted);font-style:italic;text-align:center;}

/* Teacher answer key */
.answer-key{break-before:page;} .answer-key h2{font-size:14pt;color:var(--mis);}
.answer-key .ak{border:1px solid var(--line);border-radius:6pt;padding:8pt 12pt;margin:6pt 0;}
`;

// ── Read input and emit ──────────────────────────────────────────────────────
let input = "";
for await (const chunk of process.stdin) input += chunk;
const c = JSON.parse(input);

// NB: no process.exit() anywhere below — it truncates async stdout before it
// flushes to the pipe. Letting the module finish naturally flushes the full HTML.
const overview = c.overview
  ? `<div class="overview">
      <div class="oh">At a glance</div>
      <p>${mdi(c.overview.summary)}</p>
      ${c.overview.objectives?.length ? `<div class="lbl">Learning objectives</div><ul>${c.overview.objectives.map((o) => `<li>${mdi(o)}</li>`).join("")}</ul>` : ""}
      <div class="lbl">Difficulty & assessment</div><p>${mdi(c.overview.assessment_focus)}</p>
    </div>`
  : "";

// --answers produces the TEACHER document (mark scheme only); without it the
// student copy is built and carries NO answers at all, so a teacher can print
// and hand it out without first deleting the answer key.
const answersOnly = process.argv.includes("--answers");

// Per-question answers (the reveal data the .pptx uses) collected per lesson,
// so the answer document covers the DIN and exit ticket too — not just the
// lesson-level worked solutions in teacher_answer_key.
const answerSections = (l, i) => {
  const parts = [];
  const din = (l.din?.questions || []).filter((q) => q.answer_md);
  if (din.length)
    parts.push(card("ans", "[ANS]", "Do It Now — answers",
      `<ol class="tasks">${din.map((q) => `<li>${md(q.answer_md)}</li>`).join("")}</ol>`));
  const we = (l.we_do || []).filter((t) => t.worked_answer_md);
  if (we.length)
    parts.push(card("ans", "[ANS]", "We Do — worked solutions",
      `<ol class="tasks">${we.map((t) => `<li>${md(t.worked_answer_md)}</li>`).join("")}</ol>`));
  const tiers = [["foundation", "Foundation"], ["core", "Core"], ["stretch", "Stretch"]]
    .map(([k, label]) => [k, label, l.you_do?.[`${k}_answers`] || []])
    .filter(([, , a]) => a.length);
  if (tiers.length)
    parts.push(card("ans", "[ANS]", "You Do — differentiated answers",
      tiers.map(([k, label, a]) =>
        `<div class="examq"><span class="tier-badge tier-${k}">${label}</span><ol class="tasks">${a.map((x) => `<li>${md(x)}</li>`).join("")}</ol></div>`).join("")));
  const ex = (l.exam_questions || []).filter((q) => q.answer_md);
  if (ex.length)
    parts.push(card("ans", "[ANS]", "Exam questions — mark scheme",
      ex.map((q) => `<div class="examq"><div class="examq-prompt">${md(q.prompt_md)} <span class="marks">[${q.marks} marks]</span></div>${md(q.answer_md)}</div>`).join("")));
  if (l.exit_ticket_answers?.length) {
    // Pair each answer with its question. SAME ORDER / SAME LENGTH is prompt-only, so a
    // plain <ol> over the answers silently marks them against the wrong questions when the
    // two lists disagree. Matches docx_writer's answers() block.
    const qs = l.exit_ticket || [];
    const n = Math.max(qs.length, l.exit_ticket_answers.length);
    parts.push(card("ans", "[ANS]", "Exit ticket — answers",
      Array.from({ length: n }, (_, i) =>
        `<div class="examq"><div class="examq-prompt">${i + 1}. ${md(qs[i] ?? "(no question generated)")}</div>${md(l.exit_ticket_answers[i] ?? "(no answer generated)")}</div>`,
      ).join("")));
  }
  const s = (c.teacher_answer_key || [])[i];
  if (s) parts.push(card("ans", "[ANS]", s.heading, md(s.markdown_body)));
  return parts.length
    ? `<article class="lesson${i > 0 ? " page-break" : ""}">
         <header class="lesson-head"><div class="lesson-title">${mdi(l.title)}</div></header>
         ${parts.join("\n")}</article>`
    : "";
};

const body = answersOnly
  ? (c.lessons || []).map(answerSections).join("\n")
  : `${overview}\n${(c.lessons || [])
      .map((l, i) => renderLesson(l, i, Boolean(c.exam_board), (c.lessons || []).length > 1))
      .join("\n")}`;

const foot = answersOnly
  ? "TEACHER COPY — contains answers. Do not hand to students. · Fulwood Forge"
  : "AI can make mistakes. Always review generated content before using in class. · Fulwood Forge";

// Chromium copies <title> into the PDF's /Title metadata. Without it the exported
// file shows as untitled (or as "doc.html") in readers and VLEs, while the .docx sets
// dc:title correctly — so the two formats disagreed on their own name.
// The title is a "plain text" field by prompt rule, but the model writes LaTeX into it
// anyway (seen live: "Graphs of $y = ax^n$"). The <h1> renders it as maths like every
// other field; /Title metadata is plain text and cannot hold KaTeX, so there we drop
// the delimiters rather than ship "$y = ax^n$" as the file's name.
const titleSuffix = answersOnly ? " — Answer Key" : "";
process.stdout.write(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>${esc(String(c.title ?? "").replace(/\$/g, ""))}${titleSuffix}</title>
<link rel="stylesheet" href="${katexCss}">
<style>${CSS}</style></head><body>
<div class="doc-head">
  <h1>${mdi(c.title)}${titleSuffix}</h1>
  <div class="sub">${answersOnly ? "TEACHER COPY · " : ""}${esc(c.audience_note)}</div>
  ${c.spec_reference ? `<span class="spec">${esc(c.spec_reference)}</span>` : ""}
  <div class="spec-grid">
    <div><b>Subject</b>${esc(c.subject ?? "")}</div>
    <div><b>Year group</b>${esc(c.year_group ?? "")}</div>
    <div><b>Exam board</b>${esc(c.exam_board ?? "General")}</div>
    <div><b>Resource</b>${esc(c.task_type ?? "")}</div>
  </div>
</div>
${body}
<footer class="doc-foot">${foot}</footer>
</body></html>`);
