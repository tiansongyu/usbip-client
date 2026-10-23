# 实验性 Linux USBFS 服务端

本指南供已获得两端授权的测试环境使用。`physical-server` 通过普通用户进程中的 nusb/usbfs 转发管理员指定设备，客户端仍使用 Linux VHCI。它与 Native `usbip_host`、固定虚拟 HID/CDC profile 是三条不同的设备来源；同一设备不能同时使用 Native 和 USBFS。实施设计与未完成验收见[物理 adapter 设计](research/rust-physical-backend-design.md)和[验收记录](acceptance.md)。

当前是实验性开发预览：干净37bc6b4在QEMU HID/FTDI经过真实USBFS的正常/热拔场景通过，真实硬件仍未验收。不能因能够读取描述符或通过编译，就声明某类真实设备已支持。ISO 明确不支持；存储、Hub、不明/不完整接口及已被 usbfs/usbip-host 占用的设备会被拒绝。真实 Composite、备用接口和配置切换需逐设备验证。

## 构建与授权

Linux 构建需要 Rust ≥1.97，使用锁定的可选 nusb 0.2.8 本地源码。服务端不需要加载 `usbip_host`，也不启动 `usbipd`。客户端需要匹配内核的 `vhci_hcd`、原生 usbip 工具及受限 helper。

```bash
cargo build --workspace --release --all-features --locked
target/release/rustusb physical-server --help
target/release/rustusb devices --json
```

两端管理员按 [Native 安装步骤](../README.md#native-共享流程)保护 helper 二进制、配置和 Unix socket。服务端 helper 必须以 `physical-usb` feature 构建；上面的 all-features 命令包含它。仅将指定测试设备的精确 `usb-…` 指纹放进 root 管理的 `physical_device_ids`，不要将 Bus ID 当作指纹。旧策略省略此字段时等于空列表。

```json
{
  "allowed_uids": [1000],
  "export_device_ids": [],
  "physical_device_ids": ["usb-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
  "peers": [],
  "trusted_network": true
}
```

示例指纹必须替换为管理员核对的当前测试设备指纹。`physical_device_ids` 与 Native 的 `export_device_ids` 不得重叠。`trusted_network` 仅记录管理员声明，既不创建 VPN，也不验证或加密数据通道。非 loopback 监听还需要服务端命令显式确认受管测试网络并指定精确对端 IP。

helper 校验调用 UID、allowlist、安全状态和设备代际，然后打开固定 usbfs 字符节点，经受保护 socket 传递一个 `OwnedFd`。普通服务不接受任意设备路径，也不要求将 USB 节点改为普通用户所有。FD 是整台设备的写能力，不是逐 USB 请求沙箱；策略变化不能撤销已交付 FD，更不能替代远端身份认证。

## 启动与连接

普通用户启动服务，`--device-id` 可重复，范围为 1–16 个不同设备：

```bash
rustusb --socket /run/rustusb/control.sock physical-server \
  --device-id usb-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --listen 192.0.2.1:3240 \
  --allow-insecure-test-network --allow-peer 192.0.2.2 \
  --control-stdin
```

地址为隔离测试网示例，须替换为实际受管地址。默认监听 `127.0.0.1:3240`。服务本身没有认证或 TLS，部署前需由管理员提供真实受控网络和访问限制；控制请求与 IMPORT 后 URB 长连接均需受保护。

客户端由普通用户运行现有 `discover`、`attach`、`sessions` 和 `detach`，按 [CLI 帮助与 Native 流程](../README.md#native-共享流程)配置客户端 helper 的远端授权。设备只有在 IMPORT 时才独占 claim 全部接口；忙、清理中、隔离或旧代际不会作为可用设备发布。

切换配置后，服务会在新的接口 claim 前重新检查当前配置，而不沿用旧配置的安全快照。存储驱动、块设备后代、外部占用或不完整观察会阻止继续绑定；恢复时发现这些状态也会保持隔离，不强制切回配置或解绑现有驱动。该检查不能撤销内核已经执行的配置变更。多接口驱动已恢复的原绑定会逐接口重新核对，不能单凭 CONNECT 返回 EBUSY 当作成功。

## 观察与退出

启用 `--control-stdin` 后，本地 stdin 接受有界 JSONL，stdout 输出机器事件；数据不注入 USB payload：

```json
{"id":"check-1","op":"snapshot"}
{"id":"stop-1","op":"shutdown"}
```

只有 `snapshot` 和 `shutdown` 适用于物理服务。虚拟服务的 `hid_key` / `cdc_emit` 会被拒绝。`ready`、`snapshot`、`stopped` 事件包含 `snapshot`，可观察每设备状态、pending/queued/inflight、真实回收计数、配置、速度、EP0 大小、原/当前驱动和全服务缓冲预算；不输出原始数据或序列号。它是最后一次观察的缓存，查询本身不重新读取 sysfs；累计请求终态也包含逻辑拒绝，只有 `usbfs_reaped` 专门统计真实 raw 回收。

网络 EOF 或网络写失败清理对应会话，listener 继续接受连接；本地stdin EOF、`shutdown`、Ctrl-C或Unix SIGTERM结束服务并清理所有会话。取消请求后必须等待真实 terminal，不能将 UNLINK 当成已回滚 OUT 数据。最后一个物理 URB 回收、接口释放和原驱动独立核对完成前，设备保持独占。claim/清理监督截止后报告隔离，不能冒充可用；单次控制传输超时不等于设备必然隔离。应保留诊断，由管理员协调当前内核状态。

正常结束且清理确认后退出码为0，输出失败或未确认清理返回非零。Ctrl-C或Unix SIGTERM取消已阻塞的stdout事件时仍执行清理，但可能没有最终 `stopped` 事件，调用方应同时检查进程退出状态。

拔出后旧 slot 不会自动授权同端口的新设备。重新连接需要管理员核对新设备，再显式重新启动服务获取新代际的 FD；无序列号设备的候选指纹仍不构成可靠硬件身份认证。

Unix信号监听在helper通信前注册，等待异步helper响应时可取消；这不终止已运行的blocking内核初始化。合作停止须等待原有清理，真实验证见[报告](research/server-signal-shutdown.md)。SIGKILL/崩溃后的自动驱动恢复仍未实现，不能用进程退出代替设备恢复核验。新的持久授权保护与管理员协调见下节；运行证据以测试报告为准。

在 [SIGKILL/首次重启测试](research/physical-crash-recovery.md)中，旧进程退出后原驱动没有恢复，新进程却以当时的空驱动作为 original 并报告 `cleanup_verified=true`。该字段只验证当前进程的基线。强制退出后不能靠重启后的 ready/stopped 判断旧设备已恢复；应保留原状态和诊断，由管理员核对同代际配置、alternate 与驱动。


## 持久授权记录与管理员协调

helper 与物理客户端需一起升级到 journal schema 1；旧版 OpenPhysical 请求在授权前拒绝。每次授权在 FD 交付前保存原配置、完整接口/alternate/driver、代际和 OS owner，不保存序列号或 USB 数据。默认目录 `/var/lib/rustusb/physical` 由 helper 首次创建，root0700，固定 journal/lock 文件 root0600；root helper 可用 `--physical-state-dir` 选另一受保护绝对路径。前端不能选择状态路径；同一设备的所有 helper 必须共用一个状态目录，不得用另一个目录绕过未决记录。

正常服务结束会先清理 USB、等待自己的后台任务、释放 registry，再由 helper 独立核验并清除记录。交付不确定、初始化中断、清理或记录写入失败会保留未决状态；下一次同指纹启动被拒绝，包括同端口的新代际。已交付 FD 不可由该记录撤销，获授权服务仍是受信任进程。

管理员首先停止并回收旧服务进程，读取原始记录：

```bash
sudo rustusb --json physical-pending
```

输出是 `schema_version`、`records` 和 `next_after`；若 `next_after` 非空，用 `--after <值>` 继续。先依据记录与现场状态，由管理员恢复**同代际**设备的原配置、全部 alternate 和驱动。确认之后执行：

```bash
sudo rustusb --json physical-reconcile <lease-id>
```

该命令只读设备状态并退休记录，不执行 bind、配置切换或强制清理；状态仍不匹配则失败并保留记录。旧代际已拔出或跨启动消失时，管理员可加 `--acknowledge-gone` 确认结束旧记录，命令不操作替代设备；随后重新启动仍需当前策略和新的完整扫描/FD receipt。这不是每次重插都自动要求修改 allowlist 的机制。

不要删除 journal、lock 或整个目录来解除拒绝。已有目录缺 journal、损坏、不安全权限、重复 helper 或提交不确定都会拒绝授权；删除整个目录会破坏不可恢复的原始历史，程序无法区分管理员删除与首次安装。本轮不声称断电耐久已实测，也没有自动重新绑定驱动。实现与验收状态见[设计](research/physical-crash-recovery-design.md)。
