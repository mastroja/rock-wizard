"""RockWizard's local server: the desktop window and the phone web app both talk to it.

Runs on this PC (its GPU does the work). Requests from this PC are always allowed; from other
devices (a phone over the home network or Tailscale) only with the pairing key, which the phone
gets once from the QR code / link in Settings and then keeps as a cookie.

    python -m chc.server            (http://127.0.0.1:8765; the desktop app starts it itself, see rockwizard_server.pyw)
"""
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import feedback, library, pipeline, rechart, review, search, startup, tidy

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CONFIG = library.config_path()  # shared with the earlier desktop app
PORT = 8765
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus", ".aac", ".wma"}
STEM_NAMES = {"bass", "drums", "other", "vocals", "guitar", "piano", "keys", "song", "crowd", "preview",
              "rhythm", "drums_1", "drums_2", "drums_3", "drums_4", "no_vocals", "instrumental"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------- settings ----------
def load_config() -> dict:
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    changed = False
    if not cfg.get("songs_dir"):
        cfg["songs_dir"] = str(Path.home() / "Documents" / "Clone Hero" / "Songs")
        changed = True
    if not cfg.get("pair_key"):
        cfg["pair_key"] = secrets.token_urlsafe(16)
        changed = True
    songs = Path(cfg["songs_dir"])
    # guesses for a usual Clone Hero install: <Clone Hero>\songs\<staging>, \songs\Quickplay
    if not cfg.get("live_dir") and (songs.parent / "Quickplay").is_dir() and songs.name != "Quickplay":
        cfg["live_dir"] = str(songs.parent / "Quickplay")
        changed = True
    if not cfg.get("clonehero_exe"):
        for p in [songs, *songs.parents][:4]:
            if (p / "Clone Hero.exe").exists():
                cfg["clonehero_exe"] = str(p / "Clone Hero.exe")
                changed = True
                break
    if changed:
        save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


cfg = load_config()


def staging_dir() -> Path:
    return Path(cfg.get("songs_dir"))


def live_dir() -> Path | None:
    return Path(cfg["live_dir"]) if cfg.get("live_dir") else None


# ---------- jobs ----------
@dataclass
class Job:
    id: str
    kind: str                 # "convert" or "update"
    title: str
    steps: list
    status: str = "queued"    # queued, running, done, failed, cancelled
    step: int = -1
    progress: float = 0.0
    message: str = ""
    result: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    args: dict = field(default_factory=dict)
    by: str = ""              # name of the friend who added it ("" for the owner)


jobs: dict[str, Job] = {}
job_queue: "queue.Queue[str]" = queue.Queue()
stops: dict[str, threading.Event] = {}
CONVERT_STEPS = ["Downloading the song"] + pipeline.STEPS


def add_job(kind, label, steps, by="", **args) -> Job:
    """label is what the queue shows; args (artist, title, path, url...) go to the job runner.
    (Not called 'title': the song's title arrives in args under that name.)"""
    j = Job(uuid.uuid4().hex[:10], kind, label, steps, args=args, by=by)
    jobs[j.id] = j
    stops[j.id] = threading.Event()
    job_queue.put(j.id)
    return j


def public(j: Job) -> dict:
    d = asdict(j)
    d.pop("args")
    return d


def run_convert(j: Job, stop: threading.Event):
    a = j.args
    tmp = Path(tempfile.mkdtemp(prefix="rw_job_"))
    try:
        src, offset = a.get("path"), 0
        if a.get("url"):
            j.step = 0
            src = search.youtube_download(a["url"], tmp, lambda f: setattr(j, "progress", 0.12 * f), stop)
            offset = 1
        artist, title = a.get("artist", ""), a.get("title", "") or Path(src).stem
        details = search.lookup(artist, title)
        if details and search.similar(details.title, title) >= 0.8:
            artist, title = details.artist, details.title  # proper spelling, fixes swapped names
        j.title = f"{artist + ' - ' if artist else ''}{title}"
        cover = search.cover_art(details, tmp / "cover.jpg") if details else None
        start = 0.12 if a.get("url") else 0.0

        def on_step(i):
            j.step = i + offset

        def on_progress(f):
            j.progress = start + (1 - start) * f
        res = pipeline.make_song(Path(src), artist, title, staging_dir(), charter=cfg.get("charter", ""),
                                 on_step=on_step, on_progress=on_progress, stop=stop,
                                 details=details, cover=cover)
        shutil.rmtree(res.preview_dir, ignore_errors=True)
        try:  # same names and capitals as the rest of the library
            res.song_dir = tidy.tidy_one(res.song_dir, artist_map=artist_map())
        except (FileExistsError, OSError):
            pass
        if j.by:  # shows on Clone Hero's loading screen, and in RockWizard
            tidy.write_ini(res.song_dir, {"chc_added_by": j.by, "loading_phrase": f"Suggested by {j.by}"})
        rep = review.check(res.song_dir, live_dir())
        j.result = {"folder": str(res.song_dir), "counts": res.counts, "verdict": rep.verdict,
                    "replaced": [str(p) for p in res.replaced]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_update(j: Job, stop: threading.Event):
    folder = Path(j.args["folder"])
    saved = pipeline.update_song(folder, on_step=lambda i: setattr(j, "step", i),
                                 on_progress=lambda f: setattr(j, "progress", f), stop=stop)
    rep = review.check(folder, live_dir())
    j.result = {"folder": str(folder), "backup": str(saved), "verdict": rep.verdict}


def run_rechart(j: Job, stop: threading.Event):
    folder = Path(j.args["folder"])
    saved = rechart.run(folder, j.args["plan"], on_step=lambda i: setattr(j, "step", i),
                        on_progress=lambda f: setattr(j, "progress", f), stop=stop)
    j.result = {"folder": str(folder), "backup": str(saved)}


def worker():
    while True:
        jid = job_queue.get()
        j = jobs.get(jid)
        if j is None or j.status == "cancelled":
            continue
        j.status = "running"
        try:
            {"convert": run_convert, "update": run_update, "rechart": run_rechart}[j.kind](j, stops[jid])
            j.status, j.progress, j.step = "done", 1.0, len(j.steps)
        except pipeline.Cancelled:
            j.status = "cancelled"
        except Exception as e:
            traceback.print_exc()
            j.status = "cancelled" if stops[jid].is_set() else "failed"
            j.message = str(e).splitlines()[0] if str(e) else e.__class__.__name__


threading.Thread(target=worker, daemon=True).start()


# ---------- devices ----------
# Every phone/browser gets its own id cookie (rw_dev). The owner's devices (and this PC) act as the
# owner; anyone else is asked their name once, and songs they add are tagged with it.
DEVICE_COOKIE = "rw_dev"
_devices_lock = threading.Lock()


def devices() -> dict:
    return cfg.setdefault("devices", {})


def device_of(request: Request) -> dict | None:
    """The device record for this request; None on the PC itself (always the owner)."""
    return getattr(request.state, "device", None)


def is_owner(request: Request) -> bool:
    d = device_of(request)
    return d is None or bool(d.get("owner"))


def added_by(request: Request) -> str:
    """The friend's name to tag new songs with, or "" for the owner."""
    d = device_of(request)
    return "" if d is None or d.get("owner") else (d.get("name") or "a friend")


def rater(request: Request) -> str:
    """Who is rating a chart: the device's name (the PC is the owner)."""
    d = device_of(request)
    if d is None:
        return (cfg.get("owner_names") or ["owner"])[0]
    return d.get("name") or "a friend"


def require_owner(request: Request):
    if not is_owner(request):
        raise HTTPException(403, "Only RockWizard's owner can do that")


def _track_device(request: Request) -> tuple[dict, str | None]:
    """Find or make this browser's device record; returns (record, new cookie id or None)."""
    did = request.cookies.get(DEVICE_COOKIE, "")
    now = time.time()
    with _devices_lock:
        devs = devices()
        if did in devs:
            d = devs[did]
            if now - d.get("last", 0) > 60:
                d["last"] = now
                save_config(cfg)
            return d, None
        if request.url.path != "/":
            # only the page itself registers a device: a phone's first page load fires several
            # requests at once, which would otherwise each make their own record
            return {"name": "", "owner": False, "kind": ""}, None
        did = secrets.token_urlsafe(12)
        agent = request.headers.get("user-agent", "")
        kind = "iPhone" if "iPhone" in agent else "iPad" if "iPad" in agent else "Android" if "Android" in agent \
            else "Mac" if "Macintosh" in agent else "Windows" if "Windows" in agent else "Browser"
        # the phone that was already paired before devices were tracked is the owner's
        paired_before = request.cookies.get("rw_key") == cfg["pair_key"] and not request.query_params.get("key")
        first_owner = paired_before and not any(x.get("owner") for x in devs.values())
        devs[did] = d = {"name": cfg.get("charter", "") if first_owner else "", "owner": first_owner,
                         "kind": kind, "first": now, "last": now}
        save_config(cfg)
        return d, did


# ---------- app + access ----------
app = FastAPI(title="RockWizard")


@app.middleware("http")
async def access(request: Request, call_next):
    host = request.client.host if request.client else ""
    local = host in ("127.0.0.1", "::1", "localhost")
    key = request.query_params.get("key") or request.cookies.get("rw_key")
    if not local and key != cfg["pair_key"]:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "This device isn't paired. Scan the QR code in RockWizard's Settings."},
                                status_code=401)
        return HTMLResponse("<h2 style='font-family:system-ui;padding:24px'>RockWizard: this device isn't paired."
                            "<br>Open Settings &rarr; Phone in RockWizard on your PC and scan the QR code.</h2>",
                            status_code=401)
    new_id = None
    if not local:
        request.state.device, new_id = _track_device(request)
    response = await call_next(request)
    if not local and request.query_params.get("key") == cfg["pair_key"]:
        response.set_cookie("rw_key", cfg["pair_key"], max_age=10 * 365 * 86400, samesite="lax")
    if new_id:
        response.set_cookie(DEVICE_COOKIE, new_id, max_age=10 * 365 * 86400, samesite="lax")
    return response


@app.get("/api/me")
def me(request: Request):
    d = device_of(request)
    return {"local": d is None, "owner": is_owner(request), "name": cfg.get("charter", "") if d is None else d.get("name", "")}


@app.post("/api/me")
async def set_me(request: Request):
    d = device_of(request)
    if d is None:
        raise HTTPException(400, "This PC is the owner's")
    name = " ".join(str((await request.json()).get("name", "")).split())[:40]
    if not name:
        raise HTTPException(400, "Please type your name")
    d["name"] = name
    # the owner's own name gives full rights on any device (it's among friends)
    if name.lower() in {n.lower() for n in cfg.setdefault("owner_names", ["Jacob"])}:
        d["owner"] = True
    save_config(cfg)
    return me(request)


@app.get("/api/devices")
def device_list(request: Request):
    require_owner(request)
    mine = request.cookies.get(DEVICE_COOKIE, "")
    return [{"id": k, "this": k == mine and device_of(request) is not None, **v}
            for k, v in sorted(devices().items(), key=lambda kv: -kv[1].get("last", 0))]


@app.post("/api/devices/{did}")
async def device_set(did: str, request: Request):
    require_owner(request)
    b = await request.json()
    d = devices().get(did) or {}
    if not d:
        raise HTTPException(404)
    if "owner" in b:
        d["owner"] = bool(b["owner"])
    if "name" in b:
        d["name"] = " ".join(str(b["name"]).split())[:40]
    save_config(cfg)
    return d


@app.delete("/api/devices/{did}")
def device_forget(did: str, request: Request):
    """Forget a device: next time it's asked for a name again (it stays paired)."""
    require_owner(request)
    devices().pop(did, None)
    save_config(cfg)
    return {"ok": True}


def body_folder(path: str, base: Path | None = None) -> Path:
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"{p} not found")
    return p


# ---------- jobs API ----------
@app.get("/api/jobs")
def list_jobs():
    return [public(j) for j in sorted(jobs.values(), key=lambda j: -j.created)][:100]


@app.post("/api/jobs/upload")
async def job_upload(request: Request, file: UploadFile = File(...), artist: str = Form(""), title: str = Form("")):
    up = Path(tempfile.mkdtemp(prefix="rw_up_")) / Path(file.filename).name
    with open(up, "wb") as f:
        shutil.copyfileobj(file.file, f)
    a, t = search.names_from_file(up.stem)
    j = add_job("convert", f"{artist or a} - {title or t}".strip(" -"), CONVERT_STEPS[1:], by=added_by(request),
                path=str(up), artist=artist or a, title=title or t)
    return public(j)


@app.post("/api/jobs/convert")
async def job_convert(req: Request):
    """{path} for a file on this PC, or {url} for YouTube; optional artist/title."""
    b = await req.json()
    if b.get("url"):
        steps = CONVERT_STEPS
        if not b.get("title"):  # a pasted link: names from the video's title and channel
            try:
                b["artist"], b["title"] = search.names_from_video(*search.video_title(b["url"]))
            except Exception:
                pass
    else:
        require_owner(req)  # converting a file already on the PC
        if not Path(b.get("path", "")).exists():
            raise HTTPException(404, "file not found")
        steps = CONVERT_STEPS[1:]
        if not b.get("title"):
            b["artist"], b["title"] = search.names_from_file(Path(b["path"]).stem)
    j = add_job("convert", f"{b.get('artist', '')} - {b.get('title', '')}".strip(" -") or "YouTube song",
                steps, by=added_by(req), path=b.get("path"), url=b.get("url"), artist=b.get("artist", ""),
                title=b.get("title", ""))
    return public(j)


@app.post("/api/jobs/update")
async def job_update(req: Request):
    require_owner(req)
    b = await req.json()
    out = []
    for f in b.get("folders", []):
        if library.is_ours(Path(f)):
            out.append(public(add_job("update", Path(f).name, pipeline.UPDATE_STEPS, folder=f)))
    return out


@app.post("/api/jobs/rechart")
async def job_rechart(req: Request):
    """Keep or redo a song part by part. Asked for per song, with the person's choices."""
    require_owner(req)
    b = await req.json()
    f = body_folder(b["folder"]).resolve()
    if not any(r.resolve() in f.parents for r in (staging_dir(), live_dir()) if r):
        raise HTTPException(400, "Only songs in staging or Quickplay can be recharted")
    opts = rechart.options(f)
    if opts is None or "blocked" in opts:
        raise HTTPException(400, (opts or {}).get("blocked") or "This song can't be recharted here")
    if any(j.status in ("queued", "running") and j.args.get("folder") == str(f) for j in jobs.values()):
        raise HTTPException(409, "This song already has a job running")
    try:
        plan = rechart.check_plan(b.get("plan") or {}, opts)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return public(add_job("rechart", f.name, rechart.STEPS, folder=str(f), plan=plan))


@app.post("/api/jobs/{jid}/cancel")
def job_cancel(jid: str, request: Request):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404)
    if not is_owner(request) and j.by != added_by(request):
        raise HTTPException(403, "You can only cancel songs you added")
    stops[jid].set()
    if j.status == "queued":
        j.status = "cancelled"
    return public(j)


@app.post("/api/jobs/clear")
def jobs_clear(request: Request):
    require_owner(request)
    for jid in [k for k, j in jobs.items() if j.status in ("done", "failed", "cancelled")]:
        jobs.pop(jid, None)
    return {"ok": True}


# ---------- search ----------
@app.get("/api/search")
def do_search(q: str):
    artist, title = search.parse_query(q)
    out = {"charts": [], "details": None, "videos": [], "errors": []}
    try:
        out["charts"] = [asdict(c) | {"download_url": c.download_url} for c in search.community_charts(artist, title)]
    except Exception as e:
        out["errors"].append(f"chart search ({e.__class__.__name__})")
    d = search.lookup(artist, title)
    if d:
        out["details"] = asdict(d)
        artist = artist or d.artist
    try:
        vids = search.youtube_search(artist, title, expect_seconds=d.seconds if d else 0)
        for v in vids:
            va, vt = search.names_from_video(v.title, v.channel)
            if d and search.similar(d.title, vt) >= 0.8:
                va, vt = d.artist, d.title
            out["videos"].append(asdict(v) | {"artist": va or artist, "song": vt or title})
    except Exception as e:
        out["errors"].append(f"YouTube search ({e.__class__.__name__}; try Settings, Update downloader)")
    return out


@app.post("/api/charts/download")
async def chart_download(req: Request):
    b = await req.json()
    c = search.Chart(b["name"], b["artist"], b.get("charter", "unknown"), b["md5"])
    by = added_by(req)
    dest = live_dir() if b.get("to") == "live" and live_dir() and not by else staging_dir()
    saved = search.download_chart(c, dest)
    if by and saved and Path(saved).is_dir():
        tidy.write_ini(Path(saved), {"chc_added_by": by})
    return {"saved": str(saved)}


# ---------- staging ----------
_reviews: dict[str, dict] = {}


def review_dict(folder: Path) -> dict:
    rep = review.check(folder, live_dir())
    d = {"verdict": rep.verdict, "findings": [asdict(f) for f in rep.findings],
         "checked": time.time(), "mtime": (folder / "notes.mid").stat().st_mtime if (folder / "notes.mid").exists() else 0}
    _reviews[str(folder)] = d
    return d


def song_summary(folder: Path, with_review=False) -> dict:
    # Clone Hero allows <color=...>/<b> tags in names; show the plain text
    ini = {k: re.sub(r"</?[a-z]+(=[^>]*)?>", "", v, flags=re.I) for k, v in tidy.read_ini(folder).items()}
    r = _reviews.get(str(folder))
    mtime = (folder / "notes.mid").stat().st_mtime if (folder / "notes.mid").exists() else folder.stat().st_mtime
    if with_review and (r is None or r.get("mtime") != mtime):
        r = review_dict(folder)
    return {"folder": str(folder), "name": folder.name, "artist": ini.get("artist", ""), "title": ini.get("name", ""),
            "album": ini.get("album", ""), "year": ini.get("year", ""), "status": review.status(folder),
            "tiers": {k: ini.get(f"diff_{k}", "") for k in ("guitar", "bass", "drums", "vocals")},
            "has_cover": (folder / "album.jpg").exists() or (folder / "album.png").exists(),
            "cover_v": max((int((folder / n).stat().st_mtime) for n in ("album.jpg", "album.png") if (folder / n).exists()), default=0),
            "genre": ini.get("genre", ""), "charter": ini.get("charter", ""), "added_by": ini.get("chc_added_by", ""),
            "stems": [p.stem for p in folder.iterdir() if p.suffix.lower() in (".ogg", ".opus", ".mp3")],
            "modified": mtime, "verdict": r["verdict"] if r else None} | feedback.of(ini)


@app.get("/api/staging")
def staging_list(review_all: bool = False):
    root = staging_dir()
    if not root.exists():
        return {"folder": str(root), "songs": []}
    songs = [song_summary(p, review_all) for p in library.song_folders(root) if library.is_ours(p)]
    return {"folder": str(root), "songs": sorted(songs, key=lambda s: -s["modified"])}


@app.get("/api/staging/review")
def staging_review(folder: str):
    f = body_folder(folder)
    return song_summary(f) | review_dict(f) | {"rechart": rechart.options(f)}


@app.post("/api/staging/status")
async def staging_status(req: Request):
    require_owner(req)
    b = await req.json()
    review.set_status(body_folder(b["folder"]), b["status"])
    return {"ok": True}


@app.post("/api/staging/publish")
async def staging_publish(req: Request):
    require_owner(req)
    b = await req.json()
    if not live_dir():
        raise HTTPException(400, "Set the live folder (Quickplay) in Settings first")
    ok, msg, dest = review.publish(body_folder(b["folder"]), live_dir(), bool(b.get("force")))
    return {"ok": ok, "message": msg, "folder": str(dest) if dest else None}


@app.post("/api/staging/remove")
async def staging_remove(req: Request):
    """Send a staging song to the Recycle Bin. Only RockWizard's own songs inside staging qualify."""
    require_owner(req)
    f = our_song((await req.json())["folder"])
    if staging_dir().resolve() not in f.resolve().parents:
        raise HTTPException(403, "Only songs in staging can be removed here")
    if any(j.status in ("queued", "running") and j.args.get("folder") == str(f) for j in jobs.values()):
        raise HTTPException(409, "This song is being re-charted. Cancel that first")
    n, errs = library.recycle([f])
    if not n:
        raise HTTPException(500, errs[0])
    return {"ok": True}


@app.post("/api/open")
async def open_on_pc(req: Request):
    """Desktop helpers: open a folder, play in Clone Hero, open in Moonscraper (on this PC)."""
    require_owner(req)
    b = await req.json()
    what, folder = b.get("what"), body_folder(b["folder"])
    if what == "folder":
        os.startfile(folder)
    elif what == "clonehero":
        exe = cfg.get("clonehero_exe", "")
        if not exe or not Path(exe).exists():
            raise HTTPException(400, "Set Clone Hero's location in Settings")
        subprocess.Popen([exe], cwd=str(Path(exe).parent))
    elif what == "moonscraper":
        exe = cfg.get("moonscraper_exe", "")
        if not exe or not Path(exe).exists():
            raise HTTPException(400, "Set Moonscraper's location in Settings")
        subprocess.Popen([exe, str(folder / "notes.mid")], cwd=str(Path(exe).parent))
    return {"ok": True}


@app.get("/api/audio")
def stem_audio(folder: str, stem: str):
    f = body_folder(folder)
    if not stem.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "Bad stem name")
    for ext in (".ogg", ".opus", ".mp3"):
        p = f / f"{stem}{ext}"
        if p.exists():
            return FileResponse(p, media_type="audio/ogg" if ext != ".mp3" else "audio/mpeg")
    raise HTTPException(404)


THUMBS = ROOT / ".cache" / "covers"


@app.get("/api/cover")
def cover(folder: str, w: int = 0):
    """The song's cover; w=… gives a small cached copy (many human charts ship multi-MB covers)."""
    f = body_folder(folder)
    for n in ("album.jpg", "album.png"):
        src = f / n
        if not src.exists():
            continue
        if not 0 < w <= 600:
            return FileResponse(src, headers={"Cache-Control": "max-age=86400"})
        import hashlib
        st = src.stat()
        thumb = THUMBS / (hashlib.sha1(f"{src}|{st.st_mtime_ns}|{st.st_size}|{w}".encode()).hexdigest() + ".jpg")
        if not thumb.exists():
            try:
                from PIL import Image
                THUMBS.mkdir(parents=True, exist_ok=True)
                img = Image.open(src).convert("RGB")
                img.thumbnail((w, w))
                img.save(thumb, "JPEG", quality=85)
            except Exception:
                return FileResponse(src)
        return FileResponse(thumb, headers={"Cache-Control": "max-age=86400"})
    raise HTTPException(404)


def our_song(path: str) -> Path:
    """A song folder RockWizard made: the only ones the app edits (human charts are never touched)."""
    f = body_folder(path)
    if not library.is_ours(f):
        raise HTTPException(403, "Only songs RockWizard made can be edited here")
    return f


@app.get("/api/cover/search")
def cover_search(folder: str = "", artist: str = "", title: str = ""):
    if folder:
        ini = tidy.read_ini(body_folder(folder))
        artist, title = artist or ini.get("artist", ""), title or ini.get("name", "")
    return {"covers": [asdict(c) for c in search.cover_candidates(artist, title)]}


@app.post("/api/cover/set")
async def cover_set(req: Request):
    require_owner(req)
    b = await req.json()
    f = our_song(b["folder"])
    try:
        search.save_cover(b["url"], f)
    except Exception as e:
        raise HTTPException(400, f"Couldn't save the cover: {e}")
    return song_summary(f)


@app.post("/api/cover/upload")
async def cover_upload(request: Request, folder: str = Form(...), file: UploadFile = File(...)):
    require_owner(request)
    f = our_song(folder)
    try:
        search.write_cover(await file.read(), f)
    except Exception as e:
        raise HTTPException(400, f"That file isn't an image Clone Hero can use ({e.__class__.__name__})")
    return song_summary(f)


@app.post("/api/cover/fill")
async def cover_fill(req: Request):
    """Give every listed song without a cover the best match, when the match is clear."""
    require_owner(req)
    done, missed = [], []
    for path in (await req.json())["folders"]:
        f = our_song(path)
        if (f / "album.jpg").exists() or (f / "album.png").exists():
            continue
        ini = tidy.read_ini(f)
        cs = search.cover_candidates(ini.get("artist", ""), ini.get("name", ""), n=1)
        try:
            if not cs or not ini.get("artist"):
                raise ValueError("no clear match")
            search.save_cover(cs[0].url, f)
            done.append(f.name)
        except Exception:
            missed.append(f.name)
    return {"done": done, "missed": missed}


# ---------- live library (Quickplay) ----------
@app.get("/api/live")
def live_list():
    root = live_dir()
    if not root or not root.exists():
        return {"folder": str(root or ""), "songs": []}
    songs = [song_summary(p) | {"ours": library.is_ours(p)} for p in library.song_folders(root)]
    songs.sort(key=lambda x: -x["modified"])
    return {"folder": str(root), "songs": songs}


@app.get("/api/live/song")
def live_song(folder: str):
    f = body_folder(folder)
    return song_summary(f) | {"ours": library.is_ours(f), "rechart": rechart.options(f)}


@app.post("/api/live/unpublish")
async def live_unpublish(req: Request):
    """Move a RockWizard song from the live folder back to staging (Syncthing removes it from the Deck)."""
    require_owner(req)
    f = our_song((await req.json())["folder"])
    dest = staging_dir() / f.name
    if dest.exists():
        raise HTTPException(409, f"“{f.name}” is already in staging")
    review.set_status(f, "draft")
    shutil.move(str(f), str(dest))
    return {"folder": str(dest)}


DETAIL_KEYS = ("name", "artist", "album", "year", "genre", "charter")


@app.post("/api/song/details")
async def song_details_set(req: Request):
    """Edit song.ini details; rename=True then tidies the song the same way Tidy names does
    (title without (…)/[…] tags, folder renamed 'Artist - Song Title')."""
    require_owner(req)
    b = await req.json()
    f = our_song(b["folder"])
    fields = {k: str(v).strip() for k, v in (b.get("fields") or {}).items() if k in DETAIL_KEYS}
    if "name" in fields and not fields["name"]:
        raise HTTPException(400, "The song title can't be empty")
    tidy.write_ini(f, fields)
    if b.get("rename"):
        try:
            new = tidy.tidy_one(f, artist_map=artist_map())
        except FileExistsError as e:
            raise HTTPException(409, f"Saved, but couldn't rename: “{e}” already exists")
        if str(new) != str(f):
            _reviews.pop(str(f), None)
            f = new
    return song_summary(f)


@app.post("/api/song/rating")
async def song_rating(req: Request):
    """How a RockWizard chart plays, from anyone who played it (friends too): one part's rating
    (inst + rating, "" clears it) and/or a note on what's off."""
    b = await req.json()
    f = our_song(b["folder"])
    roots = [r.resolve() for r in (staging_dir(), live_dir()) if r]
    if not any(f.resolve().is_relative_to(r) for r in roots):  # friends rate; keep them to the libraries
        raise HTTPException(403, "Only songs in staging or Quickplay can be rated")
    inst, value, note = b.get("inst"), b.get("rating") or "", b.get("note")
    if inst is not None and (inst not in feedback.INSTRUMENTS or value not in (*feedback.RATINGS, "")):
        raise HTTPException(400, "Unknown rating")
    if note is not None:
        note = " ".join(str(note).split())[:300]  # one line in song.ini
    feedback.rate(f, rater(req), inst, value, note)
    return song_summary(f)


# ---------- library ----------
@app.get("/api/library/ours")
def library_ours(request: Request, folder: str = ""):
    require_owner(request)
    root = Path(folder) if folder else staging_dir()
    return {"folder": str(root), "songs": [song_summary(p) for p in library.song_folders(root) if library.is_ours(p)]}


@app.get("/api/library/batch")
def library_batch(request: Request, folder: str):
    require_owner(request)
    root = body_folder(folder)
    have = {}
    if staging_dir().exists():
        for f in library.song_folders(staging_dir()):
            if library.is_ours(f):
                have[library.song_key(*library.names_of(f))] = f.name
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXT
                   and not tidy.is_song_folder(p.parent) and p.stem.lower() not in STEM_NAMES)
    out = []
    for p in files:
        a, t = search.names_from_file(p.stem)
        out.append({"path": str(p), "file": p.name, "artist": a, "title": t,
                    "existing": have.get(library.song_key(a, t)) or have.get(library.song_key(t, a))})
    return {"folder": str(root), "files": out}


def artist_map() -> dict:
    """How the live library capitalizes each artist (staging songs aren't trusted for this)."""
    return tidy.artist_spellings(live_dir()) if live_dir() else {}


@app.get("/api/tidy")
def tidy_scan(request: Request, folder: str, parens: bool = True, brackets: bool = True, ini: bool = True,
              caps: bool = True):
    require_owner(request)
    root = body_folder(folder)
    props = tidy.scan(root, parens, brackets, ini, caps, artist_map() if caps else None)
    return {"folder": str(root), "can_undo": tidy.can_undo(root),
            "proposals": [{"path": str(p.path), "name": p.path.name, "new_name": p.new_name, "note": p.note,
                           "ini_only": p.ini_only, "check": p.check} for p in props]}


@app.post("/api/tidy/apply")
async def tidy_apply(req: Request):
    require_owner(req)
    b = await req.json()
    root = body_folder(b["folder"])
    chosen = set(b.get("paths", []))
    caps = b.get("caps", True)
    amap = artist_map() if caps else None
    props = tidy.scan(root, b.get("parens", True), b.get("brackets", True), b.get("ini", True), caps, amap)
    for p in props:
        p.selected = str(p.path) in chosen
    n, errs = tidy.apply(props, root, b.get("parens", True), b.get("brackets", True), True, caps, amap)
    return {"renamed": n, "errors": errs}


@app.post("/api/tidy/undo")
async def tidy_undo(req: Request):
    require_owner(req)
    b = await req.json()
    n, errs = tidy.undo(body_folder(b["folder"]))
    return {"restored": n, "errors": errs}


@app.get("/api/dupes")
def dupes(request: Request, folder: str):
    require_owner(request)
    root = body_folder(folder)
    groups = library.find_duplicates(root)
    return {"folder": str(root), "groups": [[{
        "path": str(c.folder), "name": c.folder.name, "artist": c.artist, "title": c.title, "charter": c.charter,
        "parts": c.parts, "size_mb": round(c.size_mb, 1), "ours": c.ours, "keep": c.keep,
        "identical": c.identical} for c in g] for g in groups]}


@app.post("/api/dupes/remove")
async def dupes_remove(req: Request):
    require_owner(req)
    b = await req.json()
    n, errs = library.recycle([Path(p) for p in b.get("paths", []) if Path(p).exists()])
    return {"removed": n, "errors": errs}


# ---------- settings + status ----------
SETTING_KEYS = ("songs_dir", "live_dir", "tidy_dir", "charter", "clonehero_exe", "moonscraper_exe")


@app.get("/api/settings")
def get_settings(request: Request):
    local = request.client and request.client.host in ("127.0.0.1", "::1")
    return {k: cfg.get(k, "") for k in SETTING_KEYS} | {"local": bool(local)}


@app.post("/api/settings")
async def set_settings(req: Request):
    require_owner(req)
    b = await req.json()
    for k in SETTING_KEYS:
        if k in b:
            cfg[k] = str(b[k]).strip()
    save_config(cfg)
    return {k: cfg.get(k, "") for k in SETTING_KEYS}


@app.get("/api/startup")
def get_startup():
    return {"enabled": startup.enabled()}


@app.post("/api/startup")
async def set_startup(req: Request):
    require_owner(req)
    if (await req.json()).get("enabled"):
        startup.enable()
    else:
        startup.disable()
    return {"enabled": startup.enabled()}


@app.post("/api/downloader/update")
def downloader_update(request: Request):
    require_owner(request)
    return {"message": search.update_downloader()}


def lan_addresses() -> list[str]:
    import socket
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addrs.add(ip)
    except OSError:
        pass
    # Tailscale addresses (100.64.0.0/10) first: they work away from home
    return sorted(addrs, key=lambda ip: (not ip.startswith("100."), ip))


@app.get("/api/pairing")
def pairing(request: Request):
    require_owner(request)
    import io
    import qrcode
    import qrcode.image.svg
    urls = [f"http://{ip}:{PORT}/?key={cfg['pair_key']}" for ip in lan_addresses()]
    svg = ""
    if urls:
        buf = io.BytesIO()
        qrcode.make(urls[0], image_factory=qrcode.image.svg.SvgPathImage, box_size=10).save(buf)
        svg = buf.getvalue().decode()
    return {"urls": urls, "qr": svg}


@app.post("/api/pairing/reset")
def pairing_reset(request: Request):
    require_owner(request)
    cfg["pair_key"] = secrets.token_urlsafe(16)
    save_config(cfg)
    return pairing()


def syncthing_status() -> dict | None:
    """How the live folder is syncing (Syncthing's local REST API), or None without Syncthing."""
    conf = Path(os.environ.get("LOCALAPPDATA", "")) / "Syncthing" / "config.xml"
    if not conf.exists() or not live_dir():
        return None
    try:
        import urllib.request
        root = ET.parse(conf).getroot()
        key = root.findtext("gui/apikey")
        addr = root.findtext("gui/address") or "127.0.0.1:8384"
        live = str(live_dir().resolve()).lower()
        folder = next((f for f in root.findall("folder") if str(Path(f.get("path")).resolve()).lower() == live), None)
        if folder is None:
            return {"state": "not shared", "label": ""}

        def get(path):
            r = urllib.request.Request(f"http://{addr}{path}", headers={"X-API-Key": key})
            return json.load(urllib.request.urlopen(r, timeout=3))
        st = get(f"/rest/db/status?folder={folder.get('id')}")
        devices = [d.get("id") for d in folder.findall("device")]
        names = {d.get("id"): d.get("name") for d in root.findall("device")}
        conns = get("/rest/system/connections").get("connections", {})
        remote = []
        for d in devices:
            if d not in conns:
                continue
            comp = get(f"/rest/db/completion?folder={folder.get('id')}&device={d}")
            remote.append({"name": names.get(d, "device"), "connected": conns[d].get("connected", False),
                           "completion": round(comp.get("completion", 0), 1)})
        return {"state": st.get("state", "?"), "label": folder.get("label", ""), "devices": remote}
    except Exception as e:
        return {"state": "unavailable", "error": e.__class__.__name__}


@app.post("/api/sync/rescan")
def sync_rescan(request: Request):
    require_owner(request)
    conf = Path(os.environ.get("LOCALAPPDATA", "")) / "Syncthing" / "config.xml"
    try:
        import urllib.request
        root = ET.parse(conf).getroot()
        key, addr = root.findtext("gui/apikey"), root.findtext("gui/address") or "127.0.0.1:8384"
        live = str(live_dir().resolve()).lower()
        folder = next(f for f in root.findall("folder") if str(Path(f.get("path")).resolve()).lower() == live)
        r = urllib.request.Request(f"http://{addr}/rest/db/scan?folder={folder.get('id')}", method="POST",
                                   headers={"X-API-Key": key})
        urllib.request.urlopen(r, timeout=5)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, f"Couldn't reach Syncthing ({e.__class__.__name__})")


@app.get("/api/status")
def status():
    run = ROOT / "data" / "v3" / "run.log"
    training = None
    if run.exists():
        lines = run.read_text(errors="replace").strip().splitlines()
        last = lines[-1] if lines else ""
        lock = ROOT / "data" / "v3" / "RUNNING"
        training = {"last": last, "running": lock.exists() and "done" not in last.split(" ")[-8:]}
    busy = any(j.status == "running" for j in jobs.values())
    queued = sum(1 for j in jobs.values() if j.status == "queued")
    return {"busy": busy, "queued": queued, "training": training, "sync": syncthing_status()}


# ---------- web app ----------
@app.get("/")
def index():
    # stamp the script and styles with their change time so browsers (and the phone's home-screen
    # app) always load the current version after an update
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "app.css"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={int((WEB / name).stat().st_mtime)}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return Response((WEB / "manifest.webmanifest").read_text(encoding="utf-8"),
                    media_type="application/manifest+json")


app.mount("/static", StaticFiles(directory=WEB), name="static")


_uvicorn = None  # the running uvicorn.Server, so /api/server/stop can end it cleanly


@app.post("/api/server/stop")
def server_stop(request: Request):
    """Stop the server (only from this PC: a phone couldn't start it again). Any running job ends too."""
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "The server can only be stopped from the PC")
    if _uvicorn is None:
        raise HTTPException(400, "This server wasn't started by RockWizard")
    threading.Timer(0.3, lambda: setattr(_uvicorn, "should_exit", True)).start()
    return {"stopping": True}


def serve(host="0.0.0.0", port=PORT):
    global _uvicorn
    import uvicorn
    _uvicorn = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    _uvicorn.run()


if __name__ == "__main__":
    serve()
