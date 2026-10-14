#!/usr/bin/env python3
"""
expand.py -- the "planner".

Turns the raw link list into many small, balanced jobs so that a 45 GB album
never lands on one runner:

  1. every link is listed with gallery-dl (nothing is downloaded) to learn how
     many files it contains and, when the site says so, how big they are
  2. the files are cut into chunks (CHUNK_GB each, at most CHUNK_MAX_ITEMS files)
  3. every chunk becomes one parallel job that downloads only its slice
     (gallery-dl --range a-b) and cleans up after itself

Env: INPUT_LINKS, CHUNK_GB (8), CHUNK_MAX_ITEMS (40), UNKNOWN_ITEM_MB (150),
     COOKIES, CREATE_RELEASES (1/0), GITHUB_RUN_NUMBER, GITHUB_REPOSITORY, GITHUB_SHA
"""
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

GB = 1024 ** 3
MB = 1024 ** 2
CHUNK_BYTES = float(os.getenv("CHUNK_GB", "8")) * GB
MAX_ITEMS = int(os.getenv("CHUNK_MAX_ITEMS", "40"))
UNKNOWN = int(float(os.getenv("UNKNOWN_ITEM_MB", "150")) * MB)
MAX_JOBS = 256          # GitHub matrix limit
MAX_ASSETS = 800        # a release holds at most 1000 files
RUN = os.getenv("GITHUB_RUN_NUMBER", "0")
DIRECT_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".avif", ".heic",
              ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".ts", ".wmv", ".3gp",
              ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"}


def read_links():
    raw = os.environ.get("INPUT_LINKS", "").strip()
    if not raw:
        lines = []
        if Path("links.txt").exists():
            for line in open("links.txt", encoding="utf-8"):
                line = line.split(" #")[0].strip()
                if line and not line.startswith("#"):
                    lines.append(line)
        raw = "\n".join(lines)
    links = []
    for tok in re.split(r"\s+", raw):
        for u in re.split(r",(?=https?://)", tok):
            u = u.strip().strip(",")
            if u and u not in links:
                links.append(u)
    return links


def target_of(url):
    ext = Path(urlparse(url).path).suffix.lower()
    return "direct://" + url if ext in DIRECT_EXT else url


def list_items(url):
    """Returns a list of item sizes in bytes (0 = unknown), or None if listing failed."""
    cmd = ["gallery-dl", "-J", "--retries", "4"]
    if Path("cookies.txt").exists():
        cmd += ["--cookies", "cookies.txt"]
    try:
        p = subprocess.run(cmd + [target_of(url)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=900)
        data = json.loads(p.stdout)
    except Exception as e:
        print(f"::warning::could not list {url} ({e.__class__.__name__}); it will run as one job")
        return None
    sizes = []
    for m in data:
        if isinstance(m, list) and m and m[0] == 3:  # 3 = file URL message
            meta = m[2] if len(m) > 2 and isinstance(m[2], dict) else {}
            size = 0
            for key in ("size", "filesize", "file_size", "bytes"):
                v = meta.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    size = int(v)
                    break
            sizes.append(size)
    return sizes or None


def chunk(sizes, cap, max_items):
    out, start, acc, n = [], 1, 0, 0
    for i, s in enumerate(sizes, 1):
        s = s or UNKNOWN
        if n and (acc + s > cap or n >= max_items):
            out.append((start, i - 1))
            start, acc, n = i, 0, 0
        acc += s
        n += 1
    if n:
        out.append((start, len(sizes)))
    return out


def plan(links, listed):
    cap, max_items = CHUNK_BYTES, MAX_ITEMS
    while True:
        per_link = {}
        for idx, url in enumerate(links, 1):
            sizes = listed.get(url)
            per_link[idx] = chunk(sizes, cap, max_items) if sizes else None
        total = sum(len(c) if c else 1 for c in per_link.values())
        if total <= MAX_JOBS or cap > 40 * GB:
            return per_link, cap
        cap, max_items = cap * 1.5, max_items * 2  # too many jobs -> make chunks bigger


def main():
    if os.getenv("COOKIES"):
        Path("cookies.txt").write_text(os.environ["COOKIES"])
    links = read_links()
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(list_items, links))
    listed = dict(zip(links, results))
    per_link, cap = plan(links, listed)
    if cap != CHUNK_BYTES:
        print(f"::notice::many files -> chunk size raised to {cap / GB:.0f} GB to stay under {MAX_JOBS} jobs")

    include, rows, tags = [], [], []
    for idx, url in enumerate(links, 1):
        sizes, chunks = listed[url], per_link[idx]
        if not chunks:  # listing failed / empty -> one plain job, fetch.py reports what happens
            include.append({"job": f"{idx}", "idx": idx, "part": "", "range": "", "url": url,
                            "tag": f"run-{RUN}-{idx}"})
            rows.append((idx, url, "?", "?", 1))
            tags.append(f"run-{RUN}-{idx}")
            continue
        per_chunk_tag = len(sizes) > MAX_ASSETS
        for k, (a, b) in enumerate(chunks, 1):
            part = f"p{k:02d}" if len(chunks) > 1 else ""
            rng = f"{a}-{b}" if len(chunks) > 1 else ""
            tag = f"run-{RUN}-{idx}" + (f"-{part}" if per_chunk_tag and part else "")
            include.append({"job": f"{idx}-{part}" if part else f"{idx}", "idx": idx,
                            "part": part, "range": rng, "url": url, "tag": tag})
            if tag not in tags:
                tags.append(tag)
        known = sum(sizes)
        unknown = sum(1 for s in sizes if not s)
        est = f"{known / GB:.1f} GB" + (f" + {unknown} unknown" if unknown else "")
        rows.append((idx, url, len(sizes), est, len(chunks)))

    if len(include) > MAX_JOBS:
        print(f"::warning::{len(include)} jobs, GitHub allows {MAX_JOBS}; using the first {MAX_JOBS}")
        include = include[:MAX_JOBS]

    if os.getenv("CREATE_RELEASES") == "1":
        repo, sha = os.environ["GITHUB_REPOSITORY"], os.getenv("GITHUB_SHA", "")
        for tag in tags:
            cmd = ["gh", "release", "create", tag, "--repo", repo, "--title", f"Batch {tag}",
                   "--notes", f"Files from workflow run {RUN}"]
            if sha:
                cmd += ["--target", sha]
            subprocess.run(cmd, capture_output=True, text=True)

    summary = ["## Plan", "| # | Link | Files | Size | Jobs |", "|---|---|---|---|---|"]
    summary += [f"| {i} | {u} | {n} | {s} | {j} |" for i, u, n, s, j in rows]
    text = "\n".join(summary)
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text + "\n")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"matrix={json.dumps({'include': include})}\n")
            f.write(f"count={len(include)}\n")
    print(f"{len(include)} job(s) queued")


if __name__ == "__main__":
    main()
