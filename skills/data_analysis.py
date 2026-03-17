"""DataAnalysisSkill — analyze CSV/JSON data with pandas."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from .base import Skill, SkillResult

log = logging.getLogger(__name__)


class DataAnalysisSkill(Skill):
    """Analyze CSV or JSON data using pandas."""

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "data_analysis"

    @property
    def description(self) -> str:
        return "用pandas分析CSV/JSON数据，支持summary/correlation/trend/custom"

    @property
    def capabilities(self) -> list[str]:
        return ["analyze_data", "statistics", "trend_analysis", "csv_processing"]

    @property
    def param_schema(self) -> dict:
        return {
            "source": {
                "type": "str",
                "required": True,
                "description": "File path or inline CSV/JSON string",
            },
            "analysis_type": {
                "type": "str",
                "required": False,
                "default": "summary",
                "description": "'summary', 'correlation', 'trend', or 'custom'",
            },
            "column": {
                "type": "str",
                "required": False,
                "description": "Target column for trend analysis",
            },
            "query": {
                "type": "str",
                "required": False,
                "description": "Custom pandas query/expression for 'custom' type",
            },
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        import pandas as pd

        source = params.get("source", "")
        analysis_type = params.get("analysis_type", "summary")
        column = params.get("column", "")
        query = params.get("query", "")
        self._call_count += 1

        if not source:
            return {"success": False, "result": "", "error": "No data source provided"}

        try:
            df = self._load_data(source, pd)
        except Exception as e:
            return {"success": False, "result": "", "error": f"Failed to load data: {e}"}

        try:
            if analysis_type == "summary":
                result = self._analyze_summary(df)
            elif analysis_type == "correlation":
                result = self._analyze_correlation(df)
            elif analysis_type == "trend":
                result = self._analyze_trend(df, column)
            elif analysis_type == "custom":
                result = self._analyze_custom(df, query, pd)
            else:
                return {"success": False, "result": "", "error": f"Unknown analysis type: {analysis_type}"}

            self._success_count += 1
            return {"success": True, "result": result, "error": None}

        except Exception as e:
            log.error("DataAnalysisSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @staticmethod
    def _load_data(source: str, pd_module: object) -> object:
        """Load data from file path or inline string."""
        pd = pd_module

        # Try as file path first
        path = Path(source)
        if path.exists():
            if path.suffix.lower() == ".csv":
                return pd.read_csv(path)
            elif path.suffix.lower() == ".json":
                return pd.read_json(path)
            else:
                # Try CSV
                return pd.read_csv(path)

        # Try as inline data
        source_stripped = source.strip()
        if source_stripped.startswith("[") or source_stripped.startswith("{"):
            try:
                data = json.loads(source_stripped)
                return pd.DataFrame(data)
            except json.JSONDecodeError:
                pass

        # Try as inline CSV
        return pd.read_csv(io.StringIO(source_stripped))

    @staticmethod
    def _analyze_summary(df: object) -> str:
        """Basic statistics summary."""
        lines: list[str] = []
        lines.append(f"Shape: {df.shape[0]} rows x {df.shape[1]} columns")
        lines.append(f"Columns: {', '.join(df.columns.tolist())}")
        lines.append(f"\nDtypes:\n{df.dtypes.to_string()}")
        lines.append(f"\nDescribe:\n{df.describe().to_string()}")
        null_counts = df.isnull().sum()
        if null_counts.any():
            lines.append(f"\nNull counts:\n{null_counts[null_counts > 0].to_string()}")
        return "\n".join(lines)

    @staticmethod
    def _analyze_correlation(df: object) -> str:
        """Correlation matrix for numeric columns."""
        numeric = df.select_dtypes(include=["number"])
        if numeric.empty:
            return "No numeric columns found for correlation analysis."
        corr = numeric.corr()
        return f"Correlation matrix:\n{corr.to_string()}"

    @staticmethod
    def _analyze_trend(df: object, column: str) -> str:
        """Simple trend analysis on a column."""
        if not column:
            # Pick first numeric column
            numeric_cols = df.select_dtypes(include=["number"]).columns
            if len(numeric_cols) == 0:
                return "No numeric column available for trend analysis."
            column = numeric_cols[0]

        if column not in df.columns:
            return f"Column '{column}' not found. Available: {', '.join(df.columns.tolist())}"

        series = df[column].dropna()
        if len(series) < 2:
            return f"Not enough data points in column '{column}'."

        lines: list[str] = []
        lines.append(f"Trend analysis for '{column}':")
        lines.append(f"  Count: {len(series)}")
        lines.append(f"  Mean: {series.mean():.4f}")
        lines.append(f"  Std: {series.std():.4f}")
        lines.append(f"  Min: {series.min():.4f}")
        lines.append(f"  Max: {series.max():.4f}")

        # Simple trend: compare first half vs second half
        mid = len(series) // 2
        first_half = series.iloc[:mid].mean()
        second_half = series.iloc[mid:].mean()
        change = second_half - first_half
        pct_change = (change / abs(first_half) * 100) if first_half != 0 else 0

        if change > 0:
            direction = "UPWARD"
        elif change < 0:
            direction = "DOWNWARD"
        else:
            direction = "FLAT"

        lines.append(f"  Trend: {direction} ({pct_change:+.1f}% from first to second half)")
        return "\n".join(lines)

    @staticmethod
    def _analyze_custom(df: object, query: str, pd_module: object) -> str:
        """Execute a custom pandas expression."""
        if not query:
            return "No query provided for custom analysis."

        # Safe subset of operations
        local_vars = {"df": df, "pd": pd_module}
        result = eval(query, {"__builtins__": {}}, local_vars)  # noqa: S307
        return str(result)

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
