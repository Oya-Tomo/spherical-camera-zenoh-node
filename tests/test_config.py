import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import zenoh
from pydantic import ValidationError

from config import NodeConfig, load_node_config

LENS = {
    "center": [320.0, 320.0],
    "radius": 300.0,
    "fov_deg": 190.0,
    "rotation_deg": 0.0,
}
VALID_CONFIG: dict[str, Any] = {
    "zenoh_key_prefix": "robot/spherical_camera",
    "source": "/dev/video4",
    "mount": {"roll_deg": 0.0, "pitch_deg": 0.0, "yaw_deg": 0.0},
    "lenses": {"front": LENS, "back": LENS},
    "equirectangular": {"size": [1280, 640], "jpeg_quality": 90},
    "views": {
        "fov_deg": 90.0,
        "size": [640, 480],
        "jpeg_quality": 90,
        "channels": [
            {"view_key": "agent"},
            {"view_key": "operator", "initial_yaw_deg": 180.0},
        ],
    },
    "publisher": {
        "publish_frequency_hz": 15.0,
        "congestion_control": "drop",
        "reliability": "best_effort",
    },
}


def config_with(path: tuple[str | int, ...], value: object) -> dict[str, Any]:
    document = copy.deepcopy(VALID_CONFIG)
    parent: Any = document
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return document


def config_without(*fields: str) -> dict[str, Any]:
    return {key: value for key, value in VALID_CONFIG.items() if key not in fields}


class ExampleConfigTests(unittest.TestCase):
    def test_example_node_config_is_valid(self) -> None:
        config = load_node_config(Path("config/node-config.example.json5"))

        assert config.views is not None
        self.assertIsNotNone(config.equirectangular)
        self.assertEqual(
            [view.view_key for view in config.views.channels], ["agent", "operator"]
        )

    def test_example_zenoh_configs_are_valid(self) -> None:
        modes = {
            path: json.loads(zenoh.Config.from_file(path).get_json("mode"))
            for path in (
                "config/zenoh-config.example.json5",
                "examples/viewer-zenoh-config.json5",
            )
        }

        self.assertEqual(list(modes.values()), ["peer", "client"])


class NodeConfigTests(unittest.TestCase):
    def test_initial_yaw_defaults_to_forward(self) -> None:
        config = NodeConfig.model_validate(VALID_CONFIG)

        assert config.views is not None
        self.assertEqual(
            [view.initial_yaw_deg for view in config.views.channels], [0.0, 180.0]
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
            "wildcard prefix": config_with(("zenoh_key_prefix",), "robot/*"),
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
            "duplicate view keys": config_with(
                ("views", "channels"), [{"view_key": "agent"}, {"view_key": "agent"}]
            ),
            "zero frequency": config_with(("publisher", "publish_frequency_hz"), 0.0),
            "unknown reliability": config_with(("publisher", "reliability"), "maybe"),
        }
        for case, document in invalid_configs.items():
            with self.subTest(case=case), self.assertRaises(ValidationError):
                NodeConfig.model_validate(document)


class LoadNodeConfigTests(unittest.TestCase):
    def load_text(self, text: str) -> NodeConfig:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "node.json5")
            path.write_text(text, encoding="utf-8")
            return load_node_config(path)

    def test_duplicate_json5_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid JSON5"):
            self.load_text("{source: 0, source: 1}")

    def test_validation_errors_name_the_file(self) -> None:
        with self.assertRaisesRegex(
            ValueError, r"node\.json5: invalid node configuration"
        ):
            self.load_text("{}")


if __name__ == "__main__":
    unittest.main()
