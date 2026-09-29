"""Reproducible Docker archive headers and manifest ordering."""

import hashlib
import io
import json
import runpy
import tarfile
from pathlib import Path


def make_archive(path: Path, timestamp: int, reverse: bool) -> None:
    images = [
        {"Config": f"blobs/sha256/{name}", "RepoTags": [f"emonos/{name}:1"], "Layers": []}
        for name in ("a", "b")
    ]
    if reverse:
        images.reverse()
    with tarfile.open(path, "w:") as archive:
        index = {"schemaVersion": 2, "manifests": [
            {"annotations": {"io.containerd.image.name": name}}
            for name in (("b", "a") if reverse else ("a", "b"))
        ]}
        members = [
            ("blobs", None),
            ("manifest.json", json.dumps(images).encode()),
            ("index.json", json.dumps(index).encode()),
        ]
        if reverse:
            members.reverse()
        for name, content in members:
            info = tarfile.TarInfo(name)
            info.mtime = timestamp
            if content is None:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))


def test_canonical_archive_ignores_export_order_and_timestamps(tmp_path: Path) -> None:
    script = runpy.run_path(
        str(Path(__file__).parents[1] / "buildroot-external/app/canonicalize-docker-archive.py")
    )
    paths = [tmp_path / "first.tar", tmp_path / "second.tar"]
    outputs = [tmp_path / "first-normal.tar", tmp_path / "second-normal.tar"]
    make_archive(paths[0], 100, reverse=False)
    make_archive(paths[1], 200, reverse=True)
    for source, output in zip(paths, outputs):
        script["canonicalize"](source, output, 1700000000)
    assert hashlib.sha256(outputs[0].read_bytes()).digest() == hashlib.sha256(
        outputs[1].read_bytes()
    ).digest()
    with tarfile.open(outputs[0]) as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        assert [item["RepoTags"][0] for item in manifest] == ["emonos/a:1", "emonos/b:1"]
