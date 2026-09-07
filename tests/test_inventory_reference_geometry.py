"""Selection and mutation preflight regressions; native geometry is tested separately."""

from unittest.mock import Mock

import pytest

from nx_mcp.inventory import compact_reference, page
from nx_mcp.runtime import NXToolError
from tests.fakes import Body, Object


@pytest.mark.parametrize("offset,limit", [(-1, None), (True, 2), (0, 0), (0, 1001), (0, True)])
def test_invalid_paging(offset, limit):
    with pytest.raises(NXToolError):
        page([], offset, limit)


def test_compact_identity_keeps_occurrence_context_and_page_totals():
    reference = {
        "id": "id",
        "kind": "component",
        "name": "bolt",
        "part_id": "owner",
        "occurrence_path": ["A", "bolt"],
        "session_id": "session",
    }
    assert compact_reference(reference) == {k: v for k, v in reference.items() if k != "session_id"}
    assert page([1, 2, 3], 1, 1) == (
        [2],
        {"total_count": 3, "count": 1, "offset": 1, "next_offset": 2},
    )


def test_topology_face_filter_rejects_foreign_faces_and_exposes_both_directions(rig):
    body = Body()
    rig.part.Bodies.append(body)
    face, edge = body.faces[0], body.edges[0]
    face.GetEdges = lambda: [edge]
    edge.GetFaces = lambda: [face]
    bid, fid = rig.ref(body), rig.ref(face, "face")
    result = rig.e._list_topology(bid, face=fid, include_adjacency=True, compact=True)
    assert result["face_edges"][0]["face"] == fid
    assert result["edge_faces"][0]["faces"] == [fid]
    assert result["edge_count"] == 1 and "session_id" not in result["faces"][0]
    with pytest.raises(NXToolError, match="belong"):
        rig.e._list_topology(bid, face=rig.ref(Body().faces[0], "face"))


def test_duplicate_reference_set_is_rejected_before_creation(rig):
    existing = Object("SOLIDS")
    rig.part.GetAllReferenceSets = lambda: [existing]
    rig.part.CreateReferenceSet = Mock()
    with pytest.raises(NXToolError, match="already exists"):
        rig.e._create_reference_set("solids", ["unresolved"])
    rig.part.CreateReferenceSet.assert_not_called()


@pytest.mark.parametrize("name", ["", "Entire Part", "Empty", "x" * 133])
def test_invalid_reference_set_name_never_resolves_members(rig, name):
    with pytest.raises(NXToolError):
        rig.e._create_reference_set(name, ["unresolved"])
