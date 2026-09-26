from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QDialog
from test_desktop_blueprint_drafts import _state
from test_desktop_ui import _Facade, _fill_preparation_page

from integrations.deeptutor_shchem_v1.desktop_blueprint_preparation import (
    BlueprintPreparationService,
)
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    normalize_preparation_payload,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
from integrations.deeptutor_shchem_v1.desktop_workbench.blueprint_preparation_dialog import (
    BlueprintPreparationDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def facade(tmp_path):
    service = BlueprintPreparationService(_state(tmp_path / "state"))
    return SimpleNamespace(
        preparation_blueprint_options=service.options,
        preparation_blueprint_reference=service.reference,
        service=service,
    )


def test_dialog_previews_exact_text_and_is_offline(app, facade):
    before = facade.service.state.path.read_bytes()
    dialog = BlueprintPreparationDialog(facade)
    dialog.resize(420, 700)
    dialog.show()
    app.processEvents()
    assert dialog.import_button.isEnabled()
    assert "E2" in dialog.preview.toPlainText()
    assert dialog.preview.toPlainText() == dialog.reference["materials"]
    assert dialog.preview.horizontalScrollBar().maximum() == 0
    dialog.import_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert facade.service.state.path.read_bytes() == before
    dialog.close()


def test_stale_source_between_preview_and_accept_is_refused(app, facade):
    dialog = BlueprintPreparationDialog(facade)
    state = facade.service.state
    record = state.snapshot()["drafts"]["BLUEPRINT-PREVIEW-1"]
    record["result"]["candidate"]["theme_center"] = "changed"
    state.save_draft("BLUEPRINT-PREVIEW-1", record)
    dialog.import_button.click()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.reference is None
    assert not dialog.import_button.isEnabled()
    dialog.close()


def test_evidence_change_requires_preview_confirmation_again(app, facade):
    dialog = BlueprintPreparationDialog(facade)
    state = facade.service.state
    record = state.snapshot()["drafts"]["BLUEPRINT-PREVIEW-1"]
    record["preview"]["evidence"][0]["supports"] = ["new evidence"]
    state.save_draft("BLUEPRINT-PREVIEW-1", record)
    dialog.import_button.click()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "new evidence" in dialog.preview.toPlainText()
    assert "再次确认" in dialog.status.text()
    dialog.import_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_empty_and_error_are_non_destructive(app):
    dialog = BlueprintPreparationDialog(
        SimpleNamespace(preparation_blueprint_options=list)
    )
    assert not dialog.import_button.isEnabled()
    assert "暂无" in dialog.status.text()
    dialog.close()

    def failed():
        raise RuntimeError("raw-private-diagnostic")

    dialog = BlueprintPreparationDialog(
        SimpleNamespace(preparation_blueprint_options=failed)
    )
    assert not dialog.import_button.isEnabled()
    assert "raw-private" not in dialog.status.text()
    dialog.close()


@pytest.mark.parametrize("accept", [False, True])
def test_page_import_preserves_existing_fields_and_flows_into_prompt(
    app, facade, monkeypatch, accept
):
    import integrations.deeptutor_shchem_v1.desktop_workbench.blueprint_preparation_dialog as module

    option = facade.preparation_blueprint_options()[0]
    reference = facade.preparation_blueprint_reference(
        option["preview_id"], option["source_id"], option["source_revision"]
    )

    class Choice:
        DialogCode = QDialog.DialogCode

        def __init__(self, *_args):
            self.reference = deepcopy(reference)

        def exec(self):
            return self.DialogCode.Accepted if accept else self.DialogCode.Rejected

    monkeypatch.setattr(module, "BlueprintPreparationDialog", Choice)
    fixture = _Facade()
    bridge = DesktopTaskBridge()
    page = PreparationPage(fixture, bridge)
    page._availability_timer.stop()
    _fill_preparation_page(page)
    before = page._payload()
    page.blueprint_import_button.click()
    after = page._payload()
    for key in before:
        if key != "materials":
            assert before[key] == after[key]
    if accept:
        assert (
            after["materials"] == before["materials"] + "\n\n" + reference["materials"]
        )
        prompt = _prompt(normalize_preparation_payload(after))
        assert reference["source_revision"] in prompt
        assert "E2" in prompt and "uncertainties" in prompt
        page.blueprint_import_button.click()
        assert page._payload() == after
        assert "重复" in page.status.text()
    else:
        assert after == before
    assert fixture.saved_preparation_payloads == []
    page.close()
    bridge.shutdown()
