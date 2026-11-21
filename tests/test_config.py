import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import zenoh
from pydantic import ValidationError

from config import NodeConfig, ViewerConfig, load_config

LENS = {
    "center": [320.0, 320.0],
    "radius": 300.0,
    "fov_deg": 190.0,
    "rotation_deg": 0.0,
}
VALID_NODE_CONFIG: dict[str, Any] = {
    "zenoh_key_prefix": "camera/spherical",
    "source": "/dev/video4",
    "mount": {"roll_deg": 0.0, "pitch_deg": 0.0, "yaw_deg": 0.0},
    "lenses": {"front": LENS, "back": LENS},
    "equirectangular": {"size": [1280, 640], "jpeg_quality": 90},
    "views": {
        "fov_deg": 90.0,
        "size": [640, 480],
        "jpeg_quality": 90,
        "channels": [
            {"view_key": "main"},
            {"view_key": "sub", "direction": {"yaw_deg": 180.0, "pitch_deg": -30.0}},
            {"view_key": "rear", "fixed": True},
        ],
    },
    "publisher": {
        "publish_frequency_hz": 15.0,
        "congestion_control": "drop",
        "reliability": "best_effort",
    },
}


VALID_VIEWER_CONFIG: dict[str, Any] = {
    "zenoh_key_prefix": "camera/spherical",
    "view_key": "sub",
    "direction_step_deg": 5.0,
}


def config_with(path: tuple[str | int, ...], value: object) -> dict[str, Any]:
    document = copy.deepcopy(VALID_NODE_CONFIG)
    parent: Any = document
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return document


def config_without(*fields: str) -> dict[str, Any]:
    return {key: value for key, value in VALID_NODE_CONFIG.items() if key not in fields}


class ExampleConfigTests(unittest.TestCase):
    def test_example_node_config_is_valid(self) -> None:
        config = load_config(Path("config/node-config.example.json5"), NodeConfig)

        assert config.views is not None
        self.assertIsNotNone(config.equirectangular)
        self.assertEqual(
            [(view.view_key, view.fixed) for view in config.views.channels],
            [("main", False), ("sub", False), ("rear", True)],
        )

    def test_example_viewer_config_selects_a_view_of_the_example_node(self) -> None:
        node_config = load_config(Path("config/node-config.example.json5"), NodeConfig)
        viewer_config = load_config(
            Path("examples/viewer-config.example.json5"), ViewerConfig
        )

        assert node_config.views is not None
        self.assertEqual(viewer_config.zenoh_key_prefix, node_config.zenoh_key_prefix)
        self.assertIn(
            viewer_config.view_key,
            [view.view_key for view in node_config.views.channels],
        )
        self.assertEqual(viewer_config.direction_step_deg, 5.0)

    def test_example_zenoh_configs_are_valid(self) -> None:
        modes = {
            path: json.loads(zenoh.Config.from_file(path).get_json("mode"))
            for path in (
                "config/zenoh-config.example.json5",
                "examples/viewer-zenoh-config.example.json5",
            )
        }

        self.assertEqual(list(modes.values()), ["peer", "peer"])


class NodeConfigTests(unittest.TestCase):
    def test_channels_default_to_steerable_views_facing_forward_and_level(
        self,
    ) -> None:
        config = NodeConfig.model_validate(VALID_NODE_CONFIG)

        assert config.views is not None
        self.assertEqual(
            [
                (view.direction.yaw_deg, view.direction.pitch_deg, view.fixed)
                for view in config.views.channels
            ],
            [(0.0, 0.0, False), (180.0, -30.0, False), (0.0, 0.0, True)],
        )

    def test_direction_pitch_accepts_straight_up_and_down(self) -> None:
        for pitch_deg in (-90.0, 90.0):
            with self.subTest(pitch_deg=pitch_deg):
                config = NodeConfig.model_validate(
                    config_with(
                        ("views", "channels", 0, "direction"),
                        {"yaw_deg": 0.0, "pitch_deg": pitch_deg},
                    )
                )

                assert config.views is not None
                self.assertEqual(
                    config.views.channels[0].direction.pitch_deg, pitch_deg
                )

    def test_source_accepts_device_path_or_index(self) -> None:
        for source in ("/dev/v4l/by-id/theta", 0):
            with self.subTest(source=source):
                self.assertEqual(
                    NodeConfig.model_validate(config_with(("source",), source)).source,
                    source,
                )

    def test_either_output_alone_is_enough(self) -> None:
        for output in ("equirectangular", "views"):
            with self.subTest(output=output):
                NodeConfig.model_validate(config_without(output))

    def test_invalid_configs_are_rejected(self) -> None:
        invalid_configs = {
            "no outputs": config_without("equirectangular", "views"),
            "missing mount": config_without("mount"),
            "unknown field": config_with(("unknown",), 1),
            "wildcard prefix": config_with(("zenoh_key_prefix",), "camera/*"),
            "blank prefix": config_with(("zenoh_key_prefix",), " "),
            "blank source": config_with(("source",), " "),
            "negative source index": config_with(("source",), -1),
            "infinite mount angle": config_with(("mount", "pitch_deg"), float("inf")),
            "lens fov of 180": config_with(("lenses", "front", "fov_deg"), 180.0),
            "equirectangular not 2:1": config_with(
                ("equirectangular", "size"), [1280, 720]
            ),
            "view fov of 180": config_with(("views", "fov_deg"), 180.0),
            "jpeg quality above 100": config_with(("views", "jpeg_quality"), 101),
            "no view channels": config_with(("views", "channels"), []),
            "multi-segment view key": config_with(
                ("views", "channels", 0, "view_key"), "a/b"
            ),
            "direction without yaw": config_with(
                ("views", "channels", 0, "direction"), {"pitch_deg": 0.0}
            ),
            "direction without pitch": config_with(
                ("views", "channels", 0, "direction"), {"yaw_deg": 0.0}
            ),
            "direction with roll": config_with(
                ("views", "channels", 0, "direction"),
                {"yaw_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0},
            ),
            "infinite direction yaw": config_with(
                ("views", "channels", 0, "direction"),
                {"yaw_deg": float("inf"), "pitch_deg": 0.0},
            ),
            "direction pitch not a number": config_with(
                ("views", "channels", 0, "direction"),
                {"yaw_deg": 0.0, "pitch_deg": float("nan")},
            ),
            "direction pitch above 90": config_with(
                ("views", "channels", 0, "direction"),
                {"yaw_deg": 0.0, "pitch_deg": 90.5},
            ),
            "direction pitch below -90": config_with(
                ("views", "channels", 0, "direction"),
                {"yaw_deg": 0.0, "pitch_deg": -120.0},
            ),
            "fixed given as text": config_with(
                ("views", "channels", 0, "fixed"), "true"
            ),
            "fixed given as number": config_with(("views", "channels", 0, "fixed"), 1),
            "duplicate view keys": config_with(
                ("views", "channels"), [{"view_key": "main"}, {"view_key": "main"}]
            ),
            "zero frequency": config_with(("publisher", "publish_frequency_hz"), 0.0),
            "unknown reliability": config_with(("publisher", "reliability"), "maybe"),
        }
        for case, document in invalid_configs.items():
            with self.subTest(case=case), self.assertRaises(ValidationError):
                NodeConfig.model_validate(document)


class ViewerConfigTests(unittest.TestCase):
    def test_step_accepts_angles_up_to_90_degrees(self) -> None:
        for direction_step_deg in (0.5, 90.0):
            with self.subTest(direction_step_deg=direction_step_deg):
                config = ViewerConfig.model_validate(
                    VALID_VIEWER_CONFIG | {"direction_step_deg": direction_step_deg}
                )

                self.assertEqual(config.direction_step_deg, direction_step_deg)

    def test_every_field_is_required(self) -> None:
        for field in VALID_VIEWER_CONFIG:
            document = {
                key: value for key, value in VALID_VIEWER_CONFIG.items() if key != field
            }
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ViewerConfig.model_validate(document)

    def test_invalid_configs_are_rejected(self) -> None:
        invalid_configs = {
            "unknown field": {"unknown": 1},
            "wildcard prefix": {"zenoh_key_prefix": "camera/*"},
            "blank prefix": {"zenoh_key_prefix": " "},
            "multi-segment view key": {"view_key": "a/b"},
            "zero step": {"direction_step_deg": 0.0},
            "step above 90": {"direction_step_deg": 90.5},
            "step given as text": {"direction_step_deg": "5.0"},
        }
        for case, fields in invalid_configs.items():
            with self.subTest(case=case), self.assertRaises(ValidationError):
                ViewerConfig.model_validate(VALID_VIEWER_CONFIG | fields)


class LoadConfigTests(unittest.TestCase):
    def load_text(self, text: str) -> NodeConfig:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "node.json5")
            path.write_text(text, encoding="utf-8")
            return load_config(path, NodeConfig)

    def test_duplicate_json5_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid JSON5"):
            self.load_text("{source: 0, source: 1}")

    def test_validation_errors_name_the_file(self) -> None:
        documents = {
            "empty": {},
            "fixed given as text": config_with(
                ("views", "channels", 0, "fixed"), "true"
            ),
        }
        for case, document in documents.items():
            with (
                self.subTest(case=case),
                self.assertRaisesRegex(
                    ValueError, r"node\.json5: invalid configuration"
                ),
            ):
                self.load_text(json.dumps(document))


if __name__ == "__main__":
    unittest.main()
