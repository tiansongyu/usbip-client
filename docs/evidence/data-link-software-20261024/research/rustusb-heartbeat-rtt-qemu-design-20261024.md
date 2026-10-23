# 同 USB 数据 TLS 心跳 / RTT：QEMU 验收草案

日期：2026-10-24。状态：proposed，只读设计，未实施、未编译、未运行新 QEMU。源码起点 `031f63d9b27fa8712a13089f95aa61c1c2c223aa`；原始需求为仓库 `docs/requirements.md`。本稿由既有 secure/agent harness 作者做源代码分析，不是独立执行验收，也不把历史 PASS 重记为本次用例。源文件及固定上游原件 SHA 另见同目录 `rustusb-heartbeat-rtt-qemu-sources-20261024.json`。

## 结论与实施前必须冻结的接口

最小有辨识力的实验，是两台 disposable guest 运行真实产品 client/server，以新版本 framing 承载同一 IMPORT 后 USB/IP 数据和心跳；宿主只在其私有 Ethernet 链路上丢弃一个已证明归属的 TCP 数据连接。连接保持打开、另一设备持续真实 I/O、新 Catalog 连接成功，同时目标会话必须自行判定超时并完成既有真实资源清理。单独控制连接的 pong、TCP 主动 abort、关闭 relay、杀进程都不满足本项。

正式实现前需从 transport/service 设计锁定：

1. 明确 raw-v1 与 framed-v2 的协商和配置字段、双方不匹配的拒绝方式、Catalog/标准 IMPORT 回复到 framing 的唯一转换点。保持原 raw-v1 行为；不在 raw URB 字节中直接插入 ping，也不静默 downgrade。
2. 数据帧最大长度、有限队列/flow control 与公平性，Ping/Pong 匹配规则、并发 outstanding 上限与 local sample 序号归属；未匹配或过期 pong 不刷新活性。N/Root 当前评估 h2 0.4.20 成熟 flow control + PingPong，仍为 proposed：每 IMPORT TLS connection 至多一个 h2 USB stream，不自研无 credit 帧。若采用 h2，应复用它的标准协议/opaque Ping 处理，不另造业务 nonce 或宣称 local probe_seq 是 wire nonce。
3. 探测间隔 I、Pong 截止 D、发送/排队截止 W 以及哪一个时刻启动计时。RTT 必须由同一端单调时钟测量真实匹配往返；未收到匹配 Pong 不能制造 RTT。h2 public API 能否提供真实 wire flush receipt 尚未确认；应如实使用 queued/send-start 等已证明的时刻并标记本地排队成本，不把 API 排队成功写成已上 wire。数值目前未定，不在 harness 自选宽限来掩盖产品违约。
4. 每条实际数据连接的非敏感观察：mode、connection/attempt 标识、local/peer 地址与端口、sample_seq、attempt-relative elapsed、last_probe/last_pong 的匹配序号与时间、RTT、终止原因。agent 一个进程持多个连接，不能靠 PID 或“最旧 3241 连接”猜测目标。不要为采证暴露 TLS 密钥、原始 USB 数据或额外任意控制能力。
5. 驱动实际 read/write/heartbeat 的 task 必须独立于 1 秒业务 observe/WAL 收据；超时后先结束并 join 网络 driver/两 half，再等待既有 helper/USBFS 实际清理。operation Timeout、cleanup 与 recording 分别保留；health 不覆盖 client 的 VHCI Connected 或 server 的 ImportReplyFlushed 语义。

I 的 service 建议是每 connection/attempt 分别暴露 `raw_v1/framed_v2`、sample_seq、elapsed/last_probe/last_pong/rtt；raw-v1 为 unavailable，旧 epoch 历史为 stale。N 的独立 LinkDriver 建议包含 local probe_seq、queued_at、可证明的 send-start/flush receipt、matched_pong_at、rtt_us、typed timeout；local seq 不跨新连接，localDataBlocked 独立报告。transport Pong 只证明这条 transport 可以响应，不证明 USB completion 或目的设备健康。I 另建议 agent 新增独立只读 health 操作，保持原 sessions JSON；ImportReplyFlushed 仍需真实 DATA flush fence，PING ack 绝不能代替 IMPORT 回复交付证明。这些均为设计输入，尚不是已落地字段；本稿不替代正式契约。

## 已有事实与可复用接点

| 当前文件 | 可以复用 | 不能据此声称 |
| --- | --- | --- |
| `run-secure-qemu.py:58,155` | locked builds/source/ELF guards、双角色 PKI 隔离、BusyBox/模块/initramfs 来源 | 新 framing 已通过，或实际私钥可写入日志 |
| `run-secure-qemu.py:616–650` | 两 guest、e1000 私有 socket netdev、双侧 filter-dump、S0/S1/S2、QMP 键盘与 FTDI 字符通道、宿主 monotonic 控制日志 | 当前 socketpair 可丢精确单流；当前只是直通 |
| `run-agent-qemu.py:84,138,172,388` | 实际可信 TLS JSON/public DER/pin/权限采证、tracked session/attempt/Root proof、严格历史 ID、双 guest lifecycle | 该 runner 当前支持 USBFS/virtual；它现在只支持 Native |
| `guest-agent-init.sh:125,150–177` | 实际 VHCI 目录代际、父 shell 预开 evdev、35 B 双向 FTDI、真实 QMP key | 此函数末关闭 tty FD4；一次 Connected 或先前 I/O 不证明 arm 时仍有 pending URB |
| `guest-secure-init.sh:276–278,387–389` | virtual/USBFS 实际 snapshot、USBFS claims/inflight/驱动清理、两轮复用 | cached snapshot 等于任意未来瞬间内核状态；Native 有同样 inflight 指标 |
| `run-fault-qemu.py:4–5` / `guest-fault-init.sh:102–125` | 精确 tuple、listener/helper 存活、真实 NULL/AVAILABLE 核验经验 | 原实验明确是 `ss -K` SOCK_DESTROY，不是黑洞或心跳实验 |
| `service/jobs/client.rs:94–105,191–204` / `jobs/native.rs:718–750` | 原真实 receipt、IMPORT flush、两模式 EOF 语义、join/close、独立错误归并 | 心跳可挂在 await observe 后；旧 raw foreground 半关闭可随便改变 |
| `rust-backend/physical.rs:79–113` | 实际 pending_urbs、usbfs_inflight、reaped、完整驱动及 cleanup_verified | 只看 queued_urbs 就证明 URB 已进内核 |

新文件建议为 `scripts/qemu/run-heartbeat-qemu.py`、专用 guest 片段和小的 host Ethernet valve 模块。用显式 composition 或可审计共享纯函数复用基础设施，不复制旧 PASS 判定、不编辑旧六 secure/两 agent/TERM harness。新增源均进入 inputs，生成 init 字节/SHA 入账。具体 CLI 待正式 mode/config 落地后固定；建议接口只含 `--backend native|usbfs|virtual --family ipv4|ipv6 --fault-set directional --evidence PATH --kernel NAME`，运行参数与产品真实 I/D/W 全写入 summary。

## 故障注入：只触及目标数据 TCP 流

用两个 AF_UNIX stream socketpair 将两 QEMU 的 socket-netdev 接到普通宿主进程的有界 Ethernet valve。继续使用 guest-only root 权限操作 USB；宿主 valve 不需要 TAP、iptables/nft/tc、网络配置或 root USB 权限。它转发 Ethernet 帧，不终止 TCP/TLS、不解析 USB、不生成 Pong。

已核官方 QEMU v6.2.0：`net/socket.c:84` 在帧前写 4 B network-order 长度；`net/net.c:1689–1759` 重组；`include/net/net.h:19` 的 `NET_BUFSIZE` 是 4096+65536=69632。当前 e1000 socket netdev 未启用 vnet_hdr。实现须明确仅这一模式，限制帧长度 1..69632、每方向固定有限帧队列/总字节上限、部分读写与总截止；超限/不支持格式/日志满均失败，不隐式丢包。运行记录实际 QEMU 版本；若版本偏离已核语义先重新核固定源，不能把 v6.2 文本当任意 QEMU 版本的保证。

目标必须同时由产品 session/attempt tuple、guest socket/FD/PID 原件及两侧 SYN 流确认。arm 记录包含 family、两端地址/端口、方向、连接 epoch/SYN 识别、目标 session/resource IDs、host monotonic。tuple 重用不可继承旧规则；不能按整个 3241 端口或 NIC 丢包。

三个阶段分别丢 c→s、s→c、双向目标流的全部 IP/TCP 包（含 ACK/重传/FIN），保持所有 socket FD 打开。保留 ARP/NDP、另一设备数据流和 Catalog；不调用 ss -K、shutdown、kill、detach 或 QMP link-down。丢包期间至少实际截获所选方向目标包，否则阶段是无效注入，不能 PASS。阀门保存逐帧方向、长度、摘要、tuple、TCP seq/ack/flags、接收/转发/丢弃 monotonic 记录与计数；不给密钥或 USB 明文。第一次产品超时后也保持 drop，直到目标真实清理被观察，再 disarm；不会提前放行积压数据帮助清理。

单向黑洞不能强制两个端点都必须以 Timeout 结束：一端先超时，反向可达的 EOF/FIN 可能使另一端以 EOF/Io 结束。必须证明首个故障判定来自同数据链 heartbeat deadline，并保存两端实际 operation/cleanup，不能把远端正常 EOF 伪造成第二次心跳超时。

## 每个方向阶段的真实序列

1. 以新 attempt/连接启动两个设备，保存实际可信配置、权限、公用 DER/pin、执行 ELF、UID/caps、helper grants/journals。完成真实 DEVLIST、IMPORT、VHCI 枚举，保存完整 receipt、目录代际与设备关联；实测 HID key down/up 和每向 35 B serial。server Connected 仅证明回复 flush，完整枚举以 client 原件判定。
2. idle 正向窗口：不注入新的 HID/serial payload，保持设备句柄，采多次同连接匹配 Ping/Pong 与正 RTT；应用 bytes 不因心跳 framing 增长（若正式定义为 IMPORT 后 USB/IP application bytes）。不要求“完全无 TCP/USB 事务”，因为真实内核有控制与 pending IN。窗口和样本数依据冻结的 I/D 选择，不能靠 sleep 后一个 healthy 布尔值裁决。
3. pending 正向窗口：USBFS 目标 HID 同时满足实际 pending_urbs>0、usbfs_inflight>0、leased/config1；arm 前后保存同 receipt 与真实计数，保持 evdev 已打开且无新的输入。快照只能证明采样时确有请求；若要求同一 URB 跨 arm 不间断，另需精确 request/submit ID 与无 completion 的有界元数据证据，不以计数相等冒称同一 URB。Native 当前没有这些计数；只把 Native 记作真实内核设备已连接+idle。若扩大 Native pending 声明，须先设计 bounded usbmon/tracepoint 的 submit/complete 原件及依赖，不能拿 `dd` 等待代替。
4. 以两侧真实同数据 tuple 和旧 receipt arm 一个方向。另一 serial 仍使用原 session/connection/helper resource/目录代际，持续完成一轮 35 B 每向 I/O；目标禁用人工 stop。新建独立 authenticated Catalog 并成功完成，保存其不同 tuple，证明管理可达也不能使目标健康。
5. 保存真实可观测的 probe queued/send-start（仅确有 receipt 时记 flushed）、matching pong 最后 local 序号、timeout 的同端单调时间和原因；正确 pong 停止后，目标不能因另一设备或 Catalog 仍活动而继续报告新鲜健康。框架超时前没有人为关闭目标连接。guest 观察窗口按产品 D/W 加独立固定采证裕量；不得为过慢实现延长产品 D。parent 总截止始终只标 FAIL，不标已清理。
6. 等目标真实网络 driver 退出/join 和内核/USBFS 清理。目标 terminal 必须保留 Timeout 等实际失败；cleanup 可 Verified，但不据此改成操作成功。另一 serial 的原身份/句柄保持且再做真实 I/O。断言当前 live health 与历史/stale 有别。
7. 仅在真实清理后 disarm，明确新 attempt/新 tuple 重新 IMPORT 目标，完成新 HID key 与 serial 双向 I/O，不能复用旧 RTT。旧 session/attempt/Root proof 保留。再进入下一方向，禁止失败后自动继续累积表面 PASS。
8. 最后正常合作关闭全部任务和 server export，保存全原驱动/config/IF/alt/代际基线、资源0、实际进程 wait/guest exit、两侧 S0/S1 和完整原件；任何实际 kernel panic/Oops 从最终原始 S0 重读裁决。

## 终态证明按能力分别记账

- Native client：原 VHCI port NULL、原 local generation Gone、确切原 Import receipt 对应的 `native_import_released` proof；不以 PID 死亡/新实例0资源替代。
- Native server：每条 stub cleanup 对应 `native_stub_available`、同 export/bound generation 仍有效；stub关闭不要求也不假称 export retired。最终 export.close 后原完整 baseline 已恢复且 `native_export_restored`；managed Root proof 核 kind/correlation/key/UID/resource。
- USBFS：目标 IMPORT 的 actual cancel/reap/checked release 与 pending/queued/inflight/buffer归零、实际完整 baseline。服务启动时取得的 helper physical grant 可能在 incoming 已清理后仍由 server 持有，只有真实 server Finish 时才能退休该 grant；不能把 per-incoming lease cleanup 冒充 helper tombstone。
- virtual：真实 VHCI 端照常核；服务端每 lease 的 pending/队列/config清理及另一 lease 独立持续传输。它不是物理 USBFS/驱动恢复证据。
- 前台保持 untracked journal/disk 语义；只有 managed 才要求相应 retained Root tombstone。产品记录写失败须保留 recording 错误，不能以采证失败或时间流逝写假 terminal。

## 单调时序与 PCAP 的证据边界

RTT 用同一 connection driver 的真实 monotonic queued/send-start（或有 receipt 的 flushed）→matched pong 差值，明确是否含本端排队/flush；不减两个 guest 的 uptime，不减 wallclock PCAP 时戳得出产品 RTT。host valve 的 monotonic 证明注入/转发时间，串口 arm/ack 可给跨域因果夹逼，但不是时钟同步。可另加有限已知单向帧延迟窗口检查 RTT 随路径延迟变化；只有在实际交付延迟原件与对应 probe 序号可关联时才声称该结论，不从密文包猜 Pong 边界或发布 WAN 性能数字。

原 secure `pcap_review` 要求连续重组且有固定最少流数。有意丢包后双侧 capture 合理不同，不能放松旧 auditor 的 gap 检查然后复用 PASS。新独立 auditor 逐方向对照 valve ingress/drop/forward 和双侧 PCAP：非目标包必须完整转发，目标缺口必须精确有 drop 原件，重传单独计；未知协议/fragment/长度异常仍拒绝。TLS 可完整重组的发送方向检查真实 handshake/加密 record 和无明文 USB/IP signatures；受丢包而不完整的接收方向明确记录仅可核的前缀/后缀，不把部分 record 当完整 TLS 流。3240 IPv4/IPv6 bypass 拒绝与证书/权限老覆盖保持。

密文 PCAP 本身无法证明 Ping/Pong 内容或 nonce 匹配。该点由实际数据 driver 的受限诊断、同 tuple 关联和真实 TLS codec 测试共同支持；不导出会话密钥解密，也不把 host控制S2回复当网络 Pong。

## 最小分步矩阵与软件边界

首实施建议两个新 dual-guest run：Native/IPv4 managed（idle、tuple隔离、Root proof）和 USBFS/IPv4（真实物理 pending、worker清理）。每个 run 顺序三个方向，各阶段 fresh attempt，失败立即终止，保留第一 FAIL。它们只证明该 backend/family/模式。

在声称三 backend/双栈同数据黑洞能力前，完成 native/usbfs/virtual × ipv4/ipv6 六个新 run（每组独立三个方向），每组实测正向 RTT、故障、另一设备继续、最终复用；virtual 使用已有受控前台测试输入，不能给生产 agent 新 tap/inject RPC。Native 的 pending 声明仍按上述边界。后续 root 可按风险先分批执行，不将计划矩阵写成已覆盖。

因产品 pipeline/EOF/framing变化，保留原六 raw-v1 secure、两 raw-v1 Native agent、TERM 九组同源回归及原四 plaintext signals/两背压、新 secure 前台阻塞五场景。它们证明兼容和 cleanup，不重新算作 heartbeat case。未决 accepted initializer 的 kernel硬阻塞窗口仍不是本项证明。

软件必须有实际 TLS 端点测试：version不匹配/不降级、产品 framing/stream admission 边界、截断/超限/异常 peer 拒绝、错误或迟到 pong 不刷新本次 probe、用户数据不代替 pong、空闲主动读写、双向数据压力下控制公平性和接收方不消费时的 localDataBlocked、发送阻塞截止、EOF/stop/repeated signal、output/WAL observer 堵住仍真实 driver 超时+join、operation/cleanup 双错保留。若使用 h2，帧/credit/PING 细节遵循成熟库并围绕产品能控制的 API 和真实 peer 做必要回归，不写一套测试自研协议来冒充 h2 验收；尤其 h2 control 可以在 DATA credit 停滞时继续 Pong，健康不能掩盖本地 USB/data 不前进。定时器 fixture 与真实 loopback TLS 分别记范围，不将虚拟时钟当 QEMU timing。四平台CI实际 aarch64 可证明这些软件契约及本机架构，现 runner 强制 qemu-system-x86_64 且 evdev24字节解析，不证明 ARM guest 内核/USB。无真实两机硬件、WAN丢包性能或 storage重连验收。

## 原件与交付裁决

每 run 保留 clean HEAD、完整源码/生成init/内核模块/实际ELF前后 guards；真实roles UID/caps，TLS实际 JSON、DER/pin和私钥metadata-only；S0/S1/S2、原base64还原字节/hash、进程wait、完整物理与VHCI快照、应用state/audit与helperjournals、每attempt数据connection观察、原35B和key事实、双PCAP、valve日志及controller单调barrier。失败仍即时保存首次原因与已有快照，不让后续wait/collector错误覆盖首因。

`result=PASS` 仅在实际运行后由新verifier与独立原件复核成立；此设计没有检查数量/性能数字/新USB case。Phase2整体、G1/G2、完整Phase3、ARM真实USB、TUI/GUI与后续阶段均不据本稿标完成。

## 固定来源

项目事实以配套 SHA 清单中的本地文件为准。QEMU 帧格式只作机制设计依据：[v6.2.0 socket backend](https://raw.githubusercontent.com/qemu/qemu/v6.2.0/net/socket.c)、[v6.2.0 packet reader](https://raw.githubusercontent.com/qemu/qemu/v6.2.0/net/net.c)、[v6.2.0 buffer bound](https://raw.githubusercontent.com/qemu/qemu/v6.2.0/include/net/net.h)；原字节已保存在 `/tmp/rustusb-heartbeat-qemu-source-research-20261024/`。运行前仍须核实际 QEMU 版本/模式。现有官方[网络设备说明](https://www.qemu.org/docs/master/system/devices/net.html)仅辅助理解，不作为固定版本的运行证据。
