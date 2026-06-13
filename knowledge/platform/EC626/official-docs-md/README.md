# 平台官方文档 - Markdown 转换版

存放 `official-docs/` 原始文档转换后的 `.md` 格式版本，供语义检索/RAG 使用。

## 目录结构（镜像 official-docs/）

| 目录 | 来源 | 说明 |
|------|------|------|
| `cn/` | `official-docs/cn/*.pdf` | 中文应用笔记转换 |
| `en/` | `official-docs/en/*.pdf` | 英文应用笔记转换 |
| `EC626_Driver_API_Reference_Manual/` | `official-docs/EC626_Driver_API_Reference_Manual/*.html` | API 参考手册转换 |

## 命名约定

- 与原始文档同名，扩展名改 `.md`
- 例：`AN0003- EC NB-IoT COAP应用笔记_V1.2.pdf` → `AN0003-EC NB-IoT COAP应用笔记_V1.2.md`

## 转换方式

- **PDF → MD**：推荐 `marker`、`markitdown`(微软)、或 `pymupdf4llm`
- **HTML → MD**：`pandoc` 或 `html2text`

```bash
# 示例：单个 PDF 转换
markitdown "official-docs/cn/AN0003- EC NB-IoT COAP应用笔记_V1.2.pdf" > "official-docs-md/cn/AN0003-COAP应用笔记.md"
```

## 转换后用途

转换后的 `.md` 可被向量索引（全文切分），实现"查 EC626 官方 MQTT 应用笔记里的 QoS 说明"这类语义检索。
