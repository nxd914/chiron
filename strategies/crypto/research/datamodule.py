"""PyTorch Lightning data scaffold for DeepLOB-style L2 research."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset, Subset

from strategies.crypto.core.models import L2Snapshot
from strategies.crypto.research.targets import future_return

try:
    from lightning import LightningDataModule
except ImportError:  # pragma: no cover - keeps import errors focused when Lightning is absent.
    class LightningDataModule:  # type: ignore[no-redef]
        pass

DEFAULT_DEPTH = 10
DEFAULT_ROLLING_NORM_WINDOW = 256


def load_snapshots(data_paths: Sequence[str | Path]) -> list[L2Snapshot]:
    snapshots: list[L2Snapshot] = []
    for path in data_paths:
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    snapshots.append(L2Snapshot.from_dict(json.loads(line)))
    snapshots.sort(key=lambda snapshot: (snapshot.symbol, snapshot.timestamp, snapshot.sequence))
    return snapshots


def snapshot_to_feature_vector(snapshot: L2Snapshot, depth: int = 10) -> list[float]:
    """Return raw `[bid_px, bid_vol, ask_px, ask_vol]` features for `depth` levels."""
    reference_price = snapshot.volume_weighted_mid
    if not math.isfinite(reference_price) or reference_price <= 0:
        reference_price = snapshot.mid
    if not math.isfinite(reference_price) or reference_price <= 0:
        raise ValueError("snapshot has no valid reference price")

    bids = list(snapshot.bids[:depth])
    asks = list(snapshot.asks[:depth])
    bid_prices = [(level.price / reference_price) - 1.0 for level in bids]
    ask_prices = [(level.price / reference_price) - 1.0 for level in asks]
    bid_volumes = [math.log1p(max(level.volume, 0.0)) for level in bids]
    ask_volumes = [math.log1p(max(level.volume, 0.0)) for level in asks]

    bid_prices.extend([0.0] * (depth - len(bid_prices)))
    ask_prices.extend([0.0] * (depth - len(ask_prices)))
    bid_volumes.extend([0.0] * (depth - len(bid_volumes)))
    ask_volumes.extend([0.0] * (depth - len(ask_volumes)))
    return bid_prices + bid_volumes + ask_prices + ask_volumes


def _load_cpp_backend():
    try:
        from strategies.crypto import _cpp_lob
    except ImportError:
        return None
    return _cpp_lob


def _snapshots_to_arrays(
    snapshots: Sequence[L2Snapshot],
    depth: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(snapshots)
    bid_prices = np.zeros((n, depth), dtype=np.float64)
    bid_volumes = np.zeros((n, depth), dtype=np.float64)
    ask_prices = np.zeros((n, depth), dtype=np.float64)
    ask_volumes = np.zeros((n, depth), dtype=np.float64)
    symbol_ids = np.zeros(n, dtype=np.int64)
    symbol_map: dict[str, int] = {}

    for row, snapshot in enumerate(snapshots):
        symbol_ids[row] = symbol_map.setdefault(snapshot.symbol, len(symbol_map))
        for level, book_level in enumerate(snapshot.bids[:depth]):
            bid_prices[row, level] = book_level.price
            bid_volumes[row, level] = book_level.volume
        for level, book_level in enumerate(snapshot.asks[:depth]):
            ask_prices[row, level] = book_level.price
            ask_volumes[row, level] = book_level.volume

    return bid_prices, bid_volumes, ask_prices, ask_volumes, symbol_ids


def _reference_prices(
    bid_prices: np.ndarray,
    bid_volumes: np.ndarray,
    ask_prices: np.ndarray,
    ask_volumes: np.ndarray,
) -> np.ndarray:
    bid_volumes = np.maximum(bid_volumes, 0.0)
    ask_volumes = np.maximum(ask_volumes, 0.0)
    valid_bids = np.isfinite(bid_prices) & (bid_prices > 0)
    valid_asks = np.isfinite(ask_prices) & (ask_prices > 0)

    bid_notional = np.where(valid_bids, bid_prices * bid_volumes, 0.0).sum(axis=1)
    ask_notional = np.where(valid_asks, ask_prices * ask_volumes, 0.0).sum(axis=1)
    total_volume = np.where(valid_bids, bid_volumes, 0.0).sum(axis=1) + np.where(
        valid_asks, ask_volumes, 0.0
    ).sum(axis=1)
    references = np.divide(
        bid_notional + ask_notional,
        total_volume,
        out=np.full(total_volume.shape, np.nan, dtype=np.float64),
        where=total_volume > 0,
    )

    invalid = ~np.isfinite(references) | (references <= 0)
    if invalid.any():
        best_bids = bid_prices[:, 0]
        best_asks = ask_prices[:, 0]
        mids = (best_bids + best_asks) / 2.0
        references[invalid] = mids[invalid]

    if (~np.isfinite(references) | (references <= 0)).any():
        raise ValueError("snapshot has no valid reference price")
    return references


def _raw_feature_matrix(
    bid_prices: np.ndarray,
    bid_volumes: np.ndarray,
    ask_prices: np.ndarray,
    ask_volumes: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    references = _reference_prices(bid_prices, bid_volumes, ask_prices, ask_volumes)
    raw = np.concatenate(
        [
            np.where(bid_prices > 0, (bid_prices / references[:, None]) - 1.0, 0.0),
            np.log1p(np.maximum(bid_volumes, 0.0)),
            np.where(ask_prices > 0, (ask_prices / references[:, None]) - 1.0, 0.0),
            np.log1p(np.maximum(ask_volumes, 0.0)),
        ],
        axis=1,
    )
    raw = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0)
    return raw, references


def _contiguous_symbol_ranges(symbol_ids: np.ndarray) -> list[tuple[int, int]]:
    if len(symbol_ids) == 0:
        return []

    ranges: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(symbol_ids)):
        if symbol_ids[index] != symbol_ids[start]:
            ranges.append((start, index))
            start = index
    ranges.append((start, len(symbol_ids)))
    return ranges


def _backward_rolling_normalize(
    raw: np.ndarray,
    symbol_ids: np.ndarray,
    rolling_norm_window: int,
) -> np.ndarray:
    normalized = np.zeros_like(raw, dtype=np.float64)
    for start, end in _contiguous_symbol_ranges(symbol_ids):
        for row in range(start, end):
            norm_start = (
                max(start, row - rolling_norm_window + 1)
                if rolling_norm_window > 0
                else start
            )
            history = raw[norm_start : row + 1]
            means = history.mean(axis=0)
            stds = history.std(axis=0)
            stds = np.where(stds > 1e-8, stds, 1.0)
            normalized[row] = (raw[row] - means) / stds
    return np.nan_to_num(normalized, nan=0.0, posinf=0.0, neginf=0.0)


def _build_lob_windows_python(
    bid_prices: np.ndarray,
    bid_volumes: np.ndarray,
    ask_prices: np.ndarray,
    ask_volumes: np.ndarray,
    symbol_ids: np.ndarray,
    window_size: int,
    horizon: int,
    depth: int,
    rolling_norm_window: int,
) -> dict[str, np.ndarray]:
    if window_size <= 1:
        raise ValueError("window_size must be greater than 1")
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if len(symbol_ids) < window_size + horizon:
        raise ValueError("not enough snapshots for requested window and horizon")

    raw, weighted_mids = _raw_feature_matrix(bid_prices, bid_volumes, ask_prices, ask_volumes)
    features = _backward_rolling_normalize(raw, symbol_ids, rolling_norm_window)

    windows: list[np.ndarray] = []
    targets: list[float] = []
    for start, end in _contiguous_symbol_ranges(symbol_ids):
        length = end - start
        if length < window_size + horizon:
            continue
        last_start = length - window_size - horizon
        for local_start in range(last_start + 1):
            global_start = start + local_start
            windows.append(features[global_start : global_start + window_size])
            targets.append(future_return(weighted_mids[start:end], local_start + window_size - 1, horizon))

    if not windows:
        raise ValueError("no per-symbol windows available for requested window and horizon")

    return {
        "windows": np.stack(windows).astype(np.float32, copy=False),
        "targets": np.asarray(targets, dtype=np.float32),
    }


def build_lob_windows(
    snapshots: Sequence[L2Snapshot],
    window_size: int,
    horizon: int,
    depth: int = DEFAULT_DEPTH,
    rolling_norm_window: int = DEFAULT_ROLLING_NORM_WINDOW,
    use_cpp: bool = True,
) -> dict[str, np.ndarray]:
    arrays = _snapshots_to_arrays(snapshots, depth)
    if use_cpp:
        backend = _load_cpp_backend()
        if backend is not None:
            return backend.build_lob_windows(
                *arrays,
                window_size=window_size,
                horizon=horizon,
                depth=depth,
                rolling_norm_window=rolling_norm_window,
            )
    return _build_lob_windows_python(
        *arrays,
        window_size=window_size,
        horizon=horizon,
        depth=depth,
        rolling_norm_window=rolling_norm_window,
    )


class LOBSnapshotDataset(Dataset[tuple[torch.Tensor, torch.Tensor]]):
    """Rolling-window dataset over normalized L2 snapshot features."""

    def __init__(
        self,
        snapshots: Sequence[L2Snapshot],
        window_size: int = 100,
        horizon: int = 10,
        depth: int = DEFAULT_DEPTH,
        rolling_norm_window: int = DEFAULT_ROLLING_NORM_WINDOW,
        use_cpp: bool = True,
    ) -> None:
        built = build_lob_windows(
            snapshots,
            window_size=window_size,
            horizon=horizon,
            depth=depth,
            rolling_norm_window=rolling_norm_window,
            use_cpp=use_cpp,
        )
        self.windows = torch.from_numpy(built["windows"])
        self.targets = torch.from_numpy(built["targets"])
        self.features = self.windows
        self.targets = torch.nan_to_num(self.targets, nan=0.0, posinf=0.0, neginf=0.0)

    def __len__(self) -> int:
        return self.windows.shape[0]

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.windows[index], self.targets[index]


class LOBDataModule(LightningDataModule):
    """Lightning DataModule for JSONL L2 snapshots."""

    def __init__(
        self,
        data_paths: Sequence[str | Path],
        window_size: int = 100,
        horizon: int = 10,
        batch_size: int = 64,
        val_fraction: float = 0.2,
        test_fraction: float = 0.1,
        depth: int = DEFAULT_DEPTH,
        rolling_norm_window: int = DEFAULT_ROLLING_NORM_WINDOW,
        use_cpp: bool = True,
    ) -> None:
        super().__init__()
        self.data_paths = list(data_paths)
        self.window_size = window_size
        self.horizon = horizon
        self.batch_size = batch_size
        self.val_fraction = val_fraction
        self.test_fraction = test_fraction
        self.depth = depth
        self.rolling_norm_window = rolling_norm_window
        self.use_cpp = use_cpp
        self.train_dataset: Subset | None = None
        self.val_dataset: Subset | None = None
        self.test_dataset: Subset | None = None

    def setup(self, stage: str | None = None) -> None:
        dataset = LOBSnapshotDataset(
            load_snapshots(self.data_paths),
            self.window_size,
            self.horizon,
            depth=self.depth,
            rolling_norm_window=self.rolling_norm_window,
            use_cpp=self.use_cpp,
        )
        n = len(dataset)
        n_test = int(n * self.test_fraction)
        n_val = int(n * self.val_fraction)
        n_train = n - n_val - n_test
        if n_train <= 0:
            raise ValueError("split fractions leave no training samples")

        indices = list(range(n))
        self.train_dataset = Subset(dataset, indices[:n_train])
        self.val_dataset = Subset(dataset, indices[n_train : n_train + n_val])
        self.test_dataset = Subset(dataset, indices[n_train + n_val :])

    def train_dataloader(self) -> DataLoader:
        return DataLoader(self._require(self.train_dataset), batch_size=self.batch_size, shuffle=True)

    def val_dataloader(self) -> DataLoader:
        return DataLoader(self._require(self.val_dataset), batch_size=self.batch_size, shuffle=False)

    def test_dataloader(self) -> DataLoader:
        return DataLoader(self._require(self.test_dataset), batch_size=self.batch_size, shuffle=False)

    def _require(self, dataset: Subset | None) -> Subset:
        if dataset is None:
            raise RuntimeError("call setup() before requesting dataloaders")
        return dataset
