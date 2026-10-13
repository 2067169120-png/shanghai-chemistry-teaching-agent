"""Native editable PowerPoint entrance animations, one click per resource group."""
from pptx.oxml import parse_xml
from pptx.oxml.ns import nsdecls


def add_click_appear(slide, resource_shapes):
    """Entrance timing hides a group until its own click in PowerPoint slideshow.

    Shapes are grouped without rasterizing text or changing child geometry.
    A group is one teaching step even when its paragraph wraps to many lines.
    Static printed/exported PDF shows the completed slide; the student canvas
    and worksheet are separately filtered before this operation.
    """
    groups = []
    for identity, shapes in resource_shapes:
        if not shapes:
            continue
        group = slide.shapes.add_group_shape(shapes)
        group.name = 'shchem-click-' + identity
        groups.append(group.shape_id)
    if not groups:
        return
    nodes = []
    serial = 3
    for shape_id in groups:
        outer, parallel, effect, behavior = range(serial, serial+4)
        serial += 4
        nodes.append(f'''<p:par><p:cTn id="{outer}" fill="hold">
          <p:stCondLst><p:cond delay="indefinite"/></p:stCondLst><p:childTnLst>
          <p:par><p:cTn id="{parallel}" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>
          <p:par><p:cTn id="{effect}" presetID="1" presetClass="entr" presetSubtype="0" fill="hold" grpId="0" nodeType="clickEffect">
          <p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst><p:set><p:cBhvr>
          <p:cTn id="{behavior}" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>
          <p:tgtEl><p:spTgt spid="{shape_id}"/></p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst>
          </p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set></p:childTnLst>
          </p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>''')
    timing = parse_xml(f'''<p:timing {nsdecls('p')}><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>
      <p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>{''.join(nodes)}</p:childTnLst></p:cTn>
      <p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>
      <p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>
      </p:seq></p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>''')
    slide._element.insert_element_before(timing, 'p:extLst')
