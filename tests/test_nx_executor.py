from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from nx_mcp.bridge import BridgeClient, BridgeServer
from nx_mcp.contracts import NXToolError
from nx_mcp.nx_bridge import NXOpenExecutor, start_bridge, stop_bridge
from nx_mcp.real_smoke import run_iteration
from nx_mcp.server import create_server
from nx_mcp.workspace import Workspace

pytestmark = [pytest.mark.integration, pytest.mark.fake_nx]


class FakeObject:
    def __init__(self, tag: int, name: str):
        self.Tag = tag
        self.Name = name

    def SetName(self, name):
        self.Name = name


class FakeSketch(FakeObject):
    def __init__(self, tag: int, name: str):
        super().__init__(tag, name)
        self.Feature = self
        self.geometry = []
        self.active = False

    def Activate(self, reorient):
        self.active = True

    def AddGeometry(self, geometry, infer_option):
        self.geometry.append(geometry)

    def Deactivate(self, reorient, update_level):
        self.active = False


class FakeCollection:
    def __init__(self, *values):
        self.values = list(values)

    def __iter__(self):
        return iter(self.values)


class FakeSketchBuilder:
    def __init__(self, sketches):
        self.sketches = sketches

    def Commit(self):
        sketch = FakeSketch(102 + len(self.sketches.values), "SKETCH(2)")
        self.sketches.values.append(sketch)
        return sketch

    def Destroy(self):
        pass


class FakeSketches(FakeCollection):
    def CreateSketchInPlaceBuilder2(self, operation):
        self.last_builder = FakeSketchBuilder(self)
        return self.last_builder


class FakePlanes:
    def CreatePlane(self, origin, normal, update_option):
        self.last_plane = SimpleNamespace(
            origin=origin,
            normal=normal,
            update_option=update_option,
        )
        return self.last_plane


class FakeCurves:
    def __init__(self):
        self.values = []

    def CreateLine(self, start, end):
        line = FakeObject(400 + len(self.values), f"LINE({len(self.values) + 1})")
        line.start = start
        line.end = end
        self.values.append(line)
        return line


class FakeFeature(FakeObject):
    def __init__(self, tag: int, name: str, bodies):
        super().__init__(tag, name)
        self._bodies = bodies

    def GetBodies(self):
        return list(self._bodies)


class FakeExtrudeBuilder:
    def __init__(self, part):
        self.part = part
        self.Limits = SimpleNamespace(
            StartExtend=SimpleNamespace(Value=SimpleNamespace(RightHandSide=None)),
            EndExtend=SimpleNamespace(Value=SimpleNamespace(RightHandSide=None)),
        )
        self.BooleanOperation = SimpleNamespace(Type=None)

    def AllowSelfIntersectingSection(self, allowed):
        self.allow_self_intersecting_section = allowed

    def CommitFeature(self):
        body = FakeObject(202 + len(self.part.Bodies.values), "BODY(2)")
        feature = FakeFeature(302 + len(self.part.Features.values), "EXTRUDE(2)", [body])
        self.part.Bodies.values.append(body)
        self.part.Features.values.append(feature)
        return feature

    def Destroy(self):
        pass


class FakeFeatures(FakeCollection):
    def __init__(self, part, *values):
        super().__init__(*values)
        self.part = part

    def CreateExtrudeBuilder(self, feature):
        self.last_extrude_builder = FakeExtrudeBuilder(self.part)
        return self.last_extrude_builder


class FakeSection:
    def AddToSection(
        self,
        rules,
        first,
        second,
        third,
        point,
        mode,
        chain,
    ):
        self.rules = rules
        self.point = point
        self.mode = mode
        self.chain = chain


class FakeSections:
    def CreateSection(self):
        self.last_section = FakeSection()
        return self.last_section


class FakeRuleFactory:
    def CreateRuleOptions(self):
        return SimpleNamespace()

    def CreateRuleCurveFeature(self, features, displayable, options):
        return SimpleNamespace(features=features)


class FakeDirections:
    def CreateDirection(self, sketch, sense, update_option):
        self.last_direction = SimpleNamespace(
            sketch=sketch,
            sense=sense,
            update_option=update_option,
        )
        return self.last_direction


class FakePart(FakeObject):
    def __init__(self, name: str = "bracket", tag: int = 42):
        super().__init__(tag, name)
        self.FullPath = f"C:/workspace/{name}.prt"
        self.Sketches = FakeSketches(FakeSketch(101, "SKETCH(1)"))
        self.Bodies = FakeCollection(FakeObject(201, "BODY(1)"))
        self.Features = FakeFeatures(self, FakeFeature(301, "EXTRUDE(1)", [self.Bodies.values[0]]))
        self.Planes = FakePlanes()
        self.Sections = FakeSections()
        self.ScRuleFactory = FakeRuleFactory()
        self.Directions = FakeDirections()
        self.Curves = FakeCurves()
        self.ModelingViews = SimpleNamespace(WorkView=SimpleNamespace(Fit=lambda: None))
        self.saved = False
        self._parts = None

    def Save(self, save_components, close_after_save):
        self.saved = True

    def Close(self, whole_tree, close_modified, responses):
        self.close_args = (whole_tree, close_modified, responses)
        self._parts.Work = None
        self._parts.Display = None
        self._parts.last_closed = self


class FakeLoadStatus:
    def Dispose(self):
        pass


class FakeStepCreator:
    def __init__(self):
        self.ObjectTypes = SimpleNamespace()

    def Commit(self):
        assert self.ExportFrom == "existing-part"
        assert self.ObjectTypes.Solids is True
        Path(self.OutputFile).write_text(
            "ISO-10303-21;\nMANIFOLD_SOLID_BREP();\nEND-ISO-10303-21;", encoding="utf-8"
        )

    def Destroy(self):
        pass


class FakeDexManager:
    def CreateStepCreator(self):
        return FakeStepCreator()


class FakeFileNew:
    def __init__(self, parts):
        self.parts = parts

    def Commit(self):
        part = FakePart(Path(self.NewFileName).stem, 43)
        part.FullPath = self.NewFileName
        self.parts._set_active(part)
        return part

    def Destroy(self):
        pass


class FakeParts:
    def __init__(self):
        self._set_active(FakePart())

    def _set_active(self, part):
        part._parts = self
        self.Work = part
        self.Display = part

    def FileNew(self):
        self.last_file_new = FakeFileNew(self)
        return self.last_file_new

    def OpenBaseDisplay(self, path):
        part = FakePart(Path(path).stem, 44)
        part.FullPath = path
        self._set_active(part)
        return part, FakeLoadStatus()


class FakeSession:
    def __init__(self):
        self.Parts = FakeParts()
        self.DexManager = FakeDexManager()
        self.undo_to_marks = []
        self.rollback_count = 0
        self.next_mark = 1
        self.marks = {}

    def SetUndoMark(self, visibility, name):
        mark = self.next_mark
        self.next_mark += 1
        self.marks[mark] = name
        return mark

    def UndoToMark(self, mark, name):
        self.undo_to_marks.append(mark)
        self.rollback_count += 1
        part = self.Parts.Work
        if self.marks.get(mark) == "NX MCP: nx_extrude" and len(part.Bodies.values) > 1:
            part.Bodies.values.pop()
            part.Features.values.pop()


FAKE_NXOPEN = SimpleNamespace(
    StepCreator=SimpleNamespace(ExportFromOption=SimpleNamespace(ExistingPart="existing-part")),
    BasePart=SimpleNamespace(
        SaveComponents=SimpleNamespace(TrueValue=True, FalseValue=False),
        CloseAfterSave=SimpleNamespace(FalseValue=False),
        CloseWholeTree=SimpleNamespace(TrueValue="whole-tree"),
        CloseModified=SimpleNamespace(CloseModified="close"),
    ),
    Part=SimpleNamespace(Units=SimpleNamespace(Millimeters="mm", Inches="inch")),
    DisplayPartOption=SimpleNamespace(AllowAdditional="allow-additional"),
    DisplayableObject=SimpleNamespace(Null=None),
    NXObject=SimpleNamespace(Null=None),
    Vector3d=lambda x, y, z: (x, y, z),
    Point3d=lambda x, y, z: (x, y, z),
    Sketch=SimpleNamespace(
        Null=None,
        ViewReorient=SimpleNamespace(TrueValue=True),
        UpdateLevel=SimpleNamespace(Model="model"),
        InferConstraintsOption=SimpleNamespace(InferNoConstraints="none"),
    ),
    SmartObject=SimpleNamespace(UpdateOption=SimpleNamespace(WithinModeling="within-modeling")),
    Section=SimpleNamespace(Mode=SimpleNamespace(Create="create")),
    Sense=SimpleNamespace(Forward="forward", Reverse="reverse"),
    Features=SimpleNamespace(Feature=SimpleNamespace(Null=None)),
    GeometricUtilities=SimpleNamespace(
        BooleanOperation=SimpleNamespace(BooleanType=SimpleNamespace(Create="create"))
    ),
    Expression=SimpleNamespace(ValueType=SimpleNamespace(Double="double")),
    Unit=SimpleNamespace(CollectionType=SimpleNamespace(Millimeter="mm")),
    Session=SimpleNamespace(MarkVisibility=SimpleNamespace(Visible="visible")),
)


def test_status_reports_version_and_stable_active_part_reference(tmp_path: Path):
    executor = NXOpenExecutor(FakeSession(), FAKE_NXOPEN, "NX 2512.7000", Workspace(tmp_path))

    first = executor.execute("nx_status", {})
    second = executor.execute("nx_status", {})

    assert first == second
    assert first["connected"] is True
    assert first["nx_version"] == "NX 2512.7000"
    assert first["bridge_protocol"] == 1
    assert first["active_part"]["kind"] == "part"
    assert first["active_part"]["name"] == "bracket"


def test_query_commands_return_typed_stable_object_references(tmp_path: Path):
    executor = NXOpenExecutor(FakeSession(), FAKE_NXOPEN, "NX test", Workspace(tmp_path))

    sketches = executor.execute("nx_list_sketches", {})["objects"]
    bodies = executor.execute("nx_list_bodies", {})["objects"]
    features = executor.execute("nx_list_features", {})["objects"]

    assert [item["kind"] for item in sketches + bodies + features] == [
        "sketch",
        "body",
        "feature",
    ]
    assert executor.execute("nx_list_bodies", {})["objects"][0]["id"] == bodies[0]["id"]


def test_create_part_uses_validated_workspace_path_and_returns_part_reference(
    tmp_path: Path,
):
    session = FakeSession()
    executor = NXOpenExecutor(session, FAKE_NXOPEN, "NX test", Workspace(tmp_path))
    part_path = tmp_path / "parts" / "new-bracket.prt"

    result = executor.execute("nx_create_part", {"path": str(part_path), "units": "mm"})

    assert result["part"]["kind"] == "part"
    assert result["part"]["name"] == "new-bracket"
    assert result["message"] == "Created part: new-bracket"
    assert session.Parts.last_file_new.TemplateFileName == "model-plain-1-mm-template.prt"
    assert session.Parts.last_file_new.Units == "mm"
    assert session.Parts.last_file_new.DisplayPartOption == "allow-additional"
    assert not hasattr(session.Parts.last_file_new, "UseBlankTemplate")


def test_open_save_export_and_close_part_lifecycle(tmp_path: Path):
    session = FakeSession()
    executor = NXOpenExecutor(session, FAKE_NXOPEN, "NX test", Workspace(tmp_path))
    source = tmp_path / "existing.prt"
    source.write_text("part", encoding="utf-8")

    opened = executor.execute("nx_open_part", {"path": str(source)})
    saved = executor.execute("nx_save_part", {})
    exported = executor.execute("nx_export_step", {"path": str(tmp_path / "out" / "part.stp")})
    closed = executor.execute("nx_close_part", {"save": True})

    assert opened["part"]["name"] == "existing"
    assert saved["message"] == "Saved part: existing"
    assert exported["path"].endswith("part.stp")
    assert "MANIFOLD_SOLID_BREP" in Path(exported["path"]).read_text(encoding="utf-8")
    assert closed["message"] == "Closed part: existing"
    assert session.Parts.last_closed.close_args[:2] == ("whole-tree", "close")
    assert executor.execute("nx_status", {})["active_part"] is None


def test_explicit_sketch_workflow_adds_geometry_to_the_referenced_sketch(tmp_path: Path):
    session = FakeSession()
    executor = NXOpenExecutor(session, FAKE_NXOPEN, "NX test", Workspace(tmp_path))

    created = executor.execute("nx_create_sketch", {"plane": "XY", "name": "PROFILE"})
    sketch_id = created["object"]["id"]
    line = executor.execute(
        "nx_sketch_line",
        {"sketch_id": sketch_id, "start": {"x": 0, "y": 0}, "end": {"x": 20, "y": 0}},
    )
    rectangle = executor.execute(
        "nx_sketch_rectangle",
        {
            "sketch_id": sketch_id,
            "corner1": {"x": 0, "y": 0},
            "corner2": {"x": 20, "y": 10},
        },
    )
    finished = executor.execute("nx_finish_sketch", {"sketch_id": sketch_id})

    sketch = session.Parts.Work.Sketches.values[-1]
    assert created["object"]["name"] == "PROFILE"
    assert session.Parts.Work.Planes.last_plane.normal == (0.0, 0.0, 1.0)
    assert (
        session.Parts.Work.Sketches.last_builder.PlaneReference
        is session.Parts.Work.Planes.last_plane
    )
    assert line["object"]["kind"] == "curve"
    assert len(rectangle["objects"]) == 4
    assert len(sketch.geometry) == 5
    assert finished["object"]["id"] == sketch_id
    assert sketch.active is False


def test_extrude_returns_new_feature_and_body_references(tmp_path: Path):
    session = FakeSession()
    executor = NXOpenExecutor(session, FAKE_NXOPEN, "NX test", Workspace(tmp_path))
    sketch = executor.execute("nx_create_sketch", {"plane": "XY", "name": "PROFILE"})

    result = executor.execute(
        "nx_extrude",
        {"sketch_id": sketch["object"]["id"], "distance": 12.5, "reverse": False},
    )

    assert result["feature"]["kind"] == "feature"
    assert result["body"]["kind"] == "body"
    assert result["message"] == "Extruded PROFILE by 12.5"
    builder = session.Parts.Work.Features.last_extrude_builder
    assert builder.Limits.StartExtend.Value.RightHandSide == "0"
    assert builder.Limits.EndExtend.Value.RightHandSide == "12.5"
    assert builder.BooleanOperation.Type == "create"
    assert builder.Direction.sense == "forward"


def test_undo_and_fit_view_are_exposed_by_the_executor(tmp_path: Path):
    session = FakeSession()
    executor = NXOpenExecutor(session, FAKE_NXOPEN, "NX test", Workspace(tmp_path))
    sketch = executor.execute("nx_create_sketch", {"plane": "XY"})
    executor.execute(
        "nx_extrude",
        {"sketch_id": sketch["object"]["id"], "distance": 12.5},
    )

    undo = executor.execute("nx_undo", {})
    fitted = executor.execute("nx_fit_view", {})

    assert undo == {"message": "Undo successful"}
    assert fitted == {"message": "View fitted"}
    assert len(session.Parts.Work.Bodies.values) == 1
    assert session.undo_to_marks[-1] == 2


def test_save_clears_mcp_undo_marks(tmp_path: Path):
    executor = NXOpenExecutor(FakeSession(), FAKE_NXOPEN, "NX test", Workspace(tmp_path))

    executor.execute("nx_create_sketch", {"plane": "XY"})
    executor.execute("nx_save_part", {})

    with pytest.raises(NXToolError) as error:
        executor.execute("nx_undo", {})

    assert error.value.code == "NX_UNDO_UNAVAILABLE"


def test_failed_model_mutation_rolls_back_its_undo_mark(tmp_path: Path):
    session = FakeSession()
    executor = NXOpenExecutor(session, FAKE_NXOPEN, "NX test", Workspace(tmp_path))
    sketch = executor.execute("nx_create_sketch", {"plane": "XY", "name": "PROFILE"})

    with pytest.raises(NXToolError):
        executor.execute(
            "nx_sketch_rectangle",
            {
                "sketch_id": sketch["object"]["id"],
                "corner1": {"x": 0, "y": 0},
                "corner2": {"x": 0, "y": 10},
            },
        )

    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_mcp_sidecar_bridge_and_nx_executor_complete_core_workflow(tmp_path: Path):
    executor = NXOpenExecutor(FakeSession(), FAKE_NXOPEN, "NX test", Workspace(tmp_path))
    bridge_server = BridgeServer(executor.execute, token="workflow-token")
    bridge_server.start()
    mcp = create_server(
        BridgeClient("127.0.0.1", bridge_server.port, token="workflow-token"),
        Workspace(tmp_path),
    )
    try:
        async with create_connected_server_and_client_session(mcp) as client:
            result = await run_iteration(client, tmp_path, 1, prefix="rerun")
    finally:
        bridge_server.stop()

    assert result["iteration"] == 1
    assert result["nx_version"] == "NX test"
    assert (tmp_path / "rerun" / "run-01.stp").is_file()


def test_python_nx_bridge_requires_explicit_feasibility_gate(tmp_path: Path, monkeypatch):
    stop_bridge()
    monkeypatch.delenv("NX_MCP_ALLOW_UNVERIFIED_PYTHON_BRIDGE", raising=False)

    with pytest.raises(RuntimeError, match="has not been verified"):
        start_bridge(tmp_path)
