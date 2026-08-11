import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from social_media_toolkit.platforms.core import (
    BilibiliPlatformAdapter,
    DouyinPlatformAdapter,
    PlatformRouter,
    SocialPost,
    XHSStateParser,
    fetch_douyin_public_comments,
    normalize_douyin_public_comment,
)
from social_media_toolkit.providers.volcengine import (
    MAX_STANDARD_DURATION_SECONDS,
    STANDARD_SUCCESS_CODE,
    VOLCENGINE_ASR_STANDARD_QUERY_ENDPOINT,
    VOLCENGINE_ASR_STANDARD_RESOURCE_ID,
    VOLCENGINE_ASR_STANDARD_SUBMIT_ENDPOINT,
    VOLCENGINE_ASR_RESOURCE_ID,
    VOLCENGINE_ASR_SECRET_NAME,
    VolcengineASR,
    VolcengineASRError,
    _call_volcengine,
    _call_volcengine_standard,
    _download_youtube_media,
    _load_api_key,
    _submit_and_query_standard,
    _timed_transcript,
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


class FakeHtmlResponse:
    def __init__(self, *, url, text):
        self.url = url
        self.text = text

    def raise_for_status(self):
        return None


class DouyinAdapterTests(unittest.TestCase):
    def test_uses_router_data_from_the_original_signed_share_response(self):
        signed_url = "https://www.iesdouyin.com/share/video/123456/?share_sign=public"
        router_data = {
            "loaderData": {
                "video_(id)/page": {
                    "videoInfoRes": {
                        "item_list": [{
                            "desc": "公开作品",
                            "create_time": 100,
                            "aweme_type": 0,
                            "author": {"nickname": "作者"},
                            "statistics": {"digg_count": 8},
                            "video": {
                                "duration": 3000,
                                "cover": {"url_list": ["https://cdn.example.com/cover.jpg"]},
                                "play_addr": {"url_list": ["https://cdn.example.com/video.mp4"]},
                            },
                        }]
                    }
                }
            }
        }
        html = f"<script>window._ROUTER_DATA = {json.dumps(router_data)}</script>"
        response = FakeHtmlResponse(url=signed_url, text=html)
        with patch("social_media_toolkit.platforms.core.requests.get", return_value=response) as request:
            post = DouyinPlatformAdapter().fetch_post(signed_url)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(post.post_id, "123456")
        self.assertEqual(post.title, "公开作品")
        self.assertEqual(post.cover_url, "https://cdn.example.com/cover.jpg")

    def test_reports_an_unavailable_signed_share_instead_of_returning_empty_metadata(self):
        signed_url = "https://www.iesdouyin.com/share/video/123456/?share_sign=public"
        router_data = {
            "loaderData": {
                "video_(id)/page": {
                    "videoInfoRes": {
                        "status_code": 0,
                        "item_list": [],
                        "filter_list": [{
                            "filter_reason": "status_self_see",
                            "detail_msg": "作品权限或已被删除",
                        }],
                    }
                }
            }
        }
        html = f"<script>window._ROUTER_DATA = {json.dumps(router_data)}</script>"
        response = FakeHtmlResponse(url=signed_url, text=html)
        with patch("social_media_toolkit.platforms.core.requests.get", return_value=response):
            with self.assertRaisesRegex(ValueError, "status_self_see"):
                DouyinPlatformAdapter().fetch_post(signed_url)


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
        with patch("social_media_toolkit.platforms.core.requests.get", return_value=FakeResponse(payload)) as request:
            result = fetch_douyin_public_comments("123456", sort_by="likes", limit=2)
        self.assertEqual([item["comment_id"] for item in result["comments"]], ["2", "1"])
        self.assertEqual(result["ranking_scope"], "retrieved_public_top_level_comments")
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["reply_bodies_included"])
        self.assertEqual(request.call_args.kwargs["params"]["count"], 2)

    def test_truncates_an_oversized_source_sample_to_the_requested_limit(self):
        payload = {
            "comments": [
                {"cid": "1", "text": "A", "create_time": 100, "digg_count": 2, "user": {}},
                {"cid": "2", "text": "B", "create_time": 90, "digg_count": 9, "user": {}},
            ]
        }
        with patch("social_media_toolkit.platforms.core.requests.get", return_value=FakeResponse(payload)):
            result = fetch_douyin_public_comments("123456", sort_by="likes", limit=1)
        self.assertEqual(result["fetched_top_level_count"], 2)
        self.assertEqual(result["returned_count"], 1)
        self.assertEqual(len(result["comments"]), 1)

    def test_accepts_up_to_one_hundred_and_returns_the_available_sample(self):
        payload = {
            "comments": [
                {"cid": "1", "text": "A", "create_time": 100, "digg_count": 2, "user": {}},
                {"cid": "2", "text": "B", "create_time": 90, "digg_count": 9, "user": {}},
            ]
        }
        with patch("social_media_toolkit.platforms.core.requests.get", return_value=FakeResponse(payload)) as request:
            result = fetch_douyin_public_comments("123456", sort_by="likes", limit=100)
        self.assertEqual(request.call_args.kwargs["params"]["count"], 100)
        self.assertEqual(result["requested_limit"], 100)
        self.assertEqual(result["returned_count"], 2)
        self.assertEqual(result["status"], "success")

    def test_rejects_comment_limits_outside_supported_request_range(self):
        for limit in (0, 101, True, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    fetch_douyin_public_comments("123456", limit=limit)


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

    def test_route_rejects_media_over_five_hours_before_credentials_or_download(self):
        self.post.duration_sec = MAX_STANDARD_DURATION_SECONDS + 1
        with (
            patch("social_media_toolkit.providers.volcengine._load_api_key") as load_key,
            patch("social_media_toolkit.providers.volcengine._download_media") as download,
        ):
            with self.assertRaisesRegex(VolcengineASRError, "longer than 5 hours"):
                VolcengineASR().transcribe(self.post)
        load_key.assert_not_called()
        download.assert_not_called()

    def test_over_two_hours_routes_to_standard_asr(self):
        self.post.duration_sec = 7201
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.mp4"
            audio = Path(tmpdir) / "speech.mp3"
            source.write_bytes(b"source")
            audio.write_bytes(b"audio")
            with (
                patch("social_media_toolkit.providers.volcengine._load_api_key", return_value="test-key"),
                patch("social_media_toolkit.providers.volcengine._download_media", return_value=source),
                patch("social_media_toolkit.providers.volcengine._prepare_audio", return_value=audio),
                patch(
                    "social_media_toolkit.providers.volcengine._call_volcengine_standard",
                    return_value={"result": {"text": "standard result"}},
                ) as standard,
                patch("social_media_toolkit.providers.volcengine._call_volcengine") as flash,
            ):
                result = VolcengineASR().transcribe(self.post)
        self.assertEqual(result, "standard result")
        standard.assert_called_once_with(
            audio,
            "test-key",
            timed=False,
            speaker_info=False,
            context=None,
        )
        flash.assert_not_called()

    def test_process_environment_works_without_agent_switch(self):
        with (
            patch.dict("os.environ", {VOLCENGINE_ASR_SECRET_NAME: "synthetic-public-key"}, clear=True),
            patch("social_media_toolkit.providers.volcengine.shutil.which") as find_executable,
        ):
            self.assertEqual(_load_api_key(), "synthetic-public-key")
        find_executable.assert_not_called()

    def test_missing_agent_switch_is_not_a_runtime_error(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "social_media_toolkit.providers.volcengine.shutil.which",
                return_value=None,
            ),
        ):
            self.assertIsNone(_load_api_key())

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
        call.assert_called_once_with(
            audio,
            "test-key",
            timed=False,
            speaker_info=False,
            context=None,
        )

    def test_youtube_asr_uses_yt_dlp_retry_download_instead_of_signed_url_stream(self):
        post = SocialPost(
            platform="youtube",
            content_type="video",
            source_url="https://youtu.be/video123",
            resolved_url="https://www.youtube.com/watch?v=video123",
            page_url="https://www.youtube.com/watch?v=video123",
            post_id="video123",
            title="Podcast",
            video_url="https://googlevideo.example/signed-video",
            media={"audio_url": "https://googlevideo.example/signed-audio"},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.m4a"
            audio = Path(tmpdir) / "speech.mp3"
            source.write_bytes(b"source")
            audio.write_bytes(b"audio")
            with (
                patch("social_media_toolkit.providers.volcengine._load_api_key", return_value="test-key"),
                patch(
                    "social_media_toolkit.providers.volcengine._download_youtube_media",
                    return_value=source,
                ) as youtube_download,
                patch("social_media_toolkit.providers.volcengine._download_media") as direct_download,
                patch("social_media_toolkit.providers.volcengine._prepare_audio", return_value=audio),
                patch(
                    "social_media_toolkit.providers.volcengine._call_volcengine",
                    return_value={"result": {"text": "Podcast result"}},
                ),
            ):
                result = VolcengineASR().transcribe(post)

        self.assertEqual(result, "Podcast result")
        youtube_download.assert_called_once()
        self.assertEqual(
            youtube_download.call_args.args[0],
            "https://www.youtube.com/watch?v=video123",
        )
        direct_download.assert_not_called()

    def test_youtube_temporary_download_suppresses_progress_on_json_cli_stdout(self):
        observed = {}

        class FakeYoutubeDL:
            def __init__(self, options):
                observed.update(options)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, _url, *, download):
                self.path = Path(observed["outtmpl"].replace("%(ext)s", "m4a"))
                self.path.write_bytes(b"temporary audio")
                return {"id": "video123", "ext": "m4a"}

            def prepare_filename(self, _info):
                return str(self.path)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("yt_dlp.YoutubeDL", FakeYoutubeDL):
                result = _download_youtube_media(
                    "https://www.youtube.com/watch?v=video123",
                    Path(tmpdir),
                )
            self.assertTrue(result.is_file())

        self.assertTrue(observed["quiet"])
        self.assertTrue(observed["no_warnings"])
        self.assertTrue(observed["noprogress"])

    def test_transcript_can_join_utterances(self):
        self.assertEqual(
            _transcript_text({"result": {"utterances": [{"text": "第一句"}, {"text": "第二句"}]}}),
            "第一句\n第二句",
        )

    def test_timed_transcript_preserves_utterance_and_word_boundaries(self):
        result = _timed_transcript(
            {
                "result": {
                    "utterances": [
                        {
                            "start_time": 100,
                            "end_time": 900,
                            "text": "Hello world",
                            "words": [
                                {"text": "Hello", "start_time": 100, "end_time": 400},
                                {"text": "world", "start_time": 450, "end_time": 900},
                            ],
                        }
                    ]
                }
            },
            duration_ms=1000,
        )
        self.assertEqual(result["timing_precision"], "asr_word")
        self.assertEqual(result["segments"], [
            {"start_ms": 100, "end_ms": 900, "text": "Hello world"}
        ])
        self.assertEqual(result["words"][1]["start_ms"], 450)
        self.assertEqual(result["duration_ms"], 1000)

    def test_timed_transcript_reads_speaker_from_utterance_additions(self):
        result = _timed_transcript(
            {
                "result": {
                    "utterances": [
                        {
                            "start_time": 100,
                            "end_time": 900,
                            "text": "Host question",
                            "additions": {"speaker": "1"},
                        },
                        {
                            "start_time": 1000,
                            "end_time": 1900,
                            "text": "Guest answer",
                            "additions": {"speaker": "2"},
                        },
                    ]
                }
            },
            duration_ms=2000,
            require_speaker_info=True,
        )
        self.assertEqual(
            [segment["speaker"] for segment in result["segments"]],
            ["SPEAKER_01", "SPEAKER_02"],
        )
        self.assertEqual(result["speaker_diarization"]["speaker_count"], 2)

    def test_transcribe_timed_reports_temporary_media_deleted(self):
        observed = {}

        def fake_download(_url, temp_dir, *, referer):
            observed["temp_dir"] = temp_dir
            source = temp_dir / "source.mp4"
            source.write_bytes(b"source")
            return source

        def fake_prepare(_source, temp_dir):
            audio = temp_dir / "speech.mp3"
            audio.write_bytes(b"audio")
            return audio

        payload = {
            "result": {
                "utterances": [
                    {"start_time": 0, "end_time": 800, "text": "Timed result"}
                ]
            }
        }
        with (
            patch("social_media_toolkit.providers.volcengine._load_api_key", return_value="test-key"),
            patch("social_media_toolkit.providers.volcengine._download_media", side_effect=fake_download),
            patch("social_media_toolkit.providers.volcengine._prepare_audio", side_effect=fake_prepare),
            patch("social_media_toolkit.providers.volcengine._call_volcengine", return_value=payload),
        ):
            result = VolcengineASR().transcribe_timed(self.post)
        self.assertEqual(result["segments"][0]["text"], "Timed result")
        self.assertEqual(result["timing_precision"], "asr_utterance")
        self.assertTrue(result["temp_media_deleted"])
        self.assertFalse(observed["temp_dir"].exists())

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

    def test_timed_speaker_request_uses_verified_podcast_parameters_and_context(self):
        class FakeHTTPResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"result":{"text":"ok"}}'

        context = {
            "context_type": "dialog_ctx",
            "context_data": [{"text": "Podcast: Example. Guest: Jane Doe."}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            audio = Path(tmpdir) / "speech.mp3"
            audio.write_bytes(b"audio")
            with patch("social_media_toolkit.providers.volcengine.urlopen", return_value=FakeHTTPResponse()) as open_url:
                _call_volcengine(
                    audio,
                    "test-key",
                    timed=True,
                    speaker_info=True,
                    context=context,
                )

        request = open_url.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        options = body["request"]
        self.assertEqual(options["model_name"], "bigmodel")
        self.assertTrue(options["enable_itn"])
        self.assertTrue(options["enable_punc"])
        self.assertFalse(options["enable_ddc"])
        self.assertTrue(options["show_utterances"])
        self.assertTrue(options["enable_speaker_info"])
        self.assertEqual(options["ssd_version"], "200")
        self.assertFalse(options["enable_channel_split"])
        self.assertEqual(json.loads(options["corpus"]["context"]), context)
        self.assertNotIn("language", options)

    def test_standard_route_deletes_temporary_tos_object_after_query(self):
        temporary = Mock()
        temporary.signed_url = "https://tos.example/presigned-audio"
        with tempfile.TemporaryDirectory() as tmpdir:
            audio = Path(tmpdir) / "speech.mp3"
            audio.write_bytes(b"audio")
            with (
                patch(
                    "social_media_toolkit.providers.volcengine._upload_standard_audio",
                    return_value=temporary,
                ),
                patch(
                    "social_media_toolkit.providers.volcengine._submit_and_query_standard",
                    return_value={"result": {"text": "done"}},
                ) as submit_query,
            ):
                result = _call_volcengine_standard(
                    audio,
                    "test-key",
                    timed=True,
                    speaker_info=True,
                    context={"context_data": [{"text": "guest"}]},
                )
        self.assertEqual(result["result"]["text"], "done")
        submit_query.assert_called_once()
        temporary.delete.assert_called_once_with()

    def test_standard_route_deletes_temporary_tos_object_after_query_failure(self):
        temporary = Mock()
        temporary.signed_url = "https://tos.example/presigned-audio"
        with tempfile.TemporaryDirectory() as tmpdir:
            audio = Path(tmpdir) / "speech.mp3"
            audio.write_bytes(b"audio")
            with (
                patch(
                    "social_media_toolkit.providers.volcengine._upload_standard_audio",
                    return_value=temporary,
                ),
                patch(
                    "social_media_toolkit.providers.volcengine._submit_and_query_standard",
                    side_effect=VolcengineASRError("synthetic query failure"),
                ),
            ):
                with self.assertRaisesRegex(VolcengineASRError, "synthetic query failure"):
                    _call_volcengine_standard(audio, "test-key")
        temporary.delete.assert_called_once_with()

    def test_standard_submit_query_uses_async_endpoints_and_podcast_options(self):
        responses = [
            (
                {
                    "X-Api-Status-Code": STANDARD_SUCCESS_CODE,
                    "X-Tt-Logid": "synthetic-log-id",
                },
                {},
            ),
            ({"X-Api-Status-Code": "20000001"}, {}),
            (
                {"X-Api-Status-Code": STANDARD_SUCCESS_CODE},
                {"result": {"text": "done", "utterances": []}},
            ),
        ]
        context = {"context_data": [{"text": "Podcast guest: Example"}]}
        with (
            patch(
                "social_media_toolkit.providers.volcengine._post_standard_json",
                side_effect=responses,
            ) as post_json,
            patch("social_media_toolkit.providers.volcengine.time.sleep") as sleep,
        ):
            result = _submit_and_query_standard(
                "https://tos.example/presigned-audio",
                "test-key",
                timed=True,
                speaker_info=True,
                context=context,
                poll_interval=1,
                max_wait_seconds=30,
            )

        self.assertEqual(result["result"]["text"], "done")
        self.assertEqual(post_json.call_args_list[0].args[0], VOLCENGINE_ASR_STANDARD_SUBMIT_ENDPOINT)
        self.assertEqual(post_json.call_args_list[1].args[0], VOLCENGINE_ASR_STANDARD_QUERY_ENDPOINT)
        submit_headers = post_json.call_args_list[0].args[1]
        submit_body = post_json.call_args_list[0].args[2]
        self.assertEqual(submit_headers["X-Api-Resource-Id"], VOLCENGINE_ASR_STANDARD_RESOURCE_ID)
        self.assertEqual(submit_body["audio"]["url"], "https://tos.example/presigned-audio")
        self.assertFalse(submit_body["request"]["enable_ddc"])
        self.assertTrue(submit_body["request"]["enable_speaker_info"])
        self.assertEqual(submit_body["request"]["ssd_version"], "200")
        self.assertFalse(submit_body["request"]["enable_channel_split"])
        self.assertEqual(
            json.loads(submit_body["request"]["corpus"]["context"]),
            context,
        )
        query_headers = post_json.call_args_list[1].args[1]
        self.assertEqual(query_headers["X-Tt-Logid"], "synthetic-log-id")
        sleep.assert_called_once_with(1)


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
