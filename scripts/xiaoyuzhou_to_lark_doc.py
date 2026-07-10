#!/usr/bin/env python3
"""将公开的小宇宙单集链接转换成飞书文档。"""

from __future__ import annotations

import argparse
import dataclasses
import html
import json
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".wav", ".wma", ".amr", ".mp4", ".m4v", ".mov"}
USER_AGENT = "Mozilla/5.0 (compatible; xiaoyuzhou-to-lark-doc/1.0)"
REQUIRED_LARK_SCOPES = "minutes:minutes:readonly minutes:minutes.artifacts:read"


@dataclasses.dataclass
class EpisodeInfo:
    input_url: str
    title: str = ""
    description: str = ""
    podcast_title: str = ""
    author: str = ""
    published_at: str = ""
    duration: str = ""
    episode_id: str = ""
    audio_url: str = ""
    audio_source: str = ""
    rss_url: str = ""


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.scripts: list[tuple[dict[str, str], str]] = []
        self._script_attrs: dict[str, str] | None = None
        self._script_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): v or "" for k, v in attrs}
        if tag == "meta":
            self.meta.append(attr_map)
        elif tag == "link":
            self.links.append(attr_map)
        elif tag == "script":
            self._script_attrs = attr_map
            self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._script_attrs is not None:
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_attrs is not None:
            self.scripts.append((self._script_attrs, "".join(self._script_chunks)))
            self._script_attrs = None
            self._script_chunks = []


class HttpClient:
    def __init__(self, insecure: bool = False, timeout: int = 30) -> None:
        self.timeout = timeout
        if insecure:
            self.context = ssl._create_unverified_context()
        else:
            self.context = self._default_context()

    @staticmethod
    def _default_context() -> ssl.SSLContext:
        try:
            import certifi  # type: ignore

            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl.create_default_context()

    def get_text(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout, context=self.context) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, "replace")

    def download(self, url: str, output_path: Path) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout, context=self.context) as response:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)


class LarkCliError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}

    @property
    def error_type(self) -> str:
        error = self.payload.get("error") if isinstance(self.payload, dict) else None
        return str(error.get("type") or "") if isinstance(error, dict) else ""

    @property
    def error_subtype(self) -> str:
        error = self.payload.get("error") if isinstance(self.payload, dict) else None
        return str(error.get("subtype") or "") if isinstance(error, dict) else ""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workdir = args.workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        client = HttpClient(insecure=args.insecure, timeout=args.timeout)
        info = resolve_episode(args.url, client, rss_url=args.rss)
        if not info.audio_url:
            raise RuntimeError("没有解析到音频地址。请补充 --rss 或音频直链。")

        print_json({"stage": "resolved", "episode": dataclasses.asdict(info)})
        if args.dry_run:
            return 0

        lark = LarkClient(cwd=workdir)
        if not args.skip_auth_check:
            lark.ensure_required_scopes(REQUIRED_LARK_SCOPES)
        audio_path: Path | None = None
        if args.minute_token or args.minute_url:
            minute_url = args.minute_url or args.minute_token
            minute_token = args.minute_token or extract_minute_token(args.minute_url)
            print_json({"stage": "using_existing_minute", "minute_token": minute_token, "minute_url": minute_url})
        else:
            audio_path = download_audio(client, info.audio_url, workdir, info.title or info.episode_id or "episode")
            print_json({"stage": "downloaded", "audio_path": str(audio_path)})

            file_token = lark.upload_file(audio_path)
            minute_url = lark.create_minute(file_token)
            minute_token = extract_minute_token(minute_url)
            write_state(workdir, {"minute_url": minute_url, "minute_token": minute_token, "file_token": file_token})
            print_json({"stage": "minute_created", "minute_token": minute_token, "minute_url": minute_url})
        notes = lark.poll_notes(minute_token, polls=args.polls, interval=args.poll_interval)

        transcript = read_transcript(notes, workdir)
        corrections = load_corrections(args.glossary) if args.glossary else {}
        corrected_transcript = correct_transcript(transcript, corrections)
        document_xml = build_document_xml(info, notes, minute_url)
        doc_url = lark.create_doc(document_xml, parent_token=args.parent_token, parent_position=args.parent_position)
        for chunk in transcript_xml_chunks(corrected_transcript, max_chars=args.append_chars):
            lark.append_doc(doc_url, chunk)

        result = {"stage": "done", "doc_url": doc_url, "minute_url": minute_url}
        if audio_path is not None:
            result["audio_path"] = str(audio_path)
        print_json(result)
        return 0
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将小宇宙单集转换成飞书文档。")
    parser.add_argument("url", help="小宇宙单集链接、RSS 单集链接，或音频直链")
    parser.add_argument("--rss", help="可选 RSS 链接，用于页面解析失败时回退")
    parser.add_argument("--glossary", type=Path, help="术语/错词修正表")
    parser.add_argument("--workdir", type=Path, default=Path("runs/default"), help="工作目录")
    parser.add_argument("--parent-token", help="飞书文档父文件夹或知识库节点 token")
    parser.add_argument("--parent-position", help="飞书文档父位置，如 my_library")
    parser.add_argument("--minute-token", help="复用已生成的妙记 token，跳过下载、上传和生成妙记")
    parser.add_argument("--minute-url", help="复用已生成的妙记链接，跳过下载、上传和生成妙记")
    parser.add_argument("--polls", type=int, default=20, help="等待妙记生成的轮询次数")
    parser.add_argument("--poll-interval", type=int, default=30, help="等待妙记生成的轮询间隔秒数")
    parser.add_argument("--append-chars", type=int, default=15000, help="每次追加到飞书文档的 XML 字符数上限")
    parser.add_argument("--timeout", type=int, default=30, help="网络请求超时秒数")
    parser.add_argument("--skip-auth-check", action="store_true", help="跳过前置飞书 scope 检查")
    parser.add_argument("--dry-run", action="store_true", help="只解析信息，不下载和调用飞书")
    parser.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验，仅在本机证书链异常时使用")
    return parser.parse_args(argv)


def resolve_episode(url: str, client: HttpClient, rss_url: str | None = None) -> EpisodeInfo:
    if looks_like_audio_url(url):
        return EpisodeInfo(input_url=url, title=Path(urllib.parse.urlparse(url).path).stem, audio_url=url, audio_source="direct")

    html_text = client.get_text(url)
    info = parse_episode_page(url, html_text)
    if info.audio_url:
        return info

    candidate_rss = rss_url or info.rss_url
    if candidate_rss:
        rss_info = parse_rss_episode(client.get_text(candidate_rss), url, info)
        rss_info.rss_url = candidate_rss
        return rss_info

    return info


def parse_episode_page(url: str, html_text: str) -> EpisodeInfo:
    parser = PageParser()
    parser.feed(html_text)
    info = EpisodeInfo(input_url=url, episode_id=episode_id_from_url(url))

    for meta in parser.meta:
        key = meta.get("property") or meta.get("name")
        content = meta.get("content", "")
        if key in {"og:title", "twitter:title"} and not info.title:
            info.title = content
        elif key in {"og:description", "description", "twitter:description"} and not info.description:
            info.description = content
        elif key in {"og:audio", "twitter:player:stream"} and is_probable_audio(content):
            info.audio_url = html.unescape(content)
            info.audio_source = key

    for link in parser.links:
        rel = link.get("rel", "").lower()
        typ = link.get("type", "").lower()
        href = link.get("href", "")
        if href and ("alternate" in rel or "rss" in typ) and ("rss" in typ or href.endswith(".xml")):
            info.rss_url = urllib.parse.urljoin(url, html.unescape(href))

    for attrs, body in parser.scripts:
        typ = attrs.get("type", "")
        script_id = attrs.get("id", "")
        if script_id == "__NEXT_DATA__" or typ == "application/json":
            merge_info_from_json(info, try_json(body), source="__NEXT_DATA__")
        elif typ == "application/ld+json":
            merge_info_from_json_ld(info, try_json(body))

    if not info.audio_url:
        candidate = first_audio_candidate_from_text(html_text)
        if candidate:
            info.audio_url = candidate
            info.audio_source = "html-regex"

    return info


def merge_info_from_json(info: EpisodeInfo, data: Any, source: str) -> None:
    episode = deep_find_episode(data)
    if not isinstance(episode, dict):
        return
    info.title = prefer_richer(info.title, str(episode.get("title") or ""))
    info.description = prefer_richer(info.description, str(episode.get("description") or ""))
    info.published_at = info.published_at or str(episode.get("pubDate") or episode.get("publishedAt") or "")
    info.duration = info.duration or str(episode.get("duration") or "")
    info.episode_id = info.episode_id or str(episode.get("eid") or episode.get("id") or "")
    podcast = episode.get("podcast")
    if isinstance(podcast, dict):
        info.podcast_title = info.podcast_title or str(podcast.get("title") or "")
        info.author = info.author or str(podcast.get("author") or "")
    for path in (
        ("enclosure", "url"),
        ("media", "source", "url"),
        ("media", "backupSource", "url"),
    ):
        value = nested_get(episode, path)
        if isinstance(value, str) and is_probable_audio(value):
            info.audio_url = info.audio_url or html.unescape(value)
            info.audio_source = info.audio_source or source + "." + ".".join(path)


def merge_info_from_json_ld(info: EpisodeInfo, data: Any) -> None:
    if isinstance(data, list):
        for item in data:
            merge_info_from_json_ld(info, item)
        return
    if not isinstance(data, dict):
        return
    info.title = info.title or str(data.get("name") or "")
    info.description = info.description or str(data.get("description") or "")
    info.published_at = info.published_at or str(data.get("datePublished") or "")
    info.duration = info.duration or str(data.get("timeRequired") or "")


def parse_rss_episode(xml_text: str, input_url: str, page_info: EpisodeInfo) -> EpisodeInfo:
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall(".//item")
    best = None
    for item in items:
        score = rss_match_score(item, input_url, page_info)
        if score > 0 and (best is None or score > best[0]):
            best = (score, item)
    if best is None and len(items) == 1:
        best = (1, items[0])
    if best is None:
        raise RuntimeError("RSS 中没有找到匹配的单集。")

    item = best[1]
    info = dataclasses.replace(page_info)
    info.title = text_of(item, "title") or info.title
    info.description = strip_html(text_of(item, "description") or text_of(item, "{http://purl.org/rss/1.0/modules/content/}encoded")) or info.description
    info.published_at = text_of(item, "pubDate") or info.published_at
    info.duration = text_of(item, "{http://www.itunes.com/dtds/podcast-1.0.dtd}duration") or info.duration
    enclosure = item.find("enclosure")
    if enclosure is not None:
        info.audio_url = html.unescape(enclosure.attrib.get("url", ""))
        info.audio_source = "rss.enclosure"
    if not info.audio_url:
        candidate = first_audio_candidate_from_text(ET.tostring(item, encoding="unicode"))
        if candidate:
            info.audio_url = candidate
            info.audio_source = "rss-regex"
    return info


def rss_match_score(item: ET.Element, input_url: str, page_info: EpisodeInfo) -> int:
    haystack = " ".join(
        filter(
            None,
            [
                text_of(item, "guid"),
                text_of(item, "link"),
                text_of(item, "title"),
                text_of(item, "description"),
            ],
        )
    )
    score = 0
    if page_info.episode_id and page_info.episode_id in haystack:
        score += 100
    if input_url and input_url in haystack:
        score += 80
    if page_info.title and normalize_text(page_info.title) == normalize_text(text_of(item, "title")):
        score += 60
    return score


class LarkClient:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        if shutil.which("lark-cli") is None:
            raise RuntimeError("没有找到 lark-cli，请先安装并完成飞书配置。")

    def run(self, args: list[str], input_text: str | None = None) -> dict[str, Any]:
        proc = subprocess.run(
            ["lark-cli", *args],
            cwd=self.cwd,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            payload = try_parse_json_output(proc.stderr) or try_parse_json_output(proc.stdout)
            raise LarkCliError(f"lark-cli {' '.join(args)} 失败：{proc.stderr.strip() or proc.stdout.strip()}", payload)
        return parse_json_output(proc.stdout)

    def upload_file(self, audio_path: Path) -> str:
        rel_path = os.path.relpath(audio_path, self.cwd)
        data = self.run(["drive", "+upload", "--as", "user", "--file", rel_path, "--json"])
        token = first_key(data, {"file_token", "token"})
        if not token:
            raise RuntimeError("上传成功但没有在返回值中找到 file_token。")
        return str(token)

    def ensure_required_scopes(self, scopes: str) -> None:
        self.run(["auth", "check", "--scope", scopes])

    def create_minute(self, file_token: str) -> str:
        data = self.run(["minutes", "+upload", "--as", "user", "--file-token", file_token, "--json"])
        minute_url = first_key(data, {"minute_url", "url"})
        if not minute_url:
            raise RuntimeError("妙记创建成功但没有在返回值中找到 minute_url。")
        return str(minute_url)

    def poll_notes(self, minute_token: str, polls: int, interval: int) -> dict[str, Any]:
        last_error = ""
        for attempt in range(1, polls + 1):
            try:
                data = self.run(
                    [
                        "vc",
                        "+notes",
                        "--as",
                        "user",
                        "--minute-tokens",
                        minute_token,
                        "--output-dir",
                        "minutes-output",
                        "--overwrite",
                        "--json",
                    ]
                )
                if first_key(data, {"transcript_file", "summary", "chapters"}):
                    return data
            except LarkCliError as exc:
                if exc.error_type == "authorization" or exc.error_subtype == "missing_scope":
                    raise RuntimeError(str(exc)) from exc
                last_error = str(exc)
            if attempt < polls:
                time.sleep(interval)
        raise RuntimeError(f"妙记产物未在预期时间内生成。最后一次错误：{last_error}")

    def create_doc(self, document_xml: str, parent_token: str | None, parent_position: str | None) -> str:
        args = ["docs", "+create", "--api-version", "v2", "--as", "user", "--content", "-", "--json"]
        if parent_token:
            args.extend(["--parent-token", parent_token])
        if parent_position:
            args.extend(["--parent-position", parent_position])
        data = self.run(args, input_text=document_xml)
        url = first_key(data, {"url"})
        if not url:
            raise RuntimeError("文档创建成功但没有在返回值中找到 url。")
        return str(url)

    def append_doc(self, doc_url: str, document_xml: str) -> None:
        self.run(
            [
                "docs",
                "+update",
                "--api-version",
                "v2",
                "--as",
                "user",
                "--doc",
                doc_url,
                "--command",
                "append",
                "--content",
                "-",
                "--json",
            ],
            input_text=document_xml,
        )


def build_document_xml(info: EpisodeInfo, notes: dict[str, Any], minute_url: str) -> str:
    artifacts = collect_artifacts(notes)
    summary = artifact_to_text(artifacts.get("summary")) or "飞书妙记未返回摘要。"
    todos = artifact_list_to_texts(artifacts.get("todos"))
    chapters = artifact_list_to_texts(artifacts.get("chapters"))
    keywords = artifact_list_to_texts(artifacts.get("keywords"))
    title = info.title or "小宇宙播客转写"

    parts = [
        f"<title>{x(title)} - 播客转写与总结</title>",
        '<callout emoji="✅" background-color="light-green" border-color="green">',
        f"<p><b>摘要：</b>{x(first_paragraph(summary))}</p>",
        "</callout>",
        "<h1>节目元信息</h1>",
        "<table><thead><tr><th background-color=\"light-gray\">字段</th><th background-color=\"light-gray\">内容</th></tr></thead><tbody>",
        table_row("单集标题", title),
        table_row("播客", info.podcast_title),
        table_row("作者", info.author),
        table_row("发布时间", info.published_at),
        table_row("时长", info.duration),
        table_row("原始链接", info.input_url),
        table_row("音频来源", info.audio_source or info.audio_url),
        "</tbody></table>",
        "<hr/>",
        "<h1>核心总结</h1>",
        paragraphs(summary),
    ]

    if keywords:
        parts.extend(["<h2>关键词</h2>", bullet_list(keywords[:20])])
    if todos:
        parts.extend(["<h2>可能的行动项</h2>", checkbox_list(todos)])

    parts.extend(["<hr/>", "<h1>分段大纲</h1>"])
    parts.append(bullet_list(chapters) if chapters else "<p>飞书妙记未返回分段大纲。</p>")
    parts.extend(
        [
            "<hr/>",
            "<h1>来源</h1>",
            f'<p><a type="url-preview" href="{attr(info.input_url)}">{x(info.input_url)}</a></p>',
            f'<p><a type="url-preview" href="{attr(minute_url)}">{x(minute_url)}</a></p>',
            "<hr/>",
            "<h1>校对后 ASR 原文</h1>",
        ]
    )
    return "\n".join(parts)


def download_audio(client: HttpClient, audio_url: str, workdir: Path, title: str) -> Path:
    parsed = urllib.parse.urlparse(audio_url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        suffix = mimetypes.guess_extension("audio/mpeg") or ".mp3"
    filename = safe_filename(title) + suffix
    path = workdir / filename
    client.download(audio_url, path)
    return path


def read_transcript(notes: dict[str, Any], workdir: Path) -> str:
    transcript_file = first_key(notes, {"transcript_file"})
    if transcript_file:
        path = Path(str(transcript_file))
        if not path.is_absolute():
            path = workdir / path
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    transcript_text = first_key(notes, {"transcript", "text"})
    if transcript_text:
        return str(transcript_text)
    raise RuntimeError("没有找到逐字稿文件或逐字稿文本。")


def load_corrections(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        if isinstance(data, list):
            result: dict[str, str] = {}
            for item in data:
                if isinstance(item, dict) and "from" in item and "to" in item:
                    result[str(item["from"])] = str(item["to"])
            return result
    except json.JSONDecodeError:
        pass

    corrections: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = "=>" if "=>" in line else "=" if "=" in line else None
        if sep:
            left, right = line.split(sep, 1)
            corrections[left.strip()] = right.strip()
    return corrections


def correct_transcript(text: str, corrections: dict[str, str]) -> str:
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    for source, target in sorted(corrections.items(), key=lambda item: len(item[0]), reverse=True):
        if source:
            text = text.replace(source, target)
    return text


def transcript_xml_chunks(transcript: str, max_chars: int = 15000) -> list[str]:
    paragraphs = [chunk.strip() for chunk in re.split(r"\n\s*\n", transcript) if chunk.strip()]
    if not paragraphs:
        return ["<p>未获取到逐字稿。</p>"]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        for piece in split_long_text(paragraph, max_chars=max_chars // 2):
            block = f"<p>{x(piece)}</p>"
            if current and current_len + len(block) > max_chars:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            current.append(block)
            current_len += len(block)
    if current:
        chunks.append("\n".join(current))
    return chunks


def split_long_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = max(text.rfind("。", start, end), text.rfind("\n", start, end), text.rfind(" ", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        pieces.append(text[start:end].strip())
        start = end
    return [piece for piece in pieces if piece]


def collect_artifacts(notes: dict[str, Any]) -> dict[str, Any]:
    artifacts = first_key(notes, {"artifacts"})
    if isinstance(artifacts, dict):
        return artifacts
    return {}


def artifact_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (artifact_to_text(item) for item in value)))
    if isinstance(value, dict):
        for key in ("content", "text", "summary", "title", "name"):
            if key in value:
                return artifact_to_text(value[key])
        return "\n".join(f"{k}: {artifact_to_text(v)}" for k, v in value.items() if v)
    return str(value)


def artifact_list_to_texts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [artifact_to_text(item).strip() for item in value if artifact_to_text(item).strip()]
    text = artifact_to_text(value).strip()
    return [line.strip("-• 0123456789.、") for line in text.splitlines() if line.strip()]


def parse_json_output(stdout: str) -> dict[str, Any]:
    stdout = stdout.strip()
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stdout, flags=re.S)
        if not match:
            raise RuntimeError(f"无法解析 lark-cli JSON 输出：{stdout[:500]}")
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        return data
    raise RuntimeError("lark-cli JSON 输出不是对象。")


def try_parse_json_output(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def write_state(workdir: Path, state: dict[str, Any]) -> None:
    (workdir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def first_key(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and child not in (None, ""):
                return child
        for child in value.values():
            found = first_key(child, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = first_key(child, keys)
            if found not in (None, ""):
                return found
    return None


def deep_find_episode(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("type") == "EPISODE" or ("eid" in value and "enclosure" in value):
            return value
        if "episode" in value and isinstance(value["episode"], dict):
            return value["episode"]
        for child in value.values():
            found = deep_find_episode(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = deep_find_episode(child)
            if found:
                return found
    return None


def nested_get(value: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def first_audio_candidate_from_text(text: str) -> str:
    unescaped = html.unescape(text).replace("\\u0026", "&").replace("\\/", "/")
    pattern = r"https?://[^\s\"'<>]+?(?:\.mp3|\.m4a|\.aac|\.ogg|\.wav|\.wma|\.amr|\.mp4|\.m4v|\.mov)(?:\?[^\s\"'<>]*)?"
    for match in re.finditer(pattern, unescaped, flags=re.I):
        return match.group(0)
    return ""


def looks_like_audio_url(url: str) -> bool:
    return Path(urllib.parse.urlparse(url).path).suffix.lower() in AUDIO_EXTENSIONS


def is_probable_audio(url: str) -> bool:
    return bool(url) and (looks_like_audio_url(url) or any(marker in url.lower() for marker in ("audio", "media", "xmcdn")))


def episode_id_from_url(url: str) -> str:
    match = re.search(r"/episode/([^/?#]+)", url)
    return match.group(1) if match else ""


def extract_minute_token(minute_url: str) -> str:
    parsed = urllib.parse.urlparse(minute_url)
    token = Path(parsed.path).name
    if not token:
        raise RuntimeError(f"无法从妙记链接提取 token：{minute_url}")
    return token


def text_of(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def prefer_richer(current: str, candidate: str) -> str:
    if not candidate:
        return current
    if not current:
        return candidate
    return candidate if len(candidate.strip()) > len(current.strip()) * 1.2 else current


def safe_filename(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:120] or "episode"


def x(text: Any) -> str:
    return html.escape(str(text or ""), quote=False).replace("\n", "<br/>")


def attr(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)


def table_row(left: str, right: str) -> str:
    return f"<tr><td>{x(left)}</td><td>{x(right or '-')}</td></tr>"


def first_paragraph(text: str) -> str:
    for chunk in re.split(r"\n\s*\n|\n", text):
        if chunk.strip():
            return chunk.strip()
    return text.strip()


def paragraphs(text: str) -> str:
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    return "\n".join(f"<p>{x(chunk)}</p>" for chunk in chunks) if chunks else "<p>-</p>"


def bullet_list(items: list[str]) -> str:
    if not items:
        return "<p>-</p>"
    return "<ul>" + "".join(f"<li>{x(item)}</li>" for item in items if item.strip()) + "</ul>"


def checkbox_list(items: list[str]) -> str:
    if not items:
        return "<p>-</p>"
    return "\n".join(f'<checkbox done="false">{x(item)}</checkbox>' for item in items if item.strip())


def transcript_blocks(transcript: str) -> str:
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", transcript) if chunk.strip()]
    if not chunks:
        return "<p>未获取到逐字稿。</p>"
    return "\n".join(f"<p>{x(chunk)}</p>" for chunk in chunks)


def print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
