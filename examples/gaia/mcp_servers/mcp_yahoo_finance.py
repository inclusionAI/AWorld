import logging
import sys
import traceback
from typing import Any, Dict, List, Optional, Union

import pandas as pd
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# Import yfinance for Yahoo Finance data
try:
    import yfinance as yf
except ImportError:
    logging.error("yfinance library is not installed. Please install it by running: pip install yfinance")
    yf = None

# Initialize logger (similar to pdf_mcp_server.py)
# You might want to use a shared logging setup if you have one in aworld.logs.util
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# pylint: disable=W0707
# Initialize MCP server
mcp = FastMCP("yahoo-finance-server")


@mcp.tool(description="Fetches the current stock quote for a given symbol.")
async def get_stock_quote(
    symbol: str = Field(description="The stock ticker symbol (e.g., AAPL, MSFT)."),
) -> Dict[str, Any]:
    """
    Fetches the current stock quote for a given symbol from Yahoo Finance.

    Args:
        symbol: The stock ticker symbol.

    Returns:
        A dictionary containing key quote information such as current price,
        previous close, open, day's high/low, volume, 52-week high/low, etc.
        Returns a concise summary for LLM-friendliness.

    Raises:
        RuntimeError: If yfinance library is not installed or if data cannot be fetched.
        ValueError: If the symbol is invalid or no data is found.
    """
    if yf is None:
        raise RuntimeError("yfinance library is not installed.")

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info  # Fetches a lot of data

        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            # Sometimes 'regularMarketPrice' is not available for all symbols/markets
            # 'currentPrice' can be an alternative for some ETFs or indices
            # If both are None, it's likely an issue or the symbol is delisted/invalid for quotes
            logger.warning(f"Could not retrieve current price for symbol: {symbol}. Info: {info}")
            # Attempt to get basic history to see if the ticker is valid at all
            hist = ticker.history(period="1d")
            if hist.empty:
                raise ValueError(f"No data found for symbol: {symbol}. It might be invalid or delisted.")
            # If history exists but no 'info' price, it's a partial data scenario
            # For now, we'll treat this as an error for get_stock_quote
            raise ValueError(f"Could not retrieve detailed quote for symbol: {symbol}. Limited data available.")

        # Select key information for LLM-friendliness
        quote_summary = {
            "symbol": symbol.upper(),
            "companyName": info.get("shortName", info.get("longName")),
            "currentPrice": info.get("regularMarketPrice", info.get("currentPrice")),
            "previousClose": info.get("previousClose"),
            "open": info.get("regularMarketOpen", info.get("open")),
            "dayHigh": info.get("regularMarketDayHigh", info.get("dayHigh")),
            "dayLow": info.get("regularMarketDayLow", info.get("dayLow")),
            "volume": info.get("regularMarketVolume", info.get("volume")),
            "averageVolume": info.get("averageVolume"),
            "marketCap": info.get("marketCap"),
            "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
            "currency": info.get("currency"),
            "exchange": info.get("exchange"),
        }
        # Filter out None values for a cleaner response
        return {k: v for k, v in quote_summary.items() if v is not None}

    except ValueError as ve:
        logger.error(f"Value error for symbol {symbol}: {ve}")
        raise  # Re-raise the specific ValueError
    except Exception as e:
        logger.error(f"Error fetching stock quote for {symbol}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Failed to fetch stock quote for {symbol}: {str(e)}")


@mcp.tool(description="Retrieves historical stock data (OHLCV) for a given symbol.")
async def get_historical_data(
    symbol: str = Field(description="The stock ticker symbol (e.g., AAPL, MSFT)."),
    start: str = Field(
        description="Download start date string (YYYY-MM-DD) or _datetime, inclusive. Default is 99 years ago."
    ),
    end: str = Field(description="Download end date string (YYYY-MM-DD) or _datetime, exclusive. Default is now."),
    interval: str = Field(
        default="1d",
        description="The interval of data points "
        "(e.g., '1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h', '1d', '5d', '1wk', '1mo', '3mo').",
    ),
    max_rows_preview: Optional[int] = Field(
        default=10,  # Show first 5 and last 5 rows for preview if data is large
        description="Max number of rows to return in the preview if data is extensive. "
        "Set to 0 for all data (can be large).",
    ),
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Retrieves historical stock data (Open, High, Low, Close, Volume) for a given symbol.

    To be LLM-friendly, if the dataset is large (more than `max_rows_preview` if specified and > 0),
    it returns a summary dictionary containing the first few and last few data points,
    total rows, and a message. Otherwise, it returns the full list of data points.

    Args:
        symbol: The stock ticker symbol.
        period: The period for which to fetch data.
        interval: The interval of data points.
        max_rows_preview: Max rows for preview if data is large. If 0, returns all data.

    Returns:
        A list of dictionaries, where each dictionary represents a data point (date, open, high, low, close, volume),
        or a summary dictionary if the data is extensive and max_rows_preview is used.

    Raises:
        RuntimeError: If yfinance library is not installed or if data cannot be fetched.
        ValueError: If the symbol is invalid, or no data is found for the given parameters.
    """
    if yf is None:
        raise RuntimeError("yfinance library is not installed.")

    try:
        ticker = yf.Ticker(symbol)
        hist_df = ticker.history(start=start, end=end, interval=interval)

        if hist_df.empty:
            raise ValueError(
                f"No historical data found for symbol {symbol} with {start=} {end=} and interval {interval}."
            )

        # Convert DataFrame to list of dictionaries
        hist_df.reset_index(inplace=True)
        # Ensure 'Date' or 'Datetime' is a string for JSON serialization
        if "Date" in hist_df.columns:
            hist_df["Date"] = hist_df["Date"].astype(str)
        if "Datetime" in hist_df.columns:  # For intraday data
            hist_df["Datetime"] = hist_df["Datetime"].astype(str)

        # Rename columns to be more standard (e.g., remove spaces)
        hist_df.columns = hist_df.columns.str.replace(" ", "")  # Remove spaces from column names

        historical_data = hist_df.to_dict(orient="records")

        if max_rows_preview is not None and max_rows_preview > 0 and len(historical_data) > max_rows_preview:
            preview_count = max_rows_preview // 2
            if preview_count == 0:
                preview_count = 1  # ensure at least one row from start/end if max_rows_preview is 1 or 2

            summary_data = {
                "message": f"Historical data is extensive ({len(historical_data)} rows). Showing a preview.",
                "total_rows": len(historical_data),
                "start": start,
                "end": end,
                "interval": interval,
                "data_preview_start": historical_data[:preview_count],
                "data_preview_end": historical_data[-preview_count:],
            }
            return summary_data
        else:
            return historical_data

    except ValueError as ve:
        logger.error(f"Value error for historical data {symbol}: {ve}")
        raise
    except Exception as e:
        logger.error(f"Error fetching historical data for {symbol}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Failed to fetch historical data for {symbol}: {str(e)}")


@mcp.tool(description="Provides key information about a company (e.g., sector, industry, business summary).")
async def get_company_info(
    symbol: str = Field(description="The stock ticker symbol (e.g., AAPL, MSFT)."),
) -> Dict[str, Any]:
    """
    Provides key information about a company from Yahoo Finance.

    Args:
        symbol: The stock ticker symbol.

    Returns:
        A dictionary containing company information like sector, industry, fullTimeEmployees,
        longBusinessSummary, city, state, country, website, etc.
        Returns a curated set for LLM-friendliness.

    Raises:
        RuntimeError: If yfinance library is not installed or if data cannot be fetched.
        ValueError: If the symbol is invalid or no info is found.
    """
    if yf is None:
        raise RuntimeError("yfinance library is not installed.")

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        if not info or not info.get("symbol"):
            raise ValueError(f"No company information found for symbol: {symbol}. It might be invalid.")

        # Select key information for LLM-friendliness
        company_details = {
            "symbol": info.get("symbol"),
            "shortName": info.get("shortName"),
            "longName": info.get("longName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "fullTimeEmployees": info.get("fullTimeEmployees"),
            "longBusinessSummary": info.get("longBusinessSummary"),
            "city": info.get("city"),
            "state": info.get("state"),
            "country": info.get("country"),
            "website": info.get("website"),
            "exchange": info.get("exchange"),
            "currency": info.get("currency"),
            "marketCap": info.get("marketCap"),
        }
        # Filter out None values
        return {k: v for k, v in company_details.items() if v is not None}

    except ValueError as ve:
        logger.error(f"Value error for company info {symbol}: {ve}")
        raise
    except Exception as e:
        logger.error(f"Error fetching company info for {symbol}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Failed to fetch company info for {symbol}: {str(e)}")


@mcp.tool(description="Fetches financial statements (income, balance sheet, cash flow) for a company.")
async def get_financial_statements(
    symbol: str = Field(description="The stock ticker symbol (e.g., AAPL, MSFT)."),
    statement_type: str = Field(
        description="Type of financial statement to fetch.", enum=["income_statement", "balance_sheet", "cash_flow"]
    ),
    period_type: str = Field(
        default="annual",
        description="Periodicity of the statement ('annual' or 'quarterly').",
        enum=["annual", "quarterly"],
    ),
    max_columns_preview: Optional[int] = Field(
        default=4,  # Show most recent 4 columns for preview if data is large
        description="Max number of periods/columns to return in the preview. "
        "Set to 0 for all available (can be large).",
    ),
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Fetches financial statements for a company (Income Statement, Balance Sheet, or Cash Flow).

    Args:
        symbol: The stock ticker symbol.
        statement_type: 'income_statement', 'balance_sheet', or 'cash_flow'.
        period_type: 'annual' or 'quarterly'.
        max_columns_preview: Max number of recent periods to show. 0 for all.

    Returns:
        A list of dictionaries, where each dictionary represents a row in the statement,
        with keys as period dates and 'Item' for the financial item name.
        Or a summary dictionary if data is extensive and max_columns_preview is used.

    Raises:
        RuntimeError: If yfinance library is not installed or if data cannot be fetched.
        ValueError: If parameters are invalid or no data is found.
    """
    if yf is None:
        raise RuntimeError("yfinance library is not installed.")

    try:
        ticker = yf.Ticker(symbol)
        statement_df = None

        if statement_type == "income_statement":
            statement_df = ticker.income_stmt if period_type == "annual" else ticker.quarterly_income_stmt
        elif statement_type == "balance_sheet":
            statement_df = ticker.balance_sheet if period_type == "annual" else ticker.quarterly_balance_sheet
        elif statement_type == "cash_flow":
            statement_df = ticker.cashflow if period_type == "annual" else ticker.quarterly_cashflow
        else:
            raise ValueError(
                f"Invalid statement_type: {statement_type}. "
                "Must be one of 'income_statement', 'balance_sheet', 'cash_flow'."
            )

        if statement_df is None or statement_df.empty:
            raise ValueError(f"No {period_type} {statement_type} data found for symbol {symbol}.")

        # Transpose for easier LLM consumption (items as rows, dates as columns)
        # yfinance already returns it in a somewhat friendly format (index is items, columns are dates)
        statement_df.reset_index(inplace=True)
        statement_df.rename(columns={"index": "Item"}, inplace=True)

        # Convert date columns to string for JSON serialization
        for col in statement_df.columns:
            if col != "Item":  # Assuming other columns are date-like or numeric
                try:
                    # Attempt to convert to string if it's a datetime-like object
                    if hasattr(col, "strftime"):
                        statement_df.rename(columns={col: col.strftime("%Y-%m-%d")}, inplace=True)
                except Exception:
                    # If conversion fails, keep original column name (might be already string or int)
                    pass

        # Convert the DataFrame to a list of dictionaries (each dict is a row/financial item)
        statement_data = statement_df.to_dict(orient="records")

        if (
            max_columns_preview is not None
            and max_columns_preview > 0
            and len(statement_df.columns) > (max_columns_preview + 1)
        ):  # +1 for 'Item' column
            # Select 'Item' and the most recent 'max_columns_preview' columns
            columns_to_keep = ["Item"] + list(statement_df.columns[1 : max_columns_preview + 1])
            preview_df = statement_df[columns_to_keep]
            preview_data = preview_df.to_dict(orient="records")

            summary_response = {
                "message": (
                    f"{statement_type} data is extensive. ",
                    f"Showing a preview of the most recent {max_columns_preview} periods.",
                ),
                "total_periods_available": len(statement_df.columns) - 1,
                "statement_type": statement_type,
                "period_type": period_type,
                "data_preview": preview_data,
            }
            return summary_response
        else:
            return statement_data

    except ValueError as ve:
        logger.error(f"Value error for financial statements {symbol}: {ve}")
        raise
    except Exception as e:
        logger.error(f"Error fetching {statement_type} for {symbol}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Failed to fetch {statement_type} for {symbol}: {str(e)}")


@mcp.tool(description="Searches for financial news relevant to a query or ticker symbol.")
async def search_news(
    symbol: str = Field(description="The stock ticker symbol (e.g., AAPL, MSFT) to get news for."),
    max_results: int = Field(default=5, description="Maximum number of news articles to return."),
) -> List[Dict[str, Any]]:
    """
    Searches for financial news for a given ticker symbol using yfinance.

    Args:
        symbol: The stock ticker symbol.
        max_results: Maximum number of news articles to return.

    Returns:
        A list of dictionaries, where each dictionary contains news article details
        (title, publisher, link, providerPublishTime, type, thumbnail (if available)).

    Raises:
        RuntimeError: If yfinance library is not installed or if news cannot be fetched.
        ValueError: If the symbol is invalid or no news is found.
    """
    if yf is None:
        raise RuntimeError("yfinance library is not installed.")

    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news

        if not news:
            raise ValueError(f"No news found for symbol: {symbol}.")

        # yfinance returns a list of news dictionaries directly
        # We need to ensure providerPublishTime is JSON serializable (it's often a Unix timestamp)
        # and select relevant fields for LLM-friendliness.
        formatted_news = []
        for article in news[:max_results]:
            formatted_article = {
                "title": article.get("title"),
                "publisher": article.get("publisher"),
                "link": article.get("link"),
                "providerPublishTime": pd.to_datetime(article.get("providerPublishTime"), unit="s").isoformat()
                if article.get("providerPublishTime")
                else None,
                "type": article.get("type"),
                # "summary": article.get("summary"), # Often not available or too long
                "thumbnail_url": article.get("thumbnail", {})
                .get("resolutions", [{}])[0]
                .get("url"),  # Safely access thumbnail
            }
            formatted_news.append({k: v for k, v in formatted_article.items() if v is not None})

        if not formatted_news:
            raise ValueError(f"No news found for symbol: {symbol} after formatting (or max_results was 0).")

        return formatted_news

    except ValueError as ve:
        logger.error(f"Value error for news search {symbol}: {ve}")
        raise
    except Exception as e:
        logger.error(f"Error fetching news for {symbol}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Failed to fetch news for {symbol}: {str(e)}")


@mcp.tool(
    description="Provides a brief overview of the current state of major market indices "
    "(e.g., S&P 500, Dow Jones, Nasdaq)."
)
async def get_market_summary(
    indices: Optional[List[str]] = Field(
        default=["^GSPC", "^DJI", "^IXIC"],  # S&P 500, Dow Jones, Nasdaq Composite
        description="List of ticker symbols for market indices. Defaults to S&P 500, Dow Jones, Nasdaq.",
    ),
) -> List[Dict[str, Any]]:
    """
    Provides a brief summary of major market indices.

    Args:
        indices: A list of ticker symbols for the market indices.
                 Defaults to ["^GSPC", "^DJI", "^IXIC"].

    Returns:
        A list of dictionaries, each containing a summary for an index
        (symbol, name, current price, change, percent change).

    Raises:
        RuntimeError: If yfinance library is not installed or if data cannot be fetched.
    """
    if yf is None:
        raise RuntimeError("yfinance library is not installed.")

    summaries = []
    if not indices:
        indices = ["^GSPC", "^DJI", "^IXIC"]  # Default if empty list provided

    for symbol in indices:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
                logger.warning(f"Could not retrieve current price for index: {symbol}. Skipping.")
                continue

            current_price = info.get("regularMarketPrice", info.get("currentPrice"))
            previous_close = info.get("previousClose")
            change = None
            percent_change = None

            if current_price is not None and previous_close is not None:
                change = current_price - previous_close
                percent_change = (change / previous_close) * 100

            summary = {
                "symbol": symbol,
                "name": info.get("shortName", info.get("longName")),
                "currentPrice": current_price,
                "change": round(change, 2) if change is not None else None,
                "percentChange": round(percent_change, 2) if percent_change is not None else None,
                "previousClose": previous_close,
                "marketState": info.get("marketState"),
            }
            summaries.append({k: v for k, v in summary.items() if v is not None})
        except Exception as e:
            logger.error(f"Error fetching market summary for index {symbol}: {e}. Skipping this index.")
            # Continue to next index if one fails
            continue

    if not summaries:
        # This could happen if all indices fail or the input list was effectively empty and defaults also failed
        raise RuntimeError("Failed to fetch summary for any of the market indices.")

    return summaries


def main():
    """
    Main function to start the MCP server.
    """
    load_dotenv()  # Load environment variables from .env file if present
    logger.info("Starting Yahoo Finance MCP Server...")
    mcp.run(transport="stdio")


# Make the module callable for uvx (similar to pdf_mcp_server.py)
def __call__():
    """
    Make the module callable for uvx.
    This function is called when the module is executed directly by uvx.
    """
    main()


# Add this for compatibility with uvx if it's used as a module
if __name__ != "__main__":
    sys.modules[__name__].__call__ = __call__

# Run the server when the script is executed directly
if __name__ == "__main__":
    main()
