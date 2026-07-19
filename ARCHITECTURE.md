# Trace Architecture

Trace is a small FastAPI application with a static browser client and one
streaming investigation endpoint. It uses GitHub for repository data and
Gemini through PydanticAI for repository investigation and final answer
generation.

## Request flow

`POST /investigate` follows this sequence:

1. Resolve the GitHub repository metadata and default-branch commit SHA.
2. Materialize the exact commit into the persistent filesystem snapshot cache.
   A cache miss downloads and safely extracts the GitHub tarball; a hit reuses
   the already extracted files.
3. Read top-level file names and README content from the local snapshot.
4. Filter noisy paths and build the deterministic hierarchical repository map.
5. Build a versioned investigation-cache key from the repository identity,
   commit SHA, normalized question, model, prompt version, tool version, and
   workspace-policy version.
6. On an investigation-cache hit, restore the structured result and gathered
   evidence without making investigation-model requests. On a miss, run the
   PydanticAI agent with `read_file`, `search_code`, and `list_directory`.
7. Persist only successful, evidence-complete investigation results using an
   atomic filesystem write.
8. Generate the final answer in a separate Gemini streaming request. This
   remains separate even when investigation evidence came from cache.
9. Emit answer chunks as Server-Sent Events and release the request's snapshot
   handle. Persistent cached snapshots are retained.

The current implementation does not use Redis, a database, embeddings, RAG,
or Gemini provider-side context caching.

## Caches

Both caches use hashed filenames and never place repository names or questions
directly in paths.

### Repository snapshot cache

The snapshot key is derived from:

```text
provider + owner + repository + exact commit SHA
```

The cache stores safely extracted files and a completion manifest. Population
uses a temporary directory followed by an atomic directory rename. A simple
per-key in-process lock prevents concurrent population of the same snapshot
within one process. Corrupt, incomplete, expired, or incompatible entries are
ignored and rebuilt.

### Investigation-result cache

The investigation key includes:

```text
provider
owner
repository
exact commit SHA
normalized question
effective investigation model
investigation prompt version
tool schema version
workspace policy version
```

Question normalization trims leading/trailing whitespace and collapses
repeated whitespace. It does not lowercase or otherwise rewrite meaning.

Only successful `model_finished` investigations are cached. Exceptions,
budget terminations, invalid actions, incomplete work, and partial writes are
not cached. Malformed entries are cache misses.

The default cache root is the operating system temporary directory under
`trace-cache`. It can be changed with:

```text
TRACE_CACHE_DIR=/path/to/cache
TRACE_SNAPSHOT_CACHE_TTL_SECONDS=86400
```

If a project-local cache directory is used, `.trace-cache/` should remain
ignored by Git. Cache failures degrade to the uncached path wherever possible.

## Observability

Trace logs aggregate request metrics including model requests, input/output
tokens, evidence size, action counts, cache hits, and cache lookup/write
durations.

PydanticAI 2.13's public `AgentRunResult.all_messages()` API is used to inspect
each public `ModelResponse.usage`. Per-response metrics include request number,
input/output tokens, cumulative totals, cache read/write token fields, and the
names of tool results immediately preceding the request. Per-request model
latency is not exposed through this path and is not estimated.

On an investigation-cache hit, current-request investigation usage remains
zero. Historical usage is not copied into current-request counters.

Centralized production observability (e.g. OpenTelemetry/Langfuse) may be added after deployment/multi-user operation. Current structured InvestigationTrace is sufficient for local evaluation and optimization.

## Evaluation safety

The evaluation runner is explicitly opt-in:

```text
python -m evals.runner
python -m evals.runner --case requests-session-002
python -m evals.runner --all --confirm-live
```

The first command only prints usage. The full suite is rejected without
`--confirm-live`. Diagnostics follow the same explicit-selection policy:

```text
python -m evals.run_diagnostics --case httpx-transport-004
python -m evals.run_diagnostics --all --confirm-live
```

Offline unit tests do not start either live runner.

## Deliberately separate final-answer pass

The investigation agent currently returns structured evidence metadata while
the final-answer service owns presentation and streaming. Combining them could
save one model pass and avoid resending evidence, but it would couple evidence
validation, retry behavior, answer formatting, and model selection. It would
also make streaming structured output more complex. This remains a future
optimization, not part of the current cache phase.

## Future: Investigation Context Efficiency

* Phase F bounded reads reduce individual tool-result size.
* Final-answer evidence reconstruction dramatically reduces final-answer context.
* PydanticAI investigation history remains a cost hotspot.
* Experimental removal/compaction of older ToolReturnPart content caused behavioral regressions.
* Production therefore currently preserves full PydanticAI history for correctness.
* Future options include:
    * structured external working memory
    * model-visible compact evidence ledger
    * summarization/checkpointing designed into the agent state
    * manual multi-step orchestration with controlled message history
    * provider-side context caching after architecture stabilizes
    * revisiting PydanticAI capabilities if newer framework versions provide safer memory management
