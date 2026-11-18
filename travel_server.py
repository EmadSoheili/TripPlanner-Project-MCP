# 

from dotenv import load_dotenv      # To load environment variables from a .env file
from pathlib import Path            # To manage file paths
from mcp.server.mcpserver import MCPServer          # To create a FastAPI server for the MCP (Multi-Component Platform)


# Paths
BASE_DIR = Path(__file__).parent        # Define the base directory of the project
load_dotenv(BASE_DIR / ".env")          # Load environment variables from the .env file located in the base directory
PREFERENCES_PATH = BASE_DIR / "data" / "preferences.txt"            # Define the path to the preferences file


# Initialize the MCPServer with a name
mcp = MCPServer("TripPlanner-MCP")


# Define a health check tool for the MCP server
@mcp.tool()
def health_check():
    """Verify the Travel MCP server is running and check directory paths."""
    return f"Server is online. Base directory: {BASE_DIR}"


#
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


if __name__ == "__main__":
    mcp.run(transport="stdio")          # Run the MCP server using standard input/output for communication