import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pygame

from config import ViewerConfig, load_config
from examples import viewer
from node import ViewDirection, parse_view_direction

os.environ["SDL_VIDEODRIVER"] = "dummy"
EXAMPLE_ZENOH_CONFIG_PATH = "examples/viewer-zenoh-config.example.json5"
EXAMPLE_VIEWER_CONFIG_PATH = "examples/viewer-config.example.json5"
EXAMPLE_CONFIG = load_config(Path(EXAMPLE_VIEWER_CONFIG_PATH), ViewerConfig)
INITIAL_DIRECTION = ViewDirection(yaw_deg=30.0, pitch_deg=85.0)


def key_down(key: int) -> list[pygame.event.Event]:
    return [pygame.event.Event(pygame.KEYDOWN, key=key)]


def image_sample(width: int, height: int) -> MagicMock:
    _, jpeg = cv2.imencode(".jpg", np.zeros((height, width, 3), dtype=np.uint8))
    sample = MagicMock()
    sample.payload.to_bytes.return_value = jpeg.tobytes()
    return sample


class ViewerRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = MagicMock()
        self.requested_directions: list[ViewDirection | None] = []
        self.session.get.side_effect = self.answer_direction_query
        subscriber = self.session.declare_subscriber.return_value.__enter__.return_value
        subscriber.try_recv.side_effect = [image_sample(64, 48)] + [None] * 100

    def answer_direction_query(
        self, key: str, *, payload: str | None, **_: object
    ) -> list[MagicMock]:
        direction = None if payload is None else parse_view_direction(payload.encode())
        self.requested_directions.append(direction)
        reply = MagicMock(err=None)
        reply.ok.payload.to_bytes.return_value = (
            (direction or INITIAL_DIRECTION).model_dump_json().encode()
        )
        return [reply]

    def run_viewer(
        self,
        events: list[list[pygame.event.Event]],
        viewer_config: ViewerConfig = EXAMPLE_CONFIG,
    ) -> tuple[MagicMock, MagicMock]:
        with (
            redirect_stdout(StringIO()),
            patch("examples.viewer.zenoh.open") as open_session,
            patch("examples.viewer.pygame.event.get", side_effect=events),
            patch(
                "examples.viewer.pygame.display.set_mode", wraps=pygame.display.set_mode
            ) as set_mode,
            patch("examples.viewer.pygame.font.Font") as font,
        ):
            open_session.return_value.__enter__.return_value = self.session
            font.return_value.render.return_value = pygame.Surface((1, 1))
            viewer.run(MagicMock(), viewer_config)
        return set_mode, font.return_value.render

    def test_keys_steer_the_view_through_direction_queries(self) -> None:
        events = [
            [],
            key_down(pygame.K_LEFT),
            key_down(pygame.K_RIGHT),
            key_down(pygame.K_RIGHT),
            key_down(pygame.K_DOWN),
            key_down(pygame.K_DOWN),
            key_down(pygame.K_UP),
            key_down(pygame.K_r),
            key_down(pygame.K_q),
        ]

        _, render = self.run_viewer(events)

        self.assertEqual(
            [
                None if direction is None else (direction.yaw_deg, direction.pitch_deg)
                for direction in self.requested_directions
            ],
            [
                None,
                (35.0, 85.0),
                (30.0, 85.0),
                (25.0, 85.0),
                (25.0, 90.0),
                (25.0, 90.0),
                (25.0, 85.0),
                (0.0, 0.0),
            ],
        )
        self.assertEqual(
            self.session.declare_subscriber.call_args.args[0],
            "spherical_camera/view/sub",
        )
        self.assertEqual(
            {call.args[0] for call in self.session.get.call_args_list},
            {"spherical_camera/view/sub/direction"},
        )
        rendered_lines = [call.args[0] for call in render.call_args_list]
        self.assertIn("Yaw: +25.0 deg", rendered_lines)
        self.assertIn("Pitch: +90.0 deg", rendered_lines)
        self.assertIn("Pitch: +0.0 deg", rendered_lines)
        self.assertIn("Left / Right: turn", rendered_lines)
        self.assertIn("Up / Down: tilt", rendered_lines)

    def test_configured_view_is_displayed_and_steered(self) -> None:
        viewer_config = ViewerConfig(
            zenoh_key_prefix="camera/spherical", view_key="main", direction_step_deg=5.0
        )

        self.run_viewer([key_down(pygame.K_LEFT), key_down(pygame.K_q)], viewer_config)

        self.assertEqual(
            self.session.declare_subscriber.call_args.args[0],
            "camera/spherical/view/main",
        )
        self.assertEqual(
            {call.args[0] for call in self.session.get.call_args_list},
            {"camera/spherical/view/main/direction"},
        )

    def test_configured_step_angle_sizes_each_turn_and_tilt(self) -> None:
        events = [
            key_down(pygame.K_LEFT),
            key_down(pygame.K_UP),
            key_down(pygame.K_RIGHT),
            key_down(pygame.K_DOWN),
            key_down(pygame.K_q),
        ]

        self.run_viewer(
            events, EXAMPLE_CONFIG.model_copy(update={"direction_step_deg": 12.5})
        )

        self.assertEqual(
            self.requested_directions,
            [
                None,
                ViewDirection(yaw_deg=42.5, pitch_deg=85.0),
                ViewDirection(yaw_deg=42.5, pitch_deg=72.5),
                ViewDirection(yaw_deg=30.0, pitch_deg=72.5),
                ViewDirection(yaw_deg=30.0, pitch_deg=85.0),
            ],
        )

    def test_window_follows_the_received_image_and_closes(self) -> None:
        set_mode, _ = self.run_viewer([[], [pygame.event.Event(pygame.QUIT)]])

        self.assertEqual(set_mode.call_args.args[0], (64, 48))
        self.assertFalse(pygame.display.get_init())

    def test_unanswered_and_failed_direction_queries_stop_the_viewer(self) -> None:
        failed_reply = MagicMock(ok=None)
        failed_reply.err.payload.to_string.return_value = "bad yaw"
        for replies, message in (([], "no node answered"), ([failed_reply], "bad yaw")):
            self.session.get.side_effect = None
            self.session.get.return_value = replies
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(RuntimeError, message),
            ):
                self.run_viewer([])


class ViewerMainTests(unittest.TestCase):
    def test_reads_the_given_configuration_files(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("examples.viewer.run") as run,
        ):
            zenoh_config_path = Path(directory, "zenoh.json5")
            zenoh_config_path.write_text('{mode: "client"}', encoding="utf-8")
            viewer_config_path = Path(directory, "viewer.json5")
            viewer_config_path.write_text(
                '{zenoh_key_prefix: "camera", view_key: "main", '
                "direction_step_deg: 10.0}",
                encoding="utf-8",
            )
            exit_code = viewer.main(
                [
                    "--zenoh-config",
                    str(zenoh_config_path),
                    "--viewer-config",
                    str(viewer_config_path),
                ]
            )

        zenoh_config, viewer_config = run.call_args.args
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(zenoh_config.get_json("mode")), "client")
        self.assertEqual(
            viewer_config,
            ViewerConfig(
                zenoh_key_prefix="camera", view_key="main", direction_step_deg=10.0
            ),
        )

    def test_absent_or_invalid_viewer_config_exits_with_an_error_naming_it(
        self,
    ) -> None:
        viewer_config_texts = {
            "absent.json5": None,
            "unknown-field.json5": (
                '{zenoh_key_prefix: "camera", view_key: "main", '
                "direction_step_deg: 10.0, unknown: 1}"
            ),
            "duplicate-field.json5": (
                '{zenoh_key_prefix: "camera", view_key: "main", view_key: "sub", '
                "direction_step_deg: 10.0}"
            ),
        }
        for file_name, text in viewer_config_texts.items():
            stderr = StringIO()
            with (
                self.subTest(file_name=file_name),
                tempfile.TemporaryDirectory() as directory,
                redirect_stderr(stderr),
                patch("examples.viewer.run") as run,
            ):
                viewer_config_path = Path(directory, file_name)
                if text is not None:
                    viewer_config_path.write_text(text, encoding="utf-8")
                exit_code = viewer.main(
                    [
                        "--zenoh-config",
                        EXAMPLE_ZENOH_CONFIG_PATH,
                        "--viewer-config",
                        str(viewer_config_path),
                    ]
                )

                run.assert_not_called()
                self.assertEqual(exit_code, 1)
                self.assertIn("[ERROR]", stderr.getvalue())
                self.assertIn(file_name, stderr.getvalue())

    def test_runtime_errors_exit_with_status_one(self) -> None:
        stderr = StringIO()
        with (
            redirect_stderr(stderr),
            patch("examples.viewer.run", side_effect=RuntimeError("no node")),
        ):
            exit_code = viewer.main(
                [
                    "--zenoh-config",
                    EXAMPLE_ZENOH_CONFIG_PATH,
                    "--viewer-config",
                    EXAMPLE_VIEWER_CONFIG_PATH,
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("[ERROR] no node", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
