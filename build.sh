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
#   bash build.sh defconfig     Only generate .config
#   bash build.sh kernel        Build kernel (Image + modules + dtbs)
#   bash build.sh all           Build kernel and copy outputs to out/dist
#   bash build.sh zip           Build kernel and package AnyKernel3 flashable zip
#   bash build.sh clean         Clean build directory (preserves .config)
#   bash build.sh deepclean     Deep clean: out/ + AnyKernel3/ + submodule artifacts
#   bash build.sh mrproper      Full source tree clean (make mrproper + deepclean)
#   bash build.sh toolchain     Show current toolchain configuration
#
# Environment variables:
#   CLANG_PATH        Path to directory containing custom clang (e.g. /opt/clang/bin)
#   GCC_PATH          Path to directory containing aarch64-linux-gnu-gcc (for CROSS_COMPILE)
#   JOBS              Override number of parallel jobs (default: nproc)
#   KERNEL_NAME       Override kernel name shown in AnyKernel3
#   OUT_DIR           Override output directory (default: out)
#   USE_CCACHE        Set to 1 to enable ccache (default: auto-detect)
#   CCACHE_DIR        Override ccache directory (default: ~/.ccache)
#   FAST_BUILD        Set to 1 to skip modules and only build Image+dtbs
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
ANYKERNEL3_REPO="https://github.com/osm0sis/AnyKernel3.git"
ANYKERNEL3_ZIP="miro-kernel-ultra-$(date +%Y%m%d-%H%M).zip"

# Kernel name shown in AnyKernel3 installer
KERNEL_NAME="miro-kernel-ultra"

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
    if [ "${FAST_BUILD}" = "1" ]; then
        log "Building kernel (fast: Image + dtbs only, no modules)..."
        make "${MAKE_ARGS[@]}" Image dtbs
    else
        log "Building kernel (Image + modules + dtbs)..."
        make "${MAKE_ARGS[@]}" Image modules dtbs
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
    make "${MAKE_ARGS[@]}" INSTALL_MOD_PATH=${OUT_DIR}/modules_install modules_install
    # Remove symlinks and build artifacts
    rm -f "${OUT_DIR}/modules_install/lib/modules/"*/build
    rm -f "${OUT_DIR}/modules_install/lib/modules/"*/source
}

setup_anykernel3() {
    if [ -d "${ANYKERNEL3_DIR}/.git" ]; then
        log "AnyKernel3 submodule found, using existing."
    elif [ -n "${ANYKERNEL3_REPO}" ]; then
        log "AnyKernel3 not initialized, cloning..."
        git clone --depth=1 "${ANYKERNEL3_REPO}" "${ANYKERNEL3_DIR}"
    else
        error "AnyKernel3 not found. Run: git submodule update --init AnyKernel3"
        exit 1
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

    # Configure anykernel3.sh for this device
    local ak3_sh="${ANYKERNEL3_DIR}/anykernel3.sh"
    if [ -f "${ak3_sh}" ]; then
        sed -i \
            -e "s/^kernel.string=.*/kernel.string=${KERNEL_NAME}/" \
            -e "s/^block=.*/block=auto/" \
            -e "s/^kernel_type=.*/kernel_type=Image/" \
            -e "s/^dtbo_enable=.*/dtbo_enable=true/" \
            -e "s/^module=.*/module=none/" \
            "${ak3_sh}"
        log "  Configured anykernel3.sh"
    fi

    # Create flashable zip
    local zip_path="${DIST_DIR}/${ANYKERNEL3_ZIP}"
    mkdir -p "${DIST_DIR}"
    ( cd "${ANYKERNEL3_DIR}" && zip -r9 "${SCRIPT_DIR}/${zip_path}" . -x ".git/*" "README.md" )
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

    # Also deepclean AnyKernel3 and submodule artifacts
    if [ -d "${ANYKERNEL3_DIR}" ]; then
        rm -rf "${ANYKERNEL3_DIR}"
        log "  Removed ${ANYKERNEL3_DIR}/"
    fi
    if [ -d "KernelSU/kernel" ]; then
        rm -f KernelSU/kernel/built-in.a
        log "  Cleaned KernelSU submodule artifacts"
    fi

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

show_help() {
    cat << 'EOF'
build.sh - Build script for miro-kernel-ultra

Usage: bash build.sh <command>

Commands:
  help        Show this help message
  defconfig   Generate .config (merge gki_defconfig + vendor fragments)
  kernel      Build kernel (defconfig + Image + modules + dtbs)
  fast        Build kernel fast (defconfig + Image + dtbs only, no modules)
  all         Build kernel, install modules, and copy outputs to out/dist
  zip         Build kernel and package AnyKernel3 flashable zip
  package     Package AnyKernel3 zip from existing out/dist outputs
  modules     Install kernel modules (run after 'kernel')
  toolchain   Show current toolchain configuration
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

Tips for faster builds:
  1. Install ccache:  apt install ccache && ccache -M 50G
     - First build: normal speed. Subsequent builds: 3-5x faster.
  2. Use 'fast' command:  bash build.sh fast
     - Skips modules (~40% less compile time)
  3. Put out/ on tmpfs (RAM disk):
     mkdir -p /tmp/build && OUT_DIR=/tmp/build bash build.sh all
  4. Increase jobs:  JOBS=$(nproc) bash build.sh all

Examples:
  bash build.sh zip                                    # Full build + flashable zip
  bash build.sh all                                    # Full build without packaging
  bash build.sh fast                                   # Quick build (no modules)
  bash build.sh kernel                                 # Just build kernel
  bash build.sh defconfig                              # Just generate config
  bash build.sh package                                # Re-package zip from existing build
  bash build.sh toolchain                              # Show toolchain info
  bash build.sh clean                                  # Clean out/ (keep .config)
  bash build.sh deepclean                              # Remove all build artifacts
  bash build.sh update-submodules                      # Update KernelSU + AnyKernel3 to upstream
  bash build.sh mrproper                               # Full source tree clean
  CLANG_PATH=/opt/clang/bin bash build.sh zip          # Build with custom clang
  USE_CCACHE=0 bash build.sh all                       # Disable ccache
  FAST_BUILD=1 bash build.sh zip                        # Fast zip (no modules in zip)
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
        defconfig)
            check_prerequisites
            init_submodules
            clean_source_tree
            make_defconfig
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
