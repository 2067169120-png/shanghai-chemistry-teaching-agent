from io import BytesIO
from zipfile import ZipFile
import pytest
from lxml import etree
from test_raster_numbers import fixture_documents, edit_for
from integrations.deeptutor_shchem_v1.desktop_raster_numbers import apply_to_documents


@pytest.mark.parametrize('fmt', ['PNG', 'JPEG'])
def test_content_types_keeps_opc_default_namespace_for_office(fmt):
    docs,image=fixture_documents(fmt)
    result=apply_to_documents(docs,[edit_for(image)])
    for role in ('student','teacher'):
        with ZipFile(BytesIO(result[role+'_bytes'])) as package:
            root=etree.fromstring(package.read('[Content_Types].xml'))
            assert root.prefix is None
            assert root.nsmap[None]=='http://schemas.openxmlformats.org/package/2006/content-types'
            for part in package.namelist():
                if part.startswith('word/media/'):
                    assert root.xpath('*[@PartName="/'+part+'"]')[0].get('ContentType')=='image/png'
