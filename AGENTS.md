# RustUSB 开发约定

目标：Rust 管理的真实 USB 设备重定向，Linux 优先。任务原文见 `docs/requirements.md`，分阶段执行，不将长期规划标成已完成。

修改 USB/IP 核心、设备操作、协议解析或特权代码前，必须先读取 `.agents/skills/rustusb-development/SKILL.md`，再读相关设计和源码审计。此显式入口确保不依赖特定 Agent 的技能自动发现机制。Codex 项目技能位于 `.agents/skills/`；其他 Agent 应通过本文件显式读取，不假定通用自动加载。

沿用用户级 UI/UX 和技术文档技能选择偏好。当前 Native、Rust 虚拟profile及实验性 USBFS adapter 已有 QEMU 证据；USBFS正常/热拔与原虚拟回归在干净37bc6b4通过；最新配置安全/多接口恢复在干净22d172d完成ECM、HID/FTDI正常及热拔三项回归，host-state syscall-entry取消/EOF六场景在干净c921a27最终通过；checked release修复与8个host-state/ECM/物理正常热拔在同一干净6e809a1的11项QEMU全部通过，113case/22guest0，软件89/114/19共222项及37c2d61四平台CI通过（后者仅Native测试夹具修订，QEMU结果仍锁6e809a1），见`docs/research/usbfs-checked-release.md`；内核内部active窗口及真实设备 G1/G2 仍未完成。物理设计见 `docs/research/rust-physical-backend-design.md`，不将整个 Phase2 标成完成。可选六页TUI预览见`docs/tui.md`和`docs/research/tui-validation.md`，持久偏好/Native双栈确认操作已有E46，E47未知应答/失联与E48热插拔/权威拒绝/d/l已补文档化G4软件/仿真证据；硬件/完整G3与发布门槛保持，原生GUI预览见`docs/gui.md`；技术文档以代码和固定上游版本为依据。

可选Rust服务的Unix SIGINT/SIGTERM合作停止见 `docs/research/server-signal-shutdown.md`：产品修复d39517b，clean934358f软件222项、本地四项与四项QEMU通过；QEMU的虚拟与all-features构建产物分别记账，CI39899971271四原生平台均通过，每格222次测试执行、原两背压及新增四信号场景通过。修改此路径须保留listener前异步prepare取消、取得server后总await cleanup及输出背压语义。重复信号不丢cleanup目前为源码约束；SIGKILL/崩溃、kernel硬阻塞初始化、Windows USB和硬件G1/G2继续未完成。

SIGKILL与首次重启的独立负向见 `docs/research/physical-crash-recovery.md`：clean1342a6a恢复FAIL、收集完整，原driver未恢复且新epoch采用null基线。持久授权基线、合作Finish和root只读协调已实现于b75f14b，clean933e720五项QEMU/十guest及本地四信号/两背压通过，软件250次检查及CI39908004666四原生平台每格250次/信号/背压通过，见`docs/research/physical-journal-recovery.md`。原失败保留；后续opt-in实现见下文，跨配置恢复仍draft。不得把新进程cleanup_verified/资源0当作旧恢复，不得仅dup旧FD或凭PID死亡清记录；信任获授权服务的合作退休不提供FD撤销。

准备期记录停止见`docs/research/prepare-recording-shutdown-design.md`与`docs/research/prepare-recording-shutdown-validation.md`：产品8ec570a，667软件actual dirtyb1的167输入逐Gitblob映射，原四signal/两背压同CLI72adb291…通过。修改必须保留每个received Native export在Resource ACK前唯一actual-close watcher、局部stop不连关sibling/原空catalog listener、known identity错误跨所有ACK分支、reserved-unsent保守Unconfirmed。USBFS全部actual FD/backend/accepted initializer与grant Finish先于原ACK；client lateAttempt不能再发helper；sticky service shutdown须覆盖晚注册control并总join。clean1582f06二十三QEMU/46guest0，同CLI/helper通过（八新prepare、六health18方向、九旧分账）；精确5ff的CI40044605684四原生each667/39及原信号背压通过；QEMU与CI只按英文报告实际revision记账，旧负向及采证FAIL不得改判；任意永久FS/kernel阻塞、真实G1/G2与完整Phase3仍未完成。

## 每次交付

服务端volatile计数见`docs/research/server-security-observations-design.md`及`server-security-observations-validation.md`：clean c097cf8完整808软件/194稳定输入、六认证观测paired与独立Native-v4/v2诊断回归通过/14guest0。TLS/framing/outer限额区分，loaded configuration scope跨clone/job，agent实例标epoch；metadata无能力，Read授权所有结果绕WAL，拒绝仍durable。不是持久安全审计或USB释放证明；安装态agent counter/v2负向counter、硬件G1/G2与完整G3未验收。

Phase3前置基线见`docs/research/secure-transport-foundation.md`：当时18项transport/304软件与匿名kernel机制，不重归属后续产品结果。当前安全broker已接Native、virtual和USBFS：clean b83c931六项双guest IPv4/IPv6 QEMU全PASS，12guest0、125输入/ELF guards真；完整软件412及精确f9558fc四平台CI各412通过，见`docs/research/secure-usbip-validation.md`。四个开发FAIL保留。Native USED早于device_add，须保留首次row/目录代际固定和有界初始化等待，不能 blanket retry/覆盖旧receipt；cfg/IF仍是phase状态。修改broker必须保留专用UID/互斥设备池、同TLS管理+URB、helper只交R/自持K、实际blockingguard/cleanup及持久未决不接管。完整G1/G2/G3、真实硬件和后续阶段未完成。

独立恢复句柄机制在clean da764d0双QEMU通过237项检查（原journal184+新增53）、两guest0和恢复后第三轮真实HID/FTDI I/O，见`docs/research/usbfs-guardian-mechanism.md`。guest-only probe证明独立B与dupA/C的占用差异和CONNECT恢复，未修改产品或实现自动恢复；首43d6d43的jq测试脚本FAIL保留。机制PASS不等于跨配置、FD撤销或硬件验收。

显式opt-in原driver恢复已实现于e34e309，clean2ab9976两个产品正向和五个原QEMU回归通过，14guest0、软件90/158/20共268次及本地四信号/两背压通过，CI39921583807四原生平台每格268次/信号/背压通过，见`docs/research/physical-driver-autorestore.md`。默认空许可、旧record manual、私有disk1/2与IPC1分开；仅sameboot/原owner ended/完整同cfg+IF+alt/独立B CONNECT，实际blocking任务持变更锁到核验退休，停止须join真实任务。旧producer迁移、许可撤销、外部C占用已在clean4565f7a三QEMU首次PASS/6guest0（318/293/313 host检查），见`docs/research/physical-driver-autorestore-negatives.md`。L始终manual并人工尾部；P/C在明确重启后产品恢复，C释放后同实例不重试。CI39928383971四原生平台每格268次/两背压/四信号通过，精确归属4565f7a。产品111输入/ELF保持；通用恢复/FD撤销、ISO/跨cfg/alt及完整Phase2仍未完成，该历史交付后的安全设计与产品接入结果见上方Phase3新基线。

共享非root应用服务见`docs/research/application-service-design.md`、`docs/application-service-guide.md`与`docs/research/application-service-validation.md`：产品在2a0f8d7，eaa1936软件495/四原生平台CI及六前台/两Native agent QEMU通过；cb08672只增强actual TLS配置/public DER/pin/权限采证，两Native双栈新clean通过，产品150源/ELF仍等eaa。helper tracked Native disk2/physical disk3保留精确terminal；managed IMPORT须持久admission后claim，stop请求不等于cleanup、softdeadline不得退休实际owner。共享前台提取在7f007a9实现、b45c2e8恢复原拒绝文案；530软件输入核对2638a8c、九QEMU/18guest0锁clean b45c2e8，见`docs/research/shared-owner-validation.md`，原单组FAIL保留；测试专用2638a8c修内核等待点观测，CI39999926362四原生每格530/五TLS/原信号背压通过，原b45两CI FAIL保留。Foreground无WAL/correlation且保留旧EOF/JSON/信号/背压，stdout ACK非持久准入。Native真实stub close/failed/unlock先于记录ACK；该E32证据未覆盖准备Resource ACK停顿；initializer kernel内部硬阻塞仍未验证。该E32时点尚无日志轮转，后续E35补充；无独立helper proof的Rust崩溃session协调和raw-v1 heartbeat/RTT仍不由此完成，不宣告完整Phase3。修改这些路径先读上述契约，保留operation/cleanup分离及Root kind/correlation/key/UID/resource全匹配。

可选数据链v2见`docs/research/data-link-health-design.md`、`docs/data-link-health.md`及`docs/research/data-link-health-validation.md`：产品85f2bb1/e29cc11、短management寿命修复6d933d2，626软件165输入逐Gitblob匹配，clean531e0c3十五QEMU全部PASS/30guest0（六新18方向+九旧另账），同CLIbb6fb276…/helperb4af4b5f…。e152 Catalog及934 virtual链路就绪FAIL保留；精确531的CI40029976284四原生各626/五TLS/原信号背压通过，runner/tmp缺项不补造。修改须保留独立driver、真DATA/FIN TLS flush、严格schema2/无downgrade、quiet全API/poll/drop、所有actualowner总join与ACK延迟时真实cleanup。短Catalog/已flush拒绝须借用保IO、双方FIN/有界EOF后自然join，不能flush后直接drop/stop；成功IMPORT不FIN，raw1保持旧路径且probe/RTT unavailable。新agent私有disk2严格迁移/有界link_history、旧Session/Metrics/IPC1不变；PONG不证明USB进展，pending URB/内核active窗口/硬件G1G2/完整Phase3仍未完成。

英文README为产品入口，中文README保留详细概览，Native操作/诊断见`docs/native-guide.md`。静态项目展示站在`website/`，维护和GitHub Pages发布见`docs/website.md`；它不是Phase5 GUI，也不扩大设备/平台/安全验收范围。README与展示站任务可使用用户全局`readme-site` skill。

- 先检查当前文件、Git 状态与 `docs/work-log.md`，不覆盖其他贡献者工作。
- 设计和范围变化先更新文档，再修改代码。记录来源、验证命令、结果与未完成的硬件验证。
- 稳定 Rust、2024 Edition；禁止核心路径 `unsafe`、`unwrap()`、`expect()`、`panic!()`。公共 API 写文档，错误保留上下文。
- 完成阶段执行 `cargo fmt --all -- --check`、`cargo clippy --workspace --all-targets -- -D warnings`、`cargo test --workspace`、`cargo build --workspace --release`。
- 按有意义的研究/实现/修复单元提交。用户已授权提交并最终推送到 `git@github.com:tiansongyu/usbip-client.git`。不强推、不删除远端历史。
- 保留需求逐项验收证据；缺少真实两端 USB 枚举不能宣告 Phase 1 验收通过。


有界日志与授权读取见`docs/research/rotating-logs-validation.md`/`docs/logging.md`：产品f3cf8d4、actualdirty48a4软件723次（308/395/20、177输入映射），同CLI6cc/helperbb0本地新6进程/旧4signal2BP、精确89ef四原生CI通过；clean48c9794一个installed Native-v4场景/三epoch/2guest0通过，原44b1 hostFAIL及41fce四cache前置FAIL保留。file-only typed NDJSON无stderr tee，业务commit后mirror不代替WAL/Rootproof，授权read含Busy/error绕Runtime，actualUSB cleanup后才join logger。源码修改须保留实际writer/reader配额和freshOFD locks；日志loss/IO可在操作完成后exit1，不能据此假设USB无副作用。normal peer-stop保留State/Protocol、fault客户端可保留Io而cleanup另为Verified；窄采证须锁原ID/tuple/control先后，非cleanEOF或reset因果trace。旧23矩阵仍锁1582/旧ELF；此时点未完成的diagnostic export由下述E36补充，年龄保留/永久hardblock/完整G3和硬件继续未完成。


私有诊断导出见`docs/diagnostics.md`与`docs/research/diagnostic-export-validation.md`：产品dff36a3及stderr修复8351577；797软件actualdirty2ca/192选定source-build输入逐835/d9匹配，本地clean835新allfeatures9组13child/default4组6child及旧4signal2BP通过；d9四native CI每格797/新9组13child通过，default4不外推。clean d9ebe2e一项Native-v4/framed-v2双端QEMU、一epoch两轮HID/FTDI真实I/O/2guest0通过；同原state.next candidate1 fsync-entry门下导出seq46/排除draft47与第二轮I/O，之后原syscall实际return0、六原Rootproof/fullbaseline/VHCI NULL/Gone。原2ca exit101与bde argc启动FAIL不可重判。双Read+Audit逐peer/profile、root同ownDenied，授权所有结果绕WAL；commit/state/mirror后发布immutable view，四实际Arc/每UID一/30秒，metadata无USB/proof权力。闭合4MiB/alias、0700/0600/EXCL/NOFOLLOW/identity/write/hash/sync/NOREPLACE/dirsync，保留全部worker/runtime及kill-reap；信号不丢已接受publication。原tokenEnd无rawtrace、单权限负向缺独立proc、existing拒绝非并发rename；State/Protocol与Verified cleanup分开。CheckedConsole保留真实returned IO/禁recursive internal errors，actualcleanup后最终exit1；不保证任意formatter panic/闭FD或永久stderr阻塞。年龄保留、永久FS/kernel、硬件G1/G2、完整G3及后续阶段继续未完成。

认证负向与startup pin退休见`docs/research/secure-negative-qemu-validation.md`及设计：clean25870c6三backend/双栈六paired run、12guest0，每pair同guest/helper内两credential epoch/四拒绝屏障/两真实I/O，Rust192源不变、clean3d7软件797。各44条目标负向client退出记录/总264非USBcases；server直接未授权IMPORT4/authorized duplicate2、原资源/I/O、真实旧server wait0/fullbaseline后新PID旧pin拒绝/B成功均有原件。四原hostFAIL保持（close_notify、服务层错误投影、mapping104、004数值解析），不能以guestPASS重判。仅raw-v1合作startup退休，不宣称热吊销/CRL/OCSP/崩溃恢复/fullG3。fast负向无单独executedELF采样；混合成功/拒绝PCAP无旧全正向classifier裁决，同作者另写原件核对非独立实现审计。修改harness保留early INJECT到held屏障、真正INPUT_READY之后才注入；只mapping类接受原TLS close/精确104，其他仍固定TLS/service类别，actualIO/完整proof不放宽。

TLS认证准入E39见`docs/research/authentication-admission-design.md`与`authentication-admission-validation.md`：产品47f、干净软件824/196输入，clean d619十三paired/26guest0（六限速/六默认/独立v2诊断）；首956 host采证FAIL保留。修改须保留连接permit先于rate decision、TLS/IO在其后，loaded配置clone/job共享、失败不退款、原stream不复查、独立metadata监视器不持gate/凭据/能力，Read授权绕WAL。不是durable USB准入、公平/长期DoS/性能或完整G3；安装态agent/v2专项耗尽及真实硬件仍未验收。

主机名回退E40见`docs/research/dual-stack-connect-design.md`/`dual-stack-connect-validation.md`：软件clean f3bf579的842/198稳定输入，安装clean67d十四paired/28guest0；六历史hostFAIL保留，有效旧对照为detached0d/source196/旧ELF，同修正NSS实际两候选仍timeout。修改须保留四actual blocking lookup permits至真实完成、64raw/16unique、OS偏好/250ms正常100ms失败间隔、inline候选drop、原总deadline及独立TLS名/仅winner进TLS/USB。std/libc分配/lookup时长不受保留界限制，不能假取消；wire-DNS/VPN/性能/硬件/完整G3仍未完成。

本地设备观察E41见`docs/research/local-device-monitor-design.md`/`local-device-monitor-validation.md`：软件clean c034872/206稳定输入，clean f66默认/all各一单guest PASS，六新process及原4signal/2BP通过。改此路径须保留kernel真实source提示与scanner权威状态、Initializing/Stale不伪空、candidate/地址/观察戳不授能力、实际worker配额/stop wake/总join、CLI owns_signal_cleanup与64KiB分帧/完整end/resync。旧0c09 INT错误和两QEMU采证FAIL保留，宿主raw输出.private不得公开递归归档；不是ENOBUFS/所有瞬时事件/双端USBIP新验收或硬件/G3完成。

托管目录E42见`docs/research/catalog-supervisor-design.md`/`catalog-supervisor-validation.md`：clean b675626完整882软件/220输入、两Native managed双栈QEMU/4guest0与原4signal2BP通过。目录网络在独立JoinSet最多2entry，lost waiter不还槽、原UID/profile授权先于容量、成功audit先于回应；外部stop直接到原owner且命令EOF另有stop，总join不abort。真实WAL/DNS硬阻塞、v2 installed专项和完整G3/硬件不由此完成，旧red/开发compile FAIL保留。

远程目录观察E43见`docs/research/remote-catalog-watch-design.md`/`remote-catalog-watch-validation.md`：903软件/223源锁clean ec91，两Native raw1 paired双栈QEMU/4guest0锁clean633，各原264+新69及旧4signal2BP3local通过。修改保两actual本地owner、完成后轮询/退避、initial/null/stale保旧/terminal clear、完整batch/resync、candidate地址不授generation；direct真FIN/driverjoin，agent本地stop不假取消服务端。每poll原成功audit/WAL成本保留；busy Native目录可隐藏使用中设备，不能改过滤来迎合count。首gate/两QEMU FAIL、fast denial无liveELF、启动取消未做新gate与硬件/G3未完成均保留；宿主.private不公开。

离线CRL E44见`docs/research/offline-crl-design.md`/`offline-crl-validation.md`：clean fcb927软件/226源，clean e441六CRL/六默认/一无CRL v2诊断13paired/26guest0。修改两角色TLS/TrustRoots/config须保完整CRL有界/单issuer/strict null-empty、standard WebPki fullchain unknown-deny及expiry、Checked SystemTime共同窗口/禁resumption。x509元数据不授认证；agent启动快照/clone共用，整个agent合作退出重启才重载，已有stream不热撤销，无在线fetch/序号抗回滚。新无ALPNprobe只供保存空输入，仍strict1/protocol delta1，不新增104例外；原47IPv6 FAIL不重判。installed v2/managed CRL专项、硬件和完整G3仍缺。

终端E45：clean ba0cedc9软件937/233源，clean d181665b Native双栈QEMU/4guest0、每pair10实际UID1000产品PTY及原264核验/三轮I/O。只用原agent RPC/metadata owner，普通用户、不读私钥、不调用helper变更；Unknown Start保原UUID且只能RetryStart，stop accepted不当cleanup，退出不停止service job。terminal同blocking线程/Fixed240x100/control清理/显式各项恢复，INT/TERM/quit后真实join两观察器。原f131 raw文字误判FAIL保留，解码差量屏幕后实际重跑；不把terminal coverage当confirmed USB动作或完整G4/G3/硬件。

终端E46见`docs/research/tui-settings-actions-design.md`与`docs/research/tui-settings-actions-validation.md`：clean5584c5b3完整950软件/235输入，四Native双栈paired/8guest0、58实际UID1000 PTY通过。新38确认为三偏好save/重启load/CLI override/可见save错误、6Start/1定向stop/4jobstop每pair，另20原invocation-only回归；均原264核验/三I/O/完整Rootproof。真实Start回执是原UI已ACK后的CLI RetryStart readback，非新CLI Start/合成JSON或lost-ACK UI验收。用户文件strict1/4KiB/无权限/凭据/endpoint/自动Start、0700/0600/NOFOLLOW/nlink1/独立锁/own临时/sync/重核rename；accepted FS task总join、不假取消OS硬阻塞。旧开发5fixture FAIL/C告警及历史失败保留；完整G4/UI专项异常、硬件/完整G3与后续阶段保持。


终端E47见[未知启动/失联验收](docs/research/tui-failure-retry-validation.md)：clean4ee6be3e完整952软件/235源，两故障与两原E46正常动作Native双栈pair/8guest0、76实际UID1000 PTY通过。实际Start超时后同agent恢复，UI只确认原UUID RetryStart；精确job current/stale/current，首次Profiles失联不再保留Current或旧journal/health。原38477360两guest0/19PTY但整体FAIL保留，不改其裁决；原264核验/三轮I/O/完整Rootproof不弱化。SIGSTOP不证明wire-loss或永久OS阻塞；UI热插拔/权威拒绝、完整G4/G3/硬件与后续阶段仍待完成。


终端E48见`docs/research/tui-hotplug-permission-validation.md`：clean2dc888ce完整956软件/235源，两single238/238加三托管237/237/232，8guest0、56实际PTY（54child0/两预期unresolved child1）；minimal与all ELF分账。实际同TUI kernel socket/PID下地址2→open modal空提示→地址3，Read/Audit-only agent两真实拒绝/0准入及d/l/Reload错通过。Root/json/nonTTY快负向无逐live-proc，不补造。选择消失/代际变化清detail scroll、相同identity重排保留；原935整体FAIL与首template失败保留。文档化G4软件/仿真逐项覆盖不扩大硬件/完整G3/发布或Phase5–7；普通用户设置不编辑管理员策略，确认不授予权限，拒绝后原UUID仍保守保留。


Phase5候选研究见`docs/research/gui-installation-research.md`：固定Tauri2.12.5/993038c，MSRV1.90；Linux直接GTK/WebKit依赖、AppManifest显式ACL、异步handle不能靠Promise丢弃取消、bounded pull与真实exit join是设计约束。该独立研究当时未解决glib0.18.5的0429；后续上游兼容修复与实际GUI证据见文末E49，GTK0415及ATK0413的旧unmaintained advisory已withdrawn，不误传仍在归档。研究314原payload hash核对、选择性摘要/API/metadata入库；研究任务本身未实施GUI/新依赖/安装/验收，也未接受架构。下一步须沿原完整Phase5要求完成GUI和包生命周期，不用原生Chrome mock或bundler输出替代native IPC/Ubuntu安装实测。


原生GUI/共享worker修改前读`docs/research/native-gui-design.md`和`docs/research/native-gui-validation.md`。TUI run/Options/Settings经headless rustusb-frontend复用，不另写USB/RPC业务；GUI独立workspace有mandatory check-gui与四平台CI，root all不承担GTK。glib两行上游补丁保持121原文件/许可/版本，只有variant_iter.rs变更；不压advisory、不执行旧UB。AppManifest三命令/本地main实际origin/有限准入到ack/原UUID跨reload/字符串counter/完整投影上限/CSP与导航拒绝/exit先join保留。长详情workspace min-height0与真实宽窄footer geometry已验。core956锁b15，GUI11及native QEMU最终锁03621a06（此前08保持独立）（guest0、child预期unresolved1、agent0、无USB准入）；原TUI四PTY锁72。所有原失败保留。positive GUI Start/Stop/远端RTT/activejob、规则/deb/systemd/安装/硬件/完整G3和原Phase5–7保持未完成。


GUI正向E50见`docs/research/gui-native-actions-validation.md`与`gui-native-actions-design.md`：clean8e8d50d9软件956+11及双栈378源/各12真实UID1000窗口通过，24次WM_DELETE_WINDOW/actual wait0/local join；六Start/一session Stop/四job Stop每pair、非空Remote及原三I/O/264核验/完整Rootproof保留。原agent229源/768MiB和旧single GUI372源另回归，共七guest0/无forced。产品三ELF保持E49；三夹具FAIL与误将exit4归native-close/已有Start的更正完整保留。raw-v1 RTT unavailable已有UI证据，v2 GUI live RTT/规则/完整GUI/deb/systemd/安装生命周期/硬件/完整G3/原Phase0–7仍未完成。


GUI链路E51见`docs/research/gui-link-health-design.md`和`gui-link-health-validation.md`：原80b同session reply1→3时真实open/focus丢失FAIL保留；a29产品修复current DOM有限匹配profile/epoch/session/connection，保展开/焦点/scroll并标历史RTT。clean c4f完整956+11软件、两新v2 GUI双栈390源/六guard及原v2/raw actions双栈/single GUI全部PASS，六次最终invocation/11guest0/31GUI child（30正常0/一旧预期unresolved1）、无forced。新六窗口保三方向/七I/O/packet/fullproof；IPv6c2s client io按原件保留。CLI/helper保持E50、GUIc0b141c6…16996192B。自动规则设计与两源码审查在`docs/research/automatic-sharing-design.md`，status draft/未实现；不得self-RPC伪UID、独立写trigger与Start或只做whole-profile auto-start便宣告完整规则。规则/安装/硬件/完整G3/后续阶段仍待完成。

自动规则E52授权/输入基础见`docs/research/automatic-sharing-foundation-validation.md`：clean52fd127d完整root978+GUI11软件通过/392输入guard真，增加10unit与1compile-fail各两次。独立root policy唯一trusted read、完整固定pool绑定、逐次ACL/UID复核、有限RuleSpec/RuleId/ExpectedRevision及四mutation输入已实现，无新依赖；没有生产调用者/agent启动选项/RPC/journal/后台触发/GUI启用，不改旧Manifest1/disk2/DTO。无新QEMU/硬件结果，不将旧c4f证据归给新ELF；下一步同事务准入/抑制/actual owner/dynamic pool/GUI/安装等原门槛不略。


自动规则E53运行时见`docs/research/automatic-sharing-runtime-validation.md`和`docs/automatic-sharing.md`：clean20a15a01完整root1019+GUI11/396输入及两Native本地规则双栈、原agent/v2 IPv4四QEMU/8guest0/无forced均通过，原264/三I/O和v2三方向/七I/O/packet/fullproof保持。显式policy才启disk3/观察与自动准入，origin+原Start同事务，独立mutation UUID/CAS/query/audit；Stop在WAL外sticky抑制并持久pause，晚注册继承，显式Enable不得清较新Stop。local source时间不可被WAL等待刷新，相同周期扫描只更新内存不增wire revision；单自动poll不提前还实际槽，所有owner总join。CLI调用前完整UUID意图帧非commit，unknown只query原UUID，原Start输出保持。开发早停两FAIL/0测试过滤/clippy expect失败原件保留。物理远端缺完整安全receipt而拒绝自动import；virtual/USBFS自动QEMU、hotplug/failure扩展、dynamic pool、GUI规则编辑/安装/硬件/完整Phase0–7仍需完成，不能把本增量称完整自动规则。
