"""Project dual-fisheye frames to equirectangular and perspective images."""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from config import DualFisheyeLenses, MountConfig

FloatArray = npt.NDArray[np.float32]
Rays = npt.NDArray[np.floating]
Rotation = npt.NDArray[np.float64]


def rotation_matrix(roll_deg: float, pitch_deg: float, yaw_deg: float) -> Rotation:
    roll, pitch, yaw = map(math.radians, (roll_deg, pitch_deg, yaw_deg))
    about_x = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, math.cos(roll), -math.sin(roll)],
            [0.0, math.sin(roll), math.cos(roll)],
        ]
    )
    about_y = np.array(
        [
            [math.cos(pitch), 0.0, math.sin(pitch)],
            [0.0, 1.0, 0.0],
            [-math.sin(pitch), 0.0, math.cos(pitch)],
        ]
    )
    about_z = np.array(
        [
            [math.cos(yaw), -math.sin(yaw), 0.0],
            [math.sin(yaw), math.cos(yaw), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    return about_z @ about_y @ about_x


def equirectangular_rays(size: tuple[int, int]) -> Rays:
    width, height = size
    longitude = math.pi * (1.0 - 2.0 * (np.arange(width) + 0.5) / width)
    latitude = 0.5 * math.pi * (1.0 - 2.0 * (np.arange(height) + 0.5) / height)
    longitude, latitude = np.meshgrid(longitude, latitude)
    return np.stack(
        (
            np.cos(latitude) * np.cos(longitude),
            np.cos(latitude) * np.sin(longitude),
            np.sin(latitude),
        ),
        axis=-1,
    )


def perspective_rays(size: tuple[int, int], fov_deg: float, yaw_deg: float) -> Rays:
    width, height = size
    focal_length = 0.5 * width / math.tan(math.radians(fov_deg) / 2.0)
    right, down = np.meshgrid(
        np.arange(width) + 0.5 - 0.5 * width, np.arange(height) + 0.5 - 0.5 * height
    )
    rays = np.stack((np.full_like(right, focal_length), -right, -down), axis=-1)
    rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
    return rays @ rotation_matrix(0.0, 0.0, yaw_deg).T


@dataclass(frozen=True)
class RemapTable:
    front_x: FloatArray
    front_y: FloatArray
    back_x: FloatArray
    back_y: FloatArray
    front_weight: FloatArray
    back_weight: FloatArray

    def apply(self, frame: cv2.typing.MatLike) -> cv2.typing.MatLike:
        front = cv2.remap(frame, self.front_x, self.front_y, cv2.INTER_LINEAR)
        back = cv2.remap(frame, self.back_x, self.back_y, cv2.INTER_LINEAR)
        return cv2.blendLinear(front, back, self.front_weight, self.back_weight)


def build_remap_table(
    body_rays: Rays, lenses: DualFisheyeLenses, mount: MountConfig
) -> RemapTable:
    body_from_camera = rotation_matrix(mount.roll_deg, mount.pitch_deg, mount.yaw_deg)
    camera_rays = body_rays @ body_from_camera
    front_off_axis_angle = np.arccos(np.clip(camera_rays[..., 0], -1.0, 1.0))
    lens_maps: list[tuple[FloatArray, FloatArray]] = []
    for lens, optical_axis_sign, off_axis_angle in (
        (lenses.front, 1.0, front_off_axis_angle),
        (lenses.back, -1.0, math.pi - front_off_axis_angle),
    ):
        image_angle = np.arctan2(
            camera_rays[..., 2], -optical_axis_sign * camera_rays[..., 1]
        ) + math.radians(lens.rotation_deg)
        image_radius = lens.radius * off_axis_angle / math.radians(lens.fov_deg / 2.0)
        map_x = lens.center[0] + image_radius * np.cos(image_angle)
        map_y = lens.center[1] - image_radius * np.sin(image_angle)
        lens_maps.append((map_x.astype(np.float32), map_y.astype(np.float32)))
    (front_x, front_y), (back_x, back_y) = lens_maps

    overlap_half_width = math.radians(
        min(lenses.front.fov_deg, lenses.back.fov_deg) / 2.0 - 90.0
    )
    front_weight = np.clip(
        0.5 + (0.5 * math.pi - front_off_axis_angle) / (2.0 * overlap_half_width),
        0.0,
        1.0,
    ).astype(np.float32)
    return RemapTable(
        front_x=front_x,
        front_y=front_y,
        back_x=back_x,
        back_y=back_y,
        front_weight=front_weight,
        back_weight=1.0 - front_weight,
    )
