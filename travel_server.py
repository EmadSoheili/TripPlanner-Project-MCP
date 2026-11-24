# Here I have created a Travel MCP Server


import asyncio                      # To prevent slow image API calls from hanging the MCP server
import os                           # To interact with the operating system
import re                           # To sanitize filenames and format Markdown tables
from datetime import datetime       # To timestamp generated files
from dotenv import load_dotenv      # To load environment variables from a .env file
from pathlib import Path            # To manage file paths
import httpx                        # To make HTTP requests (used for testing the server)
import markdown                     # To convert Claude's exact Markdown output into HTML for PDF export
from fpdf import FPDF               # To write HTML/Markdown directly to a clean PDF
from google import genai            # To call Gemini image generation for the visual infographic
from google.genai import types      # To configure Gemini image generation parameters
from mcp.server.mcpserver import MCPServer          # To create a FastAPI server for the MCP (Multi-Component Platform)


# Paths
BASE_DIR = Path(__file__).parent        # Define the base directory of the project
load_dotenv(BASE_DIR / ".env")          # Load environment variables from the .env file located in the base directory
PREFERENCES_PATH = BASE_DIR / "data" / "preferences.txt"            # Define the path to the preferences file
OUTPUT_DIR = BASE_DIR / "output"        # Define the directory for exported PDF and PNG files


# Initialize the MCPServer with a name
mcp = MCPServer(
    "TripPlanner-MCP",
    instructions=(
        "Whenever the user asks to plan a trip, search flights, or build an itinerary, "
        "always call read_preferences first to load their saved constraints and output format, "
        "then use search_flights for real-time flight pricing and Tavily to verify live hotel rates and ratings. "
        "When the user asks to save or export the itinerary, call save_itinerary and pass your exact Markdown response."
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
    currency: str = "CAD",
    max_price: int = 3000,
) -> str:
    """
    Search real-time flights via Google Flights (SerpApi), filter by layover and budget constraints,
    and return up to 5 valid options ranked by lowest price and shortest duration.
    
    Args:
        origin: 3-letter IATA airport code (e.g., 'YYZ')
        destination: 3-letter IATA airport code (e.g., 'LHR', 'CDG', 'CUN')
        departure_date: Outbound date in YYYY-MM-DD format
        return_date: Return date in YYYY-MM-DD format
        adults: Number of adult passengers (default 2)
        currency: Currency code for pricing (default 'CAD')
        max_price: Maximum round-trip price cap in the selected currency (default 3000)
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
        "stops": "2",                               # 2 = 1 stop or fewer (direct or max 1 layover)
        "layover_duration": "120,360",              # Layover duration between 2 hours (120m) and 6 hours (360m)
        "api_key": api_key,                         # Include the SerpApi key for authentication
    }

    # Helper to verify max 1 layover and 2h-6h layover window
    def _is_valid_leg(offer: dict) -> bool:
        price = offer.get("price")
        if isinstance(price, (int, float)) and price > max_price:
            return False
        layovers = offer.get("layovers", [])
        if len(layovers) > 1:
            return False
        return all(120 <= l.get("duration", 0) <= 360 for l in layovers)

    # Use an asynchronous HTTP client to make the request to SerpApi
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.get("https://serpapi.com/search", params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            return f"Error: Failed to fetch flight data from SerpApi. Details: {str(e)}"

        # Combine best_flights and other_flights, then filter BEFORE slicing
        flights_pool = data.get("best_flights", []) + data.get("other_flights", [])
        valid_outbound = [offer for offer in flights_pool if _is_valid_leg(offer)]

        if not valid_outbound:
            return (
                f"Error: No round-trip flights from {origin} to {destination} ({departure_date} to {return_date}) "
                f"matched max 1 layover, 2h-6h layover duration, and ${max_price} {currency.upper()} cap."
            )

        # Rank strictly by lowest price first, then shortest total duration as tie-breaker
        valid_outbound.sort(
            key=lambda offer: (
                offer.get("price", float("inf")),
                offer.get("total_duration", float("inf"))
            )
        )

        top_flights = valid_outbound[:5]          # Slice top 5 AFTER filtering
        summaries = [f"Top {len(top_flights)} Valid Round-Trip Flight Options ({origin} -> {destination}, Depart: {departure_date}, Return: {return_date}, {adults} Adults):"]

        # Generate summaries for each flight option
        for idx, offer in enumerate(top_flights, start=1):
            price = offer.get("price", "N/A")
            total_duration_min = offer.get("total_duration", 0)
            hours, mins = divmod(total_duration_min, 60)

            legs = offer.get("flights", [])
            airlines = list(dict.fromkeys(leg.get("airline", "Unknown") for leg in legs))
            flight_nums = [leg.get("flight_number", "N/A") for leg in legs]
            cabin_classes = list(dict.fromkeys(leg.get("travel_class", "Economy") for leg in legs))

            dep_info = legs[0].get("departure_airport", {}) if legs else {}
            arr_info = legs[-1].get("arrival_airport", {}) if legs else {}

            layovers = offer.get("layovers", [])
            if not layovers:
                layover_str = "Direct flight (0h layover)"
            else:
                layover_details = [
                    f"{l.get('id', 'Unknown')} ({l.get('duration', 0) // 60}h {l.get('duration', 0) % 60}m)"
                    for l in layovers
                ]
                layover_str = f"{len(layovers)} layover(s): " + ", ".join(layover_details)

            # Fetch matching return leg details using SerpApi's departure_token
            return_summary = "  - Return Leg: Details unavailable"
            dep_token = offer.get("departure_token")
            if dep_token:
                ret_params = {**params, "departure_token": dep_token}
                try:
                    ret_response = await client.get("https://serpapi.com/search", params=ret_params)
                    ret_response.raise_for_status()
                    ret_data = ret_response.json()
                    ret_pool = ret_data.get("best_flights", []) + ret_data.get("other_flights", [])
                    valid_returns = [r for r in ret_pool if _is_valid_leg(r)] or ret_pool

                    if valid_returns:
                        valid_returns.sort(
                            key=lambda r: (
                                r.get("price", float("inf")),
                                r.get("total_duration", float("inf"))
                            )
                        )
                        best_ret = valid_returns[0]
                        ret_total_min = best_ret.get("total_duration", 0)
                        ret_hours, ret_mins = divmod(ret_total_min, 60)

                        ret_legs = best_ret.get("flights", [])
                        ret_airlines = list(dict.fromkeys(leg.get("airline", "Unknown") for leg in ret_legs))
                        ret_nums = [leg.get("flight_number", "N/A") for leg in ret_legs]
                        ret_dep = ret_legs[0].get("departure_airport", {}) if ret_legs else {}
                        ret_arr = ret_legs[-1].get("arrival_airport", {}) if ret_legs else {}

                        ret_layovers = best_ret.get("layovers", [])
                        if not ret_layovers:
                            ret_layover_str = "Direct flight (0h layover)"
                        else:
                            ret_layover_details = [
                                f"{l.get('id', 'Unknown')} ({l.get('duration', 0) // 60}h {l.get('duration', 0) % 60}m)"
                                for l in ret_layovers
                            ]
                            ret_layover_str = f"{len(ret_layovers)} layover(s): " + ", ".join(ret_layover_details)

                        price = best_ret.get("price", price)
                        return_summary = (
                            f"  - Return Airline(s): {', '.join(ret_airlines)} (Flight: {', '.join(ret_nums)})\n"
                            f"  - Return Departure: {ret_dep.get('id', destination)} at {ret_dep.get('time', 'N/A')}\n"
                            f"  - Return Arrival: {ret_arr.get('id', origin)} at {ret_arr.get('time', 'N/A')}\n"
                            f"  - Return Duration: {ret_hours}h {ret_mins}m | {ret_layover_str}"
                        )
                except httpx.HTTPError:
                    pass

            summaries.append(
                f"\nOption {idx} | Total Price (for {adults} adults): ${price} {currency.upper()}\n"
                f"  - Outbound Airline(s): {', '.join(airlines)} (Flight: {', '.join(flight_nums)} | Class: {', '.join(cabin_classes)})\n"
                f"  - Outbound Departure: {dep_info.get('id', origin)} at {dep_info.get('time', 'N/A')}\n"
                f"  - Outbound Arrival: {arr_info.get('id', destination)} at {arr_info.get('time', 'N/A')}\n"
                f"  - Outbound Duration: {hours}h {mins}m | {layover_str}\n"
                f"{return_summary}"
            )        

    return "\n".join(summaries)


# Define an async tool to export Claude's exact Markdown response as a PDF and generate a visual infographic via Gemini
@mcp.tool()
async def save_itinerary(
    destination: str,
    dates: str,
    flight_summary: str,
    hotel_summary: str,
    daily_routes: list[str],
    total_cost_cad: str,
    markdown_itinerary: str,
) -> str:
    """
    Save the exact Markdown itinerary shown in chat as a PDF file and generate a rich illustrated travel infographic PNG via Gemini in output/.

    Args:
        destination: Destination title (e.g., 'Paris, France (CDG)')
        dates: Travel date range (e.g., '2027-03-10 to 2027-03-15')
        flight_summary: Concise summary of the selected round-trip flight and price in CAD
        hotel_summary: Concise summary of the selected hotel, rating, and cost in CAD
        daily_routes: List of 1-line daily neighborhood summaries (e.g., ['Day 1: Le Marais', 'Day 2: Latin Quarter'])
        total_cost_cad: Total estimated trip cost in CAD (e.g., '~$3,800 CAD')
        markdown_itinerary: The complete, unmodified Markdown itinerary text and tables shown in chat
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", destination.strip()).strip("_").lower() or "trip"
    stem = OUTPUT_DIR / f"{slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    pdf_path, png_path = stem.with_suffix(".pdf"), stem.with_name(f"{stem.name}_infographic.png")

    # 1. Convert Claude's exact Markdown (with properly spaced tables) to PDF first so it is always saved
    char_map = str.maketrans({
        "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2022": "*", "\u2192": "->",
        "\u21c4": "<->", "\u2026": "...", "\u20ac": "EUR ", "\u0153": "oe",
    })
    cleaned_md = re.sub(r"([^\n])\n(\|[^\n]+\|\n\|[-:| ]+\|)", r"\1\n\n\2", markdown_itinerary.translate(char_map))
    html_body = markdown.markdown(cleaned_md, extensions=["tables"])
    safe_html = html_body.encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    pdf.write_html(safe_html)
    pdf.output(str(pdf_path))

    # 2. Generate an illustrated travel infographic PNG asynchronously with a 45s timeout
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return f"PDF saved to {pdf_path}, but GEMINI_API_KEY is missing in .env so PNG infographic was skipped."

    routes_text = "\n".join(f"- {r}" for r in daily_routes)
    image_prompt = (
        f"Create a modern, clean, editorial travel itinerary infographic poster for a trip to {destination}.\n"
        f"Include an illustrated map and iconic landmark motif of {destination}, clean vector icons (airplane, hotel, map pins, budget badge), "
        f"and legible typography organized into clear visual sections:\n"
        f"- Header: {destination.upper()} | Dates: {dates} | Total Budget: {total_cost_cad}\n"
        f"- Flights Card: {flight_summary}\n"
        f"- Hotel Card: {hotel_summary}\n"
        f"- Illustrated Day-by-Day Route Map & Stops:\n{routes_text}\n"
        f"Style: Flat modern editorial travel design, warm cream and deep navy palette with coral and teal accents, high legibility."
    )

    try:
        gemini_client = genai.Client(
            api_key=gemini_key,
            http_options=types.HttpOptions(timeout=60_000),  # 60,000 ms = 60s HTTP timeout
        )
        response = await asyncio.wait_for(
            gemini_client.aio.models.generate_content(
                model="gemini-3-pro-image",
                contents=image_prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="3:4"),
                ),
            ),
            timeout=60.0,  # 60s asyncio hard cap
        )

        saved_png = False
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                png_path.write_bytes(part.inline_data.data)
                saved_png = True
                break
        png_status = f"- Illustrated Gemini PNG Infographic: {png_path}" if saved_png else "- PNG Infographic: No image returned by model."
    except asyncio.TimeoutError:
        png_status = "- PNG Infographic skipped (Gemini image generation timed out after 60s)."
    except Exception as e:
        png_status = f"- PNG Infographic failed: {str(e)}"

    return f"Itinerary exported:\n- PDF: {pdf_path}\n{png_status}"


if __name__ == "__main__":
    mcp.run(transport="stdio")          # Run the MCP server using standard input/output for communication