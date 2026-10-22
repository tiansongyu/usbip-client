# RustUSB 工作日志

## 2026-10-22：任务与环境基线

- 读取完整任务并保存在 `requirements.md`。当前目录初始为空且无 Git 历史。
- 用户追加要求：适时 commit、记录过程、最终 push 到 `git@github.com:tiansongyu/usbip-client.git`。
- `git ls-remote --symref … HEAD refs/heads/*` 成功，无 refs；建立 main 和 origin，不覆盖既有历史。
- 工具链：rustc/cargo 1.97.1；宿主 Ubuntu 内核 6.8.0-106-generic，x86_64；普通 uid 1000。已存在 usbip/usbipd (usbip-utils 2.0)。
- `/sys/bus/usb/devices` 有真实设备，但未获指定测试设备，不自动解绑、导出、加载模块或修改宿主服务。VHCI sysfs 未出现。不能声明实际 USB 直通成功。
- 启动并行 Linux 内核/用户态审计、jiegec Rust 后端审计和安全/验收计划。源代码下载仅在 /tmp，项目不包含 Linux GPL 源码。
- 使用 writing-technical-design、technical-documentation、skill-creator；用户明确限定项目技能和文档范围，不生成无关逐组件技能。
- 下一步：固定上游来源并形成 Phase 0 设计，再实现和测试 Phase 1 Native 路径。长期阶段保留完整验收 gate。
