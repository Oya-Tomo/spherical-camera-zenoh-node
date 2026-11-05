"""Validated JSON5 settings for the spherical camera node."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

import json5
import zenoh
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
JpegQuality = Annotated[int, Field(strict=True, ge=0, le=100)]
FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
PositiveFloat = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
ImageSize = tuple[PositiveInt, PositiveInt]


def _validate_concrete_key(key: str, field_name: str) -> str:
    try:
        zenoh.KeyExpr(key)
    except zenoh.ZError as error:
        raise ValueError(f"{field_name} must be a valid Zenoh key: {error}") from error
    if not key.strip() or "*" in key:
        raise ValueError(f"{field_name} must be a non-empty key without wildcards")
    return key


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MountConfig(ConfigModel):
    roll_deg: FiniteFloat
    pitch_deg: FiniteFloat
    yaw_deg: FiniteFloat


class FisheyeLens(ConfigModel):
    center: tuple[FiniteFloat, FiniteFloat]
    radius: PositiveFloat
    fov_deg: Annotated[float, Field(strict=True, gt=180, lt=360)]
    rotation_deg: FiniteFloat


class DualFisheyeLenses(ConfigModel):
    front: FisheyeLens
    back: FisheyeLens


class EquirectangularConfig(ConfigModel):
    size: ImageSize
    jpeg_quality: JpegQuality

    @field_validator("size")
    @classmethod
    def validate_aspect_ratio(cls, size: tuple[int, int]) -> tuple[int, int]:
        width, height = size
        if width != 2 * height:
            raise ValueError("equirectangular size must have a 2:1 aspect ratio")
        return size


class ViewChannelConfig(ConfigModel):
    view_key: StrictStr
    initial_yaw_deg: FiniteFloat = 0.0
    initial_pitch_deg: Annotated[float, Field(strict=True, ge=-90, le=90)] = 0.0

    @field_validator("view_key")
    @classmethod
    def validate_view_key(cls, view_key: str) -> str:
        if "/" in view_key:
            raise ValueError("view_key must be a single key segment")
        return _validate_concrete_key(view_key, "view_key")


class ViewsConfig(ConfigModel):
    fov_deg: Annotated[float, Field(strict=True, gt=0, lt=180)]
    size: ImageSize
    jpeg_quality: JpegQuality
    channels: Annotated[tuple[ViewChannelConfig, ...], Field(min_length=1)]

    @field_validator("channels")
    @classmethod
    def validate_unique_view_keys(
        cls, channels: tuple[ViewChannelConfig, ...]
    ) -> tuple[ViewChannelConfig, ...]:
        view_keys = [channel.view_key for channel in channels]
        if len(set(view_keys)) != len(view_keys):
            raise ValueError(f"view_key values must be unique: {view_keys}")
        return channels


class PublisherConfig(ConfigModel):
    publish_frequency_hz: PositiveFloat
    congestion_control: Literal["drop", "block", "block_first"]
    reliability: Literal["best_effort", "reliable"]


class NodeConfig(ConfigModel):
    zenoh_key_prefix: StrictStr
    source: NonNegativeInt | Annotated[StrictStr, Field(pattern=r"\S")]
    mount: MountConfig
    lenses: DualFisheyeLenses
    equirectangular: EquirectangularConfig | None = None
    views: ViewsConfig | None = None
    publisher: PublisherConfig

    @field_validator("zenoh_key_prefix")
    @classmethod
    def validate_zenoh_key_prefix(cls, prefix: str) -> str:
        return _validate_concrete_key(prefix, "zenoh_key_prefix")

    @model_validator(mode="after")
    def validate_outputs(self) -> Self:
        if self.equirectangular is None and self.views is None:
            raise ValueError("at least one of equirectangular or views is required")
        return self


def load_node_config(path: Path) -> NodeConfig:
    try:
        document = json5.loads(
            path.read_text(encoding="utf-8"), allow_duplicate_keys=False
        )
    except ValueError as error:
        raise ValueError(f"{path}: invalid JSON5: {error}") from error
    try:
        return NodeConfig.model_validate(document)
    except ValidationError as error:
        raise ValueError(f"{path}: invalid node configuration:\n{error}") from error
