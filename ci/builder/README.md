# EmonOS host builder

`ci/builder/Dockerfile` defines the host environment for Buildroot. It contains
only host tools. Buildroot still builds every target toolchain, kernel and
rootfs inside the run.

## Pins

- The base image is pinned by digest (`ubuntu:24.04@sha256:…`).
- apt uses the dated Ubuntu snapshot in `UBUNTU_SNAPSHOT`, so package versions
  stay fixed between rebuilds.
- Change the digest and snapshot date together, then review the image diff.

## Running a build

`ci/builder/run.sh` runs one command in the builder with fixed paths:

| Host path | Container path | Purpose |
| --- | --- | --- |
| checkout | `/workspace` | Buildroot tree and `output/<target>` |
| download cache argument | `/cache/dl` | `BR2_DL_DIR` |
| ccache argument | `/cache/ccache` | `BR2_CCACHE_DIR` |
| `/var/run/docker.sock` | same | `preload-images.sh` pulls and saves images |

Example:

```sh
docker build -t emonos-builder:local ci/builder
ci/builder/run.sh emonos-builder:local "$HOME/.cache/emonos/dl" \
    "$HOME/.cache/emonos/ccache/x86-64-vm" make emonos_x86_64_vm
```

The wrapper runs as the caller's UID and GID, so outputs stay owned by the
checkout owner. It does not use any secrets or registry login. It sets
`CCACHE_MAXSIZE` to `EMONOS_CCACHE_MAXSIZE` (default `2G`) and writes a
per-run ccache statistics log to `/cache/ccache/statslog`.

## Security note

A mounted Docker socket gives the container control of the Docker daemon. That
is effectively root on the host. Use the builder only on ephemeral CI runners
or on a workstation you trust, and never with untrusted pull requests that can
change `run.sh` or the Dockerfile on a self-hosted runner.

## Caches

- Buildroot downloads are keyed by the pinned Buildroot submodule revision.
- Both defconfigs set `BR2_CCACHE=y`; the cache location comes from
  `BR2_CCACHE_DIR` (`/cache/ccache` in the builder, `~/.buildroot-ccache` locally).
- ccache is keyed per target by Buildroot revision, the target defconfig, the
  board files, this Dockerfile and the top-level Makefile.
- `output/` is never cached. It contains absolute build paths and is large.

## Reproducibility limits

The workflow verifies that repeated assembly into the same output path gives a
byte-identical image. It does not prove identical images across different
checkout paths or host toolchains. Buildroot's reproducibility mode documents
those limits.
