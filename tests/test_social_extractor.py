import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from social_media_toolkit.platforms.core import (
    BilibiliPlatformAdapter,
    PlatformRouter,
    SocialPost,
    XHSStateParser,
    fetch_douyin_public_comments,
    normalize_douyin_public_comment,
)
from social_media_toolkit.providers.volcengine import (
    VOLCENGINE_ASR_RESOURCE_ID,
    VOLCENGINE_ASR_SECRET_NAME,
    VolcengineASR,
    VolcengineASRError,
    _call_volcengine,
    _transcript_text,
)
from social_media_toolkit.service import SocialMediaToolkit


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class DouyinCommentsTests(unittest.TestCase):
    def test_normalizes_public_comment_without_private_session(self):
        normalized = normalize_douyin_public_comment({
            "cid": "comment-1",
            "text": "公开评论",
            "create_time": 100,
            "digg_count": 8,
            "reply_comment_total": 2,
            "user": {"nickname": "公开用户", "unique_id": "public-handle"},
        })
        self.assertEqual(normalized["comment_id"], "comment-1")
        self.assertEqual(normalized["like_count"], 8)
        self.assertEqual(normalized["reply_count"], 2)

    def test_fetches_and_sorts_only_the_returned_public_sample(self):
        payload = {
            "comments": [
                {"cid": "1", "text": "A", "create_time": 100, "digg_count": 2, "user": {}},
                {"cid": "2", "text": "B", "create_time": 90, "digg_count": 9, "user": {}},
            ]
        }
        with patch("social_media_toolkit.platforms.core.requests.get", return_value=FakeResponse(payload)):
            result = fetch_douyin_public_comments("123456", sort_by="likes", limit=2)
        self.assertEqual([item["comment_id"] for item in result["comments"]], ["2", "1"])
        self.assertEqual(result["ranking_scope"], "retrieved_public_top_level_comments")
        self.assertFalse(result["reply_bodies_included"])


class XHSStateParserTests(unittest.TestCase):
    def test_parses_image_note_metadata(self):
        state = {
            "note": {
                "noteDetailMap": {
                    "id": {
                        "note": {
                            "noteId": "note-1",
                            "title": "图文标题",
                            "desc": "图文正文",
                            "type": "normal",
                            "imageList": [
                                {"urlDefault": "http://cdn.example.com/one.jpg"},
                                {"urlPre": "https://cdn.example.com/two.jpg"},
                            ],
                            "user": {"userId": "user-1", "nickname": "作者"},
                            "interactInfo": {"likedCount": "12", "commentCount": "3"},
                        }
                    }
                }
            }
        }
        html = f"<script>window.__INITIAL_STATE__={json.dumps(state, ensure_ascii=False)}</script>"
        post = XHSStateParser.parse_html(
            html,
            "https://xhslink.com/demo",
            "https://www.xiaohongshu.com/explore/note-1",
        )
        self.assertEqual(post.content_type, "image_note")
        self.assertEqual(post.body, "图文正文")
        self.assertEqual(post.image_urls[0], "https://cdn.example.com/one.jpg")
        self.assertEqual(post.public_metrics["likes"], "12")


class BilibiliAdapterTests(unittest.TestCase):
    def test_view_payload_maps_public_metadata(self):
        payload = {
            "code": 0,
            "data": {
                "bvid": "BV1TEST",
                "title": "B站标题",
                "desc": "B站简介",
                "pic": "http://i.example.com/cover.jpg",
                "duration": 120,
                "pubdate": 1000,
                "owner": {"mid": 42, "name": "UP主", "face": "http://i.example.com/a.jpg"},
                "stat": {"view": 100, "like": 8, "reply": 3},
                "pages": [{"cid": 99}],
            },
        }
        post = BilibiliPlatformAdapter.post_from_view_payload(
            payload,
            source_url="https://b23.tv/demo",
            resolved_url="https://www.bilibili.com/video/BV1TEST",
        )
        self.assertEqual(post.post_id, "BV1TEST")
        self.assertEqual(post.author_profile["id"], "42")
        self.assertEqual(post.cover_url, "https://i.example.com/cover.jpg")


class PlatformRouterTests(unittest.TestCase):
    def test_router_has_no_provider_or_artifact_configuration(self):
        parameters = inspect.signature(PlatformRouter).parameters
        self.assertEqual(set(parameters), {"platform_adapters"})
        self.assertFalse(hasattr(PlatformRouter(platform_adapters=[]), "asr_providers"))


class VolcengineASRTests(unittest.TestCase):
    def setUp(self):
        self.post = SocialPost(
            platform="douyin",
            content_type="video",
            source_url="https://example.com/share",
            resolved_url="https://example.com/video",
            page_url="https://example.com/video",
            post_id="1",
            title="测试视频",
            video_url="https://cdn.example.com/video.mp4",
        )

    def test_missing_secret_stops_without_fallback(self):
        with patch("social_media_toolkit.providers.volcengine._load_api_key", return_value=None):
            with self.assertRaisesRegex(VolcengineASRError, VOLCENGINE_ASR_SECRET_NAME):
                VolcengineASR().transcribe(self.post)

    def test_transcribe_uses_only_volcengine_response(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.mp4"
            audio = Path(tmpdir) / "speech.mp3"
            source.write_bytes(b"source")
            audio.write_bytes(b"audio")
            with (
                patch("social_media_toolkit.providers.volcengine._load_api_key", return_value="test-key"),
                patch("social_media_toolkit.providers.volcengine._download_media", return_value=source) as download,
                patch("social_media_toolkit.providers.volcengine._prepare_audio", return_value=audio) as prepare,
                patch(
                    "social_media_toolkit.providers.volcengine._call_volcengine",
                    return_value={"result": {"text": "唯一火山结果"}},
                ) as call,
            ):
                result = VolcengineASR().transcribe(self.post)
        self.assertEqual(result, "唯一火山结果")
        download.assert_called_once()
        prepare.assert_called_once()
        call.assert_called_once_with(audio, "test-key")

    def test_transcript_can_join_utterances(self):
        self.assertEqual(
            _transcript_text({"result": {"utterances": [{"text": "第一句"}, {"text": "第二句"}]}}),
            "第一句\n第二句",
        )

    def test_request_uses_cloud_transcript_protocol_constants(self):
        class FakeHTTPResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"result":{"text":"ok"}}'

        with tempfile.TemporaryDirectory() as tmpdir:
            audio = Path(tmpdir) / "speech.mp3"
            audio.write_bytes(b"audio")
            with patch("social_media_toolkit.providers.volcengine.urlopen", return_value=FakeHTTPResponse()) as open_url:
                result = _call_volcengine(audio, "test-key")
        request = open_url.call_args.args[0]
        self.assertEqual(request.headers["X-api-resource-id"], VOLCENGINE_ASR_RESOURCE_ID)
        self.assertEqual(result["result"]["text"], "ok")


class SingleOrchestratorTests(unittest.TestCase):
    def test_public_service_has_no_provider_selector(self):
        get_text_params = inspect.signature(SocialMediaToolkit.get_text).parameters
        capture_params = inspect.signature(SocialMediaToolkit.capture).parameters
        self.assertNotIn("asr_provider", get_text_params)
        self.assertNotIn("asr_model", get_text_params)
        self.assertNotIn("asr_provider", capture_params)
        self.assertNotIn("asr_model", capture_params)

    def test_server_declares_one_toolkit_instance_and_no_legacy_service(self):
        server_path = Path(__file__).parents[1] / "social_post_extractor_mcp" / "server.py"
        source = server_path.read_text(encoding="utf-8")
        self.assertEqual(source.count("_TOOLKIT = SocialMediaToolkit()"), 1)
        self.assertNotIn("_SERVICE", source)
        self.assertNotIn("load_default_env_files", source)


if __name__ == "__main__":
    unittest.main()
