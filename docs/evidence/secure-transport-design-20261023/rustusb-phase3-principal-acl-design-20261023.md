# Phase 3：证书身份、设备 ACL 与完整 USB/IP 会话接入

日期：2026-10-23。状态：**只读设计建议，未实现、未编译、未进行 QEMU 或网络/USB 操作**。

审查基线：`a7406d7080f6b3d9a73d1ea36a16d300eee0eb55`；开始时工作区干净。阅读了 AGENTS、核心开发 skill、technical-design skill、security/architecture/requirements、固定 Linux USB/IP 审计及下述源码。此报告不标记 Phase 3 或 Phase 2 完成。

## 1. 推荐决策

基于当前已实现接口，Rust backend 最小改动的产品通路是：**受信专用服务进程完成 mTLS → 从已验证叶证书派生不可伪造的连接上下文 → 按设备 ACL 过滤 DEVLIST、在 IMPORT claim 之前授权 → 将同一 TLS stream 直接交给现有 Rust USB/IP engine → 原有完整 URB/UNLINK/cleanup 路径**。它可以覆盖固定虚拟 profile 与既有实验性 USBFS 注册池，客户端仍须实际 VHCI 枚举和 HID/FTDI I/O，不能以 TLS echo 或 TCP relay 自测作为交付终点。用户完整 Native secure 目标保留；下文新增的 Native 直接 socketpair→stub 候选若机制验证通过，可避免 usbipd/明文端点，不应在验证前排除它或预先锁定 TCP upstream 代理。

Native 有直接 kernel FD adapter 与隔离 usbipd 两种候选，分别需要完整管理协议授权、长连接转发和清理验收。不要因为它已有工具而将所有第一轮 TLS 工作强行塞进通用代理：现有 Rust engine 的 transport seam 更小，设备租约/代际和完整数据流所有权更明确。Native 仍是产品的既有主路线，尚未完成其 secure adapter 就不能声称所有 Native 流量已受 mTLS 保护。

首轮不增加 helper 请求中的任意 `remote_principal` 字段。helper 继续只信内核本地凭据及管理员上限策略；受信 TLS 服务在该上限内实施远端 ACL。若将来要求 helper 独立验证远端身份，则必须把握手/令牌签发放入它信任的组件，不能把 JSON 字符串包装成远端认证。

## 2. 当前事实及改动接点

| 当前事实 | 源码位置 | 设计影响 |
| --- | --- | --- |
| listener 的 IP allowlist 不是认证；loopback 默认放行本机进程 | `rustusb-rust-backend/src/server.rs:47–98` | TLS 模式有独立、强制的认证配置，不能由 `trusted_network=true` 代替 |
| accept 后先检查 IP 并取得 16 个连接 permit 之一，再 spawn；shutdown abort/join 并等待 backend settle | 同文件 `168–212` | TLS handshake 必须在同一个 permit 和 JoinSet 生命周期内，不能创建无界预认证任务 |
| handshake 的 reader/writer 已 generic，DEVLIST 暴露所有 available 设备；IMPORT 直接调用 claim(busid) | 同文件 `222–260` | 这里是过滤与授权位置；权限拒绝发生在 claim 前 |
| 只有 connection 入口固定 TcpStream；actor、wire reader/writer 已 transport-independent | 同文件 `262–313`、`session.rs:37–64` | 改 generic duplex stream 即可复用同一 URB engine，不新增 raw plaintext listener |
| write_frame 目前只有 write_all | 同文件 `215–220` | **TLS 接入必须补 flush，并保持 write_all+flush 共同截止**，不能假设 write_all 已发送完 TLS 记录 |
| physical slot 持有 source Device、helper receipt、已打开 FD、原配置；claim 找到固定 slot 后改 claiming，并由 supervisor 持有工作 | `physical.rs:124–134,192–228,327–340,384–426` | ACL 可使用 `slot.source.id`，代际绑定可直接利用固定 slot；不要另按路径重开设备 |
| physical CLI 在 bind 前拿到注册池全部 helper FD，并保存 lease IDs 以在退出时 Finish | `rustusb-cli/src/physical_server.rs:20–121` | helper grant 的 owner 是服务进程；不是每个 TLS peer。TLS ACL 是该进程内部的可信约束 |
| helper 的 allowed_uids 是共享管理权限，physical IDs 是其可下发设备池 | `rustusb-daemon/src/lib.rs:225–328` | 交互用户拥有同一 OpenPhysical 权限时可以绕过 TLS ACL；secure 部署不能继续把其当普通不可信调用者 |
| helper 从 UnixStream.peer_cred 读取 UID/PID，不信请求 UID | `rustusb-daemon/src/linux.rs:125–178,841–886` | 这是本地身份依据，不是远端证书身份依据 |
| Native attach 通过官方 usbip 工具连接 host:port，再验证内核状态与 endpoint record | `rustusb-native/src/lib.rs:233–304` | 匿名 FD bridge 需要新的 typed attach 路径及独立 endpoint 元数据，不能伪装为旧 Peer 的 localhost |

上表行为来自当前源码，不是新增功能的运行证据。固定上游 Linux 审计基线为 `7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614`，并已有 v5.15/v6.8 对照；不是 Ubuntu 补丁内核的运行证明。

## 3. 身份与最小策略

### 3.1 首轮建议用证书指纹映射，避免自报身份和额外 X.509 名称解析

强制标准 mTLS 验证成功后，读取 TLS 库返回的 peer chain 第一张证书，对完整叶证书 DER 做 SHA-256，再查 root 管理的精确映射。**证书链/签名/有效期/用途验证与指纹授权是两个都必须满足的条件**，pin 不代替验证。初期不接受 Common Name、客户端参数、SNI、源 IP 或 USB/IP 字段作为 principal。

候选配置（名称为建议，非现有 API）：

```json
{
  "schema_version": 1,
  "principals": [
    {
      "id": "lab-a",
      "leaf_sha256": ["<64 lowercase hex>"],
      "device_ids": ["usb-<existing candidate hash>"],
      "max_sessions": 2
    }
  ]
}
```

- `id` 仅作管理员定义的稳定审计标识；由已验证证书指纹映射得到，不能由客户端选择。一个指纹只能归属一个 principal。
- `leaf_sha256` 可允许少量并存指纹用于轮换（例如最多 2 个）；空列表、重复/跨 principal 冲突、未知字段和过大配置拒绝启动。新证书需要显式更新；不因同 CN/同公钥/同 CA 自动继承设备许可。
- 首轮 `device_ids` 表示“可发现并可导入”；DEVLIST 与 IMPORT 复用同一 predicate。以后有实际需要再拆只读 discover/import 权限，避免两套 allowlist 漂移。
- 真实设备沿用现有候选 ID，但它不是硬件密码学身份。绑定本次服务注册代际，拔出后旧 slot 不能授权新代际；新设备需重新受控 grant。虚拟 profile 使用专属命名空间，如 `virtual:hid`，不可与物理 ID 混淆。
- 候选界限：配置 64 KiB、最多 64 principals、每 principal 最多 16 devices，ACL 并集必须是服务已批准注册池的子集；总会话仍 16。数值需与实际部署规模确认，不能因这些建议偷偷放大现有 helper/USB 内存上限。
- 配置/CA/私钥必须来自可信目录与受限文件权限；服务不可编辑自己的 ACL 来扩大权限。不要把秘密放入 argv、环境诊断或日志。

Rustls 标准 client verifier 默认要求客户端证书；`allow_unauthenticated`/`no_client_auth` 会改变该性质，secure 模式不得调用。`peer_certificates` 只有在相应认证完成后提供 peer 证书；仍须检查非空并经本模式的 verifier 成功路径取得。[Rustls verifier](https://docs.rs/rustls/latest/rustls/server/struct.WebPkiClientVerifier.html)、[CommonState](https://docs.rs/rustls/latest/rustls/struct.CommonState.html)（此次读取页面标记 0.23.46）。

### 3.2 生命周期与撤销

初期推荐 immutable startup policy + 明确 restart-required，不临时实现热重载。删除指纹或设备许可后，管理员停止服务、等待活动会话 cleanup，再用新策略启动；仅修改文件不立即撤销现存连接。日志包括 policy digest/epoch、principal、叶证书指纹、session ID、设备 ID/代际摘要、认证/授权结果与稳定错误码，不记录 USB payload、原始序列号或私钥。

首轮建议禁用 0-RTT 和会话恢复，降低撤销与旧身份缓存的范围；正常重连每次完整认证。不得共享不同认证要求服务的 resumption store/ticketer。Rustls 明确提示这种共享可能让未认证会话恢复进要求认证的配置。[ServerConfig](https://docs.rs/rustls/latest/rustls/server/struct.ServerConfig.html)。

证书到期默认是握手准入截止，不会自动中断已建 TLS session；若产品要求到期即时断开，应新增有界会话期限与明确清理测试。CRL 或在线撤销、无损热轮换、自动重连均不是首轮暗含能力。若只用短期证书+pin removal+restart，操作手册必须明确该撤销契约及可信时间前提。

## 4. 统一 engine 的最小内部接口

以下为接口草案，不是已编译签名；目的在于明确可信对象和顺序。

```rust,ignore
// fields private; produced only after this server's completed mTLS + policy lookup.
struct AuthenticatedPeer {
    principal: PrincipalId,
    leaf_sha256: [u8; 32],
    policy_epoch: PolicyEpoch,
    session_id: SessionId,
}

struct AuthorizedDevice {
    key: DeviceKey,
    registration: RegistrationId, // current slot/generation, not a reusable busid
}

async fn connection<S, B>(
    stream: S,
    backend: B,
    peer: AuthenticatedPeer,
    policy: Arc<AccessPolicy>,
) -> Result<()>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
    B: ServingBackend;

// resolve + authorize happens before ownership changes, using the actual slot.
fn visible_devices(&self, access: &AccessContext) -> Result<Vec<RemoteDevice>>;
async fn claim_authorized(
    &self, bus_id: &str, access: &AccessContext
) -> Result<Self::Lease>;
```

也可用 `AuthorizedBackend<B>` wrapper；必要条件是 inner backend 提供不可由 busid 重解释的注册句柄或 checked-claim。**不要先取 DEVLIST 做一次外部过滤，再直接保留旧 `claim(busid)` 无授权路径**。最小的当前 physical 实现可在找到 slot 后、写 `claiming` 前，用 `slot.source.id` 与固定 receipt 判断策略，返回错误时没有 USB claim/detach。无需为此开放全部 ServingBackend trait 为公共 API。

建议内部调用链：

1. 全部配置、证书和 ACL 先校验；physical 模式在请求 helper grant 前做完静态校验。
2. accept TCP → 可选额外 IP 限制 → 获取现有全局 permit → bounded TLS handshake → 认证证书映射 principal → 获取 principal 配额。
3. 设置 TCP_NODELAY 在 TLS 包装前完成。用 `tokio::io::split(stream)` 替代仅 TcpStream 的 into_split，两个 half 仍属于一个连接 task。
4. DEVLIST：只返回 available 且获准设备；计数根据过滤后条目重新编码；未授权 principal 不得到全量 metadata。
5. IMPORT：严格解析 → 查当前 slot → 同一个 ACL 再判定 → 取得设备租约。跳过 DEVLIST 直接猜 busid 也必须拒绝。未授权与未知设备可返回同一失败码，避免忙/存在性侧信道；内部日志保留真实原因。
6. IMPORT 成功后将 principal+device key+registration 绑定到 session；此连接后续只有该设备的既有 URB/UNLINK parser 与 actor。继续保留 wire devid 检查，不能借后续 management/opcode 切换另一设备。
7. 断网、TLS error、半包、超时、取消和 SIGTERM 均走已有 lease.begin_close/settle、后台 supervisor、Finish/journal；认证失败绝不产生租约，不能错误退休服务注册池。

**TLS flush 是实际实现必须处理的差异。** Tokio-rustls 的写入可能只进入内部缓冲，读取不会替它 flush；每个协议响应的 `write_all` 与 `flush` 应在同一 5 秒 deadline 下完成。否则一帧 IMPORT/DEVLIST 或单个 URB 完成可能等待下一次写入而死锁。成功发送/退休请求的判断必须在 flush 结果后；网络关闭时仍按原有规则回收真实 USB 工作。[Tokio-rustls 说明](https://docs.rs/tokio-rustls/latest/tokio_rustls/)、[TlsStream](https://docs.rs/tokio-rustls/latest/tokio_rustls/server/struct.TlsStream.html)（此次读取 0.26.6）。

必须维持：16 连接包括握手中连接；TLS 握手总截止与 USB/IP management 截止分开但均有限；2 帧队列、64 请求、64 KiB 单次传输、16 MiB USB 缓冲不变。TLS 自身增加的握手/证书/记录缓冲应单独审计和计量，不能宣称已有 16 MiB 覆盖 TLS。默认禁用证书压缩可减少首轮审计面；准确库版本、crypto provider、MSRV、许可证及已知安全修订由依赖审计确认，本报告不把文档版本当已选定供应链。

旧 `bind`/`bind_physical` 可为显式可信网络旧模式保留，secure server 使用独立构造入口且不会顺带开明文 listener；两种模式不能共享同一物理注册池对外服务。不要把公开 generic stream API 的“传入 principal”当网络认证：同进程调用者本来属于可信进程边界，运行时只有受验证 TLS 路径可创建远端上下文。

## 5. 本地 UID、服务信任与 helper

威胁模型中的本机非 root 用户可以打开 loopback TCP、运行自己的 usbip 客户端、向可达 helper 发 JSON、创建自己的 user/net namespace；不能据此读取 root/专用服务私钥或加入 root 所有的隔离 namespace。root、内核及已被攻陷的可信服务不在这次远端 ACL 防护之内，但其权限上限应尽量缩小。

- **推荐首轮不改 helper IPC**：专用、不允许交互登录的 `rustusb-service` UID 独占该 secure 服务的 OpenPhysical 权限；helper physical allowlist 是管理员批准的最大池。普通操作用户不再同时拥有同池 OpenPhysical/export/任意 attach 的共享管理权。现有全局 allowed_uids 不能表达“只允许 status”——若部署仍需保留普通用户管理，需另做 per-operation roles/独立服务策略，不能假装现配置已有角色隔离。
- 私钥、程序、配置、工作目录和加载路径均由 root 管理且其他 UID 不可替换；同服务 UID 的任意代码视为同一可信主体。单纯检查 `/proc/PID/exe` 或在请求里填 PID/程序名不能建立比 UID 更强的隔离。必要时 systemd 沙箱/LSM 收紧，不以“非root运行”自动等同不可信。
- helper 的 SO_PEERCRED 只能证明发 IPC 的本地进程 UID/PID；它不能证明 `remote_principal=lab-a` 来自证书。若添加这样的字段，只能用于可信 broker 提供的审计信息，明确标记 provenance，不能给普通 allowed UID 扩权。
- 若以后引入外部 untrusted worker，必须移走完整设备 FD 或将其纳入可信执行边界。当前 USBFS 整 FD 是真实设备能力；后置日志和 remote ACL 不能撤销恶意 dup，也不能限制持有者绕开 USB/IP actor。此报告不扩大已接受的可信服务模型。
- helper reload、journal 恢复、正常 Finish 的现有语义保持；TLS peer 与 journal OS owner 是不同身份，不把某个远端断线误当整个服务死亡，也不把 TLS session ID 当可退休旧授权的证明。

## 6. Native usbipd 的隔离候选与明确拒绝条件

### 6.0 新增 Native 直接 FD 候选：去掉 usbipd 和全部 backend TCP listener

parent 提出的候选值得优先做小型机制验证：helper 对明确批准的 Native export 记录完整代际 receipt，并 **预先打开指向原对象的 `usbip_sockfd` 属性 FD**；helper 创建匿名 Unix stream pair，将一端按既有内核 sysfs 接口交给该原 stub，另一端通过受保护 UDS/SCM_RIGHTS 给专用 broker。broker 完成 mTLS、DEVLIST 过滤和 IMPORT ACL 后，将同一连接的后续 URB 明文通过该 pair 送进 `usbip_host`。管理回复由已有 bounded Rust codec 生成，设备元数据/稳定 key/代际来自 helper 核准的导出对象，不借助另一个 usbipd 的动态 busid 查找。

| 候选 | 直接优点 | 本轮尚未证明的门槛 |
| --- | --- | --- |
| Rust engine 直接 TLS | 已有固定物理 slot、完整 actor、cleanup；仅 transport/ACL seam | TLS flush、身份/配额、client桥及端到端QEMU |
| Native anonymous pair→stub | 无 usbipd、无 backend TCP；可保留成熟内核完整 URB 通路；潜在原对象绑定 | stub 实际 AF_UNIX 收发/关闭、旧 attr FD 在移除/换代时 fail-closed、IMPORT事务和失败清理 |
| Native 私有 netns usbipd | 复用官方完整 IMPORT/URB；双栈 raw端点可隔离 | 动态 busid 二次解析的代际窗口、namespace启动与实测、额外进程/端点 |
| host nftables usbipd | 不需私有ns | 双栈+本机UID+现有连接及规则生命周期复杂；仍有代际窗口 |

直接 FD 候选一旦证实，Native secure adapter 更值得选择它，而非为兼容现有 CLI 固化一个公开的 localhost 代理；但当前不能把“SOCK_STREAM 检查通过”或“open 属性文件”本身当作兼容与同代际保证。native agent 正做固定源码复核，本报告未独立证明这些 kernel 细节。最小机制实验同时覆盖 VHCI 与 stub 的 anonymous pair，正常断开和旧对象失效；该实验本身不是 Phase3 完成。

helper 的新增 typed 请求应仅接受批准设备 key/不可伪造 reservation ID 与已认证的本地 broker 角色，不接受任意 sysfs 路径、任意数字 FD 或远端 principal 自报扩权。helper 保留 original attribute FD 和 kernel端 socket 所有权，发送端数量、ACK、超时和取消有界；若 socket 已交内核而 broker grant/IMPORT回复失败，必须触发关闭并观察原 stub 的实际状态，不能只 close 管理 FD 就报告无副作用。失败/重启的所有权需单独设计，不能偷用 USBFS journal 当 Native 导出记录。

DEVLIST 只能展示核准且当前可用的导出对象；IMPORT 对同一对象重复核准并消耗受管 reservation。broker 的 mTLS principal 仍由服务验证，helper 只确认服务角色与最大设备池，不能因为新接口可回传 metadata 就信任任意客户端身份。Native 的原设备解绑/恢复语义继续按已有实现验证，此候选不自动获得 USBFS baseline 恢复能力。

**移除本进程 usbipd 仍不足以消除外部 legacy usbipd 的旁路。** USB/sysfs 是全局设备池；必须拒绝或隔离既有可达 raw exporter，检查 host 和网络可达性，并在部署中禁用其启动。下列 dual-stack/local-UID 负向测试继续适用；不能以“新服务没有开3240”替代完整部署边界。

### 6.1 若保留 usbipd：仅 loopback 的 root-owned 私有网络命名空间

部署结构：root 创建一个无 veth、无外部网卡、无外部路由的 netns，仅启用 lo；官方 usbipd 与受信网关在同一 netns。**外部 TLS listener 在 host netns 由 root/systemd 创建，只将该 listener FD 传给网关**；网关新建的 localhost backend TCP 则属于私有 netns。不要把原始 3240 socket 也做 host socket activation。

systemd 249 官方文档明确支持服务处于私有 netns 而接收 host namespace 的 activation socket；`PrivateNetwork` 仅创建 lo，文件系统 Unix socket 仍可访问。可以基于显式 root-owned namespace + `NetworkNamespacePath` 或受控 `PrivateNetwork`/`JoinsNamespaceOf` 单元实现；最终 unit 必须在目标版本实测。[systemd.socket 249](https://www.freedesktop.org/software/systemd/man/249/systemd.socket.html)、[systemd.exec 249](https://www.freedesktop.org/software/systemd/man/249/systemd.exec.html)。

固定上游 usbipd 的 `getaddrinfo(NULL, ..., AI_PASSIVE)` 默认同时创建 IPv4、IPv6 wildcard listener（各 socket 设 v6only），没有按指定 IP 绑定的产品接口。因此改端口、仅测 IPv4、或只相信“网关连127.0.0.1”都不是隔离。私有 netns 将两个 wildcard 限定在同一孤立网络栈，不依赖 IPv6 恰好被禁用。[固定 usbipd 源码](https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbipd.c#L426-L530)。

Native adapter 不是无条件 `copy_bidirectional`：它先在 TLS 端解析 management，DEVLIST 获取受控 backend catalog 后按当前设备 ID/代际过滤并重新编码；IMPORT 验证 principal+device 后，只向固定内部端点发一个受控 IMPORT，核验成功响应和本地原代际，之后把**原 backend 连接**与 TLS 连接桥接直到会话结束。不能只隧道 DEVLIST，或 IMPORT 后让客户端重新直连公网。内核接管的 socket 仍需要整个 TLS relay 进程在场；退出需验证 VHCI/server state。

关键拒绝条件：

1. namespace 配置不可用或实际 namespace 与预期不符，**启动失败**，不得回退 host namespace。
2. 任意外部接口/veth/route 出现在 backend netns，或 raw 端口被映射到 host，secure ready 失败。
3. host 上已有可达 usbipd/其他 raw exporter 服务于同一全局 `usbip_host` 设备池时拒绝 secure 部署。USB/sysfs 并未因 netns 私有化；另一个 root usbipd 仍可能访问同一导出池。必须停用/隔离既有明文服务，不能仅隔离新进程就宣称无旁路。
4. 仅知道 busid，不能确认属于 root 管理池和当前代际时不列出、不 IMPORT；未知外部导出不自动继承 TLS ACL。
5. 原生 IMPORT 在授权检查后由 usbipd 再按 busid 查找，存在热拔重枚举竞态。前后完整 receipt 核验可阻止新代际 URB 转发，但事后检查本身不能证明从未短暂 claim 错误代际。必须在 Native 切片明确其授权语义并加入实际竞态测试；若要求零错误 claim 副作用，应引入原对象绑定的 typed helper handoff/受管 reservation，而非宣称通用代理已解决。

### 6.2 可行但非首选：host nftables 双栈防火墙

如不用私有 netns，必须用 inet family 同时处理 IPv4/IPv6，外部 input 拒绝 raw backend 端口；本机 output 只允许专用 gateway socket UID 到固定 loopback backend，其他 UID 对127.0.0.1、::1及所有本机非loopback地址均拒绝。仅 input 外网规则、只允许 lo、`usbipd -4`、随机高端口都不能防本机 UID。规则必须在 daemon 启动前原子安装，已存在连接/其他 base-chain、NAT/转发路径、重启与规则丢失都要纳入测试，管理权只属于 root。

nftables 官方说明 `inet` 覆盖 IPv4/IPv6，output 处理本地发送，`skuid` 是发起 socket 的 UID。[nftables manual](https://netfilter.org/projects/nftables/manpage.html)。规则组合与实际 socket/kernel 所有权仍需验证，尤其 kernel 接管后的流量；本报告没有执行或认证某个防火墙规则集。相对 host 防火墙方案，私有 netns 更容易给出独立双栈无旁路证据；在必须保留 usbipd 时优先考虑。若 6.0 直接 FD 候选通过机制验证，则不需要为它新增 usbipd 端点。

## 7. 客户端本地明文桥与 helper 的最小新增边界

Linux VHCI 不能直接接管 TLS TCP FD：其内容是密文，内核仍按 USB/IP 解码。客户端确实需要明文桥，但**共享的 loopback TCP listener 会把持有 client certificate 的服务权限借给所有本机 UID**，即使私钥本身不可读。端口难猜、握手后检查远端 ACL、绑定127.0.0.1都不能解决这个凭据代理问题。

与 native-diagnostics agent 的源码分析一致，优先验证匿名 `AF_UNIX/SOCK_STREAM` pair：root helper 创建 pair，一端留给受控 IMPORT/VHCI handoff，另一端经已有受保护 IPC 与 SCM_RIGHTS 交给专用 TLS 服务；普通调用者只能请求已允许的远端 profile+设备，不能指定任意 FD、raw路径、CA或 host。固定 v6.8 与主审计的 VHCI 入口只检查 SOCK_STREAM，后续为 socket ops 读写/关闭，源码上存在兼容路径；**这仍必须先做 QEMU 内核实验，不能以源码判定就宣布支持 Unix socket**。

如果内核实验拒绝 Unix pair，fallback 是 root 创建并同时持有两端的短生命 TCP pair：核对两端真实交叉四元组、关闭 listener 后才交付 relay FD，有限 accept/retry，任何抢连不匹配即关闭。不要把“第一个 accept”或秘密端口当本地身份。

新增 typed helper API 若采用该方向，至少绑定：`remote_profile_id`（root 配置的 host/port/server name/CA/client credential identity）、`remote_bus_id`、本地 caller UID、opaque tunnel ID、实际 kernel session receipt；消息不能任意选出口，向普通用户不得下发可重用的原始 tunnel capability。服务角色通过控制 UDS 的 SO_PEERCRED 检查；pair 上的 SO_PEERCRED 是创建时身份，**不能证明后来 SCM_RIGHTS 接收者**。

这需要独立的 Native session provenance：逻辑远端证书 principal/server name 与实际本地 bridge 不同。不能沿用旧 `/run/vhci_hcd/portN` 的 localhost record 冒充真实远端身份；失败或重启后身份未知就保留 unknown，不认领任意既有端口。本报告建议该 client bridge 与 mTLS server 第一条端到端通路一并设计，不承诺未实现的具体 RPC schema。

## 8. 最小有意义验收与未验证问题

| 验收 | 必须观察的结果 |
| --- | --- |
| 两个有效 principal A/B、不同设备 ACL | A/B 的 DEVLIST 各自过滤；直接 IMPORT 对方 busid 被拒绝，且未产生真实 claim/driver 变更；第三未知证书即使同受信 CA 也无设备权限 |
| 认证拒绝 | 缺证书、未知 CA、过期、错误用途、错误 server name；均在设备获取前失败；客户端不得重试明文 |
| 真实安全数据链 | 双 guest 经 TLS 完成 VHCI 枚举、HID输入和FTDI原 bytes 往返；server/client实际 TLS 连接、证书principal、lease和本地receipt相互对应 |
| 完整长连接 | IMPORT 后同 TLS 会话持续 URB/UNLINK；无外部明文连接；至少单帧小回复、TLS flush/backpressure、半包、中途断线和SIGTERM |
| 本机非root攻击 | 另一个 UID 无 helper grant/credentials/bridge FD；直接raw IPv4/IPv6/loopback连接失败；有效principal不能借CLI参数扩大device/remote profile |
| Native隔离独立gate | 实际netns inode、仅lo、两族监听、host无rawlistener、第二guest IPv4与IPv6 direct raw失败；host非root对127.0.0.1/::1/本机地址失败；TLS路径仍真实I/O |
| 资源与清理 | 握手占满16后有界拒绝/超时；unauthorized不创建租约；关闭/取消沿用真实cleanup和journal语义；不能因TLS错误误报资源0 |
| 设备代际 | 获准设备热拔后旧registration不可导入新代际；另一被拒设备没有因busid重用获权；Native另测IMPORT检查与接管间race |
| 撤销契约 | 停止旧服务并等待清理、修改pin/ACL后新实例拒绝旧证书/设备；不把磁盘编辑标成即时撤销；无session-resumption绕过 |

必须先定稿/实验证据：TLS依赖固定版本与安全配置；匿名 Unix pair 在目标内核 VHCI/stub 两端的实际支持；原 sysfs 属性FD的换代失效行为与 Native generation handoff保证；systemd namespace/socket activation在目标Ubuntu实际行为；本地角色隔离是否需要扩展现helperPolicy；TLS额外内存上限与读写取消语义。以上均未在本报告运行验证。

首个实现切片的成功终态应是“一个受设备 ACL 限制的 mTLS principal，经完整USB/IP链完成真实I/O，并在错误/断开后正确清理”。证书交换成功、网页状态变绿、TLS echo、通用proxy能收字节都不是该终态；也不能由这一个切片推出Native、硬件G1/G2、Windows、ISO或整个Phase3已完成。

## 9. 只读来源记录

此报告只创建 `/tmp` 文件。未编辑仓库、未编译/测试、未安装依赖、未调用 QEMU 或改网络/USB。外部读取限于上述官方库文档、systemd与netfilter资料；版本化docs.rs URL部分抓取失败，最终读取的latest页面显示 rustls0.23.46/tokio-rustls0.26.6，因此只将它们记作API参考，不伪称下载了固定crate或完成供应链审计。

下列哈希锁定本报告实际依据的本地文件；此后仓库变化不追溯成当前源码事实。

| 文件 | SHA-256 |
| --- | --- |
| `AGENTS.md` | `9d334b68590ddfcddb82e1d5920413e2e44d6ea17fa909fcb69e9496fcb7b21a` |
| `.agents/skills/rustusb-development/SKILL.md` | `10ef52f9f77ca2da0eca60f9fee0f7b7082a6eb4bf0f658cf0e887f581ba2380` |
| `docs/security.md` | `247d345170823874b217b7cb6ebe717fb3b85554b98b6eaf31cc3a669c9178c8` |
| `docs/architecture.md` | `2f750e317e3464cfcca5aa6e687747fbc1c100b5b6d2cb9a9fcd479b58b64bc1` |
| `docs/requirements.md` | `b7d168e31c7eadfa9c829c25da7ddcda6376815baabcd50d9cc5285b6bb67c2c` |
| `docs/research/linux-usbip-audit.md` | `60eb0ecec003eb24de586b0e96ee2ff00f2de5823d90f874167ae34852d237ff` |
| `crates/rustusb-rust-backend/src/server.rs` | `4fc5491fff5e952ed1b808e9293489925d9db3b279384703d4fa941026f3c532` |
| `crates/rustusb-rust-backend/src/session.rs` | `d7fe7367f6a1f870268eea22f51bd665b2e4eb90b653ee0499e9bc8d4caa578a` |
| `crates/rustusb-rust-backend/src/physical.rs` | `3d16abe180ad15b317431abf3a1c1a4f9412d67bb14e3b6535beb2551992c976` |
| `crates/rustusb-rust-backend/src/physical/observe.rs` | `f71bbc75dad0301c2e578c95a4c8aa5da2b03d037ac3c347ba4e245b9c6d100d` |
| `crates/rustusb-cli/src/physical_server.rs` | `c3b4e917b67a297ff321239b71ae26be28efd8c41cbe231574acb78ab67d2d7f` |
| `crates/rustusb-daemon/src/lib.rs` | `ab28cb8419f5bb2a07c855688bc9d1789d33edc1364b2a06b6be84051938f599` |
| `crates/rustusb-daemon/src/linux.rs` | `373aace67ecb6f63d891005c2e20f777832d128461cb6e0bf2172e99f21b7c35` |
| `crates/rustusb-native/src/lib.rs` | `b6465ddde887ce698a9c20232ab03df73df61505a5f4bcf9e25d2273a5735876` |
| `Cargo.toml` | `9b06bd0a1e9ef4aa2eadfad02dc9f5e67ef0613be574d118c8388a0763e2c552` |
| `Cargo.lock` | `629eb355ed917f7d213cffcd6cd93630ec37c479095b70689d6abcd4a0918e57` |

结束时 HEAD：`a7406d7080f6b3d9a73d1ea36a16d300eee0eb55`。
结束时工作区：干净。
