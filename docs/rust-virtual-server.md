# 运行实验性虚拟 USB 服务

此服务提供固定的虚拟 HID 键盘与 CDC ACM 回显串口，不访问本机物理 USB。客户端仍通过 Linux VHCI 枚举设备。物理 rusb/nusb、ISO、任意 Composite/alternate、认证和 TLS 尚未实现；完整设计与验收范围见[实施设计](research/rust-virtual-backend-design.md)。

## 构建和启动

```bash
cargo build -p rustusb-cli --features rust-backend --release --locked
target/release/rustusb virtual-server --control-stdin
```

默认监听 `127.0.0.1:3240`。普通用户运行，无需 root helper 或 usbip_host；导入端的 Native helper/VHCI 仍按[既有流程](native-guide.md#native-共享流程)由管理员配置。

仅在已授权的隔离测试网络，可显式指定监听和 peer：

```bash
target/release/rustusb virtual-server \
  --listen 192.0.2.1:3240 \
  --allow-insecure-test-network --allow-peer 192.0.2.2 \
  --control-stdin
```

这两个选项仅是测试前提声明和源 IP 筛选，不提供身份认证或数据加密。固定设备为 `1-1`（HID，1234:0001）和 `1-2`（CDC，1234:0002），Full-Speed；VID/PID 是隔离测试标记，未声称注册或商用分配。每台设备只有一个导入者，busy IMPORT 返回状态2，已占用设备从可用 DEVLIST 隐去。

## 本地测试输入

启用 `--control-stdin` 后，每行一个 JSON 对象，每行最多4096字节。`id` 是1–64字符的 ASCII 字母数字、点、下划线或连字符。所有服务事件均为 stdout JSONL `schema_version:1`，日志在 stderr；普通日志不记录 USB payload。

```json
{"id":"key-1","op":"hid_key","key":30}
{"id":"serial-1","op":"cdc_emit","hex":"525553545553422d3031"}
{"id":"check-1","op":"snapshot"}
{"id":"done","op":"shutdown"}
```

`key` 是 USB HID usage，4–101；30/31 对应数字1/2。一次操作排入完整8字节按下及8字节释放报告。CDC 的 hex 每次编码1–1024字节，由服务器向客户端 ttyACM 发送；客户端 OUT 数据也回显至 IN，队列有上限，满时明确拒绝。未导入设备不能接收本地测试输入，避免跨会话残留。

ready 事件给出实际 `address` 和虚拟设备快照；操作成功返回 `event:ack`、原 id 和 `ok:true`。snapshot 返回 `event:snapshot`、id、`snapshot`；错误仅返回稳定 code，不回显原始输入。shutdown 先 ack、等待全部连接清理、最后输出 stopped 和释放后快照。stdin EOF 或 Ctrl-C 也触发关闭；Unix还支持SIGTERM。未启用stdin控制时，可用Ctrl-C或Unix SIGTERM停止。

stdout 由专用线程输出，有界队列2、单事件64KiB、确认截止5秒。消费者不读取时，超时报错并释放服务；Ctrl-C或Unix SIGTERM可直接进入清理。若信号中断尚未完成的输出，关闭后不再等待同一 stdout，因此可能没有完整 stopped 事件。此保证针对虚拟控制输出；显式 debug tracing 的阻塞 stderr sink 仍是后续日志系统需处理的边界。

快照中的传输计数、队列长度和 hash 用于测试诊断，不能当作实际硬件兼容性或吞吐基准。只有 QEMU 的实际 evdev/ttyACM 与独立 wire 证据能支持相应的 profile 验收；最终结果在[工作日志](work-log.md)登记。

Unix信号合作停止的本地及QEMU验证见[报告](research/server-signal-shutdown.md)。信号监听早于listener创建，清理完成后的末输出也可被信号打断；SIGKILL/崩溃恢复仍未实现。
