# Question Bank — Authoring Layout

This directory is the authoring source-of-truth for the question bank. The
seed loader (`src/seed/seed_index.py`) reads files from here (or from the
mirrored Blob container in production) and upserts them into the AI Search
`questions` index.

## Directory shape

```
src/seed/questions/
  <language>/<topic>/<logical_id>.json
```

* `<language>` is an ISO 639-1 code from the allowlist (`en`, `fr`, `es` in
  v1; see `SUPPORTED_LANGUAGES` in `src/data/models.py` and SEC-010).
* `<topic>` is the topic catalog ID, matching a row in the Cosmos `topics`
  container (003 TASK-043).
* `<logical_id>` is the language-independent question identifier
  (e.g. `az-net-vpn-001`). The AI Search document key is built as
  `f"{logical_id}-{language}"` per NFR-011.

## One file per `(logical_id, language)` pair

> **The schema validator rejects merged files.** Do not author a single JSON
> file containing all three translations.

This is the load-bearing rule for NFR-011 (per-language analyzers operate
on per-language records) and 008-api §3.2. The seed loader walks the tree
and treats each file as a single `(logical_id, language)` row.

## File schema

```json
{
  "logical_id": "az-net-vpn-001",
  "topic": "azure-networking",
  "language": "en",
  "text": "Which Azure service provides a site-to-site encrypted VPN connection to a virtual network?",
  "options": [
    {"key": "A", "text": "Application Gateway"},
    {"key": "B", "text": "VPN Gateway"},
    {"key": "C", "text": "Azure Firewall"},
    {"key": "D", "text": "Front Door"}
  ],
  "correct_answer": ["B"],
  "difficulty": "medium",
  "tags": ["vpn", "ipsec"],
  "category": "networking",
  "explanation": "VPN Gateway provides encrypted site-to-site (S2S) and point-to-site (P2S) connectivity into an Azure VNet over IPsec/IKE.",
  "score_weight": 1.0
}
```

Validated by `Question` in `src/data/models.py`. Validation failures abort
the seed loader before any index write — authoring drift surfaces here, not
in production.

## Translation discipline

Each logical question has three files (`en`, `fr`, `es`). Translations are
**not** machine-translated — they are authored or reviewed by a competent
speaker so the difficulty and ambiguity are preserved across languages.
Per-language Foundry Evaluations (009 TASK-167 / TEST-011) gate index
publishes — if a translation drifts in difficulty or ambiguity, the gate
catches it.

## Authoring conventions

* `logical_id` — lowercase kebab-case, prefixed by topic shorthand
  (`az-net-`, `az-stg-`, `az-sec-`) and a stable per-question suffix.
  Adding a new translation reuses the existing `logical_id`.
* `text` — TTS-friendly per NFR-014: sentence-length, no markdown, no
  code blocks; expand acronyms on first use within the question.
* `options[].text` — short, distinct, no leading "A)" / "B)" framing
  (the tool layer adds option framing — GOV-050).
* `correct_answer` — array of `OptionKey` (single uppercase letter). For
  v1 we author single-correct questions; multi-correct is supported by
  the grader (008-api §1.6.4) but is rare.
* `difficulty` — one of `easy`, `medium`, `hard`. Aim for a balanced mix
  per topic so per-language difficulty parity tests have signal.
* `tags` — short keywords for the synonyms map / facets; lowercase.
* `category` — broader bucket than `topic` (e.g. `networking`,
  `storage`, `security`); used for cross-topic reporting.
* `explanation` — one or two sentences, factual. Surfaced by the tool
  layer only after `submit_answer` per GOV-031.

## Loading

```bash
# From repo root, with az login already done:
python -m src.seed.seed_index --confirm
```

The loader is idempotent: a re-run over an unchanged tree is a no-op (zero
upserts, zero deletes). See `src/seed/seed_index.py` and 002 TASK-026 /
TASK-028.

## Layout in Blob Storage (production)

In production, this tree is mirrored to the `questions/` container in the
project's Storage account (provisioned by 001 TASK-006). The
`uami-indexer-*` identity has `Storage Blob Data Reader` on the account and
walks the same `<lang>/<topic>/<logical_id>.json` shape. Source-of-truth
remains this repo; Blob is the runtime mirror.
