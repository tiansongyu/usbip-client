# USBFS host-state 取消与断线测试设计

2026-10-23（Asia/Shanghai），基线 `fbc856c`。状态：独立guest harness已实现，六场景在同一clean `c921a27`最终通过；开发六PASS和三FAIL保留。承接 [Composite 最终报告](composite-qemu.md) 明确保留的 active 操作窗口缺口；不修改 G1/G2 真实硬件结论。

## 需要证明的契约

`physical/worker.rs` 将 SET_CONFIGURATION、SET_INTERFACE 和 CLEAR_HALT 放入 `ControlAction::Special`。它们通过 nusb 的 blocking task 执行，网络 UNLINK/EOF 不能撤回已经启动的任务。当前 QEMU 仅观察 queued 取消赢，以及写入配置请求后立即断线再 available，不能确定 server 接受了请求，更不能证明操作已进入阻塞线程。

新增回归须观察真实 physical worker、真实 helper FD 与实际 QEMU USBFS 调用，而不靠给 server 的抽象 AsyncBackend 填写相同 setup 字节来宣称覆盖。需要证明：

- 操作已进入 blocking 线程后，UNLINK 不提前生成取消成功或丢失原 terminal；释放门后保留真实成功/错误，原 SUBMIT 与 UNLINK 的完成赢行为准确。
- 同一窗口 EOF 后仍独占 slot、保留请求预算与授权 FD；操作 terminal 之前不开始最终 restore，不提前 available。真实 terminal 后恢复实际驱动/配置/alternate，最终资源归零。
- 后续依赖端点映射的请求等待状态操作完成；真实错误不能伪造成成功。每个观察均记录操作、case、单调时序和来源，不默认输出 USB 数据。

SET_CONFIGURATION 本来先释放旧 interfaces，再调用 ioctl；因此不能断言其 ioctl 入口仍持有全部旧 claims。该操作需要证明 slot/FD/预算保留和 EOF 不触发提前最终恢复。SET_INTERFACE/CLEAR_HALT 则另核相关 interface/endpoint 所有权。

`usbfs_inflight` 只统计 raw URB，Special 进行时可以合法为零；不得据此判断 host-state 已终态。

## 选择测试边界

优先评估新增独立 QEMU harness 和仅来宾使用的 ptrace 协调器，不为测试把生产 Slot/Worker/Endpoint 全部改为 mock façade，不添加生产 test feature 或修改 vendor。

root 协调器只追踪本次启动的普通 UID1000 server 及其线程。按明确 arm 的 ioctl request，在目标 blocking 线程的 **syscall-entry stop** 暂停该线程；其余 runtime 线程继续处理 UNLINK、EOF 和只读控制快照。只有观察到目标入口、确认 fd 是本次授权 USB 节点后才放行 client 的取消/断线阶段。释放门后继续执行真实 ioctl，记录对应 syscall-exit 和真实返回值。

[Linux man-pages 6.8](https://cdn.kernel.org/pub/linux/docs/man-pages/book/man-pages-6.8.pdf) 与 [ptrace 手册](https://man7.org/linux/man-pages/man2/ptrace.2.html) 说明 syscall-entry 位于系统调用执行之前；PTRACE_O_TRACESYSGOOD 配合 GET_SYSCALL_INFO 区分 entry/exit，TRACECLONE 跟踪后续线程，EXITKILL 防止 tracer 异常退出后 tracee 失控。实现须处理 clone、exec、signal 与退出事件，不能简单交替假定 entry/exit。

这个门只能证明真实 Special future 已进入不可取消 blocking 线程并到达系统调用入口，**不能称内核 USB 驱动内部已执行或阻塞**，也不替代真实硬件故障。若后续仍需要内核内部 active 窗口，应另建设备/内核故障注入，不能升级本轮证据措辞。

## 隔离与失败规则

- 仅一次性 x86_64 Linux6.8 QEMU 来宾执行 tracer；检查 root、QEMU 专用 kernel cmdline 标记和固定测试目录，宿主不得 ptrace、加载模块、修改网络或打开真实 USB。
- server 仍为 UID1000、零 effective/permitted/ambient capabilities，helper 为独立受控 root 进程；tracer 不追踪 helper，不扩大 helper 策略。
- 从启动时跟踪且处理所有既存/新线程；只保持目标线程停止，其余线程恢复。记录 GET_SYSCALL_INFO 的 nr/fd/request，不读取或修改 USB payload、不改 syscall 参数和返回值，不用 PEEKDATA 解码任意指针。
- 控制文件/管道位于 root 管理的私有来宾目录，case nonce 唯一且拒绝过期门；arm/entered/release/exit 均须精确对应同一 TID/FD/request。单个门、每个 case、tracer 和整个 QEMU 均有截止；失败后终止并回收所有测试进程，不能把自动放行或重试当 PASS。
- 启动、UID、fd 验证、门命中、等待行为、terminal 和最终 cleanup 任一缺失均 FAIL。默认构建并保存 source/binary/kernel/tracer 哈希、原始日志与全部失败；先开发验证，再提交干净冻结复跑。

## 最小矩阵

| 操作 | active 后 UNLINK | active 后 EOF | 真实结果与恢复 |
| --- | --- | --- | --- |
| SET_CONFIGURATION | 无提前响应，释放门后完成赢 | terminal 前保持 busy，之后恢复 | 真实配置变化及独立回读，最终原配置/双驱动/alternate |
| SET_INTERFACE | 无提前取消，后续映射请求不得越门 | terminal 前保持 claim/FD/预算 | 真实 alt 变化，最终原 alternate 与驱动 |
| CLEAR_HALT | 保留模型真实错误，UNLINK0 | 错误 terminal 后才清理 | 不伪造 QEMU 模型缺少的成功 clear；控制连接可继续 |

queued 取消已有观察，但本轮若声称确定性覆盖，必须再建立明确排队门并证明目标 ioctl 没有开始；不能只沿用一次竞态 winner。期限隔离、kernel 内部挂起和真实设备行为是独立后续范围。

实现前先验证 tracer 的线程与 syscall 生命周期可行性；若无法在保持普通 UID、严格截止和原协议断言的条件下实现，应记录实际失败和限制，不退回抽象 fake 却沿用真实 worker 覆盖结论。

## 实施约定

新增四文件，旧 runner 和生产 Rust/vendor 均保持：`scripts/qemu/run-host-state-qemu.py`、`guest-host-state-init.sh`、`host-state-wire-probe.rs`、`usbfs-syscall-gate.c`。每次运行参数 `--operation configuration|alternate|clear --mode unlink|eof`，一次两 guest；三类各两种模式在同一 clean 提交依次冻结复跑。

协调器调用形式：`usbfs-syscall-gate --directory /run/rustusb-gate --usb-node /dev/bus/usb/BBB/DDD -- /usr/bin/setpriv ... /usr/bin/rustusb ...`。保持 child 的 stdin/stdout/stderr 用于实际 server；追踪器自己的事件仅写 root0700目录。kernel cmdline另带`rustusb.host_state_test=1`。child只接受固定前缀`/usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb`，其后为实际physical-server参数。额外`ready.json`仅在实际rustusb exec、UID/GID1000和CapEff/Prm/Amb均0后生成。目录含 `tracee.pid`、`arm`（`<nonce> <operation>\n`）、`held.json`、`release`（`<nonce>\n`）、`exit.json`、`events.jsonl`。只接受一次 arm；nonce 为安全 ASCII token。匹配 `SYS_ioctl`、白名单 request 及与指定字符节点 dev/inode/rdev 相同的实际 FD，不读取参数指针。held/exit JSON均包含 nonce/operation/tid/fd/request、节点身份、monotonic_ns；exit 另含 signed return_value/is_error。追踪期间额外统计同一FD的 claim/release/config/interface/clear/driver ioctl 白名单，持门期间不得出现提前 cleanup 的变更；不记录数据或指针。单门8秒、进程120秒硬截止，超时只能FAIL并结束测试。

独立Rust probe在client普通UID运行，参数为 `<operation> <mode> <nonce>`，USB/IP连接 `192.0.2.1:3240`；与server root shell通过独立测试TCP `192.0.2.1:5002` 单连接双向行协议协调，每行不超过128B。server先发 `READY <nonce>`；后续命令与成功响应为同名命令加nonce，只有 `ARM` 成功响应名为 `ARMED`：

- `ARM`：IMPORT已成功，server准备门；之后发A。`HELD`：等匹配入口并独立核slot/budget/FD/UID，再响应。
- unlink模式：UNLINK A，随后Submit B同类Special再UNLINK B；独立收到B的RET_UNLINK -104，证明真实worker已经在A仍持门时处理取消。此时A不得有terminal，B不得到达对应ioctl。再发C=IN EP82/64B，并`WITNESS`等待pending2/queued>=1、预算640（A256+C384）及保持busy；放门前A/C不得提前响应。
- eof模式：A持门后发B同类0B Control，`ACCEPTED`等待pending2/queued>=1、预算512，确认server真实接受；关闭USB/IP TCP，再`CLOSED`等待真实closing/queued0但pending2/预算512，原FD保持且没有门后提前恢复。独立DEVLIST不列出此slot、第二IMPORT仍busy。
- `RELEASE`：写匹配nonce，等待真实syscall-exit，原retval不得改写。unlink模式随后精确核A completion和UNLINK0，C行为及同连接GET_CONFIGURATION/GET_INTERFACE健康；eof模式没有旧网络回复。
- `RESTORED`：probe先关闭原wire（unlink模式），server等待同代际原cfg1/alt0,1/双cdc_ether恢复和全部pending/queued/inflight/budget0、available/cleanup_verified。`DONE`：有界shutdown实际server并等待tracer和server正常退出后回应，两个guest最后各自PASS退出。

A/B选择：SET_CONFIGURATION A=1/B=2（A重新配置后IF1回alt0），SET_INTERFACE A=(1,0)/B=(1,1)，CLEAR_HALT A/B=0x82。A配置/接口真实返回0，clear严格保留当前QEMU ECM的-32。配置/接口后C须按新空map返回-32；clear失败后C先发额外`PENDING <nonce>` RPC，server等待snapshot唯一pending1/queued0/usbfs_inflight1/预算384再回应，probe才取消C并要求-104，证明真实提交后的pending而非只取消排队；不能因失败损坏原映射。若发行版实际行为不同必须保留FAIL并调查，不放宽原errno或新映射断言。

所有成功条件必须由实际trace入口/出口、wire字节、server事件和独立sysfs/FD观察共同支持。门期间读取内存snapshot不会碰被暂停线程持有的nusb接口mutex；不得对同接口调用阻塞的alternate查询造成测试死锁。

## 最终冻结前的启动期限复核

初始READY写入FIFO时，TCP客户端可能尚未连接，随后还需DEVLIST与IMPORT；此前初次ARM和后续RPC共用3秒，短于客户端15秒初始连接预算，可能造成慢启动假FAIL。仅初次`read_rpc ARM`使用独立25秒idle上限，作为该启动阶段的独立总截止，后续RPC仍3秒、单门8秒、wire单帧2秒；不重发已接受的USB请求，不放宽任何取消/恢复断言。六次dirty开发PASS保留原始四源码SHA，该启动修订随后在同一clean `c921a27`的六场景全部通过，见[报告](usbfs-host-state-races.md)。
