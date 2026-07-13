# 电信 CTWING AEP 平台接入相关命令（第 14 章）

> 来源：N706 AT 命令手册 · 第 14 章 · 深圳市有方科技股份有限公司。
> 本文档为指令集 markdown 整理版，供 CTWing/CTM2M 相关缺陷分析与方案设计参照。

---

## 目录

- [14.1 AT+CTM2MVER – 查询模组版本](#141-atctm2mver--查询模组版本)
- [14.2 AT+CTM2MINIT – 初始化接入物联网开放平台能力](#142-atctm2minit--初始化接入物联网开放平台能力)
- [14.3 AT+CTM2MREG – 登录物联网开放平台](#143-atctm2mreg--登录物联网开放平台)
- [14.4 AT+CTM2MUPDATE – 更新 BINDING MODE 模式](#144-atctm2mupdate--更新-binding-mode-模式)
- [14.5 AT+CTM2MDEREG – 登出物联网开放平台](#145-atctm2mdereg--登出物联网开放平台)
- [14.6 AT+CTM2MSEND – 发送业务数据到物联网开放平台](#146-atctm2msend--发送业务数据到物联网开放平台)
- [14.7 +CTM2MRECV – 接收到物联网开放平台下发的数据](#147-ctm2mrecv--接收到物联网开放平台下发的数据)
- [14.8 AT+CTM2MRMODE – 设置收到下行数据后的指示方式](#148-atctm2mrmode--设置收到下行数据后的指示方式)
- [14.9 AT+CTM2MREAD – 读取缓存的下行数据](#149-atctm2read--读取缓存的下行数据)
- [14.10 +CTM2M – 上报指令处理结果通知及其它通知](#1410-ctm2m--上报指令处理结果通知及其它通知)

---

## 14.1 AT+CTM2MVER – 查询模组版本

查询模组接入物联网开放平台软件版本。

### 命令格式

| 类型 | 命令 | 响应格式 |
|------|------|----------|
| 查询 | `AT+CTM2MVER?<CR>` | `<CR><LF>+CTM2MVER:<lwm2m>,<ctm2m>,<chip>,<sv><CR><LF><CR><LF>OK<CR><LF>` |

**响应时间**：最大 300ms。

### 参数

| 参数 | 说明 |
|------|------|
| `<lwm2m>` | LWM2M 协议版本，固定为 `1.0`。 |
| `<ctm2m>` | CTM2M 协议版本号，固定为 `1.2.0`。 |
| `<chip>` | 模组使用的芯片类型，字符串类型。 |
| `<sv>` | 模组软件版本号，字符串类型。 |

### 示例

```
AT+CTM2MVER?
+CTM2MVER:1.0,1.2.0,RDA8908,N21_RDD0CM_BZ_V005
OK
```

---

## 14.2 AT+CTM2MINIT – 初始化接入物联网开放平台能力

设置模组接入物联网开放平台相关参数。**该命令设置后掉电保存。**

### 命令格式

| 类型 | 命令 | 响应格式 |
|------|------|----------|
| 执行 | `AT+CTM2MINIT=<Sever_IP>,<Port>,<lifetime>,<Binding Mode>,<Securitymode>[,(<PSKID>,<PSK>)]<CR>` | `<CR><LF>OK<CR><LF>` 或 `<CR><LF>+CTM2M ERROR:<err><CR><LF>` |
| 查询 | `AT+CTM2MINIT?<CR>` | `<CR><LF>+CTM2MINIT:<Sever_IP>,<Port>,<lifetime>,<Binding Mode>,<Securitymode>[,(<PSKID>,<PSK>)]<CR><LF><CR><LF>OK<CR><LF>` |

**响应时间**：最大 300ms。

### 参数

| 参数 | 说明 |
|------|------|
| `<Sever_IP>` | LWM2M 服务器 IP 地址。 |
| `<Port>` | LWM2M Server 端口号。 |
| `<Lifetime>` | 模组向中国电信物联网开放平台发送 register update 的时间间隔（秒）；必须 ≥ 300，否则返回错误（“参数值错误”）。 |
| `<Binding Mode>` | 整形：`0` – U，`1` – UQ。 |
| `<Securitymode>` | 是否加密：`0`-NoSec mode；`1`-Pre-Shared Keys(TLS_PSK_WITH_AES_128_CCM_8)；`2`-PreShared Keys(TLS_PSK_WITH_AES_128_CBC_SHA256)。 |
| `<PSKID>` | `<Securitymode>=1,2` 时必须提供。 |
| `<PSK>` | `<Securitymode>=1,2` 时必须提供。 |
| `<err>` | 错误码，见下表。 |

**`<err>` 错误码**

| 值 | 说明 |
|----|------|
| 1 | 其它错误 |
| 2 | 参数数量错误 |
| 3 | 参数值错误 |
| 5 | `<Securitymode>` 不支持 |
| 32 | 网络存在问题或对应的 AT engine 为空 |

### 示例

```
AT+CTM2MINIT="221.229.214.202",5683,3600,0,0
OK

AT+CTM2MINIT?
+CTM2MINIT: 221.229.214.202,5683,3600,0,0
OK
```

> `<Sever_IP>`、`<PSKID>`、`<PSK>` 参数在使用时需要添加引号。

---

## 14.3 AT+CTM2MREG – 登录物联网开放平台

登录中国电信物联网开放平台。

### 命令格式

| 类型 | 命令 | 响应格式 |
|------|------|----------|
| 执行 | `AT+CTM2MREG=[<Lifetime>]<CR>` | `<CR><LF>OK<CR><LF><CR><LF>+CTM2M:<operation>,<status code><CR><LF>` 或 `<CR><LF>+CTM2M ERROR:<err><CR><LF>` |
| 查询 | `AT+CTM2MREG?<CR>` | `<CR><LF>+CTM2M:<state><CR><LF><CR><LF>OK<CR><LF>` |

**响应时间**：最大 300ms。

### 参数

| 参数 | 说明 |
|------|------|
| `<Lifetime>` | register update 时间间隔（秒）；必须 ≥ 300，否则返回错误（“参数值错误”）。 |
| `<operation>` | `reg` 表示登录结果指示；`obsrv` 表示设备登录后观察 19/0/0 的结果指示。 |
| `<status code>` | 描述结果，见下表。 |
| `<state>` | 当前登录状态，见下表。 |
| `<err>` | 错误码，见下表。 |

**`<status code>`（operation = `reg`）**

| 值 | 说明 |
|----|------|
| 0 | 登录成功（平台返回 2.01 Created） |
| 1 | 超时后没有响应 |
| 2 | 不发送注册消息 |
| 10 | 端点名称无法识别或参数错误（平台返回 4.00 错误请求） |
| 13 | 身份验证失败，服务器拒绝访问（平台返回 4.03 禁止） |
| 22 | 物联网协议或 LwM2M 版本不支持（平台返回 4.12 前提条件失败） |

**`<status code>`（operation = `obsrv`）**

| 值 | 说明 |
|----|------|
| 0 | 设备接收到平台 Observe 19/0/0 命令，且发送 Observe 响应成功（CON 消息） |
| 1 | 设备接收到 Observe 19/0/0 命令，但未发送/未能发送 Observe 响应 |
| 2 | 在 19/0/0 收到取消观察通知，在本地删除记录的订阅 ID |

**`<state>` 当前登录状态（`AT+CTM2MREG?` 查询返回）**

| 值 | 状态 | 说明 |
|----|------|------|
| 0 | 未登录 | — |
| 1 | 登录中 | — |
| 2 | 已登录未 Observe | 未收到 Observe 19/0/0，**不能发送数据**，可接收数据 |
| 3 | 已登录已 Observe | 收到 Observe 19/0/0，**可以发送数据**，可接收数据 |
| 4 | 取消登录中 | — |

> ⚠️ 注意：权威定义中**没有“挂起”状态**。状态值 0/1/2/3/4 的语义如上固定。

**`<err>` 错误码**

| 值 | 说明 |
|----|------|
| 1 | 其它错误 |
| 2 | 参数数量错误 |
| 3 | 参数值错误 |
| 8 | 物联网开放平台连接参数未初始化 |
| 16 | 无法获取 IMSI（如未插 SIM 卡） |
| 32 | 网络存在问题或对应的 AT engine 为空 |
| 33 | 不能发出此指令（终端只有处于“未登录”状态时，才能发出登录请求） |
| 34 | 创建 LWM2M 会话失败 |

### 示例

```
AT+CTM2MREG
OK
+CTM2M:reg,0
+CTM2M:obsrv,0        // 注册成功 / 订阅成功

AT+CTM2MREG?
+CTM2M:3
OK                    // 登录平台成功、订阅成功
```

---

## 14.4 AT+CTM2MUPDATE – 更新 BINDING MODE 模式

向中国电信物联网开放平台更新 Binding Mode 模式。

### 命令格式

| 类型 | 命令 | 响应格式 |
|------|------|----------|
| 执行 | `AT+CTM2MUPDATE=<Binding Mode><CR>` | `<CR><LF>+CTM2MUPDATE:<MsgID><CR><LF><CR><LF>OK<CR><LF><CR><LF>+CTM2M:update,<status code>,<MsgID><CR><LF>` 或 `<CR><LF>+CTM2M ERROR:<err><CR><LF>` |

**响应时间**：最大 300ms。

### 参数

| 参数 | 说明 |
|------|------|
| `<Binding Mode>` | `0`：U 模式；`1`：UQ 模式。 |
| `<MsgID>` | 本消息的 ID。 |
| `<status code>` | 更新绑定模式的结果，见下表。 |
| `<err>` | 错误码，见下表。 |

**`<status code>`（operation = `update`）**

| 值 | 说明 |
|----|------|
| 0 | 已成功更新（平台返回 2.01 Created） |
| 1 | 超时后没有响应 |
| 2 | 未发送消息 |
| 10 | 参数错误（平台返回 4.00 错误请求） |
| 13 | 身份验证失败，服务器拒绝访问（平台返回 4.03 Forbidden） |
| 14 | 设备未登录（平台返回 4.04 Not Found） |

**`<err>` 错误码**

| 值 | 说明 |
|----|------|
| 1 | 其它错误 |
| 2 | 参数数量错误 |
| 3 | 参数值错误 |
| 4 | 不能发出此指令 |

### 示例

```
AT+CTM2MUPDATE=0
+CTM2MUPDATE:6650
OK
+CTM2M:update,0,6650
```

---

## 14.5 AT+CTM2MDEREG – 登出物联网开放平台

取消登录中国电信物联网开放平台。

### 命令格式

| 类型 | 命令 | 响应格式 |
|------|------|----------|
| 执行 | `AT+CTM2MDEREG<CR>` | `<CR><LF>OK<CR><LF><CR><LF>+CTM2M:dereg,<status code>,<MsgID><CR><LF>` 或 `<CR><LF>+CTM2M ERROR:<err><CR><LF>` |

**响应时间**：最大 300ms。

### 参数

| 参数 | 说明 |
|------|------|
| `<status code>` | 从物联网平台注销的结果，见下表。 |
| `<err>` | 错误码，见下表。 |

**`<status code>`（operation = `dereg`）**

| 值 | 说明 |
|----|------|
| 0 | 注销平台成功（平台返回 2.02 Deleted） |
| 1 | 超时后没有响应 |
| 2 | 未发送消息 |
| 10 | 未知原因失败（平台返回 4.00 错误请求） |
| 13 | 身份验证失败，服务器拒绝访问（平台返回 4.03 Forbidden） |
| 14 | 设备未登录（平台返回 4.04 Not Found） |

**`<err>` 错误码**

| 值 | 说明 |
|----|------|
| 1 | 其它错误 |
| 2 | 参数数量错误 |
| 3 | 参数值错误 |
| 4 | 不能发出此指令（终端处于“未登录”状态时，不能发出此请求） |

### 示例

```
AT+CTM2MDEREG
OK
+CTM2M:dereg,0
```

---

## 14.6 AT+CTM2MSEND – 发送业务数据到物联网开放平台

向中国电信物联网开放平台发送业务数据。

### 命令格式

| 类型 | 命令 | 响应格式 |
|------|------|----------|
| 执行 | `AT+CTM2MSEND=<Data>[,<mode>]<CR>` | `<CR><LF>+CTM2MSEND:<MsgID><CR><LF><CR><LF>OK<CR><LF><CR><LF>+CTM2M:send,<status code>,<MsgID><CR><LF>` 或 `<CR><LF>+CTM2M ERROR:<err><CR><LF>` |

**响应时间**：最大 300ms。

### 参数

| 参数 | 说明 |
|------|------|
| `<Data>` | 十六进制字符，发送到 CTWing 平台的数据。**数据长度不能超过 1000**。 |
| `<MsgID>` | 本消息的 ID。 |
| `<mode>` | 整数参数，`0` 表示 CON 模式，`1` 表示 NON 模式，默认值表示 CON 方式。 |
| `<status code>` | 向物联网平台发送数据的结果，见下表。 |
| `<err>` | 错误码，见下表。 |

**`<status code>`（operation = `send`）**

| 值 | 说明 |
|----|------|
| 0 | 成功发送数据。CON 模式下设备接收到 ACK 后成功；NON 模式下设备发送消息之后即成功 |
| 1 | 超时后没有响应（CON 模式下有效，即使多次重传也未收到 ACK） |
| 2 | **未发送消息** |
| 9 | 平台无法处理报告的数据（平台返回 RST 消息） |
| 11 | 其他错误，处理失败 |

**`<err>` 错误码**

| 值 | 说明 |
|----|------|
| 1 | 其它错误 |
| 2 | 参数数量错误 |
| 3 | 参数值错误 |
| 4 | 不能发出此指令（终端处于“未登录”、“登录中”、“取消登录中”状态时不能发送数据） |
| 13 | Data 字段解码失败 |
| 14 | Data 字段长度超过上限 |
| 15 | 平台未准备好接收数据（终端处于“已登录未 Observe”状态时不能发送数据） |
| 17 | Data 字段长度不是偶数 |

### 示例

```
AT+CTM2MSEND=02000400020805
+CTM2MSEND:29656
OK
+CTM2M:send,0,29656
```

---

## 14.7 +CTM2MRECV – 接收到物联网开放平台下发的数据

模组使用该命令通知终端接收中国电信物联网开放平台下发的数据。

### 命令格式

| 类型 | 命令 |
|------|------|
| 主动上报 | `+CTM2MRECV:<Data><CR><LF>` |

**响应时间**：无。

### 参数

| 参数 | 说明 |
|------|------|
| `<Data>` | 接收到的下行数据。 |

### 示例

```
+CTM2MRECV:31323334353637383930
```

---

## 14.8 AT+CTM2MRMODE – 设置收到下行数据后的指示方式

设置收到下行数据后的指示方式。

- `0`：收到下行数据后不显示，缓存起来，可通过 `AT+CTM2MREAD` 读取；UE 最多缓存 8 条，超出后最早的数据先被丢弃。
- `1`：收到下行数据后立刻显示，格式为 `+CTM2MRECV:<data>`。
- `2`：UE 缓存收到的下行数据，显示 `+CTM2MRECV`，可通过 `AT+CTM2MREAD` 读取；最多缓存 8 条，超出后最早数据先被丢弃。

**默认值为 1，模块重启后自动还原为默认值。**

### 命令格式

| 类型 | 命令 | 响应格式 |
|------|------|----------|
| 设置 | `AT+CTM2MRMODE=<mode><CR>` | `<CR><LF>OK<CR><LF>` 或 `<CR><LF>+CTM2M ERROR:<err><CR><LF>` |
| 查询 | `AT+CTM2MRMODE?<CR>` | `<CR><LF>+CTM2MRMODE:<mode><CR><LF><CR><LF>OK<CR><LF>` |
| 测试 | `AT+CTM2MRMODE=?<CR>` | `<CR><LF>+CTM2MRMODE:(0-2)<CR><LF><CR><LF>OK<CR><LF>` |

**响应时间**：最大 300ms。

### 参数

| 参数 | 说明 |
|------|------|
| `<mode>` | 新消息指示状态：`0` 无指示；`1` 指示和消息（默认值）；`2` 仅指示。 |
| `<err>` | 错误码，见下表。 |

**`<err>` 错误码**

| 值 | 说明 |
|----|------|
| 1 | 其它错误 |
| 2 | 参数数量错误 |
| 3 | 参数值错误 |
| 8 | 物联网开放平台连接参数未初始化 |
| 16 | 无法获取 IMSI（如未插 SIM 卡） |
| 32 | 网络存在问题或对应的 AT engine 为空 |
| 33 | 不能发出此指令（终端只有处于“未登录”状态时，才能发出登录请求） |
| 34 | 创建 LWM2M 会话失败 |

### 示例

```
AT+CTM2MRMODE?
+CTM2MRMODE: 2
OK

AT+CTM2MRMODE=?
+CTM2MRMODE:(0-2)
OK

AT+CTM2MRMODE=1
OK
```

---

## 14.9 AT+CTM2MREAD – 读取缓存的下行数据

读取 UE 缓存的下行数据。每次返回最先缓存的消息并从缓存中删除；若没有缓存消息，直接返回 OK；若设置了 `AT+CTM2MRMODE=1`，该指令直接返回 OK。

### 命令格式

| 类型 | 命令 | 响应格式 |
|------|------|----------|
| 执行 | `AT+CTM2MREAD<CR>` | `<CR><LF>OK<CR><LF>` 或 `<CR><LF><data_len><data><CR><LF><CR><LF>OK<CR><LF>` 或 `<CR><LF>+CTM2M ERROR:<err><CR><LF>` |

**响应时间**：最大 300ms。

### 参数

| 参数 | 说明 |
|------|------|
| `<data>` | 十六进制字符，缓存的下行数据。 |
| `<data_len>` | 整型，数据长度。 |
| `<err>` | 错误码，见下表。 |

**`<err>` 错误码**

| 值 | 说明 |
|----|------|
| 1 | 其它错误 |
| 2 | 参数数量错误 |
| 3 | 参数值错误 |
| 8 | 物联网开放平台连接参数未初始化 |
| 16 | 无法获取 IMSI（如未插 SIM 卡） |
| 32 | 网络存在问题或对应的 AT engine 为空 |
| 33 | 不能发出此指令（终端只有处于“未登录”状态时，才能发出登录请求） |
| 34 | 创建 LWM2M 会话失败 |

### 示例

```
AT+CTM2MREAD
5,3132333435
OK                    // 读取到缓存的数据

AT+CTM2MREAD
OK                    // 没有读取到缓存的数据
```

---

## 14.10 +CTM2M – 上报指令处理结果通知及其它通知

模组主动向终端上报指令处理的结果通知信息及其它通知信息。

### 命令格式

| 类型 | 命令 |
|------|------|
| 主动上报 | `<CR><LF>+CTM2M:<Operation>,<status code>,[<data1>,<data2>,<data3>]<CR><LF>` |

**响应时间**：最大 300ms。

### `<Operation>` 取值

| Operation | 含义 |
|-----------|------|
| `reg` | 登录物联网开放平台处理结果 |
| `obsrv` | 登录后模组收到对 19/0/0 的 Observe 的处理结果 |
| `update` | 更新物联网开放平台 Binding Mode 模式处理结果 |
| `ping` | 模组后台自动更新物联网开放平台 lifetime 处理结果通知 |
| `dereg` | 取消登录物联网开放平台处理结果 |
| `send` | 发送业务数据到物联网开放平台处理结果 |
| `lwstatus` | 模组发出 lwm2m session 的 status 状态变化通知 |

### 各 Operation 的 `<status code>`

**`reg`**：`0` 登录成功 / `1` 超时无响应 / `2` 未发出报文 / `10` Endpoint Name 无法识别或参数错误 / `13` 鉴权失败 Server 拒绝接入 / `22` IOT Protocol 或 LWM2M 版本不支持

**`obsrv`**：`0` 成功收到 19/0/0 的 Observe 通知 / `1` 接收到但处理失败 / `2` 收到 Cancel Observe 通知，删除本地订阅

**`update`**：`0` 更新成功 / `1` 超时无响应 / `2` 未发出报文 / `10` 参数错误 / `13` 鉴权失败 Server 拒绝接入 / `14` 终端未登录（URI 错误）；`<data1>` = 更新 Binding Mode 报文的 MsgID

**`ping`**：`0` 延长 Lifetime 成功 / `1` 超时无响应 / `10` 参数错误 / `13` 鉴权失败 Server 拒绝接入 / `14` 终端未登录（URI 错误）

**`dereg`**：`0` 取消登录成功 / `1` 超时无响应 / `2` 未发出报文 / `10` 未明原因失败 / `13` 鉴权失败 Server 拒绝接入 / `14` 终端未登录（URI 错误）

**`send`**：`0` 上报平台成功 / `1` 超时无响应 / `2` 未发出报文 / `9` 平台不能处理上报数据 / `11` 其它错误处理失败；`<data1>` = 上报数据报文的 MsgID

**`lwstatus`（lwm2m session 状态变化通知）**

| 值 | 说明 |
|----|------|
| `0` | lwm2m session 已经失效（**用户应重新登录物联网开放平台**） |
| `1` | lwm2m session 恢复成功 |
| `2` | lwm2m session 开始恢复 |

### 关键 URC 与用户处置指引

| 上报通知 | 用户处置 |
|----------|----------|
| `+CTM2M: update,13` / `update,14` | 重新使用 `AT+CTM2MREG` 登录平台 |
| `+CTM2M: ping,1` / `ping,10` / `ping,13` / `ping,14` | 重新使用 `AT+CTM2MREG` 登录平台 |
| `+CTM2M: dereg,10` / `dereg,13` / `dereg,14` | 重新使用 `AT+CTM2MREG` 登录平台 |
| `+CTM2M: reg,1` / `reg,2` | 重新使用 `AT+CTM2MREG` 登录平台 |
| `+CTM2M: update,1` / `update,2` | 继续重试 `AT+CTM2MUPDATE` |
| `+CTM2M: dereg,1` / `dereg,2` | 可重试 `AT+CTM2MDEREG` |
| `+CTM2M: lwstatus,0` | **重新登录物联网开放平台** |
| `+CTM2M: obsrv,1` / `obsrv,2` | 若要发送数据，应先 `AT+CTM2MDEREG` 再重新登录 |
| `+CTM2M: send,9,<MsgID>` | （平台返回 RST，数据未被处理） |

---

## 附：状态机与 send 权限速查

| `<state>` | 状态名 | 能否 `CTM2MSEND` | 能否接收 |
|-----------|--------|------------------|----------|
| 0 | 未登录 | ❌ | — |
| 1 | 登录中 | ❌ | — |
| 2 | 已登录未 Observe | ❌（err 15） | ✅ |
| 3 | 已登录已 Observe | ✅ | ✅ |
| 4 | 取消登录中 | ❌ | — |

**`AT+CTM2MSEND` 仅在 `state==3`（已登录已 Observe）时允许发送**；其它状态返回错误（`state==2` 返回 err 15；`0/1/4` 返回 err 4）。
