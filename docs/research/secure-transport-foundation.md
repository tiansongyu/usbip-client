# TLS 与匿名内核通道：基础模块验证

2026-10-23。TLS实现提交 `7bad8bf60fd9c011b849fd3942ff06c4b60fedd8`，首个内核实验版本 `f4774a63e189a0cfaec9d8515b719b0e399c87ea`。[设计](secure-transport-design.md)仍为proposed；当前没有将TLS接入USB/IP listener、CLI、特权FD接口或设备数据链路，完整Phase3/G3、G1/G2硬件均未通过。

## 已实现并实际运行的TLS基础库

新增非空`rustusb-transport`，接受调用者提供的DER凭据和不可变策略，在已有duplex I/O上运行真实双向TLS。显式ring provider、TLS1.3、双方完成握手后检查固定ALPN `rustusb-usbip/1`；标准证书链、时间、用途及server-name验证不能被pin替代。经验证的client叶DER SHA256才可映射到管理员principal及精确device allowlist。

物理/Native key使用现有`usb-`加64位小写hex指纹、各自独立命名空间；虚拟key仅两个固定profile。策略拒绝重复principal、共享pin和重复device；最多64principal、各2pin/16device。空策略拒绝全部。认证结果构造私有；它是权限快照，不能当作设备租约、物理可信证明或实时撤销机制。

每个server及其clone共享16连接配额，握手之前非等待取得permit，返回的stream持有到Drop；没有可提取inner的公开接口。握手默认5秒、禁client resumption、server session cache/tickets及0RTT。证书链/roots/key均有数量与字节上限，Debug/错误不包含凭据。应用仍须限制任务、读写截止，正确flush回复，并在DEVLIST和IMPORT claim之前使用同一设备predicate。

5秒是合作式异步截止，不能抢占同步密码或首次系统随机源调用。64KiB分别限制rustls待发送plaintext/TLS缓冲，不能相加后宣称总内存小于64KiB；证书、握手和底层I/O还有其他成本。调用者读取凭据文件及其权限检查、专用broker UID、策略加载/重启撤销和完整审计仍需后续实现。client握手成功验证server，设备获批须等待真实管理回复。

### 实际软件证据

首次`cargo test -p rustusb-transport --locked`18项全部通过。随后完整`bash scripts/check.sh` exit0：default108/all-features176/vendor20，共**304次测试执行、35个result、0失败/忽略**，包括fmt、两组Clippy/release与严格rustdoc。首次独立18项不重复加进304。104个选定Rust/Cargo/check输入前后SHA一致；该清单不是全部仓库文件。运行时HEAD为a7406d7上的未提交候选，具体输入哈希随后对应实现7bad8bf，不能称软件检查发生于clean7bad8bf。

18项包含真实内存duplex TLS、24KiB请求与12B回复的双向payload/flush、重复完整握手、缺/错ALPN、两侧未知CA、DNS、缺client证书、过期/错EKU、有效但未映射或新策略移除pin、16条已认证stream及clone共同持配额、取消/超时释放、输入长度/畸形/key mismatch及Debug脱敏。这些不是TCP、USB、外链抓包或硬件测试。

[软件原件](../evidence/secure-transport-foundation-20261023/manifest.json)保存完整命令、日志、输入哈希、实际features及源审查。独立28项源码审查无mustfix，不替代上述实际执行。Cargo精确锁rustls0.23.46/tokio-rustls0.26.6/rcgen0.14.10；148包清单包括未编入当前host的target/optional项，不能将其等同生产二进制依赖。未启用TLS1.2/aws-lc；rcgen只用于测试。来源/许可证见[第三方清单](../third-party.md)。

[补充源审查](../evidence/secure-transport-supplement-20261023/manifest.json)记录167项依赖/许可证/feature检查及Native导出配置诊断；它们不增加软件或USB测试数量。最初依赖审查使用移动HEAD作基线，随后固定a7406d7重核，初稿保留，未改变实际软件结果。

精确f4774a6的[CI39936932379](https://github.com/tiansongyu/usbip-client/actions/runs/39936932379) attempt1四原生平台均completed/success，Ubuntu22.04/24.04 × x86_64/aarch64各304次/35result、0失败忽略，另两项背压和四项信号场景通过。292个该CI采证冻结文件逐Gitblob、前后guard及实际执行ELF receipt通过；18项transport在default/allfeatures各执行一次。六份connector日志与官方原始ZIP逐字节一致，独立554项证据复核通过；部分per-case文件/ELF未上传，只能核日志中的receipt，不能声称保存了全部原件。

[42份CI发布原件](../evidence/publication-secure-transport-f4774a6-20261023.json)含Pages39936932305同提交check119/JS/deploy成功；后续README增链的本地检查计数另记。审查器初稿把6.17内核anon_pipe_write误限为pipe_write，保留初稿并按实际集合修正，非CI失败/重跑，也不影响独立QEMU FAIL。

## 首个QEMU实验：失败保留

clean f4774a6默认locked release构建，一台可销毁guest，宿主普通UID1000，QEMU6.2/KVM、guest Linux6.8.0-106-generic，`-nic none`。原有helper经UID1000成功导出合成HID/FTDI；此前UID1001 probe首guard拒绝已实际运行。随后测试脚本在导出后的snapshot阶段退出，root机制probe未启动，匿名pair、HID/serial I/O、显式shutdown及unexport均未执行。guest poweroff退出0不改变实验**FAIL**。

[首失败原件](../evidence/kernel-socketpair-first-failure-f4774a6-20261023/manifest.json)保存全部日志、build/ELF与source guards及冻结脚本。观察发现原keyboard为480Mbps、FTDI12Mbps，原probe把两者都假定为12Mbps也是后续必须修正的独立错误。Native导出前后配置和interface可随driver生命周期改变，不能与不可变hardware generation混在一次恒等比较；修复前先更新了设计。

固定v6.8的generic disconnect调用usb_set_configuration(-1)，移除interface并清空actconfig；bConfigurationValue与bNumInterfaces在未配置时均返回0字节。原shell的未匹配IF glob触发`[ -d ]`与set-e退出，符合日志中导出成功但snapshot未输出的边界；没有shell逐命令trace，不能冒称记录过实际失败指令。完整固定source引用、Git blob/SHA及原件不变检查在上述补充审查中；原源码预审未发现这两项假设，原首失败不会改判。

## 修订后的实际内核机制结果

修复提交 **clean `24a424b117ecdca32a93fcbfa8a09d867110e2a8`** 首次运行**PASS，50项host断言、一个guest退出0**。原f4774a6仍为FAIL。相同机型、默认locked release及原时限，109个选定Rust/Cargo/QEMU输入、HEAD/clean及ELF前后guard通过。该50是证据断言数，不是50个设备或USB case。

| 阶段 | 原设备真实观察 | 本地VHCI |
| --- | --- | --- |
| 导出前 | 配置1，IF0/alt0，usbhid/ftdi_sio | 全部NULL |
| helper导出后 / pair prepared | 原硬件代际保持，配置null、接口空，usbip-host AVAILABLE | 全部NULL |
| attached / 实际I/O | 配置1、完整IF/class/subclass/protocol，原IF driver:null，stub USED | HID480Mbps/kernel3、FTDI12Mbps/kernel2；新3-1/3-2，usbhid/ftdi_sio |
| 显式shutdown后 | 同原硬件代际、配置1及完整IF0/alt0，stub AVAILABLE | 全部NULL，两个旧本地代际已消失 |
| UID1000 helper unexport后 | 原device与接口driver、配置、完整原快照恢复 | 全部NULL |

两匿名UnixStream::pair共四端点，probe实际/proc FD为5/6/9/10，inode376/377/381/382；各自一次完整stub/VHCI sysfs write，FD由root probe真实持有，未写stub -1。实际KEY1 down/up与FTDI各方向35字节通过；host原始发送/接收bin及guest evdev/RX/TX hex保存。一个按键周期、每方向35字节，不能称多轮稳定性或性能验收。

四次SHUT_RDWR成功后，在四个用户态端点仍持有时确认NULL、旧local不存在及stub AVAILABLE，再真实wait0和普通helper unexport。未建立TCP连接或listener，guest只有lo且QEMU没有NIC；sessions正确将两条内核导入显示为unmanaged，没有伪造官方usbip端点记录。这是正常显式停止机制，不证明只Drop/进程崩溃、热拔旧attr代际或产品自动回收。

[最终原件](../evidence/kernel-socketpair-final-24a424b-20261023/manifest.json)保存完整日志、生成init、源码与构建/执行哈希链；未保存临时ELF本体。default CLI/helper SHA分别538b2ef1…8554d / cf7273af…e7827，与首失败同feature产物相同；probe为797bb889…f746d。它们不是此前all-features USBFS QEMU的ELF；本轮新TLS库未被CLI/helper链接，不能据workspace build含该库宣称USB链已加密。

另一agent不调用runner verifier的独立raw复核40个分组谓词通过：109选定源逐Gitblob/当前字节、六冻结harness文件、13原件、现存产品ELF/内核/11模块，以及真实FD、sysfs写、数据和完整基线。已删除的probe ELF/initramfs只核构建→安装→实际执行digest链，没有重建来替代旧运行。console中的两次speed not set及三次endpoint0 stall/-104原样保留；本结果不表示内核日志零warning或无USB错误，不外推长期稳定性。

复现需Native QEMU先决条件及干净源码，使用新的证据目录：

```bash
python3 scripts/qemu/run-kernel-socketpair-qemu.py \
  --kernel 6.8.0-106-generic --timeout 180 \
  --evidence /tmp/rustusb-kernel-pair-new-run
```

首失败后的[修复准备](../evidence/kernel-socketpair-export-fix-20261023/manifest.json)含39项静态/jq/纯合成verifier检查、一个实际Rust单测遍历13配置边界、严格编译和普通宿主UID首guard拒绝；20项独立delta源审查无mustfix。这些不增加QEMU或304软件数量。产品Cargo/crates/vendor/check与f4774a6逐字节相同，未为仅测试harness的修复重跑全软件。后续仍须实现真实两端broker/FD所有权和加密USB/IP管理帧及全URB外链验收，完整G3尚未通过。

24a424b的[较新发布状态](../evidence/publication-kernel-socketpair-24a424b-20261023.json)另核software39938851575四原生job及Pages39938851717均completed/success；未重新解析这次各Rust测试计数，不将旧计数冒称新日志复核。README和展示站只新增基础库进度及未接USB链的说明，原268/七QEMU/14guest仍明确属于2ab9976的生命周期验证。六线上HTTPS资源逐24a424b字节一致；CUA实际读到304新段落，1280和375宽度均无横向溢出，console无warn/error。响应式override清除后实际默认618宽；本次未重新测试键盘/复制/无脚本行为或截图，CSS/JS/资产未改。全局readme-site四文件仍等于原已提交快照，Codex发现symlink有效。
