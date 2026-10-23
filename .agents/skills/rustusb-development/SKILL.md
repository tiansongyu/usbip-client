---
name: rustusb-development
description: Develop or review RustUSB USB/IP discovery, protocol, native backend, privileged helper, sessions, and related tests. Read before changing USB/IP core code; excludes unrelated UI work.
---

# RustUSB 核心开发

先读仓库根 `AGENTS.md`、`docs/architecture.md` 和相关 `docs/research/` 审计；范围以 `docs/requirements.md` 为准，进度见 `docs/work-log.md`。技能目录相对于仓库根，不依赖用户全局安装。其他 Agent 必须由 AGENTS.md 显式进入本文件。

## 数据通路与上游边界

- Native：Linux `usbip_host` → 原生 USB/IP TCP → Linux `vhci_hcd`。复用外部 usbip 工具，不复制 GPL 用户态实现进入 MIT Rust 源码。`tools/usb/usbip` 不是内核。
- Rust 后端：独立的用户态设备实现 → USB/IP → VHCI。jiegec 的 rusb/nusb 路径与 usbip_host 不叠加控制同一设备。完成源码审查和每种传输测试后才接入。
- Linux VHCI 不是 Windows 驱动；Ubuntu 22.04/24.04、x86_64/aarch64 是目标，实际测试系统单独记录。发现能力不等于转发验证。
- 按固定提交引用上游实现和许可证；新增依赖更新 `docs/third-party.md` 和 Cargo.lock。

## 特权和安全

- 普通 CLI/TUI/GUI 共用应用服务；特权仅通过受控 Unix socket helper。不得默认整应用 root，不调用 shell，不接受任意程序或路径执行。
- Bus ID、主机名、端口、配置、协议计数均使用有界类型和验证；网络与 IPC 读取设长度、并发、超时上限。
- helper 校验调用者 OS 身份、root 管理的 allowlist、设备当前指纹和实时状态；限制固定命令。sysfs 热插拔存在 TOCTOU，失败须重新读取并报告不确定状态，不能虚构回滚成功。
- 不默认开放公网 3240；管理连接认证不意味着 USB 数据连接加密。必须分别验证 DEVLIST/IMPORT 与 IMPORT 后的 URB 长连接经安全通道。
- 存储导出必须检查所有 interface 类别、块设备后代、挂载和使用者；无法证明安全就拒绝。不得强制卸载、自动写入恢复或操作生产盘。硬件测试只用管理员指定的测试设备。
- 不记录密钥、原始 USB 数据、序列号或存储内容。可读和 JSON 日志写 stderr；stdout 留给机器输出。

## Rust 接口和异步执行

- 公共类型表达所有权、Send/Sync、生命周期、错误、超时和取消。避免无用途空 crate；平台细节留在 linux/native 层。
- 使用类型化 Result，不能吞 USB I/O 错误或把未知状态当空列表；核心代码禁止 unwrap/expect/panic/unimplemented 和 unsafe。
- 阻塞 sysfs/进程/USB 调用不能长期占 Tokio worker。`spawn_blocking` 取消不终止底层调用；进程超时需 kill + reap，并重新协调设备状态。
- 每台物理设备独占；串行化冲突变更。清理幂等，从内核/sysfs 恢复现实状态，不仅相信内存记录。存储断线默认人工确认。
- 应用job/session/managed IMPORT修改前读`docs/research/application-service-design.md`，共享前台owner另读`docs/research/shared-owner-design.md`。Foreground无WAL/correlation，delivery ACK不等于持久准入；保留两模式EOF策略、原JSON/信号/背压。持久admission后才claim，停止只发合作请求、真实owner总await；softdeadline不是terminal。server Connected只证明IMPORT flush，client须VHCI receipt。Root proof全核kind/correlation/key/UID/resource，不能用新进程资源0或PID死亡恢复旧session；保留operation/cleanup独立结果，raw-v1不编造heartbeat/RTT。

## 可选数据连接v2

修改同数据链探测/RTT、framed stream、健康历史或短management收尾前，读`docs/research/data-link-health-design.md`与`docs/research/data-link-health-validation.md`。626软件/十五QEMU锁各自实际来源，硬件及完整Phase3未完成。

- TLS schema2/ALPN2显式启用且不downgrade；schema1/raw1不伪造probe/RTT。单TLS连接只一data stream，不用Catalog活性替代设备连接。
- driver独立于应用读取、持久ACK和stdout；metadata监视器不持设备/stream/permit。流控只按实际消费还credit，真TLS完整DATA/FIN flush不能用入队或PONG替代；全部h2 API/poll/map/drop在quiet边界内。
- Catalog/完整flush的拒绝回复保留借用IO，经双方FIN/有界EOF后自然join；错误/停止保留outer owner并stop+join，成功IMPORT不提前FIN。e152的真实Catalog FAIL及相同函数修后PASS是回归契约，不可改成sleep/retry。
- 新health是独立只读RPC，旧Session/Metrics/IPC1不改；私有disk2严格字段/迁移/容量/epoch历史，探测不逐次写WAL。operation、actual cleanup与recording保持独立；网络timeout不等于设备释放或driver恢复。

## 验证与文档

- 单元测试覆盖字节序、描述符边界、ID、状态机、配置、认证、错误、超时与取消；网络解析加畸形包和大小边界测试及 fuzz 入口。
- 假 sysfs、假命令和本地 TCP 测试可验证分支，不能证明真实驱动枚举。互操作三方向、QEMU HID/CDC、真实设备和故障矩阵见 `docs/testing.md`。
- 每阶段执行根 AGENTS.md 的四项 Cargo 检查。新增公共接口执行 rustdoc 检查。硬件测试须显式选择设备和两端授权，不扫描后自动解绑。
- 文档区分设计、源码事实、软件测试、硬件实测；修改行为同步 README、架构、兼容性和工作日志。不得编造性能或设备支持。
