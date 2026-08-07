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
#   bash build.sh clean         Clean build directory
#   bash build.sh mrproper      Deep clean (also removes .config)
#

set -e

# ============================================================
# Configuration
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ARCH=arm64
DEFCONFIG=gki_defconfig
OUT_DIR="out"
DIST_DIR="out/dist"
JOBS=$(nproc --all)

# Toolchain: use system-installed LLVM
export LLVM=1
export ARCH=${ARCH}

# Clang cross-compilation triple
CLANG_TRIPLE=aarch64-linux-gnu-
CROSS_COMPILE=aarch64-linux-gnu-
CROSS_COMPILE_COMPAT=arm-linux-gnueabi-

# Common make arguments
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
    local missing=()
    for tool in clang ld.lld llvm-strip flex bison bc cpio dtc; do
        command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing tools: ${missing[*]}"
        error "Install with: apt install clang lld llvm flex bison bc cpio device-tree-compiler libelf-dev libssl-dev libncurses-dev"
        exit 1
    fi
    log "Clang version: $(clang --version | head -1)"
}

init_submodules() {
    log "Initializing submodules..."
    if [ -f .gitmodules ]; then
        git submodule update --init --recursive
    else
        log "No .gitmodules found, skipping."
    fi
}

make_defconfig() {
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
}

build_kernel() {
    log "Building kernel..."
    make "${MAKE_ARGS[@]}" Image modules dtbs
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
    if [ -d "${ANYKERNEL3_DIR}" ]; then
        log "AnyKernel3 directory found, using existing."
    else
        log "Cloning AnyKernel3..."
        git clone --depth=1 "${ANYKERNEL3_REPO}" "${ANYKERNEL3_DIR}"
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
    log "Cleaning build directory..."
    make "${MAKE_ARGS[@]}" clean
    rm -rf "${OUT_DIR}"
    log "Done."
}

mrproper() {
    log "Running mrproper..."
    make "${MAKE_ARGS[@]}" mrproper
    rm -rf "${OUT_DIR}"
    log "Done."
}

show_help() {
    cat << 'EOF'
build.sh - Build script for miro-kernel-ultra

Usage: bash build.sh <command>

Commands:
  help        Show this help message
  defconfig   Generate .config (merge gki_defconfig + vendor fragments)
  kernel      Build kernel (defconfig + Image + modules + dtbs)
  all         Build kernel, install modules, and copy outputs to out/dist
  zip         Build kernel and package AnyKernel3 flashable zip
  package     Package AnyKernel3 zip from existing out/dist outputs
  modules     Install kernel modules (run after 'kernel')
  clean       Clean build artifacts (preserves .config)
  mrproper    Deep clean (removes everything including .config)

Environment:
  LLVM=1      Use LLVM toolchain (clang + ld.lld) [default: 1]

Examples:
  bash build.sh zip              # Full build + AnyKernel3 flashable zip
  bash build.sh all              # Full build without packaging
  bash build.sh kernel           # Just build kernel
  bash build.sh defconfig        # Just generate config
  bash build.sh package          # Re-package zip from existing build
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
            make_defconfig
            ;;
        kernel)
            check_prerequisites
            init_submodules
            make_defconfig
            build_kernel
            log "Total time: $(elapsed)"
            ;;
        all)
            check_prerequisites
            init_submodules
            make_defconfig
            build_kernel
            install_modules
            copy_outputs
            log "Total time: $(elapsed)"
            ;;
        zip)
            check_prerequisites
            init_submodules
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
        clean)
            clean
            ;;
        mrproper)
            mrproper
            ;;
        *)
            error "Unknown command: $cmd"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
