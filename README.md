# CLANKER-HYDRUS-TAGGER

Windows setup for tagging files in Hydrus with local ONNX models.

## First-Time Setup

### 1. Install the runtime

Run one of these:

- `install_cpu.bat`
- `install_gpu.bat`

Use `install_cpu.bat` if you want to run on the CPU, or if your GPU is too old for this setup.

Use `install_gpu.bat` if you want to use your NVIDIA GPU for faster tagging. Your NVIDIA driver should be version `522.06` or newer.

### 2. Enable the Hydrus Client API

Open Hydrus, then go to:

`services -> manage services`

In the list of services, click `client api`.

Make sure this is enabled:

`run the client api?`

If the Client API is disabled, the project will not be able to read files, search for hashes, or write tags.

### 3. Create or choose a tag service

By default, it writes tags into:

`A.I. Tags`

If you want to keep using that name, create a local tag service with that exact name in Hydrus if it does not already exist.

Open Hydrus, then go to:

`services -> manage services`

From there:

1. click `add`
2. choose a local tag service
3. name it `A.I. Tags`
4. click `apply` or `ok`

If you already have a tag service you want to use instead, keep it and put that exact name into `TAG_SERVICE` in `.env`.

### 4. Create an API key

Open Hydrus, then go to:

`services -> review services -> local -> client api`

Then:

1. open the permissions / access key management page
2. create a new access key
3. give it a clear name so you remember what it is for
4. enable at least these permissions

- `edit file tags`
- `search and fetch files`

5. save the key and copy it

Then open `.env` and paste the key here:

```env
HYDRUS_TOKEN=PASTE_YOUR_KEY_HERE
```

### 5. Put hashes into `hashes.txt`

In Hydrus, select the image, `right-click -> share -> copy hashes -> sha256`. Put one hash on each line:

```text
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
```

## What To Run Next

Once `.env` is filled in and `hashes.txt` contains valid `sha256` hashes, you can start using the launchers.

### Update to the latest release

If you want every normal launcher to check for a newer GitHub Release before it starts, set this in `.env`:

```env
AUTO_CHECK_UPDATES=1
```

With `AUTO_CHECK_UPDATES=1`, launchers prompt you with:

- update and launch
- update and close
- proceed without update

With `AUTO_CHECK_UPDATES=0`, launchers skip the check completely.

The updater expects the release to contain the asset:

- `clanker-hydrus-tagger-portable.zip`

That asset is built from `.service/release_manifest.txt`, so the release payload stays aligned with what the updater is allowed to replace.

It intentionally preserves your local data and runtime:

- `.env`
- `hashes.txt`
- `venv`
- `.portable`
- `model`

### API check

- `0_check_hydrus_api.bat`

Run this first if you want a quick validation that:

- Hydrus is reachable
- your API key works
- the configured tag service exists
- the required permissions are present

### AI image tagging

Furry & Anthro:

* `1_JTP-3.bat`
  Your best choice. A modern, cutting-edge tagger specifically fine-tuned for furry art and the e621 ecosystem. Start with this one for the most accurate and deep results.
* `2_Z3D-E621-Convnext.bat`
  The classic alternative. An older, well-established model for the same furry and anthro content. Use it if you prefer a time-tested option or want to compare its tags against JTP-3.

Anime, Manga & Game Art:

* `4_camie-tagger.bat`
  The modern standard. A fresh, powerful tagger with a huge vocabulary tailored for anime, manga, and gaming illustrations. Highly recommended as your default choice for general art.
* `3_wd-eva02-large.bat`
  The reliable veteran. A classic Danbooru-style tagger that works great as a safe fallback. It is perfect for mixed illustration libraries or for cross-checking against Camie-Tagger.

---

### Source-based tagging

These launchers also start from the same `hashes.txt`, but they do not tag the image directly.

Instead, they:

1. find the file in Hydrus by its `sha256`
2. read Hydrus metadata such as `known_urls`
3. resolve the file's `md5` through Hydrus
4. try two source lookup paths:
   - source URLs already attached to the file in Hydrus
   - exact `md5` matches on supported booru-style sites
5. merge the discovered source facts and write the result back into your Hydrus tag service

Available source-based launchers:

- `99_search_artist.bat`
  Tries to add a creator tag from source URLs or exact `md5` matches. By default this writes `creator:name`.
- `99_search_all.bat`
  Tries to pull all source metadata it can find from source URLs or exact `md5` matches on supported sites.
- `99_search_year.bat`
  Tries to add a year tag from source URLs, matched posts, or related metadata.

Supported exact-match source sites:

- `danbooru`
- `e621`
- `e926`
- `yandere`
- `konachan`
- `gelbooru`
- `rule34`
- `safebooru`

`rule34` uses the authenticated `api.rule34.xxx` endpoint when `RULE34_USER_ID` and `RULE34_API_KEY` are set in `.env`. Without those credentials, public `rule34.xxx` lookups may return `403` or no data.

`gelbooru` can also use authenticated DAPI access when `GELBOORU_USER_ID` and `GELBOORU_API_KEY` are set in `.env`. Without them, lookups still run, but `gelbooru` may throttle or temporarily reject requests more aggressively.

Source lookup also stops early once the selected mode already has enough data. In practice that means lower-priority sites like `gelbooru` may be skipped if higher-priority sites already satisfied the current `99_search_*` run.

### Ratings only

- `99_ratings.bat`

This adds only rating tags such as safe, questionable, or explicit, without running a full tag pass.

The model used by this launcher is controlled by:

- `RATINGS_MODEL_KEY`

Use either:

- `WD_EVA02_LARGE`
- `CAMIE_TAGGER`

## What You Can Change

Most users only need to edit two files:

- `.env`
- `hashes.txt`

### `.env`

At minimum, put your Hydrus API key here:

```env
HYDRUS_TOKEN=REPLACE_WITH_HYDRUS_API_KEY
```

You can also change:

- `HYDRUS_HOST`
  The Hydrus Client API address.
- `TAG_SERVICE`
  The tag service that receives the tags.
- `HASH_FILE`
  Default input file for the normal AI tagger launchers.
- `SEARCH_ARTIST_FILE`, `SEARCH_ALL_FILE`, `SEARCH_YEAR_FILE`
  Optional input files for the source-based launchers. If left unset or pointed at `hashes.txt`, they behave the same as `HASH_FILE`. These files can contain `sha256`, `md5`, `sha1`, `sha512`, `file_id`, or full `https://` URLs.
- `*_THRESHOLD`
  Controls how strict a model is. Higher values usually mean fewer tags and less noise. A practical range is often somewhere around `0.30` to `0.80`, depending on the model.
- `*_MAX_TAGS`
  Controls how many tags a model is allowed to write. Lower values give shorter output. Higher values give fuller output. A practical range is often around `20` to `80`. `0` means no cap.
- `*_BATCH_SIZE`
  Controls speed and VRAM usage. Lower values are safer on weak GPUs. Higher values are faster if your GPU can handle them.
- `*_NAMESPACE`
  Controls how each model writes tag categories into Hydrus. `auto` is the recommended default and uses category-aware tags where the model metadata supports it, for example `character:name`, `copyright:name`, `creator:name`, `species:name`, `meta:name`, `rating:explicit`, `year:2014`. Examples:
  - `auto`
  - `all=` -> plain tags only
  - `all=ai` -> `ai:tag`
  - `general=,character=character,artist=creator,meta=skip`
- `*_SKIP_EXISTING_NAMESPACES`
  Optionally suppresses selected model categories if the target Hydrus tag service already has any tag in that namespace. This is useful for keeping AI artist or character tags from overriding tags you already trust. Examples:
  - `artist,character`
  - `rating`
  - `all`
- `RATINGS_MODEL_KEY`
  Selects which built-in rating-capable model `99_ratings.bat` uses. Valid values are `WD_EVA02_LARGE` and `CAMIE_TAGGER`.
- `RATINGS_BATCH_SIZE`
  Batch size for `99_ratings.bat`.
- `HYDRUS_BATCH_INFERENCE`
  Controls whether the batch launchers use true batched inference. `1` enables `--batch-inference`, `0` switches to one-image-at-a-time inference inside each batch.
- `TAGGER_VERBOSE`
  Controls console output for the launcher `.bat` files. `0` keeps tagging output quiet, `1` prints more detail.
- `SEARCH_ARTIST_NAMESPACE`
  Controls how creator tags are written. The standard setting is `creator`, so source authors become `creator:name`. Examples:
  - `creator` -> `creator:name`
  - `artist` -> `artist:name` if you prefer Hydrus-style compatibility
  - `none` -> plain `name`
- `SEARCH_ALL_NAMESPACE`
  Controls grouped source tags for `99_search_all.bat`. It can also write extra fields like rating, year, source site, filetype, and creator names. The config key stays `artist=...`, but the recommended namespace value is `creator`. Examples:
  - `all=` -> plain tags, no namespaces
  - `all=source` -> `source:tag`
  - `general=,copyright=copyright,character=character,meta=meta,species=species,lore=lore,rating=rating,year=year,site=source,filetype=filetype,artist=skip`
  - `general=,meta=skip,year=skip,artist=creator`
- `SEARCH_*_SITES`
  Limits which source sites are checked. Use `all`, or a comma-separated list such as `danbooru,e621,gelbooru`.
- `RULE34_USER_ID` and `RULE34_API_KEY`
  Optional, but recommended if you want reliable `rule34` source lookup. These are used for authenticated requests to `api.rule34.xxx`.
- `GELBOORU_USER_ID` and `GELBOORU_API_KEY`
  Optional, but recommended if you want more reliable `gelbooru` DAPI access.
- `SOURCE_LOOKUP_MAX_WORKERS`
  Controls parallel source/url checks inside one file lookup.
- `LOOKUP_RECORD_MAX_WORKERS`
  Controls how many files `99_search_all.bat` processes in parallel.
- `SOURCE_RETRY_MAX_ATTEMPTS`
  Controls how many times source-site requests are retried after `429`, temporary `5xx`, or transient network errors.
- `SOURCE_CIRCUIT_BREAKER_THRESHOLD`
  After this many fully failed requests in a row to the same source, that source is temporarily suspended.
- `SOURCE_CIRCUIT_BREAKER_COOLDOWN_SECONDS`
  How long a repeatedly failing source stays suspended before requests are attempted again.
- `SOURCE_RETRY_BASE_DELAY_MS`
  Base delay for automatic source-site backoff.
- `SOURCE_RETRY_MAX_DELAY_MS`
  Maximum delay for automatic source-site backoff.

### `hashes.txt`

This is your input list.

Put Hydrus `sha256` hashes here, one per line.

## Daily Use

### Run a normal image tagger

1. Put your Hydrus `sha256` hashes into `hashes.txt`
2. Run one of these:
   - `1_JTP-3.bat`
   - `2_Z3D-E621-Convnext.bat`
   - `3_wd-eva02-large.bat`
   - `4_camie-tagger.bat`

### Pull creator tags, source metadata, or year from source pages

1. Put your lookup values into the file used by the launcher
2. Run one of these:
   - `99_search_artist.bat`
   - `99_search_all.bat`
   - `99_search_year.bat`

By default those launchers use:

- `SEARCH_ARTIST_FILE`
- `SEARCH_ALL_FILE`
- `SEARCH_YEAR_FILE`

These default to `hashes.txt`, but they can also contain `md5`, `sha1`, `sha512`, `file_id`, or full source URLs when that fits your workflow better.

These launchers first try attached `known_urls`, then fall back to exact `md5` checks on the supported source sites only when the URL pass did not already produce usable data for the chosen mode. If a file has no source links and no site has the same file hash, there may be nothing to import.

The source lookup pipeline is tuned for high throughput without hammering sites blindly:

- many files can be processed in parallel
- identical URL and `md5` lookups are deduplicated across worker threads
- requests are throttled separately per source site
- `429` and temporary server errors trigger automatic retry with backoff
- when the file queue is large, the tool prefers file-level throughput over exploding nested request fan-out

### Add only ratings

1. Put your Hydrus `sha256` hashes into `hashes.txt`
2. Run:
   - `99_ratings.bat`

## Troubleshooting

### "Could not connect to the Hydrus Client API"

Check these first:

- Hydrus is open
- the Client API is enabled
- `HYDRUS_HOST` matches your real Hydrus API address
- your API key in `.env` is correct

### "Tag service not found"

Either:

- create a local tag service with the same name as `TAG_SERVICE`
- or change `TAG_SERVICE` in `.env`

If you want a quick confirmation, run:

- `0_check_hydrus_api.bat`

### Source-based tagging finds nothing

Check these:

- the files really exist in Hydrus
- the `sha256` hashes in `hashes.txt` are correct
- Hydrus can resolve the file's `md5`
- the files have useful `known_urls`, or the same file exists on one of the checked sites with the same `md5`
- the source pages still exist and still expose useful metadata
- `SEARCH_*_SITES` is not excluding the site where the file actually exists

## Credits

- [Garbevoir/wd-e621-hydrus-tagger](https://github.com/Garbevoir/wd-e621-hydrus-tagger)
- Abtalerico for the original `wd-hydrus-tagger`
- SmilingWolf for the WD tagging models and related tagging work
- Zack3d / furzacky for the e621-focused model work
- Hydrus Dev for Hydrus itself

## License

GPL-2.0-or-later. See `LICENSE`.
