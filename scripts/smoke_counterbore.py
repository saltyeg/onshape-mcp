"""On-demand live smoke: native counterbore + countersink via cad_hole.

style='counterbore'/'countersink' drive Onshape's native Hole feature. Verified by the
geometry they leave: a counterbore shows two cylinder radii (bore + cbore); a countersink
shows the bore cylinder plus a cone. Budget: ~8 successful API calls.
"""
import asyncio
import json

from cadkit_mcp import server as S
from cadkit_mcp.devkit import parse_fs

DOC = "550b17de256c3edec2066d48"
WS = "53187ebf4f6319f9815dc853"
BORE, CBORE_D, CSINK_D = 0.25, 0.6, 0.55


async def call(tool, **a):
    r = await S.dispatch(tool, {"documentId": DOC, "workspaceId": WS, **a})
    t = r[0].text
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return t


async def main():
    elem = (await call("cad_part_studio_create", name="native holes smoke"))["elementId"]
    # plate on the Top plane, extruded +Z -> drilling from Top with up=true cuts down into it
    beg = await call("cad_sketch_begin", elementId=elem, plane="Top", name="plate")
    await call("cad_sketch_rectangle", elementId=elem, sessionId=beg["sessionId"],
               corner1=[-1.5, -1], corner2=[1.5, 1])
    close = await call("cad_sketch_close", elementId=elem, sessionId=beg["sessionId"])
    await call("cad_extrude", elementId=elem, sketchFeatureId=close["sketchFeatureId"],
               depth=1.0, operation="NEW", name="plate")

    cb = await call("cad_hole", elementId=elem, plane="Top", centers=[[-0.7, 0]], up=True,
                    diameter=BORE, depth=0.8, style="counterbore", cboreDiameter=CBORE_D, cboreDepth=0.3, name="CB")
    cs = await call("cad_hole", elementId=elem, plane="Top", centers=[[0.7, 0]], up=True,
                    diameter=BORE, depth=0.8, style="countersink", csinkDiameter=CSINK_D, csinkAngle=90, name="CS")

    solid = "qBodyType(qEverything(EntityType.BODY),BodyType.SOLID)"
    fs = ('function(context is Context, queries){ var cyl=[]; var cones=0;'
          f'for (var f in evaluateQuery(context, qGeometry(qOwnedByBody({solid}, EntityType.FACE), GeometryType.CYLINDER)))'
          '{ cyl=append(cyl, roundToPrecision(evSurfaceDefinition(context,{"face":f}).radius/inch, 4)); }'
          f'for (var f in evaluateQuery(context, qGeometry(qOwnedByBody({solid}, EntityType.FACE), GeometryType.CONE))) {{ cones=cones+1; }}'
          f'return {{"cyl": cyl, "cones": cones, "solids": size(evaluateQuery(context, {solid}))}};}}')
    g = parse_fs((await S.FS.evaluate(DOC, WS, elem, fs)).get("result", {}))
    radii = sorted(set(g.get("cyl") or []))

    ok = {"OK", "INFO", "WARNING"}  # native Hole regenerates as INFO (an informational note), not OK
    checks = [
        ("counterbore feature regenerated", cb.get("status") in ok),
        ("countersink feature regenerated", cs.get("status") in ok),
        ("one solid", g.get("solids") == 1),
        ("bore radius present", any(abs(r - BORE / 2) < 1e-3 for r in radii)),
        ("counterbore radius present", any(abs(r - CBORE_D / 2) < 1e-3 for r in radii)),
        ("countersink leaves a cone", (g.get("cones") or 0) >= 1),
    ]
    print("=== NATIVE HOLES SMOKE (counterbore + countersink) ===")
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"\ncounterbore -> {json.dumps(cb)}")
    print(f"countersink -> {json.dumps(cs)}")
    print(f"cyl radii {radii} | cones {g.get('cones')}")
    print(f"element  https://cad.onshape.com/documents/{DOC}/w/{WS}/e/{elem}")
    print("\nSMOKE:", "PASS ✅" if all(ok for _, ok in checks) else "FAIL ❌")


if __name__ == "__main__":
    asyncio.run(main())
