# bug 索引 - ASR1603

> 自动生成于 2026-06-22 14:53，共 7 条

| # | 工作项ID | 模块 | 症状关键词 | 根因方向 | 文件 |
|---|---|---|---|---|------|
| 1 | NA | FTP / AT框架 | URC端口切换, FILEFTPGET进度上报, USB端口抢占 | 全局变量 `nwy_at_engine` 在每次AT命令处理时被覆写，FTP异步回调通过该全局变量获取端口，导致后... | [FILEFTPGET_URC端口切换](FILEFTPGET_URC端口切换.md) |
| 2 | NA | FTP / AT框架 | URC端口错乱, FILEFTPGET进度上报, 全局变量覆盖 | `nwy_at_engine` 是全局单一变量，每次AT命令到来时被覆盖写入，异步回调中通过该变量获取端口信息时取... | [FILEFTPGET下载进度URC端口错乱](FILEFTPGET下载进度URC端口错乱.md) |
| 3 | NA | FTP / LFS文件系统 | File Write Fail, LFS No more free space, FTP下载失败 | Flash文件系统(LFS)存储空间耗尽，`nwy_vfs_fwrite` 返回0，触发 `+FILEFTPGET... | [FILEFTPGET文件写入失败](FILEFTPGET文件写入失败.md) |
| 4 | 7017934398 | FTP 客户端（pcac/duster/src/ftp_client.c + pcac/NWY_FRAMEWORK AT 层） | Server Control Link Disconnect, Error Not Login, PASV/STOR, FTP 压测概率性断开, 服务器 RST | 服务器（FileZilla Server 0.9.60 beta）在一次 PASV 后异常连发两条 227（端口 ... | [FTP控制链路压测异常断开](ASR1603_FTP控制链路压测异常断开.md) |
| 5 | NA | FOTA / 启动加载 / scatter 段放置 | DataAbort, FotaTrig死机, FOTA循环测试死机, mini.sys.enable, NON_OTA段未加载, BIST残留, Permission fault | FOTA 重试 NV 函数被 scatter 通配符 `*nota-nota.lib` 归入 NON_OTA_CODE_IN_PSRAM 段，mini system 模式(mini.sys.enable=1)启动时该段未加载，但重试逻辑仅在此模式下运行——"必须执行的代码位于无法加载的段"设计矛盾。已修复（迁至 download.c/PL_CODE 段）。 | [FotaTrig_DataAbort_代码段未加载](FotaTrig_DataAbort_代码段未加载.md) |
| 6 | NA | TCP自动重建(nwy_tcpsrv_redial) / PPPREDIAL / RTI线程切换插桩 | DataAbort, rti_thread_switch_out, 空指针解引用, RTI记录全零, FAULT_ADDRESS=0xE59FF018, 栈溢出, 越界写, 任务栈2048B, nwy_tcpsrv_redial, cisend | 新增的 TCP 自动重建任务 `nwy_tcpsrv_redial` 栈仅 2048B，在 ASR(Cortex-... | [NA_rti_switchout_null_deref](ASR1603_NA_rti_switchout_null_deref.md) |
| 7 | 7018786802 | AT 命令 / UDP 服务端 / IPv6 地址处理 | UDPSENDS ERROR、UDPRECV(S) 源 IP 错误、IPv6 地址截断、V6 链路收不到数据 | UDP 服务端 `+UDPRECV(S)` URC 生成时，`nwy_app_ip6addr_ntoa()` 入参... | [UDP_V6源地址上报错误](ASR1603_UDP_V6源地址上报错误.md) |
