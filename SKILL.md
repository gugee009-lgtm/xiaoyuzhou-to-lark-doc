---
name: xiaoyuzhou-to-lark-doc
description: 将公开的小宇宙单集链接或播客音频直链转换成飞书文档，文档包含逐字稿、校对后的 ASR 原文、摘要、分段大纲和来源信息。用户要求处理小宇宙、xiaoyuzhoufm.com、播客链接、提取播客音频、ASR 转写、飞书妙记，或把播客生成飞书文档时使用。
---

# 小宇宙转飞书文档

使用这个 skill 将公开的小宇宙单集转换成飞书文档。内置脚本会解析音频地址，下载音频，上传到飞书云空间，生成飞书妙记，读取逐字稿和 AI 产物，并创建包含节目元信息、摘要、分段大纲和校对后 ASR 原文的飞书文档。

## 主要流程

1. 默认使用内置脚本，除非用户明确要求采用其他实现。
2. 在一个可写目录中运行脚本。
3. 将用户提供的小宇宙单集链接作为第一个参数传入。
4. 使用 `--workdir` 保存单集相关的中间文件。目录名优先使用 episode id。
5. 如果之前已经生成过飞书妙记，使用 `--minute-token` 或 `--minute-url` 跳过下载、上传和生成妙记。

```bash
python3 ~/.codex/skills/xiaoyuzhou-to-lark-doc/scripts/xiaoyuzhou_to_lark_doc.py \
  "https://www.xiaoyuzhoufm.com/episode/..." \
  --workdir ./runs/<episode-id>
```

复用已生成的飞书妙记：

```bash
python3 ~/.codex/skills/xiaoyuzhou-to-lark-doc/scripts/xiaoyuzhou_to_lark_doc.py \
  "https://www.xiaoyuzhoufm.com/episode/..." \
  --minute-token obcn_example_token \
  --workdir ./runs/<episode-id>
```

## 输入

- 必填：小宇宙单集链接、音频直链，或其他能暴露音频元数据的公开播客单集链接。
- 可选 `--rss`：页面解析不到音频时，用 RSS 链接回退匹配单集。
- 可选 `--glossary`：ASR 术语/错词修正表，支持 `错词=>正确词`、`错词=正确词`、JSON 对象，或 `{ "from": "...", "to": "..." }` 数组。
- 可选 `--parent-token` 或 `--parent-position`：指定飞书文档创建位置。
- 可选 `--insecure`：仅在本机 Python 证书链无法校验公开小宇宙 HTTPS 请求时使用。

## 飞书要求

脚本依赖 `lark-cli` 和用户身份。如果缺少 scope，按照 CLI 提示补授权；作为 agent 操作时使用分段授权流程。

常见必需权限：

```bash
lark-cli auth login --scope "minutes:minutes:readonly minutes:minutes.artifacts:read"
```

如果 `vc +notes` 返回 `authorization` / `missing_scope`，不要继续轮询。请用户完成授权后，再用 `--minute-token` 复用已生成的妙记，避免重复上传音频。

脚本默认会在下载和上传前检查这些 scope。除非用户明确只想生成飞书原生妙记，或正在调试权限问题，否则不要传 `--skip-auth-check`。

## 行为说明

- 尊重版权和访问边界。只处理公开单集或用户有权处理的音频。不要绕过付费、登录限制或私有媒体权限。
- 用户先收到的飞书文档可能是飞书妙记自动生成的原生文档。脚本后续还会读取 artifacts，并创建一份整理后的文档。
- 长逐字稿会通过 `docs +update append` 分块追加，不要改成一次性创建超长文档。
- 脚本创建妙记后会写入 `state.json`，其中包含 `minute_url`、`minute_token` 和 `file_token`。失败后优先复用这些信息。

## 校验

修改内置脚本后，运行：

```bash
python3 -m py_compile ~/.codex/skills/xiaoyuzhou-to-lark-doc/scripts/xiaoyuzhou_to_lark_doc.py
```

如果有开发仓库，可以在开发仓库中运行解析和回归测试。
