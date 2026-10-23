# 原驱动恢复：旧记录、许可撤销与外部占用验收

2026-10-23。实际版本 **clean `4565f7aaddb5a9377c15261a288ca9c3a57eebe5`**。三项新双QEMU场景首次全部PASS，6个guest正常退出；318/293/313项host检查无失败。这些数量含逐轮询/来源检查，不是USB测试case或设备类型数量。

本轮增加[原驱动恢复](physical-driver-autorestore.md)的授权边界证据，产品代码仍来源于e34e309。完整Phase2、G1/G2真实设备及通用恢复继续未完成。[正式设计](physical-driver-autorestore-negative-design.md)先于实现，旧两个正向及原journal harness仍保留其实际版本。旧1342a6a恢复FAIL、43d6d43脚本FAIL等历史结果不因本轮通过改判。

## 三项实际裁决

| 场景 | 实际观察 | 合法收尾 |
| --- | --- | --- |
| `legacy-schema1` | 真实旧helper生成磁盘1；新版明确opt-in后至少20秒原记录/driver:null不变、无恢复事件，新grant被未决guard拒绝 | 管理员恢复原driver；再重启后的同一旧记录仍至少5秒不自动退休；root只读reconcile后，新opt-in grant才产生磁盘2并完成第三轮真实I/O |
| `policy-revoked` | 新版原有磁盘2许可保持，但停止helper后撤销当前恢复名单、重启加载，至少20秒不恢复、不退休、无恢复事件，新grant被拒绝 | 重新启用名单并明确重启helper后，产品自主恢复原基线及退休，再完成第三轮真实I/O |
| `external-claim` | helper停机且原owner已reap后，独立C先成功claim两个接口；当前helper各lease仅attempt→failed，至少20秒保持usbfs和原记录 | C只释放/关闭自己的FD并真实wait0，同helper至少5秒不重试；明确重启helper后产品恢复/退休，第三轮真实I/O通过 |

L的尾部是**人工恢复与只读协调**，不能计为自动恢复成功；新策略不能追授旧记录权限。P/C的尾部才由产品自动恢复，前提是明确的新helper实例。P验证的是启动时加载策略的撤销边界，不是运行中编辑文件立即生效或FD撤销。

三个场景均先完成第一轮正常真实I/O、一次无IMPORT重开/合作关闭、第二轮真实I/O及active pending。先合作停止helper并实际wait0，再由普通UID1000向自己的服务发送一次KILL、真实wait137并reap。三次被杀epoch末资源均保持null/不可观察，不借新epoch的0证明旧进程清理。

每场景三轮HID key-down/up和FTDI每方向3×35=105字节，合计九轮、每方向315字节；host TX原bin及client RX/evdev原hex保存。三次新grant拒绝均实际exit1且有精确未决guard原因；原记录、原基线和lease不被新授权覆盖。最终可复用性由第三轮传输和正常退休证明。

## 真实旧producer与构建来源

L使用实际 `933e720531fd96b7a23616ed687d59b816e0ce09` helper，当前普通CLI提供服务；只把helper称为旧producer，不声称整套旧前端升级测试。独立detached worktree及独立target目录执行：

```bash
cargo build -p rustusb-daemon --all-features --release --locked \
  --target-dir /tmp/rustusb-legacy-producer-build-933e720-20261023/target
```

109个旧输入逐Git933e720 blob核对，实际build exit0，source/head/clean前后guard通过。旧ELF **3769624B**、SHA256 `2ce8940889b5831f92bebaa0218b5e182d3b54c8e5f15c22b1b43a495fe0819b`，guest真实执行路径 `/usr/libexec/rustusb-helper-legacy`，从proc exe再次核SHA。旧policy没有新增字段，磁盘1由其真实授权生成；没有手造journal、删recovery字段或用新版default-off冒充。

当前三个runner各自默认locked all-features构建，使用同CLI `375301a7cf7b79f9d7a8ec24a43232ca98df0063e395c91b8cdf9a1e9f724e3d`、helper `1428d92b1fc5ff867604b5ce950e35a914e3b70d2659ce80d9432a61fbc6a828`，与2ab9976实际产品产物相同。三次运行分别核155个当前冻结输入和109个旧输入、当前/旧HEAD、clean状态及ELF前后，全部通过；运行后才写本轮交付文档。

宿主普通UID1000、Ubuntu22.04.5 x86_64、Linux6.8.0-106-generic，guest同内核、合成HID/FTDI与独立socketpair网络，不操作宿主USB。新的C程序仅在external-claim场景打包，旧成功正向无此actor。

## 外部C实际证据

C入口在USB节点读取/open之前要求guest root及两固定启动标记，随后核固定root0600字符节点、VID/PID、bus/devnum、完整配置1/IF0/alt0/null及前后receipt。只独立open、plain CLAIM、RELEASE和close；没有CONNECT、DISCONNECT、reset、配置/alternate切换或sysfs bind。

实际C PID1894/start768，两个CLAIM rc0/errno0先于helper epoch2 PID2035/start786；四个真实ioctl是两CLAIM和两RELEASE，全部rc0/errno0。两个close后F_GETFD均-1/EBADF9，C实际wait0。root0700控制目录、root0600 FIFO、proc exe SHA与两个FD receipt均保存；父shell不常驻打开控制FIFO为冻结源码约束，不能当作完整系统FD审计。

两条记录各一次attempt→failed，理由为设备unsafe/already exported/claimed/incompletely described。实际冻结源码通常在 `observe_current_device → ensure_eligible` 即拒绝，早于scope或CONNECT；不强求一次EBUSY ioctl。无逐产品CONNECT syscall/errno跟踪，不能用日志宣称跟踪过内核调用。外部占用保持及后续复用由独立状态和真实数据支持。

所有tick单位为厘秒。实际窗口：L拒绝771–2783，人工恢复后仍pending 2849–3380；P拒绝778–2797，明确重启后的恢复观察2857–2869；C拒绝797–2804，同helper释放后2854–3383，重启后的恢复观察3408–3420。它们是保存的测试观察窗口，不是SIGKILL起SLA或性能基准。

## 软件、静态与复现

本轮完整 `bash scripts/check.sh`实际exit0：default90/all-features158/vendor20，共268次执行、31个result、0失败/忽略；fmt、Clippy、release和严格rustdoc通过，111产品/vendor/check输入前后不变。[准备证据](../evidence/physical-autorestore-negative-preparation-20261023/manifest.json)还包括旧helper构建、来源预审及前一交付744d9be的软件/Pages已完成状态；没有重解析那份历史CI的每个测试计数。

[静态证据](../evidence/physical-autorestore-negative-static-20261023/manifest.json)包括严格C编译、普通宿主UID首guard拒绝、三种生成init/sh-n、80个实际jq编译程序等87项检查。三个verifier构造fixtures为180/170/187项，各有伪造wait0拒绝；这些是parser证据，不是guest结果。源码预审在首次运行前修复C日志创建竞态，并补授权ready→pending不变及实际C身份/FIFO采证。旧两个正向及184裁决没有被调用。

[最终运行索引](../evidence/physical-autorestore-negative-final-4565f7a-20261023/manifest.json)保存全部实际原件、旧109份源文件、生成init及编排。例：

```bash
python3 scripts/qemu/run-autorestore-negative-qemu.py --scenario legacy-schema1 \
  --legacy-helper /tmp/rustusb-legacy-producer-build-933e720-20261023/target/release/rustusb-helper \
  --legacy-manifest /tmp/rustusb-legacy-producer-build-933e720-20261023/manifest.json \
  --evidence /tmp/rustusb-autorestore-negative-4565f7a-20261023/legacy-schema1
python3 scripts/qemu/run-autorestore-negative-qemu.py --scenario policy-revoked \
  --evidence /tmp/rustusb-autorestore-negative-4565f7a-20261023/policy-revoked
python3 scripts/qemu/run-autorestore-negative-qemu.py --scenario external-claim \
  --evidence /tmp/rustusb-autorestore-negative-4565f7a-20261023/external-claim
```

实际ELF以构建→guest→运行后哈希链记账，索引没有保存ELF本体。重建旧helper可因路径/环境得到新哈希，runner要求manifest与实际ELF匹配、固定源109项及构建成功，不硬编码本机哈希为跨环境唯一产物。

独立raw复核由两名agent分别完成，均未调用runner/verifier：L 615/615项通过，P/C 516/516项通过。这些是来源与原件断言，不是更多USB case。L逐155当前/109旧Gitblob、实际旧ELF、原disk1字节、所有helper/owner、窗口及三轮实际数据核验；P/C逐policy、immutable disk2、C实际ioctl/proc/receipt、释放后同实例不重试及最终I/O核验。P/C审查器初稿把构建flag顺序硬编码错而误判两项，初版报告保留，按实际runner顺序修正；QEMU及原件未修改。当前ELF以记录链和guest helper procSHA核验，旧ELF另实际重hash；没有逐人工bind或产品CONNECT syscall跟踪。

精确4565f7a的[软件CI39928383971](https://github.com/tiansongyu/usbip-client/actions/runs/39928383971)与Pages39928383815均attempt1 completed/success。四原生平台Ubuntu22.04/24.04 × x86_64/aarch64，每格90+158+20=268次测试执行、31 result、0失败/忽略，以及两背压/四信号通过；该CI采证187输入逐Gitblob与前后guard、实际uname/rust host和proc-exe inode/SHA核验通过。219项独立CI审查不是产品case数；CI未上传每case原始protocol/stdout文件，其证据为完整日志summary/hash。Pages实际115检查/deploy成功，本次交付后本地README链接检查为119项，按各自版本计数。

[CI发布原件](../evidence/publication-autorestore-negative-4565f7a-20261023.json)保存47原件及原manifest。审查器初稿误把隐藏`.nojekyll`列入上传artifact，按实际tar排除隐藏文件的日志规则修正，初稿保留；不是CI失败或重跑。实际artifact12489134583含6份非隐藏website文件，站点源在本轮测试提交没有变更。

## 仍未完成

本轮只覆盖固定模型的旧producer迁移、启动时恢复许可撤销和持续外部接口claim。不同旧版本、更多策略变化/错误组合、非claim的其他FD或设备级EP0活动、恶意/并发外部root、跨配置/alternate、旧boot或热插拔代际、内核硬阻塞、断电耐久和真实硬件仍需相应设计/验收。启动doctor/Status仍会先调用；纯观察primary仅保证早于pending API与新grant。

真实G1/G2、Ubuntu24.04/aarch64 USB内核路径、ISO执行、无原driver非零alternate通用恢复，以及完整Phase2/安全/界面/兼容性目标继续保留。下一切片的优先级只读复核建议推进实际USB/IP数据通道认证与加密设计，同时保持这些gate未通过；该建议不等于Phase3实现或验收。
