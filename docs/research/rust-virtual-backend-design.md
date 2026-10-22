# 可选 Rust 虚拟服务端实施设计

2026-10-22，基线 `85e87fa`。状态：实施中；不等于完整 Phase2 已验收。依据项目核心开发、技术设计与技术文档技能，沿用现有研究目录，不建立无关组件技能或空 crate。

## 范围与复用决策

用户选择 QEMU 仿真，继续软件与内核互操作验证；真实设备 G1 仍未完成。此子步骤实现独立用户态虚拟 HID Keyboard、标准双接口 CDC ACM，通过 USB/IP 连接已有 Linux VHCI 客户端。物理 rusb/nusb、任意 Composite/alternate、ISO、TLS 和 UI 属于后续原需求，不能由两个虚拟 profile 推导为已支持。

固定 [jiegec/usbip 61938f8](https://github.com/jiegec/usbip/tree/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0) 的公开同步 handler 无法表达 pending/cancel，核心 dispatch/registry 又是私有；wrapper 无法修复已验证 R01/R04/R05/R08。直接依赖还会无条件引入 rusb/nusb/libusb。维护完整 fork 需重写 parser、session 与 handler 契约，成本并非薄适配。采用自有有界协议/会话引擎，复用现有 BusId、RemoteDevice、DeviceDiscovery/ExportBackend 与 Native VHCI；仅按 MIT 许可复用已审查 HID 描述符及报告格式，逐项注明来源、保留完整版权。Linux 只参考公开协议，不复制 GPL 源码。

新 `rustusb-rust-backend` crate 有实际虚拟设备和服务端职责；CLI 依赖通过 `rust-backend` feature 显式开启，默认构建仍为 Native。虚拟服务不需要 root、sysfs 写操作、usbip_host、rusb 或 libusb。虚拟设备 id/topology 明确带 virtual 前缀，不伪装物理指纹。

## API 与所有权

`VirtualBackend` 为可克隆共享状态，实现只读发现、虚拟注册/取消注册和 typed HID/CDC 控制。只支持固定两个 profile：bus1/dev1 `1-1` HID，bus1/dev2 `1-2` CDC，Linux Full-Speed 枚举2、EP0 64B。VID/PID `1234:0001`、`1234:0002` 仅用于此隔离测试，未声称注册或商用分配。

`ServerHandle::bind(ServerConfig, VirtualBackend)` 返回实际监听地址。默认 `127.0.0.1:3240`；非 loopback 要显式受管测试网络声明及精确 peer IP allowlist。IP allowlist 不是认证或加密。`shutdown(self)` 终止并等待所有连接；Drop 同样终止子任务。每连接最多成功 IMPORT 一次，设备租约 RAII 持有；EOF、写失败、取消、shutdown 均释放该租约及 pending URB，清空会话数据，不影响别人的设备。

单连接 actor 排序 SUBMIT、UNLINK 与可用输入，单 writer 发回复。独立有界 reader 保存半包进度，不在 `select!` 中取消不安全的 `read_exact`；锁仅用于短状态变更，不跨网络 await。pending IN 允许其他 Control/OUT/UNLINK 继续执行；无输入时不返回空成功造成忙轮询。成功取消返回 RET_UNLINK `-ECONNRESET`，不再返回原 RET_SUBMIT；已完成或不存在目标返回0。

传输契约保存 seqnum、devid、方向、端点、flags、长度、SETUP 与 OUT 数据；completion 明确负 Linux errno、实际长度、IN payload。IN 数据统一受 URB length 约束，控制另受 wLength 约束；OUT 实际长度反映已接受字节，无响应 payload。不支持的请求 stall/unsupported，不 panic 或假成功。物理错误 R06 与阻塞 USB R07 仍待物理 adapter 验证，不能标为修复。

## 资源和协议边界

- 固定2设备、16连接、每 imported session 最多64 pending URB、单 transfer 64KiB；连接队列也有固定上限，先验证后分配，不接受负数长度/ISO allocation。
- HID 最多64个完整8B报告；CDC 单方向最多64KiB，溢出明确失败，不静默丢字节。
- 握手、半包、写回复各5秒；已 IMPORT 的完整空闲连接不因无键盘输入超时。开始收到首字节才计半包截止。
- management/data phase 严格区分；版本、opcode、busid、devid、direction、endpoint、duplicate active seqnum、ISO count 均校验。USB/IP header 大端；USB descriptor/SETUP 依规范小端。
- descriptor interface/index 校验，不使用输入直接索引数组。CDC 两接口含 Header、Call Management、ACM、Union 和通知端点，真实 GET/SET_LINE_CODING、DTR/RTS、break；HID down/up 各8B，支持必要 idle/protocol/report/LED 控制。
- 日志只记非敏感 ID、长度、结果，不记录原始 USB payload、串口内容或序列号。控制 stdin JSONL 单行4096B，显式 op/id、响应 ack；hex 是主动本地测试输入，不进入普通日志。
- 虚拟 stdout 专用 std 线程只持有编码后的有界事件，不持租约；队列2、事件64KiB、写确认5秒。整个控制 future 与 Ctrl-C 竞争，任何输出错误后先 await server shutdown。已取消的阻塞输出不再次尝试 stopped；虚拟模式错误也不回落通用同步 stdout JSON。stderr 的同步 tracing sink 属既有日志边界，不能声称任意日志 sink 下退出均有界。

## 验证与裁决

先执行 wire TCP 回归：未 IMPORT、重复 IMPORT、busy、超长/负长度、截断、未知版本、非法 interface255、1B descriptor、pending、UNLINK、失败后再导入及 shutdown 清理。独立 probe 不复用服务端 codec，核对准确字节和不应出现的旧 completion。

QEMU 使用两个隔离来宾与私有 socketpair：server UID1000 只运行可选 Rust 服务，不加载 USB/IP host 模块；client 普通 CLI 经 root helper 操作 VHCI。真实 Linux evdev 按下/释放、cdc_acm ttyACM 双向带序号字节、两设备并发和两次 attach/detach，核对末尾 lease/pending0。保留开发失败及最终 clean commit/source/binary/script 指纹。成功也只证明这些 profile 和具体内核路径；不证明物理、长时间性能或认证。

默认和 all-features 的 fmt/clippy/test/release/rustdoc 均须通过，CI 保留四个原生 OS/架构 job。网络输入 fuzz 入口与上游 R01–R12 对应证据逐项维护，不写笼统“全部修复”。

### 固定上游缺陷的逐项对照

上游本身未修改。以下是自有虚拟路径的控制与已执行软件回归，最终 QEMU 记录另见[互操作报告](userspace-qemu.md)，不能泛化为物理或上游已修复。

| 项 | 本实现与证据边界 |
| --- | --- |
| R01 无界资源 | 长度/flags/ISO预分配拒绝、16连接/64pending、半包5秒、实际TCP边界及生产parser ASan fuzz；不是洪泛压测或物理预算证明 |
| R02 未IMPORT崩溃 | management/data严格阶段；实际TCP与独立probe拒绝SUBMIT-before-IMPORT |
| R03 interface越界 | recipient/interface/endpoint有界校验，255返回stall，不索引panic；软件与独立probe |
| R04 失败泄漏 | RAII lease、栈内reader、JoinSet abort+join；写失败/Drop/SIGINT/输出阻塞回归、QEMU末状态 |
| R05 重复IMPORT | imported阶段不再接受management；连接关闭后另两设备可用，独立probe |
| R06 物理错误吞掉 | 虚拟completion负errno/实际长度；物理adapter尚未实现，此项仍未完成 |
| R07 阻塞USB worker | 虚拟路径短锁无物理调用；物理USB执行/取消仍未实现，不将stdout专用线程修复当作物理验证 |
| R08 超长应答 | Control受wLength和URB双上界、CDC按URB取FIFO；1B/剩余15B、SHORT_NOT_OK回归 |
| R09 网络panic | 不支持请求typed stall/unsupported，核心禁止unsafe/unwrap/expect/panic；畸形probe和parser fuzz，不等于穷尽所有输入 |
| R10 锁反转 | 无跨网络await持设备锁，单actor保序、共享短临界；并发busy/写失败/fragmented OUT/seqwrap回归 |
| R11 无认证 | 默认loopback、非loopback显式声明和精确IP筛选；仍无认证/TLS，此项保持Phase3未完成 |
| R12 payload日志 | 虚拟trace秘密marker与无输入回显回归；QEMU只记录计数/摘要；未来物理/隧道日志需另测 |

## 官方依据

- [Linux USB/IP 公开线协议](https://docs.kernel.org/usb/usbip_protocol.html)：management/data 阶段、48B URB header、实际长度和 UNLINK 语义。
- [固定上游审计](rust-backend-audit.md)：具体缺陷与实际复现，不以 README 作为能力证明。
- [Tokio 1.54.1 select cancellation safety](https://docs.rs/tokio/1.54.1/tokio/macro.select.html)：`read_exact` 不可随意取消；本设计用单 reader 持有进度。

实现签名以新 crate 公共 API 为准；后续行为变化先更新本设计和工作日志。
