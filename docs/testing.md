# 测试手册与证据要求

本手册定义 RustUSB 的自动检查、双 Ubuntu 实机验收、异常、安全和性能测试。命令面向稳定 Rust 及 Linux；本文本身不是已执行测试结果。**硬件与新增端到端场景均需实际执行后登记，不能以编译成功推断 USB 直通成功。** 最终状态以仓库工作记录中的命令、退出码和原始证据为准。

## 1. 环境与安全前提

软件目标为 Ubuntu 22.04 / 24.04、x86_64 / aarch64，实际内核、发行版工具包和模块可用性须逐台检查。容器通常没有加载宿主内核模块和接入真实 USB 的权限，容器内成功测试不等于目标硬件通过。先取得两端管理员及设备所有者授权，只用隔离测试设备与测试数据。Phase 1 首次实机建议选 USB 串口，或专用测试 HID；存储导出默认拒绝，不用生产 U 盘规避限制。

使用有控制台恢复途径的测试机；不要导出当前唯一的键盘、鼠标、USB 网卡、系统盘、认证 token 或承担远程登录网络的设备。安装系统软件、加载模块、防火墙和服务操作由管理员执行；CLI / TUI 不因此改为 root。抓包可能含 USB 业务数据，证据文件应限制权限、脱敏并在验证后按保留策略清理。

## 2. 可自动执行的构建检查

在仓库根目录、已安装兼容稳定 Rust 且依赖可获取的环境执行：

`scripts/check.sh`还运行固定vendor/nusb的14项纯内存library测试，构建产物放在根target子目录；它们覆盖raw Control错误/partial/长度边界，不枚举或打开USB设备，并在四个平台CI中执行。

```bash
rustc --version
cargo --version
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo build --workspace --release
```

`scripts/check.sh` 另运行 all-features Clippy、test、release 与严格 rustdoc，覆盖可选 `rust-backend` CLI。默认 CLI 保持 Native；CI 最后使用 all-features 产物在普通 UID/回环临时端口执行一次虚拟服务启动、stdin shutdown 和释放 smoke，不接触宿主 USB。网络 wire 与 QEMU 实际功能仍须分别验证，见[设计](research/rust-virtual-backend-design.md)。

虚拟用户态路径入口：`python3 scripts/qemu/run-userspace-qemu.py --evidence /tmp/rustusb-virtual-test`。它使用新服务端和独立std wireprobe，QEMU不添加USB设备，客户端实际VHCI验证两个profile的evdev/ttyACM两周期；冻结cfeffd3结果见[完整报告](research/userspace-qemu.md)。普通UID本机stdout回压回归为`python3 scripts/test-virtual-backpressure.py`，验证持有租约时不drain输出仍能SIGINT或5秒截止关闭。它们不能代替物理验收。

真实USBFS adapter的QEMU入口为`python3 scripts/qemu/run-physical-qemu.py --evidence /tmp/rustusb-physical-test`，加`--hot-unplug`验证拔出HID时FTDI同会话继续I/O、同端口重插不继承授权。服务端使用QEMU创建的HID/FTDI，普通UID通过helper接收FD，节点保持root0600；客户端实际VHCI/usbhid/ftdi_sio验证。它默认locked/all-features构建，独立std probe检查0/1/15B、配置pipeline、取消、EOF、busy，结果与全部开发FAIL见[专项报告](research/physical-qemu.md)。不能把此路径称为真实硬件G1/G2或标准CDC物理设备测试。

新服务parser fuzz可先执行`python3 scripts/seed-server-fuzz.py`，再运行`cargo +nightly fuzz run server_frame -- -max_len=65584 -max_total_time=30 -timeout=5`；[最终冻结记录](evidence/server-fuzz-final-20261022.json)包含nightly、cargo-fuzz、精确source/lock哈希与exit0，[原始输出](evidence/server-fuzz-final-20261022.txt)保留3,615,249次/31秒无观测崩溃。该目标不含设备handler或完整网络调度，不以短时fuzz表示全部安全或USB性能。

每条命令分别保存退出码。`Cargo.lock` 必须纳入仓库，CI 可追加 `--locked` 固定解析。若尚无 workspace 或某阶段代码未落地，相应检查记录未执行 / 失败并说明原因，不创建空测试掩盖缺口。交叉 `cargo check --target` 只能验证该 target 的编译，不能证明 aarch64 实机运行或 USB 功能。

| 自动测试层 | 必须覆盖 | 成功证明范围 |
| --- | --- | --- |
| 设备发现 | sysfs fixture、缺失 / 非 UTF-8 字段、Composite、接口 / 端点、速度、驱动、重枚举、重复序列号、无序列号 | 元数据解析与身份规则；不证明重定向 |
| 协议编解码 | 网络大端序、版本 / 状态、定长字符串、DEVLIST、IMPORT、截断、非法接口数、超大长度、溢出、半包和超时 | 本项目实际处理消息；不覆盖 Native 内核 URB 解析 |
| 核心模型 | 设备 ID、授权、独占、状态转移、取消、错误分类、重复操作、启动协调 | 模型行为；须另做系统故障测试 |
| 配置 / helper | schema、未知动作、root ownership、错误权限、符号链接、输入长度、UID / 动作 / 设备策略、固定可执行参数 | 授权与注入防护；mock 命令测试不证明内核副作用 |
| 进程 / IPC | 部分读写、超时、子进程退出 / 卡死、断开客户端、最大报文、并发上限、输出截断 | 资源边界与错误传播 |
| 存储拒绝 | 类码 08、含存储的 Composite、多 LUN / 分区、mount / swap / holders、未知接口或权限不足 | fail-closed；不证明存储安全写入 |
| 日志 | 预置秘密标记、控制字符、超长错误、诊断脱敏 | 不泄露测试标记；不能据此保证所有秘密均被覆盖 |

所有依赖真实 sysfs、root、内核模块、网络注入或硬件的测试应标清条件并与默认无特权测试分离，跳过时输出原因。CI 必须看得出跑了多少测试、哪些被跳过；不能让 `#[ignore]` 隐藏必需验收。

## 3. 双 Ubuntu 最小验收（G1，待实测）

### 3.1 拓扑与证据

服务器 S 插入测试 USB；客户端 C 使用 `vhci_hcd`。两端通过管理员已建立的可信 VPN / 受管隧道连接；记录两端 VPN 地址和接口，外网直接 3240 必须被防火墙拒绝。Phase 1 没有内置 TLS，不能用普通 LAN 连接截图证明安全传输。

在 S 和 C 分别记录以下只读信息，存放到本次测试记录；序列号公开前脱敏：

```bash
cat /etc/os-release
uname -r
uname -m
usbip version
usbip help
lsusb
lsusb -t
```

如果目标发行版工具不支持某命令，保留实际错误并记录包版本，不静默替换为推测值。管理员安装匹配运行内核的原生 USB/IP 工具和模块；用 `rustusb doctor` 核对实际可用性，Secure Boot、内核配置、虚拟机 USB passthrough 和权限限制均写入记录。记录服务 / helper 的有效配置及 socket 权限，但不采集凭据内容。

### 3.2 启动前检查

1. 在服务器确认测试设备、完整接口、当前驱动、是否已导出及是否为关键输入 / 网络设备。
2. 由管理员按实现的配置参考部署 root helper 和最小 per-device allowlist，只允许测试 UID、测试动作和明确设备身份；不使用通配“允许全部”。
3. 在开启 `usbipd` 之前限制其监听网络的访问：仅允许指定 VPN 接口和客户端；同时检查 IPv4 / IPv6。服务单元只由管理员管理。
4. 确认客户端 helper 仅允许指定导入目标和会话动作，socket 与目录不可被普通账户替换。
5. 以普通账户执行 `rustusb doctor`、`rustusb --json devices`、`rustusb status`，保存输出；不可满足的条件必须停止变更操作。

下节 CLI 为目标接口，实施时以当前 `rustusb --help` 和各子命令帮助为准；如命令调整，必须同步本文后再记录结果。不得把命令示例写成已执行。

### 3.3 执行与判定

1. S 执行 `rustusb --json devices`，确认设备身份及当前 Bus ID / 代际，记录返回设备 ID。
2. S 执行 `rustusb export <device-id>`。用 `rustusb status` 与 `usbip list -l` 对照导出状态；仅显示在列表中尚未通过验收。
3. C 执行 `rustusb discover <S的VPN主机名或地址>`，对照设备身份、接口和可用状态；设备描述是远端输入，不能据此放宽授权。
4. C 执行 `rustusb attach <S的VPN主机名或地址> <bus-id>`，其中 Bus ID 必须对应刚发现并授权的远端设备。记录导入 port、`rustusb sessions` 和 `usbip port`，观察内核日志（按系统权限使用 `journalctl -k`）。当前 Native CLI 使用远端 Bus ID 寻址，不能把它当作永久身份。
5. C 通过 `lsusb`、`lsusb -t` 和具体原生驱动节点确认**客户端内核枚举真实设备**。记录驱动绑定及错误；只有 attach 命令退出 0 不够。
6. 执行实际功能：专用串口连可控 loopback / 测试端，收发带序号和校验的数据；HID 用测试输入工具验证受控事件。保存操作、数据校验、时长和错误数，不采集真实用户键盘输入。
7. C 停止设备应用 I/O 后执行 `rustusb detach <port>`，确认本工具端口已释放、设备节点消失、重复 detach 安全。
8. S 执行 `rustusb unexport <device-id>`，确认原驱动 / 可用状态符合设计，重复 unexport 安全且未影响其他设备；重新进行一次 export / attach / 功能 / 清理循环。

**通过条件：** 两台真实 Ubuntu、至少一类真实设备、客户端内核枚举、原生驱动功能、正常释放与重新连接均有证据，且软件 gate 通过。设备发现、远端列表、mock、QEMU 或同机 loopback 不单独满足 G1。当前未执行本节时，Phase 1 硬件验收保持未完成。

## 4. 协议互操作与 QEMU

用户已要求使用 QEMU 仿真。除上游模拟设备研究外，必须建立**本项目双来宾 Native 全链路** G1-Q：两个隔离来宾运行本项目构建产物和原生内核模块，服务端将可控虚拟 USB 设备绑定 usbip_host，客户端经实际 helper 导入到 VHCI。测试完整的 export / discover / attach / 功能 I/O / detach / unexport，同时验证 UID 和设备策略拒绝。来宾 root helper 只在隔离来宾内运行，不在开发宿主绑定任何真实设备。

G1-Q 入口为 `scripts/qemu/run-native-qemu.py` 和 `scripts/qemu/guest-init.sh`：两个来宾使用独立虚拟网卡与 QEMU socket 网络，地址为 192.0.2.1 / 192.0.2.2，无宿主 USB passthrough。默认服务端使用 QEMU 虚拟键盘，通过 QMP 注入按键，客户端 evdev 验证 KEY_1；释放后再连一次确认恢复。`--device ftdi-serial` 使用FT232BM厂商类Bulk串口，核对ftdi_sio与双向完整序号字节；它不提供CDC ACM证据。测试记录 QEMU 版本、来宾内核 / initramfs 来源、模块、复制的二进制及库、两个来宾独立日志、退出状态和持久证据位置。

互操作场景使用 `--scenario managed|linux-client|linux-server`，各支持上述两种设备；`--scenario reconnect` 用HID验证外部释放后直接重连和同端口新代际拒绝控制。两端另有helper启动前/后和未授权UID的真实doctor断言，核对schema1、版本、有效sysfs、root peer和状态分类；doctor退出0不能代替授权判断。完整命令、源码依据、负向所有权断言及实际结果见[矩阵记录](research/native-qemu-matrix.md)。

新增独立入口：`python3 scripts/qemu/run-contention-qemu.py`用三个guest验证A持有时B拒绝、A同receipt继续输入、A释放后B接手；B普通UID的RustUSB错误与独立IMPORT busy状态分别记录，见[争用报告](research/native-qemu-contention.md)。它不证明两个fresh attach同时竞速或完整多客户端调度。`python3 scripts/qemu/run-fault-qemu.py`在guest内对唯一ESTABLISHED四元组执行ss SOCK_DESTROY，验证内核NULL/旧节点消失、自有记录幂等清理、无显式detach直接再次导入和最终驱动恢复，见[主动TCP中止报告](research/native-qemu-transport-abort.md)。它不修改宿主网络/模块，也不模拟WAN黑洞或默认心跳检测。两入口均默认locked构建、180秒总观察截止、要求原始标记与各guest正常退出；开发PASS与clean提交最终证据单独保留。

若仿真使用隔离的明文测试网络，明确它仅是测试环境，不宣称已满足 VPN / TLS 安全远程 gate；不能用设置 `trusted_network` 布尔值代替外部网络控制证据。脚本落地后在工作记录给出实际命令及结果；在此之前标未执行。双来宾仿真通过仍不证明物理设备兼容性或两个真实 Ubuntu 系统的 G1 硬件 gate。

| 场景 | 路径与判定 | 阶段 |
| --- | --- | --- |
| RustUSB Server → Linux 客户端 | Native 管理导出可先验证；客户端 `usbip list -r <host>`、`usbip attach -r <host> -b <busid>`，用 `usbip port` 与实际功能确认 | Native 在 Phase 1；Rust 服务端在 Phase 2 |
| Linux usbipd → RustUSB Client | 外部管理员导出设备，RustUSB discover / attach，经 VHCI 枚举并 detach；不接管外部非受管导出 | Phase 1 / 2 |
| RustUSB Server → RustUSB Client | 指明后端，完整端到端功能和清理；两端都叫 RustUSB 不代表数据平面由 Rust 实现 | Phase 1 / 2 |
| 虚拟 HID / CDC ACM | 参考固定上游 commit 的 QEMU 脚本，记录 QEMU / 来宾内核、设备模型和具体启动参数；验证枚举、事件 / 字节传输 | Phase 2 |
| 异常与重连 | QEMU / 协议 harness 断开、重新枚举、非法头、截断、错误序号、版本不兼容，验证错误和资源回收 | Phase 2 |

可选Rust虚拟服务、USBFS物理adapter及ECM各有独立入口：`scripts/qemu/run-userspace-qemu.py`、`scripts/qemu/run-physical-qemu.py`（热拔加`--hot-unplug`）、`scripts/qemu/run-composite-qemu.py`。ECM的两个私有netdev捕获只含合成测试数据，可用`python3 docs/evidence/verify-composite-captures.py <evidence-directory>`独立检查精确UDP字节和帧边界；USBMON仅在来宾显式诊断。最新ECM与原物理正常/热拔在同一干净22d172d三项通过，范围和所有失败见[报告](research/composite-qemu.md)。默认均构建，顺序运行避免共享release产物变化；检查期间禁止仓库写入，最终summary须记录clean、来源和guest退出码。

USBFS host-state syscall-entry取消/断线使用独立入口，另需Linux x86_64 C编译器及libc/Linux headers；宿主以普通用户运行，不在宿主ptrace或访问USB。先提交并确认工作树干净，以下八轮必须串行、默认locked构建且期间不写仓库，`/tmp`证据目录须尚不存在：

```bash
for operation in release configuration alternate clear; do
  for mode in unlink eof; do
    python3 scripts/qemu/run-host-state-qemu.py \
      --operation "$operation" --mode "$mode" \
      --evidence "/tmp/rustusb-host-state-$operation-$mode" || exit
  done
done
```

每轮两个一次性来宾，实际普通UID1000 physical server及独立wire probe通过匹配门nonce协调；tracer仅在server来宾运行。首次ARM启动idle25秒、后续RPC3秒、持门8秒，未使用`--skip-build`才可作最终证据。该门位于真实ioctl执行前，不能称kernel内部挂起；CLEAR模型返回的-32必须保留。同一干净c921a27六场景最终PASS，48项协议检查和12guest正常退出；范围、三个开发FAIL和最终裁决见[专项报告](research/usbfs-host-state-races.md)。

最新[checked release最终验收](research/usbfs-checked-release.md)在同一clean6e809a1完成上述八项、ECM及HID/FTDI正常/热拔共十一项，113case/22guest exit0。release动作仅在guest对授权IF0外部unbind产生真实EINVAL，必须保留-22、继续释放IF1、零SET_CONFIGURATION入口和独立恢复。probe按调用记录诊断，不重试；tracer以parent-only SIGCHLD通知等待减少观察开销，原frame2秒/RPC3秒/门8秒不变。发现FAIL立即锁定失败，最多30秒仅收集peer诊断且受原overall限制；guest0不替代FAIL。

物理runner的ttyS0保存kernel console、ttyS1保存程序证据；两通道均检查FAIL/内核完整性错误，终止后再次检查。启动须等待两设备均无blocker，原100次/50ms及最终完整断言保留。完整marker或严格JSON被打断仍FAIL，不通过拼接原始日志改判。

可选Rust服务的Unix信号验收分本地虚拟child和USBFS双QEMU两部分，均须普通Linux用户执行，并使用不存在的证据目录：

```bash
python3 scripts/test-server-signals.py --evidence /tmp/rustusb-server-signals
python3 scripts/qemu/run-sigterm-qemu.py --evidence /tmp/rustusb-sigterm-qemu
```

两入口默认执行locked all-features release构建并保存来源。前者的ready/blocked × SIGINT/SIGTERM四项，先对HID/CDC两个profile真实IMPORT、配置1并各保留一个pending IN；blocked用独立OFD填满4096B stdout、核对真实writer阻塞后不再drain。ready收到stopped才证明backend末状态，blocked的exit0/双socket EOF仅证明有界退出和连接释放，不能外推未观察的stopped或物理恢复。CI的`--skip-build`明确复用之前all-features产物，仍保存实际来源和SHA。

后者需要已有QEMU依赖以及ss和匹配内核UNIX_DIAG，仅在guest加载该诊断模块：先STOP已核root helper，以child FD/inode关联其UDS listener backlog，验证尚无ready/3240监听时的TERM；连接排队不等于helper已接受或授权。CONT helper后再运行主服务，完成HID实际按下/释放及FTDI精确35B双向I/O，确认两个真实lease各有pending/inflight和预算后仅向该已核PID/starttime发送一次TERM。先收齐双方原始wait、sysfs、VHCI和FD事实再判定，旧wait143不能截断观察，guest0不能覆盖FAIL。

clean934358f的四项QEMU及8guest均已通过；原虚拟回归使用`--features rustusb-cli/rust-backend`，TERM/物理正常/热拔使用`--all-features`，不得写为四项相同ELF。旧负向、完整命令、source/binary guard及限制见[报告](research/server-signal-shutdown.md)。重复信号、SIGKILL/abort、内核内部硬阻塞和真实硬件不因本轮通过改为已验收。

## SIGKILL 与首次重启负向

`python3 scripts/qemu/run-crash-qemu.py --evidence /tmp/rustusb-crash-observation` 使用两个隔离 guest，真实 HID/FTDI I/O 后对普通自有服务发送一次 SIGKILL，保留旧 sysfs 基线、wait137 和客户端自主清理，再进行一次无 IMPORT 的普通重启/合作停止。目录必须尚不存在。

clean1342a6a 的唯一实跑为恢复 FAIL、证据收集完整、runner exit1/两 guest exit0：55 host 检查中 4 项恢复断言失败。20.06 秒后原驱动仍为 null，重启采用 null 新基线；旧进程末资源不可观察。独立 host FTDI 原始 35 字节、RX/evdev hex、完整串口及来源见[报告](research/physical-crash-recovery.md)。这是持续保留的负向验收，不纳入默认绿色 CI，不改判为 XFAIL 成功，也未实现自动恢复。

原生 `usbip attach` 需要权限，测试时由管理员按发行版规则执行；不要为了手册方便给整个 RustUSB setuid。上述独立入口与各自报告分别记录已执行范围，不能复用Native通过标签替代其他后端或设备的验证。

## 5. 状态机和异常测试

每项测试记录故障前 / 故障中 / 清理后的 sysfs、VHCI、会话及日志；判定需要核对实际所有权和资源，不能只看 CLI 状态。

| 注入 | 期望行为 | 必须核验 |
| --- | --- | --- |
| 断网 / 关闭 VPN | Connected → 故障协调，停止新请求，给明确原因 | 不虚报正常；端口最终受控释放；无存储自动写恢复 |
| 高 RTT / 丢包 / 限速 | 有界等待、准确错误与可取消操作 | 无忙循环 / 无界积压；测试结束恢复网络规则 |
| 应用服务 / helper / 客户端被终止 | 不依赖析构保证全部清理；重启读取系统实况 | 不永久占用、不清空外部端口、不接管其他进程资源 |
| 设备在 discover / export / attach / I/O 时拔出 | 识别代际变化并报错，取消旧请求 | 旧 Bus ID 复用时不导出新设备 |
| 双客户端争用同一设备 | 单一所有者，另一方明确 busy / denied | 不能双主机独立使用同一存储文件系统 |
| export / detach 连续重复与并发取消 | 幂等结果，受控并发，错误不丢失 | 无多余子进程、悬挂代理、重复端口或泄露锁 |
| 驱动切换 / 已被外部 usbip 管理 | 报告外部状态或拒绝 | 不解绑他人的设备，不根据旧记录强制修复 |
| 配置损坏 / 状态记录截断 / 磁盘满 | 变更 fail-closed，保留原错误 | 不以空策略允许全部、不以空会话表证明无占用 |
| 权限不足 / 工具缺失 / 模块不可用 | doctor 和变更提供可操作错误 | 普通用户程序不自行提权、无部分成功伪装 |

高延迟 / 丢包可在专用测试网络中由管理员用 `tc netem` 注入；脚本必须限定测试接口并有显式清理动作，禁止对唯一管理链路盲目注入。对存储仅使用测试盘和只读模式；任何写入恢复测试须在后续安全机制通过后单独审批为受控实验。

## 6. 安全负向与 fuzz

必须测试未授权 UID、未授权设备、无认证对端、恶意 Bus ID / 主机 / 端口 / 配置、任意命令 / 路径请求、symlink 和不安全配置权限、超大 / 无终止 IPC、连接洪泛、畸形 USB/IP、超大接口 / 长度字段、半包、版本错误、重复 import / replay、日志秘密泄露和 root helper 滥用。每个拒绝用例核对外部命令未启动及系统状态未被错误改变。

网络解析入口须有 fuzz harness，采用固定 seed 回归已发现问题，并覆盖完整消息及分块到达。在 fuzz harness 实际加入后记录 `cargo-fuzz` 版本、target 名称、最大输入、时长、总执行次数、覆盖度和崩溃样本；没有 target 时下列是**计划命令模式**，不能当作运行记录：

```bash
cargo fuzz list
cargo fuzz run <实际协议解析target> -- -max_len=65536 -max_total_time=300
```

`cargo-fuzz` 的工具链和 sanitizer 前提按所用版本官方说明确认；fuzz 通常需要独立 nightly，不因此把主项目稳定 Rust 要求改成 nightly。IPC、配置、设备描述符和 USB/IP 解析分别设目标，长度上限与生产实现一致；发现崩溃后保留最小样本并增加确定性单元测试。五分钟 smoke fuzz 只能证明该运行未发现问题，不能证明解析层安全。

TLS 阶段增加错误 CA、错误主机身份、过期 / 吊销证书、无客户端证书、轮换、断开的 TLS、并发握手和认证限速。对外部接口捕获实际 DEVLIST 与 IMPORT / URB 流，检查只出现受管 VPN / TLS 数据；测试直接 TCP 3240 绕过、IPv6 与 DNS 多地址，不以管理 API 的 HTTPS 截图替代 USB 数据证据。抓包通过只能证明所测路径，仍须核对有效防火墙和监听策略。

## 7. 真实设备兼容性矩阵

每一行必须填写设备型号、VID/PID、固件、序列号是否存在、速度、接口 / endpoints、OS / 内核、架构、后端、工具 commit、网络条件、实际功能、异常和结果。初始全部标记未验证，源码支持或被发现不改为通过。

| 类别 | 实际功能检查 | 特别限制 |
| --- | --- | --- |
| USB Mass Storage | 只读校验、完整 LUN / 分区识别、安全释放、后续受控一致性实验 | Phase 1 默认拒绝；不使用生产数据；不自动重挂载写入 |
| USB Serial / CDC ACM | 双向带校验收发、长时间连续、拔出 / 重连 | 记录 tty 参数和实际驱动 |
| HID | 受控键 / 鼠事件、重复 / 丢失、断开 | 不导出主机唯一输入设备，不记录真实密码 |
| JTAG / Debug Probe | 测试板识别、只读 ID / 安全调试操作、恢复 | 不向生产板刷写以完成验收 |
| UVC 摄像头 | 分辨率 / 帧率、掉帧、带宽、备用接口 | Isochronous、固件和具体传输方式逐项验证 |
| USB Audio | 采样率 / 通道、抖动、丢帧、切换 | 不由 UVC 或 Bulk 成功推断支持 |
| USB 网卡 | 独立测试网络吞吐与重连 | 不共享承载当前 RustUSB 会话的唯一网卡 |
| Composite | 全接口枚举、实际功能、独占、alternate setting | 包含存储接口则遵循存储拒绝；不按单接口标整体支持 |

Ubuntu 22.04 / 24.04 × x86_64 / aarch64 的组合各有状态；HWE、OEM 或自行编译内核须单独记录，不能仅按发行版名称合并。Windows 在独立驱动和平台测试前不适用 Linux 通过结果。

## 8. 性能基准（目标，尚无实测值）

以同设备本地直连、原生 usbip 和 RustUSB 受管路径作可重复对照，固定线缆 / 端口、设备固件、CPU 调频、网络和工具版本。先预热，记录采样时长、重复次数、原始样本和统计方法。不要把发现延迟、TLS 握手耗时或文件缓存吞吐当成 USB 请求性能。

至少测 RTT、吞吐、P50 / P95 / P99 延迟、CPU、RSS、并发会话、错误率及断线恢复；分别用 Serial / HID / JTAG 小请求、存储只读、视频 / 音频连续流说明业务。Native 无法观测的 URB 细粒度指标标不可用。给出网络 RTT / 丢包 / 带宽、负载与是否加密；性能目标待基线后定义，不在此编造数值。

## 9. 结果模板与阶段裁决

```text
测试编号 / 阶段 gate：
日期、执行者、代码 commit：
两端 OS / 内核 / 架构 / 工具 / 配置版本：
设备 / 后端 / 网络（敏感字段脱敏）：
前置条件：
具体命令与退出码：
预期：
实际结果与日志 / 抓包 / 样本路径：
资源清理与最终系统实况：
结论：已通过 / 失败 / 未执行 / 不适用（理由）
遗留问题与后续工作：
```

阶段裁决依照 [roadmap](roadmap.md) 逐项 gate：自动检查通过只满足软件项；G1 仍需双 Ubuntu 真实设备；G2 需 Rust 后端 / QEMU / 互操作；G3 需实际数据通道与安全；后续 UI、安装、性能和 Windows 各自验收。任何失败和跳过均保留，修复后关联新记录，不能覆盖原始结果造成全部通过的假象。


## 持久物理授权记录

独立 `python3 scripts/qemu/run-journal-qemu.py --evidence /tmp/rustusb-journal-observation` 使用两台隔离 guest、QEMU HID/FTDI，不操作宿主 USB。新目录必须不存在。它检查真实 I/O 后正常结束退休、正常再次启动、真实 I/O 后 SIGKILL/wait137、跨 helper 重启保留原基线并明确拒绝新服务、普通 UID 管理拒绝、原驱动未恢复时 root 协调拒绝、guest 管理员显式恢复原驱动后核验退休和再启动。管理员协调后再启动执行第三轮真实 HID/FTDI I/O、活动请求及正常关闭；中间的正常重开仍是无 IMPORT 对照。本场景不覆盖 ECM、重插或断电，因此另跑原相应回归。

原 `run-crash-qemu.py` 保持不变，旧 clean1342a6a 的恢复 FAIL 不因新 guard 改判。新 runner 的绿色只证明 guard/正常退休/显式协调，不证明 SIGKILL 后自动恢复驱动。每次实际版本、命令、失败和来源以工作日志及证据索引为准。

## 独立 USBFS 恢复句柄机制

`python3 scripts/qemu/run-guardian-qemu.py --evidence /tmp/rustusb-guardian-observation` 在相同两guest中增加仅guest root可用的C probe。它验证独立open B、pidfd_getfd的A副本、kcmp身份、wait137后副本延长占用、另一OFD C占用时拒绝抢占、B恢复原驱动、关闭后只读协调及第三轮真实I/O。原journal两脚本不改，生成guest的6个唯一锚和完整来源单独保存；需要C编译器、Linux UAPI headers及guest pidfd_getfd/KCMP支持。

clean da764d0的237项检查/两guest0通过；首43d6d43因jq变量名错误在KILL前失败，原件保留。详见[报告与限制](research/usbfs-guardian-mechanism.md)。这是恢复句柄机制实验，不是产品自动恢复、任意外部进程/FD撤销、多配置或真实硬件验收。


## 明确授权的产品原驱动恢复

`run-autorestore-qemu.py --scenario owner-death|helper-restart --evidence <新目录>` 要求干净提交，默认locked all-features构建；使用两个隔离guest及合成HID/FTDI。先真实I/O，再一次KILL/wait137，由产品helper自主CONNECT恢复并退休；成功先通过纯磁盘/sysfs观察，之后才读pending API并完成新grant第三轮真实I/O。helper-restart还在原owner活着时重启helper、保持原记录/占用并继续真实I/O。不运行测试恢复probe、管理员bind或reconcile，不调用旧184的相反裁决。

clean2ab9976两个正向161/205 host检查和原默认off journal184、正常/热拔、ECM、SIGTERM五回归均通过，共14guest0；本地四信号和两背压也通过。全部实际来源与未完成负向见[报告](research/physical-driver-autorestore.md)。恢复窗口从wait137后控制握手完成才计时，不是kill起20秒SLA；活owner不执行恢复的证据为日志/源码约束、状态和继续I/O，没有逐syscall跟踪。该2ab9976矩阵未覆盖旧schema真实producer迁移、撤销、独立C占用三组合；后续独立运行见下一节。


## 原驱动恢复的旧记录、撤销与外部占用边界

新增 `run-autorestore-negative-qemu.py --scenario legacy-schema1|policy-revoked|external-claim --evidence <新目录>`。L还必须提供固定933e720实际构建的 `--legacy-helper` 与 `--legacy-manifest`，runner逐109个Git输入、ELF及干净隔离构建来源核验；不得用手改记录或新版default-off模拟旧producer。当前runner默认locked all-features构建，要求干净提交。

clean4565f7a首次三项全部PASS/6guest0，318/293/313项host检查。L尾部人工恢复和只读reconcile单列；P/C尾部只有明确新helper实例才能自主恢复、退休并第三轮真实I/O。C程序仅该场景打包，只plain CLAIM/RELEASE/close；20秒持有/拒绝后关闭C、同helper至少5秒不重试。纯primary早于pending API/新grant，启动doctor/Status仍先调用。具体命令、原件、软件/静态检查范围及其余未完成场景见[报告](research/physical-driver-autorestore-negatives.md)。
