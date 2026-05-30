# RAG Design

RAG is treated as a governed memory path, not just a vector database.

## Ingestion Path

```text
Source Systems
  -> Document Loader
  -> Data Classification
  -> PII / Secret Detection
  -> Chunking
  -> Embedding
  -> Metadata Enrichment
  -> Vector / Hybrid Index
  -> Approved Search Corpus
```

## Runtime Retrieval Path

```text
User Question
  -> Identity and Tenant Context
  -> Query Rewrite
  -> Access-Control Filter
  -> Vector / Hybrid Search
  -> Reranking
  -> Policy Filter
  -> Context Pack with Citations
  -> Prompt Builder
  -> Model
```

## Required Metadata

Each chunk should carry:

```text
tenant_id
document_id
chunk_id
source_system
classification
access_policy
document_version
index_version
embedding_model
created_at
expires_at / retention class
```

## Observability

Capture:

```text
retrieval_query
query_rewrite
document_ids
chunk_ids
retrieval_scores
reranker_scores
index_version
context_pack_id
citation_coverage
zero_result_events
user_feedback
```

Use this to answer:

```text
Why did the model answer that way?
Which documents grounded the answer?
Was the user allowed to see those documents?
Did retrieval fail or did generation fail?
Which corpus version was used?
```

## Safety Rules

```text
Runtime conversation does not write directly into the RAG corpus.
RAG ingestion is separate from session memory.
Only approved documents are promoted to searchable corpus.
Access filters run before context reaches the prompt.
Citations are retained in the audit envelope.
```

