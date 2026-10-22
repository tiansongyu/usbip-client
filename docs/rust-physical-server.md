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

网络 EOF 或网络写失败清理对应会话，listener 继续接受连接；本地 stdin EOF、`shutdown` 或 Ctrl-C 结束服务并清理所有会话。取消请求后必须等待真实 terminal，不能将 UNLINK 当成已回滚 OUT 数据。最后一个物理 URB 回收、接口释放和原驱动独立核对完成前，设备保持独占。claim/清理监督截止后报告隔离，不能冒充可用；单次控制传输超时不等于设备必然隔离。应保留诊断，由管理员协调当前内核状态。

正常结束且清理确认后退出码为0，输出失败或未确认清理返回非零。Ctrl-C 取消已阻塞的 stdout 事件时仍执行清理，但可能没有最终 `stopped` 事件，调用方应同时检查进程退出状态。

拔出后旧 slot 不会自动授权同端口的新设备。重新连接需要管理员核对新设备，再显式重新启动服务获取新代际的 FD；无序列号设备的候选指纹仍不构成可靠硬件身份认证。
