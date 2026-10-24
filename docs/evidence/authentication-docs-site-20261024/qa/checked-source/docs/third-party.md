# 第三方来源与许可证

RustUSB 自有代码采用 MIT。未复制 Linux GPL 用户态/内核源文件。可选 Rust 虚拟后端按 MIT 选择性复用固定 jiegec 的 HID report 描述符，源码注明来源、修改与版权，完整许可见 [LICENSES/jiegec-usbip-MIT.txt](../LICENSES/jiegec-usbip-MIT.txt)。公开协议其余部分独立实现。本清单是工程登记，不代替发布时逐文件合规审查。

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
| OpenSSL CLI | QEMU认证负向使用发行版3.0系列工具，实际版本、ELF及动态库哈希随每次运行记录 | Apache-2.0；仅测试签发一次性证书和独立TLS客户端，不是产品Rust依赖。临时guest镜像不发布，私钥不归档；用法与边界见[测试设计](research/secure-negative-qemu-design.md) |
| h2 / http / bytes | h2精确0.4.20，[固定源码b0e89084](https://github.com/hyperium/h2/tree/b0e890842ac16d3c76836fd4fcc06421acb15fa7)；本轮lock实际http1.5.0、bytes1.12.1 | h2 MIT，http/bytes MIT OR Apache-2.0。v2候选复用单stream流控及PING/PONG，不修改或复制上游实现，不启实验feature；实际TLS flush另由本项目安全wrapper跟踪，不能把h2入队当发送完成 |
| h2新增传递依赖 | lock实际atomic-waker1.1.2、equivalent1.0.3、fnv1.0.7、futures-sink0.3.34、hashbrown0.17.1、indexmap2.14.2、tokio-util0.7.20 | 前六项主体MIT OR Apache-2.0（fnv旧元数据写作Apache-2.0 / MIT，实际两许可文件分别核）；tokio-util MIT。atomic-waker另有LICENSE-THIRD-PARTY，记录2016 Alex Crichton/2017 Tokio的Apache/MIT条款，发布时须保留相应通知。九个新crate归档SHA逐Cargo.lock checksum核对，清单不是完整发布合规审查 |

工作区所有feature的直接/传递依赖**实际解析版本和SPDX**导出到 `dependency-inventory.json`，生成脚本为 `scripts/dependency-inventory.py`（`cargo metadata --all-features --locked`）。发布前重新运行并审查所有license缺失/自定义条目和目标平台依赖；锁文件固定版本不等于已完成许可证审查。原生系统usbip/libusb/QEMU等不包含在Cargo元数据中，需单独登记。nusb原有底层包含unsafe，项目自身核心不新增unsafe；不能将核心规范扩大表述成全部传递依赖无unsafe。

诊断导出新增内部 `rustusb-diagnostics` crate（MIT），锁解析159→160包；所有外部package块（版本、source、checksum和依赖）逐字节保持。固定Rust1.97.1、Tokio1.54.1、rustix1.1.5和serde_json1.0.154的文件/进程/有界序列化研究见[设计](research/diagnostic-export-design.md)，没有复制其产品实现或新增第三方依赖。

QEMU仅用于开发审计，未随产品捆绑。上游QEMU脚本下载/构造的内核、busybox和工具有各自许可证，生成的临时镜像不作为本仓库发布物。
