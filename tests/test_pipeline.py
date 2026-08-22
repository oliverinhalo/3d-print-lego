"""End-to-end pipeline behaviour (spec sections 12, 17, 18, 20, 33)."""
import zipfile

import pytest

from app.models.job import Job, JobStatus
from app.models.part import PartStatus


async def run_job(worker, query="77263", set_num="77263-1") -> Job:
    job = Job(query=query, set_num=set_num)
    worker.jobs.register(job)
    await worker.run(job)
    return job


def events_of(worker, job, event_type):
    return [e for e in worker.jobs.channels[job.id].history if e["type"] == event_type]


class TestHappyPath:
    async def test_a_job_completes_and_produces_a_zip(self, worker):
        job = await run_job(worker)
        assert job.status is JobStatus.COMPLETE
        assert job.zip_path and job.zip_bytes > 0
        assert job.files_written == 5          # 3001 x2 + 3024 x3

    async def test_the_zip_holds_one_file_per_physical_piece(self, worker):
        job = await run_job(worker)
        with zipfile.ZipFile(job.zip_path) as zf:
            stls = [n for n in zf.namelist() if n.endswith(".stl")]
        assert len(stls) == 5

    async def test_every_part_ends_ready(self, worker):
        job = await run_job(worker)
        assert all(p.status in (PartStatus.READY, PartStatus.CACHED)
                   for p in job.parts.values())


class TestDeduplication:
    async def test_a_shape_is_converted_once_however_many_copies(
            self, worker, fake_set_provider, fake_model_provider):
        fake_set_provider.inventory = {"3001": 50}
        job = await run_job(worker)

        assert fake_model_provider.build_calls == ["3001"], "converted more than once"
        assert job.files_written == 50

    async def test_parts_sharing_a_shape_convert_once(
            self, worker, fake_set_provider, fake_model_provider):
        """A printed part and its plain parent are one geometry."""
        fake_set_provider.inventory = {"3001": 2, "3001pr0001": 3}
        fake_model_provider.known = {"3001"}
        job = await run_job(worker)

        assert fake_model_provider.build_calls == ["3001"]
        assert job.distinct_geometries == 1
        assert job.files_written == 5
        assert job.parts["3001pr0001"].geometry.model_id == "3001"

    async def test_a_second_job_reuses_the_cache(
            self, worker, fake_model_provider):
        await run_job(worker)
        calls_after_first = len(fake_model_provider.build_calls)
        second = await run_job(worker)

        assert len(fake_model_provider.build_calls) == calls_after_first
        assert all(p.status is PartStatus.CACHED for p in second.parts.values())


class TestFailureIsolation:
    async def test_one_missing_model_does_not_fail_the_job(
            self, worker, fake_set_provider, fake_model_provider):
        fake_set_provider.inventory = {"3001": 2, "3024": 3, "9999": 4}
        job = await run_job(worker)

        assert job.status is JobStatus.PARTIAL
        assert len(job.failed_parts) == 1
        assert job.failed_parts[0].part_num == "9999"
        assert job.zip_path, "successful parts must still be downloadable"

    async def test_successful_parts_are_retained(
            self, worker, fake_set_provider):
        fake_set_provider.inventory = {"3001": 2, "3024": 3, "9999": 4}
        job = await run_job(worker)
        with zipfile.ZipFile(job.zip_path) as zf:
            stls = [n for n in zf.namelist() if n.endswith(".stl")]
        assert len(stls) == 5           # the 9999 pieces are absent, rest present

    async def test_a_build_error_is_reported_against_that_part_only(
            self, worker, fake_set_provider, fake_model_provider):
        fake_set_provider.inventory = {"3001": 1, "3024": 1}
        fake_model_provider.failing = {"3024"}
        job = await run_job(worker)

        assert job.status is JobStatus.PARTIAL
        assert job.parts["3001"].status is PartStatus.READY
        assert job.parts["3024"].status is PartStatus.FAILED
        assert "unavailable" in job.parts["3024"].error

    async def test_transient_failures_are_retried(
            self, worker, fake_set_provider, fake_model_provider):
        fake_set_provider.inventory = {"3001": 1}
        fake_model_provider.fail_times = {"3001": 1}      # fails once, then works
        job = await run_job(worker)

        assert job.status is JobStatus.COMPLETE
        assert len(fake_model_provider.build_calls) == 2

    async def test_retry_recovers_previously_failed_parts(
            self, worker, fake_set_provider, fake_model_provider):
        fake_set_provider.inventory = {"3001": 2, "3024": 3}
        fake_model_provider.failing = {"3024"}
        job = await run_job(worker)
        assert job.status is JobStatus.PARTIAL

        fake_model_provider.failing = set()               # the source recovered
        worker.jobs.channels[job.id].closed = False
        await worker.retry_failed(job)

        assert job.status is JobStatus.COMPLETE
        assert not job.failed_parts
        with zipfile.ZipFile(job.zip_path) as zf:
            assert len([n for n in zf.namelist() if n.endswith(".stl")]) == 5

    async def test_an_unknown_set_fails_cleanly(self, worker):
        job = await run_job(worker, query="99999", set_num="99999-1")
        assert job.status is JobStatus.FAILED
        assert "not found" in job.error.lower()

    async def test_non_printable_rows_are_skipped(self, worker, fake_set_provider):
        fake_set_provider.inventory = {"3001": 1, "10118464": 1}
        fake_set_provider.names = {"10118464": "Sticker Sheet for Set 77263-1"}
        job = await run_job(worker)

        assert "10118464" not in job.parts
        assert job.status is JobStatus.COMPLETE


class TestProgressEvents:
    async def test_the_documented_events_are_emitted_in_order(self, worker):
        job = await run_job(worker)
        types = [e["type"] for e in worker.jobs.channels[job.id].history]

        for expected in ("job_started", "set_found", "inventory_loaded",
                         "parts_identified", "models_resolved",
                         "part_progress", "job_complete"):
            assert expected in types, f"missing {expected}"
        assert types.index("set_found") < types.index("parts_identified")
        assert types.index("parts_identified") < types.index("job_complete")

    async def test_part_progress_carries_the_documented_payload(self, worker):
        job = await run_job(worker)
        event = events_of(worker, job, "part_progress")[-1]

        assert event["part"]["part_num"]
        assert event["part"]["status"] in [s.value for s in PartStatus]
        assert isinstance(event["completed"], int)
        assert isinstance(event["total"], int)
        assert event["completed"] <= event["total"]

    async def test_every_stage_is_announced(self, worker):
        job = await run_job(worker)
        stages = [e["stage"] for e in events_of(worker, job, "stage")]
        for expected in ("finding_set", "loading_inventory", "identifying_parts",
                         "finding_models", "converting", "building_zip", "complete"):
            assert expected in stages

    async def test_set_details_arrive_before_generation_finishes(self, worker):
        job = await run_job(worker)
        history = worker.jobs.channels[job.id].history
        set_index = next(i for i, e in enumerate(history) if e["type"] == "set_found")
        done_index = next(i for i, e in enumerate(history) if e["type"] == "job_complete")
        assert set_index < done_index
        assert history[set_index]["set"]["name"] == "Test Set"

    async def test_a_subscriber_joining_late_receives_the_full_history(self, worker):
        job = await run_job(worker)
        received = [event async for event in worker.jobs.subscribe(job.id)]
        assert any(e["type"] == "set_found" for e in received)
        assert any(e["type"] == "job_complete" for e in received)

    async def test_failures_are_reported_in_the_completion_event(
            self, worker, fake_set_provider):
        fake_set_provider.inventory = {"3001": 1, "9999": 2}
        job = await run_job(worker)
        event = events_of(worker, job, "job_complete")[-1]

        assert len(event["failed_parts"]) == 1
        failed = event["failed_parts"][0]
        assert failed["part_num"] == "9999"
        assert failed["quantity"] == 2
        assert failed["error"]


class TestLimits:
    async def test_an_oversized_set_is_refused(self, worker, fake_set_provider):
        worker.settings.max_pieces_per_job = 10
        fake_set_provider.inventory = {"3001": 5000}
        job = await run_job(worker)

        assert job.status is JobStatus.FAILED
        assert "limit" in job.error.lower()

    async def test_concurrency_is_bounded(self, worker, fake_set_provider,
                                          fake_model_provider, monkeypatch):
        fake_set_provider.inventory = {str(3000 + i): 1 for i in range(30)}
        fake_model_provider.known = set(fake_set_provider.inventory)

        active = 0
        peak = 0
        original = fake_model_provider.build_stl

        def counting_build(model_id, destination):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                return original(model_id, destination)
            finally:
                active -= 1

        monkeypatch.setattr(fake_model_provider, "build_stl", counting_build)
        await run_job(worker)
        assert peak <= worker.settings.max_concurrent_downloads
