from integrations.deeptutor_shchem_v1.theme_workbench import _shared_materials


def test_explicit_descriptor_page_wins_over_filename_digits():
    record = {
        "evidence_descriptors": [
            {
                "crop_id": "paper-p99-context",
                "evidence_role": "shared_material",
                "source_page": 3,
            }
        ]
    }
    materials = _shared_materials(
        record,
        crop_index={"paper-p99-context": {"path": "evidence/paper-p99-context.png"}},
        preview_allowed=True,
    )
    assert [item["page"] for item in materials] == [3]


def test_absent_page_is_not_inferred_from_crop_id_or_path():
    record = {
        "evidence_descriptors": [
            {"crop_id": "paper-p03-context", "evidence_role": "shared_material"}
        ]
    }
    materials = _shared_materials(
        record,
        crop_index={"paper-p03-context": {"path": "evidence/paper-p03-context.png"}},
        preview_allowed=True,
    )
    assert [item["page"] for item in materials] == [None]
