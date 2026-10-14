#!/bin/sh
# Run one command inside the pinned EmonOS host builder.
#
# Usage: ci/builder/run.sh <image> <download-dir> <ccache-dir> <command> [argument...]
#
# The checkout is mounted at /workspace, so Buildroot paths are identical on
# every CI run. The container receives only the checkout, the Buildroot
# download cache, the ccache directory and the Docker socket required by
# buildroot-external/app/preload-images.sh.
#
# A mounted Docker socket gives the command control of the Docker daemon, which
# is equivalent to root on the host. Use this wrapper only on ephemeral runners.
set -eu

image=${1:?usage: ci/builder/run.sh <image> <download-dir> <ccache-dir> <command> [argument...]}
download_dir=${2:?missing download directory}
ccache_dir=${3:?missing ccache directory}
shift 3
test "$#" -gt 0 || {
    echo 'missing command to run in the builder' >&2
    exit 2
}

repo_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
mkdir -p "$download_dir" "$ccache_dir"
docker_socket=/var/run/docker.sock
docker_gid=$(stat -c %g "$docker_socket")

exec docker run --rm \
    --user "$(id -u):$(id -g)" \
    --group-add "$docker_gid" \
    --env HOME=/tmp \
    --env BR2_DL_DIR=/cache/dl \
    --env BR2_CCACHE_DIR=/cache/ccache \
    --env BR_CACHE_DIR=/cache/ccache \
    --env CCACHE_DIR=/cache/ccache \
    --env CCACHE_MAXSIZE="${EMONOS_CCACHE_MAXSIZE:-3G}" \
    --volume "$repo_dir:/workspace" \
    --volume "$download_dir:/cache/dl" \
    --volume "$ccache_dir:/cache/ccache" \
    --volume "$docker_socket:$docker_socket" \
    --workdir /workspace \
    "$image" "$@"
