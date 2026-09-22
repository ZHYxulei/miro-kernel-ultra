#!/usr/bin/env bash
#
# Generate the bilingual (中文 / English) release notes for a tag.
#
# If release-notes/<tag>.md exists it is used verbatim as the "changes"
# section, which lets a release describe what actually changed in both
# languages. Otherwise the commit subjects between the previous tag and this
# one are listed so the page is never empty.
#
# Usage: scripts/ci/release-notes.sh <tag> [output-file]
#
# Environment:
#   GITHUB_REPOSITORY   owner/repo, used for the compare link (optional)
#   RELEASE_NOTES_DIR   where curated notes live (default: release-notes)
set -euo pipefail

version="${1:-${GITHUB_REF_NAME:-}}"
out_file="${2:-}"
notes_dir="${RELEASE_NOTES_DIR:-release-notes}"

fail() {
    printf 'release-notes: %s\n' "$*" >&2
    exit 1
}

[ -n "${version}" ] || fail "usage: $0 <tag> [output-file]"

curated_file="${notes_dir}/${version}.md"

if [ -f "${curated_file}" ]; then
    changes_section="$(cat "${curated_file}")"
else
    prev_tag="$(git describe --tags --abbrev=0 --match 'v*' "${version}^" 2>/dev/null || true)"
    if [ -n "${prev_tag}" ]; then
        range="${prev_tag}..${version}"
    else
        range="${version}"
    fi

    subjects="$(git log --no-merges --pretty=format:'- %s' "${range}" 2>/dev/null \
        | grep -viE '^- (merge|bump|chore)' || true)"
    if [ -z "${subjects}" ]; then
        subjects="- 维护性更新，无用户可见变更 / Maintenance update with no user visible changes"
    fi

    changes_section="$(printf '%s\n' "${subjects}" | head -n 40)"
    if [ -n "${prev_tag}" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
        changes_section="${changes_section}

完整差异 / Full diff: https://github.com/${GITHUB_REPOSITORY}/compare/${prev_tag}...${version}"
    fi
fi

standard_zip="miro-kernel-ultra-${version}.zip"
kpatch_zip="miro-kernel-ultra-kpatch-exp-${version}.zip"
debug_zip="miro-kernel-ultra-debug-${version}.zip"
kpatch_debug_zip="miro-kernel-ultra-kpatch-exp-debug-${version}.zip"
debug_archive="miro-kernel-ultra-debug-symbols-${version}.tar.zst"

read -r -d '' notes <<NOTES || true
# miro-kernel-ultra ${version}

Redmi K80 Pro（24122RKC7C / miro）Android 15 GKI 内核。每个版本同时提供标准版与
KPatch-Next 实验版，并各自提供 release（已剥离）与 debug（保留调试信息）两种刷机包。

Android 15 GKI kernel for the Redmi K80 Pro (24122RKC7C / miro). Every release
ships a standard and an experimental KPatch-Next package, each in a release
(stripped) and a debug (debug info kept) flavour.

## 本版变更 / Changes in this release

${changes_section}

## 下载内容 / Release contents

### 刷机包 / Flashable packages

| 文件 / File | 说明 / Description |
| --- | --- |
| \`${standard_zip}\` | 标准版，日常使用 / Standard package, for daily use |
| \`${kpatch_zip}\` | KPatch-Next 实验版 / Experimental KPatch-Next package |
| \`${debug_zip}\` | 标准版 debug，模块保留调试信息 / Standard debug package, modules keep debug info |
| \`${kpatch_debug_zip}\` | KPatch-Next 实验版 debug / Experimental KPatch-Next debug package |

四个刷机包的内核 \`Image\` 与 AnyKernel3 配置完全相同，区别只在
随包的模块：release 包的模块已用 \`llvm-strip\` 剥离调试信息（体积小），debug 包的模块
保留完整调试信息，可直接 \`objdump\` 定位崩溃点。**只刷机请用 release 包**；debug 包体积
明显更大，仅在排查问题时使用。

All four packages carry the exact same kernel \`Image\` and AnyKernel3
configuration. They differ only in the modules they ship: the release
packages carry modules stripped with \`llvm-strip\` (small), while the debug
packages keep full debug info so a crash can be pinpointed with \`objdump\`.
**Use the release packages for normal flashing**; the debug ones are much larger
and only useful when debugging.

### 内核镜像 / Raw kernel images

| 文件 / File | 说明 / Description |
| --- | --- |
| \`Image\` | 标准版内核镜像，供 fastboot 手动刷写或自行打包 / Raw standard kernel image for fastboot or custom repacking |
| \`Image-kpatch-next-exp\` | 已嵌入 KPatch-Next 的内核镜像 / Kernel image with KPatch-Next embedded |

### 调试符号 / Debug symbols

| 文件 / File | 说明 / Description |
| --- | --- |
| \`${debug_archive}\` | 调试符号归档，内含未剥离的模块、\`vmlinux\`、\`System.map\`、\`vmlinux.symvers\`、\`.config\` / Debug symbols archive with unstripped modules, \`vmlinux\`, \`System.map\`, \`vmlinux.symvers\` and \`.config\` |
| \`System.map\` | 内核符号表，用于把地址翻译成符号名 / Kernel symbol table for address to symbol lookup |
| \`vmlinux.symvers\` | 内核模块符号 CRC，编译外部模块时需要 / Module symbol CRCs, needed to build out-of-tree modules |

调试符号用于在崩溃后把地址还原成函数名：\`vmlinux\` 供 \`addr2line\`，
\`System.map\` 供符号查表，未剥离的模块供 \`objdump\`。日常刷机不需要下载。

These let a crash address be resolved back to a function name: \`vmlinux\` for
\`addr2line\`, \`System.map\` for symbol lookup and the unstripped modules for
\`objdump\`. You do not need them for a normal flash.

### 校验和 / Checksums

| 文件 / File | 覆盖的文件 / Covers |
| --- | --- |
| \`SHA256SUMS-standard\` | \`${standard_zip}\`、\`Image\` |
| \`SHA256SUMS-kpatch-exp\` | \`${kpatch_zip}\`、\`Image-kpatch-next-exp\` |
| \`SHA256SUMS-debug\` | \`${debug_zip}\` |
| \`SHA256SUMS-debug-kpatch-exp\` | \`${kpatch_debug_zip}\` |
| \`SHA256SUMS-debug-symbols\` | \`${debug_archive}\`、\`System.map\`、\`vmlinux.symvers\` |

## 刷入方法 / Installation

### 使用 KernelFlasher / Using KernelFlasher

1. 下载想要刷入的 zip 压缩包（建议 release 标准版） / Download the ZIP you want (the release standard package is the recommended default).
2. 使用 KernelFlasher 刷入 boot 分区 / Flash it to the boot partition.
3. 重启即可 / Reboot.

刷机包基于 AnyKernel3，模块通过 \`ak3-helper\` 模块随刷入一起投递，无需额外操作。

The packages are AnyKernel3 ZIPs; modules are delivered automatically through the
\`ak3-helper\` module, no manual step required.

刷入前请务必备份当前 boot 镜像；首次刷入 KPatch 实验版建议保留可回退的原厂镜像。

Back up your current boot image before flashing. For a first flash of the
experimental KPatch package, keep a known good image you can roll back to.

## 完整性校验 / Integrity verification

Release assets are accompanied by SHA256 checksums; verify before flashing:

\`\`\`bash
sha256sum -c SHA256SUMS-standard          # 标准版内核与镜像 / standard kernel + Image
sha256sum -c SHA256SUMS-kpatch-exp        # 实验版内核与镜像 / experimental kernel + Image
sha256sum -c SHA256SUMS-debug             # 标准版 debug 刷机包 / standard debug package
sha256sum -c SHA256SUMS-debug-kpatch-exp  # 实验版 debug 刷机包 / experimental debug package
sha256sum -c SHA256SUMS-debug-symbols     # 调试符号归档 / debug symbols archive
\`\`\`

## 设备检测 / Device check

- 24122RKC7C
- miro
- Redmi K80 Pro

## 发布前验证 / Pre-release verification

每个版本发布前都会在 CI 中完成以下检查：完整构建、四个刷机包的产物校验（ZIP 结构、
镜像一致性、模块逐个字节比对以确认 debug 包确实保留、release 包确实剥离调试信息），
以及在 QEMU 中真实启动该内核并确认进入用户态。

Every release is gated in CI by a full build, artifact verification of all four
packages (ZIP layout, image consistency and a per-module byte comparison that
proves the debug packages kept and the release packages dropped their debug
info) and a real boot of the produced kernel under QEMU that has to reach
userspace.
NOTES

if [ -n "${out_file}" ]; then
    printf '%s\n' "${notes}" > "${out_file}"
    printf 'release-notes: wrote %s\n' "${out_file}" >&2
else
    printf '%s\n' "${notes}"
fi
