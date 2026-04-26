# ADR-0004 — RO-Crate: Default Profile, Release-Only Assembly

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

Research Object Crates (RO-Crate) provide a community standard for packaging research outputs with their metadata and provenance. trevor uses RO-Crate as the release format for approved egress requests.

Decisions required:
1. Which RO-Crate profile to target
2. When in the lifecycle to generate the crate
3. How to handle the metadata model within the crate

---

## Decision

### 1. Profile: RO-Crate 1.1 (default / base profile)

Use the base RO-Crate 1.1 specification without adopting a domain-specific profile (e.g. Workflow Run Crate, Bioschemas). Reasons:
- No suitable TRE-specific profile exists yet.
- The base profile is sufficient for packaging files with provenance metadata.
- trevor will add a small set of custom context extensions for TRE-specific metadata (airlock request ID, approval record, statbarn classifications) using the `@context` extension mechanism.
- If a community TRE profile emerges, trevor can adopt it via a single ADR update.

### 2. Assembly: At release only (C-11)

The RO-Crate is assembled as a final step in the `RELEASING` → `RELEASED` transition. There is no draft or in-progress crate maintained during the review lifecycle. The crate is assembled from:
- The database metadata at the point of release (all approved output object metadata)
- The approved files fetched from quarantine storage
- The approval record (both reviews)
- The audit trail summary

This avoids maintaining a live crate that can drift from the database state.

### 3. Crate structure

```
ro-crate-{request_id}/
├── ro-crate-metadata.json     ← RO-Crate 1.1 metadata document
├── ro-crate-preview.html      ← Human-readable HTML preview (generated)
└── data/
    ├── {object_id_v2}.md      ← Approved output objects (latest version only)
    ├── {object_id_v2}.csv
    ├── {object_id_v2}.png
    └── ...
```

Only the **latest approved version** of each logical output object is included in the release crate. Superseded versions are not included (they remain in quarantine storage for internal audit purposes).

### 4. `ro-crate-metadata.json` key entities

The metadata document will include:

- **Root dataset**: the airlock request (ID, title, description, project, submission date, approval date)
- **File entities**: one per approved output object, with:
  - `@id`: relative path to file
  - `name`, `description`: from OutputObjectMetadata
  - `encodingFormat`: MIME type
  - `sha256`: checksum
  - TRE extensions: `tre:statbarn`, `tre:researcherJustification`, `tre:suppressionNotes`
- **Person entities**: researcher and checkers
- **CreateAction**: the approval event, with `agent` (checkers), `result` (the dataset), `startTime` / `endTime`

### 5. Custom context extensions

```json
{
  "@context": [
    "https://w3id.org/ro/crate/1.1/context",
    {
      "tre": "https://karectl.example/trevor/context#",
      "tre:statbarn": { "@id": "tre:statbarn" },
      "tre:researcherJustification": { "@id": "tre:researcherJustification" },
      "tre:suppressionNotes": { "@id": "tre:suppressionNotes" },
      "tre:airlockRequestId": { "@id": "tre:airlockRequestId" },
      "tre:agentReviewSummary": { "@id": "tre:agentReviewSummary" }
    }
  ]
}
```

---

## Implementation

Use the `rocrate` Python library for crate assembly. It handles `ro-crate-metadata.json` generation and `ro-crate-preview.html` rendering.

The assembled crate directory is zipped and streamed to the release S3 bucket. The zip checksum is recorded in `ReleaseRecord`.

---

## Consequences

- **Positive**: Standard, interoperable format. Researchers get a portable, self-describing package.
- **Positive**: Custom context extensions are forwards-compatible with future TRE profiles.
- **Positive**: Assembly at release only keeps the DB as single source of truth during review.
- **Negative**: Preview HTML is auto-generated; it won't be as polished as a bespoke design. Accepted for v1.
