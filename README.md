# Smart Fetch

Give it links -> every link runs on its **own GitHub runner at the same time** ->
it downloads, compresses based on what the file really is, and commits the result
to the `data` branch. You download from there. No Telegram.

## Setup (2 minutes)
1. Create a **private** repo (public = everyone can see what you download) and push these files.
2. Settings -> Actions -> General -> Workflow permissions -> **Read and write**.
3. Put links in `links.txt` (commit it), **or** paste them into the Run form.
4. Actions tab -> **Fetch & Compress** -> Run workflow.
5. When it finishes, switch to the **`data`** branch -> `data/<date>-run<N>/<NN-link>/`.
   Bulk download: `git clone --branch data --depth 1 <repo-url>`

**Default storage is a GitHub Release** (`Releases` -> `run-<N>`): each file may be up to ~1.9 GB, nothing
is added to the repo, and old batches are deleted unless `append` is on. Choose `data-branch` in the Run form
to commit into git instead (files <= 90 MB).

Not in the repo? In `data-branch` mode each link's output is also an artifact on the run page (kept 3 days).

## What it does per file
| Type | Treatment |
|---|---|
| Video | Scaled **down** to `max_height` (never up), H.265/H.264, AAC 96k, faststart. Already small + efficient videos are only remuxed. |
| Too big for GitHub | Re-encoded to an exact bitrate budget; if that's hopeless it is cut into playable `_partNN.mp4` files. |
| Photo | WebP q82, max 3000px, EXIF rotation applied. Original kept if WebP isn't smaller. |
| Animated GIF/WebP | Converted to MP4 (~10x smaller). |
| Many images (>5) | Packed into `images.zip` (auto-split `images_01.zip`...), one click instead of 200. |
| Audio | AAC 96k `.m4a` if smaller. |
| Anything else | Copied untouched; if >90 MB split into `.part001...` (rejoin: `cat name.part* > name`). |

Everything is downloaded with **gallery-dl** only (direct file links use its `direct://` mode).

## Limits worth knowing
- Release mode: files up to 2 GB each (kept <=1.9 GB). Data-branch mode: GitHub rejects files >100 MB, so everything is kept <=90 MB (`MAX_MB`).
- Free plan = 20 jobs in parallel, 256 links per run max. Private repos get 2000 free minutes/month
  (each link uses its own minutes, so 20 long videos can burn a lot).
- `append` off (default) **replaces** the previous batch on `data` so the repo never grows.
  Turn it on to keep history, but the repo size will grow (GitHub soft limit ~1-5 GB).
- Sites that block datacenter IPs (YouTube often does, Instagram/TikTok/X sometimes) need logins:
  export cookies with a "cookies.txt" browser extension and save the content as a repo secret named
  `COOKIES`. Use a throwaway account.
- Only download content you have the right to download.

## Tuning
Run-form options: max height, codec, append. Everything else is an env var in `scripts/fetch.py`
(`IMAGE_QUALITY`, `IMAGE_MAX_SIDE`, `BUNDLE_IMAGES_OVER`, `MAX_MB`).
