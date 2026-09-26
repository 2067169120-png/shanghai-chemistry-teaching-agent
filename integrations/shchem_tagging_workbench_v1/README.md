# Shanghai chemistry tagging workbench v1

This package stores proposed tag changes for one `atomic_part`. It is an
isolated candidate ledger, not a central-KB mutation layer.

## Fixed boundary

- A request has exactly `node_type`, `node_id`, `paper_id`, the three
  optimistic-concurrency hashes, `changes`, `reason`, and
  `evidence_binding_ids`.
- `node_type` is always `atomic_part`.
- `changes` is a field-replacement object, never JSON Patch. Its only keys are
  `item_type`, `selection_rule`, `primary_knowledge_K`,
  `supporting_knowledge_K`, `ability_A`, `context_C`, `response_R`,
  `representation_RP`, `cognitive_prelabel`, and `difficulty_factors`.
- K/A/C/R/RP/D membership is checked against the caller-loaded, hash-bound
  taxonomy snapshot. R and RP have separate membership sets and prefixes.
- A non-null D1-D5 `cognitive_prelabel` requires all ten non-unknown difficulty
  factors. `measured_difficulty` is never accepted.
- Every returned or persisted record fixes `candidate_only=true` and
  `human_reviewed`, `retrieval_ready`, `generation_allowed`,
  `publication_allowed`, and `official` to `false`.
- There is no apply, promotion, retrieval activation, generation activation, or
  publication API.
- The public `AUTHORITY_FLAGS` view is immutable. Store writes use fresh private
  fixed flags, and integrity verification compares independent fixed literals.

## Caller-loaded snapshots

The store does not accept or discover central paths. The caller loads canonical
raw JSON bytes for the base index manifest, target atomic-part row, and taxonomy,
then passes those bytes with hashes in the request. Equivalent reparsed JSON is
not enough: the exact canonical raw bytes are the concurrency unit.

The base manifest has the closed
`shchem_atomic_part_index_manifest_v1` shape: manifest identity, one `paper_id`,
`node_kind=atomic_part`, and membership rows binding `paper_id`, `node_kind`,
record ID, exact target-record SHA-256, and byte count. The target row must carry
the same `paper_id`; cross-paper, cross-kind, cross-record, hash, or byte-count
substitution fails closed.

Evidence bindings use exactly one of:

- `evidence_snapshots={opaque_id: immutable_bytes}` for a content-addressed copy
  inside the candidate package; or
- `evidence_hash_bindings={opaque_id: lowercase_sha256}` for an exact external
  hash binding.

All evidence IDs in the request must have exactly one binding.

```python
store = AppendOnlyTagPatchStore(workspace_root)
created = store.create_candidate_patch(
    request,
    base_index_manifest_raw=base_raw,
    target_record_raw=target_raw,
    taxonomy_raw=taxonomy_raw,
    evidence_snapshots={"EVIDENCE-001": evidence_raw},
)
```

Production state is code-derived under the dedicated candidate directory
`staging/coordination/tagging_workbench/candidate_store_v1`; callers cannot
choose a state path. Tests use only
`AppendOnlyTagPatchStore.for_test_workspace(temp_workspace)`, which derives its
own state directory and requires a dedicated OS-temporary workspace. Central-KB,
live, private, symlink, and reparse-root paths are rejected.

`create_patch` is an alias. `get_patch`, `read_patch`, `list_patches`, and
`status` are read-only.

## Append-only package

The deterministic `TAGPATCH-<sha256>` directory is exclusively created so
concurrent identical submissions have one winner. Every file uses no-replace
creation. The final `commit.json` binds the exact file inventory and is written
last. Creation rechecks stable file identity, bytes, hashes, self-hashes, source
snapshots, and the commit marker before returning. A failed, not-yet-returned
transaction is rolled back without following symlinks or reparse points;
committed packages have no overwrite or delete interface.

The implementation uses only the Python standard library. Tests are in
`staging/coordination/tagging_workbench/tests/test_tag_patch_core.py`.
