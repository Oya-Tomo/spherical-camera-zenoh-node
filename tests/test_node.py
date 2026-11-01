import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from config import MountConfig, ViewsConfig, load_node_config
from node import (
    JPEG_ENCODING,
    JSON_ENCODING,
    MAX_CONSECUTIVE_CAPTURE_FAILURES,
    TEXT_ENCODING,
    ViewChannel,
    encode_view_direction,
    main,
    normalize_yaw_deg,
    parse_view_direction,
    publish_frames,
    run,
    view_direction_key,
)

EXAMPLE_CONFIG = load_node_config(Path("config/node-config.example.json5"))
FRAME = np.random.default_rng(0).integers(0, 256, (720, 1280, 3), dtype=np.uint8)
SMALL_VIEWS = ViewsConfig.model_validate(
    {
        "fov_deg": 90.0,
        "size": [64, 48],
        "jpeg_quality": 80,
        "channels": [{"view_key": "agent"}],
    }
)
NO_MOUNT = MountConfig(roll_deg=0.0, pitch_deg=0.0, yaw_deg=0.0)


class StopLoop(Exception):
    pass


def direction_query(payload: bytes | None) -> MagicMock:
    query = MagicMock()
    query.payload = None if payload is None else MagicMock(to_bytes=lambda: payload)
    return query


class ViewDirectionTests(unittest.TestCase):
    def test_yaw_is_normalized_into_half_open_range(self) -> None:
        cases = {
            0.0: 0.0,
            180.0: 180.0,
            -180.0: 180.0,
            190.0: -170.0,
            540.0: 180.0,
            -350.0: 10.0,
        }
        for yaw_deg, expected in cases.items():
            with self.subTest(yaw_deg=yaw_deg):
                self.assertAlmostEqual(normalize_yaw_deg(yaw_deg), expected)

    def test_encoded_direction_round_trips(self) -> None:
        self.assertEqual(parse_view_direction(encode_view_direction(-42.5)), -42.5)
        self.assertEqual(parse_view_direction(b'{"yaw_deg": 90}'), 90.0)

    def test_malformed_payloads_are_rejected_with_a_reason(self) -> None:
        payloads = (
            b"not json",
            b"{}",
            b'{"yaw_deg": "left"}',
            b'{"yaw_deg": 1e999}',
            b'{"yaw_deg": 0, "pitch_deg": 0}',
        )
        for payload in payloads:
            with (
                self.subTest(payload=payload),
                self.assertRaisesRegex(ValueError, ".+"),
            ):
                parse_view_direction(payload)

    def test_direction_key_extends_the_view_image_key(self) -> None:
        self.assertEqual(
            view_direction_key("camera/view/agent"), "camera/view/agent/direction"
        )


class ViewChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.publisher = MagicMock()
        self.view_channel = ViewChannel(
            370.0,
            SMALL_VIEWS,
            EXAMPLE_CONFIG.lenses,
            NO_MOUNT,
            self.publisher,
            "camera/dir",
        )

    def publish(self) -> tuple[bytes, float]:
        self.view_channel.publish(FRAME)
        call = self.publisher.put.call_args
        return call.args[0], parse_view_direction(call.kwargs["attachment"])

    def test_publishes_sized_jpeg_tagged_with_its_initial_direction(self) -> None:
        jpeg, yaw_deg = self.publish()

        image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert image is not None
        self.assertEqual(image.shape, (48, 64, 3))
        self.assertAlmostEqual(yaw_deg, 10.0)

    def test_query_without_payload_reports_the_direction(self) -> None:
        query = direction_query(None)

        self.view_channel.answer_direction_query(query)

        query.reply.assert_called_once_with(
            "camera/dir", encode_view_direction(10.0), encoding=JSON_ENCODING
        )

    def test_query_with_direction_turns_the_following_frames(self) -> None:
        jpeg_before, _ = self.publish()
        query = direction_query(b'{"yaw_deg": 270}')

        self.view_channel.answer_direction_query(query)
        jpeg_after, yaw_deg = self.publish()

        query.reply.assert_called_once_with(
            "camera/dir", encode_view_direction(-90.0), encoding=JSON_ENCODING
        )
        self.assertAlmostEqual(yaw_deg, -90.0)
        self.assertNotEqual(jpeg_after, jpeg_before)

    def test_malformed_query_gets_an_error_and_keeps_the_direction(self) -> None:
        query = direction_query(b'{"yaw_deg": "left"}')

        self.view_channel.answer_direction_query(query)
        _, yaw_deg = self.publish()

        query.reply.assert_not_called()
        self.assertIn("yaw_deg", query.reply_err.call_args.args[0])
        self.assertEqual(query.reply_err.call_args.kwargs, {"encoding": TEXT_ENCODING})
        self.assertAlmostEqual(yaw_deg, 10.0)


class PublishFramesTests(unittest.TestCase):
    def published_frame_indices(self, capture_times: list[float]) -> list[int]:
        capture = MagicMock()
        capture.read.side_effect = [
            (True, np.full((1, 1, 3), index, dtype=np.uint8))
            for index in range(len(capture_times))
        ] + [StopLoop()]
        published: list[int] = []
        with self.assertRaises(StopLoop):
            publish_frames(
                capture,
                [lambda frame: published.append(int(frame[0, 0, 0]))],
                frequency_hz=10.0,
                clock=iter(capture_times).__next__,
            )
        return published

    def test_faster_camera_is_downsampled_to_the_target_frequency(self) -> None:
        camera_times = [index / 15.0 for index in range(16)]

        self.assertEqual(
            self.published_frame_indices(camera_times),
            [0, 1, 3, 4, 6, 7, 9, 10, 12, 13, 15],
        )

    def test_jitter_does_not_drop_frames_when_rates_match(self) -> None:
        jittered_times = [0.0, 0.098, 0.203, 0.297, 0.402]

        self.assertEqual(self.published_frame_indices(jittered_times), [0, 1, 2, 3, 4])

    def test_late_frames_skip_missed_slots_without_a_burst(self) -> None:
        self.assertEqual(
            self.published_frame_indices([0.0, 0.33, 0.34, 0.47, 0.5]), [0, 1, 3]
        )

    def test_consecutive_read_failures_stop_the_loop(self) -> None:
        capture = MagicMock()
        capture.read.return_value = (False, None)

        with (
            patch("node.time.sleep"),
            self.assertRaisesRegex(RuntimeError, "consecutive"),
        ):
            publish_frames(capture, [], frequency_hz=10.0)

        self.assertEqual(capture.read.call_count, MAX_CONSECUTIVE_CAPTURE_FAILURES)


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = MagicMock()
        self.capture = MagicMock()

    def run_node(self) -> MagicMock:
        with (
            redirect_stdout(StringIO()),
            patch("node.zenoh.open") as open_session,
            patch("node.cv2.VideoCapture", return_value=self.capture),
            patch("node.publish_frames", side_effect=StopLoop) as publish_frames_mock,
            self.assertRaises((StopLoop, RuntimeError)),
        ):
            open_session.return_value.__enter__.return_value = self.session
            run(MagicMock(), EXAMPLE_CONFIG)
        return publish_frames_mock

    def test_declares_every_channel_then_releases_the_camera(self) -> None:
        publish_frames_mock = self.run_node()

        publisher_calls = self.session.declare_publisher.call_args_list
        self.assertEqual(
            [call.args[0] for call in publisher_calls],
            [
                "spherical_camera/equirectangular",
                "spherical_camera/view/agent",
                "spherical_camera/view/operator",
            ],
        )
        self.assertEqual(
            {call.kwargs["encoding"] for call in publisher_calls}, {JPEG_ENCODING}
        )
        self.assertEqual(
            [call.args[0] for call in self.session.declare_queryable.call_args_list],
            [
                "spherical_camera/view/agent/direction",
                "spherical_camera/view/operator/direction",
            ],
        )
        self.assertEqual(len(publish_frames_mock.call_args.args[1]), 3)
        self.capture.release.assert_called_once_with()

    def test_unopened_camera_fails_before_declaring_channels(self) -> None:
        self.capture.isOpened.return_value = False

        self.run_node()

        self.session.declare_publisher.assert_not_called()
        self.capture.release.assert_called_once_with()


class MainTests(unittest.TestCase):
    def test_invalid_configuration_exits_with_error(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            exit_code = main(["--node-config", "missing.json5"])

        self.assertEqual(exit_code, 1)
        self.assertIn("[ERROR]", stderr.getvalue())

    def test_keyboard_interrupt_stops_cleanly(self) -> None:
        with (
            redirect_stdout(StringIO()),
            patch("node.run", side_effect=KeyboardInterrupt),
        ):
            exit_code = main(
                [
                    "--zenoh-config",
                    "config/zenoh-config.example.json5",
                    "--node-config",
                    "config/node-config.example.json5",
                ]
            )

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
