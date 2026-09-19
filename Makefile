BUILDROOT_DIR := $(CURDIR)/buildroot
EXTERNAL_DIR := $(CURDIR)/buildroot-external
OUTPUT_BASE ?= $(CURDIR)/output
HOST_TOOLS := $(CURDIR)/tools/bin

X86_OUTPUT := $(OUTPUT_BASE)/x86-64-vm
RPI4_OUTPUT := $(OUTPUT_BASE)/rpi4

.PHONY: emonos_x86_64_vm emonos_rpi4 run_x86_64_vm clean_x86_64_vm

emonos_x86_64_vm:
	PATH=$(HOST_TOOLS):$$PATH $(MAKE) -C $(BUILDROOT_DIR) O=$(X86_OUTPUT) BR2_EXTERNAL=$(EXTERNAL_DIR) emonos_x86_64_vm_defconfig
	PATH=$(HOST_TOOLS):$$PATH $(MAKE) -C $(X86_OUTPUT)

emonos_rpi4:
	PATH=$(HOST_TOOLS):$$PATH $(MAKE) -C $(BUILDROOT_DIR) O=$(RPI4_OUTPUT) BR2_EXTERNAL=$(EXTERNAL_DIR) emonos_rpi4_defconfig
	PATH=$(HOST_TOOLS):$$PATH $(MAKE) -C $(RPI4_OUTPUT)

run_x86_64_vm: emonos_x86_64_vm
	qemu-system-x86_64 -machine q35 -accel kvm -cpu host -m 1024M -display none -serial mon:stdio -bios $(X86_OUTPUT)/images/OVMF.fd -drive file=$(X86_OUTPUT)/images/emonos-x86-64-vm.img,format=raw,if=virtio -nic user,model=virtio-net-pci

clean_x86_64_vm:
	$(MAKE) -C $(BUILDROOT_DIR) O=$(X86_OUTPUT) clean
