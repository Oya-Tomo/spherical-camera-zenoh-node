# spherical-camera-zenoh-node

[日本語](README_ja.md)

A Zenoh node for a RICOH THETA S. It turns the camera's dual-fisheye stream into a 360° equirectangular image and undistorted perspective views and publishes them as JPEG. Each view is either steerable, with its direction set at runtime by any Zenoh client, or fixed in its configured direction.

## Quick start

```console
$ uv sync
$ cp config/zenoh-config.example.json5 config/zenoh-config.json5
$ cp config/node-config.example.json5 config/node-config.json5
$ cp examples/viewer-config.example.json5 examples/viewer-config.json5
$ cp examples/viewer-zenoh-config.example.json5 examples/viewer-zenoh-config.json5
```

Put the THETA S into live streaming mode, connect it over USB, and set `source` in `config/node-config.json5` to its device. Start the node from the repository root:

```console
$ uv run node.py
```

Display a view and turn or tilt it with the arrow keys:

```console
$ uv run --group example examples/viewer.py
```

## Documentation

- [Setup](docs/en/setup.md)
- [Usage and Zenoh interface](docs/en/usage.md)
