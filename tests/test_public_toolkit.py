import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from social_media_toolkit.downloader import MediaDownloader, _normalize_include, _validate_remote_url
from social_media_toolkit.models import PostBundle
from social_media_toolkit.platforms.youtube import _parse_subtitle_payload, _select_subtitle_track
from social_media_toolkit.providers.getnote import GETNOTE_INSTALL_HINT, GetNoteResult, GetNoteTextProvider
from social_media_toolkit.service import SocialMediaToolkit
from social_post_extractor_mcp.social_extractor import BilibiliPlatformAdapter, ExtractionContext, SocialPost


class FakeGetNote:
    def __init__(self, result: GetNoteResult, *, available: bool = True):
        self.result = result
        self._available = available
        self.calls = []

    def extract(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.result

    def available(self) -> bool:
        return self._available

    def authenticated(self) -> bool:
        return self._available


class FakeAsr:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def transcribe(self, post: SocialPost, context: ExtractionContext) -> str:
        self.calls.append((post, context))
        return self.text


class FakeExtractor:
    def __init__(self, post: SocialPost, *, asr=None):
        self.post = post
        self.parse_calls = 0
        self.platform_adapters = [SimpleNamespace(can_handle=lambda url: True)]
        self.asr_providers = {"fake": asr} if asr else {}

    def parse_social_post(self, url: str) -> SocialPost:
        self.parse_calls += 1
        return self.post

    def get_douyin_comments(self, url: str, *, sort_by: str, limit: int):
        return self.get_douyin_comments_for_post(self.post, sort_by=sort_by, limit=limit)

    def get_douyin_comments_for_post(self, post: SocialPost, *, sort_by: str, limit: int):
        return {
            "comments": [{"comment_id": "1", "text": "好内容", "like_count": 9}],
            "reported_comment_total": 20,
            "ranking_scope": "retrieved_public_top_level_comments",
            "sort_by": sort_by,
            "source": "douyin_public_mobile_share_api",
            "reply_bodies_included": False,
        }


class FailingCommentExtractor(FakeExtractor):
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


class ExplodingGetNote(FakeGetNote):
    def extract(self, url: str, **kwargs):
        raise RuntimeError("provider crashed")


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
    def test_maps_legacy_post_into_stable_bundle_and_dedupes_cover(self):
        post = make_video_post()
        post.image_urls.append("https://cdn.example.com/frame.jpg")
        post.media["image_urls"] = list(post.image_urls)

        bundle = PostBundle.from_social_post(post).to_dict()

        self.assertEqual(bundle["schema_version"], "1.0")
        self.assertEqual(bundle["source"]["platform"], "bilibili")
        self.assertEqual(bundle["author"]["name"], "作者")
        self.assertEqual(len(bundle["media"]["covers"]), 1)
        self.assertEqual(bundle["media"]["images"], [
            {"type": "image", "url": "https://cdn.example.com/frame.jpg", "index": 1}
        ])
        self.assertEqual(bundle["comments"]["reported_total"], 3)
        self.assertIn("platform_data", bundle["source"])


class GetNoteProviderTests(unittest.TestCase):
    def test_missing_binary_returns_install_and_auth_hint(self):
        provider = GetNoteTextProvider(executable="missing-getnote")
        with patch("social_media_toolkit.providers.getnote.shutil.which", return_value=None):
            result = provider.extract("https://example.com/post")

        self.assertEqual(result.status, "unavailable")
        self.assertIn(GETNOTE_INSTALL_HINT, result.warnings)

    def test_any_non_empty_canonical_content_is_accepted(self):
        provider = GetNoteTextProvider(
            runner=lambda command, timeout: {
                "data": {"note": {"web_page": {"content": "短内容"}}}
            }
        )
        with patch("social_media_toolkit.providers.getnote.shutil.which", return_value="/usr/local/bin/getnote"):
            result = provider.extract("https://example.com/post")

        self.assertTrue(result.success)
        self.assertEqual(result.text, "短内容")

    def test_content_wins_over_stale_task_error(self):
        commands = []

        def runner(command, timeout):
            commands.append(command)
            if command[1] == "save":
                return {"data": {"task_id": "task-1"}}
            if command[1] == "task":
                return {"data": {"note_id": "note-1", "error_msg": "生成笔记失败，请手动重试"}}
            return {
                "data": {
                    "note": {
                        "id": "note-1",
                        "title": "真实标题",
                        "web_page": {"content": "这是已经生成完成、长度足够的 canonical 正文内容。"},
                    }
                }
            }

        provider = GetNoteTextProvider(
            executable="getnote",
            runner=runner,
            sleeper=lambda seconds: None,
            clock=lambda: 0,
        )
        with patch("social_media_toolkit.providers.getnote.shutil.which", return_value="/usr/local/bin/getnote"):
            result = provider.extract("https://example.com/post", wait_sec=1, interval_sec=1)

        self.assertTrue(result.success)
        self.assertEqual(result.note_id, "note-1")
        self.assertEqual(result.title, "真实标题")
        self.assertIn("stale task message ignored", result.warnings[0])
        self.assertEqual([command[1] for command in commands], ["save", "task", "note"])


class TextPipelineTests(unittest.TestCase):
    def test_getnote_success_short_circuits_platform_extraction(self):
        extractor = FakeExtractor(make_video_post())
        getnote = FakeGetNote(GetNoteResult(status="success", text="GetNote 正文", title="GetNote 标题"))
        toolkit = SocialMediaToolkit(extractor=extractor, getnote=getnote, downloader=FakeDownloader())

        result = toolkit.get_text("https://example.com/post")

        self.assertEqual(result["provider"], "getnote")
        self.assertEqual(result["text"], "GetNote 正文")
        self.assertEqual(extractor.parse_calls, 0)

    def test_native_subtitle_precedes_cloud_asr(self):
        asr = FakeAsr("不应被调用")
        extractor = FakeExtractor(make_video_post(subtitle="平台原生字幕"), asr=asr)
        getnote = FakeGetNote(GetNoteResult(status="failed", warnings=["GetNote 暂不可用"]))
        toolkit = SocialMediaToolkit(extractor=extractor, getnote=getnote, downloader=FakeDownloader())

        result = toolkit.get_text("https://example.com/post", asr_provider="fake")

        self.assertEqual(result["provider"], "platform_subtitle")
        self.assertEqual(result["text"], "平台原生字幕")
        self.assertEqual(asr.calls, [])
        self.assertIn("GetNote 暂不可用", result["warnings"])

    def test_cloud_asr_is_final_video_fallback(self):
        asr = FakeAsr("云端 ASR 结果")
        extractor = FakeExtractor(make_video_post(), asr=asr)
        getnote = FakeGetNote(GetNoteResult(status="unavailable", warnings=[GETNOTE_INSTALL_HINT]))
        toolkit = SocialMediaToolkit(extractor=extractor, getnote=getnote, downloader=FakeDownloader())

        result = toolkit.get_text(
            "https://example.com/post",
            asr_provider="fake",
            asr_model="fake-model",
        )

        self.assertEqual(result["provider"], "cloud_asr")
        self.assertEqual(result["text"], "云端 ASR 结果")
        self.assertEqual(result["metadata"]["asr_provider"], "fake")
        self.assertEqual(result["metadata"]["asr_model"], "fake-model")
        self.assertEqual(len(asr.calls), 1)

    def test_unexpected_getnote_error_still_falls_back(self):
        asr = FakeAsr("ASR fallback")
        extractor = FakeExtractor(make_video_post(), asr=asr)
        toolkit = SocialMediaToolkit(
            extractor=extractor,
            getnote=ExplodingGetNote(GetNoteResult(status="failed")),
            downloader=FakeDownloader(),
        )

        result = toolkit.get_text("https://example.com/post", asr_provider="fake")

        self.assertEqual(result["text"], "ASR fallback")
        self.assertIn("GetNote failed unexpectedly", result["warnings"][0])

    def test_capture_enriches_one_bundle_without_implicit_download(self):
        post = make_video_post(subtitle="字幕", platform="douyin")
        extractor = FakeExtractor(post)
        toolkit = SocialMediaToolkit(
            extractor=extractor,
            getnote=FakeGetNote(GetNoteResult(status="failed", warnings=[])),
            downloader=FakeDownloader(),
        )

        result = toolkit.capture(
            "https://example.com/post",
            include_comments=True,
            output_dir=None,
        )

        self.assertEqual(result["content"]["canonical_text"], "字幕")
        self.assertEqual(result["comments"]["items"][0]["comment_id"], "1")
        self.assertNotIn("downloads", result)
        self.assertEqual(extractor.parse_calls, 1)

    def test_capture_keeps_metadata_when_optional_comments_fail(self):
        post = make_video_post(subtitle="字幕", platform="douyin")
        toolkit = SocialMediaToolkit(
            extractor=FailingCommentExtractor(post),
            getnote=FakeGetNote(GetNoteResult(status="failed", warnings=[])),
            downloader=FakeDownloader(),
        )

        result = toolkit.capture("https://example.com/post", include_comments=True)

        self.assertEqual(result["source"]["post_id"], "post-1")
        self.assertEqual(result["comments"]["coverage"], "failed")
        self.assertIn("comment endpoint unavailable", result["provenance"]["warnings"][-1])


class DownloaderTests(unittest.TestCase):
    def test_http_download_writes_checksum_manifest(self):
        post = make_video_post(platform="douyin")
        response = FakeResponse(b"image-bytes")
        downloader = MediaDownloader(max_bytes=1024)

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "social_media_toolkit.downloader.requests.get", return_value=response
        ) as request_get:
            result = downloader.download_post(post, output_dir=tmpdir, include="cover")

            item = result["items"][0]
            self.assertTrue(Path(item["local_path"]).exists())
            self.assertEqual(Path(item["local_path"]).read_bytes(), b"image-bytes")
            self.assertEqual(item["sha256"], "2c8648d103e3dd7ad87660da0f126a1443b6d21ac1bd3ec000c5e24e2373a90c")
            self.assertEqual(result["requested"], ["cover"])
            self.assertTrue(response.closed)
            self.assertTrue(request_get.call_args.kwargs["stream"])

    def test_media_selection_and_private_url_validation(self):
        self.assertEqual(_normalize_include("video,cover,video"), ["video", "cover"])
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            _normalize_include("video,cookies")
        with self.assertRaisesRegex(ValueError, "Private or local"):
            _validate_remote_url("http://127.0.0.1/private.mp4")

    def test_requested_but_missing_media_is_not_reported_as_success(self):
        post = make_video_post(platform="douyin")
        post.video_url = None
        post.media["video_url"] = None
        downloader = MediaDownloader(max_bytes=1024)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = downloader.download_post(post, output_dir=tmpdir, include="video")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["downloaded_count"], 0)
        self.assertIn("no downloadable video URL", result["warnings"][0])

    def test_network_failure_is_returned_as_manifest_error(self):
        post = make_video_post(platform="douyin")
        downloader = MediaDownloader(max_bytes=1024)

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "social_media_toolkit.downloader.requests.get",
            side_effect=RuntimeError("network unavailable"),
        ):
            result = downloader.download_post(post, output_dir=tmpdir, include="cover")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["errors"][0]["kind"], "cover")
        self.assertIn("network unavailable", result["errors"][0]["error"])


class YouTubeSubtitleTests(unittest.TestCase):
    def test_manual_chinese_vtt_is_selected_and_parsed(self):
        selected = _select_subtitle_track(
            {
                "en": [{"url": "https://example.com/en.vtt", "ext": "vtt"}],
                "zh-Hans": [{"url": "https://example.com/zh.vtt", "ext": "vtt"}],
            }
        )
        self.assertEqual(selected[0], "zh-Hans")

        text = _parse_subtitle_payload(
            """WEBVTT

00:00:00.000 --> 00:00:01.000
第一句话

00:00:01.000 --> 00:00:02.000
第一句话

00:00:02.000 --> 00:00:03.000
第二句话
""",
            "vtt",
        )
        self.assertEqual(text, "第一句话\n第二句话")

    def test_json3_subtitles_are_supported(self):
        payload = json.dumps(
            {"events": [{"segs": [{"utf8": "Hello "}, {"utf8": "world"}]}]},
            ensure_ascii=False,
        )
        self.assertEqual(_parse_subtitle_payload(payload, "json3"), "Hello world")


class BilibiliSubtitleTests(unittest.TestCase):
    def test_native_subtitle_body_is_joined_in_order(self):
        player_payload = {
            "data": {
                "subtitle": {
                    "subtitles": [{"subtitle_url": "//example.com/subtitle.json"}]
                }
            }
        }
        subtitle_payload = {
            "body": [{"content": "第一句"}, {"content": "第二句"}]
        }
        with patch(
            "social_post_extractor_mcp.social_extractor.requests.get",
            side_effect=[FakeJsonResponse(player_payload), FakeJsonResponse(subtitle_payload)],
        ):
            text = BilibiliPlatformAdapter()._fetch_subtitle_text(
                "BV1TEST",
                123,
                "https://www.bilibili.com/video/BV1TEST",
            )

        self.assertEqual(text, "第一句\n第二句")


if __name__ == "__main__":
    unittest.main()
