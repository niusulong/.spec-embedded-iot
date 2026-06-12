# Bug 解决方案索引 - ASR1603

> 自动生成于 2026-06-12 16:03，共 4 条

| # | 模块 | 症状关键词 | 根因方向 | 文件 |
|---|------|-----------|---------|------|
| 1 | FTP / AT框架 | URC端口切换, FILEFTPGET进度上报, USB端口抢占 | 全局变量 `nwy_at_engine` 在每次AT命令处理时被覆写，FTP异步回调通过该全局变量获取端口，导致后... | [FILEFTPGET_URC端口切换](FILEFTPGET_URC端口切换.md) |
| 2 | FTP / AT框架 | URC端口错乱, FILEFTPGET进度上报, 全局变量覆盖 | `nwy_at_engine` 是全局单一变量，每次AT命令到来时被覆盖写入，异步回调中通过该变量获取端口信息时取... | [FILEFTPGET下载进度URC端口错乱](FILEFTPGET下载进度URC端口错乱.md) |
| 3 | FTP / LFS文件系统 | File Write Fail, LFS No more free space, FTP下载失败 | Flash文件系统(LFS)存储空间耗尽，`nwy_vfs_fwrite` 返回0，触发 `+FILEFTPGET... | [FILEFTPGET文件写入失败](FILEFTPGET文件写入失败.md) |
| 4 | FOTA / 启动加载 | DataAbort, FotaTrig死机, BIST残留, PSRAM未加载 | `NON_OTA_CODE_IN_PSRAM` 段未从Flash搬运到PSRAM，CPU执行BIST内存测试残留数... | [FotaTrig_DataAbort_代码段未加载](FotaTrig_DataAbort_代码段未加载.md) |
