# Aura 💫

A multi-campaign platform for human evaluation of machine translation. An admin
creates campaigns, uploads segment files, and shares per-campaign links.
Annotators register a global account once and can join any campaign. Each
annotation can be edited until the admin closes the campaign.

## Features

- **Admin role** (single account, configured via env vars) with a dashboard, campaign creation form, per-campaign progress view, master CSV download, and campaign close action.
- **Annotator role** with global account (one email + password works across all campaigns).
- **Three Likert criteria**: adequacy, fluency, meaning preservation.
- **Error-span marking** on the translation text — three overlapping error types, colored highlights, free-form cursor selection.
- **Persistent on-screen instructions** plus per-criterion guidance.
- **Optional reference translation** behind a disclosure.
- **Resumable** — annotators can come back, jump to any segment, and edit until the campaign is closed.
- **Storage**: SQLite locally (`data/app.db`). When a campaign is closed, a full results pack (CSV, summary, charts, JSON, HTML report) is emailed to the campaign owner as a ZIP.

## Quick start (local development)

```bash
Steps to follow:

Clone the repo

cd to the folder

maybe, create your virtual env and activate
python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
flask db migrate -m "Initial schema"   # only if migrations don't exist
flask db upgrade

export RESEND_API_KEY="re_JzsPfnHg_FemG4UCEqeCVMyE5DwZWPwgu"
export MT_EVAL_EMAIL_FROM="Aura <noreply@aura-a.site>"
python -m flask --app app test-email ADD-YOUR-EMAIL@gmail.com
python -m flask --app app run

to test
```

Open <http://localhost:8000> and click **Admin sign in**.

## Production

Run with gunicorn behind a reverse proxy (nginx, Caddy, etc.) terminating TLS:

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

Make sure `data/` is on persistent storage so SQLite, segment files, and the
service-account JSON survive restarts.

## Results delivery (email)

There is no cloud-storage integration to configure. While a campaign is open you can download
the per-rating master CSV anytime from the campaign page. When you **close** a campaign, Aura
builds a results ZIP and emails it to the campaign owner (the admin who created it):

- `ratings.csv` — one row per completed (annotator, segment) rating
- `results_summary.csv` — key tables (per-criterion means, win rates, span F1 + **span density**, post-edit, difficulty, and the **most-contested segments** with readability and comment counts)
- `results.json` — the full computed results
- `results_report.html` — a self-contained HTML report; its header shows the language pair **with input/output scripts** where set (e.g. *English (Latin) → Assamese (Bengali–Assamese)*)
- `figures/*.png` — every chart

### Linguistic diagnosis

The results dashboard and report include a **Linguistic diagnosis** section that goes beyond
"these segments were contested" to help explain *why*. For each of the most-contested
segments it shows a language-agnostic **readability proxy** (0–100, higher = easier — a
heuristic from sentence and word length that flags unusually long or dense items), a preview
of the **annotator comments** left on that segment, and a **link to inspect the segment
directly** (its source/target text and every annotator's scores, the exact error spans they
marked, and their comments). This makes it quick to jump from a disagreement number to the
actual source of confusion.

### Error-span density

Span tables report not just how many spans were marked and how many characters they cover,
but **density**: the mean length of a single span (in characters and words) and the **share
of reviewed text flagged**. This distinguishes a one-word slip from a whole-clause error —
one wrong word and one wrong clause both count as "1 span", but have very different density.
Density is also broken down per system.

Delivery uses whatever email transport you've configured (Resend / SendGrid / SMTP — see
"Getting verification emails to actually send"). If no transport is set, the campaign still
closes and everything remains downloadable from the results page.

## Admin workflow

1. **Sign in** at `/admin/login` with the credentials in your env vars.
2. **New campaign**: set name, source/target languages, optional script (Bengali / Meitei Mayek for Manipuri), and upload segments JSON.
3. **Copy the share link** from the campaign detail page and send it to your annotators (e.g. by email).
4. **Monitor progress**: the detail page shows each annotator with their completion percentage.
5. **Download CSV** at any time — completed ratings only.
6. **Close campaign** when done — irreversible. After this, annotators can't edit.

## Annotator workflow

1. Click the share link from the admin.
2. **Create account** (name, email, password, optional native language) — or sign in if they've worked on another campaign before.
3. Rate each segment on three criteria using a 1–5 scale.
4. Mark error spans in the translation by selecting words with the cursor.
5. Click **Save & next** to advance. Ratings save to the server.
6. The "Jump to…" button opens a modal with all segments and their status, for review and editing.
7. Once finished, the thank-you screen offers a "Review my ratings" button — they can come back to this URL any time until the admin closes the campaign.

## Segment (input) file format

You can upload (or paste) segments as **JSON, JSON Lines (`.jsonl`), CSV, or TSV** — the
format is auto-detected from the file extension or content. All produce the same segments.

JSON array of objects:

```json
[
  {
    "id": "seg_001",
    "source": "Source text (a sentence, document, article, passage, dialogue, …).",
    "target": "Machine translation / summary / answer / etc.",
    "reference": "Optional human reference.",
    "system": "SystemA",
    "domain": "Medical"
  },
  ...
]
```

The same data as CSV (the header names are the field names):

```csv
id,source,target,reference,system,domain
seg_001,Source text,Machine output,Reference,SystemA,Medical
```

Required fields/columns: `id` (unique), `source`, and `target` (for pairwise, `target_a`
and `target_b` instead — or a `candidates` column holding a JSON array or a `|`-delimited
pair). Optional: `reference`, `system`, `domain`. Any extra columns are preserved as
metadata. The **source can be any text** — summarization inputs aren't limited to
"documents"; the input label is just an editable hint.

## CSV output columns

| Column | Description |
|---|---|
| `response_id` | UUID for this rating |
| `timestamp_utc` | Last update time |
| `annotator_id` | Stable UUID of the annotator account (join key across campaigns) |
| `annotator_name`, `annotator_email`, `annotator_native_lang` | Annotator info |
| `annotator_source_fluency`, `annotator_target_fluency` | Annotator's self-rated fluency in the source and target languages (`native`/`fluent`/`advanced`/`intermediate`/`beginner`, or blank for monolingual tasks) |
| `campaign_id`, `campaign_name` | Campaign info |
| `source_language`, `target_language`, `script` | Languages and optional script |
| `segment_id`, `system`, `domain` | Segment metadata |
| `source`, `target`, `reference` | The texts |
| `adequacy`, `fluency`, `meaning_preservation` | The score per criterion (column names follow your criteria). Integer in the campaign's scale range — `1`–`N` for a Likert scale, or `min`–`max` for a continuous slider. |
| `adequacy_spans`, `fluency_spans`, `meaning_preservation_spans` | JSON arrays of `[start, end]` character offsets into `target` |
| `comments` | Free-text comments |
| `time_spent_seconds` | Cumulative time spent on this segment |
| `updated_at_utc` | Last update timestamp |

The exact columns depend on the evaluation mode (an `eval_mode` column is always present):

- **Likert**: as above — one score column per criterion id, plus one `<criterion_id>_spans` column each (when spans are enabled).
- **Pairwise**: instead of `target`, the row carries `candidate_a`, `candidate_b`, `system_a`, `system_b`, `reference`, and the chosen `preference_id` / `preference_label`.
- **Span-only**: one `<criterion_id>_spans` column per error-type category (no score columns), plus a `reviewed` flag.

The weighted score is no longer displayed in the UI but you can compute it from
the three Likert columns after the fact:
`0.35*adequacy + 0.30*fluency + 0.35*meaning_preservation` (adjust weights as desired).

## Files

```
mt_eval_v2/
├── app.py              Flask app + routes
├── auth.py             Session helpers, admin/annotator decorators
├── models.py           SQLAlchemy: Annotator, Campaign, Rating
├── exporter.py         Results CSV + ZIP builder (emailed on close)
├── mailer.py           Email transports (Resend / SendGrid / SMTP)
├── requirements.txt
├── README.md
├── templates/
│   ├── base.html
│   ├── landing.html
│   ├── admin_login.html
│   ├── admin_dashboard.html
│   ├── admin_campaign_new.html
│   ├── admin_campaign_detail.html
│   ├── annotator_login.html
│   ├── campaign_closed.html
│   └── rate.html
├── static/
│   ├── style.css
│   └── rate.js
└── data/
    ├── app.db              (auto-created)
```

## What the admin configures per campaign (v3)

When creating **or editing** a campaign, the admin first picks an **evaluation mode**, then configures the options for that mode.

### Evaluation modes

1. **Likert scale rating** — annotators score each criterion on a scale. This is the original flow and keeps all the scale options below. Span annotation is *optional* here and, when enabled, can be marked in the **target only** or in **both source and target**.
2. **Pairwise preference** — each segment shows **two candidate translations** (A and B); annotators choose which is better from a set of **preference options you define**. No scoring or spans.
3. **Span annotation only** — annotators only mark error spans (no scoring). Your criteria become the **error-type categories**. A segment is complete once the annotator ticks "reviewed" (which also allows zero-error segments to be completed).

### Shared options (all modes)

- **Evaluation criteria** (Likert + Span-only): add/remove criteria, each with a name and a definition. In Likert these are the scored dimensions / CSV score columns; in Span-only they are the error-type categories.
- **Segments per page** — the *default* number of segments shown per page before "Save & next page", plus a **min/max range** (both 1–50). Annotators can pick their own segments-per-page within that range for their convenience; their choice is saved **to their account** (server-side, per campaign), so it follows them across devices and browsers. The default must lie inside the range.
- **Annotation instructions** — free text shown in the collapsible panel on every rating page.
- **Span annotation instructions** (when spans are active) — free text shown in the span section; falls back to a built-in explanation if blank.

### Likert-only scale options

- **Rating scale type**: **Likert** (discrete, 2–11 points) or **Continuous** (slider over an integer range, e.g. Direct Assessment 0–100).
- **Scale appearance**: Likert renders as **numbered circles**, **labelled buttons**, **radio buttons**, or **stars**; continuous renders as a **slider** with endpoint labels.
- **Point / endpoint labels** — optional, shown on the control and in the legend.

### Pairwise-only options

- **Preference options** — the ordered list of choices an annotator picks from (at least two). Defaults to a 5-point comparative scale (A much better … B much better).

### Pairwise segment format

Each segment must provide two candidates. Any of these work:

```json
{"id": "seg_001", "source": "...", "target_a": "...", "target_b": "...",
 "system_a": "SystemX", "system_b": "SystemY", "reference": "..."}
```

or a `candidates` array of two (strings, or `{"target": "...", "system": "..."}` objects):

```json
{"id": "seg_001", "source": "...", "candidates": ["translation A", "translation B"]}
```

`system_a`/`system_b` (or the candidates' `system` fields) are optional and flow through to the CSV. For Likert and Span-only modes, the usual `id` / `source` / `target` format applies.

### Editing a campaign

Every part of the configuration above — including the evaluation mode — can be changed from the campaign's detail page via **Edit configuration**, at any time **until the campaign is closed**. The uploaded segments themselves are fixed after creation, and switching to a mode the existing segments don't support (e.g. switching to Pairwise when segments have only one candidate) is rejected with an explanatory error. Renaming a criterion re-keys its stored scores; editing a criterion's *definition* is always safe. Switching modes or scale bounds after annotation has begun can leave earlier responses outside the new structure — fine for fresh campaigns, something to weigh mid-campaign.

## Deleting a campaign

On a campaign's detail page there's a **Delete campaign** card. Clicking it opens a
centered confirmation dialog (not a browser `confirm()`): you must type the exact campaign
name, and only then does the **Delete permanently** button enable. **Cancel is the
default-focused action**, and Enter in the field never submits — so a stray click or
keypress can't wipe out annotation progress. Closing a campaign uses the same style of
dialog.

## Campaign IDs

Every campaign shows its **ID** (with a one-click copy button) next to its name on the
dashboard, detail, and results pages, and on the annotator's rating screen. This makes it
easy to refer to a specific campaign when you're running several similar ones and need to
coordinate within your team or with annotators.

## Annotator background variables

Alongside native language, expertise and fluency, annotators can optionally provide
**location**, **dialect / variety**, and **age group** (a range, never an exact age) at
registration. These help interpret ratings (e.g. dialectal variation) and are included in
results and the CSV export (`annotator_location`, `annotator_dialect`,
`annotator_age_group`). An admin can view and **edit** any annotator's profile from the
progress table on the campaign page (the "edit" link on each row).

## Grouping campaigns

When you split one study across several campaigns — for example one campaign per difficulty
level (easy / medium / hard), or one per system — you can bundle them into a **group** to see
and export their results together.

- **Create a group** from the admin dashboard (the *Groups* panel) or directly from a
  campaign's detail page (the *Group* card).
- **Add campaigns** to a group from each campaign's detail page. A campaign belongs to at
  most one group; adding it elsewhere just moves it.
- **Combined results** (`Groups → View combined results`) show: overall counts (segments,
  distinct annotators de-duplicated by account, completed ratings), a per-campaign summary
  table with each campaign's headline metric, **pooled Likert means** per criterion (an
  n-weighted mean across the group's Likert campaigns, matched by criterion name), and a
  **combined difficulty breakdown** (segments/ratings summed per level, the metric pooled as
  an n-weighted mean).
- **Combined CSV** — one download with every completed rating from all campaigns in the
  group. Because campaigns can use different modes/criteria, the file uses the union of all
  columns; every row still carries its `campaign_id`, `campaign_name`, `eval_mode`,
  `annotator_id`, and `response_id`, so rows are never ambiguous.
- Inter-annotator agreement (α/κ/F1) is **not** pooled across campaigns — segments differ
  between them — so it stays on each campaign's own dashboard.
- **Deleting a group never deletes its campaigns**: they are simply un-grouped.

## CSV columns are dynamic

Because criteria are now per-campaign, the CSV columns adapt: one column per criterion id (the score), plus one `<criterion_id>_spans` column each. Span cells are JSON arrays; in target-only mode items are `[start, end]`, in both-mode they are `[start, end, "target"|"source"]`.

## Security notes

- Set `MT_EVAL_SECRET_KEY` to a long random string in production. Without it, sessions are not secure.
- Use HTTPS in production. Flask cookie sessions are signed but not encrypted.
- Annotator passwords are bcrypt-hashed.
- The admin password is read from `MT_EVAL_ADMIN_PASSWORD` — store it via your platform's secrets manager, not in source control.
- Set a sensible `MAX_CONTENT_LENGTH` (default 16 MB) for the segment file upload.

## AI guideline assistant (optional)

Each campaign can enable an **AI guideline assistant** that helps annotators understand the guidelines without ever assigning scores or labels. It is configured by the admin under "AI guideline assistant" in the campaign form.

- **Providers**: Ollama (local, free — the default), Anthropic (Claude), OpenAI, Google Gemini, and any OpenAI-compatible endpoint. The admin picks a provider, a model, and (for hosted providers) an API key. Keys are stored **encrypted at rest** and never sent to annotators or shown again (use "Test connection" to verify).
- **Resolution order**: per-campaign config first, then an instance-wide default from environment variables (`MT_EVAL_AI_PROVIDER`, `MT_EVAL_AI_MODEL`, `MT_EVAL_AI_BASE_URL`, `MT_EVAL_AI_API_KEY`). If neither is configured the assistant is simply off.
- **Floating & always available**: on the rating screen the assistant is a floating launcher (bottom-right) that opens a chat-style panel. It helps with **understanding the instructions, the criteria and their definitions, how the rating interface works, and the segment currently in view** — not just spans.
- **Grounded & advisory**: answers are grounded in the campaign's own instructions, criteria, span guidance, an auto-generated description of the interface, and (optionally) the current segment. It is instructed to cite the relevant criterion/instruction or say it can't find guidance — never to state a score/label or call a translation correct/incorrect.
- **Logged**: every interaction (provider, model, question, answer) and the annotator's feedback (helpful / made me reconsider / no) is stored in `assistant_logs` for provenance and for measuring AI influence on annotation.

Local Ollama needs a running server (default endpoint `http://localhost:11434/v1`); no key required. Hosted providers require the relevant Python access only through outbound HTTPS — no extra SDKs are bundled (calls are plain REST).

## Results export

The results dashboard (and the downloadable HTML report) support:
- **Interactive charts** — bar charts on the dashboard are rendered with a vendored copy of Chart.js (works offline) with hover tooltips and toggleable legends; heatmaps remain static images. The downloadable report uses static (matplotlib) images so it stays fully self-contained.
- **Per-figure download** — every chart has a "⬇ PNG" link.
- **Per-table copy/export** — each table has a toolbar to copy it as **Markdown**, **LaTeX** (`table`/`tabular`, with α/κ escaped), or **Word** (rich HTML that pastes as a real table into Word/Google Docs), or to download **CSV**.
- **Whole-report download** — a self-contained HTML report (charts embedded, copy/export buttons inlined so it works offline) and the raw stats as JSON.


### Span agreement variants
Span agreement is reported as character-level F1 and token-level (whole-word) F1; the token variant ignores off-by-a-character boundary differences.


## Evaluation modes

Aura supports four annotation modes, all of which work across every task type:

- **Likert scale rating** — score each criterion on a scale (optionally also mark error spans).
- **Pairwise preference** — compare two candidate outputs (A vs B) with preference options you define.
- **Span annotation (MQM)** — mark error spans and assign error types (your criteria become the categories); reports character- and token-level inter-annotator F1.
- **Post-editing** — annotators correct the output by editing it directly. Results report how much editing each system needed (share of outputs changed, mean normalised edit distance 0–1, per system), plus agreement on whether a segment needed editing. Less editing = better output.

A **Factuality / hallucination** task preset is also included, with ready-made error categories
(Unsupported/hallucination, Contradicts source, Misattribution, Incorrect fact, Fabricated detail)
for span-annotating LLM or summary outputs. Standard criterion definitions are built in and are
used both to pre-fill the campaign form and as the AI assistant's fallback guidance.

## Beyond machine translation (task types)

The three evaluation modes (Likert scoring, pairwise preference, span/error annotation) are not
specific to translation. Each campaign has a **Task type** that relabels the interface and
suggests criteria, so the same machinery serves other text-evaluation tasks:

| Task type | Input label | Output label | Example criteria | Languages |
|---|---|---|---|---|
| Machine translation | Source | Translation | Adequacy, Fluency | **Two (required)** — source ≠ target |
| Summarization | Document | Summary | Coherence, Consistency, Fluency, Relevance | One, or two (cross-lingual summarization) |
| Question answering | Question | Answer | Correctness, Completeness, Fluency | One, or two (cross-lingual QA) |
| Text simplification | Original text | Simplified text | Meaning preservation, Simplicity, Fluency | **One only** |
| Dialogue / response | Conversation | Response | Helpfulness, Coherence, Safety | **One only** |
| Factuality / hallucination | Source / context | Output | (error categories) | One, or two |
| General LLM output | Input | Output | Overall quality | One, or two |
| Custom | (your own) | (your own) | (your own) | One, or two |

Choosing a task type on the campaign form pre-fills the input/output labels and example
criteria (all editable), and makes the **Source/Target language** fields optional for
monolingual tasks. Machine-translation campaigns are unchanged and remain the default, so
existing campaigns keep working exactly as before. The labels flow through the annotator
screen, the AI assistant, and the segment context.

### Cross-lingual tasks and annotator fluency

Some tasks are inherently **bilingual** (machine translation: the source and target are in
different languages and both matter). Others are **usually monolingual but can be
cross-lingual** (summarizing an English document into Hindi, or answering an English
question from a Bengali passage) — for these, Aura treats the campaign as cross-lingual only
when you actually set two *different* source and target languages. The rest
(**text simplification**, **dialogue / response**) are **strictly single-language**.

For any campaign that ends up cross-lingual, annotators are asked at registration to rate
their **fluency in both the source and target language** (native / fluent / advanced /
intermediate / beginner). These are stored on the annotator account and exported as
`annotator_source_fluency` / `annotator_target_fluency`, so you can filter or weight results
by how well each rater knew each language. Monolingual campaigns don't show these fields.

The demo loader includes two non-MT examples — **Summarization quality (English)** and
**LLM response comparison (English)** — alongside the nine Northeast India language campaigns.

## Demo / pilot data (Northeast India languages)

Load a ready-made pilot with one click — **"Load demo campaigns"** on the admin dashboard, or
`flask seed-demo` (with `FLASK_APP=app`). This creates **nine** small campaigns (6 segments each)
spanning every evaluation mode and nine Northeast India languages, **three demo annotators**, and
**synthetic, partially-agreeing ratings**, so every results dashboard is populated out of the box.

| Language | Mode |
|---|---|
| Assamese | Likert (Direct Assessment) |
| Manipuri (Meitei) | Pairwise |
| Mizo | Span / MQM error annotation |
| Bodo | Likert + error spans |
| Khasi | Likert |
| Nyishi | Span / MQM |
| Kokborok | Pairwise |
| Nagamese | Likert |
| Garo | Likert + error spans |

**Editable data, no code changes.** Everything lives in the `demo_data/` directory:
`campaigns.json` (the manifest of campaigns + demo annotators) and `segments/*.csv` (one file
per campaign). Edit those — or point a campaign's `segments_file` at your own CSV/JSON — and
re-run the loader to pilot with your real system outputs. See `demo_data/README.md` for the
column formats. Loading is idempotent (existing demos are left untouched).

**Sample text disclaimer.** The bundled target strings are illustrative example MT outputs to
demonstrate the interface and populate the dashboards — not validated references. Replace them
before collecting real judgments. The demo annotators (logins in `campaigns.json`, default
password `demopass123`) and their synthetic ratings should also be removed for a real pilot.

## Admin accounts & sign-up

The built-in admin (env vars `MT_EVAL_ADMIN_EMAIL` / `MT_EVAL_ADMIN_PASSWORD`) always works. Additional
admins can self-register from the home page ("Create admin account"), verifying their email with a
6-digit one-time code.

**Email (OTP) delivery** — set SMTP to actually send codes; otherwise the code is written to the
server log:
- `MT_EVAL_SMTP_HOST`, `MT_EVAL_SMTP_PORT` (default 587)
- `MT_EVAL_SMTP_USER`, `MT_EVAL_SMTP_PASSWORD`
- `MT_EVAL_SMTP_FROM` (default: the SMTP user), `MT_EVAL_SMTP_USE_TLS` (default `1`)
- `MT_EVAL_OTP_DEV_ECHO=1` — show the code in the UI (local testing only; do not use in production).

**Locking down sign-up** — by default anyone who can receive email at the address they enter can
register. Restrict it with any combination of:
- `MT_EVAL_ADMIN_SIGNUP_ENABLED=0` — disable self-registration entirely (only the env admin and
  already-registered admins can sign in).
- `MT_EVAL_ADMIN_INVITE_CODES="code1,code2"` — require a valid invite code to register.
- `MT_EVAL_ADMIN_EMAIL_DOMAINS="iitb.ac.in,example.org"` — only allow these email domains to register.

These combine: e.g. set both an invite code and a domain allow-list to require both.

### Getting verification emails to actually send

The OTP code only reaches an inbox when an email transport is configured; otherwise it is written
to the server log. Aura uses the first configured transport, in this order. **HTTP email APIs are
recommended for hosted apps** because many platforms block outbound SMTP ports.

1. **Resend** (simplest): set `RESEND_API_KEY` and `MT_EVAL_EMAIL_FROM` (e.g. `Aura <noreply@yourdomain.com>`; a verified domain or Resend's `onboarding@resend.dev` for testing).
2. **SendGrid**: set `SENDGRID_API_KEY` and `MT_EVAL_EMAIL_FROM`.
3. **SMTP**: set `MT_EVAL_SMTP_HOST`, `MT_EVAL_SMTP_USER`, `MT_EVAL_SMTP_PASSWORD` (and `_PORT`, `_FROM`, `_USE_TLS` as needed). For Gmail, create an App Password and use host `smtp.gmail.com`, port `587`.

Verify your setup without going through sign-up:

```
FLASK_APP=app flask test-email you@youraddress.com
```

It prints the active transport and whether the send succeeded (with the provider's error if not).
