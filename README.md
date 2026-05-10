# Microstructure

Microstructure is an institutional-grade Limit Order Book (LOB) research pipeline for high-frequency price dynamics. The system ingests asynchronous Level 2 order-book updates, converts them into reproducible training records, and provides a DeepLOB-style CNN-LSTM architecture for modeling short-horizon volume-weighted mid-price returns from 10-level bid/ask structure.

## Production Infrastructure

The ingestion daemon subscribes to Kraken L2 WebSocket `book` streams and maintains local 10-level bid and ask books per symbol. Each normalized `L2Snapshot` is written as append-only JSONL so training jobs can consume deterministic, replayable records without depending on a running exchange connection.

```text
Kraken L2 WebSocket -> CryptoFeedAgent -> L2Snapshot queue -> L2JsonlWriter
                                                            |
                                                            +-> LOBDataModule
                                                            +-> DeepLOB CNN-LSTM
```

The containerized daemon is designed for long-running collection. Docker owns the runtime environment, `.env` controls symbols and persistence settings, and mounted volumes retain distributed snapshot shards under `data/l2`.

## Rigorous Research Methodology

Market microstructure data is noisy, asynchronous, and frequently incomplete. The pipeline handles this by sorting snapshots by symbol, timestamp, and sequence; padding missing book levels with neutral values; transforming price levels relative to the current volume-weighted mid; and preserving raw event order for offline audits. Tensor creation flows through the PyTorch Lightning `LOBDataModule`, which builds rolling LOB windows and the prediction target:

```text
volume_weighted_mid[t + k] / volume_weighted_mid[t] - 1
```

Research code must avoid look-ahead bias. Normalization for experiments should use backward-looking rolling windows only, and validation should use Purged K-Fold or Walk-Forward splits rather than random train/test splits.

## C++ Acceleration Layer

The research DataModule can delegate CPU-bound LOB preprocessing to an optional C++17/pybind11 extension. The extension builds relative price/volume features, applies backward-looking rolling normalization, constructs per-symbol rolling windows, and aligns future-return targets while releasing the Python GIL. If the compiled module is unavailable, the same `LOBDataModule` API falls back to the reference Python/NumPy implementation.

Build the extension locally:

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cmake -S . -B build
cmake --build build
```

## Quick Start

Clone the repository:

```bash
git clone https://github.com/nxd914/microstructure.git
cd microstructure
```

Start the ingestion daemon with Docker:

```bash
cp .env.example .env 2>/dev/null || true
docker compose -f deploy/docker-compose.yml up --build
```

Default ingestion settings:

```bash
KRAKEN_SYMBOLS=BTC,ETH
KRAKEN_BOOK_DEPTH=10
SNAPSHOT_QUEUE_SIZE=5000
L2_PERSIST_JSONL=true
L2_JSONL_OUTPUT_DIR=data/l2
```

Run the local research and regression suite:

```bash
source .venv/bin/activate
pytest
```

## Repository Layout

```text
strategies/crypto/daemon.py          Ingestion daemon entry point
strategies/crypto/agents/            Kraken L2 feed agent
strategies/crypto/core/              Config, L2 models, JSONL writer, logging
strategies/crypto/research/          DataModule, targets, DeepLOB model scaffold
core/                                Shared runtime utilities
deploy/                              Container and service files
tests/                               Feed, storage, target, data, model tests
```

## License

Proprietary. All rights reserved.
