# 前台入口统一到 service runner：只读契约审查

2026-10-24；审查基线 `9314aa08c41d84672467b06df8d0cf51e5d7ecf2`。本报告是源码与既有测试契约评估，不是本轮执行结果；未运行 Cargo、构建、QEMU、产品或 USB 操作，未写仓库。当前两套真实 runner 已有各自证据，不能直接把 `run_job` 接到旧 CLI 并宣称语义等价。相关文件 SHA/长度与固定 Git blob 对照见同名前缀 `sources.json`。

## 建议的最小提取边界

保留 CLI 对参数、可信配置路径、同步信号注册、stdin 和 stdout 的所有权；service 库拥有共享的连接、helper handle、relay 和最终清理树。前台适配器不启动另一个 agent/CLI 进程，不要求新的 manifest、operator、data/control 目录或持久应用 store。

建议在现 `rustusb-service::jobs` 下提取实际共用 owner，入口显式区分 `Foreground` 与 `Managed(JobContext)`，不能用临时内存假 ACK 模拟“已持久化”托管模式。

- `Foreground`：helper 调用保持 `prepare_native_import/export`、`open_stub`、`open_secure_physical` 的 `correlation=None`；内存局部 ID 可用于输出/控制，但不是 Root tracked proof。
- `Managed`：继续由现 `begin_session/attempt/resource/observe/finish` 和 backend managed admission 取得真实落盘 ACK；没有 ACK 不 claim。每个能力独立 fresh correlation。
- runner 输入应为已验证的 `LoadedClientConfig` / `LoadedServerConfig`，以及 Rust 的明确 `Binding::{Legacy(ServerConfig), Tls(...)}`。不能为了统一设置 `trusted_network=true`，不能把普通测试 listener 强制改成 mTLS。
- 停止入口采用非能力的 bounded control/watch；调用者取消等待只失去观察，不能丢弃 owner。API 文档须要求调用者合作 stop 后总 join。若暴露 handle，stop 与 join 是不同操作；Drop 只能请求停止，不能返回 cleanup proof。
- 返回值保留三个独立分量：operation、actual cleanup、recording。可用具体 `RunnerOutcome` 结构承载 Result；前台适配器保留原错误上下文，托管路径另映射固定 `Failure` code，不能先合成字符串再丢失两个原因。
- 新 typed runner facts 至少要包含真实 `NativeImportReceipt`、监听 `SocketAddr`、Native publication snapshot/accepting 状态、Virtual/Physical snapshot、实际最终 cleanup facts。前台 JSON 由适配器生成；不能从粗粒度 application Session DTO 重造 receipt 或当前 USB 状态。
- CLI 的 `--control-stdin` 通过本进程 bounded 控制通道发送 Snapshot/Shutdown；Virtual 的 HidKey/CdcEmit 必须限定显式前台测试控制能力，不能进入 agent RPC、manifest 或生产 operator API。

以上是接口形状建议，不是已实施公共 API。可先共享 secure client 与 Native owner，再共享 Rust binding/owner；完成目标仍须覆盖既有前台和托管入口，不以把副本搬到另一 crate 为完成。

## 迁移必须保持的外部契约

| ID | 必须保持 | 当前代码/验收接点 |
|---|---|---|
| C01 | 命令、feature 和权限保持。secure-server backend/config/control-stdin、secure-discover、secure-attach 参数不能新增 endpoint/key/pool 覆盖；普通 legacy 命令不要求 agent 配置。Native-only 不引入 nusb/Rust engine；非 Linux 保留明确 Unsupported，默认功能不意外启安全服务。 | `crates/rustusb-cli/src/main.rs:73`；两 crate Cargo.toml；`service/jobs/mod.rs:61` |
| C02 | Unix INT/TERM 在第一次 await、可信配置读取、helper/FD/listener 之前注册。main 不用外层 select 丢弃拥有清理的 runner；停止后重复信号不能 drop cleanup。配置 spawn_blocking 即使取消也 join。 | `cli/shutdown_signals.rs:16`；`cli/secure_server.rs:46`；`cli/secure_client.rs:21`；`cli/main.rs:560` |
| C03 | 接收可能已被 helper 接受的 Native prepare/open_stub，不因 stop 抛弃响应；得到 handle 后必须显式 close。client 先 take R、启动 TLS relay 再 commit；server stub commit 只确认 USED，IMPORT 回复完整 write+flush 后才允许 raw URB pump。 | `cli/secure_client.rs:133`；`cli/native_server.rs:110,393,426`；`daemon/native_channel.rs:84` |
| C04 | 所有退出路径先停止/回收实际网络 owner，再 await helper close；server 先停 accept、通知子任务、join 所有可能拥有 helper 事务的任务、最后恢复 export。不能以 task abort/Drop/新资源0/PID死亡代替原 NULL/Gone/完整 baseline。 | `cli/secure_client.rs:175`；`cli/native_server.rs:260,453`；`service/jobs/native.rs:54,267` |
| C05 | 保持 Native 原 K/R、原 attribute generation、首次 VHCI row/目录 generation 固定及有界初始化等待；只把明确未初始化属性视为 Pending，USED 不等完整 device_add。提取不改 kernel lifecycle 算法、helper mutation guard 或 actor 半帧取消保障。 | `docs/research/secure-usbip-integration.md`；`daemon/native_channel.rs:90`；`daemon/native_export.rs:118`；`native/src/channel.rs` |
| C06 | 前台默认 untracked；新空 Native store 保持 disk1，无 correlation/terminal。已升级 store 不降级；physical 原 disk1/manual、disk2/opt-in 授权原值不变，不因前台启动写 disk3。IPC schema1 与私有磁盘版本仍分开。 | `daemon/native_tracking.rs:289`；`daemon/journal.rs:1008`；`guest-secure-init.sh:229,253,488` |
| C07 | Native publication 明确 pre-export registration，不冒充 current config。导出后实际 cfg/count 双0字节、IF=[]；真实 import 后 cfg1/完整 IF/driver null；最终原 device/interface driver、cfg/alt 和硬件 generation 全核。 | `cli/native_server.rs:71`；`guest-secure-init.sh:214`；`run-secure-qemu.py:508` |
| C08 | stdout 仍 bounded JSONL schema1；stderr 是诊断。EventOutput queue2、编码<65536B、5s send+write/flush deadline，纯 std thread 只持 encoded event/stdout 不持设备。已取消在途输出不再次等待 stopped。不可把 std thread 换成 runtime 退出必须等待的阻塞任务。 | `cli/event_output.rs:15`；`cli/virtual_server.rs:199`；`cli/secure_client.rs:190` |
| C09 | JSON 必须保持 backend 不同形状。client connected: transport=`mtls`、server_id、device_key、完整 receipt；disconnected: cleanup_verified=true。secure server ready: transport=`mutual_tls`+真实address；Virtual 使用 devices，Physical 使用 snapshot，Native 使用 backend/native+publication snapshot。Native stopped 保留 exports_closed/export_cleanup_verified；Rust stopped 保留 backend snapshot。 | `cli/secure_client.rs:157,196`；`cli/virtual_server.rs:143,211`；`cli/physical_server.rs:144,206`；`cli/native_server.rs:197,288`；`run-secure-qemu.py:490` |
| C10 | stdin 仅显式启用；EOF 合作关闭，不终止进程/丢任务。4096B 单行、queue2、id 1..64安全ASCII、deny_unknown_fields，输入/hex不回显。Virtual 支持 hid_key/cdc_emit/snapshot/shutdown；Physical 控制注入返回 unsupported_physical_control；Native 只 snapshot/shutdown。 | `cli/virtual_server.rs:15,37,55,73,86`；`cli/physical_server.rs:152`；`cli/native_server.rs:509` |
| C11 | operation 与 cleanup 分开。secure client 同时失败保留两个原上下文，完成 RPC 优先于 invalidated，信号优先。cleanup成功不使 operation成功；cleanup不确定不得输出 disconnected/cleanup_verified。Rust旧服务 cleanup错误优先是现行为，不能误写为它已经保留双原始错误。 | `cli/secure_client.rs:164,181`；`service/jobs/mod.rs:122`；`cli/physical_server.rs:190` |
| C12 | USBFS 必须 backend close/实际 worker结束后，再尝试所有已收到 lease 的 Finish；一个失败不跳过其他 lease。初始化实际仍活时不能 Finish。defaultoff/optin及旧producer recovery 语义不能借提取变化。 | `cli/physical_server.rs:61,122,190,210,233`；`service/jobs/rust_server.rs:366` |
| C13 | 不改变 mTLS 同连接 management+URB、Catalog snapshot key↔BusID映射、slot 原子授权、16连接上限、5s管理frame写+flush以及外网无3240旁路。agent stop-session 继续只停对应设备；server Connected仍只表示IMPORT已flush，client Connected须真实VHCIreceipt。 | `cli/native_server.rs:292,307,364`；`service/jobs/native.rs:374`；`service/jobs/rust_server.rs:89`；secure integration设计 |
| C14 | 托管持久 admission/准入容量及真实owner到末次cleanup继续保留；stdout慢/断开不得阻塞agent的真实cleanup或改变audit含义。soft deadline不是terminal；没有Root exactproof的旧attempt仍未决。前台不能伪造持久证明，托管不能退化成可丢观察通知。 | `service/context.rs:17`；`service/jobs/rust_server.rs:61,121,238`；application-service设计 |

## 现有两套 runner 的具体语义差异

以下是迁移风险，不等于已证明的运行缺陷；不要顺手改现有行为后称纯重构。

1. **强制持久化与 tracked API**：`jobs/mod.rs:41` 只接 JobContext；`jobs/client.rs:17,42,47`、`jobs/native.rs:93,423`、`jobs/rust_server.rs:383`全走真实持久ACK/tracked能力。现前台 None 分支与六旧QEMU disk1约束真实不同。必须在共用层显式选择 ownership/admission，不可用无效 correlation 或假 store绕过。
2. **client EOF 语义**：前台 `secure_client.rs:154` 用 Tokio copy_bidirectional，读半端 EOF后对另一端执行半关闭并继续相反方向；托管 `jobs/io.rs:86` 用 select，第一方向 EOF直接退并释放两边。service Native server与旧Native server本来都是首EOF退出，不能把 client/server混成一个默认泵。首次提取需保留前台client语义或单独记录经授权的行为变更与尾部数据/半关闭验收。
3. **USBFS prepare 停止**：前台 `physical_server.rs:61` 可取消纯准备；已知 lease保留并Finish，实际 initializer仍活则明确pending。service `jobs/rust_server.rs:378,411`完整等待grant和initializer。直接替换会改变helper无响应/初始化窗口停止延迟与返回状态。建议只共享 actual resource owner，保留入口的pre-admission策略；若统一为总await，须另有设计和对应 startup SIGTERM新实测，不能只跑ready本地信号即外推。
4. **可观察结果不足**：service client丢弃commit receipt，只发 `LocalEnumeration`；service Native run没有把真实local_addr/publication导出给适配器；service Virtual/Physical消耗backend进serving并返回Result，无旧backend snapshot/testinput入口。需要添加实际typed observation/control，不允许从Session state重新扫描猜receipt或猜监听端口。
5. **双错上下文损失**：`jobs/mod.rs:122`双错返回固定State文字；现前台client保留两个原Error。持久日志保持sanitized Failure合理，但前台最终错误要拿到独立operation/cleanup结果。另一方面，旧Virtual/Physical/Native server普遍先cleanup?后operation，不能宣称它们现在已经双错误完整保留；修复需明确记账。
6. **控制解析结束行为不完全相同**：Virtual的合法长度 malformed JSON会输出invalid_control后继续；其超长/读取错误使reader结束，随后EOF退出。Native reader对任意parse错误都会关闭sender，输出invalid_control后终止循环。Physical沿用Virtual解析，合法Virtual注入op返回unsupported_physical_control。统一parser若改变这些结束规则须单独解释，不把“都严格JSON”当等价。
7. **低层错误映射不同**：service connect把所有TLS错误映射固定Permission；CLI connect保留transport展示上下文。service Native tls.accept全部Permission，旧Native区分Timeout/ConnectionLimit。可以在审计DTO净化，但不能无意吞掉CLI已有错误分类/调试原因。
8. **managed engine 的额外等待/容量**：service使用bind_managed_tls和observer的admit/ack；旧foreground bind_tls不等application持久化。若让前台也走managed monitor，应有真实、bounded、本进程 admission owner持续消费并在cleanup期间保活；它不能把stdout消费者作为持久admission、也不能泄漏未ack terminal阻塞下一IMPORT。
9. **运行权限/初始化依赖不同**：现foreground在指定broker UID启动，无agent data锁和profile ACL；run_job是已加载trustedprofile输入。前台适配器不能通过调用agent run/start改变PID/OSowner、helper归属或令旧shell终止只退出UI而留下服务。前台仍随该进程的TERM/EOF停止；agent的operator RPC退出则任务继续。

## 验证必须保留与新增的最小集合

既有脚本初期保持原件；新产品 clean commit与实际ELF重新归属，不用旧PASS充当提取后的证据。

- 原 `run-secure-qemu.py --backend {virtual,usbfs,native} --family {ipv4,ipv6}` 六组完整执行。保留真DEVLIST/IMPORT、两周期 HID keydown/up、每周期35B每向、Virtual echo/Physical FTDI host原bytes、四实际clientreceipt、精确TERM wait0、client NULL/旧localgone、四freshchannel、新catalog复用、Native held/released原export不变/四freshstub、USBFS/Native完整原baseline、双方实际guest0、base64/raw/PCAP/source/ELF/head/clean guards。Native disk1原断言不削弱为任意version。外链验证仍检查实际TLS流重组而非只看没有字符串。
- 原 `test-server-signals.py`四组 ready/blocked × INT/TERM：两个profile已IMPORT/config1且各pending IN1；ready须收恰好一个stopped并核资源0；blocked须保留4096B满管、不drain、独立OFD的NONBLOCK隔离和真实pipe_write阻塞，8s内exit0/双socketEOF。blocked没有末snapshot，不能声称独立观察backend零资源。
- 原 `test-virtual-backpressure.py`两组：SIGINT<=2s且exit0；5s输出deadline在4..7s区间非零，stderr含 `server stdout consumer is blocked`，lease socket真实EOF，测试期间不drain满stdout。更换logger/eventadapter不能更改这些门槛。
- 托管仍至少保留新两Nativeagent双栈：operator start退出而I/O继续、targeted HID停止而原serial receipt和真实I/O继续、actualwait137后exactRootproof、旧IDs与新attempt、原driverbaseline。不要把前台适配器共用成功当agent durableadmission未退化的证明。
- 有意义新软件fixtures：一次prepare已接受后stop/observer错误仍接收handle+close；commit/error和invalidated同时ready保留原error；operation+cleanup双错均保留；纯stream半关闭时反向尾部完整可交付；CLI事件字段从真实typedreceipt得到；stopped仅实际cleanup核验后；stdout事件receiver断开触发stop+join、不会abortowner；managedadmission缺ACK不claim，foreground不生成tracked request。可用可控mock/duplex/tempdir验证分支，但不能声称真kernel取消。
- 若改变USBFS prebind取消，追加原 `run-sigterm-qemu.py` startup helper-backlog与实际inflight TERM；ready/blocked本地Virtual不覆盖此窗口。如果只是搬移代码仍需按受影响路径选择回归，不能把3秒任意硬阻塞退出作保证。
- feature/静态gate：默认、all-features、secure-transport-only Native、strict rustdoc/Clippy、依赖隔离检查由root统一执行。新软件计数以实际日志为准；本报告没有新增测试PASS数。

## 结论与未决边界

可实施路线是共享真正的资源owner及cleanup，保留前台/托管两个明确适配模式。当前API最需要补的是untracked选择、实际typedreceipt/snapshot/control出口、独立operation/cleanup结果；EOF和prepare取消差异必须先决定，不宜隐藏在提取中。

本审查没有新runtime结果，没有修改旧六QEMU脚本；不将已知软件/仿真能力扩大为硬件、完整Phase2/3、heartbeat/RTT、轮转或后续TUI完成。Root可据上述接口点定实施契约，再授权必要新harness。
