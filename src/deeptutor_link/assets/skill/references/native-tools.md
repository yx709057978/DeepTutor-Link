# Explicit native tools and DeepTutor turns

Use this reference only after the learner explicitly requests a native Mastery
tool or a conversation with DeepTutor. Ordinary assistant teaching, questioning,
grading and writeback stay on the daily path in `SKILL.md`; they do not call
native quiz/grade tools or DeepTutor chat even when host models are configured.

`CLIENT describe tool OPERATION` is the authoritative field, response, success,
binding, retry, pending and model contract. `CLIENT tool get_mastery_tutor_tools`
lists the registered native tool schemas and each tool's host-model behavior.
Both reads are safe; neither read authorizes a mutation by itself.

## Native Mastery tool

`call_mastery_tutor` executes one registered host tool. Use the explicit
`path_id`, a `tool` from the catalog, matching `arguments`, a stable
`client_session_id`, a stable `turn_id` for one learner turn, a fresh UUID
`request_id` for each tool operation, and `confirm: true`.

The extension applies original account/workspace scope, host hooks, path lease
and receipt semantics. The response's `client_session_id` is reused unchanged for
the next native tool request. `host_session_id` identifies the underlying
DeepTutor session and is not the client continuation key. `replayed=true` means
the exact prior request receipt was returned.

For `record_turn`, omit `arguments`, provide the completed tutor text in
`assistant_message`, and use a fresh request ID. Do not send
`direct_dt_conversation` to native tool calls; that field belongs to explicit
capability turns.

Do not submit an empty learner answer. A grade may use only the learner's actual
answer in the current user message or `arguments.answer`, or a reply already
committed by DeepTutor. The host may invoke its own model where the selected
tool's declared semantics require it. Existing web/assistant pending work and
active leases are protected; never cancel or overwrite them.

Read-only bridges remain separate:

```sh
CLIENT tool read_mastery_status --file - <<'JSON'
{}
JSON

CLIENT tool read_mastery_paths --file - <<'JSON'
{}
JSON
```

## Direct DeepTutor turn

Only when the learner explicitly asks to converse with DeepTutor, start one
capability turn:

```sh
CLIENT tool send_message_to_deeptutor --file - <<'JSON'
{
  "capability": "chat",
  "message": "The learner's explicit request",
  "request_id": "A_FRESH_UUID",
  "direct_dt_conversation": true,
  "confirm": true
}
JSON
```

This may invoke configured host models. Messages and replies are stored in the
DeepTutor session; it does not copy or replace this assistant chat. If the result
is `waiting_for_user`, submit the learner's actual reply with
`reply_to_deeptutor_turn`, preserving the returned session/turn IDs and sequence
cursor. Use a new request ID for the reply. Neither direct path replaces or
cancels another client's active turn.
