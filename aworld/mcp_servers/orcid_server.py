"""
ORCID MCP Server

This module provides MCP server functionality for interacting with the ORCID API.
It allows for retrieving researcher profiles, works, education, employment, and other information from ORCID.

Key features:
- Retrieve researcher profile information
- Fetch works (publications) from ORCID profiles
- Get education and employment history
- Search for researchers by name or keywords

Main functions:
- mcpgetresearcher: Retrieves information about a researcher by ORCID ID
- mcpgetworks: Fetches publications and works from a researcher's ORCID profile
- mcpgeteducation: Retrieves education history from a researcher's ORCID profile
- mcpgetemployment: Retrieves employment history from a researcher's ORCID profile
- mcpsearchresearchers: Searches for researchers based on query
"""

import json
import os
import traceback
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server
from aworld.utils import import_package

# Import ORCID package, install if not available
import_package("orcid", install_name="orcid-python")

import orcid


# Define model classes for different ORCID API responses
class ORCIDResearcher(BaseModel):
    """Model representing an ORCID researcher profile"""

    orcid_id: str
    name: str
    given_names: Optional[str] = None
    family_name: Optional[str] = None
    credit_name: Optional[str] = None
    other_names: List[str] = []
    biography: Optional[str] = None
    country: Optional[str] = None
    keywords: List[str] = []
    emails: List[str] = []
    websites: List[Dict[str, str]] = []
    created_date: Optional[str] = None
    last_modified_date: Optional[str] = None


class ORCIDWork(BaseModel):
    """Model representing a work (publication) in an ORCID profile"""

    title: str
    subtitle: Optional[str] = None
    journal_title: Optional[str] = None
    description: Optional[str] = None
    citation: Optional[Dict[str, str]] = None
    type: Optional[str] = None
    publication_date: Optional[Dict[str, str]] = None
    url: Optional[str] = None
    external_ids: List[Dict[str, str]] = []
    contributors: List[Dict[str, str]] = []
    created_date: Optional[str] = None
    last_modified_date: Optional[str] = None


class ORCIDEducation(BaseModel):
    """Model representing education history in an ORCID profile"""

    organization: str
    department: Optional[str] = None
    role_title: Optional[str] = None
    start_date: Optional[Dict[str, str]] = None
    end_date: Optional[Dict[str, str]] = None
    organization_address: Optional[Dict[str, str]] = None
    url: Optional[str] = None
    created_date: Optional[str] = None
    last_modified_date: Optional[str] = None


class ORCIDEmployment(BaseModel):
    """Model representing employment history in an ORCID profile"""

    organization: str
    department: Optional[str] = None
    role_title: Optional[str] = None
    start_date: Optional[Dict[str, str]] = None
    end_date: Optional[Dict[str, str]] = None
    organization_address: Optional[Dict[str, str]] = None
    url: Optional[str] = None
    created_date: Optional[str] = None
    last_modified_date: Optional[str] = None


class ORCIDSearchResult(BaseModel):
    """Model representing search results"""

    query: str
    total_count: int
    items: List[Any]


class ORCIDError(BaseModel):
    """Model representing an error in ORCID API processing"""

    error: str
    operation: str


def handle_error(e: Exception, operation_type: str) -> str:
    """Unified error handling and return standard format error message"""
    error_msg = f"{operation_type} error: {str(e)}"
    logger.error(traceback.format_exc())

    error = ORCIDError(error=error_msg, operation=operation_type)

    return error.model_dump_json()


def get_orcid_api():
    """
    Create and return ORCID API instances.

    Returns:
        Tuple of ORCID API instances (member_api, public_api)
    """
    client_id = os.environ.get("ORCID_CLIENT_ID")
    client_secret = os.environ.get("ORCID_CLIENT_SECRET")

    if client_id and client_secret:
        # Use member API if credentials are available
        member_api = orcid.MemberAPI(client_id, client_secret, sandbox=False)
        public_api = orcid.PublicAPI(sandbox=False)
        return member_api, public_api
    else:
        # Use public API only if no credentials
        logger.warning("No ORCID credentials found. Using public API with rate limits.")
        public_api = orcid.PublicAPI(sandbox=False)
        return None, public_api


def mcpgetresearcher(
    orcid_id: str = Field(
        description="ORCID ID of the researcher (e.g., 0000-0002-1825-0097)"
    ),
) -> str:
    """
    Get information about a researcher by ORCID ID.

    Args:
        orcid_id: ORCID ID of the researcher

    Returns:
        JSON string containing researcher information
    """
    try:
        # Initialize ORCID API
        _, public_api = get_orcid_api()

        # Get researcher profile
        profile = public_api.read_record_public(orcid_id, "record")

        # Extract person data
        person = profile.get("person", {})
        name = person.get("name", {})
        biography = person.get("biography", {}).get("content", None)

        # Extract other names
        other_names_list = []
        other_names = person.get("other-names", {}).get("other-name", [])
        if other_names:
            for other_name in other_names:
                if "content" in other_name:
                    other_names_list.append(other_name["content"])

        # Extract keywords
        keywords_list = []
        keywords = person.get("keywords", {}).get("keyword", [])
        if keywords:
            for keyword in keywords:
                if "content" in keyword:
                    keywords_list.append(keyword["content"])

        # Extract emails
        emails_list = []
        emails = person.get("emails", {}).get("email", [])
        if emails:
            for email in emails:
                if "email" in email:
                    emails_list.append(email["email"])

        # Extract websites
        websites_list = []
        researcher_urls = person.get("researcher-urls", {}).get("researcher-url", [])
        if researcher_urls:
            for url in researcher_urls:
                website = {
                    "name": url.get("url-name", ""),
                    "url": url.get("url", {}).get("value", ""),
                }
                websites_list.append(website)

        # Extract country
        country = None
        addresses = person.get("addresses", {}).get("address", [])
        if addresses and len(addresses) > 0:
            country = addresses[0].get("country", {}).get("value", None)

        # Create researcher object
        researcher_obj = ORCIDResearcher(
            orcid_id=orcid_id,
            name=name.get("credit-name", {}).get("value", "")
            or f"{name.get('given-names', {}).get('value', '')} {name.get('family-name', {}).get('value', '')}".strip(),
            given_names=name.get("given-names", {}).get("value", None),
            family_name=name.get("family-name", {}).get("value", None),
            credit_name=name.get("credit-name", {}).get("value", None),
            other_names=other_names_list,
            biography=biography,
            country=country,
            keywords=keywords_list,
            emails=emails_list,
            websites=websites_list,
            created_date=profile.get("history", {})
            .get("creation-method", {})
            .get("created-date", {})
            .get("value", None),
            last_modified_date=profile.get("history", {})
            .get("last-modified-date", {})
            .get("value", None),
        )

        return researcher_obj.model_dump_json()

    except Exception as e:
        return handle_error(e, "Get Researcher")


def mcpgetworks(
    orcid_id: str = Field(description="ORCID ID of the researcher"),
    limit: int = Field(default=50, description="Number of works to retrieve (max 100)"),
) -> str:
    """
    Get works (publications) from a researcher's ORCID profile.

    Args:
        orcid_id: ORCID ID of the researcher
        limit: Number of works to retrieve (max 100)

    Returns:
        JSON string containing works information
    """
    try:
        # Validate input
        if limit > 100:
            limit = 100
            logger.warning("Limit capped at 100 works")

        # Initialize ORCID API
        _, public_api = get_orcid_api()

        # Get works summary
        works_data = public_api.read_record_public(orcid_id, "works")
        works_group = works_data.get("group", [])

        # Process works
        works_list = []
        count = 0

        for work_group in works_group:
            if count >= limit:
                break

            # Get the preferred work summary from the group
            work_summaries = work_group.get("work-summary", [])
            if not work_summaries:
                continue

            # Use the first work summary (usually the preferred one)
            work_summary = work_summaries[0]

            # Extract work details
            title = work_summary.get("title", {}).get("title", {}).get("value", "")
            subtitle = (
                work_summary.get("title", {}).get("subtitle", {}).get("value", None)
            )
            journal = work_summary.get("journal-title", {}).get("value", None)

            # Extract publication date
            pub_date = None
            if "publication-date" in work_summary:
                year = work_summary["publication-date"].get("year", {}).get("value", "")
                month = (
                    work_summary["publication-date"].get("month", {}).get("value", "")
                )
                day = work_summary["publication-date"].get("day", {}).get("value", "")

                if year:
                    pub_date = {"year": year, "month": month, "day": day}

            # Extract external IDs
            external_ids = []
            if (
                "external-ids" in work_summary
                and "external-id" in work_summary["external-ids"]
            ):
                for ext_id in work_summary["external-ids"]["external-id"]:
                    id_type = ext_id.get("external-id-type", "")
                    id_value = ext_id.get("external-id-value", "")
                    id_url = ext_id.get("external-id-url", {}).get("value", "")

                    if id_type and id_value:
                        external_ids.append(
                            {"type": id_type, "value": id_value, "url": id_url}
                        )

            # Create work object
            work_obj = ORCIDWork(
                title=title,
                subtitle=subtitle,
                journal_title=journal,
                type=work_summary.get("type", None),
                publication_date=pub_date,
                url=work_summary.get("url", {}).get("value", None),
                external_ids=external_ids,
                created_date=work_summary.get("created-date", {}).get("value", None),
                last_modified_date=work_summary.get("last-modified-date", {}).get(
                    "value", None
                ),
            )

            works_list.append(work_obj)
            count += 1

        # Create result
        result = {
            "orcid_id": orcid_id,
            "works": [work.model_dump() for work in works_list],
            "count": len(works_list),
            "total_available": len(works_group),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get Works")


def mcpgeteducation(
    orcid_id: str = Field(description="ORCID ID of the researcher"),
) -> str:
    """
    Get education history from a researcher's ORCID profile.

    Args:
        orcid_id: ORCID ID of the researcher

    Returns:
        JSON string containing education information
    """
    try:
        # Initialize ORCID API
        _, public_api = get_orcid_api()

        # Get education data
        education_data = public_api.read_record_public(orcid_id, "educations")
        education_summaries = education_data.get("education-summary", [])

        # Process education entries
        education_list = []

        for edu in education_summaries:
            # Extract organization details
            organization = edu.get("organization", {}).get("name", "")
            department = edu.get("department-name", None)
            role_title = edu.get("role-title", None)

            # Extract address
            org_address = None
            if "organization" in edu and "address" in edu["organization"]:
                address = edu["organization"]["address"]
                org_address = {
                    "city": address.get("city", ""),
                    "region": address.get("region", ""),
                    "country": address.get("country", ""),
                }

            # Extract dates
            start_date = None
            if "start-date" in edu:
                start_date = {
                    "year": edu["start-date"].get("year", {}).get("value", ""),
                    "month": edu["start-date"].get("month", {}).get("value", ""),
                    "day": edu["start-date"].get("day", {}).get("value", ""),
                }

            end_date = None
            if "end-date" in edu:
                end_date = {
                    "year": edu["end-date"].get("year", {}).get("value", ""),
                    "month": edu["end-date"].get("month", {}).get("value", ""),
                    "day": edu["end-date"].get("day", {}).get("value", ""),
                }

            # Create education object
            education_obj = ORCIDEducation(
                organization=organization,
                department=department,
                role_title=role_title,
                start_date=start_date,
                end_date=end_date,
                organization_address=org_address,
                url=edu.get("url", {}).get("value", None),
                created_date=edu.get("created-date", {}).get("value", None),
                last_modified_date=edu.get("last-modified-date", {}).get("value", None),
            )

            education_list.append(education_obj)

        # Create result
        result = {
            "orcid_id": orcid_id,
            "education": [edu.model_dump() for edu in education_list],
            "count": len(education_list),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get Education")


def mcpgetemployment(
    orcid_id: str = Field(description="ORCID ID of the researcher"),
) -> str:
    """
    Get employment history from a researcher's ORCID profile.

    Args:
        orcid_id: ORCID ID of the researcher

    Returns:
        JSON string containing employment information
    """
    try:
        # Initialize ORCID API
        _, public_api = get_orcid_api()

        # Get employment data
        employment_data = public_api.read_record_public(orcid_id, "employments")
        employment_summaries = employment_data.get("employment-summary", [])

        # Process employment entries
        employment_list = []

        for emp in employment_summaries:
            # Extract organization details
            organization = emp.get("organization", {}).get("name", "")
            department = emp.get("department-name", None)
            role_title = emp.get("role-title", None)

            # Extract address
            org_address = None
            if "organization" in emp and "address" in emp["organization"]:
                address = emp["organization"]["address"]
                org_address = {
                    "city": address.get("city", ""),
                    "region": address.get("region", ""),
                    "country": address.get("country", ""),
                }

            # Extract dates
            start_date = None
            if "start-date" in emp:
                start_date = {
                    "year": emp["start-date"].get("year", {}).get("value", ""),
                    "month": emp["start-date"].get("month", {}).get("value", ""),
                    "day": emp["start-date"].get("day", {}).get("value", ""),
                }

            end_date = None
            if "end-date" in emp:
                end_date = {
                    "year": emp["end-date"].get("year", {}).get("value", ""),
                    "month": emp["end-date"].get("month", {}).get("value", ""),
                    "day": emp["end-date"].get("day", {}).get("value", ""),
                }

            # Create employment object
            employment_obj = ORCIDEmployment(
                organization=organization,
                department=department,
                role_title=role_title,
                start_date=start_date,
                end_date=end_date,
                organization_address=org_address,
                url=emp.get("url", {}).get("value", None),
                created_date=emp.get("created-date", {}).get("value", None),
                last_modified_date=emp.get("last-modified-date", {}).get("value", None),
            )

            employment_list.append(employment_obj)

        # Create result
        result = {
            "orcid_id": orcid_id,
            "employment": [emp.model_dump() for emp in employment_list],
            "count": len(employment_list),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get Employment")


def mcpsearchresearchers(
    query: str = Field(description="Search query (name, keyword, etc.)"),
    limit: int = Field(
        default=20, description="Number of results to retrieve (max 100)"
    ),
) -> str:
    """
    Search for researchers based on query.

    Args:
        query: Search query (name, keyword, etc.)
        limit: Number of results to retrieve (max 100)

    Returns:
        JSON string containing search results
    """
    try:
        # Validate input
        if limit > 100:
            limit = 100
            logger.warning("Limit capped at 100 results")

        # Initialize ORCID API
        _, public_api = get_orcid_api()

        # Search researchers
        search_results = public_api.search_public(query, rows=limit)

        # Process results
        researchers = []

        for result in search_results.get("result", []):
            orcid_id = result.get("orcid-identifier", {}).get("path", "")

            if not orcid_id:
                continue

            # Get basic profile info
            try:
                profile = public_api.read_record_public(orcid_id, "person")

                # Extract name
                name = profile.get("name", {})
                given_name = name.get("given-names", {}).get("value", "")
                family_name = name.get("family-name", {}).get("value", "")
                credit_name = name.get("credit-name", {}).get("value", "")

                display_name = credit_name or f"{given_name} {family_name}".strip()

                # Extract country
                country = None
                addresses = profile.get("addresses", {}).get("address", [])
                if addresses and len(addresses) > 0:
                    country = addresses[0].get("country", {}).get("value", None)

                # Create researcher summary
                researcher = {
                    "orcid_id": orcid_id,
                    "name": display_name,
                    "given_names": given_name,
                    "family_name": family_name,
                    "country": country,
                }

                researchers.append(researcher)

            except Exception as e:
                logger.warning(f"Error fetching profile for {orcid_id}: {str(e)}")
                # Add minimal info if profile fetch fails
                researchers.append({"orcid_id": orcid_id, "name": "Unknown"})

        # Create search result
        search_result = ORCIDSearchResult(
            query=query,
            total_count=search_results.get("num-found", 0),
            items=researchers,
        )

        return search_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Search Researchers")


def mcpgetfunding(
    orcid_id: str = Field(description="ORCID ID of the researcher"),
) -> str:
    """
    Get funding information from a researcher's ORCID profile.

    Args:
        orcid_id: ORCID ID of the researcher

    Returns:
        JSON string containing funding information
    """
    try:
        # Initialize ORCID API
        _, public_api = get_orcid_api()

        # Get funding data
        funding_data = public_api.read_record_public(orcid_id, "fundings")
        funding_groups = funding_data.get("group", [])

        # Process funding entries
        funding_list = []

        for funding_group in funding_groups:
            funding_summaries = funding_group.get("funding-summary", [])

            if not funding_summaries:
                continue

            # Use the first funding summary
            funding = funding_summaries[0]

            # Extract funding details
            title = funding.get("title", {}).get("title", {}).get("value", "")
            type = funding.get("type", "")
            organization = funding.get("organization", {}).get("name", "")

            # Extract dates
            start_date = None
            if "start-date" in funding:
                start_date = {
                    "year": funding["start-date"].get("year", {}).get("value", ""),
                    "month": funding["start-date"].get("month", {}).get("value", ""),
                    "day": funding["start-date"].get("day", {}).get("value", ""),
                }

            end_date = None
            if "end-date" in funding:
                end_date = {
                    "year": funding["end-date"].get("year", {}).get("value", ""),
                    "month": funding["end-date"].get("month", {}).get("value", ""),
                    "day": funding["end-date"].get("day", {}).get("value", ""),
                }

            # Extract external IDs
            external_ids = []
            if "external-ids" in funding and "external-id" in funding["external-ids"]:
                for ext_id in funding["external-ids"]["external-id"]:
                    id_type = ext_id.get("external-id-type", "")
                    id_value = ext_id.get("external-id-value", "")
                    id_url = ext_id.get("external-id-url", {}).get("value", "")

                    if id_type and id_value:
                        external_ids.append(
                            {"type": id_type, "value": id_value, "url": id_url}
                        )

            # Create funding object
            funding_obj = {
                "title": title,
                "type": type,
                "organization": organization,
                "start_date": start_date,
                "end_date": end_date,
                "external_ids": external_ids,
                "url": funding.get("url", {}).get("value", None),
                "created_date": funding.get("created-date", {}).get("value", None),
                "last_modified_date": funding.get("last-modified-date", {}).get(
                    "value", None
                ),
            }

            funding_list.append(funding_obj)

        # Create result
        result = {
            "orcid_id": orcid_id,
            "funding": funding_list,
            "count": len(funding_list),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get Funding")


# Main function
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Launch ORCID MCP server with random port allocation"
    )
    parser.add_argument(
        "--port",
        type=int,
        help=f"Listening to port. Must be specified.",
    )
    args = parser.parse_args()
    run_mcp_server(
        "ORCID Server",
        funcs=[
            mcpgetresearcher,
            mcpgetworks,
            mcpgeteducation,
            mcpgetemployment,
            mcpsearchresearchers,
            mcpgetfunding,
        ],
        port=args.port,
    )
