# Route create and revise

`CLIENT` means the absolute command documented in `SKILL.md`. Query exact fields
with `CLIENT describe route-create`, `CLIENT describe route-preview`, and
`CLIENT describe route-commit`. Refresh `CLIENT doctor`, `CLIENT route` and
`CLIENT context` before editing.

The assistant designs the route from the learner's goal, background, time and
selected materials. Do not call DeepTutor route generation or its chat model.
Read `CLIENT doctor` and check `capabilities.route_create` and
`capabilities.route_edit` before saving. Ask only for information that materially
changes the plan. Show proposed modules and objectives before saving. An explicit
instruction to implement a reviewed plan authorizes its save; do not ask for the
same approval again.

## Create

Use `CLIENT route-create --file -` with JSON on stdin. Generate `creation_id`
with the local UUID runtime. No host path/ID is supplied; the receipt returns
`topic_id`. New modules/objectives omit `id`; optional `client_ref` values let
objectives refer to each other before durable IDs exist.

```json
{
  "creation_id": "12345678-1234-4123-8123-123456789abc",
  "name": "A learner-approved route name",
  "goal": "The learner's intended outcome",
  "modules": [{
    "name": "Foundations",
    "objective": "Explain and apply the foundations",
    "knowledge_points": [
      {"client_ref": "first", "name": "First concept", "type": "concept"},
      {"name": "Apply the concept", "type": "procedure", "prerequisite_ids": ["first"]}
    ]
  }],
  "profile": {"time_budget": "30 minutes a day", "preferences": "Concrete examples"}
}
```

Optional fields: `description`; `profile.prior_knowledge`, `target_level`,
`time_budget`, `preferences`, and `teaching_strategy` (`probe_first` or
`teach_first`). Objective types are `memory`, `concept`, `procedure`, `design`.
Up to 20 modules, normally 7 objectives per module; pass thresholds default to
0.7. A preserved existing module may retain a larger stored objective count.

`sources` may contain up to 16 existing KB/file references, each with
`client_ref`, `kind` (`knowledge_base` or `file`), `source_id`, and `label`. A
KB's `source_id` is its original KB reference; a file's `source_id` is its
relative document path and also requires `kb_name`. Put source aliases into
objectives' `topic_source_ids`. Use actual references selected by the learner,
never invented resources or arbitrary file paths. The server checks access and
file containment. Creation performs no upload, indexing, extraction or embedding
probe; do not present an attached source as verified grounding. With no sources,
the host stores a goal-only route.

Keep exact JSON/UUID for network retries. A different payload with the same
creation ID is rejected. Creation does not change the current topic binding.
After `saved=true`, use `CLIENT bind --topic RETURNED_TOPIC_ID`, then refresh
context. Do not manufacture a learner answer or grade to test a new route.

## Revise

1. Read `CLIENT route` for the complete editable module tree and current
   revision, plus `CLIENT context` for learning state. Preserve each unchanged
   module/objective ID. Never use position or names alone to infer identity.
2. Submit `CLIENT route-preview --file -` with `request_id` (new UUID),
   `expected_revision`, and the complete proposed `modules` array. Omitted
   modules/objectives mean deletion. New items omit `id`; existing ones retain
   it. Array order controls module/objective order. Omitted objective relations,
   module objective and threshold retain their existing values; explicit empty
   arrays clear relations. Existing visual requirements are read-only and
   retained by the server. Do not copy `visual_requirements` into the payload.
3. Inspect the returned `changes` and `identity_map`. Explain additions,
   removals, moves and replacements to the learner. A preview persists a draft
   but does not change the route, revision, mastery or review history.
4. After reviewing that result, call `CLIENT route-commit --file -` with
   `{"request_id":"A_NEW_UUID","preview_id":123}` using the actual integer
   preview ID. The commit UUID must differ from the preview UUID. Commit accepts
   only the saved draft, never an unreviewed replacement payload. Reuse the exact
   commit JSON/UUID for a retry; another UUID cannot claim its receipt.
5. Read `CLIENT route` and `CLIENT context` again; report the saved revision and
   original website link. Drafts expire after one hour. State changes require a
   fresh preview and review; do not silently rebase a plan after a conflict.

Example preview envelope:
`{"request_id":"A_UUID","expected_revision":7,"modules":[...]}`. Generate valid
UUIDs locally, not the illustrative strings. Module names/purposes/thresholds,
order, prerequisites and untouched objectives can be revised. Objective name/type
changes mint new IDs. A target with study history cannot be deleted, replaced or
reassigned to different sources; retain it and add a new target instead. Mastery,
attempts, evidence, review, learner claims, explanations, stage failure records
and visual gates remain protected. The current target follows its ID when moved.

An active web lease, pending question or archived topic blocks changes. Finish
the web turn or restore the topic first; never cancel a web turn to force an
edit. Existing topic name/goal/profile and the topic-wide source set are not
edited by these route-revision commands. Preview responses contain curriculum
differences only, never the host's raw progress diff.
