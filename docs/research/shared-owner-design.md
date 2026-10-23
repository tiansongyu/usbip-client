# 前台与托管入口的共享任务 owner

2026-10-24，起点 clean `9314aa0`。上一 goal turn 为实际进展：应用服务、495 软件、Native agent 双栈及六前台回归、英文入口与发布。原完整目标保持；本项完成共同应用核心的前台提取，不以此代替未完成的 heartbeat/RTT、轮转、Rust 崩溃协调、硬件或后续阶段。

## 源码约束与选择

已读当前 CLI 与 service 实际 owner、helper 客户端、Tokio 1.54.1 的半关闭语义及既有验收脚本。两处实现不能简单替换：前台 helper 请求无 correlation、保持旧 disk1/既有 sticky 格式；托管任务的每次准入必须持久确认并使用 tracked API。stdout 的 delivery ACK 不是 durable admission。

采用一套 service owner pipeline，内部明确 `Foreground` / `Managed(JobContext)` 模式。准备、监听、连接、relay、实际 shutdown/Finish 和错误归并在库中共享；CLI 只加载可信配置、装信号、读有界 stdin、编码/写 stdout。禁止起 CLI 子进程、解析其输出、构造内存 actor 假 ACK 或让输出线程持 lease。

- Managed 保持现 `run_job(LoadedJob, JobContext)`，fixed profile、持久 session/attempt、managed bind/admission/逐 attempt completion 全保留。
- Foreground 使用显式 `ForegroundOptions` 与容量 2 的 typed 控制/事件 channel、独立 sticky stop。它不伪造 SessionId/correlation，也不声称持久状态；实际调用原 untracked helper API、非managed bind。生产 agent RPC 不获得测试输入。
- `LoadedJob` 增加 legacy virtual/physical 选择，供已有明文测试命令复用 owner；managed dispatcher 在任何设备操作前拒绝这些选择。原 `trusted_network` / allowlist 校验不变。Native-only feature 不引 Rust engine/nusb。
- 公共 typed Event 保留真实 full NativeImportReceipt、实际 local address、发布注册 snapshot / backend snapshot、safe control ID 与固定错误类别。CLI 编码原 schema1 的 connected/disconnected、ready/snapshot/ack/error/stopped，不能从 SessionState 重造 receipt。
- 每个事件 delivery 等待 stdout 适配器 ACK；stop 独立于该队列。事件读取者消失、序列化/写失败或 5 秒 stdout 截止仍进入真正 cleanup。library 不安装 signal、不读 stdin、不写 stdout。
- terminal `JobOutcome {operation,cleanup,recording,final_event}` 在真实清理后返回；终态不发到可能无人消费的 channel。任意两个错误保留各自上下文。只在所有结果成功且无 in_flight 输出时，CLI 按旧条件写最终事件。

## 必须显式保留的差异

前台 client 继续原 `copy_bidirectional` 的双向半关闭/排空策略；managed client 保持首 EOF 终止的当前策略。共享 owner 内按模式选择，不能静默改变另一半字节处理。两者均在停止后 join 真实 relay，再 await helper close。后续版本化数据 framing 再单独处理统一策略。

Native 前台标准 IMPORT 失败回复 status 2/4 保留，managed 目前 EOF 策略不借提取默改。Native 发布 snapshot 是 pre-export registration，绝不是 live configuration。Rust virtual ready 的 devices、physical ready 的 snapshot、Native 的 backend/transport/exports_closed 字段各保持原形状。4096B stdin、safe id 1..64、两项输入队列和拒绝输入不回显 payload 的规则保持。

USBFS 初始化采用显式安全改进：一旦交付 helper handle 或启动 actual initialization，就保留并等待真实任务；stop 到来不再通过 drop prepare future 提前返回。取得结果后不 bind，先实际 shutdown，再按原规则 Finish。kernel 硬阻塞不宣称有强制截止或已清理；这仍是未完成的异常窗口，需要独立证据。

只读 helper Status 尚未交付能力，保持可取消，不能因以上约束阻塞原 startup TERM 验收。Native 的独占锁仅保护真实 stub 生命周期，实际 close 后不能继续被 WAL Closing/Finish 收据持有；准备失败的多个 export 清理也不能被首项记录等待串行阻挡。相关持久化错误保留，不能把取消收据当成功 ACK。

## CLI 停止与输出

信号在加载配置/设备准备前登记；blocking loader 即使取消也 join。真正 runner 由一个保留的 task 持有，第一次及重复 INT/TERM 只设置 stop，始终 await owner；不能把 owner 放进会被 drop 的外层 select 分支。stdout std thread 仅持有有限编码事件和写句柄。

5 秒预算从 EventOutput 既有 write/flush 收据统一计算，不在库中另叠 5 秒。signal 取消写后 `in_flight=true`，owner 完成后不能再堵在 stopped/disconnected 输出。正常 stdin shutdown 的 ACK 和最终状态保留原顺序。无 control_stdin 时不读输入，也不把“未启用控制”当 EOF。

## 验证

1. 有意义的 async duplex/真实 typed delivery、consumer 丢失、输出阻塞下 stop+实际 owner join、双错误保留、foreground 不调用 durable/tracked、managed 不降级等软件回归。
2. 完整 fmt/两 Clippy/default+allfeatures tests/release/rustdoc/vendor；Native-only feature 与 dependency tree。
3. 原六 foreground 双栈 QEMU、两 Native agent 双栈，旧 disk1 与 managed disk2/3 分别核；同源/ELF/clean guards 与 raw 独立复核。失败原件不覆盖。
4. 本地原四 signal/两背压仍跑；它们只覆盖 plaintext virtual，另加实际 TLS secure 子进程输出阻塞/信号回归。初始化合作停止的实际 task 证据独立记账，不把纯软件 fixture 当 kernel 硬阻塞验收。

设计先于代码。本项完成后也不把完整 Phase3、真实硬件或 TUI/GUI 标完成。
