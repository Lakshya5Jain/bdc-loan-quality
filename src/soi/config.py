"""Project-wide settings and paths."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

BDC_DATASET_BASE = (
    "https://www.sec.gov/files/datastandardsinnovation/data/"
    "business-development-company-bdc-data-sets/"
)
BDC_REPORT_URL = (
    "https://www.sec.gov/files/investment/data/other/"
    "business-development-company-report/business-development-company-{year}.csv"
)
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    sec_user_agent: str = "soi-research research@example.com"
    data_dir: Path = PROJECT_ROOT / "data"

    @property
    def raw_bdc_dir(self) -> Path:
        return self.data_dir / "raw" / "bdc"

    @property
    def raw_ref_dir(self) -> Path:
        return self.data_dir / "raw" / "ref"

    @property
    def raw_prices_dir(self) -> Path:
        return self.data_dir / "raw" / "prices"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "soi.duckdb"

    def ensure_dirs(self) -> None:
        for d in (self.raw_bdc_dir, self.raw_ref_dir, self.raw_prices_dir, self.parquet_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
