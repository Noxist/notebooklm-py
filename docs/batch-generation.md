# JSON Studio batches

**Status:** Active
**Last Updated:** 2026-11-11

The fork includes a separate local application over the public NotebookLM client.
It generates every supported Studio type without changing the library's generation
implementations. Google account quotas and feature entitlements still apply.

## Start

From the checkout, install `uv sync --frozen --extra server --extra browser --extra dev`
and activate `.venv` (`source .venv/bin/activate`). Authenticate separately with `notebooklm login`. Do not place Google cookies,
tokens, or storage state in JSON jobs or Git.

Run `python -m notebooklm.batch --help` for configuration, then
`python -m notebooklm.batch`. Open the loopback address printed by the server.
Paste JSON or import a file, validate it, inspect a dry run, then submit.
The editor includes live capability metadata. No Google request occurs during
validation or dry run. The app is intended for a trusted single local user;
do not expose it through a tunnel or reverse proxy.

## Job files

See [all types](../examples/batch/all-types.json) and
[a source-selected deferred report](../examples/batch/source-selected.json). Replace notebook and source IDs
before submitting. A file contains `{"jobs": [...]}`. Each job has a stable unique
`id`, `type`, `notebook_id`, and `title`; optional `prompt`, `language`, `format`,
`source_ids`, `when` (`now`, the default, or `later`), and an `options` object.
Omitting sources lets the public API use the notebook's sources. Unknown options,
invalid enum choices, and unsupported combinations are rejected before submission.
Use the capability metadata in the UI for exact option names and enum choices.

Common `prompt` maps to public generation instructions, report preset
`extra_instructions`, or CUSTOM report `custom_prompt`; `format` maps to the type's format enum. Other public generation options
belong in `options`. Do not specify the same option twice through common fields
and `options`. Enum member names and enum values follow the capability metadata.

| Type | Public API | Generate Later |
| --- | --- | --- |
| `audio` | `artifacts.generate_audio` | Yes |
| `video` | `artifacts.generate_video` | Yes, except cinematic |
| `cinematic_video` | `artifacts.generate_cinematic_video` | No |
| `report` | `artifacts.generate_report` | Yes |
| `slide_deck` | `artifacts.generate_slide_deck` | Yes |
| `infographic` | `artifacts.generate_infographic` | Yes |
| `mind_map_interactive` | `mind_maps.generate(kind=INTERACTIVE)` | No |
| `mind_map_note` | `mind_maps.generate(kind=NOTE_BACKED)` | No |
| `flashcards` | `artifacts.generate_flashcards` | Yes |
| `quiz` | `artifacts.generate_quiz` | Yes |
| `data_table` | `artifacts.generate_data_table` | No |

Reports include briefing documents, study guides, blog posts, concept explanations,
and custom reports. Report options that the selected preset would ignore
are rejected; use `prompt` to target the appropriate public argument. Video formats include cinematic via the dedicated public API.
Quiz question types, images, quantity and difficulty; infographic orientation,
detail and style; audio length; video style prompts; and slide length are supported.
Web interactive mind maps have no language field on the wire. The app rejects an
explicit language for these jobs instead of silently claiming it was applied.

`when: "later"` is Google's Generate Later queue. Google chooses execution time,
usually hours later. This app provides no exact-time scheduler. A queued Google
job is polled without submitting another generation request.

## Persistence, recovery, and retries

SQLite stores jobs and returned artifact IDs outside the checkout by default.
Keep this database across restarts. Losing it loses duplicate protection.
IDs are unique across batches: importing the same job again is idempotent;
reusing an ID for changed content is rejected. Use a new ID only when you
intentionally want another artifact.

Submission intent is committed before contacting Google. If the process dies or
loses the response before saving the returned ID, the job becomes uncertain.
Google does not supply a transactional idempotency key for this operation, so
exactly-once remote execution cannot be guaranteed. The app deliberately refuses
to resubmit uncertain jobs. Inspect Studio, then reconcile with the exact generated
artifact ID. Do not invent an ID or blindly create a replacement job.

Known artifact IDs are retained during polling and rename failures. Individual
retry controls resume the existing work, including in-place retries for confirmed
failed artifacts. Titles are renamed using the returned ID and verified by reading
the API result. A job cannot report complete until the requested title is verified.
Note-backed maps use the public mind-map rename API rather than artifact rename.

Concurrency and spacing are configurable from the launch command, for example
`python -m notebooklm.batch --database ~/.notebooklm/batch.sqlite3 --concurrency 2 --interval 2 --poll-interval 10`.
The spacing applies to all public calls made by the worker. They limit this
application, not other clients on the same account. There is no automatic retry
loop for ambiguous creation failures. Transient poll/rename errors retain the ID
for safe retry. Logs contain job IDs and state transitions, not authentication or
raw upstream error responses. The database contains job prompts and notebook IDs;
protect it as private local data. Run one app process per database.

## Development and extension

`src/notebooklm/batch/core.py` owns registry, validation, state and execution;
`web.py` adapts it to FastAPI and a local editor. Add a registry entry for a newly
supported public generation method and extend the mocked routing/validation tests.
Do not duplicate RPC payload construction in this application.

Run `pytest tests/unit/test_batch_core.py tests/unit/test_batch_web.py --no-cov`.
Tests use fake clients and never generate real NotebookLM content. Browser UI checks
must likewise run with a fake client, or avoid submitting jobs.

Stop the app to stop local polling; remote jobs already accepted by Google continue.
To roll back the application, stop it and remove these additive modules. Preserve
the database if you may resume work. No library migration or auth change is needed.
