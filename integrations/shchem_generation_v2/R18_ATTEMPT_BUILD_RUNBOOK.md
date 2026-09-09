# R18 attempt-scoped build runbook

## Safety boundary

The R18 fixed-path `freeze` command is disabled.  It performs a read-only
partial-live check and exits before writing.  The current fixed 41-path set is
known to be mixed-generation and is never a selectable successful package.

`freeze-attempt` is the only build entry point.  It:

1. requires an ID matching `R18-BUILD-ATTEMPT-<32 lowercase hex>`;
2. snapshots the fixed 41 paths, absent review leaves, and the complete closed
   inventories of protected archive/review/G1/G2 control directories;
3. reserves the ID with `O_EXCL` (the reservation remains even after failure);
4. generates only below a unique temporary attempt root;
5. writes provenance with `O_EXCL` and no overwrite fallback;
6. verifies trusted production schemas byte-for-byte, receipt self-hashes,
   exact inventory, one explicit execution self-report across all receipts,
   internal file-reference
   closure, portable paths, and the absence of reparse points/hardlinks/aliases;
7. revalidates protected snapshots and atomically renames the complete
   directory into `staging/v1_generation/build_attempts/r18/packages/<ID>`;
8. writes a separate `O_EXCL` commit attestation only after post-rename package
   validation and protected-state revalidation.  A package without this marker
   is never selectable, including after a failed rollback.

No `current` pointer is created or updated.  A committed package keeps all
generated candidate, controller subject, deterministic request/report,
reports, figures, producer/SOL/provenance, prefreeze, delivery, and vendored
schema inputs below its own immutable root.

## Commands

Do not run a build without a separate build authorization.  Code/test audit
does not authorize this command.

```powershell
python -m integrations.shchem_generation_v2.build_demo freeze-attempt `
  --attempt-id R18-BUILD-ATTEMPT-0123456789abcdef0123456789abcdef `
  --generator-execution-metadata staging/v1_generation/build_inputs/r18/generator_execution_self_report.json
```

Read-only package selection/validation:

```powershell
python -m integrations.shchem_generation_v2.build_demo check-attempt `
  --attempt-id R18-BUILD-ATTEMPT-0123456789abcdef0123456789abcdef
```

The metadata file is a generator-owned `generation_execution_self_report`, not
a root observation or platform signature.  It must be a new, non-legacy,
workspace-relative input that validates for R18 and is copied into the package.

The selector accepts only an atomically committed production package with a
retained reservation, an exact commit attestation, trusted schemas, and a valid
closed inventory.  Synthetic transaction fixtures are deliberately
unselectable.  It never falls back to the legacy fixed paths.

## Failure handling

- A provenance, schema, validation, or pre-commit failure removes only the
  attempt temporary directory; the burned reservation prevents reuse.
- A rename failure leaves no package and removes the temporary directory.
- A post-rename validation/protected-snapshot failure removes the unselected
  package; no pointer exists that could expose it.
- Never delete or rewrite a reservation to retry.  Use a new attempt ID.
- Never repair the current mixed fixed-path set in place, move its provenance
  aside, weaken `O_EXCL`, or use it as a downstream subject.
