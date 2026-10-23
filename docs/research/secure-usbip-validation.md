# 安全 USB/IP 产品接入与双栈 QEMU 验收

2026-10-23。实际 `secure-server`、`secure-discover` 和 `secure-attach` 已接通身份认证、设备授权与完整 USB/IP 数据路径。同一干净提交 `b83c931afb1789ae178bf49c6402f996fc9fb3c5` 的 **virtual / USBFS / Native × IPv4 / IPv6 六场景全部 PASS，12 个 guest 退出 0**。本轮验证的是两台隔离 QEMU 中的真实内核枚举、合成设备 I/O 和合作清理；真实硬件 G1/G2、ISO、跨配置/崩溃恢复和完整 Phase3 会话审计仍未完成。

设计见[产品接入契约](secure-usbip-integration.md)，使用见[安全连接指南](../secure-guide.md)。最终原件集中于[证据清单](../evidence/secure-usbip-final-b83c931-20261023/manifest.json)。两项独立复核已分别通过：生命周期492个审计谓词、安全与PCAP350个谓词；这些是证据检查数，不是USB测试case数，历史FAIL保留。

## 实现与信任边界

产品实现始于 `80a21f1`。Rust virtual/USBFS 复用同一设备 engine；Native 服务使用 helper 持有的 export/stub 能力，URB 由内核执行。每个连接强制 TLS1.3、mTLS 和 ALPN `rustusb-usbip/1`；标准 PKI 验证后，以叶证书 DER SHA256 映射 principal 和精确稳定设备 key。配置启动后固定，轮换需合作停止/重启。

安全端口先收有界 key 前导，再处理标准 USB/IP 帧。DEVLIST 和 IMPORT 使用分别认证的连接；目录映射与描述符来自同一授权快照。IMPORT 在对应设备锁/slot 中同时核对 key、Bus ID、principal ACL 和占用状态，之后才 claim。成功 IMPORT 回复及全部后续 URB 使用同一 TLS 流，不转接明文 TCP 后端。软件测试覆盖未经授权设备隐藏、猜 Bus ID、错误 key、畸形前导和紧随 IMPORT 的 URB 不被预读丢弃。

客户端 helper 创建匿名 AF_UNIX 端点，将自有内核端交给 VHCI、relay 端交给专用 broker；Native 服务端也使用原 export 的固定属性 FD 和匿名通道，不启动 usbipd。root helper 以控制 UDS 的 SO_PEERCRED 和 root policy 校验本机角色，不盲信自报的远端 principal。专用 broker UID 与旧交互 UID、可导出设备池分离；同一 broker UID 下的进程属于同一信任域。

Native 发布描述符是导出前的注册快照，不能冒充导出后的当前 sysfs 配置。通道在阻塞写实际结束前保留 FD 和变更锁；合作停止按自有 socket/receipt 关闭并核验内核现实。未决状态不会因 PID 退出或内存清空自动变成“已释放”。旧明文命令仍受原有外部可信网络契约约束；新安全命令不会自动开启它们。

## 冻结来源与软件验证

六次运行各自从干净提交执行 `cargo build --workspace --all-features --release --locked`，125 个选定输入、HEAD、工作树清洁度和实际产物四项 guard 均为 true；构建没有跳过。每组保存实际运行 broker/helper 的 `/proc` executable 身份及 SHA，不能只凭打包路径推断实际进程。

| 项目 | 六场景共同来源 |
| --- | --- |
| HEAD | `b83c931afb1789ae178bf49c6402f996fc9fb3c5` |
| CLI ELF SHA256 | `7dddc2861f92b228113a627f8ac2843110bc199258b26868e68146fb67e3e31f` |
| helper ELF SHA256 | `72415f416b10d6df508de4dc0c3f36935b8abc6450444d569260b82afe6bb9ea` |
| guest 内核 | `6.8.0-106-generic`，x86_64 |
| 内核 image SHA256 | `ac8edf636f7c5fa494195c644c1dbbdbb98b1e7893bbaa6af86bc1e0e2a118f8` |
| 内核 config SHA256 | `a83db1b3b61ca8e4b6c76e2d247fcd9efce7aa8a78f8a7948a5c3b5169c788a4` |

最新完整 `scripts/check.sh` 实际 exit0：default159、all-features233、vendor20，共 **412 次测试执行、35 条 result、0 失败/忽略**；fmt、两组 Clippy/release 和严格 rustdoc 通过。[初始化修复归档](../evidence/secure-usbip-native-initialization-fix-20261023/manifest.json)保留123项输入及前后核验。该门禁发生于基于 `cbcab68` 的未提交候选；123项输入逐 Git blob 匹配后续 `f9558fc`/`b83c931`，不改称 clean 提交运行。最终六组 ELF 与该门禁 all-features release 产物相同。

[精确 f9558fc CI39953971059](https://github.com/tiansongyu/usbip-client/actions/runs/39953971059)在 Ubuntu22.04/24.04 × x86_64/aarch64 四原生平台均 completed/success；每格实际412次/35条 result、0失败/忽略，原两项背压与四项本地信号场景通过。CI 输入采证范围与本地123项、QEMU125项不同；CI 不执行这六组 USB QEMU，也不证明 ARM USB 内核或硬件兼容。后续 b83c931 的 CI 发布状态另记，不把未重解析的日志复制成新的逐项计数。

早期软件第三、第四轮各396次（151/225/20），121项输入分别匹配 `80a21f1` 和 `18817cd`；Native-only secure-transport CLI 另有6项通过，不能重复加进396。它们见[初始软件](../evidence/secure-usbip-software-20261023/manifest.json)及[准备/诊断](../evidence/secure-usbip-integration-preparation-20261023/manifest.json)。两份早期输入清单保留 `head:056341c` 原字段；版本归属由完整输入 SHA/Git blob 说明，不重写历史元数据。精确41e2c67的[CI39950528568](https://github.com/tiansongyu/usbip-client/actions/runs/39950528568)四平台每格396次，仅归属该旧版本。其审查器首稿误把 `anon_pipe_write` 当成异常，初稿和按固定 harness 规则修订稿均保留。

## 六场景实际结果

所有场景使用独立外链 NIC；宿主控制、证据输出、内核控制台分别走不同串口。server/client 各有独立 initramfs，只包含本端私钥；CA 私钥仅留在宿主临时目录，公开证书可归档。两端实际检查 CRNG/RTC，不跳过证书时间验证。broker 为 UID2000、旧交互用户为 UID1000、helper 为 root；policy 始终 `trusted_network:false`。

| 服务端与实际地址族 | 终态与原件 | 设备与两个完整周期 | server 外链包数 / TLS 单向流数 |
| --- | --- | --- | --- |
| virtual / IPv4 | [PASS，双 guest0](../evidence/secure-usbip-final-b83c931-20261023/virtual-ipv4/summary.json) | 固定 HID + 双接口 CDC | 524 / 26 |
| virtual / IPv6 | [PASS，双 guest0](../evidence/secure-usbip-final-b83c931-20261023/virtual-ipv6/summary.json) | 固定 HID + 双接口 CDC | 530 / 26 |
| USBFS / IPv4 | [PASS，双 guest0](../evidence/secure-usbip-final-b83c931-20261023/usbfs-ipv4/summary.json) | QEMU HID + FTDI | 496 / 26 |
| USBFS / IPv6 | [PASS，双 guest0](../evidence/secure-usbip-final-b83c931-20261023/usbfs-ipv6/summary.json) | QEMU HID + FTDI | 474 / 26 |
| Native / IPv4 | [PASS，双 guest0](../evidence/secure-usbip-final-b83c931-20261023/native-ipv4/summary.json) | QEMU HID + FTDI | 491 / 26 |
| Native / IPv6 | [PASS，双 guest0](../evidence/secure-usbip-final-b83c931-20261023/native-ipv6/summary.json) | QEMU HID + FTDI | 490 / 26 |

这里的六场景是六次独立运行，每次两台 guest。26条方向流为13条 TCP 连接，不是26个 USB 用例；packet、断言、轮询、文件及软件测试数量均不增加设备覆盖范围。

每次运行两个周期均取得实际 VHCI 本地设备、驱动和接口；HID input_event 包含 code2/3 的按下与释放，serial 每周期各方向精确35字节公开 fixture。virtual CDC 另核35字节 echo；USBFS/Native 的 FTDI host 端实际收到两个周期合计70字节 TX。FTDI 不是 CDC，不能据此声称物理 CDC 通用支持。

占用期间第二个独立 broker 进程因授权目录中设备不可用而拒绝；持久资源和内核状态不增。这是同 guest 的目录占用拒绝，不是第三台 guest、直接 IMPORT busy 或首次并发竞速测试。client UID1000 的新 helper import 动作被拒绝，前后资源相同；USBFS/Native server 端也检查非 broker 动作拒绝及完整原基线不变。坏证书、unmapped pin、未授权设备和猜 Bus ID 的负向属于实际 TLS 软件测试，不能称本 QEMU 矩阵逐一重跑过。

每周期两个 attach 进程实际 SIGTERM/wait0；核对 disconnected/cleanup_verified、VHCI NULL、旧设备节点消失，以及 client Native `records:[]`、`exports:[]`。重新发现目录恢复原值，第二周期再次真实导入和 I/O。最后服务端 SIGTERM/wait0；USBFS/Native 完整原 driver/configuration/interface/alternate 基线恢复。Native 另逐阶段记录 exported 的空配置、held 的 USED、released 的 AVAILABLE；stub 先关闭，再退 export，最终 server records/exports 为空。USBFS本次保留的是最终physical journal原件，结合实际snapshot、Finish及原driver恢复判断；不声称独立逐项核对过中间grant的全部immutable字段。没有把发布快照或 guest poweroff 充当当前配置与合作清理。

## 外链加密与绕过边界

每组保存完整 server/client 外链 PCAP。runner 对 server capture 的每条 TCP 方向流按 sequence 重组，拒绝缺口、矛盾重传和截断；全部应用字节形成有界 TLS records，检查 ServerHello 选择 TLS1.3、客户端 ALPN offer 和加密 application records。对完整字节流检索 DEVLIST/IMPORT、实际 device ID 的 URB 头及公开 I/O fixture 明文特征，未发现这些指定签名。没有记录 session keys 或解密流量；不能把签名缺失等同任意密码学或数据泄露证明。

IPv4/IPv6 的直连3240均真实拒绝且抓到 RST、没有该端口 payload；IPv6 三场景先完成真实 IPv6 TLS 与 USB I/O，不靠“拒绝 IPv6 3240”冒充 IPv6 支持。隔离 guest 中未启动 legacy usbipd，也没有明文本地 TCP bridge；已有外部 usbipd、生产防火墙、VPN/WAN 和 DNS 多地址部署仍需独立检查。

IPv4仅允许 TCP；IPv6只允许 TCP、严格分类的 RS/NS/NA 和实际 MLDv2 Report。最多一个256字节 Hop-by-Hop 头，只接受 padding/Router Alert，解析后 TCP 仍进入相同完整检查；fragment、其它扩展、未知协议或未计账控制报文拒绝。ICMPv6核 checksum、hop limit、选项/记录边界并按类型计数。此规则是隔离测试链路的保守 classifier，不是通用 IPv6 栈。

独立[生命周期复核](../evidence/secure-usbip-final-b83c931-20261023/reviews/rustusb-secure-six-lifecycle-raw-review-20261023.json)不调用runner验证器，逐源码Gitblob、实际ELF、原始串口base64、sysfs/record及精确I/O核对；独立[安全/PCAP复核](../evidence/secure-usbip-final-b83c931-20261023/reviews/rustusb-secure-six-independent-review-20261023.json)以另一解析器重组两端捕获、验证公开证书链/用途/名称/有效期、实际UID/caps、配置pin与直连RST。两者均无未通过的最终断言，未改原件或重跑QEMU。审查器初稿缺少alias或误把virtual echo套给FTDI的错误另存，不改判产品运行。

抓包不解密TLS，ClientHello的ALPN offer和ServerHello的TLS1.3选择可见；加密握手内的ALPN协商和证书检验仍结合固定代码、软件测试、配置及真实连接结果，不声称从密文读取。QEMU配置中的同一合法principal获授权两设备，不能把它充当未授权网络证书/设备负例；相应实际TLS/ACL软件负向和未完成的完整G3矩阵分开记账。未扫描任意生产端口或验证生产防火墙。

## 四次历史失败与修复

四次均为 virtual/IPv4。原 clean HEAD、summary、日志与抓包保持原样；guest进程exit0、来源guard为真均不能覆盖整体FAIL。

| 运行与 clean HEAD | 实际失败 | 采证与原件 |
| --- | --- | --- |
| 首次 `41e2c67a71a0257cd1e0aec109477b93869e9cc0` | 首周期 serial attach 提前退出；未完成首轮 I/O | trap遇 `base64: not found` 后PID1退出，两guest真实kernel panic，导出不完整；[142份已保存原件](../evidence/secure-usbip-first-failure-41e2c67-20261023/manifest.json)不等于完整采集 |
| 第二次 `5f1e25b06fa38bda7c8f4aa1fcfe04d7e3f272a1` | 首周期 serial attach 失败；cleanup错误遮住operation错误 | 编码修复后server12/client26文件完整，无panic；尚无失败journal；[182份归档](../evidence/secure-usbip-second-failure-5f1e25b-20261023/manifest.json) |
| 第三次 `18817cd3b0eab3b947aa3086f80171a38d322527` | 首周期真实I/O、关闭和重新发现成功，第二周期HID import报I/O(NotFound)，整体FAIL | server21/client51文件，Native failure journal725B保留新intent；[216份归档](../evidence/secure-usbip-third-failure-18817cd-20261023/manifest.json) |
| 第四次 `f9558fcd7c0c29e3df1b13bbd109f7d471806c9f` | 两guest确实PASS，两周期I/O/TERM/reuse/最终清理通过；host PCAP classifier把实际HBH→MLDv2当未知协议，整体FAIL | [完整原件与修订准备](../evidence/secure-usbip-capture-failure-f9558fc-20261023/manifest.json)；后验parser成功不改原summary |

首失败暴露实际 payload 没有 base64，sh-n/源码预审未发现运行依赖缺口。修复复制独立 encoder及动态库，实际检查34个必需BusyBox applet/链接、10个ELF及依赖；先在 `/run` 完成固定快照和编码，成功才输出artifact块，单项失败继续采证并保留原状态。若poweroff返回，PID1等待host有界终止，不再直接退出造成panic。隔离encoder故障夹具和临时payload预检不是额外USB验收。

第二次后保留operation与cleanup两份错误，cleanup仍总await；第三次新增固定Native/physical journal的有界只读失败快照，区分absent、不可读、超限等状态，不调用helper或改变USB。第三次首周期实际KEY_1/双向35B/echo、两个wait0、NULL/Gone、空journal和目录复原成立，但第二次实际import失败；不能把“可重新发现”写成“已再次工作”。

固定Linux v6.8的源码审计证明 VHCI 在 SET_ADDRESS 阶段先置 USED，后续 `device_add` 才发布完整sysfs。旧观察对尚未出现的属性立即报NotFound。第三次错误和持久Intent与此时序相容，但没有逐syscall tracing，不能指认最早缺失的确切open/read/canonicalize，也不能断言前三次首因完全相同。`f9558fc` 修复只把明确初始化未就绪置为有截止的Pending，固定首次代际；权限、格式、身份或已观察代际消失继续拒绝，保留变更锁/阻塞任务的实际生命周期。

第四次两份原PCAP各533包：8个HBH为 `3a00050200000100`，携RouterAlert0后接ICMPv6 type143，另5个RS/NS/NA；IPv4仅TCP。修订classifier后，独立后验读取保持summary和PCAP哈希不变；38个新构造用例加14个原parser/artifact/OpenSSL回归通过，覆盖extension-wrapped TCP仍拒明文、畸形/超限/未知/fragment。`b83c931`随后重新实际执行完整六场景，最终PASS归属这些新运行。

## 剩余验收与历史发布归属

本轮不证明真实硬件G1/G2、ISO、任意Composite/alternate、跨配置与设备换代恢复、SIGKILL/断电/黑洞网络、长期负载/性能、证书热轮换/吊销体系、完整会话审计/恢复或生产服务隔离。Phase3获得真实安全数据链子步骤证据，整体阶段仍未完成；Windows USB、TUI/GUI、安装与后续阶段保持原要求。

英文/中文README、Native指南、静态展示站和全局readme-site skill是在此前轮次交付的产品入口；展示站不是Phase5 GUI。本轮之前的304次TLS基础软件、clean24a424b无NIC/无TLS匿名内核机制、六个线上资源字节和1280/375浏览器检查，都保留原时点，不升级为本次安全六矩阵证据。本轮入口文档与网站同步属于后续交付检查，不能用历史Pages success推断新页面已发布。
