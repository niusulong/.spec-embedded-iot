# 通用协议官方资料库

存放跨平台的通用协议官方文档、规范、应用笔记。与 `platform/` 下按芯片平台隔离的项目知识库互补——这里放的是协议本身的标准资料，不绑定具体平台。

## 目录结构

| 目录 | 内容 |
|------|------|
| `uart/` | UART 协议规范、串口通信、波特率、流控 |
| `spi/` | SPI 协议规范、主从模式、时序、四线/QSPI |
| `i2c/` | I2C 协议规范、地址、多主、时序 |
| `mqtt/` | MQTT 协议规范、QoS、遗嘱、订阅模型 |
| `tcp/` | TCP 协议、RFC、拥塞控制、状态机 |
| `http/` | HTTP 协议、状态码、方法、HTTPS |
| `ftp/` | FTP 协议、主动/被动模式、命令集 |

## 文档命名建议

```
protocols/{协议名}/{主题}.md
protocols/mqtt/MQTT-3.1.1-协议规范.md
protocols/mqtt/MQTT-QoS机制详解.md
protocols/spi/SPI-时序与四种模式.md
```

## 与平台知识库的关系

- `platform/{平台}/bug-solutions/`：某平台遇到的具体 bug 及根因（如 EC626 的 MQTT 死机）
- `protocols/mqtt/`：MQTT 协议本身的通用规范（所有平台通用）

分析 bug 时可两者结合：先查平台 bug-solutions 看是否有现成案例，再查 protocols 理解协议本身。
