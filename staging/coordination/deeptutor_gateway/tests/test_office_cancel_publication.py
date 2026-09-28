"""Durable preview publication and real task/process cancellation, synthetic data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from threading import Event
import json
import time

import pytest
from test_desktop_mixed_paper_service import setup, _add_word, _request
from mixed_pagination_test_support import paginate_and_read, synthetic_pagination
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import MixedPaperError, _ACTIVE
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, TaskCancellationEvent, read_cancel_scope
from integrations.deeptutor_shchem_v1.owned_process import ProcessTimeout
from test_desktop_facade import desktop_paths, _build_export_facade, _create_export_preview


def approved(setup):
    service,store,words,*_=setup
    _add_word(service,words)
    first=paginate_and_read(service,service.create_preview(_request(service)))
    service.approve(first.preview_id,first.preview_hash)
    return service,store,first


@pytest.mark.parametrize("stage",["before_renderer","after_renderer","before_publish","timeout","failure"])
def test_abandoned_new_preview_never_replaces_approved_preview_and_can_retry(setup,monkeypatch,stage):
    service,store,first=approved(setup)
    drafts=deepcopy(store.snapshot()["drafts"])
    candidate=service.create_preview(_request(service))
    event=TaskCancellationEvent()
    def render(paths,output):
        if stage=="timeout":raise ProcessTimeout()
        if stage=="failure":raise RuntimeError("synthetic conversion failure")
        if stage=="before_renderer":event.set()
        value=synthetic_pagination(paths,output)
        if stage=="after_renderer":event.set()
        return value
    service._pagination_builder=render
    original=service._save_preview_records
    def publish(*args,**kwargs):
        if stage=="before_publish":event.set()
        return original(*args,**kwargs)
    monkeypatch.setattr(service,"_save_preview_records",publish)
    with pytest.raises((ReadCancelled,ProcessTimeout,RuntimeError)),read_cancel_scope(event):
        service.prepare_pagination(candidate.preview_id,candidate.preview_hash)
    current=store.snapshot()["drafts"]
    assert current[_ACTIVE]==drafts[_ACTIVE]
    assert current[first.preview_id]==drafts[first.preview_id]
    assert len(service.export(first.preview_id,first.preview_hash)["artifacts"])==4
    monkeypatch.setattr(service,"_save_preview_records",original)
    service._pagination_builder=synthetic_pagination
    retried=service.prepare_pagination(candidate.preview_id,candidate.preview_hash)
    assert store.snapshot()["drafts"][_ACTIVE]["preview_id"]==retried.preview_id
    assert retried.preview_model["pagination"]["status"]=="rendered_pending_review"


def test_repeated_pagination_of_active_preview_rejects_without_overwrite(setup):
    service,store,first=approved(setup)
    before=store.path.read_bytes()
    with pytest.raises(MixedPaperError,match="分页已生成"):
        service.prepare_pagination(first.preview_id,first.preview_hash)
    assert store.path.read_bytes()==before
    assert service.export(first.preview_id,first.preview_hash)["status"]=="completed"


def test_concurrent_candidates_have_one_cas_winner(setup):
    service,store,first=approved(setup)
    candidates=[service.create_preview(_request(service)) for _ in range(2)]
    barrier=Barrier(2)
    def render(paths,output):
        value=synthetic_pagination(paths,output)
        barrier.wait(timeout=10)
        return value
    service._pagination_builder=render
    def prepare(value):
        with read_cancel_scope(TaskCancellationEvent()):
            try:return service.prepare_pagination(value.preview_id,value.preview_hash)
            except MixedPaperError as error:return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(prepare,candidates))
    winners=[value for value in results if not isinstance(value,Exception)]
    assert len(winners)==1
    assert store.snapshot()["drafts"][_ACTIVE]["preview_id"]==winners[0].preview_id
    assert next(value for value in results if isinstance(value,Exception)).code=="paper_preview_stale"


def test_cancel_after_final_publish_is_rejected_and_result_remains_current(setup):
    service,store,*_=setup
    words=setup[2];_add_word(service,words)
    candidate=service.create_preview(_request(service))
    service._pagination_builder=synthetic_pagination
    event=TaskCancellationEvent()
    with read_cancel_scope(event):
        result=service.prepare_pagination(candidate.preview_id,candidate.preview_hash)
        assert event.request_cancel() is False
    assert store.snapshot()["drafts"][_ACTIVE]["preview_hash"]==result.preview_hash


@pytest.mark.parametrize("delayed_beyond_ui_bound",[False,True])
def test_legacy_parent_waits_boundedly_for_real_child_cleanup_diagnostic(desktop_paths,monkeypatch,delayed_beyond_ui_bound):
    from integrations.deeptutor_shchem_v1 import desktop_facade as desktop, paper_export_workbench as jobs
    from integrations.deeptutor_shchem_v1.reader_cancellation import cancellation_requested
    started,release=Event(),Event();event=TaskCancellationEvent()
    manager=jobs.PaperExportJobManager(desktop_paths.state_root)
    facade,_=_build_export_facade(desktop_paths,manager)
    monkeypatch.setattr(jobs,"_locate_toolchain",lambda:None)
    if delayed_beyond_ui_bound:monkeypatch.setattr(desktop,"_PAPER_EXPORT_CANCEL_WAIT_SECONDS",.05)
    session=str(desktop_paths.state_root/"synthetic-retained-session")
    def render(*args,**kwargs):
        started.set()
        while not cancellation_requested():time.sleep(.005)
        if delayed_beyond_ui_bound:assert release.wait(5)
        else:time.sleep(.12)
        error=ReadCancelled("本次结果已作废；合成转换器清理尚未确认。")
        error.cleanup_complete=False;error.office_session=session
        raise error
    monkeypatch.setattr(jobs,"render_export_bundle",render)
    preview=_create_export_preview(facade);facade.approve_paper_preview(preview.preview_id,preview.preview_hash)
    def export():
        with read_cancel_scope(event):return facade.export_paper_preview(preview.preview_id,preview.preview_hash)
    try:
        with ThreadPoolExecutor(max_workers=1) as caller:
            future=caller.submit(export);assert started.wait(3);event.set()
            with pytest.raises(ReadCancelled) as captured:future.result(timeout=3)
            assert captured.value.cleanup_complete is False
            if delayed_beyond_ui_bound:
                assert "后台转换清理尚未确认" in captured.value.message_zh
            else:
                assert "合成转换器清理尚未确认" in captured.value.message_zh
                assert captured.value.office_session==session
            release.set()
    finally:
        release.set();manager.shutdown()
    record=json.loads(next(manager.root.glob("WBEXP-*/job.json")).read_text(encoding="utf-8"))
    assert record["status"]=="cancelled" and record["artifacts"]==[]
    assert record["error"]["details"]=={"cleanup_complete":False,"office_session":session}


@pytest.mark.parametrize("event_type",[Event,TaskCancellationEvent])
def test_legacy_parent_returns_committed_child_if_stop_arrives_before_poll(desktop_paths,monkeypatch,event_type):
    from integrations.deeptutor_shchem_v1 import paper_export_workbench as jobs
    from test_desktop_facade import paper_export_fixture
    manager=jobs.PaperExportJobManager(desktop_paths.state_root)
    facade,_=_build_export_facade(desktop_paths,manager)
    monkeypatch.setattr(jobs,"_locate_toolchain",lambda:None)
    monkeypatch.setattr(jobs,"render_export_bundle",paper_export_fixture._fake_render_factory({}))
    event=event_type();start=manager.start
    def completed_before_parent_poll(*args,**kwargs):
        queued=start(*args,**kwargs);deadline=time.monotonic()+5
        while manager.get(queued["job_id"])["status"]!="completed":
            assert time.monotonic()<deadline
            time.sleep(.005)
        event.set()
        return queued
    monkeypatch.setattr(manager,"start",completed_before_parent_poll)
    preview=_create_export_preview(facade);facade.approve_paper_preview(preview.preview_id,preview.preview_hash)
    try:
        # This legacy caller delegates terminal-state decisions to the facade.
        # Qt normally uses TaskCancellationEvent and its own boundary checks.
        with read_cancel_scope(event,check_boundaries=False):
            result=facade.export_paper_preview(preview.preview_id,preview.preview_hash)
        assert result["status"]=="completed" and len(result["artifacts"])==4
        assert manager.get(result["job_id"])["status"]=="completed"
        assert event.is_set() is (event_type is Event)
    finally:manager.shutdown()
