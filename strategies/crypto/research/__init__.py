"""Research utilities for DeepLOB-style modeling on L2 snapshots."""

__all__ = [
    "DeepLOBCNNLSTM",
    "LOBDataModule",
    "LOBSnapshotDataset",
    "build_lob_windows",
    "future_return",
    "snapshot_to_feature_vector",
]


def __getattr__(name: str):
    if name == "DeepLOBCNNLSTM":
        from .deeplob import DeepLOBCNNLSTM

        return DeepLOBCNNLSTM
    if name in {"LOBDataModule", "LOBSnapshotDataset", "build_lob_windows", "snapshot_to_feature_vector"}:
        from .datamodule import (
            LOBDataModule,
            LOBSnapshotDataset,
            build_lob_windows,
            snapshot_to_feature_vector,
        )

        return {
            "LOBDataModule": LOBDataModule,
            "LOBSnapshotDataset": LOBSnapshotDataset,
            "build_lob_windows": build_lob_windows,
            "snapshot_to_feature_vector": snapshot_to_feature_vector,
        }[name]
    if name == "future_return":
        from .targets import future_return

        return future_return
    raise AttributeError(name)
