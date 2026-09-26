# DeepTutor × Shanghai Chemistry Gateway v1

This package is the versioned local integration boundary between the read-only DeepTutor upstream and the Shanghai Chemistry system.

## Guarantees

- `shchem.gateway.v1` JSON envelopes and Bearer authentication on every API route.
- Loopback bind only. Configuration rejects non-loopback hosts.
- Per-principal student allow-lists, strict identifiers, path containment, request/upload limits, CORS allow-listing, and JSONL audit events without credentials or raw student IDs.
- Raw student images may be stored locally. Image privacy processing belongs to `integrations/student_learning_v1`; this gateway only accepts `deeptutor_image_sanitization_v1` receipts and exposes no raw-media read API.
- Only a receipt with `state=ready_for_model_processing`, `egress_allowed=true`, `raw_model_access_allowed=false`, and a matching sanitized SHA-256 can enter a model binding. Missing/uncertain checks keep the raw local file and block model egress.
- CCSwitch is disabled by default and hard-pinned to `http://127.0.0.1:15721`. The client receives no OAuth/provider token and the gateway never reads DeepTutor OAuth storage.
- Controller mode calls the restored controller 2.0.0 JSON CLI for live `status`, `validate`, `query`, and `preflight`; it never substitutes a historical report.
- Versioned dynamic domain adapters call public APIs from `student_learning_v1` 2.1 and `shchem_generation_v2`. Real diagnosis is strictly `prepare_real_diagnosis → issue_real_diagnosis_receipt → execute_real_diagnosis`: the student adapter calls controller `preflight diagnose`, saves its adapter-v2 receipt privately, and runs `diagnosis-receipt-verify` immediately before execution. The Gateway neither accepts caller receipts nor duplicates the preflight.
- Generation is post-gated by the generation domain itself after its candidate and governance chain exist. The Gateway uses the public job/content/figure/ZIP APIs and does not duplicate controller `generate` preflight.
- Candidate hierarchy is derived from the public current-paper binding. `printed_count`, `atomic_count`, both complete ID sets, and the atomic-to-printed map are never constants. Content and registry membership must exactly match the current candidate atomic IDs; retired, missing, extra, duplicate, and printed-as-atomic mutations fail closed.
- Mock mode succeeds only with the bundled `synthetic_fixture_only` fixture and an explicit `synthetic_fixture=true`. It never claims real diagnosis, teaching readiness, human review, or publication approval.
- Publication remains closed. All generated ZIPs state `machine_only=true`, `human_reviewed=false`, `teaching_use_allowed=false`, and `publication_allowed=false`.
- Slice3-A exposes a separate teacher-only generation workbench for strict task-card candidates, hash/byte-bound task-card freeze, structured evidence preflight, and plan artifacts. It stores exclusively below `<gateway state_root>/generation_workbench`, invokes no model, produces no questions, exposes no stored path/raw evidence, and has no actual-generation, formal-freeze, register, promote, publish, tag, or download action.
- `GET /api/v1/generation/runs/current/governance` is a teacher-only, trusted-loopback-Origin, zero-R18-write projection of the controller-selected active R18 state. It separates `prefreeze_deterministic_qa`, review A, review B, adversarial, chain, and registration; validates activation-scoped bindings without legacy receipt fallback; and returns only bounded reason/finding metadata plus hash/bytes. Phase 2, registration, download, release, prompts, internal paths, raw evidence, and private task metadata remain unavailable.
- `GET /api/v1/kb/full-bank-readiness/status` and `/records` expose hash-verified readiness accounting only. They keep the 80-paper Shanghai inventory and 98 quarantined teaching packages as two distinct cohorts, verify the report self hash plus five exact upstream source hashes, return no source paths/question text/answers, and keep the formal-ready count and every downstream gate at zero/false.
- Workbench provider metadata is the fixed `sol_xhigh_generation_v1` request (`gpt-5.6-sol`, `xhigh`). It is requested configuration only: `platform_actual_reported=false` and `signed=false`.
- Teacher-managed delivery is a separate local gate. Generation candidates require a ZIP-external, final-archive-hash-bound attestation whose registry binding exactly matches the current atomic IDs. Student bundles instead require `manifest_scope=sidecar_final_zip_hash_bound`, an empty reason list, and matching verified ZIP hash. Content-only status cannot unlock download. Mock ZIPs remain visibly separate synthetic test artifacts.

## API surface

The OpenAPI inventory is in `staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml`. It covers status, evidence search/get, preflight, the teacher-only task-card/plan workbench, existing candidate jobs, job status, student diagnosis, handout candidates, figure get, publication preflight, local upload, and existing student-scoped ZIP delivery.

The central-controller adapter contract is `shchem.controller_cli_adapter.v1`, bound to controller contract `2.0.0`. The controller owns gates, not domain jobs. Diagnosis/private input/week manifests route through `shchem.student_learning_domain_adapter.v2`; generation/content/figure/delivery candidates route through `shchem.generation_domain_adapter.v2`; teacher delivery uses `shchem.teacher_managed_delivery.v2`. Dynamic probes fail closed on missing symbols, syntax errors, public-candidate drift, or version drift. Read-only catalog fallback never marks a record retrieval-ready.

## Layout

- `config.py`: environment-only credentials and safety invariants.
- `security.py`: authentication, student scoping, path containment, atomic JSON, and audit redaction.
- `student_bridge.py`: call-only bridge to the student task's private domain, including score/progress/attempt/timing mappings and the real three-stage receipt flow.
- `domain_adapters.py`: dynamic generation-v2 candidate and figure bridge.
- `generation_workbench.py`: safe public projection and append-only orchestration over `shchem_generation_workbench_v1`; plan-only and no model call.
- `full_bank_readiness.py`: exact-file, self-hash and upstream-hash verified readiness summary reader; no question/document content and no mutation surface.
- `adapters.py`: controller fixture/catalog/CCSwitch adapters.
- `service.py`: uploads, jobs, machine QA, and safe ZIP packaging.
- `http_app.py`: dependency-free loopback HTTP server and static overlay host.
- `launcher.py`: fail-closed Windows lifecycle wrapper over `AppConfig`, the gateway HTTP server machinery, the live controller, and the existing overlay. It generates a per-session Bearer token, keeps session files outside the project under an owner-only ACL, verifies the overlay manifest/assets and controller contract, exposes authenticated health checking, and uses a local instance-bound stop signal for clean shutdown.

No file in `staging/third_party/DeepTutor` is modified.
