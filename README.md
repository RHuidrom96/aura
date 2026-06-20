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
cd mt_eval_v2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Required environment variables
export MT_EVAL_ADMIN_EMAIL=youremail@example.com
export MT_EVAL_ADMIN_PASSWORD=a-strong-password
export MT_EVAL_SECRET_KEY=some-long-random-string


python app.py            # serves on PORT or default 8000
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
- `results_summary.csv` — key tables (per-criterion means, win rates, span F1, post-edit, difficulty)
- `results.json` — the full computed results
- `results_report.html` — a self-contained HTML report
- `figures/*.png` — every chart

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

## Segment file format

A JSON array of objects:

```json
[
  {
    "id": "seg_001",
    "source": "Source sentence text.",
    "target": "Machine translation text.",
    "reference": "Optional human reference translation.",
    "system": "SystemA",
    "domain": "Medical"
  },
  ...
]
```

Required fields: `id` (unique within the file), `source`, `target`.
Optional: `reference`, `system`, `domain`.

## CSV output columns

| Column | Description |
|---|---|
| `response_id` | UUID for this rating |
| `timestamp_utc` | Last update time |
| `annotator_name`, `annotator_email`, `annotator_native_lang` | Annotator info |
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
- **Segments per page** — how many segments per page before "Save & next page" (1–50).
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

On a campaign's detail page there's a **Delete campaign** card. To prevent accidents, the admin must type the exact campaign name to confirm. Deleting removes the campaign, all its ratings (local DB).

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

| Task type | Input label | Output label | Example criteria |
|---|---|---|---|
| Machine translation | Source | Translation | Adequacy, Fluency |
| Summarization | Document | Summary | Coherence, Consistency, Fluency, Relevance |
| Text simplification | Original text | Simplified text | Meaning preservation, Simplicity, Fluency |
| Dialogue / response | Conversation | Response | Helpfulness, Coherence, Safety |
| Question answering | Question | Answer | Correctness, Completeness, Fluency |
| General LLM output | Input | Output | Overall quality |
| Custom | (your own) | (your own) | (your own) |

Choosing a task type on the campaign form pre-fills the input/output labels and example
criteria (all editable), and makes the **Source/Target language** fields optional for
monolingual tasks. Machine-translation campaigns are unchanged and remain the default, so
existing campaigns keep working exactly as before. The labels flow through the annotator
screen, the AI assistant, and the segment context.

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
