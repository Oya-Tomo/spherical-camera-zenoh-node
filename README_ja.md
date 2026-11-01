# spherical-camera-zenoh-node

[English](README.md)

Unitree Go2に載せたRICOH THETA S用のZenohノードです。カメラのDual-Fisheye映像を360°のequirectangular画像と歪みのない透視投影viewに変換してJPEGでpublishし、各viewの向きを任意のZenoh clientから実行時に設定できます。

## クイックスタート

```console
$ uv sync
$ cp config/zenoh-config.example.json5 config/zenoh-config.json5
$ cp config/node-config.example.json5 config/node-config.json5
```

THETA Sをライブストリーミングモードにして USB で接続し、`config/node-config.json5`の`source`をそのdeviceに設定します。リポジトリrootからノードを起動します。

```console
$ uv run node.py
```

viewを表示し、矢印キーで向きを変えます。

```console
$ uv run --group example examples/viewer.py spherical_camera/view/operator
```

## ドキュメント

- [セットアップ](docs/ja/setup.md)
- [使い方とZenoh interface](docs/ja/usage.md)
