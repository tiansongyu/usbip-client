# 第三方来源与许可证

RustUSB 自有代码采用 MIT。未复制 Linux GPL 用户态/内核源文件。可选 Rust 虚拟后端按 MIT 选择性复用固定 jiegec 的 HID report 描述符，源码注明来源、修改与版权，完整许可见 [LICENSES/jiegec-usbip-MIT.txt](../LICENSES/jiegec-usbip-MIT.txt)。公开协议其余部分独立实现。本清单是工程登记，不代替发布时逐文件合规审查。

| 组件 | 固定研究版本 / 来源 | 许可证与处理 |
| --- | --- | --- |
| Linux USB/IP | [7be4bc52](https://github.com/torvalds/linux/tree/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip)；补核5.15/6.8 | 工具/驱动主要GPL-2.0-or-later或文件SPDX；协议文档与各文件须分别核对。只外部调用发行版程序，不重标MIT；若发布包捆绑需履行对应源码/许可义务 |
| jiegec/usbip | [61938f8c](https://github.com/jiegec/usbip/tree/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0) | MIT，版权2020–2025 Jiajie Chen。HID report descriptor 片段选择性复用；不依赖上游 crate、网络 engine 或物理 host 实现；来源见新后端 descriptors.rs，完整许可随仓库保留 |
| nusb | 可选物理adapter固定0.2.8，[c80218d3](https://github.com/kevinmehall/nusb/tree/c80218d342740321190436bc45304ebbbf16bef0) | MIT OR Apache-2.0；完整70份原文件与两份许可保留于`vendor/nusb`，维护Linux safe API扩展。[补丁说明](../vendor/nusb/RUSTUSB.md)、[逐文件来源哈希](../vendor/nusb/RUSTUSB-PROVENANCE.json)和[精确diff](../vendor/nusb/RUSTUSB.patch)随库保留；边界见[物理adapter设计](research/rust-physical-backend-design.md) |
| rusb / libusb | 上游研究对象，未作为本产品依赖 | rusb MIT；libusb LGPL-2.1-or-later，若以后采用需评估链接和再分发 |
| Rust/Cargo | 本次安装1.97.1，Edition2024 | 工具链外部依赖，不捆绑 |

工作区所有feature的直接/传递依赖**实际解析版本和SPDX**导出到 `dependency-inventory.json`，生成脚本为 `scripts/dependency-inventory.py`（`cargo metadata --all-features --locked`）。发布前重新运行并审查所有license缺失/自定义条目和目标平台依赖；锁文件固定版本不等于已完成许可证审查。原生系统usbip/libusb/QEMU等不包含在Cargo元数据中，需单独登记。nusb原有底层包含unsafe，项目自身核心不新增unsafe；不能将核心规范扩大表述成全部传递依赖无unsafe。

QEMU仅用于开发审计，未随产品捆绑。上游QEMU脚本下载/构造的内核、busybox和工具有各自许可证，生成的临时镜像不作为本仓库发布物。
