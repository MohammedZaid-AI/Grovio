from typing import TypedDict, Dict, Any, List


class RestaurantState(TypedDict):

    message: str

    selected_agents: List[str]

    results: Dict[str, Any]

    response: str