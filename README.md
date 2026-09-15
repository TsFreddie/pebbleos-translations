# PebbleOS translations

Translation catalogs, language-pack resource maps, fonts, and character sets
for [PebbleOS](https://github.com/coredevices/pebbleos).

## Packing

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and GNU
gettext (`msgfmt`, `msginit`, and `msgmerge` on `PATH`).
On macOS, `brew install gettext` supplies gettext;
on Debian/Ubuntu, install the `gettext` package. Then, from this checkout:

```sh
uv sync --locked
uv run --locked python tools/lang.py pack_lang --lang fr_FR --output dist
uv run --locked python tools/lang.py pack_all_langs --output dist
```

To initialize or update a language, supply the current source catalog:

```sh
uv run --locked python tools/lang.py make_lang --lang fr_FR --pot /path/to/pebbleos.pot
```

## License

This project is licensed under the [Apache License 2.0](LICENSE), except
where individual files or accompanying notices specify another license.
Third-party fonts retain their original licenses
