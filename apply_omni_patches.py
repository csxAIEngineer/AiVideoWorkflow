#!/usr/bin/env python3
"""Apply OmniRef Ref2VA patches to FastVideo local_api on the ECS."""
from pathlib import Path
import shutil

ROOT = Path("/opt/fastvideo/FastVideo/local_api")

# ---------- mapping.py ----------
mp = ROOT / "mapping.py"
m = mp.read_text()

if "OMNI_GRID_POINTS" not in m:
    m = m.replace(
        "TRIM_SEED = 1234\n",
        "TRIM_SEED = 1234\nOMNI_GRID_POINTS = 8\nOMNI_VSA_SPARSITY = 0.9\n",
        1,
    )

old_prep = '''@dataclass(frozen=True)
class PreparedGeneration:
    """One Trim request after media files exist on disk."""

    prompt: str
    duration: int
    width: int
    height: int
    num_frames: int
    steps: int
    seed: int
    first_frame: Path | None
    last_frame: Path | None
    output_path: Path
    image_count: int
'''
new_prep = '''@dataclass(frozen=True)
class PreparedGeneration:
    """One request after media files exist on disk."""

    prompt: str
    duration: int
    width: int
    height: int
    num_frames: int
    steps: int
    seed: int
    first_frame: Path | None
    last_frame: Path | None
    output_path: Path
    image_count: int
    # Ordered Ref2VA (media_type, path). Empty for Trim FL2VA/T2VA.
    references: tuple[tuple[str, Path], ...] = ()
'''
if "references: tuple[tuple[str, Path], ...]" not in m:
    if old_prep not in m:
        raise SystemExit("PreparedGeneration block mismatch")
    m = m.replace(old_prep, new_prep, 1)

# Replace Trim-only reject with helpers + mode-aware check
old_reject = '''    _validate_media_roles(media)
    if any(item.role in REFERENCE_ROLES for item in media):
        raise APIError(
            "FastH3 Trim supports first_frame and last_frame; reference_image, "
            "reference_video, and reference_audio require a Ref2VA checkpoint", )

    parameters = data.get("parameters", {})
'''
new_reject = '''    _validate_media_roles(media)

    parameters = data.get("parameters", {})
'''
if "require a Ref2VA checkpoint" in m:
    if old_reject not in m:
        raise SystemExit("reject block mismatch")
    m = m.replace(old_reject, new_reject, 1)

helper = '''
def media_as_references(paths: list[tuple[MediaItem, Path]]) -> tuple[tuple[str, Path], ...]:
    """Ordered Ref2VA (media_type, path) pairs from DashScope media roles."""
    role_to_type = {
        "reference_image": "image",
        "reference_video": "video",
        "reference_audio": "audio",
    }
    out: list[tuple[str, Path]] = []
    for item, path in paths:
        media_type = role_to_type.get(item.role)
        if media_type:
            out.append((media_type, path))
    return tuple(out)


def enforce_mode_media(media: tuple[MediaItem, ...], mode: str) -> None:
    """Trim = frames only; OmniRef = at least one reference image/video."""
    has_ref = any(item.role in REFERENCE_ROLES for item in media)
    has_frame = any(item.role in FRAME_ROLES for item in media)
    if mode == "ref2va":
        if has_frame:
            raise APIError(
                "OmniRef Ref2VA does not accept first_frame/last_frame; "
                "use reference_image, reference_video, or reference_audio"
            )
        if not any(item.role in ("reference_image", "reference_video") for item in media):
            raise APIError("OmniRef Ref2VA requires at least one reference_image or reference_video")
    else:
        if has_ref:
            raise APIError(
                "FastH3 Trim supports first_frame and last_frame; reference_image, "
                "reference_video, and reference_audio require a Ref2VA checkpoint",
            )


'''
if "def enforce_mode_media" not in m:
    m = m.replace(
        "def parse_synthesis_body(body: object) -> JobSpec:",
        helper + "def parse_synthesis_body(body: object, mode: str = \"trim\") -> JobSpec:",
        1,
    )
else:
    m = m.replace(
        "def parse_synthesis_body(body: object) -> JobSpec:",
        'def parse_synthesis_body(body: object, mode: str = "trim") -> JobSpec:',
        1,
    )

if "enforce_mode_media(media, mode)" not in m:
    m = m.replace(
        "    _validate_media_roles(media)\n\n    parameters = data.get(\"parameters\", {})\n",
        "    _validate_media_roles(media)\n    enforce_mode_media(media, mode)\n\n"
        "    parameters = data.get(\"parameters\", {})\n",
        1,
    )

# Allow Omni model ids too
m = m.replace(
    '''def _accepted_model(model: str) -> bool:
    name = model.strip()
    if name in (CLIENT_MODEL_NAME, "MiniMax-H3"):
        return True
    return name.startswith("FastVideo/FastVideo-FastH3-Trim")
''',
    '''def _accepted_model(model: str) -> bool:
    name = model.strip()
    if name in (CLIENT_MODEL_NAME, "MiniMax-H3"):
        return True
    if name.startswith("FastVideo/FastVideo-FastH3-Trim"):
        return True
    if name.startswith("FastVideo/FastH3-Omni") or name.startswith("FastVideo/FastVideo-FastH3-Omni"):
        return True
    if "FastH3-Omni" in name or "FastH3-Trim" in name:
        return True
    return False
''',
)

# steps: allow 8 for Omni
m = m.replace(
    '''def _steps(parameters: dict) -> int | None:
    if "steps" not in parameters or parameters["steps"] is None:
        return None
    value = _as_int(parameters["steps"], "parameters.steps")
    if value in (8, TRIM_GRID_POINTS):
        return TRIM_GRID_POINTS
    raise APIError("parameters.steps must be 9 sigma-grid points (8 DMD forwards) for FastH3 Trim")
''',
    '''def _steps(parameters: dict) -> int | None:
    if "steps" not in parameters or parameters["steps"] is None:
        return None
    value = _as_int(parameters["steps"], "parameters.steps")
    if value in (8, TRIM_GRID_POINTS, OMNI_GRID_POINTS):
        return value if value == OMNI_GRID_POINTS else TRIM_GRID_POINTS
    raise APIError("parameters.steps must be 8 (OmniRef) or 9 (Trim sigma-grid)")
''',
)

mp.write_text(m)
print("mapping.py patched")

# ---------- generator.py (full replace) ----------
src = Path("/tmp/generator_omni.py")
if not src.is_file():
    raise SystemExit("missing /tmp/generator_omni.py")
shutil.copy2(src, ROOT / "generator.py")
# Ensure FastVideoTrimBackend alias exists (generator_omni already has it)
g = (ROOT / "generator.py").read_text()
if "FastVideoTrimBackend" not in g:
    raise SystemExit("generator missing FastVideoTrimBackend alias")
print("generator.py replaced")

# ---------- server.py ----------
sp = ROOT / "server.py"
s = sp.read_text()

# Import OMNI constants + media_as_references + enforce via parse mode
if "OMNI_GRID_POINTS" not in s:
    s = s.replace(
        "    TRIM_GRID_POINTS,\n",
        "    TRIM_GRID_POINTS,\n    OMNI_GRID_POINTS,\n    media_as_references,\n",
        1,
    )

# App gains mode
old_init = '''    def __init__(
        self,
        *,
        backend: GenerationBackend,
        output_dir: Path,
        work_cwd: Path,
        seed: int,
        host: str,
        port: int,
    ) -> None:
        self.backend = backend
        self.output_dir = output_dir
        self.work_cwd = work_cwd
        self.seed = seed
        self.host = host
        self.port = port
'''
new_init = '''    def __init__(
        self,
        *,
        backend: GenerationBackend,
        output_dir: Path,
        work_cwd: Path,
        seed: int,
        host: str,
        port: int,
        mode: str = "ref2va",
    ) -> None:
        self.backend = backend
        self.output_dir = output_dir
        self.work_cwd = work_cwd
        self.seed = seed
        self.host = host
        self.port = port
        self.mode = mode if mode in ("trim", "ref2va") else "ref2va"
'''
if "self.mode =" not in s:
    if old_init not in s:
        raise SystemExit("App.__init__ mismatch")
    s = s.replace(old_init, new_init, 1)

# _generate: references + steps by mode
old_gen = '''        width, height = resolve_canvas(task.spec, paths)
        image_count = sum(1 for item, _path in paths if item.role in ("first_frame", "last_frame"))
        seed = task.spec.seed if task.spec.seed is not None else self.seed
        steps = task.spec.steps if task.spec.steps is not None else TRIM_GRID_POINTS
        with task.lock:
            task.width = width
            task.height = height
            task.image_count = image_count
        prepared = PreparedGeneration(
            prompt=task.prompt,
            duration=task.spec.duration,
            width=width,
            height=height,
            num_frames=task.spec.num_frames,
            steps=steps,
            seed=seed,
            first_frame=_first_path(paths, {"first_frame"}),
            last_frame=_first_path(paths, {"last_frame"}),
            output_path=video_path,
            image_count=image_count,
        )
'''
new_gen = '''        width, height = resolve_canvas(task.spec, paths)
        image_count = sum(
            1
            for item, _path in paths
            if item.role in ("first_frame", "last_frame", "reference_image")
        )
        seed = task.spec.seed if task.spec.seed is not None else self.seed
        default_steps = OMNI_GRID_POINTS if self.mode == "ref2va" else TRIM_GRID_POINTS
        steps = task.spec.steps if task.spec.steps is not None else default_steps
        references = media_as_references(paths)
        with task.lock:
            task.width = width
            task.height = height
            task.image_count = image_count
        prepared = PreparedGeneration(
            prompt=task.prompt,
            duration=task.spec.duration,
            width=width,
            height=height,
            num_frames=task.spec.num_frames,
            steps=steps,
            seed=seed,
            first_frame=_first_path(paths, {"first_frame"}),
            last_frame=_first_path(paths, {"last_frame"}),
            output_path=video_path,
            image_count=image_count,
            references=references,
        )
'''
if "references=references" not in s:
    if old_gen not in s:
        raise SystemExit("_generate PreparedGeneration block mismatch")
    s = s.replace(old_gen, new_gen, 1)

# parse_synthesis_body with mode
s = s.replace(
    "            spec = parse_synthesis_body(body)\n",
    "            spec = parse_synthesis_body(body, mode=app.mode)\n",
    1,
)

# CLI --mode and default model
old_model = '''    parser.add_argument(
        "--model",
        default=os.environ.get("FASTH3_MODEL", "FastVideo/FastVideo-FastH3-Trim-8-Step"),
        help="FastH3 Trim checkpoint repo id or local directory",
    )
'''
new_model = '''    parser.add_argument(
        "--mode",
        default=os.environ.get("FASTH3_MODE", "ref2va"),
        choices=("trim", "ref2va"),
        help="trim=FL2VA/T2VA; ref2va=OmniRef Ref2VA (default)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("FASTH3_MODEL", "/opt/fastvideo/models/FastH3-Omni-FP8"),
        help="FastH3 Trim or OmniRef checkpoint repo id or local directory",
    )
'''
if "--mode" not in s or 'choices=("trim", "ref2va")' not in s:
    if old_model not in s:
        raise SystemExit("--model arg block mismatch")
    s = s.replace(old_model, new_model, 1)

# FastVideoTrimBackend with mode + App mode=
old_backend = '''    backend = FastVideoTrimBackend(
        model_path=args.model,
        quant=args.quant,
        num_gpus=args.num_gpus,
        vsa_sparsity=args.vsa_sparsity,
        offload=bool(args.offload),
    )
    return App(
        backend=backend,
        output_dir=output,
        work_cwd=cwd,
        seed=args.seed,
        host=args.host,
        port=args.port,
    )
'''
new_backend = '''    backend = FastVideoTrimBackend(
        model_path=args.model,
        quant=args.quant,
        num_gpus=args.num_gpus,
        vsa_sparsity=args.vsa_sparsity,
        offload=bool(args.offload),
        mode=args.mode,
    )
    return App(
        backend=backend,
        output_dir=output,
        work_cwd=cwd,
        seed=args.seed,
        host=args.host,
        port=args.port,
        mode=args.mode,
    )
'''
if "mode=args.mode" not in s:
    if old_backend not in s:
        raise SystemExit("app_from_args backend block mismatch")
    s = s.replace(old_backend, new_backend, 1)

# Startup message
s = s.replace(
    'print(f"failed to start FastH3 Trim: {exc}", file=sys.stderr)\n',
    'print(f"failed to start FastH3: {exc}", file=sys.stderr)\n',
    1,
)

sp.write_text(s)
print("server.py patched")

# Sanity compile
import py_compile
for name in ("mapping.py", "generator.py", "server.py"):
    py_compile.compile(str(ROOT / name), doraise=True)
    print("compile ok", name)
print("DONE")
