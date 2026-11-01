# spherical-camera-zenoh-node

[日本語](README_ja.md)

A Zenoh node for a RICOH THETA S mounted on a Unitree Go2. It turns the camera's dual-fisheye stream into a 360° equirectangular image and undistorted perspective views, publishes them as JPEG, and lets any Zenoh client set the direction of each view at runtime.

## Quick start

```console
$ uv sync
$ cp config/zenoh-config.example.json5 config/zenoh-config.json5
$ cp config/node-config.example.json5 config/node-config.json5
```

Put the THETA S into live streaming mode, connect it over USB, and set `source` in `config/node-config.json5` to its device. Start the node from the repository root:

```console
$ uv run node.py
```

Display a view and turn it with the arrow keys:

```console
$ uv run --group example examples/viewer.py spherical_camera/view/operator
```

## Documentation

- [Setup](docs/en/setup.md)
- [Usage and Zenoh interface](docs/en/usage.md)
