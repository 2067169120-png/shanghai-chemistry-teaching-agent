"""Use the existing frozen mixed-paper pagination pipeline for reviewed edits."""
from .desktop_mixed_paper_service import MixedPaperService, MixedPaperError
from .desktop_raster_numbers import image_catalog, apply_to_documents


class RasterPaperService(MixedPaperService):
    def inspect_images(self, preview_id, preview_hash):
        folder, _, snapshot = self._load(preview_id, require_current=True)
        if snapshot['preview_hash'] != preview_hash or snapshot.get('pagination_binding'):
            raise MixedPaperError('预览已改变，请按当前题序重新准备题图。')
        if snapshot['preview_model']['blockers']:
            raise MixedPaperError('题目材料尚不完整，请先核对来源。')
        from functools import partial
        from .desktop_mixed_paper_export import build_mixed_paper_docx
        from .desktop_number_regions import suggest_number
        hints = {}
        original_builder = self._docx_builder
        if original_builder is None:
            self._docx_builder = partial(build_mixed_paper_docx, image_number_hints=hints)
        try:
            documents = super()._build_docx(preview_id, snapshot, folder)
        finally:
            self._docx_builder = original_builder
        catalog = image_catalog(documents)
        for image in catalog['images']:
            image['suggested_number'] = suggest_number(image, hints)
        return catalog

    def prepare_corrected(self, preview_id, preview_hash, edits):
        self._reviewed_number_edits = edits
        try:
            return super().prepare_pagination(preview_id, preview_hash)
        finally:
            self._reviewed_number_edits = []

    def _build_docx(self, preview_id, snapshot, folder):
        original = super()._build_docx(preview_id, snapshot, folder)
        edits = getattr(self, '_reviewed_number_edits', [])
        result = apply_to_documents(original, edits)
        if edits:
            from copy import deepcopy
            snapshot['preview_model']['scan_numbering'] = {
                'edits': deepcopy(edits), 'scope': 'reviewed_export_copies_only',
                'requires_review_again_after_reordering': True,
            }
        return result
