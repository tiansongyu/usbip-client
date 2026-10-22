# RustUSB 工作日志

## 2026-10-22：任务与环境基线

- 读取完整任务并保存在 `requirements.md`。当前目录初始为空且无 Git 历史。
- 用户追加要求：适时 commit、记录过程、最终 push 到 `git@github.com:tiansongyu/usbip-client.git`。
- `git ls-remote --symref … HEAD refs/heads/*` 成功，无 refs；建立 main 和 origin，不覆盖既有历史。
- 工具链：rustc/cargo 1.97.1；宿主 Ubuntu 内核 6.8.0-106-generic，x86_64；普通 uid 1000。已存在 usbip/usbipd (usbip-utils 2.0)。
- `/sys/bus/usb/devices` 有真实设备，但未获指定测试设备，不自动解绑、导出、加载模块或修改宿主服务。VHCI sysfs 未出现。不能声明实际 USB 直通成功。
- 启动并行 Linux 内核/用户态审计、jiegec Rust 后端审计和安全/验收计划。源代码下载仅在 /tmp，项目不包含 Linux GPL 源码。
- 使用 writing-technical-design、technical-documentation、skill-creator；用户明确限定项目技能和文档范围，不生成无关逐组件技能。
- 下一步：固定上游来源并形成 Phase 0 设计，再实现和测试 Phase 1 Native 路径。长期阶段保留完整验收 gate。

## Phase 0 研究交付（提交 9800dd4）

- Linux 固定 `7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614`，补核5.15/6.8；逐项审查工具、内核、协议、许可证、驱动切换、fd交接、URB/ISO和断网。发现 attach可先改变内核再因状态文件失败退出、bind失败不完整恢复驱动、usbipd默认全地址监听。已据此调整Native验证和部署边界。
- Rust上游固定 `61938f8c48ed01c508faaacfa7cb3fc3810b5ae0`，检查全部指定源码/示例/QEMU。上游30测试通过；release探针重现6项panic/占用泄漏/响应超长问题，确认接受8MiB OUT。源码表明缺ISO/完整alt，物理USB错误可被忽略、同步操作阻塞worker，因此不直接纳入默认物理后端。
- 上游QEMU虚拟HID/CDC枚举与实际输入通过，但有seqnum/重复完成日志；仅是上游实验事实，不是RustUSB产品或物理兼容性验收。
- 交付审计、差距/复用、协议、架构、威胁模型、许可证、兼容性、阶段计划、测试手册、AGENTS和项目Skill。`quick_validate.py` 与两个审计脚本 `bash -n` 通过。

## Phase 1 软件实现与首轮检查

- 建立6个实际crate：core/protocol/linux/native/daemon/cli；2024 Edition，固定稳定Rust1.97.1，提交Cargo.lock。CLI普通用户运行；root helper使用OS UID、root管理策略与设备指纹白名单、固定argv、无用户shell解释。
- 本地扫描含接口/端点/驱动/速度/USB版本/身份/共享状态；读取缓存所有配置和alt，Composite及未激活存储接口也阻断导出。持久候选ID排除bus/address，保留controller/port和可用serial；明确同型号同端口替换歧义。
- DEVLIST解析设备数≤256、每设备接口≤32；版本、opcode、字符串、重复BusID和截断校验；总DNS/TCP/读取截止。IMPORT/URB仍由Linux工具/内核完成。
- Native命令前后核对sysfs，错误/超时也重新观察，不伪造回滚；只有remote devid、VID/PID、前空闲端口、USED、本地设备和端点记录均匹配才认领。detach限定已认领且身份未改变的端口，重启未知连接fail-closed；完整持久恢复仍未完成。
- 首次编译发现状态机Error变体遮蔽Error类型，已明确限定`crate::Error`修复。集成检查曾在其他并行文件尚未写完时报告缺模块，待文件落地后解决；未用空占位绕过。
- 首轮授权测试发现Serde内部tag无参数变体接受多余字段；改为私有严格wire结构+自定义Deserialize，保留公开API并补回归。策略元数据测试的fixture默认0664被正确拒绝，显式0644后再验证0666拒绝。
- 首轮完整 `scripts/check.sh` exit0：fmt、clippy -D warnings、27项测试、release、rustdoc -D warnings全部通过。后续新增边界/取消/工具路径检查和QEMU将再作最终验证，首轮不能替代最终结果。
- 实际普通用户CLI只读扫描6台设备；测试存储被正确标记阻断、Hub拒绝、另有端点不完整设备拒绝。未执行宿主bind/attach或加载模块；`doctor`明确本机usbip_host/vhci_hcd未加载、helper未运行、无内建加密。
- 安装单独nightly与cargo-fuzz0.13.2供开发，不修改默认稳定工具链。ASan DEVLIST smoke fuzz：3,412,047次/21秒、exit0、未发现崩溃；限时测试不能证明无漏洞。指标与限制见 `evidence/fuzz-devlist-2026-10-22.json`，不能当成USB性能数据。
- Cargo metadata导出81个工作区/传递/目标依赖的版本和许可到`dependency-inventory.json`，未缺许可证元数据；发布合规审查与外部GPL程序仍单独处理。
- 用户明确要求改用QEMU仿真测试。新增目标：两个隔离guest，测试本项目非root CLI→root helper→Native bind/usbipd→VHCI实际输入、detach/unexport/重连。与上游实验和真实物理设备报告分开；执行结果待记录。
- 实现增量已补：原生工具canonical与原路径父链权限、100字节状态文件限制、端口身份在Native锁内再次校验、kill/reap和sysfs读截止、helper Status真实内核快照、未知IPC字段和未授权mutation拒绝、协议精确计数上限。
- 第二轮完整` scripts/check.sh ` exit0：**32项测试全部通过**，fmt/clippy/release/rustdoc均通过，完整输出存`evidence/software-checks-2026-10-22.txt`。在此之后的产品变更需重新验证。
- QEMU首轮测试环境失败：BusyBox standalone ash命中内建setpriv，不支持`--reuid`；显式使用已复制的`/usr/bin/setpriv`修复测试脚本。initramfs显式root ownership/0755目录，保持产品权限校验。失败证据单独保留，未放宽helper安全约束。

## QEMU 闭环与安全复核

- Native workspace/CLI首版提交为`ef12209`。daemon初期还曾定位Rust partial-move编译错误，改为match前保存操作标志；最终所有权改动后的Clippy nested-if提示按2024 let-chain修正，没有删除功能消除告警。
- 本项目QEMU共5轮初测，前4轮FAIL原因和原始日志均保留：BusyBox setpriv选择、/run tmpfs模式过宽、BusyBox timeout退出码差异、VHCI端口NULL早于sysfs设备移除。具体修复和边界见`research/native-qemu.md`。
- 第5轮`evidence/qemu-native-20261022T100708Z/summary.json`完整PASS：隔离双guest、模拟键盘、UID1001拒绝、UID1000完整Native路径、真实Linux VHCI/usbhid/evdev KEY_1、两次导出/导入/断开/取消共享、本地usb/usbhid恢复。它属于中间二进制证据，最终修复版本另行验证；没有操作宿主USB。
- QEMU揭示的产品问题已修：active detach必须等旧sysfs目录(dev,inode)代际消失和portNULL，不能只看端口；如果端口已NULL但期望地址仍存在而无原代际证据，只读有界等待后明确要求协调，不操作新设备。
- 独立复核补强Native状态文件root可信路径/NOFOLLOW/元数据检查、attach前置检查、unexport仅AVAILABLE可执行。helper新增export ownership，只认领本实例成功创建且代际核验的绑定；外部/重启未知绑定拒绝接管。失败后新出现但来源不明的绑定不凭差分认领，全面自动恢复仍未验收。
- 文档状态转换与core规则同步，明确USB setup保持小端，doctor仅存在性探测；CLI表格适配完整指纹。项目架构/安全声明与实际能力分别记录，不把规划变成支持承诺。
- 新一轮`software-checks-final-2026-10-22.txt`曾验证38项测试及全部Cargo/rustdoc检查通过；随后存储quirk驱动/块设备后代边界检查新增，最终快照需再次核验，历史32项记录仍保存在前次commit。
- 最终冻结前新增存储安全回归：usb-storage/uas/ums-*驱动、真实目录中的block后代、遍历深度/数量上限；不跟随subsystem等symlink逃逸。scanner共8项测试通过。
- 最终冻结源码完整` scripts/check.sh ` exit0：**41项测试全部通过**，fmt、clippy -D warnings、release和rustdoc -D warnings均通过；以`evidence/software-checks-final-2026-10-22.txt`为最终软件证据。所有本地Markdown链接检查通过，QEMU脚本语法检查及宿主误执行拒绝测试通过。等待最终提交QEMU复测和push。

## 最终仿真裁决与发布准备

- 修复提交`0c3e85a`，可复现QEMU脚本/原始证据提交`97c2914`。原始console保留CRLF/空格，Git whitespace检查最初将其视为编辑文本报错；为evidence设置`-text -whitespace`保留原始字节，源码检查不放宽。
- 最终`python3 scripts/qemu/run-native-qemu.py` exit0，证据`evidence/qemu-native-20261022T101910Z/`：开始时工作树clean，默认`cargo build --workspace --release --locked`，commit97c2914，源码/lock/scripts/binary指纹完整；两周期全部断言PASS，两guest均exit0。运行后只修改文档/证据，不再修改产品源码。
- 最终验收逐条对照原需求在`acceptance.md`。Phase0产物交付、Phase1软件与G1-Q仿真通过；真实物理G1、完整恢复、Rust后端、TLS/TUI/GUI/安装/性能/Windows仍保留未完成，不标完整长期目标完成。
- 最后将提交最终证据并执行`git push -u origin main`，使用`git ls-remote origin refs/heads/main`与本地HEAD比较验证；远端结果在后续出版记录追加。
