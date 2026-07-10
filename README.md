# Xiaoyuzhou To Lark Doc

Codex skill for turning a public Xiaoyuzhou podcast episode into a Lark document.

The bundled script resolves the episode audio URL, downloads the media, uploads it to Lark Drive, creates a Lark Minutes record, reads transcript and AI artifacts, then creates a Lark document containing podcast metadata, summary, outline, and corrected ASR text.

## Install

Copy this folder into your Codex skills directory:

```sh
mkdir -p ~/.codex/skills
cp -R xiaoyuzhou-to-lark-doc ~/.codex/skills/
```

Restart Codex or start a new thread, then invoke:

```text
Use $xiaoyuzhou-to-lark-doc to turn this Xiaoyuzhou episode link into a Lark document: https://...
```

## Requirements

- Python 3
- `lark-cli`
- A configured Lark/Feishu app and user login
- Lark scopes for file upload, minutes upload/read, and document create/update

For reading Lark Minutes artifacts, authorize:

```sh
lark-cli auth login --scope "minutes:minutes:readonly minutes:minutes.artifacts:read"
```

## Direct Script Usage

```sh
python3 scripts/xiaoyuzhou_to_lark_doc.py "https://www.xiaoyuzhoufm.com/episode/..." \
  --workdir ./runs/<episode-id>
```

Reuse an existing Lark Minutes record:

```sh
python3 scripts/xiaoyuzhou_to_lark_doc.py "https://www.xiaoyuzhoufm.com/episode/..." \
  --minute-token obcn_example_token \
  --workdir ./runs/<episode-id>
```

## Safety

Process only public episodes or audio you have rights to use. Do not bypass paywalls, login restrictions, or private media controls.

Do not commit `runs/`, audio files, transcripts, QR codes, local Lark tokens, or app credentials.
