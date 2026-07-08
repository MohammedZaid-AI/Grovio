from typing import TypedDict, Dict, Any, List


class RestaurantState(TypedDict):

    message: str

    phone: str

    selected_agents: List[str]

    results: Dict[str, Any]

    response: str