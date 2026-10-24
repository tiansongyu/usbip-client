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

## 已推送与远端CI验证

- `git push -u origin main` exit0，首批完整发布到用户指定SSH仓库；`git ls-remote origin refs/heads/main`返回`af14f88a85be7f8c4659453796b2e231e53b6c26`，与当时本地HEAD完全相等。未强推或删除历史。
- GitHub Actions运行[39762479133](https://github.com/tiansongyu/usbip-client/actions/runs/39762479133)已completed/success；Ubuntu22.04 job119769782769与Ubuntu24.04 job119769783193均success，包含固定稳定工具链、全部软件检查和只读CLI smoke。完整出版摘要见`evidence/publication-2026-10-22.json`。
- 将上述远端事实和验收表中的历史“待复测”说明同步为最终结果，作为单独文档提交推送；产品源码与最终QEMU验证的97c2914保持一致。真实硬件及长期阶段仍未标完成。

## Phase 1 后续工作启动（基线21e98f9）

- 重新读取原任务、当前AGENTS/项目Skill、架构/路线与实际源码；确认上一轮为进展，工作树clean。继续Phase1软件与仿真，物理及长期范围不缩减。
- 独立审查定位：doctor只有路径存在性；helper内存旧端口在内核外部释放后未协调，可能在新attach已产生副作用后因map冲突拒绝；单靠可复用busid/VIDPID不足以保护新连接代际。当前为源码风险，尚未伪称已复现。
- 先在architecture记录运行诊断、sysfs generation receipt、先协调再attach及QEMU互操作/FTDI/reconnect设计，再并行实施与验证。用户继续授权QEMU、阶段commit和最终push；不操作宿主真实USB。

## 运行诊断、代际修复与矩阵扩展

- 用独立clone冻结已发布21e98f9、核对CLI/helper原SHA256，执行新reconnect脚本。cycle1键盘输入、官方detach、sessions=0及旧设备消失通过；cycle2直接RustUSB attach却在产生新内核连接后报`new import used an already recorded port`，旧缓存还将新代际标为managed。完整FAIL保存在`evidence/qemu-reconnect-old-20261022/`，不是环境失败。
- NativeSession新增可选本地地址和sysfs dev/inode receipt；USED快照前后复核属性、代际与VHCI行。helper在attach前仅清除已NULL且旧代际消失的记录；新外部连接不继承旧所有权，未知状态仍保守要求协调。并发冲突的最终防护使用新连接自己的receipt清理并如实报告结果。
- doctor改为固定可信工具的有界版本执行、模块安装/loaded/有效sysfs独立观察和OS root peer校验后的只读Status。helper状态schema1；CLI人类/JSON双输出，退出0表示观察完成。原生工具/IPC的任意stderr、策略和设备标识不进入doctor报告。没有自动加载模块、启动服务或测试网络/USB I/O。
- 新QEMU脚本覆盖三方向Native互操作、HID和FTDI双向字节、旧缓存重连及新代际拒绝控制；先用冻结旧产物验证3项harness，原始PASS/FAIL独立保留。官方QEMU6.2源码确认usb-serial为FTDI0403:6001而非CDC；本轮不把串口测试扩充为CDC、WAN、多客户端或物理设备通过。
- 软件gate首轮在既有子进程timeout测试失败，随后native28项单独通过；第二轮完整gate59项与fmt/clippy/release/rustdoc通过（保留intermediate输出）。补齐人类doctor内核/版本显示后，第三轮在新版versionrunner测试得到IoError。所有失败日志保留，正在增补测试错误上下文和调查竞态；尚不认定根因或最终gate通过。
- 调查时30轮完整syscall筛选和100轮execve追踪均未复现；增强断言后无strace第2轮明确捕获既有timeout fixture的`Err(Io(Os{code:26,kind:ExecutableFileBusy,...}))`，证据单独保留。已确认这一次失败是临时脚本spawn ETXTBSY；此前diagnostics IoError无errno，不能认定同一根因。只在测试端用共享异步锁覆盖可执行脚本生成到子进程清理窗口，不给生产命令增加重试、不放宽超时或断言。
- `python3 scripts/dependency-inventory.py`仍输出81包，清单无变化；新CLI依赖为既有workspace native，无新增第三方包。QEMU Python编译、shell语法和宿主直接执行guest-init拒绝(exit2)已核对。
- 测试夹具锁实施后，无strace、64测试线程的Native28项连续20轮全部通过；`evidence/native-fixture-stability-2026-10-22.txt`记录命令、每轮结果与源码哈希，未把文件描述符继承机制写成已证实事实。最终`bash scripts/check.sh` exit0：**59项测试**（core2/protocol4/scanner8/native28/daemon16/CLI1）、fmt、Clippy -D warnings、release、Rustdoc -D warnings全部通过，完整输出`evidence/software-checks-phase1-runtime-2026-10-22.txt`。
- 普通UID1000执行release doctor的人类/JSON输出均exit0并持久保存；宿主两原生工具安全版本2.0可用、两模块未加载/sysfs缺失、helper missing，断言schema和未验证安全/硬件标志通过。只执行固定version和只读Status，未改宿主USB、模块或服务。
- 产品与测试脚本将分开提交后冻结；最终7项QEMU矩阵待在同一干净提交运行，以上59项软件测试不能代替本轮实际内核验收。

## 本轮最终QEMU与发布裁决

- 产品修复/诊断提交`a4d258c`，互操作harness/软件证据提交`b4cbfc3`。此时工作树clean，所有agent停止源码和脚本修改。7项分别写入/tmp独立目录，全部运行结束后才一次性按原字节复制到`evidence/qemu-matrix-20261022-*`，逐文件SHA256核对，避免前项证据污染后项dirty状态。
- 7项均默认locked release构建，冻结`b4cbfc3bd6c63eb22d8296bfa835767586aa345b`，source/scripts/lock/binaries/kernel/native工具指纹一致；**全部PASS，14guest均exit0**。三方向Native各HID/FTDI两周期，串口每周期每方向35字节，host精确70字节；reconnect三次实际KEY_1，外部释放后直接RustUSB attach成功，同port/local Bus ID/VIDPID新外部代际为unmanaged且旧detach明确拒绝，拒绝后输入仍正常，最终资源释放及server驱动恢复通过。
- 从原始serial重新解析42份doctor JSON：两端helper启动前missing、启动后root peer verified/available、UID1001 permission_denied，角色sysfs、server helper无VHCI/client有VHCI、schema1和未验证安全/硬件flags全部核对；本发行版usbipd版本探测均实际available，未将root stderr提示或doctor exit0当授权通过。逐项命令和限制见`research/native-qemu-matrix.md`，机器索引`evidence/qemu-matrix-20261022.json`。
- 根进程独立核对七个summary、运行前clean/locked构建、两端退出码、当前生产源码/脚本与b4cbfc3无差异；旧21e98f9真实重连FAIL及中间harness证据保留。本轮结果不证明CDC ACM、真实设备、netem/WAN、多客户端、TLS或长期性能；完整Phase1物理及Phase2–7仍未标完成。
- `git push origin main` exit0推送b4cbfc3，`git ls-remote origin refs/heads/main`与本地HEAD一致。GitHub Actions [39766360288](https://github.com/tiansongyu/usbip-client/actions/runs/39766360288) completed/success；Ubuntu24.04 job119782683028、Ubuntu22.04 job119782683326均success，含全部质量检查与只读CLI smoke。本轮出版事实记录`evidence/publication-runtime-2026-10-22.json`。
- 最终验收文档、全部QEMU原始日志与出版证据作为独立提交普通push；其后核对远端main与HEAD及工作树，产品/scripts保持b4cbfc3被测内容。最后一次自身提交的SHA以Git历史和实际push/ls-remote输出为准，文档不虚构自引用哈希。
- 验收/日志提交`3dc7fb8`普通push exit0。归档复核发现全局`*.log`忽略了6份已复制且被索引hash引用的runner.log；补充evidence专用忽略例外并提交这些runner日志，未改任何被测产品或脚本。最终额外核对机器索引的每个artifact均被Git跟踪且SHA256匹配，避免本地存在而远端缺文件。

## 继续Phase1：争用、传输故障与原生架构CI（基线67c83d5）

- 上一轮属于进展：权威源码、四个提交、59项软件检查、七项实际双QEMU、14个guest和远端CI均有证据；最新67c83d5已push，工作树clean。本轮重新读取原始附件、AGENTS、项目Skill、架构及计划，不缩减完整目标。
- 原需求的多客户端争用和异常TCP断链仍缺系统证据，设计先行：新增隔离三guest争用和双guest传输中止harness，沿用QEMU模拟HID/私有socketpair和UID1000 CLI/root helper。不会操作宿主USB、模块或网络，不把QEMU载波变化当作TCP断链。
- 复核Linux6.8：普通TCP错误走SHUTDOWN|RESET并回到NULL；ERROR/UNUSABLE另有成因且sysfs隐藏身份。故先验证真实传输故障后的NULL/旧代际消失和直接重连，不为假设的ERROR强行清理放宽所有权。
- 原生aarch64软件验证是原目标尚缺项。核实仓库public、GitHub官方Ubuntu22.04-arm/24.04-arm运行器可用后，计划扩展到四格OS/架构矩阵，显式断言uname和记录工具链/内核；运行fmt/clippy/test/release/rustdoc及只读smoke。它不代替ARM内核USB互操作/硬件验收。checkout将固定官方Node24版本完整SHA，移除旧Node20运行时弃用警告，保留只读token/persist-credentials=false。
- 官方GitHub API确认checkout v7.0.1精确commit`3d3c42e5aac5ba805825da76410c181273ba90b1`、action.yml运行时node24；workflow按四格矩阵实现，15分钟job截止。PyYAML解析及四项矩阵唯一性检查通过，本地`bash scripts/check.sh` exit0、59项及全部质量检查通过，输出`evidence/software-checks-native-matrix-2026-10-22.txt`。此时ARM结果仍待真实远端执行，不把矩阵配置写成已通过。
- CI提交`bf08652`已普通push。实际[39768009618](https://github.com/tiansongyu/usbip-client/actions/runs/39768009618) completed/success，四个原生job全部通过：Ubuntu24.04 x86_64 119788179584、Ubuntu24.04 aarch64 119788179807、Ubuntu22.04 aarch64 119788179843、Ubuntu22.04 x86_64 119788180159。分别保存uname/Rust host、59项/0失败/0忽略和job链接到`evidence/ci-native-matrix-2026-10-22.json`，不保存无关CI设备输出或凭据。ARM软件验证已从未执行变为已通过，ARM USB内核/物理测试仍未执行。
- 三guest争用dev01 FAIL：争用拒绝、wire忙、A同receipt继续输入及A释放已通过，但B新reader后台尚未建立采样文件，前台wc失败。修为父shell先创建文件并打开evdev后再发布WAIT；dev02完整PASS，3guest exit0。两次原始日志、runner与当次脚本快照/哈希保留在`evidence/qemu-contention-dev-20261022-{01,02}`，不改产品。B的RustUSB实际观察为not_advertised，独立原始IMPORT才证明busy status2，两种证据不混写。
- 双guest传输中止dev1 FAIL：真实SOCK_DESTROY及第一次内核释放/清理已过，但服务端采样AVAILABLE前客户端已重连，测试同步不足。新增guest私有确认barrier；dev2两次abort、三次输入/NULL和直接重连都过，最终立即unexport前服务端异步reset尚未完成，仍保留FAIL。使用同一15秒截止等待AVAILABLE并提前打开evdev后，dev3完整PASS、2guest exit0；各次原始日志/summary/build.log在`evidence/qemu-transport-abort-dev-{1,2,3}-20261022`，未改产品占用拒绝或内核超时。
- 独立安全复核确认两个harness无剩余must-fix；Native源码和原七项脚本与67c83d5无差异，ERROR仍拒绝猜测清理。新增生命周期审查准确区分TCP→NULL与ERROR身份缺口。root分别执行两Python编译/--help、两shell语法和宿主直接运行拒绝(exit2)、Markdown本地链接及whitespace检查通过；补证据专用通用*-serial.log/build.log规则，覆盖client-a/b的原始日志。
- 本轮两个新harness开发PASS仍是dirty工作树，最终需要统一commit冻结后，在/tmp独立目录默认locked构建并复跑；root不以开发输出代替发布证据。

## 新增争用与TCP中止的最终冻结裁决

- 新harness、全部开发FAIL/PASS与源码依据提交`b487144`，root确认工作树clean后两项分别在/tmp独立目录运行，运行中禁止任何仓库写入；两项都结束后才归档证据。**最终两项PASS，5guest全部exit0**，两次默认locked构建、同一b487144、运行前dirty=false、运行后完整源码/新旧依赖脚本指纹一致。
- 三guest最终证据`evidence/qemu-contention-final-20261022/`：B的UID1000 RustUSB明确not_advertised且sessions0；独立IMPORT完整8字节`0111000300000002`与官方Device busy证明内核拒绝。A同完整receipt及同一evdev reader在B失败前后分别收到数字1/2；A释放后B受管导入、数字3和清理通过，最后server恢复usb/usbhid、两个client DEVLIST0。8份原始日志/summary/runner逐字节归档，附SHA清单。
- 双guest最终证据`evidence/qemu-transport-abort-final-20261022/`：两次仅对唯一已建立USB/IP四元组执行guest root ss SOCK_DESTROY，目标ESTABLISHED各从1到0、server AVAILABLE且listener/原usbipd/helper仍活。三周期KEY_1、NULL/旧节点消失、自有已释放记录幂等清理、第二次不显式detach直接new-generation attach和最终驱动恢复全部通过。实测ss5.15.0、Linux6.8原生工具/module/hash均记录；官方5.15源码路径已核对，6.1仅为先前研究。7份原始产物及独立SHA/长度清单保留。
- root独立验证两个final summary、源码/脚本、全部原始文件SHA和长度、5退出码及原七项矩阵CLI/helper产物哈希相同；聚合索引`evidence/qemu-native-contention-transport-20261022.json`。Native Rust、Cargo和原七项脚本与b4cbfc3无差异，既有七项证据仍适用，没有为通过测试放宽ERROR或占用检查。
- b487144已普通push、remote main=本地HEAD；其四格[CI 39769296031](https://github.com/tiansongyu/usbip-client/actions/runs/39769296031)全部success，Ubuntu22.04 x86_64 job119792492182、Ubuntu24.04 aarch64 job119792492683、Ubuntu24.04 x86_64 job119792492762、Ubuntu22.04 aarch64 job119792492855。事实记录`evidence/publication-native-validation-2026-10-22.json`，最终文档/evidence随后独立提交并普通push，再核对远端SHA与clean状态。
- 完整目标仍active：物理G1、CDC/Rust后端、真实设备兼容性、完整会话恢复、WAN黑洞/延迟/丢包、加密、TUI/GUI/安装/性能/Windows各gate均保持原需求；本轮不把安全拒绝或主动中止的手动重连称为完整自动恢复。

## 可选 Rust 虚拟服务端（基线85e87fa，实施中）

- 重新读取原始完整目标、当前源码/审计、项目技能和技术文档/技术设计技能。Native九项QEMU及四平台软件CI是已有进展；真实设备G1与后续完整目标仍未完成。用户指定QEMU，继续可自动验证的虚拟Rust后端子步骤，不等待未提供的物理设备。
- 设计先行写入`research/rust-virtual-backend-design.md`：对比固定依赖、维护fork、自有有界adapter三条路线。固定上游同步接口和私有状态不能通过薄wrapper解决已有真实缺陷，且无条件拉入物理USB依赖；选择复用领域类型/VHCI与MIT HID描述符，独立实现有界session。完整上游许可保留，不复制Linux GPL实现。
- 并行实施wire/session、HID/标准两接口CDC模型、隔离QEMU/独立wireprobe；root负责feature CLI、质量检查和文档。默认CLI不启用Rust服务，非loopback须显式受管测试网声明和精确peer IP allowlist；无认证/TLS的边界保留。
- 本轮初步资源契约：2设备、16连接、64pending/session、64KiB transfer/CDC queue、64完整HID reports，握手/半包/写5秒；pending IN不假回空成功，UNLINK成功=-104且不发原completion。物理R06/R07、ISO、任意Composite/alternate不因虚拟profile而标完成。
- 新crate实际实现完成：共享DeviceDiscovery/ExportBackend、显式virtual来源、两设备RAII独占、标准HID/CDC控制与有界队列/计数/SHA。初审发现CDC OUT队列满误返回pending，修为ENOSPC/actual0且不改变原数据；SET_CONFIGURATION明确退休旧pending并清USB状态。flags统一0x03c5、默认HID idle0，描述符USB2.00/FullSpeed和MIT来源保持一致。
- 栈内pinned reader与actor并发，半包跨输入/timer唤醒保存进度，外层JoinSet统一16连接abort+join；避免嵌套reader任务只abort未等待。新crate16项有效tests包括真实TCP、UNLINK/无迟到completion、seqwrap保序、重复seq/pending65拒绝、busy/阶段、写失败/Drop、peer/连接预算、配置重置和队列满恢复。
- root独立loopback std wireprobe全13marker通过，CLI负向监听/秘密marker trace日志脱敏、SIGINT清理均通过。实时时钟deadline：半包management5.001秒、data5.002秒主动关闭；完整import空闲5.506秒仍可GET_DESCRIPTOR，不伪造无输入的成功completion。对应开发证据保留，不等于QEMU/物理验收。
- 新生产parser fuzz入口与10个确定性seed已加入；开发ASan smoke3,702,581runs/31秒、cov562/ft718、无观测崩溃。它覆盖有界management/data framing，不覆盖设备handler、全部网络调度或安全证明；最终冻结source后另行复跑。
- 独立review实际复现stdout满pipe会卡worker、阻止CtrlC清理（原FAIL材料完整保留）。修为仅持编码事件的专用std线程、有界2队列/64KiB事件/5秒确认；整个控制future参与CtrlC select，虚拟模式错误不回落同步stdoutJSON。root持真实HID lease、固定4096B pipe不排空：SIGINT exit0/0.001秒且socketEOF；不发信号时--json超时exit1/4.819秒且socketEOF；独立原probe复核新binary也PASS。blocked事件被信号取消后不再等待stopped；显式debug stderr回压仍属既有日志sink缺口，未声称任意sink有界退出。回归脚本进入CI。
- stdout修复后完整scripts/check.sh exit0：默认75tests、allfeatures76tests，0失败/0忽略，fmt、两组Clippy/release/严格Rustdoc全过；原始输出`evidence/software-checks-virtual-20261022.txt`。Cargo仅增加自有crate，外部依赖版本未变，清单82packages均有license metadata。
- QEMU开发dev01 FAIL为runner首轮读取尚未创建serial日志；dev02实际两个kernel驱动已枚举，但bNumInterfaces的%2d空格被错误字符串断言拒绝；均保留FAIL/原脚本，不修改产品绕过。harness修复后dev03双guest完整PASS，HID两轮按下/释放、CDC exact35B注入与35B回显/轮、总OUT86/IN156/injected70（含独立probe16B）、末lease/pending/queues0。dev03是dirty开发证据，不作为最终冻结发布验收。

## Rust 虚拟子步骤的最终冻结与出版

- 实现提交`2d6aadf`、QEMU测试/开发原始证据提交`cfeffd3`后，确认工作树clean。最终QEMU默认locked-feature release在`cfeffd3ee1d74359438d6bdb791861356a1f3c7e`运行：整体PASS、两guest均exit0；19条probe记录（15protocol含汇总、两次busy、两次health）、两设备并发、两周期真实HID按下/释放和CDC57600/8/DTR/RTS与精确I/O、末lease/pending/queues0全部通过。运行前后source/scripts/CLI/helper hash一致，root独立重新解析snapshot和CDC总账/摘要通过。
- 最终证据`evidence/qemu-userspace-final-20261022/`逐字节复制，附SHA清单；CLI SHA29aca2e9472d13394ac8c331545fc1770c565e458e1f988412d03e6655d8ea67，helper SHA cb59c5fec9fe53a6a42f8de4c256c58cbe3b4264b43e11b312795e19455a9dad。聚合`evidence/virtual-validation-final-20261022.json`索引11份最终产物，记录SHA和长度。四份开发目录的FAIL/PASS/脚本快照和原始bytes保持未改。
- 同一clean cfeffd3另跑最终server_frame ASan fuzz：3,615,249runs/31秒，cov562/ft718、exit0、无观测崩溃；nightly1.101.0(2026-10-21)/cargo-fuzz0.13.2、生产parser/source/lock指纹和起止clean状态均保存。它不覆盖handler/完整网络调度/物理USB，不是安全或吞吐证明。相同release产物的stdout4096B不drain回归再次PASS，SIGINT0及5秒deadline1均关闭真实lease socket。
- 普通`git push origin main` exit0已推送两个提交，随后`git ls-remote`准确等于cfeffd3。实际四格[CI39774177011](https://github.com/tiansongyu/usbip-client/actions/runs/39774177011) completed/success：Ubuntu22.04 x86_64 job119808847152、22.04 aarch64 job119808847387、24.04 aarch64 job119808847498、24.04 x86_64 job119808847541。root读取每job原始logs，核对native Rust host、默认75/allfeatures76（合计151）/0失败/0忽略与两项stdout背压回归；全部通过。完整步骤和链接`evidence/publication-virtual-20261022.json`。
- 最终验收/README/协议/架构/兼容性/计划同步固定虚拟支持范围；真实G1及G2物理、ISO/通用Composite/alternate、G3认证/TLS/完整恢复、TUI/GUI/部署/性能/Windows仍未完成。Native核心和旧九场景脚本相对被测cfeffd3无更改；不会以文档修饰缺失功能。
- root全Markdown本地链接159处检查通过，所有虚拟原始产物SHA/长度通过；最终文档/evidence单独commit普通push，再核对Git存储字节、远端SHA和clean状态。最后提交自身SHA以实际Git历史/push结果为准，不伪造自引用证据。

## Rust 物理 USBFS adapter（基线037a9c7，实施中）

- 上一轮是实质进展：2d6aadf/cfeffd3/037a9c7均push，最终四格CI39774946417全success、工作树clean。重新读取完整原始目标与项目skill，继续G2真实用户态设备路径，不把已测两个虚拟profile当作物理后端。
- 并行审查nusb/rusb、共享engine和QEMU物理模型。固定nusb0.2.8公开API会丢Control partial/OUT实际长度、限制IN MPS倍数、只有cancel_all且Control Drop不立即取消；Linuxerrno类别折叠。不能直接套便利API宣称正确USB/IP。
- 设计先行`research/rust-physical-backend-design.md`：固定许可vendor小扩展复用原usbfs底层；唯一网络engine下统一异步会话、真实terminal前保留lease/预算；helper精确授权OwnedFd能力、物理/Native策略互斥；普通UID服务，不接触宿主USB。/tmp纯编译原型已证明约140行safe API扩展可编译，尚不是设备或取消通过。
- helper新增`physical-usb`可选操作：固定root节点/代际receipt、两阶段JSON→ACK→SCM_RIGHTS，旧策略缺字段默认空，Native/physical列表不能重叠。22项daemon all-features测试及Clippy通过，覆盖UID/ID/可信网声明在扫描前拒绝、无授权不传FD、CLOEXEC、标记/多FD/截断和普通文件拒绝。首次拒绝测试把未知UID关闭时的ECONNRESET误当失败；核对Unix stream未读数据行为后，只额外接受该明确错误，仍要求无FD与关闭，复跑全过。
- 共享engine默认24项测试通过，原虚拟16项保持通过；新回归包含Control/IN/OUT异步errno与partial、真实terminal前保持busy/预算、完成/取消竞态、10秒清理截止和空active设备断开。空active此前不会poll事件，补独立disconnect观察，验证TCP关闭与USB清理所有权分别保持。
- CLI提前验证设备数量/重复和网络策略，再联系helper；helper拒绝保留脱敏原因，不误报“未安装物理功能”。初版完整CLI `cargo check --all-features --locked`通过；随后vendor Control API补flag时的一次并行CLI测试遇到新旧签名中间态，待adapter冻结后统一复跑，不把实施中的编译结果写成最终gate。
- 独立review发现并协调修正响应预算早退（write中的reply、未消费events与incoming队列均须先退休）、watchdog累积及旧timer误伤新claim、尚在controls队列中的配置barrier、取消先于已observable completion、Control SHORT_NOT_OK遗漏、配置观察失败fallback和claim后driver错误被忽略。原driver恢复还需严格限于本会话实际取得的接口；完成后统一测试，不用正常HID/FTDI路径覆盖这些并发风险。
- 新QEMU harness独立于旧九项和纯虚拟场景：root0600节点、普通UID1000/零cap server、UID1001直接OpenPhysical拒绝、真实UNLINK/EOF/reap、两周期HID/FTDI、热拔与新代际不自动授权。独立std probe包含EP0一致1B请求、FTDI0B OUT、0/1B IN观测/取消、专用13B注入后的精确15B IN及late UNLINK；依据QEMU6.2 FTDI<=2B NAK行为，不虚构overflow或周期status包。当前只有静态检查，尚未运行QEMU；静态记录及首次脚本快照保留在evidence。
- 所有feature的锁定依赖清单导出改为`cargo metadata --all-features --locked`，当前100包均有许可元数据。移除物理crate未使用的scanner依赖边；固定nusb完整原文件和MIT/Apache许可另行保留，核心无新unsafe不等于依赖无unsafe。
- helper按独立逻辑提交`2fde196`。adapter及唯一engine冻结后root执行完整`scripts/check.sh` exit0，默认84/allfeatures97，全部fmt/Clippy/release/严格Rustdoc通过；日志暂存`/tmp/rustusb-physical-checks-dev01-20261022.txt`待统一归档。root另复跑vendor14纯测试、两项stdout背压（SIGINT0/0.003秒、deadline1/4.820秒且lease socket关闭）通过，并独立核对crate archive SHA、全部70原文件/当前文件、双许可与精确patch；原始unsafe token数量346保持346。
- 双QEMU normal dev01 FAIL发生于start_helper的UID1001 sessions前置检查：root helper已SO_PEERCRED拒绝并关闭，但CLI后写Request得到EPIPE而没有读出已缓冲的拒绝；尚未进入USBFS生命周期。未把EPIPE泛化为权限PASS。root新增同一已验证socket的exchange：仅IO/timeout写失败时用5秒上限读取一次严格完整`ok=false/data=null/error非空`的Response，伪成功/畸形/EOF等保留原write error，不重发、不重连、不发FD ACK。3个新回归确定先拒绝后write的时序，25项daemon测试全过；probe按同原则保留严格caller拒绝断言。dev01原始日志与准确脚本快照保留。
- 修复后第二次完整`scripts/check.sh` exit0，默认86/allfeatures100，0失败/0忽略；日志`/tmp/rustusb-physical-checks-dev02-20261022.txt`待归档。此时normal dev02开始，尚无PASS结论，不把软件检查当作实际USBFS证据。
- normal dev02的严格CLI UID拒绝已修好；独立std OpenPhysical probe等待EOF时遇未读request导致的ECONNRESET，未输出已完整读取的拒绝JSON，纯harness FAIL。修为仅在完整有界单帧之后接受EOF或明确reset作为连接关闭，仍须精确callerUID拒绝，不把reset独立当PASS。normal dev03及hot dev01随后均PASS，4guest均exit0，15条probe含0/1B真实pending取消、15B真实2状态+13数据、配置pipeline、lateUNLINK；两轮HID与每轮35B RX/TX准确，normal host70B、hot140B。拔HID时FTDI同receipt继续，QMP完成事件、新代际不自动授权和剩余driver恢复均通过。所有开发FAIL/PASS原始bytes与当次3脚本/SHA保存在`evidence/qemu-physical-*dev*-20261022/`。
- root独立核对normal dev03两guest退出码、15marker、所有release/budget0和原驱动恢复、CLI/helper SHA；后补两处共享API文案后第三次完整`scripts/check.sh` exit0，默认86/allfeatures100及全部Cargo gates通过。三次完整输出、vendor14纯测试、provenance复核脚本/输出、stdout背压和CLI帮助已原样归档，聚合`evidence/physical-software-validation-20261022.json`。产品/scripts将分逻辑提交再clean冻结复跑normal/hot和原虚拟场景；当前开发PASS不是最终冻结验收。
- 实现拆为`270bc75`共享engine/固定nusb与监督器、`428fb7d`普通用户CLI、`f42d0f6`QEMU/harness与全部开发证据；此前IPC时序修复单独为`bfbd5fc`。clean f42d首轮最终normal仍FAIL：第一轮真实HID/FTDI I/O已通过、两端driver随后恢复，但client FTDI detach后核验两次VHCI status不一致而立即返回reconciliation错误。trap稍后只读sessions为[]，不把该快照错误表述为已证实的永久资源泄漏；不以harness重复detach掩盖。最终FAIL原字节和clean/commit/defaultbuild事实保存在`evidence/qemu-physical-normal-final-f42d-fail-20261022/`，hot/userspace最终系列已停止，等待Native post-detach有界只读观察修复后新commit重新验收。
- Native最小修复使用私有typed观察：只有同一status属性两次成功解析但内容不等返回Changed；公开快照仍fail-closed，仅一次成功detach后的原settle截止允许只读重观测。其他错误原样失败、稳定replacement立即Permission，不重发命令。3个FIFO/代际确定性回归验证Changed、单次调用、NULL与旧代际gone双条件，以及unknown receipt/replacement拒绝；Native31tests与Clippy通过，独立review无mustfix。root完整`scripts/check.sh`再次exit0，默认89/allfeatures103、vendor14合计206项0失败/0忽略和全部Cargo gates通过。vendor纯测试加入check脚本/四平台CI，原始输出与聚合索引保留。root另核Git存储的8软件产物、48开发QEMU文件、70原vendor文件共126个blob均与哈希/原bytes一致，未漏被忽略日志。

## USBFS adapter 的最终冻结、QEMU与出版

- Native修复单独提交`9b0da33`，FAIL/质量证据提交`37bc6b4`后确认工作树clean；最终normal、hot与原userspace依次默认locked构建，在全部运行结束前没有仓库写入。三项均PASS、6guest全部exit0，起始commit均`37bc6b48906844fd6fd6cb6ded8680ed7fe2d7b2`且dirty=false，结束source/binary guards通过；f42d原最终FAIL仍保留，未重写为PASS。
- normal/hot分别15条独立probe：root0600节点/UID1000零cap真实helper FD、UID1001明确OpenPhysical拒绝、真实0/1B pending取消、原长15B=2状态+13数据、配置pipeline、EOF/UNLINK/busy、HID两轮down/up与FTDI准确35B每向每轮。normal host70B，hot140B；QMP DEVICE_DELETED、Gone、FTDI同receipt继续、新devnum/node不继承旧授权、可用设备原驱动恢复及全pending/queued/inflight/预算0全部核对。physical CLI/helper SHA分别`8bd10c5a08f857ea0ab8c7897c93cd9e5feff27f5d7219114871340a40b8ec69`、`5d40d8e39286ab722a1abe7a09d1c68e0da9c5f3a7d2273f783b09f95cd64ae0`。
- 原虚拟HID/标准CDC回归19条probe、两周期实际evdev/ttyACM通过，CDC累计OUT86/IN156/injected70和末lease/pending/queues0准确。该runner按原feature构建，CLI/helper分别`f8c5d2fe008ee92d9f5842f6a8b0e2d59bc55c63ce4c51c8570a51c0a6f3e5eb`、`df767b8092f00a4f0301651193327332ee3bfc799e5d5f168b716315af0e03b6`，与all-features物理binary的差异有构建命令依据，不混写同一产物。root独立重新计算两物理source指纹、三份summary/退出码/数据账及全部36份原始产物bytes、SHA清单通过，聚合`evidence/physical-validation-final-20261022.json`。
- `git push origin main` exit0将037a9c7普通推进37bc6b4，`git ls-remote`准确一致。真实[CI39786348508](https://github.com/tiansongyu/usbip-client/actions/runs/39786348508)四job success：22.04 x86_64 119850486766、24.04 aarch64 119850487139、24.04 x86_64 119850487165、22.04 aarch64 119850487366。root读取每job原始log核对native Rust host、89/103/14合计206项0失败/0忽略与两项stdout背压，均通过；精确步骤/链接/原log SHA记录`evidence/publication-physical-20261022.json`，不保存无关CI设备详情或凭据。
- 最终36份原日志、验收/README/架构/安全/兼容性同步作为独立文档证据提交后普通push，再核对Git存储bytes、远端SHA与clean。自有代码/scripts相对被测37bc不再改变；最后提交自身SHA以实际Git历史与push为准，不伪造自引用记录。真实G1/G2硬件、ISO/任意Composite/alternate、认证/TLS/完整恢复、TUI/GUI/安装/性能/Windows仍保留原目标，完整goal仍active。
- 文档证据提交`03d8a88`普通push后，remote main与本地一致、工作树clean；root核对Git存储的36份最终QEMU及9份软件产物SHA/长度通过。其四平台[CI39787450720](https://github.com/tiansongyu/usbip-client/actions/runs/39787450720)已completed/success，4job全部success；产品/scripts仍与被测37bc6b4一致。
- 最后独立文档复核再次确认45份索引产物及本地链接，发现acceptance仍有旧75/76测试数量、未限定历史的Native源码未变、E12当前CI引用及USBFS热拔未验证表述。统一明确cfeffd3历史范围，更新为E13/E14的89/103/vendor14、9b0da33最小修复与37bc三项QEMU裁决；保留全部旧结果，不更改产品、脚本或测试证据。此次仅文档更正，完成diff/本地链接核对后另行commit/push。

## 继续 Phase 2：Composite 与备用接口（基线7d06ffa）

- 上一轮为实际进展：USBFS adapter、Native修复、206项软件检查、3项最终QEMU/6guest、四平台CI和全部原始证据已提交推送。最后文档更正7d06ffa与远端一致、工作树clean，CI39788484935四job全部success；完整目标仍active，真实硬件不因QEMU通过改成完成。
- 本轮重新读取原始附件、AGENTS、项目skill、roadmap和物理设计，并并行审查模型/实现/验收边界。QEMU6.2 usb-net实际ECM配置值为1、RNDIS为2，ECM IF1 alt0无端点、alt1有Bulk；以固定源码和运行观察区分descriptor index与configuration value。
- 源码审查发现真实Composite恢复缺口：CDC控制接口probe会同步认领数据接口，而当前恢复循环仍用此前的全接口快照，可能对已恢复的第二接口再次CONNECT并因EBUSY误隔离。先写入`research/usbfs-composite-qemu-design.md`的窄修复与确定性回归契约，再实施；目前尚未通过新的QEMU，不提前声明修复或兼容性通过。
- 设计提交57ca5a8；实现逐自有IF重观察和错误后严格真实收敛，原alternate已被内核恢复时不重复claim。5个确定性fixture覆盖sibling同步绑定、真实匹配/不同driver、代际/配置变化及未owned不操作；定向35项与Clippy通过，旧原始日志保留。
- 独立审查补出新配置安全缺口：SET_CONFIGURATION后可能出现旧active快照未包含的vendor-class存储驱动或block后代，不能立即detach新接口。增加可选既有scanner依赖，在每个claim及恢复动作前核对当前scope、ID、全IF、blockers和外部所有权；只允许本次实际持有的usbfs sibling。配置0合法且不claim；新危险owner使恢复拒绝而不盲目回滚配置。此检查不撤销已触发kernel probe、不预知所有inactive quirk或消除admin TOCTOU。新增5项安全fixture后后端40项及Clippy通过，Cargo.lock仅增加既有path依赖边，清单仍100包/版本不变。
- root冻结产品后执行完整`bash scripts/check.sh` exit0：默认89/allfeatures113/vendor14合计216项，0失败/0忽略及fmt、两组Clippy/release/严格Rustdoc全过。独立产品复核无mustfix；6份中间/最终软件日志原字节归档，聚合`evidence/composite-software-validation-20261022.json`记录当前96份软件输入SHA及执行环境。这是dirty开发软件gate，后续仍需提交冻结后的实际ECM QEMU，不把软件检查当设备通过。
- 产品/软件证据提交192640c。进一步核Linux6.8 interface unbind会重置alt0，因此本轮CDC原driver重新probe恢复alt1不能外推原来无驱动的非零alternate；该场景当前最终grant不匹配会隔离，仍须后续恢复设计与实际验证，不为容易通过而新增整类设备拒绝。
- 新3脚本静态6项通过并独立复核；审查修正host-state成功queued取消须实际仍config1、完成赢才config2，以及raw probe期间peer NIC保持down防止IPv6/ARP干扰。dev01默认locked、产品192640c、dirty工作树的双QEMU仍FAIL：server在original-composite-state前置断言退出，helper/IMPORT尚未启动；实际Full-Speed设备和两个cdc_ether接口已枚举，不能归因产品。原始summary/serial/runner及准确前置源码快照保全于`/tmp/rustusb-composite-dev01-20261022`，正在细化前置断言定位，未放宽预期。
- dev02细化子STAGE后定位同一纯harness根因：随guest复制的BusyBox `tr`不支持`[:space:]`，sysfs的`bNumInterfaces=' 2'`保留空格而严格比较失败。宿主同binary与独立复核均纯文本重现原hex `20 32 0a`、显式ASCII空白删除后`32`，diagnosis原始输出单独保留；仅替换新guest的trim写法，配置/alt/接口数原预期不放宽。dev01/02均未进入helper/IMPORT，dev03再默认locked执行，尚无PASS结论。
- dev03的15条raw lifecycle全部PASS：真实配置1/2/0与alt0/1回读、barrier退休旧active/queued请求与新映射、Control/Interrupt STALL、模型clear失败保留、UNLINK实际cancel_won/config1、EOF清理及ISO明确拒绝；随后双原cdc driver/alt恢复和cycle1真实client内核两接口枚举、busy拒绝通过。但UDP client接收5秒超时，整轮仍FAIL，未报任何内容I/O通过。下一轮仅补私有data-netdev PCAP/ARP/routes/counter和失败snapshot诊断，不放宽原payload/超时或改产品；QEMU ECM OUT短包flush与xHCI聚合目前只是待证候选。
- dev04保留相同xHCI/UDP内容/5秒，新增两条私有data-netdev PCAP和失败网络/快照后仍FAIL。两端carrier=1，client ARP incomplete，真实bulk_out已9次/642B，pcap无单独ARP/UDP。root与agent独立按IPv6 payload length解析确认两份PCAP均有132B帧=90B IPv6+42B ARP(198.18.0.2→198.18.0.1)，证明QEMU旧模型拼接网络帧，而非等待PHY或仅靠timeout猜因。固定源码ECM OUT以单USBPacket<64B才flush，xHCI聚合TD触发不兼容；更新设计仅新harness改Full-Speed UHCI/来宾uhci-hcd，保留原>64B UDP帧、精确内容与5秒。xHCI原FAIL/PCAP/解析保留，产品192640c和旧脚本不改。
- 设计/产品/当前软件与诊断文档已普通push到d361ea3，远端SHA一致；四平台CI39795249579已排队，尚无通过结论。新QEMU脚本未提交/最终冻结，不能把此次push当作ECM通过。
- UHCI dev05/06保留原payload/5秒与产品，前7条raw通过，均在Interrupt IN EP1/len1的严格STALL actual0预期处FAIL。dev06只增加错误字段上下文，实际为status=-32/actual1/data_length1；QEMUstderr也明确STALL，但尚未独立判定长度来源。保留两轮原字节/脚本，下一步用仅来宾USBMON独立捕kernel完成记录，并核固定UHCI源码，不直接放宽断言或称真实设备传输了1字节。
- dev07仍保持actual0旧断言准确FAIL，仅来宾usbmon/debugfs显式抓合成测试设备；同URBtag的S Ii len1与C Ii -32/len1/data00独立匹配wire seq43的-32/actual1/data_length1。固定QEMU UHCI STALL跳过TD实际长度更新，Linux提交低11位0、完成先按((ctrl+1)&0x7ff)解码累加再判-EPIPE的源码链已独立核对；adapter没有加一。后续探针按该模型/kernel组合严格期待-32/1，并交叉比较USBMON与wire数据，保留原FAIL；不称设备真实转移1B或硬件partial-error通过。
- dev08默认locked/UHCI/原38B request与39B不同response/5秒完整PASS，两guest exit0、19probe通过；两周期分别RustUSB managed与官方usbip unmanaged（helper明确拒绝其detach，随后官方释放）。USBMON同tag S/C与wire的opaque单字节精确匹配，不硬编码00；双原driver/config1/alt0,1及全部pending/queued/inflight/budget0恢复，reaped87。生产默认stderr/JSON无本次nonce标记。dirty开发PASS/source/binary guards及全部7次开发FAIL均保留，尚不是最终clean验收。
- root额外独立解析两份1710B私有PCAP，每份均实际包含两次38B请求/39B响应，源/目标IP与UDP端口、IPv4/UDP校验及payload SHA逐字节相符；复用只读`evidence/verify-composite-captures.py`，dev08通过且原dev04明确拒绝，正/负向结果单独保留。独立最终harness审查无mustfix，原/当前三个script SHA均与dev08一致；准备统一归档提交后clean冻结最终ECM与原physical normal/hot回归。
- 四平台CI39795249579已实际全部completed/success：22.04 ARM job119880822548、24.04 x86_64 119880822938、24.04 ARM 119880823026、22.04 x86_64 119880823203。每格原生日志确认uname/Rusthost、89/113/14共216项0失败/忽略、两项4096B不drain背压且lease socket关闭；root复核四份原log SHA/长度和新job状态响应。`evidence/publication-composite-20261022.json`持久保存实际结果，产品crates/vendor/Cargo与192640c一致；最终QEMU仍待clean运行。

## Composite最终冻结与原物理回归

- 开发8轮及静态证据共116份逐字节归档，root核对长度/SHA和提交后的全部Git blob一致；130文件提交22d172d，产品/scripts与dev08一致。复核发现报告两处过强fixture表述，明确收窄：不可取消host-state ioctl的active窗口尚无专门确定性测试，queued取消赢和提交后EOF不能证明命中该窗口。
- `git push origin main`将d361ea3普通推进22d172d，之后在同一clean22d172d串行执行`python3 scripts/qemu/run-composite-qemu.py --evidence /tmp/rustusb-composite-final-20261022`、`python3 scripts/qemu/run-physical-qemu.py --evidence /tmp/rustusb-physical-normal-composite-regression-final-20261022`及同入口`--hot-unplug --evidence /tmp/rustusb-physical-hot-composite-regression-final-20261022`。三项exit0、6guest exit0，三次默认locked all-features release构建，开始/结束clean且source/binary guards通过；全部完成前没有仓库写入。sequence原始结果单独保留。
- ECM19probe通过，两周期分别RustUSB managed与官方unmanaged，helper拒绝接管官方端口后I/O仍准确；每周期UDP38B请求/39B不同响应，两个原cdc_ether/config1/alt0,1及同generation恢复，末pending/queued/inflight/budget0、cleanup_verified，实际reaped88。root独立校验两份1710B PCAP中各4组精确UDP、路由/端口/IP和UDP校验和/完整Ethernet边界通过，结果`evidence/composite-capture-final-verification-20261022.json`。USBMON同tag ffff892843b5b300的S len1与C -32/len1/data00和wire准确相符，仅为该UHCI/kernel组合报告值。
- 原HID/FTDI正常/热拔各15probe、各两个客户端导入周期和真实输入/精确串口通过，host输出分别70B SHA a40e2368e405f8bcc8b832ee7d42b3d471b41363046704a77e0834444ca36cb5与140B SHA 7d2284a6df1220f6daa02362b9cc884b2aca1a86386c045f8157f27b090c5e2c。热拔期间FTDI同receipt继续、旧HID Gone和重插不自动授权、剩余原driver恢复/末资源0均通过。三项CLI/helper SHA全部相同，分别c033d3931a159ad579977331140a6d327bd86939bfdd19cf794f304527d04db9和5d40d8e39286ab722a1abe7a09d1c68e0da9c5f3a7d2273f783b09f95cd64ae0。
- 最终三项原始runtime/runner共23份和源码快照、独立解码、清单/sequence共33份索引产物已归档，root独立核对全部长度/SHA及与/tmp原字节一致。另核96份当前软件输入与软件gate和192640c Gitblob完全一致；本轮最终证据之后仅修改文档/证据，未修改产品或脚本。最终文档提交自身SHA和push实况以Git实际历史为准，避免伪造自引用哈希。
- 独立第二复核78项全部PASS，`evidence/composite-final-independent-review-20261022.json`记录PCAP/USBMON/ownership/driver/config/alt/资源核对及复核时仅文档变动与run-start clean的区别；没有把归档后的脏文档误判为运行时源码变化。

## 继续：不可取消 host-state 操作的确定性观察

- 用户在Asia/Shanghai 2026-10-23要求继续，原完整goal仍active。fbc856c已普通push、远端main同SHA、clean；最终该head四原生CI job均实际completed/success，root逐份核原生日志216次执行、0失败/忽略及两项背压，原aggregate API仍显示旧in_progress，未伪造aggregate结论；单job事实保存`evidence/publication-composite-final-20261022.json`。
- 重新读取项目skill/AGENTS、架构和Composite恢复设计，并并行只读审查测试缺口。现抽象AsyncBackend不能覆盖physical Special；Slot包含真实nusb::Device，不为纯fixture伪造USB FD或新增unsafe。只为测试引入全套I/O façade会扩大生产改动，优先评估仅QEMU ptrace syscall-entry门：保持实际ordinary server/helper/USBFS，单独暂停blocking TID，其余runtime处理UNLINK/EOF。设计见`research/usbfs-host-state-race-design.md`，尚无实现或本轮PASS。
- 明确边界：syscall-entry尚未执行kernel ioctl，不能称kernel内部active；SETCONFIG有合法的前置旧claim释放，不和EOF最终恢复混淆；Special不是raw URB，snapshot usbfs_inflight=0不能证明其终态。后续采用门命中/真实terminal/slot与预算、FD及独立恢复观察作为裁决，保留真实QEMU错误。
- 设计及前一交付精确head的实际四job CI证明提交345163b；随后批准仅新增guest tracer、独立wireprobe、runner/guest-init四文件，三项并行实现。门控方案保留实际生产worker/nusb/USBFS调用，不修改核心、vendor或旧harness；强同步使用B排队取消回复及EOF前pending2确认实际请求被接受，开发阶段尚无新运行PASS。
- 静态审查进一步补两处确定性门：ARMED仅在tracer实际消费arm并记录匹配armed事件后回复，避免256-event batch未消费arm就执行A；CLEAR的后到C须额外PENDING RPC独立确认pending1/queued0/真实inflight1/预算384后才UNLINK，避免把queued取消冒称真实USBFS pending。tracer child exec前关闭所有非stdio继承FD，原编译缺头FAIL和修复静态输出保留。
- 新4文件全部冻结并逐SHA核对后，root首次默认locked all-features运行configuration/unlink dev01（nonce hs-dev01-config-unlink）。整轮FAIL：guest在baseline record_observation的jq变量`$label`保留字编译错误退出，尚未IMPORT/门命中；实际root helper、UID1000零cap server、ptrace exec与多线程ready、原CDC状态/节点权限及baseline资源0均已观察。两guest退出码虽0，runner仍正确拒绝PASS。完整原始证据`/tmp/rustusb-host-state-configuration-unlink-dev01-20261023`和runner log、当次4源码快照`/tmp/rustusb-host-state-dev01-source-20261023`保留；root核对源码与summary SHA一致，CLI/helper来源仍为之前已测相同产物。只修新guest jq变量/显式key，不放宽USB/取消/清理断言。
- dev01的同jq纯文本复现为exit3，改`--arg tag`与显式JSON键后精确预期输出exit0，诊断和原bytes保留。dev02默认locked重跑通过该baseline步骤，但client在coordinator_ready报EHOSTUNREACH113（3.079秒）；server管理e1000直到guest3.023秒才报告Link Up，未IMPORT/门命中。原4源码和rawlog完整保全，只新增两来宾管理网卡carrier==1有界启动观察；不改ARM后USB/取消/清理断言、期限或重发请求。
- dev03已实际完成9probe和两guest PASS/exit0：真实TID328/FD10配置ioctl-entry持门约0.733s，B排队取消-104证明worker处理取消，A释放后真实0/UNLINK0、C按新空map -32、同连接cfg1/alt0健康，最终同generation原cfg1/alt0,1/双driver及全资源0、tracer6021stops正常退出。但serial TTY使jq BEACONS输出ANSI，host严格JSON解析准确FAIL；不改判。独立76/76范围复核仅将ANSI去除作为明确派生诊断，原始6产物SHA前后不变。只在BEACONS生产端加-M，保持host严格parser；同jq PTY正/负原始字节与诊断保全。
- dev04默认locked配置UNLINK完整PASS，随后在同4文件冻结字节下串行dev05配置EOF、dev06备用接口UNLINK、dev07备用接口EOF、dev08 CLEAR_HALT UNLINK、dev09 CLEAR_HALT EOF全部PASS，12guest exit0、合计48条独立协议case marker。6次前后source/binary guard、实际guest ELF SHA一致；root另核96份软件输入仍完全匹配192640c/既有216次质量gate，CLI/helper与之前最终ECM相同。配置/接口ioctl真实0，clear真实-32且未伪造成功；clear后C snapshot实际inflight1/预算384再取消。EOF均真实接受pending2后进入closing/queued0、仍持512B及FD/slot，terminal后才恢复原配置/alt/驱动及资源0。
- 上述仍为dirty开发验证，三次FAIL及当次四源码、全部静态/诊断和6PASS正在逐字节归档，独立开发复核进行中；尚不能替代下一步同一clean提交六场景最终验收。

- 开发161份归档和全部三次FAIL保留；第二独立复核直接读raw、532项全部通过。最终审查发现首次ARM在TCP连接/DEVLIST/IMPORT之前已计时，单独改为25秒启动idle上限；所有ARMED之后RPC3秒、门8秒、wire2秒与USB断言不变。开发6PASS仍绑定旧SHA，新增启动修订的clean最终六场景待运行，不改写历史结果。
- root再次执行完整`bash scripts/check.sh`，exit0，89+113+14共216次测试执行、0失败/忽略，以及默认/全功能Clippy、release和严格rustdoc全部通过。新Python AST与目标BusyBox shell语法通过；原96份软件输入SHA逐项未变，原始输出与新harness四SHA见`evidence/host-state-software-validation-20261023.json`。

## Host-state最终冻结与六场景验证

- 开发测试工具、全部九轮原bytes及216次完整软件gate提交c921a278bc65341182c0badbb3898600faeb4408，root逐项核Git存储161份索引产物一致、工作树clean，普通push到指定SSH origin并以ls-remote确认同SHA。未修改生产crates/vendor/Cargo和旧harness。
- root单进程在上述clean head串行默认locked all-features构建执行configuration/alternate/clear各UNLINK与EOF六场景；全部完成前没有仓库写入或并行构建，6次exit0/12guest exit0/48独立wire检查，source/binary前后guard与guest四ELF匹配。25秒初始ARM修订包含在该最终源码，后续RPC3秒/门8秒/errno和资源断言保持。
- 配置/alternate真实ioctl0，CLEAR真实-32；取消期间B的-104形成worker witness，后到C不得越门，新空map或失败保留原map行为准确；CLEAR的C真正inflight1/384B后再取消。EOF先实际pending2再closing/queued0，保留512B及原FD/slot、DEVLIST隐藏/第二IMPORT busy，terminal后才恢复。每轮同generation/cfg1/alt0,1/双cdc_ether和末pending/queued/inflight/预算0、available/cleanup_verified独立匹配，server/tracer exit0。
- root最终六summary/Git源码及96软件输入复核通过，CLI/helper仍与前次最终ECM完全相同。最终85份产物已按长度/SHA逐项归档，root核73份原/tmp逐bytes和12份manifest相同；第二独立raw复核不调用runner.verify，572/572通过，准确区分run期间clean与归档后docs dirty。四平台CI39886203010和四job均实际completed/success，每格216次执行0失败/忽略及两个背压通过，原log与API响应按原bytes保留，见`evidence/publication-host-state-20261023.json`。此次syscall-entry证据不外推kernel内部active/成功clear、真实硬件或完整Phase2。

## 继续：显式USBFS release结果

- 上一交付54da6151已push且工作树clean，其最新四平台CI39887093631已实际全部completed/success，各216次测试与两个背压通过，精确记录`evidence/publication-host-state-docs-20261023.json`。原完整需求重新读取，Phase2其他缺口与所有后续阶段仍保留。
- 并行核固定Linux6.8 devio/driver和nusb来源后，确认普通USBFS不能靠claim-setalt-release保证None+非零alt恢复；不擅自宣布该缺口解决。另定位产品使用best-effort release造成真实RELEASEINTERFACE错误不可见，设计见`research/usbfs-checked-release-design.md`。先保留portable API兼容，新增Linux checked释放与首errno传播，并计划来宾外部unbind产生真实EINVAL的修复前/后验证。尚无本轮实现或新测试结果。
- 最新54da615四平台CI的四份原log和两个API响应另按原bytes归档，见`evidence/ci-host-state-docs-54da615-20261023/artifacts.sha256`。新测试只扩展四harness的release动作，原8秒门/3秒ARM后RPC保持；静态检查及新parser读取旧六clean原日志48项通过，未冒称重跑旧QEMU。
- 旧产品dev01 UNLINK和dev02 EOF默认locked构建均真实FAIL。dev01内核EXIT marker=-22但wire seq100 status0，独立probe在cases4准确拒绝；host早停缺完整末trace。dev02持久34event前缀为release -22→下一release0→SETCONFIG entry/exit0，后续adapter快照cfg2触发RESTORED严格失败，下游clientshortframe；未持久fresh独立sysfs2或最终恢复，不能补造永久泄漏结论。两run均缺after guards，原guest退出0也不改FAIL。
- 25份开发产物/共同dev01前置源码/静态来源已逐bytes归档，明确dev02复用同冻结快照。另一独立69项复核全部通过而原运行裁决保持FAIL；源码设计review另存。具体范围与缺失见`research/usbfs-checked-release.md`。产品实现候选在独立worktree并行完成纯测试，尚未应用本工作树；之后应用再开发与clean最终验证。
- 实现子任务中断后root接手独立worktree收尾：保留legacy接口，新增Linux checked one-shot/真实release与CONNECT errno，worker全部await/首errno和临时alt失败仍记录release。vendor19、后端41与Clippy通过；独立源码/许可证/70文件/精确patch重建217项PASS，无新增unsafe，仍346 token。候选提交5f92833 cherry-pick为main4e81a11。
- vendor严格rustdoc仅开tokio时，新旧产品均在同三个未启用futures_io链接失败，原日志保留；按该固定crate的docs.rs metadata开启all-features后严格rustdoc通过，未通过改断言或改原源码掩盖。root完整scripts/check.sh在clean4e81a11 exit0，89/114/19共222项0失败/忽略，全部Cargo gates通过，96软件输入及四harness前后SHA一致，随后普通push4e81a11且远端一致。
- 同一clean4e81a11串行十一项候选矩阵全部运行结束、无repo写/并行构建：release两项、原六host-state、ECM和physicalnormal共前10PASS；最后physicalhot原始串口在REINSERT_STALE标记中间插入kernel hid-generic printk，host准确按缺完整marker判FAIL，sequence保持FAIL。其余可证功能与最终scope待独立raw复核，不把guest自身PASS替代host裁决。先归档本轮，再分离物理测试ttyS0 kernel与ttyS1程序证据，产品不改。

- 首轮候选106份原始归档完成，独立913项复核完整性PASS但runtime仍10PASS/1FAIL；全部85输入SHA未变。双UART只改物理两脚本，34项静态/20负向通道检查和独立源码审查通过；dirty4e81a11热拔dev01实际PASS，15probe、140B精确FTDI、两guest0、完整重插marker及双channel beacon。准备提交冻结后新v2十一项，不覆盖首轮失败。
- 双UART开发原始/source/static共20份归档SHA逐项通过；第二独立raw复核113/113，完整marker、双通道guard/实际QEMU argv/source与二进制均相符。仅开发证据，准备clean v2；.gitignore同步允许保留新的evidence串口原始.log。

- 双UART修订及软件/CI/候选证据提交ab44a8445d35d9c738f6441c9a42c924c35b0107并普通push，远端一致；126份indexed Gitblob逐SHA/长度一致，工作树clean。root预捕全部12harness输入后启动distinct final-v2矩阵，严格串行且无repo写/并行build。
- v2实际在第六项alternate/EOF停止exit1：前5PASS，第6discovery_import EAGAIN11/cases0/elapsed3.540519493s，remaining5 NOT_RUN。两guest0不覆盖FAIL；未发ARM/A、缺最终trace/afterguard，不能断定准确I/O或产品无故障。旧候选FAIL与v2FAIL都保留。下一步仅细化启动I/O诊断及FAIL-only bounded收尾采集，WIRE2/RPC3/gate8和通过断言不变。
- 保持原wire2/RPC3/gate8的诊断dev01 alternate/EOF实际PASS（7case、两guest0），新增记录显示IMPORT header1.519263924秒、DEVLIST约12ms；不是旧FAIL根因证明。独立两文件诊断审查无mustfix。下一步减少tracer固定1ms轮询引入的观察开销，parent-only SIGCHLD有界等待设计先记录，仍不放宽成功期限。
- parent-only SIGCHLD有界唤醒替换测试tracer固定idle sleep，cc -Werror和独立signal竞态审查无mustfix；dev02实际PASS（7case、两guest0、IMPORT header9.122247ms）。这是两开发观察非benchmark，原v2失败准确调用仍未知。实际runner AST四项粘住失败/grace/原overall/正常完成检查及observe_io两项原errno/单次执行测试通过。生产96软件输入保持不变。
- v2部分矩阵63份原始/源码/sequence/清单产物及两诊断开发32份按原字节归档，root逐长度/SHA核对；独立开发复核208/208及三文件源码审查无mustfix。准备提交冻结v3，原两个FAIL均保持。
- 诊断/通知修订与完整证据提交19ffa119107e169765789ccdd071e860fca195af并push，远端一致/clean，95份indexed Gitblob核对通过。v3从全部12脚本前置快照串行运行十一项，无repo写/并行build；前10PASS，最后physicalhot在select-simulated-devices前置FAIL，整批仍FAIL。
- 原失败快照FTDI已出现但interfaces空、bNumInterfaces不可读和blockers非空；harness按count2提前break后被原严格完整性断言拒绝，未开始helper/IMPORT，不能说USB热拔产品故障。只计划把启动等待成功条件改为同原最终完整predicate，原100次/50ms不变。原所有FAIL保留，先fixture与单项hot后再freeze。
- 启动完整predicate四项fixture和shell语法通过；dirty hot dev03实际完整PASS：15case/140B/两guest0，独立raw113/113；仅guest loopbreak同原最终断言，不改变100/50ms或USB断言。19ffa11四原生CI39894149759也实际全completed/success，各222项+两项背压，root核原log/平台/计数并归档，不能覆盖v3 QEMU FAIL。

- 启动等待修订及v3/dev03/CI证据提交6e809a1c870e3ef84a7891eeca499b949366edf3并push，远端一致clean，132份indexed Gitblob核对通过；v3独立诊断与前10项复核随后定稿，保留10PASS1FAIL及明确未进入hot产品路径。
- root在clean6e809a1前置捕获全部12脚本，串行默认locked all-features执行v4十一项；全部PASS，113case/22guest0，运行期间无repo写/并行构建，source/binary guards及所有来源一致。root核同CLI/helper/guest、96软件输入与222gate完全一致；真实release-22、IF1继续释放、无SETCONFIG、原cfg/alt/driver和资源0均核。ECM两个私有PCAP独立逐帧实际38/39B和校验和通过；physical正常70B/热拔140B精确内容及完整双串口marker通过。
- root首次归档复核误把归档开始后docs dirty当成运行时dirty，在读取run前停止；已纠正为只允许新增evidence路径并逐冻结Gitblob核所有软件/scripts，原run clean和结果未变，说明保存root-review JSON。不是一次QEMU或产品FAIL。
- 后续范围审查rev1漏读ECM官方cycle2，root核源码/原日志后撤回过宽重复互操作建议；rev2与原版都保留，下一步优先SIGTERM合作停止的独立guest复现/修复，SIGKILL跨进程监督单列。未把原硬件G1/G2、ISO、内核内部active或后续安全/TUI/GUI阶段改为完成。
- 最终v4独立raw复核1047/1047通过；110份索引产物（97原copy、12manifest、1来源说明）全归档，历史378产物及全部FAIL未改。root核原bytes/SHA/Git来源后同步README/AGENTS/架构/测试/兼容/roadmap/逐项验收E20/E21及第三方边界，准备最终文档证据提交。
- 最终QEMU后核freeze6e809 CI39894822120实际3success/1failure：Ubuntu24ARM旧Native代际测试在200ms fake-command deadline失败，原代际断言未到；六原log/API已完整归档，其他3格各222项。未重跑旧CI碰碰运气；并行定位仅test fixture确定性，拟取消40ms时序猜测且保留真实timeout用例的严格期限，生产Native/USBFS与最终QEMU源不改。
- Native测试修订设计：普通fixture command5秒、专门timeout用例显式原200ms/kill-reap；settle150ms不变。更替代际由fake命令顺序NULL/mv旧dir/mkdir新dir后退出，取消后台argv/40ms时序猜测，保留原generation与路径断言并核精确一次detach。仅cfg(test)，生产默认15s/10s与QEMU源码不改。
- cfg(test)修订及原CI失败/定向31项日志提交37c2d61f84626329a4089524261ea60ae6299e64并push。root随后完整scripts/check.sh exit0，89/114/19共222项、0失败/忽略，96软件输入始终等于37c2d61且前后不变，pending docs dirty明确单列。release重新构建后CLI/helper哈希实际改变，root纠正派生复核中“应相同”的不当预期，按真实值记录；不将旧QEMU二进制重归新commit，本次test-only未重复USB矩阵。
- 37c2d61最新CI39895914376实际四job均completed/success：22ARM120226207665、24ARM120226207837、22x86 120226207911、24x86 120226208020。root逐原生日志核版本/架构、31组结果89/114/19共222项0失败/忽略、两项4096B不drain背压和lease关闭；原4log+2API+manifest已存，汇总publication-native-fixture-20261023.json。较早6e809 CI3/1不改判。
- 独立文档审查提出三处必须澄清，均已修正：-22/IF1/零SETCONFIG只针对release两项；各CI结果标精确提交；acceptance旧E15/192640c/22d历史描述不冒充当前，新增E22区分test-only后的软件与6e809 QEMU。准备最终文档证据提交与普通push；最终自身SHA和remote核验以实际Git历史为准，不造自引用。

## 继续：Rust服务SIGTERM合作停止

- 上一goal turn有实际进展：修复、验收及Native测试夹具修订均已提交。root重新读取完整原始需求/AGENTS/项目skill，核main cf9fd3d22588958486cbbbd771d2f29c2d1b7bcd和SSH远端同SHA，开始时工作树clean。完整目标仍active，未缩为现有测试。
- 源码确认两个Rust服务只select ctrl_c，main让服务拥有清理。并行只读设计产品signal入口、QEMU旧负向和本地child验收，未修改生产代码或执行新测试；方案见research/server-signal-shutdown-design.md，明确setup取消/提前注册、总await cleanup和SIGKILL能力边界。
- 固定Tokio1.54.1线上三个docs页面工具访问失败，registry也无.cargo-checksum.json；改用实际缓存crate archive SHA核Cargo.lock package checksum，再逐bytes核两signal源码。未用其他版本代替、未复制上游实现或添加依赖。
- 新local child工具先在root旧产品实际跑两项：ready dev01与blocked dev02都严格FAIL，实际Popen -15，无强制清理或重试；先有两profile真实IMPORT/cfg1/各pending1，两个data socket均EOF。ready实际无stopped；blocked独立OFD填满4096B/FIONREAD4096、child flags保持blocking、真实writer pipe_write后TERM且不drain。两次source/binary afterguard均通过，默认locked allfeatures构建，原bytes已归档。
- local源码仅一份在ready前捕获、blocked明确复用，包含106份与cf9fd3d Gitblob完全相同的产品输入身份；未虚构blocked独立pre-capture。新增stderr.log精确evidence ignore例外以保留空stderr和失败原日志。物理QEMU负向仍待新harness冻结，root未应用候选产品1762d65。

- 新QEMU两文件68项静态/synthetic与另一agent源码审查通过，先修main-ready同步barrier再冻结；未改旧harness/产品。root在177c7b4 dirty测试工具状态前置捕获7脚本+106产品Gitblob，默认locked allfeatures实跑old-dev01：startup真实helper backlog（排除Tokio selfpipe）下TERM wait143、原sysfs未变；主服务真实KEY1和35B双向、两lease各pending/inflight后TERM也wait143。完整20.04秒后同代际cfg1/alt0/root0600仍保留，但两driver实际null、无stopped；client0.19秒自主NULL/旧receipt消失后才清缓存。host整体52断言8FAIL保持，两个guest0不覆盖FAIL，source/binary afterguards与实际TXsink35B相符。
- 旧local负向31份索引原bytes经125项独立parser/归档复核通过（复核者为harness作者，非独立harness实现审查）；QEMU32份源码/raw/static/候选review索引亦已逐bytes归档。无stopped不支持末资源/reap结论，20秒观察不支持永久泄漏；产品候选仍未应用。

- 旧负向与3个新harness独立提交e4d54f8，63份indexed staged Gitblob逐长度/SHA一致。独立worktree候选1762d65经16项另一agent产品源码审查后cherry-pick为d39517b；仅4个CLI文件，私有共享Unix信号入口、prepare取消、cleanup所有权与错误优先级，未改engine/USBFS/native/vendor或依赖。四平台CI拟增加独立4-case ready/blocked×SIGINT/SIGTERM，复用已有allfeatures release且保存实际退出状态/summary；当前主树尚未运行修复后验收。

- clean934358fa457b71c5e3436cc544ca4031b656f147前置冻结138份Cargo/crates/vendor/scripts/CI输入，root完整scripts/check.sh exit0：89/114/19共222次执行、31result、0失败/忽略，fmt/两组Clippy/release/严格rustdoc全通过。随后同clean默认locked allfeatures本地ready/blocked×INT/TERM四项全PASS，source/binary guard通过；原SIGINT/5秒deadline两背压PASS。正常输出真实唯一stopped证明虚拟资源0；4096B不drain只证明exit0/双socket EOF，未补造未读取snapshot。
- 同clean934 root串行默认构建四QEMU：新TERM52host断言、原虚拟19probe、物理正常15probe与热拔15probe全PASS/8guest0，所有138输入每run后及归档前仍等于冻结Gitblob且repo clean，无并行共享build。TERM startup/main均实际wait0；主服务0.21秒观察原cfg1/alt0/usbhid和ftdi_sio同代际恢复，末stopped lease/pending/queued/inflight/预算0、真实reap增长，client先自主NULL/旧receipt消失才清缓存。原物理host TX70/140B内容精确。此时间仅本fixture一次观察，不作性能承诺。
- 构建feature实际分开：TERM/物理正常/热拔allfeatures CLI adedf4b1c90d5f1a95033d49fd72caa425860d192855d827d5a88eb0e654699e/helper837aec7045ab08d935e8d887b686db8e160a5c849fd236c653710c0320dca46b；原虚拟使用rust-backend专用CLI05dcacc24c173c8a2bbc032fe2f6438f2003f172de9e96cc3ae837c6f3a0f393/helper390ee333129f852a31869bbfef4394e4b2c85ed2eac05c47d32bc5229d5a2b99，未写作四run同ELF。旧runners不序列化afterguard字段，root序列显式逐138文件核验，不补造summary字段。
- e4d54f8/d39517b/934358f已普通push到指定SSH origin。CLI旧gh run list不支持commit过滤，改用只读head_sha API；实际CI39899971271 attempt1四原生OS/arch job均completed/success，各31result共222次执行、原两背压和新增四信号场景通过。四job raw log/API/固定GitHub源码及空stderr全保留，190项独立解析通过；CI仅打印完整summary和per-case原件SHA，未上传其单独stdout/protocol bytes，不声称已重建缺失原件。
- 旧QEMU177项独立raw复核通过但runtime仍FAIL；修复后本地138项与TERM181项为harness作者独立parser离线复核，明确非独立harness实现审查。TERM原host chardev payload未单独保存，只持久实际collector count/digest/exactmatch；未外推raw重放。root最初152份indexed原件逐SHA/长度及144次直接/tmp bytes核对通过，后续CI另27份索引。归档后的docs dirty不回写为运行期dirty。

- 三项旧虚拟/物理正常/热拔回归的独立raw复核227/227通过，49probe/6guest0，严格分开feature/ELF。复核首次用字符串排序重建fingerprint不匹配，改为生产Path组件排序后逐Git输入重建一致；这是离线复核脚本错误，不是一次QEMU或产品FAIL，原开发版保留。旧harness未持久evdev/TTY原件、FTDI独立sink payload和hot第三组receipt；对应事实依赖冻结guest精确断言/实际collector摘要/同slot计数，不冒充读取不存在的capture。
- 独立candidate的三份定向原log、精确1762d65 patch和未执行的备用draft亦按原bytes补存server-signals-isolated-candidate-20261023，原validation明确fmt仅工具响应、无单独rawlog，不补造缺失记录。

- 最终11份文档与证据的另一agent只读审查347/347通过、无mustfix。README/AGENTS/架构/测试/兼容/E23/roadmap和两服务指南已同步，保留完整项目范围；本机与四平台各222次执行、四信号+原两背压、四QEMU8guest0均归精确clean934，旧6e809矩阵和37c2d61历史CI不重归属。root再次核138软件/scripts/CI冻结文件未变、四索引179份原件全部长度/SHA一致、本地文档链接可解析，准备文档证据提交与普通push。

## 继续：USBFS 强制退出与重启协调

- 从 clean e0e6118 继续，完整原始需求、AGENTS、核心 skill 和架构已重新核对；上一轮文档证据已正常 push。当前读源码和独立并行审查，使用 writing-technical-design 与 technical-documentation，沿用现有 docs/research，不为套模板新增无关组件 skill。
- 先记录 draft 设计 physical-crash-recovery-design.md，再新增独立 SIGKILL / 首次 restart QEMU 负向；产品与原 SIGTERM harness 不变。重点核旧原状态是否随进程消失、新服务是否重置恢复基线。SCM_RIGHTS 的同 OFD 与独立 open 区别、pidfd 的有限保证均以固定 Linux v6.8 源码及 Linux man-pages 核对，不能把 FD dup 当作不影响旧 claim 释放的恢复句柄。
- 两份独立设计审查均无阻止负向测试的 mustfix，但明确尚非持久 guard/自动恢复实施契约：正常记录退休、交付不确定性、helper 重启/丢失或损坏记录、并发预留与能力不可撤销边界均需解决。五份固定 Linux v6.8 commit 源码逐 Git blob/长度/SHA 核对，报告及来源清单归档 physical-crash-design-20261023；没有复制 GPL 实现进产品。新 guest 预审发现条件调用函数抑制 shell set-e 的身份验证漏洞，已要求作者逐项显式返回失败，尚未启动运行。
- 设计单独提交 433697f。两个新 crash 脚本已冻结：host 6715b259d3503297ec18aa6daadec38eadbc1596f5f4b708e5ef59ce986b9f6e，guest cb648a50cae2cf86cb6b804a5cf250c32856783420a5be2535c745b97a83d923。50项静态检查及15项提取身份函数检查通过，另一agent源码审查无mustfix；条件身份检查改为显式return，root重新核SHA/Python AST/POSIX语法和产品/原TERM零diff。最初名为negative-before的提取已读到修复版，实际NOT_REPRODUCED，不冒称旧版负例；故障变体明确为派生示例。尚无本轮USB运行结论，准备单独提交后从clean源码实跑。
- 新harness单独提交1342a6a64cb6045052e49b701b773c4e00811fa8后，root从clean前置冻结140输入，默认locked/allfeatures构建并完成唯一一次新QEMU。runner实际exit1/恢复FAIL、证据收集complete、两guest exit0；55host检查中13fixture+35fact全PASS，恢复7项中4FAIL/3PASS，未改判或重跑碰碰运气。产品/原TERM源码不变，CLI/helper实际SHA仍为adedf4b/837aec7，全部输入/source/binary/commit guards通过，结束时仍clean。
- 信号前实际KEY1 down/up及FTDI各35B，两个lease各inflight1、预算2832，HIDpending1/FTDIpending2含queued1。已核普通PID303/start293发送一次KILL，wait137；20.06秒后同代际/config1/alt0/root0600但两个driver均null，旧stopped缺失且末资源保持未知。client先0.19秒自主NULL/旧设备消失再清理缓存。新PID12905/start2517首次无IMPORT重启确实ready，original/current均null且cleanup_verified=true，正常shutdown ACK/wait0/stopped后外部driver仍null；新epoch0不替代旧清理证据。
- 19份运行原件按bytes归档，新增实际FTDI host sink35B原件；RX和evdev hex在原client证据中。静态报告及原始提取检查压缩包亦保存，根逐140文件核归档前仍匹配。首次观测证明基线丢失风险，不推导永久泄漏或所有FD副本已结束；自动恢复与未决租约仍未实现。
- 另一agent运行后独立raw复核74/74通过，未调用runner.verify，逐Git140输入/7脚本/分层fingerprint、双向原bytes和evdev、所有epoch/sysfs/FD/VHCI事实相符；原运行FAIL保持。报告及可复核脚本按实际SHA归档，未重跑QEMU或扩展既有绿色矩阵。


## 继续：持久物理授权基线与合作结束

- 从 clean a484d11 继续，重读完整原始需求和项目约定。已有 FD 是可信服务的整设备能力，正常合作结束不以不可实现的全系统 FD 撤销为前提；实际自动崩溃恢复保持后续范围。先在 physical-crash-recovery-design.md 写明确实施契约，再按持久 Store、helper/客户端接入、后台任务回收及独立 QEMU 四部分实施。
- 新增 root 账本保存完整原配置/接口/alternate/driver、boot/receipt 和 OS owner，授权前落盘，同指纹未决禁止重新授权；正常 Finish 由原 owner 发起并独立核验。root-only pending 分页/只读 reconcile 不替代 USB 状态恢复。未知交付和初始化取消保留记录；不接受任意设备或状态路径 RPC，不新增依赖或核心 unsafe。
- backend close 跟踪/join 自有 monitor 和 supervisor，保存末快照后释放 registry，CLI 再 Finish 后输出 stopped。新增独立 journal 双 guest 场景保留旧 crash harness；当前仍是实现候选，尚未有本轮编译/QEMU结论，不能引用旧绿色矩阵作为此版本已通过。

- 首次定向编译暴露root新增schema的三个测试构造遗漏和目录组件双引用类型，原日志保留；修复后首次Store测试因tempfile目录继承组可写umask被正确拒绝，改为fixture明确0700，不放宽生产权限。随后daemon48、后端45及CLI定向测试通过。root补运行期同inode字节损坏/删除/截断拒绝、缺lock禁止另建锁域、缺driver字段不能冒充显式null，以及分页/协议/OS owner测试。
- 完整 `bash scripts/check.sh` 在工作树候选上实际exit0：default89/allfeatures142/vendor19共250次，31result、0失败/忽略，fmt/两组Clippy/release/严格rustdoc通过；98项软件输入前后SHA不变。待提交后从clean运行新QEMU与原正常/热拔/ECM/TERM回归；尚不宣称这些运行已通过。
- 新harness独立预审发现管理拒绝只查非零可能掩盖错误原因；改为按phase/UID核单个JSON ok=false及精确error。首次提取检查44/46揭示jq -e对空/多JSON流的宽松性，修为jq -se且length==1后46/46通过，原报告保留。两脚本各自755、原crash与TERM脚本未改。
- 产品与软件/审查证据提交 b75f14b，新两脚本独立提交 e0ac478；98项软件和21份index原件逐staged blob核验。clean e0ac478 首次新QEMU实际FAIL/exit1，source/binary/head guards通过，运行时clean。两轮真实HID/FTDI和正常退休/再ready已走到，KILL137后客户端自主NULL；客户端发送cycle-2-observed时server仍处原20秒观察，100次快速连接拒绝+0.1s间隔先耗尽，后续restart/admin尚未执行。完整raw保留，不能将两guest退出0当作验收通过；summary.error是之后wait10秒超时，原clientFAIL才是已知同步失败。
- 仅修新guest双方primary保存后的握手顺序：server观察保存后先通知client，client保存自主清理事实后等待，再显式detach并回告。20秒观察、重试数量、整体截止和所有USB/记录断言不变；host另保留首次失败marker避免后续收尾错误掩盖。原crash/TERM和产品不改，下一次从新clean提交重跑。
- 首次失败的独立raw复核进一步限定证据：正常/重开及两轮I/O、pending/disk原件存在；wait137由冻结guest严格断言后到达killed-post间接证明，但单独wait行及当前sysfs观察尚在guest临时文件，未到最终dump，不能冒充已持久读取。新harness补每次record/每个实际wait立即写独立串口行，避免后续协调失败丢掉已完成观察；这不回填旧run缺失数据。

- 修订时同时补齐管理员协调后的第三轮真实 HID/FTDI I/O 与正常退休，保持中间无IMPORT重开的对照。设计先追加此验收目的；最终是否通过仍等实际运行，不以ready替代恢复后传输。

- 同步/即时采证/第三周期修订与首FAIL证据提交933e720。root从clean前置冻结144输入，默认locked/allfeatures串行完成新journal、原物理正常/热拔、ECM、TERM五项QEMU全PASS/十guest0；各自184host、15probe、15probe、19probe、52host检查通过。本地ready/blocked×INT/TERM四项和原两背压亦PASS；七run每次source/head/clean guard通过。五项同CLI6c56ade7…/helper14f33dad…，未混淆feature来源。
- 新journal完整真实wait137与同代际driver:null原件已即时保存；跨helper重启相同pending两次新server均exit1/明确guard拒绝，普通UID/root未恢复拒绝严格核原因，guestroot恢复原driver后两record实际retired，第三KEY3/FTDI35B双向与正常退场通过。三轮各105B、hostTX原bin及RX/evdev原hex保存；这不证明自动恢复。
- 三个实现/测试提交已普通push到指定SSH origin，远端从a484d11更新933e720。四原生平台CI39908004666已实际启动，当前运行中；源码连接器只支持PR-triggered runs，push run由只读gh API定位，jobs用GitHub连接器观察。99份最终运行原件按bytes归档，独立raw审查继续，未将尚未完成的CI标通过。

- 新journal另一agent独立raw解析418/418通过，包含144 Git输入、真实三轮字节/evdev、旧基线和每个wait/权限/退休事实；原四QEMU回归独立575/575通过，含两份PCAP协议校验。复核parser的初始WAIT分类与ECM stderr空值假设已纠正并保存，不改任何运行结论。旧回归无逐grant磁盘原件，其退休只按冻结成功路径与真实退出/末事件判定。
- 精确933e720的CI39908004666四原生job均completed/success：22x86 120264284433、24x86 120264284710、22ARM120264284774、24ARM120264284822。每格250次/31result、0失败忽略、两背压和四信号通过；root对连接器解码日志和官方API做746项解析，核实际OS/arch/Rust host、checkout与所有signal输入Gitblob。11份CI产物已归档，未伪造工作流没有上传的per-case原件。

- 最终文档独立审查582/582通过、0 mustfix；root首轮4索引166原件长度/SHA/原tmp bytes与144冻结输入及链接804项通过，归档两份审查后共168 indexed原件，再核所有staged Gitblob及144输入一致。软件98输入也分别等于产品b75f14b和测试933e720。仅文档/证据交付，不重归属测试版本；准备正常push。


## 英文 README、项目展示网站与全局技能

- 用户追加要求以 fan-ziqi/rstop 的 README 和 GitHub Pages 展示站为参考。本轮先完成旧验收文档b02b97c并正常push，随后固定参考tree f2abbaac3ea4b44c77a926195e5f847e3ce43a6e、README blob1551effb…并读取站点HTML/CSS/JS；只借鉴信息结构，没有复制上游文案、品牌、代码或图像。先记录readme-website-design，再实现英文入口、原中文全文保留和Native指南提取；三处旧中文锚迁移，历史实验/失败与安全说明均保留。
- 新静态website使用原创横向/纵向USB/IP路径SVG、三后端说明切换、构建命令与复制反馈。明示开发预览和TLS/硬件/自动恢复未完成，不制作假的已实现GUI。独立审查16组、0 mustfix；13项Node VM交互逻辑检查不是浏览器/USB测试。现有ELF的15次CLI/help核对通过，未运行设备操作；软件/QEMU结论仍锁933e720。
- CUA实际浏览器观察：1440px主页面与375px手机均无横向溢出；后端点击、方向键/Home/End/循环切换与复制成功反馈通过。手机使用324×493.94px纵向图且资源已加载。临时移除script标签的/usbip-client/子路径fixture实际显示全部三个panel、构建命令和已加载资产；这是无脚本fixture，不冒充浏览器设置切换。一次多尺寸设置只改变当前选中的另一tab，返回仍375px，未把320/768请求当作实际通过。截图接口不可用，未获得全页像素截图；原SVG单独渲染PNG并目视检查。用户暂停中断的调用不作验收证据，恢复后重新读取状态，未重启USB测试。
- 新全局readme-site安装在~/.agents/skills/readme-site并由~/.codex/skills/readme-site符号链接发现，4文件；初版quick_validate和30项定向检查通过。另一agent进行真实README-only演练，范围正确；据其建议仅修1行，将参考站点源码调研改为按需，新quick_validate及8项定向检查通过，初版/演练记录均留档且不冒称重跑。技能已用于复核这次成对交付。
- Pages workflow仅上传website，PR只检查、main才部署，write权限限部署job，action使用官方核定commit。GitHub连接器无Pages配置能力，使用gh官方REST创建workflow发布源（实际成功，HTTPS已启用）；未改域名/其他项目设置。增加维护说明后本地111项链接/资产/SVG、JS语法及diff检查通过。28份审查、CLI帮助、技能原件/演练、图示渲染和Pages设置回执已归档readme-site-20261023。此时尚未推送网站或验证线上部署。
- 最后staged检查发现新Native指南多一个末尾空行；前一shell未以检查失败短路而已生成本地commit，尚未push。清理空行后重新核验整个staged差异，再amend本轮未发布提交；产品和所有原始证据不变。
- 网站/英文README提交f87b3ad已正常push；Pages39912070096的check/deploy均success，111本地检查在CI重现，部署URL实际返回。公开HTML/CSS/JS/3 SVG六项HTTPS200/MIME/bytes逐Gitblob完全一致；CUA在线点击物理tab/Home回Native及复制反馈通过，图片加载、1280px无横向溢出、console无warning/error。软件CI39912070077四原生job全部success，此文档版未重新解析各测试计数，不重归属933e720 USB数据。全局4技能文件与已提交快照一致且发现symlink有效；14份线上/Actions原件与限定的浏览器观察存入publication证据。

## 继续：独立 USBFS 恢复句柄实验

- 从 clean 60be074 继续原完整目标，重新核对需求、项目约定、核心 skill、架构与异常恢复设计。README、展示站和全局技能交付已完成；当前回到 Phase 2 的崩溃恢复缺口。三个并行只读审查分别核固定 Linux v6.8、现有 journal/worker 接口和最小 QEMU 实验，未启动并行构建或设备操作。
- 先追加机制实验设计：保留产品及原 journal harness，新 guest probe 分别持有独立 B 和 pidfd_getfd 的 A 副本，以 kcmp、真实 wait137、plain claim/release/CONNECT 与外部 C 占用作正反对照。实际恢复后仍需完整原基线、只读退休及第三轮真实 I/O。此时尚未实现或运行该实验，不把研究结论当作产品自动恢复。
- 固定 Linux v6.8 审查核七份源码 Git blob/SHA，确认 CONNECT 不指定原 driver、非零 alternate 在 unbind 时可能回0、接口可 claim 不等于设备级 EP0 已全部结束。集成审查最初把所有恢复都要求逐操作持久事务，讨论后收窄：明确基线恢复授权可支持同配置/alternate的CONNECT-only路径；跨配置仍需变更日志，旧只读记录不能静默升级。初版与澄清均保留，不把建议标成实现。
- 三个新测试文件组合复用旧 journal：六个唯一替换锚包含失败采证，保留原184检查和20秒观察。独立预审发现数字全空白可能误当0，编译前修正并以13个合法/非法输入实际验证；root严格C编译、生成init与片段sh-n、旧SHA漂移拒绝和宿主普通UID运行拒绝均通过。probe只在guest root及固定启动标记下打开两指定字符节点，FIFO只由root probe持有，不向普通CLI继承。静态证据已归档；当前尚无QEMU运行结论。
- 测试/设计提交43d6d43后从clean冻结149输入，首轮QEMU实际exit1/FAIL，source/head/ELF/clean guards全通过。新probe已独立打开B并ready，随后guardian_record内jq保留变量`label`导致编译错误；原第一轮真实I/O及normal/reopen关闭已记录，但第二epoch尚未启动，KILL/dup/C/恢复均未执行。后续client wait超时不是首因，两guest退出0不覆盖FAIL。26份原件已按bytes归档；只修变量名并补实际jq表达式检查，原断言、时限和产品不变。
- 独立首FAIL复核213项核验原来源与已完成/缺失范围；未保存的第一轮client evdev/RX hex不从路径成功推造。修复仅将jq变量改mark，JSON字段仍label；24处/19种filter的60次实际编译与正反fixture及附加静态检查共64项通过，含旧错误的真实复现。原失败和复核、修复检查一起保存，下一次仍从新clean提交执行。
- 修复/失败证据提交da764d0，root再次从clean冻结149输入串行执行唯一新QEMU，实际exit0/两guest0，237检查全PASS（原184+新53），source/head/clean/ELF guards全部通过。真实wait137后D仍持占用、两组共8EBUSY、B/C各自claim/release及B两次CONNECT返回1；完整原driver/config/alt恢复后probe wait0、只读退休和第三轮HID/FTDI通过，每方向105字节。产品ELF与933e720相同，26份最终原件/编排源码已归档，原首FAIL不改判。
- 两个测试提交已正常push到指定SSH origin，远端更新da764d0；CI39916267084和Pages39916267217已触发，暂未将未完成job标通过。当前补齐机制报告及验收E26，产品自动恢复仍未实现。
- 最终独立raw复核596/596通过，未调用runner.verify：逐149 Git输入、生成init、真实kcmp/18 ioctl/全部close/wait、完整基线和三轮KEY/RX/TX原件核验；交付文档另一agent审查无mustfix。root逐69份归档原件长度/SHA和144本地文档链接核对通过，另追加文档审查原件。现有产品/旧harness保持不变，不重复整套旧QEMU来替代本轮单项实测。
- 精确da764d0的CI39916267084已四原生job completed/success，Pages39916267217亦completed/success；连接器与官方API最终job身份/结论一致。五份发布原件及索引`publication-guardian-da764d0-20261023.json`已归档。本次未重新解析各Rust测试计数，旧250项仍归属933e720；站点源码未变。准备最后文档/证据提交，保持完整目标未完成，后续为受限产品自动恢复契约。

## 继续：明确授权的原驱动自动恢复

- 上一goal turn有实际进展：独立句柄机制、失败修复和完整证据已提交推送c8f2e90；本轮开始时工作区clean，重读完整原始需求、AGENTS、核心skill、架构和相关审查。完整目标继续active，不把机制实验当成产品恢复。
- 先写`physical-driver-autorestore-design.md`，确定默认空的精确设备恢复许可、私有disk2与旧IPC1分离、旧record永远manual、新许可持久化后才交付FD；samecfg/完整IF/alt才允许CONNECT，不改变现有正常多配置功能。恢复在owner确实结束后独立open B，实际blocking任务持有变更锁，helper停止join真实任务；当前策略仍按既有重启生效语义。三个只读审查分别细化journal、safe CONNECT/任务所有权及产品QEMU正向。

- 候选实现新增严格磁盘1/2迁移、显式恢复策略、旧IPC投影、单后台任务和独立B受限CONNECT。首次定向daemon all-features测试64项通过；正在执行完整软件门禁，尚未运行新产品QEMU。设计/实现审查无mustfix不等于运行验收；CONNECT的driver probe可能改变状态，必须全scope复核，不能承诺其没有USB副作用。

- 完整`bash scripts/check.sh`实际exit0：default90/all-features158/vendor20，共268次测试执行，31result、0失败/忽略；fmt、两组Clippy/release和严格rustdoc均通过，111个产品/vendor/check输入前后SHA相同。独立实现审查未发现mustfix；软件证据已归档，下一步从干净提交执行两个产品自动恢复QEMU正向及旧默认off回归，不能提前归属产品运行通过。

- 产品提交e34e309。新增两个产品QEMU正向模式，仅复用旧journal传输/采证，使用独立正向verifier；旧184断言未改。作者67项静态（含54种jq实际编译）和明确标识的构造parser fixture通过，独立21项源码核验无mustfix；静态阶段修复新jq保留字`$end`并补完整receipt.bus_number检查，尚无QEMU失败/通过。成功先以纯磁盘/sysfs观察，再读pending API与新grant；20秒是wait137后控制握手完成起的观察窗，不是从SIGKILL起的SLA。

- 从clean2ab9976串行冻结152输入执行七项QEMU全PASS/14guest0：两个产品正向161/205 host、旧默认off184、原正常/热拔15+15 probe、ECM19 probe、TERM52 host；本地四信号与两背压也PASS。九run各source/head/clean guard真，同all-features CLI375301a7…/helper1428d92b…。两正向各真实wait137、两lease attempt→retired、纯sysfs+disk先于API、新grant第三轮I/O，每方向105B。helper重启活owner窗约5.47秒后继续真实I/O；这不是逐syscall跟踪。141份运行原件已归档，独立raw审查进行中。
- e34e309/2ab9976已正常push到指定SSH origin；CI39921583807四平台运行中，Pages39921583884已check/deploy成功。此时不把尚未完成的CI或未运行的撤销/旧producer/外部C负向标通过。英文README、展示页及技术文档随限定证据更新；原历史失败保持。

- 精确2ab9976的CI39921583807四原生job全部completed/success，每格268次/31result、0失败忽略、两背压和四信号通过；独立CI审查194项核精确checkout/平台/183个该采证冻结输入/实际执行ELF。40份CI原件已按字节归档。旧五QEMU及本地回归另一agent独立raw审查1116项通过；初稿误要求文档交付时仍全树clean，保留原审查稿并改为核运行HEAD/152冻结输入，未重跑或改判运行。

- 两新产品正向另一agent独立raw复核559/559证据断言通过，未调用runner verifier，核实际来源、完整基线/许可、wait137、日志/纯观察先于API和六轮原I/O。复核器初稿以字符串排序代替Path组件排序误判fingerprint，保留初稿/修正稿；实际QEMU和产品不改判。归档无ELF本体，审查核build→guest→after记录哈希链；无逐CONNECT syscall/errno跟踪。新报告、E27及README/网站均保留这些边界。

- 最终文档独立30项审查通过、225本地链接无缺失；root逐206归档原件/staged blob和152运行输入核对，111软件输入亦保持。报告/README/展示站更新提交10dde54已正常push；Pages39923414349 check/deploy全部success，线上HTML/CSS/JS/3 SVG六资源HTTPS200且逐10dde54字节一致。全局readme-site四文件仍等于原归档，Codex发现symlink有效。五份发布/技能复核原件归档autorestore-site-publication；该时点10dde54软件CI仍运行中，完整产品CI通过仍精确归属2ab9976，未冒称新的CI计数。

## 继续：恢复许可与外部占用的实际边界

- 上一goal turn为实际进展：e34e309实现受限原driver恢复，clean2ab9976七QEMU/14guest与268次软件/四原生CI通过，英文README/网站/全局skill交付已推送至744d9be。本轮重新读取完整原始需求和项目约定，开始时工作区clean；完整目标保持active。
- 先写physical-driver-autorestore-negative-design，明确L真实旧helper产出schema1不追授、P撤销当前许可、C外部OFD先claim及失败不重试。L尾部必须人工恢复+只读协调，P/C才在明确helper重启后由产品恢复；不混淆手动与自动验收。root负责隔离旧producer构建，测试harness另分配，所有真实USB操作仅发生在可销毁guest。

- 旧producer在独立detached933e720和独立target目录实际locked/all-features构建成功，109源码输入/head/clean前后guard真；3769624B helper SHA2ce89408…0819b（实际新构建，不要求等于历史不同路径ELF）。当前产物未覆盖。只读预审确认C可先被ensure_eligible拒绝、P空恢复名单不启动scheduler、L保留disk1旧IPC形状；正式设计修正C释放必须在同一仍运行helper下观察不重试。744d9be的后续软件/Pages CI均已实际success，本轮不重新解析那份日志的测试计数。

- 当前产品完整scripts/check.sh再次实际exit0，111输入前后SHA保持，default90/allfeatures158/vendor20共268次、31result、0失败/忽略，fmt/Clippy/release/严格rustdoc通过；与此前产品输入相同。旧producer与准备/预审原件分开归档，尚未把新增C源码或构造校验当成QEMU运行。

- 新三个negative测试文件已作者冻结：C只CLAIM/RELEASE/close，严格编译（含runner的pedantic选项）及普通宿主UID首guard拒绝通过，没有宿主USB操作。三模式生成脚本/sh-n、80种jq实际编译、ANSI日志解析和旧producer来源核对共87静态项通过；明确标识的verifier构造fixtures为180/170/187项及wait0负例拒绝，均非guest运行。预审在运行前修复C日志创建竞态，补ready→pending原记录/owner不变及实际C执行SHA/FIFO事实；旧positive/journal文件仍逐字节等于2ab9976。pure primary只早于pending API/新grant，启动doctor/Status仍先调用。

- clean4565f7a从155当前输入+109旧源/ELF冻结后串行三QEMU首次全部PASS，6guest0、318/293/313 host检查；每run source/head/clean/legacy guard全真，同现有CLI375301a7…/helper1428d92b…。L旧真实producer的disk1保持manual，人工bind+只读协调后第三I/O；P/C只有明确重启helper后产品恢复/退休，C release后同实例5秒不重试。每场景双向105B/三轮HID，合计九轮。186份运行原件已归档，两个独立raw复核进行中，未把静态fixture计为USB case。
- 准备67f5b9d与harness4565f7a已正常push；software CI39928383971运行中，Pages39928383815已触发，尚不标未完成job通过。产品代码/111软件输入与e34e309/2ab9976保持，完整目标仍active；新增报告记录负向与各自合法尾部，下一优先级拟为真实USB数据通道安全设计，未标Phase2完成。

- 两名agent独立raw复核L615/615、P/C516/516通过，均未调用runner verifier；核当前155/旧109源、实际旧ELF、三helper epoch、immutable记录/窗口、C四真实ioctl及close/wait、全部三周期原I/O。P/C审查器初稿硬编码Cargo flag顺序误判两项，初稿保留并按实际顺序修；原QEMU未重跑/改判。报告明确人工bind与产品CONNECT没有syscall tracing，纯primary仅早于pending API/新grant。

- 精确4565f7a的software39928383971与Pages39928383815实际attempt1 completed/success，四原生格各268次、31result/0失败忽略、两背压/四信号通过；187个该CI采证输入逐Gitblob及真实执行身份/前后guard通过。219项独立CI审查及48份原件/原manifest已归档；初稿artifact误含.nojekyll的审查器单项误判按实际tar排除隐藏规则修正，初稿保留，未重跑CI。Pages按该版本115检查，本地新README为119检查；不混用计数或把CI软件当USB内核验收。

## 继续：真实USB数据通道安全设计与内核端点验证

- 上一goal turn为实际进展：三恢复边界首次QEMU通过、独立raw及四平台CI归档，交付已push a7406d7。本轮从clean重新读完整原始需求、项目约定和核心skill，完整目标仍active。技术设计沿用docs/research，候选架构保持proposed；未把硬件G1/G2或Phase2改为完成。
- 开始Phase3源核：实际registry核rustls0.23.46/tokio-rustls0.26.6稳定版本；并行只读审查Native socket-family/FD交付、principal→设备ACL/专用UID和双栈全数据QEMU。先写secure-transport-design，再验证匿名AF_UNIX pair能否承载真实kernelstub↔VHCI数据；本项不是TLS或安全远程连接验收。当前未新增TLS依赖/产品API。

- 固定registry下载的rustls0.23.46/tokio-rustls0.26.6/rcgen0.14.10已逐checksum核官方crate，核7个API/资源限制源文件。先在设计补TLS基础模块候选契约，再准备新非空transport crate：强制mTLS/TLS1.3/ALPN、leaf-pin principal+device ACL、16连接/握手截止/stream持permit。采用显式内置ring provider、关闭TLS12/默认provider及resumption/0RTT；候选尚未编译验收，不宣称USB链已加密。内核AF_UNIX单VM真I/O实验与此独立实施，避免有意建立可抢占的loopback端口。

- 新transport首次18项真实TLS/策略测试全通过；完整scripts/check.sh exit0，104个选定Rust/Cargo/check输入前后SHA相同，新增模块未接设备或CLI。独立冻结源审查28项无mustfix，显式补双方握手后ALPN检查，避免把配置ALPN误当强制协商；依赖清单实际148包。guest-only probe严格rustc及普通宿主UID首guard拒绝通过，尚未执行QEMU。预审中root误套旧%pK显示，作者核固定v6.8为%06u后纠正；这是审查纠正，不是产品或运行失败。

- 完整软件计数为default108/allfeatures176/vendor20，共304次、35个result，0失败忽略；前面独立18项不重复加进304。软件命令、104选定输入、依赖features、作者和独立源审查及probe静态/首guard原件已归档secure-transport-foundation-20261023；不是USB链TLS或QEMU结果。设计研究提交2ade292后准备冻结实现与机制harness。

- TLS基础库提交7bad8bf、机制harness f4774a6已正常push。首次clean f4774a6实际QEMU FAIL：两helper export成功，但导出后的snapshot退出，probe/匿名pair/真实I/O尚未执行，guest poweroff0不能覆盖FAIL；源码和ELF guards真、完整原件另存kernel-socketpair-first-failure-f4774a6-20261023。预审漏掉Native去配置移除接口及keyboard实际480Mbps两项假设，先修设计的phase metadata/speed契约，再修harness；不通过改超时或重跑掩盖。精确push软件CI39936932379仍进行，Pages39936932305已success。

- 精确f4774a6的软件CI39936932379四原生平台attempt1全部success，每格304次/35result、两背压及四信号通过；292采证文件/实际执行receipt、六connector日志对官方ZIP及554项独立复核通过。42份发布原件归档，Pages同版119检查/deploy成功；不把软件CI覆盖原QEMU FAIL。窄文档审查纠正初稿把24KiB请求+12B回复误写双向24KiB、设计状态未限定集成及root机制probe未启动措辞；程序和原运行结果不变。README/展示站沿全局readme-site指导同步基础模块与尚未集成的边界。

- 机制harness按已修设计分离硬件generation和生命周期cfg/IF：未配置只接受两属性真实0字节且0IF，正常attached/down后保持cfg1完整IF0/alt0、server IF无driver；分别传HID480/kernel3与FTDI12/kernel2。旧首FAIL/三旧源码和构造fixture单独留存。39项作者静态（含13实际jq及纯合成verifier）通过；root实际rustc严格编译、一个Rust单测遍历13边界及宿主普通UID首guard拒绝通过。产品Cargo/crates/vendor/check逐f4774a6无差异，不重复304软件；四端shutdown、NULL/Gone/AVAILABLE及普通unexport约束和时限保留，准备新clean QEMU。

- 修复24a424b从clean首次实际单guest QEMU PASS/50 host断言、guest0；109选定Rust/Cargo/QEMU输入、HEAD/clean及ELF前后guard真，default CLI538b2ef1…/helpercf7273af…与首FAIL相同feature产物，probe797bb889…。两匿名pair/四真实FD、一次HID KEY1 down/up和FTDI双向各35B，四次SHUT_RDWR后在FD仍持有时核NULL/旧local消失/stub AVAILABLE，再普通helper unexport完整原driver恢复。实际原件已归档kernel-socketpair-final-24a424b-20261023，独立raw复核进行；24a424b已正常push。无NIC/无TCP/无TLS，不能把机制当全数据安全连接，完整目标仍active。

- 最终独立raw复核40个分组谓词通过，未调用runner verifier；核109源、六冻结文件、13原件、现存ELF/内核/11模块和真实端点/数据/完整基线。probe ELF/initramfs已删除仅核digest链；原console speed not set及endpoint0 stall/-104保留，不宣称零内核warning或所有USB请求无错误。24a424b新software39938851575四原生job及Pages39938851717亦success，未重解析该新CI计数；f4774a6详细304计数原件仍独立。网站六线上资源逐24a字节一致，CUA新段落/1280与375无横向溢出及console无warn/error（这是浏览器console），全局readme-site四文件/发现symlink保持；不重做未改JS的交互验收。

- 最后十份维护文档244个本地目标/锚和站点122检查、JS语法、diff检查通过；保留较早243项QA而不混计。八个新索引共135份归档原件，逐bytes/SHA/原临时文件及staged Gitblob核对；109选定QEMU输入逐clean24a424b Gitblob仍一致。最终只交付文档/证据，不重归属软件或QEMU版本，完整原目标继续active；下一项为受限broker/helper FD所有权和实际USB/IP安全集成。

- 最终staged核验在提交前发现新归档中的.log被仓库忽略规则排除，前面目录add没有收入这些原件；校验中止，未产生最终交付commit。随后仅按八个显式索引补入缺失日志并重新核135原件的staged bytes/SHA，软件/QEMU/CI及原始文件不变；这是归档打包修正，不是运行重测。

## 继续：安全USB/IP产品接入

- 上一goal turn为实际进展：TLS基础库与clean24a424b匿名kernel机制已验证，交付push056341c。本轮从clean重新读完整原始需求、AGENTS、核心skill及架构/安全设计，完整目标仍active。先细化secure-usbip-integration契约，再实施真实engine、稳定key前导、可信配置与专用helper角色；DEVLIST独立连接、IMPORT→URB同TLS，Native及Rust两条最终路径均保留。标准DEVLIST不带稳定key的真实缺口通过有界映射帧解决，不以BusID冒充权限身份。

- 第一批已落共享key/前导、Rust engine实际TLS与原子ACL、root可信配置、broker专用USBFS授权和受监督Native客户端FD。开发验证core/protocol15、transport27及backend56通过；首backend测试有旧无效id夹具失败，保留原UnsafeDevice断言并改合法指纹、另验非法key拒绝。两轮workspace编译分别发现Error::State字符串转换及MutexGuard临时借用类型错误，原日志保留后修；第三轮allfeatures实际215次、0失败忽略通过，尚未完整软件gate或QEMU。独立审查发现Native变更队列等待不看EOF/stop，作者补biased取消与取得锁后复查，实际新测试覆盖。继续Native export/stub及完整broker前端，不将先通的Rust路径缩成最终目标。

- Native服务端真实export/stub及CLI已落地，管理codec提取到protocol::server共享，protocol/backend定向67项通过；删除该轮发现的一个unused import后，共享四crate严格Clippy通过。完整软件门禁首轮停在default Clippy四个等价条件风格错误，保留原日志并按相同授权语义修正，未把此前215项重归属新服务端。QEMU harness已隔离每端私钥、记录各自ramfs/payload及要求实际CRNG/RTC，静态夹具不计设备运行；Native harness扩展与独立源码审查继续。

- 完整门禁第二轮发现root新增Native管理CLI误调用不存在的helper函数，改用原invoke后第三轮scripts/check.sh exit0：default151/allfeatures225/vendor20共396次、35result、0失败忽略，121选定软件输入前后SHA保持；全部fmt/Clippy/release/严格rustdoc通过。另Native-only secure-transport CLI六项通过，feature tree无Rust engine。helper与共享协议/客户端两个独立源码审查无mustfix，新增英文secure-guide先标实际QEMU待验收，未改硬件或整体Phase完成状态。

- 设计db2d4c8、产品80a21f1及六模式harness41e2c67已正常push。首clean41e2c67 virtual/IPv4实际QEMU FAIL：HID已枚举，第二个serial attach进程提前结束，原因待完整诊断；trap缺少base64导致原始guest文件导出中断并退出PID1，产生真实kernel panic，不能称采集完整或设备运行通过。source/head/clean/binary guard与FAIL分别记账，142份已有原件归档保留；先修采证依赖再定位，不以guest退出或重跑覆盖首FAIL。精确41e2c67软件CI39950528568与Pages39950528577已触发，尚不标运行中job通过。

- 采证修复clean5f1e25b第二次virtual/IPv4仍FAIL，182份原件归档；两guest0、四guards真且全部已输出文件可提取，不能覆盖实际FAIL。日志确认serial helper I/O失败，但客户端cleanup错误掩盖原operation。先在设计固定两错误都保留，再修CLI结果合并与完成RPC优先于失效通知，actual close仍总await；第四轮完整软件gate再次396/35result零失败忽略，121输入前后保持。新failure trap只读采有界Native/physical持久文件，准备第三次诊断。独立固定v6.8确认USED早于device_add的窗口，但未仅凭相关性改判首因；Native代码仍未改。41e2c67四平台CI已独立169项复核通过，每格396及本地信号/背压原范围，原QEMU失败保持。

- 第三clean18817cd virtual/IPv4整体FAIL：cycle1真实HID key2 down/up、serial双向各35B和CDC echo、TERM/wait0/NULL/Gone/空journal及目录重用均通过；cycle2 HID失败。216份原件完整保留，operation NotFound与cleanup未确认同时可见，失败journal完整捕获为Intent；两guest0与四guards真不替代FAIL。没有逐syscall定位缺失属性。按已更新设计实施Native初始化Pending/Ready与首次目录代际固定，补真实临时文件夹具，由另一agent只读审；原Native调用方、10秒deadline和实际blockingguard/ownK边界不变。

- Native私有InitialObserver修复已落：首次USED row及目录dev/ino固定，只对never-seen初始化属性缺失等待，已见属性消失/权限/格式/身份变化拒绝，完成后同row和local/profile复核；无publicAPI或helper角色扩张。8项真实tempdir夹具实际执行通过；第五轮完整软件gate exit0，default159/allfeatures233/vendor20共412次、35result零失败忽略，123选定输入前后SHA保持（本次纳入vendor测试lock及toolchain）。作者与独立源码审查原件归档，准备clean修复版QEMU，不把先前396/CI重归属新代码。

- cleanf9558fc修复后virtual/IPv4两个guest真正PASS，两周期I/O、清理与复用均完成，但整体FAIL在pcap分类器未识别IPv6扩展头。238原件保留；独立raw生命周期86组和PCAP33组核实各自范围，125输入逐Gitblob一致，没有把分组数当USBcase或改判整轮。两原PCAP各有8个HBH→MLDv2，独立checksum有效；按官方RFC8200/3810/4861及实际子集修分类器，仍完整检查HBH后TCP、拒绝未知协议/分片/越界。38构造用例和14原静态回归通过、双原PCAP后验通过且哈希不变，待新clean六矩阵。精确f9558fc四平台CI39953971059亦独立169项核每格412/35result与本地信号/背压原范围，668采证筛选对象逐Gitblob核，不冒称其等于123软件或125QEMU选定输入。

- clean b83c931串行六矩阵首次全PASS：virtual/USBFS/Native各IPv4/IPv6，12guest0、每组125输入/HEAD/clean/ELF四guards真，统一all-features CLI7dddc286…/helper72415f41…。12真实I/O周期、24客户端import、Native8个stub；每周期serial每向35B，合计每向420B，不当吞吐基准。1518份最终原件/独立脚本已归档。两独立解析各完成生命周期492/安全350个审计谓词，不调用runner verifier；逐原始base64/sysfs/journal/证书配置/UID/caps/实际ELF与全外链TCP/TLS重组核，限制随报告保留。USBFS只有最终journal原件，不假称中间immutable字段审计；四个原FAIL全部原判保留。

- 英文README/中文入口、展示页及安全/架构/路线图/兼容性同步实测范围，旧Native图明确legacy；全局readme-site四文件仍等原安装且发现symlink有效。15份维护文件、303本地/GitHub映射链接、28表格及SVG经文档复核，修正五个历史措辞/目录独立连接等一致性点；站点128检查和JS语法通过。CUA本地1280/375的DOM无横向overflow、手机图切换及新版文案可读、console无warn/error；两截图接口不可用，不称本轮pixel/键盘/noJS/reduced-motion重验。精确b83c931软件CI39955861064四job及Pages39955860984均success，仅记后续status不重计f955的详细412。123软件输入仍保持；真实G1/G2、ISO/跨配置/崩溃与完整Phase3及后续阶段继续未完成，原完整goal保持active。

- 最终内容与1518运行/复核原件、9文档验证原件逐original/bytes/SHA/staged blob核后提交18907f1并正常push；125 QEMU输入仍逐b83c931字节相同。精确18907f1软件CI39959240032四job全部completed/success，Pages39959240010 check/deploy均success；线上HTML/CSS/JS/三SVG六资源HTTPS200、逐18907f1字节一致。CUA线上实际412/6/12文案与安全guide链接可见、图已加载、console无warn/error；这是DOM确认而非pixel截图。全局skill四文件及发现symlink最终保持，临时本地预览已关闭。八份发布回执另行归档；最后仅提交该发布记录，不重归属软件/QEMU/详细CI版本，完整原目标继续active。

### 2026-10-23：共享应用服务、独立会话控制与持久清理证明（实施中）

- 从clean `8e6baf5`继续完整原目标；重新读取原始需求、开发skill、当前日志与实际安全runner。先写`docs/research/application-service-design.md`，再实施纯core IDs/事实DTO、helper tracked与原子terminal、backend managed admission/定向stop，以及非root应用服务。实际job由库直接拥有，不启动CLI子进程。前台命令统一提取另行验收；本轮不宣称消除全部重复。
- 采用锁定Tokio1.54.1/rustix1.1.5实际官方crate源码；JoinSet shutdown会abort，因此owner supervisor必须合作stop并join。raw USB/IP v1不插ping，RTT/heartbeat仍无值；基础有界审计不等同永久归档/日志轮转，原完整Phase3及硬件G1/G2不因新API而完成。本节尚无本轮软件或QEMU通过结果。

- 三路并行实施后统一整合：真实service runner直接持有client/Native/virtual/USBFS任务；Rust managed monitor先持久admission再claim，独立attempt完成witness不以全局settle代替；helper同事务保存tracked terminal，旧schema保持严格迁移。源审发现canonical裁剪内存/磁盘分叉、Finished父job孤儿引用、fast-stop拒绝早EOF无audit和过期retry语义四项，修复后补六项真实临时FS回归。容量准入上限1MiB/总2MiB预留完成空间；写前容量拒绝保持healthy。Error后的真正cleanup经Recovering审计，operation与cleanup独立。
- 首次cargo check暴露trait/inherent claim同名解析，改为明确ServingBackend调用；第二次暴露catalog响应缺Deserialize及unused import，补纯DTO严格decoder后第三次check通过。Clippy依次修core测试serde错误映射、测试needless question mark及五处collapsible if，default和all-features均通过。首完整软件gate在默认daemon新8个fixture失败（umask0002使临时祖先group writable），150输入前后保持、原失败保留；仅给测试私有root显式0700，不放宽产品目录验证。daemon/service all-features定向实测随后通过；第二完整gate进行中。尚未运行新agent QEMU。

- 第二完整`scripts/check.sh` exit0：default196/all-features279/vendor20，共495次测试、39个result，零失败忽略；150选定输入前后SHA保持，fmt/两Clippy/两release/两rustdoc全部通过。Native-only CLI的dependency tree及`cargo check --features secure-transport`另行通过，确认有service而无Rust engine/nusb/rusb，不重复计入495。49份研究/交接/检查原件归档`docs/evidence/application-service-software-20261023`；首gate变化的3个fixture源码从仅反转chmod改动恢复，逐首run SHA精确匹配，明确标作reconstructed而非伪称原运行现场拷贝。先提交helper/Core基础`cdd14ed`，随后提交应用owner/monitor与软件证据。

- 应用实现及495软件证据已提交`2a0f8d7`。独立Native双guest agent harness首版预审纠正evdev读者open竞态、空历史vacuous断言、退出后late kernel fault遗漏、初始state.json未存在假设及过宽terminal proof/未核helper UID；原source/报告保留，最终author62静态、独立10静态及root生成PID1 dash/ShellCheck/help均过，均非QEMU。root自写预审初次只因exec3空白敏感literal失败，改regex检查器未改harness，两shell检查原已0。新154 QEMU输入含service全部16项，等待clean提交后首次执行。KILL点仅声称Connected/真实能力持有，不声称当时有pending URB/ioctl。

- clean `eaa1936` 首Native agent IPv4/IPv6及旧virtual/USBFS/Native各IPv4/IPv6共8项QEMU全部PASS，16guest0、全部source/ELF/HEAD/clean guards真；两agent154输入、旧六152输入。统一all-features CLI7a7f07ca…/helpercc6525a4…。agent实际RPC退出后I/O、单停HID而原serial再35B、client SIGKILL/wait137后Root精确proof协调/新attempt及server TERM完整driverbaseline均成立；每组server8sessions/2jobs、client4sessions/4jobs，12Rootterminal。独立raw生命周期两agent各967比较/旧六504、安全两family93分组均通过各自scope，非USBcase计数；两个agent client各8条告警及HID operation io/cleanup verified保留。
- 精确eaa软件CI39979747581四原生平台均success，每格495/39result与两背压/四信号，官方ZIP对connector job logs逐字节一致，195谓词另核150软件输入Gitblob。相同ELF本地四信号/两背压通过。2435原件/CI/独立parser已归档`application-service-validation-eaa1936-20261023`。安全独立审指出agent实际TLS JSON/pin/mode未采，只能核执行init/manifest/DER及实际Rootreceipt；原8PASS保持限定范围。随后仅两harness补actual配置/public DER/pin/policy与metadata，私钥只stat不cat/cp/hash；pin显式0644纠正宿主umask0002，旧secure脚本/产品不改。revision3 author72静态、独立预审与root三静态通过，150软件输入保持；将新clean重跑Native两family，不补造旧原件。

### 2026-10-24：应用服务采证补强与英文入口同步

- clean`cb08672` Native agent IPv4/IPv6补采首次全PASS，4guest0、154输入及四guards真，150产品源和两ELF逐eaa相同。每组386原件/206 guest base64文件，actual TLS JSON/manifest/policy/public DER/pin及server17/client18权限路径每epoch完整；collector不读/归档私钥内容或hash。独立生命周期两组各1420比较、安全两组101分组均通过各自范围，不计USBcase。新安全parser首轮本已PASS，随后仅修继承旧eaa说明并保留first-pass，不是产品/运行失败；旧eaa五份parser/报告SHA保持。787原件及独立脚本已归档`application-agent-config-final-cb08672-20261024`。
- 主要八场景按六前台eaa+两Native补采cb记16guest，原两eaa agent仅作额外保留，不算新增兼容。E31/英文guide、README英中、站点、架构/安全/兼容/测试/路线图及项目skill同步；源审纠正当前验收表仍412/E30、untracked physical disk2不能降1与两列表多一格三项。14份文档310本地/仓库映射链接、表格检查通过；32外链未做HTTP遍历。135站点静态与JS语法通过。CUA在项目子路径实际1280/375无横向overflow、图片与手机竖图加载、495/8/16文案和新guide链接存在、键盘左右切页/恢复成功、console无warn/error。截图接口仍不可用，仅DOM确认；本轮不称pixel/noJS/reducedmotion重验。CSS/JS/三SVG仍逐eaa相同。全局readme-site四文件与Codex发现symlink保持原安装字节。

- 最终794份新运行/QA原件逐original/bytes/SHA/staged blob核后提交`f9b6cde`并正常push。精确f9软件CI39987113008四原生job和Pages39987113149 check/deploy均completed/success；这是新版本状态，不重计或重归属eaa详细495/CI日志。线上HTML/CSS/JS/三SVG六资源HTTPS200且逐f9 Gitblob字节一致，CUA刷新实际495/8/16文案、新guide链接和已加载图，console无warn/error；非pixel截图。临时tab9已关闭、预览server2090901 TERM/wait143，线上tab8保留交付。九份发布回执另存`application-service-publication-f9b6cde-20261024`；150产品/152前台/154agent冻结源集合仍逐所记版本相同。最后仅提交发布记录，完整原目标继续active，真实硬件及完整Phase3和后续阶段未宣告完成。

### 2026-10-24：前台与托管共享实际 owner（进行中）

- 从 clean `9314aa0` 继续原完整目标；上一轮是有证据的实际进展，英文 README、展示站与全局 readme-site skill 已交付。本轮按项目技能先读架构、应用服务设计和实际前台/托管源码，先写 `research/shared-owner-design.md`，再提取共同 owner。CLI 保留信号/有界 stdin/stdout 适配，实际 helper/网络/设备生命周期由 service 一套 pipeline 持有；Foreground 不伪造 SessionId/WAL/correlation，Managed 仍真实持久准入。
- 三位 agent 分别负责 Native/client、Rust owner 和独立 mTLS 生命周期测试，另做交叉只读审查；root 独占 Cargo/QEMU/提交。保留前台 copy_bidirectional 排空与托管首 EOF、Native status2/4、旧 JSON/helper 格式和 5 秒输出预算。新增 USBFS accepted helper/initializer 不取消的安全边界，不宣称 kernel 硬阻塞有截止。
- 初次 cargo check exit0，指出两处提取后未使用的错误汇总 shim；删除无用 shim，统一使用保留 operation/cleanup/recording 的 JobOutcome。首 all-features Clippy 因 unused import 与大 enum 失败，原日志保留；测试专属 import 移入 test 模块，真实 Native receipt 装 Box 不改 wire JSON。第二 Clippy exit0。这些属于开发检查，不是最终冻结验收。
- 独立源码审查发现并修正 Native 非法 stdin 后 EOF 兼容、重复信号可能饿死已完成 owner、Rust WAL 错误误归 operation；另指出只读 Status 不能随 accepted-init 一起总 await，已恢复 stop-select，旧 helper-backlog TERM 场景将原样回归。Native use_lock 跨记录 ACK 及多 export 串行记录阻碍实际 close 的风险正在窄修。尚未执行本轮运行验收/QEMU，不提前记 PASS。

- 首完整 gate 停在默认特性 Clippy 的 `is_managed` dead-code，未执行测试；152 输入前后不变，实际两份待修源码在修改前保存并逐首 guard SHA 确认。仅给 Rust 专用私有 method 加 feature cfg；独立审查还在执行前发现两 Native 测试 key 少 `usb-`，只修 fixture，不放宽产品验证。第二完整 `scripts/check.sh` exit0：default212/all-features298/vendor20，共530次测试、39个result，零失败/忽略，152输入前后保持，fmt/两Clippy/两release/两严格Rustdoc通过。
- Native use_lock/记录 future 窄修已完成并独立源码复核：Begin/Intent/Attempt 不持锁；已有 stub 后 stop 保留原已poll记录 future，先实际 close/failed 标记/解锁，再等原 ACK 与 Closing/Finish；多个 export 收尾先全部启动再总 join，无 abort。调用方 stop sender 消失亦停止。准备阶段 Resource ACK 无限停顿仍是未决边界，不宣称任意 WAL stall 有界清理。
- 新真实 loopback mTLS 子进程五组合全部 PASS（四信号 ready/blocked + 约5秒输出截止）；memory DER 不覆盖可信文件加载/helper/USB。blocked 只证明有界退出与 TLS 数据 socket 关闭，ready 才由真实 stopped 证明末资源。旧本地四 signal 与两背压均 PASS，同 all-features release，四项 source/binary guards 真；约5秒输出截止仍非零错误。Native-only CLI secure-transport check/tree 通过，service存在而无 Rust engine/nusb/rusb，不重复计530。QEMU尚未执行，接下来冻结 clean 提交。

- 产品提交 `7f007a9`，103份/约3.69MB软件、研究、原FAIL与本地运行原件逐 original/bytes/SHA/staged blob 核后提交 `4d700c2`。该 clean 版本首安全 virtual IPv4 QEMU 实际 FAIL，matrix 随即停止，其余八组未执行；四 source/binary/HEAD/clean guards 真。两个客户端实际 VHCI 已枚举、helper disk1 两 observed records 正确，争用确被拒绝；新共享 client 沿用 managed 错误字串少了旧前台的 `is`，原 guest 精确 grep 失败并停止，server 等 host 控制亦失败。保留完整原 FAIL，修复 foreground 原字串、保持 managed 原字串和原断言，之后重新软件 gate 与另 clean QEMU 矩阵，不当环境失败。

- foreground 仅恢复原精确错误文案后，第三完整gate再次530次/39result零失败忽略，152输入前后不变；逐第二PASS只有client.rs这一项源变化。最终Native-only check/tree和同新all-features ELF的四signal/两背压亦PASS；新mTLS五组合另目录PASS。原第二PASS与独立52项raw复核保留原revision，未冒充第三run。73份最终软件/运行原件已归档。原 clean4d 单组FAIL的229份原件、154输入逐Gitblob和独立447项边界复核完整保留，不宣称未到达的I/O/cleanup通过；将提交文案修复后另名锁新clean矩阵。

- 修复后clean `b45c2e8` 九项既有QEMU全部PASS、18guest0：virtual/USBFS/Native×IPv4/IPv6六前台、两Native agent及原USBFS TERM。192总输入运行前后不变；前台154/agent156逐Gitblob、八组source/binary/head/clean guards及旧TERM独立fingerprint/前后clean/binary guard均真。九项均默认locked all-features构建，CLI ee66f1c9…/helper cc6525a4…实际guest/proc相同。未运行accepted initializer gate，旧startup仍明确helper acceptance=false。
- 生命周期raw独立方法复核3457比较与214批量guards另列、1030base64与2483运行原件核验，安全六前台350/两agent101分组另核，两类诊断数均非USBcase。复用历史parser且一名reviewer是harness作者，明确独立方法而非完全独立harness身份。准备工具的缩进锚/index eager-default错误保留，未放宽产品或原harness断言。2522运行/研究/审查原件约29.88MB归档`shared-owner-final-b45c2e8-20261024`。第三本地mTLS五组合52比较、后续新增writer原件的第四五组合58比较分别归原版本。
- 正常push b45后实际CI39997678019：Ubuntu22.04×两架构success（各530/39result、两背压四signal）；Ubuntu24.04×两架构FAIL（各216pass/1fail/20result），均是新TLS背压夹具exact pipe_write观察超时。四份官方connector完整日志保留；旧runner临时目录未上传，不补造缺失原wchan。官方Linux v6.17 fs/pipe.c显示anonymous write_iter走anon_pipe_write，结合实际24内核6.17/22内核6.8是源码诊断，原失败仍FAIL。
- H仅cfg(test)接受pipe_write/anon_pipe_write精确两符号，增加实际PID/TID/comm/wchan成功及失败原件，不放宽2秒观察、4096B/EAGAIN/no-drain、两租约pending、5秒输出和关闭断言，不增测试数量。I/N独立源审查通过。CI新增quality后always只读打印受控摘要/线程观察，不读key/USB payload且不掩盖主gate；root初次PyYAML查询器因checkout无name而KeyError，只修checker，原错误记录保留。
- 第四完整gate再530/39result/152输入保持，唯一软件输入delta是CLI cfg(test)文件；逐2638a8c Gitblob核，release CLI/helper逐b45产物完全相同，九项QEMU继续锁b45不重归属。第四mTLS29原件/三writer实际pipe_write已独立58项复核，不由本机6.8补造旧CI24值。测试+workflow提交2638a8c正常push，CI39999926362正在重新验证。62份最终fixture/software/旧CI日志及审查约6.47MB原件另档。
- 英文共享owner报告、README英中、站点与E32/架构/安全/兼容/路线图/技能同步；软件在修复工作树运行而输入匹配提交，QEMU才clean b45，两种归属明确。四平台尚未全绿时README/站点仅称旧495四格，等待新结果。全局readme-site四hash和Codex发现symlink保持原安装。初次新文案16文档337本地链接/21表格及137站点静态检查通过；CUA 1280/375项目子路径无横向overflow、图已加载、530/9/18文案与新链接、键盘左右恢复和console0，截图接口仍不可用，不宣称pixel/noJS/reducedmotion本轮重验。preview tab10关闭、server Ctrl-C已退出、viewport reset实观1280×720。CI归属文案随后有小修，最终QA另记。

- 新CI39999926362精确2638a8c四原生job全部completed/success：120573053833/120573054181/120573054318/120573054415；各530pass/39result/0失败忽略、五TLS及原两背压四signal通过。新always step原日志实际24两格各三anon_pipe_write、22两格各三pipe_write；这是新观测，未回填旧失败缺失值。官方API/connector完整解码日志及root独立parser12份约11.86MB先归档`shared-owner-ci-2638a8c-20261024`，无独立ZIP字节比较或runner/tmp原件重建声明。最后文案明确软件inputs匹配2638/QEMU锁cleanb45；实际最终HTML与已测DOM QA SHA完全相同，故复用其实际验证而非重做不变页面。最终16文档339本地目标/表格检查无错，136站点静态检查通过，外链仅计38个未全遍历。

- 2614份新增原件逐original/bytes/SHA/staged blob核后，与17份文档/技能/页面变更提交`a496384`并正常push。精确a496软件CI40002318487四原生job（120581305449/120581305804/120581305930/120581306010）全部completed/success；Pages40002318530 check/deploy全部success。此为最终交付状态，不把新的状态检查重计为USBcase，详细530/旧信号背压与真实anon/pipe原件仍归2638。
- 线上HTML/CSS/JS/三SVG六资源HTTPS200并逐a496 Gitblob字节一致，CUA刷新实际530/9/18、新报告链接、图加载和console0；tab8重新标deliverable，非pixel截图。17份发布回执原件已另归档`shared-owner-publication-a496384-20261024`。晚到的2638独立CI复核117/117首遍PASS，两parser/report原件加入原CI manifest：逐实际旧4signal的3143源entries/Gitblob与before/after aggregate核、各child ELF/UID/starttime/满管道/ready末snapshot，32个runner/tmp原件仅索引无文件不补造，旧两背压无逐child ELF guard只作前序workflow来源关联。原b45失败缺wchan继续保持缺失。
- 最后仅提交独立CI/发布记录与本日志，再普通push核远端HEAD和clean状态；软件152与site字节不变，QEMU锁b45/CI详细530锁2638/最终内容状态锁a496，各版本不重归属。原完整目标保持active；本轮属于可验证进展，真实硬件、accepted initializer/kernel硬阻塞、任意准备WAL停顿、完整Phase3及后续阶段仍未完成。

### 2026-10-24：实际数据连接的心跳与RTT（设计/实施中）

- 上一goal turn为实际进展：共享owner、530软件、旧九QEMU与发布，最后031f63d已push且本轮起点clean。重新完整读原附件/AGENTS/项目skill/架构与相关owner契约；原Phase0–7目标保留。继续明确未实现的4.4同USB数据链心跳/RTT/失联检测，不以独立Catalog pong或TCP keepalive冒充。
- 使用writing-technical-design的固定源码/具体接口研究方法，沿用docs/research且遵用户指示不自动创建组件skills/无关ADR。三agent只读并行审transport/h2、服务/owner/persistence和QEMU精确单流valve；root独占Cargo/QEMU。先写research/data-link-health-design.md，状态proposed，后续再改代码；目前无新运行验收PASS。
- cargo info h2@0.4.20完成（MIT、MSRV1.63、官方tag b0e890842ac16d3c76836fd4fcc06421acb15fa7），只下载registry缓存，Git仍clean后才写设计。采用成熟h2流控/PING候选，发现send_data入队不能当原IMPORT flush、零DATA的FIN另需fence、h2 WARN可回显未知ACK payload、业务WAL暂停会阻塞普通relay。设计记录真实TLS写/flush tracker、quiet API/poll/drop、独立driver与ended唤醒门槛；Ping opaque固定payload只能本地seq归属，不宣称wire nonce/USB健康。

- 实施前补冻结 API 的 Result、实际TcpStream tuple、strict schema2(5/15秒默认，1..60/至少2倍且<=300秒)、RX独立pump与真实TLS flush/FIN fence。Root实现可信配置/TLS v2包装/flush跟踪，N实现core DTO/h2 driver/quiet，I实现health/store/资源owner接线，H实现独立QEMU定向blackhole阀门。首次 transport flush 开发子集已启动，证据锁输入与原日志于`/tmp/rustusb-data-link-dev-gate-20261024`，不是完整验收，也不宣称通过。

- 首次flush开发子集在编译阶段FAIL（0测试执行）：driver select持有`&FlushMonitor`，该类型内有Send但非Sync的changed future，使tokio::spawn future非Send。保留原Cargo日志与前后输入；改为owner内显式可变借用，不提升为不实Sync保证，修后另名重跑。

- 修改非Send借用后7项flush子集PASS，输入前后不变；随后transport完整46项PASS（含3项真实TCP+mTLS、strict配置、满窗口PING与quiet baseline/负向），0失败忽略。firsttransport Clippy因显式可变借用触发两lint FAIL，改typed helper保持Send-only独占借用后secondtransport Clippy PASS，均保留原log。上述仍为开发子集，不含service/backend集成或QEMU。
- `scripts/dependency-inventory.py`按locked all-features重新导出158项；新增九个crate归档SHA逐lock checksum为真并登记实际许可文件。准备脚本首次因host Python无tomllib在任何比较前退出，后用受限lock字段解析重做且原准备问题留记录；不是产品测试失败。

- 元数据故障窄修与nullable必需字段加入后，core10+transport53共63项开发检查PASS，含真实GOAWAY(11)的129x1B/101空帧洪水、合法1024个及时消费1B、trailer拒绝；第三core/transport Clippy PASS。所有真实计数溢出/poison分类明确，未虚报terminal/cleanup。
- 客户端已取得helper句柄后的Resource/Prepared/Connected/sample ACK改为保留同一已poll future；断链/停止先join网络与实际helper close，再等原ACK。Native incoming同样处理；Rust非managed v2 greeting取消保留该attempt唯一claim completion，不用global settle或假durable admission。新增strict disk2、容量/ACL/分页、WAL停顿读与实际duplex生命周期回归，旧Session/Metrics/IPC1保持。
- 完整`bash scripts/check.sh`首次运行PASS：251默认+337all-features+20vendor共608次执行、39条结果，0失败/忽略。164项Rust/TOML/lock/check输入前后不变；实际run是dirty candidate031，随后逐source输入映射提交，不冒充clean运行。N独立重建357个qualified测试名（不是608个独立case）并核全输入。默认/all-features fmt、Clippy、tests、release、rustdoc及vendor均执行；工具链/最终ELF另收。
- 同一all-features CLI 76546440…/helper b4af4b5f…的本地四signal与两背压均PASS；Native-only secure-transport check/tree已独立记录，service/transport/h2存在且无Rust engine/nusb/rusb。新增英文操作指南`docs/data-link-health.md`，仍明确QEMU/硬件/完整Phase3未验收。旧九组agent verifier增强为strict disk2与raw-v1无伪RTT，不改IPC1/原设备断言。
- 传输/core/配置/研究与依赖单元已提交`85f2bb1`；接下来提交服务owner接线，再从干净提交运行旧九组兼容回归。新的heartbeat定向阀门实际harness仍在收尾，其软件夹具不是QEMU通过证据。

- 服务owner/health/操作指南与旧agent disk2 verifier提交`e29cc11`。164项实际软件gate输入逐Gitblob映射该commit全部一致，保持原dirty031run身份；同样本的Native-only check/tree PASS且无Rust engine/nusb/rusb。软件/研究/原compile与Clippy失败/本地runtime共101份约5.13MB归档`docs/evidence/data-link-software-20261024`，逐original/bytes/SHA与staged blob核验后提交。
- 在独立干净worktree锁e29cc11启动旧九QEMU矩阵，不受primary新harness并行编辑影响。当前六组virtual/USBFS/Native×双栈前台均PASS；两agent与原SIGTERM尚未报告完成。本项只记既有raw-v1兼容，新的v2/3方向定向blackhole与硬件未由此通过。

- clean e29cc11旧九QEMU全部PASS/18guest0，204输入前后稳定，八新harness四guard与旧SIGTERM独立guard均真；同actual CLI76546440…/helperb4af4b5f…字节与软件gate一致。I独立raw复核3879个记录谓词与244个额外batch谓词（都非USBcase）、1030个guest base64原件、2579个原件SHA，204Gitblob/166secure/168agent选定源一致；两agent各16份private disk2严格关联。旧证据及原FAIL不改；仍保留SIGTERM startup helperAcceptance=false、非pending Native KILL与USBFS无中间grantjournal等原边界。
- Native新v2定向blackhole harness七源冻结，H静态68项/55jq、AF_UNIX合成114观察与3边界/8phase正负fixtures通过，N源码交叉审查无具体阻断；这些不是QEMU。每方向fresh HID attempt、serial原连接持续I/O，双端health/tuple精确绑定，最后actualSIGTERM/Rootproof/完整baseline先于网络teardown。Native pending和独立procFD关联未由此证明。即将提交后以独立clean Native worktree首跑；primary另行实现virtual/USBFS四组，避免源guard并行竞态。

- 旧九组clean e29全部运行原件、独立raw审查与matrix runner共2628份/约29.99MB归档`docs/evidence/data-link-compat-e29cc11-20261024`，逐original/bytes/SHA/staged blob核对；诊断数3879+244与USB case严格分开。新Native7源已提交`e1529ea`并从独立clean worktree实际首Native-v4启动，证据目录`/tmp/rustusb-data-link-native-ipv4-e1529ea-20261024`；本句不宣告新case通过。primary另两份Rust backend guest扩展由I拥有、run分派由H拥有，均不影响冻结Native首跑。

- 新clean e1529ea Native-v4首实际FAIL：client-prepare中初始agent discover返回typed Io，尚无IMPORT/arm/drop，server只有2Available exports；valve44packet全部forward，两guest虽0退出但FAIL marker保留，四guard真。H121原件谓词/94base64与N copiedsource诊断分别锁原件，未推断已捕获RST。
- 新public Rust engine+真实TCP/memoryDER mTLS+production discover单项回归首次实际0pass/1fail，ConnectionAborted；原FAIL日志在`/tmp/rustusb-data-link-short-reply-regression-20261024`。据此先补设计约束，再修v2短management双方FIN/EOF+自然join，保留原失败与raw-v1/成功IMPORT路径。

- 首Native-v4 clean e152实际失败、独立原件/guest诊断、单项mTLS回归原FAIL与初始静态交接共324份/约3.67MB归档`docs/evidence/data-link-first-failure-e1529ea-20261024`，逐original/bytes/SHA/staged blob核。没有解密RST；44forward/0arm、无IMPORT是准确失败边界。N负责protocol有界借用FIN/EOF与Rust backend，I负责service保留IO/naturaljoin与actual服务mTLS fixture；仅service测试复用既有rcgen，生产依赖不变。新三backendharness源静态198/构造32已freeze，仍未作为运行通过。

- v2短management修复完成：共用借用IO的5秒FIN/EOF辅助；Native/Rust managed与foreground的Catalog/已flush2/4拒绝/准备失败短回复保留IO至自然driver join，取消/错误仍stop+join；成功IMPORT与raw-v1绕过新收尾。真实Native handler、client拒绝无helper、等待peer FIN时停止及publicRust engine回归均PASS；首次单项FAIL原件保持。仅service dev-dep复用既有rcgen，lock只增该dev边，158包版本未变。
- 修后首完整`bash -x scripts/check.sh`PASS，260默认+346all-features+20vendor共626次执行、39result，0失败忽略，165输入前后不变。原run为dirty3ca，随后逐input映射新commit不伪装clean。actual10步xtrace、toolchain与最终CLIbb6fb276…/helperb4af4b5f…另收；Native-only check/tree PASS且无Rust engine/nusb/rusb，同CLI本地四signal/两背压PASS。下一步固定clean源重跑六新组及旧九组，不以旧608/e29兼容PASS冒充此修复版本。

- 短management产品修复提交`6d933d2`。N独立核626新gate：366个qualified观察名、10条actualcargo xtrace、两rustdoc-Dwarnings、165输入与CLI/helper均一致；原失败test函数逐字未改，现在默认/allfeatures各PASS，608旧run与626新run另账。
- I/H完成新harness的virtual/USBFS分派，两新增guest/verifier文件与Native原脚本隔离；三backend静态198/构造32通过，Native生成仍逐e152相同。静态审查曾把Native非空pool初始化journal类推到Rustserver，复核helper条件后撤回，不是实际运行失败且未修改冻结源。virtual仅HID枚举闲置+真实CDC echo，USBFS不把linkbuffer冒作pending；per-lease与grant最终Finish分清。即将锁单一cleanhead运行六新/九旧共15组。

- clean934新矩阵前四项Native/USBFS×双栈全部PASS（12方向），第五virtual-v4实际FAIL，余下virtual-v6与旧九共10项未执行。不是Catalog产品回归：client在原nc-w2的3240负向探测即超时，未产生catalog/jobs/sessions，0arm；两个原network.txt显示NO-CARRIER，真实dmesg LinkUp在guest约2.767秒，valve完整转发9包且无TCP/SYN。H源码核drive每轮无条件poll阀门，不是最初怀疑的virtual漏pump。准备在新harness初始增加实际carrier=1/operstate=up的有界事实屏障并收原件，保留nc-w2/refused原断言，不以sleep或扩大超时覆盖。四PASS/一FAIL与所有未运行项原样保留。

- 626修复后软件/实际xtrace/165输入Git映射/工具与ELF/Native-only/本地runtime/作者与独立审查共71份约7.41MB归档`docs/evidence/data-link-management-software-20261024`，逐original/bytes/SHA/staged blob核。此前608、首mTLS FAIL及e152 QEMU FAIL继续独立归档。当前934矩阵四PASS/virtual readiness FAIL不改变626软件结论，也不宣告15组全通过。

- 934原矩阵四PASS/virtual准备FAIL/十未运行、源与ELFguards及只读诊断共2552份约29.36MB归档`docs/evidence/data-link-matrix-93428ce-20261024`，逐original/bytes/SHA/staged核。修正精度：valve9个socket完整forward并不代表peerPCAP收到9（server6/client8），失败拆解期间不能给完整PacketAudit结论；Host START为send时刻而非nc syscall开始。LinkUp晚于初始NO-CARRIER与负向探测超时相符，保留为事实支持的readiness候选，不凭跨时钟差宣称精确nc截止。新屏障将直接采carrier/operstate/uptime并有hostACK，原nc/probe门槛保持。

- 新harness readiness修复冻结：双端实际carrier1/operstateup及guest uptime原件由host核验后才ACK，双方ACK后再START；client原3240探测前再核实际up。最多200样本/10 guest秒仅约束初始化事实等待，原nc-w2/refused、1000/3000probe、原逐包重组与生命周期断言不变。H201静态/17构造checks PASS，N源审无mustfix，165 Rust输入和CLI/helper仍逐字等626；这些静态检查不冒充新QEMU结果。下一轮从新的cleanhead重跑15组，934原FAIL与四PASS保持。

- clean531e0c3最终15组全部PASS/30guest0：Native/USBFS/virtual×双栈六个新v2场景含18方向故障，原raw-v1九组随后另账通过。213选定输入与源/ELF/HEAD/clean guards稳定，CLIbb6fb276…/helperb4af4b5f…与626软件gate同字节。七轮35B的原serial/CDC连接双向各245B、新Catalog、逐attempt释放/再准入及最终TERM/baseline均有实际原件；virtual HID只枚举/闲置不称注入key，Native/USBFS idle/connected不称pending URB。
- H独立方法复核12361 PCAP（6298源ingress/6063实际peer到达/235定向drop）、168 TLS方向完整重组及实际/proc tuple→UID/inode/FD/PID/starttime/ELF关联；I独立方法复核18窗口/35个3000–3002ms端点timeout与原资源证据。两者均参与部分实现/脚本，不称独立实现；785098与31255是记录/逐字节谓词而非USB cases。串行/proc快照非原子、TLS server ALPN/PING未解密，最终部分server link.end=protocol但cleanup独立verified，不改写为全部干净传输。审查parser自身失败与更正保留，不改QEMU原判。
- 531e0c3已普通SSH push并核remote main相同。CI40029976284/attempt1四实际原生Ubuntu22/24×x64/arm64 job全success，每格260+346+20=626次/39result/0失败忽略，五memory-DER TLS及原四signal/两背压通过；actual24各anon_pipe_write、22各pipe_write。6597项source inventory逐Gitblob匹配，5939归档.rs不等于编译源数。无上传artifacts，不补造runner/tmp：TLS5缺逐child ELF/full前后原件、signal4有实际ELF、BP2仅workflow build来源，分开记账。
- 最终15组/源及构造夹具/两方法复核与parser历次共6403份约85.18MB归档data-link-final-531e0c3-20261024，CI官方完整connector/decoded logs及独立parser共23份约46.33MB归档data-link-ci-531e0c3-20261024；逐original/bytes/SHA及暂存Git blob核验。此前608/626软件、e29兼容、e152短回复FAIL、934四PASS/一FAIL/十未执行均保持原归属。硬件、完整G3及后续阶段未完成。
- 英文专项报告、README英中、网站及E33/架构/兼容/路线图/测试/项目skill同步626/clean531十五组/CI40029976284。N只读233本地链接及声明复核无数字归属阻断，指出acceptance五处旧“前台未统一/黑洞未验/RTT未做”措辞；root改为E32/E33增量与RTT性能基准、未覆盖WAN等准确范围。最终14文档351本地链接/表格、142站点静态及node语法/diff检查通过，34外链未逐个抓取。
- 沿用已安装全局readme-site及rstop固定参考，四源SHA/Codex发现symlink未变。站点修复深链接/URL/BackForward/键盘状态同步、正文链接下划线及selected对比；复制立即显示可手选提示，只有actual clipboard resolve才显示copied，拒绝提示保留。实际CUA本地1280/390无横向页面overflow、URL历史/Home焦点、CSP禁JS全部内容、正常复制逐DOM相等与Permissions-Policy禁复制回退均验证，console0；截图接口不可用，reduced-motion只核实际CSSOM而未模拟偏好，不称pixel/emulation。QA及源码审查/全局skill快照/初次push回执另归档data-link-docs-site-20261024，当前尚未将新页面宣称上线。
- 最终证据提交e01b092，英文报告/README/站点/技能同步提交f095ff5，普通push并核远端main相同且primary clean。精确f095的CI40032354147四原生job及Pages40032354233 check/deploy全部completed/success；这里只核状态，不重算626、不把531详细CI/QEMU重归属。165软件输入逐f095 Gitblob及当前文件一致。
- 线上六资源HTTPS200且逐f095 Gitblob完全同字节，CUA实际显示626/15/18及新报告，1280/390无页面横向overflow、两响应式图加载、ArrowRight/Home和URL同步、console0。viewport变化后首次立即观察图未就绪/首次click未改变选择，检查实际ready/bounds/console后再语义click成功；不推断未捕获原因或修改代码。reset下一次读取实观1280×720，public tab8标deliverable；临时tab11已关、两个exact-owned预览脚本PID核cmdline后TERM且确认消失。发布API初始pending/progress/final、六原资源、只读状态审查及browser记录归档data-link-publication-f095ff5-20261024。最后仅提交这些回执/维护文档和日志再普通push；软件165与网站六源不变。完整Phase0–7目标保持active，本轮为有证据的进展，硬件G1/G2、完整G3及后续阶段继续未完成。
- 添加发布链接后最终143站点/354本地链接、node及diff检查通过。首次补验沿用旧checker临时输出名，覆盖了临时351报告；新354先另存、已提交351归档从未改变，临时旧报告由该归档逐字恢复，另名checker再验354。两次新报告和恢复说明加入发布归档，不把补验回填为原351结果。

### 2026-10-24：准备期记录停顿下的实际停止（设计中）

- 上一goal turn为实际进展，e10b5fb正常push/clean；本轮完整重读原附件及AGENTS/项目skill、相关共享owner/应用服务契约。完整Phase0–7目标保持，不把README/展示页完成缩作整个目标。三agent只读交叉定位准备期记录等待，root独占Cargo/QEMU/Git。
- 源码确认Native Resource、后续设备Begin/Attempt及Available，USBFS Resource/后续Attempt、Rust Available，以及client远端IMPORT后的Attempt仍可阻挡实际资源清理；已有incoming/client后续retained-ACK不能外推这些边界。IPC授权stop有独立watch，但agent TERM仅入Runtime队列，persist停顿时无法fanout；补点须含sticky latch和晚注册control继承，不能只发一次快照。
- 先写research/prepare-recording-shutdown-design.md，明确actualclose与record finalize分离、全已接收资源先释放/全部Finish后再等原ACK、不丢accepted helper/initializer、不伪造terminal。QEMU计划区分Resource fsync门与accepted initializer syscall-entry门，旧Status startup acceptance=false继续不作为该证明。Tokio1.54.1两个versioned docs.rs访问失败，转实际已安装固定源码及SHA记账；目前尚无本项新运行PASS。
- 首软件RED在隔离dirtyc246只做生产函数私有提取+新断言，165选定输入前后稳定：queued-only service shutdown为3新FAIL/1旧PASS（当前owned Unix端点仍开、晚job和晚session漏stop）；独立sticky fanout修后同scope4PASS。另Native收到owner后的生产阶段/真实Resource oneshot首RED为0PASS/1FAIL，原ACK未放时2秒内actual owned Unix close不到达；晚拒绝后先完整fixture teardown再报FAIL，未绕root/helper、无kernel验收声明，原件分别在prepare-stop-red/root-fixed/native-red临时目录待统一归档。
- Rust/client修复首开发子集在另一隔离dirtyc246、166选定输入前后稳定下28PASS/0失败忽略/29过滤，含heldAvailable真实Rust listener/IMPORT lease与heldAttempt v1/v2实际TCP mTLS、原JobMessage oneshot保留；不是完整gate或USBFS内核证明。只读交叉审root sticky无阻断，发现client晚Attempt ACK后sender消失的窄竞态，已授权统一后置guard并增加pendingAttempt senderloss真实TLS路径；旧PASS快照保留。Native两owner/后续metadata门与QEMU首原产品负向仍在实施。
- 四开发run原件/独立方法复核、源快照/设计交接、C1新源但未运行记录及guest-only C首-Werror编译共80份约1.31MB归档prepare-recording-development-20261024，逐original/bytes/SHA/staged blob核对；37原件hash/661输入比较和17快照均匹配，数字非USBcase。C门只在host编译exit0未执行ptrace/USB；版本化上游网页失败与固定本地Tokio源SHA另收。root poison/C1新路径未计入先前4/28PASS，缺当时debugELF SHA不拿当前shared target补造。
- 首primary167选定源开发gate全部PASS：fmt、service默认55/allfeatures64、两service Clippy-Dwarnings，输入前后稳定，包含root poison/C1 senderloss与Native多owner新测试，原件另存不回填旧4/28。I只读核唯一actualclose/顺序闭合，发现identity mismatch的Protocol只到job、未传export session的归因回归N1；作者窄修prior_failure并加真实received-owner归因fixture，I另版delta复核无剩余阻断，原首PASS源及N1初报保留，修后尚待root运行。
- 新prepare-recording QEMU五源已冻结：同pool双owner的第二Resource state.next fsync entry及独立accepted GET_CONFIGURATION entry，job/TERM各可选；30秒guest-only门、15秒资源观察与生产预算分开，held-before/after由真实tracer核，旧FAIL也放同门/总join后实际TLS/VHCI HID+FTDI再用且不改判。六生成init与jq构造54静态检查通过，初collector把stat格式误识为jq的自查失败原件保留；未执行QEMU。root只将README示例kernel从90改为实有106，四程序源SHA保持作者freeze。即将只提交harness到仍含旧Rust产品的HEAD，独立clean worktree实际首负向；primary新产品继续单独验证，不混其ELF。
- 候选首完整bash-x gate实际274默认+361allfeatures+20vendor=655次/39result/0失败忽略，167源前后稳定；Native-only check/tree通过，CLI b7d610e0…/helper b4af…，原四signal与两BP同ELF通过。I独立方法核381qualified名/十cargo/两rustdoc/完整源及214原件，signal selector与fullgate恰有check.sh一缺项/166重叠，parser首KeyError原件保留；不把source selector差异改作产品FAIL。实际run为dirty21a96fa，未映射未来commit/伪称clean。
- root继续自审发现A/B：Resource被stop中断仍会吞已知identity mismatch；Native准备期local StopSession被any-stop判断升级为wholejob close_all，可能连关sibling。I/N确认，先前N1只核Ready分支/active局部stop，补充审查另存并承认未覆盖；655原PASS保留但不作为A/B验收。先补设计：已知错误不被stop抹去，local取消与jobstop/真实故障分清、空catalog listener继续；unsent reserved仍保守Unconfirmed，不额外helper申请造proof、不伪Verified，metadata局部未决与真实资源关闭失败分开。尚未修后验收。
- 原Rust产品逐e10相同、只加冻结harness的clean68cc3ad实际首NativeResource/job和USBFSResource/job均FAIL/各2guest0、四guard真、同oldCLIbb6/helperb4；Native161/USBFS175检查各仅预期三项失败（门内未释放/held判FAIL/serverFAIL），不是脚本初始化或编译失败。第二Resource的实际state.next fsync entry暂停期间，Native两export或USBFS两characterFD/两grant始终保留、Root terminal0；原同syscall放行rc0后才实际close/精确两proof/全baseline。新epoch实际KEY1 down/up与FTDI35B各向完成仍不改原FAIL。
- 两原QEMU的独立方法核1795原件SHA、1395base64、两174-source逐68 Gitblob；Native74/USBFS69门内采样before→after约15.312/15.460秒，非USB性能实测；StopJob CLI实际5秒超时exit1、门后actor_uid1000审计，门内无TERM。另一harness作者raw方法单独复核Native同结论，不另算run/独立实现；两方法时长起点不同保持各原定义。初独立review的vacuous Gone断言已更正并留initial：raw直接有VHCI NULL，Gone依赖冻结helper的精确NativeImportReleased proof、无独立最终sysfs stat。
- A/B及reserved未发helper的五新软件RED已在隔离dirty68+655完整产品源副本实际0PASS/5FAIL，167输入前后稳定；两个cargo筛选先identity1/local3、后reserved1，均Finished/Running后断言失败，先放原ACK/清理fixture再报错。证明本项不能以655绿色suite替代局部取消验收；Runtime NotStarted/proof guard保持。N修复现已shortfreeze含真实Native ACL允许的v1/v2 mTLS直接IMPORT4/busy2/emptyCatalog fixture，尚待root运行。
- service首55/64、candidate655全gate/同CLI四signal两BP、独立复核及五RED原源/命令共611份约17.41MB归档prepare-recording-candidate-655-20261024；两原QEMU及两raw方法、global harness原静态54→57和29定向fixture、初parser/jq空输入问题原件共2084份约17.24MB归档prepare-recording-red-68cc3ad-20261024。逐original/bytes/SHA/staged blob核；所有诊断数与USBcase分开，candidate仍有已知A/B不标最终通过。
- Native A/B修订采用私有局部取消结果/最多16项未决aggregate：真实资源与记录错误仍全局失败，reserved未发helper仍session Unconfirmed且最后job显错，不篡Runtime proof/NotStarted guard；local stop直接作用Catalog/IMPORT准入，保留busy2，all-local取消仍空catalog listener。已知identity先捕获，所有ACK分支保留原Protocol。I只读delta无阻断；root首修后service开发gate实际fmt/default62/allfeatures71/两Clippy全部PASS，167输入稳定，原五RED核心断言不弱化；真实mTLS管理fixture明确两Native key授权并验证停止4/忙2/两次空Catalog，无helper/kernel/可信文件loader验收声明。
- Local QEMU扩展冻结并通过70静态、I只读预审无阻断：native-resource/session与native-available/session独立裁决，原serial唯一终态/HID原receipt保持；同原job继续、实际目录仅原HID，serial真实Catalog-selection Permission/NotStarted/0attempt（非stale直接IMPORT）；原HID KEY1/正常detach后再局部停最后export，实mTLS空Catalog和原job/listener仍在，最后全局停/新epoch二设备35B再用。旧六生成init逐字节不变，旧global guest/verifier/C未改；root仅补README Local操作说明。尚无Local QEMU运行结果，下一步clean旧产品首负向和修后同门验证。
- A/B修订完整bash-x质量gate实际280默认+367allfeatures+20vendor=667次/39result，0失败忽略；167输入前后稳定，Native-only check/tree通过。actualdirtyb1de034不伪称clean；release CLI72adb291…/helper仍b4af…，同CLI原四signal/两BP真实终态PASS并167/ELF guard真。独立parser已重建十cargo/两rustdoc/387qualified名，仍审原件细节，不把655或旧68负向重归属此版。接下来将精确暂存源映射该run后提交产品，QEMU final仍未执行。

- 产品实现提交8ec570a；667实际运行的167项源逐暂存blob和提交blob全部同字节，仍保留dirtyb1运行身份。old baseline的detach屏障harness单独提交b529，再普通merge至2222，产品Rust167项未变。原b1 LocalResource失败保留4项断言；新cleanb529 LocalResource/Available两负向各仅预期3FAIL、4guest0，同原门放行后实际owner join、HID和FTDI新epoch再用完成。独立raw审查核2854原件SHA及1501base64，接受stub退休采样确早于最后exportStop；Available caller实际EOF、Resource超时均exit1，首审误统一预期timeout的parser原件保留。
- final矩阵clean2222首NativeResource/job FAIL、2guest0，其余22项未运行；真实门内第二saved sample已经exact2Root terminal/完整host基线，首sample仍过渡态。严格verifier正确发现首布尔proof由live journal重读、与保存root-observe1错配。原FAIL/420原件SHA及881项作者补充方法审查保持，修复限定采证函数显式读取每份已保存样本，不放宽产品、时限或proof规则。旧产品两global负向、三local负向与本项失败按各原revision保留，下一次完整矩阵另名启动。
- 667软件/同ELF本地runtime/开发子集及审查282份约14.37MB归档prepare-recording-final-software-20261024；旧三local及detach屏障/merge/方法审查3132份约26.67MB归档prepare-recording-local-baseline-20261024，逐original/bytes/SHA/staged blob已核。诊断、source entries、执行次数均不另计USB case。

- saved-sample修复1582f06仅两shell源；60静态含实际原件saved/live分歧与8生成dash/ShellCheck通过，N另一只读方法21诊断通过。strict verifier/C/dispatcher/时限未变；167软件输入逐8ec/2222/1582/5ff四组blob及实际保存源均匹配，QEMU matrix222 code输入逐1582 Gitblob匹配，不能据此重标dirty软件运行。修复与前述四类证据3917原件提交5ffdc6c，正常SSH push确认远端同head；CI40044605684四原生开始执行，Pages40044605647与其分账。首次jobs API envelope解析误用content字段产生parser错误，原响应和错误说明已另存，不作CI或产品FAIL。
- clean1582f06八新prepare QEMU全部PASS/16guest0，同release CLI72/helperb4，所有source/binary/head/clean guards真。H独立raw方法核3420原件SHA与7959诊断，含accepted ioctl16/GET_CONFIGURATION request3222820096真实entry/保持两FD/同调用return1后Finish、两local原stub退休先于lastStop/原同job空Catalog/新epoch真实KEY1与35B各向。178code输入与每run额外README范围分开，首补充parser误将README纳入matrix集合的错误原件保留；作者身份明确非独立实现。其余六health与九旧回归继续执行，未提前宣告23矩阵通过。

- clean1582完整23矩阵实际exec55568 wait终态exit0、46guest0，222输入前后不变，八prepare/六health18方向/九旧全部sameCLI72/helperb4且所有四guard为真。六health独立方法首版及显式baseline补强版均PASS，3632原件SHA、12383 PCAP记录/168TLS方向、35个3000..3002ms实际timeout及一被动IO另记；不是USB性能基准。旧九独立方法3897组内/262批量诊断、2594原件SHA与1030base64核，同源/实际生命周期闭合；不把复核数重算USBcase。
- 普通push的精确5ff CI40044605684四原生全部completed/success，每格280/367/20共667次、39rows零失败忽略；H独立155诊断核官方完整logs/response逐UTF8与四格10549-entry fingerprint/逐Gitblob、actualchild ELF receipts及两wchan/4096nodrain/readySnapshot与blockedClosure界限。第一次两日志由patch派生有换行差异，派生原件保留initial-derived，最终logs直接从官方content UTF8逐字节写并核；未补造缺失runner/tmp原件或ELF文件。
- README英中、site及维护契约同步667/clean1582二十三/CI5ff；全局readme-site原四源与Codex symlink重核保持。site145静态/Node检查通过，实际CUA本地项目子路径1280×800与390×844无页面横溢、两响应图加载、new667/23/46/18/报告链接、ArrowRight/Home及URL同步、console0。CSS/JS/资产逐f095相同，旧noJS/clipboard验证不冒称本轮重跑；截图不可用、reducedmotion未模拟。临时tab已关、viewport reset，owned preview57329 Ctrl-C后实际PID消失；线上部署仍待后续观察。

- 最终QEMU9536原件约117.68MB与CI44原件约94.53MB逐original/bytes/SHA/staged blob核后提交910a0d8，未改变产品或已测HTML。N文档14份/18一致性诊断/338本地target无阻断，root修正三处E31/E20过时“latest/未完成”并留历史归属；I专项英文147行最终停止写入。最终17文档396本地/仓库映射链接与表格、145site/Node/diff全部通过，37外链未全抓。本地QA/global skill快照与两作者交接另归档prepare-recording-docs-site-20261024；下一提交为最终英文README/site/契约与QA，线上状态不提前标成功。

- 内容提交ac4e802已普通push并核远端同head/primary clean，167实际软件输入逐ac4/current一致。精确ac4的软件CI40045915202四原生job120727480755/929/952/965全部completed/success；Pages40045915200 check/deploy成功。这里只核后续状态，详细667仍锁5ff、QEMU23仍锁clean1582，不重计执行或重归属。线上六资源HTTPS200逐ac4 Gitblob字节同，实际CUA1280×720/390×844无页面横溢、667/23/46/18与新报告、图加载、ArrowRight/Home/URL及console0；viewport reset下一读取实观1280×720，public tab标deliverable。发布初/终态官方回执、实际live原bytes、DOM观测与只读独立复核另档prepare-recording-publication-ac4e802-20261024。
- 本轮为原完整目标的可验证进展：实现准备期metadata ACK停顿下真实资源停止并补accepted initializer/local stop证据，英文README/site与全局skill保留已交付状态。真实硬件G1/G2、内核内部active/永久hardblock、无独立proof的Rust崩溃session、长期轮转及完整Phase3/后续阶段继续未完成，goal保持active。最后仅提交发布回执/引用/此日志并普通push，再核远端HEAD和clean；产品167与网站六源保持不变。

- 最后发布引用补验使用全新delivery-doc-links路径，17文档399本地target/表格、145site/Node/diff全部PASS；39外链未全抓。新QA原件追加本次尚未提交的发布manifest，再逐original/bytes/SHA/staged blob核，未覆盖先前已提交396报告。只读发布方法初版保留CIinprogress，终版26状态/来源诊断另文件确认四格/Pages成功，不重算667；七份consulted原件前后SHA保持。

### 2026-10-24：Phase 3 后续能力审计

- 上一goal turn属于实际进展：准备期记录停止产品8ec、667软件/clean1582二十三QEMU/四原生CI及英文README/site发布，最终bd6e08b已普通push且本轮检查primary clean。已重新完整阅读原附件，原Phase0–7与真实日常USB共享目标保持；用户QEMU选择不被改写为真实硬件G1/G2通过。
- 本轮重新读取AGENTS/项目RustUSB skill、架构及应用服务/安全契约，三agent只读并行核对无独立proof的Rust崩溃协调、4.11持久日志/轮转/诊断与G3凭据负向矩阵，root保留统一设计、Git/build/QEMU职责。当前尚未修改产品或宣告新验收；下一项按实际源码缺口选择，避免重复已通过旧矩阵或提前跳到TUI/GUI。

- 已选择原4.11缺失的真实日志轮转/读取，而不是重复旧矩阵：先落rotating-logs-design.md。固定tracing-appender0.2.5与file-rotate0.8源审计表明bare guard/普通writer不满足stdout、显式错误与实际join/安全open契约，因此沿成熟tracing/subscriber加薄的checked有界sink，无新外部依赖。设计复核先明确file-only模式与--log-json互斥，避免满stderr耦合文件进度；access flock每操作fresh OFD；授权logs错误/Busy也不回WAL；audit镜像在commit/state替换后、可失败healthrefresh前，无准入或proof权力。尚无此新功能运行PASS。

- 新共享rustusb-logging及CLI/helper/service接线已实现；启动file-only与--log-json互斥，外层同步main在Tokio/设备前打开logger，既有async所有者清理后实际join writer/两reader。schema1业务WAL/授权不变，新增ReadLogs/LogError仅原Audit权限；commit/state替换后再投递typed镜像。首dev actual22日志+4服务及CLIall-targets/check通过、177输入稳定；root随后加2严格JSON/游标负向及只读取消总join。
- 首完整gate在default Clippy的reader collapsible-if FAIL，0测试执行；窄修后单独allfeatures Clippy在CLI同类样式FAIL，均177源稳定/原日志保留，不放宽lint或测试。两处style-only改完后第二完整bash-x门禁全部PASS：308default+395allfeatures+20vendor=723次、43result、零失败忽略；177输入前后恒同。Native-only check/tree通过，实际CLI6cc828f3…/helperbb0ea543…工具/ELF收据保留；actual为dirty48a4，后续逐Gitblob映射不冒充clean。锁解析仅加内部crate及三条本地依赖，外部package版本/checksum全未变，license inventory159已更新。
- 真实subprocess runner首静态版本33317B保存；I发现finally单项采证异常可掩盖primary FAIL，H修为38064B/c876cf05，首版本、独立review、11构造采证异常与新source保留。此前静态检查不计真实CLI；下一步在隔离clean产品checkpoint运行新六场景和同ELF旧四signal/两背压，再做聚焦installed日志QEMU，不重复旧23当新增覆盖。

- 产品提交f3cf8d4，177 actual723输入逐暂存及commit blob匹配，actualdirty48a4软件身份保持。隔离clean f3cf8工作树不构建、外部选定同CLI6cc执行新6进程场景、旧4signal/2BP全部actualPASS，root exec1633 wait0、177/head/clean/ELF guards全真。新场景实际文件rotation/restart/cursor、两个follow信号、真实4096满stdout和RLIMIT_FSIZE errno27/同inode partial16均有原件；最后runtime日志失败保留真实stdout stopped资源0、最终wait Exit1与wait后读取的两路socket EOF；产品源顺序为实际owner cleanup后logger收尾，但不把父进程EOF采样时间前移到exit前，不能改判为USB操作失败或宣称硬件。H/I聚焦日志QEMU正在独立脚本实现，旧23保留原来源。四原生workflow新增同一真实六场景，fullRust723仍已测源码，无新额外外部依赖。

- 固定上游/设计/依赖/作者交接59份约1.33MB、两次样式FAIL及最终723软件761份约11.34MB、实际本地runtime与runner历次/方法复核323份约16.19MB，分别归档rotating-logs-research/software/local-runtime-20261024；共1143份逐original/bytes/SHA及暂存Git blob核验。N复核986诊断不计USB cases，其本人为record/worker作者，仅原件/统计方法独立；38新child只有11实际/proc ELF采样，27fast缺采保持。初始review parser两次错误原件与更正版分别保留。
- 精确41fce的CI40052402641四原生job均FAIL：官方完整日志显示新六场景前置检查要求HOME/.cache已存在，而运行器缺少该目录，新增case尚未执行。软件723与原四signal/两背压在此前步骤通过，不把整job称success。保留四官方response与逐UTF8原文，准备仅修runner安全创建缺失cache（不改已有目录mode、不降低产品路径检查）后另提交/另run验证。Pages40052402588为旧网站内容发布，和此次软件FAIL分开记账。
- runner缺失cache修复为39143B/957d1a40：先lstat完整受保护HOME祖先，再仅在缺失时mkdir0700，已有安全0755目录不chmod；symlink/不可信owner/可被他人写/并发恶意赢家均拒绝。N在普通UID1000的12个真实临时FS函数fixture及AST检查通过；没有执行CLI/Cargo/QEMU，未变HOME环境或真实HOME元数据，产品Rust177源与ELF不变。原38064B及静态记录单独保留；下一四原生CI按新提交另账。
- 41fce四原生失败的官方完整API/逐UTF8原log、原script/窄修diff、12FSfixture和60项只读判因共23份约111.10MB归档rotating-logs-ci-first-failure-41fce5f-20261024，逐original/bytes/SHA/staged blob核。原四job每格723/43rows零失败忽略、原4signal/2BP通过而新0case/0child；CI原FAIL保留，未从后续修复推翻。修复提交89ef420已普通push，替代CI40053603593另账在运行。
