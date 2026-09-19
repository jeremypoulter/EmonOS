# EmonOS

EmonOS is a minimal, immutable host OS for the emoncms container stack.

## Development status

The repository currently implements the first two PoC milestones: a Buildroot-based x86-64
UEFI VM image that boots to a systemd serial console and runs Docker containers. A/B slots,
RAUC, the persistent data partition, and the emoncms application stack are subsequent
milestones documented in `Docs/`.

## Build the x86 VM image

```sh
make emonos_x86_64_vm
```

The image is written to `output/x86-64-vm/images/emonos-x86-64-vm.img`.

## Run the x86 VM

```sh
make run_x86_64_vm
```

The guest uses the serial console. Log in as `root`; the development image has no root
password.

Docker is available after boot. The current writable-root development image can verify the
runtime with:

```sh
docker run --rm hello-world
```

## Dependencies

Buildroot downloads and builds its own toolchain. The host needs standard build tools,
Python 3, QEMU/KVM, and enough disk space for Buildroot output. See
`Docs/emonos-poc-implementation-plan.md` for the full PoC scope and prerequisites.
