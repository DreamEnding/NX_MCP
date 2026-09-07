"""Legacy entry points resolve typed references and reject unsupported geometry."""

from types import SimpleNamespace as S

import pytest

from nx_mcp.legacy_repairs import LegacyRepairsMixin
from nx_mcp.runtime import NXToolError


class Line:
    def __init__(self, tag, end):
        self.Tag = tag
        self.StartPoint = S(X=0, Y=0, Z=0)
        self.EndPoint = S(X=end[0], Y=end[1], Z=end[2])


class Harness(LegacyRepairsMixin):
    def __init__(self):
        self.nxopen = S(Line=Line, Edge=type("Edge", (), {}), Face=type("Face", (), {}))
        self.a, self.b = Line(1, [1, 0, 0]), Line(2, [0, 1, 0])
        self.part = S(Sketches=[S(GetAllGeometry=lambda: [self.a, self.b])])
        self.calls = []

    def _resolve(self, ref, kinds):
        return {"a": self.a, "b": self.b}[ref]

    def _work_part(self):
        return self.part

    def _reference(self, *args):
        return {"id": "sketch"}

    def _edit_sketch(self, *args):
        self.calls.append(args)
        return {"edited": True}


@pytest.mark.parametrize(("end", "expected"), [([0, 1, 0], 90), ([1, 0, 0], 0), ([-1, 0, 0], 180)])
def test_angle_uses_actual_directions(end, expected):
    h = Harness()
    h.b = Line(2, end)
    result = h._measure_angle("a", "b")
    assert result["angle_deg"] == pytest.approx(expected)
    assert result["units"] == "deg"
    assert result["coordinate_frame"] == "work_part"


def test_zero_length_rejected():
    h = Harness()
    h.a = Line(1, [0, 0, 0])
    with pytest.raises(NXToolError, match="degenerate"):
        h._measure_angle("a", "b")


@pytest.mark.parametrize(
    ("kind", "targets", "value"),
    [
        ("midpoint", ["a"], None),
        ("horizontal", ["a", "b"], None),
        ("horizontal", ["a"], 5),
        ("radius", ["a"], None),
    ],
)
def test_constraint_preflight_has_no_mutation(kind, targets, value):
    h = Harness()
    with pytest.raises(NXToolError):
        h._sketch_constraint(kind, targets, value)
    assert not h.calls


def test_fixed_constraint_routes_to_owned_sketch_editor():
    h = Harness()
    h._sketch_constraint("fix", ["a"])
    assert h.calls == [("sketch", [{"action": "constraint", "curve": "a", "type": "fixed"}])]


def test_constraint_rejects_cross_sketch_targets():
    h = Harness()
    h.part.Sketches = [S(GetAllGeometry=lambda: [h.a]), S(GetAllGeometry=lambda: [h.b])]
    with pytest.raises(NXToolError, match="one sketch"):
        h._sketch_constraint("parallel", ["a", "b"])


def test_delete_feature_updates_native_delete_list_and_reports_reference():
    h = Harness()
    h.session = S(
        UpdateManager=S(
            AddToDeleteList=lambda feature: h.calls.append(feature), DoUpdate=lambda: None
        )
    )
    h._require_api = lambda *args: None
    h._update_model = lambda: h.calls.append("update")
    assert h._delete_feature("a") == {"deleted": [{"id": "sketch"}]}
    assert h.calls == [h.a, "update"]


@pytest.mark.parametrize("batch", [True, False])
def test_canned_view_reports_actual_host_mode(batch):
    from nx_mcp.nx_bridge import NXOpenExecutor

    calls = []
    executor = S(
        session=S(IsBatch=batch),
        nxopen=S(View=S(Canned=S(Top=1), ScaleAdjustment=S(Fit=2))),
        _work_part=lambda: S(ModelingViews=S(WorkView=S(Orient=lambda *a: calls.append(a)))),
    )
    result = NXOpenExecutor._set_view(executor, "Top")
    assert calls == [(1, 2)]
    assert result["orientation"] == "top" and result["viewport_available"] is not batch
