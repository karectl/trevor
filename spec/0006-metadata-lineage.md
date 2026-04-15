# ADR-0006 — Metadata Lineage: Linear Versioning on a Logical Object

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

When a researcher replaces an output object (e.g. resubmitting a table after low-number suppression), the system needs to:

1. Retain the original file and its audit history (immutability constraint C-03)
2. Carry metadata annotations forward to the new version
3. Maintain checker feedback across versions so reviewers can see the full history
4. Ensure that the final RO-Crate references only the latest approved version

Two modelling approaches were considered:

**Option A: Separate metadata per version**  
Each `OutputObject` version has its own metadata record. Metadata must be explicitly copied or referenced across versions.

**Option B: Shared metadata on a logical object (lineage chain)**  
A `logical_object_id` groups all versions. One `OutputObjectMetadata` record exists per `logical_object_id`. It accumulates state across versions.

---

## Decision

Use **Option B: shared metadata on a logical object** with **linear versioning**.

### Logical object

A `logical_object_id` (UUID) is assigned when the first version of an object is uploaded. All replacement versions share this ID. The lineage is a strictly linear chain: v1 → v2 → v3 ... There are no branches. A researcher may only replace the *current* (non-superseded) version.

### Metadata ownership

`OutputObjectMetadata` is keyed on `logical_object_id`, not on a specific version's UUID. It holds:
- Fields set by the researcher at submission (title, description, justification, suppression notes)
- An array of checker feedback entries, each tagged with the version it was given on
- Tags and any other annotations added by either party

When the researcher creates a replacement version, they may update the metadata fields. The old values are not overwritten — the `checker_feedback` array retains all previous entries, and the metadata record's `updated_at` reflects the latest change.

### Interdependencies

If one output object references another (e.g. a narrative markdown file references a table), the reference always points to the `logical_object_id`, not to a specific version UUID. At render time and at crate assembly time, trevor resolves the `logical_object_id` to its current (latest non-superseded) version. This means references are automatically updated when a replacement is submitted.

### Version state transitions

```
v1: SUPERSEDED ──replaces──► v2: SUPERSEDED ──replaces──► v3: PENDING
                                                              └── current version
```

Only the current version is in a non-`SUPERSEDED` state. There can be only one non-superseded version per `logical_object_id` at any time (enforced by DB constraint).

### What "carry forward" means in practice

A table is submitted as v1. The checker requests changes: "Please suppress cells with n < 10." The checker feedback is appended to the `OutputObjectMetadata.checker_feedback` array with `version=1`.

The researcher uploads a corrected table as v2, which replaces v1. The same `OutputObjectMetadata` record now has:
- `title`, `description`, `justification`: researcher can update these
- `checker_feedback[0]`: `{version: 1, feedback: "Suppress cells with n < 10", reviewer: ..., timestamp: ...}`
- `suppression_notes`: researcher updates this to describe what they did

The checker reviewing v2 sees the full history — what was asked, what changed — without switching between records.

---

## Consequences

- **Positive**: Single metadata record per logical output — no risk of divergence between versions.
- **Positive**: Checker feedback history is always visible in context.
- **Positive**: RO-Crate assembly is straightforward — one metadata record per included file.
- **Positive**: Interdependency references don't need updating when a replacement is submitted.
- **Negative**: Metadata record grows over many revision cycles — acceptable given expected volumes (typically < 20 revisions per object in practice).
- **Negative**: Concurrent updates to the metadata record from different actors (researcher + checker) require optimistic locking. Implemented via a `version` counter on `OutputObjectMetadata`.
