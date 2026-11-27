"""Live update hub and after-commit fan-out."""

from __future__ import annotations

import asyncio

from backend.services import live


def test_hub_publish_reaches_subscriber():
    async def run() -> None:
        queue = live.hub.subscribe({"DEMO"})
        try:
            live.hub.publish(
                project_key="demo",
                kind="issue",
                issue_key="DEMO-1",
                actor_id=1,
                fields=["status_id"],
            )
            await asyncio.sleep(0)
            event = await asyncio.wait_for(queue.get(), timeout=1)
            assert event["type"] == "change"
            assert event["project_key"] == "DEMO"
            assert event["issue_key"] == "DEMO-1"
            assert event["fields"] == ["status_id"]
        finally:
            live.hub.unsubscribe(queue)

    asyncio.run(run())


def test_hub_filters_other_projects():
    async def run() -> None:
        queue = live.hub.subscribe({"AAA"})
        try:
            live.hub.publish(project_key="BBB", kind="project", fields=["settings"])
            await asyncio.sleep(0)
            assert queue.empty()
        finally:
            live.hub.unsubscribe(queue)

    asyncio.run(run())


def test_queue_flushes_after_commit(admin):
    before = live.hub.subscriber_count

    async def run() -> None:
        queue = live.hub.subscribe({"ELEC"})
        try:
            patched = admin.put(
                "/api/projects/ELEC",
                json={"description": "live-sync"},
            )
            assert patched.status_code == 200, patched.text
            event = await asyncio.wait_for(queue.get(), timeout=2)
            assert event["kind"] == "project"
            assert event["project_key"] == "ELEC"
            assert "description" in event["fields"]
        finally:
            live.hub.unsubscribe(queue)

    asyncio.run(run())
    assert live.hub.subscriber_count == before
