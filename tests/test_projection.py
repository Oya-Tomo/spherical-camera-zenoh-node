import math
import unittest

import numpy as np
import numpy.typing as npt

from config import DualFisheyeLenses, FisheyeLens, MountConfig
from projection import (
    build_remap_table,
    equirectangular_rays,
    perspective_rays,
    rotation_matrix,
)

FRONT_CENTER = (320.0, 320.0)
BACK_CENTER = (960.0, 320.0)
LENS_RADIUS = 300.0
LENS_FOV_DEG = 200.0
QUARTER_SPHERE_RADIUS = LENS_RADIUS * 90.0 / (LENS_FOV_DEG / 2.0)
NO_MOUNT = MountConfig(roll_deg=0.0, pitch_deg=0.0, yaw_deg=0.0)


def make_lenses(front_rotation_deg: float = 0.0) -> DualFisheyeLenses:
    return DualFisheyeLenses(
        front=FisheyeLens(
            center=FRONT_CENTER,
            radius=LENS_RADIUS,
            fov_deg=LENS_FOV_DEG,
            rotation_deg=front_rotation_deg,
        ),
        back=FisheyeLens(
            center=BACK_CENTER,
            radius=LENS_RADIUS,
            fov_deg=LENS_FOV_DEG,
            rotation_deg=0.0,
        ),
    )


def as_ray_grid(*rays: tuple[float, float, float]) -> npt.NDArray[np.float64]:
    grid = np.array([rays], dtype=np.float64)
    return grid / np.linalg.norm(grid, axis=-1, keepdims=True)


class RayTests(unittest.TestCase):
    def test_equirectangular_columns_follow_yaw_around_the_robot(self) -> None:
        rays = equirectangular_rays((360, 180))
        equator = rays[90]

        np.testing.assert_allclose(equator[180], [1.0, 0.0, 0.0], atol=0.02)
        np.testing.assert_allclose(equator[90], [0.0, 1.0, 0.0], atol=0.02)
        np.testing.assert_allclose(equator[270], [0.0, -1.0, 0.0], atol=0.02)
        np.testing.assert_allclose(rays[0, 0], [0.0, 0.0, 1.0], atol=0.02)

    def test_perspective_view_centers_on_yaw_with_horizontal_fov(self) -> None:
        rays = perspective_rays((400, 300), 90.0, 90.0)
        center = rays[150, 200]
        left_edge = rays[150, 0]

        np.testing.assert_allclose(center, [0.0, 1.0, 0.0], atol=0.01)
        self.assertAlmostEqual(
            math.degrees(math.acos(center @ left_edge)), 45.0, delta=0.5
        )
        self.assertLess(left_edge[0], 0.0)

    def test_rotation_matrix_follows_roll_pitch_yaw_convention(self) -> None:
        forward = np.array([1.0, 0.0, 0.0])
        left = np.array([0.0, 1.0, 0.0])

        np.testing.assert_allclose(
            rotation_matrix(0.0, 0.0, 90.0) @ forward, left, atol=1e-12
        )
        np.testing.assert_allclose(
            rotation_matrix(0.0, 30.0, 0.0) @ forward,
            [math.cos(math.radians(30.0)), 0.0, -math.sin(math.radians(30.0))],
            atol=1e-12,
        )
        np.testing.assert_allclose(
            rotation_matrix(90.0, 0.0, 0.0) @ left, [0.0, 0.0, 1.0], atol=1e-12
        )


class RemapTableTests(unittest.TestCase):
    def test_lens_axes_and_sides_follow_the_equidistant_model(self) -> None:
        table = build_remap_table(
            as_ray_grid((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 1, 0)),
            make_lenses(),
            NO_MOUNT,
        )

        front = np.stack((table.front_x[0], table.front_y[0]), axis=-1)
        back = np.stack((table.back_x[0], table.back_y[0]), axis=-1)
        np.testing.assert_allclose(front[0], FRONT_CENTER, atol=1e-3)
        np.testing.assert_allclose(back[1], BACK_CENTER, atol=1e-3)
        np.testing.assert_allclose(
            front[2],
            (FRONT_CENTER[0], FRONT_CENTER[1] - QUARTER_SPHERE_RADIUS),
            atol=1e-3,
        )
        np.testing.assert_allclose(
            front[3],
            (FRONT_CENTER[0] - QUARTER_SPHERE_RADIUS, FRONT_CENTER[1]),
            atol=1e-3,
        )
        np.testing.assert_allclose(
            back[3], (BACK_CENTER[0] + QUARTER_SPHERE_RADIUS, BACK_CENTER[1]), atol=1e-3
        )

    def test_lens_rotation_turns_the_image_counterclockwise(self) -> None:
        table = build_remap_table(as_ray_grid((0, 0, 1)), make_lenses(90.0), NO_MOUNT)

        self.assertAlmostEqual(
            float(table.front_x[0, 0]),
            FRONT_CENTER[0] - QUARTER_SPHERE_RADIUS,
            places=3,
        )
        self.assertAlmostEqual(float(table.front_y[0, 0]), FRONT_CENTER[1], places=3)

    def test_mount_orientation_is_compensated(self) -> None:
        pitch = math.radians(30.0)
        mounts_and_front_axis_rays = (
            (MountConfig(roll_deg=0.0, pitch_deg=0.0, yaw_deg=90.0), (0.0, 1.0, 0.0)),
            (
                MountConfig(roll_deg=0.0, pitch_deg=30.0, yaw_deg=0.0),
                (math.cos(pitch), 0.0, -math.sin(pitch)),
            ),
        )
        for mount, front_axis_ray in mounts_and_front_axis_rays:
            with self.subTest(mount=mount):
                table = build_remap_table(
                    as_ray_grid(front_axis_ray), make_lenses(), mount
                )
                self.assertAlmostEqual(
                    float(table.front_x[0, 0]), FRONT_CENTER[0], places=3
                )
                self.assertAlmostEqual(
                    float(table.front_y[0, 0]), FRONT_CENTER[1], places=3
                )

    def test_overlap_blends_from_front_to_back_lens(self) -> None:
        table = build_remap_table(
            as_ray_grid((1, 0, 0), (0, 1, 0), (-1, 0, 0)), make_lenses(), NO_MOUNT
        )

        np.testing.assert_allclose(table.front_weight[0], [1.0, 0.5, 0.0], atol=1e-6)
        np.testing.assert_allclose(
            table.front_weight + table.back_weight, 1.0, atol=1e-6
        )

    def test_apply_samples_each_lens_into_the_output(self) -> None:
        frame = np.zeros((640, 1280, 3), dtype=np.uint8)
        frame[:, :640] = (0, 0, 255)
        frame[:, 640:] = (255, 0, 0)
        table = build_remap_table(
            as_ray_grid((1, 0, 0), (-1, 0, 0)), make_lenses(), NO_MOUNT
        )

        image = table.apply(frame)

        np.testing.assert_array_equal(image[0], [[0, 0, 255], [255, 0, 0]])


if __name__ == "__main__":
    unittest.main()
