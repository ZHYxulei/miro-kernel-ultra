#!/usr/bin/env bash
#
# Boot the freshly built kernel under QEMU and assert that it reaches
# userspace. This is deliberately a *small* test: it does not emulate the
# Redmi K80 Pro, it only proves that the Image we are about to publish is a
# bootable arm64 kernel whose core subsystems (MMU, timers, GIC, PL011
# console, devtmpfs, initramfs unpacking) came up and that /init ran.
#
# Usage: scripts/ci/smoke-boot-test.sh [path/to/Image]
#
# Environment:
#   SMOKE_TEST_TIMEOUT  seconds to wait for the guest (default 180)
#   QEMU_BIN            qemu-system-aarch64 binary (default: from PATH)
#   CROSS_CC            cross compiler for the throwaway init
set -euo pipefail

image="${1:-out/dist/Image}"
timeout_secs="${SMOKE_TEST_TIMEOUT:-180}"
qemu_bin="${QEMU_BIN:-qemu-system-aarch64}"

fail() {
    printf 'Smoke boot test failed: %s\n' "$*" >&2
    exit 1
}

log() {
    printf '==> %s\n' "$*"
}

[ -s "${image}" ] || fail "${image} is missing or empty"
command -v "${qemu_bin}" >/dev/null 2>&1 || fail "${qemu_bin} not found in PATH"

work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

# ---------------------------------------------------------------------------
# Cross compiler for the freestanding init below
# ---------------------------------------------------------------------------
cc_cmd=()
if command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
    cc_cmd=(aarch64-linux-gnu-gcc -O2 -static -nostdlib -nostartfiles -Wl,-e,_start)
elif command -v clang >/dev/null 2>&1 && command -v ld.lld >/dev/null 2>&1; then
    cc_cmd=(clang --target=aarch64-linux-gnu -march=armv8-a -O2 -static -nostdlib
            -fuse-ld=lld -Wl,-e,_start)
else
    fail "no aarch64 cross compiler found (install gcc-aarch64-linux-gnu or clang+lld)"
fi
if [ -n "${CROSS_CC:-}" ]; then
    read -r -a cc_cmd <<< "${CROSS_CC}"
    cc_cmd+=(-O2 -static -nostdlib -nostartfiles -Wl,-e,_start)
fi

# ---------------------------------------------------------------------------
# Tiny init: no libc, raw syscalls only, so it needs no rootfs at all.
# It mounts devtmpfs, prints a marker plus the running kernel release, and
# powers the machine off so QEMU exits by itself.
# ---------------------------------------------------------------------------
cat > "${work_dir}/init.c" <<'INIT_C'
typedef unsigned long u64;

static long sc5(long n, long a, long b, long c, long d, long e)
{
	register long x8 __asm__("x8") = n;
	register long x0 __asm__("x0") = a;
	register long x1 __asm__("x1") = b;
	register long x2 __asm__("x2") = c;
	register long x3 __asm__("x3") = d;
	register long x4 __asm__("x4") = e;

	__asm__ volatile("svc #0"
			 : "+r"(x0)
			 : "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x8)
			 : "memory");
	return x0;
}

static long sc3(long n, long a, long b, long c) { return sc5(n, a, b, c, 0, 0); }
static long sc1(long n, long a) { return sc5(n, a, 0, 0, 0, 0); }

#define SYS_mount  40
#define SYS_write  64
#define SYS_reboot 142
#define SYS_uname  160

static u64 slen(const char *s)
{
	u64 n = 0;

	while (s[n])
		n++;
	return n;
}

static void puts_(const char *s)
{
	sc3(SYS_write, 1, (long)s, (long)slen(s));
}

void _start(void)
{
	/* struct utsname is six 65-byte fields: sysname, nodename, release, ... */
	char uts[390];
	const char *release;
	u64 i, len;

	/* devtmpfs proves the in-kernel filesystem side works too */
	sc5(SYS_mount, (long)"devtmpfs", (long)"/dev", (long)"devtmpfs", 0, 0);

	puts_("SMOKE-BOOT: userspace init reached\n");

	if (sc1(SYS_uname, (long)uts) == 0) {
		release = uts + 65 + 65;
		len = slen(release);
		if (len > 64)
			len = 64;
		puts_("SMOKE-BOOT: uname -r = ");
		for (i = 0; i < len; i++)
			sc3(SYS_write, 1, (long)(release + i), 1);
		puts_("\n");
	}

	puts_("SMOKE-BOOT-OK\n");

	/* reboot(LINUX_REBOOT_MAGIC1, MAGIC2, RB_POWER_OFF, NULL) */
	sc5(SYS_reboot, 0xfee1dead, 672274793, 0x4321fedc, 0, 0);

	for (;;)
		;
}
INIT_C

log "Compiling init for aarch64"
"${cc_cmd[@]}" -o "${work_dir}/init" "${work_dir}/init.c"

# ---------------------------------------------------------------------------
# Build the initramfs
# ---------------------------------------------------------------------------
rootfs="${work_dir}/rootfs"
mkdir -p "${rootfs}/dev"
install -m 0755 "${work_dir}/init" "${rootfs}/init"
( cd "${rootfs}" && find . | cpio -o -H newc --quiet | gzip -9 ) > "${work_dir}/initramfs.cpio.gz"
[ -s "${work_dir}/initramfs.cpio.gz" ] || fail "failed to build initramfs"

# ---------------------------------------------------------------------------
# Boot it
# ---------------------------------------------------------------------------
guest_log="${work_dir}/console.log"
qemu_args=(
    -M virt
    # virt attaches a virtio-net-pci NIC by default, whose option ROM is a
    # separate package; this test needs no networking at all.
    -nic none
    -cpu cortex-a710
    -smp 2
    -m 1024M
    -display none
    -monitor none
    -serial "file:${guest_log}"
    -kernel "${image}"
    -initrd "${work_dir}/initramfs.cpio.gz"
    -append "console=ttyAMA0,115200 earlycon=pl011,0x9000000 rdinit=/init panic=-1 loglevel=7"
    -no-reboot
)

log "Booting ${image} under QEMU (timeout ${timeout_secs}s)"
set +e
timeout "${timeout_secs}" "${qemu_bin}" "${qemu_args[@]}"
qemu_status=$?
set -e
# timeout(1) returns 124 when it had to kill the guest; the poweroff path makes
# QEMU exit 0, but accept a killed guest as long as the log proves we got there.
if [ "${qemu_status}" -ne 0 ]; then
    log "QEMU exited with status ${qemu_status} (continuing, log is what matters)"
fi

[ -s "${guest_log}" ] || fail "guest produced no console output"

dump_tail() {
    printf -- '---- last 60 lines of guest console ----\n' >&2
    tail -n 60 "${guest_log}" >&2
    printf -- '-----------------------------------------\n' >&2
}

grep -q 'Linux version' "${guest_log}" || { dump_tail; fail "kernel never printed a version banner"; }
grep -q 'Kernel panic' "${guest_log}" && { dump_tail; fail "kernel panicked during boot"; }
grep -q 'Unable to handle kernel' "${guest_log}" && { dump_tail; fail "kernel oopsed during boot"; }
grep -q 'SMOKE-BOOT: userspace init reached' "${guest_log}" ||
    { dump_tail; fail "kernel did not reach userspace /init"; }
grep -q 'SMOKE-BOOT-OK' "${guest_log}" || { dump_tail; fail "init did not complete"; }

log "Boot log:"
grep -E 'Linux version|SMOKE-BOOT' "${guest_log}" | sed 's/^/    /'
log "Kernel booted to userspace successfully"
