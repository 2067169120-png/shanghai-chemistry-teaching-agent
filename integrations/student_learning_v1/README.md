# student_learning_v1 integration

This package owns the versioned boundary for the private student-learning domain.

- Every profile gets a random UUID root and a separate 256-bit bearer capability; every read, write, derived artifact, bundle, and deletion operation re-authorizes that pair.
- Direct identifier keys and obvious identifier text are rejected from structured inputs. A raw error image may be saved only under that student's private root. Every new upload returns `shchem.student-image-local-hold.v1` with `status=awaiting_visual_provider`, `ocr_invoked=false`, and zero transport attempts. The retired sanitizer/text-recognition entry points fail closed; historical sanitized files and receipts remain read-only and cannot unlock evidence or model input. Student pages leave the machine only through the separate direct-vision workflow after capability, structured-output, data-class, exact page-hash, privacy, and one-time provider-revision gates pass.
- Real diagnosis is a formal two-stage private machine-only path, not a synthetic alias. `prepare_real_diagnosis` freezes the exact attempts, media receipts, canonical `paper → theme_big_question → printed_question → atomic_part` chains, and hashes without accepting a receipt. `issue_real_diagnosis_receipt` gives that frozen payload to the live controller `preflight --mode diagnose`, which alone may return `diagnosis_input_gate.controller_receipt` as `central_diagnosis_adapter_v2`. `execute_real_diagnosis` consumes only the privately saved controller receipt and must first pass the live controller `diagnosis-receipt-verify`, which re-derives it from the current registry. Self-asserted `allowed` or `live_central_state_used` fields cannot unlock execution. Its output is `machine_only_real_student_candidate`, `human_reviewed=false`, `teaching_effectiveness_unverified=true`, and `teaching_use_allowed=false`. Historical status reports never satisfy this path.
- Synthetic regression remains separate. Every synthetic conclusion and bundle artifact says `synthetic_fixture_only`, `synthetic_fixture_not_real_student`, and `human_reviewed=false`. Synthetic ZIP verification returns `PASS_STRUCTURE_FIXTURE_ONLY_NOT_CONTENT_COMPLETE`; it never represents formal questions, solutions or a complete week pack.
- Diagnosis exposes score/trend, latest progress, low-weight declared weakness, structured response evidence, and elapsed-time contributions. Unreviewed visual-analysis suggestions and historical image/text-recognition artifacts are excluded. Attempt/timing seconds must match, and main/secondary error-attempt support cannot overlap.
- Every positive weekly cycle has exactly seven 60-minute days. Activity-level buckets recompute to 252/105/63 minutes. Week 1/2 checkpoints are day 7/day 14; later cycles use `week_end` at absolute day `7*cycle_index`. Successful bundle builds append an immutable private history record binding weekly hashes, mastery, delta, focus, spaced-review due work and next objectives.
- Public export enforces `k >= 5` and emits no profile IDs, direct identifiers, exact times, or free-text narratives.

Synthetic output remains a 15-JSON scaffold. A real formal bundle is built only
from an injected generation provider matching
`sh-chem-db/kb/student_learning_v2/generation_content_provider.schema.json`.
It must supply formal stems, suggested answers, detailed explanations,
non-official suggested scoring points, source/atomic-governance bindings, and
eight files: training student/answers plus combined retest student/answers, each
in valid DOCX and PDF. Each file is bound to its semantic payload and locally
extracted text hash; the required task IDs, stems and answer-version details
must occur in the rendered text. The resulting manifest has 23 artifacts.
Missing, blank, mislabeled or placeholder content fails closed and never claims
publication approval.

Gateway calls `gateway_api.py` or imports `GatewayStudentLearningAPI`; the stable JSON envelope is `gateway_api.schema.json`. CLI credentials come from `STUDENT_LEARNING_CAPABILITY`, never a command-line flag.
Python injects `formal_content_provider=...`; CLI uses
`build-week-bundle ... --content-export-json <generation-export.json>` and never
generates missing content itself.

The real-path methods/commands are:

1. `prepare_real_diagnosis(...)` / `prepare-real-diagnosis` → an `awaiting_controller` job containing `prepare_id`, `prepare_request_sha256`, `attempt_input_sha256`, per-attempt bindings, and `controller_input_without_receipt`.
2. `issue_real_diagnosis_receipt(...)` / `issue-real-diagnosis-receipt` → invokes central `preflight diagnose` with that exact no-receipt payload and privately saves only the returned v2 receipt plus a non-authoritative cache record.
3. `execute_real_diagnosis(...)` / `execute-real-diagnosis` → invokes central `diagnosis-receipt-verify` on the frozen input and saved receipt, then returns a completed diagnosis job only after live re-derivation passes; otherwise code 15.

The current central repository reports zero valid governance chains, so its preflight issues no receipt and the formal real entry is intentionally blocked today.
