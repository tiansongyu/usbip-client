# Shared owner：窄修复复核与最小回归矩阵

2026-10-24。只读审查；没有修改仓库、编译、执行 Cargo/QEMU/USB 操作或调用任何 runner verifier。HEAD 为 `9314aa08c41d84672467b06df8d0cf51e5d7ecf2`，当前为已授权但尚未提交的 shared-owner 实施工作区；源码快照及旧证据 SHA 见同名前缀 JSON，不能将此前运行归属到本次工作区。

## 两项原问题已按源码闭合

1. CLI `foreground.rs:25,57,72`：Native 模式的畸形输入或不支持的 HID/CDC 控制转换为 Invalid，随后发送 Eof 并结束 stdin reader；真实 owner 继续执行 cleanup。virtual/physical 对长度合法但解析失败的输入仍继续。stdin 仍在 Ready 输出 ACK 后才启动，没有把 stdout delivery 当 durable admission。
2. `rust_server.rs:20,31,468,488,559,637,671,809,852,881`：Begin/Attempt/Resource/Available 记录收据错误属于 Recording；helper/backend/network/output 错误属于 Operation。observer 失败只触发停止，原错误由 observer join 保留。服务 shutdown、registry close、每份 helper Finish 与实际 observer join 仍完成，不因为记录错误改成虚构 operation 成功或跳过 cleanup。这里 operation 为 Ok 仅表示未发生 operation 类失败，最终 `JobOutcome` 仍因 Recording 返回失败。

额外发现的旧 Status 等待兼容性回归，也已在 root 新增的源码修复中闭合：`rust_server.rs:590–598` 仅对尚未交付能力的只读 Status 使用 biased stop-select。实际 grant 请求和 `PhysicalBackend::from_sources` 仍直接 await；没有把授权后取消重新变成 drop。软件和实际 QEMU 是否通过由后续集中运行决定，本报告不预判 PASS。旧 startup 3 秒断言必须保留。

此次复核无剩余具体 must-fix。以上是源码结论，新增回归测试的通过状态不由本审查代替。

## 本轮执行矩阵：9 个既有 run，预期 18 个 guest

在最终 clean 提交冻结输入后串行执行。下列命令是运行计划，本报告未执行；每次 evidence 路径必须不存在，保留任何首 FAIL。默认 locked build，不使用 development `--skip-build`。

```sh
python3 scripts/qemu/run-secure-qemu.py --backend virtual --family ipv4 --evidence /tmp/rustusb-shared-owner-secure-virtual-ipv4
python3 scripts/qemu/run-secure-qemu.py --backend virtual --family ipv6 --evidence /tmp/rustusb-shared-owner-secure-virtual-ipv6
python3 scripts/qemu/run-secure-qemu.py --backend usbfs --family ipv4 --evidence /tmp/rustusb-shared-owner-secure-usbfs-ipv4
python3 scripts/qemu/run-secure-qemu.py --backend usbfs --family ipv6 --evidence /tmp/rustusb-shared-owner-secure-usbfs-ipv6
python3 scripts/qemu/run-secure-qemu.py --backend native --family ipv4 --evidence /tmp/rustusb-shared-owner-secure-native-ipv4
python3 scripts/qemu/run-secure-qemu.py --backend native --family ipv6 --evidence /tmp/rustusb-shared-owner-secure-native-ipv6
python3 scripts/qemu/run-agent-qemu.py --backend native --family ipv4 --evidence /tmp/rustusb-shared-owner-agent-native-ipv4
python3 scripts/qemu/run-agent-qemu.py --backend native --family ipv6 --evidence /tmp/rustusb-shared-owner-agent-native-ipv6
python3 scripts/qemu/run-sigterm-qemu.py --evidence /tmp/rustusb-shared-owner-legacy-sigterm
```

六个前台 run 保留真实 TLS 管理+长数据连接、双设备两周期实际 I/O、端口/代际消失、TERM 与重用、Native 原完整 driver/config/IF 基线及 untracked journal 语义、原 pcap 分类和双栈无明文入口断言。两个 Native agent run 保留独立 RPC 与 job owner、精确 tracked Root proof、定向停止另一设备继续、137 与恢复、新 attempt 和实际 TLS 配置采证。原 legacy SIGTERM run 保留 Status 尚未被接受时的 3 秒退出，以及 Ready 后双设备实际 I/O/持有资源的合作停止。

每组完整核 source/head/clean/ELF、S0/S1、实际 wait/两 guest 退出；中间 marker 或新进程资源 0 不能代替旧能力清理。六前台与两 Native agent 是不同边界，不能互相替代。现 agent runner 仅支持 `native`，本矩阵没有验证托管 Rust virtual/USBFS QEMU 或 managed physical disk3。

另行保留完整软件 gate、Native-only feature、原 plaintext 四信号/两背压，以及新增真实 TLS 五场景子进程测试。它们分别记账，不加入上述 QEMU run/guest 数量：

```sh
python3 scripts/test-server-signals.py --evidence /tmp/rustusb-shared-owner-local-signals
python3 scripts/test-virtual-backpressure.py --binary target/release/rustusb
cargo test -p rustusb-cli --all-features secure_foreground_tests::secure_foreground_real_tls_signals_and_stdout_backpressure -- --exact --nocapture
```

最后一条是目标测试示例；完整 gate 如已执行同一测试，不需为计数重复运行。新增 TLS fixture 使用测试内 programmatic DER/config，不据此宣称生产可信配置 loader 被该测试覆盖。原两背压脚本没有 evidence 参数，应额外保存其 stdout/stderr/退出码和实际 binary 来源。

## accepted initializer 的现有证据不足；本轮不扩大 harness

抽查历史 `physical-autorestore-final-2ab9976-20261023/physical-sigterm/summary.json`：原 commit `2ab997673a5894099f8df770707a060da3735154`，原 PASS 保留；startup 明确 `helper_acceptance_observed:false`、没有 Ready/listener、实际 wait 0。`guest-sigterm-init.sh:149–232` 先 STOP helper，再观察连接进入 UDS backlog，TERM 后在 helper CONT 之前要求 3 秒内退出。这证明 Status 未交付能力的取消，不证明 helper 已发 grant 或 USBFS constructor 已进入。

旧 host-state tracer 也不重叠：`guest-host-state-init.sh:178–184` 等实际服务 Ready 后才进入后续 gate 操作；`usbfs-syscall-gate.c:538–607` 选择 configuration/alternate/clear/release 的 ioctl-entry。它们不覆盖 startup descriptor read；更不证明任意 in-kernel active 阻塞。

当前源码约束可定位到 `rust_server.rs:678` 直接 await `PhysicalBackend::from_sources`；后者 `physical.rs:198–219` 逐个 await `observe::initialize`。`physical/observe.rs:352–427` 核授权 receipt/原 node 和 sysfs 后进入 `nusb::Device::from_fd(fd).await`；固定 vendored `device.rs:119–140,569–575` 的 blocking closure 实际 seek/read descriptor。stop 返回后才检查是否 bind，close 后再 Finish。该约束不能当作已执行 kernel 硬阻塞验收。

后续独立设计可选 guest-only tracer，在初始化阶段匹配实际 helper-granted USBFS FD 的第一次 descriptor `read` syscall-entry，严格关联 PID/starttime、TID、FD node/rdev/inode、lease/receipt/原基线。只 hold 该 blocking worker TID，让 Tokio 信号线程仍可处理 TERM；不能 STOP 整个进程后宣称测到 task await。需要同时证明 grant 已交付、尚无 listener/Ready、TERM 后不提前退出/Finish；释放 gate 后记录真实 syscall-exit→initializer 返回→owner close→Finish、完整基线，再新 grant 真双设备 I/O。该新场景尚未实现，不能给出貌似可执行的新 runner 命令，也不能使用原旧证据填空；即使将来通过，也仅证明受控 syscall-entry 等待，不是任意内核硬阻塞或硬件 G1/G2。
