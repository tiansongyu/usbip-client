# CONNECT-only 产品自动恢复：最小双 guest 验收设计

2026-10-23；只读设计，不是执行结果。检查起点为 clean `c8f2e905fa68ca80a5a707c63a95d652c1f1ac32`。本次未改仓库，未编译，未运行 Cargo/QEMU。已读取项目 AGENTS、rustusb-development skill、架构/崩溃设计及 journal/guardian/已有 syscall gate 实现。本文描述拟新增测试，不把候选参数或产品接口写成已实现功能。

## 1. 建议先交付的三个独立运行

| 运行 | 策略与记录 | 需要证明 | 不得依赖 |
| --- | --- | --- | --- |
| A：owner-death | root 明确为两个合成 device ID 启用新基线恢复契约；新记录持久保留该授权 | 普通服务真实 HID/FTDI I/O、一次 SIGKILL/真实 wait137 后，产品 helper 自行恢复完整原基线并退休原记录，之后新 grant 再次真实 I/O | root 测试 USBFS probe、sysfs bind/unbind、physical-reconcile、重新启动服务触发恢复 |
| B：helper-restart-live | 与 A 相同，同一 state dir；活服务保有原 grant | helper 合作重启后识别原 owner 仍活着，不接管其租约；服务继续真实 I/O；随后 owner KILL 后由新 helper 自动恢复/退休，再次真实 I/O | 给重启服务新 grant、测试端补恢复、原 owner PID 恰好不同即判断死亡 |
| D：default-off 回归 | 原 journal runner 原默认策略，不含新 opt-in | 原真实 I/O、未恢复、跨 helper 重启拒绝、管理员显式恢复/只读 reconcile、第三轮真实 I/O 全部保留 | 修改旧断言来适应自动恢复，或者把管理员恢复结果计入 A/B |

D 保持原 184 项断言与原件字节；A/B 各有自己的新契约、明确名称及实际计数。A/B 不调用旧 `verify()` 后删去失败项，不把旧 184 项列入其通过数量。旧 guardian 的 237 项仍只是机制实验历史证据。

首轮 A/B/D 足以形成小而有意义的实际增量；它不覆盖下文全部故障。旧 schema、当前禁用、外部占用仍是产品实现必须有的软件拒绝测试；对应 QEMU 运行可以独立逐项追加，未运行时在验收表保留“未验证”。

## 2. 产品接口应先固定的契约

当前检查到的产品仍为 schema 1；新的策略字段、记录授权字段、恢复事件字段和后台 tick 周期未固定。实施测试前，应从真实产品代码绑定以下内容，不能由测试杜撰名称：

1. root policy 中按设备允许原基线恢复的字段；默认 false/空。普通 CLI 不能自授这项权利，恢复设备必须同时满足当前物理 allowlist 与当前恢复策略。
2. 新记录如何持久保存明确授权，以及旧 schema 1 的加载/拒绝/显式迁移行为。不能把旧记录仅因当前策略启用而视作新授权。
3. 活 owner 的判定、helper 轮询周期/唤醒方式、后台恢复运行是否独立于管理查询和下一次 OpenPhysical。
4. 恢复允许操作只有受条件约束的 CONNECT；完整 receipt、boot、配置、接口集合、alternate、当前驱动的比较规则；外部占用或不确定结果保留未决记录。
5. 恢复日志至少关联 lease/device/owner/helper 身份，记录实际 ioctl 返回与失败上下文、完整复核结果和退休结果，不记录 USB payload/序列号。

A/B 必须能在没有新服务、没有 root reconcile、没有测试设备操作的区间观察成功。若产品只有“下一次请求顺带清理”，不能用这种结果声称服务死后 helper 后台自动恢复。

## 3. 两个正向运行共享的设备、身份和 I/O 合同

复用原双 guest：普通宿主 UID；x86_64；QEMU 独立合成 HID `1-1` 与 FTDI `1-2`；私有继承 Unix socketpair，无宿主网络 listener；server guest 不加载 usbip_host/vhci_hcd；client 用真实 vhci_hcd。guest 内设备字符节点 root:0600，server UID1000、全部 UID 字段为1000，CapEff/Prm/Amb 为0，UID1001 helper 授权拒绝，UID1000/1001 直接打开节点均拒绝。

保留原三轮客户流程与固定字节：KEY1/2/3 分别核 evdev code2/3/4 的 down+up；每轮 FTDI 两方向各35字节、逐字节比较；host 接收合计105字节、三次串口注入与三次键盘注入恰好一次。cycle1 正常结束、cycle2 被杀、cycle3 恢复后正常结束。原 normal reopen 无 IMPORT 对照可原样保留，不计额外 I/O。

每轮真实传输后保留原 `actual_pending`：两个设备真实 leased，pending_urbs>=1、usbfs_inflight>=1、正的保留预算；外部 sysfs 显示当前 driver=usbfs；/proc 中服务 FD 对应相同 root0600 字符节点和代际。该检查不能被“客户端已连上”或“ready”替代。

原基线从首次 grant 前独立采集并永久保留：设备 ID/bus ID、boot ID、设备 sysfs stat、node inode/rdev/权限、bus/devnum、当前配置、枚举到的全部接口 number/alternate/driver。当前 fixture 预期只有 IF0，但必须实际枚举接口集合，不能仅硬编码读取 `:1.0` 就声称完整接口验证。可复用 guardian 的只读 full-interface shell 观察逻辑，正向镜像不复制/启动 guardian C probe。

每个新授权记录与上述实际基线逐字段对应；owner PID/starttime/UID/boot 与实际服务一致；原 lease ID 在 helper 重启时保持，在下一次 grant 时全部更新。必须区分“记录不存在”“记录损坏/无法读”和“观察尚未完成”。

## 4. A：owner-death 的阶段顺序

1. 记录固定 root policy 原字节/hash、完整原基线、helper PID/starttime/exe/UID、state dir 的 root0700/file0600、空表。核新 opt-in 只覆盖两固定合成设备。
2. cycle1 正常真 I/O -> 实际 pending -> 合作停止 -> 实际 wait0/stopped资源0 -> 原基线一致且记录空。保留原无 IMPORT reopen 对照，正常结束同样必须退休。
3. 启动 killed epoch，ready 时核两条新授权持久记录及基线；真实 cycle2 I/O 后取 actual_pending、服务身份与 FD、完整 sysfs、原 pending/disk 快照。
4. guest UID1000 仅向已核身份的自己的服务发一次 SIGKILL。保存 signal sender、PID/starttime、次数=1；实际 wait 只执行一次，结果必须137，立刻输出单行持久证据。服务没有 stopped 就保留缺失；killed terminal resources 必须 null/unobserved。
5. KILL 后不启动新服务、不执行管理 reconcile、不运行 USBFS 恢复测试 probe、不写 sysfs。只读轮询 sysfs 与磁盘 journal，等待两设备完整原驱动/config/full IF/alt/receipt 恢复且两旧记录已退休。建议使用原20秒上限作为新自动恢复验收预算，记录真实 elapsed；不足时应分析失败，不用无界等待掩盖。此20秒预算只约束本运行，保留旧 D 的原20秒观察不改。
6. 首个成功观察必须来自纯 sysfs/磁盘读取；之后才能调用 root physical-pending 作交叉验证。这样可排除“管理查询触发恢复”假阳性。即使查询按设计只读，也应保存其顺序。保存每次完整观察、产品 helper 连续日志以及恢复前/后 journal hash。
7. client 在显式 detach 前保存自主断连、sessions空、VHCI NULL、旧代际消失。双方各自保存 primary 后使用现有 `cycle-2-server-observed` -> `cycle-2-observed` barrier，不能让短重试 send 提前撞上 server 的20秒观察窗口。
8. 在自动恢复/退休 primary 证据已完成后，再启动新的 ordinary service。其 ready 的 original 必须等于最早原基线，不能等于中间 null；新 lease 与旧 lease 均不同。cycle3 完整真实 HID/FTDI I/O -> actual_pending -> 正常关闭/wait0/stopped0 -> journal空、原基线一致。
9. 两 guest collection_complete 与实际退出0、所有数据完整后才判 PASS。产品恢复与 evidence_collection_complete 分开记录，异常保留最早 failure marker 及后续收集错误。

并发注意：helper 可能在父 shell 收到 wait137 前已恢复；不可要求“wait 日志一定先于 helper 恢复事件”这种无因果保障的次序。wait证明进程终态，helper的观测与操作另记。也不要求恰好捕获一次中间 driver=null，快速成功可能没有采到；pre-kill 的 usbfs claim、真实死亡、产品实际 CONNECT 事件和 post 原基线才是主链。

## 5. B：helper-restart-live 的最小额外 barrier

A 的 cycle1/3 和 KILL 后合同均相同；唯一差异在 killed epoch 的真实 cycle2 I/O 之前。

建议在 client 的 cycle2 已 attach 两设备、核 receipts、打开 evdev/TTY 后，且在启动8秒 serial dd 及输出 WAIT_INPUT/WAIT_SERIAL 之前插入唯一条件 barrier：

- client -> server `cycle-2-attached`；client 等 `cycle-2-io-allowed`。其它 cycle 不插入。
- server 在 `cycle-2-ready` 后等待 attached，核 same owner活着/FD、两 grant原字段、两个接口已由 usbfs claim、两设备leased；可保存 pending快照，但不把尚未开始串口读时的 inflight>=1 作为新增脆弱前提。
- 原 helper合作 TERM，实际 wait0且socket消失，保存原 helper日志；记录磁盘原件。新 helper以相同产品ELF、policy与state dir启动，新PID/starttime不同，旧服务PID/starttime/FD/lease全不变。
- 在原owner活着时观察至少两个实际 recovery扫描周期；固定一个有界活体观察窗口（建议5秒，最终需确认覆盖产品实际tick），核完整 journal原记录不变、sysfs仍usbfs、owner身份仍真，不退休/不驱动恢复。日志须显示对应周期和“owner仍存活”决定，不能只 sleep后断言程序没坏。
- server -> client `cycle-2-io-allowed`；client 才启动原8秒serial reader、发原host注入marker，进行原35B与KEY2真实I/O。这样不延长或削弱旧I/O timeout，无需另加第四轮payload，也不会因helper重启耗时让dd先超时。
- 接收原 `cycle-2-io`，重新做原严格 actual_pending 和same-owner/journal核；之后才一次KILL/wait137。新helper在后续后台周期完成恢复/退休，cycle3验证新grant实际使用。

“没有写”的证据等级必须明确：sysfs/journal未变只能证明没有可见状态改变，不能排除失败的 CONNECT。最小产品可审计恢复事件与独立源码检查应证明 live-owner分支在任何USBFS mutation前返回；日志完整且带扫描身份/决定/实际action才能形成较强运行链。若要独立断言“零 ioctl 尝试”，需另外可计数的系统调用观察；现有 `usbfs-syscall-gate.c` 是会暂停 syscall-entry 的服务测试器，不是直接可复用的 helper只读跟踪器，不建议为首轮强行接入或把其测试当当前运行证据。若本轮没有这种观察，应将结论写为“活 owner 的状态和授权保持，实际I/O继续；零主动恢复操作由产品事件/源码约束”，不能扩大为全系统无USB操作。

B 只证明 helper 合作进程重启+owner随后被杀；不等价于 helper SIGKILL、OS reboot、掉电耐久、并发第二helper、磁盘错误或PID重用验收。

## 6. 具体复用接点和文件建议

建议新增 `scripts/qemu/run-autorestore-qemu.py` 与小的 `guest-autorestore-fragments.sh`，不修改旧 journal/crash/guardian/TERM harness。按 guardian 的 composition模式保留生成init原字节/hash和所有源输入。

Host 可复用 journal.main 的构建、source/ELF冻结、initramfs、模块audit、socketpair/QMP/FTDI、三轮注入、双通道、30秒失败收集grace、最终进程退出和证据持久化。新wrapper预解析 `--scenario owner-death|helper-restart-live` 后只向 base main 传已有参数，扩充自身source_fingerprint和SCRIPTS。新 scenario 单独落metadata；summary scope必须改为新实际合同，不能保留“not automatic recovery”旧文案。原host main 中硬编码错误文字可作为继承transport错误上下文，但最终summary需准确命名场景，不能仅改报告标题。

Guest在固定SHA旧源上做少量经过审核的唯一锚转换：

1. root policy生成处：为两固定设备加入新字段；绑定真实接口。
2. 初始baseline前：加载只读fullIF/恢复观察辅助函数；现有sysfs函数扩为完整IF枚举（只替换生成版本），保存原模板与替换ledger。
3. 从 `STAGE=kill-service` 到 `STAGE=authorized-after-reconcile` 的完整旧流程区间：替换成新的自动恢复专用流程；边界必须各恰好一次且顺序正确。这是有意的新测试合同，不是删除旧测试要求。保留后续第三轮I/O流程，可将新epoch命名为 `recovered` 并同步新verifier；不要留 `reconciled` 造成误读。
4. 仅B在client cycle2 TTY打开后、serial dd开始前加入 attached/allow barrier；A和其它cycle无barrier。
5. 失败trap补新完整只读观察/等待/helper两份日志/恢复事件原件；每条record、wait即时输出完整单行。只读取完整已结束JSON行，避免并发半行parse误判。

准确的原source目前为：

- run-journal-qemu.py：`1cefbc14bc45c9e9b5c9f6f0d9784be834fe3ee5856f8bc2937b7baabbbc01aa`
- guest-journal-init.sh：`2236c3080d17e447c4d9cdec495609ab6c7d3976d6c92306e3b1286e9d94d8d4`

初始化必须拒绝source drift和锚数量不为1。保存生成init、fragments、composition ledger、scenario、产品接口契约版本、原脚本SHA。用实际jq逐表达式compile/fixture检查（包括变量名；避免重演保留字 `$label`），`sh -n`、Python语法检查后再freeze。正向initramfs中没有 `usbfs-guardian-probe`，其生成流程中也没有 root bind/unbind/reconcile语句；白名单式审查恢复区间仅采证，不能把仅grep无某个字符串当完整安全证明。

新 verify 独立解析新的record标签和action事件，但可复用 `json_block`/`epoch_events`/`zero_resources` 等纯函数。若产品修改公开snapshot schema，需明确适配，不能宽松接受任意schema。保持通道S1/S0互斥、真实client receipts/IOfacts、wait、权限、完整基线、每轮唯一事件、任意FAIL marker不可PASS、source/head/ELF/clean guards等语义；不要假称完整继承旧184。A/B verifier可共享大部分检查，B再添加重启活体合同。

所有新checks都基于实际采集值，而非把预期值先写进JSON后再比对：进程身份必须读/proc，配置/接口必须读sysfs，等待必须来自wait，ioctl必须来自产品真实调用结果，records必须来自真实文件/API。记录手工设定的scenario/policy属于输入，不当运行结果。

## 7. 后续独立负向：旧记录、禁用和外部claim

### L：真实旧schema1记录不可静默升级

最强最小集成办法是固定旧helper ELF实际生成schema1 grant/记录，普通旧服务完成真实I/O后KILL，保存旧record原件，然后停旧helper，在相同state dir/设备/boot上以新helper+显式opt-in策略启动。新helper必须按既定兼容契约保留手动处理/明确拒绝，不能CONNECT或静默给记录补恢复授权；旧records完整基线与lease身份不被重写成新授权。新服务授权继续拒绝，实际驱动保持null。

必须单独账记旧producer/currentconsumer的source SHA、features和ELF哈希，不将双版本运行写成单一clean当前ELF。若当前产品仍能正式产生schema1，可使用实际路径；不能把“新schema中disabled record”称旧schema1测试。手工生成合法schema1夹具可做软件解析/迁移拒绝测试，但不冒称跨版本真实grant集成。旧helper是否可读新空state等细节需从最终schema实现确认，不能改字段绕过。

### D / P：默认禁用和当前策略撤销

D直接运行原journal，保护默认未授权仍是原只读边界。另一个独立P可以先由明确opt-in产生新record，helper合作停止而owner仍活着，root将当前策略恢复为禁用，再KILLowner和启动helper；此时记录自带旧授权也不能越过当前策略。保留原record/drivernull与新服务拒绝。P不是D的替代；仅D通过不证明撤销语义。

若新helper公开schema升级导致原journal旧固定schema断言不兼容，应先讨论真正兼容契约。不要悄悄改旧184或把不兼容当测试无意义；必须区分默认行为保持与管理schema改变，并保留原测试及明确的新兼容测试。

### C：独立外部OFD占用拒绝，禁止恢复probe混入正向

在单独的一次性双guest运行中使用guardian probe的受限负对照能力。为消除C与产品恢复竞速：

1. opt-in owner已有真实I/O和两记录时，先合作停止helper，owner保持活着；记录helper真实wait0及state不变。
2. 一次KILLowner/真实wait137，独立观察接口释放为null（无保留Adup）。
3. root test probe独立open C并plain CLAIMINTERFACE占两固定接口，实际rc0、完整sysfs=usbfs、C的FD/receipt/自身PID/starttime可查。此阶段helper未运行，保证C在恢复之前已占有。
4. 以同policy/state重新启动产品helper。完整多个扫描周期内C占用不被解除、原记录不退休、原driver不bind、新grant明确拒绝。若helper尝试CONNECT，必须实际EBUSY；也允许在完整检查已发现非null usbfs时直接拒绝且没有CONNECT，此时按产品合同验证拒绝原因，不能硬要求必须执行失败ioctl。
5. 保存完整primary后关闭C；是否进一步观察自动恢复可单列后续阶段，不能回填前面的拒绝判定，更不能在probe里执行CONNECT替产品修复。

现有guardian probe会预开B且C命令等有固定协议；新负向不必照搬全23命令/恢复阶段。可以复用其guest-only/root/两root0600节点/完整receipt和OFD证明的受限实现，另写只open/claim/release/close的最小负向控制。另一个OFD可以同probe进程，明确不称另一进程。Adup留存是另一项负对照：其真实kcmp必须Adup=A；不能把D假称独立C。两者若都执行需分别报告，初轮不强制扩范围。

## 8. 原件、运行预算和拒绝成功规则

每个运行独立新目录；保存source SHA和clean状态、完整冻结输入、Cargo.lock/toolchain/buildlog、产品/辅助工具/guestELF SHA、kernel/config/module hash、实际QEMU命令、生成init与composition、policy原件、双guest kernel S0/program S1原日志、即时完整record/wait、client二进制I/O、hostTX原件、failure marker、summary。旧记录或故障原件不因后续成功而覆盖。

新helper恢复日志至少保存进程身份和每次相关lease的决定/实际调用/复核/退休。测试不得仅根据“recovered”字符串或record消失作成功：仍需完整独立sysfs相等、旧lease确实退出未决集、新grant采用最早基线、第三轮真实传输。没有旧server终态资源快照就一直标unobserved。

保留base整体300秒、现有I/O8秒、控制接收60秒与失败收集30秒，不用放宽时间修复顺序错误。新增live窗口按真实产品tick写入契约；若产品周期不适配预定预算，先调整设计并解释，不静默重试或无限poll。同次运行首次FAIL永久保留；重跑有新目录与新source/commit归属。

成功范围只包括：当前QEMU/kernel、合成HID/FTDI、同boot/receipt、同配置/完整IF/alt、driver为原值或可解释null、已获root显式授权的CONNECT-only路径。它不证明一般硬件、G1/G2、任意驱动选择、跨cfg/alt恢复、热拔换代、外部新driver、部分多接口恢复、helper硬崩溃、kernel内部活跃阻塞、全部FD/EP0能力撤销或OS断电耐久。即使实测通过也不能标整个Phase2完成。

## 9. 待实现后的建议命令（本次未执行）

以下脚本/参数是建议命名，需实现并freeze后使用。三个命令必须串行，运行期间产品/测试脚本/head/ELF不变：

```sh
python3 scripts/qemu/run-autorestore-qemu.py --scenario owner-death --evidence /tmp/rustusb-autorestore-SOURCE-owner-death
python3 scripts/qemu/run-autorestore-qemu.py --scenario helper-restart-live --evidence /tmp/rustusb-autorestore-SOURCE-helper-restart-live
python3 scripts/qemu/run-journal-qemu.py --evidence /tmp/rustusb-autorestore-SOURCE-default-journal
```

在此前完成新脚本静态检查、jq真实fixture验证、产品规定软件检查和独立代码审查；root统一编排构建/QEMU，本任务不执行这些命令。新正向最终source应绑定实现后的实际clean提交，不归属到本文的设计起点c8f2e90，也不重归属旧da764d0机制或933e720 journal证据。
