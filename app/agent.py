# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from app.a2ui_utils import a2ui_callback

# HARDCODED CONFIGURATION
FIRESTORE_PROJECT = "qwiklabs-gcp-02-5e57235b3d5f"
GCS_BUCKET_NAME = "commutepulse-public-qwiklabs-gcp-02-5e57235b3d5f"

db = firestore.Client(project=FIRESTORE_PROJECT)

# Configure AgentEngineSandboxCodeExecutor using deployment_metadata.json if available
metadata_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deployment_metadata.json")
agent_engine_resource_name = None
if os.path.exists(metadata_path):
    try:
        with open(metadata_path) as f:
            meta = json.load(f)
            agent_engine_resource_name = meta.get("remote_agent_runtime_id")
    except Exception:
        pass

code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=agent_engine_resource_name
)


async def generate_memories_callback(callback_context: CallbackContext):
    """Callback triggered after each turn to extract and store durable facts (including all user allergies, health restrictions, and preferences) into Memory Bank."""
    await callback_context.add_session_to_memory()
    return None


def _get_maps_api_key() -> str:
    """Reads GOOGLE_MAPS_API_KEY from environment variables or .env file."""
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key or api_key == "PASTE_KEY_HERE":
        env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    if line.startswith("GOOGLE_MAPS_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break
    return api_key


def list_transit_routes(search_term: str = "") -> str:
    """Retrieves available transit routes from the Firestore database.

    Args:
        search_term: Optional keyword to filter routes by name, origin, destination, or transport mode.

    Returns:
        JSON string containing the list of transit routes.
    """
    collection_ref = db.collection("transit_routes")
    docs = collection_ref.stream()

    routes = []
    term = search_term.lower().strip()
    for doc in docs:
        data = doc.to_dict()
        if not term:
            routes.append(data)
        else:
            combined_text = f"{data.get('name', '')} {data.get('origin', '')} {data.get('destination', '')} {data.get('transport_mode', '')}".lower()
            if term in combined_text:
                routes.append(data)

    return json.dumps({"routes": routes, "count": len(routes)}, indent=2)


def add_transit_route(
    route_id: str,
    name: str,
    origin: str,
    destination: str,
    transport_mode: str,
    avg_duration_mins: int,
    est_cost_usd: float,
    co2_saved_kg: float,
    traffic_status: str,
) -> str:
    """Adds or updates a transit route option in the Firestore database.

    Args:
        route_id: Unique string identifier for the route (e.g. 'route-express-1').
        name: Descriptive name of the transit route (e.g. 'Oakland-SF Express Bus').
        origin: Starting point/city of the route.
        destination: End point/city of the route.
        transport_mode: Transit mode (e.g. 'Carpool', 'Metro', 'Ferry', 'Express Bus').
        avg_duration_mins: Average travel duration in minutes.
        est_cost_usd: Estimated cost per trip in USD.
        co2_saved_kg: Estimated CO2 saved compared to solo driving in kg.
        traffic_status: Current traffic/congestion status or schedule note.

    Returns:
        Success message confirming the route addition to Firestore.
    """
    route_data = {
        "route_id": route_id,
        "name": name,
        "origin": origin,
        "destination": destination,
        "transport_mode": transport_mode,
        "avg_duration_mins": int(avg_duration_mins),
        "est_cost_usd": float(est_cost_usd),
        "co2_saved_kg": float(co2_saved_kg),
        "traffic_status": traffic_status,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    doc_ref = db.collection("transit_routes").document(route_id)
    doc_ref.set(route_data)

    return f"Successfully saved route '{name}' (ID: {route_id}) to Firestore."


def calculate_commute_savings(
    daily_miles: float,
    transport_mode: str = "transit",
    days_per_week: int = 5,
) -> str:
    """Calculates estimated monthly financial savings (gas, tolls, parking) and CO2 reduction compared to solo driving.

    Args:
        daily_miles: Round-trip commute distance in miles per day.
        transport_mode: Target commute mode ('transit', 'carpool', 'bike', 'ferry').
        days_per_week: Number of commuting days per week (default 5).

    Returns:
        A JSON string with monthly cost savings in USD and CO2 saved in kg.
    """
    monthly_days = days_per_week * 4.33
    monthly_miles = daily_miles * monthly_days

    # Solo driving cost estimate ($0.35/mile for fuel + wear, plus $5/day toll/parking)
    driving_cost = (monthly_miles * 0.35) + (monthly_days * 5.0)

    mode_lower = transport_mode.lower()
    if "carpool" in mode_lower:
        alt_cost = driving_cost * 0.4
        co2_saved_kg = monthly_miles * 0.25
    elif "bike" in mode_lower or "walk" in mode_lower:
        alt_cost = 0.0
        co2_saved_kg = monthly_miles * 0.40
    elif "ferry" in mode_lower:
        alt_cost = monthly_days * 9.0
        co2_saved_kg = monthly_miles * 0.30
    else:  # transit / metro / bus
        alt_cost = monthly_days * 6.0
        co2_saved_kg = monthly_miles * 0.35

    monthly_savings = max(0.0, driving_cost - alt_cost)

    return json.dumps({
        "daily_miles": daily_miles,
        "transport_mode": transport_mode,
        "days_per_week": days_per_week,
        "monthly_driving_cost_usd": round(driving_cost, 2),
        "monthly_alt_cost_usd": round(alt_cost, 2),
        "monthly_savings_usd": round(monthly_savings, 2),
        "monthly_co2_saved_kg": round(co2_saved_kg, 1),
    }, indent=2)


def fetch_bikeshare_stations(
    network_id: str = "bay-wheels",
    search_location: str = "",
) -> str:
    """Fetches real-time bike-share station availability from the public CityBikes API.

    Args:
        network_id: Bike-share network ID (e.g. 'bay-wheels' for SF Bay Area, 'citi-bike-new-york' for NYC).
        search_location: Optional station name or location filter (e.g. 'Market', 'Ferry', 'CalTrain').

    Returns:
        JSON string containing live station names, available bikes, and free docks.
    """
    api_key = os.environ.get("CITYBIKES_API_KEY", "")
    url = f"https://api.citybik.es/v2/networks/{network_id}"

    headers = {"User-Agent": "CommutePulseAI/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            network = data.get("network", {})
            stations = network.get("stations", [])

            term = search_location.lower().strip()
            results = []
            for s in stations:
                name = s.get("name", "")
                if not term or term in name.lower():
                    results.append({
                        "name": name,
                        "free_bikes": s.get("free_bikes", 0),
                        "empty_slots": s.get("empty_slots", 0),
                    })
                    if len(results) >= 10:
                        break

            return json.dumps({
                "network": network.get("name", network_id),
                "total_stations": len(stations),
                "matching_stations": results,
            }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch bike-share data: {str(e)}"})


def geocode_address(address: str) -> str:
    """Converts a street address or landmark into coordinates using the Google Maps Geocoding API.

    Args:
        address: Street address or location (e.g. '1 Ferry Building, San Francisco, CA').

    Returns:
        JSON string containing formatted address, latitude, longitude, and place_id.
    """
    api_key = _get_maps_api_key()
    encoded_address = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"

    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") == "OK" and data.get("results"):
                result = data["results"][0]
                geometry = result.get("geometry", {}).get("location", {})
                return json.dumps({
                    "name": address,
                    "formatted_address": result.get("formatted_address"),
                    "location": {
                        "latitude": geometry.get("lat"),
                        "longitude": geometry.get("lng"),
                    },
                    "place_id": result.get("place_id"),
                }, indent=2)
            else:
                return json.dumps({
                    "error": f"Geocoding status: {data.get('status', 'UNKNOWN')}",
                    "details": data.get("error_message", "No results found"),
                })
    except Exception as e:
        return json.dumps({"error": f"Geocoding request failed: {str(e)}"})


def search_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "transit_station",
    radius_meters: float = 1000.0,
) -> str:
    """Finds nearby places of a given type using the Google Places API (New).

    Args:
        latitude: Center latitude.
        longitude: Center longitude.
        place_type: Type of place to search for (e.g. 'transit_station', 'bus_station', 'train_station', 'parking').
        radius_meters: Search radius in meters (default 1000m).

    Returns:
        JSON string containing matching places with name, formatted address, and location coordinates.
    """
    api_key = _get_maps_api_key()
    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types",
    }
    payload = {
        "includedTypes": [place_type],
        "maxResultCount": 10,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                },
                "radius": float(radius_meters),
            }
        },
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            places = data.get("places", [])
            results = []
            for p in places:
                display_name = p.get("displayName", {}).get("text", "")
                results.append({
                    "name": display_name,
                    "formatted_address": p.get("formattedAddress", ""),
                    "location": p.get("location", {}),
                    "types": p.get("types", []),
                })
            return json.dumps({
                "place_type": place_type,
                "center": {"latitude": latitude, "longitude": longitude},
                "places": results,
                "count": len(results),
            }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Places searchNearby request failed: {str(e)}"})


def generate_commute_image(
    prompt: str,
    tool_context: ToolContext = None,
) -> str:
    """Generates an image for a commute route, traffic overview, or eco-badge using gemini-3.1-flash-lite-image in global region.

    Saves the image with tool_context.save_artifact for Playground display and uploads image bytes to public Cloud Storage.

    Args:
        prompt: Description of the commute image to generate (e.g. 'A vibrant modern map showing the Bay Bridge carpool route').
        tool_context: ADK ToolContext injected automatically by the framework.

    Returns:
        JSON string containing the public HTTPS URL of the uploaded image and artifact status.
    """
    client = genai.Client(
        vertexai=True,
        project="qwiklabs-gcp-02-5e57235b3d5f",
        location="global",
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
        )

        image_bytes = None
        mime_type = "image/jpeg"

        if response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, "inline_data") and part.inline_data:
                            image_bytes = part.inline_data.data
                            mime_type = part.inline_data.mime_type or "image/jpeg"
                            break

        if not image_bytes:
            return json.dumps({"error": "No image data returned from gemini-3.1-flash-lite-image model."})

        filename = f"commute_image_{int(time.time())}.jpg"

        # (1) Save artifact in Playground
        artifact_saved = False
        if tool_context:
            try:
                artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                tool_context.save_artifact(filename=filename, artifact=artifact_part)
                artifact_saved = True
            except Exception as ae:
                print(f"Failed to save artifact: {ae}")

        # (2) Upload image bytes to public Cloud Storage bucket without local file write
        storage_client = storage.Client(project="qwiklabs-gcp-02-5e57235b3d5f")
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(f"images/{filename}")
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/images/{filename}"

        return json.dumps({
            "prompt": prompt,
            "public_url": public_url,
            "artifact_saved": artifact_saved,
            "filename": filename,
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Image generation failed: {str(e)}"})


def generate_commute_video(
    prompt: str,
    tool_context: ToolContext = None,
) -> str:
    """Generates a short video for a commute item (traffic simulation, transit route animation, eco-badge) using gemini-omni-flash-preview model in global region.

    Saves the generated video using tool_context.save_artifact for Playground display, uploads video bytes to public Cloud Storage, and returns its public HTTPS URL.

    Args:
        prompt: Description of the commute video to generate (e.g. 'A short video simulation of a sleek urban metro train').
        tool_context: ADK ToolContext injected automatically by the framework.

    Returns:
        JSON string containing the public HTTPS URL of the uploaded video and artifact status.
    """
    client = genai.Client(
        vertexai=True,
        project="qwiklabs-gcp-02-5e57235b3d5f",
        location="global",
    )

    try:
        video_bytes = None
        mime_type = "video/mp4"

        try:
            res = client.interactions.create(
                model="gemini-omni-flash-preview",
                input=prompt,
            )
            if hasattr(res, "outputs") and res.outputs:
                for out in res.outputs:
                    if hasattr(out, "data") and out.data:
                        video_bytes = out.data
                        if hasattr(out, "mime_type") and out.mime_type:
                            mime_type = out.mime_type
                        break
            elif hasattr(res, "video_bytes") and res.video_bytes:
                video_bytes = res.video_bytes
        except Exception:
            response = client.models.generate_content(
                model="gemini-omni-flash-preview",
                contents=prompt,
            )
            if response.candidates:
                for candidate in response.candidates:
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, "inline_data") and part.inline_data:
                                video_bytes = part.inline_data.data
                                mime_type = part.inline_data.mime_type or "video/mp4"
                                break

        if not video_bytes:
            return json.dumps({"error": "No video data returned from gemini-omni-flash-preview model."})

        filename = f"commute_video_{int(time.time())}.mp4"

        # (1) Save artifact in Playground
        artifact_saved = False
        if tool_context:
            try:
                artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
                tool_context.save_artifact(filename=filename, artifact=artifact_part)
                artifact_saved = True
            except Exception as ae:
                print(f"Failed to save artifact: {ae}")

        # (2) Upload video bytes to public Cloud Storage bucket without writing to local file
        storage_client = storage.Client(project="qwiklabs-gcp-02-5e57235b3d5f")
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(f"videos/{filename}")
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/videos/{filename}"

        return json.dumps({
            "prompt": prompt,
            "public_url": public_url,
            "artifact_saved": artifact_saved,
            "filename": filename,
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Video generation failed: {str(e)}"})


def get_weather(query: str) -> str:
    """Simulates getting weather information.

    Args:
        query: Location query string.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy in San Francisco."
    return "It's 72 degrees and clear."


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description="You are CommutePulse AI, a smart urban commute & traffic optimization assistant.",
    workflow_description=(
        "Help commuters find fast, eco-friendly transit routes, calculate monthly financial and CO2 savings, "
        "check live bike-share availability, geocode locations, locate nearby transit hubs, generate visual commute maps, video simulations, or eco-badges, "
        "and execute Python code in a secure sandbox for custom data processing or calculations. "
        "Analyze the user request and return structured A2UI display cards and components when appropriate."
    ),
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects. "
        "IMPORTANT MEMORY RULE: You MUST remember all user allergies (e.g. food allergies like peanuts/gluten/seafood, environmental allergies like pollen/latex/dust), "
        "health conditions, and personal preferences across all conversations using your Memory Bank. "
        "Whenever a user states an allergy, confirm that it has been saved to memory and ensure all future commute, food, and transit recommendations strictly honor it."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        list_transit_routes,
        add_transit_route,
        calculate_commute_savings,
        fetch_bikeshare_stations,
        geocode_address,
        search_nearby_places,
        generate_commute_image,
        generate_commute_video,
        get_weather,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
