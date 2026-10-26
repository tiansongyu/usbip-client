---
name: rustusb-development
description: Develop or review RustUSB USB/IP discovery, protocol, native backend, privileged helper, sessions, and related tests. Read before changing USB/IP core code; excludes unrelated UI work.
---

# RustUSB 核心开发

存储接入前机制见`docs/research/storage-arrival-quarantine-{design,validation}.md`：最终05f26ffb四组/八正常guest0，软件1166锁3b9；仅上游工具、产品存储仍拒绝。三次早期FAIL保留，两client强制QMP退出不当正常关机。非零LUN sense限制、HCD重建/旧USBFS能力/硬件/完整交接未解决。相关修改先读报告，不把机制PASS当产品准入。

E64补充见`docs/research/arm64-virtual-data-{design,validation}.md`：ARM foreground virtual双栈两PASS/四I/O轮/八独立channel/四guest0，source分别1bb65475/3fc08f9c、包仍ed1，742输入guard全真。首IPv6 dpkg锁FAIL/两guest0保留；新guest仅安装前等锁，不删锁/杀owner/重试dpkg。无managed durable correlation，不伪造Root terminal；完整硬件/故障/GUI/后续Phase仍未完成。

E63最新见`docs/research/native-import-power-{design,validation}.md`：clean ed1e8fef原完整1138软件/738输入、八installed正向类别/104Rootproof与三legacy/virtual回归通过；共11正向+1failed boot pair/24guest0。ARM USBFS-v6正向仅独立metadata xHCI trace trial，原pre-product kernel stall/forced QMP quit保FAIL，不能称boot修复。只改新授权pinned VHCI generation delay=-1且active/identity复核；两原owner/typed partial receipt/同锁补偿/有界冲突与原NULL+gone proof保持，无全局/源设备/旧journal/新FD/wire/schema。未支持PM拒绝，不假取消永久sysfs阻塞；legacy其他await点原drop协调限制保持。E62旧失败/原加密原因未证实及硬件/完整目标不改判。下文旧条目是各轮历史范围。

ARM USB数据E62见`docs/research/arm64-usb-data-validation.md`：37b四MAC失败/bed49四HID失败及一trace均保FAIL，18paired guest0/全guard真不代I/O；两原full gate1114/734源。五单guest对照中原Linux USB/IP导入kbd auto/suspended无事件，仅该VHCI设备on后恢复；尚无产品PM修复或四case完整正向。修改Native tool/anonymous import前先读此报告与design，保持pinned generation/授权/partial cleanup，禁止全局autosuspend或替代node变更；硬件/全阶段仍未完成。

ARM64完整系统验收先读`docs/research/arm64-installed-validation-design.md`与`docs/research/arm64-installed-validation.md`。E61 clean76b4原软件1114/731输入与两Ubuntu cold、四QEMU0、每case42不同断言通过；ARM USB I/O/硬件/完整阶段未验收。原guest/C/GUI budgets保持，显式TCG/virt-6.2/cortex-a72/私有VARS和实际owner等待；真实ARM guest编译原fsync C并核AArch64/源与运行库hash。CI0316 ARM payload与+armtest1控制字段升版独立收据，Version/GUI Depends之外data/scripts必须字节/权限/owner/link相同；不是native rebuild或旧代码迁移。实际UID动态读（cold112/108），禁止从AMD/prepare拷常数或凭新进程0推旧释放。

修改framed management关闭先读`docs/research/framed-management-race-design.md`与`framed-management-race-validation.md`。E60保唯一原connection到真双向关闭：active stream1开始server graceful，原EOS/消费/credit/FIN flush后ClosingIo外置TrackingIo同transport write shutdown+peer TLS EOF，64KiB bounded discard/1KiB/每poll32读，不作尾部h2语义保证。无新owner/FD/权限/schema，IO/close_notify/stop/deadline/quiet保留，raw1不改。1114软件/722输入锁473，五PASS/70Rootproof锁四1b59+一393；mixed FAIL须协调两端升级再启导入，不downgrade。old control仅test overlay且独立target101；跨checkout禁共用target作来源证据，污染两run排除。原失败、c59计数修正及E59加密根因未证实保持，不能宣称硬件/ARM/整体阶段完成。

完整Ubuntu安装服务USB夹具见`scripts/qemu_installed_usb/README.md`与`docs/research/installed-usb-data-validation.md`。E59四Native/USBFS双栈targeted pair通过（三pair dd9390c4、Native/v4 d6e68915），八guest0/十二I/O轮/双方live升级/52原Rootproof；十原FAIL及Native/v4未定位间歇import IO不能重判或称修复。clean d6e68915全软件1094/717输入、原ELF保持。动态broker UID/原units，DAD/source route就绪；client升级后等原incoming terminal/verified，export Available不代释放。捕获完整boot和product-window，原classifier不改；原invocation journal stop/deactivate与PID/cgroup、Root exact kind/correlation/key/UID/resource/criterion分层核。正常released app proof可null但原Root必有；不扩为硬件/ARM/永久阻塞/完整Phase5验收。

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


## 原生GUI与共享前端

修改共享state/worker/preferences先读`docs/research/native-gui-design.md`与`docs/research/native-gui-validation.md`。rustusb-frontend保原TUI语义；GUI独立workspace/check-gui/CI，根默认Native无GTK。AppManifest/local main实际origin/有限action到ack、原Start UUID跨renderer reload、完整有界pull、counter字符串、实际exit join保留；配置不是权限，StopAccepted不是release。GLib上游0429两行backport不改version/license/其余原文件，不执行旧UB或压advisory。已验范围锁具体source/ELF与QEMU。后续修改GUI动作夹具先读`docs/research/gui-native-actions-design.md`和`gui-native-actions-validation.md`：E50在clean8e8d双栈各12真实进程/实际窗口close0/local join、六真实Start/精确Stop/非空Remote及原三I/O/264/Rootproof；原agent/single GUI回归独立。三旧FAIL与exit4误诊更正不改判；raw-v1无RTT。E51修改health显示/夹具先读`docs/research/gui-link-health-design.md`和`gui-link-health-validation.md`：保current DOM有限身份匹配/展开/焦点/scroll、历史RTT不当当前响应、原v2精确valve/packet/lifecycle/15秒窗口与真实typed io不重分类。原80b负向不改判；c4f两v2 GUI及原v2/raw actions/single回归独立。自动规则先读`docs/research/automatic-sharing-design.md`及两audit，均draft/未实现；权限/同事务准入/手动抑制/dynamic pool缺口不可略。规则/完整GUI/安装/硬件仍需验收，不以窗口编译替代。

## 自动规则授权与输入基础

E52见`docs/research/automatic-sharing-foundation-validation.md`：clean52fd127d root978+GUI11/392输入guard通过，仅授权/输入库，无生产执行入口/新QEMU。`AutomationPolicy`只能由root trusted read构造，不加用户JSON/Default旁路；每次启用/触发复核完整固定pool及真实或持久delegated UID的Read/Start/Stop/remote Discover，不以Start/隐式root Read授无限后台操作。RuleSpec/ExpectedRevision解码须保持有界/strict，mutation用struct变体。整体规则仍draft未接journal/RPC/runner；后续不能略去同事务origin+Start、独立mutation retry、manual Stop sticky抑制/持久pause、未决tombstone/旧owner、unsafe/stale拒绝及dynamic pool/GUI/安装验收。新ELF不得套旧QEMU结果。


## 自动规则持久运行时

修改前读`docs/research/automatic-sharing-runtime-validation.md`及`automatic-sharing-design.md`的Runtime契约。E53 clean20a15a01 root1019+GUI11/396输入、Native本地规则双栈及原agent/v2 IPv4四QEMU/8guest0均通过；只证明报告锁定范围，virtual/USBFS自动QEMU/物理远端receipt/dynamic pool/GUI/安装/硬件仍未完成。旧早停FAIL、0测试过滤与clippy expect失败保留。

E54补充见`automatic-sharing-rust-qemu-validation.md`：clean42e98ab3同产品源/三ELF，root1019+GUI11/399输入、50 schema/两语法及Virtual远端/USBFS本地双栈v2与原回归七QEMU/14guest0通过。三方向15秒/七I/O/原grant和sibling/packet/全Rootproof不弱化；Virtual HID只枚举，不伪注入；USBFS-v6 c2s client Io不重标timeout。UID1001全部manual ACL仍因无auto grant拒绝；新fixture显式schema/operator参数不改变原严格disk2默认。后续GUI/dynamic/物理远端安全receipt/安装/硬件等范围继续保留。

- 显式root policy才启observer/自动准入；严格disk2/3不同完整shape、必需nonnull automation、不降级/不重置旧owner。冻结Job/Session/Metrics/旧audit/log/diagnostic schema。
- origin/rule/revision/delegated UID/token与原Start job/request/audit和Fired同事务先commit再runner；Start FIFO淘汰不能消除已保origin。mutation独立UUID/CAS/query，unknown/expired不是没副作用，不自动换UUID或重发edit。
- manual Stop在WAL外sticky代际抑制，Stop事务持久pause；晚auto注册继承，旧Enable commit不得清较新Stop，原定向stop安全sibling保持。disable/delete不假cleanup、未决tomb计quota。
- local source monotonic完成时刻不由消费/WAL等待刷新；只Current/完整/安全/≤6秒，冻结primitive元组+boot，不以observerepoch/DTO新增字段造新设备。相同Periodic仅内存refresh，不加wire帧。
- 单auto Discover保原两actual槽/授权/成功audit，disable或丢waiter不提前还槽；fresh真实completion才fire。原DEVLIST不证明all-alt/config安全或物理generation，physical/Native远端必须拒绝自动import直至另有认证完整receipt。
- observer/network/USB真实owner直接stop并总await，metadata不持能力；pre-backend继承Stop/过期源须以原State/0session结束，不能仅相信backend prepare合作停即从未进入。CLI完整UUID意图先于RPC且非commit，INT/TERM/背压不追加generic JSON或发不完整意图后的RPC。

## GUI 自动规则

修改前读`docs/research/gui-automatic-rules-design.md`和`gui-automatic-rules-validation.md`。E55产品7ef/root1027+GUI15、fixture e668六QEMU/11guest0/34native child（single旧预期1）仅证明固定profile范围。Settings按需独立Rules/RuleEvents，不增TUI默认RPC；revision canonical decimal string→checked u64，typed有限action/完整pool与服务授权保持。新Create禁用/所有edit默认Cancel，disable/delete仅未来；草稿/原revision不自动rebase。原mutation UUID/profile/payload在Rust独立Start/Stop保留到精确Applied；Error/Expired/renderer reload仅原Query不重发，issued IPC总await，退出先实际join并报告未决。首DOM/第二tombstone verifier FAIL保留，回收resolved deleted须保history和原实际proof。settled负向协调、进程崩溃ID、dynamic/物理远端receipt/完整GUI安装硬件未完成。

修改规则decision路径前另读`docs/research/settled-rule-decisions-design.md`与`settled-rule-decisions-validation.md`。E56同clean650c root1054+GUI17/410源、八QEMU/15guest0/40native（single旧1）与CLI五进程通过。独立trusted root2/显式strict disk4不改root1/旧disk2/3；正负UUID去重/256拒绝表，先查committed positive，只有healthy已完成precommit失败经原Store持久才Rejected，writer未知保unknown，publication错误不覆盖已commit正负结果。support是Read metadata非授权/不阻WAL。frontend保原protocol/identity/payload，精确Applied effect或持久Rejected才清slot，显式默认Cancel恢复原profile/revision而不rebase/submit；Error/Expired不重发、独立Stop/实际join保持。known-unhealthy/support unknown在mint UUID前挡新edit；whole-process crash ID/动态pool/物理远端receipt/完整GUI安装硬件仍待完成。

修改frontend请求/checked preferences/recovery前另读`docs/research/frontend-intent-recovery-design.md`与`frontend-intent-recovery-validation.md`。E57产品/软件f174 root1076+GUI18/428源、final10117两fixture变更而三ELF保持，16QEMU/31guest0/60native（8计划137、原single1）及96PTY0通过，保两readonly-helper FAIL。local strict1闭合7字段/16KiB与endpoint摘要/UID/broker、两pending namespace/最后issued receipt，fresh OFD锁持于实际worker；共用checked0700/0600/EXCL/identity/rename/sync，blocking总join。先本地持久后新IPC、先本地记录后清slot，错误保原identity，恢复只原Query/原protocol，不自动submit/rebase/丢UUID；Started核原request/profile/OS UID，Stop不clear pending也不受writer挡。local metadata非service/USB授权或cleanup proof；未确认表单/所有Expired协调/断电安装态reboot/完整Phase仍未完成。

## Debian / systemd 安装生命周期

修改packaging或unit前读`docs/research/debian-installation-design.md`、两Debian/systemd research和`debian-installation-validation.md`。包源22d3/最终fded的软件1076+18、451输入、两Ubuntu amd64 cold通过只属所述scope，原五FAIL/两开发续跑不重判。无配置/状态payload、无默认enable/start/restart；仅安全缺失leaf创建、不递归chown，不删UID/凭据/旧journal，异常不拿PID0代USB proof。三条pre-unpack/remove gate保原失败、不让failed-upgrade绕过；实际先agent后helper总wait/cgroup，mixed/no KILL/infinity，已inactive mask保留。SIGSTOP会被manager CONT，只有明确test-only真实fsync gate作有限等待证据；arm64安装、新unit Native/USBFS I/O、断电/永久阻塞/真实硬件仍未验收。

E58停止信号守卫补充：当前包源/完整cold及软件锁clean0ad3a6ee，preview5→6，两Ubuntu amd64各44记录/42不同断言、四QEMU0/无forced；old/new升级gate在发信号前拒绝SendSIGKILL=yes、初始KillSignal=SIGKILL、SendSIGHUP=yes且原PID仍active。完整1076+18/451 inputs及三f174 ELF保持。原22d3/fded与五FAIL/两诊断继续原归属，见`docs/research/debian-installation-validation.md`当前段和`docs/evidence/debian-stop-signals-20261025`。非任意root配置/OOM/永久阻塞或新USB/ARM安装证明。

## 存储占用观察

修改storage-inspect前读`docs/research/storage-inspection-design.md`与`storage-inspection-validation.md`。clean c55a6c57本地1166/两Ubuntu各12场景/原Native回归，原ce840 mmap FAIL保留。UID0先于读取/配额；固定sysfs/procfs，实际blocking permit在closure内、helper shutdown总join，报告不持USB/块设备FD。逐TID查fd，map_files须走stat前后匹配的top-level /proc/TID，nested task/TID无该目录；mount root可为nsfs动态名。busy与issues/static limitations分开，不把Unknown变空，不输出路径/命令行/serial/完整dmUUID/存储内容。现有全部存储拒绝保持；本地RO/O_EXCL/proc扫描/解绑/Root释放不证明写入排他或持久缓存。完整存储共享/4.6、UAS/ARM检查/真实硬件仍待完成。
