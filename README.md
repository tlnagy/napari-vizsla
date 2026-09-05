# napari-vizsla

[![License MIT](https://img.shields.io/pypi/l/napari-vizsla.svg?color=green)](https://github.com/tlnagy/napari-vizsla/raw/main/LICENSE)
[![PyPI](https://img.shields.io/pypi/v/napari-vizsla.svg?color=green)](https://pypi.org/project/napari-vizsla)
[![Python Version](https://img.shields.io/pypi/pyversions/napari-vizsla.svg?color=green)](https://python.org)
[![tests](https://github.com/tlnagy/napari-vizsla/workflows/tests/badge.svg)](https://github.com/tlnagy/napari-vizsla/actions)
[![codecov](https://codecov.io/gh/tlnagy/napari-vizsla/branch/main/graph/badge.svg)](https://codecov.io/gh/tlnagy/napari-vizsla)
[![napari hub](https://img.shields.io/endpoint?url=https://api.napari-hub.org/shields/napari-vizsla)](https://napari-hub.org/plugins/napari-vizsla)
[![npe2](https://img.shields.io/badge/plugin-npe2-blue?link=https://napari.org/stable/plugins/index.html)](https://napari.org/stable/plugins/index.html)
[![Copier](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/copier-org/copier/master/img/badge/badge-grayscale-inverted-border-purple.json)](https://github.com/copier-org/copier)

<img src="https://raw.githubusercontent.com/tlnagy/napari-vizsla/a7305fcf9bed6e43c54916019a096752f4da870d/assets/vizsla.png" width="500"/>

Your friendly tracking assistant. Sniff out and fix broken tracks.

----------------------------------

There are many fantastic tracking libraries out there (e.g. Trackastra, Ultrack, etc) but in my experience they only get ~90% of tracks right from one frame to the next...over a long movie that's a lot of broken tracks! 

`napari-vizsla`'s goal is to help you quickly fix these broken tracks. Load your tracks using the Cell-Tracking-Challenge (CTC) format (which many tracking programs can output) and get fixing!

https://github.com/user-attachments/assets/07aa9885-f69d-4f8c-a1e6-9e69f6b1b936

## Usage

`napari-vizsla` mostly consists of a set of shortcuts to quickly modify and update tracks. It will autosave your changes as you go!

| Keyboard Shortcut | Action                                                                                      |
|-------------------|---------------------------------------------------------------------------------------------|
| `l`               | (l)ink tracks. Select two cells, in two different timepoints, and link them into one track. |
| `b`               | (b)reak track. This breaks the track into two tracklets at the given time point.            |
| `h`               | (h)ide overlay. Hides Vizsla overlay.                                                       |
| `t`               | hide (t)racks. Hides the track layer.                                                       |
| `s`               | hide (s)egmentation layer. Helpful to better see the underlying signal layer.               |

## Installation

You can install `napari-vizsla` via [pip]:

```bash
pip install napari-vizsla
```

If napari is not already installed, you can install `napari-vizsla` with napari and Qt via:

```bash
pip install "napari-vizsla[all]"
```



## Contributing

Contributions are very welcome. Tests can be run with [tox], please ensure
the coverage at least stays the same before you submit a pull request.

## License

Distributed under the terms of the [MIT] license,
"napari-vizsla" is free and open source software

## Issues

If you encounter any problems, please [file an issue] along with a detailed description.

[napari]: https://github.com/napari/napari
[copier]: https://copier.readthedocs.io/en/stable/
[MIT]: http://opensource.org/licenses/MIT
[BSD-3]: http://opensource.org/licenses/BSD-3-Clause
[GNU GPL v3.0]: http://www.gnu.org/licenses/gpl-3.0.txt
[GNU LGPL v3.0]: http://www.gnu.org/licenses/lgpl-3.0.txt
[Apache Software License 2.0]: http://www.apache.org/licenses/LICENSE-2.0
[Mozilla Public License 2.0]: https://www.mozilla.org/media/MPL/2.0/index.txt
[napari-plugin-template]: https://github.com/napari/napari-plugin-template

[tox]: https://tox.readthedocs.io/en/latest/
[pip]: https://pypi.org/project/pip/
[PyPI]: https://pypi.org/
