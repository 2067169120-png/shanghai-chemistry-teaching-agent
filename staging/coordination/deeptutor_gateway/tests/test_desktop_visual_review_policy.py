"""Public visual-import review policy only; no provider, key, source file or network."""

from dataclasses import replace

import pytest
from test_desktop_visual_egress import _PreviewFacade

from integrations.deeptutor_shchem_v1 import desktop_visual_schema as policy
from integrations.deeptutor_shchem_v1.desktop_visual_egress import (
    DesktopVisualEgressService,
    VisualEgressError,
)


@pytest.mark.parametrize("url,model,style", [
    ("https://example.invalid/v1", "synthetic-vision", "responses"),
    ("https://example.invalid/v1", "synthetic-vision", "chat_completions"),
    ("https://api.deepseek.com", "deepseek-v4-flash-vision-exp", "responses"),
])
def test_review_is_required_by_default_on_every_import_route(url, model, style):
    result = policy.visual_import_request_policy(url, model, style)
    assert result["observation_prompt_version"] == "normalized-xywh-roles-pages-v5"
    assert result["crop_review_version"] == policy.VISUAL_CROP_REVIEW_VERSION == "source-page-actual-crops-v1"
    assert result["crop_review_batch_limit"] == policy.VISUAL_CROP_REVIEW_BATCH_LIMIT == 8
    assert result["crop_review_required"] is True
    result["crop_review_required"] = False
    result["crop_review_batch_limit"] = 100
    assert policy.visual_import_request_policy(url, model, style)["crop_review_required"] is True
    assert policy.visual_import_request_policy(url, model, style)["crop_review_batch_limit"] == 8


def test_approval_without_review_policy_cannot_authorize_a_new_review_run():
    facade = _PreviewFacade()
    service = DesktopVisualEgressService(facade)
    args = {"batch_id": facade.descriptor["batch_id"], "profile_id": "synthetic-profile",
            "expected_profile_revision": "synthetic-revision"}
    plan = service.preview(**args)
    current = service._snapshots[plan["preview_id"]]
    previous_policy = {key: value for key, value in current.request_policy.items()
                       if not key.startswith("crop_review_")}
    previous_policy["observation_prompt_version"] = "normalized-xywh-roles-pages-v4"
    service._snapshots[plan["preview_id"]] = replace(current, request_policy=previous_policy)
    with pytest.raises(VisualEgressError, match="处理规则已变化"):
        service.frozen_renderer(**args, preview_id=plan["preview_id"], revision=plan["revision"],
                                sources=(facade.source,))
    assert facade.provider_borrows == 0
    # Source-page preview remains available even though its old approval cannot
    # authorize newly expanded sending rules.
    assert service.image(plan["preview_id"], plan["revision"], plan["pages"][0]["page_id"]) == facade.renderer.raw
