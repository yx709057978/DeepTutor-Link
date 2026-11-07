# PDF and repair workflows

`CLIENT` means the absolute command documented in `SKILL.md`. Refresh
`CLIENT context` first. Generate UUIDs locally. Read JSON from stdin with
`--file -`; do not put learner answers in shell arguments. Save page images to
the learner-approved temporary directory and open each file before visual claims.

## Restore teaching role and protocol

For a new chat, complete the Guided Start in `SKILL.md`, then run
`CLIENT resume --new-chat`. Inspect
`teaching_protocol.persona`, `mode`, `sections`, `engine_state` and `operations`
before teaching. To choose or clear a style role, use the learner's explicit
choice, then read the protocol again:

```sh
CLIENT persona-list
CLIENT persona-select NAME
CLIENT protocol --new-chat
CLIENT persona-clear
CLIENT protocol --new-chat
```

If the learner asks to restore a website session, first list bounded session
metadata with `CLIENT history-list`; let the learner choose by title. Read only
that session with `CLIENT history-show --session SESSION_ID`. History does not
rewind current progress or silently change the bound topic. Only an exact,
learner-selected session can restore website preferences:

```sh
CLIENT persona-restore --session SESSION_ID
CLIENT protocol --new-chat
```

`persona_missing` and Mastery prompt-resource diagnostics are stop conditions:
report them and let the learner choose or repair the original installation; do not
select a substitute role or prompt pack. The protocol's operation mapping is the
executable bridge contract.

## Prepare and commit PDF evidence

```sh
CLIENT tool list_objective_pdfs --file - <<'JSON'
{"objective_id": "OBJECTIVE_ID"}
JSON

CLIENT tool prepare_pdf_evidence --file - <<'JSON'
{
  "request_id": "UUID",
  "expected_revision": 7,
  "objective_id": "OBJECTIVE_ID",
  "pages": [{"source_id": "SOURCE_ID", "filename": "lesson.pdf", "page_index": 0}]
}
JSON

CLIENT pdf-image --preparation PREPARATION_ID --page 0 --output /tmp/page-1.png
CLIENT tool commit_pdf_evidence --file - <<'JSON'
{
  "request_id": "UUID",
  "preparation_id": "PREPARATION_ID",
  "expected_revision": 7,
  "objective_id": "OBJECTIVE_ID",
  "purpose": "assessment",
  "coverage_summary": "State which required facets these inspected pages cover.",
  "coverage_complete": true,
  "confirm": true,
  "sources": [{
    "page_number": 0,
    "quote": "Exact text from the rendered original page.",
    "visual_observation": "Specific relationship you saw in the opened image.",
    "supports": "Explain how the page supports the objective or question/key."
  }]
}
JSON
```

`prepare.pages[].page_index` and `commit.sources[].page_number` are zero-based.
The commit page number refers to the preparation array. Assessment
`evidence_quotes[].page_number` is one-based. Assessment
`question_citations[].source_index` is the zero-based index into the committed
packet `sources`. A required visual objective needs at least one question
citation with a matching `visual_observation`; providing both quote and
observation preserves both.

Then submit `CLIENT assess --file -` with the normal assessment payload. Include
the selected PDF `source_id`, a one-based `evidence_quotes.page_number`, and the
matching zero-based `question_citations.source_index`.

## Inspect and repair a historical question

Use the exact interaction ID returned by the host or `codex-ASSESSMENT_ID` for a
the AI assistant assessment. Inspect before proposing a change.

```sh
CLIENT tool inspect_question_repair --file - <<'JSON'
{"question_id": "QUESTION_ID"}
JSON

CLIENT tool preview_question_repair --file - <<'JSON'
{
  "request_id": "UUID",
  "expected_revision": 9,
  "question_id": "QUESTION_ID",
  "action": "correct",
  "reason": "Why the stored key or verdict is wrong.",
  "expected_answer": "Corrected answer key or rubric.",
  "result": "correct",
  "confidence": 0.95,
  "feedback": "Corrected rationale.",
  "citations": [{
    "source_index": 0,
    "quote": "Exact supporting passage.",
    "visual_observation": "Matching inspected-page observation when visual.",
    "support_statement": "How this evidence supports the corrected key."
  }]
}
JSON

CLIENT tool commit_question_repair --file - <<'JSON'
{
  "request_id": "UUID",
  "preview_id": "PREVIEW_ID",
  "confirm": true
}
JSON
```

For void, omit `expected_answer`, `result`, `confidence`, `feedback` and
`citations`; keep a concrete `reason`. `action=void` removes the invalid
assessment from mastery and review; `action=correct` regrades the stored
verdict/key. Retry commit with the exact `request_id` and `preview_id`; the
server replays the durable receipt after an interruption. Do not repair through
the native `mastery_repair_question` for a the AI assistant qualitative record.
