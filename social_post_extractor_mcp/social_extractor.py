#!/usr/bin/env python3
"""Unified social post extraction pipeline for Douyin and XiaoHongShu."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import asyncio
import gzip
import mimetypes
import time
import uuid
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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

DEFAULT_ASR_PROVIDER = "bailian"
DEFAULT_ASR_MODEL = "paraformer-v2"
DEFAULT_VISION_PROVIDER = "bailian"
DEFAULT_CLEAN_PROVIDER = "bailian"
DEFAULT_DASHSCOPE_SHORT_ASR_MODEL = "qwen3-asr-flash"
DEFAULT_DASHSCOPE_LONG_ASR_MODEL = "qwen3-asr-flash-filetrans"
DEFAULT_DASHSCOPE_SHORT_MAX_DURATION_SEC = 300
DOUYIN_CREATOR_OVERVIEW_LABELS = {
    "play": "播放量",
    "profile": "主页访问量",
    "digg": "作品点赞",
    "share": "作品分享",
    "comment": "作品评论",
    "new_fans": "净增粉丝",
    "fans": "粉丝相关",
    "cancel_fans": "取消关注",
    "account_search": "账号搜索",
    "post_search": "作品搜索",
    "music_create": "音乐使用",
}

VISION_PROMPT = (
    "请分析这张小红书图片，输出结构化文本：\n"
    "1. 可见文字：逐字提取图片里的文字，保持段落、列表、标题和换行。\n"
    "2. 画面信息：简要描述主体、场景、物品、人物动作和布局。\n"
    "3. 内容线索：列出对理解这篇笔记有帮助的视觉信息。\n"
    "不要编造图片里不存在的内容，不要做营销式润色。"
)

OCR_FALLBACK_PROMPT = (
    "请只做 OCR 抽字。逐字输出图片中的所有可见文字，保留顺序与换行。"
    "不要总结，不要改写，不要补全。"
)

CLEANUP_PROMPT_TEMPLATE = """你要对短视频/图文内容做轻量整理，只提升可读性，不改写内容。

要求：
1. 不能丢失信息，不能新增信息，不能做摘要。
2. 只允许做这几类处理：补标点、分段、修正极明显的错别字、去除重复空行。
3. 不要改写句子意思，不要重组结构，不要润色成另一种文风。
4. 保留原文中的口头表达、术语、清单和操作步骤。
5. 输出纯文本，不要加解释。

标题：{title}
平台：{platform}
内容类型：{content_type}

原笔记正文：
{body}

原始转写：
{raw_transcript}

图片文字：
{image_texts}
"""


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


@dataclass
class ArtifactPaths:
    script_path: Path
    info_path: Path


@dataclass
class ExtractionContext:
    asr_provider: Optional[str] = None
    asr_model: Optional[str] = None
    vision_provider: Optional[str] = None
    vision_model: Optional[str] = None
    clean_provider: Optional[str] = None
    clean_model: Optional[str] = None
    output_dir: Optional[Path] = None


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
        share_url = f"https://www.iesdouyin.com/share/video/{video_id}"

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
        cover_url = _normalize_media_url(cover_urls[0]) if cover_urls else None
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
        media = {
            "cover_url": cover_url,
            "image_urls": [cover_url] if cover_url else [],
            "video_url": video_url,
        }

        return SocialPost(
            platform="douyin",
            content_type="video",
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
            video_url=video_url,
            image_urls=media["image_urls"],
            page_url=share_url,
            author_profile=author_profile,
            public_metrics=public_metrics,
            media=media,
            extra={"aweme_type": item.get("aweme_type"), "statistics": statistics},
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

        media_url = self._fetch_playable_audio_or_video_url(post.post_id, cid, post.page_url or post.resolved_url)
        if media_url:
            post.video_url = media_url
            post.media["video_url"] = media_url

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

    def _fetch_playable_audio_or_video_url(self, bvid: str, cid: Any, referer: str) -> Optional[str]:
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
            for item in dash.get("audio") or []:
                url = item.get("baseUrl") or item.get("base_url")
                if url:
                    return _normalize_media_url(url)
            for item in dash.get("video") or []:
                url = item.get("baseUrl") or item.get("base_url")
                if url:
                    return _normalize_media_url(url)
            durl = data.get("durl") or []
            if durl and isinstance(durl[0], dict) and durl[0].get("url"):
                return _normalize_media_url(durl[0]["url"])
        except Exception:
            return None
        return None


class SocialExtractorService:
    def __init__(
        self,
        platform_adapters: Optional[list[PlatformAdapter]] = None,
        asr_providers: Optional[dict[str, Any]] = None,
        cleanup_providers: Optional[dict[str, Any]] = None,
        vision_providers: Optional[dict[str, Any]] = None,
        ocr_provider: Any = None,
    ):
        self.platform_adapters = platform_adapters if platform_adapters is not None else [
            DouyinPlatformAdapter(),
            XiaoHongShuPlatformAdapter(),
            BilibiliPlatformAdapter(),
        ]
        self.asr_providers = asr_providers if asr_providers is not None else {
            "siliconflow": SiliconFlowASRProvider(),
            "dashscope": DashScopeASRProvider(),
            "bailian": DashScopeASRProvider(),
            "doubao": OpenAICompatibleASRProvider("doubao"),
            "volcengine_speech": VolcengineSpeechASRProvider(),
        }
        self.cleanup_providers = cleanup_providers if cleanup_providers is not None else build_llm_provider_registry()
        self.vision_providers = vision_providers if vision_providers is not None else build_llm_provider_registry()
        self.ocr_provider = ocr_provider or LLMOcrProvider(self.vision_providers)

    def parse_social_post(self, share_text: str) -> SocialPost:
        adapter = self._resolve_platform_adapter(share_text)
        return adapter.fetch_post(share_text)

    def get_douyin_comments(
        self,
        share_text: str,
        *,
        limit: int = DOUYIN_PUBLIC_COMMENT_LIMIT,
        sort_by: str = "likes",
    ) -> dict[str, Any]:
        post = self.parse_social_post(share_text)
        if post.platform != "douyin":
            raise ValueError("评论工具目前仅支持抖音链接")

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

    def extract_social_post(
        self,
        share_text: str,
        *,
        output_dir: Optional[str] = None,
        asr_provider: Optional[str] = None,
        asr_model: Optional[str] = None,
        vision_provider: Optional[str] = None,
        vision_model: Optional[str] = None,
        clean_provider: Optional[str] = None,
        clean_model: Optional[str] = None,
        save_raw_segments: bool = False,
    ) -> dict[str, Any]:
        post = self.parse_social_post(share_text)
        resolved_asr_provider = asr_provider or os.getenv("ASR_PROVIDER") or DEFAULT_ASR_PROVIDER
        resolved_vision_provider = vision_provider or os.getenv("VISION_PROVIDER") or self._default_vision_provider()
        if resolved_vision_provider and resolved_vision_provider not in self.vision_providers:
            resolved_vision_provider = None
        resolved_clean_provider = clean_provider or os.getenv("CLEAN_PROVIDER") or self._default_cleanup_provider()
        if resolved_clean_provider and resolved_clean_provider not in self.cleanup_providers:
            resolved_clean_provider = None
        context = ExtractionContext(
            asr_provider=resolved_asr_provider,
            asr_model=(
                asr_model
                or os.getenv("ASR_MODEL")
                or default_model_for_provider(resolved_asr_provider, "asr")
                or DEFAULT_ASR_MODEL
            ),
            vision_provider=resolved_vision_provider,
            vision_model=None,
            clean_provider=resolved_clean_provider,
            clean_model=None,
        )
        context.vision_model = (
            vision_model
            or os.getenv("VISION_MODEL")
            or (
                default_model_for_provider(context.vision_provider, "vision")
                if context.vision_provider
                else None
            )
        )
        context.clean_model = (
            clean_model
            or os.getenv("CLEAN_MODEL")
            or (
                default_model_for_provider(context.clean_provider, "cleanup")
                if context.clean_provider
                else None
            )
        )

        raw_transcript = None
        transcript_source = None
        image_texts: list[str] = []
        errors: list[str] = []

        if post.content_type == "video":
            platform_subtitle = post.extra.get("subtitle_text")
            if platform_subtitle:
                raw_transcript = platform_subtitle
                transcript_source = "platform_subtitle"
            else:
                try:
                    raw_transcript = self._extract_video_text(post, context)
                    transcript_source = "cloud_asr"
                except Exception as exc:
                    raise RuntimeError(f"视频转写失败: {exc}") from exc
        else:
            image_texts, image_errors = self._extract_image_texts(post, context)
            errors.extend(image_errors)

        cleaned_script = self._cleanup_content(post, raw_transcript, image_texts, context, errors)
        status = "success"
        if errors:
            status = "partial_success"
        if not cleaned_script and not raw_transcript and not image_texts and not post.body:
            status = "failed"

        artifact_dir = self._prepare_output_dir(output_dir, post)
        paths = self._write_artifacts(
            post=post,
            context=context,
            artifact_dir=artifact_dir,
            cleaned_script=cleaned_script,
            raw_transcript=raw_transcript,
            transcript_source=transcript_source,
            image_texts=image_texts,
            status=status,
            errors=errors,
        )

        info = json.loads(paths.info_path.read_text(encoding="utf-8"))
        return {
            "platform": post.platform,
            "content_type": post.content_type,
            "post_id": post.post_id,
            "script_path": str(paths.script_path),
            "info_path": str(paths.info_path),
            "script_preview": cleaned_script or raw_transcript or "\n\n".join(image_texts) or post.body,
            "info": info,
            "raw_transcript": raw_transcript,
        }

    def _resolve_platform_adapter(self, share_text: str) -> PlatformAdapter:
        for adapter in self.platform_adapters:
            if adapter.can_handle(share_text):
                return adapter
        raise ValueError("unsupported_platform")

    def _extract_video_text(self, post: SocialPost, context: ExtractionContext) -> str:
        provider_name = context.asr_provider or DEFAULT_ASR_PROVIDER
        provider = self.asr_providers.get(provider_name)
        if not provider:
            raise ValueError(f"未配置 ASR provider: {provider_name}")
        return provider.transcribe(post, context)

    def _extract_image_texts(self, post: SocialPost, context: ExtractionContext) -> tuple[list[str], list[str]]:
        texts: list[str] = []
        errors: list[str] = []
        primary = self.vision_providers.get(context.vision_provider or "") if context.vision_provider else None
        for index, image_url in enumerate(post.image_urls, start=1):
            try:
                if primary:
                    text = primary.read_image_text(image_url, context, index)
                elif self.ocr_provider:
                    text = self.ocr_provider.read_image_text(image_url, context, index)
                else:
                    raise RuntimeError("未配置 vision provider")
                texts.append(text.strip())
            except Exception as vision_error:
                try:
                    if not self.ocr_provider:
                        raise
                    fallback_text = self.ocr_provider.read_image_text(image_url, context, index)
                    texts.append(fallback_text.strip())
                except Exception as ocr_error:
                    errors.append(
                        f"图片 {index} 提取失败: vision={vision_error}; ocr={ocr_error}"
                    )
        return texts, errors

    def _cleanup_content(
        self,
        post: SocialPost,
        raw_transcript: Optional[str],
        image_texts: list[str],
        context: ExtractionContext,
        errors: list[str],
    ) -> str:
        if context.clean_provider:
            provider = self.cleanup_providers.get(context.clean_provider)
            if provider:
                try:
                    return provider.cleanup(post, raw_transcript, image_texts, context).strip()
                except Exception as exc:
                    errors.append(f"整理失败: {exc}")
        return rule_based_cleanup(post, raw_transcript, image_texts)

    def _prepare_output_dir(self, output_dir: Optional[str], post: SocialPost) -> Path:
        base_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir()) / "social-post-extract"
        artifact_dir = base_dir / f"{post.platform}_{post.content_type}_{post.post_id}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir

    def _write_artifacts(
        self,
        *,
        post: SocialPost,
        context: ExtractionContext,
        artifact_dir: Path,
        cleaned_script: str,
        raw_transcript: Optional[str],
        transcript_source: Optional[str],
        image_texts: list[str],
        status: str,
        errors: list[str],
    ) -> ArtifactPaths:
        script_path = artifact_dir / "script.md"
        info_path = artifact_dir / "info.json"

        script_text = build_script_markdown(
            post=post,
            cleaned_script=cleaned_script,
            raw_transcript=raw_transcript,
            image_texts=image_texts,
        )
        script_path.write_text(script_text, encoding="utf-8")

        info = build_info_dict(
            post,
            context,
            status=status,
            errors=errors,
            transcript_text=raw_transcript,
            transcript_source=transcript_source,
            image_analysis=image_texts,
        )
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return ArtifactPaths(script_path=script_path, info_path=info_path)

    def _default_vision_provider(self) -> Optional[str]:
        if os.getenv("VISION_PROVIDER"):
            return os.getenv("VISION_PROVIDER")
        for candidate in ("bailian", "qwen", "doubao", "minimax", "generic"):
            if provider_credentials(candidate):
                return candidate
        return None

    def _default_cleanup_provider(self) -> Optional[str]:
        if os.getenv("CLEAN_PROVIDER"):
            return os.getenv("CLEAN_PROVIDER")
        for candidate in ("bailian", "qwen", "doubao", "minimax", "generic"):
            if provider_credentials(candidate):
                return candidate
        return None


class SiliconFlowASRProvider:
    def __init__(self, api_base_url: Optional[str] = None):
        self.api_base_url = api_base_url or os.getenv(
            "SILICONFLOW_ASR_BASE_URL", "https://api.siliconflow.cn/v1/audio/transcriptions"
        )

    def transcribe(self, post: SocialPost, context: ExtractionContext) -> str:
        api_key = first_env("SILICONFLOW_API_KEY", "API_KEY")
        if not api_key:
            raise ValueError("未设置 SiliconFlow API Key")
        if not post.video_url:
            raise ValueError("视频内容缺少 video_url，无法进行 ASR")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            video_path = download_binary(post.video_url, tmp_path / f"{post.post_id}.mp4")
            audio_path = extract_audio(video_path, tmp_path / f"{post.post_id}.mp3")
            return transcribe_audio_via_upload(
                audio_path=audio_path,
                api_url=self.api_base_url,
                api_key=api_key,
                model=context.asr_model or DEFAULT_ASR_MODEL,
            )


class DashScopeASRProvider:
    def transcribe(self, post: SocialPost, context: ExtractionContext) -> str:
        api_key = first_env("DASHSCOPE_API_KEY", "BAILIAN_API_KEY")
        if not api_key:
            raise ValueError("未设置 DashScope/Bailian API Key")
        if not post.video_url:
            raise ValueError("视频内容缺少 video_url，无法调用 DashScope ASR")
        requested_model = _normalize_dashscope_requested_model(context.asr_model)
        preferred_model = requested_model or _select_dashscope_cloud_asr_model(post)

        try:
            transcript = self._transcribe_via_cloud_mirror(post, api_key=api_key, model=preferred_model)
            context.asr_model = preferred_model
            return transcript
        except Exception:
            # Short-form realtime ASR has tighter limits. If the auto path picked it,
            # retry once with the long-file async model for better robustness.
            if requested_model is None and preferred_model == DEFAULT_DASHSCOPE_SHORT_ASR_MODEL:
                fallback_model = DEFAULT_DASHSCOPE_LONG_ASR_MODEL
                transcript = self._transcribe_via_cloud_mirror(post, api_key=api_key, model=fallback_model)
                context.asr_model = fallback_model
                return transcript
            raise

    def _transcribe_via_cloud_mirror(self, post: SocialPost, *, api_key: str, model: str) -> str:
        oss_url = stream_remote_media_to_dashscope_oss(
            source_url=post.video_url,
            api_key=api_key,
            model_name=model,
            filename_hint=_default_dashscope_media_filename(post),
            referer_url=post.page_url or post.resolved_url,
        )
        if model == DEFAULT_DASHSCOPE_SHORT_ASR_MODEL:
            return run_dashscope_multimodal_asr(
                oss_url=oss_url,
                api_key=api_key,
                model=model,
            )
        return run_dashscope_filetrans_task(
            oss_url=oss_url,
            api_key=api_key,
            model=model,
        )


class OpenAICompatibleASRProvider:
    def __init__(self, provider_name: str):
        self.provider_name = provider_name

    def transcribe(self, post: SocialPost, context: ExtractionContext) -> str:
        config = provider_asr_config(self.provider_name)
        if not config:
            raise ValueError(f"provider {self.provider_name} 未配置 ASR 凭据")
        if not post.video_url:
            raise ValueError("视频内容缺少 video_url，无法进行 ASR")

        model = context.asr_model or default_model_for_provider(self.provider_name, "asr") or DEFAULT_ASR_MODEL

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                video_path = download_binary(post.video_url, tmp_path / f"{post.post_id}.mp4")
                audio_path = extract_audio(video_path, tmp_path / f"{post.post_id}.mp3")
                return transcribe_audio_via_upload(
                    audio_path=audio_path,
                    api_url=config["api_url"],
                    api_key=config["api_key"],
                    model=model,
                )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                raise RuntimeError(
                    f"{self.provider_name} ASR endpoint 不存在: {config['api_url']}。"
                    f"请配置 {provider_asr_url_env_name(self.provider_name)} 指向当前账号可用的官方转写接口。"
                ) from exc
            raise


class VolcengineSpeechASRProvider:
    def transcribe(self, post: SocialPost, context: ExtractionContext) -> str:
        config = provider_volcengine_speech_config()
        if not config:
            raise ValueError("未设置火山语音识别所需的 App ID / Access Token")
        if not post.video_url:
            raise ValueError("视频内容缺少 video_url，无法进行火山语音识别")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            video_path = download_binary(post.video_url, tmp_path / f"{post.post_id}.mp4")
            audio_path = extract_wav_audio(video_path, tmp_path / f"{post.post_id}.wav")
            return run_async(
                _transcribe_via_volcengine_websocket(
                    audio_path=audio_path,
                    config=config,
                    context=context,
                    user_id=post.author_id or post.post_id,
                )
            )


def run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("检测到已运行的事件循环，当前同步 provider 无法直接调用火山语音识别")


class OpenAICompatibleProvider:
    def __init__(self, provider_name: str, *, mode: str):
        self.provider_name = provider_name
        self.mode = mode

    def cleanup(self, post: SocialPost, raw_transcript: Optional[str], image_texts: list[str], context: ExtractionContext) -> str:
        model = context.clean_model or default_model_for_provider(self.provider_name, "cleanup")
        prompt = CLEANUP_PROMPT_TEMPLATE.format(
            title=post.title,
            platform=post.platform,
            content_type=post.content_type,
            body=post.body or "(无)",
            raw_transcript=raw_transcript or "(无)",
            image_texts="\n\n".join(image_texts) or "(无)",
        )
        return self._chat_text(prompt=prompt, model=model)

    def read_image_text(self, image_url: str, context: ExtractionContext, image_index: int) -> str:
        model = context.vision_model or default_model_for_provider(self.provider_name, "vision")
        prompt = VISION_PROMPT if self.mode == "vision" else OCR_FALLBACK_PROMPT
        return self._chat_vision(prompt=prompt, image_url=image_url, model=model)

    def _chat_text(self, prompt: str, model: str) -> str:
        config = provider_credentials(self.provider_name)
        if not config:
            raise ValueError(f"provider {self.provider_name} 未配置 API 凭据")
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        response = requests.post(
            f"{config['base_url'].rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        return extract_message_text(response.json())

    def _chat_vision(self, prompt: str, image_url: str, model: str) -> str:
        config = provider_credentials(self.provider_name)
        if not config:
            raise ValueError(f"provider {self.provider_name} 未配置 API 凭据")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _normalize_media_url(image_url)}},
                    ],
                }
            ],
        }
        response = requests.post(
            f"{config['base_url'].rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        return extract_message_text(response.json())


class LLMOcrProvider:
    def __init__(self, vision_providers: dict[str, Any]):
        self.vision_providers = vision_providers

    def read_image_text(self, image_url: str, context: ExtractionContext, image_index: int) -> str:
        provider_name = context.vision_provider or "qwen"
        provider = self.vision_providers.get(provider_name)
        if not provider:
            raise RuntimeError("未配置 OCR fallback provider")
        original_mode = getattr(provider, "mode", None)
        if original_mode is not None:
            provider.mode = "ocr"
        try:
            return provider.read_image_text(image_url, context, image_index)
        finally:
            if original_mode is not None:
                provider.mode = original_mode


class OwnerAnalyticsCommandProvider:
    """Wrap browser-backed CLIs for creator-center and own-account analytics."""

    def __init__(self, *, opencli_command: str = "opencli", bb_browser_command: str = "bb-browser"):
        self.opencli_command = opencli_command
        self.bb_browser_command = bb_browser_command

    def build_command(
        self,
        platform: str,
        report_type: str,
        *,
        limit: Optional[int] = None,
        post_id: Optional[str] = None,
        account_id: Optional[str] = None,
        account_name: Optional[str] = None,
        period: Optional[str] = None,
    ) -> list[str]:
        platform = platform.lower().strip()
        report_type = report_type.lower().strip()
        limit_arg = str(limit or 10)

        if platform in {"xiaohongshu", "xhs", "rednote"}:
            if report_type in {"recent_posts", "posts", "summary"}:
                return [
                    self.opencli_command,
                    "xiaohongshu",
                    "creator-notes-summary",
                    "--limit",
                    limit_arg,
                    "-f",
                    "json",
                ]
            if report_type in {"post_detail", "detail", "stats"}:
                if not post_id:
                    raise ValueError("小红书单篇复盘需要 post_id")
                return [
                    self.opencli_command,
                    "xiaohongshu",
                    "creator-note-detail",
                    post_id,
                    "-f",
                    "json",
                ]
            if report_type in {"profile", "account"}:
                return [self.opencli_command, "xiaohongshu", "creator-profile", "-f", "json"]
            if report_type in {"account_stats", "creator_stats"}:
                command = [self.opencli_command, "xiaohongshu", "creator-stats"]
                if period:
                    command.extend(["--period", period])
                return command + ["-f", "json"]

        if platform in {"douyin", "tiktok_cn"}:
            if report_type in {"recent_posts", "posts", "videos"}:
                return [self.opencli_command, "douyin", "videos", "--limit", limit_arg, "-f", "json"]
            if report_type in {"post_detail", "detail", "stats"}:
                if not post_id:
                    raise ValueError("抖音单条复盘需要 post_id")
                return [self.opencli_command, "douyin", "stats", post_id, "-f", "json"]
            if report_type in {"profile", "account"}:
                return [self.opencli_command, "douyin", "profile", "-f", "json"]
            if report_type in {"account_stats", "creator_stats"}:
                return [self.opencli_command, "douyin", "profile", "-f", "json"]

        if platform in {"bilibili", "bili"}:
            if report_type in {"profile", "account"}:
                return [self.opencli_command, "bilibili", "me", "-f", "json"]
            if report_type in {"search_user", "user_search"}:
                query = account_name or post_id
                if not query:
                    raise ValueError("Bilibili 用户搜索需要 account_name")
                return [
                    self.opencli_command,
                    "bilibili",
                    "search",
                    query,
                    "--type",
                    "user",
                    "--limit",
                    limit_arg,
                    "-f",
                    "json",
                ]
            if report_type in {"recent_posts", "posts", "videos", "user_videos"}:
                uid = account_id or post_id
                if not uid:
                    raise ValueError("Bilibili 投稿列表需要 account_id 或 uid")
                return [
                    self.opencli_command,
                    "bilibili",
                    "user-videos",
                    uid,
                    "--limit",
                    limit_arg,
                    "-f",
                    "json",
                ]
            if report_type in {"post_detail", "detail", "stats", "video"}:
                if not post_id:
                    raise ValueError("Bilibili 单条复盘需要 BV 号或视频 ID")
                return [self.bb_browser_command, "site", "bilibili/video", post_id, "--json"]

        raise ValueError(f"暂不支持 {platform}/{report_type} 的账号复盘命令")

    def run(
        self,
        platform: str,
        report_type: str,
        *,
        limit: Optional[int] = None,
        post_id: Optional[str] = None,
        account_id: Optional[str] = None,
        account_name: Optional[str] = None,
        period: Optional[str] = None,
        timeout: int = 120,
    ) -> dict[str, Any]:
        command = self.build_command(
            platform,
            report_type,
            limit=limit,
            post_id=post_id,
            account_id=account_id,
            account_name=account_name,
            period=period,
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = completed.stdout.strip()
        if completed.returncode != 0:
            error = (completed.stderr or output or "账号复盘命令执行失败").strip()
            if platform.lower().strip() in {"douyin", "tiktok_cn"}:
                return self._run_douyin_creator_fallback(
                    report_type,
                    limit=limit,
                    post_id=post_id,
                    period=period,
                    timeout=timeout,
                    original_command=command,
                    original_error=error,
                )
            raise RuntimeError(error)
        try:
            data = json.loads(output) if output else None
        except json.JSONDecodeError:
            data = {"text": output}
        if platform.lower().strip() in {"douyin", "tiktok_cn"} and _douyin_opencli_result_needs_fallback(
            report_type,
            data,
        ):
            return self._run_douyin_creator_fallback(
                report_type,
                limit=limit,
                post_id=post_id,
                period=period,
                timeout=timeout,
                original_command=command,
                original_error="OpenCLI returned an empty or legacy-shaped Douyin result",
            )
        return {
            "status": "success",
            "platform": platform,
            "report_type": report_type,
            "command": command,
            "data": data,
        }

    def _run_douyin_creator_fallback(
        self,
        report_type: str,
        *,
        limit: Optional[int],
        post_id: Optional[str],
        period: Optional[str],
        timeout: int,
        original_command: list[str],
        original_error: str,
    ) -> dict[str, Any]:
        """Read Douyin creator data from a same-origin OpenCLI daemon tab.

        OpenCLI 1.5.x Douyin adapters evaluate fetches from a blank data: tab and
        also expect older response field names. The fallback navigates the
        automation tab to creator.douyin.com first, then returns raw JSON text so
        Python can parse 19-digit aweme IDs without JavaScript precision loss.
        """

        tab_id = self._opencli_daemon_navigate(
            "https://creator.douyin.com/creator-micro/home",
            timeout=timeout,
        )
        report_type = report_type.lower().strip()

        if report_type in {"profile", "account"}:
            payload = self._douyin_creator_fetch_json(
                "https://creator.douyin.com/web/api/media/user/info/?aid=1128",
                tab_id=tab_id,
                timeout=timeout,
            )
            data = parse_douyin_creator_profile(payload)
        elif report_type in {"recent_posts", "posts", "videos"}:
            payload = self._douyin_creator_fetch_json(
                _douyin_creator_item_list_url(limit or 10),
                tab_id=tab_id,
                timeout=timeout,
            )
            data = parse_douyin_creator_items(payload, limit=limit or 10)
        elif report_type in {"post_detail", "detail", "stats"}:
            if not post_id:
                raise ValueError("抖音单条复盘需要 post_id")
            payload = self._douyin_creator_fetch_json(
                _douyin_creator_item_list_url(max(limit or 50, 50)),
                tab_id=tab_id,
                timeout=timeout,
            )
            data = parse_douyin_creator_post_detail(payload, post_id=post_id)
        elif report_type in {"account_stats", "creator_stats", "summary"}:
            days_type = "2" if period in {"30", "30d", "month", "近30日"} else "1"
            payload = self._douyin_creator_fetch_json(
                f"https://creator.douyin.com/aweme/janus/creator/data/overview/all/?last_days_type={days_type}",
                tab_id=tab_id,
                timeout=timeout,
            )
            data = parse_douyin_creator_overview(payload)
        else:
            raise ValueError(f"暂不支持 douyin/{report_type} 的账号复盘命令")

        return {
            "status": "success",
            "platform": "douyin",
            "report_type": report_type,
            "command": original_command,
            "source": "opencli_daemon_same_origin_fallback",
            "fallback_from_error": original_error,
            "data": data,
        }

    def _opencli_daemon_navigate(self, url: str, *, timeout: int) -> int:
        status = self._opencli_daemon_get("/status", timeout=timeout)
        if not status.get("extensionConnected"):
            raise RuntimeError("OpenCLI daemon 已启动，但浏览器扩展未连接")
        data = self._opencli_daemon_command(
            "navigate",
            timeout=timeout,
            url=url,
        )
        tab_id = data.get("tabId")
        if tab_id is None:
            raise RuntimeError("OpenCLI daemon 未返回 tabId")
        return int(tab_id)

    def _douyin_creator_fetch_json(self, url: str, *, tab_id: int, timeout: int) -> dict[str, Any]:
        code = (
            "(async () => {"
            f"const r = await fetch({json.dumps(url)}, {{credentials: 'include'}});"
            "const text = await r.text();"
            "return {ok: r.ok, status: r.status, text};"
            "})()"
        )
        result = self._opencli_daemon_command(
            "exec",
            timeout=timeout,
            tabId=tab_id,
            code=code,
        )
        if not result.get("ok"):
            raise RuntimeError(f"抖音创作者中心接口请求失败: HTTP {result.get('status')}")
        text = result.get("text") or ""
        try:
            return json.loads(text, parse_int=str)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"抖音创作者中心接口返回非 JSON: {text[:200]}") from exc

    def _opencli_daemon_get(self, path: str, *, timeout: int) -> dict[str, Any]:
        daemon_url = self._opencli_daemon_url()
        response = requests.get(
            f"{daemon_url}{path}",
            headers={"X-OpenCLI": "1"},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def _opencli_daemon_command(self, action: str, *, timeout: int, **params: Any) -> dict[str, Any]:
        daemon_url = self._opencli_daemon_url()
        payload = {
            "id": f"social_post_extractor_{uuid.uuid4().hex}",
            "action": action,
            **params,
        }
        response = requests.post(
            f"{daemon_url}/command",
            headers={"Content-Type": "application/json", "X-OpenCLI": "1"},
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "OpenCLI daemon command failed")
        return result.get("data") or {}

    @staticmethod
    def _opencli_daemon_url() -> str:
        port = os.getenv("OPENCLI_DAEMON_PORT", "19825")
        return f"http://127.0.0.1:{port}"


def _douyin_creator_item_list_url(limit: int) -> str:
    return (
        "https://creator.douyin.com/web/api/creator/item/list"
        f"?count={int(limit)}"
        "&fields=visibility%2Cmetrics%2Creview"
        "&status_list%5B%5D=102"
        "&status_list%5B%5D=143"
        "&need_long_article=true"
    )


def _douyin_opencli_result_needs_fallback(report_type: str, data: Any) -> bool:
    report_type = report_type.lower().strip()
    if report_type in {"recent_posts", "posts", "videos"}:
        return data == [] or data is None
    if report_type in {"profile", "account"}:
        return data == [] or data is None
    if report_type in {"post_detail", "detail", "stats"}:
        return data == [] or data is None
    return False


def parse_douyin_creator_profile(payload: dict[str, Any]) -> list[dict[str, Any]]:
    user = payload.get("user") or payload.get("user_info") or {}
    if not isinstance(user, dict) or not user:
        raise RuntimeError("抖音创作者中心未返回用户信息")
    return [
        {
            "uid": user.get("uid") or user.get("sec_uid"),
            "nickname": user.get("nickname"),
            "unique_id": user.get("unique_id"),
            "signature": user.get("signature"),
            "follower_count": user.get("follower_count"),
            "following_count": user.get("following_count"),
            "aweme_count": user.get("aweme_count"),
            "favoriting_count": user.get("favoriting_count"),
            "total_favorited": user.get("total_favorited"),
        }
    ]


def parse_douyin_creator_items(payload: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    raw_items = payload.get("items") or payload.get("aweme_list") or []
    if not isinstance(raw_items, list):
        raw_items = []
    rows = []
    for item in raw_items[:limit]:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") or item.get("statistics") or {}
        post_id = item.get("id") or item.get("aweme_id")
        if post_id is not None:
            post_id = str(post_id)
        rows.append(
            {
                "post_id": post_id,
                "title": item.get("description") or item.get("desc") or "",
                "create_time": item.get("create_time"),
                "created_at": _format_epoch_seconds(item.get("create_time")),
                "url": f"https://www.douyin.com/video/{post_id}" if post_id else None,
                "cover_url": _first_media_url(*(((item.get("cover") or item.get("Cover") or {}).get("url_list")) or [])),
                "visibility": item.get("visibility"),
                "review": item.get("review"),
                "metrics": normalize_douyin_creator_metrics(metrics),
            }
        )
    return rows


def parse_douyin_creator_post_detail(payload: dict[str, Any], *, post_id: str) -> dict[str, Any]:
    rows = parse_douyin_creator_items(payload, limit=10_000)
    for row in rows:
        if row.get("post_id") == str(post_id):
            return row
    raise RuntimeError(f"抖音创作者中心列表中未找到作品 {post_id}")


def parse_douyin_creator_overview(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") or {}
    metrics = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if not isinstance(value, dict) or "current_count" not in value:
                continue
            metrics[key] = {
                "label": DOUYIN_CREATOR_OVERVIEW_LABELS.get(key, key),
                "current_count": value.get("current_count"),
                "last_period_incr": value.get("last_period_incr"),
                "trend": value.get("option_list") or [],
            }
    summary = {key: value.get("current_count") for key, value in metrics.items()}
    return {"summary": summary, "metrics": metrics}


def normalize_douyin_creator_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {}
    return {
        "views": _first_existing(metrics, "view_count", "play_count"),
        "likes": _first_existing(metrics, "like_count", "digg_count"),
        "comments": _first_existing(metrics, "comment_count"),
        "shares": _first_existing(metrics, "share_count"),
        "impressions": _first_existing(metrics, "cover_show"),
        "favorites": _first_existing(metrics, "favorite_count", "collect_count"),
        "downloads": _first_existing(metrics, "download_count"),
        "danmaku": _first_existing(metrics, "danmaku_count"),
        "homepage_visits": _first_existing(metrics, "homepage_visit_count"),
        "followers_gained": _first_existing(metrics, "subscribe_count"),
        "followers_lost": _first_existing(metrics, "unsubscribe_count"),
        "avg_view_seconds": _first_existing(metrics, "avg_view_second"),
        "avg_view_proportion": _first_existing(metrics, "avg_view_proportion"),
        "completion_rate": _first_existing(metrics, "completion_rate"),
        "completion_rate_5s": _first_existing(metrics, "completion_rate_5s"),
        "bounce_rate_2s": _first_existing(metrics, "bounce_rate_2s"),
        "cover_show": _first_existing(metrics, "cover_show"),
        "cover_click_rate": _first_existing(metrics, "cover_click_rate"),
        "raw": metrics,
    }


def _format_epoch_seconds(value: Any) -> Optional[str]:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone().isoformat()


def rule_based_cleanup(post: SocialPost, raw_transcript: Optional[str], image_texts: list[str]) -> str:
    parts = []
    if post.body:
        parts.append(post.body.strip())
    if raw_transcript:
        parts.append(raw_transcript.strip())
    if image_texts:
        parts.append("\n\n".join(text.strip() for text in image_texts if text.strip()))
    return "\n\n".join(part for part in parts if part).strip()


def build_script_markdown(
    *,
    post: SocialPost,
    cleaned_script: str,
    raw_transcript: Optional[str],
    image_texts: list[str],
) -> str:
    lines = [
        f"# {post.title}",
        "",
        f"- 平台: {post.platform}",
        f"- 类型: {post.content_type}",
        f"- ID: `{post.post_id}`",
        f"- 原链接: {post.source_url}",
    ]
    if post.author_name:
        lines.append(f"- 作者: {post.author_name}")
    if post.page_url:
        lines.append(f"- 页面: {post.page_url}")
    lines.append("")
    lines.append("## 整理稿")
    lines.append("")
    lines.append(cleaned_script or "（无整理稿）")
    if post.body:
        lines.extend(["", "## 原笔记正文", "", post.body])
    if raw_transcript:
        lines.extend(["", "## 原始转写", "", raw_transcript])
    if image_texts:
        lines.extend(["", "## 图片文字提取", ""])
        for index, text in enumerate(image_texts, start=1):
            lines.extend([f"### 图片 {index}", "", text or "（无内容）", ""])
    return "\n".join(lines).rstrip() + "\n"


def build_info_dict(
    post: SocialPost,
    context: ExtractionContext,
    *,
    status: str,
    errors: list[str],
    transcript_text: Optional[str] = None,
    transcript_source: Optional[str] = None,
    image_analysis: Optional[list[str]] = None,
) -> dict[str, Any]:
    author = {
        "name": post.author_name,
        "id": post.author_id,
        **(post.author_profile or {}),
    }
    media = {
        "cover_url": post.cover_url,
        "image_urls": post.image_urls,
        "video_url": post.video_url,
        **(post.media or {}),
    }
    return {
        "platform": post.platform,
        "content_type": post.content_type,
        "source_url": post.source_url,
        "resolved_url": post.resolved_url,
        "post_id": post.post_id,
        "title": post.title,
        "body": post.body,
        "author": author,
        "public_metrics": post.public_metrics,
        "owner_metrics": post.owner_metrics,
        "media": media,
        "transcript": {
            "source": transcript_source,
            "text": transcript_text,
        },
        "image_analysis": image_analysis or [],
        "publish_time": post.publish_time,
        "cover_url": post.cover_url,
        "duration_sec": post.duration_sec,
        "video_url": post.video_url,
        "media_urls": post.image_urls if post.content_type == "image_note" else ([post.video_url] if post.video_url else []),
        "page_url": post.page_url,
        "xsec_token": post.xsec_token,
        "tags": post.tags,
        "asr_provider": context.asr_provider,
        "asr_model": context.asr_model,
        "vision_provider": context.vision_provider,
        "vision_model": context.vision_model,
        "clean_provider": context.clean_provider,
        "clean_model": context.clean_model,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "error": "\n".join(errors) if errors else None,
        "extra": post.extra,
    }


def _normalize_dashscope_requested_model(model: Optional[str]) -> Optional[str]:
    if model in (DEFAULT_DASHSCOPE_SHORT_ASR_MODEL, DEFAULT_DASHSCOPE_LONG_ASR_MODEL):
        return model
    return None


def _select_dashscope_cloud_asr_model(post: SocialPost) -> str:
    short_model = first_env("DASHSCOPE_SHORT_ASR_MODEL", "BAILIAN_SHORT_ASR_MODEL") or DEFAULT_DASHSCOPE_SHORT_ASR_MODEL
    long_model = first_env("DASHSCOPE_LONG_ASR_MODEL", "BAILIAN_LONG_ASR_MODEL") or DEFAULT_DASHSCOPE_LONG_ASR_MODEL
    max_short_duration = int(
        first_env("DASHSCOPE_SHORT_MAX_DURATION_SEC", "BAILIAN_SHORT_MAX_DURATION_SEC")
        or DEFAULT_DASHSCOPE_SHORT_MAX_DURATION_SEC
    )
    if post.duration_sec and post.duration_sec <= max_short_duration:
        return short_model
    return long_model


def _default_dashscope_media_filename(post: SocialPost) -> str:
    parsed = urlparse(post.video_url or "")
    basename = Path(parsed.path).name
    if basename and "." in basename:
        return basename
    if "video_id" in parse_qs(parsed.query):
        return f"{parse_qs(parsed.query)['video_id'][0]}.mp4"
    return f"{post.post_id}.mp4"


def build_llm_provider_registry() -> dict[str, OpenAICompatibleProvider]:
    registry: dict[str, OpenAICompatibleProvider] = {}
    for provider_name in ("minimax", "qwen", "bailian", "doubao", "generic"):
        registry[provider_name] = OpenAICompatibleProvider(provider_name, mode="vision")
    return registry


def provider_volcengine_speech_config() -> Optional[dict[str, str]]:
    app_id = first_env("VOLCENGINE_SPEECH_APP_ID", "VOLCENGINE_APP_ID")
    access_token = first_env("VOLCENGINE_SPEECH_ACCESS_TOKEN", "VOLCENGINE_ACCESS_TOKEN")
    if not app_id or not access_token:
        return None
    return {
        "app_id": app_id,
        "access_token": access_token,
        "resource_id": first_env("VOLCENGINE_SPEECH_RESOURCE_ID") or "volc.seedasr.sauc.duration",
        "ws_url": first_env("VOLCENGINE_SPEECH_WS_URL") or "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
    }


def provider_asr_config(provider_name: str) -> Optional[dict[str, str]]:
    env_map = {
        "bailian": {
            "keys": ["BAILIAN_API_KEY", "DASHSCOPE_API_KEY"],
            "api_urls": ["BAILIAN_ASR_URL", "DASHSCOPE_ASR_URL"],
            "base_urls": ["BAILIAN_ASR_BASE_URL", "DASHSCOPE_ASR_BASE_URL", "BAILIAN_BASE_URL", "DASHSCOPE_BASE_URL"],
            "default_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/audio/transcriptions",
        },
        "doubao": {
            "keys": ["DOUBAO_API_KEY", "ARK_API_KEY"],
            "api_urls": ["DOUBAO_ASR_URL", "ARK_ASR_URL"],
            "base_urls": ["DOUBAO_ASR_BASE_URL", "ARK_ASR_BASE_URL", "DOUBAO_BASE_URL", "ARK_BASE_URL"],
            "default_url": "https://ark.cn-beijing.volces.com/api/v3/audio/transcriptions",
        },
    }
    spec = env_map.get(provider_name)
    if not spec:
        return None
    api_key = first_env(*spec["keys"])
    if not api_key:
        return None
    api_url = first_env(*spec["api_urls"])
    if not api_url:
        base_url = first_env(*spec["base_urls"])
        api_url = _join_api_url(base_url, "/audio/transcriptions") if base_url else spec["default_url"]
    return {"api_key": api_key, "api_url": api_url}


def provider_credentials(provider_name: str) -> Optional[dict[str, str]]:
    env_map = {
        "minimax": {
            "keys": ["MINIMAX_API_KEY"],
            "base_urls": ["MINIMAX_BASE_URL"],
            "default": "https://api.minimax.chat/v1",
        },
        "qwen": {
            "keys": ["QWEN_API_KEY", "DASHSCOPE_API_KEY", "BAILIAN_API_KEY"],
            "base_urls": ["QWEN_BASE_URL", "DASHSCOPE_BASE_URL", "BAILIAN_BASE_URL"],
            "default": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
        "bailian": {
            "keys": ["BAILIAN_API_KEY", "DASHSCOPE_API_KEY"],
            "base_urls": ["BAILIAN_BASE_URL", "DASHSCOPE_BASE_URL"],
            "default": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
        "doubao": {
            "keys": ["DOUBAO_API_KEY", "ARK_API_KEY"],
            "base_urls": ["DOUBAO_BASE_URL", "ARK_BASE_URL"],
            "default": "https://ark.cn-beijing.volces.com/api/v3",
        },
        "generic": {
            "keys": ["OPENAI_API_KEY"],
            "base_urls": ["OPENAI_BASE_URL"],
            "default": "https://api.openai.com/v1",
        },
    }
    spec = env_map.get(provider_name)
    if not spec:
        return None
    api_key = first_env(*spec["keys"])
    if not api_key:
        return None
    base_url = first_env(*spec["base_urls"]) or spec["default"]
    return {"api_key": api_key, "base_url": base_url}


def default_model_for_provider(provider_name: str, purpose: str) -> Optional[str]:
    purpose_env = {
        ("bailian", "asr"): "ASR_MODEL",
        ("qwen", "vision"): "VISION_MODEL",
        ("qwen", "cleanup"): "CLEAN_MODEL",
        ("bailian", "vision"): "VISION_MODEL",
        ("bailian", "cleanup"): "CLEAN_MODEL",
        ("doubao", "asr"): "ASR_MODEL",
        ("volcengine_speech", "asr"): "ASR_MODEL",
        ("doubao", "vision"): "VISION_MODEL",
        ("doubao", "cleanup"): "CLEAN_MODEL",
        ("minimax", "vision"): "VISION_MODEL",
        ("minimax", "cleanup"): "CLEAN_MODEL",
        ("generic", "vision"): "VISION_MODEL",
        ("generic", "cleanup"): "CLEAN_MODEL",
    }
    env_name = purpose_env.get((provider_name, purpose))
    if env_name and os.getenv(env_name):
        return os.getenv(env_name)
    defaults = {
        ("bailian", "asr"): "paraformer-v2",
        ("qwen", "vision"): "qwen3-vl-flash",
        ("qwen", "cleanup"): "qwen-flash",
        ("bailian", "vision"): "qwen3-vl-flash",
        ("bailian", "cleanup"): "qwen-flash",
        ("doubao", "asr"): os.getenv("DOUBAO_ASR_MODEL") or os.getenv("ARK_ASR_MODEL"),
        ("volcengine_speech", "asr"): os.getenv("VOLCENGINE_SPEECH_MODEL_NAME", "bigmodel"),
        ("doubao", "vision"): "doubao-vision-pro-32k",
        ("doubao", "cleanup"): "doubao-pro-32k",
        ("minimax", "vision"): "MiniMax-Text-01",
        ("minimax", "cleanup"): "MiniMax-Text-01",
        ("generic", "vision"): os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini"),
        ("generic", "cleanup"): os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini"),
    }
    return defaults.get((provider_name, purpose))


def get_dashscope_upload_policy(api_key: str, model_name: str) -> dict[str, Any]:
    response = requests.get(
        "https://dashscope.aliyuncs.com/api/v1/uploads",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        params={
            "action": "getPolicy",
            "model": model_name,
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"DashScope 上传凭证响应异常: {payload}")
    return data


class StreamingMultipartForm:
    def __init__(
        self,
        *,
        boundary: str,
        fields: list[tuple[str, str]],
        file_field_name: str,
        file_name: str,
        file_content_type: str,
        file_chunks: Any,
        file_size: int,
    ):
        self.boundary = boundary
        self.file_chunks = file_chunks
        self.file_size = file_size
        self._prefix = self._build_prefix(
            fields=fields,
            file_field_name=file_field_name,
            file_name=file_name,
            file_content_type=file_content_type,
        )
        self._suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")

    def _build_prefix(
        self,
        *,
        fields: list[tuple[str, str]],
        file_field_name: str,
        file_name: str,
        file_content_type: str,
    ) -> bytes:
        parts: list[bytes] = []
        for name, value in fields:
            parts.append(
                (
                    f"--{self.boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )
        parts.append(
            (
                f"--{self.boundary}\r\n"
                f'Content-Disposition: form-data; name="{file_field_name}"; filename="{file_name}"\r\n'
                f"Content-Type: {file_content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        return b"".join(parts)

    def __iter__(self):
        yield self._prefix
        for chunk in self.file_chunks:
            if chunk:
                yield chunk
        yield self._suffix

    def __len__(self) -> int:
        return len(self._prefix) + self.file_size + len(self._suffix)


def _guess_media_content_type(source_url: str, response: requests.Response, filename: str) -> str:
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
    if content_type:
        return content_type
    guessed, _ = mimetypes.guess_type(filename or source_url)
    return guessed or "application/octet-stream"


def stream_remote_media_to_dashscope_oss(
    *,
    source_url: str,
    api_key: str,
    model_name: str,
    filename_hint: str,
    referer_url: Optional[str] = None,
) -> str:
    policy_data = get_dashscope_upload_policy(api_key, model_name)
    media_headers = dict(HEADERS)
    if referer_url:
        media_headers["Referer"] = referer_url
    with requests.Session() as media_session:
        media_session.trust_env = False
        with media_session.get(
            _normalize_media_url(source_url),
            headers=media_headers,
            timeout=120,
            stream=True,
            allow_redirects=True,
        ) as media_response:
            media_response.raise_for_status()
            content_length = int(media_response.headers.get("Content-Length") or "0")
            if content_length <= 0:
                raise RuntimeError("远程媒体缺少 Content-Length，无法稳定上传到 DashScope 临时存储")

            file_name = filename_hint or Path(urlparse(str(media_response.url)).path).name or f"{uuid.uuid4().hex}.bin"
            content_type = _guess_media_content_type(source_url, media_response, file_name)
            key = f"{policy_data['upload_dir'].rstrip('/')}/{file_name}"
            form = StreamingMultipartForm(
                boundary=f"dashscope-{uuid.uuid4().hex}",
                fields=[
                    ("OSSAccessKeyId", policy_data["oss_access_key_id"]),
                    ("Signature", policy_data["signature"]),
                    ("policy", policy_data["policy"]),
                    ("x-oss-object-acl", policy_data["x_oss_object_acl"]),
                    ("x-oss-forbid-overwrite", policy_data["x_oss_forbid_overwrite"]),
                    ("key", key),
                    ("success_action_status", "200"),
                ],
                file_field_name="file",
                file_name=file_name,
                file_content_type=content_type,
                file_chunks=media_response.iter_content(chunk_size=1024 * 1024),
                file_size=content_length,
            )
            with requests.Session() as upload_session:
                upload_session.trust_env = False
                upload_response = upload_session.post(
                    policy_data["upload_host"],
                    data=form,
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={form.boundary}",
                        "Content-Length": str(len(form)),
                    },
                    timeout=(60, 1800),
                )
            upload_response.raise_for_status()
            return f"oss://{key}"


def run_dashscope_multimodal_asr(*, oss_url: str, api_key: str, model: str) -> str:
    import dashscope

    dashscope.base_http_api_url = first_env("DASHSCOPE_BASE_URL", "BAILIAN_BASE_URL") or "https://dashscope.aliyuncs.com/api/v1"
    response = dashscope.MultiModalConversation.call(
        api_key=api_key,
        model=model,
        messages=[{"role": "user", "content": [{"audio": oss_url}]}],
        result_format="message",
        asr_options={
            "language": "zh",
            "enable_itn": False,
        },
    )
    if getattr(response, "status_code", 200) != 200:
        raise RuntimeError(getattr(response, "message", "DashScope 实时 ASR 调用失败"))
    transcript = extract_dashscope_message_text(response)
    if transcript:
        return transcript
    raise RuntimeError("DashScope 实时 ASR 返回空文本")


def run_dashscope_filetrans_task(*, oss_url: str, api_key: str, model: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
        "X-DashScope-OssResourceResolve": "enable",
    }
    submit_response = requests.post(
        "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription",
        headers=headers,
        json={
            "model": model,
            "input": {"file_url": oss_url},
            "parameters": {
                "language": "zh",
                "enable_itn": False,
                "enable_words": True,
                "channel_id": [0],
            },
        },
        timeout=60,
    )
    submit_response.raise_for_status()
    submit_payload = submit_response.json()
    task_id = ((submit_payload.get("output") or {}).get("task_id"))
    if not task_id:
        raise RuntimeError(f"DashScope filetrans 未返回 task_id: {submit_payload}")

    poll_interval = float(first_env("DASHSCOPE_FILETRANS_POLL_INTERVAL_SEC", "BAILIAN_FILETRANS_POLL_INTERVAL_SEC") or "5")
    timeout_sec = float(first_env("DASHSCOPE_FILETRANS_TIMEOUT_SEC", "BAILIAN_FILETRANS_TIMEOUT_SEC") or "7200")
    deadline = time.monotonic() + timeout_sec
    last_payload: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        poll_response = requests.get(
            f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}",
            headers=headers,
            timeout=60,
        )
        poll_response.raise_for_status()
        last_payload = poll_response.json()
        output = last_payload.get("output") or {}
        task_status = output.get("task_status")
        if task_status == "SUCCEEDED":
            transcript = _extract_dashscope_filetrans_text(output)
            if transcript:
                return transcript
            raise RuntimeError(f"DashScope filetrans 成功但未返回文本: {last_payload}")
        if task_status == "FAILED":
            raise RuntimeError(f"DashScope filetrans 失败: {last_payload}")
        time.sleep(poll_interval)

    raise RuntimeError(f"DashScope filetrans 超时: {last_payload}")


def _extract_dashscope_filetrans_text(output: dict[str, Any]) -> str:
    result = output.get("result") or {}
    transcription_url = result.get("transcription_url")
    if transcription_url:
        transcript_response = requests.get(transcription_url, timeout=60)
        transcript_response.raise_for_status()
        transcript_payload = transcript_response.json()
        transcripts = transcript_payload.get("transcripts") or []
        parts = [item.get("text", "").strip() for item in transcripts if item.get("text")]
        return "\n".join(part for part in parts if part)

    results = output.get("results") or []
    for item in results:
        transcription_url = item.get("transcription_url")
        if transcription_url:
            transcript_response = requests.get(transcription_url, timeout=60)
            transcript_response.raise_for_status()
            transcript_payload = transcript_response.json()
            transcripts = transcript_payload.get("transcripts") or []
            parts = [entry.get("text", "").strip() for entry in transcripts if entry.get("text")]
            if parts:
                return "\n".join(parts)
    return ""


def transcribe_audio_via_upload(*, audio_path: Path, api_url: str, api_key: str, model: str) -> str:
    files = {
        "file": (audio_path.name, open(audio_path, "rb"), "audio/mpeg"),
        "model": (None, model),
    }
    try:
        response = requests.post(
            api_url,
            files=files,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=600,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("text") or response.text
    finally:
        files["file"][1].close()


def build_volcengine_full_client_request(payload: dict[str, Any]) -> bytes:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return bytes([0x11, 0x10, 0x11, 0x00]) + len(body).to_bytes(4, "big") + body


def build_volcengine_audio_request(audio_chunk: bytes, *, is_final: bool) -> bytes:
    body = gzip.compress(audio_chunk)
    message_type = 0x22 if is_final else 0x20
    return bytes([0x11, message_type, 0x01, 0x00]) + len(body).to_bytes(4, "big") + body


def parse_volcengine_server_message(message: bytes) -> dict[str, Any]:
    if len(message) < 4:
        raise ValueError("火山语音返回了无效响应帧")

    header_words = message[0] & 0x0F
    header_size = max(header_words, 1) * 4
    if len(message) < header_size:
        raise ValueError("火山语音响应头长度非法")

    message_code = message[1]
    serialization = message[2] >> 4
    compression = message[2] & 0x0F
    payload = message[header_size:]
    message_type = message_code & 0xF0
    is_final = (message_code & 0x0F) in (0x2, 0x3)

    if message_type == 0x90:
        payload_bytes, sequence = _extract_volcengine_full_response_payload(payload)
        decoded_payload = _decode_volcengine_payload(payload_bytes, serialization, compression)
        return {
            "message_type": message_type,
            "sequence": sequence,
            "payload": decoded_payload,
            "is_final": is_final or (sequence is not None and sequence < 0),
        }

    if message_type == 0xF0:
        if len(payload) < 8:
            raise ValueError("火山语音返回的错误响应体长度不足")
        error_code = int.from_bytes(payload[0:4], "big", signed=True)
        payload_size = int.from_bytes(payload[4:8], "big")
        payload_bytes = payload[8 : 8 + payload_size]
        decoded_payload = _decode_volcengine_payload(payload_bytes, serialization, compression)
        return {
            "message_type": message_type,
            "error_code": error_code,
            "payload": decoded_payload,
            "is_final": True,
        }

    return {
        "message_type": message_type,
        "payload": _decode_volcengine_payload(payload, serialization, compression),
        "is_final": is_final,
    }


# 抖音视频 CDN 域名列表，按优先级排序
# zjcdn 灰度节点不稳定，主 CDN aweme.snssdk.com 更可靠
_DOUYIN_CDN_REPLACEMENTS: list[tuple[str, str]] = [
    # (待替换域名, 替换为)
    ("v5-dy-o-abtest.zjcdn.com", "aweme.snssdk.com"),
    ("v3-dy-o-abtest.zjcdn.com", "aweme.snssdk.com"),
    ("v10-dy-o-abtest.zjcdn.com", "aweme.snssdk.com"),
    ("v11-dy-o-abtest.zjcdn.com", "aweme.snssdk.com"),
    ("v3.douyinvod.com", "aweme.snssdk.com"),
    ("v2.douyinvod.com", "aweme.snssdk.com"),
]


def _rewrite_douyin_cdn(url: str) -> str:
    """将抖音视频 URL 替换为更稳定的 CDN 节点"""
    for old_host, new_host in _DOUYIN_CDN_REPLACEMENTS:
        if old_host in url:
            # 替换域名，保留路径和参数
            parsed = urlparse(url)
            rewritten = f"{parsed.scheme}://{new_host}{parsed.path}"
            if parsed.query:
                rewritten += f"?{parsed.query}"
            return rewritten
    return url


def download_binary(url: str, destination: Path) -> Path:
    # 优先使用稳定的 aweme.snssdk.com CDN
    url = _rewrite_douyin_cdn(url)
    response = requests.get(_normalize_media_url(url), headers=HEADERS, timeout=60, stream=True)
    response.raise_for_status()
    with destination.open("wb") as file_obj:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                file_obj.write(chunk)
    return destination


def extract_audio(video_path: Path, audio_path: Path) -> Path:
    import ffmpeg

    (
        ffmpeg.input(str(video_path))
        .output(str(audio_path), acodec="libmp3lame", q=0)
        .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
    )
    return audio_path


def extract_wav_audio(video_path: Path, audio_path: Path) -> Path:
    import ffmpeg

    (
        ffmpeg.input(str(video_path))
        .output(str(audio_path), format="wav", acodec="pcm_s16le", ac=1, ar=16000)
        .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
    )
    return audio_path


def build_volcengine_request_payload(audio_path: Path, context: ExtractionContext, user_id: str) -> dict[str, Any]:
    with wave.open(str(audio_path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        bits = wav_file.getsampwidth() * 8

    return {
        "user": {"uid": user_id},
        "audio": {
            "format": "pcm",
            "codec": "raw",
            "rate": sample_rate,
            "bits": bits,
            "channel": channels,
            "language": os.getenv("VOLCENGINE_SPEECH_LANGUAGE", "zh-CN"),
        },
        "request": {
            "model_name": (
                context.asr_model
                or os.getenv("VOLCENGINE_SPEECH_MODEL_NAME")
                or default_model_for_provider("volcengine_speech", "asr")
                or "bigmodel"
            ),
            "enable_itn": _env_flag("VOLCENGINE_SPEECH_ENABLE_ITN", True),
            "enable_ddc": _env_flag("VOLCENGINE_SPEECH_ENABLE_DDC", False),
            "enable_punc": _env_flag("VOLCENGINE_SPEECH_ENABLE_PUNC", True),
            "show_utterances": _env_flag("VOLCENGINE_SPEECH_SHOW_UTTERANCES", True),
            "result_type": os.getenv("VOLCENGINE_SPEECH_RESULT_TYPE", "full"),
        },
    }


def iter_wav_audio_chunks(audio_path: Path, *, chunk_ms: int = 200):
    with wave.open(str(audio_path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        frames_per_chunk = max(int(sample_rate * chunk_ms / 1000), 1)
        while True:
            chunk = wav_file.readframes(frames_per_chunk)
            if not chunk:
                break
            yield chunk


async def _transcribe_via_volcengine_websocket(
    *,
    audio_path: Path,
    config: dict[str, str],
    context: ExtractionContext,
    user_id: str,
) -> str:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("未安装 websockets 依赖，无法调用火山语音识别") from exc

    payload = build_volcengine_request_payload(audio_path, context, user_id)
    connect_headers = {
        "X-Api-App-Key": config["app_id"],
        "X-Api-Access-Key": config["access_token"],
        "X-Api-Resource-Id": config["resource_id"],
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }

    connect_kwargs = {
        "open_timeout": 30,
        "close_timeout": 30,
        "max_size": 8 * 1024 * 1024,
    }
    last_error: Optional[Exception] = None
    for header_arg_name in ("additional_headers", "extra_headers"):
        try:
            async with websockets.connect(
                config["ws_url"],
                **connect_kwargs,
                **{header_arg_name: connect_headers},
            ) as websocket:
                await websocket.send(build_volcengine_full_client_request(payload))
                latest_text = await _drain_volcengine_messages(websocket, latest_text="", wait_final=False)

                pending_chunk = None
                for audio_chunk in iter_wav_audio_chunks(audio_path):
                    if pending_chunk is not None:
                        await websocket.send(build_volcengine_audio_request(pending_chunk, is_final=False))
                        latest_text = await _drain_volcengine_messages(
                            websocket,
                            latest_text=latest_text,
                            wait_final=False,
                        )
                    pending_chunk = audio_chunk

                if pending_chunk is None:
                    raise RuntimeError("提取出的音频为空，无法调用火山语音识别")

                await websocket.send(build_volcengine_audio_request(pending_chunk, is_final=True))
                latest_text = await _drain_volcengine_messages(websocket, latest_text=latest_text, wait_final=True)
                return latest_text.strip()
        except TypeError as exc:
            last_error = exc
            continue

    if last_error:
        raise last_error
    raise RuntimeError("无法建立火山语音 websocket 连接")


async def _drain_volcengine_messages(websocket: Any, *, latest_text: str, wait_final: bool) -> str:
    timeout = 15.0 if wait_final else 0.3
    while True:
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return latest_text

        parsed = parse_volcengine_server_message(message)
        if parsed.get("error_code") is not None:
            error_payload = parsed.get("payload")
            raise RuntimeError(f"火山语音识别失败({parsed['error_code']}): {_stringify_volcengine_payload(error_payload)}")

        current_text = _extract_volcengine_result_text(parsed.get("payload"))
        if current_text:
            latest_text = current_text

        if parsed.get("is_final"):
            return latest_text
        timeout = 0.05 if not wait_final else 15.0


def extract_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and ("text" in item or item.get("type") == "text"):
                parts.append(item.get("text", ""))
        return "\n".join(part for part in parts if part)
    return ""


def extract_dashscope_message_text(payload: Any) -> str:
    if hasattr(payload, "output"):
        payload = {"output": getattr(payload, "output")}
    output = payload.get("output") if isinstance(payload, dict) else None
    if isinstance(output, dict):
        return extract_message_text(output)
    return ""


def provider_asr_url_env_name(provider_name: str) -> str:
    env_names = {
        "bailian": "BAILIAN_ASR_URL 或 DASHSCOPE_ASR_URL",
        "doubao": "DOUBAO_ASR_URL 或 ARK_ASR_URL",
    }
    return env_names.get(provider_name, "ASR_URL")


def _join_api_url(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


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


def first_env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _decode_volcengine_payload(payload: bytes, serialization: int, compression: int) -> Any:
    if compression == 1 and payload:
        payload = gzip.decompress(payload)
    if serialization == 1 and payload:
        return json.loads(payload.decode("utf-8"))
    if not payload:
        return ""
    return payload.decode("utf-8", errors="ignore")


def _extract_volcengine_full_response_payload(payload: bytes) -> tuple[bytes, Optional[int]]:
    if len(payload) < 4:
        raise ValueError("火山语音返回的响应体长度不足")

    if len(payload) >= 8:
        sequence = int.from_bytes(payload[0:4], "big", signed=True)
        payload_size = int.from_bytes(payload[4:8], "big")
        if payload_size <= len(payload) - 8:
            return payload[8 : 8 + payload_size], sequence

    size_only_payload_size = int.from_bytes(payload[0:4], "big")
    if size_only_payload_size <= len(payload) - 4:
        return payload[4 : 4 + size_only_payload_size], None

    raise ValueError("火山语音返回的响应体长度不足")


def _extract_volcengine_result_text(payload: Any) -> str:
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            text = result.get("text")
            if isinstance(text, str):
                return text
        text = payload.get("text")
        if isinstance(text, str):
            return text
    if isinstance(payload, str):
        return payload
    return ""


def _stringify_volcengine_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if payload is None:
        return ""
    return json.dumps(payload, ensure_ascii=False)


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}
