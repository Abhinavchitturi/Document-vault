# RAG Pipeline with Hybrid Search — Project Brief

## Goal
Build a production-style RAG system that answers questions over a document set, using
hybrid retrieval (vector + keyword) with reranking, and measurable eval numbers at each
stage. This is a learning + resume project — depth and honest evaluation matter more than
speed.

## Dataset
- BEIR's **Natural Questions (NQ)** subset (single-hop QA over Wikipedia passages) for v1.
- Stretch goal once v1 works end-to-end: **HotpotQA** (multi-hop questions) as a v2 harder eval set.
- BEIR datasets: https://github.com/beir-cellar/beir — has ready-made corpus + queries + qrels
  (ground-truth relevance judgments), which makes eval straightforward.

## Tech stack (chosen to match existing skills: Python, FastAPI, Docker, AWS, MongoDB)
- **Chunking/ingestion**: plain Python, ~300–500 token chunks with overlap
- **Embeddings**: `sentence-transformers`, model `BAAI/bge-small-en-v1.5` (small, fast, strong for its size) or `intfloat/e5-base-v2`
- **Vector store**: Qdrant (Docker) or Chroma (simpler, embedded) — pick Qdrant if you want closer-to-production experience
- **Keyword index**: `rank_bm25` (simple, in-process) to start; Elasticsearch later if you want it resume-recognizable
- **Fusion**: Reciprocal Rank Fusion (RRF) to merge vector + BM25 rankings
- **Reranker**: cross-encoder, `BAAI/bge-reranker-base`
- **Generation**: any LLM API (swap-in provider, keep this decoupled)
- **API**: FastAPI
- **Eval**: Recall@k, MRR@k (using BEIR's qrels), plus end-to-end answer accuracy against NQ gold answers
- **Containerization**: Docker Compose (app + vector DB)

## Architecture (request path)
```
query
  -> [BM25 search]         -> top-k candidates A
  -> [vector search]       -> top-k candidates B
  -> [RRF fusion of A + B] -> fused top-k
  -> [cross-encoder rerank]-> reranked top-n
  -> [LLM generation]      -> final answer, cited to source chunks
```

## Build order (each step should end with a working, measurable pipeline)
1. **Ingestion + chunking** — load NQ corpus subset, chunk, store chunk metadata (doc id, source, position)
2. **Vector-only retrieval** — embed chunks, embed queries, cosine search, get a baseline Recall@10 / MRR@10 number
3. **Add BM25 + RRF fusion** — measure improvement over step 2's baseline
4. **Add cross-encoder reranking** — measure improvement over step 3
5. **Wrap in FastAPI** — `/query` endpoint, returns answer + cited chunks
6. **Generation layer** — plug in an LLM to turn retrieved chunks into a real answer
7. **Dockerize** — app + vector DB via docker-compose
8. (Optional stretch) minimal UI, HotpotQA multi-hop eval

## Definition of "done" for v1
- Can answer a held-out set of NQ questions
- Has a documented Recall@k / MRR@k number for each retrieval strategy (vector-only vs hybrid vs hybrid+rerank), so the improvement is provable, not just claimed
- Runs via `docker-compose up`
- README explains architecture + eval results

## Notes for whoever picks this up (Claude Code)
- Start at step 1 regardless of what's already scaffolded — confirm ingestion + chunking works
  and inspect a few chunks manually before moving to embeddings.
- Keep retrieval and generation decoupled — the interesting engineering (and the eval story) is
  entirely in retrieval; generation is a thin final layer.
- At every step, save the eval number before moving to the next step. The before/after deltas are
  the whole point of this project.
