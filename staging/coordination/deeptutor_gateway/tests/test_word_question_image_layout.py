"""Source pictures must fit after the actual reader gains a scrollbar or resizes."""
from copy import deepcopy

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog
from test_word_question_dialog import _Facade, _Tasks, _isolated_qt_app


def _wide_source():
    image = QImage(1100, 290, QImage.Format.Format_RGB32)
    image.fill("white")
    image.setPixelColor(0, 0, "black")
    image.setPixelColor(1099, 289, "black")
    data = QByteArray()
    buffer = QBuffer(data)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _settle(app):
    for _ in range(5):
        app.processEvents()
        QTest.qWait(20)


@pytest.mark.parametrize("change", ["reader_scrollbar", "splitter_width"])
def test_source_image_fits_reader_when_only_its_container_changes(change):
    with _isolated_qt_app() as app:
        facade, tasks = _Facade(), _Tasks()
        source = _wide_source()
        facade.word_question_image = lambda *_args: {"bytes": source, "mime_type": "image/png"}
        item = facade.catalog["items"][0]
        item["question_blocks"][0]["warnings"] = ["图后文字，保留原范围"]
        before = deepcopy(item)
        dialog = WordQuestionDialog(facade, tasks)
        dialog.resize(1400, 980)
        dialog.show()
        tasks.flush()
        _settle(app)
        image, original = dialog._image_targets[0]
        outer_size = dialog.size()
        if change == "reader_scrollbar":
            image.parentWidget().layout().addWidget(QLabel("长后续材料\n" * 100))
        else:
            dialog.splitter.setSizes([700, 450])
        _settle(app)
        assert dialog.size() == outer_size
        assert image.pixmap().width() <= image.contentsRect().width()
        assert image.pixmap().height() <= image.contentsRect().height()
        widgets = [dialog._panel_layouts[0].itemAt(i).widget()
                   for i in range(dialog._panel_layouts[0].count())]
        assert all(not image.geometry().intersects(w.geometry())
                   for w in widgets if w is not None and w is not image)
        assert original.width() == 1100 and original.height() == 290
        assert facade.catalog["items"][0] == before
        dialog.reject()
