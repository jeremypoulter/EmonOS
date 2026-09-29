#!/usr/bin/env python3
"""Make a Docker save archive independent of daemon map order and tar headers.

Docker may emit the same pinned images in different manifest/member orders,
and its directory headers can contain timestamps from previous pulls. Keep
the Docker load format, but sort members and image manifests and normalize
the tar metadata to SOURCE_DATE_EPOCH.
"""

import io
import json
import sys
import tarfile
from pathlib import Path


def canonical_json(name: str, source: tarfile.TarFile) -> bytes:
    member = source.extractfile(name)
    if member is None:
        raise ValueError(f"missing Docker metadata: {name}")
    value = json.load(member)
    if name == "manifest.json":
        value.sort(key=lambda image: tuple(image["RepoTags"]))
    elif name == "index.json":
        value["manifests"].sort(
            key=lambda image: image["annotations"]["io.containerd.image.name"]
        )
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonicalize(source_path: Path, output_path: Path, epoch: int) -> None:
    if source_path == output_path:
        raise ValueError("source and output must differ")
    with tarfile.open(source_path, "r:") as source, tarfile.open(
        output_path, "w:", format=tarfile.USTAR_FORMAT
    ) as output:
        members = source.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("Docker archive contains duplicate member names")
        for member in sorted(members, key=lambda entry: entry.name):
            if not (member.isdir() or member.isfile()):
                raise ValueError(f"unsupported Docker archive member: {member.name}")
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.mtime = epoch
            member.mode = 0o755 if member.isdir() else 0o644
            if member.isdir():
                output.addfile(member)
            elif member.name in ("index.json", "manifest.json", "repositories"):
                data = canonical_json(member.name, source)
                member.size = len(data)
                output.addfile(member, io.BytesIO(data))
            else:
                blob = source.extractfile(member)
                if blob is None:
                    raise ValueError(f"missing archive content: {member.name}")
                output.addfile(member, blob)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: canonicalize-docker-archive.py input.tar output.tar epoch")
    canonicalize(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
