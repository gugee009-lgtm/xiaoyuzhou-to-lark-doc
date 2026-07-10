# 小宇宙转飞书文档

这是一个 Codex skill，用来把公开的小宇宙播客单集转换成飞书文档。

内置脚本会解析单集音频地址，下载音频，上传到飞书云空间，生成飞书妙记，读取逐字稿和 AI 产物，最后创建一份包含节目元信息、摘要、分段大纲和校对后 ASR 原文的飞书文档。

仓库地址：[https://github.com/GOUGOUW09/xiaoyuzhou-to-lark-doc](https://github.com/GOUGOUW09/xiaoyuzhou-to-lark-doc)

## 安装

把这个文件夹复制到 Codex 的 skills 目录：

```sh
mkdir -p ~/.codex/skills
cp -R xiaoyuzhou-to-lark-doc ~/.codex/skills/
```

重启 Codex 或新开一个线程后，可以这样调用：

```text
用 $xiaoyuzhou-to-lark-doc 把这个小宇宙链接转成飞书文档：https://...
```

## 使用前准备

- 安装 Python 3
- 安装并配置 `lark-cli`
- 完成飞书应用配置和用户登录
- 飞书应用和用户授权需要覆盖文件上传、妙记上传/读取、文档创建/更新等权限

读取飞书妙记 AI 产物时，通常需要先授权：

```sh
lark-cli auth login --scope "minutes:minutes:readonly minutes:minutes.artifacts:read"
```

## 直接运行脚本

```sh
python3 scripts/xiaoyuzhou_to_lark_doc.py "https://www.xiaoyuzhoufm.com/episode/..." \
  --workdir ./runs/<episode-id>
```

如果已经生成过飞书妙记，可以复用妙记 token，跳过下载、上传和生成妙记：

```sh
python3 scripts/xiaoyuzhou_to_lark_doc.py "https://www.xiaoyuzhoufm.com/episode/..." \
  --minute-token obcn_example_token \
  --workdir ./runs/<episode-id>
```

## 安全说明

只处理公开播客，或你有权处理的音频。不要绕过付费、登录限制或私有媒体权限。

不要提交 `runs/`、音频文件、逐字稿、二维码、本地飞书 token 或应用凭据。
