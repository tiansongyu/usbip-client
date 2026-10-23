# Rust服务SIGTERM合作停止设计

2026-10-23，基线cf9fd3d22588958486cbbbd771d2f29c2d1b7bcd。状态：旧产品负向保留，Unix合作停止已实现并在clean934358f完成软件、本地及QEMU验证，详见报告。完整范围、真实硬件G1/G2和后续阶段不变。

## 修复前源码与本轮保证

基线virtual_server::run和physical_server::run仅在operations阶段select ctrl_c，main将信号清理交给服务。SIGTERM没有订阅入口；USBFS使用no_reattach，OS关闭FD不等于应用恢复原config/alternate/driver。因此先隔离来宾复现，不能仅据源码宣称永久占用。

本轮只实现Unix SIGINT/SIGTERM合作停止，汇入现有ServerHandle::shutdown并等待真实USB终态、原状态与所有资源核验。SIGKILL、abort/panic崩溃、跨进程监督和持久重启协调保持独立未完成，不把SIGTERM修复称作hard-crash恢复。

## 实现约束

- 使用既有Tokio1.54.1 signal，私有CLI共享对象，无新依赖、unsafe或自定义handler。Unix同步注册SIGINT与SIGTERM，早于任何helper I/O、FD取得或listener创建；失败不启动服务，recv流关闭须报错。
- 尚未拥有listener/lease的prepare（helper授权/FD接收/from_sources）可与signal race取消；不把accepted helper操作仍可能结束写成回滚。spawn_blocking取消不终止已运行内核读取/runtime等待，不能承诺硬阻塞初始化立即结束。bind短步骤不参加会丢Ready(handle)的race；一旦创建server，所有后续错误/EOF/控制shutdown/信号都总await cleanup。
- EventOutput线程创建在bind之前，避免线程创建失败只Drop已运行server。main不新增外层select而丢清理future。重复SIGTERM不提前终止cleanup；保持现有backend10秒清理deadline和错误/隔离语义，cleanup错误优先，不能被普通输出错误隐藏。
- signal对象贯穿setup/operations/cleanup/末输出。取消阻塞stdout事件后in_flight保持true，cleanup完成不向同一消费者重复写stopped；末stopped等待也应能被signal打断，不能只保留对象但完全不poll。原control协议、SIGINT和普通Native客户端取消语义保持。
- 非Unix保留Ctrl-C能力，不声称Windows USB支持；SIGTERM保证限定Unix。signal用于启动一次合作清理，不以合并后的通知数计请求数量。

## 固定源码依据

Cargo.lock的Tokio1.54.1 package checksum为b52fd3255258d26fac7264ab5a76b53c13367136dc011d3d6ad4f8ed070396a3。缓存crate archive实际SHA与之相同，以下两文件逐字节等于该archive。registry未包含.cargo-checksum.json，未假造其存在。线上docs.rs对应三个页面本次工具访问失败，以实际固定包源码为依据而非借用其他版本。

signal()同步注册、Signal::recv支持select取消，通知可能合并，handler不会随对象drop恢复默认；由此只等待一次有界合作停止。源码SHA如下：

- `tokio1.54.1/src/signal/unix.rs` SHA256 `8dcfb6950a39c1fd27c500670cc24e7df4c95278b6e7e52e7629b25e91fe49ea`。
- `tokio1.54.1/src/signal/ctrl_c.rs` SHA256 `9b2a47659fe106805036a664310040c07d98c061d7104270d9a641025cc875f0`。

## 验收与过程

1. 新harness先在旧产品上运行真实负向断言。宿主普通UID，不访问宿主USB、模块、ptrace或网络设置；仅一次性guest操作管理员指定的合成非存储设备与已核PID/starttime，程序ttyS1/kernel ttyS0双通道，严格JSON/marker不拼补。
2. 物理普通UID1000经root helper取得授权FD。真实HID/FTDI内容I/O后保持两个lease和真实pending/inflight/预算，独立确认usbfs claim；仅guest发一次SIGTERM。先保存真实waitcode、client旧节点/receipt终态、同generation的driver/config/alternate与FD观察，再判定。旧wait143不允许提早跳过观察，旧无stopped则记缺失，不伪造资源0。
3. 修复后相同条件要求exit0、原driver/config/alternate恢复、节点权限与代际、全部pending/queued/inflight/budget0和清理证据。socket EOF/进程退出不能代替backend恢复。
4. 虚拟服务独立child测试覆盖ready与stdout4096B满不drain时的SIGINT/SIGTERM：先实际IMPORT/SET_CONFIG1/HID IN8及snapshot leased/pending，再仅signal自身Popen child。正常stdout须stopped资源0；满stdout只做其可证明的exit/EOF，不冒充物理恢复。旧TERM真实-15（shell143）保持FAIL，原INT/deadline回归保留。
5. 开发后默认/全部feature的fmt/Clippy/tests/release/严格Rustdoc，随后同clean冻结QEMU与必要回归。记录每次失败、前置精确源码、真实日志及SHA，适当commit并普通push。修复后实际软件、本地与QEMU结果分别归于934358f，CI39899971271已实际四平台通过；不将源码审查代替运行，见[报告](server-signal-shutdown.md)。

旧产品[本地两项负向](../evidence/server-sigterm-old-local-development-20261023.json)均实际Popen -15；[QEMU负向](../evidence/server-sigterm-old-qemu-development-20261023.json)的startup与两lease主服务均实际wait143。主服务退出后完整20.04秒观察中，同代际cfg1/alt0仍在，但原usbhid/ftdi_sio均未恢复，实际driver为null。客户端自主断连不代表服务端恢复；无stopped也不能推导末reap/资源数，更不推导永久泄漏。


[产品入口设计复核](../evidence/server-sigterm-design-review-20261023.json)与[独立生命周期/本地验收复核](../evidence/server-sigterm-lifecycle-independent-design-review-20261023.json)保留各自源码事实与测试边界。候选1762d65在独立worktree完成后，经独立源码审查合入为d39517b；先完成旧产品负向再应用修复，原FAIL保留。
