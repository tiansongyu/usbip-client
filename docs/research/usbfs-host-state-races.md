# USBFS host-state syscall-entry 门控验证

2026-10-23，状态：六个开发场景全部PASS，共48条独立wire probe检查、12个guest exit0；均为dirty `345163bef598ffe45b62f0b4175a4a7a56c36a98`开发证据，最终clean提交的六场景验证仍待执行。三次先前FAIL完整保留。设计见[host-state设计](usbfs-host-state-race-design.md)，已有实际CDC ECM双向数据与原驱动恢复见[Composite最终报告](composite-qemu.md)。本轮没有修改生产Rust、vendor、Cargo或旧QEMU脚本。

## 实际观察的窗口

物理worker的`ControlAction::Special`通过nusb blocking task执行SET_CONFIGURATION、SET_INTERFACE和CLEAR_HALT。新guest-root协调器只暂停实际普通UID1000 server的目标blocking TID，位置是**真实ioctl的syscall-entry stop**；其余线程继续执行。释放门后调用原内核ioctl，记录真实syscall-exit，再观察worker完成、wire回复和最终恢复。

这个位置在内核执行目标ioctl之前，证明Special已进入blocking线程并到达系统调用入口，不能称USB内核驱动内部已经执行或阻塞。没有通过改写寄存器、参数或返回值模拟USB结果，也没有用抽象AsyncBackend替代实际physical worker。本轮补的是该入口窗口的取消/断线排序证据，内核内部active/hung ioctl仍未验证。

SET_CONFIGURATION在入口之前本来就会释放旧interfaces，因此不声称这时旧claims仍保留；要求slot、授权FD、请求预算继续持有，且EOF不能提前触发最终restore/available。SET_INTERFACE和CLEAR_HALT另外独立核对两个实际usbfs驱动绑定及原alt0/1。`usbfs_inflight`只数raw URB，Special持门时为0合法，不能用它判断Special已经终态。

## 隔离与程序边界

新增四个测试程序：`run-host-state-qemu.py`、`guest-host-state-init.sh`、`host-state-wire-probe.rs`和`usbfs-syscall-gate.c`。runner以普通宿主UID运行，默认实际执行`cargo build --workspace --release --locked --all-features`；独立Rust probe使用std，C协调器仅在一次性x86_64 Linux来宾以root运行。没有新增生产test feature或I/O façade。

两来宾的e1000仅通过继承Unix socketpair组成192.0.2.0/24私网。server QEMU6.2.0以UHCI模拟Full-Speed USB-net：ECM配置值1、IF0控制/IF1数据，IF1 alt0无Bulk端点、alt1有0x82/0x02；另一个私有socketpair连接USB-net backend和server第二e1000，该peer网卡全程down，防止合成网络帧提前完成待测Bulk IN。两个管理eth0都须实际carrier=1才进入后续准备，不用固定启动延时替代观察。没有宿主USB、ptrace、模块加载、TAP、网络配置或host listener。

guest root helper仅允许UID1000和精确一个`physical_device_ids`，Native导出列表为空。USB节点始终root0600；UID1000/1001直接O_RDWR均须明确Permission denied，UID1001 helper调用另须精确拒绝。实际server和client probe均为UID/GID1000、零effective/permitted/ambient/inheritable capabilities；server通过helper取得已核验FD，helper不被ptrace。新client是独立wire probe，本轮不重新宣称VHCI/cdc_ether数据枚举或网络吞吐；这些实际内核与UDP证据属于前述Composite测试。

tracer检查guest专用kernel标记、固定root0700目录、精确启动argv和授权字符节点。它用GET_SYSCALL_INFO区分entry/exit，追踪clone/exec及退出事件，只对本次arm的节点dev/inode/rdev和白名单request持门；不读取ioctl参数指针或USB payload。真实PID/starttime、TID、FD、request、单调时序、变更ioctl计数与原返回值均留证。只stat已有`/proc/PID/fd/N`，不从proc重新open设备。单门8秒、ARMED之后RPC3秒、tracer120秒与runner整体截止分别有界；异常EXITKILL或自动结束不能算产品清理PASS。

## 六场景的确定性契约

server只在实际读到tracer的匹配`armed`事件后回复ARMED，避免arm文件尚未消费就放过A。收到匹配held beacon后，独立snapshot/sysfs/FD/UID复核通过才放行client下一阶段。A为0B控制请求，持门时pending1、预算256；node/generation和PID starttime持续匹配。

| 路径 | 持门期间的实际证据 | 释放后的实际证据 |
| --- | --- | --- |
| UNLINK | 先UNLINK A，再Submit同类B并UNLINK B；B的RET_UNLINK=-104证明worker在A仍停住时处理过取消，B没有目标ioctl entry。后到C为EP82 IN/64B；独立snapshot pending2、queued≥1、预算640，A/C无提前回复，实际持有状态和变更计数不变。 | A的RET_SUBMIT保留真实状态、actual0，UNLINK A=0；二者均在放门后且各一次；配置/接口后的C按新空map返回-32，GET_CONFIGURATION/GET_INTERFACE仍可用。 |
| EOF | 先确认server实际接受A+B：pending2、queued≥1、预算512，再关闭原wire。关闭后仍closing/leased、pending2/预算512，但queued0；FD和对应实际所有权不提前释放、无门后最终恢复。独立DEVLIST为空、第二IMPORT明确busy2。 | 释放真实ioctl，完成worker后续协调及恢复；原wire没有回复，直到原配置/alt/驱动独立匹配才available，待处理资源和预算全部0。 |

SET_CONFIGURATION选择A=1/B=2，A重新配置同一值后IF1变为alt0；SET_INTERFACE选择A=(IF1,0)/B=(IF1,1)。两者真实syscall与A completion均为0，C=-32来自新的无端点映射，不冒称设备产生真实STALL。CLEAR_HALT选择A/B=0x82，当前QEMU ECM模型不实现成功clear，必须原样保留真实syscall及completion的-32，UNLINK A仍为0。

CLEAR/UNLINK还有单独PENDING RPC：probe先收到A completion及UNLINK0，server再确认唯一C为pending1/queued0/**usbfs_inflight1**、预算384且两接口仍usbfs，才允许UNLINK C并要求-104。这样证明真实提交后的pending取消，不能用“短时间无回复”替代硬件提交观察。随后同连接读回配置1/IF1 alt1。

每轮结束均独立核同一sysfs代际、配置1、IF0 alt0/IF1 alt1、两个cdc_ether原驱动，以及available/cleanup_verified、pending/queued/inflight/buffer全0；有界shutdown后实际server和tracer都exit0。CLEAR的真实失败不等于成功clear后恢复，本轮不据此关闭整个R06/R07范围。

## 九次开发运行及全部FAIL

全部运行均使用Linux6.8.0-106-generic、QEMU6.2.0/KVM、默认locked all-features构建。九轮CLI/helper SHA256始终分别为`c033d3931a159ad579977331140a6d327bd86939bfdd19cf794f304527d04db9`、`5d40d8e39286ab722a1abe7a09d1c68e0da9c5f3a7d2273f783b09f95cd64ae0`，guest实际ELF与构建产物相等；六个PASS还使用完全相同的四份新源码。起始commit、dirty、源码/guest/kernel/tracer/probe/module指纹和原始产物均见[开发聚合索引](../evidence/host-state-development-20261023.json)。

| 运行 | 操作/模式 | 裁决与准确原因 | 原始summary / 全部SHA |
| --- | --- | --- | --- |
| dev01 | configuration/unlink | FAIL，baseline记录时jq变量`$label`碰到保留字，编译exit3；实际helper/tracer/普通server已ready，尚未IMPORT或持门。仅改jq变量名为tag并保留JSON键label。 | [summary](../evidence/qemu-host-state-configuration-unlink-dev01-20261023/summary.json) / [SHA](../evidence/qemu-host-state-configuration-unlink-dev01-20261023/artifacts.sha256) |
| dev02 | configuration/unlink | FAIL，coordinator_ready在3.079秒EHOSTUNREACH113；server管理e1000到guest3.023秒才Link Up，未IMPORT/持门。仅增两来宾实际carrier=1的有界启动barrier，不改ARM后期限或重发请求。 | [summary](../evidence/qemu-host-state-configuration-unlink-dev02-20261023/summary.json) / [SHA](../evidence/qemu-host-state-configuration-unlink-dev02-20261023/artifacts.sha256) |
| dev03 | configuration/unlink | FAIL，实际9项probe、双guest PASS/exit0、真实门/取消/映射/恢复均完成，但BEACONS的jq向TTY输出ANSI颜色，严格host JSON解析拒绝。仅给该jq加-M，host不strip ANSI；不将原run改判PASS。 | [summary](../evidence/qemu-host-state-configuration-unlink-dev03-20261023/summary.json) / [SHA](../evidence/qemu-host-state-configuration-unlink-dev03-20261023/artifacts.sha256) |
| dev04 | configuration/unlink | PASS，9检查；B取消-104，真实A成功0且UNLINK0，C新map=-32，同连接读回配置1/IF1 alt0。 | [summary](../evidence/qemu-host-state-configuration-unlink-dev04-20261023/summary.json) / [SHA](../evidence/qemu-host-state-configuration-unlink-dev04-20261023/artifacts.sha256) |
| dev05 | configuration/eof | PASS，7检查；真实入口持门时接受A+B、EOF后512预算保留/closing/busy，释放后原状态恢复。 | [summary](../evidence/qemu-host-state-configuration-eof-dev05-20261023/summary.json) / [SHA](../evidence/qemu-host-state-configuration-eof-dev05-20261023/artifacts.sha256) |
| dev06 | alternate/unlink | PASS，9检查；真实SET_INTERFACE成功0且UNLINK0，C等待新空map=-32，最终原alt与驱动恢复。 | [summary](../evidence/qemu-host-state-alternate-unlink-dev06-20261023/summary.json) / [SHA](../evidence/qemu-host-state-alternate-unlink-dev06-20261023/artifacts.sha256) |
| dev07 | alternate/eof | PASS，7检查；入口持门及EOF后实际claims/FD/512预算不提前释放，真实terminal之后原状态恢复。 | [summary](../evidence/qemu-host-state-alternate-eof-dev07-20261023/summary.json) / [SHA](../evidence/qemu-host-state-alternate-eof-dev07-20261023/artifacts.sha256) |
| dev08 | clear/unlink | PASS，9检查；真实-32和UNLINK0保留，C经PENDING确认真实inflight1后取消-104，连接及原map仍健康。 | [summary](../evidence/qemu-host-state-clear-unlink-dev08-20261023/summary.json) / [SHA](../evidence/qemu-host-state-clear-unlink-dev08-20261023/artifacts.sha256) |
| dev09 | clear/eof | PASS，7检查；持门EOF仍busy/512预算，真实-32 terminal之后才完成原状态恢复与清理。 | [summary](../evidence/qemu-host-state-clear-eof-dev09-20261023/summary.json) / [SHA](../evidence/qemu-host-state-clear-eof-dev09-20261023/artifacts.sha256) |

六个PASS为3×9+3×7=48条probe检查、12guest exit0。每个guest退出码为0并不足以通过host裁决：dev01–03的原FAIL正是保留的反例。[追加顺序原始记录](../evidence/host-state-additional-development-sequence-20261023.json)覆盖dev05–09，不能把它误写成包含dev01–04的完整sequence。

root的[开发汇总复核](../evidence/host-state-development-root-verification-20261023.json)核对六场景summary和96份现有软件输入未变；另一份[独立逐项复核](../evidence/host-state-development-independent-review-20261023.json)直接解析原始wire/trace/snapshot/sysfs，532项全部通过，未调用runner的verify函数，复核前后36份raw产物SHA保持不变。dev03的[独立诊断](../evidence/host-state-dev03-independent-review-20261023.json)虽有76项范围检查通过，仍明确保留overall FAIL；其中对ANSI的派生分析仅用于解释原因，不更改原summary或绕过生产runner的严格解析。

## 静态诊断与来源保全

[静态目录清单](../evidence/qemu-host-state-static-20261023/artifacts.sha256)保留Python/目标BusyBox语法、非guest执行拒绝、不安全token拒绝、同BusyBox `read -n129 -t3`结果，以及原始C编译/普通UID拒绝/analyzer输出。

- [jq保留字诊断](../evidence/qemu-host-state-static-20261023/rustusb-host-state-jq-label-diagnosis-20261023/diagnosis.json)：同jq1.6纯合成输入，旧命令exit3，变量名修复后精确输出exit0，原stdout/stderr及输入逐字节保留。
- [PTY颜色诊断](../evidence/qemu-host-state-static-20261023/rustusb-host-state-jq-pty-diagnosis-20261023/diagnosis.json)：同jq旧761B输出含ESC且strict json.loads拒绝，-M后194B无ANSI并精确接受；原bytes、命令和工具SHA保留，没有通过清洗运行日志绕过host解析。
- C初次静态编译因缺`sys/ioctl.h`失败，后续-Werror编译通过；它是额外编译诊断，不是第十次QEMU运行。两份`-fanalyzer -fsyntax-only`输出仅按语法检查记录，另份真实`-fanalyzer -c`对象编译才作为完整analyzer执行证据。准确范围见[来源说明](../evidence/qemu-host-state-static-20261023/static-provenance.json)。
- standalone probe静态命令当时没有另存stdout/stderr文件，因此不补造raw日志。七份ELF/object仅保留[实际hash及来源限制](../evidence/qemu-host-state-static-20261023/excluded-build-artifacts.json)，不将构建物放入仓库；早期C编译没有保存的源码快照/hash明确标为缺失。每轮实际构建命令、源SHA和构建日志仍在各run summary中。

九个目录分别保存原始build/serial/stderr/summary、外部runner日志、四份准确前置源码及source.json，并附source与目录SHA清单；原字节、CRLF和ANSI保持不动。聚合索引覆盖161份产物：126份run产物、34份静态/派生清单产物、1份追加sequence。原始与派生的provenance逐项区分，`.gitattributes`按raw evidence保留字节。

最终clean提交后的六场景仍待执行，不能以本轮dirty PASS替代。真实硬件G1/G2、内核内部挂起、超时后内核任务终止、成功CLEAR_HALT恢复、任意混合Composite、原无driver的非零alt恢复、ISO实际传输与长期性能均未由本轮证明。

## 固定技术依据

- [Linux man-pages 6.8](https://cdn.kernel.org/pub/linux/docs/man-pages/book/man-pages-6.8.pdf)和[ptrace文档](https://man7.org/linux/man-pages/man2/ptrace.2.html)：syscall-entry在目标系统调用执行之前；线程/clone/exec及GET_SYSCALL_INFO语义。实际guest核验是Linux6.8，而不是把手册版本当作运行结果。
- [QEMU v6.2.0 USB-net](https://github.com/qemu/qemu/blob/v6.2.0/hw/usb/dev-network.c)：ECM配置/alternate/端点和CLEAR_FEATURE真实失败；没有为模型补假成功。
- 固定vendor nusb0.2.8的`maybe_future.rs`、Linux USBFS `device.rs`和已锁定Tokio1.54.1本地源码：blocking task在网络future取消后仍可能执行。本轮以实际入口/出口和资源持有证据约束，不依赖推测任务已撤回。

最终冻结前另做窄启动修订：首次ARM等待覆盖连接、DEVLIST、IMPORT，独立idle上限25秒；ARMED之后RPC仍3秒、门8秒及wire2秒不变。它未包含在上述六次dirty PASS中，聚合索引分别记录开发冻结SHA和修订后的SHA，需clean六场景验证后再作最终结论。
