BUILDROOT_DIR := $(CURDIR)/buildroot
EXTERNAL_DIR := $(CURDIR)/buildroot-external
COMMON_DIR := $(CURDIR)/common
OUTPUT_BASE ?= $(CURDIR)/output
HOST_TOOLS := $(CURDIR)/tools/bin
# Buildroot's pinned commit is the common source epoch on every build host.
# A caller may override SOURCE_DATE_EPOCH to reproduce an older artifact.
export SOURCE_DATE_EPOCH ?= $(shell git -C $(BUILDROOT_DIR) log -1 --format=%ct)
export TZ := UTC
export EMONOS_VERSION ?= 0.1.0
export EMONOS_APP_FAULT ?= none
export EMONOS_RAUC_KEY_DIR ?= $(OUTPUT_BASE)/signing

# VARIANT selects the OS build system. Buildroot output stays at
# output/<target> so existing paths, tests and CI are unchanged; every other
# variant builds into output/<variant>/<target>.
VARIANT ?= buildroot
# Exact comparisons: $(filter) would treat a value such as deb% as a pattern.
ifneq ($(VARIANT),buildroot)
ifneq ($(VARIANT),debian)
$(error VARIANT must be buildroot or debian, got '$(VARIANT)')
endif
endif
export EMONOS_VARIANT := $(VARIANT)
ifeq ($(VARIANT),buildroot)
VARIANT_OUTPUT := $(OUTPUT_BASE)
else
VARIANT_OUTPUT := $(OUTPUT_BASE)/$(VARIANT)
endif

X86_OUTPUT := $(VARIANT_OUTPUT)/x86-64-vm
RPI4_OUTPUT := $(VARIANT_OUTPUT)/rpi4

.PHONY: emonos_x86_64_vm emonos_rpi4 run_x86_64_vm clean_x86_64_vm rauc_dev_keys

rauc_dev_keys:
	$(COMMON_DIR)/ota/dev-keys.sh "$(EMONOS_RAUC_KEY_DIR)"

emonos_x86_64_vm:
ifeq ($(VARIANT),debian)
	$(error VARIANT=debian x86-64-vm is not implemented yet)
endif
	PATH=$(HOST_TOOLS):$$PATH $(MAKE) -C $(BUILDROOT_DIR) O=$(X86_OUTPUT) BR2_EXTERNAL=$(EXTERNAL_DIR) emonos_x86_64_vm_defconfig
	PATH=$(HOST_TOOLS):$$PATH $(MAKE) -C $(X86_OUTPUT)

emonos_rpi4:
ifeq ($(VARIANT),debian)
	$(error VARIANT=debian rpi4 is not implemented yet)
endif
	PATH=$(HOST_TOOLS):$$PATH $(MAKE) -C $(BUILDROOT_DIR) O=$(RPI4_OUTPUT) BR2_EXTERNAL=$(EXTERNAL_DIR) emonos_rpi4_defconfig
	PATH=$(HOST_TOOLS):$$PATH $(MAKE) -C $(RPI4_OUTPUT)

run_x86_64_vm: emonos_x86_64_vm
	qemu-system-x86_64 -machine q35 -accel kvm -cpu host -m 4096M -display none -serial mon:stdio -bios $(X86_OUTPUT)/images/OVMF.fd -drive file=$(X86_OUTPUT)/images/emonos-x86-64-vm.img,if=virtio,format=raw,snapshot=on,aio=threads -nic user,model=virtio-net-pci -device i6300esb -watchdog-action reset

clean_x86_64_vm:
	$(MAKE) -C $(BUILDROOT_DIR) O=$(X86_OUTPUT) clean
