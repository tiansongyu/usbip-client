# Native USED 与 sysfs 初始化窗口（只读）

固定 Linux v6.8 源码确认：`vhci_hcd.c:759–774` 在 SET_ADDRESS 中先设置 `VDEV_ST_USED`；`hub.c:4992/5412/5483` 的枚举路径随后才进入 `usb_new_device`，到 `hub.c:2614` 执行 `device_add`。因此 status 6 不保证 `/sys/bus/usb/devices/<bus>` 已可访问。

当前 `channel.rs:531` 的 `observe_local` 对 canonicalize/devnum/VIDPID 的 ENOENT 立即返回错误；现有等待仅覆盖配置真正为空和接口数量尚不足。首个 41e2c67 run 的 serial 在 SetAddress 后断开与此相容，但 stderr 因采证脚本缺 base64 丢失，不能将它定为已证实首因。产品未修改，等第二 run 完整诊断。

若之后修复，只让明确初始化未就绪返回 Pending，保留原有界等待/取消/actual blocking guard。首次已观察 generation 固定，replacement、权限、格式及身份不符仍拒绝；完成前后复核同 row 和 local generation，不用 blanket retry。commit 当前等待期间会覆盖 self.local，应同时收紧此身份不变式。

固定文件及 SHA：

- `/tmp/rustusb-vhci-family-source-20261023/drivers__usb__usbip__vhci_hcd.c`：`043dc9e555ed4d4f1ecd606e43bed83a9240bb3a8ba6a19de5f437c8a4308c4e`；Git blob `82650c11e4516125777dc55e4719e398fbbb644c`。
- `/tmp/rustusb-vhci-family-source-20261023/drivers__usb__usbip__vhci_sysfs.c`：`8c3a861a5a474f8688779fa1127c6b31a78a330c3918e0844feb1eda82886e34`；Git blob `e2847cd3e6e363767a6dbf411f24bb628eb29566`。
- `/tmp/rustusb-native-export-state-source-20261023/drivers__usb__core__hub.c`：`d486569c6da9a3166ae06fa5a9ab27b3ff11ad708ea57846e07e58ce2038626f`；Git blob `e38a4124f6102a5ff2a47107a8286815cfc5c8e2`。

产品 `crates/rustusb-native/src/channel.rs`：`dc46c36b87c813c8c7fa95aa34cddd22ee454db465d6603feb7e8b3ea454ff2c`，与首 run 冻结副本逐字节相同。完整原件/行号/边界见同名 JSON。
