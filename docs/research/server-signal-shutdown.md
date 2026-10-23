# Rust服务Unix信号合作停止验证

2026-10-23。产品修复d39517b，冻结验证提交934358fa457b71c5e3436cc544ca4031b656f147。`virtual-server`与`physical-server`的SIGINT/SIGTERM共用合作清理入口；本机软件、本地四项信号及四项QEMU已通过，四平台CI39899971271已通过，每格222次测试执行、原两背压及新增四信号场景通过。它不提供SIGKILL、abort或持久重启恢复，也不改变真实硬件G1/G2未执行的状态。

## 改动与来源

[设计](server-signal-shutdown-design.md)先于实现保存。旧服务只在operations阶段监听Ctrl-C；外层main将服务清理交给自身，未订阅TERM。USBFS的no_reattach使进程/FD关闭不足以证明原驱动恢复。修复只涉及4个CLI文件，无新依赖、unsafe、helper权限、engine、USBFS worker、Native或vendor改动。

Unix同步注册SIGINT与SIGTERM，早于helper通信、FD授予和listener。listener前prepare仅取得/验证backend，可由信号取消；bind直接await取得handle，之后任何操作结果都先await现有shutdown，并保留cleanup错误。输出线程创建在bind前；已中断的stdout事件不重试，末stopped输出也能被新信号中断。对象保持至返回，不用外层select丢掉正在清理的server。

取消prepare不终止已运行的spawn_blocking/nusb初始化系统调用，Tokio runtime仍可能等待它；不得据此承诺任意硬阻塞初始化能在3秒退出。重复信号不丢cleanup为源码所有权保证，本轮没有重复信号保持清理窗口的运行证据。非Unix保留Ctrl-C路径，未验证Windows USB。

## 旧产品负向

实际运行HEAD177c7b4、dirty新测试工具状态；前置捕获的106项产品输入与cf9fd3d相同，未把dirty写为clean。两个本地负向共享ready前捕获的一份source，blocked未伪称另有独立pre-capture。

| 实际运行 | 结果与可证明事实 | 持久证据 |
| --- | --- | --- |
| virtual ready TERM dev01 | FAIL；两个profile真实IMPORT/config1，各pending IN1；child实际-15，双数据socket EOF，已消费stdout无stopped | [local索引](../evidence/server-sigterm-old-local-development-20261023.json) |
| virtual blocked TERM dev02 | FAIL；独立OFD填满4096B、FIONREAD4096及真实writer阻塞；保持child blocking且不drain后TERM，child实际-15、双socket EOF。仅保存已消费前缀，不据此虚构末snapshot | [原summary](../evidence/local-server-sigterm-old-blocked-dev02-20261023/summary.json) |
| USBFS startup与主服务TERM dev01 | FAIL；startup关联已STOP helper的真实UDS backlog而非Signal selfpipe，TERM wait143且原sysfs不变；主服务KEY1和FTDI精确35B双向、两lease各pending/inflight后TERM也wait143。完整20.04秒后原driver均null，无stopped | [QEMU索引](../evidence/server-sigterm-old-qemu-development-20261023.json)、[原summary](../evidence/qemu-server-sigterm-old-dev01-20261023/summary.json) |

旧QEMU的52项host断言有8FAIL，两个guest exit0未覆盖失败。主服务的PID/FD消失、客户端0.19秒自主NULL/旧代际消失都不能代替服务端恢复；当轮原设备代际、config1/alt0及root0600保持，但usbhid/ftdi_sio未恢复。该有界观察不证明永久泄漏。没有stopped就没有末reap/预算/请求数证明。

[旧本地125项离线复核](../evidence/server-sigterm-old-local-independent-review-20261023.json)验证原bytes/索引及事实，复核者为harness作者，不称独立harness实现验收。[旧QEMU独立177项复核](../evidence/server-sigterm-old-qemu-independent-review-20261023.json)也保持原FAIL。三脚本、31份local及32份QEMU索引原件、失败日志和来源均保留。源码审查与68项静态/synthetic检查也不代替实际运行。

## 修复后软件与本地用例

clean934358f执行`scripts/check.sh`，默认89/all-features114/vendor19共222次测试执行，31条result、0失败/忽略，fmt、两组Clippy/release/严格Rustdoc通过，138份冻结输入前后未变：[完整证据](../evidence/server-signals-software-934358f-20261023/software.json)。

同一clean提交默认locked all-features构建运行`test-server-signals.py`，ready/blocked × SIGINT/SIGTERM四项均PASS、child exit0且未强制清理，source/binary guard通过：[本地最终索引](../evidence/local-server-signals-fixed-934358f-20261023/summary.json)。ready两项各收到真实stopped，两个profile均配置0、leased/pending/队列0；blocked两项保持4096B不drain，只证明有界exit0及双socket EOF，未观察的backend末资源不补为0。[本地离线138项复核](../evidence/server-sigterm-fixed-local-independent-review-20261023.json)为harness作者使用独立parser核实原始事实，不称独立harness实现审查。原SIGINT和约5秒输出deadline两项背压回归也通过，deadline仍非零报告输出阻塞。

CI934358f新增四项本地信号step，复用此前all-features release（显式skip-build），原命令失败退出状态在打印summary后保留。四原生平台[run39899971271](https://github.com/tiansongyu/usbip-client/actions/runs/39899971271)均实际completed/success，每格222次测试执行、旧两项背压与新增四组合通过；190项独立解析通过，[原日志/API与来源](../evidence/publication-server-signals-934358f-20261023.json)按原bytes归档。CI未上传各case独立stdout/protocol原文件，日志中的完整summary保留快照及文件哈希，不据此重建缺失原件。

## 修复后QEMU与回归

同一clean934358f顺序完成以下四项，全部PASS、8guest exit0，运行后138份输入和clean状态未变：[最终索引](../evidence/server-signals-fixed-validation-934358f-20261023.json)。以下构建均locked且没有skip-build；原虚拟不是all-features，不将四项写为相同ELF。

| 场景 | 构建 | 已有运行结果 |
| --- | --- | --- |
| SIGTERM | `--all-features` | 52项host检查PASS；startup与主服务均wait0，同代际原config1/alt0/usbhid与ftdi_sio恢复，stopped的lease/pending/queued/inflight/预算0；真实reap增长，client自主NULL/旧代际消失 |
| 原虚拟HID/CDC | `--features rustusb-cli/rust-backend` | PASS；19条probe，原两周期evdev/CDC功能和释放回归；227项独立raw复核覆盖此回归 |
| USBFS HID/FTDI正常 | `--all-features` | PASS；15条probe、两周期实际I/O与恢复；227项独立raw复核覆盖此回归 |
| USBFS HID/FTDI热拔 | `--all-features` | PASS；15条probe、热拔后另一设备继续、新代际不继承授权；227项独立raw复核覆盖此回归 |

all-features CLI SHA256 `adedf4b1c90d5f1a95033d49fd72caa425860d192855d827d5a88eb0e654699e`，helper `837aec7045ab08d935e8d887b686db8e160a5c849fd236c653710c0320dca46b`。原虚拟CLI为`05dcacc24c173c8a2bbc032fe2f6438f2003f172de9e96cc3ae837c6f3a0f393`，helper为`390ee333129f852a31869bbfef4394e4b2c85ed2eac05c47d32bc5229d5a2b99`。每项结果归其实际构建，不把同源码当相同ELF。

TERM两来宾使用ttyS1程序证据/ttyS0内核日志，严格JSON/marker，无拼补或ANSI清洗。startup只证明请求连接已进入真实helper listener的内核backlog，helper接受/授权未被观察；主服务先保持真实USBFS inflight/预算再送一次TERM。client先自主NULL/旧代际消失再清自有缓存，server原config/alternate/driver及代际、节点模式与末资源须独立核验。reaped增长与cancel计数单调分别记录，不强迫取消总是获胜。

## 复现和未完成项

命令、环境和严格判定见[测试手册](../testing.md)。本地测试只信号其自身普通用户child，使用loopback；QEMU仅操作一次性guest合成HID/FTDI，私有socketpair网络，无宿主USB、模块或网络配置变更。ss/UNIX_DIAG仅用于guest诊断。

SIGKILL/abort/panic后的恢复、跨进程监督及持久重启协调仍未实现；helper丢失后的物理设备恢复本轮未验收。kernel内部硬阻塞、重复信号运行矩阵、非Unix信号/USB路径、Windows驱动和真实G1/G2保持未验收。ISO、无原驱动非零alternate通用恢复、远端认证/TLS、完整Phase2/3及后续TUI/GUI/安装/性能目标继续保留。

[TERM作者raw复核181项](../evidence/server-sigterm-fixed-qemu-author-raw-review-20261023.json)明确披露harness作者身份，未调用runner.verify。FTDI host接收器持久记录实际count/digest/exactmatch，未单独保存host chardev原始payload，不能把离线复核写成重放原始TX字节。

[三项回归独立复核227项](../evidence/server-signals-fixed-regressions-independent-review-20261023.json)重建各feature/source来源并核对原始事件。旧runners的evdev/TTY原文件及热拔第三组receipt未持久；该部分依赖冻结guest的精确断言与同slot计数，未写成独立读取不存在的capture/receipt。
