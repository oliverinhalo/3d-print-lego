"""Application configuration.

Every setting can be overridden through the environment or a ``.env`` file.
See ``.env.example`` for documentation of each value.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root: backend/app/config.py -> backend/app -> backend -> root
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ENV_FILE", ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage ---
    database_url: str = "sqlite:///data/app.sqlite3"
    source_directory: Path = Path("data/sources")
    cache_directory: Path = Path("data/cache")
    output_directory: Path = Path("data/jobs")

    # --- generation ---
    max_concurrent_downloads: int = Field(default=8, ge=1, le=32)
    job_timeout: int = Field(default=1800, ge=30)
    job_retention: int = Field(default=86_400, ge=60)
    cache_retention: int = Field(default=2_592_000, ge=60)
    max_retries: int = Field(default=3, ge=1, le=10)
    max_pieces_per_job: int = Field(default=6000, ge=1)
    max_zip_bytes: int = Field(default=2_147_483_648, ge=1)

    # --- rate limiting ---
    rate_limit_requests: int = Field(default=10, ge=1)
    rate_limit_window: int = Field(default=60, ge=1)

    # --- geometry ---
    ldu_mm: float = Field(default=0.4, gt=0)
    part_scale: float = Field(default=1.0, gt=0)
    stl_binary: bool = True
    auto_orient: bool = True
    #: "native" keeps LDraw orientation (studs up); "flat" lays parts down.
    orient_strategy: str = "native"

    # --- build plates ---
    #: Printer whose bed size is used when packing plates. See BED_PRESETS.
    bed_preset: str = "bambu_p1"
    #: Gap left between parts on a plate, in millimetres.
    plate_gap_mm: float = Field(default=3.0, ge=0.0, le=20.0)
    #: Clear margin at the edge of the bed, in millimetres.
    plate_margin_mm: float = Field(default=5.0, ge=0.0, le=50.0)
    #: Default colour grouping: "none", "family" or "exact".
    color_mode: str = "family"
    #: Most filament colours a job may need. Similar colours are merged until
    #: no more than this many remain. 0 means no limit.
    max_colors: int = Field(default=4, ge=0, le=32)
    #: Write pre-arranged 3MF plate files into the download.
    build_plates: bool = True
    #: How plate files are delivered:
    #:   "separate" - one 3MF per plate (plain core 3MF, works everywhere)
    #:   "project"  - a single 3MF holding every plate, named by colour
    #:   "both"     - ship both, so either workflow is available
    #:
    #: Default is "separate": the per-plate files are plain core 3MF and are
    #: confirmed working, whereas the single-project format is Bambu-specific
    #: and still being verified against real slicer behaviour.
    plate_output: str = "separate"
    #: Also write one STL per physical piece. Off by default now that plates
    #: exist: a large set is hundreds of files that no slicer enjoys importing.
    include_stls: bool = False
    #: Refuse to build more than this many plates in one job.
    max_plates: int = Field(default=60, ge=1)

    # --- print estimates ---
    #: Slicing profile the filament/time estimate assumes. Change these to
    #: match your own profile, then the numbers track your real prints.
    layer_height_mm: float = Field(default=0.2, gt=0)
    wall_count: int = Field(default=2, ge=1, le=10)
    line_width_mm: float = Field(default=0.42, gt=0)
    infill_percent: float = Field(default=15.0, ge=0, le=100)
    #: PLA 1.24, PETG 1.27, ABS 1.04 g/cm3.
    filament_density: float = Field(default=1.24, gt=0)
    filament_price_per_kg: float = Field(default=20.0, ge=0)
    #: Average volumetric flow actually achieved. Calibrate from one real
    #: slice: if the slicer says half our time, double this.
    flow_rate_mm3_s: float = Field(default=8.0, gt=0)
    currency: str = "£"

    # --- providers ---
    set_provider: str = "rebrickable_csv"
    model_provider: str = "ldraw"
    rebrickable_api_key: str = ""

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"

    @field_validator("source_directory", "cache_directory", "output_directory")
    @classmethod
    def _absolutise(cls, value: Path) -> Path:
        """Resolve relative paths against the repository root, not the cwd."""
        return value if value.is_absolute() else (ROOT_DIR / value)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sqlite_path(self) -> Path:
        """Filesystem path behind ``database_url`` (sqlite URLs only)."""
        url = self.database_url
        if not url.startswith("sqlite:"):
            raise ValueError(f"Only sqlite:// URLs are supported, got {url!r}")
        raw = url.split("sqlite:///", 1)[-1] if "sqlite:///" in url else url.split("sqlite:", 1)[-1]
        path = Path(raw)
        return path if path.is_absolute() else (ROOT_DIR / path)

    @property
    def ldraw_dir(self) -> Path:
        return self.source_directory / "ldraw"

    @property
    def rebrickable_dir(self) -> Path:
        return self.source_directory / "rebrickable"

    def ensure_directories(self) -> None:
        for d in (self.source_directory, self.cache_directory, self.output_directory):
            d.mkdir(parents=True, exist_ok=True)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
