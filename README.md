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
| 独立编译脚本 `build.sh` | 已支持（LLVM 工具链 + AnyKernel3 打包 + 错误捕获） |
| 一条龙编译命令 `quick` | 已支持（更新子模块 + menuconfig + 编译 + 打包） |
| GKI `perf` / `consolidate` 构建变体 | 已支持 |
| DroidSpaces 配置片段 | 已接入构建配置 |
| SUSFS | 已接入 |
| ReSukiSU/KernelSU | 已接入（子模块） |
| AnyKernel3 刷机包打包 | 已支持（子模块） |
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

**推荐使用 SSH 协议**克隆代码库，不建议使用 HTTPS 协议。SSH 协议的优势：

- **无需重复输入密码**：配置 SSH 密钥后自动认证，无需每次输入用户名和密码
- **更高的安全性**：SSH 使用非对称加密进行身份验证，比 HTTPS 的密码认证更安全
- **更好的稳定性**：不受 GitHub HTTPS 限流影响，大仓库克隆更稳定

```bash
git clone --recursive git@github.com:ZHYxulei/miro-kernel-ultra.git
```

> 如果尚未配置 SSH 密钥，请参考 [GitHub SSH 密钥配置指南](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)。
> 在没有 SSH 密钥的环境下，也可使用 HTTPS 作为备选：
> `git clone --recursive https://github.com/ZHYxulei/miro-kernel-ultra.git`

`--recursive` 会在克隆时自动初始化并拉取 `KernelSU` 和 `AnyKernel3` 子模块。

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
#  <hash> AnyKernel3 (...)
```

确认驱动符号链接已就绪：

```bash
ls -l drivers/kernelsu
# 预期输出：drivers/kernelsu -> ../KernelSU/kernel
```

### 2. 脚本准备

拉取代码后，为构建脚本添加可执行权限：

```bash
chmod +x build.sh
```

之后可以直接使用 `./build.sh <command>` 执行脚本，无需每次输入 `bash`。

### 3. 模块更新

项目包含 `KernelSU` 和 `AnyKernel3` 两个 git 子模块。使用以下命令将子模块更新至上游最新版本：

```bash
./build.sh update-submodules
```

该命令会：

1. 拉取所有子模块的上游最新代码
2. 将子模块指针切换到上游 `origin/HEAD`
3. 暂存更新后的子模块指针（`git add`）
4. 显示更新后的子模块状态

更新后需要手动提交以保存变更：

```bash
git commit -m "update submodules to latest upstream"
```

### 4. 一条龙编译（quick 命令）

使用 `quick` 命令实现全自动编译流程，一条命令完成从更新到打包的全部步骤：

```bash
./build.sh quick
```

该命令依次执行以下步骤：

1. **更新子模块**：拉取 `KernelSU` 和 `AnyKernel3` 的最新上游代码
2. **检查依赖**：验证编译工具链是否完整
3. **生成默认配置**：合并所有 defconfig 片段，生成 `.config` 文件
4. **弹出 menuconfig**：打开交互式配置界面，允许用户自定义内核配置
   - 如需自定义：修改配置后保存并退出
   - 如使用默认值：直接退出即可（不修改 = 使用默认配置）
5. **编译内核**：编译 Image + modules + dtbs（含错误捕获）
6. **安装模块**：将内核模块安装到 `out/modules_install/`
7. **复制产物**：将构建产物复制到 `out/dist/`
8. **打包刷机包**：生成 AnyKernel3 刷机 zip

生成的刷机包位于：

```text
out/dist/miro-kernel-ultra-YYYYMMDD-HHMM.zip
```

### 5. 独立编译（build.sh）

#### 依赖安装

**一键安装**（推荐）：使用脚本内置命令自动检测包管理器并安装所有依赖：

```bash
./build.sh install-deps
```

该命令支持 `apt`（Debian/Ubuntu）、`dnf`（Fedora/RHEL）、`pacman`（Arch Linux）和 `zypper`（openSUSE）。

**手动安装**：也可以手动执行以下命令安装依赖：

```bash
# Debian/Ubuntu
apt install clang lld llvm flex bison bc cpio device-tree-compiler \
    libelf-dev libssl-dev libncurses-dev zip git ccache

# Fedora/RHEL
dnf install clang lld llvm flex bison bc cpio dtc \
    elfutils-libelf-devel openssl-devel ncurses-devel zip git ccache

# Arch Linux
pacman -S clang lld llvm flex bison bc cpio dtc \
    libelf openssl ncurses zip git ccache
```

完整依赖列表：

| 依赖 | 说明 |
| --- | --- |
| `clang` `lld` `llvm` | LLVM 编译工具链（clang >= 15） |
| `flex` `bison` | 词法/语法分析器生成工具 |
| `bc` | 数学计算工具（内核 Makefile 使用） |
| `cpio` | initramfs 打包工具 |
| `dtc` (device-tree-compiler) | 设备树编译器 |
| `libelf-dev` (libelf-devel) | ELF 文件处理库 |
| `libssl-dev` (openssl-devel) | OpenSSL 开发库 |
| `libncurses-dev` (ncurses-devel) | menuconfig 界面依赖 |
| `zip` | AnyKernel3 刷机包打包 |
| `git` | 子模块管理 |
| `ccache`（可选） | 编译缓存，加速重复编译 |

#### 命令一览

```bash
./build.sh help              # 查看帮助
./build.sh install-deps      # 一键安装编译依赖
./build.sh quick             # 一条龙：更新子模块 + menuconfig + 编译 + 打包
./build.sh defconfig         # 仅生成 .config
./build.sh menuconfig        # 打开 menuconfig 界面编辑 .config
./build.sh kernel            # 编译内核（Image + modules + dtbs）
./build.sh fast              # 快速编译（仅 Image + dtbs，不含 modules）
./build.sh all               # 编译 + 安装模块 + 输出到 out/dist
./build.sh zip               # 编译 + 打包 AnyKernel3 刷机包
./build.sh package           # 从已有 out/dist 重新打包 AnyKernel3
./build.sh modules           # 安装内核模块（在 kernel 之后执行）
./build.sh toolchain         # 显示当前工具链配置
./build.sh clean             # 清理构建产物（保留 .config）
./build.sh deepclean         # 深度清理（out/ + AnyKernel3 产物 + 子模块产物）
./build.sh mrproper          # 完全清理（make mrproper + deepclean）
./build.sh update-submodules # 更新 KernelSU 和 AnyKernel3 到最新上游
```

#### 完整编译并打包刷机包

```bash
./build.sh zip
```

该命令会依次执行：
1. 检查编译依赖
2. 初始化 KernelSU 子模块
3. 合并 defconfig 片段（`gki_defconfig` + `sun_perf` + `miro_perf` + `droidspaces`）
4. 应用后置配置（启用 `PINCTRL_MSM` 等）
5. 编译内核（Image + modules + dtbs）
6. 安装内核模块
7. 复制构建产物到 `out/dist/`
8. 使用 AnyKernel3 子模块，注入内核产物并生成刷机 zip

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
| `out/build.log` | 编译日志（含错误捕获） |

#### 配置说明

`build.sh` 使用 LLVM 工具链（`clang` + `ld.lld`）进行交叉编译，合并以下
defconfig 片段：

1. `arch/arm64/configs/gki_defconfig` — GKI 基础配置
2. `arch/arm64/configs/vendor/sun_perf.config` — Qualcomm Sun 平台配置
3. `arch/arm64/configs/vendor/miro_perf.config` — 小米 miro 设备配置
4. `arch/arm64/configs/droidspaces.fragment` — DroidSpaces 配置

合并后通过 `olddefconfig` 自动解析新增的 Kconfig 选项，避免交互式提示。

如需手动修改内核配置，可使用 `menuconfig` 命令打开交互式配置界面：

```bash
./build.sh menuconfig
```

在 menuconfig 中修改配置后保存退出即可。若 `.config` 不存在，脚本会先自动生成默认配置。

#### 错误处理

`build.sh` 内置了编译错误捕获和显示功能，无需在大量输出中手动查找错误：

- **实时日志记录**：编译输出同时显示在终端并保存到 `out/build.log`
- **自动错误提取**：编译失败时，脚本自动从日志中提取错误信息
- **醒目错误展示**：以红色高亮显示错误汇总，包含：
  - 错误编号和日志行号
  - 错误前后各 2 行上下文代码
  - 最多显示前 10 个错误（避免输出过长）
- **完整日志路径**：提示完整日志文件位置，方便深入排查

支持的错误模式包括：`error:`、`fatal error:`、`Error N`、`undefined reference`、`No rule to make target`、`recipe for target failed` 等。

编译失败时的输出示例：

```text
[build.sh] Building kernel (Image + modules + dtbs)...
... (编译输出) ...

═══════════════════════════════════════════════════════════════════
  编译错误汇总
═══════════════════════════════════════════════════════════════════

━━━ 错误 #1 (日志行 1234) ━━━
  drivers/usb/dwc3/dwc3-msm-core.c: In function 'dwc3_msm_probe':
  drivers/usb/dwc3/dwc3-msm-core.c:567:9: error: implicit declaration of function 'mca_sysfs_init'
    567 |         mca_sysfs_init(pdev);
        |         ^~~~~~~~~~~~~

  完整编译日志: out/build.log
```

#### 环境变量

`build.sh` 支持以下环境变量进行自定义配置：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CLANG_PATH` | 系统 PATH | 自定义 clang 工具链目录（如 `/opt/clang/bin`） |
| `GCC_PATH` | 系统 PATH | 自定义 GCC 交叉编译器目录（`aarch64-linux-gnu-gcc`） |
| `JOBS` | `nproc --all` | 编译并行任务数 |
| `KERNEL_NAME` | `miro-kernel-ultra` | AnyKernel3 安装包中显示的内核名称 |
| `OUT_DIR` | `out` | 构建输出目录 |
| `USE_CCACHE` | 自动检测 | 设为 `1` 启用 ccache，`0` 禁用 |
| `CCACHE_DIR` | `~/.ccache` | ccache 缓存目录 |
| `FAST_BUILD` | 未设置 | 设为 `1` 跳过模块编译，仅编译 Image + dtbs |

使用示例：

```bash
# 使用自定义工具链编译
CLANG_PATH=/opt/clang/bin GCC_PATH=/opt/gcc/bin ./build.sh zip

# 限制并行任务数
JOBS=4 ./build.sh all

# 禁用 ccache
USE_CCACHE=0 ./build.sh all

# 快速编译（不含模块）
FAST_BUILD=1 ./build.sh zip

# 将输出目录放在 tmpfs 加速编译
OUT_DIR=/tmp/kernel-build ./build.sh zip
```

#### AnyKernel3 配置

`build.sh` 支持通过环境变量自定义 AnyKernel3 刷机包的配置项（对应 `anykernel.sh` 中的参数）：

| 环境变量 | 默认值 | 对应参数 | 说明 |
| --- | --- | --- | --- |
| `AK3_DEVICE_CHECK` | `0` | `do.devicecheck=` | 设为 `1` 开启设备名称检测，`0` 关闭 |
| `AK3_DEVICE_NAME1` | `miro` | `device.name1=` | 设备名称 1（开发代号或设备名称） |
| `AK3_DEVICE_NAME2` | 空 | `device.name2=` | 设备名称 2 |
| `AK3_DEVICE_NAME3` | 空 | `device.name3=` | 设备名称 3 |
| `AK3_DEVICE_NAME4` | 空 | `device.name4=` | 设备名称 4 |
| `AK3_DEVICE_NAME5` | 空 | `device.name5=` | 设备名称 5 |
| `AK3_BLOCK` | `auto` | `BLOCK=` | 刷写的分区（如 `boot`、`init_boot`、`auto`） |
| `AK3_SLOT_DEVICE` | `1` | `IS_SLOT_DEVICE=` | A/B 插槽设备：`1` 开启、`0` 关闭、`auto` 自动判断 |
| `AK3_PATCH_VBMETA` | `auto` | `PATCH_VBMETA_FLAG=` | 修补 vbmeta 关闭 AVB 验证：`1` 开启、`0` 关闭、`auto` 自动判断 |

使用示例：

```bash
# 开启设备检测并设置设备名称
AK3_DEVICE_CHECK=1 AK3_DEVICE_NAME1=miro AK3_DEVICE_NAME2=mipro ./build.sh zip

# 刷写到 init_boot 分区（GKI 设备常用）
AK3_BLOCK=init_boot ./build.sh zip

# 关闭 A/B 插槽检测
AK3_SLOT_DEVICE=0 ./build.sh zip

# 强制修补 vbmeta 关闭 AVB 验证
AK3_PATCH_VBMETA=1 ./build.sh zip

# 自定义内核名称
KERNEL_NAME="my-custom-kernel" ./build.sh zip
```

#### 加速编译技巧

1. **安装 ccache**（推荐）：首次编译后，后续编译速度提升 3-5 倍
   ```bash
   apt install ccache && ccache -M 50G
   ```

2. **使用 `fast` 命令**：跳过模块编译，减少约 40% 编译时间
   ```bash
   ./build.sh fast
   ```

3. **将输出放在 tmpfs**（内存盘）上：
   ```bash
   mkdir -p /tmp/build && OUT_DIR=/tmp/build ./build.sh all
   ```

4. **调整并行任务数**：
   ```bash
   JOBS=$(nproc) ./build.sh all
   ```

#### 常见问题

**Q: 编译报错 "The source tree is not clean"**

A: 脚本会自动清理源码树中的 `.config` 等残留文件。如果仍然报错，执行 `./build.sh deepclean` 后重试。

**Q: 编译报错 "Missing tools: ..."**

A: 安装缺失的依赖：`apt install clang lld llvm flex bison bc cpio device-tree-compiler libelf-dev libssl-dev libncurses-dev zip`

**Q: 编译报错找不到 `drivers/kernelsu` 符号链接**

A: 未初始化子模块。执行 `git submodule update --init --recursive` 或 `./build.sh update-submodules`。

**Q: menuconfig 界面无法正常显示**

A: 确保安装了 `libncurses-dev`，且终端窗口足够大（至少 19 行 x 80 列）。

**Q: 编译速度太慢**

A: 参考上方的「加速编译技巧」部分，推荐安装 ccache。

**Q: 如何只修改配置不重新编译？**

A: 使用 `./build.sh menuconfig` 修改配置，然后使用 `./build.sh kernel` 仅编译内核。

**Q: `mrproper` 会删除 AnyKernel3 子模块吗？**

A: 不会。`mrproper` 只清理 AnyKernel3 目录中的构建产物（Image、dtb、modules），保留子模块本身。`deepclean` 同理。

### 6. Kleaf/Bazel 构建

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

### 7. 传统 build.config 流程

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

## 鸣谢

本项目基于以下开源项目，感谢它们的贡献：

- [ReSukiSU](https://github.com/ReSukiSU/ReSukiSU) — 内核级 root 管理和模块扩展框架
- [SUSFS (susfs4ksu)](https://gitlab.com/simonpunk/susfs4ksu) — 内核级 root 隐藏和文件系统隔离补丁
- [DroidSpaces](https://github.com/ravindu644/Droidspaces-OSS) — Android/Linux 容器化用户空间支持
- [AnyKernel3](https://github.com/osm0sis/AnyKernel3) — Android 内核刷机包打包工具
- [Linux Kernel](https://www.kernel.org/) — Android Common Kernel `android15-6.6`

> 如需其他功能或有任何问题，请 [提交 Issue](https://github.com/ZHYxulei/miro-kernel-ultra/issues)。

## 开源许可证

本项目基于 Linux 内核，遵循 [`GPL-2.0 WITH Linux-syscall-note`](COPYING)
开源许可证。项目中集成的第三方组件各自保持其原始许可证：

- **Linux 内核**：GPL-2.0 WITH Linux-syscall-note
- **ReSukiSU/KernelSU**：GPL-2.0（子模块 `KernelSU/`）
- **SUSFS (susfs4ksu)**：GPL-2.0（内核补丁部分）

使用、修改和分发本项目代码须遵守上述许可证条款。
