---
name: xiaoyuzhou-to-lark-doc
description: Convert public Xiaoyuzhou episode links or direct podcast audio links into Lark documents with transcript, corrected ASR text, summary, outline, and source metadata. Use when the user asks to process 小宇宙, xiaoyuzhoufm.com, podcast episode links, podcast audio extraction, ASR transcription, 飞书妙记, or generating a 飞书文档 from a podcast.
---

# Xiaoyuzhou To Lark Doc

Use this skill to turn a public Xiaoyuzhou episode into a Lark document. The bundled script resolves the audio URL, downloads the media, uploads it to Lark Drive, creates a Lark Minutes record, reads transcript/artifacts, and creates a Lark document with metadata, summary, outline, and corrected ASR text.

## Primary Workflow

1. Use the bundled script unless the user explicitly asks for a different implementation.
2. Run from a writable working directory.
3. Pass the user-provided Xiaoyuzhou episode URL as the first argument.
4. Use `--workdir` for per-episode output. Prefer a stable folder name based on the episode id.
5. If a previous run already generated a Lark Minutes token, use `--minute-token` or `--minute-url` to skip download/upload.

```bash
python3 ~/.codex/skills/xiaoyuzhou-to-lark-doc/scripts/xiaoyuzhou_to_lark_doc.py \
  "https://www.xiaoyuzhoufm.com/episode/..." \
  --workdir ./runs/<episode-id>
```

To reuse an existing Lark Minutes record:

```bash
python3 ~/.codex/skills/xiaoyuzhou-to-lark-doc/scripts/xiaoyuzhou_to_lark_doc.py \
  "https://www.xiaoyuzhoufm.com/episode/..." \
  --minute-token obcn_example_token \
  --workdir ./runs/<episode-id>
```

## Inputs

- Required: Xiaoyuzhou episode URL, direct audio URL, or another public podcast episode URL that exposes audio metadata.
- Optional `--rss`: RSS feed URL for fallback matching when page parsing cannot find audio.
- Optional `--glossary`: correction file for ASR cleanup. Supported formats: `wrong=>right`, `wrong=right`, JSON object, or JSON array of `{ "from": "...", "to": "..." }`.
- Optional `--parent-token` or `--parent-position`: target Lark document location.
- Optional `--insecure`: only use when local Python certificate validation fails for public Xiaoyuzhou HTTPS requests.

## Lark Requirements

The script relies on `lark-cli` and user identity. If scopes are missing, follow the CLI hint and use split-flow auth when acting as the agent.

Common required scopes include:

```bash
lark-cli auth login --scope "minutes:minutes:readonly minutes:minutes.artifacts:read"
```

If `vc +notes` returns `authorization` / `missing_scope`, do not keep polling. Ask the user to complete authorization, then rerun with `--minute-token` to avoid re-uploading the audio.

The script checks these scopes before download/upload by default. Do not pass `--skip-auth-check` unless the user explicitly wants to create only the native Lark Minutes record or is debugging permissions.

## Behavior Notes

- Respect copyright and access boundaries. Process only public episodes or audio the user has rights to use. Do not bypass paywalls, login restrictions, or private media controls.
- The first Lark document the user receives may be the native Lark Minutes document. The script may still need to read artifacts and create a second curated document.
- Long transcripts are appended in chunks via `docs +update append`, so do not rewrite the script to create one huge document payload.
- The script writes a `state.json` with `minute_url`, `minute_token`, and `file_token` after creating a minute. Reuse it after failures.

## Validation

After changing the bundled script, run:

```bash
python3 -m py_compile ~/.codex/skills/xiaoyuzhou-to-lark-doc/scripts/xiaoyuzhou_to_lark_doc.py
```

For parser and regression tests, run the test suite from the development repository when available.
