# Phase 3：实际 USB/IP 认证加密的 QEMU 候选验收

状态：**draft / 只读测试设计，未实现、未运行**。日期：2026-10-23。审查基线：`a7406d7080f6b3d9a73d1ea36a16d300eee0eb55`，读取时工作区 clean。本文件只写入 `/tmp`；没有修改仓库、运行构建、QEMU、产品命令或设备操作，也没有新增测试框架。

## 1. 目标和未决接口

验证的是双方真实内核 USB 数据路径：独立 DEVLIST 连接，以及 IMPORT 成功后持续承载 Control/Interrupt/Bulk URB 的同一连接，都经过身份认证和 TLS；不能以管理 API HTTPS、TLS 握手成功、`trusted_network=true`、监听 IP allowlist 或 ready 事件替代。

这是 Phase 3 的候选验收切片。Phase 2 的 ISO、未覆盖 alternate/异常恢复等缺口及真实硬件 G1/G2 继续未完成；此设计也不提前宣告 G3/S1–S6 完成。

候选产品尚无 CLI/IPC 定稿，测试不得先编造可用命令。实现前至少明确以下契约，随后把真实 argv、JSON schema 和稳定错误类别固定到设计及 harness：

| 接口问题 | 验收必须能够区分的事实 |
| --- | --- |
| TLS 客户端/服务端及本地转发端点的生命周期 | 谁创建监听、谁持有连接/取消任务、如何等待 cleanup；不能把控制进程退出等同于内核 FD 关闭 |
| 远端 principal 与设备授权 | 证书身份如何映射 root 管理规则；DEVLIST 是否按身份过滤；IMPORT 对完整设备/当前代际重新授权 |
| 本地 UID → 代理能力 → 远端会话 | UDS 管理授权如何约束真正的本地 TCP 数据入口，避免 UID 1001 利用 UID 1000 的凭据 |
| 会话标识与重复语义 | 区分正常的多次发现、新 TLS 连接、重复 IMPORT、消费过的会话能力与合法断开后重连 |
| 凭据更新/撤销 | 静态加载还是 reload/重启；已建立会话是终止还是保留到结束；恢复票据和 0-RTT 如何处理 |
| 有界资源与截止 | 握手、认证、首帧、连接/会话数、每流/全局缓冲、写入、cleanup 的实际限额；已导入空闲 URB 不误当半包超时 |
| 可观察字段 | session/operation ID、principal 的非秘密标识、设备 ID、后端、两个 TCP tuple、阶段、稳定错误、结果；不把私钥/token/原始 URB 放进产品日志 |

最小候选可先限制 TLS 1.3、双向证书认证，关闭 0-RTT，并在撤销语义落实前关闭会话恢复；这是减少第一切片状态数的**建议，不是已选库/已实现默认值**。库与版本由正式产品设计决定并锁定，不能用 OpenSSL/stunnel 测试替身充当 RustUSB 成功路径。

## 2. 双 guest 拓扑与最低依赖

沿用当前 ordinary-user host + 两个可销毁 x86_64 Linux guest、QEMU 模拟 USB、真实 USB/IP/VHCI 内核，禁止 host USB passthrough、tap、宿主防火墙/模块/USB 操作或公网监听。

```text
client guest: evdev / tty + vhci_hcd
     ↕ 原生 USB/IP（受本地身份约束的入口）
 client product TLS proxy
     ⇄ 独立 private socketpair 的“外链” ⇄ server product TLS proxy
       QEMU filter-dump 全量捕获两端 netdev
     ↕ 受控本地 USB/IP 后端端点
server guest: native usbip_host，或 Rust virtual，或 Rust USBFS

第二条独立 socketpair / NIC：仅测试 barrier / 采证控制；不承载产品 USB/IP。
QMP、串口证据及 FTDI chardev：仍是 host 私有 Unix socket/文件。
```

现 journal/physical runner 在同一网卡上使用 5001 明文控制；直接在该网卡抓包并宣称“只有 TLS”会混入控制流。新候选应隔离控制链，保存两链完整路由/监听/防火墙配置。控制链同样不得成为后门：从对端探测服务的控制链地址 3240/其它 raw 后端端口；测试控制服务只接固定 barrier token，不提供任意执行/转发。

外链可使用 `192.0.2.1/2` 与 `2001:db8:1::1/2`，控制链使用另一私有网段。地址只是测试 fixture；IPv6 必须在两 guest 实际配置、邻居解析且成功完成 TLS/I/O，未启用/不可达不能计为安全阻断。记录 `uname`、内核 config/模块、QEMU/原生 usbip 工具、IPv6、TLS 库版本；当前 harness 没有 aarch64 QEMU 证据。

最低复用依赖：现 QEMU/BusyBox/cpio/jq/setpriv、真实 usbip/usbipd、内核模块、Python 标准库 runner；增加的 guest 诊断工具只按实际拓扑复制可信 `ip`/`ss`/防火墙工具和必要模块。外链 PCAP 优先 QEMU `filter-dump`，无需宿主 tcpdump 权限；本地明文边界如需独立 PCAP，可在 guest root 用固定 tcpdump，记录其二进制/库哈希并限制捕获到测试端点。这是测试观测工具，不是传输实现。

证书仅使用新生成的临时测试 CA/leaf（含正确身份、错误 CA、错误用途、过期/尚未生效、已撤销等 fixture），不接触真实凭据。记录 public cert/CRL 的 DER SHA、有效期、SAN/EKU、签发关系和两 guest 实际时钟；不能通过“证书生成命令没报错”认定负例有效。私钥只在临时 0700 目录/guest 受保护路径，不进 argv、产品日志、Git 或归档。含私钥的 initramfs 可只归档其 SHA、生成参数和公钥材料，临时本体不上传；需要重现时重新生成测试凭据并记录新的精确 hash。

## 3. 以最少增量复用既有 harness

| 现有文件/模型 | 直接可复用 | 不可沿用为新结论 |
| --- | --- | --- |
| `run-native-qemu.py` + `guest-init.sh` | `copy_binary`、模块/initramfs 打包、匹配 ELF usbip 工具、QMP；Native export/attach/receipt、HID evdev、FTDI 双向字节 | 原例为明文 IPv4，`usbipd -4` 不能证明 IPv6 或 TLS；每次现例选一种设备 |
| `run-userspace-qemu.py` + `guest-userspace-init.sh` | 固定 HID/CDC，真实 VHCI/usbhid/cdc_acm、pending IN、普通 UID、原 wire probe | 192.0.2.1:3240 直连与 IP allowlist 不是认证；原 verifier 没有 TLS/身份断言 |
| `run-physical-qemu.py`、`run-journal-qemu.py` | `physical.Qmp`、QMP 键盘/FTDI socket、exact RX/TX、receipt、双串口、完整 config/interface/alt/driver、真实 wait、首次失败留档与双端 barrier | 原184项是旧 journal 契约，不能改预期后冒充继续通过；原kill/admin恢复步骤不应塞进首次TLS正向 |
| `run-fault-qemu.py` | 实际 ESTABLISHED tuple 选择、精确 transport abort、双方先保存 primary 再重连 | 杀 usbipd/载波 down 不是已交内核 TCP 的关闭证明；15秒旧窗口不是TLS SLA |
| `run-sigterm-qemu.py`、`test-server-signals.py`、`test-virtual-backpressure.py` | owner PID/starttime、真正 wait、ready/blocked × INT/TERM、管道占用/EAGAIN/阻塞写线程与 cleanup 证据方式 | 旧 virtual stdout 背压不等于 TLS 网络背压；blocked stdout 无末事件时不能声称读到了资源0 |
| `run-composite-qemu.py` | QEMU filter-dump 及 PCAP bytes/SHA归档方法 | 其 ECM data-PCAP 不是外链 TLS PCAP；ECM 不作为第一切片额外设备范围 |
| autorestore 两套 composition | 精确一次锚检查、生成 guest bytes/SHA、own-source 与旧来源 guard | 不调用旧 positive/journal verifier 给全新安全语义评分，也不无条件字符串全局替换地址 |

建议未来仅增加一个小的 security runner/guest 入口（名称待正式设计），导入上述稳定工具函数并拥有自己的网络编排和独立验收。外链/控制链与认证流程变化较大，不建议继续 monkeypatch `journal.main` 的整个状态机。需要生成片段时保留唯一锚计数和生成源码；旧 runner 保持不变，原 journal184和原信号/物理回归单独运行。当前不创建这些代码。

最小增量顺序：

1. **V：virtual IPv4**。真实 HID/CDC I/O，完成 TLS 路径、完整凭据/设备授权负向、本地 UID 和资源限额；固定 profile 适合先定位代理语义。
2. **P：physical IPv4**。同一产品代理接 USBFS HID/FTDI，验证真实 long URB、EOF/信号后 driver 与 journal 协作退休，以及重新授权后实际 I/O。默认关闭自动恢复，防止后台恢复掩盖代理/服务合作 cleanup 缺陷。
3. **N：Native IPv4**。正式 usbip/usbipd + 内核 socket 路径；HID 与 FTDI 可沿用现例分开运行，不能从 Rust 后端成功外推 Native。
4. **IPv6：V/P/N 对应正向与绕过/关键拒绝**。执行真实 IPv6 TLS、DEVLIST、IMPORT、HID/serial，而非只做端口探测。证书完整矩阵如共用唯一验证路径可集中在 V，必须记录源码共用依据及未重复的 backend 单元；若后端有独立认证分支就分别覆盖。

这是六个 backend×address-family 覆盖单元，不提前固定 guest/run 数量（Native旧例一次只测一种设备）。可以分切片交付，只将实际运行的单元标通过；在所有声明适用单元到齐前不写“全后端/双栈安全连接已验收”。

## 4. 每个正向单元的真实路径与抓包裁决

开始前固定 source/build/guest/proxy ELF 哈希、root policy、监听、路由、所有开放端口和证书元数据；捕获须在监听前开始，持续到释放/退出。保存每个过程的独立单调时间和序号；不同 guest 的时间不能直接当同一时钟，顺序以明确 barrier / request ID 对齐。

1. UID1000 调用正式发现接口；获得经身份过滤的真实 DEVLIST。随后通过 helper/真实 usbip 建立 IMPORT，核返回 receipt、VHCI USED、当前代际、设备描述符及 `usbhid`/`ftdi_sio` 或 `cdc_acm`。独立 DEVLIST 与两个 IMPORT 流都有自己的 TLS connection/operation 关联；同一 TLS 多路复用与否由真实设计决定，不能预先假定“一设备恰一TLS”。
2. 将 HID evdev 和串口 reader 先打开再发 ready；注入互不相同的 KEY1/KEY2/KEY3 与每轮带随机 nonce 的合成 RX/TX 数据。保存 host FTDI 发送/接收原件、client evdev/tty 读取 bytes、长度和 SHA；virtual 则对照实际 control feed 与内核读回。原35B模板可做短基线，再用**至少256KiB、分块、双向、唯一序列**的合成串口数据覆盖跨多条 TLS record 的长流；不把它当吞吐基准。虚拟/FTDI容量不同，按记录的流控逐块完成，不无界灌入。
3. 导入后保持 pending HID/serial IN，空闲超过所设握手/半包截止（例如合同为5秒则观察10秒），随后再次真实 I/O，证明 IMPORT 后同一业务会话仍有效。实际测试截止单列，不偷换为产品网络故障 SLA。
4. 关闭/故障后双方先保存 primary：client ports NULL/旧sysfs代际消失/自有sessions清除；server独占/连接/在途资源与后端状态。双方握手确认保存完成后才允许新连接，第三轮用新 receipt/新session 实际 I/O，避免 ready 或新会话遮蔽旧未清理。

**抓包证据必须形成闭环：**

- 外链两个 QEMU netdev 原 PCAP，全量包长，无静默 snaplen 截断；解析 TCP 重传/分段并重组流，标记缺洞/未完整覆盖。独立识别 TLS 握手与随后双向受保护记录，关联实际 tuple、connection ID、认证/导入事件和 I/O 窗口。TLS1.3外层 record legacy version 不等于实际协商版本，实际版本另核握手/库事件。
- 本地受控 USB/IP 边界的独立观测（优先 guest内限定端点PCAP）解析 `OP_REQ/REP_DEVLIST`、`OP_REQ/REP_IMPORT` 和同流 `CMD/RET_SUBMIT`、需要时 `CMD/RET_UNLINK`，证明代理确实处理了真实协议和数据。只把 synthetic 设备字节作为测试证据；产品日志仍不能记录原始 URB。
- 产品审计/FD/socket事实将本地流与外部 TLS 流映射，说明计数在哪一层；不要求 TLS 密文字节数等于 USB/IP 明文字节数。外链所有产品流必须被解释，禁止有 raw 后端旁路；控制NIC、loopback和第三方连接不能暗送 USB 数据。
- 在外链**重组后的应用方向流**检索本轮长nonce标记，并检查不存在有效明文 USB/IP 事务。单纯 `grep` PCAP 找不到短 magic 不是加密证明；随机密文偶含短头也不能直接判泄漏。无真实应用流量、TLS handshake-only、capture缺洞/提前停止均不能判PASS。
- 因果负对照：在有 pending URB/实际I/O时，仅精确中止/阻断对应外链 TLS flow，保持控制链和后端进程活着，旧业务必须停止并进入定义的cleanup；恢复必须经新认证连接。仅“套了TLS同时真实USB直连仍能工作”的假成功由此排除。
- 正向保密判定与负向注入分别按已记录的phase/tuple裁决。绕过/畸形测试主动发送的明文探针保留在完整PCAP并标明来源，不能删包伪造“全捕获无明文”；其通过条件是没有获准的USB/IP回复/设备副作用。ARP/NDP、TLS握手和网络元数据也不冒称都已加密。
- 无需把 TLS session key 写入归档证明路径；若后来选择 test-only解密验真，必须单列密钥导出安全/删除策略，不能默认为产品支持 key logging。

QEMU 外链 PCAP证明的是隔离仿真外接口，不是公网、VPN服务、真实网卡或生产网络部署安全；这个区别必须保留在报告标题/结论中。

## 5. 3240 绕过与本地 UID 抢占

### 5.1 两地址族的网络对照

- 从 client guest 外链分别对 server 所有外链IPv4/IPv6地址的3240发实际 TCP连接和DEVLIST/IMPORT；同时检查已知其它raw后端端口，不只改端口后盯3240。反向检查 client外链不能访问它的本地代理入口。
- 也探测 server控制NIC地址以及主机名的每个A/AAAA结果，明确是否允许DNS回退；IPv4规则不能代替IPv6规则。保存请求bytes（合成）、确切目的tuple/errno/截止、外链SYN/RST/ICMP或drop、backend计数/driver不变。
- 成功TLS的同family流作为路径可达正对照。阻断可为refused或策略drop；必须核实际listener与规则/counter，不仅用timeout猜测防火墙。若无raw listener，可判“绑定隔离拒绝”，不能虚称已经观察到防火墙drop。
- 若Native的usbipd仍全地址监听，则在启动它之前安装两family隔离规则，启动/关闭/异常路径均不得有开放窗口；规则失效时产品是否拒绝启动由接口契约决定。测试中若仅guest管理员手工规则保护，报告明确这是部署前提，不称产品自动配置已完成。

### 5.2 普通 UID 的真实数据入口负向

Loopback地址不是UID认证。现helper UDS的SO_PEERCRED和root-owned配置可验证管理请求，却不能自然鉴别随后连loopback TCP的进程；随机高端口、一连接后关闭、隐藏端口或“先调用管理API”都不够。

候选必须给出能与原生usbip自己建立TCP兼容的防护（例如真正受保护的网络隔离/内核规则，或严格绑定且不可挪用的数据能力）；本计划不预先批准某种实现。不能把TCP的peer IP冒充SO_PEERCRED，也不能只测试UID1001访问管理UDS失败。

实际负向至少在：①代理创建前预绑定/冒充本地端点；②合法会话已准备、原生工具尚未连接时抢先连接并发DEVLIST/IMPORT；③合法连接已占用时追加连接；④旧会话清理/端口复用后重放旧能力；⑤client及server本地raw后端入口，分别以UID1001执行。每次核UID/PID/starttime/实际tuple、拒绝原因、无越权上游/claim/VHCI副作用，并让原UID1000继续真实I/O或合法新连接，排除“攻击只是让整个服务坏掉”。若当前设计无法阻止抢入口，登记设计阻断，不降低为“仅可信本机用户”后宣称原威胁模型通过。

## 6. 凭据、设备和重复会话拒绝矩阵

每行保存实际fixture元数据、product机器错误/对端关闭、同一运行有效对照、相关backend/sysfs/会话前后事实；非零退出、EPIPE、握手timeout或`openssl verify`输出本身都不是正确拒绝原因。网络握手失败和USB/IP授权失败分别裁决，不要求TLS库暴露相同底层alert文字。

| 用例 | 必须发生的拒绝与不变量 |
| --- | --- |
| 没有client cert；错误CA/签名或坏DER | 服务端拒绝认证；没有授权成功、没有USB/IP后端转发/claim；之后有效对照可用 |
| server错误CA、错误SAN/hostname、用途不匹配 | 客户端拒绝真实TLS连接；不能静默跳校验或改走raw；真实服务身份对照成功 |
| client/server过期、尚未生效 | 两guest时间落在fixture宣称的区间，产品实际拒绝；不改变宿主时间 |
| 撤销 | 原凭据先成功；按正式机制更新CRL/denylist/信任集并使其生效；旧凭据新连接失败，新合法凭据成功。缓存ticket/恢复若启用也必须重测；存量会话处理按明示契约，不能从新握手失败推论已撤销旧USBFS FD |
| 有效身份但不在设备规则内 | 真实存在且可由另一获准身份使用的设备被拒；DEVLIST不泄漏不允许项，直接IMPORT同样拒；不是只测不存在BusID |
| 合法身份+旧设备代际/错会话绑定 | 重插/重授权或会话能力绑定发生变化后旧请求拒绝；若首切片没实现代际能力，标未覆盖，不能补造结果 |
| 同设备第二IMPORT/两个合法身份同时争用 | barrier并发后最多一个真实owner/claim/VHCI实例；loser明确busy/unauthorized，winner继续真实I/O；结束后合法重连可成功 |
| 重复/重放请求或已消费能力 | 对同operation/session/device/principal绑定作实际重复、跨身份/跨设备挪用，不能生成第二owner；如接口允许幂等应返回同操作结果而非新会话。正常DEVLIST重复不应该被混成攻击 |
| 连接内重复IMPORT、握手半包/超大帧、认证洪泛 | 有界拒绝、没有提前backend副作用；资源/任务/FD/内存上限可观察，释放后有效连接继续可用。fuzz另行执行，有限负例不能称完整抗DoS |
| 密钥/秘密泄露 | 日志/错误/诊断/归档不含预置私钥或会话secret标记；只保留公钥指纹及受限错误信息 |

TLS1.3 0-RTT存在重放边界，因此第一切片建议禁用；“同一个client证书可以建立第二次正常TLS连接”本身不是重复会话漏洞。抓到旧密文并发到新TCP连接被拒，也不能单独证明应用operation/设备能力的防重放语义。

## 7. EOF、信号、背压与后端清理

以真实pending IN和既有I/O为前提，每次仅改变一个故障轴并保存注入前状态；以下是必要覆盖类别，可在源码共用证据充分时把网络泵边界集中在virtual，然后在Native/physical验证各自真实资源收尾。

| 轴 | 注入与采证 | 不能冒充的结论 |
| --- | --- | --- |
| 正常结束 | 客户端正常detach、TLS有序结束、local sockets关闭、task join/会话释放；之后第三轮I/O | `close_notify`是传输有序结束，不是USB清理本身 |
| TLS异常EOF / RST / 半关闭 | 精确外链tuple，保留close_notify有无/错误分类；本地只关闭读或写一侧按协议合同处理，取消另一泵并join | TCP EOF无close_notify不能当“所有数据已完整交付”；丢future不等于终止真实USB工作 |
| INT/TERM | 两代理分别ready与output-blocked状态，准确PID/starttime、单次真实wait、exit/首错；必要时覆盖握手中/IMPORT后 | 旧backend SIGTERM PASS不自动证明新proxy的cleanup |
| stdout背压 | 实际pipe容量/占用4096或实测值、EAGAIN/blocked writer、未在注入后偷偷读空；控制/采证走另一渠道 | 无stopped输出时只认socket/sysfs独立观察，不能声称读到了末快照 |
| TLS网络背压 | 有效TLS对端停止读/耗尽明确窗口，持续足够合成数据使实际写阻塞、buffer/FD/任务有界；此后INT/TERM或断网，等待全部泵退出 | 小35B写入或仅stdout满不能证明网络背压；吞掉未发送partial字节不能报完整传输 |
| 代理死亡/重启 | 区分proxy-only退出与backend仍活着；老业务结束后新认证、新映射、新代际实际I/O | 不将SIGKILL等同合作停止；不默认启用USBFS自动恢复来遮蔽旧连接仍存活 |

客户端 primary：完整VHCI行/端口NULL、旧USB sysfs receipt消失、没有残留本地连接、受管会话身份和释放原因。服务端 primary按backend分开：

- **Native**：准确内核持有连接消失、`usbip_status`回到可用；后续通过合法unexport才核设备级`usb`与原接口driver，不能要求仅detach就已经unexport。
- **virtual**：原两profile真实leased/config/pending/queued资源0，服务活着时能再次导入；观察不到就记unknown。
- **physical**：会话EOF后实际在途回收/claim释放、原cfg/fullIF/alt/driver及同代际，服务停机时再核合作Finish和journal退休；backend活着时grant记录仍存在可能是正确状态，不要求连接断开即退休。

所有新窗口需要实际deadline与起点，不能写无限等待。超时保留first failure、注入/partial状态和双方原件，紧急清理另记，不能用最终guest exit0改写失败。旧自恢复/默认journal184回归按其原契约独立保留。

## 8. 采证清单和可复核 verdict

建议每run复用现有manifest格式增加如下字段，不另建数据库/平台：

- `source`: exact HEAD、clean状态、完整受影响Rust/锁文件/旧依赖harness/新源逐文件bytes/SHA，build命令/features/exit；build→initramfs→guest实际执行ELF→after链；系统工具/kernel/modules/QEMU/initramfs/生成guest SHA。
- `topology`: backend、address_family、netdev与地址/路由、外链/控制链PCAP位置、监听与实际防火墙规则/计数、cert public元数据和有效guest时钟、策略版本/摘要、候选明确安全前提。
- `operations`: 发现/IMPORT/会话 ID，principal、device/current-generation binding，local/TLS/backend tuple、PID/starttime、认证阶段、拒绝类别、逐阶段单调时间与barrier。
- `usb`: server原cfg/fullIF/alt/driver/receipt；client VHCI/driver/receipt；原evdev/合成serial bytes及预期、length/hash；pending状态、第三周期实际I/O。
- `failures`: 实际注入对象/方法、真实wait与退出、端口/FD/task/缓冲前后；first failure和后续cleanup分别保留；每一负例证明没有相应授权/设备副作用且合法对照继续成功。
- `transport`: raw外链PCAP、必要内链PCAP、完整重组/截断判定、TLS观测、明文事务与flow对应、旁路探测原件；packet/key/产品日志边界分开。
- `verdict`: 精确scenario×backend×family的PASS/FAIL/未执行，assertion数与实际USB轮数分开。独立review直接解析原件，不调用runner.verify；原失败和修订来源保留，不将后续docs commit重新归属运行。

## 9. 当前模型不能证明的部分

- 两个模拟guest不是两台真实Ubuntu/真实硬件；不证明G1/G2、S6、aarch64内核USB互操作、Windows、恶意固件或任意设备/ISO/摄像头/音频。
- 双guest的独立认证客户端进程可证明服务端会话独占；它不是两个独立远端内核同时attach的完整三机争用。可先采用“第一真实VHCI + 第二真实TLS/IMPORT对手”，完整三机按后续范围单列。
- 私有socketpair不代表公网路由、防火墙设备、DNS基础设施、真实链路丢包/MTU/黑洞、长稳态/性能；主动RST不等于黑洞超时。第一切片如用静态hosts，只覆盖该解析fixture，不能宣布DNS多地址策略全部通过。
- 外链无明文是所捕获路径的事实，不能证明没有其它未知网卡/side-channel/错误部署，也不证明密码库无漏洞；成熟库固定版本、依赖审计/fuzz和部署检查仍需独立证据。
- CRL/allowlist新连接拒绝不等于撤销已交付USBFS FD；可信服务/OS root边界仍存在。证书轮换、已建立会话撤销、ticket恢复未定义就必须未完成。
- 小规模认证负例/资源上界测试不是抗DDoS、全部协议fuzz或完整九状态持久业务状态机验收；S2存储保护及保守不自动续写要求保持。
- 当前文件只有计划：没有新增产品CLI、证书解析器、TLS库、代理、防火墙实现或成功运行证据。不得把计划条目计入验收表PASS。

## 10. 依据

仓库依据（均按上述commit读取）：`docs/requirements.md` §4.4/4.5/安全测试/Phase3；`docs/security.md` §5及S1–S6；`docs/roadmap.md` §6 G3；`docs/architecture.md`；`docs/testing.md` §4/6；`docs/research/native-qemu-transport-abort.md`；前表各runner/guest与既有signal/backpressure脚本。用户技能偏好明确不因技术设计skill自动创建额外组件skills/框架，本轮仅输出该/tmp计划。

外部一手资料仅用于协议边界，2026-10-23读取，不声称已选择实现库：

- [Linux v6.8 USB/IP protocol](https://www.kernel.org/doc/html/v6.8/usb/usbip_protocol.html)：DEVLIST独立连接；IMPORT成功后该连接继续承载SUBMIT/UNLINK及回复。
- [RFC8446 §8](https://www.rfc-editor.org/rfc/rfc8446.html#section-8)：0-RTT重放边界；[§6.1](https://www.rfc-editor.org/rfc/rfc8446.html#section-6.1)：有序关闭与未经close_notify的传输结束不可混同。本文建议第一切片禁用0-RTT，并将异常EOF与业务cleanup分别验收。
- [Linux unix(7)：SO_PEERCRED](https://man7.org/linux/man-pages/man7/unix.7.html)：本地UNIX socket的内核对端身份是管理边界依据；本计划要求单独验证后续TCP数据入口，不能从UDS授权外推。
- [QEMU invocation：filter-dump](https://www.qemu.org/docs/master/system/invocation.html)：netdev可输出libpcap并受maxlen影响。该网页是当前文档说明，实际命令/支持项必须由将来运行的QEMU版本核定；本轮现有composite harness已采用filter-dump，未据master网页假定新增功能存在。
