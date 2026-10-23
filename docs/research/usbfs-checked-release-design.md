# USBFS 显式释放结果与错误传播

2026-10-23，基线`54da615`。状态：待实现与验证。承接[host-state最终验证](usbfs-host-state-races.md)，不将本轮扩展为原无驱动非零alternate的通用恢复承诺。

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
