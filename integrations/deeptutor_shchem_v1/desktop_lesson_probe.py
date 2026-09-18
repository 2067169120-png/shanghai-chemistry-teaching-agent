"""Isolated lesson-design UI and real-file acceptance; no provider calls."""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time


def sample_payload():
    from .desktop_lesson_design import new_design, new_node
    p = {"output_kind": "joint", "topic": "动态平衡的证据与表达",
         "audience": "高二 · 合成软件验收", "lesson_route": "复习", "lesson_timing": "1课时×40分钟",
         "objective": "根据速率证据判断动态平衡\n写出A⇌B的平衡常数表达式",
         "materials": "合成反应A⇌B，温度固定。正逆反应速率用相对单位表示。\n本材料只用于软件流程验收。",
         "advanced": dict.fromkeys(("learning_and_experiment", "template_and_delivery", "homework_and_strategy"), "")}
    d = new_design(p); n = new_node("观察与提问")
    n.update(title="根据速率判断平衡", minutes=12,
             objective_ids=[d["objectives"][0]["id"]], confirmed=True,
             teacher_action="展示材料，先收集判断，再追问证据。",
             student_task="比较正逆反应速率，并说明达到动态平衡的依据。",
             expected_output="结论与对应的速率证据。",
             criteria="正逆速率相等且反应仍在进行。",
             material_text=p["materials"], student_material=True,
             teacher_answer="教师核对：v正=v逆且不为零。", notes="比较不同理由，不只核对结论。")
    d["nodes"].append(n); p["lesson_design"] = d
    return p


def run_probe(output):
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont
    from PySide6.QtCore import QTimer, QRect, Qt
    from PySide6.QtWidgets import QApplication, QFileDialog
    from .desktop_facade import build_default_facade
    from .desktop_paths import DesktopPaths
    from .desktop_version import DESKTOP_VERSION
    from .desktop_preparation_drafts import PreparationDraftService
    from .desktop_editor_recovery import PreparationRecoveryStore
    from .desktop_lesson_design import coverage, content_fingerprint, update_node
    from .desktop_lesson_output import checked_file, actual_ppt_preview, FILES
    from .desktop_workbench.app import create_application
    from .desktop_workbench.main_window import TeacherWorkbenchWindow
    from .desktop_workbench.lesson_design_dialog import LessonDesignDialog, ActualPptPreview
    from .desktop_workbench.preparation_recovery import apply_editor_payload

    app = create_application(["lesson-design-acceptance"])
    target = Path(output); target.mkdir(parents=True, exist_ok=True)
    shots, errors = [], []
    oldhook = sys.excepthook
    sys.excepthook = lambda typ, exc, tb: errors.append(typ.__name__ + ": " + str(exc))
    def settle(test=lambda: True):
        end = time.monotonic() + 90
        for _ in range(5): app.processEvents(); time.sleep(.025)
        while not test() and time.monotonic() < end:
            app.processEvents(); time.sleep(.025)
        assert test(), "Lesson design operation did not finish"
        assert not errors, errors
    def capture(widget, name):
        file = target / name
        assert widget.grab().save(str(file))
        shots.append({"file": name, "sha256": sha256(file.read_bytes()).hexdigest()})

    with tempfile.TemporaryDirectory(prefix="lesson100-") as temp:
        temp = Path(temp)
        workspace = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        facade = build_default_facade(DesktopPaths.from_workspace(workspace, state_root=temp/"state"))
        win = TeacherWorkbenchWindow(facade); win.resize(1366, 820); win.show()
        win.navigate("preparation")
        page = win.preparation_page
        p = sample_payload()
        image = Image.new("RGB", (960, 460), "white")
        draw = ImageDraw.Draw(image)
        draw.line((80, 380, 860, 380), fill="black", width=3)
        draw.line((80, 380, 80, 40), fill="black", width=3)
        draw.line([(80,80),(250,160),(450,220),(850,220)], fill="black", width=4)
        draw.line([(80,360),(250,285),(450,220),(850,220)], fill="black", width=4)
        try:
            font = ImageFont.truetype("arial.ttf", 22)
        except OSError:
            font = ImageFont.load_default(size=22)
        draw.text((470,185), "v(forward) = v(reverse) > 0", font=font, fill="black")
        draw.text((540,410), "Synthetic model / time", font=font, fill="black")
        draw.text((86,12), "Relative rate", font=font, fill="black")
        raw=BytesIO(); image.save(raw,format="PNG")
        file=temp/"rate-model.png"; file.write_bytes(raw.getvalue())
        asset=facade.import_preparation_image(str(file),"速率比较（合成示意）","软件测试人工绘制","观察相对速率")
        p["image_assets"]=[asset]
        p["lesson_design"]["nodes"][0]["image_ids"]=[asset["asset_id"]]
        apply_editor_payload(page,p)
        evidence = {}
        def drive():
            d=next((w for w in QApplication.topLevelWidgets() if isinstance(w,LessonDesignDialog) and w.isVisible()),None)
            try:
                assert d is not None
                settle()
                assert [g["status"] for g in coverage(d.history.value)["objectives"]]==["已明确关联","未安排"]
                d.tabs.setCurrentIndex(2); settle(); capture(d,"lesson-goal-gap.png")
                d.tabs.setCurrentIndex(0); d.add_node()
                d.title_edit.setText("独立写出表达式")
                d.change({"title":"独立写出表达式"})
                d.fields["teacher_action"].setPlainText("先独立书写，再请学生说明分子与分母。")
                d.fields["student_task"].setPlainText("对合成反应A⇌B，以c(A)、c(B)表示平衡浓度，写出Kc表达式。")
                d.minutes.setValue(15)
                d.goal_links.item(1).setCheckState(Qt.CheckState.Checked)
                d.fields["expected_output"].setPlainText("表达式及各符号的含义。")
                d.fields["criteria"].setPlainText("浓度幂次与反应计量关系一致。")
                d.fields["teacher_answer"].setPlainText("教师答案：Kc=c(B)/c(A)。")
                d.confirmed.setChecked(True)
                assert coverage(d.history.value)["objectives"][1]["status"]=="已明确关联"
                before=deepcopy(d.history.value["nodes"][-1])
                d.locked.setChecked(True)
                try:
                    update_node(d.history.value,d.current,{"teacher_answer":"unwanted replacement","locked":False})
                except ValueError: pass
                else: raise AssertionError("Locked answer changed")
                d.fields["teacher_action"].setPlainText("仅改教师讲解，不改题目和答案。")
                assert d.history.value["nodes"][-1]["teacher_answer"]==before["teacher_answer"]
                assert not d.history.value["nodes"][-1]["confirmed"]
                d.confirmed.setChecked(True)
                original_order=[n["id"] for n in d.history.value["nodes"]]
                d.move_node(-1); d.travel(False)
                assert [n["id"] for n in d.history.value["nodes"]]==original_order
                d.properties.setCurrentIndex(1);settle();capture(d,"lesson-design-editor.png")
                for width,height in ((1366,768),(800,700)):
                    d.resize(width,height);settle()
                    for w in (d.tabs,d.save,d.generate,d.preview,d.return_button):
                        assert d.rect().contains(QRect(w.mapTo(d,w.rect().topLeft()),w.size()))
                    assert d.tabs.height()>=400
                capture(d,"lesson-design-compact.png")
                d.generate.click();settle(lambda:not d.busy)
                assert len(d.history.value["exports"])==1,d.status.text()
                first=deepcopy(d.history.value["exports"][0])
                d.tabs.setCurrentIndex(0);d.fields["student_task"].setPlainText(
                    "仍以合成反应A⇌B为背景，写出Kc表达式，并标明式中浓度对应平衡状态。")
                assert "需更新" in d.report.toPlainText()
                assert checked_file(facade,d.history.value,first["id"],FILES[0]).is_file()
                d.generate.click();settle(lambda:not d.busy)
                assert len(d.history.value["exports"])==2,d.status.text()
                latest = d.history.value["exports"][-1]
                assert d.outputs.currentData() == latest["id"], "Newly generated version was not selected"
                assert "一致" in d.selected_output_status.text()
                d.outputs.setCurrentIndex(d.outputs.findData(first["id"]))
                assert "历史内容" in d.selected_output_status.text()
                d.outputs.setCurrentIndex(d.outputs.findData(latest["id"]))
                destination = temp / "teacher-export"
                destination.mkdir()
                original_chooser = QFileDialog.getExistingDirectory
                QFileDialog.getExistingDirectory = lambda *args, **kw: str(destination)
                try:
                    d.export_copy.click();settle(lambda:not d.busy)
                finally:
                    QFileDialog.getExistingDirectory = original_chooser
                copies = list(destination.iterdir())
                assert len(copies) == 1 and copies[0].is_dir(), d.status.text()
                for name in FILES:
                    assert (copies[0] / name).read_bytes() == checked_file(facade, d.history.value, latest["id"], name).read_bytes()
                evidence["copy_export"] = True
                d.save.click();settle(lambda:not d.busy)
                assert "已保存" in d.status.text(),d.status.text()
                capture(d,"lesson-linked-outputs.png")
                evidence["first"]=first
                evidence["plan"]=deepcopy(d.history.value)
            except Exception as exc:
                errors.append(type(exc).__name__+": "+str(exc))
                if d: capture(d,"lesson-design-failure.png")
            finally:
                if d:
                    d.busy=False;d.reject()
        try:
            QTimer.singleShot(200,drive)
            page.lesson_design_button.click()
            assert not errors,errors
            service=PreparationDraftService(facade.state_store)
            option=service.search()["items"][0]
            loaded=service.load(option["draft_id"],option["revision"])["payload"]
            assert loaded["lesson_design"]==evidence["plan"]
            assert PreparationRecoveryStore(facade.paths.state_root).load()["payload"]["lesson_design"]==evidence["plan"]
            # Recreate the editor from a saved original preparation draft.
            page._lesson_design=None
            apply_editor_payload(page,loaded)
            again=LessonDesignDialog(page);again.show();settle()
            assert len(again.history.value["nodes"])==2 and len(again.history.value["exports"])==2
            again.reject();again.deleteLater()
            latest=evidence["plan"]["exports"][-1]
            for name in FILES:
                shutil.copyfile(checked_file(facade,evidence["plan"],latest["id"],name),target/name)
            actual=actual_ppt_preview(facade,evidence["plan"],latest["id"])
            shutil.copyfile(actual["path"],target/"actual-pptx.pdf")
            pv=ActualPptPreview(actual,win);pv.show();settle();capture(pv,"lesson-actual-pptx.png")
            assert pv.count==3,pv.count
            pv.next.click();settle();pv.close();pv.deleteLater()
            from .desktop_local_pagination import render_docx
            for name in ("lesson_plan","student_worksheet"):
                _,pdf,_=render_docx(target/(name+".docx"),target/(name+"-pages"))
                shutil.copyfile(pdf,target/(name+".pdf"))
            assert file.read_bytes()==raw.getvalue()
            (target/"lesson-design.json").write_text(json.dumps(evidence["plan"],ensure_ascii=False,indent=2),encoding="utf-8")
            report={"version":DESKTOP_VERSION,"source_commit":os.environ.get("GITHUB_SHA","local"),
                "frozen":getattr(sys,"frozen",False),"qt_platform":app.platformName(),"model_calls":0,
                "checks":{"native_entry":True,"uncovered_goal_detected":True,"explicit_evaluation_link":True,
                    "locked_answer_preserved":True,"undo_identity":True,"old_output_retained":True,
                    "three_real_outputs":True,"stale_output_marked":True,"formal_draft_reopen":True,
                    "original_recovery_retains_design":True,"actions_visible":True,"source_image_unchanged":True,
                    "actual_pptx_pdf":True,"new_output_selected":True,"independent_export_copy":evidence["copy_export"]},
                "slides":3,"screenshots":shots,"uncaught_errors":errors,
                "scope":"Synthetic teacher-confirmed nodes; local deterministic export; LibreOffice rendering, not PowerPoint parity or AI quality"}
            (target/"lesson-design-probe.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        finally:
            win.close();app.processEvents();sys.excepthook=oldhook
            if errors:
                (target/"errors.json").write_text(json.dumps(errors,ensure_ascii=False),encoding="utf-8")
    return 0
