# 明确授权的 USBFS 原驱动自动恢复

2026-10-23。产品实现 `e34e309`，测试提交及实际运行版本 **clean `2ab997673a5894099f8df770707a060da3735154`**。本轮两个产品自动恢复正向、五个原 QEMU 回归全部通过，14 个 guest 退出码为0；本地四信号与两背压场景也通过。完整目标、Phase 2 与真实硬件 G1/G2 仍未完成。

这次成功路径由产品 helper 自主恢复，不运行测试恢复 probe、管理员 sysfs bind 或 `physical-reconcile`。先直接读取磁盘记录及 sysfs，观察原基线已恢复、记录已退休，再读 pending API并启动新服务完成真实传输。旧 [SIGKILL 负向](physical-crash-recovery.md)、[人工协调验收](physical-journal-recovery.md)和[独立句柄机制实验](usbfs-guardian-mechanism.md)保留原版本及原结论。

## 实现范围

[实施设计](physical-driver-autorestore-design.md)和[操作指南](../rust-physical-server.md#可选的原驱动自动恢复)说明完整契约：

- 新策略 `physical_connect_restore_device_ids` 默认空，必须是物理导出名单的无重复精确子集。仅新授权记录可持久保存 `connect_original_drivers`；旧记录保持 `manual_only`。
- 私有磁盘1严格读为manual，首次新opt-in授权在FD交付前原子升为磁盘2，此后不降级；磁盘2缺失/未知恢复许可拒绝。生命周期IPC继续为1，pending返回旧record投影与当前页的附加许可摘要。
- 单后台任务核当前启动时策略、同boot原owner确已结束（僵尸未回收仍延后）和handoff结束。独立重开B，要求receipt、配置、完整接口和全部alternate一致，只对当前缺失的原driver发CONNECT。不发送配置/alternate切换、claim、detach或reset。
- CONNECT由内核选择driver，其probe仍可能发USB请求或改变状态；每次前后都观察完整范围。外部占用、变化或不确定保留记录，不通过解绑回滚。关闭B之后再次完整核验，才持久退休。
- 实际blocking任务拥有全局变更锁，取消等待不释放该锁；journal mutex不跨USB调用持有。合作退出停止安排新工作并join已开始工作，每lease每helper实例最多一次恢复尝试，失败须人工协调或明确重启后重新判断。

该能力不撤销其他FD，不证明设备级EP0已全部结束，也不隔离外部root的操作。策略编辑仍需重启helper生效。内核硬阻塞没有严格退出期限。

## 软件与来源

完整 `bash scripts/check.sh` 实际exit0：default **90**、all-features **158**、vendor **20**，共 **268** 次测试执行；31个result、0失败/忽略。fmt、两组Clippy、release和严格rustdoc均通过；111个产品/vendor/check输入前后SHA相同，并逐staged blob核对后提交产品。

新增覆盖默认/子集/重复许可、严格双版本迁移与损坏/不确定提交、旧record不可追授、磁盘2不降级、IPC投影与分页预算、同boot owner、完整scope拒绝、sibling恢复、错误/部分进展不退休、取消等待仍持真实变更锁及safe borrowed-FD errno。注入逻辑测试不等于内核行为或整个serve停止流程实测。

没有新增crate版本：Linux helper的可选feature复用固定vendored nusb 0.2.8，新增safe借用FD的同步CONNECT薄入口，保留原errno，不构造nusb Device/事件线程。70份上游文件、许可证和精确patch来源核对通过，新增入口未新增unsafe；底层既有unsafe不因此消失。Cargo.lock只新增daemon→现有nusb依赖边，100项依赖清单重新生成且内容不变。

[设计原件](../evidence/physical-autorestore-design-20261023/manifest.json)、[软件与源码审查](../evidence/physical-autorestore-software-20261023/manifest.json)、[测试脚本静态审查](../evidence/physical-autorestore-harness-20261023/manifest.json)分别记账。构造parser fixture只验证验收器；不是实际guest结果。静态阶段发现新jq `$end` 保留字，运行前改为 `$finish`，没有将其登记为未发生的QEMU失败。

精确2ab9976的[软件CI39921583807](https://github.com/tiansongyu/usbip-client/actions/runs/39921583807)已四原生平台completed/success：Ubuntu22.04/24.04 × x86_64/aarch64，每格268次测试、两背压与四信号通过。独立CI审查194项通过，核实际checkout、平台、原始日志、183项该CI信号采证的冻结输入及ELF执行来源；CI信号脚本的`--skip-build`按本job先前成功all-features构建关联，不冒充脚本自行构建。四格共1072次单元执行，不能把它们算成1072种测试。[发布原件](../evidence/publication-autorestore-2ab9976-20261023.json)保存实际日志、连接器及官方API结果。USB内核路径仍只归属下述x86_64/QEMU环境。

## 实际 QEMU 与本地运行

宿主普通UID、Ubuntu22.04.5 x86_64、Linux6.8.0-106-generic；guest使用同版本内核、隔离socketpair网络及合成HID/FTDI或ECM，未操作宿主USB。每个QEMU runner默认执行locked all-features release构建。编排串行冻结152项输入，九次运行均source/head/clean guard通过。

| 场景 | 实际裁决 | 边界 |
| --- | --- | --- |
| owner-death | 161 host检查PASS，两guest0 | 一次SIGKILL/wait137后自主恢复、退休和第三轮真实I/O |
| helper-restart | 205 host检查PASS，两guest0 | owner存活时helper合作重启，继续真实I/O；其后KILL自主恢复 |
| journal-default | 原184 host检查PASS，两guest0 | 默认关闭，磁盘1/manual、跨重启拒绝、管理员恢复与第三轮I/O |
| physical-normal | 原15 probe场景PASS，两guest0 | 原正常传输/清理 |
| physical-hot-unplug | 原15 probe场景PASS，两guest0 | 热拔、FTDI继续、旧代际不重授 |
| physical-composite | 原19 probe场景PASS，两guest0 | ECM原配置/多接口恢复与互操作 |
| physical-sigterm | 原52 host检查PASS，两guest0 | 活动真实I/O下合作SIGTERM清理 |
| local-signals | ready/blocked × INT/TERM全部PASS | 本地虚拟服务四场景，不操作物理USB |
| local-backpressure | SIGINT/deadline两场景PASS | 输出阻塞仍释放虚拟租约 |

host检查数含原件/轮询逐项校验，不能当成USB设备类型或独立USB测试case数。

所有七项QEMU使用同一构建来源和ELF：

- CLI SHA256 `375301a7cf7b79f9d7a8ec24a43232ca98df0063e395c91b8cdf9a1e9f724e3d`
- helper SHA256 `1428d92b1fc5ff867604b5ce950e35a914e3b70d2659ce80d9432a61fbc6a828`

两个新正向都经过正常第一轮、无IMPORT的重开关闭、第二轮真实I/O/active pending、一次KILL和第三轮真实复用。每场景三轮HID key-down/up，FTDI每方向3×35=105字节；原host TX bin、client RX及evdev hex已保存。旧被杀epoch末资源不可观察，仍为null，不借新epoch的0冒充旧资源已释放。

owner-death中的被杀普通服务PID1282/start578，真实wait137；只读观察起tick752、成功primary781，窗口结束783。helper-restart中的原helper PID291/start291合作wait0，新helper1747/start727；同一owner PID1288/start573在tick762–1309期间保持存活和原磁盘记录、usbfs占用，之后仍完成第二轮真实I/O，再wait137。其恢复观察起1358、primary1399、结束1401。tick单位为厘秒；20秒截止从wait137及控制握手完成后起算，不是从SIGKILL瞬间起的SLA，也不是性能基准。

存活窗口“未尝试恢复”的证据为实际helper操作日志和源码控制流，配合独立磁盘/sysfs状态及继续I/O；没有syscall tracer，不能宣称逐ioctl跟踪。两新场景各保存两record的真实`attempt`→`retired`，成功primary早于后续pending API/新授权。原完整driver是`usbhid`、`ftdi_sio`，配置1、interface0/alternate0，代际不变。

两新正向的独立raw复核559/559项证据断言通过，未调用runner验收器：逐来源、完整原基线与持久许可、真实wait137、helper操作、primary/API顺序及六轮HID/FTDI原数据。首次解析器以字符串而非Path组件排序重建指纹导致两项误判，保留初版并修正；产品及原运行未改。归档没有保留构建ELF文件本身，审查核的是实际记录的build→guest→after哈希链；没有逐CONNECT ioctl/errno跟踪，不外推跨配置/alternate或任意FD撤销。

五个旧QEMU回归及本地信号/背压的另一agent独立raw复核1116项通过，未调用runner.verify，直接解析journal三周期、ECM PCAP和本地信号原协议。旧normal/hot/TERM没有本轮新补host原字节，blocked信号缺末cleanup快照、旧背压仅collector JSON，均保留原证据边界。审查器初稿末尾错误要求交付文档编辑期间工作树仍clean；原稿保留，修为核运行HEAD和152冻结输入，不改任何运行裁决。

全部运行原件、脚本和编排来源见[最终证据索引](../evidence/physical-autorestore-final-2ab9976-20261023/manifest.json)。两个正向实际命令为：

```bash
python3 scripts/qemu/run-autorestore-qemu.py --scenario owner-death --evidence /tmp/rustusb-autorestore-2ab9976-20261023/owner-death
python3 scripts/qemu/run-autorestore-qemu.py --scenario helper-restart --evidence /tmp/rustusb-autorestore-2ab9976-20261023/helper-restart
```

## 未完成范围

- 新记录许可被撤销后的QEMU负向、真正旧schema1 producer迁移负向、helper停机期间独立C先claim再启动的产品负向仍待运行。默认off已由原journal回归验证，纯逻辑拒绝与机制实验不能替代这些新组合。
- 跨配置/alternate、不同boot或热插拔代际不会自动恢复；需保留记录并人工协调。无原driver的非零alternate、任意FD/EP0活动、内核内部active硬阻塞、断电耐久和全部恢复异常矩阵仍未完成。
- 两端真实设备、Ubuntu24.04内核USB和aarch64内核USB路径仍未验证。软件CI平台与USB运行平台分开记账。
- TLS/认证、完整TUI/GUI、性能矩阵、存储/ISO及Windows USB仍按原路线分阶段推进。
