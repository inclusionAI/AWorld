"""
Batch result sink implementations.
"""
import csv
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from aworld_cli._globals import console


class CsvBatchSink:
    """
    CSV file batch sink.

    Writes batch results to CSV file.

    Example:
        >>> sink = CsvBatchSink("./result/output.csv")
        >>> await sink.write(result1)
        >>> await sink.write(result2)
        >>> await sink.finalize()
    """

    def __init__(
        self,
        file_path: str,
        encoding: str = "utf-8",
        delimiter: str = ","
    ):
        """
        Initialize CSV batch sink.

        Args:
            file_path: Path to output CSV file
            encoding: File encoding (default: utf-8)
            delimiter: CSV delimiter (default: ,)
        """
        self.file_path = Path(file_path)
        self.encoding = encoding
        self.delimiter = delimiter
        self.results: List[Dict[str, Any]] = []
        self._file_handle: Optional[Any] = None
        self._writer: Optional[csv.DictWriter] = None
        self._columns_written = False

    async def write(self, result: Dict[str, Any]) -> None:
        """
        Write a single result to the output file.

        Buffers results and writes them in batches for efficiency.
        For minimal first version, we'll append immediately.

        Args:
            result: Result dictionary with:
            - record_id: Record ID
            - success: Whether task succeeded
            - response: Agent response
            - error: Error message if failed
            - metrics: Optional metrics dict

        Example:
            >>> await sink.write({
            ...     "record_id": "0",
            ...     "success": True,
            ...     "response": "Generated PPT...",
            ...     "error": None
            ... })
        """
        self.results.append(result)

        # For minimal version, write header on first write
        if not self._columns_written:
            # Create parent directory if needed
            self.file_path.parent.mkdir(parents=True, exist_ok=True)

            # Determine columns based on first result
            columns = ["record_id", "success", "response", "error"]
            if result.get("metrics"):
                columns.extend(["cost", "tokens", "latency"])

            # Also include original record columns if available
            original_record = result.get("original_record", {})
            for key in original_record.keys():
                if key not in columns and key != "row_id":
                    columns.append(f"original_{key}")

            # Open file and write header
            self._file_handle = open(self.file_path, "w", encoding=self.encoding, newline="")
            self._writer = csv.DictWriter(self._file_handle, fieldnames=columns, delimiter=self.delimiter)
            self._writer.writeheader()
            self._columns_written = True

        # Prepare row data
        row = {
            "record_id": result.get("record_id", ""),
            "success": str(result.get("success", False)),
            "response": str(result.get("response", "")),
            "error": str(result.get("error", "")) if result.get("error") else ""
        }

        # Add metrics if available
        metrics = result.get("metrics", {})
        if metrics:
            row["cost"] = str(metrics.get("cost", 0.0))
            row["tokens"] = str(metrics.get("tokens", 0))
            row["latency"] = str(metrics.get("latency", 0.0))

        # Add original record columns
        original_record = result.get("original_record", {})
        for key, value in original_record.items():
            if key != "row_id":
                row[f"original_{key}"] = str(value) if value is not None else ""

        # Write row
        if self._writer:
            self._writer.writerow(row)
            self._file_handle.flush()  # Ensure immediate write

    async def finalize(self) -> None:
        """
        Finalize sink, close file handles and write summary.

        Example:
            >>> await sink.finalize()
        """
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
            self._writer = None

        console.print(f"✅ Results written to {self.file_path}")

    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics of written results.

        Returns:
            Summary dict with total, success count, failure count

        Example:
            >>> summary = sink.get_summary()
            >>> print(f"Success rate: {summary['success_count'] / summary['total'] * 100}%")
        """
        total = len(self.results)
        success_count = sum(1 for r in self.results if r.get("success", False))
        failure_count = total - success_count

        return {
            "total": total,
            "success_count": success_count,
            "failure_count": failure_count,
            "output_path": str(self.file_path)
        }
