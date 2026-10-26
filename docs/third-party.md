# 第三方来源与许可证

E63 Native导入PM不新增依赖、外部版本或vendor改动；复用已锁定rustix1.1.5 safe open/openat与标准File。固定Linuxv5.15/v6.8 PM/USB源码、原许可和主来源读取收据仅保存在[研究证据](evidence/native-import-power-research-20261026/summary.json)中，保留各文件GPL/SPDX与COPYING/GPL-2.0/Linux-syscall-note，不重标MIT、不链接进产品。新Rust策略独立实现固定sysfs接口；官方源码用于行为审查。

E58不新增Rust包/version/checksum。Debian builder使用现有dpkg-dev/binutils与原Python3/systemd/adduser接口，原源码研究见[Debian](research/debian-lifecycle-research.md)/[systemd](research/systemd-lifecycle-research.md)，不复制其GPL/LGPL实现进产品。预览包另携目标解析Cargo graph（含build/dev，非逐ELF SBOM）的原license/notice、固定upstream补充文本/provenance，缺文本明确拒绝；GUI携selectors0.38.0未改MPL source与Mozilla原文本。全锁root222/GUI475并非188/345本机target节点，不能混算依赖数；实际包/许可字节检查见[验收](research/debian-installation-validation.md)。

RustUSB 自有产品代码采用 MIT。未将 Linux GPL 用户态/内核源文件复制进产品实现；研究证据中保留原许可的上游文本不属于MIT重许可。可选 Rust 虚拟后端按 MIT 选择性复用固定 jiegec 的 HID report 描述符，源码注明来源、修改与版权，完整许可见 [LICENSES/jiegec-usbip-MIT.txt](../LICENSES/jiegec-usbip-MIT.txt)。公开协议其余部分独立实现。本清单是工程登记，不代替发布时逐文件合规审查。

| 组件 | 固定研究版本 / 来源 | 许可证与处理 |
| --- | --- | --- |
| Linux USB/IP | [7be4bc52](https://github.com/torvalds/linux/tree/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip)；补核5.15/6.8 | 工具/驱动主要GPL-2.0-or-later或文件SPDX；协议文档与各文件须分别核对。只外部调用发行版程序，不重标MIT；若发布包捆绑需履行对应源码/许可义务 |
| jiegec/usbip | [61938f8c](https://github.com/jiegec/usbip/tree/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0) | MIT，版权2020–2025 Jiajie Chen。HID report descriptor 片段选择性复用；不依赖上游 crate、网络 engine 或物理 host 实现；来源见新后端 descriptors.rs，完整许可随仓库保留 |
| nusb | 可选物理adapter及Linux helper受限恢复固定0.2.8，[c80218d3](https://github.com/kevinmehall/nusb/tree/c80218d342740321190436bc45304ebbbf16bef0) | MIT OR Apache-2.0；完整70份原文件与两份许可保留于`vendor/nusb`，维护Linux safe API扩展，含保持errno的checked release和借用FD的同步CONNECT薄入口；[修复与验证](research/usbfs-checked-release.md)。[补丁说明](../vendor/nusb/RUSTUSB.md)、[逐文件来源哈希](../vendor/nusb/RUSTUSB-PROVENANCE.json)和[精确diff](../vendor/nusb/RUSTUSB.patch)随库保留；边界见[物理adapter设计](research/rust-physical-backend-design.md) |
| rusb / libusb | 上游研究对象，未作为本产品依赖 | rusb MIT；libusb LGPL-2.1-or-later，若以后采用需评估链接和再分发 |
| Rust/Cargo | 本次安装1.97.1，Edition2024 | 工具链外部依赖，不捆绑 |
| rustls / tokio-rustls | 精确0.23.46 / 0.26.6，[来源与设计](research/secure-transport-design.md) | rustls Apache-2.0 OR ISC OR MIT；tokio-rustls MIT OR Apache-2.0。官方crate checksum已核，显式ring/std、关闭默认features，仅TLS1.3；现已接入安全USB/IP broker，实际范围见[验收报告](research/secure-usbip-validation.md) |
| ring / rustls-webpki / rustls-pki-types | 实际解析0.17.14 / 0.103.16 / 1.15.1 | Cargo元数据分别为Apache-2.0 AND ISC、ISC、MIT OR Apache-2.0；ring完整`LICENSE`另列BoringSSL及历史来源条款，须按各crate完整许可分发。不能把密码库和底层unsafe表述为自有MIT实现或FIPS认证 |
| rcgen | 精确0.14.10，仅新transport测试生成内存证书 | MIT OR Apache-2.0；关闭默认features、启ring，不提交测试私钥，不作为产品证书签发工具 |
| sha2 | 已锁定0.10.9，MIT OR Apache-2.0 | 自动规则service复用既有SHA-256，对冻结的本地/远程观察元组生成metadata token；不把hash当设备授权或密码学硬件身份。根及GUI锁各新增一条service依赖边，外部版本/source/checksum及222包根库存保持 |
| x509-parser | 精确0.18.1，[CRL设计](research/offline-crl-design.md)，checksum锁定 | MIT OR Apache-2.0；关闭默认features，仅解析有界完整CRL元数据，签名/链/实际撤销仍由rustls/webpki。该版本和子树已在旧lock中，但此前不是工作区实际normal依赖；本次启用不将其结构解析当成密码认证或无panic证明 |
| governor | 精确0.10.4，固定VCS `9f3a79dd47dd32acd589c562b8d4fefe99b93372`，[设计与源码](research/authentication-admission-design.md) | MIT；仅std features，显式MonotonicClock、direct非等待GCRA，不使用keyed map、quanta或jitter。新认证quota与原16连接owner预算分开；内部约584年ns horizon保留为依赖边界，所测quota与真实验收见专项报告，不宣称无限运行或完整DoS防护 |
| OpenSSL CLI | QEMU认证负向使用发行版3.0系列工具，实际版本、ELF及动态库哈希随每次运行记录 | Apache-2.0；仅测试签发一次性证书和独立TLS客户端，不是产品Rust依赖。临时guest镜像不发布，私钥不归档；用法与边界见[测试设计](research/secure-negative-qemu-design.md) |
| h2 / http / bytes | h2精确0.4.20，[固定源码b0e89084](https://github.com/hyperium/h2/tree/b0e890842ac16d3c76836fd4fcc06421acb15fa7)；本轮lock实际http1.5.0、bytes1.12.1 | h2 MIT，http/bytes MIT OR Apache-2.0。v2候选复用单stream流控及PING/PONG，不修改或复制上游实现，不启实验feature；实际TLS flush另由本项目安全wrapper跟踪，不能把h2入队当发送完成 |
| h2新增传递依赖 | lock实际atomic-waker1.1.2、equivalent1.0.3、fnv1.0.7、futures-sink0.3.34、hashbrown0.17.1、indexmap2.14.2、tokio-util0.7.20 | 前六项主体MIT OR Apache-2.0（fnv旧元数据写作Apache-2.0 / MIT，实际两许可文件分别核）；tokio-util MIT。atomic-waker另有LICENSE-THIRD-PARTY，记录2016 Alex Crichton/2017 Tokio的Apache/MIT条款，发布时须保留相应通知。九个新crate归档SHA逐Cargo.lock checksum核对，清单不是完整发布合规审查 |

工作区所有feature的直接/传递依赖**实际解析版本和SPDX**导出到 `dependency-inventory.json`，生成脚本为 `scripts/dependency-inventory.py`（`cargo metadata --all-features --locked`）。发布前重新运行并审查所有license缺失/自定义条目和目标平台依赖；锁文件固定版本不等于已完成许可证审查。原生系统usbip/libusb/QEMU等不包含在Cargo元数据中，需单独登记。nusb原有底层包含unsafe，项目自身核心不新增unsafe；不能将核心规范扩大表述成全部传递依赖无unsafe。

诊断导出新增内部 `rustusb-diagnostics` crate（MIT），锁解析159→160包；所有外部package块（版本、source、checksum和依赖）逐字节保持。固定Rust1.97.1、Tokio1.54.1、rustix1.1.5和serde_json1.0.154的文件/进程/有界序列化研究见[设计](research/diagnostic-export-design.md)，没有复制其产品实现或新增第三方依赖。

QEMU仅用于开发审计，未随产品捆绑。上游QEMU脚本下载/构造的内核、busybox和工具有各自许可证，生成的临时镜像不作为本仓库发布物。

E61 ARM64测试额外使用发行版qemu-system-arm6.2+dfsg-2ubuntu6.31与EDK2/qemu-efi-aarch64 2022.02-3ubuntu0.22.04.7，均仅临时解包运行，未随产品或证据捆绑二进制。QEMU/固件保留各自GPL/BSD等逐文件许可，不以本仓库MIT重许可；实际发行版copyright清单及包SHA256随[环境证据](evidence/arm64-environment-20261026/summary.json)保存。无Rust依赖、Cargo.lock或vendor变化，未复制上游实现进入产品核心。

认证准入新增固定governor0.10.4及14个锁package，当前全feature清单174包且无缺失license metadata。它的normal子树禁用quanta/dashmap/rand/getrandom，但不将此扩大为TLS/ring全树无随机源；实际编入、源码、feature和后续验收分别记账。

主机名回退未新增外部依赖或改变Cargo.lock，清单保持174包。复用固定Tokio1.54.1 TCP/actual blocking pool和平台std/NSS，test-util仅新增protocol dev feature。固定Tokio、Rust1.97.1与glibc2.35源码只读研究原件及各自许可保存在[E40证据](research/dual-stack-connect-validation.md)，未将glibc源码移入MIT产品；Ubuntu精确patch源码404的审查限制保留。

本地发现观察新增内部MIT包rustusb-discovery（完整实现，service re-export），现有锁定rustix1.1.5增用net/event安全API；未引入udev/tokio-udev/libudev或新外部package/version。175包清单中的增量仅这一内部包。固定Linux/systemd源码只作研究，不复制其GPL/LGPL实现进入MIT产品。研究许可及实际零宿主事件探针另按清单保留，未作为热插拔产品验收。

离线CRL把锁定x509-parser0.18.1提升为transport直接normal依赖，未改变任何外部package/version/checksum，重新导出的全feature清单仍175包且无缺失license metadata。独立实际构建为parser features=[]、normal子树30包（含自身），不是30个新增锁包或全产品无密码库；此前Cargo metadata宽resolver包含未实际编入的optional/dev feature，不能用它冒充编译证据。固定源码、许可、探针及失败按CRL专项报告归档。

可选终端前端新增内部MIT crate rustusb-tui，固定Ratatui0.30.2及Crossterm0.29.0（两者MIT/MSRV1.88和1.63）。全feature inventory为221包（增46含内部1）；旧版本/source/checksum不变，mio/time/hashbrown原锁包的依赖边受新feature/多版本名称消歧影响，不声称整个旧package块不变。新增包license metadata无缺失，包含Apache-2.0 OR BSL-1.0等表达式，仍需完整发布许可审查。Ratatui关闭facade默认仍经backend启用Crossterm默认features及core layout-cache；不可称实际只有events。默认Native CLI normal依赖树仍需实核不含UI/TLS。组件精确来源/许可/探针见终端设计与后续验证报告；不复制上游渲染实现，不将传递unsafe或全依赖漏洞审查说成已消除。

前端偏好复用既有serde/Rustix/Tokio/std，无新外部runtime依赖；tempfile仅加到TUI dev依赖，所有外部version/source/checksum不变，inventory仍221且无缺失license metadata。XDG配置位置按官方0.8核对，未复制外部配置库或新增组件技能。QEMU屏幕解码沿E45的外部test-only pyte0.8.2/LGPLv3和wcwidth0.2.13/MIT，二者未随MIT Rust产品或证据打包为依赖实现。


E47终端故障验证复用现有Ratatui/Crossterm/Tokio/Rustix与test-only pyte0.8.2/wcwidth0.2.13；未新增或升级任何外部依赖，库存仍221。新增guest-only C控制夹具为本仓库MIT代码，不进入产品数据通路。详见[验证报告](research/tui-failure-retry-validation.md)。


E48仅复用锁定Ratatui/Crossterm/Tokio/Rustix和原test-only pyte0.8.2/wcwidth0.2.13；无外部版本或依赖边变更，inventory仍221。新增guest-only C/脚本为本仓库MIT夹具，不进入产品USB数据通路。 见[验收与审计](research/tui-hotplug-permission-validation.md)。


Phase5原生GUI使用独立workspace：Tauri2.12.5、tauri-build2.7.4、runtime-wry2.12.3、wry0.57.1/tao0.37.2实际锁定，Apache-2.0 OR MIT（具体包以[GUI库存](../apps/rustusb-gui/dependency-inventory.json)为准，共475条全平台Cargo metadata，不等于475个Linux runtime）。GTK3/WebKitGTK/Mesa/fonts等原生系统组件保留各自LGPL/GPL/MIT等许可，未被本项目MIT重许可，临时QEMU rootfs不发布。glib0.18.5完整121原文件/MIT许可保留，只有variant_iter.rs两行按上游05dff0ee回补0429；[来源/精确补丁](../apps/rustusb-gui/vendor/README.md)和[实际验证](research/native-gui-validation.md)独立记账，不压制advisory或伪造版本，不称整个GTK已审计。根库存222仅新增内部MIT frontend crate，外部root依赖版本保持；GUI自己的gate/CI不被根检查替代。WebKitWebDriver/Xvfb及测试用程序仅在临时guest运行，不随产品分发，实际ELF哈希在证据原件中。

E57 frontend另直接复用已锁定sha20.10.9作本地endpoint文件名/上下文摘要，不当授权或设备认证。两锁仅内部frontend package依赖表多一条sha2，全部外部package/version/source/checksum不变；offline locked metadata重导出的root222/GUI475库存逐字节相同且无缺失license metadata，见[原核对](evidence/frontend-intent-recovery-20261025/dependency-review.json)。原test-only pyte/wcwidth及GLib版本/backport不变，不称完整许可/安全审计。

## Storage inspection research (2026-10-26)

The [storage inspection evidence](evidence/storage-inspection-20261026/manifest.json) includes fixed Linux v5.15/v6.8 and QEMU v6.2.0 research originals, source manifests and license notices. These GPL research copies are separate from the MIT product implementation. No new product dependency or copied kernel implementation is introduced. Existing rustix filesystem/process wrappers provide the metadata calls; the disposable C reference holder is an original test fixture. See the [design](research/storage-inspection-design.md) for exact upstream commits and limitations.

## Storage arrival mechanism research

The [arrival validation](research/storage-arrival-quarantine-validation.md) retains Linux v5.15/v6.8, QEMU v6.2.0 and sg3-utils 1.46 research originals with source receipts and license files in its evidence archive. These GPL upstream files are not copied into MIT product implementation. The guest-only claim probe is local test code; no new product dependency or patched emulator is introduced. The QEMU nonzero-LUN REQUEST SENSE limitation is documented, not silently corrected or accepted as an equivalent fault result.
