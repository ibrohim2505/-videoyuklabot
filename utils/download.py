from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import ffmpeg
import requests
import yt_dlp

from utils.config import get_settings

DOWNLOAD_DIR = Path("downloads")
OUTPUT_TEMPLATE = str(DOWNLOAD_DIR / "%(id)s.%(ext)s")
INSTAGRAM_DOMAINS = ("instagram.com", "instagr.am")
TIKTOK_DOMAINS = ("tiktok.com", "tiktokcdn.com", "vm.tiktok.com", "vt.tiktok.com")
SNAPCHAT_DOMAINS = (
    "snapchat.com",
    "story.snapchat.com",
)
LIKEE_DOMAINS = (
    "likee.video",
    "l.likee.video",
    "like.video",
)
YOUTUBE_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
)
SUPPORTED_DOMAINS = (
    *INSTAGRAM_DOMAINS,
    *TIKTOK_DOMAINS,
    *SNAPCHAT_DOMAINS,
    *LIKEE_DOMAINS,
    *YOUTUBE_DOMAINS,
)

INSTAGRAM_REQUEST_TIMEOUT = 20

_settings = get_settings()

INSTAGRAM_HEADERS = {
    "User-Agent": _settings.download_user_agent,
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.instagram.com/",
    "X-Requested-With": "XMLHttpRequest",
    "X-IG-App-ID": "936619743392459",
}


class DownloadError(RuntimeError):
    """Raised when media download fails."""


@dataclass(slots=True)
class DownloadResult:
    file_path: Path
    title: str
    duration: Optional[float]
    ext: str
    media_type: str = "video"  # "video" yoki "photo"


def is_supported_url(url: str) -> bool:
    lowered = url.lower()
    return any(domain in lowered for domain in SUPPORTED_DOMAINS)


def _is_instagram_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain in host for domain in INSTAGRAM_DOMAINS)


def _is_tiktok_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain in host for domain in TIKTOK_DOMAINS)


def _is_snapchat_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain in host for domain in SNAPCHAT_DOMAINS)


def _is_likee_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain in host for domain in LIKEE_DOMAINS)


def _is_youtube_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain in host for domain in YOUTUBE_DOMAINS)


async def download_video(url: str) -> DownloadResult:
    """Download media (video or photo) and return downloaded file details."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if _is_instagram_url(url):
        try:
            return await asyncio.to_thread(_download_instagram_media, url)
        except DownloadError as error:
            logging.info("Instagram JSON API ishlamadi, yt-dlp ga o'tilmoqda: %s", error)
            return await _download_with_ytdlp(url, ensure_playable=False)

    if _is_tiktok_url(url):
        return await _download_tiktok_media(url)

    if _is_snapchat_url(url):
        return await _download_with_ytdlp(url, ensure_playable=False)

    if _is_likee_url(url):
        return await _download_with_ytdlp(url, ensure_playable=False)

    if _is_youtube_url(url):
        return await _download_with_ytdlp(url, ensure_playable=False)

    return await _download_with_ytdlp(url)


def _normalize_remote_url(raw_url: str, base: str) -> str:
    cleaned = (raw_url or "").strip()
    if not cleaned:
        return cleaned
    if cleaned.startswith("//"):
        return f"https:{cleaned}"
    if cleaned.startswith("/"):
        return f"{base.rstrip('/')}{cleaned}"
    return cleaned


async def _download_with_ytdlp(url: str, *, ensure_playable: bool = False) -> DownloadResult:
    def _worker() -> DownloadResult:
        retries = max(1, _settings.download_retries)
        socket_timeout = max(10, _settings.download_socket_timeout)

        is_instagram = _is_instagram_url(url)
        is_youtube = _is_youtube_url(url)
        is_snapchat = _is_snapchat_url(url)
        is_likee = _is_likee_url(url)

        ydl_opts = {
            "outtmpl": OUTPUT_TEMPLATE,
            "noprogress": True,
            "quiet": True,
            "writesubtitles": False,
            "writeautomaticsub": False,
            "nocheckcertificate": True,
            "socket_timeout": float(socket_timeout),
            "retries": retries,
            "fragment_retries": retries,
            "geo_bypass": True,
            "http_headers": {
                "User-Agent": _settings.download_user_agent,
                "Referer": (
                    "https://www.instagram.com/"
                    if is_instagram
                    else "https://www.youtube.com/"
                    if is_youtube
                    else "https://story.snapchat.com/"
                    if is_snapchat
                    else "https://www.likee.video/"
                    if is_likee
                    else "https://www.tiktok.com/"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        
        # Asl formatni saqlagan holda, avvalo Telegram bilan mos keladigan H264 oqimlarini tanlashga urinadi
        ydl_opts["format"] = (
            "bestvideo[ext=mp4][vcodec~=avc]+bestaudio[ext=m4a]/"
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo*+bestaudio/best"
        )

        if _settings.download_proxy:
            ydl_opts["proxy"] = _settings.download_proxy

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info: Optional[dict[str, Any]] = None
            last_error: Optional[yt_dlp.utils.DownloadError] = None  # type: ignore[attr-defined]
            for attempt in range(1, retries + 1):
                try:
                    logging.info("yt-dlp yuklash boshlandi: %s", url)
                    info = ydl.extract_info(url, download=True)
                    logging.info("yt-dlp muvaffaqiyatli: info keys = %s", list(info.keys()) if info else None)
                    break
                except yt_dlp.utils.DownloadError as exc:  # type: ignore[attr-defined]
                    last_error = exc
                    logging.warning(
                        "Yuklab olish urinish %s/%s muvaffaqiyatsiz: %s",
                        attempt,
                        retries,
                        exc,
                    )
                    if attempt >= retries:
                        raise
                    time.sleep(min(2 ** attempt, 10))

            if info is None:
                raise DownloadError("Media haqida ma'lumot olinmadi.") from last_error
            
            # Instagram playlist (bir necha rasm) uchun maxsus ishlov
            if info.get("_type") == "playlist":
                entries = info.get("entries", [])
                if not entries:
                    raise DownloadError("Post tarkibidagi media topilmadi.")

                preferred_entry: Optional[dict[str, Any]] = None
                for entry in entries:
                    vcodec = (entry.get("vcodec") or "").lower()
                    if vcodec and vcodec != "none":
                        preferred_entry = entry
                        break

                if preferred_entry is None:
                    for entry in entries:
                        ext = (entry.get("ext") or "").lower()
                        if ext and ext not in {"jpg", "jpeg", "png", "webp", "ico"}:
                            preferred_entry = entry
                            break

                if preferred_entry is None:
                    preferred_entry = entries[0]

                info = preferred_entry
                logging.info(
                    "Playlist elementi tanlandi: id=%s, ext=%s",
                    info.get("id"),
                    info.get("ext"),
                )
            
            output = Path(ydl.prepare_filename(info))
            
            # Turli file extensionlarni tekshirish
            if not output.exists():
                # Avval mp4 ni tekshirish
                candidate = output.with_suffix(".mp4")
                if candidate.exists():
                    output = candidate
                else:
                    # Boshqa formatlari: jpg, jpeg, png, webp
                    for ext in [".jpg", ".jpeg", ".png", ".webp", ".mkv", ".webm"]:
                        candidate = output.with_suffix(ext)
                        if candidate.exists():
                            output = candidate
                            break
            
            if not output.exists():
                # Download papkasidagi barcha fayllarni ko'rsatish (debug uchun)
                download_files = list(DOWNLOAD_DIR.glob("*"))
                logging.error(
                    "Yuklab olingan fayl topilmadi. Kutilgan: %s, Download papkasidagi fayllar: %s",
                    output,
                    [f.name for f in download_files[-5:]]  # oxirgi 5ta fayl
                )
                raise DownloadError("Yuklab olingan fayl topilmadi.")
            
            # Media turini aniqlash
            media_type = "photo" if output.suffix.lstrip(".") in ("jpg", "jpeg", "png", "webp") else "video"

            if media_type == "video":
                video_codec = _detect_video_codec(info)
                if video_codec and not _is_telegram_friendly_codec(video_codec):
                    logging.info(
                        "Video codec %s Telegram bilan mos emas, qayta kodlanmoqda.",
                        video_codec,
                    )
                    output = _ensure_playable_mp4(output)
            
            return DownloadResult(
                file_path=output,
                title=info.get("title", "Instagram media"),
                duration=info.get("duration"),
                ext=output.suffix.lstrip("."),
                media_type=media_type,
            )

    try:
        result = await asyncio.to_thread(_worker)
        if ensure_playable and result.media_type == "video":
            final_path = await asyncio.to_thread(_ensure_playable_mp4, result.file_path)
            if final_path != result.file_path:
                result = DownloadResult(
                    file_path=final_path,
                    title=result.title,
                    duration=result.duration,
                    ext=final_path.suffix.lstrip("."),
                    media_type=result.media_type,
                )
        return result
    except yt_dlp.utils.DownloadError as error:  # type: ignore[attr-defined]
        logging.exception("Video yuklab olishda xato: %s", error)
        error_message = str(error).lower()
        user_message = "Video yuklab olib bo'lmadi. Havolani tekshiring."
        if "handshake operation timed out" in error_message:
            user_message = "TikTok serveri juda sekin javob bermoqda. Birozdan so'ng qayta urinib ko'ring."
        raise DownloadError(user_message) from error
    except Exception as error:  # pragma: no cover
        logging.exception("Video yuklab olishda kutilmagan xato", exc_info=error)
        raise DownloadError("Kutilmagan xato yuz berdi. Keyinroq urinib ko'ring.") from error


async def _download_tiktok_media(url: str) -> DownloadResult:
    try:
        result = await asyncio.to_thread(_download_tiktok_via_ssstik, url)
        logging.info("TikTok video ssstik orqali yuklandi")
        return result
    except DownloadError as error:
        logging.error("TikTok video ssstik orqali ham yuklanmadi: %s", error)
        raise


def _download_tiktok_via_ssstik(url: str) -> DownloadResult:
    session = requests.Session()
    session.headers.update({
        "User-Agent": _settings.download_user_agent,
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        landing = session.get("https://ssstik.io/", timeout=20)
        landing.raise_for_status()
    except requests.RequestException as error:
        logging.exception("SSStik sahifasiga ulanishda xato", exc_info=error)
        raise DownloadError("TikTok videosini olishda xato yuz berdi.") from error

    token_match = re.search(r'id="tt"\s+value="([^"]+)"', landing.text)
    token = token_match.group(1) if token_match else ""

    payload = {"id": url, "locale": "en", "tt": token}
    headers = {
        "Referer": "https://ssstik.io/",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    try:
        response = session.post("https://ssstik.io/abc?url=dl", data=payload, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        logging.exception("SSStik API bilan bog'lanib bo'lmadi", exc_info=error)
        raise DownloadError("TikTok videosini olishda xato yuz berdi.") from error

    status: Optional[str] = None
    html_block = ""
    try:
        payload_data = response.json()
        if isinstance(payload_data, dict):
            status = str(payload_data.get("status", "")).lower() or None
            html_block = payload_data.get("data") or payload_data.get("result") or ""
    except ValueError:
        html_block = response.text

    if status and status not in {"ok", "success"}:
        raise DownloadError("TikTok videosini olishda xato yuz berdi.")

    if not html_block:
        raise DownloadError("TikTok videosini olishda xato yuz berdi.")

    video_url: Optional[str] = None
    link_match = re.search(
        r'<a[^>]+href="([^"\s]+)"[^>]*class="[^"]*without_watermark[^"]*"',
        html_block,
        re.IGNORECASE,
    )
    if link_match:
        video_url = link_match.group(1)
    if not video_url:
        video_match = re.search(r'(https?://[^"\s]+)', html_block)
        if video_match:
            video_url = video_match.group(1)
    if not video_url:
        raise DownloadError("SSStik javobidan video havolasi topilmadi.")

    video_url = _normalize_remote_url(html.unescape(video_url), "https://ssstik.io")

    title_pattern = r'class="[^"]*download-title[^"]*"[^>]*>(.*?)</p>'
    title_match = re.search(title_pattern, html_block, re.DOTALL)
    title = "TikTok video"
    if title_match:
        raw_title = re.sub(r"<.*?>", "", title_match.group(1)).strip()
        if raw_title:
            title = html.unescape(raw_title)

    file_id = uuid.uuid4().hex
    file_path = DOWNLOAD_DIR / f"{file_id}.mp4"

    _download_file_from_url(
        video_url,
        file_path,
        headers={"User-Agent": _settings.download_user_agent, "Referer": "https://ssstik.io/"},
        timeout=40,
    )

    try:
        if file_path.stat().st_size < 120 * 1024:
            raise DownloadError("SSStik bo'sh video qaytardi.")
    except FileNotFoundError as error:
        raise DownloadError("TikTok video fayli topilmadi.") from error

    return DownloadResult(
        file_path=file_path,
        title=title,
        duration=None,
        ext="mp4",
        media_type="video",
    )


def _download_instagram_media(url: str) -> DownloadResult:
    shortcode, media_type, requested_index = _extract_instagram_shortcode(url)
    if not shortcode:
        raise DownloadError("Instagram havolasini tushunib bo'lmadi.")

    payload = _fetch_instagram_payload(shortcode, media_type)

    media = (payload.get("graphql") or {}).get("shortcode_media")
    if not media:
        items = payload.get("items") or []
        if items:
            media = items[0]
    if not media:
        raise DownloadError("Post ma'lumotlari topilmadi.")

    nodes: list[dict[str, Any]] = []
    if media.get("edge_sidecar_to_children"):
        edges = (media.get("edge_sidecar_to_children") or {}).get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            if node:
                nodes.append(node)
    else:
        nodes.append(media)

    if not nodes:
        raise DownloadError("Post tarkibida media topilmadi.")

    index = 0
    if requested_index is not None:
        if 1 <= requested_index <= len(nodes):
            index = requested_index - 1
        else:
            logging.info(
                "Instagram img_index=%s diapazondan tashqarida, mavjud media soni=%s. Birinchi element olinadi.",
                requested_index,
                len(nodes),
            )

    node = nodes[index]
    title = _extract_instagram_caption(media) or f"Instagram {shortcode}"

    if node.get("is_video") and node.get("video_url"):
        video_url = node["video_url"]
        duration_raw = node.get("video_duration") or media.get("video_duration")
        duration = float(duration_raw) if duration_raw else None
        ext = Path(urlparse(video_url).path).suffix.lstrip(".") or "mp4"
        suffix = f"_{index + 1}" if len(nodes) > 1 else ""
        file_path = DOWNLOAD_DIR / f"{shortcode}{suffix}.{ext}"
        _download_file_from_url(
            video_url,
            file_path,
            headers=INSTAGRAM_HEADERS,
            timeout=INSTAGRAM_REQUEST_TIMEOUT,
        )
        return DownloadResult(
            file_path=file_path,
            title=title,
            duration=duration,
            ext=ext,
            media_type="video",
        )

    image_url = (
        node.get("display_url")
        or node.get("display_resources", [{}])[-1].get("src")
        or node.get("thumbnail_src")
    )
    if not image_url:
        raise DownloadError("Postda rasm topilmadi.")

    ext = Path(urlparse(image_url).path).suffix.lstrip(".") or "jpg"
    suffix = f"_{index + 1}" if len(nodes) > 1 else ""
    file_path = DOWNLOAD_DIR / f"{shortcode}{suffix}.{ext}"
    _download_file_from_url(
        image_url,
        file_path,
        headers=INSTAGRAM_HEADERS,
        timeout=INSTAGRAM_REQUEST_TIMEOUT,
    )

    return DownloadResult(
        file_path=file_path,
        title=title,
        duration=None,
        ext=ext,
        media_type="photo",
    )
def _extract_instagram_shortcode(url: str) -> tuple[Optional[str], str, Optional[int]]:
    parsed = urlparse(url)
    parts = [segment for segment in parsed.path.split("/") if segment]
    if not parts:
        return None, "p", None

    media_type = parts[0].lower()
    shortcode = parts[1] if len(parts) > 1 else parts[0]
    shortcode = shortcode.split("?")[0]

    if media_type not in {"p", "reel", "tv"}:
        media_type = "p"

    index = None
    query = parse_qs(parsed.query)
    if "img_index" in query:
        try:
            index = int(query["img_index"][0])
        except (ValueError, TypeError):
            index = None

    return shortcode or None, media_type, index


def _fetch_instagram_payload(shortcode: str, media_type: str) -> dict:
    endpoint = f"https://www.instagram.com/{media_type}/{shortcode}/"
    params = {"__a": "1", "__d": "dis"}

    try:
        response = requests.get(
            endpoint,
            params=params,
            headers=INSTAGRAM_HEADERS,
            timeout=INSTAGRAM_REQUEST_TIMEOUT,
        )
    except requests.RequestException as error:
        logging.exception("Instagram bilan bog'lanishda xato", exc_info=error)
        raise DownloadError("Instagram bilan bog'lanib bo'lmadi.") from error

    if response.ok:
        try:
            data = response.json()
        except ValueError as error:
            logging.debug("Instagram JSON javobi o'qilmadi, HTML fallback ishlatiladi.", exc_info=error)
        else:
            if data.get("graphql") or data.get("items"):
                return data
            logging.debug("Instagram JSON javobi kutilgan formatda emas, HTML fallback.")

    logging.info(
        "Instagram JSON endpoint status %s, HTML fallback sinab ko'riladi.",
        response.status_code,
    )

    return _fetch_instagram_payload_from_html(endpoint)


def _fetch_instagram_payload_from_html(page_url: str) -> dict:
    try:
        response = requests.get(
            page_url,
            headers={
                **INSTAGRAM_HEADERS,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=INSTAGRAM_REQUEST_TIMEOUT,
        )
    except requests.RequestException as error:
        logging.exception("Instagram HTML sahifasini olishda xato", exc_info=error)
        raise DownloadError("Instagram ma'lumotlarini olishda xato yuz berdi.") from error

    if response.status_code == 404:
        raise DownloadError("Instagram havolasi topilmadi yoki o'chirilgan.")

    if not response.ok:
        logging.warning("Instagram HTML status kodi: %s", response.status_code)
        raise DownloadError("Instagram ma'lumotlarini olishda xato yuz berdi.")

    html = response.text

    # __NEXT_DATA__ ga asoslangan layout
    match = re.search(
        r'<script type="application/json"[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if match:
        json_text = match.group(1)
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            logging.debug("__NEXT_DATA__ JSON parselanmadi")
        else:
            graphql = data.get("props", {}).get("pageProps", {}).get("graphql")
            if graphql and (graphql.get("shortcode_media") or graphql.get("reel")):
                return {"graphql": graphql}

    # Eski (entry_data) layout uchun
    for script_match in re.finditer(
        r'<script type="application/json"[^>]*>({"require_login".*?})</script>',
        html,
        re.DOTALL,
    ):
        try:
            data = json.loads(script_match.group(1))
        except json.JSONDecodeError:
            continue
        entry_data = data.get("entry_data", {})
        post_pages = entry_data.get("PostPage") or []
        for page in post_pages:
            graphql = page.get("graphql")
            if graphql and graphql.get("shortcode_media"):
                return {"graphql": graphql}

    logging.warning("Instagram HTML sahifasidan media ma'lumoti topilmadi")
    raise DownloadError("Instagram ma'lumotlarini olishda xato yuz berdi.")


def _extract_instagram_caption(media: dict) -> str:
    caption_edges = (media.get("edge_media_to_caption") or {}).get("edges") or []
    for edge in caption_edges:
        node = edge.get("node") or {}
        text = (node.get("text") or "").strip()
        if text:
            return text
    alt_text = (media.get("accessibility_caption") or "").strip()
    return alt_text


def _download_file_from_url(
    source_url: str,
    destination: Path,
    *,
    headers: Optional[dict[str, str]] = None,
    timeout: int = INSTAGRAM_REQUEST_TIMEOUT,
) -> None:
    try:
        with requests.get(
            source_url,
            headers=headers or {"User-Agent": _settings.download_user_agent},
            stream=True,
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            with destination.open("wb") as file:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        file.write(chunk)
    except requests.RequestException as error:
        if destination.exists():
            try:
                destination.unlink()
            except Exception:  # pragma: no cover
                logging.debug("Yarim yuklangan faylni o'chirishda muammo: %s", destination)
        logging.exception("Faylni yuklab olishda xato", exc_info=error)
        raise DownloadError("Mediat faylini yuklab olishda xato yuz berdi.") from error


async def cleanup_file(path: Path) -> None:
    """Remove downloaded file if it exists."""
    try:
        await asyncio.to_thread(path.unlink)
    except FileNotFoundError:
        pass
    except Exception as error:  # pragma: no cover
        logging.warning("Faylni o'chirishda muammo: %s", error)


def _detect_video_codec(info: dict[str, Any]) -> Optional[str]:
    codec = (info.get("vcodec") or "").strip()
    if codec and codec.lower() != "none":
        return codec

    for key in ("requested_formats", "requested_downloads", "formats"):
        collection = info.get(key) or []
        for entry in collection:
            if not isinstance(entry, dict):
                continue
            candidate = (entry.get("vcodec") or "").strip()
            if candidate and candidate.lower() != "none":
                return candidate
    return None


def _is_telegram_friendly_codec(codec: str) -> bool:
    lowered = codec.lower()
    return lowered.startswith("avc") or lowered.startswith("h264")


def _ensure_playable_mp4(path: Path) -> Path:
    """Ensure the downloaded file is an MP4 optimized for Telegram streaming."""
    try:
        probe = ffmpeg.probe(str(path))
    except ffmpeg.Error as error:
        logging.warning("Video formatini aniqlab bo'lmadi, qayta kodlanadi: %s", error)
        return _transcode_to_mp4(path, has_audio=True)

    audio_stream = _find_stream(probe, "audio")
    has_audio = bool(audio_stream and int(audio_stream.get("channels", 0)) > 0)

    return _transcode_to_mp4(path, has_audio=has_audio)


def _transcode_to_mp4(path: Path, *, has_audio: bool) -> Path:
    target = path.with_suffix(".mp4")
    if target == path:
        target = path.with_name(path.stem + "_h264.mp4")

    logging.info("Videoni mp4 formatiga o'tkazilmoqda: %s -> %s", path, target)
    try:
        stream = ffmpeg.input(str(path))
        output_args = {
            "vcodec": "libx264",
            "movflags": "+faststart",
            "preset": "veryfast",
            "crf": 23,
            "pix_fmt": "yuv420p",
            "profile:v": "baseline",
            "level": "3.1",
            "g": 48,
            "vsync": "vfr",
        }
        if has_audio:
            output_args.update({"acodec": "aac", "b:a": "128k", "ac": 2, "ar": 48000})
        else:
            output_args.update({"an": None})

        (
            stream
            .output(str(target), **output_args)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as error:
        logging.exception("Video formatini o'zgartirishda xato", exc_info=error)
        raise DownloadError("Videoni telegram uchun tayyorlashda xato yuz berdi.") from error

    if target.exists() and path != target:
        try:
            path.unlink()
        except Exception:
            logging.debug("Asl faylni o'chirib bo'lmadi: %s", path)

    return target


def _find_stream(probe_data: dict, stream_type: str) -> Optional[dict]:
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == stream_type:
            return stream
    return None
