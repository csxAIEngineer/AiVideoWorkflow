from pathlib import Path
import re

# ========== mapping.py ==========
p = Path("/opt/fastvideo/FastVideo/local_api/mapping.py")
text = p.read_text()

needle = "TRIM_SEED = 1234\n"
insert = needle + "OMNI_GRID_POINTS = 8\nOMNI_VSA_SPARSITY = 0.9\nOMNI_VSA_TILE_SIZE = 128\n"
if "OMNI_GRID_POINTS" not in text:
    if needle not in text:
        raise SystemExit("TRIM_SEED not found")
    text = text.replace(needle, insert, 1)

old_prep = (
    "@dataclass(frozen=True)\n"
    "class PreparedGeneration:\n"
    '    """One Trim request after media files exist on disk."""\n'
    "\n"
    "    prompt: str\n"
    "    duration: int\n"
    "    width: int\n"
    "    height: int\n"
    "    num_frames: int\n"
    "    steps: int\n"
    "    seed: int\n"
    "    first_frame: Path | None\n"
    "    last_frame: Path | None\n"
    "    output_path: Path\n"
    "    image_count: int\n"
)
new_prep = (
    "@dataclass(frozen=True)\n"
    "class PreparedGeneration:\n"
    '    """One request after media files exist on disk."""\n'
    "\n"
    "    prompt: str\n"
    "    duration: int\n"
    "    width: int\n"
    "    height: int\n"
    "    num_frames: int\n"
    "    steps: int\n"
    "    seed: int\n"
    "    first_frame: Path | None\n"
    "    last_frame: Path | None\n"
    "    output_path: Path\n"
    "    image_count: int\n"
    "    # Ordered Ref2VA references as (media_type, path). Empty for Trim.\n"
    "    references: tuple[tuple[str, Path], ...] = ()\n"
)
if "references: tuple[tuple[str, Path], ...]" not in text:
    if old_prep not in text:
        raise SystemExit("PreparedGeneration block not found")
    text = text.replace(old_prep, new_prep, 1)

pat2 = re.compile(
    r"    if any\(item\.role in REFERENCE_ROLES for item in media\):\n"
    r"        raise APIError\(\n"
    r'            "FastH3 Trim supports first_frame and last_frame; reference_image, "\n'
    r'            "reference_video, and reference_audio require a Ref2VA checkpoint",?\s*\)\n',
    re.M,
)
text2, n = pat2.subn("", text, count=1)
if n != 1:
    raise SystemExit(f"reject block not matched n={n}")
text = text2

text = text.replace(
    "Map MiniMax-H3 DashScope requests onto a FastH3 Trim generation.",
    "Map MiniMax-H3 DashScope requests onto a FastH3 Trim or OmniRef generation.",
    1,
)

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


def require_references_for_mode(media: tuple[MediaItem, ...], mode: str) -> None:
    """Enforce Trim vs OmniRef media rules after roles are validated."""
    has_ref = any(item.role in REFERENCE_ROLES for item in media)
    has_frame = any(item.role in FRAME_ROLES for item in media)
    if mode == "ref2va":
        if has_frame:
            raise APIError(
                "OmniRef Ref2VA does not accept first_frame/last_frame; use reference_image/video/audio"
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
if "def require_references_for_mode" not in text:
    text = text.replace(
        "def parse_synthesis_body(body: object) -> JobSpec:",
        helper + "def parse_synthesis_body(body: object) -> JobSpec:",
        1,
    )

text = text.replace(
    'def parse_synthesis_body(body: object) -> JobSpec:\n    """Validate a video-synthesis JSON body and return a Trim job."""\n',
    'def parse_synthesis_body(body: object, mode: str = "trim") -> JobSpec:\n    """Validate a video-synthesis JSON body and return a job."""\n',
    1,
)

if "require_references_for_mode(media, mode)" not in text:
    text = text.replace(
        "    _validate_media_roles(media)\n",
        "    _validate_media_roles(media)\n    require_references_for_mode(media, mode)\n",
        1,
    )

p.write_text(text)
print("mapping.py OK")

# Continue: prepare_generation return + references; rewrite generator; patch server

prep_old = '''    return PreparedGeneration(
        prompt=job.prompt,
        duration=job.duration,
        width=job.width,
        height=job.height,
        num_frames=job.num_frames,
        steps=TRIM_GRID_POINTS,
        seed=TRIM_SEED,
        first_frame=first_frame,
        last_frame=last_frame,
        output_path=out_path,
        image_count=image_count,
    )
'''
prep_new = '''    references = media_as_references(saved)
    steps = OMNI_GRID_POINTS if mode == "ref2va" else TRIM_GRID_POINTS
    return PreparedGeneration(
        prompt=job.prompt,
        duration=job.duration,
        width=job.width,
        height=job.height,
        num_frames=job.num_frames,
        steps=steps,
        seed=TRIM_SEED,
        first_frame=first_frame,
        last_frame=last_frame,
        output_path=out_path,
        image_count=image_count,
        references=references,
    )
'''

p = Path("/opt/fastvideo/FastVideo/local_api/mapping.py")
text = p.read_text()
if "references=references" not in text:
    # prepare_generation needs mode param
    text = text.replace(
        "def prepare_generation(job: JobSpec, work_dir: Path) -> PreparedGeneration:",
        'def prepare_generation(job: JobSpec, work_dir: Path, mode: str = "trim") -> PreparedGeneration:',
        1,
    )
    if prep_old not in text:
        raise SystemExit("prepare_generation return not found")
    text = text.replace(prep_old, prep_new, 1)
    p.write_text(text)
    print("prepare_generation OK")
else:
    print("prepare_generation already patched")

# generator.py: full replace from local copy
src = Path("/tmp/generator_omni.py")
dst = Path("/opt/fastvideo/FastVideo/local_api/generator.py")
if not src.is_file():
    raise SystemExit("missing /tmp/generator_omni.py")
dst.write_text(src.read_text())
print("generator.py replaced")

# server.py patches
sp = Path("/opt/fastvideo/FastVideo/local_api/server.py")
s = sp.read_text()

if "choices=(" not in s or "ref2va" not in s.split("--mode", 1)[-1][:200]:
    s = s.replace(
        '    parser.add_argument("--mode", default="trim", help="Generation mode (trim only for now)")\n',
        '    parser.add_argument(\n'
        '        "--mode",\n'
        '        default="ref2va",\n'
        '        choices=("trim", "ref2va"),\n'
        '        help="trim=FL2VA/T2VA; ref2va=OmniRef Ref2VA",\n'
        "    )\n",
        1,
    )

s = s.replace(
    '    parser.add_argument("--model", default=DEFAULT_MODEL_PATH, help="Local FastH3 Trim checkpoint")\n',
    '    parser.add_argument(\n'
    '        "--model",\n'
    '        default=os.environ.get(\n'
    '            "FASTH3_MODEL",\n'
    '            "/opt/fastvideo/models/FastH3-Omni-FP8",\n'
    "        ),\n"
    '        help="Local FastH3 Trim or OmniRef checkpoint",\n'
    "    )\n",
    1,
)

# ensure `import os` exists
if "\nimport os\n" not in s and not s.startswith("import os\n"):
    if "from __future__ import annotations\n" in s:
        s = s.replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n\nimport os\n",
            1,
        )
    else:
        s = "import os\n" + s

# FastVideoBackend construction with mode=
if "mode=args.mode" not in s:
    s = s.replace(
        "    backend = FastVideoBackend(\n"
        "        model_path=args.model,\n"
        "        quant=args.quant,\n"
        "        num_gpus=args.num_gpus,\n"
        "        vsa_sparsity=args.vsa_sparsity,\n"
        "        offload=args.offload,\n"
        "    )\n",
        "    backend = FastVideoBackend(\n"
        "        model_path=args.model,\n"
        "        quant=args.quant,\n"
        "        num_gpus=args.num_gpus,\n"
        "        vsa_sparsity=args.vsa_sparsity,\n"
        "        offload=args.offload,\n"
        "        mode=args.mode,\n"
        "    )\n",
        1,
    )

# parse_synthesis_body / prepare_generation with mode
s = s.replace("job = parse_synthesis_body(body)", 'job = parse_synthesis_body(body, mode=MODE)', 1)
s = s.replace(
    "prepared = prepare_generation(job, task_dir)",
    "prepared = prepare_generation(job, task_dir, mode=MODE)",
    1,
)

# Global MODE from args
if "\nMODE =" not in s and "MODE = args.mode" not in s:
    # set after parse
    if "    args = parser.parse_args()" in s:
        s = s.replace(
            "    args = parser.parse_args()",
            "    args = parser.parse_args()\n    global MODE\n    MODE = args.mode",
            1,
        )
    # module-level default
    if "MODE: str = " not in s:
        s = s.replace(
            "app = FastAPI(",
            'MODE: str = "ref2va"\n\napp = FastAPI(',
            1,
        )

sp.write_text(s)
print("server.py OK")
print("ALL PATCHES DONE")
