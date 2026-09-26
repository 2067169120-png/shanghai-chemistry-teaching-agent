"""Teacher-editable teaching structure; uses the existing saved delivery field."""

from PySide6.QtWidgets import QPlainTextEdit


class PreparationDesignEditor(QPlainTextEdit):
    """Retain the previous form's text/setText API for draft/import callers."""

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:
        self.setPlainText(text)
