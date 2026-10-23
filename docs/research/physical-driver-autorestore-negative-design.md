# 原驱动自动恢复的三项边界验收设计

2026-10-23，起点clean `744d9be`。前一goal turn有实际进展：受限产品恢复已实现，clean2ab9976两个正向、五个原QEMU回归及268次软件/四平台CI通过，README、展示站和全局技能已交付。本轮继续完整原始目标，补[实现报告](physical-driver-autorestore.md)明确未完成的三项组合；不是完整Phase2或硬件验收。

测试仅使用隔离QEMU内的合成HID/FTDI。先保持现有产品、已通过的正向和旧journal harness；新增负向runner/guest片段及仅在外部占用场景打包的C测试工具。复用网络、正常第一轮/第二轮真实I/O、采证基础，使用专属负向verifier，不调用旧正向或184裁决。

## L：真实旧记录不得追授

- 在独立detached worktree固定 `933e720531fd96b7a23616ed687d59b816e0ce09`，以独立target目录执行 `cargo build -p rustusb-daemon --all-features --release --locked`，不覆盖当前产物。记录完整输入、清洁状态、编译器、命令、输出ELF和构建前后哈希。新helper仍由当前runner默认locked构建。
- guest同时含实际旧helper及当前helper，明确区分固定路径、实际proc exe/SHA与启动/退出epoch。旧helper只接受旧policy形状，因此开始时不写新增恢复字段。由旧helper真实授权当前普通服务完成I/O，生成原schema1记录；不得手造journal或删除recovery字段冒充旧producer。
- 活动第二轮I/O后先合作停止并wait旧helper，再KILL/reap普通服务，保留旧记录/原基线。启动当前helper并明确开启新恢复策略，至少20秒观察同代际原driver仍null、旧磁盘1及IPC1记录不变、无任何自动恢复事件，新授权被未决guard拒绝。观察不能依赖管理API触发恢复。
- 旧record永远manual：不能通过再加策略或重启使其自动恢复。收尾由guest管理员明确恢复原driver，然后root只读reconcile退休旧manual记录，再取得新opt-in grant、真实磁盘2记录及第三轮I/O/正常退休。尾部人工协调不得算作自动恢复成功。

## P：撤销当前许可

由当前helper在opt-in下真实生成磁盘2许可及第二轮I/O。先停止并wait helper，再KILL/reap普通服务；helper停止时从root策略移除恢复ID（物理导出名单与UID保持），启动当前helper。至少20秒纯磁盘/sysfs观察原驱动未恢复、原immutable许可仍在记录中、没有自动恢复事件、新grant被拒绝。编辑policy不撤销已交付FD，此场景不声称FD撤销。

保持记录原样，重新启用恢复策略并明确重启helper，才允许产品自主恢复、完整核验和退休，然后第三轮真实I/O/正常退休。可以补一次只改文件不重启的短观察，以核既有startup-policy语义；不得把编辑文件当即时生效。

## C：外部OFD占用

当前helper先真实授权及第二轮I/O，停止并wait helper，再KILL/reap普通服务。guest-only C对两固定root0600节点各独立open，仅plain CLAIM interface0并等待控制；不用A副本，不调用CONNECT、DISCONNECT、配置/alternate切换、reset或sysfs bind。工具入口要求guest root和固定启动标记，未满足时必须在任何USB open之前拒绝。

确认C实际claim成功、sysfs为usbfs后，才启动有有效恢复许可的当前helper。每个旧lease应恰有一次attempt→failed，原记录/完整代际不变、没有retired或新grant。当前产品通常在 `observe_current_device → ensure_eligible` 即以unsafe/claimed拒绝，早于scope或CONNECT；按实际错误上下文断言，不强造EBUSY ioctl预期。

C只释放自己的两接口并关闭FD，真实wait0后观察至少5秒：同helper实例不得自动重试，原driver仍null/记录未决。之后明确重启helper，产品才可自主恢复和退休，第三轮真实I/O验证可复用。C不能混入原两个成功正向payload。

## 共同来源、同步与裁决

每场景保留三轮KEY down/up和FTDI每方向3×35字节原件、全部helper/service/C真实wait、policy前后原件、boot/owner tuple、完整配置/IF/alt/driver/receipt、原disk原字节及权限。运行从干净commit启动，串行构建与QEMU，当前源码/旧源码/ELF/脚本分别冻结，source/head/clean/binary guard核对；旧ELF哈希不要求等于过去不同构建路径的产物。

C取得及策略切换的生效按停机/重启边界协调；C释放必须在已观察失败的同一运行helper下完成，随后才显式重启。P中只改文件的可选观察单独标记，不能当作策略已生效；新grant拒绝须检查实际guard原因，不接受任意非零。负向观察先用只读disk/sysfs保存事实，随后API检查与拒绝验证另记。成功恢复同样先纯观察，再pending API/新grant。20秒窗口记录真实起点，是测试观察而非kill起SLA或性能基准。

测试失败保留原始资料，先定位源因再修复，从新干净提交重跑；不以guest退出0或缺少某日志单独判PASS。静态检查覆盖生成init、严格C编译、实际jq表达式、构造verifier正反数据，并明确这些不是实际USB结果。产品代码若未改，既有268次软件证据仍只归原版本；本轮执行项目要求的软件检查后记录真实输入，不把新增harness当成新产品能力。

实施与首次实际裁决见[运行报告](physical-driver-autorestore-negatives.md)：clean4565f7a三项PASS/6guest0；本设计保留运行前契约。真实设备G1/G2、跨配置/alternate、任意FD撤销、内核硬阻塞及完整异常矩阵仍未完成。
