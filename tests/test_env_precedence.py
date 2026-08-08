"""Tests for where the robot's settings come from.

Two candidate .env files and a shell that may have exported the same name
means "the .env is correct" does not establish what the robot actually read.
The rule these tests pin down is the ordinary one: an export beats every file,
and the nearer file beats the more distant one.
"""

from pathlib import Path

from mesh_client.app import load_env


def _write(path: Path, body: str) -> Path:
    """Write a settings file.

    Args:
        path: Where to write.
        body: File contents.

    Returns:
        The path written.
    """
    path.write_text(body)
    return path


def test_the_nearer_file_wins(tmp_path):
    # The bug this replaces: editing the package .env changed nothing, because
    # the repository-root file was loaded afterwards with override=True.
    near = _write(tmp_path / "near.env", "LIVEKIT_URL=ws://192.168.4.90:7880\n")
    far = _write(tmp_path / "far.env", "LIVEKIT_URL=ws://192.168.4.71:7880\n")

    environ: dict[str, str] = {}
    load_env([near, far], environ)

    assert environ["LIVEKIT_URL"] == "ws://192.168.4.90:7880"


def test_an_export_beats_every_file(tmp_path):
    # Otherwise `export LIVEKIT_URL=...` appears to do nothing at all, which is
    # the opposite of what every other program does.
    near = _write(tmp_path / "near.env", "LIVEKIT_URL=ws://192.168.4.71:7880\n")

    environ = {"LIVEKIT_URL": "ws://192.168.4.90:7880"}
    load_env([near], environ)

    assert environ["LIVEKIT_URL"] == "ws://192.168.4.90:7880"


def test_the_distant_file_still_supplies_what_the_near_one_omits(tmp_path):
    # Both files are read; precedence is per variable, not per file.
    near = _write(tmp_path / "near.env", "LIVEKIT_URL=ws://192.168.4.90:7880\n")
    far = _write(tmp_path / "far.env", "MESH_CAMERA_ROTATION=180\n")

    environ: dict[str, str] = {}
    load_env([near, far], environ)

    assert environ["MESH_CAMERA_ROTATION"] == "180"


def test_the_source_of_each_value_is_reported(tmp_path):
    # This is what turns "it keeps saying .71" into a one-line answer.
    near = _write(tmp_path / "near.env", "LIVEKIT_URL=ws://192.168.4.90:7880\n")
    far = _write(tmp_path / "far.env", "MESH_CAMERA_ROTATION=180\n")

    source = load_env([near, far], {"MESH_AUDIO_IN_DEVICE": "1"})

    assert source["LIVEKIT_URL"] == str(near)
    assert source["MESH_CAMERA_ROTATION"] == str(far)
    assert source["MESH_AUDIO_IN_DEVICE"] == "exported in the shell"


def test_no_files_leaves_the_environment_alone(tmp_path):
    environ = {"LIVEKIT_URL": "ws://192.168.4.90:7880"}
    load_env([], environ)
    assert environ == {"LIVEKIT_URL": "ws://192.168.4.90:7880"}
