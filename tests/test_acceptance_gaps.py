"""Offline checks for the three f38578a acceptance gaps.

No langgraph import, no provider, no AI subprocess. Model text is a local fake.
The chat path is rpc_send_message, and the label is the option string the
existing choice button sends.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import end.db as db
from end.db import project_dir, read_project, write_project
from end.history_keep import ArchiveError
from end.render_loop import RenderContext, invalidate_steps, load_run_state, output_paths, save_run_state
from end.script_review import (
    GenerationBlocked,
    authorize_image_submit,
    authorize_video_submit,
    confirm_script,
    lease_still_current,
    review_delivery,
    review_script,
    set_reviewer,
)
from end.server import _present_script_gate, rpc_send_message
from end.rerun import _drop_film
from end.trace import clear_downstream, restore_trace

_TEMPLATE = {
    "image": "",
    "image_from": "",
    "video_lock": "",
    "video_next": "",
    "video_tail": "",
    "video_from": "",
}


def _forbid_process(*_args, **_kwargs):
    raise AssertionError("offline: 测试禁止拉起子进程")


def _local_model(_value=None):
    return _value or "fake-model"


def _local_label(_value=None):
    return "fake"


class _GapBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (db.DATA, db.DB_PATH, db._CONN)
        db.DATA = self.root / "projects"
        db.DB_PATH = self.root / "pipeline.db"
        db._CONN = None
        db.DATA.mkdir()
        self.tasks = []
        self._patches = [
            patch("subprocess.Popen", side_effect=_forbid_process),
            patch("end.ai_client._spawn", side_effect=_forbid_process),
            patch("end.ai_client.call", side_effect=_forbid_process),
            patch("end.ai_client.ask_text", side_effect=_forbid_process),
            patch("end.ai_client.template_render", return_value=dict(_TEMPLATE)),
            patch("end.server.normalize_model", side_effect=_local_model),
            patch("end.server.model_label", side_effect=_local_label),
            patch("end.server.normalize_template", side_effect=_local_model),
            patch("end.server.template_label", side_effect=_local_label),
            patch("end.server.submit_agent_task", side_effect=self._capture_task),
        ]
        for item in self._patches:
            item.start()
        set_reviewer(self._review)
        self.review_body = '{"issues":[]}'

    def tearDown(self):
        set_reviewer(None)
        for item in reversed(self._patches):
            item.stop()
        conn = db._CONN
        db.DATA, db.DB_PATH, db._CONN = self._old
        if conn is not None and conn is not self._old[2]:
            conn.close()
        self.tmp.cleanup()

    def _review(self, _messages):
        return self.review_body

    def _capture_task(self, task):
        self.tasks.append(task)
        return True

class SuspectGateDispatchTest(_GapBase):
    def _project(self, project_id):
        created = "2026-01-01T00:00:00+08:00"
        write_project(
            {
                "id": project_id,
                "brief": "雨",
                "messages": [{"role": "user", "content": "雨"}],
                "title": "雨",
                "status": "writing",
                "stage": "剧本",
                "script": "甲看着雨。",
                "storyboard": "",
                "image_prompts": "",
                "error": None,
                "events": [],
                "tasks": [],
                "progress": {"percent": 40, "phase": "writing", "message": "剧本", "lock_machine": True},
                "cloud_log": "",
                "prompt_id": "",
                "video_file": "",
                "model": "fake-model",
                "template": "short-drama-director",
                "created_at": created,
                "started_at": created,
                "updated_at": created,
                "finished_at": None,
            }
        )
        folder = project_dir(project_id)
        internal = folder / "_内部_"
        internal.mkdir(parents=True, exist_ok=True)
        (internal / "剧本.md").write_text("甲看着雨。\n", encoding="utf-8")
        (folder / "spec.json").write_text(
            json.dumps({"script": "甲看着雨。", "aspect_ratio": "9:16"}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.review_body = json.dumps(
            {
                "issues": [
                    {
                        "location": "场",
                        "evidence": "甲看着雨",
                        "category": "continuity",
                        "severity": "suspect",
                        "reason": "动机没有写出来",
                        "owner": "script",
                        "revision": "补上她为什么留下",
                    }
                ]
            },
            ensure_ascii=False,
        )
        _present_script_gate(read_project(project_id))
        project = read_project(project_id)
        options = ((project.get("progress") or {}).get("question") or {}).get("options") or []
        self.assertEqual(options, ["接受可疑项并继续", "根据建议修改"])
        return project, options

    def _send(self, project_id, label):
        before = len(self.tasks)
        view = rpc_send_message({"id": project_id, "text": label})
        return view, self.tasks[before:]

    def test_frontend_choice_sends_the_option_label(self):
        source = Path("front/app.js").read_text(encoding="utf-8")
        choice = source.split("function choiceList", 1)[1].split("function messageBubble", 1)[0]
        send = source.split("async function sendText", 1)[1].split("const materialFile", 1)[0]
        self.assertIn("sendText(label)", choice)
        self.assertIn("projects.send_message", send)
        self.assertIn("text: body", send)
        self.assertNotIn('sendText("继续")', choice)
        _project, options = self._project("a1b2c3d4e501")
        self.assertEqual(options, ["接受可疑项并继续", "根据建议修改"])

    def test_explicit_accept_enters_assets(self):
        project, options = self._project("a1b2c3d4e502")
        view, tasks = self._send(project["id"], options[0])
        self.assertEqual(view["status"], "writing")
        pipeline = view["progress"]["pipeline"]
        self.assertEqual(pipeline.get("current"), "asset")
        self.assertEqual(pipeline.get("action"), "write")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].kind, "write")
        self.assertEqual(tasks[0].project_id, "a1b2c3d4e502")

    def test_plain_words_do_not_accept(self):
        from end.script_review import load_reviews

        for word, project_id in (("继续", "a1b2c3d4e503"), ("接受", "a1b2c3d4e506"), ("确认", "a1b2c3d4e507")):
            project, _options = self._project(project_id)
            view, tasks = self._send(project["id"], word)
            self.assertEqual(view["status"], "gate", word)
            self.assertEqual(tasks, [], word)
            self.assertIn("不能代替", view["messages"][-1]["content"])
            saved = read_project(project_id)
            self.assertEqual(saved["status"], "gate")
            state = load_reviews(project_dir(project_id))
            self.assertFalse(state.get("confirmed_script_hash"))
            self.assertNotEqual((state.get("script") or {}).get("accepted_by"), "human")

    def test_repeat_accept_does_not_enqueue_again(self):
        from end.server import RpcError

        project, options = self._project("a1b2c3d4e508")
        view, tasks = self._send(project["id"], options[0])
        self.assertEqual(view["status"], "writing")
        self.assertEqual(len(tasks), 1)
        with self.assertRaises(RpcError):
            rpc_send_message({"id": project["id"], "text": options[0]})
        self.assertEqual(len(self.tasks), 1)
        self.assertEqual(read_project(project["id"])["status"], "writing")

    def test_revise_clears_an_existing_acceptance(self):
        from end.script_review import load_reviews

        project, options = self._project("a1b2c3d4e504")
        folder = project_dir(project["id"])
        self.assertTrue(confirm_script(folder, accept_suspects=True, brief="雨")[0])
        planted = load_reviews(folder)
        self.assertTrue(planted.get("confirmed_script_hash"))
        self.assertEqual(planted["script"]["accepted_by"], "human")
        view, tasks = self._send(project["id"], options[1])
        self.assertEqual(options[1], "根据建议修改")
        self.assertEqual(view["status"], "writing")
        pipeline = view["progress"]["pipeline"]
        self.assertEqual(pipeline.get("current"), "script")
        self.assertEqual(pipeline.get("action"), "script_repair")
        self.assertEqual(pipeline.get("repair"), "")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].kind, "script_repair")
        state = load_reviews(folder)
        self.assertFalse(state.get("confirmed_script_hash"))
        self.assertNotEqual((state.get("script") or {}).get("accepted_by"), "human")


class ImageLeaseTest(_GapBase):
    def _folder(self, name="film"):
        folder = self.root / name
        folder.mkdir()
        internal = folder / "_内部_"
        internal.mkdir()
        script = "甲在车站等车。"
        (internal / "剧本.md").write_text(script + "\n", encoding="utf-8")
        spec = {
            "aspect_ratio": "9:16",
            "script": script,
            "assets": [{"id": "A1", "title": "甲", "prompt": "车站里的甲"}],
            "shots": [{"id": "S01", "duration": 4, "prompt": "她离开", "images": ["A1"]}],
        }
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        review_script(folder, spec, brief="")
        confirm_script(folder, brief="")
        review_delivery(folder, spec, brief="", force=True)
        return folder, spec

    def _download(self, _current, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"late-image-bytes")
        return ""

    def _run_image(self, folder):
        class Task(object):
            id = "img-1"
            status = "SUCCEEDED"

        ctx = RenderContext(folder, project_id="img-gap", sleeper=lambda _seconds: None)
        return ctx.run_remote(
            "A1",
            "image",
            "图",
            "images",
            lambda _key: Task(),
            lambda _task_id: Task(),
            self._download,
            0,
            1,
            1,
        )

    def test_first_image_without_video_lease_is_not_marked_done_after_prompt_change(self):
        folder, spec = self._folder()
        self.assertFalse(lease_still_current(folder, "image", "A1"))
        authorize_video_submit(folder, "S01", False, brief="")
        self.assertTrue(lease_still_current(folder, "video", "S01"))
        self.assertFalse(lease_still_current(folder, "image", "A1"))
        authorize_image_submit(folder, "A1", False, brief="")
        self.assertTrue(lease_still_current(folder, "image", "A1"))
        spec["assets"][0]["prompt"] = "改过的车站"
        spec["script"] = "甲决定留下。"
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        (folder / "_内部_" / "剧本.md").write_text("甲决定留下。\n", encoding="utf-8")
        with self.assertRaises(GenerationBlocked):
            self._run_image(folder)
        state = load_run_state(folder)
        self.assertNotEqual((state.get("steps") or {}).get("A1", {}).get("status"), "done")
        self.assertFalse((folder / "outputs" / "images" / "A1.png").is_file())
        archived = list((folder / "history").glob("*/outputs/images/A1.png"))
        self.assertTrue(archived)
        self.assertEqual(archived[0].read_bytes(), b"late-image-bytes")

    def test_image_download_with_only_a_video_lease_is_archived(self):
        folder, _spec = self._folder()
        authorize_video_submit(folder, "S01", False, brief="")
        with self.assertRaises(GenerationBlocked):
            self._run_image(folder)
        self.assertNotEqual(load_run_state(folder).get("steps", {}).get("A1", {}).get("status"), "done")
        self.assertTrue(list((folder / "history").glob("*/outputs/images/A1.png")))

    def _copy_side_effect(self, mode):
        import end.history_keep as history_keep

        real = history_keep.shutil.copy2

        def wrapped(src, dst, *args, **kwargs):
            if mode == "oserror":
                raise OSError("no space")
            real(src, dst, *args, **kwargs)
            if mode == "truncate":
                Path(dst).write_bytes(b"x")

        return patch.object(history_keep.shutil, "copy2", side_effect=wrapped)

    def _reject_late(self, kind, step_id, mode, expired):
        folder, spec = self._folder("%s-%s-%s" % (kind, "expired" if expired else "none", mode))
        if expired:
            if kind == "image":
                authorize_image_submit(folder, step_id, False, brief="")
            else:
                authorize_video_submit(folder, step_id, False, brief="")
            spec["script"] = "甲改口了。"
            (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            (folder / "_内部_" / "剧本.md").write_text("甲改口了。\n", encoding="utf-8")
        path, relative = output_paths(folder, step_id, kind)
        save_run_state(
            folder,
            {
                "steps": {
                    step_id: {
                        "kind": kind,
                        "status": "running",
                        "remote_task_id": "already-out",
                        "attempts": 1,
                        "epoch": 0,
                        "output_file": "",
                        "public_url": "",
                        "local_override": False,
                        "error": "",
                    }
                }
            },
        )
        submits = []

        class Task(object):
            id = "already-out"
            status = "SUCCEEDED" if kind == "image" else "completed"

        def submit(_key):
            submits.append(_key)
            return Task()

        def download(_task, dest):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"late-bytes")
            return ""

        def enter():
            return RenderContext(folder, project_id="held-" + step_id, sleeper=lambda _seconds: None).run_remote(
                step_id, kind, "迟到", kind, submit, lambda _task_id: Task(), download, 0, 1, 1
            )

        with self._copy_side_effect(mode):
            with self.assertRaises(GenerationBlocked) as first:
                enter()
        self.assertIn("归档没有成功", str(first.exception))
        self.assertNotIn("已归档并移出", str(first.exception))
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), b"late-bytes")
        held = load_run_state(folder)["steps"][step_id]
        self.assertTrue(held.get("unaccepted"))
        self.assertEqual(held.get("status"), "held")
        self.assertEqual(submits, [])

        with self._copy_side_effect(mode):
            with self.assertRaises(GenerationBlocked) as second:
                enter()
        self.assertIn("归档仍未成功", str(second.exception))
        self.assertEqual(submits, [])
        self.assertTrue(path.is_file())
        self.assertNotEqual(load_run_state(folder)["steps"][step_id]["status"], "done")
        ctx = RenderContext(folder, project_id="held-" + step_id, sleeper=lambda _seconds: None)
        with self.assertRaises(GenerationBlocked) as joined:
            ctx.run_join([path])
        self.assertIn("不能拼", str(joined.exception))
        self.assertNotEqual((load_run_state(folder).get("steps") or {}).get("join", {}).get("status"), "done")

        with self.assertRaises(GenerationBlocked) as cleared:
            enter()
        self.assertIn("重试归档已经成功", str(cleared.exception))
        self.assertFalse(path.is_file())
        self.assertEqual(submits, [])
        saved = load_run_state(folder)["steps"][step_id]
        self.assertTrue(saved.get("unaccepted"))
        self.assertNotEqual(saved.get("status"), "done")
        copies = [
            item
            for item in (folder / "history").rglob(path.name)
            if item.is_file() and item.read_bytes() == b"late-bytes"
        ]
        self.assertTrue(copies)

    def test_rejected_download_stays_unaccepted_across_a_new_render_context(self):
        for kind, step_id in (("image", "A1"), ("video", "S01")):
            for expired in (False, True):
                for mode in ("oserror", "truncate"):
                    self._reject_late(kind, step_id, mode, expired)

    def test_current_lease_still_reuses_the_finished_file(self):
        folder, _spec = self._folder()
        authorize_image_submit(folder, "A1", False, brief="")
        submits = []

        class Task(object):
            id = "img-ok"
            status = "SUCCEEDED"

        def submit(_key):
            submits.append("image")
            return Task()

        def download(_task, dest):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"fresh-image")
            return "http://local/a1"

        def enter():
            return RenderContext(folder, project_id="reuse-image", sleeper=lambda _seconds: None).run_remote(
                "A1", "image", "图", "images", submit, lambda _task_id: Task(), download, 0, 1, 1
            )

        first = enter()
        self.assertEqual(first["status"], "done")
        self.assertEqual(submits, ["image"])
        second = enter()
        self.assertEqual(second["status"], "done")
        self.assertEqual(submits, ["image"])
        self.assertFalse(second.get("unaccepted"))

        authorize_video_submit(folder, "S01", False, brief="")

        class Clip(object):
            id = "vid-ok"
            status = "completed"

        def submit_video(_key):
            submits.append("video")
            return Clip()

        def download_video(_task, dest):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"fresh-clip")
            return ""

        def enter_video():
            return RenderContext(folder, project_id="reuse-video", sleeper=lambda _seconds: None).run_remote(
                "S01", "video", "镜", "videos", submit_video, lambda _task_id: Clip(), download_video, 0, 1, 1
            )

        self.assertEqual(enter_video()["status"], "done")
        self.assertEqual(enter_video()["status"], "done")
        self.assertEqual(submits, ["image", "video"])


class RestoreSnapshotTest(_GapBase):
    def _plant(self, name="replay"):
        folder = self.root / name
        internal = folder / "_内部_"
        chain = internal / "剧本链"
        chain.mkdir(parents=True)
        (chain / "01-来源登记.md").write_text("旧来源\n", encoding="utf-8")
        (chain / "02-IP与角色.md").write_text("旧角色\n", encoding="utf-8")
        (chain / "05-正式剧本.md").write_text("旧正式剧本\n", encoding="utf-8")
        (internal / "剧本.md").write_text("完整剧本甲在车站。\n", encoding="utf-8")
        spec = {
            "script": "完整剧本甲在车站。",
            "image_prompts": "SPEC_ONLY_ASSET",
            "assets": [{"id": "A1", "title": "甲", "prompt": "只在spec里的提示词"}],
        }
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        self.assertFalse((internal / "assets.md").exists())
        record_dir = folder / "debug" / "trace"
        record_dir.mkdir(parents=True)
        (record_dir / "0001.json").write_text(
            json.dumps(
                {
                    "id": "0001",
                    "stage": "script",
                    "step": "1",
                    "label": "来源登记",
                    "pending": False,
                    "error": "",
                    "output": {"text": "新的来源登记"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return folder

    def test_restore_trace_keeps_spec_and_chain(self):
        folder = self._plant()
        plan = restore_trace(folder, "0001", {}, "")
        self.assertEqual(plan["stage"], "script")
        self.assertFalse((folder / "_内部_" / "剧本链" / "02-IP与角色.md").exists())
        self.assertFalse((folder / "_内部_" / "剧本链" / "05-正式剧本.md").exists())
        active = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        self.assertNotIn("image_prompts", active)
        specs = list((folder / "history").glob("*/spec.json"))
        roles = list((folder / "history").glob("*/_内部_/剧本链/02-IP与角色.md"))
        scripts = list((folder / "history").glob("*/_内部_/剧本链/05-正式剧本.md"))
        self.assertTrue(specs and roles and scripts)
        saved = json.loads(specs[0].read_text(encoding="utf-8"))
        self.assertEqual(saved["image_prompts"], "SPEC_ONLY_ASSET")
        self.assertEqual(saved["assets"][0]["prompt"], "只在spec里的提示词")
        self.assertEqual(roles[0].read_text(encoding="utf-8"), "旧角色\n")
        self.assertEqual(scripts[0].read_text(encoding="utf-8"), "旧正式剧本\n")

    def test_archive_failure_leaves_files(self):
        folder = self._plant()
        before_spec = (folder / "spec.json").read_text(encoding="utf-8")
        before_role = (folder / "_内部_" / "剧本链" / "02-IP与角色.md").read_text(encoding="utf-8")
        with patch("end.history_keep.snapshot_project", side_effect=ArchiveError("磁盘写失败")):
            with self.assertRaises(ValueError):
                restore_trace(folder, "0001", {}, "")
        self.assertEqual((folder / "spec.json").read_text(encoding="utf-8"), before_spec)
        self.assertEqual((folder / "_内部_" / "剧本链" / "02-IP与角色.md").read_text(encoding="utf-8"), before_role)
        self.assertTrue((folder / "_内部_" / "剧本链" / "05-正式剧本.md").is_file())

    def test_clear_downstream_archives_spec_only_prompts(self):
        folder = self.root / "clear"
        folder.mkdir()
        spec = {"script": "甲。", "image_prompts": "SPEC_ONLY_ASSET", "assets": [{"id": "A9", "prompt": "只在spec"}]}
        (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        clear_downstream(folder, "script")
        active = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
        self.assertNotIn("image_prompts", active)
        saved = json.loads(next((folder / "history").glob("*/spec.json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["image_prompts"], "SPEC_ONLY_ASSET")
        before = (folder / "spec.json").read_text(encoding="utf-8")
        with patch("end.history_keep.snapshot_project", side_effect=ArchiveError("磁盘写失败")):
            with self.assertRaises(ValueError):
                clear_downstream(folder, "script")
        self.assertEqual((folder / "spec.json").read_text(encoding="utf-8"), before)

    def _copy_side_effect(self, mode):
        import end.history_keep as history_keep

        real = history_keep.shutil.copy2

        def wrapped(src, dst, *args, **kwargs):
            if mode == "oserror":
                raise OSError("no space")
            real(src, dst, *args, **kwargs)
            if mode == "truncate":
                Path(dst).write_bytes(b"x")
            elif mode == "mutate":
                Path(src).write_bytes(Path(src).read_bytes() + b"\nCHANGED")

        return patch.object(history_keep.shutil, "copy2", side_effect=wrapped)

    def test_copy_failures_leave_restore_clear_invalidate_and_film(self):
        for mode in ("truncate", "oserror", "mutate"):
            folder = self._plant("replay-" + mode)
            (folder / "name").write_text(mode, encoding="utf-8")
            spec_text = (folder / "spec.json").read_text(encoding="utf-8")
            role_text = (folder / "_内部_" / "剧本链" / "02-IP与角色.md").read_text(encoding="utf-8")
            script_text = (folder / "_内部_" / "剧本链" / "05-正式剧本.md").read_text(encoding="utf-8")
            with self._copy_side_effect(mode):
                with self.assertRaises(ValueError):
                    restore_trace(folder, "0001", {}, "")
            self.assertTrue((folder / "spec.json").is_file())
            self.assertTrue((folder / "_内部_" / "剧本链" / "02-IP与角色.md").is_file())
            self.assertTrue((folder / "_内部_" / "剧本链" / "05-正式剧本.md").is_file())
            self.assertIn("SPEC_ONLY_ASSET", (folder / "spec.json").read_text(encoding="utf-8"))
            if mode != "mutate":
                self.assertEqual((folder / "spec.json").read_text(encoding="utf-8"), spec_text)
                self.assertEqual((folder / "_内部_" / "剧本链" / "02-IP与角色.md").read_text(encoding="utf-8"), role_text)
                self.assertEqual((folder / "_内部_" / "剧本链" / "05-正式剧本.md").read_text(encoding="utf-8"), script_text)

            clear_dir = self.root / ("clear-" + mode)
            clear_dir.mkdir()
            spec = {"script": "甲。", "image_prompts": "SPEC_ONLY_ASSET", "assets": [{"id": "A9"}]}
            (clear_dir / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            (clear_dir / "_内部_").mkdir()
            (clear_dir / "_内部_" / "assets.md").write_text("资产稿\n", encoding="utf-8")
            before_spec = (clear_dir / "spec.json").read_text(encoding="utf-8")
            before_asset = (clear_dir / "_内部_" / "assets.md").read_text(encoding="utf-8")
            with self._copy_side_effect(mode):
                with self.assertRaises(ValueError):
                    clear_downstream(clear_dir, "script")
            self.assertTrue((clear_dir / "_内部_" / "assets.md").is_file())
            self.assertIn("SPEC_ONLY_ASSET", (clear_dir / "spec.json").read_text(encoding="utf-8"))
            if mode != "mutate":
                self.assertEqual((clear_dir / "spec.json").read_text(encoding="utf-8"), before_spec)
                self.assertEqual((clear_dir / "_内部_" / "assets.md").read_text(encoding="utf-8"), before_asset)

            media = self.root / ("media-" + mode)
            image = media / "outputs" / "images" / "A1.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png-bytes")
            save_run_state(media, {"steps": {"A1": {"kind": "image", "status": "done", "output_file": "images/A1.png"}}})
            with self._copy_side_effect(mode):
                invalidate_steps(media, "media", [("A1", "image")])
            self.assertTrue(image.is_file())
            self.assertEqual(load_run_state(media)["steps"]["A1"]["status"], "done")
            if mode != "mutate":
                self.assertEqual(image.read_bytes(), b"png-bytes")

            film_dir = self.root / ("film-" + mode)
            film = film_dir / "outputs" / "joined.mp4"
            film.parent.mkdir(parents=True)
            film.write_bytes(b"film-bytes")
            project = {"video_file": "joined.mp4"}
            with self._copy_side_effect(mode):
                _drop_film(film_dir, project)
            self.assertTrue(film.is_file())
            self.assertEqual(project["video_file"], "joined.mp4")
            if mode != "mutate":
                self.assertEqual(film.read_bytes(), b"film-bytes")


if __name__ == "__main__":
    unittest.main()
