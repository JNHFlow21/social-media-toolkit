import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from social_media_toolkit.downloader import MediaDownloader, _normalize_include, _validate_remote_url
from social_media_toolkit.models import PostBundle
from social_media_toolkit.platforms.core import BilibiliPlatformAdapter, SocialPost
from social_media_toolkit.platforms.youtube import _parse_subtitle_payload, _select_subtitle_track
from social_media_toolkit.providers.getnote import GETNOTE_INSTALL_HINT, GetNoteResult, GetNoteTextProvider
from social_media_toolkit.service import SocialMediaToolkit


class FakeGetNote:
    def __init__(self, result: GetNoteResult, *, available: bool = True):
        self.result = result
        self._available = available
        self.calls = []

    def extract(self, url: str):
        self.calls.append(url)
        return self.result

    def available(self) -> bool:
        return self._available

    def authenticated(self) -> bool:
        return self._available


class ExplodingGetNote(FakeGetNote):
    def extract(self, url: str):
        raise RuntimeError("provider crashed")


class FakeASR:
    provider_name = "volcengine_bigmodel"

    def __init__(self, text: str = "云端 ASR 结果", *, configured: bool = True, error: Exception | None = None):
        self.text = text
        self._configured = configured
        self.error = error
        self.calls = []

    def transcribe(self, post: SocialPost) -> str:
        self.calls.append(post)
        if self.error:
            raise self.error
        return self.text

    def configured(self) -> bool:
        return self._configured


class FakeRouter:
    def __init__(self, post: SocialPost):
        self.post = post
        self.parse_calls = 0
        self.platform_adapters = [self]

    def can_handle(self, url: str) -> bool:
        return True

    def parse(self, url: str) -> SocialPost:
        self.parse_calls += 1
        return self.post

    def get_douyin_comments_for_post(self, post: SocialPost, *, sort_by: str, limit: int):
        return {
            "comments": [{"comment_id": "1", "text": "好内容", "like_count": 9}],
            "reported_comment_total": 20,
            "ranking_scope": "retrieved_public_top_level_comments",
            "sort_by": sort_by,
            "source": "douyin_public_mobile_share_api",
            "reply_bodies_included": False,
        }


class FailingCommentRouter(FakeRouter):
    def get_douyin_comments_for_post(self, post: SocialPost, *, sort_by: str, limit: int):
        raise RuntimeError("comment endpoint unavailable")


class FakeDownloader:
    def download_post(self, post: SocialPost, *, output_dir: str, include):
        return {
            "status": "success",
            "platform": post.platform,
            "post_id": post.post_id,
            "output_dir": output_dir,
            "requested": list(include) if not isinstance(include, str) else include.split(","),
            "downloaded_count": 1,
            "items": [{"kind": "video", "local_path": f"{output_dir}/video.mp4"}],
        }


class FakeResponse:
    def __init__(self, body: bytes, *, content_type: str = "image/jpeg"):
        self.body = body
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size: int):
        yield self.body

    def close(self):
        self.closed = True


class FakeJsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def make_video_post(*, subtitle: str | None = None, platform: str = "bilibili") -> SocialPost:
    return SocialPost(
        platform=platform,
        content_type="video",
        source_url="https://example.com/share/1",
        resolved_url="https://example.com/video/1",
        page_url="https://example.com/video/1",
        post_id="post-1",
        title="示例视频",
        body="这里只是视频简介",
        author_name="作者",
        cover_url="https://cdn.example.com/cover.jpg",
        video_url="https://cdn.example.com/video.mp4",
        image_urls=["https://cdn.example.com/cover.jpg"],
        public_metrics={"views": 100, "likes": 8, "comments": 3},
        extra={"subtitle_text": subtitle} if subtitle else {},
    )


class PostBundleTests(unittest.TestCase):
    def test_maps_post_into_stable_bundle_and_dedupes_cover(self):
        post = make_video_post()
        post.image_urls.append("https://cdn.example.com/frame.jpg")
        post.media["image_urls"] = list(post.image_urls)
        post.media["audio_url"] = "https://cdn.example.com/audio.m4a"

        bundle = PostBundle.from_social_post(post).to_dict()

        self.assertEqual(bundle["schema_version"], "1.0")
        self.assertEqual(bundle["source"]["platform"], "bilibili")
        self.assertEqual(bundle["author"]["name"], "作者")
        self.assertEqual(len(bundle["media"]["covers"]), 1)
        self.assertEqual(bundle["media"]["images"], [
            {"type": "image", "url": "https://cdn.example.com/frame.jpg", "index": 1}
        ])
        self.assertEqual(bundle["media"]["audio"][0]["url"], "https://cdn.example.com/audio.m4a")


class GetNoteProviderTests(unittest.TestCase):
    def test_missing_binary_returns_install_and_auth_hint(self):
        provider = GetNoteTextProvider(executable="missing-getnote")
        with patch("social_media_toolkit.providers.getnote.shutil.which", return_value=None):
            result = provider.extract("https://example.com/post")
        self.assertEqual(result.status, "unavailable")
        self.assertIn(GETNOTE_INSTALL_HINT, result.warnings)

    def test_current_web_content_field_is_preferred(self):
        provider = GetNoteTextProvider(
            runner=lambda command, timeout: {
                "data": {"note": {"web_content": "原始网页正文", "content": "AI 总结"}}
            }
        )
        with patch("social_media_toolkit.providers.getnote.shutil.which", return_value="/usr/local/bin/getnote"):
            result = provider.extract("https://example.com/post")
        self.assertTrue(result.success)
        self.assertEqual(result.text, "原始网页正文")

    def test_original_content_wins_over_stale_task_error(self):
        commands = []

        def runner(command, timeout):
            commands.append(command)
            if command[1] == "save":
                return {"data": {"task_id": "task-1"}}
            if command[1] == "task":
                return {"data": {"note_id": "note-1", "error_msg": "生成笔记失败，请手动重试"}}
            return {
                "data": {"note": {"id": "note-1", "title": "真实标题", "web_content": "原始正文"}}
            }

        provider = GetNoteTextProvider(executable="getnote", runner=runner)
        with patch("social_media_toolkit.providers.getnote.shutil.which", return_value="/usr/local/bin/getnote"):
            result = provider.extract("https://example.com/post")
        self.assertTrue(result.success)
        self.assertEqual(result.text, "原始正文")
        self.assertIn("stale task message ignored", result.warnings[0])
        self.assertEqual([command[1] for command in commands], ["save", "task", "note"])


class TextPipelineTests(unittest.TestCase):
    def test_getnote_success_short_circuits_platform_and_asr(self):
        router = FakeRouter(make_video_post())
        asr = FakeASR("不应调用")
        toolkit = SocialMediaToolkit(
            router=router,
            getnote=FakeGetNote(GetNoteResult(status="success", text="GetNote 原文")),
            asr=asr,
            downloader=FakeDownloader(),
        )
        result = toolkit.get_text("https://example.com/post")
        self.assertEqual(result["provider"], "getnote")
        self.assertEqual(router.parse_calls, 0)
        self.assertEqual(asr.calls, [])

    def test_native_subtitle_precedes_volcengine(self):
        asr = FakeASR("不应调用")
        toolkit = SocialMediaToolkit(
            router=FakeRouter(make_video_post(subtitle="平台原生字幕")),
            getnote=FakeGetNote(GetNoteResult(status="failed", warnings=["GetNote failed"])),
            asr=asr,
            downloader=FakeDownloader(),
        )
        result = toolkit.get_text("https://example.com/post")
        self.assertEqual(result["provider"], "platform_subtitle")
        self.assertEqual(result["text"], "平台原生字幕")
        self.assertEqual(asr.calls, [])

    def test_volcengine_is_the_only_final_video_route(self):
        asr = FakeASR("火山转写")
        toolkit = SocialMediaToolkit(
            router=FakeRouter(make_video_post()),
            getnote=FakeGetNote(GetNoteResult(status="unavailable", warnings=[GETNOTE_INSTALL_HINT])),
            asr=asr,
            downloader=FakeDownloader(),
        )
        result = toolkit.get_text("https://example.com/post")
        self.assertEqual(result["provider"], "volcengine_bigmodel")
        self.assertEqual(result["text"], "火山转写")
        self.assertEqual(result["metadata"]["local_fallback"], False)
        self.assertEqual(len(asr.calls), 1)

    def test_volcengine_failure_is_returned_without_local_fallback(self):
        asr = FakeASR(error=RuntimeError("cloud unavailable"))
        toolkit = SocialMediaToolkit(
            router=FakeRouter(make_video_post()),
            getnote=ExplodingGetNote(GetNoteResult(status="failed")),
            asr=asr,
            downloader=FakeDownloader(),
        )
        result = toolkit.get_text("https://example.com/post")
        self.assertEqual(result["status"], "error")
        self.assertIn("cloud unavailable", result["warnings"][-1])
        self.assertEqual(result["metadata"]["local_fallback"], False)

    def test_capture_enriches_one_bundle_without_implicit_download(self):
        router = FakeRouter(make_video_post(subtitle="字幕", platform="douyin"))
        toolkit = SocialMediaToolkit(
            router=router,
            getnote=FakeGetNote(GetNoteResult(status="failed")),
            asr=FakeASR(),
            downloader=FakeDownloader(),
        )
        result = toolkit.capture("https://example.com/post", include_comments=True)
        self.assertEqual(result["content"]["canonical_text"], "字幕")
        self.assertEqual(result["comments"]["items"][0]["comment_id"], "1")
        self.assertNotIn("downloads", result)
        self.assertEqual(router.parse_calls, 1)

    def test_capture_keeps_metadata_when_optional_comments_fail(self):
        toolkit = SocialMediaToolkit(
            router=FailingCommentRouter(make_video_post(subtitle="字幕", platform="douyin")),
            getnote=FakeGetNote(GetNoteResult(status="failed")),
            asr=FakeASR(),
            downloader=FakeDownloader(),
        )
        result = toolkit.capture("https://example.com/post", include_comments=True)
        self.assertEqual(result["comments"]["coverage"], "failed")
        self.assertIn("comment endpoint unavailable", result["provenance"]["warnings"][-1])


class DownloaderTests(unittest.TestCase):
    def test_http_download_writes_checksum_manifest(self):
        post = make_video_post(platform="douyin")
        response = FakeResponse(b"image-bytes")
        downloader = MediaDownloader(max_bytes=1024)
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "social_media_toolkit.downloader.requests.get", return_value=response
        ):
            result = downloader.download_post(post, output_dir=tmpdir, include="cover")
            item = result["items"][0]
            self.assertTrue(Path(item["local_path"]).exists())
            self.assertEqual(item["sha256"], "2c8648d103e3dd7ad87660da0f126a1443b6d21ac1bd3ec000c5e24e2373a90c")
            self.assertTrue(response.closed)

    def test_media_selection_and_private_url_validation(self):
        self.assertEqual(_normalize_include("video,cover,video"), ["video", "cover"])
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            _normalize_include("video,cookies")
        with self.assertRaisesRegex(ValueError, "Private or local"):
            _validate_remote_url("http://127.0.0.1/private.mp4")


class SubtitleTests(unittest.TestCase):
    def test_youtube_prefers_manual_chinese_and_dedupes_vtt(self):
        selected = _select_subtitle_track({
            "en": [{"url": "https://example.com/en.vtt", "ext": "vtt"}],
            "zh-Hans": [{"url": "https://example.com/zh.vtt", "ext": "vtt"}],
        })
        self.assertEqual(selected[0], "zh-Hans")
        text = _parse_subtitle_payload(
            """WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n第一句话\n\n00:00:01.000 --> 00:00:02.000\n第一句话\n\n00:00:02.000 --> 00:00:03.000\n第二句话\n""",
            "vtt",
        )
        self.assertEqual(text, "第一句话\n第二句话")

    def test_bilibili_native_subtitle_is_joined_in_order(self):
        player_payload = {"data": {"subtitle": {"subtitles": [{"subtitle_url": "//example.com/s.json"}]}}}
        subtitle_payload = {"body": [{"content": "第一句"}, {"content": "第二句"}]}
        with patch(
            "social_media_toolkit.platforms.core.requests.get",
            side_effect=[FakeJsonResponse(player_payload), FakeJsonResponse(subtitle_payload)],
        ):
            text = BilibiliPlatformAdapter()._fetch_subtitle_text(
                "BV1TEST", 123, "https://www.bilibili.com/video/BV1TEST"
            )
        self.assertEqual(text, "第一句\n第二句")

    def test_bilibili_keeps_video_and_audio_streams_distinct(self):
        payload = {
            "data": {
                "dash": {
                    "video": [{"baseUrl": "https://cdn.example.com/video.m4s"}],
                    "audio": [{"baseUrl": "https://cdn.example.com/audio.m4s"}],
                }
            }
        }
        with patch("social_media_toolkit.platforms.core.requests.get", return_value=FakeJsonResponse(payload)):
            streams = BilibiliPlatformAdapter()._fetch_playable_streams(
                "BV1TEST", 123, "https://www.bilibili.com/video/BV1TEST"
            )
        self.assertEqual(streams["video_url"], "https://cdn.example.com/video.m4s")
        self.assertEqual(streams["audio_url"], "https://cdn.example.com/audio.m4s")


if __name__ == "__main__":
    unittest.main()
