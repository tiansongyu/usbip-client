# USBFS Composite 与备用接口验证设计

2026-10-22，基线 `7d06ffa`。状态：实施中，尚无本轮运行通过结论。范围承接 [完整需求](../requirements.md) Phase 2 和 [物理后端设计](rust-physical-backend-design.md)，不以 QEMU 替代真实硬件 G1/G2。

## 模型与依据

使用本机 QEMU 6.2.0 的 `usb-net`。其 [固定描述符源码](https://github.com/qemu/qemu/blob/v6.2.0/hw/usb/dev-network.c) 中 descriptor index 0 对应 RNDIS 配置值 **2**，index 1 对应 CDC ECM 配置值 **1**，不能把索引当配置值。ECM 有控制接口 0（Interrupt IN 0x81）和数据接口 1；数据接口 alt 0 没有端点，alt 1 提供 Bulk IN/OUT 0x82/0x02。运行时须重新读取实际配置、alternate 和描述符，不仅依据模型名称断言。

Linux 6.8 的 [CDC 驱动](https://github.com/torvalds/linux/blob/v6.8/drivers/net/usb/cdc_ether.c) 在 probe 控制接口时会同步认领数据接口；同一驱动管理两个接口是必须验证的正常场景。[usbfs CONNECT](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/devio.c) 对已绑定驱动的接口返回 EBUSY。现有恢复循环使用一次性的全部接口快照，先 CONNECT 接口 0 后仍可能把接口 1 当成未绑定，再次 CONNECT 并误报隔离。

## 恢复修复契约

只修改恢复观察，不放宽所有权：

1. 每个实际取得所有权的接口准备恢复前，重新核对同一设备 generation、原配置和该接口当前驱动。
2. 当前驱动已等于原驱动则跳过；当前驱动是其他驱动，或原来无驱动而现在有驱动，仍拒绝更改并隔离。不得 detach 新出现的驱动。
3. 仅对原来有驱动且当前未绑定的自有接口尝试原有 CONNECT。若 CONNECT 返回错误，重新观察；只有同一代际、原配置和实际驱动已与原值一致才视为恢复收敛。不能将任意 EBUSY 当成功。
4. 原非零 alternate 恢复前也重新读取同代际/原配置的实际值；已匹配则不再 claim 接口，避免内核已恢复的驱动被重复操作。其余情况仍使用普通 claim，绝不为恢复而 detach 外部驱动。
5. 保留最终全部驱动、配置、alternate、grant 验证及既有截止；不改变 URB 回收、预算和 quarantined 状态规则。

确定性 fixture 应模拟恢复接口 0 时同步绑定接口 1，验证第二次 CONNECT 没有发生；同时覆盖真实并发收敛、不同外部驱动、代际/配置变化和未取得接口不被主动操作。QEMU 再证明实际 CDC 驱动行为。

配置切换后还必须在任何新的 detach/claim 前重新执行只读安全检查，不能把旧 active 配置的授权快照当成新配置的当前安全状态。通过 `physical-nusb` 的可选 workspace path 依赖复用现有 `rustusb-linux::SysfsDiscovery`，核对同代际、实际配置、原设备 ID、完整接口集合及全部 export blockers；新出现的 usb-storage/uas/ums-*、block 后代、usbfs/usbip-host 外部占用或观察不完整均拒绝 claim。配置 0 没有接口，仅验证配置和代际，不将未配置的空接口误报为设备支持。扫描在 blocking task 中完成，保持监督器的既有截止/隔离语义。该检查不声称撤销已经触发的内核配置变更、预知所有 inactive vendor quirk 或消除管理员并发 TOCTOU；失败仍须按原契约报告并清理/隔离。

## 隔离数据路径

仍用两个一次性来宾。现有第一条私有 socketpair 承载两端 e1000 管理网络 `192.0.2.0/24`。新增第二条私有 socketpair，两端都继承给 server QEMU，分别连接 `usb-net` backend 和 server 第二个 e1000；[QEMU socket backend](https://github.com/qemu/qemu/blob/v6.2.0/net/socket.c) 接受已连接的 stream fd。

server 第二网卡使用 `198.18.0.1/30`，client 导入后的 USB 网卡使用 `198.18.0.2/30`。两个地址位于不同来宾，实际应用数据路径为：

```text
client UDP → cdc_ether → VHCI → USB/IP 管理网
           → server 普通用户 USBFS 服务 → QEMU usb-net
           → 第二私有 socketpair → server e1000 → UDP peer
```

无 host TAP、host USB、宿主模块变更或公网 listener。root 权限仅在来宾准备节点/模块/固定网络和 helper；USBFS server 与数据 peer 使用普通 UID。原有九场景和 physical normal/hot runner 不改；新增独立 runner、guest init 和标准库 probe，复用现有 payload builder 时记录其完整依赖指纹。

## 本轮验收项

- 授权原配置为 ECM value 1，记录两个原接口及各自驱动/alternate；root0600 节点、普通 UID 无直接节点权限、helper FD 和服务零 capability 保持原契约。
- 独立 wire probe 验证实际描述符、配置值 1/2、IF1 alt0/alt1 的端点切换；排队请求不得跨错误的旧映射。无端点或不存在 alternate 返回明确失败，不产生假成功。
- 通过模型可实际产生的 Control/Interrupt STALL 检查负 errno 与零/实际长度；模型不支持的 CLEAR_FEATURE 不伪造成成功。ISO 继续明确不支持，不因此禁用可用的混合设备其他端点。
- 使用精确 nonce/payload 的 UDP 收发验证双向实际 Bulk 数据，至少两个导入/释放周期；分别执行 RustUSB 管理导入和 Linux 官方 `usbip attach` 直接导入，明确各自所有权和清理方式。
- 每次释放核对两个接口同代际恢复原驱动、原配置/alternate、pending/queued/inflight/预算归零；只有完整独立观察才能标记 available。原始 stdout/stderr、summary、命令、hash 和失败记录全部保留。
- 完成软件质量 gate 和独立审查，提交后冻结 clean commit，再默认 locked build 执行最终 QEMU；本轮只扩展该 QEMU ECM 模型证据，不声称任意 Composite、任意 alternate、ISO 或真实网卡兼容性。

设计不增加依赖、不另建 USB/IP engine，也不将完整 G2、认证/TLS 或长期会话恢复标为完成。
