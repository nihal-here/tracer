# Trace Architecture

Trace is a FastAPI application with a small browser client. It resolves an
immutable GitHub commit, investigates a local repository snapshot with
PydanticAI, reconstructs only validated evidence, and streams a grounded
answer over Server-Sent Events (SSE).

## Request flow

`POST /investigate` follows this sequence:

1. Resolve GitHub metadata and the exact branch commit SHA.
2. Materialize the commit into the persistent filesystem snapshot cache.
3. Read repository metadata, top-level names, and README information locally.
4. Filter noisy paths and build the deterministic repository map.
5. Look up the versioned investigation-result cache.
6. On a miss, run the PydanticAI agent with bounded `read_file`, `search_code`,
   and `list_directory` tools.
7. Validate every `EvidenceExcerpt` against observed `EvidenceSpan` line
   coverage, including delegated concrete implementations.
8. On success, deterministically merge overlapping excerpts into source
   citations and reconstruct only those selected lines.
9. Emit sanitized investigation steps and citation metadata, then stream the
   separate final answer model.
10. Validate answer citation tokens after streaming and emit completion.

The investigation agent and final answer remain separate model passes. No
additional model request is used for citations or the public trace.

## SSE contract

Each event is encoded as one JSON object in an SSE `data:` frame. A normal
successful flow is:

```text
metadata
investigation_trace
citations
chunk (repeated)
completed
```

The event payloads are:

```json
{"metadata": {"repo": "...", "owner": "...", "sources": [...]}}
{"investigation_trace": [{"action_number": 1, "tool": "read_file", "path": "src/auth.py", "start_line": 10, "end_line": 24, "result_summary": "Read src/auth.py lines 10-24."}]}
{"citations": [{"citation_id": "1", "path": "src/auth.py", "start_line": 10, "end_line": 24, "commit_sha": "...", "url": "..."}]}
{"chunk": "The implementation ... [1]."}
{"completed": true}
```

`investigation_trace` contains observable actions only. It does not contain
model messages, prompts, tool-return contents, hidden reasoning, or host file
paths. A failed investigation does not emit citation metadata. Domain budget
termination emits a sanitized trace and a termination answer chunk but no
misleading citations.

The existing metadata and answer chunk payloads remain compatible. Completion
is now represented explicitly; clients that ignore unknown SSE payload keys can
continue consuming metadata and chunks.

## Evidence and citations

`EvidenceSpan` records what the investigation actually observed:

```text
path, start_line, end_line, content, source_action_index, truncated
```

`EvidenceExcerpt` records the line range selected by the validated structured
investigation result. `reconstruct_evidence_text` rejects any excerpt with a
coverage gap.

`SourceCitation` is generated only after that validation:

```text
citation_id, path, start_line, end_line, commit_sha, url
```

Citation IDs are stable strings (`"1"`, `"2"`, …) assigned after sorting by
repository-relative path and line range. Exact duplicates are removed and
overlapping ranges for the same path are merged. Citations are therefore
deterministic for the same validated investigation result.

The final answer prompt receives only selected citation blocks, each labeled
with its citation ID, path, line range, and reconstructed observed evidence.
It explicitly requires repository-specific claims to use supplied IDs and
forbids invented IDs, paths, and line ranges. The streamed answer is not
rewritten after generation. Its `[1]`-style tokens are validated afterward for
known IDs and malformed/unknown references and recorded in `InvestigationTrace`.

Immutable GitHub blob URLs are generated only when the owner, repository, path,
and a hexadecimal commit SHA pass local safety checks. Otherwise `url` is
`null`; citation correctness does not depend on URL generation.

## Cache compatibility

Repository snapshots are cached by provider, owner, repository, and exact
commit SHA. Investigation results are cached by repository identity, commit,
normalized question, model, prompt version, tool schema version, and workspace
policy version.

Cached investigations retain the structured result and serialized
`EvidenceSpan` objects. The same local validation, reconstruction, citation
generation, and URL generation run after a cache hit; no investigation-model
request is needed. Citation generation therefore does not invalidate existing
valid investigation caches, and no cache version bump is required for Phase G.

Cache writes remain atomic and only successful evidence-complete investigations
are persisted. Corrupt or incompatible entries are treated as misses.

## Internal versus public trace

Internal `InvestigationTrace` retains detailed local telemetry for evaluation:
action records, model usage, cache timings, evidence counts, citation counts,
answer citation validation, and latency. `AgentStepTrace` may contain detailed
metadata for local diagnostics.

The public trace is a separate deterministic projection. It exposes only:

```text
action_number
tool
repository-relative path or search query
observed line range when applicable
short result summary/count
```

Host filesystem paths, environment data, API keys, system prompts, raw tool
returns, and chain-of-thought are never copied into the public event.

## Frontend

A small existing vanilla JavaScript frontend now renders:

- streamed Markdown answer text;
- citation IDs with file and line ranges, linking to immutable GitHub URLs
  when safely available; and
- the sanitized investigation path.

No frontend framework or new dependency was added. The backend SSE contract is
the stable integration boundary for future richer citation highlighting.

## Dependencies and deliberately excluded systems

Phase G uses existing dataclasses, Pydantic models, FastAPI streaming, and
standard-library URL/regex utilities. No dependency was added. Trace does not
use LangChain, LlamaIndex, embeddings, a vector database, RAG infrastructure,
Langfuse, Redis, or provider-side Gemini context caching.

## Context-efficiency limitation

Bounded reads and selected final evidence reduce individual and answer-pass
context size. PydanticAI investigation history is intentionally replayed
normally for correctness. A ProcessHistory/history-compaction experiment was
tested and rejected because older tool outputs disappearing from conversational
memory caused the agent to reread files incorrectly. Exact investigation
caching mitigates repeated identical questions; history trimming remains
deferred.

## Security and Deployment (Phase H)

Trace is hardened for safe portfolio demonstration via structural and operational constraints:

- **Input Validation**: `InvestigateRequest` enforces a strict 1000-character maximum limit for questions. GitHub repository URLs are parsed using strict HTTPS/host requirements with aggressive unquoting and traversal character filtering (`..`) before any network interaction.
- **Rate Limiting**: IP-based rate limiting (default 5/min) is embedded in the request cycle. Proxied `X-Forwarded-For` identities are trusted *only* if explicitly enabled via `TRUST_X_FORWARDED_FOR="true"` to prevent bypassing limits in a direct-exposure deployment.
- **Concurrency Isolation**: A strict `asyncio.Semaphore` (default 2) caps concurrent investigations, immediately rejecting excess traffic with HTTP 503 rather than infinitely queuing. Client disconnects automatically cancel investigations via `asyncio.CancelledError`.
- **Markdown Sanitization**: Server-streamed answer Markdown is rendered via a vendored `marked.min.js` and explicitly sanitized via a vendored `DOMPurify` before any assignment to `innerHTML`. No other dynamic content modifies `innerHTML` unsafely.
- **Cache TTL**: Repository snapshots and investigation results use lazy Time-to-Live (TTL) cleanup triggered opportunistically during cache operations. Expired directories/files are reclaimed without a background daemon. Missing or expired caches simply trigger safe recomputation.
- **Security Headers**: All responses include strict `X-Content-Type-Options`, `Referrer-Policy`, and a tight `Content-Security-Policy` omitting unsafe inline scripts or unnecessary remote API connect domains.
- **Deployment Assumptions**: The backend operates in a stateless container (`Dockerfile` provided), resolving API credentials globally from `GEMINI_API_KEY`. Blocking I/O operations (like repository materialization) execute in `asyncio.to_thread` to preserve FastAPI event-loop health.

## Future UI and observability work

The current source list is intentionally simple. A future UI can make inline
`[1]` tokens clickable and show expandable source excerpts, while continuing to
use only citation metadata supplied by the backend. The existing structured
`InvestigationTrace` is sufficient for local development and evaluation; a
centralized tracing dependency should be considered only after a concrete
multi-process operational need appears.
