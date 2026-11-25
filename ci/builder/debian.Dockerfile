# Host build environment for the EmonOS Debian variant (VARIANT=debian).
#
# Adapted from ramcq's mkosi proof of concept. The Ubuntu builder next to this
# file is for Buildroot; mkosi needs the newer Debian trixie packages.
#
# Every tool here is a Debian package. Nothing is compiled except (on the
# Pi) U-Boot, because EmonOS's boot-state layout needs a custom config.
# Pin the base digest and DEBIAN_SNAPSHOT together for reproducible builds.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 TZ=UTC

ARG DEBIAN_SNAPSHOT=20261120T000000Z
RUN if [ -n "$DEBIAN_SNAPSHOT" ]; then \
      printf '%s\n' \
        'Types: deb' \
        "URIs: https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}" \
        'Suites: trixie trixie-updates' 'Components: main' \
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' '' \
        'Types: deb' \
        "URIs: https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}" \
        'Suites: trixie-security' 'Components: main' \
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
        > /etc/apt/sources.list.d/debian.sources; \
      echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99snapshot; \
    fi

# mkosi turns .debs into a root tree; the rest is EmonOS's existing
# image assembly (genimage, RAUC bundles, GRUB/U-Boot environments).
RUN apt-get update && apt-get install -y --no-install-recommends \
      mkosi apt dpkg debian-archive-keyring ca-certificates curl gpg git openssl \
      squashfs-tools genimage rauc dosfstools mtools e2fsprogs \
      grub-efi-amd64-bin grub-common u-boot-tools ovmf python3 docker-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
