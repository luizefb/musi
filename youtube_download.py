"""Import YouTube videos/playlists through the selected RapidAPI MCP server."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import site
import sys
import threading
from urllib.parse import parse_qs, urlparse
import uuid


MCP_URL = "https://mcp.rapidapi.com"
API_HOST = "youtube-mp3-downloader.p.rapidapi.com"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")
KEY_FILE = Path(__file__).resolve().parent / ".rapidapi-key"
LOCAL_DEPS = Path(__file__).resolve().parent / ".deps"
if LOCAL_DEPS.is_dir() and str(LOCAL_DEPS) not in sys.path:
    site.addsitedir(str(LOCAL_DEPS))


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class Video:
    id: str
    title: str
    playlist: str = ""


def api_key() -> str:
    value = os.environ.get("RAPIDAPI_KEY", "").strip()
    if not value and KEY_FILE.is_file():
        value = KEY_FILE.read_text(encoding="utf-8").strip()
    return value


def parse_links(text: str) -> list[tuple[str, str]]:
    """Return distinct (kind, ID) pairs, preserving input order."""
    found: list[tuple[str, str]] = []
    for raw in text.split():
        url = urlparse(raw.strip("<> ,;"))
        host = (url.hostname or "").lower()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com",
                        "music.youtube.com", "youtu.be", "www.youtu.be"} or url.scheme not in {"http", "https"}:
            raise DownloadError(f"Link do YouTube inválido: {raw}")
        query = parse_qs(url.query)
        path = url.path.strip("/").split("/")
        playlist = query.get("list", [""])[0]
        video = query.get("v", [""])[0]
        if host.endswith("youtu.be"):
            video = path[0]
        elif len(path) > 1 and path[0] in {"shorts", "embed", "live"}:
            video = path[1]
        # A watch URL with list= denotes a playlist when pasted into the batch field.
        if playlist and PLAYLIST_ID.fullmatch(playlist):
            item = ("playlist", playlist)
        elif VIDEO_ID.fullmatch(video):
            item = ("video", video)
        else:
            raise DownloadError(f"Link de vídeo ou playlist inválido: {raw}")
        if item not in found:
            found.append(item)
    if not found:
        raise DownloadError("Cole pelo menos um link de vídeo ou playlist do YouTube.")
    return found


def expand_links(links: list[tuple[str, str]], cancel: threading.Event) -> list[Video]:
    """yt-dlp only enumerates playlists; audio always comes from RapidAPI."""
    videos: list[Video] = []
    seen: set[str] = set()
    for kind, ident in links:
        if cancel.is_set():
            return videos
        if kind == "video":
            if ident not in seen:
                videos.append(Video(ident, ident))
                seen.add(ident)
            continue
        try:
            import yt_dlp
        except ImportError as exc:
            raise DownloadError("Instale as dependências: python -m pip install -r requirements.txt") from exc
        try:
            with yt_dlp.YoutubeDL({"extract_flat": "in_playlist", "skip_download": True,
                                   "quiet": True, "no_warnings": True,
                                   "ignoreerrors": True}) as client:
                info = client.extract_info(f"https://www.youtube.com/playlist?list={ident}", download=False)
        except Exception as exc:
            raise DownloadError(f"Não foi possível ler a playlist {ident}: {exc}") from exc
        if not info or not info.get("entries"):
            raise DownloadError(f"A playlist {ident} está vazia ou indisponível.")
        playlist_title = str(info.get("title") or ident)
        for entry in info["entries"]:
            if cancel.is_set():
                return videos
            if not entry:
                continue
            video_id = str(entry.get("id") or "")
            if VIDEO_ID.fullmatch(video_id) and video_id not in seen:
                videos.append(Video(video_id, str(entry.get("title") or video_id), playlist_title))
                seen.add(video_id)
    return videos


def _tool_arguments(tool: object, video: Video) -> dict[str, str]:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    args: dict[str, str] = {}
    for name in required:
        lower = name.lower()
        if any(part in lower for part in ("url", "link")):
            args[name] = f"https://www.youtube.com/watch?v={video.id}"
        elif any(part in lower for part in ("video", "youtube", "id")):
            args[name] = video.id
        elif lower in {"format", "type", "extension"}:
            args[name] = "mp3"
        elif lower in {"quality", "bitrate"}:
            options = props.get(name, {}).get("enum", [])
            args[name] = next((item for item in ("high", "highest", "320", "192", "128")
                               if item in options), str(options[0]) if options else "high")
        else:
            raise DownloadError(f"A API exige o parâmetro desconhecido '{name}'.")
    if not args:
        for name in props:
            lower = name.lower()
            if lower in {"url", "link", "video_url", "youtube_url"}:
                args[name] = f"https://www.youtube.com/watch?v={video.id}"
                break
            if lower in {"id", "video_id", "videoid"}:
                args[name] = video.id
                break
    if not args:
        raise DownloadError("A API não publicou um parâmetro de vídeo reconhecível.")
    return args


def _choose_tool(tools: list[object]) -> object:
    candidates = []
    for tool in tools:
        name = str(getattr(tool, "name", "")).lower()
        description = str(getattr(tool, "description", "")).lower()
        combined = name + " " + description
        if any(word in combined for word in ("playlist", "transcript", "search")):
            continue
        if "mp3" in combined and any(word in combined for word in ("download", "convert", "audio")):
            score = (3 if "download" in name else 0) + (2 if "mp3" in name else 0)
            candidates.append((score, tool))
    if not candidates:
        raise DownloadError("Esta API não publicou uma ferramenta MCP de download MP3.")
    return max(candidates, key=lambda pair: pair[0])[1]


def _unwrap_result(result: object) -> tuple[str | None, bytes | None, str | None]:
    """Accept a direct download URL or base64 MP3 from an MCP tool result."""
    if getattr(result, "isError", False):
        blocks = getattr(result, "content", []) or []
        detail = " ".join(str(getattr(block, "text", "")) for block in blocks).strip()
        raise DownloadError(detail or "A API recusou o download.")
    values: list[object] = []
    structured = getattr(result, "structuredContent", None)
    if structured:
        values.append(structured)
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                values.append(json.loads(text))
            except json.JSONDecodeError:
                values.append(text.strip())
    title: str | None = None
    for value in values:
        if isinstance(value, dict):
            if value.get("status") in {"error", "failed"} or value.get("success") is False:
                raise DownloadError(str(value.get("message") or value.get("error") or "Falha na API."))
            title = str(value.get("title") or value.get("name") or title or "") or None
            for key in ("download_url", "downloadUrl", "download_link", "downloadLink",
                        "mp3_url", "mp3Url", "audio_url", "audioUrl", "url", "link", "file"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.startswith("https://"):
                    return candidate, None, title
            for key in ("base64", "audio_base64", "mp3_base64"):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    return None, base64.b64decode(candidate), title
            for key in ("data", "result"):
                nested = value.get(key)
                if isinstance(nested, dict):
                    nested_result = type("Result", (), {"content": [], "structuredContent": nested, "isError": False})()
                    url, data, nested_title = _unwrap_result(nested_result)
                    return url, data, title or nested_title
        elif isinstance(value, str) and value.startswith("https://"):
            return value, None, title
    raise DownloadError("A API não retornou um link ou arquivo MP3 reconhecível.")


def _safe_name(title: str, video_id: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:130].strip(" .")
    return f"{cleaned or video_id} [{video_id}].mp3"


async def download_videos(videos: list[Video], folder: Path, key: str,
                          cancel: threading.Event, on_event) -> list[Path]:
    """Use the RapidAPI MCP tool for each MP3, streaming files to a staging folder."""
    try:
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:
        raise DownloadError("Instale as dependências: python -m pip install -r requirements.txt") from exc
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    headers = {"x-api-host": API_HOST, "x-api-key": key}
    async with streamablehttp_client(MCP_URL, headers=headers, timeout=30.0,
                                     sse_read_timeout=300.0) as (read, write, _):
        async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                tool = _choose_tool(listing.tools)
                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0), follow_redirects=True) as files_http:
                    for index, video in enumerate(videos, 1):
                        if cancel.is_set():
                            break
                        on_event("downloading", index, len(videos), video.title)
                        try:
                            existing = next(folder.glob(f"* [{video.id}].mp3"), None)
                            if existing is not None:
                                saved.append(existing)
                                on_event("downloaded", video, existing)
                                on_event("download_progress", index, len(videos), video.title)
                                continue
                            result = await session.call_tool(tool.name, _tool_arguments(tool, video))
                            url, data, title = _unwrap_result(result)
                            name = _safe_name(title or video.title, video.id)
                            destination = folder / name
                            if destination.exists():
                                saved.append(destination)
                                on_event("downloaded", video, destination)
                                on_event("download_progress", index, len(videos), video.title)
                                continue
                            temporary = folder / f".{uuid.uuid4().hex}.partial.mp3"
                            try:
                                if data is not None:
                                    temporary.write_bytes(data)
                                else:
                                    assert url is not None
                                    async with files_http.stream("GET", url) as response:
                                        response.raise_for_status()
                                        with temporary.open("xb") as output:
                                            async for chunk in response.aiter_bytes():
                                                if cancel.is_set():
                                                    raise DownloadError("Download cancelado.")
                                                output.write(chunk)
                                with temporary.open("rb") as source:
                                    header = source.read(3)
                                if header != b"ID3" and not (len(header) >= 2 and header[0] == 0xff and header[1] & 0xe0 == 0xe0):
                                    raise DownloadError("A resposta não contém um MP3 válido.")
                                os.link(temporary, destination)
                                temporary.unlink()
                                saved.append(destination)
                                on_event("downloaded", video, destination)
                            finally:
                                temporary.unlink(missing_ok=True)
                        except Exception as exc:
                            on_event("download_error", video, str(exc))
                        on_event("download_progress", index, len(videos), video.title)
    return saved


def download_videos_locally(videos: list[Video], folder: Path, ffmpeg: str,
                            cancel: threading.Event, on_event) -> list[Path]:
    """Fallback when the selected RapidAPI endpoint is unavailable."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise DownloadError("Instale as dependências: python -m pip install -r requirements.txt") from exc
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for index, video in enumerate(videos, 1):
        if cancel.is_set():
            break
        on_event("downloading", index, len(videos), video.title)
        existing = next(folder.glob(f"* [{video.id}].mp3"), None)
        if existing is not None:
            saved.append(existing)
            on_event("downloaded", video, existing)
            on_event("download_progress", index, len(videos), video.title)
            continue
        temporary = folder / f"{video.id}.mp3"

        def check_cancel(_status: dict) -> None:
            if cancel.is_set():
                raise DownloadError("Download cancelado.")

        settings = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "outtmpl": str(folder / f"{video.id}.%(ext)s"),
            "ffmpeg_location": ffmpeg,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                                "preferredquality": "192"}],
            "progress_hooks": [check_cancel],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "overwrites": False,
        }
        try:
            with yt_dlp.YoutubeDL(settings) as client:
                info = client.extract_info(f"https://www.youtube.com/watch?v={video.id}", download=True)
            if cancel.is_set():
                break
            if not temporary.is_file():
                raise DownloadError("O conversor local não gerou o MP3 esperado.")
            destination = folder / _safe_name(str(info.get("title") or video.title), video.id)
            os.link(temporary, destination)
            temporary.unlink()
            saved.append(destination)
            on_event("downloaded", video, destination)
        except Exception as exc:
            if cancel.is_set():
                break
            on_event("download_error", video, str(exc))
        on_event("download_progress", index, len(videos), video.title)
    return saved
