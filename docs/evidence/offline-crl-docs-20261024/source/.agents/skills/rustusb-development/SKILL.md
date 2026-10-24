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

## 准备期记录与停止

修改prepare/ready记录、Native局部export stop或agent shutdown前读`docs/research/prepare-recording-shutdown-design.md`及`docs/research/prepare-recording-shutdown-validation.md`。667软件的167输入映射不等于clean运行；仿真只按各实际source/ELF归属。

- received Native export须在Resource ACK前注册唯一actual-close watcher；后来设备的记录不能保留已收owner。已知identity错误跨Ready/Interrupted保留，actual-close先于记录finalize/解锁。
- 局部StopSession不升级为wholejob stop；Catalog/直接IMPORT检查session stop并保留busy2。全局停止才关所有safe sibling，全局错误须传播。全局未停时空catalog仍保留原listener/job。
- pending记录仅持metadata，不持能力/FD/stream/permit；保留原已poll future及Begin/Attempt注册结果。USBFS先总join实际server/backend/accepted initializer、释放全部FD并尝试所有grant Finish，再等原ACK。client lateAttempt后不得发新helper。
- agent shutdown在Runtime persist之外sticky fanout，并覆盖晚注册control/继承parent stop；实际supervisor总join。reserved-unsent仍Unconfirmed，不伪proof/NotStarted，不以metadata局部未决关sibling。
- 采证proof必须读取对应saved snapshot，禁止以live journal补写旧观察。bounded门/采样不证明永久FS/kernel阻塞可取消；原FAIL与未运行项保持原裁决。

## 验证与文档

- 单元测试覆盖字节序、描述符边界、ID、状态机、配置、认证、错误、超时与取消；网络解析加畸形包和大小边界测试及 fuzz 入口。
- 假 sysfs、假命令和本地 TCP 测试可验证分支，不能证明真实驱动枚举。互操作三方向、QEMU HID/CDC、真实设备和故障矩阵见 `docs/testing.md`。
- 每阶段执行根 AGENTS.md 的四项 Cargo 检查。新增公共接口执行 rustdoc 检查。硬件测试须显式选择设备和两端授权，不扫描后自动解绑。
- 文档区分设计、源码事实、软件测试、硬件实测；修改行为同步 README、架构、兼容性和工作日志。不得编造性能或设备支持。


## 有界文件日志与授权读取

修改日志/外层main/ReadLogs/mirror前读`docs/research/rotating-logs-design.md`与`docs/research/rotating-logs-validation.md`。723软件/177输入、clean48c一个三epoch QEMU、本地6/4/2及89ef四原生CI各自归属，首44b1与41fce失败不可重判；旧23仍归1582。

- 默认stderr保留；file-only不tee，stdout只给命令协议。typed records不得格式化任意Debug/Error或持久化raw USB/serial/凭据/路径；generic profile字段不获得audit可见性。
- 业务commit/state替换后才mirror新序列，之前/旧ring不伪作已commit；logger无WAL/准入/Rootproof权力。先核peer+profile Audit，所有授权页/Busy/error绕Runtime；RPC不接收路径。只root本地读取helper store。
- 0700/0600/nlink1/NOFOLLOW及实际identity检查；每operation fresh accessOFD，不能dup共享flock。256finitequeue、两真实reader+两queued，取消RPC不能释放实际worker配额。FS/队列等待不持USB能力。
- 在Tokio/设备前打开，在actualowner cleanup后drain/join真实logger；永久FS硬阻塞不可假取消。LostRecords/IO可在操作完成后最终exit1，不能误说USB无副作用。
- normal peer-close若记录State+Protocol须保留，窄验收锁原incoming IDs/同conn/反tuple peerlocal_stop及host关闭先后，全Rootproof/baseline不弱化；不是cleanEOF或已跟踪reset原因。原FAIL和新cleanPASS分账。

## 私有诊断导出与默认console错误

修改diagnostics/immutable Registry/Begin-Page-End/console前读`docs/research/diagnostic-export-design.md`、`docs/research/diagnostic-export-validation.md`与`docs/research/stderr-write-failure-design.md`。797为dirty2ca/192选定source-build输入，clean835本地13组19child，clean d9一个paired QEMU/一epoch两I/O，d9四native CI每格797/九诊断组；default四组只属本地x86。原2ca exit101与bde gate启动FAIL不可重判，旧793/723及23矩阵保留原来源。

- 双Read+Audit/真实peer/profile逐请求，root同ownDenied；所有授权结果绕WAL，拒绝沿durable。实际commit/state替换/旧mirror后才发布，无USB能力/准入/proof权力，health/log独立采样。
- 四实际Arc/每UID一/30秒，End/expiry不可释放仍copy的实际quota；过滤后offset、16row/60000payload严格校验，不读私有state或凭据。
- 闭合schema1/4MiB/独立alias，无raw UID/PID/endpoint/path/key/token/USB/messages；操作/cleanup/linkend分开。真实0700/0600/EXCL/NOFOLLOW/identity/write/hash/sync/NOREPLACE/dirsync，不覆盖或删除未知文件，保留partial/不确定sideeffects。
- 保留全部worker/private runtime/accepted child与实际kill-reap；signal sticky停止新stage，已接受publication保真实结果，不假取消永久FS/kernel阻塞。CheckedConsole必须委托真实write并latch首returned IO，禁recursive internal error，actualowner cleanup后报告；不扩大为任意formatter panic/闭FD或硬阻塞保证。
- 原token End没有raw trace、单权限负向无独立proc、existing拒绝非并发rename，采证边界保留；samegate第二I/O后release、真实return0与完整Rootproof不能放宽。

## Installed认证负向与凭据退休

服务端安全计数修改前读`docs/research/server-security-observations-design.md`及`server-security-observations-validation.md`。clean c097的808软件/194输入、六raw观测paired与独立Native-v4/v2诊断回归/14guest0各自记账。loaded配置的clone/job共享volatile totals；TLS、独立framing、outer限额分开，Drop取消/饱和/一致性显式，不猜generic握手错误的证书原因。monitor不保凭据/stream/permit/USB owner；短锁无日志/FS/await。单独variant Read逐peer/profile授权，所有授权结果绕WAL，拒绝durable；旧Session/Metrics/disk/诊断bundle不改。不是持久安全审计或cleanup proof，安装态agent counter及v2负向counter仍未验收。

修改TLS/principal/device ACL或负向harness前读`docs/research/secure-negative-qemu-design.md`与`docs/research/secure-negative-qemu-validation.md`。clean25870c6六paired run/12guest0、每pair两epoch四屏障与两轮真实I/O，clean3d7软件797/同192源；四历史hostFAIL保留，264负向client退出记录不是USBcases。

- 标准PKI先于leaf pin，pin不能绕过CA/时间/用途；client服务名独立。running配置不可变，旧server actualwait/原资源基线先于新PID/pin策略，不以新进程资源0证明旧cleanup；startup退休不是热撤销/CRL/崩溃恢复。
- 未授权direct IMPORT须经真实TLS到server返回4，authorized duplicate返回2；catalog隐藏或client先拒绝不能代替它。before/after保原owner并随后实际I/O，不能只做echo/新catalog。
- TLS1.3 mapping拒绝可在client完成握手后表现为缺close_notify或精确Linux104；只该类认可此观察，不泛放IO或叫cleanEOF/已trace reset。其他证书/服务端校验保固定TLS/service类别；VHCI004按严格有界十进制解释NULL4。
- 201harness输入含192source/build；实测raw-v1、fast负向无逐executedELF、混合PCAP无旧正向classifier裁决、同作者raw方法非独立实现审计。所有实际owner/原proof/driverbaseline、guest0不能覆盖hostFAIL的规则保持。

## TLS认证准入限速

修改此路径前读`docs/research/authentication-admission-design.md`与`authentication-admission-validation.md`。固定Governor0.10.4/std-only+显式MonotonicClock，root严格policy；原连接permit先于非等待gate，TLS/verifier/IO在其后，失败/取消不退还，已建立stream/URB不再过门。配置clone/job共用gate；monitor仅metadata不持实际limiter/凭据/能力，短锁无日志/FS/await，poison拒绝新decision、饱和显式。旧TLS/framing/outer方程不变，独立Read RPC/control授权绕WAL、拒绝durable。这不是设备持久准入。软件824/196属clean47f，十三paired/26guest0属clean d619；首956采证FAIL不重判。安装态agent/v2专项限速、公平/长期DoS/性能、硬件及完整G3仍待验收。

## 主机名与双栈TCP回退

修改前读`docs/research/dual-stack-connect-design.md`与`dual-stack-connect-validation.md`。四resolver permit持于真实blocking closure直到结束，不能由waiter cancel释放；64raw/16unique只限制保留结果，不界定std/libc内部分配或lookup时长。OS首族偏好、族内稳定顺序/去重/交错，250ms正常及100ms失败启动间隔；inline16候选无detached dial task，winner返回前drop全部loser。保留调用者原总deadline/stop，只有TCP winner进入独立TLS身份验证与USB操作，TLS失败不重试/降级。clean f3软件842/198及clean67d十四paired/28guest0分账。NSS缺multi的旧失败不能当两候选对照；正确0d旧source/ELF+UID2000两答案仍timeout。Raw capture不改，projection只排精确stale空SYN，aggregate chronology非逐process归因；probe要实际UID/ELF/候选/override缺失。不是wire-DNS/VPN/性能或完整G3。

## 本地设备观察

修改`rustusb-discovery`、Linux observation/hotplug或devices --watch前读`docs/research/local-device-monitor-design.md`和`local-device-monitor-validation.md`。c034872/206软件与f66两个单guest各自归属；旧INT0c09错误/两采证QEMU FAIL不重判。source pid0/groups1仅重扫提示、原scanner当前事实；candidate/Bus ID/sysfs戳不授设备能力，Stale/Initializing不假空/不假删除。两actual worker配额留至真实完成，Drop只stop，shutdown总join；watch必须绕外层helper取消分支，stdout只有限metadata帧且完整end才提交，跳号/resync重拉全量。宿主raw设备输出只留.private并从公开清单排除；硬件/ENOBUFS/永久OS阻塞/全G3继续未完成。

## 托管远程目录

修改runtime Discover/目录并发前读`docs/research/catalog-supervisor-design.md`与`catalog-supervisor-validation.md`。clean b675626完整882/220源，两Native managed双栈QEMU/4guest0及4signal2BP各锁本次原件，旧red/开发compile FAIL保持。网络不await在session监督器内；最多2entry含已完成未消费，lost RPC不得还槽或abort owner。原UID/profile Discover授权先于容量，成功仍由原actor实际audit commit后回Catalog；无网络worker写WAL/USB/helper能力。service外部stop直接传播，队列EOF另有sticky源，总join真实raw/v2关闭树；不能将永久WAL/DNS等待说成有界可取消或外推v2 installed/完整G3/硬件。

## 认证远程目录观察

修改service::catalog/两watch CLI前读`docs/research/remote-catalog-watch-design.md`与`remote-catalog-watch-validation.md`。903/223软件锁ec91，221源两Native QEMU锁633；旧软件与两QEMU FAIL不重判。最多2actual本地owner，失订阅不持source/凭据/FD/quota；Drop仅stop，shutdown总join direct原FIN/driver，agent本地RPC取消不伪远端/独立resolver/USB结束。每poll真实UID/固定profile Discover和成功audit保留；2..120秒完成后轮询/指数退避，无自动import/降级。Initializing无数据为None、Stale保旧不假删除、permission/protocol终态清当前，generic handshake Permission不细分证书原因；busy可见性不当物理拔出，stable-key地址不授generation/能力。JSON完整matching end才应用，gaps/恢复resync；保持CLI owns_signal_cleanup/输出背压不持网络owner。新启动cancel仅源码、v2/地址变化仅loopback，硬件/完整G3不由此完成。

## 离线CRL

修改TLS信任根/CRL/config前读`docs/research/offline-crl-design.md`与`offline-crl-validation.md`。1..16/每64KiB/总256KiB完整DER、唯一raw issuer、strict present非空，metadata不替代WebPki真实签名/fullchain/unknown-deny/expiry及pin/ACL。Checked SystemTime共同窗口覆盖每次新证书验证，保TLS13/禁resumption/earlydata；旧stream不热撤销，agent仅重启job不重载。fcb927/226和e441十三QEMU分账，v2诊断没加载CRL不得冒充其installed负向；新fast CLI缺liveELF保留。无ALPNprobe空输入/strict1/protocol delta1，不加104例外或改判旧47FAIL。硬件/全G3/后续阶段不由此完成。
