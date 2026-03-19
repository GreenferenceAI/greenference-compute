"""Unit tests for LocalVolumeManager."""

from __future__ import annotations

import pytest

from greenference_compute_agent.domain.volume import LocalVolumeManager, VolumeError


def test_create_volume(volume_manager):
    vol = volume_manager.create_volume(
        deployment_id="deploy-001",
        hotkey="hotkey",
        node_id="node-001",
        size_gb=10,
    )
    assert vol.status == "created"
    assert vol.size_gb == 10
    from pathlib import Path
    assert Path(vol.path).exists()


def test_delete_volume(volume_manager):
    vol = volume_manager.create_volume(
        deployment_id="deploy-002",
        hotkey="hotkey",
        node_id="node-001",
        size_gb=5,
    )
    from pathlib import Path
    assert Path(vol.path).exists()
    volume_manager.delete_volume(vol)
    assert not Path(vol.path).exists()


def test_backup_and_restore(volume_manager, tmp_path):
    vol = volume_manager.create_volume(
        deployment_id="deploy-003",
        hotkey="hotkey",
        node_id="node-001",
        size_gb=5,
    )
    # Write a file into the volume
    from pathlib import Path
    (Path(vol.path) / "test.txt").write_text("hello compute")

    backed = volume_manager.backup_volume(vol)
    assert backed.status == "backed_up"
    assert backed.backup_uri is not None
    assert backed.last_backed_up_at is not None

    # Modify the file, then restore
    (Path(vol.path) / "test.txt").write_text("modified")
    restored = volume_manager.restore_volume(backed, backed.backup_uri)
    assert restored.status == "attached"
    content = (Path(vol.path) / "volume" / "test.txt").read_text()
    assert content == "hello compute"


def test_backup_missing_volume_raises(volume_manager, sample_volume):
    with pytest.raises(VolumeError, match="volume path missing"):
        volume_manager.backup_volume(sample_volume)
