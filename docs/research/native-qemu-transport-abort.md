# Native 双 QEMU 主动 TCP 中止验证

本测试使用隔离的 Linux guest 和 QEMU 模拟键盘，验证已经交给内核的 USB/IP TCP 被主动中止后的资源释放、helper 所有权清理与重新导入。它是确定性的 **transport abort** 测试，不是 WAN 黑洞、丢包、心跳或默认 TCP 超时性能测试，也不提供真实物理 USB 兼容性证据。

## 故障注入依据

管理员在 server guest 内使用 `ss -K`，选择唯一一条 `192.0.2.1:3240 → 192.0.2.2:<实际临时端口>` 的 ESTABLISHED 连接。注入前断言筛选结果恰好一条，再携带完整四元组执行中止；监听 socket、测试控制连接和其他会话不在筛选范围内。

实际开发运行的摘要记录 `ss_version: "ss utility, iproute2-5.15.0"`，同时记录复制进 guest 的发行版 ss 二进制 SHA256。最初预研查看的是上游 v6.1.0（`83699d3d5077bbb3957030938f746601317767d2`），它只作为源码对照，未作为实测工具版本。现已补读实际版本对应的上游 v5.15.0；发行版二进制可能包含打包补丁，不能把版本字符串与某个上游提交视为逐字相同的构建来源。

- `git ls-remote` 解析 v5.15.0 annotated tag 对象 `dd133a21cfc52d87960eaecef91b76327c6c780a`，解引用提交为 `7a8b7573a49ee15ff4ae9cdce14fda74b62075a4`。该提交 [`kill_inet_sock`](https://github.com/iproute2/iproute2/blob/7a8b7573a49ee15ff4ae9cdce14fda74b62075a4/misc/ss.c#L3545-L3560) 将选中诊断记录的完整 ID（包含 cookie）复制到 `SOCK_DESTROY` 请求；这一关键路径与 v6.1.0 预研结论一致。核对的 `misc/ss.c` SHA256 为 `931a7aba9b88afd80bbf434801436ef4ffb06bf0e48b33757e45d815b0ab37cb`。
- Linux v6.8 固定提交 `e8f897f4afef0031fe618a8e94127a0934896aba` 的 [`tcp_diag_destroy`](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/net/ipv4/tcp_diag.c#L188) 调用 `sock_diag_destroy(sk, ECONNABORTED)`；[`sock_diag_destroy`](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/net/core/sock_diag.c#L301) 要求该网络命名空间的 `CAP_NET_ADMIN`。
- 同版 [`tcp_abort`](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/net/ipv4/tcp.c#L4515) 设置 socket 错误并通知等待者，在对应 TCP 状态下发送 active reset，最后结束 TCP。USB/IP 的 shutdown/reset 与 ERROR 边界另见[生命周期审查](native-transport-lifecycle.md)。

杀掉 `usbipd` 不能保证销毁内核已经持有的连接；QMP carrier down 也不等于 TCP 收到错误。本测试不使用这两种操作代替真正的 TCP 中止，也不使用 `usbip detach` 冒充故障。没有修改 keepalive、TCP sysctl 或网络延迟参数；15 秒是测试观察截止，不是产品黑洞检测承诺。

## 三周期验收

普通 UID 1000 通过各端 root helper 操作，root 仅在一次性 guest 内执行明确的测试注入。

1. RustUSB export/attach 后核对 VID/PID、完整 session receipt 和 `usbhid`，通过 QMP 注入并在客户端 evdev 验证 `KEY_1`。随后进行第一次精确 `SOCK_DESTROY`。
2. 两端分别在 15 秒截止内观察释放：客户端指定端口 NULL、活动 sessions 为零、旧 sysfs 设备消失；服务端 `usbip_status=1`、目标 ESTABLISHED 四元组消失，原 `usbipd` 和 helper 仍存活。服务端保存观察后通过独立的私有控制端口应答，客户端才允许开始下次导入，避免新连接遮蔽短暂 AVAILABLE。
3. 客户端通过 helper 对已经由内核释放的自有记录执行 detach 清理，再次 attach 并验证第二次实际按键。随后第二次中止；同样核对两端释放，但这次不显式 detach，直接由 RustUSB attach 协调旧记录。
4. 第三次连接必须仍为 managed、新 sysfs 代际且实际按键成功。最后正常 detach、unexport，服务端设备级驱动恢复 `usb`、接口驱动恢复 `usbhid`。

不要求轮询恰好捕获瞬态 ERROR；普通 TCP 断开通常走 shutdown/reset 后回到 NULL。若最终状态无法确定或未在截止内释放，测试失败并保留原始证据，不把它写成成功恢复。

## 运行与隔离

```bash
python3 scripts/qemu/run-fault-qemu.py
```

默认执行 `cargo build --workspace --release --locked`，guest 总运行截止 180 秒。可用 `--evidence <新目录>` 指定证据位置；开发定位可使用 `--skip-build`，摘要会记录这一事实。当前 runner 限 x86_64，要求测试内核具备 `CONFIG_INET_DIAG_DESTROY=y` 和匹配的 `inet_diag` / `tcp_diag` 模块文件，以及 QEMU、BusyBox、cpio、jq、setpriv、ss 和匹配的发行版 USB/IP 工具。

runner 复用现有 `run-native-qemu.py` 的二进制/库/模块复制和 QMP 函数，只覆盖临时 initramfs 的 `/init` 并加入诊断工具。两 guest 通过私有 socketpair 网络相连；没有 host USB passthrough、tap、公网监听、宿主模块加载或宿主网络变更。只有 `/boot` 内核镜像不可读时，沿用现有 runner 的 `sudo -n cp/chmod` 复制到临时目录流程。

每次保存两端原始 serial/stderr、构建日志、唯一 TCP tuple 和 `ss -K` 输出、注入前后 TCP/sysfs/helper 快照、三个完整 receipt、dmesg 和 summary。摘要包含 Git commit/dirty 状态、Rust/lock/旧依赖脚本与新脚本指纹、二进制、内核、initramfs、原生工具、ss 和附加模块 SHA256；最终 PASS 还要求两 guest exit 0 与运行后源码指纹一致。失败也写 summary，保留已得到的现场。

## 开发记录与结论边界

首轮开发结果保存在 [`qemu-transport-abort-dev-1-20261022`](../evidence/qemu-transport-abort-dev-1-20261022/summary.json)，**FAIL**。真实 TCP abort、客户端 NULL/旧设备消失、helper 已释放会话清理及第二次实际按键已观察到；但客户端过早重连使服务端轮询错过短暂 AVAILABLE，后续控制请求失败。该轮属于测试同步缺陷，不是完整验收，也不能忽略失败。修复是增加服务端已完成释放观察的显式应答，未修改产品代码或内核超时。

第二轮 [`dev-2`](../evidence/qemu-transport-abort-dev-2-20261022/summary.json) 仍为 **FAIL**：两次 TCP 中止、三次实际按键、客户端全部释放与直接重连通过，但最终正常 detach 后服务端尚在异步 reset，立即断言 AVAILABLE 失败。harness 改为在同一 15 秒截止内等待服务端 AVAILABLE，再通过原有 helper 调用 unexport，没有放宽产品拒绝 occupied 设备的规则。同时将 evdev 订阅改为父 shell 先打开设备再宣布 QMP 输入就绪，避免后台子进程尚未订阅而丢键。

第三轮 [`dev-3`](../evidence/qemu-transport-abort-dev-3-20261022/summary.json) 完整 **PASS**，两 guest 均 exit 0，运行后源码指纹未变：两条实际 TCP tuple 各由唯一 ESTABLISHED 变为零，服务端回到 AVAILABLE，客户端三次释放、三次 `KEY_1`、已释放自有记录清理、无显式 detach 的新代际重连及服务端本地驱动恢复全部通过。原始内核日志包含服务端 `recv a header, -103` 和客户端 `connection reset by peer`。本轮是 dirty 工作树的开发验证，尚不是冻结提交的发布证据。

后续开发及冻结提交的最终裁决由实际 summary 和工作日志记录。未经最终冻结重跑的开发 PASS 不能替代发布证据。此测试不证明存储异常恢复、长期并发、多客户端争用、网络丢包/延迟、断网黑洞、TLS、CDC 或真实设备支持。
