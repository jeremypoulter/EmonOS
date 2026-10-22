# Host build environment for EmonOS Buildroot runs.
#
# This image holds only host tools. Buildroot builds the x86-64 and aarch64
# target toolchains itself, so do not install target compilers here.
#
# Update the base digest and UBUNTU_SNAPSHOT together, then review the diff of
# the resulting image before merging.
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

# The base image has no CA bundle, and the snapshot service is HTTPS-only.
# Bootstrap ca-certificates from the base image's archive, then switch apt to a
# dated snapshot so later package versions stay fixed between rebuilds.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG UBUNTU_SNAPSHOT=20261001T000000Z
RUN rm -f /etc/apt/sources.list.d/ubuntu.sources \
 && printf '%s\n' \
        'Types: deb' \
        "URIs: https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT}" \
        'Suites: noble noble-updates noble-security' \
        'Components: main universe' \
        'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
        > /etc/apt/sources.list.d/ubuntu-snapshot.sources \
 && printf 'Acquire::Check-Valid-Until "false";\nAcquire::Retries "5";\n' > /etc/apt/apt.conf.d/99snapshot

# Buildroot host requirements (see buildroot/support/dependencies) plus the
# host libraries needed by its kernel, EDK2 and GRUB builds, and the Docker CLI
# used by buildroot-external/app/preload-images.sh.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends \
        bash bc bison build-essential bzip2 cpio diffutils docker-cli file \
        findutils flex g++ gawk gcc git gzip libelf-dev libncurses-dev \
        libssl-dev make mtools patch perl python3 rsync sed tar unzip wget \
        which xz-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
