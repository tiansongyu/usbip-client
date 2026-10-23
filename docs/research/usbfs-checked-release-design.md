# USBFS 显式释放结果与错误传播

2026-10-23，基线`54da615`。状态：实现已合入`4e81a11`，222次软件测试通过；最终clean `6e809a1` 十一项QEMU全部PASS，113case/22guest exit0；三轮候选FAIL及修订过程保留，见[最终报告](usbfs-checked-release.md)。承接[host-state最终验证](usbfs-host-state-races.md)，不将本轮扩展为原无驱动非零alternate的通用恢复承诺。

## 已核实的问题

固定nusb0.2.8的`LinuxInterface::release`将最后一个Arc取出后drop并返回Ok；实际RELEASEINTERFACE在Drop里执行，错误仅写debug。这符合其portable `Interface::release`已公开的best-effort契约，但RustUSB的`physical::worker::release_all`把该Ok当成显式释放成功，无法保留真实Linux errno。其bool汇总还会把可观察错误压成一般State错误。配置切换不能在释放失败后仍执行SET_CONFIGURATION。

Linux6.8 [devio.c](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/devio.c#L812)中的releaseintf仅在该FD的ifclaimed位存在时释放，否则返回EINVAL；driver_disconnect在外部解绑usbfs时会清除该位。因此真实外部解绑与显式release的竞态足以产生可复现的内核错误，不需要修改寄存器或伪造结果。

[driver.c](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/driver.c#L431)还说明：释放非零alternate会尝试复位alt0，复位失败仅置needs_altsetting0，外层release仍可能返回0。故checked ioctl成功**也不能证明原配置/alternate/driver已恢复**，最终独立sysfs/grant校验必须保留。普通USBFS的SETINTERFACE和interface-recipient CONTROL会通过checkintf取得claim；再次release又经过同一unbind。没有据此推断通用None+非零alternate已可恢复。

## 实现选择

- 保留原portable `Interface::release()`和Drop兼容行为；新增Linux-only safe `Interface::release_checked()`，物理adapter的全部显式释放改用它。
- checked同样由现有Blocking包装执行，仍只能在最后一个Arc时操作；存在其他Interface/Endpoint/transfer引用时返回Busy，不发送ioctl。
- 最后owner在调用真实RELEASE之前设置一次性attempted状态。成功或失败后，析构不再隐式重试同一release；丢弃future不意味着撤回已经启动的blocking任务。
- 保留真实errno及上下文。release失败时绝不CONNECT；若原句柄选择自动reattach，checked路径也报告CONNECT的真实错误。portable路径继续best effort，物理adapter仍使用no_reattach并仅在整体协调后恢复原driver。
- 不加入unsafe、指针操作、依赖或生产设备mock façade；仅以小型private生命周期状态和回调对一次性顺序做纯测试。
- worker释放多个接口时全部逐项await，不遇首错提前drop其余接口；保留第一个真实errno，供Special回复和清理错误使用。配置切换遇release错误跳过SET_CONFIGURATION，重观察/协调规则仍生效。原alternate恢复中的临时claim也必须checked释放。已独立确认同代际消失的Gone分支仍按既有语义处理，不把无法恢复的拔出设备写成原驱动恢复成功。
- nusb来源70文件、原许可、精确patch与SHA清单同步；公共Linux文档区分checked结果与独立恢复核验。

## 最小验证与失败保全

1. 纯测试：共享引用Busy且零操作；显式成功/失败之后析构不重发；ENODEV/EINVAL/EIO等errno保持；release失败不CONNECT；选用reattach时严格release成功后CONNECT一次、CONNECT错误可见；legacy行为维持。worker汇总继续处理后续接口且保持首个错误。
2. 在现有独立双QEMU harness增`--operation release`，分别UNLINK/EOF。普通UID1000 server通过真实helper FD发SET_CONFIGURATION2，门只停首个RELEASEINTERFACE入口；其他线程继续。保持原8秒门和3秒ARM后RPC截止。
3. 原有持门/取消witness或EOF接收与预算断言通过后，仅guest-root在授权QEMU IF0执行usbfs/unbind。独立记录同generation/node、IF0变None、IF1仍usbfs、配置1与slot/预算/FD保持；它是明确的外部所有权干预，不冒称产品自行提前清理。
4. 放门后记录实际syscall-exit=-22。修复后A按真实-22完成且UNLINK0；EOF不要求旧wire回复。trace须证明未执行SET_CONFIGURATION2；后续IF1释放确实完成，不因首错跳过。恢复到原CDC双driver/config1/alt0,1、末资源0，server/tracer正常退出。
5. 先在旧产品上运行新断言并保留真实FAIL，再应用产品修复；全部失败、准确源码、原始日志和派生解释均保留。若外部解绑前置或内核返回不同，只能FAIL并调查，不放宽errno或恢复断言。
6. 完整Cargo质量gate、vendor纯测试；修复后开发验证，再在同一clean提交串行冻结原六场景、新release两场景及ECM/HID/FTDI必要回归。范围内所有证据完成后再更新验收，不把原硬件G1/G2或完整Phase2标为完成。

## 首轮候选矩阵后的断言输出隔离

`4e81a11`同一clean提交顺序运行八项host-state、ECM、物理normal/hot。前十项PASS；hot原始server串口第547–548行显示`RUSTUSB_PHYSICAL`与`_REINSERT_STALE: PASS ...`之间插入内核hid-generic printk。host严格匹配拒绝整条缺失标记，整轮/sequence保留FAIL，不将字符串清洗后的拼接视为PASS，也不在原证据上重写结果。

只修改物理测试的`run-physical-qemu.py`和`guest-physical-init.sh`：QEMU保留第一串口ttyS0记录完整kernel console，另加第二ISA串口ttyS1作为guest程序stdout/stderr；来宾挂载devtmpfs后确认字符节点并重定向，打印独立channel beacon。host实时协调、断言和JSON解析读独立evidence log；Kernel panic/BUG/一致性检查继续读取原kernel serial及程序输出，两个文件均归档。保持全部I/O、热拔/新代际、权限、资源、超时断言；不抑制kernel日志，不延长等待或在parser中忽略插入内容。

该修订只隔离测试输出，不改变产品或协议。先保全首轮11项candidate原bytes/固定Git来源及严格FAIL；另行提交后在同一clean提交运行十一项最终矩阵。原软件输入未变时不因纯测试输出修改重复完整Cargo gate，但更新Python/BusyBox语法及实际新串口运行验证；若发现代码或新的问题则执行相应检查。


## v2启动阶段失败的诊断约束

clean `ab44a84` 的第二轮最终矩阵在第六项 alternate/EOF 的 `discovery_import` 以EAGAIN11失败（cases0），其余后五项未运行；前五项PASS保留。该阶段同时覆盖DEVLIST与IMPORT的多次I/O，现有日志无法判定准确失败调用；客户端尚未发送ARM/A，server串口的cdc_ether unregister也不证明IMPORT已成功。旧原始裁决与缺失trace不补造。

下一轮先仅增加独立probe各启动I/O的开始/终态/真实errno/耗时记录，细分list与import阶段；不修改WIRE2秒、RPC3秒、门8秒和任何USB断言。host发现guest FAIL后立即粘住FAIL裁决，最多保留30秒仅供原server ARM25秒超时进入现有trap并输出trace，再按原有终止/reap收尾。该grace不允许失败转成功、不延长任何通过期限、不重试请求。先做有诊断的单项开发运行，获得可用证据后才决定是否需要产品或harness修正。


诊断dev01（dirty ab44a84）实际PASS，IMPORT header等待1.519263924秒，DEVLIST header约12ms；原失败准确调用依旧未知。源码可确认tracer全syscall轮询在暂时无waitpid事件时固定sleep1ms，连续entry/exit停顿可能累计额外延迟。下一步仅将tracer parent在fork之后、SEIZE之前阻塞SIGCHLD，空闲时以sigtimedwait等待该信号、上限仍1ms；子进程不继承新mask。保留waitpid的真实status处理、256事件batch、公用单调时钟、控制轮询、8秒门和所有wire/RPC期限。SIGCHLD仅作唤醒，仍必须waitpid核状态，不伪造completion；EAGAIN或EINTR只是重新检查，其他错误FAIL。通过单项guest前后观察耗时与真实stop/trace，不把该优化归因为旧失败的已证根因。


## v3设备枚举前置等待

clean19ffa11的v3十一项全部结束，前10PASS，最后physical-hot在select-simulated-devices前置FAIL：原扫描快照确有两个设备，但FTDI interfaces仍为空、bNumInterfaces尚未可读、export_blockers非空；随后dmesg显示ftdi_sio注册完成。原loop仅按length2提前退出，与紧接着的完整性断言不同。只把loop成功条件改为原最终 `length == 2 and all(.[]; (.export_blockers | length) == 0)`，仍100次、0.05秒，最终断言保留；设备稳定前只继续等待，不放行不完整数据，不改变产品scanner或授权。先以原失败/已通过快照测试旧提前退出与新等待条件，再单项hot开发验收，之后distinct v4完整冻结。
