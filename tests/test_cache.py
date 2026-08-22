"""Persistent geometry cache (spec sections 12 and 13)."""
import pytest

from app.services.cache_service import CacheService


@pytest.fixture
def cache(db, settings):
    return CacheService(db, settings.cache_directory)


class TestCacheReuse:
    def test_a_shape_is_only_ever_built_once(self, cache, fake_model_provider):
        first = cache.get_or_build(fake_model_provider, "3001")
        second = cache.get_or_build(fake_model_provider, "3001")

        assert fake_model_provider.build_calls == ["3001"], "converted twice"
        assert first.from_cache is False
        assert second.from_cache is True
        assert first.path == second.path

    def test_repeated_requests_never_reconvert(self, cache, fake_model_provider):
        for _ in range(50):
            cache.get_or_build(fake_model_provider, "3001")
        assert len(fake_model_provider.build_calls) == 1

    def test_different_shapes_are_built_separately(self, cache, fake_model_provider):
        cache.get_or_build(fake_model_provider, "3001")
        cache.get_or_build(fake_model_provider, "3024")
        assert sorted(fake_model_provider.build_calls) == ["3001", "3024"]

    def test_a_second_job_reuses_the_first_job_cache(self, db, settings,
                                                    fake_model_provider):
        """The whole point: sets share parts, so nothing is downloaded twice."""
        CacheService(db, settings.cache_directory).get_or_build(fake_model_provider, "3001")
        fresh = CacheService(db, settings.cache_directory)   # a later job/process
        result = fresh.get_or_build(fake_model_provider, "3001")
        assert result.from_cache is True
        assert len(fake_model_provider.build_calls) == 1


class TestCacheMetadata:
    def test_metadata_is_recorded(self, cache, fake_model_provider):
        entry = cache.get_or_build(fake_model_provider, "3001")
        row = cache.db.query_one(
            "SELECT * FROM geometry_cache WHERE cache_key = ?", (entry.cache_key,))
        assert row["provider"] == "fake_model"
        assert row["model_id"] == "3001"
        assert row["source_version"] == "v1"
        assert row["triangles"] == 12
        assert len(row["sha256"]) == 64
        assert row["created_at"] > 0

    def test_a_new_source_version_invalidates_the_entry(self, cache, fake_model_provider):
        cache.get_or_build(fake_model_provider, "3001")
        fake_model_provider._version = "v2"          # upstream model changed
        result = cache.get_or_build(fake_model_provider, "3001")
        assert result.from_cache is False
        assert fake_model_provider.build_calls == ["3001", "3001"]

    def test_a_new_converter_version_invalidates_the_entry(self, cache,
                                                          fake_model_provider,
                                                          monkeypatch):
        cache.get_or_build(fake_model_provider, "3001")
        monkeypatch.setattr(type(fake_model_provider), "converter_version",
                            property(lambda self: "99"))
        assert cache.get_or_build(fake_model_provider, "3001").from_cache is False

    def test_a_deleted_file_is_rebuilt(self, cache, fake_model_provider):
        entry = cache.get_or_build(fake_model_provider, "3001")
        entry.path.unlink()
        assert cache.get_or_build(fake_model_provider, "3001").from_cache is False


class TestCacheSafety:
    @pytest.mark.parametrize("model_id", [
        "../escape", "../../etc/passwd", "a/b", "a\\b", "..",
    ])
    def test_paths_cannot_escape_the_cache_root(self, cache, model_id):
        path = cache.path_for("ldraw", model_id)
        assert str(path).startswith(str(cache.root.resolve()))

    def test_invalid_geometry_is_not_cached(self, cache, fake_model_provider, monkeypatch):
        def build_broken(model_id, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"not an stl")
            from app.providers.base import ModelResult
            return ModelResult(model_id, destination, 10, 0, (0, 0, 0), [], "v1")

        monkeypatch.setattr(fake_model_provider, "build_stl", build_broken)
        with pytest.raises(ValueError, match="validation failed"):
            cache.get_or_build(fake_model_provider, "3001")
        assert cache.db.query_one(
            "SELECT * FROM geometry_cache WHERE model_id = '3001'") is None


class TestCacheMaintenance:
    def test_stale_entries_are_purged(self, cache, fake_model_provider):
        entry = cache.get_or_build(fake_model_provider, "3001")
        with cache.db.connect() as conn:
            conn.execute("UPDATE geometry_cache SET last_used_at = 0")
        assert cache.purge_older_than(60) == 1
        assert not entry.path.exists()

    def test_fresh_entries_survive_a_purge(self, cache, fake_model_provider):
        cache.get_or_build(fake_model_provider, "3001")
        assert cache.purge_older_than(3600) == 0

    def test_stats_report_the_cache_contents(self, cache, fake_model_provider):
        cache.get_or_build(fake_model_provider, "3001")
        stats = cache.stats()
        assert stats["models"] == 1 and stats["bytes"] > 0
