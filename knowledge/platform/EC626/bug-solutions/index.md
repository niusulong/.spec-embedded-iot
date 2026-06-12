# Bug 解决方案索引 - EC626

> 自动生成于 2026-06-12 15:17，共 20 条

| # | 模块 | 症状关键词 | 根因方向 | 文件 |
|---|------|-----------|---------|------|
| 1 | CTM2M/LWM2M | 定时器未停止, xQueueSend失败, pending队列溢出, 守护定时器超时 | AT+CTM2MREG异步命令中xQueueSend返回值未检查，消息入队失败时定时器回调永不触发导致定时器直到超... | [AT_CTM2MREG定时器未停止](AT_CTM2MREG定时器未停止.md) |
| 2 | CoAP/libcoap/堆内存 | COAP死机, 堆内存泄漏, OPTLIST泄漏, LL_SORT bug, pvPortMallocEC返回NULL, ASSERT死机, COAPCLOSE循环, 内存持续下降 | COAP客户端在COAPCLOSE→COAPOPEN循环中存在heap内存泄漏，OPTLIST（t=12）100%... | [COAP协议持续GET操作时模组死机](COAP协议持续GET操作时模组死机.md) |
| 3 | PS/LWIP | CeuTask ASSERT, PsifSuspendInd, Bearer Resume, EC_ASSERT, 协议栈崩溃 | PS协议栈Bearer Resume流程中CedrProcBearerResumeIndSig调用PsifSusp... | [CeuTask_ASSERT_PsifSuspendInd](CeuTask_ASSERT_PsifSuspendInd.md) |
| 4 | CoAP | COAPOPTION ERROR, Uri-Port, strlen校验, 端口号被拒绝, 选项值校验 | coap_config_client函数中COAP_OPTION_URI_PORT分支使用strlen校验十进制字... | [CoAPOPTION返回ERROR](CoAPOPTION返回ERROR.md) |
| 5 | DNS | 无效IP地址, 返回OK, NVM写入, inet_addr未校验 | nwy_dss_inet_aton()函数调用inet_addr()解析IP地址后未检查返回值是否为INADDR_... | [DNSSERVER无效地址校验缺失](DNSSERVER无效地址校验缺失.md) |
| 6 | LWIP/DNS | DNSSERVER, dns2写入dns1, index参数未使用, NVM覆盖, 垃圾数据 | nwy_app_set_dns_server函数接收index参数但从未使用，始终将IP写入ipv4Dns[0]，... | [DNSSERVER设置dns2实际写入dns1](DNSSERVER设置dns2实际写入dns1.md) |
| 7 | LWM2M | AT命令ERROR, handler未找到, 宏未启用, LWM2MCREATE | FEATURE_WAKAAMA_ENABLE宏在CFLAGS中被注释掉，导致AT命令表中未注册+LWM2MCREA... | [LWM2MCREATE_AT命令ERROR](LWM2MCREATE_AT命令ERROR.md) |
| 8 | CoAP/LWM2M | REGISTER TIMEOUT, DTLS, PSK, 加密连接超时, 握手失败 | DTLS PSK加密连接时，connection_create()中DTLS握手在后续connection_sen... | [LWM2M加密连接REGISTER TIMEOUT](LWM2M加密连接REGISTER TIMEOUT.md) |
| 9 | MQTT | Assert崩溃, SSL双向认证, 内存不足, ECC运算, mqttSend任务 | SSL双向认证握手过程中ECC椭圆曲线密码运算需要大量动态内存，系统剩余内存仅约1KB，pvPortMallocE... | [MQTT_SSL双向认证内存分配崩溃](MQTT_SSL双向认证内存分配崩溃.md) |
| 10 | MQTT/SSL | ECMTCONN失败, SSL上下文丢失, 重复URC, 订阅ERROR | ECMTOPEN阶段建立的SSL连接状态未正确传递到ECMTCONN阶段，导致ECMTCONN重新建立TCP连接时... | [MQTT_SSL双向认证连接失败](MQTT_SSL双向认证连接失败.md) |
| 11 | MQTT | SSL连接成功, MQTTConnect失败, CONNACK未等待, 连接立即关闭 | mqttConnectWithResults()函数中等待CONNACK的代码被#if 0注释掉，导致发送CONN... | [MQTT_SSL连接成功但MQTTConnect失败](MQTT_SSL连接成功但MQTTConnect失败.md) |
| 12 | BLE | NWBLEPSTR返回ERROR, BLE芯片无响应, HCI超时, UART通信200ms超时 | 执行NWBLEPSTR前未执行AT+NWBTBLEPWR=1初始化BLE芯片（或初始化不完整），YC1323芯片未... | [NWBLEPSTR返回ERROR](NWBLEPSTR返回ERROR.md) |
| 13 | LWIP/TCP | RECVMODE=0, 收不到数据, TCP窗口归零, RST断连, 缓冲区暂停 | RECVMODE=0手动接收模式下应用层recv_buff缓冲区过小（2920字节），仅读取2包数据后即触发buf... | [TCP_RECVMODE0_收不到数据断连](TCP_RECVMODE0_收不到数据断连.md) |
| 14 | LWIP/TCP | XIIC=0去激活, 卡死死机, 三方死锁, type 9内存池耗尽, EC_ASSERT | AT+XIIC=0去激活时≥3个TCP socket关闭产生大量_lwip_sock_evt事件以portMAX_... | [TCP连接XIIC0去激活死机](TCP连接XIIC0去激活死机.md) |
| 15 | LWIP/PSIF | TCP连接, xiic去激活, 概率性死机, ASSERT, PsifSuspendInd | TCP连接活跃期间TCPIP_MSG_API池耗尽，xiic=0去激活时PSIF状态机未正确转换，协议栈在Psif... | [TCP连接后xiic0去激活概率性死机](TCP连接后xiic0去激活概率性死机.md) |
| 16 | UART/AT解码器 | too much pending AT input, 逐字节接收, pending节点溢出, Doze低功耗, 长数据AT命令 | UART长数据逐字节接收，每个字节触发独立中断和信号后系统立即进入Doze，pending队列节点快速累积超过上限... | [UART_RX_FIFO超时pending节点溢出](UART_RX_FIFO超时pending节点溢出.md) |
| 17 | UDP/Socket管理 | PSM退出后UDP断开, HIB Exit后DISCONNECT, 需重新建立连接, hibCheck未注册, socket上下文未保存 | 标准UDP AT指令(AT+UDPSETUP)使用ATSKT来源socket，未注册hibCheck回调导致hib... | [UDP连接PSM模式](UDP连接PSM模式.md) |
| 18 | UDP/LWIP/NWY框架 | COPS=2后UDP未关闭, CFUN=4后socket残留, UDPSETUP ERROR1, UDP链路未清理, bearer释放路径缺陷 | COPS=2/CFUN=4的bearer释放路径仅设置OOS标志位，未调用nwy_dsnet_status_adp... | [UDP链路未关闭](UDP链路未关闭.md) |
| 19 | LWIP | 卡死, AT不通, 去激活PPP, IPv6, UDP链路, 死锁 | AT+XIIC=0去激活流程在AT线程上同步持有互斥锁关闭所有UDP socket，_lwip_sock_evt回... | [ipv6_udp_ppp_crash](ipv6_udp_ppp_crash.md) |
| 20 | CoAP/LWIP/堆内存 | 长期挂测死机, LWIP内存池耗尽, 堆碎片化, CoAP发送ASSERT, pvPortMalloc返回NULL, DTLS内存消耗 | 长期挂测中LWIP网络资源(TCP_PCB/NETCONN/PBUF等17个memp池)累积泄漏至100%耗尽，同... | [长期挂测挂测900多次出现死机](长期挂测挂测900多次出现死机.md) |
