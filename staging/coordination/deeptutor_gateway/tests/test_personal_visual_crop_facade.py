"""Crop facade delegates exact arguments without constructing local services."""

from unittest.mock import Mock, sentinel

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade


@pytest.fixture
def facade():
    result = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    result._personal_visual_question_call = Mock(return_value=sentinel.result)
    return result


def test_crop_options_delegates_exact_image_binding(facade):
    assert (
        facade.personal_visual_question_crop_options(
            "batch", "key", "content-revision", "image"
        )
        is sentinel.result
    )
    facade._personal_visual_question_call.assert_called_once_with(
        "crop_options", "batch", "key", "content-revision", "image"
    )


def test_preview_crop_delegates_bbox_and_independent_crop_revision(facade):
    bbox = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    assert (
        facade.personal_visual_question_preview_crop(
            "batch",
            "key",
            "content-revision",
            "image",
            bbox,
            expected_crop_revision="crop-revision",
        )
        is sentinel.result
    )
    facade._personal_visual_question_call.assert_called_once_with(
        "preview_crop",
        "batch",
        "key",
        "content-revision",
        "image",
        bbox,
        expected_crop_revision="crop-revision",
    )
    assert facade._personal_visual_question_call.call_args.args[-1] is bbox


def test_save_crop_defaults_to_unconfirmed_teacher_origin(facade):
    assert (
        facade.personal_visual_question_save_crop("preview", "preview-revision")
        is sentinel.result
    )
    facade._personal_visual_question_call.assert_called_once_with(
        "save_crop",
        "preview",
        "preview-revision",
        confirmed=False,
        edit_origin="teacher",
    )


@pytest.mark.parametrize("origin", ["teacher", "ai_source_review"])
def test_save_crop_forwards_explicit_confirmation_and_origin(facade, origin):
    assert (
        facade.personal_visual_question_save_crop(
            "preview", "preview-revision", confirmed=True, edit_origin=origin
        )
        is sentinel.result
    )
    facade._personal_visual_question_call.assert_called_once_with(
        "save_crop", "preview", "preview-revision", confirmed=True, edit_origin=origin
    )


def test_discard_crop_delegates_only_preview_identifier(facade):
    assert facade.personal_visual_question_discard_crop("preview") is sentinel.result
    facade._personal_visual_question_call.assert_called_once_with(
        "discard_crop", "preview"
    )
