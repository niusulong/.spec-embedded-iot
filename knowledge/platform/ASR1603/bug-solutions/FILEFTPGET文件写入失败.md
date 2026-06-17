# FILEFTPGET 文件写入失败分析报告

## 0. 结构化摘要

| **字段** | 值 |
|----------|-----|
| **工作项 ID** | NA |
| **平台** | ASR1603 |
| **模块** | FTP / LFS文件系统 |
| **问题分类** | 存储空间不足 |
| **症状关键词** | File Write Fail, LFS No more free space, FTP下载失败 |
| **根因概述** | Flash文件系统(LFS)存储空间耗尽，`nwy_vfs_fwrite` 返回0，触发 `+FILEFTPGET: File Write Fail` 错误上报并中止FTP传输 |
| **调用链摘要** | `nwy_app_at_fileftpget_func()` → `nwy_ftp_rsp_cb()` → `nwy_ftp_file_write()` → `nwy_vfs_fwrite()` → LFS: No more free space |
| **检索关键词** | FILEFTPGET, File Write Fail, LFS, No more free space, nwy_vfs_fwrite, Flash空间 |

---

## 1. 问题描述

| 项目 | 内容 |
|------|------|
| **现象** | FTP 下载文件时，AT 命令 `AT+FILEFTPGET` 返回 `+FILEFTPGET: File Write Fail`，文件无法保存到本地文件系统 |
| **时间范围** | 2026-06-02 10:43:10 ~ 10:52:22 |
| **问题类型** | 单日志分析（对比日志内成功/失败案例） |
| **涉及文件** | `009988.gz`、`05-4c.pack` |
| **日志来源** | `.spec/logs/Log 02-6月-26 10;53;04.txt` |

---

## 2. 日志时序分析

### 2.1 完整事件时间线

| 时间 | 事件 | 结果 |
|------|------|------|
| 10:43:10.405 | `AT+FILEFTPGET="009988.gz"` (第1次) | ❌ 10:44:36 File Write Fail |
| 10:50:33.213 | `AT+FSFAT` (格式化文件系统?) | OK |
| 10:50:39.033 | `AT+FILEFTPGET="009988.gz"` (第2次) | ❌ 10:52:05 File Write Fail |
| 10:52:14.522 | `AT+FILEFTPGET="009988.gz"` (第3次) | ❌ 10:52:16 File Write Fail |
| 10:52:18.967 | `AT+FILEFTPGET=05-4c.pack` (第1次) | ❌ 10:52:19 File Write Fail |
| 10:52:22.173 | `AT+FILEFTPGET=05-4c.pack` (第2次) | ❌ 10:52:22 File Write Fail |

### 2.2 第二次 009988.gz 下载详情（有成功→失败的转折）

**成功阶段**：10:50:39 ~ 10:52:05，共 **481 次**成功写入，每次 1400 字节
- 成功写入总量：481 × 1400 = **673,400 字节（约 658 KB）**

**失败转折**：10:52:05.854
```
LFS:lfs.c:608:error: No more free space 307
NWY_FRM: nwy_app_at_func_ftp.c 288 nwy_vfs_fwrite fail: expect=1400, actual=0
```
- `nwy_vfs_fwrite` 返回 0（期望 1400），实际写入 0 字节
- LFS 文件系统报 **"No more free space 307"**

---

## 3. 对比分析

### 3.1 成功 vs 失败对比

| 维度 | 成功（10:50:39~10:52:05） | 失败（10:52:05 之后） |
|------|--------------------------|----------------------|
| `nwy_vfs_fopen_ex` | 返回有效句柄 | 返回有效句柄 |
| `nwy_vfs_fwrite` 返回值 | 1400（= expect） | **0**（≠ expect） |
| LFS 错误 | 无 | **No more free space 307** |
| 数据实际写入 | 成功 | 完全无法写入 |

### 3.2 关键差异

| 时间点 | 正常日志值 | 异常日志值 | 差异说明 |
|--------|-----------|-----------|----------|
| 10:52:05.522 | `nwy_vfs_fwrite ret = 1400` | — | 最后一次成功写入 |
| 10:52:05.854 | — | `LFS: No more free space 307` | **LFS 空间耗尽** |
| 10:52:05.854 | — | `nwy_vfs_fwrite fail: expect=1400, actual=0` | 写入返回 0 |
| 10:52:05.869 | — | `+FILEFTPGET: File Write Fail` | 上报失败 |
| 10:52:05.899 | — | `+FTPLOGIN: DATA SETUP ERROR` | FTP 会话异常 |

---

## 4. 根因分析（5-Why）

```
Why 1: 为什么 +FILEFTPGET 返回 File Write Fail？
  → 因为 nwy_ftp_file_write() 函数返回了 -1

Why 2: 为什么 nwy_ftp_file_write() 返回 -1？
  → 因为 nwy_vfs_fwrite() 返回 0（期望 1400，实际 0）
     代码逻辑：if (ret != len) return -1
     位置：nwy_app_at_func_ftp.c:287-289

Why 3: 为什么 nwy_vfs_fwrite() 返回 0？
  → 因为底层 LFS 文件系统无法分配新块

Why 4: 为什么 LFS 无法分配新块？
  → 因为文件系统已无可用空间
     证据：LFS:lfs.c:608:error: No more free space 307

Why 5: 为什么文件系统空间耗尽？
  → 根本原因：Flash 存储空间不足以容纳下载的文件
     - 481 × 1400 = 673,400 字节后空间耗尽
     - 009988.gz 文件大小超过可用 Flash 空间
```

**根本原因：Flash 文件系统（LFS）存储空间不足，导致 FTP 下载文件无法完整写入。**

---

## 5. 代码交叉验证

### 5.1 关键函数调用链

```
AT+FILEFTPGET 命令
  → nwy_app_at_func_ftp.c: nwy_app_at_fileftpget_func()  [AT命令入口]
    → nwy_app_at_func_ftp.c: nwy_ftp_rsp_cb()             [FTP数据接收回调]
      → nwy_app_at_func_ftp.c: nwy_ftp_file_write()       [文件写入封装]
        → nwy_app_at_platform.c: nwy_vfs_fopen_ex()        [打开文件]
        → nwy_app_at_platform.c: nwy_vfs_fseek()           [定位写入位置]
        → nwy_app_at_platform.c: nwy_vfs_fwrite()          [实际写入]
        → nwy_app_at_platform.c: nwy_vfs_fclose()          [关闭文件]
```

### 5.2 关键代码逻辑（nwy_app_at_func_ftp.c:276-294）

```c
int nwy_ftp_file_write(nwy_ftp_fileinfo *pFileFtp, unsigned char* data, unsigned int len)
{
  int fs = nwy_vfs_fopen_ex(pFileFtp->locname, NWY_FS_AW);
  if (fs < 0) {
      NWY_APP_LOG_ERROR("%s open %s fail", __func__, pFileFtp->locname, 0);
      return -1;
  }

  nwy_vfs_fseek(fs, pFileFtp->pos, 0);
  int ret = nwy_vfs_fwrite(fs, (char*)data, len);
  nwy_vfs_fclose(fs);
  if (ret != len) {                          // ← 写入返回值 != 期望长度
      NWY_APP_LOG_ERROR("nwy_vfs_fwrite fail: expect=%d, actual=%d", len, ret, 0);
      return -1;                             // ← 返回 -1，触发 File Write Fail
  }
  pFileFtp->pos += len;
  NWY_APP_LOG_HIGH("nwy_vfs_fwrite ret = %d", ret, 0, 0);
  return 0;
}
```

**注意**：`nwy_vfs_fopen_ex` 打开文件成功了（因为 `nwy_vfs_fopen_ex` 是追加模式打开已有文件），但 `nwy_vfs_fwrite` 写入时因空间不足返回 0。

### 5.3 错误上报逻辑（nwy_app_at_func_ftp.c:462-466）

```c
if (nwy_ftp_file_write(&ftp_file_info, (unsigned char *)recv_ptr->data, recv_ptr->data_size) != 0)
{
    nwy_app_at_unsol_str(at_channel, "\r\n+FILEFTPGET:" PADDING "File Write Fail\r\n");
    nwy_app_ftp_abort(cid);    // 中止FTP传输
    break;
}
```

---

## 6. 结论

### 6.1 现象是否正常？

**此现象是正常的错误处理行为，但反映了存储空间不足的问题。**

- `+FILEFTPGET: File Write Fail` 是代码在 Flash 空间不足时的**正常错误上报**
- 代码逻辑正确：检测到写入失败 → 上报错误 → 中止 FTP → 避免数据损坏
- **真正需要解决的是：Flash 存储空间不足的问题**

### 6.2 问题总结

| 项目 | 说明 |
|------|------|
| **直接原因** | `nwy_vfs_fwrite` 返回 0，LFS 报 `No more free space 307` |
| **根本原因** | Flash 文件系统可用空间不足以容纳 FTP 下载的文件 |
| **影响范围** | 所有 FTP 下载操作，文件大小超过剩余 Flash 空间时均会触发 |
| **错误上报** | 正确上报 `File Write Fail` 并中止 FTP 会话 |

---

## 7. 解决建议

### 7.1 短期方案

1. **下载前检查可用空间**：在执行 `AT+FILEFTPGET` 前使用 `AT+FSFAT` 或相关命令检查剩余 Flash 空间，确保有足够空间存放目标文件
2. **清理无用文件**：删除 `/nwy/` 目录下不再需要的文件释放空间
3. **确认分区大小**：检查 LFS 分区配置，确认用户文件系统分区是否足够大

### 7.2 长期方案

1. **优化 Flash 分区布局**：调整 `ASR1605_SINGLE_FLASH_LAYOUT.json` 中的分区分配，增大用户数据区
2. **添加空间预检查**：在 FTP 下载流程中，先查询远程文件大小，再与本地可用空间对比，空间不足时提前返回明确错误码（如 `No Space`），而非等到写入失败
3. **增加日志**：在 `nwy_ftp_file_write` 失败时打印当前剩余空间信息，方便后续排查

### 7.3 验证步骤

1. 执行 `AT+FSFAT` 查看当前 Flash 使用情况
2. 删除 `/nwy/` 下的临时文件
3. 重新尝试 `AT+FILEFTPGET="009988.gz"` 确认是否恢复正常
4. 如需下载大文件，确认 Flash 分区大小是否满足需求

---

## 8. 相关文件

| 文件 | 说明 |
|------|------|
| `pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_ftp.c:276-294` | `nwy_ftp_file_write()` 文件写入函数 |
| `pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_ftp.c:462-466` | File Write Fail 错误上报 |
| `pcac/NWY_FRAMEWORK/nwy_app_at_proc/platform/asr1603/nwy_app_at_platform.c` | VFS 文件操作封装 |
| `AbootTool/configurations/releasepack-asr1605-source/config/partition/ASR1605_SINGLE_FLASH_LAYOUT.json` | Flash 分区配置 |