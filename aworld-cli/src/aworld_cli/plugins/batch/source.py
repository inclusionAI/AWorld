"""
Batch data source implementations.
"""
import csv
from pathlib import Path
from typing import List, Dict, Any
from aworld_cli._globals import console


class CsvBatchSource:
    """
    CSV file batch source.

    Reads records from CSV file and converts them to batch records.

    Example:
        >>> source = CsvBatchSource("eval.csv", query_column="query", encoding="utf-8")
        >>> records = await source.load()
        >>> print(f"Loaded {len(records)} records")
    """

    def __init__(
        self,
        file_path: str,
        query_column: str = "query",
        encoding: str = "utf-8",
        delimiter: str = ","
    ):
        """
        Initialize CSV batch source.

        Args:
            file_path: Path to CSV file
            query_column: Column name containing the query/prompt
            encoding: File encoding (default: utf-8)
            delimiter: CSV delimiter (default: ,)
        """
        self.file_path = Path(file_path)
        self.query_column = query_column
        self.encoding = encoding
        self.delimiter = delimiter

    async def load(self) -> List[Dict[str, Any]]:
        """
        Load all records from CSV file.

        Returns:
            List of records, each record is a dict with row data plus 'row_id' and 'query' fields

        Example:
            >>> records = await source.load()
            >>> print(records[0])
            {'row_id': '0', 'query': 'create a ppt about AI', ...}
        """
        if not self.file_path.exists():
            raise FileNotFoundError(f"📄 CSV file not found: {self.file_path}")

        records = []

        try:
            with open(self.file_path, "r", encoding=self.encoding) as f:
                reader = csv.DictReader(f, delimiter=self.delimiter)

                # Validate query_column exists
                if self.query_column not in reader.fieldnames:
                    raise ValueError(
                        f"❌ Query column '{self.query_column}' not found in CSV. "
                        f"Available columns: {', '.join(reader.fieldnames or [])}"
                    )

                for idx, row in enumerate(reader):
                    # Create record with row_id and all original data
                    record = {
                        "row_id": str(idx),
                        **row  # Include all original columns
                    }
                    records.append(record)

            console.print(f"✅ Loaded {len(records)} records from {self.file_path}")
            return records

        except Exception as e:
            console.print(f"[red]❌ Failed to load CSV: {e}[/red]")
            raise
