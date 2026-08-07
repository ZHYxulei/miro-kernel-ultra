# Miro Kernel Ultra

面向 `miro` 设备的 Android ARM64 内核项目，基于 Android Common Kernel
`android15-6.6`，用于维护设备侧内核配置、驱动和构建流程。

## 项目目标

本项目主要围绕以下方向持续开发：

- 增加并完善 DroidSpaces 支持，为容器化 Android/Linux 用户空间提供所需的 IPC、namespace、网络过滤、用户命名空间和 TMPFS 能力。
- 集成 SUSFS 支持，改善系统文件系统隐藏、隔离和兼容性能力。
- 集成 ReSukiSU/KernelSU 支持，为内核级 root 管理和模块扩展提供基础。
- 持续进行内核配置、驱动、稳定性、性能和功耗优化。
- 在保证设备可启动性、GKI/KMI 兼容性和现有硬件功能的前提下，逐步完善设备支持。

## 项目状态

| 功能 | 状态 |
| --- | --- |
| `miro` ARM64 内核构建 | 已支持 |
| 独立编译脚本 `build.sh` | 已支持（LLVM 工具链 + AnyKernel3 打包） |
| GKI `perf` / `consolidate` 构建变体 | 已支持 |
| DroidSpaces 配置片段 | 已接入构建配置 |
| SUSFS | 已接入 |
| ReSukiSU/KernelSU | 已接入（子模块） |
| 稳定性、性能和功耗优化 | 持续进行 |

当前 DroidSpaces 配置位于
[`arch/arm64/configs/droidspaces.fragment`](arch/arm64/configs/droidspaces.fragment)，
并通过 [`build.config.msm.perf`](build.config.msm.perf) 使用项目现有的
`apply_defconfig_fragment` 流程合并到 `perf` 和 `consolidate` 变体。

SUSFS 已集成到内核树中。SUSFS（Super User File System）提供内核级 root
隐藏和进程隔离能力，包括可疑路径隐藏、挂载隐藏、kstat 伪造、uname 伪造、
cmdline/bootconfig 伪造、open redirect 和内存映射隐藏等功能。SUSFS 补丁
源自 [`susfs4ksu`](https://gitlab.com/simonpunk/susfs4ksu) 的
`gki-android15-6.6` 分支，ReSukiSU 内置 SUSFS 支持作为 inline hook 方式。

ReSukiSU/KernelSU 已作为 git 子模块接入，源码位于 `KernelSU/`（上游
`ReSukiSU/ReSukiSU`），内核驱动通过符号链接 `drivers/kernelsu -> ../KernelSU/kernel`
接入驱动树，并在 `gki_defconfig` 中启用了 `CONFIG_KSU` 和 `CONFIG_KSU_SUSFS`。

## 基本信息

- 设备目标：`miro`
- Qualcomm 平台：`sun`
- 架构：ARM64
- Linux 基础版本：`6.6.30`
- Android 内核分支：`android15-6.6`
- 构建系统：`build.sh` 独立编译（LLVM 工具链），兼容 Kleaf/Bazel 和 `build.config` 流程

## 构建

本仓库支持两种构建方式：

1. **独立编译**（推荐）：使用项目自带的 `build.sh` 脚本，仅需系统安装的
   LLVM 工具链，无需完整 Android 源码树。
2. **Kleaf/Bazel 构建**：在完整的 Android Kernel 工作空间中使用，适合需要
   GKI/KMI 合规性检查的场景。

### 1. 克隆仓库

```bash
git clone --recursive https://github.com/ZHYxulei/miro-kernel-ultra.git
```

`--recursive` 会在克隆时自动初始化并拉取 `KernelSU` 子模块。

如果已经克隆但未带 `--recursive`，需要手动初始化子模块：

```bash
cd miro-kernel-ultra
git submodule update --init --recursive
```

验证子模块状态：

```bash
git submodule status
# 预期输出：
#  058cdc931016cb2cb769ed063cce6d65d6df61e0 KernelSU (v4.1.0-1338-g058cdc93)
```

确认驱动符号链接已就绪：

```bash
ls -l drivers/kernelsu
# 预期输出：drivers/kernelsu -> ../KernelSU/kernel
```

### 2. 独立编译（build.sh）

#### 依赖安装

```bash
apt install clang lld llvm flex bison bc cpio device-tree-compiler \
    libelf-dev libssl-dev libncurses-dev zip
```

#### 命令一览

```bash
bash build.sh help          # 查看帮助
bash build.sh defconfig     # 仅生成 .config
bash build.sh kernel        # 编译内核（Image + modules + dtbs）
bash build.sh all           # 编译 + 安装模块 + 输出到 out/dist
bash build.sh zip           # 编译 + 打包 AnyKernel3 刷机包
bash build.sh package       # 从已有 out/dist 重新打包 AnyKernel3
bash build.sh clean         # 清理构建产物（保留 .config）
bash build.sh mrproper      # 深度清理（包括 .config）
```

#### 完整编译并打包刷机包

```bash
bash build.sh zip
```

该命令会依次执行：
1. 检查编译依赖
2. 初始化 KernelSU 子模块
3. 合并 defconfig 片段（`gki_defconfig` + `sun_perf` + `miro_perf` + `droidspaces`）
4. 应用后置配置（启用 `PINCTRL_MSM` 等）
5. 编译内核（Image + modules + dtbs）
6. 安装内核模块
7. 复制构建产物到 `out/dist/`
8. 克隆/复用 AnyKernel3 模板，注入内核产物并生成刷机 zip

生成的刷机包位于：

```text
out/dist/miro-kernel-ultra-YYYYMMDD-HHMM.zip
```

#### 构建产物

| 文件 | 说明 |
| --- | --- |
| `out/dist/Image` | 内核镜像 |
| `out/dist/dtb` | 设备树 blob |
| `out/dist/dtbo.img` | DTBO 镜像 |
| `out/dist/modules/` | 内核模块 |
| `out/dist/vmlinux` | 未压缩内核（调试用） |
| `out/dist/System.map` | 符号表 |
| `out/dist/.config` | 最终内核配置 |

#### 配置说明

`build.sh` 使用 LLVM 工具链（`clang` + `ld.lld`）进行交叉编译，合并以下
defconfig 片段：

1. `arch/arm64/configs/gki_defconfig` — GKI 基础配置
2. `arch/arm64/configs/vendor/sun_perf.config` — Qualcomm Sun 平台配置
3. `arch/arm64/configs/vendor/miro_perf.config` — 小米 miro 设备配置
4. `arch/arm64/configs/droidspaces.fragment` — DroidSpaces 配置

合并后通过 `olddefconfig` 自动解析新增的 Kconfig 选项，避免交互式提示。

### 3. Kleaf/Bazel 构建

在 Android Kernel 工作区根目录执行：

```bash
tools/bazel run //msm-kernel:miro_perf_dist
tools/bazel run //msm-kernel:miro_consolidate_dist
```

构建产物通常位于：

```text
out/msm-kernel-miro-perf/dist/
out/msm-kernel-miro-consolidate/dist/
```

### 4. 传统 build.config 流程

传统配置入口为：

```text
build.config.msm.miro
```

该入口继承 `build.config.msm.sun`，默认使用 `consolidate` 变体，同时支持
`perf` 变体。具体构建参数以当前 Android Kernel 工作区为准。

## DroidSpaces

DroidSpaces 配置片段当前启用以下能力：

- SysV IPC 和 POSIX message queues
- IPC namespace、PID namespace 和 user namespace
- `devtmpfs`
- Enhanced NAT 所需的 Netfilter 匹配项
- UFW 所需的 Netfilter target 和 match
- Fail2ban 所需的 IP set
- TMPFS POSIX ACL 和 extended attributes

配置会在构建阶段与 GKI 和设备配置片段合并，随后由 Kconfig 生成最终内核
配置。若某个构建变体没有经过 `build.config.msm.perf`，则不会自动加载该
片段。

## ReSukiSU/KernelSU

ReSukiSU/KernelSU 通过 git 子模块方式接入：

- **子模块**：`KernelSU/`，上游为 [`ReSukiSU/ReSukiSU`](https://github.com/ReSukiSU/ReSukiSU)
- **驱动链接**：`drivers/kernelsu -> ../KernelSU/kernel`（符号链接）
- **Kconfig 入口**：[`drivers/Kconfig`](drivers/Kconfig) 中 `source "drivers/kernelsu/Kconfig"`
- **构建入口**：[`drivers/Makefile`](drivers/Makefile) 中 `obj-$(CONFIG_KSU) += kernelsu/`
- **内核配置**（在 [`gki_defconfig`](arch/arm64/configs/gki_defconfig) 中启用）：
  - `CONFIG_KPROBE_EVENTS` — kprobe 事件支持
  - `CONFIG_KSU` — KernelSU 核心
  - `CONFIG_KSU_SUSFS` — SUSFS inline hook 模式（ReSukiSU 三选一 hook 方式之一）

`CONFIG_KSU_SUSFS` 与 `CONFIG_KSU_MANUAL_HOOK` 互斥（Kconfig `choice`），
当前选择 SUSFS inline hook 模式，由 susfs4ksu 补丁提供内核侧 hook 点。

克隆仓库后必须执行 `git submodule update --init --recursive` 以拉取子模块，
否则 `drivers/kernelsu` 符号链接将无法解析，构建会失败。

## SUSFS

SUSFS（Super User File System）是 KernelSU 的 root 隐藏内核补丁，提供以下能力：

| 功能 | Kconfig 选项 | 说明 |
| --- | --- | --- |
| 路径隐藏 | `CONFIG_KSU_SUSFS_SUS_PATH` | 隐藏用户定义路径及其子路径 |
| 挂载隐藏 | `CONFIG_KSU_SUSFS_SUS_MOUNT` | 隐藏可疑挂载，伪造 mnt_id |
| kstat 伪造 | `CONFIG_KSU_SUSFS_SUS_KSTAT` | 伪造文件/目录的 kstat |
| uname 伪造 | `CONFIG_KSU_SUSFS_SPOOF_UNAME` | 伪造 uname 返回值 |
| 日志控制 | `CONFIG_KSU_SUSFS_ENABLE_LOG` | 内核 SUSFS 日志开关 |
| 符号隐藏 | `CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS` | 从 /proc/kallsyms 隐藏符号 |
| cmdline 伪造 | `CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG` | 伪造 /proc/bootconfig 或 /proc/cmdline |
| 路径重定向 | `CONFIG_KSU_SUSFS_OPEN_REDIRECT` | 将目标路径重定向到另一路径 |
| 内存映射隐藏 | `CONFIG_KSU_SUSFS_SUS_MAP` | 从 proc maps 隐藏 mmapped 文件 |

### 集成方式

SUSFS 补丁源自 [`susfs4ksu`](https://gitlab.com/simonpunk/susfs4ksu) 的
`gki-android15-6.6` 分支，按照其 README 指引集成：

- **新增文件**：`fs/susfs.c`、`include/linux/susfs.h`、`include/linux/susfs_def.h`
- **内核补丁**：`50_add_susfs_in_gki-android15-6.6.patch` 修改 24 个内核文件，
  在 syscall 路径插入 SUSFS hook（fs/exec.c、fs/namei.c、fs/namespace.c、
  security/selinux/ 等）
- **手动适配**：4 处 hunk 因 Xiaomi 内核 include 顺序差异手动修复
  （fs/namespace.c、fs/proc/base.c、fs/proc/task_mmu.c、mm/memory.c）
- **ABI 文件**：已删除 `android/abi_gki_protected_exports_aarch64`
  （susfs4ksu README step 11 要求，否则 WiFi 等模块可能不工作）
- **KernelSU 侧补丁**：跳过 `10_enable_susfs_for_ksu.patch`
  （ReSukiSU 已内置 SUSFS 支持，该补丁仅适用于原版 weishu KernelSU）

所有 SUSFS 子功能在 ReSukiSU 的 Kconfig 中默认为 `y`，由 `CONFIG_KSU_SUSFS=y`
统一激活。构建时 ReSukiSU 的 `inline_hook_check.mk` 会自动验证内核 hook 点
是否存在，缺失则编译报错。

## 开发计划

后续工作将按以下方向推进：

1. 验证 SUSFS 内核编译和设备启动兼容性，确认 GKI/KMI 影响。
2. 为新增功能补充独立配置片段和构建入口，避免影响默认 GKI 配置。
3. 完善启动、模块、网络、容器和文件系统场景的验证。
4. 持续优化性能、功耗、稳定性和构建可复现性。

## 兼容性说明

本项目处于持续开发阶段。新增内核功能可能影响 ABI/KMI、模块加载、系统启动
和安全模型。合并任何功能前，应至少验证对应构建变体的配置生成、内核编译、
模块打包和设备启动情况，并保留清晰的变更记录。

## 贡献

提交修改前请确认：

- 变更范围与项目目标相关；
- 配置片段使用现有构建系统的合并流程；
- 不提交密钥、设备个人数据或构建产物；
- 对配置、驱动和构建脚本修改进行对应验证；
- 提交信息清楚说明变更内容和验证结果。

## 免责声明

本项目面向研究、开发和设备适配用途。刷写或使用自定义内核可能导致设备
无法启动、数据丢失、功能异常或安全边界变化。请在充分备份并了解风险后使用。

## 开源许可证

本项目基于 Linux 内核，遵循 [`GPL-2.0 WITH Linux-syscall-note`](COPYING)
开源许可证。项目中集成的第三方组件各自保持其原始许可证：

- **Linux 内核**：GPL-2.0 WITH Linux-syscall-note
- **ReSukiSU/KernelSU**：GPL-2.0（子模块 `KernelSU/`）
- **SUSFS (susfs4ksu)**：GPL-2.0（内核补丁部分）

使用、修改和分发本项目代码须遵守上述许可证条款。
