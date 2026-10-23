# 原驱动自动恢复：下一切片三项负向 QEMU 方案

2026-10-23。状态：只读实施方案，尚未实现/构建/运行本切片。产品与已通过正向运行仍归属 `2ab997673a5894099f8df770707a060da3735154`；设计期间文档交付已到 `10dde54`，root 后续通知当前工作区 clean `744d9be`。这些文档提交不重归属旧运行。正式依据是 `docs/research/physical-driver-autorestore-design.md` 与当前 `linux.rs` / `physical_recovery.rs` 实现；本方案不改变产品授权契约。

## 1. 最小布局

新增两个文件即可开始：

- `scripts/qemu/run-autorestore-negative-qemu.py`：三个 mode 的独立负向 verifier、镜像组合与来源记账。
- `scripts/qemu/guest-autorestore-negative-fragments.sh`：负向阶段、明确 helper 生命周期、策略切换与条件解除后的尾部流程。

首选不改任何已通过的 journal/crash/guardian/positive autorestore 文件。复用 `run-journal-qemu.py` 的宿主 transport/main、三轮 KEY1/2/3 与每方向35B I/O、QMP/FTDI、source/ELF guards；复用正向脚本的纯函数 `generate_init(..., "owner-death")`、`rows`、`valid_owner`、`valid_helper`、`verify_clients`、tracing 解析。对生成 init 作第二层少量唯一锚变换：加载 negative fragments、切换新阶段函数、替换仅该 mode 的策略/旧 helper 启动入口、追加失败采证文件。保存两层所有锚与最终 init 字节/SHA；禁止宽泛 regex 变换。

新 verifier 不调用 `verify_positive` 后过滤失败，也不调用旧 journal184再删断言。三个负向有自己的阶段和裁决；复用纯解析/I/O检查不是继承其完整通过数量。原两个正向的源字节、模式、镜像不包含 C probe 的约束及成功断言保持不变。

建议 CLI：

```sh
python3 scripts/qemu/run-autorestore-negative-qemu.py --scenario legacy-schema1 --legacy-manifest /tmp/rustusb-legacy-c8f2e90/manifest.json --evidence /tmp/rustusb-autorestore-negative-SOURCE-legacy
python3 scripts/qemu/run-autorestore-negative-qemu.py --scenario policy-revoked --evidence /tmp/rustusb-autorestore-negative-SOURCE-policy
python3 scripts/qemu/run-autorestore-negative-qemu.py --scenario external-claim --evidence /tmp/rustusb-autorestore-negative-SOURCE-external
```

以上命令仅提议，脚本尚不存在。保留 `--kernel`、`--timeout`；整体300秒、I/O8秒、控制接收60秒、失败收集30秒均沿用。新拒绝观察窗固定5秒，覆盖当前250ms周期的多个机会；不将它写成永久无操作或内核硬阻塞证明。恢复尾部沿用正向的20秒只读窗，其起点是条件解除且新 helper就绪之后，明确不是 KILL 起算 SLA。

## 2. 共享真实证据和握手

每个 mode 是独立的新双 guest 运行，仍用 root0600 两合成节点、ordinary UID1000服务/零capabilities、UID1001拒绝、私有socketpair、S0 kernel/S1 program。保存原完整 sysfs receipt/config/full interface/alternate/driver、boot、policy原字节、grant/owner、每个 helper的实际PID/starttime/exe/UID和执行ELF SHA。

保留 cycle1正常真I/O与可选原无IMPORT reopen对照，cycle2真I/O之后 actual_pending（两设备pending/inflight>=1、预算>0、sysfs usbfs claim、实际服务FD）、cycle3解除拒绝条件后的真I/O。每次run都是三个键盘down/up与各方向105B，host六次注入恰好一次；ready不能代替第三次IMPORT和数据。

在 cycle2 真I/O 后、KILL前，先保存两grant、完整基线和owner身份，再合作停止当前 helper并实际wait0。服务仍持有原A，不重开grant；停helper不应关闭普通服务数据通路。这将三个 mode的条件设置都放在helper不运行的确定区间，避免自动恢复先赢竞速。

只向该ordinary服务发一次KILL，实际wait一次且必须137，立即保存。owner已reap后才检查同代际NULL driver。不要把僵尸、kill成功或PID不存在替代实际wait；不得写killed stopped/cleanup资源0。客户端照旧先保存自主断连、旧代际消失/VHCI NULL，然后等待server primary barrier。

拒绝期不启动第三次服务。在负向只读主观察完成后做一次新服务拒绝验证（非零wait、无ready、实际未决grant错误，而不是仅捕获任意失败），再次核record与sysfs未改变。双方负向primary保存后发送 `cycle-2-server-observed`，接收 `cycle-2-observed`，先释放原client barrier再进行尾部处理；这样不会让5秒拒绝窗、尾部等待和多次helper重启叠加到client控制超时。

每阶段即时输出完整 JSON record/wait/产品helper完整行日志，失败trap保留尚未完成的原件。完整journal磁盘字节经JSON字符串携带并记录SHA，API投影另存，不能只保存摘要。查询只读，但拒绝/恢复首个primary优先用磁盘+sysfs，不依赖管理查询触发行为。

## 3. L：真正旧 schema1 producer → 新 helper

### 来源与隔离构建（由 root 串行编排，本任务不执行）

固定旧 producer 为 `c8f2e905fa68ca80a5a707c63a95d652c1f1ac32` 的真正 `rustusb-helper`，这是自动恢复产品提交 `e34e309` 前的版本。不要改新journal JSON伪造schema1，也不要用新版本manual记录冒称旧二进制兼容。

建议隔离路径与命令形式：

```sh
git -C /home/ubuntu/github/usbip-client worktree add --detach /tmp/rustusb-legacy-c8f2e90/worktree c8f2e905fa68ca80a5a707c63a95d652c1f1ac32
cargo build --manifest-path /tmp/rustusb-legacy-c8f2e90/worktree/Cargo.toml --target-dir /tmp/rustusb-legacy-c8f2e90/target --workspace --release --locked --all-features
```

旧target必须在新workspace target之外；先后记录当前 `/home/ubuntu/github/usbip-client/target/release/rustusb{,-helper}` SHA完全不变。不能把旧helper构建输出覆盖当前产物，更不能在QEMU活跃时构建任一版本。旧worktree clean/head、全部传递输入（workspace+vendor+lock）、toolchain、完整build.log、旧helper ELF与runtime依赖均写入独立manifest。新harness读manifest并核文件字节/SHA/固定producer commit，不靠文件名或不存在的helper `--version`推断版本。

镜像中当前binary位置保持 `/usr/bin/rustusb{,-helper}`；仅L追加旧helper `/usr/libexec/rustusb-helper-legacy`。旧/current依赖如同名不同字节应拒绝组装，不能静默覆盖。base的当前产品guest ELF账不改；另列legacy guest ELF复制校验。guest每次helper启动读取 `/proc/PID/exe`、实际可执行文件SHA和身份，声明实际选中的固定路径；不通过原位覆盖正在执行的helper来切换版本。

L的server最初使用旧helper，client始终用当前helper，ordinary server用当前CLI；这有意验证当前lifecycle1客户端与真实旧producer兼容。无需旧CLI，报告必须明确“旧helper producer/current CLI及current consumer”，不能称整套旧产品运行。新server-only `start_helper`替代函数只允许两个固定helper路径，并关闭服务控制FD；原client启动函数保持当前路径。观察辅助函数对旧/current两个身份分别核实际路径，不能把legacy进程的exe写成当前helper。

### 阶段与裁决

1. server root policy不含新字段，使用旧helper；cycle1与reopen完成后disk仍1。cycle2真实grant/真实I/O后保存旧schema1 record原字节与hash。
2. 按共享顺序停旧helper、实际KILL/reap服务；同receipt/config/IF/alt、driver null。root在helper停机时写新policy，明确开启两个设备的 `physical_connect_restore_device_ids`。
3. 启动**新**helper、相同state目录和同boot。读取原schema1记录；至少5秒只读观察：原record逐字段不变，disk仍schema1且没有 `recovery`，IPC仍旧shape且没有storage2摘要；没有这些lease的 `physical_auto_restore attempt/retired/failed`，驱动持续null。当前策略的opt-in不能补授旧记录权限。
4. 新ordinary服务必须明确被旧未决记录拒绝，无ready；再次核原record/null状态。保存完整负向primary后通知client barrier。
5. **L尾部必须是明确手动恢复**：合作停新helper并wait0；guest管理员仅对原两设备/原接口绑定usbhid和ftdi_sio，完整原基线复核；用相同当前opt-in策略重新启动新helper。再观察一个固定窗口，旧ManualOnly记录仍不得被自动退休，即使设备现在已经原样恢复。
6. 只有随后root的只读 `physical-reconcile` 两旧lease才退休，保存实际retired/restored响应及磁盘空表。不能通过删除journal、改recovery字段或重新授权覆盖旧记录。
7. 新ordinary grant才合法触发新store从1升2，两新lease明确 `connect_original_drivers`；第三轮真实I/O、actualpending、正常close/wait0/stopped0、schema2空表且不降回1。

L尾部证明旧manual不被追授及新grant可用，**不计为旧租约产品自动恢复成功**。该最小L还不证明“schema2升级时同时存在其它旧manual记录”的混合表场景；两个fixture都被旧未决grant占用，本运行在它们退休后才有新grant。混合旧manual+新opt-in共存迁移由已有软件测试覆盖，真实QEMU若要补必须独立扩设备/授权场景，不应在此偷偷制造第三条journal记录。

## 4. P：持久opt-in存在，但当前恢复许可被撤销

1. 全程当前产品；初始policy启用两设备恢复。cycle2记录确为disk2、两条不可变 `recovery="connect_original_drivers"`，已真实I/O。
2. 停helper并wait0时服务仍活着，原FD/record不变。只改 `physical_connect_restore_device_ids` 为 `[]`；物理导出allowlist、UID、trusted_network等保持，确保拒绝原因是恢复许可而不是整个授权被撤销。当前实现策略是启动时快照，不做热重载。
3. KILL服务/actualwait137后，以禁用恢复的新policy启动新helper。5秒观察driver null、磁盘原record字节/基线/recovery字段不变；无自动attempt/retired/failed。这些条目因为当前策略不允许而跳过，不能要求它们有failed事件。
4. 同device普通新server明确因未决grant拒绝；负向primary保存、双端barrier完成。
5. 合作停止禁用helper并wait0；恢复policy中的两个ID，再明确启动一个新helper。policy更新与实际新进程身份、hash分别记账，不把文件写入当即时生效。
6. 纯sysfs+disk观察：新helper对每个旧lease一个attempt→retired、完整最初原基线恢复、原记录空；没有管理员bind或reconcile。之后才root pending交叉核验和第三轮新grant真I/O/正常结束。

P至少区分三个helper实例（初始opt-in、禁用、恢复许可）；每次实际wait0和不同PID/starttime、policy hash、新进程exeSHA都保留。此场景证明“当前许可需要与持久许可同时成立”，不证明撤销已经交付的整设备A能力。

## 5. C：helper停机期间独立外部C先claim

### 最小test actor复用

可以直接复用已审查/通过的 `usbfs-guardian-probe.c` 原字节，只把它编进 **external-claim负向镜像**，L/P和原两个正向镜像都没有该probe。无需新增通用设备工具。

旧probe启动要求原驱动存在，所以在cycle1完成/reopen结束后、cycle2 IMPORT之前启动。它只预开两个独立且不claim的B，保存完整receipt/原状态；这不是A的dup。KILL后再发 `copen`，C是probe内另外一次独立open，其实际 `kcmp(B,C)>0`与FD/receipt日志证明另一OFD。称同probe进程中的外部C，不能称另一进程，也不假称在A已关闭后做了C/A直接kcmp。

新控制层只允许以下非恢复命令，序号严格递增：

1. `copen`
2. `claim C 0`
3. `claim C 1`
4. `release C 0`
5. `release C 1`
6. `cclose`
7. `quit`

如为额外快照加入 `snapshot`，在设计/expected ledger中固定位置；不能松散允许任意命令。probe源码虽然具备CONNECT等旧机制能力，本次实际命令/ioctl ledger必须严格证明无CONNECT、无B claim、无dup/arm/drop、无reset/detach。`claim/release`恰好rc0，最后C关闭F_GETFD=EBADF、B关闭、probe实际wait0；不以rc>=0弱化claim/release。

已有Cprobe保留root+双cmdline guard、两固定root0600字符节点、完整identity/config/IF/alt检查。FIFO仍由root probe自己O_RDWR保持，init不常驻root控制FD传给ordinary child，所有降权/新helper均关闭无关FD。每次读取只处理完整结束的日志行。

### 阶段与裁决

1. 两设备已真实I/O/原opt-in grants，helper合作停止wait0；A仍活着直到一次KILL/reap137。确认同receipt/interface/alt已null。
2. helper尚未启动时，probe C独立open并plain claim两接口，实际rc0、sysfs两driver=usbfs、C进程/FD身份不变。此顺序消除产品自动恢复抢在C之前成功的竞速。
3. 当前helper以原opt-in策略重启。每个lease实际记录一次 `attempt` 和一次 `failed`、无retired；失败reason匹配完整scope检查的external-driver拒绝。当前 `physical_recovery::scope` 在看到usbfs时就拒绝，**不强制要求CONNECT返回EBUSY**，也不能把没有CONNECT误报为测试未执行。
4. 等到两条failed原日志完整持久后继续5秒只读窗：没有再次attempt；C两claim/FD保持，配置/完整IF/alt/receipt未变，原record不变，drivers仍usbfs。新服务授权明确因pending拒绝。保存负向primary及client barrier。
5. 在已确认两实际失败任务完成后，按ledger release C0/C1，cclose，quit并实际wait0。此probe从未执行恢复。立即独立确认原设备driver null、old record仍pending。
6. 保持这个已失败helper实例再观察5秒：无新attempt/retired，原record/null保持。这直接验证每lease每helper只尝试一次，而不是在外部claim解除后偷偷重试；结束时完整保存同helper的事件序列。若此阶段自动退休，测试应FAIL，不能跳过重启并计成功。
7. 合作停止该helper/实际wait0，再显式启动新helper。此时所有测试B/C已关闭，无恢复test actor存活。仅产品helper完成每lease attempt→retired，纯磁盘/sysfs完整原基线primary先保存，再pending API，再第三轮普通服务真I/O和正常结束。

C尾部的“关闭C后仍不恢复”是本实验刻意验证的每实例一次语义；实际管理员变更用途仍遵循正式设计的停止helper/等待工作结束规则。本方案没有把失败后probe close当成一般生产恢复命令。

## 6. 新 verifier 的必需分组

- 来源：实际current clean source、旧producer固定commit与隔离build证据、每段guest执行ELF、old/current依赖、生成init和fragment、原base hash、module/kernel、原始artifact SHA；新legacy产物绝不覆盖主target。
- 公共I/O：三cycle各HID down/up、FTDI35B双向、host105原字节、six injections、receipts、真实pending/claim/预算、client自主断连在detach前、正常两个cycle真实wait0/cleanup0。
- KILL：唯一signal9/UID1000、PID/starttime/exe/caps匹配、唯一实际wait137；killed末资源始终unobserved。
- 负向：逐mode精确schema/record原件与policy事实，完整5秒窗口与helper身份固定，newgrant非零/无ready/正确product错误，driver/receipt不变；不是只看最后一个快照。
- 产品事件：L/P拒绝期间无attempt，C每旧lease恰好attempt→failed且原因明确、没有任何retired；不能统一成“所有负向必须有failed”。C重复扫描与关闭C后的同实例窗口都不得增加attempt。
- 尾部：L必须人工恢复并且旧manual仅root只读reconcile退休；P/C明确新的helper实例自动恢复、记录退出和新lease实际复用。每个mode分别标 `tail_automatic_recovery`，L=false，P/C=true。
- 权限：root0600、ordinary不能直接open、UID1001拒绝、root管理接口边界保留；新test actor只能external-claim mode/root固定fixture。
- 失败保真：任意第一FAIL永久保留，后续collector超时不覆盖首因；完整采证和产品/负向合同通过分开。超时与脚本错误不得被当成期望拒绝。

新检查数量按实际运行记录计算，不能预先宣称继承184/159等旧计数。保存独立raw审查脚本与输出，不能仅重新调用新runner自己的verify作为独立审查。

## 7. 静态/运行交接与范围

root先把本方案的授权/范围变化写入正式设计，再授权新两个文件实现。实现后先Python AST、生成init/fragments `sh -n`、**实际jq全程序编译和最小正/负fixture**、source-only控制流独立审查；特别避免 `$label`/`$end` 保留字、条件函数调用抑制shell errexit、同一child重复wait、partial JSON行与控制barrier重试耗尽。freeze/handoff后作者停止写入。

root串行：隔离旧helper构建→新harness静态核/必要软件check→commit clean→L/P/C各新目录运行→原两个正向按需要单独回归。运行期间冻结repo、当前ELF、legacyELF与所有输入；不并发构建、不重用已失败目录。新结果归属其实际source commit，不归给2ab9976旧正向或c8f2e90旧producer单独版本。

此切片仍不覆盖helper SIGKILL/内核硬阻塞、OS reboot/power loss、跨boot自动退休、热拔换代、跨configuration/alternate恢复、外部新内核driver、多接口部分进展、任意FD/EP0撤销或真实硬件G1/G2。三负向通过只补本次明确边界，不宣称整个Phase2完成。

## 8. 实施前绑定正式设计（替代上文候选接点）

root现已批准正式 `docs/research/physical-driver-autorestore-negative-design.md` 并明确文件owner。实施以正式设计为准：旧producer改为已实际隔离构建的 **933e720531fd96b7a23616ed687d59b816e0ce09**；CLI增加 `--legacy-helper ABS_ELF`，与 `--legacy-manifest ABS_JSON`仅L必需，其余mode禁止。实际旧ELF3769624字节、SHA256 `2ce8940889b5831f92bebaa0218b5e182d3b54c8e5f15c22b1b43a495fe0819b`，路径 `/tmp/rustusb-legacy-producer-build-933e720-20261023/target/release/rustusb-helper`，manifest同构建目录。root已完成旧build，本作者不重建。

L/P拒绝窗按正式设计改为**至少20秒**；C释放后至少5秒。C改为新增严格受限的 `scripts/qemu/usbfs-external-claim-probe.c`，仅独立open/CLAIM/RELEASE/close，完全不带旧guardian的CONNECT/dup能力，且只打包进C负向镜像。实际产品C失败通常来自 `ensure_eligible` 的 `physical device is unsafe, already exported, claimed, or incompletely described`，早于scope；检查实际failed reason/category，不强制EBUSY或必须出现字面unsafe_device，errno=None可省略。旧positive/journal/guardian文件全部不改。只授权本轮新runner/fragments/C三个文件，作者不执行编译/Cargo/QEMU/commit。
