"""Public platform models, adapters, and URL router.

This module is intentionally limited to public, read-only extraction. It has no
ASR, OCR, cleanup, browser automation, secret loading, or artifact writing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import requests


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    )
}

DOUYIN_MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    )
}

DOUYIN_PUBLIC_COMMENT_ENDPOINT = "https://www.iesdouyin.com/web/api/v2/comment/list/"
DOUYIN_PUBLIC_COMMENT_LIMIT = 10


@dataclass
class SocialPost:
    platform: str
    content_type: str
    source_url: str
    resolved_url: str
    post_id: str
    title: str
    body: str = ""
    author_name: Optional[str] = None
    author_id: Optional[str] = None
    publish_time: Optional[int] = None
    cover_url: Optional[str] = None
    duration_sec: Optional[int] = None
    video_url: Optional[str] = None
    image_urls: list[str] = field(default_factory=list)
    page_url: Optional[str] = None
    xsec_token: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    author_profile: dict[str, Any] = field(default_factory=dict)
    public_metrics: dict[str, Any] = field(default_factory=dict)
    owner_metrics: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.author_profile:
            self.author_profile = {
                "id": self.author_id,
                "name": self.author_name,
                "handle": None,
                "avatar_url": None,
                "profile_url": None,
                "followers": None,
                "following": None,
                "extra": {},
            }
        else:
            self.author_profile = {
                "id": self.author_profile.get("id", self.author_id),
                "name": self.author_profile.get("name", self.author_name),
                "handle": self.author_profile.get("handle"),
                "avatar_url": self.author_profile.get("avatar_url"),
                "profile_url": self.author_profile.get("profile_url"),
                "followers": self.author_profile.get("followers"),
                "following": self.author_profile.get("following"),
                "extra": self.author_profile.get("extra", {}),
                **{k: v for k, v in self.author_profile.items() if k not in {
                    "id", "name", "handle", "avatar_url", "profile_url", "followers", "following", "extra"
                }},
            }
        if not self.media:
            self.media = {
                "cover_url": self.cover_url,
                "image_urls": self.image_urls,
                "video_url": self.video_url,
            }
        else:
            self.media = {
                "cover_url": self.media.get("cover_url", self.cover_url),
                "image_urls": self.media.get("image_urls", self.image_urls),
                "video_url": self.media.get("video_url", self.video_url),
                **{k: v for k, v in self.media.items() if k not in {"cover_url", "image_urls", "video_url"}},
            }

class PlatformAdapter:
    def can_handle(self, share_text: str) -> bool:
        raise NotImplementedError

    def fetch_post(self, share_text: str) -> SocialPost:
        raise NotImplementedError


class XHSStateParser:
    """Parse XiaoHongShu note data from the page's initial state."""

    @staticmethod
    def parse_html(html: str, source_url: str, resolved_url: str) -> SocialPost:
        state_match = re.search(r"window\.__INITIAL_STATE__=(.*?)</script>", html, flags=re.DOTALL)
        if not state_match:
            raise ValueError("未找到小红书页面状态数据")

        state_blob = state_match.group(1)
        state_blob = re.sub(r":undefined([,}])", r":null\1", state_blob)
        state = json.loads(state_blob)

        note = None
        note_map = ((state.get("note") or {}).get("noteDetailMap") or {})
        if isinstance(note_map, dict) and note_map:
            first_entry = next(iter(note_map.values()))
            if isinstance(first_entry, dict):
                note = first_entry.get("note")
        if not isinstance(note, dict):
            raise ValueError("未找到小红书笔记详情")

        note_id = note.get("noteId") or XHSStateParser._extract_note_id_from_url(resolved_url)
        if not note_id:
            raise ValueError("未找到小红书笔记 ID")

        image_urls = []
        cover_url = None
        for image in note.get("imageList") or []:
            if not isinstance(image, dict):
                continue
            image_url = image.get("urlDefault") or image.get("urlPre") or image.get("url")
            if image_url:
                normalized = _normalize_media_url(image_url)
                image_urls.append(normalized)
                if not cover_url:
                    cover_url = normalized

        video_url = None
        duration_sec = None
        video = note.get("video") or {}
        stream = ((video.get("media") or {}).get("stream") or {})
        for codec in ("h264", "h265", "av1"):
            candidates = stream.get(codec) or []
            if candidates and isinstance(candidates[0], dict):
                master_url = candidates[0].get("masterUrl")
                if master_url:
                    video_url = _normalize_media_url(master_url)
                    break
        duration_sec = ((video.get("capa") or {}).get("duration")) or None

        user = note.get("user") or {}
        interact_info = note.get("interactInfo") or note.get("interact_info") or {}
        avatar_url = _first_media_url(
            user.get("avatar"),
            user.get("avatarUrl"),
            user.get("image"),
            ((user.get("images") or "").split(",")[0] if isinstance(user.get("images"), str) else None),
        )
        user_id = user.get("userId") or user.get("user_id") or user.get("id")
        red_id = user.get("redId") or user.get("red_id")
        author_name = user.get("nickname") or user.get("nickName") or user.get("name")

        author_profile = {
            "id": user_id,
            "name": author_name,
            "handle": red_id,
            "avatar_url": avatar_url,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{user_id}" if user_id else None,
            "description": user.get("desc") or user.get("description"),
            "extra": user,
        }
        public_metrics = {
            "likes": _first_existing(
                interact_info,
                "likedCount",
                "liked_count",
                "likeCount",
                "like_count",
            ),
            "collects": _first_existing(
                interact_info,
                "collectedCount",
                "collected_count",
                "collectCount",
                "collect_count",
            ),
            "comments": _first_existing(
                interact_info,
                "commentCount",
                "comment_count",
            ),
            "shares": _first_existing(
                interact_info,
                "shareCount",
                "share_count",
            ),
        }
        media = {
            "cover_url": cover_url,
            "image_urls": image_urls,
            "video_url": video_url,
        }

        tags = []
        for tag in note.get("tagList") or []:
            if isinstance(tag, dict) and tag.get("name"):
                tags.append(tag["name"])

        note_type = note.get("type") or "normal"
        content_type = "video" if note_type == "video" or video_url else "image_note"
        title = (note.get("title") or "").strip() or _extract_html_title(html) or note_id
        return SocialPost(
            platform="xiaohongshu",
            content_type=content_type,
            source_url=source_url,
            resolved_url=resolved_url,
            post_id=note_id,
            title=title,
            body=(note.get("desc") or "").strip(),
            author_name=author_name,
            author_id=user_id,
            publish_time=note.get("time"),
            cover_url=cover_url,
            duration_sec=duration_sec,
            video_url=video_url,
            image_urls=image_urls,
            page_url=resolved_url,
            xsec_token=note.get("xsecToken") or _extract_xsec_token(resolved_url),
            tags=tags,
            author_profile=author_profile,
            public_metrics=public_metrics,
            media=media,
            extra={"note_type": note_type, "interact_info": interact_info},
        )

    @staticmethod
    def _extract_note_id_from_url(url: str) -> Optional[str]:
        match = re.search(r"/(?:explore|discovery/item)/([^/?]+)", url)
        return match.group(1) if match else None


class XiaoHongShuPlatformAdapter(PlatformAdapter):
    def can_handle(self, share_text: str) -> bool:
        lower = share_text.lower()
        return "xiaohongshu.com" in lower or "xhslink.com" in lower

    def fetch_post(self, share_text: str) -> SocialPost:
        source_url = _extract_first_url(share_text)
        response = requests.get(source_url, headers=HEADERS, timeout=30, allow_redirects=True)
        response.raise_for_status()
        post = XHSStateParser.parse_html(
            html=response.text,
            source_url=source_url,
            resolved_url=response.url,
        )
        fallback_title = _extract_title_from_share_text(share_text)
        if fallback_title and (not post.title or post.title == post.post_id):
            post.title = fallback_title
        return post


class DouyinPlatformAdapter(PlatformAdapter):
    def can_handle(self, share_text: str) -> bool:
        lower = share_text.lower()
        return "douyin.com" in lower or "iesdouyin.com" in lower

    def fetch_post(self, share_text: str) -> SocialPost:
        source_url = _extract_first_url(share_text)
        share_response = requests.get(source_url, headers=DOUYIN_MOBILE_HEADERS, timeout=30, allow_redirects=True)
        share_response.raise_for_status()
        video_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
        share_kind = "note" if "/note/" in share_response.url else "video"
        share_url = f"https://www.iesdouyin.com/share/{share_kind}/{video_id}"

        response = requests.get(share_url, headers=DOUYIN_MOBILE_HEADERS, timeout=30)
        response.raise_for_status()
        pattern = re.compile(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", flags=re.DOTALL)
        match = pattern.search(response.text)
        if not match:
            raise ValueError("从抖音 HTML 中解析视频信息失败")

        data = json.loads(match.group(1).strip())
        loader_data = data.get("loaderData") or {}
        video_info_res = None
        for key in ("video_(id)/page", "note_(id)/page"):
            page = loader_data.get(key) or {}
            if page.get("videoInfoRes"):
                video_info_res = page["videoInfoRes"]
                break
        if not video_info_res:
            raise ValueError("无法从抖音 JSON 中解析视频或图集信息")

        item = (video_info_res.get("item_list") or [{}])[0]
        video = item.get("video") or {}
        play_addr = video.get("play_addr") or {}
        url_list = play_addr.get("url_list") or []
        video_url = None
        if url_list:
            video_url = _normalize_media_url(url_list[0].replace("playwm", "play"))

        image_urls: list[str] = []
        raw_images = item.get("images") or ((item.get("image_post_info") or {}).get("images")) or []
        for image in raw_images:
            if not isinstance(image, dict):
                continue
            image_url = _first_media_url(
                *((image.get("url_list") or [])),
                *(((image.get("display_image") or {}).get("url_list") or [])),
                *(((image.get("owner_watermark_image") or {}).get("url_list") or [])),
            )
            if image_url and image_url not in image_urls:
                image_urls.append(image_url)

        author = item.get("author") or {}
        statistics = item.get("statistics") or {}
        cover = video.get("cover") or {}
        cover_urls = cover.get("url_list") or []
        duration_ms = video.get("duration")
        duration_sec = None
        if isinstance(duration_ms, (int, float)) and duration_ms:
            duration_sec = int(duration_ms / 1000) if duration_ms > 1000 else int(duration_ms)

        title = (item.get("desc") or "").strip() or f"douyin_{video_id}"
        title = re.sub(r'[\\/:*?"<>|]', "_", title)
        cover_url = _normalize_media_url(cover_urls[0]) if cover_urls else (image_urls[0] if image_urls else None)
        sec_uid = author.get("sec_uid")
        uid = author.get("uid")
        unique_id = author.get("unique_id")
        avatar_url = _first_media_url(
            *((author.get("avatar_thumb") or {}).get("url_list") or []),
            *((author.get("avatar_medium") or {}).get("url_list") or []),
            *((author.get("avatar_larger") or {}).get("url_list") or []),
        )
        author_profile = {
            "id": uid or sec_uid,
            "name": author.get("nickname") or unique_id,
            "handle": unique_id,
            "sec_uid": sec_uid,
            "avatar_url": avatar_url,
            "profile_url": f"https://www.douyin.com/user/{sec_uid}" if sec_uid else None,
            "extra": author,
        }
        public_metrics = {
            "views": statistics.get("play_count"),
            "likes": statistics.get("digg_count"),
            "comments": statistics.get("comment_count"),
            "shares": statistics.get("share_count"),
            "collects": statistics.get("collect_count"),
        }
        aweme_type = item.get("aweme_type")
        is_image_note = bool(image_urls) and (aweme_type in {68, 150} or not video_url)
        content_type = "image_note" if is_image_note else "video"
        media_images = image_urls if is_image_note else ([cover_url] if cover_url else [])
        media = {
            "cover_url": cover_url,
            "image_urls": media_images,
            "video_url": None if is_image_note else video_url,
        }

        return SocialPost(
            platform="douyin",
            content_type=content_type,
            source_url=source_url,
            resolved_url=share_response.url,
            post_id=video_id,
            title=title,
            body=(item.get("desc") or "").strip(),
            author_name=author_profile["name"],
            author_id=author_profile["id"],
            publish_time=item.get("create_time"),
            cover_url=cover_url,
            duration_sec=duration_sec,
            video_url=media["video_url"],
            image_urls=media["image_urls"],
            page_url=share_url,
            author_profile=author_profile,
            public_metrics=public_metrics,
            media=media,
            extra={"aweme_type": aweme_type, "statistics": statistics},
        )


def fetch_douyin_public_comments(
    video_id: str,
    *,
    referer: Optional[str] = None,
    limit: int = DOUYIN_PUBLIC_COMMENT_LIMIT,
    sort_by: str = "likes",
) -> dict[str, Any]:
    """Fetch the public top-level comments exposed by Douyin's mobile share API.

    This path is HTTP-only: it does not use a browser, CDP, Playwright, or a
    logged-in account. The public endpoint currently exposes at most ten
    top-level comments and reply counts, but not the reply bodies.
    """

    if not video_id or not str(video_id).isdigit():
        raise ValueError("无效的抖音视频 ID")
    if not 1 <= limit <= DOUYIN_PUBLIC_COMMENT_LIMIT:
        raise ValueError(f"limit 必须在 1 到 {DOUYIN_PUBLIC_COMMENT_LIMIT} 之间")

    normalized_sort = _normalize_douyin_comment_sort(sort_by)
    headers = {
        **DOUYIN_MOBILE_HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Referer": referer or f"https://www.iesdouyin.com/share/video/{video_id}",
    }
    response = requests.get(
        DOUYIN_PUBLIC_COMMENT_ENDPOINT,
        headers=headers,
        params={"aweme_id": str(video_id), "cursor": 0, "count": DOUYIN_PUBLIC_COMMENT_LIMIT},
        timeout=30,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("抖音公开评论接口没有返回 JSON") from exc

    raw_comments = payload.get("comments") if isinstance(payload, dict) else None
    if not isinstance(raw_comments, list):
        raise RuntimeError("抖音公开评论接口没有返回评论列表")

    comments = [
        normalize_douyin_public_comment(comment)
        for comment in raw_comments
        if isinstance(comment, dict) and comment.get("cid")
    ]
    if normalized_sort == "likes":
        comments.sort(key=lambda item: (item["like_count"], item["create_time"]), reverse=True)
    else:
        comments.sort(key=lambda item: (item["create_time"], item["like_count"]), reverse=True)

    return {
        "video_id": str(video_id),
        "sort_by": normalized_sort,
        "ranking_scope": "retrieved_public_top_level_comments",
        "requested_limit": limit,
        "fetched_top_level_count": len(comments),
        "returned_count": min(limit, len(comments)),
        "reported_reply_count": sum(item["reply_count"] for item in comments),
        "reply_bodies_included": False,
        "comments": comments[:limit],
        "source": "douyin_public_mobile_share_api",
        "source_limit": DOUYIN_PUBLIC_COMMENT_LIMIT,
    }


def normalize_douyin_public_comment(comment: dict[str, Any]) -> dict[str, Any]:
    user = comment.get("user") or {}
    create_time = _coerce_int(comment.get("createTime") or comment.get("create_time"))
    like_count = _coerce_int(comment.get("digg_count"))
    reply_count = _coerce_int(comment.get("reply_comment_total"))
    avatar_url = _first_media_url(
        *((user.get("avatar_thumb") or {}).get("url_list") or []),
        *((user.get("avatar_medium") or {}).get("url_list") or []),
        *((user.get("avatar_larger") or {}).get("url_list") or []),
    )
    return {
        "comment_id": str(comment.get("cid") or ""),
        "video_id": str(comment.get("aweme_id") or ""),
        "text": str(comment.get("text") or ""),
        "create_time": create_time,
        "create_time_iso": _unix_time_iso(create_time),
        "like_count": like_count,
        "reply_count": reply_count,
        "ip_label": comment.get("ip_label"),
        "author": {
            "name": user.get("nickname"),
            "handle": user.get("unique_id") or user.get("short_id"),
            "short_id": user.get("short_id"),
            "sec_uid": user.get("sec_uid"),
            "avatar_url": avatar_url,
        },
    }


def _normalize_douyin_comment_sort(sort_by: str) -> str:
    value = (sort_by or "likes").strip().lower()
    if value in {"likes", "like", "popular", "top"}:
        return "likes"
    if value in {"recent", "latest", "newest"}:
        return "recent"
    raise ValueError("sort_by 仅支持 likes 或 recent")


class BilibiliPlatformAdapter(PlatformAdapter):
    def can_handle(self, share_text: str) -> bool:
        lower = share_text.lower()
        return "bilibili.com" in lower or "b23.tv" in lower or _extract_bilibili_bvid(share_text) is not None

    def fetch_post(self, share_text: str) -> SocialPost:
        source_url = _extract_first_url_or_none(share_text)
        bvid = _extract_bilibili_bvid(share_text)
        resolved_url = source_url or (f"https://www.bilibili.com/video/{bvid}" if bvid else "")

        if source_url:
            response = requests.get(source_url, headers=HEADERS, timeout=30, allow_redirects=True)
            response.raise_for_status()
            resolved_url = response.url
            bvid = _extract_bilibili_bvid(resolved_url) or bvid

        if not bvid:
            raise ValueError("未找到 Bilibili BV 号")

        view_response = requests.get(
            "https://api.bilibili.com/x/web-interface/view",
            headers={**HEADERS, "Referer": resolved_url or f"https://www.bilibili.com/video/{bvid}"},
            params={"bvid": bvid},
            timeout=30,
        )
        view_response.raise_for_status()
        post = self.post_from_view_payload(
            view_response.json(),
            source_url=source_url or f"https://www.bilibili.com/video/{bvid}",
            resolved_url=resolved_url or f"https://www.bilibili.com/video/{bvid}",
        )
        self._enrich_video_access(post)
        return post

    @staticmethod
    def post_from_view_payload(payload: dict[str, Any], *, source_url: str, resolved_url: str) -> SocialPost:
        if payload.get("code") != 0:
            raise ValueError(f"Bilibili view API 返回异常: {payload}")

        data = payload.get("data") or {}
        bvid = data.get("bvid")
        if not bvid:
            raise ValueError("Bilibili view API 未返回 BV 号")

        owner = data.get("owner") or {}
        stat = data.get("stat") or {}
        mid = owner.get("mid")
        cover_url = _normalize_media_url(data.get("pic"))
        author_profile = {
            "id": str(mid) if mid is not None else None,
            "name": owner.get("name"),
            "handle": owner.get("name"),
            "avatar_url": _normalize_media_url(owner.get("face")),
            "profile_url": f"https://space.bilibili.com/{mid}" if mid is not None else None,
            "extra": owner,
        }
        public_metrics = {
            "views": stat.get("view"),
            "likes": stat.get("like"),
            "comments": stat.get("reply"),
            "shares": stat.get("share"),
            "favorites": stat.get("favorite"),
            "coins": stat.get("coin"),
            "danmaku": stat.get("danmaku"),
        }
        media = {
            "cover_url": cover_url,
            "image_urls": [cover_url] if cover_url else [],
            "video_url": None,
        }

        return SocialPost(
            platform="bilibili",
            content_type="video",
            source_url=source_url,
            resolved_url=resolved_url,
            post_id=bvid,
            title=(data.get("title") or bvid).strip(),
            body=(data.get("desc") or "").strip(),
            author_name=owner.get("name"),
            author_id=str(mid) if mid is not None else None,
            publish_time=data.get("pubdate"),
            cover_url=cover_url,
            duration_sec=data.get("duration"),
            video_url=None,
            image_urls=media["image_urls"],
            page_url=resolved_url,
            author_profile=author_profile,
            public_metrics=public_metrics,
            media=media,
            extra={
                "aid": data.get("aid"),
                "category": data.get("tname"),
                "copyright": data.get("copyright"),
                "pages": data.get("pages") or [],
            },
        )

    def _enrich_video_access(self, post: SocialPost) -> None:
        pages = post.extra.get("pages") or []
        first_page = pages[0] if pages and isinstance(pages[0], dict) else {}
        cid = first_page.get("cid")
        if not cid:
            return

        subtitle_text = self._fetch_subtitle_text(post.post_id, cid, post.page_url or post.resolved_url)
        if subtitle_text:
            post.extra["subtitle_text"] = subtitle_text

        streams = self._fetch_playable_streams(post.post_id, cid, post.page_url or post.resolved_url)
        if streams.get("video_url"):
            post.video_url = streams["video_url"]
            post.media["video_url"] = streams["video_url"]
        if streams.get("audio_url"):
            post.media["audio_url"] = streams["audio_url"]
            post.extra["audio_url"] = streams["audio_url"]

    def _fetch_subtitle_text(self, bvid: str, cid: Any, referer: str) -> Optional[str]:
        try:
            response = requests.get(
                "https://api.bilibili.com/x/player/v2",
                headers={**HEADERS, "Referer": referer},
                params={"bvid": bvid, "cid": cid},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            subtitles = (((payload.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
            for subtitle in subtitles:
                subtitle_url = subtitle.get("subtitle_url")
                if not subtitle_url:
                    continue
                subtitle_url = _normalize_media_url(_ensure_url_scheme(subtitle_url))
                subtitle_response = requests.get(subtitle_url, headers={**HEADERS, "Referer": referer}, timeout=30)
                subtitle_response.raise_for_status()
                body = subtitle_response.json().get("body") or []
                parts = [item.get("content", "").strip() for item in body if item.get("content")]
                if parts:
                    return "\n".join(parts)
        except Exception:
            return None
        return None

    def _fetch_playable_streams(self, bvid: str, cid: Any, referer: str) -> dict[str, Optional[str]]:
        streams: dict[str, Optional[str]] = {"video_url": None, "audio_url": None}
        try:
            response = requests.get(
                "https://api.bilibili.com/x/player/playurl",
                headers={**HEADERS, "Referer": referer},
                params={"bvid": bvid, "cid": cid, "qn": 16, "fnval": 16},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json().get("data") or {}
            dash = data.get("dash") or {}
            for item in dash.get("video") or []:
                url = item.get("baseUrl") or item.get("base_url")
                if url:
                    streams["video_url"] = _normalize_media_url(url)
                    break
            for item in dash.get("audio") or []:
                url = item.get("baseUrl") or item.get("base_url")
                if url:
                    streams["audio_url"] = _normalize_media_url(url)
                    break
            durl = data.get("durl") or []
            if not streams["video_url"] and durl and isinstance(durl[0], dict) and durl[0].get("url"):
                streams["video_url"] = _normalize_media_url(durl[0]["url"])
        except Exception:
            return streams
        return streams

class PlatformRouter:
    """Resolve exactly one public platform adapter for a shared URL."""

    def __init__(self, platform_adapters: Optional[list[PlatformAdapter]] = None) -> None:
        self.platform_adapters = platform_adapters or [
            DouyinPlatformAdapter(),
            XiaoHongShuPlatformAdapter(),
            BilibiliPlatformAdapter(),
        ]

    def parse(self, share_text: str) -> SocialPost:
        for adapter in self.platform_adapters:
            if adapter.can_handle(share_text):
                return adapter.fetch_post(share_text)
        raise ValueError("unsupported_platform")

    def get_douyin_comments_for_post(
        self,
        post: SocialPost,
        *,
        limit: int = DOUYIN_PUBLIC_COMMENT_LIMIT,
        sort_by: str = "likes",
    ) -> dict[str, Any]:
        if post.platform != "douyin":
            raise ValueError("Public comments are currently supported only for Douyin")
        result = fetch_douyin_public_comments(
            post.post_id,
            referer=post.page_url or post.resolved_url,
            limit=limit,
            sort_by=sort_by,
        )
        result.update(
            {
                "status": "success",
                "title": post.title,
                "author_name": post.author_name,
                "reported_comment_total": _coerce_int(post.public_metrics.get("comments")),
                "page_url": post.page_url,
            }
        )
        return result


def _first_existing(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data.get(key)
    return None


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _unix_time_iso(value: int) -> Optional[str]:
    if value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _first_media_url(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if value:
            return _normalize_media_url(value)
    return None


def _ensure_url_scheme(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return url


def _normalize_media_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return url
    url = _ensure_url_scheme(str(url).strip())
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _extract_first_url_or_none(text: str) -> Optional[str]:
    urls = re.findall(r"http[s]?://(?:[a-zA-Z0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+", text)
    return urls[0] if urls else None


def _extract_first_url(text: str) -> str:
    url = _extract_first_url_or_none(text)
    if not url:
        raise ValueError("未找到有效链接")
    return url


def _extract_title_from_share_text(text: str) -> Optional[str]:
    without_urls = re.sub(
        r"http[s]?://(?:[a-zA-Z0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
        " ",
        text,
    )
    for raw_line in without_urls.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" \t\r\n:-_")
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("copy and open"):
            continue
        if "复制" in line and ("小红书" in line or "rednote" in lower or "笔记" in line):
            continue
        if "来【小红书】看看" in line or "看看这篇笔记" in line:
            continue
        if len(line) < 2:
            continue
        return line[:120]
    return None


def _extract_html_title(html: str) -> Optional[str]:
    for pattern in (
        r'<meta[^>]+(?:property|name)=["\'](?:og:title|title)["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+(?:property|name)=["\'](?:og:title|title)["\']',
        r"<title>(.*?)</title>",
    ):
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        title = re.sub(r"\s*-\s*小红书\s*$", "", title).strip()
        title = re.split(r"\s+#", title, maxsplit=1)[0].strip()
        if title:
            return title[:160]
    return None


def _extract_bilibili_bvid(text: str) -> Optional[str]:
    match = re.search(r"\b(BV[0-9A-Za-z]{5,})\b", text)
    return match.group(1) if match else None


def _extract_xsec_token(url: str) -> Optional[str]:
    parsed = urlparse(url)
    token = parse_qs(parsed.query).get("xsec_token")
    return token[0] if token else None
