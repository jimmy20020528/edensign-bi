"""Edensign Tools — Langflow Custom Component.

Exposes 2 tools to the Agent (analyze_zipcode, generate_listing) that call
our local tool service at localhost:8002.
"""
import json
import urllib.request
import urllib.error

from langflow.custom import Component
from langflow.io import MessageTextInput, Output


TOOL_BASE = "http://localhost:8002"


def _post_json(url: str, payload: dict, timeout: int = 60) -> str:
    """Plain stdlib POST — no httpx dependency, no async issues."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.code}", "detail": e.read().decode("utf-8")[:300]})
    except urllib.error.URLError as e:
        return json.dumps({"error": "connection_failed", "detail": str(e.reason)})


class EdensignToolsComponent(Component):
    display_name = "Edensign Tools"
    description = "Edensign BI staging-style + listing generator tools."
    icon = "Home"
    name = "EdensignTools"

    inputs = [
        MessageTextInput(
            name="tool_base_url",
            display_name="Tool Service Base URL",
            value=TOOL_BASE,
            info="Base URL of the Edensign tool service. Default: http://localhost:8002",
        ),
    ]

    outputs = [
        Output(display_name="Toolset", name="tools", method="build_tools", types=["Tool"]),
    ]

    def _base_url(self) -> str:
        return (self.tool_base_url or TOOL_BASE).rstrip("/")


    def analyze_zipcode(self, zipcode: str, objective: str = "balanced") -> str:
        """Get top-3 staging style recommendations for a US ZIP code.

        Args:
            zipcode: 5-digit US ZIP code as a string, e.g. "02135".
            objective: One of "fast", "price", "balanced". Default "balanced".
        """
        return _post_json(
            f"{self._base_url()}/tool/analyze_zipcode",
            {"zipcode": zipcode, "objective": objective},
        )

    def generate_listing(
        self,
        zipcode: str,
        bedrooms: int,
        bathrooms: float,
        sqft: int,
        style: str = "",
        tone: str = "professional",
    ) -> str:
        """Get market context to compose a listing description.

        Returns JSON with property facts + recommended_style + market_context.
        You (the agent) should then compose the 2-3 paragraph description
        using these facts. Do not invent features not in the data.

        Args:
            zipcode: Property ZIP code as a string.
            bedrooms: Number of bedrooms.
            bathrooms: Number of bathrooms (e.g. 1.5).
            sqft: Square footage.
            style: Optional staging style. If empty, the top style for the ZIP is used.
            tone: One of "professional", "warm", "luxurious".
        """
        payload = {
            "zipcode": zipcode,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "sqft": sqft,
            "tone": tone,
        }
        if style:
            payload["style"] = style
        return _post_json(f"{self._base_url()}/tool/generate_listing", payload)


    def build_tools(self) -> list:
        """Expose this component's methods as LangChain tools for the Agent."""
        from langchain_core.tools import StructuredTool

        return [
            StructuredTool.from_function(
                func=self.analyze_zipcode,
                name="analyze_zipcode",
                description=(
                    "Get the top-3 staging style recommendations for a US ZIP code, "
                    "with predicted prices, predicted days on market, and confidence "
                    "scores. Use this whenever the user asks which staging style works "
                    "best in an area, or wants market analysis for a specific ZIP. "
                    "Parameters: zipcode (5-digit string like '02135'), objective "
                    "(one of 'fast', 'price', 'balanced'; default 'balanced')."
                ),
            ),
            StructuredTool.from_function(
                func=self.generate_listing,
                name="generate_listing",
                description=(
                    "Get the market context needed to write a property listing "
                    "description. Returns property facts + recommended style + "
                    "market data. Call this BEFORE writing a listing — it gives "
                    "you the facts to ground the writing in. Then YOU compose "
                    "the 2-3 paragraph listing in your final response using the "
                    "returned facts. Do NOT invent features not in the data. "
                    "Parameters: zipcode (string), bedrooms (int), bathrooms "
                    "(float like 1.5), sqft (int), style (optional string, "
                    "e.g. 'Modern Minimalist'), tone (one of 'professional', "
                    "'warm', 'luxurious')."
                ),
            ),
        ]
