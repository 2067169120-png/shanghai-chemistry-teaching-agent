"""Exact source-crop bindings for Datong H1 midterm themes 2-5.

This module contains provenance bindings only. It does not contain answers or
infer relationships from displayed question numbers or filenames. The values
were generated from the read-only inventory JSON and preserve every archived
question/shared-material evidence row for the 41 remaining atomic units.
"""

from __future__ import annotations

from typing import Final

CropBinding = tuple[str, str]

# Source page 5 prints three subquestions, with two blanks inside subquestion 3.
# Keep this display identity instead of inventing a fourth printed subquestion.
SOURCE_PART_LABELS: Final[dict[str, str]] = {
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P01": "（1）",
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P02": "（2）",
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P03-S01": "（3）第一空",
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P03-S02": "（3）第二空",
}

# Original pages 1-6 were visually checked: these questions already contain
# blanks/options, or ask pupils to annotate the printed equation/diagram.
# This is an explicit inventory, not an assumption about other imported pages.
FIRST_THEME_ANSWER_AREA_IDS = frozenset(
    "W1-DT2025-H1-MID-AP-DT2025-H1-" + suffix
    for suffix in (
        "Q01-P01",
        "Q02-P01",
        "Q03-P01-S01",
        "Q03-P01-S02",
        "Q04-P01-S01",
        "Q04-P01-S02",
        "Q04-P01-S03",
        "Q05-P01-S01",
        "Q05-P01-S02",
        "Q06-P01-S01",
        "Q06-P01-S02",
        "Q06-P01-S03",
        "Q07-P01",
        "Q08-P01",
        "Q09-P01",
    )
)

QUESTION_BINDINGS: Final[dict[str, tuple[CropBinding, ...]]] = {
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q10-P01": (
        (
            "DT2025-H1-Q10-E1",
            "ac834f6abcc1448448651ef19c93d0d82b36df79850820e1ed125b14692db2d5",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q11-P01": (
        (
            "DT2025-H1-Q11-E1",
            "a7b3252d14b34ea64bd8715d35c3a78ceda65be6a0a54daf957c033ad97c2971",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q12-P01": (
        (
            "DT2025-H1-Q12-E1",
            "bc6238c59b12048547fa44f49b8023752d34cc6f7aba0d6dc81a0dae09a41e43",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q13-P01-S01": (
        (
            "DT2025-H1-Q13-E1",
            "7b3aff0cd01940432b9146c28348362940e6af85e6737384ad3cb75bdadae79b",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q13-P01-S02": (
        (
            "DT2025-H1-Q13-E1",
            "7b3aff0cd01940432b9146c28348362940e6af85e6737384ad3cb75bdadae79b",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q14-P01": (
        (
            "DT2025-H1-Q14-E1",
            "8bb87ff8cdd9c33d9af179fe82221f8992cca5595270870523a6b199751db1f6",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q15-P01": (
        (
            "DT2025-H1-Q15-E1",
            "9bc4345f76996b708c380ee32017cf85353e006b4f7dfc86d0785fe02be95dfc",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q16-P01": (
        (
            "DT2025-H1-Q16-E1",
            "b443042d4c8ed04101218d0d9cfcbb12ff03fb7f3c4c04dca0e0d8f45c7ce0ac",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q17-P01-S01": (
        (
            "DT2025-H1-Q17-E1",
            "1a3dd41c5b032d96d3aae4ae87b29242eb0148734cf78ab8edde6ccb0b7a6a89",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q17-P01-S02": (
        (
            "DT2025-H1-Q17-E1",
            "1a3dd41c5b032d96d3aae4ae87b29242eb0148734cf78ab8edde6ccb0b7a6a89",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q18-P01": (
        (
            "DT2025-H1-Q18-E1",
            "33a6838ab60726e3d0e46254c4e4c1b5b989c91e9ff77eff0be118b340f66355",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q19-P01": (
        (
            "DT2025-H1-Q19-E1",
            "05003eafb3fc261b35790a075587704318a43746641729c4317089272c187a2e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q20-P01": (
        (
            "DT2025-H1-Q20-E1",
            "94880e5d265233d0a64b09718ebd455ed20d887da190096c2cd2599e98b56af1",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q21-P01": (
        (
            "DT2025-H1-Q21-E1",
            "d823dba5faa73bed6e40cbd31b29e1a877cdeeba88d0d602bde438f810e0a4a0",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q22-P01": (
        (
            "DT2025-H1-Q22-E1",
            "c18f4d0cde954c51658ce909cb37e675f58b3b110d3ab9051c85ae213cebfe72",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q23-P01-S01": (
        (
            "DT2025-H1-Q23-E1",
            "911019b87344b05f5417c1edadccb16e233a4ca75463663eac1ca6f87331082a",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q23-P01-S02": (
        (
            "DT2025-H1-Q23-E1",
            "911019b87344b05f5417c1edadccb16e233a4ca75463663eac1ca6f87331082a",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q24-P01-S01": (
        (
            "DT2025-H1-Q24-E1",
            "b3702f83b998ab5f1d210d29b9aa30b17b35ef755734233d3f3b65af1db96493",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q24-P01-S02": (
        (
            "DT2025-H1-Q24-E2",
            "9693e79141cd2565299a48ffc477708bef161f346e4b8e15661b6bf53a60a56b",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q25-P01": (
        (
            "DT2025-H1-Q25-E1",
            "739d238dc703b5a64b33a9acc6191b99dbd6e7f35ee7ca8d0c80e77e19bc8375",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q26-P01": (
        (
            "DT2025-H1-Q26-E1",
            "a03c0179074aaa5faaca8dfc37c4547bd744fd3265ab1a000573656e91ed5c80",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q27-P01": (
        (
            "DT2025-H1-Q27-E1",
            "151927fbb92c3198e02376fa2e04f8cec5f4eb301b13b6466671fb789549eb60",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q28-P01": (
        (
            "DT2025-H1-Q28-E1",
            "89d873e253910783245b9101b5952ab7e4ed99d3b657a91763a3c5b65a18a7db",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q29-P01": (
        (
            "DT2025-H1-Q29-E1",
            "8baf3b17db177dcf265ca34e751ec421d70a3277b4943eaf5c5a867bab1e461e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q30-P01": (
        (
            "DT2025-H1-Q30-E1",
            "88dcdc41f2cf2fbced0cdcaf51e1a75ea73603bb93a787ad480e7c4b6a72f641",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q31-P01": (
        (
            "DT2025-H1-Q31-E1",
            "b05b1138aed7e06640d6b8029cfe4d3e5ff46b2218ae049bff4072834bb28504",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q32-P01": (
        (
            "DT2025-H1-Q32-E1",
            "8df88fc31d4ccd824f8dc451f3af8ee85b85f81f60fc41c6393d8ad815097baa",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P01": (
        (
            "DT2025-H1-Q33-P01-E1",
            "34f8309d3a846d97281ac50fcc4e19fa63e067183f647e4275a2b64109571304",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P02": (
        (
            "DT2025-H1-Q33-P02-E1",
            "9863c0334b39588456f8bcac6fb7a93f873f95aed9e195c80f769ca3b3ceeee3",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P03-S01": (
        (
            "DT2025-H1-Q33-P03-E1",
            "2ed718a55136798eeaf2698c782006d95012a9909b4af423dfb1c61ed3a764e7",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P03-S02": (
        (
            "DT2025-H1-Q33-P03-E1",
            "2ed718a55136798eeaf2698c782006d95012a9909b4af423dfb1c61ed3a764e7",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q34-P01": (
        (
            "DT2025-H1-Q34-E1",
            "97892465d5599681edc3779ffd52785aac4794bd9eba782a84cd913391e6adcd",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q35-P01": (
        (
            "DT2025-H1-Q35-E1",
            "4d75d22d174d84a7290297d447caa516a7915e9a589006a56c29ec8d45c48e9e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q36-P01": (
        (
            "DT2025-H1-Q36-E1",
            "d503250970d287b2d6ba8b8f6abb2772fcab17dfb9722fbd6a6eab7e89274afb",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q37-P01": (
        (
            "DT2025-H1-Q37-E1",
            "9a963a7e7a64f58f1c56368c81caff5cf6135acebb2bd4adc03b118b11cb8705",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S01": (
        (
            "DT2025-H1-Q38-E1",
            "d0077356b14d6a9851122b213a795b0e68fdd479ca9f7826e92cbf5c19b6a842",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S02": (
        (
            "DT2025-H1-Q38-E1",
            "d0077356b14d6a9851122b213a795b0e68fdd479ca9f7826e92cbf5c19b6a842",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S03": (
        (
            "DT2025-H1-Q38-E2",
            "b123da41d17b596cdf5b97b1d294f142bbe6869ad9114191d30ddf912769e13a",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q39-P01": (
        (
            "DT2025-H1-Q39-E1",
            "cbefdfda3602afd1b5181d1b36182a2793041c4e78b01cbcd9fe641c221cb71e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q40-P01": (
        (
            "DT2025-H1-Q40-E1",
            "09d27cac367947396ca7720afd1df4a8a8b34379d7795d3792481f03a91a3b9c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q41-P01": (
        (
            "DT2025-H1-Q41-E1",
            "62ee232bedd6dda76172509c24d9dba923547050424f963510e8746a04fecfb9",
        ),
    ),
}


SHARED_CONTEXT_BINDINGS: Final[dict[str, tuple[CropBinding, ...]]] = {
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q10-P01": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q11-P01": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q12-P01": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
        (
            "DT2025-H1-SHARED-VIS_Q12_COLOR_FLOW",
            "819731ff6b126d86d8f6a0075145e1997bda6721222cb67c751844f06f36ec80",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q13-P01-S01": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q13-P01-S02": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q14-P01": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q15-P01": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q16-P01": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q17-P01-S01": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q17-P01-S02": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q18-P01": (
        (
            "DT2025-H1-SHARED-SECTION_2",
            "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q19-P01": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q20-P01": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q21-P01": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q22-P01": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q23-P01-S01": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q23-P01-S02": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q24-P01-S01": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
        (
            "DT2025-H1-SHARED-VIS_Q24_DIALYSIS",
            "e6e750f28baf42873483bf062641f3ff4eca10ea2284778850c1b2a50cd213e4",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q24-P01-S02": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
        (
            "DT2025-H1-SHARED-VIS_Q24_DIALYSIS",
            "e6e750f28baf42873483bf062641f3ff4eca10ea2284778850c1b2a50cd213e4",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q25-P01": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q26-P01": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q27-P01": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q28-P01": (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q29-P01": (
        (
            "DT2025-H1-SHARED-SECTION_4",
            "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q30-P01": (
        (
            "DT2025-H1-SHARED-SECTION_4",
            "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q31-P01": (
        (
            "DT2025-H1-SHARED-SECTION_4",
            "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q32-P01": (
        (
            "DT2025-H1-SHARED-SECTION_4",
            "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        ),
        (
            "DT2025-H1-SHARED-VIS_Q32_ATOMIC_MODELS",
            "96a8d87b9b9f413679a38b0942314a550f9c35dc69af39836bf3f91d5ef1259e",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P01": (
        (
            "DT2025-H1-SHARED-SECTION_4",
            "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P02": (
        (
            "DT2025-H1-SHARED-SECTION_4",
            "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P03-S01": (
        (
            "DT2025-H1-SHARED-SECTION_4",
            "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P03-S02": (
        (
            "DT2025-H1-SHARED-SECTION_4",
            "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q34-P01": (
        (
            "DT2025-H1-SHARED-SECTION_4",
            "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q35-P01": (
        (
            "DT2025-H1-SHARED-SECTION_5",
            "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q36-P01": (
        (
            "DT2025-H1-SHARED-SECTION_5",
            "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q37-P01": (
        (
            "DT2025-H1-SHARED-SECTION_5",
            "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S01": (
        (
            "DT2025-H1-SHARED-SECTION_5",
            "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        ),
        (
            "DT2025-H1-SHARED-VIS_Q38_ACID_LABEL",
            "41a5f7587d29d8c56330bfab4b6d3eb2ee9bb81ee0063874dfbfc21b74ac6d92",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S02": (
        (
            "DT2025-H1-SHARED-SECTION_5",
            "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        ),
        (
            "DT2025-H1-SHARED-VIS_Q38_ACID_LABEL",
            "41a5f7587d29d8c56330bfab4b6d3eb2ee9bb81ee0063874dfbfc21b74ac6d92",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S03": (
        (
            "DT2025-H1-SHARED-SECTION_5",
            "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        ),
        (
            "DT2025-H1-SHARED-VIS_Q38_ACID_LABEL",
            "41a5f7587d29d8c56330bfab4b6d3eb2ee9bb81ee0063874dfbfc21b74ac6d92",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q39-P01": (
        (
            "DT2025-H1-SHARED-SECTION_5",
            "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q40-P01": (
        (
            "DT2025-H1-SHARED-SECTION_5",
            "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        ),
    ),
    "W1-DT2025-H1-MID-AP-DT2025-H1-Q41-P01": (
        (
            "DT2025-H1-SHARED-SECTION_5",
            "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        ),
        (
            "DT2025-H1-SHARED-VIS_Q41_DATA_TABLE",
            "134654d1d65bfc213d212e26adcfd7232c178a95cdac0aaafce9abebbfe349b3",
        ),
    ),
}

EXISTING_ANSWER_AREA_NODE_IDS = FIRST_THEME_ANSWER_AREA_IDS | frozenset(
    QUESTION_BINDINGS
)

__all__ = [
    "EXISTING_ANSWER_AREA_NODE_IDS",
    "QUESTION_BINDINGS",
    "SHARED_CONTEXT_BINDINGS",
    "SOURCE_PART_LABELS",
    "CropBinding",
]
