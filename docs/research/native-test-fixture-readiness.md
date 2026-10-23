# Native代际测试夹具的确定性

2026-10-23。冻结QEMU来源6e809a1的CI39894822120在Ubuntu24.04 ARM的一项既有Native测试FAIL；[原始日志/API](../evidence/publication-checked-release-freeze-failure-20261023.json)保留3 PASS/1 FAIL，不重试或改判。失败为fake usbip子进程超过fixture200ms命令预算，尚未到达测试的代际断言。QEMU十一项通过是另一独立结果。

仅修正 `#[cfg(all(test, unix))]` 测试模块：普通fixture命令预算使用5秒，真正timeout测试显式覆盖回原200ms，仍断言Timeout和child被kill/reap，settle150ms保持。更替代际的测试不再通过等待argv加40ms sleep猜测时序，而让fake命令顺序完成NULLstatus、mv保留旧目录、mkdir同路径新目录后退出；原代际提前捕获。随后仍严格断言路径存在、原generation不匹配，并验证只有一次精确detach命令。

生产Native配置仍为command15秒、settle10秒，生产代码、USBFS修复和QEMU脚本完全不改。错误等待替换代际仍会触发原settle150ms期限，未把测试变成只检查调用成功。旧目录保留防止inode立即复用。纯fixture测试不触宿主USB、sysfs或内核模块。

[定向验证](../evidence/native-fixture-fix-review-20261023.json)Native31/31、all-target Clippy、rustfmt与diffcheck通过。随后完整222项软件gate及新提交的四平台CI由后续证据记录。既有6e809a1最终QEMU冻结证据保留原来源，不重写成测试修订后的提交。
