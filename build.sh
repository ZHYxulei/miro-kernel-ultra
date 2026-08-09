#!/usr/bin/env bash
#
# build.sh - Build script for miro-kernel-ultra (Xiaomi SM7250/Sun, Android 15, Linux 6.6)
#
# Based on:
#   https://github.com/EndCredits/kernel_xiaomi_sm7250/blob/android-4.19/build.sh
#   https://github.com/UtsavBalar1231/Drone-scripts
#
# Requirements:
#   - clang >= 15 (with ld.lld, llvm-strip)
#   - flex, bison, bc, cpio, dtc
#   - libelf-dev, libssl-dev, libncurses-dev
#
# Usage:
#   bash build.sh help          Show help
#   bash build.sh quick         One-command: update submodules + menuconfig + compile + package
#   bash build.sh defconfig     Only generate .config
#   bash build.sh menuconfig    Open menuconfig UI to edit .config
#   bash build.sh kernel        Build kernel (Image + modules + dtbs)
#   bash build.sh all           Build kernel and copy outputs to out/dist
#   bash build.sh zip           Build kernel and package AnyKernel3 flashable zip
#   bash build.sh clean         Clean build directory (preserves .config)
#   bash build.sh deepclean     Deep clean: out/ + AnyKernel3/ + submodule artifacts
#   bash build.sh mrproper      Full source tree clean (make mrproper + deepclean)
#   bash build.sh toolchain     Show current toolchain configuration
#   bash build.sh update-submodules  Update KernelSU and AnyKernel3 to latest upstream
#   bash build.sh install-deps  Install build dependencies (apt/dnf/pacman)
#
# Environment variables:
#   CLANG_PATH        Path to directory containing custom clang (e.g. /opt/clang/bin)
#   GCC_PATH          Path to directory containing aarch64-linux-gnu-gcc (for CROSS_COMPILE)
#   JOBS              Override number of parallel jobs (default: nproc)
#   KERNEL_NAME       Override kernel name shown in AnyKernel3 (default: miro-kernel-ultra)
#   OUT_DIR           Override output directory (default: out)
#   USE_CCACHE        Set to 1 to enable ccache (default: auto-detect)
#   CCACHE_DIR        Override ccache directory (default: ~/.ccache)
#   FAST_BUILD        Set to 1 to skip modules and only build Image+dtbs
#
# AnyKernel3 configuration (optional):
#   AK3_DEVICE_CHECK  Set to 1 to enable device name check (default: 1)
#   AK3_DEVICE_NAME1  Device name 1 (e.g. 24122RKC7C)
#   AK3_DEVICE_NAME2  Device name 2
#   AK3_DEVICE_NAME3  Device name 3
#   AK3_DEVICE_NAME4  Device name 4
#   AK3_DEVICE_NAME5  Device name 5
#   AK3_BLOCK         Partition to flash (default: boot, e.g. auto, init_boot)
#   AK3_SLOT_DEVICE   A/B slot device: 1, 0, or auto (default: 1)
#   AK3_PATCH_VBMETA  Patch vbmeta to disable AVB: 1, 0, or auto (default: auto)
#

set -e

# ============================================================
# Configuration
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ARCH=arm64
DEFCONFIG=gki_defconfig
OUT_DIR="${OUT_DIR:-out}"
DIST_DIR="${OUT_DIR}/dist"
JOBS="${JOBS:-$(nproc --all)}"

# ============================================================
# Toolchain configuration
# ============================================================
# Supports custom toolchain via environment variables:
#   CLANG_PATH  - directory containing clang/ld.lld/llvm-strip
#   GCC_PATH    - directory containing aarch64-linux-gnu-gcc
# If not set, falls back to system-installed toolchain.

setup_toolchain() {
    export LLVM=1
    export ARCH=${ARCH}

    # Clang cross-compilation triple
    CLANG_TRIPLE=aarch64-linux-gnu-
    CROSS_COMPILE=aarch64-linux-gnu-
    CROSS_COMPILE_COMPAT=arm-linux-gnueabi-

    # Custom clang path
    if [ -n "${CLANG_PATH}" ]; then
        if [ -d "${CLANG_PATH}" ]; then
            export PATH="${CLANG_PATH}:${PATH}"
            local clang_bin="${CLANG_PATH}/clang"
            if [ -x "${clang_bin}" ]; then
                log "Using custom clang: ${clang_bin}"
            else
                error "CLANG_PATH is set but clang not found in ${CLANG_PATH}"
                exit 1
            fi
        else
            error "CLANG_PATH directory does not exist: ${CLANG_PATH}"
            exit 1
        fi
    fi

    # Custom GCC path (for CROSS_COMPILE, needed for some assembly/link steps)
    if [ -n "${GCC_PATH}" ]; then
        if [ -d "${GCC_PATH}" ]; then
            export PATH="${GCC_PATH}:${PATH}"
            log "Using custom GCC: ${GCC_PATH}/aarch64-linux-gnu-gcc"
        else
            error "GCC_PATH directory does not exist: ${GCC_PATH}"
            exit 1
        fi
    fi

    # ccache support: speeds up rebuilds significantly
    local use_ccache="${USE_CCACHE:-auto}"
    if [ "$use_ccache" = "auto" ]; then
        if command -v ccache >/dev/null 2>&1; then
            use_ccache=1
        else
            use_ccache=0
        fi
    fi
    if [ "$use_ccache" = "1" ]; then
        if ! command -v ccache >/dev/null 2>&1; then
            error "USE_CCACHE=1 but ccache not found. Install: apt install ccache"
            exit 1
        fi
        export CCACHE_DIR="${CCACHE_DIR:-${HOME}/.ccache}"
        mkdir -p "${CCACHE_DIR}"
        # Prepend ccache to PATH so it wraps clang
        export PATH="$(dirname $(command -v ccache)):${PATH}"
        # Tell kernel build to use ccache
        export CC="ccache clang"
        export CCACHE_COMPRESS=1
        local ccache_stats=$(ccache -s 2>/dev/null | grep "cache hit" || echo "new")
        log "ccache enabled (dir: ${CCACHE_DIR})"
        log "  Run 'ccache -s' to see stats, 'ccache -M 50G' to set max size"
    fi
}

# Common make arguments (assembled after setup_toolchain)
MAKE_ARGS=(
    ARCH=${ARCH}
    LLVM=1
    CLANG_TRIPLE=${CLANG_TRIPLE}
    CROSS_COMPILE=${CROSS_COMPILE}
    CROSS_COMPILE_COMPAT=${CROSS_COMPILE_COMPAT}
    O=${OUT_DIR}
    -j${JOBS}
)

# Defconfig fragments (merged in order)
CONFIG_DIR=arch/${ARCH}/configs
DEFCONFIG_FRAGMENTS=(
    ${CONFIG_DIR}/gki_defconfig
    ${CONFIG_DIR}/vendor/sun_perf.config
    ${CONFIG_DIR}/vendor/miro_perf.config
    ${CONFIG_DIR}/droidspaces.fragment
)

# Output files
KERNEL_IMAGE=${OUT_DIR}/arch/${ARCH}/boot/Image
KERNEL_DTB=${OUT_DIR}/arch/${ARCH}/boot/dtb
KERNEL_DTBO=${OUT_DIR}/arch/${ARCH}/boot/dtbo.img

# AnyKernel3 packaging
ANYKERNEL3_DIR="AnyKernel3"
ANYKERNEL3_REPO="https://github.com/ZHYxulei/AnyKernel3.git"
ANYKERNEL3_ZIP="${ANYKERNEL3_ZIP:-miro-kernel-ultra-$(date +%Y%m%d-%H%M).zip}"

# Kernel name shown in AnyKernel3 installer
KERNEL_NAME="${KERNEL_NAME:-miro-kernel-ultra}"

# AnyKernel3 configuration (overridable via environment variables)
AK3_DEVICE_CHECK="${AK3_DEVICE_CHECK:-1}"
AK3_DEVICE_NAME1="${AK3_DEVICE_NAME1:-24122RKC7C}"
AK3_DEVICE_NAME2="${AK3_DEVICE_NAME2:-miro}"
AK3_DEVICE_NAME3="${AK3_DEVICE_NAME3:-Redmi K80 Pro}"
AK3_DEVICE_NAME4="${AK3_DEVICE_NAME4:-}"
AK3_DEVICE_NAME5="${AK3_DEVICE_NAME5:-}"
AK3_BLOCK="${AK3_BLOCK:-boot}"
AK3_SLOT_DEVICE="${AK3_SLOT_DEVICE:-1}"
AK3_PATCH_VBMETA="${AK3_PATCH_VBMETA:-auto}"

# Error log file (set during build)
ERROR_LOG=""

START_SEC=$(date +%s)

# ============================================================
# Helper functions
# ============================================================

log() {
    echo -e "\033[1;32m[build.sh]\033[0m $*"
}

error() {
    echo -e "\033[1;31m[ERROR]\033[0m $*" >&2
}

elapsed() {
    local sec=$(( $(date +%s) - START_SEC ))
    echo "$((sec / 60))m $((sec % 60))s"
}

# Display compilation errors extracted from build log.
# Shows up to 10 errors with surrounding context for easy debugging.
show_build_errors() {
    local log_file="${1:-${ERROR_LOG}}"

    [ -f "$log_file" ] || return 0

    # Common kernel build error patterns
    local error_patterns='(error:|fatal error:|Error [0-9]|undefined reference|No rule to make target|recipe for target.*failed)'

    # Find error lines (limit to first 30 matches)
    local error_lines
    error_lines=$(grep -n -E "$error_patterns" "$log_file" 2>/dev/null | head -30)

    if [ -z "$error_lines" ]; then
        # No specific error pattern found, show last 30 lines of output
        echo ""
        error "Build failed. Last 30 lines of build output:"
        echo ""
        tail -30 "$log_file" 2>/dev/null | sed 's/^/  /'
        echo ""
        return 0
    fi

    echo ""
    echo -e "\033[1;41m═══════════════════════════════════════════════════════════════════\033[0m"
    echo -e "\033[1;41m  编译错误汇总                                                       \033[0m"
    echo -e "\033[1;41m═══════════════════════════════════════════════════════════════════\033[0m"
    echo ""

    local shown=0
    local line_num
    while IFS= read -r line; do
        [ "$shown" -ge 10 ] && break
        line_num=$(echo "$line" | cut -d: -f1)
        echo -e "\033[1;33m━━━ 错误 #$((shown + 1)) (日志行 $line_num) ━━━\033[0m"
        # Show context: 2 lines before, the error line, 2 lines after
        local start=$(( line_num > 2 ? line_num - 2 : 1 ))
        local end=$(( line_num + 2 ))
        sed -n "${start},${end}p" "$log_file" 2>/dev/null | sed 's/^/  /'
        echo ""
        shown=$(( shown + 1 ))
    done <<< "$error_lines"

    if [ "$shown" -ge 10 ]; then
        echo -e "\033[1;33m  (仅显示前 10 个错误，更多错误请查看完整日志)\033[0m"
        echo ""
    fi

    echo -e "\033[1;31m  完整编译日志: $log_file\033[0m"
    echo ""
}

# ============================================================
# Build steps
# ============================================================

check_prerequisites() {
    log "Checking prerequisites..."
    setup_toolchain
    local missing=()
    for tool in clang ld.lld llvm-strip flex bison bc cpio dtc; do
        command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing tools: ${missing[*]}"
        error "Install with: apt install clang lld llvm flex bison bc cpio device-tree-compiler libelf-dev libssl-dev libncurses-dev"
        error "Or set CLANG_PATH / GCC_PATH to use a custom toolchain."
        exit 1
    fi
    log "Clang version: $(clang --version | head -1)"
}

install_deps() {
    log "Installing build dependencies..."

    # Common packages (same name across distros)
    local common_pkgs="clang lld llvm flex bison bc cpio zip git ccache"

    if command -v apt >/dev/null 2>&1; then
        # Debian/Ubuntu
        log "Detected: apt (Debian/Ubuntu)"
        sudo apt update
        sudo apt install -y $common_pkgs \
            device-tree-compiler libelf-dev libssl-dev libncurses-dev
    elif command -v dnf >/dev/null 2>&1; then
        # Fedora/RHEL
        log "Detected: dnf (Fedora/RHEL)"
        sudo dnf install -y $common_pkgs \
            dtc elfutils-libelf-devel openssl-devel ncurses-devel
    elif command -v pacman >/dev/null 2>&1; then
        # Arch Linux
        log "Detected: pacman (Arch Linux)"
        sudo pacman -S --noconfirm $common_pkgs \
            dtc libelf openssl ncurses
    elif command -v zypper >/dev/null 2>&1; then
        # openSUSE
        log "Detected: zypper (openSUSE)"
        sudo zypper install -y $common_pkgs \
            dtc libelf-devel libopenssl-devel ncurses-devel
    else
        error "Unsupported package manager. Please install manually:"
        error "  clang lld llvm flex bison bc cpio dtc zip git ccache"
        error "  libelf-dev libssl-dev libncurses-dev"
        exit 1
    fi

    log "Dependencies installed successfully."
    log "You can now run: ./build.sh quick"
}

show_toolchain() {
    setup_toolchain
    log "Toolchain configuration:"
    echo "  ARCH:                 ${ARCH}"
    echo "  LLVM:                 ${LLVM}"
    echo "  CLANG_TRIPLE:         ${CLANG_TRIPLE}"
    echo "  CROSS_COMPILE:        ${CROSS_COMPILE}"
    echo "  CROSS_COMPILE_COMPAT: ${CROSS_COMPILE_COMPAT}"
    echo "  OUT_DIR:              ${OUT_DIR}"
    echo "  JOBS:                 ${JOBS}"
    echo ""
    if [ -n "${CLANG_PATH}" ]; then
        echo "  CLANG_PATH (custom):  ${CLANG_PATH}"
    else
        echo "  CLANG_PATH:           (system default)"
    fi
    if [ -n "${GCC_PATH}" ]; then
        echo "  GCC_PATH (custom):    ${GCC_PATH}"
    else
        echo "  GCC_PATH:             (system default)"
    fi
    echo ""
    echo "  clang:        $(command -v clang)"
    echo "  ld.lld:       $(command -v ld.lld)"
    echo "  llvm-strip:   $(command -v llvm-strip)"
    if command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
        echo "  cross gcc:    $(command -v aarch64-linux-gnu-gcc)"
    fi
}

init_submodules() {
    log "Initializing submodules..."
    if [ -f .gitmodules ]; then
        git submodule update --init --recursive
    else
        log "No .gitmodules found, skipping."
    fi
}

clean_source_tree() {
    # Remove stale build artifacts from source root that trigger
    # "The source tree is not clean" when using O= out-of-tree builds.
    # These files are leftovers from in-tree builds.
    local dirty=0
    for f in .config .config.old; do
        if [ -f "$f" ]; then
            rm -f "$f"
            dirty=1
        fi
    done
    if [ "$dirty" = "1" ]; then
        log "Removed stale config files from source root."
    fi
}

# Check if out/.config is up-to-date with all defconfig fragments.
# Returns 0 (true) if config needs regeneration, 1 (false) if up-to-date.
config_needs_regen() {
    local config_file="${OUT_DIR}/.config"

    # No config file → must regenerate
    [ -f "$config_file" ] || return 0

    # Check if any defconfig fragment is newer than the config
    local fragment
    for fragment in "${DEFCONFIG_FRAGMENTS[@]}"; do
        if [ -f "$fragment" ] && [ "$fragment" -nt "$config_file" ]; then
            return 0
        fi
    done

    # Check if key post-defconfig options are present
    # (detects case where config was generated without enable_miro_config)
    local key_configs=(
        CONFIG_PINCTRL_MSM
        CONFIG_KSU
        CONFIG_KSU_SUSFS
    )
    local kc
    for kc in "${key_configs[@]}"; do
        if ! grep -q "^${kc}=" "$config_file"; then
            return 0
        fi
    done

    # Config is up-to-date
    return 1
}

make_defconfig() {
    if config_needs_regen; then
        log "Generating defconfig (merging fragments)..."

        mkdir -p "${OUT_DIR}"

        # Use kernel's merge_config.sh to merge all fragments into a single .config
        # -m: only merge fragments, don't run make
        # -O: output directory for .config
        # -y: builtin (=y) takes precedence over module (=m) when both appear
        ./scripts/kconfig/merge_config.sh -m -O "${OUT_DIR}" -y "${DEFCONFIG_FRAGMENTS[@]}"

        # Apply post-defconfig changes (from build.config.msm.miro)
        log "  Applying post-defconfig changes..."
        ./scripts/config --file "${OUT_DIR}/.config" \
            --enable CONFIG_PINCTRL_MSM \
            --enable CONFIG_PINCTRL_SUN \
            --enable CONFIG_PINCTRL_QCOM_SPMI_PMIC

        # Resolve config: olddefconfig silently answers 'n' (default) to all NEW
        # options, preventing the interactive "Restart config..." platform prompts.
        make "${MAKE_ARGS[@]}" olddefconfig

        log "Defconfig generated at ${OUT_DIR}/.config"
    else
        log "Config is up-to-date, skipping defconfig generation."
    fi
}

build_kernel() {
    mkdir -p "${OUT_DIR}"
    ERROR_LOG="${OUT_DIR}/build.log"

    set +e
    if [ "${FAST_BUILD}" = "1" ]; then
        log "Building kernel (fast: Image + dtbs only, no modules)..."
        make "${MAKE_ARGS[@]}" Image dtbs 2>&1 | tee "${ERROR_LOG}"
    else
        log "Building kernel (Image + modules + dtbs)..."
        make "${MAKE_ARGS[@]}" Image modules dtbs 2>&1 | tee "${ERROR_LOG}"
    fi
    local make_status=${PIPESTATUS[0]}
    set -e

    if [ "$make_status" -ne 0 ]; then
        show_build_errors "${ERROR_LOG}"
        error "Kernel build failed! (exit code: ${make_status})"
        error "Full log: ${ERROR_LOG}"
        exit 1
    fi

    log "Kernel build completed in $(elapsed)"
}

copy_outputs() {
    log "Copying outputs to ${DIST_DIR}..."
    mkdir -p "${DIST_DIR}"

    # Kernel image
    if [ -f "${KERNEL_IMAGE}" ]; then
        cp "${KERNEL_IMAGE}" "${DIST_DIR}/"
    fi

    # DTBs - cat all vendor dtb files into a single dtb blob
    local dts_dir="${OUT_DIR}/arch/${ARCH}/boot/dts/vendor/qcom"
    if [ -d "${dts_dir}" ]; then
        find "${dts_dir}" -name '*.dtb' -exec cat {} + > "${DIST_DIR}/dtb"
        log "  Copied dtb"
    fi

    # DTBO image
    if [ -f "${KERNEL_DTBO}" ]; then
        cp "${KERNEL_DTBO}" "${DIST_DIR}/"
    fi

    # Kernel modules
    if [ -d "${OUT_DIR}/modules_install" ]; then
        cp -r "${OUT_DIR}/modules_install" "${DIST_DIR}/modules"
    fi

    # vmlinux symbols (useful for debugging)
    [ -f "${OUT_DIR}/vmlinux" ] && cp "${OUT_DIR}/vmlinux" "${DIST_DIR}/"
    [ -f "${OUT_DIR}/System.map" ] && cp "${OUT_DIR}/System.map" "${DIST_DIR}/"
    [ -f "${OUT_DIR}/vmlinux.symvers" ] && cp "${OUT_DIR}/vmlinux.symvers" "${DIST_DIR}/"
    [ -f "${OUT_DIR}/.config" ] && cp "${OUT_DIR}/.config" "${DIST_DIR}/"

    log "Outputs copied to ${DIST_DIR}"
    ls -la "${DIST_DIR}/"
}

install_modules() {
    log "Installing kernel modules..."
    local mod_log="${OUT_DIR}/modules_install.log"
    set +e
    make "${MAKE_ARGS[@]}" INSTALL_MOD_PATH=${OUT_DIR}/modules_install modules_install 2>&1 | tee "$mod_log"
    local mod_status=${PIPESTATUS[0]}
    set -e

    if [ "$mod_status" -ne 0 ]; then
        show_build_errors "$mod_log"
        error "Module install failed! (exit code: ${mod_status})"
        exit 1
    fi

    # Remove symlinks and build artifacts
    rm -f "${OUT_DIR}/modules_install/lib/modules/"*/build
    rm -f "${OUT_DIR}/modules_install/lib/modules/"*/source
}

setup_anykernel3() {
    if git -C "${ANYKERNEL3_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        log "AnyKernel3 submodule found, using existing."
    elif [ -d "${ANYKERNEL3_DIR}" ] && [ -n "$(find "${ANYKERNEL3_DIR}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
        error "${ANYKERNEL3_DIR} exists but is not an initialized Git repository."
        error "Run: git submodule update --init --recursive AnyKernel3"
        exit 1
    elif [ -n "${ANYKERNEL3_REPO}" ]; then
        log "AnyKernel3 not initialized, cloning..."
        rm -rf "${ANYKERNEL3_DIR}"
        git clone --depth=1 "${ANYKERNEL3_REPO}" "${ANYKERNEL3_DIR}"
    else
        error "AnyKernel3 not found. Run: git submodule update --init AnyKernel3"
        exit 1
    fi

    # Ensure build artifacts are git-ignored within the AnyKernel3 directory
    local ak3_gitignore="${ANYKERNEL3_DIR}/.gitignore"
    if [ ! -f "$ak3_gitignore" ]; then
        cat > "$ak3_gitignore" << 'AK3GITIGNORE'
# Build artifacts injected by build.sh
Image
Image.gz
Image.gz-dtb
dtb
dtbo.img
modules/
AK3GITIGNORE
        log "  Created .gitignore in AnyKernel3 for build artifacts"
    fi
}

package_anykernel3() {
    log "Packaging AnyKernel3 flashable zip..."

    setup_anykernel3

    # Clean previous artifacts in AnyKernel3
    rm -f "${ANYKERNEL3_DIR}/Image" "${ANYKERNEL3_DIR}/dtb" "${ANYKERNEL3_DIR}/dtbo.img"
    rm -rf "${ANYKERNEL3_DIR}/modules"

    # Copy kernel outputs
    if [ -f "${KERNEL_IMAGE}" ]; then
        cp "${KERNEL_IMAGE}" "${ANYKERNEL3_DIR}/"
    else
        error "Kernel Image not found at ${KERNEL_IMAGE}"
        exit 1
    fi

    # Copy DTB blob
    local dts_dir="${OUT_DIR}/arch/${ARCH}/boot/dts/vendor/qcom"
    if [ -d "${dts_dir}" ]; then
        find "${dts_dir}" -name '*.dtb' -exec cat {} + > "${ANYKERNEL3_DIR}/dtb"
        log "  Copied dtb"
    fi

    # Copy DTBO image
    if [ -f "${KERNEL_DTBO}" ]; then
        cp "${KERNEL_DTBO}" "${ANYKERNEL3_DIR}/"
        log "  Copied dtbo.img"
    fi

    # Copy kernel modules
    if [ -d "${OUT_DIR}/modules_install" ]; then
        mkdir -p "${ANYKERNEL3_DIR}/modules"
        cp -r "${OUT_DIR}/modules_install/lib/modules/"* "${ANYKERNEL3_DIR}/modules/"
        log "  Copied modules"
    fi

    # Configure anykernel.sh for this device
    local ak3_sh="${ANYKERNEL3_DIR}/anykernel.sh"
    if [ -f "${ak3_sh}" ]; then
        sed -i \
            -e "s/^kernel.string=.*/kernel.string=${KERNEL_NAME}/" \
            -e "s/^do\.modules=.*/do.modules=1/" \
            -e "s/^do\.devicecheck=.*/do.devicecheck=${AK3_DEVICE_CHECK}/" \
            -e "s/^device\.name1=.*/device.name1=${AK3_DEVICE_NAME1}/" \
            -e "s/^device\.name2=.*/device.name2=${AK3_DEVICE_NAME2}/" \
            -e "s/^device\.name3=.*/device.name3=${AK3_DEVICE_NAME3}/" \
            -e "s/^device\.name4=.*/device.name4=${AK3_DEVICE_NAME4}/" \
            -e "s/^device\.name5=.*/device.name5=${AK3_DEVICE_NAME5}/" \
            -e "s/^BLOCK=.*/BLOCK=${AK3_BLOCK};/" \
            -e "s/^IS_SLOT_DEVICE=.*/IS_SLOT_DEVICE=${AK3_SLOT_DEVICE};/" \
            -e "s/^PATCH_VBMETA_FLAG=.*/PATCH_VBMETA_FLAG=${AK3_PATCH_VBMETA};/" \
            "${ak3_sh}"
        log "  Configured anykernel.sh:"
        log "    kernel.string=${KERNEL_NAME}"
        log "    do.devicecheck=${AK3_DEVICE_CHECK}"
        log "    device.name1=${AK3_DEVICE_NAME1}"
        log "    block=${AK3_BLOCK}  slot_device=${AK3_SLOT_DEVICE}  patch_vbmeta=${AK3_PATCH_VBMETA}"
    fi

    # Create flashable zip
    local zip_path="${DIST_DIR}/${ANYKERNEL3_ZIP}"
    mkdir -p "${DIST_DIR}"
    ( cd "${ANYKERNEL3_DIR}" && zip -r9 "${SCRIPT_DIR}/${zip_path}" . -x ".git/*" "README.md" ".gitignore" )
    log "AnyKernel3 zip created: ${zip_path}"
}

clean() {
    log "Cleaning build directory (preserving .config)..."
    if [ -f "${OUT_DIR}/.config" ]; then
        cp "${OUT_DIR}/.config" "${OUT_DIR}/.config.backup"
        make "${MAKE_ARGS[@]}" clean 2>/dev/null || true
        rm -rf "${OUT_DIR}"
        mkdir -p "${OUT_DIR}"
        mv "${OUT_DIR}/.config.backup" "${OUT_DIR}/.config" 2>/dev/null || true
        log "Build directory cleaned, .config preserved."
    else
        rm -rf "${OUT_DIR}"
        log "Build directory cleaned (no .config to preserve)."
    fi
}

deepclean() {
    log "Deep cleaning..."
    # Remove build output
    rm -rf "${OUT_DIR}"
    log "  Removed ${OUT_DIR}/"

    # Clean AnyKernel3 build artifacts (not the submodule itself)
    if [ -d "${ANYKERNEL3_DIR}" ]; then
        rm -f "${ANYKERNEL3_DIR}/Image" "${ANYKERNEL3_DIR}/dtb" "${ANYKERNEL3_DIR}/dtbo.img"
        rm -rf "${ANYKERNEL3_DIR}/modules"
        log "  Cleaned AnyKernel3 build artifacts"
    fi

    # Remove stale config files from source root
    rm -f .config .config.old .tmp_defconfig
    log "  Removed stale root config files"

    # Clean KernelSU submodule build artifacts
    if [ -d "KernelSU/kernel" ]; then
        rm -f KernelSU/kernel/built-in.a KernelSU/kernel/modules.order
        find KernelSU/kernel -name '*.o' -delete 2>/dev/null || true
        find KernelSU/kernel -name '*.cmd' -delete 2>/dev/null || true
        log "  Cleaned KernelSU submodule artifacts"
    fi

    # Remove stray build artifacts in source tree (not tracked by make clean)
    find . -maxdepth 1 -name '*.o' -delete 2>/dev/null || true
    find . -maxdepth 1 -name '*.a' -delete 2>/dev/null || true
    find . -maxdepth 1 -name '*.cmd' -delete 2>/dev/null || true

    log "Deep clean complete."
}

mrproper() {
    log "Running mrproper (full source tree clean)..."
    make "${MAKE_ARGS[@]}" mrproper 2>/dev/null || log "  make mrproper had warnings (ignored)"
    rm -rf "${OUT_DIR}"

    # Clean AnyKernel3 build artifacts (preserve submodule directory)
    if [ -d "${ANYKERNEL3_DIR}" ]; then
        rm -f "${ANYKERNEL3_DIR}/Image" "${ANYKERNEL3_DIR}/dtb" "${ANYKERNEL3_DIR}/dtbo.img"
        rm -rf "${ANYKERNEL3_DIR}/modules"
        log "  Cleaned AnyKernel3 build artifacts (submodule preserved)"
    fi

    # Clean KernelSU submodule build artifacts
    if [ -d "KernelSU/kernel" ]; then
        rm -f KernelSU/kernel/built-in.a KernelSU/kernel/modules.order
        find KernelSU/kernel -name '*.o' -delete 2>/dev/null || true
        find KernelSU/kernel -name '*.cmd' -delete 2>/dev/null || true
        log "  Cleaned KernelSU submodule artifacts"
    fi

    # Remove stale config files from source root
    rm -f .config .config.old .tmp_defconfig

    log "mrproper complete."
}

update_submodules() {
    log "Updating submodules to latest upstream..."

    # Fetch latest from upstream for all submodules
    git submodule foreach 'git fetch origin && git checkout origin/HEAD && echo "Updated: $(basename $(pwd)) -> $(git log --oneline -1)"'

    # Stage the updated submodule pointers
    git add KernelSU AnyKernel3
    log "Submodule pointers staged. Run 'git commit' to save changes."
    log ""
    log "Submodule status:"
    git submodule status
}

menuconfig() {
    log "Launching menuconfig..."
    if [ ! -f "${OUT_DIR}/.config" ]; then
        log "No .config found, generating defconfig first..."
        check_prerequisites
        init_submodules
        clean_source_tree
        make_defconfig
    fi
    make "${MAKE_ARGS[@]}" menuconfig
    log "Configuration saved to ${OUT_DIR}/.config"
}

# Quick build: update submodules → menuconfig → compile → package
# One-command workflow for full build with interactive configuration.
quick() {
    log "=== Quick Build: update → menuconfig → compile → package ==="

    # Step 1: Update submodules to latest upstream
    update_submodules

    # Step 2: Check prerequisites and setup toolchain
    check_prerequisites

    # Step 3: Initialize submodules
    init_submodules

    # Step 4: Clean source tree
    clean_source_tree

    # Step 5: Generate defconfig (merge all fragments, apply post-config)
    make_defconfig

    # Step 6: Launch menuconfig for user customization
    echo ""
    log "Opening menuconfig for kernel configuration..."
    log "  - Customize options as needed, then save and exit"
    log "  - To use defaults, simply exit without changes"
    echo ""
    make "${MAKE_ARGS[@]}" menuconfig
    log "Configuration saved to ${OUT_DIR}/.config"

    # Step 7: Build kernel (with error capture)
    build_kernel

    # Step 8: Install kernel modules
    install_modules

    # Step 9: Copy outputs to dist directory
    copy_outputs

    # Step 10: Package AnyKernel3 flashable zip
    package_anykernel3

    log "=== Quick build completed in $(elapsed) ==="
}

show_help() {
    cat << 'EOF'
build.sh - Build script for miro-kernel-ultra

Usage: bash build.sh <command>

Commands:
  help        Show this help message
  quick       One-command build: update submodules → menuconfig → compile → package
  defconfig   Generate .config (merge gki_defconfig + vendor fragments)
  menuconfig  Open kernel menuconfig UI to edit .config interactively
  kernel      Build kernel (defconfig + Image + modules + dtbs)
  fast        Build kernel fast (defconfig + Image + dtbs only, no modules)
  all         Build kernel, install modules, and copy outputs to out/dist
  zip         Build kernel and package AnyKernel3 flashable zip
  package     Package AnyKernel3 zip from existing out/dist outputs
  modules     Install kernel modules (run after 'kernel')
  toolchain   Show current toolchain configuration
  install-deps  Install build dependencies (apt/dnf/pacman/zypper)
  clean       Clean build directory (preserves .config)
  deepclean   Deep clean: out/ + AnyKernel3/ + KernelSU artifacts + stale files
  mrproper    Full source tree clean (make mrproper + deepclean)
  update-submodules  Update KernelSU and AnyKernel3 to latest upstream

Environment variables:
  CLANG_PATH        Path to directory containing custom clang (e.g. /opt/clang/bin)
  GCC_PATH          Path to directory containing aarch64-linux-gnu-gcc
  JOBS              Override number of parallel jobs (default: nproc)
  KERNEL_NAME       Override kernel name shown in AnyKernel3 installer
  OUT_DIR           Override output directory (default: out)
  USE_CCACHE        Set to 1 to enable ccache (default: auto-detect)
  CCACHE_DIR        Override ccache directory (default: ~/.ccache)
  FAST_BUILD        Set to 1 to skip modules and only build Image+dtbs

AnyKernel3 configuration:
  AK3_DEVICE_CHECK  Set to 1 to enable device name check (default: 1)
  AK3_DEVICE_NAME1  Device name 1, e.g. 24122RKC7C (default: 24122RKC7C)
  AK3_DEVICE_NAME2  Device name 2 (default: miro)
  AK3_DEVICE_NAME3  Device name 3 (default: Redmi K80 Pro)
  AK3_DEVICE_NAME4  Device name 4 (default: empty)
  AK3_DEVICE_NAME5  Device name 5 (default: empty)
  AK3_BLOCK         Partition to flash (default: boot, e.g. auto, init_boot)
  AK3_SLOT_DEVICE   A/B slot device: 1, 0, or auto (default: 1)
  AK3_PATCH_VBMETA  Patch vbmeta to disable AVB: 1, 0, or auto (default: auto)

Tips for faster builds:
  1. Install ccache:  apt install ccache && ccache -M 50G
     - First build: normal speed. Subsequent builds: 3-5x faster.
  2. Use 'fast' command:  bash build.sh fast
     - Skips modules (~40% less compile time)
  3. Put out/ on tmpfs (RAM disk):
     mkdir -p /tmp/build && OUT_DIR=/tmp/build bash build.sh all
  4. Increase jobs:  JOBS=$(nproc) bash build.sh all

Examples:
  bash build.sh quick                                  # One-command: update+menuconfig+build+package
  bash build.sh install-deps                           # Install build dependencies
  bash build.sh zip                                    # Full build + flashable zip
  bash build.sh all                                    # Full build without packaging
  bash build.sh fast                                   # Quick build (no modules)
  bash build.sh kernel                                 # Just build kernel
  bash build.sh defconfig                              # Just generate config
  bash build.sh menuconfig                             # Edit .config interactively
  bash build.sh package                                # Re-package zip from existing build
  bash build.sh toolchain                              # Show toolchain info
  bash build.sh clean                                  # Clean out/ (keep .config)
  bash build.sh deepclean                              # Remove all build artifacts
  bash build.sh update-submodules                      # Update KernelSU + AnyKernel3 to upstream
  bash build.sh mrproper                               # Full source tree clean
  CLANG_PATH=/opt/clang/bin bash build.sh zip          # Build with custom clang
  USE_CCACHE=0 bash build.sh all                       # Disable ccache
  FAST_BUILD=1 bash build.sh zip                        # Fast zip (no modules in zip)
  AK3_DEVICE_CHECK=1 AK3_DEVICE_NAME1=24122RKC7C bash build.sh zip  # Enable device check
  AK3_BLOCK=init_boot bash build.sh zip                # Flash to init_boot partition

Project: https://github.com/ZHYxulei/miro-kernel-ultra
Issues:  https://github.com/ZHYxulei/miro-kernel-ultra/issues
EOF
}

# ============================================================
# Main
# ============================================================

main() {
    local cmd="${1:-help}"

    case "$cmd" in
        help|-h)
            show_help
            ;;
        quick)
            quick
            ;;
        defconfig)
            check_prerequisites
            init_submodules
            clean_source_tree
            make_defconfig
            ;;
        menuconfig)
            menuconfig
            ;;
        kernel)
            check_prerequisites
            init_submodules
            clean_source_tree
            make_defconfig
            build_kernel
            log "Total time: $(elapsed)"
            ;;
        fast)
            FAST_BUILD=1
            check_prerequisites
            init_submodules
            clean_source_tree
            make_defconfig
            build_kernel
            log "Total time: $(elapsed)"
            ;;
        all)
            check_prerequisites
            init_submodules
            clean_source_tree
            make_defconfig
            build_kernel
            install_modules
            copy_outputs
            log "Total time: $(elapsed)"
            ;;
        zip)
            check_prerequisites
            init_submodules
            clean_source_tree
            make_defconfig
            build_kernel
            install_modules
            copy_outputs
            package_anykernel3
            log "Total time: $(elapsed)"
            ;;
        package)
            package_anykernel3
            ;;
        modules)
            install_modules
            ;;
        toolchain)
            show_toolchain
            ;;
        install-deps)
            install_deps
            ;;
        clean)
            clean
            ;;
        deepclean)
            deepclean
            ;;
        mrproper)
            mrproper
            ;;
        update-submodules)
            update_submodules
            ;;
        *)
            error "Unknown command: $cmd"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
