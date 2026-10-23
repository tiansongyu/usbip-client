# 同一 USB 数据连接的 heartbeat/RTT：独立源码审查草案

2026-10-24，起点 `031f63d9b27fa8712a13089f95aa61c1c2c223aa`。**Proposed；没有实现、编译、运行或设备验证。** 已读原需求全文（4.4/4.5/4.11）、项目技能、当前架构/安全、application-service/shared-owner 契约及真实连接代码。源码和版本哈希见同名 JSON。本文不把原 G1/G2/G3 或完整 Phase3 标完成。

## 建议

优先验证 **固定 h2 0.4.20 + 原 rustls mTLS + 每 TLS connection 只有一个 HTTP/2 stream 1**。PING 在这条实际 IMPORT/URB TLS 连接上，不能借 Catalog 或另一条连接的 PONG 报健康。保留现 `rustusb-usbip/1` raw-v1；新模式使用独立、严格的 `rustusb-usbip/2` ALPN，不自动重试/降级 v1，也不回退明文 3240。

h2 提供成熟 DATA 流控/控制帧解析，减少自行维护信用窗口、控制优先级和异常帧状态机的成本。它仍需要正确的 **AsyncWrite flush fence、持续驱动/任务所有权和第三方日志隔离**；不是把 `send_data` 或 duplex 直接包成 AsyncWrite 就完成。下述机制必须先用真实反压/部分写/关闭测试验证。若无法证明这些 fence，再明确比较带信用控制的自研协议，不能退回“8B DATA/PING + 满队列 await”的假有界方案。

## 固定来源与实际接点

- 已有 Tokio **1.54.1**，commit `b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8`；tokio-rustls **0.26.6**，commit `8aebb7c9556ea8024c1b974b84a4a6f2e306889a`；rustls **0.23.46**，commit `f04804fa13341a9b1dc02b5d1dd8a746f2c4b7fb`。所读本地文件逐分发 crate 字节及 Cargo.lock checksum 核过。
- h2 **0.4.20**（2026-10-06，MIT），官方 tag/crate VCS 同指 commit `b0e890842ac16d3c76836fd4fcc06421acb15fa7`；crate SHA256 `7d29020232d6aa3fb1daca64c1127cf662cf97f254ae16c18c05b8ab635fc118`。选定源码逐官方 Git blob 核过。0.4.20 是本次官方版本查询结果，不代表永久 latest。
- `transport/src/lib.rs:22` 目前只支持 ALPN1；`tls.rs:185,256,272` 在握手后严格核 TLS1.3/ALPN，stream 私有持 peer/permit。新模式必须延续这些约束。
- `protocol/src/secure.rs` 是通用 AsyncRead/Write codec：前导/目录后仍为标准 DEVLIST/IMPORT，成功只消费固定回复，之后原字节流不能预读丢失。保持它不依赖 transport，避免 crate 环。
- Rust `server.rs:444,483–503` 先拥有真实 lease，再写/flush IMPORT，关网络后清理；managed 路径保持相同证据。`service/jobs/client.rs:91,187` 前台双向半关闭排空/managed 首 EOF；`jobs/native.rs:723` 的旧 relay 会被采样 WAL 等待暂停。不能在这些循环里顺手加 tick 当 driver。

## 协议与兼容

严格 schema1 冻结旧 DTO、保持 raw-v1；新增严格 schema2 显式配置 h2-v2，不能用旧 DTO 的可选字段暗中改变模式。客户端只 offer 自己配置的 ALPN，服务端只接受所配置版本，版本不匹配在取得该 IMPORT 能力前失败。第一切片可每 listener 单版本；无需假装已有混合升级能力。若日后增加 dual server，必须显式配置且每条连接保留实际协商结果，要求 v2 的客户端仍不得降级。

v2 在 TLS 完成后立即进行有界 h2 setup，随后创建唯一 stream 1（固定有界 CONNECT authority/method；不作任意 CONNECT 代理、不接任意目标，不使用扩展 CONNECT 的 :scheme/:path）。拒绝 push、第二 stream、额外路径/方法/任意 headers/trailers。HTTP 200 仅是 framing-ready，**不是设备已授权/已导入/已枚举**。原严格 secure 前导、标准 DEVLIST/IMPORT 与后续 URB 都作为同一 body 字节流；设备 key/BusID/ACL 仍在原真实 slot/锁内核验。kernel K/R 始终只看到标准原字节，不收到 h2/PING。

Catalog 仍使用独立短连接，不能生成设备数据 session 的健康证明。客户端选择/IMPORT 总截止继续涵盖 DNS/TCP/TLS/h2/管理，不能串加多个 5s 后悄悄扩大原总预算。h2 setup 在纯网络阶段可取消；已经发送的 helper 变更仍按原 owner 契约等待真实结果。

## 有界流控与背压

建议首候选与 root 对齐：stream/connection 接收窗口 65535，DATA chunk ≤16KiB，send buffer 64KiB，header list 2048、HPACK table0、concurrency1/noPush/reset budgets1。所有 builder 参数用固定验证常量，不能把恶意整数交给内部会 assert/cast 的 API。`send_data` **即使无 capacity 也会排队且文档明确可能无界**；LinkIo 必须 reserve/poll_capacity 后才接收一块有界输入，不靠 builder 参数代替。

`FlowControl::release_capacity` 只释放实际交付应用消费的字节；不能刚收到 DATA 就全部返还，让不读的 kernel/app 无界积累。h2 Connection 单独持续 poll，DATA window 用尽不影响同 connection 的 PING/SETTINGS。应用不读时，不在 Connection reader 中 await 满应用队列；维持有界 bytebuffer/现 RecvStream 流控状态，并单独报告 local data backpressure。

**small DATA 自动预算的实际算法**：`proto/connection.rs:98–107` 为 `max(connection_window/2,25600)`，65535→32767；`proto/mod.rs:38–40` 阈值256B、empty nonfinal lifetime100。`counts.rs:120–147` 非空 n<256 扣256−n，n≥256补n−256；不是帧个数。`streams.rs:1535` 在 `poll_data` 将 frame 交给应用时就退还该 frame overhead，清空 recv buffer 也退还；**不等到 WINDOW_UPDATE 的32KiB阈值**。padding 由库处理；final DATA 不收该预算。保留 Auto，不随手设低值64。64个48B+8B响应的理想最大 overhead 为29184，小于32767，但这不是调度/背压正确性证明；仍须测试64请求合法小 URB、长期 HID、暂停 consumer、单字节洪水。适配器及时取出并合并小 frame 能减少每 frame 元数据积压，字节 credit 仍等实际消费才释放。

接收 pump 由同一 owned driver 独立推进：`RecvStream.poll_data` 后写入固定65535B Tokio duplex pipe，及时退还 small-frame 元数据预算，但绝不在 poll_data/pipe write 时归还字节 credit。LinkIo 的成功 `poll_read` 按 ReadBuf filled_delta 增加 AtomicConsumed(total)+Notify；driver 仅按已消费总量减已归还总量的 checked 差值 release_capacity。received_total 必须先于字节暴露给 pipe 发布，避免跨线程 app read 领先的假 underflow。Notify 仅唤醒，单调计数为真值；注册/检查避免 lost wake。EOS 先排空已缓存数据，再关闭 pipe writer；timeout/error 才是 sticky failure。pipe write Pending 不能挡住 Connection/PING 的轮询。65535是未消费逻辑 payload 上界，不是 RSS；还存在至多一帧正在复制的 Bytes、TLS/codec/metadata 分配。

这些是各组件边界，不是精确 RSS 上限证明。已有 TLS64KiB 不等于所有进程内存；保留16连接、backend64请求/16MiB及所有排队/待回复预算。认证 peer 仍可造成流量型 DoS，不能把 h2 或 mTLS 宣称为完整限速证明。

## IMPORT flush 与 FIN 的真实 fence

h2 公共 SendStream 没有可直接代替 Tokio flush 的收据。固定 `codec/framed_write.rs:138–176` 将本地 header/payload 写完后才调用 underlying `poll_flush`；tokio-rustls `common/mod.rs:351–358` 此时排空待发 TLS 数据并 flush underlying I/O。因此 root 的 **FlushTrackingIo<TlsStream>** 候选可以只观察本地编码元数据而不复制/读取/记录 USB 内容：

1. client 方向先严格识别并跨 partial writes 消费24B HTTP/2 preface；server 不跳该前言。之后只存9B frame header、remaining、stream/type/flags和计数。
2. 只对 TLS `poll_write` 的 `Ready(Ok(n))` 前 n 字节推进；Pending/Err 不假装写完。初版保留 scalar 写即可，避免重复处理 vectored slices；现 AuthenticatedStream 已未暴露 vectored。
3. 只计 stream1 非 padded DATA。固定 `streams.rs:1207` 从 `Data::new` 创建、仅 set_END_STREAM，`frame/data.rs:28–36` flags0/padNone；编码器不会自行 padding。未知本地 DATA stream/padding/溢出是 link fatal，不继续猜。
4. 完整 DATA frame 被接受才推进 `completed_data_offset`；实际 TLS `poll_flush=Ready(Ok)` 才发布 `flushed_offset`。LinkIo.flush 等待其此前接受的 application offset。PING/SETTINGS flush 不会凭空推进 DATA；窗口阻塞不能报 IMPORT 已 flush。
5. **零 payload END_STREAM 要独立 `fin_seen/fin_flushed`**：完整 header及payload接受、再实际 TLS flush 后才能完成 shutdown，不能因 byte target 已相等提前成功。所有 u64 加法 checked，溢出终止不回绕。
6. peer reset、Connection完成/错误、local stop 必须唤醒所有 capacity/flush/FIN waiter，并优先返回 sticky failure；不可永远等待丢弃 DATA 的 offset，更不可返回成功。flush 只证明与旧 TLS write+flush 等价的本地提交，不是 peer 已收到或 USB 完成。

必须用受控 underlying AsyncWrite 测 partial header/payload、0写、vectored fallback、先控制flush、阻塞后恢复、写错、空FIN和数据目标未达到；之后才把 ImportReplyFlushed/响应退休接到此 fence。不改该证据名来绕过问题。

## Probe、RTT 与伪健康边界

`share.rs:550–569` 的 PingPong 忽略 Ping 参数；`frame/ping.rs:17` USER opaque8B是固定值，**不是每 probe wire nonce**。内部 CAS 状态只允许一个 pending；其他/无 pending 的 ACK 被忽略。只有 PENDING_PONG 状态的 USER ACK 会完成一次。网络 TCP/TLS 重放保护与“持合法私钥的恶意 peer 能主动假 ACK”不同，后者不能靠本地 seq 防住，也不证明 USB 进度。

建议 RTT 定义为本地 `send_ping` 入队时刻→对应 library pong 完成的单调 elapsed，包含 local queue/scheduler/远端处理；不要标成 wire-only RTT 或 flush 时间。local probe_seq 只用于同一 connection generation 内关联，不伪称映射到 wire payload。一次 pending 完成后才发下一次；timeout 关闭整条连接，不取消旧 ping future 后在同一 pending 上反复发新 probe。迟到 ACK 不复活已结束 link。

周期/截止是可信部署参数或固定保守常量（例5s/15s，最终由 root 冻结），按绝对 deadline 计算，任何 DATA 或未匹配 ACK 都不能重置未完成 probe。PONG 可在 DATA flow blocked 时成功：只能报告**同数据 TLS transport endpoint 响应**，另标 app progress/backpressure，不能叫设备健康。raw-v1 RTT/heartbeat 保持 unavailable，不填0；终态/旧epoch的最后 RTT只作历史。

## 所有权与安全 API

可实施的形状（API候选，未编译）：

```text
Link { io: LinkIo, owner: LinkOwner, monitor: LinkMonitor }
LinkIo: AsyncRead + AsyncWrite + Unpin
LinkOwner::request_stop(&self)
LinkOwner::join(self) -> Future<Output=LinkOutcome>
LinkMonitor::snapshot() -> LinkHealth
LinkMonitor::changes() -> watch::Receiver<u64>
```

factory 只消费真实已认证 stream/所协商模式，不接受任意 `(io, VerifiedPeer)`。private peer与TLS permit仍跟真实 Connection driver；monitor只持 latest事实/watch/weak，不保活 TLS、lease 或 permit。Connection、probe和 Native/client 的 raw relay 是被当前 owner 留存的任务树，不交给UI、不以孤立 spawn 丢 JoinHandle。Drop只请求 stop，不是完成证明；正常路径必须 join。

Native原 relay 在 WAL等确认时不被 poll；新 rawrelay 必须独立驱动，且其自身 select link-ended/stop：若正在写满 kernelR，仅关 LinkIo并不保证它再读网络EOF。失败时取消的是纯字节I/O，dropR并join，不能取消已发送 helper prepare/commit。Native `record_or_stop` 也观察 link-ended，仍先真实close/failed/unlock，再等原记录ACK。Rust engine 的 capacity2命令队列或claim亦须外层ended唤醒；真实claim/USB terminal仍由唯一监督器收回。详见 I 的 owner-wakeup 附件。健康不逐 ping 填8-slot lifecycle/WAL；持久/IPC细节由 I 草案独立说明。

半关闭需显式保留：LinkIo readEOF来自 END_STREAM且缓存已交付；shutdown发送本方向END_STREAM并等FIN fence，不默默杀对向。既有 foreground drain / managed firstEOF由 relay policy继续决定；若新v2统一策略，必须先另行明确接受，不能通过封装偷偷改。半关闭显示 Draining/Ended，而非健康 Connected；仍需 drain 时可继续 probe以发现余向黑洞。reset/TLS截断是错误，不能合成干净 EOF。网络树结束后才进入实际 helper/lease cleanup；timeout只是 operation failure，不等于设备已恢复。

## h2 日志隔离与维护边界

固定 `proto/ping_pong.rs:128` 会在默认 WARN 打印未知 ACK 的 Debug Ping，而 `frame/ping.rs:8` deriveDebug包含 peer8B payload；HEADERS trace也不适合公开。CLI默认 info不能直接放行后仍宣称不回显输入。

root 的 private `quiet_h2` 可用 NoSubscriber Dispatch + with_default，无新 unsafe：所有h2构造/调用、Future poll、Ready输出/错误映射、handle/FlowControl/response及Future Drop均在quiet作用域，raw h2类型不逸出。Drop须在闭包里实际 `drop(inner.take())`，不能返回take后外部drop。Option<Pin<Box<F>>>可安全保留不Unpin future；不改变其它应用tracing。固定 crate src 只发现4处文档示例 spawn，生产源没有自行spawn；调用者必须驱动Connection，也使poll/drop隔离可集中审核。

只输出自己的静态/typed H2 Reason、IOKind与计数，不格式化未知 h2 Error/headers/payload。全局 h2=trace 的负向须先证实捕获器能看到 unquiet未知ACK，再验证quiet无泄漏；**Debug8B通常是十进制数组，不是ASCII**，只搜ASCII sentinel会假通过。检查ASCII/decimal/hex与额外header sentinel，同时自己的安全审计仍输出。若任一handle逃逸无法证明，增加不可被EnvFilter覆盖的硬AND target过滤防御；不能只加可被长target指令覆盖的 `h2=off`。

已核 RustSec：2026-0258(empty DATA，修于≥0.4.16)、2024-0332(CONTINUATION，≥0.4.4)、2024-0003(reset exhaustion，≥0.4.2)、2023-0034(pending reset，≥0.3.17)。0.4.20覆盖这四项修复；**不是全transitive cargo-audit通过**。源码还存在内部assert/不可达priority编码分支，不能声称依赖整体无panic；不启用unstable、不发送priority。新的direct/transitive crate需root更新lock/第三方清单后实际构建验证；本次只下载到/tmp作MIT/checksum审查，未安装依赖。

## 进入实现前的最小验证门

- 纯软件真实h2+TLS：同连接PING成功/超时、全DATA窗口+不读app、合法64组小URB/1B洪水、wrong ALPN/无降级、多stream/push/trailer/headers/reset限制、driver不被WAL观察暂停、所有permits/tasks回收。
- FlushTracker各分段/写错/控制flush/空FIN负向；原 IMPORT成功必须等真实TLS flush，物理响应预算不能因排队先退休。quiet全局trace sentinel负向。
- Native/client rawrelay写kernel受阻时link-ended能dropR；Rust queued-command/claim中止仍等唯一真实completion。保留operation/recording/cleanup错误与旧epochroot proof。
- 原 raw-v1六前台/两agent与信号/背压回归保持；v2真实Native/virtual/USBFS数据I/O另记。QEMU只在两guest私有Ethernet valve按精确数据5tuple/方向黑洞，保留连接FD，证明另一Catalog连接/另一设备仍响应而被黑洞的实际数据连接timeout并真实清理。PCAP不能解密PONG；内部typed时序须和tuple/session对齐。重连是显式新attempt，不自动恢复存储。

本报告未创建repo文件、依赖、skill或占位模块；未运行Cargo、CLI、测试、QEMU或任何USB操作。h2候选和参数需root正式设计冻结后才实施。

## Primary references

- [h2固定源码](https://github.com/hyperium/h2/tree/b0e890842ac16d3c76836fd4fcc06421acb15fa7)，[0.4.20 crate](https://crates.io/crates/h2/0.4.20)，[RFC9113](https://www.rfc-editor.org/rfc/rfc9113.html) §§3.4,5.2,6.1,6.7。
- [tokio-rustls固定flush](https://github.com/rustls/tokio-rustls/blob/8aebb7c9556ea8024c1b974b84a4a6f2e306889a/src/common/mod.rs#L351)，[Tokio固定半关闭](https://github.com/tokio-rs/tokio/blob/b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8/tokio/src/io/util/copy_bidirectional.rs#L43)。read_exact/write_all不可通过select取消后重建而遗失部分帧状态；停整条连接可以丢纯网络future，不能继续解析原流。
- [RustSec2026-0258](https://rustsec.org/advisories/RUSTSEC-2026-0258.html)、[2024-0332](https://rustsec.org/advisories/RUSTSEC-2024-0332.html)、[2024-0003](https://rustsec.org/advisories/RUSTSEC-2024-0003.html)、[2023-0034](https://rustsec.org/advisories/RUSTSEC-2023-0034.html)。版本docs.rs PingPong页本次不可访问，使用上述已核真实固定源码，未伪称在线文档访问成功。
