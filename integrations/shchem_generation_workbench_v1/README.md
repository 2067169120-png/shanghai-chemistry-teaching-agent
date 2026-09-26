# Shanghai chemistry generation workbench core v1

This module is a reusable, append-only foundation for task-card planning and
machine-only candidate run bookkeeping. It does not call a model, generate
questions, register formal questions, freeze a paper, publish artifacts, or
grant any human/official/publication authority.

## Boundary

- `generation_task_card_v1` accepts only exact contract keys. Evidence inputs
  are record IDs, never paths, URLs, source text, or attachments.
- Record IDs use an explicit opaque alphabet: ASCII letters, digits, `_`, `.`,
  and `-`. Colons, URI schemes, drive-relative paths, UNC forms, slash,
  backslash, whitespace, controls, and NUL are rejected.
- A full-paper card must use `paper -> theme_big_question ->
  printed_question -> atomic_part`; an independent selection section is
  rejected. Theme count, duration, score, numbering, and selection scoring can
  remain `unknown` or `blocked_pending_review`, in which case their value is
  `null`.
- The only provider profile is `sol_xhigh_generation_v1`. Its model and effort
  are requested configuration metadata, not a platform report or signature.
- Evidence preflight consumes structured summaries only. A failed
  `machine_candidate` request becomes `plan_only_blocked`; a plan artifact can
  still be written without question content.
- Every stored record is canonical JSON with a self hash. Candidate records
  retain exact original input bytes (base64), byte count, and SHA-256. Freeze
  transactions copy the already-verified canonical candidate-record bytes into
  an O_EXCL content-addressed snapshot inside a pending package. The freeze and
  commit records bind that snapshot's path, bytes, size, payload hash, and self
  hash; frozen reads never dereference the mutable source-candidate path.
- Freeze checks the still-open source descriptor before and after directory
  commit and again after the commit marker. Same-size, ABA, path replacement,
  or post-commit drift fails and rolls back only the unreturned transaction.
  Successful packages remain append-only and have no delete API.
- Task/run directories are created exclusively. Files use exclusive creation.
  There is no overwrite or delete API. Run events are one-file-per-sequence and
  hash-chain their predecessor.
- Store construction `lstat`s the raw workspace root before resolving it, then
  verifies every lexical and resolved parent component is not a symlink or
  reparse point.

Every API result and immutable record keeps `human_reviewed=false`,
`publication_allowed=false`, and `official=false`.

## Minimal API flow

1. Create `AppendOnlyWorkbenchStore(existing_workspace, "relative/state")`.
2. Submit strict UTF-8 JSON bytes with `create_task_card_candidate`.
3. Freeze using the returned candidate file SHA-256.
4. Create a run, attach that task's freeze, and record structured evidence
   preflight.
5. Write a plan artifact, or—only when machine preflight is ready—advance the
   state machine using record IDs for external candidate/check artifacts.

No method accepts an arbitrary prompt, model name, effort, URL, or artifact
path.
