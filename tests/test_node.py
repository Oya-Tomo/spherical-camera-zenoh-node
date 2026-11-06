import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from config import MountConfig, NodeConfig, ViewsConfig, load_node_config
from node import (
    JPEG_ENCODING,
    JSON_ENCODING,
    MAX_CONSECUTIVE_CAPTURE_FAILURES,
    TEXT_ENCODING,
    ViewChannel,
    ViewDirection,
    main,
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
        "channels": [{"view_key": "main"}],
    }
)
NO_MOUNT = MountConfig(roll_deg=0.0, pitch_deg=0.0, yaw_deg=0.0)
INITIAL_DIRECTION = ViewDirection(yaw_deg=360.0, pitch_deg=20.0)


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
                self.assertAlmostEqual(
                    ViewDirection(yaw_deg=yaw_deg, pitch_deg=0.0).yaw_deg, expected
                )

    def test_pitch_is_clamped_to_straight_up_and_down(self) -> None:
        cases = {-120.0: -90.0, -90.0: -90.0, -45.0: -45.0, 90.0: 90.0, 120.0: 90.0}
        for pitch_deg, expected in cases.items():
            with self.subTest(pitch_deg=pitch_deg):
                self.assertEqual(
                    ViewDirection(yaw_deg=0.0, pitch_deg=pitch_deg).pitch_deg, expected
                )

    def test_encoded_direction_round_trips(self) -> None:
        direction = ViewDirection(yaw_deg=-42.5, pitch_deg=12.5)

        self.assertEqual(
            parse_view_direction(direction.model_dump_json().encode()), direction
        )
        self.assertEqual(
            parse_view_direction(b'{"yaw_deg": 0, "pitch_deg": 120}'),
            ViewDirection(yaw_deg=0.0, pitch_deg=90.0),
        )

    def test_malformed_payloads_are_rejected_with_a_reason(self) -> None:
        payloads = (
            b"not json",
            b"{}",
            b'{"yaw_deg": 0}',
            b'{"pitch_deg": 0}',
            b'{"yaw_deg": "left", "pitch_deg": 0}',
            b'{"yaw_deg": 1e999, "pitch_deg": 0}',
            b'{"yaw_deg": 0, "pitch_deg": -1e999}',
            b'{"yaw_deg": 0, "pitch_deg": 0, "roll_deg": 0}',
        )
        for payload in payloads:
            with (
                self.subTest(payload=payload),
                self.assertRaisesRegex(ValueError, ".+"),
            ):
                parse_view_direction(payload)

    def test_direction_key_extends_the_view_image_key(self) -> None:
        self.assertEqual(
            view_direction_key("camera/view/main"), "camera/view/main/direction"
        )


class ViewChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.publisher = MagicMock()
        self.view_channel = ViewChannel(
            INITIAL_DIRECTION,
            SMALL_VIEWS,
            EXAMPLE_CONFIG.lenses,
            NO_MOUNT,
            self.publisher,
            "camera/dir",
        )

    def publish(self) -> tuple[bytes, ViewDirection]:
        self.view_channel.publish(FRAME)
        call = self.publisher.put.call_args
        return call.args[0], parse_view_direction(call.kwargs["attachment"].encode())

    def answer(self, payload: bytes | None) -> MagicMock:
        query = direction_query(payload)
        self.view_channel.answer_direction_query(query)
        return query

    def test_publishes_sized_jpeg_tagged_with_its_initial_direction(self) -> None:
        jpeg, direction = self.publish()

        image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert image is not None
        self.assertEqual(image.shape, (48, 64, 3))
        self.assertEqual(direction, INITIAL_DIRECTION)

    def test_query_without_payload_reports_the_direction(self) -> None:
        query = self.answer(None)
        _, direction = self.publish()

        key, reply_payload = query.reply.call_args.args
        self.assertEqual(key, "camera/dir")
        self.assertEqual(query.reply.call_args.kwargs, {"encoding": JSON_ENCODING})
        self.assertEqual(json.loads(reply_payload), {"yaw_deg": 0.0, "pitch_deg": 20.0})
        self.assertEqual(direction, INITIAL_DIRECTION)

    def test_query_with_direction_steers_the_following_frames(self) -> None:
        cases = {
            b'{"yaw_deg": 0, "pitch_deg": -45}': {"yaw_deg": 0.0, "pitch_deg": -45.0},
            b'{"yaw_deg": 0, "pitch_deg": 120}': {"yaw_deg": 0.0, "pitch_deg": 90.0},
            b'{"yaw_deg": 270, "pitch_deg": 0}': {"yaw_deg": -90.0, "pitch_deg": 0.0},
        }
        previous_jpeg, _ = self.publish()
        for payload, expected in cases.items():
            with self.subTest(payload=payload):
                query = self.answer(payload)
                jpeg, direction = self.publish()

                self.assertEqual(json.loads(query.reply.call_args.args[1]), expected)
                self.assertEqual(direction.model_dump(), expected)
                self.assertNotEqual(jpeg, previous_jpeg)
            previous_jpeg = jpeg

    def test_each_view_keeps_its_own_direction(self) -> None:
        other_publisher = MagicMock()
        other_view_channel = ViewChannel(
            INITIAL_DIRECTION,
            SMALL_VIEWS,
            EXAMPLE_CONFIG.lenses,
            NO_MOUNT,
            other_publisher,
            "camera/other/dir",
        )

        self.answer(b'{"yaw_deg": 0, "pitch_deg": -45}')
        other_view_channel.publish(FRAME)

        self.assertEqual(
            parse_view_direction(
                other_publisher.put.call_args.kwargs["attachment"].encode()
            ),
            INITIAL_DIRECTION,
        )

    def test_malformed_query_gets_an_error_and_keeps_the_direction(self) -> None:
        cases = {
            b'{"yaw_deg": "left", "pitch_deg": 0}': "yaw_deg",
            b'{"yaw_deg": 0}': "pitch_deg",
            b'{"yaw_deg": 0, "pitch_deg": 0, "roll_deg": 0}': "roll_deg",
            b'{"yaw_deg": 0, "pitch_deg": 1e999}': "pitch_deg",
        }
        for payload, reason in cases.items():
            with self.subTest(payload=payload):
                query = self.answer(payload)
                _, direction = self.publish()

                query.reply.assert_not_called()
                self.assertIn(reason, query.reply_err.call_args.args[0])
                self.assertEqual(
                    query.reply_err.call_args.kwargs, {"encoding": TEXT_ENCODING}
                )
                self.assertEqual(direction, INITIAL_DIRECTION)


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

    def run_node(self, node_config: NodeConfig = EXAMPLE_CONFIG) -> MagicMock:
        with (
            redirect_stdout(StringIO()),
            patch("node.zenoh.open") as open_session,
            patch("node.cv2.VideoCapture", return_value=self.capture),
            patch("node.publish_frames", side_effect=StopLoop) as publish_frames_mock,
            self.assertRaises((StopLoop, RuntimeError)),
        ):
            open_session.return_value.__enter__.return_value = self.session
            run(MagicMock(), node_config)
        return publish_frames_mock

    def test_declares_every_channel_then_releases_the_camera(self) -> None:
        publish_frames_mock = self.run_node()

        publisher_calls = self.session.declare_publisher.call_args_list
        self.assertEqual(
            [call.args[0] for call in publisher_calls],
            [
                "spherical_camera/equirectangular",
                "spherical_camera/view/main",
                "spherical_camera/view/sub",
            ],
        )
        self.assertEqual(
            {call.kwargs["encoding"] for call in publisher_calls}, {JPEG_ENCODING}
        )
        self.assertEqual(
            [call.args[0] for call in self.session.declare_queryable.call_args_list],
            [
                "spherical_camera/view/main/direction",
                "spherical_camera/view/sub/direction",
            ],
        )
        self.assertEqual(len(publish_frames_mock.call_args.args[1]), 3)
        self.capture.release.assert_called_once_with()

    def test_views_start_at_their_configured_directions(self) -> None:
        document = EXAMPLE_CONFIG.model_dump()
        document["views"]["channels"] = [
            {"view_key": "main"},
            {"view_key": "sub", "initial_yaw_deg": -30.0, "initial_pitch_deg": 45.0},
        ]
        self.run_node(NodeConfig.model_validate(document))

        replied_directions: list[object] = []
        for call in self.session.declare_queryable.call_args_list:
            query = direction_query(None)
            call.args[1](query)
            replied_directions.append(json.loads(query.reply.call_args.args[1]))
        self.assertEqual(
            replied_directions,
            [
                {"yaw_deg": 0.0, "pitch_deg": 0.0},
                {"yaw_deg": -30.0, "pitch_deg": 45.0},
            ],
        )

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
