"""Isolated synthetic fixture for the source and packaged review verifier.

Never imported by normal teaching workflows. The injected transport constructs
local test data only, with no HTTP client and no real model or credential.
"""
from __future__ import annotations
import contextlib
import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from .desktop_facade import DesktopWorkbenchFacade
from .desktop_paths import DesktopPaths
from .model_provider_probe import ProbeTransportResponse
from .model_provider_settings import ModelProviderProbeContext, ModelProviderSettingsError
from .student_visual_analysis import StudentVisualAnalysisManager
PROFILE_ID='review-fixture-only'
PROFILE_REVISION='rev_'+'7'*32
API_KEY_SENTINEL='synthetic-not-a-real-credential'
MODEL_ID='synthetic-review-fixture'

class _CurriculumReader:
    def catalog(self) -> dict[str, Any]:
        return {
            "counts": {
                "volumes": 1,
                "chapters": 1,
                "sections": 1,
                "active_atomic_mappings": 0,
            },
            "volumes": [
                {
                    "volume_id": "TB-M1",
                    "volume_title": "必修第一册",
                    "display_label_zh": "必修第一册",
                    "chapters": [
                        {
                            "chapter_id": "TB-M1-C1",
                            "chapter_title": "第1章",
                            "display_label_zh": "第1章",
                            "sections": [
                                {
                                    "section_key": "TB-M1-C1:1.1",
                                    "section_number": "1.1",
                                    "section_title": "物质的分类",
                                    "display_label_zh": "1.1 物质的分类",
                                }
                            ],
                        }
                    ],
                }
            ],
        }

class _ProviderStore:
    """Facade metadata plus the manager's short-lived credential context."""

    def __init__(
        self,
        *,
        allowed_data_classes: tuple[str, ...] = (
            "synthetic_only",
            "source_page_image",
            "student_answer_image",
        ),
        image_egress: str = "teacher_confirmed_visual_pages",
    ) -> None:
        self.allowed_data_classes = allowed_data_classes
        self.image_egress = image_egress

    def list_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "profile_id": PROFILE_ID,
                "display_name": "端到端假视觉模型",
                "base_url": "https://api.openai.com/v1",
                "model_id": MODEL_ID,
                "api_style": "responses",
                "capabilities": ["text", "vision", "structured_output"],
                "effective_capabilities": [
                    "text",
                    "vision",
                    "structured_output",
                ],
                "capability_evidence": {
                    "catalog": ["text", "vision", "structured_output"],
                    "declared": ["text", "vision", "structured_output"],
                    "probed": [],
                    "unknown": [],
                },
                "allowed_data_classes": list(self.allowed_data_classes),
                "image_egress": self.image_egress,
                "credential_state": "configured",
                "revision": PROFILE_REVISION,
            }
        ]

    def invocation_policy(
        self, profile_id: str, *, expected_revision: str
    ) -> dict[str, Any]:
        if profile_id != PROFILE_ID or expected_revision != PROFILE_REVISION:
            raise ModelProviderSettingsError(
                "revision_conflict", "provider settings changed", 409
            )
        return {
            "profile_id": PROFILE_ID,
            "revision": PROFILE_REVISION,
            "capability_evidence": {
                "catalog": ["text", "vision", "structured_output"],
                "declared": ["text", "vision", "structured_output"],
                "probed": [],
                "unknown": [],
            },
            "effective_capabilities": ["text", "vision", "structured_output"],
            "allowed_data_classes": list(self.allowed_data_classes),
            "image_egress": self.image_egress,
        }

    def credential_exists(self, profile_id: str) -> bool:
        return profile_id == PROFILE_ID

    @contextlib.contextmanager
    def borrow_invocation_context(self, profile_id: str, *, expected_revision: str):
        self.invocation_policy(profile_id, expected_revision=expected_revision)
        yield ModelProviderProbeContext(
            profile_id=PROFILE_ID,
            provider_id="openai",
            model_id=MODEL_ID,
            base_url_policy="openai_official_https_v1",
            base_url="https://api.openai.com/v1",
            revision=PROFILE_REVISION,
            api_key=API_KEY_SENTINEL,
            api_style="responses",
        )

class SyntheticTransport:
    def __init__(self): self.calls=0
    @staticmethod
    def _candidate(manifest: dict[str, Any]) -> dict[str, Any]:
            page_hashes = [row["page_sha256"] for row in manifest["page_manifest"]]
            match = manifest["allowed_matches"][0]
            bbox = {"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.3}
            maximum_score = float(match["maximum_score"])
            return {
                "schema_version": "shchem.student-visual-analysis-candidate.v1",
                "page_quality": [
                    {
                        "page_sha256": digest,
                        "quality": "clear",
                        "issues": [],
                        "confidence": 0.95,
                    }
                    for digest in page_hashes
                ],
                "matches": [
                    {
                        "match_id": match["match_id"],
                        "atomic_part_id": match["atomic_part_id"],
                        "printed_question_id": match["printed_question_id"],
                        "question_number": match["question_number_hint"],
                        "question_anchor": {
                            "page_sha256": match["question_page_sha256"],
                            "bbox": bbox,
                        },
                        "student_answer_anchor": {
                            "page_sha256": match["student_work_page_sha256"],
                            "bbox": bbox,
                        },
                        "reference_answer_anchor": (
                            {
                                "page_sha256": match["reference_answer_page_sha256"],
                                "bbox": bbox,
                            }
                            if match["reference_answer_page_sha256"] is not None
                            else None
                        ),
                        "answer_region_anchor": {
                            "page_sha256": match["student_work_page_sha256"],
                            "bbox": bbox,
                        },
                        "visual_response_observation": "可见学生写出 NaCl，并给出数值结果。",
                        "chemistry_observations": {
                            "formulas": ["NaCl"],
                            "charges": [],
                            "conditions": [],
                            "units": ["mol"],
                            "other_visible_details": [],
                        },
                        "scoring_points": [
                            {
                                "scoring_point_id": match["allowed_scoring_point_ids"][0],
                                "evidence": [
                                    {
                                        "page_sha256": match["student_work_page_sha256"],
                                        "bbox": bbox,
                                    }
                                ],
                                "suggested_score": 1.0,
                                "maximum_score": maximum_score,
                                "confidence": 0.9,
                                "blockers": [],
                            }
                        ],
                        "suggested_score": 1.0,
                        "maximum_score": maximum_score,
                        "confidence": 0.9,
                        "blockers": [],
                        "error_hypotheses": [
                            {
                                "hypothesis": "候选：概念表达可能不完整。",
                                "supporting_evidence": ["仅观察到简式"],
                                "counterevidence": ["仍需教师结合题意判断"],
                                "confidence": 0.55,
                            }
                        ],
                    }
                ],
                "blockers": [],
                "requires_teacher_review": True,
                "final_score": None,
                "long_term_update_allowed": False,
            }

    def send(self, request, *, cancel_event, deadline_monotonic):
        self.calls+=1
        body=json.loads(request.body)
        text=next(row['text'] for row in body['input'][0]['content'] if row['type']=='input_text')
        manifest=json.loads(text.split('\n',1)[1])
        candidate=self._candidate(manifest)
        candidate['matches']=[]
        for match in manifest['allowed_matches']:
            selected={**manifest,'allowed_matches':[match]}
            candidate['matches'].extend(self._candidate(selected)['matches'])
        reply={'status':'completed','error':None,'incomplete_details':None,
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(candidate,ensure_ascii=False)}]}],
            'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
        return ProbeTransportResponse(http_status=200,content_type='application/json',content_encoding=None,
                                      body=json.dumps(reply).encode(),latency_ms=0,model_invoked=True)


def seed_review(workspace, state, source_dir):
    """Create two visibly synthetic tasks using the real student manager."""
    from PIL import Image, ImageDraw, ImageFont
    paths=DesktopPaths.from_workspace(Path(workspace),state_root=Path(state))
    curriculum=_CurriculumReader();provider=_ProviderStore();transport=SyntheticTransport()
    manager=StudentVisualAnalysisManager(Path(state)/'student-visual-v1',project_root=Path(workspace),
        provider_store=provider,transport=transport,curriculum_catalog=curriculum.catalog(),require_project_external=True)
    facade=DesktopWorkbenchFacade(paths,curriculum_reader=curriculum,provider_store=provider,student_analysis_manager=manager)
    source_dir=Path(source_dir);source_dir.mkdir(parents=True,exist_ok=True)
    font=None
    for path in ('C:/Windows/Fonts/msyh.ttc','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try: font=ImageFont.truetype(path,30);break
        except OSError: pass
    if font is None:font=ImageFont.load_default()
    students=[];submissions=[];sources=[]
    for student_no in range(2):
        profile=facade.create_student_profile(grade='高三',retention_days=30,consent_recorded=True)
        submission=facade.create_student_submission(profile.student_id)
        for role in ('question_pages','student_work_pages','reference_answer_pages'):
            files=[]
            for n in range(2):
                image=Image.new('RGB',(880,1100),'white');draw=ImageDraw.Draw(image)
                lines=['SYNTHETIC REVIEW FIXTURE - NOT A REAL STUDENT',f'Sample {student_no+1} / item {n+1} / {role}',
                       'NaCl   H2SO4   12.5 mol/L',
                       'Original page retained in full; use zoom to inspect.',
                       'Teacher score and reason are saved independently.']
                for i,line in enumerate(lines):draw.text((40,65+i*90),line,fill='black',font=font)
                draw.rectangle((85,340,705,680),outline='black',width=2)
                draw.text((110,425),'n = c * V',fill='black',font=font)
                path=source_dir/f'synthetic-{student_no}-{role}-{n}.png';image.save(path);files.append(path);sources.append(path)
            submission=facade.add_student_submission_files(student_id=profile.student_id,submission_id=submission.submission_id,
                role=role,files=files,expected_revision=submission.revision)
        submission=facade.confirm_student_matching(student_id=profile.student_id,submission_id=submission.submission_id,
            expected_revision=submission.revision,edits=[{
                'match_id':m.match_id,'question_number_hint':str(i+1),'maximum_score':2.0,
                'question_page_sha256':m.question_page_sha256,'student_work_page_sha256':m.student_work_page_sha256,
                'reference_answer_page_sha256':m.reference_answer_page_sha256} for i,m in enumerate(submission.matches)])
        confirmation=facade.prepare_student_analysis_confirmation(student_id=profile.student_id,submission_id=submission.submission_id,profile_id=PROFILE_ID)
        facade.start_student_analysis(student_id=profile.student_id,confirmation=confirmation,identifiers_clear=True,student_page_egress_confirmed=True)
        end=time.monotonic()+30
        while time.monotonic()<end:
            submission=facade.student_submission(student_id=profile.student_id,submission_id=submission.submission_id)
            if submission.candidate_available:break
            time.sleep(.02)
        if not submission.candidate_available:raise RuntimeError('Synthetic candidate generation failed: '+submission.status)
        students.append(profile);submissions.append(submission)
    # New requests remain impossible to start via the UI after fixture setup.
    provider.list_metadata=lambda:[]
    return facade,students,submissions,transport,sources
