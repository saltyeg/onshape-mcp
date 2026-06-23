"""cadkit devkit — quota-frugal helpers for developing/verifying against the live Onshape API.

The free tier allows **2,500 SUCCESSFUL (2xx/3xx) API calls per user per YEAR**
(Pro 5,000; Enterprise 10,000/full-user). Only successful calls count — `4xx`/`5xx`
are free. A `429` is a *separate* short-term per-endpoint burst limit (pace requests);
annual exhaustion returns `402`. See https://onshape-public.github.io/docs/auth/limits/.

So a debugging session must minimize *successful* calls. The expensive habits are:
creating a fresh part studio per test, posting features one at a time, and running a
separate FeatureScript eval per measured quantity. This module makes the cheap path easy:

  - `ScratchStudio` — create ONE studio for a whole session and reuse it (rollback/clear
    between variants) instead of one `create_part_studio` per test.
  - `measure_fs` / `parse_fs` — a SINGLE eval that returns *all* measurements at once,
    with the SOLID-body filter and correct axes baked in (see the gotchas below).
  - Cached invariants (`PLANES`, `ORIGIN_VERTEX`) so static facts are never re-fetched.
  - `CUBE_SELFTEST_FS` — validate the measurement harness against a known 1" cube before
    trusting it, so a broken ruler is caught on call #1 (not after chasing phantom bugs).

Measurement gotchas these helpers encode (each one previously caused a *false* "broken
geometry" conclusion):
  * Measure `qBodyType(qEverything(BODY), SOLID)`, NOT `qEverything(BODY)`: the latter
    includes the default planes (~±3"), which pollute every bounding box.
  * On the Front plane, sketch-Y maps to world-Z (sketch-X -> world-X). A Front-sketch
    vertex always has world-Y == 0, so never use world-Y to gauge sketch height.

Discover unfamiliar feature JSON in the browser FeatureScript console / Glassworks
explorer (zero API-key cost), and prefer fetching one authoritative `featurespec` over
trial-and-error POSTs.
"""
from typing import Any, Dict, List, Optional

PLANES = {"Front": "JCC", "Top": "JDC", "Right": "JEC"}
ORIGIN_VERTEX = "IB"
SOLID = "qBodyType(qEverything(EntityType.BODY), BodyType.SOLID)"


def measure_fs(*, sketch_fid: Optional[str] = None,
               variables: tuple = ()) -> str:
    """Build ONE FeatureScript function that returns every measurement in a single eval.

    Returns a map with (units stripped to inches / cubic inches):
      solidCount, solidVolume, solidMin[xyz], solidMax[xyz],
      [sketchMin/sketchMax if sketch_fid given], [var_<name> for each name in variables].

    One eval instead of N — measuring 5 quantities goes from 5 successful calls to 1.
    """
    lines = [
        "function(context is Context, queries){",
        f"  var sb = {SOLID};",
        "  var solids = evaluateQuery(context, sb);",
        "  var out = { \"solidCount\": size(solids) };",
        "  if (size(solids) > 0) {",
        "    var b = evBox3d(context, { \"topology\": sb });",
        "    out.solidMin = [b.minCorner[0]/inch, b.minCorner[1]/inch, b.minCorner[2]/inch];",
        "    out.solidMax = [b.maxCorner[0]/inch, b.maxCorner[1]/inch, b.maxCorner[2]/inch];",
        "    out.solidVolume = evVolume(context, { \"entities\": sb }) / (inch*inch*inch);",
        "  }",
    ]
    if sketch_fid:
        lines += [
            f'  var skq = qCreatedBy(makeId("{sketch_fid}"), EntityType.VERTEX);',
            "  if (size(evaluateQuery(context, skq)) > 0) {",
            "    var sbx = evBox3d(context, { \"topology\": skq });",
            "    out.sketchMin = [sbx.minCorner[0]/inch, sbx.minCorner[1]/inch, sbx.minCorner[2]/inch];",
            "    out.sketchMax = [sbx.maxCorner[0]/inch, sbx.maxCorner[1]/inch, sbx.maxCorner[2]/inch];",
            "  }",
        ]
    for name in variables:
        lines.append(f'  out["var_{name}"] = getVariable(context, "{name}");')
    lines += ["  return out;", "}"]
    return "\n".join(lines)


def parse_fs(node: Any) -> Any:
    """Convert an Onshape BTFSValue tree (map/array/number/string/bool/undefined) to plain Python.

    Handles the `{"btType": "...BTFSValueMap", "value": [ {key, value}, ... ]}` shape and
    `...WithUnits` wrappers, so callers get ordinary dicts/lists/floats.
    """
    if not isinstance(node, dict):
        return node
    bt = node.get("btType", "")
    if bt.endswith("BTFSValueMap"):
        return {parse_fs(e["key"]): parse_fs(e["value"]) for e in node.get("value", [])}
    if bt.endswith("BTFSValueArray"):
        return [parse_fs(v) for v in node.get("value", [])]
    if bt.endswith(("BTFSValueNumber", "BTFSValueString", "BTFSValueBoolean", "BTFSValueWithUnits")):
        return node.get("value")
    if bt.endswith("BTFSValueUndefined"):
        return None
    if "value" in node:
        return parse_fs(node["value"])
    return node


# Run this in a throwaway studio (1 build + 1 eval) ONCE per session to prove the
# measurement harness is sane before trusting any bbox. A correct harness returns
# solidMax ~ [1,1,1] and solidVolume ~ 1.0 for a 1" cube extruded on the Front plane.
CUBE_SELFTEST_FS = (
    'function(context is Context, queries){'
    f'  var sb = {SOLID};'
    '  var b = evBox3d(context, {"topology": sb});'
    '  return { "max": [b.maxCorner[0]/inch, b.maxCorner[1]/inch, b.maxCorner[2]/inch],'
    '           "vol": evVolume(context, {"entities": sb})/(inch*inch*inch) };'
    '}'
)


class ScratchStudio:
    """Reuse ONE part studio across many tests instead of creating one per test.

    Cost model: 1 `create_part_studio` for the whole session + 1 `clear()` per reset,
    versus 1 create per test. Combined with `measure_fs` (1 eval per test) this is the
    difference between a variant costing ~6 successful calls and ~2.

    Usage:
        scratch = await ScratchStudio.create(ps_mgr, doc, ws)
        await ps_mgr.add_feature(doc, ws, scratch.eid, sketch_json)
        ...measure via one eval...
        await scratch.clear()      # roll back to empty, reuse for the next variant
    """

    def __init__(self, ps_mgr, doc: str, ws: str, eid: str):
        self.ps, self.doc, self.ws, self.eid = ps_mgr, doc, ws, eid

    @classmethod
    async def create(cls, ps_mgr, doc: str, ws: str, name: str = "scratch"):
        r = await ps_mgr.create_part_studio(doc, ws, name)
        return cls(ps_mgr, doc, ws, r.get("id") or r.get("elementId"))

    async def clear(self) -> int:
        """Delete all non-default features so the studio can be reused. Returns count deleted.

        Each delete is one (cheap, often 2xx) call — still far fewer total successful calls
        than spawning a new studio per test once you account for the shared create + evals.
        Skip `clear()` entirely when a test is additive and you can just keep measuring.
        """
        feats = await self.ps.get_features(self.doc, self.ws, self.eid)
        deleted = 0
        for f in reversed(feats.get("features", [])):
            fid = f.get("featureId")
            if fid and not f.get("featureType") in ("origin", "defaultPlane"):
                await self.ps.delete_feature(self.doc, self.ws, self.eid, fid)
                deleted += 1
        return deleted
