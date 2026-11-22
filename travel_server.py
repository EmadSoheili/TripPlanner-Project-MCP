# Here I have created a Travel MCP Server


import os                           # To interact with the operating system
from dotenv import load_dotenv      # To load environment variables from a .env file
from pathlib import Path            # To manage file paths
import httpx                        # To make HTTP requests (used for testing the server)
from mcp.server.mcpserver import MCPServer          # To create a FastAPI server for the MCP (Multi-Component Platform)


# Paths
BASE_DIR = Path(__file__).parent        # Define the base directory of the project
load_dotenv(BASE_DIR / ".env")          # Load environment variables from the .env file located in the base directory
PREFERENCES_PATH = BASE_DIR / "data" / "preferences.txt"            # Define the path to the preferences file


# Initialize the MCPServer with a name
mcp = MCPServer(
    "TripPlanner-MCP",
    instructions=(
        "Whenever the user asks to plan a trip, search flights, or build an itinerary, "
        "always call read_preferences first to load their saved constraints and output format, "
        "then use search_flights for real-time flight pricing and Tavily for hotels, attractions, coffee, and dining."
    ),
)


# Define a health check tool for the MCP server
@mcp.tool()
def health_check():
    """Verify the Travel MCP server is running and check directory paths."""
    return f"Server is online. Base directory: {BASE_DIR}"


# Define a tool to read user preferences from the preferences file
@mcp.tool()
def read_preferences():
    """
    Read the user's saved travel preferences, budget caps, flight rules, and daily pacing constraints.
    IMPORTANT: Always call this tool FIRST before searching flights or building any travel itinerary.
    """

    # Defensive programming: Check if the preferences file exists before attempting to read it
    if not PREFERENCES_PATH.exists():
        return f"Error: Preferences file not found at {PREFERENCES_PATH}. Please create data/preferences.txt."

    # Read the content of the preferences file
    content = PREFERENCES_PATH.read_text(encoding="utf-8")

    if not content:
        return f"Warning: data/preferences.txt is currently empty."

    return content


# Define a tool to search for flights using the SerpApi Google Flights engine
@mcp.tool()
async def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
    adults: int = 2,
    currency: str = "CAD"
) -> str:
    """
    Search real-time flights via Google Flights (SerpApi) and return the top 5 options ranked by lowest price and shortest duration.
    
    Args:
        origin: 3-letter IATA airport code (e.g., 'YYZ')
        destination: 3-letter IATA airport code (e.g., 'LHR', 'CDG', 'CUN')
        departure_date: Outbound date in YYYY-MM-DD format
        return_date: Return date in YYYY-MM-DD format
        adults: Number of adult passengers (default 2)
        currency: Currency code for pricing (default 'CAD')
    """

    api_key = os.getenv("SERPAPI_API_KEY")          # Retrieve the SerpApi key from environment variables
    
    # Defensive programming: Check if the API key is available
    if not api_key:
        return "Error: SERPAPI_API_KEY not found in the .env file."

    origin = origin.strip().upper()                 # Clean and format the origin airport code
    destination = destination.strip().upper()       # Clean and format the destination airport code
    
    # Defensive programming: Validate the length of the airport codes
    if len(origin) != 3 or len(destination) != 3:
        return "Error: origin and destination must be 3-letter IATA airport codes (e.g., 'YYZ', 'LHR')."

    # Define the parameters for the flight search request to SerpApi
    params = {
        "engine": "google_flights",                 # Specify the search engine for flight data
        "departure_id": origin,                     # Set the origin airport code
        "arrival_id": destination,                  # Set the destination airport code
        "outbound_date": departure_date,            # Set the outbound date
        "return_date": return_date,                 # Set the return date
        "currency": currency.upper(),               # Set the currency for pricing
        "hl": "en",                                 # Set the language to English
        "adults": adults,                           # Set the number of adult passengers
        "type": "1",                                # Specify the type of search (1 for Round Trip)
        "api_key": api_key,                         # Include the SerpApi key for authentication
    }


    # Use an asynchronous HTTP client to make the request to SerpApi
    async with httpx.AsyncClient(timeout = 20.0) as client:          # Use an asynchronous HTTP client for the request
        try:
            response = await client.get("https://serpapi.com/search", params=params)          # Make the GET request to SerpApi
            response.raise_for_status()             # Raise an error for any unsuccessful HTTP responses
            data = response.json()                  # Parse the JSON response from SerpApi
        except httpx.HTTPError as e:
            return f"Error: Failed to fetch flight data from SerpApi. Details: {str(e)}"          # Return an error message if the request fails


    # Combine the best matching flights and other flights from the response
    # Google will send the list of flights in two separate lists: "best_flights" and "other_flights"
    # I have combined them into a single list called flights_pool for easier processing
    flights_pool = data.get("best_flights",[]) + data.get("other_flights",[])

    # Defensive programming: Check if any flights were found in the response
    if not flights_pool:
        return f"Error: No Round-trip flights found from {origin} to {destination} for {departure_date} to {return_date}."

    # Rank strictly by lowest price first, then shortest total duration as tie-breaker
    flights_pool.sort(
        key=lambda offer: (
            offer.get("price", float("inf")),
            offer.get("total_duration", float("inf"))
        )
    )

    top_flights = flights_pool[:5]          # Get the top 5 flight options
    summaries = [f"Top {len(top_flights)} Round-Trip Flight Options ({origin} -> {destination}, Depart: {departure_date}, Return: {return_date}, {adults} Adults):"]
            

    # Generate summaries for each flight option
    for idx, offer in enumerate(top_flights, start=1):
        price = offer.get("price", "N/A")                           # Get the price of the flight offer
        total_duration_min = offer.get("total_duration", 0)         # Get the total duration of the flight in minutes
        hours, mins = divmod(total_duration_min, 60)                # Convert total duration from minutes to hours and minutes

        legs = offer.get("flights", [])                                                             # Get the list of flight legs for the offer
        airlines = list(dict.fromkeys(leg.get("airline", "Unknown") for leg in legs))               # Extract unique airline names from the flight legs
        flight_nums = [leg.get("flight_number", "N/A") for leg in legs]                             # Extract flight numbers from the flight legs
        cabin_classes = list(dict.fromkeys(leg.get("travel_class", "Economy") for leg in legs))     # Extract unique cabin classes from the flight legs

        dep_info = legs[0].get("departure_airport", {}) if legs else {}         # Get departure airport info from the first leg
        arr_info = legs[-1].get("arrival_airport", {}) if legs else {}          # Get arrival airport info from the last leg

        layovers = offer.get("layovers", [])
        if not layovers:
            layover_str = "Direct flight (0h layover)"
        else:
            layover_details = [
                f"{l.get('id', 'Unknown')} ({l.get('duration', 0) // 60}h {l.get('duration', 0) % 60}m)"
                for l in layovers
            ]

            layover_str = f"{len(layovers)} layover(s): " + ", ".join(layover_details)

        summaries.append(
            f"\nOption {idx} | Total Price (for {adults} adults): ${price} {currency.upper()}\n"
            f"  - Airline(s): {', '.join(airlines)} (Flight: {', '.join(flight_nums)} | Class: {', '.join(cabin_classes)})\n"
            f"  - Departure: {dep_info.get('id', origin)} at {dep_info.get('time', 'N/A')}\n"
            f"  - Arrival: {arr_info.get('id', destination)} at {arr_info.get('time', 'N/A')}\n"
            f"  - Outbound Duration: {hours}h {mins}m | {layover_str}"
        )        

    return "\n".join(summaries)



if __name__ == "__main__":
    mcp.run(transport="stdio")          # Run the MCP server using standard input/output for communication