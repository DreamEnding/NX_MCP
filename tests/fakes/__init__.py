"""Stateful NX seams for failure/recovery tests; not a geometric kernel oracle."""

import itertools
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nx_mcp.hardened import HardenedExecutor
from nx_mcp.workspace import Workspace


def point(x=0, y=0, z=0):
    return NS(X=x, Y=y, Z=z)


def matrix():
    return NS(Xx=1, Xy=0, Xz=0, Yx=0, Yy=1, Yz=0, Zx=0, Zy=0, Zz=1)


class Object:
    ids = itertools.count(1)

    def __init__(self, name=""):
        self.Tag = next(self.ids)
        self.Name = name
        self.JournalIdentifier = f"OBJECT({self.Tag})"
        self.IsBlanked = False
        self.Color = 7
        self.transparency = 0
        self.highlighted = False

    def SetName(self, name):
        self.Name = name

    def Blank(self):
        self.IsBlanked = True

    def Unblank(self):
        self.IsBlanked = False

    def Highlight(self):
        self.highlighted = True

    def Unhighlight(self):
        self.highlighted = False


class Face(Object):
    pass


class Edge(Object):
    pass


class Curve(Object):
    pass


class Body(Object):
    def __init__(self, name="Body", box=None):
        super().__init__(name)
        self.box = box or [0, 0, 0, 10, 10, 10]
        self.IsSolidBody = True
        self.IsOccurrence = False
        self.faces = [Face("face")]
        self.edges = [Edge("edge")]
        self.volume = 1000

    def GetFaces(self):
        return self.faces

    def GetEdges(self):
        return self.edges


class Feature(Object):
    def __init__(self, name="Extrude", bodies=None):
        super().__init__(name)
        self.FeatureType = "EXTRUDE"
        self.bodies = bodies or []
        self.expressions = [NS(Name="p1", RightHandSide="10", Value=10)]

    def GetBodies(self):
        return self.bodies

    def GetExpressions(self):
        return self.expressions

    def GetParents(self):
        return []

    def GetChildren(self):
        return []


class Sketch(Object):
    Null = None
    ViewReorient = NS(FalseValue=False, TrueValue=True)
    UpdateLevel = NS(Model="model")
    InferConstraintsOption = NS(InferNoConstraints=False)
    Status = NS(UnderConstrained=1, WellConstrained=2, OverConstrained=3)
    ConstraintClass = NS(Any=0)
    ConstraintType = NS(NoCon=0, Distance=1)

    def __init__(self, session, name="Sketch"):
        super().__init__(name)
        self.session = session
        self.Origin = point()
        self.Orientation = NS(Element=matrix())
        self.geometry = []
        self.constraints = []
        self.status = (1, 4)

    def Activate(self, *_):
        self.session.ActiveSketch = self

    def Deactivate(self, *_):
        self.session.ActiveSketch = None

    def AddGeometry(self, curve, *_):
        self.geometry.append(curve)

    def GetAllGeometry(self):
        return self.geometry

    def GetAllConstraintsOfType(self, *_):
        return self.constraints

    def GetConstraintsForGeometry(self, *_):
        return self.constraints

    def CalculateStatus(self):
        pass

    def GetStatus(self):
        return self.status


class Component(Object):
    def __init__(self, name, bodies=(), parent=None):
        super().__init__(name)
        self.Parent = parent
        self.IsSuppressed = False
        self.ReferenceSet = "Entire Part"
        self.children = []
        self.Prototype = NS(Bodies=list(bodies), FullPath=name + ".prt")
        self.position = point()
        self.rotation = matrix()
        self.occurrences = {}
        for b in bodies:
            o = Body(name + " occurrence", list(b.box))
            o.IsOccurrence = True
            o.OwningComponent = self
            self.occurrences[b.Tag] = o
        if parent:
            parent.children.append(self)

    def GetChildren(self):
        return self.children

    def FindOccurrence(self, body):
        return self.occurrences.get(body.Tag)

    def GetPosition(self):
        return self.position, self.rotation


class Collection(list):
    pass


class Session:
    def __init__(self):
        self.Parts = Collection()
        self.Parts.Work = self.Parts.Display = None
        self.ActiveSketch = None
        self.IsBatch = False
        self.marks = {}
        self.mark_ids = itertools.count(1)
        self.UpdateManager = NS(DoUpdate=Mock(return_value=0))

    def SetUndoMark(self, *_):
        mark = next(self.mark_ids)
        states = []
        for p in self.Parts:
            groups = [
                p.Bodies,
                p.Features,
                p.Curves,
                p.Sketches,
                p.DynamicSections,
                p.Notes,
                p.Labels,
            ]
            objs = list(itertools.chain.from_iterable(groups))
            attrs = [(o, o.IsBlanked, o.Color, o.transparency) for o in objs]
            states.append((p, [list(g) for g in groups], attrs, p.IsModified))
        self.marks[mark] = (states, self.ActiveSketch)
        return mark

    def UndoToMark(self, mark, *_):
        states, active = self.marks[mark]
        for p, groups, attrs, modified in states:
            for dest, values in zip(
                [p.Bodies, p.Features, p.Curves, p.Sketches, p.DynamicSections, p.Notes, p.Labels],
                groups,
                strict=True,
            ):
                dest[:] = values
            for obj, blank, color, transparency in attrs:
                obj.IsBlanked, obj.Color, obj.transparency = blank, color, transparency
            p.IsModified = modified
        self.ActiveSketch = active

    def DeleteUndoMark(self, mark, *_):
        self.marks.pop(mark, None)

    def DoesUndoMarkExist(self, mark, *_):
        return mark in self.marks


class Part(Object):
    def __init__(self, session, path):
        super().__init__(path.stem)
        self.FullPath = str(path)
        self.PartUnits = "mm"
        self.IsModified = False
        self.Bodies = Collection()
        self.Features = Collection()
        self.Curves = Collection()
        self.Datums = Collection()
        self.CoordinateSystems = Collection()
        self.Sketches = Collection()
        self.DynamicSections = Collection()
        self.Notes = Collection()
        self.Labels = Collection()
        self.ComponentAssembly = NS(RootComponent=None)
        self.WCS = NS(CoordinateSystem=NS(Orientation=NS(Element=matrix())))
        self.ModelingViews = NS(
            WorkView=NS(
                Name="Work",
                Matrix=matrix(),
                Origin=point(),
                AbsoluteOrigin=point(),
                Scale=1,
                RenderingStyle="shaded",
                ActiveDynamicSection=None,
                DisplaySectioningToggle=False,
                Fit=Mock(),
                UpdateDisplay=Mock(),
            )
        )
        self.UnitCollection = NS(FindObject=lambda name: name)
        self.MeasureManager = NS(
            NewMassProperties=lambda _u, _a, b: NS(Volume=sum(x.volume for x in b), Dispose=Mock())
        )
        self.session = session
        session.Parts.append(self)
        session.Parts.Work = session.Parts.Display = self

    def Save(self, *_):
        from pathlib import Path

        Path(self.FullPath).parent.mkdir(parents=True, exist_ok=True)
        Path(self.FullPath).write_bytes(b"native part fixture")
        self.IsModified = False
        self.session.marks.clear()
        return NS(Dispose=Mock(), NumberUnsavedParts=0, NumberUnsavedObjects=0)

    def SaveAs(self, path):
        self.FullPath = path
        return NS(Dispose=Mock(), NumberUnsavedParts=0, NumberUnsavedObjects=0)

    def Close(self, *_):
        self.session.Parts.remove(self)
        if self.session.Parts.Work is self:
            self.session.Parts.Work = None
        if self.session.Parts.Display is self:
            self.session.Parts.Display = None


@pytest.fixture
def rig(tmp_path, monkeypatch):
    session = Session()
    part = Part(session, tmp_path / "test.prt")
    nx = ModuleType("NXOpen")
    nx.Body = Body
    nx.Face = Face
    nx.Edge = Edge
    nx.Sketch = Sketch
    nx.Point3d = point
    nx.Vector3d = point
    nx.Matrix3x3 = matrix
    nx.Features = NS(Feature=Feature)
    nx.Session = NS(MarkVisibility=NS(Visible=1, Invisible=0))
    nx.MeasureBodies = NS(AnalysisUnit=NS(KilogramMillimeter=6))
    nx.BasePart = NS(
        Units=NS(Millimeters="mm"),
        SaveComponents=NS(TrueValue=True, FalseValue=False),
        CloseAfterSave=NS(FalseValue=False),
        CloseWholeTree=NS(FalseValue=False),
        CloseModified=NS(CloseModified=0),
    )
    nx.SketchWorkRegionBuilder = NS(ScopeType=NS(EntireSketch="all"))
    nx.View = NS(
        RenderingStyleType=NS(Shaded="shaded", ShadedWithEdges="edges", StaticWireframe="wire")
    )
    session.Parts.SetWork = lambda p: setattr(session.Parts, "Work", p)

    def display(p, *_):
        session.Parts.Display = p
        return None, NS(Dispose=Mock())

    session.Parts.SetDisplay = display
    session.Parts.OpenBase = lambda path: (
        Part(session, __import__("pathlib").Path(path)),
        NS(Dispose=Mock()),
    )
    e = HardenedExecutor(
        session, nx, "v2606 fake seam", Workspace(tmp_path), enable_experimental=True
    )

    def ref(obj, kind="body"):
        return e._reference(obj, kind, part, kind)["id"]

    def object_for(tag):
        return next(entry.value for entry in e.objects._objects.values() if entry.value.Tag == tag)

    uf = NS(
        Obj=NS(AskTranslucency=lambda tag: object_for(tag).transparency),
        Disp=NS(
            ColorName=NS(RED_NAME=3, MEDIUM_GRAY_NAME=5), AskClosestColorInDisplayedPart=lambda x: x
        ),
        ModlGeneral=NS(
            AskBoundingBox=lambda tag: object_for(tag).box,
            AskBoundingBoxExact=lambda tag, _: (
                object_for(tag).box[:3],
                [1, 0, 0, 0, 1, 0, 0, 0, 1],
                [object_for(tag).box[i + 3] - object_for(tag).box[i] for i in range(3)],
            ),
        ),
    )
    modules = {
        "Preferences": NS(
            Font=NS(Solid=1, Dashed=2, ValueOf=lambda value: value),
            Width=NS(
                Original=0,
                Thin=1,
                Normal=2,
                Thick=3,
                One=5,
                Two=6,
                Three=7,
                Four=8,
                Five=9,
                Six=10,
                Seven=11,
                Eight=12,
                Nine=13,
            ),
            ShadingRenderingStyleOption=NS(FullyShaded=0, PartiallyShaded=1, Wireframe=2),
        ),
        "UF": NS(UFSession=NS(GetUFSession=lambda: uf)),
        "Display": NS(
            DynamicSectionTypes=NS(
                Type=NS(OnePlane=1), CoordinateSystem=NS(Absolute=1), Clip=NS(Section=1)
            )
        ),
        "Gateway": NS(
            ImageExportBuilder=NS(
                FileFormats=NS(Png=1),
                BackgroundOptions=NS(CustomColor=1, Original=2, Transparent=3),
            )
        ),
        "GeometricAnalysis": NS(
            SimpleInterference=NS(
                InterferenceMethod=NS(InterferenceSolid=1),
                Result=NS(InterferenceExists=1, OnlyEdgesOrFacesInterfere=2, NoInterference=3),
            )
        ),
    }
    monkeypatch.setitem(__import__("sys").modules, "NXOpen", nx)
    for name, mod in modules.items():
        setattr(nx, name, mod)
        monkeypatch.setitem(__import__("sys").modules, "NXOpen." + name, mod)
    modifications = []

    def modification():
        m = NS(Dispose=Mock())

        def apply(values):
            for obj in values:
                for target in [obj] + (obj.GetFaces() if isinstance(obj, Body) else []):
                    if hasattr(m, "NewColor"):
                        target.Color = m.NewColor
                    if hasattr(m, "NewTranslucency"):
                        target.transparency = m.NewTranslucency

        m.Apply = Mock(side_effect=apply)
        modifications.append(m)
        return m

    session.DisplayManager = NS(NewDisplayModification=modification)
    return NS(e=e, session=session, part=part, nx=nx, uf=uf, ref=ref, modifications=modifications)
