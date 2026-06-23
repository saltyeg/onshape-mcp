"""Offline builder tests for cadkit — assert on the JSON the builders EMIT.

These run with **zero API calls** (the expensive, quota-bounded behaviors live in the
on-demand live smoke test, not here). They guard the class of bug that actually cost a
debugging session: a wrong/hidden parameterId that produces plausible-but-dead output.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cadkit_mcp.sketch import SketchSession, ORIGIN_VERTEX  # noqa: E402
from cadkit_mcp import server as S  # noqa: E402  (imports a client but makes no network call)


def _session() -> SketchSession:
    return SketchSession("d", "w", "e", "Front", "t")


# ---- SketchSession (pure, no client) --------------------------------------
def test_ground_origin_uses_external_origin_vertex():
    s = _session(); l = s.add_line((0, 0), (2, 0)); s.ground_origin(f"{l}.start")
    con = s.constraints[-1]
    assert con["constraintType"] == "COINCIDENT"
    q = con["parameters"][0]
    assert q["btType"].startswith("BTMParameterQueryList")
    assert ORIGIN_VERTEX in q["queries"][0]["deterministicIds"]


def test_diagnostics_flags_ungrounded_and_undimensioned():
    s = _session(); s.add_line((0, 0), (2, 0))
    d = s.diagnostics()
    assert d["grounded"] is False and d["dimensions"] == 0 and d["wellFormed"] is False


def test_diagnostics_wellformed_when_grounded_and_dimensioned():
    s = _session(); l = s.add_line((0, 0), (2, 0))
    s.ground_origin(f"{l}.start"); s.dim_length(l, "#leg")
    d = s.diagnostics()
    assert d["grounded"] and d["dimensions"] == 1 and d["wellFormed"]


def test_dim_length_accepts_variable_expression():
    s = _session(); l = s.add_line((0, 0), (2, 0)); s.dim_length(l, "#leg_len")
    q = [p for p in s.constraints[-1]["parameters"] if p.get("parameterId") == "length"][0]
    assert q["expression"] == "#leg_len"


# ---- server JSON builders (pure functions) --------------------------------
def test_assign_variable_uses_anyValue_not_hidden_value():
    # REGRESSION: the original bug emitted parameterId "value" — an AlwaysHidden/legacy field
    # that silently fails to evaluate. The value must live in anyValue with variableType ANY.
    feat = S._assign_variable_json("w", "2 in")["feature"]
    pids = {p["parameterId"] for p in feat["parameters"]}
    assert "anyValue" in pids and "value" not in pids
    vt = [p for p in feat["parameters"] if p["parameterId"] == "variableType"][0]
    assert vt["value"] == "ANY"
    assert "featureId" not in feat  # create form omits featureId


def test_assign_variable_update_embeds_featureId():
    feat = S._assign_variable_json("w", "2 in", "FID123")["feature"]
    assert feat["featureId"] == "FID123"  # update form must carry the id


def test_scalar_expr_number_and_passthrough():
    assert S._scalar_expr(1.5) == "1.5 in"
    assert S._scalar_expr("#width") == "#width"


def test_extrude_depth_accepts_expression():
    j = S._extrude_json("FSK", "#width", "NEW", "x")["feature"]
    depth = [p for p in j["parameters"] if p["parameterId"] == "depth"][0]
    assert depth["expression"] == "#width"


def test_fillet_radius_expression_and_edges():
    j = S._fillet_json(["JHN"], "#r", "f")["feature"]
    rad = [p for p in j["parameters"] if p["parameterId"] == "radius"][0]
    assert rad["expression"] == "#r"
    ents = [p for p in j["parameters"] if p["parameterId"] == "entities"][0]
    assert ents["queries"][0]["deterministicIds"] == ["JHN"]
