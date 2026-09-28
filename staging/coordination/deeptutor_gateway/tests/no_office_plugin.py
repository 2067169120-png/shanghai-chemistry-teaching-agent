"""Explicit regression guard: production Office activation is a separate QA run.

Load with ``-p staging.coordination.deeptutor_gateway.tests.no_office_plugin``.
Synthetic process tests and injected renderer/COM protocol tests remain real.
"""
from pathlib import Path
from contextlib import contextmanager
import pytest


@contextmanager
def block_office_activation():
    """Also used by direct synthetic screenshot/cold-start scripts."""
    from integrations.deeptutor_shchem_v1 import owned_process
    from integrations.deeptutor_shchem_v1.office_conversion import OfficeConversionError
    original=owned_process.OwnedProcess.__init__
    def guarded(self,command,*,env,**kwargs):
        names={Path(str(value)).name.lower() for value in command}
        if ({"SHCHEM_OFFICE_SESSION","SHCHEM_RENDER_SCRIPT"} & env.keys()
                or names & {"winword.exe","soffice.exe","soffice","libreoffice","render_docx.py"}):
            raise OfficeConversionError("常规回归已禁止真实 Office 启动；本测试只核对注入的合成输出。", "test_office_disabled")
        return original(self,command,env=env,**kwargs)
    owned_process.OwnedProcess.__init__=guarded
    try:
        yield
    finally:
        owned_process.OwnedProcess.__init__=original


@pytest.fixture(autouse=True)
def forbid_real_office_activation():
    with block_office_activation():
        yield
