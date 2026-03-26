# anomalica-digester

The digester is the knowledge graph extraction engine for [Anomalica](https://anomalica.is), an international reference platform for documenting anomalous phenomena.

It sits in the middle of the Anomalica pipeline:

```
anomalica-ingester -> anomalica-digester -> anomalica-assembler -> anomalica-content -> anomalica-site
```

## Purpose

The digester consumes pre-ingested records produced by [anomalica-ingester](https://github.com/anomalica/anomalica-ingester) and decomposes them into atomic claims stored in a knowledge graph. It does not process raw source material (PDFs, audio, video, web pages) directly - the ingester converts those into the record interchange format (markdown with YAML frontmatter and annotation blocks) before the digester sees them.

Its core responsibilities are:

- **Claim extraction** - decompose records into the smallest independently verifiable factual assertions, each linked to its source with precise location references
- **Entity identification and linking** - resolve people, organisations, locations, events, and objects across records and languages
- **Provenance chains** - trace how each claim reached the knowledge graph, distinguishing genuine corroboration from echo chambers
- **Evidence scoring** - algorithmically score claims based on independent corroboration, attestation depth, source track record, contradictions, and evidence quality

All scoring is algorithmic and transparent. No human assigns scores. The methodology is published and reproducible.

## Output

A SQLite knowledge graph database. This includes claims, facts, entities, relationships, provenance chains, and evidence scores, among other things.

## Pipeline

The digester operates a three-phase pipeline:

1. **Ingest** - catalogue and summarise source documents in any language
2. **Integrate** - extract structured claims with awareness of the existing graph, avoiding duplication
3. **Consolidate** - deduplicate claims via embedding similarity, detect corroboration and contradiction

All claims are normalised to canonical English before embedding, regardless of source language. Original-language text is preserved in provenance metadata.

