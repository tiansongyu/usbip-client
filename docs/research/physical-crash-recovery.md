# USBFS SIGKILL 与首次重启：负向验收

2026-10-23。**恢复验收 FAIL，证据收集完整。** 服务被强制结束后，客户端自主断开，但服务端原驱动没有恢复。首次重启把当时的空驱动状态作为自己的基线，不能证明旧会话已恢复。跨进程恢复仍未实现；方案边界见 [draft 设计](physical-crash-recovery-design.md)。

## 可复现输入

- 干净提交 `1342a6a64cb6045052e49b701b773c4e00811fa8`，运行前后 140 份 Cargo/crates/vendor/scripts/CI 文件与 Git 一致，工作区均干净。
- 仅新增 `run-crash-qemu.py` 和 `guest-crash-init.sh`；产品代码和原 SIGTERM harness 与 `e0e6118` 完全相同。本轮未重复已通过的完整软件/USB 矩阵，也未将旧结果重新归属于此提交。
- 默认执行 `cargo build --workspace --release --locked --all-features`，实际退出 0。CLI SHA256 `adedf4b1c90d5f1a95033d49fd72caa425860d192855d827d5a88eb0e654699e`，helper `837aec7045ab08d935e8d887b686db8e160a5c849fd236c653710c0320dca46b`；复制入 guest 与运行后的二进制校验均通过。
- 两个 x86_64 QEMU guest，Ubuntu 内核 `6.8.0-106-generic`，KVM，私有 socketpair。宿主 UID1000；宿主不操作 USB、内核模块或网络配置。程序证据在 ttyS1，内核在 ttyS0。

复现命令（证据目录必须尚不存在）：

```bash
python3 scripts/qemu/run-crash-qemu.py \
  --evidence /tmp/rustusb-crash-observation
```

上述基线实现应保留恢复 FAIL 和实际退出 1；不能把预期失败转成恢复 PASS。两台 guest 正常关机的退出 0 仅表明测试收集结束。

## 实际结果

| 时间点 | 进程、传输及设备观察 |
| --- | --- |
| 初始基线 | HID `usbhid`、FTDI `ftdi_sio`，配置 1、alternate 0；节点 root0600，同代际记录保留 |
| SIGKILL 前 | 普通 UID1000 / capabilities 0，经 helper 授权；真实 KEY1 down/up、FTDI 双向各 35 字节；两个 lease，各 1 个 USBFS inflight，HID pending1、FTDI pending2/queued1，预算 2832 字节 |
| 强制退出 | 对已核验的自有 PID303/starttime293 发送一次 SIGKILL，stdin 保持打开；实际 wait137，进程及其 FD 表消失；没有旧进程 stopped 事件 |
| 服务端退出后 | 完整 20.06 秒观察后，原代际、节点身份/root0600、配置1/alternate0仍匹配；两个 driver 均为 null，原驱动未恢复 |
| 客户端自主断连观察 | 0.19 秒内观察到 sessions 空、VHCI 全部 NULL、旧两设备消失；先保存这些事实，再显式清理本次 helper 缓存。客户端 guest 此时仍在运行；该时间是本次观察，不是性能基准 |
| 首次重启 | 同一 helper、相同显式 device-id，新普通进程 PID12905/starttime2517，确实取得对应 FD；不发新 IMPORT。真实 ready 中 original/current driver 均为 null、`cleanup_verified=true`，本次进程所有计数为 0 |
| 新进程合作停止 | 控制 shutdown 有真实 ACK，实际 wait0、唯一最后 stopped；新进程资源为 0，但外部原驱动仍为 null，原始基线依旧未恢复 |

运行共 55 项 host 检查：13 项 fixture、35 项事实检查均通过，7 项恢复检查中 3 项通过、4 项失败。失败项为服务端汇总裁决、SIGKILL 后原 sysfs 恢复、重启基线与最初基线匹配、首次重启结束后的原 sysfs 恢复。runner exit1；两个 guest exit0 不覆盖失败。

`cleanup_verified` 是当前进程基线的验证结果。此次重启的零计数、available 和 stopped 不能填补旧进程缺失的终态、URB reap 数或历史恢复证据。20.06 秒未恢复也不证明永久占用；本轮没有测量所有潜在 FD 副本的生命周期。

## 证据与审查

[运行和归档索引](../evidence/physical-crash-negative-1342a6a-20261023.json)包含前置冻结、实际命令/退出码、构建日志、双 guest 的原始两个串口、七份运行时脚本/探针、summary、静态审查和身份检查。所有运行原件逐字节复制，归档发生在运行结束后，不把随后文档修改记作运行期 dirty。

本轮保存了独立 FTDI host sink `ftdi-host-tx.bin`，实际 35 字节逐字匹配固定合成 TX；客户端原始证据含 RX hex 和 evdev input hex。只有合成测试数据进入此测试证据，未改变产品日志策略。旧 SIGTERM 测试未保存的 host payload 不因本轮文件而补齐。

本轮未保存 USB/IP 逐帧抓包；IMPORT/传输事实依据客户端内核 receipt、真实合成 I/O、worker 计数、FD/sysfs 观察和冻结脚本断言，不冒称已有 wire capture。

脚本实现前的设计有两份独立只读审查；两脚本有 50 项静态检查和另一审查者的源码复核。预审发现 shell 条件函数抑制 `set -e`，改为每项身份/FD 校验显式失败返回，15 项隔离提取检查通过。首次名为 `negative-before` 的提取已读到修复版，结果为 NOT_REPRODUCED；故障变体是明确派生的示例，不能当成旧版本原件。这些检查不是实际 USB 验收。

运行后的另一审查者独立解析原始日志和固定源码，未调用 runner 的 `verify`；[74 项复核](../evidence/physical-crash-negative-1342a6a-20261023/reviews/rustusb-crash-negative-1342a6a-independent-review-20261023.json)通过，保留原运行 FAIL。复核包括 140 份 Git 输入、七份脚本、分层来源指纹、实际合成传输及两个进程基线，复核脚本一并归档；复核通过不表示设备恢复通过。

## 后续实现边界

下一产品变更应保护未决授权的可信原基线，同时提供可验证的正常结束和管理员协调路径。不能只保留同一 FD 的 dup，不能只凭进程死亡删除记录，也不能用拒绝所有未绑定接口或删除多配置能力来回避恢复问题。

本轮只完成问题复现、测试工具、设计与证据。持久租约、跨进程监督、自动恢复、异常配置变更、helper 自身崩溃及重启仍需实现和验证；真实硬件 G1/G2、完整 Phase2 和后续阶段状态不变。
