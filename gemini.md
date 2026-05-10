# PRIME DIRECTIVE

This repository is a quantitative research environment for limit-order-book microstructure modeling. It is not an order-routing, discretionary strategy, or execution platform. Every change must preserve reproducibility, temporal correctness, and clean separation between ingestion, data construction, and model training.

## Research Integrity

- Look-ahead bias is prohibited. Features, labels, validation windows, and diagnostics must never use information unavailable at the prediction timestamp.
- Data normalization must be backward-looking only. Use rolling statistics computed strictly from observations at or before time `t`; never fit scalers on the full dataset before splitting.
- Preserve event ordering by symbol, timestamp, and sequence. Any resampling, imputation, or filtering must be explicit and auditable.
- Missing ticks, partial books, crossed books, and outliers must be handled deterministically. Do not silently discard data unless the rule is documented and covered by tests.

## Validation Standards

- Time-series experiments must use Purged K-Fold or Walk-Forward cross-validation.
- Random train/test splits are forbidden for LOB experiments.
- Validation gaps must purge samples whose feature windows or target horizons overlap evaluation periods.
- Report metrics by time window and symbol where possible so instability is visible.

## Infrastructure Rules

- Data ingestion must remain containerized. Production collection runs through Docker and the daemon entry point, not ad hoc scripts.
- The PyTorch Lightning `LOBDataModule` is the single source of truth for tensor creation.
- Model code must consume tensors produced by the DataModule rather than rebuilding feature windows independently.
- C++ acceleration must stay behind stable Python interfaces and must preserve the DataModule contract. (To build: `uv venv && source .venv/bin/activate && uv pip install -e ".[dev]" && cmake -S . -B build && cmake --build build`)
- Raw snapshots should remain replayable append-only records. Derived datasets can be regenerated from the JSONL source.

## Target Definition

The prediction target is the volume-weighted mid-price return over the next `k` ticks:

```text
target[t, k] = volume_weighted_mid[t + k] / volume_weighted_mid[t] - 1
```

The horizon `k` is measured in observed snapshots for the same symbol, not wall-clock seconds.

## Interfaces

- `CryptoFeedAgent(snapshot_queue, symbols, depth=10)` emits normalized `L2Snapshot` records from Kraken L2 books.
- `L2JsonlWriter(snapshot_queue, output_dir)` appends one JSON object per snapshot.
- `LOBDataModule(data_paths, window_size=100, horizon=10, batch_size=64)` owns snapshot loading, feature windowing, target alignment, and dataloader creation.
- `DeepLOBCNNLSTM(num_features=40, hidden_size=64, num_lstm_layers=2, dropout=0.1)` returns one scalar future-return prediction per sample.

## Environment

```bash
KRAKEN_SYMBOLS=BTC,ETH
KRAKEN_BOOK_DEPTH=10
SNAPSHOT_QUEUE_SIZE=5000
L2_PERSIST_JSONL=true
L2_JSONL_OUTPUT_DIR=data/l2
LOB_WINDOW_SIZE=100
LOB_TARGET_HORIZON=10
LOB_BATCH_SIZE=64
```

## Required Checks

```bash
pytest
rg -n -i "look-ahead|random split|full-dataset scaler" strategies tests
```
