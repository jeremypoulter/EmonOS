# U-Boot distro boot loads this script from the shared FAT boot partition.
# The two kernel slots are squashfs (gzip); system slots are squashfs (zstd).
part start mmc ${devnum} 6 dev_env
mmc dev ${devnum}
mmc read ${ramdisk_addr_r} ${dev_env} 0x20
env import -c ${ramdisk_addr_r} 0x4000
if test -z "${BOOT_ORDER}"; then setenv BOOT_ORDER 'A B'; fi
if test -z "${BOOT_A_LEFT}"; then setenv BOOT_A_LEFT 1; fi
if test -z "${BOOT_B_LEFT}"; then setenv BOOT_B_LEFT 0; fi
if test -z "${MACHINE_ID}"; then
    setenv boot_condition "systemd.condition-first-boot=true"
else
    setenv boot_condition ""
fi

# The Raspberry Pi kernel DTB hard-codes cgroup_disable=memory in /chosen.
# Do not inherit firmware bootargs: it would silently disable Docker's cgroup
# v2 memory controller. Board/device configuration is carried by the DTB.
setenv bootargs_rpi ""
setenv bootargs

# Consume the primary's trial only when an alternative remains bootable.
# Do not exhaust the known-good fallback or the factory-only A slot.
for BOOT_SLOT in ${BOOT_ORDER}; do
    if test -z "${bootargs}"; then
        if test "${BOOT_SLOT}" = A && test ${BOOT_A_LEFT} -gt 0; then
            if test "${BOOT_ORDER}" = "A B" && test ${BOOT_B_LEFT} -gt 0; then
                setexpr BOOT_A_LEFT ${BOOT_A_LEFT} - 1
            fi
            if sqfsload mmc ${devnum}:2 ${kernel_addr_r} /Image; then
                echo Booting Slot A
                setenv bootargs "${bootargs_rpi} root=PARTUUID=8d3d53e3-6d49-4c38-8349-aff6859e82fd rootfstype=squashfs rootwait ro 8250.nr_uarts=1 console=ttyS0,115200 rauc.slot=A systemd.machine_id=${MACHINE_ID} ${boot_condition}"
            fi
        fi
        if test "${BOOT_SLOT}" = B && test ${BOOT_B_LEFT} -gt 0; then
            if test "${BOOT_ORDER}" = "B A" && test ${BOOT_A_LEFT} -gt 0; then
                setexpr BOOT_B_LEFT ${BOOT_B_LEFT} - 1
            fi
            if sqfsload mmc ${devnum}:4 ${kernel_addr_r} /Image; then
                echo Booting Slot B
                setenv bootargs "${bootargs_rpi} root=PARTUUID=a3ec664e-32ce-4665-95ea-7ae90ce9aa20 rootfstype=squashfs rootwait ro 8250.nr_uarts=1 console=ttyS0,115200 rauc.slot=B systemd.machine_id=${MACHINE_ID} ${boot_condition}"
            fi
        fi
    fi
done

if test -z "${bootargs}"; then
    echo No bootable EmonOS slot
    exit
fi
env export -c -s 0x4000 ${ramdisk_addr_r} BOOT_ORDER BOOT_A_LEFT BOOT_B_LEFT MACHINE_ID
mmc write ${ramdisk_addr_r} ${dev_env} 0x20
setenv fdt_high ${fdt_addr}
booti ${kernel_addr_r} - ${fdt_addr}
