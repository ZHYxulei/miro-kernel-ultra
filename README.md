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
| GKI `perf` / `consolidate` 构建变体 | 已支持 |
| DroidSpaces 配置片段 | 已接入构建配置 |
| SUSFS | 计划中 |
| ReSukiSU/KernelSU | 已接入（子模块） |
| 稳定性、性能和功耗优化 | 持续进行 |

当前 DroidSpaces 配置位于
[`arch/arm64/configs/droidspaces.fragment`](arch/arm64/configs/droidspaces.fragment)，
并通过 [`build.config.msm.perf`](build.config.msm.perf) 使用项目现有的
`apply_defconfig_fragment` 流程合并到 `perf` 和 `consolidate` 变体。

SUSFS 尚未在当前版本中实现。相关功能需要评估内核补丁、Kconfig、符号导出、
ABI/KMI 影响以及设备启动兼容性，完成验证后再更新本 README 的状态。

ReSukiSU/KernelSU 已作为 git 子模块接入，源码位于 `KernelSU/`（上游
`ReSukiSU/ReSukiSU`），内核驱动通过符号链接 `drivers/kernelsu -> ../KernelSU/kernel`
接入驱动树，并在 `gki_defconfig` 中启用了 `CONFIG_KSU`、`CONFIG_KSU_SUSFS`
和 `CONFIG_KSU_MANUAL_HOOK`。

## 基本信息

- 设备目标：`miro`
- Qualcomm 平台：`sun`
- 架构：ARM64
- Linux 基础版本：`6.6.30`
- Android 内核分支：`android15-6.6`
- 构建系统：Kleaf/Bazel，兼容 Android Kernel `build.config` 流程

## 构建

本仓库依赖完整的 Android Kernel/Kleaf 工作空间，通常作为 `msm-kernel` 目录
使用，并需要同级的 `build`、`common`、`prebuilts` 等组件。它不是一个可以
脱离 Android 构建环境直接生成设备镜像的独立 Linux 内核树。

### 1. 克隆仓库

```bash
git clone --recursive https://github.com/ZHYxulei/miro-kernel-ultra.git msm-kernel
```

`--recursive` 会在克隆时自动初始化并拉取 `KernelSU` 子模块。

如果已经克隆但未带 `--recursive`，需要手动初始化子模块：

```bash
cd msm-kernel
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

### 2. Kleaf/Bazel 构建

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

### 3. 传统 build.config 流程

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
  - `CONFIG_KSU_SUSFS` — SUSFS 集成接口
  - `CONFIG_KSU_MANUAL_HOOK` — 手动 hook 点

克隆仓库后必须执行 `git submodule update --init --recursive` 以拉取子模块，
否则 `drivers/kernelsu` 符号链接将无法解析，构建会失败。

## 开发计划

后续工作将按以下方向推进：

1. 评估并集成 SUSFS，确认其与 Android 6.6、GKI/KMI 以及现有文件系统配置的兼容性。
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
