"""Native Windows bridge interruption, idempotency and manual-handoff acceptance.

Run beside NX with NX_BRIDGE_DESCRIPTOR and NX_VALIDATION_OUTPUT set. The token
is read locally and never included in receipts. Only disposable parts mutate.
A committed receipt is retained for verification after a real bridge restart.
"""

import asyncio
import json
import os
import socket
import traceback
import uuid
from pathlib import Path

from nx_mcp.bridge import DescriptorBridgeClient
from nx_mcp.runtime import NXToolError


async def main():
    descriptor = Path(os.environ["NX_BRIDGE_DESCRIPTOR"])
    output = Path(os.environ["NX_VALIDATION_OUTPUT"])
    output.mkdir(parents=True, exist_ok=True)
    client = DescriptorBridgeClient(descriptor)
    receipt = {"checks": []}
    prefix = str(Path(os.environ["NX_WORKSPACE"]) / ("transport-recovery-" + uuid.uuid4().hex[:8]))

    def save():
        (output / "transport-recovery.json").write_text(json.dumps(receipt, indent=2))

    async def call(method, **params):
        return await client.call(method, params)

    async def reject(method, **params):
        try:
            await call(method, **params)
        except NXToolError as error:
            return error.code
        raise AssertionError(f"{method} unexpectedly succeeded")

    before = (await call("nx_list_open_parts"))["parts"]
    assert not any(p["modified"] for p in before), "Save existing parts first"
    original = next(p for p in before if p["work"])
    display = next(p for p in before if p["display"])
    try:
        await call("nx_create_part", path=prefix + "/seed.prt")
        sketch = (await call("nx_create_sketch"))["object"]["id"]
        await call(
            "nx_sketch_rectangle",
            sketch_id=sketch,
            corner1={"x": 0, "y": 0},
            corner2={"x": 10, "y": 10},
        )
        await call("nx_finish_sketch", sketch_id=sketch)
        await call("nx_extrude", sketch_id=sketch, distance=10)
        await call("nx_save_part")
        await call("nx_create_part", path=prefix + "/assembly.prt")
        await call("nx_add_component", part_path=prefix + "/seed.prt", name="SEED")
        await call("nx_save_part")
        component = (await call("nx_list_components"))["components"][0]["object"]["id"]
        operation_id = "disconnect_" + uuid.uuid4().hex
        params = {"component": component, "dx": 7, "operation_id": operation_id}
        d = json.loads(descriptor.read_text())
        request = {
            "jsonrpc": "2.0",
            "protocol_version": 1,
            "id": uuid.uuid4().hex,
            "token": d["token"],
            "method": "nx_reposition_component",
            "params": params,
        }
        with socket.create_connection((d["host"], d["port"]), timeout=10) as connection:
            connection.sendall(json.dumps(request).encode() + b"\n")
            # Deliberately abandon the response. No mutation is retried until its
            # durable receipt demonstrates whether it committed.
        state = await call("nx_operation_status", operation_id=operation_id)
        assert state["state"] == "committed", state
        replay = await call("nx_reposition_component", **params)
        assert replay["replayed"]
        pose = (await call("nx_list_components"))["components"][0]["translation"]
        assert pose == [7, 0, 0], pose
        conflict = await reject("nx_reposition_component", **{**params, "dx": 8})
        assert conflict == "NX_IDEMPOTENCY_CONFLICT", conflict
        receipt.update(
            operation_id=operation_id, fixture=prefix, committed=state, replayed=True, pose=pose
        )
        receipt["checks"].append("disconnect_committed_receipt_exact_retry_no_double_move")
        save()
        await call("nx_save_part")
        checkpoint = await call("nx_checkpoint", label="placement rollback")
        await call("nx_reposition_component", component=component, dx=3)
        await call("nx_get_bounding_box", scope="assembly")
        await call("nx_rollback", checkpoint_id=checkpoint["checkpoint_id"])
        fresh = (await call("nx_list_components"))["components"][0]
        assert fresh["translation"] == [7, 0, 0]
        receipt["checks"].append("readonly_keeps_checkpoint_rollback_restores_pose")
        await call("nx_save_part")
        stale = fresh["object"]["id"]
        await call("nx_ui_control", mode="manual")
        assert await reject("nx_reposition_component", component=stale, dx=1) == "NX_UI_PAUSED"
        await call("nx_ui_control", mode="agent")
        error = await reject("nx_reposition_component", component=stale, dx=1)
        assert "STALE" in error, error
        fresh = (await call("nx_list_components"))["components"][0]
        assert fresh["translation"] == [7, 0, 0]
        receipt["checks"].append("manual_blocks_mutation_resumption_rejects_stale_reference")
        receipt["passed"] = True
    except Exception:
        receipt["error"] = traceback.format_exc()
        raise
    finally:
        await call("nx_ui_control", mode="agent")
        parts = (await call("nx_list_open_parts"))["parts"]
        copies = [p for p in parts if prefix in p["path"]]
        copies.sort(key=lambda p: "assembly.prt" not in p["path"])
        for part in copies:
            live = (await call("nx_list_open_parts"))["parts"]
            current = next((p for p in live if p["path"] == part["path"]), None)
            if current:
                await call("nx_close_part", part=current["part"]["id"], save=True)
        await call("nx_open_part", path=display["path"], work=False, display=True)
        await call("nx_open_part", path=original["path"], work=True, display=False)
        after = (await call("nx_list_open_parts"))["parts"]
        receipt["session_restored"] = {p["path"] for p in before} == {p["path"] for p in after}
        assert receipt["session_restored"] and not any(p["modified"] for p in after)
        save()
    print(json.dumps({"passed": receipt["passed"], "checks": receipt["checks"]}))


if __name__ == "__main__":
    asyncio.run(main())
