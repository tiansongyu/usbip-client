# USBFS 独立恢复句柄：QEMU 机制验证

2026-10-23。**干净 `da764d0b7fb156e76844918698e8edc14d7e9b79` 的机制实验通过；产品自动崩溃恢复尚未实现。** 本轮只增加测试工具，原 journal harness、产品和依赖均未修改。设计与授权边界见[异常恢复设计](physical-crash-recovery-design.md)，原持久记录验收见[journal 报告](physical-journal-recovery.md)。

## 结果与实际范围

两个一次性 QEMU guest 使用 Linux `6.8.0-106-generic`，宿主普通 UID1000、x86_64、KVM、QEMU 6.2.0。只有 guest 操作合成 HID `0627:0001` 和 FTDI `0403:6001`；私有 socketpair 连接两端，不访问宿主 USB、不在宿主加载模块或监听 USB/IP 网络端口。

root 在运行前冻结 149 个产品、脚本与 CI 输入，运行中没有源码修改或并行本地构建。完整 runner 约 41 秒，实际退出 0，两 guest 退出 0；源码、HEAD、工作树和二进制前后检查通过。host 的 **237 项检查全部通过：184 项原 journal 断言 + 53 项新机制断言**。这些是集成检查次数，不是新增 Rust 单元测试数。

| 步骤 | 实际观察 |
| --- | --- |
| 独立 B | guest root probe 在第二 epoch IMPORT 前独立 open 两个节点；全过程保持同一个 B、同 node/sysfs receipt、配置1、接口0、alternate0 |
| A 副本 D | `pidfd_getfd` 取得服务 A 的副本；两设备实际 `kcmp(A,D)=0`，`kcmp(A,B)` 与 `kcmp(B,D)` 非0，不能用相同 inode 冒充相同 OFD |
| SIGKILL | 普通服务 PID1182/start562 收到一次 SIGKILL，真实 wait137；旧 epoch 没有 stopped，末资源保持未知 |
| D 延长占用 | D 仍打开时，两设备仍由 usbfs 占用；B 的 CLAIM 与 CONNECT 共4次均返回 `-1/EBUSY(16)` |
| 关闭 D | 两次显式 close 后 `F_GETFD` 均 EBADF；B 的两次 CLAIM 和两次 RELEASE 均 `0/errno0`，释放后独立 sysfs 为 null driver |
| 独立 C 对照 | 同一 probe 进程再 open 两个独立 C，kcmp 与 B 非0；C claim 成功后，B 的 CLAIM/CONNECT 共4次 EBUSY，C 占用保持；仅由 C 自行 release/close |
| 原重启保护 | 返回原20秒观察、跨 helper 重启、新服务拒绝和未恢复时 root reconcile 拒绝；原记录不被新 original 覆盖 |
| B 恢复 | 两次 CONNECT 实际返回 **1/errno0**；随后完整 sysfs 核原 usbhid/ftdi_sio、配置、接口、alternate、节点和代际完全恢复 |
| 关闭并复用 | B 显式关闭，probe PID1080/start550 实际 wait0，才执行原 root 只读 reconcile；随后第三轮新 grant 真实 I/O、正常关闭和记录退休通过 |

C 是同一 probe 内的另一个 open file description，**不是另一个进程**。总共18次 ioctl，8次预期 EBUSY、8次成功 claim/release、2次成功 CONNECT。probe 不执行 DISCONNECT、DISCONNECT_CLAIM、reset、SET_CONFIGURATION、SET_INTERFACE 或 sysfs bind/unbind，也不写 journal。

客户端三轮 KEY1/2/3 的 evdev 事件和每轮 FTDI 35字节双向内容均保留；每方向共105字节，宿主 FTDI 原始 TX sink 精确匹配。第二轮观察客户端自主断连在显式 detach 之前，第三轮证明恢复后能重新传输，不能只凭 ready 判定复用成功。

## 来源、失败与复现

原首轮 clean `43d6d43` **FAIL** 保持不变：probe 只完成 B open/ready，首份快照因 jq 保留变量 `$label` 编译失败，尚未启动 killed epoch；KILL、OFD对照和恢复全部未执行。后续 client wait 超时是收尾错误，两 guest 退出0不覆盖失败。原第一轮 host TX35字节已保存，尚未 dump 的 client evdev/RX 原件不推造。见[首失败原件及独立复核](../evidence/physical-guardian-first-failure-43d6d43-20261023/manifest.json)。

修复仅将 jq 变量改为 `mark`，输出字段仍为 `label`，没有放宽 USB 断言或时限。全部24处/19种 jq filter 的60次实际编译及正反 fixture，加附加静态检查共64项通过。更早的严格 C 编译、13个数字解析用例、宿主普通 UID 拒绝和独立源码审查另见[静态证据](../evidence/physical-guardian-static-20261023/manifest.json)；shell 语法检查不等于验证内嵌 jq 表达式。

最终[原始证据与来源索引](../evidence/physical-guardian-final-da764d0-20261023/manifest.json)包括双串口、QEMU stderr、FTDI fixture bytes、构建日志、149输入冻结、真实退出状态、全部源脚本和生成的 init。新 runner 通过6个唯一锚组合旧 guest，旧脚本 SHA 不符立即拒绝；失败时也保留即时记录。研究依据固定 Linux v6.8，Ubuntu 实际构建的行为只按本次原件裁决。

```bash
python3 scripts/qemu/run-guardian-qemu.py \
  --kernel 6.8.0-106-generic \
  --evidence /tmp/rustusb-guardian-new-run
```

输出目录必须不存在；准备环境沿用 [QEMU 测试说明](../testing.md)，另需 C 编译器和 Linux UAPI headers，guest 内核支持 pidfd_getfd/KCMP。权限或 syscall 不可用视为失败，不跳过后标通过。默认构建是 `cargo build --workspace --release --locked --all-features`。

本轮 CLI SHA256 `6c56ade7fd7f93fe8e331aa16197c3dee24d95d0eadc2be9a7e19c73dc70c0b6`，helper `14f33dade1371d6227fa9dc74b5b76bae7d8dfa89b31c34dd133206f75eedf3f`，均与已验收 journal 矩阵一致。109个跟踪产品输入与 `933e720` 相同；既有250次软件检查及其四平台结果仍归属原报告，本次不重新归属旧运行。

## 不能据此声称的能力

- guest root 测试 probe 根据固定流程执行恢复；产品 helper 没有新增自动恢复功能，旧 schema1 仍为只读协调契约。
- B claim 成功仅证明对应接口当时可取得，不是任意 FD/EP0/内核活动全部结束或能力被撤销的证明。
- CONNECT 由内核选择匹配驱动，不能指定原 driver；本次固定加载模块并完整核验不保证任意生产设备的驱动选择。
- C 占用拒绝不消除 release 与 CONNECT 之间外部管理员操作的竞态；没有验证恶意授权服务、任意转交 FD 或 root 对抗。
- 未执行配置/alternate 变化、ECM多接口、guardian自身崩溃、重插代际、内核内部阻塞、存储恢复或真实硬件 G1/G2。本轮不完成整个 Phase2 或后续安全/UI阶段。

后续优先细化明确 opt-in 的同配置、同 alternate 原驱动恢复契约和旧记录迁移；跨配置恢复再引入可信变更事务。原合作停止、多配置和热拔能力保持，不用缩减已有功能回避恢复问题。
