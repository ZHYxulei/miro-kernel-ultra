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
debug_archive="miro-kernel-ultra-debug-${version}.tar.zst"

read -r -d '' notes <<NOTES || true
# miro-kernel-ultra ${version}

Redmi K80 Pro（24122RKC7C / miro）Android 15 GKI 内核，同时提供标准版与 KPatch-Next 实验版两个刷机包。

Android 15 GKI kernel for the Redmi K80 Pro (24122RKC7C / miro), published as a
standard package and an experimental KPatch-Next package.

## 本版变更 / Changes in this release

${changes_section}

## 下载内容 / Release contents

| 文件 / File | 说明 / Description |
| --- | --- |
| \`${standard_zip}\` | 标准版刷机包（boot 分区，A/B 插槽）/ Standard flashable package |
| \`${kpatch_zip}\` | KPatch-Next 实验版刷机包 / Experimental KPatch-Next package |
| \`${debug_archive}\` | 调试符号归档（未剥离模块 + \`vmlinux\` + \`System.map\`）/ Debug symbols archive (unstripped modules, \`vmlinux\`, \`System.map\`) |
| \`SHA256SUMS-standard\` | 标准版校验和 / Checksums for the standard package |
| \`SHA256SUMS-kpatch-exp\` | 实验版校验和 / Checksums for the experimental package |
| \`SHA256SUMS-debug\` | 调试符号归档校验和 / Checksums for the debug symbols archive |

- 标准版集成 ReSukiSU 内核级 root 与 SUSFS 隐藏，适合日常使用。
- kpatch-exp 版在标准版基础上嵌入 KPatch-Next KPM 运行时，属于实验构建。
- 刷机包基于 AnyKernel3，包含内核 \`Image\`、\`dtb\`、\`dtbo.img\` 以及随内核一起编译的模块（模块通过 \`ak3-helper\` 模块投递，无需额外操作）。刷机包内的模块已剥离调试符号以控制体积。
- 调试符号归档用于在崩溃后将地址还原为函数名：\`vmlinux\` 用于 \`addr2line\`，\`System.map\` 用于符号查表，未剥离模块用于 \`objdump\`。平时无需下载。

- The standard package ships ReSukiSU root management plus SUSFS hiding and is
  the one to use day to day.
- The kpatch-exp package additionally embeds the KPatch-Next KPM runtime and is
  experimental.
- Both ZIPs are AnyKernel3 packages carrying the kernel \`Image\`, the \`dtb\`, the
  \`dtbo.img\` and every module built alongside the kernel. Modules are delivered
  through the \`ak3-helper\` module, no manual step required. Modules inside the
  ZIP are stripped of debug info to keep the package small.
- The debug symbols archive lets a crash address be resolved back to a function
  name: \`vmlinux\` for \`addr2line\`, \`System.map\` for symbol lookup and the
  unstripped modules for \`objdump\`. You do not need it for a normal flash.

## 刷入方法 / Installation

### 使用 KernelFlasher / Using KernelFlasher

1. 下载对应版本的 zip 压缩包 / Download the ZIP you want.
2. 使用 KernelFlasher 刷入到 boot 分区 / Flash it to the boot partition.
3. 重启即可 / Reboot.

刷入前请务必备份当前 boot 镜像；首次刷入 KPatch 实验版建议保留可回退的原厂镜像。

Back up your current boot image before flashing. For a first flash of the
experimental KPatch package, keep a known good image you can roll back to.

## 完整性校验 / Integrity verification

Release assets are accompanied by SHA256 checksums; verify before flashing:

\`\`\`bash
sha256sum -c SHA256SUMS-standard      # 标准版 / standard
sha256sum -c SHA256SUMS-kpatch-exp    # 实验版 / experimental
sha256sum -c SHA256SUMS-debug         # 调试符号 / debug symbols
\`\`\`

## 设备检测 / Device check

- 24122RKC7C
- miro
- Redmi K80 Pro

## 发布前验证 / Pre-release verification

每个版本发布前都会在 CI 中完成以下检查：完整构建、产物校验（ZIP 结构与模块完整性）、以及在 QEMU 中真实启动该内核并确认进入用户态。

Every release is gated in CI by a full build, artifact verification (ZIP layout
and module completeness) and a real boot of the produced kernel under QEMU that
has to reach userspace.
NOTES

if [ -n "${out_file}" ]; then
    printf '%s\n' "${notes}" > "${out_file}"
    printf 'release-notes: wrote %s\n' "${out_file}" >&2
else
    printf '%s\n' "${notes}"
fi
