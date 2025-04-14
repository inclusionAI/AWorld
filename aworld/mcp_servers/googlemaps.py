"""
Google Maps MCP Server

This module provides MCP server functionality for interacting with the Google Maps API.
It offers tools for geocoding, distance matrix calculations, directions, and place details.

Key features:
- Geocode addresses to latitude and longitude
- Calculate distances and travel times between locations
- Retrieve directions and route information
- Access detailed place information

Main functions:
- mcpgeocode: Geocodes addresses using Google Maps API
- mcpdistancematrix: Calculates distances and travel times
- mcpdirections: Retrieves directions and route information
- mcpplacedetails: Fetches detailed information about places
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server
from aworld.utils import import_package

# Import googlemaps package, install if not available
import_package("googlemaps")
import googlemaps


# Define model classes for different Google Maps API responses
class GeocodingResult(BaseModel):
    """Model representing a geocoding result"""

    formatted_address: str
    place_id: str
    location: Dict[str, float]  # lat, lng
    location_type: str
    types: List[str]
    partial_match: Optional[bool] = None


class GeocodingResponse(BaseModel):
    """Model representing geocoding API response"""

    results: List[GeocodingResult]
    status: str


class DistanceMatrixElement(BaseModel):
    """Model representing a single element in distance matrix response"""

    status: str
    duration: Optional[Dict[str, Union[str, int]]] = None
    distance: Optional[Dict[str, Union[str, int]]] = None
    duration_in_traffic: Optional[Dict[str, Union[str, int]]] = None


class DistanceMatrixRow(BaseModel):
    """Model representing a row in distance matrix response"""

    elements: List[DistanceMatrixElement]


class DistanceMatrixResponse(BaseModel):
    """Model representing distance matrix API response"""

    origin_addresses: List[str]
    destination_addresses: List[str]
    rows: List[DistanceMatrixRow]
    status: str


class DirectionsLeg(BaseModel):
    """Model representing a leg in directions response"""

    start_address: str
    end_address: str
    distance: Dict[str, Union[str, int]]
    duration: Dict[str, Union[str, int]]
    steps: List[Dict[str, Any]]  # Steps are complex, using Dict for simplicity


class DirectionsRoute(BaseModel):
    """Model representing a route in directions response"""

    summary: str
    legs: List[DirectionsLeg]
    warnings: List[str]
    bounds: Dict[str, Dict[str, float]]
    copyrights: str
    overview_polyline: Dict[str, str]


class DirectionsResponse(BaseModel):
    """Model representing directions API response"""

    routes: List[DirectionsRoute]
    status: str
    geocoded_waypoints: List[Dict[str, Any]]


class PlaceDetails(BaseModel):
    """Model representing place details response"""

    place_id: str
    name: str
    formatted_address: Optional[str] = None
    formatted_phone_number: Optional[str] = None
    geometry: Optional[Dict[str, Any]] = None
    rating: Optional[float] = None
    types: Optional[List[str]] = None
    website: Optional[str] = None
    opening_hours: Optional[Dict[str, Any]] = None
    photos: Optional[List[Dict[str, Any]]] = None
    reviews: Optional[List[Dict[str, Any]]] = None


class PlaceSearchResult(BaseModel):
    """Model representing a place search result"""

    place_id: str
    name: str
    geometry: Dict[str, Any]
    vicinity: Optional[str] = None
    types: List[str]
    rating: Optional[float] = None
    user_ratings_total: Optional[int] = None
    photos: Optional[List[Dict[str, Any]]] = None


class PlaceSearchResponse(BaseModel):
    """Model representing place search API response"""

    results: List[PlaceSearchResult]
    status: str
    next_page_token: Optional[str] = None


class LatLngResult(BaseModel):
    """Model representing latitude and longitude result"""

    address: str
    latitude: float
    longitude: float
    status: str


class PostcodeResult(BaseModel):
    """Model representing postcode (zip code) result"""

    address: str
    postcode: str
    country: Optional[str] = None
    country_code: Optional[str] = None
    status: str


class GoogleMapsError(BaseModel):
    """Model representing an error in Google Maps API processing"""

    error: str
    status: str


def handle_error(e: Exception, error_type: str) -> str:
    """Unified error handling and return standard format error message"""
    error_msg = f"{error_type} error: {str(e)}"
    logger.error(error_msg)

    error = GoogleMapsError(error=error_msg, status="ERROR")

    return error.model_dump_json()


def mcpgeocode(
    address: str = Field(description="Address or place name to geocode"),
    api_key: str = Field(description="Google Maps API key"),
    language: str = Field(
        default="en", description="Language for results (default: en)"
    ),
    region: Optional[str] = Field(default=None, description="Region bias (optional)"),
) -> str:
    """
    Convert an address or place name to geographic coordinates (latitude and longitude).

    Args:
        address: The address or place name to geocode
        api_key: Your Google Maps API key
        language: Language code for results (e.g., 'en', 'zh-CN')
        region: Region bias for results (e.g., 'us', 'cn')

    Returns:
        JSON string containing geocoding results
    """
    try:
        # Initialize Google Maps client
        gmaps = googlemaps.Client(key=api_key)

        # Call geocoding API
        geocode_result = gmaps.geocode(
            address=address, language=language, region=region
        )

        # Process results
        results = []
        for result in geocode_result:
            # Extract location data
            location = result.get("geometry", {}).get("location", {})
            location_type = result.get("geometry", {}).get("location_type", "")

            # Create result object
            geocode_item = GeocodingResult(
                formatted_address=result.get("formatted_address", ""),
                place_id=result.get("place_id", ""),
                location=location,
                location_type=location_type,
                types=result.get("types", []),
                partial_match=result.get("partial_match", False),
            )
            results.append(geocode_item)

        # Create response
        response = GeocodingResponse(
            results=results, status="OK" if results else "ZERO_RESULTS"
        )

        return response.model_dump_json()

    except Exception as e:
        return handle_error(e, "Geocoding")


def mcpdistancematrix(
    origins: List[str] = Field(description="List of origin addresses or coordinates"),
    destinations: List[str] = Field(
        description="List of destination addresses or coordinates"
    ),
    api_key: str = Field(description="Google Maps API key"),
    mode: str = Field(
        default="driving",
        description="Travel mode (driving, walking, bicycling, transit)",
    ),
    language: str = Field(default="en", description="Language for results"),
    units: str = Field(
        default="metric", description="Units for distance (metric, imperial)"
    ),
    departure_time: Optional[str] = Field(
        default=None,
        description="Departure time (format: YYYY-MM-DD HH:MM:SS or 'now')",
    ),
    avoid: Optional[str] = Field(
        default=None, description="Features to avoid (tolls, highways, ferries, indoor)"
    ),
) -> str:
    """
    Calculate distance and travel time between multiple origins and destinations.

    Args:
        origins: List of addresses or coordinates as origins
        destinations: List of addresses or coordinates as destinations
        api_key: Your Google Maps API key
        mode: Travel mode (driving, walking, bicycling, transit)
        language: Language code for results
        units: Units system for distances (metric, imperial)
        departure_time: Departure time for time-dependent calculations
        avoid: Features to avoid in routes

    Returns:
        JSON string containing distance matrix results
    """
    try:
        # Initialize Google Maps client
        gmaps = googlemaps.Client(key=api_key)

        # Process departure_time
        departure_time_param = None
        if departure_time:
            if departure_time.lower() == "now":
                departure_time_param = datetime.now()
            else:
                try:
                    departure_time_param = datetime.strptime(
                        departure_time, "%Y-%m-%d %H:%M:%S"
                    )
                except ValueError:
                    logger.warning(
                        f"Invalid departure_time format: {departure_time}. Using current time."
                    )
                    departure_time_param = datetime.now()

        # Call distance matrix API
        distance_result = gmaps.distance_matrix(
            origins=origins,
            destinations=destinations,
            mode=mode,
            language=language,
            units=units,
            departure_time=departure_time_param,
            avoid=avoid,
        )

        # Process rows and elements
        matrix_rows = []
        for row in distance_result.get("rows", []):
            elements = []
            for element in row.get("elements", []):
                element_obj = DistanceMatrixElement(
                    status=element.get("status", ""),
                    duration=element.get("duration"),
                    distance=element.get("distance"),
                    duration_in_traffic=element.get("duration_in_traffic"),
                )
                elements.append(element_obj)

            matrix_rows.append(DistanceMatrixRow(elements=elements))

        # Create response
        response = DistanceMatrixResponse(
            origin_addresses=distance_result.get("origin_addresses", []),
            destination_addresses=distance_result.get("destination_addresses", []),
            rows=matrix_rows,
            status=distance_result.get("status", ""),
        )

        return response.model_dump_json()

    except Exception as e:
        return handle_error(e, "Distance Matrix")


def mcpdirections(
    origin: str = Field(description="Origin address or coordinates"),
    destination: str = Field(description="Destination address or coordinates"),
    api_key: str = Field(description="Google Maps API key"),
    mode: str = Field(
        default="driving",
        description="Travel mode (driving, walking, bicycling, transit)",
    ),
    waypoints: Optional[List[str]] = Field(
        default=None, description="List of waypoints"
    ),
    alternatives: bool = Field(
        default=False, description="Whether to return alternative routes"
    ),
    avoid: Optional[str] = Field(
        default=None, description="Features to avoid (tolls, highways, ferries, indoor)"
    ),
    language: str = Field(default="en", description="Language for results"),
    units: str = Field(
        default="metric", description="Units for distance (metric, imperial)"
    ),
    departure_time: Optional[str] = Field(
        default=None,
        description="Departure time (format: YYYY-MM-DD HH:MM:SS or 'now')",
    ),
    optimize_waypoints: bool = Field(
        default=False, description="Optimize the order of waypoints"
    ),
) -> str:
    """
    Get directions between an origin and destination, with optional waypoints.

    Args:
        origin: Starting point address or coordinates
        destination: Ending point address or coordinates
        api_key: Your Google Maps API key
        mode: Travel mode (driving, walking, bicycling, transit)
        waypoints: Optional list of waypoints
        alternatives: Whether to return alternative routes
        avoid: Features to avoid in routes
        language: Language code for results
        units: Units system for distances
        departure_time: Departure time for time-dependent calculations
        optimize_waypoints: Whether to optimize the order of waypoints

    Returns:
        JSON string containing directions results
    """
    try:
        # Initialize Google Maps client
        gmaps = googlemaps.Client(key=api_key)

        # Process departure_time
        departure_time_param = None
        if departure_time:
            if departure_time.lower() == "now":
                departure_time_param = datetime.now()
            else:
                try:
                    departure_time_param = datetime.strptime(
                        departure_time, "%Y-%m-%d %H:%M:%S"
                    )
                except ValueError:
                    logger.warning(
                        f"Invalid departure_time format: {departure_time}. Using current time."
                    )
                    departure_time_param = datetime.now()

        # Call directions API
        directions_result = gmaps.directions(
            origin=origin,
            destination=destination,
            mode=mode,
            waypoints=waypoints,
            alternatives=alternatives,
            avoid=avoid,
            language=language,
            units=units,
            departure_time=departure_time_param,
            optimize_waypoints=optimize_waypoints,
        )

        # Process routes
        routes = []
        for route in directions_result:
            # Process legs
            legs = []
            for leg in route.get("legs", []):
                leg_obj = DirectionsLeg(
                    start_address=leg.get("start_address", ""),
                    end_address=leg.get("end_address", ""),
                    distance=leg.get("distance", {}),
                    duration=leg.get("duration", {}),
                    steps=leg.get("steps", []),
                )
                legs.append(leg_obj)

            # Create route object
            route_obj = DirectionsRoute(
                summary=route.get("summary", ""),
                legs=legs,
                warnings=route.get("warnings", []),
                bounds=route.get("bounds", {"northeast": {}, "southwest": {}}),
                copyrights=route.get("copyrights", ""),
                overview_polyline=route.get("overview_polyline", {}),
            )
            routes.append(route_obj)

        # Create response
        response = DirectionsResponse(
            routes=routes,
            status="OK" if routes else "ZERO_RESULTS",
            geocoded_waypoints=(
                directions_result[0].get("geocoded_waypoints", [])
                if directions_result
                else []
            ),
        )

        return response.model_dump_json()

    except Exception as e:
        return handle_error(e, "Directions")


def mcpplacesearch(
    query: str = Field(description="Search query (e.g., 'restaurants in New York')"),
    api_key: str = Field(description="Google Maps API key"),
    location: Optional[str] = Field(
        default=None,
        description="Location bias as 'lat,lng' (e.g., '40.7128,-74.0060')",
    ),
    radius: Optional[int] = Field(
        default=None, description="Search radius in meters (max 50000)"
    ),
    language: str = Field(default="en", description="Language for results"),
    type: Optional[str] = Field(
        default=None, description="Place type (e.g., restaurant, cafe, park)"
    ),
    min_price: Optional[int] = Field(
        default=None, description="Minimum price level (0-4)"
    ),
    max_price: Optional[int] = Field(
        default=None, description="Maximum price level (0-4)"
    ),
    open_now: bool = Field(default=False, description="Whether place is open now"),
) -> str:
    """
    Search for places based on a text query and location.

    Args:
        query: Text search query
        api_key: Your Google Maps API key
        location: Location bias as 'lat,lng'
        radius: Search radius in meters (max 50000)
        language: Language code for results
        type: Restricts results to places of specified type
        min_price: Minimum price level (0-4)
        max_price: Maximum price level (0-4)
        open_now: Whether place is open at time of request

    Returns:
        JSON string containing place search results
    """
    try:
        # Initialize Google Maps client
        gmaps = googlemaps.Client(key=api_key)

        # Process location parameter
        location_param = None
        if location:
            try:
                lat, lng = map(float, location.split(","))
                location_param = (lat, lng)
            except ValueError:
                logger.warning(
                    f"Invalid location format: {location}. Should be 'lat,lng'."
                )

        # Call places API
        places_result = gmaps.places(
            query=query,
            location=location_param,
            radius=radius,
            language=language,
            min_price=min_price,
            max_price=max_price,
            open_now=open_now,
            type=type,
        )

        # Process results
        results = []
        for place in places_result.get("results", []):
            place_obj = PlaceSearchResult(
                place_id=place.get("place_id", ""),
                name=place.get("name", ""),
                geometry=place.get("geometry", {"location": {}}),
                vicinity=place.get("vicinity", ""),
                types=place.get("types", []),
                rating=place.get("rating"),
                user_ratings_total=place.get("user_ratings_total"),
                photos=place.get("photos", []),
            )
            results.append(place_obj)

        # Create response
        response = PlaceSearchResponse(
            results=results,
            status=places_result.get("status", ""),
            next_page_token=places_result.get("next_page_token"),
        )

        return response.model_dump_json()

    except Exception as e:
        return handle_error(e, "Place Search")


def mcpplacedetails(
    place_id: str = Field(description="Google Place ID"),
    api_key: str = Field(description="Google Maps API key"),
    language: str = Field(default="en", description="Language for results"),
    fields: Optional[List[str]] = Field(
        default=None, description="List of place data fields to return"
    ),
) -> str:
    """
    Get detailed information about a place using its Place ID.

    Args:
        place_id: Google Place ID
        api_key: Your Google Maps API key
        language: Language code for results
        fields: List of place data fields to return

    Returns:
        JSON string containing place details
    """
    try:
        # Initialize Google Maps client
        gmaps = googlemaps.Client(key=api_key)

        # Default fields if none provided
        if not fields:
            fields = [
                "name",
                "place_id",
                "formatted_address",
                "geometry",
                "types",
                "formatted_phone_number",
                "website",
                "rating",
                "reviews",
                "opening_hours",
                "photos",
            ]

        # Call place details API
        place_result = gmaps.place(place_id=place_id, language=language, fields=fields)

        if place_result.get("status") == "OK":
            result = place_result.get("result", {})

            # Create place details object
            place_details = PlaceDetails(
                place_id=result.get("place_id", ""),
                name=result.get("name", ""),
                formatted_address=result.get("formatted_address"),
                formatted_phone_number=result.get("formatted_phone_number"),
                geometry=result.get("geometry"),
                rating=result.get("rating"),
                types=result.get("types"),
                website=result.get("website"),
                opening_hours=result.get("opening_hours"),
                photos=result.get("photos"),
            )

            return place_details.model_dump_json()
        else:
            # Return error if place not found
            error = GoogleMapsError(
                error=f"Place not found or API error: {place_result.get('status')}",
                status=place_result.get("status", "ERROR"),
            )
            return error.model_dump_json()

    except Exception as e:
        return handle_error(e, "Place Details")


def mcptimezone(
    location: str = Field(
        description="Location as 'lat,lng' (e.g., '40.7128,-74.0060')"
    ),
    api_key: str = Field(description="Google Maps API key"),
    timestamp: Optional[str] = Field(
        default=None, description="Timestamp (format: YYYY-MM-DD HH:MM:SS or 'now')"
    ),
) -> str:
    """
    Get timezone information for a location.

    Args:
        location: Location as 'lat,lng'
        api_key: Your Google Maps API key
        timestamp: Timestamp for timezone calculation

    Returns:
        JSON string containing timezone information
    """
    try:
        # Initialize Google Maps client
        gmaps = googlemaps.Client(key=api_key)

        # Process location parameter
        try:
            lat, lng = map(float, location.split(","))
        except ValueError:
            return handle_error(
                ValueError("Invalid location format. Should be 'lat,lng'."), "Timezone"
            )

        # Process timestamp
        timestamp_param = None
        if timestamp:
            if timestamp.lower() == "now":
                timestamp_param = int(datetime.now().timestamp())
            else:
                try:
                    dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                    timestamp_param = int(dt.timestamp())
                except ValueError:
                    logger.warning(
                        f"Invalid timestamp format: {timestamp}. Using current time."
                    )
                    timestamp_param = int(datetime.now().timestamp())
        else:
            timestamp_param = int(datetime.now().timestamp())

        # Call timezone API
        timezone_result = gmaps.timezone(location=(lat, lng), timestamp=timestamp_param)

        # Return the result directly as it's already a simple structure
        return json.dumps(timezone_result)

    except Exception as e:
        return handle_error(e, "Timezone")


def mcpelevation(
    locations: List[str] = Field(description="List of locations as 'lat,lng'"),
    api_key: str = Field(description="Google Maps API key"),
) -> str:
    """
    Get elevation data for locations.

    Args:
        locations: List of locations as 'lat,lng'
        api_key: Your Google Maps API key

    Returns:
        JSON string containing elevation data
    """
    try:
        # Initialize Google Maps client
        gmaps = googlemaps.Client(key=api_key)

        # Process locations
        locations_param = []
        for loc in locations:
            try:
                lat, lng = map(float, loc.split(","))
                locations_param.append((lat, lng))
            except ValueError:
                logger.warning(f"Invalid location format: {loc}. Skipping.")

        if not locations_param:
            return handle_error(ValueError("No valid locations provided."), "Elevation")

        # Call elevation API
        elevation_result = gmaps.elevation(locations=locations_param)

        # Return the result directly
        return json.dumps(elevation_result)

    except Exception as e:
        return handle_error(e, "Elevation")


def mcpgetlatlng(
    address: str = Field(description="Address to convert to latitude and longitude"),
    api_key: str = Field(description="Google Maps API key"),
    language: str = Field(default="en", description="Language for results"),
    region: Optional[str] = Field(
        default=None, description="Region bias (e.g., 'us', 'cn')"
    ),
) -> str:
    """
    Get latitude and longitude coordinates for a given address.

    Args:
        address: The address to convert to coordinates
        api_key: Your Google Maps API key
        language: Language code for results
        region: Region bias for results

    Returns:
        JSON string containing latitude and longitude
    """
    try:
        # Initialize Google Maps client
        gmaps = googlemaps.Client(key=api_key)

        # Call geocoding API
        geocode_result = gmaps.geocode(
            address=address, language=language, region=region
        )

        if geocode_result and len(geocode_result) > 0:
            # Extract location data from first result
            location = geocode_result[0].get("geometry", {}).get("location", {})

            # Create result object
            result = LatLngResult(
                address=geocode_result[0].get("formatted_address", address),
                latitude=location.get("lat", 0.0),
                longitude=location.get("lng", 0.0),
                status="OK",
            )

            return result.model_dump_json()
        else:
            # Return error if no results
            return GoogleMapsError(
                error="No results found for the given address", status="ZERO_RESULTS"
            ).model_dump_json()

    except Exception as e:
        return handle_error(e, "Get LatLng")


def mcpgetpostcode(
    address: str = Field(
        description="Address or 'lat,lng' coordinates to get postcode for"
    ),
    api_key: str = Field(description="Google Maps API key"),
    language: str = Field(default="en", description="Language for results"),
) -> str:
    """
    Get postal code (zip code) for a given address or coordinates.

    Args:
        address: Address or 'lat,lng' coordinates
        api_key: Your Google Maps API key
        language: Language code for results

    Returns:
        JSON string containing postal code information
    """
    try:
        # Initialize Google Maps client
        gmaps = googlemaps.Client(key=api_key)

        # Check if input is coordinates
        is_coords = False
        location_param = None

        if "," in address:
            try:
                lat, lng = map(float, address.split(","))
                location_param = (lat, lng)
                is_coords = True
            except ValueError:
                # Not valid coordinates, treat as address
                pass

        # Call geocoding API
        if is_coords:
            geocode_result = gmaps.reverse_geocode(
                location_param, language=language, result_type=["postal_code"]
            )
        else:
            geocode_result = gmaps.geocode(
                address=address, language=language, components={"postal_code": ""}
            )

        if geocode_result and len(geocode_result) > 0:
            # Extract postal code from address components
            postal_code = ""
            country = ""
            country_code = ""

            for component in geocode_result[0].get("address_components", []):
                if "postal_code" in component.get("types", []):
                    postal_code = component.get("long_name", "")
                if "country" in component.get("types", []):
                    country = component.get("long_name", "")
                    country_code = component.get("short_name", "")

            # Create result object
            result = PostcodeResult(
                address=geocode_result[0].get("formatted_address", address),
                postcode=postal_code,
                country=country,
                country_code=country_code,
                status="OK" if postal_code else "NOT_FOUND",
            )

            return result.model_dump_json()
        else:
            # Return error if no results
            return GoogleMapsError(
                error="No postal code found for the given address",
                status="ZERO_RESULTS",
            ).model_dump_json()

    except Exception as e:
        return handle_error(e, "Get Postcode")


if __name__ == "__main__":
    run_mcp_server(
        "Google Maps Server",
        funcs=[
            mcpgeocode,
            mcpdistancematrix,
            mcpdirections,
            mcpplacesearch,
            mcpplacedetails,
            mcptimezone,
            mcpelevation,
            mcpgetlatlng,
            mcpgetpostcode,
        ],
        port=4446,
    )
