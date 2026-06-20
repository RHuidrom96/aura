# Demo data (Northeast India languages)

These files drive the **Load demo campaigns** button (and `flask seed-demo`). Edit them to
pilot with your own data — no code changes needed.

## Files
- `campaigns.json` — the manifest: the list of demo annotators and the campaigns. Each campaign
  entry sets its languages, evaluation mode, scale, criteria (or error types), pairwise
  preference options, instructions, and which segments file to load.
- `segments/*.csv` — one file per campaign. Columns depend on the mode:
  - Likert / span / MQM: `id, source, target, system, domain`
  - Pairwise: `id, source, target_a, target_b, system_a, system_b, domain`
  You can also use a `.json` segments file (a list of segment objects) instead of CSV.
- (Optional) a `reference` column is supported for any non-pairwise campaign.

## Replacing with your own data
1. Edit the relevant `segments/*.csv` (or point `segments_file` at your own CSV/JSON).
2. Adjust the campaign entry in `campaigns.json` (languages, criteria, instructions, scale).
3. Re-run **Load demo campaigns** / `flask seed-demo`. Loading is idempotent: a campaign is only
   created if one with the same name doesn't already exist, so to reload an edited campaign,
   delete it first in the admin UI (or rename it in the manifest).

## Note on the sample text
The bundled target-language strings are **illustrative sample MT outputs** to demonstrate the
interface and populate the dashboards — they are not validated reference translations. Replace
them with real system outputs before collecting real judgments.

## Demo annotators
Three demo annotators are created with synthetic, partially-agreeing ratings so the results
dashboards are populated out of the box. Their logins are listed in `campaigns.json`
(default password `demopass123`). Remove or change them for a real pilot.

## Difficulty labels

Any non-pairwise (or pairwise) segments file may include a `difficulty` column with values `easy`, `medium`, or `hard` (synonyms like low/med/high or 1/2/3 also work). When present, results are broken down by these labels. When absent, Aura auto-classifies each segment by input length so you still get an easy/medium/hard breakdown. The Assamese demo campaign ships with explicit labels as an example.
