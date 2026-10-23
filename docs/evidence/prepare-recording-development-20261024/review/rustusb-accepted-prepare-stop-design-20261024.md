# 已接受 USBFS 初始化与准备期 Resource ACK 停顿：只读验收方案

状态：proposed，2026-10-24。未修改仓库、未构建、未执行 Cargo/QEMU、未运行 guest/host USB 或 ptrace、未进行 Git 操作。本报告只提出可执行方案；不新增任何 PASS。源码字节锁见同名前缀 JSON。适用原需求 4.5 的取消与实际资源释放，不宣告完整 4.5、Phase3、kernel 内部硬阻塞或硬件验收完成。

使用项目 rustusb-development 与 writing-technical-design 的相关源码/证据约束；遵循用户限制，仅写此 /tmp 方案，不生成新组件 skill、框架或仓库文档。

## 结论与最小顺序

先补软件层“真实 owner 与延迟收据分离”的测试，再做三个独立双 guest 场景：I=USBFS initializer 的实际 syscall-entry 停顿后 TERM；R=managed USBFS 准备期 Resource 的真实文件 fsync 停顿；N=managed Native export 同一 Resource fsync 停顿。三项都用当前真实产品 ELF、真实 helper 授权、固定 QEMU HID/FTDI；不加入生产 env hook、假授权 helper 或替换设备 FD。I 的同池邻居为另一前台进程，R/N 为同一 agent 的另一既有 job，报告分别标明，不能互换。

当前源码对 I 已保持实际 initializer 总 await，但尚无该窗口的实际 QEMU 证明。R/N 有可定位的缺口：取得真实资源后直接 await Resource ACK，stop 不能在这段等待中进入 close。新验证应在产品修复前保持这个预期失败，不把解除 fsync 门之后才释放判为成功。

另有独立信号缺口：agent 级 SIGTERM 仅向 Runtime 转发 shutdown，若 Runtime 正 await 持久化，尚不会立即向每个 job 广播 stop。首个 R/N 使用已有 StopJob 的独立 watch 通路；如果本轮目标包含 WAL 停顿下的整个 agent SIGTERM，须另修并测直接 stop fanout，不把 StopJob 结果推广到它。

## 1. 已确认的实际边界

| 边界 | 当前源码 | 能证明与不能证明 |
|---|---|---|
| helper 保留授权记录后交付 FD | daemon/linux.rs:168–201、1146–1170；客户端1223–1271 | root 记录 reserve → JSON → 独立 marker → SCM_RIGHTS → validate_received；仅日志 success/记录存在不能证明服务已经接到 FD |
| USBFS 准备已持 FD | service/jobs/rust_server.rs:649–672 | grant ID 先存入 Export，FD 放入 sources，再 await Resource；这里无限 ACK 停顿确实持 FD |
| initializer | 同文件:677–683；backend physical.rs:196–217、observe.rs:352–429 | from_sources 只验证并包装 FD；实际 detach/claim 在 IMPORT 后。不能把 initializer gate 叫作已 claim/已在途 URB |
| initializer 中真实 blocking 工作 | vendor/nusb/src/platform/linux_usbfs/device.rs:119–162、592–621；maybe_future.rs:81–145 | from_fd 启动 blocking closure；读描述符后 USBDEVFS_CONTROL GET_CONFIGURATION，timeout=50ms。保留 caller future 不等于在内核中有强制取消能力 |
| 正常关闭/Finish | rust_server.rs:864–877、698–740；backend physical.rs:274–325；daemon/linux.rs:240–275 | registry close/join → 全部接收 grant 的 Finish → 应用 Closing/Finish。helper 基线 proof 不证明不合作进程没有复制 FD，必须另采原 PID 全部相关 FD 消失 |
| Resource 准入和 ACK | service/context.rs:134–146；runtime.rs:748–754、950–987 | Resource 发往真实 facts channel，Runtime 完成 persist 后才 reply。丢弃 ACK 不等于回滚持久事务 |
| WAL 实际写点 | runtime.rs:266–282；store.rs:403–447 | spawn_blocking 持 store lock；state.next write_all → file sync_all → rename → directory sync_all → 返回 ACK。文件有新内容或 rename 完成都不是最终 durable ACK |
| Native export 准备 | jobs/native.rs:228–241 | 已持 NativeExportHandle 后直接 await Resource；与 stub/client 已有 record_or_stop 不同 |
| 已有可复用取消模式 | jobs/client.rs:174–177、229–241；jobs/native.rs:96–108、770–784、846–847 | 保存同一已 poll recording future，先真实 close/unlock，再 await 原收据；不造新 ACK、不把记录错误归为网络错误 |
| 停止的两个入口 | ipc.rs:188–209；runtime.rs:96–121；ipc.rs:142、runtime.rs:312–320 | 合法 StopJob/StopSession 先同步发 watch，再排队等 durable response；whole-agent signal 目前依赖 Runtime 再 poll。RPC 超时不是“stop 已持久确认” |

这些都是源码事实，不是本轮新运行结论。原 `run-sigterm-qemu.py:147–150` 明确要求 `helper_acceptance_observed=false`，只证明连接进 listener backlog。原 host-state gate 在真实 IMPORT 后拦截 mutation syscall，证明 syscall-entry 顺序；不覆盖初始化 from_fd，也不覆盖应用 WAL Resource 收据。

## 2. 软件先行：测试实际生产共享函数，不做假的 USB 结论

最小生产调整应复用现有 client/stub 的模式：准备期 Resource 记录是可延后的非 owner future；已接受 helper I/O 和已经启动的 initializer 仍总 await。stop 抢先时保留原 recording future，取得/关闭所有实际 handle 和 FD、完成对应 helper Finish 后，才等待原收据和后续 Closing/Finish。不能把 grant 请求/initializer 放入会被 drop 的 select，也不能让 delayed record future 捕获实际 handle。

建议使用模块内 cfg(test) fixture，不增加生产环境变量：

1. **Resource 已 poll、原 ACK 保留、真实资源先关。** 驱动生产准备期记录/cleanup helper，使用真实 UnixStream/pipe owner 和容量受控的 typed JobMessage channel。确认 Resource 已到 receiver；保留原 oneshot，发 job 或 session sticky stop；另一端必须先见 EOF，锁/permit先释放且清理计数恰一次，原 owner task 此时仍未完成。随后原 ACK 先后测试 Ok、Err 和 sender dropped；必须保留 recording 错误、不能重新发送 Resource 或重复 close。不是测试泛型 fake-ACK 适配器，更不是声称实际 USB Finish。
2. **initializer 真 blocking closure 不能被 stop 丢弃。** 若需一处窄内部 seam，把现有“等待已启动初始化结果→stop 分支→关闭”抽为实际生产使用的 owner 函数。测试 closure 在 std 条件变量/有界 barrier 上停住并实际持有 FD；stop 后调用方不能完成、不能发 Finish、FD 对端不能提前 EOF。解除 barrier 后必须 join 同一任务、关闭 FD、再通知 Finish；initializer 返回错误与 cleanup 错误都保留。此项证明软件持有关系，不证明 Linux USB ioctl 的 kernel-active 窗口。
3. **真实 actor/UDS 停止通路。** 复用 Runtime 临时目录与实际 IPC 编码/peer credentials。直接持住实际 store Mutex，让已经发送的 Resource commit 挂在它后面；授权 StopJob 仍应触达独立 watch，未授权 UID/profile 不触发；RPC response 可因原5秒截止超时，不把其当 cleanup。释放 Mutex 后核原事务、序列、correlation、resource、后续 terminal 精确顺序。若调用真实 IPC serve 难以在 cfg(test) 定位 store，可仅私有测试 fixture 暴露 Store Arc，不提供生产 hook。
4. **whole-agent signal 单列。** 上层 stop 到来时，无需等待 Runtime 的 fsync completion 就应给全部 live job/session stop；仍总 await Runtime/worker。先用实际堵住 commit 的 fixture 驱动 serve 的 stop receiver，检查所有 watch 收到且 cleanup先于 ACK；若本轮不改 fanout，保持未验证/不通过，勿借1–3掩盖。

已有 client/native 软件测试 `link_failure_during_an_accepted_ack_cleans_before_waiting_for_that_same_ack`、`stopped_recording_keeps_its_ack_but_does_not_hold_cleanup_lock` 和 `all_close_tasks_start_while_a_sibling_record_ack_is_delayed` 可复用技术，但它们目前不是 prepare export/USBFS initializer 的验收。

## 3. QEMU I：确实已接收 grant 并进入 initializer 后停止

使用固定 Linux 6.8.0-106-generic、两个 disposable x86_64 guest。server 两个固定设备 root0600；普通 broker UID2000，无 capability。同一 helper secure pool 包含 HID/FTDI，opt-in 自动恢复许可为空，不能让自动恢复或测试管理员 bind 代替合作 Finish。

先启动只发布 FTDI 的真实安全前台 sibling（port3241），client 导入并完成35B每方向。另一个只发布 HID 的前台进程（port3242）由 guest-only tracer 启动。两进程同 helper/UID/pool，记录两 PID/starttime，不声称是同一 agent 两 job。

**门选点：** 新隔离 tracer 复用旧 usbfs-syscall-gate 的 per-thread PTRACE_SEIZE/TRACECLONE/GET_SYSCALL_INFO 和原信号转发模型，不能原样改用旧 gate。旧 gate 只拦 mutation、固定UID1000、启动后再arm；本项须在目标 exec 前预先 arm，固定UID2000，并针对真实已收到的 HID usbfs FD 的第一项 initializer `USBDEVFS_CONTROL` syscall-entry。若需严格标识 GET_CONFIGURATION，可只读取/验证固定12B control header（IN/device/standard、request08、value/index0、length1、timeout50），不读取 data 指针或 payload、不改变寄存器/返回值；这是新增测试工具的明确范围，旧“完全不读指针参数”承诺不改写。更保守版本仅记录 first-control-entry+固定源码推断，不能把推断写成实际头部采证。

held 时必须同时具备：root 原授权 record、exact correlation（前台为None）、lease ID、owner PID/starttime/UID、原硬件generation与完整baseline；目标进程真实FD的char/rdev/dev/ino与receipt一致；真实执行CLI ELF；实际被停TID属于该进程，control entry args核对，内核尚未执行该syscall；该FD不由tracer复制或持有。只有root记录或helper success不足以跨过“已交付”门槛。

随后仅停止这个blocking TID，其他线程持续运行。由同UID发送一次实际 SIGTERM 给已验证进程（可向已知主线程 tgkill，避免信号恰排到被门控TID而无法递交），tracer原样递交并记录signal-delivery；不发SIGSTOP、不杀整个agent。held窗口里必须观察：原进程仍活、同FD仍在、root记录未退休、无ready/listen3242；FTDI原client receipt/连接不变，并再次真实35B双向。这里不要求“停止后立刻释放”——实际任务尚未结束，恰好验证不会假报释放。

解除门后不伪造syscall成功：记录实际exit rval/is_error，GET_CONFIGURATION的原50ms deadline不变。从原init返回后不得bind3242，必须join/close实际registry/FD，再Finish。独立核所有目标设备FD消失（不能只核原FD号码）、同代际配置/IF/alt/driver完整baseline、root grant退休；前台可为旧disk1、不造tracked terminal。真实wait0与原stderr完整保留。随后用全新显式进程/新grant成功导入HID、QMP KEY并验证down/up，FTDI旧连接再35B；两服务最后合作停止，双端NULL/Gone与完整baseline另账。

此门只证明**已经进入真实初始化工作的syscall入口、停止后保留任务直到它实际返回并释放**；绝不宣称已经进入内核控制传输、真实不可中断IO、pending URB或永久kernel阻塞。若未来必须验证kernel-active，需要不同内核/设备机制及设计，本方案不扩大。

## 4. QEMU R/N：在真实 Resource WAL sync 尚未完成时释放资源

managed server agent仍UID2000；同一agent两个固定profile/job与不同监听端口。先让只含FTDI的sibling job在port3241完成真实IMPORT和35B双向；再启动只含HID的target job（port3242）。如此停target job不会要求停止sibling，且没有“一个准备中的双设备job尚不能让另一设备I/O”的逻辑矛盾。

**fsync门：** 只trace agent自己的线程，不trace helper。提前arm固定broker data目录中的真实 `state.next` regular-file `SYS_fsync`入口；该点write_all已经完成、尚未file sync/rename/directory sync/ACK。核目录链、owner2000/mode0700、文件0600/nlink1/dev/ino/size≤2MiB，匹配FD真实inode，不按文件名猜测。tracer停候选TID并发布不可覆盖的candidate receipt；guest从该已固定inode读取完整有限JSON，用jq确认目标 exact instance/job/session/attempt、last audit action Resource、resource_id等于root实际授权ID、原state.json仍有同attempt且尚无Resource ID。非目标事务由guest明确skip/release并继续，最多固定候选数，避免在C里引入通用JSON parser。每个候选有唯一seq/nonce/inode；一次最多停一个blockingTID。

可选第二点为rename后的directory fsync，但不能与首file fsync混称；原文件可见不等于完整ACK。首切片只做file fsync即可。

- **R USBFS target：** 原root physical disk3 record + exact tracked UsbfsGrant correlation，target FD实在agent进程，HID原driver仍usbhid，因为未到initializer/IMPORT。将此时的state.next Resource事实、root原件和FD原件一起保存。默认auto restore空。
- **N Native target：** 原root Native disk2 Export记录和真实NativeExportHandle已建立；HID原设备usbip-host、合法当前cfg=None/count=None/IF=[]，原baseline仍preexport配置1/fullIF/usbhid。持有helper监督UDS，target listener还未ready。不能把exported的合法空配置写成当前1。

保持门不松，独立operator UID1000发合法 `agent stop-job <target>`。此IPC独立watch路径无需Runtime commit；CLI/RPC尚无durable response或5秒timeout不能标成StopAccepted，更不能标成cleanup proof。必须通过门未松时的**实际独立结果**判定：R的全部target USBFS FD消失且helper记录精确retire为PhysicalBaselineRestored；N的真实export控制连接关闭、原设备完整baseline恢复且exact NativeExportRestored terminal（不是Gone/PreviousBoot）；target未监听/未发ready。每个proof核kind+UID+correlation+resource ID+device key+原baseline，不能仅pending空或新进程资源0。

同一门保持期间，FTDI原session/attempt/resource/内核receipt保持，实际串口35B双向再次完成；只读agent health可旁证独立通路，但不能用PONG代替字节。由于整个actor正等WAL，普通sessions读可能排队，优先原sysfs/helper原件、已知receipt和实际I/O，不为读取状态松门。禁止要求新Catalog/admission成功，因为新的准入本来需要这个被故意阻塞的WAL。

观察预算只是有限试验窗口（建议cleanup观察≤3秒、gate整体≤8秒，沿既有trace纪律）；到期没真实释放即FAIL，不能先解除门再改判。失败收集完整后再解除门/总await作guest安全收尾，收尾成功不抹原FAIL。不要因新CI/QEMU速度加sleep或放宽旧3秒startup、5秒RPC、50ms控制请求或原cleanup deadline。

释放原fsync门后，必须观察原syscall的真实返回、真实rename及directory sync完成（如追踪），原Resource ACK保留后续Closing/Finish顺序；若请求client已超时仍不允许伪造成功收据/重放mutation。原target session/job终态和root terminal一致；新fresh attempt显式start再IMPORT，实际HID KEY验证，新旧ID不能混；原FTDI连接仍保持直到测试末尾明确合作stop。最后两guest实际退出/内核日志以及两个设备完整恢复独立核。

**重要不对称：** I门要求stop期间不能提前放掉仍在工作中的FD；R/N门要求ACK仍未完成时已经实际关闭资源。把同一个“有界timeout后等门解除”断言用于两类会掩盖问题。

## 5. 软件错误分支与QEMU证据不能互相替代

软件应覆盖原Resource ACK迟到Ok/Err/dropped、实际cleanup失败、重复stop、双设备已取得cap后首项记录卡住、记录与操作错误双保留。错误注入不应在QEMU伪造ioctl/fsync errno（新tracer只hold/release），真实持久化失败另设计；本轮QEMU证明真实成功释放与持有顺序，不证明磁盘损坏或断电耐久性。

R/N首门的target是准备期HID；USBFS此时没有claim、Native此时已经设备级export，这两个baseline变化不能混用。若要求USBFS已claim/已有pendingURB取消，现正常TERM/host-state证据另账；新增测试必须真IMPORT+准确pending证据，不能从此门得出。

## 6. 新文件与复用边界

建议仅新增 `scripts/qemu_owner_stop/{run.py,guest-init.sh,prepare-syscall-gate.c,review.py,README.md}`（名称待root批准），不修改旧sigterm/host-state/secure/agent harness的历史语义。runner可只读复用已锁secure/agent的BusyBox、PKI、固定设备、QMP/FTDI、模块、双通道S0/S1、source/ELF/clean guards和实际TLS配置采证构造器；采用schema1/raw-v1可避免把这一stop实验混入心跳deadline，或使用明确锁定的schema2但不可据超时退出替代目标stop。

固定guest marker、root身份、固定CLI ELF/argv/UID、两合成USB节点、root0700 gate目录必须在任何ptrace/设备操作前验证。有限线程/日志/候选/门时长；不修改syscall、不持复制USB FD、不读私钥/USB payload。只hold一TID，其余线程快速继续；保持clone/exec/exit/signal实际wait语义。tracer失败或deadline必须先留完整原件再做明确失败清理，不产生PASS。不能用全进程SIGSTOP模拟WAL慢，否则relay/control也被停，与待验条件不同。

拟定命令形状（尚未存在、更未执行）：

```
python3 -B scripts/qemu_owner_stop/run.py --case usbfs-initializer --family ipv4 --kernel 6.8.0-106-generic --evidence <fresh-I>
python3 -B scripts/qemu_owner_stop/run.py --case usbfs-resource-ack --family ipv4 --kernel 6.8.0-106-generic --evidence <fresh-R>
python3 -B scripts/qemu_owner_stop/run.py --case native-resource-ack --family ipv4 --kernel 6.8.0-106-generic --evidence <fresh-N>
```

首切片限定三场景IPv4/x86_64；双栈已有回归单独跑，不把本方案推到ARM ptrace ABI。root唯一构建/运行器：先软件与静态审查，冻结clean source/ELF后串行QEMU；每次FAIL新目录保留，修复重跑新source，不覆盖旧summary。

每项原件最少包括：执行argv/ELF与UID四列/caps/PID-starttime；gate候选/held/signal/released/syscall-exit/真实wait；完整helper record/proof与app state.json/state.next明确区分；gate期间与解除后的同代际sysfs/FD/UDS/TCP监听；sibling每轮原始35B与原receipt；新target真实KEY；两guest S0/S1与实际退出；所有source copy与文件长度/SHA/来源前后守卫。测试计数按场景而非诊断assertion或采样次数，原startup acceptance=false继续单独保存。

## 7. 官方语义与固定来源

- Linux ptrace是per-thread；syscall-entry stop发生于syscall执行前，必须原样处理signal-delivery-stop。[Linux man-pages ptrace(2)](https://man7.org/linux/man-pages/man2/ptrace.2.html)
- file fsync与directory fsync是不同的持久化边界；本门不能将已写字节当最终确认。[Linux man-pages fsync(2)](https://man7.org/linux/man-pages/man2/fsync.2.html)
- Tokio固定1.54.1，已本地读registry `src/task/blocking.rs`；已启动spawn_blocking不能靠abort取消，本任务需保存实际join。该版本在线docs读取失败，不用别的版本冒充；固定本地文件SHA附JSON。
- nusb实际为workspace固定vendor源码；本方案依据其from_fd/create_inner/GET_CONFIGURATION，而非泛称“nusb open可能卡住”。固定vendor源码SHA附JSON。

未确定/须实施前审查：新C gate限定12B控制头读取的边界；实际候选fsync的有限跳过协议；stop先于ACK期间跨应用事实的保存方式；若纳入agent SIGTERM，单独fanout实现与验收。以上均不在本轮只读任务里修改。
