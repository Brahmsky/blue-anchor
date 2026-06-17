# Blue Anchor GraphRAG

A domain-oriented GraphRAG prototype for ship maintenance and fault diagnosis.

This repository is based on MiniRAG and adds a ship-maintenance fault-card pipeline, graph-enhanced retrieval, hybrid chunk recall, a FastAPI backend, and a Vue 3 frontend.

## Features

- Fault-case extraction from maintenance documents
- Graph-enhanced retrieval over equipment, components, and fault cases
- Hybrid text and graph query modes
- FastAPI backend with streaming query support
- Vue 3 frontend for knowledge-base management, graph exploration, and RAG chat
- Benchmark and evaluation utilities

## Public Data Policy

This public repository does not include proprietary or real maintenance datasets.

The tracked `local_ship_docs` datasource is only a skeleton:

```text
datasources/local_ship_docs/
  datasource.yaml
  source/raw/.gitkeep
```

Use `examples/demo_datasource/` for a tiny hand-written demo, or prepare your own documents under a private datasource directory.

## Quick Start

```bash
uv sync
cp .env.example .env
uv run minirag-server
```

Run with the demo datasource explicitly:

```bash
uv run python -m minirag.api.minirag_server \
  --port 9733 \
  --datasource-root ./examples/demo_datasource
```

Frontend:

```bash
cd frontend
pnpm install
pnpm run dev
```

## Datasource Layout

Each datasource follows this shape:

```text
<datasource>/
  datasource.yaml
  source/raw/      # input documents
  staging/         # generated intermediate files, ignored by git
  outputs/         # generated graph, cards, indexes, exports, ignored by git
```

Generated datasource artifacts are intentionally ignored. Do not commit real raw documents, extracted chunks, graph workdirs, vector indexes, fault cards, benchmark outputs, or model-generated private content.

## Common Commands

```bash
uv run pytest
uv run pytest tests/test_faultcase_fast_query.py -v
uv run pytest --cov=minirag --cov-report=term
```

Data pipeline scripts live in `data_pipeline/scripts/` and should be run against your own datasource with `--datasource-root` or `--datasource-id`.

## License and Attribution

This project is based on MiniRAG. The original MIT license and copyright notice are retained in `LICENSE`.

Additional modifications are made for domain-specific ship maintenance GraphRAG experiments.
